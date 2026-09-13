"""D3-A gates for structural-v1 canonical bytes and fingerprints only."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from modules.duplication import (
    CandidateAdmissionStatus,
    CandidateExtractionStatus,
    STRUCTURAL_CANONICAL_MAGIC,
    STRUCTURAL_FINGERPRINT_VERSION,
    StructuralCanonicalizationStatus,
    StructuralUnavailableReason,
    canonical_structural_bytes,
    canonicalize_structural,
    extract_candidates,
    structural_fingerprint,
)
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, select_syntax
from validation.duplication_d3a_20260818.structural_oracle import (
    oracle_structural_bytes,
    oracle_structural_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
D3A = ROOT / "validation" / "duplication_d3a_20260818"
CORPUS = D3A / "corpus"
EXPECTATIONS = json.loads((D3A / "expectations.json").read_text(encoding="utf-8"))
LANGUAGE_PATH = {
    "Go": ("go", "go"),
    "Java": ("java", "java"),
    "JavaScript": ("javascript", "js"),
    "Python": ("python", "py"),
    "TypeScript": ("typescript", "ts"),
}


@dataclass
class _CorpusState:
    syntaxes: dict[str, object]
    extractions: dict[str, object]
    canonical: dict[str, tuple[bytes, ...]]


@pytest.fixture(scope="module")
def corpus_state() -> _CorpusState:
    inventory = RepositoryInventory(CORPUS)
    registry = ParserRegistry()
    syntaxes = {}
    extractions = {}
    canonical = {}
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
        canonical[record.relative_path] = tuple(
            canonical_structural_bytes(syntax, candidate)
            for candidate in extraction.candidates
        )
    return _CorpusState(syntaxes, extractions, canonical)


def _path(language: str, stem: str) -> str:
    folder, extension = LANGUAGE_PATH[language]
    return f"{folder}/{stem}.{extension}"


def _one(state: _CorpusState, relative: str) -> bytes:
    values = state.canonical[relative]
    assert len(values) == 1, relative
    return values[0]


def _frames(canonical: bytes) -> list[bytes]:
    assert canonical.startswith(STRUCTURAL_CANONICAL_MAGIC)
    offset = len(STRUCTURAL_CANONICAL_MAGIC)
    values = []
    while offset < len(canonical):
        assert offset + 8 <= len(canonical)
        length = int.from_bytes(canonical[offset : offset + 8], "big")
        offset += 8
        assert offset + length <= len(canonical)
        values.append(canonical[offset : offset + length])
        offset += length
    return values


def _identifier_events(canonical: bytes) -> list[tuple[str, int]]:
    roles = {b"value", b"type", b"member", b"label"}
    frames = _frames(canonical)
    return [
        (frames[index + 1].decode("ascii"), int.from_bytes(frames[index + 2], "big"))
        for index in range(len(frames) - 2)
        if frames[index] == b"identifier" and frames[index + 1] in roles
    ]


def _node_kinds(canonical: bytes) -> set[str]:
    frames = _frames(canonical)
    return {
        frames[index + 1].decode("utf-8")
        for index in range(len(frames) - 1)
        if frames[index] == b"node:start"
    }


def test_baseline_contract_version_and_canonicalizer_boundary():
    assert STRUCTURAL_FINGERPRINT_VERSION == "structural-v1"
    assert STRUCTURAL_CANONICAL_MAGIC == b"ARCHLENS-DUPLICATION-STRUCTURAL\x00"
    assert (ROOT / "docs" / "DUPLICATION_STRUCTURAL_CONTRACT_V1.md").is_file()
    assert not (ROOT / "modules" / "cli" / "duplicates_command.py").exists()

    tree = ast.parse((ROOT / "modules" / "duplication" / "structural.py").read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "modules.duplication.grouping" not in imports


def test_canonical_bytes_and_fingerprints_are_deterministic(corpus_state):
    syntax = corpus_state.syntaxes["python/base.py"]
    candidate = corpus_state.extractions["python/base.py"].candidates[0]
    expected = _one(corpus_state, "python/base.py")
    assert canonical_structural_bytes(syntax, candidate) == expected
    assert canonical_structural_bytes(syntax, candidate) == expected
    assert len(expected) == 9678
    fingerprint = structural_fingerprint("Python", expected)
    assert fingerprint == structural_fingerprint("Python", expected)
    assert fingerprint == (
        "sha256:e877e182f6ab1b5c9be39065a4a794cdce0054ef8d03b1b06b5402c0c3a70d35"
    )


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
@pytest.mark.parametrize("variant", EXPECTATIONS["match_against_base"])
def test_five_language_structural_matches(corpus_state, language, variant):
    assert _one(corpus_state, _path(language, "base")) == _one(
        corpus_state, _path(language, variant)
    )


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
@pytest.mark.parametrize("variant", EXPECTATIONS["non_match_against_base"])
def test_five_language_structural_non_matches(corpus_state, language, variant):
    assert _one(corpus_state, _path(language, "base")) != _one(
        corpus_state, _path(language, variant)
    )


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
def test_mutation_guard_sorting_children_or_statements(corpus_state, language):
    assert _one(corpus_state, _path(language, "order_base")) != _one(
        corpus_state, _path(language, "order_changed")
    )


def test_identifier_equality_literal_class_child_order_and_control_non_relations(corpus_state):
    for relation in EXPECTATIONS["python_non_relations"]:
        assert _one(corpus_state, relation["left"]) != _one(corpus_state, relation["right"])


def test_mutation_guard_identifier_role_namespaces_are_separate(corpus_state):
    values = []
    for canonical in corpus_state.canonical["features/python.py"]:
        values.extend(_identifier_events(canonical))
    roles = {role for role, _ordinal in values}
    assert {"value", "member"} <= roles
    # A mutant with one shared namespace cannot restart the member ordinal at 0
    # after the many earlier value identifiers in this candidate.
    assert min(ordinal for role, ordinal in values if role == "value") == 0
    assert min(ordinal for role, ordinal in values if role == "member") == 0

    java_values = _identifier_events(corpus_state.canonical["features/Features.java"][0])
    assert {role for role, _ordinal in java_values} == {"value", "type", "member", "label"}
    assert all(
        min(ordinal for role, ordinal in java_values if role == expected) == 0
        for expected in {"value", "type", "member", "label"}
    )


def test_mutation_guard_literal_classes_are_not_erased(corpus_state):
    integer = _one(corpus_state, "relations/literal_integer.py")
    string = _one(corpus_state, "relations/literal_string.py")
    integer_frames = _frames(integer)
    string_frames = _frames(string)
    assert b"integer" in integer_frames and b"string" not in integer_frames
    assert b"string" in string_frames and b"integer" not in string_frames
    assert b"10" not in integer_frames and b"20" not in integer_frames
    assert integer != string


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
def test_mutation_guard_operators_are_retained(corpus_state, language):
    baseline = _one(corpus_state, _path(language, "base"))
    changed = _one(corpus_state, _path(language, "operator"))
    assert baseline != changed
    if language == "Python":
        assert {"Add", "Sub"} & (_node_kinds(baseline) | _node_kinds(changed))
    else:
        baseline_frames = _frames(baseline)
        changed_frames = _frames(changed)
        assert b"+" in baseline_frames or b"+" in changed_frames
        assert b"-" in baseline_frames or b"-" in changed_frames


def test_language_specific_feature_nodes_are_retained(corpus_state):
    python_nodes = set().union(
        *(_node_kinds(value) for value in corpus_state.canonical["features/python.py"])
    )
    assert {"Await", "Yield", "JoinedStr", "FormattedValue", "Match", "match_case"} <= python_nodes

    java_nodes = _node_kinds(corpus_state.canonical["features/Features.java"][0])
    assert {"switch_expression", "switch_label", "type_identifier", "field_access"} <= java_nodes

    go_nodes = _node_kinds(corpus_state.canonical["features/features.go"][0])
    assert {"select_statement", "expression_switch_statement", "type_switch_statement"} <= go_nodes

    js_nodes = _node_kinds(corpus_state.canonical["features/features.js"][0])
    js_async_nodes = _node_kinds(_one(corpus_state, "javascript/async_generator.js"))
    assert {"optional_chain", "template_string", "template_substitution", "switch_case"} <= js_nodes
    assert {"await_expression", "yield_expression", "template_string", "optional_chain"} <= js_async_nodes

    ts_nodes = _node_kinds(corpus_state.canonical["features/features.ts"][0])
    ts_async_nodes = _node_kinds(_one(corpus_state, "typescript/async_generator.ts"))
    assert {"type_annotation", "type_identifier", "optional_chain", "template_string"} <= ts_nodes
    assert {"await_expression", "yield_expression", "optional_chain"} <= ts_async_nodes


def test_unicode_identifiers_paths_and_comments_are_deterministic(corpus_state):
    left = _one(corpus_state, "python/café/日本語_unicode_a.py")
    right = _one(corpus_state, "python/café/日本語_unicode_b.py")
    assert left == right
    # Each form is abstracted only after its candidate-local equality pattern is
    # assigned, so two consistent Unicode renames have the same structure.
    assert _one(corpus_state, "python/unicode_nfc.py") == _one(
        corpus_state, "python/unicode_nfd.py"
    )
    assert structural_fingerprint("Python", left) == structural_fingerprint("Python", right)


def test_malformed_and_unavailable_syntax_are_typed(corpus_state):
    for relative in EXPECTATIONS["typed_unavailable"]:
        extraction = corpus_state.extractions[relative]
        assert extraction.status is CandidateExtractionStatus.UNAVAILABLE
        assert extraction.boundaries == ()

    malformed = corpus_state.syntaxes["python/malformed.py"]
    base_candidate = corpus_state.extractions["python/base.py"].candidates[0]
    result = canonicalize_structural(malformed, base_candidate)
    assert result.status is StructuralCanonicalizationStatus.UNAVAILABLE
    assert result.unavailable_reason is StructuralUnavailableReason.SYNTAX_UNAVAILABLE
    assert result.canonical_bytes is None and result.fingerprint is None


def test_other_invalid_inputs_have_stable_unavailable_reasons(corpus_state):
    syntax = corpus_state.syntaxes["python/base.py"]
    candidate = corpus_state.extractions["python/base.py"].candidates[0]
    unadmitted = replace(
        candidate,
        extraction_status=CandidateAdmissionStatus.BELOW_THRESHOLDS,
        failed_floors=("tokens",),
    )
    assert canonicalize_structural(syntax, unadmitted).unavailable_reason is (
        StructuralUnavailableReason.CANDIDATE_NOT_ADMITTED
    )
    wrong_language = replace(candidate, language="Java")
    assert canonicalize_structural(syntax, wrong_language).unavailable_reason is (
        StructuralUnavailableReason.LANGUAGE_MISMATCH
    )
    bad_span = replace(candidate, span=replace(candidate.span, end_byte=10**9))
    assert canonicalize_structural(syntax, bad_span).unavailable_reason is (
        StructuralUnavailableReason.SPAN_OUTSIDE_SOURCE
    )


def test_digest_collision_never_establishes_structural_equality(corpus_state):
    left = _one(corpus_state, "python/base.py")
    right = _one(corpus_state, "python/operator.py")
    collision = lambda _payload: b"\x5a" * 32
    assert left != right
    assert structural_fingerprint("Python", left, digest=collision) == structural_fingerprint(
        "Python", right, digest=collision
    )
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        structural_fingerprint("Python", left, digest=lambda _payload: b"short")


def test_mutation_guard_altered_version_and_language_are_rejected(corpus_state):
    canonical = _one(corpus_state, "python/base.py")
    altered = canonical.replace(b"structural-v1", b"structural-v0", 1)
    with pytest.raises(ValueError, match="canonical version"):
        structural_fingerprint("Python", altered)
    with pytest.raises(ValueError, match="canonical language"):
        structural_fingerprint("Java", canonical)


def test_clean_room_oracle_is_independent_and_byte_exact(corpus_state):
    oracle_tree = ast.parse((D3A / "structural_oracle.py").read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(oracle_tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("modules.duplication") for name in imported)

    compared = 0
    for relative, extraction in corpus_state.extractions.items():
        syntax = corpus_state.syntaxes[relative]
        for candidate, canonical in zip(
            extraction.candidates, corpus_state.canonical[relative], strict=True
        ):
            assert oracle_structural_bytes(syntax, candidate) == canonical
            assert oracle_structural_fingerprint(syntax.language, canonical) == (
                structural_fingerprint(syntax.language, canonical)
            )
            compared += 1
    assert compared >= 80


def test_fingerprint_payload_matches_independent_digest_seam(corpus_state):
    canonical = _one(corpus_state, "python/base.py")
    independent = hashlib.sha256(b"d3a-independent-digest").digest()
    assert structural_fingerprint(
        "Python", canonical, digest=lambda _payload: independent
    ) == "sha256:" + independent.hex()
