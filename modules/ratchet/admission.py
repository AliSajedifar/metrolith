"""Trusted admission boundary for Ratchet Baseline Contract 1.0.

The baseline bytes never authenticate themselves. Admission requires an
externally supplied SHA-256 and admitted source-run evidence, then reuses the
strict BR1 parser. Revision ancestry is delegated to
``modules.revision_source.resolve_revision_pair``; no Git command or ancestry
algorithm is implemented here.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from modules.ratchet.contract import (
    CoordinateManifestEntry,
    CoordinateStatus,
    Number,
    RatchetBaseline,
    RatchetContractError,
    canonical_metric_number,
    canonical_bytes,
    parse_baseline_json,
)
from modules.ratchet.semantics import MeasurementSemantics, ProducerIdentity
from modules.ratchet.pairing import (
    CurrentSubject,
    SubjectPair,
    pair_subjects,
    require_contract_compatibility,
)
from modules.revision_source import (
    ResolvedRevisionPair,
    RevisionUnavailable,
    resolve_revision_pair,
)
from modules.vocabularies import WorkingTreeState


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class BaselineArtifactOrigin(str, Enum):
    PROTECTED_BASE_REVISION = "protected_base_revision"
    OWNER_CONTROLLED_ARTIFACT = "owner_controlled_artifact"
    LOCAL_FILE = "local_file"
    CANDIDATE_WORKSPACE = "candidate_workspace"


class BaselineAdmissionError(ValueError):
    """A baseline failed one typed admission gate."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class BaselineTrustContext:
    """Trust facts supplied outside the artifact being admitted."""

    expected_sha256: str | None
    origin: BaselineArtifactOrigin | str
    pull_request_mode: bool = False


@dataclass(frozen=True, slots=True)
class SourceSubjectEvidence:
    subject_key: str | None
    analyzed_commit_sha: str | None
    working_tree_state: WorkingTreeState | str | None


@dataclass(frozen=True, slots=True)
class SourceRunEvidence:
    run_id: str | None
    run_manifest_sha256: str | None
    analysis_sha256: str | None
    status: str
    admitted: bool
    producer: ProducerIdentity | None
    measurement_semantics: MeasurementSemantics | None
    subjects: tuple[SourceSubjectEvidence, ...]
    coordinates: tuple["SourceCoordinateEvidence", ...]


@dataclass(frozen=True, slots=True)
class SourceCoordinateEvidence:
    coordinate: CoordinateManifestEntry
    value: Number | None


@dataclass(frozen=True, slots=True)
class AdmittedBaselineArtifact:
    baseline: RatchetBaseline
    verified_sha256: str
    origin: BaselineArtifactOrigin


@dataclass(frozen=True, slots=True)
class AdmittedBaselineSubjects:
    artifact: AdmittedBaselineArtifact
    pairs: tuple[SubjectPair, ...]


def _payload_bytes(payload: str | bytes) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise BaselineAdmissionError("baseline_unreadable", "expected text or bytes")


def _origin(context: BaselineTrustContext) -> BaselineArtifactOrigin:
    try:
        origin = BaselineArtifactOrigin(context.origin)
    except (TypeError, ValueError) as exc:
        raise BaselineAdmissionError("unknown_baseline_origin") from exc
    if context.pull_request_mode:
        if origin is BaselineArtifactOrigin.CANDIDATE_WORKSPACE:
            raise BaselineAdmissionError("candidate_workspace_baseline_rejected")
        if origin not in {
            BaselineArtifactOrigin.PROTECTED_BASE_REVISION,
            BaselineArtifactOrigin.OWNER_CONTROLLED_ARTIFACT,
        }:
            raise BaselineAdmissionError("unprotected_pr_baseline_source")
    return origin


def _verify_external_digest(payload: bytes, expected: str | None) -> str:
    if expected is None:
        raise BaselineAdmissionError("external_digest_required")
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise BaselineAdmissionError("external_digest_invalid")
    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise BaselineAdmissionError(
            "external_digest_mismatch", f"expected={expected}, actual={actual}"
        )
    return actual


