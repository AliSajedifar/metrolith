"""``summary.md`` rendering (plan section 13).

**The summary is a mandatory but non-authoritative projection.** It is generated
by default and its absence is a mandatory output failure, but nothing may read
it back to derive measurement state. `run_status.json` is authoritative for run
integrity; `analysis.json` is authoritative for repository results.

Two properties this module has to preserve:

* **Semantic invariance.** Changing this file must not change the semantic hash
  or the run-equality payload. Nothing here feeds back into measurement.
* **Fact equivalence.** :func:`extract_facts` parses a rendered summary back
  into a normalized fact object, so the in-memory renderer and a completed-run
  read-back can be compared without depending on byte identity.

Security (plan sections 13.4 and 22): Markdown metacharacters are escaped,
control and bidirectional characters are rendered visibly, and no absolute host
path is emitted. Artifact references are run-relative.
"""

from __future__ import annotations

from modules.subject import subject_key_of
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

NON_AUTHORITATIVE_NOTICE = (
    "This file is a human-readable projection and is **not authoritative**. "
    "Final run integrity is recorded in `run_status.json`; authoritative "
    "repository results are in `analysis.json`. This summary reports the "
    "finalized measurement facts available at render time."
)

CROSS_LANGUAGE_LIMITATION = (
    "The four metrics share names and aggregation rules, but their raw entity "
    "counts are not perfectly measurement-equivalent across languages. "
    "Interfaces are excluded from Classes / Structs in every language; Go counts "
    "named module-scope structs; Python counts nested and function-local classes. "
    "Raw counts alone must not be interpreted as cross-language "
    "architecture-quality measures."
)

ARCHITECTURE_LABEL_DISCLAIMER = (
    "`architecture_type` is **supplied input metadata**, not an inferred or "
    "verified property. Metrolith does not classify architecture and does not "
    "treat any reference architecture as absolute ground truth."
)

METRIC_STATUS_FIELDS = (
    "inventory_status",
    "source_files_status",
    "loc_status",
    "classes_structs_status",
    "methods_functions_status",
)

_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>])")


def visible_text(value: Any) -> str:
    """Render control and bidirectional characters visibly, escaping nothing else.

    Split out of :func:`escape_markdown` so that a renderer emitting a *code
    span* can get the safety half without the escaping half. Inside a code span
    Markdown metacharacters are already literal, and backslash-escaping them
    there corrupts the text a reader sees — `tree\\(base\\)` instead of
    `tree(base)`. One implementation of the control-character rule, two callers.
    """
    text = "" if value is None else str(value)
    out: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}:
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    return "".join(out)


def escape_markdown(value: Any) -> str:
    """Escape Markdown metacharacters and render control/bidi visibly.

    A repository URL or error message is untrusted text. Left raw, a crafted
    value could inject table rows, close a code fence, or use a bidirectional
    override to reorder what a reviewer sees.
    """
    return _MARKDOWN_SPECIALS.sub(r"\\\1", visible_text(value))


def format_value(value: Any) -> str:
    """Render one value, keeping absence, booleans, and zero distinct."""
    from modules.presentation import scalar

    return escape_markdown(scalar(value, absent="unavailable"))


def _subject_name(value: Mapping[str, Any]) -> str:
    from modules.presentation import subject_display

    return subject_display(value).name


def _raw_detail(value: Any) -> str:
    """Render a raw detail without leaking Python/null presentation tokens."""
    from modules.presentation import scalar

    return scalar(value, absent="not recorded")


def _status_distribution_entry(name: str, count: int, denominator: int) -> str:
    from modules.presentation import status as display_status

    raw = None if name == "null" else name
    return (
        f"{escape_markdown(display_status(raw, absent='unavailable'))}="
        f"{count}/{denominator} (raw: `{escape_markdown(_raw_detail(raw))}`)"
    )


def _counter(values: Iterable[Any]) -> dict[str, int]:
    found: dict[str, int] = {}
    for value in values:
        key = "null" if value is None else str(value)
        found[key] = found.get(key, 0) + 1
    return dict(sorted(found.items()))


def measurement_outcome(results: Sequence[Mapping[str, Any]]) -> str:
    """Aggregate measurement availability, derived from repository results only.

    Plan section 4.5: this must not be changed by failure to write an optional
    projection. It answers "how much was measured", not "did every file get
    written", which is what ``run_integrity_status`` answers.
    """
    if not results:
        return "not_evaluable"
    statuses = {
        str(item.get("analysis_status") or item.get("metrics", {})
            .get("aggregate", {}).get("metric_status") or "failed")
        for item in results
    }
    if statuses == {"complete"}:
        return "complete"
    if statuses <= {"failed"}:
        return "failed"
    return "partial"


def _metric_completeness(results: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, int]]]:
    rows = []
    for field in METRIC_STATUS_FIELDS:
        rows.append((
            field,
            _counter(
                item.get("metrics", {}).get("aggregate", {}).get(field)
                for item in results
            ),
        ))
    return rows


