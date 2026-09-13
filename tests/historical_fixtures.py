"""Deterministic lookup for tracked historical-Artifact fixtures.

This replaces `REPOSITORY.rglob("run_manifest.json")` scans that used to locate
historical fixtures by sweeping the whole working tree. That approach had two
defects, and both were live:

1. **It found untracked local output.** The Artifact 1.3 legacy-cell assertion
   was satisfied entirely by run directories under gitignored `output/` and
   `temp/`. On a fresh clone those directories do not exist, the scan finds
   nothing, and the test calls `skipTest` â€” which reads exactly like a pass.
   Historical-compatibility coverage silently evaporated at clone time.
2. **It was order-dependent.** Whichever run `rglob` happened to yield first
   became the fixture, so adding an unrelated run directory anywhere in the
   tree could change which bytes a compatibility test ran against.

Lookup here is by declared fixture id or generation, resolved against the
tracked manifest at ``tests/fixtures/historical/FIXTURES.json``, and every path
is repository-relative.

**A missing mandatory fixture raises, and must never be turned into a skip.**
`skipTest` is legitimate only for a genuine environment or capability
limitation â€” the Windows symlink-privilege skip is a real one, because the
host cannot create the object under test. A fixture that should be committed
and is not is a repository defect, and reporting it as a skip is how the defect
stays invisible.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPOSITORY / "tests" / "fixtures" / "historical" / "FIXTURES.json"


class HistoricalFixtureError(AssertionError):
    """A declared mandatory fixture is missing, malformed, or the wrong generation.

    Deliberately an ``AssertionError``: unittest reports it as a FAILURE rather
    than an error-of-convenience, and it can never be mistaken for a skip.
    """


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise HistoricalFixtureError(
            f"the historical fixture manifest is missing: {MANIFEST_PATH}. "
            f"Historical-compatibility coverage cannot be established without "
            f"it, and its absence is a repository defect, not a skip."
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def records() -> list[dict[str, Any]]:
    return list(manifest()["fixtures"])


def _resolve(record: dict[str, Any]) -> Path:
    """Resolve one declared fixture, proving it is present and is what it claims."""
    path = REPOSITORY / record["fixture_path"]
    fixture_id = record["fixture_id"]
    if not path.is_dir():
        raise HistoricalFixtureError(
            f"mandatory historical fixture {fixture_id!r} is declared at "
            f"{record['fixture_path']} but is not present. This is a missing "
            f"tracked fixture, not an environment limitation: restore it from "
            f"the declared privacy-normalized compatibility fixture."
        )
    manifest_path = path / "run_manifest.json"
    if not manifest_path.is_file():
        raise HistoricalFixtureError(
            f"historical fixture {fixture_id!r} has no run_manifest.json"
        )
    # Presence alone is not enough: a fixture directory carrying the wrong
    # generation would satisfy a bare existence check while proving nothing
    # about the adapter it exists to exercise.
    declared = json.loads(manifest_path.read_text(encoding="utf-8")).get(
        "artifact_schema_version"
    )
    expected = record["artifact_generation"]
    if declared != expected:
        raise HistoricalFixtureError(
            f"historical fixture {fixture_id!r} declares artifact schema "
            f"{declared!r} but the manifest requires {expected!r}; the wrong "
            f"generation cannot satisfy this fixture"
        )
    return path


def fixture(fixture_id: str) -> Path:
    """Path to one declared fixture. Raises if absent or mis-declared."""
    for record in records():
        if record["fixture_id"] == fixture_id:
            return _resolve(record)
    raise HistoricalFixtureError(
        f"no historical fixture is declared with id {fixture_id!r}; declared "
        f"ids are {sorted(item['fixture_id'] for item in records())}"
    )


def run_for(generation: str) -> Path:
    """The primary tracked run fixture for one Artifact generation."""
    for record in records():
        if (
            record["artifact_generation"] == generation
            and record.get("role", "primary") == "primary"
        ):
            return _resolve(record)
    raise HistoricalFixtureError(
        f"no primary historical fixture is declared for Artifact {generation}; "
        f"declared generations are "
        f"{sorted({item['artifact_generation'] for item in records()})}"
    )


def runs_for(generation: str) -> list[Path]:
    """Every tracked fixture for one generation, in declared order."""
    found = [
        _resolve(record)
        for record in records()
        if record["artifact_generation"] == generation
    ]
    if not found:
        raise HistoricalFixtureError(
            f"no historical fixture is declared for Artifact {generation}"
        )
    return found


def generations() -> list[str]:
    return sorted({record["artifact_generation"] for record in records()})
