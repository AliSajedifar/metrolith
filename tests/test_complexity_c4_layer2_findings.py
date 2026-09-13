"""Defects found by the C4 Layer-C2 campaign, pinned as minimal reproductions.

Every case here started as a real disagreement between ArchLens and an
independent reference on one of the five Layer-2 subjects, was reduced to the
smallest source that reproduces it, and was adjudicated against
`docs/COMPLEXITY_CONTRACT_V1.md` -- never against whichever side happened to be
larger. Three were ArchLens defects, four were reference-adapter defects, and
the contract decided each one.

The reference-adapter cases skip as **capability statements** when the pinned
toolchain is absent. A skip means the reference environment is not provisioned;
it never means the adapter is fine.
"""

from __future__ import annotations

import ast as python_ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from modules.callable_analysis import analyze_callables
from modules.core_metrics import ParserRegistry
from validation.differential.reference import complexity_drivers as drivers

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _records(language: str, extension: str, source: str):
    """Measure one snippet through the production path."""
    raw = source.encode("utf-8")
    if language == "Python":
        root = python_ast.parse(source)
        result = analyze_callables(
            language, root, raw, f"probe{extension}",
            raw_text=source, masked_text=source,
        )
    else:
        root = ParserRegistry().get(language, extension).parse(raw).root_node
        result = analyze_callables(language, root, raw, f"probe{extension}")
    return {record.qualified_name: record for record in result.records}


# ---------------------------------------------------------------------------
# ArchLens defect 1 -- an unbraced control body opened no nesting level.
# ---------------------------------------------------------------------------


class UnbracedBodyNestingTests(unittest.TestCase):
    """Contract section 10 nests on *entering the body*, not on seeing a block.

    Found on `layer2-typescript-securo` (54 callables), `layer2-java-demo` and
    the subject's `.mjs` scripts: every `if (c) statement` written without
    braces reported `max_nesting_depth` one lower than the same code with
    braces. The contract's nesting-increasing list is `if` / `else if` / `else`
    branches and every loop body -- a branch, not a `{`.
    """

    def test_typescript_unbraced_if_body_opens_a_level(self):
        records = _records("TypeScript", ".tsx", """
function buildAccountActions(t: number, props: {onDelete?: () => void}) {
  const actions: number[] = []
  if (!props.onDelete) return actions
  return actions
}
""".lstrip())
        self.assertEqual(records["buildAccountActions"].max_nesting_depth, 1)

    def test_javascript_unbraced_if_body_opens_a_level(self):
        records = _records("JavaScript", ".mjs", """
function scheduleDeploy() {
  if (deployTimer) clearTimeout(deployTimer)
  deployTimer = 1
}
""".lstrip())
        self.assertEqual(records["scheduleDeploy"].max_nesting_depth, 1)

    def test_java_unbraced_if_inside_a_loop_reaches_depth_two(self):
        records = _records("Java", ".java", """
class C {
    private static String bytesToHex(byte[] bytes) {
        StringBuilder hexString = new StringBuilder();
        for (byte b : bytes) {
            String hex = "x";
            if (hex.length() == 1) hexString.append('0');
            hexString.append(hex);
        }
        return hexString.toString();
    }
}
""".lstrip())
        self.assertEqual(records["C.bytesToHex"].max_nesting_depth, 2)

    def test_the_braced_form_is_unchanged(self):
        """The guard against over-correcting: braces already worked."""
        records = _records("TypeScript", ".tsx", """
function braced(t: number, props: {onDelete?: () => void}) {
  const actions: number[] = []
  if (!props.onDelete) { return actions }
  return actions
}
""".lstrip())
        self.assertEqual(records["braced"].max_nesting_depth, 1)

    def test_an_unbraced_else_if_chain_still_measures_flat(self):
        """`else if` is flat by contract, braces or not."""
        for source, name in (
            ("""
function chain(a: number) {
  if (a === 1) return 1
  else if (a === 2) return 2
  else if (a === 3) return 3
  return 0
}
""".lstrip(), "chain"),
        ):
            records = _records("TypeScript", ".tsx", source)
            self.assertEqual(records[name].max_nesting_depth, 1)

    def test_an_unbraced_else_branch_opens_one_level(self):
        records = _records("TypeScript", ".tsx", """
function branch(a: number) {
  if (a === 1) return 1
  else return 2
}
""".lstrip())
        self.assertEqual(records["branch"].max_nesting_depth, 1)

    def test_unbraced_loop_bodies_nest(self):
        records = _records("JavaScript", ".js", """
function loops(xs) {
  for (const x of xs) if (x) return x
  return null
}
""".lstrip())
        self.assertEqual(records["loops"].max_nesting_depth, 2)


