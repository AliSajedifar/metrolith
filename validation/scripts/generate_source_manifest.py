#!/usr/bin/env python3
"""Deterministic ArchLens source content manifest generator (Plan 3.5.0 section 6.1).

Read-only. Hashes the ArchLens program source, tests, packaging configuration,
documentation, and validation scripts so that later implementation drift is
detectable without requiring a Git commit.

Design rules enforced here:

* The ``entries_digest`` covers only the sorted file-entry table, so two runs on
  the same bytes agree exactly regardless of clock, host, or interpreter.
* Environment facts are recorded from the interpreter that actually runs this
  generator. When a fact cannot be observed it is stored as ``null`` together
  with an explicit ``*_evidence`` reason. Nothing is guessed (plan rule 3.7).
* No username, hostname, home path, raw environment variable, or secret is
  written.

Usage::

    python -m validation.scripts.generate_source_manifest
    python -m validation.scripts.generate_source_manifest --verify
    python -m validation.scripts.generate_source_manifest --out <directory>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_FORMAT_VERSION = "1.0.0"
BASELINE_LABEL = "archlens-3.4.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "validation" / "baselines"

# Directory names excluded anywhere in the tree.
EXCLUDED_DIRECTORY_NAMES = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    "wheelhouse",
    "htmlcov",
    ".archlens",
    "archlens-output",
)

# Project-relative roots excluded wholesale: run outputs, large preserved run
# corpora, and scratch input. These are evidence, not program source.
EXCLUDED_RELATIVE_ROOTS = (
    "output",
    "results",
    "data/raw",
    "data/processed",
    "input/temp",
    "validation/audit_20260802",
    "validation/archlens_hardening_20260803",
    "validation/archlens_hardening_20260804",
    "validation/final_audit_20260803",
    "validation/final_stabilization_20260804",
    "validation/javascript_compatibility_20260803",
    "validation/performance_v22",
    "validation/post_cohort_stabilization_20260805/targeted-frozen-output",
    "validation/post_cohort_stabilization_20260805/targeted-frozen-acceptance-output",
    "validation/post_cohort_stabilization_20260805/targeted-offline-acceptance-output",
    "validation/baselines",
)

EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".dll", ".tmp", ".swp")
EXCLUDED_FILE_NAMES = (".DS_Store", "Thumbs.db", "Desktop.ini")

# Suffix -> recorded file kind. Anything unlisted is recorded as "other".
FILE_KINDS = {
    ".py": "python_source",
    ".toml": "packaging_configuration",
    ".cfg": "packaging_configuration",
    ".in": "packaging_configuration",
    ".json": "data_or_configuration",
    ".csv": "tabular_data",
    ".tsv": "tabular_data",
    ".md": "documentation",
    ".txt": "text",
    ".yml": "workflow_or_configuration",
    ".yaml": "workflow_or_configuration",
    ".java": "test_fixture_source",
    ".js": "test_fixture_source",
    ".jsx": "test_fixture_source",
    ".mjs": "test_fixture_source",
    ".cjs": "test_fixture_source",
    ".ts": "test_fixture_source",
    ".tsx": "test_fixture_source",
    ".mts": "test_fixture_source",
    ".cts": "test_fixture_source",
    ".go": "test_fixture_source",
}

PARSER_DISTRIBUTIONS = (
    "tree-sitter",
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in parts):
        return True
    posix = relative.as_posix()
    for root in EXCLUDED_RELATIVE_ROOTS:
        if posix == root or posix.startswith(root + "/"):
            return True
    if relative.name in EXCLUDED_FILE_NAMES:
        return True
    if relative.suffix in EXCLUDED_SUFFIXES:
        return True
    if relative.name.endswith(".egg-info"):
        return True
    return False


def collect_entries(root: Path) -> list[dict[str, Any]]:
    """Return the sorted, deterministic file-entry table.

    Excluded directories are pruned during the walk rather than filtered after
    it, so the generator never descends into ``.git`` or preserved run corpora.
    """
    import os

    entries: list[dict[str, Any]] = []
    for directory, subdirectories, file_names in os.walk(root):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in EXCLUDED_DIRECTORY_NAMES
            and not name.endswith(".egg-info")
            and not _is_excluded(relative_dir / name)
            and not (current / name).is_symlink()
        )
        for name in sorted(file_names):
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = relative_dir / name if relative_dir != Path(".") else Path(name)
            if _is_excluded(relative):
                continue
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "kind": FILE_KINDS.get(relative.suffix, "other"),
                }
            )
    entries.sort(key=lambda entry: entry["path"])
    return entries


def _git_state(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    inside = run("rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return {
            "state": "not_a_git_work_tree",
            "head_commit": None,
            "head_commit_evidence": "no Git work tree detected",
            "commit_count": None,
            "branch": None,
            "is_dirty": None,
            "tracked_file_count": None,
        }
    head = run("rev-parse", "HEAD")
    count_text = run("rev-list", "--count", "--all")
    commit_count = int(count_text) if count_text and count_text.isdigit() else None
    status = run("status", "--porcelain")
    tracked = run("ls-files")
    tracked_count = len([line for line in tracked.splitlines() if line]) if tracked is not None else None
    if head is None:
        return {
            "state": "git_work_tree_without_commits",
            "head_commit": None,
            "head_commit_evidence": (
                "repository has no commits; HEAD is unborn. A real profiler commit SHA "
                "does not exist, so exact-revision reproduction claims are unavailable."
            ),
            "commit_count": commit_count if commit_count is not None else 0,
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "is_dirty": None,
            "tracked_file_count": tracked_count,
        }
    return {
        "state": "git_work_tree_with_commits",
        "head_commit": head,
        "head_commit_evidence": "observed",
        "commit_count": commit_count,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "is_dirty": bool(status),
        "tracked_file_count": tracked_count,
    }


def _git_version() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return {"version": None, "evidence": "git executable not available"}
    if completed.returncode != 0:
        return {"version": None, "evidence": "git --version returned a nonzero exit code"}
    return {"version": completed.stdout.strip(), "evidence": "observed"}


def _parser_versions() -> dict[str, Any]:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.8+
        return {name: {"version": None, "evidence": "importlib.metadata unavailable"} for name in PARSER_DISTRIBUTIONS}
    versions: dict[str, Any] = {}
    for name in PARSER_DISTRIBUTIONS:
        try:
            versions[name] = {"version": metadata.version(name), "evidence": "observed"}
        except metadata.PackageNotFoundError:
            versions[name] = {
                "version": None,
                "evidence": "distribution not installed in the generating environment",
            }
    return versions


def _program_versions(root: Path) -> dict[str, Any]:
    """Read declared versions textually so the generator never imports the program."""
    config_path = root / "modules" / "config.py"
    wanted = (
        "METRIC_CONTRACT_VERSION",
        "INVENTORY_SCHEMA_VERSION",
        "ARTIFACT_SCHEMA_VERSION",
        "PROGRAM_VERSION",
    )
    found: dict[str, Any] = {key.lower(): None for key in wanted}
    if config_path.is_file():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            for key in wanted:
                prefix = f"{key} = "
                if stripped.startswith(prefix):
                    found[key.lower()] = stripped[len(prefix):].strip().strip('"').strip("'")
    pyproject = root / "pyproject.toml"
    declared_project_version = None
    if pyproject.is_file():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                declared_project_version = stripped[len("version = "):].strip().strip('"').strip("'")
                break
    found["pyproject_version"] = declared_project_version
    return found


def _policy_state(root: Path) -> dict[str, Any]:
    policy_path = root / "config" / "exclusions.v1.json"
    if not policy_path.is_file():
        return {"path": "config/exclusions.v1.json", "sha256": None, "version": None,
                "evidence": "policy file not found"}
    try:
        version = json.loads(policy_path.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        version = None
    return {
        "path": "config/exclusions.v1.json",
        "sha256": _sha256_file(policy_path),
        "version": version,
        "evidence": "observed",
    }


def _environment_fingerprint() -> dict[str, Any]:
    """Normalized, non-PII environment facts. No user, host, or home path."""
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_version_supported_by_archlens_3_4_0": sys.version_info[:2] == (3, 13),
        "operating_system": platform.system(),
        "machine": platform.machine(),
        "pointer_width_bits": 64 if sys.maxsize > 2**32 else 32,
        "filesystem_encoding": sys.getfilesystemencoding(),
        "default_encoding": sys.getdefaultencoding(),
    }


def build_manifest(root: Path) -> dict[str, Any]:
    entries = collect_entries(root)
    entries_payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    total_bytes = sum(entry["size_bytes"] for entry in entries)
    return {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "baseline_label": BASELINE_LABEL,
        "review_state": "generated_unreviewed",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries_digest": _sha256_text(entries_payload),
        "file_count": len(entries),
        "total_size_bytes": total_bytes,
        "excluded_directory_names": list(EXCLUDED_DIRECTORY_NAMES),
        "excluded_relative_roots": list(EXCLUDED_RELATIVE_ROOTS),
        "excluded_suffixes": list(EXCLUDED_SUFFIXES),
        "declared_versions": _program_versions(root),
        "exclusion_policy": _policy_state(root),
        "git_state": _git_state(root),
        "git_version": _git_version(),
        "parser_distributions": _parser_versions(),
        "environment_fingerprint": _environment_fingerprint(),
        "files": entries,
    }


def serialize(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_manifest(manifest: dict[str, Any], directory: Path) -> tuple[Path, Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / f"{BASELINE_LABEL}-source-manifest.json"
    digest_path = directory / f"{BASELINE_LABEL}-source-manifest.sha256"
    payload = serialize(manifest)
    manifest_path.write_text(payload, encoding="utf-8", newline="\n")
    self_hash = _sha256_text(payload)
    digest_path.write_text(f"{self_hash}  {manifest_path.name}\n", encoding="utf-8", newline="\n")
    return manifest_path, digest_path, self_hash


def verify(directory: Path, root: Path) -> int:
    manifest_path = directory / f"{BASELINE_LABEL}-source-manifest.json"
    digest_path = directory / f"{BASELINE_LABEL}-source-manifest.sha256"
    if not manifest_path.is_file() or not digest_path.is_file():
        print("VERIFY FAIL: manifest or digest file is missing", file=sys.stderr)
        return 1
    payload = manifest_path.read_text(encoding="utf-8")
    recorded = digest_path.read_text(encoding="utf-8").split()[0]
    actual_self_hash = _sha256_text(payload)
    if recorded != actual_self_hash:
        print(f"VERIFY FAIL: self-hash mismatch\n  recorded {recorded}\n  actual   {actual_self_hash}", file=sys.stderr)
        return 1
    manifest = json.loads(payload)
    stored = {entry["path"]: entry for entry in manifest["files"]}
    current = {entry["path"]: entry for entry in collect_entries(root)}
    added = sorted(set(current) - set(stored))
    removed = sorted(set(stored) - set(current))
    changed = sorted(
        path
        for path in set(stored) & set(current)
        if stored[path]["sha256"] != current[path]["sha256"]
        or stored[path]["size_bytes"] != current[path]["size_bytes"]
    )
    entries_payload = json.dumps(
        sorted(current.values(), key=lambda entry: entry["path"]),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    digest_matches = _sha256_text(entries_payload) == manifest["entries_digest"]
    if not (added or removed or changed) and digest_matches:
        print(f"VERIFY OK: {manifest['file_count']} files, entries_digest {manifest['entries_digest']}")
        return 0
    print("VERIFY DRIFT DETECTED", file=sys.stderr)
    for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
        for path in paths:
            print(f"  {label}: {path}", file=sys.stderr)
    if not digest_matches:
        print("  entries_digest mismatch", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the ArchLens source content manifest.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIRECTORY,
                        help="Directory for the manifest and its self-hash file.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Project root to hash.")
    parser.add_argument("--verify", action="store_true", help="Verify an existing manifest instead of writing one.")
    parser.add_argument("--print-digest-only", action="store_true",
                        help="Print the deterministic entries digest and exit without writing.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.verify:
        return verify(args.out.resolve(), root)
    manifest = build_manifest(root)
    if args.print_digest_only:
        print(manifest["entries_digest"])
        return 0
    manifest_path, digest_path, self_hash = write_manifest(manifest, args.out.resolve())
    print(f"files            : {manifest['file_count']}")
    print(f"total bytes      : {manifest['total_size_bytes']}")
    print(f"entries_digest   : {manifest['entries_digest']}")
    print(f"manifest sha256  : {self_hash}")
    print(f"manifest         : {manifest_path.relative_to(root).as_posix()}")
    print(f"self-hash file   : {digest_path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
