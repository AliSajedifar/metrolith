"""D3-A structural canonicalization for admitted duplication candidates.

The canonical byte stream is the equality relation.  The SHA-256 fingerprint
is only a deterministic index; this module deliberately contains no grouping,
occurrence identity, persistence, output, or CLI behavior.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from modules.duplication.model import Candidate
from modules.source_frontend import SelectedSyntax


STRUCTURAL_FINGERPRINT_VERSION = "structural-v1"
STRUCTURAL_CANONICAL_MAGIC = b"ARCHLENS-DUPLICATION-STRUCTURAL\x00"
_FINGERPRINT_NAMESPACE = b"archlens-duplication-fingerprint"


class StructuralUnavailableReason(str, Enum):
    """Stable reasons why the frozen structural contract cannot represent input."""

    SYNTAX_UNAVAILABLE = "syntax_unavailable"
    CANDIDATE_NOT_ADMITTED = "candidate_not_admitted"
    LANGUAGE_MISMATCH = "language_mismatch"
    SPAN_OUTSIDE_SOURCE = "span_outside_source"
    STATEMENT_SEQUENCE_NOT_FOUND = "statement_sequence_not_found"
    MALFORMED_NODE = "malformed_node"
    INVALID_UTF8 = "invalid_utf8"
    UNKNOWN_IDENTIFIER_SHAPE = "unknown_identifier_shape"
    UNKNOWN_SEMANTIC_TOKEN = "unknown_semantic_token"
    UNKNOWN_PYTHON_STRING_FIELD = "unknown_python_string_field"
    UNSUPPORTED_LANGUAGE = "unsupported_language"


class StructuralCanonicalizationUnavailable(RuntimeError):
    """Typed, fail-closed structural unavailability."""

    def __init__(self, reason: StructuralUnavailableReason, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


class StructuralCanonicalizationStatus(str, Enum):
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class StructuralCanonicalizationResult:
    """One candidate's typed D3-A result, without occurrence or group data."""

    language: str
    status: StructuralCanonicalizationStatus
    canonical_bytes: bytes | None = None
    fingerprint: str | None = None
    fingerprint_version: str = STRUCTURAL_FINGERPRINT_VERSION
    unavailable_reason: StructuralUnavailableReason | None = None
    detail: str | None = None

    @property
    def available(self) -> bool:
        return self.status is StructuralCanonicalizationStatus.COMPLETE


def _frame(value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    if len(data) >= 1 << 64:
        raise OverflowError("canonical field exceeds unsigned 64-bit framing")
    return len(data).to_bytes(8, "big") + data


class _Encoder:
    def __init__(self, language: str):
        self._output = bytearray(STRUCTURAL_CANONICAL_MAGIC)
        self.emit(STRUCTURAL_FINGERPRINT_VERSION)
        self.emit(language)

    def emit(self, value: str | bytes) -> None:
        self._output.extend(_frame(value))

    def finish(self) -> bytes:
        return bytes(self._output)


class _Identifiers:
    """Candidate-local, role-local ordinal namespaces."""

    def __init__(self) -> None:
        self._roles: dict[str, dict[str, int]] = {
            "value": {},
            "type": {},
            "member": {},
            "label": {},
        }

    def ordinal(self, role: str, spelling: str) -> int:
        namespace = self._roles[role]
        if spelling not in namespace:
            namespace[spelling] = len(namespace)
        return namespace[spelling]


def _identifier(encoder: _Encoder, identifiers: _Identifiers, role: str, spelling: str) -> None:
    encoder.emit("identifier")
    encoder.emit(role)
    encoder.emit(identifiers.ordinal(role, spelling).to_bytes(8, "big"))


def _node_start(encoder: _Encoder, kind: str) -> None:
    encoder.emit("node:start")
    encoder.emit(kind)


def _node_end(encoder: _Encoder) -> None:
    encoder.emit("node:end")


def _edge_start(encoder: _Encoder, edge: str) -> None:
    encoder.emit("edge:start")
    encoder.emit(edge)


def _edge_end(encoder: _Encoder) -> None:
    encoder.emit("edge:end")


def _python_line_start(syntax: SelectedSyntax, line: int) -> int:
    starts = syntax.mapping.selected_line_starts
    if not (1 <= line <= len(starts)):
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.STATEMENT_SEQUENCE_NOT_FOUND,
            f"Python AST line {line} is outside selected source",
        )
    return starts[line - 1]


