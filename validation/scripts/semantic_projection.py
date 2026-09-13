#!/usr/bin/env python3
"""Canonical semantic projection of a finalized run, and its hash.

This is the **semantic-equivalence oracle**. It answers exactly one question:

    Do these two runs represent the same measurement of the same subject?

It exists so that later phases — structural refactoring, the golden-run fixture,
incremental analysis — can prove they changed nothing, without every test
growing its own hand-written "ignore these fields" list. Those lists are how
"we compared the runs and they matched" quietly becomes "we compared the parts
that happened to agree".

**It is an internal comparison and testing API.** It is deliberately not a
persisted run artifact, and it does not touch Artifact Schema 1.5.0, which is
frozen. If it ever becomes a written artifact it takes
:data:`SEMANTIC_PROJECTION_VERSION` as its own derived-format version — the
Artifact Schema must not absorb it.

What it is *not*
================

The projection is not a substitute for any existing guarantee, and it is
layered strictly on top of all of them:

* schema validation (``schema_store``) still decides whether documents conform;
* strict artifact reading (``artifact_io.reader``) still decides what may be
  decoded at all;
* artifact integrity hashes still cover the bytes on disk;
* finalization self-validation still decides whether a run was written correctly.

Above all: **a malformed run is never comparable.** :func:`semantic_projection`
refuses before projecting anything. That refusal is not incidental — a
projection is trivially constructible from the readable *half* of a broken run,
and a comparison built on that half would report agreement about a run whose
other half is corrupt.

The refusal deliberately checks structural errors *after* forcing the optional
projections. Inventory faults are recorded lazily, by the very property that
discovers them, and a run with a malformed inventory still classifies as
``finalized_valid`` — by design, because a broken optional projection must not
invalidate a good measurement. For *comparison* purposes it must still be
refused, so this module forces the read and then insists on zero errors.

The four field categories
=========================

Every field is classified, and the classification is the whole design:

``A. semantic measurement state``
    What was measured: metric values, per-metric statuses, parser outcomes,
    diagnostics that are part of authoritative run semantics, and the per-file
    inventory evidence those aggregates were computed from. **Hashed.**

``B. semantic source and provenance identity``
    *Which subject, in which state, under which meaning*: repository identity,
    analyzed commit, verification status, and the contract versions that define
    what the numbers mean. A metric value is meaningless without the contract
    that defines it, so contract versions are semantic, not environmental.
    **Hashed.**

``C. execution-environment evidence``
    How the measurement was executed: interpreter and program versions, grammar
    versions, host class, acquisition mode, cache warmth. **Retained and
    returned, but not hashed.**

    This category is the reason the projection returns a structure rather than
    just a digest. A field is never discarded merely because it differs between
    executions — that is precisely how real provenance gets thrown away to make
    two runs compare equal. Environment evidence stays visible; it simply does
    not decide semantic equality, because two runs that produced identical
    measurements of the same revision *are* the same measurement even if one ran
    on a warm cache.

``D. nondeterministic timing and runtime metadata``
    Wall clocks, durations, run ids, temporary paths, subprocess command lines.
    **Dropped entirely.** These cannot be equal across two executions even in
    principle, and none of them carries measurement meaning. Note that a
    *duration* is category D but a *count* is category A: elapsed time is noise,
    while "how many files were read" is evidence.

Equality contract
=================

Two runs are semantically equivalent when::

    semantic_projection(a) == semantic_projection(b)      # structural
    semantic_hash(a) == semantic_hash(b)                  # digest

The digest is over a canonical serialization with these rules, none of which
rely on Python dict insertion order:

* **Key ordering** — every object's keys are sorted by Unicode code point, at
  every depth (``sort_keys=True``). Insertion order is never the contract.
* **Null handling** — a field absent from the source is normalized to ``null``.
  Absent and explicitly-null compare equal, which is sound because the field set
  is a closed allowlist rather than whatever the document happened to contain.
* **Numbers** — ``int`` and ``float`` stay distinct, so ``1`` and ``1.0`` are not
  equal; a metric that changed type changed meaning. ``NaN`` and infinities are
  rejected rather than serialized, because they are not JSON and compare
  unequal to themselves.
* **List ordering** — declared per list, never guessed. Lists named in
  :data:`POSITIONAL_LISTS` keep their order because position *is* the meaning
  (byte offsets, for instance). Every other list is set-like: its order is an
  artifact of traversal, and it is sorted by its own canonical serialization so
  no sort key has to be invented.
* **Paths** — separators normalized to ``/``. Absolute paths are rejected: a
  host path inside a semantic artifact is a leak, not a value to normalize away.
* **Repository ordering** — **not semantic**. Cohort input order must never
  cause false inequality, so repositories are keyed by ``subject_key`` and
  sorted. The same applies to inventory file records, keyed by ``relative_path``.
* **Absence** — a field missing from the source is **not** the same as a field
  explicitly ``null``. Absence is preserved; see :data:`ABSENT_EQUALS_NULL`.

What the digest does *not* cover
================================

:func:`measurement_semantic_hash` is a **measurement/result-semantic equality**
digest. It is named for what it covers, because the obvious shorter name would
imply more than it delivers.

It does **not** answer "were these runs produced by the same ArchLens, on the
same interpreter, with the same grammars?". Category C is excluded from it by
construction. Two runs with an identical digest may come from different builds
on different machines. That is deliberate: it is exactly what lets the digest
prove that a refactor, or a cache, changed no measurement. For environment
equivalence, read the ``environment`` section the projection returns alongside
it, or use ``compare``'s contract checks.

Identity model
==============

**Semantic Projection 2.0 is defined against the Artifact 1.7 subject identity
model.** ``subject_key`` is the logical identity: the repository ordering key,
the cross-run join, and the basis of every "same subject" claim.
``repository_url`` is provenance evidence — nullable, and never the key.

Three predicates are kept apart, because collapsing them is what the old model
did:

* **same logical subject** — equal ``subject_key``;
* **same analyzed scope** — equal ``analysis_scope_hash``;
* **compatible measurement contracts** — equal contract versions.

``source_mode`` is projected as evidence but never decides comparability. The
same revision fetched from a remote and read from a local clone is the same
measurement of the same bytes, and refusing to compare the two because their
acquisition labels differ would be bookkeeping masquerading as a finding.

Legacy 1.5/1.6 artifacts have no ``subject_key``; readers derive the same
canonical key from the historical repository URL at read time, so old and new
runs join without a single historical byte being rewritten.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from validation.artifact_io.compatibility import RunLifecycle
from validation.artifact_io.reader import ImmutableRunView, open_run

#: Derived-format version, independent of Artifact Schema. Bump when the field
#: categories or the canonical serialization change, because either alters what
#: the hash means.
#:
#: **2.0.0** — Artifact 1.7 identity. `subject_key` is the logical identity and
#: the repository ordering key; `repository_url` becomes provenance evidence
#: rather than the map key. This is a change to *what identity means*, which the
#: 1.0 docstring named as requiring an explicit version review, so it is a major
#: bump rather than an allowlist addition.
#:
#: **1.1.0** — absence is preserved: a field missing from the source is omitted
#: from the projection rather than coerced to ``null``, so absent and
#: explicitly-null no longer hash alike. This changed real digests (a run with
#: no inventory and no ``git_mode_map_*`` fields moved from
#: ``90eb0c42efd58f92…`` to ``75d1888b14d6cee3…``), which is exactly the kind of
#: change this constant exists to mark.
#:
#: Bumped even though the API is internal and no digest is persisted in any
#: artifact. The discipline is worth more than the saved keystroke: a version
#: that silently stops meaning what it meant is worse than no version at all,
#: and the migration cost here was zero.
#: 2.1.0 adds `analysis_scope_hash_version` to the projected repository
#: identity. The construction algorithm is part of what the scope digest
#: *means*, so projecting the digest without it let a construction change look
#: like an unexplained difference — or, worse, let two digests from different
#: constructions be compared as if they were the same kind of value. This is a
#: projection-semantic change and deliberately not an Artifact Schema change:
#: nothing persisted moved.
#: 2.2.0 adds per-callable complexity: the repository `complexity` summary
#: (state, typed reason, contract version and the approved aggregates) and the
#: `callable_records` themselves. Both are category A -- what was measured -- so
#: omitting them would let a complexity change hash as "no change", which is
#: precisely the failure this oracle exists to prevent.
#:
#: **Historical digests do not move.** `ABSENT_EQUALS_NULL` is empty and absence
#: is preserved, so a pre-1.9 run that records neither key projects without them
#: and hashes exactly as it did at 2.1.0. That is asserted mechanically rather
#: than argued.
# 2.3.0: ArchLens Cognitive Complexity is measurement semantics -- a change to
# a callable's cognitive value must move the digest. `ABSENT_EQUALS_NULL` stays
# EMPTY, so a pre-1.10 run whose rows carry no `cognitive_complexity` key at all
# projects exactly as it did at 2.2.0: absence is preserved, never normalized to
# null, and no historical digest moves.
SEMANTIC_PROJECTION_VERSION = "2.3.0"


class RunNotComparable(RuntimeError):
    """A run was refused before projection. Never a silent partial result."""


# --------------------------------------------------------------- category A ---

#: Measurement state carried directly on the repository result.
REPOSITORY_MEASUREMENT_FIELDS = (
    "analysis_status",
    "core_metric_status",
    "expected_language_family_status",
    "partial_origin",
)

#: Measurement state carried under ``metrics``.
METRIC_FIELDS = (
    "primary_language_name",
    "aggregate",
    "by_language",
    "parser_status_by_language",
    "parse_errors",
    "parser_diagnostics",
    "recovered_parser_diagnostics",
    "recovery_taxonomy",
    "javascript_family_scope",
    "error_taxonomy",
    # Artifact Schema 1.9 / Complexity Contract 1.0.0. The summary carries the
    # measurement STATE and the aggregates; the records carry the per-callable
    # values, so a change that leaves every aggregate coincidentally equal still
    # moves the digest.
    "complexity",
    "callable_records",
)

#: Per-file evidence. The aggregates are derived from exactly these records, so
#: a change here that leaves an aggregate unchanged is still a semantic change.
INVENTORY_RECORD_FIELDS = (
    "relative_path",
    "detected_language",
    "size_bytes",
    "content_hash",
    "included_in_metrics",
    "exclusion_reason",
    "read_status",
    "parse_status",
    "parse_error",
    "error_category",
    "content_type",
    "template_family",
    "template_evidence_category",
    "template_evidence_summary",
    "git_mode",
    "is_git_symlink",
    "is_git_submodule",
    "git_symlink_target",
    "git_symlink_target_exists",
    "line_ending_style",
    "byte_order_mark",
    "parser_normalization_applied",
    "parser_compatibility_strategy",
    "parser_byte_offset_adjustment",
    "original_encoding",
    "parser_encoding",
    "encoding_transformation_applied",
    "original_byte_length",
    "parser_byte_length",
    "parser_offsets_map_directly_to_original_bytes",
    "original_byte_offsets_available",
    "nul_count",
    "nul_density",
    "nul_positions",
    "nul_contexts",
    "alternating_nul_evidence",
    "suspected_bomless_utf16",
    "nul_classification",
    "typed_javascript_dialect_evidence",
    "oversized",
)

#: Run-level measurement outcome.
RUN_MEASUREMENT_FIELDS = ("measurement_outcome",)

# --------------------------------------------------------------- category B ---

#: Subject identity and analyzed state.
REPOSITORY_PROVENANCE_FIELDS = (
    # Logical identity first: this is what makes two analyses analyses of the
    # same software. `repository_url` follows as a locator, not a key.
    "subject_key",
    "subject_key_basis",
    "source_mode",
    "analysis_scope_hash",
    # The construction algorithm is part of the meaning of the digest, so it is
    # projected alongside it. Without this, two artifacts built by different
    # constructions could present equal-looking digests, or differ with no
    # visible cause. Construction 2.0.0 removed regular-file Git mode from the
    # digest (docs/ANALYSIS_SCOPE_HASH_V2.md), which is exactly the situation
    # that has to be legible here rather than inferred.
    #
    # Absence is preserved, not collapsed: `ABSENT_EQUALS_NULL` is empty, so a
    # historical artifact that recorded no construction version projects
    # *without* the key rather than with a null that would read as a declared
    # value.
    "analysis_scope_hash_version",
    "working_tree_state",
    "repository_url",
    "repository_owner",
    "repository_name",
    "architecture_type",
    "expected_language",
)

#: Acquisition identity. ``analyzed_commit_sha`` answers *which state was
#: measured* and is never dropped to make two runs agree.
ACQUISITION_PROVENANCE_FIELDS = (
    "analyzed_commit_sha",
    "requested_commit_sha",
    "commit_verification_status",
)

#: Git-derived source evidence recorded on the result.
GIT_EVIDENCE_FIELDS = (
    "git_mode_map_status",
    "git_mode_map_available",
    "git_mode_entry_count",
)

#: The contracts that define what the measurements mean. A metric value under a
#: different metric contract is a different statement, so these are semantic.
CONTRACT_PROVENANCE_FIELDS = (
    "artifact_schema_version",
    "metric_contract_version",
    "exclusion_policy_version",
    "exclusion_policy_sha256",
    "inventory_schema_version",
)

# --------------------------------------------------------------- category C ---

#: Retained and returned, never hashed. See the module docstring.
ENVIRONMENT_FIELDS = (
    "program_version",
    "package_distribution_version",
    "python_version_exact",
    "grammar_versions",
    "profiler_git_commit_sha",
    "profiler_git_dirty",
    "effective_git_checkout_configuration",
    "acquisition_mode",
)

# --------------------------------------------------------------- category D ---

#: Dropped entirely, with the reason each one is not semantic. Documented rather
#: than merely omitted so that a future reader can tell a deliberate exclusion
#: from an oversight.
NONSEMANTIC_RUNTIME_FIELDS: Mapping[str, str] = {
    "run_id": "identifies an execution, not a measurement",
    "run_started_at": "wall clock",
    "run_finished_at": "wall clock",
    "measurement_finished_at": "wall clock",
    "measurement_wall_seconds": "elapsed time",
    "repository_start_timestamp": "wall clock",
    "repository_end_timestamp": "wall clock",
    "repository_duration_seconds": "elapsed time",
    "timings": "elapsed time, per stage",
    "checkout_timestamp": "wall clock",
    "fetch_timestamp": "wall clock",
    "acquisition_duration_seconds": "elapsed time",
    "worktree_duration_seconds": "elapsed time",
    "cleanup_duration_seconds": "elapsed time",
    "git_mode_command_duration_seconds": "elapsed time",
    "git_commands": "subprocess argv, contains temporary worktree paths",
    "cache_hit": "cache warmth varies per execution and changes no measurement",
    "cache_created": "cache warmth",
    "cache_updated": "cache warmth",
    "cache_status": "cache warmth",
    "network_contacted": "how bytes were obtained, not what was measured",
    "fetch_performed": "how bytes were obtained",
    "bytes_downloaded_if_available": "transfer volume, not measurement",
    "workspace_root": "absolute host path",
    "output_root": "absolute host path",
    "temporary_directory": "absolute host path",
    "cache_root": "absolute host path",
}

# ------------------------------------------------------- list order semantics ---
#
# Every list-valued field must be classified. There is no default: an
# unclassified list raises, because both possible defaults are wrong in a way
# that is invisible.
#
# Sorting an ordered list silently destroys meaning — `fallback_strategies` is
# the strategies *in the order they were applied*, and parser transformations
# compose, so a different order is a different parse. Preserving order on an
# unordered list produces false inequality from traversal accidents.
#
# Adding a list field to the projection therefore requires a deliberate
# decision, enforced by `_canonical` and by
# `test_every_list_field_is_classified`.

#: Order is the meaning. Preserved exactly.
ORDERED_LISTS = frozenset({
    # Ascending byte offsets, and index-aligned with `nul_contexts`: sorting
    # either one independently would break the correspondence between them.
    "nul_positions",
    "nul_contexts",
    # Application order. `list(applied)` and `list(selected.strategies)` in
    # `modules/core_metrics.py`; parser normalizations compose, so the sequence
    # is part of what happened.
    "fallback_strategies",
    "selected_fallback_strategies",
})

#: Order is an artifact of traversal or iteration. Sorted canonically.
UNORDERED_LISTS = frozenset({
    # Emitted in inventory-iteration order, which is filesystem order.
    "parse_errors",
    "parser_diagnostics",
    "recovered_parser_diagnostics",
    "malformed_nodes",
    "malformed_ast_nodes",
    "parse_failure_files",
    "typed_javascript_dialect_evidence",
    "file_contributions",
    # Per-callable complexity rows. Emitted in traversal order (path, start
    # line, row id), which is deterministic but is not the MEANING: the set of
    # measured callables is. Sorting by canonical serialization means a change
    # to any single row still moves the digest, while a reordering alone does
    # not invent inequality.
    "callable_records",
    "warnings",
    # Set-like by construction.
    "affected_metrics",
    "extensions",
    "nested_repositories_excluded",
    # Projection-owned containers, sorted on a declared key by their builders.
    "files",
    "repositories",
    "provenance_warnings",
})


class UnclassifiedListField(RunNotComparable):
    """A list-valued field with no declared ordering semantics."""


# ------------------------------------------------------------ canonicalizing ---


_REMOTE_LOCATOR = re.compile(
    r"^(?:https?://|git@|ssh://git@)(?P<host>[^/:]+)[:/](?P<owner>[^/]+)/"
    r"(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _derived_subject_key(result: Mapping[str, Any]) -> str:
    """Logical identity for any artifact generation.

    Artifact 1.7 records `subject_key`. Older artifacts have only a repository
    URL, so the same canonical form is derived from it here — at read time, with
    no historical bytes rewritten — and a 1.6 run therefore joins correctly
    against a 1.7 run of the same subject.
    """
    recorded = result.get("subject_key")
    if recorded:
        return str(recorded)
    locator = str(result.get("repository_url") or "").strip()
    match = _REMOTE_LOCATOR.match(locator)
    if match:
        return (
            f"{match.group('host').lower()}/"
            f"{match.group('owner').lower()}/{match.group('name').lower()}"
        )
    return f"legacy:{hashlib.sha256(locator.encode('utf-8')).hexdigest()[:16]}"


def _normalize_path(value: str, *, field: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (
        len(normalized) > 1 and normalized[1] == ":"
    ):
        raise RunNotComparable(
            f"{field} is an absolute path ({value!r}); a host path inside a "
            f"semantic artifact is a leak, not a value to normalize"
        )
    return normalized


def _canonical(value: Any, *, field: str | None = None) -> Any:
    """Recursively canonicalize one value. Pure; never reads the filesystem."""
    if isinstance(value, bool) or value is None:
        # bool before int: bool is an int subclass and must stay a bool.
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise RunNotComparable(
                f"non-finite number in {field or 'projection'}; NaN and "
                f"infinities are not JSON and never compare equal"
            )
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if field in ("relative_path", "git_symlink_target"):
            return _normalize_path(value, field=field)
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key], field=str(key)) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        items = [_canonical(item, field=field) for item in value]
        if field in ORDERED_LISTS:
            return items
        if field in UNORDERED_LISTS:
            # Sort by canonical serialization so no sort key must be invented.
            return sorted(items, key=_serialize)
        raise UnclassifiedListField(
            f"list field {field!r} has no declared ordering semantics; add it to "
            f"ORDERED_LISTS or UNORDERED_LISTS in semantic_projection.py. "
            f"Guessing would either destroy meaning or invent inequality."
        )
    raise RunNotComparable(
        f"unsupported value type {type(value).__name__} in {field or 'projection'}"
    )


def _serialize(value: Any) -> str:
    """The one canonical serialization. Key order and separators are fixed."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


