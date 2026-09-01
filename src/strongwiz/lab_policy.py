"""PEA Core, PECAN, and SEED control-owned laboratory interfaces.

These types support review and crossing discipline.  They do not create law,
ethics, consent, standing, permission, authorization, or human responsibility.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import (
    BoundaryStatus,
    ContractModel,
    LabBoundaryBinding,
    NonNegativeInt,
)

PEA_CORE_VERSION = "1.1.3"
PECAN_VERSION = "1.0.4"
SEED_VERSION = "0.3"


class ReviewStatus(StrEnum):
    SUPPLIED = "supplied"
    REFUSED = "refused"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class CrossingStage(StrEnum):
    DESCRIPTION = "description"
    RECOMMENDATION = "recommendation"
    PERMISSION = "permission"
    AUTHORIZATION = "authorization"


class ReleaseClaimStatus(StrEnum):
    OBSERVATION = "observation"
    MEASUREMENT = "measurement"
    INTERPRETATION = "interpretation"
    CANDIDATE = "candidate"
    BOUNDED_RECOMMENDATION = "bounded_recommendation"
    VERIFIED_RESULT = "verified_result"
    BLOCKED = "blocked"
    FAILURE = "failure"


LabGateStatus = BoundaryStatus


class PEAReview(ContractModel):
    schema_id: str = Field(default="strongwiz.pea-review.v1", alias="schema")
    source_version: str = PEA_CORE_VERSION
    boundary_context_ref: str
    external_grant_ref: str
    consent: ReviewStatus
    standing: ReviewStatus
    privacy: ReviewStatus
    reversibility: ReviewStatus
    remedy: ReviewStatus
    contestability: ReviewStatus
    refusal: ReviewStatus
    human_responsibility_ref: str
    open_concerns: tuple[str, ...] = ()
    nonexecuting: bool = True
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_review(self) -> PEAReview:
        if self.source_version != PEA_CORE_VERSION:
            raise ValueError("unsupported PEA Core profile")
        if not (
            self.boundary_context_ref
            and self.external_grant_ref
            and self.human_responsibility_ref
        ):
            raise ValueError("PEA review requires external grant and human responsibility refs")
        if not self.nonexecuting or self.authority != "NONE":
            raise ValueError("PEA review is advisory and non-self-executing")
        return self

    @property
    def protective_controls_satisfied(self) -> bool:
        statuses = (
            self.consent,
            self.standing,
            self.privacy,
            self.reversibility,
            self.remedy,
            self.contestability,
            self.refusal,
        )
        return not self.open_concerns and all(
            status in {ReviewStatus.SUPPLIED, ReviewStatus.NOT_APPLICABLE}
            for status in statuses
        )


class ConsequentialCrossing(ContractModel):
    schema_id: str = Field(default="strongwiz.pecan-crossing.v1", alias="schema")
    source_version: str = PECAN_VERSION
    boundary_context_ref: str
    subject_ref: str
    description_ref: str
    recommendation_ref: str | None = None
    permission_ref: str | None = None
    authorization_ref: str | None = None
    current_stage: CrossingStage
    externally_supplied_authorization: bool = False

    @model_validator(mode="after")
    def validate_ladder(self) -> ConsequentialCrossing:
        if self.source_version != PECAN_VERSION:
            raise ValueError("unsupported PECAN profile")
        if not self.boundary_context_ref or not self.subject_ref or not self.description_ref:
            raise ValueError("crossing requires subject and description")
        required = {
            CrossingStage.DESCRIPTION: (),
            CrossingStage.RECOMMENDATION: (self.recommendation_ref,),
            CrossingStage.PERMISSION: (self.recommendation_ref, self.permission_ref),
            CrossingStage.AUTHORIZATION: (
                self.recommendation_ref,
                self.permission_ref,
                self.authorization_ref,
            ),
        }[self.current_stage]
        if not all(value is not None and value.strip() for value in required):
            raise ValueError("crossing stage cannot skip an earlier supplied distinction")
        if self.current_stage is CrossingStage.AUTHORIZATION:
            if not self.externally_supplied_authorization:
                raise ValueError("Strongwiz cannot manufacture authorization")
        elif self.externally_supplied_authorization:
            raise ValueError("authorization flag is valid only at the authorization stage")
        return self

    @property
    def may_cross_external_boundary(self) -> bool:
        return (
            self.current_stage is CrossingStage.AUTHORIZATION
            and self.externally_supplied_authorization
        )


class SEEDReleaseReview(ContractModel):
    schema_id: str = Field(default="strongwiz.seed-release.v1", alias="schema")
    source_version: str = SEED_VERSION
    boundary_context_ref: str
    output_ref: str
    chosen_goal_id: str
    chosen_goal_ref: str
    claim_status: ReleaseClaimStatus
    claim_ceiling: str
    uncertainty_status: ReviewStatus
    uncertainty_notes: tuple[str, ...]
    authority_status: ReviewStatus
    authority_limits: tuple[str, ...]
    privacy_status: ReviewStatus
    privacy_notes: tuple[str, ...]
    correction_path: str
    reopening_condition: str
    natural_stop: str
    attention_units_requested: NonNegativeInt = 0
    preserves_user_agency: bool
    manufactured_dependency_detected: bool = False
    model_personhood_claim_detected: bool = False

    @model_validator(mode="after")
    def validate_release(self) -> SEEDReleaseReview:
        if self.source_version != SEED_VERSION:
            raise ValueError("unsupported SEED profile")
        required = (
            self.output_ref,
            self.boundary_context_ref,
            self.chosen_goal_id,
            self.chosen_goal_ref,
            self.claim_ceiling,
            self.correction_path,
            self.reopening_condition,
            self.natural_stop,
        )
        if not all(value.strip() for value in required):
            raise ValueError("SEED release review requires claim and continuation boundaries")
        burdens = (
            (self.uncertainty_status, self.uncertainty_notes, "uncertainty"),
            (self.authority_status, self.authority_limits, "authority"),
            (self.privacy_status, self.privacy_notes, "privacy"),
        )
        for status, notes, name in burdens:
            if status is ReviewStatus.SUPPLIED and not notes:
                raise ValueError(f"SEED {name} burden marked supplied requires content")
        return self

    @property
    def releasable(self) -> bool:
        return (
            self.preserves_user_agency
            and all(
                status in {ReviewStatus.SUPPLIED, ReviewStatus.NOT_APPLICABLE}
                for status in (
                    self.uncertainty_status,
                    self.authority_status,
                    self.privacy_status,
                )
            )
            and not self.manufactured_dependency_detected
            and not self.model_personhood_claim_detected
        )


class LabBoundaryContext(ContractModel):
    """Exact control-plane context to which all three reviews must bind."""

    grant_ref: str
    task_id: str
    goal_id: str
    goal_ref: str
    scope_id: str
    observation_id: str
    observation_ref: str
    proposal_ref: str
    action_ref: str
    output_destination_ref: str
    attention_budget: NonNegativeInt
    release_output_ref: str | None = None

    @model_validator(mode="after")
    def validate_context(self) -> LabBoundaryContext:
        required = (
            self.grant_ref,
            self.task_id,
            self.goal_id,
            self.goal_ref,
            self.scope_id,
            self.observation_id,
            self.observation_ref,
            self.proposal_ref,
            self.action_ref,
            self.output_destination_ref,
        )
        if not all(value.strip() for value in required):
            raise ValueError("lab context must bind grant, task, goal, and exact effect")
        return self


class LabPolicyDecision(ContractModel):
    """Control-owned preflight result; it grants no authority and executes nothing."""

    schema_id: str = Field(default="strongwiz.lab-policy-decision.v1", alias="schema")
    context: LabBoundaryContext
    pea_review_ref: str | None
    pecan_crossing_ref: str | None
    seed_release_ref: str | None
    external_effect_status: BoundaryStatus
    release_status: BoundaryStatus
    blockers: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "profiles are bounded control interfaces, not legal or ethical authorities",
        "supplied references are not independently authenticated by this decision",
    )
    nonexecuting: bool = True
    authority: str = "NONE"
    effect: str = "NONE"

    @model_validator(mode="after")
    def validate_nonexecution(self) -> LabPolicyDecision:
        if not self.nonexecuting or self.authority != "NONE" or self.effect != "NONE":
            raise ValueError("lab policy decisions are advisory and non-self-executing")
        return self

    @property
    def clears_requested_boundaries(self) -> bool:
        return all(
            status in {BoundaryStatus.CLEAR, BoundaryStatus.NOT_REQUESTED}
            for status in (self.external_effect_status, self.release_status)
        )

    @property
    def external_effect_binding(self) -> LabBoundaryBinding | None:
        if self.external_effect_status is BoundaryStatus.NOT_REQUESTED:
            return None
        return LabBoundaryBinding(
            decision_ref=self.digest,
            grant_ref=self.context.grant_ref,
            proposal_ref=self.context.proposal_ref,
            action_ref=self.context.action_ref,
            observation_id=self.context.observation_id,
            observation_ref=self.context.observation_ref,
            scope_id=self.context.scope_id,
            status=self.external_effect_status,
        )


def evaluate_lab_rules(
    *,
    context: LabBoundaryContext,
    pea_review: PEAReview | None,
    crossing: ConsequentialCrossing | None,
    seed_release: SEEDReleaseReview | None,
    external_effect_requested: bool,
    release_requested: bool,
) -> LabPolicyDecision:
    """Apply PEA, PECAN, and SEED without allowing one layer to imply another."""

    blockers: list[str] = []
    pea_statuses = (
        ()
        if pea_review is None
        else (
            pea_review.consent,
            pea_review.standing,
            pea_review.privacy,
            pea_review.reversibility,
            pea_review.remedy,
            pea_review.contestability,
            pea_review.refusal,
        )
    )
    pea_refused = any(status is ReviewStatus.REFUSED for status in pea_statuses)
    pea_unresolved = (
        pea_review is None
        or bool(pea_review.open_concerns)
        or any(status is ReviewStatus.UNRESOLVED for status in pea_statuses)
    )
    pea_grant_mismatch = (
        pea_review is not None and pea_review.external_grant_ref != context.grant_ref
    )
    pea_context_mismatch = (
        pea_review is not None and pea_review.boundary_context_ref != context.digest
    )

    def pea_gate(boundary: str) -> BoundaryStatus:
        if pea_grant_mismatch:
            blockers.append(f"PEA_GRANT_BINDING_MISMATCH:{boundary}")
            return BoundaryStatus.REFUSE
        if pea_context_mismatch:
            blockers.append(f"PEA_CONTEXT_BINDING_MISMATCH:{boundary}")
            return BoundaryStatus.REFUSE
        if pea_refused:
            blockers.append(f"PEA_REFUSED:{boundary}")
            return BoundaryStatus.REFUSE
        if pea_unresolved:
            blockers.append(f"PEA_UNRESOLVED:{boundary}")
            return BoundaryStatus.HOLD
        return BoundaryStatus.CLEAR

    if not external_effect_requested:
        effect_status = BoundaryStatus.NOT_REQUESTED
    else:
        effect_status = pea_gate("external_effect")
        if effect_status is BoundaryStatus.CLEAR:
            if crossing is not None and (
                crossing.subject_ref != context.action_ref
                or crossing.boundary_context_ref != context.digest
            ):
                blockers.append("PECAN_ACTION_BINDING_MISMATCH")
                effect_status = BoundaryStatus.REFUSE
            elif crossing is None or not crossing.may_cross_external_boundary:
                blockers.append("PECAN_EXTERNAL_AUTHORIZATION_MISSING")
                effect_status = BoundaryStatus.HOLD

    if not release_requested:
        release_status = BoundaryStatus.NOT_REQUESTED
    else:
        release_status = pea_gate("release")
        if release_status is BoundaryStatus.CLEAR:
            if seed_release is None:
                blockers.append("SEED_RELEASE_REVIEW_MISSING")
                release_status = BoundaryStatus.HOLD
            elif (
                seed_release.chosen_goal_id != context.goal_id
                or seed_release.chosen_goal_ref != context.goal_ref
                or seed_release.boundary_context_ref != context.digest
                or context.release_output_ref is None
                or seed_release.output_ref != context.release_output_ref
            ):
                blockers.append("SEED_RELEASE_BINDING_MISMATCH")
                release_status = BoundaryStatus.REFUSE
            elif seed_release.attention_units_requested > context.attention_budget:
                blockers.append("SEED_ATTENTION_BUDGET_EXCEEDED")
                release_status = BoundaryStatus.REFUSE
            elif not seed_release.releasable:
                blockers.append("SEED_RELEASE_REFUSED")
                release_status = BoundaryStatus.REFUSE

    return LabPolicyDecision(
        context=context,
        pea_review_ref=None if pea_review is None else pea_review.digest,
        pecan_crossing_ref=None if crossing is None else crossing.digest,
        seed_release_ref=None if seed_release is None else seed_release.digest,
        external_effect_status=effect_status,
        release_status=release_status,
        blockers=tuple(blockers),
    )
