from __future__ import annotations

import copy
import hashlib
import json
import runpy
from pathlib import Path

import pytest

from modules.report_view_validation import (
    report_view_validation_problems,
    validate_report_view_bytes,
)


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "validation" / "report_view_v1_samples"
HISTORICAL_REFUSAL_SAMPLE = "missing-refused-unavailable.json"
CORRECTED_REFUSAL_SAMPLE = "supplied-refused-unavailable-corrected.json"
RETAINED_SAMPLE_SHA256 = {
    "small-complete-local.json": "7505d6b61b52317683b83c6ef9c4f09312b375ab615a76cf6e35a4ae85cca418",
    "medium-partial-multilanguage.json": "d2849fa60e45487234b4d3ca7ede0e91580a272d6a2d3fa3481b67024ad29e9b",
    "protected-check-and-ratchet.json": "b203803fbb3b638cf76dbffa32514edc4af1d6bdf0a0a65c7f4a04fc002e7c81",
    "all-supplements-admitted.json": "3fdd363dd012b5beeedebfa96605b4057905ebb2c419a8614cdfc76243d10f7a",
    HISTORICAL_REFUSAL_SAMPLE: "08fdb1641047a6f12b294aa10a0eaecf5cfb013c268dea910e652672fe8ee67f",
    "large-row-population.json": "91e96159969ea7f06b88f602a389955f31a591502077068d7c1fb16c2b1b0ef1",
}


def _manifest():
    return json.loads((SAMPLES / "SAMPLES.json").read_text(encoding="utf-8"))


def _documents():
    return {
        entry["name"]: json.loads((SAMPLES / entry["name"]).read_bytes())
        for entry in _manifest()["samples"]
    }


def test_all_samples_are_canonical_valid_and_match_the_manifest():
    manifest = _manifest()
    assert manifest["format"] == "archlens-report-view-sample-manifest"
    assert manifest["report_view_format_version"] == "1.0.0"
    assert len(manifest["samples"]) == 7
    for entry in manifest["samples"]:
        payload = (SAMPLES / entry["name"]).read_bytes()
        document = validate_report_view_bytes(payload)
        assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["bytes"] == len(payload)
        assert document["document_identity"]["sample_classification"] == "synthetic_design_fixture"
        assert document["privacy_inventory"]["source_bodies_present"] is False
        assert document["privacy_inventory"]["diff_bodies_present"] is False