# ---------------------------------------------------------------------------
# ArchLens defect 2 -- a comment inside a parameter list counted as a parameter.
# ---------------------------------------------------------------------------


class CommentsAreNotParametersTests(unittest.TestCase):
    """Contract section 9: one per *parameter* node, as written in source.

    Found on `layer2-typescript-securo`: a two-parameter function whose list
    carried two explanatory comments published `formal_parameter_count` 4.
    tree-sitter reports a `comment` as a NAMED child, so counting named children
    counts prose.
    """

    def test_typescript_comments_between_parameters_are_not_counted(self):
        records = _records("TypeScript", ".tsx", """
export function useRegisterPageChatContext(
  ctx: number,
  // Pass a stable string -- when it changes the registration updates.
  // If you want every render to publish, pass a JSON.stringify of ctx.
  depKey: string,
) {
  return ctx + depKey.length
}
""".lstrip())
        self.assertEqual(
            records["useRegisterPageChatContext"].formal_parameter_count, 2
        )

    def test_javascript_block_comment_in_the_parameter_list_is_not_counted(self):
        records = _records("JavaScript", ".js", """
function f(a, /* why b exists */ b) {
  return a + b
}
""".lstrip())
        self.assertEqual(records["f"].formal_parameter_count, 2)

    def test_the_typescript_this_parameter_is_still_excluded(self):
        """The guard: the fix must not disturb the one deliberate exclusion."""
        records = _records("TypeScript", ".ts", """
export function withThis(this: Window, a: number): number {
  return a
}
""".lstrip())
        record = records["withThis"]
        self.assertEqual(record.formal_parameter_count, 1)
        self.assertTrue(record.declares_typescript_this_parameter)

    def test_java_and_go_parameter_lists_ignore_comments_too(self):
        """Both count from an allowlist already; measured, not assumed."""
        java = _records("Java", ".java", """
class C {
    int m(int a, /* why */ int b) { return a + b; }
}
""".lstrip())
        self.assertEqual(java["C.m"].formal_parameter_count, 2)

        go = _records("Go", ".go", """
package p

func F(a int, // why
	b int) int {
	return a + b
}
""".lstrip())
        self.assertEqual(go["F"].formal_parameter_count, 2)


# ---------------------------------------------------------------------------
# ArchLens defect 3 -- `else:` holding an `if` was flattened as an `elif`.
# ---------------------------------------------------------------------------


