"""Execute the D4 synthetic campaign and persist classified observations."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from modules.duplication import (
    CandidateExtractionStatus,
    LexicalOccurrence,
    StructuralOccurrence,
    canonical_lexical_bytes,
    canonical_structural_bytes,
    extract_candidates,
    group_lexical_clones,
    group_structural_clones,
    lexical_fingerprint,
    structural_fingerprint,
)
from modules.duplication.grouping import occurrence_identity
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, select_syntax
from validation.duplication_d4_20260819.clean_room_oracle import (
    lexical_membership_snapshot,
    oracle_fingerprint,
    oracle_group_lexical_clones,
    oracle_group_structural_clones,
    oracle_lexical_bytes,
    oracle_structural_bytes,
    oracle_structural_fingerprint,
    structural_snapshot,
)


HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
EXPECTATIONS = json.loads((HERE / "expectations.json").read_text(encoding="utf-8"))
OUTPUT = HERE / "synthetic_results.json"
LANGUAGE_PATH = {
    "Go": ("go", "go"),
    "Java": ("java", "java"),
    "JavaScript": ("javascript", "js"),
    "Python": ("python", "py"),
    "TypeScript": ("typescript", "ts"),
}


@dataclass
class CorpusState:
    syntaxes: dict[str, Any]
    extractions: dict[str, Any]
    lexical: dict[str, tuple[LexicalOccurrence, ...]]
    structural: dict[str, tuple[StructuralOccurrence, ...]]
    timings: dict[str, float]
    files_analyzed: int


def _occurrence_id(relative_path: str, candidate: Any) -> str:
    return occurrence_identity(
        (
            relative_path,
            candidate.span.start_line,
            candidate.span.end_line,
            candidate.unit_kind.value,
        )
    )


def load_corpus() -> CorpusState:
    inventory = RepositoryInventory(CORPUS)
    registry = ParserRegistry()
    syntaxes: dict[str, Any] = {}
    extractions: dict[str, Any] = {}
    lexical: dict[str, tuple[LexicalOccurrence, ...]] = {}
    structural: dict[str, tuple[StructuralOccurrence, ...]] = {}
    timings = {"extraction_seconds": 0.0, "canonicalization_seconds": 0.0}
    files_analyzed = 0
    for record in inventory:
        if not record.included_in_metrics:
            continue
        files_analyzed += 1
        source = inventory.read_bytes(record)
        started = time.perf_counter()
        syntax = select_syntax(
            record,
            source,
            registry,
            python_text=(
                inventory.read_text(record) if record.detected_language == "Python" else None
            ),
        )
        extraction = extract_candidates(syntax)
        timings["extraction_seconds"] += time.perf_counter() - started
        syntaxes[record.relative_path] = syntax
        extractions[record.relative_path] = extraction

        started = time.perf_counter()
        lexical_items = []
        structural_items = []
        for candidate in extraction.candidates:
            lexical_bytes = canonical_lexical_bytes(syntax, candidate)
            lexical_items.append(
                LexicalOccurrence(
                    relative_path=record.relative_path,
                    candidate=candidate,
                    canonical_bytes=lexical_bytes,
                    fingerprint=lexical_fingerprint(syntax.language, lexical_bytes),
                    fingerprint_version="lexical-exact-v1",
                    occurrence_id=_occurrence_id(record.relative_path, candidate),
                )
            )
            structural_bytes = canonical_structural_bytes(syntax, candidate)
            structural_items.append(
                StructuralOccurrence(
                    relative_path=record.relative_path,
                    candidate=candidate,
                    canonical_bytes=structural_bytes,
                    fingerprint=structural_fingerprint(syntax.language, structural_bytes),
                    fingerprint_version="structural-v1",
                    occurrence_id=_occurrence_id(record.relative_path, candidate),
                )
            )
        timings["canonicalization_seconds"] += time.perf_counter() - started
        lexical[record.relative_path] = tuple(lexical_items)
        structural[record.relative_path] = tuple(structural_items)
    return CorpusState(syntaxes, extractions, lexical, structural, timings, files_analyzed)


def _path(language: str, stem: str) -> str:
    folder, extension = LANGUAGE_PATH[language]
    return f"{folder}/{stem}.{extension}"


def _one(items: dict[str, tuple[Any, ...]], relative: str) -> Any:
    values = items.get(relative, ())
    if len(values) != 1:
        raise LookupError(f"expected one admitted candidate in {relative}; found {len(values)}")
    return values[0]


def _select(items: dict[str, tuple[Any, ...]], paths: list[str]) -> tuple[Any, ...]:
    return tuple(item for path in paths for item in items.get(path, ()))


def _check(
    checks: list[dict[str, Any]],
    name: str,
    classification: str,
    operation: Callable[[], bool],
) -> None:
    try:
        passed = bool(operation())
        detail = None if passed else "predicate returned false"
    except Exception as exc:  # disagreements remain evidence, not silent skips
        passed = False
        detail = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "name": name,
            "passed": passed,
            "detail": detail,
            "disagreement_classification": None if passed else classification,
        }
    )


def evaluate(state: CorpusState) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    for relative, extraction in sorted(state.extractions.items()):
        syntax = state.syntaxes[relative]
        for ordinal, occurrence in enumerate(state.lexical[relative]):
            _check(
                checks,
                f"lexical oracle bytes: {relative}#{ordinal}",
                "canonical_bytes",
                lambda o=occurrence, s=syntax: (
                    o.canonical_bytes == oracle_lexical_bytes(s, o.candidate)
                    and o.fingerprint
                    == oracle_fingerprint("lexical-exact-v1", s.language, o.canonical_bytes)
                ),
            )
        for ordinal, occurrence in enumerate(state.structural[relative]):
            _check(
                checks,
                f"structural oracle bytes: {relative}#{ordinal}",
                "canonical_bytes",
                lambda o=occurrence, s=syntax: (
                    o.canonical_bytes == oracle_structural_bytes(s, o.candidate)
                    and o.fingerprint == oracle_structural_fingerprint(s.language, o.canonical_bytes)
                ),
            )

    for language in EXPECTATIONS["languages"]:
        base_path = _path(language, "base")
        lexical_base = _one(state.lexical, base_path).canonical_bytes
        structural_base = _one(state.structural, base_path).canonical_bytes
        for variant in EXPECTATIONS["lexical"]["match_against_base"]:
            relative = _path(language, variant)
            _check(
                checks,
                f"lexical match: {base_path} = {relative}",
                "lexical_contract",
                lambda r=relative, b=lexical_base: _one(state.lexical, r).canonical_bytes == b,
            )
        for variant in EXPECTATIONS["lexical"]["non_match_against_base"]:
            relative = _path(language, variant)
            _check(
                checks,
                f"lexical non-match: {base_path} != {relative}",
                "lexical_contract",
                lambda r=relative, b=lexical_base: _one(state.lexical, r).canonical_bytes != b,
            )
        for variant in EXPECTATIONS["structural"]["match_against_base"]:
            relative = _path(language, variant)
            _check(
                checks,
                f"structural match: {base_path} = {relative}",
                "structural_contract",
                lambda r=relative, b=structural_base: _one(state.structural, r).canonical_bytes == b,
            )
        for variant in EXPECTATIONS["structural"]["non_match_against_base"]:
            relative = _path(language, variant)
            _check(
                checks,
                f"structural non-match: {base_path} != {relative}",
                "structural_contract",
                lambda r=relative, b=structural_base: _one(state.structural, r).canonical_bytes != b,
            )
        _check(
            checks,
            f"structural child order: {_path(language, 'order_base')} != {_path(language, 'order_changed')}",
            "structural_contract",
            lambda lang=language: (
                _one(state.structural, _path(lang, "order_base")).canonical_bytes
                != _one(state.structural, _path(lang, "order_changed")).canonical_bytes
            ),
        )

    for left, right in EXPECTATIONS["lexical"]["portability_matches"]:
        _check(
            checks,
            f"lexical portability match: {left} = {right}",
            "lexical_contract",
            lambda a=left, b=right: (
                _one(state.lexical, a).canonical_bytes == _one(state.lexical, b).canonical_bytes
            ),
        )
    for left, right in EXPECTATIONS["lexical"]["portability_non_matches"]:
        _check(
            checks,
            f"lexical portability non-match: {left} != {right}",
            "lexical_contract",
            lambda a=left, b=right: (
                _one(state.lexical, a).canonical_bytes != _one(state.lexical, b).canonical_bytes
            ),
        )
    for left, right, reason in EXPECTATIONS["structural"]["match_relations"]:
        _check(
            checks,
            f"structural match ({reason}): {left} = {right}",
            "structural_contract",
            lambda a=left, b=right: (
                _one(state.structural, a).canonical_bytes == _one(state.structural, b).canonical_bytes
            ),
        )
    for left, right, reason in EXPECTATIONS["structural"]["non_relations"]:
        _check(
            checks,
            f"structural non-match ({reason}): {left} != {right}",
            "structural_contract",
            lambda a=left, b=right: (
                _one(state.structural, a).canonical_bytes != _one(state.structural, b).canonical_bytes
            ),
        )

    malformed = set(EXPECTATIONS["malformed_unavailable"])
    _check(
        checks,
        "malformed syntax is typed unavailable",
        "parser_limitation",
        lambda: all(
            state.extractions[path].status is CandidateExtractionStatus.UNAVAILABLE
            and not state.extractions[path].boundaries
            for path in malformed
        ),
    )
    _check(
        checks,
        "below-floor bodies are measured but not admitted",
        "extraction",
        lambda: all(
            state.extractions[path].boundaries and not state.extractions[path].candidates
            for path in EXPECTATIONS["below_floor"]
        ),
    )

    all_lexical = tuple(item for values in state.lexical.values() for item in values)
    all_structural = tuple(item for values in state.structural.values() for item in values)
    started = time.perf_counter()
    lexical_groups = group_lexical_clones(all_lexical)
    structural_result = group_structural_clones(all_structural)
    grouping_seconds = time.perf_counter() - started
    oracle_lexical = oracle_group_lexical_clones(all_lexical)
    oracle_structural = oracle_group_structural_clones(all_structural)
    _check(
        checks,
        "full-corpus lexical membership and identity oracle",
        "grouping",
        lambda: lexical_membership_snapshot(lexical_groups)
        == lexical_membership_snapshot(oracle_lexical),
    )
    _check(
        checks,
        "full-corpus structural membership and suppression oracle",
        "grouping",
        lambda: structural_snapshot(structural_result) == structural_snapshot(oracle_structural),
    )

    distributions = EXPECTATIONS["grouping"]
    expected_distribution = {
        "same_file": "same_file",
        "cross_file": "cross_file",
        "mixed": "mixed",
        "multiple_occurrences": "mixed",
    }
    for scenario, distribution in expected_distribution.items():
        inputs = _select(state.lexical, distributions[scenario])
        _check(
            checks,
            f"lexical grouping {scenario}",
            "grouping",
            lambda values=inputs, expected=distribution: (
                len(group_lexical_clones(values)) == 1
                and group_lexical_clones(values)[0].distribution.value == expected
            ),
        )

    for scenario in ("nested_suppression", "nested_third_retained", "inner_only", "siblings"):
        inputs = _select(state.structural, distributions[scenario])
        _check(
            checks,
            f"structural suppression oracle: {scenario}",
            "grouping",
            lambda values=inputs: structural_snapshot(group_structural_clones(values))
            == structural_snapshot(oracle_group_structural_clones(values)),
        )
    nested = group_structural_clones(
        _select(state.structural, distributions["nested_suppression"])
    )
    nested_third = group_structural_clones(
        _select(state.structural, distributions["nested_third_retained"])
    )
    _check(
        checks,
        "nested clone is suppressed only under a complete containment bijection",
        "grouping",
        lambda: nested.initial_group_count == 2 and len(nested.groups) == 1 and len(nested.dominance) == 1,
    )
    _check(
        checks,
        "third independent inner occurrence prevents suppression",
        "grouping",
        lambda: nested_third.initial_group_count == 2
        and len(nested_third.groups) == 2
        and not nested_third.dominance,
    )

    base = _one(state.structural, "python/base.py")
    left_candidate = replace(
        base.candidate,
        span=replace(
            base.candidate.span,
            start_byte=10,
            end_byte=90,
            start_line=2,
            end_line=9,
        ),
    )
    right_candidate = replace(
        base.candidate,
        span=replace(
            base.candidate.span,
            start_byte=50,
            end_byte=130,
            start_line=5,
            end_line=13,
        ),
    )
    overlap = tuple(
        replace(
            base,
            relative_path="synthetic/overlap.py",
            candidate=candidate,
            occurrence_id=_occurrence_id("synthetic/overlap.py", candidate),
        )
        for candidate in (left_candidate, right_candidate)
    )

    def rejects(operation: Callable[[], Any]) -> bool:
        try:
            operation()
        except (RuntimeError, ValueError):
            return True
        return False

    _check(
        checks,
        "production and oracle fail closed on partial overlap",
        "grouping",
        lambda: rejects(lambda: group_structural_clones(overlap))
        and rejects(lambda: oracle_group_structural_clones(overlap)),
    )

    disagreements = [item for item in checks if not item["passed"]]
    return {
        "format": "archlens-duplication-d4-synthetic-results",
        "format_version": "1.0.0",
        "verdict": "pass" if not disagreements else "disagreement",
        "corpus": {
            "materialized_files": len(json.loads((HERE / "corpus_hashes.json").read_text(encoding="utf-8"))),
            "files_analyzed": state.files_analyzed,
            "boundaries_extracted": sum(len(value.boundaries) for value in state.extractions.values()),
            "candidates_admitted": sum(len(value.candidates) for value in state.extractions.values()),
            "malformed_unavailable": sum(
                value.status is CandidateExtractionStatus.UNAVAILABLE
                for value in state.extractions.values()
            ),
        },
        "production": {
            "lexical_occurrences": len(all_lexical),
            "structural_occurrences": len(all_structural),
            "lexical_groups": len(lexical_groups),
            "structural_initial_groups": structural_result.initial_group_count,
            "structural_groups": len(structural_result.groups),
            "suppressed_structural_groups": len(structural_result.suppressed_group_ids),
        },
        "timings": {
            **state.timings,
            "grouping_seconds": grouping_seconds,
            "total_duplication_seconds": (
                state.timings["extraction_seconds"]
                + state.timings["canonicalization_seconds"]
                + grouping_seconds
            ),
        },
        "checks": checks,
        "disagreements": disagreements,
        "classification_rule": (
            "Every failed check is recorded as extraction, lexical_contract, "
            "structural_contract, canonical_bytes, grouping, or parser_limitation "
            "before any implementation change is considered."
        ),
    }


def main() -> int:
    state = load_corpus()
    result = evaluate(state)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({key: result[key] for key in ("verdict", "corpus", "production", "timings")}, indent=2))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
