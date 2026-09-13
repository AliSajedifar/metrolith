"""Independent D3-A structural byte oracle.

This module intentionally imports no ``modules.duplication`` code.  It builds
its own event list, role tables, literal table, statement selection, framing,
and traversal from the frozen D3-A contract.
"""

from __future__ import annotations

import ast
import hashlib
from typing import Any


MAGIC = b"ARCHLENS-DUPLICATION-STRUCTURAL\x00"
VERSION = "structural-v1"
FINGERPRINT_NAMESPACE = b"archlens-duplication-fingerprint"


def _framed(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return len(raw).to_bytes(8, "big") + raw


class _Events:
    def __init__(self, language: str):
        self.values: list[str | bytes] = [VERSION, language, "statement-sequence:start"]
        self.names: dict[str, dict[str, int]] = {
            role: {} for role in ("value", "type", "member", "label")
        }

    def add(self, *values: str | bytes) -> None:
        self.values.extend(values)

    def identifier(self, role: str, spelling: str) -> None:
        names = self.names[role]
        ordinal = names.setdefault(spelling, len(names))
        self.add("identifier", role, ordinal.to_bytes(8, "big"))

    def node(self, kind: str) -> None:
        self.add("node:start", kind)

    def edge(self, name: str) -> None:
        self.add("edge:start", name)

    def bytes(self) -> bytes:
        self.add("statement-sequence:end")
        return MAGIC + b"".join(_framed(value) for value in self.values)


def _py_span(syntax: Any, node: ast.AST) -> tuple[int, int] | None:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return None
    starts = syntax.mapping.selected_line_starts
    return (
        starts[int(node.lineno) - 1] + int(node.col_offset),
        starts[int(node.end_lineno) - 1] + int(node.end_col_offset),
    )


def _py_statements(syntax: Any, candidate: Any) -> tuple[ast.stmt, ...]:
    found: dict[tuple[int, ...], tuple[ast.stmt, ...]] = {}
    for owner in ast.walk(syntax.root):
        for _field, value in ast.iter_fields(owner):
            if not isinstance(value, list) or not value or not all(
                isinstance(item, ast.stmt) for item in value
            ):
                continue
            statements = tuple(value)
            first, last = _py_span(syntax, statements[0]), _py_span(syntax, statements[-1])
            if (
                first is not None
                and last is not None
                and first[0] == candidate.span.start_byte
                and last[1] == candidate.span.end_byte
            ):
                found[tuple(id(item) for item in statements)] = statements
    if len(found) != 1:
        raise ValueError("oracle could not identify one Python statement sequence")
    return next(iter(found.values()))


def _py_literal(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, complex):
        return "complex"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "bytes"
    if value is Ellipsis:
        return "ellipsis"
    raise ValueError(f"oracle unsupported Python literal: {type(value).__name__}")


def _py_role(node: ast.AST, field: str, parent: ast.AST | None, in_type: bool) -> str | None:
    if isinstance(node, ast.Name) and field == "id":
        return "type" if in_type else "value"
    if isinstance(node, ast.Attribute) and field == "attr":
        return "member"
    if isinstance(node, ast.arg) and field == "arg":
        return "value"
    if isinstance(node, ast.keyword) and field == "arg":
        return "member"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and field == "name":
        return "member" if isinstance(parent, ast.ClassDef) else "value"
    if isinstance(node, ast.ClassDef) and field == "name":
        return "type"
    if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias) and field == "name":
        return "type"
    if isinstance(node, ast.alias) and field in {"name", "asname"}:
        return "value"
    if isinstance(node, ast.ImportFrom) and field == "module":
        return "value"
    if isinstance(node, ast.ExceptHandler) and field == "name":
        return "value"
    if isinstance(node, (ast.Global, ast.Nonlocal)) and field == "names":
        return "value"
    captures = tuple(
        item
        for item in (
            getattr(ast, "MatchAs", None),
            getattr(ast, "MatchStar", None),
            getattr(ast, "MatchMapping", None),
        )
        if item is not None
    )
    if captures and isinstance(node, captures) and field in {"name", "rest"}:
        return "value"
    if hasattr(ast, "MatchClass") and isinstance(node, ast.MatchClass) and field == "kwd_attrs":
        return "member"
    return None


_PY_TYPE_EDGES = {"annotation", "returns", "type_params", "bases", "bound", "constraints"}


