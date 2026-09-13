"""Direct Revision Diff — what intentionally changed between two source states.

``compare`` and ``diff`` answer different questions and are deliberately kept
apart:

* ``compare`` asks whether two runs that *should* represent equivalent
  measurement are semantically consistent. It is the lower-level, locator-keyed
  run-to-run primitive with its own published output contract.
* ``diff`` asks what changed between two revisions or snapshots of **one
  logical subject**. It resolves both sides, establishes comparability, and
  then explains the differences it found.

``diff`` never computes a metric. Each side is resolved to an Artifact 1.7 run
through the *canonical* acquisition → inventory → measurement → artifact
machinery, exactly as ``analyze`` and the cohort runner do. There is no second
decomposition path, no second metric implementation, and no second source
selection.

Four things are kept strictly separate, because collapsing any pair of them is
how a diff starts lying:

``subject_key``
    Logical identity. Two sides with different subject keys are not a revision
    diff, and saying so is a refusal rather than a silent comparison.
``revision / source identity``
    *Where* the bytes came from. Same subject never implies same source.
``analysis_scope_hash``
    Exact identity of the analyzed scope — *which bytes were parsed*. A changed
    scope hash means the analyzed bytes or scope changed. It is **not** a
    comparability verdict, and inequality alone never means incomparable.
``measurement-contract compatibility``
    Whether the two sides' numbers are defined the same way. This, and only
    this, decides whether metric deltas may be presented at all.

Evidence levels are inherited from ``compare`` rather than reinvented: an
observed difference is never upgraded to an explanation, and absent evidence is
reported as absent rather than as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.cli.compare_command import (
    EXACT_RECONCILIATION,
    NOT_EVALUABLE,
    OBSERVED_DIFFERENCE,
    UNEXPLAINED_RESIDUAL,
)

#: Independent format version for `diff.json`. Not the Artifact Schema version:
#: this is a derived, explicitly non-authoritative output with its own contract
#: and its own producer-to-schema test.
# 1.1.0 adds complexity: its own comparability verdict plus deltas at the
# repository, language and file levels. The document is
# `additionalProperties: false`, so new fields are a new format version; 1.0 is
# retained byte-for-byte as historical material.
# 1.2.0 adds the cognitive verdict and its three delta levels. The document is
# `additionalProperties: false`, so a new field is a new version; 1.1 is
# retained byte-for-byte as historical material.
REVISION_DIFF_FORMAT_VERSION = "1.2.0"

# Exit codes, aligned with `compare --explain` so a caller scripting both does
# not have to learn two vocabularies.
EXIT_NO_DIFFERENCE = 0
EXIT_DIFFERENCES = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3
EXIT_REFUSED = 4

RESULT_COMPLETED = "comparison_completed"
RESULT_REFUSED = "comparison_refused"

#: Benchmark aggregate dimensions, governed by Metric Contract 3.0.0.
#: Complexity is NOT added here on purpose: widening this tuple would silently
#: alter existing four-metric attribution and would make a complexity
#: difference look like a benchmark-metric difference. Complexity travels in a
#: separate list under its own comparability verdict.
AGGREGATE_DIMENSIONS = (
    "source_files", "lines_of_code", "classes_structs", "methods_functions",
)

#: Complexity Contract 1.0.0 aggregates, at the three approved levels:
#: repository, language and file. There is deliberately NO callable-level
#: matching in Direct Diff v1 -- `callable_row_id` is within-artifact row
#: identity and is not stable across revisions, so pairing on it would invent
#: a correspondence the contract explicitly disclaims.
COMPLEXITY_AGGREGATE_DIMENSIONS = (
    "callable_count",
    "cyclomatic_complexity_total",
    "cyclomatic_complexity_mean",
    "cyclomatic_complexity_median",
    "cyclomatic_complexity_max",
    "nloc_median",
    "nloc_max",
    "max_nesting_depth_median",
    "max_nesting_depth_max",
    "formal_parameter_count_median",
    "formal_parameter_count_max",
)

#: The one per-file complexity quantity the contribution ledger carries.
COMPLEXITY_FILE_DIMENSIONS = ("callable_count",)

#: Per-file complexity statuses, reported as transitions rather than deltas.
COMPLEXITY_FILE_STATUS_DIMENSIONS = (
    "structural_complexity_status", "nloc_status",
)
STATUS_DIMENSIONS = (
    "metric_status", "inventory_status", "source_files_status", "loc_status",
    "classes_structs_status", "methods_functions_status",
)
REPOSITORY_STATUS_DIMENSIONS = (
    "analysis_status", "core_metric_status", "expected_language_family_status",
    "partial_origin", "git_mode_map_status",
)
SOURCE_IDENTITY_DIMENSIONS = (
    "source_mode", "requested_revision", "analyzed_commit_sha",
    "analysis_scope_hash", "working_tree_state", "filesystem_manifest_hash",
    "subject_key_basis", "repository_url",
)

#: Ledger fields carrying each aggregate dimension's per-file contribution.
_LEDGER_FIELD = {
    "lines_of_code": "lines_of_code",
    "classes_structs": "classes_structs",
    "methods_functions": "methods_functions",
    "source_files": "source_files_contribution",
}

# Rename evidence is typed and never inferred from similarity. Only an exact
# content match that is one-to-one on both sides is called a rename; anything
# else stays an ambiguous candidate the reader can adjudicate.
RENAME_EXACT = "exact_content_rename"
RENAME_AMBIGUOUS = "ambiguous_content_match"


class DiffRefused(RuntimeError):
    """Raised when the two sides must not be diffed at all.

    Carries the structured reasons so the caller reports evidence rather than a
    bare message.
    """

    def __init__(self, reasons: Sequence[Mapping[str, Any]]):
        self.reasons = list(reasons)
        super().__init__("; ".join(str(item.get("detail")) for item in self.reasons))


@dataclass(frozen=True)
class SideSpec:
    """One resolved side of a diff, before any analysis has run."""

    label: str
    #: How the bytes will be obtained: mirrors `SourceMode` once analyzed.
    kind: str
    source: str | None = None
    revision: str | None = None
    run_directory: Path | None = None
    tracked_only: bool = False
    subject_key: str | None = None

    def describe(self) -> str:
        if self.kind == "existing_run":
            return f"run:{self.run_directory}"
        if self.kind == "worktree":
            return f"worktree:{self.source}"
        if self.kind == "remote_revision":
            return f"remote:{self.source}#{self.revision}"
        return f"revision:{self.revision}"


@dataclass
class ResolvedSide:
    """One side after it has been reduced to an Artifact 1.7 run."""

    spec: SideSpec
    run_directory: Path
    view: Any
    repository: Mapping[str, Any]
    #: Identity as recorded in the artifact — the override, when one was given.
    subject_key: str
    #: Identity this side resolves to on its own evidence, before any override.
    #: Kept separate so an override can never make the report claim the two
    #: sides were naturally the same subject when they were not.
    natural_subject_key: str | None = None
    #: True when this side was analyzed now rather than read from an existing run.
    freshly_analyzed: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        """The key used for the same-subject decision."""
        return self.natural_subject_key or self.subject_key


# --------------------------------------------------------------------------
# Side specifiers
# --------------------------------------------------------------------------

def parse_side(value: str, label: str, *, source: str | None,
               tracked_only: bool = False) -> SideSpec:
    """Parse one ``--from`` / ``--to`` side specifier.

    One uniform grammar rather than a flag per combination, because every side
    is the same concept — "which source state" — and a flag matrix would make
    the supported combinations implicit:

    ``worktree``
        The current working tree of ``source``, captured as an
        Metrolith-controlled snapshot. Live files are never parsed in place.
    ``run:<directory>``
        A Metrolith run that already exists. Nothing is re-measured.
    ``remote:<url>#<revision>``
        A revision of a remote repository, through the normal acquisition and
        cache path.
    anything else
        A Git revision of ``source`` — a SHA, tag, or branch. Materialized with
        ``git archive``; the user's repository is never modified.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError(f"--{label} requires a side specifier")

    if text.startswith("run:"):
        directory = text[len("run:"):].strip()
        if not directory:
            raise ValueError(f"--{label} run: requires a run directory")
        return SideSpec(label=label, kind="existing_run",
                        run_directory=Path(directory).expanduser())

    if text.startswith("remote:"):
        locator = text[len("remote:"):].strip()
        url, separator, revision = locator.partition("#")
        if not separator or not revision.strip():
            raise ValueError(
                f"--{label} remote: requires <url>#<revision>; a remote side "
                f"without an explicit revision would not be reproducible"
            )
        return SideSpec(label=label, kind="remote_revision",
                        source=url.strip(), revision=revision.strip())

    if text == "worktree":
        if source is None:
            raise ValueError(
                f"--{label} worktree requires a source path positional argument"
            )
        return SideSpec(label=label, kind="worktree", source=source,
                        tracked_only=tracked_only)

    if source is None:
        raise ValueError(
            f"--{label} {text!r} names a Git revision, which requires a source "
            f"path positional argument"
        )
    return SideSpec(label=label, kind="revision", source=source, revision=text,
                    tracked_only=tracked_only)


