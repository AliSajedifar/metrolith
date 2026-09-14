from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from modules.config import AnalysisConfig
from modules.core_metrics import _safe_preview, compute_repository_metrics
from modules.inventory import RepositoryInventory
from modules.repository_input import InputValidationError, load_repositories_csv


class FinalHardeningV32Tests(unittest.TestCase):
    def _write_repository(self, root: Path, files: dict[str, bytes | str]) -> None:
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def _metrics(
        self,
        files: dict[str, bytes | str],
        *,
        max_source_size: int | None = None,
    ) -> tuple[dict, RepositoryInventory]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self._write_repository(root, files)
        config = AnalysisConfig.from_env(
            max_source_file_size_bytes=max_source_size,
        )
        inventory = RepositoryInventory(root, config)
        return compute_repository_metrics(inventory), inventory

    def _git(self, root: Path, *arguments: str, input_text: str | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        ).stdout.strip()

    def _git_inventory(self) -> tuple[Path, RepositoryInventory]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self._git(root, "init", "-q")
        self._git(root, "config", "user.email", "archlens@example.invalid")
        self._git(root, "config", "user.name", "ArchLens Test")
        (root / "target.js").write_text("function target() {}\n", encoding="utf-8")
        (root / "link.js").write_text("target.js\n", encoding="utf-8")
        (root / "missing.js").write_text("does-not-exist.js\n", encoding="utf-8")
        (root / "normal.js").write_text("../looks/relative.js\n", encoding="utf-8")
        (root / "module.js").write_text("not source from a submodule\n", encoding="utf-8")
        self._git(root, "add", "target.js", "normal.js")
        for path in ("link.js", "missing.js"):
            blob = self._git(root, "hash-object", "-w", path)
            self._git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},{path}")
        empty_tree = self._git(root, "mktree", input_text="")
        commit = self._git(root, "commit-tree", empty_tree, "-m", "submodule fixture")
        self._git(
            root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{commit},module.js",
        )
        return root, RepositoryInventory(root, AnalysisConfig.from_env())

    def test_git_modes_classify_materialized_symlinks_and_submodules(self):
        _, inventory = self._git_inventory()
        internal = inventory.get("link.js")
        broken = inventory.get("missing.js")
        normal = inventory.get("normal.js")
        submodule = inventory.get("module.js")

        self.assertEqual(internal.git_mode, "120000")
        self.assertTrue(internal.is_git_symlink)
        self.assertEqual(internal.git_symlink_target, "target.js")
        self.assertTrue(internal.git_symlink_target_exists)
        self.assertEqual(internal.exclusion_reason, "git_symlink")
        self.assertFalse(internal.included_in_metrics)

        self.assertEqual(broken.git_mode, "120000")
        self.assertFalse(broken.git_symlink_target_exists)
        self.assertEqual(normal.git_mode, "100644")
        self.assertTrue(normal.included_in_metrics)
        self.assertEqual(submodule.git_mode, "160000")
        self.assertTrue(submodule.is_git_submodule)
        self.assertFalse(submodule.is_git_symlink)
        self.assertEqual(submodule.exclusion_reason, "git_submodule")

    def test_real_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.js").write_text("function target() {}\n", encoding="utf-8")
            try:
                os.symlink("target.js", root / "link.js")
            except OSError as exc:
                self.skipTest(f"host cannot create symlinks: {exc}")
            inventory = RepositoryInventory(root, AnalysisConfig.from_env())
            self.assertFalse(inventory.get("link.js").included_in_metrics)
            self.assertIn(
                inventory.get("link.js").exclusion_reason,
                {"symlink", "git_symlink"},
            )
            self.assertEqual(inventory.summary()["source_files_included"], 1)

    def test_lf_crlf_and_cr_only_java_are_equivalent(self):
        source = "class A {\n  // method\n  void m() {}\n}\n"
        observed = {}
        metadata = {}
        for name, newline in (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r")):
            metrics, inventory = self._metrics(
                {"A.java": source.replace("\n", newline).encode("utf-8")}
            )
            aggregate = metrics["aggregate"]
            observed[name] = (
                aggregate["lines_of_code"],
                aggregate["source_files"],
                aggregate["classes_structs"],
                aggregate["methods_functions"],
                aggregate["metric_status"],
            )
            record = inventory.get("A.java")
            metadata[name] = (
                record.line_ending_style,
                record.parser_normalization_applied,
                record.parser_compatibility_strategy,
            )
        self.assertEqual(observed["lf"], observed["crlf"])
        self.assertEqual(observed["lf"], observed["cr"])
        self.assertEqual(metadata["lf"], ("lf", False, None))
        self.assertEqual(metadata["crlf"], ("crlf", False, None))
        self.assertEqual(metadata["cr"], ("cr", True, "lone_cr_to_lf"))

    def test_utf8_bom_uses_explicit_parser_offset_adjustment(self):
        plain = b'const text = "Hrvatski \xd7\xa2\xd7\x91\xd7\xa8\xd7\x99\xd7\xaa \xe4\xb8\xad\xe6\x96\x87 caf\xc3\xa9";\nfunction f() {}\n'
        plain_metrics, _ = self._metrics({"plain.js": plain})
        bom_metrics, inventory = self._metrics({"bom.js": b"\xef\xbb\xbf" + plain})
        for key in ("lines_of_code", "source_files", "classes_structs", "methods_functions"):
            self.assertEqual(plain_metrics["aggregate"][key], bom_metrics["aggregate"][key])
        record = inventory.get("bom.js")
        self.assertEqual(record.byte_order_mark, "utf-8")
        self.assertTrue(record.parser_normalization_applied)
        self.assertEqual(record.parser_compatibility_strategy, "utf8_bom_removed")
        self.assertEqual(record.parser_byte_offset_adjustment, 3)

    def test_binary_preview_uses_actual_evidence(self):
        unicode_source = 'const value = "Hrvatski עברית 中文 café";'.encode("utf-8")
        self.assertNotEqual(_safe_preview(unicode_source, 0, len(unicode_source)), "<binary content omitted>")
        self.assertEqual(_safe_preview(b"const value = 1;\x00garbage", 0, 10), "<binary content omitted>")
        self.assertEqual(_safe_preview(b"const bad\x01name = 1;", 0, 20), "const bad\\x01name = 1;")

    def test_high_confidence_template_and_mixed_content_classification(self):
        _, inventory = self._metrics(
            {
                "jsp.js": '<%@ page language="java" %>\nconst x = 1;\n',
                "jsp-comment.js": '<%-- embedded --%>\n<script>function f(){}</script>\n',
                "templates/thymeleaf.js": "let size = [[${maxUploadSize}]];\n",
                "templates/thymeleaf-url.js": "let url = [[@{/items}]];\n",
                "templates/freemarker.js": "let label = ${I18n.getMessage('x')};\n",
                "wrapped.js": "<script>function wrapped() {}</script>\n",
            }
        )
        for path in (
            "jsp.js",
            "jsp-comment.js",
            "templates/thymeleaf.js",
            "templates/thymeleaf-url.js",
            "templates/freemarker.js",
            "wrapped.js",
        ):
            record = inventory.get(path)
            self.assertFalse(record.included_in_metrics, path)
            self.assertIn(record.exclusion_reason, {"templated_source", "content_type_mismatch"})
            self.assertTrue(record.template_evidence_category or record.content_type)
            self.assertTrue(record.template_evidence_summary)

    def test_ambiguous_template_markers_remain_source(self):
        _, inventory = self._metrics(
            {
                "literal.js": "const a = `${value}`; const b = '<script>'; const c = '<%';\n",
                "jsx.tsx": "export const View = () => <script>{'text'}</script>;\n",
                "generic.ts": "const less = a < b; const docs = '{{value}}';\n",
                "CmsJspLoader.java": (
                    'public class CmsJspLoader {\n'
                    '  static final String DIRECTIVE = "<%@ page language=\\"java\\" %>";\n'
                    '}\n'
                ),
                "DotPortletAction.java": (
                    'class DotPortletAction { // fix for <%@ page isErrorPage="true" %>\n'
                    '}\n'
                ),
            }
        )
        self.assertTrue(all(record.included_in_metrics for record in inventory))

    def test_oversized_source_has_explicit_accounting_counter(self):
        metrics, inventory = self._metrics(
            {"large.js": b"function f() {}\n" + b"x" * 200},
            max_source_size=32,
        )
        aggregate = metrics["aggregate"]
        self.assertEqual(aggregate["source_files"], 1)
        self.assertEqual(aggregate["source_files_oversized"], 1)
        self.assertEqual(inventory.summary()["source_files_oversized"], 1)
        self.assertEqual(
            aggregate["source_files"],
            aggregate["source_files_readable"]
            + aggregate["source_files_failed_read"]
            + aggregate["source_files_oversized"],
        )

    def test_no_javascript_rewrite_means_no_fallback_attempt(self):
        metrics, _ = self._metrics({"broken.js": "const value = ;\n"})
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertFalse(diagnostic["fallback_attempted"])
        self.assertEqual(diagnostic["fallback_strategies"], [])
        self.assertEqual(diagnostic["selected_parse"], "primary")
        self.assertIsNotNone(diagnostic["selected_error_count"])
        self.assertIsNotNone(diagnostic["selected_missing_count"])

    def test_url_only_csv_defaults_and_reordered_legacy_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            minimal = Path(directory) / "minimal.csv"
            minimal.write_text(
                "url\nhttps://github.com/owner/project\n",
                encoding="utf-8",
            )
            spec = load_repositories_csv(minimal)[0]
            self.assertEqual(spec.architecture_type, "unknown")
            self.assertIsNone(spec.expected_language)
            self.assertIsNone(spec.commit_sha)
            self.assertTrue(spec.enabled)

            legacy = Path(directory) / "legacy.csv"
            legacy.write_text(
                "notes,enabled,commit_sha,expected_language,architecture_type,url\n"
                "note,true,,Java,monolith,https://github.com/owner/legacy\n",
                encoding="utf-8",
            )
            self.assertEqual(load_repositories_csv(legacy)[0].notes, "note")

    def test_unknown_csv_header_and_invalid_boolean_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            unknown = Path(directory) / "unknown.csv"
            unknown.write_text(
                "url,expected_languge\nhttps://github.com/owner/project,Java\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(InputValidationError, "unknown column.*expected_languge"):
                load_repositories_csv(unknown)

            invalid = Path(directory) / "invalid.csv"
            invalid.write_text(
                "url,enabled\nhttps://github.com/owner/project,perhaps\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(InputValidationError, r"invalid.csv:2: enabled must be"):
                load_repositories_csv(invalid)

    def test_run_cli_requires_exactly_one_repo_or_input(self):
        parser = pipeline.build_cli()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "metrics"])
        repo_args = parser.parse_args(
            ["run", "metrics", "--repo", "https://github.com/owner/project"]
        )
        self.assertEqual(repo_args.repo, "https://github.com/owner/project")
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "run",
                    "metrics",
                    "--repo",
                    "https://github.com/owner/project",
                    "--input",
                    "repositories.csv",
                ]
            )

    def test_cli_exposes_new_noninteractive_workflows(self):
        parser = pipeline.build_cli()
        self.assertEqual(parser.parse_args(["doctor"]).command, "doctor")
        self.assertEqual(parser.parse_args(["validate", "run-dir"]).command, "validate")
        self.assertEqual(
            parser.parse_args(["compare", "run-a", "run-b"]).command,
            "compare",
        )
        example = parser.parse_args(["example", "run", "--language", "java"])
        self.assertEqual(example.command, "example")
        self.assertEqual(example.example_command, "run")

    def test_doctor_does_not_create_or_modify_workspace_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "doctor workspace"
            config = AnalysisConfig.from_env(
                workspace=workspace,
                cwd=root,
                environment={},
            )
            # The version patch this test used to carry is gone. It set
            # `importlib.metadata.version` globally, so it also made
            # `jsonschema` report the ArchLens version — harmless while doctor
            # only compared the archlens version, and wrong now that the schema
            # runtime is a real capability check. It is unnecessary too:
            # installation evidence is reported, never blocking.
            report = pipeline.doctor_report(config)
            self.assertTrue(report["healthy"], report["blocking_failures"])
            self.assertFalse(
                workspace.exists(),
                "doctor created a workspace path; an inspection command must "
                "not mutate what it inspects",
            )

    def test_workspace_path_precedence_and_unicode(self):
        resolver = getattr(__import__("modules.config", fromlist=["resolve_workspace_paths"]), "resolve_workspace_paths")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cwd = root / "current"
            home = root / "home"
            workspace = root / "space ünicode"
            explicit = root / "explicit output"
            paths = resolver(
                workspace=workspace,
                output_root=explicit,
                cwd=cwd,
                environment={"ARCHLENS_HOME": str(home)},
            )
            self.assertEqual(paths["workspace_root"], workspace.resolve())
            self.assertEqual(paths["output_root"], explicit.resolve())
            self.assertEqual(paths["cache_root"], (workspace / ".metrolith/cache/git").resolve())
            self.assertEqual(paths["temporary_directory"], (workspace / ".metrolith/worktrees").resolve())

            home_paths = resolver(cwd=cwd, environment={"ARCHLENS_HOME": str(home)})
            self.assertEqual(home_paths["workspace_root"], home.resolve())
            cwd_paths = resolver(cwd=cwd, environment={})
            self.assertEqual(cwd_paths["workspace_root"], cwd.resolve())

    def test_workspace_directories_are_created_or_fail_actionably(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            config = AnalysisConfig.from_env(workspace=workspace)
            config.prepare_paths()
            self.assertTrue(config.cache_root.is_dir())
            self.assertTrue(config.temporary_directory.is_dir())
            self.assertTrue(config.output_root.is_dir())

    def test_run_directory_names_include_planned_count_and_slug(self):
        run_artifacts = __import__("modules.run_artifacts", fromlist=["RunArtifacts"]).RunArtifacts
        with tempfile.TemporaryDirectory() as directory:
            batch = run_artifacts(
                directory,
                "2026-08-04T12:34:56Z",
                planned_repository_count=24,
            )
            self.assertRegex(batch.run_dir.name, r"^20260804T123456Z_n024_[0-9a-f]{12}$")
        with tempfile.TemporaryDirectory() as directory:
            single = run_artifacts(
                directory,
                "2026-08-04T12:34:56Z",
                planned_repository_count=1,
                repository_slug="owner/repository:bad",
            )
            self.assertRegex(
                single.run_dir.name,
                r"^20260804T123456Z_n001_owner-repository-bad_[0-9a-f]{12}$",
            )

    def test_examples_are_frozen_and_packaged(self):
        root = Path(pipeline.__file__).resolve().parent
        examples = root / "examples"
        self.assertTrue((examples / "README.md").is_file())
        rows = load_repositories_csv(examples / "quickstart_repositories.csv")
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(spec.commit_sha and len(spec.commit_sha) == 40 for spec in rows))
        self.assertEqual(
            {spec.expected_language for spec in rows},
            {"Java", "JavaScript", "Python", "Go"},
        )

    def test_version_contract_for_hardening_release(self):
        from modules.config import (
            ARTIFACT_SCHEMA_VERSION,
            INVENTORY_SCHEMA_VERSION,
            METRIC_CONTRACT_VERSION,
            PROGRAM_VERSION,
        )

        config = AnalysisConfig.from_env()
        self.assertEqual(PROGRAM_VERSION, "4.0.1")
        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")
        self.assertEqual(config.exclusion_policy_version, "1.5.0")
        self.assertEqual(INVENTORY_SCHEMA_VERSION, "1.7.0")
        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "1.12.0")


if __name__ == "__main__":
    unittest.main()
