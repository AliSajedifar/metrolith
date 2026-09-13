#!/usr/bin/env python3
"""Run scc on the exact ArchLens included-file scope at recorded Git SHAs."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

if __package__:
    from .independent_inventory_check import BatchReader, cache_for, tree_entries
else:
    from independent_inventory_check import BatchReader, cache_for, tree_entries


LANGUAGE_NAMES = {
    "Java": "java", "JavaScript": "javascript", "TypeScript": "typescript",
    "Python": "python", "Go": "go",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("scc", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    cache_root = args.cache_root.resolve()
    scc = args.scc.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scope_root = output_dir / "exact_included_scopes"
    raw_root = output_dir / "raw_scc_json"
    if scope_root.exists() or raw_root.exists():
        raise SystemExit("Refusing to overwrite an existing independent LOC evidence directory")
    scope_root.mkdir()
    raw_root.mkdir()
    analysis = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    comparisons = []
    commands = []
    for result in analysis:
        slug = f"{result['repository_owner']}__{result['repository_name']}"
        url = result["repository_url"]
        sha = result["acquisition"]["analyzed_commit_sha"]
        cache = cache_for(cache_root, url)
        inventory = json.loads((run_dir / "file_inventory" / f"{slug}.json").read_text(encoding="utf-8"))
        included = [item for item in inventory["files"] if item["included_in_metrics"]]
        objects = {entry["path"]: entry for entry in tree_entries(cache, sha)}
        repo_scope = scope_root / slug
        repo_scope.mkdir()
        batch = BatchReader(cache)
        try:
            for record in included:
                relative = record["relative_path"]
                entry = objects.get(relative)
                if entry is None or entry["kind"] != "blob":
                    raise RuntimeError(f"{url}: included path is not a Git blob: {relative}")
                destination = repo_scope / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(batch.read(entry["object_id"]))
        finally:
            batch.close()

        raw_path = raw_root / f"{slug}.json"
        command = [
            str(scc), "--format", "json", "--by-file", "--no-cocomo",
            "--no-complexity", "--no-gitignore", "--no-ignore", "--no-scc-ignore",
            str(repo_scope),
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        raw_path.write_text(completed.stdout, encoding="utf-8")
        commands.append({
            "repository_url": url,
            "commit_sha": sha,
            "command": command,
            "scope_directory": str(repo_scope),
            "raw_output": str(raw_path),
            "included_file_count": len(included),
        })
        raw = json.loads(completed.stdout)
        scc_by_language = {
            LANGUAGE_NAMES[item["Name"]]: item
            for item in raw if item.get("Name") in LANGUAGE_NAMES
        }
        for language in ("java", "javascript", "typescript", "python", "go"):
            arch = result["metrics"]["by_language"][language]
            scc_metric = scc_by_language.get(language, {})
            scc_code = scc_metric.get("Code", 0)
            scc_files = scc_metric.get("Count", 0)
            arch_code = arch["lines_of_code"]
            difference = None if arch_code is None else scc_code - arch_code
            absolute = None if difference is None else abs(difference)
            percentage = None if arch_code in {None, 0} else round(absolute * 100.0 / arch_code, 4)
            comparisons.append({
                "repository_url": url,
                "commit_sha": sha,
                "language": language,
                "archlens_code_lines": arch_code,
                "scc_code_lines": scc_code,
                "absolute_difference": absolute,
                "signed_difference_scc_minus_arch": difference,
                "percentage_difference_of_arch": percentage,
                "archlens_source_files": arch["source_files"],
                "scc_files": scc_files,
                "scope_file_count_agrees": arch["source_files"] == scc_files,
            })
    version = subprocess.run([str(scc), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    payload = {
        "tool": "scc",
        "version": version,
        "method": "scc run on materialized copies of the exact ArchLens included Git blobs",
        "language_mapping": LANGUAGE_NAMES,
        "commands": commands,
        "comparisons": comparisons,
    }
    (output_dir / "scc_commands.json").write_text(json.dumps(commands, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "loc_comparison.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
