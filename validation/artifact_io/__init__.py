"""Structural artifact input/output for ArchLens run directories.

**This package contains structural behavior only** (plan section 3.2):
allowlisted artifact discovery, strict decoding, size and path bounds, typed CSV
conversion, lifecycle classification, immutable containers, and package-resource
discovery.

It contains no measurement semantics. It never computes source inclusion, a
detected language, a parser outcome, a metric value, a per-metric status, a
repository status, an expected-language-family status, or a partial origin.

Both sides may import this package:

* production CLI handlers, and
* ``validation.scripts.validate_outputs``, the independent semantic validator.

Neither may import the other. A mechanical import-edge test enforces that rule,
so this package must never grow a dependency on ``modules`` or on
``validation.scripts``.
"""

from __future__ import annotations

from .compatibility import (
    CompatibilityState,
    CompatibilityVerdict,
    RepositoryDocumentVariant,
    RunLifecycle,
    check_inventory_pairing,
    classify_artifact_schema,
    classify_lifecycle,
    classify_repository_document,
    parse_version,
    reconciles_with_analysis,
)
from .errors import (
    ArtifactStructureError,
    StructuralError,
    StructuralErrorCode,
    raise_structural,
)
from .legacy_cells import (
    LEGACY_LIST_COLUMNS,
    LEGACY_REPR_BOUNDARY,
    MAX_LEGACY_CELL_CHARACTERS,
    LegacyCellRecovery,
    decode_legacy_list_cell,
    is_legacy_list_column,
    legacy_recovery_applies,
)
from .paths import (
    ARTIFACT_SPECS,
    ArtifactKind,
    ArtifactSpec,
    Authority,
    discover_family,
    normalize_relative,
    resolve_artifact,
    spec_for_name,
    spec_for_relative_path,
)
from .strict_csv import (
    JSON_NULL,
    CellType,
    ColumnSpec,
    TableContract,
    is_json_null,
    is_unavailable,
    iter_rows,
    read_rows,
    validate_header,
    write_rows,
)
from .strict_json import (
    decode_text,
    dumps_canonical,
    iter_json_lines,
    load_document,
    loads,
)

__all__ = [
    "ARTIFACT_SPECS",
    "JSON_NULL",
    "LEGACY_LIST_COLUMNS",
    "LEGACY_REPR_BOUNDARY",
    "MAX_LEGACY_CELL_CHARACTERS",
    "ArtifactKind",
    "ArtifactSpec",
    "ArtifactStructureError",
    "Authority",
    "CellType",
    "ColumnSpec",
    "CompatibilityState",
    "CompatibilityVerdict",
    "LegacyCellRecovery",
    "RepositoryDocumentVariant",
    "RunLifecycle",
    "StructuralError",
    "StructuralErrorCode",
    "TableContract",
    "check_inventory_pairing",
    "classify_artifact_schema",
    "classify_lifecycle",
    "classify_repository_document",
    "decode_legacy_list_cell",
    "decode_text",
    "discover_family",
    "dumps_canonical",
    "is_json_null",
    "is_legacy_list_column",
    "is_unavailable",
    "iter_json_lines",
    "iter_rows",
    "legacy_recovery_applies",
    "load_document",
    "loads",
    "normalize_relative",
    "parse_version",
    "raise_structural",
    "read_rows",
    "reconciles_with_analysis",
    "resolve_artifact",
    "spec_for_name",
    "spec_for_relative_path",
    "validate_header",
    "write_rows",
]