def _py_node(
    events: _Events,
    node: ast.AST,
    parent: ast.AST | None = None,
    in_type: bool = False,
) -> None:
    events.node(type(node).__name__)
    if isinstance(node, ast.Constant):
        events.add("literal", _py_literal(node.value), "node:end")
        return
    for field, value in ast.iter_fields(node):
        role = _py_role(node, field, parent, in_type)
        child_type = in_type or field in _PY_TYPE_EDGES
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias) and field == "value":
            child_type = True
        events.edge(field)
        if role is not None:
            if value is None:
                events.add("none")
            elif isinstance(value, str):
                events.identifier(role, value)
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                events.add("list:start")
                for item in value:
                    events.identifier(role, item)
                events.add("list:end")
            elif isinstance(value, ast.Name) and role == "type":
                _py_node(events, value, node, True)
            else:
                raise ValueError(f"oracle unknown Python identifier {type(node).__name__}.{field}")
        elif isinstance(value, ast.AST):
            _py_node(events, value, node, child_type)
        elif isinstance(value, list):
            events.add("list:start")
            for item in value:
                if isinstance(item, ast.AST):
                    _py_node(events, item, node, child_type)
                elif item is None:
                    events.add("none")
                elif isinstance(item, int):
                    events.add("integer", str(item))
                else:
                    raise ValueError(f"oracle unsupported Python list field {field}")
            events.add("list:end")
        elif value is None:
            events.add("none")
        elif isinstance(value, bool):
            events.add("boolean", "true" if value else "false")
        elif isinstance(value, int):
            events.add("integer", str(value))
        else:
            raise ValueError(f"oracle unsupported Python scalar {type(node).__name__}.{field}")
        events.add("edge:end")
    events.add("node:end")


_IGNORED = {"(", ")", "{", "}", ",", ";", ".", "`", "${", "</", "/>"}
_SYMBOLS = {
    "+", "-", "*", "/", "%", "**", "&", "|", "^", "~", "!",
    "&&", "||", "??", "<", "<=", ">", ">=", "==", "===", "!=", "!==",
    "<<", ">>", ">>>", "&^", "=", "+=", "-=", "*=", "/=", "%=", "&=",
    "|=", "^=", "<<=", ">>=", ">>>=", "&^=", "**=", "&&=", "||=", "??=",
    "++", "--", ":=", "<-", "->", "=>", "::", "...", "?", ":", "@",
    "?.", "-?", "+?", "[", "]",
}
_ID_KINDS = {
    "identifier", "type_identifier", "field_identifier", "package_identifier",
    "property_identifier", "private_property_identifier",
    "shorthand_property_identifier", "statement_identifier",
}


def _comment(node: Any) -> bool:
    return "comment" in node.type


def _anonymous(node: Any) -> str | None:
    if node.type in _IGNORED:
        return None
    if node.type in _SYMBOLS or node.type.replace("-", "_").isidentifier():
        return node.type
    raise ValueError(f"oracle unknown anonymous token: {node.type}")


def _kept(node: Any) -> bool:
    return not _comment(node) and (node.is_named or _anonymous(node) is not None)


def _text(syntax: Any, node: Any) -> str:
    return syntax.selected_source[node.start_byte : node.end_byte].decode("utf-8")


def _literal(syntax: Any, node: Any) -> str | None:
    kind, language = node.type, syntax.language
    if language == "Java":
        fixed = {"null_literal": "null", "true": "boolean", "false": "boolean", "character_literal": "char"}
        if kind in fixed:
            return fixed[kind]
        if kind == "string_literal":
            return "text_block" if _text(syntax, node).startswith('"""') else "string"
        if kind in {"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal"}:
            return "long_integer" if _text(syntax, node)[-1:].lower() == "l" else "integer"
        if kind in {"decimal_floating_point_literal", "hex_floating_point_literal"}:
            return "float" if _text(syntax, node)[-1:].lower() == "f" else "double"
    if language == "Go":
        return {
            "nil": "nil", "true": "boolean", "false": "boolean", "int_literal": "integer",
            "float_literal": "floating", "imaginary_literal": "imaginary", "rune_literal": "rune",
            "interpreted_string_literal": "interpreted_string", "raw_string_literal": "raw_string",
        }.get(kind)
    if language in {"JavaScript", "TypeScript"}:
        if kind == "null":
            return "null"
        if kind in {"true", "false"}:
            return "boolean"
        if kind == "number":
            return "bigint" if _text(syntax, node).lower().endswith("n") else "number"
        if kind == "string":
            return "string"
        if kind == "regex":
            return "regex"
    return None


def _under(ancestors: tuple[tuple[Any, str | None], ...], kinds: set[str]) -> bool:
    return any(node.type in kinds for node, _edge in ancestors)


