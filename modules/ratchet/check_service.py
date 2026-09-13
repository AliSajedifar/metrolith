"""Ratchet V1 integration boundary for the existing ``metrolith check`` flow.

The service composes BR2 admission and BR3 observation/comparison, projects
only regressions and incomparable current observations into the existing
canonical Finding model, and merges a bounded Ratchet summary into Check Result
1.3.  It does not load policy documents, open run directories, capture or
promote baselines, render SARIF, or expose a CLI/Action input.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules import complexity_view
from modules.policy import findings as finding_module
from modules.policy import metrics as policy_metric_module
from modules.policy import rules as policy_rule_module
from modules.ratchet.admission import (
    AdmittedBaselineArtifact,
    AdmittedBaselineSubjects,
    BaselineAdmissionError,
    BaselineTrustContext,
    SourceRunEvidence,
    admit_baseline,
    require_revision_compatibility,
)
from modules.ratchet.comparison import ComparisonDecision
from modules.ratchet.contract import (
    MetricContractBinding,
    RatchetContractError,
)
from modules.ratchet.observations import (
    CurrentMetricObservation,
    ObservationCompleteness,
    ObservationExtractionError,
    extract_current_observations,
)
from modules.ratchet.semantics import (
    MeasurementSemanticsError,
    SemanticsCompatibilityError,
    producer_from_manifest,
    require_producer_compatibility,
    require_semantics_compatibility,
    semantics_from_manifest,
)
from modules.ratchet.orchestration import (
    ComparisonOrchestrationError,
    ComparisonStatus,
    OrchestratedComparison,
    orchestrate_comparisons,
)
from modules.ratchet.pairing import (
    ContractCompatibilityError,
    CurrentSubject,
    SubjectPairingError,
    pair_subjects,
    require_contract_compatibility,
)
from modules.revision_source import ResolvedRevisionPair, resolve_revision_pair


RATCHET_CHECK_RESULT_FORMAT_VERSION = "1.4.0"
FAILURE_RATCHET_ADMISSION = "ratchet_admission_failed"
FAILURE_RATCHET_EVALUATION = "ratchet_evaluation_failed"

RATCHET_NOT_CONFIGURED = "not_configured"
RATCHET_ADMISSION_FAILED = "admission_failed"
RATCHET_EVALUATION_FAILED = "evaluation_failed"
RATCHET_EVALUATED = "evaluated"
RATCHET_EVALUATED_WITH_NOT_EVALUABLE = "evaluated_with_not_evaluable"

ADMISSION_NOT_REQUESTED = "not_requested"
ADMISSION_ADMITTED = "admitted"
ADMISSION_FAILED = "failed"

RevisionResolver = Callable[..., ResolvedRevisionPair]


@dataclass(frozen=True, slots=True)
class RatchetCheckRequest:
    """Trusted inputs supplied programmatically outside the candidate run.

    BR4 deliberately adds no path resolution, CLI option, or Action input.  A
    later wrapper may construct this value only after obtaining baseline bytes
    and their digest/evidence from a protected source.
    """

    baseline_payload: str | bytes
    trust: BaselineTrustContext
    source_run: SourceRunEvidence
    revision_sources: Mapping[str, str | Path]
    revision_timeout: int = 300
    revision_resolver: RevisionResolver = resolve_revision_pair


@dataclass(frozen=True, slots=True)
class RatchetCheckEvaluation:
    admitted: AdmittedBaselineSubjects
    comparisons: tuple[OrchestratedComparison, ...]
    findings: tuple[finding_module.Finding, ...]
    rule_summaries: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


class RatchetCheckServiceError(RuntimeError):
    """A typed integration failure that must become check exit code 2."""

    def __init__(
        self,
        phase: str,
        code: str,
        detail: str | None = None,
    ) -> None:
        self.phase = phase
        self.code = code
        self.detail = detail
        text = f"{phase}: {code}"
        super().__init__(text if detail is None else f"{text}: {detail}")


def _canonical_language(raw: Any, *, subject_key: str) -> str:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        raise RatchetCheckServiceError(
            "admission", "current_language_identity_invalid", subject_key
        )
    return unicodedata.normalize("NFC", raw).casefold()


def _language_population(
    repository: Mapping[str, Any], *, subject_key: str
) -> tuple[str, ...]:
    metrics = repository.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    complexity = complexity_view.repository_block(repository)
    sources = (
        ("metrics.by_language", metrics.get("by_language")),
        (
            "metrics.complexity.by_language",
            complexity.get("by_language") if complexity else None,
        ),
    )
    population: set[str] = set()
    for source_name, source in sources:
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise RatchetCheckServiceError(
                "admission",
                "current_language_mapping_invalid",
                f"{subject_key}: {source_name}",
            )
        within_source: set[str] = set()
        for raw_language in source:
            language = _canonical_language(raw_language, subject_key=subject_key)
            if language in within_source:
                raise RatchetCheckServiceError(
                    "admission",
                    "current_language_identity_duplicate",
                    f"{subject_key}: {language}",
                )
            within_source.add(language)
        population.update(within_source)
    return tuple(sorted(population))


def _contract_version(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def _baseline_languages(
    artifact: AdmittedBaselineArtifact,
) -> dict[str, set[str]]:
    baseline = artifact.baseline
    rule_by_id = {rule.rule_id: rule for rule in baseline.rules}
    result: dict[str, set[str]] = {
        subject.subject_key: set() for subject in baseline.subjects
    }
    for observation in baseline.observations:
        if rule_by_id[observation.rule_id].scope.value == "language":
            result[observation.subject_key].add(str(observation.language))
    return result


def _current_subjects(
    view: Any,
    applicable_languages: Mapping[str, set[str]],
) -> tuple[CurrentSubject, ...]:
    manifest = view.manifest
    repositories = tuple(view.repositories)
    subjects: list[CurrentSubject] = []
    for index, repository in enumerate(repositories):
        if not isinstance(repository, Mapping):
            raise RatchetCheckServiceError(
                "admission", "current_repository_invalid", str(index)
            )
        key = repository.get("subject_key")
        if not isinstance(key, str) or not key or key.strip() != key:
            raise RatchetCheckServiceError(
                "admission", "current_subject_identity_missing", str(index)
            )
        metrics_version = _contract_version(
            repository.get("metric_contract_version")
            or manifest.get("metric_contract_version")
        )
        complexity = complexity_view.repository_block(repository)
        complexity_version = _contract_version(
            (complexity or {}).get("complexity_contract_version")
            or repository.get("complexity_contract_version")
            or manifest.get("complexity_contract_version")
        )
        contracts: list[MetricContractBinding] = []
        if metrics_version is not None:
            contracts.append(MetricContractBinding("metrics", metrics_version))
        if complexity_version is not None:
            contracts.append(
                MetricContractBinding("complexity", complexity_version)
            )
        acquisition = repository.get("acquisition")
        acquisition = acquisition if isinstance(acquisition, Mapping) else {}
        subjects.append(
            CurrentSubject(
                subject_key=key,
                subject_key_basis=repository.get("subject_key_basis"),
                analyzed_commit_sha=acquisition.get("analyzed_commit_sha"),
                artifact_schema_version=(
                    repository.get("artifact_schema_version")
                    or manifest.get("artifact_schema_version")
                ),
                metric_contracts=tuple(contracts),
                analysis_scope_hash=repository.get("analysis_scope_hash"),
                analysis_scope_hash_version=repository.get(
                    "analysis_scope_hash_version"
                ),
                languages=tuple(
                    sorted(
                        set(_language_population(repository, subject_key=key))
                        & applicable_languages.get(key, set())
                    )
                ),
                admitted=True,
            )
        )
    return tuple(subjects)


def _require_coordinate_manifest_compatibility(
    artifact: AdmittedBaselineArtifact,
    view: Any,
) -> None:
    """Require the current run to expose the same rule coordinate universe."""

    baseline = artifact.baseline
    baseline_keys = {
        coordinate.key for coordinate in baseline.coordinate_manifest
    }
    current_keys: set[tuple[str, str, str]] = set()
    repositories: dict[str, Mapping[str, Any]] = {}
    for repository in view.repositories:
        if not isinstance(repository, Mapping):
            raise RatchetCheckServiceError(
                "admission", "current_repository_invalid"
            )
        key = repository.get("subject_key")
        if not isinstance(key, str) or not key:
            raise RatchetCheckServiceError(
                "admission", "current_subject_identity_missing"
            )
        repositories[key] = repository
    for rule in baseline.rules:
        for subject in baseline.subjects:
            repository = repositories.get(subject.subject_key)
            if repository is None:
                continue
            if rule.scope.value == "repository":
                current_keys.add((rule.rule_id, subject.subject_key, ""))
            else:
                for language in _language_population(
                    repository, subject_key=subject.subject_key
                ):
                    current_keys.add(
                        (rule.rule_id, subject.subject_key, language)
                    )
    if current_keys != baseline_keys:
        missing = sorted(baseline_keys - current_keys)
        unexpected = sorted(current_keys - baseline_keys)
        raise RatchetCheckServiceError(
            "admission",
            "coordinate_manifest_mismatch",
            f"missing={missing!r}, unexpected={unexpected!r}",
        )


def _optional_current_commit(repository: Mapping[str, Any]) -> str | None:
    """Translate established snapshot absence only at the Check boundary."""
    from modules.policy.check import CheckFailed, FAILURE_EVALUATION_ERROR

    acquisition = repository.get("acquisition") or {}
    commit = acquisition.get("analyzed_commit_sha")
    mode = repository.get("source_mode")
    acquired_mode = acquisition.get("acquisition_mode")
    state = repository.get("working_tree_state")
    local_modes = {"local_directory_snapshot", "local_worktree_snapshot", "local_git_revision"}
    inconsistent = mode in local_modes and acquired_mode not in (None, mode)
    if mode not in local_modes | {"remote_git_revision", None}:
        inconsistent = True
    if mode == "local_git_revision" and state not in (None, "committed_revision"):
        inconsistent = True
    if mode == "local_worktree_snapshot" and state not in (None, "clean_worktree", "dirty_worktree"):
        inconsistent = True
    if mode == "local_directory_snapshot":
        absent = (
            state == "not_applicable" and acquired_mode == mode
            and acquisition.get("requested_commit_sha") is None
            and commit in (None, "")
        )
        if absent and not inconsistent:
            return None
        inconsistent = True
    if not inconsistent and isinstance(commit, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        return commit
    if not inconsistent and commit is None and mode not in local_modes | {"remote_git_revision"}:
        # Historical optional identity, never a claim of revision binding.
        return None
    if not inconsistent and mode == "local_worktree_snapshot" and commit in (None, ""):
        evidence = repository.get("local_source_evidence") or {}
        if evidence.get("tracked_file_count") == 0 and state in {"clean_worktree", "dirty_worktree"}:
            return None
    raise CheckFailed(FAILURE_EVALUATION_ERROR, "current commit identity is malformed or inconsistent with source mode")


def _current_identity(view: Any) -> dict[str, Any]:
    subjects: list[dict[str, Any]] = []
    for repository in view.repositories:
        if not isinstance(repository, Mapping):
            continue
        acquisition = repository.get("acquisition")
        acquisition = acquisition if isinstance(acquisition, Mapping) else {}
        subjects.append(
            {
                "subject_key": repository.get("subject_key"),
                "analyzed_commit_sha": _optional_current_commit(repository),
            }
        )
    subjects.sort(
        key=lambda item: (
            str(item.get("subject_key") or ""),
            str(item.get("analyzed_commit_sha") or ""),
        )
    )
    return {"run_id": view.run_id, "subjects": subjects}


def _empty_summary(view: Any | None) -> dict[str, Any]:
    return {
        "configured": False,
        "status": RATCHET_NOT_CONFIGURED,
        "admission_status": ADMISSION_NOT_REQUESTED,
        "baseline": None,
        "current": None if view is None else _current_identity(view),
        "paired_subject_count": 0,
        "evaluated_count": 0,
        "violated_count": 0,
        "not_evaluable_count": 0,
        "error": None,
    }


def _error_summary(
    view: Any | None,
    *,
    phase: str,
    code: str,
    detail: str | None,
) -> dict[str, Any]:
    return {
        "configured": True,
        "status": (
            RATCHET_ADMISSION_FAILED
            if phase == "admission"
            else RATCHET_EVALUATION_FAILED
        ),
        "admission_status": (
            ADMISSION_FAILED if phase == "admission" else ADMISSION_ADMITTED
        ),
        # No identity from unadmitted bytes is represented as trusted baseline
        # identity.  The bounded error carries only its typed code/detail.
        "baseline": None,
        "current": None if view is None else _current_identity(view),
        "paired_subject_count": 0,
        "evaluated_count": 0,
        "violated_count": 0,
        "not_evaluable_count": 0,
        "error": {"code": code, "detail": detail},
    }


def not_configured_summary(view: Any | None = None) -> dict[str, Any]:
    """The required Check Result 1.3 summary when no baseline was requested."""

    return _empty_summary(view)


def admission_failure_summary(
    code: str, detail: str | None = None, *, view: Any | None = None
) -> dict[str, Any]:
    """Bounded configured summary for CLI acquisition failures before a run opens."""

    return _error_summary(
        view,
        phase="admission",
        code=code,
        detail=detail,
    )


def _baseline_identity(admitted: AdmittedBaselineSubjects) -> dict[str, Any]:
    baseline = admitted.artifact.baseline
    return {
        "format": baseline.format,
        "format_version": baseline.format_version,
        "verified_sha256": admitted.artifact.verified_sha256,
        "source_run_id": baseline.source_run.run_id,
        "source_run_manifest_sha256": baseline.source_run.run_manifest_sha256,
        "subjects": [
            {
                "subject_key": pair.subject_key,
                "analyzed_commit_sha": pair.baseline.analyzed_commit_sha,
            }
            for pair in admitted.pairs
        ],
    }


def _successful_summary(
    view: Any,
    admitted: AdmittedBaselineSubjects,
    comparisons: tuple[OrchestratedComparison, ...],
) -> dict[str, Any]:
    evaluated = sum(
        item.status is ComparisonStatus.COMPARED for item in comparisons
    )
    violated = sum(
        item.comparison is not None
        and item.comparison.decision is ComparisonDecision.REGRESSION
        for item in comparisons
    )
    not_evaluable = len(comparisons) - evaluated
    return {
        "configured": True,
        "status": (
            RATCHET_EVALUATED_WITH_NOT_EVALUABLE
            if not_evaluable
            else RATCHET_EVALUATED
        ),
        "admission_status": ADMISSION_ADMITTED,
        "baseline": _baseline_identity(admitted),
        "current": _current_identity(view),
        "paired_subject_count": len(admitted.pairs),
        "evaluated_count": evaluated,
        "violated_count": violated,
        "not_evaluable_count": not_evaluable,
        "error": None,
    }


def _render_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical_completeness(
    completeness: ObservationCompleteness,
) -> str:
    if completeness is ObservationCompleteness.MISSING:
        return policy_metric_module.COMPLETENESS_UNAVAILABLE
    return completeness.value


def _reason_for(status: ComparisonStatus) -> str:
    if status is ComparisonStatus.ABSENT:
        return finding_module.REASON_MEASUREMENT_ABSENT
    if status is ComparisonStatus.PARTIAL:
        return finding_module.REASON_PARTIAL_DATA_REFUSED
    if status is ComparisonStatus.NOT_APPLICABLE:
        return finding_module.REASON_NOTHING_TO_MEASURE
    return finding_module.REASON_MEASUREMENT_UNAVAILABLE


def _observation_index(
    observations: tuple[CurrentMetricObservation, ...],
) -> dict[tuple[str, str, str, str], CurrentMetricObservation]:
    return {item.coordinate: item for item in observations}


def _comparison_coordinate(
    item: OrchestratedComparison,
) -> tuple[str, str, str, str]:
    return (
        item.subject_key,
        item.language or "",
        item.scope.value,
        item.metric,
    )


def _repository_urls(view: Any) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for repository in view.repositories:
        if not isinstance(repository, Mapping):
            continue
        key = repository.get("subject_key")
        if isinstance(key, str):
            url = repository.get("repository_url")
            result[key] = url if isinstance(url, str) else None
    return result


def _provenance(
    view: Any,
    admitted: AdmittedBaselineSubjects,
    item: OrchestratedComparison,
) -> dict[str, Any]:
    pair = next(pair for pair in admitted.pairs if pair.subject_key == item.subject_key)
    return {
        "baseline_digest_sha256": admitted.artifact.verified_sha256,
        "baseline_run_id": admitted.artifact.baseline.source_run.run_id,
        "baseline_commit_sha": pair.baseline.analyzed_commit_sha,
        "current_run_id": view.run_id,
        "current_commit_sha": pair.current.analyzed_commit_sha,
        "metric_contract": item.metric_contract,
        "baseline_metric_contract_version": item.baseline_contract_version,
        "current_metric_contract_version": item.current_contract_version,
    }


def _finding_for(
    view: Any,
    admitted: AdmittedBaselineSubjects,
    item: OrchestratedComparison,
    observation: CurrentMetricObservation | None,
    rule_by_id: Mapping[str, Any],
    repository_urls: Mapping[str, str | None],
) -> finding_module.Finding | None:
    rule = rule_by_id[item.rule_id]
    comparison = item.comparison
    regression = (
        item.status is ComparisonStatus.COMPARED
        and comparison is not None
        and comparison.decision is ComparisonDecision.REGRESSION
    )
    if not regression and item.status is ComparisonStatus.COMPARED:
        return None

    location = finding_module.location_label(
        scope=item.scope.value,
        subject_key=item.subject_key,
        language=item.language,
    )
    finding_id = finding_module.finding_identity(
        rule_id=item.rule_id,
        metric=item.metric,
        scope=item.scope.value,
        subject_key=item.subject_key,
        language=item.language,
    )
    common = {
        "finding_id": finding_id,
        "rule_id": item.rule_id,
        "severity": rule.severity.value,
        "scope": item.scope.value,
        "subject_key": item.subject_key,
        "kind": finding_module.KIND_METRIC,
        "metric": item.metric,
        # The frozen ratchet condition is regression_amount > tolerance.
        "operator": "gt",
        "threshold": rule.max_regression,
        "repository_url": repository_urls.get(item.subject_key),
        "language": item.language,
        "provenance": _provenance(view, admitted, item),
    }
    if regression:
        assert comparison is not None
        evidence = comparison.to_dict()
        evidence["maximum_regression"] = rule.max_regression
        message = (
            f"{item.metric} regressed at {location}: baseline "
            f"{_render_number(comparison.baseline_value)}, current "
            f"{_render_number(comparison.current_value)}, signed delta "
            f"{_render_number(comparison.delta)}; regression amount "
            f"{_render_number(comparison.regression_amount)} exceeds the "
            f"frozen tolerance {_render_number(rule.max_regression)} "
            f"({comparison.direction.value})."
        )
        return finding_module.Finding(
            **common,
            status=finding_module.STATUS_VIOLATED,
            message=message,
            observed_value=comparison.regression_amount,
            value_status=(
                observation.completeness.value if observation else "complete"
            ),
            value_status_field=(
                observation.status_field if observation else None
            ),
            data_completeness=policy_metric_module.COMPLETENESS_COMPLETE,
            evidence=evidence,
        )

    reason = _reason_for(item.status)
    completeness = (
        observation.completeness
        if observation is not None
        else ObservationCompleteness.MISSING
    )
    message = (
        f"{item.metric} could not be compared at {location}: the current "
        f"observation is {item.status.value}. The ratchet comparison did not "
        "run; this is not a pass."
    )
    baseline_observation = next(
        observation
        for observation in admitted.artifact.baseline.observations
        if observation.rule_id == item.rule_id
        and observation.subject_key == item.subject_key
        and observation.language == item.language
    )
    return finding_module.Finding(
        **common,
        status=finding_module.STATUS_NOT_EVALUABLE,
        message=message,
        observed_value=None,
        value_status=(
            observation.status_value
            if observation is not None and observation.status_value is not None
            else item.status.value
        ),
        value_status_field=(observation.status_field if observation else None),
        data_completeness=_canonical_completeness(completeness),
        reason=reason,
        evidence={
            "baseline_value": baseline_observation.baseline_value,
            "current_value": observation.value if observation is not None else None,
            "delta": None,
            "direction": rule.direction.value,
            "regression_amount": None,
            "decision": None,
            "maximum_regression": rule.max_regression,
            "comparison_status": item.status.value,
            "current_completeness": completeness.value,
        },
    )


def _rule_summaries(
    admitted: AdmittedBaselineSubjects,
    comparisons: tuple[OrchestratedComparison, ...],
) -> tuple[dict[str, Any], ...]:
    baseline = admitted.artifact.baseline
    by_rule: dict[str, list[OrchestratedComparison]] = {
        rule.rule_id: [] for rule in baseline.rules
    }
    for item in comparisons:
        by_rule[item.rule_id].append(item)
    summaries: list[dict[str, Any]] = []
    for rule in baseline.rules:
        records = by_rule[rule.rule_id]
        violated = sum(
            item.comparison is not None
            and item.comparison.decision is ComparisonDecision.REGRESSION
            for item in records
        )
        not_evaluable = sum(
            item.status is not ComparisonStatus.COMPARED for item in records
        )
        summaries.append(
            {
                "rule_id": rule.rule_id,
                "kind": finding_module.KIND_METRIC,
                "severity": rule.severity.value,
                "metric": rule.metric,
                "metric_source": "ratchet baseline/current observation comparison",
                "operator": "gt",
                "threshold": rule.max_regression,
                "scope": rule.scope.value,
                "domain": None,
                "units_evaluated": len(records),
                "violated": violated,
                "passed": len(records) - violated - not_evaluable,
                "not_evaluable": not_evaluable,
                "not_applicable": 0,
                "evaluation_error": 0,
            }
        )
    return tuple(sorted(summaries, key=lambda item: item["rule_id"]))


def evaluate_ratchet_check(
    view: Any,
    request: RatchetCheckRequest,
) -> RatchetCheckEvaluation:
    """Run BR2 + BR3 and project canonical ratchet findings."""

    if not isinstance(request, RatchetCheckRequest):
        raise RatchetCheckServiceError("admission", "request_required")
    try:
        artifact = admit_baseline(
            request.baseline_payload,
            trust=request.trust,
            source_run=request.source_run,
        )
        current_semantics = semantics_from_manifest(view.manifest)
        current_producer = producer_from_manifest(view.manifest)
        require_semantics_compatibility(
            artifact.baseline.measurement_semantics, current_semantics
        )
        require_producer_compatibility(
            artifact.baseline.producer, current_producer
        )
        current_subjects = _current_subjects(
            view, _baseline_languages(artifact)
        )
        pairs = pair_subjects(artifact.baseline, current_subjects)
        _require_coordinate_manifest_compatibility(artifact, view)
        require_contract_compatibility(pairs)
        require_revision_compatibility(
            pairs,
            request.revision_sources,
            timeout=request.revision_timeout,
            resolver=request.revision_resolver,
        )
        admitted = AdmittedBaselineSubjects(artifact=artifact, pairs=pairs)
    except RatchetCheckServiceError:
        raise
    except (
        BaselineAdmissionError,
        SubjectPairingError,
        ContractCompatibilityError,
        RatchetContractError,
        MeasurementSemanticsError,
        SemanticsCompatibilityError,
    ) as exc:
        raise RatchetCheckServiceError(
            "admission",
            str(getattr(exc, "code", type(exc).__name__)),
            getattr(exc, "detail", None) or str(exc),
        ) from exc

    try:
        observations = extract_current_observations(view, admitted)
        comparisons = orchestrate_comparisons(admitted, observations)
    except (ObservationExtractionError, ComparisonOrchestrationError) as exc:
        raise RatchetCheckServiceError(
            "evaluation",
            str(getattr(exc, "code", type(exc).__name__)),
            getattr(exc, "detail", None) or str(exc),
        ) from exc

    observation_by_coordinate = _observation_index(observations)
    rule_by_id = {
        rule.rule_id: rule for rule in admitted.artifact.baseline.rules
    }
    urls = _repository_urls(view)
    findings = tuple(
        finding
        for item in comparisons
        if (
            finding := _finding_for(
                view,
                admitted,
                item,
                observation_by_coordinate.get(_comparison_coordinate(item)),
                rule_by_id,
                urls,
            )
        ) is not None
    )
    return RatchetCheckEvaluation(
        admitted=admitted,
        comparisons=comparisons,
        findings=findings,
        rule_summaries=_rule_summaries(admitted, comparisons),
        summary=_successful_summary(view, admitted, comparisons),
    )


_SEVERITY_RANK = {
    severity: index for index, severity in enumerate(policy_rule_module.SEVERITIES)
}
_SCOPE_RANK = {
    scope: index for index, scope in enumerate(finding_module.FINDING_SCOPES)
}


def _finding_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _SEVERITY_RANK.get(str(item.get("severity")), len(_SEVERITY_RANK)),
        str(item.get("rule_id") or ""),
        str(item.get("subject_key") or ""),
        _SCOPE_RANK.get(str(item.get("scope")), len(_SCOPE_RANK)),
        str(item.get("language") or "").casefold(),
        str(item.get("path") or ""),
        item.get("start_line") if item.get("start_line") is not None else -1,
        str(item.get("callable_row_id") or ""),
        str(item.get("finding_id") or ""),
    )


def _add_unit_counts(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key in (
        "units_evaluated",
        "violated",
        "passed",
        "not_evaluable",
        "not_applicable",
        "evaluation_error",
    ):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))


def _merge_success(
    result: dict[str, Any], evaluation: RatchetCheckEvaluation
) -> dict[str, Any]:
    ratchet_rule_ids = {
        item["rule_id"] for item in evaluation.rule_summaries
    }
    existing_rule_ids = {
        str(item.get("rule_id")) for item in result.get("rules", ())
        if isinstance(item, Mapping)
    }
    collision = sorted(existing_rule_ids & ratchet_rule_ids)
    if collision:
        raise RatchetCheckServiceError(
            "admission", "rule_id_collision", collision[0]
        )

    ratchet_findings = [item.as_dict() for item in evaluation.findings]
    combined_findings = [*result.get("findings", ()), *ratchet_findings]
    combined_findings.sort(key=_finding_sort_key)
    result["findings"] = combined_findings
    result["rules"] = sorted(
        [*result.get("rules", ()), *evaluation.rule_summaries],
        key=lambda item: (str(item.get("kind")), str(item.get("rule_id"))),
    )

    counts = result["counts"]
    counts["findings"] = len(combined_findings)
    policy_options = ((result.get("policy") or {}).get("options") or {})
    fail_on_not_evaluable = (
        policy_options.get("on_not_evaluable") == "fail"
    )
    ratchet_failing = [
        finding for finding in ratchet_findings
        if finding_module.requires_failure(
            severity=finding.get("severity"),
            status=finding.get("status"),
            fail_on_not_evaluable=fail_on_not_evaluable,
        )
    ]
    counts["failing"] = int(counts.get("failing", 0)) + len(ratchet_failing)
    counts["rules_evaluated"] = int(counts.get("rules_evaluated", 0)) + len(
        evaluation.rule_summaries
    )
    for finding in ratchet_findings:
        counts["by_status"][finding["status"]] += 1
        counts["by_severity"][finding["severity"]] += 1
    for summary in evaluation.rule_summaries:
        _add_unit_counts(counts["units"], summary)

    previous_not_evaluable = {
        item["finding_id"]: item for item in result.get("not_evaluable", ())
    }
    ratchet_ids = {item["finding_id"] for item in ratchet_findings}
    result["not_evaluable"] = [
        (
            {
                "finding_id": finding["finding_id"],
                "rule_id": finding["rule_id"],
                "metric": finding["metric"],
                "scope": finding["scope"],
                "subject_key": finding["subject_key"],
                "language": finding["language"],
                "reason": finding["reason"],
                "reason_meaning": finding_module.REASON_MEANINGS.get(
                    finding["reason"] or "", ""
                ),
                "fails_the_build": finding_module.requires_failure(
                    severity=finding.get("severity"),
                    status=finding.get("status"),
                    fail_on_not_evaluable=fail_on_not_evaluable,
                ),
            }
            if finding["finding_id"] in ratchet_ids
            else previous_not_evaluable[finding["finding_id"]]
        )
        for finding in combined_findings
        if finding["status"] == finding_module.STATUS_NOT_EVALUABLE
    ]
    result["ratchet"] = evaluation.summary
    input_provenance = result.get("evaluated_input_provenance")
    if isinstance(input_provenance, dict):
        ratchet_provenance = input_provenance.get("ratchet")
        if isinstance(ratchet_provenance, dict):
            baseline = evaluation.summary.get("baseline") or {}
            ratchet_provenance["baseline_sha256"] = baseline.get("verified_sha256")
            ratchet_provenance["source_run_manifest_sha256"] = baseline.get(
                "source_run_manifest_sha256"
            )
    if ratchet_failing and int(result.get("exit_code", 0)) != 2:
        result["verdict"] = "fail"
        result["exit_code"] = 1
        result["failure_kind"] = None
        result["failure_message"] = None
    return result


def _merge_error(
    result: dict[str, Any], view: Any, error: RatchetCheckServiceError
) -> dict[str, Any]:
    if error.code == "revision_source_missing":
        from modules.ratchet.cli_request import REVISION_SOURCE_GUIDANCE
        error = RatchetCheckServiceError(error.phase, error.code, REVISION_SOURCE_GUIDANCE)
    result["ratchet"] = _error_summary(
        view,
        phase=error.phase,
        code=error.code,
        detail=error.detail,
    )
    result["verdict"] = "error"
    result["exit_code"] = 2
    result["failure_kind"] = (
        FAILURE_RATCHET_ADMISSION
        if error.phase == "admission"
        else FAILURE_RATCHET_EVALUATION
    )
    result["failure_message"] = str(error)
    return result


def integrate_ratchet_check_result(
    check_result: Mapping[str, Any],
    view: Any,
    request: RatchetCheckRequest | None,
) -> dict[str, Any]:
    """Preserve current-state output or return configured Check Result 1.3."""

    result = dict(check_result)
    if request is None:
        return result
    result["check_result_format_version"] = RATCHET_CHECK_RESULT_FORMAT_VERSION
    try:
        evaluation = evaluate_ratchet_check(view, request)
        return _merge_success(result, evaluation)
    except RatchetCheckServiceError as exc:
        return _merge_error(result, view, exc)


__all__ = [
    "ADMISSION_ADMITTED",
    "ADMISSION_FAILED",
    "ADMISSION_NOT_REQUESTED",
    "RATCHET_CHECK_RESULT_FORMAT_VERSION",
    "FAILURE_RATCHET_ADMISSION",
    "FAILURE_RATCHET_EVALUATION",
    "RATCHET_ADMISSION_FAILED",
    "RATCHET_EVALUATED",
    "RATCHET_EVALUATED_WITH_NOT_EVALUABLE",
    "RATCHET_EVALUATION_FAILED",
    "RATCHET_NOT_CONFIGURED",
    "RatchetCheckEvaluation",
    "RatchetCheckRequest",
    "RatchetCheckServiceError",
    "admission_failure_summary",
    "evaluate_ratchet_check",
    "integrate_ratchet_check_result",
    "not_configured_summary",
]
