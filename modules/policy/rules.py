"""Policy v1 rules: integrity, diagnostics and reproducibility only.

**What v1 deliberately is.** Rules over facts Metrolith already establishes with
confidence — whether an artifact is valid, whether a run finished, whether a
required parser was available, whether a contract matches, whether a diagnostic
vocabulary drifted. Every one of these is a statement about the *measurement
process*, and Metrolith can answer each without interpretation.

**What v1 deliberately is not.** There is no metric threshold here, and none may
be added: no "LOC changed by more than X%", no "methods increased by Y", no
"architecture quality worsened". Gating on a metric delta before differential
validation has established what a delta means would be gating on an unvalidated
number, and a threshold invented to look reasonable is still invented. Metric
rules are Policy v2 and remain blocked on broader evidence, including Layer 3.

**No architecture-quality claim is derived from any threshold**, in v1 or later.

Each rule is a pure function of an evaluation context assembled once, so the
rule set is deterministic, order-independent, and cheap to extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

#: Severities. A rule's severity decides the exit code, not whether it fires.
SEVERITY_VIOLATION = "violation"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITIES = (SEVERITY_VIOLATION, SEVERITY_WARNING, SEVERITY_INFO)

#: Domains, used to group findings in the result and to scope waivers.
DOMAIN_ARTIFACT = "artifact_integrity"
DOMAIN_RUN = "run_integrity"
DOMAIN_CAPABILITY = "capability"
DOMAIN_DIAGNOSTICS = "diagnostics"
DOMAIN_CONTRACT = "contract_compatibility"
DOMAIN_MEASUREMENT = "measurement_completeness"
DOMAIN_REPRODUCIBILITY = "reproducibility"

DOMAINS = (
    DOMAIN_ARTIFACT, DOMAIN_RUN, DOMAIN_CAPABILITY, DOMAIN_DIAGNOSTICS,
    DOMAIN_CONTRACT, DOMAIN_MEASUREMENT, DOMAIN_REPRODUCIBILITY,
)


@dataclass(frozen=True)
class Finding:
    """One rule firing, always carrying the evidence it fired on."""

    rule_id: str
    domain: str
    detail: str
    subject_key: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, severity: str) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "domain": self.domain,
            "severity": severity,
            "subject_key": self.subject_key,
            "detail": self.detail,
            "evidence": dict(sorted(self.evidence.items())),
        }


@dataclass(frozen=True)
class Rule:
    """One policy rule: what it checks, why, and how severe it is by default."""

    identifier: str
    domain: str
    title: str
    rationale: str
    default_severity: str
    evaluate: Callable[[Any], Iterable[Finding]]
    #: Rules that are only meaningful when the policy explicitly opts in, such
    #: as demanding complete measurement of every subject.
    default_enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.identifier,
            "domain": self.domain,
            "title": self.title,
            "rationale": self.rationale,
            "default_severity": self.default_severity,
            "default_enabled": self.default_enabled,
        }


# --------------------------------------------------------------------------
# Rule implementations. Each takes the assembled context and yields findings.
# --------------------------------------------------------------------------

def _artifact_invalid(context) -> Iterable[Finding]:
    report = context.schema_report
    if report.get("result") == "invalid":
        yield Finding(
            "artifact.schema_invalid", DOMAIN_ARTIFACT,
            "the run's artifacts do not satisfy the contract they declare",
            evidence={
                "result": report.get("result"),
                "schema_contract_evaluated": report.get("schema_contract_evaluated"),
                "violation_count": report.get("violation_count"),
                "first_violations": report.get("violations", [])[:5],
            },
        )


def _waiver_required_by_native_artifact(context) -> Iterable[Finding]:
    """A *new* artifact needing a 1.5 compatibility waiver is a producer defect.

    The waivers exist because Artifact Schema 1.5.0 is frozen and defective.
    A current-generation artifact must satisfy its own contract literally, so a
    waiver being required here means either an incomplete correction or a new
    producer defect — never something to absorb quietly.
    """
    report = context.schema_report
    declared = str(report.get("schema_contract_evaluated") or "")
    accepted = report.get("accepted_compatibility_exception_count") or 0
    if accepted and declared and not declared.startswith("1.5"):
        yield Finding(
            "artifact.compatibility_waiver_required_by_current_generation",
            DOMAIN_ARTIFACT,
            f"an artifact declaring {declared} required {accepted} Artifact "
            f"Schema 1.5 compatibility waiver(s); a current-generation artifact "
            f"must satisfy its own contract literally",
            evidence={
                "declared_contract": declared,
                "waiver_count": accepted,
                "waivers": report.get("accepted_compatibility_exceptions", [])[:5],
            },
        )


def _run_integrity_failed(context) -> Iterable[Finding]:
    status = context.status.get("status")
    if status not in {"completed", "completed_with_errors"}:
        yield Finding(
            "run.integrity_failed", DOMAIN_RUN,
            f"the run did not reach a successful terminal state (status "
            f"{status!r})",
            evidence={"run_status": status, "lifecycle": context.lifecycle},
        )


def _mandatory_output_failed(context) -> Iterable[Finding]:
    failures = (context.manifest.get("output_failures") or {}).get("mandatory") or []
    if failures:
        yield Finding(
            "run.mandatory_output_failure", DOMAIN_RUN,
            f"{len(failures)} mandatory output(s) failed during finalization",
            evidence={"failures": list(failures)[:5]},
        )


def _parser_capability_unavailable(context) -> Iterable[Finding]:
    """A language was measured while its parser reported unavailable.

    One missing grammar is a capability fact, not a per-file accident, so it is
    reported once with the languages it affected.
    """
    for repository in context.repositories:
        statuses = (repository.get("metrics") or {}).get(
            "parser_status_by_language"
        ) or {}
        unavailable = sorted(
            language for language, state in statuses.items()
            if state in {"unavailable", "not_available", "missing"}
        )
        if unavailable:
            yield Finding(
                "capability.parser_unavailable", DOMAIN_CAPABILITY,
                f"parser capability unavailable for: {', '.join(unavailable)}",
                subject_key=context.subject_key_of(repository),
                evidence={"languages": unavailable},
            )


def _parser_execution_failure(context) -> Iterable[Finding]:
    counts: dict[str, int] = {}
    for row in context.errors:
        category = str(row.get("error_category") or "")
        if category in {"parser_execution_failure", "syntax_failed"}:
            counts[category] = counts.get(category, 0) + 1
    for category, count in sorted(counts.items()):
        yield Finding(
            "diagnostics.parser_execution_failure", DOMAIN_DIAGNOSTICS,
            f"{count} {category} diagnostic(s) recorded",
            evidence={"error_category": category, "count": count},
        )


def _unknown_diagnostic_category(context) -> Iterable[Finding]:
    """Producer/consumer vocabulary drift.

    A healthy analysis reporting an unrecognized category means the producer
    emitted a value the consumer cannot name, which makes every downstream
    count over that vocabulary untrustworthy.
    """
    for repository in context.repositories:
        unknown = repository.get("unknown_categories") or []
        if unknown:
            yield Finding(
                "diagnostics.unknown_category", DOMAIN_DIAGNOSTICS,
                f"unrecognized diagnostic value(s): {sorted(unknown)[:5]}",
                subject_key=context.subject_key_of(repository),
                evidence={"unknown_categories": sorted(unknown)[:20]},
            )


def _contract_incompatible(context) -> Iterable[Finding]:
    """Contract versions must match what the policy expects.

    Only fires when the policy states an expectation. Silence here means the
    policy did not pin a contract, not that the contracts agree.
    """
    expected = context.expected_contracts
    if not expected:
        return
    for field_name, wanted in sorted(expected.items()):
        actual = context.manifest.get(field_name)
        if str(actual) != str(wanted):
            yield Finding(
                "contract.incompatible", DOMAIN_CONTRACT,
                f"{field_name} is {actual!r}; the policy requires {wanted!r}",
                evidence={"field": field_name, "actual": actual, "expected": wanted},
            )


def _artifact_compatibility_state(context) -> Iterable[Finding]:
    verdict = context.schema_report.get("artifact_schema_compatibility") or {}
    state = verdict.get("state")
    if state and state != "supported":
        yield Finding(
            "contract.artifact_generation_unsupported", DOMAIN_CONTRACT,
            f"the artifact generation is {state!r}: {verdict.get('reason')}",
            evidence=dict(verdict),
        )


def _measurement_incomplete(context) -> Iterable[Finding]:
    """Opt-in. Only meaningful when the policy demands complete measurement."""
    for repository in context.repositories:
        status = repository.get("analysis_status")
        if status != "complete":
            yield Finding(
                "measurement.incomplete", DOMAIN_MEASUREMENT,
                f"analysis_status is {status!r} while the policy requires "
                f"complete measurement",
                subject_key=context.subject_key_of(repository),
                evidence={
                    "analysis_status": status,
                    "partial_origin": repository.get("partial_origin"),
                    "core_metric_status": repository.get("core_metric_status"),
                },
            )


def _measurement_unavailable_metric(context) -> Iterable[Finding]:
    """A null aggregate metric means unavailable, and is never read as zero."""
    for repository in context.repositories:
        aggregate = (repository.get("metrics") or {}).get("aggregate") or {}
        missing = sorted(
            name for name in (
                "lines_of_code", "source_files", "classes_structs",
                "methods_functions",
            )
            if aggregate.get(name) is None
        )
        if missing:
            yield Finding(
                "measurement.metric_unavailable", DOMAIN_MEASUREMENT,
                f"metric(s) unavailable (null, not zero): {', '.join(missing)}",
                subject_key=context.subject_key_of(repository),
                evidence={"unavailable_metrics": missing},
            )


def _provenance_incomplete(context) -> Iterable[Finding]:
    """Opt-in. Benchmark-of-record runs need complete profiler provenance."""
    manifest = context.manifest
    problems = []
    if not manifest.get("profiler_git_commit_sha"):
        problems.append("profiler_git_commit_sha is absent")
    if manifest.get("profiler_git_dirty"):
        problems.append("the profiler working tree was dirty")
    if problems:
        yield Finding(
            "reproducibility.provenance_incomplete", DOMAIN_REPRODUCIBILITY,
            "; ".join(problems),
            evidence={
                "profiler_git_commit_sha": manifest.get("profiler_git_commit_sha"),
                "profiler_git_dirty": manifest.get("profiler_git_dirty"),
            },
        )


def _structural_read_errors(context) -> Iterable[Finding]:
    if context.structural_errors:
        yield Finding(
            "artifact.structurally_unreadable", DOMAIN_ARTIFACT,
            f"{len(context.structural_errors)} artifact(s) could not be read "
            f"structurally",
            evidence={"errors": [str(item) for item in context.structural_errors][:5]},
        )


RULES: tuple[Rule, ...] = (
    Rule(
        "artifact.schema_invalid", DOMAIN_ARTIFACT,
        "Artifacts satisfy the contract they declare",
        "An artifact that violates its own published contract cannot be relied "
        "on by any consumer, and validity is judged against the declared "
        "contract rather than the newest.",
        SEVERITY_VIOLATION, _artifact_invalid,
    ),
    Rule(
        "artifact.structurally_unreadable", DOMAIN_ARTIFACT,
        "Every artifact is structurally readable",
        "A document that cannot be decoded is worse than an invalid one: no "
        "downstream check can even be attempted.",
        SEVERITY_VIOLATION, _structural_read_errors,
    ),
    Rule(
        "artifact.compatibility_waiver_required_by_current_generation",
        DOMAIN_ARTIFACT,
        "A current-generation artifact needs no compatibility waiver",
        "The 1.5 waivers exist for a frozen, defective schema. A new artifact "
        "requiring one indicates an incomplete correction or a new producer "
        "defect.",
        SEVERITY_VIOLATION, _waiver_required_by_native_artifact,
    ),
    Rule(
        "run.integrity_failed", DOMAIN_RUN,
        "The run reached a successful terminal state",
        "A run that did not finalize has not published a validated terminal "
        "state, so its outputs are not authoritative.",
        SEVERITY_VIOLATION, _run_integrity_failed,
    ),
    Rule(
        "run.mandatory_output_failure", DOMAIN_RUN,
        "No mandatory output failed during finalization",
        "A missing mandatory artifact means the run is incomplete regardless of "
        "the status it reports.",
        SEVERITY_VIOLATION, _mandatory_output_failed,
    ),
    Rule(
        "capability.parser_unavailable", DOMAIN_CAPABILITY,
        "Every parser a measurement required was available",
        "A missing grammar produces one capability fact, and measuring around "
        "it silently would understate every entity metric for that language.",
        SEVERITY_VIOLATION, _parser_capability_unavailable,
    ),
    Rule(
        "diagnostics.parser_execution_failure", DOMAIN_DIAGNOSTICS,
        "No parser execution failures were recorded",
        "Files the parser could not process contribute nothing, so their "
        "absence from the totals is invisible without this rule.",
        SEVERITY_WARNING, _parser_execution_failure,
    ),
    Rule(
        "diagnostics.unknown_category", DOMAIN_DIAGNOSTICS,
        "Every diagnostic value is one the consumer recognizes",
        "Producer/consumer vocabulary drift made a healthy analysis report a "
        "false unknown category once already; it must be visible, not absorbed.",
        SEVERITY_VIOLATION, _unknown_diagnostic_category,
    ),
    Rule(
        "contract.incompatible", DOMAIN_CONTRACT,
        "Contract versions match what the policy requires",
        "Metric values defined by different contracts are not comparable, so a "
        "policy that pins contracts must be able to say when one drifted.",
        SEVERITY_VIOLATION, _contract_incompatible,
    ),
    Rule(
        "contract.artifact_generation_unsupported", DOMAIN_CONTRACT,
        "The artifact generation is supported by this build",
        "An unsupported or future generation is readable only by accident; no "
        "conclusion drawn from it is trustworthy.",
        SEVERITY_VIOLATION, _artifact_compatibility_state,
    ),
    Rule(
        "measurement.metric_unavailable", DOMAIN_MEASUREMENT,
        "No aggregate metric is unavailable",
        "A null metric means unavailable and is never zero; a consumer that "
        "sums it would silently understate the result.",
        SEVERITY_WARNING, _measurement_unavailable_metric,
    ),
    Rule(
        "measurement.incomplete", DOMAIN_MEASUREMENT,
        "Every subject was measured completely",
        "Only meaningful where the policy explicitly demands complete "
        "measurement; partial analysis is a normal, honest outcome elsewhere.",
        SEVERITY_VIOLATION, _measurement_incomplete,
        default_enabled=False,
    ),
    Rule(
        "reproducibility.provenance_incomplete", DOMAIN_REPRODUCIBILITY,
        "Profiler provenance is complete",
        "A benchmark-of-record run must be attributable to an exact Metrolith "
        "commit with a clean tree; development runs legitimately are not.",
        SEVERITY_VIOLATION, _provenance_incomplete,
        default_enabled=False,
    ),
)

RULES_BY_ID: dict[str, Rule] = {rule.identifier: rule for rule in RULES}
