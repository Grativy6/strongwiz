"""Bounded-memory verifier, run sealer, and evidence-capsule publisher.

This tool is deliberately post-run only.  It never imports the calibration
workflow or an environment implementation, and it never interprets opaque
domain-state bytes.  It preserves the Strongwiz v1 seal/capsule identities
while replacing the legacy whole-ledger materialization with streaming passes
and a temporary disk-backed closure index.

The exclusive lock coordinates cooperative finalizers.  Link/reparse checks,
identity tokens, and quarantine-before-delete narrow filesystem races, but
Python on Windows does not expose a complete handle-relative no-follow tree API;
a privileged concurrent path mutator remains outside the crash-durability claim.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final

from pydantic import ValidationError

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for _import_root in (_REPOSITORY_ROOT, _REPOSITORY_ROOT / "src"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from calibration.models import (  # noqa: E402
    ArtifactPointer,
    CalibrationRunReceipt,
    RunTerminalRecord,
)
from strongwiz.canonical import (  # noqa: E402
    JSONValue,
    canonical_bytes,
    content_hash,
    parse_strict_json,
)
from strongwiz.contracts import ContractModel  # noqa: E402
from strongwiz.lab import (  # noqa: E402
    CAPSULE_DOMAIN_STATE_PATH,
    CAPSULE_GENESIS_PATH,
    CAPSULE_LAB_MANIFEST_PATH,
    CAPSULE_MANIFEST_PATH,
    CAPSULE_OBJECTS_PATH,
    CAPSULE_RECEIPTS_PATH,
    CAPSULE_RUN_SEAL_PATH,
    CAPSULE_RUN_SPEC_PATH,
    CapsuleFile,
    CapsuleFileRole,
    CapsuleObject,
    EvidenceCapsuleManifest,
    ExternalDomainStateSeal,
    LabGenesisSeal,
    LabManifest,
    RunSeal,
    RunSpec,
)
from strongwiz.ledger import ReceiptEnvelope  # noqa: E402

_CHUNK_BYTES: Final = 1024 * 1024
_MAX_CONTRACT_BYTES: Final = 64 * 1024 * 1024
_MAX_JSONL_LINE_BYTES: Final = 64 * 1024 * 1024
_TERMINAL_DOMAIN_RELATIVE: Final = "terminal.record.json"
_LIVE_LOCK_DOMAIN_RELATIVE: Final = "control/.calibration-live.lock"
_SQLITE_TRANSIENT_SUFFIXES: Final = ("-journal", "-shm", "-wal")
_PRIVATE_REASONING_KEYS: Final = frozenset(
    {
        "chain_of_thought",
        "hidden_cot",
        "hidden_reasoning",
        "internal_monologue",
        "private_reasoning",
        "scratchpad",
        "thought_tokens",
    }
)

_Emitter = Callable[["_HashingWriter"], None]


class StreamingPostrunError(RuntimeError):
    """The closed-run verification or publication failed."""


@dataclass(frozen=True)
class FileIdentity:
    size_bytes: int
    mtime_ns: int
    sha256: str
    device: int
    inode: int


@dataclass(frozen=True)
class SourceContractPin:
    path: Path
    raw: bytes
    stat_token: tuple[int, int, int, int]
    model: type[ContractModel]


@dataclass(frozen=True)
class WrittenArtifact:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FrozenFile:
    relative_path: str
    role: CapsuleFileRole
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class LedgerSealData:
    receipt_count: int
    receipt_head: str | None
    object_count: int
    objects_projection_ref: str
    receipts_projection_ref: str
    terminal_object_present: bool
    terminal_receipt_present: bool
    objects_file_size: int
    objects_file_sha256: str
    receipts_file_size: int
    receipts_file_sha256: str

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "object_count": self.object_count,
            "objects_projection_ref": self.objects_projection_ref,
            "receipt_count": self.receipt_count,
            "receipt_head": self.receipt_head,
            "receipts_projection_ref": self.receipts_projection_ref,
        }


@dataclass(frozen=True)
class DomainSealData:
    entry_count: int
    projection_ref: str


@dataclass(frozen=True)
class PostrunResult:
    run_seal_ref: str
    evidence_capsule_ref: str
    capsule_root: Path
    delivery_receipt_path: Path
    source_ledger_sha256: str


class _HashingWriter:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.size_bytes = 0

    def write(self, value: bytes) -> None:
        self._stream.write(value)
        self._digest.update(value)
        self.size_bytes += len(value)

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _require_no_link_components(path: Path, *, label: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if _is_link_like(current):
            raise StreamingPostrunError(f"{label} crosses a link-like path: {current}")


def _metadata_token(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_size,
        metadata.st_mtime_ns,
        getattr(metadata, "st_dev", 0),
        getattr(metadata, "st_ino", 0),
    )


def _ownership_token(path: Path) -> tuple[int, int]:
    token = _stat_token(path)
    return token[2], token[3]


def _quarantine_owned_path(path: Path, *, owned_token: tuple[int, int], purpose: str) -> Path:
    """Move an owned path to an unpredictable sibling before destructive cleanup."""

    if _ownership_token(path) != owned_token:
        raise StreamingPostrunError(
            f"{purpose} identity changed; refusing transaction-misowned cleanup"
        )
    quarantine = path.with_name(f".{path.name}.{purpose}-{os.urandom(16).hex()}")
    if quarantine.exists() or _is_link_like(quarantine):
        raise StreamingPostrunError(f"unexpected {purpose} quarantine collision")
    os.rename(path, quarantine)
    try:
        if _ownership_token(quarantine) != owned_token:
            raise StreamingPostrunError(
                f"{purpose} identity changed during quarantine; refusing cleanup"
            )
    except BaseException:
        if not path.exists() and not _is_link_like(path):
            with suppress(OSError):
                os.rename(quarantine, path)
        raise
    return quarantine


@contextmanager
def _destination_finalizer_lock(*, run: Path, capsule: Path, receipt: Path) -> Iterator[Path]:
    capsule_key = os.path.normcase(os.path.abspath(capsule))
    lock_ref = hashlib.sha256(canonical_bytes({"capsule": capsule_key})).hexdigest()[:32]
    lock_path = capsule.parent / f".strongwiz-postrun-finalizer-{lock_ref}.lock"
    _require_no_link_components(lock_path, label="finalizer lock")
    payload = canonical_bytes(
        {
            "capsule": str(capsule),
            "receipt": str(receipt),
            "run": str(run),
            "transaction_id": os.urandom(16).hex(),
        }
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as error:
        raise StreamingPostrunError(
            f"another finalizer owns the capsule destination lock: {lock_path}"
        ) from error
    except OSError as error:
        raise StreamingPostrunError(f"cannot acquire finalizer lock: {error}") from error

    owned_token = _metadata_token(os.fstat(descriptor))
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise StreamingPostrunError("finalizer lock write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with suppress(OSError):
            if _stat_token(lock_path) == owned_token:
                lock_path.unlink()
        raise
    else:
        os.close(descriptor)

    # Capture the durable metadata after the payload write.  Both this token and
    # the unpredictable transaction payload must still match before release.
    owned_token = _stat_token(lock_path)
    try:
        yield lock_path
    finally:
        try:
            lock_token = _stat_token(lock_path)
            lock_payload = _read_bounded(lock_path, limit=4096)
        except (FileNotFoundError, OSError, StreamingPostrunError) as error:
            raise StreamingPostrunError(
                "finalizer lock disappeared or became unreadable; refusing cleanup"
            ) from error
        if lock_token != owned_token or lock_payload != payload:
            raise StreamingPostrunError(
                "finalizer lock ownership changed; refusing to remove another "
                "transaction's lock"
            )
        release_path = _quarantine_owned_path(
            lock_path,
            owned_token=(owned_token[2], owned_token[3]),
            purpose="lock-release",
        )
        try:
            if _read_bounded(release_path, limit=4096) != payload:
                raise StreamingPostrunError(
                    "finalizer lock payload changed during release; refusing cleanup"
                )
            release_path.unlink()
        except BaseException:
            if (
                release_path.exists()
                and not lock_path.exists()
                and not _is_link_like(lock_path)
            ):
                with suppress(OSError):
                    os.rename(release_path, lock_path)
            raise


def _remove_owned_staging(path: Path, owned_token: tuple[int, int]) -> None:
    quarantined = _quarantine_owned_path(
        path,
        owned_token=owned_token,
        purpose="staging-cleanup",
    )
    # Refuse recursive cleanup if any entry was replaced with a reparse point.
    for _path, _kind in _walk_tree(quarantined):
        pass
    shutil.rmtree(quarantined)


def _require_regular_file(path: Path, *, label: str) -> Path:
    if _is_link_like(path) or not path.is_file():
        raise StreamingPostrunError(f"{label} must be a non-link regular file: {path}")
    return path


def _require_directory(path: Path, *, label: str) -> Path:
    if _is_link_like(path) or not path.is_dir():
        raise StreamingPostrunError(f"{label} must be a non-link directory: {path}")
    return path


def _safe_member(root: Path, relative: str, *, file: bool) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise StreamingPostrunError(f"unsafe relative path in frozen contract: {relative}")
    current = root
    for part in parts:
        current = current / part
        if _is_link_like(current):
            raise StreamingPostrunError(f"link-like path is forbidden: {relative}")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise StreamingPostrunError(f"path escapes the declared root: {relative}") from error
    if file:
        return _require_regular_file(current, label=relative)
    return _require_directory(current, label=relative)


def _read_bounded(path: Path, *, limit: int = _MAX_CONTRACT_BYTES) -> bytes:
    _require_regular_file(path, label="contract")
    before = _stat_token(path)
    output = bytearray()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise StreamingPostrunError(
                    f"bounded contract limit exceeded ({limit} bytes): {path}"
                )
    if _stat_token(path) != before or len(output) != before[0]:
        raise StreamingPostrunError(f"contract changed during bounded read: {path}")
    return bytes(output)


def _read_contract[ModelT: ContractModel](
    path: Path, model: type[ModelT]
) -> tuple[ModelT, bytes]:
    raw = _read_bounded(path)
    try:
        value = model.model_validate_json(raw)
    except (ValueError, ValidationError) as error:
        raise StreamingPostrunError(f"invalid contract file {path.name}: {error}") from error
    if canonical_bytes(value) != raw:
        raise StreamingPostrunError(f"contract file is not exact canonical JSON: {path.name}")
    return value, raw


def _pin_contract[ModelT: ContractModel](
    path: Path, model: type[ModelT]
) -> tuple[ModelT, SourceContractPin]:
    value, raw = _read_contract(path, model)
    return value, SourceContractPin(
        path=path,
        raw=raw,
        stat_token=_stat_token(path),
        model=model,
    )


def _stat_token(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        metadata.st_size,
        metadata.st_mtime_ns,
        getattr(metadata, "st_dev", 0),
        getattr(metadata, "st_ino", 0),
    )


def _hash_file(path: Path) -> tuple[int, str]:
    _require_regular_file(path, label="artifact")
    before = _stat_token(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    if _stat_token(path) != before or size != before[0]:
        raise StreamingPostrunError(f"file changed during streaming read: {path}")
    return size, digest.hexdigest()


def _file_identity(path: Path) -> FileIdentity:
    size, digest = _hash_file(path)
    token = _stat_token(path)
    return FileIdentity(
        size_bytes=size,
        mtime_ns=token[1],
        sha256=digest,
        device=token[2],
        inode=token[3],
    )


def _write_new_file(path: Path, producer: Callable[[_HashingWriter], None]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        writer = _HashingWriter(stream)
        producer(writer)
        stream.flush()
        os.fsync(stream.fileno())
    return writer.size_bytes, writer.sha256


def _write_fixed_bytes(path: Path, payload: bytes) -> tuple[int, str]:
    return _write_new_file(path, lambda writer: writer.write(payload))


def _emit_canonical_object(
    writer: _HashingWriter, fields: Mapping[str, bytes | _Emitter]
) -> None:
    writer.write(b"{")
    for index, key in enumerate(sorted(fields)):
        if index:
            writer.write(b",")
        writer.write(canonical_bytes(key))
        writer.write(b":")
        value = fields[key]
        if callable(value):
            value(writer)
        else:
            writer.write(value)
    writer.write(b"}")


def _emit_canonical_array(writer: _HashingWriter, values: Iterator[bytes]) -> None:
    writer.write(b"[")
    for index, value in enumerate(values):
        if index:
            writer.write(b",")
        writer.write(value)
    writer.write(b"]")


def _canonical_array_hash(values: Iterator[bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, value in enumerate(values):
        if index:
            digest.update(b",")
        digest.update(value)
    digest.update(b"]")
    return digest.hexdigest()


def _reject_private_reasoning(value: object, *, location: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in _PRIVATE_REASONING_KEYS:
                raise StreamingPostrunError(
                    f"private reasoning field is outside the evidence contract: {location}"
                )
            _reject_private_reasoning(item, location=f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_private_reasoning(item, location=f"{location}[{index}]")


def _transient_paths(ledger_path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{ledger_path}{suffix}") for suffix in _SQLITE_TRANSIENT_SUFFIXES)


def _require_no_sqlite_transients(ledger_path: Path) -> None:
    present = tuple(
        path for path in _transient_paths(ledger_path) if path.exists() or _is_link_like(path)
    )
    if present:
        names = ", ".join(path.name for path in present)
        raise StreamingPostrunError(
            "ledger verification requires a closed, checkpointed writer; "
            f"found transient SQLite state: {names}"
        )


def _initialize_index(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = FILE")
    connection.execute("PRAGMA cache_size = -2048")
    connection.execute("PRAGMA mmap_size = 0")
    connection.executescript(
        """
        CREATE TABLE objects (
            payload_hash TEXT PRIMARY KEY
        ) WITHOUT ROWID;
        CREATE TABLE receipts (
            receipt_id TEXT PRIMARY KEY,
            occurrence_id TEXT NOT NULL UNIQUE,
            receipt_hash TEXT NOT NULL UNIQUE
        ) WITHOUT ROWID;
        CREATE TABLE domain_entries (
            relative_path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT
        ) WITHOUT ROWID;
        """
    )


@contextmanager
def _disk_index(parent: Path) -> Iterator[sqlite3.Connection]:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".strongwiz-postrun-index-", suffix=".sqlite3", dir=parent
    )
    os.close(descriptor)
    path = Path(raw_path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        _initialize_index(connection)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        for candidate in (path, *_transient_paths(path)):
            with suppress(FileNotFoundError):
                candidate.unlink()


def _index_has(connection: sqlite3.Connection, table: str, column: str, value: str) -> bool:
    if table not in {"objects", "receipts"} or column not in {"payload_hash", "receipt_id"}:
        raise AssertionError("internal index query is not allow-listed")
    row = connection.execute(
        f"SELECT 1 FROM {table} WHERE {column} = ?",
        (value,),
    ).fetchone()
    return row is not None


def _sqlite_int(value: object, *, label: str, nonnegative: bool = False) -> int:
    if type(value) is not int:
        raise StreamingPostrunError(f"SQLite {label} must be stored as INTEGER")
    result = int(value)
    if nonnegative and result < 0:
        raise StreamingPostrunError(f"SQLite {label} must be nonnegative")
    return result


def _sqlite_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise StreamingPostrunError(f"SQLite {label} must be stored as TEXT")
    return value


def _sqlite_blob(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes | bytearray | memoryview):
        raise StreamingPostrunError(f"SQLite {label} must be stored as BLOB")
    return bytes(value)


def _validate_sqlite_schema(connection: sqlite3.Connection) -> None:
    expected_columns = {
        "objects": (
            ("payload_hash", "TEXT", 0, 1),
            ("canonical_payload", "BLOB", 1, 0),
        ),
        "receipts": (
            ("sequence", "INTEGER", 0, 1),
            ("receipt_id", "TEXT", 1, 0),
            ("occurrence_id", "TEXT", 1, 0),
            ("kind", "TEXT", 1, 0),
            ("account_id", "TEXT", 1, 0),
            ("account_version", "INTEGER", 1, 0),
            ("payload_hash", "TEXT", 1, 0),
            ("envelope_json", "BLOB", 1, 0),
            ("receipt_hash", "TEXT", 1, 0),
        ),
    }
    tables = tuple(
        _sqlite_text(row[0], label="table name")
        for row in connection.execute(
            """SELECT name FROM sqlite_schema
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    )
    if tables != ("objects", "receipts"):
        raise StreamingPostrunError("SQLite ledger has an unsupported table schema")
    for table, expected in expected_columns.items():
        actual: list[tuple[str, str, int, int]] = []
        for row in connection.execute(f"PRAGMA table_info({table})"):
            if len(row) != 6:
                raise StreamingPostrunError("SQLite table metadata has an invalid shape")
            actual.append(
                (
                    _sqlite_text(row[1], label=f"{table} column name"),
                    _sqlite_text(row[2], label=f"{table} column type").upper(),
                    _sqlite_int(row[3], label=f"{table} not-null flag", nonnegative=True),
                    _sqlite_int(row[5], label=f"{table} primary-key flag", nonnegative=True),
                )
            )
        if tuple(actual) != expected:
            raise StreamingPostrunError(f"SQLite ledger {table} columns do not match v1")

    foreign_keys = tuple(
        (
            _sqlite_text(row[2], label="foreign-key table"),
            _sqlite_text(row[3], label="foreign-key source"),
            _sqlite_text(row[4], label="foreign-key target"),
        )
        for row in connection.execute("PRAGMA foreign_key_list(receipts)")
    )
    if foreign_keys != (("objects", "payload_hash", "payload_hash"),):
        raise StreamingPostrunError("SQLite ledger receipt payload foreign key is missing")

    unique_columns: set[str] = set()
    for index_row in connection.execute("PRAGMA index_list(receipts)"):
        if _sqlite_int(index_row[2], label="index uniqueness flag", nonnegative=True) != 1:
            continue
        index_name = _sqlite_text(index_row[1], label="index name")
        escaped_index_name = index_name.replace('"', '""')
        columns = tuple(
            _sqlite_text(info[2], label="indexed column")
            for info in connection.execute(f'PRAGMA index_info("{escaped_index_name}")')
        )
        if len(columns) == 1:
            unique_columns.add(columns[0])
    if unique_columns != {"receipt_id", "occurrence_id", "receipt_hash"}:
        raise StreamingPostrunError("SQLite ledger receipt uniqueness schema is incomplete")


