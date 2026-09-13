"""One presentation seam for Complexity Contract 1.0.0 (plan section 14).

`summary.md`, the HTML report, `explain` and `metrolith diff` all show complexity,
and all four have to answer the same three questions the same way. Answering them
in four places is how the answers come apart, so they are answered once here.

**Three states, never two.** A pre-1.9 run carries no complexity block at all;
a 1.9 run may carry one whose values are null; and a measured value may be zero.
:func:`presentation` reports `absent`, `failed`/`not_applicable` and `complete`
as distinct states, and :func:`render_value` renders an unavailable value as
``unavailable`` — never as ``0``. A repository with no callables and a
repository whose parse failed produce the same zero rows and must never produce
the same sentence.

**Only the approved eleven aggregates are exposed.** The tuple below is the
whole surface: no derived ratio, no threshold, no composite score, no
callable-level detail beyond a descriptive ranking. Complexity Contract 1.0.0 is
frozen; nothing here computes a metric.

**Provenance travels with the number.** :func:`provenance` returns the contract
version, the measurement state, the analyzed scope and the definition of every
aggregate shown, so a reader never has to infer what a figure means — including
that `formal_parameter_count` is a syntactic declaration count and is **not**
measurement-equivalent across languages.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: The run measured complexity and every measured file succeeded.
STATE_COMPLETE = "complete"
#: Some measured file could not be measured; the numbers are partial observations.
STATE_PARTIAL = "partial"
#: Complexity was attempted and produced nothing usable.
STATE_FAILED = "failed"
#: There was no supported source to measure.
STATE_NOT_APPLICABLE = "not_applicable"
#: The artifact predates Complexity Contract 1.0.0 entirely. Distinct from every
#: state above: nothing was attempted, so nothing is null and nothing is zero.
STATE_ABSENT = "absent"

EVALUABLE_STATES = frozenset({STATE_COMPLETE, STATE_PARTIAL})

#: Rendered wherever a value is not a measurement. Deliberately not "0", not
#: "-" and not blank: each of those reads as a quantity or as an omission.
UNAVAILABLE = "unavailable"

#: The approved aggregate set, in emission order, with the definition each one
#: carries. The definition ships beside the number because "median" alone does
#: not say *which* median, and the answer here is not the usual one.
AGGREGATE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    (
        "callable_count", "Callables measured",
        "Rows emitted for the canonical `methods_functions` population. "
        "Constructors, lambdas, nested and anonymous callables are outside "
        "Complexity Contract 1.0.0 and are measured nowhere.",
    ),
    (
        "cyclomatic_complexity_total", "Cyclomatic total",
        "Sum over measured callables of Metrolith Syntactic Cyclomatic "
        "Complexity (1 + decision points + short-circuit operators). It is NOT "
        "the total decision count of the repository: excluded nested callables "
        "contribute to no row.",
    ),
    (
        "cyclomatic_complexity_mean", "Cyclomatic mean",
        "Arithmetic mean over measured callables, rounded to four decimals.",
    ),
    (
        "cyclomatic_complexity_median", "Cyclomatic lower median",
        "The LOWER median: with an even population the lower of the two middle "
        "values, so the figure is always a value some callable actually has.",
    ),
    (
        "cyclomatic_complexity_max", "Cyclomatic max",
        "Largest value over measured callables.",
    ),
    (
        "nloc_median", "NLOC lower median",
        "Lower median of per-callable NLOC: physical lines in the declaration "
        "span that are neither blank nor comment-only, after the same comment "
        "masking the repository LOC metric applies. A span INCLUDES lexically "
        "nested callables even though their control flow is excluded.",
    ),
    ("nloc_max", "NLOC max", "Largest per-callable NLOC."),
    (
        "max_nesting_depth_median", "Nesting depth lower median",
        "Lower median of the deepest structural nesting inside each callable. "
        "Structural: `try`, `finally`, `with`, case bodies and Java "
        "`synchronized` each open a level while adding no decision.",
    ),
    ("max_nesting_depth_max", "Nesting depth max", "Largest per-callable nesting depth."),
    (
        "formal_parameter_count_median", "Formal parameters lower median",
        "Lower median of parameters declared in each callable's own parameter "
        "list, as written in source. A syntactic declaration count only.",
    ),
    (
        "formal_parameter_count_max", "Formal parameters max",
        "Largest per-callable formal parameter count.",
    ),
)

AGGREGATE_FIELDS: tuple[str, ...] = tuple(name for name, _label, _note in AGGREGATE_DEFINITIONS)

COVERAGE_LIMITATION = (
    "Per-callable complexity describes a callable's own control flow, not a "
    "decomposition of the file. Traversal stops at every nested callable "
    "boundary, so control flow inside constructors, lambdas, callbacks, nested "
    "functions, anonymous-class methods and initializer blocks is measured "
    "nowhere. Summing cyclomatic complexity over rows does not total a file's "
    "decisions."
)

CROSS_LANGUAGE_LIMITATION = (
    "Complexity figures are NOT measurement-equivalent across languages. "
    "`formal_parameter_count` is the clearest case — the languages declare "
    "parameters differently, a destructuring pattern is one parameter, `self` "
    "is counted in Python and a Java receiver parameter is not — but the same "
    "caution applies to every syntax-sensitive figure here. Per-language "
    "aggregates are reported separately for that reason, and no cross-language "
    "composite is produced."
)

DESCRIPTIVE_ONLY = (
    "Descriptive evidence, not a quality verdict. There is no threshold, no "
    "rating and no composite score: a high number is a fact about syntax, not "
    "a defect."
)

STATE_MEANINGS: dict[str, str] = {
    STATE_COMPLETE: "every measured file produced a complexity measurement",
    STATE_PARTIAL: (
        "some measured file could not be measured; the figures are partial "
        "observations and are not verified complete values"
    ),
    STATE_FAILED: (
        "complexity measurement was attempted and produced nothing usable; the "
        "figures are unavailable and are never read as zero"
    ),
    STATE_NOT_APPLICABLE: "there was no supported source to measure",
    STATE_ABSENT: (
        "this run predates Complexity Contract 1.0.0 and recorded no complexity "
        "at all; nothing was attempted, so there is no value, null or otherwise"
    ),
}


def repository_block(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The recorded complexity block, or ``None`` when the run has none.

    ``None`` is the ABSENT case and must not be replaced by an empty dict: an
    empty dict would read as "measured, nothing found".

    The producer writes the block under ``metrics.complexity``, beside the
    other measurement output. A top-level ``complexity`` is accepted as well so
    a caller holding a already-narrowed mapping is not silently reported absent
    — reading the wrong path would manufacture exactly the false ABSENT this
    module exists to prevent.
    """
    metrics = result.get("metrics")
    if isinstance(metrics, Mapping):
        block = metrics.get("complexity")
        if isinstance(block, Mapping):
            return block
    block = result.get("complexity")
    return block if isinstance(block, Mapping) else None


