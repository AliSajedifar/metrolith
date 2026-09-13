"""Neutral tree-syntax predicates shared by metric producers.

**This module is a leaf.** It imports nothing from ``modules`` and must keep it
that way, because two independent consumers depend on it:

* ``modules.core_metrics`` -- the four benchmark metrics;
* ``modules.callable_analysis`` -- per-callable complexity records.

``core_metrics`` calls ``callable_analysis``, so if ``callable_analysis`` also
imported ``core_metrics`` the graph would cycle. Extracting these predicates is
what makes the graph acyclic **without duplicating callable-population
semantics** -- and duplication is the failure this seam exists to prevent, since
two copies of "what counts as a function" drift silently and the drift only
surfaces as an unexplained metric difference much later.

Nothing here is new. Every function was moved verbatim out of
``modules.core_metrics``, which re-exports all of them, so every existing
reference and every historical import path keeps resolving to exactly one
implementation.

Scope discipline: a predicate belongs here only if it is a **pure function of a
syntax node** (plus source bytes). Anything that touches configuration, a
``FileRecord``, a parser registry, diagnostics or metric accumulation stays in
``core_metrics``.
"""

from __future__ import annotations

import ast
from typing import Any, Iterable


# -- line classification ----------------------------------------------------

def line_is_code(original: str, without_comments: str) -> bool:
    """One physical line's classification, defined exactly once.

    A line is code when it holds something after comment masking. Blank lines
    and comment-only lines are not. `core_metrics._classify_lines` counts whole
    files with this predicate and per-callable NLOC counts a line span with it,
    so function NLOC cannot drift from repository `lines_of_code`.
    """
    return bool(original.strip()) and bool(without_comments.strip())


# -- Python declaration predicates ------------------------------------------

