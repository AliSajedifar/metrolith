import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from modules.config import AnalysisConfig
from modules.export_csv import export_catalog
from modules.fact_sheet import generate_fact_sheet


class PipelineInputTests(unittest.TestCase):
    def test_discovers_deduplicates_and_qualifies_name_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mono = root / "monolith_repos.txt"
            micro = root / "Java microservices_repos.txt"
            mono.write_text(
                "# comment\n"
                "https://github.com/a/shared\n"
                "https://github.com/a/shared.git\n",
                encoding="utf-8",
            )
            micro.write_text(
                "https://github.com/b/shared\n"
                "https://github.com/c/unique\n",
                encoding="utf-8",
            )
            with patch.object(pipeline, "INPUT_DIR", root):
                repos = pipeline.load_repositories()

        self.assertEqual(len(repos), 3)
        by_url = {repo["url"]: repo for repo in repos}
        self.assertEqual(by_url["https://github.com/a/shared"]["storage_name"], "a__shared")
        self.assertEqual(by_url["https://github.com/b/shared"]["storage_name"], "b__shared")
        self.assertEqual(by_url["https://github.com/c/unique"]["storage_name"], "unique")

    def test_malformed_url_reports_file_and_line(self):
        with tempfile.TemporaryDirectory() as directory:
            input_file = Path(directory) / "monolith_repos.txt"
            input_file.write_text(
                "https://github.com/example/valid\n"
                "https://example.com/not-github\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as context:
                pipeline.load_repositories([input_file])

            message = str(context.exception)
            self.assertIn("monolith_repos.txt:2", message)
            self.assertIn("Only github.com", message)

    def test_empty_selected_input_produces_empty_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            empty = input_dir / "monolith_repos.txt"
            empty.write_text("# intentionally empty\n", encoding="utf-8")

            with (
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "RAW_DIR", root / "raw"),
                patch.object(pipeline, "PROCESSED_DIR", root / "processed"),
                patch.object(pipeline, "OUTPUT_DIR", root / "output"),
            ):
                repos = pipeline.execute_pipeline("all", input_files=[empty])

            self.assertEqual(repos, [])
            self.assertEqual(
                (root / "processed" / "analysis.json").read_text(encoding="utf-8"),
                "[]",
            )
            with (root / "output" / "csv" / "apps_catalog.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])
            self.assertEqual(
                list((root / "output" / "fact_sheets").glob("*.md")), []
            )

    def test_raw_directory_environment_variable_is_honored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            external_raw = root / "external" / "raw"
            input_dir.mkdir()
            empty = input_dir / "monolith_repos.txt"
            empty.write_text("", encoding="utf-8")

            with (
                patch.dict(
                    os.environ,
                    {"ARCHLENS_RAW_DIR": str(external_raw)},
                    clear=False,
                ),
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "RAW_DIR", root / "default-raw"),
                patch.object(pipeline, "PROCESSED_DIR", root / "processed"),
                patch.object(pipeline, "OUTPUT_DIR", root / "output"),
            ):
                pipeline.execute_pipeline("metadata", input_files=[empty])

            self.assertTrue(external_raw.is_dir())
            self.assertFalse((root / "default-raw").exists())

    def test_all_workflow_writes_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            raw_dir = root / "data" / "raw"
            processed_dir = root / "data" / "processed"
            output_dir = root / "output"
            input_dir.mkdir(parents=True)
            repo_dir = raw_dir / "sample"
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "tests").mkdir()
            (repo_dir / "src" / "app.py").write_text(
                'from flask import Flask\napp = Flask(__name__)\n'
                '@app.get("/health")\ndef health():\n    return "ok"\n',
                encoding="utf-8",
            )
            (repo_dir / "tests" / "test_app.py").write_text(
                "def test_health():\n    pass\n", encoding="utf-8"
            )
            (input_dir / "monolith_repos.txt").write_text(
                "https://github.com/example/sample\n", encoding="utf-8"
            )

            metadata = {
                "name": "sample",
                "full_name": "example/sample",
                "language": "Python",
                "stars": 1,
                "forks": 0,
            }
            with (
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "RAW_DIR", raw_dir),
                patch.object(pipeline, "PROCESSED_DIR", processed_dir),
                patch.object(pipeline, "OUTPUT_DIR", output_dir),
                patch.object(pipeline, "fetch_metadata", return_value=metadata),
                patch.object(
                    pipeline,
                    "clone_repository",
                    return_value={
                        "fetch_status": "success",
                        "fetch_method": "existing_valid_cache",
                        "fetch_error_type": "none",
                        "fetch_error_message": "",
                        "path": repo_dir,
                    },
                ),
            ):
                repos = pipeline.execute_pipeline("all", fail_fast=True)

            self.assertEqual(repos[0]["status"], "ok")
            self.assertEqual(repos[0]["fetch_status"], "success")
            self.assertEqual(repos[0]["analysis_status"], "analyzed")
            self.assertEqual(repos[0]["static"]["source_files"], 1)
            self.assertEqual(len(repos[0]["endpoints"]), 1)
            self.assertTrue((processed_dir / "analysis.json").is_file())
            self.assertTrue((output_dir / "csv" / "apps_catalog.csv").is_file())
            self.assertTrue((output_dir / "fact_sheets" / "sample.md").is_file())
            with (output_dir / "csv" / "apps_catalog.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                row = next(csv.DictReader(handle))
            self.assertIn("class_detection_status", row)
            self.assertIn("js_ts_scanned_files", row)
            self.assertIn("go_metric_detection_status", row)
            self.assertIn("go_scanned_files", row)
            fact_sheet = (output_dir / "fact_sheets" / "sample.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("JavaScript / TypeScript Metric Audit", fact_sheet)

    def test_fact_sheet_names_do_not_collide(self):
        from modules.fact_sheet import generate_fact_sheets

        with tempfile.TemporaryDirectory() as directory:
            repos = [
                {
                    "url": "https://github.com/a/shared",
                    "storage_name": "a__shared",
                    "metadata": {"name": "shared"},
                },
                {
                    "url": "https://github.com/b/shared",
                    "storage_name": "b__shared",
                    "metadata": {"name": "shared"},
                },
            ]
            generate_fact_sheets(repos, directory)
            names = {path.name for path in Path(directory).glob("*.md")}
        self.assertEqual(names, {"a__shared.md", "b__shared.md"})

    def test_fact_sheet_export_removes_stale_generated_markdown(self):
        from modules.fact_sheet import generate_fact_sheets

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            stale = output_dir / "old-repository.md"
            stale.write_text("# stale\n", encoding="utf-8")
            repos = [
                {
                    "url": "https://github.com/example/current",
                    "storage_name": "current",
                    "metadata": {"name": "current"},
                    "analysis_status": "skipped",
                }
            ]

            generate_fact_sheets(repos, output_dir)

            self.assertFalse(stale.exists())
            self.assertTrue((output_dir / "current.md").is_file())

    def test_analysis_is_skipped_when_fetch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            (input_dir / "monolith_repos.txt").write_text(
                "https://github.com/example/broken\n", encoding="utf-8"
            )
            failed_fetch = {
                "fetch_status": "failed",
                "fetch_method": "none",
                "fetch_error_type": "incomplete_repository",
                "fetch_error_message": "tracked files are missing",
                "path": None,
            }
            with (
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "RAW_DIR", root / "raw"),
                patch.object(pipeline, "PROCESSED_DIR", root / "processed"),
                patch.object(pipeline, "OUTPUT_DIR", root / "output"),
                patch.object(
                    pipeline,
                    "fetch_metadata",
                    return_value={"name": "broken", "default_branch": "main"},
                ),
                patch.object(pipeline, "clone_repository", return_value=failed_fetch),
                patch.object(pipeline, "perform_static_analysis") as static_analysis,
                patch.object(pipeline, "extract_endpoints") as endpoints,
            ):
                repos = pipeline.execute_pipeline("all")

            static_analysis.assert_not_called()
            endpoints.assert_not_called()
            self.assertEqual(repos[0]["fetch_status"], "failed")
            self.assertEqual(repos[0]["analysis_status"], "skipped")
            self.assertNotIn("static", repos[0])
            self.assertNotIn("endpoints", repos[0])
            with (root / "output" / "csv" / "apps_catalog.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["fetch_status"], "failed")
            self.assertEqual(row["analysis_status"], "skipped")
            self.assertEqual(row["loc"], "")
            self.assertEqual(row["endpoint_count"], "")

    def test_fetching_directories_are_cleaned_and_never_analyzed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            raw_dir = root / "raw"
            final_repo = raw_dir / "r_final"
            stale = raw_dir / "r_stale.fetching"
            input_dir.mkdir()
            final_repo.mkdir(parents=True)
            stale.mkdir()
            (final_repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (stale / "wrong.py").write_text("raise RuntimeError\n", encoding="utf-8")
            (input_dir / "monolith_repos.txt").write_text(
                "https://github.com/example/project\n", encoding="utf-8"
            )
            fetch = {
                "fetch_status": "success",
                "fetch_method": "existing_valid_cache",
                "fetch_error_type": "none",
                "fetch_error_message": "",
                "path": final_repo,
            }

            with (
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "RAW_DIR", raw_dir),
                patch.object(pipeline, "PROCESSED_DIR", root / "processed"),
                patch.object(pipeline, "OUTPUT_DIR", root / "output"),
                patch.object(
                    pipeline,
                    "fetch_metadata",
                    return_value={"name": "project", "language": "Python"},
                ),
                patch.object(pipeline, "clone_repository", return_value=fetch),
                patch.object(
                    pipeline,
                    "perform_static_analysis",
                    return_value={"source_files": 1},
                ) as static_analysis,
                patch.object(pipeline, "extract_endpoints", return_value=[]),
                patch.object(pipeline, "detect_framework_markers", return_value=[]),
                patch.object(pipeline, "assess_deployability", return_value={}),
                patch.object(pipeline, "detect_db_schema", return_value={}),
                patch.object(pipeline, "compute_coverage", return_value={}),
                patch.object(pipeline, "classify_application", return_value={}),
            ):
                pipeline.execute_pipeline("all")

            self.assertFalse(stale.exists())
            static_analysis.assert_called_once()
            self.assertEqual(static_analysis.call_args.args, (final_repo,))
            self.assertIsInstance(
                static_analysis.call_args.kwargs["inventory"],
                pipeline.RepositoryInventory,
            )

    def test_short_cache_path_is_stable(self):
        repo = {"url": "https://github.com/example/a-very-long-repository-name"}
        first = pipeline._local_repository_path(repo, Path("C:/arch_raw"))
        second = pipeline._local_repository_path(repo, Path("C:/arch_raw"))
        self.assertEqual(first, second)
        self.assertRegex(first.name, r"^r_[0-9a-f]{12}$")

    def test_input_menu_metadata_detects_language_type_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory)
            (input_dir / "Go microservices_repos.txt").write_text(
                "# services\n"
                "https://github.com/example/one\n"
                "https://github.com/example/two\n",
                encoding="utf-8",
            )
            (input_dir / "Python monolith_repos.txt").write_text(
                "https://github.com/example/app\n"
                "not-a-url\n",
                encoding="utf-8",
            )

            entries = pipeline.inspect_input_files(input_dir)

        self.assertEqual(
            [
                (
                    entry["name"],
                    entry["language"],
                    entry["system_type"],
                    entry["repository_count"],
                )
                for entry in entries
            ],
            [
                ("Go microservices_repos.txt", "Go", "microservices", 2),
                ("Python monolith_repos.txt", "Python", "monolith", 1),
            ],
        )
        self.assertEqual(entries[1]["invalid_count"], 1)

    def test_interactive_menu_runs_selected_input_without_affecting_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory)
            selected = input_dir / "Go microservices_repos.txt"
            selected.write_text(
                "https://github.com/example/service\n", encoding="utf-8"
            )
            answers = iter(["1", "6"])
            output = []

            with (
                patch.object(pipeline, "INPUT_DIR", input_dir),
                patch.object(pipeline, "run_benchmark") as execute,
            ):
                pipeline.run_interactive_menu(
                    input_fn=lambda prompt: next(answers),
                    output_fn=output.append,
                )

            execute.assert_called_once()
            self.assertEqual(execute.call_args.kwargs["input_paths"], [selected])
            self.assertEqual(execute.call_args.kwargs["execution_mode"], "metrics")
            self.assertTrue(
                any("Go | microservices | 1 repositories" in line for line in output)
            )

    def test_clean_temporary_folders_preserves_repository_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_dir = root / "raw"
            cache = raw_dir / "r_valid"
            stale = raw_dir / "r_stale.fetching"
            cache.mkdir(parents=True)
            stale.mkdir()
            (cache / "app.go").write_text("package main\n", encoding="utf-8")

            with (
                patch.object(pipeline, "BASE_DIR", root),
                patch.object(pipeline.tempfile, "gettempdir", return_value=str(root / "tmp")),
            ):
                result = pipeline.clean_temporary_folders(raw_dir)

            self.assertTrue(cache.exists())
            self.assertFalse(stale.exists())
            self.assertEqual(result["warnings"], [])

    def test_cli_keeps_run_non_interactive_and_supports_verbose(self):
        parser = pipeline.build_cli()
        normal = parser.parse_args(["run", "all", "--input", "repositories.csv"])
        recommended = parser.parse_args(["run", "--input", "repositories.csv"])
        verbose = parser.parse_args(
            ["run", "all", "--input", "repositories.csv", "--debug"]
        )
        menu = parser.parse_args(["menu"])

        self.assertEqual(normal.command, "run")
        self.assertEqual(recommended.step, "metrics")
        self.assertEqual(AnalysisConfig().workers, 1)
        self.assertEqual(AnalysisConfig().git_timeout_seconds, 900)
        self.assertEqual(AnalysisConfig().git_retries, 3)
        self.assertEqual(AnalysisConfig().acquisition_deadline_seconds, 1800)
        self.assertEqual(AnalysisConfig().auxiliary_warning_seconds, 120)
        self.assertEqual(AnalysisConfig().heartbeat_interval_seconds, 60)
        self.assertFalse(normal.verbose)
        self.assertTrue(verbose.verbose)
        self.assertEqual(menu.command, "menu")

        acquisition = parser.parse_args(
            ["run", "all", "--input", "repositories.csv", "--git-timeout", "45", "--git-retries", "2", "--workers", "2"]
        )
        self.assertEqual(acquisition.git_timeout, 45)
        self.assertEqual(acquisition.git_retries, 2)
        self.assertEqual(acquisition.workers, 2)

        performance = parser.parse_args(
            [
                "run", "metrics", "--input", "repositories.csv", "--acquisition-deadline", "60",
                "--auxiliary-warning-seconds", "5", "--heartbeat-seconds", "2",
                "--full-inventory",
            ]
        )
        self.assertEqual(performance.acquisition_deadline, 60)
        self.assertEqual(performance.auxiliary_warning_seconds, 5)
        self.assertEqual(performance.heartbeat_seconds, 2)
        self.assertTrue(performance.full_inventory)

    def test_menu_exits_cleanly_on_end_of_input(self):
        output = []
        with (
            patch.object(pipeline, "inspect_input_files", return_value=[]),
        ):
            pipeline.run_interactive_menu(
                input_fn=lambda prompt: (_ for _ in ()).throw(EOFError()),
                output_fn=output.append,
            )
        self.assertIn("\nGoodbye.", output)

    def test_failed_output_audit_cells_are_blank_and_schema_is_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "apps_catalog.csv"
            repo = {
                "url": "https://github.com/example/broken",
                "type": "monolith",
                "owner": "example",
                "repo_name": "broken",
                "metadata": {"error": "rate limited"},
                "fetch_status": "failed",
                "fetch_method": "none",
                "fetch_error_type": "clone_error",
                "fetch_error_message": "clone failed",
                "analysis_status": "skipped",
                "analysis_skip_reason": "clone failed",
                "status": "error",
                "error": "clone failed",
            }

            export_catalog([repo], output)
            with output.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))

            self.assertNotIn(None, row)
            self.assertEqual(row["name"], "broken")
            self.assertEqual(row["loc"], "")
            self.assertEqual(row["js_ts_skipped_by_category"], "")
            self.assertEqual(row["go_skipped_by_category"], "")
            self.assertEqual(row["go_detection_sample_files"], "")

            fact_path = generate_fact_sheet(repo, root / "facts")
            self.assertEqual(fact_path, root / "facts" / "broken.md")
            fact_text = (root / "facts" / "broken.md").read_text(encoding="utf-8")
            self.assertIn("**LOC:** unavailable", fact_text)
            self.assertNotIn("- None found", fact_text)


if __name__ == "__main__":
    unittest.main()
