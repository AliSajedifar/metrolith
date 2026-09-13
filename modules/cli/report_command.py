"""``metrolith report`` — optional static HTML report (plan section 17).

**Optional and non-authoritative.** Failure to produce it never invalidates a
measurement artifact.

Security is enforced by construction, not by a Content-Security-Policy meta tag
(plan section 17.4). There is no script, no external resource, no `src`, no
external-scheme `href`, and no CSS `@import` anywhere in the output, because the
renderer has no code path that can emit one: every value passes through
:func:`escape_html`, and the only stylesheet is a fixed inline literal. A CSP
alone would be a request to the viewer rather than a property of the file.

Raw error previews are deliberately not reproduced. Absolute host paths are
never written.
"""

from __future__ import annotations

from modules.subject import subject_key_of
import html
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
# Aligned with `explain`, `reproduce`, and `compare --explain`, which already use
# 3 for this. Reporting 0 here meant one run directory produced four different
# verdicts depending on which command was asked.
EXIT_INVALID_ARTIFACTS = 3

REPORT_FORMAT_VERSION = "1.0.0"

STYLESHEET = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 0 auto; max-width: 60rem; padding: 2rem 1rem; line-height: 1.55; }
h1, h2 { line-height: 1.25; }
h2 { margin-top: 2.5rem; border-bottom: 1px solid currentColor; padding-bottom: .25rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; display: block;
        overflow-x: auto; }
