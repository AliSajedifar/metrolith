"""Consumer-neutral construction of the syntax tree selected for analysis.

This module owns the one parser-selection path shared by metric and duplication
consumers.  It deliberately knows nothing about metrics, duplication, policy,
SARIF, or artifact writers.  Source inclusion remains the inventory's job;
given one included record and its immutable bytes, this module returns the
selected parser view, root, parse state, recovery evidence, and source mapping.
"""

from __future__ import annotations

import ast
import importlib.metadata
import platform
import re
from dataclasses import dataclass
from typing import Any, Callable

from modules.inventory import FileRecord
from modules.syntax_predicates import _iter_all_nodes, _iter_nodes, _node_is_malformed


JAVASCRIPT_COMPATIBILITY_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs"})
TYPESCRIPT_COMPATIBILITY_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})
JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT = 64
JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES = 1024 * 1024
JAVASCRIPT_RESERVED_WORDS = frozenset(
    {
        "await", "break", "case", "catch", "class", "const", "continue",
        "debugger", "default", "delete", "do", "else", "enum", "export",
        "extends", "false", "finally", "for", "function", "if", "implements",
        "import", "in", "instanceof", "interface", "let", "new", "null",
        "package", "private", "protected", "public", "return", "static",
        "super", "switch", "this", "throw", "true", "try", "typeof", "var",
        "void", "while", "with", "yield",
    }
)
GRAMMAR_IDENTITIES = {
    "Java": ("java", "tree-sitter-java"),
    "JavaScript": ("javascript", "tree-sitter-javascript"),
    "TypeScript": ("typescript", "tree-sitter-typescript"),
    "TSX": ("tsx", "tree-sitter-typescript"),
    "Go": ("go", "tree-sitter-go"),
}


class ParserUnavailableError(RuntimeError):
    pass


class SourceEncodingError(RuntimeError):
    pass


