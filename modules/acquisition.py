"""Safe Git acquisition with reusable objects and isolated analysis checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping

from modules.config import AnalysisConfig, SUPPORTED_EXTENSIONS, git_command_prefix
from modules.repository_input import RepositorySpec, canonicalize_github_url


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AcquisitionError(RuntimeError):
    def __init__(
        self,
        error_type: str,
        message: str,
        command: list[str] | None = None,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
        diagnostics: dict[str, object] | None = None,
    ):
        self.error_type = error_type
        self.command = command or []
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.diagnostics = diagnostics or {}
        self.commands: list[list[str]] = []
        super().__init__(message)


@dataclass(slots=True)
class AcquisitionRecord:
    repository_url: str
    repository_owner: str
    repository_name: str
    requested_commit_sha: str | None
    analyzed_commit_sha: str
    resolved_ref: str
    default_branch: str | None
    acquisition_mode: str
    cache_status: str
    remote_checked: bool
    fetch_timestamp: str | None
    checkout_timestamp: str
    commit_verification_status: str
    fetch_method: str
    cache_hit: bool = False
    cache_created: bool = False
    cache_updated: bool = False
    network_contacted: bool = False
    fetch_performed: bool = False
    cached_commit_available: bool = False
    bytes_downloaded_if_available: int | None = None
    acquisition_duration_seconds: float = 0.0
    worktree_duration_seconds: float = 0.0
    cleanup_duration_seconds: float = 0.0
    git_commands: list[list[str]] = field(default_factory=list)
    archive_source_url: str | None = None
    archive_ref: str | None = None
    post_analysis_checkout_status: str = "pending"
    cleanup_status: str = "pending"
    cleanup_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AcquiredRepository:
    path: Path
    record: AcquisitionRecord


@dataclass(slots=True)
class RevisionPreparation:
    analyzed_commit_sha: str
    resolved_ref: str
    default_branch: str | None
    fetch_method: str
    remote_checked: bool
    fetch_timestamp: str | None
    fetch_performed: bool
    cached_commit_available: bool
    cache_updated: bool


_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()
_COMMAND_LOG: ContextVar[list[list[str]] | None] = ContextVar(
    "metrolith_git_command_log", default=None
)
_ACQUISITION_DEADLINE: ContextVar[float | None] = ContextVar(
    "metrolith_acquisition_deadline", default=None
)
_CACHE_COMPLETE_MARKER = ".metrolith-cache-complete.json"
_ARCHLENS_CACHE_COMPLETE_MARKER = ".archlens-cache-complete.json"
_LEGACY_CACHE_COMPLETE_MARKER = ".archbench-cache-complete.json"


def cache_path_for_url(cache_root: str | Path, repository_url: str) -> Path:
    canonical = canonicalize_github_url(repository_url)
    digest = hashlib.sha256(canonical.lower().encode("utf-8")).hexdigest()[:20]
    return Path(cache_root) / f"r_{digest}.git"


def _dirty_checkout_diagnostics(
    checkout: Path,
    porcelain: str,
    config: AnalysisConfig,
) -> dict[str, object]:
    dirty_files: list[dict[str, object]] = []
    for line in porcelain.splitlines()[:100]:
        if len(line) < 3:
            continue
        status_code = line[:2]
        relative_path = line[3:]
        if " -> " in relative_path:
            relative_path = relative_path.split(" -> ", 1)[1]
        relative_path = relative_path.strip('"')
        extension = Path(relative_path).suffix.lower()
        attributes: dict[str, str] = {}
        try:
            attribute_result = _run_git(
                [
                    "-C",
                    str(checkout),
                    "check-attr",
                    "text",
                    "eol",
                    "--",
                    relative_path,
                ],
                config,
            )
            for attribute_line in attribute_result.stdout.splitlines():
                parts = attribute_line.split(": ", 2)
                if len(parts) == 3:
                    attributes[parts[1]] = parts[2]
        except AcquisitionError as exc:
            attributes["diagnostic_error"] = str(exc)
        text_attribute = attributes.get("text")
        eol_attribute = attributes.get("eol")
        normalization_suspected = bool(
            text_attribute in {"set", "auto"} or eol_attribute in {"lf", "crlf"}
        )
        dirty_files.append(
            {
                "path": relative_path,
                "git_status_code": status_code,
                "extension": extension,
                "supported_source_file": extension in SUPPORTED_EXTENSIONS,
                "gitattributes": attributes,
                "line_ending_normalization_suspected": normalization_suspected,
            }
        )
    return {"dirty_files": dirty_files, "dirty_file_count": len(dirty_files)}


def _checkout_status(checkout: Path, config: AnalysisConfig) -> str:
    """Return content-verified status, bypassing racy-clean stat shortcuts."""
    porcelain = _run_git(
        ["-C", str(checkout), "status", "--porcelain=v1", "--untracked-files=all"],
        config,
    ).stdout.rstrip("\r\n")
    if porcelain:
        return porcelain

    verification_index = checkout.parent / f".metrolith_status_{uuid.uuid4().hex}.index"
    verification_environment = {"GIT_INDEX_FILE": str(verification_index)}
    try:
        _run_git(
            ["-C", str(checkout), "read-tree", "HEAD"],
            config,
            environment=verification_environment,
        )
        try:
            _run_git(
                ["-C", str(checkout), "update-index", "--refresh"],
                config,
                environment=verification_environment,
            )
        except AcquisitionError as exc:
            # Exit 1 means the clean-filtered worktree content differs from the
            # committed tree.  The following diff identifies the exact paths.
            if exc.returncode != 1:
                raise
        content_paths = _run_git(
            ["-C", str(checkout), "diff-files", "--name-only", "-z"],
            config,
            environment=verification_environment,
        ).stdout.split("\0")
        return "\n".join(f" M {path}" for path in content_paths if path)
    finally:
        verification_index.unlink(missing_ok=True)


def cleanup_stale_cache_staging(
    cache_root: str | Path, *, older_than_seconds: int = 3600
) -> tuple[int, list[str]]:
    """Atomically quarantine old, immediate program-owned staging directories."""
    root = Path(cache_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    quarantined = 0
    warnings: list[str] = []
    stale_before = time.time() - older_than_seconds
    for path in root.glob(".r_*.fetching_*"):
        try:
            # Quarantine/failed suffixes deliberately retain the original staging
            # prefix for provenance.  They are terminal states, not candidates
            # for another startup quarantine pass.
            if ".quarantined_" in path.name or ".failed_" in path.name:
                continue
            resolved = path.resolve()
            if resolved.parent != root or not resolved.is_dir():
                continue
            if resolved.stat().st_mtime >= stale_before:
                continue
            target = resolved.with_name(
                f"{resolved.name}.quarantined_{uuid.uuid4().hex[:8]}"
            )
            resolved.replace(target)
            quarantined += 1
        except OSError as exc:
            warnings.append(f"Could not quarantine stale cache staging directory {path}: {exc}")
    return quarantined, warnings


def _cache_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _CACHE_LOCKS_GUARD:
        return _CACHE_LOCKS.setdefault(key, threading.Lock())


def _run_git(
    args: list[str],
    config: AnalysisConfig,
    cwd: Path | None = None,
    timeout: int | None = None,
    retry: bool = False,
    progress: Callable[[str], None] | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [*git_command_prefix(), *args]
    command_log = _COMMAND_LOG.get()
    attempts = max(1, config.git_retries) if retry else 1
    backoff = (5, 15)
    last_error: AcquisitionError | None = None
    for attempt in range(1, attempts + 1):
        configured_timeout = timeout if timeout is not None else config.git_timeout_seconds
        deadline = _ACQUISITION_DEADLINE.get()
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AcquisitionError(
                    "acquisition_deadline_exceeded",
                    f"Repository acquisition exceeded {config.acquisition_deadline_seconds}s",
                    command,
                )
            effective_timeout: float = min(float(configured_timeout), remaining)
        else:
            effective_timeout = float(configured_timeout)
        if command_log is not None:
            command_log.append(list(command))
        stopped = threading.Event()
        reporter: threading.Thread | None = None
        if progress is not None:
            operation = next(
                (
                    value
                    for value in ("worktree", "fetch", "ls-remote", "clone", "init")
                    if value in args
                ),
                "command",
            )
            label = "worktree" if operation == "worktree" else f"git {operation}"
            command_started = time.perf_counter()

            def heartbeat() -> None:
                if stopped.wait(config.heartbeat_interval_seconds):
                    return
                while not stopped.is_set():
                    progress(
                        f"{label} still running; elapsed "
                        f"{time.perf_counter() - command_started:.0f}s"
                    )
                    if stopped.wait(config.heartbeat_interval_seconds):
                        return

            reporter = threading.Thread(
                target=heartbeat,
                name=f"metrolith-git-heartbeat-{operation}",
                daemon=True,
            )
            reporter.start()
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                env=({**os.environ, **environment} if environment else None),
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            deadline_exceeded = deadline is not None and time.monotonic() >= deadline
            last_error = AcquisitionError(
                "acquisition_deadline_exceeded" if deadline_exceeded else "git_timeout",
                (
                    f"Repository acquisition exceeded {config.acquisition_deadline_seconds}s"
                    if deadline_exceeded
                    else f"Git command timed out after {exc.timeout}s"
                ),
                command,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            error_type = _classify_git_failure(stderr or stdout or str(exc))
            details = (stderr or stdout or str(exc)).strip()
            last_error = AcquisitionError(
                error_type,
                f"Git command failed ({exc.returncode}): {details[:500]}",
                command,
                stdout=stdout,
                stderr=stderr,
                returncode=exc.returncode,
            )
        except OSError as exc:
            error_type = "disk_failure" if getattr(exc, "errno", None) in {13, 28} else "git_unavailable"
            raise AcquisitionError(
                error_type, f"Could not execute Git: {exc}", command, stderr=str(exc)
            ) from exc
        finally:
            stopped.set()
            if reporter is not None:
                reporter.join(timeout=0.2)

        transient = last_error.error_type in {"git_timeout", "network_reset", "early_eof"}
        if not retry or not transient or attempt >= attempts:
            raise last_error
        delay = backoff[min(attempt - 1, len(backoff) - 1)]
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AcquisitionError(
                    "acquisition_deadline_exceeded",
                    f"Repository acquisition exceeded {config.acquisition_deadline_seconds}s",
                    command,
                ) from last_error
            delay = min(delay, remaining)
        if progress:
            progress(
                f"retry {attempt + 1}/{attempts} after {last_error.error_type}; "
                f"waiting {delay}s"
            )
        time.sleep(delay)
    raise last_error or AcquisitionError("unknown_git_failure", "Git command failed", command)


def _classify_git_failure(details: str) -> str:
    text = details.casefold()
    if "early eof" in text:
        return "early_eof"
    if any(
        marker in text
        for marker in (
            "connection reset",
            "remote end hung up",
            "http/2 stream",
            "could not resolve host",
            "temporary failure in name resolution",
            "failed to connect",
            "connection timed out",
        )
    ):
        return "network_reset"
    if any(
        marker in text
        for marker in (
            "authentication failed",
            "could not read username",
            "permission denied (publickey)",
            "access denied",
        )
    ):
        return "authentication_failure"
    if "repository not found" in text or "does not appear to be a git repository" in text:
        return "repository_not_found"
    if any(marker in text for marker in ("couldn't find remote ref", "not our ref", "unadvertised object")):
        return "commit_unavailable"
    if any(marker in text for marker in ("no space left", "disk full", "cannot create directory")):
        return "disk_failure"
    if any(marker in text for marker in ("not a git repository", "invalid repository")):
        return "invalid_repository"
    return "unknown_git_failure"


def _verify_cache_identity(
    cache: Path,
    repository_url: str,
    config: AnalysisConfig,
    *,
    allow_empty: bool = False,
) -> None:
    if not cache.is_dir():
        raise AcquisitionError("cache_unavailable", f"Git object cache is unavailable: {cache}")
    if not allow_empty and any(".fetching_" in part for part in cache.parts):
        raise AcquisitionError("invalid_cache", f"Cache is an unpromoted staging repository: {cache}")
    bare = _run_git(["--git-dir", str(cache), "rev-parse", "--is-bare-repository"], config)
    if bare.stdout.strip() != "true":
        raise AcquisitionError("invalid_cache", f"Cache is not a bare Git repository: {cache}")
    remote = _run_git(["--git-dir", str(cache), "remote", "get-url", "origin"], config)
    try:
        actual = canonicalize_github_url(remote.stdout.strip())
    except ValueError as exc:
        raise AcquisitionError("cache_identity_mismatch", f"Cache has an invalid origin URL: {exc}") from exc
    if actual.lower() != canonicalize_github_url(repository_url).lower():
        raise AcquisitionError(
            "cache_identity_mismatch",
            f"Cache origin is {actual}, expected {canonicalize_github_url(repository_url)}",
        )
    _run_git(["--git-dir", str(cache), "count-objects", "-v"], config)
    if not allow_empty and not any(
        (cache / marker).is_file()
        for marker in (
            _CACHE_COMPLETE_MARKER,
            _ARCHLENS_CACHE_COMPLETE_MARKER,
            _LEGACY_CACHE_COMPLETE_MARKER,
        )
    ):
        refs = _run_git(
            ["--git-dir", str(cache), "for-each-ref", "--count=1", "--format=%(objectname)"],
            config,
        ).stdout.strip()
        if not refs:
            raise AcquisitionError(
                "invalid_cache", f"Cache contains no completed revision or completion marker: {cache}"
            )


def validate_cache_deep(
    cache: str | Path, repository_url: str, config: AnalysisConfig
) -> None:
    """Explicit diagnostic validation; normal cache hits use lightweight checks."""
    path = Path(cache)
    _verify_cache_identity(path, repository_url, config)
    _run_git(["--git-dir", str(path), "fsck", "--full", "--no-dangling"], config)


def _create_cache(
    cache: Path,
    repository_url: str,
    config: AnalysisConfig,
    progress: Callable[[str], None] | None = None,
) -> str:
    cache.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{cache.stem}.fetching_", dir=cache.parent)
    ) / "objects.git"
    try:
        if progress:
            progress("initializing targeted bare cache")
        _run_git(["init", "--bare", str(staging)], config)
        _run_git(["--git-dir", str(staging), "remote", "add", "origin", repository_url], config)
        _verify_cache_identity(staging, repository_url, config, allow_empty=True)
        try:
            staging.replace(cache)
        except FileExistsError:
            _verify_cache_identity(cache, repository_url, config)
            return "reused_after_race"
        return "created"
    finally:
        owned_root = staging.parent
        if owned_root.exists():
            shutil.rmtree(owned_root, ignore_errors=True)


def _remote_head(
    repository_url: str,
    config: AnalysisConfig,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    if progress:
        progress("resolving remote default branch")
    result = _run_git(
        ["ls-remote", "--symref", repository_url, "HEAD"],
        config,
        retry=True,
        progress=progress,
    )
    branch_ref = None
    sha = None
    for line in result.stdout.splitlines():
        if line.startswith("ref:") and line.endswith("\tHEAD"):
            branch_ref = line.split()[1]
        elif line.endswith("\tHEAD"):
            sha = line.split()[0]
    if not branch_ref or not sha:
        raise AcquisitionError(
            "default_branch_unavailable",
            f"Remote did not expose a symbolic default branch for {repository_url}",
        )
    return branch_ref, sha.lower()


def _resolve_commit(cache: Path, revision: str, config: AnalysisConfig) -> str:
    try:
        result = _run_git(
            ["--git-dir", str(cache), "rev-parse", "--verify", f"{revision}^{{commit}}"],
            config,
        )
    except AcquisitionError as exc:
        raise AcquisitionError(
            "commit_unavailable", f"Requested Git revision cannot be resolved: {revision}; {exc}"
        ) from exc
    sha = result.stdout.strip().lower()
    if len(sha) != 40:
        raise AcquisitionError("commit_unavailable", f"Git returned a non-full commit SHA: {sha!r}")
    return sha


def _cached_commit(cache: Path, revision: str, config: AnalysisConfig) -> str | None:
    try:
        return _resolve_commit(cache, revision, config)
    except AcquisitionError as exc:
        if exc.error_type == "commit_unavailable":
            return None
        raise


def _fetch_branch(
    cache: Path,
    branch_ref: str,
    config: AnalysisConfig,
    *,
    depth: int | None = 1,
    deepen: int | None = None,
    unshallow: bool = False,
    progress: Callable[[str], None] | None = None,
) -> None:
    args = ["--git-dir", str(cache), "fetch"]
    if depth is not None:
        args.extend(["--depth", str(depth)])
    if deepen is not None:
        args.extend(["--deepen", str(deepen)])
    if unshallow:
        args.append("--unshallow")
    args.extend(["--no-tags", "origin", f"+{branch_ref}:{branch_ref}"])
    _run_git(args, config, retry=True, progress=progress)


def _is_shallow(cache: Path, config: AnalysisConfig) -> bool:
    result = _run_git(
        ["--git-dir", str(cache), "rev-parse", "--is-shallow-repository"], config
    )
    return result.stdout.strip() == "true"


def _offline_default(cache: Path, config: AnalysisConfig) -> tuple[str, str | None]:
    try:
        symbolic = _run_git(["--git-dir", str(cache), "symbolic-ref", "HEAD"], config).stdout.strip()
        return symbolic, symbolic.removeprefix("refs/heads/") or None
    except AcquisitionError:
        return "HEAD", None


def _prepare_revision(
    spec: RepositorySpec,
    mode: str,
    cache: Path,
    config: AnalysisConfig,
    progress: Callable[[str], None] | None = None,
) -> RevisionPreparation:
    fetch_timestamp: str | None = None
    if mode == "offline":
        _verify_cache_identity(cache, spec.url, config)
        if spec.commit_sha:
            revision, default_branch = spec.commit_sha, None
        else:
            revision, default_branch = _offline_default(cache, config)
        return RevisionPreparation(
            _resolve_commit(cache, revision, config),
            revision,
            default_branch,
            "offline_cache",
            False,
            fetch_timestamp,
            False,
            True,
            False,
        )

    if mode == "frozen":
        if not spec.commit_sha:
            raise AcquisitionError("missing_commit", "Frozen mode requires commit_sha")
        analyzed = _cached_commit(cache, spec.commit_sha, config)
        if analyzed is not None:
            if progress:
                progress("cache hit; requested commit already available locally")
            return RevisionPreparation(
                analyzed, spec.commit_sha, None, "git_cache", False, None,
                False, True, False,
            )
        if progress:
            progress(f"fetching requested commit {spec.commit_sha[:12]}")
        remote_checked = True
        direct_error: AcquisitionError | None = None
        try:
            _run_git(
                [
                    "--git-dir", str(cache), "fetch", "--depth", "1", "--no-tags",
                    "origin", spec.commit_sha,
                ],
                config,
                retry=True,
                progress=progress,
            )
            analyzed = _cached_commit(cache, spec.commit_sha, config)
        except AcquisitionError as exc:
            direct_error = exc
            analyzed = None
        fetch_timestamp = _utc_now()
        if analyzed is not None:
            return RevisionPreparation(
                analyzed, spec.commit_sha, None, "git_fetch_commit", remote_checked,
                fetch_timestamp, True, False, True,
            )

        branch_ref, _ = _remote_head(spec.url, config, progress)
        default_branch = branch_ref.removeprefix("refs/heads/")
        if progress:
            progress(f"direct SHA fetch unavailable; trying {default_branch}")
        _fetch_branch(cache, branch_ref, config, depth=1, progress=progress)
        analyzed = _cached_commit(cache, spec.commit_sha, config)
        for amount in (50, 200, 1000):
            if analyzed is not None:
                break
            if progress:
                progress(f"deepening {default_branch} by {amount} commits")
            _fetch_branch(
                cache, branch_ref, config, depth=None, deepen=amount, progress=progress
            )
            analyzed = _cached_commit(cache, spec.commit_sha, config)
        if analyzed is None:
            if progress:
                progress(f"targeted history exhausted; fetching full {default_branch} history")
            _fetch_branch(
                cache,
                branch_ref,
                config,
                depth=None,
                unshallow=_is_shallow(cache, config),
                progress=progress,
            )
            analyzed = _cached_commit(cache, spec.commit_sha, config)
        if analyzed is None:
            if progress:
                progress("final fallback: fetching all branch histories without tags")
            _run_git(
                [
                    "--git-dir", str(cache), "fetch", "--no-tags", "origin",
                    "+refs/heads/*:refs/heads/*",
                ],
                config,
                retry=True,
                progress=progress,
            )
            analyzed = _cached_commit(cache, spec.commit_sha, config)
        if analyzed is None:
            details = f"; direct fetch: {direct_error}" if direct_error else ""
            raise AcquisitionError(
                "commit_unavailable",
                f"Requested commit {spec.commit_sha} is unavailable after bounded targeted fetches{details}",
                stderr=direct_error.stderr if direct_error else "",
            )
        return RevisionPreparation(
            analyzed, spec.commit_sha, default_branch, "git_fetch_frozen", True,
            fetch_timestamp, True, False, True,
        )

    branch_ref, remote_sha = _remote_head(spec.url, config, progress)
    default_branch = branch_ref.removeprefix("refs/heads/")
    cached_remote = _cached_commit(cache, remote_sha, config)
    if cached_remote is not None:
        if progress:
            progress("cache hit; commit already available locally")
            progress("remote SHA unchanged; no fetch required")
        _run_git(
            ["--git-dir", str(cache), "update-ref", branch_ref, remote_sha], config
        )
        _run_git(["--git-dir", str(cache), "symbolic-ref", "HEAD", branch_ref], config)
        return RevisionPreparation(
            cached_remote, branch_ref, default_branch, "git_cache", True, None,
            False, True, False,
        )
    if progress:
        progress("remote SHA changed; fetching one targeted branch")
        progress(f"fetching {default_branch} at {remote_sha[:12]}")
    _fetch_branch(cache, branch_ref, config, depth=1, progress=progress)
    _run_git(["--git-dir", str(cache), "symbolic-ref", "HEAD", branch_ref], config)
    fetch_timestamp = _utc_now()
    analyzed = _resolve_commit(cache, branch_ref, config)
    if analyzed != remote_sha:
        raise AcquisitionError(
            "remote_resolution_mismatch",
            f"Fetched {branch_ref} resolved to {analyzed}, but remote HEAD reported {remote_sha}",
        )
    return RevisionPreparation(
        analyzed, branch_ref, default_branch, "git_fetch", True, fetch_timestamp,
        True, False, True,
    )


def _quarantine_cache(
    cache: Path,
    reason: str,
    progress: Callable[[str], None] | None = None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = cache.with_name(
        f"{cache.name}.invalid_{timestamp}_{uuid.uuid4().hex[:8]}"
    )
    try:
        cache.replace(target)
    except OSError as exc:
        raise AcquisitionError(
            "cache_quarantine_failed",
            f"Invalid cache could not be quarantined: {cache}: {exc}",
            stderr=str(exc),
        ) from exc
    if progress:
        progress(f"invalid cache quarantined at {target.name}: {reason}")
    return target


def _initialize_cache_with_revision(
    cache: Path,
    spec: RepositorySpec,
    mode: str,
    config: AnalysisConfig,
    progress: Callable[[str], None] | None = None,
) -> RevisionPreparation:
    """Build and populate a first cache off-path, then promote it atomically."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    owned_root = Path(
        tempfile.mkdtemp(prefix=f".{cache.stem}.fetching_", dir=cache.parent)
    )
    staging = owned_root / "objects.git"
    try:
        if progress:
            progress("initializing targeted bare cache in staging")
        _run_git(["init", "--bare", str(staging)], config)
        _run_git(
            ["--git-dir", str(staging), "remote", "add", "origin", spec.url], config
        )
        _verify_cache_identity(staging, spec.url, config, allow_empty=True)
        prepared = _prepare_revision(spec, mode, staging, config, progress)
        resolved = _resolve_commit(staging, prepared.analyzed_commit_sha, config)
        if resolved != prepared.analyzed_commit_sha:
            raise AcquisitionError(
                "cache_initialization_failed",
                f"Staged cache resolved {resolved}, expected {prepared.analyzed_commit_sha}",
            )
        marker = {
            "repository_url": canonicalize_github_url(spec.url),
            "initialized_at": _utc_now(),
            "commit_sha": prepared.analyzed_commit_sha,
        }
        (staging / _CACHE_COMPLETE_MARKER).write_text(
            json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
        )
        _verify_cache_identity(staging, spec.url, config, allow_empty=True)
        if cache.exists():
            _verify_cache_identity(cache, spec.url, config)
            return _prepare_revision(spec, mode, cache, config, progress)
        staging.replace(cache)
        _verify_cache_identity(cache, spec.url, config)
        if progress:
            progress("initialized new persistent cache")
        return prepared
    finally:
        if owned_root.exists():
            try:
                shutil.rmtree(owned_root)
            except OSError as exc:
                failed = owned_root.with_name(
                    f"{owned_root.name}.failed_{uuid.uuid4().hex[:8]}"
                )
                try:
                    owned_root.replace(failed)
                    if progress:
                        progress(
                            f"failed staging cache quarantined at {failed.name}: {exc}"
                        )
                except OSError:
                    if progress:
                        progress(f"failed staging cache cleanup warning: {exc}")


