"""The Policy v2 metric allowlist: every field a rule may gate on.

**No metric is invented here.** Every entry below names a field Metrolith already
persists, and the persisted status that says whether that field is a
measurement. Policy v2 reads; it never measures, never recomputes and never
redefines. Nothing in this module may compute a ratio, a percentile, a composite
or a normalized score — the allowlist is the whole surface, and a rule naming
anything outside it is refused at load time rather than silently skipped.

The one apparent exception is the cognitive family, and it is not one. Artifact
Schema 1.10 persists `cognitive_complexity` per callable and
`cognitive_measurement_state` per repository; it does not persist a cognitive
aggregate. :mod:`modules.complexity_view` already owns the single definition of
each cognitive aggregate, used by `summary.md`, the HTML report, `explain` and
`diff`. This module calls that function. It defines no aggregate of its own,
because a second definition is how two answers appear.

**Missing is never zero.** :func:`numeric_value` is the only place a persisted
cell becomes a number, and it returns ``None`` for null, for the empty cell, for
a non-numeric cell, for a bool and for a non-finite float. Every caller must
branch on ``None``; none may substitute a zero. A repository with no Java and a
repository whose Java parse failed both record 0 in several columns, and telling
them apart is the entire reason the status fields exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from modules import complexity_view

# --------------------------------------------------------------------------
# Scopes
# --------------------------------------------------------------------------

#: One evaluation unit per analyzed subject.
SCOPE_REPOSITORY = "repository"
#: One evaluation unit per (subject, language) the artifact records.
SCOPE_LANGUAGE = "language"
#: One evaluation unit per row of the per-callable complexity ledger.
SCOPE_CALLABLE = "callable"
#: One evaluation unit per row of an ADMITTED standalone Hotspot document.
#:
#: A genuinely new unit rather than a re-use of an existing one: a hotspot row
#: is one file of one subject, which is neither a repository, a language, nor a
#: callable. The unit exists only when a Hotspot document has been admitted as
#: evidence; a rule at this scope with no admitted evidence reports
#: `not_evaluable`, never a pass over zero rows.
SCOPE_HOTSPOT_FILE = "hotspot_file"
#: One evaluation unit per clone group of an ADMITTED standalone Duplication
#: document.
#:
#: A genuinely new unit, like `hotspot_file`: a clone group is a set of
#: occurrences that share one canonicalized body, which is neither a repository,
#: a language, a callable nor a file. The unit exists only when a Duplication
#: document has been admitted AND the kind that produced the group was actually
#: measured; a rule here with no admitted evidence reports `not_evaluable`,
#: never a pass over zero groups.
#:
#: **The kind is part of every metric name at this scope**, never merged. A
#: lexical group and a structural group are different definitions -- structural
#: groups pass through a maximality-suppression stage lexical groups do not, and
#: one physical clone can be counted by both -- so a rule evaluates exactly one
#: of the two populations and an author who wants both writes two rules.
SCOPE_DUPLICATION_GROUP = "duplication_group"

#: Appended, never inserted: `SCOPE_RANK` is derived from this order and is part
#: of the finding sort key, so putting a new scope anywhere but the end would
#: silently reorder every existing result.
SCOPES: tuple[str, ...] = (
    SCOPE_REPOSITORY, SCOPE_LANGUAGE, SCOPE_CALLABLE, SCOPE_HOTSPOT_FILE,
    SCOPE_DUPLICATION_GROUP,
)

#: Ordering rank, so a result sorts scopes the same way every time.
SCOPE_RANK: dict[str, int] = {name: index for index, name in enumerate(SCOPES)}

#: The scopes whose evaluation unit has a per-file location, and therefore the
#: only scopes where `paths` / `exclude_paths` can select anything. An aggregate
#: scope covers its whole population and cannot be narrowed to a path.
#:
#: **`duplication_group` is deliberately NOT here, though a group finding does
#: carry a path.** A clone group spans N files by definition, so a path filter
#: would need a quantifier and neither choice is safe: matching on the primary
#: occurrence alone makes `exclude_paths: ["vendor/**"]` fail to exclude a group
#: whose canonically-first occurrence happens to sit elsewhere, and using `any`
#: for one key and `all` for the other puts two quantifiers behind one syntax.
#: The decisive reason is elsewhere: the analyzed file population is already
#: decided by the run's exclusion policy, which the Duplication analysis used to
#: build its file set. Re-filtering here would create a second place that
#: decides what is in scope, and the two would disagree INSIDE one result --
#: repository-scope counts including vendored groups while group-scope rules
#: excluded them.
PATH_BEARING_SCOPES: tuple[str, ...] = (SCOPE_CALLABLE, SCOPE_HOTSPOT_FILE)

# --------------------------------------------------------------------------
# Data completeness, derived from the persisted status. Never written back.
# --------------------------------------------------------------------------

#: Every file that had to be measured was measured.
COMPLETENESS_COMPLETE = "complete"
#: Some measured file could not be measured. The figure is an observation, not a
#: verified complete value.
COMPLETENESS_PARTIAL = "partial"
#: Measurement was attempted and produced nothing usable, or the value is null.
#: A gap. Never a zero.
COMPLETENESS_UNAVAILABLE = "unavailable"
#: There was nothing of this kind to measure. An honest, complete answer, and
#: deliberately NOT the same fact as `unavailable`: a Java rule on a Python
#: repository must not fail the build.
COMPLETENESS_NOT_APPLICABLE = "not_applicable"
#: The artifact predates the concept entirely — no block, no column, nothing
#: attempted. Distinct again from `unavailable`, where something was attempted.
COMPLETENESS_ABSENT = "absent"

COMPLETENESS_STATES: tuple[str, ...] = (
    COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL, COMPLETENESS_UNAVAILABLE,
    COMPLETENESS_NOT_APPLICABLE, COMPLETENESS_ABSENT,
)

COMPLETENESS_MEANINGS: dict[str, str] = {
    COMPLETENESS_COMPLETE: "every file that had to be measured was measured",
    COMPLETENESS_PARTIAL: (
        "some measured file could not be measured; the figure is a partial "
        "observation and is not a verified complete value"
    ),
    COMPLETENESS_UNAVAILABLE: (
        "the measurement is unavailable and is never read as zero"
    ),
    COMPLETENESS_NOT_APPLICABLE: (
        "there was nothing of this kind to measure; this is a complete answer, "
        "not a gap"
    ),
    COMPLETENESS_ABSENT: (
        "this artifact generation recorded nothing of this kind at all; nothing "
        "was attempted, so there is no value, null or otherwise"
    ),
}

#: The status vocabulary the producer writes for core and structural metrics.
_STATUS_TO_COMPLETENESS: dict[Any, str] = {
    "complete": COMPLETENESS_COMPLETE,
    "partial": COMPLETENESS_PARTIAL,
    "failed": COMPLETENESS_UNAVAILABLE,
    "not_applicable": COMPLETENESS_NOT_APPLICABLE,
}

#: The Hotspot document's own status vocabulary, mapped without reinterpretation.
#: Three states, each keeping its meaning exactly: `measured` is a complete
#: answer, `unavailable` is a gap and is never read as zero, `not_applicable` is
#: a complete answer that there was nothing of this kind to measure.
#:
#: The Hotspot model has no `partial`, and none is invented here. Where
#: partiality exists it is already expressed structurally -- some files measured
#: and some not, each row carrying its own per-block status -- and the collapse
#: of non-evaluable rows reports the count.
_HOTSPOT_STATUS_TO_COMPLETENESS: dict[Any, str] = {
    "measured": COMPLETENESS_COMPLETE,
    "unavailable": COMPLETENESS_UNAVAILABLE,
    "not_applicable": COMPLETENESS_NOT_APPLICABLE,
}


def hotspot_completeness_of_status(status: Any) -> str:
    """Map one Hotspot status onto a completeness class.

    An unrecognized status maps to ``unavailable``, for the same reason
    :func:`completeness_of_status` does: a value Metrolith cannot name is a value
    it cannot vouch for.
    """
    return _HOTSPOT_STATUS_TO_COMPLETENESS.get(status, COMPLETENESS_UNAVAILABLE)


#: The Duplication document's own status vocabulary -- five states, two more
#: than the Hotspot model -- mapped without reinterpretation.
#:
#: `not_requested` is the one that carries a decision. It means the operator ran
#: `metrolith duplication --kind lexical` and the other kind was never attempted.
#: Mapping it to `not_applicable` would make every structural rule SILENTLY PASS
#: on a lexical-only document, which is the manufactured-zero failure in its
#: purest form. It is therefore `unavailable` -- the rule did not run -- and the
#: evaluator attaches its own typed reason, `evidence_kind_not_requested`, so
#: the operator is sent to their own `--kind` flag rather than to a parser bug.
_DUPLICATION_STATUS_TO_COMPLETENESS: dict[Any, str] = {
    "complete": COMPLETENESS_COMPLETE,
    "partial": COMPLETENESS_PARTIAL,
    "failed": COMPLETENESS_UNAVAILABLE,
    "not_applicable": COMPLETENESS_NOT_APPLICABLE,
    "not_requested": COMPLETENESS_UNAVAILABLE,
}

#: The Duplication status that means "this kind was never attempted".
DUPLICATION_NOT_REQUESTED = "not_requested"


def duplication_completeness_of_status(status: Any) -> str:
    """Map one Duplication status onto a completeness class.

    An unrecognized status maps to ``unavailable``, never to ``complete``, for
    the reason :func:`completeness_of_status` states.
    """
    return _DUPLICATION_STATUS_TO_COMPLETENESS.get(status, COMPLETENESS_UNAVAILABLE)


#: The cognitive state vocabulary (Complexity Contract 2.0.0 section 12.8).
_COGNITIVE_STATE_TO_COMPLETENESS: dict[Any, str] = {
    complexity_view.COGNITIVE_MEASURED: COMPLETENESS_COMPLETE,
    complexity_view.COGNITIVE_PARTIAL: COMPLETENESS_PARTIAL,
    complexity_view.COGNITIVE_FAILED: COMPLETENESS_UNAVAILABLE,
    complexity_view.COGNITIVE_NOT_APPLICABLE: COMPLETENESS_NOT_APPLICABLE,
    complexity_view.COGNITIVE_ABSENT: COMPLETENESS_ABSENT,
}


def completeness_of_status(status: Any) -> str:
    """Map a persisted status onto a completeness class.

    An unrecognized status maps to ``unavailable``, never to ``complete``. A
    value Metrolith cannot name is a value it cannot vouch for, and treating the
    unknown as measured is how a gate silently stops gating.
    """
    return _STATUS_TO_COMPLETENESS.get(status, COMPLETENESS_UNAVAILABLE)


def numeric_value(raw: Any) -> int | float | None:
    """The single coercion path from a persisted cell to a number.

    Returns ``None`` — meaning *unavailable*, never zero — for ``None``, the
    empty or whitespace cell, a non-numeric string, a bool, and a non-finite
    float. ``bool`` is refused explicitly because it is an ``int`` subclass in
    Python and ``True`` would otherwise arrive as the number 1.

    CSV cells are strings, JSON cells are numbers, and both reach here, so the
    string branch is not a legacy path.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return None if math.isnan(raw) or math.isinf(raw) else raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            parsed = float(text)
        except ValueError:
            return None
        return None if math.isnan(parsed) or math.isinf(parsed) else parsed
    return None


