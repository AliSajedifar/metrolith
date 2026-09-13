import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pipeline
from modules.static_analysis import perform_static_analysis
from modules.metric_warnings import assess_metric_warnings


class JavaScriptTypeScriptAuditTests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_function_based_zero_is_explained(self):
        repo = self._repo()
        (repo / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^5.0.0"}}),
            encoding="utf-8",
        )
        (repo / "app.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "const start = () => app.listen(3000);\n",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 0)
        self.assertEqual(
            result["class_detection_status"],
            "not_applicable_function_based_js",
        )
        self.assertEqual(result["metric_confidence"], "high")

    def test_typescript_zero_with_class_framework_markers_is_suspicious(self):
        repo = self._repo()
        (repo / "package.json").write_text(
            json.dumps(
                {
                    "devDependencies": {"typescript": "^5.0.0"},
                    "dependencies": {"@nestjs/common": "^10.0.0"},
                }
            ),
            encoding="utf-8",
        )
        for index in range(6):
            (repo / f"module{index}.ts").write_text(
                f"export const value{index} = () => {index};\n",
                encoding="utf-8",
            )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 0)
        self.assertEqual(result["class_detection_status"], "suspicious_zero")
        self.assertEqual(result["js_ts_extension_counts"][".ts"], 6)
        self.assertEqual(result["metric_confidence"], "medium")

    def test_checked_no_classes_found_without_framework_evidence(self):
        repo = self._repo()
        (repo / "script.js").write_text(
            "function calculate(value) { return value * 2; }\n",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(
            result["class_detection_status"], "checked_no_classes_found"
        )
        self.assertTrue(result["js_ts_class_declaration_search"])

    def test_mongoose_schema_is_evidence_but_function_based_zero(self):
        repo = self._repo()
        (repo / "package.json").write_text(
            json.dumps(
                {
                    "dependencies": {
                        "express": "^5.0.0",
                        "mongoose": "^8.0.0",
                    }
                }
            ),
            encoding="utf-8",
        )
        (repo / "model.js").write_text(
            "const UserSchema = new Schema({ name: String });\n"
            "const serialize = value => value;\n"
            "module.exports = model('User', UserSchema);\n",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(
            result["class_detection_status"],
            "not_applicable_function_based_js",
        )
        self.assertEqual(
            result["class_detection_keyword_sample_files"], ["model.js"]
        )

    def test_parser_failure_is_distinct_from_true_zero(self):
        repo = self._repo()
        (repo / "broken.js").write_text(
            "function broken() {\n", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["js_ts_parse_failed_files"], 1)
        self.assertEqual(result["class_detection_status"], "syntax_partial")
        self.assertEqual(result["function_detection_status"], "syntax_partial")
        self.assertEqual(result["js_ts_error_categories"], ["syntax_partial"])
        self.assertEqual(result["metric_confidence"], "low")

    def test_partial_parse_failure_prevents_confirmed_zero(self):
        repo = self._repo()
        (repo / "good.js").write_text(
            "const run = () => 1;\n", encoding="utf-8"
        )
        (repo / "broken.js").write_text(
            "function broken() {\n", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["class_detection_status"], "syntax_partial")
        self.assertEqual(result["metric_confidence"], "medium")
        self.assertEqual(result["function_detection_status"], "functions_detected")

    def test_skip_counts_and_evidence_samples(self):
        repo = self._repo()
        (repo / "src").mkdir()
        (repo / "node_modules" / "pkg").mkdir(parents=True)
        (repo / "dist").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "app.ts").write_text(
            "@Controller()\nclass AppController {}\n", encoding="utf-8"
        )
        (repo / "node_modules" / "pkg" / "dep.js").write_text(
            "class Dependency {}\n", encoding="utf-8"
        )
        (repo / "dist" / "bundle.js").write_text(
            "class Bundle {}\n", encoding="utf-8"
        )
        (repo / "tests" / "app.test.ts").write_text(
            "class TestOnly {}\n", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["js_ts_scanned_files"], 1)
        self.assertEqual(result["js_ts_skipped_dependency_files"], 0)
        self.assertEqual(result["js_ts_pruned_dependency_directories"], 1)
        self.assertEqual(result["js_ts_skipped_generated_files"], 0)
        self.assertEqual(result["js_ts_pruned_generated_directories"], 1)
        self.assertEqual(result["js_ts_skipped_test_files"], 1)
        self.assertEqual(
            result["class_detection_keyword_sample_files"], ["src/app.ts"]
        )
        self.assertLessEqual(
            len(result["class_detection_skipped_sample_files"]), 5
        )

    def test_audit_scanner_matches_production_directory_exclusions(self):
        repo = self._repo()
        (repo / "src").mkdir()
        (repo / ".venv").mkdir()
        (repo / "target").mkdir()
        (repo / "src" / "app.js").write_text(
            "const run = () => 1;\n", encoding="utf-8"
        )
        (repo / ".venv" / "dependency.js").write_text(
            "class Dependency {}\n", encoding="utf-8"
        )
        (repo / "target" / "bundle.js").write_text(
            "class Generated {}\n", encoding="utf-8"
        )

        result = perform_static_analysis(repo)

        self.assertEqual(result["language_breakdown"]["JavaScript"]["source_files"], 1)
        self.assertEqual(result["js_ts_scanned_files"], 1)
        self.assertEqual(result["js_ts_skipped_dependency_files"], 0)
        self.assertEqual(result["js_ts_pruned_dependency_directories"], 1)
        self.assertEqual(result["js_ts_skipped_generated_files"], 0)
        self.assertEqual(result["js_ts_pruned_generated_directories"], 1)

    def test_js_parse_failures_always_produce_metric_warning(self):
        repo = self._repo()
        (repo / "valid.js").write_text(
            "class Service {}\nconst run = () => 1;\n", encoding="utf-8"
        )
        (repo / "broken.js").write_text(
            "function broken() {\n", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        warning = assess_metric_warnings(result, [], [])

        self.assertEqual(result["class_detection_status"], "classes_detected")
        self.assertTrue(warning["metric_warning"])
        self.assertIn(
            "JavaScript/TypeScript files failed lexical validation",
            warning["metric_warning_reason"],
        )

    def test_typescript_interface_policy_is_explicit(self):
        repo = self._repo()
        (repo / "types.ts").write_text(
            "interface User { id: string }\n"
            "type UserId = string;\n",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 0)
        self.assertEqual(result["metrics"]["by_language"]["typescript"]["interfaces"], 1)
        self.assertEqual(result["metrics"]["by_language"]["typescript"]["type_aliases"], 1)
        self.assertFalse(result["js_ts_typescript_interfaces_counted"])
        self.assertFalse(result["js_ts_typescript_type_aliases_counted"])

    def test_audit_command_report_is_readable(self):
        result = {
            "url": "https://github.com/example/project",
            "fetch_status": "success",
            "fetch_method": "existing_valid_cache",
            "analysis_status": "analyzed",
            "local_path": "C:/arch_raw/r_123",
            "endpoint_count": 2,
            "framework_markers": ["express"],
            "metric_warning": False,
            "metric_warning_reason": "",
            "static": {
                "js_ts_scanned_files": 3,
                "js_ts_extension_counts": {
                    ".js": 3,
                    ".jsx": 0,
                    ".ts": 0,
                    ".tsx": 0,
                },
                "js_ts_parse_failed_files": 0,
                "js_ts_skipped_files": 1,
                "js_ts_skipped_by_category": {
                    "dependency": 1,
                    "generated": 0,
                    "test": 0,
                    "other": 0,
                },
                "js_ts_class_declaration_search": True,
                "js_ts_typescript_interfaces_counted": True,
                "js_ts_typescript_type_aliases_counted": False,
                "js_ts_detected_classes_structs": 0,
                "js_ts_detected_methods_functions": 8,
                "class_detection_status": "not_applicable_function_based_js",
                "class_detection_reason": "function based",
                "function_detection_status": "functions_detected",
                "metric_confidence": "high",
                "class_detection_sample_files": ["app.js"],
            },
        }
        output = io.StringIO()
        with redirect_stdout(output):
            pipeline.print_audit_report(result)
        text = output.getvalue()
        self.assertIn("Class status: not_applicable_function_based_js", text)
        self.assertIn("Scanned JS/TS files: 3", text)

    def test_audit_repository_uses_validated_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo_dir = Path(directory) / "repo"
            repo_dir.mkdir()
            (repo_dir / "app.js").write_text(
                "const run = () => 1;\n", encoding="utf-8"
            )
            fetch_result = {
                "fetch_status": "success",
                "fetch_method": "existing_valid_cache",
                "fetch_error_type": "none",
                "fetch_error_message": "",
                "path": repo_dir,
            }
            with (
                patch.object(
                    pipeline,
                    "fetch_metadata",
                    return_value={"name": "project", "default_branch": "main"},
                ),
                patch.object(
                    pipeline, "clone_repository", return_value=fetch_result
                ),
            ):
                result = pipeline.audit_repository(
                    "https://github.com/example/project",
                    raw_data_dir=Path(directory) / "raw",
                )
        self.assertEqual(result["analysis_status"], "analyzed")
        self.assertEqual(
            result["static"]["class_detection_status"],
            "checked_no_classes_found",
        )


if __name__ == "__main__":
    unittest.main()
