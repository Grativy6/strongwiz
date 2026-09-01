from __future__ import annotations

import pytest

from strongwiz.authority import (
    AuthorityError,
    GrantRegistry,
    GrantSource,
    ReleaseStatus,
    TaskGrant,
)
from strongwiz.contracts import BoundaryStatus, CandidateProposal, ControlSnapshot, CostVector
from strongwiz.drivers import ExecutionCommand, ExecutorObservation
from strongwiz.lab_policy import (
    ConsequentialCrossing,
    CrossingStage,
    LabBoundaryContext,
    LabPolicyDecision,
    PEAReview,
    ReviewStatus,
    evaluate_lab_rules,
)
from strongwiz.orchestration import (
    ExecutionCoordinator,
    ExecutionDisposition,
    OrchestrationError,
)
from strongwiz.routing import evaluate_proposal
from tests.support import evidence, proposal, ref, scoped_goal


class RecordingExecutor:
    executor_id = "executor"
    executor_version = "executor-v1"
    executor_artifact_ref = ref("executor-artifact")

    def __init__(self) -> None:
        self.calls = 0
        self.commands: list[ExecutionCommand] = []
        self.raise_after_effect = False

    def execute(self, command: ExecutionCommand) -> ExecutorObservation:
        self.calls += 1
        self.commands.append(command)
        if self.raise_after_effect:
            raise TimeoutError("effect may have occurred before timeout")
        return ExecutorObservation(
            evidence_ref=evidence("executor-after"), raw_after={"ok": True}
        )


def clear_review(
    grant: TaskGrant,
) -> tuple[
    CandidateProposal,
    LabPolicyDecision,
    PEAReview,
    ConsequentialCrossing,
]:
    candidate = proposal()
    context = LabBoundaryContext(
        grant_ref=grant.grant_ref,
        task_id=grant.task_id,
        goal_id=grant.goal_id,
        goal_ref=grant.goal_ref,
        scope_id=grant.scope_id,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        proposal_ref=candidate.digest,
        action_ref=candidate.action.digest,
        output_destination_ref=grant.output_destination_ref,
        attention_budget=grant.maximum_attention_units,
    )
    review = PEAReview(
        boundary_context_ref=context.digest,
        external_grant_ref=grant.grant_ref,
        consent=ReviewStatus.SUPPLIED,
        standing=ReviewStatus.SUPPLIED,
        privacy=ReviewStatus.SUPPLIED,
        reversibility=ReviewStatus.SUPPLIED,
        remedy=ReviewStatus.SUPPLIED,
        contestability=ReviewStatus.SUPPLIED,
        refusal=ReviewStatus.SUPPLIED,
        human_responsibility_ref=ref("responsible-human"),
    )
    crossing = ConsequentialCrossing(
        boundary_context_ref=context.digest,
        subject_ref=candidate.action.digest,
        description_ref=ref("description"),
        recommendation_ref=ref("recommendation"),
        permission_ref=ref("permission"),
        authorization_ref=ref("authorization"),
        current_stage=CrossingStage.AUTHORIZATION,
        externally_supplied_authorization=True,
    )
    return (
        candidate,
        evaluate_lab_rules(
            context=context,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            external_effect_requested=True,
            release_requested=False,
        ),
        review,
        crossing,
    )


