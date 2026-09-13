"""Deterministic policy evaluation for ``metrolith check``.

Evaluation is a pure function of one published run bundle and one policy
document. **Nothing is re-measured.** No source is parsed, no metric is
recomputed and no analysis is re-run: a gate that measured would be a second
measurement path, and two measurement paths eventually disagree.

Two evaluations of the same run under the same policy produce byte-identical
output. Every ordering is fixed, every identity is semantic, and the only
non-artifact input is the evaluation date, which the caller may pin.

**The verdict never launders a gap into a pass.** A rule whose measurement is
unavailable is reported `not_evaluable`, and under the default
``options.on_not_evaluable = "fail"`` it fails the build. A rule whose scope had
nothing to measure is reported `not_applicable` and never fails. Those are
different facts and are never merged.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Sequence

from modules import complexity_view
from modules.config import PROGRAM_VERSION
from modules.policy import evidence as evidence_module
from modules.policy import findings as finding_module
from modules.policy import metrics as metric_module
from modules.policy import rules as rule_module
from modules.policy import trust as trust_module
from modules.policy.document import PolicyDocumentInvalid, Waiver
from modules.policy.document_v2 import (
    ON_NOT_EVALUABLE_FAIL,
    PARTIAL_NOT_EVALUABLE,
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    MetricRule,
    PolicyV2Document,
)

if TYPE_CHECKING:
    from modules.ratchet.check_service import RatchetCheckRequest

#: 1.1.0 added the `evidence` block, the `hotspot_file` finding scope and three
#: evidence-typed reasons. 1.2.0 adds the `duplication` evidence kind, the
#: `duplication_group` finding scope and the `evidence_kind_not_requested`
#: reason. A check without a baseline must remain this literal current-state
#: contract. The ratchet integration boundary explicitly promotes configured
#: results to the additive Check Result 1.3 contract.
#:
#: Purely additive at every step: every 1.0.0 and 1.1.0 field keeps its meaning,
#: and a consumer that ignores the new evidence kind reads a 1.2.0 result
#: exactly as it read a 1.1.0 one. The version moves anyway, because the ABSENCE
#: of a kind must be unambiguous -- a reader has to be able to tell a result
#: that could carry duplication evidence from one that could not.
CHECK_RESULT_FORMAT_VERSION = "1.4.0"

#: The CI contract. Three codes, deliberately coarser than
#: ``metrolith policy evaluate``'s five: CI can only branch on a small integer,
#: and a gate whose "something went wrong" is spread over three codes gets
#: scripted as `|| true`. The *result document* keeps the distinctions under
#: `failure_kind`; only the process exit code is coarse.
EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

EXIT_CODE_MEANINGS: dict[str, str] = {
    "0": "the policy was evaluated and nothing requires failure",
    "1": (
        "the policy was evaluated and at least one violation-severity finding "
        "requires failure"
    ),
    "2": (
        "the policy was NOT evaluated: usage error, unreadable or missing run, "
        "invalid policy document, unsupported artifact generation, or an "
        "evaluation error. See failure_kind."
    ),
}

#: Why exit 2 happened. Never collapsed into one another.
FAILURE_USAGE = "usage"
FAILURE_POLICY_INVALID = "policy_invalid"
FAILURE_RUN_UNREADABLE = "run_unreadable"
FAILURE_CONTRACT_INCOMPATIBLE = "contract_incompatible"
FAILURE_EVALUATION_ERROR = "evaluation_error"
FAILURE_RATCHET_ADMISSION = "ratchet_admission_failed"
FAILURE_RATCHET_EVALUATION = "ratchet_evaluation_failed"
FAILURE_TRUST_ADMISSION = "trust_admission_failed"

FAILURE_KINDS: tuple[str, ...] = (
    FAILURE_USAGE, FAILURE_POLICY_INVALID, FAILURE_RUN_UNREADABLE,
    FAILURE_CONTRACT_INCOMPATIBLE, FAILURE_EVALUATION_ERROR,
    FAILURE_TRUST_ADMISSION,
)

FAILURE_KIND_MEANINGS: dict[str, str] = {
    FAILURE_USAGE: "the command was invoked incorrectly",
    FAILURE_POLICY_INVALID: "the policy document is malformed or invalid",
    FAILURE_RUN_UNREADABLE: (
        "the run directory is missing, unreadable, or does not report "
        "successful authoritative execution"
    ),
    FAILURE_CONTRACT_INCOMPATIBLE: (
        "the run declares an artifact generation this build does not support, so "
        "no value read from it would be trustworthy"
    ),
    FAILURE_EVALUATION_ERROR: "the evaluator failed while evaluating a rule",
    FAILURE_RATCHET_ADMISSION: (
        "the configured ratchet baseline failed trust, identity, contract, or "
        "revision admission; no ratchet comparison was accepted"
    ),
    FAILURE_RATCHET_EVALUATION: (
        "the admitted ratchet could not complete current observation comparison"
    ),
    FAILURE_TRUST_ADMISSION: (
        "protected evaluator, Policy, run, or evidence trust admission failed "
        "before any rule condition was evaluated"
    ),
}

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_ERROR = "error"

SCOPE_NOTE = (
    "Every threshold in this result is USER-SUPPLIED POLICY. Metrolith ships no "
    "default threshold and makes no architecture-quality claim: a value here "
    "means the policy author decided it, never that research established it. "
    "The observed values are existing Metrolith measurements, read from the "
    "published run bundle; no metric was invented, recomputed or re-measured "
    "for this evaluation."
)

#: The two v1 integrity rules that legitimately emit more than one finding for
#: one subject, and the categorical evidence key that separates them.
#:
#: An explicit table rather than a derived discriminator: the alternative is
#: digesting the evidence, and evidence carries counts, which would move a
#: finding's identity every time the count moved. Every other v1 rule emits at
#: most one finding per (rule, subject), which a test asserts by checking that
#: no result ever contains two findings with one id.
INTEGRITY_DISCRIMINATOR_KEY: dict[str, str] = {
    "diagnostics.parser_execution_failure": "error_category",
    "contract.incompatible": "field",
}

#: The v1 integrity rules whose predicate reads the structural schema report.
#: Only these make `check` pay for full Draft 2020-12 validation of the bundle,
#: which on a cohort run means validating a 358 MB `analysis.json` and every
#: repository document. A test asserts this set matches the rules that actually
#: touch `context.schema_report`, so a rule added later cannot silently stop
#: receiving a report it depends on.
SCHEMA_REPORT_RULES: frozenset[str] = frozenset({
    "artifact.schema_invalid",
    "artifact.compatibility_waiver_required_by_current_generation",
    "contract.artifact_generation_unsupported",
})


class CheckFailed(RuntimeError):
    """The evaluation could not happen. Carries the reason it could not."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class PresentedCheckResult(dict):
    """Canonical result mapping with optional nonserialized rendering context."""

    def __init__(self, payload, *, partial_evaluated_metrics=()):
        super().__init__(payload)
        self.partial_evaluated_metrics = tuple(sorted(partial_evaluated_metrics))


# --------------------------------------------------------------------------
# Per-subject view assembled once from the artifacts
# --------------------------------------------------------------------------

@dataclass
class SubjectView:
    """Everything the metric rules may read about one analyzed subject."""

    subject_key: str
    repository_url: str | None
    result: Mapping[str, Any]

    #: Recorded language name keyed by casefolded name, so one language never
    #: yields two units when the producer spells it two ways.
    language_names: dict[str, str] = field(default_factory=dict)
    #: Cognitive values per casefolded language, and overall. Raw persisted
    #: cells; the aggregate is derived by `complexity_view`, never here.
    cognitive_values: list[Any] = field(default_factory=list)
    cognitive_values_by_language: dict[str, list[Any]] = field(default_factory=dict)
    #: Whether any callable row was seen for this subject.
    callable_row_count: int = 0
    #: Languages recorded by artifacts, excluding names introduced only by a
    #: policy filter. This preserves the difference between a genuinely absent
    #: language and a malformed missing row for a recorded language.
    recorded_language_keys: set[str] = field(default_factory=set)

    @property
    def core_aggregate(self) -> Any:
        metrics = self.result.get("metrics")
        if metrics is None:
            return None
        if not isinstance(metrics, Mapping):
            return metrics
        return metrics.get("aggregate")

    @property
    def core_by_language(self) -> Mapping[str, Any]:
        metrics = self.result.get("metrics")
        found = metrics.get("by_language") if isinstance(metrics, Mapping) else None
        return found if isinstance(found, Mapping) else {}

    @property
    def complexity_block(self) -> Mapping[str, Any] | None:
        return complexity_view.repository_block(self.result)

    @property
    def complexity_status(self) -> Any:
        block = self.complexity_block
        return block.get("status") if block else None

    @property
    def complexity_aggregate(self) -> Mapping[str, Any] | None:
        block = self.complexity_block
        found = block.get("aggregate") if block else None
        return found if isinstance(found, Mapping) else None

    @property
    def complexity_by_language(self) -> Mapping[str, Any]:
        block = self.complexity_block
        found = block.get("by_language") if block else None
        return found if isinstance(found, Mapping) else {}

    @property
    def cognitive_state(self) -> Any:
        block = self.complexity_block
        if block is None or "cognitive_measurement_state" not in block:
            return complexity_view.COGNITIVE_ABSENT
        return block.get("cognitive_measurement_state")

    def display_language(self, casefolded: str) -> str:
        return self.language_names.get(casefolded, casefolded)


def _register_language(view: SubjectView, recorded: Any, *, prefer: bool) -> str | None:
    """Record one language spelling. Display-cased spellings win.

    The producer writes core metrics under lowercase keys (`python`) and
    complexity under display keys (`Python`) for the same language. Both are the
    same unit; the display spelling is the one a human should read.
    """
    if not recorded:
        return None
    name = str(recorded)
    key = name.casefold()
    if prefer or key not in view.language_names:
        view.language_names[key] = name
    view.recorded_language_keys.add(key)
    return key


def _raw_complexity_block(view: SubjectView) -> tuple[bool, Any]:
    """Return whether the field exists and its raw value without laundering type."""

    metrics = view.result.get("metrics")
    if isinstance(metrics, Mapping) and "complexity" in metrics:
        return True, metrics.get("complexity")
    if "complexity" in view.result:
        return True, view.result.get("complexity")
    return False, None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@dataclass
class _RuleTally:
    """Per-rule outcome counts, so a pass is reported without being enumerated."""

    units: int = 0
    violated: int = 0
    passed: int = 0
    not_evaluable: int = 0
    not_applicable: int = 0
    evaluation_error: int = 0

    def record(self, status: str, count: int = 1) -> None:
        self.units += count
        setattr(self, status, getattr(self, status) + count)

    def as_dict(self) -> dict[str, int]:
        return {
            "units_evaluated": self.units,
            "violated": self.violated,
            "passed": self.passed,
            "not_evaluable": self.not_evaluable,
            "not_applicable": self.not_applicable,
            "evaluation_error": self.evaluation_error,
        }


#: Carried on every hotspot finding and stated in the result. A hotspot is a
#: maintenance-attention signal over two existing measurements; it is not a
#: defect, not a bug prediction and not a quality score, and a finding that
#: fires only means the policy author's threshold was met.
HOTSPOT_EVIDENCE_NOTE = (
    "Maintenance attention only. The Hotspot analysis ranks existing "
    "complexity and commit-frequency measurements within one repository; it "
    "predicts no defect, asserts no quality, and produces no score. The "
    "threshold that made this a finding is the policy author's."
)


def _hotspot_row_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """The row's own published classification and reasons, verbatim.

    Nothing is re-derived. `validate_hotspot_document` has already checked that
    `classification` equals `classify_attention` of this row's two signals, and
    that `reasons` explains it. Recomputing either here would be a second
    definition of a published fact.

    The ordinal signals travel as EVIDENCE, never as a comparable value: no
    rule can gate on them, because reading `high` as a number would invent a
    scale Metrolith does not have.
    """
    complexity_signal = row.get("complexity_signal") or {}
    churn_signal = row.get("churn_signal") or {}
    return {
        "hotspot_classification": row.get("classification"),
        "hotspot_complexity_signal": complexity_signal.get("signal"),
        "hotspot_churn_signal": churn_signal.get("signal"),
        "hotspot_reasons": [
            item for item in (row.get("reasons") or ()) if isinstance(item, str)
        ],
        "hotspot_note": HOTSPOT_EVIDENCE_NOTE,
    }


