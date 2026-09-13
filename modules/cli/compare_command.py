"""``metrolith compare --explain`` (plan section 15).

The default N-run comparison is untouched: same arguments, same output, same
exit codes. ``--explain`` is a separate mode over exactly two runs that reports
*why* they differ, using reconciliation wording rather than causal claims.

Comparison is **key-aligned**, never positional. Comparing scientific lists by
index means inserting one repository at the top reports every later repository
as changed, which buries the real difference in noise.

Evidence levels (plan section 15.4) distinguish what was observed from what can
be accounted for:

``observed_difference``
    The values differ. Nothing more is claimed.
``exact_observed_delta_reconciliation``
    Complete ledgers on both sides account for the delta with zero residual.
``unexplained_residual``
    Reconciliation ran and did not close.
``not_evaluable_missing_evidence``
    A required artifact is absent, so the question cannot be answered.

Absence of evidence is never reported as absence of difference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

EXIT_EQUAL = 0
EXIT_DIFFERENT = 1
EXIT_USAGE = 2
EXIT_INVALID_ARTIFACTS = 3
EXIT_INCOMPARABLE = 4

# 1.1.0: 1.0 predated fields this command already emitted, while its
# schema declared `additionalProperties: false`, so every real invocation
# violated its own published contract. The emitted payload is the intended
# interface, so the schema was brought up to it rather than the reverse.
COMPARE_FORMAT_VERSION = "1.1.0"

OBSERVED_DIFFERENCE = "observed_difference"
CONSISTENT_WITH_RECORDED_CHANGE = "consistent_with_recorded_change"
EXACT_RECONCILIATION = "exact_observed_delta_reconciliation"
UNEXPLAINED_RESIDUAL = "unexplained_residual"
NOT_EVALUABLE = "not_evaluable_missing_evidence"

REPOSITORY_ABSENT_ONE_SIDE = "not_evaluable_repository_absent_on_one_side"

# Aggregate dimensions compared per repository.
METRIC_DIMENSIONS = (
    "source_files", "lines_of_code", "classes_structs", "methods_functions",
)
STATUS_DIMENSIONS = (
    "inventory_status", "source_files_status", "loc_status",
    "classes_structs_status", "methods_functions_status", "metric_status",
)


def add_explain_arguments(parser) -> None:
    """Add the explain-mode flags to the existing ``compare`` parser."""
    parser.add_argument(
        "--explain", action="store_true",
        help=(
            "Explain differences between exactly two runs. Exit codes in this "
            "mode are 0 equal, 1 different, 2 usage, 3 invalid artifacts, "
            "4 incomparable."
        ),
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--repo", dest="repository",
        help="Restrict the explanation to one repository, matched on canonical URL",
    )


def handle(args) -> int:
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    directories = list(args.run_directories)
    if len(directories) != 2:
        print("[ERROR] compare --explain accepts exactly two run directories")
        return EXIT_USAGE

    # Reading is lazy, so a malformed optional artifact surfaces during
    # `build_comparison` rather than at `open_run`. Both stages belong inside
    # the guard; leaving the second outside produced an uncaught traceback
    # instead of a typed error and an exit code.
    try:
        left = open_run(directories[0])
        right = open_run(directories[1])
        payload = build_comparison(
            left, right, repository=getattr(args, "repository", None)
        )
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_INVALID_ARTIFACTS

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_text(payload))
        # Text surface only: `compare_output` is `additionalProperties: false`
        # and a new format version is outside the R0 schema inventory.
        print(render_qualification_comparison(left, right))

    if payload["invalid_artifacts"]:
        return EXIT_INVALID_ARTIFACTS
    if not payload["comparable"]:
        return EXIT_INCOMPARABLE
    return EXIT_DIFFERENT if payload["differences"] else EXIT_EQUAL


def qualification_gate(left, right) -> tuple[bool, list[str]]:
    """Whether these two runs may be called a repository-level benchmark comparison.

    Both gates, on both sides. A measurement comparison remains a perfectly
    valid measurement comparison without them — this only decides what the
    result may be CALLED, which matters because "Metrolith says these two
    repositories differ by 40% LOC" is a different claim from "these two
    measurements differ", and only the second is supported when either side is
    unqualified or not ready.
    """
    reasons: list[str] = []
    for label, view in (("run_a", left), ("run_b", right)):
        if view.qualification_mode != "benchmark_qualified":
            reasons.append(f"{label} is not benchmark-qualified")
            continue
        readiness = view.benchmark_of_record_readiness or {}
        if str(readiness.get("status")) != "READY":
            reasons.append(
                f"{label} benchmark-of-record readiness is "
                f"{readiness.get('status') or 'absent'}"
            )
    return (not reasons), reasons


def render_qualification_comparison(left, right) -> str:
    """Qualification differences, reported as their own dimension.

    Never merged into the measurement differences. A representativeness verdict
    changing between two runs is not a metric change, and presenting it in the
    same list would make it read as one.
    """
    from modules.benchmark_qualification import subject_of

    permitted, reasons = qualification_gate(left, right)
    lines = ["", "Qualification comparison (not a measurement comparison):"]
    lines.append(f"  run_a mode: {left.qualification_mode}")
    lines.append(f"  run_b mode: {right.qualification_mode}")

    if permitted:
        lines.append(
            "  Both sides are benchmark-qualified and benchmark-of-record "
            "READY: this may be presented as a repository-level benchmark "
            "comparison."
        )
    else:
        lines.append(
            "  This is a MEASUREMENT COMPARISON ONLY. It must not be presented "
            "as a repository-level benchmark comparison:"
        )
        lines.extend(f"    - {reason}" for reason in reasons)

    def _records(view):
        artifact = view.benchmark_qualification
        if artifact is None:
            return {}
        return {
            subject_of(record.get("binding") or {}): record
            for record in artifact.get("records") or ()
        }

    a, b = _records(left), _records(right)
    changed = []
    for subject in sorted(set(a) | set(b)):
        first, second = a.get(subject), b.get(subject)
        for field in (
            "repository_representativeness",
            "benchmark_usability",
            "repository_level_comparison_eligible",
            "manual_admission_restriction",
        ):
            before = (first or {}).get(field)
            after = (second or {}).get(field)
            if before != after:
                changed.append(f"    {subject}: {field} {before!r} -> {after!r}")
    if changed:
        lines.append("  Qualification differences:")
        lines.extend(changed)
    elif a or b:
        lines.append("  No qualification differences observed.")
    return "\n".join(lines)


def _readable(view) -> bool:
    return view.compatibility.readable and not view.structural_errors


def build_comparison(left, right, *, repository: str | None = None) -> dict[str, Any]:
    """Compare two runs and explain the differences found."""
    invalid: list[str] = []
    for label, view in (("run_a", left), ("run_b", right)):
        if not view.compatibility.readable:
            invalid.append(f"{label}: {view.compatibility.reason}")
        for problem in view.structural_errors:
            invalid.append(f"{label}: {problem}")

    # Every field here changes what the metrics *mean*, so a difference makes
    # the numbers incomparable rather than merely different.
    #
    # The Exclusion Policy was previously unchecked, yet it decides which files
    # are counted at all: two runs under different policies produce different
    # Source Files and Lines of Code for identical repositories, and comparing
    # them reports a metric change where only the selection rule changed.
    incomparable_reasons: list[str] = []
    for label, key in (
        ("Metric Contract", "metric_contract_version"),
        ("Exclusion Policy", "exclusion_policy_version"),
        ("Exclusion Policy bytes", "exclusion_policy_sha256"),
    ):
        left_value = left.manifest.get(key)
        right_value = right.manifest.get(key)
        if left_value != right_value:
            incomparable_reasons.append(
                f"{label} differs: run_a records {left_value!r}, run_b records "
                f"{right_value!r}. Metric values defined by different "
                f"{label.lower()} settings are not comparable."
            )

    contracts_equal = not incomparable_reasons
    incomparable_reason = " ".join(incomparable_reasons) or None

    comparable = bool(not invalid and contracts_equal)

    blockers: list[str] = []
    if not left.has_contribution_ledger or not right.has_contribution_ledger:
        blockers.append(
            "exact metric reconciliation requires a complete contribution ledger "
            "on both sides"
        )
    if not left.has_inventory or not right.has_inventory:
        blockers.append("inventory dimension requires inventories on both sides")
    if left.normalized_input is None or right.normalized_input is None:
        blockers.append(
            "input-population comparison requires normalized_input.csv on both sides"
        )

    differences: list[dict[str, Any]] = []
    if comparable:
        differences = _repository_differences(
            left, right, repository=repository, exact_available=not blockers
        )

    return {
        "compare_format_version": COMPARE_FORMAT_VERSION,
        "run_a": str(left.run_directory),
        "run_b": str(right.run_directory),
        "run_a_id": left.run_id,
        "run_b_id": right.run_id,
        "comparable": comparable,
        "incomparable_reason": incomparable_reason,
        "invalid_artifacts": invalid,
        "metric_contract_versions_equal": contracts_equal,
        "exact_reconciliation_available": not blockers,
        "exact_reconciliation_blockers": blockers,
        "repository_filter": repository,
        "differences": differences,
    }


def _repository_differences(
    left, right, *, repository: str | None, exact_available: bool
) -> list[dict[str, Any]]:
    """Key-aligned repository comparison. Canonical URL is the key."""
    left_by_url = dict(left.repositories_by_url)
    right_by_url = dict(right.repositories_by_url)

    keys = sorted(set(left_by_url) | set(right_by_url))
    if repository:
        keys = [key for key in keys if key == repository]

    found: list[dict[str, Any]] = []
    for url in keys:
        a = left_by_url.get(url)
        b = right_by_url.get(url)

        # A repository present on only one side is a named state, not a generic
        # difference: there is nothing to compare it against.
        if a is None or b is None:
            found.append({
                "dimension": "repository_presence",
                "key": url,
                "evidence_level": NOT_EVALUABLE,
                "value_a": None if a is None else "present",
                "value_b": None if b is None else "present",
                "observed_delta": None,
                "residual": None,
                "not_evaluable_reason": REPOSITORY_ABSENT_ONE_SIDE,
            })
            continue

        aggregate_a = (a.get("metrics") or {}).get("aggregate") or {}
        aggregate_b = (b.get("metrics") or {}).get("aggregate") or {}

        for dimension in STATUS_DIMENSIONS:
            value_a = aggregate_a.get(dimension)
            value_b = aggregate_b.get(dimension)
            if value_a != value_b:
                found.append({
                    "dimension": dimension,
                    "key": url,
                    "evidence_level": OBSERVED_DIFFERENCE,
                    "value_a": value_a,
                    "value_b": value_b,
                    "observed_delta": None,
                    "residual": None,
                    "not_evaluable_reason": None,
                })

        for dimension in METRIC_DIMENSIONS:
            value_a = aggregate_a.get(dimension)
            value_b = aggregate_b.get(dimension)
            if value_a == value_b:
                continue
            found.append(
                _metric_difference(
                    url, dimension, value_a, value_b,
                    left=left, right=right, exact_available=exact_available,
                )
            )

        for dimension in ("analysis_status", "expected_language_family_status",
                          "partial_origin"):
            if a.get(dimension) != b.get(dimension):
                found.append({
                    "dimension": dimension,
                    "key": url,
                    "evidence_level": OBSERVED_DIFFERENCE,
                    "value_a": a.get(dimension),
                    "value_b": b.get(dimension),
                    "observed_delta": None,
                    "residual": None,
                    "not_evaluable_reason": None,
                })

    return found


def _metric_difference(
    url: str, dimension: str, value_a: Any, value_b: Any, *,
    left, right, exact_available: bool,
) -> dict[str, Any]:
    """Classify one numeric metric difference."""
    # A null on either side means the measurement was unavailable, not zero.
    if value_a is None or value_b is None:
        return {
            "dimension": dimension,
            "key": url,
            "evidence_level": NOT_EVALUABLE,
            "value_a": value_a,
            "value_b": value_b,
            "observed_delta": None,
            "residual": None,
            "not_evaluable_reason": (
                "a metric value is null on at least one side; null means "
                "unavailable and is never treated as zero"
            ),
        }

    delta = value_b - value_a
    if not exact_available:
        return {
            "dimension": dimension,
            "key": url,
            "evidence_level": OBSERVED_DIFFERENCE,
            "value_a": value_a,
            "value_b": value_b,
            "observed_delta": delta,
            "residual": None,
            "not_evaluable_reason": None,
        }

    residual = _reconcile_from_ledgers(url, dimension, delta, left, right)
    if residual is None:
        return {
            "dimension": dimension,
            "key": url,
            "evidence_level": NOT_EVALUABLE,
            "value_a": value_a,
            "value_b": value_b,
            "observed_delta": delta,
            "residual": None,
            "not_evaluable_reason": "ledger rows for this repository are incomplete",
        }
    if residual == 0:
        return {
            "dimension": dimension,
            "key": url,
            "evidence_level": EXACT_RECONCILIATION,
            "value_a": value_a,
            "value_b": value_b,
            "observed_delta": delta,
            "residual": 0,
            "not_evaluable_reason": None,
        }
    return {
        "dimension": dimension,
        "key": url,
        "evidence_level": UNEXPLAINED_RESIDUAL,
        "value_a": value_a,
        "value_b": value_b,
        "observed_delta": delta,
        "residual": residual,
        "not_evaluable_reason": None,
    }


_LEDGER_FIELD = {
    "lines_of_code": "lines_of_code",
    "classes_structs": "classes_structs",
    "methods_functions": "methods_functions",
    "source_files": "source_files_contribution",
}


def _ledger_totals(view) -> dict[tuple[str, str], int | None]:
    """Sum every reconcilable field per repository in a single ledger pass.

    Memoized on the view. The previous implementation streamed the **whole**
    ledger once per repository per dimension, and streamed reads are
    deliberately uncached, so a comparison cost four full passes per side per
    repository. On a cohort-scale ledger that is the difference between one
    sequential read and hundreds.

    A ``None`` total means the ledger cannot answer for that pair — either no
    contributing row was seen, or a contributing row recorded no value, which
    must not be summed as zero.
    """
    cached = getattr(view, "_compare_ledger_totals", None)
    if cached is not None:
        return cached

    totals: dict[tuple[str, str], int] = {}
    unusable: set[tuple[str, str]] = set()
    for row in view.stream_contributions():
        if row.get("contribution_state") != "contributed":
            continue
        url = str(row.get("repository_url") or "")
        for field in set(_LEDGER_FIELD.values()):
            key = (url, field)
            value = row.get(field)
            if value is None:
                unusable.add(key)
                continue
            totals[key] = totals.get(key, 0) + int(value)

    resolved: dict[tuple[str, str], int | None] = {
        key: (None if key in unusable else value) for key, value in totals.items()
    }
    for key in unusable:
        resolved.setdefault(key, None)

    try:
        view._compare_ledger_totals = resolved
    except AttributeError:  # pragma: no cover - defensive for exotic views
        pass
    return resolved


def _reconcile_from_ledgers(url, dimension, delta, left, right) -> int | None:
    """Account for an observed delta from per-file contributions on both sides.

    Returns the residual, or ``None`` when the ledgers cannot answer.
    """
    field = _LEDGER_FIELD.get(dimension)
    if field is None:
        return None

    left_total = _ledger_totals(left).get((url, field))
    right_total = _ledger_totals(right).get((url, field))
    if left_total is None or right_total is None:
        return None
    return delta - (right_total - left_total)


def render_text(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Metrolith compare explanation {payload['compare_format_version']}")
    lines.append(f"run_a: {payload['run_a_id']}")
    lines.append(f"run_b: {payload['run_b_id']}")
    lines.append("")

    if payload["invalid_artifacts"]:
        lines.append("Invalid artifacts:")
        lines.extend(f"  - {item}" for item in payload["invalid_artifacts"])
        return "\n".join(lines)

    if not payload["comparable"]:
        lines.append(f"Incomparable: {payload['incomparable_reason']}")
        return "\n".join(lines)

    if payload["exact_reconciliation_blockers"]:
        lines.append("Exact reconciliation is not available:")
        lines.extend(
            f"  - {item}" for item in payload["exact_reconciliation_blockers"]
        )
        lines.append("")

    differences = payload["differences"]
    if not differences:
        lines.append("No differences observed in the compared dimensions.")
        return "\n".join(lines)

    lines.append(f"{len(differences)} observed difference(s):")
    for item in differences:
        lines.append("")
        lines.append(f"  {item['key']}")
        lines.append(f"    dimension: {item['dimension']}")
        lines.append(f"    evidence level: {item['evidence_level']}")
        lines.append(f"    run_a: {item['value_a']!r}   run_b: {item['value_b']!r}")
        if item.get("observed_delta") is not None:
            lines.append(f"    observed delta: {item['observed_delta']}")
        if item.get("residual") is not None:
            lines.append(f"    residual: {item['residual']}")
        if item.get("not_evaluable_reason"):
            lines.append(f"    not evaluable: {item['not_evaluable_reason']}")
    return "\n".join(lines)
