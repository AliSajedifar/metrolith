"""``metrolith check`` — the architecture quality gate.

    metrolith check <run-directory> --policy FILE [--baseline FILE]
        [--baseline-sha256 SHA256] [--format text|json|sarif] [--output FILE]

Exit codes are the contract, and there are exactly three so a CI step can
branch on them without a lookup table:

===  ==========================================================
  0  the policy was evaluated and nothing requires failure
  1  the policy was evaluated and at least one violation-severity
     finding requires failure
  2  the policy was NOT evaluated — see ``failure_kind``
===  ==========================================================

A warning-severity finding never changes the exit code. Only a rule the policy
declares a ``violation`` does — and, under the default
``options.on_not_evaluable``, a violation-severity rule that could not run at
all, because a gate that silently did not run is worse than one that failed.

``--policy`` is REQUIRED and has no default. Metrolith ships no v2 policy and no
default threshold: every threshold is the policy author's decision, and a
built-in one would be an implied recommendation there is no evidence for.

This is a generic product command. It requires no benchmark qualification, no
representativeness verdict and no benchmark-of-record readiness; when a bundle
carries them they are echoed as contextual provenance and influence nothing.
"""

from __future__ import annotations

from modules.presentation import wrap_prose

import hashlib
import sys
from pathlib import Path
from typing import Any

from modules.policy import findings as finding_module
from modules.policy import metrics as metric_module
from modules.policy.check import (
    EXIT_ERROR,
    EXIT_PASS,
    FAILURE_EVALUATION_ERROR,
    FAILURE_POLICY_INVALID,
    FAILURE_RATCHET_ADMISSION,
    FAILURE_RUN_UNREADABLE,
    FAILURE_USAGE,
    FAILURE_TRUST_ADMISSION,
    CheckFailed,
    evaluate_check,
    failure_result,
)
from modules.policy.document import PolicyDocumentInvalid
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_any_policy_bytes,
)
from modules.config import PROGRAM_VERSION
from modules.presentation import trust_label
from modules.policy import trust as trust_module
from archlens_json import (
    CHECK_JSON_LIMITS,
    POLICY_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    read_bounded_bytes,
)


_BASELINE_ORIGINS = (
    "protected_base_revision",
    "owner_controlled_artifact",
    "candidate_workspace",
    "local_file",
)