def _role(
    language: str,
    node: Any,
    parent: Any | None,
    edge: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> str:
    kind, owner = node.type, parent.type if parent is not None else ""
    if language == "Java":
        if kind == "type_identifier" or _under(ancestors, {"scoped_type_identifier"}):
            return "type"
        if owner in {
            "annotation_type_declaration", "class_declaration", "enum_declaration",
            "interface_declaration", "record_declaration", "constructor_declaration",
            "compact_constructor_declaration",
        } and edge == "name":
            return "type"
        if owner in {"labeled_statement", "break_statement", "continue_statement"}:
            return "label"
        if owner in {"method_declaration", "method_invocation"} and edge == "name":
            return "member"
        if owner in {"annotation", "marker_annotation"} and edge == "name":
            return "type"
        if owner in {"annotation_type_element_declaration", "enum_constant", "method_reference"} and edge in {None, "name"}:
            return "member"
        if owner == "field_access" and edge == "field":
            return "member"
        if _under(ancestors, {"field_declaration"}) and owner == "variable_declarator" and edge == "name":
            return "member"
        return "value"
    if language == "Go":
        if kind == "type_identifier":
            return "type"
        if kind == "field_identifier":
            return "member"
        if owner in {"labeled_statement", "break_statement", "continue_statement", "goto_statement"}:
            return "label"
        return "value"
    if kind == "statement_identifier":
        return "label"
    if kind in {"property_identifier", "private_property_identifier", "shorthand_property_identifier"}:
        return "member"
    if _under(ancestors, {
        "jsx_attribute", "jsx_closing_element", "jsx_member_expression", "jsx_namespace_name",
        "jsx_opening_element", "jsx_self_closing_element",
    }):
        return "member"
    if language == "TypeScript" and (
        kind == "type_identifier"
        or _under(ancestors, {
            "nested_type_identifier", "type_annotation", "type_arguments", "type_parameters",
            "generic_type", "predefined_type", "type_query", "index_type_query", "lookup_type",
            "implements_clause", "extends_type_clause", "type_predicate", "type_alias_declaration",
            "interface_declaration",
        })
    ):
        return "type"
    if owner in {
        "class_declaration", "abstract_class_declaration", "interface_declaration",
        "type_alias_declaration", "type_parameter",
    } and edge == "name":
        return "type"
    if owner == "new_expression" and edge in {"constructor", "function"}:
        return "type"
    if _under(ancestors, {"class_heritage", "extends_clause"}):
        return "type"
    return "value"


def _tree_node(
    events: _Events,
    syntax: Any,
    node: Any,
    parent: Any | None,
    edge: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> None:
    if node.is_error or node.is_missing or node.type == "ERROR":
        raise ValueError("oracle encountered malformed node")
    if not node.is_named:
        token = _anonymous(node)
        if token is not None:
            events.add("semantic-token", token)
        return
    events.node(node.type)
    literal = _literal(syntax, node)
    if literal is not None:
        events.add("literal", literal, "node:end")
        return
    if node.type in _ID_KINDS:
        if node.children:
            raise ValueError("oracle identifier unexpectedly has children")
        events.identifier(_role(syntax.language, node, parent, edge, ancestors), _text(syntax, node))
        events.add("node:end")
        return
    if "identifier" in node.type and not node.children:
        raise ValueError(f"oracle unknown identifier kind {node.type}")
    if node.type in {"string_fragment", "escape_sequence"} and _under(ancestors, {"template_string"}):
        events.add("literal-chunk", node.type, "node:end")
        return
    descendants = (*ancestors, (node, edge))
    for index, child in enumerate(node.children):
        if not _kept(child):
            continue
        child_edge = node.field_name_for_child(index)
        events.edge(child_edge or "")
        _tree_node(events, syntax, child, node, child_edge, descendants)
        events.add("edge:end")
    events.add("node:end")


def _tree_statements(syntax: Any, candidate: Any) -> tuple[Any, ...]:
    start, end = candidate.span.start_byte, candidate.span.end_byte
    result: list[Any] = []

    def visit(node: Any) -> None:
        if node.end_byte <= start or node.start_byte >= end or _comment(node):
            return
        if start <= node.start_byte and node.end_byte <= end:
            result.append(node)
            return
        for child in node.children:
            visit(child)

    visit(syntax.root)
    kept = tuple(node for node in result if _kept(node))
    if not kept or not all(node.is_named for node in kept):
        raise ValueError("oracle tree statement sequence unavailable")
    if kept[0].start_byte != start or kept[-1].end_byte != end:
        raise ValueError("oracle tree statement sequence does not cover candidate")
    return kept


def oracle_structural_bytes(syntax: Any, candidate: Any) -> bytes:
    """Independently produce the contract's complete canonical byte stream."""
    events = _Events(syntax.language)
    if syntax.language == "Python":
        statements = _py_statements(syntax, candidate)
        for statement in statements:
            events.edge("statement")
            _py_node(events, statement)
            events.add("edge:end")
    else:
        for statement in _tree_statements(syntax, candidate):
            events.edge("statement")
            _tree_node(events, syntax, statement, None, "statement", ())
            events.add("edge:end")
    return events.bytes()


def oracle_structural_fingerprint(language: str, canonical: bytes) -> str:
    """Independently calculate the contract fingerprint for known-good bytes."""
    payload = b"".join(
        (
            _framed(FINGERPRINT_NAMESPACE),
            _framed(VERSION),
            _framed(language),
            _framed(canonical),
        )
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "MAGIC",
    "VERSION",
    "oracle_structural_bytes",
    "oracle_structural_fingerprint",
]
