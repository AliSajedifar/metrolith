"""Regression tests for the audit hypotheses that reproduced (P-1 … P-11).

Each test below corresponds to a hypothesis raised from code reading and then
*confirmed by reproduction* before anything was changed. Hypotheses that did not
reproduce were closed rather than "fixed"; the one that mattered is recorded in
:class:`ClosedHypothesisTests`.

The common theme is the same one that runs through the whole audit: an
unavailable measurement must never be quietly turned into a value — not into
zero, not into an empty string, and not into a confident verdict.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from modules.cli import compare_command, performance_command, report_command
from modules.contribution_ledger import (
    RECONCILIATION_EXACT,
    RECONCILIATION_NOT_EVALUABLE,
    RECONCILIATION_RESIDUAL,
    reconcile_repository,
)
from tests import historical_fixtures
from validation.artifact_io.compatibility import (
    RunLifecycle,
    classify_artifact_schema,
    classify_lifecycle,
)
from validation.artifact_io.errors import ArtifactStructureError
from validation.artifact_io.reader import open_run
from validation.artifact_io.strict_csv import (
    CellType,
    ColumnSpec,
    TableContract,
    read_rows,
)

CONTRIBUTED = "contributed"


def _aggregate(**overrides) -> dict:
    base = {
        "loc_status": "complete",
        "classes_structs_status": "complete",
        "methods_functions_status": "complete",
    }
    base.update(overrides)
    return {"metrics": {"aggregate": base}}


def _reference_run() -> Path:
    """A run with integer aggregate metrics and a contribution ledger.

    Not just any run: a failed-acquisition control has null metrics, so
    perturbing it produces no observable difference and the reconciliation path
    under test is never reached.
    """
    candidate = historical_fixtures.run_for("1.5.0")
    analysis = candidate / "analysis.json"
    if not analysis.is_file() or not (candidate / "contributions.csv").is_file():
        raise AssertionError(
            "the mandatory Artifact 1.5 fixture must include analysis.json and "
            "contributions.csv for ledger-cost coverage"
        )
    document = json.loads(analysis.read_text(encoding="utf-8"))
    aggregate = (
        (document[0].get("metrics") or {}).get("aggregate") or {}
        if document
        else {}
    )
    if not any(
        isinstance(aggregate.get(key), int)
        for key in ("lines_of_code", "source_files")
    ):
        raise AssertionError(
            "the mandatory Artifact 1.5 fixture must carry integer aggregate "
            "metrics for ledger-cost coverage"
        )
    return candidate


class ContributionReconciliationTests(unittest.TestCase):
    """P-4. Source Files must obey the same rules as every other dimension.

    Rule 1 passed a hardcoded ``status="complete"`` and summed contributions
    with ``int(value or 0)``, so Source Files alone was exempt from the
    null-on-failed rule and lost the count of rows that recorded nothing.

    Note what is deliberately *not* changed: a leftover difference is still an
    ``unexplained_residual`` even when some rows recorded no value.
    ``core_metrics`` builds the aggregate by summing only the files whose
    extraction produced components, so a null row contributed nothing to the
    aggregate either — both sides skip the same rows, and a remaining gap is
    real. An earlier attempt at this fix reported such cases as
    ``not_evaluable`` and was caught by
    ``test_a_partial_repository_that_does_not_sum_reports_a_residual``.
    """

    def _source_files(self, result, rows):
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        return found["source_files"]

    def test_failed_status_with_a_null_aggregate_is_not_evaluable(self):
        """Rule 4 now reaches Source Files, with its own reason."""
        outcome = self._source_files(
            _aggregate(source_files=None, source_files_status="failed"),
            [{"contribution_state": CONTRIBUTED, "source_files_contribution": 1}],
        )
        self.assertEqual(outcome.outcome, RECONCILIATION_NOT_EVALUABLE)
        self.assertIsNone(outcome.residual)
        self.assertIn("failed", outcome.reason)

    def test_unrecorded_rows_are_counted_not_silently_zeroed(self):
        """The skipped count reaches the report instead of being absorbed by `or 0`."""
        outcome = self._source_files(
            _aggregate(source_files=1, source_files_status="partial"),
            [
                {"contribution_state": CONTRIBUTED, "source_files_contribution": 1},
                {"contribution_state": CONTRIBUTED, "source_files_contribution": None},
            ],
        )
        self.assertEqual(outcome.outcome, RECONCILIATION_EXACT)
        self.assertEqual(outcome.contribution_sum, 1)
        self.assertIn("recorded no value", outcome.reason)

    def test_matching_totals_still_reconcile_exactly(self):
        outcome = self._source_files(
            _aggregate(source_files=2, source_files_status="complete"),
            [{"contribution_state": CONTRIBUTED, "source_files_contribution": 1}] * 2,
        )
        self.assertEqual(outcome.outcome, RECONCILIATION_EXACT)

    def test_a_genuine_disagreement_is_still_reported(self):
        """The fix must not silence real mismatches."""
        outcome = self._source_files(
            _aggregate(source_files=7, source_files_status="complete"),
            [{"contribution_state": CONTRIBUTED, "source_files_contribution": 1}] * 2,
        )
        self.assertEqual(outcome.outcome, RECONCILIATION_RESIDUAL)
        self.assertEqual(outcome.residual, 5)

    def test_an_unexplained_gap_alongside_unrecorded_rows_is_still_a_residual(self):
        """Pins the semantics my first attempt at P-4 got wrong."""
        outcome = self._source_files(
            _aggregate(source_files=10, source_files_status="partial"),
            [
                {"contribution_state": CONTRIBUTED, "source_files_contribution": 1},
                {"contribution_state": CONTRIBUTED, "source_files_contribution": None},
            ],
        )
        self.assertEqual(outcome.outcome, RECONCILIATION_RESIDUAL)
        self.assertEqual(outcome.residual, 9)


class RaggedRowTests(unittest.TestCase):
    """P-5. Truncation must not read as "unavailable"."""

    def setUp(self):
        self.contract = TableContract(name="t", columns=(
            ColumnSpec("a", CellType.STRING),
            ColumnSpec("b", CellType.INTEGER),
            ColumnSpec("c", CellType.STRING),
        ))
        self.path = Path(tempfile.mkdtemp()) / "t.csv"

    def test_short_row_is_refused(self):
        self.path.write_text("a,b,c\nx,1\n", encoding="utf-8")
        with self.assertRaises(ArtifactStructureError):
            read_rows(self.path, "t.csv", self.contract)

    def test_long_row_is_still_refused(self):
        self.path.write_text("a,b,c\nx,1,y,z\n", encoding="utf-8")
        with self.assertRaises(ArtifactStructureError):
            read_rows(self.path, "t.csv", self.contract)

    def test_complete_row_is_accepted(self):
        self.path.write_text("a,b,c\nx,1,y\n", encoding="utf-8")
        self.assertEqual(
            read_rows(self.path, "t.csv", self.contract),
            [{"a": "x", "b": 1, "c": "y"}],
        )

    def test_a_genuinely_empty_cell_still_means_unavailable(self):
        self.path.write_text("a,b,c\nx,,y\n", encoding="utf-8")
        self.assertIsNone(read_rows(self.path, "t.csv", self.contract)[0]["b"])


class PerformanceComparabilityTests(unittest.TestCase):
    """P-2. An incomparable pair must not receive a noise-floor verdict."""

    BASELINE = {
        "cohort_or_case_hash": "A", "workers": 1, "acquisition_mode": "offline",
        "host_class": "H1", "raw_samples": [1.0], "median_seconds": 1.0,
        "absolute_noise_floor_seconds": 0.05, "parser_versions": {},
    }

    def test_the_most_severe_verdict_wins(self):
        candidate = dict(
            self.BASELINE, cohort_or_case_hash="B", host_class="H2",
            median_seconds=9.0, raw_samples=[9.0],
        )
        report = performance_command.compare_profiles(self.BASELINE, candidate)
        self.assertEqual(
            report["comparability_verdict"], performance_command.INCOMPARABLE_SHAPE
        )
        # Both reasons stay visible even though one verdict is reported.
        self.assertEqual(len(report["incomparability_reasons"]), 2)

    def test_no_noise_verdict_is_offered_when_incomparable(self):
        candidate = dict(self.BASELINE, cohort_or_case_hash="B", median_seconds=9.0)
        report = performance_command.compare_profiles(self.BASELINE, candidate)
        self.assertIsNone(report["within_noise_floor"])
        self.assertIsNone(report["median_delta_seconds"])
        self.assertNotIn("Within noise floor", performance_command.render_text(report))

    def test_comparable_profiles_still_get_a_verdict(self):
        candidate = dict(self.BASELINE, median_seconds=1.01, raw_samples=[1.01])
        report = performance_command.compare_profiles(self.BASELINE, candidate)
        self.assertEqual(report["comparability_verdict"], performance_command.COMPARABLE)
        self.assertTrue(report["within_noise_floor"])
        self.assertIn("Within noise floor", performance_command.render_text(report))

    def test_program_version_difference_stays_comparable(self):
        """Comparing one build against another is the point of a baseline."""
        candidate = dict(self.BASELINE, program_version="9.9.9")
        report = performance_command.compare_profiles(self.BASELINE, candidate)
        self.assertEqual(report["comparability_verdict"], performance_command.COMPARABLE)


class CompareComparabilityTests(unittest.TestCase):
    """P-3. The Exclusion Policy decides which files count, so it gates."""

    class _View:
        compatibility = type("C", (), {"readable": True, "reason": "ok"})()
        structural_errors = ()
        has_contribution_ledger = True
        has_inventory = True
        normalized_input = ()
        repositories_by_url: dict = {}
        run_id = "x"
        run_directory = "d"

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _manifest(self, **overrides):
        base = {
            "metric_contract_version": "3.0.0",
            "exclusion_policy_version": "1.5.0",
            "exclusion_policy_sha256": "AAA",
        }
        base.update(overrides)
        return base

    def test_differing_exclusion_policy_is_incomparable(self):
        left = self._View(manifest=self._manifest())
        right = self._View(
            manifest=self._manifest(
                exclusion_policy_version="1.4.0", exclusion_policy_sha256="BBB"
            )
        )
        report = compare_command.build_comparison(left, right)
        self.assertFalse(report["comparable"])
        self.assertIn("Exclusion Policy", report["incomparable_reason"])

    def test_identical_contracts_are_comparable(self):
        left = self._View(manifest=self._manifest())
        right = self._View(manifest=self._manifest())
        self.assertTrue(compare_command.build_comparison(left, right)["comparable"])


class LedgerReadCostTests(unittest.TestCase):
    """P-1. One ledger pass per side, not one per repository per dimension."""

    def setUp(self):
        source = _reference_run()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.left, self.right = root / "a", root / "b"
        shutil.copytree(source, self.left)
        shutil.copytree(source, self.right)
        # Same repository on both sides, every metric dimension differing, so
        # reconciliation actually runs.
        document = json.loads((self.right / "analysis.json").read_text(encoding="utf-8"))
        aggregate = document[0]["metrics"]["aggregate"]
        for key in (
            "lines_of_code", "source_files", "classes_structs", "methods_functions"
        ):
            if isinstance(aggregate.get(key), int):
                aggregate[key] += 1
        (self.right / "analysis.json").write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )

    def test_ledger_is_streamed_once_per_side(self):
        left, right = open_run(self.left), open_run(self.right)
        calls = {"n": 0}
        for view in (left, right):
            original = view.stream_contributions
            view.stream_contributions = (
                lambda _original=original: (
                    calls.__setitem__("n", calls["n"] + 1), _original()
                )[1]
            )
        report = compare_command.build_comparison(left, right)

        differing = [
            item for item in report["differences"]
            if item["dimension"] in compare_command.METRIC_DIMENSIONS
        ]
        self.assertTrue(differing, "the fixture must produce metric differences")
        self.assertLessEqual(
            calls["n"], 2,
            f"expected at most one ledger pass per side, saw {calls['n']}",
        )


class NullRenderingTests(unittest.TestCase):
    """P-8. `escape_html` returns text; only cells emit markup."""

    def test_escape_html_returns_text_for_none(self):
        self.assertEqual(report_command.escape_html(None), "not supplied")
        self.assertNotIn("<span", report_command.escape_html(None))

    def test_table_cells_still_mark_nulls_visually(self):
        self.assertEqual(report_command._cell(None), "<td>unavailable</td>")


class LifecycleClassificationTests(unittest.TestCase):
    """P-10. A terminal status with no manifest is damage, not `running`."""

    def test_terminal_status_without_a_manifest_is_corrupt(self):
        verdict = classify_lifecycle(
            {"status": "completed"}, None, classify_artifact_schema("1.5.0")
        )
        self.assertIs(verdict, RunLifecycle.CORRUPT)

    def test_running_status_is_still_running(self):
        verdict = classify_lifecycle(
            {"status": "running"}, None, classify_artifact_schema("1.5.0")
        )
        self.assertIs(verdict, RunLifecycle.RUNNING)


class ClosedHypothesisTests(unittest.TestCase):
    """Hypotheses that did not reproduce, pinned so they stay closed.

    P-2 originally included "program and parser versions are not compared" as a
    defect. Reproduction showed that treating a *program version* difference as
    incomparable would break the primary use of a performance baseline —
    comparing a new build against one recorded by an older build. Only the
    parser-grammar half was a genuine confounder and gates today.
    """

    def test_parser_version_difference_is_an_environment_mismatch(self):
        baseline = dict(PerformanceComparabilityTests.BASELINE, parser_versions={"go": "1"})
        candidate = dict(baseline, parser_versions={"go": "2"})
        report = performance_command.compare_profiles(baseline, candidate)
        self.assertEqual(
            report["comparability_verdict"],
            performance_command.INCOMPARABLE_ENVIRONMENT,
        )


if __name__ == "__main__":
    unittest.main()
