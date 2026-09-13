"""Exact, read-only local revision resolution and canonical side analysis.

Changed-Code Analysis asks Git to identify two commit objects once, then uses
those immutable SHAs everywhere else.  The helpers in this module never fetch,
checkout, update a ref, write an index, or add a worktree.  Measurement is
delegated to :func:`modules.benchmark_runner.run_benchmark`, preserving the
single canonical Metrolith analysis path.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from modules.config import AnalysisConfig, git_command_prefix
from modules.repository_input import RepositorySpec


class RevisionUnavailable(RuntimeError):
    """A requested two-commit question cannot be formed from local objects."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class RevisionAnalysisFailed(RuntimeError):
    """Canonical analysis could not produce a readable finalized side."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True, slots=True)
class ResolvedRevision:
    label: str
    requested: str
    sha: str


@dataclass(frozen=True, slots=True)
class ResolvedRevisionPair:
    source: Path
    base: ResolvedRevision
    head: ResolvedRevision
    shallow_repository: bool
    worktree_state: str
    ancestry: str


@dataclass(slots=True)
class AnalyzedRevision:
    revision: ResolvedRevision
    run_directory: Path
    view: Any
    repository: Mapping[str, Any]
    subject_key: str


@dataclass(slots=True)
class AnalyzedRevisionPair:
    resolved: ResolvedRevisionPair
    base: AnalyzedRevision
    head: AnalyzedRevision
    timings: dict[str, float]


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    })
    return environment


def _run_git(
    source: Path,
    arguments: list[str],
    *,
    timeout: int,
    check: bool = False,
    configured: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    prefix = git_command_prefix() if configured else ["git"]
    try:
        completed = subprocess.run(
            [*prefix, "-C", str(source), *arguments],
            check=False,
            capture_output=True,
            text=False,
            timeout=timeout,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RevisionUnavailable("git_unavailable", type(exc).__name__) from exc
    if check and completed.returncode != 0:
        raise RevisionUnavailable("git_command_failed", arguments[0])
    return completed


def _is_revision_repository(source: Path, timeout: int) -> bool:
    """Accept a worktree or a bare object store for read-only revision queries."""

    worktree = _run_git(
        source, ["rev-parse", "--is-inside-work-tree"], timeout=timeout
    )
    if worktree.returncode == 0 and worktree.stdout.strip() == b"true":
        return True
    bare = _run_git(
        source, ["rev-parse", "--is-bare-repository"], timeout=timeout
    )
    return bare.returncode == 0 and bare.stdout.strip() == b"true"


def _is_shallow(source: Path, timeout: int) -> bool:
    completed = _run_git(
        source, ["rev-parse", "--is-shallow-repository"], timeout=timeout
    )
    return completed.returncode == 0 and completed.stdout.strip() == b"true"


def _resolve_one(
    source: Path,
    revision: str,
    label: str,
    *,
    shallow: bool,
    timeout: int,
) -> ResolvedRevision:
    completed = _run_git(
        source,
        ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        timeout=timeout,
    )
    value = completed.stdout.strip().decode("ascii", errors="ignore")
    if (
        completed.returncode != 0
        or len(value) not in {40, 64}
        or re.fullmatch(r"[0-9a-fA-F]+", value) is None
    ):
        suffix = "revision_missing_in_shallow_clone" if shallow else "revision_unavailable"
        raise RevisionUnavailable(f"{label}_{suffix}", revision)
    return ResolvedRevision(label=label, requested=revision, sha=value.lower())


def resolve_revision_pair(
    source: str | Path,
    base: str,
    head: str,
    *,
    timeout: int = 300,
) -> ResolvedRevisionPair:
    """Resolve two explicit commit-ish values without changing repository state."""
    repository = Path(source).expanduser().resolve(strict=False)
    if not repository.is_dir() or not _is_revision_repository(repository, timeout):
        raise RevisionUnavailable("source_not_git_repository")

    shallow = _is_shallow(repository, timeout)
    resolved_base = _resolve_one(
        repository, base, "base", shallow=shallow, timeout=timeout
    )
    resolved_head = _resolve_one(
        repository, head, "head", shallow=shallow, timeout=timeout
    )

    status = _run_git(
        repository,
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
        timeout=timeout,
        configured=False,
    )
    worktree_state = (
        "unknown" if status.returncode != 0
        else "dirty" if status.stdout
        else "clean"
    )

    ancestry_probe = _run_git(
        repository,
        ["merge-base", "--is-ancestor", resolved_base.sha, resolved_head.sha],
        timeout=timeout,
    )
    ancestry = (
        "ancestor" if ancestry_probe.returncode == 0
        else "not_ancestor" if ancestry_probe.returncode == 1
        else "unknown"
    )
    return ResolvedRevisionPair(
        source=repository,
        base=resolved_base,
        head=resolved_head,
        shallow_repository=shallow,
        worktree_state=worktree_state,
        ancestry=ancestry,
    )


def _repository_spec(
    source: Path,
    revision: ResolvedRevision,
    *,
    subject_key: str | None,
) -> RepositorySpec:
    return RepositorySpec(
        url="",
        architecture_type="unknown",
        enabled=True,
        local_path=str(source),
        revision=revision.sha,
        subject_key=subject_key,
    )


def _natural_subject_key(source: Path, revision: ResolvedRevision) -> str:
    from modules.benchmark_runner import _identity

    return _identity(
        _repository_spec(source, revision, subject_key=None)
    ).subject_key


def _two_side_capability_barrier(
    pair: ResolvedRevisionPair,
    config: AnalysisConfig,
    subject_key: str,
) -> None:
    from modules import preflight
    from modules.benchmark_runner import discover_required_capabilities

    probes = preflight.probe_parser_capabilities()
    if not preflight.barrier_can_refuse(probes):
        return
    labelled = [
        (
            revision.label,
            _repository_spec(pair.source, revision, subject_key=subject_key),
        )
        for revision in (pair.base, pair.head)
    ]
    report = preflight.stage_b(
        discover_required_capabilities(labelled, config, "offline"),
        probes=probes,
    )
    if report.refused:
        raise RevisionAnalysisFailed(
            "parser_capability_unavailable", report.summary()
        )


def _analyze_one(
    source: Path,
    revision: ResolvedRevision,
    config: AnalysisConfig,
    subject_key: str,
) -> AnalyzedRevision:
    from modules.benchmark_runner import run_benchmark
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    output = io.StringIO()
    errors = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            summary = run_benchmark(
                repository_specs=[
                    _repository_spec(source, revision, subject_key=subject_key)
                ],
                config=config,
                acquisition_mode="offline",
                execution_mode="metrics",
                command_line_arguments=[
                    "changed", source.name, f"--{revision.label}", revision.sha
                ],
                single_repository=True,
            )
    except Exception as exc:
        raise RevisionAnalysisFailed(
            f"{revision.label}_analysis_failed", type(exc).__name__
        ) from exc
    if summary.get("status") == "failed":
        raise RevisionAnalysisFailed(f"{revision.label}_analysis_failed")

    run_directory = Path(summary["run_directory"])
    try:
        view = open_run(run_directory)
    except Exception as exc:
        raise RevisionAnalysisFailed(
            f"{revision.label}_artifact_unreadable", type(exc).__name__
        ) from exc
    if not view.compatibility.readable or view.structural_errors or not view.succeeded:
        raise RevisionAnalysisFailed(f"{revision.label}_artifact_unreadable")
    repositories = list(view.repositories)
    if len(repositories) != 1:
        raise RevisionAnalysisFailed(f"{revision.label}_analysis_result_missing")
    repository = repositories[0]
    analyzed_sha = str(
        (repository.get("acquisition") or {}).get("analyzed_commit_sha") or ""
    ).lower()
    if analyzed_sha != revision.sha:
        raise RevisionAnalysisFailed(f"{revision.label}_revision_mismatch")
    return AnalyzedRevision(
        revision=revision,
        run_directory=run_directory,
        view=view,
        repository=repository,
        subject_key=subject_key_of(dict(repository)),
    )


def analyze_revision_pair(
    pair: ResolvedRevisionPair,
    config: AnalysisConfig,
) -> AnalyzedRevisionPair:
    """Analyze both immutable commits through the canonical full metric path."""
    subject_key = _natural_subject_key(pair.source, pair.base)
    barrier_started = time.perf_counter()
    _two_side_capability_barrier(pair, config, subject_key)
    barrier_seconds = time.perf_counter() - barrier_started

    base_started = time.perf_counter()
    base = _analyze_one(pair.source, pair.base, config, subject_key)
    base_seconds = time.perf_counter() - base_started

    head_started = time.perf_counter()
    head = _analyze_one(pair.source, pair.head, config, subject_key)
    head_seconds = time.perf_counter() - head_started
    if base.subject_key != head.subject_key:
        raise RevisionAnalysisFailed("subject_identity_mismatch")
    return AnalyzedRevisionPair(
        resolved=pair,
        base=base,
        head=head,
        timings={
            "capability_barrier_seconds": barrier_seconds,
            "base_analysis_seconds": base_seconds,
            "head_analysis_seconds": head_seconds,
        },
    )


__all__ = [
    "AnalyzedRevision",
    "AnalyzedRevisionPair",
    "ResolvedRevision",
    "ResolvedRevisionPair",
    "RevisionAnalysisFailed",
    "RevisionUnavailable",
    "analyze_revision_pair",
    "resolve_revision_pair",
]
