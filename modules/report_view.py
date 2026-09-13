"""Metrolith Report View 1.0 deterministic presentation-data builder.

Report View is a derived, UI-neutral document.  It is not a run artifact or an
evidence format, and no consumer may feed it into Policy, Ratchet, duplication,
hotspot, or changed-code analysis.  This builder only selects persisted values
from already validated sources and attaches traceable source references.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from archlens_json import ARTIFACT_JSON_LIMITS, dumps_strict
from modules.presentation import status as display_status
from modules.presentation import subject_display
from modules.run_artifacts import artifact_slug
from modules.report_composition import (
    CHECK_RESULT_FORMAT,
    admit_supplement,
    analyzed_scopes,
    changed_provenance,
    check_provenance,
    duplication_provenance,
    hotspot_provenance,
    report_view_admission_state,
)
from modules.standalone_contracts import (
    CHANGED_CODE_FORMAT,
    DUPLICATION_FORMAT,
    HOTSPOT_FORMAT,
)


REPORT_VIEW_FORMAT = "archlens-report-view"
REPORT_VIEW_FORMAT_VERSION = "1.0.0"
REPORT_VIEW_BUILDER = "archlens-report-view-builder"
REPORT_VIEW_BUILDER_VERSION = "1.0.0"
# Report View 1.0's immutable schema pins the builder-program identity to the
# version under which this technical format was defined. The source evaluator's
# current Program version remains separately preserved in ``source_documents``;
# changing this value would rewrite the frozen format rather than rebrand it.
REPORT_VIEW_BUILDER_PROGRAM_VERSION = "3.8.0"

_CORE_METRICS = (
    ("source_files", "source_files_status"),
    ("lines_of_code", "loc_status"),
    ("classes_structs", "classes_structs_status"),
    ("methods_functions", "methods_functions_status"),
)
_PATH_DRIVE = re.compile(r"(?:^|[\s'\"])[A-Za-z]:[\\/]")
_PATH_UNIX = re.compile(r"(?:^|[\s'\"])/(?:Users|home|root|tmp|var|opt|etc)/")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|client[_-]?secret|"
    r"user(?:name)?|host(?:name)?)\s*[:=]\s*[^\s,;]+"
)
_ACTIVE_MARKUP = re.compile(r"(?i)<\s*(?:script|iframe|object|embed)\b|javascript\s*:")
_BIDI_CONTROL = re.compile("[\u202a-\u202e\u2066-\u2069]")


class ReportViewBuildError(ValueError):
    """Validated inputs cannot be represented without changing their meaning."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_source_bytes(document: Mapping[str, Any]) -> bytes:
    return dumps_strict(
        dict(document),
        source="Report View source document",
        limits=ARTIFACT_JSON_LIMITS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        trailing_newline=True,
    ).encode("utf-8")


def _safe_text(value: Any, *, maximum: int = 4096) -> str | None:
    if value is None:
        return None
    original = str(value)
    unsafe_control = any(
        ord(character) < 32 and character not in "\t\n\r"
        for character in original
    )
    text = "".join(
        character for character in original
        if ord(character) >= 32 or character in "\t\n\r"
    )
    if (
        _PATH_DRIVE.search(text)
        or _PATH_UNIX.search(text)
        or _SENSITIVE_TEXT.search(text)
        or _ACTIVE_MARKUP.search(text)
        or _BIDI_CONTROL.search(text)
        or unsafe_control
    ):
        return "unsafe source text omitted"
    return text[:maximum]


def _status(
    raw: Any,
    *,
    vocabulary: str,
    axis: str,
    reason_code: str | None = None,
    reason_text: Any = None,
    value: Any = None,
) -> dict[str, Any]:
    source = "missing" if raw is None else str(raw)
    normalized = {
        "completed": "completed",
        "completed_with_errors": "completed_with_errors",
        "failed": "failed" if axis == "run_lifecycle" else "unavailable",
        "complete": "complete",
        "partial": "partial",
        "unavailable": "unavailable",
        "not_applicable": "not_applicable",
        "absent": "absent",
        "missing": "missing",
        "measured": "complete",
        "passed": "passed",
        "pass": "passed",
        "violated": "violated",
        "fail": "violated",
        "not_evaluable": "not_evaluable",
        "waived": "waived",
        "error": "error",
        "evaluation_error": "error",
        "not_evaluated": "not_evaluated",
        "not_requested": "not_evaluated",
        "admitted": "admitted",
        "not_supplied": "not_supplied",
        "incompatible": "incompatible",
        "contract_incompatible": "incompatible",
        "provenance_mismatch": "provenance_mismatch",
        "unreadable": "unreadable",
        "ambiguous": "ambiguous",
        "refused": "refused",
        "validator_rejected": "refused",
        "local_unprotected": "local_unprotected",
        "protected_required": "protected_required",
    }.get(source, source)
    measured = normalized in {"complete", "partial"} and value is not None
    permits = normalized in {
        "complete", "partial", "passed", "violated", "waived"
    }
    is_pass: bool | None = None
    if normalized == "passed":
        is_pass = True
    elif normalized in {"violated", "error"}:
        is_pass = False
    return {
        "source_vocabulary": vocabulary,
        "raw_source_code": source,
        "presentation_axis": axis,
        "presentation_code": normalized,
        "display_label": display_status(normalized, absent="missing"),
        "meaning": _meaning(normalized),
        "reason_code": reason_code,
        "reason_text": _safe_text(reason_text),
        "measured_value": measured,
        "permits_evaluation": permits,
        "pass": is_pass,
    }


