"""D2 lexical-exact canonicalization and versioned fingerprinting.

Canonical bytes, rather than the digest, are the equality oracle.  This module
does not group candidates and contains no structural abstraction.
"""

from __future__ import annotations

import hashlib
import io
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from modules.duplication.model import Candidate, LexicalOccurrence
from modules.source_frontend import SelectedSyntax


LEXICAL_FINGERPRINT_VERSION = "lexical-exact-v1"
LEXICAL_CANONICAL_MAGIC = b"ARCHLENS-DUPLICATION-LEXICAL\x00"
_FINGERPRINT_NAMESPACE = b"archlens-duplication-fingerprint"

_PYTHON_IGNORED = {
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.NL,
    tokenize.COMMENT,
}
_PYTHON_MARKERS = {
    tokenize.NEWLINE: "python:logical-newline",
    tokenize.INDENT: "python:indent",
    tokenize.DEDENT: "python:dedent",
}

_TREE_BOUNDARY_TYPES = frozenset(
    {
        "block",
        "class_body",
        "class_static_block",
        "constructor_body",
        "default_case",
        "expression_case",
        "statement_block",
        "statement_list",
        "switch_block",
        "switch_default",
        "switch_rule",
        "type_case",
        "communication_case",
    }
)


class LexicalCanonicalizationError(RuntimeError):
    """The candidate cannot be represented under lexical-exact-v1."""


@dataclass(frozen=True, slots=True)
class _Item:
    category: bytes
    kind: str
    lexeme: bytes = b""


def frame(value: str | bytes) -> bytes:
    """Return the contract's unsigned-64-bit length frame."""
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    if len(encoded) >= 1 << 64:
        raise OverflowError("canonical field exceeds unsigned 64-bit framing")
    return len(encoded).to_bytes(8, "big") + encoded


