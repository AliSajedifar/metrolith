"""Strict CSV decoding and encoding for ArchLens tabular artifacts.

Strictness beyond the standard library (plan section 7.5):

* **Duplicate header fields are rejected**, since ``csv.DictReader`` silently
  keeps only the last column of a repeated name.
* **Required and optional columns are validated** against a declared contract,
  and unknown columns are refused by default.
* **Typed conversion happens in exactly one place**, so no downstream feature
  reinvents "is this cell a number".
* **Empty, JSON null, zero, false, and unavailable are five distinct outcomes.**
  An empty cell decodes to ``None``; the four-character cell ``null`` inside a
  JSON-typed column decodes to JSON null; ``0`` decodes to integer zero;
  ``False`` decodes to boolean false. None of these collapse into each other.
* **Quoted newlines are preserved** by reading through the ``csv`` module with
  ``newline=""`` rather than splitting lines manually.
* **Malformed JSON-valued cells are rejected** rather than passed through as
  opaque text.
* **The writer refuses unknown and missing fields** instead of using
  ``extrasaction="ignore"``.

Boolean and JSON literal forms below are taken from observed ArchLens 3.4.0
artifacts, not assumed. Metric CSVs emit Python ``str(bool)`` form
(``True``/``False``); input-shaped CSVs emit ``TRUE``/``FALSE``. Both are
accepted and any other spelling is an error.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from .compatibility import parse_version
from .errors import StructuralErrorCode, raise_structural
from .legacy_cells import (
    LegacyCellRecovery,
    decode_legacy_list_cell,
    is_legacy_list_column,
    legacy_recovery_applies,
)
from .strict_json import decode_text, read_bytes

# Some ArchLens rows carry very large embedded JSON payloads.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

TRUE_LITERALS = ("True", "TRUE", "true")
FALSE_LITERALS = ("False", "FALSE", "false")


class _JsonNull:
    """Sentinel for a JSON-typed cell whose content is the literal ``null``.

    Observed ArchLens artifacts use both forms in the same table: ``catalog.csv``
    writes an empty cell when a value was never produced, and the four-character
    cell ``null`` when a JSON payload exists and is explicitly null. Decoding
    both to Python ``None`` would collapse "unavailable" into "present and null",
    which plan section 7.5 forbids.

    Only a **top-level** null cell becomes this sentinel. A null nested inside a
    JSON object or array stays an ordinary Python ``None``, because at that depth
    the distinction belongs to the payload rather than to the table.
    """

    _instance: "_JsonNull | None" = None

    def __new__(cls) -> "_JsonNull":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "JSON_NULL"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return other is self

    def __hash__(self) -> int:
        return hash("<archlens-json-null>")


JSON_NULL = _JsonNull()


def is_unavailable(value: Any) -> bool:
    """True when a decoded cell was empty, meaning no value was produced."""
    return value is None


def is_json_null(value: Any) -> bool:
    """True when a decoded cell held the literal JSON ``null``."""
    return value is JSON_NULL


class CellType(str, Enum):
    """Declared type of one CSV column."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    ENUM = "enum"


@dataclass(frozen=True)
class ColumnSpec:
    """Contract for one column.

    ``nullable`` controls whether an empty cell is legal and decodes to ``None``.
    ``allow_empty_string`` distinguishes a genuinely empty string value from an
    absent value for string columns; it is false by default so that emptiness
    means "unavailable" unless a contract says otherwise.

    ``legacy_python_repr_until`` opts one JSON column into the finding F-1
    version-boundary recovery described in :mod:`.legacy_cells`. The decoder is
    unreachable unless a contract sets it, and even then it runs only after
    canonical JSON parsing has failed on an artifact declaring a version below
    the stated boundary.
    """

    name: str
    cell_type: CellType = CellType.STRING
    required: bool = True
    nullable: bool = True
    allow_empty_string: bool = False
    enum: tuple[str, ...] = ()
    legacy_python_repr_until: str | None = None

    def __post_init__(self) -> None:
        if self.cell_type is CellType.ENUM and not self.enum:
            raise ValueError(f"column {self.name!r} is an enum but declares no members")
        if self.legacy_python_repr_until is not None:
            if self.cell_type is not CellType.JSON:
                raise ValueError(
                    f"column {self.name!r} declares legacy_python_repr_until but is "
                    f"not a JSON column"
                )
            if parse_version(self.legacy_python_repr_until) is None:
                raise ValueError(
                    f"column {self.name!r} declares an unparseable "
                    f"legacy_python_repr_until {self.legacy_python_repr_until!r}"
                )