def add_parser(sub) -> None:
    parser = sub.add_parser(
        "check",
        help="Evaluate a published run against a policy and gate on the result",
        description=(
            "Architecture quality gate over existing Metrolith measurements. "
            "Every threshold is user-supplied policy: Metrolith ships no default "
            "threshold and makes no architecture-quality claim. Exit 0 = pass, "
            "1 = policy violation, 2 = configuration, evaluation, or output delivery failure. "
            "CLI Ratchet ancestry requires URL-backed subjects and their existing "
            "Git sources in the current run's runner cache; local analyze --revision "
            "alone has no repository mapping on check. "
            "Example: metrolith check RUN --policy policy.json --output check.json --overwrite"
        ),
    )
    parser.add_argument("run_directory", type=Path, help="A published run directory")
    parser.add_argument(
        "--policy", type=Path, required=True,
        help=(
            "Policy document (Policy v1 or v2). Required: there is no default "
            "policy, because a shipped threshold would be a recommendation "
            "Metrolith has no evidence for."
        ),
    )
    parser.add_argument(
        "--gate-mode",
        choices=trust_module.GATE_MODES,
        default=trust_module.LOCAL_UNPROTECTED,
        help=(
            "local_unprotected (default) keeps development behavior and labels "
            "it honestly; protected_required requires externally authenticated "
            "evaluator, Policy, and supplied evidence provenance."
        ),
    )
    parser.add_argument(
        "--policy-sha256",
        help=(
            "Externally supplied lowercase SHA-256 of the exact Policy bytes. "
            "Required in protected_required mode."
        ),
    )
    parser.add_argument("--evaluator-repository", help="Protected owner/repository identity")
    parser.add_argument("--evaluator-revision", help="Protected full 40-hex Action commit")
    parser.add_argument(
        "--evaluator-source-sha256",
        help="Externally reviewed SHA-256 identity of the installed evaluator source",
    )
    parser.add_argument(
        "--evidence-receipt",
        type=Path,
        help="Canonical owner-controlled trusted-evidence receipt",
    )
    parser.add_argument(
        "--evidence-receipt-sha256",
        help="Externally supplied lowercase SHA-256 of --evidence-receipt",
    )
    parser.add_argument(
        "--hotspots", type=Path,
        help=(
            "An `metrolith hotspots` JSON document to admit as evidence. "
            "Required by any rule naming a `hotspot_` metric; without it those "
            "rules report `not_evaluable`, which is never a pass. A document "
            "that fails admission is reported and contributes nothing."
        ),
    )
    parser.add_argument(
        "--duplication", type=Path,
        help=(
            "An `metrolith duplication` JSON document to admit as evidence. "
            "Required by any rule naming a `duplication_` metric; without it "
            "those rules report `not_evaluable`, which is never a pass. One "
            "document describes one snapshot and binds to at most one subject, "
            "so on a multi-subject run the other subjects are reported as not "
            "evaluable rather than silently gated on this one."
        ),
    )
    parser.add_argument(
        "--baseline", type=Path,
        help=(
            "Canonical Ratchet Baseline 1.0 artifact. Its immutable producing "
            "run must be available in the read-only BASELINE.source-run sidecar. "
            "Supplying this option requires --baseline-sha256."
        ),
    )
    parser.add_argument(
        "--baseline-sha256",
        help=(
            "Externally trusted lowercase SHA-256 of --baseline. Required with "
            "--baseline; a digest read from the artifact itself is not accepted."
        ),
    )
    parser.add_argument(
        "--baseline-origin",
        choices=_BASELINE_ORIGINS,
        help=(
            "External provenance of --baseline bytes. Local CLI calls default "
            "to local_file; protected automation must pass its protected "
            "source classification explicitly."
        ),
    )
    parser.add_argument(
        "--baseline-pull-request",
        action="store_true",
        help=(
            "Apply BR2 pull-request source restrictions to --baseline. This is "
            "set by the GitHub Action for pull_request events."
        ),
    )
    parser.add_argument(
        "--format", choices=("text", "json", "sarif"), default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output", type=Path,
        help=(
            "Also write the machine-readable result here (check JSON for text/"
            "json; SARIF for sarif). Refuses existing files unless --overwrite; "
            "never writes inside an input run or over Policy/evidence inputs."
        ),
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Replace an existing ordinary output file; never permits overwriting protected inputs.",
    )
    return parser


def handle(args) -> int:
    from modules.cli.check_output import CheckOutputError, validate_destination

    try:
        validate_destination(args)
        return _handle(args)
    except CheckOutputError as exc:
        # A rejected destination is never reused for an error document.
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_ERROR


