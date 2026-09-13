"""One presentation seam for source line composition (Evidence Presentation 3.9).

Metrolith has classified every physical line since Artifact Schema 1.3.0. The
five counts live in `metrics.aggregate` and `metrics.by_language[*]` of every
`analysis.json`, and per file in the contribution ledger. Only one of them —
`code_lines`, republished as `lines_of_code` — was ever shown. This module
exposes the other four. **Nothing here classifies a line.**

`modules.core_metrics._classify_lines` is the single classifier, shared with
per-callable NLOC through `modules.syntax_predicates.line_is_code`. A second
classifier is the one failure this seam exists to prevent, so this module reads
recorded counts and never inspects source.

**The state gates the number, not the other way round.** When `loc_status` is
`failed`, `_finalize_metric` nulls `lines_of_code` but leaves the five component
counts at their accumulated value, which for a repository that measured nothing
is `0`. A renderer that trusted the value would publish a measured-looking zero
for a measurement that never happened. Every figure here is therefore rendered
through :func:`render_value` against the recorded state, exactly as
`complexity_view` does — an unavailable count reads `unavailable`, never `0`.

**Ratios are shares of physical lines and nothing else.** Each one ships with
its denominator named in its own definition, is null when that denominator is
zero, and is never called documentation, quality, maintainability or health.
`comment_line_ratio` is the share of non-blank physical lines that carry no
code — a line-shape fact. A vendored bundle with a large licence header and a
carefully documented module are not distinguished by it, and it ranks neither.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# One rendering token for an unavailable figure, shared with the complexity
# seam rather than redefined. Two spellings of "unavailable" across two
# surfaces is how a reader learns to treat one of them as a different fact.
from modules.complexity_view import UNAVAILABLE

#: Every measured file produced a line classification.
STATE_COMPLETE = "complete"
#: Some measured file could not be classified; the counts are partial observations.
STATE_PARTIAL = "partial"
#: Classification was attempted and produced nothing usable.
STATE_FAILED = "failed"
#: There was no supported source to classify.
STATE_NOT_APPLICABLE = "not_applicable"
#: The record carries no composition keys at all. Distinct from every state
#: above: nothing was attempted, so there is no value, null or otherwise.
STATE_ABSENT = "absent"

EVALUABLE_STATES = frozenset({STATE_COMPLETE, STATE_PARTIAL})

STATE_MEANINGS: dict[str, str] = {
    STATE_COMPLETE: "every measured file produced a line classification",
    STATE_PARTIAL: (
        "some measured file could not be classified; the counts are partial "
        "observations and are not verified complete values"
    ),
    STATE_FAILED: (
        "line classification was attempted and produced nothing usable; the "
        "counts are unavailable and are never read as zero"
    ),
    STATE_NOT_APPLICABLE: "there was no supported source to classify",
    STATE_ABSENT: (
        "this record carries no source-composition keys at all; nothing was "
        "attempted, so there is no value, null or otherwise"
    ),
}

#: The five recorded counts, in emission order, each with the definition it
#: carries. Definitions ship beside the numbers because "comment lines" does not
#: say whether a trailing comment on a code line counts. It does not.
COMPOSITION_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    (
        "total_physical_lines", "Total physical lines",
        "Physical lines in the file as stored, counted by splitting on line "
        "terminators. A final line without a terminator still counts once, and "
        "a file of zero bytes has zero physical lines. This is a count of "
        "lines, not of statements, tokens or bytes.",
    ),
    (
        "blank_lines", "Blank lines",
        "Physical lines that are empty or contain only whitespace. Whitespace "
        "is Python `str.strip()` whitespace, so a line of tabs or of Unicode "
        "spaces is blank.",
    ),
    (
        "nonblank_lines", "Non-blank lines",
        "Physical lines containing at least one non-whitespace character. "
        "Exactly `total_physical_lines - blank_lines`, and exactly "
        "`code_lines + comment_lines`.",
    ),
    (
        "code_lines", "Code lines",
        "Non-blank physical lines that retain at least one non-whitespace "
        "character after every comment token is masked. A line carrying code "
        "AND a trailing comment is a CODE line, counted once and never also "
        "counted as a comment line. This is the same figure republished as "
        "`lines_of_code`: there is one LOC number, not two.",
    ),
    (
        "comment_lines", "Comment-only lines",
        "Non-blank physical lines that retain nothing but whitespace once "
        "comment tokens are masked — comment-ONLY lines. A trailing comment "
        "beside code contributes nothing here. Lines inside a block comment "
        "count individually; a string literal is not a comment, so a Python "
        "module docstring counts as CODE, not as comment.",
    ),
)

COMPOSITION_FIELDS: tuple[str, ...] = tuple(
    name for name, _label, _definition in COMPOSITION_DEFINITIONS
)

#: Each ratio names its own denominator. `numerator`, `denominator`, label and
#: definition are one record so a renderer cannot pair a figure with the wrong
#: base. Deliberately ABSENT: any ratio that would need a threshold to read, and
#: any composite of two ratios.
RATIO_DEFINITIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "comment_line_ratio", "Comment-only line share",
        "comment_lines", "nonblank_lines",
        "The share of NON-BLANK physical lines that are comment-only. It is a "
        "line-shape observation and NOT a documentation, comment-quality or "
        "maintainability measure: a trailing comment beside code contributes "
        "nothing to it, a generated licence header inflates it, and no value "
        "of it is better or worse than another. Null when there are no "
        "non-blank lines.",
    ),
    (
        "code_line_ratio", "Code line share",
        "code_lines", "nonblank_lines",
        "The share of NON-BLANK physical lines that retain code after comment "
        "masking. Complementary to `comment_line_ratio` by construction — the "
        "two sum to 1 whenever the denominator is non-zero — and reported "
        "beside it so neither is mistaken for an independent finding. Null "
        "when there are no non-blank lines.",
    ),
    (
        "blank_line_ratio", "Blank line share",
        "blank_lines", "total_physical_lines",
        "The share of ALL physical lines that are blank. Note the denominator "
        "differs from the two ratios above: blank lines are excluded from "
        "`nonblank_lines` by definition, so measuring them against it would be "
        "meaningless. Null when the file set has no physical lines.",
    ),
)

RATIO_FIELDS: tuple[str, ...] = tuple(
    name for name, _l, _n, _d, _definition in RATIO_DEFINITIONS
)

#: The two identities the five counts satisfy by construction. Published so a
#: reader can check the partition rather than trust it, and checked by
#: :func:`partition_check` so a violation surfaces as a stated disagreement
#: instead of a silently odd number.
PARTITION_IDENTITIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "nonblank_partition",
        "code_lines + comment_lines == nonblank_lines",
        ("code_lines", "comment_lines", "nonblank_lines"),
    ),
    (
        "physical_partition",
        "nonblank_lines + blank_lines == total_physical_lines",
        ("nonblank_lines", "blank_lines", "total_physical_lines"),
    ),
)

DESCRIPTIVE_ONLY = (
    "Descriptive evidence, not a quality verdict. Source composition counts "
    "physical lines by shape. There is no threshold, no rating, no grade and "
    "no composite score here: a comment share is a fact about line shapes, not "
    "a statement about how well a codebase is documented or maintained."
)

CROSS_LANGUAGE_LIMITATION = (
    "Composition figures are NOT measurement-equivalent across languages. What "
    "counts as a comment token is language-specific, a Python docstring is a "
    "string expression rather than a comment while a Java Javadoc block is a "
    "comment, and generated or vendored files skew a language's totals without "
    "skewing another's. Per-language composition is reported separately for "
    "that reason and no cross-language composite is produced."
)

ZERO_STATEMENT = (
    "0 is an ordinary measured value for every count here: a file set can "
    "genuinely contain no comment-only lines. A blank or `unavailable` cell "
    "means the measurement is not available and is never read as 0. The two "
    "are never rendered alike."
)


#: The statements in this module that DENY a quality reading, and are therefore
#: the only places judgment vocabulary ("grade", "score", "threshold") may
#: legitimately appear. A plain substring scan cannot tell a prohibition from a
#: violation, so the surface tests strip these and require the remaining
#: document to contain none of that vocabulary at all.
PROHIBITION_STATEMENTS: tuple[str, ...] = (DESCRIPTIVE_ONLY,)


def composition_block(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The recorded aggregate carrying composition, or ``None`` when absent.

    ``None`` is the ABSENT case and must not be replaced by an empty mapping: an
    empty mapping would read as "measured, nothing found".

    The producer writes the counts into ``metrics.aggregate``. A caller holding
    an already-narrowed mapping — a per-language block, for instance — is
    accepted directly, because reading the wrong path would manufacture exactly
    the false ABSENT this module exists to prevent.
    """
    metrics = result.get("metrics")
    if isinstance(metrics, Mapping):
        aggregate = metrics.get("aggregate")
        if isinstance(aggregate, Mapping) and _carries_composition(aggregate):
            return aggregate
    if _carries_composition(result):
        return result
    return None


