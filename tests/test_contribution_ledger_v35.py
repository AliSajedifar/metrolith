"""Phase 8 tests: per-file metric-contribution ledger (plan section 14)."""

import csv
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.contribution_ledger import (
    CONTRIBUTION_COLUMNS,
    RECONCILIATION_EXACT,
    RECONCILIATION_NOT_EVALUABLE,
    RECONCILIATION_RESIDUAL,
    build_rows,
    join_to_inventory,
    reconcile_repository,
    reconcile_run,
)
from modules.core_metrics import (
    ENTITY_KEYS,
    LOC_KEYS,
    compute_repository_metrics,
    derive_classes_structs,
    derive_lines_of_code,
    derive_methods_functions,
)
from modules.inventory import RepositoryInventory

SHA_A = "a" * 40


class RowContractTests(unittest.TestCase):
    def test_columns_match_the_packaged_schema(self):
        from validation.artifact_io.schema_store import load_schema

        schema = load_schema("contribution_row")
        self.assertEqual(set(CONTRIBUTION_COLUMNS), set(schema["properties"]))

    def test_every_raw_loc_and_entity_component_is_persisted(self):
        for key in (*LOC_KEYS, *ENTITY_KEYS):
            with self.subTest(component=key):
                self.assertIn(key, CONTRIBUTION_COLUMNS)

    def test_derived_values_are_persisted_alongside_raw_components(self):
        for key in ("lines_of_code", "classes_structs", "methods_functions",
                    "source_files_contribution"):
            with self.subTest(derived=key):
                self.assertIn(key, CONTRIBUTION_COLUMNS)


class SingleMetricImplementationTests(unittest.TestCase):
    """The ledger must not contain a second metric calculation."""

    def test_derived_functions_are_the_only_expressions(self):
        components = {
            "code_lines": 7, "classes": 2, "records": 1, "structs": 3,
            "module_functions": 4, "class_methods": 5, "receiver_methods": 6,
        }
        self.assertEqual(derive_lines_of_code(components), 7)
        self.assertEqual(derive_classes_structs(components), 6)
        self.assertEqual(derive_methods_functions(components), 15)

    def test_core_metrics_uses_the_shared_derivers(self):
        source = (
            Path(__file__).resolve().parent.parent / "modules" / "core_metrics.py"
        ).read_text(encoding="utf-8")
        # _finalize_metric must call the shared functions rather than repeat the
        # arithmetic, so the aggregate and the ledger cannot drift.
        self.assertIn('metric["lines_of_code"] = derive_lines_of_code(metric)', source)
        self.assertIn('metric["classes_structs"] = derive_classes_structs(metric)', source)
        self.assertIn('metric["methods_functions"] = derive_methods_functions(metric)', source)


