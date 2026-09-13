#!/usr/bin/env python3
"""Producer/schema closure audit: literal validation, no compatibility waivers.

Builds a deliberately varied set of small runs through the **real** pipeline and
artifact writer, then validates every emitted document against the literal
frozen schemas with every known compatibility exception switched **off**.

The point is to establish that the set of producer/schema mismatches is closed —
that the four known corrective findings are *all* of them — before a corrective
Artifact Schema version is defined. A correction that fixes three of five
problems is worse than none, because it looks finished.

Run:

    python -m validation.scripts.schema_closure_audit --output <path.json>
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from modules.acquisition import (
    AcquiredRepository,
    AcquisitionError,
    AcquisitionRecord,
)
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.repository_input import RepositorySpec
from validation.artifact_io.schema_store import validate_document
from validation.artifact_io.strict_json import load_document

ANALYZED_SHA = "a" * 40

#: Documents validated, as (schema name, run-relative path or glob).
SINGLE_DOCUMENTS = (
    ("run_manifest", "run_manifest.json"),
    ("run_status", "run_status.json"),
    ("environment", "environment.json"),
    ("analysis", "analysis.json"),
)
DOCUMENT_FAMILIES = (
    ("file_inventory", "file_inventory"),
    ("repository_document", "repositories"),
)

#: Known corrective findings, matched on (schema, pointer shape, keyword).
KNOWN = {
    ("run_manifest", "/output_failures/mandatory/*", "type"): "F-P3-3",
    ("run_manifest", "/output_failures/optional/*", "type"): "F-P3-3",
    ("run_status", "/measurement_outcome", "enum"): "F-P3-4",
    ("repository_document", "/input_line", "minimum"): "F-P3-5",
    ("repository_document", "/expected_language", "type"): "F-P3-10",
    ("analysis", "/*/expected_language", "type"): "F-P3-10",
    ("analysis", "/*/input_line", "minimum"): "F-P3-5",
    ("file_inventory", "/files/*/git_mode", "type"): "F-P3-6",
}


def _generalize(location: str) -> str:
    """Collapse array indices so pointers group across elements."""
    return re.sub(r"/\d+(?=/|$)", "/*", location or "")


# ------------------------------------------------------------------ sources ---

SOURCES: dict[str, dict[str, str]] = {
    "python_clean": {
        "app.py": "class App:\n    def run(self):\n        return 1\n",
    },
    "python_broken": {
        "app.py": "class App:\n    def run(self):\n        return 1\n",
        "broken.py": "def oops(:\n",
    },
    "all_broken": {
        "broken.py": "def oops(:\n",
        "worse.py": "class ??\n",
    },
    "multi_language": {
        "app.py": "class App:\n    def run(self):\n        return 1\n",
        "Service.java": "package a;\npublic class Service { public void run() {} }\n",
        "main.go": "package main\n\nfunc main() {}\n",
        "app.js": "class A { b() { return 1; } }\n",
        "app.ts": "export class A { b(): number { return 1; } }\n",
    },
    "empty_repository": {
        "README.md": "# nothing to measure\n",
    },
}


def _acquire_local(repo: Path) -> Callable:
    @contextmanager
    def acquire(spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha=ANALYZED_SHA,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    return acquire


@contextmanager
def _acquire_failure(*args, **kwargs):
    del args, kwargs
    raise AcquisitionError("checkout_not_clean", "simulated acquisition failure")
    yield  # pragma: no cover - keeps this a context manager


def _build(
    root: Path,
    sources: dict[str, str],
    *,
    use_specs: bool,
    acquire: Callable | None,
    no_expected_language: bool = False,
) -> Path | None:
    """Produce one run. Returns its directory, or None if none was created."""
    repo = root / "src"
    repo.mkdir(parents=True, exist_ok=True)
    for name, text in sources.items():
        (repo / name).write_text(text, encoding="utf-8")

    config = AnalysisConfig.from_env(
        workspace=root,
        output_root=root / "output",
        cache_root=root / "cache",
        temporary_directory=root / "temp",
        workers=1,
    )
    acquire = acquire or _acquire_local(repo)

    kwargs: dict[str, Any] = {}
    if use_specs:
        # `repository_specs` leaves RepositorySpec.input_line at its 0 default,
        # which is the F-P3-5 sentinel.
        kwargs["repository_specs"] = [
            RepositorySpec(
                "https://github.com/acme/mono", "monolith", "Python", ANALYZED_SHA
            )
        ]
        positional: list[Any] = []
    else:
        source_csv = root / "repositories.csv"
        # An empty expected_language column is legal input and makes the
        # producer emit null, which is the F-P3-10 case.
        language = "" if no_expected_language else "Python"
        source_csv.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,{language},{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )
        positional = [[source_csv]]

    with (
        patch("modules.benchmark_runner.acquire_repository", acquire),
        patch("modules.benchmark_runner._distribution_version", return_value="3.5.0"),
    ):
        summary = run_benchmark(
            *positional,
            config=config,
            acquisition_mode="offline",
            command_line_arguments=["closure-audit"],
            **kwargs,
        )
    directory = summary.get("run_directory")
    return Path(directory) if directory else None


SCENARIOS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("successful_complete_measurement", {"sources": "python_clean", "use_specs": False}),
    ("partial_measurement_parser_failure", {"sources": "python_broken", "use_specs": False}),
    ("all_repositories_failed", {"sources": "all_broken", "use_specs": False}),
    ("acquisition_failure", {"sources": "python_clean", "use_specs": False, "fail": True}),
    ("input_line_zero_via_specs", {"sources": "python_clean", "use_specs": True}),
    ("untracked_inventory_null_git_mode", {"sources": "python_clean", "use_specs": False}),
    ("multi_language", {"sources": "multi_language", "use_specs": False}),
    ("zero_entity_repository", {"sources": "empty_repository", "use_specs": False}),
    # A mandatory output write that fails. This is the only way to populate
    # `output_failures.mandatory`, and therefore the only scenario that can
    # exercise F-P3-3 at all.
    # No expected-language expectation supplied. RepositorySpec.expected_language
    # is optional, so this emits null -- the F-P3-10 case, and the one the
    # original scenario set missed because every scenario named a language.
    ("no_expected_language", {
        "sources": "python_clean", "use_specs": False, "no_expected_language": True,
    }),
    ("mandatory_output_failure", {
        "sources": "python_clean", "use_specs": False, "break_output": True,
    }),
)


@contextmanager
def _failing_csv_write():
    """Make one mandatory CSV write fail, without disturbing the others."""
    from modules import run_artifacts

    original = run_artifacts.atomic_write_csv

    def failing(path, *args, **kwargs):
        if Path(path).name == "language_metrics.csv":
            raise OSError("simulated mandatory output failure")
        return original(path, *args, **kwargs)

    with patch.object(run_artifacts, "atomic_write_csv", failing):
        yield


def _validate_run(run: Path) -> list[dict[str, Any]]:
    """Every document, literal schema, no waivers."""
    findings: list[dict[str, Any]] = []

    def check(schema_name: str, relative: str, expect: type = dict) -> None:
        path = run / relative
        if not path.exists():
            return
        try:
            document = load_document(path, relative, expect=expect)
        except Exception as exc:  # decoding failure is itself a finding
            findings.append({
                "schema": schema_name, "artifact": relative,
                "location": "", "keyword": "decode",
                "message": f"{type(exc).__name__}: {exc}",
            })
            return
        for error in validate_document(schema_name, document, relative):
            findings.append({
                "schema": schema_name,
                "artifact": relative,
                "location": error.location or "",
                "keyword": str((error.detail or {}).get("keyword", "")),
                "message": error.message,
            })

    for schema_name, relative in SINGLE_DOCUMENTS:
        check(schema_name, relative, expect=list if relative == "analysis.json" else dict)
    for schema_name, family in DOCUMENT_FAMILIES:
        directory = run / family
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                check(schema_name, f"{family}/{path.name}")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for name, spec in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix=f"closure_{name}_") as raw:
            root = Path(raw)
            try:
                if spec.get("break_output"):
                    with _failing_csv_write():
                        run = _build(
                            root, SOURCES[spec["sources"]],
                            use_specs=spec["use_specs"], acquire=None,
                        )
                else:
                    run = _build(
                        root,
                        SOURCES[spec["sources"]],
                        use_specs=spec["use_specs"],
                        acquire=_acquire_failure if spec.get("fail") else None,
                        no_expected_language=spec.get("no_expected_language", False),
                    )
            except Exception as exc:
                scenarios.append({
                    "scenario": name, "built": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            if run is None or not run.exists():
                scenarios.append({"scenario": name, "built": False, "error": "no run directory"})
                continue

            findings = _validate_run(run)
            status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
            scenarios.append({
                "scenario": name,
                "built": True,
                "run_status": status.get("status"),
                "measurement_outcome": status.get("measurement_outcome"),
                "violation_count": len(findings),
            })
            for item in findings:
                key = (item["schema"], _generalize(item["location"]), item["keyword"])
                entry = grouped.setdefault(key, {
                    "schema": key[0], "location_shape": key[1], "keyword": key[2],
                    "finding_id": KNOWN.get(key),
                    "occurrences": 0, "scenarios": set(), "example": item["message"],
                })
                entry["occurrences"] += 1
                entry["scenarios"].add(name)

    mismatches = []
    for entry in grouped.values():
        entry["scenarios"] = sorted(entry["scenarios"])
        mismatches.append(entry)
    mismatches.sort(key=lambda item: (item["schema"], item["location_shape"]))

    unknown = [item for item in mismatches if item["finding_id"] is None]
    report = {
        "waivers_applied": False,
        "scenarios": scenarios,
        "distinct_mismatches": len(mismatches),
        "mismatches": mismatches,
        "known_finding_ids": sorted({
            item["finding_id"] for item in mismatches if item["finding_id"]
        }),
        "unknown_mismatch_count": len(unknown),
        "unknown_mismatches": unknown,
        "closed": not unknown,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
