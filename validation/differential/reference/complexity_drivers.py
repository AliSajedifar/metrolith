"""One runner abstraction for the five complexity reference adapters.

Two harness rules live here, because both were violated once already and the
violations were silent rather than loud:

**An empty scope is refused, never reported.** A subject that resolves to zero
selected files raises :class:`ScopeRefused`. Pointing the Layer C2 runner at an
ArchLens *workspace* instead of the source checkout produced a clean all-zero
comparison table across five subjects, which reads exactly like "no
disagreements". A zero-observation result and a broken input must never look
alike.

**Paths are passed by listing file, never on the command line.** A real subject
with a thousand source files exceeds the Windows command-line limit, and the
failure arrives as an opaque ``WinError 206`` from deep inside ``subprocess``.
Every adapter accepts ``--list FILE`` holding one UTF-8 path per line.

**A compiled adapter is never reused because it exists.** The Go and Java
adapters are compiled artifacts, and the smoke run reused a Go binary built
before the ``--list`` change: it failed against the *old* contract while looking
like a current run. Existence is not freshness. :func:`ensure_go_adapter` and
:func:`ensure_java_adapter` rebuild unless a stamp records the exact source
digest, the exact toolchain fingerprint **and** the exact digest of the built
artifact still on disk -- so an edited source, an upgraded toolchain, a deleted
binary and a replaced binary all force a rebuild, and none of them can be
mistaken for a fresh build.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

REFERENCE_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference"))
PYREF = REFERENCE_ROOT / "pyref/Scripts/python.exe"
NODE = REFERENCE_ROOT / "node-v22.20.0-win-x64/node.exe"
NODE_MODULES = REFERENCE_ROOT / "nodepkgs/node_modules"
GO = REFERENCE_ROOT / "go/bin/go.exe"
JDK = Path(os.environ.get("METROLITH_REFERENCE_JDK", ".metrolith-reference/jdk/bin"))

ADAPTERS = Path(__file__).resolve().parent
PYTHON_ADAPTER = ADAPTERS / "python/reference_complexity.py"
GO_ADAPTER = ADAPTERS / "gosrc/reference_complexity.go"
JAVA_ADAPTER = ADAPTERS / "java/ReferenceComplexity.java"
NODE_ADAPTER = ADAPTERS / "node/reference_complexity.js"

#: External cyclomatic references. Not primary adapters, not ground truth.
LIZARD_DRIVER = ADAPTERS / "python/reference_lizard.py"
ESLINT_DRIVER = ADAPTERS / "node/reference_eslint_complexity.js"

#: Written beside every compiled adapter. Its absence, or any mismatch inside
#: it, forces a rebuild.
BUILD_STAMP = "build_stamp.json"

_BUILD_TIMEOUT_SECONDS = 900

SOURCE_EXTENSIONS = {
    "Go": {".go"},
    "Java": {".java"},
    "JavaScript": {".js", ".jsx", ".mjs", ".cjs"},
    "TypeScript": {".ts", ".tsx", ".mts", ".cts"},
    "Python": {".py"},
}


class ScopeRefused(RuntimeError):
    """A subject resolved to no selected files. Typed, so it cannot be a zero."""


class AdapterUnavailable(RuntimeError):
    """A reference toolchain is not provisioned. A capability statement."""


@dataclass(frozen=True)
class ScopeEvidence:
    """What the harness resolved, recorded BEFORE any adapter is invoked."""

    language: str
    subject_root: Path
    files: tuple[str, ...]
    extension_summary: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "subject_root": str(self.subject_root),
            "selected_file_count": len(self.files),
            "extension_summary": dict(sorted(self.extension_summary.items())),
        }


def resolve_scope(
    language: str, subject_root: Path, files: Sequence[str]
) -> ScopeEvidence:
    """Record the resolved scope, or refuse it.

    ``files`` are subject-relative paths chosen by ArchLens (Track A: the file
    set is given, so selection cannot contribute to a disagreement).
    """
    root = Path(subject_root)
    if not root.is_dir():
        raise ScopeRefused(
            f"{language}: subject root does not exist or is not a directory: {root}"
        )

    summary: dict[str, int] = {}
    for relative in files:
        summary[Path(relative).suffix.lower()] = (
            summary.get(Path(relative).suffix.lower(), 0) + 1
        )

    if not files:
        raise ScopeRefused(
            f"{language}: subject {root} resolved to ZERO selected files. This is "
            f"an input/harness error, not an empty comparison. Check that the "
            f"path is a SOURCE CHECKOUT and not an ArchLens workspace directory "
            f"(a workspace holds cache/, runs/ and temp/, and contains no "
            f"measurable source)."
        )

    expected = SOURCE_EXTENSIONS.get(language, set())
    unexpected = {
        suffix: count for suffix, count in summary.items() if suffix not in expected
    }
    if unexpected:
        raise ScopeRefused(
            f"{language}: selected files carry unexpected extensions {unexpected}; "
            f"the scope does not match the language under comparison"
        )

    return ScopeEvidence(language, root, tuple(files), summary)


def write_listing(paths: Sequence[str], destination: Path) -> Path:
    """One UTF-8 path per line. No shell quoting is involved at any point."""
    destination.write_text(
        "\n".join(str(path) for path in paths) + "\n", encoding="utf-8", newline="\n"
    )
    return destination


# ---------------------------------------------------------------------------
# Compiled adapters: freshness by content, never by existence.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildEvidence:
    """Why a compiled adapter is trusted to be the current one."""

    artifact: Path
    source_digest: str
    toolchain_fingerprint: str
    artifact_digest: str
    rebuilt: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": str(self.artifact),
            "source_digest": self.source_digest,
            "toolchain_fingerprint": self.toolchain_fingerprint,
            "artifact_digest": self.artifact_digest,
            "rebuilt": self.rebuilt,
            "freshness_reason": self.reason,
        }


def _digest_bytes(*chunks: bytes) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
        digest.update(b"\x1f")
    return digest.hexdigest()


def _digest_sources(sources: Sequence[Path]) -> str:
    """Content digest of the adapter sources, name-qualified and ordered."""
    chunks: list[bytes] = []
    for source in sources:
        chunks.append(source.name.encode("utf-8"))
        chunks.append(source.read_bytes())
    return _digest_bytes(*chunks)


def _digest_tree(root: Path) -> str:
    """Content digest of a built directory, so a replaced class file shows."""
    chunks: list[bytes] = []
    for item in sorted(root.rglob("*")):
        if item.is_file():
            chunks.append(item.relative_to(root).as_posix().encode("utf-8"))
            chunks.append(item.read_bytes())
    return _digest_bytes(*chunks)


def _toolchain_fingerprint(command: Sequence[str]) -> str:
    """The toolchain's own version string, so an upgrade forces a rebuild."""
    completed = subprocess.run(
        [str(part) for part in command], capture_output=True, text=True,
        timeout=_BUILD_TIMEOUT_SECONDS,
    )
    # javac writes its version to stdout on 17 and to stderr on older releases;
    # both are folded in rather than guessed at.
    return " ".join(
        f"{completed.stdout} {completed.stderr}".split()
    ) or f"exit {completed.returncode}"


