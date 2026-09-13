#!/usr/bin/env python3
"""Locate per-file ArchLens versus scc physical-LOC differences."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from modules.core_metrics import ParserRegistry, _analyze_file


def normalize_location(location: str, scope: Path) -> str:
    candidate = Path(location)
    try:
        return candidate.resolve().relative_to(scope.resolve()).as_posix()
    except ValueError:
        normalized = location.replace("\\", "/")
        marker = scope.name + "/"
        return normalized.split(marker, 1)[-1] if marker in normalized else normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("loc_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    loc_dir = args.loc_dir.resolve()
    analysis = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    differences = []
    for result in analysis:
        slug = f"{result['repository_owner']}__{result['repository_name']}"
        inventory = json.loads((run_dir / "file_inventory" / f"{slug}.json").read_text(encoding="utf-8"))
        scope = loc_dir / "exact_included_scopes" / slug
        raw = json.loads((loc_dir / "raw_scc_json" / f"{slug}.json").read_text(encoding="utf-8"))
        scc_files = {
            normalize_location(item["Location"], scope): item
            for language in raw for item in language.get("Files", [])
        }
        registry = ParserRegistry()
        for record in inventory["files"]:
            if not record["included_in_metrics"]:
                continue
            path = record["relative_path"]
            source = (scope / Path(path)).read_bytes()
            observed = _analyze_file(
                SimpleNamespace(detected_language=record["detected_language"], extension=record["extension"]),
                source, registry,
            )
            arch_code = observed.loc["code_lines"] if observed.loc is not None else None
            scc_item = scc_files.get(path)
            scc_code = scc_item.get("Code") if scc_item else None
            if arch_code != scc_code:
                differences.append({
                    "repository_url": result["repository_url"],
                    "commit_sha": result["acquisition"]["analyzed_commit_sha"],
                    "path": path,
                    "language": record["detected_language"],
                    "archlens_code_lines": arch_code,
                    "scc_code_lines": scc_code,
                    "signed_difference_scc_minus_arch": None if arch_code is None or scc_code is None else scc_code - arch_code,
                    "absolute_difference": None if arch_code is None or scc_code is None else abs(scc_code - arch_code),
                    "arch_parser_status": observed.status,
                    "arch_parser_error": observed.error,
                })
    differences.sort(key=lambda item: (-(item["absolute_difference"] or -1), item["repository_url"], item["path"]))
    payload = {"difference_file_count": len(differences), "differences": differences}
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
