"""BR5-A gates for CLI baseline input and SARIF comparison projection."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.cli import check_command
from modules.config import PROGRAM_VERSION
from modules.policy import check as check_module
from modules.policy import trust as trust_module
from modules.policy.document_v2 import load_any_policy_bytes
from modules.policy.sarif import render_sarif
from modules.ratchet import (
    capture_baseline,
    RatchetDirection,
    RatchetRule,
)
from modules.ratchet import cli_request
from modules.ratchet.check_service import (
    RATCHET_CHECK_RESULT_FORMAT_VERSION,
    RatchetCheckRequest,
)
from modules.revision_source import ResolvedRevision, ResolvedRevisionPair
from tests.test_policy_v2_check import RunFixture as _RunFixture
from tests.ratchet_contract_fixtures import mark_run_producer_clean
from validation.artifact_io.reader import open_run


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


class RatchetCliAndSarifTests(_RunFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        mark_run_producer_clean(cls.shared_run)

    def _policy_path(self) -> Path:
        path = self.scratch() / "policy.json"
        path.write_text(
            json.dumps(
                {
                    "policy_document_format_version": "2.0.0",
                    "name": "br5a-policy",
                    "metric_rules": [
                        {
                            "id": "current.source_files",
                            "metric": "repository.source_files",
                            "operator": "gt",
                            "threshold": 1_000_000,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _bundle(
        self, baseline_value: int | float, *, tolerance: int | float = 0
    ) -> tuple[Path, str, str, str]:
        root = self.scratch()
        baseline_path = root / "baseline.json"
        producing_run = cli_request.source_run_directory(baseline_path)
        shutil.copytree(self.shared_run, producing_run)
        analysis_path = producing_run / "analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = baseline_value
        analysis_path.write_text(
            json.dumps(analysis, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        for projection_path in (producing_run / "repositories").glob("*.json"):
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["metrics"]["aggregate"]["lines_of_code"] = baseline_value
            projection_path.write_text(
                json.dumps(projection, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        view = open_run(producing_run)
        repository = view.repositories[0]
        acquisition = repository["acquisition"]
        subject_key = str(repository["subject_key"])
        commit = str(acquisition["analyzed_commit_sha"])
        rule = RatchetRule(
            rule_id="ratchet.repository_loc",
            metric="repository.lines_of_code",
            metric_contract="metrics",
            scope="repository",
            direction=RatchetDirection.INCREASE_IS_WORSE,
            max_regression=tolerance,
        )
        payload = capture_baseline(producing_run, (rule,)).payload
        baseline_path.write_bytes(payload)
        return (
            baseline_path,
            hashlib.sha256(payload).hexdigest(),
            subject_key,
            commit,
        )

    @contextmanager
    def _admitted_revision(self, subject_key: str):
        real_request = RatchetCheckRequest

        def construct(**kwargs):
            kwargs["revision_sources"] = {subject_key: Path("trusted-cache")}
            kwargs["revision_resolver"] = _resolver
            return real_request(**kwargs)

        with patch.object(cli_request, "RatchetCheckRequest", side_effect=construct):
            yield

    def _args(
        self,
        *,
        baseline: Path | None = None,
        digest: str | None = None,
        output_format: str = "json",
        output: Path | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            run_directory=self.shared_run,
            policy=self._policy_path(),
            hotspots=None,
            duplication=None,
            baseline=baseline,
            baseline_sha256=digest,
            format=output_format,
            output=output,
        )

    def _handle(self, args: SimpleNamespace) -> tuple[int, str]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            exit_code = check_command.handle(args)
        return exit_code, stream.getvalue()

    def _current_loc(self) -> int:
        repository = open_run(self.shared_run).repositories[0]
        return int(repository["metrics"]["aggregate"]["lines_of_code"])

    def test_no_baseline_cli_result_is_identical_to_existing_check_behavior(self):
        args = self._args()
        policy_payload = args.policy.read_bytes()
        policy = load_any_policy_bytes(policy_payload)
        expected = check_module.evaluate_check(
            self.shared_run,
            policy,
            hotspots=None,
            duplication=None,
            trust=trust_module.CheckTrustContext.local(
                evaluator_version=PROGRAM_VERSION,
                policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
            ),
        )

        exit_code, output = self._handle(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output), expected)
        self.assertEqual(expected["check_result_format_version"], "1.4.0")
        self.assertEqual(expected["ratchet"]["status"], "not_configured")

    def test_sarif_without_baseline_preserves_current_state_projection(self):
        args = self._args(output_format="sarif")
        policy_payload = args.policy.read_bytes()
        policy = load_any_policy_bytes(policy_payload)
        expected = check_module.evaluate_check(
            self.shared_run,
            policy,
            hotspots=None,
            duplication=None,
            trust=trust_module.CheckTrustContext.local(
                evaluator_version=PROGRAM_VERSION,
                policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
            ),
        )

        exit_code, output = self._handle(args)

        document = json.loads(output)
        archlens = document["runs"][0]["properties"]["archlens"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, render_sarif(expected))
        self.assertEqual(archlens["checkResultFormatVersion"], "1.4.0")
        self.assertNotIn("baselineState", output)
        self.assertNotIn('"comparison"', output)

    def test_cli_adapter_constructs_external_source_evidence_without_mutation(self):
        path, digest, subject_key, commit = self._bundle(self._current_loc())
        before = path.read_bytes()

        request = cli_request.build_cli_ratchet_request(
            path,
            digest,
            current_run_directory=self.shared_run,
        )

        self.assertEqual(request.baseline_payload, before)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(request.trust.expected_sha256, digest)
        self.assertEqual(request.source_run.status, "complete")
        self.assertTrue(request.source_run.admitted)
        self.assertEqual(request.source_run.subjects[0].subject_key, subject_key)
        self.assertEqual(request.source_run.subjects[0].analyzed_commit_sha, commit)

    def test_cli_adapter_reports_unreadable_current_run(self):
        path, digest, _subject_key, _commit = self._bundle(self._current_loc())

        def open_unless_current(candidate):
            if Path(candidate) == path:
                raise OSError("unreadable current run")
            return open_run(candidate)

        with patch(
            "validation.artifact_io.reader.open_run",
            side_effect=open_unless_current,
        ):
            with self.assertRaises(cli_request.RatchetCliRequestError) as raised:
                cli_request.build_cli_ratchet_request(
                    path,
                    digest,
                    current_run_directory=path,
                )

        self.assertEqual(raised.exception.code, "current_run_unreadable")

    def test_valid_baseline_regression_exits_one(self):
        path, digest, subject_key, _commit = self._bundle(self._current_loc() - 1)
        with self._admitted_revision(subject_key):
            exit_code, output = self._handle(
                self._args(baseline=path, digest=digest)
            )

        result = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["ratchet"]["violated_count"], 1)

    def test_improvement_exits_zero(self):
        path, digest, subject_key, _commit = self._bundle(self._current_loc() + 1)
        with self._admitted_revision(subject_key):
            exit_code, output = self._handle(
                self._args(baseline=path, digest=digest)
            )

        result = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["ratchet"]["evaluated_count"], 1)
        self.assertEqual(result["ratchet"]["violated_count"], 0)

    def test_bad_digest_and_missing_trust_input_fail_closed_with_exit_two(self):
        path, _digest, _subject_key, _commit = self._bundle(self._current_loc())
        bad_code, bad_output = self._handle(
            self._args(baseline=path, digest="0" * 64)
        )
        missing_code, missing_output = self._handle(
            self._args(baseline=path, digest=None)
        )

        bad = json.loads(bad_output)
        missing = json.loads(missing_output)
        self.assertEqual((bad_code, missing_code), (2, 2))
        self.assertEqual(
            bad["check_result_format_version"],
            RATCHET_CHECK_RESULT_FORMAT_VERSION,
        )
        self.assertEqual(
            missing["check_result_format_version"],
            RATCHET_CHECK_RESULT_FORMAT_VERSION,
        )
        self.assertEqual(bad["failure_kind"], "ratchet_admission_failed")
        self.assertEqual(
            bad["ratchet"]["error"]["code"], "external_digest_mismatch"
        )
        self.assertEqual(missing["ratchet"]["configured"], True)
        self.assertEqual(
            missing["ratchet"]["error"]["code"], "external_digest_required"
        )

    def test_sarif_projects_bounded_comparison_without_aggregate_location(self):
        baseline_value = self._current_loc() - 2
        path, digest, subject_key, commit = self._bundle(baseline_value)
        with self._admitted_revision(subject_key):
            exit_code, output = self._handle(
                self._args(
                    baseline=path,
                    digest=digest,
                    output_format="sarif",
                )
            )

        document = json.loads(output)
        result = document["runs"][0]["results"][0]
        comparison = result["properties"]["archlens"]["comparison"]
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            document["runs"][0]["properties"]["archlens"][
                "checkResultFormatVersion"
            ],
            RATCHET_CHECK_RESULT_FORMAT_VERSION,
        )
        self.assertEqual(
            comparison,
            {
                "baselineCommit": commit,
                "baselineSha256": digest,
                "baselineValue": baseline_value,
                "currentCommit": commit,
                "currentValue": self._current_loc(),
                "delta": 2,
                "direction": "increase_is_worse",
                "regressionAmount": 2,
                "tolerance": 0,
            },
        )
        self.assertNotIn("locations", result)
        self.assertNotIn("baselineState", output)

    def test_sarif_bytes_are_deterministic(self):
        path, digest, subject_key, _commit = self._bundle(self._current_loc() - 1)
        with self._admitted_revision(subject_key):
            first_code, first = self._handle(
                self._args(
                    baseline=path,
                    digest=digest,
                    output_format="sarif",
                )
            )
            second_code, second = self._handle(
                self._args(
                    baseline=path,
                    digest=digest,
                    output_format="sarif",
                )
            )

        self.assertEqual((first_code, second_code), (1, 1))
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
