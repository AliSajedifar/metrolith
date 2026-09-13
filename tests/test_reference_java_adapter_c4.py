"""C4-A2: regressions for the independent Java complexity reference adapter.

Exercises the javac Compiler Tree API adapter, not ArchLens, so an adapter
defect is caught as an adapter defect before any real comparison turns it into a
phantom ArchLens disagreement.

Skips as a **capability** statement when the pinned JDK is absent: a skip means
the toolchain is not provisioned, never that the adapter is fine.
"""

from __future__ import annotations

import os

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
JDK = Path(os.environ.get("METROLITH_REFERENCE_JDK", ".metrolith-reference/jdk/bin"))
JAVAC = JDK / "javac.exe"
JAVA = JDK / "java.exe"
ADAPTER = REPOSITORY / "validation/differential/reference/java/ReferenceComplexity.java"
CORPUS = REPOSITORY / "validation/differential/corpus/complexity/java"

ADAPTER_VERSION = "2.0.0"
METRICS = (
    "nloc", "formal_parameter_count", "cyclomatic_complexity",
    "decision_point_count", "boolean_operator_count",
    "max_condition_operator_count", "max_nesting_depth",
)

_CLASSES: Path | None = None


def available() -> bool:
    return JAVAC.is_file() and JAVA.is_file() and ADAPTER.is_file()


def classes() -> Path:
    global _CLASSES
    if _CLASSES is None:
        target = Path(tempfile.mkdtemp())
        subprocess.run(
            [str(JAVAC), "-d", str(target), str(ADAPTER)],
            capture_output=True, text=True, check=True,
        )
        _CLASSES = target
    return _CLASSES


