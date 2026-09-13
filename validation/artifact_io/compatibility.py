"""Explicit artifact-version compatibility and run-lifecycle classification.

This module is the compatibility authority for every reader built on
``validation.artifact_io``: a missing, unparseable, or future version produces a
named state, never a quietly weakened check (plan section 7.2).

It does **not** yet replace the older ``schema_at_least`` helper in
``validation/scripts/validate_outputs.py``. That script still defines and uses
its own two-component comparison and does not import this package at all, so the
two coexist and can disagree about the same artifact. Migrating it is tracked
separately; until then, do not read this docstring as a claim that
``schema_at_least`` is gone.

It is semantics free: it classifies versions and lifecycle, and never derives a
metric, a repository status, or any measurement conclusion.

Lifecycle-variant discrimination follows the ratified decision D-1, which
diverges from plan section 4.2 on one point backed by observed 3.4.0 evidence:
finalized ``repositories/<slug>.json`` documents **do** retain
``checkpoint_status``, so that field must not be used as a discriminator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class CompatibilityState(str, Enum):
    """Result of classifying one declared artifact-schema version."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNDECLARED = "undeclared"
    CORRUPT = "corrupt"
    FUTURE_UNSUPPORTED = "future_unsupported"


class RunLifecycle(str, Enum):
    """Observable lifecycle state of a run directory (plan section 8.2)."""

    FINALIZED_VALID = "finalized_valid"
    FINALIZED_INVALID = "finalized_invalid"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    UNDECLARED_VERSION = "undeclared_version"
    UNSUPPORTED_VERSION = "unsupported_version"
    CORRUPT = "corrupt"


class RepositoryDocumentVariant(str, Enum):
    """Which ``oneOf`` branch a ``repositories/<slug>.json`` document satisfies."""

    CHECKPOINT = "checkpoint"
    FINAL_RESULT = "final_result"
    INDETERMINATE = "indeterminate"


# Artifact Schema 1.12.0 is the native target: it represents evaluator
# provenance honestly for both Git worktrees and installed distributions.
# 1.11.0 remains readable through an adapter and retains the former
# Git-worktree-only boolean dirty-state contract.
#
# Artifact Schema 1.11.0 introduced the conditional benchmark
# qualification authority, its manifest hash binding, the three-dimensional
# checked row projection, and the run-level benchmark-of-record readiness
# verdict. 1.10.0 added ArchLens Cognitive Complexity and remains fully readable
# through an adapter.
#
# 1.11 is a RUN-BUNDLE FORMAT boundary, not a metric-definition change. Metric
# Contract 3.0.0, Complexity Contract 2.0.0, Inventory Schema 1.7.0 and
# Exclusion Policy 1.5.0 are all unchanged, and `analysis.json` still declares
# and satisfies its unchanged 1.10 measurement schema. The version moves because
# the bundle gained a conditionally mandatory authoritative artifact and changed
# three row contracts -- calling that 1.10 would let two bundles share a version
# while having different mandatory artifacts.
NATIVE_ARTIFACT_SCHEMA = (1, 12, 0)