def _handle(args) -> int:
    policy_path: Path | None = getattr(args, "policy", None)
    run_directory: Path | None = getattr(args, "run_directory", None)
    output_format = getattr(args, "format", "text")
    baseline_path: Path | None = getattr(args, "baseline", None)
    baseline_sha256: str | None = getattr(args, "baseline_sha256", None)
    baseline_origin: str | None = getattr(args, "baseline_origin", None)
    baseline_pull_request = bool(getattr(args, "baseline_pull_request", False))
    gate_mode = getattr(args, "gate_mode", trust_module.LOCAL_UNPROTECTED)

    if policy_path is None:
        return _fail(
            args, FAILURE_USAGE,
            "metrolith check requires --policy. Metrolith ships no default "
            "thresholds, so there is nothing to fall back to.",
            run_directory, None,
        )

    try:
        policy_payload = read_bounded_bytes(
            policy_path, source=str(policy_path), limits=POLICY_JSON_LIMITS
        )
    except StrictJsonError as exc:
        return _fail(
            args, FAILURE_POLICY_INVALID, f"invalid policy document: {exc}",
            run_directory, None,
        )

    trust: trust_module.CheckTrustContext | None = None
    try:
        if gate_mode == trust_module.PROTECTED_REQUIRED:
            repository = getattr(args, "evaluator_repository", None)
            revision = getattr(args, "evaluator_revision", None)
            source_sha256 = getattr(args, "evaluator_source_sha256", None)
            if not all((repository, revision, source_sha256)):
                raise trust_module.TrustAdmissionError(
                    "evaluator_provenance_required"
                )
            evaluator = trust_module.EvaluatorIdentity.protected(
                repository=repository,
                revision=revision,
                source_sha256=source_sha256,
                installed_version=PROGRAM_VERSION,
            )
            receipt_path = getattr(args, "evidence_receipt", None)
            receipt_sha256 = getattr(args, "evidence_receipt_sha256", None)
            if (receipt_path is None) != (receipt_sha256 is None):
                raise trust_module.TrustAdmissionError(
                    "receipt_path_and_digest_required_together"
                )
            receipt = (
                None
                if receipt_path is None
                else trust_module.load_receipt_file(receipt_path, receipt_sha256)
            )
            expected_policy = getattr(args, "policy_sha256", None)
            if expected_policy is None:
                raise trust_module.TrustAdmissionError("policy_sha256_required")
            trust = trust_module.CheckTrustContext.protected_required(
                evaluator=evaluator,
                policy_payload=policy_payload,
                expected_policy_sha256=expected_policy,
                receipt=receipt,
            )
        else:
            trust = trust_module.CheckTrustContext.local(
                evaluator_version=PROGRAM_VERSION,
                policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
            )
    except trust_module.TrustAdmissionError as exc:
        return _fail(
            args,
            FAILURE_TRUST_ADMISSION,
            str(exc),
            run_directory,
            None,
            trust=trust,
            requested_mode=gate_mode,
        )

    try:
        policy = load_any_policy_bytes(policy_payload, source=str(policy_path))
    except PolicyDocumentInvalid as exc:
        return _fail(
            args, FAILURE_POLICY_INVALID, f"invalid policy document: {exc}",
            run_directory, None, trust=trust,
        )

    if baseline_path is not None and baseline_sha256 is None:
        return _ratchet_fail(
            args,
            "external_digest_required",
            "--baseline requires --baseline-sha256",
            run_directory,
            policy.name,
            trust=trust,
        )
    if baseline_path is None and baseline_sha256 is not None:
        return _fail(
            args,
            FAILURE_USAGE,
            "--baseline-sha256 has no meaning without --baseline.",
            run_directory,
            policy.name,
            trust=trust,
        )
    if baseline_path is None and (baseline_origin is not None or baseline_pull_request):
        return _fail(
            args,
            FAILURE_USAGE,
            "--baseline-origin and --baseline-pull-request require --baseline.",
            run_directory,
            policy.name,
            trust=trust,
        )

    ratchet = None
    if baseline_path is not None:
        from modules.ratchet.cli_request import (
            RatchetCliRequestError,
            build_cli_ratchet_request,
        )

        if (
            gate_mode == trust_module.PROTECTED_REQUIRED
            and baseline_origin not in {
                "protected_base_revision", "owner_controlled_artifact"
            }
        ):
            return _ratchet_fail(
                args,
                "unprotected_protected_gate_baseline_source",
                baseline_origin,
                run_directory,
                policy.name,
                trust=trust,
            )
        try:
            ratchet = build_cli_ratchet_request(
                baseline_path,
                str(baseline_sha256),
                current_run_directory=run_directory,
                origin=baseline_origin or "local_file",
                pull_request_mode=baseline_pull_request,
            )
        except RatchetCliRequestError as exc:
            return _ratchet_fail(
                args,
                exc.code,
                exc.detail,
                run_directory,
                policy.name,
                trust=trust,
            )

    try:
        check_arguments = {
            "hotspots": getattr(args, "hotspots", None),
            "duplication": getattr(args, "duplication", None),
            "trust": trust,
        }
        # Preserve the exact no-baseline call boundary.  Ratchet is opt-in and
        # the existing evaluator receives no new keyword when it is absent.
        if ratchet is not None:
            check_arguments["ratchet"] = ratchet
        result = evaluate_check(run_directory, policy, **check_arguments)
    except CheckFailed as exc:
        return _fail(
            args, exc.kind, exc.message, run_directory, policy.name, trust=trust
        )
    except FileNotFoundError as exc:
        return _fail(
            args, FAILURE_RUN_UNREADABLE, str(exc), run_directory, policy.name,
            trust=trust,
        )

    try:
        _emit(args, result, output_format, policy=policy)
    except CheckFailed as exc:
        return _fail(
            args, exc.kind, exc.message, run_directory, policy.name, trust=trust
        )
    return int(result["exit_code"])


