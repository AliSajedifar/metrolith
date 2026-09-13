"""The Cognitive Complexity differential-validation raw-result format.

Prepared at G2-A. **No real-subject campaign runs from here yet** -- this module
supplies the record shape, the pairing rules and the absence protocol, and is
exercised only against the synthetic probes and fixtures.

Identity before value, mechanically
===================================

Pairing goes through :mod:`validation.differential.callable_matching` unchanged
-- the same identity-only matcher the Complexity Contract 1.0.0 study used. It
cannot see a metric value, and this module never gives it one. That separation
is not stylistic: a matcher able to see values could be tuned, however
unintentionally, to pair the rows that agree, and the resulting study would
measure its own tuning.

The rule is stricter here than it was for cyclomatic complexity, because
Cognitive Complexity has a second, subtler way to leak a value into identity.
A suppressed-zero callable has **no metric row at all**, so the temptation is to
pair it from whatever the reference *did* print. The harness refuses: identity
for those languages comes from a separate enumerator run, and
:func:`build_reference_side` will not accept an enumeration that was derived
from the metric rows.

Eight distinctions the format must not lose
===========================================

Two orthogonal fields, because collapsing them into one vocabulary is how a
population difference ends up looking like a measurement:

**pairing** -- matched, archlens_only, reference_only, ambiguous.
**value source** -- reported, inferred_suppressed_zero, absent.

Their product carries every distinction the campaign has to keep apart, and
:data:`RESULT_STATES` names all eight so a test can assert each one is
reachable rather than trusting that it is.

A **reported zero** and an **inferred suppressed zero** are deliberately not the
same record. The first is a number the tool printed. The second is silence that
the harness was licensed to read as a number, on this run, after proving the
tool suppresses zeros and after establishing the callable exists by other means.
They are both "0" and they are not equally strong evidence, so they never share
a label.

No accuracy percentage is computed anywhere. A reference is a triangulation
mechanism, not ground truth, and for Cognitive Complexity there is no primary
independent adapter at all -- so a disagreement here is evidence about two
definitions, never on its own evidence of an ArchLens defect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from validation.differential import callable_matching, records
from validation.differential.cognitive_definitions import (
    COGNITIVE_REFERENCE,
    METRIC,
    mapping_for,
)
from validation.differential.cognitive_zero_suppression import (
    ABSENCE_INFERRED_ZERO,
    ABSENCE_NOT_EVALUABLE,
    ABSENCE_REFERENCE_MISSING,
    SuppressionProbe,
    interpret_absence,
)

# -- pairing -----------------------------------------------------------------

PAIRING_MATCHED = "matched"
PAIRING_ARCHLENS_ONLY = "archlens_only"
PAIRING_REFERENCE_ONLY = "reference_only"
PAIRING_AMBIGUOUS = "ambiguous"

PAIRINGS = (
    PAIRING_MATCHED,
    PAIRING_ARCHLENS_ONLY,
    PAIRING_REFERENCE_ONLY,
    PAIRING_AMBIGUOUS,
)

# -- where the reference number came from ------------------------------------

#: The reference printed this number.
VALUE_REPORTED = "reported"

#: The reference printed nothing, and the harness was licensed to read the
#: silence as 0. Weaker evidence than `reported`, and never merged with it.
VALUE_INFERRED_SUPPRESSED_ZERO = "inferred_suppressed_zero"

#: No value, and none may be inferred.
VALUE_ABSENT = "absent"

VALUE_SOURCES = (VALUE_REPORTED, VALUE_INFERRED_SUPPRESSED_ZERO, VALUE_ABSENT)

# -- outcome -----------------------------------------------------------------

OUTCOME_EXACT = records.AGREEMENT_EXACT
OUTCOME_DISAGREEMENT = records.AGREEMENT_DISAGREEMENT
OUTCOME_NOT_EVALUABLE = records.AGREEMENT_NOT_EVALUABLE
OUTCOME_NOT_COMPARABLE = records.AGREEMENT_NOT_COMPARABLE

OUTCOMES = (
    OUTCOME_EXACT,
    OUTCOME_DISAGREEMENT,
    OUTCOME_NOT_EVALUABLE,
    OUTCOME_NOT_COMPARABLE,
)

# -- the eight distinctions, named ------------------------------------------

STATE_MATCHED = "matched"
STATE_ARCHLENS_ONLY = "archlens_only"
STATE_REFERENCE_ONLY = "reference_only"
STATE_AMBIGUOUS = "ambiguous"
STATE_REPORTED_ZERO = "reported_zero"
STATE_INFERRED_SUPPRESSED_ZERO = "inferred_suppressed_zero"
STATE_NOT_EVALUABLE = "not_evaluable"
STATE_NUMERICAL_DISAGREEMENT = "numerical_disagreement"

#: Every distinction the raw format is required to preserve. A test asserts each
#: one is actually reachable from a real observation, because a vocabulary
#: nothing can produce is documentation rather than a format.
RESULT_STATES = (
    STATE_MATCHED,
    STATE_ARCHLENS_ONLY,
    STATE_REFERENCE_ONLY,
    STATE_AMBIGUOUS,
    STATE_REPORTED_ZERO,
    STATE_INFERRED_SUPPRESSED_ZERO,
    STATE_NOT_EVALUABLE,
    STATE_NUMERICAL_DISAGREEMENT,
)

#: Causes. `unresolved` is a permitted terminal state: an honest "not yet known"
#: beats a comfortable misclassification.
CAUSE_UNRESOLVED = records.CAUSE_UNRESOLVED
CAUSE_NONE = records.CAUSE_NONE
CAUSE_DEFINITION_MISMATCH = records.CAUSE_DEFINITION_MISMATCH
CAUSE_MATCHING_AMBIGUITY = "matching_ambiguity"
CAUSE_REFERENCE_TOOL_LIMITATION = "reference_tool_limitation"
CAUSE_POPULATION_DIFFERENCE = "population_difference"

CAUSES = (
    CAUSE_DEFINITION_MISMATCH,
    CAUSE_MATCHING_AMBIGUITY,
    CAUSE_POPULATION_DIFFERENCE,
    CAUSE_REFERENCE_TOOL_LIMITATION,
    records.CAUSE_ARCHLENS_DEFECT,
    records.CAUSE_PARSER_LIMITATION,
    records.CAUSE_UNSUPPORTED_CONSTRUCT,
    CAUSE_UNRESOLVED,
    CAUSE_NONE,
)

COGNITIVE_STUDY_FORMAT_VERSION = "1.0.0"


class IdentityLeak(RuntimeError):
    """An enumeration was derived from metric rows. Refused.

    Identity established from the metric output cannot see a suppressed zero --
    that is the whole reason an enumerator exists -- so accepting one would
    silently collapse the protocol back to what it replaces.
    """


@dataclass(frozen=True)
class CognitiveObservation:
    """One callable's comparison. Raw until adjudicated."""

    subject_key: str
    language: str
    reference_name: str
    reference_version: str | None
    relative_path: str
    qualified_name: str | None
    start_line: int | None
    end_line: int | None
    pairing: str
    match_tier: str | None
    archlens_value: int | None
    reference_value: int | None
    reference_value_source: str
    difference: int | None
    outcome: str
    cause: str
    reason: str | None = None
    absence_verdict: str | None = None
    metric: str = METRIC

    def __post_init__(self) -> None:
        if self.pairing not in PAIRINGS:
            raise ValueError(f"unknown pairing {self.pairing!r}")
        if self.reference_value_source not in VALUE_SOURCES:
            raise ValueError(f"unknown value source {self.reference_value_source!r}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")
        if self.cause not in CAUSES:
            raise ValueError(f"unknown cause {self.cause!r}")
        if self.outcome == OUTCOME_DISAGREEMENT and self.cause == CAUSE_NONE:
            raise ValueError(
                "a disagreement must record a cause; use 'unresolved' when it "
                "is genuinely not yet known"
            )
        if (
            self.reference_value_source == VALUE_ABSENT
            and self.reference_value is not None
        ):
            raise ValueError(
                "an absent reference value must not carry a number; this is the "
                "exact shape that turns silence into agreement"
            )
        if (
            self.reference_value_source == VALUE_INFERRED_SUPPRESSED_ZERO
            and self.reference_value != 0
        ):
            raise ValueError(
                "an inferred suppressed zero is 0 by definition; any other "
                "value means the inference was applied to the wrong row"
            )

    @property
    def state(self) -> str:
        """The named distinction this observation carries."""
        if self.pairing == PAIRING_AMBIGUOUS:
            return STATE_AMBIGUOUS
        if self.pairing == PAIRING_ARCHLENS_ONLY:
            return STATE_ARCHLENS_ONLY
        if self.pairing == PAIRING_REFERENCE_ONLY:
            return STATE_REFERENCE_ONLY
        # The outcome is decided BEFORE the value's provenance. An inferred
        # zero that disagrees is a numerical disagreement that happens to rest
        # on inferred evidence -- labelling it by its provenance would hide it
        # from the disagreement count, which is the one number adjudication
        # works from. `reference_value_source` still carries the provenance on
        # every such row, so nothing is lost by ordering it this way.
        if self.outcome == OUTCOME_DISAGREEMENT:
            return STATE_NUMERICAL_DISAGREEMENT
        if self.outcome in (OUTCOME_NOT_EVALUABLE, OUTCOME_NOT_COMPARABLE):
            return STATE_NOT_EVALUABLE
        if self.reference_value_source == VALUE_INFERRED_SUPPRESSED_ZERO:
            return STATE_INFERRED_SUPPRESSED_ZERO
        if self.reference_value == 0 and self.reference_value_source == VALUE_REPORTED:
            return STATE_REPORTED_ZERO
        return STATE_MATCHED

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        payload["state"] = self.state
        return payload


