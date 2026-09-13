"""C2 synthetic per-construct corpus for Complexity Contract 1.0.0.

Every case names the individual contributions it pins, not just a total: a
total-only expectation lets two offsetting rule errors cancel. Where a construct
must contribute NOTHING -- `else`, `default`, a labelled break, anything behind a
callable boundary -- that zero is asserted explicitly, because a silently absent
increment and a correctly absent one look identical in a total.

The negative cases at the end are the ten failure modes the C2 brief names.
"""

from __future__ import annotations

import ast
import unittest

from modules.callable_analysis import analyze_callables
from modules.core_metrics import (
    ParserRegistry,
    _python_comment_masked,
    _tree_comment_masked,
)

REGISTRY = ParserRegistry()


def measure(language: str, extension: str, source: bytes) -> dict[str, object]:
    """Analyze one snippet and return records keyed by qualified name."""
    if language == "Python":
        text = source.decode("utf-8")
        root = ast.parse(text)
        raw, masked = text, _python_comment_masked(text)[0]
    else:
        root = REGISTRY.get(language, extension).parse(source).root_node
        raw = source.decode("utf-8", "replace")
        masked = _tree_comment_masked(source, root).decode("utf-8", "replace")
    result = analyze_callables(
        language, root, source, f"case{extension}", raw_text=raw, masked_text=masked
    )
    return {record.qualified_name: record for record in result.records}


class MetricCaseMixin:
    """Assert a record field-by-field, reporting every difference at once."""

    def assertMetrics(self, record, **expected):
        actual = {name: getattr(record, name) for name in expected}
        self.assertEqual(
            actual, expected,
            f"{record.qualified_name}: expected {expected}, got {actual}",
        )

    def assertIdentity(self, record):
        """Contract section 7.2, on every complete row."""
        self.assertEqual(
            record.cyclomatic_complexity,
            1 + record.decision_point_count + record.boolean_operator_count,
        )


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

GO = b"""
package p

func Trivial() int { return 1 }

func Branches(a int, b int) int {
	if a > 0 {
		if b > 0 {
			return 1
		}
	} else {
		return 2
	}
	return 0
}

func Switching(c int) int {
	switch c {
	case 1:
		return 1
	case 2, 3:
		return 2
	default:
		return 0
	}
}

func Selecting(ch chan int) int {
	select {
	case v := <-ch:
		return v
	default:
		return 0
	}
}

func Conditions(a bool, b bool, c bool) bool {
	if a && b || c {
		return true
	}
	return a && b
}

func Literal(xs []int) int {
	if len(xs) == 0 {
		return 0
	}
	f := func(v int) int {
		if v > 0 && v < 9 {
			for i := 0; i < v; i++ {
			}
		}
		return v
	}
	return f(1)
}

func Params(a, b int, c string, rest ...int) int { return a }

func Labelled(rows [][]int) int {
outer:
	for _, row := range rows {
		for _, v := range row {
			if v < 0 {
				break outer
			}
		}
	}
	return 0
}
"""


