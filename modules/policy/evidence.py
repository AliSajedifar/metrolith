"""Admission of standalone evidence documents into Policy evaluation.

This is the trusted boundary, and nothing else. It decides whether a supplied
Duplication or Hotspot document may speak for a run at all; it reads no metric
out of one, computes no aggregate, and emits no finding. Those belong to a later
phase and must be built on top of this, never beside it.

**Two independent gates, both of which must pass.**

1. *Contract compatibility* -- the document's ``format``/``format_version`` must
   be an active exact-version identity in :mod:`modules.standalone_contracts`,
   its declared analysis contract version must be the one this build reads, and
   its own validator must accept it.
2. *Provenance and binding* -- the document must describe the same thing this
   run describes, and it must be possible to say **which subject** it speaks
   for. A document that could describe either of two subjects is refused, not
   assigned to one of them.

The order is fixed: identity, then the document's own validator, then
provenance. Checking provenance first would mean reading fields out of a
document nothing has yet accepted as well-formed.

**Three prohibitions, each structural rather than remembered.**

*Never choose the first matching subject.* :func:`_bind_duplication` collects
every candidate and refuses an ambiguous set. Picking one would silently
attribute a snapshot's duplication to an arbitrary subject, and this gate's
output is meant to authorize a merge.

*Never convert missing evidence to zero.* An :class:`EvidenceRecord` carries a
document **only** when the admission is ``admitted`` -- enforced in
``__post_init__``, so a caller structurally cannot reach a number through a
rejected record. A missing or rejected document is an absence, and an absence is
not a count of nothing.

*Never silently ignore invalid evidence.* Every rejection produces a record with
a closed-vocabulary admission, a typed reason, its published meaning, and the
values that were compared. There is no path that drops a supplied document
without saying so.

**This module imports no duplication internals, no hotspot internals and no
SARIF.** Validators are reached only through the standalone contract registry,
so admission can never drift from the registry's idea of what is active, and the
policy layer never grows a second opinion about what a duplicate group or a
hotspot is. The document fields read below (``source.resolved_revision``,
``source_run.run_id``) are the published surface of those contracts, not their
internals.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from modules.admission import (
    ADMISSION_STATES,
    ADMITTED,
    CONTRACT_INCOMPATIBLE,
    NOT_SUPPLIED,
    PROVENANCE_MISMATCH,
    UNREADABLE,
    VALIDATOR_REJECTED,
    admission_meaning,
    provenance_rule,
)
from modules.standalone_contracts import (
    DUPLICATION_FORMAT,
    HOTSPOT_FORMAT,
    STANDALONE_CONTRACTS,
    compatibility_status,
    resolve_validator,
)
from archlens_json import (
    EVIDENCE_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    loads_bytes,
    read_bounded_bytes,
)
from modules.policy import trust as trust_module

#: Evidence kinds, named for the CLI flag that will later supply each one. The
#: kind is the caller's word for the file; the FORMAT is the document's own
#: identity. The two are kept apart so a file passed under the wrong flag is
#: reported as a contract incompatibility rather than quietly re-classified.
EVIDENCE_DUPLICATION = "duplication"
EVIDENCE_HOTSPOTS = "hotspots"

EVIDENCE_KINDS: tuple[str, ...] = (EVIDENCE_DUPLICATION, EVIDENCE_HOTSPOTS)

EVIDENCE_FORMATS: Mapping[str, str] = MappingProxyType({
    EVIDENCE_DUPLICATION: DUPLICATION_FORMAT,
    EVIDENCE_HOTSPOTS: HOTSPOT_FORMAT,
})

#: **The chosen scope-hash decision.** A gate refuses duplication evidence
#: unless both sides record an ``analysis_scope_hash`` and the two agree.
#:
#: :mod:`modules.dossier` is deliberately more permissive: it matches on the
#: commit SHA alone when either side lacks a hash, and records that it did so.
#: That is safe for a document whose job is to SHOW a number beside a caveat. It
#: is not safe for a gate, whose output is meant to authorize a merge -- "the
#: commit matched, the analyzed file set was never compared" is not a basis for
#: that.
#:
#: The parameter exists because the refusal has one legitimate false positive,
#: documented at :func:`modules.subject.compute_analysis_scope_hash`: a clean
#: Windows worktree at commit X and an exact LF materialization of commit X
#: analyze genuinely different bytes while being the same subject at the same
#: revision. An operator who knows that is their situation can pass
#: ``require_scope_hash=False``, and the record always states which mode ran.
#: **No policy document key sets it yet** -- policy document changes are a later
#: phase -- so today it is True for every caller that does not override it.
REQUIRE_SCOPE_HASH_DEFAULT = True

# --------------------------------------------------------------------------
# Binding: which subject in this run does the document speak for?
# --------------------------------------------------------------------------

#: Binding was not reached, because an earlier gate refused the document.
BINDING_NOT_ATTEMPTED = "not_attempted"
#: Exactly one subject (duplication) or at least one covered subject (hotspots).
BINDING_BOUND = "bound"
#: Provenance held far enough to look, and no subject in this run matched.
BINDING_UNBOUND = "unbound"
#: More than one subject matched and nothing in the document separates them.
BINDING_AMBIGUOUS = "ambiguous"

BINDING_STATES: tuple[str, ...] = (
    BINDING_NOT_ATTEMPTED, BINDING_BOUND, BINDING_UNBOUND, BINDING_AMBIGUOUS,
)

BINDING_MEANINGS: Mapping[str, str] = MappingProxyType({
    BINDING_NOT_ATTEMPTED: (
        "binding was not attempted: either no document was supplied, or an "
        "earlier admission gate refused the one that was"
    ),
    BINDING_BOUND: (
        "the document speaks for the named subject(s) of this run and for no "
        "others"
    ),
    BINDING_UNBOUND: (
        "no subject in this run corresponds to what the document describes"
    ),
    BINDING_AMBIGUOUS: (
        "more than one subject in this run corresponds to what the document "
        "describes, and nothing in the document separates them; the evidence "
        "is refused rather than attributed to an arbitrary one of them"
    ),
})

# --------------------------------------------------------------------------
# Typed refusal reasons. A consumer never has to parse a message to find out.
# --------------------------------------------------------------------------

REASON_NOT_A_DOCUMENT = "document_is_not_a_json_object"
REASON_FORMAT_UNEXPECTED = "document_declares_another_format"
REASON_CONTRACT_STATUS = "standalone_contract_not_compatible"
REASON_ANALYSIS_CONTRACT = "analysis_contract_version_not_readable"
REASON_VALIDATOR_REJECTED = "document_validator_rejected_contents"
REASON_NO_ANALYZED_COMMIT = "document_records_no_analyzed_commit"
REASON_NO_SUBJECT_AT_COMMIT = "no_subject_in_this_run_analyzed_at_that_commit"
REASON_SCOPE_HASH_MISSING = "analysis_scope_hash_missing"
REASON_SCOPE_HASH_MISMATCH = "analysis_scope_hash_mismatch"
REASON_AMBIGUOUS_BINDING = "ambiguous_subject_binding"
REASON_RUN_IDENTITY_UNKNOWN = "run_identity_not_established"
REASON_RUN_MISMATCH = "document_produced_from_another_run"
REASON_NO_COVERED_SUBJECT = "document_covers_no_subject_in_this_run"
REASON_EXTERNAL_ATTESTATION_REQUIRED = "external_attestation_required"
REASON_EVIDENCE_NOT_CANONICAL = "evidence_document_not_canonical"

# Policy 1.4 adds a protected trust gate after the shared structural admission
# vocabulary.  The shared vocabulary is frozen into historical dossier
# contracts, so this policy-only state is intentionally not added there.
TRUST_REFUSED = "trust_refused"
POLICY_ADMISSION_STATES = (*ADMISSION_STATES, TRUST_REFUSED)

REASON_MEANINGS: Mapping[str, str] = MappingProxyType({
    REASON_NOT_A_DOCUMENT: (
        "the supplied JSON is not an object, so it presents no format identity"
    ),
    REASON_FORMAT_UNEXPECTED: (
        "the document declares a different standalone format than the one this "
        "evidence kind admits"
    ),
    REASON_CONTRACT_STATUS: (
        "the declared format identity is not an active exact-version standalone "
        "contract in this build"
    ),
    REASON_ANALYSIS_CONTRACT: (
        "the document declares an analysis contract version this build does not "
        "read, so the meaning of its values cannot be assumed"
    ),
    REASON_VALIDATOR_REJECTED: (
        "the document's own validator rejected its contents"
    ),
    REASON_NO_ANALYZED_COMMIT: (
        "the document records no analyzed commit, so there is nothing to match "
        "against this run"
    ),
    REASON_NO_SUBJECT_AT_COMMIT: (
        "no repository in this run was analyzed at the commit the document "
        "describes"
    ),
    REASON_SCOPE_HASH_MISSING: (
        "an analysis scope hash is required on both sides and one side records "
        "none, so the two analyses cannot be shown to have looked at the same "
        "files"
    ),
    REASON_SCOPE_HASH_MISMATCH: (
        "the analyzed commit matches but the analysis scope hash does not; the "
        "two analyses did not look at the same files"
    ),
    REASON_AMBIGUOUS_BINDING: (
        "more than one subject in this run matches, and the evidence is refused "
        "rather than attributed to an arbitrary one of them"
    ),
    REASON_RUN_IDENTITY_UNKNOWN: (
        "one side records no run id, so identity cannot be established"
    ),
    REASON_RUN_MISMATCH: "the document was produced from a different run",
    REASON_NO_COVERED_SUBJECT: (
        "the document is from this run but names no subject this run measured, "
        "so it speaks for nothing that could be gated on"
    ),
    REASON_EXTERNAL_ATTESTATION_REQUIRED: (
        "protected mode requires an externally authenticated receipt over the "
        "exact canonical evidence bytes and their run/subject/scope bindings"
    ),
    REASON_EVIDENCE_NOT_CANONICAL: (
        "protected evidence must be the exact canonical bytes emitted by the "
        "existing producer"
    ),
})


class EvidenceKindUnknown(ValueError):
    """A caller named an evidence kind this module does not admit."""


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _require_kind(kind: str) -> str:
    if kind not in EVIDENCE_FORMATS:
        raise EvidenceKindUnknown(
            f"{kind!r} is not one of {', '.join(EVIDENCE_KINDS)}"
        )
    return EVIDENCE_FORMATS[kind]


# --------------------------------------------------------------------------
# The run side of the comparison
# --------------------------------------------------------------------------

def analyzed_scopes(
    repositories: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    """Every ``(subject, revision, scope)`` this run measured.

    Deliberately takes repository result mappings rather than an opened run
    view: admission compares recorded values and has no business owning an
    artifact reader, and a caller that already holds a view passes
    ``view.repositories`` directly.

    ``subject_key_of`` reads any Mapping, including the reader's
    ``mappingproxy``, so no defensive copy is made here.

    The result is sorted on the full tuple rather than on the subject key alone,
    so the ordering is total even if one run somehow records two entries under
    one key.
    """
    from modules.subject import subject_key_of

    scopes: list[dict[str, Any]] = []
    for result in repositories:
        acquisition = result.get("acquisition") or {}
        scopes.append({
            "subject_key": _text(subject_key_of(result)),
            "repository_url": _text(result.get("repository_url")),
            "analyzed_commit_sha": _text(acquisition.get("analyzed_commit_sha")),
            "analysis_scope_hash": _text(result.get("analysis_scope_hash")),
        })
    return tuple(sorted(
        scopes,
        key=lambda item: (
            item["subject_key"] or "",
            item["analyzed_commit_sha"] or "",
            item["analysis_scope_hash"] or "",
            item["repository_url"] or "",
        ),
    ))


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _read_evidence_input(
    path: Path | str,
) -> tuple[Mapping[str, Any] | None, str | None, bytes | None, str | None]:
    """Read exact bytes once, hash them, and parse those same bytes."""

    try:
        payload = read_bounded_bytes(
            path, source=str(path), limits=EVIDENCE_JSON_LIMITS
        )
        document = loads_bytes(
            payload,
            source=str(path),
            limits=EVIDENCE_JSON_LIMITS,
            expect=None,
        )
    except StrictJsonError as error:
        legacy_name = {
            "json_malformed": "JSONDecodeError",
            "invalid_utf8": "UnicodeDecodeError",
        }.get(error.code)
        return None, (
            f"{legacy_name}: {error}" if legacy_name is not None else str(error)
        ), None, None
    return document, None, payload, hashlib.sha256(payload).hexdigest()


def read_evidence_document(
    path: Path | str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Read one evidence file. Returns ``(document, read_error)``.

    A read failure is returned rather than raised: it is a reportable admission
    outcome, and the caller must be able to record it beside the outcomes of the
    other evidence kinds instead of losing them all to one exception.

    Bytes are decoded explicitly rather than read through ``read_text``, whose
    universal-newline translation would rewrite a literal CR LF **inside** a JSON
    string. That would silently alter document content on Windows, and this
    repository is deliberately byte-exact.
    """
    document, error, _payload, _digest = _read_evidence_input(path)
    return document, error


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRecord:
    """One admission decision, with everything a reader needs to check it.

    ``document`` and ``admission`` travel together on purpose, and the pairing is
    enforced: a record that is not ``admitted`` carries no document, so a caller
    cannot reach a number without having gone through the admission branch. That
    is the same shape as :class:`modules.policy.metrics.Observation`, and it
    makes "a rejected document contributes nothing" a property of the code
    rather than a rule everybody has to remember.
    """

    kind: str
    expected_format: str
    admission: str
    supplied: bool
    provenance_rule: str | None = None
    document_format: str | None = None
    document_format_version: str | None = None
    contract_compatibility: str | None = None
    expected_analysis_contract_version: str | None = None
    document_analysis_contract_version: str | None = None
    binding: Mapping[str, Any] = field(default_factory=dict)
    provenance_evidence: Mapping[str, Any] | None = None
    reason: str | None = None
    detail: str | None = None
    document_sha256: str | None = None
    trust_state: str = trust_module.UNTRUSTED_LOCAL
    producer_identity: Mapping[str, Any] | None = None
    attestation: Mapping[str, Any] | None = None
    #: Present only when ``admission == ADMITTED``. Never serialized.
    document: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.admission not in POLICY_ADMISSION_STATES:
            raise ValueError(
                f"{self.admission!r} is not one of "
                f"{', '.join(POLICY_ADMISSION_STATES)}"
            )
        if self.admission == ADMITTED and self.document is None:
            raise ValueError(
                "an admitted record must carry the document it admitted"
            )
        if self.admission != ADMITTED and self.document is not None:
            raise ValueError(
                "a record that is not admitted must carry no document; its "
                "figures describe something else and must be unreachable"
            )
        state = (self.binding or {}).get("state")
        if state is not None and state not in BINDING_STATES:
            raise ValueError(f"{state!r} is not a binding state")
        if self.trust_state not in trust_module.EVIDENCE_TRUST_STATES:
            raise ValueError(f"{self.trust_state!r} is not an evidence trust state")

    @property
    def admitted(self) -> bool:
        """Whether this evidence may be read at all."""
        return self.admission == ADMITTED

    @property
    def subject_keys(self) -> tuple[str, ...]:
        """The subjects this evidence speaks for. Empty unless bound."""
        return tuple((self.binding or {}).get("subject_keys") or ())

    def as_dict(self) -> dict[str, Any]:
        """The machine-readable record. Deterministic, and free of figures.

        The admitted document is excluded: this record states whether evidence
        may be used, and copying its contents here would make a rejected
        document's numbers reachable through the very object that rejected it.
        """
        return {
            "kind": self.kind,
            "supplied": self.supplied,
            "admission": self.admission,
            "admission_meaning": (
                "protected evidence authenticity or an exact receipt binding "
                "was refused; the document contributes no values"
                if self.admission == TRUST_REFUSED
                else admission_meaning(self.admission)
            ),
            "expected_format": self.expected_format,
            "document_format": self.document_format,
            "document_format_version": self.document_format_version,
            "contract_compatibility": self.contract_compatibility,
            "expected_analysis_contract_version": (
                self.expected_analysis_contract_version
            ),
            "document_analysis_contract_version": (
                self.document_analysis_contract_version
            ),
            "provenance_rule": self.provenance_rule,
            "provenance_evidence": (
                None if self.provenance_evidence is None
                else dict(self.provenance_evidence)
            ),
            "binding": dict(self.binding or {}),
            "reason": self.reason,
            "reason_meaning": (
                None if self.reason is None
                else REASON_MEANINGS.get(self.reason, self.reason)
            ),
            "detail": self.detail,
            "document_sha256": self.document_sha256,
            "trust_state": self.trust_state,
            "producer_identity": (
                None if self.producer_identity is None
                else dict(self.producer_identity)
            ),
            "attestation": (
                None if self.attestation is None else dict(self.attestation)
            ),
        }


