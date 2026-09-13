"""The Differential Validation reference environment: pinned, separate, recorded.

**Separate from ArchLens on purpose.** None of these tools is an ArchLens
runtime dependency, and the ArchLens acceptance environment
(a separate environment) is never modified. They exist only to provide
independent reference implementations for the validation study, and they live
in their own root so a broken reference can never affect a measurement run.

**Pinned, never "latest".** Every version below is exact. A reference tool whose
version drifts turns a reproducible study into an anecdote.

Provisioning is recorded in `REFERENCE_ENVIRONMENT` and emitted into every study
document, so a result can always be traced to the exact tools that produced it.
Override the root with ``ARCHLENS_DIFFVAL_REFS`` when the environment lives
elsewhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Root of the provisioned reference environment.
DEFAULT_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference"))

#: Exact pins. Changing any of these is a study-affecting change.
PINS = {
    "node": "22.20.0",
    "typescript": "5.6.3",
    "go": "1.23.12",
    "parso": "0.8.7",
    "python_reference_interpreter": "3.13.9",
}

#: Where each pinned tool came from. Recorded so the environment can be rebuilt.
PROVISIONING_SOURCES = {
    "node": "https://nodejs.org/dist/v22.20.0/node-v22.20.0-win-x64.zip",
    "typescript": "npm registry: typescript@5.6.3",
    "go": "https://go.dev/dl/go1.23.12.windows-amd64.zip",
    "parso": "PyPI: parso==0.8.7, installed into a dedicated venv",
    "python_reference_interpreter": (
        "venv created from the ArchLens acceptance interpreter; the acceptance "
        "environment itself is not modified and parso is never installed into it"
    ),
}


@dataclass(frozen=True)
class Tool:
    name: str
    executable: Path | None
    version: str | None
    available: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executable": str(self.executable) if self.executable else None,
            "version": self.version,
            "pinned_version": PINS.get(self.name),
            "available": self.available,
            "unavailable_reason": self.reason,
            "provisioning_source": PROVISIONING_SOURCES.get(self.name),
        }


def _root() -> Path:
    return Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", str(DEFAULT_ROOT)))


def node_executable() -> Path | None:
    candidate = _root() / f"node-v{PINS['node']}-win-x64" / "node.exe"
    if candidate.is_file():
        return candidate
    found = shutil.which("node")
    return Path(found) if found else None


def go_executable() -> Path | None:
    candidate = _root() / "go" / "bin" / "go.exe"
    if candidate.is_file():
        return candidate
    found = shutil.which("go")
    return Path(found) if found else None


def python_reference_executable() -> Path | None:
    candidate = _root() / "pyref" / "Scripts" / "python.exe"
    return candidate if candidate.is_file() else None


def node_modules_root() -> Path:
    return _root() / "nodepkgs"


def _probe(name: str, executable: Path | None, arguments: list[str],
           extract) -> Tool:
    if executable is None:
        return Tool(
            name, None, None, False,
            f"{name} is not provisioned; expected under {_root()} "
            f"(see validation/differential/reference/environment.py)",
        )
    try:
        completed = subprocess.run(
            [str(executable), *arguments], capture_output=True, text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Tool(name, executable, None, False,
                    f"{name} could not be executed: {type(exc).__name__}: {exc}")
    raw = (completed.stdout or completed.stderr or "").strip()
    version = extract(raw)
    if version is None:
        return Tool(name, executable, None, False,
                    f"unrecognized {name} version output: {raw[:120]!r}")
    expected = PINS.get(name)
    if expected and version != expected:
        return Tool(
            name, executable, version, False,
            f"{name} {version} is provisioned but the study pins {expected}; "
            f"an unpinned reference makes results non-reproducible",
        )
    return Tool(name, executable, version, True, None)


def node_tool() -> Tool:
    return _probe(
        "node", node_executable(), ["--version"],
        lambda raw: raw.lstrip("v").split()[0] if raw else None,
    )


def go_tool() -> Tool:
    def extract(raw: str) -> str | None:
        parts = raw.split()
        for part in parts:
            if part.startswith("go1"):
                return part[2:]
        return None

    return _probe("go", go_executable(), ["version"], extract)


def python_reference_tool() -> Tool:
    return _probe(
        "python_reference_interpreter", python_reference_executable(),
        ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        lambda raw: raw.strip() or None,
    )


def parso_tool() -> Tool:
    executable = python_reference_executable()
    if executable is None:
        return Tool("parso", None, None, False,
                    f"the reference interpreter is not provisioned under {_root()}")
    return _probe(
        "parso", executable,
        ["-c", "import parso; print(parso.__version__)"],
        lambda raw: raw.strip() or None,
    )


def typescript_tool() -> Tool:
    executable = node_executable()
    if executable is None:
        return Tool("typescript", None, None, False, "Node.js is not provisioned")
    script = (
        "process.stdout.write("
        "require(process.argv[1] + '/node_modules/typescript').version)"
    )
    return _probe(
        "typescript", executable,
        ["-e", script, str(node_modules_root()).replace("\\", "/")],
        lambda raw: raw.strip() or None,
    )


def java_tool() -> Tool:
    """The JDK reference, kept exactly as provisioned before this phase."""
    from validation.differential.reference import java_entities

    state = java_entities.availability()
    javac = shutil.which("javac")
    return Tool(
        "javac", Path(javac) if javac else None, state.version,
        state.available, state.reason,
    )


def all_tools() -> dict[str, Tool]:
    return {
        tool.name: tool
        for tool in (
            java_tool(), node_tool(), typescript_tool(), go_tool(),
            python_reference_tool(), parso_tool(),
        )
    }


def manifest() -> dict[str, Any]:
    """Full provenance for a study document."""
    tools = all_tools()
    return {
        "reference_environment_root": str(_root()),
        "isolated_from_archlens_runtime": True,
        "note": (
            "These tools are research-validation dependencies only. None is an "
            "ArchLens runtime dependency, and the ArchLens acceptance "
            "environment is never modified by provisioning them."
        ),
        "pins": dict(sorted(PINS.items())),
        "tools": {name: tool.as_dict() for name, tool in sorted(tools.items())},
        "all_available": all(tool.available for tool in tools.values()),
        "unavailable": sorted(
            name for name, tool in tools.items() if not tool.available
        ),
    }