def _python_span(syntax: SelectedSyntax, node: ast.AST) -> tuple[int, int] | None:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return None
    start = _python_line_start(syntax, int(node.lineno)) + int(node.col_offset)
    end = _python_line_start(syntax, int(node.end_lineno)) + int(node.end_col_offset)
    return start, end


def _python_statement_sequence(syntax: SelectedSyntax, candidate: Candidate) -> tuple[ast.stmt, ...]:
    matches: list[tuple[ast.stmt, ...]] = []
    assert isinstance(syntax.root, ast.AST)
    for parent in ast.walk(syntax.root):
        for _field, value in ast.iter_fields(parent):
            if not isinstance(value, list) or not value or not all(
                isinstance(item, ast.stmt) for item in value
            ):
                continue
            statements = tuple(value)
            first = _python_span(syntax, statements[0])
            last = _python_span(syntax, statements[-1])
            if first is None or last is None:
                continue
            if (
                first[0] == candidate.span.start_byte
                and last[1] == candidate.span.end_byte
            ):
                matches.append(statements)
    if not matches:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.STATEMENT_SEQUENCE_NOT_FOUND,
            "candidate span does not identify a complete Python statement list",
        )
    # Identical AST object sequences can be observed through compatibility
    # aliases, but distinct sequences for one span would be adapter ambiguity.
    unique = {tuple(id(statement) for statement in match): match for match in matches}
    if len(unique) != 1:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.STATEMENT_SEQUENCE_NOT_FOUND,
            "candidate span identifies multiple Python statement lists",
        )
    return next(iter(unique.values()))


def _python_literal_class(value: object) -> str:
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
    raise StructuralCanonicalizationUnavailable(
        StructuralUnavailableReason.MALFORMED_NODE,
        f"unsupported Python Constant value type: {type(value).__name__}",
    )


_PYTHON_TYPE_FIELDS = frozenset(
    {
        "annotation",
        "returns",
        "type_params",
        "bases",
        "bound",
        "constraints",
    }
)


def _python_identifier_field(
    node: ast.AST,
    field: str,
    parent: ast.AST | None,
    type_context: bool,
) -> str | None:
    if isinstance(node, ast.Name) and field == "id":
        return "type" if type_context else "value"
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
    capture_types = tuple(
        value
        for value in (
            getattr(ast, "MatchAs", None),
            getattr(ast, "MatchStar", None),
            getattr(ast, "MatchMapping", None),
        )
        if value is not None
    )
    if capture_types and isinstance(node, capture_types) and field in {"name", "rest"}:
        return "value"
    if hasattr(ast, "MatchClass") and isinstance(node, ast.MatchClass) and field == "kwd_attrs":
        return "member"
    return None


def _python_node(
    encoder: _Encoder,
    identifiers: _Identifiers,
    node: ast.AST,
    *,
    parent: ast.AST | None,
    type_context: bool,
) -> None:
    kind = type(node).__name__
    _node_start(encoder, kind)
    if isinstance(node, ast.Constant):
        encoder.emit("literal")
        encoder.emit(_python_literal_class(node.value))
        _node_end(encoder)
        return

    for field, value in ast.iter_fields(node):
        if isinstance(node, ast.Constant) and field in {"value", "kind"}:
            continue
        field_type_context = type_context or field in _PYTHON_TYPE_FIELDS
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias) and field == "value":
            field_type_context = True
        role = _python_identifier_field(node, field, parent, type_context)
        _edge_start(encoder, field)
        if role is not None:
            if value is None:
                encoder.emit("none")
            elif isinstance(value, str):
                _identifier(encoder, identifiers, role, value)
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                encoder.emit("list:start")
                for item in value:
                    _identifier(encoder, identifiers, role, item)
                encoder.emit("list:end")
            elif isinstance(value, ast.Name) and role == "type":
                _python_node(
                    encoder,
                    identifiers,
                    value,
                    parent=node,
                    type_context=True,
                )
            else:
                raise StructuralCanonicalizationUnavailable(
                    StructuralUnavailableReason.UNKNOWN_IDENTIFIER_SHAPE,
                    f"unexpected {kind}.{field} identifier representation",
                )
        elif isinstance(value, ast.AST):
            _python_node(
                encoder,
                identifiers,
                value,
                parent=node,
                type_context=field_type_context,
            )
        elif isinstance(value, list):
            encoder.emit("list:start")
            for item in value:
                if isinstance(item, ast.AST):
                    _python_node(
                        encoder,
                        identifiers,
                        item,
                        parent=node,
                        type_context=field_type_context,
                    )
                elif item is None:
                    # Dict.keys uses None to retain the ** expansion boundary.
                    encoder.emit("none")
                elif isinstance(item, int):
                    encoder.emit("integer")
                    encoder.emit(str(item))
                else:
                    raise StructuralCanonicalizationUnavailable(
                        StructuralUnavailableReason.MALFORMED_NODE,
                        f"unsupported item in Python {kind}.{field}",
                    )
            encoder.emit("list:end")
        elif value is None:
            encoder.emit("none")
        elif isinstance(value, bool):
            encoder.emit("boolean")
            encoder.emit("true" if value else "false")
        elif isinstance(value, int):
            encoder.emit("integer")
            encoder.emit(str(value))
        elif isinstance(value, str):
            raise StructuralCanonicalizationUnavailable(
                StructuralUnavailableReason.UNKNOWN_PYTHON_STRING_FIELD,
                f"unclassified Python string field {kind}.{field}",
            )
        else:
            raise StructuralCanonicalizationUnavailable(
                StructuralUnavailableReason.MALFORMED_NODE,
                f"unsupported Python field {kind}.{field}: {type(value).__name__}",
            )
        _edge_end(encoder)
    _node_end(encoder)


_COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment"})
_NON_SEMANTIC_TOKENS = frozenset(
    {
        "(",
        ")",
        "{",
        "}",
        ",",
        ";",
        ".",
        "`",
        "${",
        "</",
        "/>",
    }
)
_SEMANTIC_SYMBOLS = frozenset(
    {
        "+",
        "-",
        "*",
        "/",
        "%",
        "**",
        "&",
        "|",
        "^",
        "~",
        "!",
        "&&",
        "||",
        "??",
        "<",
        "<=",
        ">",
        ">=",
        "==",
        "===",
        "!=",
        "!==",
        "<<",
        ">>",
        ">>>",
        "&^",
        "=",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&=",
        "|=",
        "^=",
        "<<=",
        ">>=",
        ">>>=",
        "&^=",
        "**=",
        "&&=",
        "||=",
        "??=",
        "++",
        "--",
        ":=",
        "<-",
        "->",
        "=>",
        "::",
        "...",
        "?",
        ":",
        "@",
        "?.",
        "-?",
        "+?",
        "[",
        "]",
    }
)


def _tree_text(syntax: SelectedSyntax, node: Any) -> str:
    value = syntax.selected_source[node.start_byte : node.end_byte]
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.INVALID_UTF8,
            f"{node.type} leaf is not UTF-8",
        ) from exc


def _tree_literal_class(syntax: SelectedSyntax, node: Any) -> str | None:
    kind = node.type
    language = syntax.language
    spelling = None
    if language == "Java":
        direct = {
            "null_literal": "null",
            "true": "boolean",
            "false": "boolean",
            "character_literal": "char",
        }
        if kind in direct:
            return direct[kind]
        if kind == "string_literal":
            spelling = _tree_text(syntax, node)
            return "text_block" if spelling.startswith('"""') else "string"
        if kind in {
            "decimal_integer_literal",
            "hex_integer_literal",
            "octal_integer_literal",
            "binary_integer_literal",
        }:
            spelling = _tree_text(syntax, node)
            return "long_integer" if spelling[-1:].lower() == "l" else "integer"
        if kind in {"decimal_floating_point_literal", "hex_floating_point_literal"}:
            spelling = _tree_text(syntax, node)
            return "float" if spelling[-1:].lower() == "f" else "double"
    elif language == "Go":
        return {
            "nil": "nil",
            "true": "boolean",
            "false": "boolean",
            "int_literal": "integer",
            "float_literal": "floating",
            "imaginary_literal": "imaginary",
            "rune_literal": "rune",
            "interpreted_string_literal": "interpreted_string",
            "raw_string_literal": "raw_string",
        }.get(kind)
    elif language in {"JavaScript", "TypeScript"}:
        if kind == "null":
            return "null"
        if kind in {"true", "false"}:
            return "boolean"
        if kind == "number":
            spelling = _tree_text(syntax, node)
            return "bigint" if spelling.lower().endswith("n") else "number"
        if kind == "string":
            return "string"
        if kind == "regex":
            return "regex"
    return None


