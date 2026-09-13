"""D1 gates for selected syntax and candidate-only extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from modules.core_metrics import compute_repository_metrics
from modules.duplication import (
    Candidate,
    CandidateAdmissionStatus,
    CandidateExtractionStatus,
    CandidateInvariantError,
    SourceSpan,
    UnitKind,
    extract_candidates,
    extract_file_candidates,
    validate_candidate_invariants,
)
from modules.duplication.model import admission
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, ParserUnavailableError, select_syntax


ROOT = Path(__file__).resolve().parents[1]
D0 = ROOT / "validation" / "duplication_d0_20260818"
CORPUS = D0 / "corpus"
LEGACY_D0_METRICS_CANONICAL_SHA256 = (
    "1bde67b92297f9b6c521cde1b204aeb9e24b01b2013e2ab1ddda240d3a1c8a1e"
)


def _selected(relative: str, registry: ParserRegistry | None = None):
    inventory = RepositoryInventory(CORPUS)
    record = next(record for record in inventory if record.relative_path == relative)
    source = inventory.read_bytes(record)
    syntax = select_syntax(
        record,
        source,
        registry or ParserRegistry(),
        python_text=(
            inventory.read_text(record) if record.detected_language == "Python" else None
        ),
    )
    return inventory, record, syntax


def test_frontend_is_consumer_neutral_leaf():
    tree = ast.parse((ROOT / "modules" / "source_frontend.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    forbidden = {
        "modules.core_metrics",
        "modules.duplication",
        "modules.policy",
        "modules.sarif",
        "modules.run_artifacts",
    }
    assert imports.isdisjoint(forbidden)


def test_duplication_layer_has_no_frozen_campaign_or_output_dependencies():
    imports = set()
    for path in (ROOT / "modules" / "duplication").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
    assert not any(
        name.startswith(
            (
                "modules.policy",
                "modules.sarif",
                "modules.run_artifacts",
                "modules.hotspots",
            )
        )
        for name in imports
    )
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "modules.duplication" in metadata["tool"]["setuptools"]["packages"]


def test_core_metrics_full_document_is_byte_equivalent_to_d0_legacy_selector():
    """Exact output captured from HEAD before the frontend extraction."""
    metrics = compute_repository_metrics(RepositoryInventory(CORPUS))
    canonical = json.dumps(
        metrics,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == LEGACY_D0_METRICS_CANONICAL_SHA256
    assert len(metrics["callable_records"]) == 98
    assert metrics["aggregate"]["source_files"] == 34
    assert metrics["aggregate"]["classes_structs"] == 17
    assert metrics["aggregate"]["methods_functions"] == 98


def test_d0_diagnostics_parse_statuses_and_recoveries_are_unchanged():
    expected = json.loads(
        (D0 / "evidence" / "inventory_portability_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    inventory = RepositoryInventory(CORPUS)
    metrics = compute_repository_metrics(inventory)
    assert metrics["aggregate"]["metric_status"] == expected["metric_status"]
    assert metrics["parse_errors"] == expected["parse_errors"]
    assert metrics["parser_diagnostics"] == expected["parser_diagnostics"]
    assert (
        metrics["recovered_parser_diagnostics"]
        == expected["recovered_parser_diagnostics"]
    )
    current_records = [record.to_dict() for record in inventory]
    # D0 captured the corpus while it was still untracked; after D0's commit the
    # only inventory difference is the now-known Git mode, not analysis state.
    for record in current_records:
        record["git_mode"] = None
    assert current_records == expected["records"]


@pytest.mark.parametrize(
    ("relative", "expected_status", "strategy"),
    [
        (
            "portability/recovered_import_assertion.js",
            "recovered",
            "javascript_import_assertion_compat",
        ),
        (
            "portability/recovered_keyword_parameter.ts",
            "recovered",
            "typescript_keyword_parameter_compat",
        ),
    ],
)
def test_js_ts_recovery_and_mapping_are_preserved(relative, expected_status, strategy):
    _inventory, _record, syntax = _selected(relative)
    assert syntax.parse_status == expected_status
    assert strategy in syntax.selected_strategies
    assert len(syntax.selected_source) == len(syntax.parser_source)
    assert syntax.mapping.newline_positions_preserved
    assert syntax.eligible_for_duplication


@pytest.mark.parametrize(
    "relative",
    [
        "portability/café/日本語.py",
        "portability/bom.ts",
        "portability/crlf.java",
        "portability/lone_cr.go",
        "portability/no_final_newline.js",
    ],
)
def test_portable_sources_have_valid_deterministic_mappings(relative):
    _inventory, record, syntax = _selected(relative)
    result = extract_candidates(syntax)
    assert result.status is CandidateExtractionStatus.COMPLETE
    assert syntax.mapping.selected_line_starts[0] == 0
    assert syntax.mapping.selected_byte_length == len(syntax.selected_source)
    for candidate in result.boundaries:
        assert 0 <= candidate.span.start_byte < candidate.span.end_byte
        assert candidate.span.end_byte <= len(syntax.selected_source)
    if record.extension == ".ts" and syntax.evidence_source.startswith(b"\xef\xbb\xbf"):
        assert syntax.mapping.parser_byte_offset_adjustment == 3
        assert all(
            candidate.span.original_start_byte == candidate.span.start_byte + 3
            for candidate in result.boundaries
        )


def test_python_unicode_token_columns_map_to_exact_utf8_bytes():
    _inventory, _record, syntax = _selected("portability/café/日本語.py")
    candidate = extract_candidates(syntax).boundaries[0]
    occurrence = syntax.selected_source[
        candidate.span.start_byte : candidate.span.end_byte
    ]
    assert occurrence.startswith("第一".encode("utf-8"))
    assert occurrence.endswith("第三".encode("utf-8"))


@pytest.mark.parametrize(
    "relative",
    [
        "portability/malformed.py",
        "portability/malformed.java",
        "portability/malformed.go",
        "portability/malformed.js",
        "portability/malformed.ts",
    ],
)
def test_partial_or_failed_parse_is_typed_unavailable_not_zero(relative):
    _inventory, _record, syntax = _selected(relative)
    result = extract_candidates(syntax)
    assert syntax.parse_status in {"partial", "failed"}
    assert result.status is CandidateExtractionStatus.UNAVAILABLE
    assert result.boundaries == ()
    assert result.reason in {"syntax_failed", "syntax_partial"}


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        (
            "grammar/python.py",
            Counter(
                callable_body=3,
                branch_body=2,
                loop_body=5,
                exception_body=6,
                switch_arm_body=3,
                scoped_body=2,
            ),
        ),
        (
            "grammar/GrammarProbe.java",
            Counter(
                callable_body=6,
                branch_body=2,
                loop_body=4,
                exception_body=5,
                switch_arm_body=4,
                scoped_body=4,
            ),
        ),
        (
            "grammar/grammar.go",
            Counter(
                callable_body=3,
                branch_body=3,
                loop_body=2,
                switch_arm_body=6,
                scoped_body=1,
            ),
        ),
        (
            "grammar/javascript.js",
            Counter(
                callable_body=7,
                branch_body=2,
                loop_body=6,
                exception_body=3,
                switch_arm_body=2,
                scoped_body=2,
            ),
        ),
        (
            "grammar/typescript.ts",
            Counter(
                callable_body=5,
                branch_body=2,
                loop_body=5,
                exception_body=3,
                switch_arm_body=2,
                scoped_body=2,
            ),
        ),
    ],
)
def test_frozen_five_language_body_rule_coverage(relative, expected):
    _inventory, _record, syntax = _selected(relative)
    result = extract_candidates(syntax)
    assert result.status is CandidateExtractionStatus.COMPLETE
    assert Counter(candidate.unit_kind.value for candidate in result.boundaries) == expected


def test_module_class_and_concise_expression_units_are_excluded(tmp_path):
    source = (
        "const moduleValue = 1;\n"
        "class C { field = 1; }\n"
        "const concise = (x) => x + 1;\n"
        "function empty() {}\n"
    )
    (tmp_path / "excluded.js").write_text(source, encoding="utf-8")
    inventory = RepositoryInventory(tmp_path)
    record = next(iter(inventory))
    syntax = select_syntax(record, inventory.read_bytes(record), ParserRegistry())
    result = extract_candidates(syntax)
    assert result.status is CandidateExtractionStatus.COMPLETE
    assert result.boundaries == ()


def test_threshold_measurements_equal_frozen_d0_evidence_for_all_languages():
    expected = json.loads(
        (D0 / "evidence" / "threshold_evidence.json").read_text(encoding="utf-8")
    )
    expected_by_file: dict[str, list[tuple[int, int, int]]] = {}
    for row in expected:
        expected_by_file.setdefault(row["file"], []).append(
            (
                row["immediate_statement_count"],
                row["significant_lexical_token_count"],
                row["duplicated_nloc"],
            )
        )
    for relative, expected_measurements in expected_by_file.items():
        _inventory, _record, syntax = _selected(relative)
        result = extract_candidates(syntax)
        actual = sorted(
            (
                candidate.immediate_statement_count,
                candidate.significant_lexical_token_count,
                candidate.duplicated_nloc,
            )
            for candidate in result.boundaries
        )
        assert actual == sorted(expected_measurements)
        assert len(result.candidates) == 1


@pytest.mark.parametrize(
    ("statements", "tokens", "nloc", "expected"),
    [
        (3, 40, 8, CandidateAdmissionStatus.BELOW_THRESHOLDS),
        (4, 40, 8, CandidateAdmissionStatus.ADMITTED),
        (4, 39, 8, CandidateAdmissionStatus.BELOW_THRESHOLDS),
        (4, 40, 8, CandidateAdmissionStatus.ADMITTED),
        (4, 40, 7, CandidateAdmissionStatus.BELOW_THRESHOLDS),
        (4, 40, 8, CandidateAdmissionStatus.ADMITTED),
    ],
)
def test_inclusive_frozen_threshold_edges(statements, tokens, nloc, expected):
    status, _failed = admission(statements, tokens, nloc)
    assert status is expected


def test_nested_candidates_are_deterministic_unique_and_laminar():
    _inventory, _record, syntax = _selected("structural/python.py")
    first = extract_candidates(syntax)
    second = extract_candidates(syntax)
    assert first == second
    spans = [(candidate.span.start_byte, candidate.span.end_byte) for candidate in first.boundaries]
    assert len(spans) == len(set(spans))
    assert any(
        left_start < right_start < right_end < left_end
        for left_start, left_end in spans
        for right_start, right_end in spans
    )
    validate_candidate_invariants(first.boundaries, len(syntax.selected_source))


def _candidate(start: int, end: int) -> Candidate:
    return Candidate(
        language="Python",
        unit_kind=UnitKind.CALLABLE_BODY,
        span=SourceSpan(start, end, 1, start, 1, end, start, end),
        immediate_statement_count=4,
        significant_lexical_token_count=40,
        duplicated_nloc=8,
        extraction_status=CandidateAdmissionStatus.ADMITTED,
    )


def test_mutation_guard_rejects_changed_or_duplicate_boundaries():
    with pytest.raises(CandidateInvariantError, match="outside selected source"):
        validate_candidate_invariants([_candidate(2, 11)], 10)
    with pytest.raises(CandidateInvariantError, match="duplicate candidate span"):
        validate_candidate_invariants([_candidate(1, 9), _candidate(1, 9)], 10)


def test_mutation_guard_rejects_partial_overlap_loudly():
    with pytest.raises(CandidateInvariantError, match="partially overlapping"):
        validate_candidate_invariants([_candidate(1, 7), _candidate(5, 9)], 10)


def test_mutation_guard_does_not_ignore_parser_initialization_failure(tmp_path):
    (tmp_path / "Broken.java").write_text("class Broken {}\n", encoding="utf-8")
    inventory = RepositoryInventory(tmp_path)
    record = next(iter(inventory))

    class FailingRegistry(ParserRegistry):
        def get(self, language, extension=None):
            raise ParserUnavailableError("forced parser failure")

    result = extract_file_candidates(
        record,
        inventory.read_bytes(record),
        FailingRegistry(),
    )
    assert result.status is CandidateExtractionStatus.UNAVAILABLE
    assert result.boundaries == ()
    assert "forced parser failure" in (result.reason or "")
