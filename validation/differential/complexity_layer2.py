"""Layer C2 — Complexity Contract 1.0.0 against real subjects, per callable.

Track A. ArchLens analyzes a subject and its own artifacts supply two things and
only two: **the selected file set** and **the callable location list**. ArchLens
metric values are never handed to a reference — that would make the comparison
circular.

Three separations are load-bearing and are kept mechanical rather than
remembered:

**Identity is decided before any value is read.** Pairing goes through
:mod:`validation.differential.callable_matching`, which cannot see a metric.
:func:`compare_pairs` receives pairs that are already fixed. A matcher able to
see values could be tuned, however unintentionally, to pair the rows that agree.

**Raw observations are persisted before adjudication.** :func:`persist_raw`
writes every observation with ``cause = unresolved`` for every disagreement.
Adjudication is a separate pass writing a separate file, so the unclassified
baseline survives and can be re-read.

**The two reference tiers are never merged.** A *primary independent adapter*
implements ArchLens's own definition with a different parser, so a disagreement
there is evidence about an implementation. An *external tool* implements its own
definition, so a difference there is a property of two definitions and is never
on its own grounds for changing ArchLens. Only cyclomatic complexity has an
external reference at all.

No accuracy percentage is computed anywhere in this module. A reference is a
triangulation mechanism, not ground truth, and success is every disagreement
localized and classified — not agreement.
"""

from __future__ import annotations

import os

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from validation.differential import callable_matching, records
from validation.differential.complexity_definitions import (
    EXTERNAL_CC,
    METRICS,
    PRIMARY_ADAPTER,
)
from validation.differential.reference import complexity_drivers

# -- the C4 cause taxonomy ---------------------------------------------------
#
# Six classes come from the campaign's existing vocabulary unchanged. Two are
# specific to per-callable complexity comparison and are declared here rather
# than pushed into the shared taxonomy, which the entity study also consumes.

CAUSE_ARCHLENS_DEFECT = records.CAUSE_ARCHLENS_DEFECT
CAUSE_ADAPTER_DEFECT = records.CAUSE_ADAPTER_DEFECT
CAUSE_DEFINITION_MISMATCH = records.CAUSE_DEFINITION_MISMATCH
CAUSE_PARSER_LIMITATION = records.CAUSE_PARSER_LIMITATION
CAUSE_UNSUPPORTED_CONSTRUCT = records.CAUSE_UNSUPPORTED_CONSTRUCT
CAUSE_UNRESOLVED = records.CAUSE_UNRESOLVED

#: An external tool reporting something its own documentation does not claim to
#: support (Lizard's overlapping TypeScript spans). Distinct from
#: `reference_tool_defect`: nothing is broken, the tool simply does not do this.
CAUSE_REFERENCE_TOOL_LIMITATION = "reference_tool_limitation"

#: Two sides could not be shown to be talking about the same callable. Never
#: folded into a metric disagreement: a wrong pairing invents one.
CAUSE_MATCHING_AMBIGUITY = "matching_ambiguity"

C4_CAUSES = (
    CAUSE_ARCHLENS_DEFECT,
    CAUSE_ADAPTER_DEFECT,
    CAUSE_REFERENCE_TOOL_LIMITATION,
    CAUSE_DEFINITION_MISMATCH,
    CAUSE_PARSER_LIMITATION,
    CAUSE_MATCHING_AMBIGUITY,
    CAUSE_UNSUPPORTED_CONSTRUCT,
    CAUSE_UNRESOLVED,
)

TIER_PRIMARY = "primary_independent_adapter"
TIER_EXTERNAL = "external_cyclomatic_tool"

#: The only metric any external tool reports. The other six are listed as
#: uncovered in the definition mappings rather than quietly left out.
EXTERNAL_METRICS = ("cyclomatic_complexity",)

#: The five initial subjects, one per language family. `l2big` is NOT run
#: automatically; expansion requires a recorded trigger.
DEFAULT_SUBJECT_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2ws/src'
DEFAULT_RUN_ROOT = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2c4'

