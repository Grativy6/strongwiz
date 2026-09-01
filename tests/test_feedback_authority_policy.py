from __future__ import annotations

import pickle

import pytest
from pydantic import ValidationError

from strongwiz.authority import (
    AuthorityError,
    DecisionPermit,
    GrantRegistry,
    GrantSource,
    GrantStatus,
    ReleaseStatus,
    TaskGrant,
)
from strongwiz.feedback import (
    ContinuationState,
    ContinuationStore,
    FeedbackError,
    StructuralHorizonAudit,
    build_splice_matrix,
    interaction_contrast,
)
from strongwiz.lab_policy import (
    ConsequentialCrossing,
    CrossingStage,
    LabBoundaryContext,
    LabGateStatus,
    PEAReview,
    ReleaseClaimStatus,
    ReviewStatus,
    SEEDReleaseReview,
    evaluate_lab_rules,
)
from tests.support import ref


def continuation(*, working: str, cache: str, branch: str = "main") -> ContinuationState:
    return ContinuationState(
        producer_id="model-driver",
        producer_version="weights-v1",
        domain_epoch=1,
        goal_epoch=2,
        observation_ref=ref(f"obs-{working}"),
        authoritative_state_ref=ref("world"),
        explicit_working_state_ref=ref(working),
        cached_context_refs=(ref(cache),),
        branch_id=branch,
        causal_reach_steps=8,
        overwrite_horizon_steps=16,
        bottleneck_description="one explicit state and bounded cache",
    )


def grant(*, replaces: str | None = None, mode: str = "COPY") -> TaskGrant:
    return TaskGrant(
        root_ref=ref("external-root"),
        source=GrantSource.HUMAN,
        task_id=f"task-{mode}",
        goal_id=f"goal-{mode}",
        goal_ref=ref(f"goal-{mode}"),
        scope_id="scope",
        generation=0 if replaces is None else 1,
        issued_boundary=0 if replaces is None else 1,
        not_before_boundary=0 if replaces is None else 1,
        expires_boundary=10,
        maximum_invocations=2,
        allowed_action_names=("inspect",),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("destination"),
        release_review_required=False,
        maximum_attention_units=10,
        replaces_grant_ref=replaces,
    )


def begin_permit(
    registry: GrantRegistry,
    grant_ref: str,
    *,
    invocation_id: str,
    serial_token: str,
    action_name: str = "inspect",
    boundary: int = 0,
) -> DecisionPermit:
    return registry._begin_permit(
        grant_ref=grant_ref,
        invocation_id=invocation_id,
        proposal_ref=ref(f"proposal-{invocation_id}"),
        action_ref=ref(f"action-{invocation_id}-{action_name}"),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        observation_id=f"observation-{invocation_id}",
        observation_ref=ref(f"observation-{invocation_id}"),
        scope_id="scope",
        route_ref=ref(f"route-{invocation_id}"),
        control_ref=ref(f"control-{invocation_id}"),
        lab_decision_ref=ref(f"lab-{invocation_id}"),
        boundary=boundary,
        action_name=action_name,
        serial_token=serial_token,
    )


def lab_context() -> LabBoundaryContext:
    return LabBoundaryContext(
        grant_ref=ref("review-grant"),
        task_id="task",
        goal_id="goal",
        goal_ref=ref("goal"),
        scope_id="scope",
        observation_id="observation",
        observation_ref=ref("observation"),
        proposal_ref=ref("proposal"),
        action_ref=ref("action"),
        output_destination_ref=ref("destination"),
        attention_budget=10,
        release_output_ref=ref("answer"),
    )


def test_continuation_is_version_bound_branch_safe_and_invalidatable() -> None:
    store = ContinuationStore()
    state = continuation(working="working-a", cache="cache-a")
    state_ref = store.put(state)
    assert (
        store.get(
            state_ref,
            producer_id="model-driver",
            producer_version="weights-v1",
            domain_epoch=1,
            goal_epoch=2,
        )
        == state
    )
    branch = store.fork(state_ref, branch_id="counterfactual")
    assert branch.parent_state_ref == state_ref
    assert branch.state_ref != state_ref
    with pytest.raises(FeedbackError, match="stale"):
        store.get(
            state_ref,
            producer_id="model-driver",
            producer_version="weights-v2",
            domain_epoch=1,
            goal_epoch=2,
        )
    assert store.invalidate_producer("model-driver", reason="weights changed")
    with pytest.raises(FeedbackError, match="invalidated"):
        store.fork(state_ref, branch_id="invalid-revival")
    with pytest.raises(FeedbackError, match="invalidated"):
        store.get(
            state_ref,
            producer_id="model-driver",
            producer_version="weights-v1",
            domain_epoch=1,
            goal_epoch=2,
        )


