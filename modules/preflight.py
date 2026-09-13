"""Two-stage environment/capability preflight.

**The defect this exists to remove.** A run in an environment with no Java
grammar produced one ``parser_execution_failure`` per Java file — 40 files, 40
identical diagnostics — as though forty source files were individually broken.
They were not. One environment fact was reported forty times, at the wrong
granularity, after the whole repository had been acquired and walked.

The fix is not "report it once and continue". It is to decide, *before any
source file is parsed*, whether the capabilities this measurement actually needs
are present, and to refuse at capability granularity if they are not.

Two stages, with deliberately different guarantees
==================================================

``Stage A`` — :func:`stage_a`, before acquisition.
    Only what can be known without looking inside any repository: runtime
    semantics, the schema validation stack, Git when the acquisition mode needs
    it, path writability, installation evidence. **A refusal here means no
    acquisition began.**

``Stage B`` — :func:`stage_b`, after acquisition and parser-free discovery,
    before any metric parsing. **A refusal here means acquisition and cache
    activity may already have occurred** — that is unavoidable, because the
    languages a repository actually contains cannot be known before fetching
    it — but no source file was parsed, no run was published, and the
    latest-run pointer did not move. Claiming "nothing was acquired" for a
    Stage B refusal would be false.

Why grammars are not a Stage A requirement
==========================================

Stage A *probes* grammar availability and reports it, but never blocks on it.

It is tempting to block early using ``expected_language``, and it is wrong.
``expected_language`` is a research *expectation*, it may be absent, and
Metrolith exists in part to measure when it is mistaken — the pipeline emits
``expected_language_mismatch`` precisely because the declared language is not
evidence. Refusing before acquisition because the expected language's grammar
is missing could prevent Metrolith from discovering that the repository is
written in something else entirely.

So ``expected_language`` is reported as an *anticipated* requirement and never
as a blocking one. The definitive decision belongs to Stage B, which knows what
the repository actually contains.

``jsonschema`` is different, and genuinely global: finalization now validates
the terminal manifest and status against their schemas before publishing them,
so an unusable schema runtime means no run can be finalized at all. ``build`` is
development and packaging tooling; it must never block an analysis.

Requirements come from the canonical inventory
==============================================

:func:`required_parser_capabilities` derives the required set from
``RepositoryInventory`` records that are ``included_in_metrics`` — the same
selection the measurement path uses. A second extension scanner would be a
second source-selection implementation, free to drift from the one that decides
what is actually measured, and would make vendored, generated and excluded files
create requirements for grammars Metrolith would never invoke.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import url2pathname

from modules.vocabularies import (
    CapabilityReason,
    CapabilityState,
    InstallationKind,
    PreflightStage,
    parser_capability,
)

#: Refusal by either preflight stage. Distinct from usage (2), analysis failure
#: (1) and invalid artifacts (3) so a caller can tell "this environment cannot
#: run the measurement" from "the measurement ran and something went wrong".
EXIT_PREFLIGHT_REFUSED = 4

#: Languages whose parsing uses tree-sitter. Python is deliberately absent: it
#: is parsed by the standard library's `ast`, so a Python-only measurement needs
#: no grammar and not even the tree-sitter core.
TREE_SITTER_LANGUAGES = ("Java", "JavaScript", "TypeScript", "TSX", "Go")

#: Free space below which an observation is worth surfacing. Explicitly **not**
#: a blocking threshold: Metrolith has no defensible universal minimum, and the
#: space a run needs depends on repositories it has not fetched yet. Recording
#: evidence and warning is honest; refusing on a guessed number is not.
LOW_FREE_SPACE_OBSERVATION_BYTES = 1024 ** 3


@dataclass(frozen=True)
class CapabilityCheck:
    """One capability, its state, and why."""

    capability: str
    state: CapabilityState
    blocking: bool
    evidence: str
    reason: CapabilityReason | None = None
    #: What made this capability required. Empty when nothing required it.
    required_by: tuple[str, ...] = ()

    @property
    def refuses(self) -> bool:
        return self.blocking and self.state is CapabilityState.UNAVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "state": self.state.value,
            "blocking": self.blocking,
            "evidence": self.evidence,
            "reason": self.reason.value if self.reason else None,
            "required_by": list(self.required_by),
        }


@dataclass(frozen=True)
class PreflightReport:
    """The result of one stage."""

    stage: PreflightStage
    checks: tuple[CapabilityCheck, ...] = ()

    @property
    def refusals(self) -> tuple[CapabilityCheck, ...]:
        return tuple(check for check in self.checks if check.refuses)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def summary(self) -> str:
        """One line per refusal. Never a traceback, never a file list."""
        if not self.refused:
            return f"{self.stage.value}: all required capabilities available"
        lines = [
            f"{self.stage.value} refused: "
            f"{len(self.refusals)} required capability/capabilities unavailable"
        ]
        for check in self.refusals:
            reason = check.reason.value if check.reason else "unavailable"
            lines.append(f"  - {check.capability}: {reason} ({check.evidence})")
            if check.required_by:
                shown = ", ".join(check.required_by[:5])
                if len(check.required_by) > 5:
                    shown += f", +{len(check.required_by) - 5} more"
                lines.append(f"      required by: {shown}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "refused": self.refused,
            "checks": [check.as_dict() for check in self.checks],
            "refusals": [check.capability for check in self.refusals],
        }


# ------------------------------------------------------------- capabilities ---


def _distribution_evidence() -> tuple[InstallationKind, str]:
    """Classify how Metrolith is present without pretending to be sure.

    ``importlib.metadata.version("metrolith")`` succeeds from a source tree that
    merely contains stale generated metadata, which is how a check that
    only compared version strings reported "version agreement" while comparing
    the source tree to itself.
    """
    try:
        distribution = importlib.metadata.distribution("metrolith")
    except importlib.metadata.PackageNotFoundError:
        return (
            InstallationKind.SOURCE_TREE_ONLY,
            "no installed metrolith distribution; running from the source tree",
        )

    try:
        location = Path(str(distribution.locate_file(""))).resolve()
    except Exception:  # pragma: no cover - defensive
        return (InstallationKind.INDETERMINATE, "distribution location unreadable")

    repository = Path(__file__).resolve().parent.parent
    version = distribution.version
    entries = {item.as_posix(): item for item in distribution.files or ()}
    metadata = [name for name in entries if name.endswith(".dist-info/METADATA")]
    if len(metadata) != 1 or metadata[0].rsplit("/", 1)[0] + "/RECORD" not in entries:
        return (
            InstallationKind.SOURCE_TREE_ONLY,
            f"metrolith {version} has source/egg-info or incomplete metadata; "
            "no independent recorded installation",
        )
    try:
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        if direct.get("dir_info", {}).get("editable") is True:
            origin = urlsplit(direct.get("url", ""))
            if (origin.scheme == "file" and origin.netloc in ("", "localhost")
                    and Path(url2pathname(origin.path)).resolve() == repository
                    and location != repository):
                return (
                    InstallationKind.EDITABLE_INSTALL,
                    f"metrolith {version} editable provenance matches {repository}; "
                    "source bytes remain mutable",
                )
            return (InstallationKind.INDETERMINATE,
                    "editable provenance does not match the loaded source")

        # A dist-info directory alone is not evidence that these are its modules.
        # Bind recorded, hashed entry points to actual loaded code. Resource and
        # producer identities remain the separate existing Run checks.
        for name, module_name in (("pipeline.py", "pipeline"),
                                  ("modules/config.py", "modules.config"),
                                  ("modules/preflight.py", __name__)):
            entry = entries.get(name)
            module = sys.modules.get(module_name)
            loaded = Path(module.__file__).resolve() if module else repository / name
            if entry is None or Path(distribution.locate_file(entry)).resolve() != loaded:
                return (InstallationKind.INDETERMINATE,
                        "recorded installation does not match the loaded modules")
            recorded = entry.hash
            actual = base64.urlsafe_b64encode(hashlib.sha256(loaded.read_bytes()).digest()).decode().rstrip("=")
            if recorded is None or recorded.mode != "sha256" or recorded.value != actual:
                return (InstallationKind.INDETERMINATE,
                        "loaded module bytes do not match installation RECORD")
    except (OSError, ValueError, TypeError, AttributeError):
        return (InstallationKind.INDETERMINATE, "installation provenance unreadable")
    return (
        InstallationKind.INSTALLED_DISTRIBUTION,
        f"metrolith {version} recorded installation matches loaded modules at {location}",
    )


def probe_parser_capabilities() -> dict[str, CapabilityCheck]:
    """Probe every tree-sitter language plus Python. Never blocking here.

    Blocking is Stage B's decision, because only Stage B knows what is required.
    """
    from modules.core_metrics import validate_parser_initialization

    try:
        statuses = validate_parser_initialization()
    except Exception as exc:  # pragma: no cover - defensive
        return {
            parser_capability(language): CapabilityCheck(
                capability=parser_capability(language),
                state=CapabilityState.UNAVAILABLE,
                blocking=False,
                evidence=f"parser probe failed: {type(exc).__name__}: {exc}",
                reason=CapabilityReason.PARSER_INITIALIZATION_FAILED,
            )
            for language in (*TREE_SITTER_LANGUAGES, "Python")
        }

    checks: dict[str, CapabilityCheck] = {}
    for language, status in sorted(statuses.items()):
        available = status == "complete"
        checks[parser_capability(language)] = CapabilityCheck(
            capability=parser_capability(language),
            state=CapabilityState.AVAILABLE if available else CapabilityState.UNAVAILABLE,
            blocking=False,
            evidence=(
                f"{language} parser initialization {status}"
                + ("" if language != "Python" else " (standard library ast; no grammar)")
            ),
            reason=None if available else CapabilityReason.GRAMMAR_UNAVAILABLE,
        )
    return checks


def _schema_runtime_check() -> CapabilityCheck:
    """The schema validation stack, which finalization genuinely requires."""
    from validation.artifact_io import schema_store

    installed = schema_store.jsonschema_version()
    if installed is None:
        return CapabilityCheck(
            capability="schema_runtime",
            state=CapabilityState.UNAVAILABLE,
            blocking=True,
            evidence=(
                f"jsonschema is not importable; "
                f"{schema_store.REQUIRED_JSONSCHEMA_VERSION} is required"
            ),
            reason=CapabilityReason.DEPENDENCY_MISSING,
            required_by=("finalization terminal validation",),
        )
    if installed != schema_store.REQUIRED_JSONSCHEMA_VERSION:
        return CapabilityCheck(
            capability="schema_runtime",
            state=CapabilityState.UNAVAILABLE,
            blocking=True,
            evidence=(
                f"jsonschema {installed} installed; exactly "
                f"{schema_store.REQUIRED_JSONSCHEMA_VERSION} is required"
            ),
            reason=CapabilityReason.DEPENDENCY_VERSION_MISMATCH,
            required_by=("finalization terminal validation",),
        )
    return CapabilityCheck(
        capability="schema_runtime",
        state=CapabilityState.AVAILABLE,
        blocking=True,
        evidence=f"jsonschema {installed}",
    )


def _referencing_check() -> CapabilityCheck:
    """`referencing` backs $ref resolution and is imported by `schema_store`."""
    try:
        module = importlib.import_module("referencing")
    except Exception as exc:
        return CapabilityCheck(
            capability="schema_reference_runtime",
            state=CapabilityState.UNAVAILABLE,
            blocking=True,
            evidence=f"referencing is unusable: {type(exc).__name__}: {exc}",
            reason=CapabilityReason.DEPENDENCY_MISSING,
            required_by=("cross-schema $ref resolution",),
        )
    try:
        version = importlib.metadata.version("referencing")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    if not hasattr(module, "Registry") or not hasattr(module, "Resource"):
        return CapabilityCheck(
            capability="schema_reference_runtime",
            state=CapabilityState.UNAVAILABLE,
            blocking=True,
            evidence=(
                f"referencing {version} provides no Registry/Resource API; "
                f"cross-schema $ref resolution cannot work"
            ),
            reason=CapabilityReason.DEPENDENCY_VERSION_MISMATCH,
            required_by=("cross-schema $ref resolution",),
        )
    return CapabilityCheck(
        capability="schema_reference_runtime",
        state=CapabilityState.AVAILABLE,
        blocking=True,
        evidence=f"referencing {version}",
    )


def _git_check(*, required: bool, required_by: tuple[str, ...]) -> CapabilityCheck:
    try:
        version = subprocess.run(
            ["git", "--version"], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return CapabilityCheck(
            capability="git",
            state=CapabilityState.UNAVAILABLE,
            blocking=required,
            evidence=f"git is unavailable: {type(exc).__name__}: {exc}",
            reason=CapabilityReason.GIT_UNAVAILABLE,
            required_by=required_by if required else (),
        )
    return CapabilityCheck(
        capability="git",
        state=CapabilityState.AVAILABLE if required else CapabilityState.AVAILABLE,
        blocking=required,
        evidence=version,
        required_by=required_by if required else (),
    )


def _path_checks(config) -> list[CapabilityCheck]:
    checks: list[CapabilityCheck] = []
    for label, configured in (
        ("cache", config.cache_root),
        ("worktree", config.temporary_directory),
        ("output", config.output_root),
    ):
        path = Path(configured)
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        writable = probe.is_dir() and os.access(probe, os.W_OK)
        detail = str(path)
        if probe != path:
            detail += f" (nearest existing ancestor: {probe})"
        checks.append(CapabilityCheck(
            capability=f"{label}_writable",
            state=CapabilityState.AVAILABLE if writable else CapabilityState.UNAVAILABLE,
            blocking=True,
            evidence=detail,
            reason=None if writable else CapabilityReason.PATH_NOT_WRITABLE,
        ))
    return checks


def _free_space_check(config) -> CapabilityCheck:
    """Record evidence. Never blocking: there is no defensible universal minimum.

    Replacing a hardcoded "always healthy" with a guessed "must have X GiB"
    would trade one arbitrary answer for another. How much space a run needs
    depends on repositories it has not fetched yet.
    """
    try:
        free = shutil.disk_usage(config.workspace_root).free
    except OSError as exc:
        return CapabilityCheck(
            capability="free_space",
            state=CapabilityState.WARNING,
            blocking=False,
            evidence=f"free space could not be determined: {exc}",
            reason=CapabilityReason.FREE_SPACE_UNKNOWN,
        )
    low = free < LOW_FREE_SPACE_OBSERVATION_BYTES
    return CapabilityCheck(
        capability="free_space",
        state=CapabilityState.WARNING if low else CapabilityState.AVAILABLE,
        blocking=False,
        evidence=f"{free} bytes free on {config.workspace_root}",
        reason=CapabilityReason.LOW_FREE_SPACE if low else None,
    )


def _symlink_capability_check(config) -> CapabilityCheck:
    """Report the capability; never block on it globally.

    Creating symlinks on Windows needs Developer Mode or elevation. Metrolith
    reads Git's recorded symlink metadata rather than requiring real symlinks,
    so this is an honest capability gap, not a prerequisite.

    The probe runs in a **system** temporary directory that it creates and
    removes, never in a configured workspace path. `doctor` must not create or
    modify the cache, worktree or output roots merely by being asked what this
    environment can do — an inspection command that mutates what it inspects is
    not an inspection.

    On Windows this is a process-privilege question rather than a per-filesystem
    one, so the answer transfers; the evidence says where it was measured so a
    reader can judge that for themselves.
    """
    del config  # deliberately not used: no workspace path may be touched
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="metrolith_symlink_probe_") as raw:
            probe_root = Path(raw)
            target = probe_root / "target"
            target.write_text("probe", encoding="utf-8")
            os.symlink(target, probe_root / "link")
    except (OSError, NotImplementedError, AttributeError) as exc:
        return CapabilityCheck(
            capability="native_symlink_creation",
            state=CapabilityState.WARNING,
            blocking=False,
            evidence=f"native symlinks cannot be created (system temp probe): {exc}",
            reason=CapabilityReason.SYMLINK_CREATION_UNAVAILABLE,
        )
    return CapabilityCheck(
        capability="native_symlink_creation",
        state=CapabilityState.AVAILABLE,
        blocking=False,
        evidence="native symlink creation succeeded (system temp probe)",
    )


def _runtime_check() -> CapabilityCheck:
    from modules.config import SUPPORTED_PYTHON_MINOR, supported_python_version

    supported = supported_python_version(sys.version_info)
    return CapabilityCheck(
        capability="python_runtime",
        state=CapabilityState.AVAILABLE if supported else CapabilityState.UNAVAILABLE,
        blocking=True,
        evidence=(
            f"{platform.python_version()} (required "
            f"{SUPPORTED_PYTHON_MINOR[0]}.{SUPPORTED_PYTHON_MINOR[1]}.x)"
        ),
        reason=None if supported else CapabilityReason.UNSUPPORTED_RUNTIME,
    )


def _installation_check() -> CapabilityCheck:
    from modules.config import PROGRAM_VERSION

    kind, evidence = _distribution_evidence()
    if kind in (InstallationKind.SOURCE_TREE_ONLY, InstallationKind.INDETERMINATE):
        # Running from source is fully supported. What is *not* supported is
        # calling this "version agreement".
        return CapabilityCheck(
            capability="installation",
            state=CapabilityState.NOT_EVALUATED,
            blocking=False,
            evidence=f"{kind.value}: {evidence}",
            reason=CapabilityReason.NO_INDEPENDENT_INSTALLATION,
        )
    try:
        installed = importlib.metadata.version("metrolith")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        installed = None
    if installed != PROGRAM_VERSION:
        return CapabilityCheck(
            capability="installation",
            state=CapabilityState.WARNING,
            blocking=False,
            evidence=(
                f"{kind.value}: workspace {PROGRAM_VERSION}, installed {installed}"
            ),
            reason=CapabilityReason.VERSION_DISAGREEMENT,
        )
    return CapabilityCheck(
        capability="installation",
        state=CapabilityState.AVAILABLE,
        blocking=False,
        evidence=f"{kind.value}: {evidence}",
    )


def _policy_check() -> CapabilityCheck:
    from modules.config import POLICY_PATH

    present = Path(POLICY_PATH).is_file()
    return CapabilityCheck(
        capability="exclusion_policy",
        state=CapabilityState.AVAILABLE if present else CapabilityState.UNAVAILABLE,
        blocking=True,
        evidence=str(POLICY_PATH),
        reason=None if present else CapabilityReason.POLICY_FILE_MISSING,
    )


# ----------------------------------------------------------------- stage A ---

#: Acquisition modes that need Git. `offline` still resolves from the local
#: object cache with Git, so every current mode does; the mapping is explicit
#: rather than assumed so a future non-Git source mode does not inherit it.
GIT_REQUIRING_MODES = frozenset({"latest", "frozen", "offline"})


def stage_a(
    config,
    *,
    acquisition_mode: str = "latest",
    expected_languages: Iterable[str] = (),
) -> PreflightReport:
    """Global preflight. A refusal here means no acquisition began."""
    checks: list[CapabilityCheck] = [
        _runtime_check(),
        _schema_runtime_check(),
        _referencing_check(),
        _git_check(
            required=acquisition_mode in GIT_REQUIRING_MODES,
            required_by=(f"acquisition_mode={acquisition_mode}",),
        ),
        _policy_check(),
        *_path_checks(config),
        _free_space_check(config),
        _symlink_capability_check(config),
        _installation_check(),
    ]

    # Grammars are probed and reported, never blocking here. `expected_language`
    # is recorded as an *anticipated* requirement only: it is an expectation
    # Metrolith exists partly to test, and blocking on it could stop Metrolith
    # discovering that a repository is written in a different language.
    anticipated = {
        parser_capability(language) for language in expected_languages if language
    }
    for capability, probe in sorted(probe_parser_capabilities().items()):
        checks.append(CapabilityCheck(
            capability=probe.capability,
            state=probe.state,
            blocking=False,
            evidence=probe.evidence,
            reason=probe.reason,
            required_by=(
                ("anticipated from expected_language (not blocking)",)
                if capability in anticipated else ()
            ),
        ))
    return PreflightReport(PreflightStage.GLOBAL, tuple(checks))


# ----------------------------------------------------------------- stage B ---


def required_parser_capabilities(inventory) -> dict[str, set[str]]:
    """Languages actually measured, from the canonical inventory selection.

    Only records the measurement path would use (``included_in_metrics``) create
    a requirement. Vendored, generated and excluded files therefore never demand
    a grammar Metrolith would not invoke, and unsupported languages never create
    a requirement at all.

    Reusing ``RepositoryInventory`` rather than re-scanning extensions is the
    point: a second source-selection implementation could drift from the one
    that decides what is measured, and then the barrier would be guarding a
    different set of files than the parser sees.
    """
    from modules.core_metrics import SUPPORTED_LANGUAGES

    languages: dict[str, set[str]] = {}
    for record in inventory:
        if not record.included_in_metrics:
            continue
        language = record.detected_language
        if language not in SUPPORTED_LANGUAGES:
            continue
        # Python needs no tree-sitter capability at all.
        if language not in TREE_SITTER_LANGUAGES:
            continue
        languages.setdefault(parser_capability(language), set()).add(language)
    return languages


def stage_b(
    required: Mapping[str, Iterable[str]],
    *,
    probes: Mapping[str, CapabilityCheck] | None = None,
) -> PreflightReport:
    """Cohort capability barrier.

    ``required`` maps a capability id to the subjects that require it. Every
    capability the planned measurement needs is checked *once*, before any
    source file is parsed, so one missing grammar produces one refusal rather
    than one diagnostic per file.
    """
    available = probes if probes is not None else probe_parser_capabilities()

    checks: list[CapabilityCheck] = []
    for capability, subjects in sorted(required.items()):
        probe = available.get(capability)
        required_by = tuple(sorted(str(subject) for subject in subjects))
        if probe is None:
            checks.append(CapabilityCheck(
                capability=capability,
                state=CapabilityState.NOT_EVALUATED,
                blocking=True,
                evidence="capability was never probed",
                reason=CapabilityReason.PARSER_INITIALIZATION_FAILED,
                required_by=required_by,
            ))
            continue
        checks.append(CapabilityCheck(
            capability=capability,
            state=probe.state,
            blocking=True,
            evidence=probe.evidence,
            reason=probe.reason,
            required_by=required_by,
        ))

    # Capabilities that exist but nothing needs are reported as NOT_REQUIRED, so
    # "the Go grammar is missing and this run does not care" is visible rather
    # than silent.
    for capability, probe in sorted(available.items()):
        if capability in required:
            continue
        checks.append(CapabilityCheck(
            capability=capability,
            state=CapabilityState.NOT_REQUIRED,
            blocking=False,
            evidence=probe.evidence,
            reason=probe.reason,
        ))
    return PreflightReport(PreflightStage.CAPABILITY_BARRIER, tuple(checks))


def barrier_can_refuse(probes: Mapping[str, CapabilityCheck] | None = None) -> bool:
    """Whether any parser capability is missing at all.

    When every capability is available the barrier cannot refuse whatever the
    cohort turns out to contain, so the discovery pass that feeds it has no
    effect on the outcome and is skipped. The guarantee is unchanged — it is
    enforced exactly when it can matter — and a healthy environment pays
    nothing for it.
    """
    available = probes if probes is not None else probe_parser_capabilities()
    return any(
        check.state is not CapabilityState.AVAILABLE for check in available.values()
    )


class PreflightRefused(RuntimeError):
    """Raised to refuse a measurement. Carries the structured report."""

    def __init__(self, report: PreflightReport):
        super().__init__(report.summary())
        self.report = report