INITIAL_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("layer2-go-shop", "Go"),
    ("layer2-java-demo", "Java"),
    ("layer2-javascript-monolith", "JavaScript"),
    ("layer2-python-ralph", "Python"),
    ("layer2-typescript-securo", "TypeScript"),
)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplexityObservation:
    """One (callable, metric, reference) comparison. Raw until adjudicated."""

    subject_key: str
    language: str
    reference_tier: str
    reference_name: str
    reference_version: str | None
    relative_path: str
    qualified_name: str | None
    start_line: int | None
    end_line: int | None
    match_tier: str
    metric: str
    archlens_value: int | None
    reference_value: int | None
    difference: int | None
    agreement_status: str
    cause: str
    not_evaluable_reason: str | None = None
    adjudication_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class PopulationAccounting:
    """Who each side saw. A population difference is a finding, not noise."""

    archlens_rows: int = 0
    reference_rows: int = 0
    matched: int = 0
    matched_by_tier: dict[str, int] = field(default_factory=dict)
    ambiguous: int = 0
    unmatched_archlens: int = 0
    unmatched_reference: int = 0
    unreadable_files: list[dict[str, str]] = field(default_factory=list)
    #: The rows themselves, not only their count: an unmatched row is evidence
    #: about a population difference and has to be classifiable one by one.
    unmatched_archlens_detail: list[dict[str, Any]] = field(default_factory=list)
    unmatched_reference_detail: list[dict[str, Any]] = field(default_factory=list)
    #: ArchLens per-file complexity state for the files those rows live in.
    file_status: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "archlens_rows": self.archlens_rows,
            "reference_rows": self.reference_rows,
            "matched": self.matched,
            "matched_by_tier": dict(self.matched_by_tier),
            "ambiguous": self.ambiguous,
            "unmatched_archlens": self.unmatched_archlens,
            "unmatched_reference": self.unmatched_reference,
            "unmatched_archlens_detail": self.unmatched_archlens_detail,
            "unmatched_reference_detail": self.unmatched_reference_detail,
            "unreadable_file_count": len(self.unreadable_files),
            "unreadable_files": self.unreadable_files[:20],
            "file_status_for_unmatched": self.file_status,
        }


@dataclass
class ComparisonResult:
    """Everything one (subject, language, reference) comparison produced."""

    subject_key: str
    language: str
    reference_tier: str
    reference_name: str
    reference_version: str | None
    scope: dict[str, Any]
    population: PopulationAccounting
    observations: list[ComplexityObservation]
    build_evidence: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "language": self.language,
            "reference_tier": self.reference_tier,
            "reference_name": self.reference_name,
            "reference_version": self.reference_version,
            "scope": self.scope,
            "population": self.population.as_dict(),
            "build_evidence": self.build_evidence,
            "observations": [item.as_dict() for item in self.observations],
        }


# ---------------------------------------------------------------------------
# The ArchLens side: artifacts only, never in-memory results
# ---------------------------------------------------------------------------


def latest_run(workspace: Path) -> Path:
    """The run directory a workspace last completed."""
    pointer = Path(workspace) / "latest_run.json"
    if not pointer.is_file():
        raise FileNotFoundError(f"{workspace} holds no completed ArchLens run")
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    return Path(workspace) / payload["run_directory"]


def _integer(value: Any) -> int | None:
    """An empty cell is an ABSENT value, never a zero."""
    if value is None or value == "":
        return None
    return int(value)


def archlens_callables(run_directory: Path) -> list[dict[str, Any]]:
    """Every callable row ArchLens persisted, read back through the reader."""
    from validation.artifact_io.reader import open_run

    view = open_run(Path(run_directory))
    if not view.has_callable_artifact:
        raise FileNotFoundError(
            f"{run_directory} carries no callable artifact; a run that measured "
            f"complexity must carry one, so this is a broken input rather than "
            f"an empty comparison"
        )
    return [dict(row) for row in view.stream_callables()]


def archlens_selected_files(run_directory: Path, language: str) -> list[str]:
    """The exact files ArchLens measured for one language, from its inventory."""
    from validation.artifact_io.reader import open_run

    view = open_run(Path(run_directory))
    selected: list[str] = []
    for inventory in view.inventories.values():
        for record in inventory.get("files", ()):
            if not record.get("included_in_metrics"):
                continue
            if record.get("detected_language") != language:
                continue
            path = record.get("relative_path")
            if path:
                selected.append(str(path).replace("\\", "/"))
    return sorted(set(selected))


