"""C4-A1: regressions for the independent Go and Python complexity adapters.

These exercise the REFERENCE implementations, not ArchLens. They exist so an
adapter defect is caught as an adapter defect, before any real-repository
comparison turns it into a phantom ArchLens disagreement.

Both adapters run under the pinned reference environment
(configured with `ARCHLENS_DIFFVAL_REFS`). When that environment is absent the tests skip as
a **capability** statement, exactly as the Windows symlink case does -- a skip
here means "the reference toolchain is not provisioned", never "the adapter is
fine".
"""

from __future__ import annotations

import os

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
REFERENCE_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference"))
PYREF = REFERENCE_ROOT / "pyref/Scripts/python.exe"
GO = REFERENCE_ROOT / "go/bin/go.exe"
PY_ADAPTER = REPOSITORY / "validation/differential/reference/python/reference_complexity.py"
GO_ADAPTER = REPOSITORY / "validation/differential/reference/gosrc/reference_complexity.go"
CORPUS = REPOSITORY / "validation/differential/corpus/complexity"

#: Moved to 2.2.0 by the C4 Layer-C2 campaign: the decorated-`async def`
#: span, the bare `except:` decision and the module-level-`def`-in-a-block
#: population are all rule corrections, so the adapter version moves.
ADAPTER_VERSION = "2.2.0"

#: The seven metrics under validation. An expectation file may also carry a
#: per-construct `contributions` breakdown, which is documentation rather than a
#: metric, so the comparison names the metrics explicitly.
METRICS = (
    "nloc", "formal_parameter_count", "cyclomatic_complexity",
    "decision_point_count", "boolean_operator_count",
    "max_condition_operator_count", "max_nesting_depth",
)


def python_available() -> bool:
    return PYREF.is_file() and PY_ADAPTER.is_file()


def go_available() -> bool:
    return GO.is_file() and GO_ADAPTER.is_file()


