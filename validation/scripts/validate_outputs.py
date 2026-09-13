#!/usr/bin/env python3
"""Independent cross-artifact and semantic-run validation for ArchLens pilots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


LANGUAGES = ("java", "javascript", "typescript", "python", "go")
CORE_FIELDS = ("lines_of_code", "source_files", "classes_structs", "methods_functions")
STATUS_RANK = {
    "not_applicable": 0,
    "complete": 1,
    "partial": 2,
    "failed": 3,
}
EXPECTED_LANGUAGE_FAMILIES = {
    "java": ("java",),
    "javascript": ("javascript", "typescript"),
    "typescript": ("javascript", "typescript"),
    "python": ("python",),
    "go": ("go",),
}


def schema_at_least(value: Any, expected: tuple[int, int]) -> bool:
    try:
        parts = tuple(int(part) for part in str(value).split(".")[:2])
    except ValueError:
        return False
    return parts >= expected


def independently_derive_repository_diagnostics(
    result: dict[str, Any],
) -> dict[str, str]:
    expected = str(result.get("expected_language") or "").strip().casefold()
    family = EXPECTED_LANGUAGE_FAMILIES.get(expected)
    metrics = result.get("metrics") or {}
    by_language = metrics.get("by_language") or {}
    applicable = []
    if family:
        applicable = [
            row
            for language in family
            if isinstance((row := by_language.get(language)), dict)
            and int(row.get("source_files") or 0) > 0
        ]
    if not family:
        expected_status = "not_applicable"
    elif applicable:
        expected_status = max(
            (str(row.get("metric_status") or "not_applicable") for row in applicable),
            key=lambda status: STATUS_RANK.get(status, 3),
        )
    elif result.get("analysis_status") in {"failed", "interrupted"}:
        expected_status = "failed"
    else:
        expected_status = "not_applicable"

    if result.get("analysis_status") == "complete":
        origin = "none"
    else:
        contributors: set[str] = set()
        aggregate = metrics.get("aggregate") or {}
        if (
            any(
                error.get("module") in {"acquisition", "inventory"}
                for error in result.get("errors", [])
                if isinstance(error, dict)
            )
            or aggregate.get("inventory_status") in {"partial", "failed"}
            or aggregate.get("source_files_status") in {"partial", "failed"}
        ):
            contributors.add("acquisition_or_inventory")
        if applicable and expected_status in {"partial", "failed"}:
            contributors.add("expected_language_family")
        outside_family = set(by_language) - set(family or ())
        if any(
            isinstance(by_language.get(language), dict)
            and int(by_language[language].get("source_files") or 0) > 0
            and by_language[language].get("metric_status") in {"partial", "failed"}
            for language in outside_family
        ):
            contributors.add("secondary_supported_language_only")
        if not contributors:
            contributors.add("multiple")
        origin = next(iter(contributors)) if len(contributors) == 1 else "multiple"
    return {
        "expected_language_family_status": expected_status,
        "partial_origin": origin,
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_fieldnames(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def csv_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_REMOTE_LOCATOR = re.compile(
    r"^(?:https?://|git@|ssh://git@)(?P<host>[^/:]+)[:/](?P<owner>[^/]+)/"
    r"(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _subject_key(result: dict[str, Any]) -> str:
    """Logical subject identity, derived independently of the producer.

    This validator must never import the production modules, so the canonical
    key is recomputed here rather than shared. That is the point of the
    independence rule: a shared helper would make the validator agree with the
    producer by construction instead of by evidence.

    Artifact 1.7 records `subject_key` directly. Older artifacts have only a
    repository URL, so the same canonical form is derived from it at read time —
    no historical bytes are rewritten.
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


def inventory_slug(result: dict[str, Any]) -> str:
    # Must be byte-for-byte the writer's portable filename construction. Local
    # display names may contain spaces or punctuation; using the raw display
    # value here made an otherwise valid packaged-example run impossible to
    # validate after finalization.
    def safe(value: Any) -> str:
        return "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in str(value)
        )

    return f"{safe(result['repository_owner'])}__{safe(result['repository_name'])}"


