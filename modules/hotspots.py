"""Git-aware, file-level maintenance hotspot analysis.

This is a derived analysis over values Metrolith already publishes.  It does not
parse source, define a metric, evaluate policy, emit findings, or project SARIF.

The model intentionally has no numeric composite score and no universal
threshold.  It turns two source values into transparent ordinal signals:

* per-file ``cognitive_complexity_total``, aggregated with the existing
  Complexity Contract presentation helper and ranked among files of the same
  language in the same repository; and
* the number of Git commits touching the file's exact-content rename lineage,
  ranked among tracked measured files in the same repository.

Ranking uses distinct observed values.  Equal source values therefore always
receive the same signal; a path or input order can never break a tie.  A cohort
with one distinct value is ``medium`` because it contains no relative evidence
for either ``low`` or ``high``.
"""

from __future__ import annotations

import bisect
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from modules.config import PROGRAM_VERSION, git_command_prefix
from modules.standalone_contracts import HOTSPOT_FORMAT, HOTSPOT_FORMAT_VERSION
from modules.subject import subject_key_of

MEASURED = "measured"
UNAVAILABLE = "unavailable"
NOT_APPLICABLE = "not_applicable"

LOW = "low"
MEDIUM = "medium"
HIGH = "high"

LOW_ATTENTION = "low_attention"
MODERATE_ATTENTION = "moderate_attention"
HIGH_ATTENTION = "high_attention"

_SIGNAL_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, None: 3}
_CLASS_ORDER = {
    HIGH_ATTENTION: 0,
    MODERATE_ATTENTION: 1,
    LOW_ATTENTION: 2,
    None: 3,
}
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class HistorySource:
    """One read-only Git object source; its path is never serialized."""

    path: Path
    kind: str


@dataclass(frozen=True)
class HotspotAnalysis:
    document: dict[str, Any]
    timings: dict[str, float]


class HotspotValidationError(ValueError):
    """A derived hotspot document contradicts its declared model."""


