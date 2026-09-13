"""NUL-safe, read-only Git tree change and zero-context hunk extraction."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from modules.config import git_command_prefix
from modules.revision_source import ResolvedRevisionPair


class GitExtractionFailed(RuntimeError):
    """The committed-tree change set could not be extracted completely."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True, slots=True)
class HunkRange:
    start_line: int
    line_count: int


@dataclass(frozen=True, slots=True)
class GitHunk:
    ordinal: int
    base: HunkRange
    head: HunkRange
    deleted_diff_lines: int
    added_diff_lines: int


@dataclass(frozen=True, slots=True)
class GitFileChange:
    base_path: str | None
    head_path: str | None
    change_kind: str
    old_mode: str
    new_mode: str
    old_object: str
    new_object: str
    hunk_status: str = "complete"
    hunk_unavailable_reason: str | None = None
    hunks: tuple[GitHunk, ...] = ()


@dataclass(frozen=True, slots=True)
class GitChangeSet:
    status: str
    file_changes: tuple[GitFileChange, ...]


_HUNK_HEADER = re.compile(
    rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)
_KIND_RANK = {
    "deleted": 0,
    "modified": 1,
    "type_changed": 2,
    "renamed_exact": 3,
    "added": 4,
}


def _environment() -> dict[str, str]:
    value = dict(os.environ)
    value.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    })
    return value


