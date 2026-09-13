"""``metrolith diff`` — Direct Revision Diff.

Orchestration only. This module resolves two source states into two Artifact 1.7
runs through the **canonical** pipeline, applies a two-side capability barrier,
and hands both to :mod:`modules.revision_diff` for comparison and explanation.
It contains no metric logic and no second source-selection implementation.

CLI shape, chosen after reading the existing ``run``, ``analyze``, ``compare``
and ``explain`` surfaces:

* ``analyze`` already owns the vocabulary for "one local source state"
  (``--revision``, ``--tracked-only``, ``--subject-key``), so ``diff`` reuses
  those words rather than inventing synonyms;
* ``compare`` already owns run-to-run comparison and keeps it;
* the two sides are expressed with one uniform *side specifier* grammar instead
  of a flag per supported combination, which would have made the supported
  matrix implicit and grown with every new source mode.

    metrolith diff <source> --from <SIDE> --to <SIDE>

where each SIDE is ``worktree``, a Git revision, ``run:<directory>``, or
``remote:<url>#<revision>``. See :func:`modules.revision_diff.parse_side`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from modules import revision_diff
from modules.revision_diff import (
    EXIT_DIFFERENCES,
    EXIT_INVALID_ARTIFACTS,
    EXIT_NO_DIFFERENCE,
    EXIT_REFUSED,
    EXIT_USAGE,
    ResolvedSide,
    SideSpec,
)

EXIT_PREFLIGHT_REFUSED = 4


def add_parser(sub) -> None:
    parser = sub.add_parser(
        "diff",
        help="Diff two revisions or snapshots of one logical subject",
        description=(
            "Compare two source states of the same subject. Each side is one of: "
            "'worktree', a Git revision, 'run:<directory>', or "
            "'remote:<url>#<revision>'. Exit 0 = no differences, 1 = differences, "
            "2 = invalid usage, 3 = invalid/unresolvable artifacts, and 4 = "
            "comparison or preflight refused."
        ),
    )
    parser.add_argument(
        "source", nargs="?",
        help=(
            "Local path to the Git repository or directory the revision sides "
            "refer to. Omit only when both sides are run: or remote:."
        ),
    )
    parser.add_argument(
        "--from", dest="from_side", required=True, metavar="SIDE",
        help="Baseline side. 'worktree', a Git revision, run:<dir>, or remote:<url>#<rev>",
    )
    parser.add_argument(
        "--to", dest="to_side", required=True, metavar="SIDE",
        help="Comparison side, same grammar as --from",
    )
    parser.add_argument(
        "--subject-key",
        help=(
            "Declare that both sides are the same logical subject. Required "
            "when they resolve to different subject keys, for example a remote "
            "upstream revision against a local clone."
        ),
    )
    parser.add_argument(
        "--tracked-only", action="store_true",
        help=(
            "For worktree sides, snapshot only Git-tracked files. By default a "
            "worktree snapshot also includes untracked files Git is not ignoring."
        ),
    )
    parser.add_argument(
        "--expected-language",
        help="Optional primary-language expectation, passed to both analyses",
    )
    parser.add_argument(
        "--architecture-type",
        choices=["monolith", "microservices", "unknown"], default="unknown",
        help="Research metadata label (default: unknown)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--output", type=Path,
        help="Write the structured diff document to this path as JSON",
    )
    return parser


# --------------------------------------------------------------------------
# Side resolution
# --------------------------------------------------------------------------

def _natural_subject_key(spec: SideSpec, *, expected_language, architecture_type) -> str:
    """The identity a side resolves to on its own evidence, before any override."""
    from modules.benchmark_runner import _identity

    repository_spec = revision_diff.side_to_repository_spec(
        spec, subject_key=None, expected_language=expected_language,
        architecture_type=architecture_type,
    )
    return _identity(repository_spec).subject_key


def _open_existing(spec: SideSpec, *, subject_key: str | None) -> ResolvedSide:
    """Resolve a ``run:<directory>`` side without re-measuring anything."""
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    view = open_run(spec.run_directory)
    if not view.compatibility.readable:
        raise revision_diff.DiffRefused([{
            "dimension": "artifact_readability",
            "from": spec.describe(), "to": None,
            "detail": (
                f"{spec.label} side {spec.run_directory}: "
                f"{view.compatibility.reason}"
            ),
        }])
    if view.structural_errors:
        raise revision_diff.DiffRefused([{
            "dimension": "artifact_structure",
            "from": spec.describe(), "to": None,
            "detail": (
                f"{spec.label} side {spec.run_directory}: "
                f"{view.structural_errors[0]}"
            ),
        }])

    repositories = list(view.repositories)
    if len(repositories) != 1 and subject_key is None:
        raise revision_diff.DiffRefused([{
            "dimension": "subject_selection",
            "from": spec.describe(), "to": None,
            "detail": (
                f"{spec.label} side holds {len(repositories)} repositories; "
                f"supply --subject-key to name which subject to diff"
            ),
        }])

    # Subject-keyed selection. `repositories_by_url` would silently drop a local
    # subject, and historical artifacts predate `subject_key` entirely, so the
    # canonical key is derived through the shared compatibility helper.
    by_subject = {subject_key_of(dict(item)): item for item in repositories}
    if subject_key is not None:
        chosen = by_subject.get(subject_key)
        if chosen is None:
            raise revision_diff.DiffRefused([{
                "dimension": "subject_selection",
                "from": spec.describe(), "to": None,
                "detail": (
                    f"{spec.label} side records no subject {subject_key!r}; "
                    f"it holds: {', '.join(sorted(by_subject)) or '<none>'}"
                ),
            }])
    else:
        chosen = repositories[0]

    natural = subject_key_of(dict(chosen))
    warnings = [str(item) for item in view.warnings]
    return ResolvedSide(
        spec=spec, run_directory=Path(view.run_directory), view=view,
        repository=chosen, subject_key=natural, natural_subject_key=natural,
        freshly_analyzed=False, warnings=warnings,
    )


def _analyze_side(spec: SideSpec, config, *, subject_key, expected_language,
                  architecture_type, natural_key) -> ResolvedSide:
    """Run one side through the canonical pipeline and open the result."""
    from modules.benchmark_runner import run_benchmark
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    repository_spec = revision_diff.side_to_repository_spec(
        spec, subject_key=subject_key, expected_language=expected_language,
        architecture_type=architecture_type,
    )
    acquisition_mode = "frozen" if spec.kind == "remote_revision" else "offline"
    summary = run_benchmark(
        repository_specs=[repository_spec],
        config=config,
        acquisition_mode=acquisition_mode,
        command_line_arguments=["diff", spec.label],
        single_repository=True,
    )
    run_directory = Path(summary["run_directory"])
    if summary["status"] == "failed":
        raise revision_diff.DiffRefused([{
            "dimension": "analysis_outcome",
            "from": spec.describe(), "to": None,
            "detail": (
                f"{spec.label} side did not finalize: run {run_directory} "
                f"published status 'failed'. A diff over an unpublished "
                f"terminal state would compare bytes nothing validated."
            ),
        }])

    view = open_run(run_directory)
    repositories = list(view.repositories)
    if not repositories:
        raise revision_diff.DiffRefused([{
            "dimension": "analysis_outcome",
            "from": spec.describe(), "to": None,
            "detail": f"{spec.label} side produced no repository result",
        }])
    chosen = repositories[0]
    return ResolvedSide(
        spec=spec, run_directory=run_directory, view=view, repository=chosen,
        subject_key=subject_key_of(dict(chosen)),
        natural_subject_key=natural_key,
        freshly_analyzed=True,
        warnings=[str(item) for item in view.warnings],
    )


def two_side_capability_barrier(sides, config) -> None:
    """Both planned analyses must be measurable before either one parses a file.

    Without this, side A is fully measured and side B then discovers a missing
    grammar, leaving a half-diff whose deltas are indistinguishable from real
    source change. It is the cohort barrier's guarantee applied across the two
    sides of a diff, using the same discovery pass so the requirement set is the
    one the measurement path would actually produce.

    Acquisition and snapshot discovery may occur here, exactly as for the cohort
    barrier: a Stage B refusal means acquisition may have happened but zero
    files were parsed and no run was published.
    """
    from modules import preflight
    from modules.benchmark_runner import discover_required_capabilities

    planned = [side for side in sides if side.kind != "existing_run"]
    if not planned:
        return

    probes = preflight.probe_parser_capabilities()
    if not preflight.barrier_can_refuse(probes):
        return

    labelled = []
    for spec in planned:
        repository_spec = revision_diff.side_to_repository_spec(
            spec, subject_key=None, expected_language=None,
            architecture_type="unknown",
        )
        labelled.append((f"{spec.label} ({spec.describe()})", repository_spec))

    required = discover_required_capabilities(
        labelled, config, "offline"
    )
    report = preflight.stage_b(required, probes=probes)
    if report.refused:
        raise preflight.PreflightRefused(report)


def resolve_sides(from_spec: SideSpec, to_spec: SideSpec, config, *,
                  subject_key, expected_language, architecture_type):
    """Resolve both sides to Artifact 1.7 runs, barrier first."""
    natural = {}
    for spec in (from_spec, to_spec):
        if spec.kind == "existing_run":
            continue
        natural[spec.label] = _natural_subject_key(
            spec, expected_language=expected_language,
            architecture_type=architecture_type,
        )

    two_side_capability_barrier([from_spec, to_spec], config)

    resolved = []
    for spec in (from_spec, to_spec):
        if spec.kind == "existing_run":
            resolved.append(_open_existing(spec, subject_key=subject_key))
        else:
            resolved.append(_analyze_side(
                spec, config, subject_key=subject_key,
                expected_language=expected_language,
                architecture_type=architecture_type,
                natural_key=natural.get(spec.label),
            ))
    return resolved[0], resolved[1]


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

def _canonical_language(value: str | None) -> str | None:
    """Match `analyze`'s normalization so both commands accept the same input."""
    from modules.config import SUPPORTED_LANGUAGES

    if not value:
        return None
    return next(
        (
            candidate for candidate in SUPPORTED_LANGUAGES
            if candidate.casefold() == value.casefold()
        ),
        None,
    )


