import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
import jsonschema
from pathlib import Path
from unittest import mock


def _finish_with_diagnostics(command, completed, *, check):
    if check and completed.returncode:
        rendered = subprocess.list2cmdline([str(part) for part in command])
        raise AssertionError(
            f"command exited {completed.returncode}: {rendered}\n"
            f"stdout:\n{completed.stdout or '<empty>'}\n"
            f"stderr:\n{completed.stderr or '<empty>'}"
        )
    return completed


def _run_with_diagnostics(command, *, check=False, **kwargs):
    """Preserve captured build/install output in the test failure message."""
    completed = subprocess.run(command, check=False, **kwargs)
    return _finish_with_diagnostics(command, completed, check=check)


def _run_installed_archlens_cli(command, *, check=False, **kwargs):
    """Capture the documented UTF-8 Metrolith CLI boundary strictly."""
    forbidden = {"text", "encoding", "errors"}.intersection(kwargs)
    if forbidden:
        raise TypeError(
            "installed Metrolith CLI decoding is fixed at strict UTF-8; "
            f"do not pass {', '.join(sorted(forbidden))}"
        )
    captured = subprocess.run(command, check=False, text=False, **kwargs)
    completed = subprocess.CompletedProcess(
        args=captured.args,
        returncode=captured.returncode,
        stdout=(
            captured.stdout.decode("utf-8", errors="strict")
            if captured.stdout is not None else None
        ),
        stderr=(
            captured.stderr.decode("utf-8", errors="strict")
            if captured.stderr is not None else None
        ),
    )
    return _finish_with_diagnostics(command, completed, check=check)


def _copy_candidate_source(repository: Path, destination: Path) -> None:
    """Copy Git-visible candidate files so build backends never touch the checkout."""
    listing = _run_with_diagnostics(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    destination.mkdir()
    for relative in (item for item in listing.stdout.split("\0") if item):
        source = repository / relative
        # The cached set includes tracked paths deleted by this candidate
        # (notably the former Action path). They must remain absent from the
        # source snapshot while their untracked renamed replacements are copied.
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _venv_python(environment_root: Path, platform_name: str | None = None) -> Path:
    # Same platform rule as tools/release_verify.py, without importing its runner.
    if (os.name if platform_name is None else platform_name) == "nt":
        return environment_root / "Scripts" / "python.exe"
    return environment_root / "bin" / "python"


class PackagingDiagnosticsTests(unittest.TestCase):
    def test_fresh_venv_interpreter_resolution(self):
        root = Path("fresh")
        self.assertEqual(_venv_python(root, "nt"), root / "Scripts" / "python.exe")
        self.assertEqual(_venv_python(root, "posix"), root / "bin" / "python")

    def test_captured_build_output_is_in_failure(self):
        completed = subprocess.CompletedProcess(
            args=["builder"], returncode=9, stdout="build stdout", stderr="build stderr"
        )
        with mock.patch.object(subprocess, "run", return_value=completed):
            with self.assertRaises(AssertionError) as raised:
                _run_with_diagnostics(
                    ["builder"], check=True, capture_output=True, text=True
                )
        self.assertIn("build stdout", str(raised.exception))
        self.assertIn("build stderr", str(raised.exception))

    def test_installed_archlens_cli_capture_is_strict_utf8(self):
        expected = "LOCAL — UNPROTECTED"
        payload = expected.encode("utf-8")
        completed = _run_installed_archlens_cli(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write({payload!r})",
            ],
            check=True,
            capture_output=True,
        )
        self.assertEqual(completed.stdout, expected)
        self.assertNotIn("â€”", completed.stdout)

    def test_installed_archlens_cli_capture_refuses_invalid_utf8(self):
        with self.assertRaises(UnicodeDecodeError):
            _run_installed_archlens_cli(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'\\xff')",
                ],
                check=True,
                capture_output=True,
            )

    def test_windows_codepage_positive_control_detects_former_mojibake(self):
        expected = "LOCAL — UNPROTECTED"
        payload = expected.encode("utf-8")
        former = payload.decode("cp1252", errors="strict")
        self.assertEqual(former, "LOCAL â€” UNPROTECTED")
        with self.assertRaises(AssertionError):
            self.assertIn(expected, former)


