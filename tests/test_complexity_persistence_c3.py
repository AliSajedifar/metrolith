"""C3: Artifact Schema 1.9 complexity persistence.

Covers the writer, the aggregates, the run-level state, the reader surface,
Semantic Projection 2.2.0, and the finalization completeness invariant.

The completeness gate is the highest-risk item here, so it is mutation-tested
against exact bytes: a candidate that CLAIMS complete complexity while the
callable artifact is withheld or corrupted must be refused, and refused before
anything terminal is committed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from modules.callable_ledger import (
    AGGREGATE_FIELDS,
    CALLABLE_COLUMNS,
    aggregate_rows,
    derive_complexity,
    lower_median,
)
from validation.artifact_io.reader import open_run

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPOSITORY_ROOT / "validation/differential/corpus/complexity"


def analyze_corpus(output_root: Path) -> Path:
    """Run a real local analysis and return its run directory."""
    from modules.cli import __name__ as _  # noqa: F401  (ensure package import)
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable, "-m", "pipeline", "analyze", str(CORPUS),
            "--output-root", str(output_root),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    runs = list((output_root / "runs").iterdir())
    assert len(runs) == 1, runs
    return runs[0]


class AggregateTests(unittest.TestCase):
    """Only the approved set, and nulls where nothing is evaluable."""

    def test_the_aggregate_set_is_exactly_the_approved_one(self):
        result = aggregate_rows([])
        self.assertEqual(sorted(result), sorted(AGGREGATE_FIELDS))
        for name in result:
            self.assertNotIn("p90", name)
            self.assertNotIn("p95", name)
            self.assertNotIn("above", name)
            self.assertNotIn("score", name)

    def test_lower_median_returns_an_observed_value(self):
        self.assertEqual(lower_median([1, 2, 3, 4]), 2)
        self.assertEqual(lower_median([5]), 5)
        self.assertIsNone(lower_median([]))

    def test_failed_rows_contribute_no_measurement(self):
        rows = [
            {"structural_complexity_status": "failed", "nloc_status": "failed",
             "cyclomatic_complexity": None, "nloc": None,
             "max_nesting_depth": None, "formal_parameter_count": None},
        ]
        result = aggregate_rows(rows)
        self.assertEqual(result["callable_count"], 1)
        self.assertIsNone(
            result["cyclomatic_complexity_total"],
            "a failed row must not reconcile as a measured zero",
        )
        self.assertIsNone(result["cyclomatic_complexity_max"])

    def test_measured_rows_aggregate(self):
        rows = [
            {"structural_complexity_status": "complete", "nloc_status": "complete",
             "cyclomatic_complexity": value, "nloc": value * 2,
             "max_nesting_depth": 1, "formal_parameter_count": 2}
            for value in (1, 3, 5)
        ]
        result = aggregate_rows(rows)
        self.assertEqual(result["cyclomatic_complexity_total"], 9)
        self.assertEqual(result["cyclomatic_complexity_max"], 5)
        self.assertEqual(result["cyclomatic_complexity_median"], 3)
        self.assertEqual(result["cyclomatic_complexity_mean"], 3.0)
        self.assertEqual(result["nloc_max"], 10)


class RepositoryStatusTests(unittest.TestCase):
    """Repository status comes from FILE statuses, never from row presence."""

    def test_measured_files_with_no_callables_are_complete_not_unavailable(self):
        summary = derive_complexity([], ["complete", "complete"], {})
        self.assertEqual(summary["status"], "complete")
        self.assertIsNone(summary["unavailable_reason"])
        self.assertEqual(summary["aggregate"]["callable_count"], 0)

    def test_all_failed_files_are_failed_with_a_typed_reason(self):
        summary = derive_complexity([], ["failed", "failed"], {})
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["unavailable_reason"], "all_files_failed_parse")
        self.assertIsNone(
            summary["aggregate"]["callable_count"],
            "a failed repository reports null, never a measured zero",
        )

    def test_no_supported_source_is_not_applicable_with_a_reason(self):
        summary = derive_complexity([], [], {})
        self.assertEqual(summary["status"], "not_applicable")
        self.assertEqual(summary["unavailable_reason"], "no_supported_source_files")

    def test_a_mix_is_partial(self):
        summary = derive_complexity([], ["complete", "failed"], {})
        self.assertEqual(summary["status"], "partial")
        self.assertIsNone(summary["unavailable_reason"])

    def test_zero_callables_measured_differs_from_unmeasurable(self):
        measured = derive_complexity([], ["complete"], {})
        unmeasurable = derive_complexity([], ["failed"], {})
        self.assertEqual(measured["aggregate"]["callable_count"], 0)
        self.assertIsNone(unmeasurable["aggregate"]["callable_count"])
        self.assertNotEqual(measured["status"], unmeasurable["status"])


class EndToEndPersistenceTests(unittest.TestCase):
    """A real run, read back through the strict reader."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.output_root = Path(cls._temporary.name)
        cls.run_dir = analyze_corpus(cls.output_root)

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_the_run_declares_the_native_artifact_schema(self):
        """Pinned to the constant, not to a literal.

        This test said "1.9" and had to be edited when the native schema became
        1.10. The completeness layer it guards had the same literal and silently
        switched itself off instead of failing -- so the literal is gone from
        both.
        """
        from modules.config import ARTIFACT_SCHEMA_VERSION

        view = open_run(self.run_dir)
        self.assertEqual(
            str(view.declared_artifact_schema), ARTIFACT_SCHEMA_VERSION
        )

    def test_the_callable_artifact_exists_and_streams(self):
        view = open_run(self.run_dir)
        self.assertTrue(view.has_callable_artifact)
        rows = list(view.stream_callables())
        self.assertGreater(len(rows), 0)
        self.assertEqual(sorted(rows[0]), sorted(CALLABLE_COLUMNS))

    def test_row_count_reconciles_with_the_declared_aggregate(self):
        view = open_run(self.run_dir)
        streamed = len(list(view.stream_callables()))
        declared = sum(
            (repository.get("metrics", {}).get("complexity") or {})
            .get("aggregate", {}).get("callable_count") or 0
            for repository in view.repositories
        )
        self.assertEqual(streamed, declared)

    def test_callable_rows_reconcile_with_methods_functions(self):
        """The C1 keystone, now across the persisted artifact."""
        view = open_run(self.run_dir)
        streamed = len(list(view.stream_callables()))
        methods = sum(
            (repository.get("metrics", {}).get("aggregate") or {})
            .get("methods_functions") or 0
            for repository in view.repositories
        )
        self.assertEqual(streamed, methods)

    def test_run_status_declares_the_measurement_state(self):
        status = json.loads((self.run_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["complexity_measurement_state"], "measured")
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["mandatory_output_failures"], [])

    def test_the_container_declares_the_row_contract(self):
        container = json.loads(
            (self.run_dir / "callables" / "container.json").read_text(encoding="utf-8")
        )
        self.assertEqual(container["row_contract_version"], "1.9.0")
        self.assertEqual(
            container["row_count"], sum(p["row_count"] for p in container["partitions"])
        )

    def test_file_level_status_distinguishes_measured_empty_from_failed(self):
        view = open_run(self.run_dir)
        rows = list(view.stream_contributions())
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row.get("relative_path")):
                self.assertIn(
                    row["structural_complexity_status"],
                    {"complete", "partial", "failed", "not_applicable"},
                )
                if row["structural_complexity_status"] == "failed":
                    self.assertIsNone(row["callable_count"])