def side_to_repository_spec(spec: SideSpec, *, subject_key: str | None,
                            expected_language: str | None,
                            architecture_type: str):
    """Build the canonical ``RepositorySpec`` for one side to be analyzed."""
    from modules.repository_input import RepositorySpec

    if spec.kind == "remote_revision":
        return RepositorySpec(
            url=spec.source or "",
            architecture_type=architecture_type,
            expected_language=expected_language,
            commit_sha=spec.revision,
            enabled=True,
            subject_key=subject_key,
        )
    return RepositorySpec(
        url="",
        architecture_type=architecture_type,
        expected_language=expected_language,
        enabled=True,
        local_path=str(Path(spec.source or ".").expanduser().resolve()),
        revision=spec.revision if spec.kind == "revision" else None,
        tracked_only=spec.tracked_only,
        subject_key=subject_key,
    )


# --------------------------------------------------------------------------
# Comparability
# --------------------------------------------------------------------------

def _dimension(name: str, from_value: Any, to_value: Any, *, blocking: bool,
               rationale: str) -> dict[str, Any]:
    compatible = from_value == to_value
    return {
        "dimension": name,
        "from": from_value,
        "to": to_value,
        "compatible": compatible,
        "blocking": blocking,
        "rationale": rationale if not compatible else None,
    }


def assess_comparability(left: ResolvedSide, right: ResolvedSide, *,
                         subject_override: str | None = None) -> dict[str, Any]:
    """Structured comparability verdict, produced before any metric delta.

    Blocking dimensions are only those that change what a metric *means*. The
    analyzed scope and the revision are reported but never blocking: a diff
    exists precisely because they differ.
    """
    left_manifest = left.view.manifest
    right_manifest = right.view.manifest

    dimensions: list[dict[str, Any]] = [
        # Judged on the identity each side resolves to *on its own evidence*.
        # An override declares the sides comparable; it must not be allowed to
        # manufacture the agreement it is claiming.
        _dimension(
            "subject_key", left.identity, right.identity,
            blocking=subject_override is None,
            rationale=(
                "the two sides resolve to different logical subjects, so this "
                "is not a revision diff of one subject. Supply --subject-key to "
                "declare that they are the same subject."
            ),
        ),
        _dimension(
            "metric_contract_version",
            left_manifest.get("metric_contract_version"),
            right_manifest.get("metric_contract_version"),
            blocking=True,
            rationale=(
                "metric values defined by different Metric Contracts are not "
                "comparable; the numbers do not mean the same thing"
            ),
        ),
        _dimension(
            "exclusion_policy_version",
            left_manifest.get("exclusion_policy_version"),
            right_manifest.get("exclusion_policy_version"),
            blocking=True,
            rationale=(
                "the Exclusion Policy decides which files are counted at all, so "
                "a policy change reports a metric change where only the "
                "selection rule changed"
            ),
        ),
        _dimension(
            "exclusion_policy_sha256",
            left_manifest.get("exclusion_policy_sha256"),
            right_manifest.get("exclusion_policy_sha256"),
            blocking=True,
            rationale="the Exclusion Policy bytes differ under the same version",
        ),
        _dimension(
            "inventory_schema_version",
            left_manifest.get("inventory_schema_version"),
            right_manifest.get("inventory_schema_version"),
            blocking=False,
            rationale=(
                "inventory schemas differ; per-file evidence dimensions may not "
                "be directly comparable"
            ),
        ),
        _dimension(
            "artifact_schema_version",
            left_manifest.get("artifact_schema_version"),
            right_manifest.get("artifact_schema_version"),
            blocking=False,
            rationale=(
                "artifact generations differ; a field one side records may be "
                "absent on the other and is reported as unavailable"
            ),
        ),
        # NON-BLOCKING, and that is the whole compatibility rule in one flag.
        # Complexity versions separately from the Metric Contract, so a side
        # that predates Complexity Contract 1.0.0 -- or carries a different one
        # -- must still yield the four benchmark-metric deltas. Only the
        # complexity deltas degrade, and they degrade to not-evaluable rather
        # than to zero.
        _dimension(
            "complexity_contract_version",
            left_manifest.get("complexity_contract_version"),
            right_manifest.get("complexity_contract_version"),
            blocking=False,
            rationale=(
                "complexity values defined by different Complexity Contracts "
                "are not comparable; complexity deltas are reported as "
                "not evaluable while every other comparison proceeds"
            ),
        ),
        _dimension(
            "parser_language_contract",
            _parser_contract(left.repository),
            _parser_contract(right.repository),
            blocking=False,
            rationale=(
                "parser status differs by language; a metric change may reflect "
                "parsing capability rather than source change"
            ),
        ),
    ]

    # Reported, never blocking. A diff is the case where these differ.
    for name in ("analyzed_commit_sha", "analysis_scope_hash", "source_mode"):
        dimensions.append({
            "dimension": name,
            "from": left.repository.get(name)
            if name != "analyzed_commit_sha"
            else (left.repository.get("acquisition") or {}).get(name),
            "to": right.repository.get(name)
            if name != "analyzed_commit_sha"
            else (right.repository.get("acquisition") or {}).get(name),
            "compatible": True,
            "blocking": False,
            "rationale": None,
            "note": (
                "source identity, reported as evidence. A difference here is "
                "what a diff exists to describe and never implies incomparable."
            ),
        })

    refusals = [item for item in dimensions if item["blocking"] and not item["compatible"]]
    return {
        "comparable": not refusals,
        "result": RESULT_REFUSED if refusals else RESULT_COMPLETED,
        "subject_key_override": subject_override,
        "dimensions": dimensions,
        "refusal_reasons": [
            {"dimension": item["dimension"], "from": item["from"],
             "to": item["to"], "detail": item["rationale"]}
            for item in refusals
        ],
    }


