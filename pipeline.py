#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pipeline Orchestrator for the Monolithic & Microservices Benchmark Dataset.

This script coordinates all processing stages:
- Metadata collection
- Repository cloning
- Static analysis (LOC, classes, methods)
- API endpoint extraction
- Deployability inspection
- Database schema detection
- Test coverage estimation
- Category classification
- CSV Export
- Fact sheet generation

All individual functionality is delegated to modules in /modules.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path

# Import internal modules
from modules.metadata import fetch_metadata
from modules.clone_repo import clone_repository, cleanup_stale_staging_directories
from modules.static_analysis import perform_static_analysis
from modules.endpoints import extract_endpoints
from modules.deployability import assess_deployability
from modules.db_analysis import detect_db_schema
from modules.coverage import compute_coverage
from modules.classify import classify_application
from modules.export_csv import export_catalog
from modules.fact_sheet import generate_fact_sheets
from modules.metadata import parse_github_repo
from modules.metric_warnings import assess_metric_warnings, detect_framework_markers
from modules.benchmark_runner import run_benchmark
from modules.preflight import EXIT_PREFLIGHT_REFUSED, PreflightRefused
from modules.config import (
    POLICY_PATH,
    PROGRAM_VERSION,
    SUPPORTED_LANGUAGES,
    SUPPORTED_PYTHON_MINOR,
    AnalysisConfig,
    resolve_environment_value,
    supported_python_version,
)
from modules.core_metrics import validate_parser_initialization
from modules.repository_input import (
    RepositorySpec,
    canonicalize_github_url,
    load_repositories_csv,
    migrate_legacy_inputs,
)
from modules.acquisition import acquire_repository
from modules.inventory import RepositoryInventory


# =====================================================================
# DIRECTORY SETUP
# =====================================================================
PACKAGE_ROOT = Path(__file__).resolve().parent
# Legacy commands retain their historical tree shape, but the tree is rooted in
# the invocation workspace rather than the installed package directory.
BASE_DIR = Path(
    resolve_environment_value(
        "METROLITH_HOME", "ARCHLENS_HOME", default=Path.cwd()
    )
).expanduser().resolve()
INPUT_DIR = BASE_DIR / "input"
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = BASE_DIR / "output"

REQUIRED_DIRS = [
    INPUT_DIR,
    RAW_DIR,
    PROCESSED_DIR,
    OUTPUT_DIR / "csv",
    OUTPUT_DIR / "fact_sheets",
]

# =====================================================================
# LOAD REPOSITORY LISTS
# =====================================================================
def load_repositories(input_files=None):
    """
    Read repository URLs from explicitly selected files or every input/*.txt.

    Filenames containing "monolith" or "microservice" determine the type.
    Blank lines, comments, and duplicate canonical URLs are ignored.
    """
    if input_files:
        paths = [Path(path) for path in input_files]
    else:
        paths = sorted(INPUT_DIR.glob("*.txt"), key=lambda path: path.name.lower())

    if not paths:
        raise FileNotFoundError("No repository list files were found under input/")

    repos = []
    seen = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Missing repository list: {path}")
        lower_name = path.name.lower()
        if "monolith" in lower_name:
            repo_type = "monolith"
        elif "microservice" in lower_name:
            repo_type = "microservices"
        else:
            print(f"[WARN] Skipping input file with unknown repository type: {path}")
            continue

        for line_number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            try:
                owner, name = parse_github_repo(url)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}: {url}") from exc
            canonical = f"https://github.com/{owner}/{name}"
            key = canonical.lower()
            if key in seen:
                print(f"[WARN] Ignoring duplicate repository URL: {url}")
                continue
            seen.add(key)
            repos.append(
                {
                    "type": repo_type,
                    "url": canonical,
                    "owner": owner,
                    "repo_name": name,
                    "input_file": path.name,
                }
            )
    if not repos:
        return []
    name_counts = Counter(repo["repo_name"].lower() for repo in repos)
    for repo in repos:
        repo["storage_name"] = (
            repo["repo_name"]
            if name_counts[repo["repo_name"].lower()] == 1
            else f"{repo['owner']}__{repo['repo_name']}"
        )
    return repos


def _local_repository_path(repo, raw_data_dir):
    """Return a stable, collision-safe, short cache path."""
    digest = hashlib.sha256(repo["url"].lower().encode("utf-8")).hexdigest()[:12]
    return Path(raw_data_dir) / f"r_{digest}"


def _legacy_repository_paths(repo, raw_data_dir):
    raw_data_dir = Path(raw_data_dir)
    candidates = [raw_data_dir / f"{repo['owner']}__{repo['repo_name']}"]
    if "__" not in repo["storage_name"]:
        candidates.append(raw_data_dir / repo["repo_name"])
    return candidates


def _write_processed_snapshot(repos):
    path = PROCESSED_DIR / "analysis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(repos, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _clear_analysis_results(repo):
    for key in (
        "static",
        "endpoints",
        "deployability",
        "db_schema",
        "coverage",
        "category",
        "framework_markers",
        "metric_warning",
        "metric_warning_reason",
    ):
        repo.pop(key, None)


def inspect_input_files(input_dir=None):
    """Return menu metadata for deterministic input-list selection."""
    input_dir = Path(input_dir or INPUT_DIR)
    language_tokens = (
        (r"typescript", "TypeScript"),
        (r"javascript", "JavaScript"),
        (r"python", "Python"),
        (r"(?<![a-z])java(?![a-z])", "Java"),
        (r"golang|(?<![a-z])go(?![a-z])", "Go"),
    )
    entries = []
    for path in sorted(input_dir.glob("*.txt"), key=lambda item: item.name.lower()):
        lowered = path.name.lower()
        if "monolith" in lowered:
            system_type = "monolith"
        elif "microservice" in lowered:
            system_type = "microservices"
        else:
            system_type = "unknown"
        language = next(
            (
                label
                for pattern, label in language_tokens
                if re.search(pattern, lowered)
            ),
            "unknown",
        )
        url_count = 0
        invalid_count = 0
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            try:
                parse_github_repo(value)
                url_count += 1
            except ValueError:
                invalid_count += 1
        entries.append(
            {
                "path": path,
                "name": path.name,
                "language": language,
                "system_type": system_type,
                "repository_count": url_count,
                "invalid_count": invalid_count,
            }
        )
    return entries


def clean_temporary_folders(raw_data_dir=None):
    """Clean pipeline staging and Python caches while preserving final repositories."""
    raw_dir = Path(
        raw_data_dir
        or resolve_environment_value(
            "METROLITH_RAW_DIR", "ARCHLENS_RAW_DIR", "ARCH_BENCH_RAW_DIR"
        )
        or RAW_DIR
    ).expanduser().resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    warnings = cleanup_stale_staging_directories(raw_dir)
    removed = 0
    candidates = [BASE_DIR / ".pytest_cache", BASE_DIR / "__pycache__"]
    for source_root in (BASE_DIR / "modules", BASE_DIR / "tests"):
        if source_root.is_dir():
            candidates.extend(source_root.rglob("__pycache__"))
    temp_root = Path(
        resolve_environment_value(
            "METROLITH_TEMP_DIR", "ARCHLENS_TEMP_DIR", "ARCH_BENCH_TEMP_DIR",
            tempfile.gettempdir(),
        )
    )
    stale_before = time.time() - (24 * 60 * 60)
    for pattern in ("metrolith_*", "archlens_*", "archbench_*"):
        for path in temp_root.glob(pattern):
            try:
                if path.stat().st_mtime < stale_before:
                    candidates.append(path)
            except OSError as exc:
                warnings.append(f"Could not inspect {path}: {exc}")
    for path in candidates:
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError as exc:
            warnings.append(f"Could not remove {path}: {exc}")
    return {"removed": removed, "warnings": warnings, "raw_dir": raw_dir}