def _carries_composition(block: Mapping[str, Any]) -> bool:
    return any(field in block for field in COMPOSITION_FIELDS)


def state_of(result: Mapping[str, Any]) -> str:
    """The recorded composition state, or ABSENT."""
    block = composition_block(result)
    if block is None:
        return STATE_ABSENT
    status = block.get("loc_status")
    return str(status) if status in STATE_MEANINGS else STATE_FAILED


def render_value(value: Any, *, state: str) -> str:
    """Render one count. An unavailable count is never a zero."""
    if state not in EVALUABLE_STATES or value is None:
        return UNAVAILABLE
    return str(value)


def _count(block: Mapping[str, Any], field: str, *, state: str) -> int | None:
    """One recorded count, or ``None`` when it is not a measurement.

    The state check comes FIRST and is the reason this helper exists: a failed
    `loc_status` can leave an accumulated `0` in the record, and returning it
    would publish a measured-looking zero for a measurement that did not happen.
    """
    if state not in EVALUABLE_STATES:
        return None
    value = block.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def ratio(numerator: Any, denominator: Any) -> float | None:
    """A descriptive share, or ``None`` when it is not defined.

    Null — never 0 and never 1 — when the denominator is zero or when either
    operand is unavailable. A zero denominator means the question has no answer,
    which is a different fact from an answer of zero.
    """
    if not isinstance(numerator, int) or isinstance(numerator, bool):
        return None
    if not isinstance(denominator, int) or isinstance(denominator, bool):
        return None
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _ratio_unavailable_reason(
    numerator: Any, denominator: Any, *, state: str
) -> str | None:
    if state not in EVALUABLE_STATES:
        return "measurement_unavailable"
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        return "component_unavailable"
    if denominator == 0:
        return "zero_denominator"
    return None


