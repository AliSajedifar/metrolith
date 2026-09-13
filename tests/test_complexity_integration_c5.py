"""C5: Complexity Bundle product integration, and its compatibility matrix.

The rule every case here exists to hold is one sentence long: **absent, null and
measured zero are three different facts.** A run that predates Complexity
Contract 1.0.0 recorded nothing; a run that tried and failed recorded an
unavailable value; a repository with no callables recorded a real zero. Rendering
any of them as another is the failure this suite is built to catch.

The second rule is the compatibility one: **complexity is never a global
blocker.** A diff whose sides disagree about complexity — or where one side has
none at all — must still produce its Metric Contract 3.0.0 deltas.

Runs are produced by the real `pipeline analyze`, not by hand-built fixtures: a
hand-built artifact can be made to say anything, including things the producer
would never write.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from html import escape as html_escape

from modules import complexity_view
from validation.artifact_io.reader import open_run

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPOSITORY_ROOT / "validation/differential/corpus/complexity"


def analyze(source: Path, output_root: Path) -> Path:
    """One real local analysis, returning its run directory."""
    completed = subprocess.run(
        [
            sys.executable, "-m", "pipeline", "analyze", str(source),
            "--output-root", str(output_root),
            "--cache-root", str(output_root / "cache"),
            "--temp-dir", str(output_root / "temp"),
        ],
        cwd=str(REPOSITORY_ROOT), capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"analyze failed ({completed.returncode}): "
            f"{(completed.stderr or completed.stdout)[-1500:]}"
        )
    pointer = json.loads(
        (output_root / "latest_run.json").read_text(encoding="utf-8")
    )
    return output_root / pointer["run_directory"]


def strip_complexity(run_directory: Path) -> Path:
    """Turn a copy of a 1.9 run into a pre-complexity one.

    Every trace goes: the repository block, the manifest field, the run-level
    state and the callable artifact itself. What is left is what a historical
    run actually looks like — no complexity key anywhere — rather than a 1.9 run
    with nulls in it, which is a different case and is tested separately.
    """
    analysis = run_directory / "analysis.json"
    document = json.loads(analysis.read_text(encoding="utf-8"))
    # `analysis.json` is a LIST of repository documents, and the complexity
    # block lives under `metrics.complexity` beside the other measurement
    # output — not at the top level.
    for repository in document:
        repository.pop("complexity_contract_version", None)
        metrics = repository.get("metrics")
        if isinstance(metrics, dict):
            metrics.pop("complexity", None)
    analysis.write_text(json.dumps(document, indent=2), encoding="utf-8", newline="\n")

    for name in ("run_manifest.json", "run_status.json"):
        path = run_directory / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("complexity_contract_version", None)
        payload.pop("complexity_measurement_state", None)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")

    for candidate in ("callables.csv",):
        target = run_directory / candidate
        if target.is_file():
            target.unlink()
    partitioned = run_directory / "callables"
    if partitioned.is_dir():
        shutil.rmtree(partitioned)
    return run_directory


class _RunFixture(unittest.TestCase):
    """One real 1.9 run over the complexity corpus, shared by the whole class."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        root = Path(cls._temporary.name)
        cls.run_directory = analyze(CORPUS, root / "native")
        cls.view = open_run(cls.run_directory)
        cls.results = [dict(item) for item in cls.view.repositories]

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()


