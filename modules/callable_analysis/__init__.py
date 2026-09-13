"""Per-callable complexity analysis (Complexity Contract 1.0.0).

One canonical traversal per file, over the tree `core_metrics` already selected.
No file is re-read and no source is re-parsed: `_analyze_file` hands in the exact
`root` and `source` the entity counters see, so every recovery and fallback
decision is inherited rather than duplicated.

Language dispatch is a dict literal, not a plugin framework. There is no
registration, no discovery and no entry point -- adding a language is adding a
key, exactly as `GRAMMAR_IDENTITIES` does it.

Two separate semantics, one package
===================================

**A. Canonical callable discovery** is GLOBAL and boundary-independent. It walks
the whole file and yields every callable in the population, however deeply
nested. A method of a class declared inside a function is in the population, so
discovery must descend *through* that function and that class to find it.

**B. Metric attribution** is BOUNDED. It descends from one callable's body and
stops at every nested callable boundary, so an unmeasured lambda's control flow
contributes to no record (Complexity Contract 1.0.0 section 3).

**These are different questions and the boundary sets belong only to B.**
Conflating them is the natural implementation mistake, and it fails silently in
one direction: a discovery walk that honours boundaries simply never emits the
inner method, `methods_functions` still counts it, and the keystone
reconciliation is what catches the loss. Each language module therefore declares
``BOUNDARY_NODES`` *beside* its ``discover_callables``, and discovery does not
consult it.

The two may share one physical pass, and are cheap either way: attribution
descents partition the tree, because a parent's descent stops exactly where a
nested callable's begins, so total work stays O(nodes).

Milestone scope: **C1 discovers** the canonical population with identity and
location. Metric fields are declared on the record and remain ``None`` until C2
fills them. The keystone reconciliation lands here, before any metric exists, so
a population error cannot hide behind a metric error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import (
    COMPLEXITY_CONTRACT_VERSION,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NOT_APPLICABLE,
    STATUS_PARTIAL,
    CallableRecord,
    assign_row_ids,
)

__all__ = [
    "COMPLEXITY_CONTRACT_VERSION",
    "CallableAnalysisResult",
    "CallableRecord",
    "analyze_callables",
    "reconcile_file",
    "SUPPORTED_LANGUAGES",
]


@dataclass(slots=True)
class CallableAnalysisResult:
    """One file's callable records and the two statuses that govern them."""

    records: list[CallableRecord] = field(default_factory=list)
    structural_complexity_status: str = STATUS_NOT_APPLICABLE
    nloc_status: str = STATUS_NOT_APPLICABLE

    @property
    def callable_count(self) -> int | None:
        """Row count, or ``None`` when nothing was measurable.

        ``None`` is not zero. A file whose parse failed emitted no records
        because it could not be measured, which is a different fact from a file
        that genuinely contains no callable -- and the difference is exactly what
        the keystone gate reads.
        """
        if self.structural_complexity_status == STATUS_FAILED:
            return None
        if self.structural_complexity_status == STATUS_NOT_APPLICABLE:
            return None
        return len(self.records)


def _go_visitor(root: Any, source: bytes, lines: Any) -> list[CallableRecord]:
    from . import go

    return go.discover_callables(root, source, lines)


def _java_visitor(root: Any, source: bytes, lines: Any) -> list[CallableRecord]:
    from . import java

    return java.discover_callables(root, source, lines)


def _js_ts_visitor(root: Any, source: bytes, lines: Any) -> list[CallableRecord]:
    from . import js_ts

    return js_ts.discover_callables(root, source, lines)


def _python_visitor(root: Any, source: bytes, lines: Any) -> list[CallableRecord]:
    # `root` is a CPython `ast` module node, not a tree-sitter node: Python is
    # the one language `core_metrics` does not parse with tree-sitter, and the
    # analyzer inherits whatever tree the producer already built.
    from . import python

    return python.discover_callables(root, lines)


#: language -> discovery function. A dict literal, deliberately.
_VISITORS = {
    "Go": _go_visitor,
    "Java": _java_visitor,
    "JavaScript": _js_ts_visitor,
    "TypeScript": _js_ts_visitor,
    "Python": _python_visitor,
}