def _elapsed(seconds):
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:04.1f}s"
    if minutes:
        return f"{minutes:d}m {seconds:04.1f}s"
    return f"{seconds:.1f}s"


def _terminal_error(value, limit=240):
    message = " ".join(str(value or "").split())
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _progress_counts(repos):
    return {
        "analyzed": sum(repo.get("analysis_status") == "analyzed" for repo in repos),
        "failed_fetch": sum(repo.get("fetch_status") == "failed" for repo in repos),
        "skipped": sum(repo.get("analysis_status") == "skipped" for repo in repos),
        "warnings": sum(bool(repo.get("metric_warning")) for repo in repos),
        "errors": sum(repo.get("status") == "error" for repo in repos),
    }


def _print_repository_progress(repos, processed, repo_elapsed, total_elapsed):
    counts = _progress_counts(repos[:processed])
    print(
        f"Completed {processed}/{len(repos)} in {_elapsed(repo_elapsed)} | "
        f"analyzed {counts['analyzed']}, fetch failures "
        f"{counts['failed_fetch']}, errors {counts['errors']}, "
        f"skipped {counts['skipped']}, "
        f"warnings {counts['warnings']} | total {_elapsed(total_elapsed)}"
    )


def _print_final_summary(repos, runtime, step):
    counts = _progress_counts(repos)
    print("\nRun Summary")
    print("=" * 52)
    rows = (
        ("Total repositories", len(repos)),
        ("Analyzed", counts["analyzed"]),
        ("Failed fetch", counts["failed_fetch"]),
        ("Processing errors", counts["errors"]),
        ("Skipped analysis", counts["skipped"]),
        ("Metric warnings", counts["warnings"]),
        ("Total runtime", _elapsed(runtime)),
    )
    for label, value in rows:
        print(f"{label:<22} {value}")
    print(f"{'JSON output':<22} {PROCESSED_DIR / 'analysis.json'}")
    if step in ("all", "export"):
        print(f"{'CSV output':<22} {OUTPUT_DIR / 'csv' / 'apps_catalog.csv'}")
    if step in ("all", "facts"):
        print(f"{'Markdown output':<22} {OUTPUT_DIR / 'fact_sheets'}")


# =====================================================================
# PIPELINE EXECUTION LOGIC
# =====================================================================
def execute_pipeline(
    step,
    input_files=None,
    fail_fast=False,
    raw_data_dir=None,
    verbose=False,
):
    """
    Runs the requested pipeline step on ALL repositories.
    """
    run_started = time.perf_counter()
    repos = load_repositories(input_files)
    raw_data_dir = Path(
        raw_data_dir
        or resolve_environment_value(
            "METROLITH_RAW_DIR", "ARCHLENS_RAW_DIR", "ARCH_BENCH_RAW_DIR"
        )
        or RAW_DIR
    ).expanduser().resolve()
    raw_data_dir.mkdir(parents=True, exist_ok=True)
    for warning in cleanup_stale_staging_directories(raw_data_dir):
        print(f"[WARN] Stale repository staging cleanup failed: {warning}")

    needs_local_repo = {
        "all",
        "static",
        "endpoints",
        "deployability",
        "db",
        "coverage",
        "classify",
        "export",
        "facts",
    }

    for repo_index, repo in enumerate(repos, start=1):
        repo_started = time.perf_counter()
        url = repo["url"]
        rtype = repo["type"]
        repo_name = repo["repo_name"]
        local_path = _local_repository_path(repo, raw_data_dir)
        inventory = None
        repo.update(
            {
                "fetch_status": "skipped",
                "fetch_method": "none",
                "fetch_error_type": "none",
                "fetch_error_message": "",
                "analysis_status": "skipped",
                "analysis_skip_reason": "analysis not requested",
                "metric_warning": False,
                "metric_warning_reason": "",
            }
        )

        print(f"\n[{repo_index}/{len(repos)}] {repo_name} [{rtype}]")
        print("-" * 52)

        try:
            if step in ("all", "metadata", "classify", "export", "facts"):
                print("  Stage 1: GitHub metadata")
                repo["metadata"] = fetch_metadata(url)
            else:
                repo["metadata"] = {"name": repo_name}

            if step in needs_local_repo or step == "clone":
                print("  Stage 2: Fetch/validate repository")
                fetch = clone_repository(
                    url,
                    local_path,
                    default_branch=repo["metadata"].get("default_branch"),
                    legacy_paths=_legacy_repository_paths(repo, raw_data_dir),
                )
                local_path = fetch.pop("path", None)
                repo.update(fetch)
                if repo["fetch_status"] != "success" or local_path is None:
                    _clear_analysis_results(repo)
                    reason = (
                        f"{repo['fetch_error_type']}: "
                        f"{repo['fetch_error_message']}"
                    ).strip(": ")
                    repo["analysis_status"] = "skipped"
                    repo["analysis_skip_reason"] = reason
                    repo["status"] = "error"
                    repo["error"] = reason
                    print(f"  ERROR: {_terminal_error(reason)}")
                    if fail_fast:
                        raise RuntimeError(reason)
                    _print_repository_progress(
                        repos,
                        repo_index,
                        time.perf_counter() - repo_started,
                        time.perf_counter() - run_started,
                    )
                    continue
                try:
                    repo["local_path"] = local_path.relative_to(BASE_DIR).as_posix()
                except ValueError:
                    repo["local_path"] = str(local_path)

            if step in needs_local_repo:
                inventory = RepositoryInventory(local_path)

            if step in ("all", "static", "classify", "export", "facts"):
                print("  Stage 3: Static analysis")
                repo["static"] = perform_static_analysis(local_path, inventory=inventory)
                if not repo["metadata"].get("language"):
                    repo["metadata"]["language"] = repo["static"].get("primary_language")

            if step in ("all", "endpoints", "classify", "export", "facts"):
                print("  Stage 4: API endpoints")
                repo["endpoints"] = extract_endpoints(local_path, inventory=inventory)
                repo["framework_markers"] = detect_framework_markers(local_path, inventory=inventory)

            if step in ("all", "deployability", "classify", "export", "facts"):
                print("  Stage 5: Deployability")
                repo["deployability"] = assess_deployability(local_path, inventory=inventory)

            if step in ("all", "db", "classify", "export", "facts"):
                print("  Stage 6: Database schema")
                repo["db_schema"] = detect_db_schema(local_path, inventory=inventory)

            if step in ("all", "coverage", "classify", "export", "facts"):
                print("  Stage 7: Test coverage")
                repo["coverage"] = compute_coverage(local_path, inventory=inventory)

            if step in ("all", "classify", "export", "facts"):
                print("  Stage 8: Classification")
                repo["category"] = classify_application(repo)
            if "static" in repo:
                repo.update(
                    assess_metric_warnings(
                        repo["static"],
                        repo.get("endpoints", []),
                        repo.get("framework_markers", []),
                    )
                )
            if step in needs_local_repo:
                repo["analysis_status"] = "analyzed"
                repo["analysis_skip_reason"] = ""
            repo["status"] = "ok"
        except Exception as exc:
            _clear_analysis_results(repo)
            repo["status"] = "error"
            repo["error"] = f"{type(exc).__name__}: {exc}"
            repo["analysis_status"] = "skipped"
            repo["analysis_skip_reason"] = repo["error"]
            print(f"  ERROR: {_terminal_error(repo['error'])}")
            if verbose:
                traceback.print_exc()
            if fail_fast:
                raise
        _print_repository_progress(
            repos,
            repo_index,
            time.perf_counter() - repo_started,
            time.perf_counter() - run_started,
        )

    # 9) CSV EXPORT (AFTER ALL REPOS ARE PROCESSED)
    if step in ("all", "export"):
        print("[9] Exporting apps_catalog.csv...")
        export_catalog(repos, OUTPUT_DIR / "csv" / "apps_catalog.csv")

    # 10) FACT SHEETS
    if step in ("all", "facts"):
        print("[10] Generating fact sheets...")
        generate_fact_sheets(repos, OUTPUT_DIR / "fact_sheets")

    _write_processed_snapshot(repos)
    _print_final_summary(repos, time.perf_counter() - run_started, step)
    return repos