_IDENTIFIER_TYPES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "package_identifier",
        "property_identifier",
        "private_property_identifier",
        "shorthand_property_identifier",
        "statement_identifier",
    }
)


def _has_ancestor(ancestors: tuple[tuple[Any, str | None], ...], kinds: Iterable[str]) -> bool:
    wanted = frozenset(kinds)
    return any(node.type in wanted for node, _edge in ancestors)


def _tree_identifier_role(
    language: str,
    node: Any,
    parent: Any | None,
    edge: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> str:
    kind = node.type
    parent_kind = parent.type if parent is not None else ""
    if language == "Java":
        if kind == "type_identifier" or _has_ancestor(ancestors, {"scoped_type_identifier"}):
            return "type"
        if parent_kind in {
            "annotation_type_declaration",
            "class_declaration",
            "enum_declaration",
            "interface_declaration",
            "record_declaration",
        } and edge == "name":
            return "type"
        if parent_kind in {"constructor_declaration", "compact_constructor_declaration"} and edge == "name":
            return "type"
        if parent_kind in {"labeled_statement", "break_statement", "continue_statement"}:
            return "label"
        if parent_kind in {"method_declaration", "method_invocation"} and edge == "name":
            return "member"
        if parent_kind in {"annotation", "marker_annotation"} and edge == "name":
            return "type"
        if parent_kind in {
            "annotation_type_element_declaration",
            "enum_constant",
            "method_reference",
        } and edge in {None, "name"}:
            return "member"
        if parent_kind == "field_access" and edge == "field":
            return "member"
        if _has_ancestor(ancestors, {"field_declaration"}) and parent_kind == "variable_declarator" and edge == "name":
            return "member"
        return "value"
    if language == "Go":
        if kind == "type_identifier":
            return "type"
        if kind == "field_identifier":
            return "member"
        if parent_kind in {
            "labeled_statement",
            "break_statement",
            "continue_statement",
            "goto_statement",
        }:
            return "label"
        return "value"
    if language in {"JavaScript", "TypeScript"}:
        if kind == "statement_identifier":
            return "label"
        if kind in {
            "property_identifier",
            "private_property_identifier",
            "shorthand_property_identifier",
        }:
            return "member"
        if _has_ancestor(
            ancestors,
            {
                "jsx_attribute",
                "jsx_closing_element",
                "jsx_member_expression",
                "jsx_namespace_name",
                "jsx_opening_element",
                "jsx_self_closing_element",
            },
        ):
            return "member"
        if language == "TypeScript" and (
            kind == "type_identifier"
            or _has_ancestor(
                ancestors,
                {
                    "nested_type_identifier",
                    "type_annotation",
                    "type_arguments",
                    "type_parameters",
                    "generic_type",
                    "predefined_type",
                    "type_query",
                    "index_type_query",
                    "lookup_type",
                    "implements_clause",
                    "extends_type_clause",
                    "type_predicate",
                    "type_alias_declaration",
                    "interface_declaration",
                },
            )
        ):
            return "type"
        if parent_kind in {
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "type_parameter",
        } and edge == "name":
            return "type"
        if parent_kind == "new_expression" and edge in {"constructor", "function"}:
            return "type"
        if _has_ancestor(ancestors, {"class_heritage", "extends_clause"}):
            return "type"
        return "value"
    raise StructuralCanonicalizationUnavailable(
        StructuralUnavailableReason.UNSUPPORTED_LANGUAGE,
        f"identifier role adapter missing for {language}",
    )


def _is_comment(node: Any) -> bool:
    return node.type in _COMMENT_TYPES or "comment" in node.type


def _anonymous_semantic_kind(node: Any) -> str | None:
    kind = node.type
    if kind in _NON_SEMANTIC_TOKENS:
        return None
    if kind in _SEMANTIC_SYMBOLS or kind.replace("-", "_").isidentifier():
        return kind
    raise StructuralCanonicalizationUnavailable(
        StructuralUnavailableReason.UNKNOWN_SEMANTIC_TOKEN,
        f"unclassified anonymous grammar token {kind!r}",
    )


def _tree_child_retained(node: Any) -> bool:
    if _is_comment(node):
        return False
    if node.is_named:
        return True
    return _anonymous_semantic_kind(node) is not None


def _tree_node(
    encoder: _Encoder,
    identifiers: _Identifiers,
    syntax: SelectedSyntax,
    node: Any,
    *,
    parent: Any | None,
    edge: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> None:
    if node.is_error or node.is_missing or node.type == "ERROR":
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.MALFORMED_NODE,
            f"malformed tree-sitter node {node.type}",
        )
    if not node.is_named:
        semantic = _anonymous_semantic_kind(node)
        if semantic is not None:
            encoder.emit("semantic-token")
            encoder.emit(semantic)
        return

    literal_class = _tree_literal_class(syntax, node)
    _node_start(encoder, node.type)
    if literal_class is not None:
        encoder.emit("literal")
        encoder.emit(literal_class)
        _node_end(encoder)
        return

    if node.type in _IDENTIFIER_TYPES:
        if node.children:
            raise StructuralCanonicalizationUnavailable(
                StructuralUnavailableReason.UNKNOWN_IDENTIFIER_SHAPE,
                f"identifier node {node.type} unexpectedly has children",
            )
        spelling = _tree_text(syntax, node)
        role = _tree_identifier_role(syntax.language, node, parent, edge, ancestors)
        _identifier(encoder, identifiers, role, spelling)
        _node_end(encoder)
        return
    if "identifier" in node.type and not node.children:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.UNKNOWN_IDENTIFIER_SHAPE,
            f"unclassified identifier node {node.type}",
        )

    if node.type in {"string_fragment", "escape_sequence"} and _has_ancestor(
        ancestors, {"template_string"}
    ):
        encoder.emit("literal-chunk")
        encoder.emit(node.type)
        _node_end(encoder)
        return

    next_ancestors = (*ancestors, (node, edge))
    for index, child in enumerate(node.children):
        if not _tree_child_retained(child):
            continue
        child_edge = node.field_name_for_child(index)
        _edge_start(encoder, child_edge or "")
        _tree_node(
            encoder,
            identifiers,
            syntax,
            child,
            parent=node,
            edge=child_edge,
            ancestors=next_ancestors,
        )
        _edge_end(encoder)
    _node_end(encoder)


