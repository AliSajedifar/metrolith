"""Deterministic policy evaluation over one published run.

Evaluation is a pure function of the run's own artifacts and the policy
document. Nothing is re-measured, no source is parsed, and no metric is
recomputed: a policy that measured would be a second measurement path.

Ordering is fixed (domain, rule id, subject key, detail) so two evaluations of
the same run produce byte-identical results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from modules.policy import rules as rule_module
from modules.policy.document import PolicyDocument, default_policy

POLICY_RESULT_FORMAT_VERSION = "1.0.0"

#: Documented exit codes. Stable, and deliberately distinct from `compare`'s.
EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_USAGE = 2
EXIT_UNREADABLE_RUN = 3
EXIT_POLICY_INVALID = 4


@dataclass
class EvaluationContext:
    """Every fact the rules may read, assembled once from the artifacts."""

    run_directory: Path
    manifest: dict[str, Any]
    status: dict[str, Any]
    repositories: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, Any], ...]
    schema_report: dict[str, Any]
    structural_errors: tuple[Any, ...]
    lifecycle: str | None
    expected_contracts: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def subject_key_of(repository: dict[str, Any]) -> str:
        from modules.subject import subject_key_of

        return subject_key_of(dict(repository))


class RunNotEvaluable(RuntimeError):
    """The run cannot be read well enough to evaluate any rule."""


def build_context(
    run_directory: Path, policy: PolicyDocument
) -> EvaluationContext:
    from modules.cli.validate_command import schema_only_report
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    run_directory = Path(run_directory)
    try:
        view = open_run(run_directory)
        manifest = dict(view.manifest)
        status = dict(view.status)
        repositories = tuple(dict(item) for item in view.repositories)
        errors = tuple(dict(item) for item in view.errors)
        structural = tuple(view.structural_errors)
        lifecycle = getattr(view.lifecycle, "value", None)
    except ArtifactStructureError as exc:
        raise RunNotEvaluable(str(exc)) from exc

    return EvaluationContext(
        run_directory=run_directory,
        manifest=manifest,
        status=status,
        repositories=repositories,
        errors=errors,
        schema_report=schema_only_report(run_directory),
        structural_errors=structural,
        lifecycle=lifecycle,
        expected_contracts=dict(policy.expected_contracts),
    )


def evaluate(
    run_directory: Path, policy: PolicyDocument | None = None, *,
    today: date | None = None,
) -> dict[str, Any]:
    """Evaluate one run against one policy. Machine-readable result."""
    policy = policy or default_policy()
    today = today or date.today()
    context = build_context(run_directory, policy)

    findings: list[dict[str, Any]] = []
    waived: list[dict[str, Any]] = []
    evaluated: list[str] = []

    for rule in rule_module.RULES:
        severity = policy.severity_for(rule.identifier)
        if severity is None:
            continue
        evaluated.append(rule.identifier)
        for finding in rule.evaluate(context):
            waiver = next(
                (item for item in policy.waivers if item.applies_to(finding, today)),
                None,
            )
            if waiver is not None:
                waived.append({
                    **finding.as_dict(severity),
                    "waiver": waiver.as_dict(),
                })
                continue
            findings.append(finding.as_dict(severity))

    order = (lambda item: (
        item["domain"], item["rule_id"], item.get("subject_key") or "",
        item["detail"],
    ))
    findings.sort(key=order)
    waived.sort(key=order)

    expired = [
        waiver.as_dict() for waiver in policy.waivers if waiver.expired(today)
    ]

    violations = [item for item in findings if item["severity"] == "violation"]
    warnings = [item for item in findings if item["severity"] == "warning"]

    return {
        "policy_result_format_version": POLICY_RESULT_FORMAT_VERSION,
        "policy_name": policy.name,
        "run_directory": str(context.run_directory),
        "run_id": context.manifest.get("run_id"),
        "evaluated_at": today.isoformat(),
        "passed": not violations,
        "exit_code": EXIT_VIOLATION if violations else EXIT_PASS,
        "rules_evaluated": sorted(evaluated),
        "rules_not_evaluated": sorted(
            rule.identifier for rule in rule_module.RULES
            if policy.severity_for(rule.identifier) is None
        ),
        "violation_count": len(violations),
        "warning_count": len(warnings),
        "findings": findings,
        "waived_findings": waived,
        "expired_waivers": expired,
        "scope_note": (
            "Policy v1 covers integrity, diagnostics and reproducibility only. "
            "It contains no metric thresholds and makes no architecture-quality "
            "claim. Metric-regression rules are Policy v2 and remain blocked on "
            "differential-validation evidence."
        ),
        "exit_code_meanings": {
            "0": "no violation",
            "1": "at least one violation",
            "2": "usage error",
            "3": "the run could not be read",
            "4": "the policy document is invalid",
        },
    }