def python_is_overload(node: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    """True when a Python function is an ``@overload`` signature declaration.

    Signature-only declarations are secondary: they are `signature_only_methods`,
    never `methods_functions`. Shared so the entity counter and the callable
    enumerator cannot disagree about which declarations are real.
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "overload":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "overload":
            return True
    return False


# -- tree walking -----------------------------------------------------------

def _iter_nodes(node: Any) -> Iterable[Any]:
    yield node
    for child in node.named_children:
        yield from _iter_nodes(child)


def _iter_all_nodes(node: Any) -> Iterable[Any]:
    yield node
    for child in node.children:
        yield from _iter_all_nodes(child)


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# -- malformed-subtree reliability guards -----------------------------------

def _node_is_malformed(node: Any) -> bool:
    return node.type == "ERROR" or bool(getattr(node, "is_missing", False))


def _malformed_ancestor(node: Any) -> bool:
    current = node
    while current is not None:
        if _node_is_malformed(current):
            return True
        current = current.parent
    return False


def _field_is_reliable(node: Any, field: str) -> bool:
    value = node.child_by_field_name(field)
    return value is not None and not _node_is_malformed(value) and not _malformed_ancestor(value)


def _reliable_declaration(node: Any, *required_fields: str) -> bool:
    if _malformed_ancestor(node):
        return False
    for field in required_fields:
        value = node.child_by_field_name(field)
        if value is None and field == "parameters":
            value = node.child_by_field_name("parameter")
        if value is None or _node_is_malformed(value) or _malformed_ancestor(value):
            return False
        if any(getattr(child, "is_missing", False) for child in _iter_all_nodes(value)):
            return False
        if field == "parameters" and _contains_malformed(value):
            return False
    return True


def _contains_malformed(node: Any) -> bool:
    return any(_node_is_malformed(child) for child in _iter_all_nodes(node))


# -- JavaScript / TypeScript scope and binding predicates -------------------

def _has_callable_ancestor(node: Any) -> bool:
    callable_types = {
        "function_declaration", "function_expression", "arrow_function", "generator_function",
        "generator_function_declaration", "method_definition",
    }
    parent = node.parent
    while parent is not None:
        if parent.type in callable_types:
            return True
        parent = parent.parent
    return False


def _at_module_scope(node: Any) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {
            "function_declaration", "function_expression", "arrow_function",
            "generator_function", "generator_function_declaration", "method_definition",
            "class_body", "object",
        }:
            return False
        if parent.type in {"program", "source_file"}:
            return True
        parent = parent.parent
    return False


def _is_named_module_variable(node: Any) -> bool:
    parent = node.parent
    if parent is None or parent.type != "variable_declarator":
        return False
    if parent.child_by_field_name("value") != node:
        return False
    name = parent.child_by_field_name("name")
    return bool(name is not None and name.type in {"identifier", "property_identifier"} and _at_module_scope(parent))


def _named_object_container(node: Any) -> bool:
    parent = node.parent
    if parent is None or parent.type != "object":
        return False
    container = parent.parent
    return bool(
        container is not None
        and container.type == "variable_declarator"
        and container.child_by_field_name("value") == parent
        and _at_module_scope(container)
    )


def _async_generator(node: Any, source: bytes) -> tuple[bool, bool]:
    prefix = _node_text(node, source).lstrip()[:80]
    async_value = prefix.startswith("async ")
    generator = "function*" in prefix or "function *" in prefix
    if node.type in {"generator_function", "generator_function_declaration"}:
        generator = True
    if node.type == "method_definition":
        opening = prefix.split("(", 1)[0]
        generator = generator or "*" in opening
    return async_value, generator


# -- Java owner classification ----------------------------------------------

def _java_method_owner(node: Any) -> str | None:
    # `enum_body` belongs here: a method declared in an enum body is an ordinary
    # method with a body and a named owner, and the owner set below already says
    # so by listing `enum_declaration`. Without it that entry was unreachable and
    # every enum method was silently dropped from `methods_functions` — counted
    # as neither a class method nor an anonymous-class method.
    #
    # Found by differential validation: the independent javac reference counted
    # 2 methods in `TailRecursive.java` where Metrolith counted 1, and the
    # difference was the method inside its `enum $` body.
    #
    # `annotation_type_body` is deliberately NOT added. Annotation elements
    # parse as `annotation_type_element_declaration`, not `method_declaration`,
    # so they never reach this function; adding it would suggest a behaviour
    # change that does not exist.
    body = node.parent
    if body is not None and body.type == "enum_body_declarations":
        # tree-sitter-java puts an extra `enum_body_declarations` level between
        # an enum's body and its members, so the method's direct parent is not
        # the body itself.
        body = body.parent
    if (
        body is None
        or body.type not in {"class_body", "interface_body", "enum_body"}
        or body.parent is None
    ):
        return None
    owner = body.parent
    if owner.type in {
        "class_declaration", "record_declaration", "interface_declaration",
        "enum_declaration", "annotation_type_declaration",
    }:
        return "named"
    if owner.type == "object_creation_expression":
        return "anonymous"
    return None


# -- CommonJS binding predicates --------------------------------------------

def _member_path(node: Any, source: bytes) -> list[str] | None:
    if node.type in {"identifier", "property_identifier"}:
        return [_node_text(node, source)]
    if node.type != "member_expression":
        return None
    object_node = node.child_by_field_name("object")
    property_node = node.child_by_field_name("property")
    if object_node is None or property_node is None or property_node.type != "property_identifier":
        return None
    prefix = _member_path(object_node, source)
    return prefix + [_node_text(property_node, source)] if prefix else None


def _commonjs_export_name(assignment: Any, source: bytes) -> str | None:
    if assignment.type != "assignment_expression" or not _at_module_scope(assignment):
        return None
    left = assignment.child_by_field_name("left")
    path = _member_path(left, source) if left is not None else None
    if path == ["module", "exports"]:
        return "default"
    if path and len(path) == 3 and path[:2] == ["module", "exports"]:
        return path[2]
    if path and len(path) == 2 and path[0] == "exports":
        return path[1]
    return None


def _commonjs_assignment(node: Any, source: bytes) -> str | None:
    parent = node.parent
    if parent is None or parent.type != "assignment_expression":
        return None
    if parent.child_by_field_name("right") != node:
        return None
    return _commonjs_export_name(parent, source)


def _stably_assigned_class(node: Any, source: bytes) -> bool:
    return _is_named_module_variable(node) or _commonjs_assignment(node, source) is not None
