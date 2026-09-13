"""Track A — scope-controlled metric differential validation.

Validates **metric computation** with source selection removed as a variable.

The protocol, in order:

1. ArchLens analyzes the subject and decides which files are included in metrics.
2. That exact file set is handed to an independent reference.
3. The reference computes comparable metrics with its own implementation.
4. Only metrics with an explicit definition mapping are compared.

Step 2 is what makes this Track A: selection cannot contribute to a
disagreement, so a difference is about the computation. Using ArchLens to supply
the file list is explicitly allowed; everything after that point is independent.

Per-file comparison is preferred over per-repository totals wherever the
reference supports it, because a total that happens to match can hide two
offsetting errors, and a total that differs localizes nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from validation.differential import definitions, records
from validation.differential.reference import (
    entity_drivers,
    java_entities,
    line_classifier,
)

#: Reference identifiers, recorded in every record so a result can be traced to
#: the exact mechanism that produced it.
REFERENCE_LINE_SCANNER = "archlens_differential.line_classifier"
REFERENCE_JAVAC = "jdk.compiler.tree_api"


def _archlens_included_files(run_directory: Path, subject_key: str) -> list[dict[str, Any]]:
    """The exact file set ArchLens measured, read from its own artifacts."""
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    view = open_run(run_directory)
    chosen = next(
        (
            item for item in view.repositories
            if subject_key_of(dict(item)) == subject_key
        ),
        None,
    )
    if chosen is None:
        raise KeyError(f"run {run_directory} records no subject {subject_key!r}")

    slug = f"{chosen.get('repository_owner')}__{chosen.get('repository_name')}"
    inventory = view.inventories.get(f"file_inventory/{slug}.json")
    if inventory is None:
        return []
    return [
        dict(record)
        for record in inventory.get("files", ())
        if record.get("included_in_metrics")
    ]


def _contributions_by_path(run_directory: Path, subject_key: str) -> dict[str, dict[str, Any]]:
    """ArchLens's own per-file metric components, for per-file comparison."""
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    view = open_run(run_directory)
    if not view.has_contribution_ledger:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for row in view.stream_contributions():
        if subject_key_of(dict(row)) != subject_key:
            continue
        path = row.get("relative_path")
        if path:
            found[str(path)] = dict(row)
    return found


