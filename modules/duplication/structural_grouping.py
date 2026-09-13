"""D3-B collision-safe structural grouping and maximality suppression.

The module consumes admitted D3-A canonical occurrences and returns internal
groups only.  It contains no CLI, report, persistence, schema, policy, SARIF,
or hotspot integration.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from modules.duplication.grouping import occurrence_identity, validate_relative_path
from modules.duplication.model import (
    Candidate,
    CloneDistribution,
    SourceLineInterval,
    StructuralCloneGroup,
    StructuralDominance,
    StructuralGroupingResult,
    StructuralOccurrence,
    UNIT_KIND_PRIORITY,
)
from modules.duplication.structural import (
    STRUCTURAL_CANONICAL_MAGIC,
    STRUCTURAL_FINGERPRINT_VERSION,
    canonical_structural_bytes,
    structural_fingerprint,
)
from modules.source_frontend import SelectedSyntax


_GROUP_NAMESPACE = b"archlens-duplication-group"
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_LANGUAGES = frozenset({"Go", "Java", "JavaScript", "Python", "TypeScript"})


class StructuralGroupingInvariantError(RuntimeError):
    """Structural occurrence, overlap, or suppression invariants were violated."""


def _frame(value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    if len(data) >= 1 << 64:
        raise OverflowError("group identity field exceeds unsigned 64-bit framing")
    return len(data).to_bytes(8, "big") + data


def _uint(value: int) -> bytes:
    if not (0 <= value < 1 << 64):
        raise ValueError("coordinate integer is outside unsigned 64-bit range")
    return value.to_bytes(8, "big")


def _coordinate_bytes(coordinate: tuple[str, int, int, str]) -> bytes:
    path, start_line, end_line, unit_kind = coordinate
    validate_relative_path(path)
    return b"".join(
        (
            _frame(path),
            _frame(_uint(start_line)),
            _frame(_uint(end_line)),
            _frame(unit_kind),
        )
    )


def _canonical_header(canonical: bytes) -> tuple[str, str]:
    if not canonical.startswith(STRUCTURAL_CANONICAL_MAGIC):
        raise StructuralGroupingInvariantError("canonical bytes have the wrong structural magic")
    offset = len(STRUCTURAL_CANONICAL_MAGIC)
    values: list[str] = []
    for field in ("version", "language"):
        if offset + 8 > len(canonical):
            raise StructuralGroupingInvariantError(f"canonical bytes truncate {field}")
        length = int.from_bytes(canonical[offset : offset + 8], "big")
        offset += 8
        if offset + length > len(canonical):
            raise StructuralGroupingInvariantError(f"canonical bytes truncate {field}")
        try:
            values.append(canonical[offset : offset + length].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise StructuralGroupingInvariantError(
                f"canonical {field} is not UTF-8"
            ) from exc
        offset += length
    return values[0], values[1]


def _group_identity(
    version: str,
    fingerprint: str,
    occurrences: tuple[StructuralOccurrence, ...],
) -> str:
    payload = bytearray(_frame(_GROUP_NAMESPACE))
    payload.extend(_frame(version))
    payload.extend(_frame(fingerprint))
    for occurrence in occurrences:
        payload.extend(_coordinate_bytes(occurrence.coordinate))
    return "dg1:" + hashlib.sha256(payload).hexdigest()


def _distribution(occurrences: tuple[StructuralOccurrence, ...]) -> CloneDistribution:
    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.relative_path] = counts.get(occurrence.relative_path, 0) + 1
    if len(counts) == 1:
        return CloneDistribution.SAME_FILE
    if all(count == 1 for count in counts.values()):
        return CloneDistribution.CROSS_FILE
    return CloneDistribution.MIXED


def _source_span_union(
    occurrences: tuple[StructuralOccurrence, ...],
) -> tuple[SourceLineInterval, ...]:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for occurrence in occurrences:
        span = occurrence.candidate.span
        by_path.setdefault(occurrence.relative_path, []).append(
            (span.start_line, span.end_line)
        )
    result: list[SourceLineInterval] = []
    for path in sorted(by_path):
        merged: list[list[int]] = []
        for start, end in sorted(by_path[path]):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        result.extend(SourceLineInterval(path, start, end) for start, end in merged)
    return tuple(result)


def _group_sort_key(group: StructuralCloneGroup) -> tuple[Any, ...]:
    return (
        group.language,
        group.fingerprint,
        tuple(occurrence.coordinate for occurrence in group.occurrences),
        group.group_id,
    )


def _validate_occurrence(occurrence: StructuralOccurrence) -> None:
    try:
        validate_relative_path(occurrence.relative_path)
    except ValueError as exc:
        raise StructuralGroupingInvariantError(str(exc)) from exc
    candidate = occurrence.candidate
    span = candidate.span
    if not candidate.admitted:
        raise StructuralGroupingInvariantError("structural occurrence candidate is not admitted")
    if candidate.language not in _LANGUAGES:
        raise StructuralGroupingInvariantError(
            f"unsupported occurrence language: {candidate.language!r}"
        )
    if occurrence.fingerprint_version != STRUCTURAL_FINGERPRINT_VERSION:
        raise StructuralGroupingInvariantError(
            f"unsupported fingerprint version: {occurrence.fingerprint_version!r}"
        )
    if not _FINGERPRINT.fullmatch(occurrence.fingerprint):
        raise StructuralGroupingInvariantError("structural fingerprint is not canonical SHA-256 text")
    if not (0 <= span.start_byte < span.end_byte):
        raise StructuralGroupingInvariantError("occurrence has an invalid byte span")
    if span.start_line < 1 or span.end_line < span.start_line:
        raise StructuralGroupingInvariantError("occurrence has an invalid line span")
    version, language = _canonical_header(occurrence.canonical_bytes)
    if version != occurrence.fingerprint_version:
        raise StructuralGroupingInvariantError("canonical and occurrence versions disagree")
    if language != candidate.language:
        raise StructuralGroupingInvariantError("canonical and occurrence languages disagree")
    try:
        expected_occurrence_id = occurrence_identity(occurrence.coordinate)
    except ValueError as exc:
        raise StructuralGroupingInvariantError(str(exc)) from exc
    if occurrence.occurrence_id != expected_occurrence_id:
        raise StructuralGroupingInvariantError("occurrence ID does not match its coordinate")


def _same_coordinate_content(
    left: StructuralOccurrence, right: StructuralOccurrence
) -> bool:
    return (
        left.relative_path == right.relative_path
        and left.candidate == right.candidate
        and left.canonical_bytes == right.canonical_bytes
        and left.fingerprint == right.fingerprint
        and left.fingerprint_version == right.fingerprint_version
        and left.occurrence_id == right.occurrence_id
    )


def _same_exact_span_content(
    left: StructuralOccurrence, right: StructuralOccurrence
) -> bool:
    a, b = left.candidate, right.candidate
    return (
        a.language == b.language
        and replace(a, unit_kind=b.unit_kind) == b
        and left.canonical_bytes == right.canonical_bytes
        and left.fingerprint == right.fingerprint
        and left.fingerprint_version == right.fingerprint_version
    )


def _deduplicate_occurrences(
    occurrences: Iterable[StructuralOccurrence],
) -> tuple[StructuralOccurrence, ...]:
    by_coordinate: dict[tuple[str, int, int, str], StructuralOccurrence] = {}
    for occurrence in occurrences:
        _validate_occurrence(occurrence)
        prior = by_coordinate.get(occurrence.coordinate)
        if prior is None:
            by_coordinate[occurrence.coordinate] = occurrence
        elif not _same_coordinate_content(prior, occurrence):
            raise StructuralGroupingInvariantError(
                "one occurrence coordinate has conflicting structural content"
            )

    by_exact_span: dict[tuple[str, int, int], StructuralOccurrence] = {}
    for occurrence in sorted(by_coordinate.values(), key=lambda item: item.coordinate):
        span = occurrence.candidate.span
        key = (occurrence.relative_path, span.start_byte, span.end_byte)
        prior = by_exact_span.get(key)
        if prior is None:
            by_exact_span[key] = occurrence
            continue
        if not _same_exact_span_content(prior, occurrence):
            raise StructuralGroupingInvariantError(
                "one exact source span has conflicting structural occurrences"
            )
        if UNIT_KIND_PRIORITY[occurrence.candidate.unit_kind] < UNIT_KIND_PRIORITY[
            prior.candidate.unit_kind
        ]:
            by_exact_span[key] = occurrence

    result = tuple(sorted(by_exact_span.values(), key=lambda item: item.coordinate))
    path_languages: dict[str, str] = {}
    canonical_fingerprints: dict[tuple[str, str, bytes], str] = {}
    for occurrence in result:
        language = occurrence.candidate.language
        prior_language = path_languages.setdefault(occurrence.relative_path, language)
        if prior_language != language:
            raise StructuralGroupingInvariantError(
                "one relative path contains occurrences from multiple languages"
            )
        content_key = (language, occurrence.fingerprint_version, occurrence.canonical_bytes)
        prior_fingerprint = canonical_fingerprints.setdefault(
            content_key, occurrence.fingerprint
        )
        if prior_fingerprint != occurrence.fingerprint:
            raise StructuralGroupingInvariantError(
                "equal canonical bytes have inconsistent fingerprints"
            )
    return result


def _properly_contains(parent: StructuralOccurrence, child: StructuralOccurrence) -> bool:
    if parent.relative_path != child.relative_path:
        return False
    outer, inner = parent.candidate.span, child.candidate.span
    return (
        outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
        and (outer.start_byte, outer.end_byte) != (inner.start_byte, inner.end_byte)
    )


def _validate_laminar(occurrences: tuple[StructuralOccurrence, ...]) -> None:
    by_path: dict[str, list[StructuralOccurrence]] = {}
    for occurrence in occurrences:
        by_path.setdefault(occurrence.relative_path, []).append(occurrence)
    for path, members in by_path.items():
        ordered = sorted(
            members,
            key=lambda item: (
                item.candidate.span.start_byte,
                item.candidate.span.end_byte,
                item.coordinate,
            ),
        )
        for index, left in enumerate(ordered):
            a = left.candidate.span
            for right in ordered[index + 1 :]:
                b = right.candidate.span
                if b.start_byte >= a.end_byte:
                    break
                if _properly_contains(left, right) or _properly_contains(right, left):
                    continue
                raise StructuralGroupingInvariantError(
                    "partially overlapping structural occurrences in "
                    f"{path}: [{a.start_byte}, {a.end_byte}) and "
                    f"[{b.start_byte}, {b.end_byte})"
                )


def _initial_groups(
    occurrences: tuple[StructuralOccurrence, ...],
) -> tuple[StructuralCloneGroup, ...]:
    buckets: dict[tuple[str, str, str], list[StructuralOccurrence]] = {}
    for occurrence in occurrences:
        key = (
            occurrence.candidate.language,
            occurrence.fingerprint_version,
            occurrence.fingerprint,
        )
        buckets.setdefault(key, []).append(occurrence)

    groups: list[StructuralCloneGroup] = []
    for (language, version, fingerprint), bucket in sorted(buckets.items()):
        equality_classes: list[tuple[bytes, list[StructuralOccurrence]]] = []
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
                StructuralCloneGroup(
                    group_id=_group_identity(version, fingerprint, ordered),
                    language=language,
                    fingerprint=fingerprint,
                    fingerprint_version=version,
                    distribution=_distribution(ordered),
                    occurrences=ordered,
                    source_span_union=_source_span_union(ordered),
                )
            )
    return tuple(sorted(groups, key=_group_sort_key))


def _containment_bijection(
    child: StructuralCloneGroup,
    parent: StructuralCloneGroup,
) -> tuple[tuple[tuple[StructuralOccurrence, StructuralOccurrence], ...], bool] | None:
    if (
        child.language != parent.language
        or child.fingerprint_version != parent.fingerprint_version
        or child.occurrence_count != parent.occurrence_count
    ):
        return None
    children = tuple(sorted(child.occurrences, key=lambda item: item.coordinate))
    parents = tuple(sorted(parent.occurrences, key=lambda item: item.coordinate))
    choices = {
        item.occurrence_id: tuple(
            candidate
            for candidate in parents
            if _properly_contains(candidate, item)
        )
        for item in children
    }
    if any(not values for values in choices.values()):
        return None

    solutions: list[tuple[tuple[StructuralOccurrence, StructuralOccurrence], ...]] = []

    def search(
        index: int,
        used: set[str],
        pairs: list[tuple[StructuralOccurrence, StructuralOccurrence]],
    ) -> None:
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
    if not solutions:
        return None
    return solutions[0], len(solutions) > 1


def group_structural_clones(
    occurrences: Iterable[StructuralOccurrence],
) -> StructuralGroupingResult:
    """Form exact structural groups and suppress bijection-dominated children."""
    unique = _deduplicate_occurrences(occurrences)
    _validate_laminar(unique)
    initial = _initial_groups(unique)

    dominance: list[StructuralDominance] = []
    suppressed: set[str] = set()
    for child in initial:
        for parent in initial:
            if child.group_id == parent.group_id:
                continue
            relation = _containment_bijection(child, parent)
            if relation is None:
                continue
            pairs, ambiguous = relation
            suppressed.add(child.group_id)
            dominance.append(
                StructuralDominance(
                    child_group_id=child.group_id,
                    parent_group_id=parent.group_id,
                    occurrence_pairs=tuple(
                        (inner.occurrence_id, outer.occurrence_id)
                        for inner, outer in pairs
                    ),
                    mapping_ambiguous=ambiguous,
                )
            )

    retained = tuple(group for group in initial if group.group_id not in suppressed)
    group_keys = {group.group_id: _group_sort_key(group) for group in initial}
    dominance.sort(
        key=lambda item: (
            group_keys[item.child_group_id],
            group_keys[item.parent_group_id],
            item.occurrence_pairs,
        )
    )
    return StructuralGroupingResult(retained, tuple(dominance))


def make_structural_occurrence(
    relative_path: str,
    syntax: SelectedSyntax,
    candidate: Candidate,
    *,
    digest: Callable[[bytes], bytes] | None = None,
) -> StructuralOccurrence:
    """Create one validated structural occurrence without grouping side effects."""
    validate_relative_path(relative_path)
    canonical = canonical_structural_bytes(syntax, candidate)
    fingerprint = structural_fingerprint(syntax.language, canonical, digest=digest)
    coordinate = (
        relative_path,
        candidate.span.start_line,
        candidate.span.end_line,
        candidate.unit_kind.value,
    )
    return StructuralOccurrence(
        relative_path=relative_path,
        candidate=candidate,
        canonical_bytes=canonical,
        fingerprint=fingerprint,
        fingerprint_version=STRUCTURAL_FINGERPRINT_VERSION,
        occurrence_id=occurrence_identity(coordinate),
    )


__all__ = [
    "StructuralGroupingInvariantError",
    "group_structural_clones",
    "make_structural_occurrence",
]
