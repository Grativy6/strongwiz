"""The small, versioned contract between models, Strongwiz, and domains.

Models propose.  Control state is supplied independently.  Strongwiz returns
advisory decisions.  No object in this module executes an external action.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strongwiz.canonical import ImmutableJSONObject, content_hash

CONTRACT_SCHEMA = "strongwiz.contract.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ContractModel(BaseModel):
    """Closed, immutable base for every cross-boundary contract value."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        validate_default=True,
    )

    @property
    def digest(self) -> str:
        return content_hash(self)

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        """Return a fully revalidated copy; Pydantic's unchecked update is unsafe here."""

        del deep
        values = self.model_dump(mode="python", by_alias=False)
        if update:
            values.update(update)
        return type(self).model_validate(values)


class DecisionEffect(StrEnum):
    PLAN = "plan"
    RISK = "risk"
    CANDIDATE_CHOICE = "candidate_choice"
    EXPERIMENT_CHOICE = "experiment_choice"
    RESOURCE = "resource"
    ACCESS = "access"
    HAZARD = "hazard"
    MOVEMENT = "movement"
    PROGRESS = "progress"
    OUTPUT = "output"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PARKED = "parked"
    SATISFIED = "satisfied"
    ABANDONED = "abandoned"
    REOPENED = "reopened"


class HypothesisStatus(StrEnum):
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    NARROWED = "narrowed"
    CONTRADICTED = "contradicted"
    PARKED = "parked"
    SUPERSEDED = "superseded"


class RouteDisposition(StrEnum):
    ADMIT = "admit"
    HOLD = "hold"
    REQUEST_WITNESS = "request_witness"
    REJECT = "reject"
    REOPEN = "reopen"


class GuardStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class BoundaryStatus(StrEnum):
    CLEAR = "clear"
    HOLD = "hold"
    REFUSE = "refuse"
    NOT_REQUESTED = "not_requested"


class DeliberationMode(StrEnum):
    EXECUTE = "execute"
    INVESTIGATE = "investigate"


class EvidenceRef(ContractModel):
    kind: str
    sha256: str = Field(alias="digest")
    locator: str | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence kind must be non-empty")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("evidence digest must be lowercase SHA-256")
        return value


class Observation(ContractModel):
    schema_id: str = Field(default=CONTRACT_SCHEMA, alias="schema")
    observation_id: str
    domain: str
    scope_id: str
    epoch: NonNegativeInt
    payload_ref: EvidenceRef
    summary: str
    available_action_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_identity(self) -> Observation:
        if self.schema_id != CONTRACT_SCHEMA:
            raise ValueError("unsupported observation contract")
        if not all((self.observation_id, self.domain, self.scope_id, self.summary)):
            raise ValueError("observation identity and summary must be non-empty")
        if len(set(self.available_action_names)) != len(self.available_action_names):
            raise ValueError("available action names must be unique")
        return self


class Goal(ContractModel):
    goal_id: str
    statement: str
    scope_id: str
    parent_goal_id: str | None = None
    governing_goal_id: str | None = None
    motivating_uncertainty: str | None = None
    decision_that_could_change: str | None = None
    smallest_sufficient_test: str | None = None
    success_condition: str
    abandonment_condition: str | None = None
    reopening_condition: str | None = None
    status: GoalStatus = GoalStatus.ACTIVE

    @model_validator(mode="after")
    def validate_goal(self) -> Goal:
        required = (self.goal_id, self.statement, self.scope_id, self.success_condition)
        if not all(value.strip() for value in required):
            raise ValueError(
                "goal identity, statement, scope, and success condition are required"
            )
        if self.parent_goal_id is not None:
            instrumental = (
                self.governing_goal_id,
                self.motivating_uncertainty,
                self.decision_that_could_change,
                self.smallest_sufficient_test,
                self.reopening_condition,
            )
            if not all(value is not None and value.strip() for value in instrumental):
                raise ValueError("subgoals require a complete relevance and reopening chain")
        return self


