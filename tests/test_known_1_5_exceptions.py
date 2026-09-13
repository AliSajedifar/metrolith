"""Known 1.5.0 schema defects: one registry, exactly scoped, never called valid.

Artifact Schema 1.5.0 is frozen and contains schemas that contradict their own
producers. Each waiver here is a bridge, not a solution, and each is dangerous
in the same way: a waiver that is one character too broad silently stops
catching real defects.

So most of this file is negative. For every violation that *is* waived there are
several near-misses that must not be — a different value, a different keyword, a
different pointer, a different schema, a different artifact version.

The other half of the point is agreement. Before the registry existed,
finalization waived F-P3-4 and published a run as ``completed_with_errors``
while ``archlens validate --schema-only`` failed that same run on exactly the
violation finalization had waived.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from validation.artifact_io import known_exceptions
from validation.artifact_io.errors import StructuralError, StructuralErrorCode


def error(location: str, keyword: str, message: str = "violation") -> StructuralError:
    return StructuralError(
        code=StructuralErrorCode.SCHEMA_VIOLATION,
        artifact="artifact.json",
        message=message,
        location=location,
        detail={"keyword": keyword},
    )


def waived(schema: str, err: StructuralError, document) -> bool:
    real, accepted = known_exceptions.partition(
        schema, [err], document, declared_artifact_schema="1.5.0"
    )
    return not real and len(accepted) == 1


class WaivedCasesTests(unittest.TestCase):
    """Exactly the three documented defects, and only in their exact shape."""

    def test_f_p3_3_output_failures_item_type(self):
        self.assertTrue(waived(
            "run_manifest", error("/output_failures/mandatory/0", "type"), {}
        ))
        self.assertTrue(waived(
            "run_manifest", error("/output_failures/optional/2", "type"), {}
        ))

    def test_f_p3_4_measurement_outcome_failed(self):
        self.assertTrue(waived(
            "run_status", error("/measurement_outcome", "enum"),
            {"measurement_outcome": "failed"},
        ))

    def test_f_p3_5_input_line_sentinel(self):
        self.assertTrue(waived(
            "repository_document", error("/input_line", "minimum"), {"input_line": 0}
        ))
        self.assertTrue(waived(
            "analysis", error("/0/input_line", "minimum"), [{"input_line": 0}]
        ))
        self.assertTrue(waived(
            "analysis", error("/7/input_line", "minimum"),
            [{}] * 7 + [{"input_line": 0}],
        ))


class NotWaivedTests(unittest.TestCase):
    """Near-misses. Each of these is a real defect and must survive."""

    def test_a_different_measurement_outcome_value_is_not_waived(self):
        for value in ("nonsense", "unavailable_typo", None, "", "FAILED"):
            with self.subTest(value=value):
                self.assertFalse(waived(
                    "run_status", error("/measurement_outcome", "enum"),
                    {"measurement_outcome": value},
                ))

    def test_a_different_input_line_value_is_not_waived(self):
        # 0 is the documented sentinel. -1 and 0.5 are simply wrong.
        for value in (-1, -20, 0.5, "0", None, True):
            with self.subTest(value=value):
                self.assertFalse(waived(
                    "repository_document", error("/input_line", "minimum"),
                    {"input_line": value},
                ))

    def test_a_different_keyword_at_the_same_pointer_is_not_waived(self):
        self.assertFalse(waived(
            "run_status", error("/measurement_outcome", "type"),
            {"measurement_outcome": "failed"},
        ))
        self.assertFalse(waived(
            "repository_document", error("/input_line", "type"), {"input_line": 0}
        ))
        self.assertFalse(waived(
            "run_manifest", error("/output_failures/mandatory/0", "required"), {}
        ))

    def test_a_different_pointer_is_not_waived(self):
        self.assertFalse(waived(
            "run_status", error("/status", "enum"), {"status": "nonsense"}
        ))
        self.assertFalse(waived(
            "run_manifest", error("/run_id", "type"), {}
        ))
        self.assertFalse(waived(
            "repository_document", error("/input_file", "minimum"), {"input_line": 0}
        ))

    def test_a_different_schema_is_not_waived(self):
        # The same shape of violation in another document is still a defect.
        self.assertFalse(waived(
            "analysis", error("/output_failures/mandatory/0", "type"), {}
        ))
        self.assertFalse(waived(
            "run_manifest", error("/measurement_outcome", "enum"),
            {"measurement_outcome": "failed"},
        ))
        self.assertFalse(waived(
            "catalog_row", error("/input_line", "minimum"), {"input_line": 0}
        ))

    def test_a_missing_value_is_not_waived(self):
        self.assertFalse(waived(
            "repository_document", error("/input_line", "minimum"), {}
        ))


class VersionScopeTests(unittest.TestCase):
    """The waivers are 1.5-only, so they die automatically at 1.6.0."""

    def _partition(self, declared):
        return known_exceptions.partition(
            "run_status",
            [error("/measurement_outcome", "enum")],
            {"measurement_outcome": "failed"},
            declared_artifact_schema=declared,
        )

    def test_waived_only_for_exactly_1_5_0(self):
        real, accepted = self._partition("1.5.0")
        self.assertEqual(real, [])
        self.assertEqual(len(accepted), 1)

    def test_not_waived_for_other_versions(self):
        # 1.5.1 and 1.5.7 are not versions whose defects were ever reviewed:
        # Artifact Schema versions carry a zero patch component by contract.
        for declared in ("1.5.1", "1.5.7", "1.6.0", "1.4.0", "2.0.0", "nonsense", None, ""):
            with self.subTest(declared=declared):
                real, accepted = self._partition(declared)
                self.assertEqual(len(real), 1, f"{declared} must not be waived")
                self.assertEqual(accepted, [])

    def test_waiving_can_be_disabled_entirely(self):
        real, accepted = known_exceptions.partition(
            "run_status",
            [error("/measurement_outcome", "enum")],
            {"measurement_outcome": "failed"},
            declared_artifact_schema="1.5.0",
            enabled=False,
        )
        self.assertEqual(len(real), 1)
        self.assertEqual(accepted, [])


class ReportingTests(unittest.TestCase):
    def test_an_accepted_entry_never_claims_validity(self):
        _real, accepted = known_exceptions.partition(
            "run_status",
            [error("/measurement_outcome", "enum")],
            {"measurement_outcome": "failed"},
            declared_artifact_schema="1.5.0",
        )
        record = accepted[0].as_dict()
        self.assertEqual(
            record["status"], "accepted_under_known_1_5_compatibility_exception"
        )
        self.assertEqual(record["issue_id"], "F-P3-4")
        self.assertNotIn("valid", record["status"].replace("compatibility", ""))

    def test_every_exception_carries_an_issue_id_and_a_resolution(self):
        described = known_exceptions.describe()
        self.assertTrue(described)
        for item in described:
            with self.subTest(issue=item["issue_id"]):
                self.assertRegex(item["issue_id"], r"^F-P3-\d+$")
                self.assertTrue(item["summary"].strip())
                self.assertTrue(item["resolution"].strip())
                self.assertIn("1.6.0", item["resolution"])

    def test_the_registry_covers_exactly_the_known_issues(self):
        self.assertEqual(
            known_exceptions.issue_ids(),
            ("F-P3-10", "F-P3-3", "F-P3-4", "F-P3-5", "F-P3-6"),
        )


class FinalizationAndValidateAgreeTests(unittest.TestCase):
    """One artifact, two commands, one verdict."""

    def _failed_run(self) -> Path:
        from modules.acquisition import AcquisitionError
        from modules.benchmark_runner import run_benchmark
        from modules.config import AnalysisConfig
        from modules.repository_input import RepositorySpec

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = AnalysisConfig.from_env(
            workspace=root,
            output_root=root / "output",
            cache_root=root / "cache",
            temporary_directory=root / "temp",
        )
        spec = RepositorySpec(
            "https://github.com/acme/dirty", "monolith", "Python", "a" * 40
        )

        @contextmanager
        def fail_acquisition(*args, **kwargs):
            del args, kwargs
            raise AcquisitionError("checkout_not_clean", "simulated")
            yield  # pragma: no cover

        with (
            patch("modules.benchmark_runner.acquire_repository", fail_acquisition),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                repository_specs=[spec],
                config=config,
                acquisition_mode="offline",
                command_line_arguments=["test"],
            )
        return Path(summary["run_directory"])

    def test_a_run_finalization_published_also_passes_validate(self):
        from modules.cli.validate_command import schema_only_report

        run = self._failed_run()
        status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
        self.assertNotEqual(status["status"], "running", "run was never published")

        report = schema_only_report(run)
        self.assertTrue(
            report["passed"],
            f"validate rejected a run finalization published: {report['violations']}",
        )

    def test_a_new_run_declares_the_corrected_schema_and_needs_no_waiver(self):
        from modules.cli.validate_command import schema_only_report

        run = self._failed_run()
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        from modules.config import ARTIFACT_SCHEMA_VERSION
        self.assertEqual(manifest["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)

        report = schema_only_report(run)
        self.assertTrue(report["passed"], report["violations"])
        self.assertEqual(report["result"], "schema_valid")
        self.assertEqual(report["accepted_compatibility_exceptions"], [])

    def test_a_1_6_artifact_may_never_claim_a_1_5_waiver(self):
        """The hard rule. 1.6 corrects these defects, so needing one is a bug."""
        from validation.artifact_io.known_exceptions import (
            CompatibilityExceptionMisuse,
            refuse_if_corrected,
        )

        _real, accepted = known_exceptions.partition(
            "run_status",
            [error("/measurement_outcome", "enum")],
            {"measurement_outcome": "failed"},
            declared_artifact_schema="1.5.0",
        )
        self.assertEqual(len(accepted), 1)

        # Same waiver, claimed by a corrected artifact version: refused.
        for declared in ("1.6.0", "1.7.0", "2.0.0"):
            with self.subTest(declared=declared):
                with self.assertRaises(CompatibilityExceptionMisuse):
                    refuse_if_corrected(declared, accepted)

        # Historical versions may still rely on them.
        refuse_if_corrected("1.5.0", accepted)
        refuse_if_corrected("1.4.0", accepted)
        # And no waivers at all is always fine.
        refuse_if_corrected("1.6.0", [])

    def test_a_clean_run_validates(self):
        """A good run must pass validate, waivers or not."""
        from modules.benchmark_runner import run_benchmark
        from modules.cli.validate_command import schema_only_report
        from modules.config import AnalysisConfig
        from modules.acquisition import AcquiredRepository, AcquisitionRecord

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "fixture"
        repo.mkdir()
        (repo / "app.py").write_text("class A:\n    def b(self):\n        return 1\n", encoding="utf-8")
        source = root / "repositories.csv"
        source.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{'a' * 40},true,one\n",
            encoding="utf-8",
        )

        @contextmanager
        def acquire(spec, config, mode="latest", progress=None):
            del config, mode, progress
            yield AcquiredRepository(repo, AcquisitionRecord(
                repository_url=spec.url, repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha, analyzed_commit_sha="a" * 40,
                resolved_ref="refs/heads/main", default_branch="main",
                acquisition_mode="offline", cache_status="reused",
                remote_checked=False, fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified", fetch_method="offline_cache",
            ))

        config = AnalysisConfig.from_env(
            output_root=root / "output", cache_root=root / "cache",
            temporary_directory=root / "temp", workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                [source], config, "offline", command_line_arguments=["test"]
            )

        report = schema_only_report(Path(summary["run_directory"]))
        self.assertTrue(report["passed"])
        # F-P3-6 applies here: the synthetic fixture is not a Git checkout, so
        # every file legitimately has git_mode null.
        for record in report["accepted_compatibility_exceptions"]:
            self.assertEqual(record["issue_id"], "F-P3-6")


if __name__ == "__main__":
    unittest.main()


class HistoricalSchemaValiditySemanticsTests(unittest.TestCase):
    """Literal validity is judged against the contract the artifact declares.

    A 1.5.0 artifact carrying a known 1.5 defect satisfies the *relaxed* 1.6
    schema byte-for-byte. Validating it against 1.6 would therefore report
    `schema_valid` and quietly erase the fact that it violates its own published
    contract — turning a corrective schema release into a retroactive rewrite of
    history.

    "Readable by the current reader" is a real and useful fact, but it is a
    different question, so it is reported as its own field rather than allowed
    to redefine this one.
    """

    HISTORICAL_1_5 = (
        Path(__file__).resolve().parent.parent
        / "tests" / "fixtures" / "historical"
        / "artifact-1.5.0-known-exceptions"
    )

    def setUp(self):
        if not (self.HISTORICAL_1_5 / "run_manifest.json").is_file():
            self.fail(
                f"the preserved 1.5.0 artifact is missing: {self.HISTORICAL_1_5}. "
                f"This test needs a real historical artifact; skipping it would "
                f"remove the only check that history is not rewritten."
            )

    def test_a_1_5_artifact_is_judged_against_its_own_declared_contract(self):
        from modules.cli.validate_command import schema_only_report

        report = schema_only_report(self.HISTORICAL_1_5)
        self.assertEqual(report["schema_contract_evaluated"], "1.5.0")
        self.assertEqual(
            report["result"], "accepted_under_known_1_5_compatibility_exception"
        )
        self.assertNotEqual(
            report["result"], "schema_valid",
            "a defective 1.5.0 artifact was reported valid because the newer "
            "relaxed schema happens to accept it",
        )
        self.assertTrue(report["passed"], "acceptance still shares a success exit")

    def test_the_waivers_are_the_known_findings(self):
        from modules.cli.validate_command import schema_only_report

        report = schema_only_report(self.HISTORICAL_1_5)
        found = {item["issue_id"] for item in report["accepted_compatibility_exceptions"]}
        self.assertTrue(found)
        self.assertTrue(found <= set(known_exceptions.issue_ids()), found)

    def test_current_schema_satisfaction_is_a_separate_fact(self):
        from modules.cli.validate_command import schema_only_report

        report = schema_only_report(self.HISTORICAL_1_5)
        # Valid under its own declared contract...
        self.assertEqual(
            report["result"], "accepted_under_known_1_5_compatibility_exception"
        )
        # ...and simultaneously not acceptable to the *current* schema, because
        # Artifact 1.7 added required subject-identity fields this artifact
        # predates. Both facts are true; neither may imply the other.
        self.assertFalse(report["satisfies_current_schema"])

    def test_the_declared_contract_is_what_is_evaluated(self):
        """Proves the distinction is real, not an artefact of a broken lookup.

        The 1.5 corrections were relaxations, so these bytes passed the 1.6
        schema. Artifact 1.7 is *restrictive* — it requires `subject_key` and
        `source_mode` — so the same bytes now fail the current schema while
        still being judged, correctly, against the 1.5 contract they declare.
        """
        import json

        from validation.artifact_io.schema_store import validate_document

        document = json.loads(
            next((self.HISTORICAL_1_5 / "repositories").glob("*.json"))
            .read_text(encoding="utf-8")
        )
        self.assertTrue(
            validate_document("repository_document", document, "r.json"),
            "Artifact 1.7 requires subject identity fields a 1.5 artifact "
            "cannot have, so the current schema must reject these bytes",
        )
        self.assertTrue(
            validate_document(
                "repository_document_1_5_historical", document, "r.json"
            ),
            "the frozen 1.5 schema should still reject these bytes",
        )