@dataclass(frozen=True)
class TableContract:
    """Declared shape of one tabular artifact."""

    name: str
    columns: tuple[ColumnSpec, ...]
    allow_unknown_columns: bool = False
    key_columns: tuple[str, ...] = ()
    encoding: str = "utf-8"
    _by_name: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        seen: dict[str, ColumnSpec] = {}
        for column in self.columns:
            if column.name in seen:
                raise ValueError(f"contract {self.name!r} repeats column {column.name!r}")
            seen[column.name] = column
        for key in self.key_columns:
            if key not in seen:
                raise ValueError(f"contract {self.name!r} keys on unknown column {key!r}")
        object.__setattr__(self, "_by_name", seen)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def required_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.required)

    def column(self, name: str) -> ColumnSpec | None:
        return self._by_name.get(name)


def _convert(
    raw: str | None,
    column: ColumnSpec,
    artifact: str,
    row_number: int,
    *,
    declared_version: str | None = None,
    recoveries: list[LegacyCellRecovery] | None = None,
) -> Any:
    location = f"row {row_number}, column {column.name!r}"

    if raw is None:
        if column.nullable:
            return None
        raise_structural(
            StructuralErrorCode.CSV_RAGGED_ROW, artifact,
            f"row is missing a value for non-nullable column {column.name!r}",
            location=location, column=column.name, row=row_number,
        )

    if raw == "":
        if column.cell_type is CellType.STRING and column.allow_empty_string:
            return ""
        if column.nullable:
            return None
        raise_structural(
            StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
            f"empty cell in non-nullable column {column.name!r}",
            location=location, column=column.name, row=row_number,
        )

    if column.cell_type is CellType.STRING:
        return raw

    if column.cell_type is CellType.ENUM:
        if raw not in column.enum:
            raise_structural(
                StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
                f"value {raw!r} is not one of {list(column.enum)}",
                location=location, column=column.name, row=row_number, value=raw,
            )
        return raw

    if column.cell_type is CellType.BOOLEAN:
        if raw in TRUE_LITERALS:
            return True
        if raw in FALSE_LITERALS:
            return False
        raise_structural(
            StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
            f"value {raw!r} is not a recognized boolean literal",
            location=location, column=column.name, row=row_number, value=raw,
        )

    if column.cell_type is CellType.INTEGER:
        # int() would accept "  7 ", underscores, and unicode digits. Refuse
        # anything that is not a plain optionally-signed decimal integer.
        candidate = raw[1:] if raw[:1] in "+-" else raw
        if not candidate.isascii() or not candidate.isdigit():
            raise_structural(
                StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
                f"value {raw!r} is not a decimal integer",
                location=location, column=column.name, row=row_number, value=raw,
            )
        return int(raw)

    if column.cell_type is CellType.NUMBER:
        lowered = raw.strip().lower()
        if lowered in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
            raise_structural(
                StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
                f"non-finite numeric value {raw!r} is not permitted",
                location=location, column=column.name, row=row_number, value=raw,
            )
        try:
            return float(raw)
        except ValueError:
            raise_structural(
                StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
                f"value {raw!r} is not a finite number",
                location=location, column=column.name, row=row_number, value=raw,
            )

    if column.cell_type is CellType.JSON:
        # The canonical path is always attempted first, for every artifact
        # version. On success no recovery is considered and no diagnostic is
        # emitted, so Artifact >= 1.4 parsing is bit-for-bit unchanged.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            if _legacy_recovery_reachable(column, artifact, declared_version):
                recovery = decode_legacy_list_cell(
                    raw,
                    artifact=artifact,
                    column=column.name,
                    row=row_number,
                    declared_version=declared_version,
                )
                if recoveries is not None:
                    recoveries.append(recovery)
                return recovery.decoded
            raise_structural(
                StructuralErrorCode.CSV_JSON_CELL_MALFORMED, artifact,
                f"JSON-valued cell is malformed: {exc.msg}",
                location=location, column=column.name, row=row_number,
            )
        # A top-level JSON null is "present and null", not "unavailable".
        return JSON_NULL if parsed is None else parsed

    raise_structural(  # pragma: no cover - exhaustive above
        StructuralErrorCode.CSV_CELL_TYPE_INVALID, artifact,
        f"unhandled cell type {column.cell_type!r}", location=location,
    )


