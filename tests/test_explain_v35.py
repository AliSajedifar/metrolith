"""Phase 6 tests: ``archlens explain`` (plan section 12)."""

import json
import unittest
from pathlib import Path

from modules.cli.explain_command import (
    CROSS_LANGUAGE_CAVEAT,
    EXPLANATION_FORMAT_VERSION,
    GROUP_ORDER,
    ONLY_CHOICES,
    build_explanation,
    render_text,
    sanitize_terminal,
)
from modules.diagnostics import project_run
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


class ContractTests(unittest.TestCase):
    def test_only_choices_match_the_plan_vocabulary(self):
        self.assertEqual(
            set(ONLY_CHOICES),
            {
                "invalid-artifacts", "unavailable-measurements",
                "complexity-unavailable",
                "expected-family-failed", "expected-family-partial",
                "multiple", "secondary-language-only", "all",
            },
        )


class SanitizationTests(unittest.TestCase):
    """Hostile artifact text must never reach a terminal raw."""

    def test_bidi_override_is_rendered_visibly(self):
        # U+202E can visually reverse everything after it in a terminal.
        rendered = sanitize_terminal("safe\u202eevil")
        self.assertNotIn("\u202e", rendered)
        self.assertIn("\\u202e", rendered)

    def test_carriage_return_and_escape_are_rendered_visibly(self):
        for hostile in ("\r", "\x1b", "\x07", "\u200b"):
            with self.subTest(character=repr(hostile)):
                rendered = sanitize_terminal(f"a{hostile}b")
                self.assertNotIn(hostile, rendered)

    def test_ordinary_text_and_newlines_survive(self):
        self.assertEqual(sanitize_terminal("plain text"), "plain text")
        self.assertEqual(sanitize_terminal("a\nb\tc"), "a\nb\tc")

    def test_non_ascii_content_is_preserved(self):
        self.assertEqual(sanitize_terminal("café — 日本語"), "café — 日本語")


class _FakeDiagnostic:
    """Minimal stand-in so grouping can be tested without a run directory."""

    def __init__(self, url, **kwargs):
        from modules.diagnostics import ExplanationCompleteness

        self.repository_url = url
        self.analysis_status = kwargs.get("analysis_status", "complete")
        self.core_metric_status = self.analysis_status
        self.metric_statuses = kwargs.get("metric_statuses", {"loc_status": "complete"})
        self.expected_language = kwargs.get("expected_language", "Java")
        self.expected_language_family_status = kwargs.get("family_status", "complete")
        self.expected_language_mismatch = False
        self.partial_origin = kwargs.get("partial_origin", "none")
        self.git_mode_map_state = "available"
        # Cognitive complexity is its own attention dimension from G2-C on.
        # Defaulting to `absent` keeps every existing case meaning what it
        # meant: absent is deliberately NOT an attention group.
        self.cognitive_state = kwargs.get("cognitive_state", "absent")
        self.cognitive_metric_name = "ArchLens Cognitive Complexity"
        self.parser_compatibility_strategies = ()
        self.recorded_error_count = 0
        self.recorded_recovery_count = 0
        self.evidence = ()
        self.unknown_categories = ()
        self.explanation_completeness = kwargs.get(
            "completeness", ExplanationCompleteness.COMPLETE
        )
        self.evaluable_dimensions = {"inventory": True}
        # Complexity Contract 1.0.0 state. Defaults to `complete` so the
        # existing grouping cases keep testing what they were written to test;
        # `failed` and `absent` are exercised explicitly where they matter.
        self.complexity_state = kwargs.get("complexity_state", "complete")
        self.complexity_contract_version = kwargs.get(
            "complexity_contract_version", "1.0.0"
        )
        self.complexity_unavailable_reason = kwargs.get(
            "complexity_unavailable_reason"
        )


class _FakeProjection:
    def __init__(self, repositories):
        self.run_id = "test-run"
        self.lifecycle = "finalized_valid"
        # Decodability and success are separate dimensions; `explain` reports
        # both, so the double has to carry both.
        self.run_integrity_status = "completed"
        self.artifact_schema_compatibility = {}
        self.repositories = tuple(repositories)
        self.run_evidence = ()
        self.provenance_warnings = ()
        self.evaluable_dimensions = {"inventory": True}


