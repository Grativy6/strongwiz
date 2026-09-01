from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.accounts import (
    AccountBook,
    AccountError,
    AccountHeader,
    AccountKind,
    ResidualStatus,
)
from strongwiz.ledger import LedgerError, SQLiteLedger
from tests.support import ref


def test_account_openings_versions_and_residual_lineage() -> None:
    book = AccountBook()
    root = book.open_root(scope_id="root", cut_ref=ref("cut-root"), witness_refs=(ref("w1"),))
    child = book.open_child(
        root, scope_id="child", cut_ref=ref("cut-child"), witness_refs=(ref("w2"),)
    )
    successor = book.open_successor(
        child,
        scope_id="next",
        cut_ref=ref("cut-successor"),
        witness_refs=(ref("w3"),),
    )
    version = book.open_version(
        successor,
        cut_ref=ref("cut-version"),
        witness_refs=(ref("w4"),),
        material_delta_refs=(ref("delta"),),
    )
    assert root.kind is AccountKind.ROOT
    assert child.parent_account_id == root.account_id
    assert child.parent_account_ref == root.digest
    assert successor.predecessor_account_id == child.account_id
    assert successor.predecessor_account_ref == child.digest
    assert version.account_id == successor.account_id
    assert version.version == 1
    residual = book.register_residual(
        version, statement="access condition unresolved", source_refs=(ref("obs"),)
    )
    reopened = book.transition_residual(
        (residual.residual_id,),
        account=version,
        statement="access condition reopened after change",
        status=ResidualStatus.REOPENED,
        source_refs=(ref("change"),),
        reason="new surface contradicted the retained fact",
    )
    assert reopened.parent_residual_ids == (residual.residual_id,)
    assert len(book.headers) == 4
    assert len(book.residuals) == 2


def test_child_identity_binds_exact_parent_version_and_witnesses_cannot_rewrite() -> None:
    book = AccountBook()
    root = book.open_root(
        scope_id="scope", cut_ref=ref("cut-0"), witness_refs=(ref("witness-0"),)
    )
    root_v1 = book.open_version(
        root,
        cut_ref=ref("cut-1"),
        witness_refs=(ref("witness-1"),),
        material_delta_refs=(ref("delta"),),
    )
    child_v0 = book.open_child(
        root,
        scope_id="child",
        cut_ref=ref("child-cut"),
        witness_refs=(ref("child-witness"),),
    )
    child_v1 = book.open_child(
        root_v1,
        scope_id="child",
        cut_ref=ref("child-cut"),
        witness_refs=(ref("child-witness"),),
    )
    assert child_v0.account_id != child_v1.account_id

    with pytest.raises(AccountError, match="one exact successor version"):
        book.open_version(
            root,
            cut_ref=ref("cut-1"),
            witness_refs=(ref("different-witness"),),
            material_delta_refs=(ref("delta"),),
        )


def test_account_version_requires_delta_and_exact_parent() -> None:
    with pytest.raises(ValidationError, match="material delta"):
        AccountHeader(
            account_id="a",
            origin_id="a",
            kind=AccountKind.VERSION,
            version=1,
            scope_id="scope",
            cut_ref=ref("cut"),
            prior_version_ref=ref("prior"),
            witness_refs=(ref("w"),),
        )
    with pytest.raises(AccountError, match="unknown"):
        AccountBook().require("absent", 0)


