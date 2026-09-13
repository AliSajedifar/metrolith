from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules import dossier
from modules.report_view import (
    REPORT_VIEW_FORMAT,
    REPORT_VIEW_FORMAT_VERSION,
    ReportViewBuildError,
    build_report_view,
    canonical_report_view_bytes,
)
from modules.report_view_validation import (
    ReportViewValidationError,
    report_view_validation_problems,
    validate_report_view_bytes,
    validate_report_view_document,
)
from modules.standalone_contracts import (
    STANDALONE_CONTRACTS,
    UnsupportedStandaloneContract,
    validate_standalone_document,
)
from validation.artifact_io.schema_store import validate_document


class _Reader:
    def __init__(self, root: Path):
        self.root = root

    def document_bytes(self, relative: str) -> bytes:
        return (self.root / relative).read_bytes()

    def family(self, name: str):
        root = self.root / name
        if not root.is_dir():
            return ()
        return tuple(
            path.relative_to(self.root).as_posix()
            for path in sorted(root.glob("*.json"))
        )


class _View:
    def __init__(self, root: Path, *, hostile_error: str | None = None):
        self.run_directory = root
        self.run_id = "run-fixture-1"
        self.integrity_status = "completed"
        self.finalized = True
        self.lifecycle = SimpleNamespace(value="finalized_valid")
        self.compatibility = SimpleNamespace(state=SimpleNamespace(value="supported"))
        self.manifest = {
            "run_id": self.run_id,
            "product_name": "ArchLens",
            "program_version": "3.8.0",
            "artifact_schema_version": "1.12.0",
            "metric_contract_version": "3.0.0",
            "complexity_contract_version": "2.0.0",
            "exclusion_policy_version": "1.5.0",
            "inventory_schema_version": "1.7.0",
            "profiler_provenance_kind": "installed_distribution",
            "profiler_source_sha256": "a" * 64,
            "profiler_git_state": "not_applicable",
            "profiler_git_dirty": None,
            "execution_mode": "metrics",
        }
        self.status = {
            "run_id": self.run_id,
            "status": "completed",
            "measurement_outcome": "complete",
        }
        self.environment = {"archlens_version": "3.8.0"}
        self.repositories = ({
            "subject_key": "example:fixture",
            "subject_key_portable": True,
            "subject_key_basis": "explicit",
            "repository_url": None,
            "architecture_type": "unknown",
            "architecture_type_source": "supplied_metadata",
            "analysis_status": "complete",
            "core_metric_status": "complete",
            "metric_contract_version": "3.0.0",
            "analysis_scope_hash": "sha256:" + "b" * 64,
            "acquisition": {"analyzed_commit_sha": "c" * 40},
            "metrics": {
                "aggregate": {
                    "source_files": 1,
                    "lines_of_code": 4,
                    "classes_structs": 0,
                    "methods_functions": 1,
                    "source_files_status": "complete",
                    "loc_status": "complete",
                    "classes_structs_status": "complete",
                    "methods_functions_status": "complete",
                },
                "source_files_by_language": {"Python": 1},
                "complexity": {
                    "status": "complete",
                    "cognitive_measurement_state": "measured",
                    "aggregate": {
                        "callable_count": 1,
                        "cyclomatic_complexity_max": 1,
                    },
                },
            },
        },)
        self.language_metrics = (
            {
                "subject_key": "example:fixture", "language": "python",
                "source_files": 1, "lines_of_code": 4,
                "classes_structs": 0, "methods_functions": 1,
                "source_files_status": "complete", "loc_status": "complete",
                "classes_structs_status": "complete",
                "methods_functions_status": "complete", "metric_status": "complete",
            },
            {
                "subject_key": "example:fixture", "language": "java",
                "source_files": 0, "lines_of_code": 0,
                "classes_structs": 0, "methods_functions": 0,
                "source_files_status": "not_applicable", "loc_status": "not_applicable",
                "classes_structs_status": "not_applicable",
                "methods_functions_status": "not_applicable",
                "metric_status": "not_applicable",
            },
        )
        self.sheet_metrics = ()
        self._callables = ({
            "subject_key": "example:fixture",
            "detected_language": "Python",
            "relative_path": "src/app.py",
            "callable_row_id": "sha256:" + "d" * 64,
            "qualified_name": "main",
            "start_line": 1, "end_line": 4, "nloc": 4,
            "cyclomatic_complexity": 1, "cognitive_complexity": 0,
            "max_nesting_depth": 0, "formal_parameter_count": 0,
            "structural_complexity_status": "complete", "nloc_status": "complete",
        },)
        self.has_callable_artifact = True
        self.errors = (() if hostile_error is None else ({
            "subject_key": "example:fixture", "detected_language": "Python",
            "file_path": "src/app.py", "error_category": "read",
            "error_type": "read_failed", "severity": "error", "message": hostile_error,
        },))
        self.recoveries = ()
        self.inventories = {}
        self.reader = _Reader(root)

    def stream_callables(self):
        yield from self._callables


