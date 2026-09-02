from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from strongwiz.curriculum import (
    AdaptiveCurriculumController,
    AdaptiveCurriculumPlan,
    CurriculumCheckpoint,
    CurriculumError,
    CurriculumStageHandoff,
    LearnedStackTransfer,
    NextStageDecision,
    four_stage_curriculum,
)
from strongwiz.heartbeat import (
    BudgetBand,
    EventDrivenHeartbeat,
    HeartbeatBoundaryWitness,
    HeartbeatEmission,
    HeartbeatError,
    HeartbeatLivenessEvidence,
    HeartbeatReason,
    HeartbeatState,
    RiskBand,
    SteeringAperture,
)
from strongwiz.lab import RunDisposition
from strongwiz.shorthand import (
    AdoptionStatus,
    CodebookRegistry,
    KevinAdoptionDecision,
    KevinCodebookRevision,
    KevinDefinitionReview,
    KevinSpeakEntry,
    KevinSpeakError,
    KevinSymbolProposal,
    ReviewDisposition,
    ShorthandLane,
    decode_shorthand_text,
    encode_shorthand_text,
)
from tests.support import ref


def _proposal(*, token: str = "motif") -> KevinSymbolProposal:
    return KevinSymbolProposal(
        token=token,
        expansion="observe-access-plan-observe-access-plan",
        concise_meaning="repeated evidence-to-plan motif",
        source_payload_refs=(ref(f"source-{token}"),),
    )


def _candidate_revision() -> tuple[KevinCodebookRevision, KevinCodebookRevision]:
    registry = CodebookRegistry()
    genesis = KevinCodebookRevision.blank(codebook_id="validation-book")
    registry.register(genesis)
    candidate = registry.build_revision(
        predecessor_ref=genesis.digest,
        proposals=(_proposal(),),
        rationale="add a measured recurring motif",
    )
    return genesis, candidate


def _assert_registry_rejects(
    genesis: KevinCodebookRevision,
    candidate: KevinCodebookRevision,
    message: str,
) -> None:
    registry = CodebookRegistry()
    registry.register(genesis)
    with pytest.raises(KevinSpeakError, match=message):
        registry.register(candidate)


def test_codebook_registry_rejects_crossed_or_tampered_successors() -> None:
    genesis, candidate = _candidate_revision()

    with pytest.raises(KevinSpeakError, match="predecessor is not registered"):
        CodebookRegistry().register(candidate)

    identity_mutations: tuple[Mapping[str, object], ...] = (
        {"codebook_id": "other-lineage"},
        {"version": 2},
        {"decoder_id": "other.decoder"},
        {"decoder_version": "2"},
        {"decoder_artifact_ref": ref("other-decoder")},
    )
    for update in identity_mutations:
        crossed = candidate.model_copy(update=update)
        _assert_registry_rejects(genesis, crossed, "lineage or decoder identity")

    unknown_retirement = KevinCodebookRevision(
        codebook_id=genesis.codebook_id,
        version=1,
        predecessor_ref=genesis.digest,
        retired_tokens=("ghost",),
        rationale="attempt to retire a symbol absent from the predecessor",
    )
    _assert_registry_rejects(genesis, unknown_retirement, "retire an unknown token")

    definition = candidate.definitions[0]
    corruptions = (
        (
            definition.model_copy(update={"supersedes_definition_ref": ref("wrong-parent")}),
            "exact predecessor",
        ),
        (
            definition.model_copy(update={"expansion_ref": ref("wrong-expansion")}),
            "disagrees with its digest",
        ),
        (
            definition.model_copy(
                update={"expansion_size_bytes": definition.expansion_size_bytes + 1}
            ),
            "disagrees with its size",
        ),
    )
    for corrupted_definition, message in corruptions:
        corrupted = candidate.model_copy(update={"definitions": (corrupted_definition,)})
        _assert_registry_rejects(genesis, corrupted, message)


