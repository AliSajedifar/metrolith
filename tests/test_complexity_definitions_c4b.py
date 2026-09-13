"""C4-B: the synthetic coverage audit, enforced.

The point of this file is that an unmapped or silently-assumed cell fails the
suite. A metric/language pair with no reviewed mapping must never be treated as
validated just because nothing complained.
"""

from __future__ import annotations

import unittest

from validation.differential.complexity_definitions import (
    CLASSIFICATIONS,
    DOCUMENTED,
    EXACT,
    LANGUAGES,
    METRICS,
    NOT_EVALUABLE,
    REFERENCE_VERSIONS,
    all_mappings,
    coverage_report,
)


class MappingShapeTests(unittest.TestCase):
    def test_every_mapping_is_fully_populated(self):
        for mapping in all_mappings():
            with self.subTest(f"{mapping.language}/{mapping.metric}/{mapping.reference}"):
                self.assertIn(mapping.classification, CLASSIFICATIONS)
                self.assertIn(mapping.metric, METRICS)
                self.assertIn(mapping.language, LANGUAGES)
                for field in (
                    "unit_compared", "callable_population",
                    "construct_semantics", "nested_callable_treatment",
                ):
                    self.assertTrue(
                        getattr(mapping, field).strip(),
                        f"{field} must be stated, never blank",
                    )

    def test_a_documented_difference_names_the_difference(self):
        """A `documented differences` classification with no differences listed
        would be a claim with no content."""
        for mapping in all_mappings():
            if mapping.classification == DOCUMENTED:
                with self.subTest(f"{mapping.language}/{mapping.reference}"):
                    self.assertTrue(
                        mapping.known_differences,
                        "classified as having documented differences but none "
                        "are recorded",
                    )

    def test_not_evaluable_states_why(self):
        for mapping in all_mappings():
            if mapping.classification == NOT_EVALUABLE:
                self.assertTrue(mapping.known_differences)


class CoverageAuditTests(unittest.TestCase):
    """The four audit requirements, each as an assertion."""

    def setUp(self):
        self.report = coverage_report()

    def test_every_metric_language_pair_has_a_primary_mapping(self):
        self.assertTrue(self.report["every_primary_cell_mapped"])
        self.assertEqual(
            self.report["primary_cells"], len(LANGUAGES) * len(METRICS), "5 x 7"
        )

    def test_no_primary_cell_is_unmapped_or_not_comparable(self):
        self.assertEqual(self.report["primary_not_comparable"], 0)
        self.assertEqual(
            self.report["primary_not_evaluable"], 0,
            "a primary adapter that cannot evaluate a whole metric would leave "
            "that metric with no independent cross-check at all",
        )

    def test_the_primary_classification_split_is_recorded(self):
        self.assertEqual(
            self.report["primary_exact"] + self.report["primary_documented"],
            self.report["primary_cells"],
        )
        self.assertGreater(self.report["primary_documented"], 0,
                           "the parso match and javac guarded-case limits")

    def test_every_external_limitation_is_explicit(self):
        self.assertTrue(
            self.report["external_not_evaluable"],
            "Lizard's TypeScript limitation must remain recorded, not dropped "
            "once ESLint was provisioned",
        )
        languages = {
            item["language"] for item in self.report["external_not_evaluable"]
        }
        self.assertIn("TypeScript", languages)

    def test_metrics_without_an_external_reference_are_listed_not_assumed(self):
        uncovered = self.report["metrics_without_an_external_reference"]
        self.assertTrue(uncovered)
        for entry in uncovered:
            self.assertNotEqual(entry["metric"], "cyclomatic_complexity",
                                "every language has an external CC reference")
            self.assertTrue(entry["reason"])
        # Six of seven metrics have no external tool, in all five languages.
        self.assertEqual(len(uncovered), len(LANGUAGES) * (len(METRICS) - 1))

    def test_every_pinned_version_is_recorded(self):
        for tool in (
            "parso", "javac", "typescript", "node", "go", "lizard", "eslint",
            "@typescript-eslint/parser", "reference_complexity_adapter",
        ):
            self.assertIn(tool, REFERENCE_VERSIONS)
            self.assertTrue(REFERENCE_VERSIONS[tool])

    def test_the_adapter_version_is_independent_of_the_tools(self):
        # 2.2.0 since the C4 Layer-C2 campaign corrected Python and Node rules.
        self.assertEqual(REFERENCE_VERSIONS["reference_complexity_adapter"], "2.2.0")
        # Each adapter carries its own version, so a reader can tell which moved.
        self.assertEqual(REFERENCE_VERSIONS["reference_complexity_adapter_java"], "2.0.0")
        self.assertEqual(REFERENCE_VERSIONS["reference_complexity_adapter_go"], "2.1.0")
        self.assertNotEqual(
            REFERENCE_VERSIONS["reference_complexity_adapter"],
            REFERENCE_VERSIONS["typescript"],
        )


class ExternalReferenceTests(unittest.TestCase):
    def test_lizard_is_not_the_typescript_reference(self):
        from validation.differential.complexity_definitions import EXTERNAL_CC

        self.assertIn("ESLint", EXTERNAL_CC["TypeScript"])
        self.assertNotIn("Lizard", EXTERNAL_CC["TypeScript"])

    def test_lizard_covers_the_other_four_languages(self):
        from validation.differential.complexity_definitions import EXTERNAL_CC

        for language in ("Go", "Java", "JavaScript", "Python"):
            self.assertIn("Lizard", EXTERNAL_CC[language])

    def test_the_eslint_default_parameter_difference_is_recorded(self):
        for mapping in all_mappings():
            if mapping.language == "TypeScript" and "ESLint" in mapping.reference:
                joined = " ".join(mapping.known_differences).lower()
                self.assertIn("default parameter", joined)
                return
        self.fail("no ESLint TypeScript mapping found")

    def test_external_comparisons_are_never_grounds_to_change_archlens(self):
        """Stated in the module docstring, asserted here so it survives edits."""
        import validation.differential.complexity_definitions as module

        self.assertIn(
            "never grounds for changing ArchLens", module.__doc__,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
