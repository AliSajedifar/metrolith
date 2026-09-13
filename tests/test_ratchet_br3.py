"""BR3 gates for persisted observation extraction and comparison orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

import pytest

from modules.ratchet import (
    AdmittedBaselineArtifact,
    AdmittedBaselineSubjects,
    BaselineArtifactOrigin,
    BaselineObservation,
    ComparisonDecision,
    ComparisonStatus,
    CurrentSubject,
    MetricContractBinding,
    ObservationCompleteness,
    ObservationExtractionError,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetDirection,
    RatchetRule,
    RatchetScope,
    SourceRunBinding,
    extract_and_compare,
    extract_current_observations,
    orchestrate_comparisons,
    pair_subjects,
    require_v1_metric,
    V1_METRICS,
)
from tests.ratchet_contract_fixtures import ratchet_baseline, source_binding


SUBJECT_A = "github.com/acme/alpha"
SUBJECT_Z = "github.com/acme/zeta"
BASE_COMMIT = "a" * 40
CURRENT_COMMIT = "b" * 40
SCOPE_HASH = "sha256:" + ("1" * 64)
CURRENT_SCOPE_HASH = "sha256:" + ("2" * 64)


@dataclass
class FakeRunView:
    repositories: tuple[Mapping[str, Any], ...]
    callable_rows: tuple[Mapping[str, Any], ...] = ()
    has_callable_artifact: bool = False
    stream_count: int = 0
    analysis_count: int = 0

    def stream_callables(self) -> Iterable[Mapping[str, Any]]:
        self.stream_count += 1
        yield from self.callable_rows

    def analyze(self) -> None:  # pragma: no cover - a forbidden escape hatch
        self.analysis_count += 1
        raise AssertionError("observation extraction must not analyze source")


def _rule(
    metric: str,
    *,
    rule_id: str = "ratchet.metric",
    tolerance: int | float = 0,
    direction: RatchetDirection = RatchetDirection.INCREASE_IS_WORSE,
) -> RatchetRule:
    scope = (
        RatchetScope.LANGUAGE
        if metric.startswith("language.")
        else RatchetScope.REPOSITORY
    )
    contract = (
        "metrics"
        if metric.rsplit(".", 1)[-1]
        in {
            "lines_of_code",
            "source_files",
            "classes_structs",
            "methods_functions",
        }
        else "complexity"
    )
    return RatchetRule(
        rule_id=rule_id,
        metric=metric,
        metric_contract=contract,
        scope=scope,
        direction=direction,
        max_regression=tolerance,
    )


def _subject(
    key: str, contracts: Mapping[str, str]
) -> PortableSubjectBinding:
    return PortableSubjectBinding(
        subject_key=key,
        subject_key_basis="remote_locator",
        analyzed_commit_sha=BASE_COMMIT,
        artifact_schema_version="1.11.0",
        metric_contracts=tuple(
            MetricContractBinding(name, version)
            for name, version in contracts.items()
        ),
        analysis_scope_hash=SCOPE_HASH,
        analysis_scope_hash_version="2.0.0",
    )


def _admitted(
    rules: tuple[RatchetRule, ...],
    observations: tuple[BaselineObservation, ...],
    *,
    current_contract_versions: Mapping[str, Mapping[str, str]] | None = None,
) -> AdmittedBaselineSubjects:
    subject_keys = sorted({item.subject_key for item in observations})
    used_contracts: dict[str, dict[str, str]] = {
        key: {} for key in subject_keys
    }
    rule_by_id = {rule.rule_id: rule for rule in rules}
    for observation in observations:
        contract = rule_by_id[observation.rule_id].metric_contract
        used_contracts[observation.subject_key][contract] = (
            "3.0.0" if contract == "metrics" else "2.0.0"
        )
    subjects = tuple(
        _subject(key, used_contracts[key]) for key in subject_keys
    )
    baseline = ratchet_baseline(
        source_run=source_binding(),
        subjects=subjects,
        rules=rules,
        observations=observations,
    )
    languages: dict[str, set[str]] = {key: set() for key in subject_keys}
    for observation in observations:
        if observation.language is not None:
            languages[observation.subject_key].add(observation.language)
    current = tuple(
        CurrentSubject(
            subject_key=subject.subject_key,
            subject_key_basis="remote_locator",
            analyzed_commit_sha=CURRENT_COMMIT,
            artifact_schema_version=subject.artifact_schema_version,
            metric_contracts=tuple(
                MetricContractBinding(name, version)
                for name, version in (
                    (current_contract_versions or {}).get(subject.subject_key)
                    or subject.contract_versions
                ).items()
            ),
            analysis_scope_hash=CURRENT_SCOPE_HASH,
            analysis_scope_hash_version=subject.analysis_scope_hash_version,
            languages=tuple(sorted(languages[subject.subject_key])),
            admitted=True,
        )
        for subject in subjects
    )
    artifact = AdmittedBaselineArtifact(
        baseline=baseline,
        verified_sha256="d" * 64,
        origin=BaselineArtifactOrigin.PROTECTED_BASE_REVISION,
    )
    return AdmittedBaselineSubjects(
        artifact=artifact,
        pairs=pair_subjects(baseline, current),
    )


def _single(
    metric: str,
    baseline_value: int | float,
    *,
    language: str | None = None,
    tolerance: int | float = 0,
) -> AdmittedBaselineSubjects:
    rule = _rule(metric, tolerance=tolerance)
    return _admitted(
        (rule,),
        (
            BaselineObservation(
                rule_id=rule.rule_id,
                subject_key=SUBJECT_A,
                language=language,
                baseline_value=baseline_value,
            ),
        ),
    )


def _repository(
    *,
    key: str = SUBJECT_A,
    loc: Any = 100,
    loc_status: str = "complete",
    language_loc: Any = 50,
    structural_max: Any = 10,
    complexity_status: str = "complete",
    cognitive_state: str = "measured",
) -> dict[str, Any]:
    return {
        "subject_key": key,
        "repository_url": f"https://{key}.git",
        "metrics": {
            "aggregate": {
                "lines_of_code": loc,
                "loc_status": loc_status,
            },
            "by_language": {
                "Python": {
                    "lines_of_code": language_loc,
                    "loc_status": loc_status,
                }
            },
            "complexity": {
                "complexity_contract_version": "2.0.0",
                "status": complexity_status,
                "cognitive_measurement_state": cognitive_state,
                "aggregate": {
                    "cyclomatic_complexity_max": structural_max,
                },
                "by_language": {
                    "PYTHON": {
                        "cyclomatic_complexity_max": structural_max,
                    }
                },
            },
        },
    }


def test_extracts_repository_core_metric_from_persisted_result() -> None:
    admitted = _single("repository.lines_of_code", 90)
    view = FakeRunView((_repository(loc=103),))

    observations = extract_current_observations(view, admitted)

    assert len(observations) == 1
    assert observations[0].value == 103
    assert observations[0].completeness is ObservationCompleteness.COMPLETE
    assert observations[0].metric_contract_version == "3.0.0"
    assert observations[0].value_field == "metrics.aggregate.lines_of_code"
    assert view.stream_count == 0
    assert view.analysis_count == 0


def test_extracts_exact_canonical_language_structural_metric() -> None:
    admitted = _single(
        "language.cyclomatic_complexity_max", 8, language="python"
    )

    observation = extract_current_observations(
        FakeRunView((_repository(structural_max=12),)), admitted
    )[0]

    assert observation.language == "python"
    assert observation.value == 12
    assert observation.completeness is ObservationCompleteness.COMPLETE
    assert observation.status_value == "complete"


def test_cognitive_projection_uses_persisted_ledger_once() -> None:
    admitted = _single("repository.cognitive_complexity_max", 2)
    view = FakeRunView(
        (_repository(),),
        callable_rows=(
            {
                "subject_key": SUBJECT_A,
                "detected_language": "Python",
                "cognitive_complexity": 4,
            },
            {
                "subject_key": SUBJECT_A,
                "detected_language": "python",
                "cognitive_complexity": 7,
            },
        ),
        has_callable_artifact=True,
    )

    observation = extract_current_observations(view, admitted)[0]

    assert observation.value == 7
    assert observation.completeness is ObservationCompleteness.COMPLETE
    assert view.stream_count == 1
    assert view.analysis_count == 0


@pytest.mark.parametrize(
    ("metric", "scope", "code"),
    [
        ("callable.cyclomatic_complexity", "callable", "unsupported_scope"),
        ("repository.hotspot_file_count", "repository", "unsupported_metric"),
        ("duplication_group.occurrence_count", "duplication_group", "unsupported_scope"),
        ("changed-code.lines_of_code", "repository", "unsupported_metric"),
    ],
)
def test_rejects_every_unsupported_scope_and_metric(
    metric: str, scope: str, code: str
) -> None:
    with pytest.raises(ObservationExtractionError) as captured:
        require_v1_metric(metric, scope)
    assert captured.value.code == code


def test_v1_allowlist_is_only_repository_and_language_supported_families() -> None:
    assert len(V1_METRICS) == 40
    assert {item.scope for item in V1_METRICS} == {
        RatchetScope.REPOSITORY,
        RatchetScope.LANGUAGE,
    }
    assert {item.metric_contract for item in V1_METRICS} == {
        "metrics",
        "complexity",
    }


def test_missing_current_subject_is_explicit_and_never_zero() -> None:
    admitted = _single("repository.lines_of_code", 10)

    result = extract_and_compare(FakeRunView(()), admitted)[0]

    assert result.status is ComparisonStatus.MISSING
    assert result.completeness is ObservationCompleteness.MISSING
    assert result.comparison is None


def test_missing_current_metric_field_is_explicit_and_never_zero() -> None:
    admitted = _single("repository.lines_of_code", 10)
    repository = _repository()
    del repository["metrics"]["aggregate"]["lines_of_code"]

    result = extract_and_compare(FakeRunView((repository,)), admitted)[0]

    assert result.status is ComparisonStatus.MISSING
    assert result.completeness is ObservationCompleteness.MISSING
    assert result.comparison is None


def test_unavailable_current_value_is_not_compared() -> None:
    admitted = _single("repository.lines_of_code", 10)

    result = extract_and_compare(
        FakeRunView((_repository(loc=None, loc_status="failed"),)), admitted
    )[0]

    assert result.status is ComparisonStatus.UNAVAILABLE
    assert result.completeness is ObservationCompleteness.UNAVAILABLE
    assert result.comparison is None


def test_partial_current_metric_preserves_state_and_is_not_compared() -> None:
    admitted = _single("repository.lines_of_code", 10)

    result = extract_and_compare(
        FakeRunView((_repository(loc=12, loc_status="partial"),)), admitted
    )[0]

    assert result.status is ComparisonStatus.PARTIAL
    assert result.completeness is ObservationCompleteness.PARTIAL
    assert result.comparison is None


def test_orchestration_reports_improvement() -> None:
    admitted = _single("repository.cyclomatic_complexity_max", 10)

    result = extract_and_compare(
        FakeRunView((_repository(structural_max=7),)), admitted
    )[0]

    assert result.status is ComparisonStatus.COMPARED
    assert result.comparison is not None
    assert result.comparison.delta == -3
    assert result.comparison.decision is ComparisonDecision.IMPROVED


def test_orchestration_reports_regression() -> None:
    admitted = _single("repository.cyclomatic_complexity_max", 10)

    result = extract_and_compare(
        FakeRunView((_repository(structural_max=14),)), admitted
    )[0]

    assert result.status is ComparisonStatus.COMPARED
    assert result.comparison is not None
    assert result.comparison.delta == 4
    assert result.comparison.decision is ComparisonDecision.REGRESSION


def test_orchestration_applies_frozen_tolerance() -> None:
    admitted = _single(
        "repository.cyclomatic_complexity_max", 10, tolerance=3
    )

    result = extract_and_compare(
        FakeRunView((_repository(structural_max=13),)), admitted
    )[0]

    assert result.comparison is not None
    assert result.comparison.regression_amount == 3
    assert result.comparison.decision is ComparisonDecision.TOLERATED


def test_incompatible_current_metric_contract_is_explicit() -> None:
    rule = _rule("repository.cyclomatic_complexity_max")
    observation = BaselineObservation(
        rule_id=rule.rule_id,
        subject_key=SUBJECT_A,
        baseline_value=10,
    )
    admitted = _admitted(
        (rule,),
        (observation,),
        current_contract_versions={SUBJECT_A: {"complexity": "2.1.0"}},
    )

    result = extract_and_compare(
        FakeRunView((_repository(structural_max=11),)), admitted
    )[0]

    assert result.status is ComparisonStatus.INCOMPATIBLE_CONTRACT
    assert result.baseline_contract_version == "2.0.0"
    assert result.current_contract_version == "2.1.0"
    assert result.comparison is None


def test_output_order_is_stable_by_subject_language_and_rule() -> None:
    repo_rule = _rule(
        "repository.lines_of_code", rule_id="ratchet.z_repository"
    )
    lang_rule_a = _rule(
        "language.lines_of_code", rule_id="ratchet.a_language"
    )
    lang_rule_z = _rule(
        "language.cyclomatic_complexity_max",
        rule_id="ratchet.z_language",
    )
    observations = (
        BaselineObservation(
            repo_rule.rule_id, SUBJECT_Z, baseline_value=100
        ),
        BaselineObservation(
            lang_rule_z.rule_id, SUBJECT_A, baseline_value=8, language="python"
        ),
        BaselineObservation(
            lang_rule_a.rule_id, SUBJECT_A, baseline_value=40, language="python"
        ),
    )
    admitted = _admitted(
        (repo_rule, lang_rule_z, lang_rule_a), observations
    )
    first_view = FakeRunView(
        (
            _repository(key=SUBJECT_Z, loc=110),
            _repository(key=SUBJECT_A, language_loc=45, structural_max=7),
        )
    )
    second_view = FakeRunView(tuple(reversed(first_view.repositories)))

    first_observations = extract_current_observations(first_view, admitted)
    second_observations = extract_current_observations(second_view, admitted)
    first = orchestrate_comparisons(admitted, reversed(first_observations))
    second = orchestrate_comparisons(admitted, second_observations)

    assert [item.to_dict() for item in first] == [
        item.to_dict() for item in second
    ]
    assert [item.sort_key for item in first] == [
        (SUBJECT_A, "python", "ratchet.a_language"),
        (SUBJECT_A, "python", "ratchet.z_language"),
        (SUBJECT_Z, "", "ratchet.z_repository"),
    ]


def test_missing_observation_object_is_not_silently_skipped() -> None:
    admitted = _single("repository.lines_of_code", 10)

    result = orchestrate_comparisons(admitted, ())[0]

    assert result.status is ComparisonStatus.MISSING
    assert result.comparison is None


def test_language_map_rejects_duplicate_normalized_keys() -> None:
    admitted = _single("language.lines_of_code", 10, language="python")
    repository = _repository()
    repository["metrics"]["by_language"]["python"] = {
        "lines_of_code": 1,
        "loc_status": "complete",
    }

    with pytest.raises(ObservationExtractionError) as captured:
        extract_current_observations(FakeRunView((repository,)), admitted)

    assert captured.value.code == "duplicate_normalized_language"


def test_current_observation_input_cannot_change_frozen_identity() -> None:
    admitted = _single("repository.lines_of_code", 10)
    extracted = extract_current_observations(
        FakeRunView((_repository(loc=11),)), admitted
    )[0]

    with pytest.raises(ValueError, match="unexpected_current_observation"):
        orchestrate_comparisons(
            admitted, (replace(extracted, subject_key=SUBJECT_Z),)
        )