def handle(args, config) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    # Establish both explicit input sides before resolve_sides can analyze.
    try:
        specs = [revision_diff.parse_side(value, label, source=args.source, tracked_only=args.tracked_only) for value, label in ((args.from_side, "from"), (args.to_side, "to"))]
        admitted = admit(args.output, overwrite=True, runs=[s.run_directory for s in specs if s.kind == "existing_run"], sources=[args.source] if args.source else [], kind="diff")
        return _handle(args, config, admitted)
    except (ExportOutputError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE


def _handle(args, config, admitted) -> int:
    from modules import preflight
    from validation.artifact_io.errors import ArtifactStructureError

    try:
        from_spec = revision_diff.parse_side(
            args.from_side, "from", source=args.source,
            tracked_only=args.tracked_only,
        )
        to_spec = revision_diff.parse_side(
            args.to_side, "to", source=args.source,
            tracked_only=args.tracked_only,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_USAGE

    try:
        left, right = resolve_sides(
            from_spec, to_spec, config,
            subject_key=args.subject_key,
            expected_language=_canonical_language(args.expected_language),
            architecture_type=args.architecture_type,
        )
    except preflight.PreflightRefused as refusal:
        print(f"[REFUSED] {refusal.report.summary()}")
        return EXIT_PREFLIGHT_REFUSED
    except revision_diff.DiffRefused as refusal:
        print(render_refusal(refusal.reasons))
        return EXIT_INVALID_ARTIFACTS
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_INVALID_ARTIFACTS

    admitted.protect_runs([left.run_directory, right.run_directory])
    payload = revision_diff.build_diff(
        left, right, subject_override=args.subject_key
    )

    if args.output:
        admitted.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_text(payload))
        # Text surface only: `revision_diff` output is
        # `additionalProperties: false` and a new format version is outside the
        # R0 schema inventory.
        print(revision_diff.render_qualification_dimension(left, right))

    if not payload["comparability"]["comparable"]:
        return EXIT_REFUSED
    return EXIT_DIFFERENCES if payload["has_differences"] else EXIT_NO_DIFFERENCE


def render_refusal(reasons) -> str:
    lines = ["Metrolith diff refused:"]
    lines.extend(f"  - [{item['dimension']}] {item['detail']}" for item in reasons)
    return "\n".join(lines)


def render_text(payload: dict[str, Any]) -> str:
    """Human-readable rendering. Markdown-compatible plain text, never HTML."""
    lines: list[str] = []
    subject = payload["subject"]
    sides = payload["sides"]

    lines.append(f"# Metrolith revision diff {payload['revision_diff_format_version']}")
    lines.append("")
    lines.append(f"subject (from): {subject['natural_subject_key_from']}")
    lines.append(f"subject (to):   {subject['natural_subject_key_to']}")
    if subject["subject_key_override"]:
        lines.append(
            f"subject key override: {subject['subject_key_override']} "
            f"(declared same subject)"
        )
    lines.append("")
    for label in ("from", "to"):
        side = sides[label]
        lines.append(f"{label}: {side['specifier']}")
        lines.append(f"    source mode:  {side['source_mode']}")
        lines.append(f"    revision:     {side['analyzed_commit_sha'] or 'n/a'}")
        lines.append(f"    scope hash:   {side['analysis_scope_hash']}")
        lines.append(f"    run:          {side['run_id']}")
    lines.append("")

    comparability = payload["comparability"]
    if not comparability["comparable"]:
        lines.append("## Comparison refused")
        for reason in comparability["refusal_reasons"]:
            lines.append(
                f"  - {reason['dimension']}: {reason['from']!r} -> {reason['to']!r}"
            )
            lines.append(f"    {reason['detail']}")
        return "\n".join(lines)

    lines.append("## Comparison completed")
    lines.append("")

    def section(title, items, render):
        lines.append(f"### {title}")
        if not items:
            lines.append("  (none)")
        else:
            for item in items:
                lines.extend(render(item))
        lines.append("")

    section(
        "Source identity changes", payload["source_identity_changes"],
        lambda item: (
            [f"  - {item['dimension']}: {item['value_from']!r} -> {item['value_to']!r}"]
            + ([f"    {item['note']}"] if item.get("note") else [])
        ),
    )
    section(
        "Measurement status changes", payload["measurement_status_changes"],
        lambda item: [
            f"  - [{item['scope']}] "
            f"{item.get('language', '') + ' ' if item.get('language') else ''}"
            f"{item['dimension']}: {item['value_from']!r} -> {item['value_to']!r}"
        ],
    )
    section(
        "Aggregate metric deltas", payload["aggregate_metric_deltas"],
        lambda item: [
            f"  - {item['dimension']}: {item['value_from']} -> {item['value_to']}"
            f" ({item['delta']:+d})" if item["delta"] is not None
            else f"  - {item['dimension']}: not evaluable "
                 f"({item['not_evaluable_reason']})"
        ],
    )
    section(
        "Per-language metric deltas", payload["per_language_metric_deltas"],
        lambda item: [
            f"  - {item['language']} {item['dimension']}: "
            f"{item['value_from']} -> {item['value_to']}"
            + (f" ({item['delta']:+d})" if item["delta"] is not None else " (not evaluable)")
        ],
    )

    # -- complexity, under its own verdict ----------------------------------
    verdict = payload["complexity_comparability"]
    lines.append("### Complexity (Complexity Contract "
                 f"{verdict.get('complexity_contract_version_from')} -> "
                 f"{verdict.get('complexity_contract_version_to')})")
    lines.append(
        f"  measurement state: {verdict.get('measurement_state_from')} -> "
        f"{verdict.get('measurement_state_to')}"
    )
    if not verdict["evaluable"]:
        lines.append(f"  not evaluable: {verdict['not_evaluable_reason']}")
        lines.append(
            "  this never blocks the diff; the metric comparisons above stand"
        )
    else:
        section(
            "Complexity aggregate deltas", payload["complexity_aggregate_deltas"],
            lambda item: [
                f"  - {item['dimension']}: {item['value_from']} -> "
                f"{item['value_to']}"
                + (f" ({item['delta']:+})" if item["delta"] is not None
                   else f" (not evaluable: {item['not_evaluable_reason']})")
            ],
        )
        section(
            "Per-language complexity deltas",
            payload["per_language_complexity_deltas"],
            lambda item: [
                f"  - {item['language']} {item['dimension']}: "
                f"{item['value_from']} -> {item['value_to']}"
                + (f" ({item['delta']:+})" if item["delta"] is not None
                   else " (not evaluable)")
            ],
        )
        file_complexity = payload["file_complexity_evidence"]
        if not file_complexity["evaluable"]:
            lines.append(
                f"  file-level complexity not evaluable: "
                f"{file_complexity['not_evaluable_reason']}"
            )
        else:
            section(
                "File complexity deltas", file_complexity["deltas"],
                lambda item: [
                    f"  - {item['relative_path']} {item['dimension']}: "
                    f"{item['value_from']} -> {item['value_to']}"
                    + (f" ({item['delta']:+})" if item["delta"] is not None
                       else " (not evaluable)")
                ],
            )
            section(
                "File complexity status changes",
                file_complexity["status_changes"],
                lambda item: [
                    f"  - {item['relative_path']} {item['dimension']}: "
                    f"{item['from']!r} -> {item['to']!r}"
                ],
            )
    lines.append(
        "  Complexity figures are not measurement-equivalent across languages; "
        "per-language deltas are reported separately and never summed."
    )

    evidence = payload["file_evidence"]
    lines.append("### File evidence")
    if not evidence["evaluable"]:
        lines.append(f"  not evaluable: {evidence['not_evaluable_reason']}")
    else:
        counts = evidence["counts"]
        lines.append(
            f"  added {counts['added']} | removed {counts['removed']} | "
            f"content changed {counts['content_changed']} | "
            f"unchanged {evidence['unchanged_count']}"
        )
        for item in evidence["added"]:
            lines.append(f"    + {item['relative_path']}")
        for item in evidence["removed"]:
            lines.append(f"    - {item['relative_path']}")
        for item in evidence["content_changed"]:
            lines.append(f"    ~ {item['relative_path']}")
        for item in evidence["renames"]:
            if item["evidence"] == revision_diff.RENAME_EXACT:
                lines.append(
                    f"    R {item['from_path']} -> {item['to_path']} "
                    f"[{item['evidence']}]"
                )
            else:
                lines.append(
                    f"    ? ambiguous content match on {item['content_hash'][:12]}: "
                    f"{item['candidate_from_paths']} -> {item['candidate_to_paths']}"
                )
    lines.append("")

    lines.append("### Metric attribution")
    if not payload["metric_attribution"]:
        lines.append("  (no aggregate delta to attribute)")
    for item in payload["metric_attribution"]:
        lines.append(f"  - {item['dimension']}: observed {item['observed_delta']:+d}")
        lines.append(f"    evidence level: {item['evidence_level']}")
        if item["not_evaluable_reason"]:
            lines.append(f"    {item['not_evaluable_reason']}")
        else:
            lines.append(
                f"    added files {item['from_added_files']:+d} | "
                f"removed files {item['from_removed_files']:+d} | "
                f"changed files {item['from_changed_files']:+d} | "
                f"residual {item['residual']:+d}"
            )
    lines.append("")

    projection = payload["semantic_projection"]
    lines.append("### Semantic projection")
    if not projection["available"]:
        lines.append(f"  unavailable: {projection['reason']}")
    else:
        lines.append(f"  equal: {projection['equal']}")
        lines.append(f"  from: {projection['from']}")
        lines.append(f"  to:   {projection['to']}")
    return "\n".join(lines)
