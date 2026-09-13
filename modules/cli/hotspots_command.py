"""``metrolith hotspots`` — derived Git-aware file maintenance attention."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from modules.hotspots import (
    HistorySource,
    analyze_hotspots,
    attention_class_counts,
    canonical_json,
)
from modules.presentation import portable_path, scalar, subject_display
from modules.subject import subject_key_of


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "hotspots",
        help="Prioritize complex, frequently changed files from an existing run",
        description=(
            "Derive maintenance-attention classes from admitted run ledgers and "
            "committed Git history. Exit 0 = validated output, 1 = operational "
            "failure, 2 = invalid mapping/overwrite refusal, and 3 = invalid, "
            "unfinished, unreadable, or unsupported run artifacts."
        ),
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--repository",
        action="append",
        default=[],
        metavar="[SUBJECT_KEY=]PATH",
        help=(
            "Read Git history from this local repository. A bare PATH is allowed "
            "for a one-repository run; multi-repository runs require repeatable "
            "SUBJECT_KEY=PATH mappings. Without an override, remote subjects use "
            "the cache root recorded by the run when available."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format (default: json for compatibility)",
    )
    parser.add_argument("--output", type=Path, help="Write the selected format here")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output file",
    )
    parser.add_argument(
        "--timings",
        action="store_true",
        help=(
            "Write observational phase timings to stderr. Timings are excluded "
            "from the deterministic JSON document."
        ),
    )


def _parse_overrides(
    values: Sequence[str], subjects: Sequence[str]
) -> dict[str, HistorySource]:
    found: dict[str, HistorySource] = {}
    known = set(subjects)
    for value in values:
        if "=" in value:
            subject, raw_path = value.split("=", 1)
            subject = subject.strip()
            raw_path = raw_path.strip()
        elif len(subjects) == 1:
            subject = subjects[0]
            raw_path = value.strip()
        else:
            raise ValueError(
                "--repository requires SUBJECT_KEY=PATH for a multi-repository run"
            )
        if subject not in known:
            raise ValueError(f"--repository names unknown subject key {subject!r}")
        if subject in found:
            raise ValueError(f"duplicate --repository mapping for {subject!r}")
        path = Path(raw_path).expanduser().resolve(strict=False)
        if not path.is_dir():
            raise ValueError(f"--repository is not a directory: {raw_path}")
        found[subject] = HistorySource(path=path, kind="explicit_repository")
    return found


def _automatic_sources(
    view: Any, explicit: Mapping[str, HistorySource]
) -> tuple[dict[str, HistorySource], dict[str, str]]:
    from modules.acquisition import cache_path_for_url

    sources = dict(explicit)
    missing: dict[str, str] = {}
    raw_cache = (view.manifest.get("resolved_paths") or {}).get("cache_root")
    cache_root = Path(raw_cache).expanduser().resolve(strict=False) if raw_cache else None
    for repository in view.repositories:
        item = dict(repository)
        subject = subject_key_of(item)
        if subject in sources:
            continue
        url = item.get("repository_url")
        if not url:
            missing[subject] = "local_repository_override_required"
            continue
        if cache_root is None:
            missing[subject] = "recorded_cache_root_unavailable"
            continue
        try:
            candidate = cache_path_for_url(cache_root, str(url))
        except ValueError:
            missing[subject] = "repository_locator_invalid"
            continue
        if not candidate.is_dir():
            missing[subject] = "recorded_git_cache_missing"
            continue
        sources[subject] = HistorySource(path=candidate, kind="archlens_cache")
    return sources, missing


def render_text(document: Mapping[str, Any], run_directory: Path) -> str:
    """Render the persisted two-signal evidence without inventing a score."""

    repositories = document.get("repositories") or []
    eligible = sum(int(item.get("file_count") or 0) for item in repositories)
    classified = sum(
        int(item.get("classified_file_count") or 0) for item in repositories
    )
    counts = attention_class_counts(document)
    names = {
        str(item.get("subject_key")): subject_display(item).name
        for item in repositories
    }
    lines = [
        f"Metrolith hotspots {document.get('format_version')}",
        "No score and no universal threshold.",
        "",
        f"Eligible files    {eligible}",
        f"Classified files  {classified}",
        f"Unavailable       {eligible - classified}",
        (
            "Attention classes "
            f"high {counts.get('high_attention', 0)}, "
            f"moderate {counts.get('moderate_attention', 0)}, "
            f"low {counts.get('low_attention', 0)}, "
            f"unclassified {counts.get('unclassified', 0)}"
        ),
        "",
    ]
    if eligible and not classified:
        lines.extend([
            "ATTENTION: no eligible file could be classified.",
            "Check path scope and Git-history availability; local runs usually need "
            "--repository [SUBJECT_KEY=]PATH.",
            "",
        ])
    for item in document.get("hotspots") or []:
        key = str(item.get("subject_key") or "")
        complexity = item.get("complexity") or {}
        churn = item.get("churn") or {}
        complexity_signal = item.get("complexity_signal") or {}
        churn_signal = item.get("churn_signal") or {}
        classification = item.get("classification") or "unclassified"
        lines.extend([
            f"[{classification}] {names.get(key, 'Local repository')} :: {item.get('file')}",
            f"  Subject key: {key}",
            (
                "  Complexity: status "
                f"{scalar(complexity.get('status'))}; cognitive total "
                f"{scalar(complexity.get('cognitive_complexity_total'))}; signal "
                f"{scalar(complexity_signal.get('signal'))}"
            ),
            (
                "  Churn: status "
                f"{scalar(churn.get('status'))}; touching commits "
                f"{scalar(churn.get('commits'))}; touched lines "
                f"{scalar(churn.get('touched_lines'))}; signal "
                f"{scalar(churn_signal.get('signal'))}"
            ),
        ])
        for reason in item.get("reasons") or []:
            lines.append(f"  Why: {reason}")
        lines.append("")
    display_run = portable_path(run_directory, base=Path.cwd())
    lines.append(f'Next: metrolith dossier "{display_run}" --hotspots HOTSPOTS.json')
    return "\n".join(lines) + "\n"


def handle(args) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    from validation.artifact_io.compatibility import (
        CompatibilityState,
        RunLifecycle,
    )
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    try:
        admitted = admit(args.output, overwrite=args.overwrite, runs=[args.run_directory], sources=[value.split("=", 1)[-1].strip() for value in args.repository], kind="hotspots")
    except ExportOutputError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE
    load_started = time.perf_counter()
    try:
        view = open_run(args.run_directory)
    except (ArtifactStructureError, OSError) as exc:
        print(f"[ERROR] cannot read run: {exc}", file=sys.stderr)
        return EXIT_INVALID_ARTIFACTS
    initial_load_seconds = time.perf_counter() - load_started

    if view.structural_errors:
        print(
            f"[ERROR] run has {len(view.structural_errors)} structural error(s)",
            file=sys.stderr,
        )
        return EXIT_INVALID_ARTIFACTS
    if view.compatibility.state is not CompatibilityState.SUPPORTED:
        print(
            f"[ERROR] artifact schema is {view.compatibility.state.value}: "
            f"{view.compatibility.reason}",
            file=sys.stderr,
        )
        return EXIT_INVALID_ARTIFACTS
    if view.lifecycle is not RunLifecycle.FINALIZED_VALID:
        print(
            f"[ERROR] run lifecycle is {view.lifecycle.value}; hotspots require "
            "a finalized, structurally valid run",
            file=sys.stderr,
        )
        return EXIT_INVALID_ARTIFACTS

    subjects = [subject_key_of(dict(item)) for item in view.repositories]
    try:
        explicit = _parse_overrides(args.repository, subjects)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE
    sources, missing = _automatic_sources(view, explicit)

    try:
        result = analyze_hotspots(
            view, sources, missing_source_reasons=missing
        )
        output_format = getattr(args, "format", "json")
        rendered = (
            canonical_json(result.document)
            if output_format == "json"
            else render_text(result.document, Path(args.run_directory))
        )
        if args.output:
            destination = Path(args.output)
            if destination.exists() and not args.overwrite:
                print(
                    f"[ERROR] {destination} already exists; pass --overwrite to replace it",
                    file=sys.stderr,
                )
                return EXIT_USAGE
            admitted.write(rendered)
        else:
            sys.stdout.write(rendered)
    except (ArtifactStructureError, OSError, ValueError) as exc:
        print(f"[ERROR] hotspot analysis failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.timings:
        timings = dict(result.timings)
        timings["artifact_loading_seconds"] += initial_load_seconds
        for name in (
            "artifact_loading_seconds",
            "git_history_extraction_seconds",
            "hotspot_computation_seconds",
        ):
            print(f"{name}={timings[name]:.6f}", file=sys.stderr)
    return EXIT_OK
