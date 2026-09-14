"""Two-stage preflight: capability granularity, and a real cohort barrier.

The behaviour this replaces: a run in an environment with no Java grammar
produced **one ``parser_execution_failure`` per Java file** — forty files,
forty identical diagnostics — as though forty source files were individually
broken. One environment fact, reported forty times, at the wrong granularity,
after the whole repository had been acquired and walked.

Two properties matter most here, and both have a dedicated test:

* :class:`CohortBarrierTests` — **zero** repositories may enter metric parsing
  before the cohort capability barrier succeeds. A per-repository check would
  let subject 1 and 2 be measured and subject 3 discover the missing grammar,
  producing a mixed, environment-dependent partial measurement.
* :class:`DiagnosticGranularityTests` — the original forty-file reproduction,
  now inverted: one capability refusal, zero file-level environment failures.
  And, just as important, a *real* file-level parser failure must still be
  reported normally when the parser itself is healthy.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules import preflight
from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.preflight import (
    CapabilityCheck,
    PreflightRefused,
    PreflightReport,
    probe_parser_capabilities,
    stage_a,
    stage_b,
)
from modules.vocabularies import (
    CapabilityReason,
    CapabilityState,
    PreflightStage,
    parser_capability,
)

ANALYZED_SHA = "a" * 40

JAVA_SOURCE = "package a;\npublic class Service%d { public void run() {} }\n"
PYTHON_SOURCE = "class App:\n    def run(self):\n        return 1\n"


def unavailable(capability: str) -> CapabilityCheck:
    return CapabilityCheck(
        capability=capability,
        state=CapabilityState.UNAVAILABLE,
        blocking=False,
        evidence="simulated: grammar not installed",
        reason=CapabilityReason.GRAMMAR_UNAVAILABLE,
    )


def available(capability: str) -> CapabilityCheck:
    return CapabilityCheck(
        capability=capability,
        state=CapabilityState.AVAILABLE,
        blocking=False,
        evidence="simulated: available",
    )


def probes_without(*missing: str) -> dict[str, CapabilityCheck]:
    """Real probe set with specific capabilities forced unavailable."""
    probes = dict(probe_parser_capabilities())
    for capability in missing:
        probes[capability] = unavailable(capability)
    return probes


class CohortFixture(unittest.TestCase):
    """Builds cohorts of synthetic repositories through the real pipeline."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_preflight_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repositories: dict[str, Path] = {}
        self.parsed: list[str] = []

    def add_repository(self, name: str, files: dict[str, str]) -> str:
        path = self.root / "sources" / name
        path.mkdir(parents=True, exist_ok=True)
        for filename, text in files.items():
            target = path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        url = f"https://github.com/acme/{name}"
        self.repositories[url] = path
        return url

    def write_input(self, entries: list[tuple[str, str]]) -> Path:
        """`entries` is (url, expected_language). Empty language means none."""
        lines = ["url,architecture_type,expected_language,commit_sha,enabled,notes"]
        for url, language in entries:
            lines.append(f"{url},monolith,{language},{ANALYZED_SHA},true,x")
        source = self.root / "repositories.csv"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return source

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            self.repositories[spec.url],
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

    def run_cohort(self, source: Path, *, missing: tuple[str, ...] = ()):
        """Run with specific parser capabilities forced unavailable.

        `compute_repository_metrics` is wrapped to record which repositories
        entered metric parsing, which is what the barrier property is asserted
        against.
        """
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
            workers=1,
        )
        probes = probes_without(*missing) if missing else None

        import modules.benchmark_runner as runner_module

        original_metrics = runner_module.compute_repository_metrics

        def recording_metrics(inventory, *args, **kwargs):
            self.parsed.append(str(inventory.root))
            return original_metrics(inventory, *args, **kwargs)

        patches = [
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
            patch.object(runner_module, "compute_repository_metrics", recording_metrics),
        ]
        if probes is not None:
            patches.append(
                patch("modules.preflight.probe_parser_capabilities", lambda: probes)
            )
        with patches[0], patches[1], patches[2]:
            if probes is not None:
                with patches[3]:
                    return run_benchmark(
                        [source], config, "offline", command_line_arguments=["preflight"]
                    )
            return run_benchmark(
                [source], config, "offline", command_line_arguments=["preflight"]
            )


# ------------------------------------------------------------------ stage A ---


class StageAGlobalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_stage_a_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
        )

    def test_a_healthy_environment_is_not_refused(self):
        report = stage_a(self.config, acquisition_mode="offline")
        self.assertFalse(report.refused, report.summary())
        self.assertIs(report.stage, PreflightStage.GLOBAL)

    def test_wrong_jsonschema_version_refuses(self):
        with patch(
            "validation.artifact_io.schema_store.jsonschema_version",
            return_value="4.25.0",
        ):
            report = stage_a(self.config, acquisition_mode="offline")
        self.assertTrue(report.refused)
        refusal = next(c for c in report.refusals if c.capability == "schema_runtime")
        self.assertIs(refusal.reason, CapabilityReason.DEPENDENCY_VERSION_MISMATCH)
        # The message names the capability once and carries no traceback.
        self.assertIn("schema_runtime", report.summary())
        self.assertNotIn("Traceback", report.summary())

    def test_missing_jsonschema_refuses(self):
        with patch(
            "validation.artifact_io.schema_store.jsonschema_version",
            return_value=None,
        ):
            report = stage_a(self.config, acquisition_mode="offline")
        refusal = next(c for c in report.refusals if c.capability == "schema_runtime")
        self.assertIs(refusal.reason, CapabilityReason.DEPENDENCY_MISSING)

    def test_unusable_referencing_refuses(self):
        real_import = preflight.importlib.import_module

        def fail_referencing(name, *args, **kwargs):
            if name == "referencing":
                raise ImportError("simulated: referencing unavailable")
            return real_import(name, *args, **kwargs)

        with patch.object(preflight.importlib, "import_module", fail_referencing):
            report = stage_a(self.config, acquisition_mode="offline")
        refusal = next(
            c for c in report.refusals if c.capability == "schema_reference_runtime"
        )
        self.assertIs(refusal.reason, CapabilityReason.DEPENDENCY_MISSING)

    def test_missing_git_refuses_when_acquisition_needs_it(self):
        def no_git(*args, **kwargs):
            raise FileNotFoundError("git")

        with patch.object(preflight.subprocess, "run", no_git):
            report = stage_a(self.config, acquisition_mode="latest")
        refusal = next(c for c in report.refusals if c.capability == "git")
        self.assertIs(refusal.reason, CapabilityReason.GIT_UNAVAILABLE)
        self.assertEqual(refusal.required_by, ("acquisition_mode=latest",))

    def test_build_is_never_required(self):
        """`build` is packaging tooling and must never block an analysis."""
        report = stage_a(self.config, acquisition_mode="offline")
        capabilities = {check.capability for check in report.checks}
        self.assertNotIn("build", capabilities)
        self.assertFalse(report.refused)

    def test_no_grammar_is_blocking_at_stage_a(self):
        report = stage_a(self.config, acquisition_mode="offline")
        for check in report.checks:
            if check.capability.endswith("_parser"):
                with self.subTest(capability=check.capability):
                    self.assertFalse(
                        check.blocking,
                        "a grammar blocked before acquisition; Stage B owns that "
                        "decision because expected_language is not evidence",
                    )

    def test_expected_language_is_recorded_as_anticipated_not_blocking(self):
        report = stage_a(
            self.config, acquisition_mode="offline", expected_languages=["Java"]
        )
        java = next(
            c for c in report.checks if c.capability == parser_capability("Java")
        )
        self.assertFalse(java.blocking)
        self.assertTrue(any("anticipated" in item for item in java.required_by))

    def test_source_tree_execution_is_not_reported_as_version_agreement(self):
        """The egg-info trap: metadata resolving to the working tree."""
        report = stage_a(self.config, acquisition_mode="offline")
        installation = next(
            c for c in report.checks if c.capability == "installation"
        )
        self.assertIn(
            installation.state,
            {CapabilityState.NOT_EVALUATED, CapabilityState.AVAILABLE,
             CapabilityState.WARNING},
        )
        if installation.state is CapabilityState.NOT_EVALUATED:
            self.assertIs(
                installation.reason, CapabilityReason.NO_INDEPENDENT_INSTALLATION
            )
            self.assertNotIn("agreement", installation.evidence)
        self.assertFalse(installation.blocking, "running from source must be allowed")

    def test_free_space_is_evidence_not_a_threshold(self):
        report = stage_a(self.config, acquisition_mode="offline")
        space = next(c for c in report.checks if c.capability == "free_space")
        self.assertFalse(space.blocking, "a guessed free-space minimum must not block")
        self.assertRegex(space.evidence, r"\d+ bytes free")

    def test_symlink_capability_is_reported_but_never_blocking(self):
        report = stage_a(self.config, acquisition_mode="offline")
        symlink = next(
            c for c in report.checks if c.capability == "native_symlink_creation"
        )
        self.assertFalse(symlink.blocking)

    def test_unwritable_path_refuses(self):
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
        )
        with patch.object(preflight.os, "access", return_value=False):
            report = stage_a(config, acquisition_mode="offline")
        self.assertTrue(report.refused)
        self.assertTrue(
            any(c.capability.endswith("_writable") for c in report.refusals)
        )


