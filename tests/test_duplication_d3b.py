"""D3-B gates for structural grouping and maximality suppression only."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from modules.duplication import (
    CandidateExtractionStatus,
    CloneDistribution,
    SourceSpan,
    StructuralGroupingInvariantError,
    UnitKind,
    extract_candidates,
    group_structural_clones,
    make_structural_occurrence,
)
from modules.duplication.grouping import occurrence_identity
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, select_syntax
from validation.duplication_d3b_20260819.grouping_oracle import (
    oracle_group_structural_clones,
    oracle_occurrence_id,
)


ROOT = Path(__file__).resolve().parents[1]
D3B = ROOT / "validation" / "duplication_d3b_20260819"
CORPUS = D3B / "corpus"
EXPECTATIONS = json.loads((D3B / "expectations.json").read_text(encoding="utf-8"))
SYNTHETIC = json.loads((D3B / "synthetic_overlap.json").read_text(encoding="utf-8"))
LANGUAGE_PATH = {
    "Go": ("go", "go"),
    "Java": ("java", "java"),
    "JavaScript": ("javascript", "js"),
    "Python": ("python", "py"),
    "TypeScript": ("typescript", "ts"),
}


@dataclass
class _CorpusState:
    syntaxes: dict[str, Any]
    extractions: dict[str, Any]
    occurrences: dict[str, tuple[Any, ...]]


@pytest.fixture(scope="module")
def corpus_state() -> _CorpusState:
    inventory = RepositoryInventory(CORPUS)
    registry = ParserRegistry()
    syntaxes: dict[str, Any] = {}
    extractions: dict[str, Any] = {}
    occurrences: dict[str, tuple[Any, ...]] = {}
    for record in inventory:
        if not record.included_in_metrics:
            continue
        source = inventory.read_bytes(record)
        syntax = select_syntax(
            record,
            source,
            registry,
            python_text=(
                inventory.read_text(record)
                if record.detected_language == "Python"
                else None
            ),
        )
        extraction = extract_candidates(syntax)
        syntaxes[record.relative_path] = syntax
        extractions[record.relative_path] = extraction
        occurrences[record.relative_path] = tuple(
            make_structural_occurrence(record.relative_path, syntax, candidate)
            for candidate in extraction.candidates
        )
    return _CorpusState(syntaxes, extractions, occurrences)


def _path(language: str, stem: str) -> str:
    folder, extension = LANGUAGE_PATH[language]
    return f"{folder}/{stem}.{extension}"


def _select(state: _CorpusState, paths: list[str] | tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(
        occurrence
        for path in paths
        for occurrence in state.occurrences[path]
    )


def _production_snapshot(result: Any) -> tuple[Any, ...]:
    return (
        tuple(
            (
                group.group_id,
                group.language,
                group.fingerprint,
                group.fingerprint_version,
                group.distribution.value,
                tuple((item.coordinate, item.occurrence_id) for item in group.occurrences),
                tuple(
                    (span.relative_path, span.start_line, span.end_line)
                    for span in group.source_span_union
                ),
            )
            for group in result.groups
        ),
        tuple(
            (
                relation.child_group_id,
                relation.parent_group_id,
                relation.occurrence_pairs,
                relation.mapping_ambiguous,
            )
            for relation in result.dominance
        ),
        result.initial_group_count,
    )


def _oracle_snapshot(result: Any) -> tuple[Any, ...]:
    return (
        tuple(
            (
                group.group_id,
                group.language,
                group.fingerprint,
                group.fingerprint_version,
                group.distribution,
                tuple(
                    (
                        (
                            item.relative_path,
                            item.candidate.span.start_line,
                            item.candidate.span.end_line,
                            item.candidate.unit_kind.value,
                        ),
                        item.occurrence_id,
                    )
                    for item in group.occurrences
                ),
                group.source_span_union,
            )
            for group in result.groups
        ),
        tuple(
            (
                relation.child_group_id,
                relation.parent_group_id,
                relation.occurrence_pairs,
                relation.mapping_ambiguous,
            )
            for relation in result.dominance
        ),
        result.initial_group_count,
    )


def _with_coordinate(
    occurrence: Any,
    path: str,
    span: SourceSpan,
    *,
    kind: UnitKind | None = None,
    canonical_bytes: bytes | None = None,
    fingerprint: str | None = None,
) -> Any:
    candidate = replace(
        occurrence.candidate,
        unit_kind=occurrence.candidate.unit_kind if kind is None else kind,
        span=span,
    )
    coordinate = path, span.start_line, span.end_line, candidate.unit_kind.value
    return replace(
        occurrence,
        relative_path=path,
        candidate=candidate,
        canonical_bytes=(
            occurrence.canonical_bytes if canonical_bytes is None else canonical_bytes
        ),
        fingerprint=occurrence.fingerprint if fingerprint is None else fingerprint,
        occurrence_id=occurrence_identity(coordinate),
    )


def test_contract_program_boundary_and_internal_exports():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "4.0.1"' in pyproject
    assert (ROOT / "docs" / "DUPLICATION_STRUCTURAL_GROUPING_CONTRACT_V1.md").is_file()
    assert (ROOT / "modules" / "duplication" / "structural_grouping.py").is_file()
    assert not (ROOT / "modules" / "cli" / "duplicates_command.py").exists()

    tree = ast.parse(
        (ROOT / "modules" / "duplication" / "structural_grouping.py").read_text(
            encoding="utf-8"
        )
    )
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = ("modules.cli", "modules.policy", "validation.artifact_io")
    assert not any(module.startswith(forbidden) for module in imported)


def test_independent_oracle_has_no_production_duplication_import():
    tree = ast.parse((D3B / "grouping_oracle.py").read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module.startswith("modules.duplication") for module in imported)


def test_materialized_corpus_hashes_are_complete_and_stable():
    expected = json.loads((D3B / "corpus_hashes.json").read_text(encoding="utf-8"))
    actual = {
        path.relative_to(D3B).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(CORPUS.rglob("*"))
        if path.is_file()
    }
    actual["synthetic_overlap.json"] = hashlib.sha256(
        (D3B / "synthetic_overlap.json").read_bytes()
    ).hexdigest()
    assert actual == expected


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
def test_five_language_exact_renamed_and_literal_class_group(corpus_state, language):
    paths = [_path(language, variant) for variant in EXPECTATIONS["core_variants"]]
    result = group_structural_clones(_select(corpus_state, paths))
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.language == language
    assert group.occurrence_count == 4
    assert group.file_count == 4
    assert group.distribution is CloneDistribution.CROSS_FILE
    assert [item.relative_path for item in group.occurrences] == sorted(paths)


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
def test_operator_change_does_not_join_structural_group(corpus_state, language):
    paths = [_path(language, name) for name in ("base", "exact", "operator")]
    result = group_structural_clones(_select(corpus_state, paths))
    assert len(result.groups) == 1
    assert {item.relative_path for item in result.groups[0].occurrences} == set(paths[:2])


@pytest.mark.parametrize(
    ("distribution", "expected_count"),
    (("same_file", 2), ("cross_file", 2), ("mixed", 3)),
)
def test_same_cross_and_mixed_distribution(corpus_state, distribution, expected_count):
    paths = EXPECTATIONS["distributions"][distribution]
    result = group_structural_clones(_select(corpus_state, paths))
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.distribution.value == distribution
    assert group.occurrence_count == expected_count


def test_source_span_union_is_path_local_and_safe(corpus_state):
    paths = EXPECTATIONS["distributions"]["mixed"]
    group = group_structural_clones(_select(corpus_state, paths)).groups[0]
    assert tuple(
        (span.relative_path, span.start_line, span.end_line)
        for span in group.source_span_union
    ) == (
        ("python/base.py", 2, 10),
        ("python/same_file.py", 2, 10),
        ("python/same_file.py", 13, 21),
    )
    assert group.source_span_line_count == 27
    assert not hasattr(group, "percentage")
    assert not hasattr(group, "density")
    assert not hasattr(group, "score")


def test_mutation_guard_fingerprint_alone_never_establishes_equality(corpus_state):
    collision = lambda _payload: b"\xa5" * 32
    values = []
    for source_path, occurrence_path in (
        ("python/base.py", "collision/base.py"),
        ("python/exact.py", "collision/exact.py"),
        ("python/operator.py", "collision/operator.py"),
        ("python/operator.py", "collision/operator-copy.py"),
    ):
        syntax = corpus_state.syntaxes[source_path]
        candidate = corpus_state.extractions[source_path].candidates[0]
        values.append(
            make_structural_occurrence(
                occurrence_path,
                syntax,
                candidate,
                digest=collision,
            )
        )
    assert len({value.fingerprint for value in values}) == 1
    result = group_structural_clones(values)
    assert len(result.groups) == 2
    assert {
        frozenset(item.relative_path for item in group.occurrences)
        for group in result.groups
    } == {
        frozenset({"collision/base.py", "collision/exact.py"}),
        frozenset({"collision/operator.py", "collision/operator-copy.py"}),
    }


@pytest.mark.parametrize("scenario", ("outer_if", "outer_loop"))
def test_mutation_guard_whole_clone_suppresses_bijective_inner_clone(corpus_state, scenario):
    result = group_structural_clones(
        _select(corpus_state, EXPECTATIONS["nested"][scenario])
    )
    assert result.initial_group_count == 2
    assert len(result.groups) == 1
    assert len(result.dominance) == 1
    assert result.dominance[0].child_group_id in result.suppressed_group_ids
    assert result.dominance[0].parent_group_id == result.groups[0].group_id
    assert len(result.dominance[0].occurrence_pairs) == 2
    assert not result.dominance[0].mapping_ambiguous
    assert {item.candidate.unit_kind for item in result.groups[0].occurrences} == {
        UnitKind.CALLABLE_BODY
    }


def test_third_independent_inner_occurrence_prevents_suppression(corpus_state):
    result = group_structural_clones(
        _select(corpus_state, EXPECTATIONS["nested"]["outer_if_with_third_inner"])
    )
    assert result.initial_group_count == 2
    assert len(result.groups) == 2
    assert not result.dominance
    assert sorted(group.occurrence_count for group in result.groups) == [2, 3]


def test_inner_only_clone_remains(corpus_state):
    result = group_structural_clones(
        _select(corpus_state, EXPECTATIONS["nested"]["inner_only"])
    )
    assert result.initial_group_count == len(result.groups) == 1
    assert not result.dominance
    assert {item.candidate.unit_kind for item in result.groups[0].occurrences} == {
        UnitKind.BRANCH_BODY
    }


def test_two_disjoint_sibling_groups_remain(corpus_state):
    result = group_structural_clones(
        _select(corpus_state, EXPECTATIONS["nested"]["siblings"])
    )
    assert result.initial_group_count == len(result.groups) == 2
    assert not result.dominance
    assert all(group.occurrence_count == 2 for group in result.groups)


def test_identical_occurrence_and_identical_nested_labels_deduplicate(corpus_state):
    base = corpus_state.occurrences["python/base.py"][0]
    exact = corpus_state.occurrences["python/exact.py"][0]
    first_fixture = SYNTHETIC["identical_span"][0]
    path = first_fixture["path"]
    identical_span = replace(
        base.candidate.span,
        start_byte=first_fixture["start_byte"],
        end_byte=first_fixture["end_byte"],
    )
    callable_occurrence = _with_coordinate(base, path, identical_span)
    branch_occurrence = _with_coordinate(
        base,
        path,
        identical_span,
        kind=UnitKind.BRANCH_BODY,
    )
    other = _with_coordinate(exact, "synthetic/other.py", exact.candidate.span)
    result = group_structural_clones(
        (branch_occurrence, callable_occurrence, callable_occurrence, other)
    )
    assert len(result.groups) == 1
    assert result.groups[0].occurrence_count == 2
    winner = next(
        item for item in result.groups[0].occurrences if item.relative_path == path
    )
    assert winner.candidate.unit_kind is UnitKind.CALLABLE_BODY


def test_identical_span_with_conflicting_content_fails(corpus_state):
    base = corpus_state.occurrences["python/base.py"][0]
    operator = corpus_state.occurrences["python/operator.py"][0]
    path = "synthetic/conflict.py"
    first = _with_coordinate(base, path, base.candidate.span)
    conflict = _with_coordinate(
        base,
        path,
        base.candidate.span,
        kind=UnitKind.BRANCH_BODY,
        canonical_bytes=operator.canonical_bytes,
        fingerprint=operator.fingerprint,
    )
    with pytest.raises(StructuralGroupingInvariantError, match="exact source span"):
        group_structural_clones((first, conflict))


def test_mutation_guard_partial_overlap_fails_explicitly(corpus_state):
    base = corpus_state.occurrences["python/base.py"][0]
    fixtures = SYNTHETIC["partial_overlap"]
    first_span = replace(
        base.candidate.span,
        start_byte=fixtures[0]["start_byte"],
        end_byte=fixtures[0]["end_byte"],
        start_line=2,
        end_line=10,
    )
    second_span = replace(
        base.candidate.span,
        start_byte=fixtures[1]["start_byte"],
        end_byte=fixtures[1]["end_byte"],
        start_line=6,
        end_line=14,
    )
    values = (
        _with_coordinate(base, fixtures[0]["path"], first_span),
        _with_coordinate(base, fixtures[1]["path"], second_span),
    )
    with pytest.raises(StructuralGroupingInvariantError, match="partially overlapping"):
        group_structural_clones(values)
    with pytest.raises(ValueError, match="partial overlap"):
        oracle_group_structural_clones(values)


def test_ambiguous_containment_mapping_is_exposed(corpus_state):
    child_template = corpus_state.occurrences["python/base.py"][0]
    parent_template = corpus_state.occurrences["python/operator.py"][0]
    path = "synthetic/ambiguous.py"
    parents = (
        _with_coordinate(
            parent_template,
            path,
            replace(parent_template.candidate.span, start_byte=0, end_byte=200, start_line=1, end_line=40),
        ),
        _with_coordinate(
            parent_template,
            path,
            replace(parent_template.candidate.span, start_byte=0, end_byte=100, start_line=1, end_line=20),
        ),
    )
    children = (
        _with_coordinate(
            child_template,
            path,
            replace(child_template.candidate.span, start_byte=10, end_byte=20, start_line=3, end_line=5),
            kind=UnitKind.BRANCH_BODY,
        ),
        _with_coordinate(
            child_template,
            path,
            replace(child_template.candidate.span, start_byte=30, end_byte=40, start_line=7, end_line=9),
            kind=UnitKind.BRANCH_BODY,
        ),
    )
    result = group_structural_clones((*parents, *children))
    assert len(result.groups) == 1
    assert len(result.dominance) == 1
    assert result.dominance[0].mapping_ambiguous
    assert len(result.dominance[0].occurrence_pairs) == 2


@pytest.mark.parametrize(
    "invalid_path",
    ("", "/absolute.py", "C:/drive.py", "parent/../file.py", "dot/./file.py", "back\\slash.py"),
)
def test_path_normalization_fails_closed(corpus_state, invalid_path):
    occurrence = replace(
        corpus_state.occurrences["python/base.py"][0],
        relative_path=invalid_path,
        occurrence_id="do1:" + "0" * 64,
    )
    with pytest.raises(
        StructuralGroupingInvariantError,
        match="relative POSIX path|absolute|parent|canonical",
    ):
        group_structural_clones((occurrence,))


def test_unicode_path_identity_and_grouping_match_independent_oracle(corpus_state):
    paths = [
        "python/café/日本語_unicode_a.py",
        "python/café/日本語_unicode_b.py",
    ]
    values = _select(corpus_state, paths)
    result = group_structural_clones(values)
    assert len(result.groups) == 1
    assert all(item.occurrence_id == oracle_occurrence_id(item.coordinate) for item in values)
    assert _production_snapshot(result) == _oracle_snapshot(
        oracle_group_structural_clones(values)
    )


@pytest.mark.parametrize("path", EXPECTATIONS["malformed_unavailable"])
def test_malformed_or_unavailable_candidates_never_form_occurrences(corpus_state, path):
    assert corpus_state.extractions[path].status is CandidateExtractionStatus.UNAVAILABLE
    assert not corpus_state.occurrences[path]


def test_mutation_guard_ordering_repetition_and_input_permutations(corpus_state):
    paths = [
        _path(language, variant)
        for language in reversed(EXPECTATIONS["languages"])
        for variant in reversed(EXPECTATIONS["core_variants"])
    ] + EXPECTATIONS["nested"]["siblings"]
    values = _select(corpus_state, paths)
    forward = group_structural_clones(values)
    repeated = group_structural_clones(values)
    reversed_result = group_structural_clones(reversed(values))
    assert forward == repeated == reversed_result
    keys = [
        (
            group.language,
            group.fingerprint,
            tuple(item.coordinate for item in group.occurrences),
            group.group_id,
        )
        for group in forward.groups
    ]
    assert keys == sorted(keys)


def test_mutation_guard_group_ids_are_deterministic_not_random(corpus_state):
    values = _select(corpus_state, ["python/base.py", "python/exact.py"])
    result = group_structural_clones(values)
    assert result.groups[0].group_id == (
        "dg1:d3f3e3ba203b46d140d3f0c0204fbfa344506b52137dab18e8d9998efc90fb34"
    )
    assert re.fullmatch(r"dg1:[0-9a-f]{64}", result.groups[0].group_id)


@pytest.mark.parametrize(
    "paths",
    (
        ["python/base.py", "python/exact.py", "python/same_file.py"],
        ["nested/outer_if_a.py", "nested/outer_if_b.py"],
        ["nested/outer_if_a.py", "nested/outer_if_b.py", "nested/inner_third.py"],
        ["nested/inner_only_a.py", "nested/inner_only_b.py"],
        ["nested/outer_loop_a.py", "nested/outer_loop_b.py"],
        ["nested/siblings_a.py", "nested/siblings_b.py"],
    ),
)
def test_production_membership_ids_order_and_suppression_match_oracle(corpus_state, paths):
    values = _select(corpus_state, paths)
    assert _production_snapshot(group_structural_clones(values)) == _oracle_snapshot(
        oracle_group_structural_clones(values)
    )


def test_conflicting_coordinate_and_equal_bytes_fingerprint_fail(corpus_state):
    base = corpus_state.occurrences["python/base.py"][0]
    conflict = replace(base, fingerprint="sha256:" + "f" * 64)
    with pytest.raises(StructuralGroupingInvariantError, match="coordinate.*conflicting"):
        group_structural_clones((base, conflict))

    relocated = _with_coordinate(
        conflict,
        "python/relocated.py",
        conflict.candidate.span,
        fingerprint=conflict.fingerprint,
    )
    with pytest.raises(StructuralGroupingInvariantError, match="inconsistent fingerprints"):
        group_structural_clones((base, relocated))
