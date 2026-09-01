"""Strict canonical JSON and content identity helpers.

Hashes identify exact bytes.  They do not prove that content is true,
authorized, safe, or useful.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, cast

from pydantic import AfterValidator, BaseModel, PlainSerializer

type JSONScalar = bool | int | str | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]


def deep_freeze_json(value: JSONValue) -> JSONValue:
    """Recursively replace mutable JSON containers with read-only equivalents."""

    if isinstance(value, dict):
        frozen = MappingProxyType({key: deep_freeze_json(item) for key, item in value.items()})
        return cast(JSONValue, frozen)
    if isinstance(value, list):
        return cast(JSONValue, tuple(deep_freeze_json(item) for item in value))
    return value


def deep_thaw_json(value: object) -> JSONValue:
    """Return ordinary JSON containers for serialization and external transport."""

    if isinstance(value, Mapping):
        return {str(key): deep_thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw_json(item) for item in value]
    return cast(JSONValue, value)


ImmutableJSONValue = Annotated[
    JSONValue,
    AfterValidator(deep_freeze_json),
    PlainSerializer(deep_thaw_json, return_type=JSONValue),
]
ImmutableJSONObject = Annotated[
    dict[str, JSONValue],
    AfterValidator(deep_freeze_json),
    PlainSerializer(deep_thaw_json, return_type=dict[str, JSONValue]),
]


class CanonicalizationError(ValueError):
    """Input cannot be represented by Strongwiz canonical JSON."""


def normalize(value: object) -> JSONValue:
    """Return a detached, float-free JSON value with string object keys."""

    if isinstance(value, BaseModel):
        return normalize(value.model_dump(mode="json", exclude_none=False, by_alias=True))
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("binary floating-point is not canonical")
    if isinstance(value, Mapping):
        output: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            if key in output:
                raise CanonicalizationError(f"duplicate canonical key: {key}")
            output[key] = normalize(item)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Encode one normalized value as deterministic UTF-8 JSON."""

    normalized = normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_text(value: object) -> str:
    return canonical_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_hash(value: object) -> str:
    return sha256_bytes(canonical_bytes(value))


def parse_strict_json(raw: str | bytes) -> JSONValue:
    """Parse JSON while rejecting duplicate keys and nonstandard constants."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in items:
            if key in output:
                raise CanonicalizationError(f"duplicate JSON key: {key}")
            output[key] = value
        return output

    def constant(value: str) -> object:
        raise CanonicalizationError(f"non-finite JSON number: {value}")

    try:
        parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as error:
        raise CanonicalizationError(str(error)) from error
    return normalize(parsed)
