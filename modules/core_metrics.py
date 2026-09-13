"""Versioned syntax-aware implementation of the four benchmark metrics."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from modules.config import (
    COMPLEXITY_CONTRACT_VERSION,
    METRIC_CONTRACT_VERSION,
    SUPPORTED_LANGUAGES,
)
from modules.inventory import FileRecord, RepositoryInventory
from modules.source_frontend import (
    JAVASCRIPT_COMPATIBILITY_EXTENSIONS,
    JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES,
    JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT,
    TYPESCRIPT_COMPATIBILITY_EXTENSIONS,
    ParserRegistry,
    ParserUnavailableError,
    SourceEncodingError,
    TreeCandidate as _TreeCandidate,
    _rewrite_json_import_assertions,
    _rewrite_raw_jsx_ampersands,
    _rewrite_reserved_jsx_attributes,
    _rewrite_typescript_keyword_parameters,
    grammar_identity as _grammar_identity,
    javascript_compatibility_parse as _javascript_compatibility_parse,
    malformed_counts as _malformed_counts,
    parser_compatible_source as _parser_compatible_source,
    select_syntax,
    successfully_parsed_byte_coverage as _successfully_parsed_byte_coverage,
    typescript_compatibility_parse as _typescript_compatibility_parse,
    validate_parser_initialization,
)


ENTITY_KEYS = (
    "classes",
    "records",
    "structs",
    "interfaces",
    "enums",
    "annotation_types",
    "type_aliases",
    "assigned_classes",
    "anonymous_classes",
    "anonymous_class_methods",
    "anonymous_structs",
    "constructors",
    "module_functions",
    "class_methods",
    "receiver_methods",
    "nested_functions",
    "anonymous_functions",
    "lambdas",
    "signature_only_methods",
    "async_functions",
    "generator_functions",
    "exported_functions",
)
LOC_KEYS = (
    "code_lines", "comment_lines", "blank_lines", "nonblank_lines", "total_physical_lines"
)
MAX_MALFORMED_NODES = 25
MAX_PREVIEW_CHARACTERS = 240
def _empty_entities() -> dict[str, int]:
    return {key: 0 for key in ENTITY_KEYS}


# Tree-syntax predicates live in `modules.syntax_predicates` and are re-exported
# here unchanged. They moved so that `modules.callable_analysis` can share the
# *same* population semantics without importing this module -- `core_metrics`
# calls `callable_analysis`, so an import back would cycle. Re-exporting keeps
# every existing reference (`core_metrics._at_module_scope`, and the historical
# import paths the tests and the differential study use) resolving to exactly one
# implementation. Duplicating them instead would let "what counts as a function"
# drift in two places, which is the one failure this seam exists to prevent.
from modules.syntax_predicates import (  # noqa: F401  (re-exported)
    _async_generator,
    _at_module_scope,
    _commonjs_assignment,
    _commonjs_export_name,
    _contains_malformed,
    _field_is_reliable,
    _has_callable_ancestor,
    _is_named_module_variable,
    _iter_all_nodes,
    _iter_nodes,
    _java_method_owner,
    _malformed_ancestor,
    _member_path,
    _named_object_container,
    _node_is_malformed,
    _node_text,
    _reliable_declaration,
    _stably_assigned_class,
    line_is_code,
    python_is_overload,
)


def _safe_preview(
    source: bytes,
    start_byte: int | None,
    end_byte: int | None,
    limit: int = MAX_PREVIEW_CHARACTERS,
    *,
    allow_textual_nul: bool = False,
) -> str:
    sample_for_binary = source[:4096]
    controls = sum(
        byte < 32
        and byte not in ({0, 9, 10, 13} if allow_textual_nul else {9, 10, 13})
        for byte in sample_for_binary
    )
    if (b"\x00" in sample_for_binary and not allow_textual_nul) or (
        sample_for_binary and controls / len(sample_for_binary) > 0.10
    ):
        return "<binary content omitted>"
    left = max(0, (start_byte or 0) - 80)
    right = min(len(source), max(end_byte or 0, start_byte or 0) + 80)
    if right <= left:
        right = min(len(source), left + 160)
    decoded = source[left:right].decode("utf-8", errors="replace")
    escaped: list[str] = []
    for character in decoded:
        codepoint = ord(character)
        if character == "\\":
            escaped.append("\\\\")
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            escaped.append(f"\\x{codepoint:02x}")
        else:
            escaped.append(character)
        if sum(len(part) for part in escaped) >= limit:
            break
    return "".join(escaped)[:limit]


def _base_diagnostic(record: FileRecord) -> dict[str, Any]:
    return {
        "repository_url": None,
        "analyzed_commit_sha": None,
        "file_path": record.relative_path,
        "file_sha256": record.content_hash,
        "detected_language": record.detected_language,
        "extension": record.extension,
        **_grammar_identity(record.detected_language, record.extension),
        "root_has_error": None,
        "total_error_nodes": 0,
        "total_missing_nodes": 0,
        "first_malformed_node_type": None,
        "first_error_start_line": None,
        "first_error_end_line": None,
        "first_error_start_column": None,
        "first_error_end_column": None,
        "first_error_start_byte": None,
        "first_error_end_byte": None,
        "preview": "",
        "affected_metrics": [],
        "stage": None,
        "fallback_attempted": False,
        "fallback_grammar": None,
        "fallback_error_count": None,
        "fallback_missing_count": None,
        "fallback_strategies": [],
        "selected_fallback_strategies": [],
        "selected_parse": "primary",
        "selected_error_count": None,
        "selected_missing_count": None,
        "selected_successfully_parsed_byte_coverage": None,
        "fallback_offsets_match_original": True,
        "original_line_ending_style": record.line_ending_style,
        "parser_normalization_applied": record.parser_normalization_applied,
        "parser_compatibility_strategy": record.parser_compatibility_strategy,
        "parser_byte_offset_adjustment": record.parser_byte_offset_adjustment,
        "parser_offsets_match_original": (
            record.parser_offsets_map_directly_to_original_bytes
            and record.parser_byte_offset_adjustment == 0
        ),
        "parser_offsets_map_directly_to_original_bytes": (
            record.parser_offsets_map_directly_to_original_bytes
        ),
        "original_byte_offsets_available": record.original_byte_offsets_available,
        "original_encoding": record.original_encoding,
        "parser_encoding": record.parser_encoding,
        "encoding_transformation_applied": record.encoding_transformation_applied,
        "original_byte_length": record.original_byte_length,
        "parser_byte_length": record.parser_byte_length,
        "nul_count": record.nul_count,
        "nul_density": record.nul_density,
        "nul_positions": list(record.nul_positions),
        "nul_contexts": list(record.nul_contexts),
        "alternating_nul_evidence": dict(record.alternating_nul_evidence),
        "suspected_bomless_utf16": record.suspected_bomless_utf16,
        "nul_classification": record.nul_classification,
        "typed_javascript_dialect_evidence": [
            dict(item) for item in record.typed_javascript_dialect_evidence
        ],
        "first_error_parser_start_byte": None,
        "first_error_parser_end_byte": None,
        "final_file_status": None,
        "final_repository_metric_statuses": None,
        "error_category": None,
        "language_version_hint": None,
        "message": None,
        "malformed_nodes": [],
        "malformed_nodes_truncated": 0,
    }


def _tree_diagnostic(
    root: Any,
    parser_source: bytes,
    record: FileRecord,
    status: str,
    message: str,
    original_source: bytes | None = None,
) -> dict[str, Any]:
    original_source = parser_source if original_source is None else original_source
    adjustment = record.parser_byte_offset_adjustment

    def original_offset(parser_offset: int) -> int | None:
        if not record.original_byte_offsets_available:
            return None
        return parser_offset + adjustment

    malformed = [node for node in _iter_all_nodes(root) if _node_is_malformed(node)]
    error_count = sum(node.type == "ERROR" and not node.is_missing for node in malformed)
    missing_count = sum(bool(node.is_missing) for node in malformed)
    details = [
        {
            "node_type": node.type,
            "malformed_node_type": "MISSING" if node.is_missing else node.type,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "start_column": node.start_point[1],
            "end_column": node.end_point[1],
            "start_byte": original_offset(node.start_byte),
            "end_byte": original_offset(node.end_byte),
            "original_start_byte": original_offset(node.start_byte),
            "original_end_byte": original_offset(node.end_byte),
            "parser_start_byte": node.start_byte,
            "parser_end_byte": node.end_byte,
        }
        for node in malformed[:MAX_MALFORMED_NODES]
    ]
    first = malformed[0] if malformed else None
    diagnostic = _base_diagnostic(record)
    diagnostic.update(
        {
            "root_has_error": bool(root.has_error),
            "total_error_nodes": error_count,
            "total_missing_nodes": missing_count,
            "selected_error_count": error_count,
            "selected_missing_count": missing_count,
            "selected_successfully_parsed_byte_coverage": _successfully_parsed_byte_coverage(
                root, len(parser_source)
            ),
            "first_malformed_node_type": (
                "MISSING" if first is not None and first.is_missing else first.type if first else None
            ),
            "first_error_start_line": first.start_point[0] + 1 if first else None,
            "first_error_end_line": first.end_point[0] + 1 if first else None,
            "first_error_start_column": first.start_point[1] if first else None,
            "first_error_end_column": first.end_point[1] if first else None,
            "first_error_start_byte": original_offset(first.start_byte) if first else None,
            "first_error_end_byte": original_offset(first.end_byte) if first else None,
            "first_error_parser_start_byte": first.start_byte if first else None,
            "first_error_parser_end_byte": first.end_byte if first else None,
            "preview": _safe_preview(
                original_source if record.original_byte_offsets_available else parser_source,
                (
                    original_offset(first.start_byte)
                    if first and record.original_byte_offsets_available
                    else first.start_byte if first else 0
                ),
                (
                    original_offset(first.end_byte)
                    if first and record.original_byte_offsets_available
                    else first.end_byte
                    if first
                    else min(
                        len(
                            original_source
                            if record.original_byte_offsets_available
                            else parser_source
                        ),
                        160,
                    )
                ),
                allow_textual_nul=(
                    record.nul_classification
                    == "low_density_intentional_textual_nul"
                ),
            ),
            "affected_metrics": ["classes_structs", "methods_functions"],
            "stage": "parse",
            "final_file_status": status,
            "error_category": (
                "syntax_partial" if status in {"partial", "partial_parse"} else "syntax_failed"
            ),
            "message": message,
            "malformed_nodes": details,
            "malformed_nodes_truncated": max(0, len(malformed) - len(details)),
        }
    )
    return diagnostic


def _tree_comment_masked(source: bytes, root: Any) -> bytes:
    """Blank every comment byte, preserving length and line structure.

    Split out from `_tree_comment_loc` so per-callable NLOC can classify a line
    span using the SAME masked bytes the repository LOC metric counts. Producing
    a second masking implementation is what would let function NLOC and
    `lines_of_code` disagree about what a comment is.
    """
    masked = bytearray(source)
    for node in _iter_nodes(root):
        if "comment" in node.type:
            for index in range(node.start_byte, node.end_byte):
                if masked[index] not in (10, 13):
                    masked[index] = 32
    return bytes(masked)


def _tree_comment_loc(source: bytes, root: Any) -> dict[str, int]:
    return _classify_lines(
        source.decode("utf-8", errors="replace"),
        _tree_comment_masked(source, root).decode("utf-8", errors="replace"),
    )


def _python_comment_masked(text: str) -> tuple[str | None, str | None]:
    """Blank every COMMENT token, preserving length and line structure.

    Split out for the same reason as `_tree_comment_masked`: one masking
    implementation, shared by repository LOC and per-callable NLOC.
    """
    raw_lines = text.splitlines(keepends=True)
    masked_lines = list(raw_lines)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (IndentationError, SyntaxError, tokenize.TokenError) as exc:
        return None, f"Python tokenization failed: {exc}"
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        start_line, start_col = token.start
        end_line, end_col = token.end
        for line_number in range(start_line, end_line + 1):
            index = line_number - 1
            if index >= len(masked_lines):
                continue
            line = masked_lines[index]
            left = start_col if line_number == start_line else 0
            right = end_col if line_number == end_line else len(line)
            masked_lines[index] = line[:left] + " " * (right - left) + line[right:]
    return "".join(masked_lines), None


def _python_comment_loc(text: str) -> tuple[dict[str, int] | None, str | None]:
    masked, error = _python_comment_masked(text)
    if masked is None:
        return None, error
    return _classify_lines(text, masked), None


def _physical_lines(text: str) -> list[str]:
    return text.splitlines() if text else []


def _classify_lines(raw_text: str, comment_masked_text: str) -> dict[str, int]:
    raw = _physical_lines(raw_text)
    masked = _physical_lines(comment_masked_text)
    if len(masked) < len(raw):
        masked.extend([""] * (len(raw) - len(masked)))
    result = {key: 0 for key in LOC_KEYS}
    result["total_physical_lines"] = len(raw)
    for original, without_comments in zip(raw, masked):
        if not original.strip():
            result["blank_lines"] += 1
        else:
            result["nonblank_lines"] += 1
            # One shared predicate, so per-callable NLOC classifies a line
            # exactly as the repository metric does.
            if line_is_code(original, without_comments):
                result["code_lines"] += 1
            else:
                result["comment_lines"] += 1
    return result


def _java_entities(root: Any) -> dict[str, int]:
    result = _empty_entities()
    mapping = {
        "class_declaration": "classes",
        "record_declaration": "records",
        "interface_declaration": "interfaces",
        "enum_declaration": "enums",
        "annotation_type_declaration": "annotation_types",
    }
    for node in _iter_nodes(root):
        if node.type in mapping and _reliable_declaration(node, "name", "body"):
            result[mapping[node.type]] += 1
        elif node.type == "constructor_declaration" and _reliable_declaration(
            node, "name", "parameters", "body"
        ):
            result["constructors"] += 1
        elif node.type == "compact_constructor_declaration" and _reliable_declaration(
            node, "name", "body"
        ):
            result["constructors"] += 1
        elif node.type == "method_declaration":
            if not _field_is_reliable(node, "name"):
                continue
            body = node.child_by_field_name("body")
            owner = _java_method_owner(node)
            if body is None:
                result["signature_only_methods"] += 1
            elif owner == "named" and _reliable_declaration(node, "name", "parameters", "body"):
                result["class_methods"] += 1
            elif owner == "anonymous" and _reliable_declaration(node, "name", "parameters", "body"):
                result["anonymous_class_methods"] += 1
        elif node.type == "object_creation_expression" and any(
            child.type == "class_body" for child in node.named_children
        ) and not _malformed_ancestor(node):
            result["anonymous_classes"] += 1
    return result


def _js_ts_entities(root: Any, source: bytes) -> dict[str, int]:
    result = _empty_entities()
    counted_classes: set[int] = set()
    for node in _iter_nodes(root):
        if node.type in {"class_declaration", "abstract_class_declaration"}:
            if _reliable_declaration(node, "name", "body"):
                counted_classes.add(node.id)
                result["classes"] += 1
        elif node.type == "class":
            if _field_is_reliable(node, "body") and _stably_assigned_class(node, source):
                counted_classes.add(node.id)
                result["classes"] += 1
                result["assigned_classes"] += 1
            else:
                result["anonymous_classes"] += 1

    for node in _iter_nodes(root):
        node_type = node.type
        if node_type == "interface_declaration" and _field_is_reliable(node, "name"):
            result["interfaces"] += 1
        elif node_type == "enum_declaration" and _field_is_reliable(node, "name"):
            result["enums"] += 1
        elif node_type == "type_alias_declaration" and _field_is_reliable(node, "name"):
            result["type_aliases"] += 1
        elif node_type in {"function_signature", "method_signature", "abstract_method_signature"}:
            result["signature_only_methods"] += 1
        elif node_type in {"function_declaration", "generator_function_declaration"}:
            async_value, generator = _async_generator(node, source)
            if not _reliable_declaration(node, "name", "parameters", "body"):
                continue
            if _has_callable_ancestor(node):
                result["nested_functions"] += 1
            elif _at_module_scope(node):
                result["module_functions"] += 1
                result["async_functions"] += int(async_value)
                result["generator_functions"] += int(generator)
        elif node_type in {"function_expression", "arrow_function", "generator_function"}:
            async_value, generator = _async_generator(node, source)
            commonjs_name = _commonjs_assignment(node, source)
            body = node.child_by_field_name("body")
            if body is None or not _reliable_declaration(node, "parameters", "body"):
                continue
            if _is_named_module_variable(node) or commonjs_name is not None:
                result["module_functions"] += 1
                result["exported_functions"] += int(commonjs_name is not None)
                result["async_functions"] += int(async_value)
                result["generator_functions"] += int(generator)
            elif _has_callable_ancestor(node):
                result["nested_functions"] += 1
            else:
                result["anonymous_functions"] += 1
        elif node_type == "method_definition":
            name = node.child_by_field_name("name")
            name_text = _node_text(name, source) if name is not None else ""
            body = node.child_by_field_name("body")
            if name_text == "constructor":
                class_node = node.parent.parent if node.parent is not None and node.parent.parent is not None else None
                if class_node is not None and class_node.id in counted_classes:
                    result["constructors"] += 1
            elif body is None:
                result["signature_only_methods"] += 1
            elif node.parent is not None and node.parent.type == "class_body":
                class_node = node.parent.parent
                if class_node is None or class_node.id not in counted_classes:
                    result["anonymous_class_methods"] += 1
                    continue
                if not _reliable_declaration(node, "name", "parameters", "body"):
                    continue
                async_value, generator = _async_generator(node, source)
                result["class_methods"] += 1
                result["async_functions"] += int(async_value)
                result["generator_functions"] += int(generator)
            elif _named_object_container(node) and _reliable_declaration(
                node, "name", "parameters", "body"
            ):
                async_value, generator = _async_generator(node, source)
                result["class_methods"] += 1
                result["async_functions"] += int(async_value)
                result["generator_functions"] += int(generator)
    return result


def _go_entities(root: Any) -> dict[str, int]:
    result = _empty_entities()
    for node in _iter_nodes(root):
        if node.type == "type_spec" and _at_module_scope(node) and _field_is_reliable(node, "name"):
            declared = node.child_by_field_name("type")
            if declared is not None and declared.type == "struct_type":
                result["structs"] += 1
            elif declared is not None and declared.type == "interface_type":
                result["interfaces"] += 1
        elif node.type == "type_alias" and _at_module_scope(node) and _field_is_reliable(node, "name"):
            result["type_aliases"] += 1
        elif node.type == "struct_type" and (node.parent is None or node.parent.type != "type_spec"):
            result["anonymous_structs"] += 1
        elif node.type == "function_declaration" and _at_module_scope(node) and _reliable_declaration(
            node, "name", "parameters", "body"
        ) and not _contains_malformed(node.child_by_field_name("parameters")):
            result["module_functions"] += 1
        elif node.type == "method_declaration" and _at_module_scope(node) and _reliable_declaration(
            node, "name", "parameters", "body"
        ) and not _contains_malformed(node.child_by_field_name("parameters")):
            result["receiver_methods"] += 1
        elif node.type == "func_literal":
            result["anonymous_functions"] += 1
    return result


class _PythonEntityVisitor(ast.NodeVisitor):
    def __init__(self):
        self.result = _empty_entities()
        self.callable_depth = 0
        self.class_stack: list[ast.ClassDef] = []

    @staticmethod
    def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        # Delegates to the shared seam so the entity counter and the callable
        # enumerator cannot disagree about which declarations are real.
        return python_is_overload(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.result["classes"] += 1
        self.class_stack.append(node)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_direct_class_method = bool(self.class_stack and node in self.class_stack[-1].body)
        if self._is_overload(node):
            self.result["signature_only_methods"] += 1
        elif is_direct_class_method:
            if node.name in {"__init__", "__new__"}:
                self.result["constructors"] += 1
            else:
                self.result["class_methods"] += 1
        elif self.callable_depth:
            self.result["nested_functions"] += 1
        elif self.class_stack:
            self.result["nested_functions"] += 1
        else:
            self.result["module_functions"] += 1
        if isinstance(node, ast.AsyncFunctionDef):
            self.result["async_functions"] += 1
        self.callable_depth += 1
        for child in node.body:
            self.visit(child)
        self.callable_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> Any:
        self.result["lambdas"] += 1
        self.generic_visit(node)


def _python_parse(
    text: str,
) -> tuple[ast.AST | None, str | None, SyntaxError | None]:
    """Parse Python source once and share the tree.

    Split out so a file is parsed exactly once per analysis: the entity counter
    and the callable enumerator both need the tree, and parsing twice would be
    both wasteful and a second chance to disagree about what the source is.
    """
    try:
        return ast.parse(text), None, None
    except SyntaxError as exc:
        return None, f"Python syntax error at line {exc.lineno}: {exc.msg}", exc


def _python_entities(
    text: str,
    tree: ast.AST | None = None,
    parse_error: str | None = None,
    syntax_error: SyntaxError | None = None,
) -> tuple[dict[str, int] | None, str | None, SyntaxError | None]:
    """Count Python entities, reusing an already-parsed tree when given one.

    Called with only ``text`` it parses for itself and behaves exactly as before,
    so every existing caller is unaffected.
    """
    if tree is None and parse_error is None and syntax_error is None:
        tree, parse_error, syntax_error = _python_parse(text)
    if tree is None:
        return None, parse_error, syntax_error
    visitor = _PythonEntityVisitor()
    visitor.visit(tree)
    return visitor.result, None, None


def _likely_python2_syntax(text: str) -> bool:
    """Detect only unambiguous Python 2 statement syntax after Python 3 parsing fails."""
    try:
        tokens = [
            token
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type
            not in {
                tokenize.ENCODING,
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            }
        ]
    except (IndentationError, SyntaxError, tokenize.TokenError):
        tokens = []
    for index, token in enumerate(tokens):
        if token.type == tokenize.NAME and token.string == "print":
            following = tokens[index + 1].string if index + 1 < len(tokens) else ""
            if following and following not in {"(", ".", "=", ":"}:
                return True
        if token.type == tokenize.NAME and token.string == "except":
            for following in tokens[index + 1 :]:
                if following.string == ":":
                    break
                if following.string == ",":
                    return True
    return bool(
        re.search(r"(?m)^\s*raise\s+[A-Za-z_][\w.]*\s*,\s*[^\n]+$", text)
    )


def _python_syntax_diagnostic(
    record: FileRecord,
    parser_source: bytes,
    error: SyntaxError,
    message: str,
    original_source: bytes | None = None,
) -> dict[str, Any]:
    original_source = parser_source if original_source is None else original_source
    line_number = error.lineno or 1
    end_line = error.end_lineno or line_number
    start_column = max((error.offset or 1) - 1, 0)
    end_column = max((error.end_offset or error.offset or 1) - 1, start_column)
    lines = parser_source.splitlines(keepends=True)
    parser_start_byte = sum(len(line) for line in lines[: max(line_number - 1, 0)])
    if 0 < line_number <= len(lines):
        decoded_line = lines[line_number - 1].decode("utf-8", errors="replace")
        parser_start_byte += len(decoded_line[:start_column].encode("utf-8"))
    parser_end_byte = parser_start_byte
    if 0 < end_line <= len(lines):
        decoded_end = lines[end_line - 1].decode("utf-8", errors="replace")
        parser_end_byte = sum(len(line) for line in lines[: max(end_line - 1, 0)]) + len(
            decoded_end[:end_column].encode("utf-8")
        )
    start_byte = (
        parser_start_byte + record.parser_byte_offset_adjustment
        if record.original_byte_offsets_available
        else None
    )
    end_byte = (
        parser_end_byte + record.parser_byte_offset_adjustment
        if record.original_byte_offsets_available
        else None
    )
    python2 = _likely_python2_syntax(parser_source.decode("utf-8", errors="replace"))
    diagnostic = _base_diagnostic(record)
    diagnostic.update(
        {
            "root_has_error": True,
            "total_error_nodes": 1,
            "first_malformed_node_type": "SyntaxError",
            "first_error_start_line": line_number,
            "first_error_end_line": end_line,
            "first_error_start_column": start_column,
            "first_error_end_column": end_column,
            "first_error_start_byte": start_byte,
            "first_error_end_byte": end_byte,
            "first_error_parser_start_byte": parser_start_byte,
            "first_error_parser_end_byte": parser_end_byte,
            "preview": _safe_preview(
                original_source if record.original_byte_offsets_available else parser_source,
                start_byte if start_byte is not None else parser_start_byte,
                end_byte if end_byte is not None else parser_end_byte,
            ),
            "affected_metrics": ["classes_structs", "methods_functions"],
            "stage": "entity",
            "final_file_status": "failed",
            "error_category": (
                "unsupported_language_version" if python2 else "syntax_failed"
            ),
            "language_version_hint": "likely_python2_syntax" if python2 else None,
            "message": message,
            "malformed_nodes": [
                {
                    "node_type": "SyntaxError",
                    "malformed_node_type": "SyntaxError",
                    "start_line": line_number,
                    "end_line": end_line,
                    "start_column": start_column,
                    "end_column": end_column,
                    "start_byte": start_byte,
                    "end_byte": end_byte,
                    "original_start_byte": start_byte,
                    "original_end_byte": end_byte,
                    "parser_start_byte": parser_start_byte,
                    "parser_end_byte": parser_end_byte,
                }
            ],
        }
    )
    return diagnostic


@dataclass(slots=True)
class FileMetricResult:
    loc: dict[str, int] | None
    entities: dict[str, int] | None
    loc_status: str
    classes_structs_status: str
    methods_functions_status: str
    error: str | None
    malformed_nodes: list[dict[str, object]]
    error_category: str | None = None
    diagnostic: dict[str, Any] | None = None
    recovery_diagnostic: dict[str, Any] | None = None
    # Complexity Contract 1.0.0 (milestone C1). Carried on the result but not yet
    # aggregated or persisted: that is Artifact Schema 1.9.0 work and is
    # deliberately not started here, so run artifacts stay byte-identical.
    callables: list[Any] | None = None
    structural_complexity_status: str = "not_applicable"
    nloc_status: str = "not_applicable"

    @property
    def status(self) -> str:
        return _worst_status(
            self.loc_status,
            self.classes_structs_status,
            self.methods_functions_status,
        )


def _discover_callables(
    language: str | None,
    root: Any,
    source: bytes,
    record: FileRecord,
    entities_status: str,
    raw_text: str | None = None,
    masked_text: str | None = None,
):
    """Enumerate this file's canonical callables over the tree already selected.

    Imported here rather than at module scope: `modules.callable_analysis` is a
    consumer of the shared `modules.syntax_predicates` seam, and keeping the call
    site local documents the one permitted direction -- `core_metrics` calls
    `callable_analysis`, never the reverse. `tests/test_callable_analysis.py`
    enforces that with an AST scan.

    Never raises. A defect in a new, non-persisted analysis must not be able to
    fail a measurement that four shipped metrics depend on, so the failure mode
    is a `failed` complexity status and nothing else.
    """
    from modules.callable_analysis import CallableAnalysisResult, analyze_callables

    if root is None:
        return CallableAnalysisResult([], "failed", "failed")
    try:
        return analyze_callables(
            language,
            root,
            source,
            record.relative_path,
            entities_status=entities_status,
            location_maps_to_original_source=(
                record.parser_offsets_map_directly_to_original_bytes
            ),
            raw_text=raw_text,
            masked_text=masked_text,
        )
    except Exception:  # pragma: no cover - defensive, asserted by a unit test
        return CallableAnalysisResult([], "failed", "failed")


def _analyze_file(
    record: FileRecord,
    source: bytes,
    registry: ParserRegistry,
    inventory: RepositoryInventory,
) -> FileMetricResult:
    language = record.detected_language
    if record.encoding_error:
        error = record.encoding_error
        diagnostic = _base_diagnostic(record)
        diagnostic.update(
            {
                "preview": _safe_preview(source, 0, min(len(source), 160)),
                "affected_metrics": [
                    "lines_of_code",
                    "classes_structs",
                    "methods_functions",
                ],
                "stage": "decode",
                "final_file_status": "failed",
                "error_category": "source_encoding_failure",
                "message": error,
            }
        )
        return FileMetricResult(
            None,
            None,
            "failed",
            "failed",
            "failed",
            error,
            [],
            "source_encoding_failure",
            diagnostic,
        )
    if language == "Python":
        text = inventory.read_text(record)
        if text is None:
            error = record.encoding_error or "Python source decoding failed"
            diagnostic = _base_diagnostic(record)
            diagnostic.update(
                {
                    "preview": _safe_preview(source, 0, min(len(source), 160)),
                    "affected_metrics": ["lines_of_code", "classes_structs", "methods_functions"],
                    "stage": "decode",
                    "final_file_status": "failed",
                    "error_category": "source_encoding_failure",
                    "message": error,
                }
            )
            return FileMetricResult(
                None,
                None,
                "failed",
                "failed",
                "failed",
                error,
                [],
                "source_encoding_failure",
                diagnostic,
            )
        python_masked, python_mask_error = _python_comment_masked(text)
        loc = (
            _classify_lines(text, python_masked) if python_masked is not None else None
        )
        loc_error = python_mask_error
        # One selected syntax record, shared by every consumer.
        selected_syntax = select_syntax(
            record, source, registry, python_text=text
        )
        python_tree = selected_syntax.root
        python_syntax_error = selected_syntax.parse_exception
        python_parse_error = (
            selected_syntax.diagnostics[0].message
            if selected_syntax.diagnostics
            else None
        )
        entities, entity_error, syntax_error = _python_entities(
            text, python_tree, python_parse_error, python_syntax_error
        )
        callable_result = _discover_callables(
            "Python", python_tree, text.encode("utf-8"), record,
            "complete" if entities is not None else "failed",
            raw_text=text, masked_text=python_masked,
        )
        parser_source = selected_syntax.parser_source
        errors = [value for value in (loc_error, entity_error) if value]
        diagnostic = (
            _python_syntax_diagnostic(
                record,
                parser_source,
                syntax_error,
                "; ".join(errors) or "Python syntax error",
                original_source=source,
            )
            if syntax_error is not None
            else None
        )
        return FileMetricResult(
            loc,
            entities,
            "complete" if loc is not None else "failed",
            "complete" if entities is not None else "failed",
            "complete" if entities is not None else "failed",
            "; ".join(errors) or None,
            diagnostic["malformed_nodes"] if diagnostic else [],
            diagnostic["error_category"] if diagnostic else None,
            diagnostic,
            callables=callable_result.records,
            structural_complexity_status=callable_result.structural_complexity_status,
            nloc_status=callable_result.nloc_status,
        )
    try:
        selected_syntax = select_syntax(
            record,
            source,
            registry,
            compatibility_iteration_limit=(
                JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT
            ),
            compatibility_growth_limit_bytes=(
                JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES
            ),
            javascript_transformations=(
                (
                    "javascript_import_assertion_compat",
                    _rewrite_json_import_assertions,
                ),
                ("jsx_reserved_attribute_compat", _rewrite_reserved_jsx_attributes),
                ("raw_jsx_ampersand_compat", _rewrite_raw_jsx_ampersands),
            ),
            typescript_transformations=(
                ("raw_jsx_ampersand_compat", _rewrite_raw_jsx_ampersands),
                (
                    "typescript_keyword_parameter_compat",
                    _rewrite_typescript_keyword_parameters,
                ),
            ),
        )
        parser_source = selected_syntax.parser_source
        primary_root = selected_syntax.primary_root
        root = selected_syntax.root
        entity_source = selected_syntax.selected_source
        loc = _tree_comment_loc(parser_source, primary_root)
        recovery_diagnostic = None
        unresolved_diagnostic = None
        if (
            language in {"JavaScript", "TypeScript"}
            and record.extension
            in (
                JAVASCRIPT_COMPATIBILITY_EXTENSIONS
                if language == "JavaScript"
                else TYPESCRIPT_COMPATIBILITY_EXTENSIONS
            )
            and _malformed_counts(primary_root) != (0, 0)
        ):
            best_fallback = selected_syntax.best_fallback
            primary_diagnostic = _tree_diagnostic(
                primary_root,
                parser_source,
                record,
                "partial_parse",
                "Tree-sitter reported syntax error or missing nodes",
                original_source=source,
            )
            fallback_errors, fallback_missing = (
                _malformed_counts(best_fallback.tree.root_node)
                if best_fallback is not None
                else (None, None)
            )
            selected_errors, selected_missing = _malformed_counts(root)
            diagnostic_update = {
                "fallback_attempted": best_fallback is not None,
                "fallback_grammar": (
                    selected_syntax.fallback_grammar
                    if best_fallback is not None
                    else None
                ),
                "fallback_error_count": fallback_errors,
                "fallback_missing_count": fallback_missing,
                "fallback_strategies": list(
                    selected_syntax.all_applied_strategies
                ),
                "selected_fallback_strategies": list(
                    selected_syntax.selected_strategies
                ),
                "selected_parse": selected_syntax.selected_parse,
                "selected_error_count": selected_errors,
                "selected_missing_count": selected_missing,
                "selected_successfully_parsed_byte_coverage": _successfully_parsed_byte_coverage(
                    root, len(entity_source)
                ),
                "fallback_offsets_match_original": (
                    best_fallback.offsets_match_original
                    and record.parser_byte_offset_adjustment == 0
                    if best_fallback is not None
                    else record.parser_byte_offset_adjustment == 0
                ),
            }
            primary_diagnostic.update(diagnostic_update)
            if selected_syntax.compatibility_limit_error is not None:
                primary_diagnostic.update(
                    {
                        "final_file_status": "partial_parse",
                        "error_category": "parser_compatibility_limit_exceeded",
                        "message": selected_syntax.compatibility_limit_error,
                    }
                )
                unresolved_diagnostic = primary_diagnostic
            elif (selected_errors, selected_missing) == (0, 0):
                primary_diagnostic.update(
                    {
                        "final_file_status": "complete",
                        "error_category": "syntax_recovered",
                        "message": (
                            f"{language} syntax recovered with in-memory compatibility fallback: "
                            + ", ".join(selected_syntax.selected_strategies)
                        ),
                    }
                )
                recovery_diagnostic = primary_diagnostic
            else:
                primary_diagnostic.update(
                    {
                        "final_file_status": "partial_parse",
                        "error_category": (
                            "unsupported_javascript_dialect"
                            if selected_syntax.unsupported_typed_javascript
                            else "syntax_partial"
                        ),
                        "message": (
                            "Typed JavaScript syntax is present without explicit repository dialect evidence"
                            if selected_syntax.unsupported_typed_javascript
                            else "Tree-sitter syntax errors remain after compatibility fallback"
                            if best_fallback is not None
                            else "Tree-sitter syntax errors remain; no compatibility strategy applied"
                        ),
                    }
                )
                unresolved_diagnostic = primary_diagnostic
        if language == "Java":
            entities = _java_entities(root)
        elif language in {"JavaScript", "TypeScript"}:
            entities = _js_ts_entities(root, entity_source)
        elif language == "Go":
            entities = _go_entities(root)
        else:
            return FileMetricResult(
                None, None, "not_applicable", "not_applicable", "not_applicable", None, []
            )
        compatibility_limited = bool(
            unresolved_diagnostic
            and unresolved_diagnostic.get("error_category")
            == "parser_compatibility_limit_exceeded"
        )
        entity_status = (
            "partial"
            if compatibility_limited or _malformed_counts(root) != (0, 0)
            else "complete"
        )
        error = (
            unresolved_diagnostic.get("message")
            if compatibility_limited and unresolved_diagnostic is not None
            else "Tree-sitter reported syntax error or missing nodes"
            if entity_status == "partial"
            else None
        )
        diagnostic = unresolved_diagnostic
        if entity_status == "partial" and diagnostic is None:
            diagnostic = _tree_diagnostic(
                root,
                parser_source,
                record,
                "partial_parse",
                error or "",
                original_source=source,
            )
        # NLOC is measured over the SELECTED tree's source: every compatibility
        # rewrite is a same-width in-place substitution, so line numbering is
        # preserved (verified in validation/complexity_c0_20260810).
        entity_masked = _tree_comment_masked(entity_source, root)
        callable_result = _discover_callables(
            language, root, entity_source, record, entity_status,
            raw_text=entity_source.decode("utf-8", errors="replace"),
            masked_text=entity_masked.decode("utf-8", errors="replace"),
        )
        return FileMetricResult(
            loc,
            entities,
            "complete",
            entity_status,
            entity_status,
            error,
            diagnostic["malformed_nodes"] if diagnostic else [],
            diagnostic["error_category"] if diagnostic else None,
            diagnostic,
            recovery_diagnostic,
            callables=callable_result.records,
            structural_complexity_status=callable_result.structural_complexity_status,
            nloc_status=callable_result.nloc_status,
        )
    except SourceEncodingError as exc:
        record.encoding_error = str(exc)
        diagnostic = _base_diagnostic(record)
        diagnostic.update(
            {
                "preview": _safe_preview(source, 0, min(len(source), 160)),
                "affected_metrics": [
                    "lines_of_code",
                    "classes_structs",
                    "methods_functions",
                ],
                "stage": "decode",
                "final_file_status": "failed",
                "error_category": "source_encoding_failure",
                "message": str(exc),
            }
        )
        return FileMetricResult(
            None,
            None,
            "failed",
            "failed",
            "failed",
            str(exc),
            [],
            "source_encoding_failure",
            diagnostic,
        )
    except ParserUnavailableError as exc:
        diagnostic = _base_diagnostic(record)
        diagnostic.update(
            {
                "preview": _safe_preview(source, 0, min(len(source), 160)),
                "affected_metrics": ["lines_of_code", "classes_structs", "methods_functions"],
                "stage": "parse",
                "final_file_status": "failed",
                "error_category": "parser_execution_failure",
                "message": str(exc),
            }
        )
        return FileMetricResult(
            None,
            None,
            "failed",
            "failed",
            "failed",
            str(exc),
            [],
            "parser_execution_failure",
            diagnostic,
        )
    except Exception as exc:
        message = f"Parser execution failed: {type(exc).__name__}: {exc}"
        diagnostic = _base_diagnostic(record)
        diagnostic.update(
            {
                "preview": _safe_preview(source, 0, min(len(source), 160)),
                "affected_metrics": ["lines_of_code", "classes_structs", "methods_functions"],
                "stage": "parse",
                "final_file_status": "failed",
                "error_category": "parser_execution_failure",
                "message": message,
            }
        )
        return FileMetricResult(
            None,
            None,
            "failed",
            "failed",
            "failed",
            message,
            [],
            "parser_execution_failure",
            diagnostic,
        )


def _language_template() -> dict[str, Any]:
    result: dict[str, Any] = {key: 0 for key in LOC_KEYS}
    result.update(_empty_entities())
    result.update(
        {
            "lines_of_code": 0,
            "source_files": 0,
            "classes_structs": 0,
            "methods_functions": 0,
            "metric_status": "not_applicable",
            "inventory_status": "not_applicable",
            "source_files_status": "not_applicable",
            "loc_status": "not_applicable",
            "classes_structs_status": "not_applicable",
            "methods_functions_status": "not_applicable",
            "parse_failure_count": 0,
            "parse_failure_files": [],
            "source_files_readable": 0,
            "source_files_loc_analyzed": 0,
            "source_files_entity_parsed": 0,
            "source_files_failed_read": 0,
            "source_files_oversized": 0,
            "source_files_partial_parse": 0,
            "source_files_failed_parse": 0,
            "source_files_recovered_parse": 0,
            "loc_files_counted": 0,
            "entity_files_parsed": 0,
        }
    )
    return result


_STATUS_RANK = {"not_applicable": 0, "complete": 1, "partial": 2, "failed": 3}


def _worst_status(*statuses: str) -> str:
    return max(statuses, key=lambda status: _STATUS_RANK[status])


def _combined_extraction_status(statuses: list[str]) -> str:
    if not statuses:
        return "not_applicable"
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status in {"partial", "failed"} for status in statuses):
        return "partial"
    return "complete"


def _account_for_inventory(status: str, inventory_status: str) -> str:
    if inventory_status == "failed":
        return "failed"
    if inventory_status == "partial" and status != "failed":
        return "partial"
    return status


# The three derived metric expressions, defined exactly once. The aggregate and
# the per-file contribution ledger both call these, so a ledger row can never
# drift from the aggregate it is supposed to reconcile with (plan section 14.3
# forbids a second metric calculation).
def derive_lines_of_code(components: dict[str, Any]) -> int:
    return components["code_lines"]


def derive_classes_structs(components: dict[str, Any]) -> int:
    return components["classes"] + components["records"] + components["structs"]


def derive_methods_functions(components: dict[str, Any]) -> int:
    return (
        components["module_functions"]
        + components["class_methods"]
        + components["receiver_methods"]
    )


def _derive_complexity_summary(
    callable_records: list[dict[str, Any]],
    file_statuses: list[str],
    status_by_language: dict[str, list[str]],
) -> dict[str, Any]:
    """Repository complexity summary, including per-language aggregates."""
    from modules.callable_ledger import derive_complexity

    by_language: dict[str, list[dict[str, Any]]] = {}
    for row in callable_records:
        language = row.get("detected_language")
        if language:
            by_language.setdefault(language, []).append(row)
    # A language with source files but no callables still deserves an entry, or
    # "no complexity for Go" and "no Go" become indistinguishable.
    for language in status_by_language:
        by_language.setdefault(language, [])
    return derive_complexity(callable_records, file_statuses, by_language)


def _callable_rows(record: Any, analyzed: Any) -> list[dict[str, Any]]:
    """Flatten one file's callable records into ledger rows.

    No metric is computed here. `modules.callable_analysis` measured every value
    during the same traversal that discovered the callable; this only joins the
    file identity the row needs to be addressable.
    """
    if not getattr(analyzed, "callables", None):
        return []
    rows: list[dict[str, Any]] = []
    for item in analyzed.callables:
        row = {
            "relative_path": record.relative_path,
            "content_sha256": record.content_hash,
            "detected_language": record.detected_language,
            "complexity_contract_version": COMPLEXITY_CONTRACT_VERSION,
        }
        for name in (
            "callable_row_id", "row_id_basis", "name", "qualified_name",
            "callable_kind", "owner_kind", "owner_name", "signature_discriminator",
            "ordinal", "receiver_type_name", "receiver_is_pointer",
            "start_line", "end_line", "body_start_line", "body_end_line",
            "location_maps_to_original_source", "structural_complexity_status",
            "nloc_status", "nloc", "formal_parameter_count",
            "declares_typescript_this_parameter", "cyclomatic_complexity",
            "decision_point_count", "boolean_operator_count",
            "max_condition_operator_count", "max_nesting_depth",
            # Artifact Schema 1.10.0 / Complexity Contract 2.0.0.
            "cognitive_complexity",
        ):
            row[name] = getattr(item, name)
        rows.append(row)
    return rows


def _contribution_row(
    record: Any, analyzed: Any, contribution_state: str
) -> dict[str, Any]:
    """Persist one file's already-computed metric components.

    No metric is calculated here. ``analyzed.loc`` and ``analyzed.entities`` are
    the same dictionaries the aggregate sums, and the three derived values use
    the shared ``derive_*`` functions.

    A component is ``None`` when the corresponding extraction produced nothing.
    ``None`` means unavailable and must never be read as zero: a file whose
    parse failed contributed no entities, which is a different fact from a file
    that genuinely contains none (plan section 14.4 rule 4).
    """
    row: dict[str, Any] = {
        "relative_path": record.relative_path,
        "content_sha256": record.content_hash,
        "size_bytes": record.size_bytes,
        "detected_language": record.detected_language,
        "inclusion_state": "included_in_metrics",
        "contribution_state": contribution_state,
        "read_status": record.read_status,
        "parse_status": getattr(record, "parse_status", None),
        "parser_compatibility_strategy": record.parser_compatibility_strategy,
        "offset_mapping_mode": (
            "direct"
            if record.parser_offsets_map_directly_to_original_bytes
            else "adjusted"
        ),
    }

    if analyzed is None:
        row.update({
            "loc_status": "not_applicable",
            "classes_structs_status": "not_applicable",
            "methods_functions_status": "not_applicable",
            "structural_complexity_status": "not_applicable",
            "nloc_status": "not_applicable",
            "callable_count": None,
            "source_files_contribution": 0,
            "recovery_strategy": None,
            "error_reference": None,
            "recovery_reference": None,
            "lines_of_code": None,
            "classes_structs": None,
            "methods_functions": None,
        })
        for key in (*LOC_KEYS, *ENTITY_KEYS):
            row[key] = None
        return row

    row.update({
        "loc_status": analyzed.loc_status,
        "classes_structs_status": analyzed.classes_structs_status,
        "methods_functions_status": analyzed.methods_functions_status,
        "structural_complexity_status": analyzed.structural_complexity_status,
        "nloc_status": analyzed.nloc_status,
        # NULL when the measurement was unavailable; 0 only when the file was
        # measured and genuinely contains no canonical callable.
        "callable_count": (
            len(analyzed.callables)
            if analyzed.callables is not None
            and analyzed.structural_complexity_status in {"complete", "partial"}
            else None
        ),
        # A contributing row counts once toward Source Files, whatever happened
        # to its content: successful reading or parsing is not a prerequisite
        # for inclusion.
        "source_files_contribution": 1,
        "recovery_strategy": (
            (analyzed.recovery_diagnostic or {}).get("parser_compatibility_strategy")
            if analyzed.recovery_diagnostic else None
        ),
        "error_reference": analyzed.error_category,
        "recovery_reference": (
            (analyzed.recovery_diagnostic or {}).get("final_file_status")
            if analyzed.recovery_diagnostic else None
        ),
    })

    for key in LOC_KEYS:
        row[key] = analyzed.loc[key] if analyzed.loc is not None else None
    for key in ENTITY_KEYS:
        row[key] = analyzed.entities[key] if analyzed.entities is not None else None

    row["lines_of_code"] = (
        derive_lines_of_code(analyzed.loc) if analyzed.loc is not None else None
    )
    row["classes_structs"] = (
        derive_classes_structs(analyzed.entities)
        if analyzed.entities is not None else None
    )
    row["methods_functions"] = (
        derive_methods_functions(analyzed.entities)
        if analyzed.entities is not None else None
    )
    return row


def _finalize_metric(
    metric: dict[str, Any],
    statuses: dict[str, list[str]],
    inventory_status: str,
) -> None:
    metric["lines_of_code"] = derive_lines_of_code(metric)
    metric["classes_structs"] = derive_classes_structs(metric)
    metric["methods_functions"] = derive_methods_functions(metric)
    metric["inventory_status"] = inventory_status
    if metric["source_files"] == 0:
        source_status = "not_applicable" if inventory_status == "complete" else inventory_status
    else:
        source_status = "complete" if inventory_status == "complete" else inventory_status
    metric["source_files_status"] = source_status
    metric["loc_status"] = _account_for_inventory(
        _combined_extraction_status(statuses["loc"]), inventory_status
    )
    metric["classes_structs_status"] = _account_for_inventory(
        _combined_extraction_status(statuses["classes_structs"]), inventory_status
    )
    metric["methods_functions_status"] = _account_for_inventory(
        _combined_extraction_status(statuses["methods_functions"]), inventory_status
    )
    if metric["source_files"] == 0 and inventory_status == "complete":
        metric["metric_status"] = "not_applicable"
    else:
        metric["metric_status"] = _worst_status(
            inventory_status,
            source_status,
            metric["loc_status"],
            metric["classes_structs_status"],
            metric["methods_functions_status"],
        )
    if metric["loc_status"] == "failed":
        metric["lines_of_code"] = None
    if metric["classes_structs_status"] == "failed":
        metric["classes_structs"] = None
    if metric["methods_functions_status"] == "failed":
        metric["methods_functions"] = None


def compute_repository_metrics(
    inventory: RepositoryInventory,
    expected_language: str | None = None,
    parser_registry: ParserRegistry | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Compute aggregate, separate-language, and primary-language benchmark metrics."""
    registry = parser_registry or ParserRegistry()
    by_language = {language: _language_template() for language in SUPPORTED_LANGUAGES}
    statuses = {
        language: {name: [] for name in ("loc", "classes_structs", "methods_functions")}
        for language in SUPPORTED_LANGUAGES
    }
    parse_errors: list[dict[str, Any]] = []
    parser_diagnostics: list[dict[str, Any]] = []
    recovered_parser_diagnostics: list[dict[str, Any]] = []
    malformed_ast_nodes: list[dict[str, object]] = []
    included_records = [record for record in inventory if record.included_in_metrics]
    total_files = len(included_records)

    # Per-file contribution evidence, captured before aggregation discards it
    # (plan section 14). Nothing is recomputed here: every value is read off the
    # same FileMetricResult the aggregate consumes.
    contributions: list[dict[str, Any]] = []
    callable_records: list[dict[str, Any]] = []
    # File-level statuses, kept because a repository's complexity status cannot
    # be derived from the rows: a measured-and-empty file and an unmeasurable one
    # both contribute zero rows.
    complexity_file_statuses: list[str] = []
    complexity_status_by_language: dict[str, list[str]] = {}

    for index, record in enumerate(included_records, start=1):
        language = record.detected_language
        if language not in by_language:
            # An included file outside the supported metric languages stays
            # represented, with no contribution (plan section 14.1).
            contributions.append(
                _contribution_row(record, None, "no_contribution_unsupported_language")
            )
            continue
        metric = by_language[language]
        metric["source_files"] += 1
        source = inventory.read_bytes(record)
        if source is not None:
            metric["source_files_readable"] += 1
        else:
            metric["source_files_failed_read"] += int(record.read_status == "failed")
            metric["source_files_oversized"] += int(
                record.oversized or record.read_status == "skipped_oversized"
            )
        if source is None:
            category = (
                "source_oversized"
                if record.oversized or record.read_status == "skipped_oversized"
                else "source_read_failure"
            )
            message = record.read_error or f"File content unavailable ({record.read_status})"
            diagnostic = _base_diagnostic(record)
            diagnostic.update(
                {
                    "affected_metrics": ["lines_of_code", "classes_structs", "methods_functions"],
                    "stage": "read",
                    "final_file_status": "failed",
                    "error_category": category,
                    "message": message,
                }
            )
            analyzed = FileMetricResult(
                None,
                None,
                "failed",
                "failed",
                "failed",
                message,
                [],
                category,
                diagnostic,
            )
        else:
            inventory.record_parser_invocation()
            analyzed = _analyze_file(record, source, registry, inventory)
        if progress and (index == 1 or index == total_files or index % 25 == 0):
            progress(f"parsing source files {index}/{total_files}")
        record.loc_status = analyzed.loc_status
        record.classes_structs_status = analyzed.classes_structs_status
        record.methods_functions_status = analyzed.methods_functions_status
        record.parse_status = analyzed.status
        record.parse_error = analyzed.error
        record.malformed_nodes = analyzed.malformed_nodes
        record.error_category = analyzed.error_category
        record.parser_diagnostic = analyzed.diagnostic or analyzed.recovery_diagnostic
        contributions.append(_contribution_row(record, analyzed, "contributed"))
        callable_records.extend(_callable_rows(record, analyzed))
        complexity_file_statuses.append(analyzed.structural_complexity_status)
        if record.detected_language:
            complexity_status_by_language.setdefault(
                record.detected_language, []
            ).append(analyzed.structural_complexity_status)
        statuses[language]["loc"].append(analyzed.loc_status)
        statuses[language]["classes_structs"].append(analyzed.classes_structs_status)
        statuses[language]["methods_functions"].append(analyzed.methods_functions_status)
        if analyzed.loc is not None:
            metric["loc_files_counted"] += 1
            metric["source_files_loc_analyzed"] += 1
            for key in LOC_KEYS:
                metric[key] += analyzed.loc[key]
        if analyzed.entities is not None:
            metric["entity_files_parsed"] += 1
            metric["source_files_entity_parsed"] += 1
            for key in ENTITY_KEYS:
                metric[key] += analyzed.entities[key]
        if analyzed.status in {"partial", "failed"}:
            metric["parse_failure_count"] += 1
            if analyzed.status == "partial":
                metric["source_files_partial_parse"] += 1
            else:
                metric["source_files_failed_parse"] += 1
            metric["parse_failure_files"].append(record.relative_path)
            parse_errors.append(
                {
                    "file": record.relative_path,
                    "language": language,
                    "status": analyzed.status,
                    "error": analyzed.error or "unspecified parser failure",
                    "error_category": analyzed.error_category or "parser_execution_failure",
                    "loc_status": analyzed.loc_status,
                    "classes_structs_status": analyzed.classes_structs_status,
                    "methods_functions_status": analyzed.methods_functions_status,
                }
            )
        if analyzed.diagnostic is not None:
            parser_diagnostics.append(analyzed.diagnostic)
        if analyzed.recovery_diagnostic is not None:
            metric["source_files_recovered_parse"] += 1
            recovered_parser_diagnostics.append(analyzed.recovery_diagnostic)
        malformed_ast_nodes.extend(
            {"file": record.relative_path, **node} for node in analyzed.malformed_nodes
        )

    for language in SUPPORTED_LANGUAGES:
        _finalize_metric(by_language[language], statuses[language], inventory.inventory_status)

    aggregate = _language_template()
    aggregate_statuses = {name: [] for name in ("loc", "classes_structs", "methods_functions")}
    for language in SUPPORTED_LANGUAGES:
        metric = by_language[language]
        if metric["source_files"]:
            for name in aggregate_statuses:
                aggregate_statuses[name].extend(statuses[language][name])
        aggregate["source_files"] += metric["source_files"]
        aggregate["parse_failure_count"] += metric["parse_failure_count"]
        aggregate["loc_files_counted"] += metric["loc_files_counted"]
        aggregate["entity_files_parsed"] += metric["entity_files_parsed"]
        for key in (
            "source_files_readable", "source_files_loc_analyzed", "source_files_entity_parsed",
            "source_files_failed_read", "source_files_oversized",
            "source_files_partial_parse", "source_files_failed_parse",
            "source_files_recovered_parse",
        ):
            aggregate[key] += metric[key]
        aggregate["parse_failure_files"].extend(metric["parse_failure_files"])
        for key in (*LOC_KEYS, *ENTITY_KEYS):
            aggregate[key] += metric[key]
    _finalize_metric(aggregate, aggregate_statuses, inventory.inventory_status)
    repository_metric_statuses = {
        "metric_status": aggregate["metric_status"],
        "inventory_status": aggregate["inventory_status"],
        "source_files_status": aggregate["source_files_status"],
        "loc_status": aggregate["loc_status"],
        "classes_structs_status": aggregate["classes_structs_status"],
        "methods_functions_status": aggregate["methods_functions_status"],
    }
    for diagnostic in parser_diagnostics:
        diagnostic["final_repository_metric_statuses"] = dict(repository_metric_statuses)
    for diagnostic in recovered_parser_diagnostics:
        diagnostic["final_repository_metric_statuses"] = dict(repository_metric_statuses)

    candidates = [language for language in SUPPORTED_LANGUAGES if by_language[language]["source_files"]]
    normalized_expected = next(
        (language for language in SUPPORTED_LANGUAGES if expected_language and language.casefold() == expected_language.casefold()),
        None,
    )
    expected_language_mismatch = bool(
        expected_language and (
            normalized_expected is None or by_language[normalized_expected]["source_files"] == 0
        )
    )
    ranked = sorted(
        candidates,
        key=lambda language: (
            -by_language[language]["code_lines"],
            -by_language[language]["source_files"],
            SUPPORTED_LANGUAGES.index(language),
        ),
    )
    fallback_primary = ranked[0] if ranked else None
    primary_language_tie = bool(
        len(ranked) > 1
        and (
            by_language[ranked[0]]["code_lines"], by_language[ranked[0]]["source_files"]
        )
        == (
            by_language[ranked[1]]["code_lines"], by_language[ranked[1]]["source_files"]
        )
    )
    primary_name = (
        normalized_expected
        if normalized_expected and by_language[normalized_expected]["source_files"]
        else fallback_primary
    )
    primary = dict(by_language[primary_name]) if primary_name else _language_template()
    source_files_by_language = {
        language.lower(): by_language[language]["source_files"] for language in SUPPORTED_LANGUAGES
    }
    language_status = {
        language.lower(): by_language[language]["metric_status"] for language in SUPPORTED_LANGUAGES
    }
    javascript_family_inventory = inventory.summary()["javascript_family_inventory"]
    javascript_family_inventory["grammar_by_extension"] = {
        ".js": "javascript",
        ".jsx": "javascript_jsx_capable",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".mts": "typescript",
        ".cts": "typescript",
    }
    return {
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "aggregate": aggregate,
        "by_language": {language.lower(): by_language[language] for language in SUPPORTED_LANGUAGES},
        "primary_language": primary,
        "primary_language_name": primary_name,
        "expected_language_mismatch": expected_language_mismatch,
        "primary_language_tie": primary_language_tie,
        "source_files_total": aggregate["source_files"],
        "source_files_by_language": source_files_by_language,
        "parser_status_by_language": language_status,
        "parser_status": aggregate["metric_status"],
        "parse_errors": parse_errors,
        "parser_diagnostics": parser_diagnostics,
        # Deterministic ordering (plan section 14.4 rule 8).
        "file_contributions": sorted(
            contributions, key=lambda item: str(item.get("relative_path") or "")
        ),
        "callable_records": sorted(
            callable_records,
            key=lambda item: (
                str(item.get("relative_path") or ""),
                int(item.get("start_line") or 0),
                str(item.get("callable_row_id") or ""),
            ),
        ),
        "complexity": _derive_complexity_summary(
            callable_records, complexity_file_statuses, complexity_status_by_language
        ),
        "recovered_parser_diagnostics": recovered_parser_diagnostics,
        "recovery_taxonomy": dict(
            sorted(
                Counter(
                    strategy
                    for item in recovered_parser_diagnostics
                    for strategy in item.get("fallback_strategies", [])
                ).items()
            )
        ),
        "javascript_family_scope": javascript_family_inventory,
        "error_taxonomy": dict(
            sorted(Counter(item["error_category"] for item in parser_diagnostics).items())
        ),
        "malformed_ast_nodes": malformed_ast_nodes,
        "warnings": (
            [
                f"Expected language {expected_language} has no included source files; "
                f"selected {primary_name or 'no primary language'} from observed metrics"
            ]
            if expected_language_mismatch
            else []
        ),
        "definitions": {
            "lines_of_code": "physical source lines containing code, excluding blank and comment-only lines",
            "multiline_strings": "syntactic string literals, including Python docstrings, are treated as code",
            "source_files": "recognized first-party production source files after exclusions; successful parsing is not required",
            "classes_structs": "named Java classes/records, stable JavaScript/TypeScript classes, Python classes, and Go named structs",
            "methods_functions": "named implementation-bearing module/exported functions, direct methods of counted named classes or objects, and Go receiver methods; constructors, anonymous class methods, anonymous callables, lambdas, nested functions, and signatures are excluded",
        },
    }


