"""Phase 1 structural-layer tests for ArchLens 3.5.0.

Covers the finding F-1 version-scoped legacy cell decoder, strict JSON and CSV
parsing, path admission, compatibility classification, packaged schema
resources, and the mechanical import-edge rule from plan section 3.2.
"""

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from validation.artifact_io import (
    JSON_NULL,
    CellType,
    ColumnSpec,
    CompatibilityState,
    TableContract,
    classify_artifact_schema,
    decode_legacy_list_cell,
    is_legacy_list_column,
    legacy_recovery_applies,
    read_rows,
)
from validation.artifact_io.errors import ArtifactStructureError, StructuralErrorCode
from validation.artifact_io.legacy_cells import (
    LEGACY_LIST_COLUMNS,
    MAX_LEGACY_CELL_CHARACTERS,
)

REPOSITORY = Path(__file__).resolve().parent.parent

from tests import historical_fixtures

# The four columns that changed serialization at the Artifact 1.3 -> 1.4
# boundary, and the contract used to exercise them.
ERRORS_CONTRACT = TableContract(
    name="errors",
    columns=(
        ColumnSpec("repository_url"),
        ColumnSpec(
            "affected_metrics",
            cell_type=CellType.JSON,
            legacy_python_repr_until="1.4.0",
        ),
    ),
)


def _write_csv(directory: Path, name: str, header: str, *rows: str) -> Path:
    path = directory / name
    path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8", newline="")
    return path


class LegacyCellAllowlistTests(unittest.TestCase):
    def test_allowlist_is_exactly_the_four_observed_columns(self):
        self.assertEqual(
            set(LEGACY_LIST_COLUMNS),
            {
                ("errors.csv", "affected_metrics"),
                ("recoveries.csv", "affected_metrics"),
                ("recoveries.csv", "fallback_strategies"),
                ("recoveries.csv", "selected_fallback_strategies"),
            },
        )

    def test_column_outside_allowlist_is_never_eligible(self):
        self.assertFalse(is_legacy_list_column("catalog.csv", "affected_metrics"))
        self.assertFalse(is_legacy_list_column("errors.csv", "metadata_json"))

    def test_undeclared_version_does_not_enable_recovery(self):
        # Absence of a version is not positive evidence of the old generation.
        self.assertFalse(legacy_recovery_applies(None))

    def test_boundary_is_exclusive(self):
        self.assertTrue(legacy_recovery_applies((1, 3, 0)))
        self.assertTrue(legacy_recovery_applies((1, 2, 0)))
        self.assertFalse(legacy_recovery_applies((1, 4, 0)))
        self.assertFalse(legacy_recovery_applies((1, 5, 0)))


