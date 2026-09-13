"""Local source acquisition: exact revisions, worktree snapshots, plain directories.

Every mode materializes an **Metrolith-controlled snapshot** and measures that.
Nothing parses files that remain live and mutable under the user's editor, and
nothing writes to the user's repository.

Why not ``git worktree add``
============================

The obvious way to check out a revision from a local repository is
``git worktree add``. It is rejected here because it **mutates the user's
repository**: it records metadata under ``.git/worktrees/``, and a crashed or
killed run leaves that behind for the user to discover and prune. Analysis is a
read-only question and must not leave scars on its subject.

Instead the revision is streamed out with ``git archive``, which reads the object
database and writes a tarball to Metrolith-controlled storage. No refs, no index,
no worktree metadata, no network.

What "snapshot" claims, and what it does not
============================================

For :data:`SourceMode.LOCAL_WORKTREE_SNAPSHOT` Metrolith copies the selected
files and then analyzes and hashes **the copy it captured**. It does not claim
that copy is an atomic point-in-time image of the filesystem: files can change
while the copy proceeds, and no ordinary filesystem API prevents that. The
honest statement — and the one made in the artifact — is that Metrolith analyzed
the snapshot it materialized, and ``analysis_scope_hash`` describes exactly that
snapshot.

Default scope for a worktree snapshot
=====================================

Tracked files, **plus** untracked files that Git is not ignoring, then the
normal Metrolith exclusion policy on top. Git-ignored content is excluded by
default, because otherwise merely having built the project locally would drag
``node_modules/``, ``dist/``, ``.venv/`` and every build cache into the
measurement — producing numbers that describe the developer's machine rather
than the software.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from modules.vocabularies import SourceMode, WorkingTreeState


class LocalSourceError(RuntimeError):
    """A local source could not be prepared. Never a partial snapshot."""


@dataclass(frozen=True)
class LocalSnapshot:
    """A materialized, Metrolith-controlled copy of a local source."""

    path: Path
    source_mode: SourceMode
    working_tree_state: WorkingTreeState
    #: Commit analyzed, when the mode is an exact revision.
    analyzed_commit_sha: str | None = None
    resolved_ref: str | None = None
    #: Evidence about the working tree, for provenance.
    tracked_file_count: int = 0
    untracked_included_count: int = 0
    modified_tracked_count: int = 0
    ignored_excluded: bool = True
    #: Internal inventory authority, never serialized as a new artifact.
    git_entries: dict[str, tuple[str, str]] | None = None
    native_links: dict[str, str | None] = field(default_factory=dict)

    @property
    def inventory_options(self) -> dict:
        return {"git_entries": self.git_entries, "native_links": self.native_links}


def _git(arguments: list[str], cwd: Path, *, binary: bool = False):
    from modules.config import git_command_prefix

    try:
        return subprocess.run(
            [*git_command_prefix(), "-C", str(cwd), *arguments],
            check=True,
            capture_output=True,
            text=not binary,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr if isinstance(exc.stderr, str) else ""
        raise LocalSourceError(
            f"git {' '.join(arguments[:2])} failed in {cwd}: {detail.strip()[:300]}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalSourceError(f"git is unusable: {exc}") from exc


def is_git_repository(path: Path) -> bool:
    from modules.config import git_command_prefix

    try:
        completed = subprocess.run(
            [*git_command_prefix(), "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalSourceError(f"git source inspection failed: {exc}") from exc
    if completed.returncode == 0:
        return completed.stdout.strip() == "true"
    if "not a git repository" in completed.stderr.lower():
        return False
    raise LocalSourceError(f"git source inspection failed: {completed.stderr.strip()[:300]}")


def classify_local_source(path: Path, *, revision: str | None) -> SourceMode:
    """Decide the source mode from the path and what was asked for."""
    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise LocalSourceError(f"not a directory: {resolved}")
    if not is_git_repository(resolved):
        if revision:
            raise LocalSourceError(
                f"--revision was requested but {resolved} is not a Git repository"
            )
        return SourceMode.LOCAL_DIRECTORY_SNAPSHOT
    return (
        SourceMode.LOCAL_GIT_REVISION if revision
        else SourceMode.LOCAL_WORKTREE_SNAPSHOT
    )


def _resolve_revision(repository: Path, revision: str) -> tuple[str, str]:
    try:
        sha = _git(["rev-parse", "--verify", f"{revision}^{{commit}}"], repository).stdout.strip()
    except LocalSourceError as exc:
        raise LocalSourceError(f"revision {revision!r} could not be resolved: {exc}") from exc
    if not sha:
        raise LocalSourceError(f"revision could not be resolved: {revision}")
    return sha, revision


def _materialize_revision(repository: Path, sha: str, destination: Path) -> None:
    """Stream a committed revision out of the object database.

    `git archive` reads objects and writes a tarball. It creates no ref, no
    index entry and no `.git/worktrees` metadata, so the user's repository is
    left exactly as it was found.
    """
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / f"{destination.name}.tar"
    completed = _git(["archive", "--format=tar", "-o", str(archive), sha], repository,
                     binary=True)
    del completed
    try:
        with tarfile.open(archive, "r") as handle:
            # `git archive` emits only regular files, symlinks and directories,
            # all repository-relative; still filtered so a crafted archive
            # cannot escape the destination.
            for member in handle:
                # Excluded links need no native-link privilege, and their
                # targets must never be extracted or dereferenced.
                if member.issym():
                    target = destination / member.name
                    target.resolve().relative_to(destination.resolve())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(member.linkname.encode("utf-8"))
                else:
                    handle.extract(member, destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise LocalSourceError(f"revision archive could not be expanded: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)


def _inspect(
    arguments: list[str], cwd: Path, *, nul_separated: bool = False
) -> list[str]:
    """Read worktree state **without** Metrolith's checkout configuration.

    `git_command_prefix()` forces `core.eol=lf` and `core.autocrlf=false` so
    that materialized bytes are identical on every platform. Applying those
    overrides to an *inspection* is wrong: on a repository whose own config
    differs, `git diff` then reports every file as modified and a pristine
    checkout looks dirty. State queries must observe the repository as the
    user's own Git sees it.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            check=True,
            capture_output=True,
            text=not nul_separated,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="backslashreplace")
        raise LocalSourceError(
            f"git {' '.join(arguments[:2])} enumeration failed: {str(detail).strip()[:300]}"
        ) from exc
    if nul_separated:
        stdout = completed.stdout
        if not isinstance(stdout, bytes):
            raise LocalSourceError("git enumeration returned non-binary path data")
        return [
            value
            for value in stdout.decode("utf-8", errors="surrogateescape").split("\0")
            if value
        ]
    return [line for line in completed.stdout.splitlines() if line]