def test_exact_route_lab_context_and_grant_form_one_execution_handoff() -> None:
    grants = GrantRegistry()
    grant = TaskGrant(
        root_ref=ref("human-root"),
        source=GrantSource.HUMAN,
        task_id="task",
        goal_id=scoped_goal().goal_id,
        goal_ref=scoped_goal().digest,
        scope_id="scope-1",
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=5,
        maximum_invocations=2,
        allowed_action_names=("inspect",),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("executor"),
        release_review_required=False,
        maximum_attention_units=10,
    )
    grant_ref = grants.activate(grant)
    candidate, decision, review, crossing = clear_review(grant)
    binding = decision.external_effect_binding
    assert binding is not None
    control = ControlSnapshot(
        account_id="account",
        account_version=0,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        scope_id=candidate.scope_id,
        active_goal_ids=(candidate.goal_id,),
        active_goal_refs=(candidate.goal_ref,),
        available_evidence_refs=candidate.evidence_refs,
        allowed_action_names=(candidate.action.name,),
        allowed_action_refs=(candidate.action.digest,),
        remaining_budget=CostVector(environment_actions=1, compute_units=10),
        lab_boundary=binding,
        execution_grant_ref=grant_ref,
        serial_token="serial",
        shadow_only=False,
    )
    route = evaluate_proposal(candidate, control)
    executor = RecordingExecutor()
    coordinator = ExecutionCoordinator(grants, executor)
    forged_route = route.model_copy(update={"guards": ()})
    with pytest.raises(OrchestrationError, match="configured hard guards"):
        coordinator.begin(
            proposal=candidate,
            route=forged_route,
            control=control,
            lab_decision=decision,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            invocation_id="forged-route",
            boundary=0,
        )
    refused_decision = decision.model_copy(update={"release_status": BoundaryStatus.REFUSE})
    refused_binding = refused_decision.external_effect_binding
    assert refused_binding is not None
    refused_control = control.model_copy(
        update={"lab_boundary": refused_binding, "serial_token": "refused-serial"}
    )
    refused_route = evaluate_proposal(candidate, refused_control)
    with pytest.raises(OrchestrationError, match="PEA, PECAN, and SEED"):
        coordinator.begin(
            proposal=candidate,
            route=refused_route,
            control=refused_control,
            lab_decision=refused_decision,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            invocation_id="refused-seed",
            boundary=0,
        )
    permit, admission = coordinator.begin(
        proposal=candidate,
        route=route,
        control=control,
        lab_decision=decision,
        pea_review=review,
        crossing=crossing,
        seed_release=None,
        invocation_id="invocation",
        boundary=0,
    )
    mismatched_admission = admission.model_copy(update={"action_ref": ref("other-action")})
    with pytest.raises(OrchestrationError, match="permit and execution admission"):
        coordinator.execute_once(permit, mismatched_admission, candidate, boundary=0)
    spliced_metadata = admission.model_copy(
        update={"scope_id": "other-scope", "schema_id": "attacker-schema"}
    )
    with pytest.raises(OrchestrationError, match="permit and execution admission"):
        coordinator.execute_once(permit, spliced_metadata, candidate, boundary=0)
    executor.executor_version = "executor-v2"
    executor.executor_artifact_ref = ref("executor-artifact-v2")
    spliced_executor = admission.model_copy(
        update={
            "executor_version": executor.executor_version,
            "executor_artifact_ref": executor.executor_artifact_ref,
        }
    )
    with pytest.raises(OrchestrationError, match="permit and execution admission"):
        coordinator.execute_once(permit, spliced_executor, candidate, boundary=0)
    executor.executor_version = "executor-v1"
    executor.executor_artifact_ref = ref("executor-artifact")
    result = coordinator.execute_once(permit, admission, candidate, boundary=0)
    assert result.release.status is ReleaseStatus.RELEASED
    assert result.release.proposal_ref == candidate.digest
    assert result.attempt.disposition is ExecutionDisposition.COMPLETED
    assert executor.calls == 1
    assert executor.commands[0].idempotency_key == admission.digest
    assert executor.commands[0].authority == "NONE"
    assert permit.used
    with pytest.raises(AuthorityError, match="already been used"):
        coordinator.execute_once(permit, admission, candidate, boundary=0)
    assert executor.calls == 1

    second_control = control.model_copy(update={"serial_token": "serial-timeout"})
    second_route = evaluate_proposal(candidate, second_control)
    second_permit, second_admission = coordinator.begin(
        proposal=candidate,
        route=second_route,
        control=second_control,
        lab_decision=decision,
        pea_review=review,
        crossing=crossing,
        seed_release=None,
        invocation_id="invocation-timeout",
        boundary=0,
    )
    executor.raise_after_effect = True
    unknown = coordinator.execute_once(second_permit, second_admission, candidate, boundary=0)
    assert unknown.release.status is ReleaseStatus.RELEASED
    assert unknown.attempt.disposition is ExecutionDisposition.UNKNOWN_EFFECT
    assert unknown.observation is None
    assert unknown.attempt.idempotency_key == second_admission.digest
    assert "TimeoutError" in (unknown.attempt.failure_category or "")
    assert executor.calls == 2


