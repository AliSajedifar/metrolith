"""The canonical finding: one deterministic record of one rule at one place.

This is the single representation every consumer reads. A later SARIF writer
must be able to produce a complete report from a list of these **without
re-evaluating the policy or re-reading the run**, which is why each finding
carries its own subject, location, observed value, operator, threshold, status
and provenance rather than a pointer back into the evaluation.

*(SARIF is deferred; this model exists so writing it later is a projection
rather than a second evaluator.)*

**Identity is semantic, never incidental.** :func:`finding_identity` digests the
rule, the metric, the scope and the location — and nothing else. Not a
timestamp, not a UUID, not an index into an array, and deliberately not the
observed value or the threshold: the same problem must keep one identity while
the number drifts run to run, and re-tuning a threshold must not orphan a
finding's history.

**Ordering is total and stable.** Two evaluations of one run produce the same
bytes, because every component of the sort key is a property of the rule or of
the artifact, never of the evaluation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from modules.policy import metrics as metric_module
from modules.policy.document_v2 import OPERATOR_SYMBOLS

#: Prefix on every finding id. Versioned so a future change to what identity
#: covers is visible in the id itself rather than silently renaming every
#: finding a consumer had already seen.
FINDING_ID_PREFIX = "alf1"

#: The rule fired: the observed value satisfies the rule's failing condition.
STATUS_VIOLATED = "violated"
#: The rule was evaluated and did not fire. Counted, never enumerated.
STATUS_PASSED = "passed"
#: The rule could not be evaluated: the measurement is unavailable, absent, or
#: partial under a policy that refuses partial data. **Never a silent pass.**
STATUS_NOT_EVALUABLE = "not_evaluable"
#: There was nothing of this kind to measure. A complete answer, not a gap, and
#: never a failure.
STATUS_NOT_APPLICABLE = "not_applicable"
#: The evaluator itself failed on this unit. A defect, reported as one.
STATUS_EVALUATION_ERROR = "evaluation_error"

STATUSES: tuple[str, ...] = (
    STATUS_VIOLATED, STATUS_PASSED, STATUS_NOT_EVALUABLE,
    STATUS_NOT_APPLICABLE, STATUS_EVALUATION_ERROR,
)

#: Statuses that become findings. A pass is counted per rule and never
#: enumerated: one row per passing callable would be megabytes of output saying
#: that nothing is wrong.
REPORTED_STATUSES: frozenset[str] = frozenset({
    STATUS_VIOLATED, STATUS_NOT_EVALUABLE, STATUS_NOT_APPLICABLE,
    STATUS_EVALUATION_ERROR,
})

STATUS_MEANINGS: dict[str, str] = {
    STATUS_VIOLATED: "the observed value satisfies the rule's failing condition",
    STATUS_PASSED: "the rule was evaluated and did not fire",
    STATUS_NOT_EVALUABLE: (
        "the rule could not be evaluated because the measurement it needs is "
        "not available; this is never read as a pass"
    ),
    STATUS_NOT_APPLICABLE: (
        "there was nothing of this kind to measure; a complete answer, not a gap"
    ),
    STATUS_EVALUATION_ERROR: "the evaluator failed on this unit",
}

#: Why a rule was not evaluable. A typed reason, so a consumer never has to
#: parse the message to find out.
REASON_MEASUREMENT_UNAVAILABLE = "measurement_unavailable"
REASON_MEASUREMENT_ABSENT = "measurement_absent"
REASON_CALLABLE_ARTIFACT_ABSENT = "callable_artifact_absent"
REASON_PARTIAL_DATA_REFUSED = "partial_data_refused"
REASON_NOTHING_TO_MEASURE = "nothing_to_measure"
#: Evidence-backed families only. Each names a different fact about the INPUT,
#: and none of them is ever a pass: a rule that had nothing to read did not run.
REASON_EVIDENCE_NOT_SUPPLIED = "evidence_not_supplied"
REASON_EVIDENCE_NOT_ADMITTED = "evidence_not_admitted"
REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT = "evidence_does_not_cover_subject"
#: The admitted evidence covers this subject, but the analysis was run without
#: the duplication kind this rule reads. Distinct from every other absence: the
#: measurement did not fail and there was nothing wrong with the document -- the
#: operator's own `--kind` flag excluded it.
REASON_EVIDENCE_KIND_NOT_REQUESTED = "evidence_kind_not_requested"
#: A field the enabled rule actually consumed violated its persisted contract.
#: This is an evaluator error, not an unavailable measurement and never a pass.
REASON_MALFORMED_CONSUMED_FIELD = "malformed_consumed_field"

REASON_MEANINGS: dict[str, str] = {
    REASON_MEASUREMENT_UNAVAILABLE: (
        "the measurement was attempted and produced nothing usable, or the "
        "persisted value is null; it is never read as zero"
    ),
    REASON_MEASUREMENT_ABSENT: (
        "this artifact generation recorded nothing of this kind at all; nothing "
        "was attempted, which is not the same fact as a failed measurement"
    ),
    REASON_CALLABLE_ARTIFACT_ABSENT: (
        "the run published no per-callable complexity ledger, so no "
        "callable-scope rule has anything to read"
    ),
    REASON_PARTIAL_DATA_REFUSED: (
        "the observation is partial and the policy sets "
        "options.partial_data = not_evaluable"
    ),
    REASON_NOTHING_TO_MEASURE: (
        "the scope recorded no measured population for this metric"
    ),
    REASON_EVIDENCE_NOT_SUPPLIED: (
        "this rule reads a standalone evidence document and none was supplied; "
        "that is an absence of input and is never read as a count of zero"
    ),
    REASON_EVIDENCE_NOT_ADMITTED: (
        "a standalone evidence document was supplied but was not admitted, so "
        "its figures describe something other than this run and are unreachable"
    ),
    REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT: (
        "the admitted evidence document says nothing about this subject; that "
        "is unknown, not a measured absence of findings"
    ),
    REASON_EVIDENCE_KIND_NOT_REQUESTED: (
        "the analysis was run without this duplication kind, so nothing of "
        "this kind was attempted; that is an absence of measurement, not an "
        "absence of duplicates"
    ),
    REASON_MALFORMED_CONSUMED_FIELD: (
        "a persisted field consumed by an enabled rule is missing, has the "
        "wrong type, or carries an unsupported status"
    ),
}

_SEVERITY_RANK: dict[str, int] = {"violation": 0, "warning": 1, "info": 2}

#: A v1 integrity rule, evaluated by the existing v1 rule engine.
KIND_INTEGRITY = "integrity"
#: A v2 metric threshold rule.
KIND_METRIC = "metric"

KINDS: tuple[str, ...] = (KIND_INTEGRITY, KIND_METRIC)

#: Scope of a run-wide integrity fact that names no subject — "the run did not
#: finalize", "a mandatory output failed". Deliberately not one of the three
#: rule-selectable scopes in :mod:`modules.policy.metrics`: no metric rule may
#: ask for it, because no metric is measured there.
SCOPE_RUN = "run"

#: Ordering rank over every scope a finding can carry, run-wide facts first.
_SCOPE_RANK: dict[str, int] = {
    SCOPE_RUN: 0,
    metric_module.SCOPE_REPOSITORY: 1,
    metric_module.SCOPE_LANGUAGE: 2,
    metric_module.SCOPE_CALLABLE: 3,
    metric_module.SCOPE_HOTSPOT_FILE: 4,
    metric_module.SCOPE_DUPLICATION_GROUP: 5,
}

FINDING_SCOPES: tuple[str, ...] = tuple(_SCOPE_RANK)


def finding_identity(
    *,
    rule_id: str,
    metric: str | None,
    scope: str,
    subject_key: str,
    language: str | None = None,
    path: str | None = None,
    callable_row_id: str | None = None,
    discriminator: str | None = None,
) -> str:
    """A deterministic id for one rule at one semantic location.

    The components are NUL-joined rather than concatenated so no two distinct
    coordinate tuples can produce one string — ``("a.b", "c")`` and
    ``("a", "b.c")`` would otherwise collide.

    ``language`` is casefolded, because the producer records core metrics in
    lowercase and complexity in display case for the same language, and one
    language must not yield two identities.

    ``discriminator`` separates the few integrity rules that legitimately emit
    more than one finding for one subject — one per diagnostic category, one per
    pinned contract field. It carries the *categorical* value only, never a
    count, so the id does not move when the count does.
    """
    parts = [
        FINDING_ID_PREFIX, rule_id, metric or "", scope, subject_key,
        (language or "").casefold(), path or "", callable_row_id or "",
        discriminator or "",
    ]
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
    return f"{FINDING_ID_PREFIX}:{digest[:32]}"


def compare(observed: int | float, operator: str, threshold: int | float) -> bool:
    """Whether the rule fires: ``observed <operator> threshold``.

    The rule states the FAILING condition, so ``gt 30`` fires when the observed
    value exceeds 30. Every operator is a plain numeric comparison; there is no
    tolerance, no rounding and no coercion here, because each of those would be
    an unstated policy decision.
    """
    if operator == "gt":
        return observed > threshold
    if operator == "gte":
        return observed >= threshold
    if operator == "lt":
        return observed < threshold
    if operator == "lte":
        return observed <= threshold
    if operator == "eq":
        return observed == threshold
    if operator == "ne":
        return observed != threshold
    raise ValueError(f"unsupported operator {operator!r}")


def requires_failure(
    *, severity: Any, status: Any, fail_on_not_evaluable: bool
) -> bool:
    """The canonical severity/status-to-exit-1 decision for every Check finding.

    Warning and info findings remain visible but can never fail the process.
    Evaluation errors are handled separately as exit 2.
    """

    return severity == "violation" and (
        status == STATUS_VIOLATED
        or (status == STATUS_NOT_EVALUABLE and fail_on_not_evaluable)
    )


def _render_number(value: Any) -> str:
    """Render a number the same way every time.

    A float that is exactly integral renders without its trailing ``.0`` so a
    threshold of ``30`` and a threshold of ``30.0`` read identically in a
    message; the JSON payload keeps the value the user actually wrote.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class Finding:
    """One deterministic finding. Every field is a fact, not a formatting choice."""

    finding_id: str
    rule_id: str
    severity: str
    status: str
    scope: str
    subject_key: str
    message: str
    #: `integrity` or `metric`. An integrity finding carries no metric,
    #: operator or threshold — a v1 rule is a predicate over the measurement
    #: process, not a comparison against a number, and filling those fields with
    #: placeholders would invent a threshold where the whole point is that none
    #: exists.
    kind: str = KIND_METRIC
    metric: str | None = None
    operator: str | None = None
    threshold: int | float | None = None
    #: Grouping domain, for integrity findings only (v1's seven domains).
    domain: str | None = None
    repository_url: str | None = None
    language: str | None = None
    path: str | None = None
    callable_row_id: str | None = None
    callable_qualified_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    observed_value: int | float | None = None
    #: The persisted status that gated the read, and the field it came from.
    value_status: Any = None
    value_status_field: str | None = None
    #: `complete` / `partial` / `unavailable` / `not_applicable` / `absent`.
    data_completeness: str = metric_module.COMPLETENESS_COMPLETE
    #: Typed reason, present whenever the status is not `violated`.
    reason: str | None = None
    #: Extra evidence: the collapsed row count for a callable-scope
    #: non-evaluability, the evaluator error text, and nothing else.
    evidence: Mapping[str, Any] = field(default_factory=dict)
    #: Where the number came from, and under which contracts.
    provenance: Mapping[str, Any] = field(default_factory=dict)
    #: The waiver suppressing this finding, when one does.
    waiver: Mapping[str, Any] | None = None

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK.get(self.severity, len(_SEVERITY_RANK))

    def sort_key(self) -> tuple:
        """Total, stable ordering.

        Severity leads because it is a property of the rule rather than of the
        run, so leading with it does not make the order depend on what happened
        to be measured. ``finding_id`` is the final tiebreak, which makes the
        order total even if two units somehow share every other coordinate.
        """
        return (
            self.severity_rank,
            self.rule_id,
            self.subject_key,
            _SCOPE_RANK.get(self.scope, len(_SCOPE_RANK)),
            (self.language or "").casefold(),
            self.path or "",
            self.start_line if self.start_line is not None else -1,
            self.callable_row_id or "",
            self.finding_id,
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "kind": self.kind,
            "severity": self.severity,
            "status": self.status,
            "scope": self.scope,
            "domain": self.domain,
            "metric": self.metric,
            "operator": self.operator,
            "operator_symbol": (
                OPERATOR_SYMBOLS[self.operator] if self.operator else None
            ),
            "threshold": self.threshold,
            "subject_key": self.subject_key,
            "repository_url": self.repository_url,
            "language": self.language,
            "path": self.path,
            "callable_row_id": self.callable_row_id,
            "callable_qualified_name": self.callable_qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "observed_value": self.observed_value,
            "value_status": (
                None if self.value_status is None else str(self.value_status)
            ),
            "value_status_field": self.value_status_field,
            "data_completeness": self.data_completeness,
            "reason": self.reason,
            "message": self.message,
            "evidence": dict(sorted(self.evidence.items())),
            "provenance": dict(sorted(self.provenance.items())),
        }
        if self.waiver is not None:
            payload["waiver"] = dict(sorted(self.waiver.items()))
        return payload