class GroupingTests(unittest.TestCase):
    def _groups(self, projection, **kwargs):
        payload = build_explanation(projection, **kwargs)
        return {
            group["group"]: [entry["repository_url"] for entry in group["entries"]]
            for group in payload["groups"]
        }

    def test_every_group_is_reachable(self):
        from modules.diagnostics import ExplanationCompleteness

        projection = _FakeProjection([
            _FakeDiagnostic(
                "https://x/invalid",
                completeness=ExplanationCompleteness.INSUFFICIENT_EVIDENCE,
            ),
            _FakeDiagnostic(
                "https://x/unavailable", analysis_status="failed",
                metric_statuses={"loc_status": "failed"}, family_status="complete",
            ),
            _FakeDiagnostic("https://x/family-failed", family_status="failed"),
            _FakeDiagnostic("https://x/family-partial", family_status="partial"),
            _FakeDiagnostic(
                "https://x/complexity-unavailable", complexity_state="failed",
                complexity_unavailable_reason="all_files_failed_parse",
            ),
            _FakeDiagnostic(
                "https://x/multiple", analysis_status="partial",
                partial_origin="multiple",
            ),
            _FakeDiagnostic(
                "https://x/secondary", analysis_status="partial",
                partial_origin="secondary_supported_language_only",
            ),
        ])
        groups = self._groups(projection)
        self.assertEqual(groups["invalid-artifacts"], ["https://x/invalid"])
        self.assertEqual(groups["unavailable-measurements"], ["https://x/unavailable"])
        self.assertEqual(
            groups["complexity-unavailable"], ["https://x/complexity-unavailable"]
        )
        self.assertEqual(groups["expected-family-failed"], ["https://x/family-failed"])
        self.assertEqual(groups["expected-family-partial"], ["https://x/family-partial"])
        self.assertEqual(groups["multiple"], ["https://x/multiple"])
        self.assertEqual(groups["secondary-language-only"], ["https://x/secondary"])

    def test_a_repository_lands_in_exactly_one_group(self):
        projection = _FakeProjection([
            _FakeDiagnostic("https://x/a", family_status="partial",
                            analysis_status="partial", partial_origin="multiple"),
        ])
        placements = [
            url for urls in self._groups(projection).values() for url in urls
        ]
        self.assertEqual(placements, ["https://x/a"])

    def test_complete_repositories_appear_in_no_group(self):
        projection = _FakeProjection([_FakeDiagnostic("https://x/clean")])
        self.assertEqual(
            [url for urls in self._groups(projection).values() for url in urls], []
        )

    def test_only_filter_restricts_the_output(self):
        projection = _FakeProjection([
            _FakeDiagnostic("https://x/family-failed", family_status="failed"),
            _FakeDiagnostic("https://x/family-partial", family_status="partial"),
        ])
        groups = self._groups(projection, only="expected-family-failed")
        self.assertEqual(list(groups), ["expected-family-failed"])

    def test_repository_filter_matches_canonical_url(self):
        projection = _FakeProjection([
            _FakeDiagnostic("https://x/a", family_status="failed"),
            _FakeDiagnostic("https://x/b", family_status="failed"),
        ])
        groups = self._groups(projection, repository="https://x/a")
        self.assertEqual(groups["expected-family-failed"], ["https://x/a"])

    def test_unmatched_repository_filter_is_reported(self):
        projection = _FakeProjection([_FakeDiagnostic("https://x/a")])
        payload = build_explanation(projection, repository="https://x/absent")
        self.assertTrue(payload["repository_filter_unmatched"])


class WordingTests(unittest.TestCase):
    def test_grouping_is_labelled_as_attention_not_severity(self):
        payload = build_explanation(_FakeProjection([]))
        semantics = payload["grouping_semantics"].lower()
        self.assertIn("attention", semantics)
        self.assertIn("not a severity ranking", semantics)
        self.assertIn("not a research-impact ranking", semantics)

    def test_forbidden_wording_never_appears(self):
        projection = _FakeProjection([
            _FakeDiagnostic("https://x/a", family_status="failed"),
        ])
        text = render_text(build_explanation(projection)).lower()
        for forbidden in ("harmless", "safe to ignore", "caused by", "you should"):
            with self.subTest(phrase=forbidden):
                self.assertNotIn(forbidden, text)

    def test_cross_language_caveat_appears_only_when_families_are_mixed(self):
        single = _FakeProjection([
            _FakeDiagnostic("https://x/a", family_status="failed", expected_language="Java"),
        ])
        mixed = _FakeProjection([
            _FakeDiagnostic("https://x/a", family_status="failed", expected_language="Java"),
            _FakeDiagnostic("https://x/b", family_status="failed", expected_language="Go"),
        ])
        self.assertIsNone(
            build_explanation(single)["cross_language_comparability_caveat"]
        )
        self.assertEqual(
            build_explanation(mixed)["cross_language_comparability_caveat"],
            CROSS_LANGUAGE_CAVEAT,
        )


class PreservedRunExplainTests(unittest.TestCase):
    def setUp(self):
        self.run = find_run()
        self.projection = project_run(open_run(self.run))
        self.payload = build_explanation(self.projection)

    def test_format_version_is_declared(self):
        self.assertEqual(
            self.payload["explanation_format_version"], EXPLANATION_FORMAT_VERSION
        )

    def test_the_failed_control_is_grouped_as_expected_family_failed(self):
        groups = {
            group["group"]: [entry["repository_url"] for entry in group["entries"]]
            for group in self.payload["groups"]
        }
        self.assertIn("https://github.com/7ep/demo", groups["expected-family-failed"])

    def test_output_is_deterministic(self):
        second = build_explanation(project_run(open_run(self.run)))
        self.assertEqual(
            json.dumps(self.payload, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )
        self.assertEqual(render_text(self.payload), render_text(second))

    def test_text_and_json_carry_the_same_repository_set(self):
        text = render_text(self.payload)
        for group in self.payload["groups"]:
            for entry in group["entries"]:
                with self.subTest(repository=entry["repository_url"]):
                    self.assertIn(entry["repository_url"], text)

    def test_repository_scoped_evidence_reports_scope_instead_of_a_path(self):
        text = render_text(self.payload)
        for group in self.payload["groups"]:
            for entry in group["entries"]:
                for item in entry["evidence"]:
                    if item["relative_path"] is None:
                        self.assertIn(f"({item['evidence_scope']} scope)", text)
                        return

    def test_missing_evidence_is_named_as_not_evaluable(self):
        text = render_text(self.payload)
        self.assertIn("Not evaluable because evidence is missing", text)

    def test_no_absolute_host_path_appears_in_the_output(self):
        text = render_text(self.payload)
        self.assertNotIn(str(REPOSITORY), text)
        self.assertNotIn("D:\\", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
