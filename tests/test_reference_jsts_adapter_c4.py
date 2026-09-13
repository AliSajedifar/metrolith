"""C4-A3: regressions for the independent JavaScript/TypeScript complexity adapter.

Exercises the TypeScript Compiler API adapter, not ArchLens. Skips as a
**capability** statement when the pinned Node/TypeScript reference environment is
absent: a skip means the toolchain is not provisioned, never that the adapter is
fine.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
REFERENCE = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference"))
NODE = REFERENCE / "node-v22.20.0-win-x64/node.exe"
NODE_MODULES = REFERENCE / "nodepkgs/node_modules"
ADAPTER = REPOSITORY / "validation/differential/reference/node/reference_complexity.js"
CORPUS = REPOSITORY / "validation/differential/corpus/complexity"

ADAPTER_VERSION = "2.0.0"
METRICS = (
    "nloc", "formal_parameter_count", "cyclomatic_complexity",
    "decision_point_count", "boolean_operator_count",
    "max_condition_operator_count", "max_nesting_depth",
)


def available() -> bool:
    return NODE.is_file() and NODE_MODULES.is_dir() and ADAPTER.is_file()


def run_adapter(source: str, suffix: str = ".js") -> list[dict]:
    environment = dict(os.environ, NODE_PATH=str(NODE_MODULES))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"sample{suffix}"
        # newline="" preserves the bytes: the bytes measured must be the bytes
        # analyzed.
        path.write_text(source, encoding="utf-8", newline="")
        completed = subprocess.run(
            [str(NODE), str(ADAPTER), str(path)],
            capture_output=True, text=True, check=True, env=environment,
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


def by_name(records: list[dict]) -> dict[str, dict]:
    return {record["name"]: record for record in records}


@unittest.skipUnless(available(), "pinned Node/TypeScript reference not provisioned")
class JavaScriptAdapterTests(unittest.TestCase):

    def test_every_loop_form_switch_catch_and_ternary(self):
        records = by_name(run_adapter(
            "function f(xs, a) {\n"
            "  for (const x of xs) { a++; }\n"
            "  for (const k in xs) { a++; }\n"
            "  for (let i = 0; i < 3; i++) { a++; }\n"
            "  while (a > 0) { a--; }\n"
            "  do { a++; } while (a < 3);\n"
            "  switch (a) { case 1: break; case 2: break; default: break; }\n"
            "  try { a++; } catch (e) { a--; } finally { a = 0; }\n"
            "  return a ? 1 : 2;\n"
            "}\n"
        ))
        self.assertEqual(
            records["f"]["decision_point_count"], 9,
            "5 loops + 2 case arms + 1 catch + 1 ternary; default and finally add 0",
        )

    def test_nullish_and_logical_assignment_count(self):
        records = by_name(run_adapter(
            "function f(a, b) {\n"
            "  const n = a ?? b;\n"
            "  let z = a;\n"
            "  z ??= 1;\n"
            "  z ||= 2;\n"
            "  z &&= 3;\n"
            "  return n + z;\n"
            "}\n"
        ))
        record = records["f"]
        self.assertEqual(record["boolean_operator_count"], 4)
        self.assertEqual(
            record["max_condition_operator_count"], 0,
            "there is no decision expression, so the per-condition maximum is 0 "
            "even though four operators exist",
        )

    def test_optional_chaining_is_not_a_decision(self):
        records = by_name(run_adapter("function f(a) { return a?.b?.c; }\n"))
        self.assertEqual(records["f"]["decision_point_count"], 0)
        self.assertEqual(records["f"]["boolean_operator_count"], 0)

    def test_callbacks_do_not_leak_structural_complexity(self):
        records = by_name(run_adapter(
            "function host(xs) {\n"
            "  xs.map((v) => v && v.z);\n"
            "  xs.forEach(function (v) { if (v) { return v; } return null; });\n"
            "  if (xs) { return 1; }\n"
            "  return 0;\n"
            "}\n"
        ))
        record = records["host"]
        self.assertEqual(record["decision_point_count"], 1)
        self.assertEqual(record["boolean_operator_count"], 0)
        self.assertEqual(record["max_nesting_depth"], 1)

    def test_expression_bodied_arrow_is_measured(self):
        records = by_name(run_adapter("const bound = (x) => x && x.y;\n"))
        self.assertEqual(records["bound"]["boolean_operator_count"], 1)
        self.assertEqual(records["bound"]["cyclomatic_complexity"], 2)

    def test_class_members_accessors_static_and_private_count(self):
        records = by_name(run_adapter(
            "class W {\n"
            "  constructor() { this.v = 1; }\n"
            "  render(x) { if (x) { return 1; } return 0; }\n"
            "  get size() { return 1; }\n"
            "  static make(a, b) { return a + b; }\n"
            "  #secret(a) { return a; }\n"
            "  handler = () => { if (this.v) { return 1; } return 0; };\n"
            "}\n"
        ))
        for name in ("render", "size", "make", "#secret"):
            self.assertIn(name, records, name)
        self.assertNotIn("constructor", records, "constructors are secondary")
        self.assertNotIn("handler", records, "a class-property arrow is anonymous")

    def test_object_literal_methods_and_accessors_count(self):
        records = by_name(run_adapter(
            "const helpers = {\n"
            "  first(a) { return a; },\n"
            "  get second() { return 2; },\n"
            "};\n"
        ))
        self.assertIn("first", records)
        self.assertIn("second", records)

    def test_async_and_generator_declarations_are_in_the_population(self):
        records = by_name(run_adapter(
            "async function a(xs) { for (const x of xs) { if (x) { return x; } } return null; }\n"
            "function* g() { yield 1; }\n"
        ))
        self.assertIn("a", records)
        self.assertIn("g", records)
        self.assertEqual(records["a"]["decision_point_count"], 2)

    def test_destructuring_and_rest_each_count_once(self):
        records = by_name(run_adapter(
            "function f({ a, b }, [c], ...rest) { return a; }\n"
        ))
        self.assertEqual(records["f"]["formal_parameter_count"], 3)

    def test_commonjs_export_target_is_a_stable_binding(self):
        records = by_name(run_adapter(
            "module.exports.exported = function (z) { return z; };\n"
        ))
        self.assertIn("exported", records)

    def test_the_javascript_corpus_agrees_metric_for_metric(self):
        expectations = json.loads(
            (CORPUS / "javascript/expectations.json").read_text(encoding="utf-8")
        )
        records = {
            record["qualified_name"]: record
            for record in run_adapter(
                (CORPUS / "javascript/constructs.js").read_text(encoding="utf-8")
            )
        }
        self.assertEqual(len(records), expectations["file_level"]["callable_count"])
        for entry in expectations["callables"]:
            with self.subTest(callable=entry["qualified_name"]):
                record = records[entry["qualified_name"]]
                for metric in METRICS:
                    self.assertEqual(
                        record[metric], entry["expected"][metric],
                        f"{entry['qualified_name']}.{metric}",
                    )


@unittest.skipUnless(available(), "pinned Node/TypeScript reference not provisioned")
class TypeScriptAdapterTests(unittest.TestCase):

    def test_this_parameter_is_excluded_and_flagged(self):
        records = by_name(run_adapter(
            "class W {\n"
            "  m(this: W, a: number, { b, c }: any, ...rest: number[]): number {\n"
            "    if (a > 0 && a < 9) { return a; }\n"
            "    return 0;\n"
            "  }\n"
            "}\n",
            ".ts",
        ))
        record = records["m"]
        self.assertEqual(record["formal_parameter_count"], 3)
        self.assertTrue(record["declares_typescript_this_parameter"])
        self.assertEqual(record["boolean_operator_count"], 1)
        self.assertEqual(record["max_condition_operator_count"], 1)

    def test_overload_signatures_are_excluded(self):
        records = by_name(run_adapter(
            "function f(a: number): number;\n"
            "function f(a: string): number;\n"
            "function f(a: any): number {\n"
            "  if (typeof a === 'number') { return a; }\n"
            "  return a.length;\n"
            "}\n"
            "declare function declaredOnly(a: number): number;\n",
            ".ts",
        ))
        self.assertEqual(
            len(records), 1,
            "only the implementation has a body; signatures and a declare are "
            "declaration-only",
        )
        self.assertEqual(records["f"]["decision_point_count"], 1)

    def test_decorator_lines_extend_the_span(self):
        records = by_name(run_adapter(
            "class W {\n"
            "  @HostListener('click')\n"
            "  @Throttle(100)\n"
            "  onClick(e: Event): number { return 1; }\n"
            "}\n"
            "function HostListener(n: string): MethodDecorator { return () => undefined; }\n"
            "function Throttle(ms: number): MethodDecorator { return () => undefined; }\n",
            ".ts",
        ))
        record = records["onClick"]
        self.assertEqual(
            record["start_line"], 2,
            "the span extends back over BOTH decorators, not just the nearest",
        )
        self.assertEqual(record["nloc"], 3)

    def test_optional_and_defaulted_parameters_each_count_once(self):
        records = by_name(run_adapter(
            "export function f(a: number, b?: string, c = 3): number { return a; }\n",
            ".ts",
        ))
        self.assertEqual(records["f"]["formal_parameter_count"], 3)

    def test_the_typescript_corpus_agrees_metric_for_metric(self):
        expectations = json.loads(
            (CORPUS / "typescript/expectations.json").read_text(encoding="utf-8")
        )
        records = {
            record["qualified_name"]: record
            for record in run_adapter(
                (CORPUS / "typescript/constructs.ts").read_text(encoding="utf-8"), ".ts"
            )
        }
        self.assertEqual(len(records), expectations["file_level"]["callable_count"])
        for entry in expectations["callables"]:
            with self.subTest(callable=entry["qualified_name"]):
                record = records[entry["qualified_name"]]
                for metric in METRICS:
                    self.assertEqual(
                        record[metric], entry["expected"][metric],
                        f"{entry['qualified_name']}.{metric}",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