def archlens_file_status(run_directory: Path, language: str) -> dict[str, dict[str, Any]]:
    """Per-file complexity state, so zero rows never has to be interpreted.

    A parse-failed file and a genuinely callable-free file both emit zero
    callable rows. Only these columns tell them apart, and without them an
    unmatched reference row in a failed file would look like a missing ArchLens
    callable rather than a file ArchLens could not read.
    """
    from validation.artifact_io.reader import open_run

    view = open_run(Path(run_directory))
    if not view.has_contribution_ledger:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for row in view.stream_contributions():
        if row.get("detected_language") != language:
            continue
        path = row.get("relative_path")
        if not path:
            continue
        found[str(path).replace("\\", "/")] = {
            "structural_complexity_status": row.get("structural_complexity_status"),
            "nloc_status": row.get("nloc_status"),
            "callable_count": _integer(row.get("callable_count")),
            "parse_status": row.get("parse_status"),
        }
    return found


def languages_present(run_directory: Path) -> dict[str, int]:
    """Selected-file counts per language, so nothing is ignored by assumption."""
    from validation.artifact_io.reader import open_run

    view = open_run(Path(run_directory))
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
# Comparison. Identity is already decided when these run.
# ---------------------------------------------------------------------------


def _lookup(rows: Sequence[Mapping[str, Any]]) -> dict[tuple, Mapping[str, Any]]:
    """Index rows by the identity fields the matcher itself uses."""
    index: dict[tuple, Mapping[str, Any]] = {}
    for row in rows:
        key = callable_matching.key_from_row(row)
        index.setdefault(
            (key.relative_path, key.qualified_name, key.start_line, key.end_line),
            row,
        )
    return index


def _key_tuple(key: callable_matching.CallableKey) -> tuple:
    return (key.relative_path, key.qualified_name, key.start_line, key.end_line)


def compare_pairs(
    *,
    subject_key: str,
    language: str,
    reference_tier: str,
    reference_name: str,
    reference_version: str | None,
    metrics: Sequence[str],
    match: callable_matching.MatchResult,
    archlens_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
) -> list[ComplexityObservation]:
    """Turn already-fixed pairings into raw per-metric observations.

    Every disagreement leaves here as ``unresolved``. Classification is a later,
    separate pass, so the unclassified baseline is a real artifact rather than a
    stage nobody can inspect.
    """
    left_index = _lookup(archlens_rows)
    right_index = _lookup(reference_rows)
    observations: list[ComplexityObservation] = []

    for left_key, right_key, tier in match.matched:
        left = left_index[_key_tuple(left_key)]
        right = right_index[_key_tuple(right_key)]
        structural_status = left.get("structural_complexity_status")
        nloc_status = left.get("nloc_status")

        for metric in metrics:
            archlens_value = _integer(left.get(metric))
            reference_value = _integer(right.get(metric))

            status = records.AGREEMENT_EXACT
            cause = records.CAUSE_NONE
            reason: str | None = None
            difference: int | None = None

            if archlens_value is None or reference_value is None:
                status = records.AGREEMENT_NOT_EVALUABLE
                cause = records.CAUSE_NONE
                if archlens_value is None:
                    owning_status = (
                        nloc_status if metric == "nloc" else structural_status
                    )
                    reason = (
                        f"ArchLens recorded no value (status {owning_status!r}); "
                        f"an absent measurement is never read as zero"
                    )
                else:
                    reason = (
                        right.get("not_evaluable_reason")
                        or "the reference recorded no value; an absent "
                           "measurement is never read as zero"
                    )
            else:
                difference = reference_value - archlens_value
                if difference != 0:
                    status = records.AGREEMENT_DISAGREEMENT
                    cause = CAUSE_UNRESOLVED

            observations.append(
                ComplexityObservation(
                    subject_key=subject_key,
                    language=language,
                    reference_tier=reference_tier,
                    reference_name=reference_name,
                    reference_version=reference_version,
                    relative_path=left_key.relative_path,
                    qualified_name=left_key.qualified_name,
                    start_line=left_key.start_line,
                    end_line=left_key.end_line,
                    match_tier=tier,
                    metric=metric,
                    archlens_value=archlens_value,
                    reference_value=reference_value,
                    difference=difference,
                    agreement_status=status,
                    cause=cause,
                    not_evaluable_reason=reason,
                )
            )

    # An ambiguous pairing is an observation in its own right. Dropping it would
    # hide the one failure mode that manufactures disagreements.
    for left_key, candidates in match.ambiguous:
        observations.append(
            ComplexityObservation(
                subject_key=subject_key,
                language=language,
                reference_tier=reference_tier,
                reference_name=reference_name,
                reference_version=reference_version,
                relative_path=left_key.relative_path,
                qualified_name=left_key.qualified_name,
                start_line=left_key.start_line,
                end_line=left_key.end_line,
                match_tier=callable_matching.AMBIGUOUS,
                metric="identity",
                archlens_value=None,
                reference_value=None,
                difference=None,
                agreement_status=records.AGREEMENT_NOT_EVALUABLE,
                cause=CAUSE_MATCHING_AMBIGUITY,
                not_evaluable_reason=(
                    f"{len(candidates)} reference candidates; classified rather "
                    f"than guessed, because a wrong pairing invents a "
                    f"disagreement that does not exist"
                ),
            )
        )

    return observations