def test_codebook_retirement_preserves_history_and_registry_fails_closed() -> None:
    registry = CodebookRegistry()
    genesis = KevinCodebookRevision.blank(codebook_id="retirement-book")
    registry.register(genesis)
    active = registry.build_revision(
        predecessor_ref=genesis.digest,
        proposals=(_proposal(),),
        rationale="introduce one validated symbol",
    )
    definition_ref = active.definitions[0].digest

    assert registry.register(active) == active.digest
    retired = registry.build_revision(
        predecessor_ref=active.digest,
        proposals=(),
        retired_tokens=("motif",),
        rationale="retire the symbol without erasing its definition history",
    )

    assert registry.resolved(retired.digest) == {}
    assert registry.effective_definition_refs(retired.digest) == ()
    assert definition_ref in registry.lineage_definition_refs(retired.digest)
    assert registry.definition(definition_ref).token == "motif"

    with pytest.raises(KevinSpeakError, match="unknown codebook revision"):
        registry.require(ref("unknown-codebook"))
    with pytest.raises(KevinSpeakError, match="unknown symbol definition"):
        registry.definition(ref("unknown-definition"))
    with pytest.raises(KevinSpeakError, match="lineage already has a genesis"):
        registry.register(
            KevinCodebookRevision(
                codebook_id=genesis.codebook_id,
                version=0,
                predecessor_ref=None,
                rationale="a conflicting second genesis",
            )
        )
    with pytest.raises(KevinSpeakError, match="concise rationale"):
        registry.build_revision(
            predecessor_ref=retired.digest,
            proposals=(_proposal(token="next"),),
            rationale=" ",
        )
    repeated = _proposal(token="repeat")
    with pytest.raises(KevinSpeakError, match="repeats a token"):
        registry.build_revision(
            predecessor_ref=retired.digest,
            proposals=(repeated, repeated),
            rationale="duplicates must not create ambiguous successor state",
        )


def test_shorthand_wire_language_rejects_ambiguous_or_executable_looking_tokens() -> None:
    for encoded in ("~bad token~", "~__import__('os')~"):
        with pytest.raises(KevinSpeakError, match="invalid symbol token"):
            decode_shorthand_text(encoded, {})

    for translations in ({"bad token": "content"}, {"valid": ""}):
        with pytest.raises(KevinSpeakError, match="valid tokens and nonempty expansions"):
            encode_shorthand_text("content", translations)


def _approved_adoption() -> KevinAdoptionDecision:
    candidate_ref = ref("candidate-codebook")
    approved_definition = KevinDefinitionReview(
        definition_ref=ref("definition-a"),
        disposition=ReviewDisposition.APPROVE,
        rationale="explicitly selected for this successor only",
    )
    return KevinAdoptionDecision(
        adoption_id="adopt-for-stage-two",
        recommendation_ref=ref("recommendation"),
        review_ref=None,
        target_stage_ref=ref("stage-two"),
        candidate_codebook_ref=candidate_ref,
        approved_codebook_ref=candidate_ref,
        definition_decisions=(approved_definition,),
        evaluation_refs=(ref("eligible-evaluation"),),
        control_source_ref=ref("external-control"),
        target_configuration_ref=ref("target-configuration"),
        status=AdoptionStatus.APPROVED,
        rationale="control explicitly adopts the evaluated representation",
    )


def test_adoption_contract_cannot_self_expand_scope_or_authority() -> None:
    adoption = _approved_adoption()
    recommended_only = adoption.definition_decisions[0].model_copy(
        update={"disposition": ReviewDisposition.RECOMMEND}
    )
    mutations = (
        ({"approved_codebook_ref": ref("different-codebook")}, "exact candidate"),
        ({"definition_decisions": (recommended_only,)}, "approve active definitions"),
        ({"status": AdoptionStatus.REJECTED}, "rejected adoption cannot approve"),
        ({"scope": "general_authority"}, "working-representation scope"),
        ({"transfers_authority": True}, "cannot transfer authority"),
    )
    for update, message in mutations:
        with pytest.raises(ValidationError, match=message):
            adoption.model_copy(update=update)


def test_entry_contract_rejects_false_compaction_and_residual_claims() -> None:
    compact = KevinSpeakEntry(
        workspace_id="workspace",
        entry_id="compact-entry",
        source_payload_ref=ref("source-payload"),
        codebook_ref=ref("codebook"),
        codebook_version=1,
        decoder_artifact_ref=ref("decoder"),
        lane=ShorthandLane.COMPACT,
        encoded_text="~motif~",
        source_size_bytes=100,
        representation_size_bytes=7,
        symbol_uses=1,
    )
    compact_mutations = (
        ({"exact_round_trip": False}, "exact round-trip"),
        ({"encoded_text": None}, "require encoded text"),
        ({"residual_reason": "not actually compact"}, "no residual reason"),
        ({"representation_size_bytes": 100}, "strictly smaller"),
        ({"symbol_uses": 0}, "at least one shorthand symbol"),
    )
    for update, message in compact_mutations:
        with pytest.raises(ValidationError, match=message):
            compact.model_copy(update=update)

    residual = KevinSpeakEntry(
        workspace_id="workspace",
        entry_id="residual-entry",
        source_payload_ref=ref("residual-source"),
        codebook_ref=ref("codebook"),
        codebook_version=1,
        decoder_artifact_ref=ref("decoder"),
        lane=ShorthandLane.RESIDUAL,
        source_size_bytes=100,
        representation_size_bytes=100,
        residual_reason="no shorter exact representation was available",
    )
    residual_mutations = (
        ({"encoded_text": "raw"}, "explicit uncompressed reason"),
        ({"residual_reason": " "}, "explicit uncompressed reason"),
        ({"representation_size_bytes": 99}, "canonical source size"),
        ({"symbol_uses": 1}, "cannot claim shorthand symbol use"),
    )
    for update, message in residual_mutations:
        with pytest.raises(ValidationError, match=message):
            residual.model_copy(update=update)


