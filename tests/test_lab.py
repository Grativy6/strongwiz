from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.canonical import canonical_bytes, content_hash, sha256_bytes
from strongwiz.contracts import CostVector
from strongwiz.lab import (
    CAPSULE_MANIFEST_PATH,
    CAPSULE_OBJECTS_PATH,
    CAPSULE_RECEIPTS_PATH,
    EvidenceCapsuleManifest,
    ExternalDomainStateSeal,
    LabError,
    LabLayout,
    LabManifest,
    PromotionReceipt,
    RunDisposition,
    RunSpec,
    initialize_lab,
    pack_evidence,
    seal_run,
    verify_evidence_capsule,
    verify_lab,
    verify_lab_genesis,
)
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger


def ref(label: str) -> str:
    return content_hash({"test-ref": label})


def manifest(**updates: object) -> LabManifest:
    value = LabManifest(
        lab_id="lab-synthetic-001",
        lab_version="0.2.0",
        purpose="exercise a model-neutral synthetic laboratory",
        strongwiz_version="0.2.0",
        kernel_artifact_ref=ref("kernel"),
        contract_schema="strongwiz.contract.v1",
        capability_refs=(ref("capability"),),
        policy_refs=(ref("policy"),),
        source_identity_refs=(ref("source"),),
    )
    return value.model_copy(update=updates)


def run_spec(lab: LabManifest, **updates: object) -> RunSpec:
    value = RunSpec(
        run_id="run-synthetic-001",
        lab_manifest_ref=lab.digest,
        objective="reach the declared synthetic terminal state",
        success_condition="the synthetic domain authority returns WIN",
        success_state="WIN",
        terminal_authority_source="synthetic-domain-v1",
        evaluation_class="synthetic",
        frozen_runtime_ref=ref("runtime"),
        model_driver_id="model-test",
        model_driver_version="1",
        model_driver_artifact_ref=ref("model"),
        domain_adapter_id="domain-test",
        domain_adapter_version="1",
        domain_adapter_artifact_ref=ref("domain"),
        seed=17,
        resource_budget=CostVector(
            environment_actions=10,
            wall_clock_ms=1_000,
            compute_units=100,
            memory_bytes=1_024,
        ),
        allowed_action_names=("inspect", "move"),
        declared_input_refs=(ref("input"),),
        policy_refs=(ref("policy"),),
    )
    return value.model_copy(update=updates)


def initialize(tmp_path: Path) -> tuple[Path, LabManifest, RunSpec]:
    root = tmp_path / "lab"
    lab = manifest()
    spec = run_spec(lab)
    initialize_lab(root, manifest=lab, run_spec=spec)
    return root, lab, spec


def add_result_evidence(root: Path, lab: LabManifest) -> tuple[str, str, str]:
    with SQLiteLedger(root / lab.layout.ledger_path) as ledger:
        terminal_ref = ledger.put_object({"source": "synthetic-domain-v1", "state": "WIN"})
        observation_ref = ledger.put_object({"observation": "door-open", "state": "WIN"})
        envelope = ledger.append(
            occurrence_id="outcome-0001",
            kind="terminal_observation",
            account_id="account-1",
            account_version=0,
            payload={"concise_summary": "the declared terminal authority returned WIN"},
            object_refs=(terminal_ref, observation_ref),
        )
    return terminal_ref, observation_ref, envelope.receipt_hash


def seal_success(root: Path, lab: LabManifest) -> str:
    terminal_ref, _, _ = add_result_evidence(root, lab)
    seal = seal_run(
        root,
        disposition=RunDisposition.SUCCESS_OBSERVED,
        terminal_state="WIN",
        terminal_evidence_ref=terminal_ref,
        completion_genuinely_observed=True,
        concise_result_summary="the declared synthetic authority reported WIN",
    )
    return seal.digest


def test_contracts_are_closed_immutable_and_content_addressed() -> None:
    lab = manifest()
    same = manifest()
    assert lab.digest == same.digest
    assert canonical_bytes(lab) == canonical_bytes(same)
    with pytest.raises(ValidationError):
        LabManifest.model_validate({**lab.model_dump(), "undeclared": True})
    with pytest.raises(ValidationError):
        lab.lab_id = "rewritten"  # type: ignore[misc]