def _meaning(code: str) -> str:
    meanings = {
        "completed": "the run completed without a recorded terminal error",
        "completed_with_errors": "the run completed and recorded one or more errors",
        "failed": "the run ended in failure",
        "complete": "the source records complete measured evidence",
        "partial": "the source records measured evidence with known gaps",
        "unavailable": "the measurement or evidence could not be obtained",
        "not_applicable": "the measurement does not apply to this population",
        "absent": "this artifact generation contains no such evidence",
        "missing": "a required or expected source value is missing",
        "passed": "the source authority evaluated the condition as passing",
        "violated": "the source authority evaluated the condition as violated",
        "not_evaluable": "the source authority could not evaluate the condition",
        "waived": "the source authority recorded an applicable waiver",
        "error": "the source authority recorded an evaluation error",
        "not_evaluated": "no evaluation was performed",
        "admitted": "the supplied document passed contract and provenance admission",
        "not_supplied": "no document of this kind was supplied",
        "incompatible": "the supplied document has an incompatible contract identity",
        "provenance_mismatch": "the document describes a different run or source scope",
        "unreadable": "the supplied document could not be read as strict JSON",
        "ambiguous": "more than one subject could match and none was selected",
        "refused": "the supplied document was rejected and contributes no evidence",
        "local_unprotected": "the evaluation is local and not an externally protected gate",
        "protected_required": "the source result records protected-gate requirements",
    }
    return meanings.get(code, f"source-defined state: {code}")[:512]


def _source_reference(
    source: Mapping[str, Any],
    *,
    subject_key: str | None = None,
    row_id: str | None = None,
    pointer: str | None = None,
    ledger_row_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source_role": source["role"],
        "format": source["format"],
        "format_version": source["format_version"],
        "sha256": source["sha256"],
        "portable_path": source["portable_path"],
        "subject_key": subject_key,
        "row_id": row_id,
        "logical_pointer": pointer,
        "ledger_row_id": ledger_row_id,
    }


def _run_sources(view) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    version = str(view.manifest.get("artifact_schema_version") or "unknown")
    sources: list[dict[str, Any]] = []
    for role, relative, format_name in (
        ("run_manifest", "run_manifest.json", "archlens-run-manifest"),
        ("run_status", "run_status.json", "archlens-run-status"),
        ("environment", "environment.json", "archlens-environment"),
        ("analysis", "analysis.json", "archlens-analysis"),
        ("sheet_metrics", "sheet_metrics.csv", "archlens-sheet-metrics"),
        ("language_metrics", "language_metrics.csv", "archlens-language-metrics"),
        ("callables", "callables.csv", "archlens-callable-ledger"),
        ("errors", "errors.csv", "archlens-error-ledger"),
        ("recoveries", "recoveries.csv", "archlens-recovery-ledger"),
        ("catalog", "catalog.csv", "archlens-catalog"),
    ):
        path = Path(view.run_directory) / relative
        if not path.is_file():
            continue
        if relative.endswith(".json"):
            payload = view.reader.document_bytes(relative)
        else:
            payload = path.read_bytes()
        sources.append({
            "role": role,
            "format": format_name,
            "format_version": version,
            "sha256": _sha256(payload),
            "portable_path": relative,
            "subject_key": None,
        })
    inventory_subjects = {
        f"file_inventory/{artifact_slug(result)}.json": str(result.get("subject_key"))
        for result in view.repositories
    }
    inventories = getattr(view, "inventories", {})
    for index, (relative, inventory) in enumerate(sorted(inventories.items())):
        sources.append({
            "role": f"file_inventory:{index:06d}",
            "format": "archlens-file-inventory",
            "format_version": str(inventory.get("inventory_schema_version") or "unknown"),
            "sha256": _sha256(view.reader.document_bytes(relative)),
            "portable_path": relative,
            "subject_key": inventory_subjects.get(relative),
        })
    sources.sort(key=lambda item: (item["role"], item["portable_path"]))
    return sources, {item["role"]: item for item in sources}


def _supplement_source(
    *, kind: str, document: Mapping[str, Any], format_name: str, version: str
) -> dict[str, Any]:
    return {
        "role": kind,
        "format": format_name,
        "format_version": version,
        "sha256": _sha256(_canonical_source_bytes(document)),
        "portable_path": f"supplements/{kind}.json",
        "subject_key": None,
    }


