"""Per-file metric-contribution ledger (plan section 14).

Persists the ``FileMetricResult`` components that aggregation would otherwise
discard, so an aggregate metric can be reconciled back to the individual files
that produced it.

**No metric is calculated here.** ``modules.core_metrics`` builds every row from
values it has already computed, using the shared ``derive_*`` functions. This
module only chooses a container, writes it, and reconciles it.

The row contract is fixed by ``contribution_row-1.5.schema.json`` and the
container choice may not change any field name, type, status semantic, or
ordering rule (plan section 14.5).

Reconciliation follows plan section 14.4, and rule 4 is the one that matters
most: **when an aggregate status is Failed and its value is null, reconciliation
is `not_evaluable`** — not zero, and not a residual. Treating an unavailable
measurement as zero would silently manufacture agreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.subject import subject_key_of
from modules.core_metrics import (
    ENTITY_KEYS,
    LOC_KEYS,
    derive_classes_structs,
    derive_lines_of_code,
    derive_methods_functions,
)

# Column order is fixed by the packaged schema.
CONTRIBUTION_COLUMNS: tuple[str, ...] = (
    "subject_key",
    "repository_url",
    "requested_sha",
    "resolved_sha",
    "analyzed_sha",
    "relative_path",
    "content_sha256",
    "size_bytes",
    "detected_language",
    "inclusion_state",
    "contribution_state",
    "read_status",
    "parse_status",
    "loc_status",
    "classes_structs_status",
    "methods_functions_status",
    # Artifact Schema 1.9.0. File-level complexity state is persisted EXPLICITLY
    # rather than inferred from the absence of callable rows: a parse-failed file
    # and a genuinely empty one both emit zero rows, and only these columns tell
    # them apart.
    "structural_complexity_status",
    "nloc_status",
    "callable_count",
    "parser_compatibility_strategy",
    "recovery_strategy",
    "error_reference",
    "recovery_reference",
    "offset_mapping_mode",
    "source_files_contribution",
    "lines_of_code",
    "classes_structs",
    "methods_functions",
    *LOC_KEYS,
    *ENTITY_KEYS,
)

# Above this row count a single file becomes an unhelpful container: it must be
# read whole to validate one repository, and comparison cost grows with the
# cohort rather than with the repository under inspection. Measured evidence for
# the choice is recorded in the container descriptor.
SINGLE_FILE_ROW_THRESHOLD = 50_000

CONTAINER_SINGLE_CSV = "single_csv"
CONTAINER_PARTITIONED_CSV = "partitioned_csv"

RECONCILIATION_NOT_EVALUABLE = "not_evaluable"
RECONCILIATION_EXACT = "exact"
RECONCILIATION_RESIDUAL = "residual"


@dataclass(frozen=True)
class ReconciliationResult:
    """One reconciliation of an aggregate value against summed contributions."""

    dimension: str
    outcome: str
    aggregate_value: Any = None
    contribution_sum: Any = None
    residual: Any = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "outcome": self.outcome,
            "aggregate_value": self.aggregate_value,
            "contribution_sum": self.contribution_sum,
            "residual": self.residual,
            "reason": self.reason,
        }


@dataclass
class ContributionLedger:
    """Rows plus the container decision that will store them."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def container_format(self) -> str:
        return (
            CONTAINER_PARTITIONED_CSV
            if self.row_count > SINGLE_FILE_ROW_THRESHOLD
            else CONTAINER_SINGLE_CSV
        )