def test_two_factor_splice_and_interaction_are_exact() -> None:
    recipient = continuation(working="recipient-working", cache="recipient-cache")
    donor = continuation(working="donor-working", cache="donor-cache")
    matrix = build_splice_matrix(recipient, donor)
    assert {cell.cell_id for cell in matrix.cells} == {"00", "01", "10", "11"}
    cell_10 = next(cell for cell in matrix.cells if cell.cell_id == "10")
    assert cell_10.working_state_ref == donor.explicit_working_state_ref
    assert cell_10.cached_context_refs == recipient.cached_context_refs
    assert interaction_contrast(y00=1, y01=2, y10=3, y11=8) == 4


def test_structural_horizon_requires_honest_measurement_window() -> None:
    audit = StructuralHorizonAudit(
        mechanism_ref=ref("mechanism"),
        causal_reach_steps=4,
        overwrite_horizon_steps=5,
        forced_convergence=True,
        convergence_reason="right-shift geometry erases donor dependence",
        valid_measurement_start=0,
        valid_measurement_end=4,
        information_bottleneck="one state vector",
    )
    assert audit.forced_convergence
    with pytest.raises(ValidationError, match="inverted"):
        StructuralHorizonAudit.model_validate(
            {
                **audit.model_dump(mode="python"),
                "valid_measurement_start": 5,
                "valid_measurement_end": 2,
            }
        )


def test_grant_is_checked_before_and_immediately_before_release() -> None:
    registry = GrantRegistry()
    active = grant()
    active_ref = registry.activate(active)
    permit = begin_permit(
        registry,
        active_ref,
        invocation_id="invoke-1",
        serial_token="serial-1",
    )
    released = registry._release_permit(
        permit,
        proposal_ref=permit.proposal_ref,
        action_ref=permit.action_ref,
        candidate_ref=ref("candidate"),
        boundary=1,
        action_name="inspect",
        executor_id="executor",
    )
    assert released.status is ReleaseStatus.RELEASED
    assert permit.used
    with pytest.raises(TypeError, match="nonserializable"):
        pickle.dumps(
            begin_permit(
                registry,
                active_ref,
                invocation_id="invoke-2",
                serial_token="serial-2",
                boundary=1,
            )
        )


def test_forged_or_mutated_permit_cannot_consume_registry_authority() -> None:
    registry = GrantRegistry()
    limited = TaskGrant.model_validate(
        {**grant().model_dump(mode="python"), "maximum_invocations": 1}
    )
    grant_ref = registry.activate(limited)
    forged = DecisionPermit(
        token="forged",
        issuer=object(),
        grant_ref=grant_ref,
        invocation_id="forged",
        proposal_ref=ref("forged-proposal"),
        action_ref=ref("forged-action"),
        action_name="inspect",
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        observation_id="forged-observation",
        observation_ref=ref("forged-observation"),
        scope_id="scope",
        route_ref=ref("forged-route"),
        control_ref=ref("forged-control"),
        lab_decision_ref=ref("forged-lab"),
        boundary=0,
    )
    with pytest.raises(AuthorityError, match="not issued"):
        registry._release_permit(
            forged,
            proposal_ref=forged.proposal_ref,
            action_ref=forged.action_ref,
            candidate_ref=forged.proposal_ref,
            boundary=0,
            action_name=forged.action_name,
            executor_id=forged.executor_id,
        )
    with pytest.raises(AttributeError, match="immutable"):
        forged.action_ref = ref("mutated")
    valid = begin_permit(
        registry,
        grant_ref,
        invocation_id="valid-after-forgery",
        serial_token="valid-serial",
    )
    assert not valid.used


def test_revocation_or_replacement_during_work_quarantines_candidate() -> None:
    registry = GrantRegistry()
    original = grant()
    original_ref = registry.activate(original)
    permit = begin_permit(
        registry,
        original_ref,
        invocation_id="invoke",
        serial_token="serial",
    )
    replacement = grant(replaces=original_ref, mode="INVERT")
    replacement_ref = registry.activate(replacement)
    quarantined = registry._release_permit(
        permit,
        proposal_ref=permit.proposal_ref,
        action_ref=permit.action_ref,
        candidate_ref=ref("private-candidate"),
        boundary=1,
        action_name="inspect",
        executor_id="executor",
    )
    assert quarantined.status is ReleaseStatus.QUARANTINED
    assert quarantined.output_destination_ref is None
    assert registry.status(original_ref) is GrantStatus.SUPERSEDED
    assert registry.active_grant_ref == replacement_ref


def test_grant_rejects_replayed_serial_and_out_of_aperture_action() -> None:
    registry = GrantRegistry()
    active_ref = registry.activate(grant())
    begin_permit(
        registry,
        active_ref,
        invocation_id="one",
        serial_token="same",
    )
    with pytest.raises(AuthorityError, match="serial"):
        begin_permit(
            registry,
            active_ref,
            invocation_id="two",
            serial_token="same",
        )
    with pytest.raises(AuthorityError, match="aperture"):
        begin_permit(
            registry,
            active_ref,
            invocation_id="three",
            action_name="delete",
            serial_token="different",
        )


