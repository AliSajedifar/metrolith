#!/usr/bin/env python3
"""Derive Draft 2020-12 row schemas for the wide ArchLens CSV artifacts.

Rationale. ``errors.csv`` alone has 79 columns and ``catalog.csv`` has 57.
Hand-typing them invites transcription errors and, worse, invented types. This
generator instead reads every preserved Artifact 1.3 and 1.4 run and records the
value forms each column actually takes, so the emitted schema is evidence-backed.

What the emitted schemas describe. They describe a **typed row** as produced by
``validation.artifact_io.strict_csv``, not the raw text on disk. An empty cell
becomes JSON ``null``; a literal ``null`` cell in a JSON-typed column becomes the
``JSON_NULL`` sentinel, which serializes back to ``null``. Both are therefore
modelled as a nullable type, and the CSV layer, not the schema, is what keeps
"unavailable" and "present and null" distinct.

Columns whose observed values were empty in every preserved run cannot have a
type inferred. They are emitted as nullable strings and listed under
``x-archlens-unverified-columns`` so a reviewer can see exactly which fields rest
on no evidence. Nothing is guessed silently.

This is a build-time authoring aid, not runtime code. Re-run it and diff the
output when new evidence becomes available.

Usage::

    python -m validation.scripts.derive_tabular_schemas            # write schemas
    python -m validation.scripts.derive_tabular_schemas --report   # evidence only
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_DIRECTORY = PROJECT_ROOT / "validation" / "resources" / "schemas"
SCHEMA_BASE_URI = "https://archlens.dev/schemas/"

# Preserved runs used as evidence, newest schema first. Both supported artifact
# generations are included so a column that only appears in one is still typed.
EVIDENCE_RUN_GLOBS = (
    "tests/fixtures/historical/*",
)

# The emitted schemas describe Artifact Schema 1.5 rows. Only runs at Artifact
# 1.4 or newer are admitted as type evidence.
#
# WHY THIS FILTER EXISTS. Artifact 1.3 and earlier serialize list-valued cells
# with Python ``repr`` (single quotes), which is not valid JSON:
#
#     artifact <= 1.3.0   ['classes_structs', 'methods_functions']
#     artifact >= 1.4.0   ["classes_structs", "methods_functions"]
#
# Verified across every preserved run: 135 Python-repr cells at artifact 1.2/1.3
# and 0 at 1.4, against 27 JSON cells at 1.4 and 0 below it, with the same clean
# split for recoveries.csv affected_metrics, fallback_strategies, and
# selected_fallback_strategies. Mixing both generations as evidence would infer
# "string" for columns that are genuinely JSON in the native format.
MINIMUM_EVIDENCE_ARTIFACT_SCHEMA = (1, 4)

# Columns known to be affected by the pre-1.4 Python-repr serialization. The
# reviewed Artifact 1.3 adapter must decide how to expose these; see the
# Phase 1 findings note.
PRE_1_4_NON_JSON_LIST_COLUMNS = {
    "errors.csv": ("affected_metrics",),
    "recoveries.csv": (
        "affected_metrics",
        "fallback_strategies",
        "selected_fallback_strategies",
    ),
}

# artifact file -> (schema name, title, key columns)
TABULAR_ARTIFACTS = {
    "catalog.csv": ("catalog_row", "ArchLens catalog row 1.5", ("repository_url",)),
    "sheet_metrics.csv": ("sheet_metrics_row", "ArchLens sheet metrics row 1.5", ("repository_url",)),
    "language_metrics.csv": ("language_metrics_row", "ArchLens language metrics row 1.5", ("repository_url", "language")),
    "errors.csv": ("errors_row", "ArchLens error row 1.5", ("repository_url", "error_category")),
    "recoveries.csv": ("recoveries_row", "ArchLens recovery row 1.5", ("repository_url", "file_path")),
}

# Columns whose emptiness is a property of the contract rather than an accident
# of the sampled corpus.
#
# `infer_type` decides nullability with `counter["empty"] > 0`, so it can only
# observe what the preserved runs happened to contain. A genuinely optional
# field that every sampled run populated is derived as non-nullable and then
# rejects the first run that omits it — which is exactly how `expected_language`
# came to be declared `nullable: false` while `run_artifacts._catalog_row`
# writes `""` for it. Optionality of an input-supplied field is a contract
# decision, so it is declared here instead of inferred.
#
# This widens the contract only. A column listed here still keeps its derived
# type and stays in `required`: the *column* must be present in the header, the
# *cell* may be empty, and an empty cell decodes to None meaning "unavailable" —
# never to zero and never to the empty string.
DECLARED_OPTIONAL_COLUMNS: dict[str, frozenset[str]] = {
    # `expected_language` is optional benchmark input. A cohort that requires it
    # must enforce that in cohort validation, not in the generic artifact
    # contract.
    "catalog.csv": frozenset({"expected_language"}),
    "sheet_metrics.csv": frozenset({"expected_language"}),
}

TRUE_LITERALS = {"True", "TRUE", "true"}
FALSE_LITERALS = {"False", "FALSE", "false"}


def _is_integer(text: str) -> bool:
    candidate = text[1:] if text[:1] in "+-" else text
    return bool(candidate) and candidate.isascii() and candidate.isdigit()


def _is_number(text: str) -> bool:
    if text.strip().lower() in {"nan", "inf", "-inf", "infinity", "-infinity"}:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _is_json_structure(text: str) -> bool:
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[") or stripped == "null"):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def _declared_artifact_schema(run: Path) -> tuple[int, ...] | None:
    try:
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    declared = manifest.get("artifact_schema_version")
    if not isinstance(declared, str):
        return None
    try:
        return tuple(int(part) for part in declared.split("."))
    except ValueError:
        return None


def discover_runs(root: Path, *, minimum_schema: tuple[int, ...] | None = None) -> list[Path]:
    """Return preserved run directories, optionally filtered by artifact schema."""
    runs: list[Path] = []
    for pattern in EVIDENCE_RUN_GLOBS:
        for candidate in sorted(root.glob(pattern)):
            if not (candidate / "run_manifest.json").is_file():
                continue
            if minimum_schema is not None:
                declared = _declared_artifact_schema(candidate)
                if declared is None or declared[:2] < minimum_schema:
                    continue
            runs.append(candidate)
    return runs


def collect_evidence(root: Path) -> dict[str, dict[str, dict]]:
    """Return ``artifact -> column -> observation counters``."""
    evidence: dict[str, dict[str, dict]] = {
        name: defaultdict(lambda: {
            "seen": 0, "empty": 0, "boolean": 0, "integer": 0,
            "number": 0, "json": 0, "other": 0, "samples": [],
        })
        for name in TABULAR_ARTIFACTS
    }
    for run in discover_runs(root, minimum_schema=MINIMUM_EVIDENCE_ARTIFACT_SCHEMA):
        for artifact in TABULAR_ARTIFACTS:
            path = run / artifact
            if not path.is_file():
                continue
            reader = csv.DictReader(
                io.StringIO(path.read_text(encoding="utf-8"), newline="")
            )
            for row in reader:
                for column, raw in row.items():
                    if column is None:
                        continue
                    counter = evidence[artifact][column]
                    counter["seen"] += 1
                    if raw is None or raw == "":
                        counter["empty"] += 1
                    elif raw in TRUE_LITERALS or raw in FALSE_LITERALS:
                        counter["boolean"] += 1
                    elif _is_json_structure(raw):
                        counter["json"] += 1
                    elif _is_integer(raw):
                        counter["integer"] += 1
                    elif _is_number(raw):
                        counter["number"] += 1
                    else:
                        counter["other"] += 1
                        if len(counter["samples"]) < 2:
                            counter["samples"].append(raw[:60])
    return evidence


def collect_column_history(root: Path) -> dict[str, dict[str, str]]:
    """Return ``artifact -> column -> earliest artifact schema that emitted it``.

    Column **presence** is read from every preserved run regardless of version,
    unlike type inference, which stays restricted to Artifact >= 1.4 by finding
    F-1. Presence carries no serialization ambiguity, and the older runs are the
    only evidence of when a column was introduced.

    This deliberately scans the whole tree rather than ``EVIDENCE_RUN_GLOBS``.
    Those globs reach no run older than Artifact 1.2, which would date every
    column to 1.2.0 and overstate its introduction version — making columns
    non-required for artifacts that in fact carry them, and weakening the check
    exactly where the compatibility matrix is supposed to be precise.

    Reading only the header keeps this cheap and avoids depending on whether any
    row happens to populate the column.
    """
    history: dict[str, dict[str, tuple[int, ...]]] = {name: {} for name in TABULAR_ARTIFACTS}
    for manifest in sorted(root.rglob("run_manifest.json")):
        run = manifest.parent
        declared = _declared_artifact_schema(run)
        if declared is None:
            continue
        for artifact in TABULAR_ARTIFACTS:
            path = run / artifact
            if not path.is_file():
                continue
            with path.open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle), [])
            for column in header:
                current = history[artifact].get(column)
                if current is None or declared < current:
                    history[artifact][column] = declared
    return {
        artifact: {
            column: ".".join(str(part) for part in version)
            for column, version in sorted(columns.items())
        }
        for artifact, columns in history.items()
    }


def infer_type(counter: dict) -> tuple[str, bool, bool]:
    """Return ``(json_type, nullable, evidenced)`` for one column."""
    nullable = counter["empty"] > 0
    populated = counter["seen"] - counter["empty"]
    if populated == 0:
        # No evidence at all. Nullable string, flagged for review.
        return "string", True, False
    if counter["other"]:
        return "string", nullable, True
    if counter["json"] and not (counter["boolean"] or counter["integer"] or counter["number"]):
        return "json", nullable, True
    if counter["json"]:
        # Mixed JSON and scalar spellings: treat as string to avoid asserting
        # a structure the artifact does not consistently carry.
        return "string", nullable, True
    if counter["boolean"] and not (counter["integer"] or counter["number"]):
        return "boolean", nullable, True
    if counter["number"]:
        return "number", nullable, True
    if counter["integer"]:
        return "integer", nullable, True
    return "string", nullable, True


def _property_for(json_type: str, nullable: bool) -> dict:
    # `x-archlens-cell-type` is the machine-readable contract annotation.
    # validation.artifact_io.contracts builds its TableContract from it, so the
    # column list exists in exactly one place instead of being retyped beside
    # the schema and drifting from it.
    if json_type == "json":
        # A JSON-valued cell may decode to any JSON type, including null.
        return {
            "description": "JSON-valued cell. Decodes through the strict CSV layer; "
                           "a top-level null is 'present and null', distinct from an empty cell.",
            "x-archlens-cell-type": "json",
            "x-archlens-nullable": nullable,
        }
    types = [json_type, "null"] if nullable else [json_type]
    payload: dict = {"type": types if len(types) > 1 else types[0]}
    if json_type in {"integer", "number"}:
        payload["description"] = "Empty cells decode to null and must not be read as zero."
    payload["x-archlens-cell-type"] = json_type
    payload["x-archlens-nullable"] = nullable
    return payload


def build_schema(
    artifact: str, columns: dict[str, dict], history: dict[str, str] | None = None
) -> dict:
    name, title, keys = TABULAR_ARTIFACTS[artifact]
    properties: dict[str, dict] = {}
    unverified: list[str] = []
    introduced = history or {}
    declared_optional = DECLARED_OPTIONAL_COLUMNS.get(artifact, frozenset())
    for column in columns:
        json_type, nullable, evidenced = infer_type(columns[column])
        # A declared-optional column is nullable whatever the corpus showed, so
        # regenerating from runs that all populate it cannot silently narrow the
        # contract back.
        if column in declared_optional:
            nullable = True
        payload = _property_for(json_type, nullable)
        # Recorded so a reader can tell a column that is genuinely absent from an
        # older artifact from one that is missing because the artifact is
        # damaged. Columns introduced after the artifact under inspection are
        # not required of it (plan section 7.2).
        since = introduced.get(column)
        if since:
            payload["x-archlens-since-artifact-schema"] = since
        properties[column] = payload
        if not evidenced:
            unverified.append(column)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_BASE_URI}{name}-1.5.schema.json",
        "title": title,
        "description": (
            f"One typed row of {artifact}, as produced by "
            "validation.artifact_io.strict_csv. Column types were derived from "
            "preserved Artifact 1.3 and 1.4 runs rather than assumed; see "
            "validation/scripts/derive_tabular_schemas.py. This artifact is a "
            "checked projection of authoritative repository results, not an "
            "independent measurement authority."
        ),
        "type": "object",
        "required": sorted(properties),
        "properties": dict(sorted(properties.items())),
        "additionalProperties": False,
        "x-archlens-key-columns": list(keys),
        "x-archlens-unverified-columns": sorted(unverified),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--out", type=Path, default=SCHEMA_DIRECTORY)
    parser.add_argument("--report", action="store_true", help="Print evidence and write nothing.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    runs = discover_runs(root, minimum_schema=MINIMUM_EVIDENCE_ARTIFACT_SCHEMA)
    skipped = len(discover_runs(root)) - len(runs)
    evidence = collect_evidence(root)
    history = collect_column_history(root)

    print(
        f"evidence runs: {len(runs)} at artifact schema "
        f">= {'.'.join(str(p) for p in MINIMUM_EVIDENCE_ARTIFACT_SCHEMA)} "
        f"({skipped} older runs excluded as type evidence)"
    )
    for artifact in TABULAR_ARTIFACTS:
        columns = evidence[artifact]
        if not columns:
            print(f"  {artifact:24} NO EVIDENCE FOUND")
            continue
        schema = build_schema(artifact, columns, history.get(artifact))
        unverified = schema["x-archlens-unverified-columns"]
        late = sum(
            1 for spec in schema["properties"].values()
            if spec.get("x-archlens-since-artifact-schema", "1.0.0") != "1.0.0"
        )
        print(
            f"  {artifact:24} columns={len(columns):3}  unverified={len(unverified)}"
            f"  introduced_after_1.0={late}"
        )
        if args.report and unverified:
            for column in unverified:
                print(f"      no populated sample: {column}")
        if not args.report:
            name, _title, _keys = TABULAR_ARTIFACTS[artifact]
            target = args.out / f"{name}-1.5.schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n",
            )
            print(f"      wrote {target.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