def _curriculum_plan() -> AdaptiveCurriculumPlan:
    return four_stage_curriculum(
        campaign_id="validation-campaign",
        objective="reach the declared terminal state",
        success_condition_ref=ref("success-condition"),
        final_authority_source="domain adapter over official environment state",
        final_wall_minutes=120,
    )


def _partial_handoff(
    controller: AdaptiveCurriculumController,
    *,
    decision: NextStageDecision = NextStageDecision.ADVANCE,
) -> CurriculumStageHandoff:
    start = controller.active_start
    assert start is not None
    return CurriculumStageHandoff(
        stage_start_ref=start.digest,
        stage_ref=start.stage_ref,
        run_seal_ref=ref(f"run-seal-{start.occurrence}"),
        disposition=RunDisposition.PARTIAL,
        completion_genuinely_observed=False,
        terminal_state="BOUND_REACHED",
        progress_evidence_refs=(ref(f"progress-{start.occurrence}"),),
        active_codebook_ref=ref(f"codebook-{start.occurrence}"),
        retained_mechanic_refs=(ref(f"mechanic-{start.occurrence}"),),
        next_decision=decision,
        concise_result="bounded work ended without terminal success",
    )


def _successor_transfer(
    controller: AdaptiveCurriculumController,
    handoff: CurriculumStageHandoff,
    *,
    shorthand: bool,
    mechanics: bool,
) -> LearnedStackTransfer:
    stage = controller.plan.stages[len(controller.checkpoint().completed_handoffs)]
    return LearnedStackTransfer(
        transfer_id=f"transfer-to-{stage.stage_id}",
        source_stage_handoff_ref=handoff.digest,
        source_run_seal_ref=handoff.run_seal_ref,
        target_stage_ref=stage.digest,
        shorthand_transfer_ref=ref("shorthand-transfer") if shorthand else None,
        shorthand_adoption_ref=ref("shorthand-adoption") if shorthand else None,
        mechanic_refs=(ref("retained-mechanic"),) if mechanics else (),
        validation_refs=(ref("transfer-validation"),),
    )


def test_successor_rejects_each_kind_of_inheritance_its_stage_did_not_allow() -> None:
    for shorthand, mechanics, message in (
        (True, False, "does not permit shorthand"),
        (False, True, "does not permit mechanic"),
    ):
        base = _curriculum_plan()
        restricted_successor = base.stages[1].model_copy(
            update={
                "may_inherit_shorthand": not shorthand,
                "may_inherit_mechanics": not mechanics,
            }
        )
        plan = base.model_copy(
            update={"stages": (base.stages[0], restricted_successor, *base.stages[2:])}
        )
        controller = AdaptiveCurriculumController(plan)
        controller.start_next(frozen_stack_ref=ref("baseline-stack"))
        handoff = controller.finish_active(_partial_handoff(controller))
        transfer = _successor_transfer(
            controller, handoff, shorthand=shorthand, mechanics=mechanics
        )

        with pytest.raises(CurriculumError, match=message):
            controller.start_next(frozen_stack_ref=ref("successor-stack"), transfer=transfer)


