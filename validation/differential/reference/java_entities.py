"""Driver for the independent Java entity reference (JDK Compiler Tree API).

**Independence.** The counting happens inside ``ReferenceEntityCount.java``,
which uses javac's own front end. This module only compiles it, feeds it a file
list, and reads its JSON. It imports nothing from ``modules``.

**Availability is reported, never assumed.** If no JDK is present the reference
is `unavailable` with a reason, and Track A records that instead of silently
producing no comparison.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_SOURCE = Path(__file__).resolve().parent / "java" / "ReferenceEntityCount.java"
_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class ReferenceAvailability:
    available: bool
    version: str | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reference_version": self.version,
            "unavailable_reason": self.reason,
        }


def _tool(name: str) -> str | None:
    return shutil.which(name)


def availability() -> ReferenceAvailability:
    """Whether the JDK reference can run here, with its exact version."""
    javac = _tool("javac")
    java = _tool("java")
    if javac is None or java is None:
        return ReferenceAvailability(
            False, None,
            "no JDK on PATH: the independent Java reference needs javac and "
            "java (JDK 17 or later for the Compiler Tree API used here)",
        )
    try:
        completed = subprocess.run(
            [javac, "-version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ReferenceAvailability(
            False, None, f"javac could not be executed: {type(exc).__name__}: {exc}"
        )
    raw = (completed.stdout or completed.stderr or "").strip()
    match = re.search(r"(\d+(?:\.\d+)*(?:_\d+)?)", raw)
    if match is None:
        return ReferenceAvailability(False, None, f"unrecognized javac version: {raw!r}")
    major = int(match.group(1).split(".")[0])
    if major < 17:
        return ReferenceAvailability(
            False, raw,
            f"javac {raw} predates the JDK 17 baseline this reference targets",
        )
    return ReferenceAvailability(True, raw, None)


class JavaReferenceError(RuntimeError):
    """The reference could not be built or run. Never silently degraded."""


def count_entities(files: Sequence[Path]) -> dict[str, Any]:
    """Count declared types and methods across an explicitly supplied file set.

    Track A supplies the file set; this reference never discovers files itself.
    """
    state = availability()
    if not state.available:
        raise JavaReferenceError(state.reason or "Java reference unavailable")
    if not files:
        return {
            "files": [],
            "totals": {"types": 0, "methods": 0, "files_parsed": 0, "files_failed": 0},
            "reference_version": state.version,
        }

    with tempfile.TemporaryDirectory(prefix="archlens_javaref_") as directory:
        workspace = Path(directory)
        compiled = workspace / "classes"
        compiled.mkdir()
        build = subprocess.run(
            [_tool("javac"), "-d", str(compiled), str(_SOURCE)],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        )
        if build.returncode != 0:
            raise JavaReferenceError(
                f"reference compilation failed: {build.stderr.strip()[:500]}"
            )

        listing = workspace / "files.txt"
        listing.write_text(
            "\n".join(str(Path(item).resolve()) for item in files) + "\n",
            encoding="utf-8", newline="\n",
        )
        run = subprocess.run(
            [_tool("java"), "-cp", str(compiled), "ReferenceEntityCount", str(listing)],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
            env={**os.environ, "JAVA_TOOL_OPTIONS": ""},
        )
        if run.returncode != 0:
            raise JavaReferenceError(
                f"reference execution failed ({run.returncode}): "
                f"{run.stderr.strip()[:500]}"
            )
        try:
            payload = json.loads(run.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise JavaReferenceError(
                f"reference emitted unreadable output: {exc}; "
                f"stdout={run.stdout[:300]!r}"
            ) from exc

    payload["reference_version"] = state.version
    return payload
