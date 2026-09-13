"""D2 gates for lexical-exact canonicalization and collision-safe grouping."""

from __future__ import annotations

import ast
import hashlib
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from modules.duplication import (
    CandidateAdmissionStatus,
    CandidateExtractionStatus,
    CloneDistribution,
    GroupingInvariantError,
    LEXICAL_CANONICAL_MAGIC,
    LEXICAL_FINGERPRINT_VERSION,
    LexicalCanonicalizationError,
    canonical_lexical_bytes,
    extract_candidates,
    extract_file_candidates,
    group_lexical_clones,
    lexical_fingerprint,
    make_lexical_occurrence,
)
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, select_syntax
from modules.source_frontend import ParserUnavailableError
from validation.duplication_d2_20260818.lexical_oracle import oracle_canonical_bytes


ROOT = Path(__file__).resolve().parents[1]
D2 = ROOT / "validation" / "duplication_d2_20260818"
CORPUS = D2 / "corpus"
EXPECTATIONS = json.loads((D2 / "expectations.json").read_text(encoding="utf-8"))
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
    results: dict[str, object]
    occurrences: tuple[object, ...]


@pytest.fixture(scope="module")
def corpus_state() -> _CorpusState:
    inventory = RepositoryInventory(CORPUS)
    registry = ParserRegistry()
    syntaxes = {}
    results = {}
    occurrences = []
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
        result = extract_candidates(syntax)
        syntaxes[record.relative_path] = syntax
        results[record.relative_path] = result
        occurrences.extend(
            make_lexical_occurrence(record.relative_path, syntax, candidate)
            for candidate in result.candidates
        )
    return _CorpusState(syntaxes, results, tuple(occurrences))


def _path(language: str, stem: str) -> str:
    folder, extension = LANGUAGE_PATH[language]
    return f"{folder}/{stem}.{extension}"


def _occurrences(state: _CorpusState, relative: str):
    return tuple(
        occurrence
        for occurrence in state.occurrences
        if occurrence.relative_path == relative
    )


def _canonical(state: _CorpusState, relative: str, ordinal: int = 0) -> bytes:
    return _occurrences(state, relative)[ordinal].canonical_bytes


def _expected_group_paths() -> list[list[str]]:
    return sorted(
        sorted(member.split("#", 1)[0] for member in group["members"])
        for group in EXPECTATIONS["equivalence_classes"]
    )


def test_frozen_expectations_and_d2_output_boundary():
    assert EXPECTATIONS["authored_before_production_matcher"] is True
    assert EXPECTATIONS["clone_kind"] == "lexical_exact"
    assert EXPECTATIONS["languages"] == [
        "Go",
        "Java",
        "JavaScript",
        "Python",
        "TypeScript",
    ]
    assert (ROOT / "docs" / "DUPLICATION_LEXICAL_CONTRACT_V1.md").is_file()
    # D2 originally stopped before structural.py existed.  D3-A now owns that
    # forward boundary; D2 continues to assert that no output/CLI was added.
    assert not (ROOT / "modules" / "cli" / "duplicates_command.py").exists()

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
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
    assert not any(
        name.startswith(
            (
                "modules.cli",
                "modules.hotspots",
                "modules.policy",
                "modules.run_artifacts",
                "modules.sarif",
            )
        )
        for name in imports
    )