@contextmanager
def _acquire_repository_impl(
    spec: RepositorySpec,
    config: AnalysisConfig,
    mode: str = "latest",
    progress: Callable[[str], None] | None = None,
) -> Iterator[AcquiredRepository]:
    """Yield a clean detached checkout and always remove only program-owned temp data."""
    acquisition_started = time.perf_counter()
    if mode not in {"latest", "frozen", "offline"}:
        raise ValueError(f"Unsupported acquisition mode: {mode}")
    effective_mode = "frozen" if spec.commit_sha and mode == "latest" else mode
    if effective_mode == "frozen" and not spec.commit_sha:
        raise AcquisitionError("missing_commit", "Frozen mode requires commit_sha")
    if shutil.which("git") is None:
        raise AcquisitionError("git_unavailable", "Git is required for repository acquisition")

    cache = cache_path_for_url(config.cache_root, spec.url)
    with _cache_lock(cache):
        if cache.exists():
            try:
                _verify_cache_identity(cache, spec.url, config)
            except AcquisitionError as exc:
                _quarantine_cache(cache, f"{exc.error_type}: {exc}", progress)
                if effective_mode == "offline":
                    raise AcquisitionError(
                        "invalid_cache",
                        f"Offline cache was invalid and has been quarantined: {exc}",
                    ) from exc
                prepared = _initialize_cache_with_revision(
                    cache, spec, effective_mode, config, progress
                )
                cache_status = "recreated_after_quarantine"
            else:
                cache_status = "reused"
                prepared = _prepare_revision(
                    spec, effective_mode, cache, config, progress
                )
        elif effective_mode == "offline":
            raise AcquisitionError("cache_unavailable", f"Offline cache is unavailable for {spec.url}")
        else:
            prepared = _initialize_cache_with_revision(
                cache, spec, effective_mode, config, progress
            )
            cache_status = "created"

    analyzed = prepared.analyzed_commit_sha
    acquisition_duration = time.perf_counter() - acquisition_started

    temp_parent = config.temporary_directory
    if temp_parent is not None:
        temp_parent.mkdir(parents=True, exist_ok=True)
    checkout_root = Path(
        tempfile.mkdtemp(prefix="metrolith_checkout_", dir=temp_parent)
    )
    checkout = checkout_root / "repository"
    record: AcquisitionRecord | None = None
    try:
        worktree_started = time.perf_counter()
        if progress:
            progress("creating detached worktree")
        with _cache_lock(cache):
            _run_git(
                ["--git-dir", str(cache), "worktree", "add", "--detach", str(checkout), analyzed],
                config,
            )
        head = _run_git(["-C", str(checkout), "rev-parse", "HEAD"], config).stdout.strip().lower()
        if head != analyzed:
            raise AcquisitionError(
                "commit_verification_failed", f"Checkout HEAD is {head}, expected {analyzed}"
            )
        dirty = _checkout_status(checkout, config)
        if dirty:
            dirty_diagnostics = _dirty_checkout_diagnostics(checkout, dirty, config)
            raise AcquisitionError(
                "checkout_not_clean",
                f"Isolated checkout unexpectedly contains changes: {dirty[:1000]}",
                diagnostics=dirty_diagnostics,
            )
        if progress:
            progress(f"worktree ready in {time.perf_counter() - worktree_started:.1f}s")
        worktree_duration = time.perf_counter() - worktree_started
        _ACQUISITION_DEADLINE.set(None)
        owner, name = spec.owner, spec.repository_name
        record = AcquisitionRecord(
            repository_url=spec.url,
            repository_owner=owner,
            repository_name=name,
            requested_commit_sha=spec.commit_sha,
            analyzed_commit_sha=analyzed,
            resolved_ref=prepared.resolved_ref,
            default_branch=prepared.default_branch,
            acquisition_mode=effective_mode,
            cache_status=cache_status,
            remote_checked=prepared.remote_checked,
            fetch_timestamp=prepared.fetch_timestamp,
            checkout_timestamp=_utc_now(),
            commit_verification_status="verified",
            fetch_method=prepared.fetch_method,
            cache_hit=cache_status == "reused" and prepared.cached_commit_available,
            cache_created=cache_status in {"created", "recreated_after_quarantine"},
            cache_updated=cache_status == "reused" and prepared.cache_updated,
            network_contacted=prepared.remote_checked,
            fetch_performed=prepared.fetch_performed,
            cached_commit_available=prepared.cached_commit_available,
            acquisition_duration_seconds=round(acquisition_duration, 6),
            worktree_duration_seconds=round(worktree_duration, 6),
            git_commands=_COMMAND_LOG.get() or [],
        )
        yield AcquiredRepository(checkout, record)
        post_analysis_dirty = _checkout_status(checkout, config)
        if post_analysis_dirty:
            record.post_analysis_checkout_status = "dirty"
            dirty_diagnostics = _dirty_checkout_diagnostics(
                checkout, post_analysis_dirty, config
            )
            raise AcquisitionError(
                "checkout_not_clean",
                "Isolated checkout contains changes after analysis: "
                f"{post_analysis_dirty[:1000]}",
                diagnostics=dirty_diagnostics,
            )
        record.post_analysis_checkout_status = "clean"
    finally:
        _ACQUISITION_DEADLINE.set(None)
        cleanup_started = time.perf_counter()
        if progress:
            progress("cleanup started")
        cleanup_errors: list[str] = []
        if checkout.exists():
            try:
                with _cache_lock(cache):
                    _run_git(
                        ["--git-dir", str(cache), "worktree", "remove", "--force", str(checkout)],
                        config,
                    )
            except AcquisitionError as exc:
                cleanup_errors.append(f"worktree remove: {exc}")
        if checkout_root.exists():
            try:
                shutil.rmtree(checkout_root)
            except OSError as exc:
                cleanup_errors.append(f"checkout directory removal: {type(exc).__name__}: {exc}")
        try:
            with _cache_lock(cache):
                _run_git(["--git-dir", str(cache), "worktree", "prune"], config)
        except AcquisitionError as exc:
            cleanup_errors.append(f"worktree prune: {exc}")
        if record is not None:
            record.cleanup_errors.extend(cleanup_errors)
            record.cleanup_status = "failed" if cleanup_errors else "complete"
            record.cleanup_duration_seconds = round(
                time.perf_counter() - cleanup_started, 6
            )
        if progress:
            state = "failed" if cleanup_errors else "completed"
            progress(f"cleanup {state} in {time.perf_counter() - cleanup_started:.1f}s")


@contextmanager
def acquire_repository(
    spec: RepositorySpec,
    config: AnalysisConfig,
    mode: str = "latest",
    progress: Callable[[str], None] | None = None,
) -> Iterator[AcquiredRepository]:
    """Acquire under one command log and one monotonic repository deadline."""
    command_log: list[list[str]] = []
    command_token = _COMMAND_LOG.set(command_log)
    deadline_token = _ACQUISITION_DEADLINE.set(
        time.monotonic() + config.acquisition_deadline_seconds
    )
    try:
        with _acquire_repository_impl(spec, config, mode, progress) as acquired:
            acquired.record.git_commands = command_log
            yield acquired
    except AcquisitionError as exc:
        exc.commands = list(command_log)
        raise
    finally:
        _ACQUISITION_DEADLINE.reset(deadline_token)
        _COMMAND_LOG.reset(command_token)
