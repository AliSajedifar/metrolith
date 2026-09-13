"""Finalization self-validation gate (audit C-0).

``RunArtifacts.finalize`` used to derive run integrity from write failures alone.
Nothing asked whether the artifacts it had just emitted could be read back, so a
run whose ``sheet_metrics.csv`` violated its own published contract was recorded
``completed`` / ``complete`` and only failed later, in four separate downstream
commands, with no trace of the problem in the run's own status.

The gate closes that. These tests pin the three properties that make it
trustworthy rather than merely present:

1. a run the strict reader rejects is **never** finalized ``completed``;
2. ``measurement_outcome`` is untouched by it, because "how much was measured" is
   a different question from "are the artifacts readable" (plan section 4.5);
3. it stays cheap — the expensive artifacts are not read.
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
from modules.config import AnalysisConfig

ANALYZED_SHA = "a" * 40

# Artifacts finalization must not pull in. `catalog.csv` is one row per source
# file and the ledger is unbounded at cohort scale; reading either here would
# turn the gate into a second full pass over the run.
EXPENSIVE_ARTIFACTS = (
    "catalog.csv", "errors.csv", "recoveries.csv", "contributions.csv",
)


class FinalizationSelfValidationTests(unittest.TestCase):
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

    def _run(self, output="output"):
        config = AnalysisConfig.from_env(
            output_root=self.root / output,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            return run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )

    @staticmethod
    def _status(run: Path) -> dict:
        return json.loads((run / "run_status.json").read_text(encoding="utf-8"))

    # -- T-4: positive and non-regressive -----------------------------------

    def test_clean_run_still_finalizes_completed_and_records_the_pass(self):
        summary = self._run()
        run = Path(summary["run_directory"])
        status = self._status(run)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["measurement_outcome"], "complete")
        self.assertEqual(status["mandatory_output_failures"], [])
        self.assertTrue(manifest["self_validation"]["passed"])
        self.assertEqual(manifest["self_validation"]["problems"], [])
        self.assertEqual(manifest["run_integrity_status"], "completed")

    def test_gate_reads_every_artifact_including_the_expensive_ones(self):
        """Reverses the earlier decision to keep the gate on the eager set.

        Staying lazy meant an invalid ``catalog.csv``, ``errors.csv``,
        ``recoveries.csv`` or contribution ledger escaped the gate purely
        because nothing had read it yet: the artifact was broken, the run
        finalized ``completed``, and the fault surfaced in whichever downstream
        command opened the run first. A gate that inspects only what is cheap
        does not establish readability.

        The cost is real and accepted — finalization now makes a second full
        pass over the run — so it is asserted rather than left implicit.
        """
        from validation.artifact_io import paths as paths_module

        opened: list[str] = []
        original = paths_module.resolve_artifact

        def recording(run_directory, relative, **kwargs):
            opened.append(str(relative))
            return original(run_directory, relative, **kwargs)

        with patch.object(paths_module, "resolve_artifact", recording):
            with patch("validation.artifact_io.reader.resolve_artifact", recording):
                self._run()

        for artifact in EXPENSIVE_ARTIFACTS:
            self.assertIn(
                artifact, opened,
                f"finalization self-validation never read {artifact}, so an "
                f"invalid one would escape the gate",
            )

    # -- T-3: negative -------------------------------------------------------

    def test_unreadable_artifact_prevents_a_completed_finalization(self):
        """Corrupt one emitted artifact between emission and the gate.

        ``language_metrics.csv`` is in the reader's eager set and is written well
        before the gate runs, so replacing its bytes reproduces exactly the
        situation C-1 created: every measurement succeeded, but the run is not
        readable.
        """
        from modules import run_artifacts as module

        original = module.RunArtifacts._self_validation_problems

        def corrupt_then_validate(self, candidate_status_path=None, candidate_manifest_path=None):
            target = self.run_dir / "language_metrics.csv"
            if target.is_file():
                # A header the contract does not declare: a structural fault the
                # strict reader must refuse, not a measurement disagreement.
                target.write_text("not_a_declared_column\nvalue\n", encoding="utf-8")
            return original(self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            module.RunArtifacts, "_self_validation_problems", corrupt_then_validate
        ):
            summary = self._run()

        run = Path(summary["run_directory"])
        status = self._status(run)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))

        self.assertNotEqual(
            status["status"], "completed",
            "a run the strict reader rejects must never finalize as completed",
        )
        self.assertEqual(status["status"], "failed")
        self.assertEqual(manifest["run_integrity_status"], "failed")

        # The reason must be recorded, not merely reflected in a status code.
        self.assertFalse(manifest["self_validation"]["passed"])
        self.assertTrue(manifest["self_validation"]["problems"])
        self.assertTrue(
            any("self_validation" in item for item in status["mandatory_output_failures"]),
            status["mandatory_output_failures"],
        )

        # Measurement availability is a separate question and must be unchanged.
        self.assertEqual(status["measurement_outcome"], "complete")

        # summary.md is rendered after the gate, so it must not claim completion.
        summary_text = (run / "summary.md").read_text(encoding="utf-8")
        self.assertIn("- Final run state: failed", summary_text)
        self.assertIn("- Measurement outcome: complete", summary_text)

    def test_latest_run_pointer_is_not_advanced_to_a_failed_run(self):
        from modules import run_artifacts as module

        original = module.RunArtifacts._self_validation_problems

        def corrupt_then_validate(self, candidate_status_path=None, candidate_manifest_path=None):
            target = self.run_dir / "language_metrics.csv"
            if target.is_file():
                target.write_text("not_a_declared_column\nvalue\n", encoding="utf-8")
            return original(self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            module.RunArtifacts, "_self_validation_problems", corrupt_then_validate
        ):
            summary = self._run()

        pointer = Path(summary["run_directory"]).parent.parent / "latest_run.json"
        self.assertFalse(
            pointer.exists(),
            "latest_run.json must not point at a run that failed self-validation",
        )


class ImportDirectionTests(unittest.TestCase):
    """T-12. The gate makes `modules` depend on `validation.artifact_io`.

    That direction is only safe while the dependency stays one-way. A reverse
    import would create a cycle and, worse, would let the reader's behaviour be
    influenced by the producer it is supposed to check independently.
    """

    def test_artifact_io_never_imports_modules(self):
        import ast

        package = Path(__file__).resolve().parent.parent / "validation" / "artifact_io"
        offenders: list[str] = []
        for source in sorted(package.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == "modules" or name.startswith("modules."):
                        offenders.append(f"{source.name}:{node.lineno} imports {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