class NativeRunPresentationTests(_RunFixture):
    """A native Artifact 1.9 run, through every surface that shows complexity."""

    def test_the_run_carries_an_evaluable_complexity_measurement(self):
        for result in self.results:
            self.assertIn(
                complexity_view.state_of(result),
                complexity_view.EVALUABLE_STATES,
            )

    def test_only_the_approved_aggregates_are_exposed(self):
        """No derived ratio, no threshold, no composite score."""
        self.assertEqual(
            complexity_view.AGGREGATE_FIELDS,
            (
                "callable_count",
                "cyclomatic_complexity_total", "cyclomatic_complexity_mean",
                "cyclomatic_complexity_median", "cyclomatic_complexity_max",
                "nloc_median", "nloc_max",
                "max_nesting_depth_median", "max_nesting_depth_max",
                "formal_parameter_count_median", "formal_parameter_count_max",
            ),
        )
        for result in self.results:
            block = complexity_view.repository_block(result)
            self.assertEqual(
                set(block["aggregate"]), set(complexity_view.AGGREGATE_FIELDS)
            )

    def test_the_summary_reports_complexity_with_its_state(self):
        from modules.summary import render_summary

        text = render_summary(
            self.view.manifest, self.results,
            outcome="complete", integrity_status="completed",
        )
        self.assertIn("### Callable complexity", text)
        self.assertIn("Callables measured", text)
        # Markdown metacharacters are escaped, dots included.
        self.assertIn(r"Complexity Contract: 2\.0\.0", text)
        # The three caveats travel with the numbers.
        self.assertIn("lower** median", text)
        self.assertIn("not a quality verdict", text)
        self.assertIn("measured nowhere", text)

    def test_the_report_shows_aggregates_provenance_and_rankings(self):
        from modules.cli.report_command import render_report

        html = render_report(self.view)
        self.assertIn("Callable complexity", html)
        self.assertIn("Complexity provenance", html)
        self.assertIn("Highest-value callables", html)
        for _field, label in complexity_view.RANKING_FIELDS:
            self.assertIn(label, html)
        # Descriptive only. Judgment vocabulary appears in this document ONLY
        # inside sentences that say there is none; a plain substring scan cannot
        # tell a prohibition from a violation, so every declared prohibition is
        # stripped and the rest of the document must contain none of it.
        #
        # The prohibitions are collected from the presentation modules rather
        # than restated here, so a new descriptive surface cannot quietly widen
        # the exemption: to be exempt, a sentence must be declared as a denial
        # in the module that renders it.
        from modules import complexity_distribution, source_composition

        body = html.lower()
        for caveat in (
            complexity_view.DESCRIPTIVE_ONLY,
            *source_composition.PROHIBITION_STATEMENTS,
            *complexity_distribution.PROHIBITION_STATEMENTS,
        ):
            lowered = caveat.lower()
            body = body.replace(html_escape(lowered), "").replace(lowered, "")
        for forbidden in ("critical", "severity", "grade", "score", "threshold"):
            self.assertNotIn(forbidden, body)

    def test_explain_reports_the_state_and_the_contract_version(self):
        from modules.cli.explain_command import build_explanation
        from modules.diagnostics import project_run

        payload = build_explanation(project_run(self.view))
        entries = [
            entry for group in payload["groups"] for entry in group["entries"]
        ]
        for entry in entries:
            self.assertIn(entry["complexity_state"], complexity_view.STATE_MEANINGS)
        self.assertIn(
            "complexity-unavailable", [group["group"] for group in payload["groups"]]
        )

    def test_the_explain_document_validates_against_its_registered_schema(self):
        from modules.cli.explain_command import build_explanation
        from modules.diagnostics import project_run
        from validation.artifact_io.schema_store import validate_document

        payload = build_explanation(project_run(self.view))
        self.assertEqual(
            validate_document("explain_output", payload, "explain.json"), []
        )


