"""``metrolith dossier`` — one offline document composed from existing evidence.

Reads an authoritative run through the same artifact reader `report`, `explain`
and `check` use, and optionally admits standalone Duplication, Hotspot,
Changed-Code and Policy documents beside it. It analyzes nothing: every figure
it prints was produced by a command that has already run.

Exit codes match the other run-reading commands so one run directory does not
produce four different verdicts depending on which command asked:
0 = composed, 1 = operational failure, 2 = invalid usage or overwrite refusal,
3 = invalid run artifacts.

**A supplement that is not admitted never fails the command.** It is reported,
with the reason, and contributes nothing. Refusing to emit a dossier because one
optional input was for the wrong commit would discard the evidence that IS
sound; printing its numbers anyway would be worse.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from modules.dossier import build_dossier, canonical_json, render_markdown

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3

#: CLI flag -> supplement kind. One mapping so the parser, the reader and the
#: composer cannot disagree about which file is which.
SUPPLEMENT_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("duplication", "--duplication", "An `metrolith duplication --format json` document"),
    ("hotspots", "--hotspots", "An `metrolith hotspots --format json` document"),
    ("changed_code", "--changed", "An `metrolith changed --format json` document"),
    ("policy", "--policy-result", "An `metrolith check --format json` result"),
)


def add_parser(subparsers) -> Any:
    parser = subparsers.add_parser(
        "dossier",
        help="Compose one offline analysis dossier from a run and existing documents",
        description=(
            "Compose a DERIVED, non-authoritative dossier from one run's "
            "artifacts plus optional standalone documents. Nothing is measured "
            "and nothing is merged: each supplement must pass its own validator "
            "AND match this run's provenance to contribute any figure. Exit "
            "0 = composed, 1 = operational failure, 2 = invalid usage/overwrite "
            "refusal, 3 = invalid run artifacts."
        ),
    )
    parser.add_argument("run_directory", type=Path)
    for name, flag, help_text in SUPPLEMENT_OPTIONS:
        parser.add_argument(flag, type=Path, dest=name, default=None, help=help_text)
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument("--output", type=Path, help="Atomically write output to this file")
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace an existing output file"
    )
    return parser


def _usage_error(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return EXIT_USAGE


def _read_supplements(args) -> tuple[dict[str, Any], dict[str, str]]:
    """Read each supplied file, recording a read failure rather than raising.

    An unreadable supplement is a reportable admission outcome, not a crash: the
    run's own evidence is still sound and still worth composing.
    """
    documents: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, _flag, _help in SUPPLEMENT_OPTIONS:
        path = getattr(args, name, None)
        if path is None:
            continue
        try:
            documents[name] = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            errors[name] = f"{type(error).__name__}: {error}"
    return documents, errors


def handle(args) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    try:
        admitted = admit(args.output, overwrite=args.overwrite, runs=[args.run_directory], files=[getattr(args, name, None) for name, _, _ in SUPPLEMENT_OPTIONS], kind="dossier")
    except ExportOutputError as exc:
        return _usage_error(str(exc))
    output = (
        Path(args.output).expanduser().resolve(strict=False)
        if args.output is not None else None
    )
    if args.overwrite and output is None:
        return _usage_error("--overwrite requires --output")
    if output is not None and output.exists() and not args.overwrite:
        return _usage_error("output already exists; pass --overwrite to replace it")

    try:
        view = open_run(args.run_directory)
    except ArtifactStructureError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return EXIT_INVALID_ARTIFACTS

    documents, read_errors = _read_supplements(args)
    try:
        document = build_dossier(
            view,
            duplication=documents.get("duplication"),
            hotspots=documents.get("hotspots"),
            changed=documents.get("changed_code"),
            policy=documents.get("policy"),
            read_errors=read_errors,
        )
    except ArtifactStructureError as error:
        # Artifacts are read lazily, so a malformed optional artifact surfaces
        # during composition rather than at open_run.
        print(f"[ERROR] {error}", file=sys.stderr)
        return EXIT_INVALID_ARTIFACTS
    except Exception as error:  # noqa: BLE001 - reported as an operational failure
        print(f"[ERROR] dossier composition failed: {error}", file=sys.stderr)
        return EXIT_ERROR

    rendered = (
        canonical_json(document) if args.format == "json"
        else render_markdown(document)
    )
    try:
        if output is None:
            sys.stdout.write(rendered)
        else:
            admitted.write(rendered)
            print(f"Wrote {len(rendered.encode('utf-8'))} byte(s) to {output}")
    except OSError:
        print("[ERROR] dossier output could not be written", file=sys.stderr)
        return EXIT_ERROR

    for record in document["supplements"]:
        if record["admission"] not in {"admitted", "not_supplied"}:
            print(
                f"[NOT ADMITTED] {record['kind']}: {record['admission']}"
                + (f" — {record['detail']}" if record.get("detail") else ""),
                file=sys.stderr,
            )
    return EXIT_OK


__all__ = [
    "EXIT_ERROR",
    "EXIT_INVALID_ARTIFACTS",
    "EXIT_OK",
    "EXIT_USAGE",
    "SUPPLEMENT_OPTIONS",
    "add_parser",
    "handle",
]