def audit_repository(repo_url, raw_data_dir=None):
    owner, repo_name = parse_github_repo(repo_url)
    canonical_url = f"https://github.com/{owner}/{repo_name}"
    repo = {
        "url": canonical_url,
        "owner": owner,
        "repo_name": repo_name,
        "storage_name": repo_name,
    }
    raw_data_dir = Path(
        raw_data_dir
        or resolve_environment_value(
            "METROLITH_RAW_DIR", "ARCHLENS_RAW_DIR", "ARCH_BENCH_RAW_DIR"
        )
        or RAW_DIR
    ).expanduser().resolve()
    raw_data_dir.mkdir(parents=True, exist_ok=True)
    for warning in cleanup_stale_staging_directories(raw_data_dir):
        print(f"[WARN] Stale repository staging cleanup failed: {warning}")
    metadata = fetch_metadata(canonical_url)
    destination = _local_repository_path(repo, raw_data_dir)
    fetch = clone_repository(
        canonical_url,
        destination,
        default_branch=metadata.get("default_branch"),
        legacy_paths=_legacy_repository_paths(repo, raw_data_dir),
    )
    local_path = fetch.pop("path", None)
    result = {"url": canonical_url, "metadata": metadata, **fetch}
    if fetch["fetch_status"] != "success" or local_path is None:
        result["analysis_status"] = "skipped"
        result["analysis_skip_reason"] = (
            f"{fetch['fetch_error_type']}: {fetch['fetch_error_message']}"
        )
        return result

    static = perform_static_analysis(local_path)
    endpoints = extract_endpoints(local_path)
    markers = detect_framework_markers(local_path)
    result.update(
        {
            "analysis_status": "analyzed",
            "analysis_skip_reason": "",
            "local_path": str(local_path),
            "static": static,
            "endpoint_count": len(endpoints),
            "framework_markers": markers,
            **assess_metric_warnings(static, endpoints, markers),
        }
    )
    return result


def audit_repository_safe(repo_url, cache_root=None):
    """Run the console audit from an isolated, commit-verified checkout."""
    owner, repo_name = parse_github_repo(repo_url)
    canonical_url = f"https://github.com/{owner}/{repo_name}"
    spec = RepositorySpec(canonical_url, "unknown")
    config = AnalysisConfig.from_env(
        cache_root=Path(cache_root) if cache_root else None,
    )
    with acquire_repository(spec, config, mode="latest") as acquired:
        inventory = RepositoryInventory(acquired.path, config)
        static = perform_static_analysis(acquired.path, inventory=inventory)
        endpoints = extract_endpoints(acquired.path, inventory=inventory)
        markers = detect_framework_markers(acquired.path, inventory=inventory)
        return {
            "url": canonical_url,
            "fetch_status": "success",
            "fetch_method": acquired.record.fetch_method,
            "analysis_status": "analyzed",
            "analyzed_commit_sha": acquired.record.analyzed_commit_sha,
            "acquisition": acquired.record.to_dict(),
            "endpoint_count": len(endpoints),
            "framework_markers": markers,
            **assess_metric_warnings(static, endpoints, markers),
            "static": static,
        }


def print_audit_report(result):
    print("\nJavaScript / TypeScript Metric Audit")
    print("=" * 38)
    print(f"Repository: {result['url']}")
    print(
        f"Fetch: {result.get('fetch_status')} "
        f"({result.get('fetch_method', 'none')})"
    )
    if result.get("analysis_status") != "analyzed":
        print(f"Analysis: skipped - {result.get('analysis_skip_reason', '')}")
        return

    static = result["static"]
    print(
        "Local path: "
        + (result.get("local_path") or "isolated temporary checkout (not retained)")
    )
    print(f"Scanned JS/TS files: {static.get('js_ts_scanned_files')}")
    print(f"  .js:  {static.get('js_ts_extension_counts', {}).get('.js', 0)}")
    print(f"  .jsx: {static.get('js_ts_extension_counts', {}).get('.jsx', 0)}")
    print(f"  .ts:  {static.get('js_ts_extension_counts', {}).get('.ts', 0)}")
    print(f"  .tsx: {static.get('js_ts_extension_counts', {}).get('.tsx', 0)}")
    print(f"Parse-failed files: {static.get('js_ts_parse_failed_files')}")
    print(
        "Skipped files: "
        f"{static.get('js_ts_skipped_files')} "
        f"{static.get('js_ts_skipped_by_category')}"
    )
    print(
        "Class declarations searched: "
        f"{static.get('js_ts_class_declaration_search')}"
    )
    print(
        "TypeScript interfaces counted: "
        f"{static.get('js_ts_typescript_interfaces_counted')}"
    )
    print(
        "TypeScript type aliases counted: "
        f"{static.get('js_ts_typescript_type_aliases_counted')}"
    )
    print(
        "Detected classes/interfaces/enums: "
        f"{static.get('js_ts_detected_classes_structs')}"
    )
    print(
        "Detected methods/functions: "
        f"{static.get('js_ts_detected_methods_functions')}"
    )
    print(f"Class status: {static.get('class_detection_status')}")
    print(f"Class reason: {static.get('class_detection_reason')}")
    print(f"Function status: {static.get('function_detection_status')}")
    print(f"Metric confidence: {static.get('metric_confidence')}")
    print(f"Endpoint count: {result.get('endpoint_count')}")
    print(f"Framework markers: {', '.join(result.get('framework_markers', [])) or 'none'}")
    print(f"Metric warning: {result.get('metric_warning')}")
    if result.get("metric_warning_reason"):
        print(f"Warning reason: {result['metric_warning_reason']}")

    for label, key in (
        ("Scanned samples", "class_detection_sample_files"),
        ("Skipped samples", "class_detection_skipped_sample_files"),
        ("Class-keyword samples", "class_detection_keyword_sample_files"),
        ("Metadata evidence", "class_detection_metadata_evidence"),
        ("Parse-failure samples", "js_ts_parse_failed_sample_files"),
    ):
        values = static.get(key, [])
        if values:
            print(f"{label}:")
            for value in values[:5]:
                if isinstance(value, dict):
                    print(f"  - {value['file']} ({value['reason']})")
                else:
                    print(f"  - {value}")
    print("\nGo Metric Audit")
    print("-" * 38)
    print(f"Scanned Go files: {static.get('go_scanned_files')}")
    print(f"Parse-failed files: {static.get('go_parse_failed_files')}")
    print(
        "Skipped files: "
        f"{static.get('go_skipped_files')} "
        f"{static.get('go_skipped_by_category')}"
    )
    print(
        "Detected named module-scope structs (interfaces excluded): "
        f"{static.get('go_detected_classes_structs')}"
    )
    print(
        "Detected functions/methods: "
        f"{static.get('go_detected_methods_functions')}"
    )
    print(f"Detection status: {static.get('go_metric_detection_status')}")
    print(f"Detection reason: {static.get('go_metric_detection_reason')}")
    print(f"Struct status: {static.get('go_struct_detection_status')}")
    print(f"Function status: {static.get('go_function_detection_status')}")
    print(f"Metric confidence: {static.get('go_metric_confidence')}")