def _detail(key: callable_matching.CallableKey) -> dict[str, Any]:
    return {
        "relative_path": key.relative_path,
        "qualified_name": key.qualified_name,
        "start_line": key.start_line,
        "end_line": key.end_line,
    }


def _population(
    match: callable_matching.MatchResult,
    archlens_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    unreadable: Iterable[Mapping[str, str]] = (),
    file_status: Mapping[str, Mapping[str, Any]] | None = None,
) -> PopulationAccounting:
    summary = match.summary()
    left_detail = [_detail(key) for key in match.unmatched_archlens]
    right_detail = [_detail(key) for key in match.unmatched_reference]
    touched = {item["relative_path"] for item in left_detail + right_detail}
    return PopulationAccounting(
        archlens_rows=len(archlens_rows),
        reference_rows=len(reference_rows),
        matched=summary["matched"],
        matched_by_tier=summary["matched_by_tier"],
        ambiguous=summary["ambiguous"],
        unmatched_archlens=summary["unmatched_archlens"],
        unmatched_reference=summary["unmatched_reference"],
        unreadable_files=[dict(item) for item in unreadable],
        unmatched_archlens_detail=left_detail,
        unmatched_reference_detail=right_detail,
        file_status={
            path: dict(state)
            for path, state in (file_status or {}).items()
            if path in touched
        },
    )


def compare_subject_language(
    *,
    subject_key: str,
    language: str,
    subject_root: Path,
    run_directory: Path,
    work_directory: Path,
    build_root: Path,
) -> list[ComparisonResult]:
    """One subject, one language, both reference tiers."""
    rows = [
        row for row in archlens_callables(run_directory)
        if row.get("detected_language") == language
    ]
    files = archlens_selected_files(run_directory, language)
    statuses = archlens_file_status(run_directory, language)
    scope = complexity_drivers.resolve_scope(language, subject_root, files)
    work_directory.mkdir(parents=True, exist_ok=True)

    results: list[ComparisonResult] = []

    # -- tier 1: the primary independent adapter ----------------------------
    build_evidence: dict[str, Any] | None = None
    if language == "Go":
        build_evidence = complexity_drivers.ensure_go_adapter(build_root).as_dict()
    elif language == "Java":
        build_evidence = complexity_drivers.ensure_java_adapter(build_root).as_dict()

    primary_rows = complexity_drivers.run_adapter(
        scope, work_directory, build_root=build_root
    )
    primary_match = callable_matching.match_callables(rows, primary_rows)
    results.append(
        ComparisonResult(
            subject_key=subject_key,
            language=language,
            reference_tier=TIER_PRIMARY,
            reference_name=PRIMARY_ADAPTER[language],
            reference_version=_adapter_version(language),
            scope=scope.as_dict(),
            population=_population(
                primary_match, rows, primary_rows, file_status=statuses
            ),
            observations=compare_pairs(
                subject_key=subject_key,
                language=language,
                reference_tier=TIER_PRIMARY,
                reference_name=PRIMARY_ADAPTER[language],
                reference_version=_adapter_version(language),
                metrics=METRICS,
                match=primary_match,
                archlens_rows=rows,
                reference_rows=primary_rows,
            ),
            build_evidence=build_evidence,
        )
    )

    # -- tier 2: the external cyclomatic opinion ----------------------------
    external = complexity_drivers.run_external_cc(scope, work_directory)
    external_rows = list(external.records)
    external_match = callable_matching.match_callables(rows, external_rows)
    results.append(
        ComparisonResult(
            subject_key=subject_key,
            language=language,
            reference_tier=TIER_EXTERNAL,
            reference_name=EXTERNAL_CC[language],
            reference_version=external.tool_version,
            scope=scope.as_dict(),
            population=_population(
                external_match, rows, external_rows, external.unreadable_files,
                file_status=statuses,
            ),
            observations=compare_pairs(
                subject_key=subject_key,
                language=language,
                reference_tier=TIER_EXTERNAL,
                reference_name=EXTERNAL_CC[language],
                reference_version=external.tool_version,
                metrics=EXTERNAL_METRICS,
                match=external_match,
                archlens_rows=rows,
                reference_rows=external_rows,
            ),
        )
    )
    return results


