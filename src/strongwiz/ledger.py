"""A serial, append-only SQLite evidence ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path

from pydantic import Field, model_validator

from strongwiz.canonical import JSONValue, canonical_bytes, content_hash, parse_strict_json
from strongwiz.contracts import ContractModel, NonNegativeInt


class LedgerError(RuntimeError):
    pass


_SQLITE_TRANSIENT_SUFFIXES = ("-journal", "-shm", "-wal")


def _require_quiescent_ledger(path: Path) -> str:
    """Return a non-mutating SQLite URI only for a closed, checkpointed ledger."""

    if not path.is_file():
        raise LedgerError("ledger does not exist as a regular file")
    transient = tuple(
        Path(f"{path}{suffix}")
        for suffix in _SQLITE_TRANSIENT_SUFFIXES
        if Path(f"{path}{suffix}").exists()
    )
    if transient:
        names = ", ".join(item.name for item in transient)
        raise LedgerError(
            "read-only verification requires a closed, checkpointed ledger; "
            f"found transient SQLite state: {names}"
        )
    return f"{path.resolve().as_uri()}?mode=ro&immutable=1"


class ReceiptEnvelope(ContractModel):
    schema_id: str = Field(default="strongwiz.receipt.v1", alias="schema")
    receipt_id: str
    occurrence_id: str
    kind: str
    account_id: str
    account_version: NonNegativeInt
    sequence: NonNegativeInt
    payload_hash: str
    object_refs: tuple[str, ...] = ()
    parent_refs: tuple[str, ...] = ()
    previous_receipt_hash: str | None = None
    receipt_hash: str

    @model_validator(mode="after")
    def validate_envelope(self) -> ReceiptEnvelope:
        if self.schema_id != "strongwiz.receipt.v1":
            raise ValueError("unsupported receipt schema")
        expected = content_hash(
            {
                "account_id": self.account_id,
                "account_version": self.account_version,
                "kind": self.kind,
                "object_refs": list(self.object_refs),
                "occurrence_id": self.occurrence_id,
                "parent_refs": list(self.parent_refs),
                "payload_hash": self.payload_hash,
                "previous_receipt_hash": self.previous_receipt_hash,
                "receipt_id": self.receipt_id,
                "schema": self.schema_id,
                "sequence": self.sequence,
            }
        )
        if self.receipt_hash != expected:
            raise ValueError("receipt hash disagrees with envelope content")
        if not self.occurrence_id.strip():
            raise ValueError("receipt occurrence identity must be non-empty")
        if len(set(self.object_refs)) != len(self.object_refs):
            raise ValueError("receipt object references must be unique")
        if len(set(self.parent_refs)) != len(self.parent_refs):
            raise ValueError("receipt parent references must be unique")
        return self


class SQLiteLedger:
    """One-writer local ledger with atomic append and exact replay."""

    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        self.path = Path(path)
        self._readonly = readonly
        if readonly:
            uri = _require_quiescent_ledger(self.path)
            self._connection = sqlite3.connect(uri, uri=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS objects (
                payload_hash TEXT PRIMARY KEY,
                canonical_payload BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
                sequence INTEGER PRIMARY KEY,
                receipt_id TEXT NOT NULL UNIQUE,
                occurrence_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_version INTEGER NOT NULL,
                payload_hash TEXT NOT NULL REFERENCES objects(payload_hash),
                envelope_json BLOB NOT NULL,
                receipt_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        self._connection.commit()

    def put_object(self, payload: object) -> str:
        """Store one canonical object by content identity without issuing a receipt."""

        if self._readonly:
            raise LedgerError("read-only ledger cannot store objects")
        payload_bytes = canonical_bytes(payload)
        payload_hash = content_hash(payload)
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO objects(payload_hash, canonical_payload) VALUES (?, ?)",
                (payload_hash, payload_bytes),
            )
            stored = connection.execute(
                "SELECT canonical_payload FROM objects WHERE payload_hash = ?", (payload_hash,)
            ).fetchone()
            if stored is None or bytes(stored[0]) != payload_bytes:
                raise LedgerError("content-addressed object collision or rewrite")
            connection.commit()
            return payload_hash
        except Exception:
            connection.rollback()
            raise

    def has_object(self, payload_hash: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM objects WHERE payload_hash = ?", (payload_hash,)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def append(
        self,
        *,
        occurrence_id: str,
        kind: str,
        account_id: str,
        account_version: int,
        payload: Mapping[str, object],
        object_refs: tuple[str, ...] = (),
        parent_refs: tuple[str, ...] = (),
    ) -> ReceiptEnvelope:
        if self._readonly:
            raise LedgerError("read-only ledger cannot append receipts")
        if not occurrence_id.strip() or not kind or not account_id or account_version < 0:
            raise LedgerError(
                "receipt occurrence, kind, account, and nonnegative version are required"
            )
        if len(set(object_refs)) != len(object_refs) or len(set(parent_refs)) != len(
            parent_refs
        ):
            raise LedgerError("receipt references must be unique")
        payload_bytes = canonical_bytes(payload)
        payload_hash = content_hash(payload)
        receipt_id = content_hash(
            {
                "account_id": account_id,
                "account_version": account_version,
                "kind": kind,
                "object_refs": list(object_refs),
                "occurrence_id": occurrence_id,
                "parent_refs": list(parent_refs),
                "payload_hash": payload_hash,
            }
        )
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            missing_objects = tuple(ref for ref in object_refs if not self.has_object(ref))
            if missing_objects:
                raise LedgerError("receipt references an unknown content object")
            missing_parents = tuple(
                ref
                for ref in parent_refs
                if connection.execute(
                    "SELECT 1 FROM receipts WHERE receipt_id = ?", (ref,)
                ).fetchone()
                is None
            )
            if missing_parents:
                raise LedgerError("receipt references an unknown parent receipt")
            existing_row = connection.execute(
                "SELECT envelope_json FROM receipts WHERE occurrence_id = ?", (occurrence_id,)
            ).fetchone()
            if existing_row is not None:
                envelope = ReceiptEnvelope.model_validate_json(bytes(existing_row[0]))
                if envelope.receipt_id != receipt_id:
                    raise LedgerError("receipt occurrence identity cannot be rewritten")
                connection.commit()
                return envelope
            tail = connection.execute(
                "SELECT sequence, receipt_hash FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if tail is None else int(tail[0]) + 1
            previous = None if tail is None else str(tail[1])
            base: dict[str, JSONValue] = {
                "account_id": account_id,
                "account_version": account_version,
                "kind": kind,
                "object_refs": list(object_refs),
                "occurrence_id": occurrence_id,
                "parent_refs": list(parent_refs),
                "payload_hash": payload_hash,
                "previous_receipt_hash": previous,
                "receipt_id": receipt_id,
                "schema": "strongwiz.receipt.v1",
                "sequence": sequence,
            }
            envelope = ReceiptEnvelope(
                **base,
                receipt_hash=content_hash(base),
            )
            connection.execute(
                "INSERT OR IGNORE INTO objects(payload_hash, canonical_payload) VALUES (?, ?)",
                (payload_hash, payload_bytes),
            )
            stored = connection.execute(
                "SELECT canonical_payload FROM objects WHERE payload_hash = ?", (payload_hash,)
            ).fetchone()
            if stored is None or bytes(stored[0]) != payload_bytes:
                raise LedgerError("content-addressed object collision or rewrite")
            connection.execute(
                """INSERT INTO receipts(
                       sequence, receipt_id, occurrence_id, kind, account_id, account_version,
                       payload_hash, envelope_json, receipt_hash
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sequence,
                    receipt_id,
                    occurrence_id,
                    kind,
                    account_id,
                    account_version,
                    payload_hash,
                    canonical_bytes(envelope),
                    envelope.receipt_hash,
                ),
            )
            connection.commit()
            return envelope
        except Exception:
            connection.rollback()
            raise

    def get_payload(self, payload_hash: str) -> JSONValue:
        row = self._connection.execute(
            "SELECT canonical_payload FROM objects WHERE payload_hash = ?", (payload_hash,)
        ).fetchone()
        if row is None:
            raise LedgerError("unknown payload hash")
        return parse_strict_json(bytes(row[0]))

    def receipts(self) -> Iterator[ReceiptEnvelope]:
        rows = self._connection.execute("SELECT envelope_json FROM receipts ORDER BY sequence")
        for row in rows:
            yield ReceiptEnvelope.model_validate_json(bytes(row[0]))

    def verify(
        self, *, expected_count: int | None = None, expected_head: str | None = None
    ) -> tuple[int, str | None]:
        """Recompute the canonical object projection and every receipt binding."""

        try:
            object_rows = self._connection.execute(
                "SELECT payload_hash, canonical_payload FROM objects ORDER BY payload_hash"
            )
            object_refs: set[str] = set()
            for stored_hash, stored_payload in object_rows:
                raw = bytes(stored_payload)
                payload = parse_strict_json(raw)
                if canonical_bytes(payload) != raw:
                    raise LedgerError("stored object is not canonical JSON")
                if content_hash(payload) != str(stored_hash):
                    raise LedgerError("stored object digest mismatch")
                object_refs.add(str(stored_hash))

            rows = self._connection.execute(
                """SELECT sequence, receipt_id, occurrence_id, kind,
                          account_id, account_version,
                          payload_hash, envelope_json, receipt_hash
                   FROM receipts ORDER BY sequence"""
            )
            previous: str | None = None
            seen_receipts: set[str] = set()
            count = 0
            for expected_sequence, row in enumerate(rows):
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
                raw_envelope = bytes(envelope_json)
                envelope = ReceiptEnvelope.model_validate_json(raw_envelope)
                if canonical_bytes(envelope) != raw_envelope:
                    raise LedgerError("stored receipt envelope is not canonical JSON")
                table_projection = (
                    int(sequence),
                    str(receipt_id),
                    str(occurrence_id),
                    str(kind),
                    str(account_id),
                    int(account_version),
                    str(payload_hash),
                    str(receipt_hash),
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
                    raise LedgerError("receipt table projection disagrees with its envelope")
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
                    raise LedgerError("receipt identity disagrees with its content binding")
                if envelope.sequence != expected_sequence:
                    raise LedgerError("receipt sequence is not contiguous")
                if envelope.previous_receipt_hash != previous:
                    raise LedgerError("receipt chain predecessor mismatch")
                if envelope.payload_hash not in object_refs:
                    raise LedgerError("receipt payload object is missing")
                if any(ref not in object_refs for ref in envelope.object_refs):
                    raise LedgerError("receipt content reference is missing")
                if any(ref not in seen_receipts for ref in envelope.parent_refs):
                    raise LedgerError("receipt parent reference is missing or forward")
                seen_receipts.add(envelope.receipt_id)
                previous = envelope.receipt_hash
                count += 1
            if expected_count is not None and count != expected_count:
                raise LedgerError("receipt count disagrees with the external seal")
            if expected_head is not None and previous != expected_head:
                raise LedgerError("receipt head disagrees with the external seal")
            return count, previous
        except LedgerError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise LedgerError(f"ledger integrity validation failed: {error}") from error

    def export_receipt_projection_jsonl(self, path: str | Path) -> None:
        """Export receipt envelopes and primary payloads, not the full object graph."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            for envelope in self.receipts():
                row = {
                    "envelope": envelope.model_dump(mode="json"),
                    "payload": self.get_payload(envelope.payload_hash),
                }
                stream.write(canonical_bytes(row) + b"\n")

    @property
    def projection_hash(self) -> str:
        rows = [envelope.model_dump(mode="json") for envelope in self.receipts()]
        return content_hash(rows)
