"""BR1 gates for the pure Baseline/Ratchet V1 foundation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from modules.ratchet import (
    BaselineObservation,
    ComparisonDecision,
    CurrentObservation,
    MetricContractBinding,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetComparisonError,
    RatchetContractError,
    RatchetDirection,
    RatchetRule,
    RatchetScope,
    SourceRunBinding,
    canonical_bytes,
    compare_observations,
    parse_baseline_json,
)
from tests.ratchet_contract_fixtures import ratchet_baseline, source_binding


SCOPE_HASH_A = "sha256:" + ("1" * 64)
SCOPE_HASH_Z = "sha256:" + ("2" * 64)
COMMIT_A = "a" * 40
COMMIT_Z = "b" * 40


def _source() -> SourceRunBinding:
    return source_binding(run_id="run-123")


def _subject(
    key: str,
    commit: str,
    scope_hash: str,
    *contracts: str,
) -> PortableSubjectBinding:
    return PortableSubjectBinding(
        subject_key=key,
        subject_key_basis="remote_locator",
        analyzed_commit_sha=commit,
        artifact_schema_version="1.11.0",
        metric_contracts=tuple(
            MetricContractBinding(name=name, version="1.0.0")
            for name in contracts
        ),
        analysis_scope_hash=scope_hash,
        analysis_scope_hash_version="2.0.0",
    )


def _max_cc_rule(
    *,
    direction: RatchetDirection = RatchetDirection.INCREASE_IS_WORSE,
    tolerance: int | float = 0,
) -> RatchetRule:
    return RatchetRule(
        rule_id="ratchet.max_cc",
        metric="repository.cyclomatic_complexity_max",
        metric_contract="complexity",
        scope=RatchetScope.REPOSITORY,
        direction=direction,
        max_regression=tolerance,
    )


def _document(*, reverse: bool = False) -> RatchetBaseline:
    subject_a = _subject(
        "github.com/acme/alpha",
        COMMIT_A,
        SCOPE_HASH_A,
        *("metrics", "complexity") if not reverse else ("complexity", "metrics"),
    )
    subject_z = _subject(
        "github.com/acme/zeta", COMMIT_Z, SCOPE_HASH_Z, "complexity"
    )
    max_cc = _max_cc_rule()
    python_loc = RatchetRule(
        rule_id="ratchet.python_loc",
        metric="language.lines_of_code",
        metric_contract="metrics",
        scope=RatchetScope.LANGUAGE,
        direction=RatchetDirection.INCREASE_IS_WORSE,
        max_regression=10,
        severity="warning",
    )
    observations = (
        BaselineObservation(
            rule_id=max_cc.rule_id,
            subject_key=subject_z.subject_key,
            baseline_value=21,
        ),
        BaselineObservation(
            rule_id=python_loc.rule_id,
            subject_key=subject_a.subject_key,
            language="python",
            baseline_value=100,
        ),
        BaselineObservation(
            rule_id=max_cc.rule_id,
            subject_key=subject_a.subject_key,
            baseline_value=13,
        ),
    )
    subjects = (subject_z, subject_a)
    rules = (python_loc, max_cc)
    if reverse:
        subjects = tuple(reversed(subjects))
        rules = tuple(reversed(rules))
        observations = tuple(reversed(observations))
    return ratchet_baseline(
        source_run=_source(),
        subjects=subjects,
        rules=rules,
        observations=observations,
    )


def _observation(value: int | float) -> BaselineObservation:
    return BaselineObservation(
        rule_id="ratchet.max_cc",
        subject_key="github.com/acme/alpha",
        baseline_value=value,
    )


def _current(value: int | float) -> CurrentObservation:
    return CurrentObservation(
        subject_key="github.com/acme/alpha",
        metric="repository.cyclomatic_complexity_max",
        scope=RatchetScope.REPOSITORY,
        current_value=value,
    )


def test_canonical_bytes_are_order_independent_stable_and_round_trip() -> None:
    first = canonical_bytes(_document())
    second = canonical_bytes(_document(reverse=True))

    assert first == second
    assert canonical_bytes(parse_baseline_json(first)) == first
    assert hashlib.sha256(first).hexdigest() == (
        "4f1b7981b05fffd4faf60d37b8cd3bcd2e15c432800a74c448104e9d96799d85"
    )
    assert not first.endswith(b"\n")
    assert b"timestamp" not in first
    assert b"history" not in first
    assert b"waiver" not in first
    assert b"D:\\" not in first


def test_valid_v1_baseline_is_accepted_by_contract_parser() -> None:
    parsed = parse_baseline_json(canonical_bytes(_document()))

    assert parsed == _document()


@pytest.mark.parametrize(
    "metric",
    (
        "repository.hotspot_density",
        "repository.duplication_group_count",
        "repository.changed_lines",
        "repository.maintainability_index",
    ),
)
def test_contract_parser_rejects_unsupported_v1_metric(metric: str) -> None:
    raw = _document().to_dict()
    raw["rules"][0]["metric"] = metric
    raw["rules"][0]["scope"] = "repository"

    with pytest.raises(RatchetContractError, match="not supported"):
        parse_baseline_json(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )


@pytest.mark.parametrize(
    "scope, metric",
    (
        ("callable", "callable.cyclomatic_complexity"),
        ("hotspot_file", "hotspot_file.churn"),
        ("duplication_group", "duplication_group.duplicated_lines"),
        ("changed_code", "changed_code.lines_of_code"),
    ),
)
def test_contract_parser_rejects_unsupported_v1_scope(
    scope: str, metric: str
) -> None:
    raw = _document().to_dict()
    raw["rules"][0]["scope"] = scope
    raw["rules"][0]["metric"] = metric

    with pytest.raises(RatchetContractError, match="rule.scope"):
        parse_baseline_json(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )


@pytest.mark.parametrize(
    "field", ["captured_at", "waivers", "history", "trends", "signatures", "audit"]
)
def test_contract_rejects_unknown_deferred_fields(field: str) -> None:
    raw = _document().to_dict()
    raw[field] = []
    with pytest.raises(RatchetContractError, match=rf"unknown keys: {field}"):
        RatchetBaseline.from_dict(raw)


def test_contract_rejects_local_absolute_subject_identity() -> None:
    with pytest.raises(RatchetContractError, match="local absolute path"):
        _subject("C:\\work\\repo", COMMIT_A, SCOPE_HASH_A, "complexity")


@pytest.mark.parametrize("value", [None, True, "1", float("nan"), float("inf")])
def test_contract_rejects_invalid_baseline_values(value: object) -> None:
    with pytest.raises(RatchetContractError):
        BaselineObservation(
            rule_id="ratchet.max_cc",
            subject_key="github.com/acme/alpha",
            baseline_value=value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "value", [-1, True, float("nan"), float("inf"), "0"]
)
def test_contract_rejects_invalid_tolerances(value: object) -> None:
    with pytest.raises(RatchetContractError):
        _max_cc_rule(tolerance=value)  # type: ignore[arg-type]


def test_contract_rejects_missing_baseline_observations() -> None:
    document = _document()
    with pytest.raises(RatchetContractError, match="coordinate_manifest is incomplete"):
        replace(
            document,
            rules=document.rules
            + (
                RatchetRule(
                    rule_id="ratchet.unobserved",
                    metric="repository.cyclomatic_complexity_mean",
                    metric_contract="complexity",
                    scope="repository",
                    direction="increase_is_worse",
                    max_regression=0,
                ),
            ),
        )


def test_comparator_rejects_missing_observations() -> None:
    rule = _max_cc_rule()
    with pytest.raises(RatchetComparisonError, match="baseline observation is required"):
        compare_observations(None, _current(1), rule)  # type: ignore[arg-type]
    with pytest.raises(RatchetComparisonError, match="current observation is required"):
        compare_observations(_observation(1), None, rule)  # type: ignore[arg-type]


def test_increase_is_worse_direction_reports_signed_delta_and_regression() -> None:
    result = compare_observations(_observation(10), _current(15), _max_cc_rule())

    assert result.baseline_value == 10
    assert result.current_value == 15
    assert result.delta == 5
    assert result.direction is RatchetDirection.INCREASE_IS_WORSE
    assert result.regression_amount == 5
    assert result.decision is ComparisonDecision.REGRESSION


def test_decrease_is_worse_direction_inverts_regression_amount() -> None:
    rule = _max_cc_rule(direction=RatchetDirection.DECREASE_IS_WORSE)
    result = compare_observations(_observation(10), _current(5), rule)

    assert result.delta == -5
    assert result.regression_amount == 5
    assert result.decision is ComparisonDecision.REGRESSION


def test_regression_equal_to_tolerance_is_tolerated() -> None:
    result = compare_observations(
        _observation(10), _current(13), _max_cc_rule(tolerance=3)
    )

    assert result.delta == 3
    assert result.regression_amount == 3
    assert result.decision is ComparisonDecision.TOLERATED


def test_regression_beyond_tolerance_is_reported() -> None:
    result = compare_observations(
        _observation(10), _current(14), _max_cc_rule(tolerance=3)
    )

    assert result.delta == 4
    assert result.regression_amount == 4
    assert result.decision is ComparisonDecision.REGRESSION


def test_zero_baseline_uses_absolute_delta_without_division() -> None:
    unchanged = compare_observations(_observation(0), _current(0), _max_cc_rule())
    regression = compare_observations(_observation(0), _current(1), _max_cc_rule())

    assert unchanged.delta == 0
    assert unchanged.decision is ComparisonDecision.UNCHANGED
    assert regression.delta == 1
    assert regression.regression_amount == 1
    assert regression.decision is ComparisonDecision.REGRESSION


def test_improvement_has_signed_delta_and_zero_regression_amount() -> None:
    increase_rule = _max_cc_rule(direction=RatchetDirection.INCREASE_IS_WORSE)
    decrease_rule = _max_cc_rule(direction=RatchetDirection.DECREASE_IS_WORSE)

    increased_quality = compare_observations(
        _observation(10), _current(7), increase_rule
    )
    decreased_quality = compare_observations(
        _observation(10), _current(12), decrease_rule
    )

    assert increased_quality.delta == -3
    assert increased_quality.regression_amount == 0
    assert increased_quality.decision is ComparisonDecision.IMPROVED
    assert decreased_quality.delta == 2
    assert decreased_quality.regression_amount == 0
    assert decreased_quality.decision is ComparisonDecision.IMPROVED


def test_comparator_requires_exact_identity_metric_and_scope() -> None:
    rule = _max_cc_rule()
    wrong_subject = replace(_current(11), subject_key="github.com/acme/other")
    wrong_metric = replace(_current(11), metric="repository.lines_of_code")

    with pytest.raises(RatchetComparisonError, match="subject"):
        compare_observations(_observation(10), wrong_subject, rule)
    with pytest.raises(RatchetComparisonError, match="metric/scope"):
        compare_observations(_observation(10), wrong_metric, rule)