def _complexity_block(
    results: Sequence[Mapping[str, Any]], denominator: int
) -> list[str]:
    """The approved eleven aggregates per repository, with the state beside them.

    Every figure is rendered through `complexity_view`, so an unavailable value
    reads `unavailable` here exactly as it does in the report and in `explain`.
    A repository that predates Complexity Contract 1.0.0 is reported as
    **absent**, which is not the same fact as a null and not the same fact as a
    measured zero.
    """
    from modules import complexity_view

    lines: list[str] = []
    states = _counter(complexity_view.state_of(item) for item in results)
    lines.append("Measurement state distribution:")
    lines.append("")
    for name, count in states.items():
        meaning = complexity_view.STATE_MEANINGS.get(name, "")
        lines.append(
            f"- {escape_markdown(name)}: {count}/{denominator}"
            + (f" — {escape_markdown(meaning)}" if meaning else "")
        )

    evaluable = [
        item for item in results
        if complexity_view.state_of(item) in complexity_view.EVALUABLE_STATES
    ]
    if not evaluable:
        lines.append("")
        lines.append(
            "No repository in this run carries an evaluable complexity "
            "measurement, so no aggregate is reported. An absent measurement is "
            "never rendered as zero."
        )
        lines.append("")
        lines.append(complexity_view.DESCRIPTIVE_ONLY)
        return lines

    header = " | ".join(label for _f, label, _d in complexity_view.AGGREGATE_DEFINITIONS)
    lines.append("")
    lines.append(f"| Repository | State | {header} |")
    lines.append("|---|---|" + "---:|" * len(complexity_view.AGGREGATE_DEFINITIONS))
    for item in results:
        view = complexity_view.presentation(item)
        cells = " | ".join(
            escape_markdown(row["rendered"]) for row in view["aggregate"]
        )
        lines.append(
            f"| {escape_markdown(_subject_name(item))} "
            f"| {escape_markdown(view['state'])} | {cells} |"
        )

    lines.append("")
    lines.append(
        "Complexity Contract: "
        + format_value(next(
            (complexity_view.presentation(item)["complexity_contract_version"]
             for item in evaluable), None
        ))
    )
    lines.append("")
    lines.append(complexity_view.DESCRIPTIVE_ONLY)
    lines.append("")
    lines.append(complexity_view.COVERAGE_LIMITATION)
    lines.append("")
    lines.append(complexity_view.CROSS_LANGUAGE_LIMITATION)
    lines.append("")
    lines.append(
        "Every median above is the **lower** median — with an even population "
        "the lower of the two middle values, so the figure is always one a "
        "measured callable actually exhibits."
    )
    return lines