@dataclass(frozen=True)
class Observation:
    """One metric read for one evaluation unit, with why it is or is not usable.

    ``value`` is ``None`` whenever ``completeness`` is not an evaluable class.
    The two travel together on purpose: a caller cannot reach the number without
    also holding the statement of what the number is worth.
    """

    value: int | float | None
    completeness: str
    #: The persisted field the status came from, for provenance.
    status_field: str
    #: The persisted status value, verbatim.
    status_value: Any
    #: The persisted field the value came from, for provenance.
    value_field: str
    #: A consumed-field contract defect. Distinct from an explicitly
    #: unavailable measurement: malformed input makes evaluation itself fail.
    error: str | None = None

    @property
    def evaluable(self) -> bool:
        return (
            self.completeness in (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL)
            and self.value is not None
        )


def _observe(
    values: Mapping[str, Any] | None,
    field: str,
    *,
    completeness: str,
    status_field: str,
    status_value: Any,
    value_field: str,
    value_type: str,
    allow_numeric_text: bool = False,
) -> Observation:
    """Assemble one observation, downgrading to unavailable when the cell is null.

    A status of `complete` beside a null cell is a producer inconsistency, and
    the honest read is *unavailable*: a status cannot conjure a number that was
    never written.
    """
    if values is None:
        raw = None
    else:
        raw = values.get(field)
    if completeness in (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL):
        if raw is None or (allow_numeric_text and isinstance(raw, str) and not raw.strip()):
            return Observation(
                None, COMPLETENESS_UNAVAILABLE, status_field, status_value,
                value_field,
            )
        if isinstance(raw, str) and not allow_numeric_text:
            return Observation(
                None, COMPLETENESS_UNAVAILABLE, status_field, status_value,
                value_field, f"{value_field} must be a JSON number, not text",
            )
        number = numeric_value(raw)
        if number is None:
            return Observation(
                None, COMPLETENESS_UNAVAILABLE, status_field, status_value,
                value_field,
                f"{value_field} must contain a finite numeric value when "
                f"{status_field} is {status_value!r}",
            )
        if value_type == "integer" and (
            isinstance(number, float) and not number.is_integer()
        ):
            return Observation(
                None, COMPLETENESS_UNAVAILABLE, status_field, status_value,
                value_field,
                f"{value_field} is integer-valued and cannot contain "
                f"non-integral value {raw!r}",
            )
        if value_type == "integer" and isinstance(number, float):
            number = int(number)
    else:
        number = None
    return Observation(
        value=number, completeness=completeness, status_field=status_field,
        status_value=status_value, value_field=value_field,
    )