def render_ratio(value: float | None) -> str:
    """Render one ratio. Four decimals, fixed, so 0 and 0.0000 never differ."""
    return UNAVAILABLE if value is None else f"{value:.4f}"


def counts(block: Mapping[str, Any], *, state: str) -> dict[str, int | None]:
    """The five counts for one block, already gated by state."""
    return {field: _count(block, field, state=state) for field in COMPOSITION_FIELDS}


def ratios(values: Mapping[str, int | None]) -> dict[str, float | None]:
    """The descriptive ratios derived from already-gated counts."""
    return {
        name: ratio(values.get(numerator), values.get(denominator))
        for name, _label, numerator, denominator, _definition in RATIO_DEFINITIONS
    }


def partition_check(values: Mapping[str, int | None]) -> list[dict[str, Any]]:
    """Check the two identities the counts satisfy by construction.

    A check whose operands are unavailable is reported ``None`` — not passed and
    not failed — because an identity over absent numbers has no truth value.
    """
    rows: list[dict[str, Any]] = []
    for name, statement, fields in PARTITION_IDENTITIES:
        left, right, total = (values.get(field) for field in fields)
        holds: bool | None
        if left is None or right is None or total is None:
            holds = None
        else:
            holds = left + right == total
        rows.append({"identity": name, "statement": statement, "holds": holds})
    return rows


def _view(block: Mapping[str, Any], *, state: str) -> dict[str, Any]:
    values = counts(block, state=state)
    shares = ratios(values)
    return {
        "counts": [
            {
                "field": field,
                "label": label,
                "definition": definition,
                "value": values[field],
                "rendered": render_value(values[field], state=state),
            }
            for field, label, definition in COMPOSITION_DEFINITIONS
        ],
        "ratios": [
            {
                "field": name,
                "label": label,
                "definition": definition,
                "numerator_field": numerator,
                "denominator_field": denominator,
                "numerator": values.get(numerator),
                "denominator": values.get(denominator),
                "value": shares[name],
                "rendered": render_ratio(shares[name]),
                "unavailable_reason": _ratio_unavailable_reason(
                    values.get(numerator), values.get(denominator), state=state
                ),
            }
            for name, label, numerator, denominator, definition in RATIO_DEFINITIONS
        ],
        "partition_checks": partition_check(values),
    }


