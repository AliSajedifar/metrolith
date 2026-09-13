"""Pure exact subject/language pairing for Ratchet Baseline V1.

Pairing is deliberately a map join over portable subject keys. It does not read
paths, inspect URLs, infer renames, use array positions, or consult Git. Current
subjects are already-admitted observations supplied by a later integration
layer; this module validates the identity and compatibility facts it needs.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from modules.ratchet.contract import (
    MetricContractBinding,
    PortableSubjectBinding,
    RatchetBaseline,
    RatchetScope,
)
from modules.vocabularies import SubjectKeyBasis


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SCOPE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class SubjectPairingError(ValueError):
    """Baseline/current subjects cannot be paired exactly."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class ContractCompatibilityError(ValueError):
    """A paired current subject cannot interpret the baseline measurement."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CurrentSubject:
    """One admitted current subject projected for ratchet pairing.

    Validation intentionally occurs in :func:`pair_subjects`, not here. Keeping
    this a passive boundary value lets pairing reject missing, duplicate, and
    local-fallback identities itself instead of relying on a caller to do so.
    ``languages`` is the applicable ratchet-language population, not an
    invitation to compare unrelated languages.
    """

    subject_key: str | None
    subject_key_basis: SubjectKeyBasis | str | None
    analyzed_commit_sha: str | None
    artifact_schema_version: str | None
    metric_contracts: tuple[MetricContractBinding, ...]
    analysis_scope_hash: str | None
    analysis_scope_hash_version: str | None
    languages: tuple[str, ...] = ()
    admitted: bool = True


@dataclass(frozen=True, slots=True)
class SubjectPair:
    baseline: PortableSubjectBinding
    current: CurrentSubject
    languages: tuple[str, ...]

    @property
    def subject_key(self) -> str:
        return self.baseline.subject_key


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SubjectPairingError("missing_identity", field)
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise SubjectPairingError("invalid_identity", field)
    if not unicodedata.is_normalized("NFC", value):
        raise SubjectPairingError("invalid_identity", f"{field} is not NFC")
    return value


def _portable_basis(value: Any, field: str) -> SubjectKeyBasis:
    try:
        basis = SubjectKeyBasis(value)
    except (TypeError, ValueError) as exc:
        raise SubjectPairingError("missing_identity", field) from exc
    if not basis.is_portable:
        raise SubjectPairingError("local_fallback_identity", field)
    return basis


def _canonical_languages(values: Any, *, subject_key: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise SubjectPairingError("invalid_language_identity", subject_key)
    try:
        languages = tuple(values)
    except TypeError as exc:
        raise SubjectPairingError("invalid_language_identity", subject_key) from exc

    normalized: list[str] = []
    for language in languages:
        if not isinstance(language, str) or not language or language.strip() != language:
            raise SubjectPairingError("invalid_language_identity", subject_key)
        canonical = unicodedata.normalize("NFC", language).casefold()
        normalized.append(canonical)
    if len(normalized) != len(set(normalized)):
        raise SubjectPairingError("duplicate_language_identity", subject_key)
    for original, canonical in zip(languages, normalized, strict=True):
        if original != canonical:
            raise SubjectPairingError(
                "noncanonical_language_identity", f"{subject_key}: {original!r}"
            )
    return tuple(sorted(normalized))


def _validate_current(subject: CurrentSubject, index: int) -> CurrentSubject:
    if not isinstance(subject, CurrentSubject):
        raise SubjectPairingError("invalid_current_subject", str(index))
    if subject.admitted is not True:
        raise SubjectPairingError("current_subject_not_admitted", str(index))
    key = _required_text(subject.subject_key, f"current[{index}].subject_key")
    if PurePosixPath(key).is_absolute() or PureWindowsPath(key).is_absolute():
        raise SubjectPairingError("local_absolute_identity", key)
    basis = _portable_basis(
        subject.subject_key_basis, f"current[{index}].subject_key_basis"
    )
    commit = _required_text(
        subject.analyzed_commit_sha, f"current[{index}].analyzed_commit_sha"
    )
    if not _COMMIT_SHA.fullmatch(commit):
        raise SubjectPairingError("invalid_current_commit", key)
    schema = _required_text(
        subject.artifact_schema_version,
        f"current[{index}].artifact_schema_version",
    )
    if not _SEMVER.fullmatch(schema):
        raise SubjectPairingError("invalid_contract_binding", key)
    scope_hash = _required_text(
        subject.analysis_scope_hash, f"current[{index}].analysis_scope_hash"
    )
    if not _SCOPE_HASH.fullmatch(scope_hash):
        raise SubjectPairingError("invalid_scope_binding", key)
    scope_version = _required_text(
        subject.analysis_scope_hash_version,
        f"current[{index}].analysis_scope_hash_version",
    )
    if not _SEMVER.fullmatch(scope_version):
        raise SubjectPairingError("invalid_scope_binding", key)

    try:
        contracts = tuple(subject.metric_contracts)
    except TypeError as exc:
        raise SubjectPairingError("invalid_contract_binding", key) from exc
    if not contracts or not all(
        isinstance(binding, MetricContractBinding) for binding in contracts
    ):
        raise SubjectPairingError("invalid_contract_binding", key)
    names = [binding.name for binding in contracts]
    if len(names) != len(set(names)):
        raise SubjectPairingError("duplicate_contract_binding", key)
    languages = _canonical_languages(subject.languages, subject_key=key)
    return replace(
        subject,
        subject_key=key,
        subject_key_basis=basis,
        analyzed_commit_sha=commit,
        artifact_schema_version=schema,
        metric_contracts=tuple(sorted(contracts, key=lambda item: item.name)),
        analysis_scope_hash=scope_hash,
        analysis_scope_hash_version=scope_version,
        languages=languages,
    )


def _baseline_languages(baseline: RatchetBaseline) -> dict[str, tuple[str, ...]]:
    rule_by_id = {rule.rule_id: rule for rule in baseline.rules}
    languages: dict[str, set[str]] = {
        subject.subject_key: set() for subject in baseline.subjects
    }
    for observation in baseline.observations:
        if rule_by_id[observation.rule_id].scope is RatchetScope.LANGUAGE:
            # Contract 1.0 already requires a canonical non-null language here.
            languages[observation.subject_key].add(str(observation.language))
    return {
        subject_key: tuple(sorted(values))
        for subject_key, values in languages.items()
    }


def pair_subjects(
    baseline: RatchetBaseline,
    current_subjects: Iterable[CurrentSubject],
) -> tuple[SubjectPair, ...]:
    """Pair exact full portable keys and exact applicable language populations."""

    if not isinstance(baseline, RatchetBaseline):
        raise SubjectPairingError("invalid_baseline")
    baseline_by_key = {subject.subject_key: subject for subject in baseline.subjects}
    if len(baseline_by_key) != len(baseline.subjects):
        raise SubjectPairingError("duplicate_baseline_subject")

    try:
        raw_current = tuple(current_subjects)
    except TypeError as exc:
        raise SubjectPairingError("invalid_current_subjects") from exc
    validated = tuple(
        _validate_current(subject, index)
        for index, subject in enumerate(raw_current)
    )
    current_by_key = {
        str(subject.subject_key): subject for subject in validated
    }
    if len(current_by_key) != len(validated):
        raise SubjectPairingError("duplicate_current_subject")

    baseline_keys = set(baseline_by_key)
    current_keys = set(current_by_key)
    if baseline_keys != current_keys:
        missing = sorted(baseline_keys - current_keys)
        unexpected = sorted(current_keys - baseline_keys)
        detail = f"missing={missing!r}, unexpected={unexpected!r}"
        raise SubjectPairingError("subject_set_mismatch", detail)

    baseline_languages = _baseline_languages(baseline)
    pairs: list[SubjectPair] = []
    for key in sorted(baseline_keys):
        current = current_by_key[key]
        expected_languages = baseline_languages[key]
        if expected_languages != current.languages:
            raise SubjectPairingError(
                "language_set_mismatch",
                f"{key}: baseline={expected_languages!r}, current={current.languages!r}",
            )
        pairs.append(
            SubjectPair(
                baseline=baseline_by_key[key],
                current=current,
                languages=expected_languages,
            )
        )
    return tuple(pairs)


def require_contract_compatibility(pairs: Iterable[SubjectPair]) -> None:
    """Require exact V1 schema/scope versions and baseline contract bindings.

    A current subject may carry additional metric contracts because the minimal
    baseline records only contracts used by its frozen rules. Every contract
    the baseline does record must exist at the exact same version.
    """

    for pair in pairs:
        baseline = pair.baseline
        current = pair.current
        key = baseline.subject_key
        if current.artifact_schema_version != baseline.artifact_schema_version:
            raise ContractCompatibilityError("artifact_schema_mismatch", key)
        if (
            current.analysis_scope_hash_version
            != baseline.analysis_scope_hash_version
        ):
            raise ContractCompatibilityError("scope_hash_version_mismatch", key)
        current_contracts = {
            binding.name: binding.version for binding in current.metric_contracts
        }
        for binding in baseline.metric_contracts:
            if current_contracts.get(binding.name) != binding.version:
                raise ContractCompatibilityError(
                    "metric_contract_mismatch", f"{key}: {binding.name}"
                )


__all__ = [
    "ContractCompatibilityError",
    "CurrentSubject",
    "SubjectPair",
    "SubjectPairingError",
    "pair_subjects",
    "require_contract_compatibility",
]
