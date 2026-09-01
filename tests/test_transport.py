from __future__ import annotations

import hashlib
import io
import struct
from collections.abc import Iterator

import pytest

import strongwiz.transport as transport_module
from strongwiz.canonical import canonical_bytes
from strongwiz.transport import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    FRAME_MAGIC,
    FRAME_VERSION,
    HEADER_SIZE,
    BinaryReader,
    DuplicateFrameError,
    EndOfStreamError,
    FrameChecksumError,
    FrameFormatError,
    FrameTimeoutError,
    FrameTooLargeError,
    IdentityReuseError,
    PayloadReplayError,
    ReplayGuard,
    TrailingDataError,
    TransportError,
    TruncatedFrameError,
    decode_frame,
    decode_frame_record,
    encode_frame,
    read_frame,
    read_identified_frame,
    read_single_frame_record,
    write_frame,
)

_TEST_HEADER = struct.Struct(">4sBQ32s")


class PartialReader:
    def __init__(self, data: bytes, chunk_size: int) -> None:
        self._stream = io.BytesIO(data)
        self._chunk_size = chunk_size

    def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            size = self._chunk_size
        return self._stream.read(min(size, self._chunk_size))


class PartialWriter:
    def __init__(self, chunk_size: int) -> None:
        self.data = bytearray()
        self._chunk_size = chunk_size

    def write(self, data: bytes, /) -> int:
        accepted = min(len(data), self._chunk_size)
        self.data.extend(data[:accepted])
        return accepted


class TimeoutReader:
    def read(self, size: int = -1, /) -> bytes:
        del size
        raise TimeoutError("native timeout")


class TimeoutWriter:
    def write(self, data: bytes, /) -> int:
        del data
        raise TimeoutError("native timeout")


class ZeroWriter:
    def write(self, data: bytes, /) -> int:
        del data
        return 0


class ManualClock:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class AdvancingReader:
    def __init__(self, data: bytes, clock: ManualClock, delays: tuple[float, ...]) -> None:
        self._stream = io.BytesIO(data)
        self._clock = clock
        self._delays = iter(delays)

    def read(self, size: int = -1, /) -> bytes:
        self._clock.advance(next(self._delays, 0.0))
        return self._stream.read(size)


class AdvancingWriter:
    def __init__(self, clock: ManualClock, delay: float) -> None:
        self._clock = clock
        self._delay = delay

    def write(self, data: bytes, /) -> int:
        self._clock.advance(self._delay)
        return len(data)


def _exact_size_value(payload_size: int) -> str:
    assert payload_size >= 2
    value = "x" * (payload_size - 2)
    assert len(canonical_bytes(value)) == payload_size
    return value


def _raw_frame(payload: bytes, *, declared_size: int | None = None) -> bytes:
    size = len(payload) if declared_size is None else declared_size
    return (
        _TEST_HEADER.pack(
            FRAME_MAGIC,
            FRAME_VERSION,
            size,
            hashlib.sha256(payload).digest(),
        )
        + payload
    )


@pytest.mark.parametrize("payload_size", [511, 512, 2169, 65536])
def test_exact_payload_sizes_round_trip_without_line_or_pty_limits(payload_size: int) -> None:
    value = _exact_size_value(payload_size)
    frame = encode_frame(value)
    decoded = decode_frame_record(frame)
    assert decoded.value == value
    assert decoded.receipt.payload_size == payload_size
    assert decoded.receipt.frame_size == HEADER_SIZE + payload_size


def test_encoding_is_deterministic_canonical_utf8_and_unicode_preserving() -> None:
    left = {"z": "雪だるま☃", "a": ["Δ", 1, True]}
    right = {"a": ["Δ", 1, True], "z": "雪だるま☃"}
    first = encode_frame(left)
    second = encode_frame(right)
    assert first == second
    assert b"\\u" not in first[HEADER_SIZE:]
    assert decode_frame(first) == left


def test_partial_reads_and_writes_are_completed_exactly() -> None:
    value = {"message_id": "partial-1", "body": "x" * 5000}
    writer = PartialWriter(chunk_size=7)
    receipt = write_frame(writer, value)
    assert receipt.frame_size == len(writer.data)
    decoded = read_frame(PartialReader(bytes(writer.data), chunk_size=3))
    assert decoded == value