def _invalid_status_observation(
    *, status_field: str, status_value: Any, value_field: str
) -> Observation:
    return Observation(
        None,
        COMPLETENESS_UNAVAILABLE,
        status_field,
        status_value,
        value_field,
        f"{status_field} has missing or unsupported status {status_value!r}",
    )


def malformed_observation(
    definition: MetricDefinition,
    *,
    status_field: str,
    status_value: Any,
    message: str,
) -> Observation:
    """Represent a selected persisted container/type defect explicitly."""

    return Observation(
        None,
        COMPLETENESS_UNAVAILABLE,
        status_field,
        status_value,
        definition.source,
        message,
    )


# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------

#: Metric families, used only to group the listing and to route the read.
FAMILY_CORE = "core"
FAMILY_STRUCTURAL_COMPLEXITY = "structural_complexity"
FAMILY_COGNITIVE_COMPLEXITY = "cognitive_complexity"
#: Values read from an ADMITTED standalone Hotspot document. The only family
#: whose source is not the run bundle, and the only one that can be
#: `not_evaluable` because no evidence was supplied.
FAMILY_HOTSPOTS = "hotspots"
#: Values read from an ADMITTED standalone Duplication document. Like the
#: hotspot family its source is not the run bundle, and unlike every other
#: family it binds to exactly ONE subject: a Duplication document describes one
#: local snapshot and carries no subject identity, so on a multi-subject run
#: every subject the document does not bind to is honestly `not_evaluable`.
FAMILY_DUPLICATION = "duplication"

#: Families that need an admitted evidence document, and which format supplies
#: each. A rule in one of these families has nothing to read until that evidence
#: is admitted -- a different state from "the run did not measure it".
FAMILY_EVIDENCE_FORMAT: dict[str, str] = {
    FAMILY_HOTSPOTS: "archlens-hotspots",
    FAMILY_DUPLICATION: "archlens-duplication",
}

#: The oldest policy document version that may name a metric of each family.
#: The published schema encodes the same rule in its `metric` enum; the loader
#: enforces it too, because `metrolith check` reads a policy file without
#: schema-validating it, and a loader that accepted what the schema refuses
#: would let a 2.0.0 document gate on a vocabulary it does not declare.
FAMILY_MINIMUM_DOCUMENT_VERSION: dict[str, str] = {
    FAMILY_HOTSPOTS: "2.1.0",
    FAMILY_DUPLICATION: "2.2.0",
}


@dataclass(frozen=True)
class MetricDefinition:
    """One allowlisted metric: what it is, where it comes from, what gates it."""

    identifier: str
    scope: str
    family: str
    #: Key inside the family's value mapping, or the ledger column.
    field: str
    #: Human pointer to the persisted location, reported as provenance.
    source: str
    #: `integer` or `number`. Recorded so a listing can say which aggregates are
    #: means and therefore fractional; it constrains nothing at evaluation time.
    value_type: str
    definition: str

    @property
    def requires_evidence(self) -> str | None:
        """The standalone format that must be admitted, or ``None``.

        Stated on the metric rather than left for a policy author to discover
        from a `not_evaluable` finding: a rule that can never run without a file
        nobody mentioned is a rule that silently protects nothing.
        """
        return FAMILY_EVIDENCE_FORMAT.get(self.family)

    @property
    def minimum_document_version(self) -> str | None:
        """The oldest policy document version that may name this metric."""
        return FAMILY_MINIMUM_DOCUMENT_VERSION.get(self.family)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.identifier,
            "scope": self.scope,
            "family": self.family,
            "source": self.source,
            "value_type": self.value_type,
            "definition": self.definition,
            "requires_evidence": self.requires_evidence,
            "minimum_document_version": self.minimum_document_version,
        }


#: (field, status field, definition) for the four canonical core metrics. Metric
#: Contract 3.0.0 defines each one; nothing here restates or reinterprets it.
_CORE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("lines_of_code", "loc_status",
     "Metric Contract 3.0.0 lines of code over files included in metrics."),
    ("source_files", "source_files_status",
     "Metric Contract 3.0.0 count of source files included in metrics."),
    ("classes_structs", "classes_structs_status",
     "Metric Contract 3.0.0 class/struct entity count."),
    ("methods_functions", "methods_functions_status",
     "Metric Contract 3.0.0 method/function entity count."),
)


