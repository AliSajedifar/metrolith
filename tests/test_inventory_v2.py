import tempfile
import unittest
import errno
from pathlib import Path
from unittest.mock import patch

from modules.config import AnalysisConfig
from modules.inventory import RepositoryInventory
from modules.message_brokers import detect_message_brokers


class CanonicalInventoryTests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_inventory_is_deterministic_and_walks_once(self):
        repo = self._repo()
        (repo / "z.py").write_text("pass\n", encoding="utf-8")
        (repo / "A.java").write_text("class A {}\n", encoding="utf-8")
        inventory = RepositoryInventory(repo)
        self.assertEqual([item.relative_path for item in inventory], ["A.java", "z.py"])
        self.assertEqual(inventory.walk_count, 1)
        self.assertEqual(inventory.summary()["filesystem_walk_count"], 1)

    def test_source_configuration_and_infrastructure_are_distinct(self):
        repo = self._repo()
        (repo / "app.py").write_text("pass\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        inventory = RepositoryInventory(repo)
        records = {item.relative_path: item for item in inventory}
        self.assertTrue(records["app.py"].included_in_metrics)
        self.assertTrue(records["pyproject.toml"].is_configuration)
        self.assertTrue(records["Dockerfile"].is_infrastructure)
        self.assertFalse(records["Dockerfile"].included_in_metrics)

    def test_standard_exclusion_reasons_are_recorded(self):
        repo = self._repo()
        files = {
            "tests/test_app.py": "pass\n",
            "vendor/v.go": "package v\n",
            "node_modules/a.js": "const a=1;\n",
            "dist/a.js": "const a=1;\n",
            "generated/a.java": "class A{}\n",
            "src/a.min.js": "const a=1;\n",
            "src/types.d.ts": "interface I {}\n",
            "src/stub.pyi": "def f(): ...\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        inventory = RepositoryInventory(repo)
        reasons = {item.relative_path: item.exclusion_reason for item in inventory}
        self.assertEqual(reasons["tests/test_app.py"], "test")
        self.assertNotIn("vendor/v.go", reasons)
        self.assertNotIn("node_modules/a.js", reasons)
        self.assertNotIn("dist/a.js", reasons)
        self.assertNotIn("generated/a.java", reasons)
        pruned = {item["path"]: item["exclusion_reason"] for item in inventory.pruned_directories}
        self.assertEqual(pruned["vendor"], "vendor")
        self.assertEqual(pruned["node_modules"], "dependency")
        self.assertEqual(pruned["dist"], "build_output")
        self.assertEqual(pruned["generated"], "generated")
        self.assertEqual(reasons["src/a.min.js"], "minified_or_bundled")
        self.assertEqual(reasons["src/types.d.ts"], "declaration_only")
        self.assertEqual(reasons["src/stub.pyi"], "declaration_only")

    def test_repository_specific_test_directory_prefixes_and_module_names_are_excluded(self):
        repo = self._repo()
        files = {
            "src/package/test.py": "def helper():\n    pass\n",
            "src/package/tests.py": "def helper():\n    pass\n",
            "tests_frontend/playwright.config.ts": "export const config = {};\n",
            "tests_integration/run.py": "def run():\n    pass\n",
            "src/testing/service.py": "def production():\n    pass\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        records = {item.relative_path: item for item in RepositoryInventory(repo)}
        for relative in files:
            if relative == "src/testing/service.py":
                self.assertTrue(records[relative].included_in_metrics)
            else:
                self.assertEqual(records[relative].exclusion_reason, "test")

    def test_test_runner_conventions_and_go_test_helpers_are_excluded(self):
        repo = self._repo()
        files = {
            "ui/src/widget.unit.js": "describe('widget', () => {});\n",
            "client/playwright.config.ts": "export default {};\n",
            "backend/internal/storagetest/storagetest.go": "package storagetest\n",
            "backend/app/conformance.go": (
                'package app\nimport "testing"\nfunc RunConformance(t *testing.T) {}\n'
            ),
            "backend/app/integration.go": "package app\nfunc Integrate() {}\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        records = {item.relative_path: item for item in RepositoryInventory(repo)}
        for relative in files:
            if relative in {"backend/app/integration.go", "backend/app/conformance.go"}:
                self.assertTrue(records[relative].included_in_metrics)
            else:
                self.assertEqual(records[relative].exclusion_reason, "test")

    def test_go_testing_import_alone_does_not_make_production_a_test(self):
        repo = self._repo()
        files = {
            "single.go": 'package p\nimport "testing"\nfunc Support(t *testing.T) {}\n',
            "grouped.go": 'package p\nimport (\n "fmt"\n "testing"\n)\nfunc Support() {}\n',
            "aliased.go": 'package p\nimport testpkg "testing"\nfunc Support() {}\n',
            "actual_test.go": 'package p\nimport "testing"\nfunc TestIt(t *testing.T) {}\n',
            "test-support/helper.go": "package helper\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        records = {item.relative_path: item for item in RepositoryInventory(repo)}
        for relative in ("single.go", "grouped.go", "aliased.go"):
            self.assertTrue(records[relative].included_in_metrics)
        self.assertTrue(records["actual_test.go"].is_test)
        self.assertTrue(records["test-support/helper.go"].is_test)

    def test_static_library_tree_is_dependency_but_domain_named_vendors_is_source(self):
        repo = self._repo()
        files = {
            "src/main/webapp/resources/js/libs/angular.js": "function angular() {}\n",
            "client/src/pages/vendors/index.tsx": "export const Vendors = () => null;\n",
            "src/vendor/dependency.js": "function dependency() {}\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        inventory = RepositoryInventory(repo)
        records = {item.relative_path: item for item in inventory}
        self.assertEqual(
            records["src/main/webapp/resources/js/libs/angular.js"].exclusion_reason,
            "dependency",
        )
        self.assertTrue(records["client/src/pages/vendors/index.tsx"].included_in_metrics)
        self.assertNotIn("src/vendor/dependency.js", records)
        self.assertIn(
            {"path": "src/vendor", "exclusion_reason": "vendor", "descended": False},
            inventory.pruned_directories,
        )

    def test_jinja_javascript_template_is_not_parsed_as_standalone_source(self):
        repo = self._repo()
        path = repo / "src/templates/settings.js"
        path.parent.mkdir(parents=True)
        path.write_text("window.settings = {{ settings | safe }};\n", encoding="utf-8")
        record = RepositoryInventory(repo).get("src/templates/settings.js")
        self.assertFalse(record.is_generated)
        self.assertEqual(record.exclusion_reason, "templated_source")
        self.assertEqual(record.template_family, "Jinja")

    def test_generated_headers_are_detected(self):
        repo = self._repo()
        (repo / "generated.go").write_text(
            "// Code generated by tool. DO NOT EDIT.\npackage p\n", encoding="utf-8"
        )
        record = RepositoryInventory(repo).get("generated.go")
        self.assertTrue(record.is_generated)
        self.assertEqual(record.exclusion_reason, "generated")

    def test_generated_marker_must_be_in_initial_comment_header(self):
        repo = self._repo()
        files = {
            "valid.js": "// Generated by tool\nconst x = 1;\n",
            "string.js": 'const text = "Code generated by tool. DO NOT EDIT";\n',
            "late.js": "const x = 1;\n// Generated by tool\n",
            "block.java": "/*\n * Generated by tool\n */\nclass A {}\n",
            "python.py": "# Code generated by tool. DO NOT EDIT.\nvalue = 1\n",
        }
        for name, content in files.items():
            (repo / name).write_text(content, encoding="utf-8")
        records = {item.relative_path: item for item in RepositoryInventory(repo)}
        for name in ("valid.js", "block.java", "python.py"):
            self.assertTrue(records[name].is_generated)
        for name in ("string.js", "late.js"):
            self.assertFalse(records[name].is_generated)

    def test_java_test_suffix_is_case_insensitive(self):
        repo = self._repo()
        for name in ("FooTest.java", "FooTests.java", "FooTest.JAVA", "FOOTEST.JAVA"):
            (repo / name).write_text("class X {}\n", encoding="utf-8")
        records = {item.relative_path: item for item in RepositoryInventory(repo)}
        self.assertTrue(all(record.is_test for record in records.values()))

    def test_content_hash_uses_bytes_not_size(self):
        first = self._repo()
        second = self._repo()
        (first / "a.py").write_text("aaa", encoding="utf-8")
        (second / "a.py").write_text("bbb", encoding="utf-8")
        hash_one = RepositoryInventory(first).get("a.py").content_hash
        hash_two = RepositoryInventory(second).get("a.py").content_hash
        self.assertNotEqual(hash_one, hash_two)

    def test_unsupported_source_does_not_enter_metrics(self):
        repo = self._repo()
        (repo / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        record = RepositoryInventory(repo).get("main.rs")
        self.assertFalse(record.is_source)
        self.assertFalse(record.included_in_metrics)

    def test_oversized_source_is_reported(self):
        repo = self._repo()
        (repo / "large.py").write_text("x = 1\n", encoding="utf-8")
        config = AnalysisConfig.from_env(max_source_file_size_bytes=2)
        record = RepositoryInventory(repo, config).get("large.py")
        self.assertEqual(record.read_status, "skipped_oversized")
        self.assertTrue(record.oversized)
        self.assertTrue(record.included_in_metrics)
        self.assertIsNone(record.content_hash)

    def test_nested_repository_is_not_mixed(self):
        repo = self._repo()
        (repo / "main.py").write_text("pass\n", encoding="utf-8")
        (repo / "nested" / ".git").mkdir(parents=True)
        (repo / "nested" / "foreign.py").write_text("pass\n", encoding="utf-8")
        inventory = RepositoryInventory(repo)
        self.assertIsNone(inventory.get("nested/foreign.py"))
        self.assertEqual(inventory.summary()["nested_repositories_excluded"], ["nested"])

    def test_content_is_reused_from_inventory_cache(self):
        repo = self._repo()
        (repo / "a.py").write_text("value = 1\n", encoding="utf-8")
        inventory = RepositoryInventory(repo)
        (repo / "a.py").write_text("changed = 2\n", encoding="utf-8")
        self.assertEqual(inventory.read_text("a.py").splitlines(), ["value = 1"])

    def test_decoded_text_is_cached_per_path_and_encoding(self):
        repo = self._repo()
        (repo / "a.py").write_text("value = 1\n", encoding="utf-8")
        inventory = RepositoryInventory(repo)
        self.assertEqual(inventory.read_text("a.py").splitlines(), ["value = 1"])
        self.assertEqual(inventory.read_text("a.py").splitlines(), ["value = 1"])
        self.assertEqual(inventory.decode_operations, 1)

    def test_unreadable_directory_is_recorded_and_makes_inventory_partial(self):
        repo = self._repo()
        (repo / "app.py").write_text("pass\n", encoding="utf-8")
        config = AnalysisConfig.from_env()
        real_walk = __import__("os").walk

        def walk_with_error(root, topdown=True, onerror=None, followlinks=False):
            yield from real_walk(root, topdown=topdown, onerror=onerror, followlinks=followlinks)
            error = PermissionError(errno.EACCES, "access denied", str(Path(root) / "secret"))
            onerror(error)

        with patch("modules.inventory.os.walk", side_effect=walk_with_error):
            inventory = RepositoryInventory(repo, config)
        self.assertEqual(inventory.inventory_status, "partial")
        self.assertEqual(inventory.directory_errors[0]["relative_path"], "secret")
        self.assertEqual(inventory.directory_errors[0]["errno"], errno.EACCES)

    def test_pruned_subtrees_are_not_opened_or_hashed(self):
        repo = self._repo()
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        for dirname in ("node_modules", "build", "dist", "vendor"):
            target = repo / dirname
            target.mkdir()
            for index in range(500):
                (target / f"fake_{index}.js").write_text("const x = 1;\n", encoding="utf-8")
        config = AnalysisConfig.from_env()
        original_open = Path.open
        opened = []

        def counting_open(path, *args, **kwargs):
            candidate = Path(path)
            try:
                opened.append(candidate.relative_to(repo).as_posix())
            except ValueError:
                pass
            return original_open(path, *args, **kwargs)

        with patch("pathlib.Path.open", new=counting_open):
            inventory = RepositoryInventory(repo, config)
        self.assertEqual([item.relative_path for item in inventory], ["src/app.py"])
        self.assertEqual(opened, ["src/app.py"])
        self.assertEqual(sum(item.content_hash is not None for item in inventory), 1)
        self.assertEqual(len(inventory.pruned_directories), 4)

    def test_summary_reports_language_distribution_and_exclusions(self):
        repo = self._repo()
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        (repo / "a_test.go").write_text("package p\n", encoding="utf-8")
        summary = RepositoryInventory(repo).summary()
        self.assertEqual(summary["language_distribution"], {"Python": 1})
        self.assertEqual(summary["files_excluded_by_reason"], {"test": 1})

    def test_message_broker_evidence_reuses_inventory(self):
        repo = self._repo()
        (repo / "go.mod").write_text(
            "module example\nrequire github.com/nats-io/nats.go v1.0.0\n", encoding="utf-8"
        )
        inventory = RepositoryInventory(repo)
        evidence = detect_message_brokers(repo, inventory=inventory)
        self.assertEqual(evidence["brokers"], ["nats"])
        self.assertEqual(evidence["evidence"][0]["file"], "go.mod")
        self.assertEqual(inventory.walk_count, 1)
