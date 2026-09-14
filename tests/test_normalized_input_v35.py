"""Phase 5 tests: complete normalized-input ledger and its derivations.

Plan section 11. The ledger must retain every accepted source row — disabled
rows, identical duplicates, and repositories whose acquisition failed — and the
frozen and retry artifacts must be derivable from it.
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
from modules.config import AnalysisConfig
from modules.normalized_input import (
    DISPOSITION_FAILED_ACQUISITION,
    DISPOSITION_SKIPPED_DISABLED,
    DISPOSITION_SKIPPED_DUPLICATE,
    DUPLICATE_IDENTICAL,
    DUPLICATE_REPRESENTATIVE,
    NORMALIZED_INPUT_COLUMNS,
    derive_frozen_rows,
    derive_retry_rows,
    normalize_input_files,
    normalize_specs,
    reconcile_counts,
)
from modules.repository_input import InputValidationError, RepositorySpec

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_REQUESTED = "c" * 40


def spec(url, *, enabled=True, sha=None, line=2, file="repositories.csv", notes=""):
    return RepositorySpec(
        url=url, architecture_type="monolith", expected_language="Python",
        commit_sha=sha, enabled=enabled, notes=notes, input_file=file, input_line=line,
    )


class PopulationCompletenessTests(unittest.TestCase):
    def test_disabled_rows_are_retained(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", line=2),
            spec("https://github.com/acme/two", enabled=False, line=3),
        ])
        self.assertEqual(result.accepted_row_count, 2)
        self.assertEqual(result.disabled_row_count, 1)
        disabled = result.by_url("https://github.com/acme/two")[0]
        self.assertFalse(disabled.normalized_enabled)
        self.assertEqual(disabled.original_enabled, "FALSE")
        self.assertEqual(disabled.execution_disposition, DISPOSITION_SKIPPED_DISABLED)
        # It must not appear in the execution set.
        self.assertEqual(len(result.executing_rows()), 1)

    def test_identical_duplicates_are_retained_and_traceable(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", line=2),
            spec("https://github.com/acme/one", line=7),
        ])
        self.assertEqual(result.accepted_row_count, 2)
        self.assertEqual(result.duplicate_rows_dropped, 1)

        rows = sorted(result.rows, key=lambda item: item.original_row_index)
        self.assertEqual(rows[0].duplicate_classification, DUPLICATE_REPRESENTATIVE)
        self.assertEqual(rows[1].duplicate_classification, DUPLICATE_IDENTICAL)
        # The dropped duplicate points back at its representative.
        self.assertEqual(rows[1].duplicate_representative_source_row, 0)
        self.assertEqual(rows[1].execution_disposition, DISPOSITION_SKIPPED_DUPLICATE)
        self.assertEqual(rows[1].original_input_line, 7)
        self.assertEqual(len(result.executing_rows()), 1)

    def test_requested_sha_is_retained_on_every_row(self):
        result = normalize_specs([spec("https://github.com/acme/one", sha=SHA_REQUESTED)])
        self.assertEqual(result.rows[0].requested_sha, SHA_REQUESTED)

    def test_source_row_index_is_stable_and_dense(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", line=2),
            spec("https://github.com/acme/two", enabled=False, line=3),
            spec("https://github.com/acme/one", line=4),
        ])
        self.assertEqual(
            [row.original_row_index for row in result.rows], [0, 1, 2]
        )

    def test_multi_file_input_keeps_each_file_identity(self):
        result = normalize_specs(
            [
                spec("https://github.com/acme/one", file="mono.csv", line=2),
                spec("https://github.com/acme/two", file="micro.csv", line=2),
            ],
            source_files={"mono.csv": "1" * 64, "micro.csv": "2" * 64},
        )
        identities = {row.source_input_file_id for row in result.rows}
        self.assertEqual(identities, {"mono.csv", "micro.csv"})
        hashes = {row.source_input_file_sha256 for row in result.rows}
        self.assertEqual(hashes, {"1" * 64, "2" * 64})

    def test_aggregate_hash_is_order_independent_and_content_sensitive(self):
        forward = normalize_specs([], source_files={"a.csv": "1" * 64, "b.csv": "2" * 64})
        reverse = normalize_specs([], source_files={"b.csv": "2" * 64, "a.csv": "1" * 64})
        changed = normalize_specs([], source_files={"a.csv": "1" * 64, "b.csv": "3" * 64})
        self.assertEqual(forward.aggregate_hash, reverse.aggregate_hash)
        self.assertNotEqual(forward.aggregate_hash, changed.aggregate_hash)

    def test_no_absolute_path_is_stored_in_a_portable_field(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", file=r"D:\secret\place\repositories.csv"),
        ])
        self.assertEqual(result.rows[0].source_input_file_id, "repositories.csv")


class ConflictingDuplicateTests(unittest.TestCase):
    """Conflicting duplicates stay fatal; they never reach a ledger."""

    def test_conflicting_duplicate_is_fatal_and_names_every_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repositories.csv"
            path.write_text(
                "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
                "https://github.com/acme/one,monolith,Python,,true,first\n"
                "https://github.com/acme/one,microservices,Python,,true,second\n",
                encoding="utf-8",
            )
            with self.assertRaises(InputValidationError) as caught:
                normalize_input_files([path])
        joined = "\n".join(caught.exception.errors)
        self.assertIn("conflicting duplicate", joined)
        self.assertIn(":2", joined)
        self.assertIn(":3", joined)


class DerivationTests(unittest.TestCase):
    def _executed_result(self):
        result = normalize_specs([
            spec("https://github.com/acme/ok", sha=SHA_REQUESTED, line=2),
            spec("https://github.com/acme/failed", sha=SHA_REQUESTED, line=3),
            spec("https://github.com/acme/disabled", enabled=False, line=4),
        ])
        from modules.normalized_input import apply_results

        apply_results(result, [
            {
                "repository_url": "https://github.com/acme/ok",
                "analysis_status": "complete",
                "acquisition": {
                    "analyzed_commit_sha": SHA_A,
                    "commit_verification_status": "verified",
                },
            },
            {
                "repository_url": "https://github.com/acme/failed",
                "analysis_status": "failed",
                "requested_commit_sha": SHA_REQUESTED,
                "acquisition": {
                    "analyzed_commit_sha": None,
                    "commit_verification_status": "not_evaluable",
                },
            },
        ])
        return result

    def test_failed_acquisition_keeps_its_requested_sha(self):
        # Finding F-5. This is the 7ep control's shape.
        result = self._executed_result()
        failed = result.by_url("https://github.com/acme/failed")[0]
        self.assertEqual(failed.execution_disposition, DISPOSITION_FAILED_ACQUISITION)
        self.assertIsNone(failed.analyzed_sha)
        self.assertEqual(failed.requested_sha, SHA_REQUESTED)
        self.assertFalse(failed.measured_scope_inclusion)

    def test_retry_derivation_falls_back_to_the_requested_sha(self):
        rows = {row["url"]: row for row in derive_retry_rows(self._executed_result())}
        self.assertEqual(rows["https://github.com/acme/failed"]["commit_sha"], SHA_REQUESTED)
        self.assertEqual(rows["https://github.com/acme/ok"]["commit_sha"], SHA_A)

    def test_frozen_derivation_requires_a_verified_analyzed_sha(self):
        rows = {row["url"]: row for row in derive_frozen_rows(self._executed_result())}
        self.assertIn("https://github.com/acme/ok", rows)
        # A failed acquisition must never enter the frozen set: a frozen input
        # asserts reproducibility that an unacquired repository cannot support.
        self.assertNotIn("https://github.com/acme/failed", rows)
        self.assertEqual(rows["https://github.com/acme/ok"]["commit_sha"], SHA_A)

    def test_disabled_rows_never_enter_frozen_or_retry(self):
        result = self._executed_result()
        urls = {row["url"] for row in derive_frozen_rows(result)}
        urls |= {row["url"] for row in derive_retry_rows(result)}
        self.assertNotIn("https://github.com/acme/disabled", urls)

    def test_dropped_duplicates_never_enter_derivations(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", line=2),
            spec("https://github.com/acme/one", line=3),
        ])
        from modules.normalized_input import apply_results

        apply_results(result, [{
            "repository_url": "https://github.com/acme/one",
            "analysis_status": "complete",
            "acquisition": {
                "analyzed_commit_sha": SHA_A, "commit_verification_status": "verified",
            },
        }])
        self.assertEqual(len(derive_frozen_rows(result)), 1)
        self.assertEqual(len(derive_retry_rows(result)), 1)


class ReconciliationTests(unittest.TestCase):
    def test_counts_reconcile_with_a_matching_manifest(self):
        result = normalize_specs([
            spec("https://github.com/acme/one", line=2),
            spec("https://github.com/acme/two", enabled=False, line=3),
            spec("https://github.com/acme/one", line=4),
        ])
        self.assertEqual(reconcile_counts(result, {
            "input_row_count": 3,
            "skipped_disabled_count": 1,
            "duplicate_rows_dropped": 1,
        }), [])

    def test_count_disagreement_is_reported(self):
        result = normalize_specs([spec("https://github.com/acme/one")])
        problems = reconcile_counts(result, {"input_row_count": 99})
        self.assertEqual(len(problems), 1)
        self.assertIn("manifest records 99", problems[0])


class EndToEndLedgerTests(unittest.TestCase):
    """The ledger must actually be emitted by a real run."""

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
            f"https://github.com/acme/mono,monolith,Python,{SHA_A},true,one\n"
            f"https://github.com/acme/micro,microservices,Python,{SHA_B},true,two\n"
            f"https://github.com/acme/off,monolith,Python,,false,disabled row\n"
            f"https://github.com/acme/mono,monolith,Python,{SHA_A},true,one\n",
            encoding="utf-8",
        )

    @contextmanager
    def _acquire(self, spec_, config, mode="latest", progress=None):
        del config, mode, progress
        sha = SHA_A if spec_.repository_name == "mono" else SHA_B
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec_.url,
                repository_owner=spec_.owner,
                repository_name=spec_.repository_name,
                requested_commit_sha=spec_.commit_sha,
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

    def _run(self):
        config = AnalysisConfig.from_env(
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])

    def _ledger(self, run):
        with (run / "normalized_input.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_normalized_input_is_emitted_with_the_contract_columns(self):
        run = self._run()
        path = run / "normalized_input.csv"
        self.assertTrue(path.is_file(), "normalized_input.csv must be a mandatory artifact")
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(tuple(header), NORMALIZED_INPUT_COLUMNS)

    def test_every_accepted_source_row_is_present(self):
        rows = self._ledger(self._run())
        # Four source rows: two executed, one disabled, one identical duplicate.
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            sorted(row["original_row_index"] for row in rows), ["0", "1", "2", "3"]
        )

    def test_disabled_and_duplicate_rows_survive_into_the_artifact(self):
        rows = {row["original_row_index"]: row for row in self._ledger(self._run())}
        self.assertEqual(rows["2"]["normalized_enabled"], "FALSE")
        self.assertEqual(rows["2"]["execution_disposition"], "skipped_disabled")
        self.assertEqual(rows["3"]["duplicate_classification"], "identical_duplicate")
        self.assertEqual(rows["3"]["duplicate_representative_source_row"], "0")

    def test_requested_sha_survives_into_the_artifact(self):
        rows = self._ledger(self._run())
        by_url = {row["canonical_url"]: row for row in rows if row["requested_sha"]}
        self.assertEqual(by_url["https://github.com/acme/mono"]["requested_sha"], SHA_A)

    def test_frozen_artifact_is_derivable_from_the_ledger(self):
        run = self._run()
        from modules.normalized_input import normalize_specs as _n

        with (run / "repositories_frozen.csv").open(encoding="utf-8", newline="") as handle:
            emitted = list(csv.DictReader(handle))
        ledger = self._ledger(run)
        analyzed = {
            row["canonical_url"]: row["analyzed_sha"]
            for row in ledger
            if row["commit_verification_state"] == "verified"
            and row["duplicate_classification"] != "identical_duplicate"
        }
        self.assertEqual(
            {row["url"]: row["commit_sha"] for row in emitted}, analyzed
        )

    def test_ledger_counts_reconcile_with_the_manifest(self):
        run = self._run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        rows = self._ledger(run)
        self.assertEqual(manifest["input_row_count"], len(rows))
        self.assertEqual(
            manifest["skipped_disabled_count"],
            sum(1 for row in rows if row["normalized_enabled"] == "FALSE"),
        )
        self.assertEqual(
            manifest["duplicate_rows_dropped"],
            sum(1 for row in rows if row["duplicate_classification"] == "identical_duplicate"),
        )

    def test_ledger_validates_against_the_packaged_schema(self):
        from validation.artifact_io import schema_store
        from validation.artifact_io.contracts import contract_for
        from validation.artifact_io.strict_csv import read_rows

        run = self._run()
        rows = read_rows(
            run / "normalized_input.csv", "normalized_input.csv",
            contract_for("normalized_input.csv", "1.5.0"),
        )
        self.assertEqual(len(rows), 4)
        for row in rows:
            violations = schema_store.validate_document(
                "normalized_input_row", _jsonable(row), "normalized_input.csv"
            )
            self.assertEqual([str(item) for item in violations], [])


def _jsonable(row):
    """Drop unavailable cells so schema optionality is exercised honestly."""
    from validation.artifact_io.strict_csv import JSON_NULL

    return {
        name: (None if value is JSON_NULL else value)
        for name, value in row.items()
        if value is not None
    }


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