class PythonElseHoldingAnIfTests(unittest.TestCase):
    """`elif` and `else:` + indented `if` are the SAME `ast` shape.

    Found on `layer2-typescript-securo`'s Python backend. Both render as
    `orelse == [If]`, so flattening on shape alone flattens a real `else`
    branch too -- and contract section 10 makes an `else` branch
    nesting-increasing. `col_offset` separates them: an `elif` sits at the outer
    `if`'s column, an indented `if` does not.
    """

    def test_an_else_holding_an_if_nests(self):
        records = _records("Python", ".py", """
def compute(a):
    if a == 1:
        return 1
    else:
        if a == 2:
            return 2
    return 0
""".lstrip())
        self.assertEqual(records["compute"].max_nesting_depth, 2)

    def test_an_elif_chain_still_measures_flat(self):
        records = _records("Python", ".py", """
def compute(a):
    if a == 1:
        return 1
    elif a == 2:
        return 2
    elif a == 3:
        return 3
    return 0
""".lstrip())
        self.assertEqual(records["compute"].max_nesting_depth, 1)

    def test_neither_form_changes_the_decision_count(self):
        """Only nesting was wrong; the decision counts already agreed."""
        nested = _records("Python", ".py", """
def compute(a):
    if a == 1:
        return 1
    else:
        if a == 2:
            return 2
    return 0
""".lstrip())["compute"]
        flat = _records("Python", ".py", """
def compute(a):
    if a == 1:
        return 1
    elif a == 2:
        return 2
    return 0
""".lstrip())["compute"]
        self.assertEqual(nested.decision_point_count, flat.decision_point_count)
        self.assertEqual(nested.cyclomatic_complexity, flat.cyclomatic_complexity)


# ---------------------------------------------------------------------------
# Reference-adapter defects. The references are corrected too: an adapter that
# is wrong makes ArchLens look wrong, which is the same loss of evidence.
# ---------------------------------------------------------------------------