#: Carried on every duplication finding and stated in the result. Duplicate code
#: is a measurement the Duplication analysis makes; it is not a defect, not a
#: quality score and not automatically bad, and a finding that fires only means
#: the policy author's threshold was met.
DUPLICATION_EVIDENCE_NOTE = (
    "Measurement only. The Duplication analysis reports clone groups it "
    "detected; duplicate code is not a defect, not a quality score and not "
    "automatically a problem -- some duplication is deliberate and correct. "
    "The threshold that made this a finding is the policy author's."
)

#: How many occurrences of one clone group travel in a finding's evidence.
#:
#: A cap, because a pathological group can have thousands of occurrences and the
#: result document is read by CI. The EXACT `occurrence_count` is always carried
#: beside the list and is never the capped length, so truncation costs detail
#: and never costs the truth.
DUPLICATION_OCCURRENCE_EVIDENCE_CAP = 50


def _duplication_group_evidence(
    group: Mapping[str, Any], kind: str
) -> dict[str, Any]:
    """One clone group's published identity and occurrences, verbatim.

    Nothing is re-derived. `validate_duplication_document` has already
    reconciled `group_id` against the occurrences, `distribution` against the
    paths and `file_count` against the distinct ones; recomputing any of them
    here would be a second definition of a published fact.

    Both identifiers travel. `fingerprint` is what the finding's identity uses
    -- it is the canonicalized-body digest and does not move when an unrelated
    edit shifts a line -- and `group_id` is the coordinate digest, which does
    move and is therefore evidence rather than identity. Carrying both means a
    reader can still match the finding to the document's own row.

    `distribution` travels as EVIDENCE and is deliberately not a metric:
    `same_file`/`cross_file`/`mixed` is categorical, and reading a category as a
    number would invent a scale Metrolith does not have.
    """
    occurrences = [
        item for item in (group.get("occurrences") or ())
        if isinstance(item, Mapping)
    ]
    capped = occurrences[:DUPLICATION_OCCURRENCE_EVIDENCE_CAP]
    payload: dict[str, Any] = {
        "duplication_kind": kind,
        "duplication_group_id": group.get("group_id"),
        "duplication_fingerprint": group.get("fingerprint"),
        "duplication_fingerprint_version": group.get("fingerprint_version"),
        "duplication_distribution": group.get("distribution"),
        "duplication_occurrence_count": group.get("occurrence_count"),
        "duplication_file_count": group.get("file_count"),
        "duplication_occurrences": [
            {
                "path": str(item.get("path") or "").replace("\\", "/"),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "unit_kind": item.get("unit_kind"),
                "occurrence_id": item.get("occurrence_id"),
            }
            for item in capped
        ],
        "duplication_occurrences_truncated": (
            len(occurrences) > DUPLICATION_OCCURRENCE_EVIDENCE_CAP
        ),
        "duplication_note": DUPLICATION_EVIDENCE_NOTE,
    }
    if "source_span_line_count" in group:
        payload["duplication_source_span_line_count"] = group[
            "source_span_line_count"
        ]
    payload["related_locations"] = _related_location_list(capped)
    return payload


