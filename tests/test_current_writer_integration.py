"""Current-writer integration harness: real Artifact 1.6 writer, real consumers.

Every earlier "does this work against a real run" test bound to a *preserved*
Artifact 1.4 fixture, so passing proved the compatibility-decode path worked and
said nothing about whether today's writer produces output today's consumers can
read. This harness closes that: it drives the production pipeline and artifact
writer, then walks the whole consumer chain over what it wrote.

    production writer
      -> strict reader
      -> validate
      -> explain
      -> report
      -> compare --explain
      -> reproduce preflight
      -> performance profile

**Tier 1** is a small Python source tree and is completely hermetic: no network,
and every root — output, cache, worktrees, workspace — is inside a fresh
temporary directory, so nothing can be satisfied by a stray cache or a run
directory left behind by another test.

**Tier 2** adds one small case per remaining language family (Java, Go,
JavaScript, TypeScript). Its purpose is producer-to-consumer integration across
families, **not** re-testing metric semantics — the conformance corpus owns
that, and duplicating it here would create two places to update when a metric
definition legitimately changes. Tier 2 therefore asserts integration
properties: the run is produced, it validates literally, and every consumer
reads it.

Artifact 1.6 runs produced here must validate with **zero schema violations and
zero compatibility waivers**. A waiver would mean the 1.6 corrections did not
land, or that a new producer defect appeared.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import ARTIFACT_SCHEMA_VERSION, AnalysisConfig
from validation.artifact_io.compatibility import RunLifecycle
from validation.artifact_io.reader import open_run
from validation.scripts.semantic_projection import measurement_semantic_hash

ANALYZED_SHA = "a" * 40

#: One small case per language family. Deliberately minimal: these exist to
#: prove the writer/consumer seam works for the family, not to measure anything.
LANGUAGE_CASES: dict[str, dict[str, str]] = {
    "Python": {
        "app.py": "class App:\n    def run(self):\n        return 1\n",
    },
    "Java": {
        "Service.java": (
            "package demo;\n\n"
            "public class Service {\n"
            "    public int run() { return 1; }\n"
            "}\n"
        ),
    },
    "Go": {
        "main.go": (
            "package main\n\n"
            "type Service struct{}\n\n"
            "func (s Service) Run() int { return 1 }\n\n"
            "func main() {}\n"
        ),
    },
    "JavaScript": {
        "app.js": (
            "export class Service {\n"
            "  run() {\n"
            "    return 1;\n"
            "  }\n"
            "}\n"
        ),
    },
    "TypeScript": {
        "app.ts": (
            "export class Service {\n"
            "  run(): number {\n"
            "    return 1;\n"
            "  }\n"
            "}\n"
        ),
    },
}


class HermeticHarness(unittest.TestCase):
    """Builds runs through the real writer with every root inside a temp dir."""

    def _isolated_root(self, label: str) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix=f"archlens_int_{label}_")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    @staticmethod
    @contextmanager
    def _acquire_local(repo: Path, spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            repo,
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

    def build_run(self, language: str, label: str, root: Path | None = None) -> Path:
        """Produce one run for `language` with every root inside a temp dir."""
        root = root or self._isolated_root(label)
        repo = root / "source"
        repo.mkdir(parents=True, exist_ok=True)
        for name, text in LANGUAGE_CASES[language].items():
            (repo / name).write_text(text, encoding="utf-8")

        # The repository URL is derived from the *language*, not the label, so
        # two runs of the same language are two measurements of the same
        # subject. Keying it on the label would make every pair a different
        # subject, and the equivalence oracle below would compare nothing.
        source_csv = root / "repositories.csv"
        source_csv.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/{language.lower()},monolith,{language},"
            f"{ANALYZED_SHA},true,x\n",
            encoding="utf-8",
        )

        # Every root is inside `root`. Nothing may be satisfied by a cache or an
        # output directory discovered outside this tree.
        config = AnalysisConfig.from_env(
            workspace=root,
            output_root=root / "output",
            cache_root=root / "cache",
            temporary_directory=root / "worktrees",
            workers=1,
        )

        def acquire(spec, config_, mode="latest", progress=None):
            return self._acquire_local(repo, spec, config_, mode, progress)

        with (
            patch("modules.benchmark_runner.acquire_repository", acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                [source_csv], config, "offline", command_line_arguments=["integration"]
            )
        return Path(summary["run_directory"])

    # -- the consumer chain, each stage asserted -----------------------------

    def assert_strict_reader(self, run: Path):
        view = open_run(run)
        view.materialize_diagnostics()
        self.assertEqual(
            [f"{e.artifact}: {e.message}" for e in view.structural_errors], []
        )
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertTrue(view.succeeded)
        return view

    def assert_validate_literally_clean(self, run: Path):
        from modules.cli.validate_command import schema_only_report

        report = schema_only_report(run)
        self.assertEqual(report["schema_contract_evaluated"], ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(report["violations"], [])
        self.assertEqual(
            report["accepted_compatibility_exceptions"], [],
            "an Artifact 1.6 run required a 1.5 compatibility waiver; the "
            "corrections did not land, or a new producer defect appeared",
        )
        self.assertEqual(report["result"], "schema_valid")
        return report

    def assert_semantic_validation(self, run: Path):
        from validation.scripts.validate_outputs import validate_run

        report = validate_run(run)
        self.assertTrue(report["passed"], report["failures"])
        return report

    def assert_explain(self, view):
        from modules.cli.explain_command import build_explanation, render_text
        from modules.diagnostics import project_run
        from validation.artifact_io.schema_store import validate_document

        payload = build_explanation(project_run(view))
        self.assertEqual(
            validate_document("explain_output", payload, "explain.json"), []
        )
        self.assertTrue(render_text(payload).strip())
        return payload

    def assert_report(self, view, destination: Path):
        from modules.cli.report_command import render_report

        html = render_report(view)
        self.assertGreater(len(html), 0)
        destination.write_text(html, encoding="utf-8")

        # The static report's defining property: nothing is *fetched* at view
        # time. The check targets resource-loading constructs specifically —
        # a bare "https://" is not evidence of anything, because repository URLs
        # legitimately appear as report content, and asserting on the substring
        # would fail on correct output while catching nothing real.
        for construct in (
            "<script src=", "<link rel=\"stylesheet\"", "<iframe", "<img src=",
            "@import", "url(http",
        ):
            self.assertNotIn(
                construct, html, f"report loads an external resource: {construct}"
            )
        return html

    def assert_compare_explain(self, left: Path, right: Path):
        from modules.cli.compare_command import build_comparison
        from validation.artifact_io.schema_store import validate_document

        payload = build_comparison(open_run(left), open_run(right))
        self.assertEqual(
            validate_document("compare_output", payload, "compare.json"), []
        )
        self.assertEqual(payload["invalid_artifacts"], [])
        return payload

    def assert_reproduce_preflight(self, view):
        from modules.cli.reproduce_command import preflight
        from validation.artifact_io.schema_store import validate_document

        payload = preflight(view)
        self.assertEqual(
            validate_document("reproduction_output", payload, "reproduction.json"), []
        )
        # Preflight is read-only by contract.
        self.assertFalse(payload["wrote_anything"])
        self.assertFalse(payload["execution_attempted"])
        checks = {item["check"]: item for item in payload["checks"]}
        self.assertEqual(checks["run_integrity_status"]["outcome"], "passed")
        return payload

    def assert_performance_profile(self, view):
        from modules.cli.performance_command import build_profile
        from validation.artifact_io.schema_store import validate_document

        payload = build_profile(view)
        self.assertEqual(
            validate_document("performance_profile", payload, "performance.json"), []
        )
        return payload

    def walk_consumer_chain(self, run: Path, second: Path, destination: Path):
        """Every stage, in order, over one produced run."""
        view = self.assert_strict_reader(run)
        self.assert_validate_literally_clean(run)
        self.assert_semantic_validation(run)
        self.assert_explain(view)
        self.assert_report(view, destination)
        self.assert_compare_explain(run, second)
        self.assert_reproduce_preflight(view)
        self.assert_performance_profile(view)


class Tier1PythonHermeticTests(HermeticHarness):
    """Small Python tree, fully hermetic, whole chain."""

    def test_the_full_consumer_chain_reads_what_the_writer_produced(self):
        root = self._isolated_root("tier1")
        run = self.build_run("Python", "tier1a", root=root / "a")
        second = self.build_run("Python", "tier1b", root=root / "b")
        self.walk_consumer_chain(run, second, root / "report.html")

    def test_the_run_declares_the_current_artifact_schema(self):
        run = self.build_run("Python", "declares")
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)

    def test_two_runs_of_the_same_source_are_semantically_equivalent(self):
        """P2 as the equivalence oracle across independent executions."""
        first = self.build_run("Python", "oracle_a")
        second = self.build_run("Python", "oracle_b")
        self.assertNotEqual(first, second)
        self.assertEqual(
            measurement_semantic_hash(first), measurement_semantic_hash(second)
        )

    def test_nothing_is_written_outside_the_temporary_root(self):
        """Hermeticity, asserted rather than assumed.

        A harness that quietly used the repository's own output or cache root
        would pass while proving nothing about a clean machine.
        """
        root = self._isolated_root("hermetic")
        run = self.build_run("Python", "hermetic", root=root)
        self.assertTrue(
            run.is_relative_to(root),
            f"run directory {run} escaped the temporary root {root}",
        )
        for expected in ("output", "cache", "worktrees", "source"):
            self.assertTrue((root / expected).exists(), f"{expected} not under root")


class Tier2LanguageFamilyTests(HermeticHarness):
    """One case per remaining family: integration, not metric semantics."""

    def _family(self, language: str):
        root = self._isolated_root(language.lower())
        run = self.build_run(language, f"{language.lower()}_a", root=root / "a")
        second = self.build_run(language, f"{language.lower()}_b", root=root / "b")

        self.walk_consumer_chain(run, second, root / "report.html")

        # The family was actually measured; otherwise the chain above would be
        # exercising an empty run and proving nothing about the parser seam.
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        metrics = analysis[0]["metrics"]
        self.assertEqual(
            metrics["primary_language_name"], language,
            f"{language} case did not measure as {language}",
        )
        self.assertGreater(metrics["aggregate"]["source_files"], 0)
        self.assertGreater(metrics["aggregate"]["lines_of_code"], 0)
        self.assertEqual(analysis[0]["analysis_status"], "complete")

        # And two independent runs of it agree.
        self.assertEqual(
            measurement_semantic_hash(run), measurement_semantic_hash(second)
        )

    def test_java(self):
        self._family("Java")

    def test_go(self):
        self._family("Go")

    def test_javascript(self):
        self._family("JavaScript")

    def test_typescript(self):
        self._family("TypeScript")


if __name__ == "__main__":
    unittest.main()
