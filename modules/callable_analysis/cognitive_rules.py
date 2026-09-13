"""Per-language readings of the frozen Cognitive Complexity rule table.

One file rather than four, deliberately: the frozen table is one document, and a
reviewer checking "is this what revision 3 says" should not have to hold four
modules open. Each specification below cites the rule ID it implements.

Boundary sets are imported from the language modules rather than restated, so a
nested-callable boundary cannot drift between the cyclomatic and cognitive
readings of the same construct.

Python is not here: its `ast` tree is a different shape and its measurement
lives in `python.py`, exactly as the cyclomatic one does.
"""

from __future__ import annotations

from typing import Any, Sequence

from modules.syntax_predicates import _node_text

from .cognitive import (
    BOOLEAN_OPERATORS,
    CognitiveRules,
    binary_operator_reader,
    boolean_root_predicate,
    self_call_predicate,
)


def _fields(node: Any, *names: str) -> list[Any]:
    found = []
    for name in names:
        child = node.child_by_field_name(name)
        if child is not None:
            found.append(child)
    return found


def _has_label(node: Any) -> bool:
    """A labelled jump carries a label child; a bare one does not."""
    return any(
        child.type in {"statement_identifier", "label_name", "identifier"}
        for child in node.named_children
    )


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_STRUCTURAL = frozenset({
    "for_statement",                # S-LOOP (Go's only loop form)
    "expression_switch_statement",  # S-SWITCH
    "type_switch_statement",        # S-SWITCH
    "select_statement",             # S-SELECT
})


def _go_is_else_if(node: Any) -> bool:
    parent = node.parent
    return (
        node.type == "if_statement"
        and parent is not None
        and parent.type == "if_statement"
        and (parent.child_by_field_name("alternative") is not None)
        and parent.child_by_field_name("alternative").id == node.id
    )


def _go_structural(node: Any) -> bool:
    if node.type in _GO_STRUCTURAL:
        return True
    return node.type == "if_statement" and not _go_is_else_if(node)


def _go_flat(node: Any, source: bytes) -> int:
    if _go_is_else_if(node):                                   # F-ELSEIF
        return 1
    if node.type == "block":
        parent = node.parent
        if (parent is not None and parent.type == "if_statement"
                and parent.child_by_field_name("alternative") is not None
                and parent.child_by_field_name("alternative").id == node.id):
            return 1                                            # F-ELSE
    if node.type == "goto_statement":                           # F-LABELJUMP
        return 1
    if node.type in {"break_statement", "continue_statement"} and _has_label(node):
        return 1                                                # F-LABELJUMP
    return 0


def _go_bodies(node: Any) -> Sequence[Any]:
    if node.type == "if_statement":
        # The `alternative` is NOT deepened here: an else-if chain stays flat,
        # and a bare `else` block deepens itself through its own F-ELSE entry.
        return _fields(node, "consequence")
    if node.type == "block":
        return list(node.named_children)      # a bare `else` block's statements
    if node.type == "for_statement":
        return _fields(node, "body")
    if node.type in {"expression_switch_statement", "type_switch_statement",
                     "select_statement"}:
        return list(node.named_children)
    return ()


_go_operator = binary_operator_reader(BOOLEAN_OPERATORS["Go"])

GO_RULES = CognitiveRules(
    is_boundary=lambda node: node.type in {
        "func_literal", "function_declaration", "method_declaration",
    },
    is_structural=_go_structural,
    body_children=_go_bodies,
    flat_increment=_go_flat,
    is_boolean_root=boolean_root_predicate(_go_operator),
    boolean_operator=_go_operator,
    is_self_call=self_call_predicate(frozenset({"call_expression"})),
)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

_JAVA_STRUCTURAL = frozenset({
    "while_statement", "do_statement", "for_statement", "enhanced_for_statement",
    "switch_expression",        # both the statement and the expression form
    "catch_clause",
    "ternary_expression",
})


def _java_is_else_if(node: Any) -> bool:
    parent = node.parent
    if node.type != "if_statement" or parent is None or parent.type != "if_statement":
        return False
    alternative = parent.child_by_field_name("alternative")
    return alternative is not None and alternative.id == node.id


def _java_structural(node: Any) -> bool:
    if node.type in _JAVA_STRUCTURAL:
        return True
    return node.type == "if_statement" and not _java_is_else_if(node)


def _java_flat(node: Any, source: bytes) -> int:
    if _java_is_else_if(node):
        return 1
    parent = node.parent
    if parent is not None and parent.type == "if_statement" and node.type != "if_statement":
        alternative = parent.child_by_field_name("alternative")
        if alternative is not None and alternative.id == node.id:
            return 1                                            # F-ELSE
    if node.type == "guard":                                    # F-GUARD
        return 1
    if node.type in {"break_statement", "continue_statement"} and _has_label(node):
        return 1                                                # F-LABELJUMP
    return 0


