from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sqlite3
import threading
import tracemalloc
from collections.abc import Iterator
from pathlib import Path

import pytest

from calibration.models import BudgetReceipt, CalibrationRunReceipt, RunTerminalRecord
from calibration.workflow import pack_run
from scripts import strongwiz_streaming_postrun as streaming
from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.contracts import CostVector
from strongwiz.lab import (
    CAPSULE_MANIFEST_PATH,
    CAPSULE_OBJECTS_PATH,
    CAPSULE_RECEIPTS_PATH,
    CapsuleObject,
    LabLayout,
    LabManifest,
    RunSpec,
    initialize_lab,
)
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger


def _ref(label: str) -> str:
    return content_hash({"fixture-ref": label})


def _manifest(*, layout: LabLayout | None = None) -> LabManifest:
    return LabManifest(
        lab_id="streaming-postrun-lab",
        lab_version="0.2.0",
        purpose="bounded-memory post-run fixture",
        strongwiz_version="0.2.0",
        kernel_artifact_ref=_ref("kernel"),
        contract_schema="strongwiz.contract.v1",
        **({"layout": layout} if layout is not None else {}),
    )


def _spec(manifest: LabManifest, *, run_id: str = "streaming-postrun-run") -> RunSpec:
    return RunSpec(
        run_id=run_id,
        lab_manifest_ref=manifest.digest,
        objective="preserve a synthetic terminal record",
        success_condition="the synthetic terminal authority returns WIN",
        success_state="WIN",
        terminal_authority_source="synthetic-terminal-authority",
        evaluation_class="synthetic",
        frozen_runtime_ref=_ref("runtime"),
        model_driver_id="fixture-model",
        model_driver_version="1",
        model_driver_artifact_ref=_ref("model"),
        domain_adapter_id="fixture-domain",
        domain_adapter_version="1",
        domain_adapter_artifact_ref=_ref("domain"),
        seed=0,
        resource_budget=CostVector(
            environment_actions=3,
            wall_clock_ms=1000,
            compute_units=10,
            memory_bytes=4096,
        ),
    )


def _terminal(spec: RunSpec, genesis_ref: str) -> RunTerminalRecord:
    return RunTerminalRecord(
        run_id=spec.run_id,
        game_id="synthetic-game-v1",
        asset_manifest_ref=_ref("asset"),
        final_state="NOT_FINISHED",
        levels_completed=0,
        win_levels=0,
        budget=BudgetReceipt(
            maximum_non_reset_actions=3,
            maximum_resets=1,
            maximum_total_environment_calls=4,
            wall_clock_seconds=60,
            non_reset_actions=1,
            resets=1,
            total_environment_calls=2,
            elapsed_wall_ms=100,
        ),
        frozen_runtime_ref=spec.frozen_runtime_ref,
        toolbelt_ref=_ref("toolbelt"),
        integration_ref=_ref("integration"),
        dependency_ref=_ref("dependencies"),
        model_interface_ref=_ref("interface"),
        domain_adapter_ref=spec.domain_adapter_artifact_ref,
        executor_ref=_ref("executor"),
        lab_genesis_ref=genesis_ref,
        latest_checkpoint_ref=_ref("checkpoint"),
        initial_reset_admission_ref=_ref("reset-admission"),
        terminal_frame=None,
        raw_trace=None,
        official_recordings=(),
        completion_genuinely_observed=False,
        disposition="partial",
        concise_result_summary="synthetic fixture stopped before WIN",
        claim_class="synthetic",
        claim_exclusions=("not an official result",),
    )


