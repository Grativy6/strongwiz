from __future__ import annotations

import hashlib

import pytest

from calibration_002.learning import (
    CALIBRATION_002_EXCLUDED_MATERIAL,
    CALIBRATION_002_STAGE_MINUTES,
    Calibration002Inheritance,
    Calibration002LearningError,
    Calibration002LearningSidecar,
)
from strongwiz.curriculum import CurriculumMode, CurriculumStageHandoff, NextStageDecision
from strongwiz.lab import RunDisposition
from strongwiz.shorthand import (
    AdoptionStatus,
    EvaluationRole,
    KevinAdoptionDecision,
    KevinCodebookRevision,
    KevinEvaluationSample,
    KevinRecommendationBundle,
    KevinSpeakConfiguration,
    KevinSpeakTransfer,
    KevinSymbolProposal,
)


def ref(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _partial_handoff(
    sidecar: Calibration002LearningSidecar,
    *,
    run_seal_ref: str,
) -> CurriculumStageHandoff:
    binding = sidecar.active_binding
    assert binding is not None
    return CurriculumStageHandoff(
        stage_start_ref=binding.stage_start_ref,
        stage_ref=binding.frozen_stack.stage_ref,
        run_seal_ref=run_seal_ref,
        disposition=RunDisposition.PARTIAL,
        completion_genuinely_observed=False,
        terminal_state="BOUND_REACHED",
        progress_evidence_refs=(ref(f"progress-{binding.run_id}"),),
        active_codebook_ref=sidecar.table().codebook_ref,
        next_decision=NextStageDecision.ADVANCE,
        concise_result="bounded learning stage ended without terminal success",
    )


def test_campaign_persists_exact_plan_checkpoint_and_blank_stage(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ledger_path = tmp_path / "learning.sqlite"
    sidecar = Calibration002LearningSidecar.create(
        ledger_path,
        success_condition_ref=ref("declared-terminal-condition"),
    )

    assert tuple(
        stage.resource_budget.wall_clock_ms // 60_000 for stage in sidecar.plan.stages
    ) == CALIBRATION_002_STAGE_MINUTES
    assert tuple(stage.mode for stage in sidecar.plan.stages) == (
        CurriculumMode.BASELINE,
        CurriculumMode.ACQUIRE,
        CurriculumMode.DEEPEN,
        CurriculumMode.FINISH_OR_REASSESS,
    )
    assert sidecar.checkpoint.active_start is None

    binding = sidecar.open_stage(run_id="baseline")

    assert binding.workspace_mode == "blank"
    assert binding.frozen_stack.inheritance_ref is None
    assert sidecar.table().codebook_version == 0
    assert sidecar.table().translations == ()
    entry = sidecar.append(
        entry_id="observation-summary",
        payload={"summary": "bounded representation-only evidence"},
    )
    assert len(entry.source_payload_ref) == 64
    verification = sidecar.verify()
    assert verification.stage_binding_count == 1
    assert verification.completed_stage_count == 0
    assert verification.active_stage_binding_ref == binding.digest
    assert verification.source_payload_refs_run_local
    assert verification.excluded_material == CALIBRATION_002_EXCLUDED_MATERIAL
    assert verification.authority == "NONE"
    sidecar.close()

    restored = Calibration002LearningSidecar.restore(ledger_path)
    try:
        assert restored.checkpoint.active_start == sidecar.checkpoint.active_start
        assert restored.active_binding == binding
        assert restored.table().translations == ()
        assert restored.verify().checkpoint_ref == restored.checkpoint.digest
    finally:
        restored.close()


def test_append_adapt_recommend_table_and_run_local_source_gate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sidecar = Calibration002LearningSidecar.create(
        tmp_path / "adapt.sqlite",
        success_condition_ref=ref("declared-terminal-condition"),
    )
    sidecar.open_stage(run_id="baseline")
    try:
        for index, excluded in enumerate(
            (
                {"privateReasoning": "must remain outside"},
                {"domain_state": "must remain outside"},
                {"action-sequences": ["must remain outside"]},
                {"authority": "must remain outside"},
            )
        ):
            with pytest.raises(Calibration002LearningError, match="excluded field"):
                sidecar.append(
                    entry_id=f"excluded-{index}",
                    payload={"summary": excluded},
                )

        expansion = "observe-access-plan-" * 16
        source = sidecar.append(
            entry_id="recurring-pattern",
            payload={"summary": expansion, "status": "bounded evidence"},
        )
        foreign = KevinSymbolProposal(
            token="motif",
            expansion=expansion,
            concise_meaning="repeated evidence-to-plan motif",
            source_payload_refs=(ref("another-run-payload"),),
        )
        samples = (
            KevinEvaluationSample(
                case_id="adaptation",
                role=EvaluationRole.ADAPTATION,
                payload={"summary": expansion * 20},
            ),
            KevinEvaluationSample(
                case_id="validation",
                role=EvaluationRole.VALIDATION,
                payload={"validation": expansion * 20},
            ),
        )
        with pytest.raises(Calibration002LearningError, match="run-local"):
            sidecar.adapt(
                proposals=(foreign,),
                samples=samples,
                rationale="a foreign source must fail closed",
                evaluation_id="foreign-source",
            )

        local = foreign.model_copy(
            update={"source_payload_refs": (source.source_payload_ref,)}
        )
        adaptation = sidecar.adapt(
            proposals=(local,),
            samples=samples,
            rationale="evaluate a recurring run-local representation",
            evaluation_id="run-local-motif",
        )
        assert adaptation.promotion is not None
        recommendation = sidecar.recommend(
            recommendation_id="next-round",
            recommending_driver_ref=ref("controller-driver"),
            evaluation_refs=(adaptation.evaluation.digest,),
            rationale="carry only the evaluated representation for separate approval",
        )
        table = sidecar.table()

        assert recommendation.status == "recommended_not_approved"
        assert table.codebook_ref == adaptation.candidate.digest
        assert tuple(item.token for item in table.translations) == ("motif",)
        assert sidecar.verify().source_payload_refs_run_local
    finally:
        sidecar.close()


def test_successor_requires_exact_target_bound_inherited_workspace(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sidecar = Calibration002LearningSidecar.create(
        tmp_path / "successor.sqlite",
        success_condition_ref=ref("declared-terminal-condition"),
    )
    baseline = sidecar.open_stage(run_id="baseline")
    source_configuration = KevinSpeakConfiguration()
    source_codebook = KevinCodebookRevision.blank(codebook_id="calibration-002.kevin-speak")
    assert sidecar.table().codebook_ref == source_codebook.digest
    recommendation = sidecar.recommend(
        recommendation_id="blank-baseline",
        recommending_driver_ref=ref("controller-driver"),
        evaluation_refs=(),
        rationale="retain the blank representation unless separately approved otherwise",
    )
    handoff = _partial_handoff(sidecar, run_seal_ref=ref("baseline-run-seal"))
    sidecar.finish_stage(handoff)

    with pytest.raises(Calibration002LearningError, match="requires an exact"):
        sidecar.open_stage(run_id="acquire-without-transfer")

    target_stage_ref = sidecar.plan.stages[1].digest
    source_capsule_ref = ref("sealed-baseline-capsule")
    bundle = KevinRecommendationBundle(
        source_run_seal_ref=handoff.run_seal_ref,
        source_capsule_ref=source_capsule_ref,
        recommendation=recommendation,
        source_configuration=source_configuration,
        codebooks=(source_codebook,),
        evaluations=(),
    )
    adoption = KevinAdoptionDecision(
        adoption_id="adopt-blank-for-acquire",
        recommendation_ref=recommendation.digest,
        review_ref=None,
        target_stage_ref=target_stage_ref,
        candidate_codebook_ref=source_codebook.digest,
        approved_codebook_ref=source_codebook.digest,
        definition_decisions=(),
        evaluation_refs=(),
        control_source_ref=ref("external-successor-control"),
        target_configuration_ref=source_configuration.digest,
        status=AdoptionStatus.APPROVED,
        rationale="approve this exact representation for this successor only",
    )
    shorthand = KevinSpeakTransfer(
        transfer_id="baseline-to-acquire-shorthand",
        source_workspace_id=baseline.frozen_stack.workspace_id,
        source_run_seal_ref=handoff.run_seal_ref,
        source_capsule_ref=source_capsule_ref,
        recommendation_bundle=bundle,
        adoption=adoption,
        target_configuration=source_configuration,
        codebooks=(source_codebook,),
        active_codebook_ref=source_codebook.digest,
    )
    inheritance = Calibration002Inheritance.bind(
        transfer_id="baseline-to-acquire-learning",
        predecessor_handoff=handoff,
        target_stage_ref=target_stage_ref,
        shorthand_transfer=shorthand,
        validation_refs=(ref("successor-transfer-validation"),),
    )
    successor = sidecar.open_stage(run_id="acquire", inheritance=inheritance)
    try:
        assert successor.workspace_mode == "explicit_inheritance"
        assert successor.frozen_stack.inheritance_ref == inheritance.digest
        assert successor.shorthand_transfer_ref == shorthand.digest
        assert sidecar.table().codebook_ref == source_codebook.digest
        verification = sidecar.verify()
        assert verification.stage_binding_count == 2
        assert verification.completed_stage_count == 1
        assert verification.workspace_verifications[-1].entry_count == 0
    finally:
        sidecar.close()
