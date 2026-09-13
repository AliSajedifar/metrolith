"""Derived output schemas must describe the output they claim to describe.

``explain_output-1.0.schema.json`` was registered, published, and wrong: it
declared five properties while ``archlens explain --format json`` emitted
twelve, under ``additionalProperties: false``. Every real invocation violated
it. Nothing noticed because **nothing ever validated the output against it** —
the schema was a document, not a contract.

A registered schema that no test exercises is worse than no schema, because it
looks like a guarantee. So the end-to-end test here goes the whole way:

    run the real command -> parse the emitted JSON -> validate against the
    exact registered schema -> require zero violations

and the audit at the bottom reports which other derived schemas have such a
test and which are still only documents.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from validation.artifact_io.schema_store import (
    SCHEMA_REGISTRY,
    load_schema,
    schema_version,
    validate_document,
)

REPOSITORY = Path(__file__).resolve().parent.parent
ANALYZED_SHA = "a" * 40

#: Derived, explicitly non-authoritative output formats (plan section 3.3).
#: Derived formats advertised as CURRENT. `report_metadata` is deliberately
#: absent: it has no producer, so advertising it would be a false contract
#: (F-P3-7). Historical entries are excluded by the `_historical` suffix.
ACTIVE_DERIVED_SCHEMAS = (
    "explain_output",
    "compare_output",
    "reproduction_output",
    "performance_profile",
    # Direct Revision Diff. Its producer contract test lives in
    # tests/test_revision_diff.py, written in the same change that registered
    # the schema rather than left for later.
    "revision_diff_output",
    # Policy v1 evaluation result. Contract test in tests/test_policy_v1.py.
    "policy_result_output",
    # `archlens check` result. Contract test in tests/test_policy_v2_check.py,
    # written in the same change that registered the schema rather than left
    # for later -- `report_metadata` was registered with no producer at all and
    # `explain_output` drifted from its own schema unnoticed, both because
    # nothing exercised them.
    "check_result_output",
)


class RunFixture(unittest.TestCase):
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
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )

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
                analyzed_commit_sha=ANALYZED_SHA,
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

    def build_run(self) -> Path:
        config = AnalysisConfig.from_env(
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])


class ExplainOutputContractTests(RunFixture):
    def test_emitted_json_validates_against_the_registered_schema(self):
        """The whole point: real command, real bytes, registered schema."""
        run = self.build_run()
        result = subprocess.run(
            [sys.executable, "pipeline.py", "explain", str(run), "--format", "json"],
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIn(
            result.returncode, (0, 1, 2, 3),
            f"explain crashed: {result.stderr[-800:]}",
        )
        payload = json.loads(result.stdout)

        violations = validate_document("explain_output", payload, "explain.json")
        self.assertEqual(
            [f"{item.location}: {item.message}" for item in violations],
            [],
            "explain output violates its own registered schema",
        )

    def test_the_emitted_version_matches_the_registered_schema_version(self):
        from modules.cli.explain_command import EXPLANATION_FORMAT_VERSION

        self.assertEqual(EXPLANATION_FORMAT_VERSION, schema_version("explain_output"))
        self.assertEqual(
            load_schema("explain_output")["properties"]
            ["explanation_format_version"]["const"],
            EXPLANATION_FORMAT_VERSION,
        )

    def test_the_schema_still_forbids_undeclared_properties(self):
        """1.1 must not have been "fixed" by simply loosening the schema."""
        self.assertFalse(load_schema("explain_output").get("additionalProperties"))

    def test_an_undeclared_property_is_still_rejected(self):
        run = self.build_run()
        from modules.cli.explain_command import build_explanation
        from modules.diagnostics import project_run
        from validation.artifact_io.reader import open_run

        payload = dict(build_explanation(project_run(open_run(run))))
        payload["something_new"] = 1
        self.assertTrue(validate_document("explain_output", payload, "explain.json"))

    def test_historical_1_0_is_retained_unchanged(self):
        self.assertIn("explain_output_1_0_historical", SCHEMA_REGISTRY)
        self.assertEqual(schema_version("explain_output_1_0_historical"), "1.0.0")
        historical = load_schema("explain_output_1_0_historical")
        self.assertEqual(
            historical["properties"]["explanation_format_version"]["const"], "1.0.0"
        )

    def test_the_1_1_schema_covers_every_property_explain_emits(self):
        run = self.build_run()
        from modules.cli.explain_command import build_explanation
        from modules.diagnostics import project_run
        from validation.artifact_io.reader import open_run

        payload = build_explanation(project_run(open_run(run)))
        declared = set(load_schema("explain_output").get("properties", {}))
        self.assertEqual(sorted(set(payload) - declared), [])


class CompareOutputContractTests(RunFixture):
    def test_emitted_comparison_validates_against_its_schema(self):
        from modules.cli.compare_command import build_comparison
        from validation.artifact_io.reader import open_run

        left = self.build_run()
        # A second run of the same source: the ordinary comparison case.
        config = AnalysisConfig.from_env(
            output_root=self.root / "output_b",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            right = Path(run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )["run_directory"])

        payload = build_comparison(open_run(left), open_run(right))
        violations = validate_document("compare_output", payload, "compare.json")
        self.assertEqual(
            [f"{item.location}: {item.message}" for item in violations],
            [],
            "compare output violates its own registered schema",
        )

    def test_the_schema_still_forbids_undeclared_properties(self):
        # F-P3-8 was resolved by describing the output, not by loosening the
        # schema: `additionalProperties: false` must still hold.
        self.assertFalse(load_schema("compare_output").get("additionalProperties"))

    def test_the_emitted_version_matches_the_registered_schema(self):
        from modules.cli.compare_command import COMPARE_FORMAT_VERSION

        self.assertEqual(COMPARE_FORMAT_VERSION, schema_version("compare_output"))


class ReproductionOutputContractTests(RunFixture):
    def test_emitted_preflight_validates_against_its_schema(self):
        from modules.cli.reproduce_command import preflight
        from validation.artifact_io.reader import open_run

        payload = preflight(open_run(self.build_run()))
        violations = validate_document(
            "reproduction_output", payload, "reproduction.json"
        )
        self.assertEqual(
            [f"{item.location}: {item.message}" for item in violations],
            [],
            "reproduction output violates its own registered schema",
        )

    def test_the_execution_honesty_fields_survived(self):
        """They are current product behaviour and were not removed to conform."""
        declared = set(load_schema("reproduction_output").get("properties", {}))
        for field in (
            "execution_attempted", "execution_error", "network_used",
            "output_root_created", "reproduction_run_directory",
            "reproduction_status",
        ):
            self.assertIn(field, declared)

    def test_the_schema_still_forbids_undeclared_properties(self):
        self.assertFalse(load_schema("reproduction_output").get("additionalProperties"))

    def test_the_emitted_version_matches_the_registered_schema(self):
        from modules.cli.reproduce_command import REPRODUCTION_FORMAT_VERSION

        self.assertEqual(
            REPRODUCTION_FORMAT_VERSION, schema_version("reproduction_output")
        )


class PerformanceProfileContractTests(RunFixture):
    def test_emitted_profile_validates_against_its_schema(self):
        from modules.cli.performance_command import build_profile
        from validation.artifact_io.reader import open_run

        payload = build_profile(open_run(self.build_run()))
        violations = validate_document(
            "performance_profile", payload, "performance.json"
        )
        self.assertEqual(
            [f"{item.location}: {item.message}" for item in violations], []
        )


class ReportMetadataHasNoProducerTests(unittest.TestCase):
    """F-P3-7: `report_metadata` is a registered schema with no producer.

    `archlens report` writes an HTML file and prints a byte count. It never
    emits a `report_metadata` document, so there is nothing to hold to this
    contract — the schema describes an output that does not exist.

    This is deliberately **not** resolved by inventing a producer or by quietly
    deregistering the schema. Both are product decisions: one adds an unrequested
    output, the other withdraws a published contract. The gap is pinned here so
    it stays visible until it is decided.
    """

    def test_the_schema_is_registered_and_well_formed(self):
        schema = load_schema("report_metadata_historical")
        self.assertEqual(schema_version("report_metadata_historical"), "1.0.0")
        self.assertEqual(
            sorted(schema["required"]),
            ["generated_at", "output_path", "report_format_version", "run_id"],
        )

    def test_no_producer_emits_it(self):
        source = (
            REPOSITORY / "modules" / "cli" / "report_command.py"
        ).read_text(encoding="utf-8")
        for field in ("report_format_version", "report_metadata"):
            self.assertNotIn(
                field, source,
                "report_command now emits report metadata; give it a real "
                "producer-to-schema contract test and remove this finding",
            )


class ActiveDerivedRegistryInvariantTests(unittest.TestCase):
    """Every actively registered derived format must have a real contract test.

    The explain defect proved the failure mode: a registered schema that no test
    exercises is not a contract, it is a document that looks like one. The
    invariant below is mechanical, so a new derived format cannot be advertised
    without evidence that something actually produces conforming output.
    """

    #: Registered names that are historical or unimplemented, with the reason.
    #: A name may only be here if it is NOT advertised as a current format.
    NON_ACTIVE = {
        "explain_output_1_0_historical": "superseded by 1.1 (F-P3-2)",
        "explain_output_1_1_historical": (
            "superseded by 1.2, which adds the complexity-unavailable group "
            "and the three complexity fields per entry (C5)"
        ),
        "explain_output_1_2_historical": (
            "superseded by 1.3, which adds cognitive_state and "
            "cognitive_metric_name per entry (G2-C)"
        ),
        "revision_diff_output_1_0_historical": (
            "superseded by 1.1, which adds Complexity Contract 1.0.0 deltas "
            "under their own comparability verdict (C5)"
        ),
        "revision_diff_output_1_1_historical": (
            "superseded by 1.2, which adds the cognitive comparability verdict "
            "and its three delta levels (G2-C)"
        ),
        "check_result_output_1_0_historical": (
            "superseded by 1.1, which adds the `evidence` block and the "
            "`hotspot_file` finding scope (Hotspot H1). Retained because a "
            "1.0.0 result is still a valid document and is still selected for "
            "one by `schema_name_for_document`"
        ),
        "check_result_output_1_1_historical": (
            "superseded by 1.2, which adds the `duplication` evidence kind, "
            "the `duplication_group` finding scope and the "
            "`evidence_kind_not_requested` reason (Duplication DP1). Retained "
            "because a 1.1.0 result is still a valid document and is still "
            "selected for one by `schema_name_for_document`"
        ),
        "check_result_output_1_2_historical": (
            "superseded by 1.3, which adds the bounded Ratchet V1 summary and "
            "typed ratchet admission/evaluation failures (Baseline/Ratchet "
            "BR4). Retained because a 1.2.0 result remains a valid document "
            "selected by `schema_name_for_document`"
        ),
        "check_result_output_1_3_historical": (
            "superseded by 1.4, which adds bounded exact-input provenance, "
            "protected evidence trust state, and removes the machine-local "
            "run directory from canonical identity (Final Hardening Sprint "
            "2). Retained because a 1.3.0 result remains a valid document "
            "selected by `schema_name_for_document`"
        ),
        "compare_output_1_0_historical": "superseded by 1.1 (F-P3-8)",
        "reproduction_output_1_0_historical": "superseded by 1.1 (F-P3-9)",
        "report_metadata_historical": "no producer exists (F-P3-7)",
    }

    @staticmethod
    def _test_sources() -> str:
        """All test source, with whitespace collapsed.

        Collapsing matters: a `validate_document(` call split across lines is
        still a contract test, and an invariant that missed those would push
        people towards writing it on one long line to satisfy the checker.
        """
        raw = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPOSITORY / "tests").glob("test_*.py"))
        )
        return re.sub(r"validate_document\(\s+", "validate_document(", raw)

    def test_every_active_derived_format_has_a_producer_contract_test(self):
        sources = self._test_sources()
        for name in ACTIVE_DERIVED_SCHEMAS:
            with self.subTest(schema=name):
                self.assertIn(
                    f'validate_document("{name}"',
                    sources,
                    f"{name} is advertised as a current derived format but no "
                    f"test validates real producer output against its schema",
                )

    def test_active_and_non_active_together_cover_every_derived_registration(self):
        derived = {
            name for name in SCHEMA_REGISTRY
            if name.endswith(("_output", "_profile", "_metadata"))
            # Any superseded derived output, not only the 1.0 ones: C5 retired
            # explain_output 1.1 and revision_diff 1.0, and a predicate that
            # hard-codes one version silently stops covering the next.
            or ("_output_" in name and name.endswith("_historical"))
            or name == "report_metadata_historical"
        }
        self.assertEqual(
            derived,
            set(ACTIVE_DERIVED_SCHEMAS) | set(self.NON_ACTIVE),
            "a derived schema is registered but classified neither active nor "
            "historical; classify it before shipping it",
        )

    def test_report_metadata_is_not_advertised_as_current(self):
        """F-P3-7 resolved by withdrawing the false contract, not inventing one."""
        self.assertNotIn("report_metadata", SCHEMA_REGISTRY)
        self.assertIn("report_metadata_historical", SCHEMA_REGISTRY)
        self.assertNotIn("report_metadata", ACTIVE_DERIVED_SCHEMAS)

    def test_no_producer_was_invented_for_report_metadata(self):
        source = (
            REPOSITORY / "modules" / "cli" / "report_command.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("report_format_version", source)


if __name__ == "__main__":
    unittest.main()