th, td { border: 1px solid #8888; padding: .4rem .6rem; text-align: left; }
th { font-weight: 600; }
td.numeric, th.numeric { text-align: right; font-variant-numeric: tabular-nums; }
.null { opacity: .6; font-style: italic; }
.notice { border-left: 4px solid #8888; padding: .5rem 1rem; margin: 1rem 0; }
.invalid { border: 2px solid currentColor; padding: .5rem 1rem; margin: 1rem 0;
           font-weight: 600; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; }
dt { font-weight: 600; }
dd { margin: 0; }
.skip-link { position: absolute; left: -10000px; top: auto; }
.skip-link:focus { left: 1rem; top: 1rem; background: Canvas; color: CanvasText;
                   padding: .5rem; z-index: 10; }
nav ul { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; padding-left: 1.25rem; }
"""


def add_parser(subparsers) -> None:
    report = subparsers.add_parser(
        "report",
        help="Render an optional static HTML report for one run",
        description=(
            "Render a non-authoritative static HTML report. Exit 0 = emitted (or "
            "invalid artifacts explicitly allowed), 1 = operational/rendering "
            "failure, 2 = invalid usage/overwrite refusal, and 3 = invalid run "
            "artifacts."
        ),
    )
    report.add_argument("run_directory", type=Path)
    report.add_argument("--output", type=Path, help="Destination file")
    report.add_argument(
        "--overwrite", action="store_true",
        help="Replace an existing file. Without this the command refuses.",
    )
    report.add_argument(
        "--allow-invalid-artifacts", action="store_true",
        help=(
            "Return 0 even when the run's artifacts do not parse. The report "
            "still carries the invalid-artifact banner."
        ),
    )


def handle(args) -> int:
    from modules.cli.export_output import admit, ExportOutputError
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    destination = Path(args.output) if args.output else Path(args.run_directory) / "report.html"
    try:
        admitted = admit(destination, overwrite=args.overwrite, runs=[args.run_directory], kind="report")
    except ExportOutputError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_USAGE
    if not Path(args.run_directory).is_dir():
        print(f"[ERROR] Run directory does not exist or is not a directory: {args.run_directory}. Check the path, then rerun.")
        return EXIT_ERROR

    try:
        view = open_run(args.run_directory)
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_ERROR

    destination = Path(args.output) if args.output else Path(args.run_directory) / "report.html"
    if destination.exists() and not args.overwrite:
        print(
            f"[ERROR] {destination} already exists. Pass --overwrite to replace it."
        )
        return EXIT_USAGE

    try:
        document = render_report(view)
    except ArtifactStructureError as exc:
        # Rendering reads lazily, so a malformed optional artifact surfaces here
        # rather than at open_run. Report it as a typed failure instead of an
        # uncaught traceback.
        print(f"[ERROR] {exc}")
        return EXIT_INVALID_ARTIFACTS

    try:
        admitted.write(document)
    except ExportOutputError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_USAGE
    written = len(document.encode("utf-8"))
    from modules.presentation import terminal_path, terminal_command

    shown_destination = terminal_path(destination)
    print(f"Wrote {written} byte(s) to {shown_destination}")

    problems = _invalid_artifact_problems(view)
    if problems:
        print(
            f"[INVALID ARTIFACTS] this run does not parse cleanly "
            f"({len(problems)} problem(s)); the report carries a banner:"
        )
        for problem in problems:
            print(f"  - {problem}")
        if not getattr(args, "allow_invalid_artifacts", False):
            return EXIT_INVALID_ARTIFACTS
        print("Exit code suppressed by --allow-invalid-artifacts.")
    print(f'Next: {terminal_command("explain", args.run_directory)}')
    return EXIT_OK


def _invalid_artifact_problems(view) -> list[str]:
    """Reasons this run's artifacts cannot be trusted, in reader terms."""
    from validation.artifact_io.compatibility import CompatibilityState

    problems = [str(error) for error in view.structural_errors]
    if view.compatibility.state is not CompatibilityState.SUPPORTED:
        problems.append(
            f"artifact schema {view.compatibility.state.value}: "
            f"{view.compatibility.reason}"
        )
    return problems


def _atomic_write(destination: Path, text: str) -> int:
    """Write via a temporary file and one rename, so no partial file is visible."""
    import os
    import tempfile

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(text.encode("utf-8"))


def escape_html(value: Any) -> str:
    """Escape for HTML text and attribute context, rendering control chars visibly.

    ``html.escape`` with ``quote=True`` covers ``& < > " '``. Control and
    bidirectional characters are additionally rendered as literal escapes, so a
    crafted repository name cannot reorder the rendered page.

    The return value is always **text**, never markup. It previously returned a
    ``<span>`` for ``None``, which contradicted the attribute-context promise in
    this docstring and rendered as visible tag text inside ``<title>``, where
    the content model is RCDATA. Styling a null belongs to the caller that knows
    it is emitting an element — see :func:`_cell`.
    """
    if value is None:
        return "not supplied"
    text = str(value)
    out: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}:
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    return html.escape("".join(out), quote=True)


def _cell(value: Any, *, numeric: bool = False) -> str:
    from modules.presentation import scalar

    classes = ' class="numeric"' if numeric else ""
    body = escape_html(scalar(value, absent="unavailable"))
    return f"<td{classes}>{body}</td>"


def _subject_name(value: Mapping[str, Any]) -> str:
    from modules.presentation import subject_display

    return subject_display(value).name


def _qualification_section(view, results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Qualification, and the unrestricted cohort derived from both authorities.

    The eligible set is derived HERE from `analysis.json` plus
    `benchmark_qualification.json`, and then checked against
    `repository_level_metrics.csv`. Reading the CSV and presenting it would make
    the report a renderer of one projection; deriving and reconciling is what
    lets a disagreement between the two surface as a disagreement.

    Excluded records are never dropped. They appear in a qualified section with
    their measurements numerically unchanged, because a restricted record is
    still a correct measurement of what it measured.
    """
    from modules.benchmark_qualification import subject_of

    mode = view.qualification_mode
    parts: list[str] = ["<h2>Benchmark qualification</h2>"]
    readiness = view.benchmark_of_record_readiness or {}

    if mode != "benchmark_qualified":
        parts.append(
            '<p class="notice">This run is a <strong>generic Metrolith '
            "analysis</strong>. It carries no qualification authority, so no "
            "row is adjudicated representative and none is admitted to a "
            "repository-level cohort. Absence of qualification never means "
            "<code>ADEQUATE</code>.</p>"
        )
        return parts

    artifact = view.benchmark_qualification
    if artifact is None:
        parts.append(
            '<div class="invalid"><p>This run declares benchmark-qualified '
            "mode but carries no qualification artifact. No cohort can be "
            "presented.</p></div>"
        )
        return parts

    records = {
        subject_of(record.get("binding") or {}): record
        for record in artifact.get("records") or ()
    }
    eligible, excluded = [], []
    for result in results:
        record = records.get(subject_of(result))
        if record is None:
            excluded.append((result, None))
        elif record.get("repository_level_comparison_eligible") is True:
            eligible.append((result, record))
        else:
            excluded.append((result, record))

    status = str(readiness.get("status") or "NOT_READY")
    parts.append("<dl class=\"facts\">")
    parts.append(f"<dt>Qualification profile</dt><dd>{escape_html(artifact.get('qualification_profile'))}</dd>")
    parts.append(f"<dt>Benchmark-of-record readiness</dt><dd>{escape_html(status)}</dd>")
    parts.append(f"<dt>Repository-level eligible</dt><dd>{len(eligible)} of {len(results)}</dd>")
    parts.append("</dl>")

    if status != "READY":
        parts.append(
            '<p class="notice"><strong>This run is not a benchmark of '
            "record.</strong> The cohort below satisfies the per-record "
            "eligibility predicate, but unrestricted benchmark-of-record use "
            "additionally requires run-level readiness, which this run does "
            "not have.</p>"
        )
        for blocker in readiness.get("blockers") or ():
            parts.append(f"<p class=\"notice\">Blocker: {escape_html(str(blocker))}</p>")

    parts.append("<h3>Unrestricted repository-level cohort</h3>")
    parts.append(
        "<p>Derived independently from the measurement and qualification "
        "authorities, then reconciled against "
        "<code>repository_level_metrics.csv</code>.</p>"
    )
    parts.append(_repository_table([result for result, _ in eligible]))

    # The reconciliation itself, reported rather than assumed.
    projected = {
        str(row.get("subject_key") or "") for row in view.repository_level_metrics
    }
    derived = {subject_of(result) for result, _ in eligible}
    if projected != derived:
        parts.append(
            '<div class="invalid"><p>The derived cohort does not match '
            "<code>repository_level_metrics.csv</code>. The projection and the "
            "authorities disagree, and neither may be used until that is "
            "resolved.</p></div>"
        )

    parts.append("<h3>Qualified exclusions</h3>")
    parts.append(
        "<p>Excluded from the unrestricted cohort. Their measurements are "
        "correct for the scope they measured and are shown unchanged.</p>"
    )
    parts.append("<table><thead><tr>")
    parts.append(
        "<th>Subject</th><th>Measurement</th><th>Representativeness</th>"
        "<th>Usability</th><th>Reason codes</th>"
    )
    parts.append("</tr></thead><tbody>")
    for result, record in excluded:
        aggregate = (result.get("metrics") or {}).get("aggregate") or {}
        codes = ", ".join(
            str(code) for code in (record or {}).get("usability_reason_codes") or ()
        )
        parts.append(
            "<tr>"
            f"{_cell(subject_of(result))}"
            f"{_cell(aggregate.get('metric_status'))}"
            f"{_cell((record or {}).get('repository_representativeness'))}"
            f"{_cell((record or {}).get('benchmark_usability'))}"
            f"{_cell(codes)}"
            "</tr>"
        )
    if not excluded:
        parts.append('<tr><td colspan="5">None</td></tr>')
    parts.append("</tbody></table>")
    parts.append(_repository_table([result for result, _ in excluded]))
    return parts


def render_report(view) -> str:
    """Render the whole report as one self-contained HTML document."""
    from modules.diagnostics import project_run
    from modules.summary import (
        ARCHITECTURE_LABEL_DISCLAIMER,
        CROSS_LANGUAGE_LIMITATION,
        measurement_outcome,
    )

    projection = project_run(view)
    results = [dict(item) for item in view.repositories]
    outcome = measurement_outcome(results)
    status = view.status
    manifest = view.manifest

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(
        f"<title>Metrolith run report — {escape_html(view.run_id)}</title>"
    )
    parts.append(f"<style>{STYLESHEET}</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<a class="skip-link" href="#main-content">Skip to results</a>')

    parts.append("<h1>Metrolith run report</h1>")
    parts.append('<nav aria-label="Report sections"><strong>Sections</strong><ul>')
    for target, label in (
        ("result", "Result"),
        ("attention", "Attention and limitations"),
        ("completeness", "Completeness"),
        ("diagnostics", "Diagnostics"),
        ("provenance", "Provenance and contracts"),
        ("appendix", "Appendix"),
    ):
        parts.append(f'<li><a href="#{target}">{label}</a></li>')
    parts.append("</ul></nav>")
    parts.append('<main id="main-content">')

    # A reader must not have to notice a single row in a definition list to
    # learn that the run does not parse. The banner is rendered before anything
    # that could be mistaken for a normal result.
    problems = _invalid_artifact_problems(view)
    if problems:
        parts.append('<div class="invalid">')
        parts.append(
            "<p><strong>Invalid artifacts.</strong> This run's artifacts do not "
            "parse cleanly, so the figures below are incomplete or unreliable "
            "and must not be read as a measurement result.</p>"
        )
        parts.append("<ul>")
        parts.extend(f"<li>{escape_html(item)}</li>" for item in problems)
        parts.append("</ul>")
        parts.append("</div>")

    parts.append(
        '<p class="notice">This report is an <strong>optional, '
        "non-authoritative</strong> presentation. Final run integrity is "
        "recorded in <code>run_status.json</code>; authoritative repository "
        "results are in <code>analysis.json</code>. Nothing here may be read "
        "back as measurement evidence.</p>"
    )

    # -- run identity and validation ---------------------------------------
    parts.append('<h2 id="result">Result / measured values</h2>')
    parts.append('<dl class="facts">')
    for label, value in (
        ("Run ID", view.run_id),
        ("Run integrity status", status.get("status")),
        ("Measurement outcome", outcome),
        ("Lifecycle", view.lifecycle.value),
        ("Artifact schema", manifest.get("artifact_schema_version")),
        ("Compatibility", view.compatibility.state.value),
        ("Report format", REPORT_FORMAT_VERSION),
    ):
        parts.append(f"<dt>{escape_html(label)}</dt><dd>{escape_html(value)}</dd>")
    parts.append("</dl>")
    parts.append("<p>Descriptive evidence, not a quality verdict.</p>")
    parts.append(_repository_table(results))
    from modules.presentation import recognized_source_count
    source_counts = [recognized_source_count((item.get("metrics") or {}).get("aggregate") or {}) for item in results]
    recognized = sum(value for value in source_counts if value is not None)
    examined = bool(results) and all(value is not None for value in source_counts)
    if recognized == 0 and examined:
        parts.append(
            '<p class="invalid">No recognized source files were found.</p>'
        )
    elif not examined:
        parts.append('<p class="invalid">Source measurement is unavailable for one or more subjects; inspect the recorded diagnostics.</p>')

    parts.append('<h2 id="attention">Attention and important limitations</h2>')
    attention = [
        item for item in results
        if item.get("analysis_status") in {"partial", "failed"}
    ]
    if attention:
        parts.append("<ul>")
        for item in attention:
            from modules.presentation import subject_display, status as display_status
            identity = subject_display(item)
            parts.append(
                f"<li>{escape_html(identity.name)}: "
                f"{escape_html(display_status(item.get('analysis_status')))} "
                f"(raw status <code>{escape_html(item.get('analysis_status'))}</code>)</li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<p>No partial or failed subject result recorded.</p>")
    parts.append(f"<p>{escape_html(CROSS_LANGUAGE_LIMITATION)}</p>")
    parts.append(f"<p>{escape_html(ARCHITECTURE_LABEL_DISCLAIMER)}</p>")

    # -- provenance ---------------------------------------------------------
    parts.append('<h2 id="provenance">Provenance, contracts, and reproducibility</h2>')
    environment = view.environment
    parts.append('<dl class="facts">')
    for label, value in (
        ("Metrolith evaluator provenance", environment.get("profiler_provenance_kind") or "legacy / not supplied"),
        ("Evaluator Git state", environment.get("profiler_git_state") or "legacy / not supplied"),
        ("Evaluator Git revision", environment.get("profiler_git_commit_sha") or "not applicable"),
        ("Installed evaluator source SHA-256", environment.get("profiler_source_sha256") or "not supplied"),
        ("Policy-gate trust", "not applicable — this report presents measurement, not a Policy gate"),
    ):
        parts.append(f"<dt>{escape_html(label)}</dt><dd>{escape_html(value)}</dd>")
    parts.append("</dl>")
    if projection.provenance_warnings:
        parts.append("<ul>")
        parts.extend(
            f"<li>{escape_html(item)}</li>" for item in projection.provenance_warnings
        )
        parts.append("</ul>")
    else:
        parts.append("<p>None recorded.</p>")

    # -- input population ---------------------------------------------------
    parts.append('<h2 id="appendix">Appendix: input population</h2>')
    parts.append('<dl class="facts">')
    for label, key in (
        ("Accepted source rows", "input_row_count"),
        ("Enabled / planned", "planned_repository_count"),
        ("Processed", "processed_repository_count"),
        ("Skipped because disabled", "skipped_disabled_count"),
        ("Identical duplicates dropped", "duplicate_rows_dropped"),
    ):
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(manifest.get(key))}</dd>"
        )
    parts.append("</dl>")
    if view.normalized_input is None:
        parts.append(
            "<p>The complete accepted population is <strong>not evaluable</strong>: "
            "<code>normalized_input.csv</code> is absent from this run.</p>"
        )

    # -- per-metric completeness -------------------------------------------
    parts.append('<h2 id="completeness">Completeness and unavailable state</h2>')
    denominator = len(results)
    parts.append(f"<p>Denominator: {denominator} repository result(s).</p>")
    parts.append(_status_table(results, denominator))

    # -- expected family and partial origin --------------------------------
    parts.append("<h2>Expected language-family status</h2>")
    parts.append(_distribution_table(
        results, "expected_language_family_status", denominator
    ))
    parts.append("<h2>Partial-origin distribution</h2>")
    parts.append(_distribution_table(results, "partial_origin", denominator))

    # -- error and recovery evidence ---------------------------------------
    parts.append('<h2 id="diagnostics">Diagnostics and exclusions</h2>')
    errors = sum(len(item.get("errors") or []) for item in results)
    recoveries = sum(
        len((item.get("metrics") or {}).get("recovered_parser_diagnostics") or [])
        for item in results
    )
    parts.append(
        f"<p>Recorded errors: {errors}. Recorded parser recoveries: {recoveries}. "
        "Raw error previews are deliberately not reproduced here; see "
        "<code>errors.csv</code> and <code>recoveries.csv</code>.</p>"
    )

    # -- runtime observations ----------------------------------------------
    parts.append("<h2>Runtime observations</h2>")
    parts.append('<dl class="facts">')
    for label, key in (
        ("Run started", "start_timestamp"),
        ("Measurement finished", "measurement_finished_at"),
        ("Finalization finished", "finalization_finished_at"),
    ):
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(manifest.get(key))}</dd>"
        )
    parts.append("</dl>")
    parts.append(
        "<p>Runtime observations are excluded from semantic run equality and "
        "from the semantic hash.</p>"
    )

    # -- benchmark qualification --------------------------------------------
    if view.qualification_mode == "benchmark_qualified":
        parts.extend(_qualification_section(view, results))

    # -- source composition --------------------------------------------------
    # Placed with the repository table rather than with complexity: these are
    # core line counts, recorded since Artifact Schema 1.3.0 and previously
    # published only as `lines_of_code`.
    parts.extend(_composition_section(view, results))

    # -- callable complexity -------------------------------------------------
    parts.extend(_complexity_section(view, results))
    parts.extend(_cognitive_section(view, results))
    parts.extend(_distribution_section(view, results))

    # -- caveats ------------------------------------------------------------
    # -- version boundary ---------------------------------------------------
    parts.append("<h2>Version and contract boundary</h2>")
    parts.append('<dl class="facts">')
    for label, key in (
        ("Metrolith program", "program_version"),
        ("Metric Contract", "metric_contract_version"),
        ("Exclusion Policy", "exclusion_policy_version"),
        ("Inventory Schema", "inventory_schema_version"),
        ("Artifact Schema", "artifact_schema_version"),
    ):
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(manifest.get(key))}</dd>"
        )
    parts.append("</dl>")

    # -- limitations --------------------------------------------------------
    parts.append("<h2>Limitations</h2>")
    parts.append("<ul>")
    for item in (
        "This report is non-authoritative and must not be parsed for measurement.",
        "Metric values are static observations; no dynamic behaviour is measured.",
        "A partial numeric value is an observation, not a verified complete value.",
        "An unavailable value is never equivalent to numeric zero.",
        "Architecture labels are supplied input metadata, not inferred truth.",
    ):
        parts.append(f"<li>{escape_html(item)}</li>")
    parts.append("</ul>")

    parts.append("</main>")
    parts.append("</body>")
    parts.append("</html>")
    document = "\n".join(parts) + "\n"
    document = document.replace(
        "<table><thead>",
        '<table><caption>Evidence details</caption><thead>',
    ).replace(
        "<table>\n<thead>",
        '<table>\n<caption>Evidence details</caption>\n<thead>',
    )
    return document


#: How many callables each descriptive ranking shows. Small on purpose: this is
#: evidence a reviewer scans, not a leaderboard.
RANKING_LIMIT = 10


def _cognitive_section(view, results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Metrolith Cognitive Complexity — its own section, its own state.

    Separate from the callable-complexity section because it is a separate
    metric with a separate measurement state: a 1.9 run has an evaluable
    structural measurement and an ABSENT cognitive one, and one heading cannot
    say both. The naming statement is rendered verbatim so no reader can take
    the figures for a Sonar product's.
    """
    from modules import complexity_view

    parts: list[str] = ["<h2>Cognitive complexity</h2>"]
    parts.append(
        f"<p>{escape_html(complexity_view.COGNITIVE_NAMING_STATEMENT)}</p>"
    )

    states: dict[str, int] = {}
    for item in results:
        state = complexity_view.cognitive_state_of(item)
        states[state] = states.get(state, 0) + 1
    parts.append("<p>Measurement state: " + ", ".join(
        f"{escape_html(name)}={count}" for name, count in sorted(states.items())
    ) + ".</p>")
    parts.append("<ul>")
    for name in sorted(states):
        parts.append(
            f"<li><strong>{escape_html(name)}</strong>: "
            f"{escape_html(complexity_view.COGNITIVE_STATE_MEANINGS[name])}</li>"
        )
    parts.append("</ul>")
    parts.append(f'<p class="notice">{escape_html(complexity_view.COGNITIVE_ZERO_STATEMENT)}</p>')

    evaluable = [
        item for item in results
        if complexity_view.cognitive_state_of(item)
        in complexity_view.COGNITIVE_EVALUABLE_STATES
    ]
    if not evaluable:
        parts.append(
            "<p>No repository in this run carries an evaluable cognitive "
            "measurement, so no aggregate is shown. An absent measurement is "
            "never rendered as zero.</p>"
        )
        parts.append(f"<p>{escape_html(complexity_view.COGNITIVE_COVERAGE_LIMITATION)}</p>")
        return parts

    header = "".join(
        f'<th scope="col">{escape_html(label)}</th>'
        for _field, label, _definition in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS
    )
    rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">State</th>' + header + "</tr></thead><tbody>"
    ]
    for item in results:
        presented = complexity_view.cognitive_presentation(
            item, _callable_rows_for(view, item)
        )
        cells = "".join(
            _cell(entry["rendered"], numeric=entry["rendered"].replace(".", "", 1).isdigit())
            for entry in presented["aggregate"]
        )
        rows.append(
            f"<tr>{_cell(_subject_name(item))}"
            f"{_cell(presented['state'])}{cells}</tr>"
        )
    rows.append("</tbody></table>")
    parts.extend(rows)

    # Per-language aggregates, reported separately and never summed.
    for item in results:
        presented = complexity_view.cognitive_presentation(
            item, _callable_rows_for(view, item)
        )
        if not presented["by_language"]:
            continue
        parts.append(
            f"<h3>Cognitive complexity by language — "
            f"{escape_html(_subject_name(item))}</h3>"
        )
        language_rows = [
            '<table><thead><tr><th scope="col">Language</th>' + header
            + "</tr></thead><tbody>"
        ]
        for language, entries in presented["by_language"].items():
            cells = "".join(
                _cell(entry["rendered"],
                      numeric=entry["rendered"].replace(".", "", 1).isdigit())
                for entry in entries
            )
            language_rows.append(f"<tr>{_cell(language)}{cells}</tr>")
        language_rows.append("</tbody></table>")
        parts.extend(language_rows)

    parts.append("<h3>Cognitive provenance</h3>")
    for item in results:
        record = complexity_view.cognitive_provenance(view.manifest, item)
        parts.append(
            f"<p><strong>{escape_html(_subject_name(item))}</strong>: "
            f"{escape_html(record['metric_name'])}, Complexity Contract "
            f"{escape_html(record['complexity_contract_version'])}, state "
            f"{escape_html(record['measurement_state'])} — "
            f"{escape_html(record['measurement_state_meaning'])}. Scope: "
            f"{escape_html(record['analyzed_scope']['analysis_scope_hash'])}.</p>"
        )
    parts.append("<h3>Aggregate definitions</h3><ul>")
    for field, label, definition in complexity_view.COGNITIVE_AGGREGATE_DEFINITIONS:
        parts.append(
            f"<li><strong>{escape_html(label)}</strong> "
            f"(<code>{escape_html(field)}</code>): {escape_html(definition)}</li>"
        )
    parts.append("</ul>")
    parts.append(f"<p>{escape_html(complexity_view.COGNITIVE_COVERAGE_LIMITATION)}</p>")
    parts.append(f'<p class="notice">{escape_html(complexity_view.DESCRIPTIVE_ONLY)}</p>')
    return parts


def _callable_rows_for(view, result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Callable rows for one repository, read back through the reader."""
    if not getattr(view, "has_callable_artifact", False):
        return []
    from modules.subject import subject_key_of

    wanted = subject_key_of(dict(result))
    return [
        row for row in view.stream_callables()
        if subject_key_of(dict(row)) == wanted
    ]


#: How many distinct frequency rows the report shows per metric before it says
#: how many it withheld. A rendering limit, not a measurement one.
FREQUENCY_ROW_LIMIT = 15


def _distribution_section(view, results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Complexity distributions: percentiles, frequencies and cumulative shares.

    Reads the persisted callable rows and reports the shape of the population
    the approved aggregates summarize. No metric is recomputed.

    The frequency tables are truncated at :data:`FREQUENCY_ROW_LIMIT` distinct
    values with the withheld count stated. Truncating silently would let a long
    tail disappear, which is the one thing a distribution exists to show.
    """
    from modules import complexity_distribution

    parts: list[str] = ["<h2>Complexity distributions</h2>"]
    parts.append(f"<p>{escape_html(complexity_distribution.PERCENTILE_METHOD)}</p>")

    rows = list(view.stream_callables()) if view.has_callable_artifact else []
    presented = [
        (item, complexity_distribution.presentation(item, _rows_for(rows, item)))
        for item in results
    ]
    evaluable = [(item, entry) for item, entry in presented if entry["evaluable"]]
    if not evaluable:
        parts.append(
            "<p>No repository in this run carries an evaluable complexity "
            "measurement, so no distribution is shown. An unavailable "
            "measurement has no shape and is not rendered as an empty one.</p>"
        )
        parts.append(
            f'<p class="notice">{escape_html(complexity_distribution.DESCRIPTIVE_ONLY)}</p>'
        )
        return parts

    positions = complexity_distribution.PERCENTILE_POSITIONS
    header = "".join(f'<th scope="col">p{position}</th>' for position in positions)
    table = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Metric</th><th scope="col">Measured</th>'
        '<th scope="col">Unmeasured</th><th scope="col">Min</th>'
        + header
        + '<th scope="col">Max</th><th scope="col">Distinct</th>'
        "</tr></thead><tbody>"
    ]
    for item, entry in evaluable:
        for record in entry["combined"]["distributions"]:
            cells = "".join(
                _cell(record["rendered"][f"p{position}"], numeric=True)
                for position in positions
            )
            table.append(
                f"<tr>{_cell(_subject_name(item))}"
                f"{_cell(record['label'])}"
                f"{_cell(record['measured_count'], numeric=True)}"
                f"{_cell(record['unmeasured_count'], numeric=True)}"
                f"{_cell(record['rendered']['min'], numeric=True)}"
                f"{cells}"
                f"{_cell(record['rendered']['max'], numeric=True)}"
                f"{_cell(record['distinct_values'], numeric=True)}</tr>"
            )
    table.append("</tbody></table>")
    parts.extend(table)

    # Exact frequencies with cumulative shares. This is the part an aggregate
    # cannot express: how many callables sit at each measured value.
    parts.append("<h3>Exact value frequencies</h3>")
    parts.append(f"<p>{escape_html(complexity_distribution.NO_BUCKETS_STATEMENT)}</p>")
    for item, entry in evaluable:
        for record in entry["combined"]["distributions"]:
            shown, withheld = complexity_distribution.frequency_head(
                record, limit=FREQUENCY_ROW_LIMIT
            )
            if not shown:
                continue
            lookup = {row["value"]: row for row in record["cumulative"]}
            parts.append(
                f"<h4>{escape_html(record['label'])} — "
                f"{escape_html(_subject_name(item))}</h4>"
            )
            frequency_rows = [
                '<table><thead><tr><th scope="col">Value</th>'
                '<th scope="col">Callables</th>'
                '<th scope="col">At or below</th>'
                '<th scope="col">Share at or below</th>'
                "</tr></thead><tbody>"
            ]
            for row in shown:
                accumulated = lookup.get(row["value"], {})
                share = accumulated.get("share_at_or_below")
                frequency_rows.append(
                    f"<tr>{_cell(row['value'], numeric=True)}"
                    f"{_cell(row['count'], numeric=True)}"
                    f"{_cell(accumulated.get('at_or_below'), numeric=True)}"
                    f"{_cell('' if share is None else f'{share:.4f}', numeric=True)}"
                    "</tr>"
                )
            frequency_rows.append("</tbody></table>")
            parts.extend(frequency_rows)
            if withheld:
                parts.append(
                    f"<p>{withheld} further distinct value(s) are not shown "
                    "here. They are present in the measurement; only this "
                    "table is abbreviated.</p>"
                )

    # Per-language distributions, because the populations were not measured
    # alike and a combined distribution mixes them.
    language_rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Language</th><th scope="col">Metric</th>'
        '<th scope="col">Measured</th><th scope="col">Min</th>'
        + header
        + '<th scope="col">Max</th></tr></thead><tbody>'
    ]
    any_language = False
    for item, entry in evaluable:
        for language, language_profile in entry["by_language"].items():
            for record in language_profile["distributions"]:
                any_language = True
                cells = "".join(
                    _cell(record["rendered"][f"p{position}"], numeric=True)
                    for position in positions
                )
                language_rows.append(
                    f"<tr>{_cell(_subject_name(item))}{_cell(language)}"
                    f"{_cell(record['label'])}"
                    f"{_cell(record['measured_count'], numeric=True)}"
                    f"{_cell(record['rendered']['min'], numeric=True)}"
                    f"{cells}"
                    f"{_cell(record['rendered']['max'], numeric=True)}</tr>"
                )
    language_rows.append("</tbody></table>")
    if any_language:
        parts.append("<h3>Per-language distributions</h3>")
        parts.extend(language_rows)

    parts.append("<h3>Distribution definitions</h3>")
    parts.append('<dl class="facts">')
    for _field, label, status_field, definition in (
        complexity_distribution.DISTRIBUTION_DEFINITIONS
    ):
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(definition)} "
            f"Governed by <code>{escape_html(status_field)}</code>.</dd>"
        )
    parts.append("</dl>")

    parts.append(f"<p>{escape_html(complexity_distribution.COVERAGE_LIMITATION)}</p>")
    parts.append(
        f"<p>{escape_html(complexity_distribution.CROSS_LANGUAGE_LIMITATION)}</p>"
    )
    parts.append(
        f'<p class="notice">{escape_html(complexity_distribution.DESCRIPTIVE_ONLY)}</p>'
    )
    return parts


