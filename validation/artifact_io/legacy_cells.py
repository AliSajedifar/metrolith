"""Version-scoped recovery of Artifact <= 1.3 Python-``repr`` list cells.

Finding F-1. ArchLens changed how it serialized list-valued CSV cells at the
Artifact 1.3 -> 1.4 boundary::

    artifact <= 1.3.0    ['classes_structs', 'methods_functions']    not JSON
    artifact >= 1.4.0    ["classes_structs", "methods_functions"]    JSON

Counted over every preserved run that emits these columns, the split is clean:
128 ``errors.csv`` cells and 477 ``recoveries.csv`` cells at Artifact 1.3 are
Python ``repr``; every Artifact 1.4 cell is JSON. No run mixes the two forms.

**This module is a translation at a version boundary, not a relaxation of the
canonical rule.** Plan section 7.5 requires malformed JSON cells to be rejected;
plan section 7.2 requires Artifact 1.3 to remain readable. Both hold, because
they govern different contracts:

* For Artifact 1.4 and the 1.5 native target, a list cell **is** JSON, and a
  non-JSON cell is genuinely malformed. Nothing weakens here.
* Artifact <= 1.3 never claimed to emit JSON in these cells. Such a cell is not
  malformed *for its own generation*; it is correctly formed under a superseded
  serialization.

Recovery is therefore confined to a closed column set on a closed version range,
is attempted only after canonical JSON parsing has already failed, and is always
announced in a diagnostic. It is never silent, and it never runs at Artifact
>= 1.4, where a Python-``repr`` cell indicates corruption or tampering and must
fail.

``ast.literal_eval`` is used, never ``eval``. ``literal_eval`` evaluates literal
structures only and executes no code.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any

from .errors import StructuralErrorCode, raise_structural

# The closed allowlist. A column outside this set is never eligible for
# recovery, whatever version the artifact declares.
LEGACY_LIST_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("errors.csv", "affected_metrics"),
        ("recoveries.csv", "affected_metrics"),
        ("recoveries.csv", "fallback_strategies"),
        ("recoveries.csv", "selected_fallback_strategies"),
    }
)

# Recovery applies to declared artifact schema versions strictly below this.
LEGACY_REPR_BOUNDARY: tuple[int, int, int] = (1, 4, 0)

# Cells are bounded before ``literal_eval`` runs. Deeply nested literal input is
# a resource-exhaustion surface, and a length bound plus the
# list-of-plain-strings element rule closes it. The largest observed legacy cell
# across all preserved fixtures is far below this.
MAX_LEGACY_CELL_CHARACTERS = 8192


@dataclass(frozen=True)
class LegacyCellRecovery:
    """One announced recovery of a superseded cell serialization.

    ``raw`` is preserved verbatim alongside ``decoded`` so the original bytes
    stay auditable and no evidence is destroyed (plan section 3.7).
    """

    artifact: str
    column: str
    row: int
    declared_artifact_schema_version: str | None
    raw: str
    decoded: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovery": "legacy_python_repr_list_cell",
            "artifact": self.artifact,
            "column": self.column,
            "row": self.row,
            "declared_artifact_schema_version": self.declared_artifact_schema_version,
            "raw_value": self.raw,
            "decoded_value": list(self.decoded),
        }

    def __str__(self) -> str:
        return (
            f"[legacy_python_repr_list_cell] {self.artifact} row {self.row}, "
            f"column {self.column!r}: artifact schema "
            f"{self.declared_artifact_schema_version} predates JSON list cells; "
            f"recovered {self.raw!r} as {self.decoded!r}"
        )


def is_legacy_list_column(artifact: str, column: str) -> bool:
    """Whether ``(artifact, column)`` is in the closed recovery allowlist."""
    return (artifact, column) in LEGACY_LIST_COLUMNS


def legacy_recovery_applies(
    parsed_version: tuple[int, int, int] | None,
    boundary: tuple[int, int, int] = LEGACY_REPR_BOUNDARY,
) -> bool:
    """Whether a declared version is old enough for recovery to be reachable.

    An undeclared or unparseable version returns ``False``. Recovery requires
    positive evidence that the artifact belongs to the superseded generation;
    absence of a version is not such evidence (plan section 3.7).
    """
    if parsed_version is None:
        return False
    return parsed_version < boundary


def decode_legacy_list_cell(
    raw: str,
    *,
    artifact: str,
    column: str,
    row: int,
    declared_version: str | None,
) -> LegacyCellRecovery:
    """Recover one Python-``repr`` list cell, or raise a typed structural error.

    The caller is responsible for having established all three gates first:
    canonical ``json.loads`` already failed, the column is in the allowlist, and
    the declared version is below the boundary. This function re-checks the
    bound and the shape, and refuses anything it cannot prove is a flat list of
    plain strings.

    Failure to recover is a hard failure reported as
    :data:`~validation.artifact_io.errors.StructuralErrorCode.CSV_JSON_CELL_MALFORMED`,
    the same code a malformed canonical cell produces.
    """
    location = f"row {row}, column {column!r}"

    if len(raw) > MAX_LEGACY_CELL_CHARACTERS:
        raise_structural(
            StructuralErrorCode.CSV_JSON_CELL_MALFORMED, artifact,
            f"legacy list cell is {len(raw)} characters, above the "
            f"{MAX_LEGACY_CELL_CHARACTERS} character bound for literal recovery",
            location=location, column=column, row=row,
        )

    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
        raise_structural(
            StructuralErrorCode.CSV_JSON_CELL_MALFORMED, artifact,
            f"cell is neither JSON nor a recoverable Python literal list: {exc}",
            location=location, column=column, row=row,
        )
        raise  # pragma: no cover

    if not isinstance(value, list):
        raise_structural(
            StructuralErrorCode.CSV_JSON_CELL_MALFORMED, artifact,
            f"legacy list cell decoded to {type(value).__name__}, not a list",
            location=location, column=column, row=row,
        )

    for index, element in enumerate(value):
        # `bool` is excluded explicitly: it is a subclass of `int`, and these
        # four columns carry metric and strategy names only.
        if not isinstance(element, str):
            raise_structural(
                StructuralErrorCode.CSV_JSON_CELL_MALFORMED, artifact,
                f"legacy list element {index} is {type(element).__name__}, not a string",
                location=f"{location}, element {index}", column=column, row=row,
            )

    # Round-trip through canonical JSON so downstream comparison, hashing, and
    # reconciliation observe exactly the shape a 1.4 run would have produced.
    decoded: list[str] = json.loads(json.dumps(value))
    return LegacyCellRecovery(
        artifact=artifact,
        column=column,
        row=row,
        declared_artifact_schema_version=declared_version,
        raw=raw,
        decoded=decoded,
    )
