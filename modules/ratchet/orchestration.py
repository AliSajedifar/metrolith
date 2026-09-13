"""Deterministic BR3 observation matching and comparison orchestration.

The output describes whether each frozen baseline target was comparable.  A
numeric :class:`~modules.ratchet.comparison.ComparisonRecord` exists only for a
complete, contract-compatible current value.  This layer makes no policy or
gate decision and emits no finding.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from modules.ratchet.admission import AdmittedBaselineSubjects
from modules.ratchet.comparison import (
    ComparisonRecord,
    CurrentObservation,
    compare_observations,
)
from modules.ratchet.contract import RatchetScope
from modules.ratchet.observations import (
    CurrentMetricObservation,
    ObservationCompleteness,
    PersistedRunView,
    extract_current_observations,
    require_v1_metric,
)


class ComparisonOrchestrationError(ValueError):
    """The admitted inputs do not form an unambiguous comparison population."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class ComparisonStatus(str, Enum):
    COMPARED = "compared"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    ABSENT = "absent"
    INCOMPATIBLE_CONTRACT = "incompatible_contract"


_STATUS_BY_COMPLETENESS: dict[ObservationCompleteness, ComparisonStatus] = {
    ObservationCompleteness.MISSING: ComparisonStatus.MISSING,
    ObservationCompleteness.UNAVAILABLE: ComparisonStatus.UNAVAILABLE,
    ObservationCompleteness.PARTIAL: ComparisonStatus.PARTIAL,
    ObservationCompleteness.NOT_APPLICABLE: ComparisonStatus.NOT_APPLICABLE,
    ObservationCompleteness.ABSENT: ComparisonStatus.ABSENT,
}


@dataclass(frozen=True, slots=True)
class OrchestratedComparison:
    """One frozen rule target and its explicit comparison availability."""

    rule_id: str
    metric: str
    scope: RatchetScope
    subject_key: str
    language: str | None
    status: ComparisonStatus
    completeness: ObservationCompleteness
    metric_contract: str
    baseline_contract_version: str | None
    current_contract_version: str | None
    comparison: ComparisonRecord | None

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.subject_key, self.language or "", self.rule_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "metric": self.metric,
            "scope": self.scope.value,
            "subject_key": self.subject_key,
            "language": self.language,
            "status": self.status.value,
            "completeness": self.completeness.value,
            "metric_contract": self.metric_contract,
            "baseline_contract_version": self.baseline_contract_version,
            "current_contract_version": self.current_contract_version,
            "comparison": (
                None if self.comparison is None else self.comparison.to_dict()
            ),
        }


def _coordinate(
    subject_key: str,
    language: str | None,
    scope: RatchetScope,
    metric: str,
) -> tuple[str, str, str, str]:
    return (subject_key, language or "", scope.value, metric)


