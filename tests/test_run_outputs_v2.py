import csv
import json
import os
import tempfile
import unittest

from modules.config import ARTIFACT_SCHEMA_VERSION
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.run_artifacts import atomic_write_text, render_fact_sheet
from validation.scripts.validate_outputs import validate_run


class ImmutableRunOutputTests(unittest.TestCase):
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
            f"https://github.com/acme/mono,monolith,Python,{'a' * 40},true,one\n"
            f"https://github.com/acme/micro,microservices,Python,{'b' * 40},true,two\n",
            encoding="utf-8",
        )

    def _config(self, name="output", workers=1):
        return AnalysisConfig.from_env(
            output_root=self.root / name,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=workers,
        )

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config, mode
        if progress:
            progress("creating detached worktree")
        sha = "a" * 40 if spec.repository_name == "mono" else "b" * 40
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha=sha,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    def _run(self, output="output", workers=1):
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            return run_benchmark([self.input], self._config(output, workers), "offline", command_line_arguments=["test"])

    def test_required_artifacts_are_created_and_consistent(self):
        summary = self._run()
        run = Path(summary["run_directory"])
        required = {
            "run_manifest.json", "analysis.json", "catalog.csv", "sheet_metrics.csv",
            "language_metrics.csv", "errors.csv", "run_status.json",
            "recoveries.csv", "retry_failed_or_partial.csv", "repositories_frozen.csv",
        }
        self.assertTrue(required <= {path.name for path in run.iterdir() if path.is_file()})
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        with (run / "sheet_metrics.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        by_url = {item["repository_url"]: item for item in analysis}
        for row in rows:
            aggregate = by_url[row["repository_url"]]["metrics"]["aggregate"]
            self.assertEqual(int(row["lines_of_code"]), aggregate["lines_of_code"])
            self.assertEqual(int(row["classes_structs"]), aggregate["classes_structs"])
            for field in (
                "inventory_status", "source_files_status", "loc_status",
                "classes_structs_status", "methods_functions_status",
                "source_files_readable", "source_files_loc_analyzed",
                "source_files_entity_parsed", "expected_language_mismatch",
            ):
                self.assertIn(field, row)
            self.assertEqual(row["metric_contract_version"], "3.0.0")
            self.assertEqual(row["exclusion_policy_version"], "1.5.0")
            self.assertEqual(row["inventory_schema_version"], "1.7.0")
            self.assertEqual(row["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)
            self.assertEqual(row["program_version"], "4.0.1")
        for result in analysis:
            self.assertEqual(result["execution_mode"], "metrics")
            self.assertIn("repository_start_timestamp", result)
            self.assertIn("repository_end_timestamp", result)
            self.assertGreaterEqual(result["repository_duration_seconds"], 0)
            self.assertIn("timings", result)
            self.assertEqual(result["metric_contract_version"], "3.0.0")
            self.assertEqual(result["exclusion_policy_version"], "1.5.0")
            self.assertEqual(result["inventory_schema_version"], "1.7.0")
            self.assertEqual(result["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)
            self.assertEqual(result["program_version"], "4.0.1")
            self.assertEqual(
                result["module_statuses"]["endpoint_analysis"],
                "skipped_by_execution_mode",
            )
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution_mode"], "metrics")
        run_status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(run_status["repository_count"], 2)
        self.assertEqual(run_status["processed_repository_count"], 2)
        self.assertEqual(run_status["success_count"], 2)
        self.assertEqual(run_status["partial_count"], 0)
        self.assertEqual(run_status["failure_count"], 0)
        self.assertEqual(summary["status"], "completed")
        validation = validate_run(run)
        self.assertTrue(validation["passed"], validation["failures"])

        with (run / "language_metrics.csv").open(encoding="utf-8", newline="") as handle:
            language_rows = list(csv.DictReader(handle))
        for row in language_rows:
            expected_sha = by_url[row["repository_url"]]["acquisition"]["analyzed_commit_sha"]
            self.assertEqual(row["analyzed_commit_sha"], expected_sha)
            self.assertEqual(row["execution_mode"], "metrics")
            self.assertEqual(row["metric_contract_version"], "3.0.0")
            self.assertEqual(row["exclusion_policy_version"], "1.5.0")
            self.assertEqual(row["inventory_schema_version"], "1.7.0")
            self.assertEqual(row["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)
            self.assertEqual(row["program_version"], "4.0.1")

    def test_cleanup_failure_is_reported_without_erasing_core_metrics(self):
        @contextmanager
        def cleanup_fails(spec, config, mode="latest", progress=None):
            with self._acquire(spec, config, mode, progress) as acquired:
                yield acquired
            acquired.record.cleanup_status = "failed"
            acquired.record.cleanup_errors.append("simulated cleanup failure")

        with patch("modules.benchmark_runner.acquire_repository", cleanup_fails):
            summary = run_benchmark(
                [self.input], self._config("cleanup_failure"), "offline"
            )
        analysis = json.loads(
            (Path(summary["run_directory"]) / "analysis.json").read_text(encoding="utf-8")
        )
        self.assertTrue(all(item["analysis_status"] == "partial" for item in analysis))
        self.assertTrue(
            all(item["metrics"]["aggregate"]["metric_status"] == "complete" for item in analysis)
        )
        self.assertTrue(
            all(
                any(error["error_type"] == "worktree_cleanup_failure" for error in item["errors"])
                for item in analysis
            )
        )

    def test_every_execution_creates_a_new_run_without_overwrite(self):
        first = self._run()
        second = self._run()
        self.assertNotEqual(first["run_directory"], second["run_directory"])
        self.assertTrue(Path(first["run_directory"]).exists())
        self.assertTrue(Path(second["run_directory"]).exists())

    def test_architecture_labels_share_one_pipeline_and_are_preserved(self):
        summary = self._run()
        analysis = json.loads(
            (Path(summary["run_directory"]) / "analysis.json").read_text(encoding="utf-8")
        )
        self.assertEqual({item["architecture_type"] for item in analysis}, {"monolith", "microservices"})
        self.assertEqual({item["metrics"]["aggregate"]["lines_of_code"] for item in analysis}, {3})
        self.assertTrue(all(item["architecture_type_source"] == "benchmark_input" for item in analysis))

    def test_worker_counts_produce_equivalent_deterministic_metrics(self):
        one = self._run("one", workers=1)
        many = self._run("many", workers=2)
        first = json.loads((Path(one["run_directory"]) / "analysis.json").read_text(encoding="utf-8"))
        second = json.loads((Path(many["run_directory"]) / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [(item["repository_url"], item["metrics"]) for item in first],
            [(item["repository_url"], item["metrics"]) for item in second],
        )

    def test_pipeline_constructs_one_inventory_per_repository(self):
        from modules import benchmark_runner

        original = benchmark_runner.RepositoryInventory
        calls = {"count": 0}

        def counted(*args, **kwargs):
            calls["count"] += 1
            return original(*args, **kwargs)

        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner.RepositoryInventory", side_effect=counted),
        ):
            run_benchmark([self.input], self._config("walks"), "offline")
        self.assertEqual(calls["count"], 2)

    def test_latest_pointer_updates_only_after_successful_finalization(self):
        summary = self._run()
        pointer = json.loads((self.root / "output" / "latest_run.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["run_id"], summary["run_id"])
        self.assertIn(pointer["status"], {"completed", "completed_with_errors"})

    def test_optional_fact_sheet_failure_marks_completed_with_errors(self):
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.run_artifacts.render_fact_sheet", side_effect=OSError("locked")),
        ):
            summary = run_benchmark([self.input], self._config("optional"), "offline")
        self.assertEqual(summary["status"], "completed_with_errors")
        run_status = json.loads(
            (Path(summary["run_directory"]) / "run_status.json").read_text(encoding="utf-8")
        )
        self.assertTrue(run_status["optional_output_failures"])

    def test_mandatory_output_failure_marks_failed_and_does_not_update_latest(self):
        from modules import run_artifacts

        original = run_artifacts.atomic_write_csv

        def fail_sheet(path, fields, rows):
            if Path(path).name == "sheet_metrics.csv":
                raise OSError("locked")
            return original(path, fields, rows)

        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.run_artifacts.atomic_write_csv", side_effect=fail_sheet),
        ):
            summary = run_benchmark([self.input], self._config("mandatory"), "offline")
        self.assertEqual(summary["status"], "failed")
        self.assertFalse((self.root / "mandatory" / "latest_run.json").exists())

    def test_atomic_replace_retries_windows_style_lock(self):
        target = self.root / "atomic.txt"
        original = os.replace
        calls = {"count": 0}

        def flaky(source, destination):
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError("locked")
            return original(source, destination)

        with patch("modules.run_artifacts.os.replace", side_effect=flaky):
            atomic_write_text(target, "complete")
        self.assertEqual(target.read_text(encoding="utf-8"), "complete")
        self.assertEqual(calls["count"], 3)

    def test_canonical_fact_sheet_renders_null_as_na_and_zero_as_zero(self):
        text = render_fact_sheet(
            {
                "repository_owner": "acme",
                "repository_name": "partial",
                "repository_url": "https://github.com/acme/partial",
                "architecture_type": "monolith",
                "metrics": {
                    "primary_language_name": "Python",
                    "expected_language_mismatch": False,
                    "aggregate": {
                        "lines_of_code": None,
                        "source_files": 1,
                        "classes_structs": 0,
                        "methods_functions": None,
                        "metric_status": "partial",
                        "inventory_status": "complete",
                        "source_files_status": "complete",
                        "loc_status": "failed",
                        "classes_structs_status": "complete",
                        "methods_functions_status": "failed",
                    },
                },
                "acquisition": {},
                "errors": [],
            }
        )
        self.assertIn("| Lines of Code | N/A | failed |", text)
        self.assertIn("| Classes / Structs | 0 | complete |", text)
        self.assertNotIn("| Lines of Code | None |", text)

    def test_fact_sheet_reports_policy_exclusion_categories(self):
        text = render_fact_sheet(
            {
                "repository_owner": "acme",
                "repository_name": "selection",
                "repository_url": "https://github.com/acme/selection",
                "architecture_type": "unknown",
                "metrics": {"aggregate": {}},
                "acquisition": {},
                "inventory_summary": {
                    "files_excluded_by_reason": {
                        "content_type_mismatch": 1,
                        "templated_source": 2,
                    }
                },
                "errors": [],
            }
        )
        self.assertIn("## Source-selection exclusions", text)
        self.assertIn("content_type_mismatch: 1", text)
        self.assertIn("templated_source: 2", text)

    def test_parser_diagnostics_are_consistent_across_json_csv_and_fact_sheet(self):
        (self.repo / "broken.js").write_bytes(b"const bad\x01name = 1;\n")
        summary = self._run("diagnostics")
        run = Path(summary["run_directory"])
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        with (run / "errors.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        validation = validate_run(run)
        self.assertTrue(validation["passed"], validation["failures"])
        by_url = {row["repository_url"]: row for row in rows}
        for result in analysis:
            diagnostic = result["metrics"]["parser_diagnostics"][0]
            row = by_url[result["repository_url"]]
            self.assertEqual(diagnostic["repository_url"], result["repository_url"])
            self.assertEqual(
                diagnostic["analyzed_commit_sha"],
                result["acquisition"]["analyzed_commit_sha"],
            )
            for field in (
                "file_path",
                "file_sha256",
                "detected_language",
                "extension",
                "parser_implementation",
                "selected_grammar",
                "grammar_package",
                "grammar_version",
                "error_category",
                "preview",
                "final_file_status",
            ):
                self.assertEqual(row[field], str(diagnostic[field]))
            self.assertEqual(row["first_error_start_line"], "1")
            self.assertEqual(row["first_error_start_column"], "6")
            self.assertEqual(
                row["repository_metric_status"],
                diagnostic["final_repository_metric_statuses"]["metric_status"],
            )
            self.assertEqual(
                row["repository_loc_status"],
                diagnostic["final_repository_metric_statuses"]["loc_status"],
            )
            fact = run / "fact_sheets" / f"{result['repository_owner']}__{result['repository_name']}.md"
            fact_text = fact.read_text(encoding="utf-8")
            self.assertIn("syntax_partial", fact_text)
            self.assertIn("broken.js", fact_text)

    def test_recovered_javascript_is_separate_from_unresolved_errors(self):
        (self.repo / "assertion.js").write_text(
            'import pkg from "./package.json" assert { type: "json" };\n'
            "export function create() { return pkg; }\n",
            encoding="utf-8",
        )
        summary = self._run("recoveries")
        run = Path(summary["run_directory"])
        with (run / "recoveries.csv").open(encoding="utf-8", newline="") as handle:
            recoveries = list(csv.DictReader(handle))
        with (run / "errors.csv").open(encoding="utf-8", newline="") as handle:
            errors = list(csv.DictReader(handle))
        with (run / "sheet_metrics.csv").open(encoding="utf-8", newline="") as handle:
            sheet_rows = list(csv.DictReader(handle))
        self.assertEqual(len(recoveries), 2)
        self.assertEqual(errors, [])
        # The run must actually finalize. This test previously read the CSV
        # without ever checking the run's own verdict, so a recoveries row that
        # failed the Artifact 1.7 contract -- `subject_key` is non-nullable, and
        # the recovery producer was not migrated -- still let the test pass while
        # finalization rejected the run and published `failed`.
        self.assertEqual(summary["status"], "completed")
        for recovery in recoveries:
            self.assertTrue(
                recovery["subject_key"],
                "recoveries.csv must carry the subject identity, not an empty cell",
            )
            self.assertEqual(recovery["file_path"], "assertion.js")
            self.assertEqual(recovery["fallback_attempted"], "True")
            self.assertEqual(recovery["fallback_error_count"], "0")
            self.assertEqual(recovery["final_file_status"], "complete")
            self.assertIn("javascript_import_assertion_compat", recovery["fallback_strategies"])
        for row in sheet_rows:
            self.assertIn("recovered: 1 import-assertion files", row["error_summary"])
            self.assertIn(
                "affected metrics: classes_structs, methods_functions",
                row["error_summary"],
            )
