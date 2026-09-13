"""Drivers for the out-of-process independent entity references.

Each reference runs under its own pinned toolchain and returns JSON. This module
only launches them and reads their output; the counting happens entirely inside
the reference, in the reference's own language and parser.

Availability is always reported, never assumed: a missing toolchain yields a
`ReferenceUnavailable` with a reason, and the study records an explicit
`not_evaluable` rather than omitting the comparison.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

from validation.differential.reference import environment

_HERE = Path(__file__).resolve().parent
_PYTHON_SCRIPT = _HERE / "python" / "reference_entity_count.py"
_NODE_SCRIPT = _HERE / "node" / "reference_entity_count.js"
_GO_SOURCE = _HERE / "gosrc" / "reference_entity_count.go"

_TIMEOUT_SECONDS = 900


class ReferenceUnavailable(RuntimeError):
    """The reference toolchain is not provisioned. Never silently degraded."""


class ReferenceExecutionError(RuntimeError):
    """The reference ran and failed. Surfaced, never swallowed."""


def _write_listing(directory: Path, files: Sequence[Path]) -> Path:
    listing = directory / "files.txt"
    listing.write_text(
        "\n".join(str(Path(item).resolve()) for item in files) + "\n",
        encoding="utf-8", newline="\n",
    )
    return listing


def _run(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        env={**os.environ, "JAVA_TOOL_OPTIONS": ""},
    )
    if completed.returncode != 0:
        raise ReferenceExecutionError(
            f"{label} exited {completed.returncode}: "
            f"{(completed.stderr or completed.stdout).strip()[:600]}"
        )
    text = completed.stdout.strip()
    if not text:
        raise ReferenceExecutionError(f"{label} produced no output")
    try:
        return json.loads(text.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise ReferenceExecutionError(
            f"{label} emitted unreadable output: {exc}; {text[:300]!r}"
        ) from exc


def _empty(version: str | None) -> dict[str, Any]:
    return {
        "files": [],
        "totals": {"types": 0, "methods": 0, "files_parsed": 0, "files_failed": 0},
        "reference_version": version,
    }


def python_entities(files: Sequence[Path]) -> dict[str, Any]:
    """parso, under the dedicated reference interpreter."""
    tool = environment.parso_tool()
    interpreter = environment.python_reference_tool()
    if not tool.available or not interpreter.available:
        raise ReferenceUnavailable(tool.reason or interpreter.reason or "unavailable")
    if not files:
        return _empty(tool.version)

    with tempfile.TemporaryDirectory(prefix="archlens_pyref_") as directory:
        listing = _write_listing(Path(directory), files)
        payload = _run(
            [str(interpreter.executable), str(_PYTHON_SCRIPT), str(listing)],
            "the parso Python reference",
        )
    payload["reference_version"] = f"parso {tool.version}"
    return payload


def javascript_entities(files: Sequence[Path]) -> dict[str, Any]:
    """The TypeScript compiler API, for both JavaScript and TypeScript."""
    tool = environment.typescript_tool()
    node = environment.node_tool()
    if not tool.available or not node.available:
        raise ReferenceUnavailable(tool.reason or node.reason or "unavailable")
    if not files:
        return _empty(tool.version)

    with tempfile.TemporaryDirectory(prefix="archlens_tsref_") as directory:
        listing = _write_listing(Path(directory), files)
        payload = _run(
            [
                str(node.executable), str(_NODE_SCRIPT),
                str(environment.node_modules_root()).replace("\\", "/"),
                str(listing),
            ],
            "the TypeScript reference",
        )
    adapter_version = payload.get("adapter_version") or "unversioned"
    payload["reference_version"] = (
        f"typescript {tool.version}; adapter {adapter_version}"
    )
    return payload


#: TypeScript and JavaScript share one reference mechanism but are reported
#: separately, because their definition mappings differ in what constructs exist.
typescript_entities = javascript_entities


def go_entities(files: Sequence[Path]) -> dict[str, Any]:
    """go/parser + go/ast, compiled on demand."""
    tool = environment.go_tool()
    if not tool.available:
        raise ReferenceUnavailable(tool.reason or "the Go toolchain is unavailable")
    if not files:
        return _empty(tool.version)

    with tempfile.TemporaryDirectory(prefix="archlens_goref_") as directory:
        workspace = Path(directory)
        # A throwaway module keeps the build hermetic and offline: the
        # reference imports only the standard library, so nothing is fetched.
        (workspace / "go.mod").write_text(
            "module archlensdiffval\n\ngo 1.23\n", encoding="utf-8", newline="\n"
        )
        (workspace / "main.go").write_text(
            _GO_SOURCE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
        listing = _write_listing(workspace, files)
        build_environment = {
            **os.environ,
            "GOFLAGS": "-mod=mod",
            "GOPROXY": "off",
            "GOCACHE": str(workspace / "gocache"),
            "GOPATH": str(workspace / "gopath"),
        }
        binary = workspace / "reference.exe"
        build = subprocess.run(
            [str(tool.executable), "build", "-o", str(binary), "."],
            cwd=str(workspace), capture_output=True, text=True,
            timeout=_TIMEOUT_SECONDS, env=build_environment,
        )
        if build.returncode != 0:
            raise ReferenceExecutionError(
                f"the Go reference failed to build: {build.stderr.strip()[:600]}"
            )
        payload = _run([str(binary), str(listing)], "the Go reference")
    payload["reference_version"] = f"go {tool.version}"
    return payload


#: language -> (driver, reference identifier)
DRIVERS = {
    "Python": (python_entities, "parso.grammar"),
    "JavaScript": (javascript_entities, "typescript.compiler_api"),
    "TypeScript": (typescript_entities, "typescript.compiler_api"),
    "Go": (go_entities, "go.parser_ast"),
}
