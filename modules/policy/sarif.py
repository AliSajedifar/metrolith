"""Deterministic SARIF 2.1.0 projection of an existing check result.

This module is deliberately downstream of :mod:`modules.policy.check`.  It
does not open a run bundle, import the metric reader, compare values, or decide
whether a rule failed.  Rule summaries and canonical finding records in the
already-created check result are its only population inputs.

The small validator at the bottom checks the exact structural subset Metrolith
emits.  It is intentionally not called an official SARIF schema and performs
no network access.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from modules.config import PROGRAM_VERSION
from modules.policy import findings as finding_module
from modules.policy import rules as rule_module
from archlens_json import (
    SARIF_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    validate_json_value,
)

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/"
    "sarif-schema-2.1.0.json"
)

SEVERITY_TO_LEVEL: dict[str, str] = {
    rule_module.SEVERITY_VIOLATION: "error",
    rule_module.SEVERITY_WARNING: "warning",
    rule_module.SEVERITY_INFO: "note",
}

_LEVELS = frozenset(SEVERITY_TO_LEVEL.values())
_FINDING_ID = re.compile(r"^alf1:[0-9a-f]{32}$")
_WINDOWS_ABSOLUTE = re.compile(
    r"(?i)(?:^|[\s(\[{'\"])(?:[a-z]:[\\/]|\\\\|//[^/])"
)
_POSIX_MACHINE_ABSOLUTE = re.compile(
    r"(?i)(?:^|[\s(\[{'\"])/(?:home|users|tmp|var/tmp|private/tmp|root)/"
)
_FILE_URI = re.compile(r"(?i)(?:^|[\s(\[{'\"])file:/+")

_PROVENANCE_KEYS = (
    "run_id",
    "artifact_schema_version",
    "program_version",
    "metric_contract_version",
    "complexity_contract_version",
    "exclusion_policy_version",
    "analysis_scope_hash",
    "analyzed_commit_sha",
    "source_mode",
    "analysis_status",
)


class SarifProjectionError(ValueError):
    """The evaluator supplied a record that cannot be projected honestly."""


def _contains_machine_path(value: str) -> bool:
    return bool(
        _WINDOWS_ABSOLUTE.search(value)
        or _POSIX_MACHINE_ABSOLUTE.search(value)
        or _FILE_URI.search(value)
    )


def _portable_text(value: Any, *, fallback: str) -> str:
    """Return text only when it contains no recognizable machine-local path."""
    text = str(value or "").strip()
    if not text or _contains_machine_path(text):
        return fallback
    return text


def _portable_optional(value: Any) -> Any:
    if isinstance(value, str) and _contains_machine_path(value):
        return None
    return value


def normalize_repository_path(value: Any) -> str | None:
    """Normalize one persisted path, or refuse it when it is not relative.

    Refusal is preferable to guessing a repository root.  In particular, this
    function never strips a drive/root prefix to make an absolute path *look*
    relative.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"(?i)^[a-z]:/", normalized)
        or re.match(r"(?i)^[a-z][a-z0-9+.-]*:", normalized)
    ):
        return None
    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        return None
    parts = [part for part in parts if part not in ("", ".")]
    if not parts or any(any(ord(character) < 32 for character in part) for part in parts):
        return None
    return "/".join(parts)