class LegacyCellDecoderTests(unittest.TestCase):
    def _decode(self, raw, *, version="1.3.0"):
        return decode_legacy_list_cell(
            raw,
            artifact="errors.csv",
            column="affected_metrics",
            row=2,
            declared_version=version,
        )

    def test_recovers_flat_list_of_strings(self):
        recovery = self._decode("['classes_structs', 'methods_functions']")
        self.assertEqual(recovery.decoded, ["classes_structs", "methods_functions"])

    def test_decoded_value_equals_the_json_form_of_the_same_list(self):
        raw_repr = "['classes_structs', 'methods_functions']"
        raw_json = '["classes_structs", "methods_functions"]'
        self.assertEqual(self._decode(raw_repr).decoded, json.loads(raw_json))

    def test_raw_input_is_preserved_verbatim(self):
        raw = "['lines_of_code']"
        recovery = self._decode(raw)
        self.assertEqual(recovery.raw, raw)
        self.assertEqual(recovery.as_dict()["raw_value"], raw)

    def test_diagnostic_carries_every_required_field(self):
        payload = self._decode("['lines_of_code']").as_dict()
        self.assertEqual(payload["recovery"], "legacy_python_repr_list_cell")
        self.assertEqual(payload["artifact"], "errors.csv")
        self.assertEqual(payload["column"], "affected_metrics")
        self.assertEqual(payload["row"], 2)
        self.assertEqual(payload["declared_artifact_schema_version"], "1.3.0")
        self.assertEqual(payload["decoded_value"], ["lines_of_code"])

    def test_empty_list_recovers(self):
        self.assertEqual(self._decode("[]").decoded, [])

    def test_non_list_top_level_is_rejected(self):
        for raw in ("{'a': 1}", "'a string'", "42", "('a', 'b')"):
            with self.subTest(raw=raw):
                with self.assertRaises(ArtifactStructureError) as caught:
                    self._decode(raw)
                self.assertEqual(
                    caught.exception.code, StructuralErrorCode.CSV_JSON_CELL_MALFORMED
                )

    def test_non_string_elements_are_rejected(self):
        for raw in ("[1, 2]", "[None]", "[True]", "[['nested']]", "[{'k': 'v'}]"):
            with self.subTest(raw=raw):
                with self.assertRaises(ArtifactStructureError) as caught:
                    self._decode(raw)
                self.assertEqual(
                    caught.exception.code, StructuralErrorCode.CSV_JSON_CELL_MALFORMED
                )

    def test_oversized_cell_is_rejected_before_parsing(self):
        raw = "['" + "x" * (MAX_LEGACY_CELL_CHARACTERS + 10) + "']"
        with self.assertRaises(ArtifactStructureError) as caught:
            self._decode(raw)
        self.assertEqual(
            caught.exception.code, StructuralErrorCode.CSV_JSON_CELL_MALFORMED
        )
        self.assertIn("bound for literal recovery", str(caught.exception))

    def test_unparsable_literal_is_rejected(self):
        with self.assertRaises(ArtifactStructureError) as caught:
            self._decode("['unterminated")
        self.assertEqual(
            caught.exception.code, StructuralErrorCode.CSV_JSON_CELL_MALFORMED
        )

    def test_expression_input_is_refused_not_evaluated(self):
        # literal_eval evaluates literals only; an expression must not run.
        with self.assertRaises(ArtifactStructureError):
            self._decode("[__import__('os').getcwd()]")


