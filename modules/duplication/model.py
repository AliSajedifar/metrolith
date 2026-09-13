"""Typed candidate, occurrence, and internal clone-group records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MIN_IMMEDIATE_STATEMENTS = 4
MIN_SIGNIFICANT_TOKENS = 40
MIN_DUPLICATED_NLOC = 8


class UnitKind(str, Enum):
    CALLABLE_BODY = "callable_body"
    BRANCH_BODY = "branch_body"
    LOOP_BODY = "loop_body"
    EXCEPTION_BODY = "exception_body"
    SWITCH_ARM_BODY = "switch_arm_body"
    SCOPED_BODY = "scoped_body"


UNIT_KIND_PRIORITY = {
    UnitKind.CALLABLE_BODY: 0,
    UnitKind.BRANCH_BODY: 1,
    UnitKind.LOOP_BODY: 2,
    UnitKind.EXCEPTION_BODY: 3,
    UnitKind.SWITCH_ARM_BODY: 4,
    UnitKind.SCOPED_BODY: 5,
}


class CandidateAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    BELOW_THRESHOLDS = "below_thresholds"


class CandidateExtractionStatus(str, Enum):
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, order=True)
class SourceSpan:
    """Half-open selected-parser span plus user-facing source coordinates."""

    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    original_start_byte: int | None
    original_end_byte: int | None


@dataclass(frozen=True, slots=True)
class Candidate:
    language: str
    unit_kind: UnitKind
    span: SourceSpan
    immediate_statement_count: int
    significant_lexical_token_count: int
    duplicated_nloc: int
    extraction_status: CandidateAdmissionStatus
    failed_floors: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.extraction_status is CandidateAdmissionStatus.ADMITTED


@dataclass(frozen=True, slots=True)
class CandidateExtractionResult:
    language: str
    status: CandidateExtractionStatus
    boundaries: tuple[Candidate, ...]
    reason: str | None = None

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in self.boundaries if candidate.admitted)


class CandidateInvariantError(RuntimeError):
    """An adapter emitted an invalid, duplicate, or partially-overlapping span."""


class CloneDistribution(str, Enum):
    SAME_FILE = "same_file"
    CROSS_FILE = "cross_file"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class LexicalOccurrence:
    """One canonicalized admitted body at a portable source coordinate."""

    relative_path: str
    candidate: Candidate
    canonical_bytes: bytes
    fingerprint: str
    fingerprint_version: str
    occurrence_id: str

    @property
    def coordinate(self) -> tuple[str, int, int, str]:
        """The frozen duplicate-occurrence identity before hashing."""
        return (
            self.relative_path,
            self.candidate.span.start_line,
            self.candidate.span.end_line,
            self.candidate.unit_kind.value,
        )


@dataclass(frozen=True, slots=True)
class LexicalCloneGroup:
    """An exact canonical-byte equality class with at least two occurrences."""

    group_id: str
    language: str
    fingerprint: str
    fingerprint_version: str
    distribution: CloneDistribution
    occurrences: tuple[LexicalOccurrence, ...]

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrences)

    @property
    def file_count(self) -> int:
        return len({occurrence.relative_path for occurrence in self.occurrences})


@dataclass(frozen=True, slots=True, order=True)
class SourceLineInterval:
    """One inclusive path-local component of a group's source-span union."""

    relative_path: str
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True, slots=True)
class StructuralOccurrence:
    """One canonicalized admitted body at its portable source coordinate."""

    relative_path: str
    candidate: Candidate
    canonical_bytes: bytes
    fingerprint: str
    fingerprint_version: str
    occurrence_id: str

    @property
    def coordinate(self) -> tuple[str, int, int, str]:
        return (
            self.relative_path,
            self.candidate.span.start_line,
            self.candidate.span.end_line,
            self.candidate.unit_kind.value,
        )