def normalized_repository(result: dict[str, Any], inventory: dict[str, Any] | None) -> dict[str, Any]:
    metrics = result["metrics"]
    normalized = {
        "repository_url": result["repository_url"],
        "architecture_type": result["architecture_type"],
        "expected_language": result.get("expected_language"),
        # The repository's terminal status. Omitting it meant `compare` could not
        # see a repository move between complete, partial, failed and
        # interrupted: two runs that disagreed about whether a repository was
        # even fully measured were reported semantically equal so long as the
        # numbers they did produce matched. Per-metric statuses do not cover
        # this — `analysis_status` also carries acquisition and interruption
        # outcomes that never reach `metrics.aggregate`.
        "analysis_status": result.get("analysis_status"),
        "expected_language_family_status": result.get(
            "expected_language_family_status"
        ),
        "partial_origin": result.get("partial_origin"),
        "analyzed_commit_sha": result.get("acquisition", {}).get("analyzed_commit_sha"),
        "commit_verification_status": result.get("acquisition", {}).get("commit_verification_status"),
        "primary_language_name": metrics.get("primary_language_name"),
        "aggregate": metrics.get("aggregate"),
        "by_language": metrics.get("by_language"),
        "parser_status_by_language": metrics.get("parser_status_by_language"),
        "parse_errors": metrics.get("parse_errors"),
        "parser_diagnostics": metrics.get("parser_diagnostics"),
        "recovered_parser_diagnostics": metrics.get("recovered_parser_diagnostics"),
        "recovery_taxonomy": metrics.get("recovery_taxonomy"),
        "javascript_family_scope": metrics.get("javascript_family_scope"),
        "error_taxonomy": metrics.get("error_taxonomy"),
        "core_metric_status": result.get("core_metric_status"),
        "git_mode_map_status": result.get("git_mode_map_status"),
        "git_mode_map_available": result.get("git_mode_map_available"),
        "git_mode_entry_count": result.get("git_mode_entry_count"),
    }
    if inventory is not None:
        normalized["inventory"] = {
            "inventory_schema_version": inventory.get("inventory_schema_version"),
            "exclusion_policy_version": inventory.get("exclusion_policy_version"),
            "files": [
                {
                    key: record.get(key)
                    for key in (
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
                }
                for record in inventory.get("files", [])
            ],
            "nested_repositories_excluded": inventory.get("summary", {}).get(
                "nested_repositories_excluded", []
            ),
        }
    return normalized


def semantic_payload(run_dir: Path) -> dict[str, Any]:
    analysis = read_json(run_dir / "analysis.json")
    repositories = []
    for result in sorted(analysis, key=lambda item: _subject_key(item).casefold()):
        inventory_path = run_dir / "file_inventory" / f"{inventory_slug(result)}.json"
        inventory = read_json(inventory_path) if inventory_path.exists() else None
        repositories.append(normalized_repository(result, inventory))
    return {"repositories": repositories}


#: Qualification enums, restated here on purpose. This validator is meant to be
#: an INDEPENDENT check, so it must not import the producer's derivation and
#: then declare that the producer agrees with itself. The rules below are
#: transcribed from the R0 plan (sections 5.3, 5.4 and 10), not from
#: `modules.benchmark_qualification`.
_FAMILY_STATUS_PATHS = (
    ("source_files", ("metrics", "aggregate", "source_files_status")),
    ("lines_of_code", ("metrics", "aggregate", "loc_status")),
    ("classes_structs", ("metrics", "aggregate", "classes_structs_status")),
    ("methods_functions", ("metrics", "aggregate", "methods_functions_status")),
    ("callable_metrics", ("metrics", "complexity", "status")),
)


def _dig(source: Any, path: tuple[str, ...]) -> Any:
    node = source
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _independent_families(result: dict[str, Any]) -> list[str]:
    return [
        family
        for family, path in _FAMILY_STATUS_PATHS
        if _dig(result, path) in {"complete", "partial"}
    ]


def _independent_usability(
    metric_status: Any, representativeness: str, restriction: str, families: list[str]
) -> str | None:
    if metric_status == "failed":
        return None
    if restriction == "unsupported_scope":
        return "unsupported_scope"
    if metric_status == "partial":
        return "partial_but_usable" if families else None
    if metric_status == "complete":
        if restriction == "analyzed_scope_only":
            return "analyzed_scope_only"
        return (
            "repository_level_usable"
            if representativeness == "ADEQUATE"
            else "analyzed_scope_only"
        )
    return None


def validate_qualification(
    run_dir: Path,
    analysis: list[dict[str, Any]],
    manifest: dict[str, Any],
    sheet: dict[str, dict[str, Any]],
) -> list[str]:
    """Independently reconcile the qualification authority and its projections.

    Re-derives every derived field from `analysis.json` using rules transcribed
    from the plan, rather than calling the producer's own derivation. A
    validator that asked the producer to confirm the producer would pass
    unconditionally and prove nothing.
    """
    failures: list[str] = []
    mode = manifest.get("qualification_mode")
    artifact_path = run_dir / "benchmark_qualification.json"
    projection_path = run_dir / "repository_level_metrics.csv"

    if mode not in {"benchmark_qualified", "not_requested", None}:
        failures.append(f"manifest declares unknown qualification_mode {mode!r}")

    if mode != "benchmark_qualified":
        # Absence is the contract here, and a stray artifact is a defect rather
        # than a bonus: it would advertise an authority the run never
        # established.
        if artifact_path.exists():
            failures.append(
                "benchmark_qualification.json is present in a run that did not "
                "request qualification"
            )
        if projection_path.exists():
            failures.append(
                "repository_level_metrics.csv is present in a run that did not "
                "request qualification"
            )
        for row in sheet.values():
            # Only an EXPLICITLY TRUE cell is a violation. A pre-1.11 run has no
            # such column at all, and absence is the correct historical state:
            # legacy qualification is not evaluable, and eligibility is false.
            # Treating a missing column as a violation would report every
            # preserved fixture as defective for lacking a field its own
            # generation never defined.
            if str(row.get("repository_level_comparison_eligible")) in {
                "True", "true", "TRUE",
            }:
                failures.append(
                    f"{_subject_key(row)}: an unqualified run projects "
                    f"repository-level eligibility true"
                )
        return failures

    if not artifact_path.is_file():
        return failures + [
            "benchmark_qualification.json is mandatory in benchmark-qualified "
            "mode and is absent"
        ]

    artifact = read_json(artifact_path)
    binding = manifest.get("benchmark_qualification_artifact") or {}

    # The manifest binding must describe the bytes actually on disk.
    raw = artifact_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if binding.get("sha256") != actual_sha:
        failures.append("manifest qualification SHA-256 does not match the artifact")
    if binding.get("size_bytes") != len(raw):
        failures.append("manifest qualification size_bytes does not match the artifact")
    records = list(artifact.get("records") or ())
    if binding.get("record_count") != len(records):
        failures.append("manifest qualification record_count does not match the artifact")

    if len(records) != len(analysis):
        failures.append(
            f"qualification holds {len(records)} record(s) for {len(analysis)} "
            f"analyzed result(s)"
        )

    by_key: dict[tuple, dict[str, Any]] = {}
    for record in records:
        key = tuple(
            (record.get("binding") or {}).get(name)
            for name in (
                "qualification_profile",
                "subject_key",
                "analyzed_commit_sha",
                "analysis_scope_hash",
                "analysis_scope_hash_version",
            )
        )
        if key in by_key:
            failures.append(f"duplicate qualification record for binding {key}")
        by_key[key] = record

    expected_eligible: set[str] = set()
    for result in analysis:
        key = (
            artifact.get("qualification_profile"),
            _subject_key(result),
            (result.get("acquisition") or {}).get("analyzed_commit_sha"),
            result.get("analysis_scope_hash"),
            result.get("analysis_scope_hash_version"),
        )
        record = by_key.get(key)
        if record is None:
            failures.append(
                f"{_subject_key(result)}: no qualification record exactly binds "
                f"this analyzed revision and scope"
            )
            continue

        metric_status = _dig(result, ("metrics", "aggregate", "metric_status"))
        families = _independent_families(result)
        if list(record.get("usable_metric_families") or ()) != families:
            failures.append(
                f"{_subject_key(result)}: usable_metric_families disagrees with "
                f"the independent status derivation"
            )
        representativeness = str(record.get("repository_representativeness"))
        restriction = str(record.get("manual_admission_restriction"))
        usability = _independent_usability(
            metric_status, representativeness, restriction, families
        )
        if record.get("benchmark_usability") != usability:
            failures.append(
                f"{_subject_key(result)}: benchmark_usability "
                f"{record.get('benchmark_usability')!r} disagrees with the "
                f"independent derivation {usability!r}"
            )
        eligible = (
            record.get("qualification_status") == "adjudicated"
            and representativeness == "ADEQUATE"
            and usability == "repository_level_usable"
            and metric_status == "complete"
            and restriction == "none"
        )
        if bool(record.get("repository_level_comparison_eligible")) != eligible:
            failures.append(
                f"{_subject_key(result)}: repository_level_comparison_eligible "
                f"disagrees with the independent derivation"
            )
        if eligible:
            expected_eligible.add(_subject_key(result))

        # Projection cells must equal the authority.
        row = sheet.get(_subject_key(result))
        if row is not None:
            if row.get("repository_representativeness") != representativeness:
                failures.append(
                    f"{_subject_key(result)}: sheet_metrics.csv "
                    f"representativeness differs from the qualification authority"
                )
            if (row.get("benchmark_usability") or None) != usability:
                failures.append(
                    f"{_subject_key(result)}: sheet_metrics.csv usability differs "
                    f"from the qualification authority"
                )

    if not projection_path.is_file():
        failures.append(
            "repository_level_metrics.csv is mandatory in benchmark-qualified mode"
        )
    else:
        projected = {_subject_key(row) for row in read_csv(projection_path)}
        for extra in sorted(projected - expected_eligible):
            failures.append(
                f"repository_level_metrics.csv contains {extra}, which is not "
                f"independently derived as eligible"
            )
        for missing in sorted(expected_eligible - projected):
            failures.append(
                f"repository_level_metrics.csv omits independently eligible {missing}"
            )
    return failures


def validate_run(run_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    analysis = read_json(run_dir / "analysis.json")
    manifest = read_json(run_dir / "run_manifest.json")
    run_status = read_json(run_dir / "run_status.json")
    # Keyed on the logical subject, never on the locator. Artifact 1.7 makes
    # `repository_url` nullable, so a local subject has no URL to key on and
    # every one of these tables would collapse onto a single empty-string key.
    catalog = {_subject_key(row): row for row in read_csv(run_dir / "catalog.csv")}
    sheet = {_subject_key(row): row for row in read_csv(run_dir / "sheet_metrics.csv")}
    language_rows = {
        (_subject_key(row), row["language"]): row
        for row in read_csv(run_dir / "language_metrics.csv")
    }
    error_rows = read_csv(run_dir / "errors.csv")
    recovery_rows = read_csv(run_dir / "recoveries.csv")
    diagnostic_schema = schema_at_least(
        manifest.get("artifact_schema_version"), (1, 4)
    )
    if diagnostic_schema:
        for table_name in ("sheet_metrics.csv", "catalog.csv"):
            fields = set(csv_fieldnames(run_dir / table_name))
            for required in (
                "expected_language_family_status",
                "partial_origin",
            ):
                if required not in fields:
                    failures.append(f"{table_name} is missing {required}")
    language_fields = csv_fieldnames(run_dir / "language_metrics.csv")
    if "source_files_oversized" not in language_fields:
        failures.append("language_metrics.csv is missing source_files_oversized")
    required_provenance = (
        "profiler_git_commit_sha",
        "profiler_git_dirty",
        "exclusion_policy_sha256",
        "package_distribution_version",
        "python_version_exact",
        "effective_git_checkout_configuration",
        "benchmark_environment",
    )
    if schema_at_least(manifest.get("artifact_schema_version"), (1, 12)):
        required_provenance += (
            "profiler_provenance_kind",
            "profiler_git_state",
            "profiler_source_sha256",
        )
    for field in required_provenance:
        if field not in manifest:
            failures.append(f"manifest is missing provenance field {field}")
    if manifest.get("package_distribution_version") != manifest.get("program_version"):
        failures.append("manifest package distribution version differs from program version")
    policy_sha = manifest.get("exclusion_policy_sha256")
    if not isinstance(policy_sha, str) or len(policy_sha) != 64 or any(
        character not in "0123456789abcdef" for character in policy_sha
    ):
        failures.append("manifest exclusion_policy_sha256 is not lowercase SHA-256")
    if not manifest.get("profiler_git_commit_sha"):
        current_provenance_schema = schema_at_least(
            manifest.get("artifact_schema_version"), (1, 12)
        )
        installed_identity = (
            manifest.get("profiler_provenance_kind") == "installed_distribution"
            and manifest.get("profiler_git_state") == "not_applicable"
            and isinstance(manifest.get("profiler_source_sha256"), str)
            and len(manifest["profiler_source_sha256"]) == 64
        )
        explanation_terms = ("evaluator",) if current_provenance_schema else (
            "evaluator",
            "profiler",
        )
        if not installed_identity and not any(
            any(term in warning.casefold() for term in explanation_terms)
            for warning in manifest.get("provenance_warnings", [])
        ):
            failures.append("missing profiler commit SHA has no provenance explanation")
    environment_path = run_dir / "environment.json"
    if not environment_path.is_file():
        failures.append("environment.json is missing")
    elif read_json(environment_path) != manifest.get("benchmark_environment"):
        failures.append("environment.json differs from manifest benchmark_environment")
    summary_path = run_dir / "summary.md"
    if not summary_path.is_file():
        failures.append("summary.md is missing")
    elif str(manifest.get("run_id")) not in summary_path.read_text(encoding="utf-8"):
        failures.append("summary.md does not contain the run ID")
    # Ordering and uniqueness are properties of the subject, which is what the
    # producer orders by. Checking the locator instead used to coincide for
    # remote cohorts and crashed outright on a local subject, whose
    # `repository_url` is legitimately null under Artifact 1.7.
    keys = [_subject_key(item) for item in analysis]
    if keys != sorted(keys, key=str.casefold):
        failures.append("analysis.json repository ordering is not deterministic")
    if len(set(keys)) != len(keys):
        failures.append("analysis.json contains duplicate subjects")

    durations: list[dict[str, Any]] = []
    events = [json.loads(line) for line in (run_dir / "logs" / "run.jsonl").read_text(encoding="utf-8").splitlines() if line]
    for result in analysis:
        # `key` joins; `url` only labels a failure message. Under Artifact 1.7 a
        # local subject has no locator, so the label falls back to the identity
        # rather than rendering "None" into every diagnostic.
        subject = _subject_key(result)
        locator = str(result.get("repository_url") or "")
        url = locator or subject
        aggregate = result["metrics"]["aggregate"]
        by_language = result["metrics"]["by_language"]
        acquisition = result.get("acquisition", {})
        if diagnostic_schema:
            recomputed = independently_derive_repository_diagnostics(result)
            for field, expected_value in recomputed.items():
                if result.get(field) != expected_value:
                    failures.append(
                        f"{url}: {field}={result.get(field)!r}, recomputed {expected_value!r}"
                    )
        git_mode_status = result.get("git_mode_map_status")
        if git_mode_status in {"failed", "timed_out"} and aggregate.get(
            "inventory_status"
        ) not in {"partial", "failed"}:
            failures.append(
                f"{url}: Git mode-map {git_mode_status} did not downgrade inventory"
            )
        # Whether a commit is even applicable is a property of the source mode,
        # which the producer states as `commit_verification_status`:
        # `verified` for an exact revision, `not_applicable` for a worktree or
        # plain-directory snapshot, `failed` for acquisition that never
        # produced one. Demanding a verified 40-hex SHA of every subject
        # assumed the remote-revision mode was the only one.
        sha = acquisition.get("analyzed_commit_sha")
        verification = acquisition.get("commit_verification_status")
        if sha and (len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)):
            failures.append(f"{url}: analyzed SHA is not full lowercase hexadecimal")
        if verification == "verified" and not sha:
            failures.append(f"{url}: commit is recorded as verified but no SHA is present")
        if sha and verification not in {"verified", "not_applicable"}:
            failures.append(f"{url}: commit was not recorded as verified")
        for field in CORE_FIELDS:
            values = [by_language[language][field] for language in LANGUAGES]
            if aggregate[field] is None or any(value is None for value in values):
                continue
            if aggregate[field] != sum(values):
                failures.append(f"{url}: aggregate {field} differs from per-language sum")

        inventory_path = run_dir / "file_inventory" / f"{inventory_slug(result)}.json"
        inventory = read_json(inventory_path) if inventory_path.exists() else None
        if aggregate["source_files"] is not None:
            if inventory is None:
                failures.append(f"{url}: successful metrics have no inventory artifact")
            else:
                included = sum(bool(record["included_in_metrics"]) for record in inventory["files"])
                if included != aggregate["source_files"]:
                    failures.append(f"{url}: inventory includes {included}, metrics report {aggregate['source_files']}")
                if inventory["summary"]["filesystem_walk_count"] != 1:
                    failures.append(f"{url}: inventory filesystem walk count is not one")
                accounted = (
                    aggregate.get("source_files_readable", 0)
                    + aggregate.get("source_files_failed_read", 0)
                    + aggregate.get("source_files_oversized", 0)
                )
                if accounted != aggregate["source_files"]:
                    failures.append(
                        f"{url}: included source accounting {accounted} differs from "
                        f"source_files {aggregate['source_files']}"
                    )

        for table_name, table in (("sheet_metrics.csv", sheet), ("catalog.csv", catalog)):
            row = table.get(subject)
            if row is None:
                failures.append(f"{url}: missing from {table_name}")
                continue
            expected = {
                "architecture_type": result["architecture_type"],
                "expected_language": result.get("expected_language") or "",
                "primary_language": result["metrics"].get("primary_language_name") or "",
                "analyzed_commit_sha": sha or "",
                "execution_mode": result.get("execution_mode") or "",
                "metric_contract_version": result.get("metric_contract_version") or "",
                "exclusion_policy_version": result.get("exclusion_policy_version") or "",
                "inventory_schema_version": result.get("inventory_schema_version") or "",
                "artifact_schema_version": result.get("artifact_schema_version") or "",
                "program_version": result.get("program_version") or "",
                "metric_status": aggregate["metric_status"],
                **{field: csv_value(aggregate[field]) for field in CORE_FIELDS},
            }
            if diagnostic_schema:
                expected.update(
                    {
                        "expected_language_family_status": result.get(
                            "expected_language_family_status"
                        ),
                        "partial_origin": result.get("partial_origin"),
                    }
                )
            for key, value in expected.items():
                if row.get(key, "") != str(value):
                    failures.append(f"{url}: {table_name} {key}={row.get(key)!r}, expected {value!r}")

        for language in LANGUAGES:
            row = language_rows.get((subject, language))
            metric = by_language[language]
            if row is None:
                failures.append(f"{url}: missing {language} row from language_metrics.csv")
                continue
            expected_provenance = {
                "analyzed_commit_sha": sha or "",
                "execution_mode": result.get("execution_mode") or "",
                "metric_contract_version": result.get("metric_contract_version") or "",
                "exclusion_policy_version": result.get("exclusion_policy_version") or "",
                "inventory_schema_version": result.get("inventory_schema_version") or "",
                "artifact_schema_version": result.get("artifact_schema_version") or "",
                "program_version": result.get("program_version") or "",
            }
            for field, value in expected_provenance.items():
                if row.get(field, "") != value:
                    failures.append(
                        f"{url}: language_metrics.csv {language} {field} mismatch"
                    )
            for field in (*CORE_FIELDS, "metric_status"):
                if row[field] != csv_value(metric[field]):
                    failures.append(f"{url}: language_metrics.csv {language} {field} mismatch")
            accounted = sum(
                int(row.get(field) or 0)
                for field in (
                    "source_files_readable",
                    "source_files_failed_read",
                    "source_files_oversized",
                )
            )
            if accounted != int(row.get("source_files") or 0):
                failures.append(
                    f"{url}: language_metrics.csv {language} source accounting mismatch"
                )

        repository_json = read_json(run_dir / "repositories" / f"{inventory_slug(result)}.json")
        if repository_json != result:
            failures.append(f"{url}: repository JSON differs from analysis.json")
        fact = (run_dir / "fact_sheets" / f"{inventory_slug(result)}.md").read_text(encoding="utf-8")
        displayed = {
            "Lines of Code": aggregate["lines_of_code"],
            "Source Files": aggregate["source_files"],
            "Classes / Structs": aggregate["classes_structs"],
            "Methods / Functions": aggregate["methods_functions"],
        }
        for label, value in displayed.items():
            rendered = "N/A" if value is None else str(value)
            if f"| {label} | {rendered} |" not in fact:
                failures.append(f"{url}: fact sheet {label} mismatch")

        # Structured log events carry the locator, not the subject key, so a
        # local subject correlates on an empty locator. That is unambiguous for
        # a single-subject run and ambiguous for several, and the ambiguity is
        # reported rather than resolved by guessing.
        if not locator and len(analysis) > 1:
            failures.append(
                f"{url}: structured log records no locator and the run has "
                f"{len(analysis)} subjects, so repository duration cannot be "
                f"attributed unambiguously"
            )
        def _same_subject(event: dict[str, Any]) -> bool:
            return str(event.get("repository_url") or "") == locator

        starts = [event for event in events if _same_subject(event) and event.get("module") == "acquisition" and event.get("message") == "Repository acquisition started"]
        ends = [
            event
            for event in events
            if _same_subject(event)
            and (
                (
                    event.get("module") == "analysis"
                    and str(event.get("message", "")).startswith("Repository analysis")
                )
                or (
                    event.get("module") == "acquisition"
                    and str(event.get("message", "")).startswith("failed (")
                )
            )
        ]
        if starts and ends:
            started = parse_time(starts[0]["timestamp"])
            ended = parse_time(ends[-1]["timestamp"])
            durations.append({"subject_key": subject, "repository_url": locator or None, "start_timestamp": starts[0]["timestamp"], "end_timestamp": ends[-1]["timestamp"], "duration_seconds": round((ended - started).total_seconds(), 6)})
        else:
            failures.append(f"{url}: structured log cannot establish repository duration")

    error_fields = csv_fieldnames(run_dir / "errors.csv")
    flattened_errors = [
        {key: csv_value(error.get(key)) for key in error_fields}
        for result in analysis for error in result.get("errors", [])
    ]
    if error_rows != flattened_errors:
        failures.append("errors.csv differs from flattened repository errors")
    recovery_fields = csv_fieldnames(run_dir / "recoveries.csv")
    flattened_recoveries = [
        {key: csv_value(diagnostic.get(key)) for key in recovery_fields}
        for result in analysis
        for diagnostic in result.get("metrics", {}).get(
            "recovered_parser_diagnostics", []
        )
    ]
    if recovery_rows != flattened_recoveries:
        failures.append("recoveries.csv differs from flattened recovery diagnostics")
    for row in error_rows:
        if row.get("fallback_attempted") == "True" and row.get("fallback_strategies") in {"", "[]"}:
            failures.append(
                f"{row.get('repository_url')}:{row.get('file_path')}: fallback attempted without a strategy"
            )
    expected_counts = {
        "repository_count": len(analysis),
        "processed_repository_count": len(analysis),
        "success_count": sum(item.get("analysis_status") == "complete" for item in analysis),
        "partial_count": sum(item.get("analysis_status") == "partial" for item in analysis),
        "failure_count": sum(item.get("analysis_status") == "failed" for item in analysis),
    }
    for key, expected in expected_counts.items():
        if manifest.get(key) != expected:
            failures.append(f"manifest {key}={manifest.get(key)!r}, expected {expected}")
        if key in run_status and run_status.get(key) != expected:
            failures.append(f"run status {key}={run_status.get(key)!r}, expected {expected}")
    for key in (
        "input_row_count",
        "planned_repository_count",
        "skipped_disabled_count",
        "duplicate_rows_dropped",
    ):
        if run_status.get(key) != manifest.get(key):
            failures.append(f"run status and manifest {key} differ")
    if run_status.get("status") != manifest.get("run_status"):
        failures.append("run_status.json and run_manifest.json final status differ")

    failures.extend(validate_qualification(run_dir, analysis, manifest, sheet))

    payload = semantic_payload(run_dir)
    semantic_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "run_directory": str(run_dir.resolve()),
        "run_id": manifest.get("run_id"),
        "run_status": run_status.get("status"),
        "passed": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "repository_durations": durations,
        "semantic_sha256": semantic_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_run(args.run_dir.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
