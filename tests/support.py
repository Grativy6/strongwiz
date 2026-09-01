from __future__ import annotations

from strongwiz.canonical import content_hash
from strongwiz.contracts import (
    ActionSpec,
    BoundaryStatus,
    CandidateProposal,
    ControlSnapshot,
    CostVector,
    DecisionEffect,
    Distinction,
    EvidenceRef,
    Goal,
    LabBoundaryBinding,
    Observation,
    Prediction,
    ReasoningRequest,
)
from strongwiz.integrity import FrozenFile, FrozenRuntimeManifest
from strongwiz.policy import CadencePolicy
from strongwiz.routing import RouterPolicy


def ref(value: str) -> str:
    return content_hash({"value": value})


def evidence(value: str) -> EvidenceRef:
    return EvidenceRef(kind="test", digest=ref(value))


def frozen_runtime() -> FrozenRuntimeManifest:
    return FrozenRuntimeManifest(
        package_version="0.1.0.dev0",
        contract_schema="strongwiz.contract.v1",
        source_files=(
            FrozenFile(
                relative_path="src/strongwiz/runtime.py",
                size_bytes=1,
                sha256=ref("runtime-source"),
            ),
        ),
        configuration_ref=ref("config"),
        dependency_lock_ref=ref("lock"),
        model_driver_id="driver-test",
        model_driver_version="driver-v1",
        model_driver_artifact_ref=ref("driver-artifact"),
        domain_adapter_id="synthetic",
        domain_adapter_version="adapter-v1",
        domain_adapter_artifact_ref=ref("adapter-artifact"),
        capability_refs=(ref("capability"),),
        policy_refs=(RouterPolicy().digest, CadencePolicy().digest),
        runtime_description="deterministic synthetic test runtime",
    )


def governing_goal() -> Goal:
    return Goal(
        goal_id="goal-root",
        statement="solve the selected problem",
        scope_id="scope-1",
        success_condition="domain terminal authority reports success",
    )


def scoped_goal() -> Goal:
    return Goal(
        goal_id="goal-local",
        statement="open the blocked route",
        scope_id="scope-1",
        parent_goal_id="goal-root",
        governing_goal_id="goal-root",
        motivating_uncertainty="the access mechanism is unknown",
        decision_that_could_change="whether to use or inspect the latch",
        smallest_sufficient_test="inspect the latch once",
        success_condition="the route is open",
        abandonment_condition="a shorter verified route exists",
        reopening_condition="the route closes or the latch changes",
    )


def observation(*, epoch: int = 0, observation_id: str = "obs-1") -> Observation:
    return Observation(
        observation_id=observation_id,
        domain="synthetic",
        scope_id="scope-1",
        epoch=epoch,
        payload_ref=evidence(f"payload-{observation_id}"),
        summary="a door blocks the route",
        available_action_names=("inspect", "open"),
    )


def distinction() -> Distinction:
    return Distinction(
        distinction_id="dist-latch",
        statement="whether the latch opens the route",
        scope_id="scope-1",
        parent_goal_id="goal-local",
        governing_goal_id="goal-root",
        candidate_resolutions=("opens", "does_not_open"),
        competing_predictions=("door opens", "door remains closed"),
        decision_effects=(DecisionEffect.ACCESS, DecisionEffect.PLAN),
        decision_that_could_change="inspect versus use the latch",
        relevance_summary="access determines the credible plan",
        smallest_discriminating_test="inspect the latch",
        reopening_condition="a later latch behaves differently",
    )


def prediction() -> Prediction:
    return Prediction(
        prediction_id="pred-1",
        hypothesis_refs=("hyp-1",),
        expected_consequences=("latch state becomes visible",),
        falsified_by=("no visible state change",),
        alternatives=("door opens immediately",),
    )