def _stale_reason(
    stamp_path: Path, source_digest: str, toolchain: str, artifact: Path,
) -> str | None:
    """Why the existing build cannot be reused, or None when it can."""
    if not artifact.exists():
        return "no built artifact on disk"
    if not stamp_path.is_file():
        return "no build stamp: existence alone is never freshness"
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"unreadable build stamp ({type(error).__name__})"
    if stamp.get("source_digest") != source_digest:
        return "adapter source changed since the recorded build"
    if stamp.get("toolchain_fingerprint") != toolchain:
        return "reference toolchain changed since the recorded build"
    current = (
        _digest_tree(artifact) if artifact.is_dir()
        else _digest_bytes(artifact.read_bytes())
    )
    if stamp.get("artifact_digest") != current:
        return "built artifact on disk is not the one that was recorded"
    return None


def _write_stamp(
    stamp_path: Path, source_digest: str, toolchain: str, artifact: Path,
) -> str:
    artifact_digest = (
        _digest_tree(artifact) if artifact.is_dir()
        else _digest_bytes(artifact.read_bytes())
    )
    stamp_path.write_text(
        json.dumps(
            {
                "source_digest": source_digest,
                "toolchain_fingerprint": toolchain,
                "artifact_digest": artifact_digest,
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return artifact_digest


def ensure_go_adapter(build_root: Path) -> BuildEvidence:
    """Compile the Go reference adapter, unless the exact build is already there.

    The throwaway module keeps the build hermetic and offline: the adapter
    imports only the standard library, so `GOPROXY=off` cannot break it and
    nothing is fetched.
    """
    if not GO.is_file():
        raise AdapterUnavailable(f"the Go toolchain is not provisioned at {GO}")

    workspace = Path(build_root) / "go_reference_complexity"
    workspace.mkdir(parents=True, exist_ok=True)
    binary = workspace / "reference_complexity.exe"
    stamp_path = workspace / BUILD_STAMP

    source_digest = _digest_sources([GO_ADAPTER])
    toolchain = _toolchain_fingerprint([GO, "version"])
    reason = _stale_reason(stamp_path, source_digest, toolchain, binary)
    if reason is None:
        return BuildEvidence(
            binary, source_digest, toolchain,
            json.loads(stamp_path.read_text(encoding="utf-8"))["artifact_digest"],
            False, "content and toolchain match the recorded build",
        )

    (workspace / "go.mod").write_text(
        "module archlensdiffvalcomplexity\n\ngo 1.23\n",
        encoding="utf-8", newline="\n",
    )
    (workspace / "main.go").write_bytes(GO_ADAPTER.read_bytes())
    build = subprocess.run(
        [str(GO), "build", "-o", str(binary), "."],
        cwd=str(workspace), capture_output=True, text=True,
        timeout=_BUILD_TIMEOUT_SECONDS,
        env={
            **os.environ,
            "GOFLAGS": "-mod=mod",
            "GOPROXY": "off",
            "GOCACHE": str(workspace / "gocache"),
            "GOPATH": str(workspace / "gopath"),
        },
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"the Go reference adapter failed to build: {build.stderr.strip()[:800]}"
        )

    artifact_digest = _write_stamp(stamp_path, source_digest, toolchain, binary)
    return BuildEvidence(
        binary, source_digest, toolchain, artifact_digest, True, reason,
    )


def ensure_java_adapter(build_root: Path) -> BuildEvidence:
    """Compile the Java reference adapter, unless the exact build is already there."""
    javac = JDK / "javac.exe"
    if not javac.is_file():
        raise AdapterUnavailable(f"the pinned JDK is not provisioned at {JDK}")

    workspace = Path(build_root) / "java_reference_complexity"
    classes = workspace / "classes"
    workspace.mkdir(parents=True, exist_ok=True)
    stamp_path = workspace / BUILD_STAMP

    source_digest = _digest_sources([JAVA_ADAPTER])
    toolchain = _toolchain_fingerprint([javac, "-version"])
    reason = _stale_reason(stamp_path, source_digest, toolchain, classes)
    if reason is None:
        return BuildEvidence(
            classes, source_digest, toolchain,
            json.loads(stamp_path.read_text(encoding="utf-8"))["artifact_digest"],
            False, "content and toolchain match the recorded build",
        )

    # A stale class file must not survive a rebuild and be loaded beside the
    # new ones, so the output directory starts empty.
    if classes.exists():
        for item in sorted(classes.rglob("*"), reverse=True):
            item.unlink() if item.is_file() else item.rmdir()
    classes.mkdir(parents=True, exist_ok=True)
    compile_result = subprocess.run(
        [str(javac), "-d", str(classes), str(JAVA_ADAPTER)],
        capture_output=True, text=True, timeout=_BUILD_TIMEOUT_SECONDS,
        env={**os.environ, "JAVA_TOOL_OPTIONS": ""},
    )
    if compile_result.returncode != 0:
        raise RuntimeError(
            "the Java reference adapter failed to compile: "
            f"{(compile_result.stderr or compile_result.stdout).strip()[:800]}"
        )

    artifact_digest = _write_stamp(stamp_path, source_digest, toolchain, classes)
    return BuildEvidence(
        classes, source_digest, toolchain, artifact_digest, True, reason,
    )


def _run(command: Sequence[str], **kwargs) -> dict[str, Any]:
    kwargs.setdefault("timeout", 3600)
    completed = subprocess.run(
        [str(part) for part in command], capture_output=True, text=True,
        encoding="utf-8", errors="replace", **kwargs
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"reference adapter failed ({completed.returncode}): "
            f"{completed.stderr[-2000:]}"
        )
    return json.loads(completed.stdout)


def _listing_for(scope: ScopeEvidence, work_directory: Path, label: str) -> Path:
    absolute = [str(scope.subject_root / relative) for relative in scope.files]
    return write_listing(
        absolute, Path(work_directory) / f"{label}_{scope.language}.txt"
    )


def _relative_to_subject(scope: ScopeEvidence, path: str) -> str:
    return (
        Path(path).resolve().relative_to(scope.subject_root.resolve()).as_posix()
    )


def run_adapter(
    scope: ScopeEvidence, work_directory: Path, *, build_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Invoke the primary adapter for one language over a resolved scope.

    The compiled adapters are built through :func:`ensure_go_adapter` /
    :func:`ensure_java_adapter` rather than taken as a path from the caller:
    a caller that can hand in a binary can hand in a stale one, which is exactly
    the failure this signature removes.
    """
    listing = _listing_for(scope, work_directory, "listing")
    root = Path(build_root) if build_root is not None else Path(work_directory)
    language = scope.language

    if language == "Python":
        if not PYREF.is_file():
            raise AdapterUnavailable("reference Python interpreter not provisioned")
        payload = _run([PYREF, PYTHON_ADAPTER, "--list", listing])
    elif language == "Go":
        payload = _run([ensure_go_adapter(root).artifact, "--list", listing])
    elif language == "Java":
        payload = _run([JDK / "java.exe", "-cp", ensure_java_adapter(root).artifact,
                        "ReferenceComplexity", listing])
    elif language in {"JavaScript", "TypeScript"}:
        if not NODE.is_file():
            raise AdapterUnavailable("reference Node runtime not provisioned")
        payload = _run(
            [NODE, NODE_ADAPTER, "--list", listing],
            env=dict(os.environ, NODE_PATH=str(NODE_MODULES)),
        )
    else:
        raise AdapterUnavailable(f"no primary adapter for {language}")

    records: list[dict[str, Any]] = []
    for entry in payload["files"]:
        relative = _relative_to_subject(scope, entry["path"])
        # `callables` is always a list; the Go adapter emits [] rather than null
        # since 2.1.0, so no consumer needs to special-case an empty result.
        for record in entry["callables"]:
            records.append(dict(record, relative_path=relative))
    return records


# ---------------------------------------------------------------------------
# External cyclomatic references. A second OPINION, never ground truth.
# ---------------------------------------------------------------------------

#: Which external tool triangulates each language, and the one substitution.
EXTERNAL_CC_TOOL = {
    "Go": "lizard",
    "Java": "lizard",
    "JavaScript": "lizard",
    "Python": "lizard",
    # Lizard reports overlapping, wrong spans for `.ts`; the pre-authorized
    # per-language substitution applies, and only here.
    "TypeScript": "eslint",
}


@dataclass(frozen=True)
class ExternalObservation:
    """One external tool's reading of one file, refusal included."""

    tool: str
    tool_version: str
    unreadable_files: tuple[dict[str, str], ...]
    records: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "unreadable_file_count": len(self.unreadable_files),
            "unreadable_files": [dict(item) for item in self.unreadable_files[:20]],
            "record_count": len(self.records),
        }


def run_lizard(scope: ScopeEvidence, work_directory: Path) -> ExternalObservation:
    """Lizard, through a wrapper that cannot turn an unreadable file into zero."""
    if not PYREF.is_file():
        raise AdapterUnavailable("reference Python interpreter not provisioned")
    listing = _listing_for(scope, work_directory, "lizard_listing")
    payload = _run([PYREF, LIZARD_DRIVER, "--list", listing])

    records: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for entry in payload["files"]:
        relative = _relative_to_subject(scope, entry["path"])
        if not entry.get("read_by_lizard"):
            unreadable.append({"relative_path": relative, "reason": entry["reason"]})
            continue
        for record in entry["callables"]:
            records.append(dict(record, relative_path=relative))
    return ExternalObservation(
        "lizard", str(payload["lizard_version"]),
        tuple(unreadable), tuple(records),
    )


def run_eslint(scope: ScopeEvidence, work_directory: Path) -> ExternalObservation:
    """ESLint core `complexity` at threshold 0, so every function is reported."""
    if not NODE.is_file():
        raise AdapterUnavailable("reference Node runtime not provisioned")
    if not (NODE_MODULES / "eslint").is_dir():
        raise AdapterUnavailable("ESLint is not provisioned in the reference tree")
    listing = _listing_for(scope, work_directory, "eslint_listing")
    payload = _run(
        [NODE, ESLINT_DRIVER, "--list", listing],
        env=dict(os.environ, NODE_PATH=str(NODE_MODULES)),
    )

    if payload.get("unmatched_results"):
        raise RuntimeError(
            "ESLint produced results for paths that were never selected: "
            f"{payload['unmatched_results'][:5]}; the path mapping is wrong and "
            f"every observation from this run is suspect"
        )

    records: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for entry in payload["files"]:
        relative = _relative_to_subject(scope, entry["path"])
        if not entry.get("linted"):
            unreadable.append({"relative_path": relative, "reason": entry["reason"]})
            continue
        for record in entry["callables"]:
            records.append(dict(record, relative_path=relative))
    return ExternalObservation(
        "eslint", str(payload["eslint_version"]),
        tuple(unreadable), tuple(records),
    )


def run_external_cc(
    scope: ScopeEvidence, work_directory: Path,
) -> ExternalObservation:
    """The reviewed external cyclomatic reference for this language."""
    tool = EXTERNAL_CC_TOOL.get(scope.language)
    if tool == "lizard":
        return run_lizard(scope, work_directory)
    if tool == "eslint":
        return run_eslint(scope, work_directory)
    raise AdapterUnavailable(
        f"no reviewed external cyclomatic reference for {scope.language}"
    )