def _fail(
    args,
    kind: str,
    message: str,
    run_directory: Path | None,
    policy_name: str | None,
    *,
    trust: trust_module.CheckTrustContext | None = None,
    requested_mode: str | None = None,
) -> int:
    """Report an evaluation that could not happen, in the requested format.

    The JSON shape is the same one a successful evaluation produces, so a CI
    step parsing the output never has to tell "no document" apart from "clean
    pass" — the two outcomes most dangerous to confuse.
    """
    result = failure_result(
        kind=kind, message=message, run_directory=run_directory,
        policy_name=policy_name, trust=trust, requested_mode=requested_mode,
    )
    _emit(args, result, getattr(args, "format", "text"))
    return EXIT_ERROR


def _ratchet_fail(
    args,
    code: str,
    detail: str | None,
    run_directory: Path | None,
    policy_name: str | None,
    *,
    trust: trust_module.CheckTrustContext | None = None,
) -> int:
    """Emit a configured admission failure before the current run can open."""

    from modules.ratchet.check_service import (
        RATCHET_CHECK_RESULT_FORMAT_VERSION,
        admission_failure_summary,
    )

    message = code if detail is None else f"{code}: {detail}"
    result = failure_result(
        kind=FAILURE_RATCHET_ADMISSION,
        message=message,
        run_directory=run_directory,
        policy_name=policy_name,
        trust=trust,
    )
    result["check_result_format_version"] = RATCHET_CHECK_RESULT_FORMAT_VERSION
    result["ratchet"] = admission_failure_summary(code, detail)
    _emit(args, result, getattr(args, "format", "text"))
    return EXIT_ERROR


def _emit(
    args, result: dict[str, Any], output_format: str, *, policy: Any = None
) -> None:
    from modules.cli.check_output import CheckOutputError, write_result
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.schema_store import validate_document

    # Validate the original result before every format and any output mutation.
    # Refuse delivery without manufacturing a new evaluation or its counts.
    try:
        problems = validate_document("check_result_output", result, "Check Result output")
    except (ArtifactStructureError, ValueError) as exc:
        raise CheckOutputError("Check output validation could not complete") from exc
    if problems:
        raise CheckOutputError(
            f"Check output violates its shipped schema at {problems[0].location}; result was not emitted"
        )

    destination: Path | None = getattr(args, "output", None)
    if output_format == "sarif":
        from modules.policy.sarif import SarifProjectionError, render_sarif

        # `render_sarif` includes exactly one final LF. stdout and a file are
        # therefore byte-identical under the same UTF-8 encoding.
        try:
            payload = render_sarif(result, policy=policy)
        except SarifProjectionError as exc:
            raise CheckFailed(FAILURE_EVALUATION_ERROR, str(exc)) from None
        if destination is not None:
            write_result(args, payload)
        sys.stdout.write(payload)
        return

    try:
        payload = dumps_strict(
            result,
            source="Check Result output",
            limits=CHECK_JSON_LIMITS,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except StrictJsonError as exc:
        raise CheckFailed(FAILURE_EVALUATION_ERROR, str(exc)) from None
    if destination is not None:
        write_result(args, payload + "\n")
    if output_format == "json":
        print(payload)
    else:
        print(render_text(result))


# --------------------------------------------------------------------------
# Human-readable rendering. Concise by design: a gate is read in a CI log.
# --------------------------------------------------------------------------

_VERDICT_LABEL = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}


def _evidence_lines(result: dict[str, Any]) -> list[str]:
    """The `evidence` block, rendered for the default output format.

    Without this a text reader cannot tell a gate that HAD its evidence from one
    that did not, which is exactly the confusion the block exists to prevent --
    and text is the format a human actually reads. Every value here is read
    straight out of the result document, so text and JSON cannot disagree: this
    function selects and formats, and derives nothing.
    """
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        return []
    if not evidence:
        # The failure path. An empty block means no evidence was even
        # considered, because no evaluation happened -- which is not the same
        # as a gate that considered evidence and found none.
        return ["", "evidence: none considered (the policy was not evaluated)"]

    lines = ["", "evidence:"]
    width = max(len(str(kind)) for kind in evidence)
    for kind in sorted(evidence):
        record = evidence[kind] or {}
        admission = record.get("admission") or "unknown"
        used = record.get("used_by_rules") or []
        detail: list[str] = []

        binding = record.get("binding") or {}
        subjects = binding.get("subject_keys") or []
        if admission == "admitted" and subjects:
            detail.append(f"bound to {', '.join(str(item) for item in subjects)}")
        elif admission == "admitted":
            detail.append(f"binding {binding.get('state') or 'unknown'}")
        elif record.get("reason"):
            detail.append(str(record["reason"]))

        if used:
            detail.append(f"read by {len(used)} rule(s)")
            if admission != "admitted":
                # The consequence, stated where the operator will see it. A rule
                # that could not run is never a pass, and a text reader must not
                # have to infer that from a count elsewhere.
                detail.append("those rules are NOT evaluable")
        else:
            detail.append("no rule reads it")

        lines.append(
            f"  {str(kind).ljust(width)}  {admission} ({'; '.join(detail)})"
        )
    return lines


