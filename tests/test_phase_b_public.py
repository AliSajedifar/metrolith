"""Public-safe documentation/resource guards; runnable directly from the sdist."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.resources import files
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import tempfile
import unittest
from unittest.mock import patch

import pipeline
from modules.cli import baseline_command, policy_command
from modules.policy.document import PolicyDocumentInvalid, load_policy_file
from modules.policy.document_v2 import load_any_policy
from modules.presentation import wrap_prose
from validation.artifact_io.schema_store import SCHEMA_REGISTRY, load_schema

ROOT = Path(__file__).resolve().parents[1]


class PublicDistributionTests(unittest.TestCase):
    def test_readme_links_resolve_without_private_archive_targets(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("../archive", text)
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            relative = Path(target.split("#", 1)[0])
            self.assertFalse(relative.is_absolute(), target)
            resolved = (ROOT / relative).resolve()
            self.assertTrue(resolved.is_relative_to(ROOT.resolve()), target)
            self.assertTrue(resolved.is_file(), target)
            current = ROOT
            for part in relative.parts:
                self.assertIn(part, [entry.name for entry in current.iterdir()], target)
                current /= part
        for path in ("CHANGELOG.md", "LICENSE", "docs/PUBLIC_RELEASE_CHECKLIST.md",
                     "docs/REPRODUCIBILITY.md", "examples/ratchet-rules.json"):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_all_schema_registrations_resolve_and_no_resource_is_unregistered(self):
        resource_root = files("validation.resources").joinpath("schemas")
        actual = {p.name for p in resource_root.iterdir() if p.name.endswith(".json")}
        expected = {filename for filename, _version in SCHEMA_REGISTRY.values()}
        self.assertEqual(actual, expected)
        for name in SCHEMA_REGISTRY:
            self.assertIsInstance(load_schema(name), dict, name)

    def test_complete_packaged_ratchet_example_and_actionable_wrong_family(self):
        path = Path(str(files("examples").joinpath("ratchet-rules.json")))
        rules = baseline_command._load_rules(path)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].metric_contract, "metrics")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw[0]["metric_contract"] = "3.0.0"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "rules.json"
            invalid.write_text(json.dumps(raw), encoding="utf-8")
            args = pipeline.build_cli().parse_args([
                "baseline", "capture", str(root), "--rules", str(invalid),
                "--output", str(root.parent / (root.name + ".baseline.json")),
            ])
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(baseline_command.handle(args), 1)
            for token in ("metric_contract", "3.0.0", "metrics", "correct the rule array"):
                self.assertIn(token, output.getvalue())

    def test_current_init_validates_and_is_refused_by_the_legacy_consumer(self):
        parser = pipeline.build_cli()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "policy.json"
            args = parser.parse_args([
                "policy", "init", "--output", str(path), "--metric",
                "repository.source_files", "--operator", "gt", "--threshold", "10",
                "--severity", "violation",
            ])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(policy_command.handle(args), 0)
                self.assertEqual(policy_command._validate_policy(path), 0)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(load_any_policy(raw))
            with self.assertRaises(PolicyDocumentInvalid):
                load_policy_file(path)
            args = parser.parse_args(["policy", "evaluate", str(root), "--policy", str(path)])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(policy_command.handle(args), 4)

    def test_dirty_evaluator_refusal_reports_the_recorded_failed_prerequisite(self):
        from modules.ratchet.capture import BaselineCaptureError, _verify_terminal_run
        from validation.artifact_io.compatibility import CompatibilityState, RunLifecycle
        environment = {"profiler_git_dirty": True, "profiler_git_commit_sha": "a" * 40}
        common = {"run_id": "fixture", "measurement_outcome": "complete",
                  "failure_count": 0, "partial_count": 0}
        view = SimpleNamespace(
            integrity_status="completed", compatibility=SimpleNamespace(state=CompatibilityState.SUPPORTED),
            lifecycle=RunLifecycle.FINALIZED_VALID, structural_errors=[], run_id="fixture",
            status=common, environment=environment,
            manifest={**common, **environment, "run_integrity_status": "completed",
                      "self_validation": {"passed": True}, "benchmark_environment": environment},
        )
        with self.assertRaises(BaselineCaptureError) as raised:
            _verify_terminal_run(view)
        self.assertEqual(raised.exception.code, "source_evaluator_identity_not_reproducible")
        self.assertIn("profiler_git_dirty=True", raised.exception.detail)
        self.assertIn("do not edit retained provenance", raised.exception.detail)

    def test_first_use_precedes_catalog_and_prose_wraps_without_splitting_tokens(self):
        for width in (80, 120):
            with patch.dict(os.environ, {"COLUMNS": str(width)}):
                help_text = pipeline.build_cli().format_help()
                self.assertLess(help_text.index("metrolith analyze ."), help_text.index("Task-oriented commands"))
                self.assertLess(help_text.index("metrolith analyze ."), help_text.index("positional arguments"))
                prose = "Partial data remains unavailable until the required evidence is supplied. " * 4
                wrapped = wrap_prose(prose)
                self.assertTrue(all(len(line) <= width for line in wrapped.splitlines()))
                token = "repository." + "x" * 140
                self.assertIn(token, wrap_prose("Metric " + token))


if __name__ == "__main__":
    unittest.main()