def _parser_contract(repository: Mapping[str, Any]) -> Any:
    metrics = repository.get("metrics") or {}
    statuses = metrics.get("parser_status_by_language")
    if not isinstance(statuses, Mapping):
        return None
    return dict(sorted(statuses.items()))


# --------------------------------------------------------------------------
# Per-file evidence
# --------------------------------------------------------------------------

def _ledger_by_path(view, subject_key: str) -> dict[str, Mapping[str, Any]] | None:
    """Per-file contribution rows for one subject, keyed by relative path.

    Returns ``None`` when the run carries no ledger, which makes every
    file-level dimension ``not_evaluable`` rather than empty.
    """
    if not view.has_contribution_ledger:
        return None
    from modules.subject import subject_key_of

    found: dict[str, Mapping[str, Any]] = {}
    for row in view.stream_contributions():
        if subject_key_of(dict(row)) != subject_key:
            continue
        path = row.get("relative_path")
        if not path:
            continue
        found[str(path)] = row
    return found


def _inventory_by_path(view, repository: Mapping[str, Any]) -> dict[str, Mapping[str, Any]] | None:
    """Per-file inventory records for one repository, keyed by relative path."""
    slug = f"{repository.get('repository_owner')}__{repository.get('repository_name')}"
    document = view.inventories.get(f"file_inventory/{slug}.json")
    if document is None:
        return None
    found: dict[str, Mapping[str, Any]] = {}
    for record in document.get("files", ()):
        path = record.get("relative_path")
        if path:
            found[str(path)] = record
    return found


def _content_hash(ledger_row: Mapping[str, Any] | None,
                  inventory_record: Mapping[str, Any] | None) -> str | None:
    if ledger_row is not None and ledger_row.get("content_sha256"):
        return str(ledger_row["content_sha256"])
    if inventory_record is not None and inventory_record.get("content_hash"):
        return str(inventory_record["content_hash"])
    return None


def _numeric(value: Any) -> int | None:
    """Coerce a ledger cell to an int, or ``None`` for an unavailable value.

    A blank or unparseable cell means the value was not recorded. Treating it
    as zero would silently invent a contribution.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_file_evidence(left: ResolvedSide, right: ResolvedSide) -> dict[str, Any]:
    """Files added, removed and content-changed, with typed rename evidence."""
    left_ledger = _ledger_by_path(left.view, left.subject_key)
    right_ledger = _ledger_by_path(right.view, right.subject_key)
    left_inventory = _inventory_by_path(left.view, left.repository)
    right_inventory = _inventory_by_path(right.view, right.repository)

    left_paths = set(left_ledger or {}) | set(left_inventory or {})
    right_paths = set(right_ledger or {}) | set(right_inventory or {})

    if not left_paths or not right_paths:
        missing = [
            side.spec.label
            for side, paths in ((left, left_paths), (right, right_paths))
            if not paths
        ]
        return {
            "evaluable": False,
            "not_evaluable_reason": (
                f"per-file evidence requires a contribution ledger or inventory "
                f"on both sides; absent for: {', '.join(missing)}"
            ),
            "added": [], "removed": [], "content_changed": [],
            "unchanged_count": None, "renames": [],
            "counts": {"added": None, "removed": None, "content_changed": None},
        }

    def hash_of(path, ledger, inventory):
        return _content_hash(
            (ledger or {}).get(path), (inventory or {}).get(path)
        )

    added_paths = sorted(right_paths - left_paths)
    removed_paths = sorted(left_paths - right_paths)
    common = sorted(left_paths & right_paths)

    changed: list[dict[str, Any]] = []
    unchanged = 0
    unknown_hash = 0
    for path in common:
        before = hash_of(path, left_ledger, left_inventory)
        after = hash_of(path, right_ledger, right_inventory)
        if before is None or after is None:
            unknown_hash += 1
            continue
        if before == after:
            unchanged += 1
            continue
        changed.append({
            "relative_path": path,
            "content_hash_from": before,
            "content_hash_to": after,
        })

    added = [
        {"relative_path": path,
         "content_hash": hash_of(path, right_ledger, right_inventory)}
        for path in added_paths
    ]
    removed = [
        {"relative_path": path,
         "content_hash": hash_of(path, left_ledger, left_inventory)}
        for path in removed_paths
    ]

    return {
        "evaluable": True,
        "not_evaluable_reason": None,
        "added": added,
        "removed": removed,
        "content_changed": changed,
        "unchanged_count": unchanged,
        "indeterminate_content_count": unknown_hash,
        "renames": detect_renames(added, removed),
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "content_changed": len(changed),
        },
    }


def detect_renames(added: Sequence[Mapping[str, Any]],
                   removed: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rename evidence, typed and never inferred from similarity.

    Only an **exact content hash match that is one-to-one on both sides** is
    called a rename. When several removed or added files share one content hash
    the correspondence is genuinely unknown, and reporting a guess as identity
    would be a fabricated claim, so those are emitted as
    ``ambiguous_content_match`` candidates instead.

    No similarity or near-match heuristic exists here. Adding one would require
    stating its algorithm and confidence explicitly.
    """
    removed_by_hash: dict[str, list[str]] = {}
    added_by_hash: dict[str, list[str]] = {}
    for item in removed:
        digest = item.get("content_hash")
        if digest:
            removed_by_hash.setdefault(str(digest), []).append(str(item["relative_path"]))
    for item in added:
        digest = item.get("content_hash")
        if digest:
            added_by_hash.setdefault(str(digest), []).append(str(item["relative_path"]))

    found: list[dict[str, Any]] = []
    for digest in sorted(set(removed_by_hash) & set(added_by_hash)):
        sources = sorted(removed_by_hash[digest])
        targets = sorted(added_by_hash[digest])
        if len(sources) == 1 and len(targets) == 1:
            found.append({
                "evidence": RENAME_EXACT,
                "content_hash": digest,
                "from_path": sources[0],
                "to_path": targets[0],
                "candidate_from_paths": sources,
                "candidate_to_paths": targets,
            })
            continue
        found.append({
            "evidence": RENAME_AMBIGUOUS,
            "content_hash": digest,
            "from_path": None,
            "to_path": None,
            "candidate_from_paths": sources,
            "candidate_to_paths": targets,
        })
    return found