# Support decisions are keyed on (artifact major, artifact minor). Each entry
# records the inventory schema the reviewed fixtures actually carry, so a
# mismatched pairing is visible rather than assumed.
SUPPORTED_ARTIFACT_SCHEMAS: dict[tuple[int, int], dict[str, Any]] = {
    (1, 12): {
        "inventory_schema": "1.7.0",
        "support": "native",
        "note": (
            "honest evaluator provenance for Git worktrees and installed "
            "distributions; profiler Git dirty state remains unknown/not "
            "applicable instead of being coerced to clean. Measurement and "
            "qualification contracts are unchanged"
        ),
    },
    (1, 11): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "benchmark qualification as a separate authority in the same "
            "immutable run bundle: a conditionally mandatory "
            "benchmark_qualification.json, its manifest hash binding, "
            "qualification columns on the three checked row contracts, and a "
            "run-level benchmark-of-record readiness verdict. Measurement is "
            "UNCHANGED: Metric Contract stays 3.0.0, Complexity Contract stays "
            "2.0.0, Inventory Schema stays 1.7.0, and analysis.json keeps its "
            "1.10 measurement schema"
        ),
    },
    (1, 10): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "ArchLens Cognitive Complexity under Complexity Contract 2.0.0: one "
            "new callable column, and the cognitive measurement state per run "
            "and per repository. The callable POPULATION is unchanged, the "
            "contribution ledger is unchanged, and Inventory Schema stays "
            "1.7.0. Metric Contract stays 3.0.0"
        ),
    },
    (1, 9): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "per-callable complexity under Complexity Contract 1.0.0: a new "
            "callables artifact family, per-file structural/nloc status and "
            "callable_count on the contribution ledger, and a repository "
            "complexity summary. Inventory Schema is unchanged: no per-file "
            "complexity field belongs on a record describing files as found"
        ),
    },
    (1, 8): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "correction-only successor to 1.7.0: recoveries_row's "
            "analyzed_commit_sha is nullable, which a local snapshot with no "
            "commit requires and every sibling 1.7 row contract already "
            "allowed (RECOVERY-SHA)"
        ),
    },
    (1, 7): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "native target; subject identity and source model (subject_key, "
            "source_mode, analysis_scope_hash)"
        ),
    },
    (1, 6): {
        "inventory_schema": "1.7.0",
        "support": "adapter",
        "note": (
            "correction-only successor to 1.5.0 (F-P3-3..F-P3-6, F-P3-10); "
            "predates the subject identity model"
        ),
    },
    (1, 5): {
        "inventory_schema": "1.6.0",
        "support": "adapter",
        "note": (
            "ArchLens 3.5.0; readable, but four of its schemas contradict their "
            "own producers and its artifacts may need the centralized 1.5 "
            "compatibility exceptions"
        ),
    },
    (1, 4): {
        "inventory_schema": "1.6.0",
        "support": "adapter",
        "note": "ArchLens 3.4.0; supported through a reviewed adapter and complete fixture",
    },
    (1, 3): {
        "inventory_schema": "1.5.0",
        "support": "adapter",
        "note": "ArchLens 3.3.0; supported only while the preserved complete fixtures pass",
    },
}

# Explicitly refused, with the reason recorded rather than inferred.
UNSUPPORTED_ARTIFACT_SCHEMAS: dict[tuple[int, int], str] = {
    (1, 2): "ArchLens 3.2.0 artifacts predate the reviewed compatibility fixtures",
    (1, 1): "ArchLens 3.1.0 artifacts predate the reviewed compatibility fixtures",
    (1, 0): "ArchLens 3.0.0 artifacts predate the reviewed compatibility fixtures",
}


@dataclass(frozen=True)
class CompatibilityVerdict:
    """One classification outcome, always carrying its own evidence."""

    state: CompatibilityState
    declared: Any
    parsed: tuple[int, int, int] | None
    reason: str
    expected_inventory_schema: str | None = None

    @property
    def readable(self) -> bool:
        """Whether a reader may proceed to interpret the artifact's content."""
        return self.state is CompatibilityState.SUPPORTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "declared_version": self.declared,
            "parsed_version": ".".join(str(part) for part in self.parsed) if self.parsed else None,
            "reason": self.reason,
            "expected_inventory_schema_version": self.expected_inventory_schema,
        }