class MeasuredZeroIsNotAbsenceTests(_RunFixture):
    """A verified zero and an unavailable value must never render alike."""

    def test_a_measured_zero_renders_as_zero(self):
        rendered = complexity_view.render_value(0, state=complexity_view.STATE_COMPLETE)
        self.assertEqual(rendered, "0")

    def test_an_unavailable_value_never_renders_as_zero(self):
        for state in (
            complexity_view.STATE_FAILED,
            complexity_view.STATE_NOT_APPLICABLE,
            complexity_view.STATE_ABSENT,
        ):
            with self.subTest(state=state):
                self.assertEqual(
                    complexity_view.render_value(0, state=state),
                    complexity_view.UNAVAILABLE,
                )
                self.assertEqual(
                    complexity_view.render_value(None, state=state),
                    complexity_view.UNAVAILABLE,
                )

    def test_a_null_under_an_evaluable_state_is_still_unavailable(self):
        self.assertEqual(
            complexity_view.render_value(None, state=complexity_view.STATE_COMPLETE),
            complexity_view.UNAVAILABLE,
        )

    def test_a_file_with_no_callables_is_a_verified_zero(self):
        """`complete` with `callable_count == 0` is a measurement, not an absence.

        Measured on a real run over a real file that genuinely declares no
        callable, because the whole point is that the producer writes `0` there
        rather than leaving the cell empty.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "constants.py").write_text(
                "ALPHA = 1\nBETA = 2\n", encoding="utf-8", newline="\n"
            )
            run = analyze(source, root / "out")
            view = open_run(run)
            rows = [dict(row) for row in view.stream_contributions()]
            results = [dict(item) for item in view.repositories]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("structural_complexity_status"), "complete")
        # `0`, not an empty cell: the file was measured and holds no callable.
        # The reader coerces the cell, so both the typed and the rendered form
        # are checked — an empty cell would decode as None, not as 0.
        self.assertEqual(row.get("callable_count"), 0)
        self.assertIsNotNone(row.get("callable_count"))

        presented = complexity_view.presentation(results[0])
        self.assertEqual(presented["state"], complexity_view.STATE_COMPLETE)
        rendered = {item["field"]: item["rendered"] for item in presented["aggregate"]}
        self.assertEqual(rendered["callable_count"], "0")
        # And the aggregates over an empty population stay unavailable: there
        # is no median of nothing, and reporting 0 would be a false claim.
        self.assertEqual(
            rendered["cyclomatic_complexity_max"], complexity_view.UNAVAILABLE
        )

    def test_the_three_states_have_distinct_meanings(self):
        meanings = {
            complexity_view.STATE_MEANINGS[state]
            for state in (
                complexity_view.STATE_COMPLETE,
                complexity_view.STATE_FAILED,
                complexity_view.STATE_ABSENT,
            )
        }
        self.assertEqual(len(meanings), 3)


class HistoricalRunWithoutComplexityTests(_RunFixture):
    """A run that predates Complexity Contract 1.0.0 reads as ABSENT."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        copy = Path(self._temporary.name) / "historical"
        shutil.copytree(self.run_directory, copy)
        self.historical = strip_complexity(copy)
        self.historical_results = [
            dict(item)
            for item in json.loads(
                (self.historical / "analysis.json").read_text(encoding="utf-8")
            )
        ]

    def test_the_state_is_absent_not_failed_and_not_zero(self):
        for result in self.historical_results:
            self.assertEqual(
                complexity_view.state_of(result), complexity_view.STATE_ABSENT
            )
            self.assertIsNone(complexity_view.repository_block(result))

    def test_every_aggregate_renders_unavailable(self):
        for result in self.historical_results:
            presented = complexity_view.presentation(result)
            self.assertFalse(presented["evaluable"])
            for row in presented["aggregate"]:
                self.assertEqual(row["rendered"], complexity_view.UNAVAILABLE)

    def test_the_summary_says_absent_rather_than_reporting_numbers(self):
        from modules.summary import render_summary

        manifest = json.loads(
            (self.historical / "run_manifest.json").read_text(encoding="utf-8")
        )
        text = render_summary(
            manifest, self.historical_results,
            outcome="complete", integrity_status="completed",
        )
        self.assertIn("### Callable complexity", text)
        self.assertIn("absent", text)
        self.assertIn("never rendered as zero", text)

    def test_explain_groups_absent_nowhere(self):
        """A historical run is not an attention item; it simply has no complexity."""
        from modules.cli.explain_command import build_explanation
        from modules.diagnostics import project_run

        payload = build_explanation(project_run(open_run(self.historical)))
        complexity_group = next(
            group for group in payload["groups"]
            if group["group"] == "complexity-unavailable"
        )
        self.assertEqual(complexity_group["entries"], [])


