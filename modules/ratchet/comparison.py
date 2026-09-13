"""Pure Ratchet V1 observation comparison.

No input is loaded here and no policy verdict is produced. The comparator
checks that three already-constructed values describe one target, calculates
the signed absolute delta, and classifies the movement against the frozen rule
direction and tolerance.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

from modules.ratchet.contract import (
    BaselineObservation,
    Number,
    RatchetDirection,
    RatchetRule,
    RatchetScope,
)


class RatchetComparisonError(ValueError):
    """The supplied observations cannot be compared by the supplied rule."""


class ComparisonDecision(str, Enum):
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    TOLERATED = "tolerated"
    REGRESSION = "regression"


def _finite_number(value: Any, field: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RatchetComparisonError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise RatchetComparisonError(f"{field} must be finite")
    return 0 if value == 0 else value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RatchetComparisonError(f"{field} must be a non-empty trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class CurrentObservation:
    subject_key: str
    metric: str
    scope: RatchetScope | str
    current_value: Number
    language: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_key", _text(self.subject_key, "current.subject_key"))
        object.__setattr__(self, "metric", _text(self.metric, "current.metric"))
        try:
            scope = RatchetScope(self.scope)
        except (TypeError, ValueError) as exc:
            raise RatchetComparisonError(f"unsupported current scope {self.scope!r}") from exc
        object.__setattr__(self, "scope", scope)
        if not self.metric.startswith(f"{scope.value}."):
            raise RatchetComparisonError(
                f"current.metric must use the {scope.value!r} scope prefix"
            )
        if scope is RatchetScope.REPOSITORY and self.language is not None:
            raise RatchetComparisonError("repository observation must not have a language")
        if scope is RatchetScope.LANGUAGE:
            language = _text(self.language, "current.language")
            canonical = unicodedata.normalize("NFC", language).casefold()
            if language != canonical:
                raise RatchetComparisonError(
                    "current.language must be the canonical case-folded language key"
                )
            object.__setattr__(self, "language", language)
        object.__setattr__(
            self,
            "current_value",
            _finite_number(self.current_value, "current.current_value"),
        )


@dataclass(frozen=True, slots=True)
class ComparisonRecord:
    baseline_value: Number
    current_value: Number
    delta: Number
    direction: RatchetDirection
    regression_amount: Number
    decision: ComparisonDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "delta": self.delta,
            "direction": self.direction.value,
            "regression_amount": self.regression_amount,
            "decision": self.decision.value,
        }


def compare_observations(
    baseline: BaselineObservation,
    current: CurrentObservation,
    rule: RatchetRule,
) -> ComparisonRecord:
    """Compare one exact observation pair under one frozen ratchet rule."""

    if not isinstance(baseline, BaselineObservation):
        raise RatchetComparisonError("baseline observation is required")
    if not isinstance(current, CurrentObservation):
        raise RatchetComparisonError("current observation is required")
    if not isinstance(rule, RatchetRule):
        raise RatchetComparisonError("ratchet rule is required")
    if baseline.rule_id != rule.rule_id:
        raise RatchetComparisonError("baseline observation does not belong to rule")
    if current.metric != rule.metric or current.scope is not rule.scope:
        raise RatchetComparisonError("current observation metric/scope does not match rule")
    if current.subject_key != baseline.subject_key:
        raise RatchetComparisonError("current subject does not match baseline subject")
    if current.language != baseline.language:
        raise RatchetComparisonError("current language does not match baseline language")

    try:
        delta = current.current_value - baseline.baseline_value
    except (ArithmeticError, OverflowError) as exc:
        raise RatchetComparisonError("observation delta cannot be represented") from exc
    delta = _finite_number(delta, "comparison.delta")
    directional_change = (
        delta
        if rule.direction is RatchetDirection.INCREASE_IS_WORSE
        else -delta
    )
    directional_change = _finite_number(
        directional_change, "comparison.directional_change"
    )

    if delta == 0:
        regression_amount: Number = 0
        decision = ComparisonDecision.UNCHANGED
    elif directional_change < 0:
        regression_amount = 0
        decision = ComparisonDecision.IMPROVED
    else:
        regression_amount = directional_change
        decision = (
            ComparisonDecision.TOLERATED
            if regression_amount <= rule.max_regression
            else ComparisonDecision.REGRESSION
        )

    return ComparisonRecord(
        baseline_value=baseline.baseline_value,
        current_value=current.current_value,
        delta=delta,
        direction=rule.direction,
        regression_amount=regression_amount,
        decision=decision,
    )


__all__ = [
    "ComparisonDecision",
    "ComparisonRecord",
    "CurrentObservation",
    "RatchetComparisonError",
    "compare_observations",
]