def _object_line(stored_hash: object, stored_payload: object) -> tuple[str, bytes]:
    payload_hash = _sqlite_text(stored_hash, label="object payload hash")
    raw = _sqlite_blob(stored_payload, label="canonical object payload")
    if len(raw) > _MAX_JSONL_LINE_BYTES:
        raise StreamingPostrunError("ledger object exceeds the bounded row limit")
    try:
        payload = parse_strict_json(raw)
        if canonical_bytes(payload) != raw:
            raise StreamingPostrunError("stored ledger object is not canonical JSON")
        item = CapsuleObject(payload_hash=payload_hash, payload=payload)
    except (ValueError, ValidationError) as error:
        raise StreamingPostrunError(f"invalid SQLite ledger object: {error}") from error
    _reject_private_reasoning(item.payload)
    return item.payload_hash, canonical_bytes(item)


def _receipt_line(row: Sequence[object]) -> tuple[ReceiptEnvelope, bytes]:
    if len(row) != 9:
        raise StreamingPostrunError("SQLite receipt row has an invalid shape")
    (
        sequence,
        receipt_id,
        occurrence_id,
        kind,
        account_id,
        account_version,
        payload_hash,
        envelope_json,
        receipt_hash,
    ) = row
    sequence_value = _sqlite_int(sequence, label="receipt sequence", nonnegative=True)
    receipt_id_value = _sqlite_text(receipt_id, label="receipt id")
    occurrence_id_value = _sqlite_text(occurrence_id, label="receipt occurrence id")
    kind_value = _sqlite_text(kind, label="receipt kind")
    account_id_value = _sqlite_text(account_id, label="receipt account id")
    account_version_value = _sqlite_int(
        account_version, label="receipt account version", nonnegative=True
    )
    payload_hash_value = _sqlite_text(payload_hash, label="receipt payload hash")
    receipt_hash_value = _sqlite_text(receipt_hash, label="receipt hash")
    raw = _sqlite_blob(envelope_json, label="receipt envelope")
    if len(raw) > _MAX_JSONL_LINE_BYTES:
        raise StreamingPostrunError("receipt envelope exceeds the bounded row limit")
    try:
        envelope = ReceiptEnvelope.model_validate_json(raw)
    except (ValueError, ValidationError) as error:
        raise StreamingPostrunError(f"invalid SQLite receipt envelope: {error}") from error
    if canonical_bytes(envelope) != raw:
        raise StreamingPostrunError("stored receipt envelope is not canonical JSON")
    table_projection = (
        sequence_value,
        receipt_id_value,
        occurrence_id_value,
        kind_value,
        account_id_value,
        account_version_value,
        payload_hash_value,
        receipt_hash_value,
    )
    envelope_projection = (
        envelope.sequence,
        envelope.receipt_id,
        envelope.occurrence_id,
        envelope.kind,
        envelope.account_id,
        envelope.account_version,
        envelope.payload_hash,
        envelope.receipt_hash,
    )
    if table_projection != envelope_projection:
        raise StreamingPostrunError("receipt table projection disagrees with its envelope")
    expected_id = content_hash(
        {
            "account_id": envelope.account_id,
            "account_version": envelope.account_version,
            "kind": envelope.kind,
            "object_refs": list(envelope.object_refs),
            "occurrence_id": envelope.occurrence_id,
            "parent_refs": list(envelope.parent_refs),
            "payload_hash": envelope.payload_hash,
        }
    )
    if envelope.receipt_id != expected_id:
        raise StreamingPostrunError("receipt identity disagrees with its content binding")
    return envelope, canonical_bytes(envelope)