def _tree_statement_sequence(syntax: SelectedSyntax, candidate: Candidate) -> tuple[Any, ...]:
    start, end = candidate.span.start_byte, candidate.span.end_byte
    roots: list[Any] = []

    def descend(node: Any) -> None:
        if node.end_byte <= start or node.start_byte >= end or _is_comment(node):
            return
        if start <= node.start_byte and node.end_byte <= end:
            roots.append(node)
            return
        for child in node.children:
            descend(child)

    descend(syntax.root)
    roots = [node for node in roots if _tree_child_retained(node)]
    if (
        not roots
        or not all(node.is_named for node in roots)
        or roots[0].start_byte != start
        or roots[-1].end_byte != end
    ):
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.STATEMENT_SEQUENCE_NOT_FOUND,
            "candidate span does not identify a complete tree-sitter statement sequence",
        )
    return tuple(roots)


def _validate_input(syntax: SelectedSyntax, candidate: Candidate) -> None:
    if not syntax.eligible_for_duplication:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.SYNTAX_UNAVAILABLE,
            "selected syntax is unavailable for duplication",
        )
    if not candidate.admitted:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.CANDIDATE_NOT_ADMITTED,
            "candidate did not meet the frozen D1 floors",
        )
    if candidate.language != syntax.language:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.LANGUAGE_MISMATCH,
            f"candidate language {candidate.language!r} != syntax {syntax.language!r}",
        )
    if not (
        0 <= candidate.span.start_byte < candidate.span.end_byte <= len(syntax.selected_source)
    ):
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.SPAN_OUTSIDE_SOURCE,
            "candidate span is outside selected source",
        )