def build_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Assemble ledger rows from finalized repository results.

    Repository identity is joined on here; the per-file components were already
    captured during measurement.
    """
    rows: list[dict[str, Any]] = []
    for result in results:
        acquisition = result.get("acquisition") or {}
        identity = {
            "subject_key": subject_key_of(result),
            "repository_url": result.get("repository_url"),
            "requested_sha": (
                result.get("requested_commit_sha")
                or acquisition.get("requested_commit_sha")
            ),
            "resolved_sha": acquisition.get("resolved_commit_sha")
            or acquisition.get("analyzed_commit_sha"),
            "analyzed_sha": acquisition.get("analyzed_commit_sha"),
        }
        for contribution in result.get("metrics", {}).get("file_contributions", ()):
            row = {name: None for name in CONTRIBUTION_COLUMNS}
            row.update(identity)
            row.update({
                key: value for key, value in contribution.items()
                if key in row
            })
            rows.append(row)

    # Deterministic ordering: repository, then path, then content hash. This is
    # also the comparison key, so an inserted row cannot cascade into unrelated
    # differences (plan section 15.2).
    rows.sort(key=lambda item: (
        str(item.get("repository_url") or ""),
        str(item.get("relative_path") or ""),
        str(item.get("content_sha256") or ""),
    ))
    return rows


def _cell(value: Any) -> str:
    """Render one cell, keeping null distinct from zero and from empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def write_ledger(
    run_dir: Path, rows: Sequence[Mapping[str, Any]], *, writer
) -> dict[str, Any]:
    """Write the ledger and return its container descriptor.

    ``writer`` is the caller's atomic CSV writer, so this module never chooses
    its own I/O path.
    """
    ledger = ContributionLedger(list(rows))
    container = ledger.container_format()
    partitions: list[dict[str, Any]] = []

    if container == CONTAINER_SINGLE_CSV:
        writer(run_dir / "contributions.csv", list(CONTRIBUTION_COLUMNS),
               [{name: _cell(row.get(name)) for name in CONTRIBUTION_COLUMNS}
                for row in rows])
        partitions.append({
            "path": "contributions.csv",
            "repository_url": None,
            "row_count": len(rows),
            "sha256": None,
        })
    else:
        by_repository: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            by_repository.setdefault(str(row.get("repository_url") or ""), []).append(row)
        for index, url in enumerate(sorted(by_repository)):
            group = by_repository[url]
            name = f"contributions/{index:05d}.csv"
            writer(run_dir / name, list(CONTRIBUTION_COLUMNS),
                   [{column: _cell(item.get(column)) for column in CONTRIBUTION_COLUMNS}
                    for item in group])
            partitions.append({
                "path": name,
                "repository_url": url,
                "row_count": len(group),
                "sha256": None,
            })

    descriptor = {
        "container_format": container,
        # 1.9.0: three complexity-state columns. `row_contract_version` is a
        # generic semver pattern in the container schema, so the container
        # document itself needs no new version.
        "row_contract_version": "1.9.0",
        "row_count": len(rows),
        "compression": None,
        "ordering": "repository_url, relative_path, content_sha256",
        "partitions": partitions,
        "selection_evidence": {
            "single_file_row_threshold": SINGLE_FILE_ROW_THRESHOLD,
            "observed_row_count": len(rows),
            "decision": (
                "row count is within the single-file threshold"
                if container == CONTAINER_SINGLE_CSV
                else "row count exceeds the single-file threshold; partitioned "
                     "per repository so validating one repository does not "
                     "require reading the whole cohort"
            ),
        },
    }
    return descriptor


# -- reconciliation ---------------------------------------------------------

def _sum_contributions(values: Iterable[Any]) -> tuple[int, int]:
    """Sum the recorded components, reporting how many rows recorded none.

    Returns ``(total, rows_without_a_recorded_value)``.

    A row whose component is ``None`` recorded no value because its extraction
    did not produce one — a file that failed to parse while others succeeded.
    That is precisely how the aggregate itself was built: ``core_metrics`` adds a
    file's components only ``if analyzed.entities is not None``. So the faithful
    reconciliation sums the recorded values and reports the skipped count
    alongside.

    This is **not** treating null as zero. A null row contributes nothing
    because it genuinely measured nothing, which is a different claim from
    "it measured zero", and the skipped count keeps that visible. The
    null-on-Failed rule is handled separately in :func:`_compare` and still
    yields ``not_evaluable``.
    """
    total = 0
    skipped = 0
    for value in values:
        if value is None:
            skipped += 1
            continue
        total += value
    return total, skipped


