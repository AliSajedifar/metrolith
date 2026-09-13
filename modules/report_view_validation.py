"""Independent semantic validation for Metrolith Report View 1.0.

This module never calls :func:`modules.report_view.build_report_view`.  It reads
the Report View and each supplied authority separately, then reconciles their
identities, values, populations, admission decisions, ordering, traceability,
and privacy invariants.  Consequently the builder and validator can disagree,
which is the point of an independent validation boundary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archlens_json import ARTIFACT_JSON_LIMITS, StrictJsonError, loads_bytes
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
from validation.artifact_io.schema_store import validate_document as validate_schema


_FORMAT = "archlens-report-view"
_VERSION = "1.0.0"
_CORE = ("source_files", "lines_of_code", "classes_structs", "methods_functions")
_STATUS_FIELD = {
    "source_files": "source_files_status",
    "lines_of_code": "loc_status",
    "classes_structs": "classes_structs_status",
    "methods_functions": "methods_functions_status",
}
_FORBIDDEN_DOMAIN_KEYS = frozenset({
    "page", "tab", "sidebar", "card", "widget", "chart", "dashboard",
    "navigation", "panel",
})
_FORBIDDEN_CONTENT_KEYS = frozenset({
    "source_body", "source_code", "diff_body", "environment_variables",
    "environment_dump", "username", "hostname", "credential", "credentials",
})
_WINDOWS_PATH = re.compile(r"(?:^|[\s'\"])[A-Za-z]:[\\/]")
_UNIX_PATH = re.compile(r"(?:^|[\s'\"])/(?:Users|home|root|tmp|var|opt|etc)/")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|client[_-]?secret|"
    r"user(?:name)?|host(?:name)?)\s*[:=]\s*[^\s,;]+"
)
_BIDI_CONTROL = re.compile("[\u202a-\u202e\u2066-\u2069]")


@dataclass(frozen=True, slots=True)
class ReportViewProblem:
    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.location}: {self.message}"


class ReportViewValidationError(ValueError):
    def __init__(self, problems: list[ReportViewProblem]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(str(problem) for problem in problems[:5]))


def _problem(problems, code, location, message):
    problems.append(ReportViewProblem(code, location, message))


def _canonical_source_bytes(document: Mapping[str, Any]) -> bytes:
    from archlens_json import dumps_strict

    return dumps_strict(
        dict(document), source="Report View validation source",
        limits=ARTIFACT_JSON_LIMITS, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), trailing_newline=True,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pointer_index(pointer: Any, prefix: str) -> int | None:
    if not isinstance(pointer, str) or not pointer.startswith(prefix):
        return None
    try:
        value = int(pointer[len(prefix):])
    except ValueError:
        return None
    return value if value >= 0 else None


def _source_map(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("role")): item
        for item in document.get("source_documents") or []
        if isinstance(item, Mapping)
    }


def _walk(value: Any, location: str = ""):
    yield location or "/", value
    if isinstance(value, Mapping):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _walk(child, f"{location}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{location}/{index}")


def _reference_problems(document, problems):
    sources = {
        (
            item.get("role"), item.get("format"), item.get("format_version"),
            item.get("sha256"), item.get("portable_path"),
        )
        for item in document.get("source_documents") or []
    }
    references = 0
    for location, value in _walk(document):
        if not isinstance(value, Mapping):
            continue
        if {
            "source_role", "format", "format_version", "sha256",
            "portable_path", "logical_pointer",
        }.issubset(value):
            references += 1
            identity = (
                value.get("source_role"), value.get("format"),
                value.get("format_version"), value.get("sha256"),
                value.get("portable_path"),
            )
            if identity not in sources:
                _problem(
                    problems, "source_reference_unknown", location,
                    "source reference does not identify a declared source document",
                )
            pointer = value.get("logical_pointer")
            if pointer is not None and not str(pointer).startswith("/"):
                _problem(
                    problems, "source_reference_pointer_invalid", location,
                    "logical pointer must start with '/'",
                )
    if references == 0:
        _problem(problems, "source_reference_missing", "/", "document has no source references")


def _privacy_problems(document, problems):
    for location, value in _walk(document):
        key = location.rsplit("/", 1)[-1]
        if key in _FORBIDDEN_DOMAIN_KEYS:
            _problem(problems, "ui_specific_key", location, f"forbidden UI-specific key {key!r}")
        if key in _FORBIDDEN_CONTENT_KEYS:
            _problem(problems, "private_content_key", location, f"forbidden content key {key!r}")
        if not isinstance(value, str):
            continue
        if key != "logical_pointer":
            if _WINDOWS_PATH.search(value):
                _problem(problems, "absolute_windows_path", location, "absolute Windows path is forbidden")
            if _UNIX_PATH.search(value):
                _problem(problems, "absolute_unix_path", location, "absolute Unix path is forbidden")
        if _SECRET.search(value):
            _problem(problems, "secret_like_text", location, "secret-like text is forbidden")
        if _BIDI_CONTROL.search(value) or any(
            ord(character) < 32 and character not in "\t\n\r" for character in value
        ):
            _problem(problems, "unsafe_control_text", location, "bidi or unsafe control text is forbidden")
    privacy = document.get("privacy_inventory") or {}
    forbidden_true = (
        "source_bodies_present", "diff_bodies_present",
        "absolute_windows_paths_present", "absolute_unix_paths_present",
        "usernames_present", "hostnames_present", "environment_dumps_present",
        "secrets_or_tokens_present", "credentials_present",
        "inventory_is_trust_claim",
    )
    for name in forbidden_true:
        if privacy.get(name) is not False:
            _problem(problems, "privacy_declaration_invalid", f"/privacy_inventory/{name}", "must be false")


def _ordering_problems(document, problems):
    sources = document.get("source_documents") or []
    if sources != sorted(sources, key=lambda row: (row.get("role"), row.get("portable_path"))):
        _problem(problems, "source_order", "/source_documents", "source documents are not deterministically ordered")
    subjects = document.get("subjects") or []
    if subjects != sorted(subjects, key=lambda row: str(row.get("subject_key", "")).casefold()):
        _problem(problems, "subject_order", "/subjects", "subjects are not deterministically ordered")
    core = (document.get("measurements") or {}).get("core_metrics") or []
    if core != sorted(core, key=lambda row: (row.get("subject_key"), row.get("metric"))):
        _problem(problems, "core_metric_order", "/measurements/core_metrics", "core metrics are not deterministically ordered")
    languages = (document.get("measurements") or {}).get("language_metrics") or []
    if languages != sorted(languages, key=lambda row: (row.get("subject_key"), row.get("language"))):
        _problem(problems, "language_order", "/measurements/language_metrics", "language rows are not deterministically ordered")
    callables = (document.get("measurements") or {}).get("callable_records") or []
    callable_key = lambda row: (
        str(row.get("subject_key") or ""), str(row.get("relative_path") or ""),
        int(row.get("start_line") or 0), str(row.get("row_id") or ""),
    )
    if callables != sorted(callables, key=callable_key):
        _problem(problems, "callable_order", "/measurements/callable_records", "callable rows are not deterministically ordered")
    admissions = document.get("supplement_admission") or []
    if admissions != sorted(admissions, key=lambda row: row.get("kind")):
        _problem(problems, "admission_order", "/supplement_admission", "admission records are not ordered by kind")


def _internal_semantics(document, problems):
    if document.get("format") != _FORMAT or document.get("format_version") != _VERSION:
        _problem(problems, "identity", "/", "Report View identity/version is invalid")
    identity = document.get("document_identity") or {}
    if identity.get("format") != _FORMAT or identity.get("format_version") != _VERSION:
        _problem(problems, "identity_mismatch", "/document_identity", "nested identity disagrees with root")
    if (document.get("authority") or {}).get("admissible_for_other_documents") is not False:
        _problem(problems, "authority", "/authority", "Report View must never be admissible evidence")
    if (document.get("run") or {}).get("subject_count") != len(document.get("subjects") or []):
        _problem(problems, "subject_count", "/run/subject_count", "subject count does not match subject population")
    subject_keys = [row.get("subject_key") for row in document.get("subjects") or []]
    if len(subject_keys) != len(set(subject_keys)):
        _problem(problems, "subject_duplicate", "/subjects", "subject keys must be unique")
    for collection in ("canonical_findings", "not_evaluable", "waived_findings"):
        for index, finding in enumerate((document.get("findings") or {}).get(collection) or []):
            key = finding.get("subject_key")
            if key is not None and key not in subject_keys:
                _problem(problems, "finding_subject", f"/findings/{collection}/{index}/subject_key", "finding subject is not in the Report View subject set")
    source_roles = [row.get("role") for row in document.get("source_documents") or []]
    if len(source_roles) != len(set(source_roles)):
        _problem(problems, "source_role_duplicate", "/source_documents", "source roles must be unique")
    callables = (document.get("measurements") or {}).get("callable_records") or []
    row_ids = [row.get("row_id") for row in callables]
    if len(row_ids) != len(set(row_ids)):
        _problem(problems, "callable_duplicate", "/measurements/callable_records", "callable row identities must be unique")
    for collection_name in ("core_metrics", "callable_records"):
        for index, row in enumerate((document.get("measurements") or {}).get(collection_name) or []):
            value = row.get("value") if collection_name == "core_metrics" else row.get("cyclomatic_complexity")
            status = row.get("status") or {}
            code = status.get("presentation_code")
            measured = status.get("measured_value")
            expected_measured = value is not None and code in {"complete", "partial"}
            if measured is not expected_measured:
                _problem(problems, "value_status_pair", f"/measurements/{collection_name}/{index}", "measured-value flag contradicts value/status")
            if value == 0 and code in {"unavailable", "absent", "missing"}:
                _problem(problems, "zero_status_pair", f"/measurements/{collection_name}/{index}", "zero cannot represent unavailable/absent/missing evidence")
    kinds = [row.get("kind") for row in document.get("supplement_admission") or []]
    if kinds != sorted(["changed", "duplication", "hotspots", "policy_result"]):
        _problem(problems, "admission_population", "/supplement_admission", "exactly four supplement kinds are required")
    for index, admission in enumerate(document.get("supplement_admission") or []):
        code = (admission.get("state") or {}).get("presentation_code")
        if admission.get("contributes_evidence") is not (code == "admitted"):
            _problem(problems, "admission_contribution", f"/supplement_admission/{index}", "only admitted supplements may contribute evidence")
        if admission.get("supplied") is False and code != "not_supplied":
            _problem(problems, "admission_supplied", f"/supplement_admission/{index}", "an unsupplied document must be not_supplied")
        if admission.get("supplied") is True and admission.get("source_reference") is None and code != "unreadable":
            _problem(problems, "admission_reference", f"/supplement_admission/{index}", "a readable supplied document requires a source reference")
    trust = document.get("trust_and_reproducibility") or {}
    trust_status = trust.get("trust_status") or {}
    if trust.get("gate_mode") != trust_status.get("presentation_code"):
        _problem(problems, "trust_status", "/trust_and_reproducibility/trust_status", "gate mode and trust status disagree")
    findings = document.get("findings") or {}
    verdict_code = {"pass": "passed", "fail": "violated", "error": "error"}.get(findings.get("verdict"))
    if verdict_code is not None and (findings.get("evaluation") or {}).get("presentation_code") != verdict_code:
        _problem(problems, "policy_verdict_internal", "/findings/evaluation", "Policy verdict and evaluation status disagree")
    exit_for_verdict = {"pass": 0, "fail": 1, "error": 2}
    if findings.get("verdict") in exit_for_verdict and findings.get("exit_code") != exit_for_verdict[findings.get("verdict")]:
        _problem(problems, "policy_exit_internal", "/findings/exit_code", "Policy verdict and exit code disagree")
    expected_collection_status = {
        "canonical_findings": {"violated"},
        "not_evaluable": {"not_evaluable", "error"},
        "waived_findings": {"waived"},
    }
    for collection, allowed in expected_collection_status.items():
        for index, finding in enumerate(findings.get(collection) or []):
            if (finding.get("status") or {}).get("presentation_code") not in allowed:
                _problem(problems, "policy_finding_status", f"/findings/{collection}/{index}/status", "finding status does not belong in this collection")
    if findings.get("source_reference") is not None and findings.get("trust_mode") != trust.get("gate_mode"):
        _problem(problems, "trust_mode", "/findings/trust_mode", "Check Result and trust projection modes disagree")
    ratchet = findings.get("ratchet")
    if isinstance(ratchet, Mapping):
        evaluated = ratchet.get("evaluated_count") or 0
        accounted = (ratchet.get("violated_count") or 0) + (ratchet.get("not_evaluable_count") or 0)
        if accounted > evaluated:
            _problem(problems, "ratchet_summary", "/findings/ratchet", "Ratchet violated/not-evaluable counts exceed evaluated count")
        if ratchet.get("configured") is False and any(
            ratchet.get(name) for name in ("evaluated_count", "violated_count", "not_evaluable_count", "paired_subject_count")
        ):
            _problem(problems, "ratchet_not_configured", "/findings/ratchet", "an unconfigured Ratchet cannot carry evaluated populations")
    _projection_consistency(document, problems)
    _ordering_problems(document, problems)
    _reference_problems(document, problems)
    _privacy_problems(document, problems)


def _projection_consistency(document, problems):
    measurements = document.get("measurements") or {}
    if measurements.get("frequency_distributions"):
        _problem(
            problems,
            "frequency_distribution_present",
            "/measurements/frequency_distributions",
            "Report View 1.0 does not admit exact-frequency distributions",
        )
    if measurements.get("bounded_projections"):
        _problem(
            problems,
            "bounded_projection_present",
            "/measurements/bounded_projections",
            "Report View 1.0 does not admit bounded projections",
        )

    duplication = document.get("duplication") or {}
    duplicate_counts = {row.get("name"): row.get("value") for row in duplication.get("counts") or []}
    groups = duplication.get("groups") or []
    derived_duplicate_counts = {
        "lexical.group_count": sum(row.get("kind") == "lexical" for row in groups),
        "lexical.occurrence_count": sum(len(row.get("occurrences") or []) for row in groups if row.get("kind") == "lexical"),
        "structural.retained_group_count": sum(row.get("kind") == "structural" for row in groups),
        "structural.occurrence_count": sum(len(row.get("occurrences") or []) for row in groups if row.get("kind") == "structural"),
    }
    for name, expected in derived_duplicate_counts.items():
        if name in duplicate_counts and duplicate_counts[name] != expected:
            _problem(problems, "duplication_count", "/duplication/counts", f"{name} does not reconcile with groups")
    for index, group in enumerate(groups):
        if group.get("occurrence_count") != len(group.get("occurrences") or []):
            _problem(problems, "duplication_occurrence_count", f"/duplication/groups/{index}", "occurrence count does not match occurrences")

    hotspots = document.get("hotspots") or {}
    hotspot_counts = {row.get("name"): row.get("value") for row in hotspots.get("counts") or []}
    hotspot_rows = hotspots.get("rows") or []
    if "row_count" in hotspot_counts and hotspot_counts["row_count"] != len(hotspot_rows):
        _problem(problems, "hotspot_count", "/hotspots/counts", "row count does not match hotspot rows")
    classes: dict[str, int] = {}
    for row in hotspot_rows:
        label = row.get("attention_class") or "unclassified"
        classes[str(label)] = classes.get(str(label), 0) + 1
    for name, value in hotspot_counts.items():
        if str(name).startswith("attention."):
            label = str(name).split(".", 1)[1]
            if value != classes.get(label, 0):
                _problem(problems, "hotspot_class_count", "/hotspots/counts", f"{name} does not reconcile with rows")

    changed = document.get("changed_code") or {}
    changed_counts = {row.get("name"): row.get("value") for row in changed.get("counts") or []}
    files = changed.get("files") or []
    expected_changed = {
        "git_changed_files": len(files),
        "added_diff_lines": sum((hunk.get("added_lines") or 0) for row in files for hunk in row.get("hunks") or []),
        "deleted_diff_lines": sum((hunk.get("deleted_lines") or 0) for row in files for hunk in row.get("hunks") or []),
    }
    for name, expected in expected_changed.items():
        if name in changed_counts and changed_counts[name] != expected:
            _problem(problems, "changed_count", "/changed_code/counts", f"{name} does not reconcile with files/hunks")

    admission = {
        row.get("kind"): (row.get("state") or {}).get("presentation_code")
        for row in document.get("supplement_admission") or []
    }
    populations = {
        "duplication": len(groups), "hotspots": len(hotspot_rows),
        "changed": len(files),
        "policy_result": len((document.get("findings") or {}).get("canonical_findings") or [])
        + len((document.get("findings") or {}).get("not_evaluable") or [])
        + len((document.get("findings") or {}).get("waived_findings") or []),
    }
    for kind, count in populations.items():
        if admission.get(kind) != "admitted" and count:
            _problem(problems, "refused_evidence_present", f"/{kind}", "non-admitted supplement contributes rows")


def _run_source_digest(role: str, source: Mapping[str, Any], view) -> str | None:
    if role.startswith("file_inventory:"):
        relative = source.get("portable_path")
        if not isinstance(relative, str) or relative not in view.reader.family("file_inventory"):
            return None
        return _sha256(view.reader.document_bytes(relative))
    relatives = {
        "run_manifest": "run_manifest.json", "run_status": "run_status.json",
        "environment": "environment.json", "analysis": "analysis.json",
        "sheet_metrics": "sheet_metrics.csv", "language_metrics": "language_metrics.csv",
        "callables": "callables.csv", "errors": "errors.csv",
        "recoveries": "recoveries.csv", "catalog": "catalog.csv",
    }
    relative = relatives.get(role)
    if relative is None:
        return None
    path = Path(view.run_directory) / relative
    if not path.is_file():
        return None
    payload = view.reader.document_bytes(relative) if relative.endswith(".json") else path.read_bytes()
    return _sha256(payload)


def _reconcile_run(document, view, problems):
    sources = _source_map(document)
    run = document.get("run") or {}
    expected_lifecycle = str(view.integrity_status)
    if (run.get("lifecycle") or {}).get("raw_source_code") != expected_lifecycle:
        _problem(problems, "run_lifecycle", "/run/lifecycle", "run lifecycle differs from the source run")
    if run.get("reader_lifecycle") != str(getattr(view.lifecycle, "value", view.lifecycle)):
        _problem(problems, "reader_lifecycle", "/run/reader_lifecycle", "reader lifecycle differs from the source reader")
    if run.get("run_id") != (str(view.run_id) if view.run_id is not None else None):
        _problem(problems, "run_id", "/run/run_id", "run identity differs from the source run")
    if run.get("measurement_outcome") != view.status.get("measurement_outcome"):
        _problem(problems, "measurement_outcome", "/run/measurement_outcome", "measurement outcome differs from run_status.json")
    for role, source in sources.items():
        if role in {"duplication", "hotspots", "changed", "policy_result"}:
            continue
        expected = _run_source_digest(role, source, view)
        if expected is None:
            _problem(problems, "source_unexpected", f"/source_documents/{role}", "source role is absent from the run")
        elif source.get("sha256") != expected:
            _problem(problems, "source_digest", f"/source_documents/{role}/sha256", "source digest does not match exact source bytes")
    expected_subjects = {}
    for result in view.repositories:
        acquisition = result.get("acquisition") or {}
        expected_subjects[str(result.get("subject_key"))] = acquisition.get("analyzed_commit_sha")
    actual_subjects = {row.get("subject_key"): row.get("analyzed_revision") for row in document.get("subjects") or []}
    if actual_subjects != expected_subjects:
        _problem(problems, "subject_or_revision", "/subjects", "subject set or analyzed revision differs from the run")
    expected_core = {}
    for result in view.repositories:
        subject = str(result.get("subject_key"))
        aggregate = (result.get("metrics") or {}).get("aggregate") or {}
        for metric in _CORE:
            expected_core[(subject, metric)] = (
                aggregate.get(metric), aggregate.get(_STATUS_FIELD[metric])
            )
    actual_core = {
        (row.get("subject_key"), row.get("metric")): (
            row.get("value"), (row.get("status") or {}).get("raw_source_code")
        )
        for row in (document.get("measurements") or {}).get("core_metrics") or []
    }
    if actual_core != expected_core:
        _problem(problems, "core_metric", "/measurements/core_metrics", "core values/statuses differ from analysis.json")
    expected_languages = {}
    for row in view.language_metrics:
        key = (str(row.get("subject_key")), str(row.get("language") or "").casefold())
        expected_languages[key] = tuple(row.get(metric) for metric in _CORE) + (row.get("metric_status"),)
    actual_languages = {}
    for row in (document.get("measurements") or {}).get("language_metrics") or []:
        by_metric = {item.get("metric"): item.get("value") for item in row.get("metrics") or []}
        key = (row.get("subject_key"), row.get("language"))
        actual_languages[key] = tuple(by_metric.get(metric) for metric in _CORE) + ((row.get("completeness") or {}).get("raw_source_code"),)
    if actual_languages != expected_languages:
        _problem(problems, "language_metric", "/measurements/language_metrics", "language population or values differ from language_metrics.csv")
    expected_callables = {}
    if view.has_callable_artifact:
        for row in view.stream_callables():
            expected_callables[row.get("callable_row_id")] = (
                str(row.get("subject_key")), str(row.get("detected_language") or "unknown").casefold(),
                row.get("relative_path"), row.get("qualified_name"), row.get("start_line"),
                row.get("end_line"), row.get("nloc"), row.get("cyclomatic_complexity"),
                row.get("cognitive_complexity"), row.get("max_nesting_depth"),
                row.get("formal_parameter_count"), row.get("structural_complexity_status"),
            )
    actual_callables = {
        row.get("row_id"): (
            row.get("subject_key"), row.get("language"), row.get("relative_path"),
            row.get("qualified_name"), row.get("start_line"), row.get("end_line"),
            row.get("nloc"), row.get("cyclomatic_complexity"), row.get("cognitive_complexity"),
            row.get("nesting_depth"), row.get("formal_parameter_count"), row.get("raw_status"),
        )
        for row in (document.get("measurements") or {}).get("callable_records") or []
    }
    if actual_callables != expected_callables:
        _problem(problems, "callable_population", "/measurements/callable_records", "callable identities/counts/values differ from the callable ledger")
    manifest_digest = _run_source_digest(
        "run_manifest", sources.get("run_manifest") or {}, view
    )
    if (document.get("trust_and_reproducibility") or {}).get("run_manifest_digest") != manifest_digest:
        _problem(problems, "trust_manifest_digest", "/trust_and_reproducibility/run_manifest_digest", "trust record does not bind the source run manifest")
    expected_revisions = sorted({
        str((result.get("acquisition") or {}).get("analyzed_commit_sha"))
        for result in view.repositories
        if (result.get("acquisition") or {}).get("analyzed_commit_sha")
    })
    if (document.get("trust_and_reproducibility") or {}).get("analyzed_revisions") != expected_revisions:
        _problem(problems, "trust_revisions", "/trust_and_reproducibility/analyzed_revisions", "trust record revisions differ from source subjects")
    _reconcile_diagnostics(document, view, sources, problems)


def _reconcile_diagnostics(document, view, sources, problems):
    for index, diagnostic in enumerate(document.get("diagnostics") or []):
        reference = diagnostic.get("source_reference") or {}
        role = reference.get("source_role")
        pointer = reference.get("logical_pointer")
        source_row = None
        row_index = _pointer_index(pointer, "/rows/")
        if role == "errors" and row_index is not None:
            if row_index < len(view.errors):
                source_row = view.errors[row_index]
                expected_kind = str(source_row.get("error_category") or source_row.get("error_type") or "error")
                expected_path = source_row.get("file_path")
            else:
                expected_kind = expected_path = None
        elif role == "recoveries" and row_index is not None:
            if row_index < len(view.recoveries):
                source_row = view.recoveries[row_index]
                expected_kind = str(source_row.get("error_category") or "recovery")
                expected_path = source_row.get("file_path")
            else:
                expected_kind = expected_path = None
        elif isinstance(role, str) and role.startswith("file_inventory:"):
            source = sources.get(role) or {}
            inventory = view.inventories.get(source.get("portable_path"))
            row_index = _pointer_index(pointer, "/files/")
            rows = (inventory or {}).get("files") or []
            if row_index is not None and row_index < len(rows):
                source_row = rows[row_index]
                expected_path = source_row.get("relative_path")
                if source_row.get("exclusion_reason"):
                    expected_kind = "excluded_file"
                elif source_row.get("oversized"):
                    expected_kind = "oversized_file"
                elif source_row.get("read_status") not in {None, "complete"}:
                    expected_kind = "source_read_failure"
                elif source_row.get("encoding_error"):
                    expected_kind = "source_encoding_failure"
                elif source_row.get("parse_status") in {"partial", "recovered"}:
                    expected_kind = "parser_partial"
                else:
                    expected_kind = "parser_failure"
            else:
                expected_kind = expected_path = None
        else:
            continue
        if source_row is None:
            _problem(problems, "diagnostic_reference", f"/diagnostics/{index}/source_reference", "diagnostic pointer does not resolve to a source row")
        elif diagnostic.get("kind") != expected_kind or diagnostic.get("relative_path") != expected_path:
            _problem(problems, "diagnostic_value", f"/diagnostics/{index}", "diagnostic kind/path differs from its source row")


def _expected_admissions(view, supplied, read_errors):
    scopes = analyzed_scopes(list(view.repositories))
    run_id = str(view.run_id) if view.run_id is not None else None
    rows = {}
    for kind, document, expected, check in (
        ("duplication", supplied.get("duplication"), DUPLICATION_FORMAT, lambda item: duplication_provenance(item, scopes)),
        ("hotspots", supplied.get("hotspots"), HOTSPOT_FORMAT, lambda item: hotspot_provenance(item, run_id)),
        ("changed", supplied.get("changed"), CHANGED_CODE_FORMAT, lambda item: changed_provenance(item, scopes)),
        ("policy_result", supplied.get("policy_result"), CHECK_RESULT_FORMAT, lambda item: check_provenance(item, run_id)),
    ):
        record = admit_supplement(
            document, kind=kind, expected_format=expected,
            provenance_check=check, read_error=read_errors.get(kind),
        )
        rows[kind] = report_view_admission_state(record)
    return rows


def _reconcile_supplements(document, view, supplied, read_errors, problems):
    expected_states = _expected_admissions(view, supplied, read_errors)
    actual_states = {
        row.get("kind"): (row.get("state") or {}).get("presentation_code")
        for row in document.get("supplement_admission") or []
    }
    if actual_states != expected_states:
        _problem(problems, "supplement_admission", "/supplement_admission", "admission states differ from independent admission")
    sources = _source_map(document)
    for kind, source_document in supplied.items():
        if source_document is None:
            continue
        source = sources.get(kind)
        if source is None:
            _problem(problems, "supplement_source_missing", "/source_documents", f"supplied {kind} has no source identity")
            continue
        expected_digest = _sha256(_canonical_source_bytes(source_document))
        if source.get("sha256") != expected_digest:
            _problem(problems, "supplement_digest", f"/source_documents/{kind}/sha256", f"{kind} digest differs from supplied bytes")
    policy = supplied.get("policy_result")
    if expected_states.get("policy_result") == "admitted" and policy is not None:
        findings = document.get("findings") or {}
        if findings.get("verdict") != policy.get("verdict") or findings.get("exit_code") != policy.get("exit_code"):
            _problem(problems, "policy_verdict", "/findings", "Policy verdict/exit semantics differ from Check Result")
        for source_name, view_name in (("findings", "canonical_findings"), ("not_evaluable", "not_evaluable"), ("waived_findings", "waived_findings")):
            expected_findings = {
                row.get("finding_id"): (
                    row.get("rule_id"), row.get("metric"), row.get("scope"),
                    row.get("subject_key"), row.get("language"), row.get("status"),
                    row.get("severity"), row.get("observed_value"), row.get("operator"),
                    row.get("threshold"), row.get("reason"), row.get("path"),
                    row.get("start_line"), row.get("end_line"),
                    tuple(sorted(str(key) for key in (row.get("evidence") or {}))),
                )
                for row in policy.get(source_name) or []
            }
            actual_findings = {
                row.get("finding_id"): (
                    row.get("rule_id"), row.get("metric"), row.get("scope"),
                    row.get("subject_key"), row.get("language"),
                    (row.get("status") or {}).get("raw_source_code"), row.get("severity"),
                    row.get("observed_value"), row.get("operator"), row.get("threshold"),
                    row.get("reason"), (row.get("location") or {}).get("relative_path"),
                    (row.get("location") or {}).get("start_line"),
                    (row.get("location") or {}).get("end_line"),
                    tuple(row.get("evidence_keys") or []),
                )
                for row in findings.get(view_name) or []
            }
            if actual_findings != expected_findings:
                _problem(problems, "policy_finding", f"/findings/{view_name}", "finding population or values differ from Check Result")
        ratchet = policy.get("ratchet") or {}
        actual_ratchet = findings.get("ratchet") or {}
        for name in ("configured", "status", "admission_status", "evaluated_count", "violated_count", "not_evaluable_count", "paired_subject_count"):
            if actual_ratchet.get(name) != ratchet.get(name):
                _problem(problems, "ratchet_summary", f"/findings/ratchet/{name}", "Ratchet summary differs from Check Result")
                break
        baseline = ratchet.get("baseline") or {}
        expected_baseline = (
            baseline.get("format"), baseline.get("format_version"),
            baseline.get("verified_sha256"), baseline.get("source_run_id"),
        )
        actual_baseline = (
            actual_ratchet.get("baseline_format"), actual_ratchet.get("baseline_format_version"),
            actual_ratchet.get("baseline_digest"), actual_ratchet.get("baseline_source_run_id"),
        )
        if actual_baseline != expected_baseline:
            _problem(problems, "ratchet_baseline", "/findings/ratchet", "Ratchet baseline identity differs from Check Result")
        provenance = policy.get("evaluated_input_provenance") or {}
        expected_mode = str(provenance.get("mode") or "local_unprotected")
        trust = document.get("trust_and_reproducibility") or {}
        if findings.get("trust_mode") != provenance.get("mode") or trust.get("gate_mode") != expected_mode:
            _problem(problems, "trust_mode", "/trust_and_reproducibility/gate_mode", "trust mode differs from Check Result provenance")
    duplication = supplied.get("duplication")
    if expected_states.get("duplication") == "admitted" and duplication is not None:
        section = document.get("duplication") or {}
        expected_groups = {
            (kind, row.get("group_id")): (
                row.get("fingerprint"), row.get("language"), row.get("occurrence_count"),
                row.get("file_count"), tuple(
                    (
                        occurrence.get("occurrence_id"), occurrence.get("path"),
                        occurrence.get("start_line"), occurrence.get("end_line"),
                        occurrence.get("duplicated_nloc"),
                    )
                    for occurrence in row.get("occurrences") or []
                ),
            )
            for kind, key in (("lexical", "lexical_groups"), ("structural", "structural_groups"))
            for row in duplication.get(key) or []
        }
        actual_groups = {
            (row.get("kind"), row.get("group_id")): (
                row.get("fingerprint"), row.get("language"), row.get("occurrence_count"),
                row.get("file_count"), tuple(
                    (
                        occurrence.get("occurrence_id"), occurrence.get("relative_path"),
                        occurrence.get("start_line"), occurrence.get("end_line"),
                        occurrence.get("duplicated_nloc"),
                    )
                    for occurrence in row.get("occurrences") or []
                ),
            )
            for row in section.get("groups") or []
        }
        if actual_groups != expected_groups:
            _problem(problems, "duplication_groups", "/duplication/groups", "duplication groups/counts differ from source")
    hotspots = supplied.get("hotspots")
    if expected_states.get("hotspots") == "admitted" and hotspots is not None:
        expected_rows = {
            (row.get("subject_key"), row.get("path")): (
                row.get("language"), (row.get("complexity") or {}).get("status"),
                (row.get("complexity") or {}).get("cognitive_complexity_total"),
                (row.get("complexity_signal") or {}).get("signal"),
                (row.get("churn") or {}).get("status"), (row.get("churn") or {}).get("commits"),
                (row.get("churn_signal") or {}).get("signal"), row.get("classification"),
                tuple(row.get("reasons") or []),
            )
            for row in hotspots.get("hotspots") or []
        }
        actual_rows = {
            (row.get("subject_key"), row.get("relative_path")): (
                row.get("language"), row.get("complexity_status"), row.get("complexity_value"),
                row.get("complexity_signal"), row.get("churn_status"), row.get("churn_value"),
                row.get("churn_signal"), row.get("attention_class"), tuple(row.get("reasons") or []),
            )
            for row in (document.get("hotspots") or {}).get("rows") or []
        }
        if actual_rows != expected_rows:
            _problem(problems, "hotspot_rows", "/hotspots/rows", "hotspot population/classes differ from source")
    changed = supplied.get("changed")
    if expected_states.get("changed") == "admitted" and changed is not None:
        expected_files = {
            row.get("file_change_id"): (
                row.get("change_kind"), row.get("base_path"), row.get("head_path"),
                row.get("scope"), tuple(
                    (
                        hunk.get("ordinal"), (hunk.get("base") or {}).get("start_line"),
                        (hunk.get("base") or {}).get("line_count"),
                        (hunk.get("head") or {}).get("start_line"),
                        (hunk.get("head") or {}).get("line_count"),
                        hunk.get("added_diff_lines"), hunk.get("deleted_diff_lines"),
                    )
                    for hunk in (row.get("hunks") or {}).get("items") or []
                ),
            )
            for row in changed.get("file_changes") or []
        }
        actual_files = {
            row.get("file_change_id"): (
                row.get("change_classification"), row.get("base_path"), row.get("head_path"),
                row.get("scope"), tuple(
                    (
                        hunk.get("ordinal"), hunk.get("base_start_line"),
                        hunk.get("base_line_count"), hunk.get("head_start_line"),
                        hunk.get("head_line_count"), hunk.get("added_lines"),
                        hunk.get("deleted_lines"),
                    )
                    for hunk in row.get("hunks") or []
                ),
            )
            for row in (document.get("changed_code") or {}).get("files") or []
        }
        if actual_files != expected_files:
            _problem(problems, "changed_files", "/changed_code/files", "changed file/hunk population differs from source")


def report_view_validation_problems(
    document: Mapping[str, Any],
    *,
    source_run=None,
    duplication: Mapping[str, Any] | None = None,
    hotspots: Mapping[str, Any] | None = None,
    changed: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    read_errors: Mapping[str, str] | None = None,
) -> list[ReportViewProblem]:
    problems: list[ReportViewProblem] = []
    for violation in validate_schema("report_view", document, "archlens-report-view"):
        _problem(problems, "schema", violation.location or "/", violation.message)
    if problems:
        return problems
    _internal_semantics(document, problems)
    if source_run is not None:
        _reconcile_run(document, source_run, problems)
        _reconcile_supplements(
            document, source_run,
            {"duplication": duplication, "hotspots": hotspots, "changed": changed, "policy_result": policy_result},
            dict(read_errors or {}), problems,
        )
    return sorted(problems, key=lambda item: (item.location, item.code, item.message))


def validate_report_view_document(
    document: Mapping[str, Any],
    **sources: Any,
) -> None:
    """Raise with every structural/semantic disagreement; return on success."""

    problems = report_view_validation_problems(document, **sources)
    if problems:
        raise ReportViewValidationError(problems)


def validate_report_view_bytes(payload: bytes, **sources: Any) -> dict[str, Any]:
    """Apply strict UTF-8/JSON admission before independent semantic validation."""

    try:
        document = loads_bytes(
            payload, source="archlens-report-view",
            limits=ARTIFACT_JSON_LIMITS, expect=dict,
        )
    except StrictJsonError as error:
        raise ReportViewValidationError([
            ReportViewProblem(error.code, error.location or "/", error.message)
        ]) from error
    validate_report_view_document(document, **sources)
    return document


__all__ = [
    "ReportViewProblem", "ReportViewValidationError",
    "report_view_validation_problems", "validate_report_view_bytes",
    "validate_report_view_document",
]
