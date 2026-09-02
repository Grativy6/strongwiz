from __future__ import annotations

from pathlib import Path

import pytest

from examples.reference_counter_lab import (
    SUCCESS_STATE,
    build_parser,
    run_reference_counter_lab,
)
from strongwiz.canonical import content_hash, parse_strict_json
from strongwiz.drivers import TerminalAuthority
from strongwiz.lab import LabError, verify_evidence_capsule, verify_lab
from strongwiz.ledger import SQLiteLedger


def ref(label: str) -> str:
    return content_hash({"test_authorization": label})


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_non_arc_reference_lab_runs_restores_seals_and_packs(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    capsule = tmp_path / "capsule"

    receipt = run_reference_counter_lab(
        lab,
        capsule,
        authorization_root_ref=ref("caller-authorized-one-local-write"),
    )

    assert receipt.final_environment_state == SUCCESS_STATE
    assert receipt.terminal_authority is TerminalAuthority.SUCCESS
    assert receipt.action_count == 1
    assert receipt.completion_genuinely_observed
    verification = verify_lab(lab)
    assert verification.run_seal_ref == receipt.run_seal_ref
    assert not verification.current_state_matches_genesis
    assert verification.domain_state_entry_count == 1
    assert (lab / "state" / "domain" / "counter.json").read_bytes() == (
        b'{"counter":1,"epoch":1}'
    )

    packed = verify_evidence_capsule(capsule, expected_capsule_ref=receipt.evidence_capsule_ref)
    assert packed.completion_genuinely_observed
    assert packed.terminal_evidence_ref
    assert receipt.replay_evidence_path == "ledger/receipts.jsonl"

    object_rows = [
        parse_strict_json(line)
        for line in (capsule / "ledger" / "objects.jsonl").read_bytes().splitlines()
    ]
    schemas = {
        row["payload"].get("schema")
        for row in object_rows
        if isinstance(row, dict) and isinstance(row.get("payload"), dict)
    }
    assert "strongwiz.task-grant.v1" in schemas
    assert "strongwiz.lab-policy-decision.v1" in schemas
    assert "strongwiz.session-checkpoint.v2" in schemas

    with SQLiteLedger(lab / "state" / "ledger.sqlite3", readonly=True) as ledger:
        count, head = ledger.verify()
    assert count >= 7
    assert head == verification.ledger_seal.receipt_head


def test_reference_lab_refuses_nonempty_rerun_without_mutation(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    capsule = tmp_path / "capsule"
    authorization = ref("single-use-test-invocation")
    run_reference_counter_lab(lab, capsule, authorization_root_ref=authorization)
    lab_before = tree_bytes(lab)
    capsule_before = tree_bytes(capsule)

    with pytest.raises(LabError, match="absent or empty root"):
        run_reference_counter_lab(lab, capsule, authorization_root_ref=authorization)

    assert tree_bytes(lab) == lab_before
    assert tree_bytes(capsule) == capsule_before


def test_cli_defaults_stay_in_repository_local_playground() -> None:
    args = build_parser().parse_args(["--authorize-local-demo"])
    repository_root = Path(__file__).resolve().parents[1]
    assert args.lab_root.resolve().is_relative_to(repository_root)
    assert args.capsule_root.resolve().is_relative_to(repository_root)
    assert "playground" in args.lab_root.parts
    assert "playground" in args.capsule_root.parts
