"""Protected-gate trust anchors and exact evaluated-input provenance.

This module contains no Policy, Hotspot, Duplication, Ratchet, or SARIF
semantics.  It authenticates byte identities supplied by a protected caller and
checks a small, canonical evidence receipt.  The evaluator remains the only
component that interprets the authenticated documents.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from archlens_json import (
    EVIDENCE_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    loads_bytes,
    read_bounded_bytes,
)


LOCAL_UNPROTECTED = "local_unprotected"
PROTECTED_REQUIRED = "protected_required"
GATE_MODES = (LOCAL_UNPROTECTED, PROTECTED_REQUIRED)

RECEIPT_FORMAT = "archlens-trusted-evidence-receipt"
RECEIPT_FORMAT_VERSION = "1.0.0"

TRUSTED = "trusted"
UNTRUSTED_LOCAL = "untrusted_local"
ABSENT = "absent"
REFUSED = "refused"
EVIDENCE_TRUST_STATES = (TRUSTED, UNTRUSTED_LOCAL, ABSENT, REFUSED)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")


class TrustAdmissionError(ValueError):
    """A protected caller failed one typed trust-admission gate."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TrustAdmissionError(f"{label}_invalid")
    return value


def verify_sha256(payload: bytes, expected: Any, label: str) -> str:
    trusted = require_sha256(expected, label)
    actual = sha256_bytes(payload)
    if not hmac.compare_digest(actual, trusted):
        raise TrustAdmissionError(
            f"{label}_mismatch", f"expected={trusted}, actual={actual}"
        )
    return actual


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrustAdmissionError("receipt_contract_invalid", f"{label} is not an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown or missing:
        detail = []
        if unknown:
            detail.append(f"unknown={','.join(unknown)}")
        if missing:
            detail.append(f"missing={','.join(missing)}")
        raise TrustAdmissionError(
            "receipt_contract_invalid", f"{label}: {'; '.join(detail)}"
        )


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise TrustAdmissionError("receipt_contract_invalid", f"{label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class EvaluatorIdentity:
    """Identity of the evaluator/producer boundary selected by its caller."""

    kind: str
    repository: str | None
    revision: str | None
    source_sha256: str | None
    installed_version: str

    @classmethod
    def local(cls, installed_version: str) -> "EvaluatorIdentity":
        return cls(
            kind="local_checkout",
            repository=None,
            revision=None,
            source_sha256=None,
            installed_version=installed_version,
        )

    @classmethod
    def protected(
        cls,
        *,
        repository: str,
        revision: str,
        source_sha256: str,
        installed_version: str,
    ) -> "EvaluatorIdentity":
        if _REPOSITORY.fullmatch(repository) is None:
            raise TrustAdmissionError("evaluator_repository_invalid")
        if _COMMIT_SHA.fullmatch(revision) is None:
            raise TrustAdmissionError("evaluator_revision_not_full_sha")
        require_sha256(source_sha256, "evaluator_source_sha256")
        return cls(
            kind="github_action_commit",
            repository=repository,
            revision=revision,
            source_sha256=source_sha256,
            installed_version=installed_version,
        )

    def receipt_dict(self) -> dict[str, str]:
        if self.kind != "github_action_commit":
            raise TrustAdmissionError("protected_evaluator_required")
        assert self.repository is not None
        assert self.revision is not None
        assert self.source_sha256 is not None
        return {
            "repository": self.repository,
            "revision": self.revision,
            "source_sha256": self.source_sha256,
        }

    def as_dict(self, *, protected: bool) -> dict[str, Any]:
        return {
            "identity_kind": self.kind,
            "repository": self.repository,
            "revision": self.revision,
            "source_sha256": self.source_sha256,
            "installed_version": self.installed_version,
            "trust": "externally_authenticated" if protected else "locally_trusted_only",
        }


@dataclass(frozen=True, slots=True)
class SubjectBinding:
    subject_key: str
    analyzed_revision: str
    analysis_scope_hash: str

    @classmethod
    def parse(cls, payload: Any, where: str) -> "SubjectBinding":
        item = _mapping(payload, where)
        _exact_keys(
            item,
            {"subject_key", "analyzed_revision", "analysis_scope_hash"},
            where,
        )
        return cls(
            subject_key=_text(item["subject_key"], f"{where}.subject_key"),
            analyzed_revision=_text(
                item["analyzed_revision"], f"{where}.analyzed_revision"
            ),
            analysis_scope_hash=_text(
                item["analysis_scope_hash"], f"{where}.analysis_scope_hash"
            ),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "subject_key": self.subject_key,
            "analyzed_revision": self.analyzed_revision,
            "analysis_scope_hash": self.analysis_scope_hash,
        }


@dataclass(frozen=True, slots=True)
class EvidenceAttestation:
    kind: str
    document_sha256: str
    producer: Mapping[str, str]
    run_manifest_sha256: str
    run_id: str
    document_format: str
    document_format_version: str
    analysis_contract_version: str
    subjects: tuple[SubjectBinding, ...]

    @classmethod
    def parse(cls, payload: Any, index: int) -> "EvidenceAttestation":
        where = f"evidence[{index}]"
        item = _mapping(payload, where)
        _exact_keys(
            item,
            {
                "kind",
                "document_sha256",
                "producer",
                "run_manifest_sha256",
                "run_id",
                "document_format",
                "document_format_version",
                "analysis_contract_version",
                "subjects",
            },
            where,
        )
        producer = _mapping(item["producer"], f"{where}.producer")
        _exact_keys(
            producer,
            {"repository", "revision", "source_sha256"},
            f"{where}.producer",
        )
        parsed_producer = {
            "repository": _text(producer["repository"], f"{where}.producer.repository"),
            "revision": _text(producer["revision"], f"{where}.producer.revision"),
            "source_sha256": require_sha256(
                producer["source_sha256"], "producer_source_sha256"
            ),
        }
        if _REPOSITORY.fullmatch(parsed_producer["repository"]) is None:
            raise TrustAdmissionError("receipt_contract_invalid", "producer repository")
        if _COMMIT_SHA.fullmatch(parsed_producer["revision"]) is None:
            raise TrustAdmissionError("receipt_contract_invalid", "producer revision")
        raw_subjects = item["subjects"]
        if not isinstance(raw_subjects, list) or not raw_subjects:
            raise TrustAdmissionError("receipt_contract_invalid", f"{where}.subjects")
        subjects = tuple(
            SubjectBinding.parse(subject, f"{where}.subjects[{position}]")
            for position, subject in enumerate(raw_subjects)
        )
        keys = [subject.subject_key for subject in subjects]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise TrustAdmissionError(
                "receipt_contract_invalid", f"{where}.subjects order/identity"
            )
        return cls(
            kind=_text(item["kind"], f"{where}.kind"),
            document_sha256=require_sha256(
                item["document_sha256"], "evidence_document_sha256"
            ),
            producer=parsed_producer,
            run_manifest_sha256=require_sha256(
                item["run_manifest_sha256"], "evidence_run_manifest_sha256"
            ),
            run_id=_text(item["run_id"], f"{where}.run_id"),
            document_format=_text(
                item["document_format"], f"{where}.document_format"
            ),
            document_format_version=_text(
                item["document_format_version"], f"{where}.document_format_version"
            ),
            analysis_contract_version=_text(
                item["analysis_contract_version"],
                f"{where}.analysis_contract_version",
            ),
            subjects=subjects,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "document_sha256": self.document_sha256,
            "producer": dict(self.producer),
            "run_manifest_sha256": self.run_manifest_sha256,
            "run_id": self.run_id,
            "document_format": self.document_format,
            "document_format_version": self.document_format_version,
            "analysis_contract_version": self.analysis_contract_version,
            "subjects": [subject.as_dict() for subject in self.subjects],
        }


@dataclass(frozen=True, slots=True)
class TrustedEvidenceReceipt:
    evaluator: Mapping[str, str]
    run_manifest_sha256: str
    evidence: tuple[EvidenceAttestation, ...]
    verified_sha256: str

    @property
    def by_kind(self) -> dict[str, EvidenceAttestation]:
        return {item.kind: item for item in self.evidence}

    def as_document(self) -> dict[str, Any]:
        return {
            "receipt_format": RECEIPT_FORMAT,
            "receipt_format_version": RECEIPT_FORMAT_VERSION,
            "evaluator": dict(self.evaluator),
            "run_manifest_sha256": self.run_manifest_sha256,
            "evidence": [item.as_dict() for item in self.evidence],
        }


def canonical_receipt_bytes(document: Mapping[str, Any]) -> bytes:
    return dumps_strict(
        document,
        source="trusted evidence receipt",
        limits=EVIDENCE_JSON_LIMITS,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        trailing_newline=True,
    ).encode("utf-8")


def parse_receipt_bytes(payload: bytes, *, verified_sha256: str) -> TrustedEvidenceReceipt:
    try:
        document = loads_bytes(
            payload,
            source="trusted evidence receipt",
            limits=EVIDENCE_JSON_LIMITS,
            expect=dict,
        )
    except StrictJsonError as exc:
        raise TrustAdmissionError("receipt_contract_invalid", exc.code) from exc
    _exact_keys(
        document,
        {
            "receipt_format",
            "receipt_format_version",
            "evaluator",
            "run_manifest_sha256",
            "evidence",
        },
        "receipt",
    )
    if document["receipt_format"] != RECEIPT_FORMAT:
        raise TrustAdmissionError("receipt_format_invalid")
    if document["receipt_format_version"] != RECEIPT_FORMAT_VERSION:
        raise TrustAdmissionError("receipt_version_invalid")
    evaluator = _mapping(document["evaluator"], "evaluator")
    _exact_keys(evaluator, {"repository", "revision", "source_sha256"}, "evaluator")
    parsed_evaluator = {
        "repository": _text(evaluator["repository"], "evaluator.repository"),
        "revision": _text(evaluator["revision"], "evaluator.revision"),
        "source_sha256": require_sha256(
            evaluator["source_sha256"], "evaluator_source_sha256"
        ),
    }
    if _REPOSITORY.fullmatch(parsed_evaluator["repository"]) is None:
        raise TrustAdmissionError("evaluator_repository_invalid")
    if _COMMIT_SHA.fullmatch(parsed_evaluator["revision"]) is None:
        raise TrustAdmissionError("evaluator_revision_not_full_sha")
    raw_evidence = document["evidence"]
    if not isinstance(raw_evidence, list):
        raise TrustAdmissionError("receipt_contract_invalid", "evidence is not an array")
    evidence = tuple(
        EvidenceAttestation.parse(item, index)
        for index, item in enumerate(raw_evidence)
    )
    kinds = [item.kind for item in evidence]
    if kinds != sorted(kinds) or len(kinds) != len(set(kinds)):
        raise TrustAdmissionError("receipt_contract_invalid", "evidence order/identity")
    if canonical_receipt_bytes(document) != payload:
        raise TrustAdmissionError("receipt_not_canonical")
    return TrustedEvidenceReceipt(
        evaluator=parsed_evaluator,
        run_manifest_sha256=require_sha256(
            document["run_manifest_sha256"], "receipt_run_manifest_sha256"
        ),
        evidence=evidence,
        verified_sha256=verified_sha256,
    )


def load_receipt_file(path: Path, expected_sha256: str) -> TrustedEvidenceReceipt:
    try:
        payload = read_bounded_bytes(
            path, source="trusted evidence receipt", limits=EVIDENCE_JSON_LIMITS
        )
    except StrictJsonError as exc:
        raise TrustAdmissionError("receipt_unreadable", exc.code) from exc
    verified = verify_sha256(payload, expected_sha256, "receipt_sha256")
    return parse_receipt_bytes(payload, verified_sha256=verified)


@dataclass(frozen=True, slots=True)
class CheckTrustContext:
    mode: str
    evaluator: EvaluatorIdentity
    policy_sha256: str
    expected_policy_sha256: str | None
    receipt: TrustedEvidenceReceipt | None = None

    @property
    def protected(self) -> bool:
        return self.mode == PROTECTED_REQUIRED

    @classmethod
    def local(cls, *, evaluator_version: str, policy_sha256: str) -> "CheckTrustContext":
        require_sha256(policy_sha256, "policy_sha256")
        return cls(
            mode=LOCAL_UNPROTECTED,
            evaluator=EvaluatorIdentity.local(evaluator_version),
            policy_sha256=policy_sha256,
            expected_policy_sha256=None,
        )

    @classmethod
    def protected_required(
        cls,
        *,
        evaluator: EvaluatorIdentity,
        policy_payload: bytes,
        expected_policy_sha256: str | None,
        receipt: TrustedEvidenceReceipt | None = None,
    ) -> "CheckTrustContext":
        if evaluator.kind != "github_action_commit":
            raise TrustAdmissionError("protected_evaluator_required")
        if expected_policy_sha256 is None:
            raise TrustAdmissionError("policy_sha256_required")
        actual = verify_sha256(
            policy_payload, expected_policy_sha256, "policy_sha256"
        )
        if receipt is not None and dict(receipt.evaluator) != evaluator.receipt_dict():
            raise TrustAdmissionError("receipt_evaluator_mismatch")
        return cls(
            mode=PROTECTED_REQUIRED,
            evaluator=evaluator,
            policy_sha256=actual,
            expected_policy_sha256=expected_policy_sha256,
            receipt=receipt,
        )

    def validate_run_binding(self, run_manifest_sha256: str) -> None:
        require_sha256(run_manifest_sha256, "run_manifest_sha256")
        if self.receipt is not None and not hmac.compare_digest(
            self.receipt.run_manifest_sha256, run_manifest_sha256
        ):
            raise TrustAdmissionError("receipt_run_manifest_mismatch")

    def evidence_attestation(self, kind: str) -> EvidenceAttestation | None:
        if self.receipt is None:
            return None
        return self.receipt.by_kind.get(kind)

    def validate_evidence(
        self,
        *,
        kind: str,
        document_sha256: str,
        run_manifest_sha256: str,
        run_id: str,
        document_format: str,
        document_format_version: str,
        analysis_contract_version: str,
        subject_keys: Sequence[str],
        scopes: Sequence[Mapping[str, Any]],
        document: Mapping[str, Any],
    ) -> tuple[bool, str | None, Mapping[str, Any] | None]:
        if not self.protected:
            return True, None, None
        entry = self.evidence_attestation(kind)
        if entry is None:
            return False, "external_attestation_required", None
        comparisons = (
            (entry.document_sha256, document_sha256, "document_digest_mismatch"),
            (entry.run_manifest_sha256, run_manifest_sha256, "run_manifest_mismatch"),
            (entry.run_id, run_id, "run_id_mismatch"),
            (entry.document_format, document_format, "document_format_mismatch"),
            (
                entry.document_format_version,
                document_format_version,
                "document_format_version_mismatch",
            ),
            (
                entry.analysis_contract_version,
                analysis_contract_version,
                "analysis_contract_version_mismatch",
            ),
        )
        for expected, actual, reason in comparisons:
            if not hmac.compare_digest(str(expected), str(actual)):
                return False, reason, entry.as_dict()
        if dict(entry.producer) != self.evaluator.receipt_dict():
            return False, "producer_identity_mismatch", entry.as_dict()
        scope_by_key = {
            str(item.get("subject_key")): (
                item.get("analyzed_commit_sha"), item.get("analysis_scope_hash")
            )
            for item in scopes
        }
        expected_subjects = [subject.subject_key for subject in entry.subjects]
        if expected_subjects != sorted(subject_keys):
            return False, "subject_identity_mismatch", entry.as_dict()
        for subject in entry.subjects:
            actual = scope_by_key.get(subject.subject_key)
            if actual is None:
                return False, "subject_identity_mismatch", entry.as_dict()
            if actual[0] != subject.analyzed_revision:
                return False, "analyzed_revision_mismatch", entry.as_dict()
            if actual[1] != subject.analysis_scope_hash:
                return False, "analysis_scope_mismatch", entry.as_dict()
        if kind == "hotspots":
            declared = {
                str(item.get("subject_key")): item.get("analyzed_commit_sha")
                for item in document.get("repositories") or ()
                if isinstance(item, Mapping)
            }
            for subject in entry.subjects:
                if declared.get(subject.subject_key) != subject.analyzed_revision:
                    return False, "document_revision_mismatch", entry.as_dict()
        elif kind == "duplication" and len(entry.subjects) == 1:
            source = document.get("source")
            source = source if isinstance(source, Mapping) else {}
            subject = entry.subjects[0]
            if source.get("resolved_revision") != subject.analyzed_revision:
                return False, "document_revision_mismatch", entry.as_dict()
            if source.get("analysis_scope_hash") != subject.analysis_scope_hash:
                return False, "document_scope_mismatch", entry.as_dict()
        return True, None, entry.as_dict()

    def receipt_provenance(self) -> dict[str, Any]:
        if self.receipt is None:
            return {"sha256": None, "format": None, "format_version": None}
        return {
            "sha256": self.receipt.verified_sha256,
            "format": RECEIPT_FORMAT,
            "format_version": RECEIPT_FORMAT_VERSION,
        }


def rule_condition_identity(rules: Sequence[Mapping[str, Any]]) -> str:
    payload = dumps_strict(
        list(rules),
        source="evaluated rule conditions",
        limits=EVIDENCE_JSON_LIMITS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


__all__ = [
    "ABSENT",
    "CheckTrustContext",
    "EVIDENCE_TRUST_STATES",
    "EvaluatorIdentity",
    "EvidenceAttestation",
    "GATE_MODES",
    "LOCAL_UNPROTECTED",
    "PROTECTED_REQUIRED",
    "RECEIPT_FORMAT",
    "RECEIPT_FORMAT_VERSION",
    "REFUSED",
    "TRUSTED",
    "TrustAdmissionError",
    "TrustedEvidenceReceipt",
    "UNTRUSTED_LOCAL",
    "canonical_receipt_bytes",
    "load_receipt_file",
    "parse_receipt_bytes",
    "require_sha256",
    "rule_condition_identity",
    "sha256_bytes",
    "verify_sha256",
]