def _normalize_line_endings(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _encode_items(language: str, items: Iterable[_Item]) -> bytes:
    output = bytearray(LEXICAL_CANONICAL_MAGIC)
    output.extend(frame(LEXICAL_FINGERPRINT_VERSION))
    output.extend(frame(language))
    output.extend(frame(b"sequence:start"))
    count = 0
    for item in items:
        output.extend(frame(item.category))
        output.extend(frame(item.kind))
        output.extend(frame(item.lexeme))
        count += 1
    if count == 0:
        raise LexicalCanonicalizationError("candidate has no retained lexical items")
    output.extend(frame(b"sequence:end"))
    return bytes(output)


def _position_to_byte(
    line_starts: tuple[int, ...],
    text_lines: tuple[str, ...],
    position: tuple[int, int],
) -> int:
    line, character_column = position
    if not (1 <= line <= len(text_lines)):
        # tokenize places ENDMARKER/DEDENT on a synthetic line after a source
        # without a final LF.  Those positions are allowed at EOF only.
        if line == len(text_lines) + 1:
            return line_starts[-1] + (
                0
                if len(line_starts) > len(text_lines)
                else len(text_lines[-1].encode("utf-8"))
            )
        raise LexicalCanonicalizationError(f"Python token line is outside source: {line}")
    return line_starts[line - 1] + len(
        text_lines[line - 1][:character_column].encode("utf-8")
    )


def _python_items(syntax: SelectedSyntax, candidate: Candidate) -> list[_Item]:
    if syntax.evidence_text is None:
        raise LexicalCanonicalizationError("Python decoded evidence text is unavailable")
    tokens = tuple(tokenize.generate_tokens(io.StringIO(syntax.evidence_text).readline))
    text_lines = tuple(syntax.evidence_text.splitlines(keepends=True))
    if not text_lines:
        raise LexicalCanonicalizationError("Python evidence text is empty")
    positioned = tuple(
        (
            token,
            _position_to_byte(syntax.mapping.selected_line_starts, text_lines, token.start),
            _position_to_byte(syntax.mapping.selected_line_starts, text_lines, token.end),
        )
        for token in tokens
    )

    start = candidate.span.start_byte
    end = candidate.span.end_byte
    lexical_indices = [
        index
        for index, (token, token_start, token_end) in enumerate(positioned)
        if token.type not in _PYTHON_IGNORED
        and token.type not in _PYTHON_MARKERS
        and start <= token_start
        and token_end <= end
    ]
    if not lexical_indices:
        raise LexicalCanonicalizationError("Python candidate contains no lexical tokens")
    first_index, last_index = lexical_indices[0], lexical_indices[-1]

    depth = 0
    for token, _token_start, _token_end in positioned[:first_index]:
        if token.type == tokenize.INDENT:
            depth += 1
        elif token.type == tokenize.DEDENT:
            depth = max(0, depth - 1)
    base_depth = depth

    items: list[_Item] = []
    seen_last = False
    for index in range(first_index, len(positioned)):
        token, token_start, token_end = positioned[index]
        token_type = token.type
        if token_type == tokenize.INDENT:
            depth += 1
            if depth > base_depth:
                items.append(_Item(b"marker", _PYTHON_MARKERS[token_type]))
        elif token_type == tokenize.DEDENT:
            if depth > base_depth:
                items.append(_Item(b"marker", _PYTHON_MARKERS[token_type]))
            depth = max(0, depth - 1)
        elif token_type == tokenize.NEWLINE:
            items.append(_Item(b"marker", _PYTHON_MARKERS[token_type]))
        elif token_type not in _PYTHON_IGNORED:
            if start <= token_start and token_end <= end:
                kind = "python:" + tokenize.tok_name.get(token_type, str(token_type))
                items.append(
                    _Item(
                        b"token",
                        kind,
                        _normalize_line_endings(token.string.encode("utf-8")),
                    )
                )
            elif seen_last:
                break
        if index == last_index:
            seen_last = True
        if seen_last and depth <= base_depth and token_type == tokenize.DEDENT:
            # A DEDENT back to the surrounding body's level is not part of the
            # synthetic statement sequence.  It was not emitted above.
            break
    return items


def _is_comment(node: Any) -> bool:
    return "comment" in node.type


def _is_boundary(node: Any) -> bool:
    kind = node.type
    return (
        kind in _TREE_BOUNDARY_TYPES
        or kind.endswith("_statement")
        or kind.endswith("_declaration")
        or kind.endswith("_clause")
        or kind.endswith("_case")
    )


def _tree_items(syntax: SelectedSyntax, candidate: Candidate) -> list[_Item]:
    if syntax.root is None:
        raise LexicalCanonicalizationError("selected tree-sitter root is unavailable")
    start, end = candidate.span.start_byte, candidate.span.end_byte
    source = syntax.parser_source
    items: list[_Item] = []

    def visit(node: Any) -> None:
        if node.end_byte <= start or node.start_byte >= end or _is_comment(node):
            return
        fully_contained = start <= node.start_byte and node.end_byte <= end
        boundary = fully_contained and bool(node.children) and _is_boundary(node)
        if boundary:
            items.append(_Item(b"marker", "grammar-boundary:start"))
        if not node.children:
            if fully_contained and node.end_byte > node.start_byte:
                lexeme = source[node.start_byte : node.end_byte]
                try:
                    lexeme.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise LexicalCanonicalizationError(
                        f"tree token is not canonical UTF-8: {node.type}"
                    ) from exc
                items.append(
                    _Item(
                        b"token",
                        "tree:" + node.type,
                        _normalize_line_endings(lexeme),
                    )
                )
        else:
            for child in node.children:
                visit(child)
        if boundary:
            items.append(_Item(b"marker", "grammar-boundary:end"))

    visit(syntax.root)
    return items


def canonical_lexical_bytes(syntax: SelectedSyntax, candidate: Candidate) -> bytes:
    """Canonicalize one admitted candidate under lexical-exact-v1."""
    if not syntax.eligible_for_duplication:
        raise LexicalCanonicalizationError("selected syntax is unavailable for duplication")
    if not candidate.admitted:
        raise LexicalCanonicalizationError("candidate did not meet the frozen D1 floors")
    if candidate.language != syntax.language:
        raise LexicalCanonicalizationError(
            f"candidate language {candidate.language!r} != syntax {syntax.language!r}"
        )
    if not (
        0 <= candidate.span.start_byte < candidate.span.end_byte <= len(syntax.selected_source)
    ):
        raise LexicalCanonicalizationError("candidate span is outside selected source")
    items = (
        _python_items(syntax, candidate)
        if syntax.language == "Python"
        else _tree_items(syntax, candidate)
        if syntax.language in {"Go", "Java", "JavaScript", "TypeScript"}
        else None
    )
    if items is None:
        raise LexicalCanonicalizationError(
            f"unsupported lexical language: {syntax.language!r}"
        )
    return _encode_items(syntax.language, items)


def _canonical_header(canonical_bytes: bytes) -> tuple[str, str]:
    if not canonical_bytes.startswith(LEXICAL_CANONICAL_MAGIC):
        raise ValueError("canonical bytes have the wrong lexical magic")
    offset = len(LEXICAL_CANONICAL_MAGIC)
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


def lexical_fingerprint(
    language: str,
    canonical_bytes: bytes,
    *,
    digest: Callable[[bytes], bytes] | None = None,
) -> str:
    """Compute the versioned deterministic index for canonical bytes.

    ``digest`` is an explicit test seam used to prove collision safety. Runtime
    callers use SHA-256 and injected functions must return exactly 32 bytes.
    """
    canonical_version, canonical_language = _canonical_header(canonical_bytes)
    if canonical_version != LEXICAL_FINGERPRINT_VERSION:
        raise ValueError(
            f"canonical version {canonical_version!r} is not "
            f"{LEXICAL_FINGERPRINT_VERSION!r}"
        )
    if canonical_language != language:
        raise ValueError(
            f"canonical language {canonical_language!r} does not match {language!r}"
        )
    payload = b"".join(
        (
            frame(_FINGERPRINT_NAMESPACE),
            frame(LEXICAL_FINGERPRINT_VERSION),
            frame(language),
            frame(canonical_bytes),
        )
    )
    value = hashlib.sha256(payload).digest() if digest is None else digest(payload)
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("lexical digest function must return exactly 32 bytes")
    return "sha256:" + value.hex()


def make_lexical_occurrence(
    relative_path: str,
    syntax: SelectedSyntax,
    candidate: Candidate,
    *,
    digest: Callable[[bytes], bytes] | None = None,
) -> LexicalOccurrence:
    """Canonicalize and fingerprint one occurrence without grouping it."""
    # Imported lazily to keep canonicalization independent from grouping.
    from modules.duplication.grouping import occurrence_identity, validate_relative_path

    validate_relative_path(relative_path)
    canonical = canonical_lexical_bytes(syntax, candidate)
    fingerprint = lexical_fingerprint(syntax.language, canonical, digest=digest)
    coordinate = (
        relative_path,
        candidate.span.start_line,
        candidate.span.end_line,
        candidate.unit_kind.value,
    )
    return LexicalOccurrence(
        relative_path=relative_path,
        candidate=candidate,
        canonical_bytes=canonical,
        fingerprint=fingerprint,
        fingerprint_version=LEXICAL_FINGERPRINT_VERSION,
        occurrence_id=occurrence_identity(coordinate),
    )


__all__ = [
    "LEXICAL_CANONICAL_MAGIC",
    "LEXICAL_FINGERPRINT_VERSION",
    "LexicalCanonicalizationError",
    "canonical_lexical_bytes",
    "frame",
    "lexical_fingerprint",
    "make_lexical_occurrence",
]