def _legacy_recovery_reachable(
    column: ColumnSpec, artifact: str, declared_version: str | None
) -> bool:
    """Whether all three finding F-1 gates hold simultaneously.

    Every gate must pass: the contract opted this column in, the artifact and
    column are in the closed allowlist, and the declared version parses and sits
    below the boundary. Canonical parsing having already failed is the caller's
    precondition.
    """
    if column.legacy_python_repr_until is None:
        return False
    if not is_legacy_list_column(artifact, column.name):
        return False
    boundary = parse_version(column.legacy_python_repr_until)
    if boundary is None:  # pragma: no cover - ColumnSpec rejects this at construction
        return False
    return legacy_recovery_applies(parse_version(declared_version), boundary)


def validate_header(
    header: Sequence[str] | None, contract: TableContract, artifact: str
) -> tuple[str, ...]:
    """Check one header row against the contract and return it normalized."""
    if header is None:
        raise_structural(
            StructuralErrorCode.CSV_MISSING_HEADER, artifact,
            "artifact has no header row",
        )
    seen: set[str] = set()
    for index, name in enumerate(header):
        if name == "":
            raise_structural(
                StructuralErrorCode.CSV_EMPTY_HEADER_FIELD, artifact,
                f"header field {index} is empty",
                location=f"header column {index}",
            )
        if name in seen:
            raise_structural(
                StructuralErrorCode.CSV_DUPLICATE_HEADER, artifact,
                f"duplicate header field {name!r}",
                location=f"header column {index}", column=name,
            )
        seen.add(name)

    missing = [name for name in contract.required_names if name not in seen]
    if missing:
        raise_structural(
            StructuralErrorCode.CSV_MISSING_REQUIRED_COLUMN, artifact,
            f"missing required column(s): {', '.join(missing)}",
            missing=missing,
        )
    if not contract.allow_unknown_columns:
        unknown = [name for name in header if contract.column(name) is None]
        if unknown:
            raise_structural(
                StructuralErrorCode.CSV_UNKNOWN_COLUMN, artifact,
                f"unknown column(s) not in the {contract.name} contract: {', '.join(unknown)}",
                unknown=unknown,
            )
    return tuple(header)


def read_rows(
    path: Path,
    artifact: str,
    contract: TableContract,
    *,
    max_rows: int | None = None,
    declared_version: str | None = None,
    recoveries: list[LegacyCellRecovery] | None = None,
) -> list[dict[str, Any]]:
    """Read and type one CSV artifact in full.

    Quoted newlines survive because parsing runs through ``csv.reader`` over a
    ``StringIO`` opened with ``newline=""``.

    ``declared_version`` is the run's ``artifact_schema_version``. It is the
    third gate on finding F-1 recovery; omitting it makes recovery unreachable,
    which is the safe default.
    """
    return list(
        iter_rows(
            path, artifact, contract,
            max_rows=max_rows,
            declared_version=declared_version,
            recoveries=recoveries,
        )
    )


