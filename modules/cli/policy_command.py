"""``metrolith policy`` — author, validate, inspect, or evaluate Policy documents.

    metrolith policy evaluate <run-directory> [--policy FILE] [--format json|text]
    metrolith policy rules
    metrolith policy default-policy
    metrolith policy metrics [--scope SCOPE] [--family FAMILY]
    metrolith policy init --output FILE --metric ID --operator OP
        --threshold USER_VALUE --severity SEVERITY
    metrolith policy validate FILE

Exit codes are part of the contract, so CI can act on them:

===  ==========================================
  0  no violation
  1  at least one violation
  2  usage error
  3  the run could not be read
  4  the policy document is invalid
===  ==========================================

A warning never changes the exit code. Only a rule the policy declares a
`violation` does.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from modules.presentation import wrap_prose

from modules.policy import rules as rule_module
from modules.policy.document import (
    PolicyDocumentInvalid,
    default_policy,
    load_policy_file,
)
from modules.policy.engine import (
    EXIT_PASS,
    EXIT_POLICY_INVALID,
    EXIT_UNREADABLE_RUN,
    EXIT_USAGE,
    EXIT_VIOLATION,
    RunNotEvaluable,
    evaluate,
)


def add_parser(sub) -> None:
    parser = sub.add_parser(
        "policy",
        help="Author, validate, inspect, or evaluate Policy documents",
        description=(
            "Create and validate current Policy documents, then use metrolith check. "
            "policy evaluate consumes legacy Policy v1 only. "
            "Every metric threshold is selected by the user; Metrolith ships no "
            "default threshold and makes no architecture-quality claim."
        ),
    )
    inner = parser.add_subparsers(dest="policy_command", required=True)

    evaluate_parser = inner.add_parser(
        "evaluate", help="Evaluate legacy Policy v1 only; use check for current Policy",
        description=(
            "Legacy Policy v1 integrity evaluation. Use metrolith check RUN --policy "
            "FILE for current Policy documents, including policy init output. "
            "Finalized/published runs are locally retained evidence, not uploads. "
            "Exits: 0 pass, 1 violation, 2 usage, 3 unreadable run, 4 invalid Policy."
        ),
    )
    evaluate_parser.add_argument("run_directory", type=Path)
    evaluate_parser.add_argument(
        "--policy", type=Path,
        help="Legacy Policy v1 document. Omit for the built-in integrity policy.",
    )
    evaluate_parser.add_argument("--format", choices=("text", "json"), default="text")
    evaluate_parser.add_argument(
        "--output", type=Path, help="Write the machine-readable result here"
    )

    inner.add_parser("rules", help="List every available rule and its rationale")
    inner.add_parser(
        "default-policy", help="Print the legacy Policy v1 integrity-only default"
    )
    # The Policy v2 allowlist. It lives beside `rules` because a policy author
    # writing a v2 document needs both listings, and putting the metric listing
    # on `check` would mean asking for a run directory to read documentation.
    metrics = inner.add_parser(
        "metrics",
        help="List every metric a Policy v2 rule may gate on, and the rule vocabulary",
    )
    metrics.add_argument("--scope", help="Show only this exact metric scope")
    metrics.add_argument("--family", help="Show only this exact metric family")
    metrics.add_argument(
        "--format", choices=("json", "text"), default="json",
        help="Output format (default: json, preserving the established machine surface)",
    )

    init = inner.add_parser(
        "init",
        help="Create a strict Policy document from an explicitly selected rule",
        description="Create an explicitly authored rule. Effective partial_data=evaluate; edit options.partial_data to not_evaluable to refuse partial observations. on_not_evaluable=fail applies to observations that cannot be evaluated.",
    )
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--name", default="Local policy")
    init.add_argument("--metric", help="Metric identifier selected by the user")
    init.add_argument("--operator", help="Failing comparison operator selected by the user")
    init.add_argument("--threshold", help="Numeric threshold selected by the user")
    init.add_argument("--severity", help="Rule severity selected by the user")
    init.add_argument("--rule-id", help="Stable rule id (default: local.<metric>)")

    validate = inner.add_parser(
        "validate", help="Validate Policy syntax: exit 0 valid, 4 invalid",
        description="Validate a legacy or current Policy document without evaluating a run. Exit 0 valid, 4 invalid; parser usage errors exit 2.",
    )
    validate.add_argument("policy", type=Path)
    return parser


def handle(args) -> int:
    command = getattr(args, "policy_command", None)

    if command == "rules":
        print(json.dumps(
            {
                "rules": [rule.as_dict() for rule in rule_module.RULES],
                "domains": list(rule_module.DOMAINS),
                "severities": list(rule_module.SEVERITIES),
                "scope_note": (
                    "These are the legacy Policy v1 integrity rules. Current "
                    "Policy 2.2.0 also supports explicitly user-authored metric "
                    "rules; Metrolith supplies no threshold."
                ),
            },
            indent=2, sort_keys=True,
        ))
        return EXIT_PASS

    if command == "default-policy":
        print(json.dumps(default_policy().as_dict(), indent=2, sort_keys=True))
        return EXIT_PASS

    if command == "metrics":
        from modules.cli.check_command import render_metric_listing

        listing = render_metric_listing()
        scope = getattr(args, "scope", None)
        family = getattr(args, "family", None)
        filtered = [
            item for item in listing["metrics"]
            if (not scope or item["scope"] == scope)
            and (not family or item["family"] == family)
        ]
        listing = {**listing, "metrics": filtered, "filtered_count": len(filtered)}
        if getattr(args, "format", "json") == "json":
            print(json.dumps(listing, indent=2, sort_keys=True))
        else:
            print(_render_metrics_text(listing))
        return EXIT_PASS

    if command == "init":
        return _init_policy(args)

    if command == "validate":
        return _validate_policy(args.policy)

    if command != "evaluate":
        print("[ERROR] unknown policy subcommand")
        return EXIT_USAGE

    from modules.cli.export_output import admit, ExportOutputError
    try:
        destination = admit(args.output, overwrite=True, runs=[args.run_directory], files=[args.policy], kind="policy")
    except ExportOutputError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        policy = load_policy_file(args.policy) if args.policy else default_policy()
    except PolicyDocumentInvalid as exc:
        print(f"[ERROR] invalid policy document: {exc}")
        return EXIT_POLICY_INVALID

    try:
        result = evaluate(args.run_directory, policy)
    except RunNotEvaluable as exc:
        print(f"[ERROR] the run could not be read: {exc}")
        return EXIT_UNREADABLE_RUN
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_UNREADABLE_RUN

    if args.output:
        try:
            destination.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        except ExportOutputError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return EXIT_USAGE

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_text(result))

    return EXIT_VIOLATION if result["violation_count"] else EXIT_PASS


def _threshold(value: str) -> int | float:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--threshold must be a JSON number") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise ValueError("--threshold must be a JSON number")
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise ValueError("--threshold must be finite")
    return parsed


def _init_policy(args) -> int:
    from modules.policy import metrics as metric_module
    from modules.policy.document_v2 import (
        POLICY_DOCUMENT_V2_FORMAT_VERSION,
        load_policy_v2,
    )
    from modules.run_artifacts import atomic_write_text

    supplied = {
        "--metric": args.metric,
        "--operator": args.operator,
        "--threshold": args.threshold,
        "--severity": args.severity,
    }
    missing = [name for name, value in supplied.items() if value is None]
    if missing:
        print(
            "Outcome: Policy was not created.\n"
            "Cause: the current Policy contract requires at least one explicit "
            "rule; Metrolith will not invent a threshold or enforcement severity.\n"
            "Safe next action: supply --metric, --operator, --threshold, and "
            "--severity together.\n"
            "Reason code: policy_rule_input_required"
        )
        return EXIT_USAGE
    definition = metric_module.METRICS_BY_ID.get(args.metric)
    if definition is None:
        print(_unknown_metric_message(args.metric))
        return EXIT_POLICY_INVALID
    try:
        threshold = _threshold(args.threshold)
        rule_id = args.rule_id or "local." + args.metric.replace("_", ".")
        document = {
            "policy_document_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
            "name": args.name,
            "description": "Thresholds in this document were selected explicitly by its author.",
            "integrity_rules": {},
            "metric_rules": [
                {
                    "id": rule_id,
                    "metric": args.metric,
                    "operator": args.operator,
                    "threshold": threshold,
                    "scope": definition.scope,
                    "severity": args.severity,
                }
            ],
            "expected_contracts": {},
            "waivers": [],
        }
        validated = load_policy_v2(document)
    except (PolicyDocumentInvalid, ValueError) as exc:
        print(
            "Outcome: Policy was not created.\n"
            f"Cause: {exc}\n"
            "Was evaluation performed? no\n"
            "Safe next action: run `metrolith policy metrics --format text` and "
            "correct the explicit rule inputs.\n"
            "Reason code: policy_invalid"
        )
        return EXIT_POLICY_INVALID
    destination = args.output.expanduser().resolve(strict=False)
    if destination.exists():
        print(
            f"Outcome: Policy was not created.\nCause: output already exists: {destination}\n"
            "Safe next action: choose a new --output path.\nReason code: output_exists"
        )
        return EXIT_USAGE
    payload = json.dumps(
        validated.as_dict(), indent=2, sort_keys=True, ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    atomic_write_text(destination, payload)
    print(f"Policy created: {destination}")
    print(f"Document version: {POLICY_DOCUMENT_V2_FORMAT_VERSION}")
    print(_partial_treatment(validated))
    print("No threshold was chosen by Metrolith; every rule value came from the command line.")
    print(f'Next: metrolith policy validate "{destination}"')
    print("Then: metrolith policy metrics --format text")
    print(f'Then: metrolith check <run-directory> --policy "{destination}"')
    return EXIT_PASS


def _validate_policy(path: Path) -> int:
    from archlens_json import POLICY_JSON_LIMITS, StrictJsonError, load_file
    from modules.policy.document_v2 import load_any_policy
    from validation.artifact_io.schema_store import (
        schema_name_for_format,
        validate_document,
    )

    try:
        raw = load_file(path, source=str(path), limits=POLICY_JSON_LIMITS, expect=dict)
        policy = load_any_policy(raw)
        declared = raw.get("policy_document_format_version")
        schema_name = (
            "policy_document"
            if declared == "1.0.0"
            else schema_name_for_format("policy_document_v2", declared)
        )
        problems = validate_document(schema_name, raw, str(path))
        if problems:
            raise PolicyDocumentInvalid(str(problems[0]))
    except (OSError, StrictJsonError, PolicyDocumentInvalid, ValueError) as exc:
        print(
            "Outcome: INVALID POLICY\n"
            f"Cause: {exc}\n"
            "Was evaluation performed? no\n"
            "Safe next action: run `metrolith policy metrics --format text` and "
            "repair the document.\n"
            "Reason code: policy_invalid"
        )
        return EXIT_POLICY_INVALID
    print(f"VALID POLICY — {policy.name} (document {declared})")
    if hasattr(policy, "options"):
        print(_partial_treatment(policy))
    print("No repository evaluation was performed.")
    print(f'Next: metrolith check <run-directory> --policy "{path}"')
    return EXIT_PASS


def _partial_treatment(policy) -> str:
    return (
        f"Partial data: partial_data={policy.options.partial_data}; "
        "evaluate uses partial observations; not_evaluable refuses them. "
        f"Not-evaluable treatment: on_not_evaluable={policy.options.on_not_evaluable}."
    )


def _unknown_metric_message(metric: str) -> str:
    import difflib
    from modules.policy import metrics as metric_module

    suggestions = difflib.get_close_matches(
        metric, sorted(metric_module.METRICS_BY_ID), n=5, cutoff=0.35
    )
    suffix = ", ".join(suggestions) if suggestions else "none"
    return (
        "Outcome: Policy metric is unknown.\n"
        f"Cause: {metric!r} is not a supported metric identifier.\n"
        f"Relevant identifiers: {suffix}\n"
        "Safe next action: metrolith policy metrics --format text\n"
        "Reason code: policy_metric_unknown"
    )


def _render_metrics_text(listing: dict[str, Any]) -> str:
    lines = [
        f"Metrolith Policy metrics — document {listing['policy_document_format_version']}",
        f"{len(listing['metrics'])} matching metric(s)",
        "",
    ]
    for item in listing["metrics"]:
        lines.append(f"{item['metric']}  [{item['scope']} / {item['family']}]")
        lines.append(wrap_prose(item["definition"], indent="  "))
        lines.append(f"  source: {item['source']}")
    lines.extend(["", wrap_prose(listing["threshold_note"])])
    return "\n".join(lines)


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"Metrolith policy {result['policy_result_format_version']}",
        f"policy: {result['policy_name']}",
        f"run:    {result['run_id']}",
        "",
        f"{result['violation_count']} violation(s), "
        f"{result['warning_count']} warning(s), "
        f"{len(result['waived_findings'])} waived",
        "",
    ]
    if not result["findings"]:
        lines.append("No findings.")
    for finding in result["findings"]:
        subject = f" [{finding['subject_key']}]" if finding.get("subject_key") else ""
        lines.append(f"  [{finding['severity']}] {finding['rule_id']}{subject}")
        lines.append(f"      {finding['detail']}")
    if result["waived_findings"]:
        lines.append("")
        lines.append("Waived (bounded, with reason and expiry):")
        for item in result["waived_findings"]:
            waiver = item["waiver"]
            lines.append(
                f"  - {item['rule_id']}: {waiver['reason']} "
                f"(expires {waiver['expires_on']})"
            )
    if result["expired_waivers"]:
        lines.append("")
        lines.append("EXPIRED waivers, no longer suppressing anything:")
        for waiver in result["expired_waivers"]:
            lines.append(
                f"  - {waiver['rule_id']}: expired {waiver['expires_on']}"
            )
    lines.append("")
    lines.append(wrap_prose(result["scope_note"]))
    return "\n".join(lines)
