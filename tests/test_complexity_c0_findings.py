"""Regressions pinning the C0.1 grammar probe findings.

Evidence: `validation/complexity_c0_20260810/PROBE_FINDINGS.md`.

These are grammar-shape facts the Complexity Contract depends on. They were
established empirically and are pinned here because a grammar upgrade could
change any of them silently -- and each one fails in a quiet direction:

* a decorator that moves inside the declaration node changes a span, not a count;
* a compatibility rewrite that stops preserving line count shifts every line
  number in a recovered file;
* a receiver parameter counted as a formal parameter is off by one, only for
  the rare Java files that use the form;
* boundary detection that trusts `_iter_all_nodes` truncates traversal at every
  `class` keyword token.
"""

from __future__ import annotations

import unittest

from modules.core_metrics import (
    ParserRegistry,
    _iter_all_nodes,
    _iter_nodes,
    _javascript_compatibility_parse,
    _rewrite_json_import_assertions,
    _rewrite_raw_jsx_ampersands,
    _rewrite_reserved_jsx_attributes,
    _rewrite_typescript_keyword_parameters,
    _typescript_compatibility_parse,
)

REGISTRY = ParserRegistry()


def parse(language: str, extension: str, source: bytes):
    return REGISTRY.get(language, extension).parse(source)


class DecoratorSpanTests(unittest.TestCase):
    """JS/TS decorators are PRECEDING SIBLINGS, not children.

    A decorated method's declaration node begins *below* its own decorators, so
    Complexity Contract 1.0.0 section 8.1 extends the NLOC span backwards over
    contiguous preceding sibling `decorator` nodes. Without that, every decorated
    TypeScript method reports a silently short span.
    """

    SINGLE = b"""
@Component({selector: 'x'})
export class Widget {
    @HostListener('click')
    onClick(event: Event) { return 1; }
}
"""

    MULTIPLE = b"""
@Injectable()
@Component({selector: 'y'})
export class Multi {
    @HostListener('click')
    @Throttle(100)
    @Log()
    onClick(event: Event) { return 1; }
}
"""

    def _method(self, source: bytes):
        root = parse("TypeScript", ".ts", source).root_node
        for node in _iter_nodes(root):
            if node.type == "method_definition":
                return node
        self.fail("no method_definition parsed")

    def _preceding_decorators(self, node):
        parent = node.parent
        found = []
        index = parent.children.index(node)
        for previous in reversed(parent.children[:index]):
            if previous.type == "decorator":
                found.append(previous)
            else:
                break
        return found

    def test_a_decorator_is_not_a_child_of_the_declaration(self):
        method = self._method(self.SINGLE)
        self.assertEqual(
            [child.type for child in method.children if child.type == "decorator"],
            [],
            "if decorators become children the span rule must be revisited",
        )

    def test_a_single_decorator_precedes_the_declaration(self):
        method = self._method(self.SINGLE)
        decorators = self._preceding_decorators(method)
        self.assertEqual(len(decorators), 1)
        self.assertLess(
            decorators[0].start_point[0], method.start_point[0],
            "the decorator must lie above the declaration's own start line",
        )

    def test_multiple_decorators_are_all_preceding_siblings(self):
        method = self._method(self.MULTIPLE)
        decorators = self._preceding_decorators(method)
        self.assertEqual(
            len(decorators), 3,
            "all three decorators must be reachable as contiguous preceding "
            "siblings; a span rule that walks back only one line loses two",
        )
        earliest = min(item.start_point[0] for item in decorators)
        self.assertEqual(
            method.start_point[0] - earliest, 3,
            "the extended span must begin three lines above the declaration",
        )

    def test_a_class_decorator_also_precedes_its_declaration(self):
        root = parse("TypeScript", ".ts", self.SINGLE).root_node
        classes = [n for n in _iter_nodes(root) if n.type == "class_declaration"]
        self.assertEqual(len(classes), 1)
        decorators = [n for n in _iter_nodes(root) if n.type == "decorator"]
        self.assertTrue(
            any(item.start_point[0] < classes[0].start_point[0] for item in decorators)
        )


