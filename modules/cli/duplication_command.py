"""``metrolith duplication`` — standalone exact duplication analysis."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterable

from modules.config import AnalysisConfig
from modules.duplication.output import (
    LEXICAL,
    STRUCTURAL,
    analyze_duplication_snapshot,
    canonical_json,
    render_text,
)
from modules.local_source import (
    LocalSourceError,
    is_git_repository,
    prepare_local_source,
)


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "duplication",
        help="Find exact lexical and structural duplicate body groups",
        description=(
            "Analyze one immutable snapshot with Duplication Output Contract "
            "1.0.0. Duplicate presence does not change the exit code and is not "
            "a quality, defect, or security finding. Exit 0 = validated output, "
            "1 = operational/analysis failure, and 2 = invalid usage or overwrite "
            "refusal."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Local source directory or Git worktree",
    )
    parser.add_argument(
        "--revision",
        help="Analyze this exact committed Git revision",
    )
    parser.add_argument(
        "--tracked-only",
        action="store_true",
        help="For a Git worktree, exclude non-ignored untracked files",
    )
    parser.add_argument(
        "--kind",
        choices=("all", LEXICAL, STRUCTURAL),
        default="all",
        help="Clone kind to analyze (default: all)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the selected standalone format to this file",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output file",
    )
    parser.add_argument(
        "--timings",
        action="store_true",
        help="Write observational phase timings to stderr only",
    )
    return parser


def _usage_error(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return EXIT_USAGE


def _safe_detail(exc: BaseException, roots: Iterable[Path]) -> str:
    detail = " ".join(str(exc).split()) or type(exc).__name__
    candidates = [*roots, Path.home()]
    for root in candidates:
        for value in {str(root), root.as_posix()}:
            if value:
                detail = detail.replace(value, "<source>")
    return detail[:300]


def _requested_kinds(value: str) -> tuple[str, ...]:
    if value == "all":
        return LEXICAL, STRUCTURAL
    return (value,)


def handle(args) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    try:
        admitted = admit(args.output, overwrite=args.overwrite, sources=[args.path], kind="duplication")
        return _handle(args, admitted)
    except ExportOutputError as exc:
        return _usage_error(str(exc))


def _handle(args, admitted) -> int:
    source = Path(args.path).expanduser().resolve(strict=False)
    output = (
        Path(args.output).expanduser().resolve(strict=False)
        if args.output is not None
        else None
    )
    if args.revision and args.tracked_only:
        return _usage_error("--tracked-only cannot be combined with --revision")
    if args.overwrite and output is None:
        return _usage_error("--overwrite requires --output")
    if not source.is_dir():
        print("[ERROR] source directory could not be prepared", file=sys.stderr)
        return EXIT_ERROR
    if args.tracked_only and not is_git_repository(source):
        return _usage_error("--tracked-only requires a Git worktree")
    if output is not None and output.exists() and not args.overwrite:
        return _usage_error("output already exists; pass --overwrite to replace it")

    snapshot_started = time.perf_counter()
    snapshot_root: Path | None = None
    try:
        with prepare_local_source(
            source,
            revision=args.revision,
            tracked_only=bool(args.tracked_only),
        ) as snapshot:
            snapshot_root = snapshot.path
            snapshot_seconds = time.perf_counter() - snapshot_started
            analysis = analyze_duplication_snapshot(
                snapshot,
                requested_kinds=_requested_kinds(args.kind),
                tracked_only=bool(args.tracked_only),
                config=AnalysisConfig(),
            )
            rendering_started = time.perf_counter()
            rendered = (
                canonical_json(analysis.document)
                if args.format == "json"
                else render_text(analysis.document)
            )
            if args.format == "text":
                rendered += "Next: metrolith dossier RUN --duplication DUPLICATION.json\n"
            if output is None:
                sys.stdout.write(rendered)
            else:
                admitted.write(rendered)
            rendering_seconds = time.perf_counter() - rendering_started
    except LocalSourceError as exc:
        print(
            f"[ERROR] source could not be prepared: "
            f"{_safe_detail(exc, (source,))}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    except Exception as exc:
        roots = (source,) if snapshot_root is None else (source, snapshot_root.parent)
        print(
            f"[ERROR] duplication analysis failed: {_safe_detail(exc, roots)}",
            file=sys.stderr,
        )
        return EXIT_ERROR

    if args.timings:
        print(f"snapshot_seconds={snapshot_seconds:.6f}", file=sys.stderr)
        for name in (
            "inventory_seconds",
            "syntax_and_extraction_seconds",
            "lexical_canonicalization_seconds",
            "structural_canonicalization_seconds",
            "lexical_grouping_seconds",
            "structural_grouping_seconds",
            "output_building_seconds",
        ):
            print(f"{name}={analysis.timings[name]:.6f}", file=sys.stderr)
        print(f"rendering_and_writing_seconds={rendering_seconds:.6f}", file=sys.stderr)
    return EXIT_OK


__all__ = ["EXIT_ERROR", "EXIT_OK", "EXIT_USAGE", "add_parser", "handle"]
