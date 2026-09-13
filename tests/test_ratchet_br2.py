"""BR2 gates for trusted baseline admission and exact subject pairing."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from modules.ratchet import (
    BaselineAdmissionError,
    BaselineArtifactOrigin,
    BaselineObservation,
    BaselineTrustContext,
    ContractCompatibilityError,
    CurrentSubject,
    MetricContractBinding,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetDirection,
    RatchetRule,
    RatchetScope,
    SourceRunBinding,
    SourceCoordinateEvidence,
    SourceRunEvidence,
    SourceSubjectEvidence,
    SubjectPairingError,
    admit_and_pair_baseline,
    admit_baseline,
    canonical_bytes,
    pair_subjects,
    require_contract_compatibility,
    require_revision_compatibility,
)
from tests.ratchet_contract_fixtures import ratchet_baseline, source_binding
from modules.vocabularies import WorkingTreeState


SUBJECT_A = "github.com/acme/alpha"
SUBJECT_Z = "github.com/acme/zeta"
BASE_COMMIT = "a" * 40
CURRENT_COMMIT = "b" * 40
MANIFEST_SHA = "c" * 64
BASE_SCOPE = "sha256:" + ("1" * 64)
CURRENT_SCOPE = "sha256:" + ("2" * 64)


def _subject(key: str, commit: str, scope_hash: str = BASE_SCOPE) -> PortableSubjectBinding:
    return PortableSubjectBinding(
        subject_key=key,
        subject_key_basis="remote_locator",
        analyzed_commit_sha=commit,
        artifact_schema_version="1.11.0",
        metric_contracts=(MetricContractBinding("complexity", "2.0.0"),),
        analysis_scope_hash=scope_hash,
        analysis_scope_hash_version="2.0.0",
    )


def _baseline(
    subjects: tuple[tuple[str, str], ...] = ((SUBJECT_A, BASE_COMMIT),),
    *,
    language: str | None = None,
) -> RatchetBaseline:
    scope = RatchetScope.LANGUAGE if language else RatchetScope.REPOSITORY
    metric = (
        "language.cognitive_complexity_max"
        if language
        else "repository.cognitive_complexity_max"
    )
    rule = RatchetRule(
        rule_id="ratchet.max_cognitive",
        metric=metric,
        metric_contract="complexity",
        scope=scope,
        direction=RatchetDirection.INCREASE_IS_WORSE,
        max_regression=0,
    )
    bindings = tuple(_subject(key, commit) for key, commit in subjects)
    observations = tuple(
        BaselineObservation(
            rule_id=rule.rule_id,
            subject_key=key,
            language=language,
            baseline_value=10,
        )
        for key, _commit in subjects
    )
    return ratchet_baseline(
        source_run=source_binding(manifest_sha=MANIFEST_SHA),
        subjects=bindings,
        rules=(rule,),
        observations=observations,
    )


def _source_evidence(
    baseline: RatchetBaseline,
    *,
    status: str = "complete",
    admitted: bool = True,
    working_tree_state: WorkingTreeState = WorkingTreeState.COMMITTED_REVISION,
) -> SourceRunEvidence:
    return SourceRunEvidence(
        run_id=baseline.source_run.run_id,
        run_manifest_sha256=baseline.source_run.run_manifest_sha256,
        analysis_sha256=baseline.source_run.analysis_sha256,
        status=status,
        admitted=admitted,
        producer=baseline.producer,
        measurement_semantics=baseline.measurement_semantics,
        subjects=tuple(
            SourceSubjectEvidence(
                subject_key=subject.subject_key,
                analyzed_commit_sha=subject.analyzed_commit_sha,
                working_tree_state=working_tree_state,
            )
            for subject in baseline.subjects
        ),
        coordinates=tuple(
            SourceCoordinateEvidence(
                coordinate,
                next(
                    (
                        observation.baseline_value
                        for observation in baseline.observations
                        if observation.key == coordinate.key
                    ),
                    None,
                ),
            )
            for coordinate in baseline.coordinate_manifest
        ),
    )


def _trust(
    payload: bytes,
    *,
    expected: str | None = None,
    origin: BaselineArtifactOrigin = BaselineArtifactOrigin.PROTECTED_BASE_REVISION,
    pull_request_mode: bool = False,
) -> BaselineTrustContext:
    return BaselineTrustContext(
        expected_sha256=(hashlib.sha256(payload).hexdigest() if expected is None else expected),
        origin=origin,
        pull_request_mode=pull_request_mode,
    )


def _current(
    subject: PortableSubjectBinding,
    *,
    commit: str = CURRENT_COMMIT,
    key: str | None = None,
    basis: str = "remote_locator",
    languages: tuple[str, ...] = (),
    contracts: tuple[MetricContractBinding, ...] | None = None,
) -> CurrentSubject:
    return CurrentSubject(
        subject_key=subject.subject_key if key is None else key,
        subject_key_basis=basis,
        analyzed_commit_sha=commit,
        artifact_schema_version=subject.artifact_schema_version,
        metric_contracts=(
            subject.metric_contracts if contracts is None else contracts
        ),
        analysis_scope_hash=CURRENT_SCOPE,
        analysis_scope_hash_version=subject.analysis_scope_hash_version,
        languages=languages,
        admitted=True,
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "subject"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "ratchet@example.invalid")
    _git(repository, "config", "user.name", "Ratchet Tests")
    (repository / "metric.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "metric.txt")
    _git(repository, "commit", "--quiet", "-m", "base")
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "metric.txt").write_text("current\n", encoding="utf-8")
    _git(repository, "commit", "--quiet", "-am", "current")
    current = _git(repository, "rev-parse", "HEAD")
    return repository, base, current


def test_valid_admission_verifies_external_digest_and_source_binding() -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)

    admitted = admit_baseline(
        payload,
        trust=_trust(payload),
        source_run=_source_evidence(baseline),
    )

    assert admitted.baseline == baseline
    assert admitted.verified_sha256 == hashlib.sha256(payload).hexdigest()
    assert admitted.origin is BaselineArtifactOrigin.PROTECTED_BASE_REVISION


def test_bad_external_digest_is_rejected_before_admission() -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            payload,
            trust=_trust(payload, expected="0" * 64),
            source_run=_source_evidence(baseline),
        )
    assert raised.value.code == "external_digest_mismatch"


def test_self_attested_digest_cannot_replace_external_digest() -> None:
    baseline = _baseline()
    raw = baseline.to_dict()
    raw["sha256"] = hashlib.sha256(canonical_bytes(baseline)).hexdigest()
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            payload,
            trust=BaselineTrustContext(
                expected_sha256=None,
                origin=BaselineArtifactOrigin.LOCAL_FILE,
            ),
            source_run=_source_evidence(baseline),
        )
    assert raised.value.code == "external_digest_required"


def test_dirty_baseline_source_is_rejected() -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)
    evidence = _source_evidence(
        baseline, working_tree_state=WorkingTreeState.DIRTY_WORKTREE
    )

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(payload, trust=_trust(payload), source_run=evidence)
    assert raised.value.code == "dirty_baseline_source"


@pytest.mark.parametrize("status", ["failed", "partial"])
def test_failed_or_partial_source_run_is_rejected(status: str) -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            payload,
            trust=_trust(payload),
            source_run=_source_evidence(baseline, status=status),
        )
    assert raised.value.code == "source_run_not_complete"


def test_unadmitted_source_run_is_rejected() -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            payload,
            trust=_trust(payload),
            source_run=_source_evidence(baseline, admitted=False),
        )
    assert raised.value.code == "source_run_not_admitted"


def test_candidate_workspace_baseline_is_rejected_in_pr_mode() -> None:
    baseline = _baseline()
    payload = canonical_bytes(baseline)

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            payload,
            trust=_trust(
                payload,
                origin=BaselineArtifactOrigin.CANDIDATE_WORKSPACE,
                pull_request_mode=True,
            ),
            source_run=_source_evidence(baseline),
        )
    assert raised.value.code == "candidate_workspace_baseline_rejected"


def test_exact_pairing_is_key_based_and_order_independent() -> None:
    baseline = _baseline(((SUBJECT_Z, BASE_COMMIT), (SUBJECT_A, BASE_COMMIT)))
    by_key = {subject.subject_key: subject for subject in baseline.subjects}
    current = (_current(by_key[SUBJECT_Z]), _current(by_key[SUBJECT_A]))

    pairs = pair_subjects(baseline, current)

    assert [pair.subject_key for pair in pairs] == [SUBJECT_A, SUBJECT_Z]
    assert all(pair.baseline.subject_key == pair.current.subject_key for pair in pairs)


def test_duplicate_current_subjects_are_rejected_without_first_match() -> None:
    baseline = _baseline()
    current = _current(baseline.subjects[0])

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (current, current))
    assert raised.value.code == "duplicate_current_subject"


def test_local_fallback_current_identity_is_rejected() -> None:
    baseline = _baseline()
    current = _current(baseline.subjects[0], basis="local_fallback")

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (current,))
    assert raised.value.code == "local_fallback_identity"


def test_subject_suffix_is_not_used_as_identity() -> None:
    baseline = _baseline()
    current = _current(
        baseline.subjects[0], key="gitlab.example/other/alpha"
    )

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (current,))
    assert raised.value.code == "subject_set_mismatch"


def test_subject_set_mismatch_is_rejected() -> None:
    baseline = _baseline(((SUBJECT_A, BASE_COMMIT), (SUBJECT_Z, BASE_COMMIT)))
    current = _current(baseline.subjects[0])

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (current,))
    assert raised.value.code == "subject_set_mismatch"


def test_repository_rename_is_irrelevant_when_portable_key_is_stable() -> None:
    baseline = _baseline()
    # Repository display names/URLs are intentionally absent from the pairing
    # input. A rename therefore pairs when the portable key remains stable.
    current_after_rename = _current(
        baseline.subjects[0], commit=CURRENT_COMMIT, key=SUBJECT_A
    )

    pairs = pair_subjects(baseline, (current_after_rename,))

    assert pairs[0].subject_key == SUBJECT_A
    assert pairs[0].current.analyzed_commit_sha == CURRENT_COMMIT


def test_language_pairing_requires_exact_canonical_identity() -> None:
    baseline = _baseline(language="python")
    subject = baseline.subjects[0]

    pair = pair_subjects(
        baseline, (_current(subject, languages=("python",)),)
    )[0]
    assert pair.languages == ("python",)

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (_current(subject, languages=("java",)),))
    assert raised.value.code == "language_set_mismatch"


def test_duplicate_normalized_languages_are_rejected() -> None:
    baseline = _baseline(language="python")
    current = _current(
        baseline.subjects[0], languages=("python", "PYTHON")
    )

    with pytest.raises(SubjectPairingError) as raised:
        pair_subjects(baseline, (current,))
    assert raised.value.code == "duplicate_language_identity"


def test_contract_compatibility_requires_exact_baseline_versions() -> None:
    baseline = _baseline()
    wrong = _current(
        baseline.subjects[0],
        contracts=(MetricContractBinding("complexity", "9.0.0"),),
    )
    pairs = pair_subjects(baseline, (wrong,))

    with pytest.raises(ContractCompatibilityError) as raised:
        require_contract_compatibility(pairs)
    assert raised.value.code == "metric_contract_mismatch"


def test_valid_admission_pairing_and_ancestor_revision(tmp_path: Path) -> None:
    repository, base, current = _repository(tmp_path)
    baseline = _baseline(((SUBJECT_A, base),))
    payload = canonical_bytes(baseline)
    current_subject = _current(baseline.subjects[0], commit=current)

    admitted = admit_and_pair_baseline(
        payload,
        trust=_trust(payload),
        source_run=_source_evidence(baseline),
        current_subjects=(current_subject,),
        revision_sources={SUBJECT_A: repository},
    )

    assert admitted.artifact.baseline == baseline
    assert admitted.pairs[0].baseline.analyzed_commit_sha == base
    assert admitted.pairs[0].current.analyzed_commit_sha == current


def test_wrong_current_commit_is_rejected_by_existing_revision_utility(
    tmp_path: Path,
) -> None:
    repository, base, _current_commit = _repository(tmp_path)
    baseline = _baseline(((SUBJECT_A, base),))
    pairs = pair_subjects(
        baseline, (_current(baseline.subjects[0], commit="f" * 40),)
    )

    with pytest.raises(BaselineAdmissionError) as raised:
        require_revision_compatibility(pairs, {SUBJECT_A: repository})
    assert raised.value.code == "revision_unavailable"


def test_non_ancestor_commit_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "forked"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "ratchet@example.invalid")
    _git(repository, "config", "user.name", "Ratchet Tests")
    (repository / "root.txt").write_text("root\n", encoding="utf-8")
    _git(repository, "add", "root.txt")
    _git(repository, "commit", "--quiet", "-m", "root")
    root = _git(repository, "rev-parse", "HEAD")

    _git(repository, "switch", "--quiet", "-c", "left", root)
    (repository / "left.txt").write_text("left\n", encoding="utf-8")
    _git(repository, "add", "left.txt")
    _git(repository, "commit", "--quiet", "-m", "left")
    left = _git(repository, "rev-parse", "HEAD")

    _git(repository, "switch", "--quiet", "-c", "right", root)
    (repository / "right.txt").write_text("right\n", encoding="utf-8")
    _git(repository, "add", "right.txt")
    _git(repository, "commit", "--quiet", "-m", "right")
    right = _git(repository, "rev-parse", "HEAD")

    baseline = _baseline(((SUBJECT_A, left),))
    pairs = pair_subjects(
        baseline, (_current(baseline.subjects[0], commit=right),)
    )

    with pytest.raises(BaselineAdmissionError) as raised:
        require_revision_compatibility(pairs, {SUBJECT_A: repository})
    assert raised.value.code == "baseline_not_ancestor"
