"""G2-B — ArchLens Cognitive Complexity against real subjects, per callable.

The five Layer-2 subjects already selected for the Complexity Contract 1.0.0
campaign, re-used unchanged so the two studies talk about the same repositories
at the same revisions. Every selected language present in a subject is compared,
not only the family language: three of the five carry a second language, and
measuring only the family one would leave those files uncompared without saying
so.

**C4/CX evidence is never written to.** The C4 runs live under `l2c4` and are
Artifact 1.9 documents with no cognitive column at all. This campaign reads
fresh Artifact 1.10 runs from a separate root, :data:`DEFAULT_RUN_ROOT`, so
nothing the C4 campaign produced is touched, moved or re-analyzed.

Three separations are mechanical rather than remembered:

**Identity is decided before any value is read.** Pairing goes through
:mod:`validation.differential.callable_matching`, which cannot see a metric.
For the three references that suppress a genuine zero, identity comes from a
separate enumerator run and never from the metric output -- a suppressed-zero
callable has no metric row to be paired from.

**Raw observations are persisted before adjudication.** :func:`persist_raw`
writes every observation with every disagreement left ``unresolved``.
Adjudication is a separate pass writing a separate file, so the unclassified
baseline survives and can be re-read.

**There is no primary independent adapter.** Every reference here implements its
own definition. A disagreement is evidence about two definitions; it is grounds
for an ArchLens defect claim only when the frozen rule table independently
proves ArchLens wrong.

No accuracy percentage is computed anywhere.
"""

from __future__ import annotations

import os

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from validation.differential import callable_matching, cognitive_layer2
from validation.differential.cognitive_definitions import (
    COGNITIVE_REFERENCE,
    LANGUAGES,
    mapping_for,
)
from validation.differential.cognitive_zero_suppression import (
    SuppressionProbe,
    prove_suppression,
)
from validation.differential.reference import cognitive_drivers

#: The subject checkouts, materialized for the C4 campaign and reused as-is.
DEFAULT_SUBJECT_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2ws/src'

#: Artifact 1.10 runs, in their OWN root. `l2c4` holds the C4 1.9 runs and is
#: read-only for this campaign -- a 1.9 document carries no cognitive column, so
#: pointing here at `l2c4` would produce a clean all-null table that reads
#: exactly like "nothing to compare".
DEFAULT_RUN_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2cg'

#: The already-selected initial subjects. `l2big` is NOT reachable from here;
#: expansion requires a recorded trigger.
INITIAL_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("layer2-go-shop", "Go"),
    ("layer2-java-demo", "Java"),
    ("layer2-javascript-monolith", "JavaScript"),
    ("layer2-python-ralph", "Python"),
    ("layer2-typescript-securo", "TypeScript"),
)


class RunUnusable(RuntimeError):
    """An ArchLens run cannot support a cognitive comparison. Typed, not zero."""


@dataclass
class SubjectComparison:
    """Everything one (subject, language) comparison produced."""

    subject_key: str
    language: str
    reference_name: str
    reference_version: str | None
    scope: dict[str, Any]
    population: cognitive_layer2.PopulationAccounting
    observations: list[cognitive_layer2.CognitiveObservation]
    suppression: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "language": self.language,
            "reference_name": self.reference_name,
            "reference_version": self.reference_version,
            "scope": self.scope,
            "zero_suppression_proof": self.suppression,
            "population": self.population.as_dict(),
            "observations": [item.as_dict() for item in self.observations],
        }


# ---------------------------------------------------------------------------
# The ArchLens side: artifacts only, never in-memory results
# ---------------------------------------------------------------------------


def latest_run(workspace: Path) -> Path:
    """The run directory a workspace last completed.

    `analyze --workspace W` writes its artifacts under `W/archlens-output`,
    while the C4 campaign passed the output root directly. Both layouts are
    accepted by looking for the pointer rather than assuming one shape -- an
    assumption here fails as "no completed run", which is indistinguishable
    from a subject that was never analyzed.
    """
    workspace = Path(workspace)
    for candidate in (workspace, workspace / "archlens-output"):
        pointer = candidate / "latest_run.json"
        if pointer.is_file():
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            status = payload.get("status")
            # `completed_with_errors` is a normal REAL-SUBJECT outcome: some
            # files did not parse and the run recorded which. Refusing it would
            # drop an entire language over per-file diagnostics, and the
            # per-file states are exactly what explains a missing callable
            # later. Anything else is a broken input rather than a comparison.
            if status not in ("completed", "completed_with_errors"):
                raise RunUnusable(
                    f"{candidate} holds run {payload.get('run_id')} with status "
                    f"{status!r}; an unfinished run is not an empty comparison"
                )
            return candidate / payload["run_directory"]
    raise RunUnusable(f"{workspace} holds no completed ArchLens run")


