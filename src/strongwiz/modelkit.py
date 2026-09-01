"""Small adapters for attaching ordinary Python model functions to Strongwiz.

The callback supplies proposal content.  Strongwiz supplies the exact driver,
observation, scope, and goal bindings so a provider adapter cannot accidentally
reuse a proposal under a different request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

from pydantic import Field, field_validator, model_validator

from strongwiz.canonical import content_hash
from strongwiz.contracts import (
    ActionSpec,
    CandidateProposal,
    ContractModel,
    CostVector,
    DecisionEffect,
    Distinction,
    NonNegativeInt,
    PositiveInt,
    Prediction,
    ReasoningRequest,
)
from strongwiz.transport import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    BinaryReader,
    BinaryWriter,
    FrameFormatError,
    ReplayGuard,
    read_frame_record,
    write_frame,
)


class ProposalDraft(ContractModel):
    """Model-authored proposal content before Strongwiz adds request bindings."""

    proposal_id: str
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

    @field_validator("proposal_id", "concise_rationale")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proposal identity and concise rationale must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_effects(self) -> ProposalDraft:
        if set(self.decision_effects) != set(self.meaningful_distinction.decision_effects):
            raise ValueError("draft and distinction decision effects disagree")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("proposal evidence references must be unique")
        return self


ProposalFunction = Callable[[ReasoningRequest], Sequence[ProposalDraft]]


def _bind_drafts(
    driver_id: str,
    request: ReasoningRequest,
    drafts: Sequence[ProposalDraft],
) -> tuple[CandidateProposal, ...]:
    proposals = tuple(
        CandidateProposal(
            proposal_id=draft.proposal_id,
            model_driver_id=driver_id,
            observation_id=request.observation.observation_id,
            observation_ref=request.observation.digest,
            scope_id=request.observation.scope_id,
            goal_id=request.scoped_goal.goal_id,
            goal_ref=request.scoped_goal.digest,
            action=draft.action,
            meaningful_distinction=draft.meaningful_distinction,
            prediction=draft.prediction,
            decision_effects=draft.decision_effects,
            evidence_refs=draft.evidence_refs,
            trace_refs=draft.trace_refs,
            residual_refs=draft.residual_refs,
            material_delta_refs=draft.material_delta_refs,
            prior_account_ref=draft.prior_account_ref,
            concise_rationale=draft.concise_rationale,
            reversible=draft.reversible,
            expected_progress_rank=draft.expected_progress_rank,
            information_gain_rank=draft.information_gain_rank,
            risk_rank=draft.risk_rank,
            costs=draft.costs,
        )
        for draft in drafts
    )
    if len({proposal.proposal_id for proposal in proposals}) != len(proposals):
        raise ValueError("model returned duplicate proposal identities")
    return proposals


@dataclass(frozen=True, slots=True)
class CallableModelDriver:
    """Bind a deterministic local callback to the public ``ModelDriver`` protocol."""

    driver_id: str
    driver_version: str
    driver_artifact_ref: str
    proposal_function: ProposalFunction

    def __post_init__(self) -> None:
        for label, value in (
            ("driver ID", self.driver_id),
            ("driver version", self.driver_version),
            ("driver artifact reference", self.driver_artifact_ref),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if not callable(self.proposal_function):
            raise TypeError("proposal function must be callable")

    def propose(self, request: ReasoningRequest) -> tuple[CandidateProposal, ...]:
        drafts = self.proposal_function(request)
        if isinstance(drafts, (str, bytes)) or not isinstance(drafts, Sequence):
            raise TypeError("proposal function must return a sequence of ProposalDraft values")
        typed: list[ProposalDraft] = []
        for draft in drafts:
            if not isinstance(draft, ProposalDraft):
                raise TypeError("proposal function returned a non-ProposalDraft value")
            typed.append(draft)
        return _bind_drafts(self.driver_id, request, typed)


class FramedModelResponse(ContractModel):
    """Provider response carried over the binary framed adapter boundary."""

    message_id: str
    kind: Literal["strongwiz.model-response.v1"] = "strongwiz.model-response.v1"
    in_reply_to: str
    driver_id: str
    proposal_drafts: tuple[ProposalDraft, ...]

    @field_validator("message_id", "in_reply_to", "driver_id")
    @classmethod
    def validate_response_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framed model response identities must be non-empty")
        return value


class FramedAcceptedResponse(ContractModel):
    """One response identity retained in the bounded restart replay window."""

    message_id: str
    payload_sha256: str

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("accepted response identity must be non-empty")
        return value

    @field_validator("payload_sha256")
    @classmethod
    def validate_payload_sha256(cls, value: str) -> str:
        if (
            len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("accepted response content must use lowercase SHA-256")
        return value


class FramedModelRestartState(ContractModel):
    """Caller-retained sequence and bounded response identities for reconstruction.

    This value makes reconstruction explicit; it is not process supervision.  A
    caller claiming process-crash durability must persist every state delivered
    to ``state_sink`` before that callback returns.
    """

    schema_id: str = Field(default="strongwiz.framed-model-state.v1", alias="schema")
    session_id: str
    driver_id: str
    driver_version: str
    driver_artifact_ref: str
    next_sequence: NonNegativeInt
    replay_capacity: PositiveInt = 4096
    accepted_responses: tuple[FramedAcceptedResponse, ...] = ()

    @field_validator("next_sequence", "replay_capacity", mode="before")
    @classmethod
    def reject_boolean_counters(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("framed model counters must be integers, not booleans")
        return value

    @model_validator(mode="after")
    def validate_restart_state(self) -> FramedModelRestartState:
        if self.schema_id != "strongwiz.framed-model-state.v1":
            raise ValueError("unsupported framed model restart state")
        if not all(
            value.strip()
            for value in (
                self.session_id,
                self.driver_id,
                self.driver_version,
                self.driver_artifact_ref,
            )
        ):
            raise ValueError("framed model restart identities must be non-empty")
        if len(self.accepted_responses) > self.replay_capacity:
            raise ValueError("accepted responses exceed the declared replay window")
        message_ids = tuple(item.message_id for item in self.accepted_responses)
        payloads = tuple(item.payload_sha256 for item in self.accepted_responses)
        if len(set(message_ids)) != len(message_ids):
            raise ValueError("restart state contains a duplicate response identity")
        if len(set(payloads)) != len(payloads):
            raise ValueError("restart state contains replayed response content")
        return self

    @classmethod
    def initial(
        cls,
        *,
        session_id: str,
        driver_id: str,
        driver_version: str,
        driver_artifact_ref: str,
        starting_sequence: int = 0,
        replay_capacity: int = 4096,
    ) -> FramedModelRestartState:
        """Declare a new session explicitly, optionally at a validated sequence."""

        return cls(
            session_id=session_id,
            driver_id=driver_id,
            driver_version=driver_version,
            driver_artifact_ref=driver_artifact_ref,
            next_sequence=starting_sequence,
            replay_capacity=replay_capacity,
        )


def framed_request_identity(
    driver_id: str,
    request: ReasoningRequest,
    sequence: int,
    *,
    session_id: str | None = None,
) -> str:
    if not driver_id.strip():
        raise ValueError("driver ID must be non-empty")
    if isinstance(sequence, bool) or sequence < 0:
        raise ValueError("framed request sequence must be non-negative")
    if session_id is not None and not session_id.strip():
        raise ValueError("framed session ID must be non-empty")
    identity = {
        "driver_id": driver_id,
        "request_ref": request.digest,
        "sequence": sequence,
    }
    if session_id is not None:
        identity["session_id"] = session_id
    return content_hash(identity)


StateSink = Callable[[FramedModelRestartState], None]


@dataclass(slots=True)
class FramedModelDriver:
    """Offline binary-stream driver with explicit reconstruction state.

    The state advances before outbound I/O and retains the bounded identities
    of accepted inbound frames.  Reconstruct a driver with its latest
    ``restart_state``; constructing a new ``initial`` state deliberately starts
    a different session.  This coordinates one framed exchange and does not
    launch, monitor, or restart an operating-system process.
    """

    driver_id: str
    driver_version: str
    driver_artifact_ref: str
    reader: BinaryReader
    writer: BinaryWriter
    restart_state: FramedModelRestartState
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    timeout_seconds: float | None = None
    state_sink: StateSink | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("driver ID", self.driver_id),
            ("driver version", self.driver_version),
            ("driver artifact reference", self.driver_artifact_ref),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if (
            self.restart_state.driver_id != self.driver_id
            or self.restart_state.driver_version != self.driver_version
            or self.restart_state.driver_artifact_ref != self.driver_artifact_ref
        ):
            raise ValueError("framed restart state belongs to a different driver")
        if self.state_sink is not None and not callable(self.state_sink):
            raise TypeError("framed model state sink must be callable")
        self._guard_for(self.restart_state)

    @staticmethod
    def _guard_for(state: FramedModelRestartState) -> ReplayGuard:
        guard = ReplayGuard(capacity=state.replay_capacity)
        for accepted in state.accepted_responses:
            guard.check_and_record(accepted.message_id, accepted.payload_sha256)
        return guard

    def _commit_state(self, state: FramedModelRestartState) -> None:
        if self.state_sink is not None:
            self.state_sink(state)
        self.restart_state = state

    def _reserve_request(self, request: ReasoningRequest) -> tuple[int, str]:
        sequence = self.restart_state.next_sequence
        message_id = framed_request_identity(
            self.driver_id,
            request,
            sequence,
            session_id=self.restart_state.session_id,
        )
        self._commit_state(
            self.restart_state.model_copy(update={"next_sequence": sequence + 1})
        )
        return sequence, message_id

    def _accept_response_identity(self, message_id: str, payload_sha256: str) -> None:
        candidate_guard = self._guard_for(self.restart_state)
        candidate_guard.check_and_record(message_id, payload_sha256)
        accepted = (
            *self.restart_state.accepted_responses,
            FramedAcceptedResponse(
                message_id=message_id,
                payload_sha256=payload_sha256,
            ),
        )[-self.restart_state.replay_capacity :]
        next_state = self.restart_state.model_copy(update={"accepted_responses": accepted})
        self._commit_state(next_state)

    def propose(self, request: ReasoningRequest) -> tuple[CandidateProposal, ...]:
        with self._lock:
            _, message_id = self._reserve_request(request)
            write_frame(
                self.writer,
                {
                    "driver_id": self.driver_id,
                    "kind": "strongwiz.model-request.v1",
                    "message_id": message_id,
                    "request": request.model_dump(mode="json", by_alias=True),
                    "session_id": self.restart_state.session_id,
                },
                max_payload_bytes=self.max_payload_bytes,
                timeout_seconds=self.timeout_seconds,
            )
            decoded = read_frame_record(
                self.reader,
                max_payload_bytes=self.max_payload_bytes,
                timeout_seconds=self.timeout_seconds,
            )
            if not isinstance(decoded.value, Mapping):
                raise FrameFormatError("identified frame payload must be a top-level object")
            response_identity = decoded.value.get("message_id")
            if not isinstance(response_identity, str) or not response_identity.strip():
                raise FrameFormatError(
                    "identified frame requires a non-empty string 'message_id'"
                )
            self._accept_response_identity(
                response_identity,
                decoded.receipt.payload_sha256,
            )
            response = FramedModelResponse.model_validate(decoded.value)
            if response.in_reply_to != message_id:
                raise ValueError("framed model response does not bind the current request")
            if response.driver_id != self.driver_id:
                raise ValueError("framed model response declares a different driver")
            return _bind_drafts(self.driver_id, request, response.proposal_drafts)
