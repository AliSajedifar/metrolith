"""Machine-readable differential-validation records and the disagreement taxonomy.

These records are **study output, not measurement output**. They are deliberately
kept out of the Artifact Schema 1.7 family and out of the production schema
registry: forcing experimental validation results into the measurement contract
would make an unvalidated research artifact look authoritative, and would tie an
evolving study to a frozen contract. They carry their own independent format
version and their own schema under ``validation/differential/schema/``.

A record is written for every comparison, including agreements and including
comparisons that could not be made. "We did not validate this" has to be visible
in the data rather than inferred from an absent row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

DIFFERENTIAL_RECORD_FORMAT_VERSION = "1.0.0"

SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schema" / "differential_record-1.0.schema.json"
)

TRACK_A = "A_scope_controlled_metric"
TRACK_B = "B_independent_source_selection"

# -- agreement -------------------------------------------------------------

AGREEMENT_EXACT = "exact_agreement"
AGREEMENT_DISAGREEMENT = "disagreement"

#: The reference could not be *executed*: a toolchain is missing, a file could
#: not be read, the reference crashed. A capability statement about this run.
AGREEMENT_NOT_EVALUABLE = "not_evaluable"

#: The reference ran fine, but the two definitions cannot be mapped onto each
#: other for this metric and language, so a numeric comparison would be
#: meaningless. A statement about the *definitions*, not about capability.
#: Kept distinct from `not_evaluable` because conflating "we could not run it"
#: with "running it would prove nothing" hides which gap is closeable by
#: provisioning.
AGREEMENT_NOT_COMPARABLE = "not_comparable_definition"

AGREEMENT_STATUSES = (
    AGREEMENT_EXACT, AGREEMENT_DISAGREEMENT, AGREEMENT_NOT_EVALUABLE,
    AGREEMENT_NOT_COMPARABLE,
)

# -- disagreement classification -------------------------------------------
#
# Typed causes. `unresolved` is a real, permitted terminal state: an honest
# "we do not know yet" beats a comfortable misclassification.

CAUSE_DEFINITION_MISMATCH = "metric_definition_mismatch"
CAUSE_SELECTION_MISMATCH = "source_selection_mismatch"
CAUSE_PARSER_LIMITATION = "parser_limitation"
CAUSE_ARCHLENS_DEFECT = "archlens_defect"
CAUSE_REFERENCE_DEFECT = "reference_tool_defect"
CAUSE_UNSUPPORTED_CONSTRUCT = "unsupported_construct"
CAUSE_ADAPTER_DEFECT = "adapter_defect"
CAUSE_UNRESOLVED = "unresolved"
CAUSE_NONE = "not_applicable"

DISAGREEMENT_CAUSES = (
    CAUSE_DEFINITION_MISMATCH,
    CAUSE_SELECTION_MISMATCH,
    CAUSE_PARSER_LIMITATION,
    CAUSE_ARCHLENS_DEFECT,
    CAUSE_REFERENCE_DEFECT,
    CAUSE_UNSUPPORTED_CONSTRUCT,
    CAUSE_ADAPTER_DEFECT,
    CAUSE_UNRESOLVED,
    CAUSE_NONE,
)

# -- adjudication ----------------------------------------------------------
#
# Recorded separately from the automated result. With one reviewer available,
# manual inspection adjudicates sampled disagreements; it is never the oracle.

ADJUDICATION_NOT_REQUIRED = "not_required"
ADJUDICATION_PENDING = "pending"
ADJUDICATION_MANUAL_SINGLE_REVIEWER = "manually_adjudicated_single_reviewer"
ADJUDICATION_AUTOMATED = "automatically_classified"

ADJUDICATION_STATUSES = (
    ADJUDICATION_NOT_REQUIRED,
    ADJUDICATION_PENDING,
    ADJUDICATION_MANUAL_SINGLE_REVIEWER,
    ADJUDICATION_AUTOMATED,
)

CORPUS_LAYERS = ("layer1_synthetic", "layer2_selected_real", "layer3_benchmark_sample")


@dataclass
class DifferentialRecord:
    """One comparison between ArchLens and one independent reference."""

    subject_key: str
    analyzed_revision: str | None
    analysis_scope_hash: str | None
    language: str
    track: str
    corpus_layer: str
    metric: str
    definition_mapping_id: str
    archlens_version: str
    metric_contract_version: str
    exclusion_policy_version: str
    reference_name: str
    reference_version: str | None
    archlens_result: Any
    reference_result: Any
    difference: Any
    agreement_status: str
    disagreement_cause: str
    evidence_paths: list[str] = field(default_factory=list)
    adjudication_status: str = ADJUDICATION_NOT_REQUIRED
    adjudication_note: str | None = None
    not_evaluable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.agreement_status not in AGREEMENT_STATUSES:
            raise ValueError(f"unknown agreement status {self.agreement_status!r}")
        if self.disagreement_cause not in DISAGREEMENT_CAUSES:
            raise ValueError(f"unknown disagreement cause {self.disagreement_cause!r}")
        if self.adjudication_status not in ADJUDICATION_STATUSES:
            raise ValueError(
                f"unknown adjudication status {self.adjudication_status!r}"
            )
        if self.track not in (TRACK_A, TRACK_B):
            raise ValueError(f"unknown track {self.track!r}")
        if self.corpus_layer not in CORPUS_LAYERS:
            raise ValueError(f"unknown corpus layer {self.corpus_layer!r}")
        # A disagreement must always carry a cause, even if that cause is
        # `unresolved`. Silence would let an unexplained difference look
        # explained.
        if (
            self.agreement_status == AGREEMENT_DISAGREEMENT
            and self.disagreement_cause == CAUSE_NONE
        ):
            raise ValueError(
                "a disagreement must record a cause; use 'unresolved' when it "
                "is genuinely not yet known"
            )
        if (
            self.agreement_status in (AGREEMENT_NOT_EVALUABLE, AGREEMENT_NOT_COMPARABLE)
            and not self.not_evaluable_reason
        ):
            raise ValueError(
                f"a {self.agreement_status} record must state why"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "differential_record_format_version": DIFFERENTIAL_RECORD_FORMAT_VERSION,
            "subject_key": self.subject_key,
            "analyzed_revision": self.analyzed_revision,
            "analysis_scope_hash": self.analysis_scope_hash,
            "language": self.language,
            "track": self.track,
            "corpus_layer": self.corpus_layer,
            "metric": self.metric,
            "definition_mapping_id": self.definition_mapping_id,
            "archlens_version": self.archlens_version,
            "metric_contract_version": self.metric_contract_version,
            "exclusion_policy_version": self.exclusion_policy_version,
            "reference_name": self.reference_name,
            "reference_version": self.reference_version,
            "archlens_result": self.archlens_result,
            "reference_result": self.reference_result,
            "difference": self.difference,
            "agreement_status": self.agreement_status,
            "disagreement_cause": self.disagreement_cause,
            "evidence_paths": sorted(self.evidence_paths),
            "adjudication_status": self.adjudication_status,
            "adjudication_note": self.adjudication_note,
            "not_evaluable_reason": self.not_evaluable_reason,
        }


def summarize(records: Sequence[DifferentialRecord]) -> dict[str, Any]:
    """Aggregate counts. Never a pass/fail verdict.

    Success for this study is not "100% agreement"; it is that every
    disagreement is localized and classified. So the summary reports the
    classification distribution and, separately, how many remain unresolved.
    """
    by_status: dict[str, int] = {}
    by_cause: dict[str, int] = {}
    for record in records:
        by_status[record.agreement_status] = by_status.get(record.agreement_status, 0) + 1
        if record.agreement_status == AGREEMENT_DISAGREEMENT:
            by_cause[record.disagreement_cause] = (
                by_cause.get(record.disagreement_cause, 0) + 1
            )
    return {
        "record_count": len(records),
        "by_agreement_status": dict(sorted(by_status.items())),
        "disagreements_by_cause": dict(sorted(by_cause.items())),
        "unresolved_disagreements": by_cause.get(CAUSE_UNRESOLVED, 0),
        "all_disagreements_classified": by_cause.get(CAUSE_UNRESOLVED, 0) == 0,
        "note": (
            "Agreement rate is not the success criterion. A classified "
            "disagreement is a successful outcome; an unclassified one is not."
        ),
    }


def write_records(
    records: Iterable[DifferentialRecord], destination: Path, *,
    study_metadata: dict[str, Any] | None = None,
) -> Path:
    """Write one study document. Deterministic bytes, sorted keys."""
    materialized = list(records)
    payload = {
        "differential_record_format_version": DIFFERENTIAL_RECORD_FORMAT_VERSION,
        "study_metadata": study_metadata or {},
        "summary": summarize(materialized),
        "records": [record.as_dict() for record in materialized],
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    return destination


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_document(document: Any) -> list[str]:
    """Validate a study document against the study's own schema.

    Uses the same pinned ``jsonschema`` as the production layer but a schema
    kept deliberately outside the production registry.
    """
    from jsonschema import validators

    validator = validators.Draft202012Validator(load_schema())
    return sorted(
        f"{'/' + '/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in validator.iter_errors(document)
    )