def canonical_json(document: Mapping[str, Any]) -> str:
    """Stable UTF-8 JSON text.  Timing observations are not part of it."""

    return json.dumps(
        document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"


def normalize_relative_path(value: Any) -> str | None:
    """Return one portable repository-relative path, or refuse it.

    Artifact paths can originate on Windows, so ``\\`` is normalized to ``/``.
    Absolute, drive-qualified, URI-shaped, empty, and traversal paths are never
    admitted.  This is also the no-absolute-path-leak boundary for output.
    """

    if not isinstance(value, str):
        return None
    text = value.replace("\\", "/")
    if (
        not text
        or text.startswith("/")
        or text.startswith("//")
        or _DRIVE_PATH.match(text)
        or "://" in text
    ):
        return None
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def signal_for(value: int | None, cohort: Iterable[int]) -> dict[str, Any]:
    """Classify one value by its rank among distinct cohort values.

    The method has no fixed numeric threshold and does not split ties.  The
    returned evidence makes every classification reproducible from source
    values alone.
    """

    distinct = sorted({item for item in cohort if isinstance(item, int)})
    if not isinstance(value, int) or value not in distinct:
        return {
            "status": UNAVAILABLE,
            "signal": None,
            "observed": value,
            "method": "distinct_observed_value_rank",
            "cohort": None,
        }
    index = bisect.bisect_left(distinct, value)
    if len(distinct) == 1:
        signal = MEDIUM
    else:
        band = (index * 2) // (len(distinct) - 1)
        signal = (LOW, MEDIUM, HIGH)[band]
    return {
        "status": MEASURED,
        "signal": signal,
        "observed": value,
        "method": "distinct_observed_value_rank",
        "cohort": {
            "distinct_value_count": len(distinct),
            "distinct_rank": index + 1,
            "minimum": distinct[0],
            "maximum": distinct[-1],
        },
    }


def classify_attention(
    complexity_signal: str | None, churn_signal: str | None
) -> str | None:
    """Combine two ordinal signals without creating a numeric score."""

    if complexity_signal not in {LOW, MEDIUM, HIGH}:
        return None
    if churn_signal not in {LOW, MEDIUM, HIGH}:
        return None
    if complexity_signal == HIGH and churn_signal == HIGH:
        return HIGH_ATTENTION
    if (
        complexity_signal == HIGH
        or churn_signal == HIGH
        or (complexity_signal == MEDIUM and churn_signal == MEDIUM)
    ):
        return MODERATE_ATTENTION
    return LOW_ATTENTION


def _git(
    repository: Path,
    arguments: Sequence[str],
    *,
    timeout: int = 300,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [*git_command_prefix(), "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=False,
        timeout=timeout,
    )


def _decode_path(value: bytes) -> str | None:
    try:
        return normalize_relative_path(value.decode("utf-8"))
    except UnicodeDecodeError:
        # Git permits non-UTF-8 path bytes; JSON does not have a lossless way to
        # identify them.  Never guess a replacement spelling.
        return None


def _history_file(status: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "unavailable_reason": reason,
        "commits": None,
        "added_lines": None,
        "deleted_lines": None,
        "touched_lines": None,
        "touched_lines_status": status,
        "binary_change_count": None,
        "exact_renames_followed": None,
    }


def _unavailable_history_result(
    paths: Iterable[str],
    reason: str,
    *,
    worktree_state: str = UNAVAILABLE,
    shallow: bool | None = None,
) -> dict[str, Any]:
    return {
        "status": UNAVAILABLE,
        "unavailable_reason": reason,
        "repository_worktree_state": worktree_state,
        "shallow": shallow,
        "files": {
            path: _history_file(UNAVAILABLE, reason) for path in sorted(paths)
        },
    }


def _parse_git_log(data: bytes, target_paths: set[str]) -> dict[str, dict[str, Any]]:
    """Parse ``git log -z --numstat`` and follow exact renames newest-to-oldest."""

    files = {
        path: {
            "status": MEASURED,
            "unavailable_reason": None,
            "commits": 0,
            "added_lines": 0,
            "deleted_lines": 0,
            "touched_lines": 0,
            "touched_lines_status": MEASURED,
            "binary_change_count": 0,
            "exact_renames_followed": 0,
        }
        for path in target_paths
    }
    aliases: dict[str, str] = {path: path for path in target_paths}
    tokens = data.split(b"\0")
    current_commit: str | None = None
    touched_in_commit: set[str] = set()

    def finish_commit() -> None:
        for path in touched_in_commit:
            files[path]["commits"] += 1
        touched_in_commit.clear()

    index = 0
    while index < len(tokens):
        token = tokens[index].lstrip(b"\r\n")
        index += 1
        if not token:
            continue
        if token.startswith(b"ARCHLENS_COMMIT:"):
            if current_commit is not None:
                finish_commit()
            current_commit = token.removeprefix(b"ARCHLENS_COMMIT:").decode(
                "ascii", errors="ignore"
            )
            continue
        if current_commit is None:
            continue

        fields = token.split(b"\t", 2)
        if len(fields) != 3:
            continue
        additions, deletions, encoded_path = fields
        renamed = encoded_path == b""
        if renamed:
            if index + 1 >= len(tokens):
                break
            old_path = _decode_path(tokens[index])
            new_path = _decode_path(tokens[index + 1])
            index += 2
            if old_path is None or new_path is None:
                continue
            current_path = aliases.get(new_path)
            if current_path is None:
                continue
            aliases[old_path] = current_path
            files[current_path]["exact_renames_followed"] += 1
        else:
            observed_path = _decode_path(encoded_path)
            if observed_path is None:
                continue
            current_path = aliases.get(observed_path)
            if current_path is None:
                continue

        touched_in_commit.add(current_path)
        record = files[current_path]
        if additions == b"-" or deletions == b"-":
            record["binary_change_count"] += 1
            record["touched_lines"] = None
            record["touched_lines_status"] = UNAVAILABLE
            continue
        try:
            added = int(additions)
            deleted = int(deletions)
        except ValueError:
            record["touched_lines"] = None
            record["touched_lines_status"] = UNAVAILABLE
            continue
        record["added_lines"] += added
        record["deleted_lines"] += deleted
        if record["touched_lines_status"] == MEASURED:
            record["touched_lines"] += added + deleted

    if current_commit is not None:
        finish_commit()
    return files


def extract_git_history(
    repository: Path,
    revision: str | None,
    target_paths: Iterable[str],
) -> dict[str, Any]:
    """Read complete committed history for target paths without modifying Git.

    Semantics:

    * history is the commit DAG reachable from the analyzed revision;
    * each commit is diffed once (merge commits against their first parent);
    * a touching commit has at least one numstat entry for the file lineage;
    * churn volume is inserted plus deleted lines, including file creation;
    * exact-content renames (``-M100%``) continue a lineage;
    * a shallow repository is unavailable, never low/zero churn;
    * a path absent from the analyzed tree is not applicable (including an
      untracked worktree file or a file deleted at that revision).
    """

    normalized = {
        path for path in (normalize_relative_path(item) for item in target_paths) if path
    }
    if not revision:
        return _unavailable_history_result(
            normalized, "analyzed_revision_unavailable"
        )

    try:
        identity = _git(repository, ["rev-parse", "--git-dir"])
    except FileNotFoundError:
        return _unavailable_history_result(
            normalized, "git_executable_unavailable"
        )
    except (OSError, subprocess.SubprocessError):
        return _unavailable_history_result(normalized, "git_history_read_failed")
    if identity.returncode != 0:
        return _unavailable_history_result(
            normalized,
            "not_a_git_repository",
            worktree_state=NOT_APPLICABLE,
        )

    try:
        bare = _git(repository, ["rev-parse", "--is-bare-repository"])
        if bare.returncode == 0 and bare.stdout.strip() == b"true":
            worktree_state = NOT_APPLICABLE
        else:
            state = _git(repository, ["status", "--porcelain=v1", "-z"])
            worktree_state = (
                UNAVAILABLE
                if state.returncode != 0
                else ("dirty" if state.stdout else "clean")
            )

        verified = _git(repository, ["cat-file", "-e", f"{revision}^{{commit}}"])
        if verified.returncode != 0:
            return _unavailable_history_result(
                normalized,
                "analyzed_revision_missing",
                worktree_state=worktree_state,
            )

        shallow_probe = _git(repository, ["rev-parse", "--is-shallow-repository"])
        if shallow_probe.returncode != 0:
            return _unavailable_history_result(
                normalized,
                "shallow_state_unavailable",
                worktree_state=worktree_state,
            )
    except (OSError, subprocess.SubprocessError):
        return _unavailable_history_result(
            normalized, "git_history_read_failed"
        )
    shallow = shallow_probe.stdout.strip() == b"true"
    if shallow:
        return _unavailable_history_result(
            normalized,
            "shallow_history",
            worktree_state=worktree_state,
            shallow=True,
        )

    try:
        tree = _git(repository, ["ls-tree", "-r", "--name-only", "-z", revision])
    except (OSError, subprocess.SubprocessError):
        return _unavailable_history_result(
            normalized,
            "analyzed_tree_unavailable",
            worktree_state=worktree_state,
            shallow=False,
        )
    if tree.returncode != 0:
        return _unavailable_history_result(
            normalized,
            "analyzed_tree_unavailable",
            worktree_state=worktree_state,
            shallow=False,
        )
    tracked = {
        path
        for path in (_decode_path(item) for item in tree.stdout.split(b"\0"))
        if path is not None
    }
    applicable = normalized & tracked
    files = {
        path: (
            _history_file(MEASURED)
            if path in applicable
            else _history_file(NOT_APPLICABLE, "not_tracked_at_analyzed_revision")
        )
        for path in sorted(normalized)
    }
    if applicable:
        try:
            log = _git(
                repository,
                [
                    "log",
                    "-z",
                    "--format=tformat:ARCHLENS_COMMIT:%H",
                    "--numstat",
                    "--find-renames=100%",
                    "--diff-merges=first-parent",
                    revision,
                ],
            )
        except (OSError, subprocess.SubprocessError):
            return _unavailable_history_result(
                normalized,
                "git_log_failed",
                worktree_state=worktree_state,
                shallow=False,
            )
        if log.returncode != 0:
            return _unavailable_history_result(
                normalized,
                "git_log_failed",
                worktree_state=worktree_state,
                shallow=False,
            )
        files.update(_parse_git_log(log.stdout, applicable))

    return {
        "status": MEASURED,
        "unavailable_reason": None,
        "repository_worktree_state": worktree_state,
        "shallow": False,
        "files": files,
    }


def _history_unavailable(paths: Iterable[str], reason: str) -> dict[str, Any]:
    return _unavailable_history_result(paths, reason)


def _complexity_by_file(
    callable_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    from modules.callable_ledger import aggregate_rows
    from modules.complexity_view import cognitive_aggregate

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in callable_rows:
        path = normalize_relative_path(row.get("relative_path"))
        if path is None:
            continue
        key = (subject_key_of(dict(row)), path)
        grouped.setdefault(key, []).append(row)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped.items():
        structural = aggregate_rows(rows)
        cognitive = cognitive_aggregate(rows)
        found[key] = {
            "cognitive_complexity_total": cognitive.get(
                "cognitive_complexity_total"
            ),
            "cognitive_complexity_max": cognitive.get("cognitive_complexity_max"),
            "cyclomatic_complexity_total": structural.get(
                "cyclomatic_complexity_total"
            ),
            "cyclomatic_complexity_max": structural.get(
                "cyclomatic_complexity_max"
            ),
            "max_nesting_depth_max": structural.get("max_nesting_depth_max"),
        }
    return found


def _reason_for_signal(label: str, evidence: Mapping[str, Any]) -> str:
    signal = evidence.get("signal")
    observed = evidence.get("observed")
    cohort = evidence.get("cohort") or {}
    return (
        f"{signal} relative {label}: observed {observed}; distinct-value rank "
        f"{cohort.get('distinct_rank')} of {cohort.get('distinct_value_count')}"
    )


#: The closed attention-class vocabulary, plus the explicit label for a row
#: whose two signals were not both measured. `unclassified` is a label, never a
#: fourth class: `classify_attention` returns ``None`` there, and folding that
#: into `low_attention` would report an unmeasured file as a calm one.
ATTENTION_CLASSES: tuple[str, ...] = (
    HIGH_ATTENTION, MODERATE_ATTENTION, LOW_ATTENTION,
)

UNCLASSIFIED = "unclassified"

ATTENTION_COUNT_LABELS: tuple[str, ...] = (*ATTENTION_CLASSES, UNCLASSIFIED)


def attention_class_counts(
    document: Mapping[str, Any], *, subject_key: str | None = None
) -> dict[str, int]:
    """Count rows per attention class. **The one definition of this tally.**

    Every consumer that needs "how many high-attention files" calls this. The
    dossier does, and so does the Policy evaluator: a second implementation is
    how two documents come to report two different numbers for one word, and a
    regression test asserts the two agree.

    Nothing is re-derived. The value read is the persisted ``classification``
    field, which :func:`validate_hotspot_document` has already checked against
    :func:`classify_attention` of that row's own signals. A row carrying
    ``null`` there is counted under :data:`UNCLASSIFIED`, never dropped and
    never folded into ``low_attention``.

    The tally is **total over the closed vocabulary**: every label is present,
    including the ones that are zero. A zero here is a MEASURED zero -- the
    document enumerates every file it classified, so "no high-attention file"
    is an answer rather than an absence. Callers that must distinguish an
    absence (no document at all, or a subject this document does not cover) do
    that before calling; there is no way to express it in a count, and no
    caller may invent one.

    ``subject_key`` restricts the tally to one subject. One document can cover
    several, and a per-subject question must never be answered with a
    cross-subject total.
    """
    counts = {label: 0 for label in ATTENTION_COUNT_LABELS}
    for entry in document.get("hotspots") or ():
        if not isinstance(entry, Mapping):
            continue
        if subject_key is not None and str(
            entry.get("subject_key") or ""
        ) != subject_key:
            continue
        classification = entry.get("classification")
        label = str(classification) if classification else UNCLASSIFIED
        if label not in counts:
            # A class outside the published vocabulary cannot be counted as one
            # of them. It is surfaced under its own label rather than merged.
            counts[label] = 0
        counts[label] += 1
    return counts


def _file_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _CLASS_ORDER.get(item.get("classification"), 3),
        _SIGNAL_ORDER.get((item.get("complexity_signal") or {}).get("signal"), 3),
        _SIGNAL_ORDER.get((item.get("churn_signal") or {}).get("signal"), 3),
        str(item.get("subject_key") or ""),
        str(item.get("file") or ""),
    )


def validate_hotspot_document(document: Mapping[str, Any]) -> None:
    """Validate the bounded output model before any bytes are emitted.

    This validator protects derived-analysis semantics only.  It is deliberately
    not registered as a run-artifact schema: a hotspot document is not an
    authoritative measurement artifact and cannot be read back as one.
    """

    if document.get("format") != HOTSPOT_FORMAT:
        raise HotspotValidationError("unexpected hotspot format")
    if document.get("format_version") != HOTSPOT_FORMAT_VERSION:
        raise HotspotValidationError("unsupported hotspot format version")
    model = document.get("classification_model")
    if not isinstance(model, Mapping):
        raise HotspotValidationError("classification_model must be an object")
    if model.get("score") is not None or model.get("thresholds") is not None:
        raise HotspotValidationError("scores and fixed thresholds are forbidden")
    hotspots = document.get("hotspots")
    if not isinstance(hotspots, list):
        raise HotspotValidationError("hotspots must be an array")
    if hotspots != sorted(hotspots, key=_file_sort_key):
        raise HotspotValidationError("hotspot ordering is not canonical")

    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(hotspots):
        if not isinstance(item, Mapping):
            raise HotspotValidationError(f"hotspots[{index}] must be an object")
        path = item.get("file")
        if normalize_relative_path(path) != path:
            raise HotspotValidationError(
                f"hotspots[{index}].file is not a portable relative path"
            )
        identity = (str(item.get("subject_key") or ""), str(path))
        if identity in identities:
            raise HotspotValidationError(f"duplicate hotspot identity {identity!r}")
        identities.add(identity)

        complexity = item.get("complexity")
        churn = item.get("churn")
        complexity_signal = item.get("complexity_signal")
        churn_signal = item.get("churn_signal")
        if not all(
            isinstance(value, Mapping)
            for value in (complexity, churn, complexity_signal, churn_signal)
        ):
            raise HotspotValidationError(
                f"hotspots[{index}] evidence blocks must be objects"
            )
        for label, evidence in (
            ("complexity", complexity),
            ("churn", churn),
            ("complexity_signal", complexity_signal),
            ("churn_signal", churn_signal),
        ):
            if evidence.get("status") not in {MEASURED, UNAVAILABLE, NOT_APPLICABLE}:
                raise HotspotValidationError(
                    f"hotspots[{index}].{label} has an invalid status"
                )

        cognitive = complexity.get("cognitive_complexity_total")
        if complexity.get("status") == MEASURED and not isinstance(cognitive, int):
            raise HotspotValidationError(
                f"hotspots[{index}] measured complexity needs an integer value"
            )
        commits = churn.get("commits")
        if churn.get("status") == MEASURED:
            if not isinstance(commits, int) or commits < 0:
                raise HotspotValidationError(
                    f"hotspots[{index}] measured churn needs non-negative commits"
                )
        elif commits is not None:
            raise HotspotValidationError(
                f"hotspots[{index}] unavailable churn cannot carry zero"
            )

        for label, evidence in (
            ("complexity_signal", complexity_signal),
            ("churn_signal", churn_signal),
        ):
            signal = evidence.get("signal")
            if evidence.get("status") == MEASURED:
                if signal not in {LOW, MEDIUM, HIGH}:
                    raise HotspotValidationError(
                        f"hotspots[{index}].{label} has no measured signal"
                    )
                cohort = evidence.get("cohort")
                if not isinstance(cohort, Mapping):
                    raise HotspotValidationError(
                        f"hotspots[{index}].{label} has no rank evidence"
                    )
            elif signal is not None or evidence.get("cohort") is not None:
                raise HotspotValidationError(
                    f"hotspots[{index}].{label} fabricates unavailable evidence"
                )

        expected = classify_attention(
            complexity_signal.get("signal"), churn_signal.get("signal")
        )
        if item.get("classification") != expected:
            raise HotspotValidationError(
                f"hotspots[{index}] classification contradicts its signals"
            )
        reasons = item.get("reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
        ):
            raise HotspotValidationError(
                f"hotspots[{index}] classification is not explained"
            )


def analyze_hotspots(
    view: Any,
    history_sources: Mapping[str, HistorySource],
    *,
    missing_source_reasons: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> HotspotAnalysis:
    """Build one deterministic hotspot document from a canonical run view."""

    missing_source_reasons = missing_source_reasons or {}
    artifact_start = clock()
    repositories = [dict(item) for item in view.repositories]
    contributions = (
        [dict(item) for item in view.stream_contributions()]
        if getattr(view, "has_contribution_ledger", False)
        else []
    )
    callables = (
        [dict(item) for item in view.stream_callables()]
        if getattr(view, "has_callable_artifact", False)
        else []
    )
    artifact_seconds = clock() - artifact_start

    contribution_by_repository: dict[str, dict[str, dict[str, Any]]] = {}
    for row in contributions:
        path = normalize_relative_path(row.get("relative_path"))
        if path is None or row.get("source_files_contribution") not in {1, True}:
            continue
        subject = subject_key_of(row)
        contribution_by_repository.setdefault(subject, {}).setdefault(path, row)
    complexity = _complexity_by_file(callables)

    history_start = clock()
    history_by_repository: dict[str, dict[str, Any]] = {}
    history_metadata: dict[str, dict[str, Any]] = {}
    ordered_repositories = sorted(
        repositories, key=lambda item: subject_key_of(item)
    )
    for repository in ordered_repositories:
        subject = subject_key_of(repository)
        paths = set(contribution_by_repository.get(subject, {}))
        source = history_sources.get(subject)
        if source is None:
            reason = missing_source_reasons.get(subject, "git_history_source_unavailable")
            history = _history_unavailable(paths, reason)
            source_kind = None
        else:
            revision = (repository.get("acquisition") or {}).get(
                "analyzed_commit_sha"
            )
            history = extract_git_history(source.path, revision, paths)
            source_kind = source.kind
        history_by_repository[subject] = history
        history_metadata[subject] = {
            "status": history["status"],
            "unavailable_reason": history["unavailable_reason"],
            "source": source_kind,
            "repository_worktree_state": history["repository_worktree_state"],
            "artifact_working_tree_state": repository.get("working_tree_state")
            or NOT_APPLICABLE,
            "shallow": history["shallow"],
        }
    history_seconds = clock() - history_start

    computation_start = clock()
    raw_files: list[dict[str, Any]] = []
    for repository in ordered_repositories:
        subject = subject_key_of(repository)
        acquisition = repository.get("acquisition") or {}
        rows = contribution_by_repository.get(subject, {})
        history_files = history_by_repository[subject]["files"]
        for path, contribution in sorted(rows.items()):
            structural_status = contribution.get("structural_complexity_status")
            values = complexity.get((subject, path), {})
            cognitive_total = values.get("cognitive_complexity_total")
            if structural_status == "not_applicable":
                complexity_status = NOT_APPLICABLE
                complexity_reason = "complexity_not_applicable"
            elif isinstance(cognitive_total, int):
                complexity_status = MEASURED
                complexity_reason = None
            else:
                complexity_status = UNAVAILABLE
                complexity_reason = "cognitive_complexity_unavailable"
            churn = history_files.get(
                path, _history_file(UNAVAILABLE, "git_history_unavailable")
            )
            raw_files.append(
                {
                    "subject_key": subject,
                    "repository_url": repository.get("repository_url"),
                    "analyzed_commit_sha": acquisition.get("analyzed_commit_sha")
                    or None,
                    "file": path,
                    "language": contribution.get("detected_language") or None,
                    "complexity": {
                        "status": complexity_status,
                        "unavailable_reason": complexity_reason,
                        "measurement_status": structural_status or UNAVAILABLE,
                        "cognitive_complexity_total": cognitive_total,
                        "cognitive_complexity_max": values.get(
                            "cognitive_complexity_max"
                        ),
                        "cyclomatic_complexity_total": values.get(
                            "cyclomatic_complexity_total"
                        ),
                        "cyclomatic_complexity_max": values.get(
                            "cyclomatic_complexity_max"
                        ),
                        "max_nesting_depth_max": values.get(
                            "max_nesting_depth_max"
                        ),
                        "code_lines": contribution.get("lines_of_code"),
                        "callable_count": contribution.get("callable_count"),
                    },
                    "churn": churn,
                }
            )

    complexity_cohorts: dict[tuple[str, str], list[int]] = {}
    churn_cohorts: dict[str, list[int]] = {}
    for item in raw_files:
        cognitive = item["complexity"]["cognitive_complexity_total"]
        if item["complexity"]["status"] == MEASURED and isinstance(cognitive, int):
            key = (item["subject_key"], str(item.get("language") or "unknown"))
            complexity_cohorts.setdefault(key, []).append(cognitive)
        commits = item["churn"].get("commits")
        if item["churn"].get("status") == MEASURED and isinstance(commits, int):
            churn_cohorts.setdefault(item["subject_key"], []).append(commits)

    hotspots: list[dict[str, Any]] = []
    for item in raw_files:
        complexity_key = (
            item["subject_key"],
            str(item.get("language") or "unknown"),
        )
        complexity_signal = signal_for(
            item["complexity"].get("cognitive_complexity_total"),
            complexity_cohorts.get(complexity_key, ()),
        )
        if item["complexity"].get("status") != MEASURED:
            complexity_signal["status"] = item["complexity"].get("status")
            complexity_signal["signal"] = None
            complexity_signal["cohort"] = None
        churn_signal = signal_for(
            item["churn"].get("commits"),
            churn_cohorts.get(item["subject_key"], ()),
        )
        if item["churn"].get("status") != MEASURED:
            churn_signal["status"] = item["churn"].get("status")
            churn_signal["signal"] = None
            churn_signal["cohort"] = None

        classification = classify_attention(
            complexity_signal.get("signal"), churn_signal.get("signal")
        )
        reasons: list[str] = []
        if complexity_signal.get("status") == MEASURED:
            reasons.append(
                _reason_for_signal("cognitive complexity", complexity_signal)
            )
        else:
            reasons.append(
                "complexity signal unavailable: "
                f"{item['complexity'].get('unavailable_reason') or complexity_signal.get('status')}"
            )
        if churn_signal.get("status") == MEASURED:
            reasons.append(_reason_for_signal("touching-commit frequency", churn_signal))
        else:
            reasons.append(
                "churn signal unavailable: "
                f"{item['churn'].get('unavailable_reason') or churn_signal.get('status')}"
            )
        if classification is None:
            reasons.append(
                "attention class unavailable because both measured signals are required"
            )
        else:
            reasons.append(
                "attention class follows the published two-signal classification table"
            )
        item["complexity_signal"] = complexity_signal
        item["churn_signal"] = churn_signal
        item["classification"] = classification
        item["reasons"] = reasons
        hotspots.append(item)

    hotspots.sort(key=_file_sort_key)
    repository_documents = []
    for repository in ordered_repositories:
        subject = subject_key_of(repository)
        selected = [item for item in hotspots if item["subject_key"] == subject]
        repository_documents.append(
            {
                "subject_key": subject,
                "repository_url": repository.get("repository_url"),
                "analyzed_commit_sha": (repository.get("acquisition") or {}).get(
                    "analyzed_commit_sha"
                )
                or None,
                "history": history_metadata[subject],
                "file_count": len(selected),
                "classified_file_count": sum(
                    item["classification"] is not None for item in selected
                ),
            }
        )

    document = {
        "format": HOTSPOT_FORMAT,
        "format_version": HOTSPOT_FORMAT_VERSION,
        "product_name": "Metrolith",
        "program_version": PROGRAM_VERSION,
        "source_run": {
            "run_id": view.run_id,
            "program_version": view.manifest.get("program_version"),
            "artifact_schema_version": view.manifest.get("artifact_schema_version"),
        },
        "purpose": (
            "Prioritize maintenance attention for files that are both relatively "
            "complex and frequently changed; this is not a bug prediction or "
            "a code-quality score."
        ),
        "classification_model": {
            "score": None,
            "thresholds": None,
            "complexity_signal": (
                "distinct-value rank of existing cognitive_complexity_total "
                "within the same repository and detected language"
            ),
            "churn_signal": (
                "distinct-value rank of commits touching the exact-content rename "
                "lineage within the same repository"
            ),
            "one_distinct_value": MEDIUM,
            "attention_classes": {
                HIGH_ATTENTION: "high complexity signal and high churn signal",
                MODERATE_ATTENTION: (
                    "either signal high, or both signals medium"
                ),
                LOW_ATTENTION: "all other pairs of measured signals",
            },
            "unavailable": "no class unless both signals are measured",
        },
        "git_semantics": {
            "history": "all commits reachable from the analyzed revision",
            "commit_touch": (
                "one commit with at least one numstat change on the file lineage; "
                "merge commits are compared with their first parent"
            ),
            "churn": (
                "inserted plus deleted text lines, including creation; binary "
                "changes make touched_lines unavailable but retain commit count"
            ),
            "renames": "exact-content renames only (-M100%)",
            "shallow_history": UNAVAILABLE,
            "absent_at_revision": NOT_APPLICABLE,
            "dirty_worktree": (
                "does not alter committed history; tracked paths are evaluated at "
                "the analyzed revision and untracked paths are not applicable"
            ),
        },
        "ordering": (
            "attention class, complexity signal, churn signal, subject key, file path"
        ),
        "repositories": repository_documents,
        "hotspots": hotspots,
    }
    validate_hotspot_document(document)
    computation_seconds = clock() - computation_start
    return HotspotAnalysis(
        document=document,
        timings={
            "artifact_loading_seconds": artifact_seconds,
            "git_history_extraction_seconds": history_seconds,
            "hotspot_computation_seconds": computation_seconds,
        },
    )