def test_stream_reader_leaves_a_following_frame_for_the_next_call() -> None:
    stream = io.BytesIO(encode_frame({"n": 1}) + encode_frame({"n": 2}))
    assert read_frame(stream) == {"n": 1}
    assert read_frame(stream) == {"n": 2}
    with pytest.raises(EndOfStreamError):
        read_frame(stream)


def test_single_frame_read_and_memory_decode_reject_trailing_data() -> None:
    frame = encode_frame({"only": True})
    with pytest.raises(TrailingDataError):
        decode_frame(frame + b"x")
    with pytest.raises(TrailingDataError):
        read_single_frame_record(io.BytesIO(frame + b"x"))
    assert read_single_frame_record(io.BytesIO(frame)).value == {"only": True}


def test_replay_guard_rejects_duplicate_identity_identity_reuse_and_payload_replay() -> None:
    guard = ReplayGuard()
    first = encode_frame({"message_id": "m-1", "value": 1})
    receipt = read_identified_frame(io.BytesIO(first), guard).receipt
    with pytest.raises(DuplicateFrameError):
        read_identified_frame(io.BytesIO(first), guard)

    changed = encode_frame({"message_id": "m-1", "value": 2})
    with pytest.raises(IdentityReuseError):
        read_identified_frame(io.BytesIO(changed), guard)

    with pytest.raises(PayloadReplayError):
        guard.check_and_record("m-2", receipt.payload_sha256)


def test_replay_guard_has_an_explicit_bounded_window() -> None:
    guard = ReplayGuard(capacity=1)
    one = decode_frame_record(encode_frame({"value": 1})).receipt.payload_sha256
    two = decode_frame_record(encode_frame({"value": 2})).receipt.payload_sha256
    guard.check_and_record("one", one)
    guard.check_and_record("two", two)
    guard.check_and_record("one", one)


def test_identified_frames_require_a_top_level_nonempty_string_identity() -> None:
    guard = ReplayGuard()
    for value in ([1], {"message_id": ""}, {"wrong": "m-1"}):
        with pytest.raises(FrameFormatError):
            read_identified_frame(io.BytesIO(encode_frame(value)), guard)


def test_truncated_header_payload_and_clean_eof_are_distinct() -> None:
    frame = encode_frame({"body": "complete"})
    with pytest.raises(EndOfStreamError):
        decode_frame(b"")
    with pytest.raises(EndOfStreamError):
        read_frame(io.BytesIO(b""))
    with pytest.raises(TruncatedFrameError, match="header"):
        decode_frame(frame[: HEADER_SIZE - 1])
    with pytest.raises(TruncatedFrameError, match="payload"):
        decode_frame(frame[:-1])
    with pytest.raises(TruncatedFrameError, match="payload"):
        read_frame(PartialReader(frame[:-1], chunk_size=5))