def _line(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _location_omission(finding: Mapping[str, Any]) -> str | None:
    """Why a finding that NAMES a path still has no SARIF location.

    ``None`` when the finding names no path at all: a repository- or
    language-scope finding is not located, and an absent location there is the
    honest projection rather than an omission. Only a path that exists and
    cannot be projected portably is an omission, and it is recorded so a reader
    sees that something was withheld instead of quietly seeing nothing.

    A refused path never drops the finding. The result still carries its rule,
    its message, its observed value and its identity; the location is the only
    thing lost, and losing the whole finding would hide a real problem to avoid
    emitting a bad URI.
    """
    raw = finding.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    if normalize_repository_path(raw) is not None:
        return None
    return "path_is_not_repository_relative"


def _location(finding: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    path = normalize_repository_path(finding.get("path"))
    if path is None:
        return None
    physical: dict[str, Any] = {"artifactLocation": {"uri": path}}
    start = _line(finding.get("start_line"))
    end = _line(finding.get("end_line"))
    if start is not None:
        region: dict[str, int] = {"startLine": start}
        if end is not None and end >= start:
            region["endLine"] = end
        physical["region"] = region
    return [{"physicalLocation": physical}]


#: The evidence key holding a finding's OTHER locations.
#:
#: A finding carries exactly one `path`/`start_line`/`end_line`, which becomes
#: the SARIF primary location. A finding whose subject genuinely covers several
#: places -- a clone group is the first, and will not be the last -- states the
#: remainder here, and this projector emits them as `relatedLocations`.
#:
#: **This is the whole of what SARIF knows about multi-location findings.** It
#: does not know what a clone group is, does not read
#: `evidence.duplication_occurrences`, and has no per-family branch: the
#: EVALUATOR decides which places a finding covers, because deciding that is
#: evaluation. A projector that reached into a family-specific key would be a
#: second place that understands that family.
RELATED_LOCATIONS_KEY = "related_locations"


def _related_locations(
    finding: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Project the finding's other locations, and count the ones refused.

    Returns ``(locations, omitted)``. A location whose path is not repository
    relative is dropped **and counted**; it never drops its neighbours and never
    drops the finding. That is the same choice `_location_omission` already
    makes for the primary location, for the same reason: withholding one URI is
    honest, and hiding a real finding to avoid emitting a bad URI is not.

    The primary location is excluded by the evaluator, not here -- this function
    emits exactly what it is given, in the order it is given, so the projection
    adds nothing the check result does not already contain.
    """
    evidence = finding.get("evidence")
    if not isinstance(evidence, Mapping):
        return [], 0
    raw = evidence.get(RELATED_LOCATIONS_KEY)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return [], 0

    locations: list[dict[str, Any]] = []
    omitted = 0
    for item in raw:
        if not isinstance(item, Mapping):
            omitted += 1
            continue
        path = normalize_repository_path(item.get("path"))
        if path is None:
            omitted += 1
            continue
        physical: dict[str, Any] = {"artifactLocation": {"uri": path}}
        start = _line(item.get("start_line"))
        end = _line(item.get("end_line"))
        if start is not None:
            region: dict[str, int] = {"startLine": start}
            if end is not None and end >= start:
                region["endLine"] = end
            physical["region"] = region
        locations.append({"physicalLocation": physical})
    return locations, omitted


def _level(severity: Any) -> str:
    try:
        return SEVERITY_TO_LEVEL[str(severity)]
    except KeyError:
        raise SarifProjectionError(
            f"unsupported canonical severity {severity!r}; no SARIF level exists"
        ) from None


def _portable_json(value: Any) -> Any:
    """Deterministically remove machine paths from optional policy metadata."""
    if isinstance(value, Mapping):
        return {
            str(key): _portable_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _contains_machine_path(str(key))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_portable_json(item) for item in value]
    if isinstance(value, str) and _contains_machine_path(value):
        return "[machine-local path omitted]"
    return value


def _policy_annotations(policy: Any) -> dict[str, dict[str, Any]]:
    """Optional descriptor annotations; never a rule-population source."""
    annotations: dict[str, dict[str, Any]] = {}
    for rule in getattr(policy, "metric_rules", ()) if policy is not None else ():
        identifier = getattr(rule, "identifier", None)
        if not isinstance(identifier, str) or not identifier:
            continue
        annotation: dict[str, Any] = {}
        message = getattr(rule, "message", None)
        if isinstance(message, str) and message.strip():
            annotation["message"] = _portable_text(
                message, fallback="[machine-local policy message omitted]"
            )
        metadata = getattr(rule, "metadata", None)
        if isinstance(metadata, Mapping) and metadata:
            annotation["metadata"] = _portable_json(metadata)
        if annotation:
            annotations[identifier] = annotation
    return annotations


def _rule_description(
    summary: Mapping[str, Any], annotation: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    rule_id = str(summary["rule_id"])
    if summary.get("kind") == finding_module.KIND_INTEGRITY:
        registered = rule_module.RULES_BY_ID.get(rule_id)
        if registered is not None:
            return registered.title, registered.rationale
        domain = summary.get("domain") or "integrity"
        return (
            f"Metrolith {domain} rule",
            f"Policy integrity rule {rule_id} in the {domain} domain.",
        )

    metric = summary.get("metric") or "persisted metric"
    scope = summary.get("scope") or "unknown"
    operator = summary.get("operator") or "unknown"
    try:
        threshold = dumps_strict(
            summary.get("threshold"),
            source=f"SARIF descriptor {rule_id!r} threshold",
            limits=SARIF_JSON_LIMITS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except StrictJsonError as exc:
        raise SarifProjectionError(str(exc)) from None
    short = (annotation or {}).get("message") or f"{metric} policy rule"
    return (
        str(short),
        f"Policy rule {rule_id} evaluates persisted {metric} at {scope} scope "
        f"using the failing condition {operator} {threshold}.",
    )


def _rule_descriptor(
    summary: Mapping[str, Any], annotation: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    rule_id = summary.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise SarifProjectionError("every evaluated rule needs a non-empty rule_id")
    short, full = _rule_description(summary, annotation)
    outcome_keys = (
        "units_evaluated", "violated", "passed", "not_evaluable",
        "not_applicable", "evaluation_error",
    )
    archlens: dict[str, Any] = {
        "kind": summary.get("kind"),
        "severity": summary.get("severity"),
        "scope": summary.get("scope"),
        "domain": summary.get("domain"),
        "metric": summary.get("metric"),
        "metricSource": _portable_optional(summary.get("metric_source")),
        "operator": summary.get("operator"),
        "threshold": summary.get("threshold"),
        "outcomes": {key: summary.get(key, 0) for key in outcome_keys},
    }
    if annotation:
        archlens["policy"] = dict(annotation)
    return {
        "id": rule_id,
        "shortDescription": {
            "text": _portable_text(short, fallback=f"Metrolith policy rule {rule_id}")
        },
        "fullDescription": {
            "text": _portable_text(
                full, fallback=f"Metrolith policy rule {rule_id}."
            )
        },
        "defaultConfiguration": {"level": _level(summary.get("severity"))},
        "properties": {"archlens": archlens},
    }


def _descriptors(
    check_result: Mapping[str, Any], policy: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    raw_rules = check_result.get("rules") or []
    if isinstance(raw_rules, (str, bytes)) or not isinstance(raw_rules, Sequence):
        raise SarifProjectionError("check_result.rules must be an array")
    by_id: dict[str, Mapping[str, Any]] = {}
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            raise SarifProjectionError("every check_result.rules entry must be an object")
        rule_id = raw.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            raise SarifProjectionError("every evaluated rule needs a non-empty rule_id")
        if rule_id in by_id:
            raise SarifProjectionError(f"duplicate evaluated rule id {rule_id!r}")
        by_id[rule_id] = raw
    annotations = _policy_annotations(policy)
    descriptors = [
        _rule_descriptor(by_id[rule_id], annotations.get(rule_id))
        for rule_id in sorted(by_id)
    ]
    return descriptors, {
        descriptor["id"]: index for index, descriptor in enumerate(descriptors)
    }


def _finding_id(finding: Mapping[str, Any]) -> str:
    identifier = finding.get("finding_id")
    if not isinstance(identifier, str) or _FINDING_ID.fullmatch(identifier) is None:
        raise SarifProjectionError(
            f"invalid canonical finding identity {identifier!r}; projection "
            "cannot manufacture or repair finding identity"
        )
    return identifier


def _portable_provenance(finding: Mapping[str, Any]) -> dict[str, Any]:
    raw = finding.get("provenance")
    if not isinstance(raw, Mapping):
        return {}
    portable: dict[str, Any] = {}
    for key in _PROVENANCE_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str) and _contains_machine_path(value):
            continue
        portable[key] = value
    return portable


def _comparison_properties(finding: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project a canonical finding's bounded ratchet comparison envelope.

    Presence is data-driven: ordinary current-state findings do not carry the
    externally verified baseline digest and therefore retain byte-for-byte the
    same property shape.  Values are copied from canonical finding evidence and
    provenance; SARIF performs no comparison and invents no lifecycle state.
    """

    evidence = finding.get("evidence")
    provenance = finding.get("provenance")
    if not isinstance(evidence, Mapping) or not isinstance(provenance, Mapping):
        return None
    baseline_sha256 = provenance.get("baseline_digest_sha256")
    if not isinstance(baseline_sha256, str) or not baseline_sha256:
        return None
    if "direction" not in evidence or "maximum_regression" not in evidence:
        return None
    return {
        "baselineSha256": baseline_sha256,
        "baselineCommit": _portable_optional(
            provenance.get("baseline_commit_sha")
        ),
        "currentCommit": _portable_optional(provenance.get("current_commit_sha")),
        "baselineValue": evidence.get("baseline_value"),
        "currentValue": evidence.get("current_value"),
        "delta": evidence.get("delta"),
        "direction": evidence.get("direction"),
        "regressionAmount": evidence.get("regression_amount"),
        "tolerance": evidence.get("maximum_regression"),
    }


def _finding_properties(finding: Mapping[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "findingId": _finding_id(finding),
        "status": finding.get("status"),
        "kind": finding.get("kind"),
        "severity": finding.get("severity"),
        "scope": finding.get("scope"),
        "subjectKey": _portable_text(
            finding.get("subject_key"), fallback="[non-portable subject]"
        ),
        "metric": finding.get("metric"),
        "operator": finding.get("operator"),
        "threshold": finding.get("threshold"),
        "observedValue": finding.get("observed_value"),
        "language": _portable_optional(finding.get("language")),
        "callableRowId": _portable_optional(finding.get("callable_row_id")),
        "callableQualifiedName": _portable_optional(
            finding.get("callable_qualified_name")
        ),
        "dataCompleteness": finding.get("data_completeness"),
        "valueStatus": finding.get("value_status"),
        "valueStatusField": finding.get("value_status_field"),
        "reason": finding.get("reason"),
        "provenance": _portable_provenance(finding),
    }
    repository_url = finding.get("repository_url")
    if isinstance(repository_url, str) and not _contains_machine_path(repository_url):
        properties["repositoryUrl"] = repository_url

    comparison = _comparison_properties(finding)
    if comparison is not None:
        properties["comparison"] = comparison

    # The evidence the EVALUATOR already attached, carried through unchanged.
    # One uniform projection for every family: an integrity rule's evidence, a
    # collapsed row count, and a hotspot row's published classification and
    # reasons all travel the same way. Nothing here knows what a hotspot is, and
    # nothing may — a family-specific branch would be a second projector.
    #
    # `_portable_json` sorts every mapping key and replaces any machine-local
    # string, so this stays deterministic and leaks nothing.
    raw_evidence = finding.get("evidence")
    if isinstance(raw_evidence, Mapping) and raw_evidence:
        properties["evidence"] = _portable_json(raw_evidence)

    omitted = _location_omission(finding)
    if omitted is not None:
        properties["locationOmitted"] = omitted

    # A refused related location is REPORTED, not merely absent. Without the
    # count, a group whose second occurrence lives at a non-portable path looks
    # exactly like a group that only ever had one place.
    _, related_omitted = _related_locations(finding)
    if related_omitted:
        properties["relatedLocationsOmitted"] = related_omitted
    return properties


def _canonical_findings(
    check_result: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], bool]]:
    combined: list[tuple[Mapping[str, Any], bool]] = []
    for key, waived in (("findings", False), ("waived_findings", True)):
        raw = check_result.get(key) or []
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise SarifProjectionError(f"check_result.{key} must be an array")
        for finding in raw:
            if not isinstance(finding, Mapping):
                raise SarifProjectionError(f"every check_result.{key} item must be an object")
            _finding_id(finding)
            combined.append((finding, waived))
    identifiers = [_finding_id(item) for item, _waived in combined]
    if len(identifiers) != len(set(identifiers)):
        raise SarifProjectionError("one check result contains duplicate finding identities")
    return combined


def _result(
    finding: Mapping[str, Any], waived: bool, rule_indexes: Mapping[str, int]
) -> dict[str, Any]:
    identifier = _finding_id(finding)
    rule_id = finding.get("rule_id")
    if rule_id not in rule_indexes:
        raise SarifProjectionError(
            f"finding {identifier} names unevaluated rule {rule_id!r}"
        )
    result: dict[str, Any] = {
        "ruleId": rule_id,
        "ruleIndex": rule_indexes[rule_id],
        "level": _level(finding.get("severity")),
        "message": {
            "text": _portable_text(
                finding.get("message"),
                fallback=(
                    f"Metrolith policy rule {rule_id} produced canonical finding "
                    f"{identifier}; machine-local text was omitted."
                ),
            )
        },
        "partialFingerprints": {"archlensFindingId/v1": identifier},
        "properties": {"archlens": _finding_properties(finding)},
    }
    locations = _location(finding)
    if locations is not None:
        result["locations"] = locations
    # Emitted independently of the primary. A finding whose own path was refused
    # still points at every place that WAS portable, and one bad URI does not
    # blind a reader to the rest of the group.
    related, _omitted = _related_locations(finding)
    if related:
        result["relatedLocations"] = related
    if waived:
        raw_waiver = finding.get("waiver")
        waiver = raw_waiver if isinstance(raw_waiver, Mapping) else {}
        result["suppressions"] = [{
            "kind": "external",
            "status": "accepted",
            "justification": _portable_text(
                waiver.get("reason"), fallback="Suppressed by a Metrolith policy waiver."
            ),
        }]
        result["properties"]["archlens"]["waiver"] = {
            key: value for key, value in (
                ("expiresOn", waiver.get("expires_on")),
                ("issueId", waiver.get("issue_id")),
            ) if value is not None and not (
                isinstance(value, str) and _contains_machine_path(value)
            )
        }
    return result


def _notification(
    finding: Mapping[str, Any], rule_indexes: Mapping[str, int]
) -> dict[str, Any]:
    identifier = _finding_id(finding)
    rule_id = finding.get("rule_id")
    if rule_id not in rule_indexes:
        raise SarifProjectionError(
            f"finding {identifier} names unevaluated rule {rule_id!r}"
        )
    return {
        "level": _level(finding.get("severity")),
        "message": {
            "text": _portable_text(
                finding.get("message"),
                fallback=(
                    f"Metrolith policy rule {rule_id} could not produce a portable "
                    f"message for canonical finding {identifier}."
                ),
            )
        },
        "properties": {"archlens": _finding_properties(finding)},
    }


#: The fields of one evidence admission record worth carrying into SARIF.
#:
#: A subset, deliberately. `provenance_evidence` holds every analyzed scope the
#: run recorded and exists so a HUMAN can check an admission decision in the
#: check result; copying it here would bloat every SARIF document with data no
#: SARIF consumer acts on. What a consumer needs is whether evidence was
#: supplied, whether it was admitted, and which rules depended on it.
_EVIDENCE_RECORD_KEYS = (
    "kind",
    "supplied",
    "admission",
    "admission_meaning",
    "document_format",
    "document_format_version",
    "reason",
    "reason_meaning",
    "used_by_rules",
)


def _evidence_properties(check_result: Mapping[str, Any]) -> dict[str, Any]:
    """Per-kind evidence admission, so a SARIF reader can see what backed a gate.

    Without this a passing SARIF document from a gate that was never given its
    evidence looks exactly like one from a gate that was -- the same confusion
    the check result's own `evidence` block exists to prevent, and it must not
    reappear one projection later.

    Absent for a 1.0.0 check result, which has no evidence block at all. That is
    the honest projection: nothing is invented for a result whose contract had
    no such concept.
    """
    raw = check_result.get("evidence")
    if not isinstance(raw, Mapping) or not raw:
        return {}
    projected: dict[str, Any] = {}
    for kind in sorted(raw):
        record = raw[kind]
        if not isinstance(record, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in _EVIDENCE_RECORD_KEYS:
            if key in record:
                item[key] = _portable_json(record[key])
        binding = record.get("binding")
        if isinstance(binding, Mapping):
            item["binding"] = {
                name: _portable_json(binding[name])
                for name in ("state", "subject_keys")
                if name in binding
            }
        projected[str(kind)] = item
    return projected


def _run_properties(check_result: Mapping[str, Any]) -> dict[str, Any]:
    policy = check_result.get("policy")
    policy = policy if isinstance(policy, Mapping) else {}
    run = check_result.get("run")
    run = run if isinstance(run, Mapping) else {}
    provenance = check_result.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    contracts = {
        key: _portable_optional(provenance.get(key)) for key in (
            "artifact_schema_version", "metric_contract_version",
            "complexity_contract_version", "exclusion_policy_version",
        ) if provenance.get(key) is not None and _portable_optional(
            provenance.get(key)
        ) is not None
    }
    payload: dict[str, Any] = {
        "checkResultFormatVersion": check_result.get("check_result_format_version"),
        "verdict": check_result.get("verdict"),
        "exitCode": check_result.get("exit_code"),
        "failureKind": check_result.get("failure_kind"),
        "policy": {
            "name": _portable_text(policy.get("name"), fallback="[unnamed policy]"),
            "documentFormatVersion": policy.get("policy_document_format_version"),
            "evaluatedAsFormatVersion": policy.get("evaluated_as_format_version"),
        },
        "artifact": {
            "runId": _portable_optional(run.get("run_id")),
            "artifactSchemaVersion": _portable_optional(
                run.get("artifact_schema_version")
            ),
            "runStatus": _portable_optional(run.get("run_status")),
            "lifecycle": _portable_optional(run.get("lifecycle")),
        },
        "contracts": contracts,
        "counts": check_result.get("counts") or {},
        "determinism": "exact-byte deterministic canonical UTF-8 JSON",
    }
    evaluated_inputs = check_result.get("evaluated_input_provenance")
    if isinstance(evaluated_inputs, Mapping):
        payload["evaluatedInputProvenance"] = _portable_json(evaluated_inputs)
    evidence = _evidence_properties(check_result)
    if evidence:
        payload["evidence"] = evidence
    return payload


def project_sarif(
    check_result: Mapping[str, Any], *, policy: Any = None
) -> dict[str, Any]:
    """Project one already-evaluated check result into one SARIF run."""
    if not isinstance(check_result, Mapping):
        raise SarifProjectionError("a check result must be an object")
    try:
        validate_json_value(
            check_result,
            source="Check Result input to SARIF",
            limits=SARIF_JSON_LIMITS,
            expect=dict,
        )
    except StrictJsonError as exc:
        raise SarifProjectionError(str(exc)) from None

    descriptors, rule_indexes = _descriptors(check_result, policy)
    canonical = _canonical_findings(check_result)
    results: list[tuple[tuple[int, str], dict[str, Any]]] = []
    notifications: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    notification_rank = {
        finding_module.STATUS_EVALUATION_ERROR: 0,
        finding_module.STATUS_NOT_EVALUABLE: 1,
    }

    for finding, waived in canonical:
        status = finding.get("status")
        rule_id = finding.get("rule_id")
        if status == finding_module.STATUS_VIOLATED:
            projected = _result(finding, waived, rule_indexes)
            results.append(((rule_indexes[str(rule_id)], _finding_id(finding)), projected))
        elif status in notification_rank:
            projected = _notification(finding, rule_indexes)
            notifications.append((
                (
                    notification_rank[str(status)],
                    rule_indexes[str(rule_id)],
                    _finding_id(finding),
                ),
                projected,
            ))
        elif status in (
            finding_module.STATUS_PASSED,
            finding_module.STATUS_NOT_APPLICABLE,
        ):
            continue
        else:
            raise SarifProjectionError(
                f"unsupported canonical finding status {status!r}"
            )

    failure_kind = check_result.get("failure_kind")
    execution_successful = failure_kind is None
    if failure_kind is not None and not any(
        finding.get("status") == finding_module.STATUS_EVALUATION_ERROR
        for finding, _waived in canonical
    ):
        # A pre-evaluation failure has no canonical finding because no rule ran.
        # Keep it out of `results`, and omit the raw failure text because artifact
        # read errors commonly contain checkout/temp paths.
        notifications.append((
            (-1, -1, str(failure_kind)),
            {
                "level": "error",
                "message": {
                    "text": (
                        "Metrolith check did not complete policy evaluation; "
                        f"failure kind: {failure_kind}."
                    )
                },
                "properties": {"archlens": {"failureKind": failure_kind}},
            },
        ))

    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "Metrolith",
                "fullName": f"Metrolith {PROGRAM_VERSION}",
                "semanticVersion": PROGRAM_VERSION,
                "rules": descriptors,
            }
        },
        "results": [item for _key, item in sorted(results, key=lambda item: item[0])],
        "invocations": [{
            "executionSuccessful": execution_successful,
            "toolExecutionNotifications": [
                item for _key, item in sorted(notifications, key=lambda item: item[0])
            ],
        }],
        "properties": {"archlens": _run_properties(check_result)},
    }
    run_identity = (check_result.get("run") or {})
    if isinstance(run_identity, Mapping):
        run_id = run_identity.get("run_id")
        if isinstance(run_id, str) and run_id and not _contains_machine_path(run_id):
            run["automationDetails"] = {"id": run_id}

    document = {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [run],
    }
    problems = validate_sarif_subset(document)
    if problems:
        raise SarifProjectionError("invalid Metrolith SARIF projection: " + "; ".join(problems))
    return document


