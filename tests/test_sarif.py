"""Focused evidence for the projection-only SARIF 2.1.0 campaign."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.cli import check_command
from modules.config import PROGRAM_VERSION
from modules.policy import check as check_module
from modules.policy import findings as finding_module
from modules.policy import sarif as sarif_module
from modules.policy.document import default_policy
from modules.policy.document_v2 import adapt_v1_document
from tests import historical_fixtures
from tests.test_policy_v2_check import RunBuilder, _policy, _rule


def _summary(
    rule_id: str,
    *,
    severity: str = "violation",
    kind: str = "metric",
    metric: str | None = "repository.lines_of_code",
    scope: str = "repository",
    passed: int = 0,
    violated: int = 0,
    not_evaluable: int = 0,
    not_applicable: int = 0,
    evaluation_error: int = 0,
) -> dict:
    return {
        "rule_id": rule_id,
        "kind": kind,
        "severity": severity,
        "metric": metric,
        "metric_source": (
            "analysis.json metrics.aggregate.lines_of_code" if metric else None
        ),
        "operator": "gt" if metric else None,
        "threshold": 10 if metric else None,
        "scope": scope,
        "domain": "run_integrity" if kind == "integrity" else None,
        "units_evaluated": (
            passed + violated + not_evaluable + not_applicable + evaluation_error
        ),
        "passed": passed,
        "violated": violated,
        "not_evaluable": not_evaluable,
        "not_applicable": not_applicable,
        "evaluation_error": evaluation_error,
    }


def _finding(
    rule_id: str = "rule.one",
    *,
    severity: str = "violation",
    status: str = "violated",
    scope: str = "repository",
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    message: str | None = None,
    discriminator: str = "",
) -> dict:
    finding_id = finding_module.finding_identity(
        rule_id=rule_id,
        metric="repository.lines_of_code",
        scope=scope,
        subject_key="github.com/acme/app",
        path=path,
        callable_row_id=(f"row-{discriminator}" if discriminator else None),
    )
    return {
        "finding_id": finding_id,
        "rule_id": rule_id,
        "kind": "metric",
        "severity": severity,
        "status": status,
        "scope": scope,
        "domain": None,
        "metric": "repository.lines_of_code",
        "operator": "gt",
        "operator_symbol": ">",
        "threshold": 10,
        "subject_key": "github.com/acme/app",
        "repository_url": "https://github.com/acme/app",
        "language": "Python" if scope != "repository" else None,
        "path": path,
        "callable_row_id": f"row-{discriminator}" if discriminator else None,
        "callable_qualified_name": "pkg.run" if path else None,
        "start_line": start_line,
        "end_line": end_line,
        "observed_value": 12 if status == "violated" else None,
        "value_status": "complete" if status == "violated" else "failed",
        "value_status_field": "loc_status",
        "data_completeness": (
            "complete" if status == "violated" else "unavailable"
        ),
        "reason": None if status == "violated" else "measurement_unavailable",
        "message": message or f"canonical message for {rule_id}",
        "evidence": {},
        "provenance": {
            "run_id": "run-1",
            "artifact_schema_version": "1.11.0",
            "program_version": "3.5.1",
            "metric_contract_version": "3.0.0",
            "complexity_contract_version": "2.0.0",
        },
    }


def _result(
    *,
    rules: list[dict] | None = None,
    findings: list[dict] | None = None,
    waived: list[dict] | None = None,
    exit_code: int = 1,
    failure_kind: str | None = None,
) -> dict:
    rules = rules if rules is not None else [_summary("rule.one", violated=1)]
    findings = findings if findings is not None else [_finding()]
    verdict = "error" if failure_kind else ("fail" if exit_code == 1 else "pass")
    return {
        # Sourced from the producer rather than pinned: SARIF only echoes this
        # value, so a hardcoded version silently drifts when the result format
        # moves and tests the fixture instead of the projection.
        "check_result_format_version": check_module.CHECK_RESULT_FORMAT_VERSION,
        "archlens_version": PROGRAM_VERSION,
        "evaluated_at": "2099-12-31",
        "verdict": verdict,
        "exit_code": exit_code,
        "failure_kind": failure_kind,
        "failure_message": None,
        "policy": {
            "name": "policy",
            "policy_document_format_version": "2.0.0",
            "evaluated_as_format_version": "2.0.0",
        },
        "run": {
            "run_id": "run-1",
            "run_directory": r"D:\private\checkout\run-1",
            "run_status": "completed",
            "lifecycle": "finalized",
            "artifact_schema_version": "1.11.0",
            "benchmark_qualification": None,
        },
        "counts": {
            "findings": len(findings),
            "waived": len(waived or []),
            "failing": int(exit_code == 1),
            "rules_evaluated": len(rules),
            "units": {},
        },
        "rules": rules,
        "findings": findings,
        "waived_findings": waived or [],
        "provenance": {
            "run_id": "run-1",
            "artifact_schema_version": "1.11.0",
            "metric_contract_version": "3.0.0",
            "complexity_contract_version": "2.0.0",
        },
    }


class SarifProjectionTests(unittest.TestCase):
    def test_one_evaluation_maps_to_one_standards_shaped_run(self):
        document = sarif_module.project_sarif(_result())
        self.assertEqual(document["version"], "2.1.0")
        self.assertEqual(document["$schema"], sarif_module.SARIF_SCHEMA_URI)
        self.assertEqual(len(document["runs"]), 1)
        driver = document["runs"][0]["tool"]["driver"]
        self.assertEqual(driver["name"], "Metrolith")
        self.assertEqual(driver["semanticVersion"], PROGRAM_VERSION)
        self.assertEqual(sarif_module.validate_sarif_subset(document), [])

    def test_result_population_is_only_violated_canonical_findings(self):
        violated = _finding(discriminator="v")
        passed = _finding(status="passed", discriminator="p")
        not_applicable = _finding(status="not_applicable", discriminator="na")
        document = sarif_module.project_sarif(_result(
            findings=[passed, not_applicable, violated]
        ))
        results = document["runs"][0]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["partialFingerprints"]["archlensFindingId/v1"],
            violated["finding_id"],
        )

    def test_passed_rule_has_a_descriptor_but_no_result(self):
        document = sarif_module.project_sarif(_result(
            rules=[_summary("clean", passed=4)], findings=[], exit_code=0
        ))
        run = document["runs"][0]
        self.assertEqual([item["id"] for item in run["tool"]["driver"]["rules"]], ["clean"])
        self.assertEqual(run["results"], [])

    def test_descriptors_are_unique_and_sorted_by_authoritative_rule_id(self):
        repeated = [_finding("z", discriminator="1"), _finding("z", discriminator="2")]
        document = sarif_module.project_sarif(_result(
            rules=[_summary("z", violated=2), _summary("a", passed=1)],
            findings=repeated,
        ))
        descriptors = document["runs"][0]["tool"]["driver"]["rules"]
        self.assertEqual([item["id"] for item in descriptors], ["a", "z"])

    def test_existing_policy_message_and_metadata_annotate_only_the_descriptor(self):
        policy = _policy(_rule(
            "rule.one", "repository.lines_of_code", "gt", 10,
            message="Team-owned LOC ceiling",
            metadata={"owner": "architecture", "ticket": "ARCH-7"},
        ))
        document = sarif_module.project_sarif(_result(), policy=policy)
        run = document["runs"][0]
        descriptor = run["tool"]["driver"]["rules"][0]
        self.assertEqual(descriptor["shortDescription"]["text"], "Team-owned LOC ceiling")
        self.assertEqual(
            descriptor["properties"]["archlens"]["policy"]["metadata"],
            {"owner": "architecture", "ticket": "ARCH-7"},
        )
        # Metadata may annotate an already-evaluated ID; it cannot manufacture
        # another descriptor or result.
        self.assertEqual(len(run["tool"]["driver"]["rules"]), 1)
        self.assertEqual(len(run["results"]), 1)

    def test_forged_finding_identity_is_refused_not_repaired(self):
        forged = _finding()
        forged["finding_id"] = "random-or-index-derived"
        with self.assertRaisesRegex(sarif_module.SarifProjectionError, "identity"):
            sarif_module.project_sarif(_result(findings=[forged]))

    def test_no_timestamp_random_identity_or_baseline_state_is_emitted(self):
        rendered = sarif_module.render_sarif(_result())
        self.assertNotIn("2099-12-31", rendered)
        self.assertNotIn("baselineState", rendered)
        self.assertNotIn("uuid", rendered.casefold())
        self.assertIn("alf1:", rendered)

    def test_all_existing_severities_map_without_escalation(self):
        expected = {"violation": "error", "warning": "warning", "info": "note"}
        for severity, level in expected.items():
            with self.subTest(severity=severity):
                finding = _finding(severity=severity)
                document = sarif_module.project_sarif(_result(
                    rules=[_summary("rule.one", severity=severity, violated=1)],
                    findings=[finding],
                ))
                run = document["runs"][0]
                self.assertEqual(run["results"][0]["level"], level)
                self.assertEqual(
                    run["tool"]["driver"]["rules"][0]
                    ["defaultConfiguration"]["level"],
                    level,
                )

    def test_non_evaluable_is_a_notification_never_a_numeric_result(self):
        finding = _finding(status="not_evaluable")
        document = sarif_module.project_sarif(_result(
            rules=[_summary("rule.one", not_evaluable=1)], findings=[finding]
        ))
        run = document["runs"][0]
        self.assertEqual(run["results"], [])
        notifications = run["invocations"][0]["toolExecutionNotifications"]
        self.assertEqual(len(notifications), 1)
        properties = notifications[0]["properties"]["archlens"]
        self.assertEqual(properties["status"], "not_evaluable")
        self.assertIsNone(properties["observedValue"])
        self.assertNotEqual(properties["observedValue"], 0)
        self.assertTrue(run["invocations"][0]["executionSuccessful"])

    def test_evaluation_error_is_an_unsuccessful_invocation_not_a_result(self):
        finding = _finding(status="evaluation_error")
        document = sarif_module.project_sarif(_result(
            rules=[_summary("rule.one", evaluation_error=1)], findings=[finding],
            exit_code=2, failure_kind="evaluation_error",
        ))
        run = document["runs"][0]
        self.assertEqual(run["results"], [])
        self.assertFalse(run["invocations"][0]["executionSuccessful"])
        self.assertEqual(
            run["invocations"][0]["toolExecutionNotifications"][0]
            ["properties"]["archlens"]["status"],
            "evaluation_error",
        )

    def test_pre_evaluation_failures_are_distinguished_without_source_results(self):
        for kind in check_module.FAILURE_KINDS:
            with self.subTest(kind=kind):
                check_result = check_module.failure_result(
                    kind=kind, message=r"failed under C:\Users\person\checkout",
                    run_directory=Path(r"C:\Users\person\checkout"),
                    policy_name=None,
                    today=date(2026, 8, 18),
                )
                document = sarif_module.project_sarif(check_result)
                run = document["runs"][0]
                self.assertEqual(run["results"], [])
                self.assertFalse(run["invocations"][0]["executionSuccessful"])
                self.assertEqual(
                    run["invocations"][0]["toolExecutionNotifications"][0]
                    ["properties"]["archlens"]["failureKind"],
                    kind,
                )

    def test_waived_violation_is_a_suppressed_canonical_result(self):
        waived = _finding(discriminator="waived")
        waived["waiver"] = {
            "rule_id": "rule.one", "reason": "accepted exception",
            "expires_on": "2026-12-31",
        }
        document = sarif_module.project_sarif(_result(
            findings=[], waived=[waived], exit_code=0
        ))
        result = document["runs"][0]["results"][0]
        self.assertEqual(result["suppressions"][0]["kind"], "external")
        self.assertEqual(
            result["partialFingerprints"]["archlensFindingId/v1"],
            waived["finding_id"],
        )


class SarifLocationAndPrivacyTests(unittest.TestCase):
    def test_repository_and_language_findings_have_no_fabricated_location(self):
        for scope in ("repository", "language"):
            with self.subTest(scope=scope):
                finding = _finding(scope=scope)
                document = sarif_module.project_sarif(_result(findings=[finding]))
                self.assertNotIn("locations", document["runs"][0]["results"][0])

    def test_callable_uses_only_existing_relative_path_and_span(self):
        finding = _finding(
            scope="callable", path=r"src\café folder\api #1.py",
            start_line=7, end_line=11,
        )
        document = sarif_module.project_sarif(_result(
            rules=[_summary("rule.one", scope="callable", violated=1)],
            findings=[finding],
        ))
        physical = document["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        self.assertEqual(physical["artifactLocation"]["uri"], "src/café folder/api #1.py")
        self.assertEqual(physical["region"], {"startLine": 7, "endLine": 11})

    def test_invalid_or_untrusted_lines_are_not_invented(self):
        finding = _finding(scope="callable", path="src/app.py", start_line=0, end_line=99)
        result = sarif_module.project_sarif(_result(
            rules=[_summary("rule.one", scope="callable", violated=1)],
            findings=[finding],
        ))["runs"][0]["results"][0]
        self.assertNotIn("region", result["locations"][0]["physicalLocation"])

    def test_windows_absolute_path_and_message_cannot_leak(self):
        local = r"D:\private user\temp\venv\src\secret.py"
        finding = _finding(
            scope="callable", path=local, start_line=4,
            message=f"failure at {local}",
        )
        rendered = sarif_module.render_sarif(_result(
            rules=[_summary("rule.one", scope="callable", violated=1)],
            findings=[finding],
        ))
        self.assertNotIn("D:", rendered)
        self.assertNotIn("private user", rendered)
        result = json.loads(rendered)["runs"][0]["results"][0]
        self.assertNotIn("locations", result)

    def test_unc_rooted_traversal_and_uri_paths_are_omitted(self):
        paths = (
            r"\\server\share\a.py", "/home/person/a.py", "../a.py",
            "file:///C:/temp/a.py", "C:/cache/a.py",
        )
        for index, path in enumerate(paths):
            with self.subTest(path=path):
                finding = _finding(
                    scope="callable", path=path, discriminator=str(index),
                    message="portable canonical message",
                )
                result = sarif_module.project_sarif(_result(
                    rules=[_summary("rule.one", scope="callable", violated=1)],
                    findings=[finding],
                ))["runs"][0]["results"][0]
                self.assertNotIn("locations", result)

    def test_run_temp_cache_home_and_virtualenv_paths_are_not_projected(self):
        check_result = _result()
        check_result["run"]["run_directory"] = r"C:\Users\sample\AppData\Local\Temp\venv"
        check_result["failure_message"] = r"cache failed at C:\cache\artifact"
        rendered = sarif_module.render_sarif(check_result)
        for forbidden in ("C:", "AppData", "Temp", "venv", "C:\\cache"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_unicode_spaces_special_characters_and_no_final_source_newline_are_irrelevant(self):
        finding = _finding(
            scope="callable", path="src/日本語 and café/[core]#1.py",
            message="μ-service finding from a source with no final newline",
        )
        rendered = sarif_module.render_sarif(_result(
            rules=[_summary("rule.one", scope="callable", violated=1)],
            findings=[finding],
        ))
        self.assertIn("日本語 and café/[core]#1.py", rendered)
        self.assertIn("μ-service", rendered)
        self.assertTrue(rendered.endswith("\n"))


class SarifDeterminismAndMutationTests(unittest.TestCase):
    def test_repeated_projection_is_exact_byte_identical(self):
        check_result = _result()
        self.assertEqual(
            sarif_module.render_sarif(check_result),
            sarif_module.render_sarif(copy.deepcopy(check_result)),
        )

    def test_reversing_rule_and_finding_input_keeps_exact_bytes(self):
        rules = [_summary("b", violated=1), _summary("a", violated=1)]
        findings = [_finding("b"), _finding("a")]
        forward = _result(rules=rules, findings=findings)
        reverse = _result(rules=list(reversed(rules)), findings=list(reversed(findings)))
        self.assertEqual(
            sarif_module.render_sarif(forward), sarif_module.render_sarif(reverse)
        )

    def test_severity_guard_mutation_changes_the_projection(self):
        honest = sarif_module.render_sarif(_result())
        with patch.dict(
            sarif_module.SEVERITY_TO_LEVEL, {"violation": "note"}, clear=False
        ):
            mutated = sarif_module.render_sarif(_result())
        self.assertNotEqual(honest, mutated)
        self.assertEqual(json.loads(honest)["runs"][0]["results"][0]["level"], "error")

    def test_projection_never_invokes_the_existing_comparator(self):
        with patch.object(
            finding_module, "compare", side_effect=AssertionError("second evaluator")
        ) as comparator:
            sarif_module.project_sarif(_result())
        comparator.assert_not_called()

    def test_projector_source_has_no_metric_reader_or_run_open(self):
        source = Path(sarif_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("modules.policy.metrics", source)
        self.assertNotIn("open_run", source)
        self.assertNotIn("evaluate_check", source)


class SarifCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="archlens_sarif_cli_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps({
            "policy_document_format_version": "2.0.0",
            "name": "cli",
            "metric_rules": [{
                "id": "rule.one", "metric": "repository.lines_of_code",
                "operator": "gt", "threshold": 10,
            }],
        }), encoding="utf-8")

    def _args(self, output_format: str, output: Path | None = None):
        return SimpleNamespace(
            policy=self.policy, run_directory=self.root / "run",
            format=output_format, output=output,
        )

    def _handle(self, check_result: dict, output_format: str, output: Path | None = None):
        stream = io.StringIO()
        with patch.object(check_command, "evaluate_check", return_value=check_result) as evaluate:
            with redirect_stdout(stream):
                exit_code = check_command.handle(self._args(output_format, output))
        return exit_code, stream.getvalue(), evaluate

    def test_human_output_remains_the_default_rendering(self):
        check_result = _result(findings=[], rules=[_summary("rule.one", passed=1)], exit_code=0)
        exit_code, stdout, _evaluate = self._handle(check_result, "text")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, check_command.render_text(check_result) + "\n")

    def test_check_json_remains_supported(self):
        check_result = _result(exit_code=1)
        exit_code, stdout, _evaluate = self._handle(check_result, "json")
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            json.loads(stdout)["check_result_format_version"],
            check_module.CHECK_RESULT_FORMAT_VERSION,
        )

    def test_sarif_selection_evaluates_once_and_preserves_exit_zero_and_one(self):
        for expected in (0, 1):
            with self.subTest(exit_code=expected):
                findings = [] if expected == 0 else [_finding()]
                rules = [_summary("rule.one", passed=1)] if expected == 0 else None
                check_result = _result(
                    findings=findings, rules=rules, exit_code=expected
                )
                exit_code, stdout, evaluate = self._handle(check_result, "sarif")
                self.assertEqual(exit_code, expected)
                evaluate.assert_called_once()
                self.assertEqual(json.loads(stdout)["version"], "2.1.0")

    def test_invalid_policy_emits_sarif_and_exits_two(self):
        self.policy.write_text("{", encoding="utf-8")
        stream = io.StringIO()
        with redirect_stdout(stream):
            exit_code = check_command.handle(self._args("sarif"))
        self.assertEqual(exit_code, 2)
        document = json.loads(stream.getvalue())
        self.assertFalse(document["runs"][0]["invocations"][0]["executionSuccessful"])
        self.assertEqual(
            document["runs"][0]["properties"]["archlens"]["failureKind"],
            "policy_invalid",
        )

    def test_invalid_artifact_emits_sarif_and_exits_two(self):
        stream = io.StringIO()
        with patch.object(
            check_command, "evaluate_check",
            side_effect=check_module.CheckFailed("run_unreadable", r"C:\temp\bad"),
        ), redirect_stdout(stream):
            exit_code = check_command.handle(self._args("sarif"))
        self.assertEqual(exit_code, 2)
        self.assertNotIn("C:", stream.getvalue())
        self.assertEqual(
            json.loads(stream.getvalue())["runs"][0]["properties"]
            ["archlens"]["failureKind"],
            "run_unreadable",
        )

    def test_sarif_stdout_and_output_file_are_exactly_identical(self):
        target = self.root / "nested" / "check.sarif"
        exit_code, stdout, _evaluate = self._handle(_result(), "sarif", target)
        self.assertEqual(exit_code, 1)
        self.assertEqual(target.read_text(encoding="utf-8"), stdout)

    def test_output_publishes_complete_sarif_through_guarded_writer(self):
        target = self.root / "check.sarif"
        stream = io.StringIO()
        with patch.object(check_command, "evaluate_check", return_value=_result()), redirect_stdout(stream):
            check_command.handle(self._args("sarif", target))
        self.assertEqual(target.read_bytes(), stream.getvalue().encode("utf-8"))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], "2.1.0")
        self.assertEqual(list(self.root.glob(".check.sarif.*")), [])


class SarifCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="archlens_sarif_compat_")
        cls.shared_run = RunBuilder.build(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_policy_v1_and_v2_both_project(self):
        documents = (
            adapt_v1_document(default_policy()),
            _policy(_rule("loc", "repository.lines_of_code", "gte", 0)),
        )
        for policy in documents:
            with self.subTest(version=policy.source_format_version):
                check_result = check_module.evaluate_check(
                    self.shared_run, policy, today=date(2026, 8, 18)
                )
                sarif = sarif_module.project_sarif(check_result)
                self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])
                self.assertEqual(
                    sarif["runs"][0]["properties"]["archlens"]["policy"]
                    ["documentFormatVersion"],
                    policy.source_format_version,
                )

    def test_generic_and_benchmark_qualified_context_do_not_change_sarif(self):
        check_result = check_module.evaluate_check(
            self.shared_run,
            _policy(_rule("loc", "repository.lines_of_code", "gte", 0)),
            today=date(2026, 8, 18),
        )
        qualified = copy.deepcopy(check_result)
        qualified["run"]["benchmark_qualification"] = {
            "qualification_mode": "benchmark_qualified",
            "qualification_artifact_present": True,
            "benchmark_of_record_status": "READY",
        }
        self.assertEqual(
            sarif_module.render_sarif(check_result),
            sarif_module.render_sarif(qualified),
        )

    def test_supported_historical_artifact_projects_without_reanalysis(self):
        check_result = check_module.evaluate_check(
            historical_fixtures.run_for("1.3.0"),
            _policy(_rule("loc", "repository.lines_of_code", "gte", 0)),
            today=date(2026, 8, 18),
        )
        document = sarif_module.project_sarif(check_result)
        self.assertEqual(sarif_module.validate_sarif_subset(document), [])

    def test_new_module_is_covered_by_the_existing_packaged_parent(self):
        import tomllib

        repository = Path(__file__).resolve().parent.parent
        packages = set(tomllib.loads(
            (repository / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["setuptools"]["packages"])
        self.assertIn("modules.policy", packages)
        self.assertTrue((repository / "modules" / "policy" / "sarif.py").is_file())


if __name__ == "__main__":
    unittest.main()