def run_interactive_menu(input_fn=input, output_fn=print):
    """Run a dependency-free terminal menu without affecting scripted commands."""
    while True:
        entries = inspect_input_files()
        output_fn("\nMetrolith Interactive Menu")
        output_fn("=" * 32)
        output_fn("Recommended default: core metrics (fast four-metric profile)")
        if entries:
            for index, entry in enumerate(entries, start=1):
                output_fn(
                    f"{index}. {entry['name']} | {entry['language']} | "
                    f"{entry['system_type']} | "
                    f"{entry['repository_count']} repositories"
                    + (
                        f" | {entry['invalid_count']} invalid"
                        if entry["invalid_count"]
                        else ""
                    )
                )
        else:
            output_fn("No input list files found.")

        all_option = len(entries) + 1
        full_option = len(entries) + 2
        audit_option = len(entries) + 3
        clean_option = len(entries) + 4
        exit_option = len(entries) + 5
        output_fn(f"{all_option}. Run core metrics for all input files (recommended)")
        output_fn(f"{full_option}. Run full optional analysis for all input files")
        output_fn(f"{audit_option}. Audit one repository")
        output_fn(f"{clean_option}. Clean temporary/cache folders")
        output_fn(f"{exit_option}. Exit")

        try:
            choice = input_fn("Select an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\nGoodbye.")
            return
        if not choice.isdigit():
            output_fn("Please enter one of the displayed numbers.")
            continue
        selected = int(choice)
        try:
            if 1 <= selected <= len(entries):
                run_benchmark(
                    input_paths=[entries[selected - 1]["path"]],
                    config=AnalysisConfig.from_env(),
                    execution_mode="metrics",
                )
            elif selected == all_option:
                run_benchmark(
                    input_paths=[entry["path"] for entry in entries],
                    config=AnalysisConfig.from_env(),
                    execution_mode="metrics",
                )
            elif selected == full_option:
                run_benchmark(
                    input_paths=[entry["path"] for entry in entries],
                    config=AnalysisConfig.from_env(),
                    execution_mode="all",
                )
            elif selected == audit_option:
                try:
                    repo_url = input_fn("GitHub repository URL: ").strip()
                except (EOFError, KeyboardInterrupt):
                    output_fn("\nAudit cancelled.")
                    continue
                if not repo_url:
                    output_fn("Repository URL cannot be empty.")
                    continue
                print_audit_report(audit_repository(repo_url))
            elif selected == clean_option:
                result = clean_temporary_folders()
                output_fn(
                    f"Removed {result['removed']} temporary/cache items. "
                    "Validated repository caches were preserved."
                )
                for warning in result["warnings"]:
                    output_fn(f"Warning: {_terminal_error(warning)}")
            elif selected == exit_option:
                output_fn("Goodbye.")
                return
            else:
                output_fn("Please enter one of the displayed numbers.")
        except Exception as exc:
            output_fn(f"Operation failed: {_terminal_error(exc)}")


# =====================================================================
# COMMAND-LINE INTERFACE
# =====================================================================
def _add_path_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        help="Invocation workspace root (defaults to METROLITH_HOME or current directory)",
    )
    parser.add_argument("--output-root", help="Override the workspace output root")
    parser.add_argument("--cache-root", help="Override the workspace bare Git cache root")
    parser.add_argument("--temp-dir", help="Override the workspace temporary worktree root")


