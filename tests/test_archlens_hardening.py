import hashlib
import io
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import pipeline
from modules.benchmark_runner import _error
from modules.config import (
    ARTIFACT_SCHEMA_VERSION,
    INVENTORY_SCHEMA_VERSION,
    METRIC_CONTRACT_VERSION,
    PROGRAM_VERSION,
    AnalysisConfig,
)
from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory
from modules.static_analysis import perform_static_analysis


class ContentClassificationHardeningTests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _record(self, filename, content, *, full_inventory=True, raw=False):
        repo = self._repo()
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return RepositoryInventory(repo, full_inventory=full_inventory).get(filename)

    def test_qt_xml_catalog_named_ts_is_a_content_type_mismatch(self):
        record = self._record(
            "i18n/fa.ts",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE TS>\n<TS version="2.1" language="fa_IR">\n</TS>\n',
        )
        self.assertFalse(record.included_in_metrics)
        self.assertFalse(record.is_source)
        self.assertIsNone(record.detected_language)
        self.assertEqual(record.exclusion_reason, "content_type_mismatch")
        self.assertEqual(record.content_type, "qt_translation_catalog")

    def test_qt_xml_catalog_detection_ignores_bom_and_whitespace(self):
        record = self._record(
            "catalog.ts",
            b"\xef\xbb\xbf  \r\n\t<?xml version=\"1.0\"?>\n<!DOCTYPE TS>\n<TS version=\"2.1\">",
            raw=True,
        )
        self.assertEqual(record.exclusion_reason, "content_type_mismatch")
        self.assertEqual(record.content_type, "qt_translation_catalog")

    def test_malformed_qt_catalog_header_is_still_high_confidence(self):
        record = self._record(
            "broken.ts",
            '<?xml version="1.0"?>\n<!DOCTYPE TS>\n<TS version="2.1"\n<context>',
        )
        self.assertEqual(record.exclusion_reason, "content_type_mismatch")

    def test_legitimate_typescript_and_generic_syntax_remain_source(self):
        sources = {
            "ordinary.ts": "export const answer: number = 42;\n",
            "generic.ts": "const identity = <T>(value: T): T => value;\n",
            "tsx_like_failure.ts": "const view = <Widget prop={value} />;\n",
        }
        for filename, source in sources.items():
            with self.subTest(filename=filename):
                record = self._record(filename, source)
                self.assertTrue(record.included_in_metrics)
                self.assertTrue(record.is_source)
                self.assertEqual(record.detected_language, "TypeScript")
                self.assertIsNone(record.content_type)

    def test_content_type_decision_matches_lightweight_and_full_inventory(self):
        for full_inventory in (False, True):
            with self.subTest(full_inventory=full_inventory):
                record = self._record(
                    "locale.ts",
                    '<?xml version="1.0"?><!DOCTYPE TS><TS version="2.1">',
                    full_inventory=full_inventory,
                )
                self.assertEqual(record.exclusion_reason, "content_type_mismatch")
                self.assertEqual(record.content_type, "qt_translation_catalog")

    def test_high_confidence_template_families_are_excluded_explicitly(self):
        fixtures = {
            "views/page.js": ("<#if user>\nconst name = '${user}';\n</#if>\n", "Freemarker"),
            "views/module.js": ("<%!\ndef helper(): return 1\n%>\nconst x = 1;\n", "Mako"),
            "deploy/service.go": ("{{- if .Model }}\npackage main\n{{- end }}\n", "Go template"),
            "config/Defaults.java": (
                'class Defaults { String pool = "@jdbc.connPoolMax@"; '
                'String query = "@sql.getName@"; }\n',
                "build placeholder",
            ),
        }
        for filename, (source, family) in fixtures.items():
            for full_inventory in (False, True):
                with self.subTest(filename=filename, full_inventory=full_inventory):
                    record = self._record(filename, source, full_inventory=full_inventory)
                    self.assertFalse(record.included_in_metrics)
                    self.assertEqual(record.exclusion_reason, "templated_source")
                    self.assertEqual(record.template_family, family)
                    self.assertFalse(record.is_generated)

    def test_ambiguous_template_markers_remain_source(self):
        fixtures = {
            "literal.js": "const message = `hello ${value}`;\n",
            "string.js": 'const example = "${value} and {{value}} and <#if>";\n',
            "braces.go": "package p\nfunc Value() map[string]int { return map[string]int{} }\n",
            "Annotation.java": (
                '@Deprecated class Annotation { String email = "dev@example.com"; '
                'String marker = "@NAME@"; }\n'
            ),
            "generic.ts": "function identity<T>(value: T): T { return value; }\n",
            "example.ts": 'const docs = "<% example %> {{ .Model }}";\n',
        }
        for filename, source in fixtures.items():
            with self.subTest(filename=filename):
                record = self._record(filename, source)
                self.assertTrue(record.included_in_metrics)
                self.assertIsNone(record.template_family)