# --------------------------------------------------------------------------
# Metric deltas and attribution
# --------------------------------------------------------------------------

def _aggregate(repository: Mapping[str, Any]) -> Mapping[str, Any]:
    return (repository.get("metrics") or {}).get("aggregate") or {}


def _delta_entry(dimension: str, before: Any, after: Any) -> dict[str, Any]:
    if before is None or after is None:
        return {
            "dimension": dimension,
            "value_from": before,
            "value_to": after,
            "delta": None,
            "evidence_level": NOT_EVALUABLE,
            "not_evaluable_reason": (
                "a metric value is null on at least one side; null means "
                "unavailable and is never treated as zero"
            ),
        }
    return {
        "dimension": dimension,
        "value_from": before,
        "value_to": after,
        "delta": after - before,
        "evidence_level": OBSERVED_DIFFERENCE,
        "not_evaluable_reason": None,
    }


def build_aggregate_deltas(left: ResolvedSide, right: ResolvedSide) -> list[dict[str, Any]]:
    before = _aggregate(left.repository)
    after = _aggregate(right.repository)
    found = []
    for dimension in AGGREGATE_DIMENSIONS:
        entry = _delta_entry(dimension, before.get(dimension), after.get(dimension))
        if entry["delta"] == 0:
            continue
        if entry["delta"] is None and before.get(dimension) == after.get(dimension):
            continue
        found.append(entry)
    return found


def build_language_deltas(left: ResolvedSide, right: ResolvedSide) -> list[dict[str, Any]]:
    before = (left.repository.get("metrics") or {}).get("by_language") or {}
    after = (right.repository.get("metrics") or {}).get("by_language") or {}
    found: list[dict[str, Any]] = []
    for language in sorted(set(before) | set(after)):
        left_row = before.get(language) or {}
        right_row = after.get(language) or {}
        for dimension in AGGREGATE_DIMENSIONS:
            entry = _delta_entry(
                dimension, left_row.get(dimension), right_row.get(dimension)
            )
            if entry["delta"] == 0:
                continue
            if entry["delta"] is None and left_row.get(dimension) == right_row.get(dimension):
                continue
            entry["language"] = language
            found.append(entry)
    return found


# --------------------------------------------------------------------------
# Complexity Contract 1.0.0 deltas. A separate list under a separate verdict,
# so complexity can be unavailable without touching the benchmark comparison.
# --------------------------------------------------------------------------

def assess_complexity_comparability(
    left: ResolvedSide, right: ResolvedSide
) -> dict[str, Any]:
    """Whether complexity deltas mean anything, decided on its own evidence.

    Never a refusal of the diff. The three ways complexity can be unavailable
    -- one side predates it, one side failed to measure it, or the two sides
    used different Complexity Contracts -- all end here, and all of them leave
    the four benchmark-metric deltas untouched.
    """
    from modules import complexity_view

    left_state = complexity_view.state_of(left.repository)
    right_state = complexity_view.state_of(right.repository)
    left_version = (
        complexity_view.repository_block(left.repository) or {}
    ).get("complexity_contract_version") or left.view.manifest.get(
        "complexity_contract_version"
    )
    right_version = (
        complexity_view.repository_block(right.repository) or {}
    ).get("complexity_contract_version") or right.view.manifest.get(
        "complexity_contract_version"
    )

    absent = [
        side.spec.label
        for side, state in ((left, left_state), (right, right_state))
        if state == complexity_view.STATE_ABSENT
    ]
    unmeasured = [
        side.spec.label
        for side, state in ((left, left_state), (right, right_state))
        if state not in complexity_view.EVALUABLE_STATES
        and state != complexity_view.STATE_ABSENT
    ]

    if absent:
        reason = (
            f"complexity is absent on: {', '.join(absent)}. That artifact "
            f"generation recorded no complexity at all, so there is nothing to "
            f"compare -- which is not the same as no difference, and not zero."
        )
    elif unmeasured:
        reason = (
            f"complexity was not evaluable on: {', '.join(unmeasured)}; an "
            f"unavailable measurement is never read as zero"
        )
    elif left_version != right_version:
        reason = (
            f"complexity values defined by different Complexity Contracts are "
            f"not comparable ({left_version!r} versus {right_version!r})"
        )
    else:
        reason = None

    return {
        "evaluable": reason is None,
        "not_evaluable_reason": reason,
        "complexity_contract_version_from": left_version,
        "complexity_contract_version_to": right_version,
        "measurement_state_from": left_state,
        "measurement_state_to": right_state,
        "blocks_diff": False,
        "note": (
            "Complexity comparability is its own dimension. It never blocks the "
            "diff: Metric Contract 3.0.0 comparisons proceed whenever they are "
            "otherwise comparable."
        ),
    }


# --------------------------------------------------------------------------
# Metrolith Cognitive Complexity deltas. A THIRD verdict, beside the complexity
# one, for the same reason complexity got its own beside the benchmark metrics:
# a 1.9 run has an evaluable structural measurement and no cognitive one, so a
# single verdict cannot answer for both without degrading one of them.
# --------------------------------------------------------------------------


