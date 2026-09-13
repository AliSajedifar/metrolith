import csv
import io
import json
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionError, AcquisitionRecord
from modules.benchmark_runner import _call_with_heartbeat, run_benchmark
from modules.config import AnalysisConfig
from modules.run_artifacts import StructuredRunLogger


class ExecutionModeTests(unittest.TestCase):
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
            "https://github.com/acme/project,monolith,Python,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,true,fixture\n",
            encoding="utf-8",
        )

    def _config(self, name: str, workers: int = 1) -> AnalysisConfig:
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
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha="a" * 40,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-02T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
                cache_hit=True,
                cached_commit_available=True,
            ),
        )

    def _analysis(self, summary):
        run = Path(summary["run_directory"])
        return run, json.loads((run / "analysis.json").read_text(encoding="utf-8"))

    def test_metrics_mode_skips_every_auxiliary_without_affecting_core(self):
        blocked = {
            name: patch(
                f"modules.benchmark_runner.{name}",
                side_effect=AssertionError(f"{name} must not run"),
            )
            for name in (
                "fetch_metadata",
                "detect_db_schema",
                "extract_endpoints",
                "assess_deployability",
                "detect_framework_markers",
                "compute_coverage",
                "detect_message_brokers",
                "classify_application",
                "collect_inventory_audits",
            )
        }
        with patch("modules.benchmark_runner.acquire_repository", self._acquire):
            for context in blocked.values():
                context.start()
            try:
                summary = run_benchmark(
                    [self.input], self._config("metrics"), "offline",
                    execution_mode="metrics",
                )
            finally:
                for context in blocked.values():
                    context.stop()

        run, analysis = self._analysis(summary)
        result = analysis[0]
        self.assertEqual(result["execution_mode"], "metrics")
        self.assertEqual(result["core_metric_status"], "complete")
        self.assertEqual(result["analysis_status"], "complete")
        for name in (
            "db_analysis", "endpoint_analysis", "deployability",
            "framework_analysis", "coverage_analysis", "message_broker_analysis",
            "classification", "audits", "metadata",
        ):
            self.assertEqual(
                result["module_statuses"][name], "skipped_by_execution_mode"
            )
        self.assertEqual(result["inventory_summary"]["inventory_mode"], "metrics_lightweight")
        self.assertTrue((run / "retry_failed_or_partial.csv").exists())
        with (run / "retry_failed_or_partial.csv").open(encoding="utf-8", newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])
        with (run / "repositories_frozen.csv").open(encoding="utf-8", newline="") as handle:
            frozen = list(csv.DictReader(handle))
        self.assertEqual(frozen[0]["commit_sha"], "a" * 40)

    def test_full_mode_times_modules_and_preserves_core_after_one_failure(self):
        checkpoint_seen = []

        def db_analysis(*args, **kwargs):
            del args, kwargs
            checkpoint = next((self.root / "full" / "runs").glob("*/repositories/*.json"))
            checkpoint_seen.append(json.loads(checkpoint.read_text(encoding="utf-8")))
            return {"db_type": None}

        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner.detect_db_schema", side_effect=db_analysis) as db,
            patch("modules.benchmark_runner.extract_endpoints", side_effect=RuntimeError("boom")) as endpoints,
            patch("modules.benchmark_runner.assess_deployability", return_value={}) as deploy,
            patch("modules.benchmark_runner.detect_framework_markers", return_value=[]) as framework,
            patch("modules.benchmark_runner.compute_coverage", return_value={}) as coverage,
            patch("modules.benchmark_runner.detect_message_brokers", return_value={}) as broker,
            patch("modules.benchmark_runner.classify_application", return_value="general") as classify,
            patch("modules.benchmark_runner.collect_inventory_audits", return_value={}) as audits,
        ):
            summary = run_benchmark(
                [self.input], self._config("full"), "offline", execution_mode="all"
            )

        _, analysis = self._analysis(summary)
        result = analysis[0]
        self.assertEqual(checkpoint_seen[0]["checkpoint_status"], "core_complete")
        self.assertEqual(checkpoint_seen[0]["core_metric_status"], "complete")
        self.assertEqual(result["core_metric_status"], "complete")
        self.assertEqual(result["module_statuses"]["endpoint_analysis"], "failed")
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(summary["status"], "completed_with_errors")
        for mocked in (db, endpoints, deploy, framework, coverage, broker, classify, audits):
            mocked.assert_called_once()
        for name in (
            "db_analysis", "endpoint_analysis", "deployability", "framework_analysis",
            "coverage_analysis", "message_broker_analysis", "classification", "audits",
        ):
            timing = result["timings"]["auxiliary"][name]
            self.assertIn(timing["status"], {"complete", "failed"})
            self.assertGreaterEqual(timing["duration_seconds"], 0)

    def test_heartbeat_and_terminal_timestamp_are_visible(self):
        config = self._config("heartbeat")
        config.heartbeat_interval_seconds = 1
        logger = StructuredRunLogger(self.root / "heartbeat.jsonl", "test")
        output = io.StringIO()
        with redirect_stdout(output):
            _call_with_heartbeat(
                config,
                logger,
                "auxiliary:endpoints",
                lambda: time.sleep(0.04),
                "https://github.com/acme/project",
                "project",
                warning_seconds=0.01,
            )
        rendered = output.getvalue()
        self.assertRegex(rendered, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("[project] [auxiliary:endpoints] still running", rendered)

    def test_failed_later_repository_keeps_checkpoint_and_generates_retry_input(self):
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            "https://github.com/acme/project,monolith,Python,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,true,first\n"
            "https://github.com/acme/broken,monolith,Python,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,true,second\n",
            encoding="utf-8",
        )

        @contextmanager
        def acquire(spec, config, mode="latest", progress=None):
            if spec.repository_name == "broken":
                raise AcquisitionError("repository_not_found", "simulated missing repository")
            with self._acquire(spec, config, mode, progress) as acquired:
                yield acquired

        with patch("modules.benchmark_runner.acquire_repository", acquire):
            summary = run_benchmark(
                [self.input], self._config("interrupted"), "offline",
                execution_mode="metrics",
            )
        run, analysis = self._analysis(summary)
        by_name = {result["repository_name"]: result for result in analysis}
        self.assertEqual(by_name["project"]["analysis_status"], "complete")
        self.assertEqual(by_name["broken"]["analysis_status"], "failed")
        self.assertTrue((run / "repositories" / "acme__project.json").is_file())
        with (run / "retry_failed_or_partial.csv").open(encoding="utf-8", newline="") as handle:
            retry = list(csv.DictReader(handle))
        self.assertEqual([row["url"] for row in retry], ["https://github.com/acme/broken"])
        with (run / "repositories_frozen.csv").open(encoding="utf-8", newline="") as handle:
            frozen = list(csv.DictReader(handle))
        self.assertEqual([row["url"] for row in frozen], ["https://github.com/acme/project"])


if __name__ == "__main__":
    unittest.main()
