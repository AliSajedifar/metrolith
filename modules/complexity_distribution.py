"""Distribution projections over the existing callable ledger (3.9).

The eleven approved aggregates answer "what is the middle and the maximum".
They cannot answer "how many callables measure exactly 1", and a mean of 3.2
beside a maximum of 412 describes two very different repositories identically.
This module reads the SAME per-callable rows the aggregates are derived from and
reports the shape of the population.

**No metric is calculated here.** `modules.callable_analysis` measured every
value during the run; `modules.callable_ledger` persisted it. This module
counts, sorts and ranks already-published columns. There is no new column, no
recomputation and no second definition of any metric.

**Why percentiles are admissible here when the aggregate set excludes them.**
`callable_ledger` and `complexity_view` both refuse percentiles, and correctly:
a persisted percentile is a frozen contract obligation, and a *threshold count*
("callables above 10") is a policy gate wearing a descriptive label. Neither
applies to this module. Nothing here is persisted into an artifact, and no cut
point is chosen for the reader — the percentiles are fixed positions in the
observed order, and the frequency table is exact and complete. A reader who
wants a threshold must still supply it themselves, through Policy v2.

**Every percentile is an OBSERVED value.** Nearest-rank selection, never
interpolation, for the same reason `lower_median` exists: an interpolated 3.5 is
a value no callable has. It follows that `p50` here is identical to the
persisted `*_median` aggregate for every population size — the two surfaces
cannot disagree, by construction, and a test pins that.

**No buckets.** A histogram needs bucket edges, and a bucket edge is a threshold
chosen by the tool and presented as if it were a property of the data. The
frequency table is exact and needs none, so none is invented.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from modules.complexity_view import (
    STATE_COMPLETE,
    STATE_FAILED,
    UNAVAILABLE,
    render_value,
)

#: Statuses under which a row's measurement may be read as a measurement.
_EVALUABLE_STATUSES = frozenset({"complete", "partial"})

#: The fixed percentile positions, in emission order. Chosen once and stated:
#: these are positions in the observed order, not quality cut points.
PERCENTILE_POSITIONS: tuple[int, ...] = (50, 75, 90, 95, 99)

#: The five distributable columns, each with the ledger status that governs it
#: and the definition it carries. The governing status differs per column and
#: is NOT interchangeable: `nloc` is governed by `nloc_status` while the other
#: four are governed by `structural_complexity_status`, and a file can succeed
#: at one and fail at the other.
DISTRIBUTION_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "cyclomatic_complexity", "Cyclomatic complexity",
        "structural_complexity_status",
        "Metrolith Syntactic Cyclomatic Complexity per callable "
        "(1 + decision points + short-circuit operators). Minimum 1, so a "
        "measured value is never 0 and a 0 would be a defect. Traversal stops "
        "at nested callable boundaries, so the distribution describes measured "
        "callables and not a decomposition of the repository.",
    ),
    (
        "cognitive_complexity", "Cognitive complexity",
        "structural_complexity_status",
        "Metrolith Cognitive Complexity per callable. Minimum 0, and 0 is the "
        "commonest measured value — a large count at 0 is a measurement, not a "
        "gap. NOT Sonar Cognitive Complexity and no compatibility with any "
        "Sonar product is claimed.",
    ),
    (
        "nloc", "NLOC",
        "nloc_status",
        "Physical lines in the callable's declaration span that are neither "
        "blank nor comment-only, after the same comment masking the repository "
        "LOC metric applies. A span INCLUDES lexically nested callables even "
        "though their control flow is excluded, so NLOC and cyclomatic "
        "complexity do not cover the same text.",
    ),
    (
        "max_nesting_depth", "Nesting depth",
        "structural_complexity_status",
        "Deepest structural nesting inside the callable. Structural: `try`, "
        "`finally`, `with`, case bodies and Java `synchronized` each open a "
        "level while adding no decision, so this is not a re-expression of "
        "cyclomatic complexity.",
    ),
    (
        "formal_parameter_count", "Formal parameters",
        "structural_complexity_status",
        "Parameters declared in the callable's own parameter list, as written "
        "in source. A syntactic declaration count: a destructuring pattern is "
        "one parameter, Python `self` is counted and a Go receiver is not. The "
        "single clearest case of a figure that is NOT measurement-equivalent "
        "across languages.",
    ),
)

DISTRIBUTION_FIELDS: tuple[str, ...] = tuple(
    name for name, _label, _status, _definition in DISTRIBUTION_DEFINITIONS
)

_GOVERNING_STATUS: dict[str, str] = {
    name: status for name, _label, status, _definition in DISTRIBUTION_DEFINITIONS
}

PERCENTILE_METHOD = (
    "Nearest rank on the ascending observed order: the value at "
    "`ceil(position/100 * n)`, 1-indexed. No interpolation, so every "
    "percentile reported is a value some measured callable actually exhibits. "
    "`p50` computed this way is identical to the persisted lower median for "
    "every population size."
)

DESCRIPTIVE_ONLY = (
    "Descriptive evidence, not a quality verdict. A distribution reports how "
    "many callables measured what. There is no grade, no healthy/unhealthy "
    "label, no universal threshold and no composite score: a long tail is a "
    "fact about syntax, not a defect, and this module names no cut point at "
    "which a value becomes bad."
)

NO_BUCKETS_STATEMENT = (
    "Reported as exact frequencies rather than as a histogram. Bucket edges "
    "are thresholds chosen by the tool and shown as if they were properties of "
    "the data; the frequency table is exact and needs none."
)

COVERAGE_LIMITATION = (
    "The population is the canonical `methods_functions` set. Constructors, "
    "lambdas, nested and anonymous callables are outside Complexity Contract "
    "1.0.0 and appear in no row, so this is the distribution of MEASURED "
    "callables and not of every callable in the source."
)

CROSS_LANGUAGE_LIMITATION = (
    "Distributions are NOT comparable across languages. The languages declare "
    "parameters, nest structure and delimit callables differently, so a "
    "combined distribution mixes populations that were not measured alike. "
    "Per-language distributions are reported separately for that reason."
)


#: The statements in this module that DENY a quality reading, and are therefore
#: the only places judgment vocabulary ("grade", "score", "threshold") may
#: legitimately appear. Stripped by the surface tests, which then require the
#: remaining document to contain none of that vocabulary at all.
PROHIBITION_STATEMENTS: tuple[str, ...] = (DESCRIPTIVE_ONLY, NO_BUCKETS_STATEMENT)


def _numeric(value: Any) -> int | None:
    """One ledger cell as an integer, or ``None`` when it is not a measurement.

    Ledger rows reach this module from two places with two representations: the
    in-memory records carry `int`, and a CSV read-back carries `str` with `""`
    for null. Both are accepted, and neither `""` nor `None` becomes 0.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_status(row: Mapping[str, Any], field: str) -> str | None:
    return row.get(_GOVERNING_STATUS[field])


