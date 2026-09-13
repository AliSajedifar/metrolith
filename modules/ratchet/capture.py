"""Trusted Ratchet Baseline capture from one admitted finalized run bundle.

Capture accepts rules and a run directory, never observation values.  It opens
the existing immutable run view, projects only authoritative persisted metric
data, reconciles the duplicated terminal repository projection, validates
metric domains, and emits canonical Ratchet Baseline Contract 1.0 bytes.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules import complexity_view
from modules.ratchet.admission import (
    SourceCoordinateEvidence,
    SourceRunEvidence,
    SourceSubjectEvidence,
)
from modules.ratchet.contract import (
    BaselineObservation,
    CoordinateManifestEntry,
    CoordinateStatus,
    MetricContractBinding,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetContractError,
    RatchetRule,
    RatchetScope,
    SourceRunBinding,
    canonical_bytes,
    canonical_metric_number,
)
from modules.ratchet.observations import (
    CurrentMetricObservation,
    MetricFamily,
    ObservationCompleteness,
    ObservationExtractionError,
    RatchetMetricDefinition,
    extract_persisted_observations,
    require_v1_metric,
)
from modules.ratchet.semantics import (
    MeasurementSemantics,
    MeasurementSemanticsError,
    ProducerIdentity,
    producer_from_manifest,
    semantics_from_manifest,
)
from modules.vocabularies import SubjectKeyBasis, WorkingTreeState


class BaselineCaptureError(ValueError):
    """A producing run cannot safely become a Ratchet baseline."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CapturedBaseline:
    baseline: RatchetBaseline
    payload: bytes
    source_run_evidence: SourceRunEvidence

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True, slots=True)
class _CapturedRun:
    run_id: str
    manifest_sha256: str
    analysis_sha256: str
    producer: ProducerIdentity
    semantics: MeasurementSemantics
    subjects: tuple[PortableSubjectBinding, ...]
    coordinates: tuple[CoordinateManifestEntry, ...]
    observations: tuple[BaselineObservation, ...]
    source_evidence: SourceRunEvidence


def _capture_error(code: str, exc: Exception) -> BaselineCaptureError:
    return BaselineCaptureError(code, str(getattr(exc, "code", type(exc).__name__)))


