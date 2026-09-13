"""BR5-B gates for opaque GitHub Action baseline argument transport."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import cache_path_for_url
from modules.ratchet import (
    capture_baseline,
    RatchetDirection,
    RatchetRule,
)
from modules.ratchet.cli_request import source_run_directory
from tests.test_github_action import (
    ACTION_METADATA,
    ACTION_RUNNER,
    _policy,
    _read_outputs,
    action,
)
from tests.test_policy_v2_check import RunBuilder
from tests.ratchet_contract_fixtures import mark_run_producer_clean
from validation.artifact_io.reader import open_run


class BaselineActionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="archlens_br5b_action_")
        cls.workspace = Path(cls._temporary.name) / "workspace with spaces Ω"
        cls.workspace.mkdir()
        cls.shared_run = RunBuilder.build(cls.workspace / "artifact source μ")
        mark_run_producer_clean(cls.shared_run)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def setUp(self) -> None:
        self.case = Path(tempfile.mkdtemp(prefix="br5b λ ", dir=self.workspace))
        self.addCleanup(lambda: shutil.rmtree(self.case, ignore_errors=True))

    def _git(self, repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _revision_bound_run(self) -> tuple[Path, str]:
        """Copy the run and bind its recorded revision to a real local Git source."""

        run = self.case / "current run"
        shutil.copytree(self.shared_run, run)
        cache_root = self.case / "recorded cache"
        repository_url = "https://github.com/acme/mono"
        revision_source = cache_path_for_url(cache_root, repository_url)
        revision_source.mkdir(parents=True)
        self._git(revision_source, "init", "--quiet")
        self._git(revision_source, "config", "user.email", "br5b@archlens.invalid")
        self._git(revision_source, "config", "user.name", "ArchLens BR5-B")
        (revision_source / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        self._git(revision_source, "add", ".")
        self._git(revision_source, "commit", "--quiet", "-m", "fixture")
        commit = self._git(revision_source, "rev-parse", "HEAD")

        prior = "a" * 40
        for path in run.rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            if prior in text:
                path.write_text(text.replace(prior, commit), encoding="utf-8")

        manifest_path = run / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("resolved_paths", {})["cache_root"] = str(cache_root)
        manifest.setdefault("effective_configuration", {})["cache_root"] = str(
            cache_root
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        view = open_run(run)
        self.assertEqual(
            view.repositories[0]["acquisition"]["analyzed_commit_sha"], commit
        )
        return run, commit

    def _baseline_bundle(
        self,
        *,
        regression: int = 1,
        malformed: bool = False,
        producer_cache_available: bool = True,
    ) -> tuple[Path, str, Path, int]:
        run, commit = self._revision_bound_run()
        path = self.case / "protected baseline.json"
        producing_run = source_run_directory(path)
        shutil.copytree(run, producing_run)
        if not producer_cache_available:
            manifest_path = producing_run / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            unavailable = self.case / "absent producer machine cache"
            manifest.setdefault("resolved_paths", {})["cache_root"] = str(
                unavailable
            )
            manifest.setdefault("effective_configuration", {})["cache_root"] = str(
                unavailable
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        view = open_run(producing_run)
        repository = view.repositories[0]
        subject_key = str(repository["subject_key"])
        current = int(repository["metrics"]["aggregate"]["lines_of_code"])
        baseline_value = current - regression
        analysis_path = producing_run / "analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = baseline_value
        analysis_path.write_text(
            json.dumps(analysis, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        for projection_path in (producing_run / "repositories").glob("*.json"):
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["metrics"]["aggregate"]["lines_of_code"] = baseline_value
            projection_path.write_text(
                json.dumps(projection, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        rule = RatchetRule(
            rule_id="ratchet.repository_loc",
            metric="repository.lines_of_code",
            metric_contract="metrics",
            scope="repository",
            direction=RatchetDirection.INCREASE_IS_WORSE,
            max_regression=0,
        )
        payload = b"{}" if malformed else capture_baseline(
            producing_run, (rule,)
        ).payload
        path.write_bytes(payload)
        return path, hashlib.sha256(payload).hexdigest(), run, current

    def _policy_path(self) -> Path:
        path = self.case / "policy.json"
        path.write_text(json.dumps(_policy(999_999)), encoding="utf-8")
        return path

    def _invoke(
        self,
        *,
        run: Path | None = None,
        baseline: Path | str | None = None,
        digest: str | None = None,
        baseline_origin: str | None = None,
        baseline_required: str = "false",
        upload: str = "false",
        event_name: str = "push",
        event: dict | None = None,
        sarif: Path | None = None,
    ) -> tuple[int, dict[str, str], str, str, Path]:
        output = self.case / "github-output.txt"
        summary = self.case / "github-summary.md"
        event_path = self.case / "event.json"
        if event is not None:
            event_path.write_text(json.dumps(event), encoding="utf-8")
        sarif_path = sarif or self.case / "result.sarif"
        environment = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "acme/project",
            "ARCHLENS_RUN": str(run or self.shared_run),
            "ARCHLENS_POLICY": str(self._policy_path()),
            "ARCHLENS_BASELINE_REQUIRED": baseline_required,
            "ARCHLENS_SARIF": str(sarif_path),
            "ARCHLENS_UPLOAD_SARIF": upload,
        }
        if baseline is not None:
            environment["ARCHLENS_BASELINE"] = str(baseline)
        if digest is not None:
            environment["ARCHLENS_BASELINE_SHA256"] = digest
        if baseline_origin is not None:
            environment["ARCHLENS_BASELINE_ORIGIN"] = baseline_origin

        captured = io.StringIO()
        with patch.dict(os.environ, environment, clear=False), redirect_stdout(captured):
            if baseline is None:
                os.environ.pop("ARCHLENS_BASELINE", None)
            if digest is None:
                os.environ.pop("ARCHLENS_BASELINE_SHA256", None)
            if baseline_origin is None:
                os.environ.pop("ARCHLENS_BASELINE_ORIGIN", None)
            code = action.main(["check"])
        return (
            code,
            _read_outputs(output),
            captured.getvalue(),
            summary.read_text(encoding="utf-8"),
            sarif_path,
        )

    def _recorded_argv(self, **kwargs) -> tuple[list[str], tuple]:
        seen: list[list[str]] = []
        real = subprocess.run

        def capture(command, *args, **keywords):
            seen.append(list(command))
            return real(command, *args, **keywords)

        with patch.object(action.subprocess, "run", side_effect=capture):
            result = self._invoke(**kwargs)
        return seen[0], result

    def test_no_baseline_preserves_existing_action_arguments_and_sarif(self):
        absent_argv, absent = self._recorded_argv()
        blank_argv, blank = self._recorded_argv(
            baseline="   ", digest="   ", sarif=self.case / "blank.sarif"
        )

        self.assertNotIn("--baseline", absent_argv)
        self.assertNotIn("--baseline-sha256", absent_argv)
        self.assertNotIn("--baseline", blank_argv)
        self.assertEqual(absent[0], blank[0])
        self.assertEqual(absent[4].read_bytes(), blank[4].read_bytes())

    def test_valid_baseline_arguments_reach_cli_and_regression_exits_one(self):
        baseline, digest, run, current = self._baseline_bundle(regression=2)

        argv, result = self._recorded_argv(
            run=run,
            baseline=baseline,
            digest=digest,
            baseline_origin="owner_controlled_artifact",
            baseline_required="true",
        )

        code, values, _stdout, _summary, sarif = result
        self.assertEqual(argv[argv.index("--baseline") + 1], str(baseline))
        self.assertEqual(argv[argv.index("--baseline-sha256") + 1], digest)
        self.assertEqual(
            argv[argv.index("--baseline-origin") + 1],
            "owner_controlled_artifact",
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["exit-code"], "1")
        comparison = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0][
            "results"
        ][0]["properties"]["archlens"]["comparison"]
        self.assertEqual(comparison["currentValue"], current)
        self.assertEqual(comparison["delta"], 2)

    def test_missing_digest_is_transport_to_cli_and_exit_two(self):
        baseline, _digest, run, _current = self._baseline_bundle()
        argv, result = self._recorded_argv(run=run, baseline=baseline)

        code, values, _stdout, _summary, sarif = result
        self.assertIn("--baseline", argv)
        self.assertNotIn("--baseline-sha256", argv)
        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "true")
        properties = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0][
            "properties"
        ]["archlens"]
        self.assertEqual(properties["failureKind"], "ratchet_admission_failed")

    def test_invalid_baseline_is_cli_exit_two_not_action_parsing(self):
        baseline, digest, run, _current = self._baseline_bundle(malformed=True)
        code, values, _stdout, _summary, sarif = self._invoke(
            run=run, baseline=baseline, digest=digest
        )

        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "true")
        properties = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0][
            "properties"
        ]["archlens"]
        self.assertEqual(properties["failureKind"], "ratchet_admission_failed")

    def test_action_and_cli_do_not_mutate_baseline(self):
        baseline, digest, run, _current = self._baseline_bundle()
        before = baseline.read_bytes()

        code, _values, *_ = self._invoke(
            run=run, baseline=baseline, digest=digest
        )

        self.assertEqual(code, 1)
        self.assertEqual(baseline.read_bytes(), before)

    def test_protected_requirement_rejects_candidate_workspace_baseline(self):
        baseline, digest, run, _current = self._baseline_bundle()
        with patch.object(action.subprocess, "run") as invoked:
            code, values, *_ = self._invoke(
                run=run,
                baseline=baseline,
                digest=digest,
                baseline_required="true",
                event_name="pull_request",
                event={
                    "pull_request": {
                        "head": {"repo": {"fork": False, "full_name": "acme/project"}}
                    }
                },
            )

        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "false")
        invoked.assert_not_called()

    def test_protected_pull_request_baseline_reaches_br2_and_is_accepted(self):
        baseline, digest, run, _current = self._baseline_bundle()

        argv, result = self._recorded_argv(
            run=run,
            baseline=baseline,
            digest=digest,
            baseline_origin="protected_base_revision",
            baseline_required="true",
            event_name="pull_request",
            event={
                "pull_request": {
                    "head": {"repo": {"fork": False, "full_name": "acme/project"}}
                }
            },
        )

        code, values, _stdout, _summary, _sarif = result
        self.assertIn("--baseline-pull-request", argv)
        self.assertEqual(
            argv[argv.index("--baseline-origin") + 1],
            "protected_base_revision",
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["exit-code"], "1")

    def test_candidate_pull_request_baseline_is_rejected_by_br2(self):
        baseline, digest, run, _current = self._baseline_bundle()

        code, values, _stdout, _summary, sarif = self._invoke(
            run=run,
            baseline=baseline,
            digest=digest,
            baseline_origin="candidate_workspace",
            event_name="pull_request",
            event={
                "pull_request": {
                    "head": {"repo": {"fork": False, "full_name": "acme/project"}}
                }
            },
        )

        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "true")
        properties = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0][
            "properties"
        ]["archlens"]
        self.assertEqual(properties["failureKind"], "ratchet_admission_failed")

    def test_local_baseline_cannot_satisfy_protected_required_mode(self):
        baseline, digest, run, _current = self._baseline_bundle()
        with patch.object(action.subprocess, "run") as invoked:
            code, values, *_ = self._invoke(
                run=run,
                baseline=baseline,
                digest=digest,
                baseline_origin="local_file",
                baseline_required="true",
            )

        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "false")
        invoked.assert_not_called()

    def test_fresh_runner_uses_current_cache_not_absent_producer_cache(self):
        baseline, digest, run, _current = self._baseline_bundle(
            producer_cache_available=False
        )

        code, values, *_ = self._invoke(
            run=run,
            baseline=baseline,
            digest=digest,
            baseline_origin="owner_controlled_artifact",
            baseline_required="true",
        )

        self.assertEqual(code, 1)
        self.assertEqual(values["exit-code"], "1")

    def test_missing_current_revision_source_fails_closed(self):
        baseline, digest, run, _current = self._baseline_bundle(
            producer_cache_available=False
        )
        manifest_path = run / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        unavailable = self.case / "absent current runner cache"
        manifest.setdefault("resolved_paths", {})["cache_root"] = str(unavailable)
        manifest.setdefault("effective_configuration", {})["cache_root"] = str(
            unavailable
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        code, values, _stdout, _summary, sarif = self._invoke(
            run=run,
            baseline=baseline,
            digest=digest,
            baseline_origin="owner_controlled_artifact",
            baseline_required="true",
        )

        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "true")
        properties = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0][
            "properties"
        ]["archlens"]
        self.assertEqual(properties["failureKind"], "ratchet_admission_failed")

    def test_required_mode_without_baseline_fails_before_cli(self):
        with patch.object(action.subprocess, "run") as invoked:
            code, values, *_ = self._invoke(baseline_required="true")
        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "false")
        invoked.assert_not_called()

    def test_baseline_path_security_matches_existing_workspace_boundary(self):
        outside = self.workspace.parent / "outside baseline.json"
        for value in (outside, "../../escape.json", "baseline\n.json"):
            with self.subTest(value=value), patch.object(
                action.subprocess, "run"
            ) as invoked:
                code, values, *_ = self._invoke(baseline=value, digest="0" * 64)
            self.assertEqual(code, 2)
            self.assertEqual(values["sarif-created"], "false")
            invoked.assert_not_called()

    def test_sarif_upload_disposition_and_metadata_are_unchanged(self):
        baseline, digest, run, _current = self._baseline_bundle()
        code, values, _stdout, _summary, sarif = self._invoke(
            run=run, baseline=baseline, digest=digest, upload="true"
        )
        metadata = json.loads(ACTION_METADATA.read_text(encoding="utf-8"))
        upload = next(
            step for step in metadata["runs"]["steps"] if step.get("id") == "upload"
        )

        self.assertEqual(code, 1)
        self.assertEqual(values["upload-eligible"], "true")
        self.assertEqual(values["upload-reason"], "eligible")
        self.assertEqual(values["sarif-created"], "true")
        self.assertEqual(
            values["sarif-sha256"], hashlib.sha256(sarif.read_bytes()).hexdigest()
        )
        self.assertEqual(
            upload["with"],
            {
                "sarif_file": "${{ steps.check.outputs.sarif-path }}",
                "category": "metrolith",
            },
        )


class BaselineActionStaticBoundaryTests(unittest.TestCase):
    SOURCE = ACTION_RUNNER.read_text(encoding="utf-8")
    TREE = ast.parse(SOURCE)

    def test_action_imports_no_archlens_or_contract_implementation(self):
        imports: list[str] = []
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertFalse(
            any(
                name.startswith(("archlens", "modules", "validation"))
                for name in imports
            )
        )

    def test_only_event_and_sarif_json_are_parsed(self):
        readers = [
            node.name
            for node in ast.walk(self.TREE)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Attribute)
                and inner.attr in {"load", "loads"}
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "json"
                for inner in ast.walk(node)
            )
        ]
        self.assertEqual(sorted(readers), ["_pull_request_is_from_fork", "_read_sarif"])

    def test_baseline_builder_only_validates_and_transports_arguments(self):
        function = next(
            node
            for node in self.TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "_baseline_arguments"
        )
        forbidden_attributes = {
            "open",
            "read_bytes",
            "read_text",
            "write_bytes",
            "write_text",
            "stat",
        }
        attributes = {
            node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
        }
        self.assertTrue(forbidden_attributes.isdisjoint(attributes))
        self.assertFalse(any(isinstance(node, ast.BinOp) for node in ast.walk(function)))

    def test_sarif_reader_does_not_inspect_baseline_or_ratchet_fields(self):
        function = next(
            node
            for node in self.TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "_read_sarif"
        )
        strings = {
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue(
            {
                "ratchet",
                "comparison",
                "baselineValue",
                "currentValue",
                "delta",
                "regressionAmount",
            }.isdisjoint(strings)
        )

    def test_action_source_has_no_baseline_lifecycle_or_decision_vocabulary(self):
        lowered = self.SOURCE.lower()
        for forbidden in (
            "parse_baseline_json",
            "admit_baseline",
            "baseline_value",
            "current_value",
            "regression_amount",
            "maximum_regression",
            "ratchetrule",
            "capture_baseline",
            "promote_baseline",
            "baseline_history",
            "baseline_waiver",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
