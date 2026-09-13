"""Phase 7 tests: summary.md redesign and finalization ordering."""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.summary import (
    ARCHITECTURE_LABEL_DISCLAIMER,
    CROSS_LANGUAGE_LIMITATION,
    NON_AUTHORITATIVE_NOTICE,
    escape_markdown,
    extract_facts,
    measurement_outcome,
    render_summary,
)

SHA_A = "a" * 40


class MeasurementOutcomeTests(unittest.TestCase):
    def test_outcome_is_derived_from_repository_results_only(self):
        self.assertEqual(
            measurement_outcome([{"analysis_status": "complete"}]), "complete"
        )
        self.assertEqual(
            measurement_outcome([
                {"analysis_status": "complete"}, {"analysis_status": "partial"},
            ]),
            "partial",
        )
        self.assertEqual(measurement_outcome([{"analysis_status": "failed"}]), "failed")
        self.assertEqual(measurement_outcome([]), "not_evaluable")

    def test_optional_projection_failure_cannot_change_the_outcome(self):
        results = [{"analysis_status": "complete"}]
        manifest = {"output_failures": {"mandatory": [], "optional": ["boom"]}}
        text = render_summary(
            manifest, results,
            outcome=measurement_outcome(results),
            integrity_status="completed_with_errors",
        )
        facts = extract_facts(text)
        # Integrity degraded; measurement did not (plan section 4.5).
        self.assertEqual(facts["measurement_outcome"], "complete")
        self.assertEqual(facts["run_integrity_status"], "completed\\_with\\_errors")


class HostileTextTests(unittest.TestCase):
    def test_markdown_table_injection_is_escaped(self):
        rendered = escape_markdown("evil | injected | row")
        self.assertNotIn(" | ", rendered.replace("\\|", ""))
        self.assertIn("\\|", rendered)

    def test_bidi_and_control_characters_are_rendered_visibly(self):
        for hostile in ("\u202e", "\x1b", "\r", "\u200b"):
            with self.subTest(character=repr(hostile)):
                rendered = escape_markdown(f"a{hostile}b")
                self.assertNotIn(hostile, rendered)
                self.assertIn("\\u", rendered)

    def test_hostile_repository_url_cannot_break_the_appendix_table(self):
        results = [{
            "repository_url": "https://x/a|evil|\u202erow",
            "analysis_status": "complete",
            "metrics": {"aggregate": {}},
        }]
        text = render_summary(
            {}, results, outcome="complete", integrity_status="completed"
        )
        appendix = text.split("### Repository details")[1]
        header = next(
            line for line in appendix.splitlines() if line.startswith("| Subject")
        )
        expected_separators = header.count("|")
        for line in appendix.splitlines():
            if line.startswith("| https"):
                # An injected raw pipe would produce more separators than the
                # declared repository-details columns.
                self.assertEqual(
                    line.count("|") - line.count("\\|"), expected_separators
                )

    def test_null_and_zero_stay_distinct(self):
        results = [
            {"repository_url": "https://x/null", "metrics": {"aggregate": {"lines_of_code": None}}},
            {"repository_url": "https://x/zero", "metrics": {"aggregate": {"lines_of_code": 0}}},
        ]
        text = render_summary({}, results, outcome="partial", integrity_status="completed")
        rows = [line for line in text.splitlines() if line.startswith("| https")]
        self.assertTrue(any("| unavailable |" in row for row in rows))
        self.assertTrue(any("| 0 |" in row for row in rows))


