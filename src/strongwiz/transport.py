"""Bounded, deterministic framed transport for model and domain adapters.

The wire format is deliberately small and independent of terminals, lines, and
platform text modes::

    magic (4) | version (1) | payload length (8) | SHA-256 (32) | payload

The payload is Strongwiz canonical JSON encoded as strict UTF-8.  The digest
protects transport integrity and exact byte identity; it does not establish
truth, authorization, freshness, or usefulness.  Callers that have a semantic
message identity can additionally use :class:`ReplayGuard`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from strongwiz.canonical import (
    CanonicalizationError,
    JSONValue,
    canonical_bytes,
    parse_strict_json,
)

FRAME_MAGIC = b"SWZJ"
FRAME_VERSION = 1
DEFAULT_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_HEADER = struct.Struct(">4sBQ32s")
HEADER_SIZE = _HEADER.size
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class BinaryReader(Protocol):
    """Minimum synchronous binary-reader surface used by this module."""

    def read(self, size: int = -1, /) -> bytes: ...


class BinaryWriter(Protocol):
    """Minimum synchronous binary-writer surface used by this module."""

    def write(self, data: bytes, /) -> int: ...


class TransportError(Exception):
    """Base error for framed transport failures."""


class FrameEncodingError(TransportError):
    """A value cannot be represented as a canonical JSON frame."""


class FrameFormatError(TransportError):
    """A received frame violates the declared wire format."""


class EndOfStreamError(FrameFormatError, EOFError):
    """The stream ended cleanly before another frame began."""


class TruncatedFrameError(FrameFormatError, EOFError):
    """The stream ended after only part of a frame arrived."""


class FrameTimeoutError(TransportError, TimeoutError):
    """The stream timed out while a frame operation was in progress."""


class FrameTooLargeError(FrameFormatError):
    """The declared or encoded payload exceeds the configured bound."""


class FrameChecksumError(FrameFormatError):
    """The received payload does not match its declared SHA-256 digest."""


class TrailingDataError(FrameFormatError):
    """A one-frame input contains bytes beyond its declared payload."""


class ReplayError(TransportError):
    """A semantic message identity or exact payload has already been accepted."""


class DuplicateFrameError(ReplayError):
    """The same semantic identity and content were presented again."""


class IdentityReuseError(ReplayError):
    """A semantic identity was reused for different content."""


class PayloadReplayError(ReplayError):
    """Exact content was presented under a different semantic identity."""


@dataclass(frozen=True, slots=True)
class FrameReceipt:
    """Transport facts for one exact canonical payload."""

    payload_size: int
    payload_sha256: str
    frame_size: int


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """A decoded JSON value and its exact transport receipt."""

    value: JSONValue
    receipt: FrameReceipt


def _validate_limit(max_payload_bytes: int) -> None:
    if isinstance(max_payload_bytes, bool) or max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be a positive integer")


def _encode_payload(value: object, max_payload_bytes: int) -> tuple[bytes, FrameReceipt]:
    _validate_limit(max_payload_bytes)
    try:
        payload = canonical_bytes(value)
    except (CanonicalizationError, UnicodeError) as error:
        raise FrameEncodingError(f"payload is not canonical JSON: {error}") from error
    payload_size = len(payload)
    if payload_size > max_payload_bytes:
        raise FrameTooLargeError(
            f"payload is {payload_size} bytes; configured maximum is {max_payload_bytes}"
        )
    digest = hashlib.sha256(payload).digest()
    receipt = FrameReceipt(
        payload_size=payload_size,
        payload_sha256=digest.hex(),
        frame_size=HEADER_SIZE + payload_size,
    )
    return _HEADER.pack(FRAME_MAGIC, FRAME_VERSION, payload_size, digest) + payload, receipt


def encode_frame(value: object, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> bytes:
    """Return one deterministic, length-prefixed canonical JSON frame."""

    frame, _ = _encode_payload(value, max_payload_bytes)
    return frame


def frame_receipt(
    value: object, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
) -> FrameReceipt:
    """Return the receipt that encoding ``value`` would produce."""

    _, receipt = _encode_payload(value, max_payload_bytes)
    return receipt


def _parse_header(header: bytes, max_payload_bytes: int) -> tuple[int, bytes]:
    _validate_limit(max_payload_bytes)
    if len(header) != HEADER_SIZE:
        raise TruncatedFrameError(
            f"header requires {HEADER_SIZE} bytes; received {len(header)}"
        )
    magic, version, payload_size, expected_digest = _HEADER.unpack(header)
    if magic != FRAME_MAGIC:
        raise FrameFormatError("invalid frame magic")
    if version != FRAME_VERSION:
        raise FrameFormatError(f"unsupported frame version: {version}")
    if payload_size == 0:
        raise FrameFormatError("canonical JSON payload cannot be empty")
    if payload_size > max_payload_bytes:
        raise FrameTooLargeError(
            f"declared payload is {payload_size} bytes; configured maximum is "
            f"{max_payload_bytes}"
        )
    return payload_size, expected_digest


def _decode_payload(payload: bytes, expected_digest: bytes) -> JSONValue:
    actual_digest = hashlib.sha256(payload).digest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise FrameChecksumError("payload SHA-256 does not match the frame header")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FrameFormatError("payload is not strict UTF-8") from error
    try:
        value = parse_strict_json(text)
        canonical = canonical_bytes(value)
    except (CanonicalizationError, UnicodeError) as error:
        raise FrameFormatError(f"payload is not strict canonical JSON: {error}") from error
    if not hmac.compare_digest(canonical, payload):
        raise FrameFormatError("payload JSON is valid but not in canonical byte form")
    return value


def decode_frame_record(
    frame: bytes | bytearray | memoryview,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> DecodedFrame:
    """Decode exactly one in-memory frame and reject missing or trailing bytes."""

    raw = bytes(frame)
    if not raw:
        raise EndOfStreamError("no frame bytes available")
    if len(raw) < HEADER_SIZE:
        raise TruncatedFrameError(f"header requires {HEADER_SIZE} bytes; received {len(raw)}")
    payload_size, expected_digest = _parse_header(raw[:HEADER_SIZE], max_payload_bytes)
    expected_size = HEADER_SIZE + payload_size
    if len(raw) < expected_size:
        raise TruncatedFrameError(
            f"payload requires {payload_size} bytes; received {len(raw) - HEADER_SIZE}"
        )
    if len(raw) > expected_size:
        raise TrailingDataError(f"frame has {len(raw) - expected_size} trailing byte(s)")
    payload = raw[HEADER_SIZE:]
    value = _decode_payload(payload, expected_digest)
    return DecodedFrame(
        value=value,
        receipt=FrameReceipt(payload_size, expected_digest.hex(), expected_size),
    )


def decode_frame(
    frame: bytes | bytearray | memoryview,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> JSONValue:
    """Decode exactly one in-memory frame and return its JSON value."""

    return decode_frame_record(frame, max_payload_bytes=max_payload_bytes).value


def _deadline(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return time.monotonic() + timeout_seconds


def _ensure_time(deadline: float | None, operation: str) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise FrameTimeoutError(f"timed out while {operation}")


def _read_exact(
    reader: BinaryReader,
    count: int,
    *,
    phase: str,
    deadline: float | None,
    clean_eof_allowed: bool,
) -> bytes:
    output = bytearray()
    while len(output) < count:
        _ensure_time(deadline, f"reading frame {phase}")
        remaining = count - len(output)
        try:
            chunk = reader.read(remaining)
        except TimeoutError as error:
            raise FrameTimeoutError(f"timed out while reading frame {phase}") from error
        _ensure_time(deadline, f"reading frame {phase}")
        if chunk is None:
            raise FrameTimeoutError(f"reader would block while reading frame {phase}")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TransportError(f"binary reader returned {type(chunk).__name__}, not bytes")
        data = bytes(chunk)
        if len(data) > remaining:
            raise TransportError("binary reader returned more bytes than requested")
        if not data:
            if clean_eof_allowed and not output:
                raise EndOfStreamError("stream ended before another frame began")
            raise TruncatedFrameError(
                f"stream ended after {len(output)} of {count} frame {phase} bytes"
            )
        output.extend(data)
    return bytes(output)


def _read_frame_record_until(
    reader: BinaryReader,
    *,
    max_payload_bytes: int,
    deadline: float | None,
) -> DecodedFrame:
    header = _read_exact(
        reader,
        HEADER_SIZE,
        phase="header",
        deadline=deadline,
        clean_eof_allowed=True,
    )
    payload_size, expected_digest = _parse_header(header, max_payload_bytes)
    payload = _read_exact(
        reader,
        payload_size,
        phase="payload",
        deadline=deadline,
        clean_eof_allowed=False,
    )
    value = _decode_payload(payload, expected_digest)
    _ensure_time(deadline, "decoding frame payload")
    return DecodedFrame(
        value=value,
        receipt=FrameReceipt(
            payload_size=payload_size,
            payload_sha256=expected_digest.hex(),
            frame_size=HEADER_SIZE + payload_size,
        ),
    )


def read_frame_record(
    reader: BinaryReader,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float | None = None,
) -> DecodedFrame:
    """Read one frame, allowing subsequent frames to remain in the stream.

    ``timeout_seconds`` bounds cooperative streams: it is checked around every
    partial operation, and native ``TimeoutError`` is normalized.  A stream
    whose own ``read`` can block forever must configure its native timeout.
    """

    return _read_frame_record_until(
        reader,
        max_payload_bytes=max_payload_bytes,
        deadline=_deadline(timeout_seconds),
    )


def read_frame(
    reader: BinaryReader,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float | None = None,
) -> JSONValue:
    """Read one frame and return its JSON value."""

    return read_frame_record(
        reader,
        max_payload_bytes=max_payload_bytes,
        timeout_seconds=timeout_seconds,
    ).value


def read_single_frame_record(
    reader: BinaryReader,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float | None = None,
) -> DecodedFrame:
    """Read a one-frame input and require immediate EOF after the payload."""

    deadline = _deadline(timeout_seconds)
    frame = _read_frame_record_until(
        reader,
        max_payload_bytes=max_payload_bytes,
        deadline=deadline,
    )
    _ensure_time(deadline, "checking for trailing data")
    try:
        trailing = reader.read(1)
    except TimeoutError as error:
        raise FrameTimeoutError("timed out while checking for trailing data") from error
    _ensure_time(deadline, "checking for trailing data")
    if trailing is None:
        raise FrameTimeoutError("reader would block while checking for trailing data")
    if not isinstance(trailing, (bytes, bytearray, memoryview)):
        raise TransportError(f"binary reader returned {type(trailing).__name__}, not bytes")
    if trailing:
        raise TrailingDataError("one-frame stream contains trailing data")
    return frame


def write_frame(
    writer: BinaryWriter,
    value: object,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float | None = None,
) -> FrameReceipt:
    """Write one complete frame, retrying all valid partial writes."""

    frame, receipt = _encode_payload(value, max_payload_bytes)
    deadline = _deadline(timeout_seconds)
    offset = 0
    while offset < len(frame):
        _ensure_time(deadline, "writing frame")
        try:
            written = writer.write(frame[offset:])
        except TimeoutError as error:
            raise FrameTimeoutError("timed out while writing frame") from error
        _ensure_time(deadline, "writing frame")
        if written is None:
            raise FrameTimeoutError("writer would block while writing frame")
        if isinstance(written, bool) or not isinstance(written, int):
            raise TransportError("binary writer did not return an integer byte count")
        if written <= 0:
            raise TransportError("binary writer made no progress")
        if written > len(frame) - offset:
            raise TransportError("binary writer reported more bytes than supplied")
        offset += written
    return receipt


class ReplayGuard:
    """Bounded, thread-safe protection for accepted message identities.

    The guard rejects an exact duplicate, an identity reused for new content,
    and exact content replayed under a second identity.  Once the oldest entry
    is evicted from the bounded window, the guard makes no freshness claim for
    it; durable anti-replay should persist identities in the run ledger.
    """

    def __init__(self, capacity: int = 4096) -> None:
        if isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._by_identity: OrderedDict[str, str] = OrderedDict()
        self._by_digest: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def check_and_record(self, identity: str, payload_sha256: str) -> None:
        """Atomically reject a replay or remember a previously unseen message."""

        if not identity.strip():
            raise ValueError("message identity must be non-empty")
        if not _SHA256_HEX.fullmatch(payload_sha256):
            raise ValueError("payload_sha256 must be lowercase SHA-256")
        with self._lock:
            prior_digest = self._by_identity.get(identity)
            if prior_digest is not None:
                if hmac.compare_digest(prior_digest, payload_sha256):
                    raise DuplicateFrameError(f"duplicate message identity: {identity}")
                raise IdentityReuseError(
                    f"message identity {identity!r} was reused for different content"
                )
            prior_identity = self._by_digest.get(payload_sha256)
            if prior_identity is not None:
                raise PayloadReplayError(
                    f"payload already accepted as message identity {prior_identity!r}"
                )
            self._by_identity[identity] = payload_sha256
            self._by_digest[payload_sha256] = identity
            if len(self._by_identity) > self._capacity:
                expired_identity, expired_digest = self._by_identity.popitem(last=False)
                if self._by_digest.get(expired_digest) == expired_identity:
                    del self._by_digest[expired_digest]


def read_identified_frame(
    reader: BinaryReader,
    replay_guard: ReplayGuard,
    *,
    identity_field: str = "message_id",
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float | None = None,
) -> DecodedFrame:
    """Read a top-level object and atomically admit its declared identity."""

    if not identity_field.strip():
        raise ValueError("identity_field must be non-empty")
    frame = read_frame_record(
        reader,
        max_payload_bytes=max_payload_bytes,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(frame.value, Mapping):
        raise FrameFormatError("identified frame payload must be a top-level object")
    identity = frame.value.get(identity_field)
    if not isinstance(identity, str) or not identity.strip():
        raise FrameFormatError(
            f"identified frame requires a non-empty string {identity_field!r}"
        )
    replay_guard.check_and_record(identity, frame.receipt.payload_sha256)
    return frame
