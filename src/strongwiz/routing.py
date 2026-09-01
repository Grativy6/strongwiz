"""Deterministic A0BK-inspired advisory routing."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from strongwiz.contracts import (
    BoundaryStatus,
    CandidateProposal,
    ContractModel,
    ControlSnapshot,
    GuardResult,
    GuardStatus,
    RouteDecision,
    RouteDisposition,
)


class RouterPolicy(ContractModel):
    policy_id: str = "strongwiz.router.v1"
    request_missing_witness: bool = True
    reopen_on_material_delta: bool = True
    limitations: tuple[str, ...] = Field(
        default=(
            "advisory route only",
            "supplied evidence and control state are not independently authenticated",
            "route does not grant permission or execute an action",
        )
    )


def _guard(
    name: str, status: GuardStatus, reason: str, refs: tuple[str, ...] = ()
) -> GuardResult:
    return GuardResult(guard=name, status=status, reason=reason, evidence_refs=refs)


def evaluate_proposal(
    proposal: CandidateProposal,
    control: ControlSnapshot,
    *,
    policy: RouterPolicy | None = None,
) -> RouteDecision:
    """Evaluate all hard guards without executing or repairing the proposal."""

    active_policy = policy or RouterPolicy()
    identity_ok = (
        proposal.observation_id == control.observation_id
        and proposal.observation_ref == control.observation_ref
        and proposal.scope_id == control.scope_id
    )
    identity = _guard(
        "IDENTITY",
        GuardStatus.PASS if identity_ok else GuardStatus.FAIL,
        "proposal binds the current observation and scope"
        if identity_ok
        else "proposal observation or scope is stale",
    )

    evidence_missing = tuple(
        sorted(set(proposal.evidence_refs) - set(control.available_evidence_refs))
    )
    delta_missing = tuple(
        sorted(set(proposal.material_delta_refs) - set(control.accepted_material_delta_refs))
    )
    witness_missing = tuple(sorted(set((*evidence_missing, *delta_missing))))
    witness = _guard(
        "WITNESS",
        GuardStatus.PASS if not witness_missing else GuardStatus.UNRESOLVED,
        "all proposal witnesses are in the control aperture"
        if not witness_missing
        else "one or more proposal witnesses are unavailable",
        tuple(ref for ref in proposal.evidence_refs if ref not in witness_missing),
    )

    scope_ok = control.contains_goal(proposal.goal_id, proposal.goal_ref)
    scope = _guard(
        "SCOPE",
        GuardStatus.PASS if scope_ok else GuardStatus.FAIL,
        "proposal serves an active scoped goal" if scope_ok else "proposal goal is not active",
    )

    missing_trace = tuple(sorted(set(proposal.trace_refs) - set(control.available_trace_refs)))
    missing_residual = tuple(
        sorted(set(proposal.residual_refs) - set(control.available_residual_refs))
    )
    trace = _guard(
        "TRACE",
        GuardStatus.PASS
        if not missing_trace and not missing_residual
        else GuardStatus.UNRESOLVED,
        "trace and residual dependencies are available"
        if not missing_trace and not missing_residual
        else "trace or residual dependency is unavailable",
        tuple(
            ref
            for ref in (*proposal.trace_refs, *proposal.residual_refs)
            if ref not in {*missing_trace, *missing_residual}
        ),
    )

    if control.shadow_only:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.NOT_APPLICABLE,
            "shadow route requests no external effect",
        )
    elif not control.execution_grant_ref:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.UNRESOLVED,
            "non-shadow consideration requires an external execution grant",
        )
    elif control.lab_boundary is None:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.UNRESOLVED,
            "non-shadow consideration requires a control-owned lab policy decision",
            (control.execution_grant_ref,),
        )
    elif (
        control.lab_boundary.grant_ref != control.execution_grant_ref
        or control.lab_boundary.proposal_ref != proposal.digest
        or control.lab_boundary.action_ref != proposal.action.digest
        or control.lab_boundary.observation_id != proposal.observation_id
        or control.lab_boundary.observation_ref != proposal.observation_ref
        or control.lab_boundary.scope_id != proposal.scope_id
    ):
        authority = _guard(
            "AUTHORITY",
            GuardStatus.FAIL,
            "the lab decision is not bound to this exact grant, proposal, action, and scope",
            (control.execution_grant_ref, control.lab_boundary.decision_ref),
        )
    elif control.lab_boundary.status is BoundaryStatus.HOLD:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.UNRESOLVED,
            "the control-owned lab policy decision holds the external effect",
            (control.execution_grant_ref, control.lab_boundary.decision_ref),
        )
    elif control.lab_boundary.status is BoundaryStatus.REFUSE:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.FAIL,
            "the control-owned lab policy decision refuses the external effect",
            (control.execution_grant_ref, control.lab_boundary.decision_ref),
        )
    elif control.lab_boundary.status is BoundaryStatus.CLEAR:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.PASS,
            "external grant and control-owned lab clearance are supplied "
            "but not authenticated here",
            (control.execution_grant_ref, control.lab_boundary.decision_ref),
        )
    else:
        authority = _guard(
            "AUTHORITY",
            GuardStatus.UNRESOLVED,
            "laboratory clearance lacks its decision receipt",
        )

    action_allowed = proposal.action.name in control.allowed_action_names and (
        not control.allowed_action_refs or proposal.action.digest in control.allowed_action_refs
    )
    consequence = _guard(
        "CONSEQUENCE",
        GuardStatus.PASS if action_allowed else GuardStatus.FAIL,
        "action is in the independently supplied action aperture"
        if action_allowed
        else "action is outside the supplied action aperture",
    )

    resource_ok = proposal.costs.fits_within(control.remaining_budget)
    resource = _guard(
        "RESOURCE",
        GuardStatus.PASS if resource_ok else GuardStatus.UNRESOLVED,
        "declared heterogeneous costs fit the remaining budget"
        if resource_ok
        else "at least one declared cost dimension exceeds its budget",
    )

    if proposal.prior_account_ref is None:
        reentry = _guard(
            "REENTRY", GuardStatus.NOT_APPLICABLE, "proposal does not request reopening"
        )
    elif proposal.prior_account_ref not in control.available_account_refs:
        reentry = _guard(
            "REENTRY",
            GuardStatus.UNRESOLVED,
            "prior account is outside the control-owned reopening aperture",
        )
    elif proposal.material_delta_refs and not delta_missing:
        reentry = _guard(
            "REENTRY",
            GuardStatus.PASS,
            "reopening names a prior account and accepted material delta",
            proposal.material_delta_refs,
        )
    else:
        reentry = _guard(
            "REENTRY",
            GuardStatus.UNRESOLVED,
            "reopening lacks an accepted material delta",
        )

    guards = (
        identity,
        witness,
        scope,
        trace,
        authority,
        consequence,
        resource,
        reentry,
    )
    hard_fail = any(
        guard.status is GuardStatus.FAIL for guard in (identity, scope, authority, consequence)
    )
    unresolved_witnesses = tuple(
        sorted(set((*witness_missing, *missing_trace, *missing_residual)))
    )
    if hard_fail:
        disposition = RouteDisposition.REJECT
        selected: str | None = None
    elif unresolved_witnesses:
        disposition = (
            RouteDisposition.REQUEST_WITNESS
            if active_policy.request_missing_witness
            else RouteDisposition.HOLD
        )
        selected = None
    elif any(
        guard.status is GuardStatus.UNRESOLVED for guard in (authority, resource, reentry)
    ):
        disposition = RouteDisposition.HOLD
        selected = None
    elif proposal.prior_account_ref is not None and active_policy.reopen_on_material_delta:
        disposition = RouteDisposition.REOPEN
        selected = proposal.proposal_id
    else:
        disposition = RouteDisposition.ADMIT
        selected = proposal.proposal_id
    return RouteDecision(
        control_ref=control.digest,
        disposition=disposition,
        selected_proposal_id=selected,
        selected_proposal_ref=proposal.digest if selected is not None else None,
        guards=guards,
        missing_witness_refs=unresolved_witnesses,
        limitations=active_policy.limitations,
    )


def select_route(
    proposals: Sequence[CandidateProposal],
    control: ControlSnapshot,
    *,
    policy: RouterPolicy | None = None,
    prefer_information: bool = False,
) -> RouteDecision:
    """Select deterministically among guard-eligible proposals.

    Numerical fields remain ranks, not probabilities or universal utilities.
    Hard guards are evaluated before this comparison and cannot be averaged away.
    """

    if not proposals:
        return RouteDecision(
            control_ref=control.digest,
            disposition=RouteDisposition.HOLD,
            selected_proposal_id=None,
            selected_proposal_ref=None,
            guards=(),
            limitations=("no proposals were supplied",),
        )
    proposal_ids = tuple(proposal.proposal_id for proposal in proposals)
    if len(set(proposal_ids)) != len(proposal_ids):
        raise ValueError("proposal identities must be unique within one decision batch")
    evaluated = [
        (proposal, evaluate_proposal(proposal, control, policy=policy))
        for proposal in proposals
    ]
    eligible = [
        (proposal, decision)
        for proposal, decision in evaluated
        if decision.disposition in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}
    ]
    if eligible:

        def key(item: tuple[CandidateProposal, RouteDecision]) -> tuple[object, ...]:
            proposal, decision = item
            primary = (
                proposal.information_gain_rank
                if prefer_information
                else proposal.expected_progress_rank
            )
            secondary = (
                proposal.expected_progress_rank
                if prefer_information
                else proposal.information_gain_rank
            )
            return (
                0 if decision.disposition is RouteDisposition.REOPEN else 1,
                primary,
                proposal.risk_rank,
                0 if proposal.reversible else 1,
                proposal.costs.environment_actions,
                proposal.costs.compute_units,
                secondary,
                proposal.proposal_id,
            )

        return min(eligible, key=key)[1]

    precedence = {
        RouteDisposition.REQUEST_WITNESS: 0,
        RouteDisposition.HOLD: 1,
        RouteDisposition.REJECT: 2,
    }
    proposal, decision = min(
        evaluated,
        key=lambda item: (precedence[item[1].disposition], item[0].proposal_id),
    )
    del proposal
    return decision
