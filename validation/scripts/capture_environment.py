#!/usr/bin/env python3
"""Capture a clean, machine-readable ArchLens audit environment record."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

from modules.benchmark_runner import _profiler_git_sha
from modules.config import AnalysisConfig
from modules.core_metrics import validate_parser_initialization


def command(*args: str) -> dict[str, object]:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "arguments": list(args),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    distributions = (
        "archlens", "tree-sitter", "tree-sitter-java",
        "tree-sitter-javascript", "tree-sitter-typescript", "tree-sitter-go",
    )
    report = {
        "working_directory": str(Path.cwd().resolve()),
        "operating_system": platform.platform(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "git_version": command("git", "--version"),
        "package_consistency": command(sys.executable, "-m", "pip", "check"),
        "distributions": {name: importlib.metadata.version(name) for name in distributions},
        "profiler_is_git_repository": command("git", "-C", str(Path.cwd()), "rev-parse", "--is-inside-work-tree"),
        "profiler_git_commit_sha": _profiler_git_sha(),
        "parser_initialization": validate_parser_initialization(),
        "effective_configuration": AnalysisConfig.from_env().snapshot(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