def test_transfer_contract_rejects_partial_empty_or_authority_bearing_payloads() -> None:
    transfer = LearnedStackTransfer(
        transfer_id="bounded-transfer",
        source_stage_handoff_ref=ref("source-handoff"),
        source_run_seal_ref=ref("source-seal"),
        target_stage_ref=ref("target-stage"),
        shorthand_transfer_ref=ref("shorthand-transfer"),
        shorthand_adoption_ref=ref("shorthand-adoption"),
        mechanic_refs=(ref("mechanic"),),
        validation_refs=(ref("validation"),),
    )
    mutations = (
        ({"shorthand_adoption_ref": None}, "both transfer and adoption"),
        ({"validation_refs": ()}, "requires validation evidence"),
        (
            {
                "shorthand_transfer_ref": None,
                "shorthand_adoption_ref": None,
                "mechanic_refs": (),
            },
            "cannot be empty",
        ),
        ({"excluded_material": ()}, "cannot carry excluded state"),
        ({"transfers_authority": True}, "cannot carry excluded state"),
        (
            {
                "mechanic_refs": tuple(
                    sorted((ref("mechanic-one"), ref("mechanic-two")), reverse=True)
                )
            },
            "sorted and unique",
        ),
    )
    for update, message in mutations:
        with pytest.raises(ValidationError, match=message):
            transfer.model_copy(update=update)


def test_checkpoint_and_controller_reject_inconsistent_persistent_state() -> None:
    plan = _curriculum_plan()
    controller = AdaptiveCurriculumController(plan)
    with pytest.raises(CurriculumError, match="no curriculum stage is active"):
        controller.finish_active(
            CurriculumStageHandoff(
                stage_start_ref=ref("never-started"),
                stage_ref=plan.stages[0].digest,
                run_seal_ref=ref("unused-seal"),
                disposition=RunDisposition.PARTIAL,
                completion_genuinely_observed=False,
                terminal_state="NOT_STARTED",
                progress_evidence_refs=(ref("unused-evidence"),),
                next_decision=NextStageDecision.ADVANCE,
                concise_result="syntactically valid but no active occurrence exists",
            )
        )

    active = controller.start_next(frozen_stack_ref=ref("baseline-stack"))
    wrong_active = active.model_copy(update={"stage_ref": plan.stages[1].digest})
    with pytest.raises(ValidationError, match="active curriculum stage disagrees"):
        CurriculumCheckpoint(plan=plan, active_start=wrong_active)

    crossed_handoff = _partial_handoff(controller).model_copy(
        update={"stage_ref": plan.stages[1].digest}
    )
    with pytest.raises(CurriculumError, match="does not close the active occurrence"):
        controller.finish_active(crossed_handoff)

    stopped = _partial_handoff(controller, decision=NextStageDecision.REASSESS)
    later = stopped.model_copy(
        update={
            "stage_start_ref": ref("later-start"),
            "stage_ref": plan.stages[1].digest,
            "run_seal_ref": ref("later-seal"),
            "progress_evidence_refs": (ref("later-progress"),),
            "next_decision": NextStageDecision.ADVANCE,
        }
    )
    with pytest.raises(ValidationError, match="continues after a stopping decision"):
        CurriculumCheckpoint(plan=plan, completed_handoffs=(stopped, later))


def _heartbeat_state(
    *,
    run_id: str = "run-1",
    phase: str = "learning",
    checkpoint_ref: str | None = None,
    gate: str = "map-access",
    budget: BudgetBand = BudgetBand.HEALTHY,
    aperture: SteeringAperture = SteeringAperture.SAFE,
    risk: RiskBand = RiskBand.LOW,
    residual_refs: tuple[str, ...] = (),
    terminal_state: str | None = None,
) -> HeartbeatState:
    return HeartbeatState(
        run_id=run_id,
        phase=phase,
        latest_checkpoint_ref=checkpoint_ref,
        active_gate=gate,
        budget_band=budget,
        budget_snapshot_ref=ref(f"budget-{phase}"),
        steering_aperture=aperture,
        risk_band=risk,
        residual_refs=residual_refs,
        terminal_state=terminal_state,
    )


def test_heartbeat_emits_every_material_boundary_reason_exactly_once() -> None:
    heartbeat = EventDrivenHeartbeat()
    initial = heartbeat.observe(_heartbeat_state())
    assert initial is not None

    changed = heartbeat.observe(
        _heartbeat_state(
            phase="executing",
            checkpoint_ref=ref("checkpoint"),
            gate="terminal-route",
            budget=BudgetBand.CRITICAL,
            aperture=SteeringAperture.LIMITED,
            risk=RiskBand.HIGH,
            residual_refs=(ref("residual"),),
            terminal_state="GAME_OVER",
        )
    )
    assert changed is not None
    assert set(changed.reasons) == {
        HeartbeatReason.PHASE_CHANGED,
        HeartbeatReason.CHECKPOINT_CHANGED,
        HeartbeatReason.ACTIVE_GATE_CHANGED,
        HeartbeatReason.BUDGET_BAND_CHANGED,
        HeartbeatReason.STEERING_APERTURE_CHANGED,
        HeartbeatReason.RISK_BAND_CHANGED,
        HeartbeatReason.RESIDUAL_SET_CHANGED,
        HeartbeatReason.TERMINAL_STATE_CHANGED,
    }


