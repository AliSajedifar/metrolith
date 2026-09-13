"""`reproduce --execute` truthfulness and isolation (audit C-2, C-3).

Two defects motivated these tests, and both concerned a command that performs
side effects while describing itself as read-only.

**C-2.** ``render_text`` printed "This preflight is read-only. It wrote nothing,
opened no network connection, created no worktree, and ran no parser." *after*
``--execute`` had re-run the benchmark and written a complete run directory. It
showed no mode, no reproduction directory, and no status, so a ``--format text``
caller had no way to learn from the command's own report that anything had run.
``execute()`` also forced ``wrote_anything = False``.

**C-3.** The isolation guard tested only whether the destination was an
*ancestor* of the source, so a *descendant* was accepted and ``--output-root
<run_dir>/nested`` wrote a run tree inside the immutable source run directory.

The rule these pin down: every claim the command makes about what it did must be
derived from observed state, never from which flags were passed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.cli import reproduce_command


class OutputRootIsolationTests(unittest.TestCase):
    """C-3. Containment must be refused in both directions."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "source" / "runs" / "20260101T000000Z_n001_abc"
        self.run_dir.mkdir(parents=True)
        self.output_root = self.root / "source"

        self.view = SimpleNamespace(
            run_directory=self.run_dir,
            normalized_input=({"canonical_url": "https://github.com/a/b"},),
            repositories_by_url={"https://github.com/a/b": {}},
        )
        self.report = {"assurance_state": reproduce_command.STATE_COMPATIBLE}

    def _authorize(self, output_root: Path):
        args = SimpleNamespace(
            mode="offline", allow_network=False, output_root=output_root,
            scope="resolved-subset", repository=None, confirm_large_run=False,
        )
        return reproduce_command.authorize_execution(args, self.view, self.report)

    def test_overlapping_destinations_are_refused(self):
        overlapping = {
            "the output root itself": self.output_root,
            "inside the output root": self.output_root / "sub",
            "the source run directory": self.run_dir,
            "inside the source run directory": self.run_dir / "nested",
            "an ancestor of the output root": self.root,
        }
        for label, destination in overlapping.items():
            with self.subTest(destination=label):
                refusal = self._authorize(destination)
                self.assertIsNotNone(
                    refusal, f"{label} must be refused as a reproduction target"
                )

    def test_a_disjoint_destination_is_authorized(self):
        self.assertIsNone(self._authorize(self.root / "elsewhere"))

    def test_overlaps_helper_is_symmetric(self):
        parent = self.root / "a"
        child = self.root / "a" / "b"
        sibling = self.root / "c"
        self.assertTrue(reproduce_command._overlaps(parent, child))
        self.assertTrue(reproduce_command._overlaps(child, parent))
        self.assertTrue(reproduce_command._overlaps(parent, parent))
        self.assertFalse(reproduce_command._overlaps(parent, sibling))


class ExecutionReportHonestyTests(unittest.TestCase):
    """C-2. What the command says must match what it did."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def _preflight_payload() -> dict:
        return {
            "reproduction_format_version": reproduce_command.REPRODUCTION_FORMAT_VERSION,
            "run_id": "abc123",
            "assurance_state": reproduce_command.STATE_COMPATIBLE,
            "executed": False,
            "execution_attempted": False,
            "execution_error": None,
            "mode": None,
            "scope": None,
            "network_authorized": False,
            "network_used": False,
            "large_run_confirmed": False,
            "workers": None,
            "wrote_anything": False,
            "output_root": None,
            "output_root_created": False,
            "reproduction_run_directory": None,
            "reproduction_status": None,
            "blockers": [],
            "checks": [],
        }

    def test_preflight_still_states_it_is_read_only(self):
        text = reproduce_command.render_text(self._preflight_payload())
        self.assertIn("read-only", text)
        self.assertIn("wrote nothing", text)

    def test_executed_report_never_claims_it_wrote_nothing(self):
        payload = self._preflight_payload()
        run_directory = self.root / "out" / "runs" / "20260101T000000Z_n001_xyz"
        payload.update({
            "executed": True,
            "execution_attempted": True,
            "mode": "offline",
            "scope": "resolved_subset",
            "wrote_anything": True,
            "output_root": str(self.root / "out"),
            "output_root_created": True,
            "reproduction_run_directory": str(run_directory),
            "reproduction_status": "completed",
            "selected_repository_count": 1,
            "workers": 1,
        })
        text = reproduce_command.render_text(payload)

        self.assertNotIn("wrote nothing", text)
        self.assertNotIn("created no worktree", text)
        self.assertNotIn("ran no parser", text)
        self.assertIn("RE-EXECUTED", text)
        # The operator must be able to find the output from this report alone.
        self.assertIn(str(run_directory), text)
        self.assertIn("completed", text)

    def test_failed_execution_is_reported_as_neither_success_nor_preflight(self):
        payload = self._preflight_payload()
        payload.update({
            "executed": True,
            "execution_attempted": True,
            "execution_error": "RuntimeError: acquisition exploded",
            "mode": "offline",
            "scope": "resolved_subset",
            "wrote_anything": False,
            "output_root": str(self.root / "out"),
            "reproduction_run_directory": None,
            "reproduction_status": None,
            "selected_repository_count": 1,
            "workers": 1,
        })
        text = reproduce_command.render_text(payload)

        self.assertNotIn("wrote nothing", text)
        self.assertIn("execution FAILED", text)
        self.assertIn("acquisition exploded", text)

    def test_network_used_is_observed_not_assumed(self):
        """`--allow-network` grants permission; it is not evidence of use."""
        run = self.root / "run"
        run.mkdir()

        (run / "analysis.json").write_text(
            json.dumps([{"acquisition": {"network_contacted": False}}]), encoding="utf-8"
        )
        self.assertFalse(reproduce_command._network_was_used(str(run)))

        (run / "analysis.json").write_text(
            json.dumps([{"acquisition": {"network_contacted": True}}]), encoding="utf-8"
        )
        self.assertTrue(reproduce_command._network_was_used(str(run)))

        # Unknowable stays distinct from "no".
        self.assertIsNone(reproduce_command._network_was_used(None))
        (run / "analysis.json").write_text(json.dumps([{"acquisition": {}}]), encoding="utf-8")
        self.assertIsNone(reproduce_command._network_was_used(str(run)))

    def test_wrote_anything_reflects_a_run_directory_on_disk(self):
        """`execute()` must observe the result, not restate its intent."""
        created = self.root / "out" / "runs" / "20260101T000000Z_n001_xyz"
        created.mkdir(parents=True)
        args = SimpleNamespace(
            mode="offline", allow_network=False, output_root=self.root / "out",
            scope="resolved-subset", repository=None, confirm_large_run=False, workers=1,
        )
        view = SimpleNamespace(
            repositories_by_url={"https://github.com/a/b": {}},
            normalized_input=None,
        )
        with (
            patch.object(
                reproduce_command, "selected_repositories",
                return_value=["https://github.com/a/b"],
            ),
            patch(
                "modules.benchmark_runner.run_benchmark",
                return_value={"run_directory": str(created), "status": "completed"},
            ),
        ):
            payload = reproduce_command.execute(args, view, self._preflight_payload())

        self.assertTrue(payload["wrote_anything"])
        self.assertEqual(payload["reproduction_run_directory"], str(created))
        self.assertEqual(payload["reproduction_status"], "completed")
        self.assertTrue(payload["execution_attempted"])
        self.assertIsNone(payload["execution_error"])


if __name__ == "__main__":
    unittest.main()
