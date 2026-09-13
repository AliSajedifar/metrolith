"""The Policy v2 document: user-supplied thresholds over persisted metrics.

v1 answered questions about the *measurement process* — is the artifact valid,
did the run finish, was the parser there — and deliberately carried no
threshold, because a threshold invented to look reasonable is still invented.
v2 adds thresholds and keeps that honesty by moving the authorship: **every
threshold in a v2 document is supplied by the user and is that user's policy.**

Metrolith ships no default v2 policy and no default threshold, and `check`
refuses to run without an explicit policy file. A shipped default would be an
implied recommendation, and there is no evidence for one: the differential
record shows the reference tools disagree on the values themselves. A number in
this document means "my team decided this", never "software engineering
established this".

**A rule is data, not code.** There is no expression language, no callable
hook and no arbitrary predicate — a small explicit vocabulary instead, so a
policy is reviewable by reading it and evaluable without executing anything the
document supplied.

**Nothing malformed is skipped.** Every rejection below raises. A rule quietly
dropped for being unrecognized is worse than no rule: the gate reports success
while checking nothing.
"""

from __future__ import annotations

from difflib import get_close_matches
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from modules.policy import metrics as metric_module
from modules.policy import rules as rule_module
from archlens_json import (
    POLICY_JSON_LIMITS,
    StrictJsonError,
    load_file,
    loads_bytes,
    validate_json_value,
)
from modules.policy.document import (
    POLICY_DOCUMENT_FORMAT_VERSION as POLICY_DOCUMENT_V1_FORMAT_VERSION,
)
from modules.policy.document import PolicyDocument, PolicyDocumentInvalid, Waiver
from modules.policy.document import _policy_file_error

#: The newest v2 document version this build understands. 2.1.0 added the
#: Hotspot metric vocabulary and the `hotspot_file` scope; 2.2.0 adds the
#: Duplication metric vocabulary and the `duplication_group` scope. Each is
#: additive and changes nothing else.
POLICY_DOCUMENT_V2_FORMAT_VERSION = "2.2.0"

#: Every v2 version this build accepts, oldest first. An older document keeps
#: working unchanged and keeps declaring its own version -- no file is rewritten
#: and no output claims the author wrote a version they did not write. What an
#: older document cannot do is name a newer version's metric, which is exactly
#: what its published schema says, and `load_metric_rule` enforces the same rule
#: for the many callers who never schema-validate their policy file.
POLICY_DOCUMENT_V2_FORMAT_VERSIONS: tuple[str, ...] = ("2.0.0", "2.1.0", "2.2.0")

#: Comparison vocabulary. Only operators whose meaning is unambiguous on a
#: single number appear. A rule FIRES when ``observed <operator> threshold``
#: holds, so the rule states the failing condition — `gt 30` means "fail when
#: the observed value exceeds 30", which is how every gate is read aloud.
#:
#: Deliberately absent: `between`/`in`/`matches` (two thresholds or a pattern,
#: neither expressible as one comparison), and anything requiring a second
#: measurement, a baseline or a percentage change — cross-revision rules are
#: out of scope for this release.
OPERATORS: dict[str, str] = {
    "gt": "greater than",
    "gte": "greater than or equal to",
    "lt": "less than",
    "lte": "less than or equal to",
    "eq": "equal to",
    "ne": "not equal to",
}

OPERATOR_SYMBOLS: dict[str, str] = {
    "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "==", "ne": "!=",
}

#: `eq` and `ne` on a fractional aggregate compare exact IEEE-754 doubles. The
#: only fractional metrics in the allowlist are the two `_mean` aggregates, and
#: a policy comparing one for exact equality is almost certainly a mistake, so
#: the listing says so rather than the loader guessing.
FLOAT_EQUALITY_NOTE = (
    "`eq` and `ne` compare exact IEEE-754 doubles. On a fractional aggregate "
    "(the `_mean` metrics) an exact-equality rule will rarely fire; use a "
    "range-forming pair of `gt`/`lt` rules instead."
)

#: How a rule that could not be evaluated affects the verdict.
#:
#: There is no `ignore`. A policy must never report PASS merely because the data
#: it needed was absent, so the weakest available setting still reports the
#: rule in `not_evaluable` and in the counts.
ON_NOT_EVALUABLE_FAIL = "fail"
ON_NOT_EVALUABLE_WARN = "warn"
ON_NOT_EVALUABLE = (ON_NOT_EVALUABLE_FAIL, ON_NOT_EVALUABLE_WARN)