def _named_values(
    values: Mapping[str, Any],
    *,
    subject_key: str,
    source: Mapping[str, Any],
    pointer: str,
    status_raw: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in sorted(values.items()):
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        rows.append({
            "subject_key": subject_key,
            "name": str(name),
            "value": value,
            "status": _status(
                status_raw,
                vocabulary="artifact_measurement",
                axis="measurement",
                value=value,
            ),
            "source_reference": _source_reference(
                source,
                subject_key=subject_key,
                pointer=f"{pointer}/{name}",
            ),
        })
    return rows


def _core_measurements(view, by_source) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(view.repositories):
        subject = str(result.get("subject_key") or "")
        aggregate = (result.get("metrics") or {}).get("aggregate") or {}
        for metric, status_field in _CORE_METRICS:
            value = aggregate.get(metric)
            raw_status = aggregate.get(status_field)
            rows.append({
                "subject_key": subject,
                "metric": metric,
                "value": value,
                "status": _status(
                    raw_status,
                    vocabulary="metric_contract",
                    axis="measurement",
                    value=value,
                ),
                "contract_version": result.get("metric_contract_version"),
                "source_reference": _source_reference(
                    by_source["analysis"],
                    subject_key=subject,
                    row_id=f"{subject}:{metric}",
                    pointer=f"/{index}/metrics/aggregate/{metric}",
                ),
            })
    return sorted(rows, key=lambda row: (row["subject_key"], row["metric"]))


def _language_measurements(view, by_source) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(view.language_metrics):
        item = dict(row)
        subject = str(item.get("subject_key") or "")
        language = str(item.get("language") or "").casefold()
        metrics = [
            {
                "metric": metric,
                "value": item.get(metric),
                "status": _status(
                    item.get(status_field),
                    vocabulary="metric_contract",
                    axis="measurement",
                    value=item.get(metric),
                ),
            }
            for metric, status_field in _CORE_METRICS
        ]
        rows.append({
            "subject_key": subject,
            "language": language,
            "metrics": metrics,
            "completeness": _status(
                item.get("metric_status"),
                vocabulary="metric_contract",
                axis="measurement",
            ),
            "source_reference": _source_reference(
                by_source["language_metrics"],
                subject_key=subject,
                row_id=f"{subject}:{language}",
                pointer=f"/rows/{index}",
            ),
        })
    return sorted(rows, key=lambda row: (row["subject_key"], row["language"]))


def _callables(view, by_source) -> list[dict[str, Any]]:
    if "callables" not in by_source:
        return []
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        (dict(row) for row in view.stream_callables()),
        key=lambda row: (
            str(row.get("subject_key") or ""),
            str(row.get("relative_path") or ""),
            int(row.get("start_line") or 0),
            str(row.get("callable_row_id") or ""),
        ),
    )
    for index, row in enumerate(ordered):
        subject = str(row.get("subject_key") or "")
        status_raw = row.get("structural_complexity_status")
        rows.append({
            "row_id": row.get("callable_row_id"),
            "subject_key": subject,
            "language": str(row.get("detected_language") or "unknown").casefold(),
            "relative_path": str(row.get("relative_path") or ""),
            "qualified_name": str(row.get("qualified_name") or ""),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
            "nloc": row.get("nloc"),
            "cyclomatic_complexity": row.get("cyclomatic_complexity"),
            "cognitive_complexity": row.get("cognitive_complexity"),
            "nesting_depth": row.get("max_nesting_depth"),
            "formal_parameter_count": row.get("formal_parameter_count"),
            "raw_status": str(status_raw or "missing"),
            "completeness": str(row.get("nloc_status") or "missing"),
            "status": _status(
                status_raw,
                vocabulary="complexity_contract",
                axis="measurement",
                value=row.get("cyclomatic_complexity"),
            ),
            "source_reference": _source_reference(
                by_source["callables"],
                subject_key=subject,
                row_id=str(row.get("callable_row_id") or ""),
                pointer=f"/rows/{index}",
                ledger_row_id=str(row.get("callable_row_id") or ""),
            ),
        })
    return rows


def _subjects(view, by_source) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(view.repositories):
        display = subject_display(result)
        acquisition = result.get("acquisition") or {}
        languages = sorted({
            str(row.get("language") or "").casefold()
            for row in view.language_metrics
            if row.get("subject_key") == display.subject_key
            and row.get("language")
        })
        rows.append({
            "subject_key": display.subject_key,
            "subject_key_portable": bool(result.get("subject_key_portable")),
            "display_name": display.name,
            "repository_locator": display.repository_locator,
            "analyzed_revision": acquisition.get("analyzed_commit_sha"),
            "architecture_label": result.get("architecture_type"),
            "architecture_label_source": result.get("architecture_type_source")
            or "supplied_metadata",
            "canonical_languages": languages,
            "source_references": [
                _source_reference(
                    by_source["analysis"],
                    subject_key=display.subject_key,
                    row_id=display.subject_key,
                    pointer=f"/{index}",
                )
            ],
        })
    return sorted(rows, key=lambda row: row["subject_key"].casefold())