def _java_bodies(node: Any) -> Sequence[Any]:
    if node.type == "if_statement":
        return _fields(node, "consequence")
    if node.type in {"while_statement", "for_statement", "enhanced_for_statement",
                     "do_statement"}:
        return _fields(node, "body")
    if node.type == "switch_expression":
        return _fields(node, "body")
    if node.type == "catch_clause":
        return _fields(node, "body")
    if node.type == "ternary_expression":
        return _fields(node, "consequence", "alternative")
    parent = node.parent
    if parent is not None and parent.type == "if_statement":
        alternative = parent.child_by_field_name("alternative")
        if alternative is not None and alternative.id == node.id:
            return list(node.named_children)     # the `else` body's statements
    return ()


def _java_boundary(node: Any) -> bool:
    if node.type == "object_creation_expression":
        return any(child.type == "class_body" for child in node.named_children)
    if node.type == "block":
        parent = node.parent
        return parent is not None and parent.type == "class_body"
    return node.type in {
        "method_declaration", "constructor_declaration",
        "compact_constructor_declaration", "lambda_expression",
        "class_declaration", "record_declaration", "enum_declaration",
        "interface_declaration", "static_initializer",
    }


_java_operator = binary_operator_reader(BOOLEAN_OPERATORS["Java"])

JAVA_RULES = CognitiveRules(
    is_boundary=_java_boundary,
    is_structural=_java_structural,
    body_children=_java_bodies,
    flat_increment=_java_flat,
    is_boolean_root=boolean_root_predicate(_java_operator),
    boolean_operator=_java_operator,
    is_self_call=self_call_predicate(
        frozenset({"method_invocation"}), function_field="name"
    ),
)


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------

_JS_STRUCTURAL = frozenset({
    "while_statement", "do_statement", "for_statement", "for_in_statement",
    "switch_statement", "catch_clause", "ternary_expression",
})


def _js_is_else_if(node: Any) -> bool:
    """In this grammar the else branch is wrapped in an `else_clause`."""
    parent = node.parent
    return (
        node.type == "if_statement"
        and parent is not None
        and parent.type == "else_clause"
    )


def _js_structural(node: Any) -> bool:
    if node.type in _JS_STRUCTURAL:
        return True
    return node.type == "if_statement" and not _js_is_else_if(node)


def _js_holds_else_if(node: Any) -> bool:
    return any(child.type == "if_statement" for child in node.named_children)


def _js_flat(node: Any, source: bytes) -> int:
    if node.type == "else_clause":
        # F-ELSE only. The `else if` increment is scored on the nested
        # `if_statement` instead -- Go and Java already do it that way, through
        # their `alternative` field.
        #
        # It cannot stay here: the engine computes `body_children` only for a
        # node that scored, so an increment on the CLAUSE left the `else if`'s
        # own consequence undeepened, and frozen table 2.2 requires F-ELSEIF to
        # raise nesting for its own body. Scoring the `if_statement` makes the
        # node that owns the consequence the node that scores.
        return 0 if _js_holds_else_if(node) else 1
    if node.type == "if_statement" and _js_is_else_if(node):
        return 1                                                # F-ELSEIF
    if node.type in {"break_statement", "continue_statement"} and _has_label(node):
        return 1                                                # F-LABELJUMP
    return 0


def _js_bodies(node: Any) -> Sequence[Any]:
    if node.type == "if_statement":
        return _fields(node, "consequence")
    if node.type in {"while_statement", "do_statement", "for_statement",
                     "for_in_statement"}:
        return _fields(node, "body")
    if node.type == "switch_statement":
        return _fields(node, "body")
    if node.type == "catch_clause":
        return _fields(node, "body")
    if node.type == "ternary_expression":
        return _fields(node, "consequence", "alternative")
    if node.type == "else_clause":
        # An `else if` chain stays flat: the nested `if_statement` deepens its
        # own consequence and nothing else.
        return [
            child for child in node.named_children if child.type != "if_statement"
        ]
    return ()


_js_operator = binary_operator_reader(BOOLEAN_OPERATORS["JavaScript"])

_TYPE_ONLY = frozenset({
    "type_annotation", "type_arguments", "type_parameters", "type_identifier",
    "predefined_type", "union_type", "intersection_type", "conditional_type",
    "generic_type", "type_predicate_annotation", "opting_type_annotation",
    "omitting_type_annotation", "asserts_annotation",
})

JS_TS_RULES = CognitiveRules(
    is_boundary=lambda node: node.type in {
        "function_declaration", "generator_function_declaration",
        "function_expression", "arrow_function", "generator_function",
        "method_definition", "class_declaration", "abstract_class_declaration",
    },
    is_structural=_js_structural,
    body_children=_js_bodies,
    flat_increment=_js_flat,
    is_boolean_root=boolean_root_predicate(_js_operator),
    boolean_operator=_js_operator,
    is_self_call=self_call_predicate(frozenset({"call_expression"})),
    #: Z-TYPEONLY is a SUBTREE exclusion, not a zero node: a runtime-looking
    #: expression written inside a type must never contribute.
    is_excluded_subtree=lambda node: node.type in _TYPE_ONLY,
)


RULES_BY_LANGUAGE = {
    "Go": GO_RULES,
    "Java": JAVA_RULES,
    "JavaScript": JS_TS_RULES,
    "TypeScript": JS_TS_RULES,
}
