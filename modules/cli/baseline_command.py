"""Public CLI for capturing an owner-custodied Ratchet baseline bundle."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from archlens_json import (
    BASELINE_JSON_LIMITS,
    StrictJsonError,
    load_file,
)
from modules.presentation import portable_path
from modules.ratchet.capture import BaselineCaptureError, capture_baseline
from modules.ratchet.cli_request import REVISION_SOURCE_GUIDANCE, source_run_directory
from modules.ratchet.contract import RatchetContractError, RatchetRule
from modules.run_artifacts import atomic_write_text


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def add_parser(subparsers) -> None:
    baseline = subparsers.add_parser(
        "baseline",
        help="Capture and inspect owner-custodied Ratchet baselines",
        description=(
            "Ratchet baselines are captured from finalized measurements. Capture "
            "does not promote, trust, or publish the resulting bytes."
        ),
    )
    commands = baseline.add_subparsers(dest="baseline_command", required=True)
    capture = commands.add_parser(
        "capture",
        help="Capture a canonical baseline plus its immutable source-run sidecar",
        description=(
            "Use the complete rule array in examples/ratchet-rules.json from the "
            "source distribution (also installed in the examples package). Its "
            "zero tolerance is illustrative, not a quality recommendation. "
            "metric_contract names a family such as metrics, not a version. "
            "Requires a finalized revision-bound producing run with reproducible "
            "evaluator provenance; never edit evidence to satisfy admission. "
            + REVISION_SOURCE_GUIDANCE
        ),
    )
    capture.add_argument("run_directory", type=Path, help="Finalized producing run")
    capture.add_argument(
        "--rules",
        type=Path,
        required=True,
        help="Strict JSON array of Ratchet rule objects; observations are rejected",
    )
    capture.add_argument(
        "--output", type=Path, required=True, help="New canonical baseline JSON path"
    )


def _load_rules(path: Path) -> tuple[RatchetRule, ...]:
    raw = load_file(
        path,
        source=str(path),
        limits=BASELINE_JSON_LIMITS,
        expect=list,
    )
    if not raw:
        raise RatchetContractError("rules must contain at least one rule")
    return tuple(RatchetRule.from_dict(item, index=index) for index, item in enumerate(raw))


def _capture(args) -> int:
    run = Path(args.run_directory).expanduser().resolve(strict=False)
    rules_path = Path(args.rules).expanduser().resolve(strict=False)
    output = Path(args.output).expanduser().resolve(strict=False)
    sidecar = source_run_directory(output)
    if not run.is_dir():
        print("[ERROR] producing run directory does not exist", file=sys.stderr)
        return EXIT_USAGE
    if output == rules_path or output == run or output.is_relative_to(run):
        print("[ERROR] baseline output must be outside the producing run", file=sys.stderr)
        return EXIT_USAGE
    if output.exists() or sidecar.exists():
        print(
            "[ERROR] output or its .source-run sidecar already exists; capture never overwrites",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        rules = _load_rules(rules_path)
        captured = capture_baseline(run, rules)
        output.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(
            tempfile.mkdtemp(prefix=".metrolith-baseline-", dir=output.parent)
        )
        staged_baseline = staging_root / output.name
        staged_sidecar = staging_root / sidecar.name
        try:
            shutil.copytree(run, staged_sidecar, copy_function=shutil.copy2)
            atomic_write_text(staged_baseline, captured.payload.decode("utf-8"))
            os.replace(staged_baseline, output)
            try:
                os.replace(staged_sidecar, sidecar)
            except OSError:
                output.unlink(missing_ok=True)
                raise
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
    except (StrictJsonError, RatchetContractError, BaselineCaptureError, OSError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", None) or str(exc)
        if isinstance(exc, RatchetContractError):
            detail += (
                "; correct the rule array using examples/ratchet-rules.json "
                "(metric_contract is a family such as metrics, not a version)"
            )
        suffix = f": {detail}" if detail else ""
        print(f"[ERROR] baseline capture failed ({code}){suffix}", file=sys.stderr)
        return EXIT_ERROR

    baseline = captured.baseline
    display_output = portable_path(output, base=Path.cwd())
    display_sidecar = portable_path(sidecar, base=Path.cwd())
    display_run = portable_path(run, base=Path.cwd())
    print("Metrolith Ratchet baseline captured")
    print(f"Baseline             {display_output}")
    print(f"SHA-256              {captured.sha256}")
    print(f"Producing run        {display_run}")
    print(f"Source-run evidence  {display_sidecar}")
    print(f"Producer identity    {baseline.producer.evaluator_source_identity}")
    print(
        "Semantics fingerprint "
        f"{baseline.measurement_semantics.fingerprint_sha256}"
    )
    print(f"Coordinates          {len(baseline.coordinate_manifest)}")
    print("")
    print(
        "Captured only: the baseline has NOT been promoted and is not trusted "
        "merely because it was written. The digest must be stored in an "
        "owner-controlled trust source."
    )
    print(REVISION_SOURCE_GUIDANCE)
    print(
        f'Next: metrolith check RUN --policy POLICY --baseline "{display_output}" '
        f"--baseline-sha256 {captured.sha256}"
    )
    return EXIT_OK


def handle(args) -> int:
    if args.baseline_command == "capture":
        return _capture(args)
    return EXIT_USAGE


__all__ = ["EXIT_ERROR", "EXIT_OK", "EXIT_USAGE", "add_parser", "handle"]