def _core_definitions(scope: str, source: str) -> list[MetricDefinition]:
    prefix = "repository" if scope == SCOPE_REPOSITORY else "language"
    return [
        MetricDefinition(
            identifier=f"{prefix}.{field}", scope=scope, family=FAMILY_CORE,
            field=field, source=f"{source}.{field}", value_type="integer",
            definition=definition,
        )
        for field, _status, definition in _CORE_METRICS
    ]


def _structural_definitions(scope: str, source: str) -> list[MetricDefinition]:
    prefix = "repository" if scope == SCOPE_REPOSITORY else "language"
    return [
        MetricDefinition(
            identifier=f"{prefix}.{field}", scope=scope,
            family=FAMILY_STRUCTURAL_COMPLEXITY, field=field,
            source=f"{source}.{field}",
            value_type="number" if field.endswith("_mean") else "integer",
            definition=definition,
        )
        for field, _label, definition in complexity_view.AGGREGATE_DEFINITIONS
    ]


def _cognitive_definitions(scope: str, source: str) -> list[MetricDefinition]:
    prefix = "repository" if scope == SCOPE_REPOSITORY else "language"
    return [
        MetricDefinition(
            identifier=f"{prefix}.{field}", scope=scope,
            family=FAMILY_COGNITIVE_COMPLEXITY, field=field, source=source,
            value_type="number" if field.endswith("_mean") else "integer",
            definition=definition,
        )
        for field, _label, definition in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS
    ]


#: (column, status column, value type, definition) for the callable ledger.
#:
#: `cognitive_complexity` is gated on the NULLABILITY OF THE VALUE rather than
#: on a status column, because Artifact Schema 1.10 puts the whole distinction
#: there: the column's minimum is 0 and 0 is an ordinary measured value, so a
#: blank cell is the only way to say unavailable.
_CALLABLE_METRICS: tuple[tuple[str, str | None, str], ...] = (
    ("cyclomatic_complexity", "structural_complexity_status",
     "Metrolith Syntactic Cyclomatic Complexity for one callable: 1 + decision "
     "points + short-circuit operators. Minimum 1."),
    ("max_nesting_depth", "structural_complexity_status",
     "Deepest structural nesting inside one callable."),
    ("formal_parameter_count", "structural_complexity_status",
     "Parameters declared in one callable's own parameter list, as written in "
     "source. A syntactic declaration count, NOT measurement-equivalent across "
     "languages."),
    ("nloc", "nloc_status",
     "Physical lines in one callable's declaration span that are neither blank "
     "nor comment-only. The span includes lexically nested callables."),
    ("cognitive_complexity", None,
     "Metrolith Cognitive Complexity for one callable, Complexity Contract "
     "2.0.0 section 12. Minimum 0, and 0 is an ordinary measured value; a blank "
     "cell means unavailable."),
)


def _callable_definitions() -> list[MetricDefinition]:
    return [
        MetricDefinition(
            identifier=f"callable.{column}", scope=SCOPE_CALLABLE,
            family=(
                FAMILY_COGNITIVE_COMPLEXITY
                if column == "cognitive_complexity"
                else FAMILY_STRUCTURAL_COMPLEXITY
            ),
            field=column, source=f"callables ledger column {column}",
            value_type="integer", definition=definition,
        )
        for column, _status, definition in _CALLABLE_METRICS
    ]


# --------------------------------------------------------------------------
# Hotspots. Descriptive counts and per-file source values, nothing else.
# --------------------------------------------------------------------------

#: Per-subject counts. The first two are persisted verbatim by the Hotspot
#: document; the last four are the attention tally, whose single definition
#: lives in `modules.hotspots.attention_class_counts` and is CALLED, never
#: reimplemented here.
#:
#: **No ranking metric and no ordinal appears in this list, deliberately.** The
#: `low`/`medium`/`high` signals and the attention classes are ordinal, and the
#: operator vocabulary is six numeric comparisons over IEEE-754 doubles.
#: Exposing an ordinal would require mapping it to a number, on which `gt 2`
#: becomes meaningful -- a threshold Metrolith would have invented. The cohort
#: ranks are also relative to the analyzed population, so the same file with the
#: same complexity can rank differently in two repositories and again after an
#: unrelated file is added. Counting how many files carry a published
#: categorical label is a count of an existing fact; reading `high` as a number
#: is not.
_HOTSPOT_REPOSITORY_METRICS: tuple[tuple[str, str, str], ...] = (
    (
        "hotspot_file_count",
        "archlens-hotspots repositories[].file_count",
        "Files this Hotspot analysis considered for one subject. A count the "
        "Hotspot document persists; it is not a quality figure and implies "
        "nothing about any of those files.",
    ),
    (
        "hotspot_classified_file_count",
        "archlens-hotspots repositories[].classified_file_count",
        "Files for which BOTH signals were measured, so an attention class "
        "could be assigned. Persisted by the Hotspot document.",
    ),
    (
        "hotspot_high_attention_file_count",
        "derived by modules.hotspots.attention_class_counts over the persisted "
        "hotspots[].classification",
        "Files whose published classification is `high_attention`: both "
        "signals measured and both high. A maintenance-attention label, NOT a "
        "defect count, a bug prediction or a quality score.",
    ),
    (
        "hotspot_moderate_attention_file_count",
        "derived by modules.hotspots.attention_class_counts over the persisted "
        "hotspots[].classification",
        "Files whose published classification is `moderate_attention`: either "
        "signal high, or both medium.",
    ),
    (
        "hotspot_low_attention_file_count",
        "derived by modules.hotspots.attention_class_counts over the persisted "
        "hotspots[].classification",
        "Files whose published classification is `low_attention`: all other "
        "pairs of measured signals.",
    ),
    (
        "hotspot_unclassified_file_count",
        "derived by modules.hotspots.attention_class_counts over the persisted "
        "hotspots[].classification",
        "Files carrying no class because at least one signal was not measured. "
        "Reported separately and never folded into `low_attention`: an "
        "unmeasured file is not a calm one.",
    ),
)