def _source_subjects(
    evidence: SourceRunEvidence,
) -> dict[str, SourceSubjectEvidence]:
    try:
        subjects = tuple(evidence.subjects)
    except TypeError as exc:
        raise BaselineAdmissionError("source_subject_evidence_invalid") from exc
    result: dict[str, SourceSubjectEvidence] = {}
    for index, subject in enumerate(subjects):
        if not isinstance(subject, SourceSubjectEvidence):
            raise BaselineAdmissionError(
                "source_subject_evidence_invalid", str(index)
            )
        key = subject.subject_key
        if not isinstance(key, str) or not key or key.strip() != key:
            raise BaselineAdmissionError("source_subject_identity_missing", str(index))
        if key in result:
            raise BaselineAdmissionError("source_subject_duplicate", key)
        commit = subject.analyzed_commit_sha
        if not isinstance(commit, str) or not _COMMIT_SHA.fullmatch(commit):
            raise BaselineAdmissionError("source_subject_commit_invalid", key)
        try:
            state = WorkingTreeState(subject.working_tree_state)
        except (TypeError, ValueError) as exc:
            raise BaselineAdmissionError("source_worktree_state_invalid", key) from exc
        if state is WorkingTreeState.DIRTY_WORKTREE:
            raise BaselineAdmissionError("dirty_baseline_source", key)
        if state not in {
            WorkingTreeState.COMMITTED_REVISION,
            WorkingTreeState.CLEAN_WORKTREE,
        }:
            raise BaselineAdmissionError("baseline_source_not_revision_bound", key)
        result[key] = subject
    return result


def _verify_source_run(
    baseline: RatchetBaseline,
    evidence: SourceRunEvidence,
) -> None:
    if not isinstance(evidence, SourceRunEvidence):
        raise BaselineAdmissionError("source_run_evidence_required")
    if evidence.status != "complete":
        raise BaselineAdmissionError("source_run_not_complete", evidence.status)
    if evidence.admitted is not True:
        raise BaselineAdmissionError("source_run_not_admitted")
    binding = baseline.source_run
    if evidence.run_id != binding.run_id:
        raise BaselineAdmissionError("source_run_id_mismatch")
    if evidence.run_manifest_sha256 != binding.run_manifest_sha256:
        raise BaselineAdmissionError("source_run_manifest_mismatch")
    if evidence.analysis_sha256 != binding.analysis_sha256:
        raise BaselineAdmissionError("source_analysis_mismatch")
    if evidence.producer != baseline.producer:
        raise BaselineAdmissionError("source_producer_identity_mismatch")
    if evidence.measurement_semantics != baseline.measurement_semantics:
        raise BaselineAdmissionError("source_measurement_semantics_mismatch")

    source_by_key = _source_subjects(evidence)
    baseline_by_key = {
        subject.subject_key: subject for subject in baseline.subjects
    }
    if set(source_by_key) != set(baseline_by_key):
        raise BaselineAdmissionError("source_subject_set_mismatch")
    for key, subject in baseline_by_key.items():
        if source_by_key[key].analyzed_commit_sha != subject.analyzed_commit_sha:
            raise BaselineAdmissionError("source_subject_commit_mismatch", key)

    try:
        coordinate_evidence = tuple(evidence.coordinates)
    except TypeError as exc:
        raise BaselineAdmissionError("source_coordinate_evidence_invalid") from exc
    source_by_coordinate: dict[
        tuple[str, str, str], SourceCoordinateEvidence
    ] = {}
    for index, item in enumerate(coordinate_evidence):
        if not isinstance(item, SourceCoordinateEvidence) or not isinstance(
            item.coordinate, CoordinateManifestEntry
        ):
            raise BaselineAdmissionError(
                "source_coordinate_evidence_invalid", str(index)
            )
        key = item.coordinate.key
        if key in source_by_coordinate:
            raise BaselineAdmissionError("source_coordinate_duplicate", "/".join(key))
        source_by_coordinate[key] = item

    baseline_by_coordinate = {
        coordinate.key: coordinate for coordinate in baseline.coordinate_manifest
    }
    if set(source_by_coordinate) != set(baseline_by_coordinate):
        raise BaselineAdmissionError("source_coordinate_set_mismatch")
    rules = {rule.rule_id: rule for rule in baseline.rules}
    observations = {item.key: item for item in baseline.observations}
    for key, coordinate in baseline_by_coordinate.items():
        source = source_by_coordinate[key]
        if source.coordinate != coordinate:
            raise BaselineAdmissionError(
                "source_coordinate_manifest_mismatch", "/".join(key)
            )
        observation = observations.get(key)
        if coordinate.status is CoordinateStatus.OBSERVED:
            if observation is None:
                raise BaselineAdmissionError(
                    "baseline_observation_missing", "/".join(key)
                )
            try:
                source_value = canonical_metric_number(
                    rules[coordinate.rule_id].metric,
                    source.value,
                    "source observation",
                )
            except RatchetContractError as exc:
                raise BaselineAdmissionError(
                    "source_observation_invalid", "/".join(key)
                ) from exc
            if source_value != observation.baseline_value:
                raise BaselineAdmissionError(
                    "source_observation_mismatch", "/".join(key)
                )
        elif source.value is not None or observation is not None:
            raise BaselineAdmissionError(
                "non_observed_coordinate_has_value", "/".join(key)
            )