class CompatibilityRewriteLineCountTests(unittest.TestCase):
    """Every recovery strategy is a same-width, in-place byte substitution.

    Complexity Contract 1.0.0 section 8.3 relies on this: it is why a recovered
    parse does not force `nloc_status` to `partial`. If a strategy ever inserts
    or removes a byte, that conclusion no longer holds and the contract must be
    revised rather than the test relaxed.
    """

    JSON_ASSERTION = b'import config from "./config.json" assert { type: "json" };\nconst v = config.name;\n'
    JSX_RESERVED = b'const App = () => (\n  <div class="box" for="field">\n    text\n  </div>\n);\nexport default App;\n'
    JSX_AMPERSAND = b"const App = () => (\n  <div>\n    Tom & Jerry\n  </div>\n);\nexport default App;\n"
    TS_KEYWORD_PARAM = b"export type Handler = (string) => void;\nexport type Other = (any) => void;\nconst x = 1;\n"

    def _assert_preserved(self, label, language, extension, source, rewrite):
        tree = parse(language, extension, source)
        rewritten = rewrite(source, tree.root_node)
        self.assertEqual(
            len(rewritten), len(source), f"{label}: byte length changed"
        )
        self.assertEqual(
            rewritten.count(b"\n"), source.count(b"\n"),
            f"{label}: physical line count changed",
        )
        self.assertNotEqual(
            rewritten, source,
            f"{label}: strategy did not fire, so this case proves nothing",
        )

    def test_json_import_assertion_preserves_lines(self):
        self._assert_preserved(
            "json_import_assertion", "JavaScript", ".js", self.JSON_ASSERTION,
            _rewrite_json_import_assertions,
        )

    def test_jsx_reserved_attribute_preserves_lines(self):
        self._assert_preserved(
            "jsx_reserved_attribute", "JavaScript", ".jsx", self.JSX_RESERVED,
            _rewrite_reserved_jsx_attributes,
        )

    def test_raw_jsx_ampersand_preserves_lines(self):
        self._assert_preserved(
            "raw_jsx_ampersand", "JavaScript", ".jsx", self.JSX_AMPERSAND,
            _rewrite_raw_jsx_ampersands,
        )

    def test_typescript_keyword_parameter_preserves_lines(self):
        self._assert_preserved(
            "typescript_keyword_parameter", "TypeScript", ".ts",
            self.TS_KEYWORD_PARAM, _rewrite_typescript_keyword_parameters,
        )

    def test_the_selector_preserves_lines_end_to_end(self):
        cases = (
            ("javascript/json-assert", "JavaScript", ".js", self.JSON_ASSERTION,
             _javascript_compatibility_parse),
            ("javascript/jsx-reserved", "JavaScript", ".jsx", self.JSX_RESERVED,
             _javascript_compatibility_parse),
            ("javascript/jsx-ampersand", "JavaScript", ".jsx", self.JSX_AMPERSAND,
             _javascript_compatibility_parse),
            ("typescript/keyword-param", "TypeScript", ".ts", self.TS_KEYWORD_PARAM,
             _typescript_compatibility_parse),
        )
        for label, language, extension, source, selector in cases:
            with self.subTest(label):
                parser = REGISTRY.get(language, extension)
                tree = parser.parse(source)
                selected, _best, _applied, _limit = selector(parser, source, tree)
                self.assertEqual(
                    selected.source.count(b"\n"), source.count(b"\n"),
                    "the SELECTED source is what line numbers are read from",
                )


class JavaParameterShapeTests(unittest.TestCase):
    """`receiver_parameter` is a distinct node type and is not a formal parameter."""

    SOURCE = b"""
class Outer {
    void plain(int a, String b) {}
    void withReceiver(Outer Outer.this, int a) {}
    void varargs(String first, int... rest) {}
}
"""

    def _parameters(self, name: str):
        root = parse("Java", ".java", self.SOURCE).root_node
        for node in _iter_nodes(root):
            if node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None and self.SOURCE[
                    name_node.start_byte : name_node.end_byte
                ].decode() == name:
                    return node.child_by_field_name("parameters")
        self.fail(f"method {name} not parsed")

    @staticmethod
    def _formal_count(parameters) -> int:
        """The Complexity Contract 1.0.0 section 9 rule for Java."""
        return sum(
            1 for child in parameters.named_children
            if child.type in {"formal_parameter", "spread_parameter"}
        )

    def test_receiver_parameter_has_its_own_node_type(self):
        kinds = [child.type for child in self._parameters("withReceiver").named_children]
        self.assertEqual(kinds, ["receiver_parameter", "formal_parameter"])

    def test_the_contract_rule_excludes_the_receiver(self):
        self.assertEqual(self._formal_count(self._parameters("withReceiver")), 1)

    def test_counting_named_children_would_be_wrong(self):
        """States the trap explicitly so it cannot be reintroduced."""
        parameters = self._parameters("withReceiver")
        self.assertEqual(len(parameters.named_children), 2)
        self.assertNotEqual(
            len(parameters.named_children), self._formal_count(parameters),
            "len(named_children) counts the receiver; the contract does not",
        )

    def test_varargs_is_one_parameter(self):
        self.assertEqual(self._formal_count(self._parameters("varargs")), 2)

    def test_plain_parameters(self):
        self.assertEqual(self._formal_count(self._parameters("plain")), 2)


class NamedNodeIterationTests(unittest.TestCase):
    """Boundary detection must use named nodes, never keyword tokens.

    `_iter_all_nodes` yields a node of type `class` for the `class` KEYWORD. A
    boundary set containing the class-expression type would match that token and
    truncate traversal at every class keyword.
    """

    SOURCE = b"class K {\n    method() { if (1) {} }\n}\n"

    def test_iter_all_nodes_sees_the_class_keyword_token(self):
        root = parse("JavaScript", ".js", self.SOURCE).root_node
        types = [node.type for node in _iter_all_nodes(root)]
        self.assertIn(
            "class", types,
            "if this stops holding the trap is gone, but the guard below is "
            "still the correct discipline",
        )

    def test_iter_nodes_does_not_see_the_keyword_token(self):
        root = parse("JavaScript", ".js", self.SOURCE).root_node
        types = [node.type for node in _iter_nodes(root)]
        self.assertNotIn(
            "class", types,
            "named-node iteration is what makes the boundary set safe",
        )
        self.assertIn("class_declaration", types)

    def test_entity_counting_and_discovery_both_use_named_iteration(self):
        """The production rule, asserted rather than assumed."""
        import ast
        import pathlib

        for relative in (
            "modules/core_metrics.py",
            "modules/callable_analysis/go.py",
            "modules/callable_analysis/java.py",
        ):
            path = pathlib.Path(__file__).resolve().parent.parent / relative
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = [
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            ]
            with self.subTest(relative):
                self.assertIn(
                    "_iter_nodes", calls,
                    "entity/callable walks must iterate NAMED nodes",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
