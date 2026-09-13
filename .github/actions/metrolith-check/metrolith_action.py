"""Thin, cross-platform mechanics for the repository-local Metrolith action.

This module is intentionally ignorant of policies, metrics, and findings.  It
installs the checked-out source, invokes the public ``metrolith check`` command
once with an argument array, reads bounded metadata from the SARIF that command
wrote, and propagates the command's existing 0/1/2 result.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import importlib
import json
import os
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_PYTHON = (3, 13, 9)
VALID_EXITS = frozenset({0, 1, 2})
EXIT_ERROR = 2
VERDICTS = {0: "pass", 1: "fail", 2: "error"}
BASELINE_ORIGINS = frozenset({
    "protected_base_revision",
    "owner_controlled_artifact",
    "candidate_workspace",
    "local_file",
})
PROTECTED_BASELINE_ORIGINS = frozenset({
    "protected_base_revision",
    "owner_controlled_artifact",
})
EXIT_MEANINGS = {
    0: "check evaluated; no configured gate requires failure",
    1: "check evaluated; a configured gate requires failure",
    2: "policy/configuration/artifact/evaluation or integration failure",
}


class IntegrationError(RuntimeError):
    """The GitHub integration could not produce a trustworthy check result."""


_DEPRECATED_ENVIRONMENT_WARNINGS: set[str] = set()


def _environment_value(name: str, default: str = "") -> str:
    """Resolve a canonical Action variable and its deprecated fallback."""

    canonical = os.environ.get(name, "")
    legacy_name = name.replace("METROLITH_", "ARCHLENS_", 1)
    legacy = os.environ.get(legacy_name, "") if legacy_name != name else ""
    if canonical and legacy and canonical != legacy:
        raise IntegrationError(
            f"conflicting action environment variables: {name}, {legacy_name}"
        )
    if legacy and legacy_name not in _DEPRECATED_ENVIRONMENT_WARNINGS:
        print(
            f"WARNING: {legacy_name} is deprecated; use {name}.",
            file=sys.stderr,
            flush=True,
        )
        _DEPRECATED_ENVIRONMENT_WARNINGS.add(legacy_name)
    return canonical or legacy or default


def _require_supported_python() -> None:
    if sys.version_info[:3] != SUPPORTED_PYTHON:
        raise IntegrationError(
            "Metrolith requires the reviewed CPython 3.13.9 patch exactly"
        )


def _source_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    project_file = root / "pyproject.toml"
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise IntegrationError(
            "the action is not inside a complete Metrolith source checkout"
        ) from exc
    if project.get("name") != "metrolith":
        raise IntegrationError(
            "the action source root does not identify the Metrolith package"
        )
    return root


def _source_identity(root: Path) -> str:
    """Digest every installed evaluator/contract/runtime-delivery source byte."""

    fixed = (
        Path("pyproject.toml"),
        Path("pipeline.py"),
        Path("archlens_json.py"),
        Path(".github/actions/metrolith-check/action.yml"),
        Path(".github/actions/metrolith-check/metrolith_action.py"),
        Path(".github/workflows/metrolith-protected-required.yml"),
        Path("requirements/action-runtime.lock"),
        Path("tools/protected_workflow.py"),
    )
    paths = list(fixed)
    for directory, suffixes in (
        (Path("modules"), {".py"}),
        (Path("validation"), {".py"}),
        (Path("validation/resources/schemas"), {".json"}),
        (Path("config"), {".py", ".json"}),
    ):
        base = root / directory
        paths.extend(
            item.relative_to(root)
            for item in base.rglob("*")
            if item.is_file()
            and item.suffix in suffixes
            and "__pycache__" not in item.parts
        )
    digest = hashlib.sha256()
    for relative in sorted(set(paths), key=lambda item: item.as_posix()):
        path = root / relative
        if not path.is_file():
            raise IntegrationError("the protected evaluator source set is incomplete")
        name = relative.as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _protected_required() -> bool:
    return _parse_bool(
        _environment_value("METROLITH_PROTECTED_REQUIRED", "false"),
        "protected-required",
    )


def _protected_evaluator_identity() -> dict[str, str]:
    """Verify GitHub's Action contexts plus owner-supplied expected identity."""

    workspace = Path(_required_environment("GITHUB_WORKSPACE")).resolve(strict=True)
    action_path = Path(
        _environment_value("METROLITH_ACTION_PATH_OVERRIDE")
        or _required_environment("GITHUB_ACTION_PATH")
    ).resolve(strict=True)
    source_root = _source_root().resolve(strict=True)
    raw_candidate_root = _environment_value("METROLITH_CANDIDATE_ROOT").strip()
    candidate_boundary = (
        _workspace_path(
            raw_candidate_root,
            workspace=workspace,
            label="candidate root",
        ).resolve(strict=True)
        if raw_candidate_root
        else workspace
    )
    for candidate in (action_path, source_root):
        try:
            candidate.relative_to(candidate_boundary)
        except ValueError:
            pass
        else:
            raise IntegrationError(
                "protected-required refuses a candidate-local Action implementation"
            )

    repository = _required_environment("METROLITH_ACTION_REPOSITORY")
    revision = _required_environment("METROLITH_ACTION_REF")
    expected_repository = _required_environment(
        "METROLITH_EXPECTED_EVALUATOR_REPOSITORY"
    )
    expected_revision = _required_environment("METROLITH_EXPECTED_EVALUATOR_REVISION")
    expected_source = _required_environment("METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256")
    candidate_repository = _environment_value("METROLITH_CANDIDATE_REPOSITORY").strip()
    if candidate_repository and repository.casefold() == candidate_repository.casefold():
        raise IntegrationError("protected-required refuses the candidate repository as evaluator")
    if "/" not in repository or repository != expected_repository:
        raise IntegrationError("the executing Action repository is not the protected one")
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise IntegrationError("the executing Action ref is not a full commit SHA")
    if revision != expected_revision:
        raise IntegrationError("the executing Action revision is not the protected revision")
    if len(expected_source) != 64 or any(
        character not in "0123456789abcdef" for character in expected_source
    ):
        raise IntegrationError("the expected evaluator source digest is invalid")
    actual_source = _source_identity(source_root)
    if not hmac.compare_digest(actual_source, expected_source):
        raise IntegrationError("the evaluator source does not match its protected identity")
    return {
        "repository": repository,
        "revision": revision,
        "source_sha256": actual_source,
    }


