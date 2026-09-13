"""``metrolith validate --schema-only`` (plan section 7.6).

Structural validation checks shape, types, enums, version, lifecycle variant,
bounds, and encoding. It does **not** check cross-artifact equality, count and
status reconciliation, policy agreement, normalized-input invariants,
contribution sums, the semantic hash, or timing consistency.

The report says so in a dedicated field rather than leaving a reader to infer
that a structural pass means the run is valid. A schema pass is not semantic
validity (plan section 3.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Documents checked when present, with the JSON root type each one declares.
# `analysis.json` is an array of repository results; the rest are objects. A
# missing optional projection degrades to an explicit warning rather than an
# exception (plan section 4.3).
SINGLE_DOCUMENTS = (
    ("run_manifest", "run_manifest.json", True, dict),
    ("run_status", "run_status.json", True, dict),
    ("environment", "environment.json", True, dict),
    ("analysis", "analysis.json", True, list),
)


def schema_only_report(run_directory: Path) -> dict[str, Any]:
    """Structurally validate one run directory and report explicitly."""
    from validation.artifact_io import known_exceptions, schema_store
    from validation.artifact_io.compatibility import (
        check_inventory_pairing,
        classify_artifact_schema,
    )
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.strict_json import load_document

    run = Path(run_directory)
    violations: list[dict[str, Any]] = []
    warnings: list[str] = []
    checked: list[str] = []

    # Documents are collected before any of them is validated, because the
    # schema each one must be judged against depends on the Artifact Schema the
    # manifest declares — and the manifest is itself one of the documents.
    #
    # This matters. Literal schema validity has to be evaluated against the
    # contract the artifact *declares*, not against whatever is newest. A 1.5.0
    # artifact carrying a known 1.5 defect satisfies the relaxed 1.6 schema
    # byte-for-byte, so validating it against 1.6 would report `schema_valid`
    # and quietly erase the fact that it violates its own published contract.
    collected: list[tuple[str, Any, str]] = []
    manifest: dict[str, Any] | None = None

    for schema_name, filename, mandatory, root_type in SINGLE_DOCUMENTS:
        path = run / filename
        if not path.exists():
            if mandatory:
                violations.append({
                    "code": "artifact_missing",
                    "artifact": filename,
                    "message": "mandatory artifact is absent",
                })
            else:
                warnings.append(f"optional projection {filename} is absent")
            continue
        try:
            document = load_document(path, filename, expect=root_type)
        except ArtifactStructureError as exc:
            violations.append(exc.as_dict())
            continue
        if filename == "run_manifest.json":
            manifest = document
        collected.append((schema_name, document, filename))
        checked.append(filename)

    declared = (manifest or {}).get("artifact_schema_version")
    verdict = classify_artifact_schema(declared)
    pairing = check_inventory_pairing(verdict, (manifest or {}).get("inventory_schema_version"))
    if pairing:
        warnings.append(pairing)

    for family, schema_name in (("file_inventory", "file_inventory"),
                                ("repositories", "repository_document")):
        directory = run / family
        if not directory.is_dir():
            warnings.append(f"optional projection directory {family}/ is absent")
            continue
        for path in sorted(directory.glob("*.json")):
            relative = f"{family}/{path.name}"
            try:
                document = load_document(path, relative)
            except ArtifactStructureError as exc:
                violations.append(exc.as_dict())
                continue
            collected.append((schema_name, document, relative))
            checked.append(relative)

    pending: list[tuple[str, Any, list[Any]]] = []
    satisfies_current_schema = True
    for logical, document, relative in collected:
        declared_name = schema_store.schema_name_for(logical, declared)
        found = schema_store.validate_document(declared_name, document, relative)
        if found:
            pending.append((logical, document, found))
        # Whether today's schema would also accept these bytes is a *separate*
        # question from validity under the declared contract, and is reported as
        # its own field rather than being allowed to redefine the first.
        #
        # It is emphatically not "can the reader open this": the strict reader
        # reads every supported generation. A 1.5 artifact predates the Artifact
        # 1.7 subject model, so it lacks `subject_key` and `source_mode` and does
        # not satisfy the current schema — while remaining perfectly valid under
        # the 1.5 contract it declares.
        if satisfies_current_schema and schema_store.validate_document(
            logical, document, relative
        ):
            satisfies_current_schema = False

    # Partition buffered violations against the one shared exception registry
    # that finalization also consults, so the two commands can never reach
    # opposite verdicts about the same artifact.
    accepted: list[dict[str, Any]] = []
    for schema_name, document, errors in pending:
        real, waived = known_exceptions.partition(
            schema_name, errors, document, declared_artifact_schema=declared
        )
        violations.extend(error.as_dict() for error in real)
        accepted.extend(item.as_dict() for item in waived)

    # Three outcomes, never two. Reporting a waived historical artifact as
    # "schema valid" would launder a known schema defect into a guarantee, so
    # acceptance is a distinct result even though it shares a success exit code.
    if violations:
        result = "invalid"
    elif accepted:
        result = "accepted_under_known_1_5_compatibility_exception"
    else:
        result = "schema_valid"

    return {
        "passed": not violations,
        "result": result,
        # Which contract literal validity was judged against. Always the version
        # the artifact declares.
        "schema_contract_evaluated": declared,
        # A separate compatibility fact, deliberately not folded into `result`:
        # whether the *current* schema would also accept these bytes. False for
        # an older generation is expected, not a defect.
        "satisfies_current_schema": satisfies_current_schema,
        "result_meanings": {
            "schema_valid": "every document satisfied its schema literally",
            "accepted_under_known_1_5_compatibility_exception": (
                "no literal violation remains once documented defects in the "
                "frozen Artifact Schema 1.5.0 are excepted; this is NOT literal "
                "schema validity"
            ),
            "invalid": "at least one violation is not covered by any exception",
        },
        # A waived document is NOT schema-valid. It is accepted under a
        # documented, exactly-scoped exception against a frozen schema that is
        # itself defective, and it is reported separately so no reader can
        # mistake acceptance for conformance.
        "accepted_compatibility_exceptions": accepted,
        "accepted_compatibility_exception_count": len(accepted),
        "compatibility_exception_note": (
            "Entries under accepted_compatibility_exceptions are NOT schema-valid. "
            "They are accepted under known defects in the frozen Artifact Schema "
            "1.5.0, each identified by issue id. See "
            "validation/artifact_io/known_exceptions.py."
        ) if accepted else None,
        "mode": "schema_only",
        "semantic_validity_assessed": False,
        "semantic_validity_note": (
            "Semantic validity was NOT assessed. This report covers structural "
            "conformance only: shape, types, enums, declared version, lifecycle "
            "variant, bounds, and encoding. Cross-artifact equality, count and "
            "status reconciliation, policy agreement, normalized-input invariants, "
            "contribution sums, the semantic hash, and timing consistency were not "
            "checked. Run `metrolith validate` without --schema-only for those."
        ),
        "run_directory": str(run),
        "artifact_schema_compatibility": verdict.as_dict(),
        "documents_checked": sorted(checked),
        "document_count": len(checked),
        "violation_count": len(violations),
        "violations": violations,
        "warnings": warnings,
    }


def render_text(report: dict[str, Any], *, details: bool = False) -> str:
    """Render schema-only and semantic reports with the same human vocabulary."""

    passed = bool(report.get("passed"))
    lines = [
        f"Metrolith validate: {'valid' if passed else 'invalid'}",
        f"Run: {report.get('run_id') or 'not supplied'}",
        f"Status: {report.get('run_status') or report.get('result') or 'not supplied'}",
    ]
    violations = report.get("violations") or []
    failures = report.get("failures") or []
    warnings = report.get("warnings") or []
    if violations:
        lines.append(f"Violations ({len(violations)}):")
        for item in violations:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('code') or 'invalid'}: {item.get('message') or item}"
                )
            else:
                lines.append(f"- {item}")
    if failures:
        lines.append(f"Failures ({len(failures)}):")
        lines.extend(f"- {item}" for item in failures)
    if warnings:
        lines.append(f"Warnings ({len(warnings)}):")
        lines.extend(f"- {item}" for item in warnings)
    if passed and not warnings:
        lines.append("No validation violation or warning was found.")
    if details:
        documents = report.get("documents_checked") or []
        if documents:
            lines.append(f"Documents checked ({len(documents)}):")
            lines.extend(f"- {item}" for item in documents)
        if report.get("semantic_validity_note"):
            lines.append(str(report["semantic_validity_note"]))
        if report.get("semantic_sha256"):
            lines.append(f"Semantic SHA-256: {report['semantic_sha256']}")
    lines.append(
        "Next: metrolith report RUN" if passed else "Next: metrolith explain RUN"
    )
    return "\n".join(lines)
