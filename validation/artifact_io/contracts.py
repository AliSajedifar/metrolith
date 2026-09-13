"""Table contracts derived from the packaged row schemas.

The column list for each tabular artifact exists in exactly one place: the
packaged JSON Schema. Retyping 79 error columns and 48 recovery columns beside
the schema would guarantee drift, and a drifted contract fails in the worst
possible way — it silently stops checking a column instead of erroring.

Each schema property carries ``x-archlens-cell-type`` and
``x-archlens-nullable``, emitted by ``validation/scripts/derive_tabular_schemas.py``
for generated schemas and written by hand for the rest. This module maps those
annotations onto :class:`~validation.artifact_io.strict_csv.ColumnSpec`.

The finding F-1 legacy opt-in is applied here, and only to the four columns in
the closed allowlist. It is deliberately not expressed in the schema: the schema
describes the Artifact 1.5 native form, where those cells are ordinary JSON.
"""

from __future__ import annotations

from functools import lru_cache

from .compatibility import parse_version
from .legacy_cells import LEGACY_LIST_COLUMNS, LEGACY_REPR_BOUNDARY
from .schema_store import load_schema
from .strict_csv import CellType, ColumnSpec, TableContract

# artifact filename -> (schema name, key columns)
TABULAR_ARTIFACTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "catalog.csv": ("catalog_row", ("repository_url",)),
    "sheet_metrics.csv": ("sheet_metrics_row", ("repository_url",)),
    "language_metrics.csv": ("language_metrics_row", ("repository_url", "language")),
    # Artifact Schema 1.11. Reuses `sheet_metrics_row` deliberately: the
    # unrestricted comparison surface is an exact filtered SUBSET of
    # `sheet_metrics.csv`, so giving it a duplicate row schema would create two
    # contracts for one row shape and guarantee they eventually disagree.
    "repository_level_metrics.csv": ("sheet_metrics_row", ("repository_url",)),
    "errors.csv": ("errors_row", ("repository_url", "error_type")),
    "recoveries.csv": ("recoveries_row", ("repository_url", "file_path")),
    "repositories_frozen.csv": ("frozen_input_row", ("url",)),
    "retry_failed_or_partial.csv": ("retry_input_row", ("url",)),
    "normalized_input.csv": (
        "normalized_input_row",
        ("source_input_file_id", "original_row_index"),
    ),
    "contributions.csv": (
        "contribution_row",
        ("repository_url", "relative_path", "content_sha256"),
    ),
    # Artifact Schema 1.9. `callable_row_id` alone identifies a row within one
    # artifact -- that is exactly what the identity was built to guarantee -- so
    # it is the whole key.
    "callables.csv": ("callable_row", ("callable_row_id",)),
}

#: Directory families whose members share one row contract.
TABULAR_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "contributions/": TABULAR_ARTIFACTS["contributions.csv"],
    "callables/": TABULAR_ARTIFACTS["callables.csv"],
}

_CELL_TYPES = {
    "string": CellType.STRING,
    "integer": CellType.INTEGER,
    "number": CellType.NUMBER,
    "boolean": CellType.BOOLEAN,
    "json": CellType.JSON,
}


def _cell_type(name: str, schema: dict) -> tuple[CellType, bool]:
    """Read the contract annotations, falling back to the JSON `type` keyword."""
    annotated = schema.get("x-archlens-cell-type")
    if annotated in _CELL_TYPES:
        return _CELL_TYPES[annotated], bool(schema.get("x-archlens-nullable", True))

    declared = schema.get("type")
    if declared is None:
        # An enum without a `type` constrains the value set, not the encoding.
        # Every ArchLens enum is spelled with strings, so treating one as a JSON
        # cell would try to parse `verified` as JSON and reject it.
        members = schema.get("enum")
        if isinstance(members, list) and members:
            concrete = [item for item in members if item is not None]
            if concrete and all(isinstance(item, str) for item in concrete):
                return CellType.STRING, len(concrete) != len(members)
        # No `type`, no annotation, no enum: the column may hold any JSON value.
        return CellType.JSON, True
    types = [declared] if isinstance(declared, str) else list(declared)
    nullable = "null" in types
    concrete = [item for item in types if item != "null"]
    if len(concrete) == 1 and concrete[0] in _CELL_TYPES:
        return _CELL_TYPES[concrete[0]], nullable
    # A union of concrete types cannot be enforced per-cell; treat as string so
    # the value survives unchanged rather than being coerced into one branch.
    return CellType.STRING, nullable


@lru_cache(maxsize=None)
def contract_for(artifact: str, declared_version: str | None = None) -> TableContract:
    """Build the :class:`TableContract` for one tabular artifact filename.

    ``declared_version`` is the run's ``artifact_schema_version``. Column sets
    grew across artifact versions — ``errors.csv`` carries 79 columns at 1.4 and
    fewer at 1.3 — so a column introduced *after* the artifact under inspection
    is not required of it. Each schema property records its introduction version
    in ``x-archlens-since-artifact-schema``, derived from preserved runs.

    Without a declared version every column stays required, which is the strict
    reading appropriate to the native target. This does not weaken the check for
    a modern artifact: a 1.5 run is still held to the full column set.
    """
    try:
        schema_name, keys = TABULAR_ARTIFACTS[artifact]
    except KeyError:
        # A partitioned ledger member (`contributions/00000.csv`,
        # `callables/00000.csv`) carries the same row contract as its
        # single-file form. Resolving by prefix keeps the partitioned and
        # unpartitioned containers held to one contract rather than leaving the
        # partitioned path with none.
        for prefix, (schema_name, keys) in TABULAR_FAMILIES.items():
            if artifact.startswith(prefix) and artifact.endswith(".csv"):
                break
        else:
            raise KeyError(f"no table contract for artifact {artifact!r}") from None

    schema = load_schema(schema_name)
    properties: dict = schema.get("properties", {})
    declared_required = set(schema.get("required", ()))
    reading = parse_version(declared_version) if declared_version else None

    columns: list[ColumnSpec] = []
    for name in sorted(properties):
        spec = properties[name]
        cell_type, nullable = _cell_type(name, spec)

        required = name in declared_required
        if required and reading is not None:
            since = parse_version(spec.get("x-archlens-since-artifact-schema", "1.0.0"))
            if since is not None and since > reading:
                required = False

        legacy = (
            LEGACY_REPR_BOUNDARY_TEXT
            if (artifact, name) in LEGACY_LIST_COLUMNS and cell_type is CellType.JSON
            else None
        )
        columns.append(
            ColumnSpec(
                name=name,
                cell_type=cell_type,
                required=required,
                nullable=nullable,
                legacy_python_repr_until=legacy,
            )
        )

    return TableContract(
        name=artifact,
        columns=tuple(columns),
        allow_unknown_columns=bool(schema.get("additionalProperties", False)),
        key_columns=tuple(key for key in keys if key in properties),
    )


LEGACY_REPR_BOUNDARY_TEXT = ".".join(str(part) for part in LEGACY_REPR_BOUNDARY)


def known_artifacts() -> tuple[str, ...]:
    return tuple(sorted(TABULAR_ARTIFACTS))