def _related_location_list(
    occurrences: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The finding's OTHER locations, as the generic projection contract.

    Deliberately unprefixed, and deliberately not duplication-specific.
    `modules.policy.sarif` reads exactly this key and knows nothing about clone
    groups: a projector that reached into `duplication_occurrences` would be a
    second place that understands what duplication is, which is the branch the
    hotspot phase already refused to introduce. Any future family whose finding
    covers several places sets the same key and gets the same projection.

    The FIRST occurrence is excluded, because it is the finding's own
    `path`/`start_line`/`end_line` -- the primary location. A consumer reading
    both arrays sees each place once.

    Ordering is `(path, start_line, end_line)`. The Duplication contract already
    canonicalizes occurrences by coordinate, so this preserves that order rather
    than imposing a new one; sorting explicitly means the projection stays
    deterministic even if a future producer orders its occurrences differently.

    Coordinates are de-duplicated. Two occurrences may share a line range while
    differing in `unit_kind`, which is one PLACE and two units: emitting it
    twice would suggest two findings' worth of evidence where there is one. The
    unit-level detail is untouched in `duplication_occurrences`.
    """
    seen: set[tuple[str, Any, Any]] = set()
    primary: tuple[str, Any, Any] | None = None
    collected: list[dict[str, Any]] = []
    for index, item in enumerate(occurrences):
        path = str(item.get("path") or "").replace("\\", "/")
        key = (path, item.get("start_line"), item.get("end_line"))
        if index == 0:
            primary = key
            continue
        if key == primary or key in seen:
            continue
        seen.add(key)
        collected.append({
            "path": path,
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
        })
    collected.sort(
        key=lambda item: (
            item["path"],
            item["start_line"] if isinstance(item["start_line"], int) else -1,
            item["end_line"] if isinstance(item["end_line"], int) else -1,
        )
    )
    return collected


def _matches_path(path: str, patterns: Sequence[str]) -> bool:
    """Glob match over a POSIX-normalized relative path.

    ``fnmatch`` semantics, which means ``*`` matches ``/`` as well. That is
    stated in the schema and in the rules listing rather than left to be
    discovered: ``src/*.py`` therefore matches ``src/a/b.py``, and a policy that
    wants one directory level should say ``src/*.py`` knowing this, or scope by
    a longer prefix.
    """
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _language_allowed(recorded: str | None, allowed: Sequence[str]) -> bool:
    if not allowed:
        return True
    if recorded is None:
        return False
    folded = recorded.casefold()
    return any(folded == item.casefold() for item in allowed)


def _outcome_for(
    observation: metric_module.Observation, policy: PolicyV2Document
) -> tuple[str, str | None, int | float | None]:
    """Map one observation onto a status, a typed reason and a usable value.

    This is the only place a persisted status decides an outcome, and it is
    deliberately exhaustive: every completeness class has an explicit branch, so
    a new class cannot fall through to "evaluated".

    The value is returned alongside the status, and is non-``None`` **only**
    when the status is `passed` — meaning "comparable, not yet compared". A
    caller therefore cannot reach a number without having gone through the
    status branch, which is the shape that makes "missing is never zero" a
    property of the code rather than a rule everyone has to remember.
    """
    if observation.error is not None:
        return (
            finding_module.STATUS_EVALUATION_ERROR,
            finding_module.REASON_MALFORMED_CONSUMED_FIELD,
            None,
        )
    completeness = observation.completeness
    if completeness == metric_module.COMPLETENESS_NOT_APPLICABLE:
        return (
            finding_module.STATUS_NOT_APPLICABLE,
            finding_module.REASON_NOTHING_TO_MEASURE,
            None,
        )
    if completeness == metric_module.COMPLETENESS_ABSENT:
        return (
            finding_module.STATUS_NOT_EVALUABLE,
            finding_module.REASON_MEASUREMENT_ABSENT,
            None,
        )
    if completeness == metric_module.COMPLETENESS_UNAVAILABLE:
        return (
            finding_module.STATUS_NOT_EVALUABLE,
            finding_module.REASON_MEASUREMENT_UNAVAILABLE,
            None,
        )
    if (
        completeness == metric_module.COMPLETENESS_PARTIAL
        and policy.options.partial_data == PARTIAL_NOT_EVALUABLE
    ):
        return (
            finding_module.STATUS_NOT_EVALUABLE,
            finding_module.REASON_PARTIAL_DATA_REFUSED,
            None,
        )
    if observation.value is None:
        # Defensive: `Observation` already refuses this pairing, and reaching it
        # would mean a status promised a number nobody wrote.
        return (
            finding_module.STATUS_NOT_EVALUABLE,
            finding_module.REASON_MEASUREMENT_UNAVAILABLE,
            None,
        )
    return finding_module.STATUS_PASSED, None, observation.value


class CheckEvaluation:
    """One evaluation of one run against one policy."""

    def __init__(
        self,
        run_directory: Path,
        policy: PolicyV2Document,
        *,
        today: date | None = None,
        hotspots: Path | None = None,
        duplication: Path | None = None,
        trust: trust_module.CheckTrustContext | None = None,
    ) -> None:
        self.run_directory = Path(run_directory)
        self.policy = policy
        self.trust = trust
        self.today = today or date.today()
        #: Supplied evidence paths, by kind. Admission happens in `open()`, once
        #: the run is readable: provenance is a comparison AGAINST the run, and
        #: reporting "your document does not match" before knowing the run is
        #: readable would point the operator at the wrong file.
        self.evidence_paths: dict[str, Path | None] = {
            evidence_module.EVIDENCE_HOTSPOTS: hotspots,
            evidence_module.EVIDENCE_DUPLICATION: duplication,
        }
        self.evidence: dict[str, evidence_module.EvidenceRecord] = {}
        #: Per-subject hotspot counts and rows from an admitted document. Empty
        #: when nothing was admitted -- which is NOT a subject whose counts are
        #: zero, and the two must never converge.
        self._hotspot_counts: dict[str, dict[str, Any]] = {}
        self._hotspot_rows: dict[str, list[Mapping[str, Any]]] = {}
        self._hotspot_covered: frozenset[str] = frozenset()
        #: Duplication evidence, indexed for the ONE subject the document binds
        #: to. A Duplication document describes one local snapshot and carries
        #: no subject identity, so binding resolves at most one subject and
        #: every other subject in the run is `not_evaluable` for duplication.
        self._duplication_counts: dict[str, dict[str, Any]] = {}
        self._duplication_groups: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
        #: Persisted `counts.<kind>.status` per subject per kind. **This is the
        #: source of truth for whether a kind was measured** -- never the length
        #: of the group list, which is empty under `not_requested` and `failed`
        #: as well as for a genuinely clone-free subject.
        self._duplication_kind_statuses: dict[str, dict[str, Any]] = {}
        self._duplication_covered: frozenset[str] = frozenset()
        self.findings: list[finding_module.Finding] = []
        self.waived: list[finding_module.Finding] = []
        self.tallies: dict[str, _RuleTally] = {}
        self.partial_evaluated_metrics: set[str] = set()
        self._subjects: dict[str, SubjectView] = {}
        self._ledger_loaded = False
        self._schema_report_loaded = False
        # Populated by `open()`. Every rule path runs after it, so nothing here
        # is read before the artifacts are on hand.
        self.view: Any = None
        self.manifest: dict[str, Any] = {}
        self.status: dict[str, Any] = {}
        # Empty until an enabled integrity rule needs it. Empty means NOT
        # ASSESSED, never `valid`.
        self.schema_report: dict[str, Any] = {}
        self.run_manifest_sha256: str | None = None

    # -- artifact access ---------------------------------------------------

    def open(self) -> None:
        """Read the run bundle. Every failure here is an exit-2 condition."""
        from validation.artifact_io.compatibility import CompatibilityState
        from validation.artifact_io.errors import ArtifactStructureError
        from validation.artifact_io.reader import open_run

        if not self.run_directory.is_dir():
            raise CheckFailed(
                FAILURE_RUN_UNREADABLE,
                f"{self.run_directory} is not a readable run directory",
            )
        try:
            self.view = open_run(self.run_directory)
        except ArtifactStructureError as exc:
            raise CheckFailed(FAILURE_RUN_UNREADABLE, str(exc)) from exc
        except OSError as exc:
            raise CheckFailed(FAILURE_RUN_UNREADABLE, str(exc)) from exc

        compatibility = self.view.compatibility
        if compatibility.state is not CompatibilityState.SUPPORTED:
            raise CheckFailed(
                FAILURE_CONTRACT_INCOMPATIBLE,
                f"the run declares artifact schema "
                f"{compatibility.declared!r}, which this build classifies as "
                f"{compatibility.state.value!r}: {compatibility.reason}. No "
                f"value read from it would be trustworthy, so the policy is "
                f"refused rather than evaluated against an artifact generation "
                f"this build cannot interpret.",
            )

        # Success is an admission property, not an optional policy rule.
        # ImmutableRunView owns the status vocabulary and lifecycle
        # classification; using its canonical predicate avoids a second copy of
        # either contract here. Full bundle schema validation remains lazy.
        if not self.view.succeeded:
            lifecycle = getattr(self.view.lifecycle, "value", "unknown")
            raise CheckFailed(
                FAILURE_RUN_UNREADABLE,
                f"the run reports authoritative status "
                f"{self.view.integrity_status!r} and lifecycle {lifecycle!r}; "
                "metrolith check requires a successful run",
            )

        self.manifest = dict(self.view.manifest)
        self.run_manifest_sha256 = trust_module.sha256_bytes(
            self.view.reader.document_bytes("run_manifest.json")
        )
        self.status = dict(self.view.status)
        self._build_subjects()
        self._admit_evidence()

    # -- evidence ----------------------------------------------------------

    def _admit_evidence(self) -> None:
        """Admit every supplied evidence document through the shared boundary.

        `modules.policy.evidence` owns admission: the contract identity, the
        document's own validator resolved through the standalone registry, the
        provenance comparison and the subject binding. None of that is repeated
        here, no hotspot validator is imported, and no evidence document is
        opened by any other path.

        **A document that is not admitted contributes nothing and never
        raises.** It is recorded, and every rule that needed it reports
        `not_evaluable` with a reason naming which failure occurred -- the same
        treatment an unavailable measurement gets, and under the default
        `options.on_not_evaluable` it fails the build.
        """
        scopes = evidence_module.analyzed_scopes(self.view.repositories)
        run_id = self.view.run_id
        for kind, path in self.evidence_paths.items():
            self.evidence[kind] = evidence_module.admit_evidence_file(
                kind,
                path,
                run_id=run_id,
                scopes=scopes,
                trust=self.trust,
                run_manifest_sha256=self.run_manifest_sha256,
            )
        self._index_hotspot_evidence()
        self._index_duplication_evidence()

    def protected_trust_failure(self) -> str | None:
        """Return a typed pre-evaluation refusal, or ``None`` when admitted."""

        if self.trust is None or not self.trust.protected:
            return None
        assert self.run_manifest_sha256 is not None
        try:
            self.trust.validate_run_binding(self.run_manifest_sha256)
        except trust_module.TrustAdmissionError as exc:
            for kind, path in self.evidence_paths.items():
                if path is None:
                    continue
                record = self.evidence[kind]
                self.evidence[kind] = replace(
                    record,
                    admission=evidence_module.TRUST_REFUSED,
                    trust_state=trust_module.REFUSED,
                    reason=exc.code,
                    detail=str(exc),
                    document=None,
                )
            return exc.code

        supplied = {
            kind for kind, path in self.evidence_paths.items() if path is not None
        }
        attested = (
            set() if self.trust.receipt is None
            else set(self.trust.receipt.by_kind)
        )
        if attested != supplied:
            return "receipt_evidence_set_mismatch"
        for kind in sorted(supplied):
            record = self.evidence[kind]
            if record.trust_state != trust_module.TRUSTED:
                return f"{kind}_{record.reason or record.admission}"
        return None

    def _index_duplication_evidence(self) -> None:
        """Assemble per-subject counts, kind statuses and groups.

        Everything read here is persisted verbatim by the Duplication document.
        No count is summed across kinds, no NLOC is aggregated and no group is
        re-derived: the analysis owns those definitions and Policy reads them.

        The kind STATUSES are indexed separately from the groups, and that
        separation is the point. Under `not_requested` and under `failed` the
        document carries an EMPTY group list -- indistinguishable, by length
        alone, from a subject that genuinely has no clones. Every later decision
        about whether a kind was measured reads the status.
        """
        record = self.evidence.get(evidence_module.EVIDENCE_DUPLICATION)
        if record is None or not record.admitted or record.document is None:
            return

        document = record.document
        self._duplication_covered = frozenset(record.subject_keys)
        counts = document.get("counts") or {}

        for subject_key in self._duplication_covered:
            statuses: dict[str, Any] = {}
            groups: dict[str, list[Mapping[str, Any]]] = {}
            for kind, list_key in metric_module.DUPLICATION_GROUP_LIST.items():
                block = counts.get(kind)
                statuses[kind] = (
                    block.get("status") if isinstance(block, Mapping) else None
                )
                groups[kind] = [
                    item for item in (document.get(list_key) or ())
                    if isinstance(item, Mapping)
                ]
            self._duplication_kind_statuses[subject_key] = statuses
            self._duplication_groups[subject_key] = groups

            flat: dict[str, Any] = {}
            for field_name, (kind, key) in (
                metric_module.DUPLICATION_REPOSITORY_SOURCES.items()
            ):
                if kind is None:
                    source: Mapping[str, Any] = counts
                else:
                    block = counts.get(kind)
                    source = block if isinstance(block, Mapping) else {}
                # `.get` and not a default: a key the document does not carry
                # arrives as None, which `numeric_value` turns into "unavailable"
                # rather than into a zero.
                flat[field_name] = source.get(key)
            self._duplication_counts[subject_key] = flat

    def _duplication_coverage(self, subject_key: str) -> tuple[str, str | None]:
        """This subject's duplication coverage, as a status and a typed reason.

        Four situations, kept apart because collapsing any two of them would
        turn a missing input into a clean gate:

        * no document supplied -> `unavailable`, `evidence_not_supplied`
        * supplied and refused -> `unavailable`, `evidence_not_admitted`
        * admitted, bound to a DIFFERENT subject -> `unavailable`,
          `evidence_does_not_cover_subject`
        * admitted and bound to this subject -> `complete`

        The third case is duplication's structural limit made visible rather
        than engineered around. One document describes one snapshot and binds to
        at most one subject, so a multi-subject run gated on duplication reports
        every other subject as not evaluable. The alternative -- merging two
        documents -- would be a new analysis with a new definition.
        """
        record = self.evidence.get(evidence_module.EVIDENCE_DUPLICATION)
        if record is None or not record.supplied:
            return "unavailable", finding_module.REASON_EVIDENCE_NOT_SUPPLIED
        if not record.admitted:
            return "unavailable", finding_module.REASON_EVIDENCE_NOT_ADMITTED
        if subject_key not in self._duplication_covered:
            return (
                "unavailable",
                finding_module.REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT,
            )
        return "complete", None

    def _duplication_kind_status(self, subject_key: str, kind: str) -> Any:
        """The persisted ``counts.<kind>.status``, or ``None`` when unknown."""
        return (self._duplication_kind_statuses.get(subject_key) or {}).get(kind)

    def _duplication_kind_reason(self, kind_status: Any) -> str | None:
        """The typed reason for a kind whose status is not evaluable.

        `not_requested` gets its own reason rather than sharing
        `measurement_unavailable`. Both are honestly "the rule did not run", but
        they send an operator to different places: one to their own `--kind`
        flag, the other to a failed measurement.
        """
        completeness = metric_module.duplication_completeness_of_status(kind_status)
        if completeness == metric_module.COMPLETENESS_NOT_APPLICABLE:
            return finding_module.REASON_NOTHING_TO_MEASURE
        if kind_status == metric_module.DUPLICATION_NOT_REQUESTED:
            return finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED
        return finding_module.REASON_MEASUREMENT_UNAVAILABLE

    def _duplication_admission(self) -> str | None:
        record = self.evidence.get(evidence_module.EVIDENCE_DUPLICATION)
        return None if record is None else record.admission

    def _index_hotspot_evidence(self) -> None:
        """Assemble per-subject counts and rows from an admitted document.

        The attention tally is NOT computed here. It comes from
        `modules.hotspots.attention_class_counts`, the single definition of that
        count and the same function the dossier calls, so the gate and the
        dossier can never report two different numbers for one word.
        """
        record = self.evidence.get(evidence_module.EVIDENCE_HOTSPOTS)
        if record is None or not record.admitted or record.document is None:
            return
        from modules.hotspots import attention_class_counts

        document = record.document
        self._hotspot_covered = frozenset(record.subject_keys)

        for row in document.get("hotspots") or ():
            if isinstance(row, Mapping):
                self._hotspot_rows.setdefault(
                    str(row.get("subject_key") or ""), []
                ).append(row)

        blocks: dict[str, Mapping[str, Any]] = {
            str(item["subject_key"]): item
            for item in document.get("repositories") or ()
            if isinstance(item, Mapping) and item.get("subject_key")
        }

        for subject_key in self._hotspot_covered:
            block = blocks.get(subject_key) or {}
            counts: dict[str, Any] = {
                "hotspot_file_count": block.get("file_count"),
                "hotspot_classified_file_count": block.get(
                    "classified_file_count"
                ),
            }
            for label, value in attention_class_counts(
                document, subject_key=subject_key
            ).items():
                counts[f"hotspot_{label}_file_count"] = value
            self._hotspot_counts[subject_key] = counts

    def _hotspot_coverage(self, subject_key: str) -> tuple[str, str | None]:
        """This subject's hotspot coverage, as a status and a typed reason.

        Four situations, kept apart because collapsing any two of them would
        turn a missing input into a clean gate:

        * no document supplied -> `unavailable`, `evidence_not_supplied`
        * supplied and refused -> `unavailable`, `evidence_not_admitted`
        * admitted, subject not covered -> `unavailable`,
          `evidence_does_not_cover_subject`
        * admitted and covered -> `measured`, or `not_applicable` when the
          document recorded no row for this subject
        """
        record = self.evidence.get(evidence_module.EVIDENCE_HOTSPOTS)
        if record is None or not record.supplied:
            return "unavailable", finding_module.REASON_EVIDENCE_NOT_SUPPLIED
        if not record.admitted:
            return "unavailable", finding_module.REASON_EVIDENCE_NOT_ADMITTED
        if subject_key not in self._hotspot_covered:
            return (
                "unavailable",
                finding_module.REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT,
            )
        if not self._hotspot_rows.get(subject_key):
            return "not_applicable", finding_module.REASON_NOTHING_TO_MEASURE
        return "measured", None

    def _hotspot_admission(self) -> str | None:
        record = self.evidence.get(evidence_module.EVIDENCE_HOTSPOTS)
        return None if record is None else record.admission

    def _load_schema_report(self) -> dict[str, Any]:
        """Structurally validate the bundle, once, and only when asked.

        This is deliberately NOT part of :meth:`open`. Full Draft 2020-12
        validation of a cohort bundle means validating every repository
        document plus a 358 MB `analysis.json` on the benchmark of record, and
        it dominates everything the policy evaluator does. A policy that gates
        on metrics and enables none of the three schema-reading integrity rules
        has no use for the verdict, and should not pay for it.

        When it was not computed, `run.schema_validation_result` is reported as
        ``null`` — meaning *not assessed*, never *valid*.
        """
        if self._schema_report_loaded:
            return self.schema_report
        from modules.cli.validate_command import schema_only_report

        self._schema_report_loaded = True
        self.schema_report = schema_only_report(self.run_directory)
        return self.schema_report

    def _build_subjects(self) -> None:
        from modules.subject import subject_key_of

        for result in self.view.repositories:
            payload = dict(result)
            key = subject_key_of(payload)
            view = SubjectView(
                subject_key=key,
                repository_url=payload.get("repository_url"),
                result=payload,
            )
            for recorded in view.core_by_language:
                _register_language(view, recorded, prefer=False)
            for recorded in view.complexity_by_language:
                _register_language(view, recorded, prefer=True)
            # A duplicate subject_key in one run would silently drop a subject's
            # rules, so the later element is kept under a disambiguated key
            # rather than overwriting the earlier one.
            if key in self._subjects:
                key = f"{key}#{len(self._subjects)}"
                view.subject_key = key
            self._subjects[key] = view

    def _needs_ledger(self) -> bool:
        """Whether any enabled rule reads the per-callable ledger.

        Checked so an ordinary repository-scope policy never pays for a pass
        over a ledger that can be millions of rows at cohort scale.
        """
        for rule in self.policy.metric_rules:
            if rule.scope == metric_module.SCOPE_CALLABLE:
                return True
            if rule.definition.family == metric_module.FAMILY_COGNITIVE_COMPLEXITY:
                return True
        return False

    def _load_ledger(self) -> None:
        """One streaming pass: accumulate cognitive values, evaluate callables.

        Only the cognitive *values* are retained, never the rows. Callable-scope
        rules are evaluated as each row arrives, so the ledger is never
        materialized.
        """
        if self._ledger_loaded:
            return
        self._ledger_loaded = True

        callable_rules = [
            rule for rule in self.policy.metric_rules
            if rule.scope == metric_module.SCOPE_CALLABLE
        ]
        cognitive_needed = any(
            rule.definition.family
            == metric_module.FAMILY_COGNITIVE_COMPLEXITY
            for rule in self.policy.metric_rules
        )
        # Per (rule id, subject key): violated findings plus collapsed counts.
        collapsed: dict[tuple[str, str], dict[str, Any]] = {}
        for rule in callable_rules:
            for subject_key in self._subjects:
                if rule.subjects and subject_key not in rule.subjects:
                    continue
                collapsed[(rule.identifier, subject_key)] = {
                    "matched": 0, "passed": 0,
                    "not_applicable": 0, "by_reason": {},
                }

        for row in self._iter_callable_rows():
            if not isinstance(row, Mapping):
                raise CheckFailed(
                    FAILURE_EVALUATION_ERROR,
                    "the callable ledger produced a non-object row",
                )
            raw_subject_key = row.get("subject_key")
            if not isinstance(raw_subject_key, str) or not raw_subject_key:
                raise CheckFailed(
                    FAILURE_EVALUATION_ERROR,
                    "a callable ledger row has a missing or non-string subject_key",
                )
            subject_key = raw_subject_key
            view = self._subjects.get(subject_key)
            if view is None:
                raise CheckFailed(
                    FAILURE_EVALUATION_ERROR,
                    f"callable ledger row names unknown subject {subject_key!r}",
                )
            raw_language = row.get("detected_language")
            if not isinstance(raw_language, str) or not raw_language.strip():
                raise CheckFailed(
                    FAILURE_EVALUATION_ERROR,
                    f"callable ledger row for {subject_key!r} has a missing or "
                    "non-string detected_language",
                )
            if callable_rules:
                raw_path = row.get("relative_path")
                if not isinstance(raw_path, str) or not raw_path:
                    raise CheckFailed(
                        FAILURE_EVALUATION_ERROR,
                        f"callable ledger row for {subject_key!r} has a missing "
                        "or non-string relative_path",
                    )
            if cognitive_needed:
                self._validated_ledger_integer(
                    row.get("cognitive_complexity"),
                    "cognitive_complexity",
                    allow_empty=True,
                )
            view.callable_row_count += 1
            language_key = _register_language(view, raw_language, prefer=True)
            if cognitive_needed:
                raw_cognitive = row.get("cognitive_complexity")
                view.cognitive_values.append(raw_cognitive)
                if language_key is not None:
                    view.cognitive_values_by_language.setdefault(
                        language_key, []
                    ).append(raw_cognitive)

            for rule in callable_rules:
                bucket = collapsed.get((rule.identifier, subject_key))
                if bucket is None:
                    continue
                self._evaluate_callable_row(rule, view, row, bucket)

        for (rule_id, subject_key), bucket in collapsed.items():
            rule = next(
                item for item in self.policy.metric_rules if item.identifier == rule_id
            )
            self._finish_callable_rule(rule, self._subjects[subject_key], bucket)

    @staticmethod
    def _validated_ledger_integer(
        raw: Any, field: str, *, allow_empty: bool = False
    ) -> int | None:
        if allow_empty and (raw is None or (isinstance(raw, str) and not raw.strip())):
            return None
        number = metric_module.numeric_value(raw)
        if number is None or (
            isinstance(number, float) and not number.is_integer()
        ):
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                f"callable ledger field {field!r} must be an integer"
                + (" or empty" if allow_empty else "")
                + f"; got {raw!r}",
            )
        return int(number)

    def _iter_callable_rows(self) -> Iterator[Mapping[str, Any]]:
        if not self.view.has_callable_artifact:
            return iter(())
        return self.view.stream_callables()

    # -- integrity half ----------------------------------------------------

    def evaluate_integrity(self) -> None:
        """Run the v1 rules through the v1 engine, unchanged.

        The rule predicates, their severities, their domains and their evidence
        are v1's. Only the presentation moves: the v1 finding is projected onto
        the canonical model so one gate emits one kind of finding.
        """
        from modules.policy.engine import EvaluationContext

        if not self.policy.integrity_rules:
            return

        if set(self.policy.integrity_rules) & SCHEMA_REPORT_RULES:
            self._load_schema_report()

        context = EvaluationContext(
            run_directory=self.run_directory,
            manifest=self.manifest,
            status=self.status,
            repositories=tuple(dict(item) for item in self.view.repositories),
            errors=tuple(dict(item) for item in self.view.errors),
            schema_report=self.schema_report,
            structural_errors=tuple(self.view.structural_errors),
            lifecycle=getattr(self.view.lifecycle, "value", None),
            expected_contracts=dict(self.policy.expected_contracts),
        )

        for rule in rule_module.RULES:
            severity = self.policy.severity_for_integrity(rule.identifier)
            if severity is None:
                continue
            tally = self.tallies.setdefault(rule.identifier, _RuleTally())
            fired = 0
            try:
                emitted = list(rule.evaluate(context))
            except Exception as exc:  # pragma: no cover - defensive
                self._record(finding_module.Finding(
                    finding_id=finding_module.finding_identity(
                        rule_id=rule.identifier, metric=None,
                        scope=finding_module.SCOPE_RUN, subject_key="",
                    ),
                    rule_id=rule.identifier, kind=finding_module.KIND_INTEGRITY,
                    severity=severity,
                    status=finding_module.STATUS_EVALUATION_ERROR,
                    scope=finding_module.SCOPE_RUN, domain=rule.domain,
                    subject_key="",
                    message=(
                        f"the evaluator failed while evaluating "
                        f"{rule.identifier}: {exc}"
                    ),
                    evidence={"error": str(exc)},
                    provenance=self._provenance(),
                ))
                tally.record(finding_module.STATUS_EVALUATION_ERROR)
                continue

            for item in emitted:
                fired += 1
                self._record(self._integrity_finding(rule, item, severity))
                tally.record(finding_module.STATUS_VIOLATED)
            if not fired:
                tally.record(finding_module.STATUS_PASSED)

    def _integrity_finding(
        self, rule: rule_module.Rule, item: rule_module.Finding, severity: str
    ) -> finding_module.Finding:
        subject_key = item.subject_key or ""
        discriminator_key = INTEGRITY_DISCRIMINATOR_KEY.get(rule.identifier)
        discriminator = (
            str(item.evidence.get(discriminator_key))
            if discriminator_key and discriminator_key in item.evidence
            else None
        )
        scope = (
            metric_module.SCOPE_REPOSITORY if subject_key
            else finding_module.SCOPE_RUN
        )
        subject = self._subjects.get(subject_key)
        return finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=None, scope=scope,
                subject_key=subject_key, discriminator=discriminator,
            ),
            rule_id=rule.identifier,
            kind=finding_module.KIND_INTEGRITY,
            severity=severity,
            status=finding_module.STATUS_VIOLATED,
            scope=scope,
            domain=rule.domain,
            subject_key=subject_key,
            repository_url=subject.repository_url if subject else None,
            message=item.detail,
            evidence=dict(item.evidence),
            provenance=self._provenance(),
        )

    # -- metric half -------------------------------------------------------

    def evaluate_metrics(self) -> None:
        for rule in self.policy.metric_rules:
            self.tallies.setdefault(rule.identifier, _RuleTally())
            self._validate_rule_subjects(rule)
        if self._needs_ledger():
            self._load_ledger()
        for rule in self.policy.metric_rules:
            if rule.scope == metric_module.SCOPE_REPOSITORY:
                self._evaluate_repository_rule(rule)
            elif rule.scope == metric_module.SCOPE_LANGUAGE:
                self._evaluate_language_rule(rule)
            elif rule.scope == metric_module.SCOPE_HOTSPOT_FILE:
                self._evaluate_hotspot_file_rule(rule)
            elif rule.scope == metric_module.SCOPE_DUPLICATION_GROUP:
                self._evaluate_duplication_group_rule(rule)
            # Callable-scope rules were evaluated during the ledger pass.

    def _validate_rule_subjects(self, rule: MetricRule) -> None:
        """Refuse every policy subject identity the opened run does not carry."""

        unknown = sorted(set(rule.subjects).difference(self._subjects))
        if not unknown:
            return
        raise CheckFailed(
            FAILURE_EVALUATION_ERROR,
            f"metric rule {rule.identifier!r} selects unknown repository "
            f"subject(s) {unknown!r}; available subjects are "
            f"{sorted(self._subjects)!r}",
        )

    def _subjects_for(self, rule: MetricRule) -> Iterable[SubjectView]:
        for key, view in self._subjects.items():
            if rule.subjects and key not in rule.subjects:
                continue
            yield view

    def _evaluate_repository_rule(self, rule: MetricRule) -> None:
        definition = rule.definition
        for view in self._subjects_for(rule):
            reason_override: str | None = None
            if definition.family == metric_module.FAMILY_HOTSPOTS:
                coverage, reason_override = self._hotspot_coverage(
                    view.subject_key
                )
                observation = metric_module.read_hotspot_repository(
                    self._hotspot_counts.get(view.subject_key), definition,
                    coverage_status=coverage,
                )
            elif definition.family == metric_module.FAMILY_DUPLICATION:
                coverage, reason_override = self._duplication_coverage(
                    view.subject_key
                )
                kind = metric_module.DUPLICATION_REPOSITORY_KIND.get(
                    definition.identifier
                )
                kind_status = (
                    None if kind is None
                    else self._duplication_kind_status(view.subject_key, kind)
                )
                observation = metric_module.read_duplication_repository(
                    self._duplication_counts.get(view.subject_key), definition,
                    coverage_status=coverage, kind_status=kind_status,
                )
                if reason_override is None and kind is not None:
                    # The document covers this subject, so any remaining
                    # non-evaluability is the KIND's, and `not_requested` must
                    # not be reported as a generic unavailable measurement.
                    completeness = metric_module.duplication_completeness_of_status(
                        kind_status
                    )
                    if completeness not in (
                        metric_module.COMPLETENESS_COMPLETE,
                        metric_module.COMPLETENESS_PARTIAL,
                    ):
                        reason_override = self._duplication_kind_reason(kind_status)
            else:
                observation = self._read_repository(view, definition)
            self._emit(
                rule, view, observation,
                scope=metric_module.SCOPE_REPOSITORY,
                reason_override=reason_override,
            )

    def _read_repository(
        self, view: SubjectView, definition: metric_module.MetricDefinition
    ) -> metric_module.Observation:
        if definition.family == metric_module.FAMILY_CORE:
            return metric_module.read_core(view.core_aggregate, definition)
        if definition.family == metric_module.FAMILY_STRUCTURAL_COMPLEXITY:
            complexity_present, raw_complexity = _raw_complexity_block(view)
            if complexity_present and not isinstance(raw_complexity, Mapping):
                return metric_module.malformed_observation(
                    definition,
                    status_field="metrics.complexity.status",
                    status_value=None,
                    message="metrics.complexity must be a JSON object",
                )
            return metric_module.read_structural(
                view.complexity_aggregate, definition,
                block_status=view.complexity_status,
                block_present=view.complexity_block is not None,
            )
        complexity_present, raw_complexity = _raw_complexity_block(view)
        if complexity_present and not isinstance(raw_complexity, Mapping):
            return metric_module.malformed_observation(
                definition,
                status_field="metrics.complexity.cognitive_measurement_state",
                status_value=None,
                message="metrics.complexity must be a JSON object",
            )
        return metric_module.read_cognitive(
            self._cognitive_aggregate(view.cognitive_values), definition,
            cognitive_state=view.cognitive_state,
            ledger_present=bool(self.view.has_callable_artifact),
        )

    def _cognitive_aggregate(self, values: list[Any]) -> Mapping[str, Any] | None:
        """The existing aggregate definition, applied to persisted cells.

        ``None`` when the unit has no rows at all, which is "nothing to measure"
        rather than a measured zero.
        """
        if not values:
            return None
        return complexity_view.cognitive_aggregate(
            {"cognitive_complexity": value} for value in values
        )

    def _evaluate_language_rule(self, rule: MetricRule) -> None:
        definition = rule.definition
        for view in self._subjects_for(rule):
            self._validate_language_container(view, definition)
            for language_key in self._language_units(view, rule):
                observation = self._read_language(view, definition, language_key)
                self._emit(
                    rule, view, observation,
                    scope=metric_module.SCOPE_LANGUAGE,
                    language=view.display_language(language_key),
                )

    @staticmethod
    def _validate_language_container(
        view: SubjectView, definition: metric_module.MetricDefinition
    ) -> None:
        """Validate only the language container an enabled rule will consume."""

        if definition.family == metric_module.FAMILY_CORE:
            raw_metrics = view.result.get("metrics")
            raw_by_language = (
                raw_metrics.get("by_language")
                if isinstance(raw_metrics, Mapping) else None
            )
            if not isinstance(raw_metrics, Mapping) or not isinstance(
                raw_by_language, Mapping
            ):
                raise CheckFailed(
                    FAILURE_EVALUATION_ERROR,
                    f"language rule {definition.identifier!r} requires "
                    "metrics.by_language to be a JSON object",
                )
            return

        complexity_present, raw_complexity = _raw_complexity_block(view)
        if not complexity_present:
            return
        if not isinstance(raw_complexity, Mapping):
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                f"language rule {definition.identifier!r} requires "
                "metrics.complexity to be a JSON object",
            )
        if (
            definition.family == metric_module.FAMILY_STRUCTURAL_COMPLEXITY
            and not isinstance(raw_complexity.get("by_language"), Mapping)
        ):
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                f"language rule {definition.identifier!r} requires "
                "metrics.complexity.by_language to be a JSON object",
            )

    def _language_units(self, view: SubjectView, rule: MetricRule) -> list[str]:
        """The languages a language-scope rule evaluates for one subject.

        Every language the artifact records, plus every language the rule names
        even when the artifact records none. A rule naming Java against a
        Python-only repository must produce a visible `not_applicable`, not
        silence — silence is indistinguishable from a rule that ran and passed.
        """
        universe = set(view.language_names)
        for name in rule.languages:
            key = name.casefold()
            universe.add(key)
            view.language_names.setdefault(key, name)
        if rule.languages:
            allowed = {name.casefold() for name in rule.languages}
            universe &= allowed
        return sorted(universe)

    def _read_language(
        self,
        view: SubjectView,
        definition: metric_module.MetricDefinition,
        language_key: str,
    ) -> metric_module.Observation:
        if definition.family == metric_module.FAMILY_CORE:
            values = _lookup_language(view.core_by_language, language_key)
            return metric_module.read_core(
                values,
                definition,
                missing_completeness=(
                    metric_module.COMPLETENESS_UNAVAILABLE
                    if language_key in view.recorded_language_keys
                    else metric_module.COMPLETENESS_NOT_APPLICABLE
                ),
            )
        if definition.family == metric_module.FAMILY_STRUCTURAL_COMPLEXITY:
            values = _lookup_language(view.complexity_by_language, language_key)
            return metric_module.read_structural(
                values, definition,
                block_status=view.complexity_status,
                block_present=view.complexity_block is not None,
            )
        return metric_module.read_cognitive(
            self._cognitive_aggregate(
                view.cognitive_values_by_language.get(language_key, [])
            ),
            definition, cognitive_state=view.cognitive_state,
            ledger_present=bool(self.view.has_callable_artifact),
        )

    def _evaluate_callable_row(
        self,
        rule: MetricRule,
        view: SubjectView,
        row: Mapping[str, Any],
        bucket: dict[str, Any],
    ) -> None:
        language = row.get("detected_language")
        if not _language_allowed(
            str(language) if language else None, rule.languages
        ):
            return
        path = str(row.get("relative_path") or "").replace("\\", "/")
        if rule.paths and not _matches_path(path, rule.paths):
            return
        if rule.exclude_paths and _matches_path(path, rule.exclude_paths):
            return

        # Once the rule selects this row, validate its identity/location before
        # comparing. A malformed row must not disappear merely because its
        # metric happens not to cross the threshold.
        start_line = self._validated_ledger_integer(
            row.get("start_line"), "start_line"
        )
        end_line = self._validated_ledger_integer(row.get("end_line"), "end_line")
        if start_line < 1 or end_line < start_line:
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                "callable ledger coordinates must satisfy "
                f"1 <= start_line <= end_line; got {start_line}, {end_line}",
            )
        raw_qualified = row.get("qualified_name")
        if not isinstance(raw_qualified, str):
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                "a selected callable ledger row has a missing or non-string "
                "qualified_name",
            )
        raw_row_id = row.get("callable_row_id")
        if not isinstance(raw_row_id, str) or not raw_row_id:
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                "a selected callable ledger row has a missing or non-string "
                "callable_row_id",
            )

        bucket["matched"] += 1
        observation = metric_module.read_callable(row, rule.definition)
        if observation.error is not None:
            raise CheckFailed(
                FAILURE_EVALUATION_ERROR,
                f"malformed callable row selected by rule "
                f"{rule.identifier!r}: {observation.error}",
            )
        status, reason, observed = self._observe(rule, observation)

        if status == finding_module.STATUS_NOT_APPLICABLE:
            bucket["not_applicable"] += 1
            return
        if status == finding_module.STATUS_NOT_EVALUABLE or observed is None:
            bucket["by_reason"][reason] = bucket["by_reason"].get(reason, 0) + 1
            return

        if not finding_module.compare(observed, rule.operator, rule.threshold):
            bucket["passed"] += 1
            return

        qualified = raw_qualified
        row_id = raw_row_id
        location = finding_module.location_label(
            scope=metric_module.SCOPE_CALLABLE, subject_key=view.subject_key,
            path=path, qualified_name=qualified,
            start_line=int(start_line) if start_line is not None else None,
        )
        self._record(finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=rule.metric,
                scope=metric_module.SCOPE_CALLABLE,
                subject_key=view.subject_key,
                language=str(language) if language else None,
                path=path, callable_row_id=row_id,
            ),
            rule_id=rule.identifier, severity=rule.severity,
            status=finding_module.STATUS_VIOLATED,
            scope=metric_module.SCOPE_CALLABLE, metric=rule.metric,
            operator=rule.operator, threshold=rule.threshold,
            subject_key=view.subject_key, repository_url=view.repository_url,
            language=str(language) if language else None,
            path=path, callable_row_id=row_id, callable_qualified_name=qualified,
            start_line=int(start_line) if start_line is not None else None,
            end_line=int(end_line) if end_line is not None else None,
            observed_value=observed,
            value_status=observation.status_value,
            value_status_field=observation.status_field,
            data_completeness=observation.completeness,
            message=finding_module.violation_message(
                metric=rule.metric, observed=observed,
                operator=rule.operator, threshold=rule.threshold,
                location=location, data_completeness=observation.completeness,
                custom=rule.message,
            ),
            provenance=self._provenance(view),
        ))
        self.tallies[rule.identifier].record(finding_module.STATUS_VIOLATED)

    def _finish_callable_rule(
        self, rule: MetricRule, view: SubjectView, bucket: dict[str, Any]
    ) -> None:
        """Emit the collapsed non-evaluable and not-applicable findings.

        One finding per (rule, subject, reason), never one per row. A repository
        whose parse failed produces one honest sentence instead of ten thousand
        identical ones — and the row count travels with it, so nothing is hidden
        by the collapse.
        """
        tally = self.tallies[rule.identifier]
        tally.record(finding_module.STATUS_PASSED, bucket["passed"])

        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )

        if not self.view.has_callable_artifact:
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_EVALUABLE,
                finding_module.REASON_CALLABLE_ARTIFACT_ABSENT, 0, location,
                completeness=metric_module.COMPLETENESS_ABSENT,
                evidence={
                    "matched_callable_rows": 0,
                    "subject_callable_rows": view.callable_row_count,
                },
            ))
            tally.record(finding_module.STATUS_NOT_EVALUABLE)
            return

        for reason, count in sorted(bucket["by_reason"].items()):
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_EVALUABLE, reason, count,
                location, completeness=metric_module.COMPLETENESS_UNAVAILABLE,
                evidence={
                    "collapsed_callable_rows": count,
                    "matched_callable_rows": bucket["matched"],
                    "subject_callable_rows": view.callable_row_count,
                },
            ))
            tally.record(finding_module.STATUS_NOT_EVALUABLE, count)

        if bucket["not_applicable"] or bucket["matched"] == 0:
            count = bucket["not_applicable"]
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_APPLICABLE,
                finding_module.REASON_NOTHING_TO_MEASURE, count, location,
                completeness=metric_module.COMPLETENESS_NOT_APPLICABLE,
                evidence={
                    "collapsed_callable_rows": count,
                    "matched_callable_rows": bucket["matched"],
                    "subject_callable_rows": view.callable_row_count,
                },
            ))
            tally.record(
                finding_module.STATUS_NOT_APPLICABLE, max(count, 1)
            )

    def _collapsed_finding(
        self,
        rule: MetricRule,
        view: SubjectView,
        status: str,
        reason: str,
        count: int,
        location: str,
        *,
        completeness: str,
        evidence: Mapping[str, Any],
        scope: str = metric_module.SCOPE_CALLABLE,
        unit: str = "callable",
    ) -> finding_module.Finding:
        if status == finding_module.STATUS_NOT_APPLICABLE:
            message = finding_module.not_applicable_message(
                metric=rule.metric, location=location, row_count=count,
                unit=unit,
            )
        else:
            message = finding_module.not_evaluable_message(
                metric=rule.metric, location=location, reason=reason,
                row_count=count, unit=unit,
            )
        return finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=rule.metric, scope=scope,
                subject_key=view.subject_key, discriminator=reason,
            ),
            rule_id=rule.identifier, severity=rule.severity, status=status,
            scope=scope, metric=rule.metric,
            operator=rule.operator, threshold=rule.threshold,
            subject_key=view.subject_key, repository_url=view.repository_url,
            data_completeness=completeness, reason=reason, message=message,
            evidence=dict(evidence), provenance=self._provenance(view),
        )

    # -- hotspot file scope ------------------------------------------------

    #: The noun a collapsed hotspot finding counts, so its sentence does not
    #: report a number of callables that were never involved.
    _HOTSPOT_UNIT = "hotspot file"

    def _evaluate_hotspot_file_rule(self, rule: MetricRule) -> None:
        """One evaluation unit per hotspot row, collapsed exactly like callables.

        A repository whose git history was unavailable produces ONE honest
        sentence and a row count, not one identical finding per file.
        """
        for view in self._subjects_for(rule):
            coverage, reason = self._hotspot_coverage(view.subject_key)
            if coverage != "measured":
                self._collapse_hotspot_subject(rule, view, coverage, reason)
                continue

            bucket: dict[str, Any] = {
                "matched": 0, "passed": 0, "not_applicable": 0, "by_reason": {},
            }
            for row in self._hotspot_rows.get(view.subject_key, ()):
                self._evaluate_hotspot_row(rule, view, row, bucket)
            self._finish_hotspot_file_rule(rule, view, bucket)

    def _collapse_hotspot_subject(
        self, rule: MetricRule, view: SubjectView, coverage: str,
        reason: str | None,
    ) -> None:
        """One finding for a subject with no readable hotspot rows at all."""
        completeness = metric_module.hotspot_completeness_of_status(coverage)
        status = (
            finding_module.STATUS_NOT_APPLICABLE
            if completeness == metric_module.COMPLETENESS_NOT_APPLICABLE
            else finding_module.STATUS_NOT_EVALUABLE
        )
        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )
        self._record(self._collapsed_finding(
            rule, view, status,
            reason or finding_module.REASON_NOTHING_TO_MEASURE, 0, location,
            completeness=completeness,
            evidence={
                "hotspot_rows_for_subject": len(
                    self._hotspot_rows.get(view.subject_key, ())
                ),
                "hotspot_evidence_admission": self._hotspot_admission(),
            },
            scope=metric_module.SCOPE_HOTSPOT_FILE,
            unit=self._HOTSPOT_UNIT,
        ))
        self.tallies[rule.identifier].record(status)

    def _evaluate_hotspot_row(
        self,
        rule: MetricRule,
        view: SubjectView,
        row: Mapping[str, Any],
        bucket: dict[str, Any],
    ) -> None:
        language = row.get("language")
        if not _language_allowed(
            str(language) if language else None, rule.languages
        ):
            return
        path = str(row.get("file") or "").replace("\\", "/")
        if rule.paths and not _matches_path(path, rule.paths):
            return
        if rule.exclude_paths and _matches_path(path, rule.exclude_paths):
            return

        bucket["matched"] += 1
        observation = metric_module.read_hotspot_file(row, rule.definition)
        status, reason, observed = self._observe(rule, observation)

        if status == finding_module.STATUS_NOT_APPLICABLE:
            bucket["not_applicable"] += 1
            return
        if status == finding_module.STATUS_NOT_EVALUABLE or observed is None:
            bucket["by_reason"][reason] = bucket["by_reason"].get(reason, 0) + 1
            return
        if not finding_module.compare(observed, rule.operator, rule.threshold):
            bucket["passed"] += 1
            return

        location = finding_module.location_label(
            scope=metric_module.SCOPE_HOTSPOT_FILE,
            subject_key=view.subject_key, path=path,
        )
        self._record(finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=rule.metric,
                scope=metric_module.SCOPE_HOTSPOT_FILE,
                subject_key=view.subject_key,
                language=str(language) if language else None,
                path=path,
            ),
            rule_id=rule.identifier, severity=rule.severity,
            status=finding_module.STATUS_VIOLATED,
            scope=metric_module.SCOPE_HOTSPOT_FILE, metric=rule.metric,
            operator=rule.operator, threshold=rule.threshold,
            subject_key=view.subject_key, repository_url=view.repository_url,
            language=str(language) if language else None,
            path=path,
            observed_value=observed,
            value_status=observation.status_value,
            value_status_field=observation.status_field,
            data_completeness=observation.completeness,
            message=finding_module.violation_message(
                metric=rule.metric, observed=observed,
                operator=rule.operator, threshold=rule.threshold,
                location=location, data_completeness=observation.completeness,
                custom=rule.message,
            ),
            evidence=_hotspot_row_evidence(row),
            provenance=self._provenance(view),
        ))
        self.tallies[rule.identifier].record(finding_module.STATUS_VIOLATED)

    def _finish_hotspot_file_rule(
        self, rule: MetricRule, view: SubjectView, bucket: dict[str, Any]
    ) -> None:
        tally = self.tallies[rule.identifier]
        tally.record(finding_module.STATUS_PASSED, bucket["passed"])
        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )
        subject_rows = len(self._hotspot_rows.get(view.subject_key, ()))

        for reason, count in sorted(bucket["by_reason"].items()):
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_EVALUABLE, reason, count,
                location, completeness=metric_module.COMPLETENESS_UNAVAILABLE,
                evidence={
                    "collapsed_hotspot_rows": count,
                    "matched_hotspot_rows": bucket["matched"],
                    "subject_hotspot_rows": subject_rows,
                },
                scope=metric_module.SCOPE_HOTSPOT_FILE,
                unit=self._HOTSPOT_UNIT,
            ))
            tally.record(finding_module.STATUS_NOT_EVALUABLE, count)

        if bucket["not_applicable"] or bucket["matched"] == 0:
            count = bucket["not_applicable"]
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_APPLICABLE,
                finding_module.REASON_NOTHING_TO_MEASURE, count, location,
                completeness=metric_module.COMPLETENESS_NOT_APPLICABLE,
                evidence={
                    "collapsed_hotspot_rows": count,
                    "matched_hotspot_rows": bucket["matched"],
                    "subject_hotspot_rows": subject_rows,
                },
                scope=metric_module.SCOPE_HOTSPOT_FILE,
                unit=self._HOTSPOT_UNIT,
            ))
            tally.record(finding_module.STATUS_NOT_APPLICABLE, max(count, 1))

    # -- duplication group scope -------------------------------------------

    #: The noun a collapsed duplication finding counts, so its sentence does not
    #: report a number of callables that were never involved.
    _DUPLICATION_UNIT = "duplication group"

    def _evaluate_duplication_group_rule(self, rule: MetricRule) -> None:
        """One evaluation unit per clone group, collapsed exactly like callables.

        **The kind's persisted status decides whether the population exists, and
        it is consulted BEFORE the group list is touched.** Under
        `not_requested` and under `failed` the document carries an empty group
        list, which by length alone is indistinguishable from a subject that
        genuinely has no clones. Iterating first and concluding "no groups, so
        nothing to report" would convert a non-measurement into a silent pass --
        the single most dangerous mistake available in this integration.
        """
        kind, _field = metric_module.DUPLICATION_GROUP_SOURCES[rule.metric]
        for view in self._subjects_for(rule):
            coverage, reason = self._duplication_coverage(view.subject_key)
            if coverage != "complete":
                self._collapse_duplication_subject(
                    rule, view, coverage, reason, kind
                )
                continue

            kind_status = self._duplication_kind_status(view.subject_key, kind)
            completeness = metric_module.duplication_completeness_of_status(
                kind_status
            )
            if completeness not in (
                metric_module.COMPLETENESS_COMPLETE,
                metric_module.COMPLETENESS_PARTIAL,
            ):
                self._collapse_duplication_kind(
                    rule, view, kind, kind_status, completeness
                )
                continue

            bucket: dict[str, Any] = {
                "matched": 0, "passed": 0, "not_applicable": 0, "by_reason": {},
            }
            for group in self._duplication_groups.get(
                view.subject_key, {}
            ).get(kind, ()):
                self._evaluate_duplication_group(
                    rule, view, group, kind, kind_status, bucket
                )
            self._finish_duplication_group_rule(rule, view, kind, bucket)

    def _collapse_duplication_subject(
        self, rule: MetricRule, view: SubjectView, coverage: str,
        reason: str | None, kind: str,
    ) -> None:
        """One finding for a subject no admitted document speaks for."""
        completeness = metric_module.duplication_completeness_of_status(coverage)
        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )
        self._record(self._collapsed_finding(
            rule, view, finding_module.STATUS_NOT_EVALUABLE,
            reason or finding_module.REASON_MEASUREMENT_UNAVAILABLE, 0, location,
            completeness=completeness,
            evidence={
                "duplication_kind": kind,
                "duplication_evidence_admission": self._duplication_admission(),
                "duplication_bound_subjects": sorted(self._duplication_covered),
            },
            scope=metric_module.SCOPE_DUPLICATION_GROUP,
            unit=self._DUPLICATION_UNIT,
        ))
        self.tallies[rule.identifier].record(
            finding_module.STATUS_NOT_EVALUABLE
        )

    def _collapse_duplication_kind(
        self, rule: MetricRule, view: SubjectView, kind: str,
        kind_status: Any, completeness: str,
    ) -> None:
        """One finding for a kind this analysis did not measure.

        The count reported is 0 groups, and that is the honest number: the
        document carries no group of this kind BECAUSE the kind was not
        measured, which is a different fact from a measured zero and is why this
        never becomes a pass.
        """
        status = (
            finding_module.STATUS_NOT_APPLICABLE
            if completeness == metric_module.COMPLETENESS_NOT_APPLICABLE
            else finding_module.STATUS_NOT_EVALUABLE
        )
        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )
        self._record(self._collapsed_finding(
            rule, view, status, self._duplication_kind_reason(kind_status), 0,
            location, completeness=completeness,
            evidence={
                "duplication_kind": kind,
                "duplication_kind_status": kind_status,
                "duplication_kind_status_field": f"counts.{kind}.status",
                "duplication_evidence_admission": self._duplication_admission(),
            },
            scope=metric_module.SCOPE_DUPLICATION_GROUP,
            unit=self._DUPLICATION_UNIT,
        ))
        self.tallies[rule.identifier].record(status)

    def _evaluate_duplication_group(
        self,
        rule: MetricRule,
        view: SubjectView,
        group: Mapping[str, Any],
        kind: str,
        kind_status: Any,
        bucket: dict[str, Any],
    ) -> None:
        language = group.get("language")
        if not _language_allowed(
            str(language) if language else None, rule.languages
        ):
            return

        bucket["matched"] += 1
        observation = metric_module.read_duplication_group(
            group, rule.definition, kind_status=kind_status
        )
        status, reason, observed = self._observe(rule, observation)

        if status == finding_module.STATUS_NOT_APPLICABLE:
            bucket["not_applicable"] += 1
            return
        if status == finding_module.STATUS_NOT_EVALUABLE or observed is None:
            bucket["by_reason"][reason] = bucket["by_reason"].get(reason, 0) + 1
            return
        if not finding_module.compare(observed, rule.operator, rule.threshold):
            bucket["passed"] += 1
            return

        occurrences = [
            item for item in (group.get("occurrences") or ())
            if isinstance(item, Mapping)
        ]
        primary = occurrences[0] if occurrences else {}
        path = str(primary.get("path") or "").replace("\\", "/") or None
        start_line = primary.get("start_line")
        end_line = primary.get("end_line")
        fingerprint = str(group.get("fingerprint") or "")

        location = finding_module.location_label(
            scope=metric_module.SCOPE_DUPLICATION_GROUP,
            subject_key=view.subject_key, path=path,
            start_line=start_line if isinstance(start_line, int) else None,
        )
        self._record(finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=rule.metric,
                scope=metric_module.SCOPE_DUPLICATION_GROUP,
                subject_key=view.subject_key,
                language=str(language) if language else None,
                # `path` is deliberately NOT an identity component. A clone
                # group spans several files by definition, so feeding one of
                # them into identity would make the id depend on canonical
                # ordering and an unrelated rename could orphan the finding.
                # The discriminator is the canonicalized-body fingerprint, which
                # does not move when an edit shifts a line above the clone --
                # `group_id` would, because it digests every occurrence's
                # coordinates, and an id that changes on every neighbouring edit
                # cannot support suppression or history.
                discriminator=f"{kind}:{fingerprint}",
            ),
            rule_id=rule.identifier, severity=rule.severity,
            status=finding_module.STATUS_VIOLATED,
            scope=metric_module.SCOPE_DUPLICATION_GROUP, metric=rule.metric,
            operator=rule.operator, threshold=rule.threshold,
            subject_key=view.subject_key, repository_url=view.repository_url,
            language=str(language) if language else None,
            path=path,
            start_line=start_line if isinstance(start_line, int) else None,
            end_line=end_line if isinstance(end_line, int) else None,
            observed_value=observed,
            value_status=observation.status_value,
            value_status_field=observation.status_field,
            data_completeness=observation.completeness,
            message=finding_module.violation_message(
                metric=rule.metric, observed=observed,
                operator=rule.operator, threshold=rule.threshold,
                location=location, data_completeness=observation.completeness,
                custom=rule.message,
            ),
            evidence=_duplication_group_evidence(group, kind),
            provenance=self._provenance(view),
        ))
        self.tallies[rule.identifier].record(finding_module.STATUS_VIOLATED)

    def _finish_duplication_group_rule(
        self, rule: MetricRule, view: SubjectView, kind: str,
        bucket: dict[str, Any],
    ) -> None:
        tally = self.tallies[rule.identifier]
        tally.record(finding_module.STATUS_PASSED, bucket["passed"])
        location = finding_module.location_label(
            scope=metric_module.SCOPE_REPOSITORY, subject_key=view.subject_key
        )
        subject_groups = len(
            self._duplication_groups.get(view.subject_key, {}).get(kind, ())
        )

        for reason, count in sorted(bucket["by_reason"].items()):
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_EVALUABLE, reason, count,
                location, completeness=metric_module.COMPLETENESS_UNAVAILABLE,
                evidence={
                    "duplication_kind": kind,
                    "collapsed_duplication_groups": count,
                    "matched_duplication_groups": bucket["matched"],
                    "subject_duplication_groups": subject_groups,
                },
                scope=metric_module.SCOPE_DUPLICATION_GROUP,
                unit=self._DUPLICATION_UNIT,
            ))
            tally.record(finding_module.STATUS_NOT_EVALUABLE, count)

        if bucket["not_applicable"] or bucket["matched"] == 0:
            # `matched == 0` with a MEASURED kind is a measured absence: the
            # analysis looked and found no clone group of this kind, so there
            # was no unit to evaluate. That is `not_applicable` and never a
            # violation -- and it is reachable only after the kind status has
            # already been confirmed evaluable, which is what keeps it apart
            # from "the kind was never run".
            count = bucket["not_applicable"]
            self._record(self._collapsed_finding(
                rule, view, finding_module.STATUS_NOT_APPLICABLE,
                finding_module.REASON_NOTHING_TO_MEASURE, count, location,
                completeness=metric_module.COMPLETENESS_NOT_APPLICABLE,
                evidence={
                    "duplication_kind": kind,
                    "collapsed_duplication_groups": count,
                    "matched_duplication_groups": bucket["matched"],
                    "subject_duplication_groups": subject_groups,
                },
                scope=metric_module.SCOPE_DUPLICATION_GROUP,
                unit=self._DUPLICATION_UNIT,
            ))
            tally.record(finding_module.STATUS_NOT_APPLICABLE, max(count, 1))

    def _emit(
        self,
        rule: MetricRule,
        view: SubjectView,
        observation: metric_module.Observation,
        *,
        scope: str,
        language: str | None = None,
        reason_override: str | None = None,
    ) -> None:
        """Evaluate one repository- or language-scope unit and record the outcome.

        ``reason_override`` replaces the generic reason for a unit whose
        non-evaluability has a more specific cause the reader needs -- "no
        evidence document was supplied" rather than "the measurement is
        unavailable". It never changes the STATUS, only the explanation: a rule
        that could not run still could not run.
        """
        tally = self.tallies[rule.identifier]
        status, reason, observed = self._observe(rule, observation)
        if reason_override is not None and status != finding_module.STATUS_PASSED:
            reason = reason_override
        location = finding_module.location_label(
            scope=scope, subject_key=view.subject_key, language=language
        )

        if status == finding_module.STATUS_PASSED and observed is not None:
            if not finding_module.compare(observed, rule.operator, rule.threshold):
                tally.record(finding_module.STATUS_PASSED)
                return
            status = finding_module.STATUS_VIOLATED
            message = finding_module.violation_message(
                metric=rule.metric, observed=observed,
                operator=rule.operator, threshold=rule.threshold,
                location=location, data_completeness=observation.completeness,
                custom=rule.message,
            )
        elif status == finding_module.STATUS_NOT_APPLICABLE:
            message = finding_module.not_applicable_message(
                metric=rule.metric, location=location
            )
        elif status == finding_module.STATUS_EVALUATION_ERROR:
            message = (
                f"cannot evaluate {rule.metric} at {location}: "
                f"{observation.error}"
            )
        else:
            message = finding_module.not_evaluable_message(
                metric=rule.metric, location=location, reason=reason
            )

        self._record(finding_module.Finding(
            finding_id=finding_module.finding_identity(
                rule_id=rule.identifier, metric=rule.metric, scope=scope,
                subject_key=view.subject_key, language=language,
            ),
            rule_id=rule.identifier, severity=rule.severity, status=status,
            scope=scope, metric=rule.metric, operator=rule.operator,
            threshold=rule.threshold, subject_key=view.subject_key,
            repository_url=view.repository_url, language=language,
            observed_value=observed,
            value_status=observation.status_value,
            value_status_field=observation.status_field,
            data_completeness=observation.completeness, reason=reason,
            message=message, provenance=self._provenance(view),
        ))
        tally.record(status)

    # -- waivers, provenance, assembly -------------------------------------

    def _waiver_for(self, finding: finding_module.Finding) -> Waiver | None:
        """The first live waiver covering this finding, if any.

        A waiver never applies to a `not_evaluable` finding. Waiving a rule that
        did not run would suppress the statement that it did not run, which is
        precisely the silence the status exists to prevent — a waiver excuses a
        known result, not an absent one.
        """
        if finding.status != finding_module.STATUS_VIOLATED:
            return None
        for waiver in self.policy.waivers:
            if waiver.rule_id != finding.rule_id:
                continue
            if (
                waiver.subject_key is not None
                and waiver.subject_key != finding.subject_key
            ):
                continue
            if waiver.expired(self.today):
                continue
            return waiver
        return None

    def _record(self, finding: finding_module.Finding) -> None:
        waiver = self._waiver_for(finding)
        if waiver is None:
            self.findings.append(finding)
            return
        self.waived.append(replace(finding, waiver=waiver.as_dict()))

    def _provenance(self, view: SubjectView | None = None) -> dict[str, Any]:
        """Enough context to know what a number means, without copying the run."""
        payload: dict[str, Any] = {
            "run_id": self.view.run_id,
            "artifact_schema_version": self.manifest.get("artifact_schema_version"),
            "program_version": self.manifest.get("program_version"),
            "metric_contract_version": self.manifest.get("metric_contract_version"),
            "complexity_contract_version": self.manifest.get(
                "complexity_contract_version"
            ),
            "exclusion_policy_version": self.manifest.get("exclusion_policy_version"),
        }
        if view is not None:
            acquisition = view.result.get("acquisition") or {}
            payload["analysis_scope_hash"] = view.result.get("analysis_scope_hash")
            payload["analyzed_commit_sha"] = acquisition.get("analyzed_commit_sha")
            payload["source_mode"] = view.result.get("source_mode")
            payload["analysis_status"] = view.result.get("analysis_status")
        return payload

    def _observe(self, rule, observation):
        outcome = _outcome_for(observation, self.policy)
        if outcome[0] == finding_module.STATUS_PASSED and observation.completeness == metric_module.COMPLETENESS_PARTIAL:
            self.partial_evaluated_metrics.add(rule.metric)
        return outcome

    # -- result ------------------------------------------------------------

    def result(self) -> dict[str, Any]:
        ordered = finding_module.sort_findings(self.findings)
        waived = finding_module.sort_findings(self.waived)

        by_status = {status: 0 for status in finding_module.STATUSES}
        by_severity = {severity: 0 for severity in rule_module.SEVERITIES}
        for item in ordered:
            by_status[item.status] += 1
            by_severity[item.severity] += 1

        fail_on_not_evaluable = (
            self.policy.options.on_not_evaluable == ON_NOT_EVALUABLE_FAIL
        )
        failing = [
            item for item in ordered
            if finding_module.requires_failure(
                severity=item.severity,
                status=item.status,
                fail_on_not_evaluable=fail_on_not_evaluable,
            )
        ]
        errored = [
            item for item in ordered
            if item.status == finding_module.STATUS_EVALUATION_ERROR
        ]

        failure_message = None
        if errored:
            # An evaluator failure is not a policy result. It exits 2 rather
            # than 1, because "the gate found a problem" and "the gate broke"
            # must never be the same signal to a CI step.
            verdict, exit_code, failure_kind = (
                VERDICT_ERROR, EXIT_ERROR, FAILURE_EVALUATION_ERROR
            )
            failure_message = (
                f"{len(errored)} rule(s) failed to evaluate; the first was "
                f"{errored[0].rule_id!r}: {errored[0].message}"
            )
        elif failing:
            verdict, exit_code, failure_kind = VERDICT_FAIL, EXIT_VIOLATION, None
        else:
            verdict, exit_code, failure_kind = VERDICT_PASS, EXIT_PASS, None

        from modules.ratchet.check_service import not_configured_summary

        return {
            "check_result_format_version": CHECK_RESULT_FORMAT_VERSION,
            "archlens_version": PROGRAM_VERSION,
            "evaluated_at": self.today.isoformat(),
            "verdict": verdict,
            "exit_code": exit_code,
            "failure_kind": failure_kind,
            "failure_message": failure_message,
            "policy": self._policy_identity(),
            "run": self._run_identity(),
            "counts": {
                "findings": len(ordered),
                "waived": len(waived),
                "failing": len(failing),
                "rules_evaluated": len(self.tallies),
                # Over EMITTED findings. `passed` is always 0 here, because a
                # pass is counted and never enumerated; the honest pass count is
                # in `units` and per rule in `rules[]`.
                "by_status": by_status,
                "by_severity": by_severity,
                # Over every evaluation unit, including the ones that passed.
                "units": self._unit_totals(),
            },
            "evidence": self._evidence_summary(),
            "evaluated_input_provenance": self._evaluated_input_provenance(),
            "rules": self._rule_summaries(),
            "findings": [item.as_dict() for item in ordered],
            "waived_findings": [item.as_dict() for item in waived],
            "expired_waivers": [
                waiver.as_dict() for waiver in self.policy.waivers
                if waiver.expired(self.today)
            ],
            "not_evaluable": [
                {
                    "finding_id": item.finding_id,
                    "rule_id": item.rule_id,
                    "metric": item.metric,
                    "scope": item.scope,
                    "subject_key": item.subject_key,
                    "language": item.language,
                    "reason": item.reason,
                    "reason_meaning": finding_module.REASON_MEANINGS.get(
                        item.reason or "", ""
                    ),
                    "fails_the_build": (
                        finding_module.requires_failure(
                            severity=item.severity,
                            status=item.status,
                            fail_on_not_evaluable=fail_on_not_evaluable,
                        )
                    ),
                }
                for item in ordered
                if item.status == finding_module.STATUS_NOT_EVALUABLE
            ],
            "provenance": self._provenance(),
            "ratchet": not_configured_summary(self.view),
            "status_meanings": dict(finding_module.STATUS_MEANINGS),
            "completeness_meanings": dict(metric_module.COMPLETENESS_MEANINGS),
            "exit_code_meanings": dict(EXIT_CODE_MEANINGS),
            "scope_note": SCOPE_NOTE,
        }

    def _evidence_summary(self) -> dict[str, Any]:
        """One admission record per evidence kind this command accepts.

        Present even when nothing was supplied, so a CI reader can always tell a
        gate that HAD evidence from one that did not -- otherwise the two are
        indistinguishable in a passing result, which is exactly what makes an
        unfed gate look healthy.

        `used_by_rules` closes the loop in both directions: a supplied document
        no rule reads is visible, and so is a rule whose evidence never arrived.
        """
        used: dict[str, list[str]] = {}
        for rule in self.policy.metric_rules:
            required = rule.definition.requires_evidence
            if required is None:
                continue
            for kind, expected in evidence_module.EVIDENCE_FORMATS.items():
                if expected == required:
                    used.setdefault(kind, []).append(rule.identifier)

        payload: dict[str, Any] = {}
        for kind in sorted(self.evidence_paths):
            record = self.evidence.get(kind) or evidence_module.not_supplied(kind)
            item = record.as_dict()
            item["used_by_rules"] = sorted(used.get(kind, ()))
            payload[kind] = item
        return payload

    def _unit_totals(self) -> dict[str, int]:
        """Outcome totals over every evaluation unit, passes included.

        `by_status` counts findings, and a pass never becomes one. Without this
        block a reader could not tell a policy that evaluated ten thousand
        callables cleanly from one whose rules matched nothing at all — and
        those are very different states for a gate to be in.
        """
        totals = _RuleTally()
        for tally in self.tallies.values():
            totals.units += tally.units
            totals.violated += tally.violated
            totals.passed += tally.passed
            totals.not_evaluable += tally.not_evaluable
            totals.not_applicable += tally.not_applicable
            totals.evaluation_error += tally.evaluation_error
        return totals.as_dict()

    def _policy_identity(self) -> dict[str, Any]:
        return {
            "name": self.policy.name,
            "description": self.policy.description,
            "policy_document_format_version": self.policy.source_format_version,
            "evaluated_as_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
            "options": self.policy.options.as_dict(),
            "integrity_rule_count": len(self.policy.integrity_rules),
            "metric_rule_count": len(self.policy.metric_rules),
            "thresholds_are_user_supplied": True,
            "expected_contracts": dict(sorted(self.policy.expected_contracts.items())),
        }

    def _evaluated_input_provenance(self) -> dict[str, Any]:
        """Bounded identities for every byte input and evaluated condition."""

        protected = bool(self.trust is not None and self.trust.protected)
        evaluator = (
            self.trust.evaluator
            if self.trust is not None
            else trust_module.EvaluatorIdentity.local(PROGRAM_VERSION)
        )
        conditions: list[dict[str, Any]] = [
            {
                "rule_id": rule_id,
                "kind": finding_module.KIND_INTEGRITY,
                "severity": severity,
            }
            for rule_id, severity in sorted(self.policy.integrity_rules.items())
        ]
        conditions.extend(
            {"kind": finding_module.KIND_METRIC, **rule.as_dict()}
            for rule in self.policy.metric_rules
        )
        conditions.sort(
            key=lambda item: (str(item.get("kind")), str(item.get("rule_id") or item.get("id")))
        )
        evidence: dict[str, Any] = {}
        for kind in sorted(self.evidence_paths):
            record = self.evidence.get(kind) or evidence_module.not_supplied(kind)
            evidence[kind] = {
                "state": record.trust_state,
                "sha256": record.document_sha256,
                "producer_identity": (
                    None if record.producer_identity is None
                    else dict(record.producer_identity)
                ),
            }
        return {
            "mode": (
                self.trust.mode if self.trust is not None
                else trust_module.LOCAL_UNPROTECTED
            ),
            "protected_gate": protected,
            "evaluator": evaluator.as_dict(protected=protected),
            "policy": {
                "sha256": None if self.trust is None else self.trust.policy_sha256,
                "trust": (
                    "externally_authenticated" if protected
                    else "unprotected_local"
                ),
            },
            "run_manifest": {"sha256": self.run_manifest_sha256},
            "evidence": evidence,
            "trusted_evidence_receipt": (
                {
                    "sha256": None,
                    "format": None,
                    "format_version": None,
                }
                if self.trust is None
                else self.trust.receipt_provenance()
            ),
            "ratchet": {
                "baseline_sha256": None,
                "source_run_manifest_sha256": None,
            },
            "rule_conditions": {
                "count": len(conditions),
                "sha256": trust_module.rule_condition_identity(conditions),
            },
        }

    def _run_identity(self) -> dict[str, Any]:
        qualification = self.view.benchmark_qualification
        readiness = self.view.benchmark_of_record_readiness
        return {
            "run_id": self.view.run_id,
            "run_status": self.status.get("status"),
            "lifecycle": getattr(self.view.lifecycle, "value", None),
            "artifact_schema_version": self.manifest.get("artifact_schema_version"),
            # `null` means NOT ASSESSED -- no enabled integrity rule needed
            # the structural verdict, so none was computed. It never means valid.
            "schema_validation_result": (
                self.schema_report.get("result")
                if self._schema_report_loaded else None
            ),
            "subjects": [
                {
                    "subject_key": view.subject_key,
                    "repository_url": view.repository_url,
                    "analysis_status": view.result.get("analysis_status"),
                    "core_metric_status": view.result.get("core_metric_status"),
                }
                for view in self._subjects.values()
            ],
            # Contextual provenance only. `check` is a generic command: this can
            # move no metric, no threshold, no status and no verdict, and a
            # generic bundle that declares nothing is not less checkable for it.
            "benchmark_qualification": {
                "qualification_mode": self.view.qualification_mode,
                "qualification_artifact_present": qualification is not None,
                "benchmark_of_record_status": (
                    readiness.get("status") if readiness else None
                ),
                "note": (
                    "Contextual provenance only. `metrolith check` requires no "
                    "benchmark qualification and no qualification value "
                    "influences any finding."
                ),
            },
        }

    def _rule_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for rule in self.policy.metric_rules:
            definition = rule.definition
            summaries.append({
                "rule_id": rule.identifier,
                "kind": finding_module.KIND_METRIC,
                "severity": rule.severity,
                "metric": rule.metric,
                "metric_source": definition.source,
                "operator": rule.operator,
                "threshold": rule.threshold,
                "scope": rule.scope,
                "domain": None,
                **self.tallies.get(rule.identifier, _RuleTally()).as_dict(),
            })
        for rule_id, severity in sorted(self.policy.integrity_rules.items()):
            rule = rule_module.RULES_BY_ID[rule_id]
            summaries.append({
                "rule_id": rule_id,
                "kind": finding_module.KIND_INTEGRITY,
                "severity": severity,
                "metric": None,
                "metric_source": None,
                "operator": None,
                "threshold": None,
                "scope": finding_module.SCOPE_RUN,
                "domain": rule.domain,
                **self.tallies.get(rule_id, _RuleTally()).as_dict(),
            })
        summaries.sort(key=lambda item: (item["kind"], item["rule_id"]))
        return summaries


def _lookup_language(
    values: Mapping[str, Any], language_key: str
) -> Mapping[str, Any] | None:
    """Case-insensitive lookup into one of the producer's by-language maps."""
    for recorded, payload in values.items():
        if str(recorded).casefold() == language_key:
            return payload if isinstance(payload, Mapping) else None
    return None


def evaluate_check(
    run_directory: Path,
    policy: PolicyV2Document,
    *,
    today: date | None = None,
    hotspots: Path | None = None,
    duplication: Path | None = None,
    ratchet: RatchetCheckRequest | None = None,
    trust: trust_module.CheckTrustContext | None = None,
) -> dict[str, Any]:
    """Evaluate one run against one policy. Raises :class:`CheckFailed` on exit-2.

    ``hotspots`` and ``duplication`` optionally supply standalone evidence
    documents. A document that fails admission never raises and never changes
    the exit-code contract: it is recorded, and the rules that needed it report
    `not_evaluable`.
    """
    evaluation = CheckEvaluation(
        run_directory, policy, today=today, hotspots=hotspots,
        duplication=duplication, trust=trust,
    )
    evaluation.open()
    trust_failure = evaluation.protected_trust_failure()
    if trust_failure is not None:
        result = evaluation.result()
        result["verdict"] = VERDICT_ERROR
        result["exit_code"] = EXIT_ERROR
        result["failure_kind"] = FAILURE_TRUST_ADMISSION
        result["failure_message"] = trust_failure
        return result
    evaluation.evaluate_integrity()
    evaluation.evaluate_metrics()
    # Ratchet admission, comparison, finding projection and summary merging all
    # live behind this service.  `check.py` remains the lifecycle orchestrator.
    from modules.ratchet.check_service import integrate_ratchet_check_result

    return PresentedCheckResult(
        integrate_ratchet_check_result(evaluation.result(), evaluation.view, ratchet),
        partial_evaluated_metrics=evaluation.partial_evaluated_metrics,
    )


def failure_result(
    *,
    kind: str,
    message: str,
    run_directory: Path | None,
    policy_name: str | None,
    today: date | None = None,
    trust: trust_module.CheckTrustContext | None = None,
    requested_mode: str | None = None,
) -> dict[str, Any]:
    """The machine-readable document for an evaluation that could not happen.

    A caller parsing JSON gets the same shape whether the gate ran or not, so a
    CI step never has to distinguish "no output" from "clean pass" — the two
    outcomes that are most dangerous to confuse.
    """
    return {
        "check_result_format_version": CHECK_RESULT_FORMAT_VERSION,
        "archlens_version": PROGRAM_VERSION,
        "evaluated_at": (today or date.today()).isoformat(),
        "verdict": VERDICT_ERROR,
        "exit_code": EXIT_ERROR,
        "failure_kind": kind,
        "failure_message": message,
        "policy": {
            "name": policy_name,
            "description": "",
            "policy_document_format_version": None,
            "evaluated_as_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
            "options": None,
            "integrity_rule_count": 0,
            "metric_rule_count": 0,
            "thresholds_are_user_supplied": True,
            "expected_contracts": {},
        },
        "run": {
            "run_id": None,
            "run_status": None,
            "lifecycle": None,
            "artifact_schema_version": None,
            "schema_validation_result": None,
            "subjects": [],
            "benchmark_qualification": None,
        },
        "counts": {
            "findings": 0, "waived": 0, "failing": 0, "rules_evaluated": 0,
            "by_status": {status: 0 for status in finding_module.STATUSES},
            "by_severity": {
                severity: 0 for severity in rule_module.SEVERITIES
            },
            "units": _RuleTally().as_dict(),
        },
        # Same shape as a successful evaluation, so a CI step parsing JSON
        # never has to tell "no output" apart from "clean pass". Empty means no
        # evidence was even considered, because no evaluation happened.
        "evidence": {},
        "evaluated_input_provenance": {
            "mode": (
                trust.mode if trust is not None
                else requested_mode or trust_module.LOCAL_UNPROTECTED
            ),
            "protected_gate": bool(trust is not None and trust.protected),
            "evaluator": (
                trust.evaluator.as_dict(protected=trust.protected)
                if trust is not None
                else trust_module.EvaluatorIdentity.local(PROGRAM_VERSION).as_dict(
                    protected=False
                )
            ),
            "policy": {
                "sha256": None if trust is None else trust.policy_sha256,
                "trust": (
                    "externally_authenticated"
                    if trust is not None and trust.protected
                    else "unprotected_local"
                ),
            },
            "run_manifest": {"sha256": None},
            "evidence": {},
            "trusted_evidence_receipt": (
                {
                    "sha256": None,
                    "format": None,
                    "format_version": None,
                }
                if trust is None
                else trust.receipt_provenance()
            ),
            "ratchet": {
                "baseline_sha256": None,
                "source_run_manifest_sha256": None,
            },
            "rule_conditions": {"count": 0, "sha256": None},
        },
        "rules": [],
        "findings": [],
        "waived_findings": [],
        "expired_waivers": [],
        "not_evaluable": [],
        "provenance": {},
        "ratchet": {
            "configured": False,
            "status": "not_configured",
            "admission_status": "not_requested",
            "baseline": None,
            "current": None,
            "paired_subject_count": 0,
            "evaluated_count": 0,
            "violated_count": 0,
            "not_evaluable_count": 0,
            "error": None,
        },
        "status_meanings": dict(finding_module.STATUS_MEANINGS),
        "completeness_meanings": dict(metric_module.COMPLETENESS_MEANINGS),
        "exit_code_meanings": dict(EXIT_CODE_MEANINGS),
        "scope_note": SCOPE_NOTE,
    }


__all__ = [
    "CHECK_RESULT_FORMAT_VERSION", "EXIT_PASS", "EXIT_VIOLATION", "EXIT_ERROR",
    "EXIT_CODE_MEANINGS", "FAILURE_KINDS", "FAILURE_KIND_MEANINGS",
    "FAILURE_RATCHET_ADMISSION", "FAILURE_RATCHET_EVALUATION",
    "FAILURE_TRUST_ADMISSION",
    "CheckFailed", "PolicyDocumentInvalid", "evaluate_check", "failure_result",
]
