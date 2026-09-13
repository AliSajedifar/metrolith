"""``metrolith reproduce`` preflight (plan section 16).

**The default is read-only.** Preflight writes nothing, opens no network
connection, creates no worktree, and runs no parser or metric extraction. It
answers one question — could this run be reproduced, and if not, exactly what is
missing — and it answers it from the run's own artifacts.

Assurance states (plan section 16.2):

``exact_environment_ready``
    Impossible when the original run has no real profiler commit SHA. A content
    manifest supports local drift detection; it is not a third-party
    exact-revision claim, so this state is never reached on such a run.
``compatible_reexecution_ready``
    The program, contract, policy, and parser versions agree and the full
    normalized population is available.
``resolved_subset_only``
    No normalized-input ledger, so only the repositories that actually resolved
    can be re-executed. An older run always lands here.
``not_reproducible``
    A blocker prevents re-execution.
``invalid_artifacts``
    The run cannot be read.

Execution (``--execute``) **is** implemented, in :func:`execute`, behind the
explicit gates in :func:`authorize_execution`. Once it runs, this command is no
longer read-only, and every fact it reports about what happened —
``wrote_anything``, ``network_used``, ``output_root_created``,
``reproduction_status`` — is observed from the resulting run rather than assumed
from the flags that were passed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from modules.config import (
    ARTIFACT_SCHEMA_VERSION,
    INVENTORY_SCHEMA_VERSION,
    METRIC_CONTRACT_VERSION,
    PROGRAM_VERSION,
)

EXIT_READY = 0
EXIT_NOT_REPRODUCIBLE = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3

# 1.1.0: 1.0 predated fields this command already emitted, while its
# schema declared `additionalProperties: false`, so every real invocation
# violated its own published contract. The emitted payload is the intended
# interface, so the schema was brought up to it rather than the reverse.
REPRODUCTION_FORMAT_VERSION = "1.1.0"

STATE_EXACT = "exact_environment_ready"
STATE_COMPATIBLE = "compatible_reexecution_ready"
STATE_SUBSET = "resolved_subset_only"
STATE_NOT_REPRODUCIBLE = "not_reproducible"
STATE_INVALID = "invalid_artifacts"

PASSED = "passed"
FAILED = "failed"
NOT_EVALUABLE = "not_evaluable"
ADVISORY = "advisory"

# Above this repository count, execution requires --confirm-large-run. The
# default is documented in CLI help so a non-interactive caller can see it
# without reading the source (plan section 18.3).
LARGE_RUN_REPOSITORY_THRESHOLD = 25


def add_parser(subparsers) -> None:
    reproduce = subparsers.add_parser(
        "reproduce",
        help="Read-only preflight assessing whether a run could be reproduced",
    )
    reproduce.add_argument("run_directory", type=Path)
    reproduce.add_argument("--format", choices=("text", "json"), default="text")

    # Execution (plan section 18). Every gate is opt-in and explicit: nothing
    # about a preflight invocation can drift into re-running a benchmark.
    reproduce.add_argument(
        "--execute", action="store_true",
        help="Re-execute the run. Requires --mode and an isolated --output-root.",
    )
    reproduce.add_argument(
        "--mode", choices=("offline", "frozen"),
        help="Acquisition mode. There is no Offline-to-Frozen fallback.",
    )
    reproduce.add_argument(
        "--scope", choices=("full", "resolved-subset", "repository"),
        default="resolved-subset",
        help="What to re-execute (default: resolved-subset)",
    )
    reproduce.add_argument(
        "--repo", dest="repository", action="append",
        help="Canonical URL to re-execute; repeatable. Requires --scope repository.",
    )
    reproduce.add_argument(
        "--allow-network", action="store_true",
        help="Authorize network access. Required by --mode frozen.",
    )
    reproduce.add_argument(
        "--confirm-large-run", action="store_true",
        help=(
            f"Required when the scope exceeds {LARGE_RUN_REPOSITORY_THRESHOLD} "
            f"repositories."
        ),
    )
    reproduce.add_argument(
        "--output-root", type=Path,
        help="Isolated output root. Must not be the source run's output root.",
    )
    reproduce.add_argument(
        "--workers", type=int, default=1,
        help="Worker count. Final benchmark evidence uses 1 (the default).",
    )


def handle(args) -> int:
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    try:
        view = open_run(args.run_directory)
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_INVALID_ARTIFACTS

    payload = preflight(view)

    if getattr(args, "execute", False):
        refusal = authorize_execution(args, view, payload)
        if refusal is not None:
            print(f"[REFUSED] {refusal}")
            return EXIT_USAGE
        payload = execute(args, view, payload)

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_text(payload))

    if payload["assurance_state"] == STATE_INVALID:
        return EXIT_INVALID_ARTIFACTS
    if payload["assurance_state"] == STATE_NOT_REPRODUCIBLE:
        return EXIT_NOT_REPRODUCIBLE
    return EXIT_READY


def selected_repositories(args, view) -> list[str]:
    """Resolve the execution scope to a concrete repository list."""
    scope = getattr(args, "scope", "resolved-subset")
    if scope == "repository":
        return list(getattr(args, "repository", None) or [])
    if scope == "full":
        normalized = view.normalized_input
        if normalized is None:
            return []
        return sorted({
            str(row.get("canonical_url"))
            for row in normalized
            if row.get("normalized_enabled")
            and row.get("duplicate_classification") != "identical_duplicate"
        })
    return sorted(view.repositories_by_url)


def _overlaps(first: Path, second: Path) -> bool:
    """Whether two resolved paths are the same or one contains the other.

    Containment has to be checked in **both** directions. The original guard
    tested only ``destination in source.parents``, which is true only when the
    destination is an *ancestor*. A descendant was therefore accepted, so
    ``--output-root <source_run_dir>/nested`` wrote a complete run tree inside
    the immutable source run directory — adding paths its own allowlist does not
    contain, to a tree the reader treats as untrusted input.
    """
    if first == second:
        return True
    for parent, child in ((first, second), (second, first)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        return True
    return False


def authorize_execution(args, view, report) -> str | None:
    """Return a refusal message, or ``None`` when execution is authorized.

    Every gate is checked before anything is written. A refusal is a usage
    error, not a partial run.
    """
    if report["assurance_state"] in {STATE_INVALID, STATE_NOT_REPRODUCIBLE}:
        return (
            f"preflight reports {report['assurance_state']}; execution is refused "
            f"until the recorded blockers are resolved"
        )

    mode = getattr(args, "mode", None)
    if mode is None:
        return "--execute requires an explicit --mode offline|frozen"

    # No Offline-to-Frozen fallback: if Offline cannot proceed, the run stops.
    # Silently reaching for the network would turn an offline reproduction into
    # a different experiment.
    if mode == "frozen" and not getattr(args, "allow_network", False):
        return "--mode frozen requires explicit --allow-network authorization"
    if mode == "offline" and getattr(args, "allow_network", False):
        return "--allow-network is not meaningful with --mode offline"

    output_root = getattr(args, "output_root", None)
    if output_root is None:
        return "--execute requires an explicit isolated --output-root"

    destination = Path(output_root).resolve()
    source_run = view.run_directory.resolve()

    # The invariant that actually protects the measurement: nothing may be
    # written into, or around, the source run directory.
    if _overlaps(destination, source_run):
        return (
            "--output-root must be isolated from the source run directory; the "
            "source run is immutable"
        )

    # The enclosing output root is only knowable when the run sits in the
    # canonical `<output-root>/runs/<run>` layout. Deriving it unconditionally
    # from `parent.parent` invents an "output root" for any other layout — for a
    # run opened from an arbitrary directory that is its grandparent, which can
    # be the system temp directory — and would then refuse every destination
    # underneath it.
    source_root = (
        view.run_directory.parent.parent.resolve()
        if view.run_directory.parent.name == "runs"
        else None
    )
    if source_root is not None and _overlaps(destination, source_root):
        return (
            "--output-root must be isolated from the source run's output root; "
            "the source run is immutable"
        )

    scope = getattr(args, "scope", "resolved-subset")
    if scope == "repository" and not getattr(args, "repository", None):
        return "--scope repository requires at least one --repo"
    if scope == "full" and view.normalized_input is None:
        return (
            "--scope full requires normalized_input.csv; this run records only "
            "the resolved subset"
        )

    selected = selected_repositories(args, view)
    if not selected:
        return "the selected scope contains no repositories"
    if (
        len(selected) > LARGE_RUN_REPOSITORY_THRESHOLD
        and not getattr(args, "confirm_large_run", False)
    ):
        return (
            f"scope selects {len(selected)} repositories, above the "
            f"{LARGE_RUN_REPOSITORY_THRESHOLD} threshold; pass "
            f"--confirm-large-run to proceed"
        )
    return None


def execute(args, view, report) -> dict[str, Any]:
    """Re-execute the selected scope into an isolated output root.

    Arguments are reconstructed from validated fields. The original command line
    is never replayed: it may contain paths that no longer exist, or options
    whose meaning has changed between versions.
    """
    from modules.benchmark_runner import run_benchmark
    from modules.config import AnalysisConfig
    from modules.repository_input import RepositorySpec

    selected = selected_repositories(args, view)
    output_root = Path(args.output_root).resolve()
    # Observed before the run, so `output_root_created` reports what this
    # invocation actually did rather than what it intended to do.
    output_root_existed = output_root.exists()

    # A repository that never resolved has no entry in `analysis.json`, so
    # building its spec from that alone invented `architecture_type="unknown"`
    # and dropped its expected language — silently reproducing something other
    # than what the original run was asked to measure. The normalized-input
    # ledger is the artifact that still knows those values, so it is consulted
    # first for identity and the result is used only for the analyzed SHA.
    ledger: dict[str, Mapping[str, Any]] = {}
    for row in view.normalized_input or ():
        url = str(row.get("canonical_url") or "")
        if url:
            ledger[url] = row

    specs: list[RepositorySpec] = []
    for url in selected:
        result = view.repositories_by_url.get(url) or {}
        acquisition = result.get("acquisition") or {}
        recorded = ledger.get(url, {})
        architecture = (
            result.get("architecture_type")
            or recorded.get("architecture_metadata")
        )
        specs.append(RepositorySpec(
            url=url,
            architecture_type=str(architecture) if architecture else "unknown",
            expected_language=(
                result.get("expected_language") or recorded.get("expected_language")
            ),
            commit_sha=(
                acquisition.get("analyzed_commit_sha")
                or result.get("requested_commit_sha")
                or recorded.get("requested_sha")
            ),
            enabled=True,
        ))

    config = AnalysisConfig.from_env(
        output_root=output_root,
        workers=max(1, int(getattr(args, "workers", 1) or 1)),
    )

    summary: dict[str, Any] = {}
    execution_error: str | None = None
    try:
        summary = run_benchmark(
            input_paths=None,
            repository_specs=specs,
            config=config,
            acquisition_mode=args.mode,
            command_line_arguments=["metrolith", "reproduce", "--execute"],
        ) or {}
    except Exception as exc:
        # An aborted execution must still be reported truthfully — neither as a
        # silent success nor as a read-only preflight.
        execution_error = f"{type(exc).__name__}: {exc}"

    run_directory = summary.get("run_directory")
    payload = dict(report)
    payload.update({
        "executed": True,
        "execution_attempted": True,
        "execution_error": execution_error,
        "mode": args.mode,
        "scope": {
            "full": "full_normalized_population",
            "resolved-subset": "resolved_subset",
            "repository": "explicit_repository_selection",
        }[getattr(args, "scope", "resolved-subset")],
        "network_authorized": bool(getattr(args, "allow_network", False)),
        # What the reproduction run actually did, read back from its own
        # acquisition records. The flag only grants permission; it is not
        # evidence that the network was used.
        "network_used": _network_was_used(run_directory),
        "large_run_confirmed": bool(getattr(args, "confirm_large_run", False)),
        "workers": config.workers,
        "output_root": str(output_root),
        "output_root_created": (not output_root_existed) and output_root.exists(),
        "reproduction_run_directory": run_directory,
        "reproduction_status": summary.get("status"),
        "selected_repository_count": len(selected),
    })
    # Observed, never assumed: a reproduction run directory on disk is the
    # evidence that this invocation wrote something. Reporting a constant here
    # was how the command came to claim it "wrote nothing" after writing a
    # complete run tree.
    payload["wrote_anything"] = bool(
        run_directory and Path(run_directory).exists()
    )
    return payload


def _network_was_used(run_directory: str | None) -> bool | None:
    """Whether the reproduction run recorded contacting the network.

    ``None`` means the question is not evaluable — there is no run to inspect,
    or its artifacts do not record it. That is deliberately distinct from
    ``False``: "we did not observe network use" is not "no network was used".
    """
    if not run_directory:
        return None
    try:
        import json

        analysis = json.loads(
            (Path(run_directory) / "analysis.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    observed = [
        (item.get("acquisition") or {}).get("network_contacted")
        for item in analysis
        if isinstance(item, dict)
    ]
    recorded = [value for value in observed if isinstance(value, bool)]
    if not recorded:
        return None
    return any(recorded)


def _check(name: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
    return {"check": name, "outcome": outcome, "detail": detail}


def preflight(view) -> dict[str, Any]:
    """Assess one run. Performs no writes, no network access, and no metrics."""
    checks: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []

    def block(check: str, detail: str) -> None:
        blockers.append({"check": check, "detail": detail})

    # -- structural and schema validity ------------------------------------
    if view.structural_errors:
        checks.append(_check(
            "structural_validity", FAILED,
            f"{len(view.structural_errors)} structural error(s)",
        ))
        block("structural_validity", "run artifacts do not parse")
    else:
        checks.append(_check("structural_validity", PASSED))

    compatibility = view.compatibility
    if compatibility.readable:
        checks.append(_check(
            "artifact_schema_compatibility", PASSED, compatibility.reason
        ))
    else:
        checks.append(_check(
            "artifact_schema_compatibility", FAILED, compatibility.reason
        ))
        block("artifact_schema_compatibility", compatibility.reason)

    # -- lifecycle ----------------------------------------------------------
    # Lifecycle answers "can this run be decoded", not "did it succeed". Both
    # are checked, separately, because they genuinely come apart: a run whose
    # terminal state failed self-validation publishes `status: failed` while
    # every artifact that reached disk still decodes. Reproducing from such a
    # run would be reproducing from a run Metrolith itself rejected.
    if view.finalized:
        checks.append(_check("lifecycle", PASSED, view.lifecycle.value))
    else:
        checks.append(_check("lifecycle", FAILED, view.lifecycle.value))
        block("lifecycle", f"run is {view.lifecycle.value}, not finalized")

    # -- run integrity ------------------------------------------------------
    recorded_integrity = view.integrity_status
    if view.succeeded:
        checks.append(_check("run_integrity_status", PASSED, str(recorded_integrity)))
    elif recorded_integrity is None:
        checks.append(_check(
            "run_integrity_status", NOT_EVALUABLE, "no terminal status recorded"
        ))
        block("run_integrity_status", "the run recorded no terminal status")
    else:
        checks.append(_check("run_integrity_status", FAILED, recorded_integrity))
        block(
            "run_integrity_status",
            f"the run recorded integrity status {recorded_integrity!r}",
        )

    # -- program identity and contracts ------------------------------------
    manifest = view.manifest
    for name, recorded, current in (
        ("program_version", manifest.get("program_version"), PROGRAM_VERSION),
        ("metric_contract_version",
         manifest.get("metric_contract_version"), METRIC_CONTRACT_VERSION),
        ("inventory_schema_version",
         manifest.get("inventory_schema_version"), INVENTORY_SCHEMA_VERSION),
    ):
        if recorded is None:
            checks.append(_check(name, NOT_EVALUABLE, "not recorded in the manifest"))
        elif recorded == current:
            checks.append(_check(name, PASSED, str(recorded)))
        else:
            checks.append(_check(
                name, FAILED,
                f"run recorded {recorded!r}; this build is {current!r}",
            ))
            if name == "metric_contract_version":
                block(name, "metric values defined by a different contract")

    policy_hash = manifest.get("exclusion_policy_sha256")
    if policy_hash:
        try:
            from modules.config import POLICY_PATH
            import hashlib

            current_hash = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
            checks.append(_check(
                "exclusion_policy_sha256",
                PASSED if current_hash == policy_hash else FAILED,
                "policy bytes match" if current_hash == policy_hash
                else "exclusion policy bytes differ from the recorded hash",
            ))
        except OSError:
            checks.append(_check("exclusion_policy_sha256", NOT_EVALUABLE, "policy unreadable"))
    else:
        checks.append(_check("exclusion_policy_sha256", NOT_EVALUABLE, "not recorded"))

    # -- profiler revision --------------------------------------------------
    profiler_sha = view.environment.get("profiler_git_commit_sha")
    if profiler_sha:
        checks.append(_check("profiler_revision", PASSED, str(profiler_sha)))
    else:
        # This is what makes exact_environment_ready unreachable. It is a
        # limitation of the recorded run, not a defect in this check.
        checks.append(_check(
            "profiler_revision", NOT_EVALUABLE,
            "profiler_git_commit_sha is null; the original run has no immutable "
            "revision, so exact-environment reproduction cannot be claimed",
        ))

    # -- normalized input population ---------------------------------------
    normalized = view.normalized_input
    if normalized is None:
        checks.append(_check(
            "normalized_input_population", NOT_EVALUABLE,
            "normalized_input.csv is absent; only the resolved subset can be "
            "re-executed",
        ))
    else:
        checks.append(_check(
            "normalized_input_population", PASSED, f"{len(normalized)} accepted row(s)"
        ))

    # -- SHA availability ---------------------------------------------------
    missing_sha = [
        str(item.get("repository_url"))
        for item in view.repositories
        if not (item.get("acquisition") or {}).get("analyzed_commit_sha")
    ]
    if missing_sha:
        checks.append(_check(
            "analyzed_sha_availability", NOT_EVALUABLE,
            f"{len(missing_sha)} repository/repositories have no analyzed SHA",
        ))
    else:
        checks.append(_check("analyzed_sha_availability", PASSED))

    # -- qualification registry identity -----------------------------------
    # A qualified run is only reproducible AS a qualified run if the exact
    # registry bytes are available again. Without them the reproduction is an
    # ordinary generic analysis, or one with explicit unresolved qualification;
    # what it must never do is silently carry the original run's decisions
    # forward, because those were adjudicated against a specific revision and
    # scope that a reproduction has not yet re-established.
    if view.qualification_mode == "benchmark_qualified":
        registry = view.manifest.get("qualification_registry") or {}
        digest = registry.get("registry_sha256")
        if digest:
            checks.append(_check(
                "qualification_registry_identity", PASSED,
                f"source_id={registry.get('source_id')} sha256={digest}",
            ))
            checks.append(_check(
                "qualification_reuse_policy", ADVISORY,
                "reproducing this run as benchmark-qualified requires supplying "
                f"the identical registry ({digest}); without it the reproduction "
                "is generic or explicitly unresolved, and the original decisions "
                "are not reused",
            ))
        else:
            checks.append(_check(
                "qualification_registry_identity", FAILED,
                "the run declares benchmark-qualified mode but records no "
                "registry hash, so its decisions cannot be reproduced",
            ))
            block(
                "qualification_registry_identity",
                "qualified run records no reproducible registry identity",
            )
    else:
        checks.append(_check(
            "qualification_registry_identity", NOT_EVALUABLE,
            "run is not benchmark-qualified; there is no registry to carry",
        ))

    # -- offline cache readiness -------------------------------------------
    checks.append(_offline_cache_check(view))

    # -- output feasibility -------------------------------------------------
    checks.append(_check(
        "output_path_feasibility", ADVISORY,
        "reproduction would require a separate output root; writability is not "
        "proven here because preflight performs no writes",
    ))

    state = _assurance_state(
        blockers=blockers,
        readable=compatibility.readable and not view.structural_errors,
        has_normalized=normalized is not None,
        has_profiler_sha=bool(profiler_sha),
    )

    return {
        "reproduction_format_version": REPRODUCTION_FORMAT_VERSION,
        "run_id": view.run_id,
        "assurance_state": state,
        "executed": False,
        "execution_attempted": False,
        "execution_error": None,
        "mode": None,
        "scope": None,
        "network_authorized": False,
        "network_used": False,
        "large_run_confirmed": False,
        "workers": None,
        # Preflight really is read-only by contract, so this constant is a true
        # statement here. `execute()` replaces it with an observed value.
        "wrote_anything": False,
        "output_root": None,
        "output_root_created": False,
        "reproduction_run_directory": None,
        "reproduction_status": None,
        "blockers": blockers,
        "checks": checks,
    }


def _offline_cache_check(view) -> dict[str, Any]:
    """Verify cached Git objects with a read-only command.

    ``git cat-file -e`` is explicitly permitted (plan section 16.3): it inspects
    the object database and writes nothing.
    """
    cache_root = (view.manifest.get("effective_configuration") or {}).get("cache_root")
    if not cache_root:
        return _check("offline_cache_readiness", NOT_EVALUABLE, "no cache root recorded")

    cache = Path(cache_root)
    if not cache.is_dir():
        return _check(
            "offline_cache_readiness", NOT_EVALUABLE,
            "the recorded cache root does not exist on this machine",
        )

    # The cache root holds one bare mirror per repository (`r_<digest>.git`); it
    # is not itself a repository. Probing it directly made git exit 128 for
    # every SHA and reported every commit missing even when it was present.
    # `cache_path_for_url` is the same derivation acquisition uses, so the
    # mirror inspected here is the one a reproduction would actually read.
    from modules.acquisition import cache_path_for_url

    wanted = [
        (str(item.get("repository_url") or ""),
         (item.get("acquisition") or {}).get("analyzed_commit_sha"))
        for item in view.repositories
    ]
    wanted = [(url, sha) for url, sha in wanted if url and sha]
    if not wanted:
        return _check("offline_cache_readiness", NOT_EVALUABLE, "no analyzed SHAs recorded")

    missing: list[str] = []
    unresolved: list[str] = []
    for url, sha in wanted:
        mirror = cache_path_for_url(cache, url)
        if not mirror.is_dir():
            # No mirror for this repository: the question is unanswerable here,
            # not answered "no".
            unresolved.append(url)
            continue
        try:
            completed = subprocess.run(
                ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=str(mirror), capture_output=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return _check(
                "offline_cache_readiness", NOT_EVALUABLE,
                "git is unavailable, so cache readiness cannot be verified",
            )
        if completed.returncode != 0:
            # Deliberately not searched for in other mirrors: a commit sitting
            # in an unrelated repository's mirror does not make *this*
            # repository reproducible offline.
            missing.append(url)

    if missing:
        return _check(
            "offline_cache_readiness", FAILED,
            f"{len(missing)} of {len(wanted)} repository mirror(s) do not contain "
            f"their analyzed commit",
        )
    if unresolved:
        return _check(
            "offline_cache_readiness", NOT_EVALUABLE,
            f"{len(unresolved)} of {len(wanted)} repository/repositories have no "
            f"cache mirror on this machine",
        )
    return _check(
        "offline_cache_readiness", PASSED,
        f"all {len(wanted)} analyzed commit(s) present in their own mirror",
    )


def _assurance_state(
    *, blockers, readable: bool, has_normalized: bool, has_profiler_sha: bool
) -> str:
    if not readable:
        return STATE_INVALID
    if blockers:
        return STATE_NOT_REPRODUCIBLE
    if not has_normalized:
        # An older run without the ledger can only re-execute what resolved.
        return STATE_SUBSET
    if not has_profiler_sha:
        # Exact is unreachable without an immutable revision, however complete
        # everything else is.
        return STATE_COMPATIBLE
    return STATE_EXACT


def _tri_state(value: Any) -> str:
    """Render an observed boolean, keeping "not evaluable" distinct from "no"."""
    if value is None:
        return "not evaluable"
    return "yes" if value else "no"


def _execution_lines(payload: dict[str, Any]) -> list[str]:
    """Report what the execution actually did, from observed state."""
    lines = [
        "This invocation RE-EXECUTED the run. It was not read-only: it created "
        "worktrees, ran parsers, and wrote a new run directory.",
        "",
        "Execution:",
        f"  mode:                    {payload.get('mode')}",
        f"  scope:                   {payload.get('scope')}",
        f"  repositories selected:   {payload.get('selected_repository_count')}",
        f"  workers:                 {payload.get('workers')}",
        f"  network authorized:      {_tri_state(payload.get('network_authorized'))}",
        f"  network used (observed): {_tri_state(payload.get('network_used'))}",
        f"  wrote anything:          {_tri_state(payload.get('wrote_anything'))}",
        f"  output root:             {payload.get('output_root')}",
        f"  output root created:     {_tri_state(payload.get('output_root_created'))}",
        f"  reproduction run:        {payload.get('reproduction_run_directory') or 'none'}",
        f"  reproduction status:     {payload.get('reproduction_status') or 'none'}",
    ]
    if payload.get("execution_error"):
        lines.extend([
            "",
            f"  execution FAILED: {payload['execution_error']}",
        ])
    lines.append("")
    lines.append(
        "The checks below describe the ORIGINAL run's reproducibility, assessed "
        "before execution. They are not a verdict on the reproduction."
    )
    return lines


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Metrolith reproduce preflight {payload['reproduction_format_version']}")
    lines.append(f"Run: {payload['run_id']}")
    lines.append(f"Assurance state: {payload['assurance_state']}")
    lines.append("")
    if payload.get("execution_attempted"):
        # The read-only paragraph below is a statement of fact about this
        # invocation, so it must never be printed after an execution. It once
        # was, and claimed nothing had been written while a complete run tree
        # sat on disk.
        lines.extend(_execution_lines(payload))
    else:
        lines.append(
            "This preflight is read-only. It wrote nothing, opened no network "
            "connection, created no worktree, and ran no parser."
        )
    lines.append("")
    lines.append("Checks:")
    for item in payload["checks"]:
        detail = f" — {item['detail']}" if item["detail"] else ""
        lines.append(f"  [{item['outcome']:<14}] {item['check']}{detail}")

    if payload["blockers"]:
        lines.append("")
        lines.append("Blockers:")
        for item in payload["blockers"]:
            lines.append(f"  - {item['check']}: {item['detail']}")
    return "\n".join(lines)