def _group_detail_lines(finding: dict[str, Any], indent: str) -> list[str]:
    """The clone group's own published figures, for a finding that has them.

    Every value is read straight out of the finding's `evidence`, which the
    EVALUATOR wrote from the Duplication document. Nothing is counted, summed or
    inferred here: `len(occurrences)` is deliberately never used in place of
    `duplication_occurrence_count`, because the occurrence list is capped and
    the count is exact, and a renderer that quietly reported the capped length
    would contradict the JSON it is supposed to echo.

    Returns no lines for a finding with no duplication evidence, so this stays
    one branch on data rather than a branch on scope.
    """
    evidence = finding.get("evidence") or {}
    kind = evidence.get("duplication_kind")
    if not kind:
        return []

    lines: list[str] = []
    occurrences = evidence.get("duplication_occurrence_count")
    files = evidence.get("duplication_file_count")
    distribution = evidence.get("duplication_distribution")
    span = evidence.get("duplication_source_span_line_count")

    if occurrences is not None or files is not None:
        summary = f"{indent}{kind} clone group"
        parts = []
        if occurrences is not None:
            parts.append(f"{occurrences} occurrence(s)")
        if files is not None:
            parts.append(f"{files} file(s)")
        if span is not None:
            # Structural groups only, and it is the MERGED span the Duplication
            # contract publishes -- never a sum of the occurrences.
            parts.append(f"{span} merged span line(s)")
        if distribution:
            parts.append(str(distribution))
        lines.append(f"{summary}: {', '.join(parts)}")
    else:
        # A kind with no group figures: the collapse case, where the reason is
        # the kind's own persisted status.
        status = evidence.get("duplication_kind_status")
        field = evidence.get("duplication_kind_status_field")
        detail = f"{indent}duplication kind: {kind}"
        if status is not None:
            detail = f"{detail} ({field or 'status'} = {status})"
        lines.append(detail)
        return lines

    for occurrence in evidence.get("duplication_occurrences") or ():
        if not isinstance(occurrence, dict):
            continue
        start = occurrence.get("start_line")
        end = occurrence.get("end_line")
        where = str(occurrence.get("path") or "")
        if start is not None:
            where = f"{where}:{start}" + (f"-{end}" if end is not None else "")
        lines.append(f"{indent}  {where}")
    if evidence.get("duplication_occurrences_truncated"):
        shown = len(evidence.get("duplication_occurrences") or ())
        lines.append(
            f"{indent}  ... {occurrences} occurrence(s) in total; "
            f"{shown} listed"
        )
    return lines


