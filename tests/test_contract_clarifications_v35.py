"""Contract-clarification regression tests for findings F-6 and F-7.

Neither finding changes a metric definition. These tests exist so the behaviour
each one documents cannot regress silently.
"""

import csv
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import METRIC_CONTRACT_VERSION, AnalysisConfig
from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory

REPOSITORY = Path(__file__).resolve().parent.parent
SHA_A = "a" * 40


def measure(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return compute_repository_metrics(RepositoryInventory(root))["aggregate"]


class F6StableAssignmentTests(unittest.TestCase):
    """A stable named module-scope binding counts; an anonymous form does not."""

    def test_metric_contract_version_is_unchanged(self):
        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")

    def test_clarification_is_documented_as_a_clarification(self):
        text = (REPOSITORY / "docs" / "METRIC_CONTRACT_V3.md").read_text(encoding="utf-8")
        self.assertIn("no definition changed", text)
        self.assertIn("stable named module-scope binding", text)
        self.assertIn("Metric Contract remains 3.0.0", text)

    # -- positive controls -------------------------------------------------

    def test_stable_arrow_binding_counts_as_a_module_function(self):
        aggregate = measure({"a.js": b"const double = (value) => value * 2;\n"})
        self.assertEqual(aggregate["methods_functions"], 1)
        self.assertEqual(aggregate["module_functions"], 1)
        self.assertEqual(aggregate["lambdas"], 0)

    def test_stable_function_expression_binding_counts(self):
        aggregate = measure(
            {"a.js": b"const double = function (value) { return value * 2; };\n"}
        )
        self.assertEqual(aggregate["methods_functions"], 1)
        self.assertEqual(aggregate["module_functions"], 1)

    def test_stable_class_binding_counts_the_symmetric_class_case(self):
        aggregate = measure({"a.js": b"const Alpha = class {\n  run() {}\n};\n"})
        self.assertEqual(aggregate["classes_structs"], 1)

    # -- negative controls -------------------------------------------------

    def test_anonymous_callback_counts_as_nothing(self):
        aggregate = measure({
            "a.js": b"const items = [1, 2];\nitems.forEach(function (item) {\n"
                    b"  return item;\n});\n",
        })
        self.assertEqual(aggregate["methods_functions"], 0)
        self.assertEqual(aggregate["methods_functions_status"], "complete")

    def test_anonymous_inline_arrow_counts_as_nothing(self):
        aggregate = measure({
            "a.js": b"const items = [1, 2];\nitems.map((item) => item + 1);\n",
        })
        self.assertEqual(aggregate["methods_functions"], 0)

    def test_the_two_forms_are_distinguished_by_the_binding_not_the_syntax(self):
        # Identical arrow syntax; only the stable module-scope name differs.
        named = measure({"a.js": b"const f = (v) => v + 1;\n"})
        anonymous = measure({"a.js": b"[1].map((v) => v + 1);\n"})
        self.assertEqual(named["methods_functions"], 1)
        self.assertEqual(anonymous["methods_functions"], 0)


class F7InputPopulationTests(unittest.TestCase):
    """input_row_count counts the accepted population before deduplication."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    def _write_input(self, rows):
        path = self.root / "repositories.csv"
        path.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            + "".join(rows),
            encoding="utf-8",
        )
        return path

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

    def _run(self, path):
        config = AnalysisConfig.from_env(
            output_root=self.root / "output", cache_root=self.root / "cache",
            temporary_directory=self.root / "temp", workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                [path], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])

    def test_input_row_count_equals_the_ledger_row_count(self):
        path = self._write_input([
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            f"https://github.com/acme/two,monolith,Python,{SHA_A},true,b\n",
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            "https://github.com/acme/off,monolith,Python,,false,disabled\n",
        ])
        run = self._run(path)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        with (run / "normalized_input.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        # Four accepted source rows: two unique, one identical duplicate, one
        # disabled. Before this correction the manifest reported three.
        self.assertEqual(len(rows), 4)
        self.assertEqual(manifest["input_row_count"], 4)
        self.assertEqual(manifest["duplicate_rows_dropped"], 1)
        self.assertEqual(manifest["skipped_disabled_count"], 1)

    def test_duplicates_are_a_subset_of_the_count_not_a_separate_addend(self):
        path = self._write_input([
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
        ])
        run = self._run(path)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["input_row_count"], 2)
        self.assertEqual(manifest["duplicate_rows_dropped"], 1)
        # The old semantics would have made these sum to the population.
        self.assertNotEqual(
            manifest["input_row_count"] + manifest["duplicate_rows_dropped"], 2
        )

    def test_run_status_agrees_with_the_manifest(self):
        path = self._write_input([
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
        ])
        run = self._run(path)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["input_row_count"], manifest["input_row_count"])
        self.assertEqual(
            status["duplicate_rows_dropped"], manifest["duplicate_rows_dropped"]
        )

    def test_ledger_reconciles_with_the_manifest_counts(self):
        from modules.normalized_input import normalize_input_files, reconcile_counts

        path = self._write_input([
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            f"https://github.com/acme/one,monolith,Python,{SHA_A},true,a\n",
            "https://github.com/acme/off,monolith,Python,,false,disabled\n",
        ])
        run = self._run(path)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(reconcile_counts(normalize_input_files([path]), manifest), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
