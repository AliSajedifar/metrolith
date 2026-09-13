"""Active standalone-output contract identities and validator discovery.

Duplication, Changed-Code, and Hotspots are deliberately not Artifact schemas.
They nevertheless need one small authority for their active format identities,
exact compatibility rule, and existing in-process validators.  This registry is
not a migration system and does not alter historical documents.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping


EXACT_VERSION_COMPATIBILITY = "exact-version"

CHANGED_CODE_FORMAT = "archlens-changed-code"
CHANGED_CODE_FORMAT_VERSION = "1.0.0"

DUPLICATION_FORMAT = "archlens-duplication"
DUPLICATION_FORMAT_VERSION = "1.0.0"
DUPLICATION_CONTRACT_VERSION = "1.0.0"

HOTSPOT_FORMAT = "archlens-hotspots"
HOTSPOT_FORMAT_VERSION = "1.0.0"

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class StandaloneContractError(ValueError):
    """Standalone contract metadata or a supplied identity is invalid."""


class UnsupportedStandaloneContract(StandaloneContractError):
    """No active exact-version contract accepts the supplied identity."""


@dataclass(frozen=True, slots=True)
class StandaloneContract:
    format_name: str
    format_version: str
    validator_path: str
    compatibility_policy: str = EXACT_VERSION_COMPATIBILITY
    analysis_contract_version: str | None = None
    #: The top-level document key that carries the analysis contract version,
    #: when the format declares one. Recorded here rather than at each consumer
    #: because it is the same category of fact as the format identity: which
    #: field names the contract a reader must be able to interpret. A consumer
    #: that hard-coded the key would be a second place to update when a format
    #: adds one, and the one most likely to be missed.
    analysis_contract_field: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "format_name": self.format_name,
            "format_version": self.format_version,
            "validator": self.validator_path,
            "compatibility_policy": self.compatibility_policy,
            "analysis_contract_version": self.analysis_contract_version,
            "analysis_contract_field": self.analysis_contract_field,
        }


def build_contract_registry(
    contracts: Iterable[StandaloneContract],
) -> Mapping[str, StandaloneContract]:
    """Build an immutable active registry, failing on ambiguous identities."""

    registry: dict[str, StandaloneContract] = {}
    for contract in contracts:
        if not contract.format_name or contract.format_name.strip() != contract.format_name:
            raise StandaloneContractError("format name must be a non-empty trimmed string")
        if contract.format_name in registry:
            raise StandaloneContractError(
                f"duplicate active standalone format: {contract.format_name}"
            )
        if not _SEMVER.fullmatch(contract.format_version):
            raise StandaloneContractError(
                f"invalid format version for {contract.format_name}: "
                f"{contract.format_version!r}"
            )
        if (
            contract.analysis_contract_version is not None
            and not _SEMVER.fullmatch(contract.analysis_contract_version)
        ):
            raise StandaloneContractError(
                f"invalid analysis contract version for {contract.format_name}: "
                f"{contract.analysis_contract_version!r}"
            )
        # Both or neither. A declared version with no field naming it cannot be
        # checked against a document, and a field with no version has nothing to
        # check it against; either half alone is a contract that silently is not
        # enforced.
        if (contract.analysis_contract_version is None) != (
            contract.analysis_contract_field is None
        ):
            raise StandaloneContractError(
                f"analysis contract version and field must be declared together "
                f"for {contract.format_name}"
            )
        if contract.analysis_contract_field is not None and (
            not contract.analysis_contract_field
            or contract.analysis_contract_field.strip()
            != contract.analysis_contract_field
        ):
            raise StandaloneContractError(
                f"analysis contract field must be a non-empty trimmed key for "
                f"{contract.format_name}"
            )
        if contract.compatibility_policy != EXACT_VERSION_COMPATIBILITY:
            raise StandaloneContractError(
                f"unsupported compatibility policy for {contract.format_name}: "
                f"{contract.compatibility_policy!r}"
            )
        module_name, separator, attribute = contract.validator_path.partition(":")
        if not separator or not module_name or not attribute:
            raise StandaloneContractError(
                f"validator must be module:attribute for {contract.format_name}"
            )
        registry[contract.format_name] = contract
    return MappingProxyType(registry)


STANDALONE_CONTRACTS = build_contract_registry(
    (
        StandaloneContract(
            CHANGED_CODE_FORMAT,
            CHANGED_CODE_FORMAT_VERSION,
            "modules.changed_code:validate_document",
        ),
        StandaloneContract(
            DUPLICATION_FORMAT,
            DUPLICATION_FORMAT_VERSION,
            "modules.duplication.output:validate_duplication_document",
            analysis_contract_version=DUPLICATION_CONTRACT_VERSION,
            analysis_contract_field="duplication_contract_version",
        ),
        StandaloneContract(
            HOTSPOT_FORMAT,
            HOTSPOT_FORMAT_VERSION,
            "modules.hotspots:validate_hotspot_document",
        ),
    )
)


def compatibility_status(format_name: Any, format_version: Any) -> str:
    """Return a deterministic, closed compatibility classification."""

    if not isinstance(format_name, str) or not isinstance(format_version, str):
        return "invalid_identity"
    if not _SEMVER.fullmatch(format_version):
        return "invalid_identity"
    contract = STANDALONE_CONTRACTS.get(format_name)
    if contract is None:
        return "unknown_format"
    if format_version != contract.format_version:
        return "unsupported_version"
    return "compatible"


def require_compatible_contract(
    format_name: Any, format_version: Any
) -> StandaloneContract:
    status = compatibility_status(format_name, format_version)
    if status != "compatible":
        raise UnsupportedStandaloneContract(
            f"standalone contract {format_name!r} {format_version!r}: {status}"
        )
    return STANDALONE_CONTRACTS[format_name]


def resolve_validator(
    format_name: Any, format_version: Any
) -> Callable[[Mapping[str, Any]], None]:
    """Resolve the existing validator only after exact identity admission."""

    contract = require_compatible_contract(format_name, format_version)
    module_name, _, attribute = contract.validator_path.partition(":")
    validator = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(validator):
        raise StandaloneContractError(
            f"validator is not callable for {contract.format_name}: "
            f"{contract.validator_path}"
        )
    return validator


def validate_standalone_document(document: Mapping[str, Any]) -> None:
    """Discover and invoke the active validator from document identity."""

    resolve_validator(document.get("format"), document.get("format_version"))(document)


def validate_contract_registry() -> list[dict[str, str | None]]:
    """Resolve every active validator and return deterministic release evidence."""

    rows: list[dict[str, str | None]] = []
    for format_name in sorted(STANDALONE_CONTRACTS):
        contract = STANDALONE_CONTRACTS[format_name]
        resolve_validator(contract.format_name, contract.format_version)
        rows.append(contract.as_dict())
    return rows


__all__ = [
    "CHANGED_CODE_FORMAT",
    "CHANGED_CODE_FORMAT_VERSION",
    "DUPLICATION_CONTRACT_VERSION",
    "DUPLICATION_FORMAT",
    "DUPLICATION_FORMAT_VERSION",
    "EXACT_VERSION_COMPATIBILITY",
    "HOTSPOT_FORMAT",
    "HOTSPOT_FORMAT_VERSION",
    "STANDALONE_CONTRACTS",
    "StandaloneContract",
    "StandaloneContractError",
    "UnsupportedStandaloneContract",
    "build_contract_registry",
    "compatibility_status",
    "require_compatible_contract",
    "resolve_validator",
    "validate_contract_registry",
    "validate_standalone_document",
]