def install_source() -> int:
    """Install the exact locked runtime and immutable Action checkout source."""
    _require_supported_python()
    if _protected_required():
        _protected_evaluator_identity()
    root = _source_root()
    lock = root / "requirements" / "action-runtime.lock"
    locked = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(lock),
        ],
        check=False,
        shell=False,
    )
    if locked.returncode != 0:
        return locked.returncode
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-build-isolation",
            "--no-deps",
            "--editable",
            str(root),
        ],
        check=False,
        shell=False,
    )
    return completed.returncode


def preflight() -> int:
    """Reject elevated PR execution before installing checked-out source."""
    _require_supported_python()
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request_target":
        raise IntegrationError("pull_request_target execution is refused")
    if _protected_required():
        _protected_evaluator_identity()
    return 0


def _required_environment(name: str) -> str:
    value = _environment_value(name)
    if value is None or not value.strip():
        raise IntegrationError(f"required action environment {name} is absent")
    return value


def _reject_control_characters(value: str, label: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise IntegrationError(f"the {label} path contains a control character")


def _workspace_path(raw: str, *, workspace: Path, label: str) -> Path:
    """Resolve a path and keep traversal/symlinks inside the checked-out tree."""
    _reject_control_characters(raw, label)
    supplied = Path(raw)
    candidate = supplied if supplied.is_absolute() else workspace / supplied
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise IntegrationError(
            f"the {label} path resolves outside GITHUB_WORKSPACE"
        ) from exc
    return resolved


def _optional_workspace_path(
    name: str, *, workspace: Path, label: str
) -> Path | None:
    """Resolve an OPTIONAL path input, or ``None`` when it was not supplied.

    GitHub passes an unset optional input as the empty string, so empty and
    whitespace-only both mean absent. When a value IS supplied it goes through
    exactly the same containment and control-character checks as every required
    path: an optional input is not a less-guarded one.

    Existence is deliberately not checked here. The action is a wrapper, and
    `metrolith check` already owns what an unreadable evidence file means -- it
    reports the admission outcome and lets the policy's own options decide the
    verdict. Second-guessing that here would put evidence semantics in the
    wrapper, which is precisely what it must not contain.
    """
    raw = _environment_value(name)
    if not raw.strip():
        return None
    return _workspace_path(raw, workspace=workspace, label=label)


def _optional_text(name: str, *, label: str) -> str | None:
    """Return one opaque optional argument after transport-level validation."""

    raw = _environment_value(name)
    if not raw.strip():
        return None
    _reject_control_characters(raw, label)
    return raw


#: The optional evidence inputs, as ``(environment, flag, label)``, in the fixed
#: order they reach the command line.
#:
#: A table rather than a branch per kind, so a kind cannot get a different
#: guard by accident: every entry goes through the same
#: :func:`_optional_workspace_path`, which applies the same containment and
#: control-character checks the required inputs get.
#:
#: **The order is fixed and `hotspots` stays first.** Adding a kind must not
#: move an existing one: a workflow that passes only `--hotspots` produces the
#: same argument array it produced before `duplication` existed.
#:
#: The wrapper transports a PATH and nothing else. It does not open these files
#: and holds no vocabulary from the analyses that produce them -- naming what is
#: inside a document is the first step towards interpreting it. `metrolith check`
#: owns admission, and what an unreadable or mismatched document means is a
#: policy outcome, not an integration one.
EVIDENCE_INPUTS: tuple[tuple[str, str, str], ...] = (
    ("METROLITH_HOTSPOTS", "--hotspots", "hotspots"),
    ("METROLITH_DUPLICATION", "--duplication", "duplication"),
)


def _evidence_arguments(workspace: Path) -> tuple[str, ...]:
    """Resolve every supplied evidence path into command-line arguments.

    An absent input contributes NO argument, so the command line is
    byte-for-byte what it was before that input existed. A flag with an empty
    value would be a different invocation, and "unset" must not mean "set to
    nothing".
    """
    arguments: list[str] = []
    for name, flag, label in EVIDENCE_INPUTS:
        resolved = _optional_workspace_path(name, workspace=workspace, label=label)
        if resolved is not None:
            arguments.extend((flag, str(resolved)))
    return tuple(arguments)


def _trust_arguments(workspace: Path) -> tuple[tuple[str, ...], dict[str, str] | None]:
    """Transport protected trust anchors; never interpret Policy/evidence."""

    if not _protected_required():
        return ("--gate-mode", "local_unprotected"), None
    identity = _protected_evaluator_identity()
    policy_sha256 = _required_environment("METROLITH_POLICY_SHA256")
    receipt = _optional_workspace_path(
        "METROLITH_EVIDENCE_RECEIPT", workspace=workspace, label="evidence receipt"
    )
    receipt_sha256 = _optional_text(
        "METROLITH_EVIDENCE_RECEIPT_SHA256", label="evidence-receipt-sha256"
    )
    if (receipt is None) != (receipt_sha256 is None):
        raise IntegrationError("evidence receipt path and digest must be supplied together")
    arguments = [
        "--gate-mode", "protected_required",
        "--policy-sha256", policy_sha256,
        "--evaluator-repository", identity["repository"],
        "--evaluator-revision", identity["revision"],
        "--evaluator-source-sha256", identity["source_sha256"],
    ]
    if receipt is not None:
        arguments.extend((
            "--evidence-receipt", str(receipt),
            "--evidence-receipt-sha256", str(receipt_sha256),
        ))
    return tuple(arguments), identity


def _baseline_arguments(workspace: Path) -> tuple[str, ...]:
    """Transport opaque baseline inputs without opening or interpreting them."""

    baseline = _optional_workspace_path(
        "METROLITH_BASELINE", workspace=workspace, label="baseline"
    )
    digest = _optional_text(
        "METROLITH_BASELINE_SHA256", label="baseline-sha256"
    )
    declared_origin = _optional_text(
        "METROLITH_BASELINE_ORIGIN", label="baseline-origin"
    )
    required = _parse_bool(
        _environment_value("METROLITH_BASELINE_REQUIRED", "false"),
        "baseline-required",
    )
    if required and baseline is None:
        raise IntegrationError("baseline-required is true but no baseline was supplied")
    if baseline is None and declared_origin is not None:
        raise IntegrationError("baseline-origin requires a baseline")

    pull_request = os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    origin = declared_origin
    if baseline is not None and origin is None:
        origin = "candidate_workspace" if pull_request else "local_file"
    if origin is not None and origin not in BASELINE_ORIGINS:
        raise IntegrationError("baseline-origin is unsupported")
    if required and origin not in PROTECTED_BASELINE_ORIGINS:
        raise IntegrationError(
            "baseline-required requires a protected baseline-origin"
        )

    arguments: list[str] = []
    if baseline is not None:
        arguments.extend(("--baseline", str(baseline)))
    if digest is not None:
        arguments.extend(("--baseline-sha256", digest))
    if origin is not None:
        arguments.extend(("--baseline-origin", origin))
    if baseline is not None and pull_request:
        arguments.append("--baseline-pull-request")
    return tuple(arguments)


def _parse_bool(value: str, label: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise IntegrationError(f"{label} must be the literal true or false")


def _pull_request_is_from_fork(event_path: str | None, repository: str | None) -> bool:
    """Fail closed when a pull-request event cannot prove same-repo origin."""
    if not event_path or not repository:
        return True
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        head_repository = event["pull_request"]["head"]["repo"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return True
    if not isinstance(head_repository, Mapping):
        return True
    if head_repository.get("fork") is True:
        return True
    return head_repository.get("full_name") != repository


def _upload_disposition(requested: bool) -> tuple[bool, str]:
    if not requested:
        return False, "disabled"
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request_target":
        raise IntegrationError("pull_request_target execution is refused")
    if event_name == "pull_request" and _pull_request_is_from_fork(
        os.environ.get("GITHUB_EVENT_PATH"), os.environ.get("GITHUB_REPOSITORY")
    ):
        return False, "fork-pull-request"
    return True, "eligible"


def _file_instance(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns)


def _metrolith_executable() -> str | None:
    """Find the console script installed for this exact Python environment."""
    scripts = Path(sysconfig.get_path("scripts"))
    names = ("metrolith.exe", "metrolith") if os.name == "nt" else ("metrolith",)
    for name in names:
        candidate = scripts / name
        if candidate.is_file():
            return str(candidate)
    return None


def _verify_installed_evaluator(
    source_root: Path, identity: dict[str, str] | None
) -> None:
    """Prove imports and the installed package resolve to this Action source."""

    try:
        source_version = tomllib.loads(
            (source_root / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        installed_version = importlib.metadata.version("metrolith")
    except (OSError, KeyError, importlib.metadata.PackageNotFoundError) as exc:
        raise IntegrationError("the checked-out Metrolith source is not installed") from exc
    if installed_version != source_version:
        raise IntegrationError(
            "the installed Metrolith version does not match the action source"
        )
    for module_name in (
        "pipeline",
        "archlens_json",
        "modules.policy.check",
        "modules.policy.document_v2",
        "modules.policy.evidence",
        "modules.policy.sarif",
        "validation.artifact_io.reader",
    ):
        module = importlib.import_module(module_name)
        raw_path = getattr(module, "__file__", None)
        if not raw_path:
            raise IntegrationError("an installed evaluator module has no source path")
        try:
            Path(raw_path).resolve(strict=True).relative_to(source_root)
        except (OSError, ValueError) as exc:
            raise IntegrationError(
                "an evaluator module resolved outside the protected Action source"
            ) from exc
    if identity is not None and _source_identity(source_root) != identity["source_sha256"]:
        raise IntegrationError("the installed evaluator source changed after admission")


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationError(f"generated SARIF has no {label} object")
    return value


def _read_sarif(path: Path, process_exit: int) -> dict[str, Any]:
    """Read but never rewrite the SARIF produced by the public CLI."""
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("Metrolith did not create readable SARIF") from exc
    if not isinstance(document, Mapping):
        raise IntegrationError("generated SARIF is not an object")
    if document.get("version") != "2.1.0":
        raise IntegrationError("Metrolith output is not SARIF 2.1.0")
    runs = document.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise IntegrationError("Metrolith SARIF must contain exactly one run")
    run = _as_mapping(runs[0], "run")
    tool = _as_mapping(run.get("tool"), "tool")
    driver = _as_mapping(tool.get("driver"), "driver")
    if driver.get("name") != "Metrolith":
        raise IntegrationError("generated SARIF is not a Metrolith result")
    properties = _as_mapping(run.get("properties"), "run properties")
    technical_properties = _as_mapping(
        properties.get("archlens"), "Metrolith run properties"
    )
    embedded_exit = technical_properties.get("exitCode")
    if embedded_exit not in VALID_EXITS or embedded_exit != process_exit:
        raise IntegrationError("process and SARIF exit codes do not agree")
    verdict = technical_properties.get("verdict")
    if verdict != VERDICTS[process_exit]:
        raise IntegrationError("process exit and SARIF verdict do not agree")
    counts = _as_mapping(technical_properties.get("counts", {}), "counts")
    by_severity = _as_mapping(counts.get("by_severity", {}), "severity counts")
    inputs = _as_mapping(
        technical_properties.get("evaluatedInputProvenance"),
        "evaluated input provenance",
    )
    expected_protected = _protected_required()
    if inputs.get("protected_gate") is not expected_protected:
        raise IntegrationError(
            "the generated SARIF protection status does not match the requested mode"
        )

    def count(mapping: Mapping[str, Any], key: str) -> int:
        value = mapping.get(key, 0)
        if not isinstance(value, int) or value < 0:
            raise IntegrationError("generated SARIF contains an invalid count")
        return value

    return {
        "exit-code": str(process_exit),
        "verdict": verdict,
        "sarif-created": "true",
        "sarif-sha256": hashlib.sha256(payload).hexdigest(),
        "finding-count": str(count(counts, "findings")),
        "violation-count": str(count(by_severity, "violation")),
        "warning-count": str(count(by_severity, "warning")),
        "info-count": str(count(by_severity, "info")),
        "failure-kind": str(technical_properties.get("failureKind") or ""),
        "evaluator-protection": (
            "protected" if expected_protected else "local-unprotected"
        ),
    }


def _emit_outputs(values: Mapping[str, str]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise IntegrationError("an action output contains a newline")
            handle.write(f"{key}={value}\n")


def _markdown_code(value: str) -> str:
    fence = "`" * (max((len(part) for part in value.split("`")), default=0) + 1)
    return f"{fence}{value}{fence}"


def _append_summary(values: Mapping[str, str], *, message: str | None = None) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    lines = ["## Metrolith quality gate", ""]
    if message is not None:
        lines.extend([
            "- Result: ERROR",
            "- The integration could not produce a trustworthy SARIF result.",
        ])
    else:
        exit_code = int(values["exit-code"])
        lines.extend([
            f"- Result: {values['verdict'].upper()}",
            f"- Exit {exit_code}: {EXIT_MEANINGS[exit_code]}",
            f"- Findings: {values['finding-count']} "
            f"(violation {values['violation-count']}, warning "
            f"{values['warning-count']}, info {values['info-count']})",
            f"- SARIF: {_markdown_code(values['sarif-path'])}",
            f"- Evaluator: {values['evaluator-protection']}",
        ])
        if values.get("failure-kind"):
            lines.append(f"- Failure kind: {_markdown_code(values['failure-kind'])}")
    with Path(destination).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def run_check() -> int:
    _require_supported_python()
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request_target":
        raise IntegrationError("pull_request_target execution is refused")
    workspace = Path(_required_environment("GITHUB_WORKSPACE")).resolve(strict=True)
    if not workspace.is_dir():
        raise IntegrationError("GITHUB_WORKSPACE is not a directory")
    run_path = _workspace_path(
        _required_environment("METROLITH_RUN"), workspace=workspace, label="run"
    )
    policy_path = _workspace_path(
        _required_environment("METROLITH_POLICY"), workspace=workspace, label="policy"
    )
    evidence_arguments = _evidence_arguments(workspace)
    trust_arguments, protected_identity = _trust_arguments(workspace)
    baseline_arguments = _baseline_arguments(workspace)
    sarif_path = _workspace_path(
        _required_environment("METROLITH_SARIF"), workspace=workspace, label="SARIF"
    )
    if sarif_path == workspace or sarif_path.is_dir():
        raise IntegrationError("the SARIF output path identifies a directory")
    requested = _parse_bool(
        _environment_value("METROLITH_UPLOAD_SARIF", "false"), "upload-sarif"
    )
    upload_eligible, upload_reason = _upload_disposition(requested)

    source_root = _source_root().resolve(strict=True)
    _verify_installed_evaluator(source_root, protected_identity)

    executable = _metrolith_executable()
    if executable is None:
        raise IntegrationError("the installed metrolith console command is unavailable")
    before = _file_instance(sarif_path)
    completed = subprocess.run(
        [
            executable,
            "check",
            str(run_path),
            "--policy",
            str(policy_path),
            *trust_arguments,
            *evidence_arguments,
            *baseline_arguments,
            "--format",
            "sarif",
            "--output",
            str(sarif_path),
        ],
        check=False,
        shell=False,
        cwd=str(source_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process_exit = completed.returncode
    if process_exit not in VALID_EXITS:
        raise IntegrationError("metrolith check returned an unsupported process exit")
    after = _file_instance(sarif_path)
    if after is None or after == before:
        raise IntegrationError("metrolith check did not create a fresh SARIF file")

    values = _read_sarif(sarif_path, process_exit)
    values.update({
        "sarif-path": sarif_path.relative_to(workspace).as_posix(),
        "upload-eligible": str(upload_eligible).lower(),
        "upload-reason": upload_reason,
    })
    _emit_outputs(values)
    _append_summary(values)
    return process_exit


def _failure_outputs() -> dict[str, str]:
    return {
        "exit-code": "2",
        "verdict": "error",
        "sarif-created": "false",
        "sarif-path": "",
        "sarif-sha256": "",
        "finding-count": "0",
        "violation-count": "0",
        "warning-count": "0",
        "info-count": "0",
        "failure-kind": "integration_error",
        "evaluator-protection": "not-verified",
        "upload-eligible": "false",
        "upload-reason": "integration-error",
    }


def propagate() -> int:
    try:
        value = int(_environment_value("METROLITH_EXIT_CODE"))
    except ValueError:
        return EXIT_ERROR
    return value if value in VALID_EXITS else EXIT_ERROR


def verify_sarif() -> int:
    """Prove that GitHub integration steps left the Metrolith bytes untouched."""
    workspace = Path(_required_environment("GITHUB_WORKSPACE")).resolve(strict=True)
    sarif_path = _workspace_path(
        _required_environment("METROLITH_SARIF"), workspace=workspace, label="SARIF"
    )
    expected = _required_environment("METROLITH_EXPECTED_SHA256")
    if len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise IntegrationError("the expected SARIF digest is invalid")
    try:
        observed = hashlib.sha256(sarif_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise IntegrationError("the generated SARIF is no longer readable") from exc
    if observed != expected:
        raise IntegrationError(
            "the generated SARIF bytes changed after Metrolith wrote them"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["preflight"]:
        try:
            return preflight()
        except IntegrationError:
            print(
                "::error title=Metrolith security boundary::"
                "pull_request_target execution is refused."
            )
            return EXIT_ERROR
    if arguments == ["install"]:
        try:
            return install_source()
        except IntegrationError:
            print(
                "::error title=Metrolith setup error::"
                "The checked-out Metrolith source could not be installed safely."
            )
            return EXIT_ERROR
    if arguments == ["check"]:
        try:
            return run_check()
        except (IntegrationError, OSError):
            values = _failure_outputs()
            try:
                _emit_outputs(values)
                _append_summary(values, message="integration error")
            except (IntegrationError, OSError):
                pass
            print(
                "::error title=Metrolith integration error::"
                "The action could not produce a trustworthy SARIF result."
            )
            return EXIT_ERROR
    if arguments == ["propagate"]:
        return propagate()
    if arguments == ["verify"]:
        try:
            return verify_sarif()
        except (IntegrationError, OSError):
            print(
                "::error title=Metrolith SARIF integrity error::"
                "The generated SARIF bytes changed or became unreadable."
            )
            return EXIT_ERROR
    print("::error title=Metrolith integration error::Unknown action runner mode.")
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