class GoMetricTests(MetricCaseMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = measure("Go", ".go", GO)

    def test_trivial_is_exactly_one(self):
        self.assertMetrics(
            self.records["Trivial"],
            cyclomatic_complexity=1, decision_point_count=0,
            boolean_operator_count=0, max_nesting_depth=0,
            formal_parameter_count=0,
        )

    def test_nested_branches_and_a_bare_else(self):
        """Two `if`s contribute 2; the `else` contributes 0 but DOES nest."""
        self.assertMetrics(
            self.records["Branches"],
            decision_point_count=2, boolean_operator_count=0,
            cyclomatic_complexity=3, max_nesting_depth=2,
            formal_parameter_count=2,
        )

    def test_switch_counts_arms_not_default(self):
        """Two case arms (`case 2, 3` is ONE arm); `default` adds nothing."""
        self.assertMetrics(
            self.records["Switching"],
            decision_point_count=2, cyclomatic_complexity=3,
            max_nesting_depth=2,
        )

    def test_select_default_arm_adds_nothing(self):
        self.assertMetrics(
            self.records["Selecting"],
            decision_point_count=1, cyclomatic_complexity=2,
            max_nesting_depth=2,
        )

    def test_boolean_total_differs_from_max_condition(self):
        """`a && b || c` in the condition is 2; the trailing `a && b` raises the
        total to 3 but not the per-condition maximum."""
        self.assertMetrics(
            self.records["Conditions"],
            decision_point_count=1, boolean_operator_count=3,
            max_condition_operator_count=2, cyclomatic_complexity=5,
        )

    def test_a_func_literal_contributes_nothing_structural(self):
        record = self.records["Literal"]
        self.assertMetrics(
            record,
            decision_point_count=1, boolean_operator_count=0,
            max_condition_operator_count=0, cyclomatic_complexity=2,
            max_nesting_depth=1,
        )
        self.assertGreater(
            record.nloc, 8,
            "the literal's LINES stay in the span even though its control flow "
            "belongs to no record",
        )

    def test_grouped_and_variadic_parameters(self):
        """`(a, b int, c string, rest ...int)` is 4 declared names."""
        self.assertMetrics(self.records["Params"], formal_parameter_count=4)

    def test_labelled_break_adds_no_decision(self):
        self.assertMetrics(
            self.records["Labelled"],
            decision_point_count=3, cyclomatic_complexity=4,
            max_nesting_depth=3,
        )

    def test_identity_holds_for_every_record(self):
        for record in self.records.values():
            with self.subTest(record.qualified_name):
                self.assertIdentity(record)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

JAVA = b"""
class S {
  int trivial() { return 1; }

  int colonSwitch(int c) {
    switch (c) {
      case 1: case 2: return 1;
      default: return 0;
    }
  }

  int arrowSwitch(int c) {
    return switch (c) { case 1 -> 1; case 2, 3 -> 2; default -> 0; };
  }

  int loopsAndCatch(java.util.List<String> xs, int a) {
    for (String s : xs) { if (a > 0 && a < 9) { a++; } }
    while (a > 0) { a--; }
    do { a++; } while (a < 3);
    try { a++; } catch (RuntimeException e) { a--; } finally { a = 0; }
    return a > 0 ? 1 : 2;
  }

  int receiver(S S.this, int a, int... rest) { return a; }

  int lambdaHost(java.util.List<Integer> xs) {
    xs.forEach(v -> { if (v > 0 && v < 9) { count(v); } });
    return 1;
  }

  int anonymousHost() {
    Runnable r = new Runnable() { public void run() { if (true) { } } };
    return 1;
  }

  int bareBlock(int a) {
    { a++; }
    synchronized (this) { a++; }
    return a;
  }

  void count(int v) {}
}
"""


class JavaMetricTests(MetricCaseMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = measure("Java", ".java", JAVA)

    def test_trivial_is_exactly_one(self):
        self.assertMetrics(
            self.records["S.trivial"], cyclomatic_complexity=1,
            decision_point_count=0, max_nesting_depth=0,
        )

    def test_colon_switch_counts_each_case_label(self):
        """Fallthrough `case 1: case 2:` is TWO arms; `default` is zero."""
        self.assertMetrics(
            self.records["S.colonSwitch"],
            decision_point_count=2, cyclomatic_complexity=3,
            max_nesting_depth=2,
        )

    def test_arrow_switch_counts_each_rule(self):
        self.assertMetrics(
            self.records["S.arrowSwitch"],
            decision_point_count=2, cyclomatic_complexity=3,
        )

    def test_loops_catch_and_ternary(self):
        """for + if + while + do + catch + ternary = 6; `finally` adds none."""
        self.assertMetrics(
            self.records["S.loopsAndCatch"],
            decision_point_count=6, boolean_operator_count=1,
            max_condition_operator_count=1, cyclomatic_complexity=8,
            max_nesting_depth=2,
        )

    def test_receiver_is_not_a_parameter_and_varargs_is_one(self):
        self.assertMetrics(self.records["S.receiver"], formal_parameter_count=2)

    def test_a_lambda_contributes_nothing_structural(self):
        self.assertMetrics(
            self.records["S.lambdaHost"],
            decision_point_count=0, boolean_operator_count=0,
            cyclomatic_complexity=1, max_nesting_depth=0,
        )

    def test_an_anonymous_class_body_contributes_nothing(self):
        self.assertMetrics(
            self.records["S.anonymousHost"],
            decision_point_count=0, cyclomatic_complexity=1,
            max_nesting_depth=0,
        )

    def test_synchronized_nests_but_adds_no_decision(self):
        """Nesting is STRUCTURAL nesting.

        `synchronized` joins Python `with`, `try`/`finally` and case bodies: it
        opens a level and contributes zero decisions. The bare block in the same
        method still opens nothing, so a max depth of 1 proves both halves --
        the synchronized body nested, the bare block did not.
        """
        self.assertMetrics(
            self.records["S.bareBlock"],
            decision_point_count=0, cyclomatic_complexity=1,
            max_nesting_depth=1,
        )

    def test_a_bare_block_alone_opens_no_nesting(self):
        source = b"class S { int f(int a) { { a++; } return a; } }"
        self.assertMetrics(
            measure("Java", ".java", source)["S.f"],
            decision_point_count=0, max_nesting_depth=0,
        )

    def test_synchronized_alone_opens_exactly_one_level(self):
        source = b"class S { int f(int a) { synchronized (this) { a++; } return a; } }"
        self.assertMetrics(
            measure("Java", ".java", source)["S.f"],
            decision_point_count=0, cyclomatic_complexity=1,
            max_nesting_depth=1,
        )

    def test_identity_holds_for_every_record(self):
        for record in self.records.values():
            with self.subTest(record.qualified_name):
                self.assertIdentity(record)


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

JAVASCRIPT = b"""
function trivial() { return 1; }

function switching(a) {
  switch (a) { case 1: return 1; case 2: return 2; default: return 0; }
}

function loops(xs, a) {
  for (const x of xs) { if (a) { a--; } }
  for (const k in xs) { }
  while (a) { a--; }
  do { a++; } while (a < 3);
  try { a++; } catch (e) { a--; } finally { a = 0; }
  return a ? 1 : 2;
}

function nullish(a, b, c) {
  const n = a ?? b;
  let z = a;
  z ??= 1;
  z ||= 2;
  z &&= 3;
  if (a && b || c) { return 1; }
  return 0;
}

function optional(a) { return a?.b?.c; }

function callbackHost(xs) {
  xs.map(v => v && v.z);
  xs.forEach(function (v) { if (v) { } });
  if (xs) { return 1; }
  return 0;
}

function destructuring({a, b}, [c], ...rest) { return a; }

const bound = (x) => x && x.y;
"""


class JavaScriptMetricTests(MetricCaseMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = measure("JavaScript", ".js", JAVASCRIPT)

    def test_trivial_is_exactly_one(self):
        self.assertMetrics(
            self.records["trivial"], cyclomatic_complexity=1,
            decision_point_count=0,
        )

    def test_switch_counts_cases_not_default(self):
        self.assertMetrics(
            self.records["switching"],
            decision_point_count=2, cyclomatic_complexity=3,
            max_nesting_depth=2,
        )

    def test_every_loop_form_and_catch(self):
        """for-of + if + for-in + while + do + catch + ternary = 7."""
        self.assertMetrics(
            self.records["loops"],
            decision_point_count=7, cyclomatic_complexity=8,
            max_nesting_depth=2,
        )

    def test_nullish_and_logical_assignments_count(self):
        """`a ?? b`, `??=`, `||=`, `&&=` and `a && b || c` are 6 operators in
        total, while the single decision expression holds only 2."""
        self.assertMetrics(
            self.records["nullish"],
            decision_point_count=1, boolean_operator_count=6,
            max_condition_operator_count=2, cyclomatic_complexity=8,
        )

    def test_optional_chaining_is_not_a_decision(self):
        self.assertMetrics(
            self.records["optional"], decision_point_count=0,
            boolean_operator_count=0, cyclomatic_complexity=1,
        )

    def test_callbacks_contribute_nothing_structural(self):
        self.assertMetrics(
            self.records["callbackHost"],
            decision_point_count=1, boolean_operator_count=0,
            cyclomatic_complexity=2, max_nesting_depth=1,
        )

    def test_destructuring_and_rest_each_count_once(self):
        self.assertMetrics(
            self.records["destructuring"], formal_parameter_count=3
        )

    def test_a_bound_arrow_with_an_expression_body_is_measured(self):
        self.assertMetrics(
            self.records["bound"], boolean_operator_count=1,
            cyclomatic_complexity=2, formal_parameter_count=1,
        )

    def test_identity_holds_for_every_record(self):
        for record in self.records.values():
            with self.subTest(record.qualified_name):
                self.assertIdentity(record)


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

TYPESCRIPT = b"""
class W {
  withThis(this: W, a: number, {b, c}: any, ...rest: number[]): number {
    if (a > 0 && a < 9) { return a; }
    return 0;
  }
  plain(a: number, b: string): number { return a; }
}

export function guarded(a?: number, b: number = 1): number {
  return a ?? b;
}
"""


class TypeScriptMetricTests(MetricCaseMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = measure("TypeScript", ".ts", TYPESCRIPT)

    def test_this_parameter_is_excluded_and_flagged(self):
        record = self.records["W.withThis"]
        self.assertMetrics(record, formal_parameter_count=3)
        self.assertTrue(
            record.declares_typescript_this_parameter,
            "the exclusion must be visible, or the published number is "
            "unexplainable",
        )

    def test_a_method_without_this_sets_no_flag(self):
        record = self.records["W.plain"]
        self.assertMetrics(record, formal_parameter_count=2)
        self.assertIsNone(record.declares_typescript_this_parameter)

    def test_nullish_counts_in_typescript_too(self):
        self.assertMetrics(
            self.records["guarded"], boolean_operator_count=1,
            cyclomatic_complexity=2, formal_parameter_count=2,
        )

    def test_identity_holds_for_every_record(self):
        for record in self.records.values():
            with self.subTest(record.qualified_name):
                self.assertIdentity(record)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

PYTHON = b'''
def trivial():
    return 1

def comprehensions(xs, ys):
    a = [x for x in xs if x > 0 if x < 9]
    b = {k: v for k, v in ys}
    return a, b

def ternary(a, b):
    return 1 if a and b else 2

def matcher(v):
    match v:
        case 1:
            return 1
        case int() as n if n > 0 and n < 9:
            return 2
        case _:
            return 3

def elif_chain(a, b):
    if a:
        return 1
    elif b:
        return 2
    else:
        return 3

def deep(a, b, c):
    try:
        with open("x") as fh:
            if a and b or c:
                for i in range(3):
                    pass
    except ValueError:
        pass
    finally:
        pass
    return 1

def lambda_host(xs):
    f = lambda v: v > 0 and v < 9
    if xs:
        return 1
    return 0

def params(a, /, b, *args, c=1, **kw):
    return a

class Holder:
    def method(self, a):
        if a:
            return 1
        return 0
'''


class PythonMetricTests(MetricCaseMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = measure("Python", ".py", PYTHON)

    def test_trivial_is_exactly_one(self):
        self.assertMetrics(
            self.records["trivial"], cyclomatic_complexity=1,
            decision_point_count=0, max_nesting_depth=0,
        )

    def test_comprehension_for_and_if_clauses_each_count(self):
        """One `for` plus two `if`s, then a second comprehension's `for`."""
        self.assertMetrics(
            self.records["comprehensions"],
            decision_point_count=4, boolean_operator_count=0,
            cyclomatic_complexity=5,
        )

    def test_conditional_expression_counts(self):
        self.assertMetrics(
            self.records["ternary"], decision_point_count=1,
            boolean_operator_count=1, max_condition_operator_count=1,
            cyclomatic_complexity=3,
        )

    def test_match_arms_and_guards_count_but_wildcard_does_not(self):
        self.assertMetrics(
            self.records["matcher"],
            decision_point_count=3, boolean_operator_count=1,
            cyclomatic_complexity=5, max_nesting_depth=2,
        )

    def test_elif_reads_flat_and_measures_flat(self):
        self.assertMetrics(
            self.records["elif_chain"],
            decision_point_count=2, cyclomatic_complexity=3,
            max_nesting_depth=1,
        )

    def test_try_with_and_nested_loops(self):
        """try body, with, if and for each open a level: depth 4."""
        self.assertMetrics(
            self.records["deep"],
            decision_point_count=3, boolean_operator_count=2,
            max_condition_operator_count=2, cyclomatic_complexity=6,
            max_nesting_depth=4,
        )

    def test_a_lambda_contributes_nothing_structural(self):
        self.assertMetrics(
            self.records["lambda_host"],
            decision_point_count=1, boolean_operator_count=0,
            cyclomatic_complexity=2,
        )

    def test_every_parameter_form_counts_including_self(self):
        self.assertMetrics(self.records["params"], formal_parameter_count=5)
        self.assertMetrics(
            self.records["Holder.method"], formal_parameter_count=2
        )

    def test_identity_holds_for_every_record(self):
        for record in self.records.values():
            with self.subTest(record.qualified_name):
                self.assertIdentity(record)


# ---------------------------------------------------------------------------
# The ten named negative / boundary cases
# ---------------------------------------------------------------------------

class NegativeAndBoundaryTests(MetricCaseMixin, unittest.TestCase):
    """Each of these would pass a total-only check while being wrong."""

    def test_1_nested_callable_leakage_go(self):
        records = measure("Go", ".go", GO)
        self.assertEqual(
            records["Literal"].decision_point_count, 1,
            "the literal's `if` and `for` must not reach the parent",
        )
        self.assertEqual(records["Literal"].boolean_operator_count, 0)
        self.assertEqual(records["Literal"].max_nesting_depth, 1)

    def test_2_else_must_not_increment_cyclomatic(self):
        source = b"package p\nfunc f(a int) int {\n if a > 0 {\n  return 1\n } else {\n  return 2\n }\n}\n"
        record = measure("Go", ".go", source)["f"]
        self.assertEqual(
            record.decision_point_count, 1,
            "a bare `else` is not an independent decision",
        )
        self.assertEqual(record.cyclomatic_complexity, 2)

    def test_3_default_arm_must_not_increment(self):
        with_default = b"package p\nfunc f(c int) int {\n switch c {\n case 1:\n  return 1\n default:\n  return 0\n }\n}\n"
        without = b"package p\nfunc f(c int) int {\n switch c {\n case 1:\n  return 1\n }\n return 0\n}\n"
        self.assertEqual(
            measure("Go", ".go", with_default)["f"].decision_point_count,
            measure("Go", ".go", without)["f"].decision_point_count,
            "adding a `default` arm must not change the decision count",
        )

    def test_4_boolean_total_is_not_the_max_condition(self):
        record = measure("Go", ".go", GO)["Conditions"]
        self.assertEqual(record.boolean_operator_count, 3)
        self.assertEqual(record.max_condition_operator_count, 2)
        self.assertNotEqual(
            record.boolean_operator_count, record.max_condition_operator_count,
            "conflating the two is the natural implementation error",
        )

    def test_5_java_receiver_is_not_a_parameter(self):
        record = measure("Java", ".java", JAVA)["S.receiver"]
        self.assertEqual(record.formal_parameter_count, 2)
        self.assertNotEqual(
            record.formal_parameter_count, 3,
            "counting named_children would include the receiver",
        )

    def test_6_typescript_this_is_not_a_parameter(self):
        record = measure("TypeScript", ".ts", TYPESCRIPT)["W.withThis"]
        self.assertEqual(record.formal_parameter_count, 3)
        self.assertNotEqual(record.formal_parameter_count, 4)

    def test_7_go_grouped_parameters_count_names_not_declarations(self):
        source = b"package p\nfunc grouped(a, b int, c string) int { return a }\nfunc unnamed(int, string) int { return 0 }\n"
        records = measure("Go", ".go", source)
        self.assertEqual(
            records["grouped"].formal_parameter_count, 3,
            "two declarations, three names",
        )
        self.assertEqual(
            records["unnamed"].formal_parameter_count, 2,
            "a declaration with no identifier counts as one",
        )

    def test_8_decorator_lines_are_inside_the_python_span(self):
        source = b'''
@decorator
@another
def decorated(a):
    return a
'''
        record = measure("Python", ".py", source)["decorated"]
        self.assertEqual(record.start_line, 2, "span begins at the first decorator")
        self.assertEqual(
            record.nloc, 4,
            "both decorator lines, the signature and the body are code",
        )

    def test_8b_decorator_lines_are_inside_the_typescript_span(self):
        source = b"""
class W {
    @HostListener('click')
    @Throttle(100)
    onClick(e: Event) { return 1; }
}
"""
        record = measure("TypeScript", ".ts", source)["W.onClick"]
        self.assertEqual(
            record.start_line, 3,
            "decorators are preceding siblings; the span must extend back over "
            "them or a decorated method reports a short NLOC",
        )
        self.assertEqual(record.nloc, 3)

    def test_9_nloc_includes_nested_callable_lines_while_complexity_does_not(self):
        """The deliberate asymmetry of contract section 8.2, in one assertion."""
        record = measure("Go", ".go", GO)["Literal"]
        span = record.end_line - record.start_line + 1
        self.assertGreater(span, 8)
        self.assertGreater(record.nloc, 8)
        self.assertEqual(record.cyclomatic_complexity, 2)

    def test_10_comment_and_blank_lines_are_excluded_from_nloc(self):
        source = b'''
def documented(a):
    """A docstring counts as code."""
    # a comment does not

    return a
'''
        record = measure("Python", ".py", source)["documented"]
        self.assertEqual(
            record.nloc, 3,
            "signature, docstring and return; the comment and blank are not code",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