class ParserRegistry:
    """Lazily initialize and retain the exact parser instances used by a run."""

    def __init__(self):
        self.parsers: dict[str, Any] = {}
        self.errors: dict[str, str] = {}

    def _build(self, language: str, extension: str | None = None):
        try:
            from tree_sitter import Language, Parser

            if language == "Java":
                import tree_sitter_java

                capsule = tree_sitter_java.language()
            elif language == "JavaScript":
                import tree_sitter_javascript

                capsule = tree_sitter_javascript.language()
            elif language == "TypeScript":
                import tree_sitter_typescript

                capsule = (
                    tree_sitter_typescript.language_tsx()
                    if extension == ".tsx"
                    else tree_sitter_typescript.language_typescript()
                )
            elif language == "Go":
                import tree_sitter_go

                capsule = tree_sitter_go.language()
            else:
                raise ParserUnavailableError(f"Tree-sitter is not used for {language}")
            return Parser(Language(capsule))
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise ParserUnavailableError(
                f"{language} parser initialization failed: {exc}"
            ) from exc

    def get(self, language: str, extension: str | None = None):
        key = "TSX" if language == "TypeScript" and extension == ".tsx" else language
        if key in self.errors:
            raise ParserUnavailableError(self.errors[key])
        if key not in self.parsers:
            try:
                self.parsers[key] = self._build(language, extension)
            except ParserUnavailableError as exc:
                self.errors[key] = str(exc)
                raise
        return self.parsers[key]

    def validate(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for language, extension in (
            ("Java", ".java"),
            ("JavaScript", ".js"),
            ("TypeScript", ".ts"),
            ("TypeScript", ".tsx"),
            ("Go", ".go"),
        ):
            key = "TSX" if extension == ".tsx" else language
            try:
                self.get(language, extension)
                statuses[key] = "complete"
            except ParserUnavailableError:
                statuses[key] = "failed"
        statuses["Python"] = "complete"
        return statuses


def validate_parser_initialization() -> dict[str, str]:
    return ParserRegistry().validate()


def grammar_identity(language: str | None, extension: str) -> dict[str, str | None]:
    if language == "Python":
        return {
            "parser_implementation": "cpython-ast",
            "selected_grammar": "python",
            "grammar_package": "Python",
            "grammar_version": platform.python_version(),
        }
    key = "TSX" if language == "TypeScript" and extension == ".tsx" else language
    grammar, package = GRAMMAR_IDENTITIES.get(key or "", (None, None))
    version = None
    if package:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {
        "parser_implementation": "tree-sitter" if package else None,
        "selected_grammar": grammar,
        "grammar_package": package,
        "grammar_version": version,
    }


def malformed_counts(root: Any) -> tuple[int, int]:
    malformed = [node for node in _iter_all_nodes(root) if _node_is_malformed(node)]
    return (
        sum(node.type == "ERROR" and not node.is_missing for node in malformed),
        sum(bool(node.is_missing) for node in malformed),
    )


def successfully_parsed_byte_coverage(root: Any, source_length: int) -> int:
    spans = sorted(
        (
            max(0, min(node.start_byte, source_length)),
            max(0, min(node.end_byte, source_length)),
        )
        for node in _iter_all_nodes(root)
        if _node_is_malformed(node) and node.end_byte > node.start_byte
    )
    covered = 0
    start = end = None
    for next_start, next_end in spans:
        if start is None:
            start, end = next_start, next_end
        elif next_start <= end:
            end = max(end, next_end)
        else:
            covered += end - start
            start, end = next_start, next_end
    if start is not None:
        covered += end - start
    return max(0, source_length - covered)


def _replace_offsets(source: bytes, replacements: dict[int, bytes]) -> bytes:
    transformed = bytearray(source)
    for offset in sorted(replacements, reverse=True):
        transformed[offset : offset + 1] = replacements[offset]
    return bytes(transformed)


def _rewrite_json_import_assertions(source: bytes, root: Any) -> bytes:
    replacements: dict[int, bytes] = {}
    json_source = re.compile(rb"(?:from\s*)?[\"'][^\"'\r\n]+\.json[\"']", re.IGNORECASE)
    json_type = re.compile(
        rb"\bassert\b\s*\{[^{}]*\btype\s*:\s*[\"']json[\"']", re.IGNORECASE
    )
    for node in _iter_nodes(root):
        if node.type != "import_statement":
            continue
        statement = source[node.start_byte : node.end_byte]
        if not json_source.search(statement) or not json_type.search(statement):
            continue
        for match in re.finditer(rb"\bassert\b(?=\s*\{)", statement):
            replacements[node.start_byte + match.start()] = b"with  "
    if not replacements:
        return source
    transformed = bytearray(source)
    for offset in sorted(replacements, reverse=True):
        transformed[offset : offset + 6] = replacements[offset]
    return bytes(transformed)


def _protected_javascript_ranges(root: Any) -> list[tuple[int, int]]:
    return sorted(
        (node.start_byte, node.end_byte)
        for node in _iter_all_nodes(root)
        if node.type in {"comment", "regex", "string", "template_string"}
    )


def _inside_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _skip_jsx_expression(source: bytes, offset: int) -> int:
    depth = 0
    quote: int | None = None
    index = offset
    while index < len(source):
        byte = source[index]
        if quote is not None:
            if byte == 92:
                index += 2
                continue
            if byte == quote:
                quote = None
        elif byte in {34, 39, 96}:
            quote = byte
        elif source[index : index + 2] == b"//":
            newline = source.find(b"\n", index + 2)
            index = len(source) if newline < 0 else newline
            continue
        elif source[index : index + 2] == b"/*":
            closing = source.find(b"*/", index + 2)
            index = len(source) if closing < 0 else closing + 2
            continue
        elif byte == 123:
            depth += 1
        elif byte == 125:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(source)


def _jsx_tag_reserved_attribute_offsets(source: bytes, root: Any) -> list[int]:
    malformed_ranges = [
        (node.start_byte, node.end_byte)
        for node in _iter_all_nodes(root)
        if _node_is_malformed(node)
    ]
    protected_ranges = _protected_javascript_ranges(root)
    offsets: list[int] = []
    index = 0
    name_start_bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
    name_bytes = name_start_bytes + b"0123456789:.-"
    whitespace = b" \t\r\n"
    while index < len(source):
        tag_start = source.find(b"<", index)
        if tag_start < 0:
            break
        index = tag_start + 1
        if _inside_ranges(tag_start, protected_ranges):
            continue
        cursor = tag_start + 1
        if (
            cursor >= len(source)
            or source[cursor] in b"/!?>"
            or source[cursor] not in name_start_bytes
        ):
            continue
        while cursor < len(source) and source[cursor] in name_bytes:
            cursor += 1
        candidate_offsets: list[int] = []
        tag_end: int | None = None
        while cursor < len(source):
            while cursor < len(source) and source[cursor] in whitespace:
                cursor += 1
            if cursor >= len(source):
                break
            if source[cursor : cursor + 2] == b"/>":
                tag_end = cursor + 2
                break
            if source[cursor] == 62:
                tag_end = cursor + 1
                break
            if source[cursor] == 123:
                cursor = _skip_jsx_expression(source, cursor)
                continue
            if source[cursor] not in name_start_bytes:
                break
            attribute_start = cursor
            while cursor < len(source) and source[cursor] in name_bytes:
                cursor += 1
            attribute = source[attribute_start:cursor].decode("ascii", errors="ignore")
            if attribute in JAVASCRIPT_RESERVED_WORDS:
                candidate_offsets.append(attribute_start)
            while cursor < len(source) and source[cursor] in whitespace:
                cursor += 1
            if cursor < len(source) and source[cursor] == 61:
                cursor += 1
                while cursor < len(source) and source[cursor] in whitespace:
                    cursor += 1
                if cursor < len(source) and source[cursor] in {34, 39}:
                    quote = source[cursor]
                    cursor += 1
                    while cursor < len(source):
                        if source[cursor] == 92:
                            cursor += 2
                            continue
                        if source[cursor] == quote:
                            cursor += 1
                            break
                        cursor += 1
                elif cursor < len(source) and source[cursor] == 123:
                    cursor = _skip_jsx_expression(source, cursor)
                else:
                    while cursor < len(source) and source[cursor] not in whitespace + b">":
                        cursor += 1
        if tag_end is not None and any(
            malformed_start < tag_end and malformed_end > tag_start
            for malformed_start, malformed_end in malformed_ranges
        ):
            offsets.extend(candidate_offsets)
            index = tag_end
    return sorted(set(offsets))


def _rewrite_reserved_jsx_attributes(source: bytes, root: Any) -> bytes:
    offsets = _jsx_tag_reserved_attribute_offsets(source, root)
    return _replace_offsets(source, {offset: b"_" for offset in offsets}) if offsets else source


def _has_jsx_text_or_attribute_context(node: Any) -> bool:
    current = node.parent
    in_quoted_attribute = False
    in_jsx_text = False
    while current is not None:
        if current.type == "jsx_expression":
            return False
        if current.type == "jsx_attribute":
            in_quoted_attribute = True
        if current.type == "jsx_text":
            in_jsx_text = True
        if current.type in {"jsx_element", "jsx_fragment"}:
            return in_quoted_attribute or in_jsx_text or node.parent == current
        current = current.parent
    return False


def _rewrite_raw_jsx_ampersands(source: bytes, root: Any) -> bytes:
    replacements: dict[int, bytes] = {}
    for node in _iter_all_nodes(root):
        if (
            node.type == "ERROR"
            and source[node.start_byte : node.start_byte + 1] == b"&"
            and _has_jsx_text_or_attribute_context(node)
        ):
            replacements[node.start_byte] = b" "
    return _replace_offsets(source, replacements) if replacements else source


TYPESCRIPT_KEYWORD_PARAMETER = re.compile(
    rb"(?P<prefix>\(\s*)(?P<keyword>any|boolean|string)(?P<suffix>\s*\)\s*=>)"
)
TYPED_JAVASCRIPT_PARAMETER_ANNOTATION = re.compile(
    rb"(?:\(|,)\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\??\s*:\s*"
    rb"(?:[A-Za-z_$][A-Za-z0-9_$.<>|&?\[\]]*|\{|\[)(?=\s*(?:,|\)))"
)


def _rewrite_typescript_keyword_parameters(source: bytes, root: Any) -> bytes:
    malformed_ranges = [
        (node.start_byte, max(node.end_byte, node.start_byte + 1))
        for node in _iter_all_nodes(root)
        if _node_is_malformed(node)
    ]
    replacements: dict[int, bytes] = {}
    for match in TYPESCRIPT_KEYWORD_PARAMETER.finditer(source):
        preceding = source[max(0, match.start() - 512) : match.start()]
        stripped = preceding.rstrip()
        function_type_context = bool(
            stripped.endswith(b":")
            or re.search(
                rb"\b(?:export\s+)?type\s+[A-Za-z_$][A-Za-z0-9_$<>\s,]*=\s*$",
                preceding,
                re.DOTALL,
            )
        )
        if not function_type_context:
            continue
        start, _end = match.span("keyword")
        match_start, match_end = match.span()
        if any(
            match_start < malformed_end and match_end > malformed_start
            for malformed_start, malformed_end in malformed_ranges
        ):
            replacements[start] = b"_"
    return _replace_offsets(source, replacements) if replacements else source


def _looks_like_typed_javascript(source: bytes, root: Any) -> bool:
    malformed_starts = [
        node.start_byte for node in _iter_all_nodes(root) if _node_is_malformed(node)
    ]
    return any(
        any(match.start() - 1 <= start <= match.end() + 8 for start in malformed_starts)
        for match in TYPED_JAVASCRIPT_PARAMETER_ANNOTATION.finditer(source)
    )


@dataclass(slots=True)
class TreeCandidate:
    tree: Any
    source: bytes
    strategies: tuple[str, ...]
    order: int
    offsets_match_original: bool


def _candidate_score(candidate: TreeCandidate, original_length: int) -> tuple[int, int, int, int]:
    errors, missing = malformed_counts(candidate.tree.root_node)
    coverage = successfully_parsed_byte_coverage(
        candidate.tree.root_node, min(len(candidate.source), original_length)
    )
    return errors, missing, -coverage, candidate.order


def _strictly_improves(candidate: TreeCandidate, baseline: TreeCandidate) -> bool:
    candidate_errors, candidate_missing = malformed_counts(candidate.tree.root_node)
    baseline_errors, baseline_missing = malformed_counts(baseline.tree.root_node)
    return bool(
        candidate_errors <= baseline_errors
        and candidate_missing <= baseline_missing
        and (candidate_errors < baseline_errors or candidate_missing < baseline_missing)
    )


def compatibility_parse(
    parser: Any,
    source: bytes,
    primary_tree: Any,
    transformations: tuple[tuple[str, Callable[[bytes, Any], bytes]], ...],
    family_label: str,
    *,
    iteration_limit: int = JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT,
    growth_limit_bytes: int = JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES,
) -> tuple[TreeCandidate, TreeCandidate | None, tuple[str, ...], str | None]:
    primary = TreeCandidate(primary_tree, source, (), 0, True)
    candidates = [primary]
    current_source = bytes(bytearray(source))
    current_tree = primary_tree
    applied: list[str] = []
    iterations = 0
    limit_error: str | None = None
    for strategy, transformation in transformations:
        while True:
            if iterations >= iteration_limit:
                limit_error = (
                    f"{family_label} compatibility rewrite iteration limit exceeded "
                    f"({iteration_limit})"
                )
                break
            transformed = transformation(current_source, current_tree.root_node)
            if transformed == current_source:
                break
            growth = len(transformed) - len(source)
            if growth > growth_limit_bytes:
                limit_error = (
                    f"{family_label} compatibility rewrite growth limit exceeded "
                    f"({growth} > {growth_limit_bytes} bytes)"
                )
                break
            iterations += 1
            current_source = transformed
            if strategy not in applied:
                applied.append(strategy)
            current_tree = parser.parse(current_source)
            candidates.append(
                TreeCandidate(
                    current_tree,
                    current_source,
                    tuple(applied),
                    len(candidates),
                    len(current_source) == len(source),
                )
            )
            if malformed_counts(current_tree.root_node) == (0, 0):
                break
        if limit_error is not None or malformed_counts(current_tree.root_node) == (0, 0):
            break
    improving = [candidate for candidate in candidates[1:] if _strictly_improves(candidate, primary)]
    best = min(improving, key=lambda item: _candidate_score(item, len(source))) if improving else primary
    best_fallback = (
        min(candidates[1:], key=lambda item: _candidate_score(item, len(source)))
        if len(candidates) > 1
        else None
    )
    return best, best_fallback, tuple(applied), limit_error


def javascript_compatibility_parse(
    parser: Any,
    source: bytes,
    primary_tree: Any,
    *,
    transformations: tuple[tuple[str, Callable[[bytes, Any], bytes]], ...] | None = None,
    iteration_limit: int = JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT,
    growth_limit_bytes: int = JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES,
):
    return compatibility_parse(
        parser,
        source,
        primary_tree,
        transformations
        or (
            ("javascript_import_assertion_compat", _rewrite_json_import_assertions),
            ("jsx_reserved_attribute_compat", _rewrite_reserved_jsx_attributes),
            ("raw_jsx_ampersand_compat", _rewrite_raw_jsx_ampersands),
        ),
        "JavaScript",
        iteration_limit=iteration_limit,
        growth_limit_bytes=growth_limit_bytes,
    )


def typescript_compatibility_parse(
    parser: Any,
    source: bytes,
    primary_tree: Any,
    *,
    transformations: tuple[tuple[str, Callable[[bytes, Any], bytes]], ...] | None = None,
    iteration_limit: int = JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT,
    growth_limit_bytes: int = JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES,
):
    return compatibility_parse(
        parser,
        source,
        primary_tree,
        transformations
        or (
            ("raw_jsx_ampersand_compat", _rewrite_raw_jsx_ampersands),
            ("typescript_keyword_parameter_compat", _rewrite_typescript_keyword_parameters),
        ),
        "TypeScript",
        iteration_limit=iteration_limit,
        growth_limit_bytes=growth_limit_bytes,
    )


def parser_compatible_source(record: FileRecord, source: bytes) -> bytes:
    if record.encoding_error:
        raise SourceEncodingError(record.encoding_error)
    parser_source = source
    strategies: list[str] = []
    adjustment = 0
    if parser_source.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            decoded = parser_source.decode("utf-16", errors="strict")
        except UnicodeDecodeError as exc:
            raise SourceEncodingError(f"Invalid UTF-16 source: {exc}") from exc
        parser_source = decoded.encode("utf-8")
        record.original_encoding = "utf-16-le" if source.startswith(b"\xff\xfe") else "utf-16-be"
        record.parser_encoding = "utf-8"
        record.encoding_transformation_applied = "utf16_to_utf8"
        record.parser_offsets_map_directly_to_original_bytes = False
        record.original_byte_offsets_available = False
        strategies.append("utf16_to_utf8")
    elif b"\x00" in parser_source:
        if record.nul_classification != "low_density_intentional_textual_nul":
            raise SourceEncodingError("NUL byte found without trusted low-density textual context")
        parser_view = bytearray(parser_source)
        for position in record.nul_positions:
            if position >= len(parser_view) or parser_view[position] != 0:
                raise SourceEncodingError("NUL metadata does not match the immutable source bytes")
            parser_view[position] = 32
        parser_source = bytes(parser_view)
        strategies.append("low_density_textual_nul_compat")
    elif parser_source.startswith(b"\xef\xbb\xbf"):
        parser_source = parser_source[3:]
        adjustment = 3
        strategies.append("utf8_bom_removed")
    if b"\r" in parser_source:
        normalized = re.sub(rb"\r(?!\n)", b"\n", parser_source)
        if normalized != parser_source:
            parser_source = normalized
            strategies.append("lone_cr_to_lf")
    record.parser_normalization_applied = bool(strategies)
    record.parser_compatibility_strategy = "+".join(strategies) or None
    record.parser_byte_offset_adjustment = adjustment
    record.original_byte_length = len(source)
    record.parser_byte_length = len(parser_source)
    return parser_source


def _newline_offsets(source: bytes) -> tuple[int, ...]:
    return tuple(index for index, value in enumerate(source) if value in {10, 13})


@dataclass(frozen=True, slots=True)
class SourceMapping:
    parser_byte_offset_adjustment: int
    offsets_map_directly_to_original_bytes: bool
    original_byte_offsets_available: bool
    original_byte_length: int
    parser_byte_length: int
    selected_byte_length: int
    newline_positions_preserved: bool
    selected_line_starts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SyntaxDiagnostic:
    category: str
    message: str
    error_count: int = 0
    missing_count: int = 0


@dataclass(slots=True)
class SelectedSyntax:
    language: str
    extension: str
    evidence_source: bytes
    evidence_text: str | None
    parser_source: bytes
    selected_source: bytes
    primary_root: Any
    root: Any
    parse_status: str
    selected_parse: str
    selected_strategies: tuple[str, ...]
    all_applied_strategies: tuple[str, ...]
    grammar: dict[str, str | None]
    mapping: SourceMapping
    diagnostics: tuple[SyntaxDiagnostic, ...] = ()
    primary_tree: Any = None
    selected_tree: Any = None
    best_fallback: TreeCandidate | None = None
    compatibility_limit_error: str | None = None
    fallback_grammar: str | None = None
    unsupported_typed_javascript: bool = False
    parse_exception: BaseException | None = None

    @property
    def eligible_for_duplication(self) -> bool:
        return self.parse_status in {"complete", "recovered"} and (
            self.mapping.newline_positions_preserved
            and len(self.selected_source) == len(self.parser_source)
        )


def _mapping(record: FileRecord, parser_source: bytes, selected_source: bytes) -> SourceMapping:
    starts = [0]
    starts.extend(index + 1 for index, value in enumerate(selected_source) if value == 10)
    return SourceMapping(
        parser_byte_offset_adjustment=record.parser_byte_offset_adjustment,
        offsets_map_directly_to_original_bytes=(
            record.parser_offsets_map_directly_to_original_bytes
            and record.parser_byte_offset_adjustment == 0
        ),
        original_byte_offsets_available=record.original_byte_offsets_available,
        original_byte_length=record.original_byte_length or 0,
        parser_byte_length=len(parser_source),
        selected_byte_length=len(selected_source),
        newline_positions_preserved=(
            len(selected_source) == len(parser_source)
            and _newline_offsets(selected_source) == _newline_offsets(parser_source)
        ),
        selected_line_starts=tuple(starts),
    )


def select_syntax(
    record: FileRecord,
    source: bytes,
    registry: ParserRegistry,
    *,
    python_text: str | None = None,
    compatibility_iteration_limit: int = JAVASCRIPT_COMPATIBILITY_ITERATION_LIMIT,
    compatibility_growth_limit_bytes: int = JAVASCRIPT_COMPATIBILITY_GROWTH_LIMIT_BYTES,
    javascript_transformations: tuple[
        tuple[str, Callable[[bytes, Any], bytes]], ...
    ] | None = None,
    typescript_transformations: tuple[
        tuple[str, Callable[[bytes, Any], bytes]], ...
    ] | None = None,
) -> SelectedSyntax:
    """Build the exact syntax view historically selected by core metrics."""
    language = record.detected_language or ""
    if language == "Python":
        if python_text is None:
            raise SourceEncodingError(record.encoding_error or "Python source decoding failed")
        parser_source = python_text.encode("utf-8")
        record.parser_encoding = "utf-8"
        record.original_byte_length = len(source)
        record.parser_byte_length = len(parser_source)
        parse_exception: BaseException | None = None
        try:
            root = ast.parse(python_text)
            status = "complete"
            diagnostics: tuple[SyntaxDiagnostic, ...] = ()
        except SyntaxError as exc:
            root = None
            parse_exception = exc
            status = "failed"
            diagnostics = (
                SyntaxDiagnostic(
                    "syntax_failed",
                    f"Python syntax error at line {exc.lineno}: {exc.msg}",
                ),
            )
        return SelectedSyntax(
            language="Python",
            extension=record.extension,
            evidence_source=source,
            evidence_text=python_text,
            parser_source=parser_source,
            selected_source=parser_source,
            primary_root=root,
            root=root,
            parse_status=status,
            selected_parse="cpython_ast",
            selected_strategies=(),
            all_applied_strategies=(),
            grammar=grammar_identity("Python", record.extension),
            mapping=_mapping(record, parser_source, parser_source),
            diagnostics=diagnostics,
            parse_exception=parse_exception,
        )

    parser = registry.get(language, record.extension)
    parser_source = parser_compatible_source(record, source)
    tree = parser.parse(parser_source)
    primary_root = tree.root_node
    selected = TreeCandidate(tree, parser_source, (), 0, True)
    best_fallback = None
    applied: tuple[str, ...] = ()
    limit_error = None
    fallback_grammar = None
    unsupported_typed_javascript = False
    if (
        language in {"JavaScript", "TypeScript"}
        and record.extension
        in (
            JAVASCRIPT_COMPATIBILITY_EXTENSIONS
            if language == "JavaScript"
            else TYPESCRIPT_COMPATIBILITY_EXTENSIONS
        )
        and malformed_counts(primary_root) != (0, 0)
    ):
        compatibility = (
            javascript_compatibility_parse
            if language == "JavaScript"
            else typescript_compatibility_parse
        )
        selected, best_fallback, applied, limit_error = compatibility(
            parser,
            parser_source,
            tree,
            transformations=(
                javascript_transformations
                if language == "JavaScript"
                else typescript_transformations
            ),
            iteration_limit=compatibility_iteration_limit,
            growth_limit_bytes=compatibility_growth_limit_bytes,
        )
        fallback_grammar = (
            "javascript"
            if language == "JavaScript"
            else "tsx" if record.extension == ".tsx" else "typescript"
        )
        if (
            language == "JavaScript"
            and malformed_counts(selected.tree.root_node) != (0, 0)
            and _looks_like_typed_javascript(selected.source, selected.tree.root_node)
        ):
            if record.typed_javascript_dialect_evidence:
                tsx_parser = registry.get("TypeScript", ".tsx")
                tsx_tree = tsx_parser.parse(selected.source)
                typed_candidate = TreeCandidate(
                    tsx_tree,
                    selected.source,
                    (*selected.strategies, "typed_javascript_tsx_compat"),
                    selected.order + 1,
                    len(selected.source) == len(parser_source),
                )
                if _strictly_improves(typed_candidate, selected):
                    selected = typed_candidate
                    best_fallback = typed_candidate
                    applied = (*applied, "typed_javascript_tsx_compat")
                    fallback_grammar = "tsx"
            else:
                unsupported_typed_javascript = True

    errors, missing = malformed_counts(selected.tree.root_node)
    if limit_error is not None:
        status = "partial"
        diagnostics = (SyntaxDiagnostic("parser_compatibility_limit_exceeded", limit_error, errors, missing),)
    elif (errors, missing) == (0, 0) and selected.order:
        status = "recovered"
        diagnostics = (
            SyntaxDiagnostic(
                "syntax_recovered",
                f"{language} syntax recovered with in-memory compatibility fallback: "
                + ", ".join(selected.strategies),
            ),
        )
    elif (errors, missing) == (0, 0):
        status = "complete"
        diagnostics = ()
    else:
        status = "partial"
        category = "unsupported_javascript_dialect" if unsupported_typed_javascript else "syntax_partial"
        message = (
            "Typed JavaScript syntax is present without explicit repository dialect evidence"
            if unsupported_typed_javascript
            else "Tree-sitter syntax errors remain after compatibility fallback"
            if best_fallback is not None
            else "Tree-sitter syntax errors remain; no compatibility strategy applied"
        )
        diagnostics = (SyntaxDiagnostic(category, message, errors, missing),)

    return SelectedSyntax(
        language=language,
        extension=record.extension,
        evidence_source=source,
        evidence_text=None,
        parser_source=parser_source,
        selected_source=selected.source,
        primary_root=primary_root,
        root=selected.tree.root_node,
        parse_status=status,
        selected_parse="primary" if selected.order == 0 else "fallback",
        selected_strategies=selected.strategies,
        all_applied_strategies=applied,
        grammar=grammar_identity(language, record.extension),
        mapping=_mapping(record, parser_source, selected.source),
        diagnostics=diagnostics,
        primary_tree=tree,
        selected_tree=selected.tree,
        best_fallback=best_fallback,
        compatibility_limit_error=limit_error,
        fallback_grammar=fallback_grammar,
        unsupported_typed_javascript=unsupported_typed_javascript,
    )


__all__ = [
    "ParserRegistry",
    "ParserUnavailableError",
    "SelectedSyntax",
    "SourceEncodingError",
    "SourceMapping",
    "SyntaxDiagnostic",
    "grammar_identity",
    "malformed_counts",
    "parser_compatible_source",
    "select_syntax",
    "successfully_parsed_byte_coverage",
    "validate_parser_initialization",
]