class ParserDiagnosticHardeningTests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _metrics(self, files, *, max_size=None):
        repo = self._repo()
        for filename, content in files.items():
            path = repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        config = (
            AnalysisConfig.from_env(max_source_file_size_bytes=max_size)
            if max_size is not None
            else AnalysisConfig.from_env()
        )
        inventory = RepositoryInventory(repo, config)
        return compute_repository_metrics(inventory), inventory

    def test_parser_diagnostic_has_normalized_exact_location_and_grammar(self):
        metrics, inventory = self._metrics({"broken.js": "function broken( {\n"})
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(diagnostic["file_path"], "broken.js")
        self.assertEqual(diagnostic["file_sha256"], inventory.get("broken.js").content_hash)
        self.assertEqual(diagnostic["detected_language"], "JavaScript")
        self.assertEqual(diagnostic["extension"], ".js")
        self.assertEqual(diagnostic["parser_implementation"], "tree-sitter")
        self.assertEqual(diagnostic["selected_grammar"], "javascript")
        self.assertEqual(diagnostic["grammar_package"], "tree-sitter-javascript")
        self.assertEqual(diagnostic["grammar_version"], "0.25.0")
        self.assertTrue(diagnostic["root_has_error"])
        self.assertEqual(diagnostic["total_error_nodes"], 1)
        self.assertEqual(diagnostic["total_missing_nodes"], 0)
        self.assertEqual(diagnostic["first_malformed_node_type"], "ERROR")
        self.assertEqual(diagnostic["first_error_start_line"], 1)
        self.assertEqual(diagnostic["first_error_end_line"], 1)
        self.assertEqual(diagnostic["first_error_start_column"], 0)
        self.assertEqual(diagnostic["first_error_end_column"], 18)
        self.assertEqual(diagnostic["first_error_start_byte"], 0)
        self.assertEqual(diagnostic["first_error_end_byte"], 18)
        self.assertEqual(diagnostic["error_category"], "syntax_partial")
        self.assertEqual(diagnostic["affected_metrics"], ["classes_structs", "methods_functions"])
        self.assertEqual(diagnostic["stage"], "parse")
        self.assertFalse(diagnostic["fallback_attempted"])
        self.assertIsNone(diagnostic["fallback_grammar"])
        self.assertIsNone(diagnostic["fallback_error_count"])
        self.assertEqual(diagnostic["fallback_strategies"], [])
        self.assertEqual(diagnostic["selected_parse"], "primary")
        self.assertEqual(diagnostic["final_file_status"], "partial_parse")
        self.assertLessEqual(len(diagnostic["preview"]), 240)
        self.assertLessEqual(len(diagnostic["malformed_nodes"]), 25)

    def test_control_characters_are_escaped_in_textual_preview(self):
        metrics, _ = self._metrics({"control.js": b"const bad\x01name = 1;\n"})
        preview = metrics["parser_diagnostics"][0]["preview"]
        self.assertNotIn("\x01", preview)
        self.assertIn("\\x01", preview)

    def test_nul_content_is_not_exposed_as_text(self):
        metrics, _ = self._metrics({"nul.js": b"function ok() {}\n\x00"})
        self.assertEqual(
            metrics["parser_diagnostics"][0]["preview"],
            "<binary content omitted>",
        )

    def test_refined_taxonomy_distinguishes_python2_and_malformed_python(self):
        metrics, _ = self._metrics(
            {
                "legacy.py": "print value\n",
                "broken.py": "value = [1 2]\n",
            }
        )
        diagnostics = {item["file_path"]: item for item in metrics["parser_diagnostics"]}
        self.assertEqual(
            diagnostics["legacy.py"]["error_category"],
            "unsupported_language_version",
        )
        self.assertEqual(diagnostics["legacy.py"]["language_version_hint"], "likely_python2_syntax")
        self.assertEqual(diagnostics["broken.py"]["error_category"], "syntax_failed")
        python = metrics["by_language"]["python"]
        self.assertEqual(python["source_files"], 2)
        self.assertEqual(python["loc_status"], "complete")
        self.assertEqual(python["classes_structs_status"], "failed")

    def test_source_oversized_has_distinct_failure_category(self):
        metrics, _ = self._metrics({"large.py": "value = 1\n"}, max_size=2)
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(diagnostic["error_category"], "source_oversized")
        self.assertEqual(diagnostic["stage"], "read")
        self.assertEqual(diagnostic["final_file_status"], "failed")

    def test_non_parser_error_rows_use_stable_acquisition_categories(self):
        result = {
            "repository_url": "https://github.com/acme/dirty",
            "acquisition": {},
            "errors": [],
        }
        _error(
            result,
            Mock(),
            "acquisition",
            "checkout_not_clean",
            "dirty checkout",
        )
        self.assertEqual(
            result["errors"][0]["error_category"],
            "checkout_not_clean",
        )
        _error(
            result,
            Mock(),
            "acquisition",
            "repository_not_found",
            "not found",
        )
        self.assertEqual(
            result["errors"][1]["error_category"],
            "acquisition_failure",
        )

    def test_import_assertion_compatibility_is_precise_and_in_memory(self):
        fixtures = {
            "assertion.js": 'import pkg from "../package.json" assert { type: "json" };\n',
            "plain.js": 'import pkg from "../package.json";\n',
            "attribute.js": 'import pkg from "../package.json" with { type: "json" };\n',
            "malformed.js": 'import pkg from "../package.json" assert { type: };\n',
            "dynamic.js": (
                'const pkg = await import("../package.json", '
                '{ with: { type: "json" } });\n'
            ),
            "object.js": "const value = { assert: true };\n",
        }
        metrics, inventory = self._metrics(fixtures)
        statuses = {record.relative_path: record.parse_status for record in inventory}
        self.assertEqual(statuses["assertion.js"], "complete")
        self.assertEqual(statuses["malformed.js"], "partial")
        for filename in ("plain.js", "attribute.js", "dynamic.js", "object.js"):
            self.assertEqual(statuses[filename], "complete")
        diagnostic = next(
            item
            for item in metrics["recovered_parser_diagnostics"]
            if item["file_path"] == "assertion.js"
        )
        self.assertEqual(diagnostic["total_error_nodes"], 1)
        self.assertEqual(diagnostic["first_error_start_byte"], 34)
        self.assertEqual(diagnostic["first_error_end_byte"], 57)
        self.assertEqual(diagnostic["selected_grammar"], "javascript")
        self.assertTrue(diagnostic["fallback_attempted"])
        self.assertEqual(diagnostic["fallback_error_count"], 0)
        self.assertEqual(diagnostic["final_file_status"], "complete")
        self.assertEqual(
            diagnostic["fallback_strategies"],
            ["javascript_import_assertion_compat"],
        )
        self.assertNotIn(
            "assertion.js", [item["file_path"] for item in metrics["parser_diagnostics"]]
        )
        self.assertEqual(
            (inventory.root / "assertion.js").read_text(encoding="utf-8"),
            fixtures["assertion.js"],
        )

    def test_focused_javascript_compatibility_fixtures(self):
        fixtures = {
            "import_assertion.js": (
                'import pkg from "./package.json" assert { type: "json" };\n\n'
                "export class Service {\n"
                "  run() {\n    return pkg.name;\n  }\n"
                "}\n\n"
                "export function createService() {\n  return new Service();\n}\n"
            ),
            "multiple_assertions.js": (
                'import en from "./en.json" assert { type: "json" };\n'
                'import es from "./es.json" assert { type: "json" };\n\n'
                "export const getMessages = () => ({ en, es });\n"
            ),
            "reserved.jsx": (
                "export function Form() {\n  return (\n"
                '    <div class="container">\n'
                '      <label for="name">Name</label>\n'
                "      <Widget delete={() => true} />\n"
                "    </div>\n  );\n}\n"
            ),
            "ampersands.jsx": (
                "export const Terms = () => (\n  <section>\n"
                "    <p>Terms & Conditions</p>\n"
                '    <a href="https://example.test?a=1&b=2">Open & Review</a>\n'
                "  </section>\n);\n"
            ),
        }
        metrics, inventory = self._metrics(fixtures)
        recoveries = {
            item["file_path"]: item
            for item in metrics["recovered_parser_diagnostics"]
        }
        self.assertEqual(set(recoveries), set(fixtures))
        self.assertEqual(metrics["parser_diagnostics"], [])
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
        self.assertEqual(metrics["aggregate"]["classes_structs"], 1)
        self.assertEqual(metrics["aggregate"]["methods_functions"], 5)
        self.assertEqual(metrics["aggregate"]["source_files_recovered_parse"], 4)
        self.assertEqual(
            recoveries["import_assertion.js"]["fallback_strategies"],
            ["javascript_import_assertion_compat"],
        )
        self.assertEqual(
            recoveries["multiple_assertions.js"]["fallback_strategies"],
            ["javascript_import_assertion_compat"],
        )
        self.assertEqual(
            recoveries["reserved.jsx"]["fallback_strategies"],
            ["jsx_reserved_attribute_compat"],
        )
        self.assertEqual(
            recoveries["ampersands.jsx"]["fallback_strategies"],
            ["raw_jsx_ampersand_compat"],
        )
        for filename, original in fixtures.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    (inventory.root / filename).read_text(encoding="utf-8"), original
                )
                self.assertEqual(recoveries[filename]["fallback_error_count"], 0)
                self.assertEqual(recoveries[filename]["final_file_status"], "complete")

    def test_normal_javascript_does_not_attempt_fallback_or_change_source(self):
        source = "export function unchanged() { return 1; }\n"
        metrics, inventory = self._metrics({"normal.js": source})
        record = inventory.get("normal.js")
        self.assertEqual(record.parse_status, "complete")
        self.assertIsNone(record.parser_diagnostic)
        self.assertEqual(metrics["aggregate"]["methods_functions"], 1)
        self.assertEqual(metrics["recovered_parser_diagnostics"], [])
        self.assertEqual((inventory.root / "normal.js").read_text(encoding="utf-8"), source)

    def test_genuinely_malformed_javascript_remains_an_unresolved_lower_bound(self):
        metrics, inventory = self._metrics(
            {"broken.js": "export function broken( {\n  return 1;\n"}
        )
        record = inventory.get("broken.js")
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(record.parse_status, "partial")
        self.assertEqual(metrics["aggregate"]["metric_status"], "partial")
        self.assertEqual(metrics["aggregate"]["classes_structs_status"], "partial")
        self.assertEqual(metrics["aggregate"]["classes_structs"], 0)
        self.assertFalse(diagnostic["fallback_attempted"])
        self.assertEqual(diagnostic["final_file_status"], "partial_parse")
        self.assertEqual(diagnostic["error_category"], "syntax_partial")

    def test_missing_node_also_attempts_fallback_and_remains_partial(self):
        metrics, _ = self._metrics({"missing.js": "export function incomplete() {\n"})
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertGreater(diagnostic["total_missing_nodes"], 0)
        self.assertFalse(diagnostic["fallback_attempted"])
        self.assertIsNone(diagnostic["fallback_missing_count"])
        self.assertEqual(diagnostic["final_file_status"], "partial_parse")

    def test_reserved_word_rewrite_never_touches_ordinary_javascript(self):
        source = (
            'const text = "A & B"; const bits = left & right;\n'
            "const value = { delete: true, class: 1 };\nexport function broken( {\n"
        )
        metrics, inventory = self._metrics({"ordinary.js": source})
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(diagnostic["fallback_strategies"], [])
        self.assertEqual((inventory.root / "ordinary.js").read_text(encoding="utf-8"), source)

    def test_multiple_compatibility_strategies_are_recorded_in_order(self):
        source = (
            'import pkg from "./package.json" assert { type: "json" };\n'
            'export function View(){ return <div class="x">Terms & Conditions</div>; }\n'
        )
        metrics, _ = self._metrics({"combined.jsx": source})
        diagnostic = metrics["recovered_parser_diagnostics"][0]
        self.assertEqual(
            diagnostic["fallback_strategies"],
            [
                "javascript_import_assertion_compat",
                "jsx_reserved_attribute_compat",
                "raw_jsx_ampersand_compat",
            ],
        )

    def test_failed_fallback_selects_lower_malformed_score_but_remains_partial(self):
        source = (
            'import pkg from "./package.json" assert { type: "json" };\n'
            "export class Service {}\n"
            "export function broken( {\n"
        )
        metrics, _ = self._metrics({"lower-bound.js": source})
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(diagnostic["selected_parse"], "fallback")
        self.assertEqual(
            diagnostic["fallback_strategies"],
            ["javascript_import_assertion_compat"],
        )
        self.assertLess(diagnostic["selected_error_count"], diagnostic["total_error_nodes"])
        self.assertEqual(diagnostic["final_file_status"], "partial_parse")
        self.assertEqual(metrics["aggregate"]["classes_structs_status"], "partial")

    def test_fallback_leaves_git_checkout_clean_and_source_hashes_identical(self):
        repo = self._repo()
        sources = {
            "assertion.js": 'import pkg from "./package.json" assert { type: "json" };\n',
            "reserved.jsx": (
                'export function View(){ return <div class="x">A & B</div>; }\n'
            ),
        }
        for filename, source in sources.items():
            (repo / filename).write_text(source, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "ArchLens Test"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "archlens-test@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "add", "--", *sources], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
        before = {
            filename: hashlib.sha256((repo / filename).read_bytes()).hexdigest()
            for filename in sources
        }
        inventory = RepositoryInventory(repo, AnalysisConfig.from_env())
        metrics = compute_repository_metrics(inventory)
        after = {
            filename: hashlib.sha256((repo / filename).read_bytes()).hexdigest()
            for filename in sources
        }
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual(metrics["aggregate"]["source_files_recovered_parse"], 2)
        self.assertEqual(before, after)
        self.assertEqual(status, "")

    def test_parser_diagnostics_are_deterministically_ordered_by_path(self):
        metrics, _ = self._metrics(
            {"z.js": "function z( {\n", "A.js": "function a( {\n"}
        )
        self.assertEqual(
            [item["file_path"] for item in metrics["parser_diagnostics"]],
            ["A.js", "z.js"],
        )

    def test_compatibility_audit_exposes_refined_failure_category(self):
        repo = self._repo()
        (repo / "broken.js").write_text("function broken( {\n", encoding="utf-8")
        result = perform_static_analysis(repo)
        self.assertEqual(result["class_detection_status"], "syntax_partial")
        self.assertEqual(result["function_detection_status"], "syntax_partial")
        self.assertEqual(result["js_ts_error_categories"], ["syntax_partial"])


class MetrolithIdentityTests(unittest.TestCase):
    def test_versions_match_the_hardening_scope(self):
        self.assertEqual(PROGRAM_VERSION, "4.0.1")
        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")
        self.assertEqual(AnalysisConfig().exclusion_policy_version, "1.5.0")
        self.assertEqual(INVENTORY_SCHEMA_VERSION, "1.7.0")
        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "1.12.0")
        snapshot = AnalysisConfig().snapshot()
        self.assertEqual(
            snapshot["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION
        )

    def test_package_metadata_uses_canonical_name_version_and_commands(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "metrolith")
        self.assertEqual(metadata["project"]["version"], PROGRAM_VERSION)
        self.assertEqual(metadata["project"]["scripts"]["metrolith"], "pipeline:main")
        self.assertEqual(
            metadata["project"]["scripts"]["archlens"],
            "pipeline:archlens_compat_main",
        )
        self.assertEqual(
            metadata["project"]["scripts"]["arch-bench"],
            "pipeline:deprecated_main",
        )

    def test_wheel_packages_the_versioned_exclusion_policy(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(
            "config",
            metadata["tool"]["setuptools"]["packages"],
        )
        self.assertEqual(
            metadata["tool"]["setuptools"]["package-data"]["config"],
            ["*.json"],
        )
        self.assertTrue((root / "config" / "__init__.py").is_file())

    def test_workspace_distribution_version_matches_program(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], PROGRAM_VERSION)

    def test_cli_and_interactive_banner_use_metrolith(self):
        parser = pipeline.build_cli()
        self.assertEqual(parser.prog, "metrolith")
        self.assertIn("Metrolith", parser.description)
        self.assertNotIn("ARCH-Bench", parser.format_help())
        answers = iter(["5"])
        output = []
        with patch.object(pipeline, "inspect_input_files", return_value=[]):
            pipeline.run_interactive_menu(
                input_fn=lambda prompt: next(answers),
                output_fn=output.append,
            )
        self.assertTrue(any("Metrolith Interactive Menu" in line for line in output))

    def test_deprecated_console_aliases_warn_and_invoke_canonical_main(self):
        stderr = io.StringIO()
        with patch.object(pipeline, "main", return_value=17) as canonical, redirect_stderr(stderr):
            archlens_result = pipeline.archlens_compat_main()
            arch_bench_result = pipeline.deprecated_main()
        self.assertEqual((archlens_result, arch_bench_result), (17, 17))
        self.assertEqual(canonical.call_count, 2)
        warnings = stderr.getvalue().splitlines()
        self.assertEqual(len(warnings), 2)
        self.assertIn("`archlens` is a deprecated compatibility alias", warnings[0])
        self.assertIn("'arch-bench' is deprecated", warnings[1])
        self.assertTrue(all("metrolith" in warning.casefold() for warning in warnings))


if __name__ == "__main__":
    unittest.main()
