"""Per-callable complexity ledger and aggregates (Artifact Schema 1.9.0).

Mirrors ``modules.contribution_ledger`` deliberately: same column-order-is-the-
contract rule, same null-vs-empty cell rendering, same partition threshold, same
container descriptor. A per-row artifact whose size tracks the repository rather
than the cohort already has a proven shape here, and inventing a second one would
add a second thing to get wrong.

**No metric is calculated in this module.** ``modules.callable_analysis`` computed
every value during measurement; this only joins repository identity, chooses a
container, writes it, and derives the approved aggregates by reading rows back.

The aggregate set is deliberately small and fixed. No percentiles, no threshold
counts, no composite score: a threshold count is a policy gate wearing a
descriptive label, and Policy v2 is not in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.config import COMPLEXITY_CONTRACT_VERSION
from modules.subject import subject_key_of

#: Column order is fixed by `callable_row-1.9.schema.json`.
CALLABLE_COLUMNS: tuple[str, ...] = (
    "subject_key",
    "repository_url",
    "analyzed_sha",
    "relative_path",
    "content_sha256",
    "detected_language",
    "callable_row_id",
    "row_id_basis",
    "name",
    "qualified_name",
    "callable_kind",
    "owner_kind",
    "owner_name",
    "signature_discriminator",
    "ordinal",
    "receiver_type_name",
    "receiver_is_pointer",
    "start_line",
    "end_line",
    "body_start_line",
    "body_end_line",
    "location_maps_to_original_source",
    "structural_complexity_status",
    "nloc_status",
    "nloc",
    "formal_parameter_count",
    "declares_typescript_this_parameter",
    "cyclomatic_complexity",
    "decision_point_count",
    "boolean_operator_count",
    "max_condition_operator_count",
    "max_nesting_depth",
    # Artifact Schema 1.10.0 / Complexity Contract 2.0.0. Minimum 0, and 0 is an
    # ORDINARY MEASURED VALUE -- so an unavailable measurement is NULL and never
    # 0. Governed by structural_complexity_status, which it shares because it is
    # computed in the same traversal over the same selected tree.
    "cognitive_complexity",
    "complexity_contract_version",
)

#: Same threshold as the contribution ledger, for the same reason: above it a
#: single file must be read whole to validate one repository.
SINGLE_FILE_ROW_THRESHOLD = 50_000

CONTAINER_SINGLE_CSV = "single_csv"
CONTAINER_PARTITIONED_CSV = "partitioned_csv"

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_NOT_APPLICABLE = "not_applicable"

#: Typed reasons. The completeness gate requires one whenever the callable
#: artifact is absent, so "we did not write it" can never be silent.
REASON_NO_SUPPORTED_SOURCE = "no_supported_source_files"
REASON_ALL_FAILED = "all_files_failed_parse"
REASON_PARSER_MISSING = "parser_capability_missing"
REASON_DISABLED = "disabled_by_configuration"

#: The approved aggregate set, in emission order.
AGGREGATE_FIELDS: tuple[str, ...] = (
    "callable_count",
    "cyclomatic_complexity_total",
    "cyclomatic_complexity_mean",
    "cyclomatic_complexity_median",
    "cyclomatic_complexity_max",
    "nloc_median",
    "nloc_max",
    "max_nesting_depth_median",
    "max_nesting_depth_max",
    "formal_parameter_count_median",
    "formal_parameter_count_max",
)


def _cell(value: Any) -> str:
    """Render one cell, keeping null distinct from zero and from empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def lower_median(values: Sequence[int]) -> int | None:
    """Lower median: always an OBSERVED value, never an interpolated half-unit.

    With an even population the arithmetic median of 3 and 4 is 3.5, which no
    callable has. Reporting a value no measured callable exhibits invites reading
    it as a real observation, so the lower of the two middle values is used and
    the choice is stated rather than left to a library default.
    """
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def aggregate_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive the approved aggregates from evaluable rows only.

    A row whose structural status is failed contributed no measurement, so it is
    excluded rather than counted as zero. When nothing is evaluable every
    aggregate is null -- unavailable, never a measured zero.
    """
    cyclomatic: list[int] = []
    nloc: list[int] = []
    nesting: list[int] = []
    parameters: list[int] = []
    counted = 0

    for row in rows:
        counted += 1
        if row.get("structural_complexity_status") in {STATUS_COMPLETE, STATUS_PARTIAL}:
            for source, target in (
                ("cyclomatic_complexity", cyclomatic),
                ("max_nesting_depth", nesting),
                ("formal_parameter_count", parameters),
            ):
                value = row.get(source)
                if isinstance(value, int):
                    target.append(value)
        if row.get("nloc_status") in {STATUS_COMPLETE, STATUS_PARTIAL}:
            value = row.get("nloc")
            if isinstance(value, int):
                nloc.append(value)

    return {
        "callable_count": counted,
        "cyclomatic_complexity_total": sum(cyclomatic) if cyclomatic else None,
        "cyclomatic_complexity_mean": (
            round(sum(cyclomatic) / len(cyclomatic), 4) if cyclomatic else None
        ),
        "cyclomatic_complexity_median": lower_median(cyclomatic),
        "cyclomatic_complexity_max": max(cyclomatic) if cyclomatic else None,
        "nloc_median": lower_median(nloc),
        "nloc_max": max(nloc) if nloc else None,
        "max_nesting_depth_median": lower_median(nesting),
        "max_nesting_depth_max": max(nesting) if nesting else None,
        "formal_parameter_count_median": lower_median(parameters),
        "formal_parameter_count_max": max(parameters) if parameters else None,
    }


def empty_aggregate() -> dict[str, Any]:
    """Every aggregate null, with a null count. Used when nothing is evaluable."""
    return {name: None for name in AGGREGATE_FIELDS}


#: Complexity Contract 2.0.0 section 12.8. One mapping, so the cognitive state
#: can never drift from the structural status it is derived from.
COGNITIVE_MEASURED = "measured"
COGNITIVE_PARTIAL = "partial"
COGNITIVE_FAILED = "failed"
COGNITIVE_NOT_APPLICABLE = "not_applicable"

_COGNITIVE_STATE_BY_STATUS = {
    STATUS_COMPLETE: COGNITIVE_MEASURED,
    STATUS_PARTIAL: COGNITIVE_PARTIAL,
    STATUS_FAILED: COGNITIVE_FAILED,
    STATUS_NOT_APPLICABLE: COGNITIVE_NOT_APPLICABLE,
}


def cognitive_state_for(status: str) -> str:
    """The cognitive measurement state implied by a structural status."""
    return _COGNITIVE_STATE_BY_STATUS[status]


def derive_complexity(
    rows: Sequence[Mapping[str, Any]],
    file_statuses: Sequence[str],
    rows_by_language: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build one repository's complexity summary.

    ``file_statuses`` is every measured file's ``structural_complexity_status``,
    which is what distinguishes "measured and empty" from "could not measure".
    Deriving the repository status from the ROWS alone cannot do that: both cases
    have zero rows.
    """
    considered = [status for status in file_statuses if status != STATUS_NOT_APPLICABLE]
    if not considered:
        status = STATUS_NOT_APPLICABLE
        reason: str | None = REASON_NO_SUPPORTED_SOURCE
    elif all(status == STATUS_FAILED for status in considered):
        status = STATUS_FAILED
        reason = REASON_ALL_FAILED
    elif any(status in {STATUS_FAILED, STATUS_PARTIAL} for status in considered):
        status = STATUS_PARTIAL
        reason = None
    else:
        status = STATUS_COMPLETE
        reason = None

    evaluable = status in {STATUS_COMPLETE, STATUS_PARTIAL}
    by_language: dict[str, Any] = {}
    for language, language_rows in sorted((rows_by_language or {}).items()):
        by_language[language] = aggregate_rows(language_rows)

    return {
        "complexity_contract_version": COMPLEXITY_CONTRACT_VERSION,
        "status": status,
        # Complexity Contract 2.0.0 section 12.8. Cognitive complexity shares
        # the structural traversal, so it shares that traversal's outcome; the
        # state is named separately because its ABSENCE is a distinct fact --
        # a pre-1.10 run measured no cognitive complexity at all, which is not
        # the same as having failed to.
        "cognitive_measurement_state": _COGNITIVE_STATE_BY_STATUS[status],
        "unavailable_reason": reason,
        "aggregate": aggregate_rows(rows) if evaluable else empty_aggregate(),
        "by_language": by_language if evaluable else {},
    }