def serialize_sarif(document: Mapping[str, Any]) -> str:
    """Canonical UTF-8-ready JSON text, including exactly one final LF."""
    try:
        return dumps_strict(
            document,
            source="SARIF output",
            limits=SARIF_JSON_LIMITS,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            trailing_newline=True,
        )
    except StrictJsonError as exc:
        raise SarifProjectionError(str(exc)) from None


def render_sarif(check_result: Mapping[str, Any], *, policy: Any = None) -> str:
    """Project and canonically serialize one check result."""
    return serialize_sarif(project_sarif(check_result, policy=policy))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _coordinate_of(location: Any) -> tuple[str, Any, Any] | None:
    """The `(uri, startLine, endLine)` of one SARIF location, or ``None``."""
    physical = _as_mapping((_as_mapping(location) or {}).get("physicalLocation"))
    if physical is None:
        return None
    uri = (_as_mapping(physical.get("artifactLocation")) or {}).get("uri")
    if not isinstance(uri, str):
        return None
    region = _as_mapping(physical.get("region")) or {}
    return (uri, region.get("startLine"), region.get("endLine"))


def _related_location_errors(result: Mapping[str, Any], index: int) -> list[str]:
    """Prove every `relatedLocations` entry came from the finding record.

    A projector's whole promise is that it invented nothing, and a second
    locations array is a second place to invent. Each check below is a way a
    hand-edited or buggy document could claim Metrolith found something somewhere
    it never looked:

    * every entry is portable, by the same rule the primary location obeys;
    * the ordering is `(uri, startLine, endLine)`, so two runs cannot disagree;
    * no entry repeats the primary location, which would double-count one place;
    * **every entry appears in the finding's own evidence.** This is the
      anti-fabrication check: `properties.archlens.evidence.related_locations`
      is the evaluator's statement of which places the finding covers, and a
      location absent from it was not projected from anything.
    """
    raw = result.get("relatedLocations")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [f"results[{index}] relatedLocations is not an array"]

    errors: list[str] = []
    evidence = _as_mapping(
        (_as_mapping(
            (_as_mapping(result.get("properties")) or {}).get("archlens")
        ) or {}).get("evidence")
    )
    # The evidence carries the path the EVALUATOR recorded; the location carries
    # the path the projector emitted, which is the NORMALIZED form. Comparing
    # the two raw would make the projector refuse its own honest output the
    # moment a producer wrote `src/./b.py` -- portable, but not in normal form.
    # Normalizing both sides compares places rather than spellings. A declared
    # path that does not normalize at all is simply absent from this set, which
    # is correct: it cannot back a portable emitted location.
    declared: set[tuple[str, Any, Any]] = set()
    if evidence is not None:
        for item in evidence.get(RELATED_LOCATIONS_KEY) or []:
            if not isinstance(item, Mapping):
                continue
            path = normalize_repository_path(item.get("path"))
            if path is None:
                continue
            declared.add((path, item.get("start_line"), item.get("end_line")))

    primary = {
        coordinate for coordinate in (
            _coordinate_of(location) for location in result.get("locations") or []
        ) if coordinate is not None
    }

    coordinates: list[tuple[str, Any, Any]] = []
    for position, location in enumerate(raw):
        coordinate = _coordinate_of(location)
        if coordinate is None:
            errors.append(
                f"results[{index}].relatedLocations[{position}] is not a "
                f"physical location"
            )
            continue
        uri, _start, _end = coordinate
        if normalize_repository_path(uri) != uri:
            errors.append(
                f"results[{index}].relatedLocations[{position}] is not portable"
            )
        if coordinate in primary:
            errors.append(
                f"results[{index}].relatedLocations[{position}] repeats the "
                f"primary location"
            )
        if coordinate not in declared:
            errors.append(
                f"results[{index}].relatedLocations[{position}] is not present "
                f"in the finding's evidence"
            )
        coordinates.append(coordinate)

    normalized = [
        (uri, start if isinstance(start, int) else -1,
         end if isinstance(end, int) else -1)
        for uri, start, end in coordinates
    ]
    if normalized != sorted(normalized):
        errors.append(
            f"results[{index}] relatedLocations order is not deterministic"
        )
    if len(set(coordinates)) != len(coordinates):
        errors.append(f"results[{index}] repeats a related location")
    return errors