SUPPORTED_LANGUAGES = tuple(_VISITORS)


def analyze_callables(
    language: str | None,
    root: Any,
    source: bytes,
    relative_path: str,
    *,
    entities_status: str = STATUS_COMPLETE,
    location_maps_to_original_source: bool = True,
    raw_text: str | None = None,
    masked_text: str | None = None,
) -> CallableAnalysisResult:
    """Enumerate one file's canonical callables.

    ``entities_status`` is the status `core_metrics` already assigned to the
    entity extraction for this file. It is passed in rather than recomputed
    because the two must agree by construction: the callable enumeration and
    `methods_functions` reject the same declarations for the same reasons, so
    they cannot honestly carry different statuses.
    """
    visitor = _VISITORS.get(language or "")
    if visitor is None or root is None:
        return CallableAnalysisResult(
            [], STATUS_NOT_APPLICABLE, STATUS_NOT_APPLICABLE
        )

    if entities_status == STATUS_FAILED:
        return CallableAnalysisResult([], STATUS_FAILED, STATUS_FAILED)

    from .metrics import LineIndex

    lines = (
        LineIndex.build(raw_text, masked_text)
        if raw_text is not None and masked_text is not None
        else None
    )
    records = visitor(root, source, lines)
    assign_row_ids(records, relative_path, language or "")

    status = STATUS_PARTIAL if entities_status == STATUS_PARTIAL else STATUS_COMPLETE
    # NLOC has its own failure mode: the comment-masking machinery can fail for a
    # file whose tree traversed fine. Without a line index `nloc` stays null --
    # unavailable, never zero -- and the status says so.
    nloc_status = status if lines is not None else STATUS_FAILED
    for record in records:
        record.structural_complexity_status = status
        record.nloc_status = nloc_status
        record.location_maps_to_original_source = location_maps_to_original_source

    return CallableAnalysisResult(records, status, nloc_status)


def reconcile_file(
    result: CallableAnalysisResult,
    methods_functions: int | None,
    methods_functions_status: str,
) -> dict[str, Any]:
    """The keystone reconciliation, status-gated.

    Complexity Contract 1.0.0 section 5. Equality is a claim about two
    computations over the same selected tree, so it is asserted only where that
    tree produced an evaluable measurement.

    **Zero records under a failed measurement is never a measured zero.** That is
    the one rule this function exists to enforce, and the reason it returns
    ``not_evaluable`` rather than comparing against ``0``.
    """
    count = result.callable_count

    if methods_functions_status == STATUS_FAILED or methods_functions is None:
        return {
            "outcome": "not_evaluable",
            "callable_count": count,
            "methods_functions": methods_functions,
            "observation_quality": None,
            "reason": (
                "methods_functions is unavailable; an absent measurement is "
                "never read as zero"
            ),
        }
    if methods_functions_status == STATUS_NOT_APPLICABLE:
        return {
            "outcome": "not_evaluable",
            "callable_count": count,
            "methods_functions": methods_functions,
            "observation_quality": None,
            "reason": "no measurable source for this scope",
        }
    if count is None:
        return {
            "outcome": "not_evaluable",
            "callable_count": None,
            "methods_functions": methods_functions,
            "observation_quality": None,
            "reason": (
                "the callable enumeration recorded no value; an absent "
                "measurement is never read as zero"
            ),
        }

    quality = "partial" if methods_functions_status == STATUS_PARTIAL else "complete"
    if count == methods_functions:
        return {
            "outcome": "exact",
            "callable_count": count,
            "methods_functions": methods_functions,
            "observation_quality": quality,
            "reason": (
                "equality holds over what both computations could observe; a "
                "partial equality is not a verified count"
                if quality == "partial"
                else None
            ),
        }
    return {
        "outcome": "residual",
        "callable_count": count,
        "methods_functions": methods_functions,
        "observation_quality": quality,
        "residual": methods_functions - count,
        "reason": (
            "the callable enumeration and methods_functions disagree over the "
            "same selected tree; one of the two population predicates has drifted"
        ),
    }