def _diagnostics(view, by_source) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "errors" in by_source:
        for index, error in enumerate(view.errors):
            item = dict(error)
            identity = _sha256(_canonical_source_bytes(item))
            rows.append({
                "diagnostic_id": f"error:{identity[:24]}",
                "kind": str(item.get("error_category") or item.get("error_type") or "error"),
                "severity": str(item.get("severity") or "error"),
                "status": _status(
                    "unavailable",
                    vocabulary="artifact_diagnostic",
                    axis="measurement",
                    reason_code=str(item.get("error_type") or "source_error"),
                    reason_text=item.get("message"),
                ),
                "subject_key": item.get("subject_key"),
                "language": item.get("detected_language"),
                "relative_path": item.get("file_path"),
                "message": _safe_text(item.get("message")),
                "recovery_strategy": None,
                "source_reference": _source_reference(
                    by_source["errors"],
                    subject_key=item.get("subject_key"),
                    row_id=f"error:{identity}",
                    pointer=f"/rows/{index}",
                    ledger_row_id=f"error:{identity}",
                ),
            })
    if "recoveries" in by_source:
        for index, recovery in enumerate(view.recoveries):
            item = dict(recovery)
            identity = _sha256(_canonical_source_bytes(item))
            rows.append({
                "diagnostic_id": f"recovery:{identity[:24]}",
                "kind": str(item.get("error_category") or "recovery"),
                "severity": "information",
                "status": _status(
                    item.get("final_file_status") or "partial",
                    vocabulary="artifact_recovery",
                    axis="measurement",
                    reason_code="recovery_applied",
                    reason_text=item.get("message"),
                ),
                "subject_key": item.get("subject_key"),
                "language": item.get("detected_language"),
                "relative_path": item.get("file_path"),
                "message": _safe_text(item.get("message")),
                "recovery_strategy": _safe_text(
                    item.get("selected_parse")
                    or item.get("parser_compatibility_strategy")
                ),
                "source_reference": _source_reference(
                    by_source["recoveries"],
                    subject_key=item.get("subject_key"),
                    row_id=f"recovery:{identity}",
                    pointer=f"/rows/{index}",
                    ledger_row_id=f"recovery:{identity}",
                ),
            })
    inventory_sources = {
        source["portable_path"]: source
        for source in by_source.values()
        if source["role"].startswith("file_inventory:")
    }
    for relative, inventory in sorted(getattr(view, "inventories", {}).items()):
        source = inventory_sources.get(relative)
        if source is None:
            continue
        for index, record in enumerate(inventory.get("files") or []):
            item = dict(record)
            kind: str | None = None
            raw_status = "unavailable"
            reason = None
            if item.get("exclusion_reason"):
                kind = "excluded_file"
                raw_status = "not_applicable"
                reason = item.get("exclusion_reason")
            elif item.get("oversized"):
                kind = "oversized_file"
                reason = "oversized"
            elif item.get("read_status") not in {None, "complete"}:
                kind = "source_read_failure"
                reason = item.get("read_error") or item.get("read_status")
            elif item.get("encoding_error"):
                kind = "source_encoding_failure"
                reason = item.get("encoding_error")
            elif item.get("parse_status") not in {None, "complete"}:
                raw_status = "partial" if item.get("parse_status") in {"partial", "recovered"} else "unavailable"
                kind = "parser_partial" if raw_status == "partial" else "parser_failure"
                reason = item.get("parse_error") or item.get("parser_diagnostic") or item.get("parse_status")
            if kind is None:
                continue
            identity = _sha256(_canonical_source_bytes({
                "source": relative,
                "path": item.get("relative_path"),
                "kind": kind,
                "reason": reason,
            }))
            rows.append({
                "diagnostic_id": f"inventory:{identity[:24]}",
                "kind": kind,
                "severity": "warning",
                "status": _status(
                    raw_status,
                    vocabulary="file_inventory",
                    axis="measurement",
                    reason_code=kind,
                    reason_text=reason,
                ),
                "subject_key": source.get("subject_key"),
                "language": item.get("detected_language"),
                "relative_path": _safe_text(item.get("relative_path")),
                "message": _safe_text(reason),
                "recovery_strategy": _safe_text(item.get("parser_compatibility_strategy")),
                "source_reference": _source_reference(
                    source,
                    subject_key=source.get("subject_key"),
                    row_id=f"inventory:{identity}",
                    pointer=f"/files/{index}",
                ),
            })
    return sorted(rows, key=lambda row: row["diagnostic_id"])


def _finding_record(
    finding: Mapping[str, Any], index: int, source: Mapping[str, Any]
) -> dict[str, Any]:
    status_raw = finding.get("status")
    location = {
        "relative_path": finding.get("path"),
        "start_line": finding.get("start_line"),
        "end_line": finding.get("end_line"),
    }
    return {
        "finding_id": str(finding.get("finding_id") or f"finding:{index}"),
        "rule_id": str(finding.get("rule_id") or ""),
        "metric": finding.get("metric"),
        "scope": str(finding.get("scope") or ""),
        "subject_key": finding.get("subject_key"),
        "language": finding.get("language"),
        "status": _status(
            status_raw,
            vocabulary="check_result",
            axis="policy",
            reason_code=finding.get("reason"),
            reason_text=finding.get("message"),
            value=finding.get("observed_value"),
        ),
        "severity": str(finding.get("severity") or "info"),
        "message": _safe_text(finding.get("message")) or "",
        "observed_value": finding.get("observed_value"),
        "operator": finding.get("operator"),
        "threshold": finding.get("threshold"),
        "reason": finding.get("reason"),
        "location": location,
        "related_locations": [],
        "evidence_keys": sorted(str(key) for key in (finding.get("evidence") or {})),
        "source_reference": _source_reference(
            source,
            subject_key=finding.get("subject_key"),
            row_id=str(finding.get("finding_id") or f"finding:{index}"),
            pointer=f"/findings/{index}",
        ),
    }


