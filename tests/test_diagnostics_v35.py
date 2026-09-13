"""Phase 4 tests: factual diagnostic projection (plan section 10)."""

import json
import unittest
from pathlib import Path

from modules.diagnostics import (
    PATHLESS_MODULES,
    EvidenceScope,
    ExplanationCompleteness,
    MetricEffect,
    project_repository,
    project_run,
)
from validation.artifact_io.reader import open_run

REPOSITORY = Path(__file__).resolve().parent.parent

from tests import historical_fixtures


def find_run(version="1.4.0"):
    """The tracked historical fixture for `version`.

    Resolved from the fixture manifest rather than by scanning the tree. The
    scan it replaces could satisfy itself from gitignored local output, so a
    fresh clone found nothing and the caller skipped -- silently reporting no
    historical coverage as a pass. A missing fixture now raises.
    """
    return historical_fixtures.run_for(version)


class ProjectionCopiesRatherThanDerivesTests(unittest.TestCase):
    """The projection must copy emitted statuses verbatim.

    If it recomputed them, the independent validator would end up comparing a
    value to itself and would agree regardless of what either side did.
    """

    def test_emitted_statuses_are_copied_verbatim(self):
        result = {
            "repository_url": "https://example.test/a",
            "analysis_status": "partial",
            "core_metric_status": "partial",
            # Deliberately self-inconsistent: a recomputing projection would
            # "fix" these, a copying one reports them as recorded.
            "expected_language_family_status": "complete",
            "partial_origin": "none",
            "expected_language": "Java",
            "metrics": {"aggregate": {"loc_status": "failed"}},
        }
        diagnostic = project_repository(
            result, has_inventory=True, has_contribution_ledger=False
        )
        self.assertEqual(diagnostic.expected_language_family_status, "complete")
        self.assertEqual(diagnostic.partial_origin, "none")
        self.assertEqual(diagnostic.analysis_status, "partial")

    def test_unknown_status_values_are_surfaced_not_normalized(self):
        diagnostic = project_repository(
            {
                "repository_url": "https://example.test/a",
                "analysis_status": "brand_new_status",
                "partial_origin": "unheard_of_origin",
                "metrics": {},
            },
            has_inventory=True, has_contribution_ledger=False,
        )
        self.assertIn("analysis_status='brand_new_status'", diagnostic.unknown_categories)
        self.assertIn("partial_origin='unheard_of_origin'", diagnostic.unknown_categories)


class EvidenceScopeTests(unittest.TestCase):
    def test_pathless_modules_never_receive_an_invented_path(self):
        for module in sorted(PATHLESS_MODULES):
            with self.subTest(module=module):
                diagnostic = project_repository(
                    {
                        "repository_url": "https://example.test/a",
                        "analysis_status": "failed",
                        "errors": [{
                            "module": module,
                            "error_category": "checkout_not_clean",
                            "message": "something happened",
                            # Even when the artifact carries a path, a pathless
                            # module must not present it as file evidence.
                            "file_path": "some/file.java",
                        }],
                        "metrics": {"aggregate": {"loc_status": "failed"}},
                    },
                    has_inventory=True, has_contribution_ledger=False,
                )
                evidence = diagnostic.evidence[0]
                self.assertIsNone(evidence.relative_path)
                self.assertEqual(evidence.scope, EvidenceScope.REPOSITORY)

    def test_file_scoped_evidence_keeps_its_path(self):
        diagnostic = project_repository(
            {
                "repository_url": "https://example.test/a",
                "analysis_status": "partial",
                "errors": [{
                    "module": "core_metrics",
                    "error_category": "parse_error",
                    "file_path": "src/Main.java",
                    "message": "syntax",
                }],
                "metrics": {"aggregate": {"loc_status": "partial"}},
            },
            has_inventory=True, has_contribution_ledger=False,
        )
        evidence = diagnostic.evidence[0]
        self.assertEqual(evidence.scope, EvidenceScope.FILE)
        self.assertEqual(evidence.relative_path, "src/Main.java")