def assess_cognitive_comparability(
    left: ResolvedSide, right: ResolvedSide
) -> dict[str, Any]:
    """Whether cognitive deltas mean anything. Never a refusal of the diff.

    `blocks_diff` is False and nothing here is consulted by the four benchmark
    deltas or by the structural complexity deltas — an older or failed
    cognitive measurement degrades cognitive figures and nothing else.
    """
    from modules import complexity_view

    left_state = complexity_view.cognitive_state_of(left.repository)
    right_state = complexity_view.cognitive_state_of(right.repository)
    left_version = (
        complexity_view.repository_block(left.repository) or {}
    ).get("complexity_contract_version") or left.view.manifest.get(
        "complexity_contract_version"
    )
    right_version = (
        complexity_view.repository_block(right.repository) or {}
    ).get("complexity_contract_version") or right.view.manifest.get(
        "complexity_contract_version"
    )

    absent = [
        side.spec.label
        for side, state in ((left, left_state), (right, right_state))
        if state == complexity_view.COGNITIVE_ABSENT
    ]
    unmeasured = [
        side.spec.label
        for side, state in ((left, left_state), (right, right_state))
        if state not in complexity_view.COGNITIVE_EVALUABLE_STATES
        and state != complexity_view.COGNITIVE_ABSENT
    ]

    if absent:
        reason = (
            f"cognitive complexity is absent on: {', '.join(absent)}. That "
            f"artifact generation recorded no cognitive measurement at all, so "
            f"there is nothing to compare — which is not the same as no "
            f"difference, and not the same as a measured zero."
        )
    elif unmeasured:
        reason = (
            f"cognitive complexity was not evaluable on: "
            f"{', '.join(unmeasured)}; an unavailable measurement is never read "
            f"as zero"
        )
    elif left_version != right_version:
        reason = (
            f"cognitive values defined by different Complexity Contracts are "
            f"not comparable ({left_version!r} versus {right_version!r})"
        )
    else:
        reason = None

    return {
        "metric_name": complexity_view.COGNITIVE_METRIC_NAME,
        "evaluable": reason is None,
        "not_evaluable_reason": reason,
        "complexity_contract_version_from": left_version,
        "complexity_contract_version_to": right_version,
        "measurement_state_from": left_state,
        "measurement_state_to": right_state,
        "blocks_diff": False,
        "note": (
            "Cognitive comparability is its own dimension. It never blocks the "
            "diff and never degrades a Metric Contract 3.0.0 comparison or a "
            "Complexity Contract 1.0.0 delta."
        ),
    }


def _cognitive_rows(side: ResolvedSide) -> list[Mapping[str, Any]] | None:
    """Callable rows for one side, or ``None`` when the run carries none."""
    if not getattr(side.view, "has_callable_artifact", False):
        return None
    from modules.subject import subject_key_of

    found: list[Mapping[str, Any]] = []
    for row in side.view.stream_callables():
        if subject_key_of(dict(row)) != side.subject_key:
            continue
        found.append(row)
    return found


def build_cognitive_aggregate_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Repository-level cognitive deltas."""
    from modules import complexity_view

    before = complexity_view.cognitive_aggregate(_cognitive_rows(left) or ())
    after = complexity_view.cognitive_aggregate(_cognitive_rows(right) or ())
    found: list[dict[str, Any]] = []
    for dimension in complexity_view.COGNITIVE_AGGREGATE_FIELDS:
        entry = _complexity_delta_entry(
            dimension, before.get(dimension), after.get(dimension), verdict
        )
        if entry["delta"] == 0:
            continue
        if entry["delta"] is None and before.get(dimension) == after.get(dimension):
            continue
        found.append(entry)
    return found


def _cognitive_by_language(
    rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    from modules import complexity_view

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows or ():
        language = row.get("detected_language")
        if language:
            grouped.setdefault(str(language), []).append(row)
    return {
        language: complexity_view.cognitive_aggregate(items)
        for language, items in sorted(grouped.items())
    }


def build_cognitive_language_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Language-level cognitive deltas, never summed across languages."""
    from modules import complexity_view

    before = _cognitive_by_language(_cognitive_rows(left))
    after = _cognitive_by_language(_cognitive_rows(right))
    found: list[dict[str, Any]] = []
    for language in sorted(set(before) | set(after)):
        left_row = before.get(language) or {}
        right_row = after.get(language) or {}
        for dimension in complexity_view.COGNITIVE_AGGREGATE_FIELDS:
            entry = _complexity_delta_entry(
                dimension, left_row.get(dimension), right_row.get(dimension), verdict
            )
            if entry["delta"] == 0:
                continue
            if entry["delta"] is None and left_row.get(dimension) == right_row.get(dimension):
                continue
            entry["language"] = language
            found.append(entry)
    return found