def test_sqlite_ledger_occurrences_idempotence_and_projection_export(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    export = tmp_path / "receipts.jsonl"
    with SQLiteLedger(path) as ledger:
        object_ref = ledger.put_object({"raw": "observation"})
        first = ledger.append(
            occurrence_id="observation-0",
            kind="observation",
            account_id="account",
            account_version=0,
            payload={"state": "initial"},
            object_refs=(object_ref,),
        )
        replay = ledger.append(
            occurrence_id="observation-0",
            kind="observation",
            account_id="account",
            account_version=0,
            payload={"state": "initial"},
            object_refs=(object_ref,),
        )
        repeated = ledger.append(
            occurrence_id="observation-1",
            kind="observation",
            account_id="account",
            account_version=0,
            payload={"state": "initial"},
            object_refs=(object_ref,),
        )
        second = ledger.append(
            occurrence_id="decision-0",
            kind="decision",
            account_id="account",
            account_version=0,
            payload={"action": "inspect"},
            parent_refs=(repeated.receipt_id,),
        )
        assert replay == first
        assert repeated.receipt_id != first.receipt_id
        assert repeated.previous_receipt_hash == first.receipt_hash
        assert second.previous_receipt_hash == repeated.receipt_hash
        assert ledger.verify() == (3, second.receipt_hash)
        assert ledger.get_payload(object_ref) == {"raw": "observation"}
        ledger.export_receipt_projection_jsonl(export)
        assert len(export.read_text(encoding="utf-8").splitlines()) == 3
        assert ledger.projection_hash


def test_ledger_detects_payload_tampering(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    with SQLiteLedger(path) as ledger:
        receipt = ledger.append(
            occurrence_id="observation-0",
            kind="observation",
            account_id="account",
            account_version=0,
            payload={"state": "initial"},
        )
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE objects SET canonical_payload = ? WHERE payload_hash = ?",
        (b'{"state":"tampered"}', receipt.payload_hash),
    )
    connection.commit()
    connection.close()
    with SQLiteLedger(path) as ledger, pytest.raises(LedgerError, match="digest"):
        ledger.verify()


def test_ledger_rejects_dangling_references_and_projection_tampering(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    with SQLiteLedger(path) as ledger:
        with pytest.raises(LedgerError, match="unknown content"):
            ledger.append(
                occurrence_id="bad-object-0",
                kind="bad-object",
                account_id="account",
                account_version=0,
                payload={"bad": True},
                object_refs=(ref("missing-object"),),
            )
        with pytest.raises(LedgerError, match="unknown parent"):
            ledger.append(
                occurrence_id="bad-parent-0",
                kind="bad-parent",
                account_id="account",
                account_version=0,
                payload={"bad": True},
                parent_refs=(ref("missing-parent"),),
            )
        receipt = ledger.append(
            occurrence_id="valid-0",
            kind="valid",
            account_id="account",
            account_version=0,
            payload={"ok": True},
        )
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE receipts SET kind = ? WHERE receipt_id = ?",
        ("tampered-index", receipt.receipt_id),
    )
    connection.commit()
    connection.close()
    with (
        SQLiteLedger(path, readonly=True) as ledger,
        pytest.raises(LedgerError, match="projection"),
    ):
        ledger.verify()


def test_readonly_verification_requires_existing_ledger_and_external_seal(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(LedgerError, match="does not exist"):
        SQLiteLedger(missing, readonly=True)
    assert not missing.exists()

    path = tmp_path / "sealed.sqlite3"
    with SQLiteLedger(path) as ledger:
        receipt = ledger.append(
            occurrence_id="sealed-0",
            kind="sealed",
            account_id="account",
            account_version=0,
            payload={"sealed": True},
        )
    with SQLiteLedger(path, readonly=True) as ledger:
        assert ledger.verify(expected_count=1, expected_head=receipt.receipt_hash) == (
            1,
            receipt.receipt_hash,
        )
        with pytest.raises(LedgerError, match="external seal"):
            ledger.verify(expected_count=2, expected_head=receipt.receipt_hash)


def test_readonly_verification_does_not_mutate_the_ledger_directory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sealed.sqlite3"
    with SQLiteLedger(path) as ledger:
        ledger.append(
            occurrence_id="sealed-0",
            kind="sealed",
            account_id="account",
            account_version=0,
            payload={"sealed": True},
        )
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    with SQLiteLedger(path, readonly=True) as ledger:
        assert ledger.verify()[0] == 1
    after = {item.name: item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    assert after == before


def test_readonly_verification_refuses_uncheckpointed_sqlite_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sealed.sqlite3"
    with SQLiteLedger(path):
        pass
    transient = Path(f"{path}-wal")
    transient.write_bytes(b"uncheckpointed")
    with pytest.raises(LedgerError, match="closed, checkpointed"):
        SQLiteLedger(path, readonly=True)
    assert transient.read_bytes() == b"uncheckpointed"
