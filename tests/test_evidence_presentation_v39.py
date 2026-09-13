"""Evidence Presentation 3.9: the four presentation surfaces and their boundaries.

The rule every case here exists to hold is the one the whole product rests on:
**missing is not zero, and unavailable is not complete.** A presentation layer is
where that rule is most likely to be lost, because a renderer's natural default
for "no number" is to print `0`.

The second rule is about authority. A derived document may summarize a
measurement; it may not become one, and it may not quietly combine two
measurements that describe different things.

Fixtures are built from the smallest inputs that exhibit the case. Where a real
recorded artifact exists — the Artifact 1.4.0 historical run, whose repository
failed wholesale — it is used in preference to a hand-built one, because a
hand-built artifact can be made to say anything.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import MappingProxyType

from modules import complexity_distribution, dossier, source_composition
from modules.changed_code import render_markdown

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = REPOSITORY_ROOT / "tests" / "fixtures" / "historical"


def _result(aggregate: dict, *, by_language: dict | None = None) -> dict:
    return {
        "metrics": {
            "aggregate": aggregate,
            "by_language": by_language or {},
            "metric_contract_version": "3.0.0",
        }
    }


def _composition(
    *, code: int, comment: int, blank: int, status: str = "complete"
) -> dict:
    return {
        "loc_status": status,
        "code_lines": code,
        "comment_lines": comment,
        "blank_lines": blank,
        "nonblank_lines": code + comment,
        "total_physical_lines": code + comment + blank,
    }


class SourceCompositionDefinitionTests(unittest.TestCase):
    """The five counts, their identities, and what they are not."""

    def test_the_five_counts_are_exposed_with_definitions(self):
        view = source_composition.presentation(
            _result(_composition(code=70, comment=20, blank=10))
        )
        self.assertEqual(
            [entry["field"] for entry in view["counts"]],
            [
                "total_physical_lines", "blank_lines", "nonblank_lines",
                "code_lines", "comment_lines",
            ],
        )
        for entry in view["counts"]:
            with self.subTest(field=entry["field"]):
                self.assertTrue(entry["definition"].strip())

    def test_both_partition_identities_hold_on_consistent_counts(self):
        view = source_composition.presentation(
            _result(_composition(code=70, comment=20, blank=10))
        )
        self.assertEqual(
            [check["holds"] for check in view["partition_checks"]], [True, True]
        )

    def test_a_violated_identity_is_reported_rather_than_hidden(self):
        broken = _composition(code=70, comment=20, blank=10)
        broken["nonblank_lines"] = 999
        view = source_composition.presentation(_result(broken))
        self.assertEqual(
            [check["holds"] for check in view["partition_checks"]], [False, False]
        )

    def test_an_identity_over_unavailable_counts_has_no_truth_value(self):
        view = source_composition.presentation(
            _result(_composition(code=70, comment=20, blank=10, status="failed"))
        )
        for check in view["partition_checks"]:
            with self.subTest(identity=check["identity"]):
                self.assertIsNone(check["holds"])

    def test_code_lines_is_the_same_figure_as_lines_of_code(self):
        aggregate = _composition(code=70, comment=20, blank=10)
        aggregate["lines_of_code"] = 70
        view = source_composition.presentation(_result(aggregate))
        code = next(
            entry for entry in view["counts"] if entry["field"] == "code_lines"
        )
        self.assertEqual(code["value"], aggregate["lines_of_code"])


class SourceCompositionUnavailabilityTests(unittest.TestCase):
    """Unavailable, absent and measured zero stay three different facts."""

    def test_a_failed_status_renders_unavailable_even_when_the_record_holds_zero(self):
        # The case the seam exists for: `_finalize_metric` nulls `lines_of_code`
        # on failure but leaves the components at their accumulated 0.
        view = source_composition.presentation(
            _result(_composition(code=0, comment=0, blank=0, status="failed"))
        )
        self.assertEqual(view["state"], source_composition.STATE_FAILED)
        for entry in view["counts"]:
            with self.subTest(field=entry["field"]):
                self.assertEqual(entry["rendered"], source_composition.UNAVAILABLE)
                self.assertIsNone(entry["value"])

    def test_a_measured_zero_renders_as_zero(self):
        view = source_composition.presentation(
            _result(_composition(code=0, comment=0, blank=0))
        )
        self.assertEqual(view["state"], source_composition.STATE_COMPLETE)
        for entry in view["counts"]:
            with self.subTest(field=entry["field"]):
                self.assertEqual(entry["rendered"], "0")

    def test_a_record_without_composition_keys_is_absent_not_zero(self):
        view = source_composition.presentation(_result({"source_files": 3}))
        self.assertEqual(view["state"], source_composition.STATE_ABSENT)
        for entry in view["counts"]:
            self.assertEqual(entry["rendered"], source_composition.UNAVAILABLE)

    def test_the_recorded_artifact_140_failure_is_reported_unavailable(self):
        paths = sorted(HISTORICAL.glob("artifact-1.4.0-run/**/analysis.json"))
        self.assertTrue(paths, "the Artifact 1.4.0 historical fixture is mandatory")
        results = json.loads(paths[0].read_text(encoding="utf-8"))
        view = source_composition.presentation(
            results[0] if isinstance(results, list) else results
        )
        self.assertEqual(view["state"], source_composition.STATE_FAILED)
        self.assertTrue(
            all(
                entry["rendered"] == source_composition.UNAVAILABLE
                for entry in view["counts"]
            )
        )


class SourceCompositionRatioTests(unittest.TestCase):
    """Ratios are shares with named denominators, null when undefined."""

    def test_ratios_are_computed_against_their_declared_denominator(self):
        view = source_composition.presentation(
            _result(_composition(code=70, comment=30, blank=25))
        )
        found = {entry["field"]: entry for entry in view["ratios"]}
        self.assertEqual(found["comment_line_ratio"]["denominator_field"], "nonblank_lines")
        self.assertEqual(found["comment_line_ratio"]["value"], 0.3)
        self.assertEqual(found["code_line_ratio"]["value"], 0.7)
        self.assertEqual(found["blank_line_ratio"]["denominator_field"], "total_physical_lines")
        self.assertEqual(found["blank_line_ratio"]["value"], 0.2)

    def test_a_zero_denominator_yields_null_and_never_zero(self):
        view = source_composition.presentation(
            _result(_composition(code=0, comment=0, blank=0))
        )
        for entry in view["ratios"]:
            with self.subTest(ratio=entry["field"]):
                self.assertIsNone(entry["value"])
                self.assertEqual(entry["rendered"], source_composition.UNAVAILABLE)
                self.assertEqual(entry["unavailable_reason"], "zero_denominator")

    def test_an_unavailable_measurement_is_distinguished_from_a_zero_denominator(self):
        view = source_composition.presentation(
            _result(_composition(code=0, comment=0, blank=0, status="failed"))
        )
        for entry in view["ratios"]:
            with self.subTest(ratio=entry["field"]):
                self.assertEqual(entry["unavailable_reason"], "measurement_unavailable")

    def test_ratio_helper_refuses_a_zero_denominator_directly(self):
        self.assertIsNone(source_composition.ratio(5, 0))
        self.assertIsNone(source_composition.ratio(None, 10))
        self.assertIsNone(source_composition.ratio(5, None))
        self.assertEqual(source_composition.ratio(1, 4), 0.25)

    def test_complementary_ratios_sum_to_one(self):
        view = source_composition.presentation(
            _result(_composition(code=37, comment=63, blank=11))
        )
        found = {entry["field"]: entry["value"] for entry in view["ratios"]}
        self.assertEqual(
            found["comment_line_ratio"] + found["code_line_ratio"], 1.0
        )

    def test_no_quality_vocabulary_appears_in_any_definition(self):
        prohibitions = " ".join(source_composition.PROHIBITION_STATEMENTS).lower()
        for _field, _label, definition in source_composition.COMPOSITION_DEFINITIONS:
            for word in ("maintainability", "quality score", "grade", "health"):
                self.assertNotIn(word, definition.lower())
        for _name, _label, _n, _d, definition in source_composition.RATIO_DEFINITIONS:
            for word in ("documentation quality", "maintainability score", "health"):
                self.assertNotIn(word, definition.lower())
        # The denial itself is allowed to name what it denies.
        self.assertIn("no composite score", prohibitions)


class SourceCompositionLanguageTests(unittest.TestCase):
    """Language separation, per-language state, and Unicode."""

    def test_each_language_carries_its_own_state(self):
        view = source_composition.presentation(
            _result(
                _composition(code=10, comment=2, blank=1),
                by_language={
                    "Python": _composition(code=10, comment=2, blank=1),
                    "Go": _composition(code=0, comment=0, blank=0, status="failed"),
                },
            )
        )
        self.assertEqual(view["by_language"]["Python"]["state"], "complete")
        self.assertEqual(view["by_language"]["Go"]["state"], "failed")
        # The failed language must not borrow the healthy one's numbers.
        for entry in view["by_language"]["Go"]["counts"]:
            self.assertEqual(entry["rendered"], source_composition.UNAVAILABLE)

    def test_languages_are_emitted_in_sorted_order(self):
        view = source_composition.presentation(
            _result(
                _composition(code=1, comment=1, blank=1),
                by_language={
                    name: _composition(code=1, comment=1, blank=1)
                    for name in ("TypeScript", "Go", "Python", "Java")
                },
            )
        )
        self.assertEqual(
            list(view["by_language"]), ["Go", "Java", "Python", "TypeScript"]
        )

    def test_a_unicode_repository_name_is_escaped_in_the_summary(self):
        from modules.summary import render_summary

        result = _result(_composition(code=5, comment=1, blank=1))
        result["repository_url"] = "https://example.invalid/‮evil|pipe"
        text = render_summary(
            {"run_id": "r"}, [result], outcome="complete", integrity_status="completed"
        )
        self.assertIn("### Source composition", text)
        # The bidirectional override is rendered visibly, not passed through.
        self.assertNotIn("‮", text)
        self.assertIn("\\u202e", text)

    def test_a_unicode_blank_line_convention_is_documented(self):
        definition = dict(
            (field, definition)
            for field, _label, definition in source_composition.COMPOSITION_DEFINITIONS
        )["blank_lines"]
        self.assertIn("Unicode", definition)


class ComplexityDistributionTests(unittest.TestCase):
    """Exact counts, observed percentiles, and language separation."""

    @staticmethod
    def _rows(values, *, field="cyclomatic_complexity", language="Python"):
        return [
            {
                field: value,
                "structural_complexity_status": "complete",
                "nloc_status": "complete",
                "detected_language": language,
            }
            for value in values
        ]

    def test_frequency_counts_are_exact(self):
        rows = self._rows([1, 1, 1, 2, 5, 5])
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        self.assertEqual(
            computed["frequency"],
            [
                {"value": 1, "count": 3},
                {"value": 2, "count": 1},
                {"value": 5, "count": 2},
            ],
        )
        self.assertEqual(computed["measured_count"], 6)
        self.assertEqual(computed["distinct_values"], 3)

    def test_cumulative_counts_and_shares_accumulate(self):
        rows = self._rows([1, 1, 2, 4])
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        self.assertEqual(
            [(row["value"], row["at_or_below"]) for row in computed["cumulative"]],
            [(1, 2), (2, 3), (4, 4)],
        )
        self.assertEqual(computed["cumulative"][0]["share_at_or_below"], 0.5)

    def test_every_percentile_is_an_observed_value(self):
        rows = self._rows([1, 2, 3, 4, 5, 6, 7, 8, 9, 100])
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        observed = {1, 2, 3, 4, 5, 6, 7, 8, 9, 100}
        for name, value in computed["percentiles"].items():
            with self.subTest(percentile=name):
                self.assertIn(value, observed)

    def test_p50_equals_the_persisted_lower_median_for_every_population_size(self):
        from modules.callable_ledger import lower_median

        for size in range(1, 40):
            values = list(range(size))
            with self.subTest(size=size):
                self.assertEqual(
                    complexity_distribution.percentile(values, 50),
                    lower_median(values),
                )

    def test_an_unmeasured_row_is_excluded_rather_than_counted_as_zero(self):
        rows = self._rows([3, 4])
        rows.append({
            "cyclomatic_complexity": None,
            "structural_complexity_status": "failed",
            "nloc_status": "failed",
            "detected_language": "Python",
        })
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        self.assertEqual(computed["measured_count"], 2)
        self.assertEqual(computed["unmeasured_count"], 1)
        self.assertEqual(computed["min"], 3)

    def test_an_empty_population_reports_nulls_and_never_zeros(self):
        computed = complexity_distribution.distribution([], "cyclomatic_complexity")
        self.assertEqual(computed["measured_count"], 0)
        self.assertIsNone(computed["min"])
        self.assertIsNone(computed["max"])
        for value in computed["percentiles"].values():
            self.assertIsNone(value)

    def test_string_cells_from_a_csv_read_back_are_accepted_and_blank_is_not_zero(self):
        rows = [
            {
                "cyclomatic_complexity": "7",
                "structural_complexity_status": "complete",
                "nloc_status": "complete",
                "detected_language": "Go",
            },
            {
                "cyclomatic_complexity": "",
                "structural_complexity_status": "complete",
                "nloc_status": "complete",
                "detected_language": "Go",
            },
        ]
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        self.assertEqual(computed["measured_count"], 1)
        self.assertEqual(computed["min"], 7)

    def test_nloc_is_governed_by_its_own_status(self):
        # A file may classify lines and fail structural measurement, or the
        # reverse. One status must not gate the other's column.
        rows = [{
            "nloc": 12,
            "cyclomatic_complexity": 4,
            "nloc_status": "complete",
            "structural_complexity_status": "failed",
            "detected_language": "Java",
        }]
        self.assertEqual(
            complexity_distribution.distribution(rows, "nloc")["measured_count"], 1
        )
        self.assertEqual(
            complexity_distribution.distribution(
                rows, "cyclomatic_complexity"
            )["measured_count"],
            0,
        )

    def test_languages_are_partitioned_and_never_pooled(self):
        rows = self._rows([1, 1], language="Python") + self._rows(
            [50, 60], language="Go"
        )
        result = {"metrics": {"complexity": {"status": "complete"}}}
        view = complexity_distribution.presentation(result, rows)
        self.assertEqual(sorted(view["by_language"]), ["Go", "Python"])
        python = view["by_language"]["Python"]["distributions"][0]
        go = view["by_language"]["Go"]["distributions"][0]
        self.assertEqual(python["max"], 1)
        self.assertEqual(go["min"], 50)
        self.assertEqual(view["combined"]["distributions"][0]["measured_count"], 4)

    def test_an_unevaluable_state_reports_no_distribution_at_all(self):
        result = {"metrics": {"complexity": {"status": "failed"}}}
        view = complexity_distribution.presentation(result, self._rows([1, 2, 3]))
        self.assertFalse(view["evaluable"])
        self.assertIsNone(view["combined"])
        self.assertEqual(view["by_language"], {})

    def test_a_large_callable_set_is_counted_exactly(self):
        rows = self._rows([value % 37 + 1 for value in range(50_000)])
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        self.assertEqual(computed["measured_count"], 50_000)
        self.assertEqual(sum(row["count"] for row in computed["frequency"]), 50_000)
        self.assertEqual(computed["cumulative"][-1]["at_or_below"], 50_000)
        self.assertEqual(computed["cumulative"][-1]["share_at_or_below"], 1.0)

    def test_frequency_truncation_reports_what_it_withheld(self):
        rows = self._rows(list(range(1, 101)))
        computed = complexity_distribution.distribution(rows, "cyclomatic_complexity")
        shown, withheld = complexity_distribution.frequency_head(computed, limit=10)
        self.assertEqual(len(shown), 10)
        self.assertEqual(withheld, 90)

    def test_no_bucket_or_grade_vocabulary_is_produced(self):
        for _field, _label, _status, definition in (
            complexity_distribution.DISTRIBUTION_DEFINITIONS
        ):
            for word in ("healthy", "unhealthy", "grade", "rating"):
                self.assertNotIn(word, definition.lower())


class ChangedCodeMarkdownTests(unittest.TestCase):
    """Deterministic bytes, precise wording, and no host paths."""

    def _document(self) -> dict:
        return {
            "format": "archlens-changed-code",
            "format_version": "1.0.0",
            "program_version": "3.8.0",
            "status": "complete",
            "reasons": [],
            "capabilities": {
                "exact_direct_tree_comparison": True,
                "side_local_callable_overlap": True,
                "cross_revision_callable_matching": False,
                "duplication_change_classification": False,
                "hotspot_change_classification": False,
                "policy_evaluation": False,
                "sarif_projection": False,
                "risk_or_severity_scoring": False,
            },
            "subject": {"subject_key": "local:demo", "subject_key_basis": "explicit"},
            "base": {
                "requested_revision": "a" * 40,
                "resolved_commit_sha": "a" * 40,
                "analyzed_commit_sha": "a" * 40,
                "source_mode": "local",
                "analysis_scope_hash": "scope-a",
                "analysis_status": "complete",
                "metric_contract_version": "3.0.0",
                "complexity_contract_version": "2.0.0",
                "exclusion_policy_version": "1.5.0",
            },
            "head": {
                "requested_revision": "b" * 40,
                "resolved_commit_sha": "b" * 40,
                "analyzed_commit_sha": "b" * 40,
                "source_mode": "local",
                "analysis_scope_hash": "scope-b",
                "analysis_status": "complete",
                "metric_contract_version": "3.0.0",
                "complexity_contract_version": "2.0.0",
                "exclusion_policy_version": "1.5.0",
            },
            "git_provenance": {
                "comparison": "tree(base) -> tree(head)",
                "change_extraction_status": "complete",
                "ancestry": "ancestor",
                "shallow_repository": False,
                "worktree_state_observed": "clean",
                "uncommitted_and_untracked_content_excluded": True,
                "network_contacted": False,
                "merge_base_inferred": False,
                "rename_detection": "exact_content_one_to_one_only",
            },
            "comparability": {
                "metric_contracts_equal": True,
                "complexity_contracts_equal": True,
                "exclusion_policies_equal": True,
            },
            "counts": {
                "git_changed_files": 1,
                "changed_code_files": 1,
                "non_code_files": 0,
                "by_change_kind": {
                    "added": 0, "deleted": 0, "modified": 1,
                    "renamed_exact": 0, "type_changed": 0,
                },
                "added_diff_lines": 4,
                "deleted_diff_lines": 2,
            },
            "file_changes": [{
                "file_change_id": "fcc1_demo",
                "base_path": "src/app.py",
                "head_path": "src/app.py",
                "change_kind": "modified",
                "scope": "changed_code",
                "side_statuses": {
                    "base": {
                        "presence": "present", "scope_inclusion": "included",
                        "language": "Python", "scope_reason": None,
                    },
                    "head": {
                        "presence": "present", "scope_inclusion": "included",
                        "language": "Python", "scope_reason": None,
                    },
                },
                "hunks": {
                    "status": "complete",
                    "unavailable_reason": None,
                    "items": [{
                        "ordinal": 1,
                        "base": {"start_line": 10, "line_count": 2},
                        "head": {"start_line": 10, "line_count": 4},
                        "deleted_diff_lines": 2,
                        "added_diff_lines": 4,
                    }],
                },
                "evidence": {
                    "base": {"measurement_availability": "complete"},
                    "head": {"measurement_availability": "complete"},
                    "metric_observations": [{
                        "metric": "lines_of_code",
                        "family": "core_metrics",
                        "contract_version": "3.0.0",
                        "base": {"value": 10, "status": "complete"},
                        "head": {"value": 12, "status": "complete"},
                        "delta": 2,
                        "not_evaluable_reason": None,
                    }],
                },
                "affected_callables": {
                    "base": {
                        "availability": "complete",
                        "unavailable_reason": None,
                        "observations": [],
                    },
                    "head": {
                        "availability": "complete",
                        "unavailable_reason": None,
                        "observations": [{
                            "side": "head",
                            "callable_row_id": "row-1",
                            "path": "src/app.py",
                            "language": "Python",
                            "callable_kind": "module_function",
                            "name": "handle",
                            "qualified_name": "app.handle",
                            "signature_discriminator": None,
                            "start_line": 8,
                            "end_line": 20,
                            "body_start_line": 9,
                            "body_end_line": 20,
                            "overlaps": [{"hunk_ordinal": 1, "mode": "interval_intersection"}],
                            "structural_complexity_status": "complete",
                            "nloc_status": "complete",
                            "metrics": {
                                "nloc": 11, "formal_parameter_count": 2,
                                "cyclomatic_complexity": 3,
                                "decision_point_count": 2,
                                "boolean_operator_count": 0,
                                "max_condition_operator_count": 0,
                                "max_nesting_depth": 1,
                                "cognitive_complexity": 2,
                            },
                        }],
                    },
                    "identity_boundary": (
                        "side-local overlap observations only; base and head rows "
                        "are not matched"
                    ),
                },
            }],
            "repository_metric_evidence": {
                "base": {"core_metrics": []},
                "head": {"core_metrics": []},
                "deltas": [{
                    "metric": "lines_of_code",
                    "contract_version": "3.0.0",
                    "base": {"value": 100, "status": "complete"},
                    "head": {"value": 102, "status": "complete"},
                    "delta": 2,
                    "not_evaluable_reason": None,
                }],
            },
        }

    def test_rendering_is_byte_deterministic(self):
        document = self._document()
        self.assertEqual(
            render_markdown(document).encode("utf-8"),
            render_markdown(document).encode("utf-8"),
        )

    def test_no_forbidden_cross_revision_identity_claim_appears(self):
        text = render_markdown(self._document()).lower()
        for phrase in (
            "new function", "changed function", "added function",
            "removed function", "modified function", "renamed function",
            "new callable", "changed callable", "new method", "changed method",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_the_permitted_side_local_wording_is_used(self):
        text = render_markdown(self._document())
        self.assertIn("Head-side callables intersecting a changed hunk", text)
        self.assertIn("side-local observation", text)

    def test_no_absolute_host_path_or_timestamp_is_emitted(self):
        import re

        text = render_markdown(self._document())
        self.assertIsNone(re.search(r"[A-Za-z]:\\", text))
        self.assertIsNone(re.search(r"(?m)^/(?:home|Users|var|tmp)/", text))
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text))

    def test_file_sections_follow_the_documents_recorded_order(self):
        document = self._document()
        second = json.loads(json.dumps(document["file_changes"][0]))
        second["base_path"] = second["head_path"] = "src/aaa.py"
        second["file_change_id"] = "fcc1_second"
        document["file_changes"].append(second)
        document["counts"]["git_changed_files"] = 2
        document["counts"]["changed_code_files"] = 2
        document["counts"]["by_change_kind"]["modified"] = 2
        text = render_markdown(document)
        self.assertLess(text.index("src/app.py"), text.index("src/aaa.py"))

    def test_a_diagnostic_document_is_never_rendered_as_an_empty_diff(self):
        from modules.changed_code import diagnostic_document

        text = render_markdown(
            diagnostic_document(
                status="unavailable",
                reason="revision_not_found",
                base_requested="a" * 40,
                head_requested="b" * 40,
            )
        )
        self.assertIn("No comparison was produced", text)
        self.assertIn("not evidence that nothing changed", text)

    def test_a_backtick_in_a_value_cannot_break_out_of_its_code_span(self):
        from modules.changed_code import _code_span

        rendered = _code_span("weird`name")
        self.assertTrue(rendered.startswith("``"))
        self.assertIn("weird`name", rendered)

    def test_a_pipe_in_a_table_cell_is_escaped(self):
        from modules.changed_code import _code_span

        self.assertIn("\\|", _code_span("a|b"))
        self.assertNotIn("\\|", _code_span("a|b", table_cell=False))

    def test_a_control_character_is_rendered_visibly(self):
        from modules.changed_code import _code_span

        self.assertIn("\\u202e", _code_span("evil‮text"))


class DossierAuthorityTests(unittest.TestCase):
    """Two authorities, two admission gates, and no silent combination."""

    class _View:
        """The smallest surface `build_dossier` reads from a run."""

        def __init__(self, *, run_id="run-1", results=()):
            self.run_id = run_id
            self.manifest = MappingProxyType({
                "program_version": "3.8.0",
                "artifact_schema_version": "1.11.0",
                "metric_contract_version": "3.0.0",
                "complexity_contract_version": "2.0.0",
            })
            self.integrity_status = "completed"
            self.lifecycle = None
            self.compatibility = type("C", (), {"state": None})()
            self.repositories = tuple(MappingProxyType(item) for item in results)
            self.has_callable_artifact = False

        def stream_callables(self):
            return iter(())

    def _view(self):
        return self._View(results=[{
            "subject_key": "local:demo",
            "repository_url": None,
            "analysis_status": "complete",
            "core_metric_status": "complete",
            "acquisition": {"analyzed_commit_sha": "c" * 40},
            "analysis_scope_hash": "scope-1",
            "metrics": {
                "aggregate": {
                    **_composition(code=10, comment=2, blank=1),
                    "lines_of_code": 10,
                    "source_files": 1,
                    "classes_structs": 0,
                    "methods_functions": 1,
                },
                "by_language": {},
                "metric_contract_version": "3.0.0",
            },
        }])

    @staticmethod
    def _hotspot_row(path, *, complexity_signal, churn_signal):
        """One hotspot row that satisfies the real validator.

        Built to pass `validate_hotspot_document` rather than to look plausible:
        the classification must equal `classify_attention` of the two signals,
        and unavailable evidence may carry neither a signal nor a cohort.
        """
        from modules.hotspots import classify_attention

        def evidence(signal):
            return {
                "status": "measured",
                "signal": signal,
                "cohort": {"rank": 1, "distinct_values": 2},
            }

        return {
            "subject_key": "local:demo",
            "file": path,
            "complexity": {"status": "measured", "cognitive_complexity_total": 5},
            "churn": {"status": "measured", "commits": 3, "touched_lines": 40},
            "complexity_signal": evidence(complexity_signal),
            "churn_signal": evidence(churn_signal),
            "classification": classify_attention(complexity_signal, churn_signal),
            "reasons": ["both signals measured"],
        }

    def _hotspot_document(self, run_id="run-1"):
        from modules.hotspots import validate_hotspot_document

        document = {
            "format": "archlens-hotspots",
            "format_version": "1.0.0",
            "product_name": "ArchLens",
            "program_version": "3.8.0",
            "source_run": {
                "run_id": run_id,
                "program_version": "3.8.0",
                "artifact_schema_version": "1.11.0",
            },
            "purpose": "maintenance attention",
            "classification_model": {"score": None, "thresholds": None},
            "git_semantics": {},
            "ordering": "attention class",
            "repositories": [],
            "hotspots": [
                self._hotspot_row("a.py", complexity_signal="high", churn_signal="high"),
                self._hotspot_row("b.py", complexity_signal="high", churn_signal="high"),
                self._hotspot_row("c.py", complexity_signal="low", churn_signal="low"),
            ],
        }
        # The fixture must be a document the product would accept; otherwise the
        # admission tests would pass for the wrong reason.
        validate_hotspot_document(document)
        return document

    def test_the_dossier_declares_itself_derived(self):
        document = dossier.build_dossier(self._view())
        self.assertEqual(document["authority"]["this_document"], "derived")
        dossier.validate_dossier(document)

    def test_an_unsupplied_supplement_is_an_absence_of_input(self):
        document = dossier.build_dossier(self._view())
        for record in document["supplements"]:
            with self.subTest(kind=record["kind"]):
                self.assertEqual(record["admission"], dossier.NOT_SUPPLIED)
                self.assertIsNone(record["summary"])
                self.assertIn("absence of input", record["admission_meaning"])

    def test_a_matching_supplement_is_admitted_and_summarized(self):
        document = dossier.build_dossier(
            self._view(), hotspots=self._hotspot_document()
        )
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        self.assertEqual(record["admission"], dossier.ADMITTED)
        self.assertEqual(
            record["summary"]["by_attention_class"],
            {"high_attention": 2, "low_attention": 1},
        )
        self.assertEqual(record["summary"]["hotspot_row_count"], 3)

    def test_a_supplement_from_another_run_contributes_no_figures(self):
        document = dossier.build_dossier(
            self._view(), hotspots=self._hotspot_document(run_id="run-OTHER")
        )
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        self.assertEqual(record["admission"], dossier.PROVENANCE_MISMATCH)
        self.assertIsNone(record["summary"])
        self.assertNotIn("high_attention", dossier.render_markdown(document))

    def test_an_incompatible_contract_version_is_refused(self):
        supplement = self._hotspot_document()
        supplement["format_version"] = "9.9.9"
        document = dossier.build_dossier(self._view(), hotspots=supplement)
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        self.assertEqual(record["admission"], dossier.CONTRACT_INCOMPATIBLE)
        self.assertIsNone(record["summary"])

    def test_a_document_of_the_wrong_kind_is_refused(self):
        supplement = self._hotspot_document()
        supplement["format"] = "archlens-duplication"
        document = dossier.build_dossier(self._view(), hotspots=supplement)
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        self.assertEqual(record["admission"], dossier.CONTRACT_INCOMPATIBLE)

    def test_a_document_its_own_validator_rejects_is_refused(self):
        supplement = self._hotspot_document()
        del supplement["hotspots"]
        document = dossier.build_dossier(self._view(), hotspots=supplement)
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        self.assertEqual(record["admission"], dossier.VALIDATOR_REJECTED)
        self.assertIsNone(record["summary"])

    def test_an_unreadable_supplement_is_reported_not_raised(self):
        document = dossier.build_dossier(
            self._view(), read_errors={"duplication": "JSONDecodeError: bad"}
        )
        record = next(
            item for item in document["supplements"] if item["kind"] == "duplication"
        )
        self.assertEqual(record["admission"], dossier.UNREADABLE)
        self.assertIsNone(record["summary"])

    def test_validation_rejects_a_summary_on_a_refused_supplement(self):
        document = dossier.build_dossier(
            self._view(), hotspots=self._hotspot_document(run_id="run-OTHER")
        )
        record = next(
            item for item in document["supplements"] if item["kind"] == "hotspots"
        )
        record["summary"] = {"smuggled": 1}
        with self.assertRaises(dossier.DossierError):
            dossier.validate_dossier(document)

    def test_a_scope_hash_disagreement_blocks_admission_even_on_a_matching_commit(self):
        duplication = {
            "format": "archlens-duplication",
            "format_version": "1.0.0",
            "source": {
                "resolved_revision": "c" * 40,
                "analysis_scope_hash": "scope-DIFFERENT",
            },
        }
        matched, evidence = dossier._duplication_provenance(
            duplication, dossier._analyzed_scopes([dict(self._view().repositories[0])])
        )
        self.assertFalse(matched)
        self.assertIn("scope hash", evidence["reason"])

    def test_rendering_is_deterministic_and_emits_no_host_path(self):
        import re

        document = dossier.build_dossier(
            self._view(), hotspots=self._hotspot_document()
        )
        first = dossier.render_markdown(document)
        self.assertEqual(first, dossier.render_markdown(document))
        self.assertIsNone(re.search(r"[A-Za-z]:\\", first))
        self.assertNotIn("run_directory", first)

    def test_the_json_form_is_canonical_and_validated(self):
        document = dossier.build_dossier(self._view())
        text = dossier.canonical_json(document)
        self.assertEqual(json.loads(text)["format"], "archlens-dossier")
        self.assertTrue(text.endswith("\n"))

    def test_optional_sections_are_present_with_no_supplements_at_all(self):
        text = dossier.render_markdown(dossier.build_dossier(self._view()))
        for heading in ("## Authority", "## Run", "## Supplements", "## Repositories"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)

    def test_a_run_with_no_repository_reports_absence_not_an_empty_measurement(self):
        text = dossier.render_markdown(dossier.build_dossier(self._View()))
        self.assertIn("absence of measurement", text)

    def test_provenance_rules_are_published_for_every_supplement_kind(self):
        document = dossier.build_dossier(self._view())
        for record in document["supplements"]:
            with self.subTest(kind=record["kind"]):
                self.assertTrue(record["provenance_rule"])

    def test_no_cross_source_composite_is_produced(self):
        text = dossier.render_markdown(
            dossier.build_dossier(self._view(), hotspots=self._hotspot_document())
        ).lower()
        # Judgment vocabulary appears only inside the sentences that deny it, so
        # every declared denial is stripped before the document is scanned.
        for denial in (
            dossier.NO_SCORE_STATEMENT,
            dossier.COMPOSITION_STATEMENT,
            *source_composition.PROHIBITION_STATEMENTS,
            *complexity_distribution.PROHIBITION_STATEMENTS,
        ):
            text = text.replace(denial.lower(), "")
        for phrase in ("overall score", "health score", "composite score", "rating"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)


class PresentationDoesNotMeasureTests(unittest.TestCase):
    """The layer boundary: these modules read recorded evidence and nothing else."""

    def test_no_presentation_module_imports_a_parser_or_metric_engine(self):
        forbidden = ("tree_sitter", "modules.core_metrics", "modules.callable_analysis")
        for name in (
            "modules/source_composition.py",
            "modules/complexity_distribution.py",
            "modules/dossier.py",
        ):
            source = (REPOSITORY_ROOT / name).read_text(encoding="utf-8")
            for symbol in forbidden:
                with self.subTest(module=name, symbol=symbol):
                    self.assertNotIn(f"import {symbol}", source)
                    self.assertNotIn(f"from {symbol}", source)


if __name__ == "__main__":
    unittest.main()
