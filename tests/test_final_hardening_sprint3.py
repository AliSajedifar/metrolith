"""Final Hardening Sprint 3 trusted Ratchet capture adversarial gates."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from modules.ratchet import (
    BaselineAdmissionError,
    BaselineArtifactOrigin,
    BaselineCaptureError,
    BaselineTrustContext,
    RatchetContractError,
    RatchetDirection,
    RatchetCheckRequest,
    RatchetCheckServiceError,
    RatchetRule,
    SemanticsCompatibilityError,
    admit_baseline,
    canonical_bytes,
    capture_baseline,
    evaluate_ratchet_check,
    parse_baseline_json,
    require_semantics_compatibility,
    semantics_from_manifest,
    source_run_evidence_from_run,
)
from tests.test_policy_v2_check import RunBuilder
from tests.ratchet_contract_fixtures import mark_run_producer_clean
from validation.artifact_io.reader import open_run
from validation.artifact_io.schema_store import validate_document


def _rule(*, language: bool = False) -> RatchetRule:
    return RatchetRule(
        rule_id=("ratchet.language_loc" if language else "ratchet.repository_loc"),
        metric=("language.lines_of_code" if language else "repository.lines_of_code"),
        metric_contract="metrics",
        scope=("language" if language else "repository"),
        direction=RatchetDirection.INCREASE_IS_WORSE,
        max_regression=0,
    )


@pytest.fixture(scope="module")
def valid_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run = RunBuilder.build(tmp_path_factory.mktemp("sprint3-valid-run"))
    mark_run_producer_clean(run)
    return run


def _copy_run(valid_run: Path, tmp_path: Path) -> Path:
    target = tmp_path / "run"
    shutil.copytree(valid_run, target)
    return target


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _set_repository_loc(run: Path, value, *, projection: bool = True) -> None:
    analysis_path = run / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis[0]["metrics"]["aggregate"]["lines_of_code"] = value
    _write_json(analysis_path, analysis)
    if projection:
        for path in (run / "repositories").glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            document["metrics"]["aggregate"]["lines_of_code"] = value
            _write_json(path, document)


def _trust(payload: bytes) -> BaselineTrustContext:
    return BaselineTrustContext(
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        origin=BaselineArtifactOrigin.OWNER_CONTROLLED_ARTIFACT,
    )


def _canonical_raw(raw: dict) -> bytes:
    return json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_capture_api_has_no_observation_value_input() -> None:
    assert tuple(inspect.signature(capture_baseline).parameters) == (
        "run_directory",
        "rules",
    )


def test_valid_run_capture_is_canonical_traceable_and_admitted(valid_run: Path) -> None:
    captured = capture_baseline(valid_run, (_rule(),))
    view = open_run(valid_run)

    assert canonical_bytes(parse_baseline_json(captured.payload)) == captured.payload
    assert captured.baseline.source_run.run_id == view.run_id
    assert captured.baseline.source_run.run_manifest_sha256 == hashlib.sha256(
        view.reader.document_bytes("run_manifest.json")
    ).hexdigest()
    assert captured.baseline.source_run.analysis_sha256 == hashlib.sha256(
        view.reader.document_bytes("analysis.json")
    ).hexdigest()
    assert captured.baseline.coordinate_manifest[0].metric == "repository.lines_of_code"
    assert captured.baseline.observations[0].baseline_value == view.repositories[0][
        "metrics"
    ]["aggregate"]["lines_of_code"]
    assert validate_document(
        "ratchet_baseline", captured.baseline.to_dict(), "baseline.json"
    ) == []

    admitted = admit_baseline(
        captured.payload,
        trust=_trust(captured.payload),
        source_run=captured.source_run_evidence,
    )
    assert admitted.baseline == captured.baseline


def test_missing_status_and_failed_run_are_refused(
    valid_run: Path, tmp_path: Path
) -> None:
    missing = _copy_run(valid_run, tmp_path / "missing")
    (missing / "run_status.json").unlink()
    with pytest.raises(BaselineCaptureError) as absent:
        capture_baseline(missing, (_rule(),))
    assert absent.value.code == "source_run_not_successful"

    failed = _copy_run(valid_run, tmp_path / "failed")
    status_path = failed / "run_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["status"] = "failed"
    _write_json(status_path, status)
    with pytest.raises(BaselineCaptureError) as rejected:
        capture_baseline(failed, (_rule(),))
    assert rejected.value.code == "source_run_not_successful"


def test_dirty_evaluator_source_identity_is_refused(
    valid_run: Path, tmp_path: Path
) -> None:
    run = _copy_run(valid_run, tmp_path)
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiler_git_dirty"] = True
    manifest["benchmark_environment"]["profiler_git_dirty"] = True
    _write_json(manifest_path, manifest)
    environment_path = run / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["profiler_git_dirty"] = True
    _write_json(environment_path, environment)

    with pytest.raises(BaselineCaptureError) as raised:
        capture_baseline(run, (_rule(),))
    assert raised.value.code == "source_evaluator_identity_not_reproducible"


def test_dirty_subject_snapshot_is_refused(valid_run: Path, tmp_path: Path) -> None:
    run = _copy_run(valid_run, tmp_path)
    analysis_path = run / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis[0]["working_tree_state"] = "dirty_worktree"
    _write_json(analysis_path, analysis)
    for path in (run / "repositories").glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["working_tree_state"] = "dirty_worktree"
        _write_json(path, document)

    with pytest.raises(BaselineCaptureError) as raised:
        capture_baseline(run, (_rule(),))
    assert raised.value.code == "source_subject_dirty"


def test_modified_analysis_is_detected_before_capture(
    valid_run: Path, tmp_path: Path
) -> None:
    run = _copy_run(valid_run, tmp_path)
    current = open_run(run).repositories[0]["metrics"]["aggregate"]["lines_of_code"]
    _set_repository_loc(run, current + 1, projection=False)

    with pytest.raises(BaselineCaptureError) as raised:
        capture_baseline(run, (_rule(),))
    assert raised.value.code == "source_analysis_projection_mismatch"


def test_negative_loc_is_rejected_even_when_projections_agree(
    valid_run: Path, tmp_path: Path
) -> None:
    run = _copy_run(valid_run, tmp_path)
    _set_repository_loc(run, -1)

    with pytest.raises(BaselineCaptureError) as raised:
        capture_baseline(run, (_rule(),))
    assert raised.value.code in {
        "source_run_artifact_integrity_failed",
        "source_observation_domain_invalid",
    }


def test_impossible_cyclomatic_value_is_rejected_by_metric_domain(
    valid_run: Path, tmp_path: Path
) -> None:
    run = _copy_run(valid_run, tmp_path)
    analysis_path = run / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis[0]["metrics"]["complexity"]["aggregate"][
        "cyclomatic_complexity_max"
    ] = 0
    _write_json(analysis_path, analysis)
    for path in (run / "repositories").glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["metrics"]["complexity"]["aggregate"][
            "cyclomatic_complexity_max"
        ] = 0
        _write_json(path, document)
    rule = RatchetRule(
        rule_id="ratchet.repository_max_cc",
        metric="repository.cyclomatic_complexity_max",
        metric_contract="complexity",
        scope="repository",
        direction="increase_is_worse",
        max_regression=0,
    )

    with pytest.raises(BaselineCaptureError) as raised:
        capture_baseline(run, (rule,))
    assert raised.value.code == "source_observation_domain_invalid"


def test_manual_forged_observation_json_is_refused(valid_run: Path) -> None:
    captured = capture_baseline(valid_run, (_rule(),))
    raw = captured.baseline.to_dict()
    raw["observations"][0]["baseline_value"] += 1
    forged = _canonical_raw(raw)

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            forged,
            trust=_trust(forged),
            source_run=captured.source_run_evidence,
        )
    assert raised.value.code == "source_observation_mismatch"


def test_source_analysis_changed_after_capture_is_refused(
    valid_run: Path, tmp_path: Path
) -> None:
    run = _copy_run(valid_run, tmp_path)
    captured = capture_baseline(run, (_rule(),))
    current = captured.baseline.observations[0].baseline_value
    _set_repository_loc(run, current + 1)
    changed_evidence = source_run_evidence_from_run(run, (_rule(),))

    with pytest.raises(BaselineAdmissionError) as raised:
        admit_baseline(
            captured.payload,
            trust=_trust(captured.payload),
            source_run=changed_evidence,
        )
    assert raised.value.code == "source_analysis_mismatch"


def test_unknown_coordinate_and_missing_required_language_fail_closed(
    valid_run: Path,
) -> None:
    repository = capture_baseline(valid_run, (_rule(),))
    unknown = repository.baseline.to_dict()
    unknown["coordinate_manifest"][0]["subject_key"] = "github.com/acme/unknown"
    unknown["observations"][0]["subject_key"] = "github.com/acme/unknown"
    payload = _canonical_raw(unknown)
    with pytest.raises(BaselineAdmissionError) as bad_coordinate:
        admit_baseline(
            payload,
            trust=_trust(payload),
            source_run=repository.source_run_evidence,
        )
    assert bad_coordinate.value.code == "baseline_contract_invalid"

    language = capture_baseline(valid_run, (_rule(language=True),))
    raw = language.baseline.to_dict()
    removed = next(
        item
        for item in raw["coordinate_manifest"]
        if item["status"] == "observed"
    )
    raw["coordinate_manifest"].remove(removed)
    raw["observations"] = [
        item
        for item in raw["observations"]
        if not (
            item["rule_id"] == removed["rule_id"]
            and item["subject_key"] == removed["subject_key"]
            and item["language"] == removed["language"]
        )
    ]
    incomplete = canonical_bytes(parse_baseline_json(_canonical_raw(raw)))
    with pytest.raises(BaselineAdmissionError) as missing_language:
        admit_baseline(
            incomplete,
            trust=_trust(incomplete),
            source_run=language.source_run_evidence,
        )
    assert missing_language.value.code == "source_coordinate_set_mismatch"


def test_semantics_fingerprint_selects_only_measurement_relevant_fields(
    valid_run: Path,
) -> None:
    manifest = copy.deepcopy(dict(open_run(valid_run).manifest))
    same = copy.deepcopy(manifest)
    same["start_timestamp"] = "2099-01-01T00:00:00Z"
    same["end_timestamp"] = "2099-01-01T00:00:01Z"
    same["platform"] = "another-host-platform"
    same["resolved_paths"]["workspace_root"] = "Z:\\different\\workspace"
    same["effective_configuration"]["workspace_root"] = "/different/workspace"

    original = semantics_from_manifest(manifest)
    irrelevant_change = semantics_from_manifest(same)
    assert original.fingerprint_sha256 == irrelevant_change.fingerprint_sha256

    exclusion = copy.deepcopy(manifest)
    exclusion["effective_configuration"]["exclusion_policy"][
        "excluded_directories"
    ].append("semantic-change")
    grammar = copy.deepcopy(manifest)
    grammar["benchmark_environment"]["grammar_versions"][
        "tree-sitter-javascript"
    ] = "99.0.0"
    parser_runtime = copy.deepcopy(manifest)
    parser_runtime["benchmark_environment"]["tree_sitter_version"] = "99.0.0"

    changed_exclusion = semantics_from_manifest(exclusion)
    changed_grammar = semantics_from_manifest(grammar)
    changed_parser_runtime = semantics_from_manifest(parser_runtime)
    assert changed_exclusion.fingerprint_sha256 != original.fingerprint_sha256
    assert changed_grammar.fingerprint_sha256 != original.fingerprint_sha256
    assert changed_parser_runtime.fingerprint_sha256 != original.fingerprint_sha256
    with pytest.raises(SemanticsCompatibilityError) as incompatible:
        require_semantics_compatibility(original, changed_parser_runtime)
    assert incompatible.value.code == "measurement_semantics_mismatch"


def test_ratchet_admission_refuses_incompatible_current_semantics(
    valid_run: Path, tmp_path: Path
) -> None:
    captured = capture_baseline(valid_run, (_rule(),))
    current = _copy_run(valid_run, tmp_path)
    manifest_path = current / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["benchmark_environment"]["grammar_versions"][
        "tree-sitter-javascript"
    ] = "99.0.0"
    _write_json(manifest_path, manifest)
    request = RatchetCheckRequest(
        baseline_payload=captured.payload,
        trust=_trust(captured.payload),
        source_run=captured.source_run_evidence,
        revision_sources={},
    )

    with pytest.raises(RatchetCheckServiceError) as raised:
        evaluate_ratchet_check(open_run(current), request)
    assert raised.value.phase == "admission"
    assert raised.value.code == "measurement_semantics_mismatch"


def test_ratchet_admission_refuses_incomplete_current_coordinates(
    valid_run: Path, tmp_path: Path
) -> None:
    rule = _rule(language=True)
    captured = capture_baseline(valid_run, (rule,))
    current = _copy_run(valid_run, tmp_path)
    for path in [current / "analysis.json", *(current / "repositories").glob("*.json")]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = raw[0] if isinstance(raw, list) else raw
        document["metrics"]["by_language"].pop("python", None)
        document["metrics"]["complexity"]["by_language"].pop("Python", None)
        _write_json(path, raw)
    request = RatchetCheckRequest(
        baseline_payload=captured.payload,
        trust=_trust(captured.payload),
        source_run=captured.source_run_evidence,
        revision_sources={},
    )

    with pytest.raises(RatchetCheckServiceError) as raised:
        evaluate_ratchet_check(open_run(current), request)
    assert raised.value.phase == "admission"
    assert raised.value.code in {
        "language_set_mismatch",
        "coordinate_manifest_mismatch",
    }


def test_integer_numeric_identity_is_canonical_and_nonfinite_is_rejected(
    valid_run: Path,
) -> None:
    captured = capture_baseline(valid_run, (_rule(),))
    integer = replace(
        captured.baseline,
        observations=(
            replace(captured.baseline.observations[0], baseline_value=1),
        ),
    )
    integral_float = replace(
        captured.baseline,
        observations=(
            replace(captured.baseline.observations[0], baseline_value=1.0),
        ),
    )
    assert integer.observations[0].baseline_value == 1
    assert integral_float.observations[0].baseline_value == 1
    assert canonical_bytes(integer) == canonical_bytes(integral_float)

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(RatchetContractError):
            replace(
                captured.baseline,
                observations=(
                    replace(captured.baseline.observations[0], baseline_value=value),
                ),
            )


def test_strict_json_refuses_nan_and_infinity_tokens(valid_run: Path) -> None:
    raw = capture_baseline(valid_run, (_rule(),)).baseline.to_dict()
    for value in (float("nan"), float("inf"), float("-inf")):
        raw["observations"][0]["baseline_value"] = value
        with pytest.raises(RatchetContractError):
            parse_baseline_json(json.dumps(raw, sort_keys=True).encode("utf-8"))
