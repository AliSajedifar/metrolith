"""``metrolith perf`` — performance profiles (plan section 19).

**Report-only.** No threshold blocks anything. A regression verdict here is an
observation for a human, not a gate, and it stays that way until repeated
stability evidence exists across hosts (plan section 19.8). A blocking threshold
built on a single machine's noise would fail runs for reasons unrelated to the
code.

`performance_profile.json` is created only by these commands, at the path the
caller asks for. It is **not** a mandatory run artifact and its absence never
fails run validation.

Timing fields and performance profiles are excluded from the semantic payload
and from Frozen/Offline measurement equality (plan section 19.4).
"""

from __future__ import annotations

from modules.subject import subject_key_of
import hashlib
import json
import platform
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

PERFORMANCE_FORMAT_VERSION = "1.0.0"

# The normative clock a profile measures (plan section 19.1).
TIMING_DEFINITION = (
    "measurement_wall_seconds: run_started_at to measurement_finished_at, "
    "excluding artifact finalization"
)

COMPARABLE = "comparable"
INCOMPARABLE_SHAPE = "incomparable_shape"
INCOMPARABLE_ENVIRONMENT = "incomparable_environment"
NOT_EVALUABLE = "not_evaluable"


def add_parser(subparsers) -> None:
    perf = subparsers.add_parser(
        "perf", help="Record or compare performance profiles (report-only)"
    )
    perf_sub = perf.add_subparsers(dest="perf_command", required=True)

    baseline = perf_sub.add_parser("baseline", help="Record a profile from a run")
    baseline.add_argument("run_directory", type=Path)
    baseline.add_argument("--out", type=Path, required=True)

    check = perf_sub.add_parser("check", help="Compare a run against a baseline profile")
    check.add_argument("run_directory", type=Path)
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")


def handle(args) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    try:
        destination = admit(getattr(args, "out", None), overwrite=True, runs=[args.run_directory], kind="perf")
    except ExportOutputError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        view = open_run(args.run_directory)
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_ERROR

    if args.perf_command == "baseline":
        profile = build_profile(view)
        try:
            destination.write(json.dumps(profile, indent=2, sort_keys=True) + "\n")
        except ExportOutputError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"Wrote performance profile to {args.out}")
        print(f"Profile hash: {profile['profile_hash']}")
        return EXIT_OK

    if args.perf_command == "check":
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[ERROR] cannot read baseline profile: {exc}")
            return EXIT_ERROR
        report = compare_profiles(baseline, build_profile(view))
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_text(report))
        # Report-only: a regression never changes the exit code.
        return EXIT_OK

    return EXIT_USAGE


def repository_samples(view) -> list[float]:
    """Per-repository durations, in deterministic repository order.

    These are per-repository wall times and may overlap under concurrency. They
    are never summed into a run wall time (plan section 19.2).
    """
    samples: list[float] = []
    for item in sorted(
        view.repositories, key=lambda entry: subject_key_of(entry)
    ):
        duration = item.get("repository_duration_seconds")
        if isinstance(duration, (int, float)):
            samples.append(float(duration))
    return samples


def _host_class() -> str:
    """A normalized, non-PII host descriptor. Never a hostname or username."""
    return f"{platform.system()}-{platform.machine()}-cpython{sys.version_info.major}.{sys.version_info.minor}"


def build_profile(view) -> dict[str, Any]:
    """Build a profile from a completed run's recorded timings."""
    manifest = view.manifest
    samples = repository_samples(view)
    cohort = hashlib.sha256(
        "\n".join(sorted(view.repositories_by_url)).encode("utf-8")
    ).hexdigest()

    profile: dict[str, Any] = {
        "performance_format_version": PERFORMANCE_FORMAT_VERSION,
        "cohort_or_case_hash": cohort,
        "raw_samples": samples,
        "sample_count": len(samples),
        "warmup_count": 0,
        "median_seconds": round(statistics.median(samples), 6) if samples else 0.0,
        "percentiles": _percentiles(samples),
        "absolute_noise_floor_seconds": 0.05,
        "workers": int((manifest.get("effective_configuration") or {}).get("workers") or 1),
        "acquisition_mode": str(manifest.get("acquisition_mode") or "offline"),
        "cache_mode": _cache_mode(view),
        "host_class": _host_class(),
        "python_version": str(manifest.get("benchmark_environment", {}).get(
            "python_version_exact") or platform.python_version()),
        "parser_versions": dict(manifest.get("grammar_versions") or {}),
        "program_version": str(manifest.get("program_version") or ""),
        "profiler_identity": view.environment.get("profiler_git_commit_sha"),
        "timing_definition": TIMING_DEFINITION,
        "comparability_verdict": COMPARABLE if samples else NOT_EVALUABLE,
        "blocking": False,
    }
    profile["profile_hash"] = _profile_hash(profile)
    return profile


