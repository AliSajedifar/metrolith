"""``metrolith changed`` — exact committed-tree changed-code analysis."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Mapping

from modules.changed_code import (
    build_document,
    canonical_json,
    diagnostic_document,
    render_markdown,
    render_text,
)
from modules.git_change_extractor import GitExtractionFailed, extract_git_changes
from modules.revision_source import (
    RevisionAnalysisFailed,
    RevisionUnavailable,
    analyze_revision_pair,
    resolve_revision_pair,
)


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3


def add_parser(subparsers) -> Any:
    parser = subparsers.add_parser(
        "changed",
        help="Describe changed files/ranges with canonical metrics on both sides",
        description=(
            "Compare two explicit committed revisions directly. Git identifies "
            "file/range changes; canonical Metrolith analysis supplies side-local "
            "measurement evidence. This command is descriptive, not a gate. Exit "
            "0 = completed, 1 = failed, 2 = invalid usage/overwrite refusal, and "
            "3 = a requested revision was unavailable."
        ),
    )
    parser.add_argument("source", type=Path, help="Local Git repository")
    parser.add_argument("--base", required=True, help="Explicit baseline commit-ish")
    parser.add_argument("--head", required=True, help="Explicit comparison commit-ish")
    parser.add_argument(
        "--format", choices=("text", "json", "markdown"), default="text",
        help=(
            "Output format (default: text). `markdown` renders a deterministic "
            "human review brief from the same document `json` emits; it is a "
            "rendering, not a second analysis."
        ),
    )
    parser.add_argument("--output", type=Path, help="Atomically write output to this file")
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace an existing output file"
    )
    parser.add_argument(
        "--timings", action="store_true",
        help="Write observational phase timings to stderr only",
    )
    # Keep the public v1 surface closed while satisfying the shared config
    # loader's internal namespace contract. Workspace locations remain
    # configurable through the ordinary Metrolith environment variables.
    parser.set_defaults(
        workspace=None,
        output_root=None,
        cache_root=None,
        temp_dir=None,
    )
    return parser


def _usage_error(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return EXIT_USAGE


#: One renderer per format. A mapping rather than a chain of conditionals so
#: that adding a format cannot silently fall through to the default one.
_RENDERERS = {
    "json": canonical_json,
    "markdown": render_markdown,
    "text": render_text,
}


def _render(document: Mapping[str, Any], output_format: str) -> str:
    return _RENDERERS[output_format](document)


def _emit(document: Mapping[str, Any], args) -> None:
    rendered = _render(document, args.format)
    if args.format != "json":
        rendered += "Next: metrolith dossier RUN --changed CHANGED.json\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args._export_destination.write(rendered)


def _diagnostic(
    args,
    *,
    status: str,
    reason: str,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    return diagnostic_document(
        status=status,
        reason=reason,
        base_requested=args.base,
        head_requested=args.head,
        base_sha=base_sha,
        head_sha=head_sha,
    )


def handle(args, config) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    try:
        args._export_destination = admit(args.output, overwrite=args.overwrite, sources=[args.source], kind="changed")
        return _handle(args, config)
    except ExportOutputError as exc:
        return _usage_error(str(exc))


def _handle(args, config) -> int:
    source = Path(args.source).expanduser().resolve(strict=False)
    args.output = (
        Path(args.output).expanduser().resolve(strict=False)
        if args.output is not None else None
    )
    if args.overwrite and args.output is None:
        return _usage_error("--overwrite requires --output")
    if args.output is not None and args.output.exists() and not args.overwrite:
        return _usage_error("output already exists; pass --overwrite to replace it")

    timings: dict[str, float] = {}
    resolution_started = time.perf_counter()
    try:
        pair = resolve_revision_pair(
            source,
            args.base,
            args.head,
            timeout=config.git_timeout_seconds,
        )
    except RevisionUnavailable as exc:
        timings["revision_resolution_seconds"] = time.perf_counter() - resolution_started
        document = _diagnostic(args, status="unavailable", reason=exc.reason)
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_UNAVAILABLE
    except Exception:
        timings["revision_resolution_seconds"] = time.perf_counter() - resolution_started
        document = _diagnostic(
            args, status="failed", reason="revision_resolution_failed"
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    timings["revision_resolution_seconds"] = time.perf_counter() - resolution_started

    extraction_started = time.perf_counter()
    try:
        changes = extract_git_changes(pair, timeout=config.git_timeout_seconds)
    except GitExtractionFailed as exc:
        timings["git_change_extraction_seconds"] = time.perf_counter() - extraction_started
        document = _diagnostic(
            args,
            status="failed",
            reason=exc.reason,
            base_sha=pair.base.sha,
            head_sha=pair.head.sha,
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    except Exception:
        timings["git_change_extraction_seconds"] = time.perf_counter() - extraction_started
        document = _diagnostic(
            args,
            status="failed",
            reason="git_change_extraction_failed",
            base_sha=pair.base.sha,
            head_sha=pair.head.sha,
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    timings["git_change_extraction_seconds"] = time.perf_counter() - extraction_started

    analysis_started = time.perf_counter()
    try:
        analyzed = analyze_revision_pair(pair, config)
    except RevisionAnalysisFailed as exc:
        timings["canonical_analysis_seconds"] = time.perf_counter() - analysis_started
        document = _diagnostic(
            args,
            status="failed",
            reason=exc.reason,
            base_sha=pair.base.sha,
            head_sha=pair.head.sha,
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    except Exception:
        timings["canonical_analysis_seconds"] = time.perf_counter() - analysis_started
        document = _diagnostic(
            args,
            status="failed",
            reason="canonical_analysis_failed",
            base_sha=pair.base.sha,
            head_sha=pair.head.sha,
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    timings["canonical_analysis_seconds"] = time.perf_counter() - analysis_started
    timings.update(analyzed.timings)

    join_started = time.perf_counter()
    try:
        document = build_document(changes, analyzed)
    except Exception:
        timings["evidence_join_and_validation_seconds"] = time.perf_counter() - join_started
        document = _diagnostic(
            args,
            status="failed",
            reason="changed_code_document_failed",
            base_sha=pair.base.sha,
            head_sha=pair.head.sha,
        )
        _emit(document, args)
        _print_timings(args, timings)
        return EXIT_FAILED
    timings["evidence_join_and_validation_seconds"] = time.perf_counter() - join_started

    render_started = time.perf_counter()
    try:
        _emit(document, args)
    except OSError:
        print("[ERROR] changed-code output could not be written", file=sys.stderr)
        return EXIT_FAILED
    timings["rendering_and_writing_seconds"] = time.perf_counter() - render_started
    _print_timings(args, timings)
    return EXIT_OK


def _print_timings(args, timings: Mapping[str, float]) -> None:
    if not args.timings:
        return
    for name in (
        "revision_resolution_seconds",
        "git_change_extraction_seconds",
        "capability_barrier_seconds",
        "base_analysis_seconds",
        "head_analysis_seconds",
        "canonical_analysis_seconds",
        "evidence_join_and_validation_seconds",
        "rendering_and_writing_seconds",
    ):
        if name in timings:
            print(f"{name}={timings[name]:.6f}", file=sys.stderr)


__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_UNAVAILABLE",
    "EXIT_USAGE",
    "add_parser",
    "handle",
]
