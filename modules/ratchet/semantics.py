"""Canonical measurement-semantics and producer identity for Ratchet V1.

The source scope hash answers *what* was measured.  This module answers the
separate question *how* it was measured.  Only persisted, measurement-relevant
run-manifest fields participate; machine paths, timestamps, host names, run
identifiers, and other delivery metadata are deliberately absent.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from archlens_json import BASELINE_JSON_LIMITS, StrictJsonError, dumps_strict


SEMANTICS_FINGERPRINT_VERSION = "1.0.0"

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_IDENTITY = re.compile(
    r"^(?:git:[0-9a-f]{40,64}:(?:clean|dirty)|sha256:[0-9a-f]{64})$"
)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class MeasurementSemanticsError(ValueError):
    """Persisted run facts cannot form a canonical semantics identity."""


class SemanticsCompatibilityError(ValueError):
    """Baseline and current measurements do not have identical semantics."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise MeasurementSemanticsError(f"{field} must be a non-empty trimmed string")
    if not unicodedata.is_normalized("NFC", value):
        raise MeasurementSemanticsError(f"{field} must use NFC normalization")
    return value


def _matching(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    text = _text(value, field)
    if not pattern.fullmatch(text):
        raise MeasurementSemanticsError(f"{field} is not canonical: {text!r}")
    return text


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MeasurementSemanticsError(f"{field} must be an object")
    return value


def _strict_object(
    value: Any, *, field: str, required: frozenset[str]
) -> Mapping[str, Any]:
    raw = _mapping(value, field)
    keys = set(raw)
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing:
        raise MeasurementSemanticsError(
            f"{field} is missing keys: {', '.join(missing)}"
        )
    if extra:
        raise MeasurementSemanticsError(
            f"{field} has unknown keys: {', '.join(extra)}"
        )
    return raw


def _plain(value: Any, field: str) -> Any:
    """Return a JSON-domain copy with deterministic string-key validation."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # No selected semantics component needs floating-point representation.
        # Refusing it prevents 1/1.0 from becoming a configuration identity
        # question unrelated to Ratchet metric numeric canonicalization.
        raise MeasurementSemanticsError(f"{field} must not contain floats")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            canonical_key = _text(key, f"{field} key")
            if canonical_key in result:
                raise MeasurementSemanticsError(f"{field} contains duplicate keys")
            result[canonical_key] = _plain(item, f"{field}.{canonical_key}")
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_plain(item, f"{field}[]") for item in value]
    raise MeasurementSemanticsError(f"{field} contains a non-JSON value")


def _canonical_bytes(value: Any, source: str) -> bytes:
    try:
        rendered = dumps_strict(
            _plain(value, source),
            source=source,
            limits=BASELINE_JSON_LIMITS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except StrictJsonError as exc:
        raise MeasurementSemanticsError(str(exc)) from None
    return rendered.encode("utf-8")


def _digest(value: Any, source: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, source)).hexdigest()


@dataclass(frozen=True, slots=True)
class VersionBinding:
    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _matching(self.name, "version name", _NAME))
        object.__setattr__(
            self, "version", _text(self.version, f"version[{self.name}]")
        )


def _bindings(
    values: Sequence[VersionBinding], field: str
) -> tuple[VersionBinding, ...]:
    items = tuple(values)
    if not items or not all(isinstance(item, VersionBinding) for item in items):
        raise MeasurementSemanticsError(f"{field} must contain version bindings")
    names = [item.name for item in items]
    if len(names) != len(set(names)):
        raise MeasurementSemanticsError(f"{field} contains duplicate names")
    return tuple(sorted(items, key=lambda item: item.name))


@dataclass(frozen=True, slots=True)
class ProducerIdentity:
    program_name: str
    program_version: str
    evaluator_source_identity: str
    metric_contract_version: str
    complexity_contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "program_name", _text(self.program_name, "producer.program_name"))
        object.__setattr__(
            self,
            "program_version",
            _matching(self.program_version, "producer.program_version", _SEMVER),
        )
        object.__setattr__(
            self,
            "evaluator_source_identity",
            _matching(
                self.evaluator_source_identity,
                "producer.evaluator_source_identity",
                _SOURCE_IDENTITY,
            ),
        )
        object.__setattr__(
            self,
            "metric_contract_version",
            _matching(
                self.metric_contract_version,
                "producer.metric_contract_version",
                _SEMVER,
            ),
        )
        object.__setattr__(
            self,
            "complexity_contract_version",
            _matching(
                self.complexity_contract_version,
                "producer.complexity_contract_version",
                _SEMVER,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "program_name": self.program_name,
            "program_version": self.program_version,
            "evaluator_source_identity": self.evaluator_source_identity,
            "metric_contract_version": self.metric_contract_version,
            "complexity_contract_version": self.complexity_contract_version,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProducerIdentity":
        raw = _strict_object(
            value,
            field="producer",
            required=frozenset(
                {
                    "program_name",
                    "program_version",
                    "evaluator_source_identity",
                    "metric_contract_version",
                    "complexity_contract_version",
                }
            ),
        )
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class MeasurementSemantics:
    metric_contract_version: str
    complexity_contract_version: str
    exclusion_policy_version: str
    exclusion_policy_sha256: str
    effective_exclusion_configuration_sha256: str
    parser_configuration_sha256: str
    metric_options_sha256: str
    grammar_versions: tuple[VersionBinding, ...]
    runtime_versions: tuple[VersionBinding, ...]
    fingerprint_sha256: str | None = None
    fingerprint_version: str = SEMANTICS_FINGERPRINT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "metric_contract_version",
            "complexity_contract_version",
            "exclusion_policy_version",
        ):
            object.__setattr__(
                self,
                name,
                _matching(getattr(self, name), f"semantics.{name}", _SEMVER),
            )
        for name in (
            "exclusion_policy_sha256",
            "effective_exclusion_configuration_sha256",
            "parser_configuration_sha256",
            "metric_options_sha256",
        ):
            object.__setattr__(
                self,
                name,
                _matching(getattr(self, name), f"semantics.{name}", _SHA256),
            )
        object.__setattr__(
            self,
            "fingerprint_version",
            _matching(
                self.fingerprint_version,
                "semantics.fingerprint_version",
                _SEMVER,
            ),
        )
        if self.fingerprint_version != SEMANTICS_FINGERPRINT_VERSION:
            raise MeasurementSemanticsError(
                "unsupported measurement-semantics fingerprint version"
            )
        object.__setattr__(
            self, "grammar_versions", _bindings(self.grammar_versions, "grammar_versions")
        )
        object.__setattr__(
            self, "runtime_versions", _bindings(self.runtime_versions, "runtime_versions")
        )
        expected = hashlib.sha256(_canonical_bytes(self._fingerprint_body(), "measurement semantics")).hexdigest()
        supplied = self.fingerprint_sha256
        if supplied is not None:
            supplied = _matching(
                supplied, "semantics.fingerprint_sha256", _SHA256
            )
            if supplied != expected:
                raise MeasurementSemanticsError(
                    "measurement-semantics fingerprint does not match its components"
                )
        object.__setattr__(self, "fingerprint_sha256", expected)

    def _fingerprint_body(self) -> dict[str, Any]:
        return {
            "fingerprint_version": self.fingerprint_version,
            "metric_contract_version": self.metric_contract_version,
            "complexity_contract_version": self.complexity_contract_version,
            "exclusion_policy_version": self.exclusion_policy_version,
            "exclusion_policy_sha256": self.exclusion_policy_sha256,
            "effective_exclusion_configuration_sha256": (
                self.effective_exclusion_configuration_sha256
            ),
            "parser_configuration_sha256": self.parser_configuration_sha256,
            "metric_options_sha256": self.metric_options_sha256,
            "grammar_versions": {
                item.name: item.version for item in self.grammar_versions
            },
            "runtime_versions": {
                item.name: item.version for item in self.runtime_versions
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._fingerprint_body(), "fingerprint_sha256": self.fingerprint_sha256}

    @classmethod
    def from_dict(cls, value: Any) -> "MeasurementSemantics":
        required = frozenset(
            {
                "fingerprint_version",
                "fingerprint_sha256",
                "metric_contract_version",
                "complexity_contract_version",
                "exclusion_policy_version",
                "exclusion_policy_sha256",
                "effective_exclusion_configuration_sha256",
                "parser_configuration_sha256",
                "metric_options_sha256",
                "grammar_versions",
                "runtime_versions",
            }
        )
        raw = _strict_object(value, field="measurement_semantics", required=required)
        grammar = _mapping(raw["grammar_versions"], "grammar_versions")
        runtime = _mapping(raw["runtime_versions"], "runtime_versions")
        return cls(
            metric_contract_version=raw["metric_contract_version"],
            complexity_contract_version=raw["complexity_contract_version"],
            exclusion_policy_version=raw["exclusion_policy_version"],
            exclusion_policy_sha256=raw["exclusion_policy_sha256"],
            effective_exclusion_configuration_sha256=raw[
                "effective_exclusion_configuration_sha256"
            ],
            parser_configuration_sha256=raw["parser_configuration_sha256"],
            metric_options_sha256=raw["metric_options_sha256"],
            grammar_versions=tuple(
                VersionBinding(name, version) for name, version in grammar.items()
            ),
            runtime_versions=tuple(
                VersionBinding(name, version) for name, version in runtime.items()
            ),
            fingerprint_sha256=raw["fingerprint_sha256"],
            fingerprint_version=raw["fingerprint_version"],
        )


def _required(manifest: Mapping[str, Any], name: str) -> Any:
    value = manifest.get(name)
    if value is None:
        raise MeasurementSemanticsError(f"run manifest is missing {name}")
    return value


def semantics_from_manifest(manifest: Mapping[str, Any]) -> MeasurementSemantics:
    """Project the versioned semantic identity from persisted manifest facts."""

    raw = _mapping(manifest, "run manifest")
    environment = _mapping(
        _required(raw, "benchmark_environment"), "benchmark_environment"
    )
    effective = _mapping(
        _required(raw, "effective_configuration"), "effective_configuration"
    )
    exclusion = _mapping(
        _required(effective, "exclusion_policy"),
        "effective_configuration.exclusion_policy",
    )
    grammar = _mapping(_required(environment, "grammar_versions"), "grammar_versions")
    parser_configuration = {
        "parser_initialization": _mapping(
            _required(environment, "parser_initialization"),
            "parser_initialization",
        ),
        "supported_extensions": _mapping(
            _required(effective, "supported_extensions"), "supported_extensions"
        ),
    }
    metric_options = {
        "execution_mode": _required(raw, "execution_mode"),
        "full_inventory": _required(effective, "full_inventory"),
        "max_source_file_size_bytes": _required(
            effective, "max_source_file_size_bytes"
        ),
        "effective_git_checkout_configuration": _mapping(
            _required(effective, "effective_git_checkout_configuration"),
            "effective_git_checkout_configuration",
        ),
    }
    runtime = {
        "python": _required(environment, "python_version"),
        "tree-sitter": _required(environment, "tree_sitter_version"),
    }
    return MeasurementSemantics(
        metric_contract_version=_required(raw, "metric_contract_version"),
        complexity_contract_version=_required(raw, "complexity_contract_version"),
        exclusion_policy_version=_required(raw, "exclusion_policy_version"),
        exclusion_policy_sha256=_required(raw, "exclusion_policy_sha256"),
        effective_exclusion_configuration_sha256=_digest(
            exclusion, "effective exclusion configuration"
        ),
        parser_configuration_sha256=_digest(
            parser_configuration, "parser configuration"
        ),
        metric_options_sha256=_digest(metric_options, "metric options"),
        grammar_versions=tuple(
            VersionBinding(name, version) for name, version in grammar.items()
        ),
        runtime_versions=tuple(
            VersionBinding(name, version) for name, version in runtime.items()
        ),
    )


def producer_from_manifest(manifest: Mapping[str, Any]) -> ProducerIdentity:
    """Project the producer identity recorded by the completed run."""

    raw = _mapping(manifest, "run manifest")
    kind = raw.get("profiler_provenance_kind")
    if kind == "installed_distribution":
        source_sha = _matching(
            _required(raw, "profiler_source_sha256"),
            "profiler_source_sha256",
            _SHA256,
        )
        if raw.get("profiler_git_state") != "not_applicable":
            raise MeasurementSemanticsError(
                "installed distribution profiler_git_state must be not_applicable"
            )
        if raw.get("profiler_git_dirty") is not None:
            raise MeasurementSemanticsError(
                "installed distribution profiler_git_dirty must be null"
            )
        source_identity = f"sha256:{source_sha}"
    else:
        # Historical manifests have no explicit kind and retain the original
        # Git-worktree producer contract.
        sha = _matching(
            _required(raw, "profiler_git_commit_sha"),
            "profiler_git_commit_sha",
            re.compile(r"^[0-9a-f]{40,64}$"),
        )
        dirty = _required(raw, "profiler_git_dirty")
        if not isinstance(dirty, bool):
            raise MeasurementSemanticsError("profiler_git_dirty must be boolean")
        source_identity = f"git:{sha}:{'dirty' if dirty else 'clean'}"
    return ProducerIdentity(
        program_name=_required(raw, "product_name"),
        program_version=_required(raw, "program_version"),
        evaluator_source_identity=source_identity,
        metric_contract_version=_required(raw, "metric_contract_version"),
        complexity_contract_version=_required(raw, "complexity_contract_version"),
    )


def require_semantics_compatibility(
    baseline: MeasurementSemantics,
    current: MeasurementSemantics,
) -> None:
    if not isinstance(baseline, MeasurementSemantics) or not isinstance(
        current, MeasurementSemantics
    ):
        raise SemanticsCompatibilityError("measurement_semantics_required")
    if baseline.fingerprint_version != current.fingerprint_version:
        raise SemanticsCompatibilityError("semantics_fingerprint_version_mismatch")
    if baseline.fingerprint_sha256 != current.fingerprint_sha256:
        raise SemanticsCompatibilityError("measurement_semantics_mismatch")


def require_producer_compatibility(
    baseline: ProducerIdentity,
    current: ProducerIdentity,
) -> None:
    if not isinstance(baseline, ProducerIdentity) or not isinstance(
        current, ProducerIdentity
    ):
        raise SemanticsCompatibilityError("producer_identity_required")
    if baseline != current:
        raise SemanticsCompatibilityError("producer_identity_mismatch")


__all__ = [
    "MeasurementSemantics",
    "MeasurementSemanticsError",
    "ProducerIdentity",
    "SEMANTICS_FINGERPRINT_VERSION",
    "SemanticsCompatibilityError",
    "VersionBinding",
    "producer_from_manifest",
    "require_producer_compatibility",
    "require_semantics_compatibility",
    "semantics_from_manifest",
]