def measured_values(
    rows: Iterable[Mapping[str, Any]], field: str
) -> list[int]:
    """Every measurement of one column, ascending.

    A row whose governing status is not evaluable contributed no measurement
    and is excluded rather than counted as zero. A null cell under an evaluable
    status is likewise excluded: for `cognitive_complexity` that is exactly the
    row whose structural measurement failed.
    """
    values: list[int] = []
    for row in rows:
        if _row_status(row, field) not in _EVALUABLE_STATUSES:
            continue
        value = _numeric(row.get(field))
        if value is not None:
            values.append(value)
    values.sort()
    return values


def percentile(ordered: Sequence[int], position: int) -> int | None:
    """Nearest-rank percentile over an ALREADY ASCENDING sequence.

    Returns an observed value or ``None``; never interpolates, and never
    returns 0 for an empty population.
    """
    if not ordered:
        return None
    if not 0 < position <= 100:
        raise ValueError(f"percentile position out of range: {position}")
    # ceil(position/100 * n) without floating point, so the rank does not
    # depend on binary rounding of, say, 0.99 * 100.
    count = len(ordered)
    rank = -((-position * count) // 100)
    return ordered[max(1, rank) - 1]


def frequency(ordered: Sequence[int]) -> list[tuple[int, int]]:
    """Exact (value, count) pairs, ascending by value. Complete, never binned."""
    table: dict[int, int] = {}
    for value in ordered:
        table[value] = table.get(value, 0) + 1
    return sorted(table.items())


def cumulative(ordered: Sequence[int]) -> list[dict[str, Any]]:
    """Cumulative counts and shares at each distinct observed value.

    ``at_or_below`` answers "how many callables measure this value or less",
    which is the question a frequency table alone makes the reader do by hand.
    The share is a proportion of the MEASURED population and is stated as such.
    """
    total = len(ordered)
    running = 0
    rows: list[dict[str, Any]] = []
    for value, count in frequency(ordered):
        running += count
        rows.append({
            "value": value,
            "count": count,
            "at_or_below": running,
            "share_at_or_below": round(running / total, 4) if total else None,
        })
    return rows


def distribution(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    """One column's distribution. Empty population yields nulls, never zeros."""
    ordered = measured_values(rows, field)
    total = len(ordered)
    return {
        "field": field,
        "measured_count": total,
        "unmeasured_count": len(rows) - total,
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "distinct_values": len(frequency(ordered)),
        "percentiles": {
            f"p{position}": percentile(ordered, position)
            for position in PERCENTILE_POSITIONS
        },
        "frequency": [
            {"value": value, "count": count} for value, count in frequency(ordered)
        ],
        "cumulative": cumulative(ordered),
    }


def _rendered(value: Any, *, measured: bool) -> str:
    return render_value(value, state=STATE_COMPLETE if measured else STATE_FAILED)


def profile(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every distributable column for one population of ledger rows."""
    entries: list[dict[str, Any]] = []
    for field, label, status_field, definition in DISTRIBUTION_DEFINITIONS:
        computed = distribution(rows, field)
        measured = computed["measured_count"] > 0
        entries.append({
            **computed,
            "label": label,
            "definition": definition,
            "governing_status_field": status_field,
            "measured": measured,
            "rendered": {
                "min": _rendered(computed["min"], measured=measured),
                "max": _rendered(computed["max"], measured=measured),
                **{
                    name: _rendered(value, measured=measured)
                    for name, value in computed["percentiles"].items()
                },
            },
        })
    return {
        "row_count": len(rows),
        "percentile_positions": list(PERCENTILE_POSITIONS),
        "percentile_method": PERCENTILE_METHOD,
        "distributions": entries,
    }


def presentation(
    result: Mapping[str, Any], rows: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Everything a renderer needs for one repository's distributions.

    The complexity measurement state is read through `complexity_view`, so this
    module never answers "was complexity measured" a second way. When the state
    is not evaluable no distribution is reported at all — an unavailable
    measurement has no shape, and rendering an empty distribution would present
    one.

    Language separation is preserved: a row's `detected_language` partitions the
    population, and the combined profile is reported beside the per-language
    ones rather than instead of them.
    """
    from modules import complexity_view

    state = complexity_view.state_of(result)
    evaluable = state in complexity_view.EVALUABLE_STATES

    by_language: dict[str, Any] = {}
    if evaluable:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            language = row.get("detected_language")
            if language:
                grouped.setdefault(str(language), []).append(row)
        for language, language_rows in sorted(grouped.items()):
            by_language[language] = profile(language_rows)

    return {
        "state": state,
        "state_meaning": complexity_view.STATE_MEANINGS[state],
        "evaluable": evaluable,
        "complexity_contract_version": (
            (complexity_view.repository_block(result) or {})
            .get("complexity_contract_version")
        ),
        "combined": profile(rows) if evaluable else None,
        "by_language": by_language,
    }


def provenance(
    manifest: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """What a reader needs to know one distribution's meaning."""
    from modules import complexity_view

    base = complexity_view.provenance(manifest, result)
    return {
        "complexity_contract_version": base["complexity_contract_version"],
        "measurement_state": base["measurement_state"],
        "measurement_state_meaning": base["measurement_state_meaning"],
        "analyzed_scope": base["analyzed_scope"],
        "percentile_method": PERCENTILE_METHOD,
        "percentile_positions": list(PERCENTILE_POSITIONS),
        "governing_status_fields": dict(_GOVERNING_STATUS),
        "distribution_definitions": {
            field: definition
            for field, _label, _status, definition in DISTRIBUTION_DEFINITIONS
        },
        "no_buckets": NO_BUCKETS_STATEMENT,
        "coverage_limitation": COVERAGE_LIMITATION,
        "cross_language_limitation": CROSS_LANGUAGE_LIMITATION,
        "descriptive_only": DESCRIPTIVE_ONLY,
    }


def frequency_head(
    entry: Mapping[str, Any], *, limit: int
) -> tuple[list[Mapping[str, Any]], int]:
    """The first ``limit`` frequency rows and how many were not shown.

    Truncation is a RENDERING concession for a narrow surface, so the remainder
    is returned rather than dropped: a caller that cannot show every distinct
    value must still be able to say how many it withheld.
    """
    table = list(entry.get("frequency") or ())
    if limit < 0:
        raise ValueError("limit must not be negative")
    return table[:limit], max(0, len(table) - limit)


__all__ = [
    "COVERAGE_LIMITATION",
    "CROSS_LANGUAGE_LIMITATION",
    "DESCRIPTIVE_ONLY",
    "DISTRIBUTION_DEFINITIONS",
    "DISTRIBUTION_FIELDS",
    "NO_BUCKETS_STATEMENT",
    "PERCENTILE_METHOD",
    "PERCENTILE_POSITIONS",
    "PROHIBITION_STATEMENTS",
    "UNAVAILABLE",
    "cumulative",
    "distribution",
    "frequency",
    "frequency_head",
    "measured_values",
    "percentile",
    "presentation",
    "profile",
    "provenance",
]
