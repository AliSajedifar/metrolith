"""BR4 gates for Ratchet V1 canonical findings and Check Result integration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.policy import check as check_module
from modules.policy import findings as finding_module
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_policy_v2,
)
from modules.ratchet import (
    BaselineArtifactOrigin,
    BaselineObservation,
    BaselineTrustContext,
    MetricContractBinding,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetDirection,
    RatchetRule,
    RatchetSeverity,
    SourceRunBinding,
    SourceRunEvidence,
    SourceCoordinateEvidence,
    SourceSubjectEvidence,
    canonical_bytes,
    producer_from_manifest,
    semantics_from_manifest,
)
from tests.ratchet_contract_fixtures import (
    mark_run_producer_clean,
    ratchet_baseline,
    source_binding,
)
from modules.ratchet.check_service import (
    FAILURE_RATCHET_ADMISSION,
    RATCHET_ADMISSION_FAILED,
    RATCHET_CHECK_RESULT_FORMAT_VERSION,
    RATCHET_EVALUATED,
    RATCHET_EVALUATED_WITH_NOT_EVALUABLE,
    RatchetCheckRequest,
)
from modules.revision_source import ResolvedRevision, ResolvedRevisionPair
from modules.vocabularies import WorkingTreeState
from tests.test_policy_v2_check import RunFixture as _RunFixture
from validation.artifact_io.reader import open_run
from validation.artifact_io.schema_store import validate_document


TODAY = date(2026, 8, 23)


class _ViewOverride:
    def __init__(self, base, repositories):
        self._base = base
        self.repositories = tuple(repositories)

    def __getattr__(self, name):
        return getattr(self._base, name)


def _resolver(source, base, head, *, timeout=300):
    del timeout
    return ResolvedRevisionPair(
        source=Path(source),
        base=ResolvedRevision("base", base, base),
        head=ResolvedRevision("head", head, head),
        shallow_repository=False,
        worktree_state="clean",
        ancestry="ancestor",
    )


def _policy():
    return load_policy_v2(
        {
            "policy_document_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
            "name": "ratchet-integration-policy",
            "metric_rules": [
                {
                    "id": "current.source_files",
                    "metric": "repository.source_files",
                    "operator": "gt",
                    "threshold": 1_000_000,
                }
            ],
        }
    )


class RatchetCheckIntegrationTests(_RunFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        mark_run_producer_clean(cls.shared_run)

    def _view(self):
        return open_run(self.shared_run)

    def _current_loc(self) -> int:
        repository = self._view().repositories[0]
        return int(repository["metrics"]["aggregate"]["lines_of_code"])

    def _request(
        self,
        baseline_value: int | float,
        *,
        tolerance: int | float = 0,
        subject_key: str | None = None,
        severity: RatchetSeverity = RatchetSeverity.VIOLATION,
    ) -> RatchetCheckRequest:
        view = self._view()
        repository = view.repositories[0]
        acquisition = repository["acquisition"]
        key = subject_key or str(repository["subject_key"])
        commit = str(acquisition["analyzed_commit_sha"])
        contract_version = str(repository["metric_contract_version"])
        rule = RatchetRule(
            rule_id="ratchet.repository_loc",
            metric="repository.lines_of_code",
            metric_contract="metrics",
            scope="repository",
            direction=RatchetDirection.INCREASE_IS_WORSE,
            max_regression=tolerance,
            severity=severity,
        )
        baseline = ratchet_baseline(
            source_run=source_binding(run_id="trusted-baseline-run"),
            subjects=(
                PortableSubjectBinding(
                    subject_key=key,
                    subject_key_basis="remote_locator",
                    analyzed_commit_sha=commit,
                    artifact_schema_version=str(
                        repository["artifact_schema_version"]
                    ),
                    metric_contracts=(
                        MetricContractBinding("metrics", contract_version),
                    ),
                    analysis_scope_hash=str(repository["analysis_scope_hash"]),
                    analysis_scope_hash_version=str(
                        repository["analysis_scope_hash_version"]
                    ),
                ),
            ),
            rules=(rule,),
            observations=(
                BaselineObservation(
                    rule_id=rule.rule_id,
                    subject_key=key,
                    baseline_value=baseline_value,
                ),
            ),
        )
        baseline = replace(
            baseline,
            producer=producer_from_manifest(view.manifest),
            measurement_semantics=semantics_from_manifest(view.manifest),
        )
        payload = canonical_bytes(baseline)
        return RatchetCheckRequest(
            baseline_payload=payload,
            trust=BaselineTrustContext(
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                origin=BaselineArtifactOrigin.PROTECTED_BASE_REVISION,
                pull_request_mode=True,
            ),
            source_run=SourceRunEvidence(
                run_id=baseline.source_run.run_id,
                run_manifest_sha256=baseline.source_run.run_manifest_sha256,
                analysis_sha256=baseline.source_run.analysis_sha256,
                status="complete",
                admitted=True,
                producer=baseline.producer,
                measurement_semantics=baseline.measurement_semantics,
                subjects=(
                    SourceSubjectEvidence(
                        subject_key=key,
                        analyzed_commit_sha=commit,
                        working_tree_state=(
                            WorkingTreeState.COMMITTED_REVISION
                        ),
                    ),
                ),
                coordinates=tuple(
                    SourceCoordinateEvidence(
                        coordinate,
                        next(
                            observation.baseline_value
                            for observation in baseline.observations
                            if observation.key == coordinate.key
                        ),
                    )
                    for coordinate in baseline.coordinate_manifest
                ),
            ),
            revision_sources={key: Path("trusted-subject")},
            revision_resolver=_resolver,
        )

    def _evaluate(self, request: RatchetCheckRequest | None = None):
        return check_module.evaluate_check(
            self.shared_run,
            _policy(),
            today=TODAY,
            ratchet=request,
        )

    def test_no_baseline_preserves_literal_current_state_result(self):
        evaluation = check_module.CheckEvaluation(
            self.shared_run, _policy(), today=TODAY
        )
        evaluation.open()
        evaluation.evaluate_integrity()
        evaluation.evaluate_metrics()
        before_ratchet = evaluation.result()

        result = self._evaluate()

        self.assertEqual(result, before_ratchet)
        self.assertEqual(result["check_result_format_version"], "1.4.0")
        self.assertEqual(result["ratchet"]["status"], "not_configured")
        self.assertEqual(result["exit_code"], 0)

    def test_valid_regression_emits_one_canonical_finding_and_exit_one(self):
        current = self._current_loc()

        result = self._evaluate(self._request(current - 1))

        ratchet_findings = [
            item for item in result["findings"]
            if item["rule_id"] == "ratchet.repository_loc"
        ]
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(len(ratchet_findings), 1)
        finding = ratchet_findings[0]
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(finding["observed_value"], 1)
        self.assertEqual(finding["evidence"]["delta"], 1)
        self.assertEqual(result["ratchet"]["violated_count"], 1)

    def test_warning_and_info_regressions_are_visible_but_do_not_fail(self):
        current = self._current_loc()

        for severity in (RatchetSeverity.WARNING, RatchetSeverity.INFO):
            with self.subTest(severity=severity.value):
                result = self._evaluate(
                    self._request(current - 1, severity=severity)
                )
                finding = next(
                    item for item in result["findings"]
                    if item["rule_id"] == "ratchet.repository_loc"
                )
                self.assertEqual(finding["severity"], severity.value)
                self.assertEqual(finding["status"], "violated")
                self.assertEqual(result["counts"]["failing"], 0)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(result["verdict"], "pass")

    def test_improvement_has_no_finding_and_exit_zero(self):
        current = self._current_loc()
        request = self._request(current + 1)

        result = self._evaluate(request)

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["ratchet"]["status"], RATCHET_EVALUATED)
        self.assertEqual(result["ratchet"]["evaluated_count"], 1)
        self.assertEqual(result["ratchet"]["violated_count"], 0)
        self.assertFalse(
            any(
                item["rule_id"] == "ratchet.repository_loc"
                for item in result["findings"]
            )
        )
        self.assertEqual(
            result["evaluated_input_provenance"]["ratchet"],
            {
                "baseline_sha256": request.trust.expected_sha256,
                "source_run_manifest_sha256": "c" * 64,
            },
        )
        self.assertEqual(
            validate_document("check_result_output", result, "check_result.json"),
            [],
        )

    def test_tolerance_has_no_finding_and_exit_zero(self):
        current = self._current_loc()

        result = self._evaluate(
            self._request(current - 3, tolerance=3)
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["ratchet"]["evaluated_count"], 1)
        self.assertEqual(result["ratchet"]["violated_count"], 0)

    def test_unavailable_current_metric_is_not_evaluable_and_exit_one(self):
        current = self._current_loc()
        base = self._view()
        repositories = json.loads(
            json.dumps([dict(item) for item in base.repositories])
        )
        aggregate = repositories[0]["metrics"]["aggregate"]
        aggregate["lines_of_code"] = None
        aggregate["loc_status"] = "failed"
        unavailable_view = _ViewOverride(base, repositories)

        with patch(
            "validation.artifact_io.reader.open_run",
            return_value=unavailable_view,
        ):
            result = check_module.evaluate_check(
                self.shared_run,
                _policy(),
                today=TODAY,
                ratchet=self._request(current),
            )

        finding = next(
            item for item in result["findings"]
            if item["rule_id"] == "ratchet.repository_loc"
        )
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(
            result["ratchet"]["status"],
            RATCHET_EVALUATED_WITH_NOT_EVALUABLE,
        )
        self.assertEqual(result["ratchet"]["not_evaluable_count"], 1)
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(finding["observed_value"], None)

    def test_invalid_baseline_is_admission_failure_and_exit_two(self):
        current = self._current_loc()
        request = self._request(current)
        request = replace(
            request,
            trust=replace(request.trust, expected_sha256="0" * 64),
        )

        result = self._evaluate(request)

        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["verdict"], "error")
        self.assertEqual(result["failure_kind"], FAILURE_RATCHET_ADMISSION)
        self.assertEqual(result["ratchet"]["status"], RATCHET_ADMISSION_FAILED)
        self.assertEqual(
            result["ratchet"]["error"]["code"], "external_digest_mismatch"
        )
        self.assertEqual(result["ratchet"]["baseline"], None)
        self.assertEqual(
            [str(item) for item in validate_document(
                "check_result_output", result, "check_result.json"
            )],
            [],
        )

    def test_wrong_subject_is_admission_failure_and_exit_two(self):
        result = self._evaluate(
            self._request(self._current_loc(), subject_key="github.com/acme/other")
        )

        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["failure_kind"], FAILURE_RATCHET_ADMISSION)
        self.assertEqual(
            result["ratchet"]["error"]["code"], "subject_set_mismatch"
        )

    def test_finding_identity_excludes_values_and_output_is_deterministic(self):
        current = self._current_loc()
        first = self._evaluate(self._request(current - 1))
        repeated = self._evaluate(self._request(current - 1))
        second = self._evaluate(self._request(current - 2))
        first_finding = next(
            item for item in first["findings"]
            if item["rule_id"] == "ratchet.repository_loc"
        )
        repeated_finding = next(
            item for item in repeated["findings"]
            if item["rule_id"] == "ratchet.repository_loc"
        )
        second_finding = next(
            item for item in second["findings"]
            if item["rule_id"] == "ratchet.repository_loc"
        )

        self.assertEqual(first_finding, repeated_finding)
        self.assertEqual(
            first_finding["finding_id"], second_finding["finding_id"]
        )
        self.assertNotEqual(
            first_finding["evidence"]["baseline_value"],
            second_finding["evidence"]["baseline_value"],
        )

    def test_summary_counts_and_registered_schema_are_consistent(self):
        request = self._request(self._current_loc() - 1)
        result = self._evaluate(request)
        ratchet_findings = [
            item for item in result["findings"]
            if item["rule_id"].startswith("ratchet.")
        ]
        ratchet = result["ratchet"]

        self.assertEqual(
            result["check_result_format_version"],
            RATCHET_CHECK_RESULT_FORMAT_VERSION,
        )
        self.assertEqual(
            check_module.CHECK_RESULT_FORMAT_VERSION, "1.4.0"
        )
        self.assertEqual(ratchet["paired_subject_count"], 1)
        self.assertEqual(ratchet["evaluated_count"], 1)
        self.assertEqual(
            ratchet["violated_count"],
            sum(
                item["status"] == finding_module.STATUS_VIOLATED
                for item in ratchet_findings
            ),
        )
        self.assertEqual(ratchet["not_evaluable_count"], 0)
        self.assertEqual(
            ratchet["baseline"]["verified_sha256"],
            request.trust.expected_sha256,
        )
        self.assertEqual(
            ratchet["baseline"]["source_run_id"], "trusted-baseline-run"
        )
        self.assertEqual(ratchet["current"]["run_id"], self._view().run_id)
        self.assertNotIn("comparisons", ratchet)
        self.assertNotIn("history", ratchet)
        self.assertNotIn("trends", ratchet)
        self.assertEqual(
            [str(item) for item in validate_document(
                "check_result_output", result, "check_result.json"
            )],
            [],
        )