def _write_view(tmp_path: Path, *, hostile_error: str | None = None) -> _View:
    view = _View(tmp_path, hostile_error=hostile_error)
    documents = {
        "run_manifest.json": view.manifest,
        "run_status.json": view.status,
        "environment.json": view.environment,
        "analysis.json": list(view.repositories),
    }
    for relative, value in documents.items():
        (tmp_path / relative).write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n",
        )
    rows = {
        "sheet_metrics.csv": "subject_key\nexample:fixture\n",
        "language_metrics.csv": "subject_key,language\nexample:fixture,python\n",
        "callables.csv": "callable_row_id\nsha256:" + "d" * 64 + "\n",
        "errors.csv": "subject_key,message\n",
        "recoveries.csv": "subject_key,message\n",
        "catalog.csv": "subject_key\nexample:fixture\n",
    }
    for relative, text in rows.items():
        (tmp_path / relative).write_text(text, encoding="utf-8", newline="\n")
    return view


@pytest.fixture
def built(tmp_path):
    view = _write_view(tmp_path)
    return view, build_report_view(view)


def test_identity_schema_registry_and_evidence_boundaries(built):
    view, document = built
    assert document["format"] == REPORT_VIEW_FORMAT
    assert document["format_version"] == REPORT_VIEW_FORMAT_VERSION
    assert validate_document("report_view", document, "fixture") == []
    assert "archlens-report-view" not in STANDALONE_CONTRACTS
    with pytest.raises(UnsupportedStandaloneContract):
        validate_standalone_document(document)
    refused = dossier.build_dossier(view, duplication=document)
    record = next(row for row in refused["supplements"] if row["kind"] == "duplication")
    assert record["admission"] == dossier.CONTRACT_INCOMPATIBLE
    assert record["summary"] is None
    assert validate_document("analysis", document, "not-a-run")


