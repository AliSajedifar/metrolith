"""Conformance case loading and execution.

The runner is deliberately thin. It loads a reviewed oracle, feeds the case
input through the canonical measurement path, and reports an exact diff. It
computes nothing itself: plan section 3.1 forbids any second implementation of
inclusion, language detection, parser outcome, metric values, or statuses.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Iterator

DATA_PACKAGE = "validation.conformance"
DATA_DIRECTORY = "data"
MANIFEST_NAME = "manifest.json"

# A case whose review status is not one of these is refused rather than run: an
# unreviewed oracle is not evidence (plan section 9.3).
ACCEPTED_REVIEW_STATUSES = frozenset({"reviewed", "reviewed_with_notes"})


class CaseOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNREVIEWED = "unreviewed"
    SKIPPED_PLATFORM = "skipped_platform"
    # A capability the case genuinely requires is absent on this host. This is
    # deliberately distinct from PASSED: a native-symlink case that silently
    # degraded to the materialized path would report success for behaviour it
    # never exercised.
    SKIPPED_CAPABILITY = "skipped_capability"
    ERROR = "error"


def native_symlinks_available() -> bool:
    """Whether this host can create a real filesystem symlink.

    Windows needs Developer Mode or elevation for ``os.symlink``. Detection is
    by attempt, because the privilege cannot be inferred from the platform name.
    """
    import os
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.py").write_text("x = 1\n", encoding="utf-8")
            os.symlink(root / "target.py", root / "link.py")
            return (root / "link.py").is_symlink()
    except (OSError, NotImplementedError, AttributeError):
        return False


CAPABILITIES = {"native_symlink": native_symlinks_available}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    outcome: CaseOutcome
    message: str = ""
    differences: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"case_id": self.case_id, "outcome": self.outcome.value}
        if self.message:
            payload["message"] = self.message
        if self.differences:
            payload["differences"] = list(self.differences)
        return payload


@dataclass(frozen=True)
class Manifest:
    corpus_version: str
    metric_contract_version: str
    exclusion_policy_version: str
    inventory_schema_version: str
    cases: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def case(self, case_id: str) -> dict[str, Any] | None:
        return next((item for item in self.cases if item.get("id") == case_id), None)


def _data_root():
    return resources.files(DATA_PACKAGE).joinpath(DATA_DIRECTORY)


def load_manifest() -> Manifest:
    """Load the corpus manifest from package resources."""
    payload = json.loads(_data_root().joinpath(MANIFEST_NAME).read_text(encoding="utf-8"))
    return Manifest(
        corpus_version=payload["corpus_version"],
        metric_contract_version=payload["metric_contract_version"],
        exclusion_policy_version=payload["exclusion_policy_version"],
        inventory_schema_version=payload["inventory_schema_version"],
        cases=tuple(payload.get("cases", ())),
    )


def iter_case_ids() -> Iterator[str]:
    for case in load_manifest().cases:
        yield case["id"]


def _load_expected(case_id: str) -> dict[str, Any]:
    resource = _data_root().joinpath("expected", f"{case_id}.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _case_bytes(case: dict[str, Any]) -> bytes:
    resource = _data_root().joinpath("cases", case["input_path"])
    return resource.read_bytes()


def _differences(expected: Any, observed: Any, pointer: str = "") -> list[str]:
    """Exact structural diff, deterministically ordered (plan section 9.4)."""
    if isinstance(expected, dict) and isinstance(observed, dict):
        found: list[str] = []
        for key in sorted(set(expected) | set(observed)):
            child = f"{pointer}/{key}"
            if key not in expected:
                found.append(f"{child}: unexpected value {observed[key]!r}")
            elif key not in observed:
                found.append(f"{child}: expected {expected[key]!r}, absent")
            else:
                found.extend(_differences(expected[key], observed[key], child))
        return found
    if isinstance(expected, list) and isinstance(observed, list):
        found = []
        if len(expected) != len(observed):
            found.append(f"{pointer}: expected {len(expected)} items, observed {len(observed)}")
        for index, (left, right) in enumerate(zip(expected, observed)):
            found.extend(_differences(left, right, f"{pointer}/{index}"))
        return found
    if expected != observed:
        return [f"{pointer or '/'}: expected {expected!r}, observed {observed!r}"]
    return []


def run_case(case_id: str) -> CaseResult:
    """Run one reviewed case against the canonical measurement path."""
    manifest = load_manifest()
    case = manifest.case(case_id)
    if case is None:
        return CaseResult(case_id, CaseOutcome.ERROR, f"unknown case {case_id!r}")

    if case.get("review_status") not in ACCEPTED_REVIEW_STATUSES:
        return CaseResult(
            case_id, CaseOutcome.UNREVIEWED,
            f"review_status is {case.get('review_status')!r}; an unreviewed oracle "
            "is not evidence and is refused rather than run",
        )

    platforms = case.get("platforms")
    if platforms:
        import sys

        if sys.platform not in platforms:
            return CaseResult(
                case_id, CaseOutcome.SKIPPED_PLATFORM,
                f"case declares platforms {sorted(platforms)}; running on {sys.platform}",
            )

    for capability in case.get("requires_capabilities", ()):
        probe = CAPABILITIES.get(capability)
        if probe is None:
            return CaseResult(
                case_id, CaseOutcome.ERROR, f"unknown capability {capability!r}"
            )
        if not probe():
            return CaseResult(
                case_id, CaseOutcome.SKIPPED_CAPABILITY,
                f"host cannot provide {capability!r}; this case is reported as "
                f"skipped rather than passed, because the behaviour it pins was "
                f"never exercised",
            )

    try:
        observed = _observe(case)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return CaseResult(case_id, CaseOutcome.ERROR, f"{type(exc).__name__}: {exc}")

    expected = _load_expected(case_id)
    found = _differences(expected["observations"], observed)
    if found:
        return CaseResult(case_id, CaseOutcome.FAILED, "oracle mismatch", tuple(found))
    return CaseResult(case_id, CaseOutcome.PASSED)


def _observe(case: dict[str, Any]) -> dict[str, Any]:
    """Feed one case through the canonical measurement path.

    Imports are deferred so that loading the corpus manifest, listing cases, and
    reporting review state never require the parser stack.
    """
    import tempfile
    from pathlib import Path

    from modules.core_metrics import compute_repository_metrics
    from modules.inventory import RepositoryInventory

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        target = root / case["file_name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_case_bytes(case))

        # Additional files let a case exercise inclusion, exclusion, and
        # language-selection decisions that need more than one file present.
        for relative in case.get("extra_files", ()):
            extra = root / relative
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_bytes(
                _data_root().joinpath("cases", case["id"], relative).read_bytes()
            )

        # A `git` case needs a real repository, because Git file modes such as
        # 120000 (symlink) and 160000 (gitlink) exist only in the index. They
        # cannot be simulated from the working tree alone.
        if case.get("kind") == "git":
            _materialize_git_repository(root, case)

        # A case may pin a configuration bound, such as the source-file size
        # limit, so size-boundary behaviour can be exercised without writing a
        # multi-megabyte fixture into the corpus.
        config = None
        overrides = case.get("config_overrides")
        if overrides:
            from modules.config import AnalysisConfig

            config = AnalysisConfig.from_env(**overrides)

        # Native symlinks are created here rather than staged, so a case can
        # pin working-tree symlink behaviour distinctly from the Git 120000
        # index-mode behaviour.
        for link, target in (case.get("native_symlinks") or {}).items():
            import os

            os.symlink(root / target, root / link)

        with _git_fault(case.get("git_fault")):
            inventory = RepositoryInventory(root, config)
        metrics = compute_repository_metrics(
            inventory, expected_language=case.get("expected_language")
        )
        return _project(metrics, inventory, case)


@contextmanager
def _git_fault(fault: str | None):
    """Inject a deterministic Git mode-map failure.

    The alternative — arranging a real timeout or a real Git error — depends on
    machine speed and Git version, which is exactly the flakiness a conformance
    corpus must not have. Raising the exception the production code already
    handles exercises the same branch with no timing dependency at all.
    """
    if fault is None:
        yield
        return

    import subprocess
    from unittest.mock import patch

    real_run = subprocess.run

    def failing(command, *args, **kwargs):
        rendered = command if isinstance(command, (list, tuple)) else [command]
        if any("ls-files" == str(part) for part in rendered):
            if fault == "timed_out":
                raise subprocess.TimeoutExpired(
                    cmd=list(rendered), timeout=kwargs.get("timeout") or 1,
                    stderr=b"injected conformance timeout",
                )
            if fault == "failed":
                # stderr deliberately avoids "not a git repository", which the
                # production code maps to the distinct unavailable state.
                raise subprocess.CalledProcessError(
                    returncode=128, cmd=list(rendered),
                    stderr=b"fatal: injected conformance failure reading the index",
                )
            raise ValueError(f"unknown git fault {fault!r}")
        return real_run(command, *args, **kwargs)

    with patch("modules.inventory.subprocess.run", failing):
        yield


def _git(root, *arguments, check: bool = True):
    import subprocess

    return subprocess.run(
        ["git", *arguments], cwd=str(root), capture_output=True, text=True,
        check=check, timeout=60,
    )


def _materialize_git_repository(root, case: dict[str, Any]) -> None:
    """Initialise a Git repository and stage the modes the case declares.

    Modes are written into the index with ``update-index --cacheinfo`` rather
    than by creating real symlinks, because symlink creation on Windows needs
    privileges the test environment may not have. The index is what the inventory
    reads, so this exercises the same code path on every platform.
    """
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "conformance@archlens.invalid")
    _git(root, "config", "user.name", "ArchLens Conformance")

    # A submodule case must not stage the paths under its gitlink: Git refuses
    # an index that holds both a 160000 entry at `lib` and a blob at `lib/x.py`.
    # Such a case declares exactly what to stage instead.
    for path in case.get("git_add_paths", ("-A",)):
        _git(root, "add", path)

    import subprocess

    for entry in case.get("git_index_entries", ()):
        mode = entry["mode"]
        if mode == "160000":
            # A gitlink references a commit that need not exist locally.
            blob = entry.get("object") or ("0" * 39 + "1")
        else:
            written = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=str(root), input=entry.get("content", ""),
                capture_output=True, text=True, check=True, timeout=60,
            )
            blob = written.stdout.strip()
        _git(
            root, "update-index", "--add", "--cacheinfo",
            f"{mode},{blob},{entry['path']}",
        )

    if case.get("dirty_after_commit"):
        _git(root, "commit", "-q", "-m", "conformance fixture", check=False)
        for relative, content in case["dirty_after_commit"].items():
            (root / relative).write_text(content, encoding="utf-8")


def _project(
    metrics: dict[str, Any], inventory: Any, case: dict[str, Any]
) -> dict[str, Any]:
    """Copy the recorded observations a case declares interest in.

    Nothing is derived here. Every value is read straight off the canonical
    result, following the JSON-pointer-style paths the case declares. A path
    that does not resolve records ``None`` rather than a guess (plan section
    3.7).
    """
    observed: dict[str, Any] = {}

    for pointer in case.get("observed_metric_paths", ()):
        observed[pointer] = _resolve(metrics, pointer)

    inventory_fields = case.get("observed_inventory_fields", ())
    if inventory_fields:
        record = inventory.get(case.get("observed_inventory_path") or case["file_name"])
        observed["inventory"] = {
            name: (getattr(record, name, None) if record is not None else None)
            for name in inventory_fields
        }

    # Recovery evidence for one file. Reported as a sorted list so a case can
    # assert both that a named strategy fired and that none did.
    recovery_for = case.get("observed_recovery_for")
    if recovery_for:
        selected: set[str] = set()
        for diagnostic in metrics.get("recovered_parser_diagnostics", ()):
            if diagnostic.get("file_path") != recovery_for:
                continue
            selected.update(diagnostic.get("selected_fallback_strategies") or ())
        observed["recovery/selected_fallback_strategies"] = sorted(selected)

    summary_fields = case.get("observed_inventory_summary", ())
    if summary_fields:
        observed["inventory_summary"] = {
            name: getattr(inventory, name, None) for name in summary_fields
        }

    counts = case.get("observed_inventory_counts", ())
    if counts:
        records = list(inventory)
        observed["inventory_counts"] = {
            "total_records": len(records),
            "included_in_metrics": sum(1 for r in records if r.included_in_metrics),
            "git_symlinks": sum(1 for r in records if r.is_git_symlink),
            "git_submodules": sum(1 for r in records if r.is_git_submodule),
            "oversized": sum(1 for r in records if r.oversized),
        }
        observed["inventory_counts"] = {
            name: observed["inventory_counts"][name] for name in counts
        }
    return observed


def _resolve(node: Any, pointer: str) -> Any:
    for part in pointer.strip("/").split("/"):
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def run_cases(case_ids: list[str] | None = None) -> list[CaseResult]:
    """Run every case, or one named subset, in deterministic manifest order."""
    manifest = load_manifest()
    wanted = list(case_ids) if case_ids else [case["id"] for case in manifest.cases]
    return [run_case(case_id) for case_id in wanted]
