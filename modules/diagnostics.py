"""Factual diagnostic projection over a finalized run (plan section 10).

**This module copies. It does not derive.** Every status it reports was emitted
by the canonical benchmark pipeline and is read straight off the authoritative
result. It never recomputes source inclusion, detected language, parser outcome,
metric values, per-metric status, repository status,
``expected_language_family_status``, or ``partial_origin`` (plan section 3.1).

That restriction is what makes disagreement detectable. The independent
validator in ``validation/scripts/validate_outputs.py`` recomputes
``expected_language_family_status`` and ``partial_origin`` from scratch and
compares them to the emitted values. If this projection recomputed them too, the
validator would end up comparing a value to itself and would agree no matter what
either side did.

**Import direction is load-bearing.** This module must never import
``validation.scripts.validate_outputs``, and that module must never import this
one. A mechanical import-edge test enforces both directions.

Wording follows plan section 3.5: recorded status, recorded evidence, metric
unavailable, metric effect unknown, not evaluable because evidence is missing.
Never "harmless", never "safe to ignore", never a causal claim, never a
research-impact or architecture-quality judgement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from modules.vocabularies import (
    KNOWN_ANALYSIS_STATUSES,
    KNOWN_GIT_MODE_STATES,
    KNOWN_METRIC_STATUSES,
    KNOWN_PARTIAL_ORIGINS,
)


class EvidenceScope(str, Enum):
    """What an item of evidence is about (plan section 10.2).

    A file path is meaningful only at ``FILE`` scope. Acquisition, cleanup,
    traversal, Git-mode-map, and run-integrity evidence has no file path, and
    inventing one would be a fabricated location.
    """

    FILE = "file"
    REPOSITORY = "repository"
    RUN = "run"


class MetricEffect(str, Enum):
    """Whether the effect of an observation on metrics is known."""

    RECORDED = "recorded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ExplanationCompleteness(str, Enum):
    COMPLETE = "complete"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# The vocabularies this module checks against live in `modules.vocabularies`,
# imported at the top of the file. They were previously four literal sets
# restated here and kept in agreement with their producers by hand, and
# `KNOWN_GIT_MODE_STATES` had already drifted: it omitted the
# `unavailable_not_git` that `modules/inventory.py` emits, so a healthy analysis
# of a non-Git directory was reported as carrying an unknown category.
#
# A value outside these sets is still surfaced as an unknown category rather
# than silently normalized away — an unrecognized status is exactly the case a
# reader most needs to see. What changed is only where the sets come from.

# Repository-scoped evidence modules that never carry a file path.
PATHLESS_MODULES = frozenset({
    "acquisition", "cleanup", "traversal", "git_mode_map", "run_integrity", "worktree",
})

METRIC_STATUS_FIELDS = (
    "inventory_status",
    "source_files_status",
    "loc_status",
    "classes_structs_status",
    "methods_functions_status",
)


@dataclass(frozen=True)
class DiagnosticEvidence:
    """One recorded observation, located as precisely as the evidence allows."""

    scope: EvidenceScope
    category: str
    detail: str
    metric_effect: MetricEffect
    source_artifact: str
    relative_path: str | None = None
    module: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_scope": self.scope.value,
            "category": self.category,
            "detail": self.detail,
            "metric_effect": self.metric_effect.value,
            "source_artifact": self.source_artifact,
            "relative_path": self.relative_path,
            "module": self.module,
        }


@dataclass(frozen=True)
class RepositoryDiagnostic:
    """Recorded diagnostic facts for one repository. Every field is copied."""

    repository_url: str
    analysis_status: str | None
    core_metric_status: str | None
    metric_statuses: Mapping[str, str | None]
    expected_language: str | None
    expected_language_family_status: str | None
    expected_language_mismatch: bool | None
    partial_origin: str | None
    git_mode_map_state: str | None
    parser_compatibility_strategies: tuple[str, ...]
    recorded_error_count: int
    recorded_recovery_count: int
    evidence: tuple[DiagnosticEvidence, ...]
    unknown_categories: tuple[str, ...]
    explanation_completeness: ExplanationCompleteness
    evaluable_dimensions: Mapping[str, bool]
    #: Complexity Contract 1.0.0 state, kept SEPARATE from the metric statuses.
    #: `absent` means the run predates complexity entirely, which is a different
    #: fact from a measurement that failed, and neither is a zero.
    complexity_state: str = "absent"
    complexity_contract_version: str | None = None
    complexity_unavailable_reason: str | None = None
    #: Metrolith Cognitive Complexity. `absent` for every pre-1.10 run, which is
    #: a different fact from `failed` and from a measured zero.
    cognitive_state: str = "absent"
    cognitive_metric_name: str = "Metrolith Cognitive Complexity"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_url": self.repository_url,
            "recorded_status": self.analysis_status,
            "core_metric_status": self.core_metric_status,
            "metric_statuses": dict(self.metric_statuses),
            "expected_language": self.expected_language,
            "expected_language_family_status": self.expected_language_family_status,
            "expected_language_mismatch": self.expected_language_mismatch,
            "partial_origin": self.partial_origin,
            "git_mode_map_state": self.git_mode_map_state,
            "parser_compatibility_strategies": list(self.parser_compatibility_strategies),
            "recorded_error_count": self.recorded_error_count,
            "recorded_recovery_count": self.recorded_recovery_count,
            "evidence": [item.as_dict() for item in self.evidence],
            "unknown_categories": list(self.unknown_categories),
            "explanation_completeness": self.explanation_completeness.value,
            "evaluable_dimensions": dict(self.evaluable_dimensions),
            "complexity_state": self.complexity_state,
            "complexity_contract_version": self.complexity_contract_version,
            "complexity_unavailable_reason": self.complexity_unavailable_reason,
            "cognitive_state": self.cognitive_state,
            "cognitive_metric_name": self.cognitive_metric_name,
        }


@dataclass(frozen=True)
class RunDiagnosticProjection:
    """Diagnostics for a whole run, plus the run-scoped evidence."""

    run_id: str | None
    lifecycle: str
    run_integrity_status: str | None
    artifact_schema_compatibility: Mapping[str, Any]
    repositories: tuple[RepositoryDiagnostic, ...]
    run_evidence: tuple[DiagnosticEvidence, ...]
    provenance_warnings: tuple[str, ...]
    evaluable_dimensions: Mapping[str, bool] = field(default_factory=dict)

    def repository(self, canonical_url: str) -> RepositoryDiagnostic | None:
        return next(
            (item for item in self.repositories if item.repository_url == canonical_url),
            None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "lifecycle": self.lifecycle,
            "artifact_schema_compatibility": dict(self.artifact_schema_compatibility),
            "evaluable_dimensions": dict(self.evaluable_dimensions),
            "provenance_warnings": list(self.provenance_warnings),
            "run_evidence": [item.as_dict() for item in self.run_evidence],
            "repositories": [item.as_dict() for item in self.repositories],
        }


def _metric_effect(module: str | None, aggregate: Mapping[str, Any]) -> MetricEffect:
    """Classify the recorded effect. ``UNKNOWN`` is a legitimate answer.

    Plan section 3.7 forbids substituting a guessed metric effect for a missing
    one, so an observation whose effect the artifact does not record stays
    ``unknown`` rather than being optimistically called ``recorded``.
    """
    statuses = {aggregate.get(name) for name in METRIC_STATUS_FIELDS}
    if "failed" in statuses:
        return MetricEffect.UNAVAILABLE
    if "partial" in statuses:
        return MetricEffect.RECORDED
    if module in PATHLESS_MODULES:
        return MetricEffect.UNKNOWN
    return MetricEffect.RECORDED


def _evidence_for_error(
    entry: Mapping[str, Any], aggregate: Mapping[str, Any], artifact: str
) -> DiagnosticEvidence:
    module = entry.get("module")
    path = entry.get("file_path") or entry.get("path") or entry.get("dirty_path")
    # A pathless module never gets a synthesized path (plan section 10.2).
    if module in PATHLESS_MODULES:
        path = None
    scope = EvidenceScope.FILE if path else EvidenceScope.REPOSITORY
    category = str(
        entry.get("error_category") or entry.get("category") or entry.get("error_type")
        or "uncategorized"
    )
    return DiagnosticEvidence(
        scope=scope,
        category=category,
        detail=str(entry.get("message") or entry.get("detail") or category),
        metric_effect=_metric_effect(module, aggregate),
        source_artifact=artifact,
        relative_path=str(path) if path else None,
        module=str(module) if module else None,
    )


def _collect_unknown(values: Mapping[str, tuple[Any, frozenset[str]]]) -> tuple[str, ...]:
    unknown = [
        f"{name}={value!r}"
        for name, (value, allowed) in values.items()
        if value is not None and str(value) not in allowed
    ]
    return tuple(sorted(unknown))


def project_repository(
    result: Mapping[str, Any], *, has_inventory: bool, has_contribution_ledger: bool
) -> RepositoryDiagnostic:
    """Project one authoritative repository result. Copies only."""
    metrics = result.get("metrics") or {}
    aggregate = metrics.get("aggregate") or {}
    errors = [item for item in (result.get("errors") or []) if isinstance(item, dict)]
    recoveries = [
        item
        for item in (metrics.get("recovered_parser_diagnostics") or [])
        if isinstance(item, dict)
    ]

    evidence: list[DiagnosticEvidence] = [
        _evidence_for_error(entry, aggregate, "analysis.json") for entry in errors
    ]
    for entry in recoveries:
        path = entry.get("file_path")
        evidence.append(DiagnosticEvidence(
            scope=EvidenceScope.FILE if path else EvidenceScope.REPOSITORY,
            category=str(entry.get("category") or entry.get("strategy") or "parser_recovery"),
            detail=str(entry.get("message") or entry.get("strategy") or "recorded recovery"),
            # A recovery that the pipeline recorded did have a recorded effect.
            metric_effect=MetricEffect.RECORDED,
            source_artifact="analysis.json",
            relative_path=str(path) if path else None,
            module="core_metrics",
        ))

    strategies = tuple(sorted({
        str(entry["strategy"])
        for entry in recoveries
        if entry.get("strategy")
    } | {
        str(entry["parser_compatibility_strategy"])
        for entry in recoveries
        if entry.get("parser_compatibility_strategy")
    }))

    unknown = _collect_unknown({
        "analysis_status": (result.get("analysis_status"), KNOWN_ANALYSIS_STATUSES),
        "partial_origin": (result.get("partial_origin"), KNOWN_PARTIAL_ORIGINS),
        "expected_language_family_status": (
            result.get("expected_language_family_status"), KNOWN_METRIC_STATUSES
        ),
        "git_mode_map_status": (result.get("git_mode_map_status"), KNOWN_GIT_MODE_STATES),
    })

    status = result.get("analysis_status")
    completeness = (
        ExplanationCompleteness.COMPLETE
        if status == "complete" or evidence
        else ExplanationCompleteness.INSUFFICIENT_EVIDENCE
    )

    from modules import complexity_view

    complexity_block = complexity_view.repository_block(result)
    complexity_state = complexity_view.state_of(result)

    return RepositoryDiagnostic(
        repository_url=str(result.get("repository_url") or ""),
        analysis_status=status,
        core_metric_status=result.get("core_metric_status"),
        metric_statuses={name: aggregate.get(name) for name in METRIC_STATUS_FIELDS},
        expected_language=result.get("expected_language"),
        expected_language_family_status=result.get("expected_language_family_status"),
        expected_language_mismatch=metrics.get("expected_language_mismatch"),
        partial_origin=result.get("partial_origin"),
        git_mode_map_state=result.get("git_mode_map_status"),
        parser_compatibility_strategies=strategies,
        recorded_error_count=len(errors),
        recorded_recovery_count=len(recoveries),
        evidence=tuple(evidence),
        unknown_categories=unknown,
        explanation_completeness=completeness,
        evaluable_dimensions={
            "aggregate_metrics": True,
            "inventory": has_inventory,
            "exact_metric_reconciliation": has_contribution_ledger,
            "expected_language_family": (
                result.get("expected_language_family_status") is not None
            ),
            "partial_origin": result.get("partial_origin") is not None,
            # False for every pre-1.9 run, and that is the point: the dimension
            # is unavailable rather than measured-and-empty.
            "complexity": complexity_state in complexity_view.EVALUABLE_STATES,
            # Its own dimension, not folded into `complexity`: a 1.9 run has an
            # evaluable structural measurement and NO cognitive one, so one
            # flag cannot answer for both.
            "cognitive_complexity": (
                complexity_view.cognitive_state_of(result)
                in complexity_view.COGNITIVE_EVALUABLE_STATES
            ),
        },
        complexity_state=complexity_state,
        cognitive_state=complexity_view.cognitive_state_of(result),
        cognitive_metric_name=complexity_view.COGNITIVE_METRIC_NAME,
        complexity_contract_version=(complexity_block or {}).get(
            "complexity_contract_version"
        ),
        complexity_unavailable_reason=(complexity_block or {}).get(
            "unavailable_reason"
        ),
    )


def project_run(view: Any) -> RunDiagnosticProjection:
    """Project a whole :class:`ImmutableRunView` into recorded diagnostic facts.

    The view populates ``warnings`` and ``structural_errors`` lazily, as the
    optional dimensions are read. Reading them before forcing those dimensions
    silently dropped every missing-projection warning, so the same run directory
    projected differently depending on which properties had already been
    touched. Materializing first makes this a function of the directory.
    """
    materialize = getattr(view, "materialize_diagnostics", None)
    if callable(materialize):
        materialize()

    run_evidence: list[DiagnosticEvidence] = []

    for problem in view.structural_errors:
        run_evidence.append(DiagnosticEvidence(
            scope=EvidenceScope.RUN,
            category=problem.code.value,
            detail=problem.message,
            metric_effect=MetricEffect.UNKNOWN,
            source_artifact=problem.artifact,
        ))
    for warning in view.warnings:
        run_evidence.append(DiagnosticEvidence(
            scope=EvidenceScope.RUN,
            category="optional_projection_absent",
            detail=warning.message,
            metric_effect=MetricEffect.UNKNOWN,
            source_artifact=warning.artifact,
        ))
    for recovery in view.legacy_recoveries:
        run_evidence.append(DiagnosticEvidence(
            scope=EvidenceScope.RUN,
            category="legacy_python_repr_list_cell",
            detail=(
                f"artifact schema {recovery.declared_artifact_schema_version} predates "
                f"JSON list cells; column {recovery.column!r} recovered"
            ),
            metric_effect=MetricEffect.RECORDED,
            source_artifact=recovery.artifact,
        ))

    provenance: list[str] = []
    environment = view.environment
    if (
        environment.get("profiler_git_commit_sha") is None
        and environment.get("profiler_provenance_kind") != "installed_distribution"
    ):
        provenance.append(
            "the Metrolith evaluator Git revision is unavailable, so exact "
            "Git-worktree reproduction is not evaluable"
        )
    if environment.get("profiler_git_dirty") is True:
        provenance.append("the Metrolith evaluator working tree was dirty at run time")

    has_inventory = view.has_inventory
    has_ledger = view.has_contribution_ledger

    repositories = tuple(
        project_repository(
            result, has_inventory=has_inventory, has_contribution_ledger=has_ledger
        )
        for result in view.repositories
    )

    return RunDiagnosticProjection(
        run_id=view.run_id,
        lifecycle=view.lifecycle.value,
        # Copied, not derived. Lifecycle says whether the artifacts decode;
        # this says whether the run reports that it succeeded. Reporting only
        # the first let a run that Metrolith itself recorded as `failed` be
        # presented as a good run.
        run_integrity_status=view.integrity_status,
        artifact_schema_compatibility=view.compatibility.as_dict(),
        repositories=repositories,
        run_evidence=tuple(run_evidence),
        provenance_warnings=tuple(provenance),
        evaluable_dimensions={
            "inventory": has_inventory,
            "normalized_input_population": view.normalized_input is not None,
            "exact_metric_reconciliation": has_ledger,
            "finalized_verdicts": view.finalized,
        },
    )
