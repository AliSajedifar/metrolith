"""Neutral human-presentation projections for Metrolith values.

This module is deliberately not a metric, admission, or trust authority.  It
only turns already-evaluated values into consistent human labels.  Machine
artifacts keep their raw values and statuses unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import shutil
import textwrap
from typing import Any, Mapping

from modules.subject import subject_key_of


LOCAL_UNPROTECTED_LABEL = "LOCAL — UNPROTECTED"
PROTECTED_LABEL = "PROTECTED"


def wrap_prose(text: str, *, indent: str = "") -> str:
    """Wrap human prose only, preserving literal tokens and machine documents."""
    width = max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)
    return textwrap.fill(
        text, width=width, initial_indent=indent, subsequent_indent=indent,
        break_long_words=False, break_on_hyphens=False,
    )


@dataclass(frozen=True, slots=True)
class SubjectDisplay:
    """Stable display identity beside the raw portable subject key."""

    name: str
    subject_key: str
    repository_locator: str | None


def subject_display(subject: Mapping[str, Any]) -> SubjectDisplay:
    """Project one subject without ever using ``null`` as its name."""

    key = subject_key_of(subject)
    locator = subject.get("repository_url")
    if isinstance(locator, str) and locator.strip():
        clean = locator.strip()
        return SubjectDisplay(clean, key, clean)
    return SubjectDisplay("Local repository", key, None)


def boolean(value: Any, *, absent: str = "not supplied") -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return absent
    return str(value)


def scalar(
    value: Any,
    *,
    absent: str = "not supplied",
) -> str:
    """Render scalar values while retaining measured numeric zero."""

    if value is None:
        return absent
    if isinstance(value, bool):
        return boolean(value, absent=absent)
    return str(value)


_STATUS_LABELS = {
    "complete": "complete",
    "partial": "partial",
    "failed": "failed",
    "unavailable": "unavailable",
    "absent": "not supplied",
    "not_requested": "not requested",
    "not_applicable": "not applicable",
    "not_evaluable": "not evaluable",
    "refused": "refused",
    "measured": "measured",
    "running": "running",
}


def status(value: Any, *, absent: str = "not supplied") -> str:
    if value is None:
        return absent
    raw = str(value)
    return _STATUS_LABELS.get(raw, raw.replace("_", " "))


def measurement(value: Any, measurement_status: Any) -> str:
    """Show a value only when its persisted status says it was measured.

    This is a presentation guard, not a reinterpretation: the raw status stays
    visible and is the sole input deciding whether a recorded numeric cell may
    be presented as a measurement.
    """

    raw_status = str(measurement_status or "")
    if raw_status == "failed":
        return "unavailable"
    if value is not None and raw_status in {"complete", "partial", "measured"}:
        return scalar(value)
    if value is not None and raw_status == "not_applicable":
        return "not applicable"
    return status(measurement_status, absent="unavailable")


def recognized_source_count(aggregate: Mapping[str, Any]) -> int | None:
    """Read source availability, including the producer's examined-empty state."""
    value = aggregate.get("source_files")
    state = aggregate.get("source_files_status")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if state in {"complete", "partial"}:
        return value
    if state == "not_applicable" and value == 0 and aggregate.get("inventory_status") == "complete":
        return 0
    return None


def trust_label(provenance: Mapping[str, Any] | None) -> str:
    """Echo the existing evaluated gate mode as the shared trust vocabulary."""

    raw = provenance or {}
    mode = raw.get("gate_mode") or raw.get("mode")
    protected = raw.get("protected_gate")
    if mode == "protected_required" and protected is True:
        return PROTECTED_LABEL
    return LOCAL_UNPROTECTED_LABEL


def portable_path(path: str | Path, *, base: str | Path | None = None) -> str:
    value = Path(path)
    if base is not None:
        try:
            return value.resolve(strict=False).relative_to(
                Path(base).resolve(strict=False)
            ).as_posix()
        except (OSError, ValueError):
            pass
    if not value.is_absolute():
        return value.as_posix()
    return value.name or "output"


def terminal_path(path: str | Path) -> str:
    """An actionable local location; never use in portable exported artifacts."""
    return str(Path(path).expanduser().resolve(strict=False))


def terminal_command(command: str, path: str | Path) -> str:
    """Quote for PowerShell on Windows, POSIX sh elsewhere."""
    value = terminal_path(path)
    quoted = "'" + value.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(value)
    return f"metrolith {command} {quoted}"


def source_failure(result: Mapping[str, Any]) -> str | None:
    aggregate = (result.get("metrics") or {}).get("aggregate") or {}
    if aggregate.get("source_files_status") != "failed":
        return None
    errors = result.get("errors") or []
    message = next((item.get("message") for item in errors if isinstance(item, Mapping) and item.get("message")), None)
    if not message:
        return "Source measurement unavailable; inspect the recorded diagnostics before rerunning."
    # A bounded, single-line cause, never a traceback or an invented count.
    cause = " ".join(str(message).split())[:500]
    action = "Check the requested revision in this repository" if "revision" in cause.lower() else "Check the source path and read access"
    return f"Analysis failed: {cause}\nSource measurement unavailable. {action}, then rerun."


def digest_prefix(value: Any, *, length: int = 12) -> str:
    if not isinstance(value, str) or not value:
        return "not supplied"
    return value[:length]


__all__ = [
    "LOCAL_UNPROTECTED_LABEL",
    "PROTECTED_LABEL",
    "SubjectDisplay",
    "boolean",
    "digest_prefix",
    "measurement",
    "portable_path",
    "scalar",
    "status",
    "subject_display",
    "trust_label",
]
