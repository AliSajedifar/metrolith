"""Read-only CLI adapter for the BR4 ratchet request boundary.

The Ratchet Baseline Contract cannot attest that its producing run succeeded or
that the producing worktree was clean.  Consequently ``--baseline`` is a small
local bundle boundary, not permission to synthesize those facts from the JSON:

``BASELINE``
    canonical Ratchet Baseline 1.0 bytes;
``BASELINE.source-run/``
    the already-published producing Metrolith run named by the baseline.

The sibling run remains an ordinary immutable run bundle.  Its manifest bytes,
terminal status, subject commits, and working-tree state become the external
``SourceRunEvidence`` required by BR2. Revision sources are resolved from the
current run's runner-local Metrolith Git cache, never from the producing run's
recorded machine path. Missing evidence remains missing and therefore fails
admission; this adapter never fetches, repairs, or self-attests it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.ratchet.admission import (
    BaselineArtifactOrigin,
    BaselineTrustContext,
)
from modules.ratchet.capture import (
    BaselineCaptureError,
    source_run_evidence_from_run,
)
from modules.ratchet.contract import RatchetContractError, parse_baseline_json
from modules.ratchet.check_service import RatchetCheckRequest
from archlens_json import (
    BASELINE_JSON_LIMITS,
    StrictJsonError,
    read_bounded_bytes,
)
from validation.artifact_io.errors import ArtifactStructureError


SOURCE_RUN_SUFFIX = ".source-run"

REVISION_SOURCE_GUIDANCE = (
    "CLI Ratchet ancestry requires URL-backed subjects and existing Git sources "
    "in the current run's runner cache (repository_url and resolved_paths.cache_root). "
    "A local analyze --revision run alone supplies no mapping, even when its local "
    "repository still exists; check has no repository-override option. Use a "
    "current run with that URL/runner-cache prerequisite for Ratchet. Capture "
    "success does not establish this prerequisite. revision_source_missing means "
    "no ancestry comparison ran, not that no changes were found."
)


class RatchetCliRequestError(ValueError):
    """The CLI could not construct a complete BR4 request."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


def source_run_directory(baseline_path: Path) -> Path:
    """Return the one deterministic producing-run sidecar for ``BASELINE``."""

    baseline = Path(baseline_path).expanduser().resolve(strict=False)
    return baseline.with_name(baseline.name + SOURCE_RUN_SUFFIX)


def _revision_sources(view: Any) -> dict[str, Path]:
    """Resolve subject-keyed Git sources from the admitted current runner."""

    from modules.acquisition import cache_path_for_url

    raw_cache = (view.manifest.get("resolved_paths") or {}).get("cache_root")
    if not raw_cache:
        return {}
    cache_root = Path(str(raw_cache)).expanduser().resolve(strict=False)
    sources: dict[str, Path] = {}
    for repository in view.repositories:
        if not isinstance(repository, Mapping):
            continue
        key = repository.get("subject_key")
        url = repository.get("repository_url")
        if not isinstance(key, str) or not key or not isinstance(url, str) or not url:
            continue
        try:
            candidate = cache_path_for_url(cache_root, url)
        except ValueError:
            continue
        if candidate.is_dir():
            sources[key] = candidate
    return sources


def build_cli_ratchet_request(
    baseline_path: Path,
    expected_sha256: str,
    *,
    current_run_directory: Path,
    origin: BaselineArtifactOrigin | str = BaselineArtifactOrigin.LOCAL_FILE,
    pull_request_mode: bool = False,
) -> RatchetCheckRequest:
    """Construct BR4's request without comparing, mutating, or reanalyzing.

    The producing sidecar authenticates where the frozen observations came
    from. Revision ancestry is deliberately resolved from the current run's
    local cache instead: a published baseline must not depend on an absolute
    cache path from the machine that produced it.
    """

    path = Path(baseline_path).expanduser().resolve(strict=False)
    try:
        payload = read_bounded_bytes(
            path, source=str(path), limits=BASELINE_JSON_LIMITS
        )
    except StrictJsonError as exc:
        raise RatchetCliRequestError(
            "baseline_unreadable", exc.code
        ) from exc

    run_directory = source_run_directory(path)
    try:
        from validation.artifact_io.reader import open_run
        baseline = parse_baseline_json(payload)
        source_evidence = source_run_evidence_from_run(
            run_directory, baseline.rules
        )
    except (BaselineCaptureError, RatchetContractError, OSError) as exc:
        raise RatchetCliRequestError(
            "source_run_unreadable", str(getattr(exc, "code", type(exc).__name__))
        ) from exc

    try:
        current_view = open_run(Path(current_run_directory))
    except (ArtifactStructureError, OSError) as exc:
        raise RatchetCliRequestError(
            "current_run_unreadable", type(exc).__name__
        ) from exc

    return RatchetCheckRequest(
        baseline_payload=payload,
        trust=BaselineTrustContext(
            expected_sha256=expected_sha256,
            origin=origin,
            pull_request_mode=pull_request_mode,
        ),
        source_run=source_evidence,
        revision_sources=_revision_sources(current_view),
    )


__all__ = [
    "RatchetCliRequestError",
    "SOURCE_RUN_SUFFIX",
    "build_cli_ratchet_request",
    "source_run_directory",
]