def _cache_mode(view) -> str | None:
    """Observed cache warmth, from the run's own acquisition records.

    This was hardcoded ``"warm"``, which asserted a property of the run that
    nothing had checked — and cache warmth is one of the largest single
    influences on the timings a profile records. ``None`` means the run does not
    record it, which is deliberately distinct from claiming either state.
    """
    observed = [
        (item.get("acquisition") or {}).get("cache_hit")
        for item in view.repositories
    ]
    recorded = [value for value in observed if isinstance(value, bool)]
    if not recorded:
        return None
    if all(recorded):
        return "warm"
    if not any(recorded):
        return "cold"
    return "mixed"


def _percentiles(samples: Sequence[float]) -> dict[str, float]:
    if not samples:
        return {}
    ordered = sorted(samples)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 6)

    return {"p50": at(0.50), "p90": at(0.90), "p99": at(0.99)}


def _profile_hash(profile: Mapping[str, Any]) -> str:
    payload = {
        key: value for key, value in profile.items()
        if key not in {"profile_hash", "raw_samples", "median_seconds", "percentiles"}
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


# Most severe first. The old code assigned the verdict by straight assignment,
# so the *last* check that happened to fire won: a comparison across different
# cohorts AND different hosts reported only `incomparable_environment`, hiding
# the more fundamental problem that the two profiles measured different work.
#
# `program_version` is deliberately absent from every gate below. Comparing one
# build against a baseline recorded by another is the entire purpose of a
# performance baseline, so a version difference must never make a comparison
# incomparable.
_VERDICT_SEVERITY = {
    COMPARABLE: 0,
    INCOMPARABLE_ENVIRONMENT: 1,
    INCOMPARABLE_SHAPE: 2,
    NOT_EVALUABLE: 3,
}


def compare_profiles(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare two profiles. Always report-only."""
    reasons: list[str] = []
    verdict = COMPARABLE

    def worsen(candidate_verdict: str, reason: str) -> None:
        nonlocal verdict
        reasons.append(reason)
        if _VERDICT_SEVERITY[candidate_verdict] > _VERDICT_SEVERITY[verdict]:
            verdict = candidate_verdict

    if baseline.get("cohort_or_case_hash") != candidate.get("cohort_or_case_hash"):
        worsen(
            INCOMPARABLE_SHAPE,
            "cohort differs; a timing comparison across different repository "
            "sets measures the cohort, not the program",
        )
    if baseline.get("workers") != candidate.get("workers"):
        worsen(INCOMPARABLE_SHAPE, "worker count differs")
    if baseline.get("acquisition_mode") != candidate.get("acquisition_mode"):
        worsen(INCOMPARABLE_SHAPE, "acquisition mode differs")
    if baseline.get("host_class") != candidate.get("host_class"):
        worsen(INCOMPARABLE_ENVIRONMENT, "host class differs")
    if baseline.get("parser_versions") != candidate.get("parser_versions"):
        worsen(
            INCOMPARABLE_ENVIRONMENT,
            "parser grammar versions differ; parse cost is not held constant",
        )
    if not baseline.get("raw_samples") or not candidate.get("raw_samples"):
        worsen(NOT_EVALUABLE, "one profile carries no samples")

    baseline_median = baseline.get("median_seconds") or 0.0
    candidate_median = candidate.get("median_seconds") or 0.0
    noise = max(
        float(baseline.get("absolute_noise_floor_seconds") or 0.0),
        float(candidate.get("absolute_noise_floor_seconds") or 0.0),
    )

    # A delta between profiles that are not comparable is a number without a
    # meaning, and "Within noise floor: yes" underneath an `incomparable_shape`
    # verdict reads as a pass. Withhold the judgement rather than qualify it.
    comparable = verdict == COMPARABLE
    delta = round(candidate_median - baseline_median, 6) if comparable else None

    return {
        "performance_format_version": PERFORMANCE_FORMAT_VERSION,
        "comparability_verdict": verdict,
        "incomparability_reasons": reasons,
        "baseline_median_seconds": baseline_median,
        "candidate_median_seconds": candidate_median,
        "median_delta_seconds": delta,
        "absolute_noise_floor_seconds": noise,
        "within_noise_floor": (abs(delta) <= noise) if comparable else None,
        "blocking": False,
        "note": (
            "Report-only. No threshold blocks any operation. Blocking "
            "thresholds require repeated stability evidence across hosts."
        ),
    }


def render_text(report: Mapping[str, Any]) -> str:
    lines = [
        f"Metrolith performance check {report['performance_format_version']}",
        f"Comparability: {report['comparability_verdict']}",
    ]
    for reason in report["incomparability_reasons"]:
        lines.append(f"  - {reason}")
    lines.append("")
    lines.append(f"Baseline median:  {report['baseline_median_seconds']}s")
    lines.append(f"Candidate median: {report['candidate_median_seconds']}s")
    if report["comparability_verdict"] == COMPARABLE:
        lines.append(f"Delta:            {report['median_delta_seconds']}s")
        lines.append(f"Noise floor:      {report['absolute_noise_floor_seconds']}s")
        lines.append(
            f"Within noise floor: {'yes' if report['within_noise_floor'] else 'no'}"
        )
    else:
        # Printing a delta here would invite exactly the reading the verdict
        # above rules out.
        lines.append(
            "Delta:            not reported; these profiles are not comparable"
        )
    lines.append("")
    lines.append(report["note"])
    return "\n".join(lines)
