"""Run-directory path admission: allowlist, containment, symlink, and bounds.

Every run directory is treated as untrusted input (plan section 22). Nothing in
this module interprets measurement content; it decides only whether a byte
stream is allowed to be opened at all.

Three rules are enforced here and nowhere else, so they cannot be bypassed by a
later feature:

1. **Allowlist.** Only catalogued artifact paths may be opened.
2. **Containment.** The resolved path must remain inside the resolved run
   directory. Upward traversal is refused, including any attempt to reach
   ``latest_run.json`` in the output root (plan section 4.4).
3. **Symlink refusal.** In strict mode a mandatory artifact that is a symlink is
   refused, because its target is outside the integrity boundary. This concerns
   run-artifact files only, never Git symlinks inside analyzed repositories
   (plan section 7.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from .errors import StructuralErrorCode, raise_structural

MEGABYTE = 1024 * 1024


class ArtifactKind(str, Enum):
    """Parsing family. Determines which strict reader is legal for the path."""

    JSON = "json"
    JSON_LINES = "jsonl"
    CSV = "csv"
    MARKDOWN = "markdown"
    HTML = "html"


class Authority(str, Enum):
    """How much measurement weight an artifact carries (plan section 4.1)."""

    AUTHORITATIVE = "authoritative"
    CHECKED_PROJECTION = "checked_projection"
    OPTIONAL_PROJECTION = "optional_projection"
    NON_AUTHORITATIVE = "non_authoritative"
    EVENT_ORDERING_ONLY = "event_ordering_only"


@dataclass(frozen=True)
class ArtifactSpec:
    """One catalogued artifact path or path family."""

    name: str
    pattern: str
    kind: ArtifactKind
    authority: Authority
    mandatory: bool
    max_bytes: int
    max_rows: int | None = None
    directory_family: bool = False
    since_artifact_schema: str = "1.0.0"

    def matches(self, relative: str) -> bool:
        if not self.directory_family:
            return relative == self.pattern
        return re.fullmatch(self.pattern, relative) is not None


# Size bounds are deliberately generous: they exist to stop a hostile or
# corrupt artifact from exhausting memory, not to constrain legitimate cohorts.
# The 117-row monolith cohort is the sizing reference.
ARTIFACT_SPECS: tuple[ArtifactSpec, ...] = (
    ArtifactSpec("run_manifest", "run_manifest.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, True, 64 * MEGABYTE),
    ArtifactSpec("run_status", "run_status.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, True, 4 * MEGABYTE),
    ArtifactSpec("environment", "environment.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, True, 16 * MEGABYTE),
    ArtifactSpec("analysis", "analysis.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, True, 512 * MEGABYTE),
    ArtifactSpec("repository_document", r"repositories/[^/]+\.json", ArtifactKind.JSON,
                 Authority.OPTIONAL_PROJECTION, False, 64 * MEGABYTE, directory_family=True),
    ArtifactSpec("file_inventory", r"file_inventory/[^/]+\.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, True, 512 * MEGABYTE, directory_family=True),
    ArtifactSpec("fact_sheet", r"fact_sheets/[^/]+\.md", ArtifactKind.MARKDOWN,
                 Authority.OPTIONAL_PROJECTION, False, 16 * MEGABYTE, directory_family=True),
    ArtifactSpec("run_log", "logs/run.jsonl", ArtifactKind.JSON_LINES,
                 Authority.EVENT_ORDERING_ONLY, True, 512 * MEGABYTE, max_rows=5_000_000),
    ArtifactSpec("catalog", "catalog.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 256 * MEGABYTE, max_rows=1_000_000),
    ArtifactSpec("sheet_metrics", "sheet_metrics.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 32 * MEGABYTE, max_rows=200_000),
    ArtifactSpec("language_metrics", "language_metrics.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 64 * MEGABYTE, max_rows=1_000_000),
    ArtifactSpec("errors", "errors.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 256 * MEGABYTE, max_rows=5_000_000),
    ArtifactSpec("recoveries", "recoveries.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 256 * MEGABYTE, max_rows=5_000_000),
    ArtifactSpec("repositories_frozen", "repositories_frozen.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 16 * MEGABYTE, max_rows=200_000),
    ArtifactSpec("retry_failed_or_partial", "retry_failed_or_partial.csv", ArtifactKind.CSV,
                 Authority.CHECKED_PROJECTION, True, 16 * MEGABYTE, max_rows=200_000),
    # Artifact Schema 1.5 additions.
    ArtifactSpec("normalized_input", "normalized_input.csv", ArtifactKind.CSV,
                 Authority.AUTHORITATIVE, True, 64 * MEGABYTE, max_rows=1_000_000,
                 since_artifact_schema="1.5.0"),
    ArtifactSpec("contribution_ledger", "contributions.csv", ArtifactKind.CSV,
                 Authority.AUTHORITATIVE, False, 2048 * MEGABYTE, max_rows=50_000_000,
                 since_artifact_schema="1.5.0"),
    ArtifactSpec("contribution_partition", r"contributions/[^/]+\.csv", ArtifactKind.CSV,
                 Authority.AUTHORITATIVE, False, 512 * MEGABYTE, max_rows=50_000_000,
                 directory_family=True, since_artifact_schema="1.5.0"),
    ArtifactSpec("contribution_container", "contributions/container.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, False, 4 * MEGABYTE,
                 since_artifact_schema="1.5.0"),
    # Artifact Schema 1.9 additions: per-callable complexity. Bounds and the
    # partition family mirror the contribution ledger, which is the proven shape
    # for a per-row artifact whose size tracks the repository rather than the
    # cohort. `mandatory=False` is what keeps 1.8-and-earlier runs -- and any run
    # whose complexity state is explicitly unavailable -- readable; presence for
    # a run that CLAIMS measurement is enforced by the finalization completeness
    # gate, which is the layer that can see the declared state.
    ArtifactSpec("callable_complexity", "callables.csv", ArtifactKind.CSV,
                 Authority.AUTHORITATIVE, False, 2048 * MEGABYTE, max_rows=50_000_000,
                 since_artifact_schema="1.9.0"),
    ArtifactSpec("callable_complexity_partition", r"callables/[^/]+\.csv", ArtifactKind.CSV,
                 Authority.AUTHORITATIVE, False, 512 * MEGABYTE, max_rows=50_000_000,
                 directory_family=True, since_artifact_schema="1.9.0"),
    ArtifactSpec("callable_complexity_container", "callables/container.json", ArtifactKind.JSON,
                 Authority.AUTHORITATIVE, False, 4 * MEGABYTE,
                 since_artifact_schema="1.9.0"),
    # Artifact Schema 1.11 additions: benchmark qualification.
    #
    # `mandatory=False` here is a statement about the ALLOWLIST, not about the
    # run contract. Presence is CONDITIONAL -- mandatory in
    # `qualification_mode=benchmark_qualified` and forbidden in
    # `not_requested` -- and this layer cannot see the mode, because it decides
    # only whether a byte stream may be opened at all. Marking it mandatory here
    # would make every generic run and every historical 1.10 bundle structurally
    # invalid for correctly not having it. The conditional rule is enforced by
    # the finalization gate and the semantic validator, which are the layers
    # that can read the declared mode.
    ArtifactSpec("benchmark_qualification", "benchmark_qualification.json",
                 ArtifactKind.JSON, Authority.AUTHORITATIVE, False, 64 * MEGABYTE,
                 since_artifact_schema="1.11.0"),
    # The unrestricted repository-level comparison surface. A checked
    # projection: it is an exact filtered subset of `sheet_metrics.csv`, selected
    # solely by the independently derived eligibility predicate, and carries no
    # value that is not already reconciled against both authorities.
    ArtifactSpec("repository_level_metrics", "repository_level_metrics.csv",
                 ArtifactKind.CSV, Authority.CHECKED_PROJECTION, False,
                 32 * MEGABYTE, max_rows=200_000,
                 since_artifact_schema="1.11.0"),
    ArtifactSpec("summary", "summary.md", ArtifactKind.MARKDOWN,
                 Authority.NON_AUTHORITATIVE, True, 64 * MEGABYTE),
    ArtifactSpec("html_report", "report.html", ArtifactKind.HTML,
                 Authority.NON_AUTHORITATIVE, False, 128 * MEGABYTE),
)

_SPECS_BY_NAME = {spec.name: spec for spec in ARTIFACT_SPECS}

# Written to the output root, deliberately outside the run directory. Listed
# here so that an attempt to read it through the run-directory reader produces
# an explicit refusal rather than a confusing "not allowlisted" message.
OUTPUT_ROOT_POINTER = "latest_run.json"


def spec_for_name(name: str) -> ArtifactSpec:
    try:
        return _SPECS_BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown artifact name: {name!r}") from None


def spec_for_relative_path(relative: str) -> ArtifactSpec | None:
    """Return the catalogued spec for a run-relative POSIX path, if any."""
    for spec in ARTIFACT_SPECS:
        if spec.matches(relative):
            return spec
    return None


def normalize_relative(relative: str) -> str:
    """Normalize a caller-supplied relative path to POSIX form without resolving.

    Refuses absolute paths, drive letters, parent traversal, and current-segment
    noise before the filesystem is ever touched.
    """
    candidate = str(relative).replace("\\", "/").strip()
    if not candidate:
        raise_structural(
            StructuralErrorCode.ARTIFACT_NOT_ALLOWLISTED, str(relative),
            "empty artifact path",
        )
    pure = PurePosixPath(candidate)
    if pure.is_absolute() or ":" in pure.parts[0]:
        raise_structural(
            StructuralErrorCode.PATH_ESCAPES_RUN_DIRECTORY, candidate,
            "artifact path must be relative to the run directory",
        )
    if any(part == ".." for part in pure.parts):
        raise_structural(
            StructuralErrorCode.PATH_ESCAPES_RUN_DIRECTORY, candidate,
            "upward traversal is refused",
        )
    return PurePosixPath(*(part for part in pure.parts if part != ".")).as_posix()


def resolve_artifact(
    run_directory: Path,
    relative: str,
    *,
    strict_symlinks: bool = True,
    require_exists: bool = True,
) -> tuple[Path, ArtifactSpec]:
    """Admit one artifact for reading, or raise a typed structural error.

    Returns the concrete path and its catalogued spec. Performs no parsing.
    """
    normalized = normalize_relative(relative)

    if normalized == OUTPUT_ROOT_POINTER:
        raise_structural(
            StructuralErrorCode.ARTIFACT_NOT_ALLOWLISTED, normalized,
            "latest_run.json lives in the output root and carries no measurement "
            "authority; run-directory readers must not consult it",
        )

    spec = spec_for_relative_path(normalized)
    if spec is None:
        raise_structural(
            StructuralErrorCode.ARTIFACT_NOT_ALLOWLISTED, normalized,
            "path is not in the ArchLens run-artifact allowlist",
        )

    root = Path(run_directory).resolve(strict=False)
    candidate = root / normalized

    # Symlink refusal must happen before resolve(), because resolve() follows
    # links and would erase the evidence we are checking for.
    if strict_symlinks:
        probe = root
        for part in PurePosixPath(normalized).parts:
            probe = probe / part
            if probe.is_symlink():
                raise_structural(
                    StructuralErrorCode.PATH_IS_SYMLINK, normalized,
                    "run artifacts must not be symlinks in strict mode",
                    location=PurePosixPath(normalized).as_posix(),
                )

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise_structural(
            StructuralErrorCode.PATH_ESCAPES_RUN_DIRECTORY, normalized,
            "resolved artifact path escapes the run directory",
        )

    if require_exists:
        if not resolved.exists():
            raise_structural(
                StructuralErrorCode.ARTIFACT_MISSING, normalized,
                "artifact does not exist",
            )
        if not resolved.is_file():
            raise_structural(
                StructuralErrorCode.PATH_NOT_A_REGULAR_FILE, normalized,
                "artifact is not a regular file",
            )
        size = resolved.stat().st_size
        if size > spec.max_bytes:
            raise_structural(
                StructuralErrorCode.ARTIFACT_TOO_LARGE, normalized,
                f"artifact is {size} bytes, exceeding the {spec.max_bytes} byte bound",
                observed_bytes=size, max_bytes=spec.max_bytes,
            )

    return resolved, spec


def discover_family(run_directory: Path, artifact_name: str) -> list[str]:
    """List existing run-relative paths for a directory-family artifact.

    Deterministically sorted. Symlinked entries are skipped rather than raising,
    so that a hostile extra link cannot deny service to an otherwise valid run;
    opening any individual entry still enforces the strict rules above.
    """
    spec = spec_for_name(artifact_name)
    if not spec.directory_family:
        raise KeyError(f"{artifact_name!r} is not a directory family")
    root = Path(run_directory).resolve(strict=False)
    directory_name = spec.pattern.split("/", 1)[0]
    directory = root / directory_name
    if not directory.is_dir() or directory.is_symlink():
        return []
    found: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_file():
            continue
        relative = f"{directory_name}/{entry.name}"
        if spec.matches(relative):
            found.append(relative)
    return sorted(found)
