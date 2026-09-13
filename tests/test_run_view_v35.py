"""Phase 2 tests: compatibility-aware lazy reader and ImmutableRunView."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from validation.artifact_io.compatibility import RepositoryDocumentVariant, RunLifecycle
from validation.artifact_io.contracts import contract_for
from validation.artifact_io.errors import ArtifactStructureError, StructuralErrorCode
from validation.artifact_io.reader import (
    EAGER_TABLES,
    ImmutableRunView,
    StrictArtifactReader,
    open_run,
)

REPOSITORY = Path(__file__).resolve().parent.parent

from tests import historical_fixtures


def find_run(version):
    """The tracked historical fixture for `version`, or a failure.

    Resolved from the fixture manifest instead of scanning the tree, so the
    Artifact 1.3 fixture can no longer be satisfied by gitignored local output
    that a fresh clone does not have.
    """
    return historical_fixtures.run_for(version)


class RequiresRun(unittest.TestCase):
    VERSION = "1.4.0"

    def setUp(self):
        self.run = find_run(self.VERSION)

    def copy_run(self):
        """Copy the fixture so a test may mutate it without touching evidence."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "run"
        shutil.copytree(self.run, target)
        return target


class RunViewBasicsTests(RequiresRun):
    def test_finalized_valid_run_is_readable(self):
        view = open_run(self.run)
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertTrue(view.finalized)
        self.assertTrue(view.compatibility.readable)
        self.assertEqual(view.structural_errors, ())

    def test_authoritative_results_come_from_analysis(self):
        view = open_run(self.run)
        self.assertGreater(len(view.repositories), 0)
        self.assertEqual(len(view.repositories_by_url), len(view.repositories))

    def test_repository_lookup_is_key_aligned_by_canonical_url(self):
        view = open_run(self.run)
        url = next(iter(view.repositories_by_url))
        self.assertIsNotNone(view.repository(url))
        self.assertIsNone(view.repository("https://example.invalid/absent"))

    def test_optional_repository_projection_reconciles(self):
        view = open_run(self.run)
        self.assertEqual([str(item) for item in view.check_repository_projection()], [])

    def test_repository_documents_are_final_result_variant(self):
        view = open_run(self.run)
        for relative in view.repository_documents:
            with self.subTest(document=relative):
                self.assertEqual(
                    view.repository_document_variant(relative),
                    RepositoryDocumentVariant.FINAL_RESULT,
                )


class LazyLoadingTests(RequiresRun):
    def test_catalog_is_not_eagerly_loaded(self):
        # catalog.csv is one row per source file; treating it as small would
        # make opening a cohort run unaffordable (plan section 8.4).
        self.assertNotIn("catalog.csv", EAGER_TABLES)
        view = open_run(self.run)
        self.assertNotIn("catalog.csv", view.reader._tables)

    def test_eager_tables_are_present_after_open(self):
        view = open_run(self.run)
        for relative in EAGER_TABLES:
            with self.subTest(table=relative):
                self.assertIn(relative, view.reader._tables)

    def test_inventories_load_only_on_request(self):
        view = open_run(self.run)
        loaded_before = [k for k in view.reader._documents if k.startswith("file_inventory/")]
        self.assertEqual(loaded_before, [])
        self.assertGreater(len(view.inventories), 0)

    def test_catalog_loads_on_request(self):
        view = open_run(self.run)
        self.assertGreater(len(view.catalog), 0)
        self.assertIn("catalog.csv", view.reader._tables)