def run_adapter(source: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "Sample.java"
        # newline="" preserves the bytes: the bytes measured must be the bytes
        # analyzed, and Windows translation would otherwise alter the input.
        path.write_text(source, encoding="utf-8", newline="")
        listing = Path(directory) / "files.txt"
        listing.write_text(str(path) + "\n", encoding="utf-8", newline="")
        completed = subprocess.run(
            [str(JAVA), "-cp", str(classes()), "ReferenceComplexity", str(listing)],
            capture_output=True, text=True, check=True,
        )
    return json.loads(completed.stdout)["files"][0]["callables"]


def by_name(records: list[dict]) -> dict[str, dict]:
    return {record["name"]: record for record in records}


@unittest.skipUnless(available(), "pinned reference JDK not provisioned")
class JavaAdapterTests(unittest.TestCase):

    def test_switch_expression_arms_are_decisions(self):
        """The arrow form reaches the traversal through an EXPRESSION.

        Routing only `Tree.Kind.SWITCH` counted zero decisions for every switch
        expression. That was a real adapter defect, and this pins the fix.
        """
        records = by_name(run_adapter(
            "class S {\n"
            "  int arrow(int c) {\n"
            "    return switch (c) { case 1 -> 1; case 2, 3 -> 2; default -> 0; };\n"
            "  }\n"
            "}\n"
        ))
        record = records["arrow"]
        self.assertEqual(
            record["decision_point_count"], 2,
            "two arms; `case 2, 3` is ONE arm and `default` adds nothing",
        )
        self.assertEqual(record["cyclomatic_complexity"], 3)

    def test_colon_and_arrow_forms_agree_on_arm_counting(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int colon(int c) {\n"
            "    switch (c) { case 1: return 1; case 2: return 2; default: return 0; }\n"
            "  }\n"
            "  int arrow(int c) {\n"
            "    return switch (c) { case 1 -> 1; case 2 -> 2; default -> 0; };\n"
            "  }\n"
            "}\n"
        ))
        self.assertEqual(
            records["colon"]["decision_point_count"],
            records["arrow"]["decision_point_count"],
        )

    def test_lambda_control_flow_does_not_leak(self):
        records = by_name(run_adapter(
            "import java.util.List;\n"
            "class S {\n"
            "  int host(List<Integer> xs) {\n"
            "    xs.forEach(v -> { if (v > 0 && v < 9) { count(v); } });\n"
            "    return 1;\n"
            "  }\n"
            "  void count(int v) {}\n"
            "}\n"
        ))
        record = records["host"]
        self.assertEqual(record["decision_point_count"], 0)
        self.assertEqual(record["boolean_operator_count"], 0)
        self.assertEqual(record["max_nesting_depth"], 0)

    def test_anonymous_class_methods_stay_outside_the_population(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int host() {\n"
            "    Runnable r = new Runnable() { public void run() { if (true) { } } };\n"
            "    return 1;\n"
            "  }\n"
            "}\n"
        ))
        self.assertNotIn("run", records)
        self.assertEqual(records["host"]["decision_point_count"], 0)

    def test_synchronized_nests_and_a_bare_block_does_not(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int f(int a) { synchronized (this) { a++; } return a; }\n"
            "  int g(int a) { { a++; } return a; }\n"
            "}\n"
        ))
        self.assertEqual(records["f"]["max_nesting_depth"], 1)
        self.assertEqual(records["f"]["decision_point_count"], 0)
        self.assertEqual(records["g"]["max_nesting_depth"], 0)

    def test_receiver_excluded_and_varargs_is_one_parameter(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int f(S S.this, int a, int... rest) { return a; }\n"
            "}\n"
        ))
        self.assertEqual(
            records["f"]["formal_parameter_count"], 2,
            "javac keeps the receiver out of getParameters(); varargs is one",
        )

    def test_local_enum_and_interface_default_methods_are_included(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int host(int f) { class Local { int inner(int v) { return v; } } return f; }\n"
            "  interface Api { default int d(int x) { return x; } int abs(int y); }\n"
            "  enum K { A; int em() { return 1; } }\n"
            "}\n"
        ))
        for name in ("host", "inner", "d", "em"):
            self.assertIn(name, records, name)
        self.assertNotIn("abs", records, "a body-less declaration is excluded")

    def test_constructors_and_initializers_are_excluded(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  S() { }\n"
            "  static { }\n"
            "  int f() { return 1; }\n"
            "}\n"
        ))
        self.assertEqual(sorted(records), ["f"])

    def test_guarded_case_is_not_evaluable_under_the_pinned_jdk(self):
        """JDK 17 javac rejects `case P when g`.

        Reporting a number for a construct the compiler could not parse would
        manufacture agreement, so the adapter refuses instead.
        """
        records = by_name(run_adapter(
            "class S {\n"
            "  int g(Object o) {\n"
            "    return switch (o) { case Integer i when i > 0 -> 1; default -> 0; };\n"
            "  }\n"
            "}\n"
        ))
        record = records["g"]
        self.assertEqual(record["not_evaluable_reason"], "javac_parse_error_in_span")
        self.assertIsNone(record["cyclomatic_complexity"])
        self.assertIsNotNone(
            record["nloc"], "NLOC is read from the source and still survives"
        )

    def test_else_if_measures_flat(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  int f(int v) {\n"
            "    if (v < 0) { return -1; } else if (v == 0) { return 0; } else { return 1; }\n"
            "  }\n"
            "}\n"
        ))
        self.assertEqual(records["f"]["decision_point_count"], 2)
        self.assertEqual(records["f"]["max_nesting_depth"], 1)

    def test_every_loop_form_and_catch_and_ternary(self):
        records = by_name(run_adapter(
            "import java.util.List;\n"
            "class S {\n"
            "  int f(List<String> xs, int a) {\n"
            "    for (String s : xs) { a++; }\n"
            "    for (int i = 0; i < 3; i++) { a++; }\n"
            "    while (a > 0) { a--; }\n"
            "    do { a++; } while (a < 3);\n"
            "    try { a++; } catch (RuntimeException e) { a--; } finally { a = 0; }\n"
            "    return a > 0 ? 1 : 2;\n"
            "  }\n"
            "}\n"
        ))
        self.assertEqual(
            records["f"]["decision_point_count"], 6,
            "4 loops + 1 catch + 1 ternary; finally adds nothing",
        )

    def test_cyclomatic_identity_holds(self):
        for record in run_adapter(
            "class S {\n"
            "  int f(int a, boolean b) {\n"
            "    if (a > 0 && b) { return 1; }\n"
            "    for (int i = 0; i < a; i++) { if (b) { return 2; } }\n"
            "    return a > 0 ? 3 : 4;\n"
            "  }\n"
            "}\n"
        ):
            if record["not_evaluable_reason"]:
                continue
            self.assertEqual(
                record["cyclomatic_complexity"],
                1 + record["decision_point_count"] + record["boolean_operator_count"],
            )

    def test_max_condition_differs_from_the_total(self):
        records = by_name(run_adapter(
            "class S {\n"
            "  boolean f(boolean a, boolean b, boolean c) {\n"
            "    if (a && b) { return true; }\n"
            "    return a && b || c;\n"
            "  }\n"
            "}\n"
        ))
        self.assertEqual(records["f"]["boolean_operator_count"], 3)
        self.assertEqual(
            records["f"]["max_condition_operator_count"], 1,
            "only the `if` condition is a decision expression",
        )

    def test_the_corpus_agrees_metric_for_metric(self):
        """The adapter versus the HAND-AUTHORED expectations, never ArchLens."""
        from validation.differential.callable_matching import match_callables

        expectations = json.loads(
            (CORPUS / "expectations.json").read_text(encoding="utf-8")
        )
        records = run_adapter(
            (CORPUS / "Constructs.java").read_text(encoding="utf-8")
        )
        self.assertEqual(len(records), expectations["file_level"]["callable_count"])

        path = "Constructs.java"
        left = [dict(item, relative_path=path) for item in expectations["callables"]]
        right = [
            dict(item, relative_path=path, signature_discriminator=None)
            for item in records
        ]
        result = match_callables(left, right)
        self.assertEqual(result.summary()["ambiguous"], 0)
        self.assertEqual(result.summary()["unmatched_archlens"], 0)
        self.assertEqual(result.summary()["unmatched_reference"], 0)

        expected_index = {
            (item["qualified_name"], item["start_line"]): item
            for item in expectations["callables"]
        }
        record_index = {
            (item["qualified_name"], item["start_line"]): item for item in records
        }
        not_evaluable = 0
        for left_key, right_key, _tier in result.matched:
            entry = expected_index[(left_key.qualified_name, left_key.start_line)]
            record = record_index[(right_key.qualified_name, right_key.start_line)]
            if record["not_evaluable_reason"]:
                not_evaluable += 1
                continue
            with self.subTest(callable=entry["qualified_name"]):
                for metric in METRICS:
                    self.assertEqual(
                        record[metric], entry["expected"][metric],
                        f"{entry['qualified_name']}.{metric}",
                    )
        self.assertEqual(
            not_evaluable, 1,
            "exactly the guarded-case callable is unmeasurable under JDK 17",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