# ------------------------------------------------------------------ stage B ---


class RequirementDerivationTests(CohortFixture):
    """Requirements come from canonical source selection, not an extension scan."""

    def _inventory(self, files: dict[str, str]):
        from modules.inventory import RepositoryInventory

        path = self.root / "derive"
        path.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        config = AnalysisConfig.from_env(workspace=self.root)
        return RepositoryInventory(path, config, full_inventory=False)

    def test_python_requires_no_tree_sitter_capability(self):
        required = preflight.required_parser_capabilities(
            self._inventory({"app.py": PYTHON_SOURCE})
        )
        self.assertEqual(required, {})

    def test_java_source_requires_the_java_parser(self):
        required = preflight.required_parser_capabilities(
            self._inventory({"Service.java": JAVA_SOURCE % 1})
        )
        self.assertEqual(sorted(required), [parser_capability("Java")])

    def test_excluded_sources_create_no_requirement(self):
        """Vendored trees are not measured, so they demand no grammar."""
        inventory = self._inventory({
            "app.py": PYTHON_SOURCE,
            "node_modules/pkg/Vendored.java": JAVA_SOURCE % 2,
        })
        included = [r.relative_path for r in inventory if r.included_in_metrics]
        self.assertNotIn(
            "node_modules/pkg/Vendored.java", included,
            "fixture assumption broken: the vendored file was not excluded",
        )
        self.assertEqual(preflight.required_parser_capabilities(inventory), {})

    def test_unsupported_languages_create_no_requirement(self):
        required = preflight.required_parser_capabilities(
            self._inventory({"notes.md": "# text\n", "data.csv": "a,b\n1,2\n"})
        )
        self.assertEqual(required, {})


class StageBDecisionTests(unittest.TestCase):
    def test_a_missing_required_capability_refuses(self):
        report = stage_b(
            {parser_capability("Java"): {"https://github.com/acme/x"}},
            probes={parser_capability("Java"): unavailable(parser_capability("Java"))},
        )
        self.assertTrue(report.refused)
        refusal = report.refusals[0]
        self.assertEqual(refusal.capability, "java_parser")
        self.assertIs(refusal.reason, CapabilityReason.GRAMMAR_UNAVAILABLE)
        self.assertEqual(refusal.required_by, ("https://github.com/acme/x",))

    def test_a_missing_capability_nothing_requires_does_not_refuse(self):
        report = stage_b(
            {},
            probes={parser_capability("Go"): unavailable(parser_capability("Go"))},
        )
        self.assertFalse(report.refused)
        go = next(c for c in report.checks if c.capability == "go_parser")
        self.assertIs(go.state, CapabilityState.NOT_REQUIRED)

    def test_available_required_capabilities_do_not_refuse(self):
        capability = parser_capability("Java")
        report = stage_b({capability: {"x"}}, probes={capability: available(capability)})
        self.assertFalse(report.refused)

    def test_the_barrier_is_skipped_only_when_nothing_can_refuse(self):
        self.assertFalse(preflight.barrier_can_refuse({
            "a": available("a"), "b": available("b"),
        }))
        self.assertTrue(preflight.barrier_can_refuse({
            "a": available("a"), "b": unavailable("b"),
        }))