def reconcile_repository(
    result: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> list[ReconciliationResult]:
    """Reconcile one repository's aggregate against its contribution rows."""
    aggregate = result.get("metrics", {}).get("aggregate", {}) or {}
    contributing = [
        row for row in rows if row.get("contribution_state") == "contributed"
    ]
    found: list[ReconciliationResult] = []

    # Rule 1. Source Files contributions sum when a row contributes.
    #
    # The status is read rather than assumed. Passing a hardcoded "complete"
    # here bypassed rule 4, so a Failed aggregate was reconciled as though it
    # were trustworthy. `or 0` did the same damage one level down: it turned a
    # row that recorded *no* contribution into a row that contributed *zero*,
    # inventing a residual out of a measurement that never happened.
    source_files_total, source_files_skipped = _sum_contributions(
        row.get("source_files_contribution") for row in contributing
    )
    found.append(_compare(
        "source_files",
        aggregate.get("source_files"),
        source_files_total,
        status=aggregate.get("source_files_status"),
        rows_without_recorded_value=source_files_skipped,
    ))

    # Rules 2 and 3. Raw components sum exactly unless the status is Failed.
    for status_field, keys in (
        ("loc_status", LOC_KEYS),
        ("classes_structs_status", ("classes", "records", "structs")),
        ("methods_functions_status",
         ("module_functions", "class_methods", "receiver_methods")),
    ):
        status = aggregate.get(status_field)
        for key in keys:
            total, skipped = _sum_contributions(row.get(key) for row in contributing)
            found.append(_compare(
                key, aggregate.get(key), total, status=status,
                rows_without_recorded_value=skipped,
            ))

    # Rule 6. Derived metrics are recomputed from stored raw components only for
    # validation, using the shared Metric Contract expressions.
    components = {}
    skipped_by_key: dict[str, int] = {}
    for key in (*LOC_KEYS, *ENTITY_KEYS):
        components[key], skipped_by_key[key] = _sum_contributions(
            row.get(key) for row in contributing
        )
    for dimension, deriver, status_field in (
        ("lines_of_code", derive_lines_of_code, "loc_status"),
        ("classes_structs", derive_classes_structs, "classes_structs_status"),
        ("methods_functions", derive_methods_functions, "methods_functions_status"),
    ):
        status = aggregate.get(status_field)
        try:
            recomputed = deriver(components)
        except TypeError:
            recomputed = None
        found.append(_compare(
            dimension, aggregate.get(dimension), recomputed, status=status,
            rows_without_recorded_value=max(skipped_by_key.values(), default=0),
        ))

    return found


def _compare(
    dimension: str, aggregate_value: Any, contribution_sum: Any, *, status: Any,
    rows_without_recorded_value: int = 0,
) -> ReconciliationResult:
    """Compare one dimension, honouring the null-on-failed rule."""
    # Rule 4. A Failed status with a null value is not evaluable. It is neither
    # zero nor a residual, and reporting it as either would be a false claim.
    if status == "failed" and aggregate_value is None:
        return ReconciliationResult(
            dimension, RECONCILIATION_NOT_EVALUABLE,
            aggregate_value, contribution_sum, None,
            "aggregate status is failed and the reported value is null",
        )
    if aggregate_value is None or contribution_sum is None:
        return ReconciliationResult(
            dimension, RECONCILIATION_NOT_EVALUABLE,
            aggregate_value, contribution_sum, None,
            "a required value is unavailable, so no comparison is possible",
        )
    # A row that recorded no value is deliberately *not* treated as making the
    # comparison unevaluable. `core_metrics` builds the aggregate by summing
    # only the files whose extraction produced components, so a null row
    # contributed nothing to the aggregate either. Both sides skip the same
    # rows, which means a leftover difference cannot be explained by them and is
    # a genuine unexplained residual.
    residual = aggregate_value - contribution_sum
    if residual == 0:
        return ReconciliationResult(
            dimension, RECONCILIATION_EXACT, aggregate_value, contribution_sum, 0,
            (
                f"{rows_without_recorded_value} row(s) recorded no value for this "
                f"component and contributed nothing"
                if rows_without_recorded_value else None
            ),
        )
    return ReconciliationResult(
        dimension, RECONCILIATION_RESIDUAL, aggregate_value, contribution_sum, residual,
        "contribution rows do not account for the aggregate value",
    )


def reconcile_run(
    results: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Reconcile every repository. Returns a report, never raises on mismatch."""
    by_repository: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_repository.setdefault(str(row.get("repository_url") or ""), []).append(row)

    per_repository: dict[str, list[dict[str, Any]]] = {}
    residuals = 0
    not_evaluable = 0
    exact = 0
    for result in results:
        url = str(result.get("repository_url") or "")
        found = reconcile_repository(result, by_repository.get(url, []))
        per_repository[url] = [item.as_dict() for item in found]
        for item in found:
            if item.outcome == RECONCILIATION_RESIDUAL:
                residuals += 1
            elif item.outcome == RECONCILIATION_NOT_EVALUABLE:
                not_evaluable += 1
            else:
                exact += 1

    return {
        "exact_count": exact,
        "residual_count": residuals,
        "not_evaluable_count": not_evaluable,
        "reconciled": residuals == 0,
        "per_repository": per_repository,
    }


def join_to_inventory(
    rows: Sequence[Mapping[str, Any]], inventories: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Check rule 7: every row joins to exactly one inventory record.

    Join key is repository, relative path, and content hash.
    """
    known: set[tuple[str, str, str]] = set()
    for inventory in inventories.values():
        url = str(inventory.get("repository_url") or "")
        for record in inventory.get("files", ()):
            known.add((
                url,
                str(record.get("relative_path") or ""),
                str(record.get("content_hash") or ""),
            ))

    problems: list[str] = []
    for row in rows:
        key = (
            str(row.get("repository_url") or ""),
            str(row.get("relative_path") or ""),
            str(row.get("content_sha256") or ""),
        )
        if known and key not in known:
            problems.append(
                f"contribution row {key[1]!r} in {key[0]} has no matching inventory record"
            )
    return problems