class FinalizationCompletenessMutationTests(unittest.TestCase):
    """The gate must refuse a run that claims complexity it did not publish.

    Each case mutates the PERSISTED bytes of an already-finalized run and
    re-evaluates the check, which is the only way to prove it reads bytes rather
    than the producer's memory.
    """

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.source_run = analyze_corpus(Path(cls._temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def _copy(self) -> Path:
        target = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        shutil.copytree(self.source_run, target)
        return target

    def _problems(self, run_dir: Path) -> list[str]:
        from modules.run_artifacts import RunArtifacts

        artifacts = RunArtifacts.__new__(RunArtifacts)
        artifacts.run_dir = run_dir
        return artifacts._complexity_completeness_problems(
            run_dir / "run_status.json"
        )

    def test_an_untouched_run_passes(self):
        self.assertEqual(self._problems(self._copy()), [])

    def test_withholding_the_callable_artifact_is_refused(self):
        run_dir = self._copy()
        (run_dir / "callables.csv").unlink()
        shutil.rmtree(run_dir / "callables")
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("no callable artifact was published", " ".join(problems))

    def test_truncating_the_callable_artifact_is_refused(self):
        run_dir = self._copy()
        path = run_dir / "callables.csv"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(lines[:3]), encoding="utf-8", newline="")
        container = run_dir / "callables" / "container.json"
        document = json.loads(container.read_text(encoding="utf-8"))
        document["partitions"][0]["row_count"] = 2
        document["row_count"] = 2
        container.write_text(json.dumps(document), encoding="utf-8", newline="\n")
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("declare", " ".join(problems))

    def test_a_status_claiming_measured_without_evaluable_repositories_is_refused(self):
        run_dir = self._copy()
        analysis = run_dir / "analysis.json"
        document = json.loads(analysis.read_text(encoding="utf-8"))
        for repository in document:
            repository["metrics"]["complexity"]["status"] = "failed"
            repository["metrics"]["complexity"]["unavailable_reason"] = (
                "all_files_failed_parse"
            )
        analysis.write_text(json.dumps(document), encoding="utf-8", newline="\n")
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("complexity_measurement_state", " ".join(problems))

    def test_an_unavailable_state_without_a_typed_reason_is_refused(self):
        run_dir = self._copy()
        analysis = run_dir / "analysis.json"
        document = json.loads(analysis.read_text(encoding="utf-8"))
        for repository in document:
            repository["metrics"]["complexity"]["status"] = "failed"
            repository["metrics"]["complexity"]["unavailable_reason"] = None
        analysis.write_text(json.dumps(document), encoding="utf-8", newline="\n")
        problems = self._problems(run_dir)
        self.assertIn("unavailable_reason", " ".join(problems))

    def test_the_check_reads_bytes_not_producer_memory(self):
        """Mutating only the on-disk artifact must change the verdict.

        A check that consulted the results it was handed would still pass here,
        because those objects are untouched.
        """
        clean = self._copy()
        self.assertEqual(self._problems(clean), [])
        (clean / "callables.csv").unlink()
        shutil.rmtree(clean / "callables")
        self.assertTrue(self._problems(clean))


class SemanticProjectionTests(unittest.TestCase):
    """Projection 2.2.0: complexity is measurement semantics."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.run_dir = analyze_corpus(Path(cls._temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def _digest_after(self, mutate) -> str:
        """Copy the run, mutate analysis.json bytes, and re-hash."""
        from validation.scripts.semantic_projection import measurement_semantic_hash

        target = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        shutil.copytree(self.run_dir, target)
        analysis = target / "analysis.json"
        document = json.loads(analysis.read_text(encoding="utf-8"))
        mutate(document)
        analysis.write_text(json.dumps(document), encoding="utf-8", newline="\n")
        return measurement_semantic_hash(target)

    def test_a_complexity_value_difference_changes_the_measurement_hash(self):
        baseline = self._digest_after(lambda document: None)
        changed = self._digest_after(
            lambda document: document[0]["metrics"]["complexity"]["aggregate"].update(
                {"cyclomatic_complexity_total": 999999}
            )
        )
        self.assertNotEqual(
            baseline, changed,
            "a complexity difference must move the measurement semantic hash, "
            "or a complexity change could hash as no change at all",
        )

    def test_a_per_callable_difference_changes_the_measurement_hash(self):
        baseline = self._digest_after(lambda document: None)

        def bump(document):
            records = document[0]["metrics"]["callable_records"]
            records[0]["cyclomatic_complexity"] = (
                (records[0]["cyclomatic_complexity"] or 1) + 7
            )

        self.assertNotEqual(
            baseline, self._digest_after(bump),
            "per-callable values are projected, so a single row moving is visible "
            "even when every aggregate happens to stay equal",
        )

    def test_removing_complexity_entirely_changes_the_hash(self):
        """Absent and present-with-values are different measurements."""
        baseline = self._digest_after(lambda document: None)

        def strip(document):
            document[0]["metrics"].pop("complexity", None)
            document[0]["metrics"].pop("callable_records", None)

        self.assertNotEqual(baseline, self._digest_after(strip))

    def test_absent_is_not_equal_to_explicit_null(self):
        def strip(document):
            document[0]["metrics"].pop("complexity", None)
            document[0]["metrics"].pop("callable_records", None)

        def nulled(document):
            document[0]["metrics"]["complexity"] = None
            document[0]["metrics"]["callable_records"] = None

        self.assertNotEqual(
            self._digest_after(strip), self._digest_after(nulled),
            "absence must stay distinguishable from an explicit null",
        )

    def test_a_pre_1_9_projection_omits_the_keys_entirely(self):
        """The mechanical proof that historical digests do not move.

        A run recording neither key projects WITHOUT them, so its canonical
        serialization is byte-identical to what 2.1.0 produced.
        """
        from validation.scripts.semantic_projection import semantic_projection

        target = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        shutil.copytree(self.run_dir, target)
        analysis = target / "analysis.json"
        document = json.loads(analysis.read_text(encoding="utf-8"))
        for repository in document:
            repository["metrics"].pop("complexity", None)
            repository["metrics"].pop("callable_records", None)
        analysis.write_text(json.dumps(document), encoding="utf-8", newline="\n")

        projection = semantic_projection(target)
        repositories = projection["semantic"]["repositories"]
        self.assertTrue(repositories)
        for repository in repositories:
            metrics = repository.get("metrics", {})
            self.assertNotIn("complexity", metrics)
            self.assertNotIn("callable_records", metrics)

    def test_the_projection_version_is_the_declared_one(self):
        from validation.scripts.semantic_projection import SEMANTIC_PROJECTION_VERSION

        self.assertEqual(SEMANTIC_PROJECTION_VERSION, "2.3.0")

    def test_complexity_is_in_the_projected_metric_field_set(self):
        from validation.scripts.semantic_projection import METRIC_FIELDS

        self.assertIn("complexity", METRIC_FIELDS)
        self.assertIn("callable_records", METRIC_FIELDS)

    def test_absent_complexity_is_omitted_not_nulled(self):
        """A pre-1.9 run records neither key, so 2.2.0 leaves its digest alone."""
        from validation.scripts.semantic_projection import ABSENT_EQUALS_NULL

        self.assertEqual(
            tuple(ABSENT_EQUALS_NULL), (),
            "absence must stay distinguishable from an explicit null",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
