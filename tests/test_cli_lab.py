from __future__ import annotations

from pathlib import Path

from strongwiz.canonical import canonical_bytes
from strongwiz.cli import main
from strongwiz.contracts import CONTRACT_SCHEMA, CostVector
from strongwiz.lab import LabManifest, RunSpec
from strongwiz.ledger import SQLiteLedger

from .support import ref


def lab_contracts() -> tuple[LabManifest, RunSpec]:
    manifest = LabManifest(
        lab_id="cli-reference-lab",
        lab_version="1",
        purpose="exercise generic lab genesis",
        strongwiz_version="0.2.0",
        kernel_artifact_ref=ref("kernel"),
        contract_schema=CONTRACT_SCHEMA,
        source_identity_refs=(ref("source-stack"),),
    )
    spec = RunSpec(
        run_id="cli-reference-run",
        lab_manifest_ref=manifest.digest,
        objective="solve a generic reference problem",
        success_condition="reference domain reports SUCCESS",
        success_state="SUCCESS",
        terminal_authority_source="reference-domain-v1",
        evaluation_class="synthetic-reference",
        frozen_runtime_ref=ref("runtime"),
        model_driver_id="reference-model",
        model_driver_version="1",
        model_driver_artifact_ref=ref("model"),
        domain_adapter_id="reference-domain",
        domain_adapter_version="1",
        domain_adapter_artifact_ref=ref("domain"),
        seed=0,
        resource_budget=CostVector(compute_units=100),
        allowed_action_names=("inspect",),
    )
    return manifest, spec


def test_cli_initializes_and_verifies_a_genesis_lab(tmp_path: Path, capsys: object) -> None:
    manifest, spec = lab_contracts()
    manifest_path = tmp_path / "manifest.json"
    spec_path = tmp_path / "run.json"
    manifest_path.write_bytes(canonical_bytes(manifest))
    spec_path.write_bytes(canonical_bytes(spec))
    lab_root = tmp_path / "lab"

    assert (
        main(
            [
                "lab",
                "init",
                str(lab_root),
                "--manifest",
                str(manifest_path),
                "--run-spec",
                str(spec_path),
            ]
        )
        == 0
    )
    init_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"ledger_receipt_count":0' in init_output
    assert main(["lab", "verify", str(lab_root), "--require-genesis"]) == 0
    verify_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"current_state_matches_genesis":true' in verify_output


def test_cli_verifies_declared_source_registry(capsys: object) -> None:
    assert main(["verify-sources", "docs/source-identities.json"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"source_count":14' in output
    assert '"valid":true' in output


def test_cli_initializes_and_audits_blank_kevin_workspace(
    tmp_path: Path, capsys: object
) -> None:
    assert main(["kevin", "schema"]) == 0
    schema_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"contract_version":"strongwiz.kevin-speak.v1"' in schema_output
    assert '"next_round_recommendation"' in schema_output

    ledger_path = tmp_path / "kevin.sqlite3"
    base = [str(ledger_path), "--workspace-id", "cli-kevin"]

    assert main(["kevin", "init", *base]) == 0
    init_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"codebook_count":1' in init_output
    assert '"entry_count":0' in init_output

    assert main(["kevin", "verify", *base]) == 0
    verify_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"exact_round_trips":true' in verify_output

    assert main(["kevin", "table", *base]) == 0
    table_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"translations":[]' in table_output


def test_cli_prints_pal23_and_scribe_schema_claim_ceilings(capsys: object) -> None:
    assert main(["pal23", "schema"]) == 0
    pal_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"schema":"strongwiz.pal23-adapter.v1"' in pal_output
    assert "not package-wide PAL conformance" in pal_output

    assert main(["scribe", "schema"]) == 0
    scribe_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"schema":"strongwiz.scribe.v1"' in scribe_output
    assert "representation-only recommendations" in scribe_output


def test_cli_seals_packs_and_verifies_a_partial_run(tmp_path: Path, capsys: object) -> None:
    manifest, spec = lab_contracts()
    manifest_path = tmp_path / "manifest.json"
    spec_path = tmp_path / "run.json"
    manifest_path.write_bytes(canonical_bytes(manifest))
    spec_path.write_bytes(canonical_bytes(spec))
    lab_root = tmp_path / "lab"
    capsule_root = tmp_path / "capsule"
    main(
        [
            "lab",
            "init",
            str(lab_root),
            "--manifest",
            str(manifest_path),
            "--run-spec",
            str(spec_path),
        ]
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    with SQLiteLedger(lab_root / manifest.layout.ledger_path) as ledger:
        terminal_ref = ledger.put_object({"state": "STOPPED"})
        ledger.append(
            occurrence_id="cli-run-stopped",
            kind="terminal_evidence",
            account_id="cli",
            account_version=0,
            payload={"terminal_ref": terminal_ref},
            object_refs=(terminal_ref,),
        )

    assert (
        main(
            [
                "lab",
                "seal-run",
                str(lab_root),
                "--disposition",
                "partial",
                "--terminal-state",
                "STOPPED",
                "--terminal-evidence-ref",
                terminal_ref,
                "--summary",
                "bounded reference stopped",
            ]
        )
        == 0
    )
    seal_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"disposition":"partial"' in seal_output
    assert main(["lab", "pack-evidence", str(lab_root), str(capsule_root)]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert main(["lab", "verify-capsule", str(capsule_root)]) == 0
    capsule_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"complete_sqlite_projection":true' in capsule_output