def run_python_adapter(source: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.py"
        path.write_text(source, encoding="utf-8", newline="")
        completed = subprocess.run(
            [str(PYREF), str(PY_ADAPTER), str(path)],
            capture_output=True, text=True, check=True,
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


_GO_BINARY: Path | None = None


def go_binary() -> Path:
    """Build once per session; `go run` would treat an argument .go as source."""
    global _GO_BINARY
    if _GO_BINARY is None:
        target = Path(tempfile.mkdtemp()) / "reference_complexity.exe"
        subprocess.run(
            [str(GO), "build", "-o", str(target), str(GO_ADAPTER)],
            capture_output=True, text=True, check=True, cwd=REPOSITORY,
        )
        _GO_BINARY = target
    return _GO_BINARY


def run_go_adapter(source: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.go"
        path.write_text(source, encoding="utf-8", newline="")
        completed = subprocess.run(
            [str(go_binary()), str(path)],
            capture_output=True, text=True, check=True,
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


def by_name(records: list[dict]) -> dict[str, dict]:
    return {record["name"]: record for record in records}


@unittest.skipUnless(python_available(), "reference Python environment not provisioned")
class PythonAdapterTests(unittest.TestCase):
    def test_adapter_reports_its_own_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.py"
            path.write_text("def f():\n    return 1\n", encoding="utf-8")
            payload = json.loads(
                subprocess.run(
                    [str(PYREF), str(PY_ADAPTER), str(path)],
                    capture_output=True, text=True, check=True,
                ).stdout
            )
        self.assertEqual(payload["adapter_version"], ADAPTER_VERSION)
        self.assertIn("parso_version", payload["files"][0])

    def test_nested_callable_is_excluded_and_contributes_nothing(self):
        records = by_name(run_python_adapter(
            "def outer(xs):\n"
            "    def helper(v):\n"
            "        if v > 0 and v < 9:\n"
            "            return v\n"
            "        return 0\n"
            "    if xs:\n"
            "        return helper\n"
            "    return None\n"
        ))
        self.assertNotIn("helper", records, "a nested function is outside the population")
        outer = records["outer"]
        self.assertEqual(outer["decision_point_count"], 1)
        self.assertEqual(
            outer["boolean_operator_count"], 0,
            "the nested function's `and` must belong to no record",
        )

    def test_lambda_is_excluded(self):
        records = by_name(run_python_adapter(
            "def host(xs):\n"
            "    f = lambda v: v > 0 and v < 9\n"
            "    if xs:\n"
            "        return 1\n"
            "    return 0\n"
        ))
        self.assertEqual(records["host"]["boolean_operator_count"], 0)
        self.assertEqual(records["host"]["decision_point_count"], 1)

    def test_cyclomatic_identity_holds(self):
        for record in run_python_adapter(
            "def f(a, b, c):\n"
            "    if a and b:\n"
            "        return 1\n"
            "    for x in c:\n"
            "        if x or a:\n"
            "            return 2\n"
            "    return 3\n"
        ):
            self.assertEqual(
                record["cyclomatic_complexity"],
                1 + record["decision_point_count"] + record["boolean_operator_count"],
            )

    def test_boolop_carries_n_minus_one_operators(self):
        records = by_name(run_python_adapter(
            "def f(a, b, c, d):\n"
            "    if a and b and c and d:\n"
            "        return 1\n"
            "    return 0\n"
        ))
        self.assertEqual(
            records["f"]["boolean_operator_count"], 3,
            "a four-operand chain carries three operators",
        )
        self.assertEqual(records["f"]["max_condition_operator_count"], 3)

    def test_max_condition_differs_from_the_total(self):
        records = by_name(run_python_adapter(
            "def f(a, b, c):\n"
            "    if a and b:\n"
            "        return 1\n"
            "    return a and b or c\n"
        ))
        record = records["f"]
        self.assertEqual(record["boolean_operator_count"], 3)
        self.assertEqual(
            record["max_condition_operator_count"], 1,
            "only the `if` condition is a decision expression; the return is not",
        )

    def test_comprehension_for_and_if_clauses_each_count(self):
        records = by_name(run_python_adapter(
            "def f(xs):\n"
            "    return [x for x in xs if x > 0 if x < 9]\n"
        ))
        self.assertEqual(
            records["f"]["decision_point_count"], 3,
            "one `for` clause plus two `if` clauses",
        )

    def test_decorated_span_includes_the_decorator_lines(self):
        records = by_name(run_python_adapter(
            "@first\n"
            "@second\n"
            "def f(a):\n"
            "    return a\n"
        ))
        record = records["f"]
        self.assertEqual(record["start_line"], 1, "the span begins at the first decorator")
        self.assertEqual(record["end_line"], 4)
        self.assertEqual(record["nloc"], 4)

    def test_docstring_is_code_and_comments_are_not(self):
        records = by_name(run_python_adapter(
            'def f(a):\n'
            '    """Doc."""\n'
            '    # comment\n'
            '\n'
            '    return a  # trailing\n'
        ))
        self.assertEqual(
            records["f"]["nloc"], 3,
            "signature, docstring and the return; comment-only and blank are not code",
        )

    def test_a_hash_inside_a_string_is_not_a_comment(self):
        records = by_name(run_python_adapter(
            'def f():\n'
            '    value = "# not a comment"\n'
            '    return value\n'
        ))
        self.assertEqual(records["f"]["nloc"], 3)

    def test_match_is_reported_not_evaluable_never_zero(self):
        """parso 0.8.7 has no `match` grammar.

        Reporting 0 decisions for a construct the parser could not read would
        manufacture agreement, so the adapter must refuse instead.
        """
        records = by_name(run_python_adapter(
            "def f(v):\n"
            "    match v:\n"
            "        case 1:\n"
            "            return 1\n"
            "        case _:\n"
            "            return 2\n"
        ))
        record = records["f"]
        self.assertEqual(
            record["not_evaluable_reason"], "parso_grammar_cannot_parse_construct"
        )
        self.assertIsNone(record["decision_point_count"])
        self.assertIsNone(record["cyclomatic_complexity"])
        self.assertIsNotNone(
            record["nloc"], "NLOC is read from the source and still survives"
        )

    def test_async_functions_are_in_the_population(self):
        records = by_name(run_python_adapter(
            "async def f(xs):\n"
            "    async for x in xs:\n"
            "        if x:\n"
            "            return x\n"
            "    return None\n"
        ))
        self.assertIn(
            "f", records,
            "parso wraps `async def` in an async_stmt; a scope walk that does "
            "not step through it drops every async function",
        )
        self.assertEqual(records["f"]["decision_point_count"], 2)

    def test_self_counts_as_a_declared_parameter(self):
        records = by_name(run_python_adapter(
            "class C:\n"
            "    def method(self, a):\n"
            "        return a\n"
        ))
        self.assertEqual(records["method"]["formal_parameter_count"], 2)

    def test_every_parameter_form_counts(self):
        records = by_name(run_python_adapter(
            "def f(a, /, b, *args, c=1, **kw):\n"
            "    return a\n"
        ))
        self.assertEqual(records["f"]["formal_parameter_count"], 5)

    def test_the_corpus_agrees_metric_for_metric(self):
        """The adapter versus the HAND-AUTHORED expectations, never ArchLens."""
        expectations = json.loads(
            (CORPUS / "python/expectations.json").read_text(encoding="utf-8")
        )
        records = by_name(
            run_python_adapter((CORPUS / "python/constructs.py").read_text(encoding="utf-8"))
        )
        expected_by_name = {e["name"]: e for e in expectations["callables"]}
        self.assertEqual(len(records), expectations["file_level"]["callable_count"])
        for name, entry in expected_by_name.items():
            with self.subTest(callable=name):
                record = records[name]
                if record.get("not_evaluable_reason"):
                    continue
                for metric in METRICS:
                    self.assertEqual(
                        record[metric], entry["expected"][metric], f"{name}.{metric}"
                    )


@unittest.skipUnless(go_available(), "reference Go toolchain not provisioned")
class GoAdapterTests(unittest.TestCase):
    def test_func_literal_is_excluded_and_contributes_nothing(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Host(xs []int) int {\n"
            "\tf := func(v int) int {\n"
            "\t\tif v > 0 && v < 9 {\n"
            "\t\t\treturn v\n"
            "\t\t}\n"
            "\t\treturn 0\n"
            "\t}\n"
            "\tif len(xs) > 0 {\n"
            "\t\treturn f(1)\n"
            "\t}\n"
            "\treturn 0\n"
            "}\n"
        ))
        record = records["Host"]
        self.assertEqual(record["decision_point_count"], 1)
        self.assertEqual(
            record["boolean_operator_count"], 0,
            "the literal's && must belong to no record",
        )
        self.assertEqual(record["max_nesting_depth"], 1)

    def test_grouped_and_unnamed_parameters(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Grouped(a, b int, c string) int { return a }\n"
            "func Unnamed(int, string) int { return 0 }\n"
            "func Variadic(prefix string, rest ...int) int { return 0 }\n"
        ))
        self.assertEqual(records["Grouped"]["formal_parameter_count"], 3)
        self.assertEqual(records["Unnamed"]["formal_parameter_count"], 2)
        self.assertEqual(records["Variadic"]["formal_parameter_count"], 2)

    def test_receiver_is_excluded_from_the_parameter_count(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "type S struct{}\n"
            "func (s *S) M(a int) int { return a }\n"
        ))
        self.assertEqual(records["M"]["formal_parameter_count"], 1)
        self.assertEqual(records["M"]["callable_kind"], "receiver_method")
        self.assertEqual(records["M"]["qualified_name"], "S.M")

    def test_select_default_arm_adds_nothing(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Sel(ch chan int) int {\n"
            "\tselect {\n"
            "\tcase v := <-ch:\n"
            "\t\treturn v\n"
            "\tdefault:\n"
            "\t\treturn 0\n"
            "\t}\n"
            "}\n"
        ))
        record = records["Sel"]
        self.assertEqual(record["decision_point_count"], 1)
        self.assertEqual(record["max_nesting_depth"], 2)

    def test_type_switch_cases_count_and_default_does_not(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func TS(v interface{}) string {\n"
            "\tswitch v.(type) {\n"
            "\tcase int:\n"
            "\t\treturn \"i\"\n"
            "\tcase string:\n"
            "\t\treturn \"s\"\n"
            "\tdefault:\n"
            "\t\treturn \"o\"\n"
            "\t}\n"
            "}\n"
        ))
        self.assertEqual(records["TS"]["decision_point_count"], 2)

    def test_a_multi_value_case_is_one_arm(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Sw(c int) int {\n"
            "\tswitch c {\n"
            "\tcase 1:\n"
            "\t\treturn 1\n"
            "\tcase 2, 3:\n"
            "\t\treturn 2\n"
            "\t}\n"
            "\treturn 0\n"
            "}\n"
        ))
        self.assertEqual(records["Sw"]["decision_point_count"], 2)

    def test_else_if_chain_measures_flat(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Chain(v int) int {\n"
            "\tif v < 0 {\n"
            "\t\treturn -1\n"
            "\t} else if v == 0 {\n"
            "\t\treturn 0\n"
            "\t} else {\n"
            "\t\treturn 1\n"
            "\t}\n"
            "}\n"
        ))
        record = records["Chain"]
        self.assertEqual(record["decision_point_count"], 2)
        self.assertEqual(record["max_nesting_depth"], 1)

    def test_bare_block_opens_no_nesting(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Bare(a int) int {\n"
            "\t{\n"
            "\t\ta++\n"
            "\t}\n"
            "\treturn a\n"
            "}\n"
        ))
        self.assertEqual(records["Bare"]["max_nesting_depth"], 0)

    def test_max_condition_differs_from_the_total(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func F(a, b, c bool) bool {\n"
            "\tif a && b {\n"
            "\t\treturn true\n"
            "\t}\n"
            "\treturn a && b || c\n"
            "}\n"
        ))
        record = records["F"]
        self.assertEqual(record["boolean_operator_count"], 3)
        self.assertEqual(record["max_condition_operator_count"], 1)

    def test_cyclomatic_identity_holds(self):
        for record in run_go_adapter(
            "package p\n"
            "func F(xs []int, a bool) int {\n"
            "\tfor _, x := range xs {\n"
            "\t\tif x > 0 && a {\n"
            "\t\t\treturn x\n"
            "\t\t}\n"
            "\t}\n"
            "\treturn 0\n"
            "}\n"
        ):
            self.assertEqual(
                record["cyclomatic_complexity"],
                1 + record["decision_point_count"] + record["boolean_operator_count"],
            )

    def test_bodyless_declaration_is_excluded(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func Stub(a int) int\n"
            "func Real(a int) int { return a }\n"
        ))
        self.assertNotIn("Stub", records)
        self.assertIn("Real", records)

    def test_comment_lines_are_not_code(self):
        records = by_name(run_go_adapter(
            "package p\n"
            "func F(a int) int {\n"
            "\t// a comment\n"
            "\n"
            "\tvalue := a // trailing\n"
            "\treturn value\n"
            "}\n"
        ))
        self.assertEqual(records["F"]["nloc"], 4)

    def test_the_corpus_agrees_metric_for_metric(self):
        expectations = json.loads(
            (CORPUS / "go/expectations.json").read_text(encoding="utf-8")
        )
        records = by_name(
            run_go_adapter((CORPUS / "go/constructs.go").read_text(encoding="utf-8"))
        )
        self.assertEqual(len(records), expectations["file_level"]["callable_count"])
        for entry in expectations["callables"]:
            with self.subTest(callable=entry["name"]):
                record = records[entry["name"]]
                for metric in METRICS:
                    self.assertEqual(
                        record[metric], entry["expected"][metric],
                        f"{entry['name']}.{metric}",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
