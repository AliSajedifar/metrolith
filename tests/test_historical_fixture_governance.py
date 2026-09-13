"""Governance for the tracked historical-Artifact fixtures.

The defect this guards against is specific and was live: historical
compatibility for Artifact 1.3 was proved against run directories that only
existed on one workstation. Every 1.3 run in the tree was gitignored, so a
fresh clone found none, the consuming tests called `skipTest`, and the suite
reported a pass. Coverage that disappears at clone time is worse than absent
coverage, because absent coverage is visible.

So these tests assert the two things that make the fixtures trustworthy:

1. every mandatory fixture the manifest declares is actually present, and
2. each one declares the Artifact generation it claims â€” presence alone would
   be satisfied by copying the wrong generation into the right directory name.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tests import historical_fixtures
from tests.historical_fixtures import HistoricalFixtureError
from validation.artifact_io.compatibility import (
    SUPPORTED_ARTIFACT_SCHEMAS,
    CompatibilityState,
    classify_artifact_schema,
    parse_version,
)

REPOSITORY = Path(__file__).resolve().parent.parent

#: Generations whose adapter is part of the CURRENT supported compatibility
#: contract *and* is exercised by tests, so a tracked fixture is mandatory.
#: 1.0-1.2 are deliberately absent: no test locates a fixture for them, and
#: inventing one would be new coverage rather than hardening.
MANDATORY_GENERATIONS = ("1.3.0", "1.4.0", "1.5.0")


class FixtureManifestTests(unittest.TestCase):
    def test_the_manifest_is_present_and_declares_fixtures(self):
        records = historical_fixtures.records()
        self.assertTrue(records, "the fixture manifest declares no fixtures")
        for record in records:
            with self.subTest(fixture=record["fixture_id"]):
                for field in (
                    "fixture_id",
                    "artifact_generation",
                    "role",
                    "purpose",
                    "mandatory",
                    "representation",
                    "fixture_manifest_sha256",
                    "fixture_tree_hash_algorithm",
                    "fixture_path",
                    "fixture_tree_sha256",
                    "fixture_size_bytes",
                    "retained_files",
                    "omitted_files",
                ):
                    self.assertIn(field, record, f"{field} is not recorded")

    def test_every_mandatory_fixture_path_exists(self):
        """A declared-but-absent fixture is a FAILURE, never a skip."""
        for record in historical_fixtures.records():
            if not record.get("mandatory"):
                continue
            with self.subTest(fixture=record["fixture_id"]):
                path = REPOSITORY / record["fixture_path"]
                self.assertTrue(
                    path.is_dir(),
                    f"mandatory fixture {record['fixture_id']!r} is missing at "
                    f"{record['fixture_path']}",
                )
                self.assertTrue((path / "run_manifest.json").is_file())

    def test_each_fixture_declares_the_generation_it_claims(self):
        """Renaming or copying the wrong generation cannot satisfy presence."""
        for record in historical_fixtures.records():
            with self.subTest(fixture=record["fixture_id"]):
                manifest = json.loads(
                    (REPOSITORY / record["fixture_path"] / "run_manifest.json")
                    .read_text(encoding="utf-8")
                )
                self.assertEqual(
                    manifest.get("artifact_schema_version"),
                    record["artifact_generation"],
                )

    def test_every_mandatory_generation_has_a_primary_fixture(self):
        for generation in MANDATORY_GENERATIONS:
            with self.subTest(generation=generation):
                self.assertTrue(historical_fixtures.run_for(generation).is_dir())

    def test_mandatory_generations_are_supported_and_non_native(self):
        """A fixture only earns its place if it exercises a real adapter."""
        for generation in MANDATORY_GENERATIONS:
            with self.subTest(generation=generation):
                verdict = classify_artifact_schema(generation)
                self.assertIs(verdict.state, CompatibilityState.SUPPORTED)
                parsed = parse_version(generation)
                entry = SUPPORTED_ARTIFACT_SCHEMAS[(parsed[0], parsed[1])]
                self.assertEqual(entry["support"], "adapter")

    def test_fixtures_do_not_resolve_into_gitignored_evidence(self):
        """Fixtures must live under tests/, not in scanned run output.

        The whole failure mode was fixtures being satisfied from directories a
        clone does not have, so their location is part of the contract.
        """
        for record in historical_fixtures.records():
            with self.subTest(fixture=record["fixture_id"]):
                self.assertTrue(
                    record["fixture_path"].startswith("tests/fixtures/historical/"),
                    f"{record['fixture_id']} resolves outside the tracked "
                    f"fixture directory: {record['fixture_path']}",
                )

    def test_a_missing_fixture_raises_rather_than_skipping(self):
        """The guard itself must fail. A guard that cannot fail proves nothing.

        `HistoricalFixtureError` subclasses `AssertionError`, so unittest
        reports it as a failure. `SkipTest` must never be reachable from a
        missing fixture.
        """
        with self.assertRaises(HistoricalFixtureError):
            historical_fixtures.run_for("9.9.9")
        with self.assertRaises(HistoricalFixtureError):
            historical_fixtures.fixture("no-such-fixture")
        self.assertTrue(issubclass(HistoricalFixtureError, AssertionError))
        self.assertFalse(issubclass(HistoricalFixtureError, unittest.SkipTest))

    def test_mandatory_run_consumers_do_not_fall_back_to_ignored_output(self):
        """Clean-checkout release coverage must use the tracked run authority."""
        for relative in (
            "tests/test_diagnostic_consistency_v35.py",
            "tests/test_hypothesis_triage_v35.py",
        ):
            with self.subTest(file=relative):
                source = (REPOSITORY / relative).read_text(encoding="utf-8")
                self.assertIn("historical_fixtures.run_for", source)
                self.assertNotIn("archlens-output", source)
                self.assertNotIn("no reference run directory", source)


class FixtureProvenanceTests(unittest.TestCase):
    def test_derived_fixture_byte_identities_are_exact(self):
        for record in historical_fixtures.records():
            with self.subTest(fixture=record["fixture_id"]):
                root = REPOSITORY / record["fixture_path"]
                digest = hashlib.sha256()
                size = 0
                for name in sorted(record["retained_files"]):
                    raw = (root / name).read_bytes()
                    size += len(raw)
                    digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(raw).digest())
                self.assertEqual(digest.hexdigest(), record["fixture_tree_sha256"])
                self.assertEqual(size, record["fixture_size_bytes"])
                self.assertEqual(hashlib.sha256((root / "run_manifest.json").read_bytes()).hexdigest(), record["fixture_manifest_sha256"])
                self.assertEqual(record["representation"], "privacy-normalized synthetic compatibility fixture")

    def test_retained_and_omitted_files_are_disjoint_and_accounted(self):
        for record in historical_fixtures.records():
            with self.subTest(fixture=record["fixture_id"]):
                retained = set(record["retained_files"])
                omitted = set(record["omitted_files"])
                self.assertEqual(retained & omitted, set())
                present = {
                    path.relative_to(REPOSITORY / record["fixture_path"]).as_posix()
                    for path in (REPOSITORY / record["fixture_path"]).rglob("*")
                    if path.is_file()
                }
                self.assertEqual(
                    present,
                    retained,
                    "the fixture on disk does not match its declared retained set",
                )

    def test_no_omitted_file_is_an_authoritative_run_document(self):
        """Only per-subject optional projections may be omitted.

        Dropping a terminal document, `analysis.json`, an inventory or a CSV
        projection would change what the fixture proves rather than shrink it.
        """
        protected = {
            "run_manifest.json",
            "run_status.json",
            "environment.json",
            "analysis.json",
            "catalog.csv",
            "sheet_metrics.csv",
            "language_metrics.csv",
            "errors.csv",
            "recoveries.csv",
            "normalized_input.csv",
            "repositories_frozen.csv",
            "retry_failed_or_partial.csv",
            "summary.md",
        }
        for record in historical_fixtures.records():
            for omitted in record["omitted_files"]:
                with self.subTest(fixture=record["fixture_id"], omitted=omitted):
                    self.assertNotIn(omitted, protected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
