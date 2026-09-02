from __future__ import annotations

import ast
import json
import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from calibration_003.cli import _load_declared_carry, _parser
from calibration_003.models import (
    Calibration003Error,
    Calibration003Plan,
    CampaignClaimLabel,
    CampaignVerification,
    OperatorBinding,
    SyntheticPreflightReceipt,
    V2CarryConsumptionRules,
    V2CarryFact,
    V2CarryFactStatus,
    V2CarryPacket,
    V2CarrySourceArtifact,
    V2CarrySourceCampaign,
    V2CarrySourceDisposition,
    calibration_003_schema_bundle,
)
from calibration_003.workflow import (
    CAMPAIGN_INDEX_PATH,
    CAMPAIGN_PREPARATION_MARKER_PATH,
    SYNTHETIC_PREFLIGHT_RECEIPT_PATH,
    LoadedV2CarryPacket,
    _repository_file,
    load_plan,
    load_v2_carry_packet,
    prepare_campaign,
    run_synthetic_preflight,
    verify_campaign,
)
from strongwiz.canonical import canonical_bytes, content_hash, sha256_bytes
from strongwiz.contracts import CostVector
from strongwiz.lab import LabError, LabManifest, RunSpec
from strongwiz.ledger import SQLiteLedger
from strongwiz.scribe import ScribeDriverBinding, ScribePolicy


def _ref(label: str) -> str:
    return content_hash({"calibration-003-test": label})


def _plan(**updates: object) -> Calibration003Plan:
    value = Calibration003Plan(
        campaign_id="calibration-003-test",
        claim_label=CampaignClaimLabel.FRESH_MATCHED_ABLATION,
        objective="compare matched representation policies on one later declared task",
        success_condition="the later declared terminal authority reports its success state",
        success_state="SUCCESS",
        evaluation_class="future-bounded-calibration",
        strongwiz_version="0.4.0.dev0",
        kernel_artifact_ref=_ref("kernel"),
        frozen_runtime_ref=_ref("runtime"),
        pal23_profile_ref=_ref("pal23-profile"),
        operator=OperatorBinding(
            operator_id="codex-operated-test",
            operator_version="fixture-1",
            operator_artifact_ref=_ref("operator"),
            hosted_weights_bound=False,
        ),
        domain={
            "adapter_id": "future-domain-adapter",
            "adapter_version": "fixture-1",
            "adapter_artifact_ref": _ref("domain-adapter"),
            "terminal_authority_source": "future declared environment",
        },
        scribe_driver=ScribeDriverBinding(
            driver_id="representation-only-scribe",
            driver_version="fixture-1",
            driver_artifact_ref=_ref("scribe-driver"),
        ),
        scribe_policy=ScribePolicy(),
        source_identity_refs=tuple(sorted((_ref("pal-spine"), _ref("pal-tests")))),
        seed=23,
        resource_budget=CostVector(
            environment_actions=300,
            wall_clock_ms=3_600_000,
            compute_units=1_000,
            memory_bytes=2_147_483_648,
            context_tokens=500_000,
            validation_units=100,
            transport_units=100,
            output_units=100,
        ),
    )
    return value.model_copy(update=updates)


def _load_arm(root: Path, name: str) -> tuple[LabManifest, RunSpec]:
    arm = root / "arms" / name
    return (
        LabManifest.model_validate_json((arm / "lab.manifest.json").read_bytes()),
        RunSpec.model_validate_json((arm / "run.spec.json").read_bytes()),
    )


