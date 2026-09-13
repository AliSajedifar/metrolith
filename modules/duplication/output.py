"""Standalone Duplication Output Contract 1.0.0.

This module orchestrates the already-validated D1--D3 primitives over one
immutable local snapshot.  It deliberately has no Artifact, Policy, SARIF,
GitHub Action, hotspot, persistence, or output-file responsibilities.  The CLI
owns source snapshot lifetime and the selected output sink.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from modules.config import AnalysisConfig, PROGRAM_VERSION
from modules.standalone_contracts import (
    DUPLICATION_CONTRACT_VERSION,
    DUPLICATION_FORMAT,
    DUPLICATION_FORMAT_VERSION,
)
from modules.duplication.candidates import extract_candidates
from modules.duplication.grouping import (
    occurrence_identity,
    validate_relative_path,
)
from modules.duplication.lexical import (
    LEXICAL_FINGERPRINT_VERSION,
    LexicalCanonicalizationError,
    frame,
    make_lexical_occurrence,
)
from modules.duplication.model import (
    MIN_DUPLICATED_NLOC,
    MIN_IMMEDIATE_STATEMENTS,
    MIN_SIGNIFICANT_TOKENS,
    CandidateExtractionStatus,
    LexicalCloneGroup,
    LexicalOccurrence,
    StructuralCloneGroup,
    StructuralGroupingResult,
    StructuralOccurrence,
)
from modules.duplication.structural import (
    STRUCTURAL_FINGERPRINT_VERSION,
    StructuralCanonicalizationUnavailable,
)
from modules.duplication.structural_grouping import (
    group_structural_clones,
    make_structural_occurrence,
)
from modules.duplication.grouping import group_lexical_clones
from modules.inventory import FileRecord, RepositoryInventory
from modules.local_source import LocalSnapshot
from modules.source_frontend import (
    ParserRegistry,
    ParserUnavailableError,
    SourceEncodingError,
    grammar_identity,
    select_syntax,
)
from modules.subject import compute_analysis_scope_hash

LEXICAL = "lexical"
STRUCTURAL = "structural"
KINDS = (LEXICAL, STRUCTURAL)

COMPLETE = "complete"
PARTIAL = "partial"
FAILED = "failed"
NOT_APPLICABLE = "not_applicable"
NOT_REQUESTED = "not_requested"
UNAVAILABLE = "unavailable"

SOURCE_UNREADABLE = "source_unreadable"
SOURCE_OVERSIZED = "source_oversized"
ENCODING_FAILED = "encoding_failed"
PARSER_UNAVAILABLE = "parser_unavailable"
SYNTAX_PARTIAL = "syntax_partial"
SOURCE_MAPPING_UNAVAILABLE = "source_mapping_unavailable"
CANDIDATE_EXTRACTION_FAILED = "candidate_extraction_failed"
LEXICAL_CANONICALIZATION_FAILED = "lexical_canonicalization_failed"
STRUCTURAL_CANONICALIZATION_FAILED = "structural_canonicalization_failed"
RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"

_FILE_STAGE_STATUSES = frozenset({COMPLETE, UNAVAILABLE, FAILED, NOT_REQUESTED})
_RESULT_STATUSES = frozenset(
    {COMPLETE, PARTIAL, FAILED, NOT_APPLICABLE, NOT_REQUESTED}
)
_REASONS = frozenset(
    {
        SOURCE_UNREADABLE,
        SOURCE_OVERSIZED,
        ENCODING_FAILED,
        PARSER_UNAVAILABLE,
        SYNTAX_PARTIAL,
        SOURCE_MAPPING_UNAVAILABLE,
        CANDIDATE_EXTRACTION_FAILED,
        LEXICAL_CANONICALIZATION_FAILED,
        STRUCTURAL_CANONICALIZATION_FAILED,
        RESOURCE_LIMIT_EXCEEDED,
    }
)
_ID = re.compile(r"^(?:do1|dg1):[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_GROUP_NAMESPACE = b"archlens-duplication-group"

_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "format_version",
        "duplication_contract_version",
        "program_version",
        "source",
        "provenance",
        "requested_kinds",
        "status",
        "counts",
        "files",
        "lexical_groups",
        "structural_groups",
    }
)
_SOURCE_KEYS = frozenset(
    {
        "analysis_scope_hash",
        "mode",
        "resolved_revision",
        "tracked_only",
        "working_tree_state",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "candidate_thresholds",
        "exclusion_policy_sha256",
        "exclusion_policy_version",
        "fingerprint_versions",
        "grammars",
        "inventory_schema_version",
        "maximum_source_file_size_bytes",
    }
)
_COUNT_KEYS = frozenset(
    {
        "candidate_complete_file_count",
        "candidate_unavailable_file_count",
        "eligible_file_count",
        "lexical",
        "observed_candidate_count",
        "structural",
    }
)
_LEXICAL_COUNT_KEYS = frozenset({"status", "group_count", "occurrence_count"})
_STRUCTURAL_COUNT_KEYS = frozenset(
    {
        "status",
        "initial_group_count",
        "suppressed_group_count",
        "retained_group_count",
        "occurrence_count",
    }
)
_FILE_KEYS = frozenset(
    {
        "candidate_count",
        "candidate_reason",
        "candidate_status",
        "language",
        "lexical_reason",
        "lexical_status",
        "path",
        "structural_reason",
        "structural_status",
    }
)
_GROUP_KEYS = frozenset(
    {
        "distribution",
        "file_count",
        "fingerprint",
        "fingerprint_version",
        "group_id",
        "language",
        "occurrence_count",
        "occurrences",
    }
)
_STRUCTURAL_GROUP_KEYS = frozenset(
    {*_GROUP_KEYS, "source_span_line_count", "source_span_union"}
)
_OCCURRENCE_KEYS = frozenset(
    {
        "duplicated_nloc",
        "end_line",
        "immediate_statement_count",
        "occurrence_id",
        "path",
        "significant_lexical_token_count",
        "start_line",
        "unit_kind",
    }
)
_SPAN_KEYS = frozenset({"end_line", "path", "start_line"})


class DuplicationOutputError(ValueError):
    """A product document contradicts Duplication Output Contract 1.0.0."""


@dataclass(frozen=True, slots=True)
class DuplicationAnalysis:
    """One deterministic document plus non-contract observational timings."""

    document: dict[str, Any]
    timings: dict[str, float]


def canonical_json(document: Mapping[str, Any]) -> str:
    """Return canonical UTF-8-compatible JSON text with exactly one final LF."""

    validate_duplication_document(document)
    return json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _file_document(record: FileRecord, requested: frozenset[str]) -> dict[str, Any]:
    return {
        "path": record.relative_path,
        "language": record.detected_language,
        "candidate_status": UNAVAILABLE,
        "candidate_reason": None,
        "candidate_count": None,
        "lexical_status": UNAVAILABLE if LEXICAL in requested else NOT_REQUESTED,
        "lexical_reason": None,
        "structural_status": (
            UNAVAILABLE if STRUCTURAL in requested else NOT_REQUESTED
        ),
        "structural_reason": None,
    }


def _set_candidate_unavailable(
    item: dict[str, Any], reason: str, requested: frozenset[str]
) -> None:
    item["candidate_status"] = UNAVAILABLE
    item["candidate_reason"] = reason
    item["candidate_count"] = None
    if LEXICAL in requested:
        item["lexical_status"] = UNAVAILABLE
        item["lexical_reason"] = reason
    if STRUCTURAL in requested:
        item["structural_status"] = UNAVAILABLE
        item["structural_reason"] = reason


def _source_reason(record: FileRecord) -> str:
    if record.oversized or record.read_status == "skipped_oversized":
        return SOURCE_OVERSIZED
    if record.encoding_error:
        return ENCODING_FAILED
    return SOURCE_UNREADABLE


def _extraction_reason(syntax: Any, raw_reason: str | None) -> str:
    reason = str(raw_reason or "")
    if "limit" in reason:
        return RESOURCE_LIMIT_EXCEEDED
    if syntax.parse_status in {"failed", "partial"}:
        return SYNTAX_PARTIAL
    if not syntax.mapping.newline_positions_preserved or (
        len(syntax.selected_source) != len(syntax.parser_source)
    ):
        return SOURCE_MAPPING_UNAVAILABLE
    return CANDIDATE_EXTRACTION_FAILED


def _grammar_documents(records: Iterable[FileRecord]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        identity = grammar_identity(record.detected_language, record.extension)
        item = {"language": record.detected_language, **identity}
        key = (
            item["language"],
            item["selected_grammar"],
            item["grammar_package"],
            item["grammar_version"],
            item["parser_implementation"],
        )
        unique[key] = item
    ordered = sorted(
        unique,
        key=lambda value: tuple(str(component or "") for component in value),
    )
    return [unique[key] for key in ordered]


def _occurrence_document(occurrence: LexicalOccurrence | StructuralOccurrence) -> dict[str, Any]:
    candidate = occurrence.candidate
    return {
        "path": occurrence.relative_path,
        "start_line": candidate.span.start_line,
        "end_line": candidate.span.end_line,
        "unit_kind": candidate.unit_kind.value,
        "occurrence_id": occurrence.occurrence_id,
        "immediate_statement_count": candidate.immediate_statement_count,
        "significant_lexical_token_count": (
            candidate.significant_lexical_token_count
        ),
        "duplicated_nloc": candidate.duplicated_nloc,
    }


def _group_document(group: LexicalCloneGroup | StructuralCloneGroup) -> dict[str, Any]:
    item: dict[str, Any] = {
        "group_id": group.group_id,
        "language": group.language,
        "fingerprint": group.fingerprint,
        "fingerprint_version": group.fingerprint_version,
        "distribution": group.distribution.value,
        "occurrence_count": group.occurrence_count,
        "file_count": group.file_count,
        "occurrences": [
            _occurrence_document(occurrence) for occurrence in group.occurrences
        ],
    }
    if isinstance(group, StructuralCloneGroup):
        item["source_span_union"] = [
            {
                "path": interval.relative_path,
                "start_line": interval.start_line,
                "end_line": interval.end_line,
            }
            for interval in group.source_span_union
        ]
        item["source_span_line_count"] = group.source_span_line_count
    return item


def _kind_status(files: Sequence[Mapping[str, Any]], kind: str) -> str:
    if not files:
        return NOT_APPLICABLE
    statuses = [str(item[f"{kind}_status"]) for item in files]
    completed = sum(status == COMPLETE for status in statuses)
    if completed == len(statuses):
        return COMPLETE
    if completed:
        return PARTIAL
    return FAILED


def _overall_status(kind_statuses: Iterable[str]) -> str:
    statuses = tuple(status for status in kind_statuses if status != NOT_REQUESTED)
    if FAILED in statuses:
        return FAILED
    if PARTIAL in statuses:
        return PARTIAL
    if COMPLETE in statuses:
        return COMPLETE
    return NOT_APPLICABLE


def analyze_duplication_snapshot(
    snapshot: LocalSnapshot,
    *,
    requested_kinds: Sequence[str] = KINDS,
    tracked_only: bool = False,
    config: AnalysisConfig | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> DuplicationAnalysis:
    """Analyze one immutable snapshot with the existing duplication engine."""

    requested_tuple = tuple(kind for kind in KINDS if kind in requested_kinds)
    if not requested_tuple or len(set(requested_kinds)) != len(requested_kinds):
        raise DuplicationOutputError("requested kinds must be a non-empty unique subset")
    if any(kind not in KINDS for kind in requested_kinds):
        raise DuplicationOutputError("requested kinds contain an unsupported value")
    requested = frozenset(requested_tuple)
    active_config = config or AnalysisConfig()

    timings = {
        "inventory_seconds": 0.0,
        "syntax_and_extraction_seconds": 0.0,
        "lexical_canonicalization_seconds": 0.0,
        "structural_canonicalization_seconds": 0.0,
        "lexical_grouping_seconds": 0.0,
        "structural_grouping_seconds": 0.0,
        "output_building_seconds": 0.0,
    }

    started = clock()
    inventory = RepositoryInventory(
        snapshot.path,
        active_config,
        full_inventory=False,
        **snapshot.inventory_options,
    )
    timings["inventory_seconds"] = clock() - started
    scope_hash = compute_analysis_scope_hash(inventory)
    eligible = sorted(
        (record for record in inventory if record.included_in_metrics),
        key=lambda record: record.relative_path,
    )
    registry = ParserRegistry()
    files: list[dict[str, Any]] = []
    lexical_occurrences: list[LexicalOccurrence] = []
    structural_occurrences: list[StructuralOccurrence] = []

    for record in eligible:
        file_item = _file_document(record, requested)
        files.append(file_item)
        source = inventory.read_bytes(record)
        if source is None:
            _set_candidate_unavailable(file_item, _source_reason(record), requested)
            continue

        started = clock()
        try:
            python_text = (
                inventory.read_text(record)
                if record.detected_language == "Python"
                else None
            )
            inventory.record_parser_invocation()
            syntax = select_syntax(
                record,
                source,
                registry,
                python_text=python_text,
            )
            extraction = extract_candidates(syntax)
        except ParserUnavailableError:
            timings["syntax_and_extraction_seconds"] += clock() - started
            _set_candidate_unavailable(file_item, PARSER_UNAVAILABLE, requested)
            continue
        except SourceEncodingError:
            timings["syntax_and_extraction_seconds"] += clock() - started
            _set_candidate_unavailable(file_item, ENCODING_FAILED, requested)
            continue
        timings["syntax_and_extraction_seconds"] += clock() - started

        if extraction.status is not CandidateExtractionStatus.COMPLETE:
            reason = _extraction_reason(syntax, extraction.reason)
            _set_candidate_unavailable(file_item, reason, requested)
            continue

        file_item["candidate_status"] = COMPLETE
        file_item["candidate_reason"] = None
        file_item["candidate_count"] = len(extraction.candidates)

        if LEXICAL in requested:
            local_lexical: list[LexicalOccurrence] = []
            lexical_failed = False
            started = clock()
            try:
                for candidate in extraction.candidates:
                    local_lexical.append(
                        make_lexical_occurrence(record.relative_path, syntax, candidate)
                    )
            except LexicalCanonicalizationError:
                lexical_failed = True
            timings["lexical_canonicalization_seconds"] += clock() - started
            if lexical_failed:
                file_item["lexical_status"] = UNAVAILABLE
                file_item["lexical_reason"] = LEXICAL_CANONICALIZATION_FAILED
            else:
                file_item["lexical_status"] = COMPLETE
                file_item["lexical_reason"] = None
                lexical_occurrences.extend(local_lexical)

        if STRUCTURAL in requested:
            local_structural: list[StructuralOccurrence] = []
            structural_failed = False
            started = clock()
            try:
                for candidate in extraction.candidates:
                    local_structural.append(
                        make_structural_occurrence(
                            record.relative_path,
                            syntax,
                            candidate,
                        )
                    )
            except StructuralCanonicalizationUnavailable:
                structural_failed = True
            timings["structural_canonicalization_seconds"] += clock() - started
            if structural_failed:
                file_item["structural_status"] = UNAVAILABLE
                file_item["structural_reason"] = STRUCTURAL_CANONICALIZATION_FAILED
            else:
                file_item["structural_status"] = COMPLETE
                file_item["structural_reason"] = None
                structural_occurrences.extend(local_structural)

    lexical_status = (
        _kind_status(files, LEXICAL) if LEXICAL in requested else NOT_REQUESTED
    )
    structural_status = (
        _kind_status(files, STRUCTURAL)
        if STRUCTURAL in requested
        else NOT_REQUESTED
    )

    lexical_groups: tuple[LexicalCloneGroup, ...] = ()
    if lexical_status not in {FAILED, NOT_REQUESTED}:
        started = clock()
        lexical_groups = group_lexical_clones(lexical_occurrences)
        timings["lexical_grouping_seconds"] = clock() - started

    structural_result = StructuralGroupingResult((), ())
    if structural_status not in {FAILED, NOT_REQUESTED}:
        started = clock()
        structural_result = group_structural_clones(structural_occurrences)
        timings["structural_grouping_seconds"] = clock() - started

    started = clock()
    candidate_complete = sum(
        item["candidate_status"] == COMPLETE for item in files
    )
    candidate_count: int | None
    if not files:
        candidate_count = 0
    elif candidate_complete:
        candidate_count = sum(
            int(item["candidate_count"])
            for item in files
            if item["candidate_status"] == COMPLETE
        )
    else:
        candidate_count = None

    lexical_measured = lexical_status not in {FAILED, NOT_REQUESTED}
    structural_measured = structural_status not in {FAILED, NOT_REQUESTED}
    document = {
        "format": DUPLICATION_FORMAT,
        "format_version": DUPLICATION_FORMAT_VERSION,
        "duplication_contract_version": DUPLICATION_CONTRACT_VERSION,
        "program_version": PROGRAM_VERSION,
        "source": {
            "mode": snapshot.source_mode.value,
            "resolved_revision": snapshot.analyzed_commit_sha,
            "tracked_only": bool(tracked_only),
            "working_tree_state": snapshot.working_tree_state.value,
            "analysis_scope_hash": scope_hash,
        },
        "provenance": {
            "inventory_schema_version": active_config.inventory_schema_version,
            "exclusion_policy_version": active_config.exclusion_policy_version,
            "exclusion_policy_sha256": (
                f"sha256:{active_config.exclusion_policy_sha256}"
            ),
            "maximum_source_file_size_bytes": (
                active_config.max_source_file_size_bytes
            ),
            "candidate_thresholds": {
                "minimum_immediate_statements": MIN_IMMEDIATE_STATEMENTS,
                "minimum_significant_lexical_tokens": MIN_SIGNIFICANT_TOKENS,
                "minimum_duplicated_nloc": MIN_DUPLICATED_NLOC,
            },
            "fingerprint_versions": {
                LEXICAL: LEXICAL_FINGERPRINT_VERSION,
                STRUCTURAL: STRUCTURAL_FINGERPRINT_VERSION,
            },
            "grammars": _grammar_documents(eligible),
        },
        "requested_kinds": list(requested_tuple),
        "status": _overall_status((lexical_status, structural_status)),
        "counts": {
            "eligible_file_count": len(files),
            "candidate_complete_file_count": candidate_complete,
            "candidate_unavailable_file_count": len(files) - candidate_complete,
            "observed_candidate_count": candidate_count,
            LEXICAL: {
                "status": lexical_status,
                "group_count": len(lexical_groups) if lexical_measured else None,
                "occurrence_count": (
                    sum(group.occurrence_count for group in lexical_groups)
                    if lexical_measured
                    else None
                ),
            },
            STRUCTURAL: {
                "status": structural_status,
                "initial_group_count": (
                    structural_result.initial_group_count
                    if structural_measured
                    else None
                ),
                "suppressed_group_count": (
                    len(structural_result.suppressed_group_ids)
                    if structural_measured
                    else None
                ),
                "retained_group_count": (
                    len(structural_result.groups) if structural_measured else None
                ),
                "occurrence_count": (
                    sum(group.occurrence_count for group in structural_result.groups)
                    if structural_measured
                    else None
                ),
            },
        },
        "files": files,
        "lexical_groups": [_group_document(group) for group in lexical_groups],
        "structural_groups": [
            _group_document(group) for group in structural_result.groups
        ],
    }
    validate_duplication_document(document)
    timings["output_building_seconds"] = clock() - started
    return DuplicationAnalysis(document=document, timings=timings)


def _require_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DuplicationOutputError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise DuplicationOutputError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    return value


def _require_nonnegative_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DuplicationOutputError(f"{label} must be a non-negative integer")
    return value


def _coordinate(item: Mapping[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(item["path"]),
        int(item["start_line"]),
        int(item["end_line"]),
        str(item["unit_kind"]),
    )


def _uint(value: int) -> bytes:
    if not (0 <= value < 1 << 64):
        raise DuplicationOutputError("coordinate integer is outside unsigned 64-bit range")
    return value.to_bytes(8, "big")


def _expected_group_id(group: Mapping[str, Any]) -> str:
    payload = bytearray(frame(_GROUP_NAMESPACE))
    payload.extend(frame(str(group["fingerprint_version"])))
    payload.extend(frame(str(group["fingerprint"])))
    for occurrence in group["occurrences"]:
        path, start, end, unit_kind = _coordinate(occurrence)
        validate_relative_path(path)
        payload.extend(frame(path))
        payload.extend(frame(_uint(start)))
        payload.extend(frame(_uint(end)))
        payload.extend(frame(unit_kind))
    return "dg1:" + hashlib.sha256(payload).hexdigest()


def _distribution(occurrences: Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    paths: dict[str, int] = {}
    for occurrence in occurrences:
        path = str(occurrence["path"])
        paths[path] = paths.get(path, 0) + 1
    if len(paths) == 1:
        return "same_file", 1
    if all(count == 1 for count in paths.values()):
        return "cross_file", len(paths)
    return "mixed", len(paths)


def _span_union(occurrences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for occurrence in occurrences:
        by_path.setdefault(str(occurrence["path"]), []).append(
            (int(occurrence["start_line"]), int(occurrence["end_line"]))
        )
    result: list[dict[str, Any]] = []
    for path in sorted(by_path):
        merged: list[list[int]] = []
        for start, end in sorted(by_path[path]):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        result.extend(
            {"path": path, "start_line": start, "end_line": end}
            for start, end in merged
        )
    return result


def _validate_occurrence(item: Any, label: str) -> Mapping[str, Any]:
    occurrence = _require_keys(item, _OCCURRENCE_KEYS, label)
    path = occurrence["path"]
    if not isinstance(path, str):
        raise DuplicationOutputError(f"{label}.path must be text")
    try:
        validate_relative_path(path)
    except ValueError as exc:
        raise DuplicationOutputError(f"{label}.path is not portable: {exc}") from exc
    start = _require_nonnegative_integer(occurrence["start_line"], f"{label}.start_line")
    end = _require_nonnegative_integer(occurrence["end_line"], f"{label}.end_line")
    if start < 1 or end < start:
        raise DuplicationOutputError(f"{label} has an invalid inclusive line range")
    for field in (
        "immediate_statement_count",
        "significant_lexical_token_count",
        "duplicated_nloc",
    ):
        _require_nonnegative_integer(occurrence[field], f"{label}.{field}")
    occurrence_id = occurrence["occurrence_id"]
    if not isinstance(occurrence_id, str) or not _ID.fullmatch(occurrence_id):
        raise DuplicationOutputError(f"{label}.occurrence_id is not canonical")
    expected_id = occurrence_identity(_coordinate(occurrence))
    if occurrence_id != expected_id:
        raise DuplicationOutputError(f"{label}.occurrence_id does not match coordinates")
    return occurrence


def _validate_groups(
    groups: Any,
    *,
    kind: str,
    files_by_path: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int]:
    if not isinstance(groups, list):
        raise DuplicationOutputError(f"{kind}_groups must be an array")
    expected_version = (
        LEXICAL_FINGERPRINT_VERSION if kind == LEXICAL else STRUCTURAL_FINGERPRINT_VERSION
    )
    group_keys: list[tuple[Any, ...]] = []
    seen_groups: set[str] = set()
    seen_coordinates: set[tuple[str, int, int, str]] = set()
    occurrence_total = 0
    for group_index, raw_group in enumerate(groups):
        label = f"{kind}_groups[{group_index}]"
        group = _require_keys(
            raw_group,
            _GROUP_KEYS if kind == LEXICAL else _STRUCTURAL_GROUP_KEYS,
            label,
        )
        if not isinstance(group["language"], str) or not group["language"]:
            raise DuplicationOutputError(f"{label}.language must be non-empty text")
        if group["fingerprint_version"] != expected_version:
            raise DuplicationOutputError(f"{label} has the wrong fingerprint version")
        if not isinstance(group["fingerprint"], str) or not _FINGERPRINT.fullmatch(
            group["fingerprint"]
        ):
            raise DuplicationOutputError(f"{label}.fingerprint is not canonical")
        if not isinstance(group["group_id"], str) or not _ID.fullmatch(group["group_id"]):
            raise DuplicationOutputError(f"{label}.group_id is not canonical")
        if group["group_id"] in seen_groups:
            raise DuplicationOutputError(f"duplicate group identity {group['group_id']!r}")
        seen_groups.add(str(group["group_id"]))
        occurrences = group["occurrences"]
        if not isinstance(occurrences, list) or len(occurrences) < 2:
            raise DuplicationOutputError(f"{label} must contain at least two occurrences")
        validated = [
            _validate_occurrence(item, f"{label}.occurrences[{index}]")
            for index, item in enumerate(occurrences)
        ]
        coordinates = [_coordinate(item) for item in validated]
        if coordinates != sorted(coordinates) or len(coordinates) != len(set(coordinates)):
            raise DuplicationOutputError(f"{label} occurrence ordering is not canonical")
        for occurrence, coordinate in zip(validated, coordinates):
            if coordinate in seen_coordinates:
                raise DuplicationOutputError(
                    f"one {kind} occurrence participates in multiple groups"
                )
            seen_coordinates.add(coordinate)
            file_item = files_by_path.get(str(occurrence["path"]))
            if file_item is None or file_item["language"] != group["language"]:
                raise DuplicationOutputError(f"{label} occurrence has no matching file")
            if file_item[f"{kind}_status"] != COMPLETE:
                raise DuplicationOutputError(f"{label} uses an incomplete file")
        if group["group_id"] != _expected_group_id(group):
            raise DuplicationOutputError(f"{label}.group_id does not match membership")
        occurrence_count = _require_nonnegative_integer(
            group["occurrence_count"], f"{label}.occurrence_count"
        )
        if occurrence_count != len(validated):
            raise DuplicationOutputError(f"{label}.occurrence_count does not reconcile")
        expected_distribution, file_count = _distribution(validated)
        if group["distribution"] != expected_distribution:
            raise DuplicationOutputError(f"{label}.distribution does not reconcile")
        if group["file_count"] != file_count:
            raise DuplicationOutputError(f"{label}.file_count does not reconcile")
        if kind == STRUCTURAL:
            union = group["source_span_union"]
            if not isinstance(union, list):
                raise DuplicationOutputError(f"{label}.source_span_union must be an array")
            for span_index, span in enumerate(union):
                _require_keys(span, _SPAN_KEYS, f"{label}.source_span_union[{span_index}]")
            expected_union = _span_union(validated)
            if union != expected_union:
                raise DuplicationOutputError(f"{label}.source_span_union does not reconcile")
            expected_lines = sum(
                int(span["end_line"]) - int(span["start_line"]) + 1
                for span in expected_union
            )
            if group["source_span_line_count"] != expected_lines:
                raise DuplicationOutputError(
                    f"{label}.source_span_line_count does not reconcile"
                )
        occurrence_total += occurrence_count
        group_keys.append(
            (
                group["language"],
                group["fingerprint"],
                tuple(coordinates),
                group["group_id"],
            )
        )
    if group_keys != sorted(group_keys):
        raise DuplicationOutputError(f"{kind} group ordering is not canonical")
    return len(groups), occurrence_total


def _validate_file(item: Any, index: int, requested: frozenset[str]) -> Mapping[str, Any]:
    label = f"files[{index}]"
    file_item = _require_keys(item, _FILE_KEYS, label)
    path = file_item["path"]
    if not isinstance(path, str):
        raise DuplicationOutputError(f"{label}.path must be text")
    try:
        validate_relative_path(path)
    except ValueError as exc:
        raise DuplicationOutputError(f"{label}.path is not portable: {exc}") from exc
    if not isinstance(file_item["language"], str) or not file_item["language"]:
        raise DuplicationOutputError(f"{label}.language must be non-empty text")
    for stage in ("candidate", LEXICAL, STRUCTURAL):
        status = file_item[f"{stage}_status"]
        reason = file_item[f"{stage}_reason"]
        if status not in _FILE_STAGE_STATUSES:
            raise DuplicationOutputError(f"{label}.{stage}_status is unsupported")
        if status in {UNAVAILABLE, FAILED}:
            if reason not in _REASONS:
                raise DuplicationOutputError(f"{label}.{stage}_reason is required")
        elif reason is not None:
            raise DuplicationOutputError(f"{label}.{stage}_reason must be null")
    candidate_count = file_item["candidate_count"]
    if file_item["candidate_status"] == COMPLETE:
        _require_nonnegative_integer(candidate_count, f"{label}.candidate_count")
    elif candidate_count is not None:
        raise DuplicationOutputError(f"{label}.candidate_count must be null")
    for kind in KINDS:
        status = file_item[f"{kind}_status"]
        if kind in requested and status == NOT_REQUESTED:
            raise DuplicationOutputError(f"{label}.{kind} was requested")
        if kind not in requested and status != NOT_REQUESTED:
            raise DuplicationOutputError(f"{label}.{kind} was not requested")
        if file_item["candidate_status"] != COMPLETE and status == COMPLETE:
            raise DuplicationOutputError(f"{label}.{kind} completed without candidates")
    return file_item


def _expected_kind_status(
    files: Sequence[Mapping[str, Any]], kind: str, requested: frozenset[str]
) -> str:
    return _kind_status(files, kind) if kind in requested else NOT_REQUESTED


def validate_duplication_document(document: Mapping[str, Any]) -> None:
    """Reject any document that contradicts the standalone output contract."""

    root = _require_keys(document, _TOP_LEVEL_KEYS, "document")
    if root["format"] != DUPLICATION_FORMAT:
        raise DuplicationOutputError("unexpected duplication format")
    if root["format_version"] != DUPLICATION_FORMAT_VERSION:
        raise DuplicationOutputError("unsupported duplication format version")
    if root["duplication_contract_version"] != DUPLICATION_CONTRACT_VERSION:
        raise DuplicationOutputError("unsupported duplication contract version")
    if root["program_version"] != PROGRAM_VERSION:
        raise DuplicationOutputError("program version does not match this producer")

    source = _require_keys(root["source"], _SOURCE_KEYS, "source")
    if not isinstance(source["mode"], str) or not source["mode"]:
        raise DuplicationOutputError("source.mode must be non-empty text")
    if source["resolved_revision"] is not None and not re.fullmatch(
        r"[0-9a-f]{40}", str(source["resolved_revision"])
    ):
        raise DuplicationOutputError("source.resolved_revision must be a full Git SHA")
    if not isinstance(source["tracked_only"], bool):
        raise DuplicationOutputError("source.tracked_only must be boolean")
    if not isinstance(source["working_tree_state"], str):
        raise DuplicationOutputError("source.working_tree_state must be text")
    if not isinstance(source["analysis_scope_hash"], str) or not _FINGERPRINT.fullmatch(
        source["analysis_scope_hash"]
    ):
        raise DuplicationOutputError("source.analysis_scope_hash is not canonical")

    provenance = _require_keys(root["provenance"], _PROVENANCE_KEYS, "provenance")
    thresholds = _require_keys(
        provenance["candidate_thresholds"],
        frozenset(
            {
                "minimum_immediate_statements",
                "minimum_significant_lexical_tokens",
                "minimum_duplicated_nloc",
            }
        ),
        "provenance.candidate_thresholds",
    )
    expected_thresholds = {
        "minimum_immediate_statements": MIN_IMMEDIATE_STATEMENTS,
        "minimum_significant_lexical_tokens": MIN_SIGNIFICANT_TOKENS,
        "minimum_duplicated_nloc": MIN_DUPLICATED_NLOC,
    }
    if dict(thresholds) != expected_thresholds:
        raise DuplicationOutputError("candidate thresholds do not match D1")
    fingerprints = _require_keys(
        provenance["fingerprint_versions"],
        frozenset(KINDS),
        "provenance.fingerprint_versions",
    )
    if fingerprints != {
        LEXICAL: LEXICAL_FINGERPRINT_VERSION,
        STRUCTURAL: STRUCTURAL_FINGERPRINT_VERSION,
    }:
        raise DuplicationOutputError("fingerprint provenance is inconsistent")
    if not isinstance(provenance["grammars"], list):
        raise DuplicationOutputError("provenance.grammars must be an array")
    if not isinstance(provenance["exclusion_policy_sha256"], str) or not _FINGERPRINT.fullmatch(
        provenance["exclusion_policy_sha256"]
    ):
        raise DuplicationOutputError("exclusion policy hash is not canonical")
    _require_nonnegative_integer(
        provenance["maximum_source_file_size_bytes"],
        "provenance.maximum_source_file_size_bytes",
    )

    requested_raw = root["requested_kinds"]
    if not isinstance(requested_raw, list) or not requested_raw:
        raise DuplicationOutputError("requested_kinds must be a non-empty array")
    if requested_raw != [kind for kind in KINDS if kind in requested_raw]:
        raise DuplicationOutputError("requested_kinds ordering is not canonical")
    if len(requested_raw) != len(set(requested_raw)) or any(
        kind not in KINDS for kind in requested_raw
    ):
        raise DuplicationOutputError("requested_kinds contains invalid values")
    requested = frozenset(requested_raw)

    raw_files = root["files"]
    if not isinstance(raw_files, list):
        raise DuplicationOutputError("files must be an array")
    files = [_validate_file(item, index, requested) for index, item in enumerate(raw_files)]
    paths = [str(item["path"]) for item in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise DuplicationOutputError("file ordering is not canonical")
    files_by_path = {str(item["path"]): item for item in files}

    lexical_group_count, lexical_occurrence_count = _validate_groups(
        root["lexical_groups"], kind=LEXICAL, files_by_path=files_by_path
    )
    structural_group_count, structural_occurrence_count = _validate_groups(
        root["structural_groups"], kind=STRUCTURAL, files_by_path=files_by_path
    )

    counts = _require_keys(root["counts"], _COUNT_KEYS, "counts")
    eligible_count = _require_nonnegative_integer(
        counts["eligible_file_count"], "counts.eligible_file_count"
    )
    if eligible_count != len(files):
        raise DuplicationOutputError("eligible file count does not reconcile")
    complete_count = sum(item["candidate_status"] == COMPLETE for item in files)
    if counts["candidate_complete_file_count"] != complete_count:
        raise DuplicationOutputError("candidate complete file count does not reconcile")
    if counts["candidate_unavailable_file_count"] != len(files) - complete_count:
        raise DuplicationOutputError("candidate unavailable file count does not reconcile")
    expected_candidates: int | None
    if not files:
        expected_candidates = 0
    elif complete_count:
        expected_candidates = sum(
            int(item["candidate_count"])
            for item in files
            if item["candidate_status"] == COMPLETE
        )
    else:
        expected_candidates = None
    if counts["observed_candidate_count"] != expected_candidates:
        raise DuplicationOutputError("observed candidate count does not reconcile")

    lexical_counts = _require_keys(counts[LEXICAL], _LEXICAL_COUNT_KEYS, "counts.lexical")
    structural_counts = _require_keys(
        counts[STRUCTURAL], _STRUCTURAL_COUNT_KEYS, "counts.structural"
    )
    expected_lexical_status = _expected_kind_status(files, LEXICAL, requested)
    expected_structural_status = _expected_kind_status(files, STRUCTURAL, requested)
    if lexical_counts["status"] != expected_lexical_status:
        raise DuplicationOutputError("lexical status does not reconcile")
    if structural_counts["status"] != expected_structural_status:
        raise DuplicationOutputError("structural status does not reconcile")
    for status in (lexical_counts["status"], structural_counts["status"]):
        if status not in _RESULT_STATUSES:
            raise DuplicationOutputError("kind status is unsupported")

    lexical_measured = expected_lexical_status not in {FAILED, NOT_REQUESTED}
    if lexical_counts["group_count"] != (
        lexical_group_count if lexical_measured else None
    ) or lexical_counts["occurrence_count"] != (
        lexical_occurrence_count if lexical_measured else None
    ):
        raise DuplicationOutputError("lexical counts do not reconcile")
    if not lexical_measured and root["lexical_groups"]:
        raise DuplicationOutputError("unmeasured lexical analysis emitted groups")

    structural_measured = expected_structural_status not in {FAILED, NOT_REQUESTED}
    expected_structural_values = (
        (structural_group_count, structural_occurrence_count)
        if structural_measured
        else (None, None)
    )
    if structural_counts["retained_group_count"] != expected_structural_values[0]:
        raise DuplicationOutputError("structural retained group count does not reconcile")
    if structural_counts["occurrence_count"] != expected_structural_values[1]:
        raise DuplicationOutputError("structural occurrence count does not reconcile")
    if structural_measured:
        initial = _require_nonnegative_integer(
            structural_counts["initial_group_count"],
            "counts.structural.initial_group_count",
        )
        suppressed = _require_nonnegative_integer(
            structural_counts["suppressed_group_count"],
            "counts.structural.suppressed_group_count",
        )
        if initial != structural_group_count + suppressed:
            raise DuplicationOutputError("structural suppression counts do not reconcile")
    elif (
        structural_counts["initial_group_count"] is not None
        or structural_counts["suppressed_group_count"] is not None
        or root["structural_groups"]
    ):
        raise DuplicationOutputError("unmeasured structural analysis emitted counts")

    expected_overall = _overall_status(
        (expected_lexical_status, expected_structural_status)
    )
    if root["status"] != expected_overall:
        raise DuplicationOutputError("overall status does not reconcile")


def render_text(document: Mapping[str, Any]) -> str:
    """Render deterministic human output without weakening machine identity."""

    validate_duplication_document(document)
    source = document["source"]
    counts = document["counts"]
    thresholds = document["provenance"]["candidate_thresholds"]
    lines = [
        f"Metrolith duplication {document['format_version']} "
        f"(Program {document['program_version']})",
        f"scope:  {source['analysis_scope_hash']}",
        f"source: {source['mode']}",
        "revision: " + (source["resolved_revision"] or "not_applicable"),
        f"status: {document['status']}",
        (
            "files: "
            f"{counts['eligible_file_count']} eligible, "
            f"{counts['candidate_complete_file_count']} candidate-complete, "
            f"{counts['candidate_unavailable_file_count']} unavailable"
        ),
        "candidates observed: "
        + (
            str(counts["observed_candidate_count"])
            if counts["observed_candidate_count"] is not None
            else "unavailable"
        ),
        (
            "candidate thresholds: "
            f"duplicated NLOC >= {thresholds['minimum_duplicated_nloc']}, "
            f"immediate statements >= {thresholds['minimum_immediate_statements']}, "
            "significant lexical tokens >= "
            f"{thresholds['minimum_significant_lexical_tokens']}"
        ),
        "",
    ]

    lexical = counts[LEXICAL]
    if lexical["status"] in {FAILED, NOT_REQUESTED}:
        lines.append(f"Lexical exact: {lexical['status']}")
    else:
        lines.append(
            f"Lexical exact: {lexical['status']}, {lexical['group_count']} groups, "
            f"{lexical['occurrence_count']} occurrences"
        )
    lines.append("")
    for index, group in enumerate(document["lexical_groups"], start=1):
        lines.extend(_render_group(group, f"L{index:03d}"))

    structural = counts[STRUCTURAL]
    if structural["status"] in {FAILED, NOT_REQUESTED}:
        lines.append(f"Structural: {structural['status']}")
    else:
        lines.extend(
            [
                (
                    f"Structural: {structural['status']}, "
                    f"{structural['retained_group_count']} retained groups, "
                    f"{structural['occurrence_count']} occurrences"
                ),
                (
                    f"            {structural['initial_group_count']} initial groups, "
                    f"{structural['suppressed_group_count']} suppressed by maximality"
                ),
            ]
        )
    lines.append("")
    for index, group in enumerate(document["structural_groups"], start=1):
        lines.extend(_render_group(group, f"S{index:03d}"))

    total_groups = len(document["lexical_groups"]) + len(document["structural_groups"])
    if total_groups == 0 and counts["candidate_complete_file_count"]:
        lines.extend([
            "Measured zero duplicate groups",
            "  Candidate extraction completed, but no exact group met the published "
            "minimums above. This is a measured zero, not unavailable evidence.",
            "",
        ])

    unavailable = [
        item
        for item in document["files"]
        if item["candidate_status"] != COMPLETE
        or any(
            item[f"{kind}_status"] in {UNAVAILABLE, FAILED}
            for kind in document["requested_kinds"]
        )
    ]
    if unavailable:
        lines.append("Unavailable files")
        for item in unavailable:
            reasons: list[str] = []
            for stage in ("candidate", *document["requested_kinds"]):
                reason = item[f"{stage}_reason"]
                if reason and reason not in reasons:
                    reasons.append(str(reason))
            lines.append(f"  {item['path']}: {', '.join(reasons)}")
        lines.append("")
    lines.append(
        "Exact canonical duplicate groups only; no score, similarity, defect, or risk claim."
    )
    return "\n".join(lines) + "\n"


def _render_group(group: Mapping[str, Any], display_id: str) -> list[str]:
    lines = [
        f"[{display_id}] {group['group_id']}",
        (
            f"  {group['language']} | {group['distribution']} | "
            f"{group['occurrence_count']} occurrences in {group['file_count']} files"
        ),
        f"  fingerprint: {group['fingerprint_version']} {group['fingerprint']}",
    ]
    for occurrence in group["occurrences"]:
        lines.extend(
            [
                (
                    f"  {occurrence['path']}:{occurrence['start_line']}-"
                    f"{occurrence['end_line']} {occurrence['unit_kind']} "
                    f"{occurrence['occurrence_id']}"
                ),
                (
                    "    statements="
                    f"{occurrence['immediate_statement_count']} "
                    f"tokens={occurrence['significant_lexical_token_count']} "
                    f"duplicated_nloc={occurrence['duplicated_nloc']}"
                ),
            ]
        )
    lines.append("")
    return lines


__all__ = [
    "DUPLICATION_CONTRACT_VERSION",
    "DUPLICATION_FORMAT",
    "DUPLICATION_FORMAT_VERSION",
    "DuplicationAnalysis",
    "DuplicationOutputError",
    "analyze_duplication_snapshot",
    "canonical_json",
    "render_text",
    "validate_duplication_document",
]