def compatibility_static_result(metrics: dict[str, Any]) -> dict[str, Any]:
    """Expose historical flat keys without changing the new scientific contract."""
    aggregate = metrics["aggregate"]
    display_names = {language.lower(): language for language in SUPPORTED_LANGUAGES}
    language_breakdown = {
        display_names[language]: {
            "loc": value["lines_of_code"],
            "source_files": value["source_files"],
            "classes_structs": value["classes_structs"],
            "methods_functions": value["methods_functions"],
            "parse_failures": value["parse_failure_count"],
            "loc_status": value["loc_status"],
            "classes_structs_status": value["classes_structs_status"],
            "methods_functions_status": value["methods_functions_status"],
        }
        for language, value in metrics["by_language"].items()
        if value["source_files"]
    }
    status = aggregate["metric_status"]
    return {
        "loc": aggregate["lines_of_code"],
        "source_files": aggregate["source_files"],
        "classes": aggregate["classes_structs"],
        "methods": aggregate["methods_functions"],
        "classes_structs": aggregate["classes_structs"],
        "methods_functions": aggregate["methods_functions"],
        "primary_language": metrics["primary_language_name"],
        "expected_language_mismatch": metrics.get("expected_language_mismatch", False),
        "language_breakdown": language_breakdown,
        "parse_failure_count": aggregate["parse_failure_count"],
        "parse_failure_files": aggregate["parse_failure_files"],
        "metric_extraction_status": status,
        "metric_status": status,
        "inventory_status": aggregate["inventory_status"],
        "source_files_status": aggregate["source_files_status"],
        "loc_status": aggregate["loc_status"],
        "classes_structs_status": aggregate["classes_structs_status"],
        "methods_functions_status": aggregate["methods_functions_status"],
        "source_files_readable": aggregate["source_files_readable"],
        "source_files_loc_analyzed": aggregate["source_files_loc_analyzed"],
        "source_files_entity_parsed": aggregate["source_files_entity_parsed"],
        "source_files_failed_read": aggregate["source_files_failed_read"],
        "source_files_oversized": aggregate["source_files_oversized"],
        "source_files_partial_parse": aggregate["source_files_partial_parse"],
        "source_files_failed_parse": aggregate["source_files_failed_parse"],
        "loc_definition": metrics["definitions"]["lines_of_code"],
        "classes_structs_definition": metrics["definitions"]["classes_structs"],
        "methods_functions_definition": metrics["definitions"]["methods_functions"],
        "metrics": metrics,
    }