def _view(run_directory: Path):
    from validation.artifact_io.reader import open_run

    return open_run(Path(run_directory))


def archlens_callables(run_directory: Path) -> list[dict[str, Any]]:
    """Every callable row, read back through the reader rather than remembered."""
    view = _view(run_directory)
    if not view.has_callable_artifact:
        raise RunUnusable(
            f"{run_directory} carries no callable artifact; a run that measured "
            f"cognitive complexity must carry one, so this is a broken input "
            f"rather than an empty comparison"
        )
    generation = tuple(
        int(part) for part in str(view.declared_artifact_schema).split(".")[:2]
    )
    if generation < (1, 10):
        raise RunUnusable(
            f"{run_directory} declares Artifact Schema "
            f"{view.declared_artifact_schema}, which has NO cognitive column. "
            f"Comparing against it would produce an all-null table that reads "
            f"like a clean result. Re-analyze the subject at 1.10 or later."
        )
    return [dict(row) for row in view.stream_callables()]


def archlens_selected_files(run_directory: Path, language: str) -> list[str]:
    view = _view(run_directory)
    found: list[str] = []
    for inventory in view.inventories.values():
        for record in inventory.get("files", ()):
            if not record.get("included_in_metrics"):
                continue
            if record.get("detected_language") != language:
                continue
            path = record.get("relative_path")
            if path:
                found.append(str(path).replace("\\", "/"))
    return sorted(set(found))


def languages_present(run_directory: Path) -> dict[str, int]:
    view = _view(run_directory)
    counts: dict[str, int] = {}
    for inventory in view.inventories.values():
        for record in inventory.get("files", ()):
            if not record.get("included_in_metrics"):
                continue
            language = record.get("detected_language")
            if language:
                counts[str(language)] = counts.get(str(language), 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# The reference side
# ---------------------------------------------------------------------------


def run_reference(
    language: str, subject_root: Path, files: Sequence[str], work: Path
) -> cognitive_drivers.ReferenceRun:
    """Dispatch to the one pinned reference for this language."""
    if language == "Go":
        return cognitive_drivers.run_gocognit(subject_root, files)
    if language == "Java":
        return cognitive_drivers.run_pmd(subject_root, files, work)
    if language in ("JavaScript", "TypeScript"):
        return cognitive_drivers.run_sonarjs(language, subject_root, files, work)
    if language == "Python":
        return cognitive_drivers.run_python_reference(subject_root, files, work)
    raise KeyError(language)


def licensed_probes(work: Path) -> dict[str, SuppressionProbe]:
    """Measure every reference's zero behaviour, then license it. Once per run.

    Measuring and licensing stay separate calls: a measurement must never
    authorize its own inference.
    """
    probes: dict[str, SuppressionProbe] = {}
    for language in LANGUAGES:
        live = cognitive_drivers.probe_zero_suppression(
            language, Path(work) / "probe" / language
        )
        probes[language] = prove_suppression(live)
    return probes


def compare_subject_language(
    *,
    subject_key: str,
    language: str,
    subject_root: Path,
    run_directory: Path,
    work: Path,
    suppression: SuppressionProbe,
) -> SubjectComparison:
    """One subject, one language, one reference."""
    rows = [
        row
        for row in archlens_callables(run_directory)
        if row.get("detected_language") == language
    ]
    files = archlens_selected_files(run_directory, language)
    scope = cognitive_drivers.resolve_scope(language, subject_root, files)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)

    reference = run_reference(language, subject_root, scope, work)
    combined = cognitive_layer2.build_reference_side(
        metric_rows=reference.rows, enumerated_rows=reference.enumerated
    )
    match = callable_matching.match_callables(rows, combined)
    observations = cognitive_layer2.compare_pairs(
        subject_key=subject_key,
        language=language,
        match=match,
        archlens_rows=rows,
        reference_rows=combined,
        suppression=suppression,
    )
    population = cognitive_layer2.account(
        match, rows, reference.rows, reference.enumerated, observations,
        reference.unreadable_files,
    )
    return SubjectComparison(
        subject_key=subject_key,
        language=language,
        reference_name=COGNITIVE_REFERENCE[language],
        reference_version=mapping_for(language).reference_version,
        scope={
            "selected_file_count": len(scope),
            "subject_root": str(subject_root),
            "invocation": reference.invocation,
        },
        population=population,
        observations=observations,
        suppression=suppression.as_dict(),
    )


