"""G2-C — ArchLens Cognitive Complexity product integration.

Two things are pinned here that nothing else pins:

**The three-way distinction survives every surface.** `absent` (no cognitive
measurement was ever attempted), `unavailable` (attempted, nothing usable) and
a **measured zero** are three different facts. Cognitive complexity makes this
sharper than the structural metric did: 0 is the commonest measured value, so a
renderer that prints `0` for an unavailable measurement is not making a cosmetic
mistake — it is inventing the most plausible-looking wrong answer there is.

**Cognitive comparability never blocks anything.** An older, failed or
differently-contracted cognitive measurement degrades cognitive deltas and
touches neither the four benchmark metrics nor the Complexity Contract 1.0.0
deltas. The compatibility matrix below is the whole point of the phase.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from modules import complexity_view


def _repository(
    *, contract: str | None = "2.0.0", status: str = "complete",
    cognitive: str | None = "measured", block: bool = True,
) -> dict:
    """One repository result, at a chosen artifact generation."""
    if not block:
        return {"repository_url": "https://x/none", "metrics": {}}
    inner: dict = {"status": status, "complexity_contract_version": contract}
    if cognitive is not None:
        inner["cognitive_measurement_state"] = cognitive
    return {"repository_url": "https://x/one", "metrics": {"complexity": inner}}


def _rows(*values, language: str = "Go") -> list[dict]:
    return [
        {"cognitive_complexity": value, "detected_language": language,
         "relative_path": "a.go", "qualified_name": f"f{index}"}
        for index, value in enumerate(values)
    ]


class AggregateDefinitionTests(unittest.TestCase):
    """The smallest justified set, and nothing beyond it."""

    def test_exactly_five_aggregates(self):
        self.assertEqual(
            complexity_view.COGNITIVE_AGGREGATE_FIELDS,
            (
                "cognitive_callable_count",
                "cognitive_complexity_total",
                "cognitive_complexity_mean",
                "cognitive_complexity_median",
                "cognitive_complexity_max",
            ),
        )

    def test_no_field_is_a_percentile_threshold_hotspot_or_composite(self):
        """Scanned on the FIELD NAMES and labels, not the prose.

        The definitions legitimately contain words like "score" inside
        negations -- "it is NOT a repository-wide comprehension score" -- and a
        scan that cannot tell a denial from a claim would force the prose to
        get vaguer to satisfy the test.
        """
        published = " ".join(
            f"{field} {label}"
            for field, label, _definition
            in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS
        ).lower()
        for word in (
            "percentile", "p90", "p95", "threshold", "hotspot", "score",
            "rating", "grade", "critical", "severity", "composite", "index",
            "ratio",
        ):
            with self.subTest(word):
                self.assertNotIn(word, published)

    def test_every_aggregate_ships_its_definition(self):
        for field, label, definition in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS:
            with self.subTest(field):
                self.assertTrue(label.strip())
                self.assertTrue(len(definition.strip()) > 30, definition)

    def test_the_median_is_the_lower_median_and_says_so(self):
        aggregate = complexity_view.cognitive_aggregate(_rows(1, 2, 3, 4))
        self.assertEqual(aggregate["cognitive_complexity_median"], 2)
        definition = dict(
            (field, note)
            for field, _label, note in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS
        )["cognitive_complexity_median"]
        self.assertIn("LOWER", definition)

    def test_the_count_excludes_unavailable_rows(self):
        """`cognitive_callable_count` counts MEASUREMENTS, not rows."""
        aggregate = complexity_view.cognitive_aggregate(_rows(0, 3, None, 5))
        self.assertEqual(aggregate["cognitive_callable_count"], 3)
        self.assertEqual(aggregate["cognitive_complexity_total"], 8)


class AbsentUnavailableZeroTests(unittest.TestCase):
    """The distinction the whole phase is built around."""

    def test_a_measured_zero_is_a_number(self):
        aggregate = complexity_view.cognitive_aggregate(_rows(0, 0, 0))
        self.assertEqual(aggregate["cognitive_complexity_total"], 0)
        self.assertEqual(aggregate["cognitive_complexity_max"], 0)
        self.assertEqual(aggregate["cognitive_callable_count"], 3)
        presented = complexity_view.cognitive_presentation(
            _repository(), _rows(0, 0, 0)
        )
        rendered = {row["field"]: row["rendered"] for row in presented["aggregate"]}
        self.assertEqual(rendered["cognitive_complexity_total"], "0")
        self.assertEqual(rendered["cognitive_complexity_max"], "0")

    def test_an_empty_population_is_unavailable_never_zero(self):
        aggregate = complexity_view.cognitive_aggregate([])
        self.assertEqual(set(aggregate.values()), {None})
        presented = complexity_view.cognitive_presentation(_repository(), [])
        for row in presented["aggregate"]:
            with self.subTest(row["field"]):
                self.assertEqual(row["rendered"], complexity_view.UNAVAILABLE)

    def test_the_three_absences_are_three_states(self):
        cases = {
            "pre-1.9 (no complexity block)": (
                _repository(block=False), complexity_view.COGNITIVE_ABSENT
            ),
            "1.9 (complexity, no cognitive)": (
                _repository(contract="1.0.0", cognitive=None),
                complexity_view.COGNITIVE_ABSENT,
            ),
            "1.10 failed": (
                _repository(status="failed", cognitive="failed"),
                complexity_view.COGNITIVE_FAILED,
            ),
            "1.10 measured": (_repository(), complexity_view.COGNITIVE_MEASURED),
        }
        for label, (result, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(complexity_view.cognitive_state_of(result), expected)

    def test_a_failed_measurement_never_renders_as_zero(self):
        presented = complexity_view.cognitive_presentation(
            _repository(status="failed", cognitive="failed"), _rows(0, 1, 2)
        )
        self.assertFalse(presented["evaluable"])
        for row in presented["aggregate"]:
            with self.subTest(row["field"]):
                self.assertEqual(row["rendered"], complexity_view.UNAVAILABLE)
                self.assertNotEqual(row["rendered"], "0")

    def test_every_state_carries_a_meaning(self):
        for state in complexity_view.COGNITIVE_STATE_MEANINGS:
            self.assertTrue(complexity_view.COGNITIVE_STATE_MEANINGS[state].strip())


class NamingDisciplineTests(unittest.TestCase):
    """G0's finding, enforced across every rendered string in the seam."""

    FORBIDDEN = ("sonar-compatible", "sonar compatible", "sonarqube", "sonarjs")

    def test_the_metric_name(self):
        self.assertEqual(
            complexity_view.COGNITIVE_METRIC_NAME, "Metrolith Cognitive Complexity"
        )

    def test_no_seam_string_claims_sonar_compatibility(self):
        blob = " ".join([
            complexity_view.COGNITIVE_METRIC_NAME,
            complexity_view.COGNITIVE_NAMING_STATEMENT,
            complexity_view.COGNITIVE_ZERO_STATEMENT,
            complexity_view.COGNITIVE_COVERAGE_LIMITATION,
            *complexity_view.COGNITIVE_STATE_MEANINGS.values(),
            *(note for _f, _l, note in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS),
        ]).lower()
        for word in self.FORBIDDEN:
            with self.subTest(word):
                self.assertNotIn(word, blob)

    def test_the_naming_statement_denies_compatibility_explicitly(self):
        statement = complexity_view.COGNITIVE_NAMING_STATEMENT
        self.assertIn("NOT Sonar Cognitive Complexity", statement)
        self.assertIn("no compatibility", statement.lower())