def test_canonical_bytes_are_compact_deterministic_and_movable(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = build_report_view(_write_view(first_root))
    second = build_report_view(_write_view(second_root))
    one = canonical_report_view_bytes(first)
    assert one == canonical_report_view_bytes(first)
    assert one == canonical_report_view_bytes(second)
    assert one.endswith(b"\n") and not one.endswith(b"\n\n")
    assert b"\r" not in one
    assert b'": "' not in one
    assert str(first_root).encode() not in one


def test_strict_bytes_refuse_duplicate_nonfinite_and_deep_json(built):
    _view, document = built
    payload = canonical_report_view_bytes(document)
    validate_report_view_bytes(payload)
    with pytest.raises(ReportViewValidationError) as duplicate:
        validate_report_view_bytes(b'{"format":"archlens-report-view","format":"x"}\n')
    assert duplicate.value.problems[0].code == "json_duplicate_key"
    with pytest.raises(ReportViewValidationError) as nonfinite:
        validate_report_view_bytes(b'{"value":NaN}\n')
    assert nonfinite.value.problems[0].code == "json_non_standard_number"
    with pytest.raises(ReportViewValidationError) as deep:
        validate_report_view_bytes(("[" * 65 + "0" + "]" * 65).encode())
    assert deep.value.problems[0].code == "json_depth_exceeded"


@pytest.mark.parametrize(
    ("location", "mutate", "expected_code"),
    [
        ("subject", lambda d: d["subjects"][0].__setitem__("subject_key", "changed"), "subject_or_revision"),
        ("revision", lambda d: d["subjects"][0].__setitem__("analyzed_revision", "e" * 40), "subject_or_revision"),
        ("metric", lambda d: d["measurements"]["core_metrics"][0].__setitem__("value", 99), "core_metric"),
        ("zero-status", lambda d: d["measurements"]["core_metrics"][0]["status"].update({"presentation_code": "unavailable", "measured_value": False}), "zero_status_pair"),
        ("language", lambda d: d["measurements"]["language_metrics"][0].__setitem__("language", "ruby"), "language_metric"),
        ("callable", lambda d: d["measurements"]["callable_records"][0].__setitem__("cyclomatic_complexity", 9), "callable_population"),
        ("trust", lambda d: d["trust_and_reproducibility"].__setitem__("run_manifest_digest", "f" * 64), "trust_manifest_digest"),
        ("lifecycle", lambda d: d["run"]["lifecycle"].__setitem__("raw_source_code", "failed"), "run_lifecycle"),
        ("digest", lambda d: d["source_documents"][0].__setitem__("sha256", "f" * 64), "source_digest"),
        ("reference", lambda d: d["measurements"]["core_metrics"][0]["source_reference"].__setitem__("sha256", "f" * 64), "source_reference_unknown"),
        ("privacy", lambda d: d["privacy_inventory"].__setitem__("source_bodies_present", True), "schema"),
    ],
)
def test_independent_validator_detects_mutations(built, location, mutate, expected_code):
    view, document = built
    changed = copy.deepcopy(document)
    mutate(changed)
    codes = {problem.code for problem in report_view_validation_problems(changed, source_run=view)}
    assert expected_code in codes, (location, codes)


def test_report_view_1_0_empty_projection_arrays_validate(built):
    view, document = built
    measurements = document["measurements"]
    assert measurements["frequency_distributions"] == []
    assert measurements["bounded_projections"] == []
    assert report_view_validation_problems(document, source_run=view) == []
    validate_report_view_document(document, source_run=view)


@pytest.mark.parametrize(
    ("field", "row", "expected_code"),
    [
        (
            "frequency_distributions",
            {
                "subject_key": "example:fixture",
                "metric": "cyclomatic_complexity",
                "values": [{"name": "1", "value": 1}],
            },
            "frequency_distribution_present",
        ),
        (
            "bounded_projections",
            {
                "name": "highest-cyclomatic-complexity",
                "bounded": True,
                "limit": 1,
                "row_ids": ["sha256:" + "d" * 64],
            },
            "bounded_projection_present",
        ),
    ],
)
def test_independent_validator_rejects_nonempty_projection_arrays(
    built, field, row, expected_code,
):
    view, document = built
    assert document["measurements"][field] == []
    changed = copy.deepcopy(document)
    fabricated = copy.deepcopy(row)
    fabricated["source_reference"] = copy.deepcopy(
        document["measurements"]["callable_records"][0]["source_reference"]
    )
    changed["measurements"][field].append(fabricated)

    assert validate_document("report_view", changed, "fabricated-projection") == []
    problems = report_view_validation_problems(changed, source_run=view)
    assert {(problem.code, problem.location) for problem in problems} == {
        (expected_code, f"/measurements/{field}")
    }
    with pytest.raises(ReportViewValidationError) as caught:
        validate_report_view_document(changed, source_run=view)
    assert {problem.code for problem in caught.value.problems} == {expected_code}


def test_unknown_field_wrong_version_and_malformed_status_fail_schema(built):
    _view, document = built
    for mutate in (
        lambda d: d.__setitem__("unknown", True),
        lambda d: d.__setitem__("format_version", "2.0.0"),
        lambda d: d["run"].__setitem__("subject_count", "one"),
        lambda d: d["run"].__setitem__("lifecycle", {"presentation_code": "complete"}),
    ):
        changed = copy.deepcopy(document)
        mutate(changed)
        with pytest.raises(ReportViewValidationError):
            validate_report_view_document(changed)


def test_absence_zero_and_all_admission_records_remain_distinct(built):
    _view, document = built
    admissions = {row["kind"]: row for row in document["supplement_admission"]}
    assert sorted(admissions) == ["changed", "duplication", "hotspots", "policy_result"]
    assert {row["state"]["presentation_code"] for row in admissions.values()} == {"not_supplied"}
    java = next(row for row in document["measurements"]["language_metrics"] if row["language"] == "java")
    assert {metric["value"] for metric in java["metrics"]} == {0}
    assert java["completeness"]["presentation_code"] == "not_applicable"
    assert java["completeness"]["measured_value"] is False


def test_local_subject_display_and_privacy_sanitization(tmp_path):
    view = _write_view(
        tmp_path,
        hostile_error=r"failed under C:\\Users\\alice\\secret.py with password=hunter2",
    )
    document = build_report_view(view)
    assert document["subjects"][0]["display_name"] == "Local repository"
    assert document["subjects"][0]["repository_locator"] is None
    assert document["diagnostics"][0]["message"] == "unsafe source text omitted"
    payload = canonical_report_view_bytes(document)
    assert b"alice" not in payload and b"hunter2" not in payload
    validate_report_view_document(document, source_run=view)


def test_incompatible_unreadable_and_refused_supplements_never_contribute(built):
    view, base = built
    document = build_report_view(
        view,
        duplication=base,
        read_errors={"hotspots": "cannot decode supplied bytes"},
    )
    states = {
        row["kind"]: row["state"]["presentation_code"]
        for row in document["supplement_admission"]
    }
    assert states["duplication"] == "incompatible"
    assert states["hotspots"] == "unreadable"
    assert document["duplication"]["groups"] == []
    assert document["hotspots"]["rows"] == []
    validate_report_view_document(
        document,
        source_run=view,
        duplication=base,
        read_errors={"hotspots": "cannot decode supplied bytes"},
    )


def test_validator_refused_supplement_has_refused_section_availability(built):
    view, _base = built
    invalid_duplication = {
        "format": "archlens-duplication",
        "format_version": "1.0.0",
    }
    document = build_report_view(view, duplication=invalid_duplication)
    refusal = next(
        row
        for row in document["supplement_admission"]
        if row["kind"] == "duplication"
    )

    assert refusal["supplied"] is True
    assert refusal["document_format_version"] == "1.0.0"
    assert refusal["state"]["presentation_code"] == "refused"
    assert refusal["state"]["reason_code"] == "refused"
    assert refusal["contributes_evidence"] is False
    assert document["duplication"]["availability"]["presentation_code"] == "refused"
    assert document["duplication"]["groups"] == []
    validate_report_view_document(
        document,
        source_run=view,
        duplication=invalid_duplication,
    )


def test_builder_refuses_nonfinalized_run(tmp_path):
    view = _write_view(tmp_path)
    view.finalized = False
    with pytest.raises(ReportViewBuildError):
        build_report_view(view)


def test_inventory_exclusions_oversized_and_partial_parser_are_traceable(tmp_path):
    view = _write_view(tmp_path)
    result = view.repositories[0]
    result["repository_owner"] = "example"
    result["repository_name"] = "fixture"
    inventory = {
        "inventory_schema_version": "1.7.0",
        "files": [
            {"relative_path": "generated/out.py", "detected_language": "Python", "exclusion_reason": "generated", "oversized": False, "read_status": "complete", "encoding_error": None, "parse_status": None},
            {"relative_path": "large/data.py", "detected_language": "Python", "exclusion_reason": None, "oversized": True, "read_status": "complete", "encoding_error": None, "parse_status": None},
            {"relative_path": "src/partial.py", "detected_language": "Python", "exclusion_reason": None, "oversized": False, "read_status": "complete", "encoding_error": None, "parse_status": "partial", "parser_diagnostic": "partial parse", "parser_compatibility_strategy": "retained measured prefix"},
        ],
    }
    relative = "file_inventory/example__fixture.json"
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    view.inventories = {relative: inventory}
    document = build_report_view(view)
    assert {row["kind"] for row in document["diagnostics"]} == {
        "excluded_file", "oversized_file", "parser_partial",
    }
    assert all(row["source_reference"]["source_role"].startswith("file_inventory:") for row in document["diagnostics"])
    validate_report_view_document(document, source_run=view)


@pytest.mark.parametrize(
    "hostile",
    [
        '<script>alert("x")</script>',
        "password=not-for-export",
        "hostname=private-builder-01",
        "unsafe\u202eexe.txt",
        "control\x01text",
    ],
)
def test_hostile_or_private_diagnostic_text_is_omitted(tmp_path, hostile):
    view = _write_view(tmp_path, hostile_error=hostile)
    document = build_report_view(view)
    assert document["diagnostics"][0]["message"] == "unsafe source text omitted"
    validate_report_view_document(document, source_run=view)


def test_canonical_bytes_normalize_equivalent_object_key_order(tmp_path):
    view = _write_view(tmp_path)
    document = build_report_view(view)

    def reverse_mappings(value):
        if isinstance(value, dict):
            return {
                key: reverse_mappings(child)
                for key, child in reversed(tuple(value.items()))
            }
        if isinstance(value, list):
            return [reverse_mappings(child) for child in value]
        return value

    assert canonical_report_view_bytes(reverse_mappings(document)) == canonical_report_view_bytes(document)