def parse_version(value: Any) -> tuple[int, int, int] | None:
    """Parse a strict ``MAJOR.MINOR.PATCH`` string. Returns ``None`` if unparseable."""
    if not isinstance(value, str):
        return None
    match = _VERSION_PATTERN.match(value.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def classify_artifact_schema(declared: Any) -> CompatibilityVerdict:
    """Classify a declared ``artifact_schema_version`` into one explicit state.

    Never silently downgrades. The five outcomes are exhaustive.
    """
    if declared is None:
        return CompatibilityVerdict(
            CompatibilityState.UNDECLARED, None, None,
            "artifact_schema_version is absent; the artifact predates versioned "
            "artifacts or has been stripped, and no compatibility guarantee applies",
        )
    if isinstance(declared, str) and declared.strip() == "":
        return CompatibilityVerdict(
            CompatibilityState.UNDECLARED, declared, None,
            "artifact_schema_version is empty",
        )

    parsed = parse_version(declared)
    if parsed is None:
        return CompatibilityVerdict(
            CompatibilityState.CORRUPT, declared, None,
            "artifact_schema_version is not a MAJOR.MINOR.PATCH string",
        )

    # Artifact Schema versions are MAJOR.MINOR compatibility versions, and the
    # patch component MUST be zero.
    #
    # This is a formalization of how the machinery already worked, not a new
    # restriction. Support is keyed on `(major, minor)` below, so `1.5.1` would
    # otherwise be classified exactly like `1.5.0` while its schemas, its
    # compatibility exceptions and its expected inventory pairing were never
    # reviewed. Silently treating an unreviewed version as a reviewed one is the
    # failure mode this refusal removes; resolving patch versions properly would
    # mean version-aware schema selection, which does not exist here and is
    # deliberately not being built.
    if parsed[2] != 0:
        return CompatibilityVerdict(
            CompatibilityState.UNSUPPORTED, declared, parsed,
            f"artifact_schema_version {declared} has a non-zero patch component; "
            f"Artifact Schema versions are MAJOR.MINOR compatibility versions and "
            f"the patch component must be 0",
        )

    key = (parsed[0], parsed[1])
    if key in SUPPORTED_ARTIFACT_SCHEMAS:
        entry = SUPPORTED_ARTIFACT_SCHEMAS[key]
        return CompatibilityVerdict(
            CompatibilityState.SUPPORTED, declared, parsed, entry["note"],
            expected_inventory_schema=entry["inventory_schema"],
        )
    if key in UNSUPPORTED_ARTIFACT_SCHEMAS:
        return CompatibilityVerdict(
            CompatibilityState.UNSUPPORTED, declared, parsed,
            UNSUPPORTED_ARTIFACT_SCHEMAS[key],
        )
    if parsed > NATIVE_ARTIFACT_SCHEMA:
        return CompatibilityVerdict(
            CompatibilityState.FUTURE_UNSUPPORTED, declared, parsed,
            f"artifact declares schema {declared}, newer than the native "
            f"{'.'.join(str(part) for part in NATIVE_ARTIFACT_SCHEMA)} this build understands",
        )
    return CompatibilityVerdict(
        CompatibilityState.UNSUPPORTED, declared, parsed,
        f"artifact schema {declared} is not in the supported set",
    )


def check_inventory_pairing(
    verdict: CompatibilityVerdict, declared_inventory: Any
) -> str | None:
    """Return a warning when the inventory version does not match its expected pairing.

    Returns ``None`` when the pairing is as reviewed. A mismatch is reported, not
    silently tolerated, but it does not by itself make the run unreadable.
    """
    if verdict.expected_inventory_schema is None:
        return None
    if declared_inventory is None:
        return (
            "inventory_schema_version is absent while artifact schema "
            f"{verdict.declared} expects {verdict.expected_inventory_schema}"
        )
    if str(declared_inventory) != verdict.expected_inventory_schema:
        return (
            f"inventory_schema_version {declared_inventory!r} does not match the "
            f"reviewed pairing {verdict.expected_inventory_schema!r} for artifact "
            f"schema {verdict.declared!r}"
        )
    return None


TERMINAL_RUN_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})
NONTERMINAL_RUN_STATUSES = frozenset({"running"})


