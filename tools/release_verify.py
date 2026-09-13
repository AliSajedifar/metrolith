"""Read-only Metrolith release verification and machine-readable evidence.

This repository tool verifies a candidate commit. It never creates a tag,
pushes, publishes, or writes inside the repository. Build, doctor, install, and
smoke-test state lives in an operating-system temporary directory; evidence is
printed to stdout unless ``--output`` names a path outside the repository.
"""

from __future__ import annotations

import argparse
import codecs
import gzip
import hashlib
import io
import importlib.metadata
import json
import os
import platform
import queue
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


EVIDENCE_FORMAT = "archlens-release-verification"
EVIDENCE_FORMAT_VERSION = "1.0.0"
VERIFICATION_PYTHON_VERSION = "3.13.9"
LOCK_FILE = Path("requirements/release-verification.lock")
CLI_COMMANDS = (
    "run",
    "analyze",
    "audit",
    "menu",
    "migrate-input",
    "example",
    "doctor",
    "validate",
    "compare",
    "schema",
    "explain",
    "reproduce",
    "report",
    "perf",
    "diff",
    "policy",
    "check",
    "baseline",
    "hotspots",
    "duplication",
    "changed",
    "dossier",
    "trust",
)
FOCUSED_HARDENING_TESTS = (
    "tests/test_policy_v2_check.py",
    "tests/test_schema_identity_hardening.py",
    "tests/test_cognitive_persistence_g1c.py",
    "tests/test_historical_fixture_governance.py",
    "tests/test_diagnostic_consistency_v35.py",
    "tests/test_hypothesis_triage_v35.py",
    "tests/test_release_verification.py",
    "tests/test_release_documentation.py",
    "tests/test_final_release_hardening.py",
    "tests/test_github_action.py",
    "tests/test_protected_workflow_closure.py",
    "tests/test_packaging_v33.py",
)

# These are intentionally outside this local, read-only campaign. Keeping them
# in every evidence document prevents a green local run from being presented as
# proof that an owner/service-dependent release boundary was exercised.
DEFERRED_VALIDATIONS = (
    {
        "id": "owner-publication-metadata",
        "reason": "License, citation, author, maintainer, and project URL decisions require owner input.",
    },
    {
        "id": "github-hosted-action",
        "reason": "Hosted runners, permissions, artifact service, and SARIF/Code Scanning processing require an owner-approved GitHub repository.",
    },
    {
        "id": "publication-actions",
        "reason": "No commit, tag, push, GitHub release, or package-index publication is performed by verification.",
    },
    {
        "id": "benchmark-of-record-publication",
        "reason": "BOR modification and publication are outside release hardening.",
    },
    {
        "id": "standalone-output-schema-policy",
        "reason": "Standalone-output schema integration remains a separate compatibility-policy decision.",
    },
    {
        "id": "changed-code-portability-campaign",
        "reason": "Real-subject and non-UTF-8 path portability validation remains an external campaign.",
    },
    {
        "id": "pipeline-and-cli-refactors",
        "reason": "Pipeline, namespace, and CLI-layer refactors are explicitly excluded from this campaign.",
    },
)

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
_NORMALIZE_NAME = re.compile(r"[-_.]+")
_ACTIVE_COMMAND_LOG: list[dict[str, Any]] | None = None
_ACTIVE_REPOSITORY: Path | None = None
_ACTIVE_LOG_DIRECTORY: Path | None = None
_ACTIVE_STAGE: str | None = None