class DirectDiffComplexityTests(_RunFixture):
    """Complexity in `archlens diff`, and the compatibility rule that governs it.

    The rule, stated once: **absence of complexity is never a global blocker.**
    Whatever complexity turns out to be, the Metric Contract 3.0.0 deltas must
    still be produced whenever the two sides are otherwise comparable.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        copy = Path(self._temporary.name) / "historical"
        shutil.copytree(self.run_directory, copy)
        self.historical = strip_complexity(copy)

    def _diff(self, left_directory: Path, right_directory: Path) -> dict:
        from modules import revision_diff
        from modules.cli import diff_command

        left_spec = revision_diff.parse_side(
            f"run:{left_directory}", "from", source=None
        )
        right_spec = revision_diff.parse_side(
            f"run:{right_directory}", "to", source=None
        )
        left, right = diff_command.resolve_sides(
            left_spec, right_spec, None,
            subject_key=None, expected_language=None, architecture_type="unknown",
        )
        return revision_diff.build_diff(left, right)

    def test_the_contract_dimension_is_reported_and_never_blocking(self):
        payload = self._diff(self.run_directory, self.run_directory)
        dimension = next(
            item for item in payload["comparability"]["dimensions"]
            if item["dimension"] == "complexity_contract_version"
        )
        self.assertFalse(dimension["blocking"])

    def test_same_contract_on_both_sides_makes_complexity_evaluable(self):
        payload = self._diff(self.run_directory, self.run_directory)
        verdict = payload["complexity_comparability"]
        self.assertTrue(verdict["evaluable"])
        self.assertEqual(verdict["complexity_contract_version_from"], "2.0.0")
        self.assertEqual(verdict["complexity_contract_version_to"], "2.0.0")
        # Identical sides: nothing to report, and that is a real zero-difference
        # rather than an unavailable comparison.
        self.assertEqual(payload["complexity_aggregate_deltas"], [])
        self.assertEqual(payload["per_language_complexity_deltas"], [])
        self.assertTrue(payload["file_complexity_evidence"]["evaluable"])

    def test_a_side_without_complexity_does_not_block_the_diff(self):
        payload = self._diff(self.historical, self.run_directory)
        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["comparability"]["refusal_reasons"], [])

        verdict = payload["complexity_comparability"]
        self.assertFalse(verdict["evaluable"])
        self.assertFalse(verdict["blocks_diff"])
        self.assertIn("absent", verdict["not_evaluable_reason"])
        self.assertEqual(verdict["measurement_state_from"], "absent")
        self.assertEqual(verdict["measurement_state_to"], "complete")

    def test_the_benchmark_metric_comparison_still_proceeds(self):
        """The compatibility requirement, checked on the artifact itself."""
        payload = self._diff(self.historical, self.run_directory)
        # The four benchmark dimensions are evaluable: the two sides are the
        # same measurement, so they agree and no delta is emitted -- which is a
        # completed comparison, not a refused one.
        self.assertEqual(payload["comparability"]["result"], "comparison_completed")
        self.assertTrue(payload["file_evidence"]["evaluable"])
        for entry in payload["aggregate_metric_deltas"]:
            self.assertNotEqual(
                entry["evidence_level"], "not_evaluable_missing_evidence",
                "a missing complexity block must not degrade a benchmark metric",
            )

    def test_complexity_deltas_report_unavailable_rather_than_zero(self):
        payload = self._diff(self.historical, self.run_directory)
        entries = (
            payload["complexity_aggregate_deltas"]
            + payload["per_language_complexity_deltas"]
        )
        for entry in entries:
            self.assertIsNone(entry["delta"])
            self.assertEqual(entry["evidence_level"], "not_evaluable_missing_evidence")
            self.assertIn("absent", entry["not_evaluable_reason"])
        file_evidence = payload["file_complexity_evidence"]
        self.assertFalse(file_evidence["evaluable"])
        self.assertEqual(file_evidence["deltas"], [])

    def test_no_callable_level_matching_happens_anywhere(self):
        """Direct Diff v1 pairs files, never callables."""
        payload = self._diff(self.run_directory, self.run_directory)
        serialized = json.dumps(payload)
        self.assertNotIn("callable_row_id", serialized)
        self.assertNotIn("qualified_name", serialized)

    def test_the_document_validates_against_its_registered_schema(self):
        from validation.artifact_io.schema_store import validate_document

        for left, right in (
            (self.run_directory, self.run_directory),
            (self.historical, self.run_directory),
        ):
            payload = self._diff(left, right)
            self.assertEqual(
                validate_document("revision_diff_output", payload, "diff.json"), []
            )

    def test_metric_contract_is_untouched_by_complexity(self):
        from modules import revision_diff

        self.assertEqual(
            revision_diff.AGGREGATE_DIMENSIONS,
            ("source_files", "lines_of_code", "classes_structs", "methods_functions"),
        )
        self.assertNotIn(
            "callable_count", revision_diff.AGGREGATE_DIMENSIONS
        )


class ComplexityViewShapeTests(unittest.TestCase):
    """The states a run can be in, without needing a run in each of them."""

    def _result(self, **complexity):
        return {"repository_url": "https://x/y", "complexity": dict(complexity)}

    def test_a_failed_measurement_is_evaluable_nowhere(self):
        result = self._result(
            complexity_contract_version="1.0.0", status="failed",
            unavailable_reason="all_files_failed_parse",
            aggregate={name: None for name in complexity_view.AGGREGATE_FIELDS},
            by_language={},
        )
        presented = complexity_view.presentation(result)
        self.assertEqual(presented["state"], complexity_view.STATE_FAILED)
        self.assertFalse(presented["evaluable"])
        self.assertEqual(presented["unavailable_reason"], "all_files_failed_parse")
        for row in presented["aggregate"]:
            self.assertEqual(row["rendered"], complexity_view.UNAVAILABLE)

    def test_a_partial_measurement_is_evaluable_and_says_so(self):
        result = self._result(
            complexity_contract_version="1.0.0", status="partial",
            unavailable_reason=None,
            aggregate={
                **{name: None for name in complexity_view.AGGREGATE_FIELDS},
                "callable_count": 3, "cyclomatic_complexity_max": 4,
            },
            by_language={},
        )
        presented = complexity_view.presentation(result)
        self.assertTrue(presented["evaluable"])
        self.assertIn("partial observations", presented["state_meaning"])
        rendered = {row["field"]: row["rendered"] for row in presented["aggregate"]}
        self.assertEqual(rendered["callable_count"], "3")
        self.assertEqual(rendered["nloc_median"], complexity_view.UNAVAILABLE)

    def test_a_measured_empty_repository_reports_a_real_zero(self):
        result = self._result(
            complexity_contract_version="1.0.0", status="complete",
            unavailable_reason=None,
            aggregate={
                **{name: None for name in complexity_view.AGGREGATE_FIELDS},
                "callable_count": 0,
            },
            by_language={},
        )
        rendered = {
            row["field"]: row["rendered"]
            for row in complexity_view.presentation(result)["aggregate"]
        }
        self.assertEqual(rendered["callable_count"], "0")
        # The other aggregates are genuinely unavailable: nothing was measured
        # to take a median of. Zero would be a different, and false, claim.
        self.assertEqual(rendered["cyclomatic_complexity_max"], complexity_view.UNAVAILABLE)

    def test_provenance_carries_all_four_required_facts(self):
        result = {
            "repository_url": "https://x/y",
            "analysis_scope_hash": "sha256:abc",
            "source_mode": "local_directory",
            "acquisition": {"analyzed_commit_sha": "0" * 40},
            "complexity": {
                "complexity_contract_version": "1.0.0", "status": "complete",
                "unavailable_reason": None, "aggregate": {}, "by_language": {},
            },
        }
        record = complexity_view.provenance({}, result)
        self.assertEqual(record["complexity_contract_version"], "1.0.0")
        self.assertEqual(record["measurement_state"], "complete")
        self.assertEqual(record["analyzed_scope"]["analysis_scope_hash"], "sha256:abc")
        self.assertEqual(
            set(record["aggregate_definitions"]),
            set(complexity_view.AGGREGATE_FIELDS),
        )

    def test_no_cross_language_equivalence_is_implied(self):
        self.assertIn(
            "NOT measurement-equivalent", complexity_view.CROSS_LANGUAGE_LIMITATION
        )
        self.assertIn(
            "formal_parameter_count", complexity_view.CROSS_LANGUAGE_LIMITATION
        )

    def test_an_unavailable_callable_value_is_not_ranked(self):
        rows = [
            {"relative_path": "a.py", "qualified_name": "a", "cyclomatic_complexity": 5},
            {"relative_path": "b.py", "qualified_name": "b", "cyclomatic_complexity": ""},
            {"relative_path": "c.py", "qualified_name": "c", "cyclomatic_complexity": None},
        ]
        ranked = complexity_view.rank_callables(rows, "cyclomatic_complexity")
        self.assertEqual([row["qualified_name"] for row in ranked], ["a"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