def state_of(result: Mapping[str, Any]) -> str:
    block = repository_block(result)
    if block is None:
        return STATE_ABSENT
    status = block.get("status")
    return str(status) if status in STATE_MEANINGS else STATE_FAILED


def render_value(value: Any, *, state: str) -> str:
    """Render one aggregate. An unavailable value is never a zero."""
    if state not in EVALUABLE_STATES or value is None:
        return UNAVAILABLE
    return str(value)


def presentation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Everything a renderer needs for one repository, already decided here."""
    block = repository_block(result)
    state = state_of(result)
    aggregate = (block or {}).get("aggregate") or {}
    by_language = (block or {}).get("by_language") or {}

    rows = [
        {
            "field": field,
            "label": label,
            "definition": definition,
            "value": aggregate.get(field) if state in EVALUABLE_STATES else None,
            "rendered": render_value(aggregate.get(field), state=state),
        }
        for field, label, definition in AGGREGATE_DEFINITIONS
    ]

    languages = {
        language: [
            {
                "field": field,
                "label": label,
                "value": (values or {}).get(field),
                "rendered": render_value((values or {}).get(field), state=state),
            }
            for field, label, _definition in AGGREGATE_DEFINITIONS
        ]
        for language, values in sorted(by_language.items())
    }

    return {
        "state": state,
        "state_meaning": STATE_MEANINGS[state],
        "evaluable": state in EVALUABLE_STATES,
        "complexity_contract_version": (block or {}).get("complexity_contract_version"),
        "unavailable_reason": (block or {}).get("unavailable_reason"),
        "aggregate": rows,
        "by_language": languages,
    }


def provenance(
    manifest: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """What a reader needs to know a complexity figure's meaning.

    Four things, and the plan names all four: the contract version, the
    measurement state, the analyzed scope, and the definition of the aggregate
    being shown. A number without them is not explainable.
    """
    block = repository_block(result)
    acquisition = result.get("acquisition") or {}
    state = state_of(result)
    return {
        "complexity_contract_version": (
            (block or {}).get("complexity_contract_version")
            or manifest.get("complexity_contract_version")
        ),
        "measurement_state": state,
        "measurement_state_meaning": STATE_MEANINGS[state],
        "unavailable_reason": (block or {}).get("unavailable_reason"),
        "analyzed_scope": {
            "analysis_scope_hash": result.get("analysis_scope_hash"),
            "analyzed_commit_sha": acquisition.get("analyzed_commit_sha"),
            "source_mode": result.get("source_mode"),
        },
        "aggregate_definitions": {
            field: definition for field, _label, definition in AGGREGATE_DEFINITIONS
        },
        "coverage_limitation": COVERAGE_LIMITATION,
        "cross_language_limitation": CROSS_LANGUAGE_LIMITATION,
        "descriptive_only": DESCRIPTIVE_ONLY,
    }


# ---------------------------------------------------------------------------
# Metrolith Cognitive Complexity — Complexity Contract 2.0.0 section 12
# ---------------------------------------------------------------------------
#
# Same seam, second metric. It lives here rather than in a module of its own
# because the four surfaces must answer the same questions the same way for
# BOTH metrics, and two seams is how two answers appear.
#
# **The aggregates are DERIVED here, not read from the artifact.** Artifact
# Schema 1.10 persists `cognitive_complexity` per callable and
# `cognitive_measurement_state` per repository; it does not persist a cognitive
# aggregate, and this module computes one from the rows. Two consequences, both
# wanted: there is exactly one definition of each aggregate, and every existing
# 1.10 run gains these figures without being re-analyzed.

#: The metric's name. Never "Sonar Cognitive Complexity" and never
#: "Sonar-compatible": G0 established by execution that the four Sonar-lineage
#: references disagree with each other and two disagree with the published
#: model, so there is no single behaviour to be compatible with. A test scans
#: every rendered surface for that vocabulary.
COGNITIVE_METRIC_NAME = "Metrolith Cognitive Complexity"

COGNITIVE_NAMING_STATEMENT = (
    "Metrolith Cognitive Complexity is an explicit versioned contract inspired "
    "by the published Cognitive Complexity model. It is NOT Sonar Cognitive "
    "Complexity and no compatibility with any Sonar product is claimed: the "
    "available Sonar-lineage implementations disagree with each other, so "
    "there is no single behaviour to match. Every definitional divergence is "
    "enumerated in the differential-validation record."
)

#: Recorded per repository by Artifact 1.10.
COGNITIVE_MEASURED = "measured"
COGNITIVE_PARTIAL = "partial"
COGNITIVE_FAILED = "failed"
COGNITIVE_NOT_APPLICABLE = "not_applicable"
#: The run carries a complexity block but no cognitive state at all — a 1.9
#: artifact. Distinct from `failed`: nothing was attempted, so there is no
#: value, null or otherwise, and distinct again from a MEASURED ZERO.
COGNITIVE_ABSENT = "absent"

COGNITIVE_EVALUABLE_STATES = frozenset({COGNITIVE_MEASURED, COGNITIVE_PARTIAL})

COGNITIVE_STATE_MEANINGS: dict[str, str] = {
    COGNITIVE_MEASURED: "every measured file produced a cognitive measurement",
    COGNITIVE_PARTIAL: (
        "some measured file could not be measured; the figures are partial "
        "observations and are not verified complete values"
    ),
    COGNITIVE_FAILED: (
        "cognitive measurement was attempted and produced nothing usable; the "
        "figures are unavailable and are never read as zero"
    ),
    COGNITIVE_NOT_APPLICABLE: "there was no supported source to measure",
    COGNITIVE_ABSENT: (
        "this run predates Complexity Contract 2.0.0 and recorded no cognitive "
        "complexity at all; nothing was attempted, so there is no value, null "
        "or otherwise — which is not the same as a measured zero"
    ),
}

#: The smallest justified set. Five figures, each a direct summary of the
#: persisted per-callable column.
#:
#: Deliberately ABSENT: percentiles, thresholds, hotspot scores, ratios,
#: ratings and composites. Cognitive complexity has no defensible threshold —
#: the references disagree on the values themselves — so publishing one would
#: manufacture authority the measurement does not have.
COGNITIVE_AGGREGATE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    (
        "cognitive_callable_count", "Cognitive callables measured",
        "Rows carrying a cognitive measurement. This is NOT `callable_count`: "
        "a row whose structural measurement failed carries a null cognitive "
        "value and is excluded here, so the two counts differ exactly where a "
        "measurement was attempted and failed.",
    ),
    (
        "cognitive_complexity_total", "Cognitive total",
        "Sum over measured callables of Metrolith Cognitive Complexity. It is "
        "NOT a repository-wide measure of comprehension burden: traversal stops "
        "at every nested callable boundary, so excluded nested callables "
        "contribute to no row.",
    ),
    (
        "cognitive_complexity_mean", "Cognitive mean",
        "Arithmetic mean over measured callables, rounded to four decimals.",
    ),
    (
        "cognitive_complexity_median", "Cognitive lower median",
        "The LOWER median: with an even population the lower of the two middle "
        "values, so the figure is always a value some callable actually has. "
        "On real repositories this is usually 0, because most callables "
        "measure 0 — which is a measurement, not a gap.",
    ),
    (
        "cognitive_complexity_max", "Cognitive max",
        "Largest value over measured callables.",
    ),
)

COGNITIVE_AGGREGATE_FIELDS: tuple[str, ...] = tuple(
    name for name, _label, _note in COGNITIVE_AGGREGATE_DEFINITIONS
)

COGNITIVE_ZERO_STATEMENT = (
    "0 is an ordinary measured value for this metric, and the commonest one: "
    "`cognitive_complexity` has minimum 0 where cyclomatic complexity has "
    "minimum 1. A blank cell means the measurement is unavailable and is never "
    "read as 0; a `0` means a callable was measured and found to carry no "
    "comprehension burden."
)

COGNITIVE_COVERAGE_LIMITATION = (
    "Cognitive complexity describes one callable's own control flow. Rule "
    "table section 7.2 stops attribution at every nested callable boundary, so "
    "a lambda, callback, closure or nested function contributes to no "
    "enclosing row. Summing the column does not total a file's comprehension "
    "burden."
)


def cognitive_state_of(result: Mapping[str, Any]) -> str:
    """The recorded cognitive measurement state, or ABSENT.

    Three absences are kept apart, which is the whole point:

    * no complexity block at all — a pre-1.9 run;
    * a complexity block with no `cognitive_measurement_state` — a 1.9 run,
      which measured structural complexity and never attempted cognitive;
    * a recorded `failed` / `not_applicable` — attempted, nothing usable.

    All three render as unavailable, and only the first two are `absent`.
    """
    block = repository_block(result)
    if block is None:
        return COGNITIVE_ABSENT
    state = block.get("cognitive_measurement_state")
    if state is None:
        return COGNITIVE_ABSENT
    return str(state) if state in COGNITIVE_STATE_MEANINGS else COGNITIVE_FAILED


def cognitive_aggregate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive the five aggregates from callable rows.

    A null `cognitive_complexity` is an unavailable measurement and is excluded
    rather than counted as zero — the distinction the whole contract rests on.
    When nothing is measurable every figure is null, including the count, so an
    empty population never renders as a measured zero.
    """
    values: list[int] = []
    for row in rows:
        value = row.get("cognitive_complexity")
        if value is None or value == "":
            continue
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue

    if not values:
        return {name: None for name in COGNITIVE_AGGREGATE_FIELDS}
    return {
        "cognitive_callable_count": len(values),
        "cognitive_complexity_total": sum(values),
        "cognitive_complexity_mean": round(sum(values) / len(values), 4),
        "cognitive_complexity_median": lower_median(values),
        "cognitive_complexity_max": max(values),
    }


