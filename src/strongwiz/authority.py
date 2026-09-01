"""Externally rooted goal/grant replacement and pre-release revalidation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt


class AuthorityError(ValueError):
    pass


class GrantSource(StrEnum):
    HUMAN = "human"
    EXTERNAL_CONTROL = "external_control"
    SYSTEM_POLICY = "system_policy"


class GrantStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"


class ReleaseStatus(StrEnum):
    RELEASED = "released"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"


class TaskGrant(ContractModel):
    schema_id: str = Field(default="strongwiz.task-grant.v1", alias="schema")
    root_ref: str
    source: GrantSource
    task_id: str
    goal_id: str
    goal_ref: str
    scope_id: str
    generation: NonNegativeInt
    issued_boundary: NonNegativeInt
    not_before_boundary: NonNegativeInt
    expires_boundary: NonNegativeInt
    maximum_invocations: PositiveInt
    allowed_action_names: tuple[str, ...]
    allowed_action_refs: tuple[str, ...] = ()
    executor_id: str
    executor_version: str
    executor_artifact_ref: str
    output_destination_ref: str
    release_review_required: bool
    maximum_attention_units: NonNegativeInt = 0
    replaces_grant_ref: str | None = None

    @model_validator(mode="after")
    def validate_grant(self) -> TaskGrant:
        required = (
            self.root_ref,
            self.task_id,
            self.goal_id,
            self.goal_ref,
            self.scope_id,
            self.executor_id,
            self.executor_version,
            self.executor_artifact_ref,
            self.output_destination_ref,
        )
        if not all(value.strip() for value in required):
            raise ValueError("grant root, task, goal, scope, and destination are required")
        if self.not_before_boundary < self.issued_boundary:
            raise ValueError("grant cannot begin before it was issued")
        if self.expires_boundary < self.not_before_boundary:
            raise ValueError("grant expires before becoming active")
        if not self.allowed_action_names:
            raise ValueError("grant requires an independently supplied action aperture")
        if len(set(self.allowed_action_names)) != len(self.allowed_action_names) or len(
            set(self.allowed_action_refs)
        ) != len(self.allowed_action_refs):
            raise ValueError("grant action apertures must be unique")
        return self

    @property
    def grant_ref(self) -> str:
        return self.digest


class ReleaseReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.release-receipt.v1", alias="schema")
    grant_ref: str
    invocation_id: str
    proposal_ref: str
    action_ref: str
    action_name: str
    executor_id: str
    executor_version: str
    executor_artifact_ref: str
    observation_id: str
    observation_ref: str
    scope_id: str
    route_ref: str
    control_ref: str
    lab_decision_ref: str
    candidate_ref: str | None
    status: ReleaseStatus
    reason: str
    boundary: NonNegativeInt
    output_destination_ref: str | None


class DecisionPermit:
    """One-use in-process permit deliberately excluded from serialization."""

    __slots__ = (
        "_issuer",
        "_token",
        "_used",
        "action_name",
        "action_ref",
        "boundary",
        "control_ref",
        "executor_artifact_ref",
        "executor_id",
        "executor_version",
        "grant_ref",
        "invocation_id",
        "lab_decision_ref",
        "observation_id",
        "observation_ref",
        "proposal_ref",
        "route_ref",
        "scope_id",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("decision permit bindings are immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        token: str,
        issuer: object,
        grant_ref: str,
        invocation_id: str,
        proposal_ref: str,
        action_ref: str,
        action_name: str,
        executor_id: str,
        executor_version: str,
        executor_artifact_ref: str,
        observation_id: str,
        observation_ref: str,
        scope_id: str,
        route_ref: str,
        control_ref: str,
        lab_decision_ref: str,
        boundary: int,
    ) -> None:
        required = (
            token,
            grant_ref,
            invocation_id,
            proposal_ref,
            action_ref,
            action_name,
            executor_id,
            executor_version,
            executor_artifact_ref,
            observation_id,
            observation_ref,
            scope_id,
            route_ref,
            control_ref,
            lab_decision_ref,
        )
        if not all(required) or boundary < 0:
            raise AuthorityError("permit bindings and nonnegative boundary are required")
        self._token = token
        self._issuer = issuer
        self.grant_ref = grant_ref
        self.invocation_id = invocation_id
        self.proposal_ref = proposal_ref
        self.action_ref = action_ref
        self.action_name = action_name
        self.executor_id = executor_id
        self.executor_version = executor_version
        self.executor_artifact_ref = executor_artifact_ref
        self.observation_id = observation_id
        self.observation_ref = observation_ref
        self.scope_id = scope_id
        self.route_ref = route_ref
        self.control_ref = control_ref
        self.lab_decision_ref = lab_decision_ref
        self.boundary = boundary
        self._used = False

    @property
    def used(self) -> bool:
        return self._used

    def consume(self) -> None:
        if self._used:
            raise AuthorityError("decision permit has already been consumed")
        object.__setattr__(self, "_used", True)

    def issued_by(self, issuer: object) -> bool:
        return self._issuer is issuer

    def __getstate__(self) -> object:
        raise TypeError("decision permits are intentionally nonserializable")


class GrantRegistry:
    """Current-grant control plane; historical reasoning cannot reactivate it."""

    def __init__(self) -> None:
        self._grants: dict[str, TaskGrant] = {}
        self._statuses: dict[str, GrantStatus] = {}
        self._active_ref: str | None = None
        self._invocations: dict[str, int] = {}
        self._outstanding: dict[str, int] = {}
        self._invocation_ids: set[tuple[str, str]] = set()
        self._serial_tokens: set[str] = set()
        self._permit_issuer = object()

    def activate(self, grant: TaskGrant) -> str:
        grant_ref = grant.grant_ref
        current = self._grants.get(grant_ref)
        if current is not None and current != grant:
            raise AuthorityError("grant identity cannot be rewritten")
        if self._active_ref is not None:
            if grant.replaces_grant_ref != self._active_ref:
                raise AuthorityError("replacement must bind the exact active grant")
            self._statuses[self._active_ref] = GrantStatus.SUPERSEDED
        elif grant.replaces_grant_ref is not None:
            raise AuthorityError("initial grant cannot replace an absent grant")
        self._grants[grant_ref] = grant
        self._statuses[grant_ref] = GrantStatus.ACTIVE
        self._invocations.setdefault(grant_ref, 0)
        self._outstanding.setdefault(grant_ref, 0)
        self._active_ref = grant_ref
        return grant_ref

    def revoke(self, grant_ref: str) -> None:
        if self._statuses.get(grant_ref) is not GrantStatus.ACTIVE:
            raise AuthorityError("only the active grant can be revoked")
        self._statuses[grant_ref] = GrantStatus.REVOKED
        if self._active_ref == grant_ref:
            self._active_ref = None

    def _begin_permit(
        self,
        *,
        grant_ref: str,
        invocation_id: str,
        proposal_ref: str,
        action_ref: str,
        executor_id: str,
        executor_version: str,
        executor_artifact_ref: str,
        observation_id: str,
        observation_ref: str,
        scope_id: str,
        route_ref: str,
        control_ref: str,
        lab_decision_ref: str,
        boundary: int,
        action_name: str,
        serial_token: str,
    ) -> DecisionPermit:
        grant = self._require_active(
            grant_ref,
            boundary=boundary,
            action_name=action_name,
            action_ref=action_ref,
            executor_id=executor_id,
            executor_version=executor_version,
            executor_artifact_ref=executor_artifact_ref,
            scope_id=scope_id,
        )
        if serial_token in self._serial_tokens:
            raise AuthorityError("serial token has already been admitted")
        invocation_key = (grant_ref, invocation_id)
        if invocation_key in self._invocation_ids:
            raise AuthorityError("invocation identity has already been admitted")
        if self._invocations[grant_ref] >= grant.maximum_invocations:
            raise AuthorityError("grant invocation budget is exhausted")
        self._serial_tokens.add(serial_token)
        self._invocation_ids.add(invocation_key)
        self._invocations[grant_ref] += 1
        self._outstanding[grant_ref] += 1
        return DecisionPermit(
            token=serial_token,
            issuer=self._permit_issuer,
            grant_ref=grant_ref,
            invocation_id=invocation_id,
            proposal_ref=proposal_ref,
            action_ref=action_ref,
            action_name=action_name,
            executor_id=executor_id,
            executor_version=executor_version,
            executor_artifact_ref=executor_artifact_ref,
            observation_id=observation_id,
            observation_ref=observation_ref,
            scope_id=scope_id,
            route_ref=route_ref,
            control_ref=control_ref,
            lab_decision_ref=lab_decision_ref,
            boundary=boundary,
        )

    def _release_permit(
        self,
        permit: DecisionPermit,
        *,
        proposal_ref: str,
        action_ref: str,
        candidate_ref: str,
        boundary: int,
        action_name: str,
        executor_id: str,
    ) -> ReleaseReceipt:
        if not permit.issued_by(self._permit_issuer):
            raise AuthorityError("decision permit was not issued by this grant registry")
        if permit.used:
            raise AuthorityError("decision permit has already been used")
        bindings_match = (
            proposal_ref == permit.proposal_ref
            and action_ref == permit.action_ref
            and action_name == permit.action_name
            and executor_id == permit.executor_id
            and boundary >= permit.boundary
        )
        if not bindings_match:
            permit.consume()
            self._finish_reservation(permit.grant_ref)
            return ReleaseReceipt(
                grant_ref=permit.grant_ref,
                invocation_id=permit.invocation_id,
                proposal_ref=permit.proposal_ref,
                action_ref=permit.action_ref,
                action_name=permit.action_name,
                executor_id=permit.executor_id,
                executor_version=permit.executor_version,
                executor_artifact_ref=permit.executor_artifact_ref,
                observation_id=permit.observation_id,
                observation_ref=permit.observation_ref,
                scope_id=permit.scope_id,
                route_ref=permit.route_ref,
                control_ref=permit.control_ref,
                lab_decision_ref=permit.lab_decision_ref,
                candidate_ref=candidate_ref,
                status=ReleaseStatus.QUARANTINED,
                reason="release bindings differ from the admitted proposal or action",
                boundary=boundary,
                output_destination_ref=None,
            )
        try:
            grant = self._require_active(
                permit.grant_ref,
                boundary=boundary,
                action_name=action_name,
                action_ref=permit.action_ref,
                executor_id=permit.executor_id,
                executor_version=permit.executor_version,
                executor_artifact_ref=permit.executor_artifact_ref,
                scope_id=permit.scope_id,
            )
        except AuthorityError as error:
            permit.consume()
            self._finish_reservation(permit.grant_ref)
            return ReleaseReceipt(
                grant_ref=permit.grant_ref,
                invocation_id=permit.invocation_id,
                proposal_ref=permit.proposal_ref,
                action_ref=permit.action_ref,
                action_name=permit.action_name,
                executor_id=permit.executor_id,
                executor_version=permit.executor_version,
                executor_artifact_ref=permit.executor_artifact_ref,
                observation_id=permit.observation_id,
                observation_ref=permit.observation_ref,
                scope_id=permit.scope_id,
                route_ref=permit.route_ref,
                control_ref=permit.control_ref,
                lab_decision_ref=permit.lab_decision_ref,
                candidate_ref=candidate_ref,
                status=ReleaseStatus.QUARANTINED,
                reason=f"control changed before release: {error}",
                boundary=boundary,
                output_destination_ref=None,
            )
        permit.consume()
        self._finish_reservation(permit.grant_ref)
        return ReleaseReceipt(
            grant_ref=permit.grant_ref,
            invocation_id=permit.invocation_id,
            proposal_ref=permit.proposal_ref,
            action_ref=permit.action_ref,
            action_name=permit.action_name,
            executor_id=permit.executor_id,
            executor_version=permit.executor_version,
            executor_artifact_ref=permit.executor_artifact_ref,
            observation_id=permit.observation_id,
            observation_ref=permit.observation_ref,
            scope_id=permit.scope_id,
            route_ref=permit.route_ref,
            control_ref=permit.control_ref,
            lab_decision_ref=permit.lab_decision_ref,
            candidate_ref=candidate_ref,
            status=ReleaseStatus.RELEASED,
            reason="grant revalidated immediately before the control-owned executor call",
            boundary=boundary,
            output_destination_ref=grant.output_destination_ref,
        )

    def _finish_reservation(self, grant_ref: str) -> None:
        if self._outstanding.get(grant_ref, 0) <= 0:
            raise AuthorityError("grant has no outstanding permit reservation")
        self._outstanding[grant_ref] -= 1
        grant = self._grants[grant_ref]
        if (
            self._active_ref == grant_ref
            and self._statuses[grant_ref] is GrantStatus.ACTIVE
            and self._invocations[grant_ref] >= grant.maximum_invocations
            and self._outstanding[grant_ref] == 0
        ):
            self._statuses[grant_ref] = GrantStatus.EXHAUSTED

    def _require_active(
        self,
        grant_ref: str,
        *,
        boundary: int,
        action_name: str,
        action_ref: str,
        executor_id: str,
        executor_version: str,
        executor_artifact_ref: str,
        scope_id: str,
    ) -> TaskGrant:
        try:
            grant = self._grants[grant_ref]
        except KeyError as error:
            raise AuthorityError("unknown grant") from error
        if self._active_ref != grant_ref or self._statuses[grant_ref] is not GrantStatus.ACTIVE:
            raise AuthorityError("grant is not the current active grant")
        if boundary < grant.not_before_boundary:
            raise AuthorityError("grant is not active yet")
        if boundary > grant.expires_boundary:
            self._statuses[grant_ref] = GrantStatus.EXPIRED
            raise AuthorityError("grant has expired")
        if action_name not in grant.allowed_action_names:
            raise AuthorityError("action is outside the grant aperture")
        if grant.allowed_action_refs and action_ref not in grant.allowed_action_refs:
            raise AuthorityError("exact action content is outside the grant aperture")
        if executor_id != grant.executor_id:
            raise AuthorityError("executor is outside the grant aperture")
        if (
            executor_version != grant.executor_version
            or executor_artifact_ref != grant.executor_artifact_ref
        ):
            raise AuthorityError("executor version or artifact is outside the grant aperture")
        if scope_id != grant.scope_id:
            raise AuthorityError("action scope is outside the grant aperture")
        return grant

    @property
    def active_grant_ref(self) -> str | None:
        return self._active_ref

    def require(self, grant_ref: str) -> TaskGrant:
        try:
            return self._grants[grant_ref]
        except KeyError as error:
            raise AuthorityError("unknown grant") from error

    def status(self, grant_ref: str) -> GrantStatus:
        try:
            return self._statuses[grant_ref]
        except KeyError as error:
            raise AuthorityError("unknown grant") from error
