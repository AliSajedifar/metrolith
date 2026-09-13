"""Canonical callable record and its closed vocabularies.

Complexity Contract 2.0.0. This module owns the record shape and the row-identity
construction; it computes no metric and knows no grammar.

**The record identity is row identity within one artifact.** It is deliberately
named ``callable_row_id`` rather than ``callable_id``: it is *not* a stable
cross-revision identity and must never be used as one. A future cross-revision
identity is a separate, versioned contract, and leaving the shorter name unused
is what keeps that door open without a rename.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from modules.config import COMPLEXITY_CONTRACT_VERSION

#: The contract version is owned by ``modules.config`` and re-exported here for
#: the established callable-analysis API. Artifact writers must not redeclare it.

#: The canonical population. Exactly the kinds that contribute to
#: `methods_functions`; see docs/COMPLEXITY_CONTRACT_V1.md section 2.
KIND_MODULE_FUNCTION = "module_function"
KIND_CLASS_METHOD = "class_method"
KIND_RECEIVER_METHOD = "receiver_method"
CALLABLE_KINDS = (KIND_MODULE_FUNCTION, KIND_CLASS_METHOD, KIND_RECEIVER_METHOD)

#: Why the row key needed what it needed.
BASIS_QUALIFIED = "qualified"
BASIS_QUALIFIED_WITH_SIGNATURE = "qualified_with_signature"
BASIS_POSITIONAL = "positional"
ROW_ID_BASES = (BASIS_QUALIFIED, BASIS_QUALIFIED_WITH_SIGNATURE, BASIS_POSITIONAL)

OWNER_MODULE = "module"
OWNER_CLASS = "class"
OWNER_INTERFACE = "interface"
OWNER_ENUM = "enum"
OWNER_RECORD = "record"
OWNER_OBJECT_LITERAL = "object_literal"
OWNER_RECEIVER_TYPE = "receiver_type"
OWNER_CALLABLE = "callable"
OWNER_KINDS = (
    OWNER_MODULE, OWNER_CLASS, OWNER_INTERFACE, OWNER_ENUM, OWNER_RECORD,
    OWNER_OBJECT_LITERAL, OWNER_RECEIVER_TYPE, OWNER_CALLABLE,
)

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUSES = (STATUS_COMPLETE, STATUS_PARTIAL, STATUS_FAILED, STATUS_NOT_APPLICABLE)

#: Field separator for the row key. Matches the `analysis_scope_hash`
#: construction convention so Metrolith has one digest-input style, not two.
_SEPARATOR = "\x1f"


@dataclass(slots=True)
class CallableRecord:
    """One measured callable.

    Populated in two passes. The language visitor fills identity and location;
    ``assign_row_ids`` then resolves collisions and computes ``callable_row_id``,
    because an ordinal cannot be known until every sibling in the file is known.

    Metric fields are declared here and stay ``None`` under Complexity Contract
    1.0.0 enumeration (milestone C1). Milestone C2 fills them in the same
    traversal that produced the record.
    """

    # -- identity components (all six feed the row key) ----------------------
    name: str
    qualified_name: str
    callable_kind: str
    signature_discriminator: str | None = None
    ordinal: int | None = None
    row_id_basis: str = BASIS_QUALIFIED
    callable_row_id: str | None = None

    # -- owner and receiver evidence ----------------------------------------
    owner_kind: str | None = None
    owner_name: str | None = None
    receiver_type_name: str | None = None
    receiver_is_pointer: bool | None = None

    # -- location evidence ---------------------------------------------------
    start_line: int = 0
    end_line: int = 0
    body_start_line: int | None = None
    body_end_line: int | None = None
    location_maps_to_original_source: bool = True

    # -- measurement ---------------------------------------------------------
    structural_complexity_status: str = STATUS_NOT_APPLICABLE
    nloc_status: str = STATUS_NOT_APPLICABLE
    nloc: int | None = None
    formal_parameter_count: int | None = None
    declares_typescript_this_parameter: bool | None = None
    cyclomatic_complexity: int | None = None
    decision_point_count: int | None = None
    boolean_operator_count: int | None = None
    max_condition_operator_count: int | None = None
    max_nesting_depth: int | None = None
    #: Metrolith Cognitive Complexity (G1-B). In-memory only: no artifact column,
    #: no Complexity Contract 2.0.0, nothing persisted. Frozen rule table
    #: revision 3 is the specification.
    cognitive_complexity: int | None = None

    #: Document-order position within the file. Traversal order, never persisted
    #: as identity — it exists so ordinals are assigned deterministically.
    _order: int = field(default=0, repr=False)

    def identity_components(self, relative_path: str, language: str) -> tuple[str, ...]:
        """The six row-key components, absent values rendered as empty strings."""
        return (
            relative_path,
            language,
            self.callable_kind,
            self.qualified_name,
            self.signature_discriminator or "",
            "" if self.ordinal is None else str(self.ordinal),
        )


def compute_row_id(record: CallableRecord, relative_path: str, language: str) -> str:
    """Deterministic ``sha256:<hex>`` over the six identity components."""
    payload = _SEPARATOR.join(record.identity_components(relative_path, language))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def assign_row_ids(
    records: list[CallableRecord], relative_path: str, language: str
) -> list[CallableRecord]:
    """Resolve collisions, set ``row_id_basis``, and compute every row key.

    An ordinal is assigned **only** to members of a colliding group, so the
    common case carries no positional component and a record's key does not move
    because an unrelated sibling was added. ``row_id_basis`` records which of the
    three cases applied, which is the field a future cross-revision identity
    contract would build on and the field Diff would use to exclude rows it
    cannot honestly match.
    """
    groups: dict[tuple[str, str, str], list[CallableRecord]] = {}
    for record in records:
        key = (
            record.callable_kind,
            record.qualified_name,
            record.signature_discriminator or "",
        )
        groups.setdefault(key, []).append(record)

    for group in groups.values():
        if len(group) == 1:
            record = group[0]
            record.ordinal = None
            record.row_id_basis = (
                BASIS_QUALIFIED_WITH_SIGNATURE
                if record.signature_discriminator
                else BASIS_QUALIFIED
            )
            continue
        # A collision the first five components could not resolve. Every member
        # becomes positional -- including the first, because "the one without an
        # ordinal" would be an identity that silently changes when an earlier
        # sibling is deleted.
        for index, record in enumerate(sorted(group, key=lambda item: item._order)):
            record.ordinal = index
            record.row_id_basis = BASIS_POSITIONAL

    for record in records:
        record.callable_row_id = compute_row_id(record, relative_path, language)
    return records


def qualify(owner_qualified_name: str | None, name: str) -> str:
    """Join a lexical owner chain segment. Non-callable blocks contribute none."""
    return f"{owner_qualified_name}.{name}" if owner_qualified_name else name
