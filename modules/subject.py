"""Logical subject identity, source locators, and analyzed-scope identity.

Artifact Schema 1.6 and earlier used ``repository_url`` as the de-facto primary
key: catalog joins, comparison, diagnostics lookup and the performance cohort
hash all keyed on it. That only worked because every subject was a remote GitHub
URL. The moment a subject can be a local directory, the URL is either absent or
meaningless, and a key that is sometimes absent is not a key.

So four concepts that were tangled together are separated here:

``subject_key``
    **Logical identity.** What makes two analyses analyses *of the same
    software*, across revisions, across runs, and across source modes. This is
    the join key.

``repository_url``
    **Source locator**, now nullable. Where bytes came from. Useful for
    provenance and as a selector; never the identity.

``analysis_scope_hash`` / ``analyzed_commit_sha``
    **Analyzed revision or snapshot identity.** *Which bytes* were measured.

``source_mode``
    **How** the bytes were obtained.

The distinctions are load-bearing:

* same ``subject_key`` does **not** imply the same bytes — that is the whole
  point of comparing two revisions of one project;
* same bytes do **not** imply the same subject — two unrelated projects can
  contain identical files, and a vendored copy is not its upstream;
* a different ``source_mode`` does **not** make two analyses incomparable — a
  revision fetched from a remote and the same revision read from a local clone
  are the same measurement of the same bytes.

Content hash is deliberately **not** used as ``subject_key``. Two revisions of
the same software must keep the same logical identity, and a content-derived key
would give every commit a new one.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from modules.vocabularies import (
    SourceMode,
    SubjectKeyBasis,
    WorkingTreeState,
)

#: Version of the `analysis_scope_hash` construction. A change to what the hash
#: covers changes what equality means, so it is recorded alongside the digest
#: rather than left implicit.
#:
#: 2.0.0 — the raw Git file mode was replaced by a derived metric-relevant file
#: kind (:func:`scope_file_kind`). 1.0.0 hashed provenance: a checkout could
#: observe ``100644`` where a ``git archive`` materialization of the same commit
#: could observe nothing, so byte-identical regular files at one commit produced
#: two different digests. No production path reads a regular file's mode. This
#: is a construction correction, not a contract change: the persisted field and
#: its meaning are unchanged, which is why Artifact Schema stays at 1.7.0 and
#: this version exists to carry the difference.
ANALYSIS_SCOPE_HASH_VERSION = "2.0.0"

_GITHUB_LIKE = re.compile(
    r"^(?:https?://|git@|ssh://git@)"
    r"(?P<host>[^/:]+)[:/]"
    r"(?P<owner>[^/]+)/"
    r"(?P<name>[^/]+?)(?:\.git)?/?$"
)


def canonical_remote_subject_key(locator: str) -> str | None:
    """Canonical portable identity for a remote Git locator.

    ``https://github.com/acme/app``, ``git@github.com:acme/app.git`` and
    ``ssh://git@github.com/acme/app`` are the same subject, so they must produce
    the same key — otherwise the same project analyzed over two transports would
    look like two subjects.
    """
    match = _GITHUB_LIKE.match((locator or "").strip())
    if match is None:
        return None
    host = match.group("host").lower()
    owner = match.group("owner").lower()
    name = match.group("name").lower()
    if not host or not owner or not name:
        return None
    return f"{host}/{owner}/{name}"


def _git(arguments: list[str], cwd: Path) -> str | None:
    """Run a read-only Git command, or return None. Never raises."""
    from modules.config import git_command_prefix

    try:
        completed = subprocess.run(
            [*git_command_prefix(), "-C", str(cwd), *arguments],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def git_origin_locator(path: Path) -> str | None:
    """The push/fetch origin of a local Git repository, if it has a usable one."""
    origin = _git(["remote", "get-url", "origin"], path)
    return origin or None


def local_fallback_subject_key(path: Path) -> str:
    """Deterministic local identity for a subject with no portable identity.

    Explicitly **not portable**: it is derived from the resolved local path, so
    the same project checked out at two locations, or on two machines, produces
    two keys. That is recorded through
    :attr:`SubjectKeyBasis.LOCAL_FALLBACK` rather than papered over, because a
    host-derived key presented as stable identity would silently corrupt any
    cross-machine join.

    The path is hashed rather than embedded so an absolute host path does not
    leak into an artifact that may be shared.
    """
    resolved = str(Path(path).resolve()).replace("\\", "/").casefold()
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return f"local:{Path(path).resolve().name.casefold()}:{digest}"


@dataclass(frozen=True)
class SubjectIdentity:
    """Resolved identity for one analyzed subject."""

    subject_key: str
    subject_key_basis: SubjectKeyBasis
    source_mode: SourceMode
    #: Nullable source/origin locator. Never the identity.
    repository_url: str | None = None
    #: Absolute local path, when there is one. **Execution/provenance evidence
    #: only** — host-specific, never semantic, never part of any hash.
    local_source_path: str | None = None

    @property
    def portable(self) -> bool:
        return self.subject_key_basis.is_portable

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "subject_key_basis": self.subject_key_basis.value,
            "subject_key_portable": self.portable,
            "source_mode": self.source_mode.value,
            "repository_url": self.repository_url,
        }


def resolve_subject_identity(
    *,
    source_mode: SourceMode,
    explicit_subject_key: str | None = None,
    repository_url: str | None = None,
    local_path: Path | None = None,
) -> SubjectIdentity:
    """Resolve identity by the agreed precedence.

    1. an explicit user-provided key — the mechanism by which a user declares
       that a remote revision and a local clone are the same subject;
    2. canonical remote repository identity;
    3. canonical Git origin identity for a local repository that has one;
    4. a deterministic local fallback, marked non-portable.
    """
    if explicit_subject_key and explicit_subject_key.strip():
        return SubjectIdentity(
            subject_key=explicit_subject_key.strip(),
            subject_key_basis=SubjectKeyBasis.EXPLICIT,
            source_mode=source_mode,
            repository_url=repository_url,
            local_source_path=str(local_path.resolve()) if local_path else None,
        )

    if repository_url:
        canonical = canonical_remote_subject_key(repository_url)
        if canonical:
            return SubjectIdentity(
                subject_key=canonical,
                subject_key_basis=SubjectKeyBasis.REMOTE_LOCATOR,
                source_mode=source_mode,
                repository_url=repository_url,
                local_source_path=str(local_path.resolve()) if local_path else None,
            )

    if local_path is not None:
        origin = git_origin_locator(Path(local_path))
        if origin:
            canonical = canonical_remote_subject_key(origin)
            if canonical:
                return SubjectIdentity(
                    subject_key=canonical,
                    subject_key_basis=SubjectKeyBasis.GIT_ORIGIN,
                    source_mode=source_mode,
                    repository_url=origin,
                    local_source_path=str(Path(local_path).resolve()),
                )
        return SubjectIdentity(
            subject_key=local_fallback_subject_key(Path(local_path)),
            subject_key_basis=SubjectKeyBasis.LOCAL_FALLBACK,
            source_mode=source_mode,
            repository_url=repository_url,
            local_source_path=str(Path(local_path).resolve()),
        )

    # No locator and no path: the caller gave us nothing portable to key on.
    return SubjectIdentity(
        subject_key=f"unidentified:{hashlib.sha256(b'').hexdigest()[:16]}",
        subject_key_basis=SubjectKeyBasis.LOCAL_FALLBACK,
        source_mode=source_mode,
        repository_url=repository_url,
    )


# ---------------------------------------------------- analyzed scope hash ---


def compute_analysis_scope_hash(inventory: Iterable[Any]) -> str:
    """**Exact analyzed-scope identity**: the materialization Metrolith measured.

    Covers, for every file ``included_in_metrics``:

    * the normalized relative path,
    * the exact content hash **of the bytes that were parsed**,
    * the Git file mode and symlink evidence where Metrolith semantics make those
      relevant (a file and a symlink to it are not the same source).

    Deliberately excludes absolute host paths, temporary worktree paths,
    timestamps and cache state: those describe the execution, not the source.

    **Equality proves the analyzed source scope was identical. Inequality does
    not, by itself, make two runs incomparable.**

    That distinction is load-bearing, and a concrete case makes it obvious. On
    Windows a checked-out file is typically CRLF on disk, while
    :data:`SourceMode.LOCAL_GIT_REVISION` materializes canonical LF bytes from
    the object database. A *clean* worktree at commit X and an exact
    materialization of commit X therefore analyze genuinely different bytes and
    produce genuinely different digests — while being the same subject at the
    same revision. Recording that as a difference is evidence; engineering it
    away would be manufacturing an equality that does not exist.

    Comparability is multidimensional, and these dimensions are kept separate:

    ============================  ==========================================
    ``subject_key``               logical subject identity
    ``analyzed_commit_sha``       Git revision identity, where available
    ``analysis_scope_hash``       exact analyzed-materialization identity
    contract versions             metric/parser/exclusion compatibility
    ============================  ==========================================

    Collapsing them into "same scope hash means comparable" is exactly the
    over-simplification the identity model exists to remove.

    **The bytes hashed are the bytes parsed.** No normalization happens here and
    none may be introduced: hashing normalized bytes while measuring different
    ones would make the digest describe a source that was never analyzed. A
    representation-normalized identity would need defensible answers for
    ``.gitattributes``, ``working-tree-encoding``, clean/smudge filters, binary
    files, symlinks and exec bits — that is a separate concept for a later
    phase, not a partial reimplementation of Git normalization bolted on to
    manufacture equality.

    This is **not** a broad filesystem manifest either. Files Metrolith excludes
    cannot affect the metrics, so they cannot affect this hash; see
    :func:`compute_filesystem_manifest_hash`.
    """
    entries: list[str] = []
    for record in inventory:
        if not getattr(record, "included_in_metrics", False):
            continue
        relative = str(record.relative_path).replace("\\", "/")
        entries.append("\x1f".join([
            relative,
            str(record.content_hash or ""),
            scope_file_kind(record),
            str(getattr(record, "git_symlink_target", None) or ""),
        ]))
    payload = "\n".join(sorted(entries))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


#: Exclusion reasons that describe *what kind of filesystem entry this is*,
#: rather than a policy decision about an ordinary file.
_FILE_KIND_BY_EXCLUSION_REASON = {
    "git_submodule": "submodule",
    "git_symlink": "symlink",
    "symlink": "symlink",
}


def scope_file_kind(record: Any) -> str:
    """Metric-relevant file kind: ``regular``, ``symlink`` or ``submodule``.

    This replaced the raw Git mode in :func:`compute_analysis_scope_hash`, and
    the distinction it draws is the whole point.

    **What Git mode is actually used for.** Across the entire production tree,
    ``git_mode`` is only ever compared against ``120000`` (symlink) and
    ``160000`` (submodule). ``100644`` and ``100755`` appear in no comparison
    anywhere: no source-selection, exclusion, parsing or metric decision reads
    them. So for an ordinary file the mode is *provenance* — evidence Git
    happened to supply — and not a property of what was measured.

    **Why that mattered.** Hashing the raw mode made the scope hash record
    whether Git metadata was *observable*, not what was analyzed. A checkout
    reports ``100644``; a ``git archive`` materialization of the same commit is
    not a Git repository, so it reports nothing. Byte-identical regular files at
    one commit therefore produced two different scope digests purely because the
    two acquisition paths differ in what they can see about themselves. That is
    a false inequality in the one value whose stated purpose is exact
    analyzed-scope identity.

    **What is deliberately preserved.** File *kind* still participates, because
    a symlink and the file it points to are genuinely not the same source, and
    :func:`compute_analysis_scope_hash` also keeps ``git_symlink_target``. Today
    that is belt and braces: symlinks and submodules are the first exclusion
    reasons applied, so they are never ``included_in_metrics`` and never reach
    the digest at all. Deriving the kind rather than dropping the concept keeps
    the guarantee correct if that policy is ever revisited, and it works from
    native filesystem evidence when Git evidence is absent.

    **What is deliberately unchanged.** Content is hashed exactly as before: the
    bytes hashed remain the bytes parsed, with no normalization. A CRLF worktree
    and the canonical LF materialization of the same commit still produce
    different digests, which is the ratified behaviour and is not weakened here.
    """
    reason = getattr(record, "exclusion_reason", None)
    kind = _FILE_KIND_BY_EXCLUSION_REASON.get(reason)
    if kind is not None:
        return kind
    if getattr(record, "is_git_submodule", False):
        return "submodule"
    if getattr(record, "is_git_symlink", False):
        return "symlink"
    return "regular"


def compute_filesystem_manifest_hash(inventory: Iterable[Any]) -> str:
    """Broader filesystem evidence: every inventoried file, not just measured.

    Named separately and on purpose. It is **not** the content/comparability
    identity: it moves when an excluded file changes, which cannot affect any
    metric. Calling this a content or tree hash would invite exactly the
    false-inequality it is designed not to cause.
    """
    entries: list[str] = []
    for record in inventory:
        relative = str(record.relative_path).replace("\\", "/")
        entries.append("\x1f".join([
            relative,
            str(record.content_hash or ""),
            str(record.size_bytes if record.size_bytes is not None else ""),
            str(record.git_mode or ""),
            "1" if getattr(record, "included_in_metrics", False) else "0",
        ]))
    payload = "\n".join(sorted(entries))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


# ------------------------------------------------------- legacy read model ---


def legacy_subject_identity(repository_url: str | None) -> SubjectIdentity:
    """Normalize a historical 1.5/1.6 artifact's identity at **read time**.

    Old artifacts have no ``subject_key``; they only have a repository URL. A
    reader derives the same canonical key it would derive today, so a historical
    run and a current one join correctly — without rewriting a single byte of
    the historical artifact.
    """
    canonical = canonical_remote_subject_key(repository_url or "")
    if canonical:
        return SubjectIdentity(
            subject_key=canonical,
            subject_key_basis=SubjectKeyBasis.REMOTE_LOCATOR,
            source_mode=SourceMode.REMOTE_GIT_REVISION,
            repository_url=repository_url,
        )
    digest = hashlib.sha256((repository_url or "").encode("utf-8")).hexdigest()[:16]
    return SubjectIdentity(
        subject_key=f"legacy:{digest}",
        subject_key_basis=SubjectKeyBasis.LOCAL_FALLBACK,
        source_mode=SourceMode.REMOTE_GIT_REVISION,
        repository_url=repository_url,
    )


def subject_key_of(result: Any) -> str:
    """Read a subject key from any artifact generation.

    Artifact 1.7 records it directly. Older artifacts get the same canonical key
    derived from their repository URL, so joins work across generations without
    every call site knowing which generation it is holding.

    The mapping test is :class:`collections.abc.Mapping`, not ``dict``. It was
    ``dict``, and the artifact reader hands out ``mappingproxy``: a recorded key
    read straight from :attr:`ImmutableRunView.repositories` therefore missed
    this branch, fell through to the attribute lookup, found no
    ``.repository_url`` attribute either, and returned the URL-derived fallback
    for an empty URL. Every such subject silently collapsed onto the SAME
    ``legacy:e3b0c44298fc1c14`` key — so a lookup keyed on it matched nothing and
    a sort keyed on it did nothing, in both cases without raising. Call sites
    that already copy with ``dict(...)`` were working around exactly this; the
    copies remain correct and are now redundant.
    """
    if isinstance(result, Mapping):
        recorded = result.get("subject_key")
        if recorded:
            return str(recorded)
        return legacy_subject_identity(result.get("repository_url")).subject_key
    recorded = getattr(result, "subject_key", None)
    if recorded:
        return str(recorded)
    return legacy_subject_identity(getattr(result, "repository_url", None)).subject_key