#: How a partial observation is treated. `evaluate` compares it and stamps the
#: finding `data_completeness: partial`; `not_evaluable` routes it to the
#: non-evaluable path for callers who will not gate on an unverified value.
PARTIAL_EVALUATE = "evaluate"
PARTIAL_NOT_EVALUABLE = "not_evaluable"
PARTIAL_DATA = (PARTIAL_EVALUATE, PARTIAL_NOT_EVALUABLE)

_RULE_ID_MAXIMUM = 128
_ALLOWED_RULE_ID_CHARACTERS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)

_RULE_KEYS = frozenset({
    "id", "metric", "operator", "threshold", "scope", "severity", "languages",
    "paths", "exclude_paths", "subjects", "message", "metadata",
})

_DOCUMENT_KEYS = frozenset({
    "policy_document_format_version", "name", "description", "integrity_rules",
    "metric_rules", "options", "waivers", "expected_contracts",
})

_OPTION_KEYS = frozenset({"on_not_evaluable", "partial_data"})


@dataclass(frozen=True)
class PolicyOptions:
    """Policy-level semantics for data that is not a clean measurement."""

    on_not_evaluable: str = ON_NOT_EVALUABLE_FAIL
    partial_data: str = PARTIAL_EVALUATE

    def as_dict(self) -> dict[str, Any]:
        return {
            "on_not_evaluable": self.on_not_evaluable,
            "partial_data": self.partial_data,
        }


@dataclass(frozen=True)
class MetricRule:
    """One user-authored threshold rule.

    ``severity`` decides whether firing fails the build; it never decides
    whether the rule fires. That separation is inherited from v1 and is what
    lets a team introduce a rule as a warning before enforcing it.
    """

    identifier: str
    metric: str
    operator: str
    threshold: int | float
    scope: str
    severity: str = rule_module.SEVERITY_VIOLATION
    languages: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def definition(self) -> metric_module.MetricDefinition:
        return metric_module.METRICS_BY_ID[self.metric]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.identifier,
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "scope": self.scope,
            "severity": self.severity,
        }
        if self.languages:
            payload["languages"] = list(self.languages)
        if self.paths:
            payload["paths"] = list(self.paths)
        if self.exclude_paths:
            payload["exclude_paths"] = list(self.exclude_paths)
        if self.subjects:
            payload["subjects"] = list(self.subjects)
        if self.message is not None:
            payload["message"] = self.message
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class PolicyV2Document:
    """A complete v2 policy: integrity rules, metric rules, options, waivers."""

    name: str
    description: str = ""
    #: v1 rule id -> severity. Evaluated by the EXISTING v1 rule engine, never a
    #: reimplementation of it.
    integrity_rules: Mapping[str, str] = field(default_factory=dict)
    metric_rules: tuple[MetricRule, ...] = ()
    options: PolicyOptions = field(default_factory=PolicyOptions)
    waivers: tuple[Waiver, ...] = ()
    expected_contracts: Mapping[str, str] = field(default_factory=dict)
    #: The format version the source document declared. A v1 file adapted for
    #: `check` keeps `1.0.0` here, so the result never claims the user wrote a
    #: v2 policy they did not write.
    source_format_version: str = POLICY_DOCUMENT_V2_FORMAT_VERSION

    def severity_for_integrity(self, rule_id: str) -> str | None:
        return self.integrity_rules.get(rule_id)

    def as_v1_document(self) -> PolicyDocument:
        """The integrity half, as the v1 engine expects it."""
        return PolicyDocument(
            name=self.name,
            enabled_rules=dict(self.integrity_rules),
            waivers=self.waivers,
            expected_contracts=dict(self.expected_contracts),
            description=self.description,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            # The version the source declared, not the newest this build knows.
            # Re-stamping a 2.0.0 policy as 2.2.0 would claim the author wrote a
            # version they did not write, and would make the round trip lossy.
            "policy_document_format_version": (
                self.source_format_version
                if self.source_format_version in POLICY_DOCUMENT_V2_FORMAT_VERSIONS
                else POLICY_DOCUMENT_V2_FORMAT_VERSION
            ),
            "name": self.name,
            "description": self.description,
            "integrity_rules": dict(sorted(self.integrity_rules.items())),
            "metric_rules": [rule.as_dict() for rule in self.metric_rules],
            "options": self.options.as_dict(),
            "expected_contracts": dict(sorted(self.expected_contracts.items())),
            "waivers": [waiver.as_dict() for waiver in self.waivers],
        }


