"""Compatibility-aware lazy artifact reader and the canonical run view.

Plan section 8. The layering is::

    validation.artifact_io
            |
    ArtifactCompatibility
            |
    StrictArtifactReader
            |
    ImmutableRunView

**This module is structural.** It admits, decodes, types, and caches artifact
content, and classifies lifecycle and version compatibility. It derives no
measurement: not source inclusion, not a detected language, not a parser
outcome, not a metric value, not a per-metric status, not a repository status,
not an expected-language-family status, and not a partial origin. Every such
value is copied from what the canonical pipeline emitted.

:class:`ImmutableRunView` is the canonical in-memory run representation
(plan section 3.3). No serialized derived artifact competes with it, and no
derived output may be read back as measurement evidence.

Loading is lazy. The initial load covers the manifest, status, environment,
analysis, and the two small named metric tables. Inventories, repository
documents, error and recovery tables, ledgers, the catalog, and the log are read
only when asked for. ``catalog.csv`` is deliberately **not** treated as small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from .compatibility import (
    CompatibilityState,
    CompatibilityVerdict,
    RepositoryDocumentVariant,
    RunLifecycle,
    check_inventory_pairing,
    classify_artifact_schema,
    classify_lifecycle,
    classify_repository_document,
    reconciles_with_analysis,
)
from .contracts import contract_for
from .errors import ArtifactStructureError, StructuralError, StructuralErrorCode
from .legacy_cells import LegacyCellRecovery
from .paths import Authority, discover_family, resolve_artifact, spec_for_name
from .strict_csv import iter_rows, read_rows
from .strict_json import decode_text, iter_json_lines
from .strict_json import loads as loads_document_text
from .strict_json import read_bytes as read_document_bytes

# Read during the initial load. `catalog.csv` is absent by design: it is one row
# per source file and is not small at cohort scale (plan section 8.4).
EAGER_TABLES = ("sheet_metrics.csv", "language_metrics.csv")

# Root JSON type each single-document artifact declares.
DOCUMENT_ROOTS: dict[str, type] = {
    "run_manifest.json": dict,
    "run_status.json": dict,
    "environment.json": dict,
    "analysis.json": list,
}


@dataclass(frozen=True)
class ProjectionWarning:
    """A missing or degraded optional projection.

    Plan section 4.3: a missing optional projection must not crash validation
    and must not invalidate authoritative metrics, but it must be visible.
    """

    artifact: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"artifact": self.artifact, "message": self.message}

    def __str__(self) -> str:
        return f"{self.artifact}: {self.message}"


class StrictArtifactReader:
    """Admits and decodes run artifacts, caching each one at most once.

    Every read passes through the allowlist, containment, symlink, and size
    checks in :mod:`.paths`. Nothing here interprets measurement content.
    """

    def __init__(self, run_directory: Path, *, strict_symlinks: bool = True) -> None:
        self.run_directory = Path(run_directory).resolve(strict=False)
        self.strict_symlinks = strict_symlinks
        self._documents: dict[str, Any] = {}
        # Exact bytes paired with `_documents`.  A protected evaluator hashes
        # this cache, so the run-manifest digest always identifies the same
        # bytes the structural reader parsed rather than a second filesystem
        # read that could race or normalize content.
        self._document_bytes: dict[str, bytes] = {}
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._recoveries: list[LegacyCellRecovery] = []

    # -- admission -----------------------------------------------------------

    def exists(self, relative: str) -> bool:
        """Whether an allowlisted artifact is present, without reading it."""
        try:
            resolve_artifact(
                self.run_directory, relative,
                strict_symlinks=self.strict_symlinks, require_exists=True,
            )
        except ArtifactStructureError:
            return False
        return True

    def family(self, artifact_name: str) -> list[str]:
        return discover_family(self.run_directory, artifact_name)

    # -- typed reads ---------------------------------------------------------

    def document(self, relative: str, *, expect: type | None = None) -> Any:
        """Read one JSON artifact, cached."""
        if relative in self._documents:
            return self._documents[relative]
        path, _spec = resolve_artifact(
            self.run_directory, relative, strict_symlinks=self.strict_symlinks
        )
        root = expect if expect is not None else DOCUMENT_ROOTS.get(relative, dict)
        payload = read_document_bytes(path, relative)
        document = loads_document_text(decode_text(payload, relative), relative, expect=root)
        self._document_bytes[relative] = payload
        self._documents[relative] = document
        return document

    def document_bytes(self, relative: str) -> bytes:
        """Return the exact cached bytes parsed for one JSON artifact."""

        if relative not in self._documents:
            self.document(relative)
        return self._document_bytes[relative]

    def table(
        self, relative: str, *, declared_version: str | None = None
    ) -> list[dict[str, Any]]:
        """Read one tabular artifact in full, cached.

        ``declared_version`` is the third finding F-1 gate. Callers that omit it
        make legacy recovery unreachable, which is the safe default.
        """
        if relative in self._tables:
            return self._tables[relative]
        path, spec = resolve_artifact(
            self.run_directory, relative, strict_symlinks=self.strict_symlinks
        )
        rows = read_rows(
            path, relative, contract_for(relative, declared_version),
            max_rows=spec.max_rows,
            declared_version=declared_version,
            recoveries=self._recoveries,
        )
        self._tables[relative] = rows
        return rows

    def stream_table(
        self, relative: str, *, declared_version: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream one tabular artifact without materializing it.

        Used for ledgers that may be far larger than memory. Streamed reads are
        not cached, because caching them would defeat the point.
        """
        path, spec = resolve_artifact(
            self.run_directory, relative, strict_symlinks=self.strict_symlinks
        )
        yield from iter_rows(
            path, relative, contract_for(relative, declared_version),
            max_rows=spec.max_rows,
            declared_version=declared_version,
            recoveries=self._recoveries,
        )

    def stream_log(self) -> Iterator[tuple[int, dict[str, Any]]]:
        """Stream ``logs/run.jsonl``, authoritative for event ORDERING only."""
        relative = "logs/run.jsonl"
        path, spec = resolve_artifact(
            self.run_directory, relative, strict_symlinks=self.strict_symlinks
        )
        yield from iter_json_lines(path, relative, max_rows=spec.max_rows)

    @property
    def legacy_recoveries(self) -> tuple[LegacyCellRecovery, ...]:
        """Every finding F-1 recovery announced during this reader's lifetime."""
        return tuple(self._recoveries)


