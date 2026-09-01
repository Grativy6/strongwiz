"""Control-owned bridge from an advisory route to a bound one-use permit."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.authority import (
    DecisionPermit,
    GrantRegistry,
    ReleaseReceipt,
    ReleaseStatus,
)
from strongwiz.contracts import (
    BoundaryStatus,
    CandidateProposal,
    ContractModel,
    ControlSnapshot,
    DecisionEffect,
    RouteDecision,
    RouteDisposition,
)
from strongwiz.drivers import ActionExecutor, ExecutionCommand, ExecutorObservation
from strongwiz.lab_policy import (
    ConsequentialCrossing,
    LabPolicyDecision,
    PEAReview,
    SEEDReleaseReview,
    evaluate_lab_rules,
)
from strongwiz.routing import RouterPolicy, evaluate_proposal

_EXECUTION_RESULT_ISSUER = object()


class OrchestrationError(ValueError):
    pass


class ExecutionAdmission(ContractModel):
    schema_id: str = Field(default="strongwiz.execution-admission.v1", alias="schema")
    route_ref: str
    control_ref: str
    lab_decision_ref: str
    grant_ref: str
    proposal_ref: str
    action_ref: str
    action_name: str
    executor_id: str
    executor_version: str
    executor_artifact_ref: str
    observation_id: str
    observation_ref: str
    scope_id: str
    invocation_id: str
    nonexecuting: bool = True
    effect: str = "NONE"

    @model_validator(mode="after")
    def validate_admission(self) -> ExecutionAdmission:
        required = (
            self.route_ref,
            self.control_ref,
            self.lab_decision_ref,
            self.grant_ref,
            self.proposal_ref,
            self.action_ref,
            self.action_name,
            self.executor_id,
            self.executor_version,
            self.executor_artifact_ref,
            self.observation_id,
            self.observation_ref,
            self.scope_id,
            self.invocation_id,
        )
        if not all(value.strip() for value in required):
            raise ValueError("execution admission must bind the complete proposed effect")
        if not self.nonexecuting or self.effect != "NONE":
            raise ValueError("execution admission records a handoff; it executes nothing")
        return self


class ExecutionDisposition(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED_INFRASTRUCTURE = "failed_infrastructure"
    UNKNOWN_EFFECT = "unknown_effect"


class ExecutionAttemptReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.execution-attempt.v1", alias="schema")
    admission_ref: str
    release_ref: str
    executor_id: str
    executor_version: str
    executor_artifact_ref: str
    proposal_ref: str
    action_ref: str
    idempotency_key: str
    disposition: ExecutionDisposition
    result_evidence_ref: str | None = None
    failure_category: str | None = None

    @model_validator(mode="after")
    def validate_attempt(self) -> ExecutionAttemptReceipt:
        required = (
            self.admission_ref,
            self.release_ref,
            self.executor_id,
            self.executor_version,
            self.executor_artifact_ref,
            self.proposal_ref,
            self.action_ref,
            self.idempotency_key,
        )
        if not all(value.strip() for value in required):
            raise ValueError("execution attempt must retain every admitted identity")
        if self.disposition is ExecutionDisposition.COMPLETED:
            if self.result_evidence_ref is None or self.failure_category is not None:
                raise ValueError("completed execution requires evidence and no failure")
        elif not (self.failure_category and self.failure_category.strip()):
            raise ValueError("non-completed execution requires a failure category")
        return self


class ExecutionCallResult:
    """Opaque coordinator-issued result; raw data stays outside serializable receipts."""

    __slots__ = ("_issuer", "admission", "attempt", "observation", "release")

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("execution call result bindings are immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        issuer: object,
        admission: ExecutionAdmission,
        release: ReleaseReceipt,
        attempt: ExecutionAttemptReceipt,
        observation: ExecutorObservation | None,
    ) -> None:
        self._issuer = issuer
        self.admission = admission
        self.release = release
        self.attempt = attempt
        self.observation = observation

    def issued_by(self, issuer: object) -> bool:
        return self._issuer is issuer

    @property
    def coordinator_issued(self) -> bool:
        return self._issuer is _EXECUTION_RESULT_ISSUER

    def __getstate__(self) -> object:
        raise TypeError("execution call results are intentionally nonserializable")


class ExecutionCoordinator:
    """Recheck every identity before issuing and consuming an execution permit."""

    def __init__(
        self,
        grants: GrantRegistry,
        executor: ActionExecutor,
        *,
        router_policy: RouterPolicy | None = None,
    ) -> None:
        self._grants = grants
        self._executor = executor
        self._router_policy = router_policy or RouterPolicy()

    def _result(
        self,
        admission: ExecutionAdmission,
        release: ReleaseReceipt,
        attempt: ExecutionAttemptReceipt,
        observation: ExecutorObservation | None,
    ) -> ExecutionCallResult:
        return ExecutionCallResult(
            issuer=_EXECUTION_RESULT_ISSUER,
            admission=admission,
            release=release,
            attempt=attempt,
            observation=observation,
        )

    def begin(
        self,
        *,
        proposal: CandidateProposal,
        route: RouteDecision,
        control: ControlSnapshot,
        lab_decision: LabPolicyDecision,
        pea_review: PEAReview | None,
        crossing: ConsequentialCrossing | None,
        seed_release: SEEDReleaseReview | None,
        invocation_id: str,
        boundary: int,
    ) -> tuple[DecisionPermit, ExecutionAdmission]:
        if route.disposition not in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}:
            raise OrchestrationError("only an admitted exact route can request execution")
        if route.control_ref != control.digest:
            raise OrchestrationError("route does not bind the supplied control snapshot")
        if route != evaluate_proposal(proposal, control, policy=self._router_policy):
            raise OrchestrationError("route was not produced by the configured hard guards")
        if (
            route.selected_proposal_ref != proposal.digest
            or route.selected_proposal_id != proposal.proposal_id
        ):
            raise OrchestrationError("route does not bind the supplied proposal")
        if control.shadow_only or control.execution_grant_ref is None:
            raise OrchestrationError("execution requires a non-shadow external grant")
        grant = self._grants.require(control.execution_grant_ref)
        if (
            grant.executor_id != self._executor.executor_id
            or grant.executor_version != self._executor.executor_version
            or grant.executor_artifact_ref != self._executor.executor_artifact_ref
        ):
            raise OrchestrationError("task grant does not bind this coordinator's writer")
        release_review_required = (
            grant.release_review_required or DecisionEffect.OUTPUT in proposal.decision_effects
        )
        expected_lab_decision = evaluate_lab_rules(
            context=lab_decision.context,
            pea_review=pea_review,
            crossing=crossing,
            seed_release=seed_release,
            external_effect_requested=True,
            release_requested=release_review_required,
        )
        if lab_decision != expected_lab_decision:
            raise OrchestrationError(
                "lab decision was not produced by the supplied PEA, PECAN, and SEED records"
            )
        binding = lab_decision.external_effect_binding
        if (
            binding is None
            or binding.status is not BoundaryStatus.CLEAR
            or control.lab_boundary != binding
            or not lab_decision.clears_requested_boundaries
        ):
            raise OrchestrationError("control does not contain this exact clear lab decision")
        if (
            proposal.observation_id != control.observation_id
            or proposal.observation_ref != control.observation_ref
            or proposal.scope_id != control.scope_id
            or not control.contains_goal(proposal.goal_id, proposal.goal_ref)
        ):
            raise OrchestrationError("proposal is stale or outside the active control scope")
        context = lab_decision.context
        if (
            context.grant_ref != grant.grant_ref
            or context.task_id != grant.task_id
            or context.goal_id != grant.goal_id
            or context.goal_ref != grant.goal_ref
            or context.scope_id != grant.scope_id
            or context.output_destination_ref != grant.output_destination_ref
            or context.attention_budget != grant.maximum_attention_units
            or context.observation_id != proposal.observation_id
            or context.observation_ref != proposal.observation_ref
            or context.proposal_ref != proposal.digest
            or context.action_ref != proposal.action.digest
            or proposal.goal_id != grant.goal_id
            or proposal.goal_ref != grant.goal_ref
        ):
            raise OrchestrationError("lab context, proposal, and task grant disagree")
        permit = self._grants._begin_permit(
            grant_ref=grant.grant_ref,
            invocation_id=invocation_id,
            proposal_ref=proposal.digest,
            action_ref=proposal.action.digest,
            executor_id=grant.executor_id,
            executor_version=grant.executor_version,
            executor_artifact_ref=grant.executor_artifact_ref,
            observation_id=proposal.observation_id,
            observation_ref=proposal.observation_ref,
            scope_id=proposal.scope_id,
            route_ref=route.digest,
            control_ref=control.digest,
            lab_decision_ref=lab_decision.digest,
            boundary=boundary,
            action_name=proposal.action.name,
            serial_token=control.serial_token,
        )
        admission = ExecutionAdmission(
            route_ref=route.digest,
            control_ref=control.digest,
            lab_decision_ref=lab_decision.digest,
            grant_ref=grant.grant_ref,
            proposal_ref=proposal.digest,
            action_ref=proposal.action.digest,
            action_name=proposal.action.name,
            executor_id=grant.executor_id,
            executor_version=grant.executor_version,
            executor_artifact_ref=grant.executor_artifact_ref,
            observation_id=proposal.observation_id,
            observation_ref=proposal.observation_ref,
            scope_id=proposal.scope_id,
            invocation_id=invocation_id,
        )
        return permit, admission

    def execute_once(
        self,
        permit: DecisionPermit,
        admission: ExecutionAdmission,
        proposal: CandidateProposal,
        *,
        boundary: int,
    ) -> ExecutionCallResult:
        expected_admission = ExecutionAdmission(
            route_ref=permit.route_ref,
            control_ref=permit.control_ref,
            lab_decision_ref=permit.lab_decision_ref,
            grant_ref=permit.grant_ref,
            proposal_ref=permit.proposal_ref,
            action_ref=permit.action_ref,
            action_name=permit.action_name,
            executor_id=permit.executor_id,
            executor_version=permit.executor_version,
            executor_artifact_ref=permit.executor_artifact_ref,
            observation_id=permit.observation_id,
            observation_ref=permit.observation_ref,
            scope_id=permit.scope_id,
            invocation_id=permit.invocation_id,
        )
        if (
            admission != expected_admission
            or permit.grant_ref != admission.grant_ref
            or permit.invocation_id != admission.invocation_id
            or permit.proposal_ref != admission.proposal_ref
            or permit.action_ref != admission.action_ref
            or permit.route_ref != admission.route_ref
            or permit.control_ref != admission.control_ref
            or permit.lab_decision_ref != admission.lab_decision_ref
            or permit.executor_id != admission.executor_id
            or permit.executor_version != admission.executor_version
            or permit.executor_artifact_ref != admission.executor_artifact_ref
            or admission.executor_id != self._executor.executor_id
            or admission.executor_version != self._executor.executor_version
            or admission.executor_artifact_ref != self._executor.executor_artifact_ref
            or permit.observation_id != admission.observation_id
            or permit.observation_ref != admission.observation_ref
            or admission.proposal_ref != proposal.digest
            or admission.action_ref != proposal.action.digest
        ):
            raise OrchestrationError("permit and execution admission disagree")
        release = self._grants._release_permit(
            permit,
            proposal_ref=admission.proposal_ref,
            action_ref=admission.action_ref,
            candidate_ref=admission.proposal_ref,
            boundary=boundary,
            action_name=admission.action_name,
            executor_id=self._executor.executor_id,
        )
        idempotency_key = admission.digest
        if release.status is not ReleaseStatus.RELEASED:
            attempt = ExecutionAttemptReceipt(
                admission_ref=admission.digest,
                release_ref=release.digest,
                executor_id=admission.executor_id,
                executor_version=admission.executor_version,
                executor_artifact_ref=admission.executor_artifact_ref,
                proposal_ref=admission.proposal_ref,
                action_ref=admission.action_ref,
                idempotency_key=idempotency_key,
                disposition=ExecutionDisposition.BLOCKED,
                failure_category="grant_revalidation_blocked",
            )
            return self._result(admission, release, attempt, None)
        try:
            command = ExecutionCommand(
                invocation_id=admission.invocation_id,
                idempotency_key=idempotency_key,
                grant_ref=admission.grant_ref,
                admission_ref=admission.digest,
                proposal_ref=admission.proposal_ref,
                action_ref=admission.action_ref,
                action=proposal.action,
                executor_id=admission.executor_id,
                executor_version=admission.executor_version,
                executor_artifact_ref=admission.executor_artifact_ref,
            )
            observation = self._executor.execute(command)
            if not isinstance(observation, ExecutorObservation):
                raise TypeError("executor returned no bound observation evidence")
        except Exception as error:
            attempt = ExecutionAttemptReceipt(
                admission_ref=admission.digest,
                release_ref=release.digest,
                executor_id=admission.executor_id,
                executor_version=admission.executor_version,
                executor_artifact_ref=admission.executor_artifact_ref,
                proposal_ref=admission.proposal_ref,
                action_ref=admission.action_ref,
                idempotency_key=idempotency_key,
                disposition=ExecutionDisposition.UNKNOWN_EFFECT,
                failure_category=f"executor_effect_unknown:{type(error).__name__}",
            )
            return self._result(admission, release, attempt, None)
        attempt = ExecutionAttemptReceipt(
            admission_ref=admission.digest,
            release_ref=release.digest,
            executor_id=admission.executor_id,
            executor_version=admission.executor_version,
            executor_artifact_ref=admission.executor_artifact_ref,
            proposal_ref=admission.proposal_ref,
            action_ref=admission.action_ref,
            idempotency_key=idempotency_key,
            disposition=ExecutionDisposition.COMPLETED,
            result_evidence_ref=observation.evidence_ref.sha256,
        )
        return self._result(admission, release, attempt, observation)