def proposal(
    *,
    proposal_id: str = "proposal-1",
    action: str = "inspect",
    evidence_refs: tuple[str, ...] | None = None,
    trace_refs: tuple[str, ...] = (),
    material_delta_refs: tuple[str, ...] = (),
    prior_account_ref: str | None = None,
    progress_rank: int = 1,
    information_rank: int = 1,
    risk_rank: int = 0,
    costs: CostVector | None = None,
) -> CandidateProposal:
    return CandidateProposal(
        proposal_id=proposal_id,
        model_driver_id="driver-test",
        observation_id="obs-1",
        observation_ref=observation().digest,
        scope_id="scope-1",
        goal_id="goal-local",
        goal_ref=scoped_goal().digest,
        action=ActionSpec(name=action),
        meaningful_distinction=distinction(),
        prediction=prediction(),
        decision_effects=(DecisionEffect.ACCESS, DecisionEffect.PLAN),
        evidence_refs=(ref("evidence-1"),) if evidence_refs is None else evidence_refs,
        trace_refs=trace_refs,
        material_delta_refs=material_delta_refs,
        prior_account_ref=prior_account_ref,
        concise_rationale="this is the smallest safe discriminating action",
        reversible=True,
        expected_progress_rank=progress_rank,
        information_gain_rank=information_rank,
        risk_rank=risk_rank,
        costs=costs or CostVector(environment_actions=1, compute_units=2),
    )


def control(
    *,
    evidence_refs: tuple[str, ...] | None = None,
    trace_refs: tuple[str, ...] = (),
    residual_refs: tuple[str, ...] = (),
    account_refs: tuple[str, ...] = (),
    delta_refs: tuple[str, ...] = (),
    allowed_actions: tuple[str, ...] = ("inspect", "open"),
    allowed_action_refs: tuple[str, ...] = (),
    budget: CostVector | None = None,
    shadow_only: bool = True,
    grant_ref: str | None = None,
    lab_ref: str | None = None,
    lab_status: BoundaryStatus = BoundaryStatus.NOT_REQUESTED,
    lab_boundary_override: LabBoundaryBinding | None = None,
) -> ControlSnapshot:
    lab_boundary = lab_boundary_override
    if lab_boundary is None and lab_status is not BoundaryStatus.NOT_REQUESTED:
        if lab_ref is None or grant_ref is None:
            raise ValueError("non-shadow lab test control requires grant and decision refs")
        bound = proposal()
        lab_boundary = LabBoundaryBinding(
            decision_ref=lab_ref,
            grant_ref=grant_ref,
            proposal_ref=bound.digest,
            action_ref=bound.action.digest,
            observation_id=bound.observation_id,
            observation_ref=bound.observation_ref,
            scope_id=bound.scope_id,
            status=lab_status,
        )
    return ControlSnapshot(
        account_id="account-1",
        account_version=0,
        observation_id="obs-1",
        observation_ref=observation().digest,
        scope_id="scope-1",
        active_goal_ids=("goal-root", "goal-local"),
        active_goal_refs=(governing_goal().digest, scoped_goal().digest),
        available_evidence_refs=(ref("evidence-1"),)
        if evidence_refs is None
        else evidence_refs,
        available_trace_refs=trace_refs,
        available_residual_refs=residual_refs,
        available_account_refs=account_refs,
        accepted_material_delta_refs=delta_refs,
        allowed_action_names=allowed_actions,
        allowed_action_refs=allowed_action_refs,
        remaining_budget=budget
        or CostVector(
            environment_actions=10,
            irreversible_actions=10,
            life_risk_units=10,
            wall_clock_ms=10_000,
            compute_units=10_000,
            memory_bytes=10_000,
            context_tokens=10_000,
            acquisition_units=10_000,
            validation_units=10_000,
            transport_units=10_000,
            invalidation_units=10_000,
            output_units=10_000,
        ),
        lab_boundary=lab_boundary,
        execution_grant_ref=grant_ref,
        serial_token="serial-1",
        shadow_only=shadow_only,
    )


def request() -> ReasoningRequest:
    return ReasoningRequest(
        observation=observation(),
        governing_goal=governing_goal(),
        scoped_goal=scoped_goal(),
        active_distinctions=(distinction(),),
        retained_fact_refs=(ref("fact-1"),),
    )