#: Per-file source values, each with the block it lives in and the status that
#: gates it. `touched_lines` has its OWN status because a binary change makes
#: the line count unavailable while the commit count stays measured -- reading
#: one status for both would report a measured zero for lines nobody counted.
_HOTSPOT_FILE_METRICS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "cognitive_complexity_total", "complexity", "cognitive_complexity_total",
        "status",
        "Sum of Metrolith Cognitive Complexity over the callables in one file, "
        "as the Hotspot document records it from the run's callables ledger.",
    ),
    (
        "cyclomatic_complexity_total", "complexity",
        "cyclomatic_complexity_total", "status",
        "Sum of Metrolith Syntactic Cyclomatic Complexity over the callables in "
        "one file, as the Hotspot document records it.",
    ),
    (
        "max_nesting_depth_max", "complexity", "max_nesting_depth_max", "status",
        "Deepest structural nesting of any callable in one file, as the "
        "Hotspot document records it.",
    ),
    (
        "churn_commits", "churn", "commits", "status",
        "Commits touching this file's exact-content rename lineage, reachable "
        "from the analyzed revision. A frequency of change, not a defect "
        "signal: a file may be edited often because it is healthy and useful.",
    ),
    (
        "churn_touched_lines", "churn", "touched_lines", "touched_lines_status",
        "Inserted plus deleted text lines across those commits. Gated by its "
        "own status: a binary change makes it unavailable while the commit "
        "count remains measured.",
    ),
)

#: `hotspot_file.<id>` -> (document block, field in that block, status key).
HOTSPOT_FILE_SOURCES: dict[str, tuple[str, str, str]] = {
    f"{SCOPE_HOTSPOT_FILE}.{identifier}": (block, field_name, status_key)
    for identifier, block, field_name, status_key, _definition
    in _HOTSPOT_FILE_METRICS
}

#: Field names in the per-subject counts mapping the evaluator assembles.
HOTSPOT_REPOSITORY_FIELDS: tuple[str, ...] = tuple(
    field_name for field_name, _source, _definition in _HOTSPOT_REPOSITORY_METRICS
)


def _hotspot_definitions() -> list[MetricDefinition]:
    repository = [
        MetricDefinition(
            identifier=f"repository.{field_name}", scope=SCOPE_REPOSITORY,
            family=FAMILY_HOTSPOTS, field=field_name, source=source,
            value_type="integer", definition=definition,
        )
        for field_name, source, definition in _HOTSPOT_REPOSITORY_METRICS
    ]
    per_file = [
        MetricDefinition(
            identifier=f"{SCOPE_HOTSPOT_FILE}.{identifier}",
            scope=SCOPE_HOTSPOT_FILE, family=FAMILY_HOTSPOTS,
            field=field_name,
            source=f"archlens-hotspots hotspots[].{block}.{field_name}",
            value_type="integer", definition=definition,
        )
        for identifier, block, field_name, _status_key, definition
        in _HOTSPOT_FILE_METRICS
    ]
    return repository + per_file


# --------------------------------------------------------------------------
# Duplication. Counts the Duplication document already persists, nothing else.
# --------------------------------------------------------------------------

#: Per-subject counts, each with the KIND whose persisted status gates it, or
#: ``None`` when the value is gated by evidence coverage alone.
#:
#: **The two gates are different, and conflating them would lie.** The four
#: `None` entries count the analyzed FILE POPULATION: the document writes
#: `eligible_file_count` as the size of its file set and
#: `candidate_unavailable_file_count` as the complement of the complete ones, so
#: both are taken whenever the analysis ran at all -- regardless of whether
#: either detection kind then succeeded. Gating them on a kind status would
#: report `unavailable` for a tally that was in fact taken, and
#: `candidate_unavailable_file_count` is precisely the honest "how much did we
#: fail to look at" number, so making it vanish when something failed would
#: invert its purpose.
#:
#: The six kind-scoped counts have a real persisted status and are gated on it.
#: The producer writes `null` for every one of them when the kind was not
#: measured, so the null path and the status path agree by construction rather
#: than by a second opinion.
#:
#: **No cross-kind total appears here.** The document never sums the two kinds
#: and summing them would be a new number: they are different definitions, one
#: passes through a suppression stage the other does not, and one clone can be
#: counted by both.
_DUPLICATION_REPOSITORY_METRICS: tuple[
    tuple[str, str | None, str, str, str], ...
] = (
    (
        "duplication_eligible_file_count", None, "eligible_file_count",
        "archlens-duplication counts.eligible_file_count",
        "Files this Duplication analysis was eligible to read for one subject. "
        "A count the Duplication document persists; it says nothing about any "
        "of those files.",
    ),
    (
        "duplication_candidate_complete_file_count", None, "candidate_complete_file_count",
        "archlens-duplication counts.candidate_complete_file_count",
        "Eligible files whose candidate extraction completed. Persisted by the "
        "Duplication document.",
    ),
    (
        "duplication_candidate_unavailable_file_count", None, "candidate_unavailable_file_count",
        "archlens-duplication counts.candidate_unavailable_file_count",
        "Eligible files whose candidate extraction did NOT complete -- the "
        "honest count of what the analysis could not look at. Reported "
        "separately and never folded into a duplicate count.",
    ),
    (
        "duplication_observed_candidate_count", None, "observed_candidate_count",
        "archlens-duplication counts.observed_candidate_count",
        "Total duplication candidates observed across the completed files. "
        "Null when no file completed candidate extraction, and a null is "
        "`unavailable`, never a count of zero.",
    ),
    (
        "duplication_lexical_group_count", "lexical", "group_count",
        "archlens-duplication counts.lexical.group_count",
        "Lexical-exact clone groups for one subject, as Duplication Lexical "
        "Contract v1 defines a group. A count of a measurement, not a defect "
        "count and not a quality figure.",
    ),
    (
        "duplication_lexical_occurrence_count", "lexical", "occurrence_count",
        "archlens-duplication counts.lexical.occurrence_count",
        "Occurrences across those lexical groups. One clone body appearing "
        "three times contributes three occurrences and one group.",
    ),
    (
        "duplication_structural_initial_group_count", "structural", "initial_group_count",
        "archlens-duplication counts.structural.initial_group_count",
        "Structural clone groups before maximality suppression, as Duplication "
        "Structural Grouping Contract v1 defines them.",
    ),
    (
        "duplication_structural_suppressed_group_count", "structural", "suppressed_group_count",
        "archlens-duplication counts.structural.suppressed_group_count",
        "Structural groups removed by maximality suppression because a larger "
        "group already covers them.",
    ),
    (
        "duplication_structural_retained_group_count", "structural", "retained_group_count",
        "archlens-duplication counts.structural.retained_group_count",
        "Structural groups surviving maximality suppression -- the reportable "
        "structural group count.",
    ),
    (
        "duplication_structural_occurrence_count", "structural", "occurrence_count",
        "archlens-duplication counts.structural.occurrence_count",
        "Occurrences across the retained structural groups.",
    ),
)

