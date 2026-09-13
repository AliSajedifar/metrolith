"""Neutral bounded JSON input and output for external ArchLens documents.

This module deliberately has no dependency on Policy, Ratchet, CLI, or the
validation package.  Those domains translate :class:`StrictJsonError` into
their own public error types while sharing one byte-exact JSON boundary.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrictJsonLimits:
    """Resource limits applied before and after JSON decoding."""

    max_bytes: int
    max_depth: int
    max_collection_items: int
    max_total_items: int
    max_string_bytes: int


POLICY_JSON_LIMITS = StrictJsonLimits(
    max_bytes=4 * 1024 * 1024,
    max_depth=64,
    max_collection_items=20_000,
    max_total_items=100_000,
    max_string_bytes=1 * 1024 * 1024,
)
BASELINE_JSON_LIMITS = StrictJsonLimits(
    max_bytes=64 * 1024 * 1024,
    max_depth=64,
    max_collection_items=1_000_000,
    max_total_items=2_000_000,
    max_string_bytes=16 * 1024 * 1024,
)
EVIDENCE_JSON_LIMITS = StrictJsonLimits(
    max_bytes=512 * 1024 * 1024,
    max_depth=64,
    max_collection_items=5_000_000,
    max_total_items=10_000_000,
    max_string_bytes=64 * 1024 * 1024,
)
CHECK_JSON_LIMITS = StrictJsonLimits(
    max_bytes=512 * 1024 * 1024,
    max_depth=64,
    max_collection_items=5_000_000,
    max_total_items=10_000_000,
    max_string_bytes=64 * 1024 * 1024,
)
SARIF_JSON_LIMITS = CHECK_JSON_LIMITS
ARTIFACT_JSON_LIMITS = EVIDENCE_JSON_LIMITS


class StrictJsonError(ValueError):
    """One bounded JSON admission or serialization failure."""

    def __init__(
        self,
        code: str,
        source: str,
        message: str,
        *,
        location: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.message = message
        self.location = location
        self.detail = detail or {}

    def __str__(self) -> str:
        where = f" at {self.location}" if self.location else ""
        return f"[{self.code}] {self.source}{where}: {self.message}"


_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"
_UTF32_LE_BOM = b"\xff\xfe\x00\x00"
_UTF32_BE_BOM = b"\x00\x00\xfe\xff"


def read_bounded_bytes(
    path: Path | str,
    *,
    source: str | None = None,
    limits: StrictJsonLimits,
) -> bytes:
    """Read a regular external JSON payload without unbounded allocation."""

    candidate = Path(path)
    label = source or str(candidate)
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise StrictJsonError(
            "artifact_unreadable", label, f"cannot stat JSON input: {exc}"
        ) from exc
    if size > limits.max_bytes:
        raise StrictJsonError(
            "artifact_too_large",
            label,
            f"JSON input is {size} bytes; limit is {limits.max_bytes}",
            detail={"actual_bytes": size, "max_bytes": limits.max_bytes},
        )
    try:
        # The stat check gives an early, cheap rejection.  The bounded read is
        # still required because an external file can grow between stat/open.
        with candidate.open("rb") as handle:
            payload = handle.read(limits.max_bytes + 1)
    except OSError as exc:
        raise StrictJsonError(
            "artifact_unreadable", label, f"cannot read JSON input: {exc}"
        ) from exc
    _check_byte_count(payload, label, limits)
    return payload


def _check_byte_count(
    payload: bytes, source: str, limits: StrictJsonLimits
) -> None:
    if len(payload) > limits.max_bytes:
        raise StrictJsonError(
            "artifact_too_large",
            source,
            f"JSON input is {len(payload)} bytes; limit is {limits.max_bytes}",
            detail={"actual_bytes": len(payload), "max_bytes": limits.max_bytes},
        )


def decode_utf8(
    payload: bytes,
    *,
    source: str,
    limits: StrictJsonLimits,
) -> str:
    """Decode strict UTF-8 bytes without universal-newline translation."""

    if not isinstance(payload, bytes):
        raise StrictJsonError("invalid_input_type", source, "JSON input must be bytes")
    _check_byte_count(payload, source, limits)
    if payload.startswith(_UTF32_LE_BOM) or payload.startswith(_UTF32_BE_BOM):
        raise StrictJsonError(
            "unsupported_encoding", source, "UTF-32 JSON is not supported; use UTF-8"
        )
    if payload.startswith(_UTF16_LE_BOM) or payload.startswith(_UTF16_BE_BOM):
        raise StrictJsonError(
            "unsupported_encoding", source, "UTF-16 JSON is not supported; use UTF-8"
        )
    if payload.startswith(_UTF8_BOM):
        raise StrictJsonError(
            "unexpected_byte_order_mark", source, "a UTF-8 BOM is not permitted"
        )
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StrictJsonError(
            "invalid_utf8",
            source,
            f"JSON input is not valid UTF-8: {exc.reason}",
            location=f"byte offset {exc.start}",
            detail={"byte_offset": exc.start},
        ) from exc


def _text_as_utf8(
    text: str, *, source: str, limits: StrictJsonLimits
) -> bytes:
    if not isinstance(text, str):
        raise StrictJsonError("invalid_input_type", source, "JSON input must be text")
    if text.startswith("\ufeff"):
        raise StrictJsonError(
            "unexpected_byte_order_mark", source, "a Unicode BOM is not permitted"
        )
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise StrictJsonError(
            "invalid_utf8",
            source,
            "JSON text contains a value that cannot be encoded as UTF-8",
            location=f"character offset {exc.start}",
        ) from exc
    _check_byte_count(encoded, source, limits)
    return encoded


def _check_text_depth(text: str, source: str, limit: int) -> None:
    """Bound container nesting before ``json.loads`` can recurse."""

    depth = 0
    in_string = False
    escaped = False
    for offset, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limit:
                raise StrictJsonError(
                    "json_depth_exceeded",
                    source,
                    f"JSON nesting exceeds the {limit} level limit",
                    location=f"character offset {offset}",
                    detail={"max_depth": limit},
                )
        elif character in "]}":
            depth -= 1


def _duplicate_hook(source: str):
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise StrictJsonError(
                    "json_duplicate_key",
                    source,
                    f"duplicate JSON object key {key!r}",
                    location=f"key {key!r}",
                    detail={"key": key},
                )
            result[key] = value
        return result

    return hook


def _constant_hook(source: str):
    def reject(value: str) -> Any:
        raise StrictJsonError(
            "json_non_standard_number",
            source,
            f"{value} is not valid JSON; numbers must be finite",
            detail={"constant": value},
        )

    return reject


def _expected_name(expect: type | tuple[type, ...]) -> str:
    if isinstance(expect, type):
        return expect.__name__
    return "/".join(item.__name__ for item in expect)


def validate_json_value(
    value: Any,
    *,
    source: str,
    limits: StrictJsonLimits,
    expect: type | tuple[type, ...] | None = None,
) -> Any:
    """Recursively enforce JSON types, finite numbers, and item bounds."""

    if expect is not None and not isinstance(value, expect):
        raise StrictJsonError(
            "json_root_type_unexpected",
            source,
            f"root value is {type(value).__name__}, expected {_expected_name(expect)}",
        )

    stack: list[tuple[Any, int, str, bool]] = [(value, 1, "$", False)]
    active_containers: set[int] = set()
    total = 0
    while stack:
        item, depth, location, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(item))
            continue
        total += 1
        if total > limits.max_total_items:
            raise StrictJsonError(
                "json_item_count_exceeded",
                source,
                f"JSON value contains more than {limits.max_total_items} items",
                location=location,
                detail={"max_total_items": limits.max_total_items},
            )
        if item is None or isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise StrictJsonError(
                    "json_non_standard_number",
                    source,
                    "JSON numbers must be finite",
                    location=location,
                )
            continue
        if isinstance(item, str):
            if len(item) > limits.max_string_bytes:
                raise StrictJsonError(
                    "json_item_too_large",
                    source,
                    f"JSON string exceeds the {limits.max_string_bytes} byte limit",
                    location=location,
                )
            try:
                item_size = len(item.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as exc:
                raise StrictJsonError(
                    "invalid_utf8",
                    source,
                    "JSON string cannot be encoded as UTF-8",
                    location=location,
                ) from exc
            if item_size > limits.max_string_bytes:
                raise StrictJsonError(
                    "json_item_too_large",
                    source,
                    f"JSON string is {item_size} bytes; limit is "
                    f"{limits.max_string_bytes}",
                    location=location,
                )
            continue
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active_containers:
                raise StrictJsonError(
                    "json_circular_reference",
                    source,
                    "JSON value contains a circular reference",
                    location=location,
                )
            if depth > limits.max_depth:
                raise StrictJsonError(
                    "json_depth_exceeded",
                    source,
                    f"JSON nesting exceeds the {limits.max_depth} level limit",
                    location=location,
                    detail={"max_depth": limits.max_depth},
                )
            active_containers.add(identity)
            stack.append((item, depth, location, True))
            if len(item) > limits.max_collection_items:
                raise StrictJsonError(
                    "json_collection_size_exceeded",
                    source,
                    f"JSON collection contains {len(item)} items; limit is "
                    f"{limits.max_collection_items}",
                    location=location,
                )
            if isinstance(item, dict):
                children: list[tuple[Any, int, str, bool]] = []
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise StrictJsonError(
                            "json_object_key_type_invalid",
                            source,
                            "JSON object keys must be strings",
                            location=location,
                        )
                    children.append((key, depth, f"{location}.<key>", False))
                    children.append((child, depth + 1, f"{location}.{key}", False))
                stack.extend(reversed(children))
            else:
                for index in range(len(item) - 1, -1, -1):
                    stack.append(
                        (item[index], depth + 1, f"{location}[{index}]", False)
                    )
            continue
        raise StrictJsonError(
            "json_value_type_invalid",
            source,
            f"{type(item).__name__} is not a JSON value type",
            location=location,
        )
    return value


def loads_text(
    text: str,
    *,
    source: str,
    limits: StrictJsonLimits,
    expect: type | tuple[type, ...] | None = dict,
) -> Any:
    """Parse bounded JSON text with duplicate-key and finite-number rejection."""

    _text_as_utf8(text, source=source, limits=limits)
    _check_text_depth(text, source, limits.max_depth)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_hook(source),
            parse_constant=_constant_hook(source),
        )
    except json.JSONDecodeError as exc:
        raise StrictJsonError(
            "json_malformed",
            source,
            f"malformed JSON: {exc.msg}",
            location=f"line {exc.lineno}, column {exc.colno}",
            detail={"line": exc.lineno, "column": exc.colno},
        ) from exc
    return validate_json_value(value, source=source, limits=limits, expect=expect)


def loads_bytes(
    payload: bytes,
    *,
    source: str,
    limits: StrictJsonLimits,
    expect: type | tuple[type, ...] | None = dict,
) -> Any:
    text = decode_utf8(payload, source=source, limits=limits)
    return loads_text(text, source=source, limits=limits, expect=expect)


def load_file(
    path: Path | str,
    *,
    source: str | None = None,
    limits: StrictJsonLimits,
    expect: type | tuple[type, ...] | None = dict,
) -> Any:
    label = source or str(path)
    payload = read_bounded_bytes(path, source=label, limits=limits)
    return loads_bytes(payload, source=label, limits=limits, expect=expect)


def dumps_strict(
    value: Any,
    *,
    source: str,
    limits: StrictJsonLimits,
    ensure_ascii: bool = False,
    sort_keys: bool = True,
    separators: tuple[str, str] | None = None,
    indent: int | None = None,
    trailing_newline: bool = False,
) -> str:
    """Serialize only bounded, recursively valid JSON with ``allow_nan=False``."""

    validate_json_value(value, source=source, limits=limits)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
            separators=separators,
            indent=indent,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StrictJsonError(
            "json_serialization_failed", source, f"cannot serialize JSON: {exc}"
        ) from exc
    if trailing_newline:
        rendered += "\n"
    encoded = _text_as_utf8(rendered, source=source, limits=limits)
    _check_byte_count(encoded, source, limits)
    return rendered


__all__ = [
    "ARTIFACT_JSON_LIMITS",
    "BASELINE_JSON_LIMITS",
    "CHECK_JSON_LIMITS",
    "EVIDENCE_JSON_LIMITS",
    "POLICY_JSON_LIMITS",
    "SARIF_JSON_LIMITS",
    "StrictJsonError",
    "StrictJsonLimits",
    "decode_utf8",
    "dumps_strict",
    "load_file",
    "loads_bytes",
    "loads_text",
    "read_bounded_bytes",
    "validate_json_value",
]