def lower_median(values: Sequence[int]) -> int | None:
    """Lower median: always a value some measured callable actually exhibits.

    The same rule Complexity Contract 1.0.0 uses, restated here rather than
    imported so the presentation seam has no dependency on the producer.
    """
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def cognitive_presentation(
    result: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Everything a renderer needs for one repository's cognitive figures."""
    state = cognitive_state_of(result)
    evaluable = state in COGNITIVE_EVALUABLE_STATES
    aggregate = cognitive_aggregate(rows) if evaluable else {
        name: None for name in COGNITIVE_AGGREGATE_FIELDS
    }

    by_language: dict[str, list[dict[str, Any]]] = {}
    if evaluable:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            language = row.get("detected_language")
            if language:
                grouped.setdefault(str(language), []).append(row)
        for language, language_rows in sorted(grouped.items()):
            values = cognitive_aggregate(language_rows)
            by_language[language] = [
                {
                    "field": field,
                    "label": label,
                    "value": values.get(field),
                    "rendered": render_value(values.get(field), state=(
                        STATE_COMPLETE if evaluable else STATE_FAILED
                    )),
                }
                for field, label, _definition in COGNITIVE_AGGREGATE_DEFINITIONS
            ]

    return {
        "metric_name": COGNITIVE_METRIC_NAME,
        "state": state,
        "state_meaning": COGNITIVE_STATE_MEANINGS[state],
        "evaluable": evaluable,
        "complexity_contract_version": (
            (repository_block(result) or {}).get("complexity_contract_version")
        ),
        "aggregate": [
            {
                "field": field,
                "label": label,
                "definition": definition,
                "value": aggregate.get(field),
                "rendered": render_value(
                    aggregate.get(field),
                    state=STATE_COMPLETE if evaluable else STATE_FAILED,
                ),
            }
            for field, label, definition in COGNITIVE_AGGREGATE_DEFINITIONS
        ],
        "by_language": by_language,
    }


def cognitive_provenance(
    manifest: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """What a reader needs to know a cognitive figure's meaning."""
    block = repository_block(result)
    acquisition = result.get("acquisition") or {}
    state = cognitive_state_of(result)
    return {
        "metric_name": COGNITIVE_METRIC_NAME,
        "naming": COGNITIVE_NAMING_STATEMENT,
        "complexity_contract_version": (
            (block or {}).get("complexity_contract_version")
            or manifest.get("complexity_contract_version")
        ),
        "measurement_state": state,
        "measurement_state_meaning": COGNITIVE_STATE_MEANINGS[state],
        "analyzed_scope": {
            "analysis_scope_hash": result.get("analysis_scope_hash"),
            "analyzed_commit_sha": acquisition.get("analyzed_commit_sha"),
            "source_mode": result.get("source_mode"),
        },
        "aggregate_definitions": {
            field: definition
            for field, _label, definition in COGNITIVE_AGGREGATE_DEFINITIONS
        },
        "zero_statement": COGNITIVE_ZERO_STATEMENT,
        "coverage_limitation": COGNITIVE_COVERAGE_LIMITATION,
        "cross_language_limitation": CROSS_LANGUAGE_LIMITATION,
        "descriptive_only": DESCRIPTIVE_ONLY,
    }


#: Rankings the report may show. Descriptive orderings of already-published
#: per-callable fields — no new metric, no threshold, no colouring.
RANKING_FIELDS: tuple[tuple[str, str], ...] = (
    ("cyclomatic_complexity", "Cyclomatic complexity"),
    ("max_nesting_depth", "Nesting depth"),
    ("nloc", "NLOC"),
    ("formal_parameter_count", "Formal parameters"),
    ("cognitive_complexity", "Cognitive complexity"),
)


def rank_callables(
    rows: Sequence[Mapping[str, Any]], field: str, *, limit: int = 10
) -> list[Mapping[str, Any]]:
    """Top rows by one published field. Ties broken deterministically.

    A row whose value is unavailable is not ranked at all — ranking it would
    require treating an absent measurement as a number.
    """
    def numeric(row: Mapping[str, Any]) -> int | None:
        value = row.get(field)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    ranked = [(numeric(row), row) for row in rows]
    ranked = [(value, row) for value, row in ranked if value is not None]
    ranked.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("relative_path") or ""),
            str(item[1].get("qualified_name") or ""),
            str(item[1].get("start_line") or ""),
        )
    )
    return [row for _value, row in ranked[:limit]]