#: Fields whose contract establishes that absent and explicitly-null mean the
#: same thing. **Deliberately empty.**
#:
#: An audit of every allowlisted field across five real runs found no field for
#: which a producer emits `null` in one run and omits the key in another for the
#: *same* state. What it did find is the opposite: `git_mode_map_status`,
#: `git_mode_map_available` and `git_mode_entry_count` are absent on results
#: from runs that never reached inventory at all, and explicitly `null` or
#: populated once inventory ran. Absent there means "never attempted"; null
#: means "attempted, no value". Collapsing them would erase a real distinction.
#:
#: Adding a field here is a claim about that field's contract, so it needs a
#: cited reason and a test — see `AbsenceTests`.
ABSENT_EQUALS_NULL: frozenset[str] = frozenset()


def _select(source: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Allowlist projection preserving absence.

    A field missing from the source is **omitted** from the projection rather
    than normalized to ``None``, so that absent and explicitly-null produce
    different canonical serializations. Only fields named in
    :data:`ABSENT_EQUALS_NULL` are collapsed.
    """
    projected: dict[str, Any] = {}
    for name in fields:
        if name in source:
            projected[name] = _canonical(source[name], field=name)
        elif name in ABSENT_EQUALS_NULL:
            projected[name] = None
    return projected


# ------------------------------------------------------------------ refusal ---


def _admit(view: ImmutableRunView, run_dir: Path) -> None:
    """Refuse anything that must not be compared. Never partial."""
    if view.lifecycle is not RunLifecycle.FINALIZED_VALID:
        raise RunNotComparable(
            f"{run_dir} has lifecycle {view.lifecycle.value}; only "
            f"finalized_valid runs are comparable"
        )
    # Force the lazily-read optional projections *before* asking for errors.
    # A malformed inventory records its fault only when read, and leaves the
    # lifecycle at finalized_valid by design.
    view.materialize_diagnostics()
    if view.structural_errors:
        detail = "; ".join(
            f"{error.artifact}: {error.message}" for error in view.structural_errors[:5]
        )
        raise RunNotComparable(
            f"{run_dir} has {len(view.structural_errors)} structural error(s) and "
            f"is not comparable: {detail}"
        )


# --------------------------------------------------------------- projection ---


def _repository_projection(
    result: Mapping[str, Any], inventory: Mapping[str, Any] | None
) -> dict[str, Any]:
    acquisition = result.get("acquisition") or {}
    metrics = result.get("metrics") or {}

    identity = _select(result, REPOSITORY_PROVENANCE_FIELDS)
    # A legacy artifact has no `subject_key` field at all; supply the derived
    # one so ordering and cross-generation joins work without it.
    identity.setdefault("subject_key", _derived_subject_key(result))

    projected: dict[str, Any] = {
        "identity": identity | _select(acquisition, ACQUISITION_PROVENANCE_FIELDS),
        "measurement": _select(result, REPOSITORY_MEASUREMENT_FIELDS)
        | _select(metrics, METRIC_FIELDS),
        "git_evidence": _select(result, GIT_EVIDENCE_FIELDS),
    }

    if inventory is None:
        # Distinct from "an inventory with no files": absence is not emptiness.
        projected["inventory"] = None
    else:
        records = [
            _select(record, INVENTORY_RECORD_FIELDS)
            for record in inventory.get("files") or []
        ]
        projected["inventory"] = {
            "inventory_schema_version": inventory.get("inventory_schema_version"),
            "exclusion_policy_version": inventory.get("exclusion_policy_version"),
            # File order is traversal order, not meaning.
            "files": sorted(records, key=lambda item: (str(item["relative_path"]), _serialize(item))),
            "nested_repositories_excluded": _canonical(
                (inventory.get("summary") or {}).get("nested_repositories_excluded")
                or [],
                field="nested_repositories_excluded",
            ),
        }
    return projected


def _inventory_for(
    view: ImmutableRunView, result: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    slug = f"{result.get('repository_owner')}__{result.get('repository_name')}"
    for relative, document in view.inventories.items():
        if Path(relative).stem == slug:
            return document
    return None


def project_view(view: ImmutableRunView, run_dir: Path) -> dict[str, Any]:
    """Project an already-opened view. Refuses first; never partial."""
    _admit(view, run_dir)
    manifest = view.manifest

    repositories = [
        _repository_projection(result, _inventory_for(view, result))
        for result in view.repositories
    ]
    # Cohort input order is not semantic.
    # Ordered by logical identity. `repository_url` is nullable in Artifact
    # 1.7, so ordering on it would fail outright for a local subject and, worse,
    # would key the projection on a locator rather than an identity.
    repositories.sort(
        key=lambda item: (
            str(item["identity"].get("subject_key") or "").casefold(),
            _serialize(item),
        )
    )

    return {
        "semantic_projection_version": SEMANTIC_PROJECTION_VERSION,
        "semantic": {
            "run": _select(manifest, RUN_MEASUREMENT_FIELDS)
            | _select(manifest, CONTRACT_PROVENANCE_FIELDS),
            "repositories": repositories,
        },
        # Category C: reported so provenance is never silently discarded, and
        # excluded from the digest so it cannot cause false inequality.
        "environment": _select(manifest, ENVIRONMENT_FIELDS),
    }


def semantic_projection(run_dir: Path) -> dict[str, Any]:
    """Open a run strictly and project it, or refuse."""
    run_dir = Path(run_dir).resolve()
    try:
        view = open_run(run_dir)
    except Exception as exc:  # strict reader refusal is a refusal to compare
        raise RunNotComparable(f"{run_dir} could not be opened strictly: {exc}") from exc
    return project_view(view, run_dir)


def measurement_semantic_hash(run_dir: Path) -> str:
    """Digest of *what was measured, of which subject, under which contract*.

    **This is not a reproducibility digest and not an environment-equivalence
    digest.** It deliberately excludes category C: program version, grammar
    versions, interpreter version, profiler revision, acquisition mode, cache
    warmth. Two runs can share this digest while having been produced by
    different ArchLens builds on different machines — that is the point, because
    it is what makes the digest usable for proving a refactor changed nothing.

    Asking "are these two runs reproducible from the same environment?" is a
    different question, answered by comparing the ``environment`` section that
    :func:`semantic_projection` returns alongside this digest, or by the
    ``compare`` command's contract checks. Do not use this hash for that.
    """
    return hash_measurement_projection(semantic_projection(run_dir))


def hash_measurement_projection(projection: Mapping[str, Any]) -> str:
    """Digest a projection. Only ``semantic`` and the version are covered.

    ``environment`` is excluded by construction, not by omission: it is not put
    into the hashed payload at all, so a future field added there cannot start
    silently affecting measurement equality.
    """
    payload = {
        "semantic_projection_version": projection["semantic_projection_version"],
        "semantic": projection["semantic"],
    }
    return hashlib.sha256(_serialize(payload).encode("utf-8")).hexdigest()
