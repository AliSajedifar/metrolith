"""Deterministic callable matching between ArchLens and an external reference.

A comparison is only evidence if both sides are talking about the SAME callable.
Matching on qualified name alone is not enough: Java and TypeScript overload,
JavaScript reuses method names across classes, and an external tool's naming
scheme is its own (`Constructs::trivial`, `Widget.render`, `<lambda>`).

So a match must rest on the strongest deterministic evidence available, and an
ambiguous pairing is **classified, never guessed**. A wrong pairing does not
merely lose one observation: it manufactures a disagreement that does not exist
and sends adjudication after a defect nobody has.

Evidence, strongest first:

1. ``relative_path`` + ``qualified_name`` + ``signature_discriminator``
   -- exact, and the only tier that separates overloads;
2. ``relative_path`` + ``qualified_name`` + overlapping line span;
3. ``relative_path`` + line span alone, when the reference names callables
   differently (an anonymous or tool-renamed callable).

Anything that resolves to more than one candidate at every tier is
``ambiguous``. Anything with no candidate is ``unmatched_archlens`` or
``unmatched_reference`` -- reported, never silently dropped, because a
systematic population difference is itself a finding.

Nothing here computes or compares a metric value. Matching and comparison are
separate steps on purpose: a matcher that could see metric values could be
tuned, however unintentionally, to pair the rows that agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

MATCH_EXACT_SIGNATURE = "exact_path_name_signature"
MATCH_NAME_AND_SPAN = "path_name_and_overlapping_span"
MATCH_SPAN_ONLY = "path_and_span_only"

UNMATCHED_ARCHLENS = "unmatched_archlens"
UNMATCHED_REFERENCE = "unmatched_reference"
AMBIGUOUS = "ambiguous"

#: Match tiers, strongest first. Reported on every pairing so a downstream
#: reader can weight a span-only match differently from an exact one.
MATCH_TIERS = (MATCH_EXACT_SIGNATURE, MATCH_NAME_AND_SPAN, MATCH_SPAN_ONLY)


@dataclass(frozen=True)
class CallableKey:
    """The identifying evidence one side offers for one callable."""

    relative_path: str
    qualified_name: str | None
    signature_discriminator: str | None
    start_line: int | None
    end_line: int | None

    @property
    def span(self) -> tuple[int, int] | None:
        if self.start_line is None or self.end_line is None:
            return None
        return (self.start_line, self.end_line)

    def overlaps(self, other: "CallableKey") -> bool:
        """Spans overlap at all. Deliberately lenient in ONE direction only.

        External tools disagree with ArchLens about where a callable starts --
        decorators, annotations and modifiers are the usual causes -- so
        requiring identical spans would reject correct pairings. Overlap is
        enough to identify, and never enough to conclude anything about a
        metric.
        """
        left, right = self.span, other.span
        if left is None or right is None:
            return False
        return left[0] <= right[1] and right[0] <= left[1]


@dataclass
class MatchResult:
    """Pairings plus everything that could not be paired."""

    matched: list[tuple[CallableKey, CallableKey, str]] = field(default_factory=list)
    ambiguous: list[tuple[CallableKey, list[CallableKey]]] = field(default_factory=list)
    unmatched_archlens: list[CallableKey] = field(default_factory=list)
    unmatched_reference: list[CallableKey] = field(default_factory=list)

    @property
    def comparable_count(self) -> int:
        return len(self.matched)

    def summary(self) -> dict[str, Any]:
        tiers: dict[str, int] = {tier: 0 for tier in MATCH_TIERS}
        for _left, _right, tier in self.matched:
            tiers[tier] += 1
        return {
            "matched": len(self.matched),
            "matched_by_tier": tiers,
            "ambiguous": len(self.ambiguous),
            "unmatched_archlens": len(self.unmatched_archlens),
            "unmatched_reference": len(self.unmatched_reference),
        }


def key_from_row(row: Mapping[str, Any]) -> CallableKey:
    """Build a key from an ArchLens callable row or an adapter record."""

    def integer(name: str) -> int | None:
        value = row.get(name)
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return CallableKey(
        relative_path=str(row.get("relative_path") or "").replace("\\", "/"),
        qualified_name=(row.get("qualified_name") or None),
        signature_discriminator=(row.get("signature_discriminator") or None),
        start_line=integer("start_line"),
        end_line=integer("end_line"),
    )


def _take_unique(
    candidates: Sequence[CallableKey],
) -> CallableKey | None:
    return candidates[0] if len(candidates) == 1 else None


def match_callables(
    archlens: Iterable[Mapping[str, Any]],
    reference: Iterable[Mapping[str, Any]],
) -> MatchResult:
    """Pair ArchLens rows with reference rows using the tiered evidence rule."""
    left_keys = [key_from_row(row) for row in archlens]
    right_keys = [key_from_row(row) for row in reference]
    remaining = list(right_keys)
    result = MatchResult()

    for left in left_keys:
        same_path = [item for item in remaining if item.relative_path == left.relative_path]

        # Tier 1: path + qualified name + signature. The only tier that can
        # separate overloads, so it is tried first and alone.
        tier1 = [
            item
            for item in same_path
            if item.qualified_name == left.qualified_name
            and item.signature_discriminator == left.signature_discriminator
            and left.qualified_name is not None
            and left.signature_discriminator is not None
        ]
        chosen = _take_unique(tier1)
        tier = MATCH_EXACT_SIGNATURE

        if chosen is None:
            # Tier 2: path + qualified name + overlapping span.
            tier2 = [
                item
                for item in same_path
                if item.qualified_name == left.qualified_name
                and left.qualified_name is not None
                and left.overlaps(item)
            ]
            chosen = _take_unique(tier2)
            tier = MATCH_NAME_AND_SPAN
            if chosen is None and len(tier2) > 1:
                result.ambiguous.append((left, tier2))
                continue

        if chosen is None:
            # Tier 3: path + span alone, for a reference that names callables
            # its own way.
            tier3 = [item for item in same_path if left.overlaps(item)]
            chosen = _take_unique(tier3)
            tier = MATCH_SPAN_ONLY
            if chosen is None and len(tier3) > 1:
                result.ambiguous.append((left, tier3))
                continue

        if chosen is None:
            result.unmatched_archlens.append(left)
            continue

        result.matched.append((left, chosen, tier))
        remaining.remove(chosen)

    result.unmatched_reference.extend(remaining)
    return result