class ContributionCaptureTests(unittest.TestCase):
    def _metrics(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        inventory = RepositoryInventory(root)
        return compute_repository_metrics(inventory), inventory

    def test_one_row_per_included_file(self):
        metrics, inventory = self._metrics({
            "a.py": b"x = 1\n",
            "b.py": b"y = 2\n",
        })
        contributions = metrics["file_contributions"]
        included = [r for r in inventory if r.included_in_metrics]
        self.assertEqual(len(contributions), len(included))

    def test_rows_are_deterministically_ordered_by_path(self):
        metrics, _ = self._metrics({
            "z.py": b"z = 1\n", "a.py": b"a = 1\n", "m.py": b"m = 1\n",
        })
        paths = [row["relative_path"] for row in metrics["file_contributions"]]
        self.assertEqual(paths, sorted(paths))

    def test_raw_components_are_present_and_typed(self):
        metrics, _ = self._metrics({"a.java": b"public class A {\n    void run() {}\n}\n"})
        row = metrics["file_contributions"][0]
        for key in (*LOC_KEYS, *ENTITY_KEYS):
            with self.subTest(component=key):
                self.assertIn(key, row)
                self.assertIsInstance(row[key], int)

    def test_derived_values_match_the_shared_derivers(self):
        metrics, _ = self._metrics({"a.java": b"public class A {\n    void run() {}\n}\n"})
        row = metrics["file_contributions"][0]
        self.assertEqual(row["lines_of_code"], derive_lines_of_code(row))
        self.assertEqual(row["classes_structs"], derive_classes_structs(row))
        self.assertEqual(row["methods_functions"], derive_methods_functions(row))


class ReconciliationTests(unittest.TestCase):
    def _result(self, aggregate, rows):
        return (
            {"repository_url": "https://x/a", "metrics": {"aggregate": aggregate}},
            [{"repository_url": "https://x/a", "contribution_state": "contributed", **row}
             for row in rows],
        )

    def test_exact_reconciliation_when_components_sum(self):
        result, rows = self._result(
            {"source_files": 2, "code_lines": 10, "loc_status": "complete",
             "lines_of_code": 10},
            [
                {"source_files_contribution": 1, "code_lines": 4},
                {"source_files_contribution": 1, "code_lines": 6},
            ],
        )
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        self.assertEqual(found["code_lines"].outcome, RECONCILIATION_EXACT)
        self.assertEqual(found["source_files"].outcome, RECONCILIATION_EXACT)
        self.assertEqual(found["lines_of_code"].outcome, RECONCILIATION_EXACT)

    def test_residual_is_reported_when_components_do_not_sum(self):
        result, rows = self._result(
            {"source_files": 2, "code_lines": 99, "loc_status": "complete"},
            [
                {"source_files_contribution": 1, "code_lines": 4},
                {"source_files_contribution": 1, "code_lines": 6},
            ],
        )
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        self.assertEqual(found["code_lines"].outcome, RECONCILIATION_RESIDUAL)
        self.assertEqual(found["code_lines"].residual, 89)

    def test_null_on_failed_is_not_evaluable_never_zero(self):
        # Plan section 14.4 rule 4. This is the rule most likely to be violated
        # by an implementation that "helpfully" treats null as zero.
        result, rows = self._result(
            {"source_files": 1, "loc_status": "failed", "lines_of_code": None,
             "code_lines": None},
            [{"source_files_contribution": 1, "code_lines": None}],
        )
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        for dimension in ("code_lines", "lines_of_code"):
            with self.subTest(dimension=dimension):
                self.assertEqual(found[dimension].outcome, RECONCILIATION_NOT_EVALUABLE)
                self.assertIsNone(found[dimension].residual)
                self.assertIn("failed", found[dimension].reason)

    def test_a_partial_repository_reconciles_over_its_recorded_rows(self):
        """A file that recorded nothing contributed nothing to the aggregate.

        `core_metrics` adds a file's components only when the extraction
        produced them, so the aggregate for a partial repository is the sum over
        the files that succeeded. Reconciliation follows the same rule, and the
        rows that recorded nothing are reported alongside rather than hidden.
        """
        result, rows = self._result(
            {"source_files": 2, "code_lines": 4, "loc_status": "partial"},
            [
                {"source_files_contribution": 1, "code_lines": 4},
                {"source_files_contribution": 1, "code_lines": None},
            ],
        )
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        self.assertEqual(found["code_lines"].outcome, RECONCILIATION_EXACT)
        self.assertEqual(found["code_lines"].contribution_sum, 4)
        self.assertIn("recorded no value", found["code_lines"].reason)

    def test_a_partial_repository_that_does_not_sum_reports_a_residual(self):
        # The aggregate claims 10 but only 4 is accounted for. A null row cannot
        # explain the other 6, so this is a genuine unexplained residual.
        result, rows = self._result(
            {"source_files": 2, "code_lines": 10, "loc_status": "partial"},
            [
                {"source_files_contribution": 1, "code_lines": 4},
                {"source_files_contribution": 1, "code_lines": None},
            ],
        )
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        self.assertEqual(found["code_lines"].outcome, RECONCILIATION_RESIDUAL)
        self.assertEqual(found["code_lines"].residual, 6)

    def test_unsupported_language_rows_do_not_contribute(self):
        result = {"repository_url": "https://x/a",
                  "metrics": {"aggregate": {"source_files": 1, "code_lines": 4,
                                            "loc_status": "complete"}}}
        rows = [
            {"repository_url": "https://x/a", "contribution_state": "contributed",
             "source_files_contribution": 1, "code_lines": 4},
            {"repository_url": "https://x/a",
             "contribution_state": "no_contribution_unsupported_language",
             "source_files_contribution": 0, "code_lines": None},
        ]
        found = {item.dimension: item for item in reconcile_repository(result, rows)}
        self.assertEqual(found["source_files"].outcome, RECONCILIATION_EXACT)
        self.assertEqual(found["code_lines"].outcome, RECONCILIATION_EXACT)


class InventoryJoinTests(unittest.TestCase):
    def test_every_row_joins_to_exactly_one_inventory_record(self):
        inventories = {"a": {
            "repository_url": "https://x/a",
            "files": [{"relative_path": "a.py", "content_hash": "h1"}],
        }}
        rows = [{"repository_url": "https://x/a", "relative_path": "a.py",
                 "content_sha256": "h1"}]
        self.assertEqual(join_to_inventory(rows, inventories), [])

    def test_an_unjoinable_row_is_reported(self):
        inventories = {"a": {
            "repository_url": "https://x/a",
            "files": [{"relative_path": "a.py", "content_hash": "h1"}],
        }}
        rows = [{"repository_url": "https://x/a", "relative_path": "ghost.py",
                 "content_sha256": "h2"}]
        problems = join_to_inventory(rows, inventories)
        self.assertEqual(len(problems), 1)
        self.assertIn("ghost.py", problems[0])


class EndToEndLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )
        (self.repo / "helper.py").write_text(
            "def helper():\n    return 2\n", encoding="utf-8"
        )
        (self.repo / "notes.md").write_text("# not a source file\n", encoding="utf-8")
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

    def test_ledger_and_container_descriptor_are_emitted(self):
        run = self._run()
        self.assertTrue((run / "contributions.csv").is_file())
        descriptor = json.loads(
            (run / "contributions" / "container.json").read_text(encoding="utf-8")
        )
        self.assertEqual(descriptor["container_format"], "single_csv")
        # 1.9.0: the row gained three complexity-state columns. The container
        # DOCUMENT is unchanged -- `row_contract_version` is a generic semver
        # pattern there, not a const -- so only the value moves.
        self.assertEqual(descriptor["row_contract_version"], "1.9.0")
        self.assertIn("selection_evidence", descriptor)

    def test_ledger_header_matches_the_fixed_row_contract(self):
        run = self._run()
        with (run / "contributions.csv").open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(tuple(header), CONTRIBUTION_COLUMNS)

    def test_exact_reconciliation_against_the_emitted_run(self):
        run = self._run()
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        with (run / "contributions.csv").open(encoding="utf-8", newline="") as handle:
            raw = list(csv.DictReader(handle))

        numeric = set(LOC_KEYS) | set(ENTITY_KEYS) | {
            "lines_of_code", "classes_structs", "methods_functions",
            "source_files_contribution", "size_bytes",
        }
        rows = [
            {
                key: (None if value == "" else (int(value) if key in numeric else value))
                for key, value in row.items()
            }
            for row in raw
        ]

        report = reconcile_run(analysis, rows)
        self.assertTrue(report["reconciled"], json.dumps(report, indent=2)[:2000])
        self.assertEqual(report["residual_count"], 0)
        self.assertGreater(report["exact_count"], 0)

    def test_ledger_rows_join_to_the_emitted_inventory(self):
        run = self._run()
        with (run / "contributions.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        inventories = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in (run / "file_inventory").glob("*.json")
        }
        for inventory in inventories.values():
            inventory.setdefault(
                "repository_url", rows[0]["repository_url"] if rows else ""
            )
        self.assertEqual(join_to_inventory(rows, inventories), [])

    def test_non_source_files_are_absent_from_the_ledger(self):
        run = self._run()
        with (run / "contributions.csv").open(encoding="utf-8", newline="") as handle:
            paths = {row["relative_path"] for row in csv.DictReader(handle)}
        self.assertIn("app.py", paths)
        self.assertNotIn("notes.md", paths)

    def test_ledger_validates_against_the_packaged_schema(self):
        from validation.artifact_io import schema_store
        from validation.artifact_io.contracts import contract_for
        from validation.artifact_io.strict_csv import JSON_NULL, read_rows

        run = self._run()
        rows = read_rows(
            run / "contributions.csv", "contributions.csv",
            contract_for("contributions.csv", "1.5.0"),
        )
        self.assertGreater(len(rows), 0)
        for row in rows:
            payload = {
                name: (None if value is JSON_NULL else value)
                for name, value in row.items() if value is not None
            }
            violations = schema_store.validate_document(
                "contribution_row", payload, "contributions.csv"
            )
            self.assertEqual([str(item) for item in violations], [])

    def test_reader_streams_the_ledger(self):
        from validation.artifact_io.reader import open_run

        run = self._run()
        view = open_run(run)
        self.assertTrue(view.has_contribution_ledger)
        streamed = list(view.stream_contributions())
        self.assertGreater(len(streamed), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