def test_corrupt_lengths_are_rejected_as_oversize_truncated_or_trailing() -> None:
    payload = canonical_bytes({"body": "length"})
    with pytest.raises(FrameTooLargeError):
        decode_frame(_raw_frame(payload, declared_size=DEFAULT_MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(TruncatedFrameError):
        decode_frame(_raw_frame(payload, declared_size=len(payload) + 1))
    with pytest.raises(TrailingDataError):
        decode_frame(_raw_frame(payload, declared_size=len(payload) - 1))


def test_malformed_magic_version_checksum_utf8_and_noncanonical_json_are_rejected() -> None:
    valid = bytearray(encode_frame({"ok": True}))
    valid[0] ^= 1
    with pytest.raises(FrameFormatError, match="magic"):
        decode_frame(valid)

    wrong_version = (
        _TEST_HEADER.pack(
            FRAME_MAGIC,
            FRAME_VERSION + 1,
            1,
            hashlib.sha256(b"0").digest(),
        )
        + b"0"
    )
    with pytest.raises(FrameFormatError, match="version"):
        decode_frame(wrong_version)

    bad_checksum = bytearray(encode_frame({"ok": True}))
    bad_checksum[-1] ^= 1
    with pytest.raises(FrameChecksumError):
        decode_frame(bad_checksum)

    with pytest.raises(FrameFormatError, match="UTF-8"):
        decode_frame(_raw_frame(b"\xff"))
    with pytest.raises(FrameFormatError, match="canonical"):
        decode_frame(_raw_frame(b'{"b":1, "a":2}'))
    with pytest.raises(FrameFormatError, match="duplicate"):
        decode_frame(_raw_frame(b'{"a":1,"a":2}'))


def test_encoding_and_declared_sizes_obey_the_configured_bound() -> None:
    value = _exact_size_value(512)
    frame = encode_frame(value, max_payload_bytes=512)
    assert decode_frame(frame, max_payload_bytes=512) == value
    with pytest.raises(FrameTooLargeError):
        encode_frame(value, max_payload_bytes=511)
    with pytest.raises(ValueError, match="positive"):
        encode_frame(value, max_payload_bytes=0)


def test_native_timeouts_are_normalized_and_zero_writes_do_not_spin() -> None:
    with pytest.raises(FrameTimeoutError):
        read_frame(TimeoutReader())
    with pytest.raises(FrameTimeoutError):
        write_frame(TimeoutWriter(), {"message_id": "timeout"})
    with pytest.raises(TransportError, match="no progress"):
        write_frame(ZeroWriter(), {"message_id": "zero"})
    with pytest.raises(ValueError, match="positive"):
        read_frame(io.BytesIO(b""), timeout_seconds=0)


def test_deadline_rejects_a_final_payload_read_that_returns_too_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    monkeypatch.setattr(transport_module.time, "monotonic", clock.monotonic)
    frame = encode_frame({"message_id": "slow-payload"})
    reader = AdvancingReader(frame, clock, delays=(0.0, 2.0))

    with pytest.raises(FrameTimeoutError, match="reading frame payload"):
        read_frame(reader, timeout_seconds=1.0)


def test_deadline_rejects_a_complete_write_that_returns_too_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    monkeypatch.setattr(transport_module.time, "monotonic", clock.monotonic)

    with pytest.raises(FrameTimeoutError, match="writing frame"):
        write_frame(
            AdvancingWriter(clock, delay=2.0),
            {"message_id": "slow-write"},
            timeout_seconds=1.0,
        )


def test_deadline_rejects_a_trailing_byte_check_that_returns_too_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = ManualClock()
    monkeypatch.setattr(transport_module.time, "monotonic", clock.monotonic)
    frame = encode_frame({"message_id": "slow-trailing-check"})
    reader = AdvancingReader(frame, clock, delays=(0.0, 0.0, 2.0))

    with pytest.raises(FrameTimeoutError, match="checking for trailing data"):
        read_single_frame_record(reader, timeout_seconds=1.0)


def test_single_frame_read_constructs_one_end_to_end_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_calls = 0
    real_deadline = transport_module._deadline

    def counted_deadline(timeout_seconds: float | None) -> float | None:
        nonlocal deadline_calls
        deadline_calls += 1
        return real_deadline(timeout_seconds)

    monkeypatch.setattr(transport_module, "_deadline", counted_deadline)
    frame = encode_frame({"message_id": "one-deadline"})
    assert read_single_frame_record(io.BytesIO(frame), timeout_seconds=1.0).value == {
        "message_id": "one-deadline"
    }
    assert deadline_calls == 1


def test_reader_protocol_is_satisfied_by_partial_reader() -> None:
    reader: BinaryReader = PartialReader(encode_frame({"ok": True}), chunk_size=2)
    assert read_frame(reader) == {"ok": True}


def test_replay_guard_rejects_invalid_configuration_and_receipts() -> None:
    with pytest.raises(ValueError, match="positive"):
        ReplayGuard(capacity=0)
    guard = ReplayGuard()
    with pytest.raises(ValueError, match="non-empty"):
        guard.check_and_record(" ", "0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        guard.check_and_record("m", "invalid")


def _yield_frames(values: list[object]) -> Iterator[bytes]:
    for value in values:
        yield encode_frame(value)


def test_frame_sequence_is_stable_across_repeated_construction() -> None:
    values: list[object] = [{"n": 1}, {"n": 2}, "Δ"]
    assert b"".join(_yield_frames(values)) == b"".join(_yield_frames(values))
