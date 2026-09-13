"""Phase 10 tests: reproduction preflight safety (plan section 16)."""

import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.cli.reproduce_command import (
    STATE_COMPATIBLE,
    STATE_INVALID,
    STATE_NOT_REPRODUCIBLE,
    STATE_SUBSET,
    preflight,
    render_text,
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
        self.source = find_run()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run"
        shutil.copytree(self.source, self.run)


class NoWriteTests(RequiresRun):
    """Preflight must not modify anything, anywhere."""

    @staticmethod
    def _snapshot(root: Path):
        return {
            path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    def test_no_file_is_created_modified_or_removed(self):
        before = self._snapshot(self.run)
        preflight(open_run(self.run))
        after = self._snapshot(self.run)
        self.assertEqual(before, after)

    def test_preflight_declares_that_it_wrote_nothing(self):
        payload = preflight(open_run(self.run))
        self.assertFalse(payload["wrote_anything"])
        self.assertFalse(payload["executed"])
        self.assertIsNone(payload["output_root"])

    def test_open_is_never_called_for_writing(self):
        real_open = open
        opened_for_write = []

        def spy(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                opened_for_write.append((str(file), mode))
            return real_open(file, mode, *args, **kwargs)

        with patch("builtins.open", spy):
            preflight(open_run(self.run))
        self.assertEqual(opened_for_write, [])


class NoNetworkTests(RequiresRun):
    """Preflight must not open a network connection."""

    def test_no_socket_connection_is_attempted(self):
        attempts = []
        real_connect = socket.socket.connect

        def spy(self, address, *args, **kwargs):
            attempts.append(address)
            return real_connect(self, address, *args, **kwargs)

        with patch.object(socket.socket, "connect", spy):
            preflight(open_run(self.run))
        self.assertEqual(attempts, [])

    def test_git_is_invoked_read_only_and_without_a_remote(self):
        commands = []
        import subprocess as _subprocess

        real_run = _subprocess.run

        def spy(args, *rest, **kwargs):
            commands.append(list(args) if isinstance(args, (list, tuple)) else [args])
            return real_run(args, *rest, **kwargs)

        with patch("modules.cli.reproduce_command.subprocess.run", spy):
            preflight(open_run(self.run))

        for command in commands:
            with self.subTest(command=command):
                # Only object-existence checks are permitted; anything that
                # contacts a remote is forbidden.
                self.assertEqual(command[0], "git")
                self.assertIn("cat-file", command)
                for forbidden in ("fetch", "clone", "pull", "ls-remote", "push"):
                    self.assertNotIn(forbidden, command)


class NoMeasurementTests(RequiresRun):
    def test_no_parser_or_metric_extraction_runs(self):
        with patch("modules.core_metrics.compute_repository_metrics") as metrics:
            preflight(open_run(self.run))
        metrics.assert_not_called()

    def test_no_worktree_directory_is_created(self):
        before = {p.as_posix() for p in self.root.rglob("*") if p.is_dir()}
        preflight(open_run(self.run))
        after = {p.as_posix() for p in self.root.rglob("*") if p.is_dir()}
        self.assertEqual(before, after)


class AssuranceStateTests(RequiresRun):
    def test_run_without_normalized_input_is_resolved_subset_only(self):
        payload = preflight(open_run(self.run))
        self.assertEqual(payload["assurance_state"], STATE_SUBSET)

    def test_exact_state_is_unreachable_without_a_profiler_commit(self):
        payload = preflight(open_run(self.run))
        self.assertNotEqual(payload["assurance_state"], "exact_environment_ready")
        profiler = next(
            item for item in payload["checks"] if item["check"] == "profiler_revision"
        )
        self.assertEqual(profiler["outcome"], "not_evaluable")
        self.assertIn("no immutable revision", profiler["detail"])

    def test_unsupported_schema_is_not_reproducible(self):
        path = self.run / "run_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["artifact_schema_version"] = "1.0.0"
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = preflight(open_run(self.run))
        self.assertIn(
            report["assurance_state"], {STATE_NOT_REPRODUCIBLE, STATE_INVALID}
        )

    def test_non_finalized_run_is_blocked(self):
        path = self.run / "run_status.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "running"
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = preflight(open_run(self.run))
        self.assertEqual(report["assurance_state"], STATE_NOT_REPRODUCIBLE)
        self.assertTrue(
            any(item["check"] == "lifecycle" for item in report["blockers"])
        )

    def test_metric_contract_mismatch_is_a_blocker(self):
        path = self.run / "run_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metric_contract_version"] = "9.9.9"
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = preflight(open_run(self.run))
        self.assertTrue(
            any(item["check"] == "metric_contract_version" for item in report["blockers"])
        )

    def test_missing_cache_is_reported_not_guessed(self):
        report = preflight(open_run(self.run))
        cache = next(
            item for item in report["checks"]
            if item["check"] == "offline_cache_readiness"
        )
        self.assertIn(cache["outcome"], {"passed", "failed", "not_evaluable"})
        self.assertIsNotNone(cache["detail"])

    def test_output_feasibility_is_advisory_only(self):
        report = preflight(open_run(self.run))
        feasibility = next(
            item for item in report["checks"]
            if item["check"] == "output_path_feasibility"
        )
        self.assertEqual(feasibility["outcome"], "advisory")


class OutputTests(RequiresRun):
    def test_text_and_json_carry_the_same_checks(self):
        payload = preflight(open_run(self.run))
        text = render_text(payload)
        for item in payload["checks"]:
            with self.subTest(check=item["check"]):
                self.assertIn(item["check"], text)

    def test_text_states_the_read_only_guarantee(self):
        text = render_text(preflight(open_run(self.run)))
        self.assertIn("read-only", text)
        self.assertIn("wrote nothing", text)

    def test_format_version_is_declared(self):
        # Pinned to the registered schema rather than a literal, so the emitted
        # version and the schema describing it cannot drift apart again — which
        # is exactly how F-P3-9 went unnoticed.
        from validation.artifact_io.schema_store import schema_version

        self.assertEqual(
            preflight(open_run(self.run))["reproduction_format_version"],
            schema_version("reproduction_output"),
        )

    def test_output_is_deterministic(self):
        first = preflight(open_run(self.run))
        second = preflight(open_run(self.run))
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_execution_fields_are_all_inert_in_preflight(self):
        payload = preflight(open_run(self.run))
        self.assertIsNone(payload["mode"])
        self.assertIsNone(payload["scope"])
        self.assertFalse(payload["network_authorized"])
        self.assertFalse(payload["large_run_confirmed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
