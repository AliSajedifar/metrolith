"""Focused, mutation-sensitive evidence for the thin GitHub integration."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.policy.document import default_policy
from tests.test_policy_v2_check import RunBuilder


REPOSITORY = Path(__file__).resolve().parent.parent
ACTION_DIRECTORY = REPOSITORY / ".github" / "actions" / "metrolith-check"
ACTION_METADATA = ACTION_DIRECTORY / "action.yml"
ACTION_RUNNER = ACTION_DIRECTORY / "metrolith_action.py"

spec = importlib.util.spec_from_file_location("metrolith_github_action", ACTION_RUNNER)
assert spec is not None and spec.loader is not None
action = importlib.util.module_from_spec(spec)
spec.loader.exec_module(action)


def _metadata() -> dict:
    # action.yml deliberately uses JSON, the strict JSON subset of YAML 1.2.
    # The standard library therefore performs a real full-document parse with
    # no test-only dependency or permissive hand-written YAML approximation.
    return json.loads(ACTION_METADATA.read_text(encoding="utf-8"))


def _policy(threshold: int) -> dict:
    return {
        "policy_document_format_version": "2.0.0",
        "name": "github-action-test",
        "metric_rules": [{
            "id": "repo.loc",
            "metric": "repository.lines_of_code",
            "operator": "gt",
            "threshold": threshold,
        }],
    }


def _read_outputs(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


class ActionMetadataTests(unittest.TestCase):
    def test_action_metadata_is_parseable_yaml_12_json_subset(self):
        metadata = _metadata()
        self.assertEqual(metadata["runs"]["using"], "composite")
        self.assertIsInstance(metadata["runs"]["steps"], list)

    def test_input_api_is_small_required_and_safe_by_default(self):
        inputs = _metadata()["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "run", "policy", "hotspots", "duplication", "baseline",
                "baseline-sha256", "baseline-origin", "baseline-required",
                "sarif", "upload-sarif", "protected-required",
                "policy-sha256", "evaluator-repository",
                "evaluator-revision", "evaluator-source-sha256",
                "evidence-receipt", "evidence-receipt-sha256",
            },
        )
        self.assertTrue(inputs["run"]["required"])
        self.assertTrue(inputs["policy"]["required"])
        self.assertEqual(inputs["sarif"]["default"], "metrolith-results/metrolith.sarif")
        self.assertEqual(inputs["upload-sarif"]["default"], "false")
        # Each evidence input names an EXISTING document to pass through. Both
        # are optional and default to empty, so an unset input is
        # indistinguishable from the action before that input existed.
        for name in ("hotspots", "duplication"):
            with self.subTest(input=name):
                self.assertFalse(inputs[name]["required"])
                self.assertEqual(inputs[name]["default"], "")
        self.assertEqual(inputs["baseline"]["default"], "")
        self.assertEqual(inputs["baseline-sha256"]["default"], "")
        self.assertEqual(inputs["baseline-origin"]["default"], "")
        self.assertEqual(inputs["baseline-required"]["default"], "false")
        self.assertEqual(inputs["protected-required"]["default"], "false")
        for name in (
            "policy-sha256", "evaluator-repository", "evaluator-revision",
            "evaluator-source-sha256", "evidence-receipt",
            "evidence-receipt-sha256",
        ):
            self.assertEqual(inputs[name]["default"], "")
        # The action stays a wrapper. Every forbidden name here would make it
        # decide something -- a threshold, an analysis to run, a command to
        # execute -- rather than pass an existing document to `archlens check`.
        forbidden = {
            "max-complexity", "max-cognitive", "max-loc", "command", "args",
            "analyze", "analyse", "threshold", "thresholds", "metric",
            "metrics", "severity", "fail-on", "score",
        }
        self.assertTrue(forbidden.isdisjoint(inputs))

    def test_every_input_is_a_path_or_a_switch_never_a_value_to_judge_by(self):
        """A wrapper carries files and booleans; it never carries a number."""
        for name, spec in _metadata()["inputs"].items():
            with self.subTest(input=name):
                default = spec.get("default", "")
                self.assertIsInstance(default, str)
                self.assertFalse(
                    default.strip().lstrip("-").isdigit(),
                    "a numeric default would be a threshold the action chose",
                )

    def test_supported_python_and_external_actions_are_immutable_pins(self):
        steps = _metadata()["runs"]["steps"]
        setup = steps[0]
        self.assertEqual(setup["with"]["python-version"], "3.13.9")
        external = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(external, [
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            "github/codeql-action/upload-sarif@faaa5d804fc648d0fdb28822a8e36cf7d0a6132c",
        ])
        for reference in external:
            owner_action, sha = reference.rsplit("@", 1)
            self.assertIn(owner_action, {"actions/setup-python", "github/codeql-action/upload-sarif"})
            self.assertEqual(len(sha), 40)
            int(sha, 16)

    def test_upload_is_not_swallowed_and_final_exit_is_always_propagated(self):
        steps = _metadata()["runs"]["steps"]
        upload = next(step for step in steps if step.get("id") == "upload")
        self.assertNotIn("continue-on-error", upload)
        self.assertIn("upload-eligible", upload["if"])
        final = steps[-1]
        self.assertEqual(final["if"], "${{ always() }}")
        self.assertIn("propagate", final["run"])
        verify = next(step for step in steps if " verify" in step.get("run", ""))
        self.assertNotIn("continue-on-error", verify)
        self.assertIn("always()", verify["if"])

    def test_pull_request_target_guard_precedes_source_install(self):
        steps = _metadata()["runs"]["steps"]
        preflight_index = next(
            index for index, step in enumerate(steps)
            if "preflight" in step.get("run", "")
        )
        install_index = next(
            index for index, step in enumerate(steps)
            if " install" in step.get("run", "")
        )
        self.assertLess(preflight_index, install_index)

    def test_action_cannot_grant_permissions_or_accept_a_token(self):
        metadata = _metadata()
        self.assertNotIn("permissions", metadata)
        self.assertNotIn("token", metadata["inputs"])
        upload = next(step for step in metadata["runs"]["steps"] if step.get("id") == "upload")
        self.assertNotIn("token", upload["with"])


class StaticSecurityGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ACTION_RUNNER.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_wrapper_imports_no_archlens_policy_metric_or_sarif_implementation(self):
        imported = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any(name == "modules" or name.startswith("modules.") for name in imported))

    def test_check_path_invokes_one_process_with_no_shell(self):
        function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_check"
        )
        calls = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
        ]
        self.assertEqual(len(calls), 1)
        keywords = {item.arg: item.value for item in calls[0].keywords}
        self.assertIsInstance(keywords["shell"], ast.Constant)
        self.assertIs(keywords["shell"].value, False)
        command = calls[0].args[0]
        self.assertIsInstance(command, ast.List)
        literals = [item.value for item in command.elts if isinstance(item, ast.Constant)]
        self.assertEqual(literals.count("check"), 1)
        self.assertIn("sarif", literals)

    def test_wrapper_has_no_eval_exec_or_sarif_writer(self):
        called_names = set()
        attributes = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            if isinstance(node, ast.Attribute):
                attributes.add(node.attr)
        self.assertTrue({"eval", "exec"}.isdisjoint(called_names))
        self.assertTrue({"write_bytes", "write_text"}.isdisjoint(attributes))

    def test_sarif_reader_does_not_inspect_results_rules_metrics_or_thresholds(self):
        function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_read_sarif"
        )
        strings = {
            node.value for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue({
            "results", "ruleId", "rules", "metric", "threshold", "observedValue",
            "partialFingerprints", "locations",
        }.isdisjoint(strings))


class GithubActionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory(prefix="archlens_github_action_")
        cls.workspace = Path(cls._temporary.name) / "workspace with spaces Ω"
        cls.workspace.mkdir()
        cls.shared_run = RunBuilder.build(cls.workspace / "artifact source μ")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="case λ ", dir=self.workspace))
        self.addCleanup(lambda: shutil.rmtree(self.case, ignore_errors=True))

    def _write_policy(
        self,
        payload: dict | str,
        name: str = "policy [safe] & ; $(echo injected) δ.json",
    ) -> Path:
        path = self.case / name
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
        return path

    def _invoke(
        self,
        *,
        run: Path | str | None = None,
        policy: Path | str | None = None,
        sarif: Path | str | None = None,
        upload: str = "false",
        event_name: str = "push",
        event: dict | None = None,
        secret_path: str | None = None,
        hotspots: Path | str | None = None,
        duplication: Path | str | None = None,
    ) -> tuple[int, dict[str, str], str, str, Path]:
        output = self.case / "github-output.txt"
        summary = self.case / "github-summary.md"
        event_path = self.case / "event.json"
        if event is not None:
            event_path.write_text(json.dumps(event), encoding="utf-8")
        sarif_path = (
            Path(sarif) if sarif is not None
            else self.case / "result #1 ; $(echo injected) μ.sarif"
        )
        env = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "acme/project",
            "METROLITH_RUN": str(self.shared_run if run is None else run),
            "METROLITH_POLICY": str(policy if policy is not None else self._write_policy(_policy(999999))),
            "METROLITH_SARIF": str(sarif_path),
            "METROLITH_UPLOAD_SARIF": upload,
        }
        # Set ONLY when the caller supplies one. An absent optional input must
        # leave the environment exactly as it was before the input existed.
        if hotspots is not None:
            env["METROLITH_HOTSPOTS"] = str(hotspots)
        if duplication is not None:
            env["METROLITH_DUPLICATION"] = str(duplication)
        if secret_path is not None:
            env["METROLITH_POLICY"] = secret_path
        captured = io.StringIO()
        with patch.dict(os.environ, env, clear=False), redirect_stdout(captured):
            os.environ.pop("GITHUB_TOKEN", None)
            exit_code = action.main(["check"])
        values = _read_outputs(output)
        summary_text = summary.read_text(encoding="utf-8")
        return exit_code, values, captured.getvalue(), summary_text, sarif_path

    def test_paths_with_spaces_unicode_and_special_characters_pass(self):
        code, values, stdout, summary, sarif = self._invoke()
        self.assertEqual(code, 0)
        self.assertEqual(values["exit-code"], "0")
        self.assertEqual(values["verdict"], "pass")
        self.assertEqual(values["sarif-created"], "true")
        self.assertTrue(sarif.is_file())
        self.assertEqual(values["sarif-sha256"], hashlib.sha256(sarif.read_bytes()).hexdigest())
        self.assertEqual(stdout, "")
        self.assertIn("Result: PASS", summary)
        self.assertIn("result #1 ; $(echo injected) μ.sarif", summary)
        self.assertFalse((self.workspace / "injected").exists())

    def test_exit_zero_one_and_two_are_preserved(self):
        pass_code, pass_values, *_ = self._invoke(policy=self._write_policy(_policy(999999), "pass.json"))
        violation_code, violation_values, *_ = self._invoke(
            policy=self._write_policy(_policy(0), "violation.json"),
            sarif=self.case / "violation.sarif",
        )
        error_code, error_values, *_ = self._invoke(
            policy=self._write_policy("{", "invalid.json"),
            sarif=self.case / "invalid.sarif",
        )
        self.assertEqual((pass_code, violation_code, error_code), (0, 1, 2))
        self.assertEqual(
            (pass_values["exit-code"], violation_values["exit-code"], error_values["exit-code"]),
            ("0", "1", "2"),
        )
        self.assertEqual(error_values["failure-kind"], "policy_invalid")

    def test_failed_run_exit_two_is_propagated_with_sarif(self):
        failed_run = self.case / "failed authoritative run"
        shutil.copytree(self.shared_run, failed_run)
        status_path = failed_run / "run_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["status"] = "failed"
        status_path.write_text(
            json.dumps(status, indent=2), encoding="utf-8", newline="\n"
        )

        code, values, _stdout, _summary, sarif = self._invoke(
            run=failed_run,
            policy=self._write_policy(_policy(999999), "metric-only.json"),
            sarif=self.case / "failed-run.sarif",
        )

        self.assertEqual(code, 2)
        self.assertEqual(values["exit-code"], "2")
        self.assertEqual(values["verdict"], "error")
        self.assertEqual(values["failure-kind"], "run_unreadable")
        self.assertEqual(values["sarif-created"], "true")
        self.assertFalse(
            json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]
            ["invocations"][0]["executionSuccessful"]
        )

    def test_policy_v1_is_passed_to_the_existing_check_path_unchanged(self):
        policy = self._write_policy(default_policy().as_dict(), "policy-v1.json")
        code, values, *_ = self._invoke(
            policy=policy, sarif=self.case / "policy-v1.sarif"
        )
        self.assertIn(code, (0, 1))
        self.assertEqual(values["exit-code"], str(code))
        self.assertEqual(values["sarif-created"], "true")

    def test_missing_policy_and_missing_artifact_remain_exit_two_with_sarif(self):
        missing_policy = self.case / "missing policy.json"
        code1, values1, *_ = self._invoke(policy=missing_policy, sarif=self.case / "missing-policy.sarif")
        missing_run = self.case / "missing run"
        code2, values2, *_ = self._invoke(
            run=missing_run,
            policy=self._write_policy(_policy(999999), "valid.json"),
            sarif=self.case / "missing-run.sarif",
        )
        self.assertEqual((code1, code2), (2, 2))
        self.assertEqual(values1["failure-kind"], "policy_invalid")
        self.assertEqual(values2["failure-kind"], "run_unreadable")
        self.assertEqual((values1["sarif-created"], values2["sarif-created"]), ("true", "true"))

    def test_invalid_output_path_is_integration_error_and_does_not_invoke_cli(self):
        with patch.object(action.subprocess, "run") as invoked:
            code, values, stdout, summary, _ = self._invoke(sarif=self.case)
        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "false")
        invoked.assert_not_called()
        self.assertNotIn(str(self.case), stdout)
        self.assertIn("Result: ERROR", summary)

    def test_paths_resolving_outside_workspace_are_rejected_without_disclosure(self):
        secret = "TOP-SECRET-policy-name"
        outside = self.workspace.parent / secret
        code, values, stdout, summary, _ = self._invoke(secret_path=str(outside))
        self.assertEqual(code, 2)
        self.assertEqual(values["upload-reason"], "integration-error")
        self.assertNotIn(secret, stdout)
        self.assertNotIn(secret, summary)

    def test_upload_disabled_and_absent_token_do_not_change_check(self):
        code, values, *_ = self._invoke(upload="false")
        self.assertEqual(code, 0)
        self.assertEqual(values["upload-eligible"], "false")
        self.assertEqual(values["upload-reason"], "disabled")

    def test_fork_pull_request_is_evaluated_but_upload_is_skipped(self):
        event = {
            "pull_request": {"head": {"repo": {"fork": True, "full_name": "fork/project"}}}
        }
        code, values, *_ = self._invoke(upload="true", event_name="pull_request", event=event)
        self.assertEqual(code, 0)
        self.assertEqual(values["upload-eligible"], "false")
        self.assertEqual(values["upload-reason"], "fork-pull-request")

    def test_same_repository_push_upload_is_only_marked_eligible(self):
        code, values, *_ = self._invoke(upload="true")
        self.assertEqual(code, 0)
        self.assertEqual(values["upload-eligible"], "true")
        self.assertEqual(values["upload-reason"], "eligible")

    def test_pull_request_target_is_refused_before_cli_execution(self):
        with patch.object(action.subprocess, "run") as invoked:
            code, values, *_ = self._invoke(event_name="pull_request_target")
        self.assertEqual(code, 2)
        self.assertEqual(values["sarif-created"], "false")
        invoked.assert_not_called()

    def test_wrapper_sarif_bytes_equal_direct_cli_bytes(self):
        policy = self._write_policy(_policy(0), "equivalence policy.json")
        wrapper_path = self.case / "wrapper result.sarif"
        direct_path = self.case / "direct result.sarif"
        code, _, *_ = self._invoke(policy=policy, sarif=wrapper_path)
        direct = subprocess.run(
            [
                action._metrolith_executable(), "check", str(self.shared_run), "--policy", str(policy),
                "--format", "sarif", "--output", str(direct_path),
            ],
            check=False,
            shell=False,
            stdout=subprocess.DEVNULL,
        )
        self.assertEqual((code, direct.returncode), (1, 1))
        self.assertEqual(wrapper_path.read_bytes(), direct_path.read_bytes())


class HotspotEvidenceInputTests(GithubActionIntegrationTests):
    """The optional `hotspots` input: a pass-through, and nothing more.

    Inherits the integration harness so evidence goes through the same real
    workspace, the same real run bundle and the same real `archlens check`
    process every other action test uses.
    """

    def _hotspot_document(self, *, run_id: str | None = None) -> Path:
        """A hotspot document matching the shared run, written into the workspace.

        Built from the run's OWN identity so admission can succeed: a document
        whose `source_run.run_id` is invented would only ever exercise the
        refusal path, and the pass-through would never be shown to work.
        """
        from modules.hotspots import classify_attention, validate_hotspot_document
        from modules.subject import subject_key_of
        from validation.artifact_io.reader import open_run

        view = open_run(self.shared_run)
        subject = subject_key_of(dict(next(iter(view.repositories))))

        def evidence(value):
            return {
                "status": "measured", "signal": value,
                "cohort": {"distinct_value_count": 2, "distinct_rank": 2},
            }

        row = {
            "subject_key": subject, "file": "app.py", "language": "Python",
            "complexity": {
                "status": "measured", "cognitive_complexity_total": 40,
                "cyclomatic_complexity_total": 25, "max_nesting_depth_max": 4,
            },
            "churn": {
                "status": "measured", "commits": 90, "touched_lines": 300,
                "touched_lines_status": "measured",
            },
            "complexity_signal": evidence("high"),
            "churn_signal": evidence("high"),
            "classification": classify_attention("high", "high"),
            "reasons": ["action integration fixture"],
        }
        document = {
            "format": "archlens-hotspots", "format_version": "1.0.0",
            "product_name": "ArchLens",
            "program_version": view.manifest.get("program_version"),
            "source_run": {
                "run_id": run_id if run_id is not None else view.run_id,
                "program_version": view.manifest.get("program_version"),
                "artifact_schema_version": view.manifest.get(
                    "artifact_schema_version"
                ),
            },
            "purpose": "maintenance attention",
            "classification_model": {"score": None, "thresholds": None},
            "git_semantics": {}, "ordering": "attention class",
            "repositories": [{
                "subject_key": subject, "repository_url": None,
                "analyzed_commit_sha": None,
                "history": {"status": "measured"},
                "file_count": 1, "classified_file_count": 1,
            }],
            "hotspots": [row],
        }
        validate_hotspot_document(document)
        path = self.case / "hotspots ; $(echo injected) ω.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    @staticmethod
    def _hotspot_policy(threshold: int) -> dict:
        return {
            "policy_document_format_version": "2.1.0",
            "name": "github-action-hotspot-test",
            "metric_rules": [{
                "id": "hot.churn", "metric": "hotspot_file.churn_commits",
                "operator": "gt", "threshold": threshold,
            }],
        }

    def _recorded_argv(self, **kwargs) -> list[str]:
        """The exact command line the wrapper builds, without running it."""
        seen: list[list[str]] = []
        real = subprocess.run

        def capture(command, *args, **keywords):
            seen.append(list(command))
            return real(command, *args, **keywords)

        with patch.object(subprocess, "run", side_effect=capture):
            self._invoke(**kwargs)
        return seen[0]

    # -- absent input ------------------------------------------------------

    def test_an_absent_input_adds_no_argument(self):
        argv = self._recorded_argv()
        self.assertNotIn("--hotspots", argv)

    def test_an_empty_input_is_indistinguishable_from_an_absent_one(self):
        """GitHub passes an unset optional input as "". It must mean unset."""
        absent = self._recorded_argv()
        empty = self._recorded_argv(hotspots="")
        blank = self._recorded_argv(hotspots="   ")
        self.assertEqual(absent[1:], empty[1:])
        self.assertEqual(absent[1:], blank[1:])

    def test_absent_evidence_produces_identical_sarif_bytes(self):
        first_path = self.case / "absent #1 ; $(echo injected) μ.sarif"
        second_path = self.case / "empty #2 ; $(echo injected) Ω.sarif"
        self.assertFalse(first_path.exists())
        self.assertFalse(second_path.exists())
        first_code, first_values, _out, _summary, first_sarif = self._invoke(sarif=first_path)
        original = first_sarif.read_bytes()
        second_code, second_values, _o, _s, second_sarif = self._invoke(hotspots="", sarif=second_path)
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual((first_values["sarif-created"], second_values["sarif-created"]), ("true", "true"))
        self.assertNotEqual(first_sarif, second_sarif)
        self.assertTrue(original)
        self.assertEqual(first_sarif.read_bytes(), original)
        self.assertEqual(second_sarif.read_bytes(), original)
        self.assertFalse((self.workspace / "injected").exists())

    # -- provided input ----------------------------------------------------

    def test_a_supplied_document_is_passed_through_as_a_resolved_path(self):
        document = self._hotspot_document()
        argv = self._recorded_argv(hotspots=document)
        self.assertIn("--hotspots", argv)
        self.assertEqual(argv[argv.index("--hotspots") + 1], str(document))
        # Still one `check`, still the same output contract around it.
        self.assertEqual(argv.count("check"), 1)
        self.assertIn("--policy", argv)
        self.assertIn("sarif", argv)

    def test_a_relative_input_resolves_inside_the_workspace(self):
        document = self._hotspot_document()
        relative = document.relative_to(self.workspace).as_posix()
        argv = self._recorded_argv(hotspots=relative)
        self.assertEqual(
            Path(argv[argv.index("--hotspots") + 1]).resolve(), document.resolve()
        )

    def test_admitted_evidence_reaches_the_gate_and_the_sarif(self):
        """The whole point: workflow -> check -> SARIF, with evidence admitted."""
        document = self._hotspot_document()
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(self._hotspot_policy(5), "hot-fail.json"),
            hotspots=document,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["verdict"], "fail")
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        run = payload["runs"][0]
        self.assertEqual(
            run["properties"]["archlens"]["evidence"]["hotspots"]["admission"],
            "admitted",
        )
        result = run["results"][0]
        self.assertEqual(
            result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
            "app.py",
        )
        self.assertEqual(
            result["properties"]["archlens"]["evidence"]["hotspot_classification"],
            "high_attention",
        )

    def test_admitted_evidence_can_also_pass(self):
        document = self._hotspot_document()
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(self._hotspot_policy(999999), "hot-pass.json"),
            hotspots=document,
        )
        self.assertEqual(code, 0)
        self.assertEqual(values["verdict"], "pass")
        self.assertEqual(values["sarif-created"], "true")

    def test_a_document_from_another_run_is_refused_by_the_gate_not_the_wrapper(self):
        """Evidence semantics belong to `check`; the wrapper only carries the file."""
        document = self._hotspot_document(run_id="run-SOMETHING-ELSE")
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(self._hotspot_policy(5), "hot-other.json"),
            hotspots=document,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["runs"][0]["properties"]["archlens"]["evidence"]["hotspots"][
                "admission"],
            "provenance_mismatch",
        )

    # -- containment and rejection ----------------------------------------

    def test_a_path_outside_the_workspace_is_refused_without_running_check(self):
        outside = Path(tempfile.gettempdir()) / "outside-hotspots.json"
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, values, _out, _summary, sarif = self._invoke(hotspots=outside)
        self.assertEqual(code, 2)
        self.assertEqual(values["verdict"], "error")
        self.assertEqual(values["sarif-created"], "false")
        self.assertFalse(sarif.exists())

    def test_a_traversal_path_is_refused(self):
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, _out, _summary, _sarif = self._invoke(
                hotspots="../../escape.json"
            )
        self.assertEqual(code, 2)

    def test_a_control_character_path_is_refused(self):
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, _out, _summary, _sarif = self._invoke(
                hotspots="evidence\n.json"
            )
        self.assertEqual(code, 2)

    def test_a_refusal_discloses_no_path(self):
        outside = Path(tempfile.gettempdir()) / "secret-hotspots.json"
        code, _values, stdout, summary, _sarif = self._invoke(hotspots=outside)
        self.assertEqual(code, 2)
        self.assertNotIn(str(outside), stdout + summary)
        self.assertNotIn("secret-hotspots", stdout + summary)

    def test_a_missing_file_is_the_gates_decision_not_an_integration_error(self):
        """The wrapper does not stat the file; `check` reports what it found."""
        absent = self.case / "not-written.json"
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(self._hotspot_policy(5), "hot-missing.json"),
            hotspots=absent,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["runs"][0]["properties"]["archlens"]["evidence"]["hotspots"][
                "admission"],
            "unreadable",
        )

    # -- unchanged guarantees ---------------------------------------------

    def test_fork_pull_request_gating_is_unchanged_with_evidence(self):
        document = self._hotspot_document()
        code, values, *_ = self._invoke(
            hotspots=document, upload="true", event_name="pull_request",
            event={"pull_request": {"head": {"repo": {"fork": True}}}},
        )
        self.assertEqual(code, 0)
        self.assertEqual(values["upload-eligible"], "false")
        self.assertEqual(values["upload-reason"], "fork-pull-request")

    def test_same_repository_upload_eligibility_is_unchanged_with_evidence(self):
        document = self._hotspot_document()
        _code, values, *_ = self._invoke(hotspots=document, upload="true")
        self.assertEqual(values["upload-eligible"], "true")
        self.assertEqual(values["upload-reason"], "eligible")

    def test_pull_request_target_is_still_refused_with_evidence(self):
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, *_ = self._invoke(
                hotspots=self._hotspot_document(),
                event_name="pull_request_target",
            )
        self.assertEqual(code, 2)

    def test_the_wrapper_still_runs_no_analysis_of_its_own(self):
        """It may NAME the input; it may not know what is inside the document.

        Each evidence kind appears as an environment variable, a flag and a path
        label, which is exactly what carrying a file requires. What must not
        appear is the vocabulary of the analysis itself -- a wrapper that
        mentioned an attention class, a churn signal, a clone group or a
        fingerprint would be reading the evidence rather than passing it on.

        The ban is over the RAW SOURCE, comments included, and deliberately so.
        `modules/policy/sarif.py` gets an AST-level version of this guard because
        that module has to explain which family-specific key it refuses to read;
        a wrapper has nothing to explain. It transports a path, so the words
        should not be there at all.
        """
        source = ACTION_RUNNER.read_text(encoding="utf-8")
        for forbidden in (
            # hotspot vocabulary
            "attention", "classification", "churn", "complexity", "signal",
            "classify_attention", "attention_class_counts",
            # duplication vocabulary
            "clone", "fingerprint", "occurrence", "lexical", "structural",
            "group_id", "duplicated_nloc", "validate_duplication_document",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.lower())

        for variable, flag, label in action.EVIDENCE_INPUTS:
            with self.subTest(input=label):
                self.assertIn(variable, source)
                self.assertIn(flag, source)

    def test_the_wrapper_never_touches_the_evidence_path(self):
        """Reading it would make the wrapper a second interpreter of evidence.

        Asserted against the evidence path specifically, not against reads in
        general: `run_check` legitimately reads `pyproject.toml` to compare the
        installed version, and forbidding that would be forbidding an unrelated
        safety check.

        This guard used to name `hotspots_path`, a local in `run_check`. DP3
        moved evidence resolution into `_evidence_arguments`, at which point the
        old form matched nothing and passed VACUOUSLY -- a guard that survives
        the refactor it was meant to police is worse than no guard. It now
        targets the function the paths actually live in, and covers every kind
        at once rather than one named local.
        """
        tree = ast.parse(ACTION_RUNNER.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_evidence_arguments"
        )
        touched = {
            node.attr for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "resolved"
        }
        self.assertEqual(
            touched, set(),
            "the wrapper calls a method on the evidence path; it must only "
            "pass it to the command",
        )
        # And the only thing done with it is `str(...)`.
        calls = {
            node.func.id for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            calls.issubset({"str", "_optional_workspace_path", "tuple"}), calls
        )


class DuplicationEvidenceInputTests(GithubActionIntegrationTests):
    """The optional `duplication` input: a pass-through, and nothing more.

    Same harness, same real workspace, same real run bundle and the same real
    `archlens check` process as every other action test, so what is proved here
    is the wrapper's behaviour and not a mock's.
    """

    _CLONE_BODY = """\