def build_cognitive_file_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> dict[str, Any]:
    """File-level cognitive evidence, joined on `relative_path`.

    **No callable-level matching**, here or anywhere else in Direct Diff:
    `callable_row_id` is within-artifact row identity and makes no
    cross-revision claim, so pairing on it would invent a correspondence the
    contract disclaims. Rows are aggregated per file and the FILES are joined.
    """
    from modules import complexity_view

    left_rows = _cognitive_rows(left)
    right_rows = _cognitive_rows(right)
    if left_rows is None or right_rows is None:
        missing = [
            side.spec.label
            for side, rows in ((left, left_rows), (right, right_rows))
            if rows is None
        ]
        return {
            "evaluable": False,
            "not_evaluable_reason": (
                f"file-level cognitive evidence requires a callable artifact on "
                f"both sides; absent for: {', '.join(missing)}"
            ),
            "deltas": [],
        }
    if not verdict["evaluable"]:
        return {
            "evaluable": False,
            "not_evaluable_reason": verdict["not_evaluable_reason"],
            "deltas": [],
        }

    def by_path(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            path = row.get("relative_path")
            if path:
                grouped.setdefault(str(path), []).append(row)
        return {
            path: complexity_view.cognitive_aggregate(items)
            for path, items in grouped.items()
        }

    before = by_path(left_rows)
    after = by_path(right_rows)
    deltas: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        left_row = before.get(path) or {}
        right_row = after.get(path) or {}
        for dimension in (
            "cognitive_callable_count", "cognitive_complexity_total",
            "cognitive_complexity_max",
        ):
            entry = _complexity_delta_entry(
                dimension, left_row.get(dimension), right_row.get(dimension), verdict
            )
            if entry["delta"] == 0:
                continue
            if entry["delta"] is None and left_row.get(dimension) == right_row.get(dimension):
                continue
            entry["relative_path"] = path
            deltas.append(entry)
    return {"evaluable": True, "not_evaluable_reason": None, "deltas": deltas}


def _complexity_aggregate(repository: Mapping[str, Any]) -> Mapping[str, Any]:
    from modules import complexity_view

    block = complexity_view.repository_block(repository)
    return (block or {}).get("aggregate") or {}


def _complexity_by_language(repository: Mapping[str, Any]) -> Mapping[str, Any]:
    from modules import complexity_view

    block = complexity_view.repository_block(repository)
    return (block or {}).get("by_language") or {}


def _complexity_delta_entry(
    dimension: str, before: Any, after: Any, verdict: Mapping[str, Any]
) -> dict[str, Any]:
    """A complexity delta, or the reason there is none.

    When complexity itself is not comparable the reason comes from the verdict
    rather than from the values, so "this run predates complexity" never
    degrades into the generic "a value is null".
    """
    if not verdict["evaluable"]:
        return {
            "dimension": dimension,
            "value_from": before,
            "value_to": after,
            "delta": None,
            "evidence_level": NOT_EVALUABLE,
            "not_evaluable_reason": verdict["not_evaluable_reason"],
        }
    entry = _delta_entry(dimension, before, after)
    if isinstance(before, float) or isinstance(after, float):
        # The mean is the only non-integer aggregate; rounding here would
        # invent precision the producer did not publish.
        if entry["delta"] is not None:
            entry["delta"] = round(after - before, 4)
    return entry


def build_complexity_aggregate_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Repository-level complexity deltas, one of the three approved levels."""
    before = _complexity_aggregate(left.repository)
    after = _complexity_aggregate(right.repository)
    found: list[dict[str, Any]] = []
    for dimension in COMPLEXITY_AGGREGATE_DIMENSIONS:
        entry = _complexity_delta_entry(
            dimension, before.get(dimension), after.get(dimension), verdict
        )
        if entry["delta"] == 0:
            continue
        if entry["delta"] is None and before.get(dimension) == after.get(dimension):
            continue
        found.append(entry)
    return found


def build_complexity_language_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Language-level complexity deltas.

    Reported per language and never summed across languages: the figures are
    not measurement-equivalent between them.
    """
    before = _complexity_by_language(left.repository)
    after = _complexity_by_language(right.repository)
    found: list[dict[str, Any]] = []
    for language in sorted(set(before) | set(after)):
        left_row = before.get(language) or {}
        right_row = after.get(language) or {}
        for dimension in COMPLEXITY_AGGREGATE_DIMENSIONS:
            entry = _complexity_delta_entry(
                dimension, left_row.get(dimension), right_row.get(dimension), verdict
            )
            if entry["delta"] == 0:
                continue
            if entry["delta"] is None and left_row.get(dimension) == right_row.get(dimension):
                continue
            entry["language"] = language
            found.append(entry)
    return found


def build_complexity_file_deltas(
    left: ResolvedSide, right: ResolvedSide, verdict: Mapping[str, Any]
) -> dict[str, Any]:
    """File-level complexity evidence, joined on `relative_path`.

    The join key is the path, exactly as `build_file_evidence` uses it. No
    callable-level matching happens anywhere: `callable_row_id` is
    within-artifact row identity and makes no cross-revision claim.
    """
    left_ledger = _ledger_by_path(left.view, left.subject_key)
    right_ledger = _ledger_by_path(right.view, right.subject_key)
    if left_ledger is None or right_ledger is None:
        missing = [
            side.spec.label
            for side, ledger in ((left, left_ledger), (right, right_ledger))
            if ledger is None
        ]
        return {
            "evaluable": False,
            "not_evaluable_reason": (
                f"file-level complexity requires a contribution ledger on both "
                f"sides; absent for: {', '.join(missing)}"
            ),
            "deltas": [],
            "status_changes": [],
        }
    if not verdict["evaluable"]:
        return {
            "evaluable": False,
            "not_evaluable_reason": verdict["not_evaluable_reason"],
            "deltas": [],
            "status_changes": [],
        }

    deltas: list[dict[str, Any]] = []
    status_changes: list[dict[str, Any]] = []
    for path in sorted(set(left_ledger) & set(right_ledger)):
        before_row = left_ledger[path]
        after_row = right_ledger[path]
        for dimension in COMPLEXITY_FILE_DIMENSIONS:
            entry = _complexity_delta_entry(
                dimension,
                _numeric(before_row.get(dimension)),
                _numeric(after_row.get(dimension)),
                verdict,
            )
            if entry["delta"] == 0:
                continue
            if entry["delta"] is None and before_row.get(dimension) == after_row.get(dimension):
                continue
            entry["relative_path"] = path
            deltas.append(entry)
        for dimension in COMPLEXITY_FILE_STATUS_DIMENSIONS:
            before_value = before_row.get(dimension) or None
            after_value = after_row.get(dimension) or None
            if before_value != after_value:
                status_changes.append({
                    "relative_path": path,
                    "dimension": dimension,
                    "from": before_value,
                    "to": after_value,
                })
    return {
        "evaluable": True,
        "not_evaluable_reason": None,
        "deltas": deltas,
        "status_changes": status_changes,
    }


def build_status_changes(left: ResolvedSide, right: ResolvedSide) -> list[dict[str, Any]]:
    """Measurement-status transitions, aggregate and repository level."""
    found: list[dict[str, Any]] = []
    before_aggregate = _aggregate(left.repository)
    after_aggregate = _aggregate(right.repository)
    for dimension in STATUS_DIMENSIONS:
        if before_aggregate.get(dimension) != after_aggregate.get(dimension):
            found.append({
                "dimension": dimension,
                "scope": "aggregate",
                "value_from": before_aggregate.get(dimension),
                "value_to": after_aggregate.get(dimension),
            })
    for dimension in REPOSITORY_STATUS_DIMENSIONS:
        if left.repository.get(dimension) != right.repository.get(dimension):
            found.append({
                "dimension": dimension,
                "scope": "repository",
                "value_from": left.repository.get(dimension),
                "value_to": right.repository.get(dimension),
            })

    before_parsers = _parser_contract(left.repository) or {}
    after_parsers = _parser_contract(right.repository) or {}
    for language in sorted(set(before_parsers) | set(after_parsers)):
        if before_parsers.get(language) != after_parsers.get(language):
            found.append({
                "dimension": "parser_status",
                "scope": "language",
                "language": language,
                "value_from": before_parsers.get(language),
                "value_to": after_parsers.get(language),
            })
    return found


def build_source_identity_changes(left: ResolvedSide, right: ResolvedSide) -> list[dict[str, Any]]:
    """Category A: how the analyzed source state itself differs."""
    found: list[dict[str, Any]] = []
    left_acquisition = left.repository.get("acquisition") or {}
    right_acquisition = right.repository.get("acquisition") or {}
    for dimension in SOURCE_IDENTITY_DIMENSIONS:
        if dimension in ("requested_revision", "analyzed_commit_sha"):
            key = ("requested_commit_sha" if dimension == "requested_revision"
                   else "analyzed_commit_sha")
            before = left_acquisition.get(key)
            after = right_acquisition.get(key)
        else:
            before = left.repository.get(dimension)
            after = right.repository.get(dimension)
        if before == after:
            continue
        entry = {
            "dimension": dimension,
            "value_from": before,
            "value_to": after,
        }
        if dimension == "analysis_scope_hash":
            entry["note"] = (
                "the analyzed scope changed: different bytes were parsed. This "
                "is exact scope identity, not a comparability verdict."
            )
        found.append(entry)
    return found


def attribute_metric_changes(
    left: ResolvedSide, right: ResolvedSide, evidence: Mapping[str, Any],
    deltas: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Account for each aggregate delta from per-file contributions.

    This is reconciliation, not causal inference. Every component below is a
    sum of recorded per-file contributions; the residual is what those
    contributions do **not** account for, and it is reported rather than
    explained away. An unexplained residual is a finding.
    """
    left_ledger = _ledger_by_path(left.view, left.subject_key)
    right_ledger = _ledger_by_path(right.view, right.subject_key)

    attributions: list[dict[str, Any]] = []
    for entry in deltas:
        dimension = entry["dimension"]
        field_name = _LEDGER_FIELD.get(dimension)
        if entry.get("delta") is None or field_name is None:
            continue
        if left_ledger is None or right_ledger is None or not evidence.get("evaluable"):
            attributions.append({
                "dimension": dimension,
                "observed_delta": entry["delta"],
                "evidence_level": NOT_EVALUABLE,
                "not_evaluable_reason": (
                    "file-level attribution requires a contribution ledger on "
                    "both sides"
                ),
                "from_added_files": None,
                "from_removed_files": None,
                "from_changed_files": None,
                "residual": None,
            })
            continue

        def total(paths: Iterable[str], ledger) -> int | None:
            running = 0
            for path in paths:
                row = ledger.get(path)
                if row is None or row.get("contribution_state") != "contributed":
                    continue
                value = _numeric(row.get(field_name))
                if value is None:
                    return None
                running += value
            return running

        added_paths = [item["relative_path"] for item in evidence["added"]]
        removed_paths = [item["relative_path"] for item in evidence["removed"]]
        changed_paths = [item["relative_path"] for item in evidence["content_changed"]]

        added_total = total(added_paths, right_ledger)
        removed_total = total(removed_paths, left_ledger)
        changed_before = total(changed_paths, left_ledger)
        changed_after = total(changed_paths, right_ledger)

        if None in (added_total, removed_total, changed_before, changed_after):
            attributions.append({
                "dimension": dimension,
                "observed_delta": entry["delta"],
                "evidence_level": NOT_EVALUABLE,
                "not_evaluable_reason": (
                    "at least one contributing file records no value for this "
                    "dimension; an absent contribution is never summed as zero"
                ),
                "from_added_files": None,
                "from_removed_files": None,
                "from_changed_files": None,
                "residual": None,
            })
            continue

        changed_delta = changed_after - changed_before
        accounted = added_total - removed_total + changed_delta
        residual = entry["delta"] - accounted
        attributions.append({
            "dimension": dimension,
            "observed_delta": entry["delta"],
            "evidence_level": (
                EXACT_RECONCILIATION if residual == 0 else UNEXPLAINED_RESIDUAL
            ),
            "not_evaluable_reason": None,
            "from_added_files": added_total,
            "from_removed_files": -removed_total,
            "from_changed_files": changed_delta,
            "residual": residual,
        })
    return attributions


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------

def _side_document(side: ResolvedSide) -> dict[str, Any]:
    acquisition = side.repository.get("acquisition") or {}
    return {
        "label": side.spec.label,
        "specifier": side.spec.describe(),
        "kind": side.spec.kind,
        "run_directory": str(side.run_directory),
        "run_id": side.view.run_id,
        "freshly_analyzed": side.freshly_analyzed,
        "subject_key": side.subject_key,
        "subject_key_basis": side.repository.get("subject_key_basis"),
        "source_mode": side.repository.get("source_mode"),
        "repository_url": side.repository.get("repository_url"),
        "requested_revision": acquisition.get("requested_commit_sha"),
        "analyzed_commit_sha": acquisition.get("analyzed_commit_sha") or None,
        "analysis_scope_hash": side.repository.get("analysis_scope_hash"),
        "filesystem_manifest_hash": side.repository.get("filesystem_manifest_hash"),
        "working_tree_state": side.repository.get("working_tree_state"),
        "artifact_schema_version": side.view.manifest.get("artifact_schema_version"),
        "metric_contract_version": side.view.manifest.get("metric_contract_version"),
        "exclusion_policy_version": side.view.manifest.get("exclusion_policy_version"),
        "inventory_schema_version": side.view.manifest.get("inventory_schema_version"),
        "analysis_status": side.repository.get("analysis_status"),
        "warnings": list(side.warnings),
    }


def _semantic_hashes(left: ResolvedSide, right: ResolvedSide) -> dict[str, Any]:
    """Semantic Projection 2.0 used as a guard, not as the diff itself.

    Equal hashes mean the measurement projections are identical. A changed hash
    means a semantic dimension moved — it is never, by itself, a statement about
    architectural quality.
    """
    from validation.scripts.semantic_projection import measurement_semantic_hash

    try:
        before = measurement_semantic_hash(left.run_directory)
        after = measurement_semantic_hash(right.run_directory)
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "from": None, "to": None, "equal": None,
        }
    return {
        "available": True,
        "reason": None,
        "from": before,
        "to": after,
        "equal": before == after,
        "note": (
            "Semantic Projection 2.0 equality over the whole run. A difference "
            "records that a measured dimension changed; it is not a quality "
            "judgement."
        ),
    }


def render_qualification_dimension(left: ResolvedSide, right: ResolvedSide) -> str:
    """Qualification as a SEPARATE non-metric dimension of a revision diff.

    Kept out of the metric delta lists on purpose. A revision whose
    representativeness changed from ADEQUATE to MATERIAL_MIX has not had a
    metric change; it has had an admission change, and listing the two together
    would invite reading the second as the first. `metrolith diff` remains a
    measurement comparison — this section says what may be CONCLUDED from it.
    """
    from modules.benchmark_qualification import subject_of

    def _record(side: ResolvedSide) -> Mapping[str, Any] | None:
        view = side.view
        if getattr(view, "qualification_mode", "not_requested") != "benchmark_qualified":
            return None
        artifact = getattr(view, "benchmark_qualification", None)
        if artifact is None:
            return None
        wanted = subject_of(side.repository)
        for record in artifact.get("records") or ():
            if subject_of(record.get("binding") or {}) == wanted:
                return record
        return None

    first, second = _record(left), _record(right)
    lines = ["", "## Qualification dimension (not a metric comparison)"]
    lines.append("")
    lines.append(
        f"  from mode: {getattr(left.view, 'qualification_mode', 'not_requested')}"
    )
    lines.append(
        f"  to mode:   {getattr(right.view, 'qualification_mode', 'not_requested')}"
    )

    if first is None or second is None:
        lines.append("")
        lines.append(
            "  At least one side carries no qualification decision, so this diff "
            "is a measurement comparison only and must not be presented as a "
            "repository-level benchmark comparison."
        )
        return "\n".join(lines)

    changes = []
    for field_name in (
        "repository_representativeness",
        "benchmark_usability",
        "repository_level_comparison_eligible",
        "manual_admission_restriction",
        "usable_metric_families",
    ):
        before, after = first.get(field_name), second.get(field_name)
        if before != after:
            changes.append(f"    {field_name}: {before!r} -> {after!r}")
    lines.append("")
    if changes:
        lines.append("  Qualification changes:")
        lines.extend(changes)
    else:
        lines.append("  No qualification differences observed.")

    if not (
        first.get("repository_level_comparison_eligible")
        and second.get("repository_level_comparison_eligible")
    ):
        lines.append("")
        lines.append(
            "  At least one side is not repository-level eligible: this is a "
            "measurement comparison, not a repository-level benchmark "
            "comparison."
        )
    return "\n".join(lines)


def build_diff(left: ResolvedSide, right: ResolvedSide, *,
               subject_override: str | None = None) -> dict[str, Any]:
    """Assemble the complete diff document for two resolved sides."""
    comparability = assess_comparability(left, right, subject_override=subject_override)

    source_changes = build_source_identity_changes(left, right)
    status_changes: list[dict[str, Any]] = []
    aggregate_deltas: list[dict[str, Any]] = []
    language_deltas: list[dict[str, Any]] = []
    attribution: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "evaluable": False,
        "not_evaluable_reason": "comparison refused; per-file evidence not derived",
        "added": [], "removed": [], "content_changed": [],
        "unchanged_count": None, "renames": [],
        "counts": {"added": None, "removed": None, "content_changed": None},
    }

    complexity_verdict = {
        "evaluable": False,
        "not_evaluable_reason": "comparison refused; complexity not derived",
        "complexity_contract_version_from": None,
        "complexity_contract_version_to": None,
        "measurement_state_from": None,
        "measurement_state_to": None,
        "blocks_diff": False,
        "note": (
            "Complexity comparability is its own dimension and never blocks "
            "the diff."
        ),
    }
    complexity_aggregate_deltas: list[dict[str, Any]] = []
    complexity_language_deltas: list[dict[str, Any]] = []
    complexity_file: dict[str, Any] = {
        "evaluable": False,
        "not_evaluable_reason": "comparison refused; complexity not derived",
        "deltas": [], "status_changes": [],
    }

    cognitive_verdict = {
        "metric_name": "Metrolith Cognitive Complexity",
        "evaluable": False,
        "not_evaluable_reason": "comparison refused; cognitive complexity not derived",
        "complexity_contract_version_from": None,
        "complexity_contract_version_to": None,
        "measurement_state_from": None,
        "measurement_state_to": None,
        "blocks_diff": False,
        "note": (
            "Cognitive comparability is its own dimension and never blocks "
            "the diff."
        ),
    }
    cognitive_aggregate_deltas: list[dict[str, Any]] = []
    cognitive_language_deltas: list[dict[str, Any]] = []
    cognitive_file: dict[str, Any] = {
        "evaluable": False,
        "not_evaluable_reason": "comparison refused; cognitive complexity not derived",
        "deltas": [],
    }

    if comparability["comparable"]:
        status_changes = build_status_changes(left, right)
        aggregate_deltas = build_aggregate_deltas(left, right)
        language_deltas = build_language_deltas(left, right)
        evidence = build_file_evidence(left, right)
        attribution = attribute_metric_changes(left, right, evidence, aggregate_deltas)
        # Derived AFTER, and independently. Whatever complexity turns out to be,
        # the four lines above have already produced their result.
        complexity_verdict = assess_complexity_comparability(left, right)
        complexity_aggregate_deltas = build_complexity_aggregate_deltas(
            left, right, complexity_verdict
        )
        complexity_language_deltas = build_complexity_language_deltas(
            left, right, complexity_verdict
        )
        complexity_file = build_complexity_file_deltas(left, right, complexity_verdict)
        # Third and last, and independent again: whatever cognitive turns out
        # to be, the benchmark deltas AND the structural complexity deltas
        # above have already produced their results.
        cognitive_verdict = assess_cognitive_comparability(left, right)
        cognitive_aggregate_deltas = build_cognitive_aggregate_deltas(
            left, right, cognitive_verdict
        )
        cognitive_language_deltas = build_cognitive_language_deltas(
            left, right, cognitive_verdict
        )
        cognitive_file = build_cognitive_file_deltas(left, right, cognitive_verdict)

    has_differences = bool(
        source_changes or status_changes or aggregate_deltas or language_deltas
        or complexity_aggregate_deltas or complexity_language_deltas
        or complexity_file.get("deltas") or complexity_file.get("status_changes")
        or cognitive_aggregate_deltas or cognitive_language_deltas
        or cognitive_file.get("deltas")
        or evidence.get("counts", {}).get("added")
        or evidence.get("counts", {}).get("removed")
        or evidence.get("counts", {}).get("content_changed")
    )

    return {
        "revision_diff_format_version": REVISION_DIFF_FORMAT_VERSION,
        "subject": {
            "subject_key_from": left.subject_key,
            "subject_key_to": right.subject_key,
            # The pre-override identities. When an override is in force these
            # are what the sides actually resolved to, and they may differ.
            "natural_subject_key_from": left.identity,
            "natural_subject_key_to": right.identity,
            "same_logical_subject": left.identity == right.identity,
            "subject_key_override": subject_override,
        },
        "sides": {
            "from": _side_document(left),
            "to": _side_document(right),
        },
        "comparability": comparability,
        "source_identity_changes": source_changes,
        "measurement_status_changes": status_changes,
        "aggregate_metric_deltas": aggregate_deltas,
        "per_language_metric_deltas": language_deltas,
        # Complexity Contract 1.0.0, at the three approved levels and under its
        # own verdict. Kept out of `aggregate_metric_deltas` so a complexity
        # difference can never be read as a benchmark-metric difference, and so
        # Metric Contract 3.0.0 stays exactly what it was.
        "cognitive_comparability": cognitive_verdict,
        "cognitive_aggregate_deltas": cognitive_aggregate_deltas,
        "per_language_cognitive_deltas": cognitive_language_deltas,
        "file_cognitive_evidence": cognitive_file,
        "complexity_comparability": complexity_verdict,
        "complexity_aggregate_deltas": complexity_aggregate_deltas,
        "per_language_complexity_deltas": complexity_language_deltas,
        "file_complexity_evidence": complexity_file,
        "file_evidence": evidence,
        "metric_attribution": attribution,
        "semantic_projection": _semantic_hashes(left, right),
        "has_differences": has_differences,
        "evidence_level_meanings": {
            OBSERVED_DIFFERENCE: "the values differ; nothing further is claimed",
            EXACT_RECONCILIATION: (
                "per-file contributions on both sides account for the delta with "
                "zero residual"
            ),
            UNEXPLAINED_RESIDUAL: "reconciliation ran and did not close",
            NOT_EVALUABLE: (
                "required evidence is absent, so the question cannot be answered; "
                "this is never reported as no difference"
            ),
        },
    }