class StageBIntegrationTests(CohortFixture):
    def test_python_only_subject_with_go_grammar_missing_is_allowed(self):
        url = self.add_repository("pyonly", {"app.py": PYTHON_SOURCE})
        source = self.write_input([(url, "Python")])
        summary = self.run_cohort(source, missing=(parser_capability("Go"),))
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(len(self.parsed), 1)

    def test_python_only_subject_with_no_tree_sitter_at_all_is_allowed(self):
        url = self.add_repository("pyonly2", {"app.py": PYTHON_SOURCE})
        source = self.write_input([(url, "Python")])
        summary = self.run_cohort(source, missing=tuple(
            parser_capability(language) for language in preflight.TREE_SITTER_LANGUAGES
        ))
        self.assertEqual(summary["status"], "completed")

    def test_java_scope_with_java_grammar_missing_refuses(self):
        url = self.add_repository("javaonly", {"Service.java": JAVA_SOURCE % 1})
        source = self.write_input([(url, "Java")])
        with self.assertRaises(PreflightRefused) as caught:
            self.run_cohort(source, missing=(parser_capability("Java"),))
        report = caught.exception.report
        self.assertIs(report.stage, PreflightStage.CAPABILITY_BARRIER)
        self.assertEqual([c.capability for c in report.refusals], ["java_parser"])
        self.assertEqual(self.parsed, [], "metric parsing began despite the barrier")

    def test_expected_java_but_discovered_python_only_is_not_refused(self):
        """`expected_language` is an expectation, not evidence.

        Refusing here would stop ArchLens discovering that the repository is
        actually Python — which is exactly the mismatch it exists to measure.
        """
        url = self.add_repository("mislabelled", {"app.py": PYTHON_SOURCE})
        source = self.write_input([(url, "Java")])
        summary = self.run_cohort(source, missing=(parser_capability("Java"),))
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(len(self.parsed), 1)

    def test_expected_python_but_java_actually_present_is_refused(self):
        """The opposite direction: a real secondary language must be caught."""
        url = self.add_repository("secondary", {
            "app.py": PYTHON_SOURCE,
            "Service.java": JAVA_SOURCE % 1,
        })
        source = self.write_input([(url, "Python")])
        with self.assertRaises(PreflightRefused) as caught:
            self.run_cohort(source, missing=(parser_capability("Java"),))
        self.assertEqual(
            [c.capability for c in caught.exception.report.refusals], ["java_parser"]
        )
        self.assertEqual(self.parsed, [])

    def test_vendored_java_does_not_make_the_java_grammar_required(self):
        url = self.add_repository("vendored", {
            "app.py": PYTHON_SOURCE,
            "node_modules/pkg/Vendored.java": JAVA_SOURCE % 3,
        })
        source = self.write_input([(url, "Python")])
        summary = self.run_cohort(source, missing=(parser_capability("Java"),))
        self.assertEqual(summary["status"], "completed")

    def test_all_capabilities_available_proceeds_normally(self):
        url = self.add_repository("healthy", {
            "app.py": PYTHON_SOURCE, "Service.java": JAVA_SOURCE % 1,
        })
        source = self.write_input([(url, "Java")])
        summary = self.run_cohort(source)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(len(self.parsed), 1)


class CohortBarrierTests(CohortFixture):
    """The property the whole phase exists for.

    An early subject is perfectly measurable and a later one needs a grammar
    that is missing. A per-repository check would measure the early subject
    first and refuse partway through, leaving a mixed, environment-dependent
    partial measurement. The barrier must refuse before **any** subject is
    parsed.
    """

    def test_zero_repositories_are_parsed_before_the_barrier_refuses(self):
        first = self.add_repository("measurable", {"app.py": PYTHON_SOURCE})
        second = self.add_repository("needs_java", {"Service.java": JAVA_SOURCE % 1})
        third = self.add_repository("also_python", {"other.py": PYTHON_SOURCE})
        source = self.write_input([
            (first, "Python"), (second, "Java"), (third, "Python"),
        ])

        with self.assertRaises(PreflightRefused) as caught:
            self.run_cohort(source, missing=(parser_capability("Java"),))

        self.assertEqual(
            self.parsed, [],
            "a repository entered metric parsing before the cohort barrier; "
            "this is the mixed partial measurement the barrier exists to prevent",
        )
        report = caught.exception.report
        self.assertEqual([c.capability for c in report.refusals], ["java_parser"])
        self.assertEqual(report.refusals[0].required_by, (second,))

    def test_no_run_is_published_and_the_pointer_does_not_move(self):
        first = self.add_repository("ok", {"app.py": PYTHON_SOURCE})
        second = self.add_repository("java", {"Service.java": JAVA_SOURCE % 1})
        source = self.write_input([(first, "Python"), (second, "Java")])

        with self.assertRaises(PreflightRefused):
            self.run_cohort(source, missing=(parser_capability("Java"),))

        output = self.root / "output"
        self.assertFalse(
            (output / "latest_run.json").exists(), "latest_run advanced on refusal"
        )
        runs = list((output / "runs").glob("*")) if (output / "runs").is_dir() else []
        self.assertEqual(runs, [], "a run directory was published on refusal")

    def test_stage_b_does_not_claim_nothing_was_acquired(self):
        """Acquisition genuinely happened; the report must not pretend otherwise."""
        url = self.add_repository("java2", {"Service.java": JAVA_SOURCE % 1})
        source = self.write_input([(url, "Java")])
        with self.assertRaises(PreflightRefused) as caught:
            self.run_cohort(source, missing=(parser_capability("Java"),))
        self.assertIs(
            caught.exception.report.stage, PreflightStage.CAPABILITY_BARRIER
        )


