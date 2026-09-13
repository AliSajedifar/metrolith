"""Complete normalized-input population and its derived artifacts (plan section 11).

``load_repositories_csv`` returns only the rows that will actually execute:
disabled rows are filtered out and identical duplicates are collapsed to a
count. That is correct for driving a run, but it destroys the evidence needed to
answer "what was asked for?" — and once the run finishes, nothing can recover it.

This module keeps the whole accepted population. Every accepted source row is
represented exactly once, including:

* disabled rows;
* identical duplicates dropped from execution, each pointing at its
  representative;
* rows whose acquisition failed, which **retain their requested SHA**.

Conflicting duplicates are deliberately not represented here. They remain fatal
input validation (plan section 11.1): the run never starts, so no ledger exists.

``repositories_frozen.csv`` and ``retry_failed_or_partial.csv`` are derived from
this ledger, so all three artifacts necessarily agree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.repository_input import RepositorySpec

# Column order is fixed by normalized_input_row-1.5.schema.json.
NORMALIZED_INPUT_COLUMNS = (
    "source_input_aggregate_hash",
    "source_input_file_id",
    "source_input_file_sha256",
    "original_row_index",
    "original_input_line",
    "subject_key",
    "canonical_url",
    "architecture_metadata",
    "expected_language",
    "original_enabled",
    "normalized_enabled",
    "requested_sha",
    "resolved_sha",
    "analyzed_sha",
    "commit_verification_state",
    "acquisition_outcome",
    "execution_disposition",
    "measured_scope_inclusion",
    "skip_or_failure_reason",
    "duplicate_classification",
    "duplicate_representative_source_row",
    "normalization_warnings",
    "notes",
)

DUPLICATE_UNIQUE = "unique"
DUPLICATE_IDENTICAL = "identical_duplicate"
DUPLICATE_REPRESENTATIVE = "duplicate_representative"

DISPOSITION_EXECUTED = "executed"
DISPOSITION_SKIPPED_DISABLED = "skipped_disabled"
DISPOSITION_SKIPPED_DUPLICATE = "skipped_duplicate"
DISPOSITION_FAILED_ACQUISITION = "failed_acquisition"
DISPOSITION_NOT_ATTEMPTED = "not_attempted"



def _spec_subject_key(spec) -> str:
    """Logical identity for one input spec, local or remote."""
    from modules.subject import resolve_subject_identity
    from modules.vocabularies import SourceMode

    if getattr(spec, "is_local", False):
        from pathlib import Path as _Path

        return resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT,
            explicit_subject_key=getattr(spec, "subject_key", None),
            repository_url=spec.url or None,
            local_path=_Path(spec.local_path),
        ).subject_key
    return resolve_subject_identity(
        source_mode=SourceMode.REMOTE_GIT_REVISION,
        explicit_subject_key=getattr(spec, "subject_key", None),
        repository_url=spec.url,
    ).subject_key


@dataclass
class NormalizedInputRow:
    """One accepted source row, retained whatever happened to it."""

    source_input_file_id: str
    source_input_file_sha256: str
    original_row_index: int
    original_input_line: int | None
    #: Logical subject identity. The locator below may be absent for a local
    #: subject, so it can never serve as the join key.
    subject_key: str
    canonical_url: str | None
    architecture_metadata: str | None
    expected_language: str | None
    original_enabled: str
    normalized_enabled: bool
    requested_sha: str | None
    duplicate_classification: str = DUPLICATE_UNIQUE
    duplicate_representative_source_row: int | None = None
    notes: str = ""
    normalization_warnings: tuple[str, ...] = ()

    # Filled in after execution. Absent values stay None; none of them may be
    # replaced by a zero, an empty success, or a guessed SHA (plan section 3.7).
    resolved_sha: str | None = None
    analyzed_sha: str | None = None
    commit_verification_state: str = "not_evaluable"
    acquisition_outcome: str = "not_attempted"
    execution_disposition: str = DISPOSITION_NOT_ATTEMPTED
    measured_scope_inclusion: bool = False
    skip_or_failure_reason: str | None = None

    def as_row(self, aggregate_hash: str) -> dict[str, Any]:
        return {
            "source_input_aggregate_hash": aggregate_hash,
            "source_input_file_id": self.source_input_file_id,
            "source_input_file_sha256": self.source_input_file_sha256,
            "original_row_index": self.original_row_index,
            "original_input_line": self.original_input_line,
            "subject_key": self.subject_key,
            "canonical_url": self.canonical_url,
            "architecture_metadata": self.architecture_metadata,
            "expected_language": self.expected_language,
            "original_enabled": self.original_enabled,
            "normalized_enabled": self.normalized_enabled,
            "requested_sha": self.requested_sha,
            "resolved_sha": self.resolved_sha,
            "analyzed_sha": self.analyzed_sha,
            "commit_verification_state": self.commit_verification_state,
            "acquisition_outcome": self.acquisition_outcome,
            "execution_disposition": self.execution_disposition,
            "measured_scope_inclusion": self.measured_scope_inclusion,
            "skip_or_failure_reason": self.skip_or_failure_reason,
            "duplicate_classification": self.duplicate_classification,
            "duplicate_representative_source_row": self.duplicate_representative_source_row,
            "normalization_warnings": list(self.normalization_warnings) or None,
            "notes": self.notes or None,
        }


@dataclass
class NormalizationResult:
    """The complete accepted population plus the subset that will execute."""

    rows: list[NormalizedInputRow] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)

    @property
    def aggregate_hash(self) -> str:
        """One hash over every contributing source file, order-independent."""
        digest = hashlib.sha256()
        for file_id in sorted(self.source_files):
            digest.update(file_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self.source_files[file_id].encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    @property
    def accepted_row_count(self) -> int:
        return len(self.rows)

    @property
    def disabled_row_count(self) -> int:
        return sum(1 for row in self.rows if not row.normalized_enabled)

    @property
    def duplicate_rows_dropped(self) -> int:
        return sum(
            1 for row in self.rows if row.duplicate_classification == DUPLICATE_IDENTICAL
        )

    def executing_rows(self) -> list[NormalizedInputRow]:
        """Rows that drive the run: enabled, and not a dropped duplicate."""
        return [
            row for row in self.rows
            if row.normalized_enabled
            and row.duplicate_classification != DUPLICATE_IDENTICAL
        ]

    def by_url(self, canonical_url: str) -> list[NormalizedInputRow]:
        return [row for row in self.rows if row.canonical_url == canonical_url]

    def as_rows(self) -> list[dict[str, Any]]:
        aggregate = self.aggregate_hash
        return [row.as_row(aggregate) for row in self.rows]


def _file_identity(path: Path) -> tuple[str, str]:
    """Return ``(logical_id, sha256)``.

    The logical id is the file name only. Plan section 11.3 forbids storing an
    absolute source-input path in a portable field, and a run directory is meant
    to be movable between machines.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return Path(path).name, digest.hexdigest()