def test_all_six_retained_payloads_keep_their_historical_exact_bytes():
    entries = {entry["name"]: entry for entry in _manifest()["samples"]}
    for name, expected_sha256 in RETAINED_SAMPLE_SHA256.items():
        payload = (SAMPLES / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
        assert entries[name]["sha256"] == expected_sha256


def test_schema_review_copy_matches_the_registered_schema_exactly():
    assert (SAMPLES / "report_view-1.0.schema.json").read_bytes() == (
        ROOT / "validation" / "resources" / "schemas" / "report_view-1.0.schema.json"
    ).read_bytes()


def test_required_scenarios_retain_their_distinguishing_states():
    documents = _documents()
    small = documents["small-complete-local.json"]
    assert small["subjects"][0]["display_name"] == "Local repository"
    assert {row["state"]["presentation_code"] for row in small["supplement_admission"]} == {"not_supplied"}
    assert small["trust_and_reproducibility"]["gate_mode"] == "local_unprotected"

    medium = documents["medium-partial-multilanguage.json"]
    assert len(medium["measurements"]["language_metrics"]) >= 3
    assert len(medium["measurements"]["callable_records"]) >= 200
    assert medium["diagnostics"]
    assert medium["run"]["lifecycle"]["presentation_code"] == "completed_with_errors"

    protected = documents["protected-check-and-ratchet.json"]
    assert protected["trust_and_reproducibility"]["gate_mode"] == "protected_required"
    assert protected["findings"]["ratchet"]["configured"] is True
    assert protected["findings"]["canonical_findings"]

    admitted = documents["all-supplements-admitted.json"]
    assert {row["state"]["presentation_code"] for row in admitted["supplement_admission"]} == {"admitted"}
    assert admitted["duplication"]["groups"]
    assert admitted["hotspots"]["rows"]
    assert admitted["changed_code"]["files"][0]["hunks"]

    missing = documents[HISTORICAL_REFUSAL_SAMPLE]
    states = {row["kind"]: row["state"]["presentation_code"] for row in missing["supplement_admission"]}
    assert states["duplication"] == "refused"
    assert states["changed"] == states["hotspots"] == "not_supplied"
    core = missing["measurements"]["core_metrics"]
    assert any(row["status"]["presentation_code"] == "unavailable" for row in core)
    assert any(row["status"]["presentation_code"] == "not_applicable" for row in core)
    assert any(row["value"] == 0 and row["status"]["measured_value"] for row in core)
    assert missing["findings"]["not_evaluable"]

    corrected = documents[CORRECTED_REFUSAL_SAMPLE]
    refusal = next(row for row in corrected["supplement_admission"] if row["kind"] == "duplication")
    assert refusal["supplied"] is True
    assert refusal["state"]["presentation_code"] == "refused"
    assert refusal["state"]["reason_code"] == "refused"
    assert refusal["document_format_version"] == "1.0.0"
    assert "independent validator" in refusal["detail"]
    assert refusal["contributes_evidence"] is False
    assert refusal["source_reference"] is not None
    assert corrected["duplication"]["availability"]["presentation_code"] == "refused"
    assert corrected["duplication"]["groups"] == []
    corrected_core = corrected["measurements"]["core_metrics"]
    assert any(row["value"] == 0 and row["status"]["measured_value"] is True for row in corrected_core)
    assert any(row["value"] is None and row["status"]["presentation_code"] == "unavailable" for row in corrected_core)
    assert any(row["value"] is None and row["status"]["presentation_code"] == "not_applicable" for row in corrected_core)
    assert corrected["findings"]["not_evaluable"][0]["status"]["presentation_code"] == "not_evaluable"
    assert corrected["trust_and_reproducibility"]["gate_mode"] == "local_unprotected"

    large = documents["large-row-population.json"]
    assert len(large["measurements"]["callable_records"]) == 2500
    assert len(large["duplication"]["groups"]) == 120
    assert len(large["hotspots"]["rows"]) == 500
    assert len(large["changed_code"]["files"]) == 250


def test_sample_rebuild_is_exact_byte_deterministic():
    namespace = runpy.run_path(str(SAMPLES / "build_samples.py"))
    rebuilt = namespace["build_all"]()
    for name, payload in rebuilt.items():
        assert payload == (SAMPLES / name).read_bytes()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["trust_and_reproducibility"].__setitem__("gate_mode", "local_unprotected"), "trust_status"),
        (lambda d: d["findings"].__setitem__("verdict", "pass"), "policy_verdict_internal"),
        (lambda d: d["findings"]["canonical_findings"][0]["status"].__setitem__("presentation_code", "not_evaluable"), "policy_finding_status"),
        (lambda d: d["findings"]["ratchet"].__setitem__("evaluated_count", 0), "ratchet_summary"),
        (lambda d: d["supplement_admission"][0]["state"].__setitem__("presentation_code", "refused"), "admission_contribution"),
        (lambda d: d["duplication"]["groups"][0].__setitem__("occurrence_count", 2), "duplication_occurrence_count"),
        (lambda d: d["duplication"]["counts"][0].__setitem__("value", 2), "duplication_count"),
        (lambda d: d["hotspots"]["rows"][0].__setitem__("attention_class", "low_attention"), "hotspot_class_count"),
        (lambda d: next(row for row in d["hotspots"]["counts"] if row["name"] == "row_count").__setitem__("value", 2), "hotspot_count"),
        (lambda d: next(row for row in d["changed_code"]["counts"] if row["name"] == "git_changed_files").__setitem__("value", 2), "changed_count"),
        (lambda d: d["source_documents"][0].__setitem__("sha256", "f" * 64), "source_reference_unknown"),
        (lambda d: d["duplication"]["groups"][0]["source_reference"].__setitem__("format_version", "9.9.9"), "source_reference_unknown"),
        (lambda d: d["duplication"]["groups"][0]["source_reference"].__setitem__("logical_pointer", "not/a/pointer"), "source_reference_pointer_invalid"),
        (lambda d: d["privacy_inventory"].__setitem__("source_bodies_present", True), "schema"),
    ],
)
def test_independent_internal_validator_detects_domain_mutations(mutate, expected):
    document = copy.deepcopy(_documents()["all-supplements-admitted.json"])
    mutate(document)
    codes = {problem.code for problem in report_view_validation_problems(document)}
    assert expected in codes, codes


def test_sample_status_and_traceability_class_coverage_is_explicit():
    documents = _documents()
    all_supplements = documents["all-supplements-admitted.json"]
    assert all_supplements["findings"]["ratchet"]["source_reference"]["logical_pointer"] == "/ratchet"
    assert all_supplements["duplication"]["groups"][0]["occurrences"][0]["source_reference"]
    assert all_supplements["hotspots"]["rows"][0]["source_reference"]
    assert all_supplements["changed_code"]["files"][0]["hunks"][0]["source_reference"]
    medium = documents["medium-partial-multilanguage.json"]
    assert medium["diagnostics"][0]["source_reference"]
    entries = {entry["name"]: entry for entry in _manifest()["samples"]}
    historical = documents[HISTORICAL_REFUSAL_SAMPLE]
    historical_refused = next(row for row in historical["supplement_admission"] if row["kind"] == "duplication")
    assert historical_refused["supplied"] is True
    assert historical["duplication"]["availability"]["presentation_code"] == "not_supplied"
    assert entries[HISTORICAL_REFUSAL_SAMPLE]["fixture_role"] == "retained_historical_design_evidence"
    assert entries[HISTORICAL_REFUSAL_SAMPLE]["explorer_refusal_missingness_use"] == "historical_evidence_only"
    corrected = documents[CORRECTED_REFUSAL_SAMPLE]
    refused = next(row for row in corrected["supplement_admission"] if row["kind"] == "duplication")
    assert refused["source_reference"] and refused["contributes_evidence"] is False
    assert entries[CORRECTED_REFUSAL_SAMPLE]["corrects_historical_sample"] == HISTORICAL_REFUSAL_SAMPLE
    assert entries[CORRECTED_REFUSAL_SAMPLE]["explorer_refusal_missingness_use"] == "future_design_and_testing"