def admit_baseline(
    payload: str | bytes,
    *,
    trust: BaselineTrustContext,
    source_run: SourceRunEvidence,
) -> AdmittedBaselineArtifact:
    """Verify external trust and return an immutable admitted Contract 1.0."""

    if not isinstance(trust, BaselineTrustContext):
        raise BaselineAdmissionError("trust_context_required")
    origin = _origin(trust)
    raw = _payload_bytes(payload)
    verified_digest = _verify_external_digest(raw, trust.expected_sha256)
    try:
        baseline = parse_baseline_json(raw)
    except RatchetContractError as exc:
        raise BaselineAdmissionError("baseline_contract_invalid", str(exc)) from exc
    if canonical_bytes(baseline) != raw:
        raise BaselineAdmissionError("baseline_not_canonical")
    _verify_source_run(baseline, source_run)
    return AdmittedBaselineArtifact(
        baseline=baseline,
        verified_sha256=verified_digest,
        origin=origin,
    )


RevisionResolver = Callable[..., ResolvedRevisionPair]


def require_revision_compatibility(
    pairs: Iterable[SubjectPair],
    revision_sources: Mapping[str, str | Path],
    *,
    timeout: int = 300,
    resolver: RevisionResolver = resolve_revision_pair,
) -> None:
    """Require baseline commits to be ancestors using the existing resolver."""

    for pair in pairs:
        key = pair.subject_key
        source = revision_sources.get(key)
        if source is None:
            raise BaselineAdmissionError("revision_source_missing", key)
        baseline_commit = pair.baseline.analyzed_commit_sha
        current_commit = pair.current.analyzed_commit_sha
        if not isinstance(current_commit, str) or not _COMMIT_SHA.fullmatch(
            current_commit
        ):
            raise BaselineAdmissionError("current_commit_invalid", key)
        try:
            resolved = resolver(
                source,
                baseline_commit,
                current_commit,
                timeout=timeout,
            )
        except RevisionUnavailable as exc:
            raise BaselineAdmissionError(
                "revision_unavailable", f"{key}: {exc.reason}"
            ) from exc
        if resolved.base.sha != baseline_commit or resolved.head.sha != current_commit:
            raise BaselineAdmissionError("resolved_commit_mismatch", key)
        if resolved.ancestry == "not_ancestor":
            raise BaselineAdmissionError("baseline_not_ancestor", key)
        if resolved.ancestry != "ancestor":
            raise BaselineAdmissionError("revision_ancestry_unknown", key)


def admit_and_pair_baseline(
    payload: str | bytes,
    *,
    trust: BaselineTrustContext,
    source_run: SourceRunEvidence,
    current_subjects: Iterable[CurrentSubject],
    revision_sources: Mapping[str, str | Path],
    revision_timeout: int = 300,
    revision_resolver: RevisionResolver = resolve_revision_pair,
) -> AdmittedBaselineSubjects:
    """Compose BR2 gates without performing any metric comparison or policy."""

    artifact = admit_baseline(payload, trust=trust, source_run=source_run)
    pairs = pair_subjects(artifact.baseline, current_subjects)
    require_contract_compatibility(pairs)
    require_revision_compatibility(
        pairs,
        revision_sources,
        timeout=revision_timeout,
        resolver=revision_resolver,
    )
    return AdmittedBaselineSubjects(artifact=artifact, pairs=pairs)


__all__ = [
    "AdmittedBaselineArtifact",
    "AdmittedBaselineSubjects",
    "BaselineAdmissionError",
    "BaselineArtifactOrigin",
    "BaselineTrustContext",
    "SourceRunEvidence",
    "SourceCoordinateEvidence",
    "SourceSubjectEvidence",
    "admit_and_pair_baseline",
    "admit_baseline",
    "require_revision_compatibility",
]