class MissingProjectionTests(RequiresRun):
    """Missing optional projections warn; they never crash and never invalidate."""

    def test_missing_repository_projection_degrades_gracefully(self):
        run = self.copy_run()
        shutil.rmtree(run / "repositories")
        view = open_run(run)
        self.assertEqual(view.repository_documents, {})
        self.assertTrue(any("repositories/" in w.artifact for w in view.warnings))
        # Authoritative results are unaffected.
        self.assertGreater(len(view.repositories), 0)
        self.assertEqual(view.structural_errors, ())

    def test_missing_inventory_leaves_aggregates_readable(self):
        run = self.copy_run()
        shutil.rmtree(run / "file_inventory")
        view = open_run(run)
        self.assertFalse(view.has_inventory)
        self.assertTrue(any("file_inventory/" in w.artifact for w in view.warnings))
        self.assertGreater(len(view.repositories), 0)

    def test_absent_normalized_input_is_none_not_empty(self):
        # None means "not evaluable"; an empty tuple would mean "no rows", which
        # is a different and false claim (plan section 3.7).
        view = open_run(self.run)
        self.assertIsNone(view.normalized_input)
        self.assertTrue(any("normalized_input" in w.artifact for w in view.warnings))

    def test_absent_contribution_ledger_is_reported_as_absent(self):
        view = open_run(self.run)
        self.assertFalse(view.has_contribution_ledger)
        self.assertEqual(list(view.stream_contributions()), [])


class LatestRunPointerTests(RequiresRun):
    def test_reader_refuses_latest_run_json(self):
        reader = StrictArtifactReader(self.run)
        with self.assertRaises(ArtifactStructureError) as caught:
            reader.document("latest_run.json")
        self.assertEqual(
            caught.exception.code, StructuralErrorCode.ARTIFACT_NOT_ALLOWLISTED
        )

    def test_reader_refuses_upward_traversal(self):
        reader = StrictArtifactReader(self.run)
        with self.assertRaises(ArtifactStructureError) as caught:
            reader.document("../latest_run.json")
        self.assertEqual(
            caught.exception.code, StructuralErrorCode.PATH_ESCAPES_RUN_DIRECTORY
        )


class CorruptAndUnsupportedTests(RequiresRun):
    def _rewrite_manifest(self, run, **changes):
        path = run / "run_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(changes)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_undeclared_version_yields_undeclared_lifecycle(self):
        run = self.copy_run()
        path = run / "run_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("artifact_schema_version", None)
        path.write_text(json.dumps(payload), encoding="utf-8")
        view = open_run(run)
        self.assertEqual(view.lifecycle, RunLifecycle.UNDECLARED_VERSION)
        self.assertFalse(view.compatibility.readable)

    def test_unparseable_version_yields_corrupt(self):
        run = self.copy_run()
        self._rewrite_manifest(run, artifact_schema_version="not-a-version")
        self.assertEqual(open_run(run).lifecycle, RunLifecycle.CORRUPT)

    def test_future_version_yields_unsupported(self):
        run = self.copy_run()
        self._rewrite_manifest(run, artifact_schema_version="9.9.0")
        view = open_run(run)
        self.assertEqual(view.lifecycle, RunLifecycle.UNSUPPORTED_VERSION)
        self.assertIn("newer than the native", view.compatibility.reason)

    def test_running_status_forbids_a_finalized_verdict(self):
        run = self.copy_run()
        path = run / "run_status.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "running"
        path.write_text(json.dumps(payload), encoding="utf-8")
        view = open_run(run)
        self.assertEqual(view.lifecycle, RunLifecycle.RUNNING)
        self.assertFalse(view.finalized)

    def test_running_run_selects_the_checkpoint_variant(self):
        run = self.copy_run()
        path = run / "run_status.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "running"
        path.write_text(json.dumps(payload), encoding="utf-8")
        view = open_run(run)
        for relative in view.repository_documents:
            with self.subTest(document=relative):
                self.assertEqual(
                    view.repository_document_variant(relative),
                    RepositoryDocumentVariant.CHECKPOINT,
                )