def run_campaign(
    *,
    subjects: Sequence[tuple[str, str]] = INITIAL_SUBJECTS,
    subject_root: Path = DEFAULT_SUBJECT_ROOT,
    run_root: Path = DEFAULT_RUN_ROOT,
    work_root: Path,
    every_language: bool = True,
) -> list[SubjectComparison]:
    """The initial five subjects, every selected language with a reference."""
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    probes = licensed_probes(work_root)

    results: list[SubjectComparison] = []
    for subject_key, family_language in subjects:
        run_directory = latest_run(Path(run_root) / subject_key)
        present = languages_present(run_directory)
        languages = (
            [name for name in present if name in LANGUAGES]
            if every_language
            else [family_language]
        )
        if family_language not in languages:
            raise RunUnusable(
                f"{subject_key}: the family language {family_language} selected "
                f"no files; the subject or the run is wrong, and this is not an "
                f"empty comparison"
            )
        for language in sorted(languages):
            results.append(
                compare_subject_language(
                    subject_key=subject_key,
                    language=language,
                    subject_root=Path(subject_root) / subject_key,
                    run_directory=run_directory,
                    work=work_root / subject_key / language,
                    suppression=probes[language],
                )
            )
    return results


# ---------------------------------------------------------------------------
# Persistence. Raw first, always.
# ---------------------------------------------------------------------------


def summarize(results: Sequence[SubjectComparison]) -> dict[str, Any]:
    """Raw counts across the campaign. Deliberately no percentage."""
    every = [item for result in results for item in result.observations]
    by_state: dict[str, int] = {name: 0 for name in cognitive_layer2.RESULT_STATES}
    for item in every:
        by_state[item.state] = by_state.get(item.state, 0) + 1
    return {
        "comparisons": len(results),
        "subjects": sorted({result.subject_key for result in results}),
        "languages": sorted({result.language for result in results}),
        "observations": len(every),
        "by_state": by_state,
        "per_comparison": [
            {
                "subject_key": result.subject_key,
                "language": result.language,
                "reference_name": result.reference_name,
                "selected_file_count": result.scope["selected_file_count"],
                "population": result.population.as_dict(),
                "by_state": {
                    name: sum(
                        1 for item in result.observations if item.state == name
                    )
                    for name in cognitive_layer2.RESULT_STATES
                },
            }
            for result in results
        ],
        "reporting_rule": (
            "Raw counts only. No accuracy percentage. A reported zero and an "
            "inferred suppressed zero are counted separately and are never "
            "merged: both are 0, and they are not equally strong evidence."
        ),
    }


def persist_raw(
    results: Sequence[SubjectComparison],
    destination: Path,
    *,
    study_metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write the unclassified baseline. Called BEFORE any adjudication."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cognitive_study_format_version": cognitive_layer2.COGNITIVE_STUDY_FORMAT_VERSION,
        "stage": "raw_observations_before_adjudication",
        "study_metadata": dict(study_metadata or {}),
        "result_states": list(cognitive_layer2.RESULT_STATES),
        "cause_taxonomy": list(cognitive_layer2.CAUSES),
        "summary": summarize(results),
        "comparisons": [result.as_dict() for result in results],
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def disagreements(
    results: Sequence[SubjectComparison],
) -> list[cognitive_layer2.CognitiveObservation]:
    return [
        item
        for result in results
        for item in result.observations
        if item.outcome == cognitive_layer2.OUTCOME_DISAGREEMENT
    ]