def violation_message(
    *,
    metric: str,
    observed: int | float,
    operator: str,
    threshold: int | float,
    location: str,
    data_completeness: str,
    custom: str | None = None,
) -> str:
    """The deterministic sentence for a firing rule.

    A partial observation says so in the sentence. A reader who sees only the
    message must still learn that the number is an observation rather than a
    verified complete value.
    """
    if custom:
        return custom
    caveat = (
        " (partial observation, not a verified complete value)"
        if data_completeness == metric_module.COMPLETENESS_PARTIAL else ""
    )
    return (
        f"{metric} is {_render_number(observed)} at {location}; the policy "
        f"fails when the value is {OPERATOR_SYMBOLS[operator]} "
        f"{_render_number(threshold)}{caveat}"
    )


def not_evaluable_message(
    *,
    metric: str,
    location: str,
    reason: str,
    row_count: int | None = None,
    unit: str = "callable",
) -> str:
    """The deterministic sentence for a rule that could not run.

    It states that the rule did not run, never that it passed. That distinction
    is the whole reason this status exists.

    ``unit`` names what was collapsed. It defaults to ``callable`` so every
    existing caller's sentence is byte-identical to before; a scope whose unit
    is something else passes its own noun rather than reporting a count of
    callables that were never involved.
    """
    collapsed = (
        f" across {row_count} {unit}(s)"
        if row_count is not None and row_count > 0 else ""
    )
    return (
        f"{metric} could not be evaluated at {location}{collapsed}: "
        f"{REASON_MEANINGS.get(reason, reason)}. The rule did not run; this is "
        f"not a pass."
    )


