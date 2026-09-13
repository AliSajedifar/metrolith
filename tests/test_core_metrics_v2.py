import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.core_metrics import ParserRegistry, compute_repository_metrics
from modules.config import AnalysisConfig
from modules.inventory import RepositoryInventory


class CoreMetricContractTests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _metrics(self, repo, expected=None):
        inventory = RepositoryInventory(repo)
        return compute_repository_metrics(inventory, expected_language=expected), inventory

    def test_physical_loc_excludes_blank_and_comment_only_lines(self):
        repo = self._repo()
        (repo / "A.java").write_text(
            "// comment\nclass A { // inline\n  /* block\n     comment */\n  String s = \"// code\";\n}\n\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["java"]
        self.assertEqual(metric["code_lines"], 3)
        self.assertEqual(metric["comment_lines"], 3)
        self.assertEqual(metric["blank_lines"], 1)
        self.assertEqual(metric["total_physical_lines"], 7)

    def test_loc_preserves_comment_markers_inside_language_strings(self):
        fixtures = {
            "Text.java": (
                "class Text {\n"
                "  String marker = \"// not comment\";\n"
                "  String block = \"\"\"\n"
                "      /* not comment */\n"
                "      line\n"
                "      \"\"\";\n"
                "  // comment\n"
                "  void run() {} /* inline */\n"
                "}\n",
                (8, 1, 9),
            ),
            "app.js": (
                "const url = \"http://example\"; // inline\n"
                "const regex = /https?:\\/\\/example/;\n"
                "const tpl = `first\n"
                "// still string\n"
                "/* still string */`;\n"
                "/* comment only */\n"
                "function run() {}\n",
                (6, 1, 7),
            ),
            "app.py": (
                "\"\"\"module docs\n"
                "# still string\n"
                "\"\"\"\n"
                "marker = r\"# not comment\"\n"
                "# comment only\n"
                "def run(): return 1  # inline\n",
                (5, 1, 6),
            ),
            "app.go": (
                "package p\n"
                "var raw = `first\n"
                "// still string\n"
                "/* still string */\n"
                "`\n"
                "func Run() {} // inline\n"
                "// comment only\n",
                (6, 1, 7),
            ),
        }
        for filename, (source, expected) in fixtures.items():
            with self.subTest(filename=filename):
                repo = self._repo()
                (repo / filename).write_text(source, encoding="utf-8", newline="")
                metrics, _ = self._metrics(repo)
                language = next(
                    value
                    for value in ("java", "javascript", "python", "go")
                    if metrics["by_language"][value]["source_files"]
                )
                metric = metrics["by_language"][language]
                self.assertEqual(
                    (
                        metric["code_lines"],
                        metric["comment_lines"],
                        metric["total_physical_lines"],
                    ),
                    expected,
                )

    def test_empty_file_and_final_line_without_newline_are_exact(self):
        repo = self._repo()
        (repo / "empty.py").write_bytes(b"")
        (repo / "single.py").write_bytes(b"value = 1")
        metric = self._metrics(repo)[0]["by_language"]["python"]
        self.assertEqual(metric["source_files"], 2)
        self.assertEqual(metric["lines_of_code"], 1)
        self.assertEqual(metric["total_physical_lines"], 1)
        self.assertEqual(metric["metric_status"], "complete")

    def test_python_docstrings_are_consistently_treated_as_code(self):
        repo = self._repo()
        (repo / "a.py").write_text(
            '"""module docs\ncontinued"""\n# comment\nvalue = 1  # inline', encoding="utf-8"
        )
        metric = self._metrics(repo)[0]["by_language"]["python"]
        self.assertEqual(metric["code_lines"], 3)
        self.assertEqual(metric["comment_lines"], 1)
        self.assertEqual(metric["total_physical_lines"], 4)

    def test_java_entities_use_ast_context(self):
        repo = self._repo()
        (repo / "Types.java").write_text(
            "class Other {}\n"
            "class Main { Main(){} void Other(){} void run(){} }\n"
            "record Data(int x) {}\n"
            "interface Port { void missing(); default void ready(){} }\n"
            "enum E { A }\n@interface Marker {}\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["java"]
        self.assertEqual(metric["classes_structs"], 3)
        self.assertEqual(metric["interfaces"], 1)
        self.assertEqual(metric["enums"], 1)
        self.assertEqual(metric["annotation_types"], 1)
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["signature_only_methods"], 1)
        self.assertEqual(metric["methods_functions"], 3)

    def test_javascript_counts_only_stably_named_implementations(self):
        repo = self._repo()
        (repo / "a.js").write_text(
            "class C { constructor(){} async m(){} get x(){return 1} }\n"
            "function top(){}\nconst arrow=()=>{};\nconst expr=function(){};\n"
            "const object={ run(){}, callback:()=>{} };\n"
            "items.map(x=>x);\nfunction outer(){ function nested(){} }\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["javascript"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["methods_functions"], 7)
        self.assertEqual(metric["nested_functions"], 1)
        self.assertGreaterEqual(metric["anonymous_functions"], 2)
        self.assertEqual(metric["async_functions"], 1)

    def test_typescript_separates_interfaces_enums_aliases_and_signatures(self):
        repo = self._repo()
        (repo / "a.ts").write_text(
            "interface I { m(): void }\ntype T = string;\nenum E { A }\n"
            "function f(x: string): void;\nfunction f(x: any) {}\n"
            "class C { constructor(){} m(): void; m(){} }\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["typescript"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["interfaces"], 1)
        self.assertEqual(metric["enums"], 1)
        self.assertEqual(metric["type_aliases"], 1)
        self.assertEqual(metric["signature_only_methods"], 3)
        self.assertEqual(metric["methods_functions"], 2)

    def test_typescript_abstract_declare_and_namespace_entities_are_explicit(self):
        repo = self._repo()
        (repo / "declarations.ts").write_text(
            "abstract class AbstractService { abstract missing(): void; ready() {} }\n"
            "declare class DeclaredService { signature(): void }\n"
            "namespace Services { export function create() {} }\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["typescript"]
        self.assertEqual(metric["classes_structs"], 2)
        self.assertEqual(metric["signature_only_methods"], 2)
        self.assertEqual(metric["class_methods"], 1)
        self.assertEqual(metric["module_functions"], 1)
        self.assertEqual(metric["methods_functions"], 2)
        self.assertEqual(metric["metric_status"], "complete")

    def test_javascript_local_named_class_methods_are_main_methods(self):
        repo = self._repo()
        (repo / "local.js").write_text(
            "function outer() { class Local { constructor() {} run() {} } return Local; }\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["javascript"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["class_methods"], 1)
        self.assertEqual(metric["module_functions"], 1)
        self.assertEqual(metric["methods_functions"], 2)

    def test_tsx_grammar_is_supported(self):
        repo = self._repo()
        (repo / "view.tsx").write_text(
            "export function View(){ return <div/> }\n", encoding="utf-8"
        )
        metric = self._metrics(repo)[0]["by_language"]["typescript"]
        self.assertEqual(metric["metric_status"], "complete")
        self.assertEqual(metric["methods_functions"], 1)

    def test_all_javascript_and_typescript_extensions_select_working_grammars(self):
        repo = self._repo()
        javascript = {
            "app.js": "function js() {}\n",
            "view.jsx": "function jsx(){ return <div/> }\n",
            "module.mjs": "export function mjs() {}\n",
            "common.cjs": "module.exports.cjs = function () {};\n",
        }
        typescript = {
            "app.ts": "function ts(x: number) { return x }\n",
            "view.tsx": "function tsx(){ return <div/> }\n",
            "module.mts": "export function mts(x: number) { return x }\n",
            "common.cts": "function cts(x: number) { return x }\n",
        }
        for name, content in {**javascript, **typescript}.items():
            (repo / name).write_text(content, encoding="utf-8")
        metrics, _ = self._metrics(repo)
        for language in ("javascript", "typescript"):
            with self.subTest(language=language):
                metric = metrics["by_language"][language]
                self.assertEqual(metric["source_files"], 4)
                self.assertEqual(metric["methods_functions"], 4)
                self.assertEqual(metric["metric_status"], "complete")

    def test_javascript_family_scope_inventories_js_ts_and_tsx_before_aggregation(self):
        repo = self._repo()
        for name, content in {
            "app.js": "export function js() {}\n",
            "typed.ts": "export function ts(value: number) { return value }\n",
            "view.tsx": "export function View(){ return <div/> }\n",
        }.items():
            (repo / name).write_text(content, encoding="utf-8")
        metrics, _ = self._metrics(repo)
        scope = metrics["javascript_family_scope"]
        self.assertEqual(scope["contract"], "javascript_typescript_family_aggregate")
        self.assertEqual(
            scope["extensions"],
            [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"],
        )
        self.assertEqual(scope["inventoried_by_extension"], {".js": 1, ".ts": 1, ".tsx": 1})
        self.assertEqual(scope["included_by_extension"], {".js": 1, ".ts": 1, ".tsx": 1})
        self.assertEqual(scope["grammar_by_extension"][".jsx"], "javascript_jsx_capable")
        self.assertEqual(scope["grammar_by_extension"][".ts"], "typescript")
        self.assertEqual(scope["grammar_by_extension"][".tsx"], "tsx")
        self.assertEqual(metrics["aggregate"]["source_files"], 3)
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")

    def test_supported_extensions_are_case_insensitive(self):
        repo = self._repo()
        for name, content in {
            "A.JAVA": "class A {}\n",
            "app.MJS": "function run() {}\n",
            "typed.MTS": "function typed(x: number) { return x }\n",
            "main.PY": "def main(): return 1\n",
            "main.GO": "package p\nfunc Main() {}\n",
        }.items():
            (repo / name).write_text(content, encoding="utf-8")
        metrics, _ = self._metrics(repo)
        self.assertEqual(metrics["aggregate"]["source_files"], 5)
        self.assertEqual(metrics["aggregate"]["classes_structs"], 1)
        self.assertEqual(metrics["aggregate"]["methods_functions"], 4)
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")

    def test_python_excludes_constructors_nested_functions_and_lambdas(self):
        repo = self._repo()
        (repo / "a.py").write_text(
            "class C:\n"
            "  def __init__(self): pass\n"
            "  @classmethod\n  def make(cls): return cls()\n"
            "  @property\n  def value(self): return 1\n"
            "async def top():\n  def nested(): pass\n  return lambda x: x\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["python"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["methods_functions"], 3)
        self.assertEqual(metric["nested_functions"], 1)
        self.assertEqual(metric["lambdas"], 1)
        self.assertEqual(metric["async_functions"], 1)

    def test_go_counts_named_structs_functions_and_receiver_methods(self):
        repo = self._repo()
        (repo / "a.go").write_text(
            "package p\ntype S[T any] struct{}\ntype I interface{}\ntype A = S[int]\n"
            "var anon = struct{ X int }{}\nfunc init(){}\nfunc F[T any](){}\n"
            "func (s S[int]) M(){}\nvar callback=func(){}\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["go"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["interfaces"], 1)
        self.assertEqual(metric["type_aliases"], 1)
        self.assertEqual(metric["anonymous_structs"], 1)
        self.assertEqual(metric["methods_functions"], 3)
        self.assertEqual(metric["anonymous_functions"], 1)

    def test_declaration_and_test_files_do_not_contribute(self):
        repo = self._repo()
        for name, content in {
            "types.d.ts": "declare function f(): void;\n",
            "types.d.mts": "declare function g(): void;\n",
            "stub.pyi": "def f(): ...\n",
            "main_test.go": "package p\nfunc TestX(t *testing.T){}\n",
        }.items():
            (repo / name).write_text(content, encoding="utf-8")
        metrics, inventory = self._metrics(repo)
        self.assertEqual(metrics["aggregate"]["source_files"], 0)
        self.assertTrue(all(not record.included_in_metrics for record in inventory))

    def test_invalid_python_does_not_become_verified_zero(self):
        repo = self._repo()
        (repo / "bad.py").write_text("def broken(:\n", encoding="utf-8")
        metric = self._metrics(repo)[0]["aggregate"]
        self.assertIn(metric["metric_status"], {"partial", "failed"})
        self.assertIsNone(metric["classes_structs"])
        self.assertIsNone(metric["methods_functions"])
        self.assertEqual(metric["source_files"], 1)

    def test_parser_unavailable_is_failed_not_zero(self):
        repo = self._repo()
        (repo / "A.java").write_text("class A {}\n", encoding="utf-8")
        registry = ParserRegistry()
        with patch.object(registry, "get", side_effect=RuntimeError("missing")):
            metric = compute_repository_metrics(RepositoryInventory(repo), parser_registry=registry)["aggregate"]
        self.assertEqual(metric["metric_status"], "failed")
        self.assertIsNone(metric["lines_of_code"])
        self.assertIsNone(metric["classes_structs"])

    def test_multilanguage_aggregate_and_expected_primary(self):
        repo = self._repo()
        (repo / "a.py").write_text("def f(): pass\n", encoding="utf-8")
        (repo / "a.go").write_text("package p\nfunc F(){}\n", encoding="utf-8")
        metrics, _ = self._metrics(repo, expected="Go")
        self.assertEqual(metrics["aggregate"]["source_files"], 2)
        self.assertEqual(metrics["aggregate"]["methods_functions"], 2)
        self.assertEqual(metrics["primary_language_name"], "Go")
        self.assertEqual(metrics["primary_language"], metrics["by_language"]["go"])

    def test_empty_repository_is_not_applicable(self):
        metrics, _ = self._metrics(self._repo())
        self.assertEqual(metrics["aggregate"]["metric_status"], "not_applicable")
        self.assertEqual(metrics["aggregate"]["source_files"], 0)

    def test_commonjs_functions_and_stable_class_assignments(self):
        repo = self._repo()
        (repo / "exports.js").write_text(
            "module.exports = function () {};\n"
            "module.exports = async () => {};\n"
            "module.exports.run = function named() {};\n"
            "exports.go = async function* named() {};\n"
            "obj[dynamicName] = function () {};\n"
            "getObject().run = () => {};\n"
            "function nested(){ exports.nope = () => {}; }\n"
            "const Service = class { method(){} };\n"
            "module.exports.Controller = class { run(){} };\n"
            "exports.Other = class {};\n"
            "callback(class { ignored(){} });\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["javascript"]
        self.assertEqual(metric["module_functions"], 5)
        self.assertEqual(metric["exported_functions"], 4)
        self.assertEqual(metric["classes_structs"], 3)
        self.assertEqual(metric["class_methods"], 2)
        self.assertEqual(metric["methods_functions"], 7)
        self.assertGreaterEqual(metric["anonymous_classes"], 1)

    def test_python_local_class_methods_are_methods(self):
        repo = self._repo()
        (repo / "local.py").write_text(
            "def outer():\n"
            "    class Local:\n"
            "        def __init__(self): pass\n"
            "        def method(self):\n"
            "            def nested(): pass\n"
            "            return nested()\n"
            "    return Local()\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["python"]
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["class_methods"], 1)
        self.assertEqual(metric["module_functions"], 1)
        self.assertEqual(metric["nested_functions"], 1)

    def test_python_overload_stubs_are_signature_only(self):
        repo = self._repo()
        (repo / "overloads.py").write_text(
            "from typing import overload\n"
            "@overload\ndef parse(x: int) -> int: ...\n"
            "@overload\ndef parse(x: str) -> str: ...\n"
            "def parse(x): return x\n"
            "class C:\n"
            "    @overload\n    def method(self, x: int) -> int: ...\n"
            "    @overload\n    def method(self, x: str) -> str: ...\n"
            "    def method(self, x): return x\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["python"]
        self.assertEqual(metric["signature_only_methods"], 4)
        self.assertEqual(metric["module_functions"], 1)
        self.assertEqual(metric["class_methods"], 1)
        self.assertEqual(metric["methods_functions"], 2)
        self.assertEqual(metric["methods_functions_status"], "complete")

    def test_python_source_encodings(self):
        repo = self._repo()
        (repo / "utf8.py").write_text("name = 'λ'\n", encoding="utf-8")
        (repo / "bom.py").write_bytes(b"\xef\xbb\xbfdef bom():\n    pass\n")
        (repo / "latin.py").write_bytes("# -*- coding: latin-1 -*-\nname = 'café'\n".encode("latin-1"))
        metrics, inventory = self._metrics(repo)
        metric = metrics["by_language"]["python"]
        self.assertEqual(metric["source_files"], 3)
        self.assertEqual(metric["loc_status"], "complete")
        self.assertEqual(inventory.get("utf8.py").detected_encoding, "utf-8")
        self.assertEqual(inventory.get("bom.py").detected_encoding, "utf-8-sig")
        self.assertEqual(inventory.get("latin.py").detected_encoding, "iso-8859-1")

    def test_invalid_python_encoding_is_not_complete(self):
        repo = self._repo()
        (repo / "bad.py").write_bytes(b"# coding: not-a-real-codec\nvalue = '\xff'\n")
        metric = self._metrics(repo)[0]["aggregate"]
        self.assertEqual(metric["source_files"], 1)
        self.assertEqual(metric["source_files_status"], "complete")
        self.assertEqual(metric["loc_status"], "failed")
        self.assertEqual(metric["classes_structs_status"], "failed")

    def test_java_compact_constructor_and_anonymous_scope(self):
        repo = self._repo()
        (repo / "Types.java").write_text(
            "record R(int x) { R { if (x < 0) throw new IllegalArgumentException(); } void ok(){} }\n"
            "class C { void outer(){ class Local { void localMethod(){} } "
            "Runnable r = new Runnable(){ public void ignored(){} }; } }\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["java"]
        self.assertEqual(metric["constructors"], 1)
        self.assertEqual(metric["classes_structs"], 3)
        self.assertEqual(metric["anonymous_classes"], 1)
        self.assertEqual(metric["anonymous_class_methods"], 1)
        self.assertEqual(metric["methods_functions"], 3)

    def test_malformed_tree_keeps_loc_independent_and_filters_bad_entities(self):
        repo = self._repo()
        (repo / "bad.go").write_text(
            "package p\nfunc ok(){}\nfunc broken( {\ntype Hidden struct{}\n",
            encoding="utf-8",
        )
        metrics, inventory = self._metrics(repo)
        metric = metrics["by_language"]["go"]
        self.assertEqual(metric["loc_status"], "complete")
        self.assertEqual(metric["methods_functions_status"], "partial")
        self.assertEqual(metric["methods_functions"], 1)
        self.assertEqual(metric["classes_structs_status"], "partial")
        self.assertEqual(metric["classes_structs"], 0)
        self.assertTrue(inventory.get("bad.go").malformed_nodes)

    def test_expected_language_absence_uses_deterministic_observed_primary(self):
        repo = self._repo()
        (repo / "a.py").write_text("value = 1\ndef f(): pass\n", encoding="utf-8")
        (repo / "a.go").write_text("package p\nfunc F(){}\n", encoding="utf-8")
        metrics, _ = self._metrics(repo, expected="Java")
        self.assertTrue(metrics["expected_language_mismatch"])
        self.assertEqual(metrics["primary_language_name"], "Python")
        self.assertTrue(metrics["primary_language_tie"])

    def test_metric_statuses_are_independent(self):
        repo = self._repo()
        (repo / "bad.go").write_text("package p\nfunc broken( {\n", encoding="utf-8")
        metric = self._metrics(repo)[0]["aggregate"]
        self.assertEqual(metric["inventory_status"], "complete")
        self.assertEqual(metric["source_files_status"], "complete")
        self.assertEqual(metric["loc_status"], "complete")
        self.assertEqual(metric["classes_structs_status"], "partial")
        self.assertEqual(metric["methods_functions_status"], "partial")
        self.assertEqual(metric["metric_status"], "partial")

    def test_malformed_java_missing_name_is_not_counted_as_a_class(self):
        repo = self._repo()
        (repo / "Broken.java").write_text("class { void run(){} }\n", encoding="utf-8")
        metric = self._metrics(repo)[0]["by_language"]["java"]
        self.assertEqual(metric["classes_structs"], 0)
        self.assertEqual(metric["classes_structs_status"], "partial")
        self.assertNotEqual(metric["classes_structs_status"], "complete")

    def test_typescript_commonjs_export_uses_same_ast_rules(self):
        repo = self._repo()
        (repo / "exports.ts").write_text(
            "module.exports.run = async () => {};\n"
            "exports.Service = class { execute(){} };\n",
            encoding="utf-8",
        )
        metric = self._metrics(repo)[0]["by_language"]["typescript"]
        self.assertEqual(metric["exported_functions"], 1)
        self.assertEqual(metric["classes_structs"], 1)
        self.assertEqual(metric["methods_functions"], 2)

    def test_undecodable_python_bytes_are_not_silently_replaced(self):
        repo = self._repo()
        (repo / "bad.py").write_bytes(b"value = '\xff'\n")
        metrics, inventory = self._metrics(repo)
        metric = metrics["aggregate"]
        self.assertEqual(metric["source_files"], 1)
        self.assertEqual(metric["loc_status"], "failed")
        self.assertTrue(inventory.get("bad.py").encoding_error)

    def test_primary_language_loc_tie_is_broken_by_source_files(self):
        repo = self._repo()
        (repo / "app.py").write_text("value = 1\nvalue2 = 2\n", encoding="utf-8")
        (repo / "a.go").write_text("package p\n", encoding="utf-8")
        (repo / "b.go").write_text("package p\n", encoding="utf-8")
        metrics, _ = self._metrics(repo, expected="Java")
        self.assertEqual(metrics["by_language"]["python"]["lines_of_code"], 2)
        self.assertEqual(metrics["by_language"]["go"]["lines_of_code"], 2)
        self.assertEqual(metrics["primary_language_name"], "Go")
        self.assertFalse(metrics["primary_language_tie"])

    def test_unreadable_source_is_counted_and_marks_content_metrics_failed(self):
        repo = self._repo()
        (repo / "unreadable.py").write_text("def hidden(): pass\n", encoding="utf-8")
        with patch(
            "modules.inventory.RepositoryInventory._read_and_hash",
            return_value=(None, "failed", None, "PermissionError: access denied"),
        ):
            inventory = RepositoryInventory(repo)
        metric = compute_repository_metrics(inventory)["aggregate"]
        self.assertEqual(metric["source_files"], 1)
        self.assertEqual(metric["source_files_status"], "complete")
        self.assertEqual(metric["source_files_readable"], 0)
        self.assertEqual(metric["source_files_failed_read"], 1)
        self.assertEqual(metric["loc_status"], "failed")
        self.assertEqual(metric["classes_structs_status"], "failed")
        self.assertIsNone(metric["lines_of_code"])

    def test_oversized_source_is_counted_and_marks_content_metrics_failed(self):
        repo = self._repo()
        (repo / "large.py").write_text("def hidden(): pass\n", encoding="utf-8")
        config = AnalysisConfig.from_env(max_source_file_size_bytes=2)
        inventory = RepositoryInventory(repo, config)
        metric = compute_repository_metrics(inventory)["aggregate"]
        self.assertEqual(metric["source_files"], 1)
        self.assertEqual(metric["source_files_status"], "complete")
        self.assertEqual(metric["source_files_failed_read"], 0)
        self.assertEqual(metric["loc_status"], "failed")
        self.assertTrue(inventory.get("large.py").oversized)

    def test_partial_inventory_makes_all_repository_metrics_partial(self):
        import errno
        import os

        repo = self._repo()
        (repo / "app.py").write_text("class App:\n    def run(self): pass\n", encoding="utf-8")
        real_walk = os.walk

        def walk_with_error(root, topdown=True, onerror=None, followlinks=False):
            yield from real_walk(root, topdown=topdown, onerror=onerror, followlinks=followlinks)
            onerror(PermissionError(errno.EACCES, "access denied", str(Path(root) / "secret")))

        with patch("modules.inventory.os.walk", side_effect=walk_with_error):
            inventory = RepositoryInventory(repo)
        metric = compute_repository_metrics(inventory)["aggregate"]
        for field in (
            "inventory_status", "source_files_status", "loc_status",
            "classes_structs_status", "methods_functions_status", "metric_status",
        ):
            self.assertEqual(metric[field], "partial")
        self.assertEqual(metric["source_files"], 1)
        self.assertEqual(metric["classes_structs"], 1)


class JavaEnumMethodRegressionTests(unittest.TestCase):
    """Methods declared in an enum body count toward `methods_functions`.

    Found by differential validation, not by review: the independent javac
    reference counted 2 methods in a real file where ArchLens counted 1, and the
    difference was a method inside an `enum` body.

    `_java_method_owner` guarded on the member's parent being a `class_body` or
    `interface_body`. tree-sitter-java nests enum members under
    `enum_body_declarations` inside `enum_body`, so an enum method matched
    neither, and was counted as neither a class method nor an anonymous-class
    method — it was dropped. The function's own owner set already listed
    `enum_declaration`, so the intent was never in doubt; the guard above it
    simply made that branch unreachable.
    """

    @staticmethod
    def _entities(source: bytes):
        import tree_sitter
        import tree_sitter_java

        from modules.core_metrics import _java_entities

        language = tree_sitter.Language(tree_sitter_java.language())
        parser = tree_sitter.Parser(language)
        return _java_entities(parser.parse(source).root_node)

    def test_an_enum_method_is_counted(self):
        entities = self._entities(
            b"enum Mode { FAST; int speed() { return 1; } }\n"
        )
        self.assertEqual(entities["class_methods"], 1)
        self.assertEqual(entities["enums"], 1)

    def test_enum_and_class_methods_are_both_counted(self):
        entities = self._entities(
            b"enum Mode { FAST; int speed() { return 1; } }\n"
            b"class Holder { int ordinary() { return 2; } }\n"
        )
        self.assertEqual(entities["class_methods"], 2)

    def test_an_anonymous_class_method_is_still_classified_separately(self):
        """The fix must not reclassify anonymous-class members."""
        entities = self._entities(
            b"class A { Runnable r = new Runnable() { public void run() {} };"
            b" void m() {} }\n"
        )
        self.assertEqual(entities["class_methods"], 1)
        self.assertEqual(entities["anonymous_class_methods"], 1)

    def test_interface_methods_keep_their_existing_classification(self):
        entities = self._entities(
            b"interface I { static int s() { return 1; } int abstractOne(); }\n"
        )
        self.assertEqual(entities["class_methods"], 1)
        self.assertEqual(entities["signature_only_methods"], 1)

    def test_an_enum_constructor_is_still_a_constructor(self):
        entities = self._entities(
            b"enum Mode { FAST(1); private final int v;"
            b" Mode(int v) { this.v = v; } int get() { return v; } }\n"
        )
        self.assertEqual(entities["constructors"], 1)
        self.assertEqual(entities["class_methods"], 1)
