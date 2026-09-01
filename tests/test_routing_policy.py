from __future__ import annotations

import pytest

from strongwiz.contracts import BoundaryStatus, CostVector, RouteDisposition
from strongwiz.policy import (
    CadencePolicy,
    CadenceSignals,
    DeepTrigger,
    DeliberationMode,
    ReasoningDepth,
    action_mode,
    proposal_order_key,
)
from strongwiz.routing import RouterPolicy, evaluate_proposal, select_route
from tests.support import control, proposal, ref


def test_shadow_route_admits_without_manufacturing_authority() -> None:
    decision = evaluate_proposal(proposal(), control())
    assert decision.disposition is RouteDisposition.ADMIT
    assert decision.nonexecution_marker
    assert decision.authority == "NONE"
    authority = next(item for item in decision.guards if item.guard == "AUTHORITY")
    assert authority.status.value == "not_applicable"


def test_route_requests_exact_missing_witnesses() -> None:
    missing_evidence = ref("missing-evidence")
    missing_trace = ref("missing-trace")
    decision = evaluate_proposal(
        proposal(evidence_refs=(missing_evidence,), trace_refs=(missing_trace,)),
        control(evidence_refs=(), trace_refs=()),
    )
    assert decision.disposition is RouteDisposition.REQUEST_WITNESS
    assert decision.missing_witness_refs == tuple(sorted((missing_evidence, missing_trace)))


def test_stale_or_illegal_proposal_is_rejected() -> None:
    stale = proposal().model_copy(update={"observation_id": "old-observation"})
    assert evaluate_proposal(stale, control()).disposition is RouteDisposition.REJECT
    assert (
        evaluate_proposal(proposal(action="forbidden"), control()).disposition
        is RouteDisposition.REJECT
    )


def test_route_rejects_observation_content_or_goal_pair_splicing() -> None:
    altered_content = proposal().model_copy(update={"observation_ref": ref("altered")})
    assert evaluate_proposal(altered_content, control()).disposition is RouteDisposition.REJECT

    base = proposal()
    root_distinction = base.meaningful_distinction.model_copy(
        update={"parent_goal_id": "goal-root"}
    )
    spliced_goal = base.model_copy(
        update={"goal_id": "goal-root", "meaningful_distinction": root_distinction}
    )
    assert evaluate_proposal(spliced_goal, control()).disposition is RouteDisposition.REJECT

    with pytest.raises(ValueError, match="exact ordered pairs"):
        control().model_copy(update={"active_goal_refs": (proposal().goal_ref,)})


def test_budget_and_nonshadow_authority_hold() -> None:
    tiny = CostVector(environment_actions=0, compute_units=1)
    budget_hold = evaluate_proposal(proposal(), control(budget=tiny))
    assert budget_hold.disposition is RouteDisposition.HOLD
    authority_hold = evaluate_proposal(proposal(), control(shadow_only=False))
    assert authority_hold.disposition is RouteDisposition.HOLD
    grant_only = evaluate_proposal(
        proposal(), control(shadow_only=False, grant_ref=ref("external-grant"))
    )
    assert grant_only.disposition is RouteDisposition.HOLD
    supplied = evaluate_proposal(
        proposal(),
        control(
            shadow_only=False,
            grant_ref=ref("external-grant"),
            lab_ref=ref("lab-decision"),
            lab_status=BoundaryStatus.CLEAR,
        ),
    )
    assert supplied.disposition is RouteDisposition.ADMIT
    assert supplied.authority == "NONE"
    refused = evaluate_proposal(
        proposal(),
        control(
            shadow_only=False,
            grant_ref=ref("external-grant"),
            lab_ref=ref("lab-decision"),
            lab_status=BoundaryStatus.REFUSE,
        ),
    )
    assert refused.disposition is RouteDisposition.REJECT
    lab_hold = evaluate_proposal(
        proposal(),
        control(
            shadow_only=False,
            grant_ref=ref("external-grant"),
            lab_ref=ref("lab-decision"),
            lab_status=BoundaryStatus.HOLD,
        ),
    )
    assert lab_hold.disposition is RouteDisposition.HOLD