def classify_lifecycle(
    run_status: dict[str, Any] | None,
    run_manifest: dict[str, Any] | None,
    compatibility: CompatibilityVerdict,
    *,
    structurally_valid: bool = True,
    interrupted_evidence: bool = False,
) -> RunLifecycle:
    """Classify a run directory's lifecycle from its own artifacts only.

    ``interrupted_evidence`` is set by the caller when a repository document
    carries ``checkpoint_status == "interrupted"``. ArchLens never writes an
    ``interrupted`` *run* status, so without that positive evidence a run whose
    status is still ``running`` is reported as ``running`` rather than guessed to
    be abandoned.
    """
    if compatibility.state is CompatibilityState.CORRUPT:
        return RunLifecycle.CORRUPT
    if compatibility.state is CompatibilityState.UNDECLARED:
        return RunLifecycle.UNDECLARED_VERSION
    if compatibility.state in (
        CompatibilityState.UNSUPPORTED, CompatibilityState.FUTURE_UNSUPPORTED
    ):
        return RunLifecycle.UNSUPPORTED_VERSION

    if run_status is None:
        return RunLifecycle.CORRUPT

    status = run_status.get("status")
    if status in NONTERMINAL_RUN_STATUSES:
        return RunLifecycle.INTERRUPTED if interrupted_evidence else RunLifecycle.RUNNING
    if status not in TERMINAL_RUN_STATUSES:
        return RunLifecycle.CORRUPT
    if run_manifest is None:
        # A terminal status with no manifest is damage, not work in progress.
        # This used to fall into the non-terminal branch and report `running`,
        # contradicting the run's own authoritative status. Unreachable through
        # `ImmutableRunView` — a missing manifest makes the declared version
        # undeclared, which returns earlier — but this function is public and
        # must not misclassify when called directly.
        return RunLifecycle.CORRUPT
    return RunLifecycle.FINALIZED_VALID if structurally_valid else RunLifecycle.FINALIZED_INVALID


TERMINAL_LIFECYCLES = frozenset({RunLifecycle.FINALIZED_VALID, RunLifecycle.FINALIZED_INVALID})


def classify_repository_document(
    document: dict[str, Any], lifecycle: RunLifecycle
) -> RepositoryDocumentVariant:
    """Select the ``oneOf`` branch for one ``repositories/<slug>.json`` document.

    Decision D-1, in order:

    1. Run lifecycle is primary. A non-terminal run yields the checkpoint variant.
    2. Presence of ``checkpoint_updated_at`` yields the checkpoint variant.
    3. Otherwise a terminal run yields the final-result variant, whose
       reconciliation against ``analysis.json`` is checked separately by
       :func:`reconciles_with_analysis`.

    ``checkpoint_status`` is deliberately **not** consulted: every finalized
    ArchLens 3.4.0 repository document retains it.
    """
    if lifecycle in (RunLifecycle.RUNNING, RunLifecycle.INTERRUPTED):
        return RepositoryDocumentVariant.CHECKPOINT
    if "checkpoint_updated_at" in document:
        return RepositoryDocumentVariant.CHECKPOINT
    if lifecycle in TERMINAL_LIFECYCLES:
        return RepositoryDocumentVariant.FINAL_RESULT
    return RepositoryDocumentVariant.INDETERMINATE


# Identity fields that must agree between a final repository document and its
# matching analysis element. Metric equality is checked by the semantic layer,
# not here; this is a structural identity reconciliation only.
RECONCILIATION_FIELDS = (
    "repository_url",
    "analysis_status",
    "program_version",
    "metric_contract_version",
    "exclusion_policy_version",
    "inventory_schema_version",
    "artifact_schema_version",
)


def reconciles_with_analysis(
    document: dict[str, Any], analysis_element: dict[str, Any] | None
) -> tuple[bool, list[str]]:
    """Structurally reconcile a final repository document with its analysis element.

    Per D-1, a terminal document that does not reconcile is invalid or corrupt.
    Returns ``(reconciled, differing_field_names)``.
    """
    if analysis_element is None:
        return False, ["<no matching analysis.json element>"]
    differences = [
        name
        for name in RECONCILIATION_FIELDS
        if document.get(name) != analysis_element.get(name)
    ]
    return (not differences), differences