#: Per-group values. The KIND is part of the identifier, so each rule evaluates
#: exactly one of the two populations.
#:
#: `structural_source_span_line_count` exists only for structural groups, and
#: that is why there is no lexical counterpart rather than a lexical
#: `not_applicable`: the Duplication contract persists a merged span length only
#: where it computed one. A group's occurrences can overlap within one file, so
#: a naive sum double-counts -- `_span_union` merges the intervals and
#: `source_span_line_count` is the merged result. Producing a lexical
#: equivalent, or any `duplicated_nloc` total, would be Metrolith defining a
#: number the Duplication contract does not define.
_DUPLICATION_GROUP_METRICS: tuple[tuple[str, str, str, str], ...] = (
    (
        "lexical_occurrence_count", "lexical", "occurrence_count",
        "Occurrences in one lexical-exact clone group. A group with two "
        "occurrences is one body appearing twice.",
    ),
    (
        "lexical_file_count", "lexical", "file_count",
        "Distinct files one lexical-exact clone group spans. A group entirely "
        "inside one file has a file count of 1.",
    ),
    (
        "structural_occurrence_count", "structural", "occurrence_count",
        "Occurrences in one retained structural clone group.",
    ),
    (
        "structural_file_count", "structural", "file_count",
        "Distinct files one retained structural clone group spans.",
    ),
    (
        "structural_source_span_line_count", "structural",
        "source_span_line_count",
        "Source lines covered by one retained structural clone group, with "
        "overlapping same-file spans MERGED rather than summed. Persisted for "
        "structural groups only.",
    ),
)

#: `repository.<id>` -> the kind whose status gates it, or ``None`` for the
#: file-population counts gated by evidence coverage alone.
DUPLICATION_REPOSITORY_KIND: dict[str, str | None] = {
    f"{SCOPE_REPOSITORY}.{field_name}": kind
    for field_name, kind, _doc_field, _source, _definition
    in _DUPLICATION_REPOSITORY_METRICS
}

#: Field names in the per-subject counts mapping the evaluator assembles.
DUPLICATION_REPOSITORY_FIELDS: tuple[str, ...] = tuple(
    field_name for field_name, _kind, _doc_field, _source, _definition
    in _DUPLICATION_REPOSITORY_METRICS
)

#: The evaluator's flat field name -> where to read it in the document:
#: ``(kind, key)``, where ``kind`` is ``None`` for a key directly under
#: ``counts`` and a kind name for one under ``counts.<kind>``.
#:
#: Stated as data here rather than as literals in the evaluator so the flat
#: mapping and the allowlist cannot drift: one table produces both, and a metric
#: added without a source would fail to build rather than silently read nothing.
DUPLICATION_REPOSITORY_SOURCES: dict[str, tuple[str | None, str]] = {
    field_name: (kind, doc_field)
    for field_name, kind, doc_field, _source, _definition
    in _DUPLICATION_REPOSITORY_METRICS
}

#: `duplication_group.<id>` -> (kind, field in the group object).
DUPLICATION_GROUP_SOURCES: dict[str, tuple[str, str]] = {
    f"{SCOPE_DUPLICATION_GROUP}.{identifier}": (kind, field_name)
    for identifier, kind, field_name, _definition in _DUPLICATION_GROUP_METRICS
}

#: The document key holding each kind's groups.
DUPLICATION_GROUP_LIST: dict[str, str] = {
    "lexical": "lexical_groups",
    "structural": "structural_groups",
}


def _duplication_definitions() -> list[MetricDefinition]:
    repository = [
        MetricDefinition(
            identifier=f"{SCOPE_REPOSITORY}.{field_name}",
            scope=SCOPE_REPOSITORY, family=FAMILY_DUPLICATION,
            field=field_name, source=source, value_type="integer",
            definition=definition,
        )
        for field_name, _kind, _doc_field, source, definition
        in _DUPLICATION_REPOSITORY_METRICS
    ]
    per_group = [
        MetricDefinition(
            identifier=f"{SCOPE_DUPLICATION_GROUP}.{identifier}",
            scope=SCOPE_DUPLICATION_GROUP, family=FAMILY_DUPLICATION,
            field=field_name,
            source=(
                f"archlens-duplication {DUPLICATION_GROUP_LIST[kind]}[]"
                f".{field_name}"
            ),
            value_type="integer", definition=definition,
        )
        for identifier, kind, field_name, definition
        in _DUPLICATION_GROUP_METRICS
    ]
    return repository + per_group


METRICS: tuple[MetricDefinition, ...] = tuple(
    _core_definitions(SCOPE_REPOSITORY, "analysis.json metrics.aggregate")
    + _structural_definitions(
        SCOPE_REPOSITORY, "analysis.json metrics.complexity.aggregate"
    )
    + _cognitive_definitions(
        SCOPE_REPOSITORY,
        "derived by modules.complexity_view.cognitive_aggregate over the "
        "persisted callables ledger",
    )
    + _core_definitions(SCOPE_LANGUAGE, "analysis.json metrics.by_language")
    + _structural_definitions(
        SCOPE_LANGUAGE, "analysis.json metrics.complexity.by_language"
    )
    + _cognitive_definitions(
        SCOPE_LANGUAGE,
        "derived by modules.complexity_view.cognitive_aggregate over the "
        "persisted callables ledger, grouped by detected_language",
    )
    + _callable_definitions()
    + _hotspot_definitions()
    + _duplication_definitions()
)

