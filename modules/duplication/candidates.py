"""D1 executable-body extraction for the five supported languages.

The module measures boundaries only.  It intentionally contains no
canonicalization, fingerprints, comparison, grouping, IDs, output document, or
CLI integration.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from modules.duplication.model import (
    Candidate,
    CandidateExtractionResult,
    CandidateExtractionStatus,
    CandidateInvariantError,
    SourceSpan,
    UNIT_KIND_PRIORITY,
    UnitKind,
    admission,
    validate_candidate_invariants,
)
from modules.inventory import FileRecord
from modules.source_frontend import (
    ParserRegistry,
    ParserUnavailableError,
    SelectedSyntax,
    SourceEncodingError,
    select_syntax,
)
from modules.syntax_predicates import _iter_nodes


PYTHON_TRIVIA = {
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.NEWLINE,
    tokenize.NL,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.COMMENT,
}


class _ExtractionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _Body:
    kind: UnitKind
    statements: tuple[Any, ...]


def _tree_leaves(node: Any) -> Iterable[Any]:
    if not node.children:
        yield node
        return
    for child in node.children:
        yield from _tree_leaves(child)


def _is_comment(node: Any) -> bool:
    return "comment" in node.type


def _named_statements(body: Any) -> tuple[Any, ...]:
    return tuple(child for child in body.named_children if not _is_comment(child))


def _field_nodes(node: Any, field: str) -> tuple[Any, ...]:
    method = getattr(node, "children_by_field_name", None)
    if method is not None:
        return tuple(method(field))
    result = []
    field_name_for_child = getattr(node, "field_name_for_child", None)
    if field_name_for_child is None:
        child = node.child_by_field_name(field)
        return (child,) if child is not None else ()
    for index, child in enumerate(node.children):
        if field_name_for_child(index) == field:
            result.append(child)
    return tuple(result)


def _one_unfielded_named(node: Any, expected_type: str) -> Any:
    matches = [child for child in node.named_children if child.type == expected_type]
    if len(matches) != 1:
        raise _ExtractionUnavailable(
            f"{node.type} expected one {expected_type} child; found {len(matches)}"
        )
    return matches[0]


def _body_field(
    node: Any,
    field: str,
    expected_type: str,
    *,
    optional: bool = False,
) -> Any | None:
    body = node.child_by_field_name(field)
    if body is None:
        if optional:
            return None
        raise _ExtractionUnavailable(f"{node.type}.{field} is missing")
    if body.type != expected_type:
        if optional:
            return None
        raise _ExtractionUnavailable(
            f"{node.type}.{field} expected {expected_type}; found {body.type}"
        )
    return body


def _deduplicate_bodies(bodies: Iterable[_Body]) -> list[_Body]:
    by_span: dict[tuple[int, int], _Body] = {}
    for body in bodies:
        if not body.statements:
            continue
        first, last = body.statements[0], body.statements[-1]
        if isinstance(first, ast.AST):
            key = (
                (int(first.lineno) << 32) + int(first.col_offset),
                (int(last.end_lineno or last.lineno) << 32)
                + int(last.end_col_offset or 0),
            )
        else:
            key = (first.start_byte, last.end_byte)
        current = by_span.get(key)
        if current is None or UNIT_KIND_PRIORITY[body.kind] < UNIT_KIND_PRIORITY[current.kind]:
            by_span[key] = body
    return list(by_span.values())


def _python_bodies(root: ast.AST) -> list[_Body]:
    bodies: list[_Body] = []
    callable_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    loop_types = (ast.For, ast.AsyncFor, ast.While)
    try_types = (ast.Try,)
    if hasattr(ast, "TryStar"):
        try_types = (*try_types, ast.TryStar)

    for node in ast.walk(root):
        if isinstance(node, callable_types):
            bodies.append(_Body(UnitKind.CALLABLE_BODY, tuple(node.body)))
        elif isinstance(node, ast.If):
            bodies.append(_Body(UnitKind.BRANCH_BODY, tuple(node.body)))
            bodies.append(_Body(UnitKind.BRANCH_BODY, tuple(node.orelse)))
        elif isinstance(node, loop_types):
            bodies.append(_Body(UnitKind.LOOP_BODY, tuple(node.body)))
            bodies.append(_Body(UnitKind.LOOP_BODY, tuple(node.orelse)))
        elif isinstance(node, try_types):
            bodies.append(_Body(UnitKind.EXCEPTION_BODY, tuple(node.body)))
            bodies.append(_Body(UnitKind.EXCEPTION_BODY, tuple(node.orelse)))
            bodies.append(_Body(UnitKind.EXCEPTION_BODY, tuple(node.finalbody)))
        elif isinstance(node, ast.ExceptHandler):
            bodies.append(_Body(UnitKind.EXCEPTION_BODY, tuple(node.body)))
        elif hasattr(ast, "match_case") and isinstance(node, ast.match_case):
            bodies.append(_Body(UnitKind.SWITCH_ARM_BODY, tuple(node.body)))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            bodies.append(_Body(UnitKind.SCOPED_BODY, tuple(node.body)))
    return _deduplicate_bodies(bodies)


JAVA_CALLABLES = {
    "method_declaration": ("block", True),
    "constructor_declaration": ("constructor_body", False),
    "compact_constructor_declaration": ("block", False),
    "lambda_expression": ("block", True),
}
JAVA_LOOPS = {
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
}
JAVA_EXCEPTIONS = {"try_statement", "try_with_resources_statement", "catch_clause"}


def _java_bodies(root: Any) -> list[_Body]:
    bodies: list[_Body] = []
    claimed: set[tuple[int, int]] = set()

    def add(kind: UnitKind, body: Any, statements: Sequence[Any] | None = None) -> None:
        claimed.add((body.start_byte, body.end_byte))
        bodies.append(_Body(kind, tuple(_named_statements(body) if statements is None else statements)))

    for node in _iter_nodes(root):
        if node.type in JAVA_CALLABLES:
            expected, optional = JAVA_CALLABLES[node.type]
            body = _body_field(node, "body", expected, optional=optional)
            if body is not None:
                add(UnitKind.CALLABLE_BODY, body)
        elif node.type == "if_statement":
            for field in ("consequence", "alternative"):
                body = _body_field(node, field, "block", optional=True)
                if body is not None:
                    add(UnitKind.BRANCH_BODY, body)
        elif node.type in JAVA_LOOPS:
            body = _body_field(node, "body", "block", optional=True)
            if body is not None:
                add(UnitKind.LOOP_BODY, body)
        elif node.type in JAVA_EXCEPTIONS:
            body = _body_field(node, "body", "block")
            add(UnitKind.EXCEPTION_BODY, body)
        elif node.type == "finally_clause":
            body = _one_unfielded_named(node, "block")
            add(UnitKind.EXCEPTION_BODY, body)
        elif node.type == "switch_block_statement_group":
            children = list(node.named_children)
            labels = [index for index, child in enumerate(children) if child.type == "switch_label"]
            if len(labels) != 1:
                raise _ExtractionUnavailable(
                    "switch_block_statement_group expected one switch_label"
                )
            statements = tuple(
                child for child in children[labels[0] + 1 :] if not _is_comment(child)
            )
            bodies.append(_Body(UnitKind.SWITCH_ARM_BODY, statements))
        elif node.type == "switch_rule":
            blocks = [child for child in node.named_children if child.type == "block"]
            if len(blocks) > 1:
                raise _ExtractionUnavailable("switch_rule contains multiple block bodies")
            if blocks:
                add(UnitKind.SWITCH_ARM_BODY, blocks[0])
            else:
                statements = tuple(
                    child
                    for child in node.named_children
                    if child.type != "switch_label" and not _is_comment(child)
                )
                if statements:
                    bodies.append(_Body(UnitKind.SWITCH_ARM_BODY, statements[-1:]))
        elif node.type == "synchronized_statement":
            body = _body_field(node, "body", "block")
            add(UnitKind.SCOPED_BODY, body)
        elif node.type == "static_initializer":
            body = _one_unfielded_named(node, "block")
            add(UnitKind.SCOPED_BODY, body)

    for node in _iter_nodes(root):
        if node.type != "block" or (node.start_byte, node.end_byte) in claimed:
            continue
        parent = node.parent
        if parent is not None and parent.type == "class_body":
            add(UnitKind.SCOPED_BODY, node)
        elif parent is not None and parent.type in {"block", "constructor_body"}:
            add(UnitKind.SCOPED_BODY, node)
    return _deduplicate_bodies(bodies)


def _go_statement_list(block: Any) -> Any:
    wrappers = [child for child in block.named_children if child.type == "statement_list"]
    other = [
        child
        for child in block.named_children
        if child.type != "statement_list" and not _is_comment(child)
    ]
    if len(wrappers) != 1 or other:
        raise _ExtractionUnavailable(
            f"Go block expected one statement_list and no other named children; "
            f"found {len(wrappers)} wrapper(s), {len(other)} other"
        )
    return wrappers[0]


def _go_bodies(root: Any) -> list[_Body]:
    bodies: list[_Body] = []
    claimed: set[tuple[int, int]] = set()

    def add(kind: UnitKind, block: Any) -> None:
        claimed.add((block.start_byte, block.end_byte))
        wrapper = _go_statement_list(block)
        bodies.append(_Body(kind, _named_statements(wrapper)))

    for node in _iter_nodes(root):
        if node.type in {"function_declaration", "method_declaration", "func_literal"}:
            add(UnitKind.CALLABLE_BODY, _body_field(node, "body", "block"))
        elif node.type == "if_statement":
            consequence = _body_field(node, "consequence", "block")
            add(UnitKind.BRANCH_BODY, consequence)
            alternative = _body_field(node, "alternative", "block", optional=True)
            if alternative is not None:
                add(UnitKind.BRANCH_BODY, alternative)
        elif node.type == "for_statement":
            add(UnitKind.LOOP_BODY, _body_field(node, "body", "block"))
        elif node.type in {
            "expression_case",
            "type_case",
            "communication_case",
            "default_case",
        }:
            wrapper = _one_unfielded_named(node, "statement_list")
            bodies.append(_Body(UnitKind.SWITCH_ARM_BODY, _named_statements(wrapper)))

    for node in _iter_nodes(root):
        if node.type != "block" or (node.start_byte, node.end_byte) in claimed:
            continue
        if node.parent is not None and node.parent.type == "statement_list":
            add(UnitKind.SCOPED_BODY, node)
    return _deduplicate_bodies(bodies)


JS_CALLABLES = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "method_definition",
    "arrow_function",
}
JS_LOOPS = {"for_statement", "for_in_statement", "while_statement", "do_statement"}


def _javascript_bodies(root: Any) -> list[_Body]:
    bodies: list[_Body] = []
    claimed: set[tuple[int, int]] = set()

    def add(kind: UnitKind, body: Any) -> None:
        claimed.add((body.start_byte, body.end_byte))
        bodies.append(_Body(kind, _named_statements(body)))

    for node in _iter_nodes(root):
        if node.type in JS_CALLABLES:
            body = _body_field(node, "body", "statement_block", optional=True)
            if body is not None:
                add(UnitKind.CALLABLE_BODY, body)
        elif node.type == "if_statement":
            body = _body_field(node, "consequence", "statement_block", optional=True)
            if body is not None:
                add(UnitKind.BRANCH_BODY, body)
        elif node.type == "else_clause":
            blocks = [child for child in node.named_children if child.type == "statement_block"]
            if blocks:
                if len(blocks) != 1:
                    raise _ExtractionUnavailable("else_clause contains multiple statement blocks")
                add(UnitKind.BRANCH_BODY, blocks[0])
        elif node.type in JS_LOOPS:
            body = _body_field(node, "body", "statement_block", optional=True)
            if body is not None:
                add(UnitKind.LOOP_BODY, body)
        elif node.type == "try_statement":
            add(UnitKind.EXCEPTION_BODY, _body_field(node, "body", "statement_block"))
        elif node.type in {"catch_clause", "finally_clause"}:
            add(UnitKind.EXCEPTION_BODY, _body_field(node, "body", "statement_block"))
        elif node.type in {"switch_case", "switch_default"}:
            statements = tuple(
                child for child in _field_nodes(node, "body") if not _is_comment(child)
            )
            if not statements:
                # Pinned bindings expose repeated body fields.  A non-empty arm
                # without those fields is a grammar-shape defect, not zero.
                named = [
                    child
                    for child in node.named_children
                    if child.type not in {"comment"}
                ]
                header_count = 1 if node.type == "switch_case" and named else 0
                if len(named) > header_count:
                    raise _ExtractionUnavailable(
                        f"{node.type} has statements but no repeated body fields"
                    )
            bodies.append(_Body(UnitKind.SWITCH_ARM_BODY, statements))
        elif node.type == "class_static_block":
            add(UnitKind.SCOPED_BODY, _body_field(node, "body", "statement_block"))

    for node in _iter_nodes(root):
        if node.type != "statement_block" or (node.start_byte, node.end_byte) in claimed:
            continue
        if node.parent is not None and node.parent.type == "statement_block":
            add(UnitKind.SCOPED_BODY, node)
    return _deduplicate_bodies(bodies)


def _original_offsets(
    syntax: SelectedSyntax, start: int, end: int
) -> tuple[int | None, int | None]:
    mapping = syntax.mapping
    if not mapping.original_byte_offsets_available:
        return None, None
    adjustment = mapping.parser_byte_offset_adjustment
    return start + adjustment, end + adjustment


def _tree_candidate(syntax: SelectedSyntax, body: _Body) -> Candidate:
    leaves = []
    significant_lines: set[int] = set()
    for statement in body.statements:
        for leaf in _tree_leaves(statement):
            if _is_comment(leaf) or leaf.end_byte <= leaf.start_byte:
                continue
            leaves.append(leaf)
            start_row = int(leaf.start_point[0]) + 1
            end_row = int(leaf.end_point[0]) + 1
            if int(leaf.end_point[1]) == 0 and end_row > start_row:
                end_row -= 1
            significant_lines.update(range(start_row, end_row + 1))
    if not leaves:
        raise _ExtractionUnavailable(
            f"{body.kind.value} body has statements but no significant lexical tokens"
        )
    first, last = leaves[0], leaves[-1]
    start_byte, end_byte = first.start_byte, last.end_byte
    original_start, original_end = _original_offsets(syntax, start_byte, end_byte)
    status, failed = admission(len(body.statements), len(leaves), len(significant_lines))
    return Candidate(
        language=syntax.language,
        unit_kind=body.kind,
        span=SourceSpan(
            start_byte=start_byte,
            end_byte=end_byte,
            start_line=int(first.start_point[0]) + 1,
            start_column=int(first.start_point[1]),
            end_line=int(last.end_point[0]) + 1,
            end_column=int(last.end_point[1]),
            original_start_byte=original_start,
            original_end_byte=original_end,
        ),
        immediate_statement_count=len(body.statements),
        significant_lexical_token_count=len(leaves),
        duplicated_nloc=len(significant_lines),
        extraction_status=status,
        failed_floors=failed,
    )


def _python_position_to_byte(
    line_starts: tuple[int, ...],
    text_lines: tuple[str, ...],
    line: int,
    character_column: int,
) -> int:
    """Translate tokenize's character column into CPython's UTF-8 byte space."""
    prefix = text_lines[line - 1][:character_column]
    return line_starts[line - 1] + len(prefix.encode("utf-8"))


def _python_candidate(
    syntax: SelectedSyntax,
    body: _Body,
    tokens: tuple[tokenize.TokenInfo, ...],
) -> Candidate:
    first_statement, last_statement = body.statements[0], body.statements[-1]
    starts = syntax.mapping.selected_line_starts
    text_lines = tuple((syntax.evidence_text or "").splitlines(keepends=True))
    lower = starts[int(first_statement.lineno) - 1] + int(first_statement.col_offset)
    upper = (
        starts[int(last_statement.end_lineno or last_statement.lineno) - 1]
        + int(last_statement.end_col_offset or 0)
    )
    positioned = tuple(
        (
            token,
            _python_position_to_byte(starts, text_lines, token.start[0], token.start[1]),
            _python_position_to_byte(starts, text_lines, token.end[0], token.end[1]),
        )
        for token in tokens
        if token.type not in PYTHON_TRIVIA
    )
    significant = tuple(
        (token, start, end)
        for token, start, end in positioned
        if start >= lower and end <= upper
    )
    if not significant:
        raise _ExtractionUnavailable(
            f"{body.kind.value} body has statements but no significant lexical tokens"
        )
    lines: set[int] = set()
    for token, _start, _end in significant:
        final_line = token.end[0] - (
            1 if token.end[1] == 0 and token.end[0] > token.start[0] else 0
        )
        lines.update(range(token.start[0], final_line + 1))
    first, start_byte, _first_end = significant[0]
    last, _last_start, end_byte = significant[-1]
    original_start, original_end = _original_offsets(syntax, start_byte, end_byte)
    status, failed = admission(len(body.statements), len(significant), len(lines))
    return Candidate(
        language=syntax.language,
        unit_kind=body.kind,
        span=SourceSpan(
            start_byte=start_byte,
            end_byte=end_byte,
            start_line=first.start[0],
            start_column=start_byte - starts[first.start[0] - 1],
            end_line=last.end[0],
            end_column=end_byte - starts[last.end[0] - 1],
            original_start_byte=original_start,
            original_end_byte=original_end,
        ),
        immediate_statement_count=len(body.statements),
        significant_lexical_token_count=len(significant),
        duplicated_nloc=len(lines),
        extraction_status=status,
        failed_floors=failed,
    )


def extract_candidates(syntax: SelectedSyntax) -> CandidateExtractionResult:
    """Extract and measure deterministic body boundaries from selected syntax."""
    if not syntax.eligible_for_duplication:
        reason = (
            syntax.diagnostics[0].category
            if syntax.diagnostics
            else "selected_syntax_not_eligible"
        )
        return CandidateExtractionResult(
            syntax.language,
            CandidateExtractionStatus.UNAVAILABLE,
            (),
            reason,
        )
    try:
        if syntax.language == "Python":
            if syntax.root is None or syntax.evidence_text is None:
                raise _ExtractionUnavailable("Python selected AST or decoded text is missing")
            bodies = _python_bodies(syntax.root)
            tokens = tuple(
                tokenize.generate_tokens(io.StringIO(syntax.evidence_text).readline)
            )
            measured = [_python_candidate(syntax, body, tokens) for body in bodies]
        elif syntax.language == "Java":
            measured = [_tree_candidate(syntax, body) for body in _java_bodies(syntax.root)]
        elif syntax.language == "Go":
            measured = [_tree_candidate(syntax, body) for body in _go_bodies(syntax.root)]
        elif syntax.language in {"JavaScript", "TypeScript"}:
            measured = [
                _tree_candidate(syntax, body) for body in _javascript_bodies(syntax.root)
            ]
        else:
            return CandidateExtractionResult(
                syntax.language,
                CandidateExtractionStatus.UNAVAILABLE,
                (),
                "unsupported_language",
            )
        measured.sort(
            key=lambda candidate: (
                candidate.span.start_byte,
                candidate.span.end_byte,
                UNIT_KIND_PRIORITY[candidate.unit_kind],
            )
        )
        validate_candidate_invariants(measured, len(syntax.selected_source))
        return CandidateExtractionResult(
            syntax.language,
            CandidateExtractionStatus.COMPLETE,
            tuple(measured),
        )
    except _ExtractionUnavailable as exc:
        return CandidateExtractionResult(
            syntax.language,
            CandidateExtractionStatus.UNAVAILABLE,
            (),
            str(exc),
        )


def extract_file_candidates(
    record: FileRecord,
    source: bytes,
    registry: ParserRegistry,
    *,
    python_text: str | None = None,
) -> CandidateExtractionResult:
    """Select syntax once, then extract candidates with typed failure status."""
    language = record.detected_language or ""
    try:
        syntax = select_syntax(
            record,
            source,
            registry,
            python_text=python_text,
        )
        return extract_candidates(syntax)
    except (ParserUnavailableError, SourceEncodingError) as exc:
        return CandidateExtractionResult(
            language,
            CandidateExtractionStatus.UNAVAILABLE,
            (),
            f"{type(exc).__name__}: {exc}",
        )
    except CandidateInvariantError:
        # Adapter boundary defects are programming errors, never a benign
        # per-file zero or an ordinary parser-unavailable status.
        raise
    except Exception as exc:  # a parser defect is typed, never mistaken for zero
        return CandidateExtractionResult(
            language,
            CandidateExtractionStatus.FAILED,
            (),
            f"{type(exc).__name__}: {exc}",
        )


__all__ = ["extract_candidates", "extract_file_candidates"]
