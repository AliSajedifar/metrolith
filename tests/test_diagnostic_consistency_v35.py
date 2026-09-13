"""Diagnostic determinism and cross-surface agreement (audit C-4, C-5, C-6, C-7).

One run directory used to yield four different verdicts depending on which
command was asked: ``summary.md`` said ``completed``, ``explain`` said
``finalized_invalid`` and exited 0 while showing no reason at all, ``reproduce``
and ``compare --explain`` exited 3, and ``report`` rendered a normal-looking page
and exited 0. On top of that the diagnostic projection was not even stable for a
single command, because it snapshotted a lazily-populated warning list before the
lazy work had run.

These tests pin the two invariants that make the diagnostics usable:

* a projection is a function of the run directory, not of property access order;
* every surface reaches the same validity verdict and the same exit code.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from modules.cli import explain_command, report_command, reproduce_command
from modules.diagnostics import project_run
from tests import historical_fixtures
from validation.artifact_io.reader import open_run

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _reference_run() -> Path:
    """The mandatory tracked run used for clean-checkout diagnostics coverage."""
    return historical_fixtures.run_for("1.5.0")


class ProjectionDeterminismTests(unittest.TestCase):
    """C-4. Access order must not change what the projection reports."""

    def setUp(self):
        source = _reference_run()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name) / "run"
        shutil.copytree(source, self.run)
        # Remove every optional projection, so their absence is what must be
        # reported consistently.
        shutil.rmtree(self.run / "file_inventory", ignore_errors=True)
        shutil.rmtree(self.run / "repositories", ignore_errors=True)
        (self.run / "normalized_input.csv").unlink(missing_ok=True)

    def test_projection_is_independent_of_property_access_order(self):
        cold = project_run(open_run(self.run))

        warm = open_run(self.run)
        # Touch the lazy dimensions first: this used to add warnings the cold
        # projection had already missed.
        _ = warm.inventories, warm.repository_documents, warm.normalized_input
        preheated = project_run(warm)

        self.assertEqual(cold.as_dict(), preheated.as_dict())

    def test_repeated_projection_on_one_view_is_stable(self):
        view = open_run(self.run)
        self.assertEqual(project_run(view).as_dict(), project_run(view).as_dict())

    def test_every_absent_optional_projection_is_reported(self):
        projection = project_run(open_run(self.run))
        absent = [
            item for item in projection.run_evidence
            if item.category == "optional_projection_absent"
        ]
        artifacts = {item.source_artifact for item in absent}
        self.assertEqual(
            artifacts, {"file_inventory/", "repositories/", "normalized_input.csv"},
            "a missing projection must be visible, not silently dropped",
        )

    def test_lifecycle_does_not_depend_on_lazy_reads(self):
        cold = open_run(self.run)
        cold_lifecycle = cold.lifecycle

        warm = open_run(self.run)
        warm.materialize_diagnostics()
        self.assertEqual(cold_lifecycle, warm.lifecycle)

    def test_materialization_leaves_the_expensive_artifacts_unread(self):
        view = open_run(self.run)
        view.materialize_diagnostics()
        for artifact in ("catalog.csv", "errors.csv", "recoveries.csv"):
            self.assertNotIn(
                artifact, view.reader._tables,
                f"materialize_diagnostics must not read {artifact}",
            )


class CrossSurfaceAgreementTests(unittest.TestCase):
    """C-5, C-7, T-13. Every surface must reach the same verdict."""

    def setUp(self):
        source = _reference_run()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.valid = self.root / "valid"
        shutil.copytree(source, self.valid)
        self.invalid = self.root / "invalid"
        shutil.copytree(source, self.invalid)
        # An eager table that no longer satisfies its contract.
        (self.invalid / "language_metrics.csv").write_text(
            "bogus_column\nvalue\n", encoding="utf-8"
        )

    def _cli(self, *arguments: str) -> int:
        import sys

        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "pipeline.py"), *arguments],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=300,
        )
        return completed.returncode

    def test_all_surfaces_reject_an_invalid_run_with_the_same_code(self):
        codes = {
            "explain": self._cli("explain", str(self.invalid)),
            "reproduce": self._cli("reproduce", str(self.invalid)),
            "report": self._cli(
                "report", str(self.invalid), "--output", str(self.root / "a.html")
            ),
            "compare": self._cli(
                "compare", str(self.invalid), str(self.valid), "--explain"
            ),
        }
        self.assertEqual(
            codes,
            {key: reproduce_command.EXIT_INVALID_ARTIFACTS for key in codes},
            "surfaces disagree about whether this run is usable",
        )

    def test_report_override_suppresses_only_the_exit_code(self):
        destination = self.root / "override.html"
        code = self._cli(
            "report", str(self.invalid), "--output", str(destination),
            "--allow-invalid-artifacts",
        )
        self.assertEqual(code, report_command.EXIT_OK)
        # The banner is not optional, only the exit code is.
        self.assertIn('class="invalid"', destination.read_text(encoding="utf-8"))

    def test_valid_run_is_accepted_everywhere(self):
        self.assertEqual(self._cli("explain", str(self.valid)), explain_command.EXIT_OK)
        self.assertEqual(self._cli("reproduce", str(self.valid)), reproduce_command.EXIT_READY)
        self.assertEqual(
            self._cli("report", str(self.valid), "--output", str(self.root / "b.html")),
            report_command.EXIT_OK,
        )

    def test_explain_reports_why_a_run_is_invalid(self):
        projection = project_run(open_run(self.invalid))
        payload = explain_command.build_explanation(projection)

        self.assertTrue(
            payload["run_evidence"],
            "explain must not report an invalid lifecycle with no evidence",
        )
        rendered = explain_command.render_text(payload)
        self.assertIn("Run-scoped recorded evidence", rendered)
        self.assertIn("language_metrics.csv", rendered)


class OfflineCacheReadinessTests(unittest.TestCase):
    """C-6. Probe the repository's own mirror, never the cache root."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.url = "https://github.com/acme/mono"
        self.other = "https://github.com/acme/other"

    def _git(self, *arguments: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *arguments], cwd=str(cwd), capture_output=True, text=True, timeout=60
        )

    def _mirror_with_commit(self, url: str) -> str:
        """Create a bare mirror at the derived path and return its commit SHA."""
        from modules.acquisition import cache_path_for_url

        work = self.root / f"work_{abs(hash(url))}"
        work.mkdir()
        # Content must differ per repository. Identical trees committed in the
        # same second by the same author produce the *same* SHA, which would
        # make the unrelated-mirror test pass for the wrong reason.
        (work / "file.txt").write_text(f"{url}\n", encoding="utf-8")
        self._git("init", "-q", cwd=work)
        self._git("config", "user.email", "t@example.com", cwd=work)
        self._git("config", "user.name", "t", cwd=work)
        self._git("add", "-A", cwd=work)
        self._git("commit", "-q", "-m", f"commit for {url}", cwd=work)
        sha = self._git("rev-parse", "HEAD", cwd=work).stdout.strip()

        mirror = cache_path_for_url(self.cache, url)
        self._git("clone", "-q", "--bare", str(work), str(mirror), cwd=self.root)
        return sha

    def _view(self, url: str, sha: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            manifest={"effective_configuration": {"cache_root": str(self.cache)}},
            repositories=[
                {"repository_url": url, "acquisition": {"analyzed_commit_sha": sha}}
            ],
        )

    def test_commit_in_its_own_mirror_passes(self):
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        sha = self._mirror_with_commit(self.url)
        check = reproduce_command._offline_cache_check(self._view(self.url, sha))
        self.assertEqual(check["outcome"], reproduce_command.PASSED, check["detail"])

    def test_commit_only_in_an_unrelated_mirror_does_not_pass(self):
        """A commit elsewhere in the cache does not make this repository ready."""
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        sha = self._mirror_with_commit(self.other)
        # The requested repository has a mirror, but not that commit.
        self._mirror_with_commit(self.url)
        check = reproduce_command._offline_cache_check(self._view(self.url, sha))
        self.assertEqual(check["outcome"], reproduce_command.FAILED, check["detail"])

    def test_absent_mirror_is_not_evaluable_rather_than_failed(self):
        check = reproduce_command._offline_cache_check(self._view(self.url, "a" * 40))
        self.assertEqual(check["outcome"], reproduce_command.NOT_EVALUABLE, check["detail"])


if __name__ == "__main__":
    unittest.main()