def _carry_packet(repository_root: Path) -> tuple[Path, LoadedV2CarryPacket]:
    artifact_path = repository_root / "evidence" / "v2-result.json"
    artifact_path.parent.mkdir(parents=True)
    fact_evidence_ref = _ref("v2-fact-evidence")
    artifact = canonical_bytes(
        {
            "schema": "strongwiz.arc-agi3-calibration-campaign-summary.v1",
            "stages": [{"run_seal_ref": fact_evidence_ref}],
            "final_handoff": {
                "handoff_ref": _ref("v2-final-handoff"),
                "selected_recommendation_ref": _ref("v2-selected-recommendation"),
            },
        }
    )
    artifact_path.write_bytes(artifact)
    packet = V2CarryPacket(
        packet_id="calibration-003-test-v2-carry",
        classification="same-game adaptive-successor evidence",
        claim_ceiling=(
            "concise reanalysis of one public-game campaign; not fresh, unseen, "
            "independent, causal, or generalization evidence"
        ),
        source_campaign=V2CarrySourceCampaign(
            campaign_id="calibration-002-test",
            game_id="public-test-game",
            toolbelt_commit="1" * 40,
            toolbelt_tree="2" * 40,
        ),
        source_artifacts=(
            V2CarrySourceArtifact(
                path="evidence/v2-result.json",
                sha256=sha256_bytes(artifact),
            ),
        ),
        source_disposition=V2CarrySourceDisposition(
            levels_completed=1,
            win_levels=7,
            non_reset_actions=9,
            resets=1,
            total_environment_calls=10,
            elapsed_wall_ms=1_000,
        ),
        excluded_material=(
            "action_sequences",
            "authority",
            "authorization",
            "domain_state",
            "frames",
            "permission",
            "private_reasoning",
            "raw_traces",
            "replay_state",
        ),
        facts=(
            V2CarryFact(
                fact_id="v2-test-unresolved",
                status=V2CarryFactStatus.UNRESOLVED,
                statement="The next mechanism remains unresolved.",
                scope="one bounded public-game test",
                evidence_refs=(fact_evidence_ref,),
                counterevidence_refs=(),
                predecessor_fact_ids=(),
                supersedes_fact_ids=(),
                uncertainty="No accepted transition was observed.",
                reopening_condition="A smallest discriminating probe returns evidence.",
            ),
        ),
        consumption_rules=V2CarryConsumptionRules(),
    )
    packet_path = repository_root / "carry.json"
    packet_path.write_text(
        json.dumps(packet.model_dump(mode="json", by_alias=True), indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
    return packet_path, load_v2_carry_packet(packet_path, repository_root)


def test_v2_carry_requires_an_honest_successor_or_reanalysis_label() -> None:
    carry_ref = _ref("v2-carry-packet")
    with pytest.raises(ValidationError, match="adaptive_successor or reanalysis"):
        _plan(v2_carry_evidence_ref=carry_ref)
    successor = _plan(
        claim_label=CampaignClaimLabel.ADAPTIVE_SUCCESSOR,
        v2_carry_evidence_ref=carry_ref,
    )
    assert successor.claim_label is CampaignClaimLabel.ADAPTIVE_SUCCESSOR
    assert successor.carry_application_order == "after_both_zero_state_genesis_seals"
    assert not successor.environment_access_allowed
    assert not successor.action_port_present


def test_prepare_creates_two_matched_physically_separate_zero_state_labs(
    tmp_path: Path,
) -> None:
    packet_path, carry = _carry_packet(tmp_path)
    del packet_path
    plan = _plan(
        claim_label=CampaignClaimLabel.REANALYSIS,
        v2_carry_evidence_ref=carry.file_sha256,
    )
    root = tmp_path / "campaign"
    index = prepare_campaign(root, plan, carry_packet=carry)
    verification = verify_campaign(root, plan, carry_packet=carry)

    assert index.plan_ref == plan.digest
    assert index.claim_label is CampaignClaimLabel.REANALYSIS
    assert verification.physically_separate_roots
    assert verification.physically_separate_ledgers
    assert verification.both_currently_zero_state
    assert verification.matched_seed
    assert verification.matched_resource_budget
    assert verification.matched_operator_identity
    assert (root / CAMPAIGN_INDEX_PATH).read_bytes() == canonical_bytes(index)

    no_scribe_manifest, no_scribe = _load_arm(root, "no_scribe")
    scribe_manifest, scribe = _load_arm(root, "scribe")
    assert no_scribe.seed == scribe.seed == plan.seed
    assert no_scribe.resource_budget == scribe.resource_budget == plan.resource_budget
    assert (
        no_scribe.model_driver_id,
        no_scribe.model_driver_version,
        no_scribe.model_driver_artifact_ref,
    ) == (
        scribe.model_driver_id,
        scribe.model_driver_version,
        scribe.model_driver_artifact_ref,
    )
    assert no_scribe.declared_input_refs == scribe.declared_input_refs == ()
    assert index.carry_evidence_ref == plan.v2_carry_evidence_ref
    assert no_scribe.allowed_action_names == scribe.allowed_action_names == ()
    assert no_scribe.shadow_only and scribe.shadow_only
    assert no_scribe.execution_grant_ref is None and scribe.execution_grant_ref is None
    assert plan.scribe_driver.digest not in no_scribe_manifest.capability_refs
    assert plan.scribe_driver.digest in scribe_manifest.capability_refs
    assert (root / "arms" / "no_scribe" / no_scribe_manifest.layout.ledger_path).resolve() != (
        root / "arms" / "scribe" / scribe_manifest.layout.ledger_path
    ).resolve()

    index_payload = index.model_dump(mode="json", by_alias=True)
    assert index_payload["contains_observations"] is False
    assert index_payload["contains_domain_state"] is False
    assert index_payload["contains_action_sequences"] is False
    assert index_payload["contains_private_reasoning"] is False


def test_declared_carry_requires_exact_validated_packet_and_source_artifacts(
    tmp_path: Path,
) -> None:
    packet_path, carry = _carry_packet(tmp_path)
    plan = _plan(
        claim_label=CampaignClaimLabel.ADAPTIVE_SUCCESSOR,
        v2_carry_evidence_ref=carry.file_sha256,
    )
    with pytest.raises(Calibration003Error, match="requires its validated packet"):
        prepare_campaign(tmp_path / "missing-carry", plan)
    with pytest.raises(Calibration003Error, match="must not receive"):
        prepare_campaign(tmp_path / "unexpected-carry", _plan(), carry_packet=carry)

    (tmp_path / "evidence" / "v2-result.json").write_bytes(b"changed")
    with pytest.raises(Calibration003Error, match="source artifact digest changed"):
        load_v2_carry_packet(packet_path, tmp_path, expected_ref=carry.file_sha256)


def test_carry_loader_rejects_changed_packet_and_repository_escape(tmp_path: Path) -> None:
    packet_path, _carry = _carry_packet(tmp_path)
    with pytest.raises(Calibration003Error, match="declared digest"):
        load_v2_carry_packet(packet_path, tmp_path, expected_ref=_ref("wrong-packet"))
    outside = tmp_path.parent / "outside-carry.json"
    outside.write_bytes(packet_path.read_bytes())
    with pytest.raises(Calibration003Error, match="escapes the repository root"):
        load_v2_carry_packet(outside, tmp_path)


def test_repository_sources_reject_alternate_streams_and_hardlinks(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    with pytest.raises(Calibration003Error, match="alternate data stream"):
        _repository_file(
            repository,
            repository / "source.json:alternate",
            label="test source",
        )
    with pytest.raises(ValidationError, match="canonical relative POSIX path"):
        V2CarrySourceArtifact(path="evidence/source.json:alternate", sha256=_ref("source"))

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    hardlink = repository / "source.json"
    try:
        os.link(outside, hardlink)
    except OSError:
        pytest.skip("hard links are unavailable on this host")
    with pytest.raises(Calibration003Error, match="hard-linked file"):
        _repository_file(repository, hardlink, label="test source")


def test_carry_packet_contract_rejects_boundary_and_consumption_drift(tmp_path: Path) -> None:
    packet_path, _carry = _carry_packet(tmp_path)
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    payload["classification"] = "fresh evidence"
    packet_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Calibration003Error, match="classification"):
        load_v2_carry_packet(packet_path, tmp_path)

    packet_path, _carry = _carry_packet(tmp_path / "second")
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    payload["consumption_rules"]["fresh_generalization_arm_may_consume"] = True
    packet_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Calibration003Error, match="fresh_generalization_arm_may_consume"):
        load_v2_carry_packet(packet_path, tmp_path / "second")


def test_carry_loader_rejects_unanchored_fact_evidence(tmp_path: Path) -> None:
    packet_path, _carry = _carry_packet(tmp_path)
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    payload["facts"][0]["evidence_refs"] = [_ref("forged-unanchored-evidence")]
    packet_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Calibration003Error, match="evidence is absent from pinned sources"):
        load_v2_carry_packet(packet_path, tmp_path)


