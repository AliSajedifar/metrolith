"""Typed structural error taxonomy for ArchLens artifact reading.

This module is **semantics free**. It describes how an artifact failed to parse
or failed to satisfy its declared structure. It never describes a measurement,
a metric value, a repository status, or any scientific conclusion.

Both production CLI handlers and the independent semantic validator may import
this module. See plan section 3.2: shared code may contain structural behavior
only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StructuralErrorCode(str, Enum):
    """Every way a run artifact can fail structural admission.

    The value is the stable machine string written into reports. Adding a member
    is a schema-visible change and must be reflected in the documentation.
    """

    # Location and admission
    ARTIFACT_NOT_ALLOWLISTED = "artifact_not_allowlisted"
    PATH_ESCAPES_RUN_DIRECTORY = "path_escapes_run_directory"
    PATH_IS_SYMLINK = "path_is_symlink"
    PATH_NOT_A_REGULAR_FILE = "path_not_a_regular_file"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_UNREADABLE = "artifact_unreadable"

    # Bounds
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    ROW_COUNT_EXCEEDED = "row_count_exceeded"

    # Encoding
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    INVALID_UTF8 = "invalid_utf8"
    UNEXPECTED_BYTE_ORDER_MARK = "unexpected_byte_order_mark"

    # JSON structure
    JSON_MALFORMED = "json_malformed"
    JSON_DUPLICATE_KEY = "json_duplicate_key"
    JSON_NON_STANDARD_NUMBER = "json_non_standard_number"
    JSON_ROOT_TYPE_UNEXPECTED = "json_root_type_unexpected"

    # CSV structure
    CSV_MISSING_HEADER = "csv_missing_header"
    CSV_DUPLICATE_HEADER = "csv_duplicate_header"
    CSV_EMPTY_HEADER_FIELD = "csv_empty_header_field"
    CSV_MISSING_REQUIRED_COLUMN = "csv_missing_required_column"
    CSV_UNKNOWN_COLUMN = "csv_unknown_column"
    CSV_RAGGED_ROW = "csv_ragged_row"
    CSV_CELL_TYPE_INVALID = "csv_cell_type_invalid"
    CSV_JSON_CELL_MALFORMED = "csv_json_cell_malformed"

    # Writer strictness
    WRITER_UNKNOWN_FIELD = "writer_unknown_field"
    WRITER_MISSING_FIELD = "writer_missing_field"

    # Schema conformance
    SCHEMA_VIOLATION = "schema_violation"
    SCHEMA_NOT_FOUND = "schema_not_found"
    SCHEMA_INVALID = "schema_invalid"
    # Raised when `jsonschema` is not installed. Structural schema validation
    # fails loudly rather than degrading to an unchecked pass (decision D-4).
    SCHEMA_DEPENDENCY_UNAVAILABLE = "schema_dependency_unavailable"

    # Version compatibility
    VERSION_UNDECLARED = "version_undeclared"
    VERSION_UNPARSEABLE = "version_unparseable"
    VERSION_UNSUPPORTED = "version_unsupported"
    VERSION_FUTURE_UNSUPPORTED = "version_future_unsupported"

    # Lifecycle
    LIFECYCLE_INDETERMINATE = "lifecycle_indeterminate"
    LIFECYCLE_VARIANT_AMBIGUOUS = "lifecycle_variant_ambiguous"
    TERMINAL_DOCUMENT_DOES_NOT_RECONCILE = "terminal_document_does_not_reconcile"


@dataclass(frozen=True)
class StructuralError:
    """One structural failure, located as precisely as the evidence allows.

    ``artifact`` is always a run-relative POSIX path so that no absolute host
    path leaks into a report (plan section 22). ``location`` is a free-form but
    stable pointer such as ``"row 14, column 'commit_sha'"`` or a JSON pointer.
    """

    code: StructuralErrorCode
    artifact: str
    message: str
    location: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "artifact": self.artifact,
            "message": self.message,
        }
        if self.location is not None:
            payload["location"] = self.location
        if self.detail:
            payload["detail"] = dict(sorted(self.detail.items()))
        return payload

    def __str__(self) -> str:
        where = f" at {self.location}" if self.location else ""
        return f"[{self.code.value}] {self.artifact}{where}: {self.message}"


class ArtifactStructureError(Exception):
    """Raised when strict structural admission fails.

    Carries the typed error so callers can branch on ``code`` instead of parsing
    a message string.
    """

    def __init__(self, error: StructuralError) -> None:
        super().__init__(str(error))
        self.error = error

    @property
    def code(self) -> StructuralErrorCode:
        return self.error.code

    def as_dict(self) -> dict[str, Any]:
        return self.error.as_dict()


def raise_structural(
    code: StructuralErrorCode,
    artifact: str,
    message: str,
    location: str | None = None,
    **detail: Any,
) -> "ArtifactStructureError":
    """Construct and raise one typed structural error.

    Returns the exception type purely so callers may write ``raise
    raise_structural(...)`` and keep static analyzers satisfied about flow.
    """
    raise ArtifactStructureError(
        StructuralError(
            code=code, artifact=artifact, message=message, location=location, detail=detail
        )
    )