def _findings(policy_result, admission, source):
    if policy_result is None or admission != "admitted" or source is None:
        return {
            "evaluation": _status(
                "not_evaluated" if policy_result is None else admission,
                vocabulary="check_result",
                axis="policy",
            ),
            "check_result_identity": None,
            "verdict": None,
            "exit_code": None,
            "failure_kind": None,
            "policy_identity": None,
            "policy_digest": None,
            "trust_mode": None,
            "counts": [],
            "canonical_findings": [],
            "not_evaluable": [],
            "waived_findings": [],
            "ratchet": None,
            "source_reference": None,
        }
    policy = policy_result.get("policy") or {}
    provenance = policy_result.get("evaluated_input_provenance") or {}
    counts = [
        {"name": str(name), "value": value}
        for name, value in sorted((policy_result.get("counts") or {}).items())
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    findings = [
        _finding_record(item, index, source)
        for index, item in enumerate(policy_result.get("findings") or [])
    ]
    not_evaluable = [
        _finding_record(item, index, source)
        for index, item in enumerate(policy_result.get("not_evaluable") or [])
    ]
    waived = [
        _finding_record(item, index, source)
        for index, item in enumerate(policy_result.get("waived_findings") or [])
    ]
    ratchet_source = policy_result.get("ratchet") or {}
    baseline = ratchet_source.get("baseline") or {}
    ratchet = {
        "configured": bool(ratchet_source.get("configured")),
        "status": ratchet_source.get("status"),
        "admission_status": ratchet_source.get("admission_status"),
        "evaluated_count": ratchet_source.get("evaluated_count", 0),
        "violated_count": ratchet_source.get("violated_count", 0),
        "not_evaluable_count": ratchet_source.get("not_evaluable_count", 0),
        "paired_subject_count": ratchet_source.get("paired_subject_count", 0),
        "baseline_format": baseline.get("format"),
        "baseline_format_version": baseline.get("format_version"),
        "baseline_digest": baseline.get("verified_sha256"),
        "baseline_source_run_id": baseline.get("source_run_id"),
        "source_reference": _source_reference(source, pointer="/ratchet"),
    }
    return {
        "evaluation": _status(
            policy_result.get("verdict"),
            vocabulary="check_result",
            axis="policy",
        ),
        "check_result_identity": {
            "format": CHECK_RESULT_FORMAT,
            "format_version": policy_result.get("check_result_format_version"),
        },
        "verdict": policy_result.get("verdict"),
        "exit_code": policy_result.get("exit_code"),
        "failure_kind": policy_result.get("failure_kind"),
        "policy_identity": {
            "name": policy.get("name"),
            "format_version": policy.get("policy_document_format_version"),
        },
        "policy_digest": (provenance.get("policy") or {}).get("sha256"),
        "trust_mode": provenance.get("mode"),
        "counts": counts,
        "canonical_findings": findings,
        "not_evaluable": not_evaluable,
        "waived_findings": waived,
        "ratchet": ratchet,
        "source_reference": _source_reference(source, pointer="/"),
    }


def _duplication_section(document, admission, source):
    empty = {
        "availability": _status(admission, vocabulary="supplement_admission", axis="admission"),
        "identities": [], "requested_kinds": [], "candidate_thresholds": [],
        "counts": [], "groups": [], "limitations": [], "source_reference": None,
    }
    if admission != "admitted" or document is None or source is None:
        return empty
    counts = document.get("counts") or {}
    groups = []
    for kind, key in (("lexical", "lexical_groups"), ("structural", "structural_groups")):
        for group_index, group in enumerate(document.get(key) or []):
            occurrences = []
            for occurrence_index, occurrence in enumerate(group.get("occurrences") or []):
                occurrences.append({
                    "occurrence_id": occurrence.get("occurrence_id"),
                    "relative_path": occurrence.get("path"),
                    "start_line": occurrence.get("start_line"),
                    "end_line": occurrence.get("end_line"),
                    "duplicated_nloc": occurrence.get("duplicated_nloc"),
                    "source_reference": _source_reference(
                        source,
                        row_id=occurrence.get("occurrence_id"),
                        pointer=f"/{key}/{group_index}/occurrences/{occurrence_index}",
                    ),
                })
            groups.append({
                "kind": kind,
                "group_id": group.get("group_id"),
                "fingerprint": group.get("fingerprint"),
                "language": group.get("language"),
                "occurrence_count": group.get("occurrence_count"),
                "file_count": group.get("file_count"),
                "occurrences": occurrences,
                "source_reference": _source_reference(
                    source,
                    row_id=group.get("group_id"),
                    pointer=f"/{key}/{group_index}",
                ),
            })
    flat_counts = []
    for name, value in sorted(counts.items()):
        if isinstance(value, Mapping):
            for child, child_value in sorted(value.items()):
                if isinstance(child_value, (int, float)) and not isinstance(child_value, bool):
                    flat_counts.append({"name": f"{name}.{child}", "value": child_value})
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flat_counts.append({"name": str(name), "value": value})
    return {
        "availability": _status(document.get("status"), vocabulary="duplication", axis="measurement"),
        "identities": [
            {"name": "format", "value": document.get("format")},
            {"name": "format_version", "value": document.get("format_version")},
            {"name": "analysis_contract_version", "value": document.get("duplication_contract_version")},
        ],
        "requested_kinds": sorted(str(item) for item in document.get("requested_kinds") or []),
        "candidate_thresholds": [
            {"name": str(name), "value": value}
            for name, value in sorted(((document.get("provenance") or {}).get("candidate_thresholds") or {}).items())
        ],
        "counts": flat_counts,
        "groups": sorted(groups, key=lambda row: (row["kind"], str(row["group_id"]))),
        "limitations": [
            "clone groups are evidence, not defect claims",
            "the source contract defines no universal threshold or score",
        ],
        "source_reference": _source_reference(source, pointer="/"),
    }


def _hotspot_section(document, admission, source):
    rows = []
    if admission == "admitted" and document is not None and source is not None:
        for index, item in enumerate(document.get("hotspots") or []):
            rows.append({
                "subject_key": item.get("subject_key"),
                "relative_path": item.get("path"),
                "language": item.get("language"),
                "complexity_status": (item.get("complexity") or {}).get("status"),
                "complexity_value": (item.get("complexity") or {}).get("cognitive_complexity_total"),
                "complexity_signal": (item.get("complexity_signal") or {}).get("signal"),
                "churn_status": (item.get("churn") or {}).get("status"),
                "churn_value": (item.get("churn") or {}).get("commits"),
                "churn_signal": (item.get("churn_signal") or {}).get("signal"),
                "attention_class": item.get("classification"),
                "reasons": [_safe_text(reason) or "" for reason in item.get("reasons") or []],
                "source_reference": _source_reference(
                    source,
                    subject_key=item.get("subject_key"),
                    row_id=f"{item.get('subject_key')}:{item.get('path')}",
                    pointer=f"/hotspots/{index}",
                ),
            })
    counts = []
    if admission == "admitted" and document is not None:
        from modules.hotspots import attention_class_counts

        counts.append({"name": "row_count", "value": len(document.get("hotspots") or [])})
        counts.extend(
            {"name": f"attention.{name}", "value": value}
            for name, value in sorted(attention_class_counts(document).items())
        )
    return {
        "availability": _status(
            document.get("status") if admission == "admitted" and document else admission,
            vocabulary="hotspots" if admission == "admitted" else "supplement_admission",
            axis="measurement" if admission == "admitted" else "admission",
        ),
        "identities": [] if not document or admission != "admitted" else [
            {"name": "format", "value": document.get("format")},
            {"name": "format_version", "value": document.get("format_version")},
        ],
        "counts": counts,
        "rows": sorted(rows, key=lambda row: (str(row["subject_key"]), str(row["relative_path"]))),
        "limitations": [] if not rows else [
            "complexity and churn remain separate signals",
            "attention classes are not defect probabilities or scores",
        ],
        "source_reference": _source_reference(source, pointer="/") if rows else None,
    }


def _changed_section(document, admission, source):
    files = []
    if admission == "admitted" and document is not None and source is not None:
        for file_index, item in enumerate(document.get("file_changes") or []):
            hunks = []
            for hunk_index, hunk in enumerate((item.get("hunks") or {}).get("items") or []):
                hunks.append({
                    "ordinal": hunk.get("ordinal"),
                    "base_start_line": (hunk.get("base") or {}).get("start_line"),
                    "base_line_count": (hunk.get("base") or {}).get("line_count"),
                    "head_start_line": (hunk.get("head") or {}).get("start_line"),
                    "head_line_count": (hunk.get("head") or {}).get("line_count"),
                    "added_lines": hunk.get("added_diff_lines"),
                    "deleted_lines": hunk.get("deleted_diff_lines"),
                    "source_reference": _source_reference(
                        source,
                        row_id=f"{item.get('file_change_id')}:hunk:{hunk.get('ordinal')}",
                        pointer=f"/file_changes/{file_index}/hunks/items/{hunk_index}",
                    ),
                })
            files.append({
                "file_change_id": item.get("file_change_id"),
                "change_classification": item.get("change_kind"),
                "base_path": item.get("base_path"),
                "head_path": item.get("head_path"),
                "scope": item.get("scope"),
                "hunks": hunks,
                "source_reference": _source_reference(
                    source,
                    row_id=item.get("file_change_id"),
                    pointer=f"/file_changes/{file_index}",
                ),
            })
    return {
        "availability": _status(
            document.get("status") if document and admission == "admitted" else admission,
            vocabulary="changed_code" if admission == "admitted" else "supplement_admission",
            axis="measurement" if admission == "admitted" else "admission",
        ),
        "identity": None if not document or admission != "admitted" else {
            "format": document.get("format"),
            "format_version": document.get("format_version"),
            "base_revision": (document.get("base") or {}).get("resolved_commit_sha"),
            "head_revision": (document.get("head") or {}).get("resolved_commit_sha"),
            "repository_subject_key": (document.get("subject") or {}).get("subject_key"),
        },
        "comparability": [] if not document or admission != "admitted" else [
            {"name": str(name), "value": value}
            for name, value in sorted((document.get("comparability") or {}).items())
            if isinstance(value, bool)
        ],
        "counts": [] if not document or admission != "admitted" else [
            {"name": str(name), "value": value}
            for name, value in sorted((document.get("counts") or {}).items())
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ],
        "files": sorted(files, key=lambda row: str(row["file_change_id"])),
        "limitations": [] if not files else [
            "callable observations are side-local and do not assert cross-revision identity",
            "diff bodies and source bodies are not included",
        ],
        "source_reference": _source_reference(source, pointer="/") if files else None,
    }


def _trust(view, policy_result, policy_admission, by_source):
    admitted = policy_result if policy_admission == "admitted" else None
    provenance = (admitted or {}).get("evaluated_input_provenance") or {}
    mode = str(provenance.get("mode") or "local_unprotected")
    evaluator = provenance.get("evaluator") or {}
    try:
        from modules.ratchet.semantics import semantics_from_manifest

        semantics = semantics_from_manifest(view.manifest).to_dict()
    except Exception:  # historical or incomplete run; absence remains explicit
        semantics = None
    contracts = [
        {"name": name, "version": str(value)}
        for name, value in sorted({
            "artifact_schema": view.manifest.get("artifact_schema_version"),
            "metric_contract": view.manifest.get("metric_contract_version"),
            "complexity_contract": view.manifest.get("complexity_contract_version"),
            "exclusion_policy": view.manifest.get("exclusion_policy_version"),
            "inventory_schema": view.manifest.get("inventory_schema_version"),
        }.items())
        if value is not None
    ]
    revisions = sorted({
        str((result.get("acquisition") or {}).get("analyzed_commit_sha"))
        for result in view.repositories
        if (result.get("acquisition") or {}).get("analyzed_commit_sha")
    })
    return {
        "gate_mode": mode,
        "trust_status": _status(mode, vocabulary="check_result", axis="trust"),
        "evaluator_identity": {
            "identity_kind": evaluator.get("identity_kind")
            or view.manifest.get("profiler_provenance_kind")
            or "git_worktree",
            "revision": evaluator.get("revision")
            or view.manifest.get("profiler_git_commit_sha"),
            "source_sha256": evaluator.get("source_sha256")
            or view.manifest.get("profiler_source_sha256"),
            "program_version": evaluator.get("installed_version")
            or view.manifest.get("program_version"),
            "trust": evaluator.get("trust") or "locally_trusted_only",
        },
        "policy_digest": (provenance.get("policy") or {}).get("sha256"),
        "policy_trust": (provenance.get("policy") or {}).get("trust"),
        "run_manifest_digest": by_source["run_manifest"]["sha256"],
        "evidence_states": [
            {"kind": str(kind), "state": values.get("state"), "sha256": values.get("sha256")}
            for kind, values in sorted((provenance.get("evidence") or {}).items())
        ],
        "trusted_evidence_receipt": provenance.get("trusted_evidence_receipt"),
        "ratchet_baseline_digest": ((provenance.get("ratchet") or {}).get("baseline_sha256")),
        "source_run_binding": str(view.run_id) if view.run_id is not None else None,
        "measurement_semantics": semantics,
        "contracts": contracts,
        "analyzed_revisions": revisions,
        "reproduction_components": [],
        "limitations": [
            "Report View generation does not upgrade source trust",
            "no reproduction command is synthesized from incomplete evidence",
        ],
    }


def _privacy(document: Mapping[str, Any]) -> dict[str, Any]:
    subjects = document["subjects"]
    callable_rows = document["measurements"]["callable_records"]
    findings = document["findings"]["canonical_findings"]
    return {
        "repository_locator_present": any(row["repository_locator"] for row in subjects),
        "commit_hashes_present": any(row["analyzed_revision"] for row in subjects),
        "subject_keys_present": bool(subjects),
        "relative_paths_present": bool(callable_rows or document["diagnostics"]),
        "callable_names_present": bool(callable_rows),
        "policy_names_or_messages_present": bool(findings or document["findings"]["policy_identity"]),
        "evaluator_identity_present": document["trust_and_reproducibility"]["evaluator_identity"] is not None,
        "digests_present": bool(document["source_documents"]),
        "bounded_error_text_present": any(row["message"] for row in document["diagnostics"]),
        "source_bodies_present": False,
        "diff_bodies_present": False,
        "absolute_windows_paths_present": False,
        "absolute_unix_paths_present": False,
        "usernames_present": False,
        "hostnames_present": False,
        "environment_dumps_present": False,
        "secrets_or_tokens_present": False,
        "credentials_present": False,
        "inventory_is_trust_claim": False,
    }


def build_report_view(
    view,
    *,
    duplication: Mapping[str, Any] | None = None,
    hotspots: Mapping[str, Any] | None = None,
    changed: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    read_errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build one Report View from a validated canonical run view."""

    if not getattr(view, "finalized", False):
        raise ReportViewBuildError("Report View requires a finalized validated run")
    sources, by_source = _run_sources(view)
    scopes = analyzed_scopes(list(view.repositories))
    run_id = str(view.run_id) if view.run_id is not None else None
    errors = dict(read_errors or {})
    inputs = (
        ("duplication", duplication, DUPLICATION_FORMAT, lambda item: duplication_provenance(item, scopes)),
        ("hotspots", hotspots, HOTSPOT_FORMAT, lambda item: hotspot_provenance(item, run_id)),
        ("changed", changed, CHANGED_CODE_FORMAT, lambda item: changed_provenance(item, scopes)),
        ("policy_result", policy_result, CHECK_RESULT_FORMAT, lambda item: check_provenance(item, run_id)),
    )
    admission_records: list[dict[str, Any]] = []
    admitted: dict[str, Mapping[str, Any] | None] = {}
    supplement_sources: dict[str, dict[str, Any]] = {}
    for kind, supplied, expected, provenance_check in inputs:
        legacy = admit_supplement(
            supplied,
            kind="policy" if kind == "policy_result" else ("changed_code" if kind == "changed" else kind),
            expected_format=expected,
            provenance_check=provenance_check,
            read_error=errors.get(kind),
        )
        state = report_view_admission_state(legacy)
        if supplied is not None:
            version = str(
                supplied.get("check_result_format_version")
                if expected == CHECK_RESULT_FORMAT
                else supplied.get("format_version")
            )
            source = _supplement_source(
                kind=kind, document=supplied, format_name=expected, version=version
            )
            supplement_sources[kind] = source
            sources.append(source)
        else:
            source = None
        admitted[kind] = supplied if state == "admitted" else None
        admission_records.append({
            "kind": kind,
            "supplied": supplied is not None or kind in errors,
            "state": _status(
                state,
                vocabulary="supplement_admission",
                axis="admission",
                reason_code=state if state != "admitted" else None,
                reason_text=legacy.get("detail"),
            ),
            "expected_format": expected,
            "document_format": legacy.get("document_format"),
            "document_format_version": legacy.get("document_format_version"),
            "detail": _safe_text(legacy.get("detail")),
            "matched_subject_keys": sorted(
                [str((legacy.get("provenance_evidence") or {}).get("matched_subject_key"))]
                if (legacy.get("provenance_evidence") or {}).get("matched_subject_key")
                else list((legacy.get("provenance_evidence") or {}).get("candidate_subject_keys") or [])
            ),
            "contributes_evidence": state == "admitted",
            "source_reference": _source_reference(source, pointer="/") if source else None,
        })
    sources.sort(key=lambda item: (item["role"], item["portable_path"]))

    subjects = _subjects(view, by_source)
    core = _core_measurements(view, by_source)
    language = _language_measurements(view, by_source)
    callables = _callables(view, by_source)
    structural: list[dict[str, Any]] = []
    cognitive: list[dict[str, Any]] = []
    composition: list[dict[str, Any]] = []
    for index, result in enumerate(view.repositories):
        subject = str(result.get("subject_key") or "")
        metrics = result.get("metrics") or {}
        complexity = metrics.get("complexity") or {}
        structural.extend(_named_values(
            complexity.get("aggregate") or {},
            subject_key=subject,
            source=by_source["analysis"],
            pointer=f"/{index}/metrics/complexity/aggregate",
            status_raw=complexity.get("status"),
        ))
        source_by_language = metrics.get("source_files_by_language") or {}
        composition.extend(_named_values(
            source_by_language,
            subject_key=subject,
            source=by_source["analysis"],
            pointer=f"/{index}/metrics/source_files_by_language",
            status_raw=(metrics.get("aggregate") or {}).get("source_files_status"),
        ))
        cognitive.extend(_named_values(
            complexity.get("cognitive_aggregate") or {},
            subject_key=subject,
            source=by_source["analysis"],
            pointer=f"/{index}/metrics/complexity/cognitive_aggregate",
            status_raw=complexity.get("cognitive_measurement_state") or "absent",
        ))

    policy_admission = next(row["state"]["presentation_code"] for row in admission_records if row["kind"] == "policy_result")
    policy_source = supplement_sources.get("policy_result")
    document: dict[str, Any] = {
        "format": REPORT_VIEW_FORMAT,
        "format_version": REPORT_VIEW_FORMAT_VERSION,
        "document_identity": {
            "format": REPORT_VIEW_FORMAT,
            "format_version": REPORT_VIEW_FORMAT_VERSION,
            "program_version": REPORT_VIEW_BUILDER_PROGRAM_VERSION,
            "builder_contract": REPORT_VIEW_BUILDER,
            "builder_contract_version": REPORT_VIEW_BUILDER_VERSION,
            "classification": "derived_non_authoritative_presentation_data",
            "sample_classification": "production_source_projection",
            "sample_fixture_identity": None,
        },
        "authority": {
            "authoritative": False,
            "derived": True,
            "run_artifact": False,
            "measurement_artifact": False,
            "policy_input": False,
            "ratchet_input": False,
            "trusted_evidence": False,
            "admissible_for_other_documents": False,
            "statement": "Values are derived projections of cited source evidence; this document is never evidence for another document.",
        },
        "source_documents": sources,
        "presentation_vocabulary": [
            _status(code, vocabulary="report_view", axis=axis)
            for axis, codes in (
                ("run_lifecycle", ("completed", "completed_with_errors", "failed")),
                ("measurement", ("complete", "partial", "unavailable", "not_applicable", "absent", "missing")),
                ("policy", ("passed", "violated", "not_evaluable", "waived", "error", "not_evaluated")),
                ("admission", ("admitted", "not_supplied", "incompatible", "provenance_mismatch", "unreadable", "ambiguous", "refused")),
                ("trust", ("local_unprotected", "protected_required")),
            )
            for code in codes
        ],
        "run": {
            "run_id": run_id,
            "lifecycle": _status(view.integrity_status, vocabulary="run_status", axis="run_lifecycle"),
            "reader_lifecycle": str(getattr(view.lifecycle, "value", view.lifecycle)),
            "measurement_outcome": view.status.get("measurement_outcome"),
            "subject_count": len(subjects),
            "source_reference": _source_reference(by_source["run_status"], pointer="/"),
        },
        "subjects": subjects,
        "measurements": {
            "core_metrics": core,
            "language_metrics": language,
            "callable_population_status": _status(
                "complete" if "callables" in by_source else "absent",
                vocabulary="artifact_schema",
                axis="measurement",
            ),
            "callable_records": callables,
            "source_composition": sorted(composition, key=lambda row: (row["subject_key"], row["name"])),
            "structural_complexity": sorted(structural, key=lambda row: (row["subject_key"], row["name"])),
            "cognitive_complexity": sorted(cognitive, key=lambda row: (row["subject_key"], row["name"])),
            "frequency_distribution_status": _status("absent", vocabulary="artifact_schema", axis="measurement"),
            "frequency_distributions": [],
            "bounded_projections": [],
        },
        "findings": _findings(admitted["policy_result"], policy_admission, policy_source),
        "duplication": _duplication_section(admitted["duplication"], next(row["state"]["presentation_code"] for row in admission_records if row["kind"] == "duplication"), supplement_sources.get("duplication")),
        "hotspots": _hotspot_section(admitted["hotspots"], next(row["state"]["presentation_code"] for row in admission_records if row["kind"] == "hotspots"), supplement_sources.get("hotspots")),
        "changed_code": _changed_section(admitted["changed"], next(row["state"]["presentation_code"] for row in admission_records if row["kind"] == "changed"), supplement_sources.get("changed")),
        "diagnostics": _diagnostics(view, by_source),
        "trust_and_reproducibility": _trust(view, admitted["policy_result"], policy_admission, by_source),
        "supplement_admission": sorted(admission_records, key=lambda row: row["kind"]),
        "privacy_inventory": {},
        "limits": {
            "complete_contract_data": True,
            "truncation_applied": False,
            "strict_json_max_bytes": ARTIFACT_JSON_LIMITS.max_bytes,
            "source_bodies_included": False,
            "diff_bodies_included": False,
            "cross_subject_ranking": False,
            "quality_or_health_score": False,
        },
    }
    document["privacy_inventory"] = _privacy(document)
    problems = _structural_problems(document)
    if problems:
        raise ReportViewBuildError(
            "built Report View violates its schema: " + "; ".join(problems[:3])
        )
    return document


def _structural_problems(document: Mapping[str, Any]) -> list[str]:
    from validation.artifact_io.schema_store import validate_document

    return [str(problem) for problem in validate_document(
        "report_view", document, "archlens-report-view"
    )]


def canonical_report_view_bytes(document: Mapping[str, Any]) -> bytes:
    """Return compact strict UTF-8 JSON with sorted keys and one final LF."""

    problems = _structural_problems(document)
    if problems:
        raise ReportViewBuildError("invalid Report View: " + "; ".join(problems[:3]))
    return dumps_strict(
        dict(document),
        source="archlens-report-view",
        limits=ARTIFACT_JSON_LIMITS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        trailing_newline=True,
    ).encode("utf-8")


__all__ = [
    "REPORT_VIEW_BUILDER",
    "REPORT_VIEW_BUILDER_VERSION",
    "REPORT_VIEW_FORMAT",
    "REPORT_VIEW_FORMAT_VERSION",
    "ReportViewBuildError",
    "build_report_view",
    "canonical_report_view_bytes",
]