def _python_reference(source: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "probe.py"
        path.write_text(source, encoding="utf-8", newline="")
        listing = drivers.write_listing([str(path)], Path(directory) / "l.txt")
        completed = subprocess.run(
            [str(drivers.PYREF), str(drivers.PYTHON_ADAPTER), "--list", str(listing)],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


def _node_reference(source: str, extension: str = ".ts") -> list[dict]:
    import os

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"probe{extension}"
        path.write_text(source, encoding="utf-8", newline="")
        listing = drivers.write_listing([str(path)], Path(directory) / "l.txt")
        completed = subprocess.run(
            [str(drivers.NODE), str(drivers.NODE_ADAPTER), "--list", str(listing)],
            capture_output=True, text=True, check=True, encoding="utf-8",
            env=dict(os.environ, NODE_PATH=str(drivers.NODE_MODULES)),
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


@unittest.skipUnless(
    drivers.PYREF.is_file(), "reference Python interpreter not provisioned"
)
class PythonReferenceAdapterFindingsTests(unittest.TestCase):
    def test_a_decorated_async_def_keeps_its_decorator_lines_in_the_span(self):
        """264 nloc disagreements on one subject, all this one cause.

        parso wraps `async def` in `async_funcdef`, so the `decorated` node is
        the GRANDparent and a parent-only check missed it. Contract section 8.1
        includes attached decorators in the span. The 80-line `@tool(...)`
        decorator on `list_transactions` is where it was largest.
        """
        callables = _python_reference(
            "@decorator(\n    name='x',\n)\nasync def f(a):\n    return a\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["start_line"], 1)
        self.assertEqual(record["nloc"], 5)

    def test_a_decorated_plain_def_is_unchanged(self):
        callables = _python_reference(
            "@decorator\ndef f(a):\n    return a\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["start_line"], 1)

    def test_a_bare_except_is_a_decision(self):
        """Contract section 7.1 counts each `except` clause, bare or not.

        parso gives `except X:` an `except_clause` node but leaves a bare
        `except:` as a keyword leaf of `try_stmt`, so a rule table keyed on
        `except_clause` silently misses it.
        """
        callables = _python_reference(
            "def f(a):\n    try:\n        return a\n    except:\n        return 0\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["decision_point_count"], 1)
        self.assertEqual(record["cyclomatic_complexity"], 2)

    def test_a_named_except_is_still_one_decision(self):
        callables = _python_reference(
            "def f(a):\n    try:\n        return a\n    except ValueError:\n        return 0\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["decision_point_count"], 1)

    def test_a_module_level_def_inside_an_if_is_in_the_population(self):
        """A block statement is not a scope.

        ArchLens's population rule is *no callable ancestor*, so a `def` guarded
        by a module-level `if` is a module function. The adapter's scope walk
        stopped at `if_stmt` and dropped two callables on `layer2-python-ralph`.
        """
        callables = _python_reference(
            "if FLAG:\n    def guarded(a):\n        return a\n"
        )
        self.assertEqual([item["qualified_name"] for item in callables], ["guarded"])

    def test_a_def_inside_a_function_is_still_excluded(self):
        """The guard: only NON-callable blocks became transparent."""
        callables = _python_reference(
            "def outer(a):\n    def inner(b):\n        return b\n    return inner\n"
        )
        self.assertEqual([item["qualified_name"] for item in callables], ["outer"])

    def test_a_def_inside_an_if_inside_a_class_body_is_still_excluded(self):
        """ArchLens counts only DIRECT class-body members; so must the adapter."""
        callables = _python_reference(
            "class C:\n    if FLAG:\n        def guarded(self):\n            return 1\n"
            "    def direct(self):\n        return 2\n"
        )
        self.assertEqual([item["qualified_name"] for item in callables], ["C.direct"])


@unittest.skipUnless(drivers.NODE.is_file(), "reference Node runtime not provisioned")
class NodeReferenceAdapterFindingsTests(unittest.TestCase):
    def test_expressions_inside_a_nested_statement_are_counted(self):
        """The adapter descended into nested statements but not their expressions.

        `walk()` counts operators only inside the CONDITIONS it visits, and only
        top-level statements were routed through the expression pass. A `??` or
        a ternary in a `return` inside an `if` or a `case` therefore counted
        nowhere. On `renderSummary` that lost 22 of 24 boolean operators.
        """
        callables = _node_reference(
            "export function f(d: Record<string, unknown>): string {\n"
            "  switch (String(d.k)) {\n"
            "    case 'one':\n"
            "      return String(d.name ?? '?') + (d.z ? 'y' : '');\n"
            "    default:\n"
            "      return '';\n"
            "  }\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 1)
        self.assertEqual(record["decision_point_count"], 2)

    def test_expressions_inside_an_if_block_are_counted(self):
        callables = _node_reference(
            "export function f(a: number, d: Record<string, unknown>): string {\n"
            "  if (a > 0) {\n"
            "    return String(d.name ?? '?');\n"
            "  }\n"
            "  return '';\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 1)
        self.assertEqual(record["decision_point_count"], 1)

    def test_a_top_level_expression_is_not_double_counted(self):
        """The guard: the pass that was missing must not now run twice."""
        callables = _node_reference(
            "export function f(d: Record<string, unknown>): string {\n"
            "  const p = (d.proposed || {}) as Record<string, unknown>;\n"
            "  return String(p.name ?? '?');\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 2)

    def test_the_iterable_of_a_for_of_is_scanned(self):
        """`for (const x of xs ?? [])` -- the iterable is an ordinary expression.

        The `for..of` branch walked only the body, so an operator in the
        iterable counted nowhere. Contract section 7.2 counts `??` occurrences
        anywhere in the callable.
        """
        callables = _node_reference(
            "export function f(xs: number[] | null): number {\n"
            "  let total = 0;\n"
            "  for (const x of xs ?? []) { total += x; }\n"
            "  return total;\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 1)
        self.assertEqual(record["cyclomatic_complexity"], 3)

    def test_the_header_clauses_of_a_c_style_for_are_scanned(self):
        callables = _node_reference(
            "export function f(a: number | null, b: number | null): number {\n"
            "  let total = 0;\n"
            "  for (let i = a ?? 0; i < 10; i += b || 1) { total += i; }\n"
            "  return total;\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 2)

    def test_a_nested_callable_still_contributes_nothing(self):
        """The guard: boundaries stay boundaries."""
        callables = _node_reference(
            "export function f(xs: number[]): string {\n"
            "  if (xs.length) {\n"
            "    const g = xs.map((x) => String(x ?? '?'));\n"
            "    return g.join('');\n"
            "  }\n"
            "  return '';\n"
            "}\n"
        )
        record = next(item for item in callables if item["qualified_name"] == "f")
        self.assertEqual(record["boolean_operator_count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