def validate_sarif_subset(document: Mapping[str, Any]) -> list[str]:
    """Offline validation of the exact SARIF subset Metrolith promises.

    This deliberately does not claim to replace or abbreviate the official
    SARIF schema.  It checks the invariants on which Metrolith' projection and
    tests rely, while the official stable schema URI remains metadata only.
    """
    errors: list[str] = []
    if not isinstance(document, Mapping):
        return ["document is not an object"]
    if document.get("version") != SARIF_VERSION:
        errors.append("version is not 2.1.0")
    if document.get("$schema") != SARIF_SCHEMA_URI:
        errors.append("$schema is not the stable SARIF 2.1.0 schema URI")
    runs = document.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        return errors + ["runs must contain exactly one run"]
    run = _as_mapping(runs[0])
    if run is None:
        return errors + ["the run is not an object"]
    driver = _as_mapping((_as_mapping(run.get("tool")) or {}).get("driver"))
    if driver is None or driver.get("name") != "Metrolith":
        errors.append("tool.driver.name is not Metrolith")
        driver = {}
    if driver.get("semanticVersion") != PROGRAM_VERSION:
        errors.append("tool.driver.semanticVersion is not the current Program version")
    rules = driver.get("rules")
    if not isinstance(rules, list):
        rules = []
        errors.append("tool.driver.rules is not an array")
    rule_ids = [
        item.get("id") for item in rules if isinstance(item, Mapping)
    ]
    if len(rule_ids) != len(rules) or any(
        not isinstance(rule_id, str) or not rule_id for rule_id in rule_ids
    ):
        errors.append("every descriptor needs a non-empty id")
    if rule_ids != sorted(rule_ids):
        errors.append("descriptor order is not deterministic rule-id order")
    if len(rule_ids) != len(set(rule_ids)):
        errors.append("descriptor ids are not unique")
    for index, descriptor in enumerate(rules):
        if not isinstance(descriptor, Mapping):
            continue
        level = (_as_mapping(descriptor.get("defaultConfiguration")) or {}).get("level")
        if level not in _LEVELS:
            errors.append(f"rules[{index}] has an unsupported default level")

    results = run.get("results")
    if not isinstance(results, list):
        errors.append("results is not an array")
        results = []
    result_order: list[tuple[int, str]] = []
    for index, result in enumerate(results):
        if not isinstance(result, Mapping):
            errors.append(f"results[{index}] is not an object")
            continue
        rule_id = result.get("ruleId")
        rule_index = result.get("ruleIndex")
        if (
            not isinstance(rule_index, int)
            or rule_index < 0
            or rule_index >= len(rule_ids)
            or rule_ids[rule_index] != rule_id
        ):
            errors.append(f"results[{index}] has an invalid rule reference")
        if result.get("level") not in _LEVELS:
            errors.append(f"results[{index}] has an unsupported level")
        if "baselineState" in result:
            errors.append(f"results[{index}] fabricates baselineState")
        message = (_as_mapping(result.get("message")) or {}).get("text")
        if not isinstance(message, str) or not message:
            errors.append(f"results[{index}] has no message text")
        fingerprint = (
            _as_mapping(result.get("partialFingerprints")) or {}
        ).get("archlensFindingId/v1")
        if not isinstance(fingerprint, str) or _FINDING_ID.fullmatch(fingerprint) is None:
            errors.append(f"results[{index}] has no canonical Metrolith fingerprint")
            fingerprint = ""
        if isinstance(rule_index, int):
            result_order.append((rule_index, fingerprint))
        for location in result.get("locations") or []:
            physical = _as_mapping((_as_mapping(location) or {}).get("physicalLocation"))
            uri = (_as_mapping((physical or {}).get("artifactLocation")) or {}).get("uri")
            if not isinstance(uri, str) or normalize_repository_path(uri) != uri:
                errors.append(f"results[{index}] has a non-portable location")
        errors.extend(_related_location_errors(result, index))
    if result_order != sorted(result_order):
        errors.append("result order is not deterministic rule/finding order")

    invocations = run.get("invocations")
    if not isinstance(invocations, list) or len(invocations) != 1:
        errors.append("invocations must contain exactly one invocation")
    elif not isinstance(invocations[0], Mapping) or not isinstance(
        invocations[0].get("executionSuccessful"), bool
    ):
        errors.append("invocation executionSuccessful is not boolean")

    try:
        serialized = dumps_strict(
            document,
            source="SARIF validation",
            limits=SARIF_JSON_LIMITS,
            ensure_ascii=False,
            sort_keys=True,
        )
    except StrictJsonError as exc:
        errors.append(str(exc))
        return errors
    if _contains_machine_path(serialized):
        errors.append("machine-local absolute path leaked into SARIF")
    return errors


__all__ = [
    "RELATED_LOCATIONS_KEY", "SARIF_VERSION", "SARIF_SCHEMA_URI",
    "SEVERITY_TO_LEVEL", "SarifProjectionError", "normalize_repository_path",
    "project_sarif", "render_sarif", "serialize_sarif", "validate_sarif_subset",
]