def _adapter_version(language: str) -> str:
    from validation.differential.complexity_definitions import REFERENCE_VERSIONS

    key = {
        "Go": "reference_complexity_adapter_go",
        "Java": "reference_complexity_adapter_java",
        "Python": "reference_complexity_adapter_python",
        "JavaScript": "reference_complexity_adapter_node",
        "TypeScript": "reference_complexity_adapter_node",
    }[language]
    return REFERENCE_VERSIONS[key]


# ---------------------------------------------------------------------------
# Persistence. Raw first, always.
# ---------------------------------------------------------------------------


def persist_raw(results: Sequence[ComparisonResult], destination: Path) -> Path:
    """Write the unclassified baseline. Called BEFORE any adjudication."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": "raw_observations_before_adjudication",
        "reporting_rule": (
            "Raw counts only. No accuracy percentage is computed anywhere: a "
            "reference is a triangulation mechanism, not ground truth."
        ),
        "cause_taxonomy": list(C4_CAUSES),
        "comparisons": [result.as_dict() for result in results],
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return destination


def disagreements(results: Sequence[ComparisonResult]) -> list[ComplexityObservation]:
    return [
        observation
        for result in results
        for observation in result.observations
        if observation.agreement_status == records.AGREEMENT_DISAGREEMENT
    ]


def run_campaign(
    *,
    subjects: Sequence[tuple[str, str]] = INITIAL_SUBJECTS,
    subject_root: Path = DEFAULT_SUBJECT_ROOT,
    run_root: Path = DEFAULT_RUN_ROOT,
    work_root: Path,
    every_language: bool = True,
) -> list[ComparisonResult]:
    """The initial five subjects. `l2big` is never reached from here.

    ``every_language`` compares every language present in a subject that has a
    primary adapter, not only the family language. The subjects are real
    repositories and three of the five carry a second language; measuring the
    family language alone would leave those files uncompared without saying so.
    """
    build_root = Path(work_root) / "adapters"
    build_root.mkdir(parents=True, exist_ok=True)
    results: list[ComparisonResult] = []

    for subject_key, family_language in subjects:
        run_directory = latest_run(Path(run_root) / subject_key)
        present = languages_present(run_directory)
        languages = (
            [name for name in present if name in PRIMARY_ADAPTER]
            if every_language else [family_language]
        )
        if family_language not in languages:
            raise RuntimeError(
                f"{subject_key}: the family language {family_language} selected "
                f"no files; the subject or the run is wrong, and this is not an "
                f"empty comparison"
            )
        for language in languages:
            results.extend(
                compare_subject_language(
                    subject_key=subject_key,
                    language=language,
                    subject_root=Path(subject_root) / subject_key,
                    run_directory=run_directory,
                    work_directory=Path(work_root) / subject_key / language,
                    build_root=build_root,
                )
            )
    return results


def summarize(results: Sequence[ComparisonResult]) -> dict[str, Any]:
    """Raw counts. Deliberately no percentage of any kind."""
    summary: dict[str, Any] = {"comparisons": []}
    for result in results:
        counts: dict[str, int] = {}
        for observation in result.observations:
            counts[observation.agreement_status] = (
                counts.get(observation.agreement_status, 0) + 1
            )
        summary["comparisons"].append({
            "subject_key": result.subject_key,
            "language": result.language,
            "reference_tier": result.reference_tier,
            "reference_name": result.reference_name,
            "population": result.population.as_dict(),
            "observations": len(result.observations),
            "by_agreement_status": dict(sorted(counts.items())),
        })
    return summary