def _insert_receipt_index(connection: sqlite3.Connection, envelope: ReceiptEnvelope) -> None:
    try:
        connection.execute(
            "INSERT INTO receipts(receipt_id, occurrence_id, receipt_hash) VALUES (?, ?, ?)",
            (envelope.receipt_id, envelope.occurrence_id, envelope.receipt_hash),
        )
    except sqlite3.IntegrityError as error:
        raise StreamingPostrunError("receipt violates identity uniqueness") from error


def _validate_receipt_closure(
    connection: sqlite3.Connection,
    envelope: ReceiptEnvelope,
    *,
    expected_sequence: int,
    previous: str | None,
) -> None:
    if envelope.sequence != expected_sequence:
        raise StreamingPostrunError("receipt sequence is not contiguous")
    if envelope.previous_receipt_hash != previous:
        raise StreamingPostrunError("receipt chain predecessor mismatch")
    if not _index_has(connection, "objects", "payload_hash", envelope.payload_hash):
        raise StreamingPostrunError("receipt payload object is missing")
    for reference in envelope.object_refs:
        if not _index_has(connection, "objects", "payload_hash", reference):
            raise StreamingPostrunError("receipt content reference is missing")
    for reference in envelope.parent_refs:
        if not _index_has(connection, "receipts", "receipt_id", reference):
            raise StreamingPostrunError("receipt parent reference is missing or forward")