@dataclass(frozen=True, slots=True)
class StructuralCloneGroup:
    """A retained structural equality class after maximality suppression."""

    group_id: str
    language: str
    fingerprint: str
    fingerprint_version: str
    distribution: CloneDistribution
    occurrences: tuple[StructuralOccurrence, ...]
    source_span_union: tuple[SourceLineInterval, ...]

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrences)

    @property
    def file_count(self) -> int:
        return len({occurrence.relative_path for occurrence in self.occurrences})

    @property
    def source_span_line_count(self) -> int:
        """Count the inclusive path-local line union without cross-file merging."""
        return sum(interval.line_count for interval in self.source_span_union)


@dataclass(frozen=True, slots=True)
class StructuralDominance:
    """One complete inner-to-outer containment bijection used for suppression."""

    child_group_id: str
    parent_group_id: str
    occurrence_pairs: tuple[tuple[str, str], ...]
    mapping_ambiguous: bool


@dataclass(frozen=True, slots=True)
class StructuralGroupingResult:
    """Retained maximal groups plus explicit suppression evidence."""

    groups: tuple[StructuralCloneGroup, ...]
    dominance: tuple[StructuralDominance, ...]

    @property
    def suppressed_group_ids(self) -> tuple[str, ...]:
        return tuple(sorted({relation.child_group_id for relation in self.dominance}))

    @property
    def initial_group_count(self) -> int:
        return len(self.groups) + len(self.suppressed_group_ids)


def admission(
    statement_count: int, token_count: int, duplicated_nloc: int
) -> tuple[CandidateAdmissionStatus, tuple[str, ...]]:
    failed = []
    if statement_count < MIN_IMMEDIATE_STATEMENTS:
        failed.append("immediate_statements")
    if token_count < MIN_SIGNIFICANT_TOKENS:
        failed.append("tokens")
    if duplicated_nloc < MIN_DUPLICATED_NLOC:
        failed.append("nloc")
    return (
        CandidateAdmissionStatus.BELOW_THRESHOLDS if failed else CandidateAdmissionStatus.ADMITTED,
        tuple(failed),
    )


def validate_candidate_invariants(
    candidates: Iterable[Candidate], selected_source_length: int
) -> None:
    """Validate uniqueness, valid ranges, and laminar containment."""
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.span.start_byte,
            candidate.span.end_byte,
            UNIT_KIND_PRIORITY[candidate.unit_kind],
        ),
    )
    seen: set[tuple[int, int]] = set()
    for candidate in ordered:
        span = candidate.span
        key = (span.start_byte, span.end_byte)
        if key in seen:
            raise CandidateInvariantError(
                f"duplicate candidate span emitted: [{span.start_byte}, {span.end_byte})"
            )
        seen.add(key)
        if not (0 <= span.start_byte < span.end_byte <= selected_source_length):
            raise CandidateInvariantError(
                "candidate span is outside selected source: "
                f"[{span.start_byte}, {span.end_byte}) of {selected_source_length}"
            )
        if span.start_line < 1 or span.end_line < span.start_line:
            raise CandidateInvariantError(f"invalid candidate line range: {span}")

    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            a, b = left.span, right.span
            if b.start_byte >= a.end_byte:
                break
            contains = (
                a.start_byte <= b.start_byte and b.end_byte <= a.end_byte
            ) or (
                b.start_byte <= a.start_byte and a.end_byte <= b.end_byte
            )
            if not contains:
                raise CandidateInvariantError(
                    "partially overlapping candidate spans: "
                    f"[{a.start_byte}, {a.end_byte}) and "
                    f"[{b.start_byte}, {b.end_byte})"
                )


__all__ = [
    "Candidate",
    "CandidateAdmissionStatus",
    "CandidateExtractionResult",
    "CandidateExtractionStatus",
    "CandidateInvariantError",
    "CloneDistribution",
    "LexicalCloneGroup",
    "LexicalOccurrence",
    "MIN_DUPLICATED_NLOC",
    "MIN_IMMEDIATE_STATEMENTS",
    "MIN_SIGNIFICANT_TOKENS",
    "SourceLineInterval",
    "SourceSpan",
    "StructuralCloneGroup",
    "StructuralDominance",
    "StructuralGroupingResult",
    "StructuralOccurrence",
    "UnitKind",
    "admission",
    "validate_candidate_invariants",
]
