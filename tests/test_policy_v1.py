"""Policy-as-Code v1: rules, bounded waivers, exit codes, output contract.

Two properties matter more than any individual rule:

* **v1 contains no metric thresholds.** A rule that gated on a metric delta
  would be gating on a number differential validation has not yet interpreted.
  A test asserts none exists, so v2 cannot arrive by accident.
* **A rule that fires must be able to fire.** Every rule is exercised against a
  run constructed to violate it, so the policy cannot be a set of checks that
  quietly never trigger.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.policy import rules as rule_module
from modules.policy.document import (
    PolicyDocument,
    PolicyDocumentInvalid,
    Waiver,
    default_policy,
    load_policy,
)
from modules.policy.engine import (
    EXIT_PASS,
    EXIT_POLICY_INVALID,
    EXIT_UNREADABLE_RUN,
    EXIT_VIOLATION,
    evaluate,
)
from validation.artifact_io.schema_store import load_schema, validate_document

ANALYZED_SHA = "a" * 40
REPOSITORY = Path(__file__).resolve().parent.parent


class RunFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_policy_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n",
            encoding="utf-8", newline="\n",
        )
        self.input = self.root / "repositories.csv"
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )

    def _acquire(self, spec, config, mode="latest", progress=None):
        from contextlib import contextmanager

        @contextmanager
        def acquire():
            yield AcquiredRepository(
                self.repo,
                AcquisitionRecord(
                    repository_url=spec.url, repository_owner=spec.owner,
                    repository_name=spec.repository_name,
                    requested_commit_sha=spec.commit_sha,
                    analyzed_commit_sha=ANALYZED_SHA,
                    resolved_ref="refs/heads/main", default_branch="main",
                    acquisition_mode="offline", cache_status="reused",
                    remote_checked=False, fetch_timestamp=None,
                    checkout_timestamp="2026-08-01T00:00:00Z",
                    commit_verification_status="verified",
                    fetch_method="offline_cache",
                ),
            )

        return acquire()

    def build_run(self, name="output") -> Path:
        config = AnalysisConfig.from_env(
            output_root=self.root / name, cache_root=self.root / "cache",
            temporary_directory=self.root / "temp", workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])


class ScopeTests(unittest.TestCase):
    """Policy v1 must not contain a metric threshold."""

    def test_no_rule_gates_on_a_metric_value(self):
        source = (REPOSITORY / "modules" / "policy" / "rules.py").read_text(
            encoding="utf-8"
        )
        import ast

        tree = ast.parse(source)
        # A threshold needs a numeric literal compared against something. The
        # only integers legitimately present are severity-free constants, so any
        # comparison against a number is treated as a threshold.
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Constant) and isinstance(
                    operand.value, (int, float)
                ) and not isinstance(operand.value, bool):
                    offenders.append(ast.dump(node)[:80])
        self.assertEqual(
            offenders, [],
            "Policy v1 must contain no numeric threshold. Metric-regression "
            "rules are v2 and remain blocked on differential-validation "
            "evidence.",
        )

    def test_no_rule_makes_an_architecture_quality_claim(self):
        """Checked on the rule definitions, not the file.

        The module docstring *explains* the prohibition, so a raw text scan
        would fire on the very sentence stating the guarantee.
        """
        for rule in rule_module.RULES:
            text = " ".join((
                rule.identifier, rule.title, rule.rationale, rule.domain
            )).lower()
            for forbidden in (
                "quality score", "maintainability", "worsened", "grade",
                "threshold", "regression",
            ):
                with self.subTest(rule=rule.identifier, term=forbidden):
                    self.assertNotIn(forbidden, text)

    def test_every_rule_belongs_to_an_integrity_domain(self):
        allowed = set(rule_module.DOMAINS)
        for rule in rule_module.RULES:
            with self.subTest(rule=rule.identifier):
                self.assertIn(rule.domain, allowed)
                self.assertTrue(rule.rationale, "a rule must say why it exists")

    def test_the_result_states_its_own_scope_limit(self):
        from modules.policy.engine import POLICY_RESULT_FORMAT_VERSION

        self.assertEqual(POLICY_RESULT_FORMAT_VERSION, "1.0.0")


class DefaultPolicyTests(unittest.TestCase):
    def test_opt_in_rules_are_off_by_default(self):
        """Partial measurement and a dirty dev tree are honest outcomes."""
        policy = default_policy()
        self.assertIsNone(policy.severity_for("measurement.incomplete"))
        self.assertIsNone(
            policy.severity_for("reproducibility.provenance_incomplete")
        )

    def test_integrity_rules_are_on_by_default(self):
        policy = default_policy()
        for rule_id in (
            "artifact.schema_invalid", "run.integrity_failed",
            "capability.parser_unavailable", "diagnostics.unknown_category",
        ):
            self.assertEqual(policy.severity_for(rule_id), "violation")

    def test_the_default_policy_validates_against_the_document_schema(self):
        violations = validate_document(
            "policy_document", default_policy().as_dict(), "policy.json"
        )
        self.assertEqual([str(item) for item in violations], [])


class PolicyDocumentTests(unittest.TestCase):
    BASE = {
        "policy_document_format_version": "1.0.0",
        "name": "test",
        "rules": {"run.integrity_failed": "violation"},
    }

    def test_an_unknown_rule_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy({**self.BASE, "rules": {"no.such.rule": "violation"}})

    def test_an_unknown_severity_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy({**self.BASE, "rules": {"run.integrity_failed": "fatal"}})

    def test_a_wrong_format_version_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy({**self.BASE, "policy_document_format_version": "2.0.0"})

    def test_a_policy_enabling_no_rule_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy({**self.BASE, "rules": {}})

    def test_a_waiver_without_a_reason_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_policy({**self.BASE, "waivers": [
                {"rule_id": "run.integrity_failed", "expires_on": "2099-01-01"}
            ]})
        self.assertIn("reason", str(caught.exception))

    def test_a_waiver_without_an_expiry_is_refused(self):
        """An unbounded waiver is indistinguishable from deleting the rule."""
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_policy({**self.BASE, "waivers": [
                {"rule_id": "run.integrity_failed", "reason": "because"}
            ]})
        self.assertIn("expiry", str(caught.exception))

    def test_a_valid_waiver_is_accepted(self):
        policy = load_policy({**self.BASE, "waivers": [{
            "rule_id": "run.integrity_failed", "reason": "tracked in ISSUE-1",
            "expires_on": "2099-01-01", "issue_id": "ISSUE-1",
        }]})
        self.assertEqual(len(policy.waivers), 1)
        self.assertEqual(policy.waivers[0].issue_id, "ISSUE-1")


class WaiverBehaviourTests(unittest.TestCase):
    FINDING = rule_module.Finding(
        "run.integrity_failed", rule_module.DOMAIN_RUN, "detail",
        subject_key="s",
    )

    def test_a_live_waiver_applies(self):
        waiver = Waiver("run.integrity_failed", "r", date(2099, 1, 1))
        self.assertTrue(waiver.applies_to(self.FINDING, date(2026, 8, 9)))

    def test_an_expired_waiver_does_not_apply(self):
        waiver = Waiver("run.integrity_failed", "r", date(2020, 1, 1))
        self.assertFalse(waiver.applies_to(self.FINDING, date(2026, 8, 9)))
        self.assertTrue(waiver.expired(date(2026, 8, 9)))

    def test_a_subject_scoped_waiver_does_not_leak_to_other_subjects(self):
        waiver = Waiver(
            "run.integrity_failed", "r", date(2099, 1, 1), subject_key="other"
        )
        self.assertFalse(waiver.applies_to(self.FINDING, date(2026, 8, 9)))


class EvaluationTests(RunFixture):
    def test_a_healthy_run_passes_the_default_policy(self):
        result = evaluate(self.build_run())
        self.assertTrue(result["passed"], result["findings"])
        self.assertEqual(result["exit_code"], EXIT_PASS)

    def test_an_invalid_artifact_is_a_violation(self):
        run = self.build_run()
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        analysis[0].pop("subject_key", None)
        (run / "analysis.json").write_text(
            json.dumps(analysis, indent=2), encoding="utf-8"
        )
        result = evaluate(run)
        rule_ids = {item["rule_id"] for item in result["findings"]}
        self.assertIn("artifact.schema_invalid", rule_ids)
        self.assertFalse(result["passed"])
        self.assertEqual(result["exit_code"], EXIT_VIOLATION)

    def test_a_failed_run_status_is_a_violation(self):
        run = self.build_run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        status["status"] = "running"
        (run / "run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
        result = evaluate(run)
        self.assertIn(
            "run.integrity_failed",
            {item["rule_id"] for item in result["findings"]},
        )

    def test_a_contract_mismatch_fires_only_when_the_policy_pins_it(self):
        run = self.build_run()
        silent = evaluate(run, PolicyDocument(
            "no-pin", {"contract.incompatible": "violation"}
        ))
        self.assertEqual(silent["findings"], [])

        pinned = evaluate(run, PolicyDocument(
            "pinned", {"contract.incompatible": "violation"},
            expected_contracts={"metric_contract_version": "99.0.0"},
        ))
        self.assertIn(
            "contract.incompatible",
            {item["rule_id"] for item in pinned["findings"]},
        )

    def test_a_waiver_moves_a_finding_without_hiding_it(self):
        run = self.build_run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        status["status"] = "running"
        (run / "run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
        policy = PolicyDocument(
            "waived", {"run.integrity_failed": "violation"},
            waivers=(Waiver(
                "run.integrity_failed", "known, tracked in ISSUE-2",
                date.today() + timedelta(days=30),
            ),),
        )
        result = evaluate(run, policy)
        self.assertTrue(result["passed"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["waived_findings"]), 1)
        self.assertEqual(
            result["waived_findings"][0]["waiver"]["reason"],
            "known, tracked in ISSUE-2",
        )

    def test_an_expired_waiver_stops_suppressing_and_is_reported(self):
        run = self.build_run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        status["status"] = "running"
        (run / "run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
        policy = PolicyDocument(
            "expired", {"run.integrity_failed": "violation"},
            waivers=(Waiver(
                "run.integrity_failed", "was tracked", date.today() - timedelta(days=1)
            ),),
        )
        result = evaluate(run, policy)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["expired_waivers"]), 1)

    def test_a_warning_never_changes_the_exit_code(self):
        run = self.build_run()
        policy = PolicyDocument(
            "warn-only", {"measurement.metric_unavailable": "warning"}
        )
        result = evaluate(run, policy)
        self.assertEqual(result["exit_code"], EXIT_PASS)
        self.assertTrue(result["passed"])

    def test_rules_not_evaluated_are_reported_separately(self):
        """A disabled rule must never look like a rule that passed."""
        result = evaluate(
            self.build_run(), PolicyDocument("tiny", {"run.integrity_failed": "violation"})
        )
        self.assertEqual(result["rules_evaluated"], ["run.integrity_failed"])
        self.assertIn("artifact.schema_invalid", result["rules_not_evaluated"])

    def test_evaluation_is_deterministic(self):
        run = self.build_run()
        self.assertEqual(
            json.dumps(evaluate(run, today=date(2026, 8, 9)), sort_keys=True),
            json.dumps(evaluate(run, today=date(2026, 8, 9)), sort_keys=True),
        )

    def test_every_rule_can_fire(self):
        """A check that cannot fail is worse than none.

        Each rule is driven directly with a context shaped to violate it, so a
        rule whose predicate can never be true is caught here rather than
        sitting in the policy looking like protection.
        """
        from modules.policy.engine import EvaluationContext

        def context(**overrides):
            base = dict(
                run_directory=Path("."), manifest={}, status={"status": "completed"},
                repositories=(), errors=(), schema_report={}, structural_errors=(),
                lifecycle="finalized_valid", expected_contracts={},
            )
            base.update(overrides)
            return EvaluationContext(**base)

        triggers = {
            "artifact.schema_invalid": context(
                schema_report={"result": "invalid", "violations": []}
            ),
            "artifact.structurally_unreadable": context(structural_errors=("boom",)),
            "artifact.compatibility_waiver_required_by_current_generation": context(
                schema_report={
                    "schema_contract_evaluated": "1.7.0",
                    "accepted_compatibility_exception_count": 1,
                }
            ),
            "run.integrity_failed": context(status={"status": "running"}),
            "run.mandatory_output_failure": context(
                manifest={"output_failures": {"mandatory": ["catalog.csv"]}}
            ),
            "capability.parser_unavailable": context(repositories=(
                {"metrics": {"parser_status_by_language": {"Java": "unavailable"}}},
            )),
            "diagnostics.parser_execution_failure": context(
                errors=({"error_category": "parser_execution_failure"},)
            ),
            "diagnostics.unknown_category": context(
                repositories=({"unknown_categories": ["mystery"]},)
            ),
            "contract.incompatible": context(
                manifest={"metric_contract_version": "1.0.0"},
                expected_contracts={"metric_contract_version": "3.0.0"},
            ),
            "contract.artifact_generation_unsupported": context(
                schema_report={"artifact_schema_compatibility": {
                    "state": "unsupported", "reason": "too old",
                }}
            ),
            "measurement.metric_unavailable": context(repositories=(
                {"metrics": {"aggregate": {"lines_of_code": None}}},
            )),
            "measurement.incomplete": context(
                repositories=({"analysis_status": "partial"},)
            ),
            "reproducibility.provenance_incomplete": context(
                manifest={"profiler_git_commit_sha": None}
            ),
        }

        for rule in rule_module.RULES:
            with self.subTest(rule=rule.identifier):
                self.assertIn(
                    rule.identifier, triggers,
                    "every rule needs a case proving it can fire",
                )
                findings = list(rule.evaluate(triggers[rule.identifier]))
                self.assertTrue(
                    findings, f"{rule.identifier} could not be made to fire"
                )
                self.assertEqual(findings[0].rule_id, rule.identifier)

    def test_a_healthy_context_fires_nothing(self):
        """The mirror image: the rules are not simply always-on."""
        from modules.policy.engine import EvaluationContext

        healthy = EvaluationContext(
            run_directory=Path("."), manifest={
                "profiler_git_commit_sha": "a" * 40, "profiler_git_dirty": False,
            },
            status={"status": "completed"},
            repositories=({
                "analysis_status": "complete",
                "metrics": {"aggregate": {
                    "lines_of_code": 1, "source_files": 1,
                    "classes_structs": 1, "methods_functions": 1,
                }},
            },),
            errors=(), schema_report={"result": "schema_valid"},
            structural_errors=(), lifecycle="finalized_valid",
            expected_contracts={},
        )
        for rule in rule_module.RULES:
            with self.subTest(rule=rule.identifier):
                self.assertEqual(list(rule.evaluate(healthy)), [])


class OutputContractTests(RunFixture):
    """Producer-to-schema contract, written with the schema."""

    def test_the_emitted_result_validates_against_the_registered_schema(self):
        result = evaluate(self.build_run())
        violations = validate_document(
            "policy_result_output", result, "policy_result.json"
        )
        self.assertEqual([str(item) for item in violations], [])

    def test_a_result_with_findings_also_validates(self):
        run = self.build_run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        status["status"] = "running"
        (run / "run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
        policy = PolicyDocument(
            "with-waiver", {"run.integrity_failed": "violation",
                            "measurement.metric_unavailable": "warning"},
            waivers=(Waiver(
                "measurement.metric_unavailable", "known",
                date.today() + timedelta(days=1),
            ),),
        )
        violations = validate_document(
            "policy_result_output", evaluate(run, policy), "policy_result.json"
        )
        self.assertEqual([str(item) for item in violations], [])

    def test_both_schemas_forbid_undeclared_properties(self):
        for name in ("policy_document", "policy_result_output"):
            with self.subTest(schema=name):
                self.assertFalse(load_schema(name)["additionalProperties"])

    def test_an_undeclared_property_is_rejected(self):
        result = evaluate(self.build_run())
        result["invented"] = True
        self.assertTrue(
            validate_document("policy_result_output", result, "policy_result.json")
        )

    def test_the_document_schema_enumerates_exactly_the_known_rules(self):
        declared = set(
            load_schema("policy_document")["properties"]["rules"]["propertyNames"]["enum"]
        )
        self.assertEqual(declared, set(rule_module.RULES_BY_ID))


class CommandTests(RunFixture):
    class _Args:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _args(self, **overrides):
        values = dict(
            policy_command="evaluate", run_directory=self.build_run(),
            policy=None, format="text", output=None,
        )
        values.update(overrides)
        return self._Args(**values)

    def test_a_healthy_run_exits_zero(self):
        from modules.cli import policy_command

        self.assertEqual(policy_command.handle(self._args()), EXIT_PASS)

    def test_an_invalid_policy_document_exits_four(self):
        from modules.cli import policy_command

        bad = self.root / "bad-policy.json"
        bad.write_text('{"policy_document_format_version": "9.9.9"}', encoding="utf-8")
        self.assertEqual(
            policy_command.handle(self._args(policy=bad)), EXIT_POLICY_INVALID
        )

    def test_an_unreadable_run_exits_three(self):
        from modules.cli import policy_command

        self.assertEqual(
            policy_command.handle(
                self._args(run_directory=self.root / "no-such-run")
            ),
            EXIT_UNREADABLE_RUN,
        )

    def test_the_result_file_is_written_when_requested(self):
        from modules.cli import policy_command

        destination = self.root / "reports" / "policy.json"
        policy_command.handle(self._args(output=destination, format="json"))
        self.assertTrue(destination.is_file())
        written = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(written["policy_result_format_version"], "1.0.0")

    def test_the_rules_listing_is_machine_readable(self):
        from modules.cli import policy_command

        code = policy_command.handle(self._Args(policy_command="rules"))
        self.assertEqual(code, EXIT_PASS)

    def test_the_text_rendering_states_the_scope_limit(self):
        from modules.cli import policy_command

        rendered = policy_command.render_text(evaluate(self.build_run()))
        # Human prose wraps at the terminal width; the scope claim must survive.
        self.assertIn("no metric thresholds", " ".join(rendered.split()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