class ProvenanceTests(unittest.TestCase):
    def test_provenance_carries_the_five_required_facts(self):
        record = complexity_view.cognitive_provenance(
            {"complexity_contract_version": "2.0.0"},
            {**_repository(), "analysis_scope_hash": "abc",
             "source_mode": "worktree", "acquisition": {"analyzed_commit_sha": "def"}},
        )
        self.assertEqual(record["metric_name"], "Metrolith Cognitive Complexity")
        self.assertEqual(record["complexity_contract_version"], "2.0.0")
        self.assertEqual(record["measurement_state"], "measured")
        self.assertEqual(record["analyzed_scope"]["analysis_scope_hash"], "abc")
        self.assertEqual(record["analyzed_scope"]["analyzed_commit_sha"], "def")
        self.assertEqual(
            set(record["aggregate_definitions"]),
            set(complexity_view.COGNITIVE_AGGREGATE_FIELDS),
        )
        self.assertTrue(record["zero_statement"])
        self.assertTrue(record["naming"])


class DiffCompatibilityMatrixTests(unittest.TestCase):
    """Task 4's matrix, on the verdict function rather than on whole runs.

    Whole-run diffs are exercised by `test_complexity_integration_c5` and by the
    acceptance suite; what is pinned here is that each combination produces the
    right verdict and that the verdict never blocks.
    """

    class _Side:
        def __init__(self, label, repository, version="2.0.0"):
            self.spec = type("S", (), {"label": label})()
            self.repository = repository
            self.view = type("V", (), {"manifest": {"complexity_contract_version": version}})()
            self.subject_key = "k"

    def _verdict(self, left, right):
        from modules.revision_diff import assess_cognitive_comparability

        return assess_cognitive_comparability(left, right)

    def test_1_10_versus_1_10_is_evaluable(self):
        verdict = self._verdict(
            self._Side("from", _repository()), self._Side("to", _repository())
        )
        self.assertTrue(verdict["evaluable"])
        self.assertIsNone(verdict["not_evaluable_reason"])
        self.assertFalse(verdict["blocks_diff"])

    def test_1_10_versus_1_9_is_absent_not_zero(self):
        verdict = self._verdict(
            self._Side("from", _repository(contract="1.0.0", cognitive=None), "1.0.0"),
            self._Side("to", _repository()),
        )
        self.assertFalse(verdict["evaluable"])
        self.assertIn("absent", verdict["not_evaluable_reason"])
        self.assertIn("not the same as a measured zero", verdict["not_evaluable_reason"])
        self.assertFalse(verdict["blocks_diff"])

    def test_failed_or_unavailable_is_never_read_as_zero(self):
        verdict = self._verdict(
            self._Side("from", _repository(status="failed", cognitive="failed")),
            self._Side("to", _repository()),
        )
        self.assertFalse(verdict["evaluable"])
        self.assertIn("never read as zero", verdict["not_evaluable_reason"])
        self.assertFalse(verdict["blocks_diff"])

    def test_different_complexity_contracts_are_not_comparable(self):
        verdict = self._verdict(
            self._Side("from", _repository(contract="2.0.0")),
            self._Side("to", _repository(contract="3.0.0")),
        )
        self.assertFalse(verdict["evaluable"])
        self.assertIn("different Complexity Contracts", verdict["not_evaluable_reason"])
        self.assertFalse(verdict["blocks_diff"])

    def test_a_genuine_zero_callable_scope_stays_evaluable(self):
        """Measured, and nothing found. Evaluable — and not the same as failed."""
        verdict = self._verdict(
            self._Side("from", _repository()), self._Side("to", _repository())
        )
        self.assertTrue(verdict["evaluable"])
        from modules.revision_diff import _cognitive_by_language

        self.assertEqual(_cognitive_by_language([]), {})

    def test_the_verdict_never_blocks_in_any_combination(self):
        combinations = (
            (_repository(), _repository()),
            (_repository(contract="1.0.0", cognitive=None), _repository()),
            (_repository(status="failed", cognitive="failed"), _repository()),
            (_repository(block=False), _repository()),
            (_repository(contract="2.0.0"), _repository(contract="9.9.9")),
        )
        for index, (left, right) in enumerate(combinations):
            with self.subTest(index):
                verdict = self._verdict(
                    self._Side("from", left), self._Side("to", right)
                )
                self.assertFalse(verdict["blocks_diff"])
                self.assertIn("never blocks", verdict["note"])