def normalize_specs(
    specs: Sequence[RepositorySpec], *, source_files: Mapping[str, str] | None = None
) -> NormalizationResult:
    """Build the complete population from already-parsed specs.

    ``specs`` must be every accepted row **before** disabled filtering and
    duplicate collapsing, in source order.
    """
    result = NormalizationResult(source_files=dict(source_files or {}))
    representative_by_url: dict[str, int] = {}

    for index, spec in enumerate(specs):
        key = spec.url.lower()
        file_id = Path(spec.input_file).name if spec.input_file else "<inline>"
        row = NormalizedInputRow(
            source_input_file_id=file_id,
            source_input_file_sha256=result.source_files.get(file_id, "0" * 64),
            original_row_index=index,
            original_input_line=spec.input_line or None,
            subject_key=_spec_subject_key(spec),
            canonical_url=spec.url or None,
            architecture_metadata=spec.architecture_type,
            expected_language=spec.expected_language,
            original_enabled="TRUE" if spec.enabled else "FALSE",
            normalized_enabled=bool(spec.enabled),
            requested_sha=spec.commit_sha,
            notes=spec.notes or "",
        )

        previous = representative_by_url.get(key)
        if previous is None:
            representative_by_url[key] = index
        else:
            row.duplicate_classification = DUPLICATE_IDENTICAL
            row.duplicate_representative_source_row = previous
            row.execution_disposition = DISPOSITION_SKIPPED_DUPLICATE
            row.skip_or_failure_reason = (
                f"identical duplicate of source row {previous}; dropped from execution "
                "and retained here for population completeness"
            )
            for candidate in result.rows:
                if candidate.original_row_index == previous:
                    candidate.duplicate_classification = DUPLICATE_REPRESENTATIVE
                    break

        if not row.normalized_enabled and row.duplicate_classification != DUPLICATE_IDENTICAL:
            row.execution_disposition = DISPOSITION_SKIPPED_DISABLED
            row.skip_or_failure_reason = "row is disabled in the source input"

        result.rows.append(row)

    return result