#: Keyed on ``(metric identifier, scope)``. The same identifier never appears at
#: two scopes — `repository.` / `language.` / `callable.` prefixes keep them
#: apart — but the pair is the honest key and a test asserts uniqueness.
METRICS_BY_ID: dict[str, MetricDefinition] = {
    definition.identifier: definition for definition in METRICS
}

METRIC_IDS: tuple[str, ...] = tuple(sorted(METRICS_BY_ID))

#: Status column consulted for each callable-scope metric, or ``None`` when the
#: value's own nullability carries the distinction.
CALLABLE_STATUS_COLUMN: dict[str, str | None] = {
    f"callable.{column}": status for column, status, _definition in _CALLABLE_METRICS
}

#: Status field consulted for each core metric, keyed on the bare field name.
CORE_STATUS_FIELD: dict[str, str] = {
    field: status for field, status, _definition in _CORE_METRICS
}


def metric_for(identifier: str) -> MetricDefinition | None:
    return METRICS_BY_ID.get(identifier)


def metrics_for_scope(scope: str) -> tuple[MetricDefinition, ...]:
    return tuple(item for item in METRICS if item.scope == scope)


# --------------------------------------------------------------------------
# Reads. Each returns an Observation; none returns a bare number.
# --------------------------------------------------------------------------

def read_core(
    values: Mapping[str, Any] | None,
    definition: MetricDefinition,
    *,
    missing_completeness: str = COMPLETENESS_UNAVAILABLE,
) -> Observation:
    """One core metric from an aggregate or per-language values mapping."""
    status_field = CORE_STATUS_FIELD[definition.field]
    if values is None:
        return Observation(
            None, missing_completeness, status_field, None,
            definition.source,
        )
    if not isinstance(values, Mapping):
        return Observation(
            None,
            COMPLETENESS_UNAVAILABLE,
            status_field,
            None,
            definition.source,
            f"the object containing {definition.source} must be a JSON object",
        )
    status = values.get(status_field)
    if status not in _STATUS_TO_COMPLETENESS:
        return _invalid_status_observation(
            status_field=status_field,
            status_value=status,
            value_field=definition.source,
        )
    return _observe(
        values, definition.field,
        completeness=completeness_of_status(status),
        status_field=status_field, status_value=status,
        value_field=definition.source,
        value_type=definition.value_type,
    )


def read_structural(
    values: Mapping[str, Any] | None,
    definition: MetricDefinition,
    *,
    block_status: Any,
    block_present: bool,
) -> Observation:
    """One structural complexity aggregate.

    The complexity block carries a single status for the whole family, so the
    gate is that status — not a per-aggregate one, which the producer does not
    write and which must not be invented here.
    """
    if not block_present:
        return Observation(
            None, COMPLETENESS_ABSENT, "metrics.complexity.status", None,
            definition.source,
        )
    if values is not None and not isinstance(values, Mapping):
        return Observation(
            None,
            COMPLETENESS_UNAVAILABLE,
            "metrics.complexity.status",
            block_status,
            definition.source,
            f"the object containing {definition.source} must be a JSON object",
        )
    if block_status not in _STATUS_TO_COMPLETENESS:
        return _invalid_status_observation(
            status_field="metrics.complexity.status",
            status_value=block_status,
            value_field=definition.source,
        )
    if values is None:
        # The block exists and is evaluable, but this language recorded no
        # measured population. Nothing of that kind to measure.
        completeness = completeness_of_status(block_status)
        if completeness in (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL):
            completeness = COMPLETENESS_NOT_APPLICABLE
        return Observation(
            None, completeness, "metrics.complexity.status", block_status,
            definition.source,
        )
    return _observe(
        values, definition.field,
        completeness=completeness_of_status(block_status),
        status_field="metrics.complexity.status", status_value=block_status,
        value_field=definition.source,
        value_type=definition.value_type,
    )


def read_cognitive(
    aggregate: Mapping[str, Any] | None,
    definition: MetricDefinition,
    *,
    cognitive_state: str,
    ledger_present: bool,
) -> Observation:
    """One cognitive aggregate, derived by the existing presentation seam.

    ``aggregate`` is the mapping :func:`complexity_view.cognitive_aggregate`
    returned for this unit's rows, or ``None`` when the unit has no rows.

    ``ledger_present`` is required because "no rows" has two causes that must
    not be merged. A subject that genuinely contains no callables of this
    language has **nothing to measure** — a complete answer. A run that
    published no callables ledger at all has an **unavailable** measurement,
    even when the repository document still claims the state is `measured`:
    these aggregates are derived from the persisted per-callable column, and
    without the column there is nothing to derive from. Reporting that as
    `not_applicable` would let a missing artifact pass a gate.
    """
    if cognitive_state not in _COGNITIVE_STATE_TO_COMPLETENESS:
        return _invalid_status_observation(
            status_field="metrics.complexity.cognitive_measurement_state",
            status_value=cognitive_state,
            value_field=definition.source,
        )
    completeness = _COGNITIVE_STATE_TO_COMPLETENESS[cognitive_state]
    evaluable_state = completeness in (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL)

    if not ledger_present:
        # A state that already says `not_applicable` or `absent` is the honest
        # answer and is kept. A state claiming a measurement, with no ledger to
        # derive it from, is unavailable.
        if evaluable_state:
            completeness = COMPLETENESS_UNAVAILABLE
        return Observation(
            None, completeness, "metrics.complexity.cognitive_measurement_state",
            cognitive_state, definition.source,
        )

    if aggregate is None:
        if evaluable_state:
            completeness = COMPLETENESS_NOT_APPLICABLE
        return Observation(
            None, completeness, "metrics.complexity.cognitive_measurement_state",
            cognitive_state, definition.source,
        )
    return _observe(
        aggregate, definition.field, completeness=completeness,
        status_field="metrics.complexity.cognitive_measurement_state",
        status_value=cognitive_state, value_field=definition.source,
        value_type=definition.value_type,
    )


