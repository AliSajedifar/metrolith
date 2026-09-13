"""Ratchet Baseline Contract 1.0 and its canonical byte representation.

The contract is intentionally smaller than a run manifest. It freezes only the
source-run binding, portable subjects, compatibility bindings, rule snapshot,
and complete baseline observations needed by a future admission/comparison
service. Unknown fields are rejected so audit metadata, paths, histories,
waivers, and other deferred concerns cannot quietly become V1 semantics.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, TypeAlias

from archlens_json import (
    BASELINE_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    loads_bytes,
    loads_text,
)
from modules.vocabularies import SubjectKeyBasis
from modules.ratchet.semantics import (
    MeasurementSemantics,
    MeasurementSemanticsError,
    ProducerIdentity,
)


BASELINE_FORMAT = "archlens-ratchet-baseline"
BASELINE_FORMAT_VERSION = "1.0.0"

Number: TypeAlias = int | float

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SCOPE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTRACT_NAME = re.compile(r"^[a-z][a-z0-9_.-]*$")
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class RatchetContractError(ValueError):
    """The supplied value is not a valid Ratchet Baseline 1.0 contract."""


class RatchetScope(str, Enum):
    REPOSITORY = "repository"
    LANGUAGE = "language"


class RatchetDirection(str, Enum):
    INCREASE_IS_WORSE = "increase_is_worse"
    DECREASE_IS_WORSE = "decrease_is_worse"


class RatchetSeverity(str, Enum):
    VIOLATION = "violation"
    WARNING = "warning"
    INFO = "info"


class CoordinateStatus(str, Enum):
    """Why one expected rule coordinate does or does not carry a value."""

    OBSERVED = "observed"
    INTENTIONAL_EXCLUSION = "intentional_exclusion"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RatchetContractError(f"{field} must be a non-empty trimmed string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise RatchetContractError(f"{field} must not contain control characters")
    if not unicodedata.is_normalized("NFC", value):
        raise RatchetContractError(f"{field} must use NFC Unicode normalization")
    return value


def _matching(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    text = _text(value, field)
    if not pattern.fullmatch(text):
        raise RatchetContractError(f"{field} is not canonical: {text!r}")
    return text


def _semver(value: Any, field: str) -> str:
    return _matching(value, field, _SEMVER)


def _number(value: Any, field: str, *, non_negative: bool = False) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RatchetContractError(f"{field} must be a JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise RatchetContractError(f"{field} must be finite")
    if non_negative and value < 0:
        raise RatchetContractError(f"{field} must be non-negative")
    # JSON distinguishes -0.0 in bytes even though comparison does not. One
    # canonical zero prevents an incidental sign bit from changing the digest.
    return 0 if value == 0 else value


def _enum(enum_type: type[Enum], value: Any, field: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise RatchetContractError(
            f"{field} must be one of {choices}; got {value!r}"
        ) from exc


def _strict_object(
    value: Any,
    *,
    field: str,
    required: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RatchetContractError(f"{field} must be an object")
    keys = set(value)
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing:
        raise RatchetContractError(f"{field} is missing keys: {', '.join(missing)}")
    if extra:
        raise RatchetContractError(f"{field} has unknown keys: {', '.join(extra)}")
    return value


def _array(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise RatchetContractError(f"{field} must be an array")
    return value


def _looks_like_absolute_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _language(value: Any, field: str) -> str:
    text = _text(value, field)
    canonical = unicodedata.normalize("NFC", text).casefold()
    if text != canonical:
        raise RatchetContractError(
            f"{field} must be the canonical case-folded language key"
        )
    return text


@dataclass(frozen=True, slots=True)
class SourceRunBinding:
    run_id: str
    run_manifest_sha256: str
    analysis_sha256: str
    producer_version: str
    status: str = "complete"
    admitted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _matching(self.run_id, "source_run.run_id", _RUN_ID))
        object.__setattr__(
            self,
            "run_manifest_sha256",
            _matching(
                self.run_manifest_sha256,
                "source_run.run_manifest_sha256",
                _SHA256,
            ),
        )
        object.__setattr__(
            self,
            "producer_version",
            _semver(self.producer_version, "source_run.producer_version"),
        )
        object.__setattr__(
            self,
            "analysis_sha256",
            _matching(self.analysis_sha256, "source_run.analysis_sha256", _SHA256),
        )
        if self.status != "complete":
            raise RatchetContractError("source_run.status must be 'complete'")
        if self.admitted is not True:
            raise RatchetContractError("source_run.admitted must be true")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_manifest_sha256": self.run_manifest_sha256,
            "analysis_sha256": self.analysis_sha256,
            "producer_version": self.producer_version,
            "status": self.status,
            "admitted": self.admitted,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SourceRunBinding:
        raw = _strict_object(
            value,
            field="source_run",
            required=frozenset(
                {
                    "run_id",
                    "run_manifest_sha256",
                    "analysis_sha256",
                    "producer_version",
                    "status",
                    "admitted",
                }
            ),
        )
        return cls(
            run_id=raw["run_id"],
            run_manifest_sha256=raw["run_manifest_sha256"],
            analysis_sha256=raw["analysis_sha256"],
            producer_version=raw["producer_version"],
            status=raw["status"],
            admitted=raw["admitted"],
        )


@dataclass(frozen=True, slots=True)
class MetricContractBinding:
    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _matching(self.name, "metric contract name", _CONTRACT_NAME)
        )
        object.__setattr__(
            self, "version", _semver(self.version, f"metric_contracts.{self.name}")
        )


@dataclass(frozen=True, slots=True)
class PortableSubjectBinding:
    subject_key: str
    subject_key_basis: SubjectKeyBasis | str
    analyzed_commit_sha: str
    artifact_schema_version: str
    metric_contracts: tuple[MetricContractBinding, ...]
    analysis_scope_hash: str
    analysis_scope_hash_version: str

    def __post_init__(self) -> None:
        subject_key = _text(self.subject_key, "subject.subject_key")
        if _looks_like_absolute_path(subject_key):
            raise RatchetContractError(
                "subject.subject_key must not be a local absolute path"
            )
        object.__setattr__(self, "subject_key", subject_key)

        basis = _enum(SubjectKeyBasis, self.subject_key_basis, "subject.subject_key_basis")
        if not basis.is_portable:
            raise RatchetContractError("subject.subject_key_basis must be portable")
        object.__setattr__(self, "subject_key_basis", basis)

        object.__setattr__(
            self,
            "analyzed_commit_sha",
            _matching(
                self.analyzed_commit_sha,
                "subject.analyzed_commit_sha",
                _COMMIT_SHA,
            ),
        )
        object.__setattr__(
            self,
            "artifact_schema_version",
            _semver(self.artifact_schema_version, "subject.artifact_schema_version"),
        )

        bindings = tuple(self.metric_contracts)
        if not bindings or not all(
            isinstance(binding, MetricContractBinding) for binding in bindings
        ):
            raise RatchetContractError(
                "subject.metric_contracts must contain contract bindings"
            )
        names = [binding.name for binding in bindings]
        if len(names) != len(set(names)):
            raise RatchetContractError("subject.metric_contracts contains duplicates")
        object.__setattr__(self, "metric_contracts", tuple(sorted(bindings, key=lambda item: item.name)))

        object.__setattr__(
            self,
            "analysis_scope_hash",
            _matching(
                self.analysis_scope_hash,
                "subject.analysis_scope_hash",
                _SCOPE_HASH,
            ),
        )
        object.__setattr__(
            self,
            "analysis_scope_hash_version",
            _semver(
                self.analysis_scope_hash_version,
                "subject.analysis_scope_hash_version",
            ),
        )

    @property
    def contract_versions(self) -> dict[str, str]:
        return {binding.name: binding.version for binding in self.metric_contracts}

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "subject_key_basis": self.subject_key_basis.value,
            "analyzed_commit_sha": self.analyzed_commit_sha,
            "artifact_schema_version": self.artifact_schema_version,
            "metric_contracts": self.contract_versions,
            "analysis_scope_hash": self.analysis_scope_hash,
            "analysis_scope_hash_version": self.analysis_scope_hash_version,
        }

    @classmethod
    def from_dict(cls, value: Any, *, index: int) -> PortableSubjectBinding:
        field = f"subjects[{index}]"
        raw = _strict_object(
            value,
            field=field,
            required=frozenset(
                {
                    "subject_key",
                    "subject_key_basis",
                    "analyzed_commit_sha",
                    "artifact_schema_version",
                    "metric_contracts",
                    "analysis_scope_hash",
                    "analysis_scope_hash_version",
                }
            ),
        )
        contract_map = raw["metric_contracts"]
        if not isinstance(contract_map, Mapping) or not contract_map:
            raise RatchetContractError(f"{field}.metric_contracts must be an object")
        bindings = tuple(
            MetricContractBinding(name=name, version=version)
            for name, version in contract_map.items()
        )
        return cls(
            subject_key=raw["subject_key"],
            subject_key_basis=raw["subject_key_basis"],
            analyzed_commit_sha=raw["analyzed_commit_sha"],
            artifact_schema_version=raw["artifact_schema_version"],
            metric_contracts=bindings,
            analysis_scope_hash=raw["analysis_scope_hash"],
            analysis_scope_hash_version=raw["analysis_scope_hash_version"],
        )


def canonical_metric_number(
    metric: str,
    value: Any,
    field: str,
    *,
    validate_domain: bool = True,
) -> Number:
    """Canonicalize one V1 metric number without changing metric meaning.

    Every V1 metric is non-negative.  Count, total, median, and maximum fields
    are integer-valued; only arithmetic means may retain a fractional JSON
    number.  Integral floats collapse to integers, so contract-equivalent
    ``1`` and ``1.0`` have one trust identity.
    """

    from modules.ratchet.observations import (
        ObservationExtractionError,
        require_v1_metric,
    )

    try:
        definition = require_v1_metric(metric, metric.split(".", 1)[0])
    except ObservationExtractionError as exc:
        raise RatchetContractError(f"{field} uses an unsupported metric") from exc
    number = _number(value, field, non_negative=True)
    value_type = definition.value_type
    if value_type == "integer":
        if isinstance(number, float) and not number.is_integer():
            raise RatchetContractError(f"{field} must be an integer metric value")
        number = int(number)
    elif isinstance(number, float) and number.is_integer():
        number = int(number)

    if validate_domain:
        bare = metric.rsplit(".", 1)[-1]
        if bare.startswith("cyclomatic_complexity_") and bare in {
            "cyclomatic_complexity_mean",
            "cyclomatic_complexity_median",
            "cyclomatic_complexity_max",
        } and number < 1:
            raise RatchetContractError(
                f"{field} is below the minimum possible cyclomatic complexity"
            )
    return number


@dataclass(frozen=True, slots=True)
class RatchetRule:
    rule_id: str
    metric: str
    metric_contract: str
    scope: RatchetScope | str
    direction: RatchetDirection | str
    max_regression: Number
    severity: RatchetSeverity | str = RatchetSeverity.VIOLATION

    def __post_init__(self) -> None:
        rule_id = _matching(self.rule_id, "rule.rule_id", _RULE_ID)
        if not rule_id.startswith("ratchet."):
            raise RatchetContractError("rule.rule_id must use the 'ratchet.' namespace")
        object.__setattr__(self, "rule_id", rule_id)

        scope = _enum(RatchetScope, self.scope, "rule.scope")
        object.__setattr__(self, "scope", scope)
        metric = _matching(self.metric, "rule.metric", _METRIC)
        if not metric.startswith(f"{scope.value}."):
            raise RatchetContractError(
                f"rule.metric must use the {scope.value!r} scope prefix"
            )
        # The frozen rule snapshot is part of the trusted artifact, so an
        # unsupported comparison surface must be rejected while parsing the
        # contract rather than deferred until a current run is projected.  The
        # import is intentionally local: observations owns the single BR3 V1
        # allowlist and itself depends on these contract types.
        from modules.ratchet.observations import (
            ObservationExtractionError,
            require_v1_metric,
        )

        try:
            require_v1_metric(metric, scope)
        except ObservationExtractionError as exc:
            raise RatchetContractError(
                f"rule.metric is not supported by Ratchet Baseline 1.0: "
                f"{metric!r}"
            ) from exc
        object.__setattr__(self, "metric", metric)
        object.__setattr__(
            self,
            "metric_contract",
            _matching(
                self.metric_contract,
                "rule.metric_contract",
                _CONTRACT_NAME,
            ),
        )
        object.__setattr__(
            self,
            "direction",
            _enum(RatchetDirection, self.direction, "rule.direction"),
        )
        object.__setattr__(
            self,
            "max_regression",
            canonical_metric_number(
                metric,
                self.max_regression,
                "rule.max_regression",
                validate_domain=False,
            ),
        )
        object.__setattr__(
            self,
            "severity",
            _enum(RatchetSeverity, self.severity, "rule.severity"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "metric": self.metric,
            "metric_contract": self.metric_contract,
            "scope": self.scope.value,
            "direction": self.direction.value,
            "max_regression": self.max_regression,
            "severity": self.severity.value,
        }

    @classmethod
    def from_dict(cls, value: Any, *, index: int) -> RatchetRule:
        field = f"rules[{index}]"
        raw = _strict_object(
            value,
            field=field,
            required=frozenset(
                {
                    "rule_id",
                    "metric",
                    "metric_contract",
                    "scope",
                    "direction",
                    "max_regression",
                    "severity",
                }
            ),
        )
        return cls(
            rule_id=raw["rule_id"],
            metric=raw["metric"],
            metric_contract=raw["metric_contract"],
            scope=raw["scope"],
            direction=raw["direction"],
            max_regression=raw["max_regression"],
            severity=raw["severity"],
        )


@dataclass(frozen=True, slots=True)
class BaselineObservation:
    rule_id: str
    subject_key: str
    baseline_value: Number
    language: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "rule_id", _matching(self.rule_id, "observation.rule_id", _RULE_ID)
        )
        object.__setattr__(
            self, "subject_key", _text(self.subject_key, "observation.subject_key")
        )
        if self.language is not None:
            object.__setattr__(
                self, "language", _language(self.language, "observation.language")
            )
        object.__setattr__(
            self,
            "baseline_value",
            _number(self.baseline_value, "observation.baseline_value"),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule_id, self.subject_key, self.language or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "subject_key": self.subject_key,
            "language": self.language,
            "baseline_value": self.baseline_value,
        }

    @classmethod
    def from_dict(cls, value: Any, *, index: int) -> BaselineObservation:
        field = f"observations[{index}]"
        raw = _strict_object(
            value,
            field=field,
            required=frozenset(
                {"rule_id", "subject_key", "language", "baseline_value"}
            ),
        )
        return cls(
            rule_id=raw["rule_id"],
            subject_key=raw["subject_key"],
            language=raw["language"],
            baseline_value=raw["baseline_value"],
        )


@dataclass(frozen=True, slots=True)
class CoordinateManifestEntry:
    """One exact coordinate expected by one frozen Ratchet rule."""

    rule_id: str
    subject_key: str
    metric: str
    scope: RatchetScope | str
    status: CoordinateStatus | str
    required: bool
    source_artifact: str
    source_artifact_sha256: str
    language: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "rule_id", _matching(self.rule_id, "coordinate.rule_id", _RULE_ID)
        )
        object.__setattr__(
            self, "subject_key", _text(self.subject_key, "coordinate.subject_key")
        )
        metric = _matching(self.metric, "coordinate.metric", _METRIC)
        object.__setattr__(self, "metric", metric)
        scope = _enum(RatchetScope, self.scope, "coordinate.scope")
        object.__setattr__(self, "scope", scope)
        if not metric.startswith(f"{scope.value}."):
            raise RatchetContractError("coordinate metric/scope prefix mismatch")
        status = _enum(CoordinateStatus, self.status, "coordinate.status")
        object.__setattr__(self, "status", status)
        if not isinstance(self.required, bool):
            raise RatchetContractError("coordinate.required must be boolean")
        if (status is CoordinateStatus.OBSERVED) != self.required:
            raise RatchetContractError(
                "observed coordinates must be required and non-observed coordinates must not be required"
            )
        if self.language is not None:
            object.__setattr__(
                self, "language", _language(self.language, "coordinate.language")
            )
        if scope is RatchetScope.REPOSITORY and self.language is not None:
            raise RatchetContractError("repository coordinate must not have a language")
        if scope is RatchetScope.LANGUAGE and self.language is None:
            raise RatchetContractError("language coordinate requires a language")
        if self.source_artifact not in {"analysis.json", "callables-ledger"}:
            raise RatchetContractError("coordinate.source_artifact is unsupported")
        object.__setattr__(
            self,
            "source_artifact_sha256",
            _matching(
                self.source_artifact_sha256,
                "coordinate.source_artifact_sha256",
                _SHA256,
            ),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule_id, self.subject_key, self.language or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "subject_key": self.subject_key,
            "language": self.language,
            "metric": self.metric,
            "scope": self.scope.value,
            "status": self.status.value,
            "required": self.required,
            "source_artifact": self.source_artifact,
            "source_artifact_sha256": self.source_artifact_sha256,
        }

    @classmethod
    def from_dict(cls, value: Any, *, index: int) -> "CoordinateManifestEntry":
        raw = _strict_object(
            value,
            field=f"coordinate_manifest[{index}]",
            required=frozenset(
                {
                    "rule_id",
                    "subject_key",
                    "language",
                    "metric",
                    "scope",
                    "status",
                    "required",
                    "source_artifact",
                    "source_artifact_sha256",
                }
            ),
        )
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class RatchetBaseline:
    source_run: SourceRunBinding
    producer: ProducerIdentity
    measurement_semantics: MeasurementSemantics
    subjects: tuple[PortableSubjectBinding, ...]
    rules: tuple[RatchetRule, ...]
    coordinate_manifest: tuple[CoordinateManifestEntry, ...]
    observations: tuple[BaselineObservation, ...]
    format: str = BASELINE_FORMAT
    format_version: str = BASELINE_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format != BASELINE_FORMAT:
            raise RatchetContractError(
                f"format must be {BASELINE_FORMAT!r}; got {self.format!r}"
            )
        if self.format_version != BASELINE_FORMAT_VERSION:
            raise RatchetContractError(
                "format_version must be "
                f"{BASELINE_FORMAT_VERSION!r}; got {self.format_version!r}"
            )
        if not isinstance(self.source_run, SourceRunBinding):
            raise RatchetContractError("source_run must be a SourceRunBinding")
        if not isinstance(self.producer, ProducerIdentity):
            raise RatchetContractError("producer must be a ProducerIdentity")
        if not isinstance(self.measurement_semantics, MeasurementSemantics):
            raise RatchetContractError(
                "measurement_semantics must be a MeasurementSemantics"
            )
        if self.source_run.producer_version != self.producer.program_version:
            raise RatchetContractError("source-run and producer versions disagree")
        if (
            self.producer.metric_contract_version
            != self.measurement_semantics.metric_contract_version
            or self.producer.complexity_contract_version
            != self.measurement_semantics.complexity_contract_version
        ):
            raise RatchetContractError(
                "producer and measurement-semantics contract versions disagree"
            )

        subjects = tuple(self.subjects)
        rules = tuple(self.rules)
        coordinates = tuple(self.coordinate_manifest)
        observations = tuple(self.observations)
        if not subjects or not all(
            isinstance(subject, PortableSubjectBinding) for subject in subjects
        ):
            raise RatchetContractError("subjects must contain portable subject bindings")
        if not rules or not all(isinstance(rule, RatchetRule) for rule in rules):
            raise RatchetContractError("rules must contain ratchet rule definitions")
        if not coordinates or not all(
            isinstance(coordinate, CoordinateManifestEntry)
            for coordinate in coordinates
        ):
            raise RatchetContractError(
                "coordinate_manifest must contain coordinate entries"
            )
        if not observations or not all(
            isinstance(observation, BaselineObservation)
            for observation in observations
        ):
            raise RatchetContractError("observations must contain baseline observations")

        subject_by_key = {subject.subject_key: subject for subject in subjects}
        if len(subject_by_key) != len(subjects):
            raise RatchetContractError("subjects contains duplicate subject_key values")
        rule_by_id = {rule.rule_id: rule for rule in rules}
        if len(rule_by_id) != len(rules):
            raise RatchetContractError("rules contains duplicate rule_id values")
        coordinate_keys = [coordinate.key for coordinate in coordinates]
        if len(coordinate_keys) != len(set(coordinate_keys)):
            raise RatchetContractError("coordinate_manifest contains duplicate targets")
        observation_keys = [observation.key for observation in observations]
        if len(observation_keys) != len(set(observation_keys)):
            raise RatchetContractError("observations contains duplicate targets")

        observed_rules: set[str] = set()
        observed_subjects: set[str] = set()
        used_contracts: dict[str, set[str]] = {
            subject.subject_key: set() for subject in subjects
        }
        coordinates_by_rule_subject: dict[tuple[str, str], int] = {}
        observed_coordinate_keys: set[tuple[str, str, str]] = set()
        for coordinate in coordinates:
            rule = rule_by_id.get(coordinate.rule_id)
            if rule is None:
                raise RatchetContractError(
                    f"coordinate references unknown rule {coordinate.rule_id!r}"
                )
            subject = subject_by_key.get(coordinate.subject_key)
            if subject is None:
                raise RatchetContractError(
                    f"coordinate references unknown subject {coordinate.subject_key!r}"
                )
            if coordinate.metric != rule.metric or coordinate.scope is not rule.scope:
                raise RatchetContractError("coordinate metric/scope does not match rule")
            if (
                coordinate.status is CoordinateStatus.OBSERVED
                and rule.metric_contract not in subject.contract_versions
            ):
                raise RatchetContractError(
                    f"subject {subject.subject_key!r} does not bind metric contract "
                    f"{rule.metric_contract!r}"
                )
            if coordinate.status is CoordinateStatus.OBSERVED:
                expected_contract_version = (
                    self.producer.metric_contract_version
                    if rule.metric_contract == "metrics"
                    else self.producer.complexity_contract_version
                )
                if (
                    subject.contract_versions[rule.metric_contract]
                    != expected_contract_version
                ):
                    raise RatchetContractError(
                        "subject metric contract does not match producer identity"
                    )
            from modules.ratchet.observations import MetricFamily, require_v1_metric

            definition = require_v1_metric(rule.metric, rule.scope)
            expected_artifact = (
                "callables-ledger"
                if definition.family is MetricFamily.COGNITIVE_COMPLEXITY
                else "analysis.json"
            )
            if coordinate.source_artifact != expected_artifact:
                raise RatchetContractError(
                    "coordinate source artifact does not match metric family"
                )
            if (
                expected_artifact == "analysis.json"
                and coordinate.source_artifact_sha256
                != self.source_run.analysis_sha256
            ):
                raise RatchetContractError(
                    "analysis coordinate digest does not match source run"
                )
            pair_key = (coordinate.rule_id, coordinate.subject_key)
            coordinates_by_rule_subject[pair_key] = (
                coordinates_by_rule_subject.get(pair_key, 0) + 1
            )
            if coordinate.status is CoordinateStatus.OBSERVED:
                used_contracts[subject.subject_key].add(rule.metric_contract)
                observed_coordinate_keys.add(coordinate.key)

        for rule in rules:
            for subject in subjects:
                count = coordinates_by_rule_subject.get(
                    (rule.rule_id, subject.subject_key), 0
                )
                expected = 1 if rule.scope is RatchetScope.REPOSITORY else None
                if (expected is not None and count != expected) or (
                    expected is None and count < 1
                ):
                    raise RatchetContractError(
                        "coordinate_manifest is incomplete for "
                        f"{rule.rule_id!r}/{subject.subject_key!r}"
                    )

        canonical_observations: list[BaselineObservation] = []
        for observation in observations:
            rule = rule_by_id.get(observation.rule_id)
            if rule is None:
                raise RatchetContractError(
                    f"observation references unknown rule {observation.rule_id!r}"
                )
            subject = subject_by_key.get(observation.subject_key)
            if subject is None:
                raise RatchetContractError(
                    "observation references unknown subject "
                    f"{observation.subject_key!r}"
                )
            if rule.scope is RatchetScope.REPOSITORY and observation.language is not None:
                raise RatchetContractError(
                    f"repository rule {rule.rule_id!r} must not have a language"
                )
            if rule.scope is RatchetScope.LANGUAGE and observation.language is None:
                raise RatchetContractError(
                    f"language rule {rule.rule_id!r} requires a language"
                )
            if rule.metric_contract not in subject.contract_versions:
                raise RatchetContractError(
                    f"subject {subject.subject_key!r} does not bind metric contract "
                    f"{rule.metric_contract!r}"
                )
            if observation.key not in observed_coordinate_keys:
                raise RatchetContractError(
                    "observation has no matching observed coordinate"
                )
            canonical_observations.append(
                replace(
                    observation,
                    baseline_value=canonical_metric_number(
                        rule.metric,
                        observation.baseline_value,
                        "observation.baseline_value",
                    ),
                )
            )
            observed_rules.add(rule.rule_id)
            observed_subjects.add(subject.subject_key)

        observations = tuple(canonical_observations)
        observation_keys = {observation.key for observation in observations}
        missing_observations = sorted(observed_coordinate_keys - observation_keys)
        if missing_observations:
            raise RatchetContractError(
                "observed coordinates without baseline observations: "
                + ", ".join("/".join(key) for key in missing_observations)
            )

        missing_rules = sorted(set(rule_by_id) - observed_rules)
        if missing_rules:
            raise RatchetContractError(
                "missing baseline observations for rules: " + ", ".join(missing_rules)
            )
        unused_subjects = sorted(set(subject_by_key) - observed_subjects)
        if unused_subjects:
            raise RatchetContractError(
                "subjects without baseline observations: " + ", ".join(unused_subjects)
            )
        for subject in subjects:
            declared = set(subject.contract_versions)
            unused = sorted(declared - used_contracts[subject.subject_key])
            if unused:
                raise RatchetContractError(
                    f"subject {subject.subject_key!r} has unused metric contracts: "
                    + ", ".join(unused)
                )

        object.__setattr__(
            self, "subjects", tuple(sorted(subjects, key=lambda item: item.subject_key))
        )
        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda item: item.rule_id)))
        object.__setattr__(
            self,
            "coordinate_manifest",
            tuple(sorted(coordinates, key=lambda item: item.key)),
        )
        object.__setattr__(
            self, "observations", tuple(sorted(observations, key=lambda item: item.key))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "format_version": self.format_version,
            "source_run": self.source_run.to_dict(),
            "producer": self.producer.to_dict(),
            "measurement_semantics": self.measurement_semantics.to_dict(),
            "subjects": [subject.to_dict() for subject in self.subjects],
            "rules": [rule.to_dict() for rule in self.rules],
            "coordinate_manifest": [
                coordinate.to_dict() for coordinate in self.coordinate_manifest
            ],
            "observations": [observation.to_dict() for observation in self.observations],
        }

    @classmethod
    def from_dict(cls, value: Any) -> RatchetBaseline:
        raw = _strict_object(
            value,
            field="baseline",
            required=frozenset(
                {
                    "format",
                    "format_version",
                    "source_run",
                    "producer",
                    "measurement_semantics",
                    "subjects",
                    "rules",
                    "coordinate_manifest",
                    "observations",
                }
            ),
        )
        subjects = _array(raw["subjects"], "subjects")
        rules = _array(raw["rules"], "rules")
        coordinates = _array(raw["coordinate_manifest"], "coordinate_manifest")
        observations = _array(raw["observations"], "observations")
        try:
            return cls(
                format=raw["format"],
                format_version=raw["format_version"],
                source_run=SourceRunBinding.from_dict(raw["source_run"]),
                producer=ProducerIdentity.from_dict(raw["producer"]),
                measurement_semantics=MeasurementSemantics.from_dict(
                    raw["measurement_semantics"]
                ),
                subjects=tuple(
                    PortableSubjectBinding.from_dict(item, index=index)
                    for index, item in enumerate(subjects)
                ),
                rules=tuple(
                    RatchetRule.from_dict(item, index=index)
                    for index, item in enumerate(rules)
                ),
                coordinate_manifest=tuple(
                    CoordinateManifestEntry.from_dict(item, index=index)
                    for index, item in enumerate(coordinates)
                ),
                observations=tuple(
                    BaselineObservation.from_dict(item, index=index)
                    for index, item in enumerate(observations)
                ),
            )
        except MeasurementSemanticsError as exc:
            raise RatchetContractError(str(exc)) from exc


def canonical_bytes(baseline: RatchetBaseline) -> bytes:
    """Return the one deterministic UTF-8 representation of a valid baseline."""

    if not isinstance(baseline, RatchetBaseline):
        raise RatchetContractError("canonical serialization requires RatchetBaseline")
    try:
        rendered = dumps_strict(
            baseline.to_dict(),
            source="ratchet baseline",
            limits=BASELINE_JSON_LIMITS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except StrictJsonError as exc:
        raise RatchetContractError(str(exc)) from None
    return rendered.encode("utf-8")


def canonical_json(baseline: RatchetBaseline) -> str:
    return canonical_bytes(baseline).decode("utf-8")


def parse_baseline_json(payload: str | bytes) -> RatchetBaseline:
    """Parse JSON bytes/text without file I/O and validate Contract 1.0 strictly."""

    try:
        if isinstance(payload, bytes):
            raw = loads_bytes(
                payload,
                source="ratchet baseline",
                limits=BASELINE_JSON_LIMITS,
                expect=dict,
            )
        elif isinstance(payload, str):
            raw = loads_text(
                payload,
                source="ratchet baseline",
                limits=BASELINE_JSON_LIMITS,
                expect=dict,
            )
        else:
            raise RatchetContractError("baseline JSON must be text or bytes")
    except StrictJsonError as exc:
        raise RatchetContractError(str(exc)) from None
    return RatchetBaseline.from_dict(raw)


__all__ = [
    "BASELINE_FORMAT",
    "BASELINE_FORMAT_VERSION",
    "BaselineObservation",
    "CoordinateManifestEntry",
    "CoordinateStatus",
    "MetricContractBinding",
    "Number",
    "PortableSubjectBinding",
    "RatchetBaseline",
    "RatchetContractError",
    "RatchetDirection",
    "RatchetRule",
    "RatchetScope",
    "RatchetSeverity",
    "SourceRunBinding",
    "canonical_bytes",
    "canonical_metric_number",
    "canonical_json",
    "parse_baseline_json",
]