def _binding(
    state: str,
    *,
    subject_keys: Sequence[str] = (),
    scope_hash_compared: bool | None = None,
    require_scope_hash: bool | None = None,
    candidate_subject_keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": state,
        "state_meaning": BINDING_MEANINGS[state],
        "subject_keys": sorted(subject_keys),
        "scope_hash_compared": scope_hash_compared,
        "require_scope_hash": require_scope_hash,
    }
    if candidate_subject_keys is not None:
        payload["candidate_subject_keys"] = sorted(candidate_subject_keys)
    return payload


@dataclass(frozen=True)
class _Provenance:
    """The provenance-and-binding outcome, before it becomes a record."""

    matched: bool
    binding: dict[str, Any]
    evidence: dict[str, Any]
    reason: str | None = None


def _mismatch(
    evidence: Mapping[str, Any], reason: str, *, binding: dict[str, Any]
) -> _Provenance:
    return _Provenance(
        matched=False, binding=binding, evidence=dict(evidence), reason=reason
    )


# --------------------------------------------------------------------------
# Duplication: revision plus analyzed scope, against the run's scopes
# --------------------------------------------------------------------------

def _bind_duplication(
    document: Mapping[str, Any],
    scopes: Sequence[Mapping[str, Any]],
    *,
    require_scope_hash: bool,
) -> _Provenance:
    """Match one duplication document to exactly one subject, or refuse.

    A duplication document is produced from a local snapshot and carries no
    subject identity of any kind -- only ``resolved_revision`` and
    ``analysis_scope_hash``. Binding is therefore inferential, and every step
    that narrows the candidate set is recorded so a reader can check it.
    """
    source = document.get("source") or {}
    commit = _text(source.get("resolved_revision"))
    scope_hash = _text(source.get("analysis_scope_hash"))
    evidence: dict[str, Any] = {
        "document_analyzed_commit_sha": commit,
        "document_analysis_scope_hash": scope_hash,
        "document_source_mode": _text(source.get("mode")),
        "document_working_tree_state": _text(source.get("working_tree_state")),
        "document_tracked_only": source.get("tracked_only"),
        "require_scope_hash": require_scope_hash,
        "run_analyzed_scopes": [dict(scope) for scope in scopes],
    }
    unbound = _binding(BINDING_UNBOUND, require_scope_hash=require_scope_hash)

    if commit is None:
        return _mismatch(evidence, REASON_NO_ANALYZED_COMMIT, binding=unbound)

    at_commit = [
        dict(scope) for scope in scopes
        if scope.get("analyzed_commit_sha") == commit
        and scope.get("subject_key") is not None
    ]
    if not at_commit:
        return _mismatch(evidence, REASON_NO_SUBJECT_AT_COMMIT, binding=unbound)
    evidence["subjects_at_commit"] = sorted(
        str(scope["subject_key"]) for scope in at_commit
    )

    if require_scope_hash:
        if scope_hash is None:
            return _mismatch(
                {**evidence, "missing_scope_hash_side": "document"},
                REASON_SCOPE_HASH_MISSING,
                binding=unbound,
            )
        with_hash = [
            scope for scope in at_commit
            if scope.get("analysis_scope_hash") is not None
        ]
        if not with_hash:
            return _mismatch(
                {**evidence, "missing_scope_hash_side": "run"},
                REASON_SCOPE_HASH_MISSING,
                binding=unbound,
            )
        matched = [
            scope for scope in with_hash
            if scope["analysis_scope_hash"] == scope_hash
        ]
        if not matched:
            return _mismatch(
                evidence, REASON_SCOPE_HASH_MISMATCH, binding=unbound
            )
    else:
        matched = []
        for scope in at_commit:
            run_hash = scope.get("analysis_scope_hash")
            if scope_hash is not None and run_hash is not None:
                if run_hash == scope_hash:
                    matched.append(scope)
                continue
            # One side records no hash. Under a relaxed policy the commit alone
            # is accepted, and the record says the hash was never compared.
            matched.append(scope)
        if not matched:
            return _mismatch(
                evidence, REASON_SCOPE_HASH_MISMATCH, binding=unbound
            )

    # Ambiguity is about SUBJECTS, not about rows. A run that records one
    # subject twice at the same revision and scope still names one subject, and
    # refusing that would be a false alarm; two different subjects that the
    # document cannot tell apart is the real ambiguity.
    subject_keys = sorted({str(scope["subject_key"]) for scope in matched})
    if len(subject_keys) > 1:
        return _mismatch(
            evidence,
            REASON_AMBIGUOUS_BINDING,
            binding=_binding(
                BINDING_AMBIGUOUS,
                require_scope_hash=require_scope_hash,
                candidate_subject_keys=subject_keys,
            ),
        )

    compared = scope_hash is not None and all(
        scope.get("analysis_scope_hash") is not None for scope in matched
    )
    return _Provenance(
        matched=True,
        binding=_binding(
            BINDING_BOUND,
            subject_keys=subject_keys,
            scope_hash_compared=compared,
            require_scope_hash=require_scope_hash,
        ),
        evidence={**evidence, "matched_subject_key": subject_keys[0]},
    )