def _record_analyze_source(argv, parser, source):
    """Keep replay options while recording the resolved local source locator.

    Later exporters must be able to protect source even after the caller changes
    directory. This is execution provenance only, never Ratchet source mapping.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            parser = action.choices["analyze"]
            break
    values = list(argv)
    start = values.index("analyze") + 1
    i = start
    while i < len(values):
        token = values[i]
        if token == "--":
            i += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        option = token.split("=", 1)[0]
        matches = [action for name, action in parser._option_string_actions.items() if name == option or name.startswith(option)]
        action = matches[0]
        i += 1 if "=" in token or action.nargs == 0 else 2
    # Put the positional locator immediately after the command for consumers;
    # all parsed option tokens and values retain their spelling/order.
    values.pop(i)
    if "--" in values[start:]:
        values.remove("--")
    values.insert(start, str(source))
    return values


def _config_from_args(args: argparse.Namespace) -> AnalysisConfig:
    return AnalysisConfig.from_env(
        workspace=Path(args.workspace) if getattr(args, "workspace", None) else None,
        output_root=Path(args.output_root) if getattr(args, "output_root", None) else None,
        cache_root=(
            Path(args.cache_root or getattr(args, "raw_data_dir", None))
            if args.cache_root or getattr(args, "raw_data_dir", None)
            else None
        ),
        temporary_directory=Path(args.temp_dir) if getattr(args, "temp_dir", None) else None,
        workers=getattr(args, "workers", None),
        log_level=getattr(args, "log_level", None),
        git_timeout_seconds=getattr(args, "git_timeout", None),
        git_retries=getattr(args, "git_retries", None),
        acquisition_deadline_seconds=getattr(args, "acquisition_deadline", None),
        auxiliary_warning_seconds=getattr(args, "auxiliary_warning_seconds", None),
        heartbeat_interval_seconds=getattr(args, "heartbeat_seconds", None),
        full_inventory=getattr(args, "full_inventory", None),
        require_clean_profiler=getattr(args, "require_clean_profiler", None),
        max_source_file_size_bytes=getattr(args, "max_source_file_size", None),
    )


def doctor_report(config: AnalysisConfig) -> dict[str, object]:
    """Report what capabilities exist in this environment.

    `doctor` and `run` share one capability model but answer different
    questions, so they deliberately do not share blocking semantics:

    * `doctor` asks **what exists here**. A missing Go grammar is a fact to
      report, not a failure — nothing has been asked for yet.
    * `run` asks **is everything this measurement needs available**, and only
      the capabilities the planned measurement actually requires can refuse it.

    So `healthy` here means "no globally required capability is missing". A
    grammar that some future run might need is reported with its state and
    never counted against this environment.
    """
    from modules import preflight

    report = preflight.stage_a(config, acquisition_mode="latest")
    checks = [check.as_dict() for check in report.checks]
    return {
        "healthy": not report.refused,
        "stage": report.stage.value,
        "question_answered": (
            "what capabilities exist in this environment; a capability no "
            "planned measurement requires is reported, never counted as failure"
        ),
        "workspace_root": str(config.workspace_root),
        "resolved_paths": {
            "cache_root": str(config.cache_root),
            "temporary_directory": str(config.temporary_directory),
            "output_root": str(config.output_root),
        },
        "checks": checks,
        "blocking_failures": [check.capability for check in report.refusals],
    }


def render_doctor_text(report: dict[str, object], *, details: bool = False) -> str:
    """Concise capability diagnosis; the JSON contract remains unchanged."""

    healthy = bool(report.get("healthy"))
    lines = [
        f"Metrolith doctor: {'healthy' if healthy else 'unhealthy'}",
        f"Blocking failures: {len(report.get('blocking_failures') or [])}",
    ]
    checks = report.get("checks") or []
    selected = checks if details else [
        item for item in checks if item.get("state") != "available"
    ]
    remediation = {
        "python_runtime": "Use a supported Python runtime and reinstall the wheel.",
        "git": "Install Git and make `git` available on PATH.",
        "workspace_directory": "Choose a writable workspace with --workspace.",
        "cache_directory": "Choose a writable cache root with --cache-root.",
        "temporary_directory": "Choose a writable temporary root with --temp-dir.",
        "output_directory": "Choose a writable output root with --output-root.",
        "jsonschema_runtime": "Install the exact dependency set from the Metrolith wheel.",
    }
    if not selected:
        lines.append("No unavailable or blocking capability was found.")
    for item in selected:
        capability = str(item.get("capability"))
        lines.append(
            f"- {capability}: {item.get('state')}"
            + (" [blocking]" if item.get("blocking") else "")
        )
        lines.append(f"  Evidence: {item.get('evidence') or 'not supplied'}")
        if item.get("state") != "available":
            action = remediation.get(
                capability,
                "Review the capability evidence and install or configure that dependency.",
            )
            lines.append(f"  Remediation: {action}")
    lines.append("Next: metrolith example run --local")
    return "\n".join(lines)


def _quickstart_specs(language: str | None = None) -> list[RepositorySpec]:
    path = PACKAGE_ROOT / "examples" / "quickstart_repositories.csv"
    specs = load_repositories_csv(path)
    if language is None:
        return specs
    return [
        spec
        for spec in specs
        if spec.expected_language and spec.expected_language.casefold() == language.casefold()
    ]


def _print_completion_summary(summary: dict[str, object], *, single: bool) -> None:
    from modules.presentation import (
        measurement,
        terminal_path,
        terminal_command,
        source_failure,
        recognized_source_count,
        status as display_status,
        subject_display,
    )

    run_directory = Path(str(summary["run_directory"]))
    display_run = terminal_path(run_directory)
    if single and summary.get("results"):
        cause = source_failure(summary["results"][0])
        if cause:
            print(cause)
    print("Metrolith final result")
    print("=" * 52)
    print(f"Final run state  {display_status(summary['status'])}")
    print(f"Planned          {summary['planned_repository_count']}")
    print(f"Processed        {summary['repository_count']}")
    print(f"Complete         {summary['success_count']}")
    print(f"Partial          {summary['partial_count']}")
    print(f"Failed           {summary['failure_count']}")
    if single and summary.get("results"):
        result = summary["results"][0]
        identity = subject_display(result)
        aggregate = result.get("metrics", {}).get("aggregate", {})
        print()
        print(f"Subject          {identity.name}")
        print(f"Subject key      {identity.subject_key}")
        if identity.repository_locator:
            print(f"Locator          {identity.repository_locator}")
        mode = result.get("source_mode")
        sha = result.get("acquisition", {}).get("analyzed_commit_sha")
        if mode == "local_worktree_snapshot":
            options = (summary.get("local_source_options") or {}).get(identity.subject_key) or {}
            tracked_only = options.get("tracked_only")
            print("Source           working files snapshot" + (
                "; tracked-only, including modified tracked bytes; untracked files excluded"
                if tracked_only is True else "; tracked and non-ignored untracked files"
                if tracked_only is False else ""
            ))
            print(f"HEAD reference   {sha or 'unavailable'} (reference only)")
            evidence = result.get("local_source_evidence") or {}
            if "modified_tracked_count" in evidence:
                print(f"Modified tracked {evidence['modified_tracked_count']}")
            if "untracked_included_count" in evidence:
                print(f"Untracked included {evidence['untracked_included_count']}")
        elif mode == "local_directory_snapshot":
            print("Source           directory snapshot (non-Git)")
        else:
            print(f"Source           exact revision; analyzed commit {sha or 'unavailable'}")
        print(f"Overall status   {display_status(result.get('analysis_status'))}")
        for label, value_key, status_key in (
            ("Lines of Code", "lines_of_code", "loc_status"),
            ("Source Files", "source_files", "source_files_status"),
            ("Classes/Structs", "classes_structs", "classes_structs_status"),
            ("Methods/Functions", "methods_functions", "methods_functions_status"),
        ):
            print(
                f"{label:<20}{measurement(aggregate.get(value_key), aggregate.get(status_key))} "
                f"({display_status(aggregate.get(status_key))})"
            )
        recognized = recognized_source_count(aggregate)
        print(f"Source recognized {'unavailable' if recognized is None else 'yes' if recognized > 0 else 'no'}")
        if recognized == 0:
            print()
            print("No recognized source files were found.")
    print()
    print(f"Run directory    {display_run}")
    human_artifact = terminal_path(summary["summary_markdown"])
    print(f"Readable result  {human_artifact}")
    if single and summary.get("results"):
        aggregate = summary["results"][0].get("metrics", {}).get("aggregate", {})
        recognized = recognized_source_count(aggregate)
        next_command = (
            terminal_command("report", run_directory)
            if recognized
            else terminal_command("explain", run_directory)
        )
    else:
        next_command = terminal_command("report", run_directory)
    print(f"Next: {next_command}")


def build_cli():
    parser = argparse.ArgumentParser(
        prog="metrolith",
        usage="%(prog)s [--version] COMMAND ...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Start locally: metrolith analyze .\n"
            "Inspect the printed run path: metrolith report RUN\n"
            "Explain exclusions and unavailable evidence: metrolith explain RUN\n"
            "Network-free tutorial: metrolith example run --local\n\n"
            "Metrolith measures versioned multilingual source evidence.\n"
            "Evidence, not scores."
        ),
        epilog="""Task-oriented commands:
  Start and inspect       analyze, report, explain, doctor
  Add evidence            duplication, hotspots, changed
  Govern                  policy, check, baseline, trust
  Compare and reproduce   validate, compare, diff, reproduce, dossier, schema
  Pipeline maintenance    run, audit, migrate-input, perf, example, menu

Policy authoring example (100000 is author-supplied, not a default):
  metrolith policy init --output policy.json --metric repository.lines_of_code --operator gt --threshold 100000 --severity warning

For a packaged network-free tutorial, run `metrolith example run --local`.