def _callable_rows(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The in-memory callable records for one repository result."""
    records = (result.get("metrics") or {}).get("callable_records") or ()
    return [record for record in records if isinstance(record, Mapping)]


def _cognitive_block(
    results: Sequence[Mapping[str, Any]], denominator: int
) -> list[str]:
    """Section 9c — Metrolith Cognitive Complexity.

    The aggregates are derived from the callable rows through
    `complexity_view`, so this section and the report and `explain` cannot
    disagree about what a figure means.

    **Absent, unavailable and measured zero stay three different facts.** A 1.9
    run reports `absent` — it never attempted cognitive measurement — while a
    failed one reports `failed`, and a repository whose callables genuinely
    score 0 reports `0`. None of the three is rendered like another.
    """
    from modules import complexity_view

    lines: list[str] = []
    states = _counter(complexity_view.cognitive_state_of(item) for item in results)
    lines.append(complexity_view.COGNITIVE_NAMING_STATEMENT)
    lines.append("")
    lines.append("Measurement state distribution:")
    lines.append("")
    for name, count in states.items():
        meaning = complexity_view.COGNITIVE_STATE_MEANINGS.get(name, "")
        lines.append(
            f"- {escape_markdown(name)}: {count}/{denominator}"
            + (f" — {escape_markdown(meaning)}" if meaning else "")
        )

    evaluable = [
        item for item in results
        if complexity_view.cognitive_state_of(item)
        in complexity_view.COGNITIVE_EVALUABLE_STATES
    ]
    if not evaluable:
        lines.append("")
        lines.append(
            "No repository in this run carries an evaluable cognitive "
            "measurement, so no aggregate is reported. An absent measurement is "
            "never rendered as zero."
        )
        lines.append("")
        lines.append(complexity_view.DESCRIPTIVE_ONLY)
        return lines

    header = " | ".join(
        label for _f, label, _d in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS
    )
    lines.append("")
    lines.append(f"| Repository | State | {header} |")
    lines.append(
        "|---|---|" + "---:|" * len(complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS)
    )
    for item in results:
        view = complexity_view.cognitive_presentation(item, _callable_rows(item))
        cells = " | ".join(
            escape_markdown(row["rendered"]) for row in view["aggregate"]
        )
        lines.append(
            f"| {escape_markdown(_subject_name(item))} "
            f"| {escape_markdown(view['state'])} | {cells} |"
        )

    lines.append("")
    lines.append(
        "Complexity Contract: "
        + format_value(next(
            (complexity_view.cognitive_presentation(item)["complexity_contract_version"]
             for item in evaluable), None
        ))
    )
    lines.append("")
    lines.append(complexity_view.COGNITIVE_ZERO_STATEMENT)
    lines.append("")
    lines.append(complexity_view.COGNITIVE_COVERAGE_LIMITATION)
    lines.append("")
    lines.append(complexity_view.DESCRIPTIVE_ONLY)
    return lines


def _distribution_block(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Section 9e — complexity distributions.

    The shape of the measured callable population, read from the same rows the
    approved aggregates are derived from. Nothing here recomputes a metric.

    Percentiles are nearest-rank, so every figure is a value some callable
    actually exhibits, and `p50` is by construction the lower median already
    reported in section 9b. The frequency tables are truncated for width — the
    withheld count is stated, never dropped silently.
    """
    from modules import complexity_distribution

    lines: list[str] = []
    evaluable = [
        item for item in results
        if complexity_distribution.presentation(item)["evaluable"]
    ]
    if not evaluable:
        lines.append(
            "No repository in this run carries an evaluable complexity "
            "measurement, so no distribution is reported. An unavailable "
            "measurement has no shape and is not rendered as an empty one."
        )
        lines.append("")
        lines.append(complexity_distribution.DESCRIPTIVE_ONLY)
        return lines

    lines.append(complexity_distribution.PERCENTILE_METHOD)
    lines.append("")
    positions = " | ".join(
        f"p{position}" for position in complexity_distribution.PERCENTILE_POSITIONS
    )
    lines.append(f"| Repository | Metric | Measured | Min | {positions} | Max | Distinct |")
    lines.append("|---|---|---:|---:|" + "---:|" * len(
        complexity_distribution.PERCENTILE_POSITIONS
    ) + "---:|---:|")
    for item in results:
        view = complexity_distribution.presentation(item, _callable_rows(item))
        if not view["evaluable"]:
            continue
        for entry in view["combined"]["distributions"]:
            percentiles = " | ".join(
                escape_markdown(entry["rendered"][f"p{position}"])
                for position in complexity_distribution.PERCENTILE_POSITIONS
            )
            lines.append(
                f"| {escape_markdown(_subject_name(item))} "
                f"| {escape_markdown(entry['label'])} "
                f"| {entry['measured_count']} "
                f"| {escape_markdown(entry['rendered']['min'])} | {percentiles} "
                f"| {escape_markdown(entry['rendered']['max'])} "
                f"| {entry['distinct_values']} |"
            )

    lines.append("")
    lines.append(complexity_distribution.NO_BUCKETS_STATEMENT)
    lines.append("")
    lines.append(complexity_distribution.COVERAGE_LIMITATION)
    lines.append("")
    lines.append(complexity_distribution.CROSS_LANGUAGE_LIMITATION)
    lines.append("")
    lines.append(complexity_distribution.DESCRIPTIVE_ONLY)
    return lines


def _composition_block(
    results: Sequence[Mapping[str, Any]], denominator: int
) -> list[str]:
    """Section 9d — source line composition.

    The counts are read from `metrics.aggregate`, where they have been recorded
    since Artifact Schema 1.3.0; nothing here classifies a line. Every figure is
    rendered through `source_composition`, so a `failed` measurement reads
    `unavailable` here exactly as it does in the report — and never as the `0`
    that the accumulated record may literally contain.

    The ratios are physical-line shares with their denominators named. They are
    not documentation, maintainability or quality measures, and the section says
    so rather than leaving a reader to assume otherwise.
    """
    from modules import source_composition

    lines: list[str] = []
    states = source_composition.state_distribution(results)
    lines.append("Measurement state distribution:")
    lines.append("")
    for name, count in states.items():
        meaning = source_composition.STATE_MEANINGS.get(name, "")
        lines.append(
            f"- {escape_markdown(name)}: {count}/{denominator}"
            + (f" — {escape_markdown(meaning)}" if meaning else "")
        )

    evaluable = source_composition.evaluable_results(results)
    if not evaluable:
        lines.append("")
        lines.append(
            "No repository in this run carries an evaluable source-composition "
            "measurement, so no count is reported. An unavailable measurement "
            "is never rendered as zero."
        )
        lines.append("")
        lines.append(source_composition.DESCRIPTIVE_ONLY)
        return lines

    count_header = " | ".join(
        label for _f, label, _d in source_composition.COMPOSITION_DEFINITIONS
    )
    ratio_header = " | ".join(
        label for _n, label, _num, _den, _d in source_composition.RATIO_DEFINITIONS
    )
    lines.append("")
    lines.append(f"| Repository | State | {count_header} | {ratio_header} |")
    lines.append(
        "|---|---|"
        + "---:|" * len(source_composition.COMPOSITION_DEFINITIONS)
        + "---:|" * len(source_composition.RATIO_DEFINITIONS)
    )
    for item in results:
        view = source_composition.presentation(item)
        cells = " | ".join(
            escape_markdown(row["rendered"])
            for row in (*view["counts"], *view["ratios"])
        )
        lines.append(
            f"| {escape_markdown(_subject_name(item))} "
            f"| {escape_markdown(view['state'])} | {cells} |"
        )

    lines.append("")
    lines.append("Ratio denominators, stated so no figure is read against the wrong base:")
    lines.append("")
    for name, label, _numerator, denominator_field, _definition in (
        source_composition.RATIO_DEFINITIONS
    ):
        lines.append(
            f"- {escape_markdown(label)} (`{escape_markdown(name)}`): "
            f"denominator is `{escape_markdown(denominator_field)}`; null when "
            "that denominator is zero, and never 0."
        )

    lines.append("")
    lines.append(source_composition.ZERO_STATEMENT)
    lines.append("")
    lines.append(source_composition.DESCRIPTIVE_ONLY)
    lines.append("")
    lines.append(source_composition.CROSS_LANGUAGE_LIMITATION)
    lines.append("")
    lines.append(
        "`code_lines` is the same figure republished elsewhere as "
        "`lines_of_code`. There is one LOC number in Metrolith, not two."
    )
    return lines


def _qualification_section(
    manifest: Mapping[str, Any],
    denominator: int,
    records: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Report the four dimensions separately, never as one number.

    Measurement completeness, repository representativeness, benchmark
    usability and benchmark-of-record readiness answer four different
    questions, and a summary that merges any two of them is how "92 complete"
    silently becomes "92 usable for repository-level comparison". The counts
    below are read from the qualification authority the run actually published;
    nothing here re-derives a verdict.
    """
    mode = str(manifest.get("qualification_mode") or "not_requested")
    readiness = manifest.get("benchmark_of_record_readiness") or {}
    lines = ["", "## 1b. Benchmark qualification and readiness", ""]
    lines.append(f"- Qualification mode: {format_value(mode)}")

    if mode != "benchmark_qualified":
        lines.append(
            "- This is a generic Metrolith analysis. It carries no qualification "
            "artifact, and its measurements are neither adjudicated "
            "representative nor admitted to any benchmark cohort. Absence of "
            "qualification never means ADEQUATE."
        )
    else:
        registry = manifest.get("qualification_registry") or {}
        binding = manifest.get("benchmark_qualification_artifact") or {}
        lines.append(f"- Qualification profile: {format_value(registry.get('qualification_profile'))}")
        lines.append(f"- Registry source: {format_value(registry.get('source_id'))}")
        lines.append(f"- Registry SHA-256: {format_value(registry.get('registry_sha256'))}")
        lines.append(f"- Qualification artifact SHA-256: {format_value(binding.get('sha256'))}")
        lines.append(
            f"- Adjudicated records: {format_value(registry.get('exact_match_count'))}"
            f" of {denominator}"
        )
        lines.append(
            f"- Unresolved records: {format_value(registry.get('unresolved_count'))}"
        )
        lines.append(
            f"- Registry records matching no executed subject: "
            f"{format_value(registry.get('unused_record_count'))}"
        )

        # The two dimensions reported separately, and labelled so that neither
        # can be read as the other. `metric_status` distribution lives in its
        # own section and is deliberately not repeated here.
        representativeness = Counter(
            str(record.get("repository_representativeness")) for record in records
        )
        usability = Counter(str(record.get("benchmark_usability")) for record in records)
        eligible = sum(
            1
            for record in records
            if record.get("repository_level_comparison_eligible") is True
        )

        lines.extend(["", "Repository representativeness (independent of measurement completeness):", ""])
        for value in ("ADEQUATE", "MATERIAL_MIX", "NON_REPRESENTATIVE", "UNRESOLVED"):
            lines.append(f"- {value}: {representativeness.get(value, 0)} of {denominator}")

        lines.extend(["", "Benchmark usability (derived):", ""])
        for value in (
            "repository_level_usable",
            "analyzed_scope_only",
            "partial_but_usable",
            "unsupported_scope",
        ):
            lines.append(f"- {value}: {usability.get(value, 0)} of {denominator}")
        unavailable = usability.get("None", 0)
        if unavailable:
            lines.append(f"- not admissible (measurement failed): {unavailable}")

        lines.extend(["", f"- Repository-level comparison eligible: {eligible} of {denominator}", ""])
        lines.append(
            "`repository_level_metrics.csv` holds exactly those eligible rows. "
            "`sheet_metrics.csv` remains the complete measured population and is "
            "not an unrestricted repository-level cohort."
        )

    lines.append("")
    lines.append(
        f"- Benchmark-of-record readiness: "
        f"{format_value(readiness.get('status') or 'NOT_READY')}"
    )
    blockers = list(readiness.get("blockers") or ())
    if blockers:
        lines.append("- Readiness blockers:")
        lines.extend(f"  - {escape_markdown(str(item))}" for item in blockers)
    else:
        lines.append("- Readiness blockers: none")
    lines.append("")
    lines.append(
        "Readiness is independent of metric correctness. A run may hold entirely "
        "valid measurements and still be NOT_READY, and READY proves no metric "
        "value correct."
    )
    return lines


def _render_summary_legacy(
    manifest: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    *,
    outcome: str,
    integrity_status: str,
    normalization: Any = None,
    qualification_records: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Render ``summary.md`` from one immutable in-memory view.

    Deterministic: the same inputs always produce the same bytes.

    ``qualification_records`` is empty for a generic run, which is the correct
    representation of "no qualification was requested" rather than a stand-in
    for an absent one.
    """
    ordered = sorted(results, key=lambda item: subject_key_of(item).casefold())
    denominator = len(ordered)
    lines: list[str] = []

    def head(title: str) -> None:
        lines.extend(["", f"## {title}", ""])

    lines.append("# Metrolith run summary")
    lines.append("")
    lines.append(NON_AUTHORITATIVE_NOTICE)

    # 1. Run integrity and measurement outcome
    head("1. Run integrity and measurement outcome")
    lines.append(f"- Run ID: {format_value(manifest.get('run_id'))}")
    lines.append(f"- Run integrity status: {format_value(integrity_status)}")
    lines.append(f"- Measurement outcome: {format_value(outcome)}")
    failures = manifest.get("output_failures") or {}
    lines.append(f"- Mandatory output failures: {len(failures.get('mandatory', []))}")
    lines.append(f"- Optional output failures: {len(failures.get('optional', []))}")
    lines.append(
        "- Note: measurement outcome is derived from repository results and is "
        "not changed by failure to write an optional projection."
    )

    lines.extend(
        _qualification_section(manifest, denominator, qualification_records)
    )

    # 2. Provenance warnings
    head("2. Provenance warnings")
    provenance: list[str] = []
    # Read from the same place `modules.diagnostics` reads it. The manifest
    # mirrors these fields at the top level, but `benchmark_environment` is what
    # is serialized to `environment.json` and therefore what `explain`, the HTML
    # report, and `reproduce` all consult. Two sources for one warning is a
    # divergence waiting to happen.
    environment = manifest.get("benchmark_environment") or manifest
    if environment.get("profiler_git_commit_sha") is None:
        provenance.append(
            "profiler Git commit SHA is null; this run has no immutable revision "
            "and is not benchmark-of-record ready"
        )
    if environment.get("profiler_git_dirty"):
        provenance.append("profiler working tree was dirty at run time")
    lines.extend(f"- {escape_markdown(item)}" for item in provenance)
    if not provenance:
        lines.append("- None recorded")

    # 3. Complete input / planned / processed population
    head("3. Input population")
    lines.append(f"- Accepted source rows: {format_value(manifest.get('input_row_count'))}")
    lines.append(f"- Enabled / planned: {format_value(manifest.get('planned_repository_count'))}")
    lines.append(f"- Processed: {format_value(manifest.get('processed_repository_count'))}")
    lines.append(f"- Skipped because disabled: {format_value(manifest.get('skipped_disabled_count'))}")
    lines.append(
        f"- Identical duplicates dropped from execution: "
        f"{format_value(manifest.get('duplicate_rows_dropped'))}"
    )
    if normalization is not None:
        lines.append(
            f"- Complete accepted population retained in `normalized_input.csv`: "
            f"{normalization.accepted_row_count} row(s)"
        )
    else:
        lines.append(
            "- `normalized_input.csv` was not produced; the complete accepted "
            "population is not evaluable from this run"
        )

    # 4. Per-metric completeness with denominators
    head("4. Per-metric completeness")
    lines.append(f"Denominator: {denominator} repository result(s).")
    lines.append("")
    lines.append("| Metric status field | Distribution |")
    lines.append("|---|---|")
    for field, distribution in _metric_completeness(ordered):
        rendered = ", ".join(
            f"{escape_markdown(name)}={count}/{denominator}"
            for name, count in distribution.items()
        ) or "none recorded"
        lines.append(f"| {escape_markdown(field)} | {rendered} |")

    # 5. Expected-family status
    head("5. Expected language-family status")
    for name, count in _counter(
        item.get("expected_language_family_status") for item in ordered
    ).items():
        lines.append(f"- {escape_markdown(name)}: {count}/{denominator}")

    # 6. Partial-origin distribution
    head("6. Partial-origin distribution")
    for name, count in _counter(item.get("partial_origin") for item in ordered).items():
        lines.append(f"- {escape_markdown(name)}: {count}/{denominator}")

    # 7. Error and recovery evidence
    head("7. Error and recovery evidence")
    error_total = sum(len(item.get("errors") or []) for item in ordered)
    recovery_total = sum(
        len(item.get("metrics", {}).get("recovered_parser_diagnostics") or [])
        for item in ordered
    )
    lines.append(f"- Recorded errors: {error_total}")
    lines.append(f"- Recorded parser recoveries: {recovery_total}")
    lines.append(
        "- Raw error previews are deliberately not reproduced here; see "
        "`errors.csv` and `recoveries.csv`."
    )

    # 8. Git mode-map summary
    head("8. Git mode-map summary")
    for name, count in _counter(
        item.get("git_mode_map_status") for item in ordered
    ).items():
        lines.append(f"- {escape_markdown(name)}: {count}/{denominator}")

    # 9. Runtime observations
    head("9. Runtime observations")
    lines.append(f"- Run started: {format_value(manifest.get('start_timestamp'))}")
    lines.append(
        f"- Measurement finished: {format_value(manifest.get('measurement_finished_at'))}"
    )
    # Finalization has not finished at render time by construction: the summary
    # is written at step 7 and finalization completes at step 9 (plan 13.1).
    finalization = manifest.get("finalization_finished_at")
    lines.append(
        "- Finalization finished: "
        + (format_value(finalization) if finalization
           else "not yet recorded at summary render time; see `run_status.json`")
    )
    workers = manifest.get("workers")
    if workers is None:
        workers = (manifest.get("effective_configuration") or {}).get("workers")
    lines.append(f"- Workers: {format_value(workers)}")
    lines.append(
        "- Runtime observations are excluded from semantic run equality and from "
        "the semantic hash."
    )

    # 9b. Callable complexity. Numbered outside the existing sequence on
    # purpose: renumbering sections 10-15 would look like a content change to
    # every reader and every reader's tooling, and the summary's own contract is
    # fact equivalence, not layout.
    head("9b. Callable complexity")
    lines.extend(_complexity_block(ordered, denominator))

    # 9c. Cognitive complexity. Numbered beside 9b for the same reason 9b sits
    # outside the sequence, and kept a SEPARATE section because it is a
    # separate metric under a separate contract section with its own
    # measurement state -- folding it into 9b would let one `unavailable`
    # stand for two different measurements.
    head("9c. Cognitive complexity")
    lines.extend(_cognitive_block(ordered, denominator))

    # 9d. Source composition. Lettered beside 9b and 9c for the same reason
    # they sit outside the sequence, and kept SEPARATE from section 4 because
    # this reports recorded line counts rather than metric completeness. The
    # evidence is not new -- it has been in `metrics.aggregate` since Artifact
    # Schema 1.3.0 -- only its exposure is.
    head("9d. Source composition")
    lines.extend(_composition_block(ordered, denominator))

    # 9e. Complexity distributions. Lettered for the same reason as 9b-9d. It
    # follows 9b/9c deliberately: a reader meets the aggregates first and then
    # the shape they summarize, and `p50` here is the same lower median 9b
    # already reported.
    head("9e. Complexity distributions")
    # No denominator argument: unlike 9b-9d this section reports no per-state
    # count over the cohort. The complexity measurement-state distribution is
    # already reported once, in 9b, and repeating it here would invite a reader
    # to treat two renderings of one fact as two facts.
    lines.extend(_distribution_block(ordered))

    # 10-11. Limitations and disclaimers
    head("10. Cross-language comparability limitations")
    lines.append(CROSS_LANGUAGE_LIMITATION)

    head("11. Supplied architecture-label disclaimer")
    lines.append(ARCHITECTURE_LABEL_DISCLAIMER)

    # 12. Version and contract boundary
    head("12. Version and contract boundary")
    for label, key in (
        ("Metrolith program", "program_version"),
        ("Metric Contract", "metric_contract_version"),
        ("Exclusion Policy", "exclusion_policy_version"),
        ("Inventory Schema", "inventory_schema_version"),
        ("Artifact Schema", "artifact_schema_version"),
        # Separate from the Metric Contract on purpose: complexity versions
        # independently, so a complexity change never invalidates a benchmark
        # metric comparison and vice versa.
        ("Complexity Contract", "complexity_contract_version"),
    ):
        lines.append(f"- {label}: {format_value(manifest.get(key))}")

    # 13. Relative artifact paths
    head("13. Artifacts")
    lines.append("Paths are relative to this run directory.")
    lines.append("")
    for relative in (
        "run_manifest.json", "run_status.json", "analysis.json", "environment.json",
        "sheet_metrics.csv", "language_metrics.csv", "catalog.csv",
        "errors.csv", "recoveries.csv", "normalized_input.csv",
        "repositories_frozen.csv", "retry_failed_or_partial.csv",
        "callables.csv", "callables/",
        "file_inventory/", "logs/run.jsonl",
    ):
        lines.append(f"- `{relative}`")

    # 14. Limitations
    head("14. Limitations")
    lines.append("- This summary is non-authoritative and must not be parsed for measurement.")
    lines.append("- Metric values are static observations; no dynamic behaviour is measured.")
    lines.append(
        "- A partial numeric value is an observation, not a verified complete value."
    )
    lines.append("- A null value means unavailable, and is never equivalent to zero.")

    # 15. Appendix: repository table
    head("15. Appendix: repository results")
    lines.append(
        "| Repository | Recorded status | Expected-family status | Partial origin "
        "| LOC | Source Files | Classes / Structs | Methods / Functions |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|")
    for item in ordered:
        aggregate = item.get("metrics", {}).get("aggregate", {})
        lines.append(
            "| {url} | {status} | {family} | {origin} | {loc} | {files} | {cls} | {met} |".format(
                url=escape_markdown(item.get("repository_url")),
                status=format_value(item.get("analysis_status")),
                family=format_value(item.get("expected_language_family_status")),
                origin=format_value(item.get("partial_origin")),
                loc=format_value(aggregate.get("lines_of_code")),
                files=format_value(aggregate.get("source_files")),
                cls=format_value(aggregate.get("classes_structs")),
                met=format_value(aggregate.get("methods_functions")),
            )
        )

    return "\n".join(lines) + "\n"


def render_summary(
    manifest: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    *,
    outcome: str,
    integrity_status: str,
    normalization: Any = None,
    qualification_records: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Render the deterministic, result-first human summary.

    Every value is projected from the same manifest/results accepted by the
    historical renderer; only information hierarchy and human vocabulary move.
    """

    from modules.presentation import (
        digest_prefix,
        measurement,
        scalar,
        status as display_status,
        subject_display,
    )

    ordered = sorted(results, key=lambda item: subject_key_of(item).casefold())
    denominator = len(ordered)
    lines: list[str] = [
        "# Metrolith run summary",
        "",
        NON_AUTHORITATIVE_NOTICE,
        "",
        "## 1. Result / measured values",
        "",
        f"- Final run state: {escape_markdown(display_status(integrity_status))}",
        f"- Measurement outcome: {escape_markdown(display_status(outcome))}",
        f"- Run ID: {escape_markdown(scalar(manifest.get('run_id')))}",
        "",
        "Descriptive evidence, not a quality verdict.",
        "",
        "| Subject | Subject key | Revision | Result | Lines of Code | Source Files | Classes / Structs | Methods / Functions |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for item in ordered:
        identity = subject_display(item)
        aggregate = (item.get("metrics") or {}).get("aggregate") or {}
        revision = (item.get("acquisition") or {}).get("analyzed_commit_sha")
        if item.get("source_mode") == "local_worktree_snapshot":
            revision = f"Working files; HEAD reference: {revision or 'unavailable'}"
        elif item.get("source_mode") == "local_directory_snapshot":
            revision = "Directory snapshot (non-Git)"
        metric_cells = [
            measurement(aggregate.get(value_key), aggregate.get(status_key))
            + f" ({display_status(aggregate.get(status_key))})"
            for value_key, status_key in (
                ("lines_of_code", "loc_status"),
                ("source_files", "source_files_status"),
                ("classes_structs", "classes_structs_status"),
                ("methods_functions", "methods_functions_status"),
            )
        ]
        lines.append(
            "| {name} | {key} | {revision} | {result} | {metrics} |".format(
                name=escape_markdown(identity.name),
                key=escape_markdown(identity.subject_key),
                revision=escape_markdown(scalar(revision, absent="not applicable")),
                result=escape_markdown(display_status(item.get("analysis_status"))),
                metrics=" | ".join(escape_markdown(value) for value in metric_cells),
            )
        )
    if not ordered:
        lines.append("| No subject result | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |")

    from modules.presentation import recognized_source_count
    source_counts = [recognized_source_count((item.get("metrics") or {}).get("aggregate") or {}) for item in ordered]
    recognized = sum(value for value in source_counts if value is not None)
    examined = bool(ordered) and all(value is not None for value in source_counts)
    if recognized == 0 and examined:
        lines.extend(["", "**No recognized source files were found.**"])
    elif not examined:
        lines.extend(["", "**Source measurement is unavailable for one or more subjects; inspect the recorded diagnostics.**"])

    lines.extend(["", "## 2. Attention and important limitations", ""])
    attention = [
        item for item in ordered
        if item.get("analysis_status") in {"partial", "failed"}
    ]
    if attention:
        for item in attention:
            identity = subject_display(item)
            lines.append(
                f"- {escape_markdown(identity.name)}: "
                f"{escape_markdown(display_status(item.get('analysis_status')))}; "
                f"raw status `{visible_text(item.get('analysis_status'))}`"
            )
    else:
        lines.append("- No partial or failed subject result recorded.")
    lines.extend(["", CROSS_LANGUAGE_LIMITATION, "", ARCHITECTURE_LABEL_DISCLAIMER])

    lines.extend(["", "## 3. Completeness and unavailable state", ""])
    lines.append(f"Denominator: {denominator} repository result(s).")
    lines.extend(["", "| Metric status field | Distribution |", "|---|---|"])
    for field, distribution in _metric_completeness(ordered):
        rendered = ", ".join(
            _status_distribution_entry(name, count, denominator)
            for name, count in distribution.items()
        ) or "none recorded"
        lines.append(f"| {escape_markdown(field)} | {rendered} |")
    lines.extend([
        "",
        "Numeric zero is a measured value. Unavailable, not applicable, and not evaluable are separate states and are never converted to zero.",
    ])

    lines.extend(["", "## 4. Diagnostics / exclusions", ""])
    error_total = sum(len(item.get("errors") or []) for item in ordered)
    recovery_total = sum(
        len((item.get("metrics") or {}).get("recovered_parser_diagnostics") or [])
        for item in ordered
    )
    lines.extend([
        f"- Recorded errors: {error_total}",
        f"- Recorded parser recoveries: {recovery_total}",
        "- Raw error previews are deliberately not reproduced here; see `errors.csv` and `recoveries.csv`.",
    ])
    for name, count in _counter(item.get("git_mode_map_status") for item in ordered).items():
        lines.append(f"- Git mode-map: {_status_distribution_entry(name, count, denominator)}")

    lines.extend(["", "## 5. Provenance, contracts, and reproducibility", ""])
    environment = manifest.get("benchmark_environment") or manifest
    lines.extend([
        f"- Metrolith evaluator provenance: {escape_markdown(scalar(environment.get('profiler_provenance_kind'), absent='legacy / not supplied'))}",
        f"- Evaluator Git state: {escape_markdown(scalar(environment.get('profiler_git_state'), absent='legacy / not supplied'))}",
        f"- Evaluator Git revision: {escape_markdown(scalar(environment.get('profiler_git_commit_sha'), absent='not applicable'))}",
        f"- Installed evaluator source digest: {escape_markdown(digest_prefix(environment.get('profiler_source_sha256')))}",
    ])
    for warning in manifest.get("provenance_warnings") or ():
        lines.append(f"- Warning: {escape_markdown(warning)}")
    for label, key in (
        ("Metrolith program", "program_version"),
        ("Metric Contract", "metric_contract_version"),
        ("Complexity Contract", "complexity_contract_version"),
        ("Exclusion Policy", "exclusion_policy_version"),
        ("Inventory Schema", "inventory_schema_version"),
        ("Artifact Schema", "artifact_schema_version"),
    ):
        lines.append(f"- {label}: {escape_markdown(scalar(manifest.get(key)))}")
    lines.extend([
        f"- Run started: {escape_markdown(scalar(manifest.get('start_timestamp')))}",
        f"- Measurement finished: {escape_markdown(scalar(manifest.get('measurement_finished_at')))}",
        "- Finalization status and time are authoritative in `run_status.json` and `run_manifest.json`.",
    ])
    if manifest.get("qualification_mode") == "benchmark_qualified":
        lines.extend(_qualification_section(manifest, denominator, qualification_records))

    lines.extend(["", "## 6. Appendix", ""])
    lines.append("The remaining sections retain detailed evidence and definitions for review and reproduction.")

    lines.extend(["", "### Input population", ""])
    lines.extend([
        f"- Accepted source rows: {format_value(manifest.get('input_row_count'))}",
        f"- Enabled / planned: {format_value(manifest.get('planned_repository_count'))}",
        f"- Processed: {format_value(manifest.get('processed_repository_count'))}",
        f"- Skipped because disabled: {format_value(manifest.get('skipped_disabled_count'))}",
        f"- Identical duplicates dropped from execution: {format_value(manifest.get('duplicate_rows_dropped'))}",
    ])
    if normalization is not None:
        lines.append(
            f"- Complete accepted population retained in `normalized_input.csv`: {normalization.accepted_row_count} row(s)"
        )
    else:
        lines.append("- Complete normalized input population: unavailable")

    lines.extend(["", "### Expected language-family status", ""])
    for name, count in _counter(item.get("expected_language_family_status") for item in ordered).items():
        lines.append(f"- {_status_distribution_entry(name, count, denominator)}")

    lines.extend(["", "### Partial-origin distribution", ""])
    for name, count in _counter(item.get("partial_origin") for item in ordered).items():
        lines.append(f"- {_status_distribution_entry(name, count, denominator)}")

    lines.extend(["", "### Callable complexity", ""])
    lines.extend(_complexity_block(ordered, denominator))
    lines.extend(["", "### Cognitive complexity", ""])
    lines.extend(_cognitive_block(ordered, denominator))
    lines.extend(["", "### Source composition", ""])
    lines.extend(_composition_block(ordered, denominator))
    lines.extend(["", "### Complexity distributions", ""])
    lines.extend(_distribution_block(ordered))

    lines.extend(["", "### Artifact index", "", "Paths are relative to this run directory.", ""])
    for relative in (
        "run_manifest.json", "run_status.json", "analysis.json", "environment.json",
        "sheet_metrics.csv", "language_metrics.csv", "catalog.csv", "errors.csv",
        "recoveries.csv", "normalized_input.csv", "repositories_frozen.csv",
        "retry_failed_or_partial.csv", "callables.csv", "callables/",
        "file_inventory/", "logs/run.jsonl",
    ):
        lines.append(f"- `{relative}`")

    lines.extend(["", "### Repository details", ""])
    lines.append(
        "| Subject | Locator | Subject key | Raw result status | Raw expected-family status "
        "| Raw partial origin | LOC | Source Files | Classes / Structs | Methods / Functions |"
    )
    lines.append("|---|---|---|---|---|---|---:|---:|---:|---:|")
    for item in ordered:
        identity = subject_display(item)
        aggregate = (item.get("metrics") or {}).get("aggregate") or {}
        lines.append(
            f"| {escape_markdown(identity.name)} "
            f"| {escape_markdown(scalar(identity.repository_locator, absent='not supplied'))} "
            f"| {escape_markdown(identity.subject_key)} "
            f"| `{escape_markdown(_raw_detail(item.get('analysis_status')))}` "
            f"| `{escape_markdown(_raw_detail(item.get('expected_language_family_status')))}` "
            f"| `{escape_markdown(_raw_detail(item.get('partial_origin')))}` "
            f"| {format_value(aggregate.get('lines_of_code'))} "
            f"| {format_value(aggregate.get('source_files'))} "
            f"| {format_value(aggregate.get('classes_structs'))} "
            f"| {format_value(aggregate.get('methods_functions'))} |"
        )

    # Authored descriptive-boundary sentences from the detailed helpers are
    # intentionally shown once. Repository-derived strings are never deduped.
    final: list[str] = []
    seen_descriptive = False
    for line in lines:
        if "descriptive evidence" in line.casefold():
            if seen_descriptive:
                continue
            seen_descriptive = True
        final.append(line)
    return "\n".join(final) + "\n"


_FACT_PATTERNS = {
    "run_id": re.compile(r"^- Run ID: (.+)$", re.MULTILINE),
    "run_integrity_status": re.compile(
        r"^- (?:Run integrity status|Final run state): (.+)$", re.MULTILINE
    ),
    "measurement_outcome": re.compile(r"^- Measurement outcome: (.+)$", re.MULTILINE),
    "input_row_count": re.compile(r"^- Accepted source rows: (.+)$", re.MULTILINE),
    "planned_repository_count": re.compile(r"^- Enabled / planned: (.+)$", re.MULTILINE),
    "processed_repository_count": re.compile(r"^- Processed: (.+)$", re.MULTILINE),
    "recorded_errors": re.compile(r"^- Recorded errors: (.+)$", re.MULTILINE),
    "recorded_recoveries": re.compile(r"^- Recorded parser recoveries: (.+)$", re.MULTILINE),
    "program_version": re.compile(
        r"^- (?:Metrolith|ArchLens) program: (.+)$", re.MULTILINE
    ),
    "metric_contract_version": re.compile(r"^- Metric Contract: (.+)$", re.MULTILINE),
    "artifact_schema_version": re.compile(r"^- Artifact Schema: (.+)$", re.MULTILINE),
}


def extract_facts(text: str) -> dict[str, Any]:
    """Parse a rendered summary back into a normalized fact object.

    Plan section 13.2 defines renderer equivalence in terms of these facts rather
    than byte identity, so a formatting change does not look like a measurement
    change.
    """
    facts: dict[str, Any] = {}
    for name, pattern in _FACT_PATTERNS.items():
        match = pattern.search(text)
        facts[name] = match.group(1).strip() if match else None
    # The result-first renderer presents status tokens as readable phrases.
    # ``extract_facts`` remains a compatibility/round-trip surface, so project
    # that one human label back to the escaped canonical token it historically
    # returned.  Persisted machine artifacts are not changed.
    integrity = facts.get("run_integrity_status")
    if isinstance(integrity, str):
        facts["run_integrity_status"] = integrity.replace(" ", "\\_")
    facts["repository_rows"] = len(
        re.findall(r"^\| https?\\?:", text, re.MULTILINE)
    ) or len([
        line for line in text.splitlines()
        if line.startswith("| ") and "github" in line
    ])
    return facts