class RequiredContentTests(unittest.TestCase):
    def _text(self):
        return render_summary(
            {"run_id": "abc", "program_version": "3.5.1"},
            [{"repository_url": "https://x/a", "analysis_status": "complete",
              "metrics": {"aggregate": {}}}],
            outcome="complete", integrity_status="completed",
        )

    def test_summary_declares_itself_non_authoritative(self):
        text = self._text()
        self.assertIn(NON_AUTHORITATIVE_NOTICE, text)
        self.assertIn("run_status.json", text)

    def test_every_required_section_is_present(self):
        text = self._text()
        for index in range(1, 7):
            with self.subTest(section=index):
                self.assertIn(f"## {index}. ", text)
        self.assertNotIn("## 7.", text)

    def test_caveats_and_disclaimers_are_present(self):
        text = self._text()
        self.assertIn(CROSS_LANGUAGE_LIMITATION, text)
        self.assertIn(ARCHITECTURE_LABEL_DISCLAIMER, text)

    def test_rendering_is_deterministic(self):
        self.assertEqual(self._text(), self._text())

    def test_per_metric_completeness_shows_denominators(self):
        text = render_summary(
            {},
            [
                {"repository_url": "https://x/a", "analysis_status": "complete",
                 "metrics": {"aggregate": {"loc_status": "complete"}}},
                {"repository_url": "https://x/b", "analysis_status": "partial",
                 "metrics": {"aggregate": {"loc_status": "partial"}}},
            ],
            outcome="partial", integrity_status="completed_with_errors",
        )
        self.assertIn("complete=1/2", text)
        self.assertIn("partial=1/2", text)


class EndToEndFinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )
        self.input = self.root / "repositories.csv"
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{SHA_A},true,one\n",
            encoding="utf-8",
        )

    @contextmanager
    def _acquire(self, spec_, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec_.url, repository_owner=spec_.owner,
                repository_name=spec_.repository_name,
                requested_commit_sha=spec_.commit_sha, analyzed_commit_sha=SHA_A,
                resolved_ref="refs/heads/main", default_branch="main",
                acquisition_mode="offline", cache_status="reused",
                remote_checked=False, fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified", fetch_method="offline_cache",
            ),
        )

    def _run(self):
        config = AnalysisConfig.from_env(
            output_root=self.root / "output", cache_root=self.root / "cache",
            temporary_directory=self.root / "temp", workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])

    def test_run_status_is_authoritative_and_agrees_with_the_manifest(self):
        run = self._run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], manifest["run_integrity_status"])
        self.assertIn("measurement_outcome", status)

    def test_measurement_outcome_and_integrity_status_are_separate_fields(self):
        run = self._run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("measurement_outcome", manifest)
        self.assertIn("run_integrity_status", manifest)

    def test_end_timestamp_is_assigned_exactly_once(self):
        run = self._run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        # The runner sets end_timestamp; finalization must mirror it rather than
        # overwrite it with a second, later clock reading.
        self.assertEqual(
            manifest["measurement_finished_at"], manifest["end_timestamp"]
        )

    def test_finalization_time_is_recorded_separately(self):
        run = self._run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(manifest.get("finalization_finished_at"))
        self.assertNotEqual(
            manifest["finalization_finished_at"], manifest["measurement_finished_at"]
        )

    def test_latest_run_pointer_records_the_finalization_time(self):
        run = self._run()
        pointer = json.loads(
            (run.parent.parent / "latest_run.json").read_text(encoding="utf-8")
        )
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["completed_at"], manifest["finalization_finished_at"])

    def test_summary_contains_no_absolute_host_path(self):
        run = self._run()
        text = (run / "summary.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("D:\\", text)
        self.assertNotIn(str(run), text)

    def test_in_memory_and_readback_facts_agree(self):
        run = self._run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))

        rerendered = render_summary(
            manifest, analysis,
            outcome=status["measurement_outcome"],
            integrity_status=manifest["run_integrity_status"],
        )
        on_disk = (run / "summary.md").read_text(encoding="utf-8")

        live = extract_facts(rerendered)
        stored = extract_facts(on_disk)
        for key in (
            "run_id", "run_integrity_status", "measurement_outcome",
            "input_row_count", "processed_repository_count",
            "metric_contract_version", "artifact_schema_version",
        ):
            with self.subTest(fact=key):
                self.assertEqual(live[key], stored[key])

    def test_summary_does_not_affect_the_semantic_payload(self):
        from validation.scripts.validate_outputs import semantic_payload

        run = self._run()
        before = semantic_payload(run)
        summary = run / "summary.md"
        summary.write_text(
            summary.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8"
        )
        after = semantic_payload(run)
        self.assertEqual(
            json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True),
            "summary.md must be excluded from the semantic payload",
        )

    def test_run_validates_after_the_finalization_restructure(self):
        from validation.scripts.validate_outputs import validate_run

        report = validate_run(self._run())
        self.assertTrue(report["passed"], report.get("failures"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
