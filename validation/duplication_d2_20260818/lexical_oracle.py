"""Clean-room D2 lexical oracle.

This validator intentionally does not import ``modules.duplication.lexical``
or ``modules.duplication.grouping``.  It independently interprets the frozen
contract and emits canonical bytes for differential tests.
"""

from __future__ import annotations

import io
import tokenize
from typing import Any

from modules.source_frontend import SelectedSyntax


MAGIC = b"ARCHLENS-DUPLICATION-LEXICAL\x00"
VERSION = "lexical-exact-v1"
BOUNDARIES = frozenset(
    {
        "block",
        "class_body",
        "class_static_block",
        "communication_case",
        "constructor_body",
        "default_case",
        "expression_case",
        "statement_block",
        "statement_list",
        "switch_block",
        "switch_default",
        "switch_rule",
        "type_case",
    }
)


def _frame(value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return len(data).to_bytes(8, "big") + data


def _normalized(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _serialize(language: str, items: list[tuple[bytes, str, bytes]]) -> bytes:
    result = bytearray(MAGIC)
    for value in (VERSION, language, b"sequence:start"):
        result.extend(_frame(value))
    for category, kind, spelling in items:
        result.extend(_frame(category))
        result.extend(_frame(kind))
        result.extend(_frame(spelling))
    result.extend(_frame(b"sequence:end"))
    return bytes(result)


def _python_position(
    starts: tuple[int, ...], lines: tuple[str, ...], position: tuple[int, int]
) -> int:
    line, column = position
    if line == len(lines) + 1:
        return starts[-1] + (
            0 if len(starts) > len(lines) else len(lines[-1].encode("utf-8"))
        )
    return starts[line - 1] + len(lines[line - 1][:column].encode("utf-8"))


def _python(syntax: SelectedSyntax, candidate: Any) -> list[tuple[bytes, str, bytes]]:
    assert syntax.evidence_text is not None
    lines = tuple(syntax.evidence_text.splitlines(keepends=True))
    positioned = []
    for token in tokenize.generate_tokens(io.StringIO(syntax.evidence_text).readline):
        positioned.append(
            (
                token,
                _python_position(syntax.mapping.selected_line_starts, lines, token.start),
                _python_position(syntax.mapping.selected_line_starts, lines, token.end),
            )
        )
    markers = {
        tokenize.NEWLINE: "python:logical-newline",
        tokenize.INDENT: "python:indent",
        tokenize.DEDENT: "python:dedent",
    }
    ignored = {tokenize.ENCODING, tokenize.ENDMARKER, tokenize.NL, tokenize.COMMENT}
    lexical = [
        index
        for index, (token, start, end) in enumerate(positioned)
        if token.type not in ignored
        and token.type not in markers
        and candidate.span.start_byte <= start
        and end <= candidate.span.end_byte
    ]
    first, last = lexical[0], lexical[-1]
    depth = 0
    for token, _start, _end in positioned[:first]:
        depth += token.type == tokenize.INDENT
        depth -= token.type == tokenize.DEDENT
    base = depth
    result: list[tuple[bytes, str, bytes]] = []
    passed_last = False
    for index, (token, start, end) in enumerate(positioned[first:], first):
        if token.type == tokenize.INDENT:
            depth += 1
            if depth > base:
                result.append((b"marker", markers[token.type], b""))
        elif token.type == tokenize.DEDENT:
            if depth > base:
                result.append((b"marker", markers[token.type], b""))
            depth -= 1
        elif token.type == tokenize.NEWLINE:
            result.append((b"marker", markers[token.type], b""))
        elif token.type not in ignored:
            if candidate.span.start_byte <= start and end <= candidate.span.end_byte:
                result.append(
                    (
                        b"token",
                        "python:" + tokenize.tok_name.get(token.type, str(token.type)),
                        _normalized(token.string.encode("utf-8")),
                    )
                )
            elif passed_last:
                break
        if index == last:
            passed_last = True
        if passed_last and depth <= base and token.type == tokenize.DEDENT:
            break
    return result


def _boundary(node: Any) -> bool:
    return (
        node.type in BOUNDARIES
        or node.type.endswith("_statement")
        or node.type.endswith("_declaration")
        or node.type.endswith("_clause")
        or node.type.endswith("_case")
    )


def _tree(syntax: SelectedSyntax, candidate: Any) -> list[tuple[bytes, str, bytes]]:
    start, end = candidate.span.start_byte, candidate.span.end_byte
    result: list[tuple[bytes, str, bytes]] = []
    stack: list[tuple[Any, bool]] = [(syntax.root, False)]
    while stack:
        node, closing = stack.pop()
        if node.end_byte <= start or node.start_byte >= end or "comment" in node.type:
            continue
        contained = start <= node.start_byte and node.end_byte <= end
        boundary = contained and bool(node.children) and _boundary(node)
        if closing:
            if boundary:
                result.append((b"marker", "grammar-boundary:end", b""))
            continue
        if boundary:
            result.append((b"marker", "grammar-boundary:start", b""))
        if node.children:
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node.children))
        elif contained and node.end_byte > node.start_byte:
            result.append(
                (
                    b"token",
                    "tree:" + node.type,
                    _normalized(syntax.parser_source[node.start_byte : node.end_byte]),
                )
            )
    return result


def oracle_canonical_bytes(syntax: SelectedSyntax, candidate: Any) -> bytes:
    items = _python(syntax, candidate) if syntax.language == "Python" else _tree(syntax, candidate)
    return _serialize(syntax.language, items)


__all__ = ["oracle_canonical_bytes"]
