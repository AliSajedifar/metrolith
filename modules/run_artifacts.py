"""Immutable run directories, atomic artifact writes, and structured logging."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.subject import subject_key_of
from modules.config import ARTIFACT_SCHEMA_VERSION, PROGRAM_VERSION


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _replace_with_retry(source: Path, target: Path, attempts: int = 6) -> None:
    error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05 * (attempt + 1))
    raise error or OSError(f"Could not replace {target}")


def atomic_write_text(path: str | Path, value: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def atomic_write_json(path: str | Path, value: Any) -> Path:
    return atomic_write_text(
        path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )


def atomic_write_csv(
    path: str | Path,
    fieldnames: list[str],
    rows: Iterable[dict[str, Any]],
    *,
    required_fields: Iterable[str] = (),
) -> Path:
    path = Path(path)
    required = tuple(required_fields)
    absent_from_schema = sorted(set(required) - set(fieldnames))
    if absent_from_schema:
        raise ValueError(
            f"Required CSV fields are absent from {path.name}: "
            + ", ".join(absent_from_schema)
        )
    materialized = list(rows)
    for index, row in enumerate(materialized, start=1):
        missing = sorted(field for field in required if field not in row)
        if missing:
            raise ValueError(
                f"{path.name} row {index} is missing required fields: "
                + ", ".join(missing)
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in row.items()
                }
                for row in materialized
            )
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


class StructuredRunLogger:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        severity: str,
        module: str,
        message: str,
        repository_url: str | None = None,
        commit_sha: str | None = None,
        error_type: str | None = None,
        exception_details: str | None = None,
    ) -> None:
        event = {
            "product_name": "Metrolith",
            "program_version": PROGRAM_VERSION,
            "timestamp": utc_now(),
            "run_id": self.run_id,
            "repository_url": repository_url,
            "commit_sha": commit_sha,
            "module": module,
            "severity": severity.upper(),
            "error_type": error_type,
            "message": message,
            "exception_details": exception_details,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()


def artifact_slug(result: Mapping[str, Any]) -> str:
    """Portable filename stem shared by every per-subject artifact family."""

    owner = result.get("repository_owner") or "unknown"
    name = result.get("repository_name") or uuid.uuid4().hex[:8]
    safe = lambda value: "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value))
    return f"{safe(owner)}__{safe(name)}"


_slug = artifact_slug


def _run_repository_slug(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value.replace("/", "-")
    )
    normalized = "-".join(part for part in normalized.split("-") if part)
    return (normalized or "repository")[:80]


def format_markdown_value(value: Any) -> Any:
    """Render null as N/A without conflating it with a verified zero."""
    return "N/A" if value is None else value


def render_fact_sheet(result: dict[str, Any]) -> str:
    aggregate = result.get("metrics", {}).get("aggregate", {})
    acquisition = result.get("acquisition", {})
    exclusions = (
        result.get("inventory_summary", {}).get("files_excluded_by_reason", {})
    )
    lines = [
        f"# {result.get('repository_owner', '')}/{result.get('repository_name', '')}",
        "",
        f"- Generated by: Metrolith {result.get('program_version', PROGRAM_VERSION)}",
        f"- Repository: {result.get('repository_url', '')}",
        f"- Architecture type (supplied): {result.get('architecture_type', '')}",
        f"- Analyzed commit: {format_markdown_value(acquisition.get('analyzed_commit_sha'))}",
        f"- Acquisition mode: {format_markdown_value(acquisition.get('acquisition_mode'))}",
        f"- Execution mode: {format_markdown_value(result.get('execution_mode'))}",
        f"- Metric contract: {format_markdown_value(result.get('metric_contract_version'))}",
        f"- Exclusion policy: {format_markdown_value(result.get('exclusion_policy_version'))}",
        f"- Inventory schema: {format_markdown_value(result.get('inventory_schema_version'))}",
        f"- Artifact schema: {format_markdown_value(result.get('artifact_schema_version'))}",
        f"- Primary language: {result.get('metrics', {}).get('primary_language_name') or 'N/A'}",
        f"- Expected language family status: {result.get('expected_language_family_status', 'not_applicable')}",
        f"- Partial origin: {result.get('partial_origin', 'none')}",
        f"- Expected-language mismatch: {result.get('metrics', {}).get('expected_language_mismatch', False)}",
        f"- Git mode map: {result.get('git_mode_map_status', 'N/A')}",
        f"- Git mode entries: {format_markdown_value(result.get('git_mode_entry_count'))}",
        f"- Metric status: {aggregate.get('metric_status', 'failed')}",
        "",
        "## Core metrics",
        "",
        "| Metric | Value | Status |",
        "|---|---:|---|",
        f"| Lines of Code | {format_markdown_value(aggregate.get('lines_of_code'))} | {aggregate.get('loc_status', 'failed')} |",
        f"| Source Files | {format_markdown_value(aggregate.get('source_files'))} | {aggregate.get('source_files_status', 'failed')} |",
        f"| Classes / Structs | {format_markdown_value(aggregate.get('classes_structs'))} | {aggregate.get('classes_structs_status', 'failed')} |",
        f"| Methods / Functions | {format_markdown_value(aggregate.get('methods_functions'))} | {aggregate.get('methods_functions_status', 'failed')} |",
        "",
        f"- Inventory status: {aggregate.get('inventory_status', 'failed')}",
        f"- Source files readable: {format_markdown_value(aggregate.get('source_files_readable'))}",
        f"- Source files LOC-analyzed: {format_markdown_value(aggregate.get('source_files_loc_analyzed'))}",
        f"- Source files entity-parsed: {format_markdown_value(aggregate.get('source_files_entity_parsed'))}",
        "",
        "Lines of Code is physical code lines excluding blank and comment-only lines.",
        "Architecture type is a benchmark input label, not an inferred scientific classification.",
    ]
    if exclusions:
        lines.extend(["", "## Source-selection exclusions", ""])
        lines.extend(
            f"- {category}: {count}"
            for category, count in sorted(exclusions.items())
        )
    if result.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(
            f"- [{error.get('error_type', 'unknown')}] "
            f"{error.get('file_path') or error.get('module')}: {error.get('message')}"
            for error in result["errors"]
        )
    return "\n".join(lines) + "\n"


def render_run_summary(
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    run_dir: Path,
) -> str:
    """Render a compact, deterministic human-readable run index."""
    ordered = sorted(results, key=lambda item: subject_key_of(item).casefold())
    start = manifest.get("start_timestamp")
    end = manifest.get("end_timestamp")
    duration = None
    if start and end:
        try:
            duration = round(
                (
                    datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                ).total_seconds(),
                3,
            )
        except ValueError:
            duration = None
    resolved = manifest.get("resolved_paths", {})
    lines = [
        "# Metrolith run summary",
        "",
        f"- Run ID: {manifest.get('run_id', 'N/A')}",
        f"- Metrolith version: {manifest.get('program_version', 'N/A')}",
        f"- Metrolith Git commit/tag: {manifest.get('profiler_git_commit_sha') or 'N/A'}",
        f"- Metrolith Git dirty: {format_markdown_value(manifest.get('profiler_git_dirty'))}",
        f"- Metric Contract: {manifest.get('metric_contract_version', 'N/A')}",
        f"- Exclusion Policy: {manifest.get('exclusion_policy_version', 'N/A')}",
        f"- Inventory Schema: {manifest.get('inventory_schema_version', 'N/A')}",
        f"- Artifact Schema: {manifest.get('artifact_schema_version', 'N/A')}",
        f"- Acquisition mode: {manifest.get('acquisition_mode', 'N/A')}",
        f"- Workspace: {resolved.get('workspace_root', 'N/A')}",
        f"- Cache: {resolved.get('cache_root', 'N/A')}",
        f"- Worktrees: {resolved.get('temporary_directory', 'N/A')}",
        f"- Output root: {resolved.get('output_root', 'N/A')}",
        f"- Started: {start or 'N/A'}",
        f"- Completed: {end or 'N/A'}",
        f"- Duration seconds: {format_markdown_value(duration)}",
        "",
        "## Counts",
        "",
        f"- Input rows: {manifest.get('input_row_count', manifest.get('repository_count', 0))}",
        f"- Enabled/planned: {manifest.get('planned_repository_count', manifest.get('repository_count', 0))}",
        f"- Processed: {manifest.get('processed_repository_count', 0)}",
        f"- Complete: {manifest.get('success_count', 0)}",
        f"- Partial: {manifest.get('partial_count', 0)}",
        f"- Failed: {manifest.get('failure_count', 0)}",
        f"- Skipped/disabled: {manifest.get('skipped_disabled_count', 0)}",
        f"- Duplicate rows dropped: {manifest.get('duplicate_rows_dropped', 0)}",
        "",
        "## Git mode-map acquisition",
        "",
    ]
    for item in manifest.get("git_mode_maps", []):
        lines.append(
            f"- {item.get('repository_url')}: {item.get('status')} "
            f"({item.get('entry_count', 0)} entries, "
            f"{format_markdown_value(item.get('command_duration_seconds'))} s)"
        )
    if not manifest.get("git_mode_maps"):
        lines.append("- No repository mode-map results recorded")
    lines.extend([
        "",
        "## Repository results",
        "",
        "| Repository | Status | Expected language family status | Partial origin | LOC | Source Files | Classes / Structs | Methods / Functions |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ])
    for result in ordered:
        aggregate = result.get("metrics", {}).get("aggregate", {})
        lines.append(
            "| {url} | {status} | {expected_status} | {partial_origin} | {loc} | {files} | {classes} | {methods} |".format(
                url=result.get("repository_url", "N/A"),
                status=result.get("analysis_status", aggregate.get("metric_status", "failed")),
                expected_status=result.get(
                    "expected_language_family_status", "not_applicable"
                ),
                partial_origin=result.get("partial_origin", "none"),
                loc=format_markdown_value(aggregate.get("lines_of_code")),
                files=format_markdown_value(aggregate.get("source_files")),
                classes=format_markdown_value(aggregate.get("classes_structs")),
                methods=format_markdown_value(aggregate.get("methods_functions")),
            )
        )
    exceptional = [
        result for result in ordered
        if result.get("analysis_status") in {"partial", "failed"}
        or result.get("metrics", {}).get("aggregate", {}).get("metric_status")
        in {"partial", "failed"}
    ]
    lines.extend(["", "## Partial and failed repositories", ""])
    lines.extend(
        f"- {result.get('repository_url')}: {result.get('analysis_status')}"
        for result in exceptional
    )
    if not exceptional:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Key artifacts",
            "",
            f"- Sheet metrics: {run_dir / 'sheet_metrics.csv'}",
            f"- Errors: {run_dir / 'errors.csv'}",
            f"- Frozen input: {run_dir / 'repositories_frozen.csv'}",
            f"- Retry input: {run_dir / 'retry_failed_or_partial.csv'}",
        ]
    )
    return "\n".join(lines) + "\n"


class RunArtifacts:
    def __init__(
        self,
        output_root: str | Path,
        start_timestamp: str,
        *,
        planned_repository_count: int = 0,
        repository_slug: str | None = None,
    ):
        self.output_root = Path(output_root)
        self.run_id = uuid.uuid4().hex[:12]
        timestamp = datetime.fromisoformat(
            start_timestamp.replace("Z", "+00:00")
        ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        count_width = max(3, len(str(max(0, planned_repository_count))))
        count_label = f"n{planned_repository_count:0{count_width}d}"
        subject = f"_{_run_repository_slug(repository_slug)}" if repository_slug else ""
        self.run_dir = (
            self.output_root / "runs" / f"{timestamp}_{count_label}{subject}_{self.run_id}"
        )
        if self.run_dir.exists():
            raise FileExistsError(f"Run directory already exists: {self.run_dir}")
        for name in ("repositories", "file_inventory", "fact_sheets", "logs"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=False)
        self.logger = StructuredRunLogger(self.run_dir / "logs" / "run.jsonl", self.run_id)
        self._checkpoint_lock = threading.Lock()
        atomic_write_json(
            self.run_dir / "run_status.json",
            {"run_id": self.run_id, "status": "running", "updated_at": utc_now()},
        )

    def _self_validation_problems(
        self,
        candidate_status_path: Path | None = None,
        candidate_manifest_path: Path | None = None,
    ) -> list[str]:
        """Read the run back through the strict reader; return what it rejects.

        The producer and the consumer of Metrolith artifacts are both in this
        repository, and nothing used to check them against each other. A column
        the writer emits in a form the reader's contract forbids therefore
        surfaced only downstream, in ``explain``/``report``/``reproduce``/
        ``compare``, long after the run was recorded as ``completed``.

        Scope:

        * **Structural admission and decoding only.** This asks whether the
          artifacts parse and satisfy their declared contracts. It never
          recomputes a metric, a status, or any measurement conclusion.
        * **Lifecycle is not consulted.** ``run_status.json`` still holds the
          initial ``running`` status while this runs — that is the whole point
          of the commit protocol — so any lifecycle verdict would be
          meaningless. Only ``structural_errors``, the declared-version
          ``compatibility``, and the candidate status bytes are read.
        * **Every artifact is forced, including the lazy ones.** This reverses
          an earlier decision to touch only the reader's eager set. Laziness
          meant an invalid ``catalog.csv``, ``errors.csv``, ``recoveries.csv``,
          inventory or contribution ledger escaped the gate purely because
          nothing had read it yet — the artifact was broken, the run was
          finalized ``completed``, and the failure surfaced in whichever
          downstream command opened it first. A gate that only inspects what is
          convenient does not establish readability. Finalization therefore now
          costs a second full pass over the run.

        ``candidate_status_path`` is the staged ``run_status.json`` that has not
        yet been committed. Its **bytes are decoded from disk and validated**
        against the ``run_status`` schema, so the check covers exactly what the
        commit will publish rather than the in-memory dictionary it came from.

        Returns a list of human-readable problems; empty means the run is
        readable. This never raises: a gate that crashed the run it was meant to
        protect would be worse than the defect it guards against.
        """
        try:
            from validation.artifact_io.compatibility import CompatibilityState
            from validation.artifact_io.reader import ImmutableRunView
            from validation.artifact_io.schema_store import validate_document

            view = ImmutableRunView(self.run_dir)

            # Force every optional and expensive projection. Each is a
            # `cached_property` that records its own faults when read, so an
            # artifact that is never touched can never be found broken.
            view.materialize_diagnostics()
            for dimension in (
                "catalog", "errors", "recoveries", "inventories",
                "normalized_input", "contribution_container", "repository_documents",
                "sheet_metrics", "language_metrics", "repositories",
                # Artifact Schema 1.11. Both are conditional, and both are forced
                # for the reason the whole list is forced: an artifact nothing
                # reads is an artifact nothing can find broken.
                "benchmark_qualification", "repository_level_metrics",
            ):
                try:
                    getattr(view, dimension)
                except Exception as exc:  # a dimension that cannot be read is a problem
                    return [
                        f"self-validation could not read {dimension}: "
                        f"{type(exc).__name__}: {exc}"
                    ]

            # The ledger rows are streamed rather than exposed as a property, so
            # they have to be drained to be checked at all. Rows are discarded as
            # they arrive: the point is to decode every one, not to hold them.
            try:
                for _ in view.stream_contributions():
                    pass
            except Exception as exc:
                return [
                    f"self-validation could not read contributions.csv: "
                    f"{type(exc).__name__}: {exc}"
                ]

            problems = [
                f"{error.artifact}: {error.message}" for error in view.structural_errors
            ]
            if view.compatibility.state is not CompatibilityState.SUPPORTED:
                problems.append(
                    f"declared artifact_schema_version is "
                    f"{view.compatibility.state.value}: {view.compatibility.reason}"
                )

            problems.extend(
                self._terminal_candidate_problems(
                    candidate_status_path, candidate_manifest_path
                )
            )
            return problems
        except Exception as exc:  # pragma: no cover - defensive
            # Inability to run the check is itself a failure to demonstrate
            # readability, and must not be reported as a pass.
            return [f"self-validation could not run: {type(exc).__name__}: {exc}"]

    @staticmethod
    def _terminal_candidate_problems(
        status_path: Path | None, manifest_path: Path | None
    ) -> list[str]:
        """Validate staged terminal documents by decoding their bytes on disk.

        Reading the files back rather than validating the dictionaries that
        produced them is the point: serialization is where a value that was
        fine in memory becomes one the contract forbids.

        Both terminal documents are held to this standard, on the success path
        and the failure path alike, so that *every* published terminal state has
        had its exact bytes validated.
        """
        from validation.artifact_io import known_exceptions
        from validation.artifact_io.schema_store import validate_document

        problems: list[str] = []
        for path, schema_name, label in (
            (manifest_path, "run_manifest", "run_manifest.json"),
            (status_path, "run_status", "run_status.json"),
        ):
            if path is None:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"candidate {label} is unreadable: {exc}")
                continue
            try:
                document = json.loads(raw)
            except json.JSONDecodeError as exc:
                problems.append(f"candidate {label} is not valid JSON: {exc}")
                continue
            # Known 1.5.0 schema defects are waived through the one shared
            # registry that `metrolith validate` also consults, so the two can
            # never reach opposite verdicts about the same artifact.
            real, _accepted = known_exceptions.partition(
                schema_name,
                validate_document(schema_name, document, label),
                document,
                declared_artifact_schema=ARTIFACT_SCHEMA_VERSION,
            )
            problems.extend(f"candidate {label}: {error.message}" for error in real)
        return problems

    def _complexity_completeness_problems(
        self, status_candidate: Path | None
    ) -> list[str]:
        """Artifact Schema 1.9 completeness, checked against EXACT bytes on disk.

        Deliberately NOT part of `_self_validation_problems`, whose documented
        scope is structural admission and decoding only and which must never
        recompute a measurement conclusion. This is a semantic cross-file
        invariant and needs its own layer.

        Everything it reads is decoded from disk: the staged candidate
        `run_status.json.candidate` bytes, and the persisted `analysis.json` and
        callables artifacts through the strict reader. It never consults the
        in-memory results that produced them, because serialization is exactly
        where a value that was fine in memory becomes one the contract forbids.

        The rule:

        * a repository declaring `complete` or `partial` complexity MUST have the
          callable artifact, and its row count must reconcile;
        * absence is permitted only when every repository is `failed` or
          `not_applicable` AND each carries a typed `unavailable_reason`.
        """
        import json as _json

        from validation.artifact_io.reader import open_run

        problems: list[str] = []
        try:
            view = open_run(self.run_dir)
        except Exception as exc:  # pragma: no cover - reader failures surface above
            return [f"complexity completeness could not open the run: {exc}"]

        # Applies to 1.9 AND EVERY LATER GENERATION. A `startswith("1.9")` test
        # silently disabled this entire layer the moment the native schema
        # became 1.10 -- the check returned no problems for any run, which reads
        # exactly like a clean result. Caught by the C3 mutation tests, which is
        # what they are for.
        if _generation_of(view.declared_artifact_schema) < (1, 9):
            return []

        repositories = list(view.repositories)
        evaluable: list[Mapping[str, Any]] = []
        for repository in repositories:
            complexity = repository_complexity(dict(repository))
            subject = repository.get("subject_key") or repository.get("repository_url")
            if complexity is None:
                problems.append(
                    f"{subject}: a native 1.9 repository document carries no "
                    f"complexity summary"
                )
                continue
            status = complexity.get("status")
            if status in {"complete", "partial"}:
                evaluable.append(repository)
            elif complexity.get("unavailable_reason") in (None, ""):
                problems.append(
                    f"{subject}: complexity status {status!r} without a typed "
                    f"unavailable_reason; absence must always be explained"
                )

        present = view.has_callable_artifact
        if evaluable and not present:
            names = ", ".join(
                str(item.get("subject_key")) for item in evaluable[:5]
            )
            problems.append(
                f"complexity is complete/partial for {len(evaluable)} repository/ies "
                f"({names}) but no callable artifact was published"
            )
        if evaluable and present:
            declared_rows = sum(
                int((repository_complexity(dict(item)) or {})
                    .get("aggregate", {}).get("callable_count") or 0)
                for item in evaluable
            )
            try:
                written_rows = sum(1 for _ in view.stream_callables())
            except Exception as exc:
                problems.append(f"callable artifact is unreadable: {exc}")
                written_rows = None
            if written_rows is not None and written_rows != declared_rows:
                problems.append(
                    f"callable artifact holds {written_rows} row(s) but the "
                    f"repository summaries declare {declared_rows}"
                )

        # The staged terminal status must agree with what was actually measured.
        if status_candidate is not None and status_candidate.is_file():
            try:
                staged = _json.loads(status_candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                problems.append(f"candidate run_status.json is unreadable: {exc}")
            else:
                state = staged.get("complexity_measurement_state")
                if state is None:
                    problems.append(
                        "terminal run_status.json declares no "
                        "complexity_measurement_state"
                    )
                elif (state == "measured") != bool(evaluable):
                    problems.append(
                        f"terminal run_status.json declares "
                        f"complexity_measurement_state={state!r} while "
                        f"{len(evaluable)} repository/ies report an evaluable state"
                    )

                # Complexity Contract 2.0.0 section 12.9. A run claiming
                # cognitive measurement must carry the artifact that holds it,
                # and every row in that artifact must obey the never-zero rule.
                # Read from the STAGED AND WRITTEN BYTES through the strict
                # reader, never from producer memory.
                cognitive = staged.get("cognitive_measurement_state")
                if _generation_of(view.declared_artifact_schema) < (1, 10):
                    # A pre-1.10 run has no cognitive state and owes none:
                    # absence is the fact that marks it, not an omission.
                    pass
                elif cognitive is None:
                    problems.append(
                        "terminal run_status.json declares no "
                        "cognitive_measurement_state; a native 1.10 run must "
                        "say whether cognitive complexity was measured, and "
                        "absence means only that a run predates 1.10"
                    )
                elif cognitive == "measured":
                    problems.extend(
                        self._cognitive_completeness_problems(cognitive)
                    )
        return problems

    @staticmethod
    def _generation(declared: Any) -> tuple[int, int]:
        return _generation_of(declared)

    def _cognitive_completeness_problems(self, state: str) -> list[str]:
        """Bytes on disk must support a claimed cognitive measurement.

        Separate from `_complexity_completeness_problems` for the same reason
        that one is separate from `_self_validation_problems`: each layer has a
        stated scope, and widening one to carry another's obligation is how a
        check stops being reviewable.
        """
        problems: list[str] = []
        try:
            from validation.artifact_io.reader import open_run

            view = open_run(self.run_dir)
        except Exception as exc:  # pragma: no cover - defensive
            return [f"cognitive completeness could not read the run: {exc}"]

        if not view.has_callable_artifact:
            return [
                f"run_status.json declares cognitive_measurement_state="
                f"{state!r} but no callable artifact was written; a claimed "
                f"measurement must be carried by the bytes that hold it"
            ]

        # A measured scope holding ZERO callables is a verified zero, not a
        # missing measurement: a run over sources that genuinely contain no
        # callable measured complexity and found none. C3 built that
        # distinction and reconciles the declared count against the written
        # one; re-deriving it here as "no rows means not measured" contradicted
        # it and failed a legitimate run.
        for row in view.stream_callables():
            status = row.get("structural_complexity_status")
            value = row.get("cognitive_complexity")
            if "cognitive_complexity" not in row:
                problems.append(
                    "a persisted callable row carries no cognitive_complexity "
                    "column while the run claims cognitive measurement"
                )
                break
            if status == "failed" and value not in (None, ""):
                problems.append(
                    f"callable row {row.get('callable_row_id')!r} reports "
                    f"cognitive_complexity={value!r} under a failed status; an "
                    f"unavailable measurement is NULL and never 0"
                )
            if status == "complete" and value in (None, ""):
                problems.append(
                    f"callable row {row.get('callable_row_id')!r} reports no "
                    f"cognitive_complexity under a complete status; 0 is a "
                    f"measured value and must be written as one"
                )
        return problems

    def checkpoint_repository(self, result: dict[str, Any], stage: str) -> Path:
        """Atomically persist one repository without weakening an existing core checkpoint."""
        path = self.run_dir / "repositories" / f"{_slug(result)}.json"
        snapshot = dict(result)
        snapshot["checkpoint_status"] = stage
        snapshot["checkpoint_updated_at"] = utc_now()
        with self._checkpoint_lock:
            if path.exists() and stage == "incomplete":
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    existing = {}
                if existing.get("checkpoint_status") in {
                    "core_complete",
                    "metrics_complete",
                    "full_complete",
                    "complete",
                }:
                    return path
            return atomic_write_json(path, snapshot)

    def _write_qualification_artifact(
        self, records: list[dict[str, Any]], registry: Any, ordered: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
        """Candidate-write, strict-read, reconcile and hash the qualification artifact.

        Returns ``(artifact, manifest_binding, problems)``. The sequence is the
        point, and it is the same discipline the terminal manifest/status use:

        1. serialize to a candidate path beside the final one;
        2. **decode the candidate BYTES back from disk** and validate them
           against the qualification schema — serialization is where a value
           that was fine in memory becomes one the contract forbids;
        3. semantically reconcile every record against `analysis.json`, by
           re-deriving each derived field rather than trusting what was written;
        4. hash and size the validated candidate bytes;
        5. only then move the candidate to its final run-bundle path.

        A failure at any step leaves no artifact at the final path and returns
        problems, so no terminal status can ever name a qualification artifact
        that was not read back and reconciled.
        """
        from modules.benchmark_qualification import (
            QUALIFICATION_ARTIFACT_PATH,
            QUALIFICATION_SCHEMA_VERSION,
            build_artifact,
            reconcile,
        )
        from validation.artifact_io.schema_store import validate_document

        final_path = self.run_dir / QUALIFICATION_ARTIFACT_PATH
        candidate = self.run_dir / f"{QUALIFICATION_ARTIFACT_PATH}.candidate"
        artifact = build_artifact(records, registry)

        try:
            atomic_write_json(candidate, artifact)
        except Exception as exc:
            return None, None, [
                f"qualification artifact could not be staged: "
                f"{type(exc).__name__}: {exc}"
            ]

        try:
            raw = candidate.read_bytes()
            decoded = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            return None, None, [
                f"staged qualification artifact is unreadable: "
                f"{type(exc).__name__}: {exc}"
            ]

        violations = validate_document(
            "benchmark_qualification", decoded, QUALIFICATION_ARTIFACT_PATH
        )
        if violations:
            candidate.unlink(missing_ok=True)
            return None, None, [
                f"qualification artifact violates its schema: {error.message}"
                for error in violations[:5]
            ]

        problems = reconcile(decoded, ordered)
        if problems:
            candidate.unlink(missing_ok=True)
            return None, None, [
                f"qualification does not reconcile with analysis.json: {problem}"
                for problem in problems[:5]
            ]

        binding = {
            "path": QUALIFICATION_ARTIFACT_PATH,
            "qualification_schema_version": QUALIFICATION_SCHEMA_VERSION,
            "qualification_profile": str(decoded["qualification_profile"]),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "record_count": len(decoded["records"]),
        }

        try:
            _replace_with_retry(candidate, final_path)
        except OSError as exc:
            candidate.unlink(missing_ok=True)
            return None, None, [
                f"qualification artifact commit failed: {type(exc).__name__}: {exc}"
            ]
        return decoded, binding, []

    def _qualification_presence_problems(self, mode: str) -> list[str]:
        """Conditional mandatory presence, checked against the filesystem.

        Both directions are enforced. A benchmark-qualified run that published
        no qualification artifact is a run-integrity failure, not an implicit
        unqualified state. A generic run that published one is equally wrong:
        it would present a qualification authority the run never established,
        and a downstream reader has no way to tell that from a real one.
        """
        from modules.benchmark_qualification import (
            MODE_BENCHMARK_QUALIFIED,
            QUALIFICATION_ARTIFACT_PATH,
        )

        problems: list[str] = []
        present = (self.run_dir / QUALIFICATION_ARTIFACT_PATH).is_file()
        projection = (self.run_dir / "repository_level_metrics.csv").is_file()

        if mode == MODE_BENCHMARK_QUALIFIED:
            if not present:
                problems.append(
                    f"{QUALIFICATION_ARTIFACT_PATH} is mandatory in "
                    f"benchmark-qualified mode and is absent"
                )
            if not projection:
                problems.append(
                    "repository_level_metrics.csv is mandatory in "
                    "benchmark-qualified mode and is absent"
                )
        else:
            if present:
                problems.append(
                    f"{QUALIFICATION_ARTIFACT_PATH} is present in a run that "
                    f"declares qualification_mode=not_requested"
                )
            if projection:
                problems.append(
                    "repository_level_metrics.csv is present in a run that "
                    "declares qualification_mode=not_requested"
                )
        # A candidate left behind means a staging step died midway; the run must
        # not finalize while an unvalidated candidate sits beside the real path.
        if (self.run_dir / f"{QUALIFICATION_ARTIFACT_PATH}.candidate").exists():
            problems.append(
                f"an unvalidated {QUALIFICATION_ARTIFACT_PATH}.candidate remains "
                f"in the run directory"
            )
        return problems

    def _projection_problems(
        self, records: list[dict[str, Any]], mode: str
    ) -> list[str]:
        """Reconcile the written CSV projections against the qualification records.

        Reads the CSV BYTES back from disk rather than inspecting the row dicts
        that produced them, for the same reason the terminal candidates are
        re-decoded: a value that was correct in memory can still be serialized
        into a cell that says something else.

        Checks the two properties that make `repository_level_metrics.csv`
        trustworthy: it holds exactly the derived-eligible subjects, and every
        `sheet_metrics.csv` eligibility cell agrees with the authority.
        """
        from modules.benchmark_qualification import MODE_BENCHMARK_QUALIFIED

        if mode != MODE_BENCHMARK_QUALIFIED:
            return []

        problems: list[str] = []
        expected = {
            str(record["binding"]["subject_key"])
            for record in records
            if record["repository_level_comparison_eligible"]
        }

        def _subjects(name: str) -> tuple[set[str], list[dict[str, str]]] | None:
            path = self.run_dir / name
            if not path.is_file():
                problems.append(f"{name} is absent in benchmark-qualified mode")
                return None
            try:
                with path.open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except OSError as exc:
                problems.append(f"{name} is unreadable: {exc}")
                return None
            return {str(row.get("subject_key") or "") for row in rows}, rows

        projected = _subjects("repository_level_metrics.csv")
        if projected is not None:
            subjects, rows = projected
            if len(rows) != len(expected):
                problems.append(
                    f"repository_level_metrics.csv holds {len(rows)} row(s) but "
                    f"{len(expected)} record(s) are derived-eligible"
                )
            for extra in sorted(subjects - expected):
                problems.append(
                    f"repository_level_metrics.csv contains {extra}, which is not "
                    f"derived-eligible"
                )
            for missing in sorted(expected - subjects):
                problems.append(
                    f"repository_level_metrics.csv omits derived-eligible {missing}"
                )
            for row in rows:
                if row.get("repository_level_comparison_eligible") not in {
                    "True", "true", "TRUE"
                }:
                    problems.append(
                        f"repository_level_metrics.csv row "
                        f"{row.get('subject_key')!r} does not carry eligibility true"
                    )

        sheet = _subjects("sheet_metrics.csv")
        if sheet is not None:
            _subjects_unused, rows = sheet
            eligible_cells = {
                str(row.get("subject_key") or "")
                for row in rows
                if row.get("repository_level_comparison_eligible")
                in {"True", "true", "TRUE"}
            }
            if eligible_cells != expected:
                problems.append(
                    "sheet_metrics.csv eligibility cells do not match the "
                    "qualification authority"
                )
        return problems

    def finalize(
        self,
        manifest: dict[str, Any],
        results: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        inventories: dict[str, dict[str, Any]],
        normalization: Any = None,
        qualification_registry: Any = None,
    ) -> str:
        ordered = sorted(results, key=lambda item: subject_key_of(item).casefold())

        # ---- Benchmark qualification (Artifact Schema 1.11) -----------------
        # Built here, from the registry plus READ-ONLY measurement status and
        # identity fields, so the checked projections below can carry it. It
        # cannot reach file discovery, parsing, metric calculation, callable
        # calculation or aggregation: all of those finished before `finalize`
        # was called, and nothing here writes back into `ordered`.
        from modules.benchmark_qualification import (
            LANGUAGE_QUALIFICATION_COLUMNS,
            MODE_BENCHMARK_QUALIFIED,
            MODE_NOT_REQUESTED,
            SHEET_QUALIFICATION_COLUMNS,
            build_records,
            evaluate_readiness,
            qualified_cells,
            subject_of,
            unqualified_cells,
        )

        qualification_mode = (
            MODE_BENCHMARK_QUALIFIED
            if qualification_registry is not None
            else MODE_NOT_REQUESTED
        )
        qualification_records: list[dict[str, Any]] = []
        qualification_problems: list[str] = []
        if qualification_registry is not None:
            qualification_records, contradictions = build_records(
                ordered, qualification_registry
            )
            # A status that claims a measurement whose value is absent is a
            # semantic defect in the RUN, surfaced here because this is the
            # first layer that reads statuses and values together. It is never
            # resolved by choosing a qualification value.
            qualification_problems.extend(
                f"metric status contradiction: {problem}"
                for problem in contradictions
            )
        qualification_by_subject = {
            subject_of(record["binding"]): record for record in qualification_records
        }
        ordered_errors = sorted(
            errors,
            key=lambda item: (
                subject_key_of(item).casefold(),
                str(item.get("file_path") or "").casefold(),
                str(item.get("error_type") or "").casefold(),
                str(item.get("message") or ""),
            ),
        )
        ordered_recoveries = sorted(
            (
                diagnostic
                for result in ordered
                for diagnostic in result.get("metrics", {}).get(
                    "recovered_parser_diagnostics", []
                )
            ),
            key=lambda item: (
                subject_key_of(item).casefold(),
                str(item.get("file_path") or "").casefold(),
            ),
        )
        mandatory_errors: list[str] = []
        optional_errors: list[str] = []

        def mandatory(label: str, function) -> None:
            try:
                function()
            except Exception as exc:
                mandatory_errors.append(f"{label}: {type(exc).__name__}: {exc}")
                self.logger.log("ERROR", "output", mandatory_errors[-1], error_type="mandatory_output_failure")

        mandatory("analysis.json", lambda: atomic_write_json(self.run_dir / "analysis.json", ordered))
        mandatory(
            "environment.json",
            lambda: atomic_write_json(
                self.run_dir / "environment.json",
                manifest.get("benchmark_environment", {}),
            ),
        )
        catalog_fields = [
            "subject_key", "repository_url", "repository_owner", "repository_name", "architecture_type",
            "expected_language", "primary_language", "analyzed_commit_sha", "acquisition_mode",
            "execution_mode",
            "program_version", "metric_contract_version", "exclusion_policy_version",
            "inventory_schema_version", "artifact_schema_version",
            "lines_of_code", "source_files", "classes_structs", "methods_functions",
            "metric_status", "inventory_status", "source_files_status", "loc_status",
            "classes_structs_status", "methods_functions_status", "source_files_readable",
            "source_files_loc_analyzed", "source_files_entity_parsed", "source_files_failed_read",
            "source_files_oversized",
            "source_files_partial_parse", "source_files_failed_parse",
            "source_files_recovered_parse", "expected_language_mismatch",
            "expected_language_family_status", "partial_origin",
            "db_analysis_status", "endpoint_analysis_status",
            "deployability_status", "coverage_analysis_status", "classification_status",
            "message_broker_analysis_status",
            "acquisition_seconds", "worktree_seconds", "inventory_seconds",
            "core_metrics_seconds", "cleanup_seconds", "total_repository_seconds",
            "error_summary", "metadata_json", "db_analysis_json", "endpoints_json",
            "deployability_json", "coverage_json", "classification_json", "message_brokers_json", "audit_json",
            "timing_json",
            *SHEET_QUALIFICATION_COLUMNS,
        ]

        def _qualification_cells(result: dict[str, Any], columns) -> dict[str, Any]:
            """Project one row's qualification block.

            In generic mode every qualification cell is an explicit null with
            eligibility false. The measurement cells are untouched either way -
            this only ever ADDS columns, and no branch here can reach a
            measurement value.
            """
            if qualification_registry is None:
                return unqualified_cells(result, columns)
            record = qualification_by_subject.get(subject_key_of(result))
            if record is None:  # pragma: no cover - reconciliation refuses first
                return unqualified_cells(result, columns)
            return qualified_cells(
                record, qualification_registry.qualification_profile, columns
            )

        catalog_rows = [
            {
                **_catalog_row(result),
                **_qualification_cells(result, SHEET_QUALIFICATION_COLUMNS),
            }
            for result in ordered
        ]
        mandatory(
            "catalog.csv",
            lambda: atomic_write_csv(self.run_dir / "catalog.csv", catalog_fields, catalog_rows),
        )
        sheet_fields = [
            "subject_key", "repository_url", "architecture_type", "expected_language", "primary_language",
            "analyzed_commit_sha", "execution_mode", "program_version", "metric_contract_version",
            "exclusion_policy_version", "inventory_schema_version", "artifact_schema_version",
            "lines_of_code", "source_files", "classes_structs",
            "methods_functions", "metric_status", "inventory_status", "source_files_status",
            "loc_status", "classes_structs_status", "methods_functions_status",
            "source_files_readable", "source_files_loc_analyzed", "source_files_entity_parsed",
            "source_files_failed_read", "source_files_oversized",
            "source_files_partial_parse", "source_files_failed_parse",
            "source_files_recovered_parse",
            "expected_language_mismatch", "expected_language_family_status",
            "partial_origin", "error_summary",
            *SHEET_QUALIFICATION_COLUMNS,
        ]
        mandatory(
            "sheet_metrics.csv",
            lambda: atomic_write_csv(self.run_dir / "sheet_metrics.csv", sheet_fields, catalog_rows),
        )
        # The unrestricted repository-level comparison surface. An exact
        # filtered SUBSET of sheet_metrics.csv, selected solely by the
        # independently derived eligibility predicate -- never by re-deciding
        # anything here. Written only in benchmark-qualified mode: in a generic
        # run there is no qualification authority, so there is no defensible
        # basis for publishing a cohort at all, and an empty file would read as
        # "nothing qualified" rather than "nothing was asked".
        #
        # `sheet_metrics.csv` remains the COMPLETE MEASURED POPULATION and must
        # never be described as this cohort.
        if qualification_registry is not None:
            eligible_rows = [
                row
                for row in catalog_rows
                if row.get("repository_level_comparison_eligible") is True
            ]
            mandatory(
                "repository_level_metrics.csv",
                lambda: atomic_write_csv(
                    self.run_dir / "repository_level_metrics.csv",
                    sheet_fields,
                    eligible_rows,
                ),
            )
        language_fields = [
            "subject_key", "repository_url", "architecture_type", "language", "analyzed_commit_sha", "execution_mode",
            "program_version", "metric_contract_version", "exclusion_policy_version",
            "inventory_schema_version", "artifact_schema_version",
            "lines_of_code", "source_files",
            "classes_structs", "methods_functions", "metric_status", "inventory_status",
            "source_files_status", "loc_status", "classes_structs_status",
            "methods_functions_status", "source_files_readable", "source_files_loc_analyzed",
            "source_files_entity_parsed", "source_files_failed_read", "source_files_oversized",
            "source_files_partial_parse",
            "source_files_failed_parse", "source_files_recovered_parse",
            "expected_language_mismatch",
            *LANGUAGE_QUALIFICATION_COLUMNS,
        ]
        language_rows = []
        for result in ordered:
            language_qualification = _qualification_cells(
                result, LANGUAGE_QUALIFICATION_COLUMNS
            )
            for language, metric in result.get("metrics", {}).get("by_language", {}).items():
                language_rows.append(
                    {
                        **language_qualification,
                        "subject_key": subject_key_of(result),
                        "repository_url": result["repository_url"],
                        "architecture_type": result["architecture_type"],
                        "language": language,
                        "analyzed_commit_sha": result.get("acquisition", {}).get(
                            "analyzed_commit_sha"
                        ),
                        "execution_mode": result.get("execution_mode"),
                        "program_version": result.get("program_version"),
                        "metric_contract_version": result.get("metric_contract_version"),
                        "exclusion_policy_version": result.get("exclusion_policy_version"),
                        "inventory_schema_version": result.get("inventory_schema_version"),
                        "artifact_schema_version": result.get("artifact_schema_version"),
                        "lines_of_code": metric["lines_of_code"],
                        "source_files": metric["source_files"],
                        "classes_structs": metric["classes_structs"],
                        "methods_functions": metric["methods_functions"],
                        "metric_status": metric["metric_status"],
                        "inventory_status": metric["inventory_status"],
                        "source_files_status": metric["source_files_status"],
                        "loc_status": metric["loc_status"],
                        "classes_structs_status": metric["classes_structs_status"],
                        "methods_functions_status": metric["methods_functions_status"],
                        "source_files_readable": metric["source_files_readable"],
                        "source_files_loc_analyzed": metric["source_files_loc_analyzed"],
                        "source_files_entity_parsed": metric["source_files_entity_parsed"],
                        "source_files_failed_read": metric["source_files_failed_read"],
                        "source_files_oversized": metric.get("source_files_oversized", 0),
                        "source_files_partial_parse": metric["source_files_partial_parse"],
                        "source_files_failed_parse": metric["source_files_failed_parse"],
                        "source_files_recovered_parse": metric["source_files_recovered_parse"],
                        "expected_language_mismatch": result.get("metrics", {}).get(
                            "expected_language_mismatch", False
                        ),
                    }
                )
        mandatory(
            "language_metrics.csv",
            lambda: atomic_write_csv(
                self.run_dir / "language_metrics.csv",
                language_fields,
                language_rows,
                required_fields=("source_files_oversized",),
            ),
        )
        error_fields = [
            "subject_key", "repository_url", "analyzed_commit_sha", "module", "severity", "error_type", "error_category",
            "file_path", "file_sha256", "detected_language", "extension",
            "parser_implementation", "selected_grammar", "grammar_package", "grammar_version",
            "root_has_error", "total_error_nodes", "total_missing_nodes",
            "first_malformed_node_type", "first_error_start_line", "first_error_end_line",
            "first_error_start_column", "first_error_end_column", "first_error_start_byte",
            "first_error_end_byte", "first_error_parser_start_byte",
            "first_error_parser_end_byte", "preview", "affected_metrics", "stage",
            "fallback_attempted", "fallback_grammar", "fallback_error_count",
            "fallback_missing_count", "fallback_strategies", "selected_parse",
            "selected_fallback_strategies",
            "selected_error_count", "selected_missing_count",
            "selected_successfully_parsed_byte_coverage", "fallback_offsets_match_original",
            "original_line_ending_style", "parser_normalization_applied",
            "parser_compatibility_strategy", "parser_byte_offset_adjustment",
            "parser_offsets_match_original",
            "parser_offsets_map_directly_to_original_bytes",
            "original_byte_offsets_available", "original_encoding", "parser_encoding",
            "encoding_transformation_applied", "original_byte_length", "parser_byte_length",
            "nul_count", "nul_density", "nul_positions", "nul_contexts",
            "alternating_nul_evidence", "suspected_bomless_utf16",
            "nul_classification", "typed_javascript_dialect_evidence",
            "final_file_status", "repository_metric_status", "repository_inventory_status",
            "repository_source_files_status", "repository_loc_status",
            "repository_classes_structs_status", "repository_methods_functions_status",
            "dirty_path", "git_status_code", "supported_source_file",
            "gitattributes_evidence", "line_ending_normalization_suspected", "message",
            "git_mode_map_status", "git_mode_map_available", "git_mode_map_error",
            "git_mode_entry_count", "git_mode_command_duration_seconds",
            "git_mode_effective_timeout_seconds",
        ]
        mandatory(
            "errors.csv",
            lambda: atomic_write_csv(
                self.run_dir / "errors.csv", error_fields, ordered_errors
            ),
        )
        recovery_fields = [
            "subject_key", "repository_url", "analyzed_commit_sha", "file_path", "file_sha256",
            "detected_language", "extension", "parser_implementation", "selected_grammar",
            "grammar_package", "grammar_version", "total_error_nodes", "total_missing_nodes",
            "affected_metrics", "stage", "fallback_attempted", "fallback_grammar",
            "fallback_error_count", "fallback_missing_count", "fallback_strategies",
            "selected_fallback_strategies",
            "selected_parse", "selected_error_count", "selected_missing_count",
            "selected_successfully_parsed_byte_coverage", "fallback_offsets_match_original",
            "original_line_ending_style", "parser_normalization_applied",
            "parser_compatibility_strategy", "parser_byte_offset_adjustment",
            "parser_offsets_match_original",
            "parser_offsets_map_directly_to_original_bytes",
            "original_byte_offsets_available", "original_encoding", "parser_encoding",
            "encoding_transformation_applied", "original_byte_length", "parser_byte_length",
            "nul_count", "nul_density", "nul_positions", "nul_contexts",
            "alternating_nul_evidence", "suspected_bomless_utf16",
            "nul_classification", "typed_javascript_dialect_evidence",
            "final_file_status", "error_category", "message",
        ]
        mandatory(
            "recoveries.csv",
            lambda: atomic_write_csv(
                self.run_dir / "recoveries.csv", recovery_fields, ordered_recoveries
            ),
        )
        input_fields = [
            "url", "architecture_type", "expected_language", "commit_sha", "enabled", "notes"
        ]
        retry_rows = [
            _repository_input_row(result, fall_back_to_requested=True)
            for result in ordered
            if result.get("metrics", {}).get("aggregate", {}).get("metric_status")
            in {"partial", "failed"}
            or result.get("metrics", {}).get("aggregate", {}).get("inventory_status")
            in {"partial", "failed"}
        ]
        frozen_rows = [
            _repository_input_row(result)
            for result in ordered
            if result.get("acquisition", {}).get("analyzed_commit_sha")
            and result.get("acquisition", {}).get("commit_verification_status") == "verified"
        ]
        # Per-file contribution ledger (plan section 14). The components were
        # captured during measurement; this only stores them.
        from modules.contribution_ledger import build_rows, write_ledger

        contribution_rows = build_rows(ordered)
        mandatory(
            "contributions",
            lambda: atomic_write_json(
                self.run_dir / "contributions" / "container.json",
                write_ledger(self.run_dir, contribution_rows, writer=atomic_write_csv),
            ),
        )

        # Per-callable complexity ledger (Artifact Schema 1.9.0). Written only
        # when some repository actually measured complexity: a run whose
        # complexity state is explicitly unavailable must NOT publish an empty
        # artifact, because an empty ledger and an unmeasured one would then look
        # identical. The completeness gate checks that this choice matches the
        # state each repository declares.
        from modules.callable_ledger import build_rows as build_callable_rows
        from modules.callable_ledger import write_ledger as write_callable_ledger

        callable_rows = build_callable_rows(ordered)
        if _any_complexity_measured(ordered):
            mandatory(
                "callables",
                lambda: atomic_write_json(
                    self.run_dir / "callables" / "container.json",
                    write_callable_ledger(
                        self.run_dir, callable_rows, writer=atomic_write_csv
                    ),
                ),
            )

        if normalization is not None:
            # The complete accepted input population (plan section 11). Written
            # before the derived frozen and retry artifacts so that all three
            # necessarily agree.
            from modules.normalized_input import (
                NORMALIZED_INPUT_COLUMNS,
                apply_results,
                derive_frozen_rows,
                derive_retry_rows,
            )

            apply_results(normalization, ordered)
            mandatory(
                "normalized_input.csv",
                lambda: atomic_write_csv(
                    self.run_dir / "normalized_input.csv",
                    list(NORMALIZED_INPUT_COLUMNS),
                    _normalized_input_cells(normalization),
                ),
            )
            derived_frozen = derive_frozen_rows(normalization)
            derived_retry = derive_retry_rows(normalization)
            # The ledger is authoritative for the population, so its derivations
            # replace the result-scan versions when it is available.
            frozen_rows = derived_frozen
            retry_rows = _merge_retry_rows(retry_rows, derived_retry)

        mandatory(
            "retry_failed_or_partial.csv",
            lambda: atomic_write_csv(
                self.run_dir / "retry_failed_or_partial.csv", input_fields, retry_rows
            ),
        )
        mandatory(
            "repositories_frozen.csv",
            lambda: atomic_write_csv(
                self.run_dir / "repositories_frozen.csv", input_fields, frozen_rows
            ),
        )
        for slug, inventory in sorted(inventories.items()):
            mandatory(
                f"file_inventory/{slug}.json",
                lambda slug=slug, inventory=inventory: atomic_write_json(
                    self.run_dir / "file_inventory" / f"{slug}.json", inventory
                ),
            )
        for result in ordered:
            try:
                slug = _slug(result)
                atomic_write_json(self.run_dir / "repositories" / f"{slug}.json", result)
                atomic_write_text(self.run_dir / "fact_sheets" / f"{slug}.md", render_fact_sheet(result))
            except Exception as exc:
                optional_errors.append(f"repository reports for {result['repository_url']}: {type(exc).__name__}: {exc}")

        # ---- Qualification artifact (R0 plan section 7) ---------------------
        # Every measurement authority is now persisted and unmodified. The
        # qualification artifact is staged, read back as bytes, reconciled
        # against analysis.json, hashed, and only then moved to its final path.
        # It happens HERE -- after measurement, before any terminal state -- so
        # that a qualification failure can refuse readiness without ever being
        # able to rewrite a measurement outcome.
        qualification_artifact: dict[str, Any] | None = None
        qualification_binding: dict[str, Any] | None = None
        if qualification_registry is not None:
            (
                qualification_artifact,
                qualification_binding,
                write_problems,
            ) = self._write_qualification_artifact(
                qualification_records, qualification_registry, ordered
            )
            qualification_problems.extend(write_problems)
            for problem in write_problems:
                # A mandatory output failure: benchmark-qualified mode promised
                # this artifact. It degrades run integrity, never measurement.
                mandatory_errors.append(f"benchmark_qualification.json: {problem}")
                self.logger.log(
                    "ERROR", "output", mandatory_errors[-1],
                    error_type="mandatory_output_failure",
                )

        # ---- Finalization ordering (plan section 13.1) ----------------------
        # Steps 1-3 are complete: repository execution finished, every
        # authoritative artifact except manifest/status has been written, and
        # mandatory/optional failures are collected.
        from modules.summary import measurement_outcome, render_summary

        # Step 4. Measurement outcome, derived from repository results alone.
        # A failed optional projection must never change it (plan section 4.5).
        outcome = measurement_outcome(ordered)

        # Step 5. Provisional run integrity status.
        integrity_status = "failed" if mandatory_errors else (
            "completed_with_errors"
            if errors or optional_errors or any(
                result.get("metrics", {}).get("aggregate", {}).get("metric_status")
                in {"partial", "failed"}
                for result in ordered
            )
            else "completed"
        )

        # Step 6. Build the final manifest payload. `end_timestamp` was already
        # assigned once by the runner; reassigning it here was the duplicate
        # end-time semantics plan section 13.1 requires removing.
        manifest["measurement_finished_at"] = manifest.get("end_timestamp")
        manifest["measurement_outcome"] = outcome
        manifest["output_failures"] = {
            "mandatory": mandatory_errors, "optional": optional_errors
        }
        manifest["run_status"] = integrity_status
        manifest["run_integrity_status"] = integrity_status

        # Step 6b. Qualification mode, artifact hash binding and registry
        # provenance. Written for EVERY run: a generic run positively declares
        # `not_requested` rather than leaving the question open, and the two
        # binding objects are explicit nulls rather than absent keys.
        manifest["qualification_mode"] = qualification_mode
        manifest["benchmark_qualification_artifact"] = qualification_binding
        manifest["qualification_registry"] = (
            {
                "registry_schema_version": qualification_registry.registry_schema_version,
                "source_id": qualification_registry.source_id,
                "registry_sha256": qualification_registry.registry_sha256,
                "qualification_profile": qualification_registry.qualification_profile,
                "exact_match_count": sum(
                    1
                    for record in qualification_records
                    if record["qualification_status"] == "adjudicated"
                ),
                "unresolved_count": sum(
                    1
                    for record in qualification_records
                    if record["qualification_status"] == "unresolved"
                ),
                "unused_record_count": qualification_registry.unused_record_count,
                "warnings": sorted(qualification_problems),
            }
            if qualification_registry is not None
            else None
        )

        # Steps 7-12 are a candidate / validate / commit protocol.
        #
        # The defect this replaces: self-validation ran against a *provisional*
        # manifest and a `running` status, and the real terminal manifest and
        # status were written afterwards. The bytes a user would later read were
        # therefore never the bytes that were validated.
        #
        # The naive repair -- write the terminal state, validate it, rewrite it
        # as failed if invalid -- does not work, because the failure rewrite is
        # itself a new terminal state that nothing validated.
        #
        # So *every* published terminal state is staged, validated as bytes, and
        # only then committed:
        #
        #   1. leave a provisional manifest in place saying `running`, which is
        #      what `run_status.json` also says, so no crash point exposes a
        #      contradiction between the two;
        #   2. build the terminal manifest and status, stage both beside their
        #      final paths;
        #   3. validate the whole run, plus both staged byte sequences decoded
        #      from disk;
        #   4. commit the manifest, then the status. Nothing is re-serialized,
        #      so committed bytes are validated bytes;
        #   5. if the success candidate fails, repeat 2-4 for a `failed`
        #      candidate. If *that* cannot be validated either, publish no
        #      terminal status at all.
        #
        # `run_status.json` is the commit record: `classify_lifecycle` reports
        # RUNNING while it says `running` and CORRUPT when it is missing, so no
        # crash before its rename can present the run as finalized.
        #
        # What the commit does and does not guarantee:
        #
        # * **Atomic publication under process failure — yes.** `os.replace` on
        #   one directory is atomic with respect to concurrent readers and to
        #   the process dying at any point: a reader sees the old file or the
        #   new one, never a partial write, and never a torn document.
        #
        # * **Durability across OS crash or power loss — not claimed.** The
        #   candidate's *contents* are fsynced by `atomic_write_text` before the
        #   rename, so the bytes are on stable storage. The **directory entry
        #   created by the rename is not fsynced**, so after a power loss the
        #   filesystem may recover to a state where the rename never happened.
        #   That is a *safe* loss — the run reverts to `running`, which reads as
        #   non-finalized — but it is a loss, and it means this protocol must
        #   not be described as crash-durable. Ordering, not durability, is what
        #   makes it correct: the only state that can survive is one that was
        #   validated, or one that is non-terminal.

        manifest_path = self.run_dir / "run_manifest.json"
        status_path = self.run_dir / "run_status.json"
        manifest_candidate = self.run_dir / "run_manifest.json.candidate"
        status_candidate = self.run_dir / "run_status.json.candidate"

        def terminal_manifest(status: str, problems: list[str]) -> dict[str, Any]:
            document = dict(manifest)
            document["run_status"] = status
            document["run_integrity_status"] = status
            document["output_failures"] = {
                "mandatory": list(mandatory_errors),
                "optional": list(optional_errors),
            }
            document["self_validation"] = {
                "passed": not problems,
                "problems": list(problems),
            }
            # Benchmark-of-record readiness, evaluated independently of every
            # measurement outcome. A run may hold entirely valid measurements
            # and still be NOT_READY -- missing profiler provenance alone is
            # enough -- and READY proves no metric value correct.
            document["benchmark_of_record_readiness"] = evaluate_readiness(
                manifest=document,
                mode=qualification_mode,
                artifact=qualification_artifact,
                binding=qualification_binding,
                registry_provenance=document.get("qualification_registry"),
                executed_count=len(ordered),
                integrity_status=status,
                self_validation_passed=not problems,
                projection_problems=self._projection_problems(
                    qualification_records, qualification_mode
                ),
            )
            document["finalization_finished_at"] = utc_now()
            return document

        def terminal_status(status: str) -> dict[str, Any]:
            # `measurement_outcome` is `outcome` on every path. How much was
            # measured is a separate question from whether the artifacts are
            # readable (plan section 4.5), so an integrity failure must never
            # rewrite it to hide how much measurement actually completed.
            return {
                "run_id": self.run_id,
                "status": status,
                "measurement_outcome": outcome,
                "updated_at": utc_now(),
                "repository_count": manifest.get("repository_count"),
                "input_row_count": manifest.get("input_row_count"),
                "planned_repository_count": manifest.get("planned_repository_count"),
                "processed_repository_count": manifest.get("processed_repository_count"),
                "success_count": manifest.get("success_count"),
                "partial_count": manifest.get("partial_count"),
                "failure_count": manifest.get("failure_count"),
                "skipped_disabled_count": manifest.get("skipped_disabled_count"),
                "duplicate_rows_dropped": manifest.get("duplicate_rows_dropped", 0),
                "mandatory_output_failures": list(mandatory_errors),
                "optional_output_failures": list(optional_errors),
                "complexity_measurement_state": run_complexity_state(ordered),
                # Complexity Contract 2.0.0 section 12.8. Cognitive complexity
                # shares the structural traversal exactly, so at RUN level it
                # shares that traversal's outcome and its vocabulary
                # (`measured` / `unavailable`) rather than the four-state
                # repository form. The field is still written separately
                # because its ABSENCE is the load-bearing fact: a pre-1.10 run
                # has a complexity state and no cognitive one.
                "cognitive_measurement_state": run_complexity_state(ordered),
            }

        def stage(candidate_manifest, candidate_status) -> str | None:
            """Serialize both candidates. Returns a problem, or None."""
            try:
                atomic_write_json(manifest_candidate, candidate_manifest)
                atomic_write_json(status_candidate, candidate_status)
            except Exception as exc:
                return f"terminal state could not be staged: {type(exc).__name__}: {exc}"
            return None

        def commit() -> str | None:
            """Publish manifest then status. Status last: it is the record."""
            try:
                _replace_with_retry(manifest_candidate, manifest_path)
            except OSError as exc:
                return f"run_manifest.json commit failed: {type(exc).__name__}: {exc}"
            try:
                _replace_with_retry(status_candidate, status_path)
            except OSError as exc:
                return f"run_status.json commit failed: {type(exc).__name__}: {exc}"
            return None

        def discard_candidates() -> None:
            manifest_candidate.unlink(missing_ok=True)
            status_candidate.unlink(missing_ok=True)

        # Step 7. Provisional manifest, needed only so the strict reader can
        # derive `artifact_schema_version` and version-gate every table
        # contract. It declares `running`, matching `run_status.json`, so this
        # write is never authoritative and never contradicts the status.
        provisional = dict(manifest)
        provisional["run_status"] = "running"
        provisional["run_integrity_status"] = "running"
        provisional["output_failures"] = {
            "mandatory": list(mandatory_errors), "optional": list(optional_errors)
        }
        mandatory(
            "run_manifest.json",
            lambda: atomic_write_json(manifest_path, provisional),
        )

        # Step 8. Stage the success candidate, and render summary.md for it.
        # summary.md is human-facing markdown, is not schema-validated, and is
        # not authoritative for anything; it is rewritten below if the candidate
        # is rejected.
        success_manifest = terminal_manifest(integrity_status, [])
        mandatory(
            "summary.md",
            lambda: atomic_write_text(
                self.run_dir / "summary.md",
                render_summary(
                    success_manifest, ordered,
                    outcome=outcome,
                    integrity_status=integrity_status,
                    normalization=normalization,
                    qualification_records=qualification_records,
                ),
            ),
        )
        staging_problem = stage(success_manifest, terminal_status(integrity_status))

        # Step 9. Validate the exact state that is about to become
        # authoritative: every artifact at its final path, plus both staged byte
        # sequences decoded from disk.
        gate_problems = self._self_validation_problems(
            status_candidate if staging_problem is None else None,
            manifest_candidate if staging_problem is None else None,
        )
        if staging_problem is not None:
            gate_problems = list(gate_problems) + [staging_problem]
        # Step 9b. Complexity completeness. Runs AFTER structural validation and
        # BEFORE commit, on exact bytes: the staged candidate status and the
        # persisted artifacts, never the in-memory results. Kept out of
        # `_self_validation_problems`, whose scope is structural only.
        complexity_problems = self._complexity_completeness_problems(
            status_candidate if staging_problem is None else None
        )
        gate_problems = list(gate_problems) + [
            f"complexity_completeness: {problem}" for problem in complexity_problems
        ]
        # Step 9c. Qualification conditional presence and projection
        # reconciliation, on the same terms: exact bytes on disk, after
        # structural validation, before commit. Kept separate from both layers
        # above because each states its own scope, and widening one to carry
        # another's obligation is how a check stops being reviewable.
        gate_problems = list(gate_problems) + [
            f"qualification_completeness: {problem}"
            for problem in (
                *self._qualification_presence_problems(qualification_mode),
                *self._projection_problems(qualification_records, qualification_mode),
            )
        ]
        for problem in gate_problems:
            mandatory_errors.append(f"self_validation: {problem}")
            self.logger.log(
                "ERROR", "output", mandatory_errors[-1],
                error_type="self_validation_failure",
            )

        # Step 10. Commit the success candidate.
        if not gate_problems and not mandatory_errors:
            commit_problem = commit()
            if commit_problem is None:
                discard_candidates()
                # Step 11. Only a committed, validated terminal state may
                # advance the convenience pointer.
                if integrity_status in {"completed", "completed_with_errors"}:
                    atomic_write_json(
                        self.output_root / "latest_run.json",
                        {
                            "run_id": self.run_id,
                            "run_directory": self.run_dir.relative_to(
                                self.output_root
                            ).as_posix(),
                            "status": integrity_status,
                            "completed_at": success_manifest.get(
                                "finalization_finished_at"
                            ),
                        },
                    )
                return integrity_status
            mandatory_errors.append(commit_problem)
            self.logger.log(
                "ERROR", "output", commit_problem,
                error_type="mandatory_output_failure",
            )

        # Step 12. Failure path. The failed terminal state is held to exactly the
        # same standard: staged, validated as bytes, and only then published. It
        # is never written unvalidated for convenience.
        integrity_status = "failed"
        failure_manifest = terminal_manifest(integrity_status, gate_problems)
        mandatory(
            "summary.md",
            lambda: atomic_write_text(
                self.run_dir / "summary.md",
                render_summary(
                    failure_manifest, ordered,
                    outcome=outcome,
                    integrity_status=integrity_status,
                    normalization=normalization,
                    qualification_records=qualification_records,
                ),
            ),
        )
        failure_staging = stage(failure_manifest, terminal_status(integrity_status))
        failure_problems = (
            [failure_staging] if failure_staging
            else self._terminal_candidate_problems(status_candidate, manifest_candidate)
        )
        if not failure_problems and commit() is None:
            discard_candidates()
            return integrity_status

        # The failure candidate could not be validated or committed through the
        # normal protocol. A command that has terminated must nevertheless not
        # leave a run claiming it is still running. Publish an explicitly
        # failed, self-validation-failed emergency state. It is fail-closed:
        # readers classify any structurally bad bytes as finalized-invalid, and
        # no consumer can mistake this for a successful finalized run.
        for problem in failure_problems:
            self.logger.log(
                "ERROR", "output",
                f"terminal failure state was not published: {problem}",
                error_type="finalization_unpublishable",
            )
        emergency_problems = [str(problem) for problem in failure_problems]
        emergency_manifest = terminal_manifest("failed", emergency_problems)
        emergency_status = terminal_status("failed")
        try:
            atomic_write_json(manifest_path, emergency_manifest)
            atomic_write_json(status_path, emergency_status)
        except OSError as exc:
            # A filesystem that cannot write either terminal document is beyond
            # the lifecycle contract's control; preserve the exact failure in
            # the log and still return failed.
            self.logger.log(
                "ERROR", "output",
                f"emergency terminal state could not be published: {type(exc).__name__}: {exc}",
                error_type="finalization_unpublishable",
            )
        discard_candidates()
        return integrity_status


def repository_complexity(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """One repository's complexity summary, or ``None`` when it has none."""
    complexity = (result.get("metrics") or {}).get("complexity")
    return complexity if isinstance(complexity, Mapping) else None