def test_reopening_requires_accepted_material_delta() -> None:
    delta = ref("delta")
    old_account = ref("old-account")
    candidate = proposal(prior_account_ref=old_account, material_delta_refs=(delta,))
    missing = evaluate_proposal(candidate, control())
    assert missing.disposition is RouteDisposition.REQUEST_WITNESS
    held = evaluate_proposal(candidate, control(delta_refs=(delta,)))
    assert held.disposition is RouteDisposition.HOLD
    reopened = evaluate_proposal(
        candidate, control(delta_refs=(delta,), account_refs=(old_account,))
    )
    assert reopened.disposition is RouteDisposition.REOPEN


def test_route_selection_switches_between_progress_and_information() -> None:
    progress = proposal(
        proposal_id="progress",
        progress_rank=1,
        information_rank=5,
        risk_rank=0,
    )
    probe = proposal(
        proposal_id="probe",
        progress_rank=4,
        information_rank=1,
        risk_rank=0,
    )
    assert select_route((progress, probe), control()).selected_proposal_id == "progress"
    assert (
        select_route((progress, probe), control(), prefer_information=True).selected_proposal_id
        == "probe"
    )
    assert proposal_order_key(progress, mode=DeliberationMode.EXECUTE) < proposal_order_key(
        probe, mode=DeliberationMode.EXECUTE
    )
    assert proposal_order_key(probe, mode=DeliberationMode.INVESTIGATE) < proposal_order_key(
        progress, mode=DeliberationMode.INVESTIGATE
    )


def test_two_speed_cadence_uses_hard_deep_triggers() -> None:
    policy = CadencePolicy(max_fast_streak=2)
    fast = policy.select(CadenceSignals())
    assert fast.depth is ReasoningDepth.FAST
    novelty = policy.select(CadenceSignals(structural_novelty=True))
    assert novelty.depth is ReasoningDepth.DEEP
    assert DeepTrigger.STRUCTURAL_NOVELTY in novelty.triggers
    streak = policy.select(CadenceSignals(fast_streak=2))
    assert DeepTrigger.MAX_FAST_STREAK in streak.triggers
    assert action_mode(
        credible_plan_supported=True, uncertainty_blocks_progress=False
    ).value == ("execute")
    assert action_mode(
        credible_plan_supported=False, uncertainty_blocks_progress=True
    ).value == ("investigate")


def test_no_proposals_is_an_honest_hold() -> None:
    decision = select_route((), control())
    assert decision.disposition is RouteDisposition.HOLD
    assert decision.selected_proposal_id is None


def test_multi_proposal_route_preserves_noneligible_precedence() -> None:
    missing = proposal(proposal_id="missing", evidence_refs=(ref("absent"),))
    rejected = proposal(proposal_id="rejected", action="forbidden")
    decision = select_route((rejected, missing), control())
    assert decision.disposition is RouteDisposition.REQUEST_WITNESS
    assert decision.missing_witness_refs == (ref("absent"),)


def test_missing_witness_cannot_be_admitted_when_requesting_is_disabled() -> None:
    missing = proposal(evidence_refs=(ref("absent"),))
    decision = evaluate_proposal(
        missing,
        control(evidence_refs=()),
        policy=RouterPolicy(request_missing_witness=False),
    )
    assert decision.disposition is RouteDisposition.HOLD
    assert decision.selected_proposal_id is None


def test_route_rejects_duplicate_proposal_identities() -> None:
    with pytest.raises(ValueError, match="unique"):
        select_route(
            (proposal(proposal_id="same"), proposal(proposal_id="same", action="open")),
            control(),
        )


def test_residual_and_exact_action_apertures_cannot_be_bypassed() -> None:
    residual = ref("residual")
    residual_candidate = proposal().model_copy(update={"residual_refs": (residual,)})
    missing = evaluate_proposal(residual_candidate, control())
    assert missing.disposition is RouteDisposition.REQUEST_WITNESS

    allowed = proposal()
    altered_action = allowed.action.model_copy(update={"parameters": {"target": "other"}})
    altered = allowed.model_copy(update={"action": altered_action})
    rejected = evaluate_proposal(
        altered,
        control(allowed_action_refs=(allowed.action.digest,)),
    )
    assert rejected.disposition is RouteDisposition.REJECT