class DiagnosticGranularityTests(CohortFixture):
    """The original reproduction, inverted."""

    def test_forty_java_files_produce_one_refusal_and_no_file_diagnostics(self):
        files = {f"src/Service{index}.java": JAVA_SOURCE % index for index in range(40)}
        url = self.add_repository("forty", files)
        source = self.write_input([(url, "Java")])

        with self.assertRaises(PreflightRefused) as caught:
            self.run_cohort(source, missing=(parser_capability("Java"),))

        report = caught.exception.report
        self.assertEqual(
            len(report.refusals), 1,
            "one missing grammar must produce exactly one capability refusal",
        )
        refusal = report.refusals[0]
        self.assertEqual(refusal.capability, "java_parser")
        self.assertIs(refusal.reason, CapabilityReason.GRAMMAR_UNAVAILABLE)
        self.assertEqual(refusal.required_by, (url,))

        # Zero file-level parser failures caused by the environment, because no
        # file was ever parsed.
        self.assertEqual(self.parsed, [])
        summary_text = report.summary()
        self.assertNotIn("Service0.java", summary_text)
        self.assertEqual(summary_text.count("java_parser"), 1)

    def test_a_healthy_parser_still_reports_a_genuinely_malformed_file(self):
        """The distinction that matters: environment vs file.

        Once the parser capability is healthy, a file that genuinely cannot be
        parsed must still produce a normal file-level diagnostic. Suppressing
        those would trade one defect for a worse one.
        """
        url = self.add_repository("mixed_java", {
            "Good.java": JAVA_SOURCE % 1,
            "Broken.java": "package a; public class Broken { !!! }\n",
        })
        source = self.write_input([(url, "Java")])
        summary = self.run_cohort(source)
        self.assertEqual(len(self.parsed), 1, "measurement did not run")

        run = Path(summary["run_directory"])
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        metrics = analysis[0]["metrics"]
        diagnostics = (metrics.get("parser_diagnostics") or []) + (
            metrics.get("recovered_parser_diagnostics") or []
        )
        self.assertTrue(
            diagnostics,
            "a malformed source file produced no file-level diagnostic; the "
            "capability barrier must not suppress real parser findings",
        )
        self.assertTrue(
            any("Broken.java" in str(item.get("file_path")) for item in diagnostics)
        )


class ReportShapeTests(unittest.TestCase):
    def test_reports_are_machine_readable_even_when_refusing(self):
        report = stage_b(
            {parser_capability("Java"): {"x"}},
            probes={parser_capability("Java"): unavailable(parser_capability("Java"))},
        )
        payload = json.loads(json.dumps(report.as_dict()))
        self.assertTrue(payload["refused"])
        self.assertEqual(payload["refusals"], ["java_parser"])
        entry = next(c for c in payload["checks"] if c["capability"] == "java_parser")
        self.assertEqual(entry["reason"], "grammar_unavailable")
        self.assertEqual(entry["state"], "unavailable")

    def test_the_reason_is_not_encoded_inside_the_state(self):
        for state in CapabilityState:
            with self.subTest(state=state.value):
                self.assertNotIn("_because_", state.value)
                self.assertLessEqual(len(state.value.split("_")), 2)

    def test_a_dedicated_exit_code_is_distinct(self):
        from modules.cli.compare_command import EXIT_USAGE
        from modules.cli.explain_command import EXIT_INVALID_ARTIFACTS

        self.assertNotIn(
            preflight.EXIT_PREFLIGHT_REFUSED, {0, 1, EXIT_USAGE, EXIT_INVALID_ARTIFACTS}
        )


class DoctorSharesTheModelTests(unittest.TestCase):
    """One capability model, two questions."""

    def test_doctor_reports_capabilities_without_requiring_them(self):
        import pipeline

        temporary = tempfile.TemporaryDirectory(prefix="archlens_doctor_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = AnalysisConfig.from_env(
            workspace=root,
            output_root=root / "output",
            cache_root=root / "cache",
            temporary_directory=root / "worktrees",
        )
        report = pipeline.doctor_report(config)
        payload = json.loads(json.dumps(report))
        self.assertIn("checks", payload)
        self.assertIn("blocking_failures", payload)
        self.assertEqual(payload["stage"], PreflightStage.GLOBAL.value)
        # Grammar states are reported; none of them decides `healthy`.
        parsers = [c for c in payload["checks"] if c["capability"].endswith("_parser")]
        self.assertTrue(parsers)
        self.assertTrue(all(not c["blocking"] for c in parsers))


if __name__ == "__main__":
    unittest.main()