def test_carry_loader_rejects_digest_noise_outside_typed_evidence_fields(
    tmp_path: Path,
) -> None:
    packet_path, _carry = _carry_packet(tmp_path)
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    forged = _ref("digest-shaped-unrelated-noise")
    artifact_path = tmp_path / payload["source_artifacts"][0]["path"]
    source = json.loads(artifact_path.read_text(encoding="utf-8"))
    source["unrelated_noise"] = forged
    artifact = canonical_bytes(source)
    artifact_path.write_bytes(artifact)
    payload["source_artifacts"][0]["sha256"] = sha256_bytes(artifact)
    payload["facts"][0]["evidence_refs"] = [forged]
    packet_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Calibration003Error, match="evidence is absent from pinned sources"):
        load_v2_carry_packet(packet_path, tmp_path)


def test_checked_in_v2_carry_packet_resolves_all_pinned_sources() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    packet_path = repository_root / "docs" / "calibrations" / "003-v2-carry-packet.json"
    loaded = load_v2_carry_packet(
        packet_path,
        repository_root,
        expected_ref=sha256_bytes(packet_path.read_bytes()),
    )
    assert len(loaded.packet.facts) == 13
    assert len(loaded.packet.source_artifacts) == 6


def test_cli_carry_binding_is_required_iff_plan_declares_it(tmp_path: Path) -> None:
    packet_path, carry = _carry_packet(tmp_path)
    successor = _plan(
        claim_label=CampaignClaimLabel.ADAPTIVE_SUCCESSOR,
        v2_carry_evidence_ref=carry.file_sha256,
    )
    with pytest.raises(ValueError, match="--carry-packet is required"):
        _load_declared_carry(successor, None, tmp_path)
    assert _load_declared_carry(successor, packet_path, tmp_path) == carry
    with pytest.raises(ValueError, match="does not declare"):
        _load_declared_carry(_plan(), packet_path, tmp_path)


