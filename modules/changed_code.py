"""Changed-Code Analysis v1 derived document assembly and rendering.

Git is authoritative for file/range changes.  Canonical Metrolith run artifacts
are authoritative for every measurement.  This module joins those sources; it
does not parse source, calculate a source metric, or match callables across
revisions.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from modules import complexity_view
from modules.callable_ledger import AGGREGATE_FIELDS, aggregate_rows
from modules.config import PROGRAM_VERSION
from modules.git_change_extractor import GitChangeSet, GitFileChange, GitHunk
from modules.revision_source import AnalyzedRevision, AnalyzedRevisionPair
from modules.standalone_contracts import (
    CHANGED_CODE_FORMAT as FORMAT,
    CHANGED_CODE_FORMAT_VERSION as FORMAT_VERSION,
)


IDENTITY_VERSION = "changed-code-file-change-id-1"
STATUS_VALUES = frozenset({"complete", "partial", "unavailable", "failed"})
CORE_METRICS = (
    ("source_files", "source_files_contribution", "inclusion_state"),
    ("lines_of_code", "lines_of_code", "loc_status"),
    ("classes_structs", "classes_structs", "classes_structs_status"),
    ("methods_functions", "methods_functions", "methods_functions_status"),
)
CALLABLE_VALUE_FIELDS = (
    "nloc",
    "formal_parameter_count",
    "cyclomatic_complexity",
    "decision_point_count",
    "boolean_operator_count",
    "max_condition_operator_count",
    "max_nesting_depth",
    "cognitive_complexity",
)
_EVALUABLE = frozenset({"complete", "partial", "measured"})


class ChangedCodeInvariantError(RuntimeError):
    """The derived document contradicts its Git or measurement evidence."""


@dataclass(slots=True)
class SideEvidence:
    side: AnalyzedRevision
    inventory: dict[str, Mapping[str, Any]] | None
    contributions: dict[str, Mapping[str, Any]] | None
    callables: dict[str, list[Mapping[str, Any]]] | None


def _subject_rows(side: AnalyzedRevision, rows: Iterable[Mapping[str, Any]]):
    from modules.subject import subject_key_of

    for row in rows:
        if subject_key_of(dict(row)) == side.subject_key:
            yield row


def _inventory_by_path(side: AnalyzedRevision) -> dict[str, Mapping[str, Any]] | None:
    repository = side.repository
    slug = f"{repository.get('repository_owner')}__{repository.get('repository_name')}"
    document = side.view.inventories.get(f"file_inventory/{slug}.json")
    if document is None:
        return None
    return {
        str(record["relative_path"]): record
        for record in document.get("files", ())
        if record.get("relative_path")
    }


def _contributions_by_path(
    side: AnalyzedRevision,
) -> dict[str, Mapping[str, Any]] | None:
    if not side.view.has_contribution_ledger:
        return None
    return {
        str(row["relative_path"]): row
        for row in _subject_rows(side, side.view.stream_contributions())
        if row.get("relative_path")
    }


def _callables_by_path(
    side: AnalyzedRevision,
) -> dict[str, list[Mapping[str, Any]]] | None:
    if not side.view.has_callable_artifact:
        return None
    found: dict[str, list[Mapping[str, Any]]] = {}
    for row in _subject_rows(side, side.view.stream_callables()):
        path = row.get("relative_path")
        if path:
            found.setdefault(str(path), []).append(row)
    for rows in found.values():
        rows.sort(key=lambda row: (
            _integer(row.get("start_line")) or 0,
            _integer(row.get("end_line")) or 0,
            str(row.get("qualified_name") or ""),
            str(row.get("callable_row_id") or ""),
        ))
    return found


def _load_side(side: AnalyzedRevision) -> SideEvidence:
    return SideEvidence(
        side=side,
        inventory=_inventory_by_path(side),
        contributions=_contributions_by_path(side),
        callables=_callables_by_path(side),
    )


def _integer(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value) if "." in str(value) else int(value)
    except (TypeError, ValueError):
        return None


def _truth(value: Any) -> bool:
    return value is True or str(value).casefold() == "true"


def _manifest_contract(side: AnalyzedRevision, name: str) -> str | None:
    value = side.view.manifest.get(name)
    return str(value) if value is not None else None


def _scope_status(bundle: SideEvidence, path: str | None) -> dict[str, Any]:
    if path is None:
        return {
            "presence": "absent",
            "scope_inclusion": "not_applicable",
            "language": None,
            "scope_reason": "side_absent",
        }
    inventory = (bundle.inventory or {}).get(path)
    contribution = (bundle.contributions or {}).get(path)
    included = bool(contribution) or bool(
        inventory and _truth(inventory.get("included_in_metrics"))
    )
    return {
        "presence": "present",
        "scope_inclusion": "included" if included else "not_included",
        "language": (
            (contribution or {}).get("detected_language")
            or (inventory or {}).get("detected_language")
        ),
        "scope_reason": (
            None if included
            else (inventory or {}).get("exclusion_reason")
            or "not_in_canonical_metric_scope"
        ),
    }


def _metric_status(row: Mapping[str, Any], field: str) -> str:
    if field == "inclusion_state":
        return "complete" if row.get("inclusion_state") == "included_in_metrics" else "unavailable"
    value = row.get(field)
    return str(value) if value else "unavailable"


def _core_snapshot(
    bundle: SideEvidence,
    path: str | None,
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    contract = _manifest_contract(bundle.side, "metric_contract_version")
    if path is None or scope["scope_inclusion"] != "included":
        status = "not_applicable"
        reason = "side_absent" if path is None else "file_not_in_metric_scope"
        return [
            {
                "metric": metric,
                "value": None,
                "status": status,
                "contract_version": contract,
                "unavailable_reason": reason,
            }
            for metric, _value_field, _status_field in CORE_METRICS
        ]
    row = (bundle.contributions or {}).get(path)
    if row is None:
        return [
            {
                "metric": metric,
                "value": None,
                "status": "unavailable",
                "contract_version": contract,
                "unavailable_reason": "contribution_evidence_missing",
            }
            for metric, _value_field, _status_field in CORE_METRICS
        ]
    return [
        {
            "metric": metric,
            "value": _number(row.get(value_field)),
            "status": _metric_status(row, status_field),
            "contract_version": contract,
            "unavailable_reason": (
                None if _metric_status(row, status_field) in _EVALUABLE
                else str(row.get("error_reference") or "measurement_unavailable")
            ),
        }
        for metric, value_field, status_field in CORE_METRICS
    ]


def _complexity_snapshot(
    bundle: SideEvidence,
    path: str | None,
    scope: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contract = _manifest_contract(bundle.side, "complexity_contract_version")
    if path is None or scope["scope_inclusion"] != "included":
        reason = "side_absent" if path is None else "file_not_in_metric_scope"
        structural = [
            {
                "metric": field,
                "value": None,
                "status": "not_applicable",
                "contract_version": contract,
                "unavailable_reason": reason,
            }
            for field in AGGREGATE_FIELDS
        ]
        cognitive = [
            {
                "metric": field,
                "value": None,
                "status": "not_applicable",
                "contract_version": contract,
                "unavailable_reason": reason,
            }
            for field in complexity_view.COGNITIVE_AGGREGATE_FIELDS
        ]
        return structural, cognitive

    contribution = (bundle.contributions or {}).get(path)
    if contribution is None or bundle.callables is None:
        reason = (
            "contribution_evidence_missing" if contribution is None
            else "callable_evidence_missing"
        )
        structural_status = cognitive_status = "unavailable"
        structural_values = {field: None for field in AGGREGATE_FIELDS}
        cognitive_values = {
            field: None for field in complexity_view.COGNITIVE_AGGREGATE_FIELDS
        }
    else:
        rows = bundle.callables.get(path, [])
        structural_values = aggregate_rows(rows)
        cognitive_values = complexity_view.cognitive_aggregate(rows)
        structural_status = str(
            contribution.get("structural_complexity_status") or "unavailable"
        )
        cognitive_status = (
            "measured" if structural_status == "complete"
            else structural_status
        )
        reason = None if structural_status in _EVALUABLE else "measurement_unavailable"

    structural = [
        {
            "metric": field,
            "value": _number(structural_values.get(field)),
            "status": structural_status,
            "contract_version": contract,
            "unavailable_reason": reason,
        }
        for field in AGGREGATE_FIELDS
    ]
    cognitive = [
        {
            "metric": field,
            "value": _number(cognitive_values.get(field)),
            "status": cognitive_status,
            "contract_version": contract,
            "unavailable_reason": reason,
        }
        for field in complexity_view.COGNITIVE_AGGREGATE_FIELDS
    ]
    return structural, cognitive


def _measurement_availability(groups: Sequence[Sequence[Mapping[str, Any]]]) -> str:
    statuses = {
        str(item.get("status"))
        for group in groups
        for item in group
    }
    if statuses <= {"not_applicable"}:
        return "not_applicable"
    if "unavailable" in statuses or "failed" in statuses:
        return "unavailable"
    if "partial" in statuses:
        return "partial"
    return "complete"


def _side_file_evidence(
    bundle: SideEvidence,
    path: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scope = _scope_status(bundle, path)
    core = _core_snapshot(bundle, path, scope)
    structural, cognitive = _complexity_snapshot(bundle, path, scope)
    evidence = {
        "core_metrics": core,
        "structural_complexity": structural,
        "cognitive_complexity": cognitive,
        "measurement_availability": _measurement_availability(
            (core, structural, cognitive)
        ),
    }
    return scope, evidence


def _observations_by_metric(
    values: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {str(item["metric"]): item for item in values}


def _metric_deltas(
    base: Mapping[str, Any],
    head: Mapping[str, Any],
    *,
    paired_file: bool,
) -> list[dict[str, Any]]:
    groups = (
        "core_metrics", "structural_complexity", "cognitive_complexity"
    )
    found: list[dict[str, Any]] = []
    for group in groups:
        left = _observations_by_metric(base[group])
        right = _observations_by_metric(head[group])
        for metric in sorted(set(left) | set(right)):
            before = left[metric]
            after = right[metric]
            value_before = _number(before.get("value"))
            value_after = _number(after.get("value"))
            contracts_equal = (
                before.get("contract_version") is not None
                and before.get("contract_version") == after.get("contract_version")
            )
            evaluable = (
                paired_file
                and before.get("status") in _EVALUABLE
                and after.get("status") in _EVALUABLE
                and value_before is not None
                and value_after is not None
                and contracts_equal
            )
            if evaluable:
                delta: int | float | None = value_after - value_before
                reason = None
            else:
                delta = None
                if not paired_file:
                    reason = "side_absent"
                elif not contracts_equal:
                    reason = "contract_incomparable"
                else:
                    reason = "measurement_unavailable"
            found.append({
                "metric": metric,
                "family": group,
                "contract_version": (
                    before.get("contract_version") if contracts_equal else None
                ),
                "base": {
                    "value": value_before,
                    "status": before.get("status"),
                },
                "head": {
                    "value": value_after,
                    "status": after.get("status"),
                },
                "delta": delta,
                "not_evaluable_reason": reason,
            })
    return found


def _range_document(hunk: GitHunk) -> dict[str, Any]:
    return {
        "ordinal": hunk.ordinal,
        "base": {
            "start_line": hunk.base.start_line,
            "line_count": hunk.base.line_count,
        },
        "head": {
            "start_line": hunk.head.start_line,
            "line_count": hunk.head.line_count,
        },
        "deleted_diff_lines": hunk.deleted_diff_lines,
        "added_diff_lines": hunk.added_diff_lines,
    }


def _overlap(
    callable_start: int,
    callable_end: int,
    start: int,
    count: int,
) -> str | None:
    if count == 0:
        return "zero_length_anchor" if callable_start <= start <= callable_end else None
    end = start + count - 1
    return "interval_intersection" if callable_start <= end and start <= callable_end else None


def _affected_callables(
    bundle: SideEvidence,
    path: str | None,
    item: GitFileChange,
    side_name: str,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    if path is None or scope["scope_inclusion"] != "included":
        return {
            "availability": "not_applicable",
            "unavailable_reason": "side_absent" if path is None else "file_not_in_metric_scope",
            "observations": [],
        }
    if item.hunk_status != "complete":
        return {
            "availability": "unavailable",
            "unavailable_reason": item.hunk_unavailable_reason,
            "observations": [],
        }
    if not item.hunks:
        return {
            "availability": "complete",
            "unavailable_reason": None,
            "observations": [],
        }
    if bundle.callables is None:
        return {
            "availability": "unavailable",
            "unavailable_reason": "callable_evidence_missing",
            "observations": [],
        }

    observations: list[dict[str, Any]] = []
    unmappable = False
    for row in bundle.callables.get(path, []):
        if not _truth(row.get("location_maps_to_original_source")):
            unmappable = True
            continue
        start = _integer(row.get("start_line"))
        end = _integer(row.get("end_line"))
        if start is None or end is None:
            unmappable = True
            continue
        overlaps: list[dict[str, Any]] = []
        for hunk in item.hunks:
            location = hunk.base if side_name == "base" else hunk.head
            mode = _overlap(start, end, location.start_line, location.line_count)
            if mode is not None:
                overlaps.append({"hunk_ordinal": hunk.ordinal, "mode": mode})
        if not overlaps:
            continue
        observations.append({
            "side": side_name,
            "callable_row_id": row.get("callable_row_id"),
            "path": path,
            "language": row.get("detected_language"),
            "callable_kind": row.get("callable_kind"),
            "name": row.get("name"),
            "qualified_name": row.get("qualified_name"),
            "signature_discriminator": row.get("signature_discriminator"),
            "start_line": start,
            "end_line": end,
            "body_start_line": _integer(row.get("body_start_line")),
            "body_end_line": _integer(row.get("body_end_line")),
            "overlaps": overlaps,
            "structural_complexity_status": row.get("structural_complexity_status"),
            "nloc_status": row.get("nloc_status"),
            "metrics": {
                field: _number(row.get(field)) for field in CALLABLE_VALUE_FIELDS
            },
        })
    observations.sort(key=lambda row: (
        row["start_line"], row["end_line"],
        str(row.get("qualified_name") or ""),
        str(row.get("callable_row_id") or ""),
    ))
    return {
        "availability": "partial" if unmappable else "complete",
        "unavailable_reason": (
            "one_or_more_callable_locations_unavailable" if unmappable else None
        ),
        "observations": observations,
    }


def _file_change_id(
    subject_key: str,
    analyzed: AnalyzedRevisionPair,
    item: GitFileChange,
) -> str:
    parts = (
        IDENTITY_VERSION,
        subject_key,
        analyzed.resolved.base.sha,
        analyzed.resolved.head.sha,
        item.change_kind,
        item.base_path or "",
        item.head_path or "",
    )
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"fcc1_{digest[:24]}"


def _side_document(side: AnalyzedRevision) -> dict[str, Any]:
    acquisition = side.repository.get("acquisition") or {}
    return {
        "requested_revision": side.revision.requested,
        "resolved_commit_sha": side.revision.sha,
        "analyzed_commit_sha": acquisition.get("analyzed_commit_sha"),
        "source_mode": side.repository.get("source_mode"),
        "analysis_scope_hash": side.repository.get("analysis_scope_hash"),
        "analysis_status": side.repository.get("analysis_status"),
        "metric_contract_version": _manifest_contract(side, "metric_contract_version"),
        "complexity_contract_version": _manifest_contract(side, "complexity_contract_version"),
        "exclusion_policy_version": _manifest_contract(side, "exclusion_policy_version"),
    }


def _repository_metric_evidence(
    analyzed: AnalyzedRevisionPair,
) -> dict[str, Any]:
    def snapshot(side: AnalyzedRevision) -> list[dict[str, Any]]:
        aggregate = (side.repository.get("metrics") or {}).get("aggregate") or {}
        contract = _manifest_contract(side, "metric_contract_version")
        repository_status_fields = {
            "source_files": "source_files_status",
            "lines_of_code": "loc_status",
            "classes_structs": "classes_structs_status",
            "methods_functions": "methods_functions_status",
        }
        return [
            {
                "metric": metric,
                "value": _number(aggregate.get(metric)),
                "status": str(
                    aggregate.get(repository_status_fields[metric]) or "unavailable"
                ),
                "contract_version": contract,
                "unavailable_reason": None,
            }
            for metric, _file_field, _status_field in CORE_METRICS
        ]

    base = {"core_metrics": snapshot(analyzed.base)}
    head = {"core_metrics": snapshot(analyzed.head)}
    left = _observations_by_metric(base["core_metrics"])
    right = _observations_by_metric(head["core_metrics"])
    deltas = []
    for metric in sorted(left):
        before, after = left[metric], right[metric]
        comparable = (
            before["contract_version"] is not None
            and before["contract_version"] == after["contract_version"]
            and before["status"] in _EVALUABLE
            and after["status"] in _EVALUABLE
            and before["value"] is not None
            and after["value"] is not None
        )
        deltas.append({
            "metric": metric,
            "contract_version": before["contract_version"] if comparable else None,
            "base": {"value": before["value"], "status": before["status"]},
            "head": {"value": after["value"], "status": after["status"]},
            "delta": after["value"] - before["value"] if comparable else None,
            "not_evaluable_reason": None if comparable else "measurement_or_contract_unavailable",
        })
    return {"base": base, "head": head, "deltas": deltas}


def _overall_status(
    analyzed: AnalyzedRevisionPair,
    units: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    reasons: set[str] = set()
    for side in (analyzed.base, analyzed.head):
        if side.repository.get("analysis_status") == "failed":
            reasons.add(f"{side.revision.label}_analysis_failed")
        elif side.repository.get("analysis_status") == "partial":
            reasons.add(f"{side.revision.label}_analysis_partial")
    for unit in units:
        if unit["hunks"]["status"] != "complete":
            reasons.add(str(unit["hunks"]["unavailable_reason"]))
        if unit["scope"] != "changed_code":
            continue
        evidence = unit.get("evidence") or {}
        for side_name in ("base", "head"):
            availability = (evidence.get(side_name) or {}).get("measurement_availability")
            if availability in {"partial", "unavailable"}:
                reasons.add(f"{side_name}_file_measurement_{availability}")
            callable_availability = unit["affected_callables"][side_name]["availability"]
            if callable_availability in {"partial", "unavailable"}:
                reasons.add(f"{side_name}_callable_overlap_{callable_availability}")
    if any(reason.endswith("analysis_failed") for reason in reasons):
        return "failed", sorted(reasons)
    return ("partial" if reasons else "complete"), sorted(reasons)


def build_document(
    changes: GitChangeSet,
    analyzed: AnalyzedRevisionPair,
) -> dict[str, Any]:
    """Join complete Git evidence to two canonical Metrolith analyses."""
    base_bundle, head_bundle = _load_side(analyzed.base), _load_side(analyzed.head)
    subject_key = analyzed.base.subject_key
    units: list[dict[str, Any]] = []
    kind_counts: Counter[str] = Counter()
    changed_code_count = 0
    added_lines = deleted_lines = 0

    for item in changes.file_changes:
        kind_counts[item.change_kind] += 1
        base_scope, base_evidence = _side_file_evidence(base_bundle, item.base_path)
        head_scope, head_evidence = _side_file_evidence(head_bundle, item.head_path)
        changed_code = (
            base_scope["scope_inclusion"] == "included"
            or head_scope["scope_inclusion"] == "included"
        )
        scope = "changed_code" if changed_code else "non_code"
        changed_code_count += int(changed_code)
        added_lines += sum(hunk.added_diff_lines for hunk in item.hunks)
        deleted_lines += sum(hunk.deleted_diff_lines for hunk in item.hunks)
        paired_file = item.base_path is not None and item.head_path is not None
        base_callables = _affected_callables(
            base_bundle, item.base_path, item, "base", base_scope
        )
        head_callables = _affected_callables(
            head_bundle, item.head_path, item, "head", head_scope
        )
        units.append({
            "file_change_id": _file_change_id(subject_key, analyzed, item),
            "base_path": item.base_path,
            "head_path": item.head_path,
            "change_kind": item.change_kind,
            "scope": scope,
            "side_statuses": {"base": base_scope, "head": head_scope},
            "hunks": {
                "status": item.hunk_status,
                "unavailable_reason": item.hunk_unavailable_reason,
                "items": [_range_document(hunk) for hunk in item.hunks],
            },
            "evidence": (
                {
                    "base": base_evidence,
                    "head": head_evidence,
                    "metric_observations": _metric_deltas(
                        base_evidence, head_evidence, paired_file=paired_file
                    ),
                }
                if changed_code else None
            ),
            "affected_callables": {
                "base": base_callables,
                "head": head_callables,
                "identity_boundary": (
                    "side-local overlap observations only; base and head rows "
                    "are not matched"
                ),
            },
        })

    status, reasons = _overall_status(analyzed, units)
    document: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "program_version": PROGRAM_VERSION,
        "status": status,
        "reasons": reasons,
        "capabilities": {
            "exact_direct_tree_comparison": True,
            "side_local_callable_overlap": True,
            "cross_revision_callable_matching": False,
            "duplication_change_classification": False,
            "hotspot_change_classification": False,
            "policy_evaluation": False,
            "sarif_projection": False,
            "risk_or_severity_scoring": False,
        },
        "subject": {
            "subject_key": subject_key,
            "subject_key_basis": analyzed.base.repository.get("subject_key_basis"),
        },
        "base": _side_document(analyzed.base),
        "head": _side_document(analyzed.head),
        "git_provenance": {
            "comparison": "tree(base) -> tree(head)",
            "change_extraction_status": changes.status,
            "ancestry": analyzed.resolved.ancestry,
            "shallow_repository": analyzed.resolved.shallow_repository,
            "worktree_state_observed": analyzed.resolved.worktree_state,
            "uncommitted_and_untracked_content_excluded": True,
            "network_contacted": False,
            "merge_base_inferred": False,
            "rename_detection": "exact_content_one_to_one_only",
        },
        "comparability": {
            "metric_contracts_equal": (
                _manifest_contract(analyzed.base, "metric_contract_version")
                == _manifest_contract(analyzed.head, "metric_contract_version")
            ),
            "complexity_contracts_equal": (
                _manifest_contract(analyzed.base, "complexity_contract_version")
                == _manifest_contract(analyzed.head, "complexity_contract_version")
            ),
            "exclusion_policies_equal": (
                _manifest_contract(analyzed.base, "exclusion_policy_version")
                == _manifest_contract(analyzed.head, "exclusion_policy_version")
            ),
        },
        "counts": {
            "git_changed_files": len(units),
            "changed_code_files": changed_code_count,
            "non_code_files": len(units) - changed_code_count,
            "by_change_kind": {
                kind: kind_counts.get(kind, 0)
                for kind in (
                    "added", "deleted", "modified", "renamed_exact", "type_changed"
                )
            },
            "added_diff_lines": added_lines,
            "deleted_diff_lines": deleted_lines,
        },
        "file_changes": units,
        "repository_metric_evidence": _repository_metric_evidence(analyzed),
    }
    validate_document(document)
    return document


def diagnostic_document(
    *,
    status: str,
    reason: str,
    base_requested: str,
    head_requested: str,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """A deterministic unavailable/failed result; never an empty-diff claim."""
    if status not in {"unavailable", "failed"}:
        raise ValueError("diagnostic status must be unavailable or failed")
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "program_version": PROGRAM_VERSION,
        "status": status,
        "reasons": [reason],
        "capabilities": {
            "exact_direct_tree_comparison": True,
            "side_local_callable_overlap": True,
            "cross_revision_callable_matching": False,
            "policy_evaluation": False,
            "sarif_projection": False,
            "risk_or_severity_scoring": False,
        },
        "base": {
            "requested_revision": base_requested,
            "resolved_commit_sha": base_sha,
        },
        "head": {
            "requested_revision": head_requested,
            "resolved_commit_sha": head_sha,
        },
        "counts": None,
        "file_changes": None,
    }


def validate_document(document: Mapping[str, Any]) -> None:
    """Validate internal v1 invariants without creating an Artifact schema."""
    if document.get("format") != FORMAT or document.get("format_version") != FORMAT_VERSION:
        raise ChangedCodeInvariantError("format_identity_invalid")
    if document.get("status") not in STATUS_VALUES:
        raise ChangedCodeInvariantError("status_invalid")
    units = document.get("file_changes")
    counts = document.get("counts")
    if units is None:
        if document.get("status") not in {"unavailable", "failed"} or counts is not None:
            raise ChangedCodeInvariantError("diagnostic_shape_invalid")
        return
    if not isinstance(units, list) or not isinstance(counts, Mapping):
        raise ChangedCodeInvariantError("result_shape_invalid")
    if counts.get("git_changed_files") != len(units):
        raise ChangedCodeInvariantError("git_file_count_mismatch")
    identifiers = [unit.get("file_change_id") for unit in units]
    if len(identifiers) != len(set(identifiers)):
        raise ChangedCodeInvariantError("file_change_id_duplicate")
    for unit in units:
        if unit.get("change_kind") == "renamed_exact" and (
            unit.get("base_path") is None or unit.get("head_path") is None
        ):
            raise ChangedCodeInvariantError("rename_path_missing")
        for ordinal, hunk in enumerate(unit["hunks"]["items"], start=1):
            if hunk.get("ordinal") != ordinal:
                raise ChangedCodeInvariantError("hunk_ordinal_invalid")
            if hunk["deleted_diff_lines"] != hunk["base"]["line_count"]:
                raise ChangedCodeInvariantError("deleted_hunk_count_mismatch")
            if hunk["added_diff_lines"] != hunk["head"]["line_count"]:
                raise ChangedCodeInvariantError("added_hunk_count_mismatch")
        evidence = unit.get("evidence")
        for observation in (evidence or {}).get("metric_observations", ()):
            delta = observation.get("delta")
            if delta is None:
                continue
            before = _number((observation.get("base") or {}).get("value"))
            after = _number((observation.get("head") or {}).get("value"))
            if before is None or after is None or delta != after - before:
                raise ChangedCodeInvariantError("metric_delta_invalid")


def canonical_json(document: Mapping[str, Any]) -> str:
    validate_document(document)
    return json.dumps(
        document,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def _display_path(unit: Mapping[str, Any]) -> str:
    base, head = unit.get("base_path"), unit.get("head_path")
    if base and head and base != head:
        return f"{base} -> {head}"
    return str(head or base or "<unknown>")


def render_text(document: Mapping[str, Any]) -> str:
    validate_document(document)
    lines = [
        f"Metrolith Changed-Code Analysis {document['format_version']}",
        f"status: {document['status']}",
    ]
    for reason in document.get("reasons") or ():
        lines.append(f"reason: {reason}")
    if document.get("file_changes") is None:
        lines.extend([
            f"base: {document['base']['resolved_commit_sha'] or document['base']['requested_revision']}",
            f"head: {document['head']['resolved_commit_sha'] or document['head']['requested_revision']}",
            "No empty-diff conclusion is available.",
        ])
        return "\n".join(lines) + "\n"

    lines.extend([
        f"base: {document['base']['resolved_commit_sha']}",
        f"head: {document['head']['resolved_commit_sha']}",
        f"ancestry: {document['git_provenance']['ancestry']}",
        (
            "files: "
            f"{document['counts']['git_changed_files']} Git-changed, "
            f"{document['counts']['changed_code_files']} changed-code, "
            f"{document['counts']['non_code_files']} non-code"
        ),
        (
            "diff lines: "
            f"+{document['counts']['added_diff_lines']} "
            f"-{document['counts']['deleted_diff_lines']} "
            "(physical Git diff lines, not Metrolith LOC)"
        ),
        "",
    ])
    for unit in document["file_changes"]:
        lines.append(
            f"[{unit['change_kind']}] {_display_path(unit)} ({unit['scope']})"
        )
        if unit["hunks"]["status"] != "complete":
            lines.append(
                f"  hunks unavailable: {unit['hunks']['unavailable_reason']}"
            )
        for hunk in unit["hunks"]["items"]:
            lines.append(
                "  hunk " + str(hunk["ordinal"])
                + f": base {hunk['base']['start_line']},{hunk['base']['line_count']}"
                + f" head {hunk['head']['start_line']},{hunk['head']['line_count']}"
            )
        if unit["scope"] != "changed_code":
            continue
        observations = unit["evidence"]["metric_observations"]
        deltas = [item for item in observations if item["delta"] not in (None, 0)]
        if not deltas:
            lines.append("  no measured metric delta observed")
        else:
            for item in deltas:
                lines.append(
                    f"  {item['metric']}: {item['base']['value']} -> "
                    f"{item['head']['value']} ({item['delta']:+})"
                )
        for side_name in ("base", "head"):
            affected = unit["affected_callables"][side_name]
            lines.append(
                f"  affected {side_name}-side callables: "
                f"{len(affected['observations'])} ({affected['availability']})"
            )
            for row in affected["observations"]:
                lines.append(
                    f"    {row['qualified_name'] or row['name']} "
                    f"[{row['start_line']}-{row['end_line']}]"
                )
    lines.extend([
        "",
        "Boundaries: no cross-revision callable matching; no new/removed callable claims.",
        "No risk, severity, Policy, SARIF, duplication-change, or hotspot-change result.",
    ])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Markdown review brief (Evidence Presentation 3.9)
# ---------------------------------------------------------------------------
#
# A THIRD RENDERING of the same document, not a third analysis. `render_markdown`
# takes the object `build_document` already produced and validated; it opens no
# repository, reads no artifact and computes no metric. If it ever needed to,
# that would be the bug.
#
# **The identity boundary is the whole reason this renderer is worded carefully.**
# Metrolith observes that a callable on ONE side intersects a changed hunk. It
# does not match a base-side callable to a head-side one, so it cannot know that
# a callable is new, renamed, moved or modified — those claims all require a
# cross-revision identity contract, and none exists. The vocabulary below says
# "intersects a changed hunk" and never "new" or "changed" callable, and a test
# scans rendered output for the forbidden phrasings.

#: Stated at the top of every brief. The reader is a human reviewer, and the
#: single most likely misreading is that this document lists changed functions.
IDENTITY_BOUNDARY_STATEMENT = (
    "Metrolith does not match callables across revisions. Every callable listed "
    "here is a **side-local observation**: a callable declared on that side "
    "whose line span intersects a changed hunk on that same side. This brief "
    "therefore never states that a callable was added, removed, renamed, moved "
    "or modified — those are cross-revision identity claims, and Metrolith has "
    "no identity contract that would support one."
)

DESCRIPTIVE_ONLY_STATEMENT = (
    "Descriptive evidence, not a gate and not a verdict. This brief carries no "
    "risk rating, no severity, no policy result and no SARIF projection. A "
    "metric delta is an observation about two measurements, not a judgment "
    "about a change."
)

DIFF_LINE_STATEMENT = (
    "Added and deleted diff-line counts are **physical Git diff lines**, not "
    "Metrolith LOC. The two count different things and will not agree."
)


def _code_span(value: Any, *, table_cell: bool = True) -> str:
    """One value as a Markdown code span that cannot break out of itself.

    Three hazards, all handled here rather than at each call site:

    * **Control and bidirectional characters** are rendered visibly, so a
      crafted path cannot reorder what a reviewer sees. This is the same rule
      `escape_markdown` applies, taken from the same implementation.
    * **Backticks in the value** would close the span early, so the delimiter
      is made longer than the longest run in the content and padded per
      CommonMark, which is the mechanism the format provides for exactly this.
    * **A pipe inside a table cell** ends the cell even within a code span —
      GFM requires `\\|` there regardless of the surrounding inline context —
      so it is escaped when, and only when, the span is going into a table.
    """
    from modules.presentation import scalar
    from modules.summary import visible_text

    text = visible_text(scalar(value, absent="unavailable"))
    # An empty value is an empty cell. There is no such thing as an empty code
    # span in CommonMark -- two adjacent backticks are literal text -- so
    # wrapping nothing would emit visible backticks instead of a blank.
    if not text:
        return ""
    if table_cell:
        text = text.replace("|", "\\|")
    longest = 0
    run = 0
    for character in text:
        run = run + 1 if character == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _markdown_number(value: Any) -> str:
    """Render one measurement for a table cell, keeping null distinct from 0."""
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _markdown_delta(value: Any) -> str:
    """Render one delta with an explicit sign, or `unavailable`.

    Signed deliberately: an unsigned `0` and an unavailable delta look alike at
    a glance, and `+0` does not.
    """
    if value is None:
        return "unavailable"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:+d}"
    if isinstance(value, float):
        return f"{value:+g}"
    return str(value)


def _markdown_lines(document: Mapping[str, Any]) -> list[str]:
    from modules.summary import escape_markdown

    escape = escape_markdown
    lines: list[str] = ["# Metrolith Changed-Code review brief", ""]
    lines.append(
        f"Format `{document['format']}` {escape(document['format_version'])}, "
        f"produced by Metrolith {escape(document['program_version'])}."
    )
    lines.append("")
    lines.append(f"- Status: **{escape(document['status'])}**")
    for reason in document.get("reasons") or ():
        lines.append(f"- Reason: {_code_span(reason, table_cell=False)}")
    lines.append("")
    lines.append(IDENTITY_BOUNDARY_STATEMENT)

    # A diagnostic document carries no file list. It must not be rendered as an
    # empty diff: "nothing changed" and "the comparison could not be made" are
    # different answers and only one of them is a result.
    if document.get("file_changes") is None:
        lines.extend([
            "",
            "## Revisions",
            "",
            "| Side | Requested | Resolved |",
            "|---|---|---|",
        ])
        for side in ("base", "head"):
            entry = document[side]
            lines.append(
                f"| {side} | {_code_span(entry.get('requested_revision'))} "
                f"| {_code_span(entry.get('resolved_commit_sha'))} |"
            )
        lines.extend([
            "",
            "**No comparison was produced, and no empty-diff conclusion is "
            "available.** The absence of a file list below is the absence of a "
            "result, not evidence that nothing changed.",
            "",
            DESCRIPTIVE_ONLY_STATEMENT,
        ])
        return lines

    lines.extend(["", "## Revisions", ""])
    lines.append(
        "| Side | Requested | Resolved | Analyzed | Analysis status | "
        "Metric Contract | Complexity Contract | Exclusion Policy |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for side in ("base", "head"):
        entry = document[side]
        lines.append(
            f"| {side} | {_code_span(entry.get('requested_revision'))} "
            f"| {_code_span(entry.get('resolved_commit_sha'))} "
            f"| {_code_span(entry.get('analyzed_commit_sha'))} "
            f"| {escape(entry.get('analysis_status'))} "
            f"| {escape(entry.get('metric_contract_version'))} "
            f"| {escape(entry.get('complexity_contract_version'))} "
            f"| {escape(entry.get('exclusion_policy_version'))} |"
        )

    provenance = document["git_provenance"]
    lines.extend(["", "### Git provenance", ""])
    for label, key in (
        ("Comparison", "comparison"),
        ("Change extraction status", "change_extraction_status"),
        ("Ancestry", "ancestry"),
        ("Shallow repository", "shallow_repository"),
        ("Worktree state observed", "worktree_state_observed"),
        ("Rename detection", "rename_detection"),
        ("Uncommitted and untracked content excluded", (
            "uncommitted_and_untracked_content_excluded"
        )),
        ("Network contacted", "network_contacted"),
        ("Merge base inferred", "merge_base_inferred"),
    ):
        lines.append(f"- {label}: {_code_span(provenance.get(key), table_cell=False)}")

    comparability = document["comparability"]
    lines.extend(["", "### Comparability", ""])
    lines.append("| Contract | Sides agree |")
    lines.append("|---|---|")
    for label, key in (
        ("Metric Contract", "metric_contracts_equal"),
        ("Complexity Contract", "complexity_contracts_equal"),
        ("Exclusion Policy", "exclusion_policies_equal"),
    ):
        lines.append(f"| {label} | {_code_span(comparability.get(key))} |")
    lines.append("")
    lines.append(
        "A delta is reported only where the two sides carry the SAME contract "
        "version. Where they do not, the delta is `unavailable` and the reason "
        "is stated rather than the numbers being subtracted anyway."
    )

    counts = document["counts"]
    lines.extend(["", "## Change summary", ""])
    lines.append(f"- Git-changed files: {counts['git_changed_files']}")
    lines.append(f"- Changed-code files: {counts['changed_code_files']}")
    lines.append(f"- Non-code files: {counts['non_code_files']}")
    for kind, value in sorted(counts["by_change_kind"].items()):
        lines.append(f"- Change kind {_code_span(kind, table_cell=False)}: {value}")
    lines.append(
        f"- Diff lines: +{counts['added_diff_lines']} "
        f"/ -{counts['deleted_diff_lines']}"
    )
    lines.append("")
    lines.append(DIFF_LINE_STATEMENT)

    evidence = document.get("repository_metric_evidence") or {}
    if evidence.get("deltas"):
        lines.extend(["", "## Repository metric observations", ""])
        lines.append("| Metric | Base | Head | Delta | Not evaluable because |")
        lines.append("|---|---:|---:|---:|---|")
        for row in evidence["deltas"]:
            lines.append(
                f"| {_code_span(row['metric'])} "
                f"| {_markdown_number(row['base']['value'])} "
                f"| {_markdown_number(row['head']['value'])} "
                f"| {_markdown_delta(row['delta'])} "
                f"| {escape(row.get('not_evaluable_reason') or '')} |"
            )

    lines.extend(["", "## Changed files", ""])
    if not document["file_changes"]:
        lines.append(
            "Git reported no changed file between these two trees. This is a "
            "measured empty result, not an unavailable one."
        )
    for unit in document["file_changes"]:
        lines.extend(_markdown_file_change(unit, escape))

    lines.extend(["", "## Boundaries", ""])
    capabilities = document.get("capabilities") or {}
    for name in sorted(capabilities):
        lines.append(
            f"- {_code_span(name, table_cell=False)}: "
            f"{escape(capabilities[name])}"
        )
    lines.extend(["", DESCRIPTIVE_ONLY_STATEMENT])
    return lines


def _markdown_file_change(unit: Mapping[str, Any], escape) -> list[str]:
    """One file's section. Head-side callable observations are the detail."""
    lines = ["", f"### {_code_span(_display_path(unit), table_cell=False)}", ""]
    lines.append(f"- Change kind: {_code_span(unit['change_kind'], table_cell=False)}")
    lines.append(f"- Scope: {_code_span(unit['scope'], table_cell=False)}")
    lines.append(f"- File change id: {_code_span(unit['file_change_id'], table_cell=False)}")
    for side in ("base", "head"):
        status = unit["side_statuses"][side]
        lines.append(
            f"- {side} scope inclusion: "
            f"{_code_span(status.get('scope_inclusion'), table_cell=False)}"
        )

    hunks = unit["hunks"]
    if hunks["status"] != "complete":
        lines.append("")
        lines.append(
            f"Hunks unavailable: "
            f"{_code_span(hunks.get('unavailable_reason'), table_cell=False)}. "
            "No line range is claimed for this file."
        )
    elif hunks["items"]:
        lines.extend([
            "",
            "| Hunk | Base range | Head range | +lines | -lines |",
            "|---:|---|---|---:|---:|",
        ])
        for hunk in hunks["items"]:
            lines.append(
                f"| {hunk['ordinal']} "
                f"| {hunk['base']['start_line']},{hunk['base']['line_count']} "
                f"| {hunk['head']['start_line']},{hunk['head']['line_count']} "
                f"| {hunk['added_diff_lines']} | {hunk['deleted_diff_lines']} |"
            )
    else:
        lines.append("")
        lines.append("No hunk was reported for this file.")

    if unit["scope"] != "changed_code":
        lines.append("")
        lines.append(
            "Outside Metrolith metric scope on both sides, so no measurement "
            "evidence is reported for this file."
        )
        return lines

    observations = unit["evidence"]["metric_observations"]
    reported = [row for row in observations if row["delta"] not in (None, 0)]
    lines.extend(["", "#### File metric observations", ""])
    if not reported:
        lines.append(
            "No non-zero metric delta was observed for this file. A zero delta "
            "is a measurement; an `unavailable` delta is not, and the two are "
            "listed separately below."
        )
    else:
        lines.append("| Metric | Family | Base | Head | Delta |")
        lines.append("|---|---|---:|---:|---:|")
        for row in reported:
            lines.append(
                f"| {_code_span(row['metric'])} | {escape(row['family'])} "
                f"| {_markdown_number(row['base']['value'])} "
                f"| {_markdown_number(row['head']['value'])} "
                f"| {_markdown_delta(row['delta'])} |"
            )

    unavailable = [row for row in observations if row["delta"] is None]
    if unavailable:
        lines.append("")
        lines.append("| Metric | Not evaluable because |")
        lines.append("|---|---|")
        for row in unavailable:
            lines.append(
                f"| {_code_span(row['metric'])} "
                f"| {_code_span(row.get('not_evaluable_reason'))} |"
            )

    # Head-side observations are the review surface. The base-side set is
    # reported as a COUNT only: listing both side by side is exactly the
    # presentation that invites a reader to pair them, and they are not paired.
    head = unit["affected_callables"]["head"]
    base = unit["affected_callables"]["base"]
    lines.extend(["", "#### Head-side callables intersecting a changed hunk", ""])
    lines.append(f"- Availability: {_code_span(head['availability'], table_cell=False)}")
    if head.get("unavailable_reason"):
        lines.append(f"- Reason: {_code_span(head['unavailable_reason'], table_cell=False)}")
    lines.append(f"- Head-side observations: {len(head['observations'])}")
    lines.append(
        f"- Base-side observations: {len(base['observations'])} "
        "(counted only; base-side and head-side callables are not matched to "
        "one another)"
    )

    if head["observations"]:
        lines.extend([
            "",
            "| Callable | Kind | Lines | Hunks | Cyclomatic | Cognitive | "
            "NLOC | Nesting | Parameters |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ])
        for row in head["observations"]:
            metrics = row.get("metrics") or {}
            overlaps = ",".join(
                str(item["hunk_ordinal"]) for item in row.get("overlaps") or ()
            )
            lines.append(
                f"| {_code_span(row.get('qualified_name') or row.get('name'))} "
                f"| {escape(row.get('callable_kind'))} "
                f"| {row['start_line']}-{row['end_line']} "
                f"| {escape(overlaps)} "
                f"| {_markdown_number(metrics.get('cyclomatic_complexity'))} "
                f"| {_markdown_number(metrics.get('cognitive_complexity'))} "
                f"| {_markdown_number(metrics.get('nloc'))} "
                f"| {_markdown_number(metrics.get('max_nesting_depth'))} "
                f"| {_markdown_number(metrics.get('formal_parameter_count'))} |"
            )
        lines.append("")
        lines.append(
            "Each row is a callable declared on the head side whose line span "
            "intersects one of the head-side hunks listed above. The measured "
            "values are that callable's own head-side measurements; no value "
            "here is a difference between revisions."
        )
    return lines


def render_markdown(document: Mapping[str, Any]) -> str:
    """Render one Changed-Code document as a deterministic Markdown brief.

    Deterministic in the strict sense the plan requires: the same document
    always produces the same bytes. There is no timestamp, no host path, no
    hash of anything not already in the document, and no iteration over an
    unordered container — `file_changes` arrives already sorted by
    `git_change_extractor`, and every mapping this renderer walks is either a
    fixed tuple of keys or explicitly sorted here.

    Validation runs first, for the same reason `canonical_json` validates: a
    document that violates its own invariants must not reach a reader in a
    format that looks authoritative.
    """
    validate_document(document)
    return "\n".join(_markdown_lines(document)) + "\n"


__all__ = [
    "ChangedCodeInvariantError",
    "DESCRIPTIVE_ONLY_STATEMENT",
    "DIFF_LINE_STATEMENT",
    "FORMAT",
    "FORMAT_VERSION",
    "IDENTITY_BOUNDARY_STATEMENT",
    "build_document",
    "canonical_json",
    "diagnostic_document",
    "render_markdown",
    "render_text",
    "validate_document",
]