def normalize_input_files(paths: Iterable[str | Path]) -> NormalizationResult:
    """Parse every input file and build the complete accepted population.

    Conflicting duplicates raise :class:`InputValidationError` exactly as before;
    they are fatal input validation and no ledger is produced.
    """
    from modules.repository_input import load_repositories_csv, parse_repository_rows

    ordered = [Path(item) for item in paths]
    source_files = dict(_file_identity(path) for path in ordered)

    specs: list[RepositorySpec] = []
    for path in ordered:
        # The conflicting-duplicate check lives in load_repositories_csv and
        # must still fire: a conflicting duplicate is fatal input validation and
        # no ledger may be produced for it (plan section 11.1).
        load_repositories_csv(path, include_disabled=True)
        specs.extend(parse_repository_rows(path))

    return normalize_specs(specs, source_files=source_files)


def apply_results(
    result: NormalizationResult, repository_results: Sequence[Mapping[str, Any]]
) -> None:
    """Fold executed repository outcomes back into the ledger."""
    by_url = {
        str(item.get("repository_url")): item
        for item in repository_results
        if item.get("repository_url")
    }
    for row in result.rows:
        outcome = by_url.get(row.canonical_url)
        if outcome is None:
            continue
        if row.duplicate_classification == DUPLICATE_IDENTICAL:
            continue

        acquisition = outcome.get("acquisition") or {}
        analyzed = acquisition.get("analyzed_commit_sha")
        row.resolved_sha = acquisition.get("resolved_commit_sha") or analyzed
        row.analyzed_sha = analyzed
        row.commit_verification_state = str(
            acquisition.get("commit_verification_status") or "not_evaluable"
        )
        # The requested SHA is never overwritten by what was resolved; it is
        # what the input asked for and must survive a failed acquisition.
        if row.requested_sha is None:
            row.requested_sha = outcome.get("requested_commit_sha")

        status = outcome.get("analysis_status")
        if status == "failed" and not analyzed:
            row.acquisition_outcome = "failed"
            row.execution_disposition = DISPOSITION_FAILED_ACQUISITION
            row.measured_scope_inclusion = False
            row.skip_or_failure_reason = str(outcome.get("notes") or "acquisition failed")
        else:
            row.acquisition_outcome = "succeeded"
            row.execution_disposition = DISPOSITION_EXECUTED
            row.measured_scope_inclusion = True


def derive_frozen_rows(result: NormalizationResult) -> list[dict[str, Any]]:
    """Derive ``repositories_frozen.csv`` rows from the ledger.

    Limited to verified analyzed SHAs (decision D-2): a frozen input is a
    promise that re-running it reproduces the same bytes, which an unverified or
    absent SHA cannot make.
    """
    rows = []
    for row in result.rows:
        if row.duplicate_classification == DUPLICATE_IDENTICAL:
            continue
        if not row.analyzed_sha or row.commit_verification_state != "verified":
            continue
        rows.append({
            "url": row.canonical_url,
            "architecture_type": row.architecture_metadata or "unknown",
            "expected_language": row.expected_language or "",
            "commit_sha": row.analyzed_sha,
            "enabled": "TRUE",
            "notes": row.notes,
        })
    return rows


def derive_retry_rows(result: NormalizationResult) -> list[dict[str, Any]]:
    """Derive ``retry_failed_or_partial.csv`` rows from the ledger.

    **Artifact Schema 1.5 behaviour change (finding F-5, decision D-2).** A retry
    row now carries the analyzed SHA when one exists and otherwise the
    *requested* SHA. Artifact <= 1.4 emitted an empty ``commit_sha`` whenever
    acquisition failed, which is precisely the case a retry file exists to
    serve — the 7ep control lost its requested SHA and the retry artifact could
    not reproduce the attempt.
    """
    rows = []
    for row in result.rows:
        if row.duplicate_classification == DUPLICATE_IDENTICAL:
            continue
        if not row.normalized_enabled:
            continue
        if row.execution_disposition not in {
            DISPOSITION_EXECUTED, DISPOSITION_FAILED_ACQUISITION
        }:
            continue
        rows.append({
            "url": row.canonical_url,
            "architecture_type": row.architecture_metadata or "unknown",
            "expected_language": row.expected_language or "",
            "commit_sha": row.analyzed_sha or row.requested_sha or "",
            "enabled": "TRUE",
            "notes": row.notes,
        })
    return rows


def reconcile_counts(result: NormalizationResult, manifest: Mapping[str, Any]) -> list[str]:
    """Check the ledger against the manifest counts. Returns disagreements."""
    problems: list[str] = []
    expected = {
        "input_row_count": result.accepted_row_count,
        "skipped_disabled_count": result.disabled_row_count,
        "duplicate_rows_dropped": result.duplicate_rows_dropped,
    }
    for name, value in expected.items():
        declared = manifest.get(name)
        if declared is not None and declared != value:
            problems.append(
                f"{name}: manifest records {declared}, normalized_input.csv contains {value}"
            )
    return problems