def _rows_for(
    rows: Sequence[Mapping[str, Any]], result: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """The callable rows belonging to one repository result.

    Matched on `subject_key`, which is what the ledger is ordered by. A cohort
    run holds every repository's rows in one artifact, so filtering is what
    keeps one repository's distribution from describing another's population.

    The reader hands out `mappingproxy`, which `subject_key_of` reads directly.
    Neighbouring call sites still copy with `dict(...)`; those copies are
    vestigial workarounds from before that boundary accepted any Mapping, and
    are not needed here.
    """
    key = subject_key_of(result)
    return [row for row in rows if row.get("subject_key") == key]


def _composition_section(view, results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Source line composition: the five recorded counts and their shares.

    Nothing here classifies a line. The counts come from `metrics.aggregate`,
    where `modules.core_metrics` recorded them; this renders them through
    `source_composition` so an unavailable count reads `unavailable` in the
    report exactly as it does in `summary.md`.

    Ratios carry their denominator in the rendered table header, because a
    share read against the wrong base is worse than no share at all.
    """
    from modules import source_composition

    parts: list[str] = ["<h2>Source composition</h2>"]
    parts.append(
        '<p class="notice">Physical-line counts by shape. These are '
        "<strong>not</strong> documentation, maintainability or quality "
        "measures, and no cut point is applied to any of them.</p>"
    )

    states = source_composition.state_distribution(results)
    parts.append("<ul>")
    for name, count in states.items():
        parts.append(
            f"<li>{escape_html(name)}: {count}/{len(results)} — "
            f"{escape_html(source_composition.STATE_MEANINGS[name])}</li>"
        )
    parts.append("</ul>")

    evaluable = source_composition.evaluable_results(results)
    if not evaluable:
        parts.append(
            "<p>No repository in this run carries an evaluable "
            "source-composition measurement, so no count is shown. An "
            "unavailable measurement is never rendered as zero.</p>"
        )
        parts.append(f'<p class="notice">{escape_html(source_composition.DESCRIPTIVE_ONLY)}</p>')
        return parts

    header = "".join(
        f'<th scope="col">{escape_html(label)}</th>'
        for _field, label, _definition in source_composition.COMPOSITION_DEFINITIONS
    ) + "".join(
        f'<th scope="col">{escape_html(label)}<br>'
        f'<small>÷ {escape_html(denominator)}</small></th>'
        for _n, label, _num, denominator, _d in source_composition.RATIO_DEFINITIONS
    )
    rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">State</th>' + header + "</tr></thead><tbody>"
    ]
    for item in results:
        presented = source_composition.presentation(item)
        cells = "".join(
            _cell(
                entry["rendered"],
                numeric=entry["rendered"] != source_composition.UNAVAILABLE,
            )
            for entry in (*presented["counts"], *presented["ratios"])
        )
        rows.append(
            f"<tr>{_cell(_subject_name(item))}"
            f"{_cell(presented['state'])}{cells}</tr>"
        )
    rows.append("</tbody></table>")
    parts.extend(rows)

    # Per-language composition, reported separately for the same reason the
    # complexity figures are: what counts as a comment is language-specific.
    language_rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Language</th><th scope="col">State</th>'
        + header + "</tr></thead><tbody>"
    ]
    any_language = False
    for item in results:
        presented = source_composition.presentation(item)
        for language, entry in presented["by_language"].items():
            any_language = True
            cells = "".join(
                _cell(
                    row["rendered"],
                    numeric=row["rendered"] != source_composition.UNAVAILABLE,
                )
                for row in (*entry["counts"], *entry["ratios"])
            )
            language_rows.append(
                f"<tr>{_cell(_subject_name(item))}"
                f"{_cell(language)}{_cell(entry['state'])}{cells}</tr>"
            )
    language_rows.append("</tbody></table>")
    if any_language:
        parts.append("<h3>Per-language source composition</h3>")
        parts.extend(language_rows)

    parts.append("<h3>Source composition definitions</h3>")
    parts.append('<dl class="facts">')
    for _field, label, definition in source_composition.COMPOSITION_DEFINITIONS:
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(definition)}</dd>"
        )
    for _name, label, _num, _den, definition in source_composition.RATIO_DEFINITIONS:
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(definition)}</dd>"
        )
    parts.append("</dl>")

    # The partition identities, published so a reader can check the counts
    # rather than trust them.
    parts.append("<h3>Partition identities</h3>")
    identity_rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Identity</th><th scope="col">Holds</th>'
        "</tr></thead><tbody>"
    ]
    for item in results:
        presented = source_composition.presentation(item)
        for check in presented["partition_checks"]:
            holds = (
                source_composition.UNAVAILABLE
                if check["holds"] is None
                else ("yes" if check["holds"] else "NO")
            )
            identity_rows.append(
                f"<tr>{_cell(_subject_name(item))}"
                f"{_cell(check['statement'])}{_cell(holds)}</tr>"
            )
    identity_rows.append("</tbody></table>")
    parts.extend(identity_rows)

    parts.append(f'<p class="notice">{escape_html(source_composition.ZERO_STATEMENT)}</p>')
    parts.append(f"<p>{escape_html(source_composition.CROSS_LANGUAGE_LIMITATION)}</p>")
    parts.append(f'<p class="notice">{escape_html(source_composition.DESCRIPTIVE_ONLY)}</p>')
    return parts


def _complexity_section(view, results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Callable complexity: the approved aggregates plus descriptive rankings.

    No colouring, no "critical", no threshold, no composite score. A ranking is
    an ordering of published values, not a judgement, and the caveat above it
    says so in the same words the summary and `explain` use.
    """
    from modules import complexity_view

    parts: list[str] = ["<h2>Callable complexity</h2>"]

    states = {}
    for item in results:
        state = complexity_view.state_of(item)
        states[state] = states.get(state, 0) + 1
    parts.append("<p>Measurement state: " + ", ".join(
        f"{escape_html(name)}={count}" for name, count in sorted(states.items())
    ) + ".</p>")
    parts.append("<ul>")
    for name in sorted(states):
        parts.append(
            f"<li><strong>{escape_html(name)}</strong>: "
            f"{escape_html(complexity_view.STATE_MEANINGS[name])}</li>"
        )
    parts.append("</ul>")
    parts.append(f'<p class="notice">{escape_html(complexity_view.DESCRIPTIVE_ONLY)}</p>')

    evaluable = [
        item for item in results
        if complexity_view.state_of(item) in complexity_view.EVALUABLE_STATES
    ]
    if not evaluable:
        parts.append(
            "<p>No repository in this run carries an evaluable complexity "
            "measurement, so no aggregate is shown. An absent measurement is "
            "never rendered as zero.</p>"
        )
        parts.append(f"<p>{escape_html(complexity_view.COVERAGE_LIMITATION)}</p>")
        parts.append(f"<p>{escape_html(complexity_view.CROSS_LANGUAGE_LIMITATION)}</p>")
        return parts

    header = "".join(
        f'<th scope="col">{escape_html(label)}</th>'
        for _field, label, _definition in complexity_view.AGGREGATE_DEFINITIONS
    )
    rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">State</th>' + header + "</tr></thead><tbody>"
    ]
    for item in results:
        presented = complexity_view.presentation(item)
        cells = "".join(
            _cell(entry["rendered"], numeric=entry["rendered"].isdigit())
            for entry in presented["aggregate"]
        )
        rows.append(
            f"<tr>{_cell(_subject_name(item))}"
            f"{_cell(presented['state'])}{cells}</tr>"
        )
    rows.append("</tbody></table>")
    parts.extend(rows)

    # Per-language aggregates, reported separately precisely because the
    # figures are not measurement-equivalent across languages.
    language_rows = [
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Language</th>' + header + "</tr></thead><tbody>"
    ]
    any_language = False
    for item in results:
        presented = complexity_view.presentation(item)
        for language, entries in presented["by_language"].items():
            any_language = True
            cells = "".join(
                _cell(entry["rendered"], numeric=entry["rendered"].isdigit())
                for entry in entries
            )
            language_rows.append(
                f"<tr>{_cell(_subject_name(item))}"
                f"{_cell(language)}{cells}</tr>"
            )
    language_rows.append("</tbody></table>")
    if any_language:
        parts.append("<h3>Per-language complexity</h3>")
        parts.extend(language_rows)

    parts.extend(_complexity_rankings(view))

    # Provenance per repository: contract version, measurement state and the
    # exact analyzed scope. A complexity figure without these is not
    # explainable, and the report is where a reader looks for them.
    parts.append("<h3>Complexity provenance</h3>")
    parts.append(
        '<table><thead><tr><th scope="col">Repository</th>'
        '<th scope="col">Complexity Contract</th><th scope="col">State</th>'
        '<th scope="col">Analyzed scope hash</th>'
        '<th scope="col">Commit / reference</th>'
        '<th scope="col">Source mode</th></tr></thead><tbody>'
    )
    for item in results:
        record = complexity_view.provenance(view.manifest, item)
        scope = record["analyzed_scope"]
        revision = scope["analyzed_commit_sha"]
        if scope["source_mode"] == "local_worktree_snapshot":
            revision = f"HEAD reference only: {revision or 'unavailable'}"
        elif scope["source_mode"] == "local_directory_snapshot":
            revision = "Directory snapshot; no Git commit"
        elif revision:
            revision = f"Analyzed commit: {revision}"
        parts.append(
            "<tr>"
            + _cell(_subject_name(item))
            + _cell(record["complexity_contract_version"])
            + _cell(record["measurement_state"])
            + _cell(scope["analysis_scope_hash"])
            + _cell(revision)
            + _cell(scope["source_mode"])
            + "</tr>"
        )
    parts.append("</tbody></table>")

    parts.append("<h3>What these figures mean</h3>")
    parts.append("<dl class=\"facts\">")
    for _field, label, definition in complexity_view.AGGREGATE_DEFINITIONS:
        parts.append(
            f"<dt>{escape_html(label)}</dt><dd>{escape_html(definition)}</dd>"
        )
    parts.append("</dl>")
    parts.append(f"<p>{escape_html(complexity_view.COVERAGE_LIMITATION)}</p>")
    parts.append(f"<p>{escape_html(complexity_view.CROSS_LANGUAGE_LIMITATION)}</p>")
    return parts


def _complexity_rankings(view) -> list[str]:
    """Top-N callables by each published field, or an explicit absence."""
    from modules import complexity_view

    if not getattr(view, "has_callable_artifact", False):
        return [
            "<h3>Highest-value callables</h3>",
            "<p>This run carries no callable artifact, so no ranking is "
            "available. That is an absence of evidence, not an empty result.</p>",
        ]

    rows = [dict(row) for row in view.stream_callables()]
    if not rows:
        return [
            "<h3>Highest-value callables</h3>",
            "<p>The callable artifact is present and holds no rows.</p>",
        ]

    parts = ["<h3>Highest-value callables</h3>"]
    parts.append(
        f"<p>Top {RANKING_LIMIT} by each published field, ordered descending. "
        "A callable whose value is unavailable is not ranked at all — ranking "
        "it would mean treating an absent measurement as a number.</p>"
    )
    for field, label in complexity_view.RANKING_FIELDS:
        ranked = complexity_view.rank_callables(rows, field, limit=RANKING_LIMIT)
        parts.append(f"<h4>By {escape_html(label)}</h4>")
        if not ranked:
            parts.append("<p>No callable carries an evaluable value.</p>")
            continue
        parts.append(
            '<table><thead><tr><th scope="col">Repository</th>'
            '<th scope="col">File</th><th scope="col">Callable</th>'
            '<th scope="col">Lines</th>'
            f'<th scope="col">{escape_html(label)}</th>'
            "</tr></thead><tbody>"
        )
        for row in ranked:
            span = f"{row.get('start_line')}-{row.get('end_line')}"
            parts.append(
                "<tr>"
                + _cell(row.get("subject_key"))
                + _cell(row.get("relative_path"))
                + _cell(row.get("qualified_name"))
                + _cell(span)
                + _cell(row.get(field), numeric=True)
                + "</tr>"
            )
        parts.append("</tbody></table>")
    return parts


STATUS_FIELDS = (
    "inventory_status", "source_files_status", "loc_status",
    "classes_structs_status", "methods_functions_status",
)


def _status_table(results: Sequence[Mapping[str, Any]], denominator: int) -> str:
    rows = ["<table><thead><tr><th scope=\"col\">Metric status field</th>"
            "<th scope=\"col\">Distribution</th></tr></thead><tbody>"]
    for field in STATUS_FIELDS:
        counts: dict[str, int] = {}
        for item in results:
            value = (item.get("metrics") or {}).get("aggregate", {}).get(field)
            key = "null" if value is None else str(value)
            counts[key] = counts.get(key, 0) + 1
        rendered = ", ".join(
            f"{escape_html(name)}={count}/{denominator}"
            for name, count in sorted(counts.items())
        ) or "none recorded"
        rows.append(f"<tr><td>{escape_html(field)}</td><td>{rendered}</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _distribution_table(
    results: Sequence[Mapping[str, Any]], field: str, denominator: int
) -> str:
    counts: dict[str, int] = {}
    for item in results:
        value = item.get(field)
        key = "null" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    rows = [
        f'<table><thead><tr><th scope="col">{escape_html(field)}</th>'
        '<th scope="col" class="numeric">Count</th></tr></thead><tbody>'
    ]
    for name, count in sorted(counts.items()):
        rows.append(
            f"<tr><td>{escape_html(name)}</td>"
            f'<td class="numeric">{count}/{denominator}</td></tr>'
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def _repository_table(results: Sequence[Mapping[str, Any]]) -> str:
    from modules.presentation import measurement, status as display_status, subject_display

    headers = (
        "Repository", "Recorded status", "Expected-family status", "Partial origin",
        "LOC", "Source Files", "Classes / Structs", "Methods / Functions",
    )
    numeric_from = 4
    rows = ["<table><thead><tr>"]
    for index, header in enumerate(headers):
        classes = ' class="numeric"' if index >= numeric_from else ""
        rows.append(f'<th scope="col"{classes}>{escape_html(header)}</th>')
    rows.append("</tr></thead><tbody>")

    for item in sorted(
        results, key=lambda entry: subject_key_of(entry).casefold()
    ):
        aggregate = (item.get("metrics") or {}).get("aggregate", {})
        rows.append("<tr>")
        # Repository URLs are rendered as text, never as a link: an external
        # href would be an external-scheme navigation target in a file the
        # security contract requires to be self-contained.
        identity = subject_display(item)
        rows.append(
            f'<th scope="row">{escape_html(identity.name)}'
            f'<br><small>subject: {escape_html(identity.subject_key)}</small></th>'
        )
        rows.append(_cell(display_status(item.get("analysis_status"))))
        rows.append(_cell(display_status(item.get("expected_language_family_status"))))
        rows.append(_cell(display_status(item.get("partial_origin"))))
        for key, status_key in (
            ("lines_of_code", "loc_status"),
            ("source_files", "source_files_status"),
            ("classes_structs", "classes_structs_status"),
            ("methods_functions", "methods_functions_status"),
        ):
            rows.append(
                _cell(
                    measurement(aggregate.get(key), aggregate.get(status_key)),
                    numeric=True,
                )
            )
        rows.append("</tr>")
    rows.append("</tbody></table>")
    return "".join(rows)
