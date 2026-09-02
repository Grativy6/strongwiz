from __future__ import annotations

import pytest

from strongwiz.curriculum import (
    AdaptiveCurriculumController,
    CurriculumError,
    CurriculumMode,
    CurriculumStageHandoff,
    LearnedStackTransfer,
    NextStageDecision,
    four_stage_curriculum,
)
from strongwiz.lab import RunDisposition
from tests.support import ref


def plan():  # type: ignore[no-untyped-def]
    return four_stage_curriculum(
        campaign_id="calibration-002",
        objective="reach the declared environment terminal state",
        success_condition_ref=ref("official-success-condition"),
        final_authority_source="domain adapter over official environment state",
        final_wall_minutes=120,
    )


def partial_handoff(
    controller: AdaptiveCurriculumController,
    *,
    next_decision: NextStageDecision = NextStageDecision.ADVANCE,
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
        retained_mechanic_refs=(ref(f"mechanics-{start.occurrence}"),),
        next_decision=next_decision,
        concise_result="bounded stage ended without terminal success",
    )


def transfer_for(
    controller: AdaptiveCurriculumController,
    source: CurriculumStageHandoff,
) -> LearnedStackTransfer:
    next_stage = controller.plan.stages[len(controller.checkpoint().completed_handoffs)]
    return LearnedStackTransfer(
        transfer_id=f"transfer-to-{next_stage.stage_id}",
        source_stage_handoff_ref=source.digest,
        source_run_seal_ref=source.run_seal_ref,
        target_stage_ref=next_stage.digest,
        shorthand_transfer_ref=ref(f"shorthand-{next_stage.ordinal}"),
        shorthand_adoption_ref=ref(f"shorthand-adoption-{next_stage.ordinal}"),
        mechanic_refs=source.retained_mechanic_refs,
        validation_refs=(ref(f"validation-{next_stage.ordinal}"),),
    )


def test_default_curriculum_is_bounded_30_60_90_then_final() -> None:
    curriculum = plan()

    assert tuple(stage.mode for stage in curriculum.stages) == (
        CurriculumMode.BASELINE,
        CurriculumMode.ACQUIRE,
        CurriculumMode.DEEPEN,
        CurriculumMode.FINISH_OR_REASSESS,
    )
    assert tuple(stage.resource_budget.wall_clock_ms for stage in curriculum.stages) == (
        30 * 60 * 1000,
        60 * 60 * 1000,
        90 * 60 * 1000,
        120 * 60 * 1000,
    )
    assert not curriculum.stages[0].may_inherit_shorthand
    assert all(stage.may_inherit_shorthand for stage in curriculum.stages[1:])


def test_stages_run_one_at_a_time_with_explicit_successor_transfer() -> None:
    controller = AdaptiveCurriculumController(plan())
    baseline = controller.start_next(frozen_stack_ref=ref("baseline-stack"))
    assert baseline.predecessor_handoff_ref is None
    with pytest.raises(CurriculumError, match="one curriculum stage"):
        controller.start_next(frozen_stack_ref=ref("duplicate-start"))

    first_handoff = controller.finish_active(partial_handoff(controller))
    transfer = transfer_for(controller, first_handoff)
    successor = controller.start_next(
        frozen_stack_ref=ref("successor-stack"), transfer=transfer
    )
    assert successor.predecessor_handoff_ref == first_handoff.digest
    assert successor.learned_stack_transfer_ref == transfer.digest

    checkpoint = controller.checkpoint()
    restored = AdaptiveCurriculumController.restore(checkpoint)
    assert restored.checkpoint() == checkpoint


def test_successor_refuses_implicit_or_crossed_inheritance() -> None:
    controller = AdaptiveCurriculumController(plan())
    controller.start_next(frozen_stack_ref=ref("baseline-stack"))
    first = controller.finish_active(partial_handoff(controller))

    with pytest.raises(CurriculumError, match="explicit learned-stack"):
        controller.start_next(frozen_stack_ref=ref("missing-transfer"))

    transfer = transfer_for(controller, first).model_copy(
        update={"source_run_seal_ref": ref("wrong-run")}
    )
    with pytest.raises(CurriculumError, match="crosses stage lineage"):
        controller.start_next(frozen_stack_ref=ref("crossed-transfer"), transfer=transfer)


def test_final_stage_requires_finish_on_observed_success_or_reassessment() -> None:
    controller = AdaptiveCurriculumController(plan())
    for index in range(3):
        controller.start_next(
            frozen_stack_ref=ref(f"stack-{index}"),
            transfer=(
                None
                if index == 0
                else transfer_for(controller, controller.checkpoint().completed_handoffs[-1])
            ),
        )
        controller.finish_active(partial_handoff(controller))

    controller.start_next(
        frozen_stack_ref=ref("final-stack"),
        transfer=transfer_for(controller, controller.checkpoint().completed_handoffs[-1]),
    )
    with pytest.raises(CurriculumError, match="finish on success or return"):
        controller.finish_active(partial_handoff(controller))

    final = partial_handoff(controller, next_decision=NextStageDecision.REASSESS)
    controller.finish_active(final)
    with pytest.raises(CurriculumError, match="no remaining stage"):
        controller.start_next(frozen_stack_ref=ref("too-late"))


def test_reassessment_stops_later_stages() -> None:
    controller = AdaptiveCurriculumController(plan())
    controller.start_next(frozen_stack_ref=ref("baseline-stack"))
    controller.finish_active(
        partial_handoff(controller, next_decision=NextStageDecision.REASSESS)
    )
    with pytest.raises(CurriculumError, match="stopped for finish or reassessment"):
        controller.start_next(frozen_stack_ref=ref("unearned-successor"))