def _closed_run(
    tmp_path: Path,
    *,
    opaque_size: int = 0,
    layout: LabLayout | None = None,
) -> tuple[Path, RunTerminalRecord]:
    run_root = tmp_path / "run"
    tmp_path.mkdir(parents=True)
    manifest = _manifest(layout=layout)
    spec = _spec(manifest)
    genesis = initialize_lab(run_root, manifest=manifest, run_spec=spec)
    terminal = _terminal(spec, genesis.digest)
    domain = run_root / manifest.layout.domain_state_path
    terminal_path = domain / "terminal.record.json"
    terminal_path.write_bytes(canonical_bytes(terminal))
    if opaque_size:
        nested = domain / "opaque" / "nested"
        nested.mkdir(parents=True)
        (nested / "recording.bin").write_bytes(
            bytes(index % 251 for index in range(opaque_size))
        )
        (nested / "empty").mkdir()
    with SQLiteLedger(run_root / manifest.layout.ledger_path) as ledger:
        terminal_ref = ledger.put_object(terminal)
        assert terminal_ref == terminal.digest
        ledger.append(
            occurrence_id=f"{spec.run_id}:terminal-record",
            kind="calibration_terminal_record",
            account_id=f"{spec.run_id}:terminal",
            account_version=0,
            payload={
                "summary": terminal.concise_result_summary,
                "terminal_record_ref": terminal.digest,
            },
            object_refs=(terminal.digest,),
        )
    return run_root, terminal


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = None if path.is_dir() else path.read_bytes()
    return result


def _source_digest(paths: Iterator[Path]) -> dict[str, str]:
    return {
        path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)
    }


def _receipt(
    *,
    sequence: int,
    occurrence_id: str,
    payload_hash: str,
    object_refs: tuple[str, ...] = (),
    parent_refs: tuple[str, ...] = (),
    previous: str | None = None,
) -> ReceiptEnvelope:
    receipt_id = content_hash(
        {
            "account_id": "fixture-account",
            "account_version": 0,
            "kind": "fixture",
            "object_refs": list(object_refs),
            "occurrence_id": occurrence_id,
            "parent_refs": list(parent_refs),
            "payload_hash": payload_hash,
        }
    )
    envelope_without_hash = {
        "account_id": "fixture-account",
        "account_version": 0,
        "kind": "fixture",
        "object_refs": list(object_refs),
        "occurrence_id": occurrence_id,
        "parent_refs": list(parent_refs),
        "payload_hash": payload_hash,
        "previous_receipt_hash": previous,
        "receipt_id": receipt_id,
        "schema": "strongwiz.receipt.v1",
        "sequence": sequence,
    }
    return ReceiptEnvelope(
        **envelope_without_hash,
        receipt_hash=content_hash(envelope_without_hash),
    )


def _write_jsonl(path: Path, values: tuple[object, ...]) -> None:
    path.write_bytes(b"".join(canonical_bytes(value) + b"\n" for value in values))


