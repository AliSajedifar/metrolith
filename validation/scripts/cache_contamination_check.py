#!/usr/bin/env python3
"""Controlled bare-cache/worktree contamination and concurrency validation."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from modules.acquisition import acquire_repository, cache_path_for_url
from modules.config import AnalysisConfig
from modules.repository_input import RepositorySpec


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=ArchLens Test", "-c", "user.email=archlens@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="archlens_contamination_") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        git("init", "-b", "audit-default", cwd=source)
        (source / "tracked.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
        git("add", "tracked.py", cwd=source)
        git("commit", "-m", "baseline", cwd=source)
        sha = git("rev-parse", "HEAD", cwd=source)

        cache_root = root / "cache"
        temp_root = root / "worktrees"
        fake_url = "https://github.com/archlens-validation/cache-safety"
        cache = cache_path_for_url(cache_root, fake_url)
        cache.parent.mkdir(parents=True)
        git("clone", "--mirror", str(source), str(cache), cwd=root)
        git("remote", "set-url", "origin", fake_url, cwd=cache)

        # Contamination placed in both the mutable source and next to bare Git data.
        (source / "tracked.py").write_text("VALUE = 'modified'\n", encoding="utf-8")
        (source / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")
        (cache / "foreign.py").write_text("FOREIGN = True\n", encoding="utf-8")

        config = AnalysisConfig.from_env(cache_root=cache_root, temporary_directory=temp_root)
        spec = RepositorySpec(fake_url, "unknown", "Python", sha)
        with acquire_repository(spec, config, mode="offline") as acquired:
            checkout = acquired.path
            head = git("rev-parse", "HEAD", cwd=checkout)
            clean = git("status", "--porcelain=v1", "--untracked-files=all", cwd=checkout)
            checks.extend([
                {"check": "detached_head_equals_requested_sha", "passed": head == sha, "observed": head},
                {"check": "tracked_source_modification_excluded", "passed": (checkout / "tracked.py").read_text(encoding="utf-8") == "VALUE = 'committed'\n"},
                {"check": "untracked_source_file_excluded", "passed": not (checkout / "untracked.py").exists()},
                {"check": "foreign_cache_file_excluded", "passed": not (checkout / "foreign.py").exists()},
                {"check": "checkout_clean", "passed": clean == "", "observed": clean},
                {"check": "analysis_path_is_not_bare_cache", "passed": checkout.resolve() != cache.resolve()},
            ])
            success_checkout_root = checkout.parent
        checks.append({"check": "success_worktree_removed", "passed": not success_checkout_root.exists()})

        failure_checkout_root = None
        try:
            with acquire_repository(spec, config, mode="offline") as acquired:
                failure_checkout_root = acquired.path.parent
                raise RuntimeError("controlled analysis failure")
        except RuntimeError as exc:
            checks.append({"check": "controlled_failure_propagated", "passed": str(exc) == "controlled analysis failure"})
        checks.append({"check": "failure_worktree_removed", "passed": failure_checkout_root is not None and not failure_checkout_root.exists()})

        def concurrent_checkout(index: int):
            with acquire_repository(spec, config, mode="offline") as acquired:
                return {
                    "index": index,
                    "path": str(acquired.path),
                    "head": git("rev-parse", "HEAD", cwd=acquired.path),
                    "content": (acquired.path / "tracked.py").read_text(encoding="utf-8"),
                    "has_foreign": (acquired.path / "foreign.py").exists(),
                }

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent = list(executor.map(concurrent_checkout, (1, 2)))
        checks.extend([
            {"check": "concurrent_worktrees_are_distinct", "passed": concurrent[0]["path"] != concurrent[1]["path"], "observed": [item["path"] for item in concurrent]},
            {"check": "concurrent_heads_match", "passed": all(item["head"] == sha for item in concurrent)},
            {"check": "concurrent_contents_not_mixed", "passed": all(item["content"] == "VALUE = 'committed'\n" and not item["has_foreign"] for item in concurrent)},
            {"check": "concurrent_worktrees_removed", "passed": all(not Path(item["path"]).parent.exists() for item in concurrent)},
        ])
        listed = git("--git-dir", str(cache), "worktree", "list", "--porcelain", cwd=root)
        checks.append({"check": "worktree_metadata_pruned", "passed": "archlens_checkout_" not in listed, "observed": listed})

        payload = {
            "method": "temporary local Git repository mirrored into a bare cache with a canonical fake GitHub identity",
            "requested_sha": sha,
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
        }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