def render_text(result: dict[str, Any]) -> str:
    lines: list[str] = [
        f"Metrolith check {result['check_result_format_version']} "
        f"(metrolith {result['archlens_version']})",
        f"trust: {trust_label(result.get('evaluated_input_provenance'))}",
    ]

    policy = result.get("policy") or {}
    run = result.get("run") or {}

    if result.get("failure_kind") and not result.get("findings"):
        from modules.policy.check import FAILURE_KIND_MEANINGS

        kind = result["failure_kind"]
        safe_next = (
            "metrolith policy validate POLICY"
            if kind == FAILURE_POLICY_INVALID
            else "metrolith check --help"
        )
        lines.extend([
            "",
            f"OUTCOME: {_VERDICT_LABEL.get(result['verdict'], 'ERROR')} ({kind})",
            f"Cause: {result.get('failure_message') or FAILURE_KIND_MEANINGS.get(kind, '')}",
            "Was evaluation performed? no",
            f"Safe next action: {safe_next}",
            f"Reason / exit: {kind} / {result.get('exit_code')}",
        ])
        lines.extend(_evidence_lines(result))
        lines.extend([
            "",
            "The policy was NOT evaluated. This is not a pass.",
            f"Next: {safe_next}",
        ])
        return "\n".join(lines)

    lines.extend([
        f"policy: {policy.get('name')} "
        f"(document {policy.get('policy_document_format_version')}, "
        f"{policy.get('integrity_rule_count')} integrity rule(s), "
        f"{policy.get('metric_rule_count')} metric rule(s))",
        f"run:    {run.get('run_id')} "
        f"(artifact {run.get('artifact_schema_version')}, "
        f"status {run.get('run_status')})",
    ])
    # Before the verdict, deliberately: a passing verdict from a gate that was
    # never given its evidence must not read as healthy.
    lines.extend(_evidence_lines(result))

    counts = result.get("counts") or {}
    by_status = counts.get("by_status") or {}
    violations = [item for item in result.get("findings", ()) if item.get("status") == finding_module.STATUS_VIOLATED]
    blocking = sum(item.get("severity") == "violation" for item in violations)
    warnings = sum(item.get("severity") == "warning" for item in violations)
    lines.extend([
        "",
        f"VERDICT: {_VERDICT_LABEL.get(result['verdict'], 'ERROR')} under this Policy  "
        f"({blocking} blocking violations, {warnings} nonblocking warnings, "
        f"{by_status.get(finding_module.STATUS_NOT_EVALUABLE, 0)} not evaluable, "
        f"{by_status.get(finding_module.STATUS_NOT_APPLICABLE, 0)} not applicable, "
        f"{counts.get('waived', 0)} waived)",
        "",
    ])
    lines.append("Policy PASS describes this gate's rules; completeness and protected execution are separate facts.")
    partial = getattr(result, "partial_evaluated_metrics", None)
    aggregate_incomplete = any(
        subject.get("core_metric_status") in {"partial", "failed"}
        for subject in run.get("subjects", ())
    )
    if partial:
        lines.append("Partial data: a gated partial observation was evaluated for " + ", ".join(partial) + ".")
    elif partial is None:
        if aggregate_incomplete:
            lines.append("Aggregate incompleteness is recorded for this run.")
        lines.append("Gated partial observation context is unavailable in this serialized/historical result.")
    elif aggregate_incomplete:
        lines.append("Aggregate incompleteness is recorded; no gated partial observation was evaluated.")
    else:
        lines.append("No gated partial observation was evaluated.")
    lines.append("")

    # Evaluator failures lead, because they mean the gate broke rather than
    # found something, and a reader who scrolled past them would draw the wrong
    # conclusion from everything below.
    errored = [
        item for item in result.get("findings", ())
        if item["status"] == finding_module.STATUS_EVALUATION_ERROR
    ]
    if errored:
        lines.append("EVALUATION ERRORS - the gate did not complete:")
        for item in errored:
            lines.append(f"  ! {item['rule_id']}: {item['message']}")
        lines.append("")

    violated = [
        item for item in result.get("findings", ())
        if item["status"] == finding_module.STATUS_VIOLATED
    ]
    if not violated and not errored:
        lines.append("No rule fired.")
    for item in violated:
        lines.append(f"  [{item['severity']}] {item['rule_id']}")
        lines.append(f"      {item['message']}")
        lines.extend(_group_detail_lines(item, "      "))

    not_evaluable = result.get("not_evaluable") or ()
    if not_evaluable:
        lines.extend([
            "",
            "Not evaluable: the rule did NOT run, which is not a pass.",
        ])
        # `not_evaluable` is a summary projection and carries no evidence, so
        # the detail is looked up on the canonical finding it names rather than
        # re-derived. Same source, same numbers as the JSON.
        by_id = {
            item.get("finding_id"): item
            for item in result.get("findings", ())
        }
        for item in not_evaluable:
            gate = " [fails the build]" if item.get("fails_the_build") else ""
            where = item.get("subject_key") or "(run)"
            if item.get("language"):
                where = f"{where} [{item['language']}]"
            lines.append(
                f"  - {item['rule_id']} {where}: {item.get('reason')}{gate}"
            )
            canonical = by_id.get(item.get("finding_id"))
            if canonical:
                lines.extend(_group_detail_lines(canonical, "      "))

    not_applicable = [
        item for item in result.get("findings", ())
        if item["status"] == finding_module.STATUS_NOT_APPLICABLE
    ]
    if not_applicable:
        lines.extend([
            "",
            "Not applicable: nothing of this kind to measure (never a failure).",
        ])
        for item in not_applicable:
            where = item.get("subject_key") or "(run)"
            if item.get("language"):
                where = f"{where} [{item['language']}]"
            lines.append(f"  - {item['rule_id']} {where}")
            lines.extend(_group_detail_lines(item, "      "))

    waived = result.get("waived_findings") or ()
    if waived:
        lines.extend(["", "Waived (bounded, with reason and expiry):"])
        for item in waived:
            waiver = item.get("waiver") or {}
            lines.append(
                f"  - {item['rule_id']}: {waiver.get('reason')} "
                f"(expires {waiver.get('expires_on')})"
            )

    expired = result.get("expired_waivers") or ()
    if expired:
        lines.extend(["", "EXPIRED waivers, no longer suppressing anything:"])
        for waiver in expired:
            lines.append(
                f"  - {waiver['rule_id']}: expired {waiver['expires_on']}"
            )

    lines.extend([
        "",
        wrap_prose(result.get("scope_note", "")),
        "Next: metrolith explain RUN",
    ])
    return "\n".join(lines)


