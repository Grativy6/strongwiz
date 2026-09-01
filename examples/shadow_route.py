"""Minimal model-neutral, nonexecuting Strongwiz route."""

from strongwiz.canonical import canonical_text, content_hash
from strongwiz.contracts import (
    ActionSpec,
    CandidateProposal,
    ControlSnapshot,
    CostVector,
    DecisionEffect,
    Distinction,
    EvidenceRef,
    Goal,
    Observation,
    Prediction,
)
from strongwiz.routing import evaluate_proposal

distinction = Distinction(
    distinction_id="door-state",
    statement="The door may be open or closed",
    scope_id="room-1",
    parent_goal_id="leave-room",
    governing_goal_id="finish-task",
    candidate_resolutions=("open", "closed"),
    competing_predictions=("inspection shows a gap", "inspection shows a barrier"),
    decision_effects=(DecisionEffect.ACCESS,),
    decision_that_could_change="whether to try passage or seek an opener",
    relevance_summary="access determines the next plan",
    smallest_discriminating_test="inspect without changing the door",
    reopening_condition="the door or room changes",
)

scoped_goal = Goal(
    goal_id="leave-room",
    statement="leave the room",
    scope_id="room-1",
    parent_goal_id="finish-task",
    governing_goal_id="finish-task",
    motivating_uncertainty="the door state is unknown",
    decision_that_could_change="whether to pass or seek an opener",
    smallest_sufficient_test="inspect the door",
    success_condition="access to the exit is observed",
    reopening_condition="the room or door changes",
)

observed = Observation(
    observation_id="observation-1",
    domain="example",
    scope_id="room-1",
    epoch=0,
    payload_ref=EvidenceRef(kind="example", digest=content_hash({"door": "unknown"})),
    summary="a door blocks the exit",
    available_action_names=("inspect",),
)

proposal = CandidateProposal(
    proposal_id="inspect-door",
    model_driver_id="example-driver",
    observation_id="observation-1",
    observation_ref=observed.digest,
    scope_id="room-1",
    goal_id="leave-room",
    goal_ref=scoped_goal.digest,
    action=ActionSpec(name="inspect", parameters={"target": "door"}),
    meaningful_distinction=distinction,
    prediction=Prediction(
        prediction_id="door-inspection",
        hypothesis_refs=(),
        expected_consequences=("door state becomes observable",),
        falsified_by=("inspection produces no door evidence",),
        alternatives=("move elsewhere",),
    ),
    decision_effects=(DecisionEffect.ACCESS,),
    evidence_refs=("observation-evidence",),
    concise_rationale="a reversible observation separates access plans",
    reversible=True,
    expected_progress_rank=2,
    information_gain_rank=1,
    risk_rank=0,
    costs=CostVector(compute_units=1),
)

control = ControlSnapshot(
    account_id="example-account",
    account_version=0,
    observation_id="observation-1",
    observation_ref=observed.digest,
    scope_id="room-1",
    active_goal_ids=("leave-room",),
    active_goal_refs=(scoped_goal.digest,),
    available_evidence_refs=("observation-evidence",),
    allowed_action_names=("inspect",),
    remaining_budget=CostVector(compute_units=10),
    serial_token="example-serial",
    shadow_only=True,
)

decision = evaluate_proposal(proposal, control)
print(canonical_text(decision))