def _any_complexity_measured(results: Sequence[Mapping[str, Any]]) -> bool:
    """True when at least one repository declares an evaluable complexity state."""
    return any(
        (repository_complexity(result) or {}).get("status")
        in {"complete", "partial"}
        for result in results
    )


def _generation_of(declared: Any) -> tuple[int, int]:
    """(major, minor) of a declared artifact schema, or (0, 0) when unreadable.

    Compared as a TUPLE, never as a string prefix: "1.10" does not start with
    "1.9", and a prefix test is how a completeness layer quietly switches
    itself off at the next minor bump.
    """
    parts = str(declared or "").split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return (0, 0)


def run_complexity_state(results: Sequence[Mapping[str, Any]]) -> str:
    """Run-level state: `measured` iff some repository measured complexity.

    Deliberately derived from the declared per-repository STATUS rather than
    from whether rows exist. A run over sources that genuinely contain no
    callable has measured complexity and found none, which is a different fact
    from a run that could not measure at all.
    """
    return "measured" if _any_complexity_measured(results) else "unavailable"


def _summarize_errors(result: dict[str, Any]) -> str:
    errors = result.get("errors", [])
    recoveries = result.get("metrics", {}).get("recovered_parser_diagnostics", [])
    unresolved_syntax = [
        error
        for error in errors
        if error.get("error_category") in {"syntax_partial", "syntax_failed"}
        and error.get("file_path")
    ]
    parts: list[str] = []
    if unresolved_syntax:
        parts.append(f"syntax_partial: {len(unresolved_syntax)} unresolved files")
    strategy_labels = {
        "javascript_import_assertion_compat": "import-assertion",
        "jsx_reserved_attribute_compat": "reserved-JSX-attribute",
        "raw_jsx_ampersand_compat": "raw-JSX-ampersand",
        "typescript_keyword_parameter_compat": "TypeScript-keyword-parameter",
        "typed_javascript_tsx_compat": "typed-JavaScript-TSX",
    }
    strategy_counts = Counter(
        strategy
        for diagnostic in recoveries
        for strategy in diagnostic.get("fallback_strategies", [])
    )
    for strategy, count in sorted(strategy_counts.items()):
        parts.append(f"recovered: {count} {strategy_labels.get(strategy, strategy)} files")
    affected = sorted(
        {
            metric
            for diagnostic in [*unresolved_syntax, *recoveries]
            for metric in diagnostic.get("affected_metrics", [])
        }
    )
    if affected:
        parts.append("affected metrics: " + ", ".join(affected))
    unresolved_ids = {id(error) for error in unresolved_syntax}
    other_messages: list[str] = []
    for error in errors:
        if id(error) in unresolved_ids:
            continue
        message = str(error.get("message") or "").strip()
        if message and message not in other_messages:
            other_messages.append(message)
    parts.extend(other_messages)
    return "; ".join(parts)


