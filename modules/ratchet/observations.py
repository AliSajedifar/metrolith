"""Current-observation projection for Baseline/Ratchet V1.

This module reads already-persisted run views.  It does not open a run, invoke
an analyzer, parse source code, or define a metric.  Repository and language
values are projected from the authoritative repository documents; cognitive
aggregates reuse :mod:`modules.complexity_view` over the persisted callable
ledger, which is the existing read-model definition for those aggregates.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from modules import complexity_view
from modules.ratchet.admission import AdmittedBaselineSubjects
from modules.ratchet.contract import Number, RatchetScope


class ObservationExtractionError(ValueError):
    """A persisted run cannot be projected onto the V1 observation surface."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class ObservationCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    ABSENT = "absent"
    # BR2 has already paired the subject population.  MISSING therefore means
    # that the admitted current run view did not contain the expected subject
    # observation at all; it is never represented by a numeric zero.
    MISSING = "missing"


class MetricFamily(str, Enum):
    CORE = "core"
    STRUCTURAL_COMPLEXITY = "structural_complexity"
    COGNITIVE_COMPLEXITY = "cognitive_complexity"


@dataclass(frozen=True, slots=True)
class RatchetMetricDefinition:
    """One persisted metric admitted to the deliberately finite V1 surface."""

    identifier: str
    scope: RatchetScope
    family: MetricFamily
    field: str
    status_field: str
    metric_contract: str
    value_type: str


@dataclass(frozen=True, slots=True)
class CurrentMetricObservation:
    """One current value plus the state that determines whether it is usable."""

    subject_key: str
    metric: str
    scope: RatchetScope
    language: str | None
    value: Number | None
    completeness: ObservationCompleteness
    metric_contract: str
    metric_contract_version: str | None
    status_field: str
    status_value: Any
    value_field: str

    @property
    def coordinate(self) -> tuple[str, str, str, str]:
        return (
            self.subject_key,
            self.language or "",
            self.scope.value,
            self.metric,
        )

    @property
    def comparable(self) -> bool:
        return (
            self.completeness is ObservationCompleteness.COMPLETE
            and self.value is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "metric": self.metric,
            "scope": self.scope.value,
            "language": self.language,
            "value": self.value,
            "completeness": self.completeness.value,
            "metric_contract": self.metric_contract,
            "metric_contract_version": self.metric_contract_version,
            "status_field": self.status_field,
            "status_value": self.status_value,
            "value_field": self.value_field,
        }


class PersistedRunView(Protocol):
    """Small structural boundary consumed from ``ImmutableRunView``."""

    repositories: Iterable[Mapping[str, Any]]
    has_callable_artifact: bool

    def stream_callables(self) -> Iterable[Mapping[str, Any]]: ...


_CORE_FIELDS: tuple[tuple[str, str], ...] = (
    ("lines_of_code", "loc_status"),
    ("source_files", "source_files_status"),
    ("classes_structs", "classes_structs_status"),
    ("methods_functions", "methods_functions_status"),
)


def _definitions() -> tuple[RatchetMetricDefinition, ...]:
    definitions: list[RatchetMetricDefinition] = []
    for scope in (RatchetScope.REPOSITORY, RatchetScope.LANGUAGE):
        prefix = scope.value
        definitions.extend(
            RatchetMetricDefinition(
                identifier=f"{prefix}.{field}",
                scope=scope,
                family=MetricFamily.CORE,
                field=field,
                status_field=status,
                metric_contract="metrics",
                value_type="integer",
            )
            for field, status in _CORE_FIELDS
        )
        definitions.extend(
            RatchetMetricDefinition(
                identifier=f"{prefix}.{field}",
                scope=scope,
                family=MetricFamily.STRUCTURAL_COMPLEXITY,
                field=field,
                status_field="metrics.complexity.status",
                metric_contract="complexity",
                value_type=("number" if field.endswith("_mean") else "integer"),
            )
            for field in complexity_view.AGGREGATE_FIELDS
        )
        definitions.extend(
            RatchetMetricDefinition(
                identifier=f"{prefix}.{field}",
                scope=scope,
                family=MetricFamily.COGNITIVE_COMPLEXITY,
                field=field,
                status_field=(
                    "metrics.complexity.cognitive_measurement_state"
                ),
                metric_contract="complexity",
                value_type=("number" if field.endswith("_mean") else "integer"),
            )
            for field in complexity_view.COGNITIVE_AGGREGATE_FIELDS
        )
    return tuple(sorted(definitions, key=lambda item: item.identifier))