def build_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Assemble ledger rows, joining repository identity onto measured records."""
    rows: list[dict[str, Any]] = []
    for result in results:
        acquisition = result.get("acquisition") or {}
        identity = {
            "subject_key": subject_key_of(result),
            "repository_url": result.get("repository_url"),
            "analyzed_sha": acquisition.get("analyzed_commit_sha"),
        }
        for record in result.get("metrics", {}).get("callable_records", ()):
            row = {name: None for name in CALLABLE_COLUMNS}
            row.update(identity)
            row.update({key: value for key, value in record.items() if key in row})
            # Complexity Contract 2.0.0 section 12.7: a failed measurement is
            # NULL, never 0. Enforced here rather than trusted of the producer,
            # because 0 is a legitimate cognitive value and a wrong zero would
            # be indistinguishable from a measured one downstream.
            if row.get("structural_complexity_status") == STATUS_FAILED:
                row["cognitive_complexity"] = None
            rows.append(row)

    # Deterministic ordering, and also the comparison key, so an inserted row
    # cannot cascade into unrelated differences.
    rows.sort(
        key=lambda item: (
            str(item.get("subject_key") or ""),
            str(item.get("relative_path") or ""),
            int(item.get("start_line") or 0),
            str(item.get("callable_row_id") or ""),
        )
    )
    return rows


def write_ledger(
    run_dir: Path, rows: Sequence[Mapping[str, Any]], *, writer
) -> dict[str, Any]:
    """Write the callables ledger and return its container descriptor."""
    container = (
        CONTAINER_PARTITIONED_CSV
        if len(rows) > SINGLE_FILE_ROW_THRESHOLD
        else CONTAINER_SINGLE_CSV
    )
    partitions: list[dict[str, Any]] = []

    if container == CONTAINER_SINGLE_CSV:
        writer(
            run_dir / "callables.csv",
            list(CALLABLE_COLUMNS),
            [{name: _cell(row.get(name)) for name in CALLABLE_COLUMNS} for row in rows],
        )
        partitions.append(
            {
                "path": "callables.csv",
                "repository_url": None,
                "row_count": len(rows),
                "sha256": None,
            }
        )
    else:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("subject_key") or ""), []).append(row)
        for index, key in enumerate(sorted(grouped)):
            group = grouped[key]
            name = f"callables/{index:05d}.csv"
            writer(
                run_dir / name,
                list(CALLABLE_COLUMNS),
                [
                    {column: _cell(item.get(column)) for column in CALLABLE_COLUMNS}
                    for item in group
                ],
            )
            partitions.append(
                {
                    "path": name,
                    "repository_url": (group[0].get("repository_url") if group else None),
                    "row_count": len(group),
                    "sha256": None,
                }
            )

    return {
        "container_format": container,
        "row_contract_version": "1.9.0",
        "row_count": len(rows),
        "compression": None,
        "ordering": "subject_key, relative_path, start_line, callable_row_id",
        "partitions": partitions,
        "selection_evidence": {
            "single_file_row_threshold": SINGLE_FILE_ROW_THRESHOLD,
            "observed_row_count": len(rows),
            "decision": (
                "row count is within the single-file threshold"
                if container == CONTAINER_SINGLE_CSV
                else "row count exceeds the single-file threshold; partitioned "
                     "per subject so validating one repository does not require "
                     "reading the whole cohort"
            ),
        },
    }