@pytest.mark.parametrize(
    "unsafe",
    (
        "../ledger.sqlite3",
        "/absolute",
        "C:/escape",
        "state\\ledger",
        "a//b",
        "./a",
        "state/CON",
        "state/ledger.",
        "state/trailing ",
    ),
)
def test_layout_refuses_unsafe_or_noncanonical_paths(unsafe: str) -> None:
    with pytest.raises(ValidationError):
        LabLayout(ledger_path=unsafe)


def test_layout_refuses_control_files_inside_domain_state() -> None:
    with pytest.raises(ValidationError):
        LabLayout(domain_state_path="state")


def test_run_spec_requires_sorted_sets_and_exact_digests() -> None:
    lab = manifest()
    with pytest.raises(ValidationError):
        run_spec(lab, allowed_action_names=("move", "inspect"))
    with pytest.raises(ValidationError):
        run_spec(lab, model_driver_artifact_ref="not-a-digest")


def test_genesis_run_cannot_inherit_prior_state_or_manufacture_authority() -> None:
    lab = manifest()
    with pytest.raises(ValidationError):
        run_spec(lab, prior_run_refs=(ref("old-run"),))
    with pytest.raises(ValidationError):
        run_spec(lab, prior_domain_state_refs=(ref("old-state"),))
    with pytest.raises(ValidationError):
        run_spec(lab, shadow_only=False)
    granted = run_spec(lab, shadow_only=False, execution_grant_ref=ref("grant"))
    assert granted.authority_ceiling == "supplied_grant_only"
    assert not granted.self_authorizing


def test_initialize_establishes_and_verifies_exact_zero_state(tmp_path: Path) -> None:
    root, lab, spec = initialize(tmp_path)
    verification = verify_lab_genesis(root)
    assert verification.lab_manifest_ref == lab.digest
    assert verification.run_spec_ref == spec.digest
    assert verification.ledger_seal.receipt_count == 0
    assert verification.ledger_seal.object_count == 0
    assert verification.ledger_seal.receipt_head is None
    assert verification.domain_state_entry_count == 0
    assert verification.current_state_matches_genesis
    assert canonical_bytes(lab) == (root / "lab.manifest.json").read_bytes()


def test_lab_verification_is_non_mutating(tmp_path: Path) -> None:
    root, _, _ = initialize(tmp_path)
    before = {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }
    verify_lab(root)
    after = {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }
    assert after == before


