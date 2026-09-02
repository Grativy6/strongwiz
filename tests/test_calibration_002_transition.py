from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from calibration.models import BudgetReceipt, RunTerminalRecord
from calibration_002.learning import Calibration002LearningSidecar
from calibration_002.transition import (
    AdvisoryDisposition,
    Calibration002AdvisoryReviewRequest,
    Calibration002ExternalControlDecision,
    Calibration002RefinementRequest,
    Calibration002RetainedMechanicDraft,
    Calibration002TransitionError,
    TransitionDisposition,
    apply_external_control,
    close_source_stage,
    review_source_recommendation,
    verify_transition_result,
)
from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.lab import (
    ExternalDomainStateSeal,
    ExternalLedgerSeal,
    RunDisposition,
    RunSeal,
)
from strongwiz.shorthand import (
    AdoptionStatus,
    EvaluationRole,
    KevinEvaluationSample,
    KevinSymbolProposal,
)


def ref(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _terminal(run_id: str, *, genesis_ref: str) -> RunTerminalRecord:
    return RunTerminalRecord(
        run_id=run_id,
        game_id="synthetic-transition-game",
        asset_manifest_ref=ref("asset"),
        final_state="NOT_FINISHED",
        levels_completed=1,
        win_levels=1,
        budget=BudgetReceipt(
            maximum_non_reset_actions=128,
            maximum_resets=4,
            maximum_total_environment_calls=132,
            wall_clock_seconds=1800,
            non_reset_actions=12,
            resets=1,
            total_environment_calls=13,
            elapsed_wall_ms=1000,
        ),
        frozen_runtime_ref=ref("runtime"),
        toolbelt_ref=ref("toolbelt"),
        integration_ref=ref("integration"),
        dependency_ref=ref("dependency"),
        model_interface_ref=ref("model-interface"),
        domain_adapter_ref=ref("domain-adapter"),
        executor_ref=ref("executor"),
        lab_genesis_ref=genesis_ref,
        latest_checkpoint_ref=ref("checkpoint"),
        initial_reset_admission_ref=ref("initial-reset"),
        terminal_frame=None,
        raw_trace=None,
        official_recordings=(),
        completion_genuinely_observed=False,
        disposition="partial",
        concise_result_summary="bounded source stage ended before terminal success",
        claim_class="synthetic",
        claim_exclusions=("not an environment result",),
    )


def _seal(terminal: RunTerminalRecord) -> RunSeal:
    ledger = ExternalLedgerSeal(
        receipt_count=1,
        receipt_head=ref("run-receipt-head"),
        object_count=2,
        objects_projection_ref=ref("objects-projection"),
        receipts_projection_ref=ref("receipts-projection"),
    )
    domain = ExternalDomainStateSeal(
        entry_count=0,
        entries=(),
        projection_ref=content_hash(()),
    )
    return RunSeal(
        run_id=terminal.run_id,
        lab_manifest_ref=ref("lab-manifest"),
        run_spec_ref=ref("run-spec"),
        genesis_ref=terminal.lab_genesis_ref,
        ledger_seal=ledger,
        domain_state_seal=domain,
        disposition=RunDisposition.PARTIAL,
        terminal_state=terminal.final_state,
        terminal_evidence_ref=terminal.digest,
        completion_genuinely_observed=False,
        terminal_authority_source="synthetic transition authority",
        concise_result_summary=terminal.concise_result_summary,
    )


def _source(
    tmp_path: Path,
    *,
    recommendation_with_symbol: bool,
) -> tuple[object, Path, Path]:
    learning_path = tmp_path / "source-learning.sqlite"
    artifacts = tmp_path / "transition-artifacts"
    sidecar = Calibration002LearningSidecar.create(
        learning_path,
        success_condition_ref=ref("success-condition"),
    )
    binding = sidecar.open_stage(run_id="stage-1")
    evaluation_refs: tuple[str, ...] = ()
    if recommendation_with_symbol:
        expansion = "observe-access-plan-" * 16
        source_entry = sidecar.append(
            entry_id="recurring-pattern",
            payload={"summary": expansion, "status": "bounded evidence"},
        )
        proposal = KevinSymbolProposal(
            token="motif",
            expansion=expansion,
            concise_meaning="repeated evidence-to-plan motif",
            source_payload_refs=(source_entry.source_payload_ref,),
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
        adaptation = sidecar.adapt(
            proposals=(proposal,),
            samples=samples,
            rationale="measure a recurring source-stage representation",
            evaluation_id="source-motif",
        )
        assert adaptation.promotion is not None
        evaluation_refs = (adaptation.evaluation.digest,)
    recommendation = sidecar.recommend(
        recommendation_id="next-stage",
        recommending_driver_ref=ref("recommending-driver"),
        evaluation_refs=evaluation_refs,
        rationale="recommend only measured shorthand for successor review",
    )
    terminal = _terminal(binding.run_id, genesis_ref=ref("lab-genesis"))
    source = close_source_stage(
        sidecar,
        transition_id="stage-1-to-stage-2",
        run_seal=_seal(terminal),
        source_capsule_ref=ref("capsule"),
        terminal_record=terminal,
        recommendation_ref=recommendation.digest,
        retained_mechanics=(
            Calibration002RetainedMechanicDraft(
                mechanic_id="access-gate",
                concise_statement="a visible gate remains a provisional access boundary",
                scope="current surface family",
                evidence_refs=(ref("mechanic-evidence"),),
                reopening_condition="reopen if a successor observation contradicts access",
                reliability="supported",
            ),
        ),
        artifact_root=artifacts,
    )
    return source, learning_path, artifacts


def test_approved_transition_is_target_bound_receipt_backed_and_reopenable(
    tmp_path: Path,
) -> None:
    source_value, learning_path, artifacts = _source(
        tmp_path,
        recommendation_with_symbol=True,
    )
    from calibration_002.transition import Calibration002SourceTransition

    assert isinstance(source_value, Calibration002SourceTransition)
    source = source_value
    review_ledger = tmp_path / "review.sqlite"
    request = Calibration002AdvisoryReviewRequest(
        review_id="review-stage-1",
        reviewer_driver_ref=ref("reviewer-driver"),
        disposition=AdvisoryDisposition.ACCEPT,
        rationale="accept the measured source representation as advisory input",
    )
    review = review_source_recommendation(
        source,
        request=request,
        source_learning_ledger_path=learning_path,
        review_ledger_path=review_ledger,
        artifact_root=artifacts,
    )
    assert review.status == "reviewed_not_adopted"
    assert review.authority == "NONE"
    assert source.next_stage_ref is not None
    configuration = source.recommendation_bundle.source_configuration
    control = Calibration002ExternalControlDecision(
        decision_id="approve-stage-2-representation",
        source_transition_ref=source.digest,
        advisory_review_ref=review.digest,
        recommendation_ref=source.recommendation_ref,
        target_stage_ref=source.next_stage_ref,
        target_configuration_ref=configuration.digest,
        control_source_ref=ref("external-control"),
        status=AdoptionStatus.APPROVED,
        rationale="approve only this exact representation for stage 2",
    )
    result = apply_external_control(
        source,
        review,
        control=control,
        target_configuration=configuration,
        source_learning_ledger_path=learning_path,
        review_ledger_path=review_ledger,
        artifact_root=artifacts,
        transfer_id="stage-1-to-stage-2",
        validation_refs=(ref("transition-validation"),),
    )

    assert result.outcome.disposition is TransitionDisposition.READY
    assert result.outcome.inheritance is not None
    assert result.outcome.shorthand_transfer is not None
    assert result.outcome.inheritance.curriculum_transfer.mechanic_refs == tuple(
        item.digest for item in source.retained_mechanics
    )
    assert (
        verify_transition_result(
            source,
            review,
            result,
            source_learning_ledger_path=learning_path,
            review_ledger_path=review_ledger,
            artifact_root=artifacts,
        )
        == result.verification
    )
    assert (artifacts / "transition.manifest.json").read_bytes() == canonical_bytes(
        result.manifest
    )

    with Calibration002LearningSidecar.restore(learning_path) as restored:
        successor = restored.open_stage(
            run_id="stage-2",
            inheritance=result.outcome.inheritance,
        )
        assert successor.workspace_mode == "explicit_inheritance"
        assert (
            restored.table().codebook_ref
            == result.outcome.shorthand_transfer.active_codebook_ref
        )


def test_review_can_evaluate_and_select_an_earned_refinement(tmp_path: Path) -> None:
    source_value, learning_path, artifacts = _source(
        tmp_path,
        recommendation_with_symbol=False,
    )
    from calibration_002.transition import Calibration002SourceTransition

    assert isinstance(source_value, Calibration002SourceTransition)
    source = source_value
    expansion = "new-stage-pattern-" * 18
    adaptation_payload = {"summary": expansion * 24}
    refinement = Calibration002RefinementRequest(
        proposals=(
            KevinSymbolProposal(
                token="next",
                expansion=expansion,
                concise_meaning="repeated successor-review pattern",
                source_payload_refs=(content_hash(adaptation_payload),),
            ),
        ),
        samples=(
            KevinEvaluationSample(
                case_id="review-adaptation",
                role=EvaluationRole.ADAPTATION,
                payload=adaptation_payload,
            ),
            KevinEvaluationSample(
                case_id="review-validation",
                role=EvaluationRole.VALIDATION,
                payload={"validation": expansion * 24},
            ),
        ),
        rationale="test a successor-facing recursive compression",
        evaluation_id="review-refinement",
    )
    request = Calibration002AdvisoryReviewRequest(
        review_id="review-with-refinement",
        reviewer_driver_ref=ref("reviewer-driver"),
        disposition=AdvisoryDisposition.ACCEPT,
        rationale="recommend the refinement only if its evaluation earns it",
        refinement=refinement,
    )
    review = review_source_recommendation(
        source,
        request=request,
        source_learning_ledger_path=learning_path,
        review_ledger_path=tmp_path / "review.sqlite",
        artifact_root=artifacts,
    )

    assert review.refinement_candidate is not None
    assert review.refinement_evaluation is not None
    assert review.refinement_evaluation.status.value == "eligible"
    assert review.refinement_selected
    assert review.kevin_review.reviewed_codebook_ref == review.refinement_candidate.digest


def test_deferred_review_cannot_self_authorize_or_export_a_transfer(tmp_path: Path) -> None:
    source_value, learning_path, artifacts = _source(
        tmp_path,
        recommendation_with_symbol=False,
    )
    from calibration_002.transition import Calibration002SourceTransition

    assert isinstance(source_value, Calibration002SourceTransition)
    source = source_value
    review_ledger = tmp_path / "review.sqlite"
    request = Calibration002AdvisoryReviewRequest(
        review_id="defer-stage-1",
        reviewer_driver_ref=ref("reviewer-driver"),
        disposition=AdvisoryDisposition.DEFER,
        rationale="retain the recommendation but defer successor use",
    )
    review = review_source_recommendation(
        source,
        request=request,
        source_learning_ledger_path=learning_path,
        review_ledger_path=review_ledger,
        artifact_root=artifacts,
    )
    assert source.next_stage_ref is not None
    configuration = source.recommendation_bundle.source_configuration
    approving = Calibration002ExternalControlDecision(
        decision_id="improper-approval",
        source_transition_ref=source.digest,
        advisory_review_ref=review.digest,
        recommendation_ref=source.recommendation_ref,
        target_stage_ref=source.next_stage_ref,
        target_configuration_ref=configuration.digest,
        control_source_ref=ref("external-control"),
        status=AdoptionStatus.APPROVED,
        rationale="this must fail because review deferred",
    )
    with pytest.raises(Calibration002TransitionError, match="deferred"):
        apply_external_control(
            source,
            review,
            control=approving,
            target_configuration=configuration,
            source_learning_ledger_path=learning_path,
            review_ledger_path=review_ledger,
            artifact_root=artifacts,
            transfer_id="must-not-transfer",
            validation_refs=(ref("validation"),),
        )

    self_authorizing = approving.model_copy(
        update={
            "status": AdoptionStatus.REJECTED,
            "control_source_ref": request.reviewer_driver_ref,
        }
    )
    with pytest.raises(Calibration002TransitionError, match="cannot authorize itself"):
        apply_external_control(
            source,
            review,
            control=self_authorizing,
            target_configuration=configuration,
            source_learning_ledger_path=learning_path,
            review_ledger_path=review_ledger,
            artifact_root=artifacts,
            transfer_id="must-not-self-authorize",
            validation_refs=(ref("validation"),),
        )


def test_rejected_review_records_nonadoption_without_successor_material(
    tmp_path: Path,
) -> None:
    source_value, learning_path, artifacts = _source(
        tmp_path,
        recommendation_with_symbol=False,
    )
    from calibration_002.transition import Calibration002SourceTransition

    assert isinstance(source_value, Calibration002SourceTransition)
    source = source_value
    review_ledger = tmp_path / "review.sqlite"
    review = review_source_recommendation(
        source,
        request=Calibration002AdvisoryReviewRequest(
            review_id="reject-stage-1",
            reviewer_driver_ref=ref("reviewer-driver"),
            disposition=AdvisoryDisposition.REJECT,
            rationale="do not carry this representation into the successor",
        ),
        source_learning_ledger_path=learning_path,
        review_ledger_path=review_ledger,
        artifact_root=artifacts,
    )
    assert source.next_stage_ref is not None
    configuration = source.recommendation_bundle.source_configuration
    control = Calibration002ExternalControlDecision(
        decision_id="reject-stage-2-representation",
        source_transition_ref=source.digest,
        advisory_review_ref=review.digest,
        recommendation_ref=source.recommendation_ref,
        target_stage_ref=source.next_stage_ref,
        target_configuration_ref=configuration.digest,
        control_source_ref=ref("external-control"),
        status=AdoptionStatus.REJECTED,
        rationale="reject this exact representation for stage 2",
    )
    result = apply_external_control(
        source,
        review,
        control=control,
        target_configuration=configuration,
        source_learning_ledger_path=learning_path,
        review_ledger_path=review_ledger,
        artifact_root=artifacts,
        transfer_id="stage-1-rejection",
        validation_refs=(ref("rejection-validation"),),
    )

    assert result.outcome.disposition is TransitionDisposition.REJECTED
    assert result.outcome.adoption.status is AdoptionStatus.REJECTED
    assert result.outcome.shorthand_transfer is None
    assert result.outcome.inheritance is None
    assert (
        verify_transition_result(
            source,
            review,
            result,
            source_learning_ledger_path=learning_path,
            review_ledger_path=review_ledger,
            artifact_root=artifacts,
        )
        == result.verification
    )


def test_refinement_rejects_excluded_run_material() -> None:
    with pytest.raises(ValidationError, match="excluded field"):
        Calibration002RefinementRequest(
            proposals=(
                KevinSymbolProposal(
                    token="bad",
                    expansion="bad-pattern",
                    concise_meaning="excluded material",
                    source_payload_refs=(ref("source"),),
                ),
            ),
            samples=(
                KevinEvaluationSample(
                    case_id="adaptation",
                    role=EvaluationRole.ADAPTATION,
                    payload={"action_sequence": ["must-not-transfer"]},
                ),
            ),
            rationale="must fail closed",
            evaluation_id="excluded",
        )


def test_review_ledger_must_be_distinct_from_source_learning_ledger(
    tmp_path: Path,
) -> None:
    source_value, learning_path, artifacts = _source(
        tmp_path,
        recommendation_with_symbol=False,
    )
    from calibration_002.transition import Calibration002SourceTransition

    assert isinstance(source_value, Calibration002SourceTransition)
    with pytest.raises(Calibration002TransitionError, match="separate"):
        review_source_recommendation(
            source_value,
            request=Calibration002AdvisoryReviewRequest(
                review_id="review",
                reviewer_driver_ref=ref("reviewer"),
                disposition=AdvisoryDisposition.ACCEPT,
                rationale="must use a distinct ledger",
            ),
            source_learning_ledger_path=learning_path,
            review_ledger_path=learning_path,
            artifact_root=artifacts,
        )
