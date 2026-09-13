"""Strict JSON and JSON Lines decoding for ArchLens run artifacts.

Strictness beyond the standard library (plan section 7.5):

* **Duplicate object keys are rejected.** ``json.loads`` silently keeps the last
  occurrence, which lets a tampered artifact carry two different values for the
  same field and present whichever a given reader happens to observe.
* **NaN, Infinity and -Infinity are rejected.** These are Python extensions, not
  RFC 8259 JSON, and they are not representable in a portable artifact.
* **Encoding is pinned to UTF-8 without a byte-order mark**, matching RFC 8259.
* **The root type is checked**, so a bare scalar cannot masquerade as a document.

This module is semantics free and is shared by production readers and the
independent semantic validator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from archlens_json import (
    ARTIFACT_JSON_LIMITS,
    StrictJsonError,
    decode_utf8,
    dumps_strict,
    load_file,
    loads_text,
    read_bounded_bytes,
)

from .errors import StructuralErrorCode, raise_structural


_ERROR_CODES: dict[str, StructuralErrorCode] = {
    "artifact_unreadable": StructuralErrorCode.ARTIFACT_UNREADABLE,
    "artifact_too_large": StructuralErrorCode.ARTIFACT_TOO_LARGE,
    "unsupported_encoding": StructuralErrorCode.UNSUPPORTED_ENCODING,
    "invalid_utf8": StructuralErrorCode.INVALID_UTF8,
    "unexpected_byte_order_mark": StructuralErrorCode.UNEXPECTED_BYTE_ORDER_MARK,
    "json_malformed": StructuralErrorCode.JSON_MALFORMED,
    "json_duplicate_key": StructuralErrorCode.JSON_DUPLICATE_KEY,
    "json_non_standard_number": StructuralErrorCode.JSON_NON_STANDARD_NUMBER,
    "json_root_type_unexpected": StructuralErrorCode.JSON_ROOT_TYPE_UNEXPECTED,
    # Depth, collection, total-item, and individual-item limits are all
    # structural size admission failures in the existing report taxonomy.
    "json_depth_exceeded": StructuralErrorCode.ARTIFACT_TOO_LARGE,
    "json_collection_size_exceeded": StructuralErrorCode.ARTIFACT_TOO_LARGE,
    "json_item_count_exceeded": StructuralErrorCode.ARTIFACT_TOO_LARGE,
    "json_item_too_large": StructuralErrorCode.ARTIFACT_TOO_LARGE,
}


def _translate(error: StrictJsonError, artifact: str) -> None:
    raise_structural(
        _ERROR_CODES.get(error.code, StructuralErrorCode.JSON_MALFORMED),
        artifact,
        error.message,
        location=error.location,
        strict_json_code=error.code,
        **error.detail,
    )


def decode_text(data: bytes, artifact: str) -> str:
    """Decode artifact bytes as UTF-8, refusing byte-order marks and bad bytes."""
    try:
        return decode_utf8(
            data, source=artifact, limits=ARTIFACT_JSON_LIMITS
        )
    except StrictJsonError as error:
        _translate(error, artifact)
        raise  # pragma: no cover


def loads(text: str, artifact: str, *, expect: type | tuple[type, ...] = dict) -> Any:
    """Parse one JSON document from text under the strict rules above."""
    try:
        return loads_text(
            text, source=artifact, limits=ARTIFACT_JSON_LIMITS, expect=expect
        )
    except StrictJsonError as error:
        _translate(error, artifact)
        raise  # pragma: no cover


def read_bytes(path: Path, artifact: str) -> bytes:
    try:
        return read_bounded_bytes(
            path, source=artifact, limits=ARTIFACT_JSON_LIMITS
        )
    except StrictJsonError as error:
        _translate(error, artifact)
        raise  # pragma: no cover


def load_document(
    path: Path, artifact: str, *, expect: type | tuple[type, ...] = dict
) -> Any:
    """Read and strictly parse one JSON artifact from disk."""
    try:
        return load_file(
            path, source=artifact, limits=ARTIFACT_JSON_LIMITS, expect=expect
        )
    except StrictJsonError as error:
        _translate(error, artifact)
        raise  # pragma: no cover


def iter_json_lines(
    path: Path, artifact: str, *, max_rows: int | None = None
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Stream a JSON Lines artifact, yielding ``(line_number, object)``.

    Streaming rather than materializing keeps ``logs/run.jsonl`` affordable for
    large cohorts. Blank lines are skipped; a trailing newline is normal and is
    not an error. Every non-blank line must be a JSON object.
    """
    text = decode_text(read_bytes(path, artifact), artifact)
    count = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        count += 1
        if max_rows is not None and count > max_rows:
            raise_structural(
                StructuralErrorCode.ROW_COUNT_EXCEEDED, artifact,
                f"event count exceeds the {max_rows} row bound",
                location=f"line {number}", max_rows=max_rows,
            )
        try:
            value = loads_text(
                line,
                source=f"{artifact}:{number}",
                limits=ARTIFACT_JSON_LIMITS,
                expect=dict,
            )
        except StrictJsonError as error:
            _translate(error, artifact)
            raise  # pragma: no cover
        yield number, value


def dumps_canonical(value: Any) -> str:
    """Serialize deterministically for hashing and byte-identical comparison.

    Sorted keys, no insignificant whitespace, ``ensure_ascii`` disabled so that
    the encoded form matches the artifact's UTF-8 text.
    """
    try:
        return dumps_strict(
            value,
            source="canonical JSON",
            limits=ARTIFACT_JSON_LIMITS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except StrictJsonError as error:
        _translate(error, "canonical JSON")
        raise  # pragma: no cover