# --------------------------------------------------------------------------
# Loading. Every path either produces a complete document or raises.
# --------------------------------------------------------------------------

def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyDocumentInvalid(f"{where}: must be an object")
    return value


def _reject_unknown_keys(
    payload: Mapping[str, Any], allowed: Iterable[str], where: str
) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise PolicyDocumentInvalid(
            f"{where}: unknown key(s) {', '.join(repr(item) for item in unknown)}; "
            f"known keys: {', '.join(sorted(allowed))}. An unrecognized key is "
            f"refused rather than ignored, because a silently dropped setting "
            f"makes a policy claim protection it does not provide."
        )


def _load_rule_id(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyDocumentInvalid(f"{where}: a rule must have a non-empty string id")
    identifier = raw.strip()
    if len(identifier) > _RULE_ID_MAXIMUM:
        raise PolicyDocumentInvalid(
            f"{where}: rule id is {len(identifier)} characters; the maximum is "
            f"{_RULE_ID_MAXIMUM}"
        )
    invalid = sorted(set(identifier) - _ALLOWED_RULE_ID_CHARACTERS)
    if invalid:
        raise PolicyDocumentInvalid(
            f"{where}: rule id {identifier!r} contains {invalid!r}; ids are "
            f"limited to letters, digits and '.', '_', ':', '-' so they stay "
            f"stable identifiers in machine-readable output"
        )
    return identifier


def _load_threshold(raw: Any, where: str) -> int | float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PolicyDocumentInvalid(
            f"{where}: threshold must be a finite number; got {raw!r}"
        )
    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
        raise PolicyDocumentInvalid(
            f"{where}: threshold must be finite; got {raw!r}. A NaN threshold "
            f"makes every comparison false, so the rule would silently never "
            f"fire."
        )
    return raw


_MISSING = object()


def _load_string_list(raw: Any, where: str, key: str) -> tuple[str, ...]:
    if raw is _MISSING:
        return ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise PolicyDocumentInvalid(
            f"{where}: {key} must be an array of strings"
        )
    values: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise PolicyDocumentInvalid(
                f"{where}: {key}[{index}] must be a non-empty string"
            )
        values.append(item.strip())
    if not values:
        raise PolicyDocumentInvalid(
            f"{where}: {key} is present but empty. An empty filter is ambiguous "
            f"— omit the key to mean 'no filter'."
        )
    return tuple(values)


def _version_rank(version: str) -> int:
    try:
        return POLICY_DOCUMENT_V2_FORMAT_VERSIONS.index(version)
    except ValueError:
        return len(POLICY_DOCUMENT_V2_FORMAT_VERSIONS)


def load_metric_rule(
    payload: Any,
    where: str,
    *,
    document_version: str = POLICY_DOCUMENT_V2_FORMAT_VERSION,
) -> MetricRule:
    """Build one metric rule, refusing anything the vocabulary does not define.

    ``document_version`` is the version the DOCUMENT declared. It gates which
    metrics may be named, because the published schema gates the same thing and
    the two must not disagree: `metrolith check` loads a policy file without
    schema-validating it, so a loader that accepted what the schema refuses
    would let a 2.0.0 document gate on a vocabulary it does not declare.
    """
    raw = _require_mapping(payload, where)
    _reject_unknown_keys(raw, _RULE_KEYS, where)

    identifier = _load_rule_id(raw.get("id"), where)

    metric_id = raw.get("metric")
    if not isinstance(metric_id, str) or metric_id not in metric_module.METRICS_BY_ID:
        suggestions = (
            get_close_matches(metric_id, metric_module.METRIC_IDS, n=3, cutoff=0.45)
            if isinstance(metric_id, str)
            else []
        )
        suggestion = (
            f" Did you mean: {', '.join(suggestions)}?"
            if suggestions
            else " Run `metrolith policy metrics --format text` to browse the vocabulary."
        )
        raise PolicyDocumentInvalid(
            f"{where}: unknown metric {metric_id!r}. Policy v2 gates only on "
            f"metrics Metrolith already persists; no metric may be invented for a "
            f"policy.{suggestion}"
        )
    definition = metric_module.METRICS_BY_ID[metric_id]

    minimum = definition.minimum_document_version
    if minimum is not None and _version_rank(document_version) < _version_rank(minimum):
        raise PolicyDocumentInvalid(
            f"{where}: metric {metric_id!r} was introduced in policy document "
            f"{minimum}, and this document declares {document_version}. Raise "
            f"`policy_document_format_version` to {minimum} to use it; the "
            f"version is what tells a reader which vocabulary a document may "
            f"use, so a document may not quietly reach past its own."
        )

    operator = raw.get("operator")
    if operator not in OPERATORS:
        raise PolicyDocumentInvalid(
            f"{where}: invalid operator {operator!r}; supported operators are "
            f"{', '.join(sorted(OPERATORS))}"
        )

    threshold = _load_threshold(raw.get("threshold"), where)

    scope = raw.get("scope", definition.scope)
    if scope not in metric_module.SCOPES:
        raise PolicyDocumentInvalid(
            f"{where}: unknown scope {scope!r}; supported scopes are "
            f"{', '.join(metric_module.SCOPES)}"
        )
    if scope != definition.scope:
        raise PolicyDocumentInvalid(
            f"{where}: metric {metric_id!r} is a {definition.scope!r}-scope "
            f"metric and cannot be evaluated at {scope!r} scope. Metrolith "
            f"persists it at one scope only; evaluating it at another would "
            f"require deriving a value nobody measured."
        )

    severity = raw.get("severity", rule_module.SEVERITY_VIOLATION)
    if severity not in rule_module.SEVERITIES:
        raise PolicyDocumentInvalid(
            f"{where}: severity {severity!r} is not one of "
            f"{', '.join(rule_module.SEVERITIES)}"
        )

    languages = _load_string_list(
        raw["languages"] if "languages" in raw else _MISSING,
        where,
        "languages",
    )
    if languages and scope == metric_module.SCOPE_REPOSITORY:
        raise PolicyDocumentInvalid(
            f"{where}: `languages` scopes a rule to a subset of languages, which "
            f"a repository-scope metric has no notion of. Use a "
            f"`language.`-prefixed metric instead."
        )

    paths = _load_string_list(
        raw["paths"] if "paths" in raw else _MISSING, where, "paths"
    )
    exclude_paths = _load_string_list(
        raw["exclude_paths"] if "exclude_paths" in raw else _MISSING,
        where,
        "exclude_paths",
    )
    if (paths or exclude_paths) and scope not in metric_module.PATH_BEARING_SCOPES:
        raise PolicyDocumentInvalid(
            f"{where}: `paths`/`exclude_paths` need a per-file location, which "
            f"only a {' or '.join(metric_module.PATH_BEARING_SCOPES)}-scope "
            f"metric has. A {scope!r}-scope aggregate covers the whole scope "
            f"and cannot be narrowed to a path."
        )

    subjects = _load_string_list(
        raw["subjects"] if "subjects" in raw else _MISSING, where, "subjects"
    )

    message = raw.get("message")
    if "message" in raw and (not isinstance(message, str) or not message.strip()):
        raise PolicyDocumentInvalid(f"{where}: message must be a non-empty string")

    metadata = raw.get("metadata")
    if "metadata" in raw and not isinstance(metadata, Mapping):
        raise PolicyDocumentInvalid(f"{where}: metadata must be an object")

    return MetricRule(
        identifier=identifier, metric=metric_id, operator=operator,
        threshold=threshold, scope=scope, severity=severity,
        languages=languages, paths=paths, exclude_paths=exclude_paths,
        subjects=subjects,
        message=message.strip() if isinstance(message, str) else None,
        metadata=dict(metadata) if metadata is not None else {},
    )


def _load_options(payload: Any) -> PolicyOptions:
    raw = _require_mapping(payload, "options")
    _reject_unknown_keys(raw, _OPTION_KEYS, "options")

    on_not_evaluable = raw.get("on_not_evaluable", ON_NOT_EVALUABLE_FAIL)
    if on_not_evaluable not in ON_NOT_EVALUABLE:
        raise PolicyDocumentInvalid(
            f"options.on_not_evaluable must be one of "
            f"{', '.join(ON_NOT_EVALUABLE)}; got {on_not_evaluable!r}. There is "
            f"deliberately no 'ignore': a policy must never report PASS merely "
            f"because required data was absent."
        )

    partial_data = raw.get("partial_data", PARTIAL_EVALUATE)
    if partial_data not in PARTIAL_DATA:
        raise PolicyDocumentInvalid(
            f"options.partial_data must be one of {', '.join(PARTIAL_DATA)}; "
            f"got {partial_data!r}"
        )
    return PolicyOptions(
        on_not_evaluable=on_not_evaluable, partial_data=partial_data
    )


def _load_waivers(
    payload: Any, known_rule_ids: set[str]
) -> tuple[Waiver, ...]:
    """v1's bounded-waiver model, widened to cover v2 rule ids.

    The bound is unchanged and non-negotiable: a reason and an expiry. An
    unbounded waiver is indistinguishable from deleting the rule.
    """
    if isinstance(payload, Mapping) or not isinstance(payload, (list, tuple)):
        raise PolicyDocumentInvalid("waivers must be an array")
    waivers: list[Waiver] = []
    for index, raw in enumerate(payload):
        where = f"waivers[{index}]"
        item = _require_mapping(raw, where)
        rule_id = item.get("rule_id")
        if not isinstance(rule_id, str) or rule_id not in known_rule_ids:
            raise PolicyDocumentInvalid(
                f"{where}: waives unknown rule {rule_id!r}. A waiver must name a "
                f"rule the policy actually enables, or it silently protects "
                f"nothing."
            )
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PolicyDocumentInvalid(
                f"{where}: a waiver must state a reason. An unexplained waiver "
                f"is indistinguishable from deleting the rule."
            )
        if "expires_on" not in item:
            raise PolicyDocumentInvalid(
                f"{where}: a waiver must have an expiry. An unbounded waiver "
                f"decays into a permanent exception nobody agreed to."
            )
        expires = item.get("expires_on")
        if not isinstance(expires, str):
            raise PolicyDocumentInvalid(
                f"{where}: expires_on must be a YYYY-MM-DD string"
            )
        try:
            expires_on = date.fromisoformat(expires)
        except ValueError as exc:
            raise PolicyDocumentInvalid(f"{where}: {exc}") from None
        subject_key = item.get("subject_key")
        if subject_key is not None and not isinstance(subject_key, str):
            raise PolicyDocumentInvalid(f"{where}: subject_key must be a string")
        issue_id = item.get("issue_id")
        if issue_id is not None and not isinstance(issue_id, str):
            raise PolicyDocumentInvalid(f"{where}: issue_id must be a string")
        waivers.append(Waiver(
            rule_id=rule_id, reason=reason.strip(), expires_on=expires_on,
            subject_key=subject_key, issue_id=issue_id,
        ))
    return tuple(waivers)


def load_policy_v2(payload: Any) -> PolicyV2Document:
    """Build a v2 policy from a parsed document, refusing anything malformed."""
    try:
        validate_json_value(
            payload, source="policy document", limits=POLICY_JSON_LIMITS, expect=dict
        )
    except StrictJsonError as exc:
        raise PolicyDocumentInvalid(str(exc)) from None
    raw = _require_mapping(payload, "the policy document")
    _reject_unknown_keys(raw, _DOCUMENT_KEYS, "the policy document")

    declared = raw.get("policy_document_format_version")
    if declared not in POLICY_DOCUMENT_V2_FORMAT_VERSIONS:
        raise PolicyDocumentInvalid(
            f"policy_document_format_version must be one of "
            f"{', '.join(repr(item) for item in POLICY_DOCUMENT_V2_FORMAT_VERSIONS)}; "
            f"got {declared!r}"
        )

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PolicyDocumentInvalid("a policy document must have a non-empty name")

    description = raw.get("description")
    if "description" in raw and not isinstance(description, str):
        raise PolicyDocumentInvalid("description must be a string")

    integrity_raw = raw["integrity_rules"] if "integrity_rules" in raw else {}
    integrity = _require_mapping(integrity_raw, "integrity_rules")
    integrity_rules: dict[str, str] = {}
    for rule_id, severity in integrity.items():
        if rule_id not in rule_module.RULES_BY_ID:
            raise PolicyDocumentInvalid(
                f"integrity_rules: unknown rule {rule_id!r}; known rules: "
                f"{', '.join(sorted(rule_module.RULES_BY_ID))}"
            )
        if severity not in rule_module.SEVERITIES:
            raise PolicyDocumentInvalid(
                f"integrity_rules[{rule_id!r}]: severity {severity!r} is not one "
                f"of {', '.join(rule_module.SEVERITIES)}"
            )
        integrity_rules[rule_id] = severity

    metric_raw = raw["metric_rules"] if "metric_rules" in raw else []
    if isinstance(metric_raw, Mapping) or not isinstance(metric_raw, (list, tuple)):
        raise PolicyDocumentInvalid("metric_rules must be an array of rule objects")

    metric_rules: list[MetricRule] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(metric_raw):
        rule = load_metric_rule(
            item, f"metric_rules[{index}]", document_version=declared
        )
        if rule.identifier in seen:
            raise PolicyDocumentInvalid(
                f"metric_rules[{index}]: duplicate rule id {rule.identifier!r}, "
                f"first defined at metric_rules[{seen[rule.identifier]}]. Rule "
                f"ids are the stable identity of a finding, so two rules cannot "
                f"share one."
            )
        if rule.identifier in integrity_rules:
            raise PolicyDocumentInvalid(
                f"metric_rules[{index}]: rule id {rule.identifier!r} is already "
                f"an enabled integrity rule. One id must name one check."
            )
        seen[rule.identifier] = index
        metric_rules.append(rule)

    if not integrity_rules and not metric_rules:
        raise PolicyDocumentInvalid(
            "a policy document must enable at least one integrity rule or one "
            "metric rule. An empty policy passes everything and protects nothing."
        )

    expected = raw["expected_contracts"] if "expected_contracts" in raw else {}
    expected_map = _require_mapping(expected, "expected_contracts")
    for key, value in expected_map.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise PolicyDocumentInvalid(
                "expected_contracts keys and values must be strings"
            )

    return PolicyV2Document(
        name=name.strip(),
        description=description.strip() if description is not None else "",
        integrity_rules=integrity_rules,
        metric_rules=tuple(metric_rules),
        options=(
            _load_options(raw["options"])
            if "options" in raw else PolicyOptions()
        ),
        waivers=_load_waivers(
            raw["waivers"] if "waivers" in raw else [],
            set(integrity_rules) | set(seen),
        ),
        expected_contracts=dict(expected_map),
        source_format_version=declared,
    )


def adapt_v1_document(document: PolicyDocument) -> PolicyV2Document:
    """Present a v1 policy to the v2 evaluator without reinterpreting it.

    The same rules, the same severities, the same waivers and the same pinned
    contracts, with no metric rules. `check` therefore works on the policy files
    teams already have, and a regression test asserts the integrity findings are
    the same set `metrolith policy evaluate` reports for the same document.

    The declared format version is carried through as `1.0.0`, so no output ever
    claims the user wrote a v2 policy.
    """
    return PolicyV2Document(
        name=document.name,
        description=document.description,
        integrity_rules=dict(document.enabled_rules),
        metric_rules=(),
        options=PolicyOptions(),
        waivers=tuple(document.waivers),
        expected_contracts=dict(document.expected_contracts),
        source_format_version=POLICY_DOCUMENT_V1_FORMAT_VERSION,
    )


def load_any_policy(payload: Any) -> PolicyV2Document:
    """Load a v1 or v2 document, dispatching on its declared version.

    Dispatch is on the declared version alone. Guessing from the keys present
    would let a typo in `policy_document_format_version` silently change which
    contract a file is judged against.
    """
    if not isinstance(payload, Mapping):
        raise PolicyDocumentInvalid("a policy document must be a JSON object")
    declared = payload.get("policy_document_format_version")
    if declared == POLICY_DOCUMENT_V1_FORMAT_VERSION:
        from modules.policy.document import load_policy

        return adapt_v1_document(load_policy(payload))
    if declared in POLICY_DOCUMENT_V2_FORMAT_VERSIONS:
        return load_policy_v2(payload)
    raise PolicyDocumentInvalid(
        f"policy_document_format_version must be "
        f"{POLICY_DOCUMENT_V1_FORMAT_VERSION!r} or one of "
        f"{', '.join(repr(item) for item in POLICY_DOCUMENT_V2_FORMAT_VERSIONS)}; "
        f"got {declared!r}"
    )


def load_any_policy_file(path: Path) -> PolicyV2Document:
    """Read and load a policy file of either version."""
    try:
        payload = load_file(
            path, source=str(path), limits=POLICY_JSON_LIMITS, expect=dict
        )
    except StrictJsonError as exc:
        raise _policy_file_error(Path(path), exc) from None
    return load_any_policy(payload)


def load_any_policy_bytes(payload: bytes, *, source: str = "policy document") -> PolicyV2Document:
    """Parse and load the exact policy bytes already authenticated by a caller."""

    try:
        document = loads_bytes(
            payload, source=source, limits=POLICY_JSON_LIMITS, expect=dict
        )
    except StrictJsonError as exc:
        raise _policy_file_error(Path(source), exc) from None
    return load_any_policy(document)