def not_applicable_message(
    *,
    metric: str,
    location: str,
    row_count: int | None = None,
    unit: str = "callable",
) -> str:
    collapsed = (
        f" across {row_count} {unit}(s)"
        if row_count is not None and row_count > 0 else ""
    )
    return (
        f"{metric} does not apply at {location}{collapsed}: there was nothing of "
        f"this kind to measure. This is a complete answer, not a gap."
    )


def location_label(
    *,
    scope: str,
    subject_key: str,
    language: str | None = None,
    path: str | None = None,
    qualified_name: str | None = None,
    start_line: int | None = None,
) -> str:
    """One human-readable location, rendered identically everywhere."""
    if scope == metric_module.SCOPE_LANGUAGE:
        return f"{subject_key} [{language}]"
    if scope == metric_module.SCOPE_HOTSPOT_FILE:
        # A hotspot statement is about a whole file, so no line is rendered.
        # Inventing one would imply the analysis pointed at a place in the file.
        return subject_key if path is None else f"{subject_key} {path}"
    if scope in (
        metric_module.SCOPE_CALLABLE, metric_module.SCOPE_DUPLICATION_GROUP
    ):
        # A clone group renders its FIRST occurrence, which is where the finding
        # points. The remaining occurrences are in the finding's evidence; a
        # group spans several places by definition and no single label can say
        # so honestly.
        if path is None:
            return subject_key
        where = f"{subject_key} {path}"
        if start_line is not None:
            where = f"{where}:{start_line}"
        if qualified_name:
            where = f"{where} {qualified_name}"
        return where
    return subject_key


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda item: item.sort_key())
