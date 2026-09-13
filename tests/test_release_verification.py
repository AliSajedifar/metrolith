import argparse
import gzip
import importlib.util
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pipeline


REPOSITORY = Path(__file__).resolve().parent.parent
SCRIPT = REPOSITORY / "tools" / "release_verify.py"
SPEC = importlib.util.spec_from_file_location("metrolith_release_verify", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_verify)


class ReleaseVerificationLockTests(unittest.TestCase):
    def setUp(self):
        self.lock_path = REPOSITORY / release_verify.LOCK_FILE
        self.lock_text = self.lock_path.read_text(encoding="utf-8")
        self.pins = release_verify.locked_versions(self.lock_path)

    def test_every_lock_entry_is_exact_and_hashed(self):
        self.assertIn("--only-binary=:all:", self.lock_text)
        lines = self.lock_text.splitlines()
        pin_indexes = [
            index for index, line in enumerate(lines)
            if release_verify._PIN.match(line.strip())
        ]
        self.assertGreater(len(pin_indexes), 10)
        for offset, index in enumerate(pin_indexes):
            end = pin_indexes[offset + 1] if offset + 1 < len(pin_indexes) else len(lines)
            block = "\n".join(lines[index:end])
            self.assertRegex(block, r"--hash=sha256:[0-9a-f]{64}")
            self.assertNotIn(">=", lines[index])
            self.assertNotIn("~=", lines[index])

    def test_lock_covers_build_backend_and_runtime_dependencies(self):
        project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
        backend = project["build-system"]["requires"]
        runtime = project["project"]["dependencies"]
        for requirement in backend + runtime:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", requirement)
            self.assertIsNotNone(match, requirement)
            name = release_verify._NORMALIZE_NAME.sub("-", match.group(1)).lower()
            self.assertEqual(self.pins.get(name), match.group(2), requirement)
        self.assertEqual(backend, ["setuptools==83.0.0"])

    def test_ci_uses_lock_full_action_shas_and_single_verifier(self):
        workflow = (REPOSITORY / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        action_uses = re.findall(r"uses:\s+([^\s#]+)", workflow)
        self.assertGreaterEqual(len(action_uses), 3)
        for action in action_uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertIn("--require-hashes -r requirements/release-verification.lock", workflow)
        self.assertIn("python tools/release_verify.py", workflow)
        self.assertIn("${{ runner.temp }}", workflow)
        self.assertIn('python-version: "3.13.9"', workflow)
        self.assertNotRegex(workflow, r"pip install\s+--upgrade")


class ReleaseVerificationProcedureTests(unittest.TestCase):
    @staticmethod
    def _source_bytecode_snapshot():
        roots = (
            REPOSITORY / "__pycache__",
            REPOSITORY / "modules",
            REPOSITORY / "validation",
        )
        return {
            path.relative_to(REPOSITORY).as_posix(): path.read_bytes()
            for root in roots
            if root.exists()
            for path in root.rglob("*.py[co]")
        }

    def test_verification_subprocess_environment_is_utf8_and_nonwriting(self):
        environment = release_verify._base_environment()
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_version_preflight_does_not_contaminate_the_source_tree(self):
        before = self._source_bytecode_snapshot()

        completed = release_verify.run_command(
            (sys.executable, "-m", "pipeline", "--version"),
            cwd=REPOSITORY,
            env=release_verify._base_environment(),
        )

        self.assertEqual(completed.stdout.strip(), "Metrolith 4.0.0")
        self.assertEqual(self._source_bytecode_snapshot(), before)

    def test_version_preflight_bytecode_guard_has_a_positive_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writing = root / "writing"
            nonwriting = root / "nonwriting"
            writing.mkdir()
            nonwriting.mkdir()
            for destination in (writing, nonwriting):
                (destination / "probe.py").write_text("VALUE = 1\n", encoding="utf-8")

            writing_environment = release_verify._base_environment()
            writing_environment.pop("PYTHONDONTWRITEBYTECODE")
            release_verify.run_command(
                (sys.executable, "-c", "import probe"),
                cwd=writing,
                env=writing_environment,
            )
            self.assertTrue(list(writing.rglob("*.pyc")))

            release_verify.run_command(
                (sys.executable, "-c", "import probe"),
                cwd=nonwriting,
                env=release_verify._base_environment(),
            )
            self.assertFalse(list(nonwriting.rglob("*.py[co]")))
            self.assertFalse(list(nonwriting.rglob("__pycache__")))

    def test_cli_command_probe_matches_parser(self):
        parser = pipeline.build_cli()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(release_verify.CLI_COMMANDS), set(subparsers.choices))

    def test_focused_gate_covers_each_hardening_and_closure_boundary(self):
        focused = set(release_verify.FOCUSED_HARDENING_TESTS)
        for required in (
            "tests/test_policy_v2_check.py",
            "tests/test_schema_identity_hardening.py",
            "tests/test_cognitive_persistence_g1c.py",
            "tests/test_historical_fixture_governance.py",
            "tests/test_diagnostic_consistency_v35.py",
            "tests/test_hypothesis_triage_v35.py",
            "tests/test_final_release_hardening.py",
        ):
            self.assertIn(required, focused)

    def test_compileall_boundary_excludes_intentionally_invalid_test_data(self):
        sources = {
            path.relative_to(REPOSITORY).as_posix()
            for path in release_verify._program_python_sources(REPOSITORY)
        }
        self.assertIn("pipeline.py", sources)
        self.assertIn("validation/conformance/__main__.py", sources)
        self.assertFalse(any("/data/" in path for path in sources))
        self.assertFalse(any("/corpus/" in path for path in sources))

    def test_sdist_tar_and_gzip_normalization_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            for path, member_mtime, gzip_mtime in (
                (first, 1, 11),
                (second, 2, 22),
            ):
                tar_payload = io.BytesIO()
                with tarfile.open(fileobj=tar_payload, mode="w") as archive:
                    member = tarfile.TarInfo("metrolith-4.0.0/file.txt")
                    member.size = len(b"same payload")
                    member.mtime = member_mtime
                    member.uid = member_mtime
                    member.uname = f"builder-{member_mtime}"
                    archive.addfile(member, io.BytesIO(b"same payload"))
                path.write_bytes(gzip.compress(tar_payload.getvalue(), mtime=gzip_mtime))

            release_verify._normalize_sdist_archive(first, mtime=123)
            release_verify._normalize_sdist_archive(second, mtime=123)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, "r:gz") as archive:
                extracted = archive.extractfile("metrolith-4.0.0/file.txt")
                self.assertIsNotNone(extracted)
                self.assertEqual(extracted.read(), b"same payload")

    def test_output_path_inside_repository_is_refused(self):
        with self.assertRaises(release_verify.VerificationFailure):
            release_verify._external_output_path(
                str(REPOSITORY / "release-evidence.json"), REPOSITORY
            )
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "release-evidence.json"
            self.assertEqual(
                release_verify._external_output_path(str(external), REPOSITORY),
                external.resolve(),
            )

    def test_command_failure_includes_stdout_and_stderr(self):
        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stderr(captured):
                with self.assertRaises(release_verify.VerificationFailure) as raised:
                    release_verify.run_command(
                        (sys.executable, "-c", "import os; os.write(1,b'build stdout\\n'); os.write(2,b'build stderr\\n'); raise SystemExit(7)"),
                        cwd=Path(directory),
                    )
        message = str(raised.exception)
        self.assertIn("build stdout", message)
        self.assertIn("build stderr", message)
        self.assertIn("build stdout", captured.getvalue())
        self.assertIn("build stderr", captured.getvalue())

    def test_dirty_candidate_fails_closed_without_running_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(("git", "init", "-q", str(repository)), check=True)
            subprocess.run(
                ("git", "-C", str(repository), "config", "user.name", "ArchLens Test"),
                check=True,
            )
            subprocess.run(
                ("git", "-C", str(repository), "config", "user.email", "test@archlens.invalid"),
                check=True,
            )
            (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(("git", "-C", str(repository), "add", "tracked.txt"), check=True)
            subprocess.run(
                ("git", "-C", str(repository), "commit", "-q", "-m", "fixture"),
                check=True,
            )
            (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            # A cleanliness regression must never recursively launch this suite.
            verifier = release_verify.ReleaseVerifier(repository)
            with mock.patch.object(verifier, "_run_stages", side_effect=AssertionError("dirty candidate reached stages")) as stages:
                evidence = verifier.run()
            stages.assert_not_called()

        self.assertEqual(evidence["status"], "failed")
        checks = {item["name"]: item for item in evidence["checks"]}
        self.assertEqual(checks["repository_clean_before"]["status"], "failed")
        self.assertEqual(checks["full_test_suite"]["status"], "skipped")
        self.assertEqual(checks["packaging"]["status"], "skipped")
        self.assertEqual(checks["full_test_suite"]["detail"]["execution"], "NOT_RUN")
        self.assertEqual(checks["repository_clean_after"]["status"], "failed")
        self.assertEqual(
            {item["id"] for item in evidence["deferred_validations"]},
            {
                "owner-publication-metadata",
                "github-hosted-action",
                "publication-actions",
                "benchmark-of-record-publication",
                "standalone-output-schema-policy",
                "changed-code-portability-campaign",
                "pipeline-and-cli-refactors",
            },
        )


class ReleaseGateOrchestrationTests(unittest.TestCase):
    # Literal expected sequence guards against shrinking acceptance. Only the
    # expensive operations are stubbed; run/check/main remain real.
    STAGES = (
        ("version_consistency", "verify_versions"),
        ("locked_environment", "verify_locked_environment"),
        ("schema_identity", "verify_schema_identities"),
        ("schema_validation", "verify_schema_documents"),
        ("standalone_contracts", "verify_standalone_contracts"),
        ("commit_delta", "verify_commit_delta"),
        ("doctor", "run_doctor"),
        ("source_reconciliation", "verify_source_reconciliation"),
        ("compileall", "run_compileall"),
        ("focused_hardening_tests", "run_focused_suite"),
        ("full_test_suite", "run_full_suite"),
        ("packaging", "build_distributions"),
        ("isolated_wheel_install", "install_wheel"),
        ("installed_wheel_smoke", "smoke_installed_wheel"),
        ("cli_command_availability", "verify_installed_commands"),
        ("installed_conformance", "run_installed_conformance"),
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.verifier = release_verify.ReleaseVerifier(self.repository)
        self.stack = self.enterContext(ExitStack())
        self.operations = {}
        for gate, method in self.STAGES:
            result = {"program": "4.0.0"} if gate == "version_consistency" else {}
            if gate == "packaging":
                result = {"_wheel": str(self.root / "not-built.whl")}
            elif gate == "isolated_wheel_install":
                result = {"_python": self.root / "not-installed-python"}
            self.operations[gate] = self.stack.enter_context(mock.patch.object(self.verifier, method, return_value=result))
        self.state = self.stack.enter_context(mock.patch.object(
            release_verify, "repository_state", return_value={"commit": "fixture", "commit_timestamp": 1, "clean": True, "changes": []},
        ))
        self.stack.enter_context(mock.patch.object(release_verify, "REPOSITORY_ROOT", self.repository))
        def factory(repository, logs):
            self.assertEqual(repository, self.repository)
            self.verifier.log_directory = logs
            return self.verifier
        self.stack.enter_context(mock.patch.object(release_verify, "ReleaseVerifier", side_effect=factory))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(redirect_stdout(self.stdout))
        self.stack.enter_context(redirect_stderr(self.stderr))
        real_popen = release_verify.subprocess.Popen
        def guarded_popen(command, *args, **kwargs):
            allowed = list(command) == ["git", "--version"] or list(command[:2]) == [sys.executable, "-c"]
            if not allowed:
                raise AssertionError(f"orchestration unit test tried a real stage: {command}")
            return real_popen(command, *args, **kwargs)
        self.stack.enter_context(mock.patch.object(release_verify.subprocess, "Popen", side_effect=guarded_popen))

    def invoke(self):
        output = self.root / "failure Î©.json"
        code = release_verify.main(["--output", str(output)])
        return code, json.loads(output.read_text(encoding="utf-8"))

    def assert_blocked_after(self, evidence, gate):
        sequence = [name for name, _ in self.STAGES]
        downstream = sequence[sequence.index(gate) + 1:]
        checks = {item["name"]: item for item in evidence["checks"]}
        for name in downstream:
            self.operations[name].assert_not_called()
            self.assertEqual(checks[name]["status"], "skipped")
            self.assertEqual(checks[name]["detail"], {"execution": "NOT_RUN", "blocked_by": gate})
        self.assertEqual(evidence["first_failure"], gate)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["artifacts"], [])

    def test_focused_failure_persists_diagnostics_and_starts_zero_downstream_stages(self):
        self.operations["focused_hardening_tests"].side_effect = lambda: release_verify.run_command(
            (sys.executable, "-c", "import os; os.write(1,'focused Î© failed'.encode()); os.write(2,b'refusal\\xff'); raise SystemExit(7)"),
            cwd=self.repository, echo_output=True,
        )
        code, evidence = self.invoke()
        self.assertEqual(code, 1)
        self.assert_blocked_after(evidence, "focused_hardening_tests")
        failure = next(item for item in evidence["checks"] if item["name"] == "focused_hardening_tests")
        self.assertIn("focused Î© failed", failure["error"])
        self.assertIn("refusal\\xff", failure["error"])
        command = next(item for item in evidence["commands"] if item["stage"] == "focused_hardening_tests")
        self.assertEqual(command["returncode"], 7)
        self.assertEqual(Path(command["logs"]["stdout"]).read_bytes(), "focused Î© failed".encode())
        self.assertEqual(Path(command["logs"]["stderr"]).read_bytes(), b"refusal\xff")
        self.assertTrue(evidence["repository"]["clean_after"])
        self.assertIn("START focused_hardening_tests", self.stderr.getvalue())
        self.assertIn("END command: exit=7", self.stderr.getvalue())

    def test_gate_exception_is_retained_and_stops_downstream_work(self):
        self.operations["locked_environment"].side_effect = RuntimeError("broken lock Î©")
        code, evidence = self.invoke()
        self.assertEqual(code, 1)
        self.assert_blocked_after(evidence, "locked_environment")
        failure = next(item for item in evidence["checks"] if item["name"] == "locked_environment")
        self.assertEqual(failure["error_type"], "RuntimeError")
        self.assertEqual(failure["error"], "broken lock Î©")

    def test_failed_full_suite_starts_no_packaging_install_or_conformance(self):
        self.operations["full_test_suite"].side_effect = release_verify.VerificationFailure("suite failed")
        code, evidence = self.invoke()
        self.assertEqual(code, 1)
        self.assert_blocked_after(evidence, "full_test_suite")

    def test_final_state_error_does_not_mask_original_failure(self):
        self.operations["focused_hardening_tests"].side_effect = RuntimeError("original failure")
        self.state.side_effect = [self.state.return_value, OSError("final state unavailable")]
        code, evidence = self.invoke()
        self.assertEqual(code, 1)
        self.assert_blocked_after(evidence, "focused_hardening_tests")
        self.assertIsNone(evidence["repository"]["clean_after"])
        self.assertEqual(evidence["checks"][-1]["error"], "final state unavailable")

    def test_workspace_cleanup_error_keeps_the_first_gate_failure(self):
        self.operations["focused_hardening_tests"].side_effect = RuntimeError("original failure")
        workspace = mock.MagicMock()
        workspace.__enter__.return_value = str(self.root / "stub-workspace")
        workspace.__exit__.side_effect = OSError("cleanup failed")
        with mock.patch.object(release_verify.tempfile, "TemporaryDirectory", return_value=workspace):
            code, evidence = self.invoke()
        self.assertEqual(code, 1)
        self.assert_blocked_after(evidence, "focused_hardening_tests")
        errors = [item["error"] for item in evidence["checks"] if item["status"] == "failed"]
        self.assertEqual(errors, ["original failure", "cleanup failed"])
        self.assertTrue(evidence["repository"]["clean_after"])

    def test_all_success_stubs_visit_every_required_gate_and_keep_stdout_json(self):
        self.assertEqual(release_verify.main([]), 0)
        evidence = json.loads(self.stdout.getvalue())
        expected = ["repository_clean_before", *[name for name, _ in self.STAGES], "repository_clean_after"]
        self.assertEqual([item["name"] for item in evidence["checks"]], expected)
        self.assertEqual(list(release_verify.REQUIRED_GATES), expected)
        self.assertTrue(all(item["status"] == "passed" for item in evidence["checks"]))
        for operation in self.operations.values():
            operation.assert_called_once()
        self.assertIsNone(evidence["first_failure"])
        self.assertEqual(evidence["status"], "passed")
        self.assertIn("START full_test_suite", self.stderr.getvalue())
        self.assertIsNone(release_verify._ACTIVE_COMMAND_LOG)


class ReleaseLiveLoggingTests(unittest.TestCase):
    def test_unterminated_unicode_start_is_visible_before_child_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "continue"
            command_log = []
            terminal = io.StringIO()
            errors, results = [], []
            script = (
                "import os,time,pathlib,sys; "
                "os.write(1,b'START \\xce'); time.sleep(.02); os.write(1,b'\\xa9'); "
                "deadline=time.monotonic()+8; path=pathlib.Path(sys.argv[1]); "
                "\nwhile not path.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                "assert path.exists(), 'parent did not observe live output'; os.write(1,b' END')"
            )
            def run():
                try:
                    results.append(release_verify.run_command((sys.executable, "-c", script, str(release)), cwd=root, echo_output=True))
                except BaseException as error:
                    errors.append(error)
            with (mock.patch.object(release_verify, "_ACTIVE_LOG_DIRECTORY", root),
                  mock.patch.object(release_verify, "_ACTIVE_COMMAND_LOG", command_log),
                  redirect_stderr(terminal)):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    deadline = time.monotonic() + 5
                    observed = b""
                    while time.monotonic() < deadline:
                        if command_log:
                            path = Path(command_log[0]["logs"]["stdout"])
                            if path.exists():
                                observed = path.read_bytes()
                        if observed == "START Î©".encode() and "START Î©" in terminal.getvalue():
                            break
                        time.sleep(.01)
                    self.assertEqual(observed, "START Î©".encode())
                    self.assertIn("START Î©", terminal.getvalue())
                    self.assertTrue(worker.is_alive(), "marker must precede child completion")
                    self.assertIsNone(command_log[0]["returncode"])
                finally:
                    release.touch()
                    worker.join(timeout=10)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results[0].stdout, "START Î© END")
                self.assertEqual(command_log[0]["returncode"], 0)
                self.assertIn("END command: exit=0", terminal.getvalue())

    def test_pytest_progress_changes_presentation_only(self):
        verifier = release_verify.ReleaseVerifier(REPOSITORY)
        completed = subprocess.CompletedProcess([], 0, "one passed", "")
        with mock.patch.object(release_verify, "run_command", return_value=completed) as command:
            verifier.run_full_suite()
            full = command.call_args.args[0]
            verifier.run_focused_suite()
            focused = command.call_args.args[0]
        self.assertEqual(full, (sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider"))
        self.assertEqual(focused, (*full, *release_verify.FOCUSED_HARDENING_TESTS))
        self.assertEqual(release_verify._base_environment()["PYTHONUNBUFFERED"], "1")


if __name__ == "__main__":
    unittest.main()