def test_permit_reserves_capacity_and_quarantines_binding_substitution() -> None:
    registry = GrantRegistry()
    limited = TaskGrant.model_validate(
        {
            **grant().model_dump(mode="python"),
            "maximum_invocations": 1,
            "allowed_action_names": ("inspect", "open"),
        }
    )
    grant_ref = registry.activate(limited)
    permit = begin_permit(
        registry,
        grant_ref,
        invocation_id="reserved",
        serial_token="serial-reserved",
    )
    with pytest.raises(AuthorityError, match="budget"):
        begin_permit(
            registry,
            grant_ref,
            invocation_id="over-budget",
            serial_token="serial-over-budget",
        )
    quarantined = registry._release_permit(
        permit,
        proposal_ref=permit.proposal_ref,
        action_ref=permit.action_ref,
        candidate_ref=ref("candidate"),
        boundary=1,
        action_name="open",
        executor_id="executor",
    )
    assert quarantined.status is ReleaseStatus.QUARANTINED
    assert "bindings differ" in quarantined.reason
    assert registry.status(grant_ref) is GrantStatus.EXHAUSTED


def test_pea_is_nonexecuting_pecan_cannot_skip_and_seed_preserves_stop() -> None:
    review = PEAReview(
        boundary_context_ref=lab_context().digest,
        external_grant_ref=ref("review-grant"),
        consent=ReviewStatus.SUPPLIED,
        standing=ReviewStatus.SUPPLIED,
        privacy=ReviewStatus.UNRESOLVED,
        reversibility=ReviewStatus.SUPPLIED,
        remedy=ReviewStatus.SUPPLIED,
        contestability=ReviewStatus.SUPPLIED,
        refusal=ReviewStatus.SUPPLIED,
        human_responsibility_ref=ref("human"),
        open_concerns=("privacy boundary remains unresolved",),
    )
    assert review.authority == "NONE"
    with pytest.raises(ValidationError, match="cannot skip"):
        ConsequentialCrossing(
            boundary_context_ref=lab_context().digest,
            subject_ref=ref("action"),
            description_ref=ref("description"),
            current_stage=CrossingStage.PERMISSION,
        )
    authorized = ConsequentialCrossing(
        boundary_context_ref=lab_context().digest,
        subject_ref=ref("action"),
        description_ref=ref("description"),
        recommendation_ref=ref("recommendation"),
        permission_ref=ref("permission"),
        authorization_ref=ref("authorization"),
        current_stage=CrossingStage.AUTHORIZATION,
        externally_supplied_authorization=True,
    )
    assert authorized.may_cross_external_boundary
    release = SEEDReleaseReview(
        boundary_context_ref=lab_context().digest,
        output_ref=ref("answer"),
        chosen_goal_id="goal",
        chosen_goal_ref=ref("goal"),
        claim_status=ReleaseClaimStatus.BOUNDED_RECOMMENDATION,
        claim_ceiling="bounded recommendation only",
        uncertainty_status=ReviewStatus.SUPPLIED,
        uncertainty_notes=("mechanism remains untested externally",),
        authority_status=ReviewStatus.SUPPLIED,
        authority_limits=("no external action authority",),
        privacy_status=ReviewStatus.SUPPLIED,
        privacy_notes=("no private data used",),
        correction_path="provide contrary evidence",
        reopening_condition="new evidence changes the recommendation",
        natural_stop="stop after the requested answer",
        attention_units_requested=1,
        preserves_user_agency=True,
    )
    assert release.releasable
    assert not release.model_personhood_claim_detected

    held = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=authorized,
        seed_release=release,
        external_effect_requested=True,
        release_requested=True,
    )
    assert held.external_effect_status is LabGateStatus.HOLD
    assert held.release_status is LabGateStatus.HOLD
    assert "PEA_UNRESOLVED:external_effect" in held.blockers
    assert held.authority == "NONE"


