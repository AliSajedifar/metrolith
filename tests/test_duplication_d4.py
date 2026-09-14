"""D4 validation-only gates for Duplication Engine v1."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from validation.duplication_d4_20260819.run_synthetic_validation import (
    HERE as D4,
    evaluate,
    load_corpus,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def corpus_state():
    # The selected syntax for every file is built once and reused by every D4
    # relation/oracle/grouping check in this module.
    return load_corpus()


@pytest.fixture(scope="module")
def evaluation(corpus_state):
    return evaluate(corpus_state)


def test_d4_baseline_and_d5_product_integration_boundary():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "4.0.1"' in pyproject
    assert (ROOT / "modules" / "duplication" / "lexical.py").is_file()
    assert (ROOT / "modules" / "duplication" / "structural.py").is_file()
    assert (ROOT / "modules" / "duplication" / "grouping.py").is_file()
    assert (ROOT / "modules" / "duplication" / "structural_grouping.py").is_file()
    # D4's validated primitives are now consumed only by the separately
    # authorized D5 standalone command. The frozen integrations remain absent.
    assert (ROOT / "modules" / "cli" / "duplication_command.py").is_file()
    assert (ROOT / "modules" / "duplication" / "output.py").is_file()
    assert "duplication_command.add_parser" in (
        ROOT / "pipeline.py"
    ).read_text(encoding="utf-8")


#: The ONLY schema locations that may mention duplication, as
#: ``filename -> {json pointer of the enum}``.
#:
#: This replaces D4's original clause, which asserted the word `duplication`
#: appeared in NO registered schema at all. That clause was the standing
#: statement that duplication was a standalone contract with no schema surface,
#: and Duplication Policy Integration DP1 is the authorized change that retires
#: it -- the path `tests/test_policy_schema_evolution_v40.py` already named when
#: it wrote "Adding it is a `check_result` 1.2 change".
#:
#: The replacement is STRICTER, not weaker. The old clause could only have
#: caught a stray `duplication` key by also forbidding the legitimate ones; this
#: one parses the schemas and pins the exact six enum members, so a sixteenth
#: metric, a second evidence kind or a stray property cannot appear anywhere in
#: any schema without this test failing. Every previously published schema is
#: still required to contain zero occurrences.
_DUPLICATION_SCHEMA_ALLOWLIST: dict[str, tuple[tuple[str, ...], ...]] = {
    "policy_document-2.2.schema.json": (
        ("$defs", "metricRule", "properties", "metric", "enum"),
        ("$defs", "metricRule", "properties", "scope", "enum"),
    ),
    "check_result-1.2.schema.json": (
        ("properties", "evidence", "propertyNames", "enum"),
        ("$defs", "evidenceRecord", "properties", "kind", "enum"),
        ("$defs", "finding", "properties", "scope", "enum"),
    ),
    # Check Result 1.3 is additive over 1.2. Its ratchet summary creates no new
    # duplication surface, so the inherited three locations remain the whole
    # allowlist and every other occurrence is still rejected.
    "check_result-1.3.schema.json": (
        ("properties", "evidence", "propertyNames", "enum"),
        ("$defs", "evidenceRecord", "properties", "kind", "enum"),
        ("$defs", "finding", "properties", "scope", "enum"),
    ),
    "check_result-1.4.schema.json": (
        ("properties", "evidence", "propertyNames", "enum"),
        ("$defs", "evidenceRecord", "properties", "kind", "enum"),
        ("$defs", "finding", "properties", "scope", "enum"),
        (
            "properties", "evaluated_input_provenance", "properties",
            "evidence", "propertyNames", "enum",
        ),
    ),
    "trusted_evidence_receipt-1.0.schema.json": (
        ("properties", "evidence", "items", "properties", "kind", "enum"),
    ),
    # Report View 1.0 is a separately versioned, derived presentation contract.
    # These four pointers are its complete Duplication surface: the required
    # root domain, that domain's closed schema, and the explicit supplement
    # admission kind. Report View remains absent from the standalone evidence
    # registry and cannot expand any historical Duplication authority.
    "report_view-1.0.schema.json": (
        ("required", 10),
        ("properties", "duplication"),
        ("$defs", "duplication"),
        ("$defs", "admission", "properties", "kind", "enum", 0),
    ),
}

#: Schemas published before DP1. The original D4 clause stays fully in force for
#: every one of them: a duplication surface may never be back-fitted into a
#: contract that already shipped without it.
_DUPLICATION_FORBIDDEN_SCHEMAS = (
    "policy_document-1.0.schema.json",
    "policy_document-2.0.schema.json",
    "policy_document-2.1.schema.json",
    "check_result-1.0.schema.json",
    "check_result-1.1.schema.json",
)

_SCHEMA_DIR = ROOT / "validation" / "resources" / "schemas"


def _strip(document, pointers):
    """The document with each allowlisted enum removed."""
    for pointer in pointers:
        node = document
        for step in pointer[:-1]:
            node = node[step]
        del node[pointer[-1]]
    return document


def _surface(node):
    """The schema's SURFACE: property names, enum members, consts, patterns.

    `description` and `title` are prose and are excluded deliberately. The
    original D4 clause was a substring scan over the whole file, so it could not
    tell a documented vocabulary from a declared one -- and a 2.2 schema must be
    allowed to explain in words what its enum declares. What may never appear
    outside the allowlist is a duplication-named PROPERTY, ENUM MEMBER, CONST or
    PATTERN, because those are what a document is actually validated against.
    """
    if isinstance(node, dict):
        parts = []
        for key, value in node.items():
            if key in ("description", "title"):
                continue
            parts.append(str(key))
            parts.append(_surface(value))
        return " ".join(parts)
    if isinstance(node, list):
        return " ".join(_surface(item) for item in node)
    return str(node)


def test_no_schema_mentions_duplication_outside_the_approved_locations():
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        pointers = _DUPLICATION_SCHEMA_ALLOWLIST.get(path.name, ())
        document = json.loads(path.read_text(encoding="utf-8"))
        remainder = _surface(_strip(document, pointers)).lower()
        assert "duplication" not in remainder, (
            f"{path.name} declares a duplication surface outside its approved "
            f"locations"
        )


def test_every_previously_published_schema_still_forbids_duplication():
    # The ORIGINAL clause, unweakened, for every contract that shipped before
    # DP1: not the surface but the whole file, prose included.
    for name in _DUPLICATION_FORBIDDEN_SCHEMAS:
        text = (_SCHEMA_DIR / name).read_text(encoding="utf-8").lower()
        assert "duplication" not in text, name


def test_the_approved_duplication_schema_surface_is_exactly_this():
    policy = json.loads(
        (_SCHEMA_DIR / "policy_document-2.2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = json.loads(
        (_SCHEMA_DIR / "check_result-1.2.schema.json").read_text(encoding="utf-8")
    )
    metrics = [
        item for item in policy["$defs"]["metricRule"]["properties"]["metric"]["enum"]
        if "duplication" in item
    ]
    assert len(metrics) == 15, metrics
    assert policy["$defs"]["metricRule"]["properties"]["scope"]["enum"][-1] == (
        "duplication_group"
    )
    assert result["properties"]["evidence"]["propertyNames"]["enum"] == [
        "duplication", "hotspots",
    ]
    assert result["$defs"]["evidenceRecord"]["properties"]["kind"]["enum"] == [
        "duplication", "hotspots",
    ]
    assert result["$defs"]["finding"]["properties"]["scope"]["enum"][-1] == (
        "duplication_group"
    )
    # No score, no rank, no severity, no cross-kind total ever reaches a schema.
    for item in metrics:
        assert not any(
            word in item
            for word in ("score", "rank", "signal", "risk", "defect", "severity")
        ), item


def test_clean_room_oracle_has_no_production_duplication_imports():
    oracle_paths = (
        D4 / "clean_room_oracle.py",
        ROOT / "validation" / "duplication_d2_20260818" / "lexical_oracle.py",
        ROOT / "validation" / "duplication_d3a_20260818" / "structural_oracle.py",
        ROOT / "validation" / "duplication_d3b_20260819" / "grouping_oracle.py",
    )
    for path in oracle_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(name.startswith("modules.duplication") for name in imports), path


def test_corpus_manifest_is_complete_and_byte_exact():
    expected = json.loads((D4 / "corpus_hashes.json").read_text(encoding="utf-8"))
    actual = {
        path.relative_to(D4 / "corpus").as_posix(): {
            "bytes": len(path.read_bytes()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted((D4 / "corpus").rglob("*"))
        if path.is_file()
    }
    assert actual == expected
    assert len(actual) == 113


def test_expanded_corpus_covers_requested_dimensions():
    expectations = json.loads((D4 / "expectations.json").read_text(encoding="utf-8"))
    assert expectations["authored_before_d4_execution"] is True
    assert expectations["languages"] == [
        "Go", "Java", "JavaScript", "Python", "TypeScript"
    ]
    assert {"exact", "whitespace", "comment"} <= set(
        expectations["lexical"]["match_against_base"]
    )
    structural_reasons = {
        relation[2] for relation in expectations["structural"]["non_relations"]
    }
    assert {
        "identifier equality pattern",
        "literal class",
        "child order",
        "identifier role and AST shape",
        "f-string child order",
        "tagged template shape",
    } <= structural_reasons
    grouping = expectations["grouping"]
    assert {
        "same_file",
        "cross_file",
        "mixed",
        "multiple_occurrences",
        "nested_suppression",
        "inner_only",
        "siblings",
        "overlap_fixture",
    } <= set(grouping)
    assert expectations["below_floor"]
    assert len(expectations["malformed_unavailable"]) == 5


def test_all_synthetic_contract_oracle_and_risk_checks_pass(evaluation):
    assert evaluation["verdict"] == "pass"
    assert evaluation["disagreements"] == []
    assert len(evaluation["checks"]) >= 350
    assert all(check["passed"] for check in evaluation["checks"])
    assert evaluation["corpus"] == {
        "materialized_files": 113,
        "files_analyzed": 113,
        "boundaries_extracted": 218,
        "candidates_admitted": 117,
        "malformed_unavailable": 5,
    }


def test_initial_fixture_disagreement_was_classified_before_correction():
    log = json.loads((D4 / "disagreement_log.json").read_text(encoding="utf-8"))
    assert log["entries"] == [
        {
            "id": "D4-OBS-001",
            "observed_during": "initial synthetic execution",
            "check": "risks/syntax_augmented.py versus risks/syntax_expanded.py structural non-relation",
            "observed": "syntax_augmented.py produced zero admitted candidates",
            "classification_before_change": "extraction / validation-corpus fixture",
            "production_defect": False,
            "disposition": "Expanded both validation-only bodies above all frozen D1 floors; no production or contract code changed.",
        }
    ]


def test_persisted_synthetic_result_is_current(evaluation):
    persisted = json.loads((D4 / "synthetic_results.json").read_text(encoding="utf-8"))
    for key in ("verdict", "corpus", "production"):
        assert persisted[key] == evaluation[key]
    # Wall-clock measurements naturally differ; only their field contract and
    # non-negative values are gated here.
    assert set(persisted["timings"]) == {
        "extraction_seconds",
        "canonicalization_seconds",
        "grouping_seconds",
        "total_duplication_seconds",
    }
    assert all(value >= 0 for value in persisted["timings"].values())