def test_canonical_bytes_equal_the_clean_room_oracle(corpus_state):
    oracle_tree = ast.parse((D2 / "lexical_oracle.py").read_text(encoding="utf-8"))
    oracle_imports = {
        node.module or ""
        for node in ast.walk(oracle_tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("modules.duplication") for name in oracle_imports)
    for occurrence in corpus_state.occurrences:
        syntax = corpus_state.syntaxes[occurrence.relative_path]
        assert occurrence.canonical_bytes == oracle_canonical_bytes(
            syntax, occurrence.candidate
        )
        assert occurrence.canonical_bytes.startswith(LEXICAL_CANONICAL_MAGIC)
        assert b"lexical-exact-v1" in occurrence.canonical_bytes
        assert occurrence.relative_path.encode("utf-8") not in occurrence.canonical_bytes


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
def test_exact_whitespace_and_comment_relations_in_five_languages(
    corpus_state, language
):
    baseline = _canonical(corpus_state, _path(language, "base"))
    assert baseline == _canonical(corpus_state, _path(language, "exact"))
    assert baseline == _canonical(corpus_state, _path(language, "whitespace"))
    assert baseline == _canonical(corpus_state, _path(language, "comment"))


@pytest.mark.parametrize("language", EXPECTATIONS["languages"])
@pytest.mark.parametrize(
    "mutation", ["identifier", "literal", "operator", "reordered", "added", "removed"]
)
def test_frozen_non_clone_relations_in_five_languages(
    corpus_state, language, mutation
):
    assert _canonical(corpus_state, _path(language, "base")) != _canonical(
        corpus_state, _path(language, mutation)
    )


def test_line_endings_bom_and_unicode_contract(corpus_state):
    assert _canonical(corpus_state, "java/base.java") == _canonical(
        corpus_state, "java/crlf.java"
    )
    assert _canonical(corpus_state, "go/base.go") == _canonical(
        corpus_state, "go/lone_cr.go"
    )
    assert _canonical(corpus_state, "typescript/base.ts") == _canonical(
        corpus_state, "typescript/bom.ts"
    )
    assert _canonical(
        corpus_state, "python/café/日本語_unicode_a.py"
    ) == _canonical(corpus_state, "python/café/日本語_unicode_b.py")
    assert _canonical(corpus_state, "python/unicode_nfc.py") != _canonical(
        corpus_state, "python/unicode_nfd.py"
    )


def test_fingerprint_version_stability_and_same_bytes_rule(corpus_state):
    baseline = _occurrences(corpus_state, "python/base.py")[0]
    exact = _occurrences(corpus_state, "python/exact.py")[0]
    assert LEXICAL_FINGERPRINT_VERSION == "lexical-exact-v1"
    assert baseline.fingerprint == exact.fingerprint
    assert baseline.fingerprint == lexical_fingerprint("Python", baseline.canonical_bytes)
    assert baseline.fingerprint == (
        "sha256:0fd95ee4a53d36367c1dd9325f9a5cc4234c396317039a2d630ec1f80fab237d"
    )
    assert baseline.fingerprint.startswith("sha256:")
    assert len(baseline.fingerprint) == len("sha256:") + 64
    with pytest.raises(ValueError, match="canonical language"):
        lexical_fingerprint("Java", baseline.canonical_bytes)


def test_full_corpus_grouping_matches_frozen_relation_oracle(corpus_state):
    groups = group_lexical_clones(corpus_state.occurrences)
    actual = sorted(
        sorted(occurrence.relative_path for occurrence in group.occurrences)
        for group in groups
    )
    assert actual == _expected_group_paths()
    assert all(group.occurrence_count >= 2 for group in groups)
    assert [group.language for group in groups] == sorted(group.language for group in groups)


def test_injected_digest_collision_cannot_create_a_false_clone(corpus_state):
    collision = lambda _payload: b"\x5a" * 32
    collided = []
    for relative_path, result in corpus_state.results.items():
        syntax = corpus_state.syntaxes[relative_path]
        collided.extend(
            make_lexical_occurrence(
                relative_path, syntax, candidate, digest=collision
            )
            for candidate in result.candidates
        )
    assert len({occurrence.fingerprint for occurrence in collided}) == 1
    groups = group_lexical_clones(collided)
    actual = sorted(
        sorted(occurrence.relative_path for occurrence in group.occurrences)
        for group in groups
    )
    assert actual == _expected_group_paths()
    assert not any(
        {"python/base.py", "python/identifier.py"}
        <= {occurrence.relative_path for occurrence in group.occurrences}
        for group in groups
    )


def test_group_and_occurrence_order_are_deterministic(corpus_state):
    expected = group_lexical_clones(corpus_state.occurrences)
    shuffled = list(corpus_state.occurrences)
    random.Random(28173).shuffle(shuffled)
    assert group_lexical_clones(shuffled) == expected
    assert group_lexical_clones(reversed(shuffled)) == expected
    for group in expected:
        assert tuple(item.coordinate for item in group.occurrences) == tuple(
            sorted(item.coordinate for item in group.occurrences)
        )
        assert group.group_id.startswith("dg1:") and len(group.group_id) == 68
        assert all(
            occurrence.occurrence_id.startswith("do1:")
            and len(occurrence.occurrence_id) == 68
            for occurrence in group.occurrences
        )


def test_same_file_cross_file_and_mixed_distribution(corpus_state):
    same = group_lexical_clones(_occurrences(corpus_state, "python/same_file.py"))
    assert len(same) == 1 and same[0].distribution is CloneDistribution.SAME_FILE

    cross_inputs = (
        *_occurrences(corpus_state, "python/base.py"),
        *_occurrences(corpus_state, "python/exact.py"),
    )
    cross = group_lexical_clones(cross_inputs)
    assert len(cross) == 1 and cross[0].distribution is CloneDistribution.CROSS_FILE

    mixed = group_lexical_clones(
        (*cross_inputs, *_occurrences(corpus_state, "python/same_file.py"))
    )
    assert len(mixed) == 1 and mixed[0].distribution is CloneDistribution.MIXED
    assert mixed[0].occurrence_count == 4


def test_duplicate_coordinate_is_deduplicated_or_rejected(corpus_state):
    base = _occurrences(corpus_state, "python/base.py")[0]
    exact = _occurrences(corpus_state, "python/exact.py")[0]
    groups = group_lexical_clones((base, exact, base))
    assert len(groups) == 1 and groups[0].occurrence_count == 2

    conflicting = replace(base, canonical_bytes=base.canonical_bytes + b"different")
    with pytest.raises(GroupingInvariantError, match="conflicting canonical content"):
        group_lexical_clones((base, conflicting))
    with pytest.raises(GroupingInvariantError, match="occurrence ID"):
        group_lexical_clones((replace(base, occurrence_id="do1:wrong"),))


@pytest.mark.parametrize(
    "relative_path",
    ["/absolute.py", "C:/machine.py", "../parent.py", "dir\\windows.py", "a/./b.py"],
)
def test_nonportable_occurrence_paths_are_rejected(corpus_state, relative_path):
    base = _occurrences(corpus_state, "python/base.py")[0]
    syntax = corpus_state.syntaxes["python/base.py"]
    with pytest.raises(ValueError):
        make_lexical_occurrence(relative_path, syntax, base.candidate)


def test_unicode_relative_path_has_stable_portable_identity(corpus_state):
    occurrence = _occurrences(corpus_state, "python/café/日本語_unicode_a.py")[0]
    rebuilt = make_lexical_occurrence(
        occurrence.relative_path,
        corpus_state.syntaxes[occurrence.relative_path],
        occurrence.candidate,
    )
    assert rebuilt == occurrence
    assert occurrence.relative_path not in occurrence.fingerprint


def test_malformed_candidates_are_typed_unavailable(corpus_state):
    for relative in EXPECTATIONS["typed_unavailable"]:
        result = corpus_state.results[relative]
        assert result.status is CandidateExtractionStatus.UNAVAILABLE
        assert result.boundaries == ()
        assert result.candidates == ()


def test_injected_parser_unavailability_produces_no_d2_input():
    inventory = RepositoryInventory(CORPUS)
    record = next(record for record in inventory if record.relative_path == "java/base.java")

    class UnavailableRegistry(ParserRegistry):
        def get(self, language, extension=None):
            raise ParserUnavailableError("D2 injected parser unavailability")

    result = extract_file_candidates(
        record, inventory.read_bytes(record), UnavailableRegistry()
    )
    assert result.status is CandidateExtractionStatus.UNAVAILABLE
    assert result.boundaries == ()
    assert "D2 injected parser unavailability" in (result.reason or "")


def test_canonicalizer_rejects_unavailable_or_unadmitted_input(corpus_state):
    base = _occurrences(corpus_state, "python/base.py")[0]
    unavailable_syntax = corpus_state.syntaxes["python/malformed.py"]
    with pytest.raises(LexicalCanonicalizationError, match="unavailable"):
        canonical_lexical_bytes(unavailable_syntax, base.candidate)
    unadmitted = replace(
        base.candidate,
        extraction_status=CandidateAdmissionStatus.BELOW_THRESHOLDS,
        failed_floors=("tokens",),
    )
    with pytest.raises(LexicalCanonicalizationError, match="frozen D1 floors"):
        canonical_lexical_bytes(corpus_state.syntaxes["python/base.py"], unadmitted)


def _selected_candidates(root: Path, names: tuple[str, ...]):
    inventory = RepositoryInventory(root)
    registry = ParserRegistry()
    result = {}
    for record in inventory:
        if record.relative_path not in names:
            continue
        syntax = select_syntax(record, inventory.read_bytes(record), registry)
        extraction = extract_candidates(syntax)
        assert len(extraction.candidates) == 1
        result[record.relative_path] = (syntax, extraction.candidates[0])
    return result


def test_javascript_grammar_significant_line_boundary_is_preserved(tmp_path):
    prefix = "".join(
        f"  const value{index} = source + {index};\n" for index in range(1, 8)
    )
    (tmp_path / "joined.js").write_text(
        "function joined(source) {\n" + prefix + "  return source;\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "split.js").write_text(
        "function split(source) {\n" + prefix + "  return\n  source;\n}\n",
        encoding="utf-8",
    )
    selected = _selected_candidates(tmp_path, ("joined.js", "split.js"))
    joined = canonical_lexical_bytes(*selected["joined.js"])
    split = canonical_lexical_bytes(*selected["split.js"])
    assert joined != split


def test_javascript_nonsignificant_line_reformat_still_matches(tmp_path):
    tail = "".join(
        f"  const value{index} = source + {index};\n" for index in range(2, 10)
    )
    (tmp_path / "one.js").write_text(
        "function one(source) {\n  const value1 = source + 1;\n" + tail + "}\n",
        encoding="utf-8",
    )
    (tmp_path / "two.js").write_text(
        "function two(source) {\n  const value1 =\n    source + 1;\n" + tail + "}\n",
        encoding="utf-8",
    )
    selected = _selected_candidates(tmp_path, ("one.js", "two.js"))
    assert canonical_lexical_bytes(*selected["one.js"]) == canonical_lexical_bytes(
        *selected["two.js"]
    )


def test_fingerprint_input_has_no_path_time_or_random_identity(corpus_state):
    left = _occurrences(corpus_state, "python/base.py")[0]
    right = make_lexical_occurrence(
        "moved/elsewhere.py",
        corpus_state.syntaxes["python/base.py"],
        left.candidate,
    )
    assert left.canonical_bytes == right.canonical_bytes
    assert left.fingerprint == right.fingerprint
    assert left.occurrence_id != right.occurrence_id


def test_fingerprint_digest_test_seam_is_strict(corpus_state):
    canonical = _canonical(corpus_state, "python/base.py")
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        lexical_fingerprint("Python", canonical, digest=lambda _payload: b"short")
    payload_digest = hashlib.sha256(b"independent").digest()
    assert lexical_fingerprint(
        "Python", canonical, digest=lambda _payload: payload_digest
    ) == "sha256:" + payload_digest.hex()