def _canonical_language(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise BaselineCaptureError("language_identity_invalid", field)
    return unicodedata.normalize("NFC", value).casefold()


def _language_population(repository: Mapping[str, Any]) -> tuple[str, ...]:
    key = str(repository.get("subject_key") or "<unknown>")
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
    languages: set[str] = set()
    for field, value in sources:
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise BaselineCaptureError("language_mapping_invalid", f"{key}: {field}")
        within: set[str] = set()
        for raw in value:
            language = _canonical_language(raw, f"{key}: {field}")
            if language in within:
                raise BaselineCaptureError(
                    "language_identity_duplicate", f"{key}: {language}"
                )
            within.add(language)
        languages.update(within)
    if not languages:
        raise BaselineCaptureError("language_population_missing", key)
    return tuple(sorted(languages))


def _source_state(repository: Mapping[str, Any]) -> str | None:
    recorded = repository.get("working_tree_state")
    if recorded is not None:
        return str(recorded)
    acquisition = repository.get("acquisition")
    acquisition = acquisition if isinstance(acquisition, Mapping) else {}
    commit = acquisition.get("analyzed_commit_sha")
    if isinstance(commit, str) and len(commit) in {40, 64}:
        return WorkingTreeState.COMMITTED_REVISION.value
    return None


def _verify_terminal_run(view: Any) -> None:
    from validation.artifact_io.compatibility import (
        CompatibilityState,
        RunLifecycle,
    )

    if view.integrity_status != "completed":
        raise BaselineCaptureError(
            "source_run_not_successful", str(view.integrity_status or "missing")
        )
    if view.compatibility.state is not CompatibilityState.SUPPORTED:
        raise BaselineCaptureError("source_run_schema_unsupported")
    if view.lifecycle is not RunLifecycle.FINALIZED_VALID or view.structural_errors:
        raise BaselineCaptureError("source_run_artifact_integrity_failed")
    manifest = view.manifest
    status = view.status
    run_id = view.run_id
    if not isinstance(run_id, str) or not run_id:
        raise BaselineCaptureError("source_run_id_missing")
    if manifest.get("run_id") != run_id or status.get("run_id") != run_id:
        raise BaselineCaptureError("source_run_id_inconsistent")
    if manifest.get("run_integrity_status") != "completed":
        raise BaselineCaptureError("source_manifest_not_completed")
    if manifest.get("measurement_outcome") != "complete" or status.get(
        "measurement_outcome"
    ) != "complete":
        raise BaselineCaptureError("source_measurement_not_complete")
    for document, name in ((manifest, "manifest"), (status, "status")):
        if document.get("failure_count") != 0 or document.get("partial_count") != 0:
            raise BaselineCaptureError("source_run_has_failures", name)
    self_validation = manifest.get("self_validation")
    if not isinstance(self_validation, Mapping) or self_validation.get("passed") is not True:
        raise BaselineCaptureError("source_run_self_validation_failed")
    benchmark_environment = manifest.get("benchmark_environment")
    if not isinstance(benchmark_environment, Mapping):
        raise BaselineCaptureError("source_producer_provenance_missing")
    persisted_environment = view.environment
    for field in (
        "profiler_git_commit_sha",
        "profiler_git_dirty",
        "metric_contract_version",
        "complexity_contract_version",
        "exclusion_policy_version",
        "exclusion_policy_sha256",
        "python_version",
        "tree_sitter_version",
        "grammar_versions",
        "parser_initialization",
    ):
        if manifest.get(field) != benchmark_environment.get(field) or (
            benchmark_environment.get(field) != persisted_environment.get(field)
        ):
            raise BaselineCaptureError(
                "source_producer_provenance_inconsistent", field
            )
    for field in (
        "profiler_provenance_kind",
        "profiler_git_state",
        "profiler_source_sha256",
    ):
        if field in manifest or field in benchmark_environment or field in persisted_environment:
            if manifest.get(field) != benchmark_environment.get(field) or (
                benchmark_environment.get(field) != persisted_environment.get(field)
            ):
                raise BaselineCaptureError(
                    "source_producer_provenance_inconsistent", field
                )
    installed = (
        manifest.get("profiler_provenance_kind") == "installed_distribution"
        and manifest.get("profiler_git_state") == "not_applicable"
        and manifest.get("profiler_git_dirty") is None
        and isinstance(manifest.get("profiler_source_sha256"), str)
    )
    clean_git = (
        manifest.get("profiler_git_dirty") is False
        and isinstance(manifest.get("profiler_git_commit_sha"), str)
    )
    if not (installed or clean_git):
        raise BaselineCaptureError(
            "source_evaluator_identity_not_reproducible",
            "recorded profiler_git_dirty="
            f"{manifest.get('profiler_git_dirty')!r}, "
            f"profiler_provenance_kind={manifest.get('profiler_provenance_kind')!r}, "
            f"profiler_git_state={manifest.get('profiler_git_state')!r}; "
            "requires recorded clean Git evaluator identity or installed_distribution "
            "with not_applicable Git state, null dirtiness and a source digest. "
            "Produce a new run using a qualified evaluator; do not edit retained provenance.",
        )


def _reconcile_analysis_projection(view: Any) -> None:
    projection_errors = view.check_repository_projection()
    if projection_errors:
        raise BaselineCaptureError(
            "source_repository_projection_invalid", projection_errors[0].code.value
        )
    projected = tuple(view.repository_documents.values())
    authoritative = tuple(view.repositories)
    if len(projected) != len(authoritative):
        raise BaselineCaptureError("source_repository_projection_incomplete")
    projected_by_key = {
        item.get("subject_key"): dict(item)
        for item in projected
        if isinstance(item, Mapping)
    }
    if len(projected_by_key) != len(projected):
        raise BaselineCaptureError("source_repository_projection_identity_invalid")
    for repository in authoritative:
        key = repository.get("subject_key")
        if projected_by_key.get(key) != dict(repository):
            raise BaselineCaptureError(
                "source_analysis_projection_mismatch", str(key)
            )


def _contracts_for_repository(
    repository: Mapping[str, Any],
    manifest: Mapping[str, Any],
    producer: ProducerIdentity,
) -> dict[str, str]:
    metrics_version = repository.get("metric_contract_version") or manifest.get(
        "metric_contract_version"
    )
    complexity = complexity_view.repository_block(repository)
    complexity_version = (
        (complexity or {}).get("complexity_contract_version")
        or repository.get("complexity_contract_version")
        or manifest.get("complexity_contract_version")
    )
    if metrics_version != producer.metric_contract_version:
        raise BaselineCaptureError("metric_contract_inconsistent")
    if complexity_version != producer.complexity_contract_version:
        raise BaselineCaptureError("complexity_contract_inconsistent")
    return {"metrics": str(metrics_version), "complexity": str(complexity_version)}


def _subjects_and_contracts(
    view: Any,
    rules: tuple[RatchetRule, ...],
    producer: ProducerIdentity,
) -> tuple[
    tuple[PortableSubjectBinding, ...],
    dict[str, Mapping[str, str]],
    dict[str, Mapping[str, Any]],
]:
    used_contracts = {rule.metric_contract for rule in rules}
    subjects: list[PortableSubjectBinding] = []
    versions: dict[str, Mapping[str, str]] = {}
    repositories: dict[str, Mapping[str, Any]] = {}
    for index, repository in enumerate(view.repositories):
        if not isinstance(repository, Mapping):
            raise BaselineCaptureError("source_subject_invalid", str(index))
        key = repository.get("subject_key")
        if not isinstance(key, str) or not key or key.strip() != key:
            raise BaselineCaptureError("source_subject_identity_missing", str(index))
        if key in repositories:
            raise BaselineCaptureError("source_subject_duplicate", key)
        try:
            basis = SubjectKeyBasis(repository.get("subject_key_basis"))
        except (TypeError, ValueError) as exc:
            raise BaselineCaptureError("source_subject_identity_invalid", key) from exc
        if not basis.is_portable:
            raise BaselineCaptureError("source_subject_identity_not_portable", key)
        acquisition = repository.get("acquisition")
        acquisition = acquisition if isinstance(acquisition, Mapping) else {}
        try:
            source_state = WorkingTreeState(_source_state(repository))
        except (TypeError, ValueError) as exc:
            raise BaselineCaptureError(
                "source_subject_worktree_state_invalid", key
            ) from exc
        if source_state is WorkingTreeState.DIRTY_WORKTREE:
            raise BaselineCaptureError("source_subject_dirty", key)
        if source_state not in {
            WorkingTreeState.COMMITTED_REVISION,
            WorkingTreeState.CLEAN_WORKTREE,
        }:
            raise BaselineCaptureError("source_subject_not_revision_bound", key)
        contract_versions = _contracts_for_repository(
            repository, view.manifest, producer
        )
        versions[key] = contract_versions
        repositories[key] = repository
        try:
            subjects.append(
                PortableSubjectBinding(
                    subject_key=key,
                    subject_key_basis=basis,
                    analyzed_commit_sha=acquisition.get("analyzed_commit_sha"),
                    artifact_schema_version=(
                        repository.get("artifact_schema_version")
                        or view.manifest.get("artifact_schema_version")
                    ),
                    metric_contracts=tuple(
                        MetricContractBinding(name, contract_versions[name])
                        for name in sorted(used_contracts)
                    ),
                    analysis_scope_hash=repository.get("analysis_scope_hash"),
                    analysis_scope_hash_version=repository.get(
                        "analysis_scope_hash_version"
                    ),
                )
            )
        except RatchetContractError as exc:
            raise _capture_error("source_subject_binding_invalid", exc) from exc
    if not subjects:
        raise BaselineCaptureError("source_subjects_missing")
    return tuple(subjects), versions, repositories


def _targets(
    rules: tuple[RatchetRule, ...],
    repositories: Mapping[str, Mapping[str, Any]],
) -> tuple[
    tuple[str, str | None, RatchetMetricDefinition, RatchetRule], ...
]:
    output: list[tuple[str, str | None, RatchetMetricDefinition, RatchetRule]] = []
    for rule in rules:
        try:
            definition = require_v1_metric(rule.metric, rule.scope)
        except ObservationExtractionError as exc:
            raise _capture_error("unsupported_capture_metric", exc) from exc
        for key in sorted(repositories):
            languages: tuple[str | None, ...] = (
                (None,)
                if rule.scope is RatchetScope.REPOSITORY
                else _language_population(repositories[key])
            )
            for language in languages:
                output.append((key, language, definition, rule))
    return tuple(output)


def _stream_sha256(path: Path, maximum: int) -> str:
    try:
        if path.stat().st_size > maximum:
            raise BaselineCaptureError("source_artifact_too_large", path.name)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise BaselineCaptureError("source_artifact_unreadable", path.name) from exc


def _callable_artifacts(view: Any) -> tuple[str, ...]:
    container = view.callable_container
    if container is not None:
        paths: list[str] = ["callables/container.json"]
        partitions = container.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise BaselineCaptureError("callable_container_invalid")
        for item in partitions:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise BaselineCaptureError("callable_container_invalid")
            paths.append(str(item["path"]))
        return tuple(paths)
    if view.reader.exists("callables.csv"):
        return ("callables.csv",)
    raise BaselineCaptureError("callable_ledger_missing")


def _ledger_digest(view: Any) -> str:
    from validation.artifact_io.paths import resolve_artifact

    digest = hashlib.sha256()
    for relative in sorted(_callable_artifacts(view)):
        path, spec = resolve_artifact(
            view.run_directory,
            relative,
            strict_symlinks=view.reader.strict_symlinks,
        )
        item_digest = _stream_sha256(path, spec.max_bytes)
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(bytes.fromhex(item_digest))
    return digest.hexdigest()


def _coordinate_status(
    observation: CurrentMetricObservation,
) -> tuple[CoordinateStatus, bool]:
    if observation.completeness is ObservationCompleteness.COMPLETE:
        if observation.value is None:
            raise BaselineCaptureError(
                "source_observation_missing", "/".join(observation.coordinate)
            )
        return CoordinateStatus.OBSERVED, True
    if observation.completeness is ObservationCompleteness.NOT_APPLICABLE:
        return CoordinateStatus.NOT_APPLICABLE, False
    raise BaselineCaptureError(
        f"source_coordinate_{observation.completeness.value}",
        "/".join(observation.coordinate),
    )


def _inspect_run(
    run_directory: Path,
    rules: Iterable[RatchetRule],
) -> _CapturedRun:
    try:
        frozen_rules = tuple(rules)
    except TypeError as exc:
        raise BaselineCaptureError("capture_rules_invalid") from exc
    if not frozen_rules or not all(isinstance(rule, RatchetRule) for rule in frozen_rules):
        raise BaselineCaptureError("capture_rules_invalid")
    if len({rule.rule_id for rule in frozen_rules}) != len(frozen_rules):
        raise BaselineCaptureError("capture_rule_duplicate")

    try:
        from validation.artifact_io.reader import open_run

        view = open_run(Path(run_directory))
    except (OSError, ValueError) as exc:
        raise BaselineCaptureError("source_run_unreadable", type(exc).__name__) from exc
    _verify_terminal_run(view)
    _reconcile_analysis_projection(view)
    try:
        producer = producer_from_manifest(view.manifest)
        semantics = semantics_from_manifest(view.manifest)
    except MeasurementSemanticsError as exc:
        raise _capture_error("source_semantics_invalid", exc) from exc
    if producer.metric_contract_version != semantics.metric_contract_version or (
        producer.complexity_contract_version != semantics.complexity_contract_version
    ):
        raise BaselineCaptureError("source_contract_semantics_mismatch")

    subjects, versions, repositories = _subjects_and_contracts(
        view, frozen_rules, producer
    )
    targets = _targets(frozen_rules, repositories)
    extraction_targets = tuple(item[:3] for item in targets)
    needs_callable = any(
        definition.family is MetricFamily.COGNITIVE_COMPLEXITY
        for _key, _language, definition, _rule in targets
    )
    ledger_before = _ledger_digest(view) if needs_callable else None
    try:
        extracted = extract_persisted_observations(
            view, extraction_targets, versions
        )
    except ObservationExtractionError as exc:
        raise _capture_error("source_observation_extraction_failed", exc) from exc
    ledger_after = _ledger_digest(view) if needs_callable else None
    if ledger_before != ledger_after:
        raise BaselineCaptureError("callable_ledger_changed_during_capture")

    manifest_sha256 = hashlib.sha256(
        view.reader.document_bytes("run_manifest.json")
    ).hexdigest()
    analysis_sha256 = hashlib.sha256(
        view.reader.document_bytes("analysis.json")
    ).hexdigest()
    rule_by_coordinate = {
        (key, language or "", definition.scope.value, definition.identifier): rule
        for key, language, definition, rule in targets
    }
    coordinates: list[CoordinateManifestEntry] = []
    observations: list[BaselineObservation] = []
    source_coordinates: list[SourceCoordinateEvidence] = []
    for observation in extracted:
        rule = rule_by_coordinate.get(observation.coordinate)
        if rule is None:
            raise BaselineCaptureError(
                "source_observation_unknown_coordinate",
                "/".join(observation.coordinate),
            )
        status, required = _coordinate_status(observation)
        definition = require_v1_metric(rule.metric, rule.scope)
        source_artifact = (
            "callables-ledger"
            if definition.family is MetricFamily.COGNITIVE_COMPLEXITY
            else "analysis.json"
        )
        source_digest = ledger_before if source_artifact == "callables-ledger" else analysis_sha256
        assert source_digest is not None
        coordinate = CoordinateManifestEntry(
            rule_id=rule.rule_id,
            subject_key=observation.subject_key,
            language=observation.language,
            metric=rule.metric,
            scope=rule.scope,
            status=status,
            required=required,
            source_artifact=source_artifact,
            source_artifact_sha256=source_digest,
        )
        value = None
        if status is CoordinateStatus.OBSERVED:
            try:
                value = canonical_metric_number(
                    rule.metric,
                    observation.value,
                    "captured observation",
                )
            except RatchetContractError as exc:
                raise _capture_error("source_observation_domain_invalid", exc) from exc
            observations.append(
                BaselineObservation(
                    rule_id=rule.rule_id,
                    subject_key=observation.subject_key,
                    language=observation.language,
                    baseline_value=value,
                )
            )
        coordinates.append(coordinate)
        source_coordinates.append(SourceCoordinateEvidence(coordinate, value))

    source_subjects = tuple(
        SourceSubjectEvidence(
            subject_key=subject.subject_key,
            analyzed_commit_sha=subject.analyzed_commit_sha,
            working_tree_state=_source_state(repositories[subject.subject_key]),
        )
        for subject in subjects
    )
    evidence = SourceRunEvidence(
        run_id=str(view.run_id),
        run_manifest_sha256=manifest_sha256,
        analysis_sha256=analysis_sha256,
        status="complete",
        admitted=True,
        producer=producer,
        measurement_semantics=semantics,
        subjects=source_subjects,
        coordinates=tuple(source_coordinates),
    )
    return _CapturedRun(
        run_id=str(view.run_id),
        manifest_sha256=manifest_sha256,
        analysis_sha256=analysis_sha256,
        producer=producer,
        semantics=semantics,
        subjects=subjects,
        coordinates=tuple(coordinates),
        observations=tuple(observations),
        source_evidence=evidence,
    )


def source_run_evidence_from_run(
    run_directory: Path,
    rules: Iterable[RatchetRule],
) -> SourceRunEvidence:
    """Re-project authoritative producer evidence for admission reconciliation."""

    return _inspect_run(run_directory, rules).source_evidence


def capture_baseline(
    run_directory: Path,
    rules: Iterable[RatchetRule],
) -> CapturedBaseline:
    """Capture canonical baseline bytes; no observation value is an input."""

    frozen_rules = tuple(rules)
    captured = _inspect_run(run_directory, frozen_rules)
    try:
        baseline = RatchetBaseline(
            source_run=SourceRunBinding(
                run_id=captured.run_id,
                run_manifest_sha256=captured.manifest_sha256,
                analysis_sha256=captured.analysis_sha256,
                producer_version=captured.producer.program_version,
            ),
            producer=captured.producer,
            measurement_semantics=captured.semantics,
            subjects=captured.subjects,
            rules=frozen_rules,
            coordinate_manifest=captured.coordinates,
            observations=captured.observations,
        )
        payload = canonical_bytes(baseline)
    except RatchetContractError as exc:
        raise _capture_error("captured_baseline_invalid", exc) from exc
    return CapturedBaseline(
        baseline=baseline,
        payload=payload,
        source_run_evidence=captured.source_evidence,
    )


__all__ = [
    "BaselineCaptureError",
    "CapturedBaseline",
    "capture_baseline",
    "source_run_evidence_from_run",
]