Inspect summary.md and language_metrics.csv for completeness and language detail.
Use explain for exclusions, partial states and unavailable evidence.
Reproducibility is scoped to declared inputs, contracts, and environment;
whole run bundles are not promised to be byte-identical.""",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Metrolith {PROGRAM_VERSION}",
    )

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser(
        "run",
        help="Run pipeline step(s)",
        description=(
            "Run the canonical repository pipeline. Exit 0 = completed (including "
            "an explicitly partial run), 1 = failed, 2 = invalid usage, and 4 = "
            "environment preflight refused. Git must be on PATH for all acquisition modes."
        ),
    )
    run.add_argument(
        "step",
        nargs="?",
        default="metrics",
        type=_parse_run_step,
        metavar="{metrics,all}",
        help="Execution stage (default: metrics; use all for optional auxiliary analyses)",
    )
    run.add_argument(
        "--acquisition-mode",
        choices=["latest", "frozen", "offline"],
        default="latest",
        help="Git revision acquisition mode (default: latest)",
    )
    run.add_argument(
        "--commit",
        "--commit-sha",
        dest="commit_sha",
        help="Analyze this commit (required for frozen/offline single-repository mode)",
    )
    run.add_argument(
        "--expected-language",
        choices=[language.lower() for language in SUPPORTED_LANGUAGES],
        help="Optional primary-language expectation for --repo",
    )
    run.add_argument(
        "--architecture-type",
        choices=["monolith", "microservices", "unknown"],
        default="unknown",
        help="Research metadata label for --repo (default: unknown)",
    )
    run.add_argument("--workers", type=int, default=None, help="Bounded repository workers (default: 1)")
    _add_path_options(run)
    run.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    run.add_argument(
        "--git-timeout",
        type=int,
        help="Timeout in seconds for every Git subprocess (default: 900)",
    )
    run.add_argument(
        "--git-retries",
        type=int,
        help="Maximum attempts for transient remote Git failures (default: 3)",
    )
    run.add_argument("--max-source-file-size", type=int, help="Maximum source file bytes to parse")
    run.add_argument(
        "--acquisition-deadline",
        type=int,
        help="Maximum total acquisition seconds per repository (default: 1800)",
    )
    run.add_argument(
        "--auxiliary-warning-seconds",
        type=int,
        help="Warning threshold for a running auxiliary module (default: 120)",
    )
    run.add_argument(
        "--heartbeat-seconds",
        type=int,
        help="Heartbeat repeat interval for long stages (default: 60)",
    )
    run.add_argument(
        "--full-inventory",
        action="store_true",
        default=None,
        help="Record the slower forensic inventory in metrics mode",
    )
    run.add_argument(
        "--require-clean-profiler",
        action="store_true",
        default=None,
        help=(
            "Block before acquisition unless the profiler has a commit SHA, "
            "a clean working tree, and a hashable exclusion policy"
        ),
    )
    run.add_argument(
        "--qualification-registry",
        dest="qualification_registry",
        default=None,
        help=(
            "Accepted benchmark qualification registry. Supplying it selects "
            "benchmark-qualified mode, in which the run bundle also carries an "
            "authoritative benchmark_qualification.json and a repository-level "
            "comparison projection. Without it the run is an ordinary generic "
            "analysis and carries no qualification artifact at all."
        ),
    )
    run_input = run.add_mutually_exclusive_group(required=True)
    run_input.add_argument(
        "--input",
        action="append",
        dest="input_files",
        help="Canonical repositories.csv (or repeatable deprecated TXT files)",
    )
    run_input.add_argument(
        "--repo",
        help="Analyze one canonical GitHub repository through the same metrics pipeline",
    )

    analyze = sub.add_parser(
        "analyze",
        help="Analyze one local directory, Git worktree, or Git revision",
        description=(
            "Analyze one local source state through the canonical pipeline without "
            "modifying the source. Exit 0 = completed (including an explicitly "
            "partial run), 1 = failed, 2 = invalid source/usage, and 4 = environment "
            "preflight refused. Git must be on PATH even for non-Git directory snapshots; "
            "the analyzed directory need not be a Git repository."
        ),
    )
    analyze.add_argument(
        "path", help="Local path to a source directory or Git repository"
    )
    analyze.add_argument(
        "--revision",
        help=(
            "Analyze this exact committed revision instead of the working tree. "
            "Materialized into Metrolith-controlled storage; your repository is "
            "never modified."
        ),
    )
    analyze.add_argument(
        "--tracked-only",
        action="store_true",
        help=(
            "Snapshot only Git-tracked files. By default a worktree snapshot "
            "also includes untracked files that Git is not ignoring."
        ),
    )
    analyze.add_argument(
        "--subject-key",
        help=(
            "Logical subject identity used to join runs. Supply it to declare "
            "that a local clone and a remote repository are the same subject."
        ),
    )
    analyze.add_argument(
        "--expected-language",
        choices=[language.lower() for language in SUPPORTED_LANGUAGES],
        help="Optional primary-language expectation",
    )
    analyze.add_argument(
        "--architecture-type",
        choices=["monolith", "microservices", "unknown"],
        default="unknown",
        help="Research metadata label (default: unknown)",
    )
    _add_path_options(analyze)
    analyze.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

    audit = sub.add_parser(
        "audit", help="Audit JavaScript/TypeScript and Go metrics for one repository"
    )
    audit.add_argument("repo_url", help="GitHub repository URL")
    audit.add_argument(
        "--raw-data-dir",
        help=(
            "Deprecated alias for the bare Git object cache root. Defaults to "
            "METROLITH_CACHE_ROOT or data/git-cache."
        ),
    )
    run.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first repository error instead of recording it in the report",
    )
    run.add_argument(
        "--raw-data-dir",
        help=(
            "Deprecated alias for --cache-root. Use a short path on Windows, "
            "for example C:\\arch_cache."
        ),
    )
    run.add_argument(
        "--verbose",
        "--debug",
        action="store_true",
        dest="verbose",
        help="Show detailed tracebacks for repository and command failures",
    )

    audit.add_argument(
        "--verbose",
        "--debug",
        action="store_true",
        dest="verbose",
        help="Show a detailed traceback if the audit command fails",
    )

    sub.add_parser("menu", help="Open the interactive terminal menu")

    migrate = sub.add_parser(
        "migrate-input", help="Convert deprecated filename-labeled TXT lists to repositories.csv"
    )
    migrate.add_argument(
        "--input",
        action="append",
        dest="input_files",
        help="Legacy TXT file (repeatable); defaults to input/*.txt",
    )
    migrate.add_argument(
        "--output",
        default=str(INPUT_DIR / "repositories.csv"),
        help="Canonical CSV destination",
    )

    example = sub.add_parser("example", help="List or run packaged frozen examples")
    example_sub = example.add_subparsers(dest="example_command", required=True)
    example_sub.add_parser("list", help="List packaged examples")
    example_run = example_sub.add_parser(
        "run", help="Run the network-free local tutorial or a frozen remote example",
        description=(
            "Run a packaged example. Git must be on PATH, including for --local. "
            "The local tutorial needs no network after installation. Exit 0 = "
            "completed (possibly partial), 1 = failed including preflight refusal, "
            "2 = invalid command-line usage."
        ),
    )
    example_choice = example_run.add_mutually_exclusive_group()
    example_choice.add_argument(
        "--language",
        choices=["java", "javascript", "python", "go"],
        default="java",
    )
    example_choice.add_argument("--all", action="store_true", dest="all_examples")
    example_choice.add_argument(
        "--local",
        action="store_true",
        help="Run the packaged Python/JavaScript tutorial without network access (Git required)",
    )
    example_run.add_argument(
        "--acquisition-mode",
        choices=["frozen", "offline"],
        default="frozen",
    )
    example_run.add_argument("--workers", type=int, default=None)
    example_run.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    example_run.add_argument(
        "--require-clean-profiler", action="store_true", default=None
    )
    _add_path_options(example_run)

    doctor = sub.add_parser(
        "doctor",
        help="Check the local Metrolith installation",
        description=(
            "Report local capabilities as JSON. Exit 0 = no blocking capability "
            "failure, 1 = unhealthy/error, and 2 = invalid usage."
        ),
    )
    doctor.add_argument(
        "--format", choices=("json", "text"), default="json",
        help="Output format (default: json for compatibility)",
    )
    doctor.add_argument(
        "--details", "--verbose", action="store_true", dest="details",
        help="In text mode, include available non-blocking capabilities",
    )
    _add_path_options(doctor)

    validate = sub.add_parser(
        "validate",
        help="Validate one Metrolith run directory",
        description=(
            "Validate a persisted run (or only its structural schemas). Exit 0 = "
            "passed, 1 = invalid/unreadable/error, and 2 = invalid usage."
        ),
    )
    validate.add_argument("run_directory", type=Path)
    validate.add_argument(
        "--format", choices=("json", "text"), default="json",
        help="Output format (default: json for compatibility)",
    )
    validate.add_argument(
        "--details", "--verbose", action="store_true", dest="details",
        help="In text mode, include checked-document details",
    )
    validate.add_argument(
        "--schema-only",
        action="store_true",
        help=(
            "Check structural schema conformance only. Semantic validity is NOT "
            "assessed and the report says so explicitly."
        ),
    )

    compare = sub.add_parser("compare", help="Compare two or more Metrolith runs")
    compare.add_argument("run_directories", nargs="+", type=Path)
    # Default N-run behaviour is unchanged; --explain adds a two-run mode with
    # its own documented exit-code contract (plan section 15.1).
    from modules.cli import compare_command as _compare_command

    _compare_command.add_explain_arguments(compare)

    # Handlers live under modules.cli (plan section 20); pipeline.py only
    # assembles the parser and delegates.
    from modules.cli import (
        changed_command, check_command, diff_command, dossier_command, duplication_command,
        explain_command, hotspots_command,
        performance_command, policy_command, report_command, reproduce_command,
        schema_command, trust_command,
    )

    schema_command.add_parser(sub)
    explain_command.add_parser(sub)
    reproduce_command.add_parser(sub)
    report_command.add_parser(sub)
    performance_command.add_parser(sub)
    # `diff` needs the workspace/output path options because it may run two
    # analyses; `compare` does not, because it only reads existing runs.
    _add_path_options(diff_command.add_parser(sub))
    # `policy` reads a published run and needs no workspace paths.
    policy_command.add_parser(sub)

    from modules.cli import baseline_command

    baseline_command.add_parser(sub)
    # `check` likewise reads a published run only. It never re-analyzes: a gate
    # that measured would be a second measurement path.
    check_command.add_parser(sub)
    # `hotspots` is a derived read-only consumer. It loads existing metric
    # ledgers and Git objects; it never invokes repository measurement.
    hotspots_command.add_parser(sub)
    # `duplication` analyzes one immutable local source snapshot and emits a
    # standalone result. It does not create or modify a run bundle.
    duplication_command.add_parser(sub)
    # `changed` compares two explicit commits, reuses canonical metric runs,
    # and emits a standalone derived document. It is not a Policy gate.
    changed_command.add_parser(sub)
    # `dossier` composes evidence that already exists. It reads one run through
    # the same artifact reader as `report` and admits optional standalone
    # documents beside it; it analyzes nothing and merges nothing.
    dossier_command.add_parser(sub)
    # `trust receipt create` consumes already-produced canonical evidence and
    # binds it to a finalized run. It performs no Hotspot/Duplication analysis.
    trust_command.add_parser(sub)

    return parser


# =====================================================================
# ENTRY POINT
# =====================================================================
_SUPPORTED_RUN_STEPS = frozenset({"metrics", "all"})
_LEGACY_RUN_STEPS = frozenset({
    "metadata", "clone", "static", "endpoints", "deployability", "db",
    "coverage", "classify", "export", "facts",
})


def _parse_run_step(value: str) -> str:
    """Accept only execution modes the canonical runner actually implements."""
    if value in _SUPPORTED_RUN_STEPS:
        return value
    if value in _LEGACY_RUN_STEPS:
        raise argparse.ArgumentTypeError(
            f"legacy stage selector {value!r} is no longer supported; "
            "migrate to 'metrics' or 'all'"
        )
    raise argparse.ArgumentTypeError(
        f"unsupported execution mode {value!r}; expected 'metrics' or 'all'"
    )


def main():
    from modules.cli.transport import (
        DeliveryError, cli_streams, report_delivery_failure,
        silence_failed_standard_stream,
    )

    with cli_streams(sys.argv[1:]) as (out, err):
        try:
            try:
                return _main()
            finally:
                out.flush()
                err.flush()
        except DeliveryError as exc:
            report_delivery_failure(exc, err)
            silence_failed_standard_stream(out)
            silence_failed_standard_stream(err)
            # Delivery is operational, never a Check violation. A result that
            # was already written keeps its actual evaluation verdict.
            raise SystemExit(2 if sys.argv[1:2] in (["check"], ["policy"]) else 1) from None


def _main():
    parser = build_cli()
    args = parser.parse_args()

    if args.command == "run":
        try:
            config = _config_from_args(args)
            repository_specs = None
            single_repository = bool(args.repo)
            if args.repo:
                language = next(
                    (
                        candidate
                        for candidate in SUPPORTED_LANGUAGES
                        if args.expected_language
                        and candidate.casefold() == args.expected_language.casefold()
                    ),
                    None,
                )
                repository_specs = [
                    RepositorySpec(
                        url=canonicalize_github_url(args.repo),
                        architecture_type=args.architecture_type,
                        expected_language=language,
                        commit_sha=args.commit_sha.strip().lower() if args.commit_sha else None,
                        enabled=True,
                    )
                ]
            elif args.expected_language or args.architecture_type != "unknown":
                raise ValueError(
                    "--expected-language and --architecture-type are valid only with --repo"
                )
            summary = run_benchmark(
                input_paths=args.input_files if not single_repository else None,
                repository_specs=repository_specs,
                config=config,
                acquisition_mode=args.acquisition_mode,
                fail_fast=args.fail_fast,
                command_line_arguments=sys.argv[1:],
                commit_sha_override=args.commit_sha if not single_repository else None,
                execution_mode="metrics" if args.step == "metrics" else "all",
                single_repository=single_repository,
                qualification_registry_path=getattr(
                    args, "qualification_registry", None
                ),
            )
            _print_completion_summary(summary, single=single_repository)
            if summary["status"] == "failed":
                sys.exit(1)
        except PreflightRefused as refusal:
            # A capability refusal is not an analysis failure and not a usage
            # error, so it gets its own exit code. The message states the root
            # capability failure once; a traceback here would bury a diagnosis
            # the preflight already made precisely.
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [REFUSED] "
                f"{refusal.report.summary()}",
                flush=True,
            )
            sys.exit(EXIT_PREFLIGHT_REFUSED)
        except Exception as exc:
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] Pipeline failed: "
                f"{_terminal_error(exc)}",
                flush=True,
            )
            if args.verbose:
                traceback.print_exc()
            sys.exit(1)
    elif args.command == "example":
        if args.example_command == "list":
            print("local\tPackaged Python/JavaScript fixture\t(no network)")
            print("Language\tRepository\tCommit")
            for spec in _quickstart_specs():
                print(f"{spec.expected_language}\t{spec.url}\t{spec.commit_sha}")
        else:
            try:
                if args.local:
                    from importlib.resources import as_file, files

                    print(
                        "Tutorial input only: this packaged fixture is not "
                        "benchmark or research evidence."
                    )
                    resource = files("examples").joinpath("local_project")
                    with as_file(resource) as fixture:
                        specs = [RepositorySpec(
                            url="",
                            architecture_type="unknown",
                            local_path=str(fixture),
                            subject_key="example:network-free",
                            local_name="Metrolith network-free example",
                        )]
                        summary = run_benchmark(
                            repository_specs=specs,
                            config=_config_from_args(args),
                            acquisition_mode="offline",
                            execution_mode="metrics",
                            command_line_arguments=sys.argv[1:],
                            single_repository=True,
                        )
                else:
                    specs = (
                        _quickstart_specs()
                        if args.all_examples
                        else _quickstart_specs(args.language)
                    )
                    if not specs:
                        raise ValueError(f"No packaged example for {args.language}")
                    summary = run_benchmark(
                        repository_specs=specs,
                        config=_config_from_args(args),
                        acquisition_mode=args.acquisition_mode,
                        execution_mode="metrics",
                        command_line_arguments=sys.argv[1:],
                        single_repository=len(specs) == 1,
                    )
                _print_completion_summary(summary, single=len(specs) == 1)
                if summary["status"] == "failed":
                    raise SystemExit(1)
            except Exception as exc:
                print(f"[ERROR] Example run failed: {_terminal_error(exc)}", file=sys.stderr)
                raise SystemExit(1)
    elif args.command == "doctor":
        report = doctor_report(_config_from_args(args))
        if getattr(args, "format", "json") == "text":
            print(render_doctor_text(report, details=bool(getattr(args, "details", False))))
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        if not report["healthy"]:
            raise SystemExit(1)
    elif args.command == "schema":
        from modules.cli import schema_command

        raise SystemExit(schema_command.handle(args))
    elif args.command == "explain":
        from modules.cli import explain_command

        raise SystemExit(explain_command.handle(args))
    elif args.command == "reproduce":
        from modules.cli import reproduce_command

        raise SystemExit(reproduce_command.handle(args))
    elif args.command == "report":
        from modules.cli import report_command

        raise SystemExit(report_command.handle(args))
    elif args.command == "perf":
        from modules.cli import performance_command

        raise SystemExit(performance_command.handle(args))
    elif args.command == "validate":
        try:
            if getattr(args, "schema_only", False):
                from modules.cli.validate_command import schema_only_report

                report = schema_only_report(args.run_directory.resolve())
            else:
                from validation.scripts.validate_outputs import validate_run

                report = validate_run(args.run_directory.resolve())
            if getattr(args, "format", "json") == "text":
                from modules.cli.validate_command import render_text

                print(render_text(report, details=bool(getattr(args, "details", False))))
            else:
                print(json.dumps(report, indent=2, sort_keys=True))
            if not report["passed"]:
                raise SystemExit(1)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Validation failed: {_terminal_error(exc)}", file=sys.stderr)
            raise SystemExit(1)
    elif args.command == "policy":
        from modules.cli import policy_command

        raise SystemExit(policy_command.handle(args))
    elif args.command == "baseline":
        from modules.cli import baseline_command

        raise SystemExit(baseline_command.handle(args))
    elif args.command == "check":
        from modules.cli import check_command

        raise SystemExit(check_command.handle(args))
    elif args.command == "hotspots":
        from modules.cli import hotspots_command

        raise SystemExit(hotspots_command.handle(args))
    elif args.command == "duplication":
        from modules.cli import duplication_command

        raise SystemExit(duplication_command.handle(args))
    elif args.command == "trust":
        from modules.cli import trust_command

        raise SystemExit(trust_command.handle(args))
    elif args.command == "changed":
        from modules.cli import changed_command

        raise SystemExit(changed_command.handle(args, _config_from_args(args)))
    elif args.command == "dossier":
        from modules.cli import dossier_command

        raise SystemExit(dossier_command.handle(args))
    elif args.command == "diff":
        from modules.cli import diff_command

        raise SystemExit(diff_command.handle(args, _config_from_args(args)))
    elif args.command == "compare":
        if getattr(args, "explain", False):
            from modules.cli import compare_command

            raise SystemExit(compare_command.handle(args))
        if len(args.run_directories) < 2:
            parser.error("compare requires at least two run directories")
        try:
            from validation.scripts.compare_semantic_runs import differences
            from validation.scripts.validate_outputs import semantic_payload

            baseline = args.run_directories[0].resolve()
            baseline_payload = semantic_payload(baseline)
            comparisons = []
            for candidate_value in args.run_directories[1:]:
                candidate = candidate_value.resolve()
                mismatch = differences(baseline_payload, semantic_payload(candidate))
                comparisons.append(
                    {
                        "baseline": str(baseline),
                        "candidate": str(candidate),
                        "semantically_equal": not mismatch,
                        "difference_count": len(mismatch),
                        "differences": mismatch,
                    }
                )
            report = {
                "all_semantically_equal": all(
                    item["semantically_equal"] for item in comparisons
                ),
                "comparisons": comparisons,
            }
            print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
            if not report["all_semantically_equal"]:
                raise SystemExit(1)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Comparison failed: {_terminal_error(exc)}", file=sys.stderr)
            raise SystemExit(1)
    elif args.command == "analyze":
        try:
            language = next(
                (
                    candidate
                    for candidate in SUPPORTED_LANGUAGES
                    if args.expected_language
                    and candidate.casefold() == args.expected_language.casefold()
                ),
                None,
            )
            source = Path(args.path).expanduser().resolve()
            if not source.is_dir():
                print(f"[ERROR] Not a directory: {source}. Choose an existing source directory, then rerun.", file=sys.stderr)
                sys.exit(2)
            spec = RepositorySpec(
                url="",
                architecture_type=args.architecture_type,
                expected_language=language,
                enabled=True,
                local_path=str(source),
                revision=args.revision,
                tracked_only=args.tracked_only,
                subject_key=args.subject_key,
            )
            summary = run_benchmark(
                repository_specs=[spec],
                config=_config_from_args(args),
                acquisition_mode="offline",
                command_line_arguments=_record_analyze_source(sys.argv[1:], parser, source),
                single_repository=True,
            )
            _print_completion_summary(summary, single=True)
            if summary["status"] == "failed":
                sys.exit(1)
        except PreflightRefused as refusal:
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [REFUSED] "
                f"{refusal.report.summary()}",
                flush=True,
            )
            sys.exit(EXIT_PREFLIGHT_REFUSED)
        except Exception as exc:
            print(f"[ERROR] Analysis failed: {_terminal_error(exc)}", file=sys.stderr)
            if getattr(args, "verbose", False):
                traceback.print_exc()
            sys.exit(1)
    elif args.command == "audit":
        try:
            print_audit_report(
                audit_repository_safe(args.repo_url, args.raw_data_dir)
            )
        except Exception as exc:
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] Audit failed: "
                f"{_terminal_error(exc)}",
                flush=True,
            )
            if args.verbose:
                traceback.print_exc()
            sys.exit(1)
    elif args.command == "menu":
        run_interactive_menu()
    elif args.command == "migrate-input":
        try:
            paths = args.input_files or sorted(INPUT_DIR.glob("*.txt"), key=lambda path: path.name.lower())
            output = migrate_legacy_inputs(paths, args.output)
            print(f"Migrated {len(paths)} legacy input file(s) to {output}")
            print("[WARN] Legacy TXT input remains supported temporarily but is deprecated.")
        except Exception as exc:
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] Input migration failed: "
                f"{_terminal_error(exc)}",
                flush=True,
            )
            sys.exit(1)
    else:
        parser.print_help()


def archlens_compat_main():
    """Warning-only compatibility entry point for the former canonical command."""
    print(
        "`archlens` is a deprecated compatibility alias; use `metrolith`.",
        file=sys.stderr,
        flush=True,
    )
    return main()


def deprecated_main():
    """Compatibility entry point for the older ARCH-Bench console command."""
    print(
        "WARNING: 'arch-bench' is deprecated; use 'metrolith' instead.",
        file=sys.stderr,
        flush=True,
    )
    return main()


if __name__ == "__main__":
    main()