class Distinction(ContractModel):
    distinction_id: str
    statement: str
    scope_id: str
    parent_goal_id: str
    governing_goal_id: str
    candidate_resolutions: tuple[str, ...]
    competing_predictions: tuple[str, ...]
    decision_effects: tuple[DecisionEffect, ...]
    decision_that_could_change: str
    relevance_summary: str
    smallest_discriminating_test: str | None = None
    reopening_condition: str
    parked: bool = False

    @model_validator(mode="after")
    def validate_relevance_chain(self) -> Distinction:
        text = (
            self.distinction_id,
            self.statement,
            self.scope_id,
            self.parent_goal_id,
            self.governing_goal_id,
            self.decision_that_could_change,
            self.relevance_summary,
            self.reopening_condition,
        )
        if not all(value.strip() for value in text):
            raise ValueError("distinction relevance chain must be complete")
        if len(self.candidate_resolutions) < 2 or len(self.competing_predictions) < 2:
            raise ValueError("a distinction requires at least two resolutions and predictions")
        if not self.decision_effects:
            raise ValueError("a meaningful distinction must name a decision effect")
        return self


class ActionSpec(ContractModel):
    name: str
    parameters: ImmutableJSONObject = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("action name must be non-empty")
        return value


class Prediction(ContractModel):
    prediction_id: str
    hypothesis_refs: tuple[str, ...]
    expected_consequences: tuple[str, ...]
    falsified_by: tuple[str, ...]
    alternatives: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_prediction(self) -> Prediction:
        if not self.prediction_id or not self.expected_consequences or not self.falsified_by:
            raise ValueError("prediction identity, consequences, and falsifiers are required")
        return self


class Hypothesis(ContractModel):
    hypothesis_id: str
    claim: str
    scope_id: str
    components: tuple[str, ...]
    status: HypothesisStatus = HypothesisStatus.CANDIDATE
    evidence_refs: tuple[str, ...] = ()
    conflicting_refs: tuple[str, ...] = ()
    parent_hypothesis_id: str | None = None
    revision_reason: str | None = None

    @model_validator(mode="after")
    def validate_hypothesis(self) -> Hypothesis:
        if not self.hypothesis_id or not self.claim or not self.scope_id or not self.components:
            raise ValueError("hypothesis identity, claim, scope, and components are required")
        if self.parent_hypothesis_id is not None and not self.revision_reason:
            raise ValueError("a hypothesis revision requires its reason")
        return self


class CostVector(ContractModel):
    """Heterogeneous costs; Strongwiz deliberately defines no universal scalar."""

    environment_actions: NonNegativeInt = 0
    irreversible_actions: NonNegativeInt = 0
    life_risk_units: NonNegativeInt = 0
    wall_clock_ms: NonNegativeInt = 0
    compute_units: NonNegativeInt = 0
    memory_bytes: NonNegativeInt = 0
    context_tokens: NonNegativeInt = 0
    acquisition_units: NonNegativeInt = 0
    validation_units: NonNegativeInt = 0
    transport_units: NonNegativeInt = 0
    invalidation_units: NonNegativeInt = 0
    output_units: NonNegativeInt = 0

    def fits_within(self, budget: CostVector) -> bool:
        return all(
            getattr(self, name) <= getattr(budget, name) for name in type(self).model_fields
        )

    def __add__(self, other: CostVector) -> CostVector:
        values = {
            name: getattr(self, name) + getattr(other, name) for name in type(self).model_fields
        }
        return CostVector.model_validate(values)

    def subtract_floor_zero(self, other: CostVector) -> CostVector:
        values = {
            name: max(0, getattr(self, name) - getattr(other, name))
            for name in type(self).model_fields
        }
        return CostVector.model_validate(values)


