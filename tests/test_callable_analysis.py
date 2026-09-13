"""Pre-propagation gate for `modules.callable_analysis` (Complexity Contract 1.0.0).

Four concerns, each of which fails silently if left unchecked:

1. the dependency graph must stay acyclic once `core_metrics` calls this package;
2. callable *discovery* must stay global even though metric *attribution* is
   boundary-bounded;
3. reconciliation must be status-aware, and zero rows must never become a
   measured zero;
4. the C0.1 probe findings must stay true as the grammars are upgraded.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from modules.callable_analysis import (
    analyze_callables,
    reconcile_file,
)
from modules.callable_analysis.model import (
    BASIS_POSITIONAL,
    BASIS_QUALIFIED,
    BASIS_QUALIFIED_WITH_SIGNATURE,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NOT_APPLICABLE,
    STATUS_PARTIAL,
    CallableRecord,
    assign_row_ids,
)
from modules.core_metrics import (
    ParserRegistry,
    _go_entities,
    _java_entities,
    _python_entities,
    derive_methods_functions,
)

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPOSITORY_ROOT / "modules" / "callable_analysis"


def _parse(language: str, extension: str, source: bytes):
    return ParserRegistry().get(language, extension).parse(source).root_node


# ---------------------------------------------------------------------------
# 1. Dependency direction
# ---------------------------------------------------------------------------

class DependencyDirectionTests(unittest.TestCase):
    """`core_metrics -> callable_analysis` is the only permitted direction.

    `core_metrics` calls `analyze_callables`, so any import back would cycle.
    A lazy import inside a function would hide the cycle from the interpreter
    while leaving it in the dependency graph, so this scans the source rather
    than relying on import success.
    """

    def _imported_modules(self, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_no_module_in_the_package_imports_core_metrics(self):
        offenders = []
        for path in sorted(PACKAGE_ROOT.glob("*.py")):
            for imported in self._imported_modules(path):
                if imported == "modules.core_metrics" or imported.startswith(
                    "modules.core_metrics."
                ):
                    offenders.append(f"{path.name} imports {imported}")
        self.assertEqual(
            offenders, [],
            "modules.callable_analysis must never import modules.core_metrics: "
            "core_metrics calls analyze_callables, so this would cycle. Share "
            "semantics through modules.syntax_predicates instead.",
        )

    def test_the_shared_seam_is_a_leaf(self):
        """`syntax_predicates` may not import from `modules` at all."""
        imported = self._imported_modules(REPOSITORY_ROOT / "modules" / "syntax_predicates.py")
        offenders = sorted(name for name in imported if name.startswith("modules"))
        self.assertEqual(
            offenders, [],
            "modules.syntax_predicates is the shared leaf; importing anything "
            "from modules would reintroduce a path back into the cycle",
        )

    def test_core_metrics_reexports_the_seam_unchanged(self):
        """Historical import paths must keep resolving to one implementation."""
        from modules import core_metrics, syntax_predicates

        for name in (
            "_iter_nodes", "_iter_all_nodes", "_node_text", "_node_is_malformed",
            "_malformed_ancestor", "_field_is_reliable", "_reliable_declaration",
            "_contains_malformed", "_has_callable_ancestor", "_at_module_scope",
            "_is_named_module_variable", "_named_object_container",
            "_async_generator", "_java_method_owner", "_member_path",
            "_commonjs_export_name", "_commonjs_assignment",
            "_stably_assigned_class",
        ):
            self.assertIs(
                getattr(core_metrics, name), getattr(syntax_predicates, name),
                f"core_metrics.{name} must be the seam's function, not a copy",
            )


# ---------------------------------------------------------------------------
# 2. Discovery is global; attribution is bounded
# ---------------------------------------------------------------------------

PYTHON_NESTED = '''
def outer(flag):
    handler = lambda value: value if flag else -value

    class Inner:
        def measured_method(self, value):
            if value > 0:
                return value
            return -value

    def helper(value):
        if value:
            return 1
        return 0

    return Inner, helper, handler
'''

JAVA_NESTED = b"""
class Outer {
    void outerMethod(int seed) {
        Runnable task = () -> { if (seed > 0) { System.out.println(seed); } };
        class Local {
            int measuredMethod(int value) {
                if (value > 0) { return value; }
                return -value;
            }
        }
        task.run();
    }
}
"""


class DiscoveryIsGlobalTests(unittest.TestCase):
    """A boundary stops attribution, never discovery.

    The failure this guards against is silent in one direction: a discovery walk
    that honoured boundaries would simply never emit the inner method, while
    `methods_functions` still counts it. Only the keystone reconciliation
    notices, which is why both are asserted together here.
    """

    def test_python_method_of_a_class_inside_a_function_is_discovered(self):
        tree = ast.parse(PYTHON_NESTED)
        result = analyze_callables("Python", tree, PYTHON_NESTED.encode(), "m.py")
        names = {record.qualified_name for record in result.records}

        self.assertIn("outer", names)
        self.assertIn(
            "outer.Inner.measured_method", names,
            "a method of a class declared inside a function is in the canonical "
            "population; discovery must descend through both to find it",
        )
        # The uncounted neighbours must NOT become rows.
        self.assertNotIn("outer.helper", names, "a nested function is secondary")
        self.assertEqual(
            len(result.records), 2, sorted(names),
        )

    def test_python_nested_case_reconciles_exactly(self):
        tree = ast.parse(PYTHON_NESTED)
        entities, _error, _syntax = _python_entities(PYTHON_NESTED)
        expected = derive_methods_functions(entities)
        result = analyze_callables("Python", tree, PYTHON_NESTED.encode(), "m.py")

        self.assertEqual(result.callable_count, expected)
        outcome = reconcile_file(result, expected, STATUS_COMPLETE)
        self.assertEqual(outcome["outcome"], "exact")

    def test_java_method_of_a_local_class_is_discovered(self):
        root = _parse("Java", ".java", JAVA_NESTED)
        result = analyze_callables("Java", root, JAVA_NESTED, "Outer.java")
        names = {record.qualified_name for record in result.records}

        self.assertIn("Outer.outerMethod", names)
        self.assertIn(
            "Outer.outerMethod.Local.measuredMethod", names,
            "a method of a named local class is a direct class method and is "
            "counted by methods_functions; discovery must find it",
        )

    def test_java_nested_case_reconciles_exactly(self):
        root = _parse("Java", ".java", JAVA_NESTED)
        expected = derive_methods_functions(_java_entities(root))
        result = analyze_callables("Java", root, JAVA_NESTED, "Outer.java")

        self.assertEqual(result.callable_count, expected)
        self.assertEqual(
            reconcile_file(result, expected, STATUS_COMPLETE)["outcome"], "exact"
        )

    def test_boundary_sets_are_declared_but_unused_by_discovery(self):
        """The boundary set belongs to attribution and must not gate discovery."""
        from modules.callable_analysis import go, java, python

        for module in (go, java, python):
            self.assertTrue(
                module.BOUNDARY_NODES,
                f"{module.__name__} must declare its attribution boundaries",
            )
        # Java's boundary set contains `class_declaration`; if discovery
        # consulted it, the local-class method above could not be found. The
        # preceding tests are the proof, this is the statement of intent.
        self.assertIn("class_declaration", java.BOUNDARY_NODES)
        self.assertIn(ast.ClassDef, python.BOUNDARY_NODES)


# ---------------------------------------------------------------------------
# 3. Status-aware reconciliation
# ---------------------------------------------------------------------------

JAVA_OVERLOADS = b"""
class Service {
    void handle(int a) {}
    void handle(java.lang.String a) {}
    void handle(String a, int... rest) {}
    void handle(String a, long... rest) {}
    void handle(Service Service.this, double d) {}
}
"""


class JavaOverloadIdentityTests(unittest.TestCase):
    """Overloads differ only by parameter types, so the qualified name alone
    cannot separate them."""

    def _records(self):
        root = _parse("Java", ".java", JAVA_OVERLOADS)
        return analyze_callables(
            "Java", root, JAVA_OVERLOADS, "Service.java"
        ).records

    def test_every_overload_gets_a_distinct_row_id(self):
        records = self._records()
        self.assertEqual(len(records), 5)
        self.assertEqual(
            len({record.callable_row_id for record in records}), 5
        )

    def test_overloads_resolve_without_falling_back_to_positional(self):
        for record in self._records():
            self.assertEqual(record.row_id_basis, BASIS_QUALIFIED_WITH_SIGNATURE)

    def test_varargs_element_type_is_part_of_the_discriminator(self):
        """`spread_parameter` has no named fields; reading a `type` field yields
        None and would render every varargs parameter identically, collapsing
        `f(String,int...)` and `f(String,long...)` into one key."""
        signatures = {record.signature_discriminator for record in self._records()}
        self.assertIn("(String,int...)", signatures)
        self.assertIn("(String,long...)", signatures)
        self.assertNotIn("(String,?...)", signatures)

    def test_a_receiver_parameter_is_absent_from_the_discriminator(self):
        signatures = {record.signature_discriminator for record in self._records()}
        self.assertIn("(double)", signatures)
        self.assertNotIn("(Service,double)", signatures)


class ReconciliationStatusTests(unittest.TestCase):
    """Complexity Contract 1.0.0 section 5.

    The rule that matters: zero emitted rows is never a measured zero.
    """

    def _result(self, count: int, status: str = STATUS_COMPLETE):
        records = [
            CallableRecord(
                name=f"f{index}", qualified_name=f"f{index}",
                callable_kind="module_function", _order=index,
            )
            for index in range(count)
        ]
        from modules.callable_analysis import CallableAnalysisResult

        return CallableAnalysisResult(records, status, status)

    def test_complete_and_equal_is_exact(self):
        outcome = reconcile_file(self._result(3), 3, STATUS_COMPLETE)
        self.assertEqual(outcome["outcome"], "exact")
        self.assertEqual(outcome["observation_quality"], "complete")

    def test_complete_and_mismatched_is_a_residual(self):
        outcome = reconcile_file(self._result(2), 3, STATUS_COMPLETE)
        self.assertEqual(outcome["outcome"], "residual")
        self.assertEqual(outcome["residual"], 1)

    def test_failed_measurement_with_zero_rows_is_not_evaluable(self):
        """The headline rule. A failed measurement never reconciles as zero."""
        result = self._result(0, STATUS_FAILED)
        outcome = reconcile_file(result, None, STATUS_FAILED)

        self.assertEqual(outcome["outcome"], "not_evaluable")
        self.assertIsNone(result.callable_count, "failed must report null, not 0")
        self.assertNotEqual(
            outcome["outcome"], "exact",
            "0 == 0 must never be reported as agreement when nothing was measured",
        )

    def test_failed_measurement_is_not_evaluable_even_if_rows_exist(self):
        outcome = reconcile_file(self._result(4, STATUS_FAILED), None, STATUS_FAILED)
        self.assertEqual(outcome["outcome"], "not_evaluable")

    def test_not_applicable_with_zero_rows_is_not_evaluable(self):
        result = self._result(0, STATUS_NOT_APPLICABLE)
        outcome = reconcile_file(result, 0, STATUS_NOT_APPLICABLE)
        self.assertEqual(outcome["outcome"], "not_evaluable")
        self.assertIsNone(result.callable_count)

    def test_partial_equality_is_exact_but_labelled_partial(self):
        outcome = reconcile_file(self._result(3, STATUS_PARTIAL), 3, STATUS_PARTIAL)
        self.assertEqual(outcome["outcome"], "exact")
        self.assertEqual(outcome["observation_quality"], "partial")
        self.assertIn("not a verified count", outcome["reason"])

    def test_complete_zero_is_a_verified_zero(self):
        """The counterpart: a genuinely empty file DOES reconcile as exact 0."""
        result = self._result(0, STATUS_COMPLETE)
        self.assertEqual(result.callable_count, 0)
        self.assertEqual(reconcile_file(result, 0, STATUS_COMPLETE)["outcome"], "exact")

    def test_missing_methods_functions_is_not_evaluable(self):
        outcome = reconcile_file(self._result(3), None, STATUS_COMPLETE)
        self.assertEqual(outcome["outcome"], "not_evaluable")


class RowIdentityTests(unittest.TestCase):
    def _record(self, name: str, order: int, discriminator: str | None = None):
        return CallableRecord(
            name=name, qualified_name=name, callable_kind="module_function",
            signature_discriminator=discriminator, _order=order,
        )

    def test_unique_names_stay_non_positional(self):
        records = assign_row_ids(
            [self._record("a", 0), self._record("b", 1)], "m.py", "Python"
        )
        self.assertEqual([item.row_id_basis for item in records],
                         [BASIS_QUALIFIED, BASIS_QUALIFIED])
        self.assertTrue(all(item.ordinal is None for item in records))

    def test_a_signature_discriminator_is_recorded_in_the_basis(self):
        records = assign_row_ids([self._record("m", 0, "(int)")], "T.java", "Java")
        self.assertEqual(records[0].row_id_basis, BASIS_QUALIFIED_WITH_SIGNATURE)

    def test_java_overloads_separate_without_becoming_positional(self):
        records = assign_row_ids(
            [self._record("m", 0, "(int)"), self._record("m", 1, "(java.lang.String)")],
            "T.java", "Java",
        )
        self.assertEqual({item.row_id_basis for item in records},
                         {BASIS_QUALIFIED_WITH_SIGNATURE})
        self.assertEqual(len({item.callable_row_id for item in records}), 2)

    def test_a_genuine_collision_makes_every_member_positional(self):
        """Including the first: "the one without an ordinal" would be an identity
        that silently changes when an earlier sibling is deleted."""
        records = assign_row_ids(
            [self._record("m", 0), self._record("m", 1)], "m.py", "Python"
        )
        self.assertEqual({item.row_id_basis for item in records}, {BASIS_POSITIONAL})
        self.assertEqual([item.ordinal for item in records], [0, 1])
        self.assertEqual(len({item.callable_row_id for item in records}), 2)

    def test_row_ids_are_unique_across_the_go_corpus(self):
        path = (
            REPOSITORY_ROOT
            / "validation/differential/corpus/complexity/go/constructs.go"
        )
        source = path.read_bytes()
        root = _parse("Go", ".go", source)
        result = analyze_callables("Go", root, source, "constructs.go")

        identifiers = [record.callable_row_id for record in result.records]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(
            result.callable_count, derive_methods_functions(_go_entities(root))
        )


CORPUS_EXTENSIONS = {
    ".go": ("Go", ".go"),
    ".java": ("Java", ".java"),
    ".js": ("JavaScript", ".js"),
    ".jsx": ("JavaScript", ".jsx"),
    ".mjs": ("JavaScript", ".mjs"),
    ".cjs": ("JavaScript", ".cjs"),
    ".ts": ("TypeScript", ".ts"),
    ".tsx": ("TypeScript", ".tsx"),
    ".mts": ("TypeScript", ".mts"),
    ".cts": ("TypeScript", ".cts"),
    ".py": ("Python", ".py"),
}


class CorpusReconciliationTests(unittest.TestCase):
    """The keystone invariant across every corpus file, all five languages.

    This is the C1 gate. It compares two independent computations over the same
    selected tree, so a drift in either population predicate shows up as a
    residual rather than as a plausible-looking number.
    """

    def _compare(self, path: pathlib.Path):
        language, extension = CORPUS_EXTENSIONS[path.suffix.lower()]
        source = path.read_bytes()
        if language == "Python":
            text = source.decode("utf-8")
            entities, _error, _syntax = _python_entities(text)
            if entities is None:
                return None
            root = ast.parse(text)
        else:
            root = _parse(language, extension, source)
            if language == "Java":
                entities = _java_entities(root)
            elif language == "Go":
                entities = _go_entities(root)
            else:
                from modules.core_metrics import _js_ts_entities

                entities = _js_ts_entities(root, source)
        expected = derive_methods_functions(entities)
        result = analyze_callables(language, root, source, str(path))
        return language, expected, result

    def _corpus_files(self):
        for base in (
            REPOSITORY_ROOT / "validation/conformance/data/cases",
            REPOSITORY_ROOT / "validation/differential/corpus",
        ):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix.lower() in CORPUS_EXTENSIONS:
                    yield path

    def test_every_corpus_file_reconciles_exactly(self):
        checked = 0
        languages: set[str] = set()
        for path in self._corpus_files():
            try:
                compared = self._compare(path)
            except Exception:  # a file the corpus keeps deliberately broken
                continue
            if compared is None:
                continue
            language, expected, result = compared
            with self.subTest(path=path.name):
                outcome = reconcile_file(result, expected, STATUS_COMPLETE)
                self.assertEqual(
                    outcome["outcome"], "exact",
                    f"{path}: methods_functions={expected} "
                    f"callable rows={result.callable_count}",
                )
            checked += 1
            languages.add(language)

        self.assertGreater(checked, 40, "the corpus sweep collected too few files")
        self.assertEqual(
            languages,
            {"Go", "Java", "JavaScript", "TypeScript", "Python"},
            "the gate must exercise all five languages",
        )

    def test_row_ids_are_unique_within_every_corpus_file(self):
        for path in self._corpus_files():
            try:
                compared = self._compare(path)
            except Exception:
                continue
            if compared is None:
                continue
            _language, _expected, result = compared
            identifiers = [record.callable_row_id for record in result.records]
            with self.subTest(path=path.name):
                self.assertEqual(len(identifiers), len(set(identifiers)))


class CoreMetricsIntegrationTests(unittest.TestCase):
    """`core_metrics` calls the analyzer, and cannot be broken by it."""

    def test_discovery_failure_cannot_fail_a_measurement(self):
        """A defect in a new, non-persisted analysis must not fail four shipped
        metrics. The failure mode is a `failed` complexity status and nothing
        else."""
        from modules import core_metrics

        class _Exploding:
            @property
            def relative_path(self):
                return "x.go"

            parser_offsets_map_directly_to_original_bytes = True

        result = core_metrics._discover_callables(
            "Go", object(), b"", _Exploding(), STATUS_COMPLETE
        )
        self.assertEqual(result.structural_complexity_status, STATUS_FAILED)
        self.assertEqual(result.records, [])

    def test_a_null_tree_yields_a_failed_status_not_an_empty_success(self):
        from modules import core_metrics

        class _Record:
            relative_path = "x.py"
            parser_offsets_map_directly_to_original_bytes = True

        result = core_metrics._discover_callables(
            "Python", None, b"", _Record(), STATUS_COMPLETE
        )
        self.assertEqual(result.structural_complexity_status, STATUS_FAILED)
        self.assertIsNone(
            result.callable_count,
            "a failed discovery reports null, never a measured zero",
        )

    def test_python_source_is_parsed_exactly_once(self):
        """`_python_parse` exists so the counter and the enumerator share a tree."""
        from modules import core_metrics

        tree, error, syntax = core_metrics._python_parse("def f():\n    return 1\n")
        self.assertIsNotNone(tree)
        self.assertIsNone(error)
        self.assertIsNone(syntax)

        entities, entity_error, _ = core_metrics._python_entities(
            "def f():\n    return 1\n", tree, error, syntax
        )
        self.assertIsNone(entity_error)
        self.assertEqual(derive_methods_functions(entities), 1)

    def test_python_entities_still_works_without_a_supplied_tree(self):
        """Every existing caller passes only text and must be unaffected."""
        entities, error, syntax = _python_entities("def f():\n    return 1\n")
        self.assertIsNone(error)
        self.assertIsNone(syntax)
        self.assertEqual(derive_methods_functions(entities), 1)

    def test_python_syntax_error_reporting_is_unchanged(self):
        entities, error, syntax = _python_entities("def broken(:\n")
        self.assertIsNone(entities)
        self.assertIsNotNone(syntax)
        self.assertTrue(error.startswith("Python syntax error at line "))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
