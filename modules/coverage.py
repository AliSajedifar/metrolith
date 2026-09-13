# -*- coding: utf-8 -*-
"""Estimate test presence without executing untrusted repository code."""

import re
from pathlib import Path

from modules.repository_files import (
    is_test_file,
    iter_source_files,
    read_text,
)


TEST_PATTERNS = {
    ".py": re.compile(r"(?m)^[ \t]*(?:async\s+)?def\s+test_"),
    ".java": re.compile(r"@\s*(?:Test|ParameterizedTest|RepeatedTest)\b"),
    ".kt": re.compile(r"@\s*Test\b"),
    ".kts": re.compile(r"@\s*Test\b"),
    ".js": re.compile(r"\b(?:it|test)\s*\("),
    ".jsx": re.compile(r"\b(?:it|test)\s*\("),
    ".ts": re.compile(r"\b(?:it|test)\s*\("),
    ".tsx": re.compile(r"\b(?:it|test)\s*\("),
    ".go": re.compile(r"(?m)^[ \t]*func\s+(?:Test|Benchmark|Example)\w*\s*\("),
    ".rb": re.compile(r"\b(?:it|test)\s+[\"']"),
    ".php": re.compile(r"\bfunction\s+test\w*\s*\(", re.I),
    ".cs": re.compile(r"\[(?:Test|TestCase|Fact|Theory)\b"),
}


def find_test_files(repo_path, inventory=None):
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")
    return [
        path
        for path in iter_source_files(repo_path, include_tests=True, inventory=inventory)
        if is_test_file(path.relative_to(repo_path))
    ]


def count_test_cases(file_path, inventory=None):
    content = read_text(file_path, inventory=inventory)
    if content is None:
        return 0
    pattern = TEST_PATTERNS.get(Path(file_path).suffix.lower())
    return len(pattern.findall(content)) if pattern else 0


def compute_coverage(repo_path, language_hint=None, inventory=None):
    """
    Return a bounded structural coverage estimate.

    This is not measured runtime coverage. ``test_case_density`` preserves the
    raw test-cases/source-files signal, while ``estimated_coverage_ratio`` maps
    that signal to 0..1 for backward-compatible classification/reporting.
    """
    del language_hint
    repo_path = Path(repo_path)
    all_sources = list(iter_source_files(repo_path, include_tests=True, inventory=inventory))
    test_files = [
        path for path in all_sources if is_test_file(path.relative_to(repo_path))
    ]
    production_files = [
        path for path in all_sources if not is_test_file(path.relative_to(repo_path))
    ]
    total_test_cases = sum(count_test_cases(path, inventory) for path in test_files)
    source_count = len(production_files)
    density = total_test_cases / source_count if source_count else 0.0

    return {
        "test_files": [
            path.relative_to(repo_path).as_posix() for path in sorted(test_files)
        ],
        "test_file_count": len(test_files),
        "test_cases": total_test_cases,
        "source_files": source_count,
        "test_case_density": round(density, 3),
        "estimated_coverage_ratio": round(min(density, 1.0), 3),
        "coverage_kind": "structural estimate; tests were not executed",
    }
