import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pipeline
from modules import core_metrics
from modules.acquisition import AcquiredRepository, AcquisitionError, AcquisitionRecord, _run_git
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory
from modules.repository_input import RepositorySpec, load_repositories_csv
from validation.scripts.validate_outputs import validate_run


class GitModeMapRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "app.js").write_text("class App {}\n", encoding="utf-8")
        self.config = AnalysisConfig.from_env(
            workspace=self.root,
            git_timeout_seconds=47,
        )

    def test_successful_git_mode_map_is_explicit_and_uses_configured_timeout(self):
        completed = subprocess.CompletedProcess(
            ["git"], 0, stdout=b"100644 aaaaaaaa 0\tapp.js\0", stderr=b""
        )
        with patch("modules.inventory.subprocess.run", return_value=completed) as run:
            inventory = RepositoryInventory(self.root, self.config)
        self.assertEqual(inventory.git_mode_map_status, "available")
        self.assertTrue(inventory.git_mode_map_available)
        self.assertEqual(inventory.git_mode_entry_count, 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 47)
        self.assertEqual(inventory.git_mode_command_count, 1)

    def test_git_mode_timeout_is_observable_and_downgrades_metrics(self):
        with patch(
            "modules.inventory.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git", "ls-files"], 47),
        ):
            inventory = RepositoryInventory(self.root, self.config)
        metrics = compute_repository_metrics(inventory)
        self.assertEqual(inventory.git_mode_map_status, "timed_out")
        self.assertFalse(inventory.git_mode_map_available)
        self.assertIn("47", inventory.git_mode_map_error)
        self.assertEqual(inventory.inventory_status, "partial")
        self.assertEqual(metrics["aggregate"]["metric_status"], "partial")

    def test_git_mode_nonzero_exit_is_observable_and_downgrades_metrics(self):
        failure = subprocess.CalledProcessError(
            128, ["git", "ls-files"], stderr=b"fatal: index corrupt"
        )
        with patch("modules.inventory.subprocess.run", side_effect=failure):
            inventory = RepositoryInventory(self.root, self.config)
        self.assertEqual(inventory.git_mode_map_status, "failed")
        self.assertIn("index corrupt", inventory.git_mode_map_error)
        self.assertEqual(inventory.inventory_status, "partial")

    def test_non_git_directory_has_distinct_unavailable_state(self):
        """The state stays distinct; only its representation was normalized.

        Artifact 1.7 splits the compound `unavailable_not_git` into an orthogonal
        `status` + `reason` pair, so "unavailable" no longer smuggles the cause
        into the status vocabulary. The property under test is unchanged: a
        non-Git directory is distinguishable from a timeout and from a failure,
        and it does not degrade the inventory.
        """
        inventory = RepositoryInventory(self.root, self.config)
        self.assertEqual(inventory.git_mode_map_status, "unavailable")
        self.assertEqual(inventory.git_mode_map_reason, "not_git_repository")
        self.assertFalse(inventory.git_mode_map_available)
        self.assertEqual(inventory.inventory_status, "complete")