class ProjectionDisagreementTests(RequiresRun):
    def test_tampered_repository_document_surfaces_as_non_reconciling(self):
        run = self.copy_run()
        target = sorted((run / "repositories").glob("*.json"))[0]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["analysis_status"] = "tampered_value"
        target.write_text(json.dumps(payload), encoding="utf-8")

        problems = open_run(run).check_repository_projection()
        self.assertEqual(len(problems), 1)
        self.assertEqual(
            problems[0].code,
            StructuralErrorCode.TERMINAL_DOCUMENT_DOES_NOT_RECONCILE,
        )
        self.assertIn("analysis_status", problems[0].message)


class VersionAwareContractTests(unittest.TestCase):
    """Column sets grew across artifact versions; required-ness must follow."""

    def test_columns_introduced_later_are_not_required_of_older_artifacts(self):
        native = contract_for("errors.csv", "1.5.0")
        legacy = contract_for("errors.csv", "1.3.0")
        self.assertGreater(
            len(native.required_names), len(legacy.required_names),
            "an Artifact 1.3 errors.csv carries fewer columns than 1.4 and must "
            "not be held to the later column set",
        )

    def test_a_1_4_column_is_required_at_1_5_but_not_at_1_3(self):
        # alternating_nul_evidence first appears at Artifact 1.4.
        native = set(contract_for("errors.csv", "1.5.0").required_names)
        legacy = set(contract_for("errors.csv", "1.3.0").required_names)
        self.assertIn("alternating_nul_evidence", native)
        self.assertNotIn("alternating_nul_evidence", legacy)

    def test_omitting_the_version_keeps_every_column_required(self):
        """An absent version relaxes nothing: it reads as the native contract.

        The native version is read from `compatibility` rather than written as a
        literal, because the property under test is "omitting the version is the
        strict reading", not "the strict reading happens to be 1.5". Pinning the
        literal made this fail at every artifact bump for no product reason.
        """
        from validation.artifact_io.compatibility import NATIVE_ARTIFACT_SCHEMA

        native_version = ".".join(str(part) for part in NATIVE_ARTIFACT_SCHEMA)
        strict = contract_for("errors.csv", None)
        native = contract_for("errors.csv", native_version)
        self.assertEqual(set(strict.required_names), set(native.required_names))

    def test_legacy_opt_in_is_limited_to_the_four_allowlisted_columns(self):
        opted = {
            column.name
            for artifact in ("errors.csv", "recoveries.csv", "catalog.csv")
            for column in contract_for(artifact, "1.3.0").columns
            if column.legacy_python_repr_until is not None
        }
        self.assertEqual(
            opted, {"affected_metrics", "fallback_strategies", "selected_fallback_strategies"}
        )

    def test_contract_columns_match_the_packaged_schema(self):
        from validation.artifact_io.schema_store import load_schema

        for artifact, (schema_name, _keys) in _known_artifacts():
            with self.subTest(artifact=artifact):
                schema = load_schema(schema_name)
                self.assertEqual(
                    set(contract_for(artifact).field_names),
                    set(schema.get("properties", {})),
                )


def _known_artifacts():
    from validation.artifact_io.contracts import TABULAR_ARTIFACTS

    return sorted(TABULAR_ARTIFACTS.items())


class LegacyRunReadTests(RequiresRun):
    """A preserved Artifact 1.3 run must read end to end through the view."""

    VERSION = "1.3.0"

    def test_legacy_run_reads_and_announces_recoveries(self):
        view = open_run(self.run)
        self.assertTrue(view.compatibility.readable)
        rows = view.errors
        self.assertGreater(len(rows), 0)
        recoveries = view.legacy_recoveries
        self.assertGreater(len(recoveries), 0)
        for recovery in recoveries:
            self.assertIsInstance(recovery.decoded, list)
            self.assertTrue(all(isinstance(item, str) for item in recovery.decoded))
            self.assertEqual(recovery.declared_artifact_schema_version, "1.3.0")

    def test_legacy_recoveries_are_visible_in_the_compatibility_report(self):
        view = open_run(self.run)
        _ = view.errors
        self.assertGreater(view.compatibility_report()["legacy_cell_recoveries"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