def _read_sqlite_blob(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
    rowid: int,
    declared_size: int,
) -> bytes:
    if declared_size < 0 or declared_size > _MAX_JSONL_LINE_BYTES:
        raise StreamingPostrunError("SQLite ledger row exceeds the bounded row limit")
    output = bytearray()
    try:
        with connection.blobopen(table, column, rowid, readonly=True) as blob:
            while True:
                remaining = declared_size - len(output)
                if remaining == 0:
                    break
                chunk = blob.read(min(_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                output.extend(chunk)
    except sqlite3.Error as error:
        raise StreamingPostrunError(f"cannot stream SQLite ledger BLOB: {error}") from error
    if len(output) != declared_size:
        raise StreamingPostrunError("SQLite ledger BLOB length changed during read")
    return bytes(output)


def _stream_source_ledger(
    ledger_path: Path,
    objects_path: Path,
    receipts_path: Path,
    index: sqlite3.Connection,
    *,
    terminal_ref: str,
) -> LedgerSealData:
    _require_no_sqlite_transients(ledger_path)
    uri = f"{ledger_path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
    try:
        source = sqlite3.connect(uri, uri=True)
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA temp_store = FILE")
        source.execute("PRAGMA cache_size = -2048")
        source.execute("PRAGMA mmap_size = 0")
        source.execute("BEGIN")
    except sqlite3.Error as error:
        raise StreamingPostrunError(
            f"cannot open the SQLite ledger read-only: {error}"
        ) from error

    object_projection = hashlib.sha256()
    object_projection.update(b"[")
    object_file_digest = hashlib.sha256()
    object_file_size = 0
    object_count = 0
    terminal_object_present = False
    previous_object: str | None = None

    try:
        _validate_sqlite_schema(source)
        with objects_path.open("xb") as object_stream:
            for row in source.execute(
                """SELECT rowid, payload_hash, length(canonical_payload),
                          typeof(canonical_payload)
                   FROM objects ORDER BY payload_hash"""
            ):
                if len(row) != 4:
                    raise StreamingPostrunError("SQLite object row has an invalid shape")
                if _sqlite_text(row[3], label="object payload storage class") != "blob":
                    raise StreamingPostrunError(
                        "SQLite canonical object payload must be stored as BLOB"
                    )
                raw_payload = _read_sqlite_blob(
                    source,
                    table="objects",
                    column="canonical_payload",
                    rowid=_sqlite_int(row[0], label="object rowid", nonnegative=True),
                    declared_size=_sqlite_int(
                        row[2], label="object payload length", nonnegative=True
                    ),
                )
                payload_hash, line = _object_line(row[1], raw_payload)
                if len(line) > _MAX_JSONL_LINE_BYTES:
                    raise StreamingPostrunError("capsule object exceeds the bounded row limit")
                if previous_object is not None and payload_hash <= previous_object:
                    raise StreamingPostrunError("ledger objects are not sorted and unique")
                try:
                    index.execute(
                        "INSERT INTO objects(payload_hash) VALUES (?)", (payload_hash,)
                    )
                except sqlite3.IntegrityError as error:
                    raise StreamingPostrunError(
                        "duplicate object identity in SQLite ledger"
                    ) from error
                if object_count:
                    object_projection.update(b",")
                object_projection.update(line)
                object_stream.write(line)
                object_stream.write(b"\n")
                object_file_digest.update(line)
                object_file_digest.update(b"\n")
                object_file_size += len(line) + 1
                object_count += 1
                previous_object = payload_hash
                terminal_object_present |= payload_hash == terminal_ref
            object_stream.flush()
            os.fsync(object_stream.fileno())
        object_projection.update(b"]")
        index.commit()

        receipt_projection = hashlib.sha256()
        receipt_projection.update(b"[")
        receipt_file_digest = hashlib.sha256()
        receipt_file_size = 0
        receipt_count = 0
        previous_receipt: str | None = None
        terminal_receipt_present = False
        with receipts_path.open("xb") as receipt_stream:
            cursor = source.execute(
                """SELECT rowid, sequence, receipt_id, occurrence_id, kind,
                          account_id, account_version, payload_hash,
                          length(envelope_json), typeof(envelope_json), receipt_hash
                   FROM receipts ORDER BY sequence"""
            )
            for row in cursor:
                if len(row) != 11:
                    raise StreamingPostrunError("SQLite receipt row has an invalid shape")
                if _sqlite_text(row[9], label="receipt envelope storage class") != "blob":
                    raise StreamingPostrunError(
                        "SQLite receipt envelope must be stored as BLOB"
                    )
                raw_envelope = _read_sqlite_blob(
                    source,
                    table="receipts",
                    column="envelope_json",
                    rowid=_sqlite_int(row[0], label="receipt rowid", nonnegative=True),
                    declared_size=_sqlite_int(
                        row[8], label="receipt envelope length", nonnegative=True
                    ),
                )
                envelope, line = _receipt_line((*row[1:8], raw_envelope, row[10]))
                if len(line) > _MAX_JSONL_LINE_BYTES:
                    raise StreamingPostrunError("capsule receipt exceeds the bounded row limit")
                _validate_receipt_closure(
                    index,
                    envelope,
                    expected_sequence=receipt_count,
                    previous=previous_receipt,
                )
                _insert_receipt_index(index, envelope)
                if receipt_count:
                    receipt_projection.update(b",")
                receipt_projection.update(line)
                receipt_stream.write(line)
                receipt_stream.write(b"\n")
                receipt_file_digest.update(line)
                receipt_file_digest.update(b"\n")
                receipt_file_size += len(line) + 1
                receipt_count += 1
                previous_receipt = envelope.receipt_hash
                terminal_receipt_present |= (
                    envelope.payload_hash == terminal_ref
                    or terminal_ref in envelope.object_refs
                )
            receipt_stream.flush()
            os.fsync(receipt_stream.fileno())
        receipt_projection.update(b"]")
        index.commit()
    except sqlite3.Error as error:
        raise StreamingPostrunError(f"cannot stream the SQLite ledger: {error}") from error
    finally:
        source.close()
    _require_no_sqlite_transients(ledger_path)
    return LedgerSealData(
        receipt_count=receipt_count,
        receipt_head=previous_receipt,
        object_count=object_count,
        objects_projection_ref=object_projection.hexdigest(),
        receipts_projection_ref=receipt_projection.hexdigest(),
        terminal_object_present=terminal_object_present,
        terminal_receipt_present=terminal_receipt_present,
        objects_file_size=object_file_size,
        objects_file_sha256=object_file_digest.hexdigest(),
        receipts_file_size=receipt_file_size,
        receipts_file_sha256=receipt_file_digest.hexdigest(),
    )


def _walk_tree(root: Path) -> Iterator[tuple[Path, str]]:
    _require_directory(root, label="tree root")

    def visit(directory: Path) -> Iterator[tuple[Path, str]]:
        with os.scandir(directory) as scanner:
            children = sorted(scanner, key=lambda item: item.name)
        for child in children:
            path = Path(child.path)
            if child.is_symlink() or _is_link_like(path):
                raise StreamingPostrunError(f"link-like path is forbidden: {path}")
            if child.is_dir(follow_symlinks=False):
                yield path, "directory"
                yield from visit(path)
            elif child.is_file(follow_symlinks=False):
                yield path, "file"
            else:
                raise StreamingPostrunError(f"special filesystem entry is forbidden: {path}")

    yield from visit(root)


def _copy_and_hash(source: Path, destination: Path) -> tuple[int, str]:
    before = _stat_token(source)
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while True:
            chunk = reader.read(_CHUNK_BYTES)
            if not chunk:
                break
            writer.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if _stat_token(source) != before or size != before[0]:
        raise StreamingPostrunError(f"opaque domain file changed while copied: {source}")
    return size, digest.hexdigest()


def _domain_entry_bytes(
    relative_path: str, kind: str, size_bytes: int, sha256: str | None
) -> bytes:
    return canonical_bytes(
        {
            "kind": kind,
            "relative_path": relative_path,
            "sha256": sha256,
            "size_bytes": size_bytes,
        }
    )


def _snapshot_domain(
    source_root: Path,
    destination_root: Path,
    index: sqlite3.Connection,
) -> DomainSealData:
    destination_root.mkdir()
    count = 0
    for source, kind in _walk_tree(source_root):
        relative = source.relative_to(source_root).as_posix()
        destination = destination_root.joinpath(*PurePosixPath(relative).parts)
        if kind == "directory":
            destination.mkdir()
            size_bytes = 0
            digest: str | None = None
        else:
            size_bytes, digest = _copy_and_hash(source, destination)
        try:
            index.execute(
                """INSERT INTO domain_entries(relative_path, kind, size_bytes, sha256)
                   VALUES (?, ?, ?, ?)""",
                (relative, kind, size_bytes, digest),
            )
        except sqlite3.IntegrityError as error:
            raise StreamingPostrunError("duplicate domain-state path") from error
        count += 1
    index.commit()
    projection = _canonical_array_hash(
        _domain_entry_bytes(
            str(path), str(kind), int(size), None if digest is None else str(digest)
        )
        for path, kind, size, digest in index.execute(
            """SELECT relative_path, kind, size_bytes, sha256
               FROM domain_entries ORDER BY relative_path"""
        )
    )
    return DomainSealData(entry_count=count, projection_ref=projection)


def _verify_domain_against_seal(
    source_root: Path,
    expected: ExternalDomainStateSeal,
    *,
    scratch_parent: Path,
) -> None:
    with _disk_index(scratch_parent) as observed:
        for source, kind in _walk_tree(source_root):
            relative = source.relative_to(source_root).as_posix()
            if kind == "directory":
                size_bytes = 0
                digest: str | None = None
            else:
                size_bytes, digest = _hash_file(source)
            observed.execute(
                """INSERT INTO domain_entries(relative_path, kind, size_bytes, sha256)
                   VALUES (?, ?, ?, ?)""",
                (relative, kind, size_bytes, digest),
            )
        observed.commit()
        observed_cursor = observed.execute(
            """SELECT relative_path, kind, size_bytes, sha256
               FROM domain_entries ORDER BY relative_path"""
        )
        try:
            for entry in expected.entries:
                observed_row = observed_cursor.fetchone()
                expected_row = (
                    entry.relative_path,
                    entry.kind,
                    entry.size_bytes,
                    entry.sha256,
                )
                if observed_row != expected_row:
                    raise StreamingPostrunError(
                        "domain state changed during post-run verification"
                    )
            if observed_cursor.fetchone() is not None:
                raise StreamingPostrunError("domain state gained undeclared entries")
        finally:
            observed_cursor.close()


def _require_terminal_domain_entry(
    seal: ExternalDomainStateSeal, *, terminal_ref: str, terminal_size: int
) -> None:
    matching = tuple(
        entry for entry in seal.entries if entry.relative_path == _TERMINAL_DOMAIN_RELATIVE
    )
    if len(matching) != 1:
        raise StreamingPostrunError(
            "domain seal must contain exactly one terminal.record.json entry"
        )
    entry = matching[0]
    if (
        entry.kind != "file"
        or entry.size_bytes != terminal_size
        or entry.sha256 != terminal_ref
    ):
        raise StreamingPostrunError(
            "domain terminal.record.json bytes disagree with the terminal record identity"
        )


def _require_indexed_terminal_domain_entry(
    index: sqlite3.Connection, *, terminal_ref: str, terminal_size: int
) -> None:
    row = index.execute(
        """SELECT kind, size_bytes, sha256 FROM domain_entries
           WHERE relative_path = ?""",
        (_TERMINAL_DOMAIN_RELATIVE,),
    ).fetchone()
    if row != ("file", terminal_size, terminal_ref):
        raise StreamingPostrunError(
            "domain terminal.record.json bytes disagree with the terminal record identity"
        )


def _recheck_source_before_publication(
    *,
    run_root: Path,
    manifest: LabManifest,
    pins: tuple[SourceContractPin, ...],
    ledger_path: Path,
    ledger_identity: FileIdentity,
    domain_path: Path,
    domain_seal: ExternalDomainStateSeal,
    live_lock: Path,
    terminal_ref: str,
    terminal_size: int,
    scratch_parent: Path,
) -> None:
    _require_no_link_components(run_root, label="resolved run root")
    _require_no_link_components(ledger_path, label="resolved ledger")
    _require_no_link_components(domain_path, label="resolved domain state")
    _verify_source_structure(run_root, manifest)
    if live_lock.exists() or _is_link_like(live_lock):
        raise StreamingPostrunError("terminal run reacquired a live calibration lock")
    for pin in pins:
        _, raw = _read_contract(pin.path, pin.model)
        if raw != pin.raw or _stat_token(pin.path) != pin.stat_token:
            raise StreamingPostrunError(
                f"pinned source contract changed before publication: {pin.path.name}"
            )
    _require_no_sqlite_transients(ledger_path)
    if _file_identity(ledger_path) != ledger_identity:
        raise StreamingPostrunError("source SQLite ledger changed before publication")
    _verify_domain_against_seal(
        domain_path,
        domain_seal,
        scratch_parent=scratch_parent,
    )
    _require_terminal_domain_entry(
        domain_seal,
        terminal_ref=terminal_ref,
        terminal_size=terminal_size,
    )
    if live_lock.exists() or _is_link_like(live_lock):
        raise StreamingPostrunError("terminal run reacquired a live calibration lock")
    for pin in pins:
        if _stat_token(pin.path) != pin.stat_token:
            raise StreamingPostrunError(
                f"pinned source contract changed during final recheck: {pin.path.name}"
            )
    expected_ledger_token = (
        ledger_identity.size_bytes,
        ledger_identity.mtime_ns,
        ledger_identity.device,
        ledger_identity.inode,
    )
    if _stat_token(ledger_path) != expected_ledger_token:
        raise StreamingPostrunError("source SQLite ledger changed during final recheck")
    _require_no_sqlite_transients(ledger_path)
    _verify_source_structure(run_root, manifest)


def _emit_domain_seal(
    writer: _HashingWriter, index: sqlite3.Connection, seal: DomainSealData
) -> None:
    def entries(output: _HashingWriter) -> None:
        _emit_canonical_array(
            output,
            (
                _domain_entry_bytes(
                    str(path), str(kind), int(size), None if digest is None else str(digest)
                )
                for path, kind, size, digest in index.execute(
                    """SELECT relative_path, kind, size_bytes, sha256
                       FROM domain_entries ORDER BY relative_path"""
                )
            ),
        )

    _emit_canonical_object(
        writer,
        {
            "content_handling": canonical_bytes("opaque_unsanitized_bytes"),
            "entries": entries,
            "entry_count": canonical_bytes(seal.entry_count),
            "projection_ref": canonical_bytes(seal.projection_ref),
        },
    )


def _write_run_seal(
    path: Path,
    *,
    manifest: LabManifest,
    spec: RunSpec,
    genesis: LabGenesisSeal,
    terminal: RunTerminalRecord,
    ledger: LedgerSealData,
    domain: DomainSealData,
    index: sqlite3.Connection,
) -> FrozenFile:
    summary = (
        "declared terminal success observed and assessed through Strongwiz"
        if terminal.completion_genuinely_observed
        else "run stopped without an earned official WIN claim"
    )

    def domain_emitter(writer: _HashingWriter) -> None:
        _emit_domain_seal(writer, index, domain)

    size, digest = _write_new_file(
        path,
        lambda writer: _emit_canonical_object(
            writer,
            {
                "authority": canonical_bytes("EVIDENCE_ONLY"),
                "claim_ceiling": canonical_bytes("declared_terminal_observation_only"),
                "completion_genuinely_observed": canonical_bytes(
                    terminal.completion_genuinely_observed
                ),
                "concise_result_summary": canonical_bytes(summary),
                "disposition": canonical_bytes(terminal.disposition),
                "domain_state_seal": domain_emitter,
                "effect": canonical_bytes("NONE"),
                "genesis_ref": canonical_bytes(genesis.digest),
                "lab_manifest_ref": canonical_bytes(manifest.digest),
                "ledger_seal": canonical_bytes(ledger.canonical_value()),
                "reasoning_record_policy": canonical_bytes("concise_summary_only"),
                "reasoning_record_policy_scope": canonical_bytes(
                    "typed_strongwiz_records_only"
                ),
                "run_id": canonical_bytes(spec.run_id),
                "run_spec_ref": canonical_bytes(spec.digest),
                "schema": canonical_bytes("strongwiz.run-seal.v1"),
                "terminal_authority_source": canonical_bytes(spec.terminal_authority_source),
                "terminal_evidence_ref": canonical_bytes(terminal.digest),
                "terminal_state": canonical_bytes(terminal.final_state),
            },
        ),
    )
    return FrozenFile(
        relative_path=CAPSULE_RUN_SEAL_PATH,
        role=CapsuleFileRole.RUN_SEAL,
        size_bytes=size,
        sha256=digest,
    )


def _capsule_file_bytes(file: FrozenFile) -> bytes:
    return canonical_bytes(
        CapsuleFile(
            role=file.role,
            relative_path=file.relative_path,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
        )
    )


def _write_capsule_manifest(
    path: Path,
    *,
    manifest: LabManifest,
    spec: RunSpec,
    genesis: LabGenesisSeal,
    terminal: RunTerminalRecord,
    ledger: LedgerSealData,
    domain: DomainSealData,
    index: sqlite3.Connection,
    files: tuple[FrozenFile, ...],
    run_seal_ref: str,
) -> WrittenArtifact:
    ordered_files = tuple(sorted(files, key=lambda item: item.relative_path))

    def domain_emitter(writer: _HashingWriter) -> None:
        _emit_domain_seal(writer, index, domain)

    def files_emitter(writer: _HashingWriter) -> None:
        _emit_canonical_array(writer, (_capsule_file_bytes(item) for item in ordered_files))

    size, digest = _write_new_file(
        path,
        lambda writer: _emit_canonical_object(
            writer,
            {
                "authority": canonical_bytes("NONE"),
                "capsule_name": canonical_bytes(f"strongwiz-arc3-{terminal.run_id}"),
                "claim_ceiling": canonical_bytes("evidence_only"),
                "complete_domain_state_projection": canonical_bytes(True),
                "complete_sqlite_projection": canonical_bytes(True),
                "completion_genuinely_observed": canonical_bytes(
                    terminal.completion_genuinely_observed
                ),
                "domain_state_disclosure_status": canonical_bytes(
                    "opaque_unsanitized_not_publication_reviewed"
                ),
                "domain_state_seal": domain_emitter,
                "effect": canonical_bytes("NONE"),
                "files": files_emitter,
                "genesis_ref": canonical_bytes(genesis.digest),
                "lab_id": canonical_bytes(manifest.lab_id),
                "lab_manifest_ref": canonical_bytes(manifest.digest),
                "ledger_seal": canonical_bytes(ledger.canonical_value()),
                "opaque_domain_state_copy_acknowledged": canonical_bytes(
                    domain.entry_count > 0
                ),
                "reasoning_record_policy": canonical_bytes("concise_summary_only"),
                "reasoning_record_policy_scope": canonical_bytes(
                    "typed_strongwiz_records_only"
                ),
                "run_id": canonical_bytes(spec.run_id),
                "run_seal_ref": canonical_bytes(run_seal_ref),
                "run_spec_ref": canonical_bytes(spec.digest),
                "schema": canonical_bytes("strongwiz.evidence-capsule.v1"),
                "self_authorizing": canonical_bytes(False),
                "terminal_evidence_ref": canonical_bytes(terminal.digest),
            },
        ),
    )
    return WrittenArtifact(size_bytes=size, sha256=digest)


def _expected_parent_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for value in paths:
        parent = PurePosixPath(value).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _scan_paths(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for path, kind in _walk_tree(root):
        relative = path.relative_to(root).as_posix()
        if kind == "file":
            files.add(relative)
        else:
            directories.add(relative)
    return files, directories


def _iter_bounded_jsonl(path: Path) -> Iterator[bytes]:
    _require_regular_file(path, label="capsule JSONL")
    buffer = bytearray()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                if newline > _MAX_JSONL_LINE_BYTES:
                    raise StreamingPostrunError("capsule JSONL line exceeds the bounded limit")
                line = bytes(buffer[:newline])
                del buffer[: newline + 1]
                if not line:
                    raise StreamingPostrunError("capsule JSONL contains an empty line")
                yield line
            if len(buffer) > _MAX_JSONL_LINE_BYTES:
                raise StreamingPostrunError("capsule JSONL line exceeds the bounded limit")
    if buffer:
        raise StreamingPostrunError("capsule JSONL must end with one LF")


def _parse_capsule_object_line(line: bytes) -> CapsuleObject:
    try:
        item = CapsuleObject.model_validate_json(line)
    except (ValueError, ValidationError) as error:
        raise StreamingPostrunError(f"invalid capsule object JSONL: {error}") from error
    if canonical_bytes(item) != line:
        raise StreamingPostrunError("capsule object JSONL is not canonical")
    _reject_private_reasoning(item.payload)
    return item


def _parse_capsule_receipt_line(line: bytes) -> ReceiptEnvelope:
    try:
        item = ReceiptEnvelope.model_validate_json(line)
    except (ValueError, ValidationError) as error:
        raise StreamingPostrunError(f"invalid capsule receipt JSONL: {error}") from error
    if canonical_bytes(item) != line:
        raise StreamingPostrunError("capsule receipt JSONL is not canonical")
    expected_id = content_hash(
        {
            "account_id": item.account_id,
            "account_version": item.account_version,
            "kind": item.kind,
            "object_refs": list(item.object_refs),
            "occurrence_id": item.occurrence_id,
            "parent_refs": list(item.parent_refs),
            "payload_hash": item.payload_hash,
        }
    )
    if item.receipt_id != expected_id:
        raise StreamingPostrunError("exported receipt identity disagrees with its binding")
    return item


def _verify_exported_ledger(
    objects_path: Path,
    receipts_path: Path,
    index: sqlite3.Connection,
    *,
    terminal_ref: str,
) -> LedgerSealData:
    object_hash = hashlib.sha256()
    object_hash.update(b"[")
    object_file_hash = hashlib.sha256()
    object_file_size = 0
    object_count = 0
    previous_object: str | None = None
    terminal_object = False
    for line in _iter_bounded_jsonl(objects_path):
        object_item = _parse_capsule_object_line(line)
        if previous_object is not None and object_item.payload_hash <= previous_object:
            raise StreamingPostrunError("capsule objects must be sorted and unique")
        try:
            index.execute(
                "INSERT INTO objects(payload_hash) VALUES (?)", (object_item.payload_hash,)
            )
        except sqlite3.IntegrityError as error:
            raise StreamingPostrunError("capsule objects must be unique") from error
        if object_count:
            object_hash.update(b",")
        object_hash.update(line)
        object_file_hash.update(line)
        object_file_hash.update(b"\n")
        object_file_size += len(line) + 1
        object_count += 1
        previous_object = object_item.payload_hash
        terminal_object |= object_item.payload_hash == terminal_ref
    object_hash.update(b"]")
    index.commit()

    receipt_hash = hashlib.sha256()
    receipt_hash.update(b"[")
    receipt_file_hash = hashlib.sha256()
    receipt_file_size = 0
    receipt_count = 0
    previous_receipt: str | None = None
    terminal_receipt = False
    for line in _iter_bounded_jsonl(receipts_path):
        receipt_item = _parse_capsule_receipt_line(line)
        _validate_receipt_closure(
            index,
            receipt_item,
            expected_sequence=receipt_count,
            previous=previous_receipt,
        )
        _insert_receipt_index(index, receipt_item)
        if receipt_count:
            receipt_hash.update(b",")
        receipt_hash.update(line)
        receipt_file_hash.update(line)
        receipt_file_hash.update(b"\n")
        receipt_file_size += len(line) + 1
        receipt_count += 1
        previous_receipt = receipt_item.receipt_hash
        terminal_receipt |= (
            receipt_item.payload_hash == terminal_ref
            or terminal_ref in receipt_item.object_refs
        )
    receipt_hash.update(b"]")
    index.commit()
    return LedgerSealData(
        receipt_count=receipt_count,
        receipt_head=previous_receipt,
        object_count=object_count,
        objects_projection_ref=object_hash.hexdigest(),
        receipts_projection_ref=receipt_hash.hexdigest(),
        terminal_object_present=terminal_object,
        terminal_receipt_present=terminal_receipt,
        objects_file_size=object_file_size,
        objects_file_sha256=object_file_hash.hexdigest(),
        receipts_file_size=receipt_file_size,
        receipts_file_sha256=receipt_file_hash.hexdigest(),
    )


def _verify_capsule_domain(
    root: Path, capsule: EvidenceCapsuleManifest, index: sqlite3.Connection
) -> None:
    domain_root = _safe_member(root, CAPSULE_DOMAIN_STATE_PATH, file=False)
    for path, kind in _walk_tree(domain_root):
        relative = path.relative_to(domain_root).as_posix()
        if kind == "directory":
            size = 0
            digest: str | None = None
        else:
            size, digest = _hash_file(path)
        index.execute(
            """INSERT INTO domain_entries(relative_path, kind, size_bytes, sha256)
               VALUES (?, ?, ?, ?)""",
            (relative, kind, size, digest),
        )
    index.commit()
    cursor = index.execute(
        """SELECT relative_path, kind, size_bytes, sha256
           FROM domain_entries ORDER BY relative_path"""
    )
    try:
        for expected in capsule.domain_state_seal.entries:
            row = cursor.fetchone()
            expected_row = (
                expected.relative_path,
                expected.kind,
                expected.size_bytes,
                expected.sha256,
            )
            if row != expected_row:
                raise StreamingPostrunError(
                    "capsule domain-state projection disagrees with bytes"
                )
        if cursor.fetchone() is not None:
            raise StreamingPostrunError("capsule has undeclared domain-state entries")
    finally:
        cursor.close()


def verify_evidence_capsule_streaming(
    root: str | Path, *, expected_capsule_ref: str | None = None
) -> EvidenceCapsuleManifest:
    """Verify a v1 evidence capsule without materializing either JSONL projection."""

    supplied = Path(root)
    _require_no_link_components(supplied, label="capsule root")
    resolved = _require_directory(supplied.resolve(strict=True), label="capsule root")
    _require_no_link_components(resolved, label="resolved capsule root")
    manifest_path = _safe_member(resolved, CAPSULE_MANIFEST_PATH, file=True)
    capsule, manifest_raw = _read_contract(manifest_path, EvidenceCapsuleManifest)
    capsule_ref = hashlib.sha256(manifest_raw).hexdigest()
    if expected_capsule_ref is not None and capsule_ref != expected_capsule_ref:
        raise StreamingPostrunError("capsule digest disagrees with the expected identity")

    expected_files = {
        CAPSULE_MANIFEST_PATH,
        *(item.relative_path for item in capsule.files),
        *(
            f"{CAPSULE_DOMAIN_STATE_PATH}/{entry.relative_path}"
            for entry in capsule.domain_state_seal.entries
            if entry.kind == "file"
        ),
    }
    expected_directories = _expected_parent_directories(expected_files)
    expected_directories.add(CAPSULE_DOMAIN_STATE_PATH)
    expected_directories.update(
        f"{CAPSULE_DOMAIN_STATE_PATH}/{entry.relative_path}"
        for entry in capsule.domain_state_seal.entries
        if entry.kind == "directory"
    )
    actual_files, actual_directories = _scan_paths(resolved)
    if actual_files != expected_files or actual_directories != expected_directories:
        raise StreamingPostrunError("capsule contains missing or undeclared paths")

    by_role = {item.role: item for item in capsule.files}
    for frozen in capsule.files:
        if frozen.role in {
            CapsuleFileRole.LEDGER_OBJECTS,
            CapsuleFileRole.LEDGER_RECEIPTS,
        }:
            continue
        path = _safe_member(resolved, frozen.relative_path, file=True)
        size, digest = _hash_file(path)
        if size != frozen.size_bytes or digest != frozen.sha256:
            raise StreamingPostrunError(
                f"capsule file disagrees with its exact digest: {frozen.relative_path}"
            )

    manifest, _ = _read_contract(
        _safe_member(resolved, by_role[CapsuleFileRole.LAB_MANIFEST].relative_path, file=True),
        LabManifest,
    )
    spec, _ = _read_contract(
        _safe_member(resolved, by_role[CapsuleFileRole.RUN_SPEC].relative_path, file=True),
        RunSpec,
    )
    genesis, _ = _read_contract(
        _safe_member(resolved, by_role[CapsuleFileRole.LAB_GENESIS].relative_path, file=True),
        LabGenesisSeal,
    )
    run_seal, _ = _read_contract(
        _safe_member(resolved, by_role[CapsuleFileRole.RUN_SEAL].relative_path, file=True),
        RunSeal,
    )

    with _disk_index(resolved.parent) as index:
        ledger = _verify_exported_ledger(
            _safe_member(
                resolved, by_role[CapsuleFileRole.LEDGER_OBJECTS].relative_path, file=True
            ),
            _safe_member(
                resolved, by_role[CapsuleFileRole.LEDGER_RECEIPTS].relative_path, file=True
            ),
            index,
            terminal_ref=run_seal.terminal_evidence_ref,
        )
        _verify_capsule_domain(resolved, capsule, index)

    for role, size, digest in (
        (
            CapsuleFileRole.LEDGER_OBJECTS,
            ledger.objects_file_size,
            ledger.objects_file_sha256,
        ),
        (
            CapsuleFileRole.LEDGER_RECEIPTS,
            ledger.receipts_file_size,
            ledger.receipts_file_sha256,
        ),
    ):
        frozen = by_role[role]
        if size != frozen.size_bytes or digest != frozen.sha256:
            raise StreamingPostrunError(
                f"capsule file disagrees with its exact digest: {frozen.relative_path}"
            )

    if not ledger.terminal_object_present or not ledger.terminal_receipt_present:
        raise StreamingPostrunError("terminal evidence is not closed by the capsule ledger")
    if (
        spec.lab_manifest_ref != manifest.digest
        or genesis.lab_manifest_ref != manifest.digest
        or genesis.run_spec_ref != spec.digest
        or run_seal.run_id != spec.run_id
        or run_seal.lab_manifest_ref != manifest.digest
        or run_seal.run_spec_ref != spec.digest
        or run_seal.genesis_ref != genesis.digest
        or run_seal.ledger_seal.model_dump(mode="json") != ledger.canonical_value()
        or run_seal.domain_state_seal != capsule.domain_state_seal
        or run_seal.terminal_authority_source != spec.terminal_authority_source
        or capsule.lab_id != manifest.lab_id
        or capsule.run_id != spec.run_id
        or capsule.lab_manifest_ref != manifest.digest
        or capsule.run_spec_ref != spec.digest
        or capsule.genesis_ref != genesis.digest
        or capsule.run_seal_ref != run_seal.digest
        or capsule.ledger_seal.model_dump(mode="json") != ledger.canonical_value()
        or capsule.terminal_evidence_ref != run_seal.terminal_evidence_ref
        or capsule.completion_genuinely_observed != run_seal.completion_genuinely_observed
    ):
        raise StreamingPostrunError("capsule cross-object bindings do not close")
    if run_seal.completion_genuinely_observed and run_seal.terminal_state != spec.success_state:
        raise StreamingPostrunError(
            "capsule completion does not match the declared success state"
        )
    return capsule


def _verify_source_structure(root: Path, manifest: LabManifest) -> None:
    layout = manifest.layout
    allowed_files = {
        layout.manifest_path,
        layout.run_spec_path,
        layout.genesis_path,
        layout.ledger_path,
    }
    seal_path = root.joinpath(*PurePosixPath(layout.run_seal_path).parts)
    if seal_path.exists():
        allowed_files.add(layout.run_seal_path)
    domain_relative = PurePosixPath(layout.domain_state_path)
    allowed_directories = _expected_parent_directories(allowed_files)
    allowed_directories.add(domain_relative.as_posix())
    allowed_directories.update(
        parent.as_posix() for parent in domain_relative.parents if parent.parts
    )

    actual_files: set[str] = set()
    actual_directories: set[str] = set()

    def visit(directory: Path) -> None:
        with os.scandir(directory) as scanner:
            children = sorted(scanner, key=lambda item: item.name)
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if child.is_symlink() or _is_link_like(path):
                raise StreamingPostrunError(f"link-like source path is forbidden: {relative}")
            if relative == domain_relative.as_posix():
                if not child.is_dir(follow_symlinks=False):
                    raise StreamingPostrunError("declared domain state is not a directory")
                actual_directories.add(relative)
                continue
            if child.is_dir(follow_symlinks=False):
                actual_directories.add(relative)
                visit(path)
            elif child.is_file(follow_symlinks=False):
                actual_files.add(relative)
            else:
                raise StreamingPostrunError(f"special source path is forbidden: {relative}")

    visit(root)
    if actual_files != allowed_files or actual_directories != allowed_directories:
        raise StreamingPostrunError("run root contains missing or undeclared control paths")


def _publish_immutable_file(
    path: Path, payload_path: Path
) -> tuple[bool, tuple[int, int] | None]:
    expected_size, expected_digest = _hash_file(payload_path)
    if path.exists():
        size, digest = _hash_file(path)
        if size != expected_size or digest != expected_digest:
            raise StreamingPostrunError(f"immutable artifact already differs: {path}")
        return False, None
    temporary = path.with_name(f".{path.name}.postrun-{os.getpid()}.tmp")
    if temporary.exists():
        raise StreamingPostrunError(
            f"stale post-run temporary file blocks publish: {temporary}"
        )
    try:
        copied_size, copied_digest = _copy_and_hash(payload_path, temporary)
        if copied_size != expected_size or copied_digest != expected_digest:
            raise StreamingPostrunError("artifact payload changed between verification passes")
        owned_token = _ownership_token(temporary)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            size, digest = _hash_file(path)
            if size != expected_size or digest != expected_digest:
                raise StreamingPostrunError(
                    f"immutable artifact appeared and differs: {path}"
                ) from error
            return False, None
        if _ownership_token(path) != owned_token:
            raise StreamingPostrunError("published artifact identity changed during link")
        return True, owned_token
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _atomic_write_receipt(path: Path, payload: bytes) -> bool:
    if path.exists():
        existing = _read_bounded(path)
        if existing != payload:
            raise StreamingPostrunError(f"existing delivery receipt differs: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.postrun-{os.getpid()}.tmp")
    if temporary.exists():
        raise StreamingPostrunError(f"stale receipt temporary file blocks publish: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        owned_token = _ownership_token(temporary)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            existing = _read_bounded(path)
            if existing != payload:
                raise StreamingPostrunError(
                    f"delivery receipt appeared and differs: {path}"
                ) from error
            return False
        if _ownership_token(path) != owned_token:
            raise StreamingPostrunError("published receipt identity changed during link")
        return True
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _artifact_pointer(path: Path) -> ArtifactPointer:
    resolved = path.resolve(strict=True)
    size, digest = _hash_file(resolved)
    return ArtifactPointer(path=str(resolved), sha256=digest, size_bytes=size)


def seal_and_pack_run(
    *,
    run_root: str | Path,
    capsule_root: str | Path,
    delivery_receipt_path: str | Path,
) -> PostrunResult:
    """Finalize one run under an exclusive destination-scoped transaction lock.

    The external receipt is the commit record.  Caught pre-commit failures try
    to roll back only transaction-owned outputs; receipt-committed outputs are
    retained and verified idempotently on retry.  An abrupt process stop can
    leave a lock or staging path that requires operator inspection, so the three
    filesystem destinations are not claimed to be one crash-atomic unit.
    """

    supplied_run = Path(run_root)
    supplied_capsule = Path(capsule_root)
    supplied_receipt = Path(delivery_receipt_path)
    for label, supplied in (
        ("run root", supplied_run),
        ("capsule destination", supplied_capsule),
        ("receipt destination", supplied_receipt),
    ):
        _require_no_link_components(supplied, label=label)
    run = supplied_run.resolve(strict=True)
    capsule = supplied_capsule.resolve(strict=False)
    receipt = supplied_receipt.resolve(strict=False)
    if not capsule.parent.is_dir() or _is_link_like(capsule.parent):
        raise StreamingPostrunError("capsule parent must be an existing non-link directory")
    with _destination_finalizer_lock(run=run, capsule=capsule, receipt=receipt):
        return _seal_and_pack_run_locked(
            run_root=run,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )


def _seal_and_pack_run_locked(
    *,
    run_root: str | Path,
    capsule_root: str | Path,
    delivery_receipt_path: str | Path,
) -> PostrunResult:
    """Verify one terminal closed run, seal it, and atomically publish its capsule."""

    supplied_run = Path(run_root)
    supplied_capsule = Path(capsule_root)
    supplied_receipt = Path(delivery_receipt_path)
    _require_no_link_components(supplied_run, label="run root")
    _require_no_link_components(supplied_capsule, label="capsule destination")
    _require_no_link_components(supplied_receipt, label="receipt destination")
    run = _require_directory(supplied_run.resolve(strict=True), label="run root")
    capsule = supplied_capsule.resolve(strict=False)
    receipt_path = supplied_receipt.resolve(strict=False)
    _require_no_link_components(run, label="resolved run root")
    _require_no_link_components(capsule, label="resolved capsule destination")
    _require_no_link_components(receipt_path, label="resolved receipt destination")
    if _is_link_like(capsule) or _is_link_like(receipt_path):
        raise StreamingPostrunError("capsule and receipt destinations must not be link-like")
    if capsule == run or run in capsule.parents or capsule in run.parents:
        raise StreamingPostrunError("capsule destination and sealed run must be disjoint")
    if receipt_path == run or run in receipt_path.parents:
        raise StreamingPostrunError("delivery receipt must remain outside the sealed run")
    if receipt_path == capsule or capsule in receipt_path.parents:
        raise StreamingPostrunError("delivery receipt must remain outside the evidence capsule")
    if not capsule.parent.is_dir() or _is_link_like(capsule.parent):
        raise StreamingPostrunError("capsule parent must be an existing non-link directory")

    manifest, manifest_pin = _pin_contract(
        _safe_member(run, CAPSULE_LAB_MANIFEST_PATH, file=True), LabManifest
    )
    spec, spec_pin = _pin_contract(
        _safe_member(run, manifest.layout.run_spec_path, file=True), RunSpec
    )
    genesis, genesis_pin = _pin_contract(
        _safe_member(run, manifest.layout.genesis_path, file=True), LabGenesisSeal
    )
    domain_relative = PurePosixPath(manifest.layout.domain_state_path)
    terminal_relative = (domain_relative / _TERMINAL_DOMAIN_RELATIVE).as_posix()
    live_lock_relative = (domain_relative / _LIVE_LOCK_DOMAIN_RELATIVE).as_posix()
    terminal_path = _safe_member(run, terminal_relative, file=True)
    terminal, terminal_pin = _pin_contract(terminal_path, RunTerminalRecord)
    control_pins = (manifest_pin, spec_pin, genesis_pin, terminal_pin)
    if (
        spec.lab_manifest_ref != manifest.digest
        or genesis.lab_manifest_ref != manifest.digest
        or genesis.run_spec_ref != spec.digest
        or terminal.run_id != spec.run_id
        or terminal.lab_genesis_ref != genesis.digest
        or terminal.frozen_runtime_ref != spec.frozen_runtime_ref
    ):
        raise StreamingPostrunError("terminal run control bindings do not close")
    live_lock = run.joinpath(*PurePosixPath(live_lock_relative).parts)
    if live_lock.exists() or _is_link_like(live_lock):
        raise StreamingPostrunError("terminal run still has a live calibration lock")
    ledger_path = _safe_member(run, manifest.layout.ledger_path, file=True)
    domain_path = _safe_member(run, manifest.layout.domain_state_path, file=False)
    _require_no_sqlite_transients(ledger_path)
    _verify_source_structure(run, manifest)
    ledger_before = _file_identity(ledger_path)

    staging_raw = tempfile.mkdtemp(prefix=f".{capsule.name}.postrun-", dir=capsule.parent)
    staging = Path(staging_raw).resolve(strict=True)
    staging_owned_token = _ownership_token(staging)
    source_seal_path = run.joinpath(*PurePosixPath(manifest.layout.run_seal_path).parts)
    source_seal_published_here = False
    source_seal_owned_token: tuple[int, int] | None = None
    capsule_published_here = False
    capsule_owned_token: tuple[int, int] | None = None
    receipt_payload: bytes | None = None
    receipt_commit_reached = False
    expected_source_seal_ref: str | None = None
    expected_capsule_ref: str | None = None
    try:
        with _disk_index(capsule.parent) as index:
            fixed: list[FrozenFile] = []
            for role, relative, payload in (
                (
                    CapsuleFileRole.LAB_MANIFEST,
                    CAPSULE_LAB_MANIFEST_PATH,
                    manifest_pin.raw,
                ),
                (CapsuleFileRole.RUN_SPEC, CAPSULE_RUN_SPEC_PATH, spec_pin.raw),
                (CapsuleFileRole.LAB_GENESIS, CAPSULE_GENESIS_PATH, genesis_pin.raw),
            ):
                size, digest = _write_fixed_bytes(staging / relative, payload)
                fixed.append(FrozenFile(relative, role, size, digest))

            objects_path = staging / CAPSULE_OBJECTS_PATH
            receipts_path = staging / CAPSULE_RECEIPTS_PATH
            objects_path.parent.mkdir(parents=True)
            ledger = _stream_source_ledger(
                ledger_path,
                objects_path,
                receipts_path,
                index,
                terminal_ref=terminal.digest,
            )
            if not ledger.terminal_object_present or not ledger.terminal_receipt_present:
                raise StreamingPostrunError(
                    "terminal record must be an object bound by a closed ledger receipt"
                )
            fixed.extend(
                (
                    FrozenFile(
                        CAPSULE_OBJECTS_PATH,
                        CapsuleFileRole.LEDGER_OBJECTS,
                        ledger.objects_file_size,
                        ledger.objects_file_sha256,
                    ),
                    FrozenFile(
                        CAPSULE_RECEIPTS_PATH,
                        CapsuleFileRole.LEDGER_RECEIPTS,
                        ledger.receipts_file_size,
                        ledger.receipts_file_sha256,
                    ),
                )
            )

            domain = _snapshot_domain(
                domain_path,
                staging / CAPSULE_DOMAIN_STATE_PATH,
                index,
            )
            _require_indexed_terminal_domain_entry(
                index,
                terminal_ref=terminal.digest,
                terminal_size=len(terminal_pin.raw),
            )

            run_seal_file = _write_run_seal(
                staging / CAPSULE_RUN_SEAL_PATH,
                manifest=manifest,
                spec=spec,
                genesis=genesis,
                terminal=terminal,
                ledger=ledger,
                domain=domain,
                index=index,
            )
            fixed.append(run_seal_file)
            capsule_manifest_file = _write_capsule_manifest(
                staging / CAPSULE_MANIFEST_PATH,
                manifest=manifest,
                spec=spec,
                genesis=genesis,
                terminal=terminal,
                ledger=ledger,
                domain=domain,
                index=index,
                files=tuple(fixed),
                run_seal_ref=run_seal_file.sha256,
            )

        verified = verify_evidence_capsule_streaming(
            staging, expected_capsule_ref=capsule_manifest_file.sha256
        )
        expected_source_seal_ref = run_seal_file.sha256
        expected_capsule_ref = verified.digest
        capsule_was_present = capsule.exists()
        if capsule_was_present:
            existing = verify_evidence_capsule_streaming(
                capsule, expected_capsule_ref=expected_capsule_ref
            )
            if existing != verified:
                raise StreamingPostrunError(
                    "immutable capsule destination contains different evidence"
                )

        # This is deliberately the last source read before the publication
        # sequence.  It rebinds every frozen control, the full SQLite file, all
        # opaque domain files, and terminal/live-lock state to the staged seal.
        _recheck_source_before_publication(
            run_root=run,
            manifest=manifest,
            pins=control_pins,
            ledger_path=ledger_path,
            ledger_identity=ledger_before,
            domain_path=domain_path,
            domain_seal=verified.domain_state_seal,
            live_lock=live_lock,
            terminal_ref=terminal.digest,
            terminal_size=len(terminal_pin.raw),
            scratch_parent=capsule.parent,
        )

        source_seal_published_here, source_seal_owned_token = _publish_immutable_file(
            source_seal_path, staging / CAPSULE_RUN_SEAL_PATH
        )
        source_seal, source_seal_raw = _read_contract(source_seal_path, RunSeal)
        staged_source_seal_raw = _read_bounded(staging / CAPSULE_RUN_SEAL_PATH)
        if (
            source_seal.digest != expected_source_seal_ref
            or source_seal_raw != staged_source_seal_raw
        ):
            raise StreamingPostrunError("published source run seal has the wrong identity")

        if not capsule_was_present:
            if capsule.exists() or _is_link_like(capsule):
                raise StreamingPostrunError("capsule destination appeared during publication")
            os.rename(staging, capsule)
            capsule_published_here = True
            capsule_owned_token = staging_owned_token

        published_capsule = verify_evidence_capsule_streaming(
            capsule, expected_capsule_ref=expected_capsule_ref
        )
        if published_capsule != verified:
            raise StreamingPostrunError("published capsule readback disagrees with staging")

        receipt = CalibrationRunReceipt(
            terminal_record_ref=terminal.digest,
            terminal_record=terminal,
            run_seal=_artifact_pointer(source_seal_path),
            run_seal_ref=source_seal.digest,
            evidence_capsule_path=str(capsule.resolve(strict=True)),
            evidence_capsule_ref=verified.digest,
            evidence_capsule_manifest=_artifact_pointer(capsule / CAPSULE_MANIFEST_PATH),
            capsule_verified=True,
        )
        receipt_payload = canonical_bytes(receipt)
        _atomic_write_receipt(receipt_path, receipt_payload)
        receipt_readback, receipt_raw = _read_contract(receipt_path, CalibrationRunReceipt)
        if receipt_raw != receipt_payload or receipt_readback != receipt:
            raise StreamingPostrunError("published delivery receipt readback disagrees")
        receipt_commit_reached = True
        return PostrunResult(
            run_seal_ref=source_seal.digest,
            evidence_capsule_ref=verified.digest,
            capsule_root=capsule,
            delivery_receipt_path=receipt_path,
            source_ledger_sha256=ledger_before.sha256,
        )
    except BaseException as error:
        rollback_errors: list[str] = []
        if not receipt_commit_reached and receipt_payload is not None and receipt_path.exists():
            try:
                receipt_commit_reached = (
                    not _is_link_like(receipt_path)
                    and _read_bounded(receipt_path) == receipt_payload
                )
            except (OSError, StreamingPostrunError) as rollback_error:
                rollback_errors.append(
                    "receipt:could not determine whether the commit record was published: "
                    f"{rollback_error}"
                )

        # An exact external receipt is the commit record.  Once it exists, keep
        # the complete, verified outputs for idempotent readback/retry instead
        # of converting a late reporting failure into partial publication.
        if not receipt_commit_reached and not rollback_errors and capsule_published_here:
            try:
                if not capsule.exists() or capsule_owned_token is None:
                    raise StreamingPostrunError("new capsule disappeared before rollback")
                if _ownership_token(capsule) != capsule_owned_token:
                    raise StreamingPostrunError("new capsule identity changed before rollback")
                rollback_capsule = verify_evidence_capsule_streaming(
                    capsule,
                    expected_capsule_ref=expected_capsule_ref,
                )
                if rollback_capsule != verified:
                    raise StreamingPostrunError("new capsule changed before rollback")
                if staging.exists():
                    raise StreamingPostrunError("staging path unexpectedly reappeared")
                os.rename(capsule, staging)
                if _ownership_token(staging) != staging_owned_token:
                    raise StreamingPostrunError(
                        "capsule identity changed while returning it to staging"
                    )
                capsule_published_here = False
            except (OSError, StreamingPostrunError) as rollback_error:
                rollback_errors.append(f"capsule:{rollback_error}")
        if not receipt_commit_reached and not rollback_errors and source_seal_published_here:
            try:
                if not source_seal_path.exists() or source_seal_owned_token is None:
                    raise StreamingPostrunError("new source seal disappeared before rollback")
                if _ownership_token(source_seal_path) != source_seal_owned_token:
                    raise StreamingPostrunError(
                        "new source seal identity changed before rollback"
                    )
                source_size, seal_digest = _hash_file(source_seal_path)
                staged_size, staged_digest = _hash_file(staging / CAPSULE_RUN_SEAL_PATH)
                if (
                    seal_digest != expected_source_seal_ref
                    or source_size != staged_size
                    or seal_digest != staged_digest
                ):
                    raise StreamingPostrunError(
                        "new source seal changed before failure rollback"
                    )
                source_seal_path.unlink()
                source_seal_published_here = False
            except (OSError, StreamingPostrunError) as rollback_error:
                rollback_errors.append(f"run-seal:{rollback_error}")
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise StreamingPostrunError(
                f"post-run publication failed and rollback was incomplete: {details}"
            ) from error
        raise
    finally:
        if staging.exists():
            _remove_owned_staging(staging, staging_owned_token)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="stream-verify, seal, and publish one closed calibration run"
    )
    parser.add_argument("run_root", type=Path)
    parser.add_argument("capsule_root", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def _require_cli_write_boundary(paths: Mapping[str, Path]) -> None:
    allowed_roots = (
        (_REPOSITORY_ROOT / "playground").resolve(strict=False),
        (_REPOSITORY_ROOT / "artifacts" / "local").resolve(strict=False),
    )
    for label, supplied in paths.items():
        _require_no_link_components(supplied, label=label)
        resolved = supplied.resolve(strict=False)
        _require_no_link_components(resolved, label=f"resolved {label}")
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise StreamingPostrunError(
                f"CLI {label} must stay under repository playground or artifacts/local"
            )


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        _require_cli_write_boundary(
            {
                "run root": args.run_root,
                "capsule destination": args.capsule_root,
                "receipt destination": args.receipt,
            }
        )
        result = seal_and_pack_run(
            run_root=args.run_root,
            capsule_root=args.capsule_root,
            delivery_receipt_path=args.receipt,
        )
    except (OSError, sqlite3.Error, StreamingPostrunError, ValidationError) as error:
        print(f"post-run sealing failed: {error}", file=sys.stderr)
        return 1
    print(
        canonical_bytes(
            {
                "capsule_root": str(result.capsule_root),
                "delivery_receipt_path": str(result.delivery_receipt_path),
                "evidence_capsule_ref": result.evidence_capsule_ref,
                "run_seal_ref": result.run_seal_ref,
                "source_ledger_sha256": result.source_ledger_sha256,
            }
        ).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