def test_operator_and_scribe_must_bind_distinct_identities_and_artifacts() -> None:
    with pytest.raises(ValidationError, match="distinct role identities"):
        _plan(
            scribe_driver=ScribeDriverBinding(
                driver_id="codex-operated-test",
                driver_version="fixture-1",
                driver_artifact_ref=_ref("other-scribe"),
            )
        )
    with pytest.raises(ValidationError, match="distinct artifacts"):
        _plan(
            scribe_driver=ScribeDriverBinding(
                driver_id="different-scribe",
                driver_version="fixture-1",
                driver_artifact_ref=_ref("operator"),
            )
        )
    plan = _plan()
    with pytest.raises(ValidationError, match="canonical NFKC text without padding"):
        plan.operator.model_copy(update={"operator_id": f"{plan.operator.operator_id} "})
    with pytest.raises(ValidationError, match="canonical NFKC text without padding"):
        ScribeDriverBinding(
            driver_id=f"{plan.operator.operator_id} ",
            driver_version="fixture-1",
            driver_artifact_ref=_ref("padded-scribe"),
        )


def test_prepare_refuses_owner_data_and_verify_detects_departure_from_genesis(
    tmp_path: Path,
) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    owner_file = occupied / "keep.txt"
    owner_file.write_text("owner data", encoding="utf-8")
    with pytest.raises(Calibration003Error, match="without its matching preparation marker"):
        prepare_campaign(occupied, _plan())
    assert owner_file.read_text(encoding="utf-8") == "owner data"

    root = tmp_path / "campaign"
    plan = _plan()
    prepare_campaign(root, plan)
    manifest, _spec = _load_arm(root, "no_scribe")
    with SQLiteLedger(root / "arms" / "no_scribe" / manifest.layout.ledger_path) as ledger:
        ledger.put_object({"synthetic": "later state"})
    with pytest.raises(LabError, match="zero-state"):
        verify_campaign(root, plan)