class MetricEffectTests(unittest.TestCase):
    def test_failed_status_reports_the_metric_as_unavailable(self):
        diagnostic = project_repository(
            {
                "repository_url": "https://example.test/a",
                "analysis_status": "failed",
                "errors": [{"module": "core_metrics", "error_category": "x"}],
                "metrics": {"aggregate": {"loc_status": "failed"}},
            },
            has_inventory=True, has_contribution_ledger=False,
        )
        self.assertEqual(diagnostic.evidence[0].metric_effect, MetricEffect.UNAVAILABLE)

    def test_effect_stays_unknown_when_the_artifact_does_not_record_one(self):
        diagnostic = project_repository(
            {
                "repository_url": "https://example.test/a",
                "analysis_status": "complete",
                "errors": [{"module": "cleanup", "error_category": "worktree_left"}],
                "metrics": {"aggregate": {"loc_status": "complete"}},
            },
            has_inventory=True, has_contribution_ledger=False,
        )
        self.assertEqual(diagnostic.evidence[0].metric_effect, MetricEffect.UNKNOWN)


class ExplanationCompletenessTests(unittest.TestCase):
    def test_non_complete_without_evidence_is_insufficient_evidence(self):
        diagnostic = project_repository(
            {
                "repository_url": "https://example.test/a",
                "analysis_status": "partial",
                "errors": [],
                "metrics": {"aggregate": {}},
            },
            has_inventory=True, has_contribution_ledger=False,
        )
        self.assertEqual(
            diagnostic.explanation_completeness,
            ExplanationCompleteness.INSUFFICIENT_EVIDENCE,
        )

    def test_evaluable_dimensions_follow_available_artifacts(self):
        without = project_repository(
            {"repository_url": "https://example.test/a", "metrics": {}},
            has_inventory=False, has_contribution_ledger=False,
        )
        self.assertFalse(without.evaluable_dimensions["inventory"])
        self.assertFalse(without.evaluable_dimensions["exact_metric_reconciliation"])

        with_all = project_repository(
            {"repository_url": "https://example.test/a", "metrics": {}},
            has_inventory=True, has_contribution_ledger=True,
        )
        self.assertTrue(with_all.evaluable_dimensions["inventory"])
        self.assertTrue(with_all.evaluable_dimensions["exact_metric_reconciliation"])


class PreservedRunProjectionTests(unittest.TestCase):
    def setUp(self):
        self.run = find_run()
        self.projection = project_run(open_run(self.run))

    def test_every_non_complete_result_carries_evidence_or_says_it_lacks_it(self):
        for diagnostic in self.projection.repositories:
            if diagnostic.analysis_status == "complete":
                continue
            with self.subTest(repository=diagnostic.repository_url):
                if not diagnostic.evidence:
                    self.assertEqual(
                        diagnostic.explanation_completeness,
                        ExplanationCompleteness.INSUFFICIENT_EVIDENCE,
                    )

    def test_no_unknown_categories_in_a_clean_preserved_run(self):
        unknown = sorted({
            item
            for diagnostic in self.projection.repositories
            for item in diagnostic.unknown_categories
        })
        self.assertEqual(unknown, [])

    def test_null_profiler_sha_is_reported_as_a_provenance_warning(self):
        joined = " ".join(self.projection.provenance_warnings)
        self.assertIn("Metrolith evaluator Git revision is unavailable", joined)

    def test_projection_is_json_serializable_and_deterministic(self):
        first = json.dumps(self.projection.as_dict(), sort_keys=True)
        second = json.dumps(project_run(open_run(self.run)).as_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_run_scope_evidence_never_carries_a_file_path(self):
        for evidence in self.projection.run_evidence:
            with self.subTest(category=evidence.category):
                self.assertEqual(evidence.scope, EvidenceScope.RUN)
                self.assertIsNone(evidence.relative_path)


class IndependenceTests(unittest.TestCase):
    """Plan section 3.2, checked as source text as well as by import edge."""

    def test_diagnostics_does_not_reference_the_independent_derivation(self):
        source = (REPOSITORY / "modules" / "diagnostics.py").read_text(encoding="utf-8")
        # The name appears only inside the module docstring, explaining why the
        # dependency is forbidden. It must never be imported or called.
        body = source.split('"""', 2)[-1]
        self.assertNotIn("independently_derive_repository_diagnostics", body)
        self.assertNotIn("validate_outputs", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