def _git_entries(repository: Path, revision: str | None = None) -> dict[str, tuple[str, str]]:
    arguments = ["ls-tree", "-r", "-z", revision] if revision else ["ls-files", "--stage", "-z"]
    entries = {}
    for entry in _inspect(arguments, repository, nul_separated=True):
        try:
            metadata, path = entry.split("\t", 1)
            parts = metadata.split()
            mode, oid = parts[0], parts[2] if revision else parts[1]
            if not revision and parts[2] != "0":
                raise LocalSourceError(f"unmerged selected path: {path}")
            entries[path] = (mode, oid)
        except (ValueError, IndexError) as exc:
            raise LocalSourceError("git returned malformed file-kind enumeration") from exc
    return entries


def _worktree_files(repository: Path) -> tuple[dict[str, tuple[str, str]], list[str], list[str]]:
    """Tracked, non-ignored untracked, and modified-tracked paths."""
    # NUL-delimited output avoids core.quotePath escaping and preserves spaces,
    # newlines, and Unicode path text across Windows and POSIX hosts.
    tracked = _git_entries(repository)
    untracked = _inspect(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        repository,
        nul_separated=True,
    )
    modified = (
        _inspect(
            ["diff", "--name-only", "-z", "HEAD"],
            repository,
            nul_separated=True,
        )
        if tracked
        else []
    )
    return tracked, untracked, modified


