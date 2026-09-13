"""Collision-safe deterministic grouping for D2 lexical-exact clones."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from modules.duplication.lexical import LEXICAL_FINGERPRINT_VERSION, frame
from modules.duplication.model import (
    CloneDistribution,
    LexicalCloneGroup,
    LexicalOccurrence,
)


_GROUP_NAMESPACE = b"archlens-duplication-group"
_OCCURRENCE_NAMESPACE = b"archlens-duplication-occurrence"
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class GroupingInvariantError(RuntimeError):
    """Occurrence identity or canonical content violates grouping invariants."""


def validate_relative_path(relative_path: str) -> None:
    if not relative_path or "\x00" in relative_path or "\\" in relative_path:
        raise ValueError("occurrence path must be a non-empty relative POSIX path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or _WINDOWS_DRIVE.match(relative_path):
        raise ValueError("occurrence path must not be absolute")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("occurrence path must not contain empty, dot, or parent segments")
    if path.as_posix() != relative_path:
        raise ValueError("occurrence path is not canonical POSIX text")


def _uint(value: int) -> bytes:
    if not (0 <= value < 1 << 64):
        raise ValueError("coordinate integer is outside unsigned 64-bit range")
    return value.to_bytes(8, "big")


def _coordinate_bytes(coordinate: tuple[str, int, int, str]) -> bytes:
    path, start_line, end_line, unit_kind = coordinate
    validate_relative_path(path)
    return b"".join(
        (
            frame(path),
            frame(_uint(start_line)),
            frame(_uint(end_line)),
            frame(unit_kind),
        )
    )


def occurrence_identity(coordinate: tuple[str, int, int, str]) -> str:
    """Hash the frozen coordinate; the coordinate itself remains authoritative."""
    payload = frame(_OCCURRENCE_NAMESPACE) + _coordinate_bytes(coordinate)
    return "do1:" + hashlib.sha256(payload).hexdigest()


def _group_identity(
    fingerprint_version: str,
    fingerprint: str,
    occurrences: tuple[LexicalOccurrence, ...],
) -> str:
    payload = bytearray(frame(_GROUP_NAMESPACE))
    payload.extend(frame(fingerprint_version))
    payload.extend(frame(fingerprint))
    for occurrence in occurrences:
        payload.extend(_coordinate_bytes(occurrence.coordinate))
    return "dg1:" + hashlib.sha256(payload).hexdigest()


def _distribution(occurrences: tuple[LexicalOccurrence, ...]) -> CloneDistribution:
    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.relative_path] = counts.get(occurrence.relative_path, 0) + 1
    if len(counts) == 1:
        return CloneDistribution.SAME_FILE
    if all(count == 1 for count in counts.values()):
        return CloneDistribution.CROSS_FILE
    return CloneDistribution.MIXED


def group_lexical_clones(
    occurrences: Iterable[LexicalOccurrence],
) -> tuple[LexicalCloneGroup, ...]:
    """Group exact lexical clones, splitting every digest bucket by bytes."""
    unique: dict[tuple[str, int, int, str], LexicalOccurrence] = {}
    for occurrence in occurrences:
        validate_relative_path(occurrence.relative_path)
        if occurrence.fingerprint_version != LEXICAL_FINGERPRINT_VERSION:
            raise GroupingInvariantError(
                f"unsupported fingerprint version: {occurrence.fingerprint_version!r}"
            )
        if occurrence.candidate.language not in {
            "Go",
            "Java",
            "JavaScript",
            "Python",
            "TypeScript",
        }:
            raise GroupingInvariantError(
                f"unsupported occurrence language: {occurrence.candidate.language!r}"
            )
        expected_id = occurrence_identity(occurrence.coordinate)
        if occurrence.occurrence_id != expected_id:
            raise GroupingInvariantError("occurrence ID does not match its coordinate")
        prior = unique.get(occurrence.coordinate)
        if prior is None:
            unique[occurrence.coordinate] = occurrence
        elif (
            prior.candidate.language != occurrence.candidate.language
            or prior.fingerprint != occurrence.fingerprint
            or prior.canonical_bytes != occurrence.canonical_bytes
        ):
            raise GroupingInvariantError(
                "one duplicate-occurrence coordinate has conflicting canonical content"
            )

    buckets: dict[tuple[str, str, str], list[LexicalOccurrence]] = {}
    for occurrence in unique.values():
        key = (
            occurrence.candidate.language,
            occurrence.fingerprint_version,
            occurrence.fingerprint,
        )
        buckets.setdefault(key, []).append(occurrence)

    groups: list[LexicalCloneGroup] = []
    for (language, version, fingerprint), bucket in sorted(buckets.items()):
        # Do not use digest equality as content equality.  The explicit list of
        # representatives makes the byte comparison visible and testable even
        # when every input has an injected identical digest.
        equality_classes: list[tuple[bytes, list[LexicalOccurrence]]] = []
        for occurrence in sorted(bucket, key=lambda item: item.coordinate):
            for canonical, members in equality_classes:
                if occurrence.canonical_bytes == canonical:
                    members.append(occurrence)
                    break
            else:
                equality_classes.append((occurrence.canonical_bytes, [occurrence]))

        for _canonical, members in equality_classes:
            if len(members) < 2:
                continue
            ordered = tuple(sorted(members, key=lambda item: item.coordinate))
            groups.append(
                LexicalCloneGroup(
                    group_id=_group_identity(version, fingerprint, ordered),
                    language=language,
                    fingerprint=fingerprint,
                    fingerprint_version=version,
                    distribution=_distribution(ordered),
                    occurrences=ordered,
                )
            )

    groups.sort(
        key=lambda group: (
            group.language,
            group.fingerprint,
            tuple(occurrence.coordinate for occurrence in group.occurrences),
            group.group_id,
        )
    )
    return tuple(groups)


__all__ = [
    "GroupingInvariantError",
    "group_lexical_clones",
    "occurrence_identity",
    "validate_relative_path",
]