class DiffIsolationTests(unittest.TestCase):
    """Cognitive must not be reachable from the benchmark or structural paths."""

    def test_cognitive_is_not_in_the_benchmark_dimensions(self):
        from modules import revision_diff

        self.assertNotIn("cognitive_complexity_total", revision_diff.AGGREGATE_DIMENSIONS)
        for dimension in revision_diff.AGGREGATE_DIMENSIONS:
            self.assertNotIn("cognitive", dimension)

    def test_cognitive_is_not_in_the_structural_complexity_dimensions(self):
        from modules import revision_diff

        for dimension in revision_diff.COMPLEXITY_AGGREGATE_DIMENSIONS:
            self.assertNotIn("cognitive", dimension)

    def test_no_callable_level_matching_exists(self):
        """Direct Diff joins files, never callables."""
        from modules import revision_diff

        source = Path(revision_diff.__file__).read_text(encoding="utf-8")
        marker = source[source.index("def build_cognitive_file_deltas"):]
        marker = marker[: marker.index("\ndef ", 10)]
        # The docstring names `callable_row_id` precisely to say the function
        # does not pair on it. Scanning the CODE keeps the explanation legal.
        body = marker.split('"""')[2]
        self.assertNotIn("callable_row_id", body)
        self.assertIn("relative_path", body)


class ExplainAndReportSurfaceTests(unittest.TestCase):
    def test_explain_output_version_moved_for_the_new_fields(self):
        from modules.cli.explain_command import EXPLANATION_FORMAT_VERSION

        self.assertEqual(EXPLANATION_FORMAT_VERSION, "1.3.0")

    def test_revision_diff_version_moved_for_the_new_fields(self):
        from modules.revision_diff import REVISION_DIFF_FORMAT_VERSION

        self.assertEqual(REVISION_DIFF_FORMAT_VERSION, "1.2.0")

    def test_the_diagnostic_declares_the_cognitive_fields(self):
        from modules.diagnostics import RepositoryDiagnostic

        annotations = RepositoryDiagnostic.__annotations__
        self.assertIn("cognitive_state", annotations)
        self.assertIn("cognitive_metric_name", annotations)
        defaults = {
            field.name: field.default
            for field in RepositoryDiagnostic.__dataclass_fields__.values()
        }
        # Absent by default: every pre-1.10 run, and never a measured zero.
        self.assertEqual(defaults["cognitive_state"], "absent")

    def test_cognitive_is_a_separate_evaluable_dimension_from_complexity(self):
        """A 1.9 run is structurally evaluable and cognitively absent."""
        source = Path(
            __import__("modules.diagnostics", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        self.assertIn('"cognitive_complexity": (', source)
        self.assertIn("cognitive_state=complexity_view.cognitive_state_of", source)

    def test_the_ranking_field_set_includes_cognitive(self):
        fields = dict(complexity_view.RANKING_FIELDS)
        self.assertIn("cognitive_complexity", fields)

    def test_a_row_without_a_cognitive_value_is_not_ranked(self):
        ranked = complexity_view.rank_callables(
            [{"cognitive_complexity": None, "relative_path": "a"},
             {"cognitive_complexity": 4, "relative_path": "b"}],
            "cognitive_complexity",
        )
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["relative_path"], "b")




if __name__ == "__main__":
    unittest.main()