def test_lab_rules_require_each_independent_boundary_to_clear() -> None:
    review = PEAReview(
        boundary_context_ref=lab_context().digest,
        external_grant_ref=ref("review-grant"),
        consent=ReviewStatus.SUPPLIED,
        standing=ReviewStatus.SUPPLIED,
        privacy=ReviewStatus.SUPPLIED,
        reversibility=ReviewStatus.SUPPLIED,
        remedy=ReviewStatus.SUPPLIED,
        contestability=ReviewStatus.SUPPLIED,
        refusal=ReviewStatus.SUPPLIED,
        human_responsibility_ref=ref("human"),
    )
    authorized = ConsequentialCrossing(
        boundary_context_ref=lab_context().digest,
        subject_ref=ref("action"),
        description_ref=ref("description"),
        recommendation_ref=ref("recommendation"),
        permission_ref=ref("permission"),
        authorization_ref=ref("authorization"),
        current_stage=CrossingStage.AUTHORIZATION,
        externally_supplied_authorization=True,
    )
    release = SEEDReleaseReview(
        boundary_context_ref=lab_context().digest,
        output_ref=ref("answer"),
        chosen_goal_id="goal",
        chosen_goal_ref=ref("goal"),
        claim_status=ReleaseClaimStatus.BOUNDED_RECOMMENDATION,
        claim_ceiling="bounded recommendation only",
        uncertainty_status=ReviewStatus.NOT_APPLICABLE,
        uncertainty_notes=(),
        authority_status=ReviewStatus.SUPPLIED,
        authority_limits=("no authority manufactured",),
        privacy_status=ReviewStatus.SUPPLIED,
        privacy_notes=("no private data",),
        correction_path="supply contrary evidence",
        reopening_condition="material evidence changes",
        natural_stop="return the requested result and stop",
        preserves_user_agency=True,
    )
    clear = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=authorized,
        seed_release=release,
        external_effect_requested=True,
        release_requested=True,
    )
    assert clear.clears_requested_boundaries
    assert clear.external_effect_status is LabGateStatus.CLEAR
    assert clear.release_status is LabGateStatus.CLEAR

    changed_destination = lab_context().model_copy(
        update={"output_destination_ref": ref("another-destination")}
    )
    replayed_reviews = evaluate_lab_rules(
        context=changed_destination,
        pea_review=review,
        crossing=authorized,
        seed_release=release,
        external_effect_requested=True,
        release_requested=True,
    )
    assert replayed_reviews.external_effect_status is LabGateStatus.REFUSE
    assert replayed_reviews.release_status is LabGateStatus.REFUSE
    assert "PEA_CONTEXT_BINDING_MISMATCH:external_effect" in replayed_reviews.blockers

    refused_release = SEEDReleaseReview.model_validate(
        {**release.model_dump(mode="python"), "manufactured_dependency_detected": True}
    )
    refused = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=authorized,
        seed_release=refused_release,
        external_effect_requested=False,
        release_requested=True,
    )
    assert refused.external_effect_status is LabGateStatus.NOT_REQUESTED
    assert refused.release_status is LabGateStatus.REFUSE
    assert not refused.clears_requested_boundaries

    over_attention = release.model_copy(update={"attention_units_requested": 11})
    attention_refused = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=authorized,
        seed_release=over_attention,
        external_effect_requested=False,
        release_requested=True,
    )
    assert attention_refused.release_status is LabGateStatus.REFUSE
    assert "SEED_ATTENTION_BUDGET_EXCEEDED" in attention_refused.blockers

    with pytest.raises(ValidationError, match=r"uncertainty.*requires content"):
        release.model_copy(
            update={
                "uncertainty_status": ReviewStatus.SUPPLIED,
                "uncertainty_notes": (),
            }
        )

    missing_crossing = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=None,
        seed_release=release,
        external_effect_requested=True,
        release_requested=False,
    )
    assert missing_crossing.external_effect_status is LabGateStatus.HOLD
    assert "PECAN_EXTERNAL_AUTHORIZATION_MISSING" in missing_crossing.blockers

    missing_release = evaluate_lab_rules(
        context=lab_context(),
        pea_review=review,
        crossing=authorized,
        seed_release=None,
        external_effect_requested=False,
        release_requested=True,
    )
    assert missing_release.release_status is LabGateStatus.HOLD
    assert "SEED_RELEASE_REVIEW_MISSING" in missing_release.blockers

    refused_review = PEAReview.model_validate(
        {**review.model_dump(mode="python"), "consent": ReviewStatus.REFUSED}
    )
    refused_effect = evaluate_lab_rules(
        context=lab_context(),
        pea_review=refused_review,
        crossing=authorized,
        seed_release=release,
        external_effect_requested=True,
        release_requested=False,
    )
    assert refused_effect.external_effect_status is LabGateStatus.REFUSE
    assert not refused_review.protective_controls_satisfied
    assert review.protective_controls_satisfied

    concerned_review = review.model_copy(update={"open_concerns": ("remedy owner unknown",)})
    concerned = evaluate_lab_rules(
        context=lab_context(),
        pea_review=concerned_review,
        crossing=authorized,
        seed_release=release,
        external_effect_requested=True,
        release_requested=False,
    )
    assert concerned.external_effect_status is LabGateStatus.HOLD
    assert "PEA_UNRESOLVED:external_effect" in concerned.blockers
    assert not concerned_review.protective_controls_satisfied
