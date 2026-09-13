"""Combined clean-room oracle for the D4 validation campaign.

Canonicalization delegates only to the independently maintained D2 and D3-A
oracles.  Lexical grouping is implemented here from the frozen contract, and
structural grouping delegates only to the independent D3-B oracle.  Production
canonicalizers, fingerprints, and groupers are intentionally not imported.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

from validation.duplication_d2_20260818.lexical_oracle import (
    oracle_canonical_bytes as oracle_lexical_bytes,
)
from validation.duplication_d3a_20260818.structural_oracle import (
    oracle_structural_bytes,
    oracle_structural_fingerprint,
)
from validation.duplication_d3b_20260819.grouping_oracle import (
    oracle_group_structural_clones,
)


FINGERPRINT_NAMESPACE = b"archlens-duplication-fingerprint"
GROUP_NAMESPACE = b"archlens-duplication-group"
OCCURRENCE_NAMESPACE = b"archlens-duplication-occurrence"
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class OracleLexicalGroup:
    group_id: str
    language: str
    fingerprint: str
    fingerprint_version: str
    distribution: str
    occurrences: tuple[Any, ...]


def _frame(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return len(raw).to_bytes(8, "big") + raw


def _uint(value: int) -> bytes:
    if not (0 <= value < 1 << 64):
        raise ValueError("coordinate integer is outside unsigned 64-bit range")
    return value.to_bytes(8, "big")


def _validate_path(value: str) -> None:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or parsed.is_absolute()
        or WINDOWS_DRIVE.match(value)
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != value
    ):
        raise ValueError("nonportable occurrence path")


def _coordinate(occurrence: Any) -> tuple[str, int, int, str]:
    return (
        occurrence.relative_path,
        occurrence.candidate.span.start_line,
        occurrence.candidate.span.end_line,
        occurrence.candidate.unit_kind.value,
    )


def _coordinate_bytes(coordinate: tuple[str, int, int, str]) -> bytes:
    path, start_line, end_line, unit_kind = coordinate
    _validate_path(path)
    return b"".join(
        (_frame(path), _frame(_uint(start_line)), _frame(_uint(end_line)), _frame(unit_kind))
    )


def oracle_occurrence_id(coordinate: tuple[str, int, int, str]) -> str:
    return "do1:" + hashlib.sha256(
        _frame(OCCURRENCE_NAMESPACE) + _coordinate_bytes(coordinate)
    ).hexdigest()


def oracle_fingerprint(version: str, language: str, canonical: bytes) -> str:
    payload = b"".join(
        (_frame(FINGERPRINT_NAMESPACE), _frame(version), _frame(language), _frame(canonical))
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _group_id(version: str, fingerprint: str, occurrences: tuple[Any, ...]) -> str:
    payload = bytearray(_frame(GROUP_NAMESPACE))
    payload.extend(_frame(version))
    payload.extend(_frame(fingerprint))
    for occurrence in occurrences:
        payload.extend(_coordinate_bytes(_coordinate(occurrence)))
    return "dg1:" + hashlib.sha256(payload).hexdigest()


def _distribution(occurrences: tuple[Any, ...]) -> str:
    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.relative_path] = counts.get(occurrence.relative_path, 0) + 1
    if len(counts) == 1:
        return "same_file"
    if all(count == 1 for count in counts.values()):
        return "cross_file"
    return "mixed"


def oracle_group_lexical_clones(
    occurrences: Iterable[Any],
) -> tuple[OracleLexicalGroup, ...]:
    unique: dict[tuple[str, int, int, str], Any] = {}
    for occurrence in occurrences:
        coordinate = _coordinate(occurrence)
        _validate_path(occurrence.relative_path)
        if occurrence.occurrence_id != oracle_occurrence_id(coordinate):
            raise ValueError("occurrence ID does not match coordinate")
        prior = unique.get(coordinate)
        if prior is None:
            unique[coordinate] = occurrence
        elif (
            prior.candidate.language != occurrence.candidate.language
            or prior.fingerprint != occurrence.fingerprint
            or prior.canonical_bytes != occurrence.canonical_bytes
        ):
            raise ValueError("conflicting duplicate coordinate")

    buckets: dict[tuple[str, str, str], list[Any]] = {}
    for occurrence in unique.values():
        buckets.setdefault(
            (
                occurrence.candidate.language,
                occurrence.fingerprint_version,
                occurrence.fingerprint,
            ),
            [],
        ).append(occurrence)

    groups: list[OracleLexicalGroup] = []
    for (language, version, fingerprint), bucket in sorted(buckets.items()):
        equality_classes: list[tuple[bytes, list[Any]]] = []
        for occurrence in sorted(bucket, key=_coordinate):
            for canonical, members in equality_classes:
                if occurrence.canonical_bytes == canonical:
                    members.append(occurrence)
                    break
            else:
                equality_classes.append((occurrence.canonical_bytes, [occurrence]))
        for _canonical, members in equality_classes:
            if len(members) < 2:
                continue
            ordered = tuple(sorted(members, key=_coordinate))
            groups.append(
                OracleLexicalGroup(
                    group_id=_group_id(version, fingerprint, ordered),
                    language=language,
                    fingerprint=fingerprint,
                    fingerprint_version=version,
                    distribution=_distribution(ordered),
                    occurrences=ordered,
                )
            )
    return tuple(
        sorted(
            groups,
            key=lambda group: (
                group.language,
                group.fingerprint,
                tuple(_coordinate(item) for item in group.occurrences),
                group.group_id,
            ),
        )
    )


def lexical_membership_snapshot(groups: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(
        (
            group.group_id,
            group.language,
            group.fingerprint,
            group.fingerprint_version,
            group.distribution if isinstance(group.distribution, str) else group.distribution.value,
            tuple(_coordinate(item) for item in group.occurrences),
        )
        for group in groups
    )


def structural_snapshot(result: Any) -> tuple[Any, ...]:
    return (
        tuple(
            (
                group.group_id,
                group.language,
                group.fingerprint,
                group.fingerprint_version,
                group.distribution if isinstance(group.distribution, str) else group.distribution.value,
                tuple(_coordinate(item) for item in group.occurrences),
                tuple(
                    (
                        interval
                        if isinstance(interval, tuple)
                        else (interval.relative_path, interval.start_line, interval.end_line)
                    )
                    for interval in group.source_span_union
                ),
            )
            for group in result.groups
        ),
        tuple(
            (
                relation.child_group_id,
                relation.parent_group_id,
                relation.occurrence_pairs,
                relation.mapping_ambiguous,
            )
            for relation in result.dominance
        ),
        result.initial_group_count,
    )


__all__ = [
    "lexical_membership_snapshot",
    "oracle_fingerprint",
    "oracle_group_lexical_clones",
    "oracle_group_structural_clones",
    "oracle_lexical_bytes",
    "oracle_occurrence_id",
    "oracle_structural_bytes",
    "oracle_structural_fingerprint",
    "structural_snapshot",
]