def _git(
    source: Path,
    arguments: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [
                *git_command_prefix(),
                "-c", "diff.renames=false",
                "-C", str(source),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=False,
            timeout=timeout,
            env=_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitExtractionFailed("git_process_failed", type(exc).__name__) from exc


def _path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _parse_raw(value: bytes) -> list[GitFileChange]:
    fields = value.split(b"\0")
    found: list[GitFileChange] = []
    index = 0
    while index < len(fields):
        header = fields[index]
        index += 1
        if not header:
            continue
        if not header.startswith(b":") or index >= len(fields):
            raise GitExtractionFailed("raw_diff_malformed")
        metadata = header[1:].split()
        if len(metadata) != 5:
            raise GitExtractionFailed("raw_diff_malformed")
        old_mode, new_mode, old_object, new_object, raw_status = (
            item.decode("ascii", errors="strict") for item in metadata
        )
        path = _path(fields[index])
        index += 1
        status = raw_status[:1]
        if status == "A":
            base_path, head_path, kind = None, path, "added"
        elif status == "D":
            base_path, head_path, kind = path, None, "deleted"
        elif status == "M":
            base_path, head_path, kind = path, path, "modified"
        elif status == "T":
            base_path, head_path, kind = path, path, "type_changed"
        else:
            raise GitExtractionFailed("raw_diff_status_unsupported", raw_status)
        found.append(GitFileChange(
            base_path=base_path,
            head_path=head_path,
            change_kind=kind,
            old_mode=old_mode,
            new_mode=new_mode,
            old_object=old_object,
            new_object=new_object,
        ))
    return found


def _pair_exact_renames(changes: Iterable[GitFileChange]) -> list[GitFileChange]:
    values = list(changes)
    deleted_by_object: dict[str, list[GitFileChange]] = {}
    added_by_object: dict[str, list[GitFileChange]] = {}
    for item in values:
        if item.change_kind == "deleted":
            deleted_by_object.setdefault(item.old_object, []).append(item)
        elif item.change_kind == "added":
            added_by_object.setdefault(item.new_object, []).append(item)

    paired: set[GitFileChange] = set()
    renames: list[GitFileChange] = []
    for object_id in sorted(set(deleted_by_object) & set(added_by_object)):
        deleted = deleted_by_object[object_id]
        added = added_by_object[object_id]
        # Duplicate blobs do not prove a path correspondence.  Leave ambiguous
        # populations as added/deleted units instead of letting Git guess.
        if len(deleted) != 1 or len(added) != 1:
            continue
        before, after = deleted[0], added[0]
        paired.update((before, after))
        renames.append(GitFileChange(
            base_path=before.base_path,
            head_path=after.head_path,
            change_kind="renamed_exact",
            old_mode=before.old_mode,
            new_mode=after.new_mode,
            old_object=before.old_object,
            new_object=after.new_object,
        ))
    return [item for item in values if item not in paired] + renames


def _numstat_binary(
    pair: ResolvedRevisionPair,
    item: GitFileChange,
    *,
    timeout: int,
) -> bool | None:
    path = item.head_path or item.base_path
    if path is None:
        return None
    completed = _git(
        pair.source,
        [
            "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff",
            "--no-textconv", pair.base.sha, pair.head.sha, "--", path,
        ],
        timeout=timeout,
    )
    if completed.returncode != 0:
        return None
    records = [record for record in completed.stdout.split(b"\0") if record]
    if not records:
        # A mode-only change has no source-line delta and is still complete.
        return False
    prefix = records[0].split(b"\t", 2)
    if len(prefix) < 2:
        return None
    return prefix[0] == b"-" and prefix[1] == b"-"


def _extract_hunks(
    pair: ResolvedRevisionPair,
    item: GitFileChange,
    *,
    timeout: int,
) -> GitFileChange:
    if item.change_kind == "renamed_exact" or item.old_object == item.new_object:
        return item
    binary = _numstat_binary(pair, item, timeout=timeout)
    if binary is None:
        return replace(
            item,
            hunk_status="unavailable",
            hunk_unavailable_reason="git_numstat_unavailable",
        )
    if binary:
        return replace(
            item,
            hunk_status="unavailable",
            hunk_unavailable_reason="binary_content",
        )

    path = item.head_path or item.base_path
    if path is None:
        raise GitExtractionFailed("file_change_path_missing")
    completed = _git(
        pair.source,
        [
            "diff", "--unified=0", "--no-color", "--no-renames",
            "--no-ext-diff", "--no-textconv", pair.base.sha, pair.head.sha,
            "--", path,
        ],
        timeout=timeout,
    )
    if completed.returncode != 0:
        return replace(
            item,
            hunk_status="unavailable",
            hunk_unavailable_reason="git_hunk_extraction_failed",
        )
    hunks: list[GitHunk] = []
    for line in completed.stdout.splitlines():
        match = _HUNK_HEADER.match(line)
        if match is None:
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        hunks.append(GitHunk(
            ordinal=len(hunks) + 1,
            base=HunkRange(old_start, old_count),
            head=HunkRange(new_start, new_count),
            deleted_diff_lines=old_count,
            added_diff_lines=new_count,
        ))
    return replace(item, hunks=tuple(hunks))


def _sort_key(item: GitFileChange) -> tuple[str, str, int]:
    # Missing base paths sort after present base paths.
    base = item.base_path if item.base_path is not None else "\U0010ffff"
    return (base, item.head_path or "", _KIND_RANK[item.change_kind])


def extract_git_changes(
    pair: ResolvedRevisionPair,
    *,
    timeout: int = 300,
) -> GitChangeSet:
    """Extract an exact direct tree(base)->tree(head) change set."""
    completed = _git(
        pair.source,
        [
            "diff", "--raw", "-z", "--no-abbrev", "--no-renames",
            "--no-ext-diff", "--no-textconv", pair.base.sha, pair.head.sha,
        ],
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise GitExtractionFailed("raw_diff_failed")
    raw = _parse_raw(completed.stdout)
    changes = _pair_exact_renames(raw)
    with_hunks = [
        _extract_hunks(pair, item, timeout=timeout) for item in changes
    ]
    return GitChangeSet(
        status="complete",
        file_changes=tuple(sorted(with_hunks, key=_sort_key)),
    )


__all__ = [
    "GitChangeSet",
    "GitExtractionFailed",
    "GitFileChange",
    "GitHunk",
    "HunkRange",
    "extract_git_changes",
]