def read_hotspot_repository(
    counts: Mapping[str, Any] | None,
    definition: MetricDefinition,
    *,
    coverage_status: str,
) -> Observation:
    """One per-subject hotspot count from an admitted Hotspot document.

    ``coverage_status`` is the Hotspot vocabulary applied to the subject as a
    whole, decided by the evaluator before this is reached: `measured` when the
    admitted document covers this subject and recorded rows for it,
    `not_applicable` when it covers the subject and there was nothing to
    measure, and `unavailable` when there is no admitted evidence for this
    subject at all.

    The last case is the one that matters. It is `unavailable`, and it is never
    a count of zero: "no document said anything about this subject" and "this
    subject has no high-attention files" are different facts, and reading the
    first as the second turns a missing input into a clean gate.
    """
    completeness = hotspot_completeness_of_status(coverage_status)
    if counts is None:
        return Observation(
            None, completeness, "(hotspot evidence coverage)", coverage_status,
            definition.source,
        )
    return _observe(
        counts, definition.field, completeness=completeness,
        status_field="(hotspot evidence coverage)",
        status_value=coverage_status, value_field=definition.source,
        value_type=definition.value_type,
    )


def read_hotspot_file(
    row: Mapping[str, Any], definition: MetricDefinition
) -> Observation:
    """One per-file value from one row of an admitted Hotspot document.

    The status consulted is the one the Hotspot contract writes for that block,
    unchanged. `validate_hotspot_document` already refuses a row whose churn is
    unavailable but carries a zero, so an unavailable measurement cannot arrive
    here disguised as a count.
    """
    block_name, field_name, status_key = HOTSPOT_FILE_SOURCES[definition.identifier]
    block = row.get(block_name)
    status_field = f"hotspots[].{block_name}.{status_key}"
    if not isinstance(block, Mapping):
        return Observation(
            None, COMPLETENESS_UNAVAILABLE, status_field, None, definition.source
        )
    status = block.get(status_key)
    return _observe(
        block, field_name,
        completeness=hotspot_completeness_of_status(status),
        status_field=status_field, status_value=status,
        value_field=definition.source,
        value_type=definition.value_type,
    )


def read_duplication_repository(
    counts: Mapping[str, Any] | None,
    definition: MetricDefinition,
    *,
    coverage_status: str,
    kind_status: Any,
) -> Observation:
    """One per-subject duplication count from an admitted Duplication document.

    Two gates, and which one applies is a property of the metric:

    * ``coverage_status`` is the Duplication vocabulary applied to the subject
      as a whole -- `complete` when an admitted document binds to this subject,
      `unavailable` when there is no admitted evidence for it. That last case is
      never a count of zero: "no document spoke for this subject" and "this
      subject has no clones" are different facts.
    * ``kind_status`` is the persisted ``counts.<kind>.status`` for the metrics
      that have one, and is ignored for the file-population counts. It is what
      keeps `not_requested` and `failed` out of the evaluable classes.

    Coverage is checked first. A missing document makes the kind status
    unknowable, and reporting a kind as `not_requested` when no document was
    supplied at all would name the wrong cause.
    """
    coverage = duplication_completeness_of_status(coverage_status)
    if coverage not in (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL):
        return Observation(
            None, coverage, "(duplication evidence coverage)", coverage_status,
            definition.source,
        )
    if counts is None:
        return Observation(
            None, COMPLETENESS_UNAVAILABLE, "(duplication evidence coverage)",
            coverage_status, definition.source,
        )

    kind = DUPLICATION_REPOSITORY_KIND.get(definition.identifier)
    if kind is None:
        return _observe(
            counts, definition.field, completeness=coverage,
            status_field="(duplication evidence coverage)",
            status_value=coverage_status, value_field=definition.source,
            value_type=definition.value_type,
        )
    return _observe(
        counts, definition.field,
        completeness=duplication_completeness_of_status(kind_status),
        status_field=f"counts.{kind}.status", status_value=kind_status,
        value_field=definition.source,
        value_type=definition.value_type,
    )


def read_duplication_group(
    group: Mapping[str, Any],
    definition: MetricDefinition,
    *,
    kind_status: Any,
) -> Observation:
    """One value from one clone group of an admitted Duplication document.

    The status consulted is the kind's persisted ``counts.<kind>.status``, not
    anything about the group itself: the Duplication contract records
    completeness per kind, and a group only exists at all when that kind was
    measured. The caller has already refused to enumerate groups for a kind
    whose status is not evaluable, so this is the partial/complete distinction
    only.
    """
    kind, field_name = DUPLICATION_GROUP_SOURCES[definition.identifier]
    return _observe(
        group, field_name,
        completeness=duplication_completeness_of_status(kind_status),
        status_field=f"counts.{kind}.status", status_value=kind_status,
        value_field=definition.source,
        value_type=definition.value_type,
    )


def read_callable(
    row: Mapping[str, Any], definition: MetricDefinition
) -> Observation:
    """One per-callable field from one ledger row."""
    status_column = CALLABLE_STATUS_COLUMN[definition.identifier]
    if status_column is None:
        # Nullability carries the distinction; there is no status column.
        raw = row.get(definition.field)
        number = numeric_value(raw)
        error = None
        if raw is not None and number is None:
            error = (
                f"{definition.source} must contain a finite integer or be empty"
            )
        elif isinstance(number, float) and not number.is_integer():
            error = (
                f"{definition.source} is integer-valued and cannot contain "
                f"non-integral value {raw!r}"
            )
            number = None
        elif isinstance(number, float):
            number = int(number)
        return Observation(
            value=number,
            completeness=(
                COMPLETENESS_COMPLETE if number is not None
                else COMPLETENESS_UNAVAILABLE
            ),
            status_field="(value nullability)",
            status_value=None if number is None else "measured",
            value_field=definition.source,
            error=error,
        )
    status = row.get(status_column)
    if status not in _STATUS_TO_COMPLETENESS:
        return _invalid_status_observation(
            status_field=status_column,
            status_value=status,
            value_field=definition.source,
        )
    return _observe(
        row, definition.field, completeness=completeness_of_status(status),
        status_field=status_column, status_value=status,
        value_field=definition.source,
        value_type=definition.value_type,
        allow_numeric_text=True,
    )