REQUIRED_GATES = (
    "repository_clean_before", "version_consistency", "locked_environment",
    "schema_identity", "schema_validation", "standalone_contracts", "commit_delta",
    "doctor", "source_reconciliation", "compileall", "focused_hardening_tests",
    "full_test_suite", "packaging", "isolated_wheel_install", "installed_wheel_smoke",
    "cli_command_availability", "installed_conformance", "repository_clean_after",
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    # The documented invocation executes a file under tools/, so Python would
    # otherwise prefer an unrelated installed Metrolith over this candidate.
    sys.path.insert(0, str(REPOSITORY_ROOT))


class VerificationFailure(RuntimeError):
    """One required release-verification check did not pass."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _command_text(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def _portable_evidence_text(value: str) -> str:
    replacements = (
        (str(Path(sys.executable).resolve()), "<verification-python>"),
        (str(_ACTIVE_REPOSITORY), "<repository>") if _ACTIVE_REPOSITORY else ("", ""),
        (str(Path(tempfile.gettempdir()).resolve()), "<temporary-root>"),
        (str(Path.home().resolve()), "<user-home>"),
    )
    portable = value
    for actual, token in replacements:
        if actual:
            portable = re.sub(re.escape(actual), token, portable, flags=re.IGNORECASE)
    return portable


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    echo_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Drain both byte streams live; keep lossless external logs and text results."""
    rendered = _command_text(command)
    directory = _ACTIVE_LOG_DIRECTORY or Path(tempfile.mkdtemp(prefix="metrolith-command-"))
    number = len(_ACTIVE_COMMAND_LOG) + 1 if _ACTIVE_COMMAND_LOG is not None else 1
    prefix = f"{number:03d}-{_ACTIVE_STAGE or 'command'}"
    paths = {name: directory / f"{prefix}.{name}.log" for name in ("stdout", "stderr")}
    entry = {
        "command": _portable_evidence_text(rendered),
        "cwd": _portable_evidence_text(str(cwd.resolve())),
        "stage": _ACTIVE_STAGE,
        "started_at": _utc_now(),
        "returncode": None,
        "logs": {name: str(path) for name, path in paths.items()},
    }
    if _ACTIVE_COMMAND_LOG is not None:
        _ACTIVE_COMMAND_LOG.append(entry)
    print(f"[release-verify] START command: {rendered}\n"
          f"[release-verify] logs: {paths['stdout']} | {paths['stderr']}", file=sys.stderr, flush=True)
    started = time.monotonic()
    streams: dict[str, bytearray] = {name: bytearray() for name in paths}
    events: queue.Queue = queue.Queue(maxsize=64)

    def drain(name, pipe):
        try:
            while chunk := pipe.read1(4096):
                events.put((name, chunk))
        except Exception as exc:
            events.put((name, exc))
        finally:
            events.put((name, None))

    try:
        with paths['stdout'].open('wb') as stdout_log, paths['stderr'].open('wb') as stderr_log:
            logs = {"stdout": stdout_log, "stderr": stderr_log}
            with subprocess.Popen(
                [str(part) for part in command], cwd=cwd,
                env=dict(env) if env is not None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as process:
                entry['pid'] = process.pid
                decoders = {name: codecs.getincrementaldecoder('utf-8')('backslashreplace') for name in paths}
                readers = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
                           for name in paths]
                for reader in readers:
                    reader.start()
                finished = 0
                while finished < len(readers):
                    name, chunk = events.get()
                    if isinstance(chunk, Exception):
                        raise chunk
                    if chunk is None:
                        finished += 1
                    else:
                        logs[name].write(chunk)
                        logs[name].flush()
                        streams[name].extend(chunk)
                    text = decoders[name].decode(chunk or b'', final=chunk is None)
                    if echo_output or name == 'stderr':
                        print(text, end='', file=sys.stderr, flush=True)
                for reader in readers:
                    reader.join()
                entry['returncode'] = process.wait()
    except Exception as exc:
        entry['error'] = str(exc)
        raise
    finally:
        entry['duration_seconds'] = round(time.monotonic() - started, 3)
        entry['completed_at'] = _utc_now()
        print(f"\n[release-verify] END command: exit={entry['returncode']} "
              f"elapsed={entry['duration_seconds']}s", file=sys.stderr, flush=True)
    decoded = {name: bytes(data).decode('utf-8', errors='backslashreplace').replace('\r\n', '\n').replace('\r', '\n')
               for name, data in streams.items()}
    completed = subprocess.CompletedProcess(list(command), entry['returncode'], decoded['stdout'], decoded['stderr'])
    if completed.returncode and not echo_output:
        print(completed.stdout, end='', file=sys.stderr, flush=True)
    if completed.returncode:
        raise VerificationFailure(
            f"command exited {completed.returncode}: {rendered}\n"
            f"stdout:\n{completed.stdout or '<empty>'}\n"
            f"stderr:\n{completed.stderr or '<empty>'}"
        )
    return completed


def locked_versions(lock_path: Path) -> dict[str, str]:
    """Read exact package pins from the reviewed hash lock."""
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
        match = _PIN.match(raw.strip())
        if not match:
            continue
        name = _NORMALIZE_NAME.sub("-", match.group(1)).lower()
        version = match.group(2)
        if name in pins:
            raise VerificationFailure(
                f"duplicate lock entry for {name!r} at {lock_path}:{line_number}"
            )
        pins[name] = version
    if not pins:
        raise VerificationFailure(f"no exact pins found in {lock_path}")
    return pins


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def repository_state(repository: Path) -> dict[str, Any]:
    commit = run_command(
        ("git", "rev-parse", "HEAD"), cwd=repository
    ).stdout.strip()
    commit_timestamp = run_command(
        ("git", "show", "-s", "--format=%ct", "HEAD"), cwd=repository
    ).stdout.strip()
    status = run_command(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        cwd=repository,
    ).stdout.splitlines()
    return {
        "commit": commit,
        "commit_timestamp": int(commit_timestamp),
        "clean": not status,
        "changes": status,
    }


def _base_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    # Machine-readable subprocesses carry reviewed Unicode path cases. Do not
    # let a Windows legacy console code page turn valid JSON into a failure or
    # silently mojibake Git/build diagnostics.
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _summary(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    stderr_lines = [line for line in completed.stderr.splitlines() if line.strip()]
    return {
        "returncode": completed.returncode,
        "stdout_sha256": _text_sha256(completed.stdout),
        "stderr_sha256": _text_sha256(completed.stderr),
        "last_stdout_line": stdout_lines[-1] if stdout_lines else None,
        "last_stderr_line": stderr_lines[-1] if stderr_lines else None,
    }


def _venv_python(environment_root: Path) -> Path:
    if os.name == "nt":
        return environment_root / "Scripts" / "python.exe"
    return environment_root / "bin" / "python"


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as handle:
        handle.extractall(destination, filter="data")


def _normalize_gzip_archive(archive: Path, *, mtime: int) -> None:
    """Rewrite a generated gzip container with stable header metadata."""
    payload = gzip.decompress(archive.read_bytes())
    normalized = archive.with_name(f"{archive.name}.normalized")
    with normalized.open("wb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw,
            mtime=mtime,
        ) as encoded:
            encoded.write(payload)
    os.replace(normalized, archive)


def _normalize_sdist_archive(archive: Path, *, mtime: int) -> None:
    """Canonicalize tar metadata and gzip metadata without changing file bytes."""
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isfile() else None
            payload = extracted.read() if extracted is not None else None
            member.mtime = mtime
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.devmajor = 0
            member.devminor = 0
            member.pax_headers = {}
            entries.append((member, payload))

    tar_payload = io.BytesIO()
    with tarfile.open(fileobj=tar_payload, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member, payload in sorted(entries, key=lambda item: item[0].name):
            target.addfile(
                member,
                io.BytesIO(payload) if payload is not None else None,
            )

    archive.write_bytes(gzip.compress(tar_payload.getvalue(), compresslevel=9, mtime=mtime))


def _program_python_sources(source: Path) -> list[Path]:
    """Python program files, excluding intentionally malformed research data."""
    files = [source / "pipeline.py"]
    files.extend((source / "modules").rglob("*.py"))
    for relative in (
        "config",
        "examples",
        "validation",
        "validation/scripts",
        "validation/artifact_io",
        "validation/resources",
        "validation/conformance",
    ):
        files.extend((source / relative).glob("*.py"))
    return sorted(path for path in files if path.is_file())


def _wheel_metadata_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            name for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise VerificationFailure(
                f"expected one wheel METADATA member, found {metadata_names}"
            )
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    for line in metadata.splitlines():
        if line.startswith("Version: "):
            return line.partition(": ")[2]
    raise VerificationFailure("built wheel METADATA has no Version field")


def _sdist_metadata_version(sdist: Path) -> str:
    with tarfile.open(sdist, "r:gz") as archive:
        metadata_members = [
            member for member in archive.getmembers()
            if member.isfile() and member.name.count("/") == 1
            and member.name.endswith("/PKG-INFO")
        ]
        if len(metadata_members) != 1:
            raise VerificationFailure(
                f"expected one top-level sdist PKG-INFO member, found "
                f"{[member.name for member in metadata_members]}"
            )
        extracted = archive.extractfile(metadata_members[0])
        if extracted is None:
            raise VerificationFailure("could not read sdist PKG-INFO")
        metadata = extracted.read().decode("utf-8")
    for line in metadata.splitlines():
        if line.startswith("Version: "):
            return line.partition(": ")[2]
    raise VerificationFailure("built sdist PKG-INFO has no Version field")


class ReleaseVerifier:
    def __init__(self, repository: Path, log_directory: Path | None = None):
        self.repository = repository.resolve()
        self.log_directory = log_directory
        self.checks: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.first_failure: str | None = None

    def _failure(self, name: str, exc: Exception, started: float) -> None:
        self.first_failure = self.first_failure or name
        elapsed = round(time.monotonic() - started, 3)
        self.checks.append({"name": name, "status": "failed", "duration_seconds": elapsed,
                            "error": str(exc), "error_type": type(exc).__name__})
        print(f"[release-verify] END {name}: FAILED elapsed={elapsed}s: {exc}", file=sys.stderr, flush=True)

    def check(self, name: str, operation: Callable[[], Any]) -> Any | None:
        global _ACTIVE_STAGE
        if self.first_failure and name != "repository_clean_after":
            self.skip(name, f"blocked by {self.first_failure}")
            return None
        started = time.monotonic()
        previous_stage = _ACTIVE_STAGE
        _ACTIVE_STAGE = name
        print(f"[release-verify] START {name}", file=sys.stderr, flush=True)
        try:
            detail = operation()
        except Exception as exc:
            self._failure(name, exc, started)
            return None
        finally:
            _ACTIVE_STAGE = previous_stage
        public_detail = detail
        if isinstance(detail, dict):
            public_detail = {
                key: value for key, value in detail.items()
                if not str(key).startswith("_")
            }
        self.checks.append(
            {
                "name": name,
                "status": "passed",
                "duration_seconds": round(time.monotonic() - started, 3),
                "detail": public_detail,
            }
        )
        print(f"[release-verify] END {name}: PASSED elapsed={self.checks[-1]['duration_seconds']}s", file=sys.stderr, flush=True)
        return detail

    def skip(self, name: str, reason: str) -> None:
        # Keep the evidence format's existing status vocabulary; distinguish
        # blocked execution explicitly from an installed capability skip.
        blocked_by = self.first_failure
        self.checks.append({"name": name, "status": "skipped",
                            "reason": f"NOT_RUN: blocked by {blocked_by}" if blocked_by else reason,
                            "detail": {"execution": "NOT_RUN", "blocked_by": blocked_by}})
        print(f"[release-verify] NOT_RUN {name}: {self.checks[-1]['reason']}", file=sys.stderr, flush=True)

    def _repository_check(self, name: str) -> dict[str, Any] | None:
        captured = None

        def capture():
            nonlocal captured
            captured = repository_state(self.repository)
            if not captured["clean"]:
                raise VerificationFailure(f"candidate worktree is not clean: {captured['changes']}")
            return captured

        self.check(name, capture)
        if captured is not None:
            self.checks[-1]["detail"] = captured
        return captured

    def verify_versions(self) -> dict[str, str]:
        project = tomllib.loads(
            (self.repository / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        from modules.config import PROGRAM_VERSION

        cli = run_command(
            (sys.executable, "-m", "pipeline", "--version"),
            cwd=self.repository,
            env=_base_environment(),
        ).stdout.strip()
        versions = {
            "program": PROGRAM_VERSION,
            "pyproject": str(project["version"]),
            "installed_distribution": _installed_version("metrolith") or "missing",
            "cli": cli.removeprefix("Metrolith "),
        }
        if len(set(versions.values())) != 1:
            raise VerificationFailure(f"package version mismatch: {versions}")
        if platform.python_version() != VERIFICATION_PYTHON_VERSION:
            raise VerificationFailure(
                f"release verification requires CPython {VERIFICATION_PYTHON_VERSION}, "
                f"got {platform.python_version()}"
            )
        return versions

    def verify_locked_environment(self) -> dict[str, Any]:
        pins = locked_versions(self.repository / LOCK_FILE)
        installed = {name: _installed_version(name) for name in pins}
        mismatches = {
            name: {"expected": pins[name], "installed": installed[name]}
            for name in pins
            if installed[name] != pins[name]
        }
        if mismatches:
            raise VerificationFailure(f"verification lock mismatch: {mismatches}")
        checked = run_command(
            (sys.executable, "-m", "pip", "check"),
            cwd=self.repository,
            env=_base_environment(),
            echo_output=True,
        )
        return {"packages": installed, "pip_check": _summary(checked)}

    def verify_schema_identities(self) -> dict[str, int]:
        from validation.artifact_io.schema_store import (
            schema_declaration_problems,
            schema_identity_problems,
            schema_names,
        )

        declaration = schema_declaration_problems()
        identity = schema_identity_problems()
        problems = declaration + identity
        if problems:
            raise VerificationFailure("; ".join(str(problem) for problem in problems))
        return {
            "registered_names": len(schema_names()),
            "declaration_problems": len(declaration),
            "identity_collisions": len(identity),
        }

    def verify_schema_documents(self) -> dict[str, int]:
        from validation.artifact_io.schema_store import check_all_schemas, schema_names

        problems = check_all_schemas()
        if problems:
            raise VerificationFailure("; ".join(str(problem) for problem in problems))
        return {"schemas_checked": len(schema_names()), "problems": 0}

    def verify_standalone_contracts(self) -> dict[str, Any]:
        from modules.standalone_contracts import validate_contract_registry

        contracts = validate_contract_registry()
        return {"contract_count": len(contracts), "contracts": contracts}

    def run_doctor(self, temporary: Path) -> dict[str, Any]:
        completed = run_command(
            (
                sys.executable,
                "-m",
                "pipeline",
                "doctor",
                "--workspace",
                str(temporary / "doctor-workspace"),
            ),
            cwd=self.repository,
            env=_base_environment(),
            echo_output=True,
        )
        return _summary(completed)

    def run_full_suite(self) -> dict[str, Any]:
        completed = run_command(
            (
                sys.executable,
                "-m",
                "pytest",
                "-v",
                "-p",
                "no:cacheprovider",
            ),
            cwd=self.repository,
            env=_base_environment(),
            echo_output=True,
        )
        return _summary(completed)

    def run_focused_suite(self) -> dict[str, Any]:
        completed = run_command(
            (
                sys.executable,
                "-m",
                "pytest",
                "-v",
                "-p",
                "no:cacheprovider",
                *FOCUSED_HARDENING_TESTS,
            ),
            cwd=self.repository,
            env=_base_environment(),
            echo_output=True,
        )
        return {"files": list(FOCUSED_HARDENING_TESTS), **_summary(completed)}

    def verify_commit_delta(self) -> dict[str, Any]:
        committed = run_command(
            ("git", "show", "--format=", "--check", "--root", "HEAD"),
            cwd=self.repository,
        )
        worktree = run_command(
            ("git", "diff", "--check"),
            cwd=self.repository,
        )
        return {
            "committed_delta": _summary(committed),
            "clean_worktree_delta": _summary(worktree),
        }

    def verify_source_reconciliation(self, temporary: Path) -> dict[str, Any]:
        """Reconcile the committed file manifest with the archived build input."""
        listing = run_command(
            ("git", "ls-files", "-z"),
            cwd=self.repository,
        )
        tracked = sorted(item for item in listing.stdout.split("\0") if item)
        manifest = hashlib.sha256()
        for relative in tracked:
            manifest.update(relative.encode("utf-8"))
            manifest.update(b"\0")
            manifest.update(_sha256(self.repository / relative).encode("ascii"))
            manifest.update(b"\n")

        archive_path = temporary / "reconciliation-source.tar"
        run_command(
            ("git", "archive", "--format=tar", f"--output={archive_path}", "HEAD"),
            cwd=self.repository,
        )
        with tarfile.open(archive_path, "r:") as archive:
            archived = sorted(member.name for member in archive.getmembers() if member.isfile())
        missing = sorted(set(tracked) - set(archived))
        unexpected = sorted(set(archived) - set(tracked))
        if missing or unexpected:
            raise VerificationFailure(
                f"source archive reconciliation failed: missing={missing}, "
                f"unexpected={unexpected}"
            )
        return {
            "tracked_file_count": len(tracked),
            "archived_file_count": len(archived),
            "missing_count": len(missing),
            "unexpected_count": len(unexpected),
            "source_manifest_sha256": manifest.hexdigest(),
        }

    def run_compileall(self, temporary: Path) -> dict[str, Any]:
        archive_path = temporary / "compile-source.tar"
        source = temporary / "compile-source"
        source.mkdir()
        run_command(
            ("git", "archive", "--format=tar", f"--output={archive_path}", "HEAD"),
            cwd=self.repository,
        )
        _extract_tar(archive_path, source)
        program_sources = _program_python_sources(source)
        if not program_sources:
            raise VerificationFailure("compileall found no Python program sources")
        completed = run_command(
            (
                sys.executable,
                "-m",
                "compileall",
                "-q",
                *(str(path) for path in program_sources),
            ),
            cwd=temporary,
            env=_base_environment(),
            echo_output=True,
        )
        return {"source_count": len(program_sources), **_summary(completed)}

    def build_distributions(
        self, temporary: Path, *, commit_timestamp: int, version: str
    ) -> dict[str, Any]:
        snapshot_archive = temporary / "source.tar"
        snapshot = temporary / "source"
        distributions = temporary / "dist"
        extracted = temporary / "sdist-source"
        for directory in (snapshot, distributions, extracted):
            directory.mkdir()

        run_command(
            (
                "git",
                "archive",
                "--format=tar",
                f"--output={snapshot_archive}",
                "HEAD",
            ),
            cwd=self.repository,
        )
        _extract_tar(snapshot_archive, snapshot)

        build_environment = _base_environment()
        build_environment["SOURCE_DATE_EPOCH"] = str(commit_timestamp)
        sdist_build = run_command(
            (
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--verbose",
                "--sdist",
                "--outdir",
                str(distributions),
                str(snapshot),
            ),
            cwd=temporary,
            env=build_environment,
            echo_output=True,
        )
        sdists = list(distributions.glob(f"metrolith-{version}.tar.gz"))
        if len(sdists) != 1:
            raise VerificationFailure(f"expected one sdist, found {sdists}")
        _normalize_sdist_archive(sdists[0], mtime=commit_timestamp)
        if _sdist_metadata_version(sdists[0]) != version:
            raise VerificationFailure("built sdist version does not match Program version")
        _extract_tar(sdists[0], extracted)
        sdist_source = extracted / f"metrolith-{version}"
        wheel_build = run_command(
            (
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--verbose",
                "--wheel",
                "--outdir",
                str(distributions),
                str(sdist_source),
            ),
            cwd=temporary,
            env=build_environment,
            echo_output=True,
        )
        wheels = list(distributions.glob(f"metrolith-{version}-py3-none-any.whl"))
        if len(wheels) != 1:
            raise VerificationFailure(f"expected one wheel, found {wheels}")
        if _wheel_metadata_version(wheels[0]) != version:
            raise VerificationFailure("built wheel version does not match Program version")

        for kind, artifact in (("sdist", sdists[0]), ("wheel", wheels[0])):
            self.artifacts.append(
                {
                    "kind": kind,
                    "filename": artifact.name,
                    "sha256": _sha256(artifact),
                    "size_bytes": artifact.stat().st_size,
                }
            )
        return {
            "_sdist": str(sdists[0]),
            "_wheel": str(wheels[0]),
            "source_date_epoch": commit_timestamp,
            "sdist_build": _summary(sdist_build),
            "wheel_build": _summary(wheel_build),
        }

    def install_wheel(self, temporary: Path, wheel: Path) -> dict[str, Any]:
        environment_root = temporary / "installed-wheel"
        run_command(
            (sys.executable, "-m", "venv", str(environment_root)),
            cwd=temporary,
            env=_base_environment(),
        )
        python = _venv_python(environment_root)
        install_lock = run_command(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                str(self.repository / LOCK_FILE),
            ),
            cwd=temporary,
            env=_base_environment(),
            echo_output=True,
        )
        install_wheel = run_command(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(wheel),
            ),
            cwd=temporary,
            env=_base_environment(),
            echo_output=True,
        )
        pip_check = run_command(
            (str(python), "-m", "pip", "check"),
            cwd=temporary,
            env=_base_environment(),
            echo_output=True,
        )
        package_list = run_command(
            (str(python), "-m", "pip", "list", "--format=json"),
            cwd=temporary,
            env=_base_environment(),
        )
        return {
            "_python": str(python),
            "install_lock": _summary(install_lock),
            "install_wheel": _summary(install_wheel),
            "pip_check": _summary(pip_check),
            "packages": json.loads(package_list.stdout),
        }

    def smoke_installed_wheel(
        self, temporary: Path, python: Path, *, version: str
    ) -> dict[str, Any]:
        version_result = run_command(
            (str(python), "-I", "-m", "pipeline", "--version"),
            cwd=temporary,
            env=_base_environment(),
        )
        if version_result.stdout.strip() != f"Metrolith {version}":
            raise VerificationFailure(
                f"installed CLI version mismatch: {version_result.stdout.strip()!r}"
            )
        doctor = run_command(
            (
                str(python),
                "-I",
                "-m",
                "pipeline",
                "doctor",
                "--workspace",
                str(temporary / "installed-doctor-workspace"),
            ),
            cwd=temporary,
            env=_base_environment(),
            echo_output=True,
        )
        schema_probe = (
            "from validation.artifact_io.schema_store import check_all_schemas; "
            "problems=check_all_schemas(); "
            "print(len(problems)); "
            "raise SystemExit(bool(problems))"
        )
        schemas = run_command(
            (str(python), "-I", "-c", schema_probe),
            cwd=temporary,
            env=_base_environment(),
        )
        installation_probe = """
import importlib.metadata as metadata
import json
from pathlib import Path
import config
import modules.config as modules_config
import pipeline
import validation.artifact_io.schema_store as schema_store

prefix = Path(__import__('sys').prefix).resolve()
distribution = metadata.distribution('metrolith')
distribution_path = Path(distribution._path).resolve()
module_paths = {
    'config': str(Path(config.__file__).resolve()),
    'modules.config': str(Path(modules_config.__file__).resolve()),
    'pipeline': str(Path(pipeline.__file__).resolve()),
    'validation.artifact_io.schema_store': str(Path(schema_store.__file__).resolve()),
}
assert distribution_path.name.endswith('.dist-info'), distribution_path
assert distribution_path.is_relative_to(prefix), (distribution_path, prefix)
assert all(Path(path).is_relative_to(prefix) for path in module_paths.values()), module_paths
print(json.dumps({
    'distribution_version': distribution.version,
    'distribution_kind': 'dist-info',
    'all_modules_under_environment_prefix': True,
}, sort_keys=True))
"""
        installation = run_command(
            (str(python), "-I", "-c", installation_probe),
            cwd=temporary,
            env=_base_environment(),
        )
        return {
            "version": version_result.stdout.strip(),
            "doctor": _summary(doctor),
            "schema_problem_count": int(schemas.stdout.strip()),
            "installation": json.loads(installation.stdout),
        }

    def verify_installed_commands(self, temporary: Path, python: Path) -> dict[str, Any]:
        for command in CLI_COMMANDS:
            run_command(
                (str(python), "-I", "-m", "pipeline", command, "--help"),
                cwd=temporary,
                env=_base_environment(),
            )
        return {"count": len(CLI_COMMANDS), "commands": list(CLI_COMMANDS)}

    def run_installed_conformance(self, temporary: Path, python: Path) -> dict[str, Any]:
        completed = run_command(
            (
                str(python),
                "-I",
                "-m",
                "validation.conformance",
                "run",
                "--format",
                "json",
            ),
            cwd=temporary,
            env=_base_environment(),
        )
        result = json.loads(completed.stdout)
        return {
            "corpus_version": result["corpus_version"],
            "case_count": result["case_count"],
            "passed_count": result["passed_count"],
            "skipped_count": result["skipped_count"],
            "failed_count": result["failed_count"],
            "stdout_sha256": _text_sha256(completed.stdout),
        }

    def run(self) -> dict[str, Any]:
        global _ACTIVE_COMMAND_LOG, _ACTIVE_REPOSITORY, _ACTIVE_LOG_DIRECTORY
        previous = (_ACTIVE_COMMAND_LOG, _ACTIVE_REPOSITORY, _ACTIVE_LOG_DIRECTORY)
        self.commands.clear()
        self.checks.clear()
        self.artifacts.clear()
        self.first_failure = None
        started_at, started = _utc_now(), time.monotonic()
        before = after = None
        try:
            directory = self.log_directory or Path(tempfile.mkdtemp(prefix="metrolith-release-logs-"))
            self.log_directory = _external_output_path(str(directory), self.repository)
            self.log_directory.mkdir(parents=True, exist_ok=True)
            _ACTIVE_COMMAND_LOG, _ACTIVE_REPOSITORY, _ACTIVE_LOG_DIRECTORY = (
                self.commands, self.repository, self.log_directory,
            )
            try:
                before = self._repository_check("repository_clean_before")
                if self.first_failure is None:
                    self._run_stages(before)
            except Exception as exc:
                # Temporary-workspace setup/cleanup must not erase a gate's failure.
                self._failure("verification_setup_or_cleanup", exc, started)
            finally:
                recorded = {item["name"] for item in self.checks}
                for name in REQUIRED_GATES[:-1]:
                    if name not in recorded:
                        self.skip(name, "qualification did not reach this gate")
                after = self._repository_check("repository_clean_after")
            return self._evidence(started_at, started, before, after)
        finally:
            _ACTIVE_COMMAND_LOG, _ACTIVE_REPOSITORY, _ACTIVE_LOG_DIRECTORY = previous

    def _run_stages(self, before: Mapping[str, Any]) -> None:
        versions = self.check("version_consistency", self.verify_versions)
        self.check("locked_environment", self.verify_locked_environment)
        self.check("schema_identity", self.verify_schema_identities)
        self.check("schema_validation", self.verify_schema_documents)
        self.check("standalone_contracts", self.verify_standalone_contracts)
        self.check("commit_delta", self.verify_commit_delta)

        with tempfile.TemporaryDirectory(prefix="metrolith-release-verify-") as directory:
            temporary = Path(directory)
            self.check("doctor", lambda: self.run_doctor(temporary))
            self.check(
                "source_reconciliation",
                lambda: self.verify_source_reconciliation(temporary),
            )
            self.check("compileall", lambda: self.run_compileall(temporary))
            self.check("focused_hardening_tests", self.run_focused_suite)
            self.check("full_test_suite", self.run_full_suite)

            package = None
            if versions is not None:
                package = self.check(
                    "packaging",
                    lambda: self.build_distributions(
                        temporary,
                        commit_timestamp=before["commit_timestamp"],
                        version=versions["program"],
                    ),
                )
            else:
                self.skip("packaging", "version consistency failed")

            installed = None
            if package is not None:
                installed = self.check(
                    "isolated_wheel_install",
                    lambda: self.install_wheel(temporary, Path(package["_wheel"])),
                )
            else:
                self.skip("isolated_wheel_install", "packaging did not pass")

            if installed is not None and versions is not None:
                python = Path(installed["_python"])
                self.check(
                    "installed_wheel_smoke",
                    lambda: self.smoke_installed_wheel(
                        temporary, python, version=versions["program"]
                    ),
                )
                self.check(
                    "cli_command_availability",
                    lambda: self.verify_installed_commands(temporary, python),
                )
                self.check(
                    "installed_conformance",
                    lambda: self.run_installed_conformance(temporary, python),
                )
            else:
                reason = "isolated wheel installation did not pass"
                self.skip("installed_wheel_smoke", reason)
                self.skip("cli_command_availability", reason)
                self.skip("installed_conformance", reason)


    def _evidence(
        self,
        started_at: str,
        started: float,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        status = (
            "passed"
            if self.checks and all(item["status"] == "passed" for item in self.checks)
            else "failed"
        )
        pins: dict[str, str] = {}
        try:
            pins = locked_versions(self.repository / LOCK_FILE)
        except Exception:
            pass
        try:
            git_version = run_command(("git", "--version"), cwd=self.repository).stdout.strip()
        except Exception as exc:
            # Supplementary metadata must not mask the original gate failure.
            self._failure("git_version_metadata", exc, time.monotonic())
            git_version = None
            status = "failed"
        return {
            "format": EVIDENCE_FORMAT,
            "format_version": EVIDENCE_FORMAT_VERSION,
            "status": status,
            "first_failure": self.first_failure,
            "log_directory": str(self.log_directory),
            "started_at": started_at,
            "completed_at": _utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "repository": {
                "commit": before.get("commit") if before else None,
                "clean_before": before.get("clean") if before else None,
                "clean_after": after.get("clean") if after else None,
            },
            "environment": {
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "git_version": git_version,
                "locked_packages": pins,
            },
            "commands": list(self.commands),
            "checks": self.checks,
            "artifacts": self.artifacts,
            "deferred_validations": list(DEFERRED_VALIDATIONS),
        }


def _external_output_path(value: str | None, repository: Path) -> Path | None:
    if value is None:
        return None
    output = Path(value).expanduser().resolve()
    if output.is_relative_to(repository.resolve()):
        raise VerificationFailure(
            "--output must be outside the repository so verification cannot dirty the candidate"
        )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify one clean Metrolith release candidate without tagging, pushing, "
            "publishing, or writing inside the repository."
        )
    )
    parser.add_argument(
        "--output",
        help="Write JSON evidence outside the repository instead of stdout.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    repository = REPOSITORY_ROOT
    try:
        output = _external_output_path(args.output, repository)
    except VerificationFailure as exc:
        print(f"release verification refused: {exc}", file=sys.stderr)
        return 2

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
    log_directory = Path(tempfile.mkdtemp(
        prefix=f"{output.stem}-logs-" if output else "metrolith-release-logs-",
        dir=output.parent if output else None,
    ))
    evidence = ReleaseVerifier(repository, log_directory).run()
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(serialized, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8", newline="\n")
        print(f"[release-verify] evidence: {output}", file=sys.stderr)
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