# --------------------------------------------------------------------------
# Hotspots: exact run identity, then the subjects the document names
# --------------------------------------------------------------------------

def _declared_hotspot_subjects(document: Mapping[str, Any]) -> list[str]:
    """Every subject key the document names, from both places it names one.

    ``repositories[]`` is the per-subject block and ``hotspots[]`` rows carry the
    key too. The union is used because a document that names a subject in either
    place speaks for it, and reading only one of the two would make binding
    depend on which section happened to be populated.
    """
    declared: set[str] = set()
    for section in ("repositories", "hotspots"):
        for item in document.get(section) or ():
            if not isinstance(item, Mapping):
                continue
            key = item.get("subject_key")
            if key:
                declared.add(str(key))
    return sorted(declared)


def _bind_hotspots(
    document: Mapping[str, Any],
    scopes: Sequence[Mapping[str, Any]],
    *,
    run_id: str | None,
) -> _Provenance:
    """Match one hotspot document to this run, then to the subjects it names.

    A hotspot document is DERIVED FROM a run view, so its binding is exact
    rather than inferential: the run id either is this run's or it is not. The
    scope-hash policy has nothing to do here, and the record says so with
    ``scope_hash_compared: null`` rather than with a misleading ``false``.
    """
    document_run_id = _text((document.get("source_run") or {}).get("run_id"))
    evidence: dict[str, Any] = {
        "document_run_id": document_run_id,
        "run_id": run_id,
    }
    unbound = _binding(BINDING_UNBOUND)

    if document_run_id is None or run_id is None:
        return _mismatch(evidence, REASON_RUN_IDENTITY_UNKNOWN, binding=unbound)
    if document_run_id != run_id:
        return _mismatch(evidence, REASON_RUN_MISMATCH, binding=unbound)

    known = {
        str(scope["subject_key"]) for scope in scopes
        if scope.get("subject_key") is not None
    }
    declared = _declared_hotspot_subjects(document)
    covered = sorted(key for key in declared if key in known)
    evidence["document_subject_keys"] = declared
    evidence["run_subject_keys"] = sorted(known)
    evidence["covered_subject_keys"] = covered

    if not covered:
        return _mismatch(evidence, REASON_NO_COVERED_SUBJECT, binding=unbound)

    return _Provenance(
        matched=True,
        binding=_binding(
            BINDING_BOUND, subject_keys=covered, scope_hash_compared=None
        ),
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------

def not_supplied(kind: str) -> EvidenceRecord:
    """The record for an evidence kind nobody supplied.

    An absence of INPUT. A caller must never read it as a measured absence of
    findings, and the record carries no document to make that reading possible.
    """
    expected_format = _require_kind(kind)
    contract = STANDALONE_CONTRACTS[expected_format]
    return EvidenceRecord(
        kind=kind,
        expected_format=expected_format,
        admission=NOT_SUPPLIED,
        supplied=False,
        provenance_rule=provenance_rule(expected_format),
        expected_analysis_contract_version=contract.analysis_contract_version,
        binding=_binding(BINDING_NOT_ATTEMPTED),
        trust_state=trust_module.ABSENT,
    )


def admit_document(
    kind: str,
    document: Any,
    *,
    run_id: str | None,
    scopes: Sequence[Mapping[str, Any]],
    read_error: str | None = None,
    require_scope_hash: bool = REQUIRE_SCOPE_HASH_DEFAULT,
    document_sha256: str | None = None,
) -> EvidenceRecord:
    """Run both admission gates over one supplied document.

    ``scopes`` is what :func:`analyzed_scopes` returned for this run, and
    ``run_id`` is the run's own id. Neither is re-derived here.
    """
    expected_format = _require_kind(kind)
    contract = STANDALONE_CONTRACTS[expected_format]
    base: dict[str, Any] = {
        "kind": kind,
        "expected_format": expected_format,
        "supplied": True,
        "provenance_rule": provenance_rule(expected_format),
        "expected_analysis_contract_version": contract.analysis_contract_version,
        "binding": _binding(BINDING_NOT_ATTEMPTED),
        "document_sha256": document_sha256,
        "trust_state": trust_module.UNTRUSTED_LOCAL,
    }

    if read_error is not None:
        return EvidenceRecord(**base, admission=UNREADABLE, detail=read_error)
    if document is None:
        return not_supplied(kind)

    if not isinstance(document, Mapping):
        return EvidenceRecord(
            **base,
            admission=CONTRACT_INCOMPATIBLE,
            reason=REASON_NOT_A_DOCUMENT,
            detail=(
                f"a standalone document must be a JSON object; got "
                f"{type(document).__name__}"
            ),
        )

    format_name = _text(document.get("format"))
    format_version = _text(document.get("format_version"))
    base["document_format"] = format_name
    base["document_format_version"] = format_version

    if format_name != expected_format:
        return EvidenceRecord(
            **base,
            admission=CONTRACT_INCOMPATIBLE,
            reason=REASON_FORMAT_UNEXPECTED,
            detail=(
                f"expected format {expected_format!r}, document declares "
                f"{format_name!r}"
            ),
        )

    status = compatibility_status(format_name, format_version)
    base["contract_compatibility"] = status
    if status != "compatible":
        return EvidenceRecord(
            **base,
            admission=CONTRACT_INCOMPATIBLE,
            reason=REASON_CONTRACT_STATUS,
            detail=f"standalone contract status: {status}",
        )

    # The analysis contract is checked BEFORE the validator, even though the
    # producer's validator would also refuse a wrong one. Checking it here gives
    # the honest classification: an unreadable contract version is an identity
    # problem, not malformed contents, and the two send an operator to different
    # places.
    if contract.analysis_contract_field is not None:
        declared = _text(document.get(contract.analysis_contract_field))
        base["document_analysis_contract_version"] = declared
        if declared != contract.analysis_contract_version:
            return EvidenceRecord(
                **base,
                admission=CONTRACT_INCOMPATIBLE,
                reason=REASON_ANALYSIS_CONTRACT,
                detail=(
                    f"{contract.analysis_contract_field} must be "
                    f"{contract.analysis_contract_version!r}; document declares "
                    f"{declared!r}"
                ),
            )

    try:
        resolve_validator(format_name, format_version)(document)
    except Exception as error:  # noqa: BLE001 - reported, never raised onward
        return EvidenceRecord(
            **base,
            admission=VALIDATOR_REJECTED,
            reason=REASON_VALIDATOR_REJECTED,
            detail=f"{type(error).__name__}: {error}",
        )

    if kind == EVIDENCE_DUPLICATION:
        outcome = _bind_duplication(
            document, scopes, require_scope_hash=require_scope_hash
        )
    else:
        outcome = _bind_hotspots(document, scopes, run_id=run_id)

    base["binding"] = outcome.binding
    if not outcome.matched:
        return EvidenceRecord(
            **base,
            admission=PROVENANCE_MISMATCH,
            provenance_evidence=outcome.evidence,
            reason=outcome.reason,
            detail=REASON_MEANINGS.get(outcome.reason or "", outcome.reason),
        )

    return EvidenceRecord(
        **base,
        admission=ADMITTED,
        provenance_evidence=outcome.evidence,
        document=document,
    )


def admit_evidence_file(
    kind: str,
    path: Path | str | None,
    *,
    run_id: str | None,
    scopes: Sequence[Mapping[str, Any]],
    require_scope_hash: bool = REQUIRE_SCOPE_HASH_DEFAULT,
    trust: trust_module.CheckTrustContext | None = None,
    run_manifest_sha256: str | None = None,
) -> EvidenceRecord:
    """Read and admit one evidence file. ``None`` means nobody supplied one."""
    if path is None:
        return not_supplied(kind)
    _require_kind(kind)
    document, read_error, payload, document_sha256 = _read_evidence_input(path)
    record = admit_document(
        kind,
        document,
        run_id=run_id,
        scopes=scopes,
        read_error=read_error,
        require_scope_hash=require_scope_hash,
        document_sha256=document_sha256,
    )
    if trust is None or not trust.protected:
        return record
    if not record.admitted or document is None or payload is None:
        return replace(record, trust_state=trust_module.REFUSED)

    canonical = dumps_strict(
        document,
        source=f"canonical {kind} evidence",
        limits=EVIDENCE_JSON_LIMITS,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        trailing_newline=True,
    ).encode("utf-8")
    if canonical != payload:
        return replace(
            record,
            admission=TRUST_REFUSED,
            trust_state=trust_module.REFUSED,
            reason=REASON_EVIDENCE_NOT_CANONICAL,
            detail=REASON_MEANINGS[REASON_EVIDENCE_NOT_CANONICAL],
            document=None,
        )

    analysis_contract = record.document_analysis_contract_version
    if analysis_contract is None:
        # Formats with no separate analysis-contract field bind their active
        # standalone format version as the analysis contract identity.
        analysis_contract = str(record.document_format_version or "")
    trusted, reason, attestation = trust.validate_evidence(
        kind=kind,
        document_sha256=str(document_sha256 or ""),
        run_manifest_sha256=str(run_manifest_sha256 or ""),
        run_id=str(run_id or ""),
        document_format=str(record.document_format or ""),
        document_format_version=str(record.document_format_version or ""),
        analysis_contract_version=analysis_contract,
        subject_keys=record.subject_keys,
        scopes=scopes,
        document=document,
    )
    if not trusted:
        typed_reason = reason or REASON_EXTERNAL_ATTESTATION_REQUIRED
        return replace(
            record,
            admission=TRUST_REFUSED,
            trust_state=trust_module.REFUSED,
            reason=typed_reason,
            detail=REASON_MEANINGS.get(typed_reason, typed_reason),
            document=None,
            attestation=attestation,
        )
    return replace(
        record,
        trust_state=trust_module.TRUSTED,
        producer_identity=trust.evaluator.receipt_dict(),
        attestation=attestation,
    )


__all__ = [
    "BINDING_AMBIGUOUS",
    "BINDING_BOUND",
    "BINDING_MEANINGS",
    "BINDING_NOT_ATTEMPTED",
    "BINDING_STATES",
    "BINDING_UNBOUND",
    "EVIDENCE_DUPLICATION",
    "EVIDENCE_FORMATS",
    "EVIDENCE_HOTSPOTS",
    "EVIDENCE_KINDS",
    "REASON_AMBIGUOUS_BINDING",
    "REASON_ANALYSIS_CONTRACT",
    "REASON_CONTRACT_STATUS",
    "REASON_FORMAT_UNEXPECTED",
    "REASON_MEANINGS",
    "REASON_NOT_A_DOCUMENT",
    "REASON_NO_ANALYZED_COMMIT",
    "REASON_NO_COVERED_SUBJECT",
    "REASON_NO_SUBJECT_AT_COMMIT",
    "REASON_RUN_IDENTITY_UNKNOWN",
    "REASON_RUN_MISMATCH",
    "REASON_SCOPE_HASH_MISMATCH",
    "REASON_SCOPE_HASH_MISSING",
    "REASON_VALIDATOR_REJECTED",
    "REQUIRE_SCOPE_HASH_DEFAULT",
    "EvidenceKindUnknown",
    "EvidenceRecord",
    "POLICY_ADMISSION_STATES",
    "TRUST_REFUSED",
    "admit_document",
    "admit_evidence_file",
    "analyzed_scopes",
    "not_supplied",
    "read_evidence_document",
]