class ImmutableRunView:
    """The canonical in-memory representation of one run directory.

    Authoritative finalized repository results come from ``analysis.json``. The
    optional ``repositories/<slug>.json`` projection is checked only when
    present, and its absence produces a warning rather than an exception.
    """

    def __init__(self, run_directory: Path, *, strict_symlinks: bool = True) -> None:
        self.run_directory = Path(run_directory).resolve(strict=False)
        self.reader = StrictArtifactReader(
            self.run_directory, strict_symlinks=strict_symlinks
        )
        self._warnings: list[ProjectionWarning] = []
        self._errors: list[StructuralError] = []
        self._eager_error_count = 0
        self._load_eager()

    # -- construction --------------------------------------------------------

    def _load_eager(self) -> None:
        for relative in ("run_manifest.json", "run_status.json"):
            try:
                self.reader.document(relative)
            except ArtifactStructureError as exc:
                self._errors.append(exc.error)

        for relative in ("environment.json", "analysis.json"):
            try:
                self.reader.document(relative)
            except ArtifactStructureError as exc:
                self._errors.append(exc.error)

        for relative in EAGER_TABLES:
            try:
                self.reader.table(relative, declared_version=self.declared_artifact_schema)
            except ArtifactStructureError as exc:
                self._errors.append(exc.error)

        # Frozen here so that :attr:`lifecycle` is a function of the eager set
        # alone. `_errors` keeps growing as optional dimensions are read later,
        # and classifying against the live list would make the lifecycle depend
        # on which properties a caller happened to touch first.
        self._eager_error_count = len(self._errors)

    # -- identity and versions ----------------------------------------------

    @property
    def manifest(self) -> Mapping[str, Any]:
        return MappingProxyType(self.reader._documents.get("run_manifest.json") or {})

    @property
    def status(self) -> Mapping[str, Any]:
        return MappingProxyType(self.reader._documents.get("run_status.json") or {})

    @property
    def environment(self) -> Mapping[str, Any]:
        return MappingProxyType(self.reader._documents.get("environment.json") or {})

    @property
    def integrity_status(self) -> str | None:
        """The run's own terminal integrity verdict, or ``None`` if unwritten.

        ``run_status.json`` is the only artifact authoritative for run
        integrity. This reads it, and nothing else.
        """
        value = self.status.get("status")
        return str(value) if value is not None else None

    @property
    def succeeded(self) -> bool:
        """Whether the run reports that it executed successfully.

        **This is the canonical success test. Lifecycle is not one.**

        ``RunLifecycle.FINALIZED_VALID`` means "the published artifacts decode",
        which is a statement about readability, not about execution. The two
        genuinely come apart: a run whose terminal state failed self-validation
        publishes ``status: failed`` while every artifact that reached disk
        still decodes cleanly, so it is simultaneously structurally valid and
        integrity-failed — and both are true.

        Consumers that need "did this run succeed" must use this. Consumers that
        need "can I decode this run" want :attr:`lifecycle` or
        :attr:`finalized`. Treating the latter as the former is the defect this
        property exists to remove.
        """
        return self.integrity_status in {"completed", "completed_with_errors"}

    @property
    def run_id(self) -> str | None:
        return self.status.get("run_id") or self.manifest.get("run_id")

    @property
    def declared_artifact_schema(self) -> str | None:
        return self.manifest.get("artifact_schema_version")

    @cached_property
    def compatibility(self) -> CompatibilityVerdict:
        return classify_artifact_schema(self.declared_artifact_schema)

    @cached_property
    def lifecycle(self) -> RunLifecycle:
        """Classified from the eager set only, so it never depends on read order.

        A structural fault in an *optional* projection does not make a finalized
        run invalid — that is what :meth:`ProjectionWarning` and
        :meth:`check_repository_projection` are for — and letting it do so would
        mean the verdict changed according to which properties a caller had
        already touched.
        """
        return classify_lifecycle(
            self.reader._documents.get("run_status.json"),
            self.reader._documents.get("run_manifest.json"),
            self.compatibility,
            structurally_valid=self._eager_error_count == 0,
            interrupted_evidence=self._interrupted_evidence(),
        )

    def _interrupted_evidence(self) -> bool:
        """Positive evidence only. ArchLens never writes an interrupted run status."""
        for relative in self.reader.family("repository_document"):
            try:
                document = self.reader.document(relative)
            except ArtifactStructureError:
                continue
            if document.get("checkpoint_status") == "interrupted":
                return True
        return False

    @property
    def finalized(self) -> bool:
        """Whether finalized verdicts are permitted (plan section 8.2)."""
        return self.lifecycle in (
            RunLifecycle.FINALIZED_VALID, RunLifecycle.FINALIZED_INVALID
        )

    # -- authoritative results ----------------------------------------------

    @property
    def repositories(self) -> tuple[Mapping[str, Any], ...]:
        """Authoritative finalized repository results, from ``analysis.json``."""
        elements = self.reader._documents.get("analysis.json") or []
        return tuple(MappingProxyType(item) for item in elements)

    @cached_property
    def repositories_by_url(self) -> Mapping[str, Mapping[str, Any]]:
        """Key-aligned access. Canonical URL is the repository key everywhere."""
        return MappingProxyType({
            str(item.get("repository_url")): item
            for item in self.repositories
            if item.get("repository_url")
        })

    @cached_property
    def repositories_by_subject(self) -> Mapping[str, Mapping[str, Any]]:
        """Artifact 1.7+ identity access, including subjects with no locator."""

        return MappingProxyType({
            str(item.get("subject_key")): item
            for item in self.repositories
            if item.get("subject_key")
        })

    def repository(self, canonical_url: str) -> Mapping[str, Any] | None:
        return self.repositories_by_url.get(canonical_url)

    # -- lazy dimensions -----------------------------------------------------

    @cached_property
    def sheet_metrics(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(row) for row in self.reader._tables.get("sheet_metrics.csv", ()))

    @cached_property
    def language_metrics(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(row) for row in self.reader._tables.get("language_metrics.csv", ()))

    def table(self, relative: str) -> tuple[Mapping[str, Any], ...]:
        """Read one tabular artifact on demand, carrying the F-1 version gate."""
        rows = self.reader.table(relative, declared_version=self.declared_artifact_schema)
        return tuple(MappingProxyType(row) for row in rows)

    @cached_property
    def errors(self) -> tuple[Mapping[str, Any], ...]:
        return self.table("errors.csv")

    @cached_property
    def recoveries(self) -> tuple[Mapping[str, Any], ...]:
        return self.table("recoveries.csv")

    @cached_property
    def catalog(self) -> tuple[Mapping[str, Any], ...]:
        """Read on demand only. One row per source file; not small at cohort scale."""
        return self.table("catalog.csv")

    @cached_property
    def inventories(self) -> Mapping[str, Mapping[str, Any]]:
        """Inventory documents keyed by run-relative path, read on demand."""
        found: dict[str, Mapping[str, Any]] = {}
        for relative in self.reader.family("file_inventory"):
            try:
                found[relative] = MappingProxyType(self.reader.document(relative))
            except ArtifactStructureError as exc:
                self._errors.append(exc.error)
        if not found:
            self._warnings.append(ProjectionWarning(
                "file_inventory/",
                "no inventory documents present; inventory dimensions are not "
                "evaluable and aggregate operations only are supported",
            ))
        return MappingProxyType(found)

    @property
    def has_inventory(self) -> bool:
        return bool(self.inventories)

    @cached_property
    def normalized_input(self) -> tuple[Mapping[str, Any], ...] | None:
        """The complete accepted input population, or ``None`` when absent.

        ``None`` means an older run without the Artifact 1.5 ledger, which
        restricts reproduction to the resolved subset (plan section 7.2). It
        never means an empty population.
        """
        if not self.reader.exists("normalized_input.csv"):
            self._warnings.append(ProjectionWarning(
                "normalized_input.csv",
                "absent; the complete input population is not evaluable and "
                "reproduction is limited to the resolved subset",
            ))
            return None
        return self.table("normalized_input.csv")

    @cached_property
    def contribution_container(self) -> Mapping[str, Any] | None:
        if not self.reader.exists("contributions/container.json"):
            return None
        return MappingProxyType(self.reader.document("contributions/container.json"))

    def stream_contributions(self) -> Iterator[Mapping[str, Any]]:
        """Stream the per-file contribution ledger from whichever container holds it.

        Yields nothing when no ledger exists; callers must treat exact metric
        reconciliation as ``not_evaluable`` in that case, never as zero.
        """
        version = self.declared_artifact_schema
        container = self.contribution_container
        if container is not None:
            for partition in container.get("partitions", ()):
                path = partition.get("path")
                if not path:
                    continue
                for row in self.reader.stream_table(path, declared_version=version):
                    yield MappingProxyType(row)
            return
        if self.reader.exists("contributions.csv"):
            for row in self.reader.stream_table("contributions.csv", declared_version=version):
                yield MappingProxyType(row)

    @property
    def has_contribution_ledger(self) -> bool:
        return (
            self.contribution_container is not None
            or self.reader.exists("contributions.csv")
        )

    # -- per-callable complexity (Artifact Schema 1.9) -----------------------

    @cached_property
    def callable_container(self) -> Mapping[str, Any] | None:
        if not self.reader.exists("callables/container.json"):
            return None
        return MappingProxyType(self.reader.document("callables/container.json"))

    def stream_callables(self) -> Iterator[Mapping[str, Any]]:
        """Stream per-callable complexity rows from whichever container holds them.

        Yields nothing when no ledger exists. A caller must then treat callable
        reconciliation as ``not_evaluable``, never as a measured zero -- which is
        why the file-level status columns on the contribution ledger exist.
        """
        version = self.declared_artifact_schema
        container = self.callable_container
        if container is not None:
            for partition in container.get("partitions", ()):
                path = partition.get("path")
                if not path:
                    continue
                for row in self.reader.stream_table(path, declared_version=version):
                    yield MappingProxyType(row)
            return
        if self.reader.exists("callables.csv"):
            for row in self.reader.stream_table("callables.csv", declared_version=version):
                yield MappingProxyType(row)

    @property
    def has_callable_artifact(self) -> bool:
        """Whether this run published per-callable complexity at all.

        Consumers must branch on this explicitly rather than inferring from an
        empty stream: a run that measured complexity and found no callables and a
        run that never measured are different facts.
        """
        return (
            self.callable_container is not None
            or self.reader.exists("callables.csv")
        )

    # -- benchmark qualification (Artifact Schema 1.11) ----------------------

    @cached_property
    def benchmark_qualification(self) -> Mapping[str, Any] | None:
        """The qualification authority, or ``None`` when the run has none.

        Exposed as its OWN dimension, deliberately separate from
        :attr:`repositories`. Qualification is a different authority answering a
        different question, and merging it onto the measurement results here
        would let a consumer read a representativeness verdict as though it were
        part of the measurement it qualifies.

        ``None`` covers two distinct situations that a caller must not conflate,
        which is why :attr:`qualification_mode` is read separately: a generic
        run that correctly requested no qualification, and a historical
        pre-1.11 bundle that predates the concept entirely. Neither means
        ``ADEQUATE``, and neither may be adapted into one.
        """
        if not self.reader.exists("benchmark_qualification.json"):
            return None
        try:
            return MappingProxyType(
                self.reader.document("benchmark_qualification.json")
            )
        except ArtifactStructureError as exc:
            self._errors.append(exc.error)
            return None

    @property
    def qualification_mode(self) -> str:
        """Declared qualification mode, defaulting to ``not_requested``.

        A run that declares nothing is not benchmark-qualified. Absence is read
        as the restrictive answer, never the permissive one.
        """
        manifest = self.reader._documents.get("run_manifest.json") or {}
        declared = manifest.get("qualification_mode")
        return str(declared) if declared else "not_requested"

    @property
    def benchmark_of_record_readiness(self) -> Mapping[str, Any] | None:
        """The persisted readiness verdict, or ``None`` for a run that has none.

        A pre-1.11 bundle carries no verdict. That absence is itself the answer
        -- such a run is not benchmark-of-record ready -- but it is reported as
        absence rather than synthesized into a NOT_READY object nobody wrote.
        """
        manifest = self.reader._documents.get("run_manifest.json") or {}
        readiness = manifest.get("benchmark_of_record_readiness")
        return MappingProxyType(readiness) if isinstance(readiness, dict) else None

    @cached_property
    def repository_level_metrics(self) -> tuple[Mapping[str, Any], ...]:
        """The unrestricted comparison subset, empty when the run publishes none."""
        if not self.reader.exists("repository_level_metrics.csv"):
            return ()
        return self.table("repository_level_metrics.csv")

    # -- optional repository projection --------------------------------------

    @cached_property
    def repository_documents(self) -> Mapping[str, Mapping[str, Any]]:
        """Optional ``repositories/<slug>.json`` projection, checked when present."""
        found: dict[str, Mapping[str, Any]] = {}
        for relative in self.reader.family("repository_document"):
            try:
                found[relative] = MappingProxyType(self.reader.document(relative))
            except ArtifactStructureError as exc:
                self._errors.append(exc.error)
        if not found:
            self._warnings.append(ProjectionWarning(
                "repositories/",
                "optional repository projection is absent; authoritative results "
                "in analysis.json are unaffected",
            ))
        return MappingProxyType(found)

    def check_repository_projection(self) -> list[StructuralError]:
        """Reconcile each final-variant repository document against analysis.json.

        Surfacing disagreement is the point (plan section 8.6). A terminal
        document that fails to reconcile is invalid, per decision D-1.
        """
        problems: list[StructuralError] = []
        for relative, document in self.repository_documents.items():
            variant = classify_repository_document(dict(document), self.lifecycle)
            if variant is not RepositoryDocumentVariant.FINAL_RESULT:
                continue
            subject_key = document.get("subject_key")
            element = (
                self.repositories_by_subject.get(str(subject_key))
                if subject_key
                else self.repositories_by_url.get(str(document.get("repository_url")))
            )
            reconciled, differences = reconciles_with_analysis(
                dict(document), dict(element) if element else None
            )
            if not reconciled:
                problems.append(StructuralError(
                    code=StructuralErrorCode.TERMINAL_DOCUMENT_DOES_NOT_RECONCILE,
                    artifact=relative,
                    message=(
                        "final-variant repository document does not reconcile with "
                        f"analysis.json on: {', '.join(differences)}"
                    ),
                ))
        return problems

    def repository_document_variant(self, relative: str) -> RepositoryDocumentVariant:
        document = self.repository_documents.get(relative)
        if document is None:
            return RepositoryDocumentVariant.INDETERMINATE
        return classify_repository_document(dict(document), self.lifecycle)

    # -- diagnostics ---------------------------------------------------------

    def materialize_diagnostics(self) -> "ImmutableRunView":
        """Force the optional dimensions the diagnostic model reports on.

        ``_warnings`` and ``_errors`` are populated *lazily*, by the very
        properties that discover a projection is missing. A caller that read
        :attr:`warnings` before touching those properties therefore saw an
        incomplete list — and ``modules.diagnostics.project_run`` did exactly
        that, so the same run directory produced different evidence depending on
        the order its properties happened to be touched.

        Calling this first makes the projection a function of the directory
        instead of the access order.

        Laziness is deliberately preserved. Only the dimensions the diagnostic
        model actually reports on are forced. ``catalog.csv``, ``errors.csv``,
        ``recoveries.csv`` and the contribution ledger rows stay unread, because
        they are unbounded at cohort scale and reading them here would make
        every ``explain`` invocation pay for a full pass over the run.

        Idempotent: each underlying property is a ``cached_property``, so
        repeated calls add no duplicate warnings. Returns ``self`` so it can be
        chained.
        """
        self.inventories
        self.repository_documents
        self.normalized_input
        self.contribution_container
        return self

    @property
    def structural_errors(self) -> tuple[StructuralError, ...]:
        return tuple(self._errors)

    @property
    def warnings(self) -> tuple[ProjectionWarning, ...]:
        return tuple(self._warnings)

    @property
    def legacy_recoveries(self) -> tuple[LegacyCellRecovery, ...]:
        return self.reader.legacy_recoveries

    def compatibility_report(self) -> dict[str, Any]:
        """Everything a caller needs to decide whether to proceed."""
        pairing = check_inventory_pairing(
            self.compatibility, self.manifest.get("inventory_schema_version")
        )
        return {
            "run_id": self.run_id,
            "run_directory": str(self.run_directory),
            "lifecycle": self.lifecycle.value,
            "finalized": self.finalized,
            "artifact_schema_compatibility": self.compatibility.as_dict(),
            "inventory_pairing_warning": pairing,
            "readable": self.compatibility.state is CompatibilityState.SUPPORTED,
            "structural_error_count": len(self._errors),
            "structural_errors": [item.as_dict() for item in self._errors],
            "warnings": [item.as_dict() for item in self._warnings],
            "legacy_cell_recoveries": len(self.legacy_recoveries),
        }


def open_run(run_directory: Path, *, strict_symlinks: bool = True) -> ImmutableRunView:
    """Open one run directory as an immutable view."""
    return ImmutableRunView(run_directory, strict_symlinks=strict_symlinks)