def _catalog_row(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics", {})
    aggregate = metrics.get("aggregate", {})
    acquisition = result.get("acquisition", {})
    statuses = result.get("module_statuses", {})
    errors = result.get("errors", [])
    timings = result.get("timings", {})
    return {
        "subject_key": subject_key_of(result),
        "repository_url": result.get("repository_url"),
        "repository_owner": result.get("repository_owner"),
        "repository_name": result.get("repository_name"),
        "architecture_type": result.get("architecture_type"),
        "expected_language": result.get("expected_language") or "",
        "expected_language_family_status": result.get(
            "expected_language_family_status", "not_applicable"
        ),
        "partial_origin": result.get("partial_origin", "none"),
        "primary_language": metrics.get("primary_language_name") or "",
        "analyzed_commit_sha": acquisition.get("analyzed_commit_sha") or "",
        "acquisition_mode": acquisition.get("acquisition_mode") or "",
        "execution_mode": result.get("execution_mode") or "",
        "program_version": result.get("program_version") or "",
        "metric_contract_version": result.get("metric_contract_version") or "",
        "exclusion_policy_version": result.get("exclusion_policy_version") or "",
        "inventory_schema_version": result.get("inventory_schema_version") or "",
        "artifact_schema_version": result.get("artifact_schema_version") or "",
        "lines_of_code": aggregate.get("lines_of_code"),
        "source_files": aggregate.get("source_files"),
        "classes_structs": aggregate.get("classes_structs"),
        "methods_functions": aggregate.get("methods_functions"),
        "metric_status": aggregate.get("metric_status", "failed"),
        "inventory_status": aggregate.get("inventory_status", "failed"),
        "source_files_status": aggregate.get("source_files_status", "failed"),
        "loc_status": aggregate.get("loc_status", "failed"),
        "classes_structs_status": aggregate.get("classes_structs_status", "failed"),
        "methods_functions_status": aggregate.get("methods_functions_status", "failed"),
        "source_files_readable": aggregate.get("source_files_readable"),
        "source_files_loc_analyzed": aggregate.get("source_files_loc_analyzed"),
        "source_files_entity_parsed": aggregate.get("source_files_entity_parsed"),
        "source_files_failed_read": aggregate.get("source_files_failed_read"),
        "source_files_oversized": aggregate.get("source_files_oversized"),
        "source_files_partial_parse": aggregate.get("source_files_partial_parse"),
        "source_files_failed_parse": aggregate.get("source_files_failed_parse"),
        "source_files_recovered_parse": aggregate.get("source_files_recovered_parse"),
        "expected_language_mismatch": metrics.get("expected_language_mismatch", False),
        "db_analysis_status": statuses.get("db_analysis", "failed"),
        "endpoint_analysis_status": statuses.get("endpoint_analysis", "failed"),
        "deployability_status": statuses.get("deployability", "failed"),
        "coverage_analysis_status": statuses.get("coverage_analysis", "failed"),
        "classification_status": statuses.get("classification", "failed"),
        "message_broker_analysis_status": statuses.get("message_broker_analysis", "failed"),
        "acquisition_seconds": timings.get("acquisition_seconds"),
        "worktree_seconds": timings.get("worktree_seconds"),
        "inventory_seconds": timings.get("inventory_seconds"),
        "core_metrics_seconds": timings.get("core_metrics_seconds"),
        "cleanup_seconds": timings.get("cleanup_seconds"),
        "total_repository_seconds": timings.get("total_repository_seconds"),
        "error_summary": _summarize_errors(result),
        "metadata_json": json.dumps(result.get("metadata"), ensure_ascii=False, sort_keys=True),
        "db_analysis_json": json.dumps(result.get("db_analysis"), ensure_ascii=False, sort_keys=True),
        "endpoints_json": json.dumps(result.get("endpoints"), ensure_ascii=False, sort_keys=True),
        "deployability_json": json.dumps(result.get("deployability"), ensure_ascii=False, sort_keys=True),
        "coverage_json": json.dumps(result.get("coverage"), ensure_ascii=False, sort_keys=True),
        "classification_json": json.dumps(result.get("classification"), ensure_ascii=False, sort_keys=True),
        "message_brokers_json": json.dumps(result.get("message_brokers"), ensure_ascii=False, sort_keys=True),
        "audit_json": json.dumps(result.get("audits"), ensure_ascii=False, sort_keys=True),
        "timing_json": json.dumps(timings, ensure_ascii=False, sort_keys=True),
    }


def _normalized_input_cells(normalization: Any) -> list[dict[str, Any]]:
    """Render ledger rows for CSV, keeping null and empty string distinct."""
    rendered: list[dict[str, Any]] = []
    for row in normalization.as_rows():
        cells: dict[str, Any] = {}
        for name, value in row.items():
            if value is None:
                cells[name] = ""
            elif isinstance(value, bool):
                cells[name] = "TRUE" if value else "FALSE"
            elif isinstance(value, list):
                cells[name] = json.dumps(value, ensure_ascii=False)
            else:
                cells[name] = value
        rendered.append(cells)
    return rendered


def _merge_retry_rows(
    scanned: list[dict[str, Any]], derived: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep the result-scan retry selection, taking SHAs from the ledger.

    The scan decides *which* repositories need retrying, because that depends on
    metric and inventory status the ledger does not carry. The ledger decides
    *which SHA* each row gets, because it is the only artifact that still knows
    the requested SHA after a failed acquisition.
    """
    by_url = {str(row.get("url")): row for row in derived}
    merged: list[dict[str, Any]] = []
    for row in scanned:
        candidate = by_url.get(str(row.get("url")))
        if candidate and not row.get("commit_sha"):
            row = {**row, "commit_sha": candidate.get("commit_sha", "")}
        merged.append(row)
    return merged


def _repository_input_row(
    result: dict[str, Any], *, fall_back_to_requested: bool = False
) -> dict[str, Any]:
    """Build one input-shaped row from a repository result.

    ``fall_back_to_requested`` is set for ``retry_failed_or_partial.csv``
    (finding F-5, decision D-2, Artifact Schema 1.5). Retry rows previously used
    the analyzed SHA alone, so a repository whose acquisition failed wrote an
    empty ``commit_sha`` — exactly the case a retry file exists to serve. The
    requested SHA is the input's own instruction and survives a failed
    acquisition, so it is the correct fallback.

    ``repositories_frozen.csv`` deliberately does **not** set this flag: a frozen
    input asserts that re-running it reproduces the same bytes, which only a
    verified analyzed SHA can support.
    """
    acquisition = result.get("acquisition", {}) or {}
    commit = acquisition.get("analyzed_commit_sha") or ""
    if not commit and fall_back_to_requested:
        commit = result.get("requested_commit_sha") or ""
    return {
        "url": result.get("repository_url"),
        "architecture_type": result.get("architecture_type"),
        "expected_language": result.get("expected_language") or "",
        "commit_sha": commit,
        "enabled": "TRUE",
        "notes": result.get("notes") or "",
    }
