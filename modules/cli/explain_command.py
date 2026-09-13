"""``metrolith explain`` (plan section 12).

Renders the factual diagnostic projection into attention groups. The output is
**attention grouping, not severity and not research impact** — that distinction
is stated in both the text and the JSON, because a reader who mistakes group
order for importance would draw exactly the conclusion plan section 3.5 forbids.

Deterministic and offline: no acquisition, no network, no source parsing. The
JSON and text renderings contain identical facts.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
import sys
from typing import Any

from modules.diagnostics import ExplanationCompleteness, project_run

EXIT_OK = 0
EXIT_DIFFERENCES = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3

# 1.1.0: the emitted payload has carried seven properties that the 1.0 schema
# never declared — `lifecycle`, `run_evidence`, `evaluable_dimensions`,
# `provenance_warnings`, the two `repository_filter` fields, and now
# `run_integrity_status`. Under `additionalProperties: false` that made every
# real invocation violate its own published contract. The output is the intended
# interface, so the schema was brought up to it rather than the reverse.
# 1.2.0 adds the `complexity-unavailable` group and the three complexity fields
# on every entry. The document is `additionalProperties: false`, so a new field
# is a new schema version; 1.1 is retained byte-for-byte as historical material.
# 1.3.0 adds `cognitive_state` and `cognitive_metric_name` on every entry, and
# the `cognitive_complexity` evaluable dimension. `additionalProperties: false`
# again makes a new field a new version; 1.2 is retained byte-for-byte.
EXPLANATION_FORMAT_VERSION = "1.3.0"

GROUP_INVALID_ARTIFACTS = "invalid-artifacts"
GROUP_UNAVAILABLE = "unavailable-measurements"
GROUP_COMPLEXITY_UNAVAILABLE = "complexity-unavailable"
GROUP_FAMILY_FAILED = "expected-family-failed"
GROUP_FAMILY_PARTIAL = "expected-family-partial"
GROUP_MULTIPLE = "multiple"
GROUP_SECONDARY_ONLY = "secondary-language-only"

# Order is the plan's presentation order. It is NOT a severity ranking.
GROUP_ORDER = (
    GROUP_INVALID_ARTIFACTS,
    GROUP_UNAVAILABLE,
    GROUP_COMPLEXITY_UNAVAILABLE,
    GROUP_FAMILY_FAILED,
    GROUP_FAMILY_PARTIAL,
    GROUP_MULTIPLE,
    GROUP_SECONDARY_ONLY,
)
ONLY_CHOICES = GROUP_ORDER + ("all",)

# Lifecycles in which the run's artifacts cannot be trusted, and the command
# must say so through its exit code. `finalized_invalid` belongs here:
# the run reached a terminal status but its artifacts do not parse, which is
# precisely the state `reproduce` and `compare --explain` already exit 3 on.
# Omitting it let `explain` exit 0 on a run three sibling commands rejected, so
# a CI job gating on this command passed while the run was unreadable.
INVALID_ARTIFACT_LIFECYCLES = frozenset({
    "corrupt", "unsupported_version", "undeclared_version", "finalized_invalid",
})

GROUPING_SEMANTICS = (
    "Groups are an attention ordering for a human reviewer. They are not a "
    "severity ranking, not a research-impact ranking, and not a judgement about "
    "architecture quality."
)

CROSS_LANGUAGE_CAVEAT = (
    "This output groups repositories across different language families. The "
    "four metrics share names and aggregation rules, but their raw entity counts "
    "are not perfectly measurement-equivalent across languages. Raw counts alone "
    "must not be interpreted as cross-language architecture-quality measures."
)


def add_parser(subparsers) -> None:
    explain = subparsers.add_parser(
        "explain", help="Explain recorded diagnostics for one Metrolith run"
    )
    explain.add_argument("run_directory", type=Path)
    explain.add_argument(
        "--repo", dest="repository",
        help="Restrict output to one repository, matched on canonical URL",
    )
    explain.add_argument("--format", choices=("text", "json"), default="text")
    explain.add_argument(
        "--only", choices=ONLY_CHOICES, default="all",
        help="Restrict output to one attention group (default: all)",
    )


def handle(args) -> int:
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    if not Path(args.run_directory).is_dir():
        print(f"[ERROR] Run directory does not exist or is not a directory: {args.run_directory}. Check the path, then rerun.", file=sys.stderr)
        return EXIT_INVALID_ARTIFACTS

    try:
        view = open_run(args.run_directory)
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_INVALID_ARTIFACTS

    projection = project_run(view)
    payload = build_explanation(
        projection,
        repository=getattr(args, "repository", None),
        only=getattr(args, "only", "all"),
    )

    if payload.get("repository_filter_unmatched"):
        print(
            f"[ERROR] no repository matches canonical URL "
            f"{payload['repository_filter']!r} in this run",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        from modules.presentation import source_failure
        for result in view.repositories:
            cause = source_failure(result)
            if cause:
                print(sanitize_terminal(cause))
        print(render_text(payload))
        # Rendered on the TEXT surface only, and read straight from the
        # qualification authority rather than folded into `payload`.
        # `explain_output` is `additionalProperties: false`, so adding these
        # fields to the JSON document would require a new format version --
        # which the R0 plan's schema inventory does not authorize. Displaying
        # them here satisfies the requirement to show all three dimensions
        # without silently changing a versioned output contract.
        rendered = render_qualification(view, repository=getattr(args, "repository", None))
        if rendered:
            print(rendered)

    if projection.lifecycle in INVALID_ARTIFACT_LIFECYCLES:
        return EXIT_INVALID_ARTIFACTS
    return EXIT_OK


def _assign_group(diagnostic) -> str | None:
    """Assign one repository to exactly one attention group.

    Every branch reads a *recorded* status. Nothing here recomputes a status,
    so a repository lands in the group its own artifacts describe.
    """
    if diagnostic.explanation_completeness is ExplanationCompleteness.INSUFFICIENT_EVIDENCE:
        return GROUP_INVALID_ARTIFACTS

    statuses = set(diagnostic.metric_statuses.values())
    if diagnostic.analysis_status == "failed" or "failed" in statuses:
        if diagnostic.expected_language_family_status == "failed":
            return GROUP_FAMILY_FAILED
        return GROUP_UNAVAILABLE

    if diagnostic.expected_language_family_status == "failed":
        return GROUP_FAMILY_FAILED
    if diagnostic.expected_language_family_status == "partial":
        return GROUP_FAMILY_PARTIAL

    # Complexity failed while the core metrics did not: a real and otherwise
    # invisible state. `absent` is deliberately NOT here — a run that predates
    # Complexity Contract 1.0.0 has nothing to report, and grouping every
    # historical run under an attention heading would be noise, not attention.
    if diagnostic.complexity_state == "failed":
        return GROUP_COMPLEXITY_UNAVAILABLE
    # Cognitive shares the structural traversal, so a cognitive-only failure is
    # rare -- but it is representable, and `absent` is excluded here for the
    # same reason as above: every pre-1.10 run is absent, and grouping them all
    # under an attention heading is noise rather than attention.
    if diagnostic.cognitive_state == "failed":
        return GROUP_COMPLEXITY_UNAVAILABLE

    origin = diagnostic.partial_origin
    if origin == "secondary_supported_language_only":
        return GROUP_SECONDARY_ONLY
    if origin == "multiple":
        return GROUP_MULTIPLE
    if diagnostic.analysis_status == "partial":
        return GROUP_MULTIPLE
    return None


def build_explanation(
    projection, *, repository: str | None = None, only: str = "all"
) -> dict[str, Any]:
    """Build the explain payload. Deterministic for a given projection."""
    selected = projection.repositories
    unmatched = False
    if repository:
        selected = tuple(
            item for item in selected if item.repository_url == repository
        )
        unmatched = not selected

    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in GROUP_ORDER}
    for diagnostic in selected:
        group = _assign_group(diagnostic)
        if group is None:
            continue
        grouped[group].append(_entry(diagnostic))

    for entries in grouped.values():
        entries.sort(key=lambda item: item["repository_url"])

    wanted = GROUP_ORDER if only == "all" else (only,)
    groups = [
        {"group": name, "entries": grouped[name]}
        for name in GROUP_ORDER
        if name in wanted
    ]

    languages = {
        item.expected_language for item in selected if item.expected_language
    }
    caveat = CROSS_LANGUAGE_CAVEAT if len(languages) > 1 else None

    return {
        "explanation_format_version": EXPLANATION_FORMAT_VERSION,
        "run_id": projection.run_id,
        "grouping_semantics": GROUPING_SEMANTICS,
        "cross_language_comparability_caveat": caveat,
        "groups": groups,
        "repository_filter": repository,
        "repository_filter_unmatched": unmatched,
        "lifecycle": projection.lifecycle,
        # Reported beside lifecycle, never folded into it. `finalized_valid`
        # means the artifacts decode; it is not a claim that the run succeeded,
        # and the two come apart when a terminal state fails self-validation.
        "run_integrity_status": projection.run_integrity_status,
        "provenance_warnings": list(projection.provenance_warnings),
        "evaluable_dimensions": dict(projection.evaluable_dimensions),
        # Run-scoped evidence is half of the shared diagnostic model and was
        # previously discarded here, so `explain` could report a lifecycle of
        # `finalized_invalid` while showing nothing at all about why — and exit
        # 0 while `reproduce` and `compare` exited 3 on the same directory.
        "run_evidence": [item.as_dict() for item in projection.run_evidence],
    }


def _entry(diagnostic) -> dict[str, Any]:
    """One repository entry. Evidence is copied, never summarized into a cause."""
    evidence = [
        {
            "evidence_scope": item.scope.value,
            "category": item.category,
            # Existing error previews are deliberately not re-rendered here
            # (plan section 12.3); only the recorded category and location.
            "relative_path": item.relative_path,
            "metric_effect": item.metric_effect.value,
            "module": item.module,
        }
        for item in diagnostic.evidence
    ]
    evidence.sort(key=lambda item: (
        item["evidence_scope"], item["category"], item["relative_path"] or ""
    ))
    return {
        "repository_url": diagnostic.repository_url,
        "recorded_status": diagnostic.analysis_status,
        "metric_statuses": dict(diagnostic.metric_statuses),
        "expected_language": diagnostic.expected_language,
        "expected_language_family_status": diagnostic.expected_language_family_status,
        "partial_origin": diagnostic.partial_origin,
        "git_mode_map_state": diagnostic.git_mode_map_state,
        "parser_compatibility_strategies": list(diagnostic.parser_compatibility_strategies),
        "unknown_categories": list(diagnostic.unknown_categories),
        "explanation_completeness": diagnostic.explanation_completeness.value,
        "evaluable_dimensions": dict(diagnostic.evaluable_dimensions),
        # Provenance travels with the state: a reader must be able to tell
        # `absent` (no complexity in this artifact generation) from `failed`
        # (attempted, unavailable) without consulting another document.
        "complexity_state": diagnostic.complexity_state,
        "complexity_contract_version": diagnostic.complexity_contract_version,
        "complexity_unavailable_reason": diagnostic.complexity_unavailable_reason,
        "evidence_count": len(evidence),
        "evidence": evidence,
    }


# Control and bidirectional characters are rendered visibly rather than emitted
# raw, so a hostile repository name cannot reorder or overwrite terminal output
# (plan section 22).
def sanitize_terminal(value: str) -> str:
    out = []
    for character in value:
        category = unicodedata.category(character)
        if character in "\t\n":
            out.append(character)
        elif category in {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}:
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    return "".join(out)


def render_qualification(view, *, repository: str | None = None) -> str:
    """Show all three dimensions, their reason codes, and their provenance.

    Deliberately verbose about WHICH authority each line came from. The whole
    failure R0 exists to close is a reader taking `metric_status=complete` as
    permission to compare repositories, so a display that lists completeness
    beside representativeness without naming them separately would reproduce it.
    """
    from modules.benchmark_qualification import subject_of

    mode = view.qualification_mode
    lines: list[str] = ["", f"Qualification mode: {mode}"]
    if mode != "benchmark_qualified":
        lines.append(
            "  This run requested no benchmark qualification. It carries no "
            "representativeness verdict and no admission decision; absence "
            "never means ADEQUATE."
        )
        return "\n".join(lines)

    artifact = view.benchmark_qualification
    if artifact is None:
        lines.append("  ERROR: benchmark-qualified mode with no qualification artifact")
        return "\n".join(lines)

    readiness = view.benchmark_of_record_readiness or {}
    lines.append(f"Qualification profile: {artifact.get('qualification_profile')}")
    provenance = artifact.get("registry_provenance") or {}
    lines.append(f"Registry source: {provenance.get('source_id')}")
    lines.append(f"Registry SHA-256: {provenance.get('registry_sha256')}")
    lines.append(
        f"Benchmark-of-record readiness: {readiness.get('status') or 'NOT_READY'}"
    )
    for blocker in readiness.get("blockers") or ():
        lines.append(f"  - blocker: {sanitize_terminal(str(blocker))}")

    lines.append("")
    lines.append("Per-repository qualification:")
    for record in artifact.get("records") or ():
        subject = subject_of(record.get("binding") or {})
        if repository and repository not in {subject, str(record.get("binding", {}).get("subject_key"))}:
            continue
        lines.append(f"  {sanitize_terminal(subject)}")
        lines.append(
            f"    representativeness: {record.get('repository_representativeness')} "
            f"[{', '.join(record.get('representativeness_reason_codes') or ()) or 'none'}]"
        )
        lines.append(
            f"    benchmark usability: {record.get('benchmark_usability')} "
            f"[{', '.join(record.get('usability_reason_codes') or ()) or 'none'}]"
        )
        lines.append(
            f"    usable metric families: "
            f"{', '.join(record.get('usable_metric_families') or ()) or 'none'}"
        )
        lines.append(
            f"    repository-level comparison eligible: "
            f"{record.get('repository_level_comparison_eligible')}"
        )
        lines.append(
            f"    manual admission restriction: "
            f"{record.get('manual_admission_restriction')}"
        )
        adjudication = record.get("adjudication") or {}
        lines.append(
            f"    adjudication: {adjudication.get('mode') or 'none'} by "
            f"{adjudication.get('adjudicator_id') or 'n/a'} at "
            f"{adjudication.get('adjudicated_at') or 'n/a'}"
        )
        evidence = record.get("evidence_summary") or {}
        lines.append(
            f"    evidence bundle: {evidence.get('evidence_bundle_sha256') or 'n/a'}"
        )
        basis = record.get("representativeness_basis")
        if basis:
            # Printed last and labelled, so nobody mistakes prose for a key.
            lines.append(
                f"    basis (non-semantic): {sanitize_terminal(str(basis))}"
            )
    return "\n".join(lines)


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Metrolith explain {payload['explanation_format_version']}")
    lines.append(f"Run: {sanitize_terminal(str(payload['run_id']))}")
    lines.append(f"Lifecycle: {payload['lifecycle']}")
    integrity = payload.get("run_integrity_status")
    lines.append(f"Run integrity status: {integrity if integrity else 'not recorded'}")
    if payload["lifecycle"] == "finalized_valid" and integrity not in (
        "completed", "completed_with_errors"
    ):
        # Stating it once, plainly, beats leaving a reader to infer that a
        # `finalized_valid` run may still have failed.
        lines.append(
            "  note: the artifacts decode, but the run did not record success; "
            "run integrity status is authoritative for whether it succeeded"
        )
    lines.append("")
    lines.append(GROUPING_SEMANTICS)

    if payload.get("cross_language_comparability_caveat"):
        lines.append("")
        lines.append(payload["cross_language_comparability_caveat"])

    if payload.get("provenance_warnings"):
        lines.append("")
        lines.append("Provenance warnings:")
        for warning in payload["provenance_warnings"]:
            lines.append(f"  - {sanitize_terminal(warning)}")

    run_evidence = payload.get("run_evidence") or []
    if run_evidence:
        lines.append("")
        lines.append(f"Run-scoped recorded evidence ({len(run_evidence)}):")
        for item in run_evidence:
            lines.append(
                f"  - [{item.get('category')}] {item.get('source_artifact')}: "
                f"{sanitize_terminal(str(item.get('detail', '')))} "
                f"[metric effect: {item.get('metric_effect')}]"
            )

    not_evaluable = sorted(
        name for name, value in payload.get("evaluable_dimensions", {}).items()
        if not value
    )
    if not_evaluable:
        lines.append("")
        lines.append("Not evaluable because evidence is missing:")
        for name in not_evaluable:
            lines.append(f"  - {name}")

    total = 0
    for group in payload["groups"]:
        entries = group["entries"]
        total += len(entries)
        lines.append("")
        lines.append(f"[{group['group']}] {len(entries)} repository/repositories")
        for entry in entries:
            lines.append(f"  {sanitize_terminal(entry['repository_url'])}")
            lines.append(f"    recorded status: {entry['recorded_status']}")
            if entry["expected_language_family_status"] is not None:
                lines.append(
                    "    expected-family status: "
                    f"{entry['expected_language_family_status']}"
                )
            if entry["partial_origin"] is not None:
                lines.append(f"    partial origin: {entry['partial_origin']}")
            if entry["explanation_completeness"] != "complete":
                lines.append(
                    "    explanation completeness: "
                    f"{entry['explanation_completeness']}"
                )
            state = entry.get("complexity_state")
            if state and state != "complete":
                detail = entry.get("complexity_unavailable_reason")
                lines.append(
                    f"    complexity state: {state}"
                    + (f" ({sanitize_terminal(str(detail))})" if detail else "")
                )
            for item in entry["evidence"]:
                location = item["relative_path"] or f"({item['evidence_scope']} scope)"
                lines.append(
                    f"    - {item['category']} at {sanitize_terminal(location)} "
                    f"[metric effect: {item['metric_effect']}]"
                )
            for unknown in entry["unknown_categories"]:
                lines.append(f"    - unknown category: {sanitize_terminal(unknown)}")

    lines.append("")
    lines.append(f"{total} repository/repositories in the selected group(s).")
    return "\n".join(lines)