def iter_rows(
    path: Path,
    artifact: str,
    contract: TableContract,
    *,
    max_rows: int | None = None,
    declared_version: str | None = None,
    recoveries: list[LegacyCellRecovery] | None = None,
):
    """Stream typed rows so large ledgers never require full materialization."""
    if contract.encoding.lower().replace("_", "-") != "utf-8":
        raise_structural(
            StructuralErrorCode.UNSUPPORTED_ENCODING, artifact,
            f"contract {contract.name!r} declares unsupported encoding {contract.encoding!r}",
        )
    text = decode_text(read_bytes(path, artifact), artifact)
    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream)
    try:
        header = next(reader)
    except StopIteration:
        header = None
    names = validate_header(header, contract, artifact)

    for row_number, raw_row in enumerate(reader, start=2):
        if max_rows is not None and row_number - 1 > max_rows:
            raise_structural(
                StructuralErrorCode.ROW_COUNT_EXCEEDED, artifact,
                f"row count exceeds the {max_rows} row bound",
                location=f"row {row_number}", max_rows=max_rows,
            )
        # A row must have exactly as many fields as the header declares.
        #
        # Only over-long rows used to be refused; a short row was padded with
        # `None`, so its missing cells decoded to "unavailable" for every
        # nullable column. That makes a truncated artifact — a partial write, a
        # cut-off download — indistinguishable from one that genuinely recorded
        # no value, which is the exact conflation the null/zero/empty rules
        # elsewhere in this module exist to prevent. Every ArchLens writer emits
        # a full row, so a short row is damage, not a valid encoding.
        if len(raw_row) != len(names):
            raise_structural(
                StructuralErrorCode.CSV_RAGGED_ROW, artifact,
                f"row has {len(raw_row)} fields, header declares {len(names)}",
                location=f"row {row_number}", row=row_number,
            )
        record: dict[str, Any] = {}
        for index, name in enumerate(names):
            column = contract.column(name)
            raw = raw_row[index] if index < len(raw_row) else None
            if column is None:
                record[name] = raw
                continue
            record[name] = _convert(
                raw, column, artifact, row_number,
                declared_version=declared_version,
                recoveries=recoveries,
            )
        yield record


def write_rows(
    path: Path,
    contract: TableContract,
    rows: Iterable[dict[str, Any]],
    *,
    artifact: str | None = None,
) -> Path:
    """Write a CSV artifact, refusing unknown or missing fields.

    ``csv.DictWriter`` is used with the default ``extrasaction="raise"``, and a
    pre-check reports the offending field names explicitly rather than as a
    bare ``ValueError``.
    """
    label = artifact or contract.name
    field_names = contract.field_names
    known = set(field_names)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=field_names, extrasaction="raise",
                            lineterminator="\n")
    writer.writeheader()
    for row_number, row in enumerate(rows, start=2):
        unknown = sorted(set(row) - known)
        if unknown:
            raise_structural(
                StructuralErrorCode.WRITER_UNKNOWN_FIELD, label,
                f"refusing to write unknown field(s): {', '.join(unknown)}",
                location=f"row {row_number}", unknown=unknown,
            )
        missing = [name for name in contract.required_names if name not in row]
        if missing:
            raise_structural(
                StructuralErrorCode.WRITER_MISSING_FIELD, label,
                f"row is missing required field(s): {', '.join(missing)}",
                location=f"row {row_number}", missing=missing,
            )
        writer.writerow({name: _encode_cell(row.get(name)) for name in field_names})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    return target


def _encode_cell(value: Any) -> str:
    """Render one typed value using the observed ArchLens 3.4.0 conventions.

    The inverse of :func:`_convert` for the five distinguished outcomes:
    ``None`` becomes an empty cell, :data:`JSON_NULL` becomes ``null``, and
    zero, false, and ordinary values keep their own spellings.
    """
    if value is None:
        return ""
    if value is JSON_NULL:
        return "null"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