def {name}(seed):
    one = seed + 1
    two = one * 2
    three = two - 3
    four = three / 4
    five = four + one
    six = five * two
    seven = six - three
    eight = seven + four
    return eight
"""

    def _duplication_document(self, *, revision: str | None = None) -> Path:
        """A real analyzer document, bound to the shared run's own identity.

        Produced by `analyze_duplication_snapshot` over a throwaway git
        repository, then re-pointed at the run's commit and analysis scope hash
        so admission can SUCCEED. Inventing a document would only ever exercise
        the refusal path, and the pass-through would never be shown to work.

        Only the two binding fields are touched; every count, group and
        occurrence is the analyzer's own, and the document is re-validated
        against its own contract before use.
        """
        from modules.duplication.output import (
            analyze_duplication_snapshot,
            validate_duplication_document,
        )
        from modules.local_source import prepare_local_source
        from modules.subject import subject_key_of
        from validation.artifact_io.reader import open_run

        view = open_run(self.shared_run)
        repository = dict(next(iter(view.repositories)))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "clones"
            for name, relative in (
                ("alpha", "src/a.py"), ("beta", "src/b.py"), ("gamma", "src/c.py")
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    self._CLONE_BODY.format(name=name), encoding="utf-8",
                    newline="\n",
                )

            def git(*arguments: str) -> None:
                subprocess.run(
                    ["git", "-C", str(root), *arguments],
                    check=True, capture_output=True, text=True,
                )

            git("init", "--quiet")
            git("config", "user.email", "action@archlens.invalid")
            git("config", "user.name", "ArchLens DP3")
            git("add", ".")
            git("commit", "--quiet", "-m", "fixture")
            with prepare_local_source(root) as snapshot:
                document = analyze_duplication_snapshot(
                    snapshot, requested_kinds=("lexical", "structural")
                ).document

        document["source"]["resolved_revision"] = (
            revision if revision is not None
            else (repository.get("acquisition") or {}).get("analyzed_commit_sha")
        )
        document["source"]["analysis_scope_hash"] = repository.get(
            "analysis_scope_hash"
        )
        validate_duplication_document(document)
        self.subject = subject_key_of(repository)

        path = self.case / "duplication ; $(echo injected) λ.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    @staticmethod
    def _duplication_policy(threshold: int, metric: str) -> dict:
        return {
            "policy_document_format_version": "2.2.0",
            "name": "github-action-duplication-test",
            "metric_rules": [{
                "id": "dup.rule", "metric": metric,
                "operator": "gt", "threshold": threshold,
            }],
        }

    def _recorded_argv(self, **kwargs) -> list[str]:
        """The exact command line the wrapper builds, without running it."""
        seen: list[list[str]] = []
        real = subprocess.run

        def capture(command, *args, **keywords):
            seen.append(list(command))
            return real(command, *args, **keywords)

        with patch.object(subprocess, "run", side_effect=capture):
            self._invoke(**kwargs)
        return seen[0]

    # -- absent input ------------------------------------------------------

    def test_an_absent_input_adds_no_argument(self):
        argv = self._recorded_argv()
        self.assertNotIn("--duplication", argv)

    def test_an_empty_input_is_indistinguishable_from_an_absent_one(self):
        absent = self._recorded_argv()
        empty = self._recorded_argv(duplication="")
        blank = self._recorded_argv(duplication="   ")
        self.assertEqual(absent[1:], empty[1:])
        self.assertEqual(absent[1:], blank[1:])

    def test_absent_evidence_produces_identical_sarif_bytes(self):
        first_path = self.case / "absent #1 ; $(echo injected) μ.sarif"
        second_path = self.case / "empty #2 ; $(echo injected) Ω.sarif"
        self.assertFalse(first_path.exists())
        self.assertFalse(second_path.exists())
        first_code, first_values, _out, _summary, first_sarif = self._invoke(sarif=first_path)
        original = first_sarif.read_bytes()
        second_code, second_values, _o, _s, second_sarif = self._invoke(duplication="", sarif=second_path)
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual((first_values["sarif-created"], second_values["sarif-created"]), ("true", "true"))
        self.assertNotEqual(first_sarif, second_sarif)
        self.assertTrue(original)
        self.assertEqual(first_sarif.read_bytes(), original)
        self.assertEqual(second_sarif.read_bytes(), original)
        self.assertFalse((self.workspace / "injected").exists())

    def test_the_hotspot_command_line_is_unchanged_by_this_input_existing(self):
        """Adding a kind must not move an existing one.

        A workflow that passes only `--hotspots` builds the same argument array
        it built before `duplication` existed, which is what makes this input
        additive rather than a change to the Action's behaviour.
        """
        document = HotspotEvidenceInputTests._hotspot_document(self)
        argv = self._recorded_argv(hotspots=document)
        self.assertNotIn("--duplication", argv)
        self.assertIn("--gate-mode", argv)
        self.assertEqual(argv[argv.index("--gate-mode") + 1], "local_unprotected")
        self.assertIn("--hotspots", argv)
        self.assertEqual(argv[argv.index("--hotspots") + 2], "--format")

    # -- provided input ----------------------------------------------------

    def test_a_supplied_document_is_passed_through_as_a_resolved_path(self):
        document = self._duplication_document()
        argv = self._recorded_argv(duplication=document)
        self.assertIn("--duplication", argv)
        self.assertEqual(argv[argv.index("--duplication") + 1], str(document))
        self.assertEqual(argv.count("check"), 1)
        self.assertIn("--policy", argv)
        self.assertIn("sarif", argv)

    def test_a_relative_input_resolves_inside_the_workspace(self):
        document = self._duplication_document()
        relative = document.relative_to(self.workspace).as_posix()
        argv = self._recorded_argv(duplication=relative)
        self.assertEqual(
            Path(argv[argv.index("--duplication") + 1]).resolve(),
            document.resolve(),
        )

    def test_both_evidence_kinds_travel_together_in_a_fixed_order(self):
        document = self._duplication_document()
        hotspots = HotspotEvidenceInputTests._hotspot_document(self)
        argv = self._recorded_argv(hotspots=hotspots, duplication=document)
        self.assertLess(argv.index("--hotspots"), argv.index("--duplication"))
        self.assertEqual(argv[argv.index("--hotspots") + 1], str(hotspots))
        self.assertEqual(argv[argv.index("--duplication") + 1], str(document))

    def test_admitted_evidence_reaches_the_gate_and_the_sarif(self):
        """The whole point: workflow -> check -> SARIF, with evidence admitted."""
        document = self._duplication_document()
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    1, "duplication_group.lexical_occurrence_count"
                ),
                "dup-fail.json",
            ),
            duplication=document,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["verdict"], "fail")
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        run = payload["runs"][0]
        self.assertEqual(
            run["properties"]["archlens"]["evidence"]["duplication"]["admission"],
            "admitted",
        )
        result = run["results"][0]
        self.assertEqual(
            result["properties"]["archlens"]["scope"], "duplication_group"
        )
        # The DP2 projection arrives through the Action unchanged.
        self.assertEqual(
            [
                item["physicalLocation"]["artifactLocation"]["uri"]
                for item in result["relatedLocations"]
            ],
            ["src/b.py", "src/c.py"],
        )

    def test_admitted_evidence_can_also_pass(self):
        document = self._duplication_document()
        code, values, _out, _summary, _sarif = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    999999, "repository.duplication_lexical_group_count"
                ),
                "dup-pass.json",
            ),
            duplication=document,
        )
        self.assertEqual(code, 0)
        self.assertEqual(values["verdict"], "pass")
        self.assertEqual(values["sarif-created"], "true")

    def test_a_document_from_another_revision_is_refused_by_the_gate(self):
        """Evidence semantics belong to `check`; the wrapper only carries it."""
        document = self._duplication_document(revision="b" * 40)
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    0, "repository.duplication_lexical_group_count"
                ),
                "dup-other.json",
            ),
            duplication=document,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["runs"][0]["properties"]["archlens"]["evidence"][
                "duplication"]["admission"],
            "provenance_mismatch",
        )

    def test_a_malformed_document_is_the_gates_decision(self):
        """Invalid evidence passes THROUGH the wrapper and is handled by policy."""
        path = self.case / "not-a-duplication-document.json"
        path.write_text('{"format": "archlens-duplication"}', encoding="utf-8")
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    0, "repository.duplication_lexical_group_count"
                ),
                "dup-malformed.json",
            ),
            duplication=path,
        )
        # Exit 1, not 2: the wrapper had no integration problem, and a document
        # the gate refused is a policy outcome.
        self.assertEqual(code, 1)
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertIn(
            payload["runs"][0]["properties"]["archlens"]["evidence"][
                "duplication"]["admission"],
            ("contract_incompatible", "validator_rejected"),
        )

    def test_a_missing_file_is_the_gates_decision_not_an_integration_error(self):
        absent = self.case / "never-written.json"
        code, values, _out, _summary, sarif = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    0, "repository.duplication_lexical_group_count"
                ),
                "dup-missing.json",
            ),
            duplication=absent,
        )
        self.assertEqual(code, 1)
        self.assertEqual(values["sarif-created"], "true")
        payload = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["runs"][0]["properties"]["archlens"]["evidence"][
                "duplication"]["admission"],
            "unreadable",
        )

    # -- containment and rejection ----------------------------------------

    def test_a_path_outside_the_workspace_is_refused_without_running_check(self):
        outside = Path(tempfile.gettempdir()) / "outside-duplication.json"
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, values, _out, _summary, sarif = self._invoke(duplication=outside)
        self.assertEqual(code, 2)
        self.assertEqual(values["verdict"], "error")
        self.assertEqual(values["sarif-created"], "false")
        self.assertFalse(sarif.exists())

    def test_a_traversal_path_is_refused(self):
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, *_ = self._invoke(duplication="../../escape.json")
        self.assertEqual(code, 2)

    def test_a_control_character_path_is_refused(self):
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, *_ = self._invoke(duplication="evidence\n.json")
        self.assertEqual(code, 2)

    def test_a_refusal_discloses_no_path(self):
        outside = Path(tempfile.gettempdir()) / "secret-duplication.json"
        code, _values, stdout, summary, _sarif = self._invoke(duplication=outside)
        self.assertEqual(code, 2)
        self.assertNotIn(str(outside), stdout + summary)
        self.assertNotIn("secret-duplication", stdout + summary)

    # -- unchanged guarantees ---------------------------------------------

    def test_fork_pull_request_gating_is_unchanged_with_evidence(self):
        document = self._duplication_document()
        code, values, *_ = self._invoke(
            duplication=document, upload="true", event_name="pull_request",
            event={"pull_request": {"head": {"repo": {"fork": True}}}},
        )
        self.assertEqual(code, 0)
        self.assertEqual(values["upload-eligible"], "false")
        self.assertEqual(values["upload-reason"], "fork-pull-request")

    def test_same_repository_upload_eligibility_is_unchanged_with_evidence(self):
        document = self._duplication_document()
        _code, values, *_ = self._invoke(duplication=document, upload="true")
        self.assertEqual(values["upload-eligible"], "true")
        self.assertEqual(values["upload-reason"], "eligible")

    def test_pull_request_target_is_still_refused_with_evidence(self):
        # Built BEFORE the patch: this fixture shells out to git, and building
        # it inside the block would trip the "cli was invoked" guard on the
        # fixture's own subprocess rather than on the wrapper's.
        document = self._duplication_document()
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            code, _values, *_ = self._invoke(
                duplication=document, event_name="pull_request_target",
            )
        self.assertEqual(code, 2)

    def test_the_exit_code_contract_is_unchanged(self):
        """0 / 1 / 2 remain reachable with fresh explicit output paths."""
        document = self._duplication_document()
        passing, _v, *_ = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    999999, "repository.duplication_lexical_group_count"
                ),
                "dup-exit-pass.json",
            ),
            duplication=document,
            sarif=self.case / "dup-pass.sarif",
        )
        failing, _v, *_ = self._invoke(
            policy=self._write_policy(
                self._duplication_policy(
                    0, "repository.duplication_lexical_group_count"
                ),
                "dup-exit-fail.json",
            ),
            duplication=document,
            sarif=self.case / "dup-fail.sarif",
        )
        with patch.object(
            subprocess, "run", side_effect=AssertionError("cli was invoked")
        ):
            erroring, _v, *_ = self._invoke(duplication="../../escape.json")
        self.assertEqual((passing, failing, erroring), (0, 1, 2))

    def test_reused_sarif_destination_is_operational_refusal_and_preserves_bytes(self):
        document = self._duplication_document()
        policy = self._write_policy(self._duplication_policy(
            999999, "repository.duplication_lexical_group_count"
        ))
        passing, _values, _stdout, _summary, output = self._invoke(
            policy=policy, duplication=document,
        )
        self.assertEqual(passing, 0)
        original = output.read_bytes()
        refused, values, _stdout, _summary, _output = self._invoke(
            policy=policy, duplication=document, sarif=output, upload="true",
        )
        self.assertEqual(refused, 2)
        self.assertEqual(values["sarif-created"], "false")
        self.assertEqual(values["upload-eligible"], "false")
        self.assertEqual(output.read_bytes(), original)


class DuplicationWrapperBoundaryTests(unittest.TestCase):
    """The wrapper transports a path. It may not become an evidence reader.

    Every assertion here is about the action SOURCE, not about a run, because a
    boundary that only holds for the inputs the tests happen to exercise is not
    a boundary.
    """

    SOURCE = ACTION_RUNNER.read_text(encoding="utf-8")
    TREE = ast.parse(ACTION_RUNNER.read_text(encoding="utf-8"))

    def test_the_action_imports_no_archlens_module_at_all(self):
        """Not merely no duplication import: no ArchLens import of any kind.

        The wrapper invokes the installed console command as a SUBPROCESS. If it
        could import ArchLens it could call into the evaluator directly, and the
        boundary would be a convention rather than a structure.
        """
        for node in ast.walk(self.TREE):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                with self.subTest(imported=name):
                    self.assertFalse(name.startswith("modules"), name)
                    self.assertFalse(name.startswith("validation"), name)
                    self.assertFalse(name.startswith("archlens"), name)

    def test_the_action_never_parses_a_duplication_document(self):
        """`json.loads` appears, and only for the event payload and the SARIF.

        Both are the action's own business: one decides fork eligibility, the
        other reads bounded metadata out of the file ArchLens just wrote. What
        must not exist is a third reader for an evidence document.
        """
        readers = [
            node for node in ast.walk(self.TREE)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Attribute)
                and inner.attr in ("loads", "load")
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "json"
                for inner in ast.walk(node)
            )
        ]
        self.assertEqual(
            sorted(node.name for node in readers),
            ["_pull_request_is_from_fork", "_read_sarif"],
        )

    def test_the_action_computes_nothing_about_clones(self):
        for forbidden in (
            "clone", "fingerprint", "occurrence", "lexical", "structural",
            "group_id", "duplicated_nloc", "span", "distribution",
            "duplication_group", "validate_duplication_document",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.SOURCE.lower())

    def test_the_input_is_carried_by_name_and_nothing_more(self):
        """The words that ARE allowed: the variable, the flag, the label."""
        self.assertIn("METROLITH_DUPLICATION", self.SOURCE)
        self.assertIn("--duplication", self.SOURCE)
        self.assertIn(
            ("METROLITH_DUPLICATION", "--duplication", "duplication"),
            action.EVIDENCE_INPUTS,
        )

    def test_every_evidence_kind_shares_one_guarded_resolution_path(self):
        """A kind cannot acquire a weaker guard by getting its own branch."""
        source = self.SOURCE
        # Definition + evidence, baseline, and trusted-receipt calls. Every
        # candidate/workspace file uses the same containment boundary.
        self.assertEqual(source.count("_optional_workspace_path("), 4)
        for _variable, _flag, label in action.EVIDENCE_INPUTS:
            with self.subTest(input=label):
                self.assertNotIn(f'label="{label}"', source)

    def test_the_metadata_and_the_runner_agree_on_the_environment(self):
        """A declared input that reaches no environment variable does nothing."""
        steps = _metadata()["runs"]["steps"]
        check = next(step for step in steps if step.get("id") == "check")
        for variable, _flag, label in action.EVIDENCE_INPUTS:
            with self.subTest(input=label):
                self.assertEqual(
                    check["env"][variable], "${{ inputs.%s }}" % label
                )
                self.assertIn(label, _metadata()["inputs"])


class RunnerMechanicsTests(unittest.TestCase):
    def test_install_uses_the_checked_out_source_and_no_release_coordinate(self):
        with patch.object(
            action.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ) as invoked:
            self.assertEqual(action.install_source(), 0)
        command = invoked.call_args.args[0]
        self.assertEqual(command[:5], [
            action.sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        ])
        self.assertEqual(Path(command[-1]), REPOSITORY)
        self.assertNotIn("-e", command)
        self.assertFalse(any(str(item).startswith(("http://", "https://")) for item in command))
        self.assertIs(invoked.call_args.kwargs["shell"], False)

    def test_propagation_never_converts_violation_or_error_to_success(self):
        for raw, expected in (("0", 0), ("1", 1), ("2", 2), ("", 2), ("99", 2), ("x", 2)):
            with self.subTest(raw=raw), patch.dict(os.environ, {"METROLITH_EXIT_CODE": raw}):
                self.assertEqual(action.propagate(), expected)

    def test_preflight_refuses_pull_request_target(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request_target"}):
            with self.assertRaises(action.IntegrationError):
                action.preflight()

    def test_wrapper_refuses_an_unsupported_python_minor(self):
        with patch.object(action.sys, "version_info", (3, 14, 0)):
            with self.assertRaises(action.IntegrationError):
                action._require_supported_python()

    def test_unrecognized_boolean_cannot_enable_upload(self):
        with self.assertRaises(action.IntegrationError):
            action._parse_bool("TRUE", "upload-sarif")

    def test_post_upload_digest_check_detects_any_sarif_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            sarif = workspace / "result.sarif"
            sarif.write_bytes(b"original\n")
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "METROLITH_SARIF": "result.sarif",
                "METROLITH_EXPECTED_SHA256": hashlib.sha256(b"original\n").hexdigest(),
            }
            with patch.dict(os.environ, environment):
                self.assertEqual(action.verify_sarif(), 0)
                sarif.write_bytes(b"mutated\n")
                with self.assertRaises(action.IntegrationError):
                    action.verify_sarif()


if __name__ == "__main__":
    unittest.main()