class LegacyCellVersionGateTests(unittest.TestCase):
    """The decoder must be reachable at 1.3 and unreachable at 1.4 and above."""

    RAW = "['classes_structs', 'methods_functions']"

    def _read(self, version):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(
                Path(directory), "errors.csv",
                "repository_url,affected_metrics",
                f'https://example.test/a,"{self.RAW}"',
            )
            recoveries = []
            rows = read_rows(
                path, "errors.csv", ERRORS_CONTRACT,
                declared_version=version, recoveries=recoveries,
            )
            return rows, recoveries

    def test_recovers_at_artifact_1_3(self):
        rows, recoveries = self._read("1.3.0")
        self.assertEqual(
            rows[0]["affected_metrics"], ["classes_structs", "methods_functions"]
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0].declared_artifact_schema_version, "1.3.0")

    def test_same_value_is_rejected_at_artifact_1_4(self):
        with self.assertRaises(ArtifactStructureError) as caught:
            self._read("1.4.0")
        self.assertEqual(
            caught.exception.code, StructuralErrorCode.CSV_JSON_CELL_MALFORMED
        )

    def test_same_value_is_rejected_at_artifact_1_5(self):
        with self.assertRaises(ArtifactStructureError):
            self._read("1.5.0")

    def test_same_value_is_rejected_when_version_is_undeclared(self):
        with self.assertRaises(ArtifactStructureError):
            self._read(None)

    def test_canonical_json_never_triggers_the_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(
                Path(directory), "errors.csv",
                "repository_url,affected_metrics",
                'https://example.test/a,"[""lines_of_code""]"',
            )
            recoveries = []
            rows = read_rows(
                path, "errors.csv", ERRORS_CONTRACT,
                declared_version="1.3.0", recoveries=recoveries,
            )
        self.assertEqual(rows[0]["affected_metrics"], ["lines_of_code"])
        self.assertEqual(recoveries, [], "canonical JSON must not emit a recovery")

    def test_recovery_is_unreachable_without_the_contract_opt_in(self):
        contract = TableContract(
            name="errors",
            columns=(
                ColumnSpec("repository_url"),
                ColumnSpec("affected_metrics", cell_type=CellType.JSON),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(
                Path(directory), "errors.csv",
                "repository_url,affected_metrics",
                f'https://example.test/a,"{self.RAW}"',
            )
            with self.assertRaises(ArtifactStructureError):
                read_rows(path, "errors.csv", contract, declared_version="1.3.0")


class ColumnSpecOptInTests(unittest.TestCase):
    def test_legacy_marker_requires_a_json_column(self):
        with self.assertRaises(ValueError):
            ColumnSpec("x", cell_type=CellType.STRING, legacy_python_repr_until="1.4.0")

    def test_legacy_marker_must_be_a_parseable_version(self):
        with self.assertRaises(ValueError):
            ColumnSpec("x", cell_type=CellType.JSON, legacy_python_repr_until="1.4")


class NoEvalAnywhereTests(unittest.TestCase):
    """Source scan: the structural layer must never call ``eval`` or ``exec``."""

    def test_structural_layer_contains_no_eval_or_exec_call(self):
        package = REPOSITORY / "validation" / "artifact_io"
        offenders = []
        for source in sorted(package.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in {"eval", "exec", "compile"}:
                        offenders.append(f"{source.name}:{node.lineno} {node.func.id}")
        self.assertEqual(offenders, [])


class PreservedArtifact13CorpusTests(unittest.TestCase):
    """Every tracked Artifact 1.3 legacy cell must decode.

    This is the finding F-1 acceptance criterion. The corpus is now the two
    TRACKED 1.3 fixtures, and the expected counts are 15 ``errors.csv`` cells
    and 6 ``recoveries.csv`` cells.

    Those constants used to read 128 and 477, and that pair was **not
    reproducible**. The corpus was gathered by scanning the whole working tree,
    so it drew on run directories under gitignored ``output/`` and ``temp/``:
    every one of the thirteen 1.3 runs it found was untracked. On a fresh clone
    the scan found nothing, ``found_any`` stayed false, and the test skipped --
    reporting no legacy-cell coverage at all as a pass. The counts are lower
    now because the corpus is smaller *and real*: two committed fixtures, both
    kinds of legacy cell, the same decoder, and a number that any clone
    reproduces.

    Absence is a failure rather than a skip: the fixtures are tracked test
    resources, so a missing one is a repository defect.
    """

    @staticmethod
    def _legacy_runs():
        for run in historical_fixtures.runs_for("1.3.0"):
            version = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )["artifact_schema_version"]
            assert legacy_recovery_applies(
                classify_artifact_schema(version).parsed
            ), f"{run} does not fall in the legacy-recovery window"
            yield version, run

    def test_all_preserved_1_3_cells_decode(self):
        import csv as _csv

        _csv.field_size_limit(2**31 - 1)
        columns = {
            "errors.csv": ["affected_metrics"],
            "recoveries.csv": [
                "affected_metrics",
                "fallback_strategies",
                "selected_fallback_strategies",
            ],
        }
        decoded = {"errors.csv": 0, "recoveries.csv": 0}
        failures = []
        found_any = False

        for version, run in self._legacy_runs():
            for filename, names in columns.items():
                path = run / filename
                if not path.exists():
                    continue
                found_any = True
                with path.open(encoding="utf-8", newline="") as handle:
                    for row_number, row in enumerate(_csv.DictReader(handle), start=2):
                        for name in names:
                            raw = row.get(name)
                            if raw in (None, ""):
                                continue
                            try:
                                json.loads(raw)
                                continue  # already canonical, not a legacy cell
                            except json.JSONDecodeError:
                                pass
                            try:
                                decode_legacy_list_cell(
                                    raw, artifact=filename, column=name,
                                    row=row_number, declared_version=version,
                                )
                                decoded[filename] += 1
                            except ArtifactStructureError as exc:
                                failures.append(f"{run.name}/{filename}:{row_number} {exc}")

        self.assertTrue(
            found_any,
            "no tracked Artifact 1.3 legacy corpus was read; the fixtures are "
            "committed test resources and their absence is a defect, not a skip",
        )
        self.assertEqual(failures, [])
        self.assertEqual(decoded["errors.csv"], 15)
        self.assertEqual(decoded["recoveries.csv"], 6)


class StrictCsvFiveStateTests(unittest.TestCase):
    """Empty, JSON null, zero, false, and a value stay five distinct outcomes."""

    CONTRACT = TableContract(
        name="five_state",
        columns=(
            ColumnSpec("blank", cell_type=CellType.JSON),
            ColumnSpec("json_null", cell_type=CellType.JSON),
            ColumnSpec("zero", cell_type=CellType.INTEGER),
            ColumnSpec("untrue", cell_type=CellType.BOOLEAN),
            ColumnSpec("value"),
        ),
    )

    def test_five_states_do_not_collapse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(
                Path(directory), "t.csv",
                "blank,json_null,zero,untrue,value",
                ",null,0,False,text",
            )
            row = read_rows(path, "t.csv", self.CONTRACT)[0]
        self.assertIsNone(row["blank"])
        self.assertIs(row["json_null"], JSON_NULL)
        self.assertEqual(row["zero"], 0)
        self.assertIs(row["untrue"], False)
        self.assertEqual(row["value"], "text")


class StrictCsvRefusalTests(unittest.TestCase):
    CONTRACT = TableContract(
        name="t",
        columns=(ColumnSpec("a"), ColumnSpec("b", cell_type=CellType.INTEGER)),
    )

    def _expect(self, code, header, *rows):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(Path(directory), "t.csv", header, *rows)
            with self.assertRaises(ArtifactStructureError) as caught:
                read_rows(path, "t.csv", self.CONTRACT)
        self.assertEqual(caught.exception.code, code)

    def test_duplicate_header_is_rejected(self):
        self._expect(StructuralErrorCode.CSV_DUPLICATE_HEADER, "a,b,a", "1,2,3")

    def test_unknown_column_is_rejected(self):
        self._expect(StructuralErrorCode.CSV_UNKNOWN_COLUMN, "a,b,c", "1,2,3")

    def test_missing_required_column_is_rejected(self):
        self._expect(StructuralErrorCode.CSV_MISSING_REQUIRED_COLUMN, "a", "1")

    def test_ragged_row_is_rejected(self):
        self._expect(StructuralErrorCode.CSV_RAGGED_ROW, "a,b", "1,2,3")

    def test_non_integer_cell_is_rejected(self):
        self._expect(StructuralErrorCode.CSV_CELL_TYPE_INVALID, "a,b", "x,seven")

    def test_quoted_newline_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.csv"
            path.write_text('a,b\n"line1\nline2",7\n', encoding="utf-8", newline="")
            row = read_rows(path, "t.csv", self.CONTRACT)[0]
        self.assertEqual(row["a"], "line1\nline2")


class CompatibilityClassificationTests(unittest.TestCase):
    def test_five_states_are_all_reachable(self):
        cases = {
            "1.5.0": CompatibilityState.SUPPORTED,
            "1.4.0": CompatibilityState.SUPPORTED,
            "1.3.0": CompatibilityState.SUPPORTED,
            "1.2.0": CompatibilityState.UNSUPPORTED,
            None: CompatibilityState.UNDECLARED,
            "": CompatibilityState.UNDECLARED,
            "not-a-version": CompatibilityState.CORRUPT,
            "9.0.0": CompatibilityState.FUTURE_UNSUPPORTED,
        }
        for declared, expected in cases.items():
            with self.subTest(declared=declared):
                self.assertEqual(classify_artifact_schema(declared).state, expected)

    def test_missing_version_never_silently_weakens(self):
        verdict = classify_artifact_schema(None)
        self.assertFalse(verdict.readable)
        self.assertIn("absent", verdict.reason)


class ImportEdgeIndependenceTests(unittest.TestCase):
    """Plan section 3.2. The independent validator must stay independent.

    Enforced mechanically by importing each side in a subprocess and inspecting
    ``sys.modules``, which catches transitive edges that a source-text scan for
    ``import`` statements would miss.
    """

    FORBIDDEN_IN_VALIDATOR = (
        "modules.diagnostics",
        "modules.cli.explain_command",
        "modules.cli.report_command",
        "modules.cli.compare_command",
        "modules.run_view",
    )

    def _imported_modules(self, module_name):
        script = (
            "import json, sys\n"
            f"import {module_name}\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPOSITORY), capture_output=True, text=True, check=True,
        )
        return set(json.loads(result.stdout))

    def test_validator_does_not_import_production_reader_or_diagnostics(self):
        loaded = self._imported_modules("validation.scripts.validate_outputs")
        leaked = sorted(name for name in self.FORBIDDEN_IN_VALIDATOR if name in loaded)
        self.assertEqual(
            leaked, [],
            "validation.scripts.validate_outputs must not import production "
            "reader or diagnostic modules (plan section 3.2)",
        )

    def test_validator_does_not_reuse_the_shared_metric_derivers(self):
        """Sharing `derive_*` between aggregation and the ledger is fine.

        Both are production: one computes the aggregate, the other persists the
        per-file components it was computed from, and sharing is exactly what
        stops them drifting. The validator is different — it must recompute the
        Metric Contract independently, or it would be comparing a value to
        itself and could never disagree.
        """
        source = (
            REPOSITORY / "validation" / "scripts" / "validate_outputs.py"
        ).read_text(encoding="utf-8")
        for name in (
            "derive_lines_of_code", "derive_classes_structs", "derive_methods_functions",
        ):
            with self.subTest(function=name):
                self.assertNotIn(name, source)

    def test_validator_imports_nothing_from_the_production_package(self):
        loaded = self._imported_modules("validation.scripts.validate_outputs")
        leaked = sorted(
            name for name in loaded
            if name == "modules" or name.startswith("modules.")
        )
        self.assertEqual(
            leaked, [],
            "the independent validator must not import production measurement code",
        )

    def test_structural_layer_imports_neither_side(self):
        loaded = self._imported_modules("validation.artifact_io")
        leaked = sorted(
            name for name in loaded
            if name.startswith("modules") or name.startswith("validation.scripts")
        )
        self.assertEqual(
            leaked, [],
            "validation.artifact_io is shared structural code and must depend on "
            "neither production modules nor the validator",
        )


class SchemaResourceTests(unittest.TestCase):
    """Packaged schemas load through importlib.resources and are Draft 2020-12."""

    def test_every_registered_schema_loads(self):
        from validation.artifact_io import schema_store

        for name in schema_store.schema_names():
            with self.subTest(schema=name):
                document = schema_store.load_schema(name)
                self.assertEqual(document["$schema"], schema_store.SCHEMA_DIALECT)
                self.assertTrue(document["$id"].startswith(schema_store.SCHEMA_BASE_URI))

    def test_every_schema_passes_check_schema(self):
        from validation.artifact_io import schema_store

        self.assertEqual([str(problem) for problem in schema_store.check_all_schemas()], [])

    def test_no_schema_uses_the_unenforced_format_keyword(self):
        from validation.artifact_io import schema_store

        self.assertEqual(list(schema_store.iter_format_keywords()), [])

    def test_jsonschema_pin_agrees_with_pyproject(self):
        from validation.artifact_io import schema_store

        text = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
        pin = f'"jsonschema=={schema_store.REQUIRED_JSONSCHEMA_VERSION}"'
        self.assertIn(
            pin, text,
            "schema_store.REQUIRED_JSONSCHEMA_VERSION must equal the pyproject pin",
        )

    def test_registry_covers_every_shipped_schema_file(self):
        from validation.artifact_io import schema_store

        shipped = {
            path.name
            for path in (REPOSITORY / "validation" / "resources" / "schemas").glob("*.json")
        }
        registered = {
            schema_store.schema_filename(name) for name in schema_store.schema_names()
        }
        self.assertEqual(shipped - registered, set(), "unregistered schema file shipped")
        self.assertEqual(registered - shipped, set(), "registered schema file missing")


class PreservedFixtureSchemaTests(unittest.TestCase):
    """Preserved historical artifacts validate against the contract they declare.

    Never against the newest one. Judging a defective old artifact against a
    newer, more permissive schema launders the defect into a false
    `schema_valid`; judging a sound old artifact against a newer, stricter
    schema reports a false `invalid`. Those are the same mistake in opposite
    directions, and `schema_store.schema_name_for` is the single place that
    prevents both — so these tests route through it rather than naming a schema
    directly, which is what lets them detect a mapping regression at all.
    """

    #: Generations preserved in this tree that must stay readable under their
    #: own declared contract. Both predate the Artifact 1.7 subject model.
    PRESERVED_GENERATIONS = ("1.3.0", "1.4.0")

    @staticmethod
    def _find_run(declared="1.4.0"):
        """The tracked fixture for `declared`. Raises when it is missing."""
        return historical_fixtures.run_for(declared)

    def _assert_not_judged_against_the_native_contract(self, resolved, declared):
        """No pre-1.7 artifact may be judged against a 1.7.0 contract.

        This is the assertion that fails if `HISTORICAL_SCHEMA_NAMES` loses an
        entry: the fallthrough silently returns the current schema, and the
        1.7 documents require `subject_key`, `source_mode` and
        `subject_key_basis`, none of which existed in these generations.
        """
        from validation.artifact_io import schema_store
        from validation.artifact_io.compatibility import NATIVE_ARTIFACT_SCHEMA

        native = ".".join(str(part) for part in NATIVE_ARTIFACT_SCHEMA)
        self.assertNotEqual(
            schema_store.schema_version(resolved), native,
            f"artifact declaring {declared} was routed to {resolved!r}, the "
            f"native {native} contract, which postdates it",
        )

    def test_preserved_run_validates(self):
        from validation.artifact_io import schema_store
        from validation.artifact_io.strict_json import load_document

        for declared in self.PRESERVED_GENERATIONS:
            run = self._find_run(declared)

            for logical, filename, root in (
                ("run_manifest", "run_manifest.json", dict),
                ("run_status", "run_status.json", dict),
                ("environment", "environment.json", dict),
                ("analysis", "analysis.json", list),
            ):
                with self.subTest(declared=declared, artifact=filename):
                    resolved = schema_store.schema_name_for(logical, declared)
                    self._assert_not_judged_against_the_native_contract(resolved, declared)
                    document = load_document(run / filename, filename, expect=root)
                    violations = schema_store.validate_document(resolved, document, filename)
                    self.assertEqual([str(item) for item in violations], [])

    def test_preserved_inventories_and_repository_documents_validate(self):
        from validation.artifact_io import schema_store
        from validation.artifact_io.strict_json import load_document

        for declared in self.PRESERVED_GENERATIONS:
            run = self._find_run(declared)

            for family, logical in (
                ("file_inventory", "file_inventory"),
                ("repositories", "repository_document"),
            ):
                resolved = schema_store.schema_name_for(logical, declared)
                self._assert_not_judged_against_the_native_contract(resolved, declared)
                for path in sorted((run / family).glob("*.json")):
                    with self.subTest(declared=declared, artifact=f"{family}/{path.name}"):
                        document = load_document(path, path.name)
                        violations = schema_store.validate_document(
                            resolved, document, path.name
                        )
                        self.assertEqual([str(item) for item in violations], [])

    def test_every_supported_historical_generation_has_a_schema_mapping(self):
        """Absence from the table is not "no mapping needed" — it is a defect.

        `schema_name_for` falls through to the current schema for any unmapped
        version. That fallthrough is correct only for the native generation, so
        every supported non-native generation must be named explicitly. 1.3 and
        1.4 were missing, which is how preserved fixtures came to be validated
        against Artifact 1.7.
        """
        from validation.artifact_io import schema_store
        from validation.artifact_io.compatibility import (
            NATIVE_ARTIFACT_SCHEMA,
            SUPPORTED_ARTIFACT_SCHEMAS,
        )

        native = NATIVE_ARTIFACT_SCHEMA[:2]
        unmapped = sorted(
            key
            for key in SUPPORTED_ARTIFACT_SCHEMAS
            if key != native and key not in schema_store.HISTORICAL_SCHEMA_NAMES
        )
        self.assertEqual(
            unmapped, [],
            "supported artifact schemas with no historical mapping would be "
            "validated against the current contract",
        )

    def test_no_historical_generation_maps_to_a_native_contract(self):
        """A historical mapping that resolves to the native contract defeats itself."""
        from validation.artifact_io import schema_store
        from validation.artifact_io.compatibility import NATIVE_ARTIFACT_SCHEMA

        native = ".".join(str(part) for part in NATIVE_ARTIFACT_SCHEMA)
        offenders = sorted(
            f"{major}.{minor} -> {logical}={resolved}"
            for (major, minor), mapping in schema_store.HISTORICAL_SCHEMA_NAMES.items()
            for logical, resolved in mapping.items()
            if schema_store.schema_version(resolved) == native
        )
        self.assertEqual(offenders, [])


class ConformanceCorpusTests(unittest.TestCase):
    """The corpus must load offline and every reviewed case must hold."""

    def test_manifest_declares_the_current_contract_versions(self):
        from validation.conformance import load_manifest

        manifest = load_manifest()
        self.assertEqual(manifest.metric_contract_version, "3.0.0")
        self.assertEqual(manifest.exclusion_policy_version, "1.5.0")
        self.assertEqual(manifest.inventory_schema_version, "1.6.0")

    def test_every_case_records_complete_oracle_metadata(self):
        from validation.conformance import load_manifest

        required = (
            "id", "purpose", "metric_contract_clause", "input_path", "input_sha256",
            "observed_metric_paths", "reviewer", "review_date", "review_status",
        )
        for case in load_manifest().cases:
            with self.subTest(case=case.get("id")):
                for field_name in required:
                    self.assertIn(field_name, case)
                self.assertIn(case["review_status"], {"reviewed", "reviewed_with_notes"})

    def test_case_inputs_match_their_recorded_hashes(self):
        import hashlib
        from importlib import resources

        from validation.conformance import load_manifest

        root = resources.files("validation.conformance").joinpath("data", "cases")
        for case in load_manifest().cases:
            with self.subTest(case=case["id"]):
                payload = root.joinpath(case["input_path"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), case["input_sha256"])

    def test_no_case_fails(self):
        from validation.conformance import CaseOutcome, run_cases

        # A capability- or platform-skipped case is not a failure, but it is
        # also not a pass, so the two are counted separately here.
        failures = [
            f"{result.case_id}: {result.outcome.value} {result.message} {result.differences}"
            for result in run_cases()
            if result.outcome in {
                CaseOutcome.FAILED, CaseOutcome.ERROR, CaseOutcome.UNREVIEWED,
            }
        ]
        self.assertEqual(failures, [])

    def test_most_cases_actually_execute(self):
        """Guard against the corpus quietly skipping itself into vacuity."""
        from validation.conformance import CaseOutcome, run_cases

        results = run_cases()
        executed = [item for item in results if item.outcome is CaseOutcome.PASSED]
        self.assertGreater(len(executed), len(results) * 0.9)


class SchemaOnlyValidationTests(unittest.TestCase):
    def test_schema_only_report_states_semantic_validity_was_not_assessed(self):
        from modules.cli.validate_command import schema_only_report

        run = PreservedFixtureSchemaTests._find_run()
        report = schema_only_report(run)
        self.assertFalse(report["semantic_validity_assessed"])
        self.assertIn("NOT assessed", report["semantic_validity_note"])
        self.assertTrue(report["passed"], report["violations"])
        self.assertGreater(report["document_count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