def render_metric_listing() -> dict[str, Any]:
    """The Policy v2 metric allowlist, as a machine-readable document.

    Rendered by ``metrolith policy metrics``. It states the operators, the
    scopes, the status semantics and the glob semantics in one place, because a
    policy author needs all four to write a rule that means what they intended.
    """
    from modules.policy.document_v2 import (
        FLOAT_EQUALITY_NOTE,
        ON_NOT_EVALUABLE,
        OPERATORS,
        OPERATOR_SYMBOLS,
        PARTIAL_DATA,
    )

    return {
        "policy_document_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
        "metrics": [item.as_dict() for item in metric_module.METRICS],
        "scopes": {
            metric_module.SCOPE_REPOSITORY: "one unit per analyzed subject",
            metric_module.SCOPE_LANGUAGE: (
                "one unit per (subject, language) the artifact records; "
                "language matching is case-insensitive"
            ),
            metric_module.SCOPE_CALLABLE: (
                "one unit per row of the per-callable complexity ledger; one of "
                "the two scopes that support `paths` / `exclude_paths`"
            ),
            metric_module.SCOPE_HOTSPOT_FILE: (
                "one unit per row of an ADMITTED standalone Hotspot document; "
                "supports `paths` / `exclude_paths` and `languages`. With no "
                "admitted evidence a rule here is `not_evaluable`, never a pass"
            ),
            metric_module.SCOPE_DUPLICATION_GROUP: (
                "one unit per clone group of an ADMITTED standalone Duplication "
                "document, for the ONE kind the metric names; supports "
                "`languages` but NOT `paths` / `exclude_paths`, because a group "
                "spans several files and the analyzed file population is "
                "already decided by the run's exclusion policy. With no "
                "admitted evidence, or with a kind the analysis did not run, a "
                "rule here is `not_evaluable`, never a pass"
            ),
        },
        "operators": {
            name: f"{OPERATOR_SYMBOLS[name]} ({meaning})"
            for name, meaning in sorted(OPERATORS.items())
        },
        "operator_note": (
            "A rule states its FAILING condition: `gt 30` fires when the "
            "observed value exceeds 30. " + FLOAT_EQUALITY_NOTE
        ),
        "options": {
            "on_not_evaluable": list(ON_NOT_EVALUABLE),
            "partial_data": list(PARTIAL_DATA),
        },
        "path_glob_note": (
            "`paths` and `exclude_paths` are fnmatch globs over the "
            "POSIX-normalized relative path, in which `*` also matches `/`. "
            "`src/*.py` therefore matches `src/a/b.py`."
        ),
        "statuses": dict(finding_module.STATUS_MEANINGS),
        "completeness": dict(metric_module.COMPLETENESS_MEANINGS),
        "threshold_note": (
            "Every threshold is USER-SUPPLIED POLICY. Metrolith ships no default "
            "threshold and no built-in v2 policy: no value below is a "
            "recommendation, and none is a research finding."
        ),
    }


__all__ = ["add_parser", "handle", "render_text", "render_metric_listing", "EXIT_PASS"]