def _object_only_ledger(path: Path, count: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE objects (
                payload_hash TEXT PRIMARY KEY,
                canonical_payload BLOB NOT NULL
            );
            CREATE TABLE receipts (
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
        connection.executemany(
            "INSERT INTO objects(payload_hash, canonical_payload) VALUES (?, ?)",
            (
                (content_hash({"bulk": index}), canonical_bytes({"bulk": index}))
                for index in range(count)
            ),
        )


def _ledger_stream_peak(tmp_path: Path, count: int) -> int:
    tmp_path.mkdir()
    ledger = tmp_path / "ledger.sqlite3"
    _object_only_ledger(ledger, count)
    tracemalloc.start()
    try:
        with streaming._disk_index(tmp_path) as index:
            result = streaming._stream_source_ledger(
                ledger,
                tmp_path / "objects.jsonl",
                tmp_path / "receipts.jsonl",
                index,
                terminal_ref=_ref("not-present"),
            )
            assert result.object_count == count
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_streaming_tool_matches_small_legacy_capsule_and_receipt(tmp_path: Path) -> None:
    original, _ = _closed_run(tmp_path / "source", opaque_size=8193)
    legacy_run = tmp_path / "legacy-run"
    streaming_run = tmp_path / "streaming-run"
    shutil.copytree(original, legacy_run)
    shutil.copytree(original, streaming_run)

    legacy_capsule = tmp_path / "legacy-capsule"
    streaming_capsule = tmp_path / "streaming-capsule"
    legacy_receipt_path = tmp_path / "legacy-receipt.json"
    streaming_receipt_path = tmp_path / "streaming-receipt.json"
    legacy_receipt = pack_run(
        run_root=legacy_run,
        capsule_root=legacy_capsule,
        delivery_receipt_path=legacy_receipt_path,
    )
    result = streaming.seal_and_pack_run(
        run_root=streaming_run,
        capsule_root=streaming_capsule,
        delivery_receipt_path=streaming_receipt_path,
    )

    assert _tree_bytes(streaming_capsule) == _tree_bytes(legacy_capsule)
    assert (streaming_run / "run.seal.json").read_bytes() == (
        legacy_run / "run.seal.json"
    ).read_bytes()
    actual_receipt = CalibrationRunReceipt.model_validate_json(
        streaming_receipt_path.read_bytes()
    )
    assert (
        actual_receipt.model_copy(
            update={
                "run_seal": legacy_receipt.run_seal,
                "evidence_capsule_path": legacy_receipt.evidence_capsule_path,
                "evidence_capsule_manifest": legacy_receipt.evidence_capsule_manifest,
            }
        )
        == legacy_receipt
    )
    assert result.run_seal_ref == legacy_receipt.run_seal_ref
    assert result.evidence_capsule_ref == legacy_receipt.evidence_capsule_ref
    assert (
        streaming.verify_evidence_capsule_streaming(
            streaming_capsule, expected_capsule_ref=result.evidence_capsule_ref
        ).digest
        == result.evidence_capsule_ref
    )


def test_requires_terminal_record_and_quiescent_ledger(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "terminal")
    (run_root / "state/domain/terminal.record.json").unlink()
    with pytest.raises(streaming.StreamingPostrunError):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=tmp_path / "terminal-capsule",
            delivery_receipt_path=tmp_path / "terminal-receipt.json",
        )

    run_root, _ = _closed_run(tmp_path / "wal")
    Path(f"{run_root / 'state/ledger.sqlite3'}-wal").write_bytes(b"closed-run violation")
    with pytest.raises(streaming.StreamingPostrunError, match="transient SQLite"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=tmp_path / "wal-capsule",
            delivery_receipt_path=tmp_path / "wal-receipt.json",
        )
    assert not (tmp_path / "wal-capsule").exists()


def test_concurrent_finalizer_is_excluded_before_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "concurrent-capsule"
    receipt = tmp_path / "concurrent-receipt.json"
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    original_locked = streaming._seal_and_pack_run_locked

    def hold_lock(**kwargs: object) -> streaming.PostrunResult:
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("test did not release the first finalizer")
        return original_locked(**kwargs)

    def first_finalizer() -> None:
        try:
            streaming.seal_and_pack_run(
                run_root=run_root,
                capsule_root=capsule,
                delivery_receipt_path=receipt,
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(streaming, "_seal_and_pack_run_locked", hold_lock)
    worker = threading.Thread(target=first_finalizer)
    worker.start()
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(streaming.StreamingPostrunError, match="finalizer owns"):
            streaming.seal_and_pack_run(
                run_root=run_root,
                capsule_root=capsule,
                delivery_receipt_path=receipt,
            )
        assert not capsule.exists()
        assert not receipt.exists()
        assert not (run_root / "run.seal.json").exists()
    finally:
        release.set()
        worker.join(timeout=20)
    assert not worker.is_alive()
    assert errors == []
    assert capsule.is_dir()
    assert receipt.is_file()
    assert not tuple(tmp_path.glob(".strongwiz-postrun-finalizer-*.lock"))


def test_existing_capsule_is_never_clobbered(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "occupied-capsule"
    capsule.mkdir()
    marker = capsule / "owner-data.bin"
    marker.write_bytes(b"not this transaction")
    receipt = tmp_path / "occupied-receipt.json"

    with pytest.raises(streaming.StreamingPostrunError):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert marker.read_bytes() == b"not this transaction"
    assert tuple(capsule.iterdir()) == (marker,)
    assert not receipt.exists()
    assert not (run_root / "run.seal.json").exists()


def test_existing_receipt_is_never_clobbered_and_new_outputs_roll_back(
    tmp_path: Path,
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "receipt-no-clobber-capsule"
    receipt = tmp_path / "occupied-receipt.json"
    receipt.write_bytes(b"foreign receipt bytes")

    with pytest.raises(streaming.StreamingPostrunError, match="existing delivery receipt"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert receipt.read_bytes() == b"foreign receipt bytes"
    assert not capsule.exists()
    assert not (run_root / "run.seal.json").exists()


def test_existing_source_seal_is_never_clobbered(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    source_seal = run_root / "run.seal.json"
    source_seal.write_bytes(b"foreign source seal bytes")
    capsule = tmp_path / "source-seal-no-clobber-capsule"
    receipt = tmp_path / "source-seal-no-clobber-receipt.json"

    with pytest.raises(streaming.StreamingPostrunError, match="immutable artifact"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert source_seal.read_bytes() == b"foreign source seal bytes"
    assert not capsule.exists()
    assert not receipt.exists()


@pytest.mark.parametrize(
    "mutation",
    ("manifest", "spec", "genesis", "terminal", "ledger", "domain", "live-lock"),
)
def test_final_source_recheck_rejects_mutation_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    run_root, _ = _closed_run(tmp_path / mutation, opaque_size=128)
    capsule = tmp_path / f"{mutation}-capsule"
    receipt = tmp_path / f"{mutation}-receipt.json"
    original_verify = streaming.verify_evidence_capsule_streaming
    mutated = False

    def verify_then_mutate(
        root: str | Path, *, expected_capsule_ref: str | None = None
    ) -> object:
        nonlocal mutated
        result = original_verify(root, expected_capsule_ref=expected_capsule_ref)
        if not mutated:
            mutated = True
            if mutation == "live-lock":
                lock = run_root / "state/domain/control/.calibration-live.lock"
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_bytes(b"writer returned")
            else:
                relative = {
                    "manifest": "lab.manifest.json",
                    "spec": "run.spec.json",
                    "genesis": "lab.genesis.json",
                    "terminal": "state/domain/terminal.record.json",
                    "ledger": "state/ledger.sqlite3",
                    "domain": "state/domain/opaque/nested/recording.bin",
                }[mutation]
                with (run_root / relative).open("ab") as stream:
                    stream.write(b"\n")
        return result

    monkeypatch.setattr(streaming, "verify_evidence_capsule_streaming", verify_then_mutate)
    with pytest.raises(streaming.StreamingPostrunError):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert not capsule.exists()
    assert not receipt.exists()
    assert not (run_root / "run.seal.json").exists()


def test_domain_terminal_entry_must_match_pinned_terminal_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, terminal = _closed_run(tmp_path / "source")
    terminal_path = run_root / "state/domain/terminal.record.json"
    original_stream = streaming._stream_source_ledger

    def stream_then_replace_terminal(*args: object, **kwargs: object) -> object:
        result = original_stream(*args, **kwargs)
        replacement = terminal.model_copy(
            update={"concise_result_summary": "changed after terminal pin"}
        )
        terminal_path.write_bytes(canonical_bytes(replacement))
        return result

    monkeypatch.setattr(streaming, "_stream_source_ledger", stream_then_replace_terminal)
    with pytest.raises(streaming.StreamingPostrunError, match=r"terminal\.record\.json bytes"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=tmp_path / "terminal-mismatch-capsule",
            delivery_receipt_path=tmp_path / "terminal-mismatch-receipt.json",
        )
    assert not (run_root / "run.seal.json").exists()


def test_custom_manifest_layout_drives_terminal_and_live_lock_paths(tmp_path: Path) -> None:
    layout = LabLayout(
        ledger_path="runtime/private-ledger.sqlite3",
        domain_state_path="runtime/opaque-domain",
    )
    run_root, _ = _closed_run(tmp_path / "source", layout=layout)
    live_lock = run_root / layout.domain_state_path / "control/.calibration-live.lock"
    live_lock.parent.mkdir(parents=True, exist_ok=True)
    live_lock.write_bytes(b"writer")
    with pytest.raises(streaming.StreamingPostrunError, match="live calibration lock"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=tmp_path / "custom-layout-capsule",
            delivery_receipt_path=tmp_path / "custom-layout-receipt.json",
        )
    live_lock.unlink()

    result = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=tmp_path / "custom-layout-capsule",
        delivery_receipt_path=tmp_path / "custom-layout-receipt.json",
    )
    assert result.run_seal_ref


@pytest.mark.parametrize("case", ("duplicate", "missing", "forward"))
def test_streaming_ledger_verifier_rejects_reference_failures(
    tmp_path: Path, case: str
) -> None:
    objects_path = tmp_path / "objects.jsonl"
    receipts_path = tmp_path / "receipts.jsonl"
    first = CapsuleObject(payload_hash=content_hash({"object": 1}), payload={"object": 1})
    second = CapsuleObject(payload_hash=content_hash({"object": 2}), payload={"object": 2})

    if case == "duplicate":
        _write_jsonl(objects_path, (first, first))
        _write_jsonl(receipts_path, ())
    elif case == "missing":
        _write_jsonl(
            objects_path, tuple(sorted((first, second), key=lambda item: item.payload_hash))
        )
        receipt = _receipt(
            sequence=0,
            occurrence_id="missing",
            payload_hash=first.payload_hash,
            object_refs=(_ref("absent"),),
        )
        _write_jsonl(receipts_path, (receipt,))
    else:
        _write_jsonl(
            objects_path, tuple(sorted((first, second), key=lambda item: item.payload_hash))
        )
        future_binding = _receipt(
            sequence=1,
            occurrence_id="future",
            payload_hash=second.payload_hash,
        )
        first_receipt = _receipt(
            sequence=0,
            occurrence_id="first",
            payload_hash=first.payload_hash,
            parent_refs=(future_binding.receipt_id,),
        )
        future = _receipt(
            sequence=1,
            occurrence_id="future",
            payload_hash=second.payload_hash,
            previous=first_receipt.receipt_hash,
        )
        assert future.receipt_id == future_binding.receipt_id
        _write_jsonl(receipts_path, (first_receipt, future))

    with (
        streaming._disk_index(tmp_path) as index,
        pytest.raises(streaming.StreamingPostrunError),
    ):
        streaming._verify_exported_ledger(
            objects_path,
            receipts_path,
            index,
            terminal_ref=_ref("irrelevant-terminal"),
        )


def test_failure_removes_staging_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source", opaque_size=1024)
    capsule = tmp_path / "atomic-capsule"
    receipt = tmp_path / "atomic-receipt.json"

    def fail_snapshot(*_args: object, **_kwargs: object) -> streaming.DomainSealData:
        raise streaming.StreamingPostrunError("injected after ledger streaming")

    monkeypatch.setattr(streaming, "_snapshot_domain", fail_snapshot)
    with pytest.raises(streaming.StreamingPostrunError, match="injected"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert not capsule.exists()
    assert not receipt.exists()
    assert not (run_root / "run.seal.json").exists()
    assert not tuple(tmp_path.glob(".atomic-capsule.postrun-*"))


def test_failure_after_source_seal_publish_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "rename-failure-capsule"
    receipt = tmp_path / "rename-failure-receipt.json"
    original_rename = os.rename

    def fail_capsule_rename(
        source: str | bytes | Path, destination: str | bytes | Path
    ) -> None:
        if Path(destination) == capsule:
            raise OSError("injected capsule rename failure")
        original_rename(source, destination)

    monkeypatch.setattr(streaming.os, "rename", fail_capsule_rename)
    with pytest.raises(OSError, match="injected capsule rename failure"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert not (run_root / "run.seal.json").exists()
    assert not capsule.exists()
    assert not receipt.exists()
    assert not tuple(tmp_path.glob(".rename-failure-capsule.postrun-*"))


def test_failure_after_receipt_commit_keeps_complete_resumable_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "receipt-failure-capsule"
    receipt = tmp_path / "receipt-failure-receipt.json"

    original_publish = streaming._atomic_write_receipt

    def publish_then_fail(path: Path, payload: bytes) -> bool:
        original_publish(path, payload)
        raise OSError("injected post-capsule receipt failure")

    monkeypatch.setattr(streaming, "_atomic_write_receipt", publish_then_fail)
    with pytest.raises(OSError, match="injected post-capsule receipt failure"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert (run_root / "run.seal.json").is_file()
    assert capsule.is_dir()
    assert receipt.is_file()
    committed = CalibrationRunReceipt.model_validate_json(receipt.read_bytes())
    assert committed.capsule_verified is True
    assert not tuple(tmp_path.glob(".receipt-failure-capsule.postrun-*"))

    monkeypatch.setattr(streaming, "_atomic_write_receipt", original_publish)
    resumed = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=capsule,
        delivery_receipt_path=receipt,
    )
    assert resumed.run_seal_ref == committed.run_seal_ref
    assert resumed.evidence_capsule_ref == committed.evidence_capsule_ref


def test_receipt_readback_failure_is_receipt_committed_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "readback-failure-capsule"
    receipt = tmp_path / "readback-failure-receipt.json"
    original_read = streaming._read_contract

    def fail_receipt_readback(path: Path, model: type[object]) -> tuple[object, bytes]:
        value, raw = original_read(path, model)
        if model is CalibrationRunReceipt:
            raise OSError("injected receipt readback failure")
        return value, raw

    monkeypatch.setattr(streaming, "_read_contract", fail_receipt_readback)
    with pytest.raises(OSError, match="injected receipt readback failure"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert (run_root / "run.seal.json").is_file()
    assert capsule.is_dir()
    assert receipt.is_file()

    monkeypatch.setattr(streaming, "_read_contract", original_read)
    resumed = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=capsule,
        delivery_receipt_path=receipt,
    )
    assert resumed.evidence_capsule_ref


def test_precommit_receipt_failure_rolls_back_new_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "precommit-failure-capsule"
    receipt = tmp_path / "precommit-failure-receipt.json"

    def fail_before_receipt_publish(_path: Path, _payload: bytes) -> bool:
        raise OSError("injected precommit receipt failure")

    monkeypatch.setattr(streaming, "_atomic_write_receipt", fail_before_receipt_publish)
    with pytest.raises(OSError, match="injected precommit receipt failure"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=capsule,
            delivery_receipt_path=receipt,
        )
    assert not (run_root / "run.seal.json").exists()
    assert not capsule.exists()
    assert not receipt.exists()
    assert not tuple(tmp_path.glob(".precommit-failure-capsule.postrun-*"))


def test_success_reads_back_the_published_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    receipt = tmp_path / "readback-receipt.json"
    original_read = streaming._read_contract
    receipt_reads = 0

    def track_receipt_read(path: Path, model: type[object]) -> tuple[object, bytes]:
        nonlocal receipt_reads
        value, raw = original_read(path, model)
        if model is CalibrationRunReceipt:
            receipt_reads += 1
        return value, raw

    monkeypatch.setattr(streaming, "_read_contract", track_receipt_read)
    streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=tmp_path / "readback-capsule",
        delivery_receipt_path=receipt,
    )
    assert receipt_reads == 1


def test_successful_rerun_is_content_idempotent(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source", opaque_size=1025)
    capsule = tmp_path / "idempotent-capsule"
    receipt = tmp_path / "idempotent-receipt.json"
    first = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=capsule,
        delivery_receipt_path=receipt,
    )
    capsule_before = _tree_bytes(capsule)
    receipt_before = receipt.read_bytes()
    second = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=capsule,
        delivery_receipt_path=receipt,
    )
    assert second == first
    assert _tree_bytes(capsule) == capsule_before
    assert receipt.read_bytes() == receipt_before
    assert not tuple(tmp_path.glob(".idempotent-capsule.postrun-*"))


def test_opaque_files_are_chunked_and_frozen_source_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _closed_run(tmp_path / "source", opaque_size=16385)
    opaque = run_root / "state/domain/opaque/nested/recording.bin"
    frozen_before = _source_digest(
        iter(
            sorted(
                [
                    *(Path("src/strongwiz").rglob("*.py")),
                    *(Path("calibration").rglob("*.py")),
                ]
            )
        )
    )
    original_open = Path.open
    largest_read = 0

    class _TrackingReader:
        def __init__(self, wrapped: object) -> None:
            self._wrapped = wrapped

        def __enter__(self) -> _TrackingReader:
            self._wrapped.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._wrapped.__exit__(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            nonlocal largest_read
            assert size >= 0
            largest_read = max(largest_read, size)
            return self._wrapped.read(size)  # type: ignore[attr-defined,no-any-return]

    def tracked_open(path: Path, *args: object, **kwargs: object) -> object:
        wrapped = original_open(path, *args, **kwargs)
        if path == opaque and args and args[0] == "rb":
            return _TrackingReader(wrapped)
        return wrapped

    monkeypatch.setattr(streaming, "_CHUNK_BYTES", 257)
    monkeypatch.setattr(Path, "open", tracked_open)
    result = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=tmp_path / "chunked-capsule",
        delivery_receipt_path=tmp_path / "chunked-receipt.json",
    )
    assert result.evidence_capsule_ref
    assert largest_read == 257
    assert frozen_before == _source_digest(
        iter(
            sorted(
                [
                    *(Path("src/strongwiz").rglob("*.py")),
                    *(Path("calibration").rglob("*.py")),
                ]
            )
        )
    )


def test_source_has_no_forbidden_imports_or_unbounded_read_shortcuts() -> None:
    source_path = Path("scripts/strongwiz_streaming_postrun.py")
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not imported & {
        "arcengine",
        "calibration.core",
        "calibration.server",
        "calibration.workflow",
    }
    assert ".fetchall(" not in source
    assert ".read_bytes(" not in source
    assert ".read_text(" not in source
    assert "requests" not in imported
    assert "httpx" not in imported
    assert "urllib" not in imported
    assert "official WIN observed and assessed" not in source


def test_temp_identity_index_has_a_fixed_non_mmap_cache(tmp_path: Path) -> None:
    with streaming._disk_index(tmp_path) as index:
        assert index.execute("PRAGMA cache_size").fetchone() == (-2048,)
        assert index.execute("PRAGMA mmap_size").fetchone() == (0,)
        assert index.execute("PRAGMA temp_store").fetchone() == (1,)


def test_source_root_rejects_a_link_like_ancestor(tmp_path: Path) -> None:
    actual_parent = tmp_path / "actual"
    run_root, _ = _closed_run(actual_parent)
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(streaming.StreamingPostrunError, match="link-like"):
        streaming.seal_and_pack_run(
            run_root=linked_parent / run_root.name,
            capsule_root=tmp_path / "linked-capsule",
            delivery_receipt_path=tmp_path / "linked-receipt.json",
        )
    assert not (run_root / "run.seal.json").exists()


def test_capsule_root_rejects_a_link_like_ancestor(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    actual_parent = tmp_path / "actual-output"
    actual_parent.mkdir()
    linked_parent = tmp_path / "linked-output"
    try:
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(streaming.StreamingPostrunError, match="link-like"):
        streaming.seal_and_pack_run(
            run_root=run_root,
            capsule_root=linked_parent / "capsule",
            delivery_receipt_path=actual_parent / "receipt.json",
        )
    assert not (run_root / "run.seal.json").exists()
    assert not (actual_parent / "capsule").exists()


def test_staging_cleanup_refuses_a_replaced_directory(tmp_path: Path) -> None:
    staging = tmp_path / "owned-staging"
    staging.mkdir()
    owned_token = streaming._ownership_token(staging)
    displaced = tmp_path / "displaced-staging"
    os.replace(staging, displaced)
    staging.mkdir()
    marker = staging / "owner-data.bin"
    marker.write_bytes(b"foreign replacement")

    with pytest.raises(streaming.StreamingPostrunError, match="identity changed"):
        streaming._remove_owned_staging(staging, owned_token)
    assert marker.read_bytes() == b"foreign replacement"


def test_rejects_sqlite_schema_drift_as_a_postrun_error(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.sqlite3"
    _object_only_ledger(ledger_path, 0)
    with sqlite3.connect(ledger_path) as connection:
        connection.execute("ALTER TABLE objects ADD COLUMN undeclared TEXT")

    with (
        streaming._disk_index(tmp_path) as index,
        pytest.raises(streaming.StreamingPostrunError, match="columns do not match v1"),
    ):
        streaming._stream_source_ledger(
            ledger_path,
            tmp_path / "objects.jsonl",
            tmp_path / "receipts.jsonl",
            index,
            terminal_ref=_ref("not-present"),
        )


@pytest.mark.parametrize("malformation", ("null", "wrong-type", "oversized"))
def test_malformed_sqlite_rows_fail_with_bounded_postrun_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    ledger_path = tmp_path / "ledger.sqlite3"
    _object_only_ledger(ledger_path, 0)
    payload = canonical_bytes({"malformed": "x" * 128})
    payload_hash: object = content_hash({"malformed": "x" * 128})
    stored_payload: object = payload
    if malformation == "null":
        payload_hash = None
    elif malformation == "wrong-type":
        stored_payload = payload.decode("utf-8")
    else:
        monkeypatch.setattr(streaming, "_MAX_JSONL_LINE_BYTES", 32)
    with sqlite3.connect(ledger_path) as connection:
        connection.execute(
            "INSERT INTO objects(payload_hash, canonical_payload) VALUES (?, ?)",
            (payload_hash, stored_payload),
        )

    with (
        streaming._disk_index(tmp_path) as index,
        pytest.raises(streaming.StreamingPostrunError),
    ):
        streaming._stream_source_ledger(
            ledger_path,
            tmp_path / "objects.jsonl",
            tmp_path / "receipts.jsonl",
            index,
            terminal_ref=_ref("not-present"),
        )


def test_ledger_stream_memory_does_not_scale_with_row_count(tmp_path: Path) -> None:
    # This is intentionally approximate: tracemalloc covers Python allocations,
    # while the disk-backed SQLite index owns the growing identity set.
    small_peak = _ledger_stream_peak(tmp_path / "small", 20)
    large_peak = _ledger_stream_peak(tmp_path / "large", 3000)
    assert large_peak <= small_peak + 4 * 1024 * 1024


def test_source_ledger_sha_is_stable_across_success(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source", opaque_size=2049)
    ledger_path = run_root / "state/ledger.sqlite3"
    before = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    before_stat = ledger_path.stat()
    result = streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=tmp_path / "stable-capsule",
        delivery_receipt_path=tmp_path / "stable-receipt.json",
    )
    after_stat = ledger_path.stat()
    assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() == before
    assert result.source_ledger_sha256 == before
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )
    assert not any(
        Path(f"{ledger_path}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")
    )


def test_cli_returns_nonzero_without_leaving_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    allowed = repository / "artifacts/local"
    allowed.mkdir(parents=True)
    monkeypatch.setattr(streaming, "_REPOSITORY_ROOT", repository)
    run_root, _ = _closed_run(allowed / "source")
    os.remove(run_root / "state/domain/terminal.record.json")
    assert (
        streaming.main(
            [
                str(run_root),
                str(allowed / "cli-capsule"),
                "--receipt",
                str(allowed / "cli-receipt.json"),
            ]
        )
        == 1
    )
    assert not (allowed / "cli-capsule").exists()
    assert not (allowed / "cli-receipt.json").exists()


def test_cli_confines_all_write_targets_to_repository_local_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    (repository / "playground").mkdir(parents=True)
    (repository / "artifacts/local").mkdir(parents=True)
    monkeypatch.setattr(streaming, "_REPOSITORY_ROOT", repository)
    outside_run, _ = _closed_run(tmp_path / "outside")
    outside_capsule = tmp_path / "outside-capsule"
    outside_receipt = tmp_path / "outside-receipt.json"
    assert (
        streaming.main(
            [
                str(outside_run),
                str(outside_capsule),
                "--receipt",
                str(outside_receipt),
            ]
        )
        == 1
    )
    assert not (outside_run / "run.seal.json").exists()
    assert not outside_capsule.exists()
    assert not outside_receipt.exists()

    allowed_run, _ = _closed_run(repository / "playground" / "allowed")
    allowed_capsule = repository / "artifacts/local/allowed-capsule"
    allowed_receipt = repository / "artifacts/local/allowed-receipt.json"
    assert (
        streaming.main(
            [
                str(allowed_run),
                str(allowed_capsule),
                "--receipt",
                str(allowed_receipt),
            ]
        )
        == 0
    )
    assert allowed_capsule.is_dir()
    assert allowed_receipt.is_file()


def test_capsule_projection_files_remain_v1_canonical_jsonl(tmp_path: Path) -> None:
    run_root, _ = _closed_run(tmp_path / "source")
    capsule = tmp_path / "canonical-capsule"
    streaming.seal_and_pack_run(
        run_root=run_root,
        capsule_root=capsule,
        delivery_receipt_path=tmp_path / "canonical-receipt.json",
    )
    manifest = capsule / CAPSULE_MANIFEST_PATH
    assert manifest.read_bytes() == canonical_bytes(
        streaming.verify_evidence_capsule_streaming(capsule)
    )
    assert (capsule / CAPSULE_OBJECTS_PATH).read_bytes().endswith(b"\n")
    assert (capsule / CAPSULE_RECEIPTS_PATH).read_bytes().endswith(b"\n")