V1_METRICS: tuple[RatchetMetricDefinition, ...] = _definitions()
V1_METRICS_BY_ID: dict[str, RatchetMetricDefinition] = {
    definition.identifier: definition for definition in V1_METRICS
}


def require_v1_metric(
    metric: str, scope: RatchetScope | str
) -> RatchetMetricDefinition:
    """Return an exact V1 definition or reject every non-allowlisted scope."""

    try:
        canonical_scope = RatchetScope(scope)
    except (TypeError, ValueError) as exc:
        raise ObservationExtractionError("unsupported_scope", str(scope)) from exc
    definition = V1_METRICS_BY_ID.get(metric)
    if definition is None or definition.scope is not canonical_scope:
        raise ObservationExtractionError(
            "unsupported_metric", f"{canonical_scope.value}: {metric}"
        )
    return definition


def _numeric_value(raw: Any) -> Number | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return raw if math.isfinite(raw) else None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                parsed = float(text)
            except ValueError:
                return None
            return parsed if math.isfinite(parsed) else None
    return None


def _canonical_language(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    return unicodedata.normalize("NFC", value).casefold()


def _language_map(
    value: Any, *, subject_key: str, source: str
) -> dict[str, Mapping[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ObservationExtractionError(
            "invalid_language_mapping", f"{subject_key}: {source}"
        )
    result: dict[str, Mapping[str, Any]] = {}
    for raw_language, raw_values in value.items():
        language = _canonical_language(raw_language)
        if language is None or not isinstance(raw_values, Mapping):
            raise ObservationExtractionError(
                "invalid_language_observation", f"{subject_key}: {raw_language!r}"
            )
        if language in result:
            raise ObservationExtractionError(
                "duplicate_normalized_language",
                f"{subject_key}: {raw_language!r}",
            )
        result[language] = raw_values
    return result


_STATUS_COMPLETENESS: dict[Any, ObservationCompleteness] = {
    "complete": ObservationCompleteness.COMPLETE,
    "partial": ObservationCompleteness.PARTIAL,
    "failed": ObservationCompleteness.UNAVAILABLE,
    "not_applicable": ObservationCompleteness.NOT_APPLICABLE,
}

_COGNITIVE_COMPLETENESS: dict[Any, ObservationCompleteness] = {
    complexity_view.COGNITIVE_MEASURED: ObservationCompleteness.COMPLETE,
    complexity_view.COGNITIVE_PARTIAL: ObservationCompleteness.PARTIAL,
    complexity_view.COGNITIVE_FAILED: ObservationCompleteness.UNAVAILABLE,
    complexity_view.COGNITIVE_NOT_APPLICABLE: (
        ObservationCompleteness.NOT_APPLICABLE
    ),
    complexity_view.COGNITIVE_ABSENT: ObservationCompleteness.ABSENT,
}


def _observation(
    *,
    subject_key: str,
    language: str | None,
    definition: RatchetMetricDefinition,
    metric_contract_version: str | None,
    values: Mapping[str, Any] | None,
    completeness: ObservationCompleteness,
    status_value: Any,
    value_field: str,
) -> CurrentMetricObservation:
    field_missing = values is not None and definition.field not in values
    number = _numeric_value((values or {}).get(definition.field))
    # A complete/partial status cannot manufacture a value that was not
    # persisted.  This is unavailable evidence, never a zero.
    if field_missing and completeness in {
        ObservationCompleteness.COMPLETE,
        ObservationCompleteness.PARTIAL,
    }:
        completeness = ObservationCompleteness.MISSING
    elif completeness in {
        ObservationCompleteness.COMPLETE,
        ObservationCompleteness.PARTIAL,
    } and number is None:
        completeness = ObservationCompleteness.UNAVAILABLE
    if completeness not in {
        ObservationCompleteness.COMPLETE,
        ObservationCompleteness.PARTIAL,
    }:
        number = None
    return CurrentMetricObservation(
        subject_key=subject_key,
        metric=definition.identifier,
        scope=definition.scope,
        language=language,
        value=number,
        completeness=completeness,
        metric_contract=definition.metric_contract,
        metric_contract_version=metric_contract_version,
        status_field=definition.status_field,
        status_value=status_value,
        value_field=value_field,
    )


def _missing_observation(
    *,
    subject_key: str,
    language: str | None,
    definition: RatchetMetricDefinition,
    metric_contract_version: str | None,
) -> CurrentMetricObservation:
    return CurrentMetricObservation(
        subject_key=subject_key,
        metric=definition.identifier,
        scope=definition.scope,
        language=language,
        value=None,
        completeness=ObservationCompleteness.MISSING,
        metric_contract=definition.metric_contract,
        metric_contract_version=metric_contract_version,
        status_field=definition.status_field,
        status_value=None,
        value_field=_value_field(definition, language),
    )


def _value_field(
    definition: RatchetMetricDefinition, language: str | None
) -> str:
    if definition.family is MetricFamily.CORE:
        base = (
            "metrics.aggregate"
            if definition.scope is RatchetScope.REPOSITORY
            else f"metrics.by_language[{language}]"
        )
        return f"{base}.{definition.field}"
    if definition.family is MetricFamily.STRUCTURAL_COMPLEXITY:
        base = (
            "metrics.complexity.aggregate"
            if definition.scope is RatchetScope.REPOSITORY
            else f"metrics.complexity.by_language[{language}]"
        )
        return f"{base}.{definition.field}"
    return (
        "modules.complexity_view.cognitive_aggregate"
        "(persisted callables[].cognitive_complexity)"
    )


def _repository_index(
    repositories: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, repository in enumerate(repositories):
        if not isinstance(repository, Mapping):
            raise ObservationExtractionError(
                "invalid_repository_observation", str(index)
            )
        key = repository.get("subject_key")
        if not isinstance(key, str) or not key or key.strip() != key:
            raise ObservationExtractionError(
                "missing_repository_subject_key", str(index)
            )
        if key in result:
            raise ObservationExtractionError("duplicate_repository_subject", key)
        result[key] = repository
    return result


def _requested_targets(
    admitted: AdmittedBaselineSubjects,
) -> tuple[tuple[str, str | None, RatchetMetricDefinition], ...]:
    if not isinstance(admitted, AdmittedBaselineSubjects):
        raise ObservationExtractionError("admitted_baseline_subjects_required")
    baseline = admitted.artifact.baseline
    rules = {rule.rule_id: rule for rule in baseline.rules}
    targets: dict[
        tuple[str, str, str, str],
        tuple[str, str | None, RatchetMetricDefinition],
    ] = {}
    for baseline_observation in baseline.observations:
        rule = rules[baseline_observation.rule_id]
        definition = require_v1_metric(rule.metric, rule.scope)
        key = (
            baseline_observation.subject_key,
            baseline_observation.language or "",
            rule.scope.value,
            rule.metric,
        )
        targets[key] = (
            baseline_observation.subject_key,
            baseline_observation.language,
            definition,
        )
    return tuple(targets[key] for key in sorted(targets))


def _cognitive_values(
    view: PersistedRunView,
    requested: set[tuple[str, str | None]],
) -> tuple[bool, dict[tuple[str, str | None], list[dict[str, Any]]]]:
    ledger_present = bool(view.has_callable_artifact)
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {
        key: [] for key in requested
    }
    if not ledger_present:
        return False, groups

    # One streaming pass, retaining only the persisted cognitive cell needed by
    # the established aggregate view.  No callable or source is reanalyzed.
    for row in view.stream_callables():
        if not isinstance(row, Mapping):
            continue
        subject_key = row.get("subject_key")
        if not isinstance(subject_key, str):
            continue
        repository_key = (subject_key, None)
        if repository_key in groups:
            groups[repository_key].append(
                {"cognitive_complexity": row.get("cognitive_complexity")}
            )
        language = _canonical_language(row.get("detected_language"))
        language_key = (subject_key, language)
        if language is not None and language_key in groups:
            groups[language_key].append(
                {"cognitive_complexity": row.get("cognitive_complexity")}
            )
    return True, groups


def extract_persisted_observations(
    view: PersistedRunView,
    targets: Iterable[tuple[str, str | None, RatchetMetricDefinition]],
    contract_versions: Mapping[str, Mapping[str, str]],
) -> tuple[CurrentMetricObservation, ...]:
    """Project exact requested coordinates from authoritative persisted views."""

    targets = tuple(targets)
    repositories = _repository_index(view.repositories)
    cognitive_targets = {
        (subject_key, language)
        for subject_key, language, definition in targets
        if definition.family is MetricFamily.COGNITIVE_COMPLEXITY
    }
    ledger_present, cognitive_rows = _cognitive_values(
        view, cognitive_targets
    ) if cognitive_targets else (False, {})

    output: list[CurrentMetricObservation] = []
    for subject_key, language, definition in targets:
        current_contracts = contract_versions.get(subject_key)
        if current_contracts is None:
            raise ObservationExtractionError(
                "missing_subject_contracts", subject_key
            )
        contract_version = current_contracts.get(definition.metric_contract)
        repository = repositories.get(subject_key)
        if repository is None:
            output.append(
                _missing_observation(
                    subject_key=subject_key,
                    language=language,
                    definition=definition,
                    metric_contract_version=contract_version,
                )
            )
            continue

        metrics = repository.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        value_field = _value_field(definition, language)

        if definition.family is MetricFamily.CORE:
            raw_values = (
                metrics.get("aggregate")
                if definition.scope is RatchetScope.REPOSITORY
                else _language_map(
                    metrics.get("by_language"),
                    subject_key=subject_key,
                    source="metrics.by_language",
                ).get(str(language))
            )
            values = raw_values if isinstance(raw_values, Mapping) else None
            if values is None:
                # BR2 already admitted this exact repository/language unit.
                # Its missing core aggregate is therefore a missing current
                # observation, not evidence that the paired unit vanished.
                completeness = ObservationCompleteness.MISSING
                status = None
            else:
                status = values.get(definition.status_field)
                completeness = _STATUS_COMPLETENESS.get(
                    status, ObservationCompleteness.UNAVAILABLE
                )
            output.append(
                _observation(
                    subject_key=subject_key,
                    language=language,
                    definition=definition,
                    metric_contract_version=contract_version,
                    values=values,
                    completeness=completeness,
                    status_value=status,
                    value_field=value_field,
                )
            )
            continue

        block = complexity_view.repository_block(repository)
        if definition.family is MetricFamily.STRUCTURAL_COMPLEXITY:
            if block is None:
                values = None
                status = None
                completeness = ObservationCompleteness.ABSENT
            else:
                status = block.get("status")
                completeness = _STATUS_COMPLETENESS.get(
                    status, ObservationCompleteness.UNAVAILABLE
                )
                if definition.scope is RatchetScope.REPOSITORY:
                    raw_values = block.get("aggregate")
                else:
                    raw_values = _language_map(
                        block.get("by_language"),
                        subject_key=subject_key,
                        source="metrics.complexity.by_language",
                    ).get(str(language))
                values = raw_values if isinstance(raw_values, Mapping) else None
                if values is None and completeness in {
                    ObservationCompleteness.COMPLETE,
                    ObservationCompleteness.PARTIAL,
                }:
                    completeness = ObservationCompleteness.NOT_APPLICABLE
            output.append(
                _observation(
                    subject_key=subject_key,
                    language=language,
                    definition=definition,
                    metric_contract_version=contract_version,
                    values=values,
                    completeness=completeness,
                    status_value=status,
                    value_field=value_field,
                )
            )
            continue

        cognitive_state = complexity_view.cognitive_state_of(repository)
        completeness = _COGNITIVE_COMPLETENESS.get(
            cognitive_state, ObservationCompleteness.UNAVAILABLE
        )
        evaluable = completeness in {
            ObservationCompleteness.COMPLETE,
            ObservationCompleteness.PARTIAL,
        }
        rows = cognitive_rows[(subject_key, language)]
        if not ledger_present:
            if evaluable:
                completeness = ObservationCompleteness.UNAVAILABLE
            aggregate = None
        elif not rows:
            if evaluable:
                completeness = ObservationCompleteness.NOT_APPLICABLE
            aggregate = None
        else:
            aggregate = complexity_view.cognitive_aggregate(rows)
        output.append(
            _observation(
                subject_key=subject_key,
                language=language,
                definition=definition,
                metric_contract_version=contract_version,
                values=aggregate,
                completeness=completeness,
                status_value=cognitive_state,
                value_field=value_field,
            )
        )

    return tuple(sorted(output, key=lambda item: item.coordinate))


def extract_current_observations(
    view: PersistedRunView,
    admitted: AdmittedBaselineSubjects,
) -> tuple[CurrentMetricObservation, ...]:
    """Project exactly the baseline's V1 coordinates from an admitted run view."""

    targets = _requested_targets(admitted)
    versions = {
        pair.subject_key: {
            binding.name: binding.version
            for binding in pair.current.metric_contracts
        }
        for pair in admitted.pairs
    }
    return extract_persisted_observations(view, targets, versions)


__all__ = [
    "CurrentMetricObservation",
    "MetricFamily",
    "ObservationCompleteness",
    "ObservationExtractionError",
    "PersistedRunView",
    "RatchetMetricDefinition",
    "V1_METRICS",
    "V1_METRICS_BY_ID",
    "extract_current_observations",
    "extract_persisted_observations",
    "require_v1_metric",
]