def test_heartbeat_rejects_cross_run_or_unbound_successor_evidence() -> None:
    heartbeat = EventDrivenHeartbeat()
    liveness = HeartbeatLivenessEvidence(
        run_id="run-1",
        progress_ordinal=1,
        evidence_ref=ref("liveness"),
        concise_observation="one durable chunk completed",
    )
    with pytest.raises(HeartbeatError, match="cannot precede heartbeat genesis"):
        heartbeat.show_liveness(liveness)
    with pytest.raises(HeartbeatError, match="undisplayed heartbeat state"):
        heartbeat.record_steering_change(
            supplied_authority_ref=ref("authority"),
            instruction_ref=ref("instruction"),
            prior_policy_ref=ref("prior"),
            resulting_policy_ref=ref("result"),
            reversible=True,
            concise_effect="change the next bounded probe",
        )

    initial = heartbeat.observe(_heartbeat_state())
    assert initial is not None and initial.durable_boundary is not None
    with pytest.raises(HeartbeatError, match="another run"):
        heartbeat.show_liveness(liveness.model_copy(update={"run_id": "run-2"}))
    with pytest.raises(HeartbeatError, match="cannot cross run identity"):
        heartbeat.observe(_heartbeat_state(run_id="run-2"))

    genesis = initial.durable_boundary
    with pytest.raises(ValidationError, match="genesis must be predecessor-free"):
        genesis.model_copy(update={"predecessor_ref": ref("invented-predecessor")})

    successor = heartbeat.observe(_heartbeat_state(phase="planning"))
    assert successor is not None and successor.durable_boundary is not None
    boundary = successor.durable_boundary
    for update, message in (
        ({"predecessor_ref": None}, "require their exact predecessor"),
        ({"reasons": (HeartbeatReason.INITIAL,)}, "non-initial change reasons"),
        (
            {
                "reasons": (
                    HeartbeatReason.RISK_BAND_CHANGED,
                    HeartbeatReason.PHASE_CHANGED,
                )
            },
            "sorted and unique",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            boundary.model_copy(update=update)


def test_heartbeat_emission_cannot_detach_view_from_evidence_or_boundary() -> None:
    heartbeat = EventDrivenHeartbeat()
    initial = heartbeat.observe(_heartbeat_state())
    assert initial is not None
    with pytest.raises(ValidationError, match="does not bind its durable boundary"):
        initial.model_copy(update={"reasons": (HeartbeatReason.PHASE_CHANGED,)})

    liveness = HeartbeatLivenessEvidence(
        run_id="run-1",
        progress_ordinal=2,
        evidence_ref=ref("liveness-2"),
        concise_observation="another immutable chunk completed",
    )
    ephemeral = heartbeat.show_liveness(liveness)
    detached_view = ephemeral.view.model_copy(update={"liveness_evidence_ref": None})
    with pytest.raises(ValidationError, match="requires exact evidence"):
        ephemeral.model_copy(update={"view": detached_view})

    with pytest.raises(ValidationError, match="only fresh liveness"):
        HeartbeatEmission(
            view=ephemeral.view,
            durable_boundary=None,
            reasons=(HeartbeatReason.RISK_BAND_CHANGED,),
        )


def test_heartbeat_state_rejects_ambiguous_residual_or_terminal_state() -> None:
    base = _heartbeat_state()
    for update, message in (
        ({"phase": " "}, "run, phase, and active gate"),
        (
            {
                "residual_refs": tuple(
                    sorted((ref("residual-one"), ref("residual-two")), reverse=True)
                )
            },
            "sorted and unique",
        ),
        ({"residual_refs": ("not-a-digest",)}, "lowercase SHA-256"),
        ({"terminal_state": " "}, "terminal state cannot be blank"),
    ):
        with pytest.raises(ValidationError, match=message):
            base.model_copy(update=update)


def test_heartbeat_boundary_schema_is_validated_on_restore_input() -> None:
    heartbeat = EventDrivenHeartbeat()
    initial = heartbeat.observe(_heartbeat_state())
    assert initial is not None and initial.durable_boundary is not None
    boundary = initial.durable_boundary

    with pytest.raises(ValidationError, match="unsupported heartbeat boundary schema"):
        HeartbeatBoundaryWitness.model_validate(
            {**boundary.model_dump(by_alias=False), "schema_id": "unknown.boundary.v9"}
        )