def test_initialize_refuses_nonempty_root_and_wrong_lab_binding(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("owner data", encoding="utf-8")
    lab = manifest()
    with pytest.raises(LabError, match="absent or empty"):
        initialize_lab(occupied, manifest=lab, run_spec=run_spec(lab))
    assert (occupied / "keep.txt").read_text(encoding="utf-8") == "owner data"

    wrong = run_spec(lab, lab_manifest_ref=ref("other-lab"))
    with pytest.raises(LabError, match="bind"):
        initialize_lab(tmp_path / "wrong", manifest=lab, run_spec=wrong)


def test_genesis_verifier_detects_later_ledger_and_domain_state(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    with SQLiteLedger(root / lab.layout.ledger_path) as ledger:
        ledger.put_object({"new": "state"})
    (root / lab.layout.domain_state_path / "checkpoint.bin").write_bytes(b"state")
    verification = verify_lab(root)
    assert not verification.current_state_matches_genesis
    assert verification.ledger_seal.object_count == 1
    assert verification.domain_state_entry_count == 1
    with pytest.raises(LabError, match="zero-state"):
        verify_lab_genesis(root)


def test_genesis_counts_empty_domain_directories_as_state(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    (root / lab.layout.domain_state_path / "empty-checkpoint").mkdir()
    verification = verify_lab(root)
    assert verification.domain_state_entry_count == 1
    assert not verification.current_state_matches_genesis
    with pytest.raises(LabError, match="zero-state"):
        verify_lab_genesis(root)


def test_verify_refuses_symlinks_inside_lab(tmp_path: Path) -> None:
    root, _, _ = initialize(tmp_path)
    target = root / "ordinary.txt"
    target.write_text("ordinary", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic-link creation is unavailable on this host")
    with pytest.raises(LabError, match="symbolic links"):
        verify_lab(root)


def test_verify_refuses_non_symlink_link_like_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, lab, _ = initialize(tmp_path)
    junction_shape = root / lab.layout.domain_state_path / "junction-shape"
    junction_shape.mkdir()
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self.name == "junction-shape",
    )
    with pytest.raises(LabError, match="link-like"):
        verify_lab(root)


def test_seal_run_binds_terminal_evidence_and_complete_ledger(tmp_path: Path) -> None:
    root, lab, spec = initialize(tmp_path)
    terminal_ref, observation_ref, receipt_head = add_result_evidence(root, lab)
    seal = seal_run(
        root,
        disposition=RunDisposition.SUCCESS_OBSERVED,
        terminal_state="WIN",
        terminal_evidence_ref=terminal_ref,
        completion_genuinely_observed=True,
        concise_result_summary="the exact declared terminal authority returned WIN",
    )
    assert seal.run_spec_ref == spec.digest
    assert seal.ledger_seal.receipt_count == 1
    assert seal.ledger_seal.object_count == 3
    assert seal.ledger_seal.receipt_head == receipt_head
    assert terminal_ref != observation_ref
    assert verify_lab(root).run_seal_ref == seal.digest


def test_seal_run_refuses_false_success_and_unknown_evidence(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    terminal_ref, _, _ = add_result_evidence(root, lab)
    with pytest.raises(LabError, match="success state"):
        seal_run(
            root,
            disposition=RunDisposition.SUCCESS_OBSERVED,
            terminal_state="NOT_FINISHED",
            terminal_evidence_ref=terminal_ref,
            completion_genuinely_observed=True,
            concise_result_summary="not actually done",
        )
    with pytest.raises(LabError, match="content object"):
        seal_run(
            root,
            disposition=RunDisposition.PARTIAL,
            terminal_state="NOT_FINISHED",
            terminal_evidence_ref=ref("missing"),
            completion_genuinely_observed=False,
            concise_result_summary="bounded partial result",
        )


def test_seal_run_refuses_an_orphan_terminal_object(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    with SQLiteLedger(root / lab.layout.ledger_path) as ledger:
        terminal_ref = ledger.put_object({"source": "synthetic-domain-v1", "state": "WIN"})
        ledger.append(
            occurrence_id="unrelated-0001",
            kind="unrelated_observation",
            account_id="account-1",
            account_version=0,
            payload={"concise_summary": "this receipt does not bind the terminal object"},
        )
    with pytest.raises(LabError, match="bound by a sealed ledger receipt"):
        seal_run(
            root,
            disposition=RunDisposition.SUCCESS_OBSERVED,
            terminal_state="WIN",
            terminal_evidence_ref=terminal_ref,
            completion_genuinely_observed=True,
            concise_result_summary="must not promote an orphan object",
        )


def test_run_disposition_and_completion_marker_must_agree() -> None:
    lab = manifest()
    spec = run_spec(lab)
    with pytest.raises(ValidationError, match="must agree"):
        from strongwiz.lab import ExternalLedgerSeal, RunSeal

        empty = ExternalLedgerSeal(
            receipt_count=0,
            receipt_head=None,
            object_count=1,
            objects_projection_ref=ref("objects"),
            receipts_projection_ref=ref("receipts"),
        )
        domain = ExternalDomainStateSeal(
            entry_count=0,
            entries=(),
            projection_ref=content_hash(()),
        )
        RunSeal(
            run_id=spec.run_id,
            lab_manifest_ref=lab.digest,
            run_spec_ref=spec.digest,
            genesis_ref=ref("genesis"),
            ledger_seal=empty,
            domain_state_seal=domain,
            disposition=RunDisposition.PARTIAL,
            terminal_state="WIN",
            terminal_evidence_ref=ref("terminal"),
            completion_genuinely_observed=True,
            terminal_authority_source=spec.terminal_authority_source,
            concise_result_summary="inconsistent result",
        )


def test_run_seal_is_idempotent_but_cannot_be_rewritten(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    terminal_ref, _, _ = add_result_evidence(root, lab)
    first = seal_run(
        root,
        disposition=RunDisposition.SUCCESS_OBSERVED,
        terminal_state="WIN",
        terminal_evidence_ref=terminal_ref,
        completion_genuinely_observed=True,
        concise_result_summary="observed WIN",
    )
    second = seal_run(
        root,
        disposition=RunDisposition.SUCCESS_OBSERVED,
        terminal_state="WIN",
        terminal_evidence_ref=terminal_ref,
        completion_genuinely_observed=True,
        concise_result_summary="observed WIN",
    )
    assert first == second
    with pytest.raises(LabError, match="different content"):
        seal_run(
            root,
            disposition=RunDisposition.SUCCESS_OBSERVED,
            terminal_state="WIN",
            terminal_evidence_ref=terminal_ref,
            completion_genuinely_observed=True,
            concise_result_summary="attempted historical rewrite",
        )


def test_ledger_change_after_run_seal_is_detected(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    with SQLiteLedger(root / lab.layout.ledger_path) as ledger:
        ledger.put_object({"late": "mutation"})
    with pytest.raises(LabError, match="sealed lab state"):
        verify_lab(root)


def test_domain_state_change_after_run_seal_is_detected(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    domain_file = root / lab.layout.domain_state_path / "state.json"
    domain_file.write_text('{"counter":1}', encoding="utf-8")
    seal_success(root, lab)
    domain_file.write_text('{"counter":2}', encoding="utf-8")
    with pytest.raises(LabError, match="sealed lab state"):
        verify_lab(root)


def test_pack_exports_all_objects_receipts_and_external_seal(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    capsule = pack_evidence(root, capsule_root, capsule_name="synthetic-proof")
    verified = verify_evidence_capsule(capsule_root, expected_capsule_ref=capsule.digest)
    assert verified == capsule
    assert capsule.ledger_seal.receipt_count == 1
    assert capsule.ledger_seal.object_count == 3
    assert (capsule_root / CAPSULE_OBJECTS_PATH).read_bytes().count(b"\n") == 3
    assert (capsule_root / CAPSULE_RECEIPTS_PATH).read_bytes().count(b"\n") == 1
    assert (capsule_root / CAPSULE_MANIFEST_PATH).read_bytes() == canonical_bytes(capsule)
    assert capsule.complete_sqlite_projection
    assert capsule.complete_domain_state_projection
    assert capsule.authority == "NONE"


def test_pack_exports_and_verifies_complete_domain_state(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    domain_root = root / lab.layout.domain_state_path
    (domain_root / "nested" / "empty").mkdir(parents=True)
    (domain_root / "nested" / "state.bin").write_bytes(b"\x00strongwiz-state\xff")
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    with pytest.raises(LabError, match="opaque and unsanitized"):
        pack_evidence(root, capsule_root)
    capsule = pack_evidence(
        root,
        capsule_root,
        acknowledge_opaque_domain_state=True,
    )
    assert capsule.domain_state_seal.entry_count == 3
    assert capsule.domain_state_seal.content_handling == "opaque_unsanitized_bytes"
    assert capsule.domain_state_disclosure_status == (
        "opaque_unsanitized_not_publication_reviewed"
    )
    assert capsule.opaque_domain_state_copy_acknowledged
    assert (capsule_root / "domain-state" / "nested" / "empty").is_dir()
    state_path = capsule_root / "domain-state" / "nested" / "state.bin"
    assert state_path.read_bytes() == b"\x00strongwiz-state\xff"
    assert verify_evidence_capsule(capsule_root) == capsule

    state_path.write_bytes(b"mutated")
    with pytest.raises(LabError):
        verify_evidence_capsule(capsule_root)


def test_capsule_verifier_rejects_an_undeclared_empty_directory(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    pack_evidence(root, capsule_root)
    (capsule_root / "undeclared-empty").mkdir()
    with pytest.raises(LabError, match="undeclared"):
        verify_evidence_capsule(capsule_root)


def test_pack_refuses_a_destination_inside_the_sealed_lab(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    destination = root / "embedded-capsule"
    with pytest.raises(LabError, match="disjoint"):
        pack_evidence(root, destination)
    assert not destination.exists()
    verify_lab(root)


def test_pack_is_content_idempotent_and_refuses_different_existing_capsule(
    tmp_path: Path,
) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    first = pack_evidence(root, capsule_root)
    second = pack_evidence(root, capsule_root)
    assert first == second
    manifest_path = capsule_root / CAPSULE_MANIFEST_PATH
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    with pytest.raises(LabError):
        pack_evidence(root, capsule_root)


@pytest.mark.parametrize("mutation", ("change", "delete", "extra"))
def test_capsule_verifier_rejects_any_file_set_or_digest_mutation(
    tmp_path: Path, mutation: str
) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    pack_evidence(root, capsule_root)
    objects = capsule_root / CAPSULE_OBJECTS_PATH
    if mutation == "change":
        objects.write_bytes(objects.read_bytes().replace(b"WIN", b"WON", 1))
    elif mutation == "delete":
        objects.unlink()
    else:
        (capsule_root / "undeclared.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(LabError):
        verify_evidence_capsule(capsule_root)


def test_pack_refuses_hidden_chain_of_thought_fields(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    ledger_path = root / lab.layout.ledger_path
    with SQLiteLedger(ledger_path) as ledger:
        ledger.put_object({"chain-of-thought": "private scratch reasoning"})
    with pytest.raises(LabError, match="private reasoning"):
        verify_lab(root)


def test_capsule_verifier_checks_receipt_closure_even_if_file_hashes_are_resealed(
    tmp_path: Path,
) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    pack_evidence(root, capsule_root)

    receipts_path = capsule_root / CAPSULE_RECEIPTS_PATH
    raw_receipts = receipts_path.read_bytes()
    assert raw_receipts
    receipts_path.write_bytes(b"")
    manifest_path = capsule_root / CAPSULE_MANIFEST_PATH
    data = EvidenceCapsuleManifest.model_validate_json(manifest_path.read_bytes())
    files = tuple(
        item.model_copy(update={"size_bytes": 0, "sha256": sha256_bytes(b"")})
        if item.relative_path == CAPSULE_RECEIPTS_PATH
        else item
        for item in data.files
    )
    # A forged outer hash still cannot satisfy both SHA-256 and the sealed receipt projection.
    forged = data.model_copy(update={"files": files})
    manifest_path.write_bytes(canonical_bytes(forged))
    with pytest.raises(LabError):
        verify_evidence_capsule(capsule_root)


def test_capsule_verifier_enforces_sqlite_receipt_identity_uniqueness(
    tmp_path: Path,
) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    pack_evidence(root, capsule_root)

    receipts_path = capsule_root / CAPSULE_RECEIPTS_PATH
    original = ReceiptEnvelope.model_validate_json(receipts_path.read_bytes().splitlines()[0])
    duplicate_base = original.model_dump(mode="json", by_alias=True)
    duplicate_base.pop("receipt_hash")
    duplicate_base["sequence"] = 1
    duplicate_base["previous_receipt_hash"] = original.receipt_hash
    duplicate = ReceiptEnvelope(
        **duplicate_base,
        receipt_hash=content_hash(duplicate_base),
    )
    forged_receipts = canonical_bytes(original) + b"\n" + canonical_bytes(duplicate) + b"\n"
    receipts_path.write_bytes(forged_receipts)

    capsule_path = capsule_root / CAPSULE_MANIFEST_PATH
    capsule = EvidenceCapsuleManifest.model_validate_json(capsule_path.read_bytes())
    files = tuple(
        item.model_copy(
            update={
                "size_bytes": len(forged_receipts),
                "sha256": sha256_bytes(forged_receipts),
            }
        )
        if item.relative_path == CAPSULE_RECEIPTS_PATH
        else item
        for item in capsule.files
    )
    capsule_path.write_bytes(canonical_bytes(capsule.model_copy(update={"files": files})))

    with pytest.raises(LabError, match="SQLite identity uniqueness"):
        verify_evidence_capsule(capsule_root)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("run_id", "different-run"),
        ("terminal_authority_source", "different-authority"),
    ),
)
def test_capsule_verifier_binds_inner_run_identity_and_terminal_authority(
    tmp_path: Path,
    field: str,
    forged_value: str,
) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    capsule_root = tmp_path / "capsule"
    pack_evidence(root, capsule_root)

    run_seal_path = capsule_root / "run.seal.json"
    from strongwiz.lab import RunSeal

    run_seal = RunSeal.model_validate_json(run_seal_path.read_bytes())
    forged_seal = run_seal.model_copy(update={field: forged_value})
    forged_seal_bytes = canonical_bytes(forged_seal)
    run_seal_path.write_bytes(forged_seal_bytes)

    capsule_path = capsule_root / CAPSULE_MANIFEST_PATH
    capsule = EvidenceCapsuleManifest.model_validate_json(capsule_path.read_bytes())
    files = tuple(
        item.model_copy(
            update={
                "size_bytes": len(forged_seal_bytes),
                "sha256": sha256_bytes(forged_seal_bytes),
            }
        )
        if item.relative_path == "run.seal.json"
        else item
        for item in capsule.files
    )
    forged_capsule = capsule.model_copy(
        update={"files": files, "run_seal_ref": forged_seal.digest}
    )
    capsule_path.write_bytes(canonical_bytes(forged_capsule))

    with pytest.raises(LabError, match="cross-object bindings"):
        verify_evidence_capsule(capsule_root)


def test_promotion_receipt_is_only_a_reviewable_candidate() -> None:
    receipt = PromotionReceipt(
        candidate_id="candidate-cadence-001",
        source_capsule_ref=ref("capsule"),
        source_run_seal_ref=ref("run-seal"),
        candidate_mechanism_ref=ref("mechanism"),
        target_scope="generic cadence policy",
        falsifiable_claim="the candidate reduces actions under a matched ablation",
        concise_rationale="the sealed run suggests a bounded mechanism worth testing",
        evidence_refs=(ref("evidence"),),
    )
    assert receipt.status == "proposed_not_adopted"
    assert receipt.claim_ceiling == "candidate_mechanism_only"
    assert receipt.requires_independent_review
    assert receipt.requires_ablation
    assert not receipt.transfers_domain_state
    assert not receipt.transfers_action_sequences
    assert not receipt.transfers_authority
    assert receipt.authority == "NONE"
    with pytest.raises(ValidationError):
        receipt.model_copy(update={"status": "adopted"})
    with pytest.raises(ValidationError):
        receipt.model_copy(update={"transfers_authority": True})


def test_sqlite_tamper_is_rejected_before_sealing(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    terminal_ref, _, _ = add_result_evidence(root, lab)
    connection = sqlite3.connect(root / lab.layout.ledger_path)
    try:
        connection.execute(
            "UPDATE objects SET canonical_payload = ? WHERE payload_hash = ?",
            (b'{"state":"NOT_WIN"}', terminal_ref),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(LabError):
        seal_run(
            root,
            disposition=RunDisposition.SUCCESS_OBSERVED,
            terminal_state="WIN",
            terminal_evidence_ref=terminal_ref,
            completion_genuinely_observed=True,
            concise_result_summary="must not seal corrupt evidence",
        )


def test_capsule_root_symlink_is_refused(tmp_path: Path) -> None:
    root, lab, _ = initialize(tmp_path)
    seal_success(root, lab)
    actual = tmp_path / "actual-capsule"
    pack_evidence(root, actual)
    link = tmp_path / "linked-capsule"
    try:
        os.symlink(actual, link, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic-link creation is unavailable on this host")
    with pytest.raises(LabError, match="symbolic link"):
        verify_evidence_capsule(link)
