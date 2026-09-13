"""Backward-compatible views over the canonical repository inventory."""

from __future__ import annotations

from pathlib import Path

from modules.config import SUPPORTED_EXTENSIONS, load_exclusion_policy
from modules.inventory import RepositoryInventory


SOURCE_EXTENSIONS = set(SUPPORTED_EXTENSIONS)
_POLICY = load_exclusion_policy()
EXCLUDED_DIRS = set(_POLICY["excluded_directories"])
EXCLUDED_FILE_SUFFIXES = set(_POLICY["generated_suffixes"]) | set(
    _POLICY["minified_or_bundle_suffixes"]
)
EXCLUDED_FILE_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
STATIC_ASSET_ROOTS = {"assets", "public", "static", "wwwroot"}
STATIC_DEPENDENCY_DIRS = {
    "bower_components", "lib", "libs", "plugins", "third-party",
    "third_party", "vendor", "vendors",
}
TEST_DIR_NAMES = set(_POLICY["test_directories"])


def is_test_file(path: str | Path) -> bool:
    """Return whether a path follows the centralized common test conventions."""
    path = Path(path)
    lowered_parts = {part.lower() for part in path.parent.parts}
    test_dirs = set(load_exclusion_policy()["test_directories"])
    name = path.name.lower()
    stem = path.stem.lower()
    return bool(
        lowered_parts & test_dirs
        or name == "conftest.py"
        or name.endswith("_test.go")
        or name.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
        or name.endswith(("test.java", "tests.java"))
    )


def iter_repository_files(
    repo_path: str | Path,
    extensions=None,
    include_tests: bool = True,
    inventory: RepositoryInventory | None = None,
):
    inventory = inventory or RepositoryInventory(repo_path)
    yield from inventory.paths(
        extensions=set(extensions) if extensions is not None else None,
        include_tests=include_tests,
    )


def iter_source_files(
    repo_path: str | Path,
    include_tests: bool = False,
    inventory: RepositoryInventory | None = None,
):
    inventory = inventory or RepositoryInventory(repo_path)
    for record in inventory:
        if not record.is_source:
            continue
        if not include_tests and record.is_test:
            continue
        if include_tests:
            if record.exclusion_reason not in {None, "test"}:
                continue
        elif not record.included_in_metrics:
            continue
        yield record._absolute_path


def read_text(path: str | Path, inventory: RepositoryInventory | None = None):
    if inventory is not None:
        return inventory.read_text(path)
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None