def _numeric(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _base_fields(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_key": context["subject_key"],
        "analyzed_revision": context.get("analyzed_revision"),
        "analysis_scope_hash": context.get("analysis_scope_hash"),
        "corpus_layer": context["corpus_layer"],
        "archlens_version": context["archlens_version"],
        "metric_contract_version": context["metric_contract_version"],
        "exclusion_policy_version": context["exclusion_policy_version"],
        "track": records.TRACK_A,
    }


def _compare_number(
    archlens: int | None, reference: int | None,
) -> tuple[str, Any, str, str | None]:
    """Return (agreement_status, difference, cause, not_evaluable_reason)."""
    if archlens is None or reference is None:
        missing = "ArchLens" if archlens is None else "the reference"
        return (
            records.AGREEMENT_NOT_EVALUABLE, None, records.CAUSE_NONE,
            f"{missing} recorded no value; an absent value is never read as zero",
        )
    difference = reference - archlens
    if difference == 0:
        return records.AGREEMENT_EXACT, 0, records.CAUSE_NONE, None
    return (
        records.AGREEMENT_DISAGREEMENT, difference, records.CAUSE_UNRESOLVED, None,
    )


def run_loc(
    root: Path, run_directory: Path, context: dict[str, Any],
) -> list[records.DifferentialRecord]:
    """Compare lines_of_code per file, over the ArchLens-selected set."""
    subject_key = context["subject_key"]
    included = _archlens_included_files(run_directory, subject_key)
    contributions = _contributions_by_path(run_directory, subject_key)
    mapping = definitions.LOC_LINE_CLASSIFICATION
    base = _base_fields(context)
    found: list[records.DifferentialRecord] = []

    for record in included:
        relative = str(record["relative_path"])
        language = record.get("detected_language")
        if language not in line_classifier.SUPPORTED:
            found.append(records.DifferentialRecord(
                **base, language=str(language), metric="lines_of_code",
                definition_mapping_id=mapping.identifier,
                reference_name=REFERENCE_LINE_SCANNER, reference_version="1.0.0",
                archlens_result=None, reference_result=None, difference=None,
                agreement_status=records.AGREEMENT_NOT_EVALUABLE,
                disagreement_cause=records.CAUSE_NONE,
                evidence_paths=[relative],
                not_evaluable_reason=(
                    f"the reference scanner defines no lexical syntax for "
                    f"{language!r}"
                ),
            ))
            continue

        path = Path(root) / relative
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as exc:
            found.append(records.DifferentialRecord(
                **base, language=language, metric="lines_of_code",
                definition_mapping_id=mapping.identifier,
                reference_name=REFERENCE_LINE_SCANNER, reference_version="1.0.0",
                archlens_result=None, reference_result=None, difference=None,
                agreement_status=records.AGREEMENT_NOT_EVALUABLE,
                disagreement_cause=records.CAUSE_NONE,
                evidence_paths=[relative],
                not_evaluable_reason=(
                    f"the reference could not read the file as UTF-8: "
                    f"{type(exc).__name__}"
                ),
            ))
            continue

        reference_counts = line_classifier.classify(text, language)
        archlens_value = _numeric((contributions.get(relative) or {}).get("lines_of_code"))
        status, difference, cause, reason = _compare_number(
            archlens_value, reference_counts.code_lines
        )
        found.append(records.DifferentialRecord(
            **base, language=language, metric="lines_of_code",
            definition_mapping_id=mapping.identifier,
            reference_name=REFERENCE_LINE_SCANNER, reference_version="1.0.0",
            archlens_result=archlens_value,
            reference_result=reference_counts.code_lines,
            difference=difference, agreement_status=status,
            disagreement_cause=cause, evidence_paths=[relative],
            adjudication_status=(
                records.ADJUDICATION_PENDING
                if status == records.AGREEMENT_DISAGREEMENT
                else records.ADJUDICATION_NOT_REQUIRED
            ),
            not_evaluable_reason=reason,
        ))
    return found


def run_source_file_count(
    run_directory: Path, context: dict[str, Any],
) -> records.DifferentialRecord:
    """Adapter-drift check: do both sides agree on how many files they saw?"""
    subject_key = context["subject_key"]
    included = _archlens_included_files(run_directory, subject_key)
    mapping = definitions.SOURCE_FILE_COUNT
    reference_count = len(included)
    archlens_count = len(included)
    return records.DifferentialRecord(
        **_base_fields(context), language="all", metric="source_files",
        definition_mapping_id=mapping.identifier,
        reference_name=REFERENCE_LINE_SCANNER, reference_version="1.0.0",
        archlens_result=archlens_count, reference_result=reference_count,
        difference=0, agreement_status=records.AGREEMENT_EXACT,
        disagreement_cause=records.CAUSE_NONE,
        evidence_paths=[],
        adjudication_note=(
            "Track A fixes the file set by construction, so this is an "
            "accounting check, not independent evidence about selection. "
            "Track B validates selection."
        ),
    )


#: Per-language reference driver, the reference identifier recorded in every
#: record, and the fields each reference reports for the constructs the mapped
#: definition deliberately excludes.
_ENTITY_REFERENCES: dict[str, tuple[Any, str, tuple[str, ...], tuple[str, ...]]] = {
    "Java": (
        lambda paths: java_entities.count_entities(paths),
        REFERENCE_JAVAC,
        ("all_types", "interfaces", "enums", "annotation_types"),
        ("constructors", "abstract_or_interface_methods", "anonymous_class_methods"),
    ),
    "Python": (
        lambda paths: entity_drivers.python_entities(paths),
        "parso.grammar",
        (),
        ("constructors", "overload_declarations", "nested_functions"),
    ),
    "JavaScript": (
        lambda paths: entity_drivers.javascript_entities(paths),
        "typescript.compiler_api",
        ("interfaces", "type_aliases", "enums"),
        ("constructors", "accessors", "bodyless_declarations",
         "nested_functions", "arrow_or_expression_functions",
         "anonymous_functions", "anonymous_container_members"),
    ),
    "TypeScript": (
        lambda paths: entity_drivers.typescript_entities(paths),
        "typescript.compiler_api",
        ("interfaces", "type_aliases", "enums"),
        ("constructors", "accessors", "bodyless_declarations",
         "nested_functions", "arrow_or_expression_functions",
         "anonymous_functions", "anonymous_container_members"),
    ),
    "Go": (
        lambda paths: entity_drivers.go_entities(paths),
        "go.parser_ast",
        ("interfaces", "other_named_types"),
        ("function_literals", "bodyless_declarations"),
    ),
}

_ENTITY_METRICS = (
    ("classes_structs", "types"),
    ("methods_functions", "methods"),
)


def run_entities(
    root: Path, run_directory: Path, context: dict[str, Any],
) -> list[records.DifferentialRecord]:
    """Compare entity metrics against an independent parser, per language.

    One code path for every language. Each reference is a different parser in a
    different technology; only the driver and the excluded-construct field names
    differ.
    """
    subject_key = context["subject_key"]
    included = _archlens_included_files(run_directory, subject_key)
    contributions = _contributions_by_path(run_directory, subject_key)
    base = _base_fields(context)
    found: list[records.DifferentialRecord] = []

    by_language: dict[str, list[dict[str, Any]]] = {}
    for record in included:
        language = record.get("detected_language")
        if language in _ENTITY_REFERENCES:
            by_language.setdefault(language, []).append(record)

    for language, items in sorted(by_language.items()):
        driver, reference_name, type_evidence, method_evidence = (
            _ENTITY_REFERENCES[language]
        )
        paths = [Path(root) / str(item["relative_path"]) for item in items]

        try:
            payload = driver(paths)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            # A reference that cannot run produces an explicit capability
            # statement, never an absent row.
            for metric, _key in _ENTITY_METRICS:
                mapping = definitions.ENTITY_MAPPINGS[(metric, language)]
                found.append(records.DifferentialRecord(
                    **base, language=language, metric=metric,
                    definition_mapping_id=mapping.identifier,
                    reference_name=reference_name, reference_version=None,
                    archlens_result=None, reference_result=None, difference=None,
                    agreement_status=records.AGREEMENT_NOT_EVALUABLE,
                    disagreement_cause=records.CAUSE_NONE,
                    evidence_paths=sorted(
                        str(item["relative_path"]) for item in items
                    ),
                    not_evaluable_reason=f"{type(exc).__name__}: {exc}",
                ))
            continue

        by_path = {
            str(Path(entry["path"]).resolve()): entry
            for entry in payload["files"]
        }
        version = payload.get("reference_version")

        for item in items:
            relative = str(item["relative_path"])
            entry = by_path.get(str((Path(root) / relative).resolve()))
            contribution = contributions.get(relative) or {}

            for metric, reference_key in _ENTITY_METRICS:
                mapping = definitions.ENTITY_MAPPINGS[(metric, language)]
                incomparable = definitions.NOT_COMPARABLE_DEFINITIONS.get(
                    (metric, language)
                )
                if incomparable:
                    found.append(records.DifferentialRecord(
                        **base, language=language, metric=metric,
                        definition_mapping_id=mapping.identifier,
                        reference_name=reference_name, reference_version=version,
                        archlens_result=_numeric(contribution.get(metric)),
                        reference_result=None, difference=None,
                        agreement_status=records.AGREEMENT_NOT_COMPARABLE,
                        disagreement_cause=records.CAUSE_DEFINITION_MISMATCH,
                        evidence_paths=[relative],
                        not_evaluable_reason=incomparable,
                    ))
                    continue

                if entry is None or entry.get("error"):
                    found.append(records.DifferentialRecord(
                        **base, language=language, metric=metric,
                        definition_mapping_id=mapping.identifier,
                        reference_name=reference_name, reference_version=version,
                        archlens_result=_numeric(contribution.get(metric)),
                        reference_result=None, difference=None,
                        agreement_status=records.AGREEMENT_NOT_EVALUABLE,
                        disagreement_cause=records.CAUSE_NONE,
                        evidence_paths=[relative],
                        not_evaluable_reason=(
                            (entry or {}).get("error")
                            or "the reference produced no result for this file"
                        ),
                    ))
                    continue

                archlens_value = _numeric(contribution.get(metric))
                reference_value = entry.get(reference_key)
                reference_value = (
                    None if reference_value is None else int(reference_value)
                )
                status, difference, cause, reason = _compare_number(
                    archlens_value, reference_value
                )
                # The constructs both sides deliberately exclude, carried as
                # evidence so the size of each definitional exclusion is
                # visible rather than silently dropped.
                keys = type_evidence if metric == "classes_structs" else method_evidence
                excluded = {key: entry.get(key) for key in keys if key in entry}
                note = (
                    "constructs excluded by the mapped definition on both "
                    "sides: "
                    + (
                        ", ".join(
                            f"{key}={value}" for key, value in sorted(excluded.items())
                        )
                        or "none reported"
                    )
                )
                found.append(records.DifferentialRecord(
                    **base, language=language, metric=metric,
                    definition_mapping_id=mapping.identifier,
                    reference_name=reference_name, reference_version=version,
                    archlens_result=archlens_value,
                    reference_result=reference_value,
                    difference=difference, agreement_status=status,
                    disagreement_cause=cause, evidence_paths=[relative],
                    adjudication_status=(
                        records.ADJUDICATION_PENDING
                        if status == records.AGREEMENT_DISAGREEMENT
                        else records.ADJUDICATION_NOT_REQUIRED
                    ),
                    adjudication_note=note,
                    not_evaluable_reason=reason,
                ))
    return found


#: Retained name for the Java-only path, now a thin alias over `run_entities`.
def run_java_entities(
    root: Path, run_directory: Path, context: dict[str, Any],
) -> list[records.DifferentialRecord]:
    return [
        record for record in run_entities(root, run_directory, context)
        if record.language == "Java"
    ]


def unavailable_reference_records(
    languages: Iterable[str], context: dict[str, Any],
) -> list[records.DifferentialRecord]:
    """Explicit records for pairs with no reference, or no mappable definition.

    "We did not validate this" must be visible in the data. An absent row would
    read as an untested nothing. The two reasons are kept apart: a missing
    toolchain is a capability gap that provisioning can close, while a
    definition gap cannot be closed by provisioning at all.
    """
    base = _base_fields(context)
    found: list[records.DifferentialRecord] = []
    for language in sorted(set(languages)):
        for metric in ("classes_structs", "methods_functions"):
            unavailable = definitions.UNAVAILABLE_REFERENCES.get((metric, language))
            incomparable = definitions.NOT_COMPARABLE_DEFINITIONS.get(
                (metric, language)
            )
            if unavailable is None and incomparable is None:
                continue
            mapping = definitions.ENTITY_MAPPINGS.get((metric, language))
            found.append(records.DifferentialRecord(
                **base, language=language, metric=metric,
                definition_mapping_id=(
                    mapping.identifier if mapping
                    else "none.no_independent_reference.v1"
                ),
                reference_name="none", reference_version=None,
                archlens_result=None, reference_result=None, difference=None,
                agreement_status=(
                    records.AGREEMENT_NOT_COMPARABLE if incomparable
                    else records.AGREEMENT_NOT_EVALUABLE
                ),
                disagreement_cause=(
                    records.CAUSE_DEFINITION_MISMATCH if incomparable
                    else records.CAUSE_NONE
                ),
                evidence_paths=[],
                not_evaluable_reason=incomparable or unavailable,
            ))
    return found
