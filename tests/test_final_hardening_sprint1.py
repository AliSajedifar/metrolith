"""Regression gates for Final Hardening Sprint 1 (COR-001/004/005, DOC/CLI)."""

from __future__ import annotations

import io
import math
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import pipeline
from archlens_json import (
    POLICY_JSON_LIMITS,
    StrictJsonError,
    StrictJsonLimits,
    dumps_strict,
    load_file,
    loads_bytes,
)
from modules.policy import metrics
from modules.policy import findings
from modules.policy.check import (
    FAILURE_EVALUATION_ERROR,
    CheckEvaluation,
    CheckFailed,
    SubjectView,
)
from modules.policy.document import PolicyDocumentInvalid
from modules.policy.document_v2 import load_policy_v2
from modules.policy.sarif import SarifProjectionError, serialize_sarif


class StrictJsonBoundaryTests(unittest.TestCase):
    def test_duplicate_keys_nonfinite_numbers_and_bad_utf8_are_rejected(self):
        cases = (
            (b'{"a":1,"a":2}', "json_duplicate_key"),
            (b'{"a":NaN}', "json_non_standard_number"),
            (b'{"a":Infinity}', "json_non_standard_number"),
            (b'{"a":"\xff"}', "invalid_utf8"),
        )
        for payload, code in cases:
            with self.subTest(code=code), self.assertRaises(StrictJsonError) as caught:
                loads_bytes(
                    payload,
                    source="hostile.json",
                    limits=POLICY_JSON_LIMITS,
                    expect=dict,
                )
            self.assertEqual(caught.exception.code, code)

    def test_bytes_depth_collection_and_string_limits_are_enforced(self):
        limits = StrictJsonLimits(
            max_bytes=24,
            max_depth=2,
            max_collection_items=2,
            max_total_items=8,
            max_string_bytes=3,
        )
        cases = (
            (b'{"a":{"b":{"c":1}}}', "json_depth_exceeded"),
            (b'[1,2,3]', "json_collection_size_exceeded"),
            (b'{"a":"four"}', "json_item_too_large"),
        )
        for payload, code in cases:
            with self.subTest(code=code), self.assertRaises(StrictJsonError) as caught:
                loads_bytes(payload, source="bounded.json", limits=limits, expect=None)
            self.assertEqual(caught.exception.code, code)

        item_limits = StrictJsonLimits(
            max_bytes=64,
            max_depth=4,
            max_collection_items=8,
            max_total_items=3,
            max_string_bytes=16,
        )
        with self.assertRaises(StrictJsonError) as caught:
            loads_bytes(
                b'{"a":1,"b":2}',
                source="bounded.json",
                limits=item_limits,
            )
        self.assertEqual(caught.exception.code, "json_item_count_exceeded")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b"{" + b" " * 30 + b"}")
            with self.assertRaises(StrictJsonError) as caught:
                load_file(path, limits=limits)
            self.assertEqual(caught.exception.code, "artifact_too_large")

    def test_serialization_rejects_nonfinite_values(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(StrictJsonError):
                dumps_strict(
                    {"value": value},
                    source="output",
                    limits=POLICY_JSON_LIMITS,
                )

        with self.assertRaises(SarifProjectionError):
            serialize_sarif({"version": "2.1.0", "runs": [], "bad": math.nan})

    def test_falsey_wrong_policy_types_do_not_default(self):
        base = {
            "policy_document_format_version": "2.2.0",
            "name": "strict",
            "metric_rules": [
                {
                    "id": "r",
                    "metric": "repository.lines_of_code",
                    "operator": "gt",
                    "threshold": 1,
                }
            ],
        }
        for key, value in (
            ("integrity_rules", []),
            ("metric_rules", {}),
            ("waivers", {}),
            ("expected_contracts", []),
            ("options", False),
            ("options", None),
        ):
            payload = dict(base)
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(PolicyDocumentInvalid):
                load_policy_v2(payload)


class ConsumedFieldTests(unittest.TestCase):
    definition = metrics.metric_for("repository.lines_of_code")

    def test_zero_unavailable_missing_and_malformed_remain_distinct(self):
        assert self.definition is not None
        zero = metrics.read_core(
            {"lines_of_code": 0, "loc_status": "complete"}, self.definition
        )
        unavailable = metrics.read_core(
            {"lines_of_code": None, "loc_status": "failed"}, self.definition
        )
        missing_aggregate = metrics.read_core(None, self.definition)
        absent_language = metrics.read_core(
            None,
            self.definition,
            missing_completeness=metrics.COMPLETENESS_NOT_APPLICABLE,
        )
        invalid_status = metrics.read_core(
            {"lines_of_code": 3, "loc_status": "mystery"}, self.definition
        )

        self.assertEqual(zero.value, 0)
        self.assertEqual(zero.completeness, metrics.COMPLETENESS_COMPLETE)
        self.assertIsNone(zero.error)
        self.assertEqual(unavailable.completeness, metrics.COMPLETENESS_UNAVAILABLE)
        self.assertIsNone(unavailable.error)
        self.assertEqual(
            missing_aggregate.completeness, metrics.COMPLETENESS_UNAVAILABLE
        )
        self.assertIsNone(missing_aggregate.error)
        self.assertEqual(
            absent_language.completeness, metrics.COMPLETENESS_NOT_APPLICABLE
        )
        self.assertIsNotNone(invalid_status.error)

    def test_boolean_and_nonintegral_integer_metrics_are_malformed(self):
        assert self.definition is not None
        for value in (True, False, 1.5):
            observation = metrics.read_core(
                {"lines_of_code": value, "loc_status": "complete"},
                self.definition,
            )
            with self.subTest(value=value):
                self.assertIsNotNone(observation.error)
                self.assertIsNone(observation.value)

    def test_falsey_wrong_type_consumed_containers_are_not_absence(self):
        structural = metrics.metric_for("repository.cyclomatic_complexity_max")
        language = metrics.metric_for("language.lines_of_code")
        assert structural is not None and language is not None

        evaluation = CheckEvaluation(Path("."), self._callable_policy())
        malformed_complexity = SubjectView(
            "known", None, {"metrics": {"complexity": []}}
        )
        observation = evaluation._read_repository(
            malformed_complexity, structural
        )
        self.assertIsNotNone(observation.error)

        malformed_languages = SubjectView(
            "known", None, {"metrics": {"by_language": []}}
        )
        with self.assertRaises(CheckFailed) as caught:
            evaluation._validate_language_container(
                malformed_languages, language
            )
        self.assertEqual(caught.exception.kind, FAILURE_EVALUATION_ERROR)

    @staticmethod
    def _callable_policy():
        return load_policy_v2(
            {
                "policy_document_format_version": "2.2.0",
                "name": "callable strictness",
                "metric_rules": [
                    {
                        "id": "callable.cyclomatic",
                        "metric": "callable.cyclomatic_complexity",
                        "operator": "gt",
                        "threshold": 100,
                    }
                ],
            }
        )

    @staticmethod
    def _evaluation(row):
        evaluation = CheckEvaluation(Path("."), ConsumedFieldTests._callable_policy())
        evaluation.view = type(
            "LedgerView",
            (),
            {
                "has_callable_artifact": True,
                "stream_callables": lambda self: iter((row,)),
            },
        )()
        evaluation._subjects = {
            "known": SubjectView("known", None, {})
        }
        for rule in evaluation.policy.metric_rules:
            evaluation.tallies.setdefault(rule.identifier, type(
                "Unused", (), {}
            )())
        return evaluation

    def test_unknown_callable_subject_is_an_evaluation_failure(self):
        evaluation = self._evaluation({"subject_key": "unknown"})
        with self.assertRaises(CheckFailed) as caught:
            evaluation._load_ledger()
        self.assertEqual(caught.exception.kind, FAILURE_EVALUATION_ERROR)
        self.assertIn("unknown subject", caught.exception.message)

    def test_unknown_repository_subject_filter_is_an_evaluation_failure(self):
        policy = load_policy_v2(
            {
                "policy_document_format_version": "2.2.0",
                "name": "repository identity strictness",
                "metric_rules": [
                    {
                        "id": "repository.loc",
                        "metric": "repository.lines_of_code",
                        "operator": "gt",
                        "threshold": 0,
                        "subjects": ["missing", "known"],
                    }
                ],
            }
        )
        evaluation = CheckEvaluation(Path("."), policy)
        evaluation._subjects = {"known": SubjectView("known", None, {})}
        with self.assertRaises(CheckFailed) as caught:
            evaluation.evaluate_metrics()
        self.assertEqual(caught.exception.kind, FAILURE_EVALUATION_ERROR)
        self.assertIn("unknown repository subject", caught.exception.message)

    def test_selected_malformed_callable_integer_is_an_evaluation_failure(self):
        evaluation = self._evaluation(
            {
                "subject_key": "known",
                "detected_language": "Python",
                "relative_path": "app.py",
                "start_line": "1",
                "end_line": "2",
                "qualified_name": "main",
                "callable_row_id": "callable-1",
                "cyclomatic_complexity": "1.5",
                "structural_complexity_status": "complete",
            }
        )
        with self.assertRaises(CheckFailed) as caught:
            evaluation._load_ledger()
        self.assertEqual(caught.exception.kind, FAILURE_EVALUATION_ERROR)
        self.assertIn("non-integral", caught.exception.message)

    def test_cognitive_bool_and_fraction_cannot_reach_int_aggregation(self):
        policy = load_policy_v2(
            {
                "policy_document_format_version": "2.2.0",
                "name": "cognitive strictness",
                "metric_rules": [
                    {
                        "id": "repository.cognitive",
                        "metric": "repository.cognitive_complexity_total",
                        "operator": "gt",
                        "threshold": 100,
                    }
                ],
            }
        )
        for raw in (True, 1.9):
            row = {
                "subject_key": "known",
                "detected_language": "Python",
                "cognitive_complexity": raw,
            }
            evaluation = CheckEvaluation(Path("."), policy)
            evaluation.view = type(
                "LedgerView",
                (),
                {
                    "has_callable_artifact": True,
                    "stream_callables": lambda self, item=row: iter((item,)),
                },
            )()
            evaluation._subjects = {"known": SubjectView("known", None, {})}
            with self.subTest(raw=raw), self.assertRaises(CheckFailed) as caught:
                evaluation._load_ledger()
            self.assertEqual(caught.exception.kind, FAILURE_EVALUATION_ERROR)
            self.assertIn("must be an integer", caught.exception.message)


class SeverityDecisionTests(unittest.TestCase):
    def test_warning_and_info_never_require_exit_one(self):
        for severity in ("warning", "info"):
            for status in (findings.STATUS_VIOLATED, findings.STATUS_NOT_EVALUABLE):
                with self.subTest(severity=severity, status=status):
                    self.assertFalse(
                        findings.requires_failure(
                            severity=severity,
                            status=status,
                            fail_on_not_evaluable=True,
                        )
                    )

    def test_violation_uses_the_policy_not_evaluable_option(self):
        self.assertTrue(
            findings.requires_failure(
                severity="violation",
                status=findings.STATUS_VIOLATED,
                fail_on_not_evaluable=False,
            )
        )
        self.assertTrue(
            findings.requires_failure(
                severity="violation",
                status=findings.STATUS_NOT_EVALUABLE,
                fail_on_not_evaluable=True,
            )
        )
        self.assertFalse(
            findings.requires_failure(
                severity="violation",
                status=findings.STATUS_NOT_EVALUABLE,
                fail_on_not_evaluable=False,
            )
        )


class CliAndDocumentationTests(unittest.TestCase):
    def test_run_accepts_only_the_two_exact_supported_selectors(self):
        parser = pipeline.build_cli()
        for selector in ("metrics", "all"):
            args = parser.parse_args(
                ["run", selector, "--input", "repositories.csv"]
            )
            self.assertEqual(args.step, selector)

    def test_each_legacy_selector_has_an_explicit_migration_error(self):
        for selector in sorted(pipeline._LEGACY_RUN_STEPS):
            stderr = io.StringIO()
            with self.subTest(selector=selector), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    pipeline.build_cli().parse_args(
                        ["run", selector, "--input", "repositories.csv"]
                    )
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("legacy stage selector", stderr.getvalue())
            self.assertIn("migrate to 'metrics' or 'all'", stderr.getvalue())

    def test_readme_uses_the_positional_metrics_command_shape(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "metrolith run metrics --input repositories.csv "
            "--qualification-registry",
            readme,
        )
        self.assertNotIn("metrolith run --step metrics", readme)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