def orchestrate_comparisons(
    admitted: AdmittedBaselineSubjects,
    current_observations: Iterable[CurrentMetricObservation],
) -> tuple[OrchestratedComparison, ...]:
    """Match exact coordinates and call the BR1 comparator where permitted."""

    if not isinstance(admitted, AdmittedBaselineSubjects):
        raise ComparisonOrchestrationError("admitted_baseline_subjects_required")
    try:
        observations = tuple(current_observations)
    except TypeError as exc:
        raise ComparisonOrchestrationError("current_observations_required") from exc

    current_by_coordinate: dict[
        tuple[str, str, str, str], CurrentMetricObservation
    ] = {}
    for index, observation in enumerate(observations):
        if not isinstance(observation, CurrentMetricObservation):
            raise ComparisonOrchestrationError(
                "invalid_current_observation", str(index)
            )
        key = observation.coordinate
        if key in current_by_coordinate:
            raise ComparisonOrchestrationError(
                "duplicate_current_observation", repr(key)
            )
        current_by_coordinate[key] = observation

    baseline = admitted.artifact.baseline
    rule_by_id = {rule.rule_id: rule for rule in baseline.rules}
    pair_by_key = {pair.subject_key: pair for pair in admitted.pairs}
    expected_coordinates: set[tuple[str, str, str, str]] = set()
    output: list[OrchestratedComparison] = []

    for baseline_observation in sorted(
        baseline.observations,
        key=lambda item: (item.subject_key, item.language or "", item.rule_id),
    ):
        rule = rule_by_id[baseline_observation.rule_id]
        definition = require_v1_metric(rule.metric, rule.scope)
        coordinate = _coordinate(
            baseline_observation.subject_key,
            baseline_observation.language,
            rule.scope,
            rule.metric,
        )
        expected_coordinates.add(coordinate)
        current = current_by_coordinate.get(coordinate)
        pair = pair_by_key.get(baseline_observation.subject_key)
        if pair is None:
            raise ComparisonOrchestrationError(
                "unpaired_baseline_subject", baseline_observation.subject_key
            )
        baseline_version = pair.baseline.contract_versions.get(
            rule.metric_contract
        )
        current_versions = {
            binding.name: binding.version
            for binding in pair.current.metric_contracts
        }
        paired_current_version = current_versions.get(rule.metric_contract)

        if current is None:
            output.append(
                OrchestratedComparison(
                    rule_id=rule.rule_id,
                    metric=rule.metric,
                    scope=rule.scope,
                    subject_key=baseline_observation.subject_key,
                    language=baseline_observation.language,
                    status=ComparisonStatus.MISSING,
                    completeness=ObservationCompleteness.MISSING,
                    metric_contract=rule.metric_contract,
                    baseline_contract_version=baseline_version,
                    current_contract_version=paired_current_version,
                    comparison=None,
                )
            )
            continue

        contracts_match = (
            definition.metric_contract == rule.metric_contract
            and current.metric_contract == rule.metric_contract
            and baseline_version is not None
            and baseline_version == paired_current_version
            and current.metric_contract_version == paired_current_version
        )
        if not contracts_match:
            output.append(
                OrchestratedComparison(
                    rule_id=rule.rule_id,
                    metric=rule.metric,
                    scope=rule.scope,
                    subject_key=baseline_observation.subject_key,
                    language=baseline_observation.language,
                    status=ComparisonStatus.INCOMPATIBLE_CONTRACT,
                    completeness=current.completeness,
                    metric_contract=rule.metric_contract,
                    baseline_contract_version=baseline_version,
                    current_contract_version=current.metric_contract_version,
                    comparison=None,
                )
            )
            continue

        if current.completeness is not ObservationCompleteness.COMPLETE:
            output.append(
                OrchestratedComparison(
                    rule_id=rule.rule_id,
                    metric=rule.metric,
                    scope=rule.scope,
                    subject_key=baseline_observation.subject_key,
                    language=baseline_observation.language,
                    status=_STATUS_BY_COMPLETENESS[current.completeness],
                    completeness=current.completeness,
                    metric_contract=rule.metric_contract,
                    baseline_contract_version=baseline_version,
                    current_contract_version=current.metric_contract_version,
                    comparison=None,
                )
            )
            continue

        if current.value is None:
            output.append(
                OrchestratedComparison(
                    rule_id=rule.rule_id,
                    metric=rule.metric,
                    scope=rule.scope,
                    subject_key=baseline_observation.subject_key,
                    language=baseline_observation.language,
                    status=ComparisonStatus.UNAVAILABLE,
                    completeness=ObservationCompleteness.UNAVAILABLE,
                    metric_contract=rule.metric_contract,
                    baseline_contract_version=baseline_version,
                    current_contract_version=current.metric_contract_version,
                    comparison=None,
                )
            )
            continue

        comparison = compare_observations(
            baseline_observation,
            CurrentObservation(
                subject_key=current.subject_key,
                metric=current.metric,
                scope=current.scope,
                language=current.language,
                current_value=current.value,
            ),
            rule,
        )
        output.append(
            OrchestratedComparison(
                rule_id=rule.rule_id,
                metric=rule.metric,
                scope=rule.scope,
                subject_key=baseline_observation.subject_key,
                language=baseline_observation.language,
                status=ComparisonStatus.COMPARED,
                completeness=current.completeness,
                metric_contract=rule.metric_contract,
                baseline_contract_version=baseline_version,
                current_contract_version=current.metric_contract_version,
                comparison=comparison,
            )
        )

    unexpected = sorted(set(current_by_coordinate) - expected_coordinates)
    if unexpected:
        raise ComparisonOrchestrationError(
            "unexpected_current_observation", repr(unexpected[0])
        )
    return tuple(sorted(output, key=lambda item: item.sort_key))


def extract_and_compare(
    view: PersistedRunView,
    admitted: AdmittedBaselineSubjects,
) -> tuple[OrchestratedComparison, ...]:
    """Compose BR3's extraction and pure comparison boundaries."""

    observations = extract_current_observations(view, admitted)
    return orchestrate_comparisons(admitted, observations)


__all__ = [
    "ComparisonOrchestrationError",
    "ComparisonStatus",
    "OrchestratedComparison",
    "extract_and_compare",
    "orchestrate_comparisons",
]