def test_prepare_resumes_from_one_complete_genesis_arm(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    plan = _plan()
    first = prepare_campaign(root, plan)
    (root / CAMPAIGN_INDEX_PATH).unlink()
    shutil.rmtree(root / "arms" / "scribe")
    resumed = prepare_campaign(root, plan)
    assert resumed == first
    assert (root / CAMPAIGN_PREPARATION_MARKER_PATH).is_file()
    assert verify_campaign(root, plan).both_currently_zero_state


def test_plan_loader_requires_exact_canonical_json(tmp_path: Path) -> None:
    plan = _plan()
    exact = tmp_path / "plan.json"
    exact.write_bytes(canonical_bytes(plan))
    assert load_plan(exact) == plan
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(canonical_bytes(plan) + b"\n")
    with pytest.raises(Calibration003Error, match="exact canonical"):
        load_plan(noncanonical)


def test_verification_and_preflight_receipts_reject_wrong_schema_ids() -> None:
    refs = (_ref("first"), _ref("second"))
    with pytest.raises(ValidationError, match="campaign verification schema"):
        CampaignVerification.model_validate(
            {
                "schema": "wrong.schema",
                "campaign_id": "calibration-003-test",
                "plan_ref": _ref("plan"),
                "index_ref": _ref("index"),
                "arm_genesis_refs": refs,
                "arm_ledger_seal_refs": refs,
                "arm_domain_seal_refs": refs,
            }
        )
    with pytest.raises(ValidationError, match="synthetic preflight receipt schema"):
        SyntheticPreflightReceipt.model_validate(
            {
                "schema": "wrong.schema",
                "preflight_id": "calibration-003-test-preflight",
                "driver_binding_ref": _ref("driver"),
                "scribe_policy_ref": _ref("policy"),
                "boundary_adapter_ref": _ref("adapter"),
                "state_projection_ref": _ref("projection"),
                "promoted_cycle_ref": _ref("promotion"),
                "residual_cycle_ref": _ref("residual"),
                "failure_cycle_ref": _ref("failure"),
                "ledger_receipt_count": 1,
                "ledger_receipt_head": _ref("head"),
            }
        )


def test_synthetic_preflight_proves_the_bounded_scribe_surface(tmp_path: Path) -> None:
    root = tmp_path / "preflight"
    receipt = run_synthetic_preflight(root)
    assert receipt.result_class == "synthetic_preflight_only"
    assert receipt.request_bound_to_driver
    assert receipt.heldout_payloads_absent_from_request_view
    assert receipt.heldout_refs_disjoint_from_adaptation
    assert receipt.duplicate_payload_refs_kept_in_one_arm
    assert receipt.exact_round_trip
    assert receipt.promotion_policy_applied
    assert receipt.restart_deterministic
    assert receipt.repeated_cycle_idempotent
    assert receipt.residual_fallback_receipted
    assert receipt.driver_failure_fallback_receipted
    assert receipt.pending_material_preserved_after_failure
    assert receipt.no_environment_access
    assert receipt.no_credentials
    assert receipt.no_action_port
    assert receipt.ledger_receipt_count > 0
    assert (root / SYNTHETIC_PREFLIGHT_RECEIPT_PATH).read_bytes() == canonical_bytes(receipt)


def test_public_surface_has_only_preparation_commands_and_no_external_imports() -> None:
    schema = calibration_003_schema_bundle()
    assert schema["commands"] == ("prepare", "schema", "synthetic-preflight", "verify")
    parser = _parser()
    for command in schema["commands"]:
        assert (
            parser.parse_args(
                [
                    command,
                    *(
                        {
                            "prepare": ["plan.json", "root"],
                            "verify": ["plan.json", "root"],
                            "synthetic-preflight": ["root"],
                            "schema": [],
                        }[command]
                    ),
                ]
            ).command
            == command
        )

    package_root = Path(__file__).resolve().parents[1] / "calibration_003"
    imported_roots: set[str] = set()
    source_text = ""
    for path in package_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        source_text += source.casefold()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    assert "arc_agi" not in source_text
    assert imported_roots.isdisjoint({"httpx", "requests", "socket", "urllib"})
