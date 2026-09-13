"""Common monolith/microservices analysis pipeline for immutable benchmark runs."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from modules.acquisition import (
    AcquisitionError,
    acquire_repository,
    cleanup_stale_cache_staging,
)
from modules.classify import classify_application
from modules import preflight
from modules.subject import subject_key_of
from modules.config import (
    GIT_CHECKOUT_CONFIGURATION,
    COMPLEXITY_CONTRACT_VERSION,
    METRIC_CONTRACT_VERSION,
    AnalysisConfig,
    PROJECT_ROOT,
    SUPPORTED_LANGUAGES,
    SUPPORTED_PYTHON_MINOR,
    supported_python_version,
)
from modules.core_metrics import (
    ParserRegistry,
    compatibility_static_result,
    compute_repository_metrics,
    validate_parser_initialization,
)
from modules.coverage import compute_coverage
from modules.db_analysis import detect_db_schema
from modules.deployability import assess_deployability
from modules.endpoints import extract_endpoints
from modules.inventory import RepositoryInventory
from modules.inventory_audits import collect_inventory_audits
from modules.metadata import fetch_metadata
from modules.message_brokers import detect_message_brokers
from modules.metric_warnings import assess_metric_warnings, detect_framework_markers
from modules.repository_input import (
    RepositorySpec,
    input_sha256,
    load_legacy_txt,
    load_repositories_csv,
)
from modules.run_artifacts import RunArtifacts, utc_now


_STATUS_RANK = {
    "not_applicable": 0,
    "complete": 1,
    "partial": 2,
    "failed": 3,
}
_EXPECTED_LANGUAGE_FAMILIES = {
    "java": ("java",),
    "javascript": ("javascript", "typescript"),
    "typescript": ("javascript", "typescript"),
    "python": ("python",),
    "go": ("go",),
}


def derive_repository_diagnostics(result: dict[str, Any]) -> dict[str, str]:
    """Derive expected-family status and the provenance of incomplete analysis."""
    expected = str(result.get("expected_language") or "").strip().casefold()
    family = _EXPECTED_LANGUAGE_FAMILIES.get(expected)
    metrics = result.get("metrics") or {}
    by_language = metrics.get("by_language") or {}

    applicable_family_rows: list[dict[str, Any]] = []
    if family:
        applicable_family_rows = [
            row
            for language in family
            if isinstance((row := by_language.get(language)), dict)
            and int(row.get("source_files") or 0) > 0
        ]
    if not family:
        expected_status = "not_applicable"
    elif applicable_family_rows:
        expected_status = max(
            (
                str(row.get("metric_status") or "not_applicable")
                for row in applicable_family_rows
            ),
            key=lambda status: _STATUS_RANK.get(status, 3),
        )
    elif result.get("analysis_status") in {"failed", "interrupted"}:
        expected_status = "failed"
    else:
        expected_status = "not_applicable"

    if result.get("analysis_status") == "complete":
        partial_origin = "none"
    else:
        contributors: set[str] = set()
        errors = result.get("errors") or []
        aggregate = metrics.get("aggregate") or {}
        if (
            any(
                error.get("module") in {"acquisition", "inventory"}
                for error in errors
                if isinstance(error, dict)
            )
            or aggregate.get("inventory_status") in {"partial", "failed"}
            or aggregate.get("source_files_status") in {"partial", "failed"}
        ):
            contributors.add("acquisition_or_inventory")
        if applicable_family_rows and expected_status in {"partial", "failed"}:
            contributors.add("expected_language_family")
        outside_family = set(by_language) - set(family or ())
        if any(
            isinstance(by_language.get(language), dict)
            and int(by_language[language].get("source_files") or 0) > 0
            and by_language[language].get("metric_status") in {"partial", "failed"}
            for language in outside_family
        ):
            contributors.add("secondary_supported_language_only")
        if not contributors and result.get("analysis_status") != "complete":
            # Valid metrics-mode results are covered above. Keep any defensive
            # legacy/all-mode remainder inside the agreed closed taxonomy.
            contributors.add("multiple")
        partial_origin = (
            next(iter(contributors)) if len(contributors) == 1 else "multiple"
        )
    return {
        "expected_language_family_status": expected_status,
        "partial_origin": partial_origin,
    }



def _identity(spec):
    """Resolve the Artifact 1.7 subject identity for one spec.

    Cached per spec so repeated reads during result construction cannot produce
    two different keys for the same subject.
    """
    from modules.subject import resolve_subject_identity
    from modules.vocabularies import SourceMode

    cached = getattr(spec, "_resolved_identity", None)
    if cached is not None:
        return cached

    if getattr(spec, "is_local", False):
        from modules.local_source import classify_local_source

        mode = classify_local_source(Path(spec.local_path), revision=spec.revision)
        identity = resolve_subject_identity(
            source_mode=mode,
            explicit_subject_key=spec.subject_key,
            repository_url=spec.url or None,
            local_path=Path(spec.local_path),
        )
    else:
        identity = resolve_subject_identity(
            source_mode=SourceMode.REMOTE_GIT_REVISION,
            explicit_subject_key=getattr(spec, "subject_key", None),
            repository_url=spec.url,
        )
    try:
        object.__setattr__(spec, "_resolved_identity", identity)
    except Exception:  # pragma: no cover - slots dataclass
        pass
    return identity


@contextmanager
def _acquire_for(spec, config, mode, progress):
    """Route to local or remote acquisition, yielding the same shape.

    Both paths continue into the identical inventory, measurement and artifact
    machinery; `analyze` never gets a second pipeline.
    """
    if getattr(spec, "is_local", False):
        from modules.local_source import acquire_local_repository

        with acquire_local_repository(spec, config, mode, progress) as prepared:
            yield prepared
    else:
        with acquire_repository(spec, config, mode=mode, progress=progress) as acquired:
            yield acquired, None

def discover_required_capabilities(
    labelled_specs, config, acquisition_mode
) -> dict[str, set[str]]:
    """Aggregate the parser capabilities a planned measurement will need.

    ``labelled_specs`` is an iterable of ``(label, spec)``; the label is what a
    refusal reports as the requiring subject. Each subject is acquired and
    discovered with the canonical **parser-free** inventory pass, so the
    requirement set is exactly the one the measurement path would produce
    rather than a second source-selection implementation that could drift.

    Shared by the cohort barrier and by ``diff``'s two-side barrier: both need
    every requirement known before *any* side parses a file, or one side is
    measured and the next discovers a missing grammar.

    Discovery failure is deliberately not a capability refusal. The normal
    measurement path records acquisition and inventory failures as repository
    results with full evidence; pre-empting that here would replace a detailed
    result with a bare exception.
    """
    required: dict[str, set[str]] = {}
    for label, spec in labelled_specs:
        try:
            with _acquire_for(spec, config, acquisition_mode, None) as prepared:
                acquired, _snapshot = prepared
                discovered = RepositoryInventory(
                    acquired.path, config, full_inventory=False,
                    **(_snapshot.inventory_options if _snapshot is not None else {}),
                )
                for capability, _languages in preflight.required_parser_capabilities(
                    discovered
                ).items():
                    required.setdefault(capability, set()).add(label)
        except Exception:
            continue
    return required


def _failed_metrics() -> dict[str, Any]:
    fields = {
        "code_lines": None,
        "comment_lines": None,
        "blank_lines": None,
        "nonblank_lines": None,
        "total_physical_lines": None,
        "lines_of_code": None,
        "source_files": None,
        "classes_structs": None,
        "methods_functions": None,
        "metric_status": "failed",
        "inventory_status": "failed",
        "source_files_status": "failed",
        "loc_status": "failed",
        "classes_structs_status": "failed",
        "methods_functions_status": "failed",
        "source_files_readable": 0,
        "source_files_loc_analyzed": 0,
        "source_files_entity_parsed": 0,
        "source_files_failed_read": 0,
        "source_files_oversized": 0,
        "source_files_partial_parse": 0,
        "source_files_failed_parse": 0,
        "source_files_recovered_parse": 0,
        "parse_failure_count": 0,
        "parse_failure_files": [],
    }
    return {
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "aggregate": dict(fields),
        "by_language": {
            language.lower(): {
                **fields,
                "source_files": 0,
                "metric_status": "not_applicable",
                "inventory_status": "not_applicable",
                "source_files_status": "not_applicable",
                "loc_status": "not_applicable",
                "classes_structs_status": "not_applicable",
                "methods_functions_status": "not_applicable",
            }
            for language in SUPPORTED_LANGUAGES
        },
        "primary_language": dict(fields),
        "primary_language_name": None,
        "expected_language_mismatch": False,
        "primary_language_tie": False,
        "source_files_total": None,
        "source_files_by_language": {language.lower(): 0 for language in SUPPORTED_LANGUAGES},
        "parser_status_by_language": {language.lower(): "not_applicable" for language in SUPPORTED_LANGUAGES},
        "parser_status": "failed",
        "parse_errors": [],
        "parser_diagnostics": [],
        "recovered_parser_diagnostics": [],
        "recovery_taxonomy": {},
        "javascript_family_scope": {
            "contract": "javascript_typescript_family_aggregate",
            "extensions": [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"],
            "inventoried_by_extension": {},
            "included_by_extension": {},
            "excluded_by_extension": {},
            "grammar_by_extension": {
                ".js": "javascript", ".jsx": "javascript_jsx_capable",
                ".mjs": "javascript", ".cjs": "javascript",
                ".ts": "typescript", ".tsx": "tsx",
                ".mts": "typescript", ".cts": "typescript",
            },
            "scope_status": "failed",
        },
        "error_taxonomy": {},
        "malformed_ast_nodes": [],
        "warnings": [],
        "definitions": {
            "lines_of_code": "physical source lines containing code, excluding blank and comment-only lines",
            "classes_structs": "unavailable because repository acquisition or metric extraction failed",
            "methods_functions": "unavailable because repository acquisition or metric extraction failed",
        },
    }


def _error(
    result: dict[str, Any],
    logger,
    module: str,
    error_type: str,
    message: str,
    details: str | None = None,
    diagnostic_fields: dict[str, Any] | None = None,
) -> None:
    category = (
        "acquisition_failure"
        if module == "acquisition" and error_type != "checkout_not_clean"
        else error_type
    )
    item = {
        # Artifact 1.7 makes `subject_key` the join key and non-nullable on every
        # row, `repository_url` a nullable locator. An error row belongs to the
        # same subject as the result that produced it, so it takes that result's
        # key rather than being left for a reader to re-derive from the URL --
        # which is impossible for a local subject that has no URL at all.
        "subject_key": subject_key_of(result),
        "repository_url": result["repository_url"],
        "analyzed_commit_sha": result.get("acquisition", {}).get("analyzed_commit_sha"),
        "module": module,
        "severity": "ERROR",
        "error_type": error_type,
        "error_category": category,
        "message": message,
    }
    if diagnostic_fields:
        item.update(diagnostic_fields)
    result["errors"].append(item)
    logger.log(
        "ERROR",
        module,
        message,
        repository_url=result["repository_url"],
        commit_sha=item["analyzed_commit_sha"],
        error_type=error_type,
        exception_details=details,
    )


def _show_info(config: AnalysisConfig) -> bool:
    return config.log_level in {"DEBUG", "INFO"}


def _local_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _terminal(config: AnalysisConfig, message: str) -> None:
    if _show_info(config):
        print(f"[{_local_timestamp()}] {message}", flush=True)


def _progress(
    config: AnalysisConfig,
    logger,
    module: str,
    message: str,
    repository_url: str | None = None,
    repository_name: str | None = None,
) -> None:
    if repository_name:
        _terminal(config, f"[{repository_name}] [{module}] {message}")
    else:
        _terminal(config, f"[{module}] {message}")
    logger.log("INFO", module, message, repository_url=repository_url)


def _call_with_heartbeat(
    config: AnalysisConfig,
    logger,
    module: str,
    function: Callable[[], Any],
    repository_url: str,
    repository_name: str,
    *,
    warning_seconds: float,
    details: Callable[[], str] | None = None,
) -> Any:
    """Run synchronously while a daemon only reports honest elapsed-time warnings."""
    stopped = threading.Event()
    started = time.perf_counter()

    def heartbeat() -> None:
        if stopped.wait(warning_seconds):
            return
        while not stopped.is_set():
            elapsed = time.perf_counter() - started
            suffix = f"; {details()}" if details and details() else ""
            _progress(
                config,
                logger,
                module,
                f"still running; elapsed {elapsed:.0f}s{suffix}",
                repository_url,
                repository_name,
            )
            if stopped.wait(config.heartbeat_interval_seconds):
                return

    reporter = threading.Thread(
        target=heartbeat,
        name=f"metrolith-heartbeat-{repository_name}-{module}",
        daemon=True,
    )
    reporter.start()
    try:
        return function()
    finally:
        stopped.set()
        reporter.join(timeout=0.2)


def _optional_module(
    result: dict[str, Any],
    config: AnalysisConfig,
    logger,
    status_name: str,
    function: Callable[[], Any],
    default: Any,
    *,
    fail_fast: bool,
    display_name: str | None = None,
) -> Any:
    repository_url = result["repository_url"]
    repository_name = result["repository_name"]
    display_name = display_name or status_name
    started = time.perf_counter()
    _progress(
        config,
        logger,
        f"auxiliary:{display_name}",
        "started",
        repository_url,
        repository_name,
    )
    status = "failed"
    try:
        value = _call_with_heartbeat(
            config,
            logger,
            f"auxiliary:{display_name}",
            function,
            repository_url,
            repository_name,
            warning_seconds=config.auxiliary_warning_seconds,
        )
        status = "complete"
        return value
    except Exception as exc:
        status = "failed"
        _error(
            result,
            logger,
            status_name,
            "optional_module_failure",
            f"{type(exc).__name__}: {exc}",
            traceback.format_exc(),
        )
        if fail_fast:
            raise
        return default
    finally:
        duration = round(time.perf_counter() - started, 6)
        result["module_statuses"][status_name] = status
        result["timings"]["auxiliary"][status_name] = {
            "status": status,
            "duration_seconds": duration,
        }
        _progress(
            config,
            logger,
            f"auxiliary:{display_name}",
            f"{'completed' if status == 'complete' else status} in {duration:.1f}s",
            repository_url,
            repository_name,
        )


def _analyze_spec(
    spec: RepositorySpec,
    config: AnalysisConfig,
    mode: str,
    logger,
    repository_index: int,
    repository_total: int,
    fail_fast: bool = False,
    execution_mode: str = "metrics",
    checkpoint_writer: Callable[[dict[str, Any], str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    repository_started_at = utc_now()
    repository_started_clock = time.perf_counter()
    inventory: RepositoryInventory | None = None
    _terminal(
        config,
        f"[{repository_index}/{repository_total}] {spec.repository_name} "
        f"[{spec.architecture_type}]",
    )
    auxiliary_initial_status = (
        "skipped_by_execution_mode" if execution_mode == "metrics" else "not_run"
    )
    result: dict[str, Any] = {
        "product_name": "Metrolith",
        "distribution_name": "metrolith",
        "program_version": config.program_version,
        "artifact_schema_version": config.artifact_schema_version,
        # Artifact 1.7 identity model. `subject_key` is the logical join key;
        # `repository_url` is a nullable source locator and is no longer the
        # hidden primary key.
        "subject_key": _identity(spec).subject_key,
        "subject_key_basis": _identity(spec).subject_key_basis.value,
        "subject_key_portable": _identity(spec).portable,
        "source_mode": _identity(spec).source_mode.value,
        "repository_url": _identity(spec).repository_url,
        "repository_owner": spec.owner,
        "repository_name": spec.repository_name,
        "architecture_type": spec.architecture_type,
        "architecture_type_source": "benchmark_input",
        "expected_language": spec.expected_language,
        "expected_language_family_status": "not_applicable",
        "partial_origin": "none",
        "requested_commit_sha": spec.commit_sha,
        "notes": spec.notes,
        "input_file": spec.input_file,
        "input_line": spec.input_line,
        "execution_mode": execution_mode,
        "metric_contract_version": config.metric_contract_version,
        # Versioned separately from Metric Contract: a complexity-contract
        # difference must not make the four benchmark metrics incomparable.
        "complexity_contract_version": COMPLEXITY_CONTRACT_VERSION,
        "exclusion_policy_version": config.exclusion_policy_version,
        "inventory_schema_version": config.inventory_schema_version,
        "repository_start_timestamp": repository_started_at,
        "module_statuses": {
            "core_metrics": "failed",
            "db_analysis": auxiliary_initial_status,
            "endpoint_analysis": auxiliary_initial_status,
            "deployability": auxiliary_initial_status,
            "framework_analysis": auxiliary_initial_status,
            "coverage_analysis": auxiliary_initial_status,
            "classification": auxiliary_initial_status,
            "metadata": auxiliary_initial_status,
            "message_broker_analysis": auxiliary_initial_status,
            "audits": auxiliary_initial_status,
        },
        "timings": {
            "acquisition_seconds": None,
            "worktree_seconds": None,
            "inventory_seconds": None,
            "core_metrics_seconds": None,
            "auxiliary": {},
            "cleanup_seconds": None,
            "total_repository_seconds": None,
        },
        "errors": [],
    }
    acquisition_started = time.perf_counter()
    _progress(
        config,
        logger,
        "acquisition",
        "Repository acquisition started",
        spec.url,
        spec.repository_name,
    )

    def acquisition_progress(message: str) -> None:
        if message.startswith("worktree") or message.startswith("creating detached"):
            module = "worktree"
        elif message.startswith("cleanup"):
            module = "cleanup"
        else:
            module = "acquisition"
        _progress(config, logger, module, message, spec.url, spec.repository_name)

    try:
        with _acquire_for(spec, config, mode, acquisition_progress) as prepared:
            acquired, local_snapshot = prepared
            result["acquisition"] = acquired.record.to_dict()
            if local_snapshot is not None:
                result["working_tree_state"] = local_snapshot.working_tree_state.value
                result["local_source_evidence"] = {
                    "tracked_file_count": local_snapshot.tracked_file_count,
                    "untracked_included_count": local_snapshot.untracked_included_count,
                    "modified_tracked_count": local_snapshot.modified_tracked_count,
                    "git_ignored_excluded": local_snapshot.ignored_excluded,
                }
            sha = acquired.record.analyzed_commit_sha
            result["timings"]["acquisition_seconds"] = (
                acquired.record.acquisition_duration_seconds
            )
            result["timings"]["worktree_seconds"] = (
                acquired.record.worktree_duration_seconds
            )
            _progress(
                config,
                logger,
                "acquisition",
                f"completed at {sha[:12]} in {time.perf_counter() - acquisition_started:.1f}s",
                spec.url,
                spec.repository_name,
            )

            if execution_mode == "metrics":
                result["metadata"] = {
                    "status": "skipped_by_execution_mode",
                    "reason": "Core metrics mode does not request live metadata",
                }
            elif not acquired.record.remote_checked:
                result["module_statuses"]["metadata"] = "skipped_offline"
                result["metadata"] = {
                    "status": "skipped_offline",
                    "reason": "offline mode does not contact the remote",
                }
            else:
                result["metadata"] = {"status": "not_run"}

            inventory_started = time.perf_counter()
            _progress(
                config,
                logger,
                "inventory",
                "scanning repository",
                spec.url,
                spec.repository_name,
            )
            inventory_progress: dict[str, int] = {}

            def update_inventory_progress(values: dict[str, int]) -> None:
                inventory_progress.clear()
                inventory_progress.update(values)

            def inventory_details() -> str:
                return ", ".join(
                    f"{key}={inventory_progress.get(key, 0)}"
                    for key in (
                        "directories_seen", "files_seen", "source_candidates", "files_hashed"
                    )
                )

            inventory = _call_with_heartbeat(
                config,
                logger,
                "inventory",
                lambda: RepositoryInventory(
                    acquired.path,
                    config,
                    full_inventory=execution_mode == "all" or config.full_inventory,
                    progress=update_inventory_progress,
                    **(local_snapshot.inventory_options if local_snapshot is not None else {}),
                ),
                spec.url,
                spec.repository_name,
                warning_seconds=config.heartbeat_interval_seconds,
                details=inventory_details,
            )
            inventory_summary = inventory.summary()
            # The comparability anchor: the exact source scope whose bytes can
            # affect the metrics. Derived from the canonical inventory, so it
            # covers precisely the files the measurement path will read.
            from modules.subject import (
                ANALYSIS_SCOPE_HASH_VERSION,
                compute_analysis_scope_hash,
                compute_filesystem_manifest_hash,
            )

            result["analysis_scope_hash"] = compute_analysis_scope_hash(inventory)
            result["analysis_scope_hash_version"] = ANALYSIS_SCOPE_HASH_VERSION
            # Wider evidence, explicitly *not* the comparability identity: it
            # moves when an excluded file changes, which cannot affect a metric.
            result["filesystem_manifest_hash"] = compute_filesystem_manifest_hash(
                inventory
            )
            result.update(
                {
                    "git_mode_map_status": inventory.git_mode_map_status,
                    "git_mode_map_reason": inventory.git_mode_map_reason,
                    "git_mode_map_available": inventory.git_mode_map_available,
                    "git_mode_map_error": inventory.git_mode_map_error,
                    "git_mode_entry_count": inventory.git_mode_entry_count,
                    "git_mode_command_duration_seconds": (
                        inventory.git_mode_command_duration_seconds
                    ),
                    "git_mode_effective_timeout_seconds": (
                        inventory.git_mode_effective_timeout_seconds
                    ),
                }
            )
            if inventory.git_mode_map_status in {"failed", "timed_out"}:
                _error(
                    result,
                    logger,
                    "inventory",
                    "git_mode_map_" + inventory.git_mode_map_status,
                    inventory.git_mode_map_error
                    or "Git mode map was not available",
                    diagnostic_fields={
                        "git_mode_map_status": inventory.git_mode_map_status,
                        "git_mode_map_available": inventory.git_mode_map_available,
                        "git_mode_map_error": inventory.git_mode_map_error,
                        "git_mode_entry_count": inventory.git_mode_entry_count,
                        "git_mode_command_duration_seconds": (
                            inventory.git_mode_command_duration_seconds
                        ),
                        "git_mode_effective_timeout_seconds": (
                            inventory.git_mode_effective_timeout_seconds
                        ),
                    },
                )
            result["timings"]["inventory_seconds"] = round(
                time.perf_counter() - inventory_started, 6
            )
            _progress(
                config,
                logger,
                "inventory",
                f"{inventory_summary['total_files_discovered']} files discovered, "
                f"{inventory_summary['source_files_included']} source files included "
                f"in {result['timings']['inventory_seconds']:.1f}s",
                spec.url,
                spec.repository_name,
            )
            for directory_error in inventory.directory_errors:
                _error(
                    result,
                    logger,
                    "inventory",
                    "unreadable_directory",
                    f"{directory_error['relative_path']}: {directory_error['message']}",
                )
            if fail_fast and inventory.directory_errors:
                raise RuntimeError("Inventory traversal was incomplete and --fail-fast is enabled")
            try:
                metric_started = time.perf_counter()
                _progress(
                    config,
                    logger,
                    "metrics",
                    "core metric extraction started",
                    spec.url,
                    spec.repository_name,
                )
                metrics = compute_repository_metrics(
                    inventory,
                    expected_language=spec.expected_language,
                    parser_registry=ParserRegistry(),
                    progress=lambda message: _progress(
                        config,
                        logger,
                        "metrics",
                        message,
                        spec.url,
                        spec.repository_name,
                    ),
                )
                result["metrics"] = metrics
                result["static"] = compatibility_static_result(metrics)
                result["module_statuses"]["core_metrics"] = metrics["aggregate"]["metric_status"]
                for diagnostic in metrics["parser_diagnostics"]:
                    diagnostic["repository_url"] = spec.url
                    diagnostic["analyzed_commit_sha"] = sha
                    diagnostic_fields = dict(diagnostic)
                    repository_statuses = diagnostic.get(
                        "final_repository_metric_statuses"
                    ) or {}
                    diagnostic_fields.update(
                        {
                            f"repository_{key}": value
                            for key, value in repository_statuses.items()
                        }
                    )
                    _error(
                        result,
                        logger,
                        "core_metrics",
                        diagnostic["error_category"],
                        f"{diagnostic['file_path']}: {diagnostic['message']}",
                        diagnostic_fields=diagnostic_fields,
                    )
                for diagnostic in metrics["recovered_parser_diagnostics"]:
                    # Same Artifact 1.7 requirement as error rows: recoveries.csv
                    # declares `subject_key` non-nullable, so the identity is
                    # stamped here rather than left empty for finalization to
                    # reject.
                    diagnostic["subject_key"] = subject_key_of(result)
                    diagnostic["repository_url"] = spec.url
                    diagnostic["analyzed_commit_sha"] = sha
                if metrics.get("expected_language_mismatch"):
                    warning = metrics["warnings"][0]
                    logger.log(
                        "WARNING",
                        "core_metrics",
                        warning,
                        repository_url=spec.url,
                        commit_sha=sha,
                        error_type="expected_language_mismatch",
                    )
                    _progress(
                        config,
                        logger,
                        "metrics",
                        f"warning: {warning}",
                        spec.url,
                        spec.repository_name,
                    )
                result["timings"]["core_metrics_seconds"] = round(
                    time.perf_counter() - metric_started, 6
                )
                _progress(
                    config,
                    logger,
                    "metrics",
                    f"completed in {result['timings']['core_metrics_seconds']:.1f}s",
                    spec.url,
                    spec.repository_name,
                )
            except Exception as exc:
                result["timings"]["core_metrics_seconds"] = round(
                    time.perf_counter() - metric_started, 6
                )
                result["metrics"] = _failed_metrics()
                result["static"] = compatibility_static_result(result["metrics"])
                _error(
                    result,
                    logger,
                    "core_metrics",
                    "metric_extraction_failure",
                    f"{type(exc).__name__}: {exc}",
                    traceback.format_exc(),
                )

            result["inventory_summary"] = inventory.summary()
            result["performance_diagnostics"] = inventory.performance_counters()
            result["included_source_manifest"] = inventory.included_source_manifest()
            result["core_metric_status"] = result["metrics"]["aggregate"]["metric_status"]
            result["analysis_status"] = (
                "failed"
                if result["core_metric_status"] == "failed"
                else "partial"
                if result["core_metric_status"] == "partial"
                else "complete"
            )
            result["checkpoint_status"] = (
                "core_failed"
                if result["core_metric_status"] == "failed"
                else "core_complete"
            )
            result.update(derive_repository_diagnostics(result))
            if checkpoint_writer:
                checkpoint_writer(result, result["checkpoint_status"])

            auxiliary_names = (
                "db_analysis",
                "endpoint_analysis",
                "deployability",
                "framework_analysis",
                "coverage_analysis",
                "message_broker_analysis",
                "classification",
                "audits",
            )
            if execution_mode == "metrics":
                result.update(
                    {
                        "endpoints": [],
                        "deployability": {},
                        "db_analysis": {},
                        "message_brokers": {},
                        "coverage": {},
                        "framework_markers": [],
                        "audits": {},
                        "classification": None,
                        "metric_warnings": {
                            "metric_warning": False,
                            "metric_warning_reason": "",
                            "status": "skipped_by_execution_mode",
                        },
                    }
                )
                for name in auxiliary_names:
                    result["timings"]["auxiliary"][name] = {
                        "status": "skipped_by_execution_mode",
                        "duration_seconds": 0.0,
                    }
            else:
                if acquired.record.remote_checked:
                    metadata = _optional_module(
                        result,
                        config,
                        logger,
                        "metadata",
                        lambda: fetch_metadata(spec.url),
                        {},
                        fail_fast=fail_fast,
                    )
                    if isinstance(metadata, dict) and metadata.get("error"):
                        result["module_statuses"]["metadata"] = "failed"
                        _error(
                            result,
                            logger,
                            "metadata",
                            "metadata_fetch_failure",
                            metadata["error"],
                        )
                    result["metadata"] = metadata
                result["db_analysis"] = _optional_module(
                    result,
                    config,
                    logger,
                    "db_analysis",
                    lambda: detect_db_schema(acquired.path, inventory=inventory),
                    {},
                    fail_fast=fail_fast,
                    display_name="db",
                )
                result["endpoints"] = _optional_module(
                    result,
                    config,
                    logger,
                    "endpoint_analysis",
                    lambda: extract_endpoints(acquired.path, inventory=inventory),
                    [],
                    fail_fast=fail_fast,
                    display_name="endpoints",
                )
                result["deployability"] = _optional_module(
                    result,
                    config,
                    logger,
                    "deployability",
                    lambda: assess_deployability(acquired.path, inventory=inventory),
                    {},
                    fail_fast=fail_fast,
                )
                framework_markers = _optional_module(
                    result,
                    config,
                    logger,
                    "framework_analysis",
                    lambda: detect_framework_markers(acquired.path, inventory=inventory),
                    [],
                    fail_fast=fail_fast,
                    display_name="framework",
                )
                result["framework_markers"] = framework_markers
                result["coverage"] = _optional_module(
                    result,
                    config,
                    logger,
                    "coverage_analysis",
                    lambda: compute_coverage(acquired.path, inventory=inventory),
                    {},
                    fail_fast=fail_fast,
                    display_name="coverage",
                )
                result["message_brokers"] = _optional_module(
                    result,
                    config,
                    logger,
                    "message_broker_analysis",
                    lambda: detect_message_brokers(acquired.path, inventory=inventory),
                    {},
                    fail_fast=fail_fast,
                    display_name="broker",
                )
                classification_input = {
                    "metadata": result["metadata"],
                    "static": result["static"],
                    "deployability": result["deployability"],
                    "db_schema": result["db_analysis"],
                    "coverage": result["coverage"],
                    "endpoints": result["endpoints"],
                }
                result["classification"] = _optional_module(
                    result,
                    config,
                    logger,
                    "classification",
                    lambda: classify_application(classification_input),
                    None,
                    fail_fast=fail_fast,
                )
                result["audits"] = _optional_module(
                    result,
                    config,
                    logger,
                    "audits",
                    lambda: collect_inventory_audits(inventory, result["metrics"]),
                    {},
                    fail_fast=fail_fast,
                )
                result["static"].update(result["audits"])
                result["metric_warnings"] = assess_metric_warnings(
                    result["static"], result["endpoints"], framework_markers
                )
            result["inventory_summary"] = inventory.summary()
            result["performance_diagnostics"] = inventory.performance_counters()
            inventory_data = inventory.to_dict()
            core_status = result["metrics"]["aggregate"]["metric_status"]
            if core_status == "failed":
                result["analysis_status"] = "failed"
            elif result["errors"] or core_status == "partial":
                result["analysis_status"] = "partial"
            else:
                result["analysis_status"] = "complete"
            result["core_metric_status"] = core_status
            for name in (
                "db_analysis", "endpoint_analysis", "deployability",
                "framework_analysis", "coverage_analysis", "classification", "audits",
            ):
                result[f"{name}_status"] = result["module_statuses"][name]
            result["message_broker_analysis_status"] = result["module_statuses"]["message_broker_analysis"]
            result["metadata_status"] = result["module_statuses"]["metadata"]
            result["checkpoint_status"] = (
                "metrics_complete" if execution_mode == "metrics" else "full_complete"
            )
            result.update(derive_repository_diagnostics(result))
            if checkpoint_writer:
                checkpoint_writer(result, result["checkpoint_status"])
            logger.log(
                "INFO",
                "analysis",
                f"Repository analysis {result['analysis_status']}",
                repository_url=spec.url,
                commit_sha=sha,
            )
            _progress(
                config,
                logger,
                "output",
                "repository result prepared",
                spec.url,
                spec.repository_name,
            )
        result["acquisition"] = acquired.record.to_dict()
        result["timings"]["cleanup_seconds"] = acquired.record.cleanup_duration_seconds
        if acquired.record.cleanup_status == "failed":
            _error(
                result,
                logger,
                "acquisition",
                "worktree_cleanup_failure",
                "; ".join(acquired.record.cleanup_errors),
            )
            if result["analysis_status"] == "complete":
                result["analysis_status"] = "partial"
        result["repository_end_timestamp"] = utc_now()
        result["repository_duration_seconds"] = round(
            time.perf_counter() - repository_started_clock, 6
        )
        result["timings"]["total_repository_seconds"] = result[
            "repository_duration_seconds"
        ]
        if result["repository_duration_seconds"] > config.repository_warning_threshold_seconds:
            message = (
                f"repository duration {result['repository_duration_seconds']:.1f}s exceeded warning "
                f"threshold {config.repository_warning_threshold_seconds}s"
            )
            logger.log("WARNING", "analysis", message, repository_url=spec.url)
            _progress(
                config,
                logger,
                "analysis",
                f"warning: {message}",
                spec.url,
                spec.repository_name,
            )
        result["checkpoint_status"] = "complete"
        result.update(derive_repository_diagnostics(result))
        if checkpoint_writer:
            checkpoint_writer(result, "complete")
        return result, inventory_data
    except AcquisitionError as exc:
        acquisition_commands = exc.commands or ([exc.command] if exc.command else [])
        network_contacted = any(
            "ls-remote" in command or "fetch" in command
            for command in acquisition_commands
        )
        fetch_performed = any("fetch" in command for command in acquisition_commands)
        result["metrics"] = _failed_metrics()
        result["static"] = compatibility_static_result(result["metrics"])
        result["acquisition"] = {
            "repository_url": spec.url,
            "requested_commit_sha": spec.commit_sha,
            "analyzed_commit_sha": None,
            "acquisition_mode": "frozen" if spec.commit_sha and mode == "latest" else mode,
            "remote_checked": network_contacted,
            "commit_verification_status": "failed",
            "cache_hit": False,
            "cache_created": False,
            "cache_updated": False,
            "network_contacted": network_contacted,
            "fetch_performed": fetch_performed,
            "cached_commit_available": False,
            "bytes_downloaded_if_available": None,
            "git_commands": acquisition_commands,
        }
        result["analysis_status"] = "failed"
        result["core_metric_status"] = "failed"
        acquisition_diagnostics = dict(exc.diagnostics)
        dirty_files = acquisition_diagnostics.get("dirty_files") or []
        if dirty_files:
            first_dirty = dirty_files[0]
            acquisition_diagnostics.update(
                {
                    "dirty_path": first_dirty.get("path"),
                    "git_status_code": first_dirty.get("git_status_code"),
                    "extension": first_dirty.get("extension"),
                    "supported_source_file": first_dirty.get("supported_source_file"),
                    "gitattributes_evidence": json.dumps(
                        first_dirty.get("gitattributes", {}), sort_keys=True
                    ),
                    "line_ending_normalization_suspected": first_dirty.get(
                        "line_ending_normalization_suspected"
                    ),
                }
            )
        _error(
            result,
            logger,
            "acquisition",
            exc.error_type,
            str(exc),
            exc.stderr or traceback.format_exc(),
            diagnostic_fields=acquisition_diagnostics,
        )
        _progress(
            config,
            logger,
            "acquisition",
            f"failed ({exc.error_type}): {str(exc).splitlines()[0]}",
            spec.url,
            spec.repository_name,
        )
        result["repository_end_timestamp"] = utc_now()
        result["repository_duration_seconds"] = round(
            time.perf_counter() - repository_started_clock, 6
        )
        result["timings"]["total_repository_seconds"] = result[
            "repository_duration_seconds"
        ]
        result["checkpoint_status"] = "incomplete"
        result.update(derive_repository_diagnostics(result))
        if checkpoint_writer:
            checkpoint_writer(result, "incomplete")
        return result, None
    except Exception as exc:
        core_was_completed = "metrics" in result
        if not core_was_completed:
            result["metrics"] = _failed_metrics()
            result["static"] = compatibility_static_result(result["metrics"])
            result["analysis_status"] = "failed"
            result["core_metric_status"] = "failed"
        else:
            result["analysis_status"] = "partial"
            result["core_metric_status"] = result["metrics"]["aggregate"][
                "metric_status"
            ]
        _error(
            result,
            logger,
            "analysis",
            "unexpected_repository_failure",
            f"{type(exc).__name__}: {exc}",
            traceback.format_exc(),
        )
        result["repository_end_timestamp"] = utc_now()
        result["repository_duration_seconds"] = round(
            time.perf_counter() - repository_started_clock, 6
        )
        result["timings"]["total_repository_seconds"] = result[
            "repository_duration_seconds"
        ]
        result["checkpoint_status"] = (
            "core_complete_with_later_failure" if core_was_completed else "incomplete"
        )
        result.update(derive_repository_diagnostics(result))
        if checkpoint_writer:
            checkpoint_writer(result, result["checkpoint_status"])
        if fail_fast:
            raise
        return result, inventory.to_dict() if inventory is not None else None
    except BaseException as exc:
        if "metrics" not in result:
            result["metrics"] = _failed_metrics()
            result["static"] = compatibility_static_result(result["metrics"])
            result["core_metric_status"] = "failed"
        result["analysis_status"] = "interrupted"
        result["repository_end_timestamp"] = utc_now()
        result["repository_duration_seconds"] = round(
            time.perf_counter() - repository_started_clock, 6
        )
        result["timings"]["total_repository_seconds"] = result[
            "repository_duration_seconds"
        ]
        result["errors"].append(
            {
                "repository_url": spec.url,
                "analyzed_commit_sha": result.get("acquisition", {}).get(
                    "analyzed_commit_sha"
                ),
                "module": "analysis",
                "severity": "ERROR",
                "error_type": "repository_interrupted",
                "message": f"{type(exc).__name__}: {exc}",
            }
        )
        result["checkpoint_status"] = "interrupted"
        result.update(derive_repository_diagnostics(result))
        if checkpoint_writer:
            checkpoint_writer(result, "interrupted")
        raise
    finally:
        if inventory is not None:
            inventory.clear_caches()


def _dependency_versions() -> dict[str, str | None]:
    names = (
        "tree-sitter", "tree-sitter-java", "tree-sitter-javascript",
        "tree-sitter-typescript", "tree-sitter-go",
    )
    values = {}
    for name in names:
        try:
            values[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            values[name] = None
    return values


def _git_version() -> str | None:
    try:
        return subprocess.run(
            ["git", "--version"], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _profiler_git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _profiler_git_provenance() -> dict[str, object]:
    sha = _profiler_git_sha()
    dirty: bool | None = None
    tag: str | None = None
    provenance_kind = "unknown"
    git_state = "unknown"
    source_sha256: str | None = None
    if sha:
        provenance_kind = "git_worktree"
        try:
            status = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain=v1"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            dirty = bool(status.stdout.strip())
            git_state = "dirty" if dirty else "clean"
        except (OSError, subprocess.SubprocessError):
            # The revision is known but the worktree state is not. It must not
            # be guessed clean merely to satisfy a schema.
            dirty = None
            git_state = "unknown"
    else:
        source_sha256 = _installed_source_sha256()
        if source_sha256 is not None:
            provenance_kind = "installed_distribution"
            git_state = "not_applicable"
        elif (PROJECT_ROOT / "pipeline.py").is_file():
            provenance_kind = "source_tree"
            git_state = "unknown"
    if sha:
        try:
            tag = subprocess.run(
                [
                    "git",
                    "-C",
                    str(PROJECT_ROOT),
                    "describe",
                    "--tags",
                    "--exact-match",
                    sha,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "profiler_git_commit_sha": sha,
        "profiler_git_dirty": dirty,
        "profiler_git_tag": tag,
        "profiler_provenance_kind": provenance_kind,
        "profiler_git_state": git_state,
        "profiler_source_sha256": source_sha256,
    }


def _installed_source_sha256() -> str | None:
    """Digest the installed evaluator payload, excluding mutable metadata.

    A wheel has no Git worktree. Its honest immutable identity is the bytes
    installed for Metrolith itself, not a fabricated Git cleanliness claim.
    Editable/source-tree installs normally take the Git branch above and do not
    use this path.
    """

    # More than one Metrolith distribution can be visible when a fresh venv is
    # deliberately given a read-only dependency directory on PYTHONPATH.  A
    # name-only metadata lookup may then select an unrelated editable install.
    # Bind the distribution to the evaluator bytes that supplied this module:
    # its recorded ``pipeline.py`` must resolve beside this ``PROJECT_ROOT``.
    distribution = None
    files: tuple[Any, ...] = ()
    try:
        candidates = importlib.metadata.distributions(name="metrolith")
        for candidate in candidates:
            candidate_files = tuple(candidate.files or ())
            if not any(
                ".dist-info/" in item.as_posix() for item in candidate_files
            ):
                continue
            pipeline_entry = next(
                (item for item in candidate_files if item.as_posix() == "pipeline.py"),
                None,
            )
            if pipeline_entry is None:
                continue
            located = Path(candidate.locate_file(pipeline_entry)).resolve(strict=False)
            if located == (PROJECT_ROOT / "pipeline.py").resolve(strict=False):
                distribution = candidate
                files = candidate_files
                break
    except (importlib.metadata.PackageNotFoundError, OSError):
        return None
    if distribution is None:
        return None
    allowed_roots = {
        "pipeline.py", "archlens_json.py", "modules", "config", "validation",
        "examples",
    }
    digest = hashlib.sha256()
    included = 0
    for relative in sorted(files, key=lambda item: item.as_posix()):
        portable = relative.as_posix()
        first = portable.split("/", 1)[0]
        if first not in allowed_roots:
            continue
        if portable.endswith((".pyc", ".pyo")) or "/__pycache__/" in portable:
            continue
        try:
            payload = Path(distribution.locate_file(relative)).read_bytes()
        except OSError:
            return None
        digest.update(portable.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
        included += 1
    return digest.hexdigest() if included else None


def _distribution_version() -> str | None:
    try:
        return importlib.metadata.version("metrolith")
    except importlib.metadata.PackageNotFoundError:
        return None


def load_run_input(
    input_paths: list[str | Path] | None,
) -> tuple[list[RepositorySpec], list[Path], bool, int]:
    paths = [Path(path) for path in input_paths] if input_paths else [PROJECT_ROOT / "input" / "repositories.csv"]
    diagnostics: dict[str, int] = {}
    if len(paths) == 1 and paths[0].suffix.lower() == ".csv":
        specs = load_repositories_csv(
            paths[0], include_disabled=True, diagnostics=diagnostics
        )
        return specs, paths, False, diagnostics.get("duplicate_rows_dropped", 0)
    if any(path.suffix.lower() != ".txt" for path in paths):
        raise ValueError("Use one canonical CSV or one or more deprecated TXT input files")
    specs = load_legacy_txt(paths, diagnostics=diagnostics)
    return specs, paths, True, diagnostics.get("duplicate_rows_dropped", 0)


def run_benchmark(
    input_paths: list[str | Path] | None = None,
    config: AnalysisConfig | None = None,
    acquisition_mode: str = "latest",
    fail_fast: bool = False,
    command_line_arguments: list[str] | None = None,
    commit_sha_override: str | None = None,
    execution_mode: str = "metrics",
    repository_specs: list[RepositorySpec] | None = None,
    single_repository: bool = False,
    qualification_registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate all input first, analyze through one common pipeline, and finalize one run.

    ``qualification_registry_path`` selects benchmark-qualified mode. It is
    loaded separately from ``RepositorySpec`` and ``normalized_input.csv``
    because benchmark adjudication is not ordinary repository request metadata:
    a subject's URL, expected language and architecture label describe what to
    measure, while a qualification decision describes how a *finished*
    measurement may be used.
    """
    config = config or AnalysisConfig.from_env()
    if not supported_python_version():
        expected = ".".join(str(value) for value in SUPPORTED_PYTHON_MINOR)
        raise RuntimeError(
            f"Metrolith benchmark runs require CPython {expected}.x; "
            f"found {platform.python_version()}"
        )
    if acquisition_mode not in {"latest", "frozen", "offline"}:
        raise ValueError(f"Unsupported acquisition mode: {acquisition_mode}")
    if execution_mode not in {"metrics", "all"}:
        raise ValueError(f"Unsupported execution mode: {execution_mode}")
    profiler = {
        "profiler_git_commit_sha": None,
        "profiler_git_dirty": None,
        "profiler_git_tag": None,
        "profiler_provenance_kind": "unknown",
        "profiler_git_state": "unknown",
        "profiler_source_sha256": None,
        **_profiler_git_provenance(),
    }
    # Test doubles and third-party callers written before Artifact 1.12 may
    # supply only the three historical Git fields. Preserve that seam while
    # deriving an honest conservative discriminator for the new schema.
    if profiler["profiler_provenance_kind"] == "unknown" and profiler[
        "profiler_git_commit_sha"
    ]:
        profiler["profiler_provenance_kind"] = "git_worktree"
    if profiler["profiler_git_state"] == "unknown" and profiler[
        "profiler_git_dirty"
    ] is not None:
        profiler["profiler_git_state"] = (
            "dirty" if profiler["profiler_git_dirty"] else "clean"
        )
    try:
        policy_sha256 = config.exclusion_policy_sha256
    except RuntimeError:
        policy_sha256 = None
    provenance_warnings: list[str] = []
    qualification_requested = qualification_registry_path is not None
    if not profiler["profiler_git_commit_sha"] and (
        qualification_requested or config.require_clean_profiler
    ):
        provenance_warnings.append(
            "The Metrolith evaluator has no Git commit identity; strict or "
            "benchmark-qualified use requires a reviewed Git worktree identity."
        )
    if profiler["profiler_git_state"] == "unknown":
        provenance_warnings.append(
            "The Metrolith evaluator Git state is unknown; local analysis remains "
            "available, but exact evaluator-worktree reproduction is not."
        )
    if profiler["profiler_git_dirty"] is True:
        provenance_warnings.append(
            "The Metrolith evaluator working tree is dirty; its source differs "
            "from the recorded Git revision."
        )
    if not policy_sha256:
        provenance_warnings.append(
            "Exclusion Policy content hash is unavailable; provenance is incomplete."
        )
    if config.require_clean_profiler:
        blockers = []
        if profiler["profiler_provenance_kind"] != "git_worktree":
            blockers.append("the Metrolith evaluator is not a Git worktree")
        if not profiler["profiler_git_commit_sha"]:
            blockers.append("profiler commit SHA is unavailable")
        if profiler["profiler_git_dirty"] is not False:
            blockers.append("profiler working tree is dirty or cannot be verified clean")
        if not policy_sha256:
            blockers.append("exclusion policy SHA-256 is unavailable")
        if blockers:
            raise RuntimeError(
                "--require-clean-profiler blocked the run before repository acquisition: "
                + "; ".join(blockers)
            )
    # Load and validate the registry BEFORE acquisition. A malformed registry,
    # a duplicate binding or a wrong profile is a fatal input error, and finding
    # it after a multi-hour cohort has been measured helps nobody. Loading here
    # does not join anything: matching needs the exact analyzed revision and
    # scope hash, which only exist once measurement has finished.
    qualification_registry = None
    if qualification_registry_path is not None:
        from modules.benchmark_qualification import load_registry

        qualification_registry = load_registry(qualification_registry_path)

    if repository_specs is not None and input_paths:
        raise ValueError("Use repository_specs or input_paths, not both")
    if repository_specs is not None:
        all_specs = list(repository_specs)
        paths: list[Path] = []
        legacy = False
        duplicate_rows_dropped = 0
    else:
        all_specs, paths, legacy, duplicate_rows_dropped = load_run_input(input_paths)

    # The complete accepted population (plan section 11). `all_specs` has
    # already had identical duplicates collapsed by the loader, so the ledger is
    # built by re-parsing the source rows instead. Conflicting duplicates raised
    # during load_run_input and never reach this point.
    from modules.normalized_input import _file_identity, normalize_specs
    from modules.repository_input import parse_repository_rows

    ledger_specs = list(all_specs)
    if paths and not legacy:
        try:
            reparsed: list[RepositorySpec] = []
            for path in paths:
                reparsed.extend(parse_repository_rows(path))
            ledger_specs = reparsed
        except (OSError, ValueError):
            # Re-parsing is an evidence enhancement, never a new failure mode:
            # these rows already validated once through load_run_input.
            ledger_specs = list(all_specs)

    normalization = normalize_specs(
        ledger_specs,
        source_files=dict(_file_identity(path) for path in paths if path.is_file()),
    )

    # Counts come from the ledger so that run_manifest.json and
    # normalized_input.csv necessarily agree (plan section 11.5). Before Artifact
    # 1.5, input_row_count was measured after duplicate collapsing and so
    # under-reported the accepted population by exactly duplicate_rows_dropped.
    input_row_count = normalization.accepted_row_count
    skipped_disabled_count = normalization.disabled_row_count
    duplicate_rows_dropped = normalization.duplicate_rows_dropped
    specs = [spec for spec in all_specs if spec.enabled]
    if not specs:
        raise ValueError("Repository input contains no enabled repositories")
    if commit_sha_override:
        normalized_override = commit_sha_override.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,40}", normalized_override):
            raise ValueError("--commit-sha must contain 7 to 40 hexadecimal characters")
        specs = [replace(spec, commit_sha=normalized_override) for spec in specs]
    if acquisition_mode in {"frozen", "offline"}:
        # Local subjects are exempt: Frozen/Offline pin a *remote* revision so a
        # cohort is reproducible without the network. A local snapshot has no
        # remote to pin, and its analyzed state is recorded by `source_mode`,
        # `analyzed_commit_sha` where one exists, and `analysis_scope_hash`.
        missing = [
            spec.url for spec in specs
            if not spec.commit_sha and not getattr(spec, "is_local", False)
        ]
        if missing:
            raise ValueError(
                f"{acquisition_mode.capitalize()} mode requires commit_sha for every enabled repository; missing for: "
                + ", ".join(missing)
            )
    # ---------------------------------------------------------- Stage A ---
    # Global preflight, before any acquisition. Grammars are probed here but
    # never block: `expected_language` is an expectation Metrolith exists partly
    # to test, so refusing on it could stop Metrolith discovering that a
    # repository is written in something else.
    stage_a_report = preflight.stage_a(
        config,
        acquisition_mode=acquisition_mode,
        expected_languages=[spec.expected_language for spec in specs],
    )
    if stage_a_report.refused:
        raise preflight.PreflightRefused(stage_a_report)

    config.prepare_paths()

    # ---------------------------------------------------------- Stage B ---
    # Cohort capability barrier. Every subject is acquired and discovered with
    # the canonical parser-free inventory pass, the required capabilities are
    # aggregated across the whole cohort, and measurement begins only if all of
    # them are available.
    #
    # This is deliberately not a per-repository check immediately before each
    # repository parses. That would allow repositories 1 and 2 to be measured
    # and repository 3 to discover a missing grammar, producing a mixed,
    # environment-dependent partial measurement.
    #
    # The discovery pass is skipped when every parser capability is already
    # available, because then no cohort content could make the barrier refuse.
    # The guarantee is unchanged — it is enforced exactly when it can matter —
    # and a complete environment pays nothing for it.
    parser_probes = preflight.probe_parser_capabilities()
    if preflight.barrier_can_refuse(parser_probes):
        # Shared with `diff`'s two-side barrier. Uses the same local/remote
        # dispatch the measurement path uses: calling remote acquisition
        # directly here silently skipped every local subject, so the barrier
        # aggregated no requirements for them and could never refuse.
        required = discover_required_capabilities(
            [(spec.url, spec) for spec in specs], config, acquisition_mode
        )
        stage_b_report = preflight.stage_b(required, probes=parser_probes)
        if stage_b_report.refused:
            raise preflight.PreflightRefused(stage_b_report)

    start = utc_now()
    repository_slug = (
        f"{specs[0].owner}-{specs[0].repository_name}"
        if single_repository and len(specs) == 1
        else None
    )
    artifacts = RunArtifacts(
        config.output_root,
        start,
        planned_repository_count=len(specs),
        repository_slug=repository_slug,
    )
    _progress(
        config,
        artifacts.logger,
        "workspace",
        f"resolved workspace {config.workspace_root}; output {config.output_root}",
    )
    for warning in provenance_warnings:
        _terminal(config, f"[PROVENANCE WARNING] {warning}")
        artifacts.logger.log(
            "WARNING", "provenance", warning, error_type="provenance_warning"
        )
    quarantined_staging, staging_warnings = cleanup_stale_cache_staging(config.cache_root)
    _progress(
        config,
        artifacts.logger,
        "acquisition",
        f"quarantined {quarantined_staging} stale cache staging director"
        f"{'y' if quarantined_staging == 1 else 'ies'}",
    )
    for warning in staging_warnings:
        artifacts.logger.log(
            "WARNING", "acquisition", warning, error_type="stale_cache_cleanup_failure"
        )
    if legacy:
        artifacts.logger.log(
            "WARNING", "input", "Legacy TXT input is deprecated; use input/repositories.csv",
            error_type="deprecated_input",
        )
    parser_startup = validate_parser_initialization()
    artifacts.logger.log("INFO", "startup", f"Parser initialization: {json.dumps(parser_startup, sort_keys=True)}")

    def analyze(indexed_spec: tuple[int, RepositorySpec]):
        index, spec = indexed_spec
        return _analyze_spec(
            spec,
            config,
            acquisition_mode,
            artifacts.logger,
            index,
            len(specs),
            fail_fast,
            execution_mode,
            artifacts.checkpoint_repository,
        )

    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    indexed_specs = list(enumerate(specs, start=1))
    if fail_fast:
        for indexed_spec in indexed_specs:
            pair = analyze(indexed_spec)
            pairs.append(pair)
            if pair[0]["analysis_status"] == "failed":
                break
    elif config.workers == 1:
        pairs = [analyze(indexed_spec) for indexed_spec in indexed_specs]
    else:
        with ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="metrolith") as executor:
            pairs = list(executor.map(analyze, indexed_specs))

    results = [pair[0] for pair in pairs]
    from modules.run_artifacts import artifact_slug

    inventories = {
        artifact_slug(result): inventory
        for result, inventory in pairs
        if inventory is not None
    }
    errors = [error for result in results for error in result.get("errors", [])]
    end = utc_now()
    success_count = sum(result["analysis_status"] == "complete" for result in results)
    partial_count = sum(result["analysis_status"] == "partial" for result in results)
    failure_count = sum(result["analysis_status"] == "failed" for result in results)
    input_hashes = {str(path): input_sha256(path) for path in paths}
    manifest = {
        "product_name": "Metrolith",
        "distribution_name": "metrolith",
        "artifact_schema_version": config.artifact_schema_version,
        "run_id": artifacts.run_id,
        "metric_contract_version": config.metric_contract_version,
        # Versioned separately from Metric Contract: a complexity-contract
        # difference must not make the four benchmark metrics incomparable.
        "complexity_contract_version": COMPLEXITY_CONTRACT_VERSION,
        "inventory_schema_version": config.inventory_schema_version,
        "input_file_path": [str(path.resolve()) for path in paths],
        "input_file_hash": (
            input_hashes[str(paths[0])]
            if len(paths) == 1
            else input_hashes if paths else None
        ),
        "input_source": "single_repository" if single_repository else "csv_or_legacy",
        "legacy_input_deprecated": legacy,
        "command_line_arguments": command_line_arguments if command_line_arguments is not None else sys.argv[1:],
        "acquisition_mode": acquisition_mode,
        "execution_mode": execution_mode,
        "exclusion_policy_version": config.exclusion_policy_version,
        "program_version": config.program_version,
        **profiler,
        "provenance_warnings": provenance_warnings,
        "exclusion_policy_sha256": policy_sha256,
        "package_distribution_version": _distribution_version(),
        "python_version": platform.python_version(),
        "python_version_exact": sys.version,
        "git_version": _git_version(),
        "tree_sitter_version": _dependency_versions()["tree-sitter"],
        "grammar_versions": {
            key: value for key, value in _dependency_versions().items() if key != "tree-sitter"
        },
        "parser_initialization": parser_startup,
        "operating_system": os.name,
        "platform": platform.platform(),
        "effective_git_checkout_configuration": dict(
            GIT_CHECKOUT_CONFIGURATION
        ),
        "start_timestamp": start,
        "end_timestamp": end,
        "worker_count": config.workers,
        "repository_count": len(specs),
        "input_row_count": input_row_count,
        "planned_repository_count": len(specs),
        "processed_repository_count": len(results),
        "success_count": success_count,
        "partial_count": partial_count,
        "failure_count": failure_count,
        "skipped_disabled_count": skipped_disabled_count,
        "duplicate_rows_dropped": duplicate_rows_dropped,
        "resolved_paths": {
            "workspace_root": str(config.workspace_root),
            "cache_root": str(config.cache_root),
            "temporary_directory": str(config.temporary_directory),
            "output_root": str(config.output_root),
        },
        "effective_configuration": config.snapshot(),
        "architecture_label_semantics": "architecture_type is supplied by benchmark input; it is not detected",
        "git_mode_maps": [
            {
                "repository_url": result.get("repository_url"),
                "status": result.get("git_mode_map_status"),
                "available": result.get("git_mode_map_available"),
                "error": result.get("git_mode_map_error"),
                "entry_count": result.get("git_mode_entry_count"),
                "command_duration_seconds": result.get(
                    "git_mode_command_duration_seconds"
                ),
                "effective_timeout_seconds": result.get(
                    "git_mode_effective_timeout_seconds"
                ),
            }
            for result in sorted(
                results, key=lambda item: subject_key_of(item).casefold()
            )
        ],
    }
    manifest["benchmark_environment"] = {
        "archlens_version": config.program_version,
        "package_distribution_version": manifest["package_distribution_version"],
        "python_version": platform.python_version(),
        "python_version_exact": sys.version,
        "git_version": manifest["git_version"],
        "tree_sitter_version": manifest["tree_sitter_version"],
        "grammar_versions": dict(manifest["grammar_versions"]),
        "parser_initialization": dict(parser_startup),
        "operating_system": os.name,
        "platform": platform.platform(),
        "profiler_git_commit_sha": profiler["profiler_git_commit_sha"],
        "profiler_git_dirty": profiler["profiler_git_dirty"],
        "profiler_git_tag": profiler["profiler_git_tag"],
        "profiler_provenance_kind": profiler["profiler_provenance_kind"],
        "profiler_git_state": profiler["profiler_git_state"],
        "profiler_source_sha256": profiler["profiler_source_sha256"],
        "exclusion_policy_version": config.exclusion_policy_version,
        "exclusion_policy_sha256": policy_sha256,
        "metric_contract_version": config.metric_contract_version,
        # Versioned separately from Metric Contract: a complexity-contract
        # difference must not make the four benchmark metrics incomparable.
        "complexity_contract_version": COMPLEXITY_CONTRACT_VERSION,
        "inventory_schema_version": config.inventory_schema_version,
        "artifact_schema_version": config.artifact_schema_version,
        "effective_git_checkout_configuration": dict(
            GIT_CHECKOUT_CONFIGURATION
        ),
    }
    _terminal(config, "Finalizing recorded measurements and diagnostics...")
    _progress(config, artifacts.logger, "output", "finalizing run artifacts")
    status = artifacts.finalize(
        manifest,
        results,
        errors,
        inventories,
        normalization,
        qualification_registry=qualification_registry,
    )
    _progress(
        config,
        artifacts.logger,
        "output",
        f"finalization {status}: {artifacts.run_dir}",
    )
    return {
        "run_id": artifacts.run_id,
        "run_directory": str(artifacts.run_dir),
        "status": status,
        "repository_count": len(specs),
        "success_count": success_count,
        "partial_count": partial_count,
        "failure_count": failure_count,
        "input_row_count": input_row_count,
        "planned_repository_count": len(specs),
        "skipped_disabled_count": skipped_disabled_count,
        "metrics_csv": str(artifacts.run_dir / "sheet_metrics.csv"),
        "errors_csv": str(artifacts.run_dir / "errors.csv"),
        "frozen_input": str(artifacts.run_dir / "repositories_frozen.csv"),
        "summary_markdown": str(artifacts.run_dir / "summary.md"),
        "results": results,
        "local_source_options": {
            _identity(spec).subject_key: {"tracked_only": spec.tracked_only}
            for spec in specs if getattr(spec, "is_local", False)
        },
    }