class CandidateProposal(ContractModel):
    schema_id: str = Field(default=CONTRACT_SCHEMA, alias="schema")
    proposal_id: str
    model_driver_id: str
    observation_id: str
    observation_ref: str
    scope_id: str
    goal_id: str
    goal_ref: str
    action: ActionSpec
    meaningful_distinction: Distinction
    prediction: Prediction
    decision_effects: tuple[DecisionEffect, ...]
    evidence_refs: tuple[str, ...]
    trace_refs: tuple[str, ...] = ()
    residual_refs: tuple[str, ...] = ()
    material_delta_refs: tuple[str, ...] = ()
    prior_account_ref: str | None = None
    concise_rationale: str
    reversible: bool
    expected_progress_rank: PositiveInt
    information_gain_rank: PositiveInt
    risk_rank: NonNegativeInt
    costs: CostVector = Field(default_factory=CostVector)

    @model_validator(mode="after")
    def validate_proposal(self) -> CandidateProposal:
        if self.schema_id != CONTRACT_SCHEMA:
            raise ValueError("unsupported proposal contract")
        if not all(
            (
                self.proposal_id,
                self.model_driver_id,
                self.observation_id,
                self.observation_ref,
                self.scope_id,
                self.goal_id,
                self.goal_ref,
                self.concise_rationale,
            )
        ):
            raise ValueError("proposal bindings and concise rationale are required")
        if self.meaningful_distinction.parent_goal_id != self.goal_id:
            raise ValueError("distinction parent must be the proposal goal")
        if set(self.decision_effects) != set(self.meaningful_distinction.decision_effects):
            raise ValueError("proposal and distinction decision effects disagree")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("proposal evidence references must be unique")
        return self


class LabBoundaryBinding(ContractModel):
    """Control-owned binding from a lab decision to one exact proposed effect."""

    decision_ref: str
    grant_ref: str
    proposal_ref: str
    action_ref: str
    observation_id: str
    observation_ref: str
    scope_id: str
    status: BoundaryStatus

    @model_validator(mode="after")
    def validate_binding(self) -> LabBoundaryBinding:
        required = (
            self.decision_ref,
            self.grant_ref,
            self.proposal_ref,
            self.action_ref,
            self.observation_id,
            self.observation_ref,
            self.scope_id,
        )
        if not all(value.strip() for value in required):
            raise ValueError("lab boundary binding must identify the exact proposed effect")
        if self.status is BoundaryStatus.NOT_REQUESTED:
            raise ValueError("an effect binding cannot be marked not requested")
        return self


class ControlSnapshot(ContractModel):
    schema_id: str = Field(default=CONTRACT_SCHEMA, alias="schema")
    account_id: str
    account_version: NonNegativeInt
    observation_id: str
    observation_ref: str
    scope_id: str
    active_goal_ids: tuple[str, ...]
    active_goal_refs: tuple[str, ...]
    available_evidence_refs: tuple[str, ...]
    available_trace_refs: tuple[str, ...] = ()
    available_residual_refs: tuple[str, ...] = ()
    available_account_refs: tuple[str, ...] = ()
    accepted_material_delta_refs: tuple[str, ...] = ()
    allowed_action_names: tuple[str, ...]
    allowed_action_refs: tuple[str, ...] = ()
    remaining_budget: CostVector
    lab_boundary: LabBoundaryBinding | None = None
    execution_grant_ref: str | None = None
    serial_token: str
    shadow_only: bool = True

    @model_validator(mode="after")
    def validate_control(self) -> ControlSnapshot:
        if self.schema_id != CONTRACT_SCHEMA:
            raise ValueError("unsupported control contract")
        if not all(
            (
                self.account_id,
                self.observation_id,
                self.observation_ref,
                self.scope_id,
                self.serial_token,
            )
        ):
            raise ValueError("control identity and serial token are required")
        if len(self.active_goal_ids) != len(self.active_goal_refs):
            raise ValueError("control goal IDs and refs must form exact ordered pairs")
        for values in (
            self.active_goal_ids,
            self.active_goal_refs,
            self.available_evidence_refs,
            self.available_trace_refs,
            self.available_residual_refs,
            self.available_account_refs,
            self.accepted_material_delta_refs,
            self.allowed_action_names,
            self.allowed_action_refs,
        ):
            if len(set(values)) != len(values):
                raise ValueError("control apertures must contain unique references")
        if self.shadow_only and self.lab_boundary is not None:
            raise ValueError("shadow-only control cannot claim an external effect clearance")
        return self

    def contains_goal(self, goal_id: str, goal_ref: str) -> bool:
        return (goal_id, goal_ref) in tuple(
            zip(self.active_goal_ids, self.active_goal_refs, strict=True)
        )