def presentation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Everything a renderer needs for one repository's composition.

    Per-language blocks carry their OWN `loc_status` and are stated with it. A
    run where one language parsed and another failed must not present both
    under one state, because that is precisely the case where a single state
    would turn one language's absent measurement into the other's zero.
    """
    block = composition_block(result)
    state = state_of(result)
    view = _view(block or {}, state=state)

    by_language: dict[str, Any] = {}
    metrics = result.get("metrics")
    languages = metrics.get("by_language") if isinstance(metrics, Mapping) else None
    for language, values in sorted((languages or {}).items()):
        if not isinstance(values, Mapping) or not _carries_composition(values):
            continue
        language_state = state_of(values)
        by_language[str(language)] = {
            "state": language_state,
            "state_meaning": STATE_MEANINGS[language_state],
            "evaluable": language_state in EVALUABLE_STATES,
            "source_files": values.get("source_files"),
            **_view(values, state=language_state),
        }

    return {
        "state": state,
        "state_meaning": STATE_MEANINGS[state],
        "evaluable": state in EVALUABLE_STATES,
        "metric_contract_version": (
            metrics.get("metric_contract_version")
            if isinstance(metrics, Mapping) else None
        ),
        **view,
        "by_language": by_language,
    }


def provenance(
    manifest: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """What a reader needs to know one composition figure's meaning."""
    acquisition = result.get("acquisition") or {}
    state = state_of(result)
    return {
        "metric_contract_version": (
            ((result.get("metrics") or {}).get("metric_contract_version"))
            or manifest.get("metric_contract_version")
        ),
        "measurement_state": state,
        "measurement_state_meaning": STATE_MEANINGS[state],
        "analyzed_scope": {
            "analysis_scope_hash": result.get("analysis_scope_hash"),
            "analyzed_commit_sha": acquisition.get("analyzed_commit_sha"),
            "source_mode": result.get("source_mode"),
        },
        "count_definitions": {
            field: definition for field, _label, definition in COMPOSITION_DEFINITIONS
        },
        "ratio_definitions": {
            name: definition
            for name, _label, _n, _d, definition in RATIO_DEFINITIONS
        },
        "ratio_denominators": {
            name: denominator
            for name, _label, _n, denominator, _definition in RATIO_DEFINITIONS
        },
        "partition_identities": {
            name: statement for name, statement, _fields in PARTITION_IDENTITIES
        },
        "zero_statement": ZERO_STATEMENT,
        "cross_language_limitation": CROSS_LANGUAGE_LIMITATION,
        "descriptive_only": DESCRIPTIVE_ONLY,
    }


def state_distribution(results: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many results carry each state. Sorted, so rendering is stable."""
    found: dict[str, int] = {}
    for item in results:
        name = state_of(item)
        found[name] = found.get(name, 0) + 1
    return dict(sorted(found.items()))


def evaluable_results(
    results: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The results whose composition may be read as measurement."""
    return [item for item in results if state_of(item) in EVALUABLE_STATES]


__all__ = [
    "COMPOSITION_DEFINITIONS",
    "COMPOSITION_FIELDS",
    "CROSS_LANGUAGE_LIMITATION",
    "DESCRIPTIVE_ONLY",
    "EVALUABLE_STATES",
    "PARTITION_IDENTITIES",
    "PROHIBITION_STATEMENTS",
    "RATIO_DEFINITIONS",
    "RATIO_FIELDS",
    "STATE_ABSENT",
    "STATE_COMPLETE",
    "STATE_FAILED",
    "STATE_MEANINGS",
    "STATE_NOT_APPLICABLE",
    "STATE_PARTIAL",
    "UNAVAILABLE",
    "ZERO_STATEMENT",
    "composition_block",
    "counts",
    "evaluable_results",
    "partition_check",
    "presentation",
    "provenance",
    "ratio",
    "ratios",
    "render_ratio",
    "render_value",
    "state_distribution",
    "state_of",
]