def test_control_owned_release_classification_cannot_be_marked_not_requested() -> None:
    grants = GrantRegistry()
    grant = TaskGrant(
        root_ref=ref("human-release-root"),
        source=GrantSource.HUMAN,
        task_id="release-task",
        goal_id=scoped_goal().goal_id,
        goal_ref=scoped_goal().digest,
        scope_id="scope-1",
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=5,
        maximum_invocations=1,
        allowed_action_names=("inspect",),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("human-output"),
        release_review_required=True,
        maximum_attention_units=10,
    )
    grants.activate(grant)
    candidate, not_requested, review, crossing = clear_review(grant)
    binding = not_requested.external_effect_binding
    assert binding is not None
    control = ControlSnapshot(
        account_id="account",
        account_version=0,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        scope_id=candidate.scope_id,
        active_goal_ids=(candidate.goal_id,),
        active_goal_refs=(candidate.goal_ref,),
        available_evidence_refs=candidate.evidence_refs,
        allowed_action_names=(candidate.action.name,),
        allowed_action_refs=(candidate.action.digest,),
        remaining_budget=CostVector(environment_actions=1, compute_units=10),
        lab_boundary=binding,
        execution_grant_ref=grant.grant_ref,
        serial_token="release-serial",
        shadow_only=False,
    )
    route = evaluate_proposal(candidate, control)
    with pytest.raises(OrchestrationError, match="PEA, PECAN, and SEED"):
        ExecutionCoordinator(grants, RecordingExecutor()).begin(
            proposal=candidate,
            route=route,
            control=control,
            lab_decision=not_requested,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            invocation_id="release-invocation",
            boundary=0,
        )


def test_execution_handoff_refuses_a_lab_decision_for_another_proposal() -> None:
    grants = GrantRegistry()
    grant = TaskGrant(
        root_ref=ref("human-root"),
        source=GrantSource.HUMAN,
        task_id="task",
        goal_id=scoped_goal().goal_id,
        goal_ref=scoped_goal().digest,
        scope_id="scope-1",
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=5,
        maximum_invocations=1,
        allowed_action_names=("inspect",),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("executor"),
        release_review_required=False,
        maximum_attention_units=10,
    )
    grants.activate(grant)
    candidate, decision, review, crossing = clear_review(grant)
    altered = candidate.model_copy(update={"proposal_id": "another"})
    binding = decision.external_effect_binding
    assert binding is not None
    control = ControlSnapshot(
        account_id="account",
        account_version=0,
        observation_id=altered.observation_id,
        observation_ref=altered.observation_ref,
        scope_id=altered.scope_id,
        active_goal_ids=(altered.goal_id,),
        active_goal_refs=(altered.goal_ref,),
        available_evidence_refs=altered.evidence_refs,
        allowed_action_names=(altered.action.name,),
        remaining_budget=CostVector(environment_actions=1, compute_units=10),
        lab_boundary=binding,
        execution_grant_ref=grant.grant_ref,
        serial_token="serial",
        shadow_only=False,
    )
    route = evaluate_proposal(altered, control)
    assert route.selected_proposal_ref is None
    with pytest.raises(OrchestrationError, match="admitted"):
        ExecutionCoordinator(grants, RecordingExecutor()).begin(
            proposal=altered,
            route=route,
            control=control,
            lab_decision=decision,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            invocation_id="invocation",
            boundary=0,
        )


def test_execution_handoff_refuses_a_route_from_another_control_snapshot() -> None:
    grants = GrantRegistry()
    grant = TaskGrant(
        root_ref=ref("human-root"),
        source=GrantSource.HUMAN,
        task_id="task",
        goal_id=scoped_goal().goal_id,
        goal_ref=scoped_goal().digest,
        scope_id="scope-1",
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=5,
        maximum_invocations=1,
        allowed_action_names=("inspect",),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("executor"),
        release_review_required=False,
        maximum_attention_units=10,
    )
    grants.activate(grant)
    candidate, decision, review, crossing = clear_review(grant)
    binding = decision.external_effect_binding
    assert binding is not None
    control = ControlSnapshot(
        account_id="account",
        account_version=0,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        scope_id=candidate.scope_id,
        active_goal_ids=(candidate.goal_id,),
        active_goal_refs=(candidate.goal_ref,),
        available_evidence_refs=candidate.evidence_refs,
        allowed_action_names=(candidate.action.name,),
        remaining_budget=CostVector(environment_actions=1, compute_units=10),
        lab_boundary=binding,
        execution_grant_ref=grant.grant_ref,
        serial_token="serial-a",
        shadow_only=False,
    )
    route = evaluate_proposal(candidate, control)
    changed_control = control.model_copy(update={"serial_token": "serial-b"})
    with pytest.raises(OrchestrationError, match="control snapshot"):
        ExecutionCoordinator(grants, RecordingExecutor()).begin(
            proposal=candidate,
            route=route,
            control=changed_control,
            lab_decision=decision,
            pea_review=review,
            crossing=crossing,
            seed_release=None,
            invocation_id="invocation",
            boundary=0,
        )