class ReasoningRequest(ContractModel):
    schema_id: str = Field(default=CONTRACT_SCHEMA, alias="schema")
    observation: Observation
    governing_goal: Goal
    scoped_goal: Goal
    active_distinctions: tuple[Distinction, ...] = ()
    retained_fact_refs: tuple[str, ...] = ()
    feedback_trace_ref: str | None = None

    @model_validator(mode="after")
    def validate_request(self) -> ReasoningRequest:
        if self.schema_id != CONTRACT_SCHEMA:
            raise ValueError("unsupported reasoning request")
        if self.scoped_goal.scope_id != self.observation.scope_id:
            raise ValueError("scoped goal must bind the current observation scope")
        if (
            self.scoped_goal.goal_id != self.governing_goal.goal_id
            and self.scoped_goal.governing_goal_id != self.governing_goal.goal_id
        ):
            raise ValueError("scoped goal must link to the governing goal")
        return self


class Outcome(ContractModel):
    outcome_id: str
    observation_before_id: str
    observation_before_ref: str
    observation_after_id: str
    observation_after_ref: str
    action: ActionSpec
    observed_consequences: tuple[str, ...]
    state_label: str
    evidence_refs: tuple[str, ...]
    terminal: bool = False

    @model_validator(mode="after")
    def validate_outcome(self) -> Outcome:
        required = (
            self.outcome_id,
            self.observation_before_id,
            self.observation_before_ref,
            self.observation_after_id,
            self.observation_after_ref,
            self.state_label,
        )
        if not all(value.strip() for value in required):
            raise ValueError("outcome must bind both exact observations and its state label")
        return self


class GuardResult(ContractModel):
    guard: str
    status: GuardStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()


class RouteDecision(ContractModel):
    schema_id: str = Field(default=CONTRACT_SCHEMA, alias="schema")
    control_ref: str
    disposition: RouteDisposition
    selected_proposal_id: str | None
    selected_proposal_ref: str | None
    guards: tuple[GuardResult, ...]
    missing_witness_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    nonexecution_marker: bool = True
    authority: str = "NONE"
    effect: str = "NONE"

    @model_validator(mode="after")
    def validate_nonexecution(self) -> RouteDecision:
        if self.schema_id != CONTRACT_SCHEMA:
            raise ValueError("unsupported route decision")
        if not self.control_ref.strip():
            raise ValueError("route decision must bind the exact control snapshot")
        if not self.nonexecution_marker or self.authority != "NONE" or self.effect != "NONE":
            raise ValueError("kernel routes are advisory and nonexecuting")
        if self.disposition in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}:
            if self.selected_proposal_id is None or self.selected_proposal_ref is None:
                raise ValueError("admit/reopen must bind the selected proposal content")
        elif self.selected_proposal_id is not None or self.selected_proposal_ref is not None:
            raise ValueError("nonselecting routes cannot identify a proposal")
        return self


def contract_json_schema() -> dict[str, Any]:
    """Return the driver-facing request schema for discovery tooling."""

    return ReasoningRequest.model_json_schema()


def contract_schema_bundle() -> dict[str, Any]:
    """Return every declared cross-boundary schema under one versioned index."""

    return {
        "contract_version": CONTRACT_SCHEMA,
        "schemas": {
            "candidate_proposal": CandidateProposal.model_json_schema(),
            "control_snapshot": ControlSnapshot.model_json_schema(),
            "observation": Observation.model_json_schema(),
            "outcome": Outcome.model_json_schema(),
            "reasoning_request": ReasoningRequest.model_json_schema(),
            "route_decision": RouteDecision.model_json_schema(),
        },
    }