class InstalledWheelPolicyTests(unittest.TestCase):
    def test_clean_wheel_contains_and_loads_versioned_policy(self):
        repository = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdist_directory = root / "sdist"
            wheel_directory = root / "wheel"
            extracted = root / "source"
            target = root / "installed"
            candidate = root / "candidate"
            for path in (sdist_directory, wheel_directory, extracted, target):
                path.mkdir()
            _copy_candidate_source(repository, candidate)

            _run_with_diagnostics(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--verbose",
                    "--sdist",
                    "--outdir",
                    str(sdist_directory),
                    str(candidate),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            sdists = list(sdist_directory.glob("metrolith-4.0.1.tar.gz"))
            self.assertEqual(len(sdists), 1)
            with tarfile.open(sdists[0], "r:gz") as archive:
                members = archive.getnames()
                self.assertTrue(
                    any(name.endswith("tests/test_final_stabilization_v33.py") for name in members)
                )
                relative = {name.partition("/")[2] for name in members}
                shipped_tests = {name for name in relative if name.startswith("tests/") and name.endswith(".py")}
                # Public source now ships its maintained tests and fixtures.
                expected_tests = {path.relative_to(candidate).as_posix()
                                  for path in (candidate / "tests").rglob("*.py")}
                self.assertEqual(shipped_tests, expected_tests)
                for required in ("LICENSE", "CHANGELOG.md", "docs/PUBLIC_RELEASE_CHECKLIST.md",
                                 "docs/REPRODUCIBILITY.md", "examples/ratchet-rules.json"):
                    self.assertIn(required, relative)
                self.assertFalse(any(name.startswith(("archive/", "research/")) for name in relative))
                for member in archive.getmembers():
                    if member.isfile():
                        data = archive.extractfile(member).read()
                        source = candidate / member.name.partition("/")[2]
                        if source.is_file():
                            self.assertEqual(data, source.read_bytes(), member.name)
                archive.extractall(extracted, filter="data")

            clean_source = extracted / "metrolith-4.0.1"
            _run_with_diagnostics(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--verbose",
                    "--wheel",
                    "--outdir",
                    str(wheel_directory),
                    str(clean_source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            wheels = list(wheel_directory.glob("metrolith-4.0.1-py3-none-any.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                members = set(archive.namelist())
                self.assertIn("metrolith-4.0.1.dist-info/licenses/LICENSE", members)
                metadata_text = archive.read("metrolith-4.0.1.dist-info/METADATA").decode("utf-8")
                self.assertIn("License-Expression: Apache-2.0", metadata_text)
                self.assertIn("License-File: LICENSE", metadata_text)
                self.assertIn("examples/ratchet-rules.json", members)
                self.assertIn("config/exclusions.v1.json", members)
                self.assertIn("examples/quickstart_repositories.csv", members)
                self.assertIn("metrolith-4.0.1.dist-info/entry_points.txt", members)
                self.assertIn("archlens_json.py", members)
                # `pyproject.toml` lists packages explicitly, so a new SUBPACKAGE
                # is silently omitted from the wheel unless it is added there.
                # `modules/callable_analysis` was omitted exactly this way, and
                # nothing would have noticed until `core_metrics` imported it and
                # every installed wheel failed. A plain module such as
                # `modules/syntax_predicates.py` is covered by the parent entry;
                # a subpackage is not.
                self.assertIn("modules/syntax_predicates.py", members)
                # SARIF is a plain module under the already-listed policy
                # package. Assert the wheel member directly so an installed
                # `check --format sarif` cannot fall back to the source tree.
                self.assertIn("modules/policy/sarif.py", members)
                # C5's presentation seam: a plain module, so the parent entry
                # covers it -- asserted anyway, because "covered by the parent"
                # is exactly what was assumed about `callable_analysis`.
                self.assertIn("modules/complexity_view.py", members)
                self.assertIn("modules/hotspots.py", members)
                self.assertIn("modules/cli/hotspots_command.py", members)
                self.assertIn("modules/cli/baseline_command.py", members)
                self.assertIn("modules/presentation.py", members)
                self.assertIn("modules/duplication/output.py", members)
                self.assertIn("modules/cli/duplication_command.py", members)
                self.assertIn("examples/local_project/app.py", members)
                self.assertIn("examples/local_project/web.js", members)
                for name in (
                    "__init__.py", "model.py", "go.py", "java.py", "python.py",
                ):
                    self.assertIn(f"modules/callable_analysis/{name}", members)
                # Derived-output schemas resolve through importlib.resources at
                # runtime, so a new one that never reached the wheel would fail
                # only on an installed copy.
                for schema in (
                    "explain_output-1.2.schema.json",
                    "revision_diff-1.1.schema.json",
                    "callable_row-1.9.schema.json",
                    "environment-1.12.schema.json",
                    "run_manifest-1.12.schema.json",
                ):
                    self.assertIn(
                        f"validation/resources/schemas/{schema}", members
                    )

            fresh_venv = target
            _run_with_diagnostics(
                [sys.executable, "-m", "venv", str(fresh_venv)],
                cwd=root, check=True, capture_output=True, text=True,
            )
            fresh_python = _venv_python(fresh_venv)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment["PYTHONNOUSERSITE"] = "1"
            _run_with_diagnostics(
                [str(fresh_python), "-m", "pip", "install", "--no-compile",
                 "--disable-pip-version-check", str(wheels[0])],
                cwd=root, env=environment, check=True, capture_output=True, text=True,
            )
            _run_with_diagnostics(
                [str(fresh_python), "-m", "pip", "check"],
                cwd=root, env=environment, check=True, capture_output=True, text=True,
            )
            expected_policy_sha = hashlib.sha256(
                (repository / "config" / "exclusions.v1.json").read_bytes()
            ).hexdigest()
            probe = """
import importlib.metadata as metadata
import json
from pathlib import Path
import config
import examples
import modules.config as arch_config
import pipeline
import modules.callable_analysis as callable_analysis
import modules.callable_analysis.go
import modules.callable_analysis.java
import modules.callable_analysis.python
import modules.syntax_predicates
import modules.hotspots
import modules.duplication.output as duplication_output

target = Path(__import__('sys').argv[1]).resolve()
source_checkout = Path(__import__('sys').argv[2]).resolve()
resolved_import_paths = {
    Path(item).resolve() for item in __import__('sys').path if item
}
assert source_checkout not in resolved_import_paths
assert Path(config.__file__).resolve().is_relative_to(target)
assert Path(examples.__file__).resolve().is_relative_to(target)
assert Path(arch_config.__file__).resolve().is_relative_to(target)
assert Path(callable_analysis.__file__).resolve().is_relative_to(target)
assert Path(modules.syntax_predicates.__file__).resolve().is_relative_to(target)
assert Path(modules.hotspots.__file__).resolve().is_relative_to(target)
assert Path(duplication_output.__file__).resolve().is_relative_to(target)
policy = arch_config.load_exclusion_policy()
distribution = metadata.distribution('metrolith')
entry_points = {item.name: item.value for item in distribution.entry_points if item.group == 'console_scripts'}
print(json.dumps({
    'distribution_version': distribution.version,
    'workspace_version': arch_config.PROGRAM_VERSION,
    'policy_version': policy['version'],
    'policy_sha256': arch_config.AnalysisConfig().exclusion_policy_sha256,
    'examples_present': (Path(examples.__file__).parent / 'quickstart_repositories.csv').is_file(),
    'entry_points': entry_points,
    'legacy_base': str(pipeline.BASE_DIR),
    'complexity_contract_version': callable_analysis.COMPLEXITY_CONTRACT_VERSION,
    'complexity_languages': sorted(callable_analysis.SUPPORTED_LANGUAGES),
}, sort_keys=True))
"""
            target_python = [str(fresh_python), "-P"]
            installed = _run_with_diagnostics(
                [*target_python, "-c", probe, str(target), str(repository)],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(installed.stdout)
            self.assertEqual(report["distribution_version"], "4.0.1")
            self.assertEqual(report["workspace_version"], "4.0.1")
            self.assertEqual(report["policy_version"], "1.5.0")
            self.assertEqual(report["policy_sha256"], expected_policy_sha)
            self.assertTrue(report["examples_present"])
            self.assertEqual(report["entry_points"]["metrolith"], "pipeline:main")
            self.assertEqual(
                report["entry_points"]["archlens"], "pipeline:archlens_compat_main"
            )
            self.assertEqual(report["entry_points"]["arch-bench"], "pipeline:deprecated_main")
            self.assertEqual(Path(report["legacy_base"]), root)
            self.assertFalse(Path(report["legacy_base"]).is_relative_to(target))
            # The package must not merely be present in the archive; it must
            # import and answer from the installed distribution under `-P`.
            self.assertEqual(report["complexity_contract_version"], "2.0.0")
            self.assertEqual(
                report["complexity_languages"],
                ["Go", "Java", "JavaScript", "Python", "TypeScript"],
                "all five supported languages must dispatch from the INSTALLED "
                "distribution, not merely from the source tree",
            )

            # Reuse the same isolated installation and normally resolved dependencies.
            installed_python = [str(fresh_python), "-P"]
            isolation_probe = _run_with_diagnostics(
                [
                    *installed_python, "-c",
                    "import pipeline,sys; from pathlib import Path; "
                    "print(Path(pipeline.__file__).resolve()); "
                    "assert Path(sys.argv[1]).resolve() not in "
                    "{Path(p).resolve() for p in sys.path if p}",
                    str(repository),
                ],
                cwd=root, env=environment, check=True, capture_output=True, text=True,
            )
            self.assertTrue(
                Path(isolation_probe.stdout.strip()).is_relative_to(fresh_venv)
            )

            version = _run_installed_archlens_cli(
                [*installed_python, "-m", "pipeline", "--version"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            self.assertEqual(version.stdout.strip(), "Metrolith 4.0.1")

            # Product Foundations installed journey. Every command runs from
            # the temporary non-Git directory, imports the fresh-venv wheel,
            # and uses only project-owned local source.
            journey_workspace = root / "journey-workspace"
            example = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "example", "run",
                    "--local", "--workspace", str(journey_workspace),
                ],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            self.assertIn("Finalizing recorded measurements and diagnostics", example.stdout)
            self.assertIn("Final run state  completed", example.stdout)
            self.assertIn("Tutorial input only", example.stdout)
            runs_root = journey_workspace / "metrolith-output" / "runs"
            example_run = max(runs_root.iterdir(), key=lambda item: item.stat().st_mtime_ns)
            status = json.loads((example_run / "run_status.json").read_text(encoding="utf-8"))
            manifest = json.loads((example_run / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertNotEqual(status["status"], "running")
            self.assertEqual(manifest["profiler_provenance_kind"], "installed_distribution")
            self.assertEqual(manifest["profiler_git_state"], "not_applicable")
            self.assertIsNone(manifest["profiler_git_dirty"])
            self.assertRegex(manifest["profiler_source_sha256"], r"^[0-9a-f]{64}$")

            validate_text = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "validate",
                    str(example_run), "--format", "text",
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("Metrolith validate: valid", validate_text.stdout)
            report_path = root / "installed-report.html"
            report = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "report",
                    str(example_run), "--output", str(report_path),
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertTrue(report_path.is_file())
            self.assertIn("Next:", report.stdout)

            policy_path = root / "local-policy.json"
            _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "policy", "init",
                    "--output", str(policy_path), "--metric",
                    "repository.source_files", "--operator", "gt",
                    "--threshold", "999", "--severity", "warning",
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "policy", "validate",
                    str(policy_path),
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            local_check_command = [
                *installed_python, "-m", "pipeline", "check",
                str(example_run), "--policy", str(policy_path), "--format", "text",
            ]
            raw_local_check = _run_with_diagnostics(
                local_check_command,
                cwd=root, env=environment, check=True, capture_output=True, text=False,
            )
            expected = "LOCAL — UNPROTECTED"
            expected_bytes = expected.encode("utf-8")
            self.assertIn(expected_bytes, raw_local_check.stdout)
            trust_line = next(
                line for line in raw_local_check.stdout.splitlines()
                if line.startswith(b"trust:")
            )
            separator = trust_line.split(b"LOCAL ", 1)[1].rsplit(
                b" UNPROTECTED", 1
            )[0]
            self.assertEqual(separator, b"\xe2\x80\x94")
            self.assertIn(
                expected,
                raw_local_check.stdout.decode("utf-8", errors="strict"),
            )
            former = raw_local_check.stdout.decode("cp1252", errors="strict")
            self.assertIn("LOCAL â€” UNPROTECTED", former)

            local_check = _run_installed_archlens_cli(
                local_check_command,
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("LOCAL — UNPROTECTED", local_check.stdout)
            self.assertNotIn("â€”", local_check.stdout)

            hotspots_text = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "hotspots",
                    str(example_run), "--format", "text",
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("No score and no universal threshold", hotspots_text.stdout)
            doctor_text = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "doctor",
                    "--format", "text", "--workspace", str(journey_workspace),
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("Metrolith doctor: healthy", doctor_text.stdout)

            # Ratchet capture requires a portable, revision-bound subject. Make
            # one local Git repository and the read-only bare revision source
            # that the existing check admission service requires.
            git_source = root / "revision-source"
            git_source.mkdir()
            (git_source / "app.py").write_text(
                "def measured(value):\n    return value + 1\n", encoding="utf-8"
            )
            for command in (
                ["git", "init"],
                ["git", "config", "user.email", "metrolith@example.invalid"],
                ["git", "config", "user.name", "Metrolith Test"],
                ["git", "add", "app.py"],
                ["git", "commit", "-m", "fixture"],
                ["git", "remote", "add", "origin", "https://github.com/metrolith/example-fixture"],
            ):
                _run_with_diagnostics(
                    command, cwd=git_source, check=True, capture_output=True, text=True
                )
            canonical = "https://github.com/metrolith/example-fixture"
            cache = journey_workspace / ".metrolith" / "cache" / "git"
            cache.mkdir(parents=True, exist_ok=True)
            cache_name = "r_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20] + ".git"
            _run_with_diagnostics(
                ["git", "clone", "--bare", str(git_source), str(cache / cache_name)],
                cwd=root, check=True, capture_output=True, text=True,
            )
            _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "analyze",
                    str(git_source), "--revision", "HEAD", "--workspace",
                    str(journey_workspace),
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            revision_run = max(runs_root.iterdir(), key=lambda item: item.stat().st_mtime_ns)
            rules_path = root / "ratchet-rules.json"
            rules_path.write_text(
                json.dumps([
                    {
                        "rule_id": "ratchet.source-files",
                        "metric": "repository.source_files",
                        "metric_contract": "metrics",
                        "scope": "repository",
                        "direction": "increase_is_worse",
                        "max_regression": 0,
                        "severity": "violation",
                    }
                ]),
                encoding="utf-8",
            )
            baseline_path = root / "baseline.json"
            capture = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "baseline", "capture",
                    str(revision_run), "--rules", str(rules_path), "--output",
                    str(baseline_path),
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("has NOT been promoted", capture.stdout)
            self.assertTrue((root / "baseline.json.source-run").is_dir())
            baseline_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            ratchet_check = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "check",
                    str(revision_run), "--policy", str(policy_path),
                    "--baseline", str(baseline_path), "--baseline-sha256",
                    baseline_sha, "--format", "text",
                ],
                cwd=root, env=environment, check=True, capture_output=True,
            )
            self.assertIn("VERDICT: PASS", ratchet_check.stdout)

            # Execute the README's positional command shape against a missing
            # local input. It must pass installed-wheel parsing and fail/refuse
            # before acquisition; exit 2 would mean the documented syntax is
            # invalid. No repository URL is supplied, so this cannot use the
            # network.
            documentation_smoke = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "run", "metrics",
                    "--input", str(root / "missing-repositories.csv"),
                    "--qualification-registry",
                    str(root / "missing-qualification.json"),
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(
                documentation_smoke.returncode,
                2,
                documentation_smoke.stdout + documentation_smoke.stderr,
            )
            self.assertNotIn(
                "unrecognized arguments",
                documentation_smoke.stdout + documentation_smoke.stderr,
            )

            hotspots_help = _run_installed_archlens_cli(
                [*installed_python, "-m", "pipeline", "hotspots", "--help"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            self.assertIn("--repository", hotspots_help.stdout)

            duplication_help = _run_installed_archlens_cli(
                [*installed_python, "-m", "pipeline", "duplication", "--help"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            self.assertIn("--tracked-only", duplication_help.stdout)
            self.assertIn("--kind {all,lexical,structural}", duplication_help.stdout)

            standalone_source = root / "standalone-source"
            standalone_source.mkdir()
            (standalone_source / "README.md").write_text(
                "installed duplication smoke\n", encoding="utf-8"
            )
            duplication_smoke = _run_with_diagnostics(
                [
                    *installed_python,
                    "-m",
                    "pipeline",
                    "duplication",
                    str(standalone_source),
                    "--format",
                    "json",
                ],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            duplication_document = json.loads(duplication_smoke.stdout)
            self.assertEqual(duplication_document["format"], "archlens-duplication")
            self.assertEqual(duplication_document["format_version"], "1.0.0")
            self.assertEqual(duplication_document["status"], "not_applicable")

            # A failed pre-evaluation check is sufficient for an isolated smoke:
            # SARIF must still be emitted, carry installed Program provenance,
            # and preserve exit 2 without importing anything from the checkout.
            invalid_policy = root / "invalid-policy.json"
            invalid_policy.write_text("{", encoding="utf-8")
            sarif = _run_installed_archlens_cli(
                [
                    *installed_python, "-m", "pipeline", "check",
                    str(root / "missing-run"), "--policy", str(invalid_policy),
                    "--format", "sarif",
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
            )
            self.assertEqual(sarif.returncode, 2, sarif.stdout + sarif.stderr)
            sarif_document = json.loads(sarif.stdout)
            self.assertEqual(sarif_document["version"], "2.1.0")
            self.assertEqual(
                sarif_document["runs"][0]["tool"]["driver"]
                ["semanticVersion"],
                "4.0.1",
            )


if __name__ == "__main__":
    unittest.main()
