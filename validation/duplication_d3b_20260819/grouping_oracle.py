"""Independent D3-B structural grouping oracle.

No production duplication module is imported. Objects are consumed by their
frozen public fields so tests can compare two independently implemented
grouping relations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable


GROUP_NAMESPACE = b"archlens-duplication-group"
OCCURRENCE_NAMESPACE = b"archlens-duplication-occurrence"
UNIT_PRIORITY = {
    "callable_body": 0,
    "branch_body": 1,
    "loop_body": 2,
    "exception_body": 3,
    "switch_arm_body": 4,
    "scoped_body": 5,
}
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class OracleGroup:
    group_id: str
    language: str
    fingerprint: str
    fingerprint_version: str
    distribution: str
    occurrences: tuple[Any, ...]
    source_span_union: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class OracleDominance:
    child_group_id: str
    parent_group_id: str
    occurrence_pairs: tuple[tuple[str, str], ...]
    mapping_ambiguous: bool


@dataclass(frozen=True)
class OracleResult:
    groups: tuple[OracleGroup, ...]
    dominance: tuple[OracleDominance, ...]
    initial_group_count: int


def _frame(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return len(raw).to_bytes(8, "big") + raw


def _path(path: str) -> None:
    parsed = PurePosixPath(path)
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or parsed.is_absolute()
        or WINDOWS_DRIVE.match(path)
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != path
    ):
        raise ValueError("oracle requires a canonical relative POSIX path")


def _uint(value: int) -> bytes:
    if not (0 <= value < 1 << 64):
        raise ValueError("oracle coordinate is outside uint64")
    return value.to_bytes(8, "big")


def _coordinate(occurrence: Any) -> tuple[str, int, int, str]:
    candidate = occurrence.candidate
    return (
        occurrence.relative_path,
        candidate.span.start_line,
        candidate.span.end_line,
        candidate.unit_kind.value,
    )


def _coordinate_bytes(coordinate: tuple[str, int, int, str]) -> bytes:
    path, start, end, kind = coordinate
    _path(path)
    return b"".join((_frame(path), _frame(_uint(start)), _frame(_uint(end)), _frame(kind)))


def oracle_occurrence_id(coordinate: tuple[str, int, int, str]) -> str:
    return "do1:" + hashlib.sha256(
        _frame(OCCURRENCE_NAMESPACE) + _coordinate_bytes(coordinate)
    ).hexdigest()


def _group_id(version: str, fingerprint: str, occurrences: tuple[Any, ...]) -> str:
    payload = bytearray(_frame(GROUP_NAMESPACE))
    payload.extend(_frame(version))
    payload.extend(_frame(fingerprint))
    for occurrence in occurrences:
        payload.extend(_coordinate_bytes(_coordinate(occurrence)))
    return "dg1:" + hashlib.sha256(payload).hexdigest()


def _candidate_without_kind(candidate: Any) -> tuple[Any, ...]:
    span = candidate.span
    return (
        candidate.language,
        span.start_byte,
        span.end_byte,
        span.start_line,
        span.start_column,
        span.end_line,
        span.end_column,
        span.original_start_byte,
        span.original_end_byte,
        candidate.immediate_statement_count,
        candidate.significant_lexical_token_count,
        candidate.duplicated_nloc,
        candidate.extraction_status.value,
        candidate.failed_floors,
    )


def _deduplicate(occurrences: Iterable[Any]) -> tuple[Any, ...]:
    coordinates: dict[tuple[str, int, int, str], Any] = {}
    for occurrence in occurrences:
        coordinate = _coordinate(occurrence)
        _path(occurrence.relative_path)
        if occurrence.occurrence_id != oracle_occurrence_id(coordinate):
            raise ValueError("oracle occurrence ID mismatch")
        prior = coordinates.get(coordinate)
        if prior is None:
            coordinates[coordinate] = occurrence
        elif (
            prior.candidate != occurrence.candidate
            or prior.canonical_bytes != occurrence.canonical_bytes
            or prior.fingerprint != occurrence.fingerprint
            or prior.fingerprint_version != occurrence.fingerprint_version
            or prior.occurrence_id != occurrence.occurrence_id
        ):
            raise ValueError("oracle coordinate conflict")

    exact: dict[tuple[str, int, int], Any] = {}
    for occurrence in sorted(coordinates.values(), key=_coordinate):
        span = occurrence.candidate.span
        key = occurrence.relative_path, span.start_byte, span.end_byte
        prior = exact.get(key)
        if prior is None:
            exact[key] = occurrence
            continue
        if (
            _candidate_without_kind(prior.candidate)
            != _candidate_without_kind(occurrence.candidate)
            or prior.canonical_bytes != occurrence.canonical_bytes
            or prior.fingerprint != occurrence.fingerprint
            or prior.fingerprint_version != occurrence.fingerprint_version
        ):
            raise ValueError("oracle exact-span conflict")
        if UNIT_PRIORITY[occurrence.candidate.unit_kind.value] < UNIT_PRIORITY[
            prior.candidate.unit_kind.value
        ]:
            exact[key] = occurrence

    result = tuple(sorted(exact.values(), key=_coordinate))
    languages: dict[str, str] = {}
    content_fingerprints: dict[tuple[str, str, bytes], str] = {}
    for occurrence in result:
        language = occurrence.candidate.language
        if languages.setdefault(occurrence.relative_path, language) != language:
            raise ValueError("oracle path language conflict")
        content = language, occurrence.fingerprint_version, occurrence.canonical_bytes
        if content_fingerprints.setdefault(content, occurrence.fingerprint) != occurrence.fingerprint:
            raise ValueError("oracle equal bytes fingerprint conflict")
    return result


def _contains(parent: Any, child: Any) -> bool:
    outer, inner = parent.candidate.span, child.candidate.span
    return (
        parent.relative_path == child.relative_path
        and outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
        and (outer.start_byte, outer.end_byte) != (inner.start_byte, inner.end_byte)
    )


def _validate_laminar(occurrences: tuple[Any, ...]) -> None:
    paths: dict[str, list[Any]] = {}
    for occurrence in occurrences:
        paths.setdefault(occurrence.relative_path, []).append(occurrence)
    for path, members in paths.items():
        ordered = sorted(
            members,
            key=lambda item: (
                item.candidate.span.start_byte,
                item.candidate.span.end_byte,
                _coordinate(item),
            ),
        )
        for index, left in enumerate(ordered):
            a = left.candidate.span
            for right in ordered[index + 1 :]:
                b = right.candidate.span
                if b.start_byte >= a.end_byte:
                    break
                if not (_contains(left, right) or _contains(right, left)):
                    raise ValueError(f"oracle partial overlap in {path}")


def _distribution(occurrences: tuple[Any, ...]) -> str:
    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.relative_path] = counts.get(occurrence.relative_path, 0) + 1
    if len(counts) == 1:
        return "same_file"
    if all(count == 1 for count in counts.values()):
        return "cross_file"
    return "mixed"


def _union(occurrences: tuple[Any, ...]) -> tuple[tuple[str, int, int], ...]:
    paths: dict[str, list[tuple[int, int]]] = {}
    for occurrence in occurrences:
        span = occurrence.candidate.span
        paths.setdefault(occurrence.relative_path, []).append((span.start_line, span.end_line))
    result: list[tuple[str, int, int]] = []
    for path in sorted(paths):
        merged: list[list[int]] = []
        for start, end in sorted(paths[path]):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        result.extend((path, start, end) for start, end in merged)
    return tuple(result)


def _group_key(group: OracleGroup) -> tuple[Any, ...]:
    return (
        group.language,
        group.fingerprint,
        tuple(_coordinate(occurrence) for occurrence in group.occurrences),
        group.group_id,
    )


def _initial(occurrences: tuple[Any, ...]) -> tuple[OracleGroup, ...]:
    buckets: dict[tuple[str, str, str], list[Any]] = {}
    for occurrence in occurrences:
        key = (
            occurrence.candidate.language,
            occurrence.fingerprint_version,
            occurrence.fingerprint,
        )
        buckets.setdefault(key, []).append(occurrence)
    groups: list[OracleGroup] = []
    for (language, version, fingerprint), bucket in sorted(buckets.items()):
        exact: list[tuple[bytes, list[Any]]] = []
        for occurrence in sorted(bucket, key=_coordinate):
            match = next((members for raw, members in exact if raw == occurrence.canonical_bytes), None)
            if match is None:
                exact.append((occurrence.canonical_bytes, [occurrence]))
            else:
                match.append(occurrence)
        for _raw, members in exact:
            if len(members) < 2:
                continue
            ordered = tuple(sorted(members, key=_coordinate))
            groups.append(
                OracleGroup(
                    _group_id(version, fingerprint, ordered),
                    language,
                    fingerprint,
                    version,
                    _distribution(ordered),
                    ordered,
                    _union(ordered),
                )
            )
    return tuple(sorted(groups, key=_group_key))


def _bijection(child: OracleGroup, parent: OracleGroup) -> tuple[tuple[tuple[Any, Any], ...], bool] | None:
    if (
        child.language != parent.language
        or child.fingerprint_version != parent.fingerprint_version
        or len(child.occurrences) != len(parent.occurrences)
    ):
        return None
    children = tuple(sorted(child.occurrences, key=_coordinate))
    parents = tuple(sorted(parent.occurrences, key=_coordinate))
    choices = {item.occurrence_id: tuple(outer for outer in parents if _contains(outer, item)) for item in children}
    if any(not values for values in choices.values()):
        return None
    solutions: list[tuple[tuple[Any, Any], ...]] = []

    def search(index: int, used: set[str], pairs: list[tuple[Any, Any]]) -> None:
        if len(solutions) >= 2:
            return
        if index == len(children):
            solutions.append(tuple(pairs))
            return
        inner = children[index]
        for outer in choices[inner.occurrence_id]:
            if outer.occurrence_id in used:
                continue
            used.add(outer.occurrence_id)
            pairs.append((inner, outer))
            search(index + 1, used, pairs)
            pairs.pop()
            used.remove(outer.occurrence_id)

    search(0, set(), [])
    return (solutions[0], len(solutions) > 1) if solutions else None


def oracle_group_structural_clones(occurrences: Iterable[Any]) -> OracleResult:
    unique = _deduplicate(occurrences)
    _validate_laminar(unique)
    initial = _initial(unique)
    relations: list[OracleDominance] = []
    suppressed: set[str] = set()
    for child in initial:
        for parent in initial:
            if child.group_id == parent.group_id:
                continue
            match = _bijection(child, parent)
            if match is None:
                continue
            pairs, ambiguous = match
            suppressed.add(child.group_id)
            relations.append(
                OracleDominance(
                    child.group_id,
                    parent.group_id,
                    tuple((inner.occurrence_id, outer.occurrence_id) for inner, outer in pairs),
                    ambiguous,
                )
            )
    retained = tuple(group for group in initial if group.group_id not in suppressed)
    keys = {group.group_id: _group_key(group) for group in initial}
    relations.sort(
        key=lambda relation: (
            keys[relation.child_group_id],
            keys[relation.parent_group_id],
            relation.occurrence_pairs,
        )
    )
    return OracleResult(retained, tuple(relations), len(initial))


__all__ = [
    "OracleDominance",
    "OracleGroup",
    "OracleResult",
    "oracle_group_structural_clones",
    "oracle_occurrence_id",
]
