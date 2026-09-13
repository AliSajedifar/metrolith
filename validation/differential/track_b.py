"""Track B — independent source-selection validation.

Validates **which files ArchLens decided to measure**, which is the largest
hidden lever in any cross-tool comparison: two tools measuring "the same"
repository usually disagree first about what counts, not about how to count it.

The ArchLens file list is deliberately **not** given to the reference. The
reference walks the tree and derives inclusion itself. That is the difference
between Track B and Track A, and collapsing the two would leave source selection
untested — which is why Track A must never be allowed to stand in for this.

Every disagreement is reported against a concrete path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from validation.differential import definitions, records
from validation.differential.reference import selection

REFERENCE_SELECTION = "archlens_differential.selection"
REFERENCE_SELECTION_VERSION = "1.0.0"


def _archlens_selection(run_directory: Path, subject_key: str) -> dict[str, dict[str, Any]]:
    """Every inventoried file and what ArchLens decided about it."""
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
        return {}
    return {
        str(record["relative_path"]): dict(record)
        for record in inventory.get("files", ())
    }


def compare_selection(root: Path, run_directory: Path, subject_key: str) -> dict[str, Any]:
    """Independently derive inclusion and diff it against ArchLens's decision."""
    archlens = _archlens_selection(run_directory, subject_key)
    reference = {item.relative_path: item for item in selection.select(Path(root))}

    archlens_included = {
        path for path, record in archlens.items() if record.get("included_in_metrics")
    }
    reference_included = selection.included_paths(reference.values())

    both = sorted(archlens_included & reference_included)
    archlens_only = sorted(archlens_included - reference_included)
    reference_only = sorted(reference_included - archlens_included)

    language_disagreements = []
    for path in both:
        archlens_language = archlens[path].get("detected_language")
        reference_language = reference[path].language
        if archlens_language != reference_language:
            language_disagreements.append({
                "relative_path": path,
                "archlens_language": archlens_language,
                "reference_language": reference_language,
            })

    classification_disagreements = []
    for path in sorted(set(archlens) & set(reference)):
        archlens_reason = archlens[path].get("exclusion_reason")
        reference_reason = reference[path].exclusion_reason
        # Only report where both sides excluded but disagreed about *why*.
        # A pure include/exclude difference is already reported above, and
        # reporting it twice would inflate the disagreement count.
        if (
            archlens_reason and reference_reason
            and archlens_reason != reference_reason
        ):
            classification_disagreements.append({
                "relative_path": path,
                "archlens_exclusion_reason": archlens_reason,
                "reference_exclusion_reason": reference_reason,
            })

    return {
        "archlens_included_count": len(archlens_included),
        "reference_included_count": len(reference_included),
        "agreed_included": both,
        "archlens_only_inclusions": archlens_only,
        "reference_only_inclusions": reference_only,
        "language_disagreements": language_disagreements,
        "classification_disagreements": classification_disagreements,
        "exact_agreement": not (archlens_only or reference_only),
    }


def _classify_selection_difference(archlens_only, reference_only) -> str:
    """Typed cause for a selection difference.

    Everything here is `source_selection_mismatch` by construction — that is
    what Track B measures — but the cause field stays explicit rather than
    implied, and adjudication may later reclassify a specific path as a defect
    on either side.
    """
    if archlens_only or reference_only:
        return records.CAUSE_SELECTION_MISMATCH
    return records.CAUSE_NONE


def run(root: Path, run_directory: Path, context: dict[str, Any]) -> list[records.DifferentialRecord]:
    """Produce Track B records for one subject."""
    subject_key = context["subject_key"]
    comparison = compare_selection(root, run_directory, subject_key)
    mapping = definitions.SOURCE_SELECTION
    base = {
        "subject_key": subject_key,
        "analyzed_revision": context.get("analyzed_revision"),
        "analysis_scope_hash": context.get("analysis_scope_hash"),
        "corpus_layer": context["corpus_layer"],
        "archlens_version": context["archlens_version"],
        "metric_contract_version": context["metric_contract_version"],
        "exclusion_policy_version": context["exclusion_policy_version"],
        "track": records.TRACK_B,
    }

    archlens_only = comparison["archlens_only_inclusions"]
    reference_only = comparison["reference_only_inclusions"]
    disagreeing_paths = sorted(set(archlens_only) | set(reference_only))
    exact = comparison["exact_agreement"]

    found = [records.DifferentialRecord(
        **base, language="all", metric="included_file_set",
        definition_mapping_id=mapping.identifier,
        reference_name=REFERENCE_SELECTION,
        reference_version=REFERENCE_SELECTION_VERSION,
        archlens_result=comparison["archlens_included_count"],
        reference_result=comparison["reference_included_count"],
        difference=(
            comparison["reference_included_count"]
            - comparison["archlens_included_count"]
        ),
        agreement_status=(
            records.AGREEMENT_EXACT if exact else records.AGREEMENT_DISAGREEMENT
        ),
        disagreement_cause=_classify_selection_difference(archlens_only, reference_only),
        evidence_paths=disagreeing_paths,
        adjudication_status=(
            records.ADJUDICATION_NOT_REQUIRED if exact
            else records.ADJUDICATION_PENDING
        ),
        adjudication_note=None if exact else (
            f"ArchLens-only: {archlens_only or 'none'}; "
            f"reference-only: {reference_only or 'none'}"
        ),
    )]

    for item in comparison["language_disagreements"]:
        found.append(records.DifferentialRecord(
            **base, language=str(item["archlens_language"]),
            metric="detected_language",
            definition_mapping_id=mapping.identifier,
            reference_name=REFERENCE_SELECTION,
            reference_version=REFERENCE_SELECTION_VERSION,
            archlens_result=item["archlens_language"],
            reference_result=item["reference_language"],
            difference="language_disagreement",
            agreement_status=records.AGREEMENT_DISAGREEMENT,
            disagreement_cause=records.CAUSE_UNRESOLVED,
            evidence_paths=[item["relative_path"]],
            adjudication_status=records.ADJUDICATION_PENDING,
        ))

    for item in comparison["classification_disagreements"]:
        found.append(records.DifferentialRecord(
            **base, language="all", metric="exclusion_classification",
            definition_mapping_id=mapping.identifier,
            reference_name=REFERENCE_SELECTION,
            reference_version=REFERENCE_SELECTION_VERSION,
            archlens_result=item["archlens_exclusion_reason"],
            reference_result=item["reference_exclusion_reason"],
            difference="classification_disagreement",
            agreement_status=records.AGREEMENT_DISAGREEMENT,
            # Both sides excluded the file, so measurement is unaffected; the
            # disagreement is about the recorded reason.
            disagreement_cause=records.CAUSE_DEFINITION_MISMATCH,
            evidence_paths=[item["relative_path"]],
            adjudication_status=records.ADJUDICATION_AUTOMATED,
            adjudication_note=(
                "both sides excluded this file, so no metric is affected; the "
                "reference's conventional classification is coarser than the "
                "exclusion policy's"
            ),
        ))
    return found