class GitCheckoutDeterminismRegressionTests(unittest.TestCase):
    def test_every_shared_git_invocation_pins_checkout_configuration(self):
        config = AnalysisConfig.from_env(git_timeout_seconds=5, git_retries=1)
        completed = subprocess.CompletedProcess(["git"], 0, stdout="", stderr="")
        with patch("modules.acquisition.subprocess.run", return_value=completed) as run:
            _run_git(["status", "--short"], config)
        command = run.call_args.args[0]
        for setting in (
            "core.longpaths=true",
            "core.autocrlf=false",
            "core.eol=lf",
            "core.safecrlf=false",
        ):
            self.assertIn(setting, command)

    def test_inherited_autocrlf_does_not_change_checkout_bytes_or_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "ArchLens test"],
                check=True,
            )
            (source / ".gitattributes").write_text(
                "forced.py text eol=crlf\n", encoding="utf-8"
            )
            (source / "app.py").write_bytes(b"class App:\n    pass\n")
            (source / "forced.py").write_bytes(b"value = 1\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-q", "-m", "fixture"],
                check=True,
            )
            outcomes = []
            for inherited in ("true", "false"):
                global_config = root / f"global-{inherited}.gitconfig"
                global_config.write_text(
                    f"[core]\n\tautocrlf = {inherited}\n", encoding="utf-8"
                )
                destination = root / f"checkout-{inherited}"
                config = AnalysisConfig.from_env(
                    workspace=root / f"workspace-{inherited}", git_retries=1
                )
                with patch.dict(
                    os.environ,
                    {"GIT_CONFIG_GLOBAL": str(global_config)},
                ):
                    _run_git(["clone", "-q", str(source), str(destination)], config)
                inventory = RepositoryInventory(destination, config)
                metrics = compute_repository_metrics(inventory)
                outcomes.append(
                    (
                        (destination / "app.py").read_bytes(),
                        (destination / "forced.py").read_bytes(),
                        inventory.get("app.py").content_hash,
                        metrics["aggregate"]["lines_of_code"],
                        metrics["aggregate"]["classes_structs"],
                    )
                )
            self.assertEqual(outcomes[0], outcomes[1])
            self.assertNotIn(b"\r\n", outcomes[0][0])
            self.assertIn(b"\r\n", outcomes[0][1])


class SourceEncodingRegressionTests(unittest.TestCase):
    def _metrics(self, payload: bytes, filename: str = "App.java"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / filename).write_bytes(payload)
        inventory = RepositoryInventory(root)
        return compute_repository_metrics(inventory), inventory.get(filename)

    def test_utf8_utf16le_and_utf16be_have_equivalent_logical_java_metrics(self):
        text = "class Café { void saluer() {} }\n"
        utf8, _ = self._metrics(text.encode("utf-8"))
        little, little_record = self._metrics(b"\xff\xfe" + text.encode("utf-16-le"))
        big, big_record = self._metrics(b"\xfe\xff" + text.encode("utf-16-be"))
        keys = ("lines_of_code", "classes_structs", "methods_functions", "metric_status")
        expected = {key: utf8["aggregate"][key] for key in keys}
        self.assertEqual({key: little["aggregate"][key] for key in keys}, expected)
        self.assertEqual({key: big["aggregate"][key] for key in keys}, expected)
        self.assertEqual(little_record.original_encoding, "utf-16-le")
        self.assertEqual(big_record.original_encoding, "utf-16-be")
        self.assertEqual(little_record.parser_encoding, "utf-8")
        self.assertFalse(little_record.parser_offsets_map_directly_to_original_bytes)
        self.assertNotEqual(little_record.original_byte_length, little_record.parser_byte_length)

    def test_truncated_utf16_fails_loc_with_source_encoding_failure(self):
        metrics, record = self._metrics(b"\xff\xfeA")
        aggregate = metrics["aggregate"]
        self.assertEqual(aggregate["loc_status"], "failed")
        self.assertIsNone(aggregate["lines_of_code"])
        self.assertEqual(record.error_category, "source_encoding_failure")

    def test_nul_containing_non_utf16_source_is_not_complete_loc(self):
        metrics, record = self._metrics(b"class App {}\n\x00")
        self.assertNotEqual(metrics["aggregate"]["loc_status"], "complete")
        self.assertEqual(record.error_category, "source_encoding_failure")

    def test_utf16_parser_diagnostics_do_not_fabricate_original_offsets(self):
        text = "class {\n"
        metrics, record = self._metrics(b"\xff\xfe" + text.encode("utf-16-le"))
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertIsNone(diagnostic["first_error_start_byte"])
        self.assertIsNotNone(diagnostic["first_error_parser_start_byte"])
        self.assertFalse(diagnostic["parser_offsets_map_directly_to_original_bytes"])
        self.assertFalse(record.original_byte_offsets_available)


class EnvironmentAndArtifactRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config, mode, progress
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
                checkout_timestamp="2026-08-04T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    def _run_oversized(self):
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
            max_source_file_size_bytes=5,
        )
        spec = RepositorySpec(
            "https://github.com/acme/example", "monolith", "Python", "a" * 40
        )
        with patch("modules.benchmark_runner.acquire_repository", self._acquire):
            return run_benchmark(
                repository_specs=[spec],
                config=config,
                acquisition_mode="offline",
                command_line_arguments=["test"],
            )

    def test_python_313_is_the_only_supported_benchmark_minor(self):
        # `doctor` now shares the preflight capability model, so the runtime
        # check lives in `modules.preflight` and reports
        # capability/state rather than name/healthy.
        from modules import preflight

        with patch.object(preflight.sys, "version_info", (3, 12, 9)):
            report = pipeline.doctor_report(AnalysisConfig.from_env(workspace=self.root))
        python_check = next(
            item for item in report["checks"] if item["capability"] == "python_runtime"
        )
        self.assertEqual(python_check["state"], "unavailable")
        self.assertEqual(python_check["reason"], "unsupported_runtime")
        self.assertTrue(python_check["blocking"])
        self.assertFalse(report["healthy"])

    def test_pep695_fixture_is_accepted_by_benchmark_interpreter(self):
        self.repo = self.root / "pep695"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "type Identifier = int\nclass Box[T]:\n    def get(self) -> T: ...\n",
            encoding="utf-8",
        )
        metrics = compute_repository_metrics(RepositoryInventory(self.repo))
        self.assertEqual(metrics["by_language"]["python"]["metric_status"], "complete")

    def test_language_csv_emits_oversized_column_and_accounting(self):
        summary = self._run_oversized()
        run = Path(summary["run_directory"])
        with (run / "language_metrics.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fields = handle.seek(0) or next(csv.reader(handle))
        self.assertIn("source_files_oversized", fields)
        python_row = next(row for row in rows if row["language"] == "python")
        self.assertEqual(python_row["source_files_oversized"], "1")
        self.assertEqual(
            int(python_row["source_files"]),
            int(python_row["source_files_readable"])
            + int(python_row["source_files_failed_read"])
            + int(python_row["source_files_oversized"]),
        )

    def test_manifest_contains_strengthened_provenance(self):
        summary = self._run_oversized()
        manifest = json.loads(
            (Path(summary["run_directory"]) / "run_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        for field in (
            "profiler_git_commit_sha",
            "profiler_git_dirty",
            "exclusion_policy_sha256",
            "package_distribution_version",
            "python_version_exact",
            "effective_git_checkout_configuration",
            "benchmark_environment",
        ):
            self.assertIn(field, manifest)

    def test_validator_accepts_a_terminal_acquisition_failure(self):
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "failed-output",
            cache_root=self.root / "failed-cache",
            temporary_directory=self.root / "failed-worktrees",
        )
        spec = RepositorySpec(
            "https://github.com/acme/dirty", "monolith", "Python", "a" * 40
        )

        @contextmanager
        def fail_acquisition(*args, **kwargs):
            del args, kwargs
            raise AcquisitionError("checkout_not_clean", "simulated dirty checkout")
            yield  # pragma: no cover - keeps this a context manager

        with (
            patch("modules.benchmark_runner.acquire_repository", fail_acquisition),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                repository_specs=[spec],
                config=config,
                acquisition_mode="offline",
                command_line_arguments=["test"],
            )
        validation = validate_run(Path(summary["run_directory"]))
        self.assertTrue(validation["passed"], validation["failures"])
        self.assertEqual(len(validation["repository_durations"]), 1)

    def test_strict_profiler_mode_is_a_configuration_option(self):
        self.assertIn("require_clean_profiler", AnalysisConfig.__dataclass_fields__)

    def test_identical_csv_duplicates_are_counted(self):
        path = self.root / "duplicates.csv"
        row = "https://github.com/acme/example,monolith,Python," + "a" * 40 + ",true,note\n"
        path.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            + row
            + row,
            encoding="utf-8",
        )
        diagnostics = {}
        rows = load_repositories_csv(path, diagnostics=diagnostics)
        self.assertEqual(len(rows), 1)
        self.assertEqual(diagnostics["duplicate_rows_dropped"], 1)

    def test_root_version_option(self):
        completed = subprocess.run(
            [sys.executable, "-m", "pipeline", "--version"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "Metrolith 4.0.1")

    def test_analyze_command_exists_with_the_local_analysis_surface(self):
        """Superseded by B1: `analyze` is now an approved command.

        This started life as `test_analyze_command_does_not_exist`, guarding
        against a documented-but-unimplemented command being silently accepted.
        Artifact 1.7 / Local Repository Analysis implements it, so the guard is
        inverted rather than deleted: the point was always that the CLI surface
        is deliberate and asserted, and that still holds.
        """
        completed = subprocess.run(
            [sys.executable, "-m", "pipeline", "analyze", "--help"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for option in (
            "--revision", "--tracked-only", "--subject-key",
            "--expected-language", "--architecture-type",
        ):
            self.assertIn(option, completed.stdout)

    def test_python_quickstart_is_a_real_python_candidate(self):
        specs = pipeline._quickstart_specs("python")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].url, "https://github.com/allegro/ralph")
        self.assertEqual(specs[0].expected_language, "Python")
        self.assertRegex(specs[0].commit_sha or "", r"^[0-9a-f]{40}$")

    def test_javascript_compatibility_has_explicit_limits(self):
        self.assertGreater(core_metrics.JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT, 0)
        self.assertGreater(core_metrics.JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES, 0)

    def test_javascript_compatibility_iteration_limit_is_partial_and_classified(self):
        repo = self.root / "javascript-limit"
        repo.mkdir()
        (repo / "broken.js").write_text("function broken( {\n", encoding="utf-8")
        with (
            patch.object(core_metrics, "JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT", 1),
            patch.object(
                core_metrics,
                "_rewrite_json_import_assertions",
                side_effect=lambda source, root: source + b" ",
            ),
        ):
            metrics = compute_repository_metrics(RepositoryInventory(repo))
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(
            diagnostic["error_category"],
            "parser_compatibility_limit_exceeded",
        )
        self.assertEqual(metrics["aggregate"]["metric_status"], "partial")

    def test_javascript_compatibility_growth_limit_is_partial_and_classified(self):
        repo = self.root / "javascript-growth"
        repo.mkdir()
        (repo / "broken.js").write_text("function broken( {\n", encoding="utf-8")
        with (
            patch.object(core_metrics, "JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES", 1),
            patch.object(
                core_metrics,
                "_rewrite_json_import_assertions",
                side_effect=lambda source, root: source + b"  ",
            ),
        ):
            metrics = compute_repository_metrics(RepositoryInventory(repo))
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(
            diagnostic["error_category"],
            "parser_compatibility_limit_exceeded",
        )

    def test_strict_profiler_mode_blocks_before_acquisition(self):
        config = AnalysisConfig.from_env(
            workspace=self.root,
            require_clean_profiler=True,
        )
        spec = RepositorySpec(
            "https://github.com/acme/example", "monolith", "Python", "a" * 40
        )
        with (
            patch(
                "modules.benchmark_runner._profiler_git_provenance",
                return_value={
                    "profiler_git_commit_sha": None,
                    "profiler_git_dirty": True,
                    "profiler_git_tag": None,
                },
            ),
            patch("modules.benchmark_runner.acquire_repository") as acquire,
            self.assertRaisesRegex(RuntimeError, "before repository acquisition"),
        ):
            run_benchmark(
                repository_specs=[spec],
                config=config,
                acquisition_mode="offline",
            )
        acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