def _copy_selected(repository: Path, relatives: list[str], destination: Path) -> int:
    copied = 0
    for relative in relatives:
        source = repository / relative
        target = destination / relative
        try:
            # Selection already established existence. A later disappearance
            # or change of kind cannot become an intentional deletion.
            if not stat.S_ISREG(source.lstat().st_mode):
                raise OSError("selected file changed kind during preparation")
            for parent in source.parents:
                if parent == repository:
                    break
                if parent.is_symlink() or parent.is_junction():
                    raise OSError("selected parent changed to a link")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            if target.is_symlink():
                raise OSError("selected file changed to a link during copying")
        except OSError as exc:
            raise LocalSourceError(
                f"selected file {relative!r} could not be copied: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
        copied += 1
    return copied


def _select_files(source: Path, destination: Path, relatives, entries, config):
    """Freeze existence before copying; preserve excluded kinds without targets."""
    from modules.inventory import _pruned_directory_reason

    selected = []
    links = {}
    for relative in relatives:
        path = source / relative
        rel = Path(relative)
        mode = entries.get(relative, (None, None))[0]
        excluded_parent = False
        for parent in reversed(rel.parents):
            if parent == Path('.'):
                continue
            original = source / parent
            reason = _pruned_directory_reason(parent, config.exclusion_policy)
            if reason or original.is_symlink() or original.is_junction() or (original / '.git').exists() or (original / '.hg').exists():
                (destination / parent).mkdir(parents=True, exist_ok=True)
                if original.is_symlink() or original.is_junction():
                    links[parent.as_posix()] = None
                elif not reason and ((original / '.git').exists() or (original / '.hg').exists()):
                    (destination / parent / '.git').touch()
                excluded_parent = True
                break
        if excluded_parent:
            continue
        try:
            info = path.lstat()
        except FileNotFoundError:
            # A tracked path already absent at selection is a worktree deletion.
            if relative in entries:
                continue
            raise LocalSourceError(f"selected path {relative!r} disappeared before copying") from None
        except OSError as exc:
            raise LocalSourceError(f"selected path {relative!r} could not be inspected: {exc}") from exc
        target = destination / relative
        native_link = stat.S_ISLNK(info.st_mode) or path.is_junction()
        if mode in {'120000', '160000'} or native_link:
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == '160000':
                target.mkdir(exist_ok=True)
            else:
                if native_link:
                    try:
                        link_target = os.readlink(path)
                    except OSError:
                        link_target = None
                    links[relative] = link_target
                    target.write_bytes((link_target or '').encode('utf-8'))
                else:
                    # This reads only the text representation, never its target.
                    try:
                        with path.open('rb') as handle:
                            payload = handle.read(4097)
                    except OSError:
                        payload = b''
                    target.write_bytes(payload)
            continue
        if stat.S_ISREG(info.st_mode):
            selected.append(relative)
        elif stat.S_ISDIR(info.st_mode) and ((path / '.git').exists() or (path / '.hg').exists()):
            target.mkdir(parents=True, exist_ok=True)
            (target / '.git').touch()
    return selected, links


def _directory_files(source: Path, destination: Path, config) -> list[str]:
    from modules.inventory import _pruned_directory_reason

    paths = []
    def onerror(exc):
        name = os.path.relpath(exc.filename or source, source)
        raise LocalSourceError(f"directory enumeration failed at {name!r}: {str(exc)[:300]}") from exc
    for root, dirs, files in os.walk(source, onerror=onerror, followlinks=False):
        for name in list(dirs):
            path = Path(root) / name
            relative = path.relative_to(source)
            if path.is_symlink() or path.is_junction():
                paths.append(relative.as_posix())
                dirs.remove(name)
            elif _pruned_directory_reason(relative, config.exclusion_policy) or (path / '.git').exists() or (path / '.hg').exists():
                (destination / relative).mkdir(parents=True, exist_ok=True)
                if not _pruned_directory_reason(relative, config.exclusion_policy):
                    (destination / relative / '.git').touch()
                dirs.remove(name)
        paths.extend((Path(root) / name).relative_to(source).as_posix() for name in files)
    return paths


@contextmanager
def acquire_local_repository(spec, config, mode="latest", progress=None):
    """Local acquisition shaped exactly like remote acquisition.

    Yields the same ``AcquiredRepository`` the remote path yields, so
    ``analyze`` routes through the *same* canonical inventory, measurement and
    artifact machinery as batch execution. Duplicating the pipeline for local
    sources would create a second measurement path free to drift from the one
    the conformance corpus protects.
    """
    from modules.acquisition import AcquiredRepository, AcquisitionRecord
    from modules.run_artifacts import utc_now

    del mode
    if progress:
        progress("preparing local snapshot")

    with prepare_local_source(
        Path(spec.local_path),
        revision=spec.revision,
        tracked_only=spec.tracked_only,
        config=config,
    ) as snapshot:
        if progress:
            progress(f"local snapshot ready ({snapshot.source_mode.value})")
        record = AcquisitionRecord(
            # Nullable now: a local subject may have no origin at all. Identity
            # lives in `subject_key`, not here.
            repository_url=spec.url or None,
            repository_owner=spec.owner,
            repository_name=spec.repository_name,
            requested_commit_sha=spec.revision,
            analyzed_commit_sha=snapshot.analyzed_commit_sha or "",
            resolved_ref=snapshot.resolved_ref or "",
            default_branch=None,
            acquisition_mode=snapshot.source_mode.value,
            cache_status="not_applicable",
            remote_checked=False,
            fetch_timestamp=None,
            checkout_timestamp=utc_now(),
            commit_verification_status=(
                "verified" if snapshot.source_mode.is_exact_revision
                else "not_applicable"
            ),
            fetch_method="local_snapshot",
            network_contacted=False,
        )
        yield AcquiredRepository(snapshot.path, record), snapshot


@contextmanager
def prepare_local_source(
    path: Path,
    *,
    revision: str | None = None,
    tracked_only: bool = False,
    config=None,
) -> Iterator[LocalSnapshot]:
    """Materialize a local source into Metrolith-controlled temporary storage."""
    source = Path(path).resolve()
    from modules.config import AnalysisConfig
    config = config or AnalysisConfig.from_env()
    mode = classify_local_source(source, revision=revision)

    holder = tempfile.TemporaryDirectory(prefix="metrolith_local_")
    try:
        snapshot_root = Path(holder.name) / "snapshot"
        snapshot_root.mkdir(parents=True, exist_ok=True)

        if mode is SourceMode.LOCAL_DIRECTORY_SNAPSHOT:
            # No Git evidence exists by construction. Copy the tree wholesale;
            # the ordinary Metrolith exclusion policy still applies during
            # inventory, so this is not a claim that everything is measured.
            relatives = _directory_files(source, snapshot_root, config)
            selected, links = _select_files(source, snapshot_root, relatives, {}, config)
            _copy_selected(source, selected, snapshot_root)
            yield LocalSnapshot(
                path=snapshot_root,
                source_mode=mode,
                working_tree_state=WorkingTreeState.NOT_APPLICABLE,
                native_links=links,
            )
            return

        if mode is SourceMode.LOCAL_GIT_REVISION:
            sha, ref = _resolve_revision(source, str(revision))
            entries = _git_entries(source, sha)
            _materialize_revision(source, sha, snapshot_root)
            yield LocalSnapshot(
                path=snapshot_root,
                source_mode=mode,
                working_tree_state=WorkingTreeState.COMMITTED_REVISION,
                analyzed_commit_sha=sha,
                resolved_ref=ref,
                git_entries=entries,
            )
            return

        # LOCAL_WORKTREE_SNAPSHOT
        tracked, untracked, modified = _worktree_files(source)
        relatives = list(tracked) if tracked_only else [*tracked, *untracked]
        selected, links = _select_files(source, snapshot_root, relatives, tracked, config)
        _copy_selected(source, selected, snapshot_root)

        head = _git(["rev-parse", "--verify", "HEAD"], source).stdout.strip() \
            if tracked else None
        included_untracked = 0 if tracked_only else len(untracked)
        dirty = bool(modified) or included_untracked > 0
        yield LocalSnapshot(
            path=snapshot_root,
            source_mode=mode,
            working_tree_state=(
                WorkingTreeState.DIRTY_WORKTREE if dirty
                else WorkingTreeState.CLEAN_WORKTREE
            ),
            analyzed_commit_sha=head or None,
            resolved_ref="HEAD" if head else None,
            tracked_file_count=len(tracked),
            untracked_included_count=included_untracked,
            modified_tracked_count=len(modified),
            ignored_excluded=True,
            git_entries=tracked,
            native_links=links,
        )
    finally:
        holder.cleanup()
