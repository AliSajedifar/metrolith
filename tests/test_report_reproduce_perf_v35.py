"""Phase 11-13 tests: HTML report, reproduction execution gates, performance."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.cli.performance_command import (
    COMPARABLE,
    INCOMPARABLE_ENVIRONMENT,
    INCOMPARABLE_SHAPE,
    build_profile,
    compare_profiles,
)
from modules.cli.report_command import escape_html, render_report
from modules.cli.reproduce_command import (
    LARGE_RUN_REPOSITORY_THRESHOLD,
    authorize_execution,
    preflight,
    selected_repositories,
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


class RequiresRun(unittest.TestCase):
    def setUp(self):
        source = find_run()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run"
        shutil.copytree(source, self.run)
        self.view = open_run(self.run)


# ---------------------------------------------------------- Phase 11 HTML ---

class HtmlEscapingTests(unittest.TestCase):
    def test_html_metacharacters_are_escaped(self):
        rendered = escape_html('<script>alert("x")</script>')
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_quotes_are_escaped_for_attribute_context(self):
        rendered = escape_html('a" onload="evil()')
        self.assertNotIn('"', rendered)

    def test_control_and_bidi_are_rendered_visibly(self):
        for hostile in ("\u202e", "\x1b", "\r"):
            with self.subTest(character=repr(hostile)):
                self.assertNotIn(hostile, escape_html(f"a{hostile}b"))

    def test_null_renders_as_a_distinct_marker(self):
        self.assertEqual("not supplied", escape_html(None))
        self.assertNotEqual(escape_html(None), escape_html(0))


class HtmlSecurityTests(RequiresRun):
    def setUp(self):
        super().setUp()
        self.document = render_report(self.view)

    def test_no_script_element_or_event_handler(self):
        lowered = self.document.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("javascript:", lowered)
        for handler in ("onload=", "onerror=", "onclick=", "onmouseover="):
            with self.subTest(handler=handler):
                self.assertNotIn(handler, lowered)

    def test_no_external_resource_of_any_kind(self):
        lowered = self.document.lower()
        for pattern in ("src=", "@import", "<link", "<iframe", "<object", "<embed"):
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, lowered)

    def test_no_external_scheme_href(self):
        for href in re.findall(r'href\s*=\s*"([^"]*)"', self.document, re.IGNORECASE):
            with self.subTest(href=href):
                self.assertFalse(href.lower().startswith(("http:", "https:", "//", "data:")))

    def test_no_absolute_host_path(self):
        self.assertNotIn(str(self.root), self.document)
        self.assertNotIn("D:\\", self.document)

    def test_no_raw_error_preview_is_reproduced(self):
        self.assertIn("not reproduced here", self.document)

    def test_document_is_self_contained_and_well_formed(self):
        self.assertTrue(self.document.startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", self.document)
        self.assertEqual(self.document.count("<table>"), self.document.count("</table>"))

    def test_report_states_it_is_non_authoritative(self):
        self.assertIn("non-authoritative", self.document)
        self.assertIn("run_status.json", self.document)

    def test_rendering_is_deterministic_apart_from_the_generation_stamp(self):
        second = render_report(open_run(self.run))
        strip = lambda text: re.sub(r"<dd>2\d{3}-.*?</dd>", "<dd/>", text)
        self.assertEqual(strip(self.document), strip(second))

    def test_tables_have_scoped_headers_for_accessibility(self):
        self.assertIn('scope="col"', self.document)
        self.assertIn('scope="row"', self.document)


class HostileContentTests(RequiresRun):
    def test_a_crafted_repository_url_cannot_inject_markup(self):
        path = self.run / "analysis.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload[0]["repository_url"] = '<script>alert(1)</script>'
        path.write_text(json.dumps(payload), encoding="utf-8")

        document = render_report(open_run(self.run))
        self.assertNotIn("<script>alert(1)</script>", document)
        self.assertIn("&lt;script&gt;", document)


# --------------------------------------------- Phase 12 execution gating ---

def _args(**overrides):
    base = dict(
        execute=True, mode="offline", scope="resolved-subset", repository=None,
        allow_network=False, confirm_large_run=False, output_root=None, workers=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class ExecutionAuthorizationTests(RequiresRun):
    def setUp(self):
        super().setUp()
        self.report = preflight(self.view)
        self.isolated = self.root / "isolated-output"

    def _refusal(self, **overrides):
        return authorize_execution(_args(**overrides), self.view, self.report)

    def test_execution_requires_an_explicit_mode(self):
        self.assertIn("--mode", self._refusal(mode=None, output_root=self.isolated))

    def test_frozen_requires_explicit_network_authorization(self):
        refusal = self._refusal(mode="frozen", output_root=self.isolated)
        self.assertIn("--allow-network", refusal)

    def test_frozen_is_authorized_once_network_is_explicit(self):
        self.assertIsNone(self._refusal(
            mode="frozen", allow_network=True, output_root=self.isolated
        ))

    def test_offline_never_falls_back_to_frozen(self):
        # Offline plus network authorization is refused rather than quietly
        # upgraded: an Offline reproduction that reached the network would be a
        # different experiment wearing the same name.
        refusal = self._refusal(
            mode="offline", allow_network=True, output_root=self.isolated
        )
        self.assertIn("not meaningful with --mode offline", refusal)

    def test_execution_requires_an_explicit_output_root(self):
        self.assertIn("--output-root", self._refusal(output_root=None))

    def test_output_root_must_be_isolated_from_the_source(self):
        refusal = self._refusal(output_root=self.run.parent.parent)
        self.assertIn("isolated", refusal)

    def test_output_root_may_not_contain_the_source_run(self):
        self.assertIsNotNone(self._refusal(output_root=self.run))

    def test_full_scope_requires_the_normalized_input_ledger(self):
        refusal = self._refusal(scope="full", output_root=self.isolated)
        self.assertIn("normalized_input.csv", refusal)

    def test_repository_scope_requires_at_least_one_repo(self):
        refusal = self._refusal(
            scope="repository", repository=None, output_root=self.isolated
        )
        self.assertIn("--repo", refusal)

    def test_large_run_requires_confirmation(self):
        many = [f"https://example.test/{index}" for index
                in range(LARGE_RUN_REPOSITORY_THRESHOLD + 1)]
        refusal = authorize_execution(
            _args(scope="repository", repository=many, output_root=self.isolated),
            self.view, self.report,
        )
        self.assertIn("--confirm-large-run", refusal)

    def test_confirmed_large_run_is_authorized(self):
        many = [f"https://example.test/{index}" for index
                in range(LARGE_RUN_REPOSITORY_THRESHOLD + 1)]
        self.assertIsNone(authorize_execution(
            _args(scope="repository", repository=many, output_root=self.isolated,
                  confirm_large_run=True),
            self.view, self.report,
        ))

    def test_a_blocked_preflight_refuses_execution(self):
        blocked = dict(self.report)
        blocked["assurance_state"] = "not_reproducible"
        refusal = authorize_execution(
            _args(output_root=self.isolated), self.view, blocked
        )
        self.assertIn("not_reproducible", refusal)

    def test_defaults_use_one_worker(self):
        self.assertEqual(_args().workers, 1)

    def test_refusal_never_starts_a_run(self):
        with patch("modules.benchmark_runner.run_benchmark") as runner:
            authorize_execution(_args(output_root=None), self.view, self.report)
        runner.assert_not_called()


class ScopeSelectionTests(RequiresRun):
    def test_resolved_subset_selects_the_recorded_repositories(self):
        selected = selected_repositories(_args(scope="resolved-subset"), self.view)
        self.assertEqual(set(selected), set(self.view.repositories_by_url))

    def test_full_scope_without_a_ledger_selects_nothing(self):
        self.assertEqual(selected_repositories(_args(scope="full"), self.view), [])

    def test_explicit_repository_scope_selects_exactly_what_was_named(self):
        selected = selected_repositories(
            _args(scope="repository", repository=["https://x/a", "https://x/b"]),
            self.view,
        )
        self.assertEqual(selected, ["https://x/a", "https://x/b"])


# ------------------------------------------------- Phase 13 performance ---

class PerformanceProfileTests(RequiresRun):
    def test_profile_records_raw_samples_not_only_summaries(self):
        profile = build_profile(self.view)
        self.assertGreater(profile["sample_count"], 0)
        self.assertEqual(len(profile["raw_samples"]), profile["sample_count"])

    def test_profile_is_never_blocking(self):
        self.assertFalse(build_profile(self.view)["blocking"])

    def test_host_class_carries_no_hostname_or_username(self):
        host = build_profile(self.view)["host_class"]
        import getpass
        import socket

        self.assertNotIn(socket.gethostname().lower(), host.lower())
        self.assertNotIn(getpass.getuser().lower(), host.lower())

    def test_timing_definition_names_the_normative_clock(self):
        definition = build_profile(self.view)["timing_definition"]
        self.assertIn("run_started_at", definition)
        self.assertIn("measurement_finished_at", definition)

    def test_profile_hash_is_stable_across_builds(self):
        self.assertEqual(
            build_profile(self.view)["profile_hash"],
            build_profile(open_run(self.run))["profile_hash"],
        )

    def test_identical_profiles_compare_as_comparable_and_within_noise(self):
        profile = build_profile(self.view)
        report = compare_profiles(profile, profile)
        self.assertEqual(report["comparability_verdict"], COMPARABLE)
        self.assertTrue(report["within_noise_floor"])
        self.assertFalse(report["blocking"])

    def test_different_cohorts_refuse_comparison(self):
        baseline = build_profile(self.view)
        candidate = dict(baseline, cohort_or_case_hash="0" * 64)
        report = compare_profiles(baseline, candidate)
        self.assertEqual(report["comparability_verdict"], INCOMPARABLE_SHAPE)

    def test_different_host_class_refuses_comparison(self):
        baseline = build_profile(self.view)
        candidate = dict(baseline, host_class="OtherOS-other-cpython3.13")
        report = compare_profiles(baseline, candidate)
        self.assertEqual(report["comparability_verdict"], INCOMPARABLE_ENVIRONMENT)

    def test_different_worker_count_refuses_comparison(self):
        baseline = build_profile(self.view)
        report = compare_profiles(baseline, dict(baseline, workers=8))
        self.assertEqual(report["comparability_verdict"], INCOMPARABLE_SHAPE)

    def test_a_regression_is_reported_but_never_blocking(self):
        baseline = build_profile(self.view)
        candidate = dict(baseline, median_seconds=baseline["median_seconds"] + 100)
        report = compare_profiles(baseline, candidate)
        self.assertFalse(report["within_noise_floor"])
        self.assertFalse(report["blocking"])
        self.assertIn("Report-only", report["note"])


class TimingContractTests(RequiresRun):
    def test_timing_is_excluded_from_the_semantic_payload(self):
        from validation.scripts.validate_outputs import semantic_payload

        payload = json.dumps(semantic_payload(self.run), sort_keys=True)
        for field in (
            "repository_duration_seconds", "measurement_wall_seconds",
            "finalization_seconds", "run_wall_seconds",
            "finalization_finished_at", "measurement_finished_at",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, payload)

    def test_performance_profile_absence_does_not_fail_validation(self):
        from validation.scripts.validate_outputs import validate_run

        self.assertFalse((self.run / "performance_profile.json").exists())
        report = validate_run(self.run)
        self.assertTrue(report["passed"], report.get("failures"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
