"""Producer/consumer round-trip over the artifact contract (audit C-1, C-0).

Every other test in this suite either builds a run directory by hand and reads it
back, or runs the pipeline and inspects raw CSV. Neither direction catches the
one failure that matters most: **ArchLens writing an artifact that ArchLens
cannot read.**

That gap is not hypothetical. Artifact Schema 1.5 declared
``sheet_metrics.expected_language`` non-nullable because the runs the schema was
derived from happened to populate it, while ``run_artifacts._catalog_row`` writes
``""`` whenever the benchmark input omits it. Every run containing such a
repository was finalized ``completed`` and then rejected as
``finalized_invalid`` by ArchLens's own strict reader, breaking ``explain``,
``reproduce``, ``reproduce --execute``, and ``compare --explain``. The full test
suite passed throughout, because every hand-built fixture sets the field.

These tests close that loop: run the real pipeline, then open the result with the
strict reader. ``expected_language`` is exercised **both** absent and populated,
because a test that only covered the absent case could be satisfied by declaring
every column nullable, which would destroy the contract instead of fixing it.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from validation.artifact_io.compatibility import RunLifecycle
from validation.artifact_io.contracts import TABULAR_ARTIFACTS, contract_for
from validation.artifact_io.reader import open_run

ANALYZED_SHA = "a" * 40


class ArtifactContractRoundTripTests(unittest.TestCase):
    """The pipeline's own output must satisfy the reader's own contract."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )

    # -- harness ------------------------------------------------------------

    def _input_csv(self, expected_language: str) -> Path:
        """One repository row. ``expected_language`` may deliberately be empty."""
        path = self.root / f"repositories_{expected_language or 'absent'}.csv"
        path.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,{expected_language},"
            f"{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )
        return path

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

    def _run(self, expected_language: str, output: str) -> Path:
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
            summary = run_benchmark(
                [self._input_csv(expected_language)],
                config,
                "offline",
                command_line_arguments=["test"],
            )
        return Path(summary["run_directory"])

    # -- T-1 / T-2 ----------------------------------------------------------

    def test_run_without_expected_language_is_readable_by_the_strict_reader(self):
        """T-1. The exact configuration that shipped broken.

        The input omits ``expected_language``, so ``_catalog_row`` writes an
        empty cell. The reader must accept it and decode it as *unavailable*.
        """
        run = self._run("", "output_absent")
        view = open_run(run)

        self.assertEqual(
            [item.as_dict() for item in view.structural_errors], [],
            "the pipeline wrote artifacts its own strict reader rejects",
        )
        self.assertIs(view.lifecycle, RunLifecycle.FINALIZED_VALID)

        # Empty must mean "unavailable" — never the empty string, never zero.
        row = view.sheet_metrics[0]
        self.assertIsNone(row["expected_language"])
        self.assertNotEqual(row["expected_language"], "")
        self.assertNotEqual(row["expected_language"], 0)

    def test_run_with_expected_language_is_readable_and_keeps_the_value(self):
        """T-2. The contract must still carry a supplied value.

        Without this, T-1 could be satisfied by making every column nullable,
        which would remove the check rather than correct it.
        """
        run = self._run("Python", "output_present")
        view = open_run(run)

        self.assertEqual([item.as_dict() for item in view.structural_errors], [])
        self.assertIs(view.lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertEqual(view.sheet_metrics[0]["expected_language"], "Python")

    def test_expected_language_column_is_still_required(self):
        """Nullable cell, required column. Dropping the column stays an error."""
        for artifact in ("sheet_metrics.csv", "catalog.csv"):
            with self.subTest(artifact=artifact):
                column = next(
                    item for item in contract_for(artifact, "1.5.0").columns
                    if item.name == "expected_language"
                )
                self.assertTrue(column.nullable, "the cell must be optional")
                self.assertTrue(column.required, "the column must still be present")

    # -- T-11 ---------------------------------------------------------------

    def test_no_emitted_empty_cell_lands_in_a_non_nullable_column(self):
        """T-11. Drift guard, driven off what the writer actually emits.

        Rather than restating the writer's field list — which would drift — this
        reads the real artifacts from both runs and asserts that every column the
        pipeline left empty is declared nullable. A future ``or ""`` added to a
        non-nullable column fails here instead of in production.
        """
        offenders: list[str] = []
        for expected_language, output in (("", "drift_absent"), ("Python", "drift_present")):
            run = self._run(expected_language, output)
            for artifact in TABULAR_ARTIFACTS:
                path = run / artifact
                if not path.is_file():
                    continue
                contract = contract_for(artifact, "1.5.0")
                with path.open(encoding="utf-8", newline="") as handle:
                    for number, row in enumerate(csv.DictReader(handle), start=2):
                        for name, value in row.items():
                            column = contract.column(name)
                            if value != "" or column is None:
                                continue
                            if not (column.nullable or column.allow_empty_string):
                                offenders.append(
                                    f"{artifact} row {number} column {name!r} is empty "
                                    f"but the contract declares it non-nullable"
                                )
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
