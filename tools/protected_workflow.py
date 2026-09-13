"""Fail-closed mechanics for the owner-controlled protected workflow.

This helper is part of the reviewed evaluator source set.  It validates all
owner trust configuration before the evaluator is installed, verifies that the
candidate checkout is data-only and disjoint from evaluator/custody roots, and
invokes the supported receipt producer with an argument array.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Mapping


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PLACEHOLDER = re.compile(
    r"(?:<[^>]+>|\b(?:FULL_SHA|SHA256|OWNER|METROLITH_REPOSITORY|REPLACE_ME|PLACEHOLDER)\b)"
)
ALLOWED_EVENTS = frozenset({"pull_request", "merge_group"})


class ProtectedWorkflowError(RuntimeError):
    """One bounded reason the protected workflow must stop."""


_DEPRECATED_ENVIRONMENT_WARNINGS: set[str] = set()


def _environment_value(name: str) -> str:
    canonical = os.environ.get(name, "")
    legacy_name = name.replace("METROLITH_", "ARCHLENS_", 1)
    legacy = os.environ.get(legacy_name, "") if legacy_name != name else ""
    if canonical and legacy and canonical != legacy:
        raise ProtectedWorkflowError(
            f"conflicting_environment:{name}:{legacy_name}"
        )
    if legacy and legacy_name not in _DEPRECATED_ENVIRONMENT_WARNINGS:
        print(
            f"WARNING: {legacy_name} is deprecated; use {name}.",
            file=sys.stderr,
            flush=True,
        )
        _DEPRECATED_ENVIRONMENT_WARNINGS.add(legacy_name)
    return canonical or legacy


def _required(name: str) -> str:
    value = _environment_value(name)
    if not value.strip():
        raise ProtectedWorkflowError(f"missing_configuration:{name}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProtectedWorkflowError(f"invalid_control_character:{name}")
    if PLACEHOLDER.search(value):
        raise ProtectedWorkflowError(f"placeholder_configuration:{name}")
    return value


def _optional(name: str) -> str | None:
    value = _environment_value(name)
    if not value.strip():
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProtectedWorkflowError(f"invalid_control_character:{name}")
    if PLACEHOLDER.search(value):
        raise ProtectedWorkflowError(f"placeholder_configuration:{name}")
    return value


def _repository(name: str) -> str:
    value = _required(name)
    if REPOSITORY.fullmatch(value) is None:
        raise ProtectedWorkflowError(f"invalid_repository:{name}")
    return value


def _full_sha(name: str) -> str:
    value = _required(name)
    if FULL_SHA.fullmatch(value) is None:
        raise ProtectedWorkflowError(f"full_sha_required:{name}")
    return value


def _sha256(name: str, *, optional: bool = False) -> str | None:
    value = _optional(name) if optional else _required(name)
    if value is None:
        return None
    if SHA256.fullmatch(value) is None:
        raise ProtectedWorkflowError(f"sha256_required:{name}")
    return value


def _root(name: str) -> Path:
    path = Path(_required(name)).resolve(strict=True)
    if not path.is_dir():
        raise ProtectedWorkflowError(f"directory_required:{name}")
    return path


def _inside(root: Path, raw: str, label: str, *, must_exist: bool = True) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProtectedWorkflowError(f"protected_relative_path_required:{label}")
    path = (root / relative).resolve(strict=must_exist)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProtectedWorkflowError(f"path_escaped_protected_root:{label}") from exc
    return path


def _disjoint(left: Path, right: Path, label: str) -> None:
    try:
        left.relative_to(right)
    except ValueError:
        try:
            right.relative_to(left)
        except ValueError:
            return
    raise ProtectedWorkflowError(f"overlapping_trust_roots:{label}")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise ProtectedWorkflowError("git_checkout_identity_unreadable")
    return completed.stdout.strip()


def _remote_coordinate(value: str) -> str | None:
    normalized = value.strip()
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    for prefix in prefixes:
        if normalized.startswith(prefix):
            coordinate = normalized[len(prefix):]
            if coordinate.endswith(".git"):
                coordinate = coordinate[:-4]
            return coordinate
    return None


def _verify_checkout(root: Path, repository: str, revision: str) -> None:
    if _git(root, "rev-parse", "HEAD") != revision:
        raise ProtectedWorkflowError("checkout_revision_mismatch")
    remote = _remote_coordinate(_git(root, "remote", "get-url", "origin"))
    if remote is None or remote.casefold() != repository.casefold():
        raise ProtectedWorkflowError("checkout_repository_mismatch")


def _action_module(evaluator_root: Path):
    source = evaluator_root / ".github" / "actions" / "metrolith-check" / "metrolith_action.py"
    spec = importlib.util.spec_from_file_location("metrolith_protected_action_identity", source)
    if spec is None or spec.loader is None:
        raise ProtectedWorkflowError("evaluator_identity_module_unreadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_file_digest(path: Path, expected: str, code: str) -> str:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ProtectedWorkflowError(code)
    return actual


def _emit(values: Mapping[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with Path(output).open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ProtectedWorkflowError("multiline_workflow_output_refused")
            handle.write(f"{key}={value}\n")


def _emit_environment(values: Mapping[str, str]) -> None:
    destination = os.environ.get("GITHUB_ENV")
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ProtectedWorkflowError("multiline_workflow_environment_refused")
            handle.write(f"{key}={value}\n")


def validate_configuration() -> int:
    event = _required("GITHUB_EVENT_NAME")
    if event == "pull_request_target":
        raise ProtectedWorkflowError("pull_request_target_refused")
    if event not in ALLOWED_EVENTS:
        raise ProtectedWorkflowError("unsupported_protected_event")

    candidate_repository = _repository("METROLITH_CANDIDATE_REPOSITORY")
    candidate_revision = _full_sha("METROLITH_CANDIDATE_REVISION")
    evaluator_repository = _repository("METROLITH_EXPECTED_EVALUATOR_REPOSITORY")
    evaluator_revision = _full_sha("METROLITH_EXPECTED_EVALUATOR_REVISION")
    evaluator_source = _sha256("METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256")
    custody_repository = _repository("METROLITH_CUSTODY_REPOSITORY")
    custody_revision = _full_sha("METROLITH_CUSTODY_REVISION")
    expected_program = _required("METROLITH_EXPECTED_PROGRAM_VERSION")
    expected_lock = _sha256("METROLITH_EXPECTED_RUNTIME_LOCK_SHA256")
    expected_policy = _sha256("METROLITH_POLICY_SHA256")
    expected_manifest = _sha256("METROLITH_RUN_MANIFEST_SHA256")
    expected_receipt = _sha256("METROLITH_EVIDENCE_RECEIPT_SHA256")
    expected_baseline = _sha256("METROLITH_BASELINE_SHA256", optional=True)

    if evaluator_repository.casefold() == candidate_repository.casefold():
        raise ProtectedWorkflowError("candidate_local_evaluator_refused")
    candidate_root = _root("METROLITH_CANDIDATE_ROOT")
    evaluator_root = _root("METROLITH_EVALUATOR_ROOT")
    custody_root = _root("METROLITH_CUSTODY_ROOT")
    _disjoint(candidate_root, evaluator_root, "candidate_evaluator")
    _disjoint(candidate_root, custody_root, "candidate_custody")
    _disjoint(evaluator_root, custody_root, "evaluator_custody")

    _verify_checkout(candidate_root, candidate_repository, candidate_revision)
    _verify_checkout(evaluator_root, evaluator_repository, evaluator_revision)
    _verify_checkout(custody_root, custody_repository, custody_revision)

    action = _action_module(evaluator_root)
    actual_source = action._source_identity(evaluator_root)
    if actual_source != evaluator_source:
        raise ProtectedWorkflowError("evaluator_source_digest_mismatch")

    project = tomllib.loads((evaluator_root / "pyproject.toml").read_text(encoding="utf-8"))
    actual_program = str(project["project"]["version"])
    if actual_program != expected_program:
        raise ProtectedWorkflowError("evaluator_program_version_mismatch")
    lock_path = evaluator_root / "requirements" / "action-runtime.lock"
    actual_lock = _verify_file_digest(
        lock_path,
        str(expected_lock),
        "runtime_lock_digest_mismatch",
    )

    run_root = _inside(custody_root, _required("METROLITH_RUN_RELATIVE_PATH"), "run")
    if not run_root.is_dir():
        raise ProtectedWorkflowError("custody_run_directory_required")
    manifest = run_root / "run_manifest.json"
    _verify_file_digest(manifest, str(expected_manifest), "run_manifest_digest_mismatch")
    policy = _inside(custody_root, _required("METROLITH_POLICY_RELATIVE_PATH"), "policy")
    _verify_file_digest(policy, str(expected_policy), "policy_digest_mismatch")

    protected_paths = {
        "METROLITH_PROTECTED_RUN_PATH": str(run_root),
        "METROLITH_PROTECTED_POLICY_PATH": str(policy),
        "METROLITH_PROTECTED_HOTSPOTS_PATH": "",
        "METROLITH_PROTECTED_DUPLICATION_PATH": "",
        "METROLITH_PROTECTED_BASELINE_PATH": "",
        "METROLITH_PROTECTED_BASELINE_ORIGIN": "",
    }
    for env_name, label in (
        ("METROLITH_HOTSPOTS_RELATIVE_PATH", "hotspots"),
        ("METROLITH_DUPLICATION_RELATIVE_PATH", "duplication"),
    ):
        value = _optional(env_name)
        if value is not None:
            path = _inside(custody_root, value, label)
            protected_paths[
                "METROLITH_PROTECTED_HOTSPOTS_PATH"
                if label == "hotspots"
                else "METROLITH_PROTECTED_DUPLICATION_PATH"
            ] = str(path)

    baseline_relative = _optional("METROLITH_BASELINE_RELATIVE_PATH")
    if (baseline_relative is None) != (expected_baseline is None):
        raise ProtectedWorkflowError("baseline_path_and_digest_required_together")
    if baseline_relative is not None:
        baseline = _inside(custody_root, baseline_relative, "baseline")
        _verify_file_digest(
            baseline,
            str(expected_baseline),
            "baseline_digest_mismatch",
        )
        source_run = baseline.parent / "BASELINE.source-run"
        if not source_run.is_dir():
            raise ProtectedWorkflowError("baseline_source_run_sidecar_required")
        protected_paths["METROLITH_PROTECTED_BASELINE_PATH"] = str(baseline)
        protected_paths["METROLITH_PROTECTED_BASELINE_ORIGIN"] = (
            "protected_base_revision"
            if custody_repository.casefold() == candidate_repository.casefold()
            else "owner_controlled_artifact"
        )

    _emit_environment(protected_paths)

    _emit(
        {
            "candidate-repository": candidate_repository,
            "candidate-revision": candidate_revision,
            "evaluator-repository": evaluator_repository,
            "evaluator-revision": evaluator_revision,
            "evaluator-source-sha256": actual_source,
            "program-version": actual_program,
            "runtime-lock-sha256": actual_lock,
            "policy-sha256": str(expected_policy),
            "run-manifest-sha256": str(expected_manifest),
            "receipt-sha256": str(expected_receipt),
            "baseline-sha256": str(expected_baseline or ""),
        }
    )
    return 0


def _canonical_repository(value: str) -> str | None:
    normalized = value.strip()
    for prefix in ("https://github.com/", "http://github.com/"):
        if normalized.startswith(prefix):
            coordinate = normalized[len(prefix):].rstrip("/")
            if coordinate.endswith(".git"):
                coordinate = coordinate[:-4]
            return coordinate
    return None


def verify_candidate_binding() -> int:
    candidate_repository = _repository("METROLITH_CANDIDATE_REPOSITORY")
    candidate_revision = _full_sha("METROLITH_CANDIDATE_REVISION")
    custody_root = _root("METROLITH_CUSTODY_ROOT")
    run_root = _inside(custody_root, _required("METROLITH_RUN_RELATIVE_PATH"), "run")

    from validation.artifact_io.reader import open_run

    view = open_run(run_root)
    if not view.succeeded or len(view.repositories) != 1:
        raise ProtectedWorkflowError("single_successful_candidate_run_required")
    repository = view.repositories[0]
    acquisition = repository.get("acquisition") or {}
    if acquisition.get("analyzed_commit_sha") != candidate_revision:
        raise ProtectedWorkflowError("candidate_revision_run_mismatch")
    recorded = _canonical_repository(str(repository.get("repository_url") or ""))
    if recorded is None or recorded.casefold() != candidate_repository.casefold():
        raise ProtectedWorkflowError("candidate_repository_run_mismatch")
    return 0


def create_receipt() -> int:
    evaluator_root = _root("METROLITH_EVALUATOR_ROOT")
    custody_root = _root("METROLITH_CUSTODY_ROOT")
    gate_root = _root("METROLITH_GATE_ROOT")
    run_root = _inside(custody_root, _required("METROLITH_RUN_RELATIVE_PATH"), "run")
    output = (gate_root / "trusted-evidence-receipt.json").resolve(strict=False)
    output.relative_to(gate_root)

    command = [
        sys.executable,
        str(evaluator_root / "pipeline.py"),
        "trust",
        "receipt",
        "create",
        str(run_root),
        "--evaluator-repository",
        _repository("METROLITH_EXPECTED_EVALUATOR_REPOSITORY"),
        "--evaluator-revision",
        _full_sha("METROLITH_EXPECTED_EVALUATOR_REVISION"),
        "--evaluator-source-sha256",
        str(_sha256("METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256")),
        "--output",
        str(output),
    ]
    for env_name, flag, label in (
        ("METROLITH_HOTSPOTS_RELATIVE_PATH", "--hotspots", "hotspots"),
        ("METROLITH_DUPLICATION_RELATIVE_PATH", "--duplication", "duplication"),
    ):
        value = _optional(env_name)
        if value is not None:
            command.extend((flag, str(_inside(custody_root, value, label))))

    completed = subprocess.run(
        command,
        check=False,
        shell=False,
        cwd=str(evaluator_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise ProtectedWorkflowError("receipt_producer_failed")
    expected = _sha256("METROLITH_EVIDENCE_RECEIPT_SHA256")
    actual = _verify_file_digest(
        output,
        str(expected),
        "receipt_digest_mismatch",
    )
    _emit({"receipt-path": output.as_posix(), "receipt-sha256": actual})
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments == ["validate"]:
            return validate_configuration()
        if arguments == ["verify-candidate"]:
            return verify_candidate_binding()
        if arguments == ["create-receipt"]:
            return create_receipt()
        raise ProtectedWorkflowError("unknown_protected_workflow_command")
    except (OSError, KeyError, ValueError, ProtectedWorkflowError) as exc:
        code = str(exc).split(":", 1)[0] or type(exc).__name__
        print(f"::error title=Metrolith protected workflow::{code}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