def canonical_structural_bytes(syntax: SelectedSyntax, candidate: Candidate) -> bytes:
    """Return canonical bytes for one admitted candidate under structural-v1."""
    _validate_input(syntax, candidate)
    encoder = _Encoder(syntax.language)
    identifiers = _Identifiers()
    encoder.emit("statement-sequence:start")
    if syntax.language == "Python":
        if not isinstance(syntax.root, ast.AST):
            raise StructuralCanonicalizationUnavailable(
                StructuralUnavailableReason.SYNTAX_UNAVAILABLE,
                "Python selected AST is missing",
            )
        statements = _python_statement_sequence(syntax, candidate)
        for statement in statements:
            _edge_start(encoder, "statement")
            _python_node(
                encoder,
                identifiers,
                statement,
                parent=None,
                type_context=False,
            )
            _edge_end(encoder)
    elif syntax.language in {"Java", "Go", "JavaScript", "TypeScript"}:
        if syntax.root is None:
            raise StructuralCanonicalizationUnavailable(
                StructuralUnavailableReason.SYNTAX_UNAVAILABLE,
                "selected tree-sitter root is missing",
            )
        for statement in _tree_statement_sequence(syntax, candidate):
            _edge_start(encoder, "statement")
            _tree_node(
                encoder,
                identifiers,
                syntax,
                statement,
                parent=None,
                edge="statement",
                ancestors=(),
            )
            _edge_end(encoder)
    else:
        raise StructuralCanonicalizationUnavailable(
            StructuralUnavailableReason.UNSUPPORTED_LANGUAGE,
            f"unsupported structural language: {syntax.language!r}",
        )
    encoder.emit("statement-sequence:end")
    return encoder.finish()


def _canonical_header(canonical_bytes: bytes) -> tuple[str, str]:
    if not canonical_bytes.startswith(STRUCTURAL_CANONICAL_MAGIC):
        raise ValueError("canonical bytes have the wrong structural magic")
    offset = len(STRUCTURAL_CANONICAL_MAGIC)
    values: list[str] = []
    for field_name in ("version", "language"):
        if offset + 8 > len(canonical_bytes):
            raise ValueError(f"canonical bytes truncate the {field_name} frame")
        length = int.from_bytes(canonical_bytes[offset : offset + 8], "big")
        offset += 8
        if offset + length > len(canonical_bytes):
            raise ValueError(f"canonical bytes truncate the {field_name} value")
        try:
            values.append(canonical_bytes[offset : offset + length].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"canonical {field_name} is not UTF-8") from exc
        offset += length
    return values[0], values[1]


def structural_fingerprint(
    language: str,
    canonical_bytes: bytes,
    *,
    digest: Callable[[bytes], bytes] | None = None,
) -> str:
    """Return a deterministic index; canonical-byte equality remains authoritative."""
    canonical_version, canonical_language = _canonical_header(canonical_bytes)
    if canonical_version != STRUCTURAL_FINGERPRINT_VERSION:
        raise ValueError(
            f"canonical version {canonical_version!r} is not "
            f"{STRUCTURAL_FINGERPRINT_VERSION!r}"
        )
    if canonical_language != language:
        raise ValueError(
            f"canonical language {canonical_language!r} does not match {language!r}"
        )
    payload = b"".join(
        (
            _frame(_FINGERPRINT_NAMESPACE),
            _frame(STRUCTURAL_FINGERPRINT_VERSION),
            _frame(language),
            _frame(canonical_bytes),
        )
    )
    value = hashlib.sha256(payload).digest() if digest is None else digest(payload)
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("structural digest function must return exactly 32 bytes")
    return "sha256:" + value.hex()


def canonicalize_structural(
    syntax: SelectedSyntax,
    candidate: Candidate,
    *,
    digest: Callable[[bytes], bytes] | None = None,
) -> StructuralCanonicalizationResult:
    """Canonicalize and fingerprint with typed fail-closed unavailability."""
    try:
        canonical = canonical_structural_bytes(syntax, candidate)
        fingerprint = structural_fingerprint(syntax.language, canonical, digest=digest)
        return StructuralCanonicalizationResult(
            language=syntax.language,
            status=StructuralCanonicalizationStatus.COMPLETE,
            canonical_bytes=canonical,
            fingerprint=fingerprint,
        )
    except StructuralCanonicalizationUnavailable as exc:
        return StructuralCanonicalizationResult(
            language=syntax.language,
            status=StructuralCanonicalizationStatus.UNAVAILABLE,
            unavailable_reason=exc.reason,
            detail=exc.detail,
        )


__all__ = [
    "STRUCTURAL_CANONICAL_MAGIC",
    "STRUCTURAL_FINGERPRINT_VERSION",
    "StructuralCanonicalizationResult",
    "StructuralCanonicalizationStatus",
    "StructuralCanonicalizationUnavailable",
    "StructuralUnavailableReason",
    "canonical_structural_bytes",
    "canonicalize_structural",
    "structural_fingerprint",
]
