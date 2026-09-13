"""Phase 9 tests: ``compare --explain`` (plan section 15)."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from modules.cli.compare_command import (
    EXIT_DIFFERENT,
    EXIT_EQUAL,
    EXIT_INCOMPARABLE,
    EXIT_INVALID_ARTIFACTS,
    EXIT_USAGE,
    EXACT_RECONCILIATION,
    NOT_EVALUABLE,
    OBSERVED_DIFFERENCE,
    REPOSITORY_ABSENT_ONE_SIDE,
    build_comparison,
    render_text,
)
from validation.artifact_io.reader import open_run

REPOSITORY = Path(__file__).resolve().parent.parent

from tests import historical_fixtures


def find_run(version="1.4.0"):
    """The tracked historical fixture for `version`.

    Resolved from the fixture manifest rather than by scanning the tree. The
    scan it replaces could satisfy itself from gitignored local output, so a
    fresh clone found nothing and the caller skipped -- silently reporting no
    historical coverage as a pass. A missing fixture now raises.
    """
    return historical_fixtures.run_for(version)


class RequiresRuns(unittest.TestCase):
    def setUp(self):
        self.source = find_run()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def copy(self, name):
        target = self.root / name
        shutil.copytree(self.source, target)
        return target

    def edit_analysis(self, run, mutate):
        path = run / "analysis.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def numeric_index(self, run):
        """Index of a repository with a numeric LOC.

        The fixture's first element is the 7ep acquisition-failure control,
        whose lines_of_code is already null. Tests about numeric deltas need a
        repository that actually has a number.
        """
        payload = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        for index, item in enumerate(payload):
            if isinstance(
                item.get("metrics", {}).get("aggregate", {}).get("lines_of_code"), int
            ):
                return index
        self.skipTest("no repository with a numeric lines_of_code in the fixture")


class DefaultBehaviourTests(unittest.TestCase):
    """The default N-run compare contract must not change."""

    def test_default_compare_help_still_accepts_many_runs(self):
        result = subprocess.run(
            [sys.executable, "pipeline.py", "compare", "--help"],
            cwd=str(REPOSITORY), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("run_directories", result.stdout)
        self.assertIn("--explain", result.stdout)

    def test_usage_error_when_fewer_than_two_runs(self):
        result = subprocess.run(
            [sys.executable, "pipeline.py", "compare", "only-one"],
            cwd=str(REPOSITORY), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, EXIT_USAGE)


class ExplainExitCodeTests(RequiresRuns):
    def _run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "pipeline.py", "compare", *arguments, "--explain"],
            cwd=str(REPOSITORY), capture_output=True, text=True,
        )

    def test_identical_runs_exit_zero(self):
        a, b = self.copy("a"), self.copy("b")
        self.assertEqual(self._run_cli(str(a), str(b)).returncode, EXIT_EQUAL)

    def test_differing_runs_exit_one(self):
        a, b = self.copy("a"), self.copy("b")
        self.edit_analysis(
            b, lambda payload: payload[self.numeric_index(a)]["metrics"]["aggregate"].update(
                {"lines_of_code": 999999}
            )
        )
        self.assertEqual(self._run_cli(str(a), str(b)).returncode, EXIT_DIFFERENT)

    def test_three_runs_is_a_usage_error_in_explain_mode(self):
        a, b, c = self.copy("a"), self.copy("b"), self.copy("c")
        result = self._run_cli(str(a), str(b), str(c))
        self.assertEqual(result.returncode, EXIT_USAGE)
        self.assertIn("exactly two", result.stdout + result.stderr)

    def test_invalid_artifacts_exit_three(self):
        a, b = self.copy("a"), self.copy("b")
        (b / "run_manifest.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(self._run_cli(str(a), str(b)).returncode, EXIT_INVALID_ARTIFACTS)

    def test_metric_contract_mismatch_exits_four(self):
        a, b = self.copy("a"), self.copy("b")
        path = b / "run_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metric_contract_version"] = "9.9.9"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self._run_cli(str(a), str(b)).returncode, EXIT_INCOMPARABLE)


class KeyAlignmentTests(RequiresRuns):
    def test_an_inserted_repository_does_not_cascade(self):
        a, b = self.copy("a"), self.copy("b")

        def insert(payload):
            clone = json.loads(json.dumps(payload[0]))
            clone["repository_url"] = "https://github.com/aaa/inserted-first"
            payload.insert(0, clone)

        self.edit_analysis(b, insert)
        payload = build_comparison(open_run(a), open_run(b))

        # Exactly one difference: the inserted repository is absent on one side.
        # Positional comparison would report every later repository as changed.
        self.assertEqual(len(payload["differences"]), 1)
        difference = payload["differences"][0]
        self.assertEqual(difference["key"], "https://github.com/aaa/inserted-first")
        self.assertEqual(difference["not_evaluable_reason"], REPOSITORY_ABSENT_ONE_SIDE)
        self.assertEqual(difference["evidence_level"], NOT_EVALUABLE)

    def test_argument_reversal_reports_the_same_repository_set(self):
        a, b = self.copy("a"), self.copy("b")
        self.edit_analysis(
            b, lambda payload: payload[self.numeric_index(a)]["metrics"]["aggregate"].update(
                {"lines_of_code": 12345}
            )
        )
        forward = build_comparison(open_run(a), open_run(b))
        reverse = build_comparison(open_run(b), open_run(a))
        self.assertEqual(
            {item["key"] for item in forward["differences"]},
            {item["key"] for item in reverse["differences"]},
        )

    def test_argument_reversal_negates_the_observed_delta(self):
        a, b = self.copy("a"), self.copy("b")
        index = self.numeric_index(a)
        original = json.loads((a / "analysis.json").read_text(encoding="utf-8"))
        base = original[index]["metrics"]["aggregate"]["lines_of_code"]
        self.edit_analysis(
            b, lambda payload: payload[index]["metrics"]["aggregate"].update(
                {"lines_of_code": base + 10}
            )
        )
        forward = build_comparison(open_run(a), open_run(b))
        reverse = build_comparison(open_run(b), open_run(a))

        def delta(payload):
            return next(
                item["observed_delta"] for item in payload["differences"]
                if item["dimension"] == "lines_of_code"
            )

        self.assertEqual(delta(forward), 10)
        self.assertEqual(delta(reverse), -10)


class MissingEvidenceTests(RequiresRuns):
    def test_missing_contribution_ledger_blocks_exact_reconciliation(self):
        a, b = self.copy("a"), self.copy("b")
        payload = build_comparison(open_run(a), open_run(b))
        self.assertFalse(payload["exact_reconciliation_available"])
        self.assertTrue(
            any("contribution ledger" in item
                for item in payload["exact_reconciliation_blockers"])
        )

    def test_missing_inventory_is_reported_as_a_named_blocker(self):
        a, b = self.copy("a"), self.copy("b")
        shutil.rmtree(b / "file_inventory")
        payload = build_comparison(open_run(a), open_run(b))
        self.assertTrue(
            any("inventory" in item for item in payload["exact_reconciliation_blockers"])
        )

    def test_null_metric_is_not_evaluable_not_a_zero_delta(self):
        a, b = self.copy("a"), self.copy("b")
        index = self.numeric_index(a)
        self.edit_analysis(
            b, lambda payload: payload[index]["metrics"]["aggregate"].update(
                {"lines_of_code": None}
            )
        )
        payload = build_comparison(open_run(a), open_run(b))
        difference = next(
            item for item in payload["differences"]
            if item["dimension"] == "lines_of_code"
        )
        self.assertEqual(difference["evidence_level"], NOT_EVALUABLE)
        self.assertIsNone(difference["residual"])
        self.assertIn("never treated as zero", difference["not_evaluable_reason"])

    def test_missing_optional_projection_does_not_make_runs_incomparable(self):
        a, b = self.copy("a"), self.copy("b")
        shutil.rmtree(b / "repositories")
        payload = build_comparison(open_run(a), open_run(b))
        self.assertTrue(payload["comparable"])


class WordingTests(RequiresRuns):
    def test_no_causal_wording_in_the_rendered_output(self):
        a, b = self.copy("a"), self.copy("b")
        self.edit_analysis(
            b, lambda payload: payload[self.numeric_index(a)]["metrics"]["aggregate"].update(
                {"lines_of_code": 4242}
            )
        )
        text = render_text(build_comparison(open_run(a), open_run(b))).lower()
        for forbidden in ("caused by", "because of", "harmless", "safe to ignore"):
            with self.subTest(phrase=forbidden):
                self.assertNotIn(forbidden, text)

    def test_text_and_json_agree_on_the_difference_set(self):
        a, b = self.copy("a"), self.copy("b")
        self.edit_analysis(
            b, lambda payload: payload[self.numeric_index(a)]["metrics"]["aggregate"].update(
                {"lines_of_code": 4242}
            )
        )
        payload = build_comparison(open_run(a), open_run(b))
        text = render_text(payload)
        for item in payload["differences"]:
            with self.subTest(key=item["key"], dimension=item["dimension"]):
                self.assertIn(item["key"], text)
                self.assertIn(item["dimension"], text)

    def test_equal_runs_state_no_difference_observed(self):
        a, b = self.copy("a"), self.copy("b")
        text = render_text(build_comparison(open_run(a), open_run(b)))
        self.assertIn("No differences observed", text)


class RepositoryFilterTests(RequiresRuns):
    def test_repo_filter_restricts_the_comparison(self):
        a, b = self.copy("a"), self.copy("b")
        self.edit_analysis(
            b, lambda payload: [
                item["metrics"]["aggregate"].update({"lines_of_code": 777})
                for item in payload
            ],
        )
        url = json.loads((a / "analysis.json").read_text(encoding="utf-8"))[0][
            "repository_url"
        ]
        payload = build_comparison(open_run(a), open_run(b), repository=url)
        self.assertTrue(payload["differences"])
        self.assertEqual({item["key"] for item in payload["differences"]}, {url})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