@dataclass
class PopulationAccounting:
    """Who each side saw. A population difference is a finding, not noise."""

    archlens_rows: int = 0
    reference_metric_rows: int = 0
    reference_enumerated_rows: int = 0
    matched: int = 0
    matched_by_tier: dict[str, int] = field(default_factory=dict)
    ambiguous: int = 0
    unmatched_archlens: int = 0
    unmatched_reference: int = 0
    inferred_suppressed_zeros: int = 0
    unreadable_files: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "archlens_rows": self.archlens_rows,
            "reference_metric_rows": self.reference_metric_rows,
            "reference_enumerated_rows": self.reference_enumerated_rows,
            "matched": self.matched,
            "matched_by_tier": dict(self.matched_by_tier),
            "ambiguous": self.ambiguous,
            "unmatched_archlens": self.unmatched_archlens,
            "unmatched_reference": self.unmatched_reference,
            "inferred_suppressed_zeros": self.inferred_suppressed_zeros,
            "unreadable_file_count": len(self.unreadable_files),
            "unreadable_files": self.unreadable_files[:20],
        }


def build_reference_side(
    *,
    metric_rows: Sequence[Mapping[str, Any]],
    enumerated_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The rows the matcher is given: every callable the reference SAW.

    For a suppressing reference this is the enumeration, with values attached
    from the metric rows where they exist. That ordering is the protocol: the
    population comes from the enumerator, and the metric only decorates it.

    Refuses an enumeration that is the metric rows in disguise. A caller that
    passes the same sequence twice has collapsed identity back onto the value,
    and the collapse is silent unless it is checked here.
    """
    if enumerated_rows and metric_rows and list(enumerated_rows) == list(metric_rows):
        raise IdentityLeak(
            "the enumeration is identical to the metric rows, so identity would "
            "be established from the value. A suppressed-zero callable is absent "
            "from the metric rows by definition and could never be enumerated "
            "this way."
        )

    if not enumerated_rows:
        # The reference reports its own zeros; the metric run IS the population.
        return [dict(row) for row in metric_rows]

    def locator(row: Mapping[str, Any]) -> tuple:
        return (
            str(row.get("relative_path") or "").replace("\\", "/"),
            row.get("qualified_name") or None,
            row.get("start_line"),
        )

    values = {locator(row): row.get("value") for row in metric_rows}
    combined: list[dict[str, Any]] = []
    for row in enumerated_rows:
        entry = {key: value for key, value in row.items() if key != "value"}
        key = locator(row)
        if key in values:
            entry["value"] = values[key]
            entry["value_present_in_metric_output"] = True
        else:
            entry["value_present_in_metric_output"] = False
        combined.append(entry)
    return combined


def _integer(value: Any) -> int | None:
    """An empty cell is an ABSENT value, never a zero."""
    if value is None or value == "":
        return None
    return int(value)


def _lookup(rows: Sequence[Mapping[str, Any]]) -> dict[tuple, Mapping[str, Any]]:
    index: dict[tuple, Mapping[str, Any]] = {}
    for row in rows:
        key = callable_matching.key_from_row(row)
        index.setdefault(
            (key.relative_path, key.qualified_name, key.start_line, key.end_line), row
        )
    return index


def _key_tuple(key: callable_matching.CallableKey) -> tuple:
    return (key.relative_path, key.qualified_name, key.start_line, key.end_line)


def compare_pairs(
    *,
    subject_key: str,
    language: str,
    match: callable_matching.MatchResult,
    archlens_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    suppression: SuppressionProbe | None,
) -> list[CognitiveObservation]:
    """Turn already-fixed pairings into raw observations.

    ``match`` is produced by the identity-only matcher and is not recomputed
    here. Every disagreement leaves as ``unresolved``; classification is a
    later, separate pass so the unclassified baseline survives as an artifact.
    """
    mapping = mapping_for(language)
    reference_name = COGNITIVE_REFERENCE[language]
    reference_version = mapping.reference_version

    left_index = _lookup(archlens_rows)
    right_index = _lookup(reference_rows)
    observations: list[CognitiveObservation] = []

    def emit(**kwargs: Any) -> None:
        observations.append(
            CognitiveObservation(
                subject_key=subject_key,
                language=language,
                reference_name=reference_name,
                reference_version=reference_version,
                **kwargs,
            )
        )

    for left_key, right_key, tier in match.matched:
        left = left_index[_key_tuple(left_key)]
        right = right_index[_key_tuple(right_key)]
        archlens_value = _integer(left.get("cognitive_complexity"))
        reference_value = _integer(right.get("value"))

        # D12: a construct the reference cannot see, scored 0 rather than
        # refused. The number exists and must still not be compared.
        if right.get("contains_unsupported_match"):
            emit(
                relative_path=left_key.relative_path,
                qualified_name=left_key.qualified_name,
                start_line=left_key.start_line,
                end_line=left_key.end_line,
                pairing=PAIRING_MATCHED,
                match_tier=tier,
                archlens_value=archlens_value,
                reference_value=None,
                reference_value_source=VALUE_ABSENT,
                difference=None,
                outcome=OUTCOME_NOT_COMPARABLE,
                cause=CAUSE_REFERENCE_TOOL_LIMITATION,
                reason=(
                    "D12: the reference cannot parse `match` and returns 0 "
                    "rather than refusing. The 0 is discarded rather than "
                    "compared -- a clean number that means 'I could not see "
                    "this' is the most dangerous shape a reference produces."
                ),
            )
            continue

        if archlens_value is None:
            emit(
                relative_path=left_key.relative_path,
                qualified_name=left_key.qualified_name,
                start_line=left_key.start_line,
                end_line=left_key.end_line,
                pairing=PAIRING_MATCHED,
                match_tier=tier,
                archlens_value=None,
                reference_value=reference_value,
                reference_value_source=(
                    VALUE_REPORTED if reference_value is not None else VALUE_ABSENT
                ),
                difference=None,
                outcome=OUTCOME_NOT_EVALUABLE,
                cause=CAUSE_NONE,
                reason=(
                    "ArchLens recorded no cognitive value for this callable "
                    f"(structural status {left.get('structural_complexity_status')!r}); "
                    "an absent measurement is never read as zero"
                ),
            )
            continue

        if reference_value is None:
            # The one place silence may become a number, and only through the
            # protocol.
            enumerated = bool(right.get("value_present_in_metric_output") is False)
            verdict = interpret_absence(
                language=language,
                identity_established=enumerated,
                suppression=suppression,
            )
            if verdict.verdict == ABSENCE_INFERRED_ZERO:
                difference = 0 - archlens_value
                emit(
                    relative_path=left_key.relative_path,
                    qualified_name=left_key.qualified_name,
                    start_line=left_key.start_line,
                    end_line=left_key.end_line,
                    pairing=PAIRING_MATCHED,
                    match_tier=tier,
                    archlens_value=archlens_value,
                    reference_value=0,
                    reference_value_source=VALUE_INFERRED_SUPPRESSED_ZERO,
                    difference=difference,
                    outcome=OUTCOME_EXACT if difference == 0 else OUTCOME_DISAGREEMENT,
                    cause=CAUSE_NONE if difference == 0 else CAUSE_UNRESOLVED,
                    reason=verdict.reason,
                    absence_verdict=verdict.verdict,
                )
            else:
                emit(
                    relative_path=left_key.relative_path,
                    qualified_name=left_key.qualified_name,
                    start_line=left_key.start_line,
                    end_line=left_key.end_line,
                    pairing=PAIRING_MATCHED,
                    match_tier=tier,
                    archlens_value=archlens_value,
                    reference_value=None,
                    reference_value_source=VALUE_ABSENT,
                    difference=None,
                    outcome=OUTCOME_NOT_EVALUABLE,
                    cause=(
                        CAUSE_POPULATION_DIFFERENCE
                        if verdict.verdict == ABSENCE_REFERENCE_MISSING
                        else CAUSE_NONE
                    ),
                    reason=verdict.reason,
                    absence_verdict=verdict.verdict,
                )
            continue

        difference = reference_value - archlens_value
        emit(
            relative_path=left_key.relative_path,
            qualified_name=left_key.qualified_name,
            start_line=left_key.start_line,
            end_line=left_key.end_line,
            pairing=PAIRING_MATCHED,
            match_tier=tier,
            archlens_value=archlens_value,
            reference_value=reference_value,
            reference_value_source=VALUE_REPORTED,
            difference=difference,
            outcome=OUTCOME_EXACT if difference == 0 else OUTCOME_DISAGREEMENT,
            cause=CAUSE_NONE if difference == 0 else CAUSE_UNRESOLVED,
        )

    for left_key, candidates in match.ambiguous:
        emit(
            relative_path=left_key.relative_path,
            qualified_name=left_key.qualified_name,
            start_line=left_key.start_line,
            end_line=left_key.end_line,
            pairing=PAIRING_AMBIGUOUS,
            match_tier=callable_matching.AMBIGUOUS,
            archlens_value=None,
            reference_value=None,
            reference_value_source=VALUE_ABSENT,
            difference=None,
            outcome=OUTCOME_NOT_EVALUABLE,
            cause=CAUSE_MATCHING_AMBIGUITY,
            reason=(
                f"{len(candidates)} reference candidates; classified rather "
                f"than guessed, because a wrong pairing invents a disagreement "
                f"that does not exist"
            ),
        )

    for left_key in match.unmatched_archlens:
        emit(
            relative_path=left_key.relative_path,
            qualified_name=left_key.qualified_name,
            start_line=left_key.start_line,
            end_line=left_key.end_line,
            pairing=PAIRING_ARCHLENS_ONLY,
            match_tier=None,
            archlens_value=_integer(
                left_index.get(_key_tuple(left_key), {}).get("cognitive_complexity")
            ),
            reference_value=None,
            reference_value_source=VALUE_ABSENT,
            difference=None,
            outcome=OUTCOME_NOT_EVALUABLE,
            cause=CAUSE_POPULATION_DIFFERENCE,
            reason=(
                "the reference's own parser did not enumerate this callable, so "
                "its absence is a population difference (D2) and never a zero"
            ),
            absence_verdict=ABSENCE_REFERENCE_MISSING,
        )

    for right_key in match.unmatched_reference:
        emit(
            relative_path=right_key.relative_path,
            qualified_name=right_key.qualified_name,
            start_line=right_key.start_line,
            end_line=right_key.end_line,
            pairing=PAIRING_REFERENCE_ONLY,
            match_tier=None,
            archlens_value=None,
            reference_value=_integer(
                right_index.get(_key_tuple(right_key), {}).get("value")
            ),
            reference_value_source=(
                VALUE_REPORTED
                if _integer(right_index.get(_key_tuple(right_key), {}).get("value"))
                is not None
                else VALUE_ABSENT
            ),
            difference=None,
            outcome=OUTCOME_NOT_EVALUABLE,
            cause=CAUSE_POPULATION_DIFFERENCE,
            reason=(
                "the reference measures a callable outside the canonical "
                "`methods_functions` population (D1/D2) -- typically a lambda, "
                "constructor or nested callable ArchLens excludes"
            ),
        )

    return observations


def account(
    match: callable_matching.MatchResult,
    archlens_rows: Sequence[Mapping[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
    enumerated_rows: Sequence[Mapping[str, Any]],
    observations: Sequence[CognitiveObservation],
    unreadable: Sequence[Mapping[str, str]] = (),
) -> PopulationAccounting:
    summary = match.summary()
    return PopulationAccounting(
        archlens_rows=len(archlens_rows),
        reference_metric_rows=len(metric_rows),
        reference_enumerated_rows=len(enumerated_rows),
        matched=summary["matched"],
        matched_by_tier=summary["matched_by_tier"],
        ambiguous=summary["ambiguous"],
        unmatched_archlens=summary["unmatched_archlens"],
        unmatched_reference=summary["unmatched_reference"],
        inferred_suppressed_zeros=sum(
            1
            for item in observations
            if item.reference_value_source == VALUE_INFERRED_SUPPRESSED_ZERO
        ),
        unreadable_files=[dict(item) for item in unreadable],
    )


def summarize(observations: Sequence[CognitiveObservation]) -> dict[str, Any]:
    """Raw counts, by every distinction the format keeps. No percentage."""
    by_state: dict[str, int] = {name: 0 for name in RESULT_STATES}
    by_outcome: dict[str, int] = {}
    by_cause: dict[str, int] = {}
    for item in observations:
        by_state[item.state] = by_state.get(item.state, 0) + 1
        by_outcome[item.outcome] = by_outcome.get(item.outcome, 0) + 1
        if item.outcome == OUTCOME_DISAGREEMENT:
            by_cause[item.cause] = by_cause.get(item.cause, 0) + 1
    return {
        "observations": len(observations),
        "by_state": by_state,
        "by_outcome": dict(sorted(by_outcome.items())),
        "disagreements_by_cause": dict(sorted(by_cause.items())),
        "unresolved_disagreements": by_cause.get(CAUSE_UNRESOLVED, 0),
        "reporting_rule": (
            "Raw counts only. No accuracy percentage is computed anywhere: a "
            "reference is a triangulation mechanism, not ground truth, and "
            "Cognitive Complexity has no primary independent adapter at all. "
            "An inferred suppressed zero is reported SEPARATELY from a reported "
            "zero because they are not equally strong evidence."
        ),
    }


def persist_raw(
    observations: Sequence[CognitiveObservation],
    population: PopulationAccounting,
    destination: Path,
    *,
    study_metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write the unclassified baseline. Called BEFORE any adjudication."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cognitive_study_format_version": COGNITIVE_STUDY_FORMAT_VERSION,
        "metric": METRIC,
        "stage": "raw_observations_before_adjudication",
        "study_metadata": dict(study_metadata or {}),
        "result_states": list(RESULT_STATES),
        "cause_taxonomy": list(CAUSES),
        "population": population.as_dict(),
        "summary": summarize(observations),
        "observations": [item.as_dict() for item in observations],
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination
