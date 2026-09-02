"""Model-neutral reasoning session with an enforced scan/act/assess lifecycle."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.authority import ReleaseStatus
from strongwiz.canonical import content_hash
from strongwiz.contracts import (
    CONTRACT_SCHEMA,
    CandidateProposal,
    ContractModel,
    ControlSnapshot,
    DeliberationMode,
    NonNegativeInt,
    Observation,
    Outcome,
    ReasoningRequest,
    RouteDecision,
    RouteDisposition,
)
from strongwiz.drivers import DomainAdapter, DriverRegistry, ModelDriver, TerminalAuthority
from strongwiz.integrity import FrozenRuntimeManifest
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger
from strongwiz.orchestration import ExecutionCallResult, ExecutionDisposition
from strongwiz.policy import CadencePolicy, CadenceSelection, CadenceSignals, action_mode
from strongwiz.routing import RouterPolicy, select_route


class RuntimeError(ValueError):
    pass


class SessionPhase(StrEnum):
    NEEDS_SCAN = "needs_scan"
    READY_TO_ACT = "ready_to_act"
    AWAITING_ASSESSMENT = "awaiting_assessment"
    TERMINAL = "terminal"


class ScanReceipt(ContractModel):
    session_id: str
    observation_ref: str
    request_ref: str
    distinction_refs: tuple[str, ...]
    retained_fact_refs: tuple[str, ...]
    phase_before: SessionPhase
    phase_after: SessionPhase


class DecisionRecord(ContractModel):
    session_id: str
    request_ref: str
    driver_id: str
    candidate_refs: tuple[str, ...]
    selected_proposal_ref: str | None
    route: RouteDecision
    cadence: CadenceSelection
    mode: DeliberationMode
    alternatives_considered: tuple[str, ...]
    concise_selection_summary: str


class AssessmentRecord(ContractModel):
    session_id: str
    proposal_ref: str
    execution_admission_ref: str
    release_ref: str
    execution_attempt_ref: str
    executor_evidence_ref: str
    outcome_ref: str
    observation_after_ref: str
    terminal_authority: TerminalAuthority
    matched_prediction_items: tuple[str, ...]
    residual_refs: tuple[str, ...]
    preserved_hypothesis_refs: tuple[str, ...]
    revised_hypothesis_refs: tuple[str, ...]
    concise_update_summary: str
    phase_after: SessionPhase

    @model_validator(mode="after")
    def validate_revision_partition(self) -> AssessmentRecord:
        overlap = set(self.preserved_hypothesis_refs) & set(self.revised_hypothesis_refs)
        if overlap:
            raise ValueError(
                "assessment cannot both preserve and revise one hypothesis version"
            )
        if not self.concise_update_summary:
            raise ValueError("assessment requires a concise update summary")
        return self


class SessionReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.session-receipt.v1", alias="schema")
    session_id: str
    driver_id: str
    domain_adapter_id: str
    frozen_runtime_ref: str
    router_policy_ref: str
    cadence_policy_ref: str
    governing_goal_ref: str
    phase: SessionPhase
    terminal_authority: TerminalAuthority | None
    scans: tuple[ScanReceipt, ...]
    decisions: tuple[DecisionRecord, ...]
    assessments: tuple[AssessmentRecord, ...]
    admitted_action_count: int
    completion_genuinely_observed: bool
    ledger_receipt_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = Field(
        default=(
            "admitted actions may still require separate permission, "
            "authorization, and execution",
            "model proposals and concise summaries are not independently guaranteed true",
        )
    )


class SessionCheckpoint(SessionReceipt):
    """Immutable, restart-complete session state at one receipt boundary.

    ``SessionReceipt`` intentionally stays concise.  A checkpoint retains the
    active request, admitted proposal, failed-action guard, and ledger account
    identity needed to continue without asking a model or environment to repeat
    work. It shares the concise fields but has a distinct versioned wire schema;
    consumers that require ``SessionReceipt`` must call :meth:`concise_receipt`.
    """

    schema_id: str = Field(default="strongwiz.session-checkpoint.v1", alias="schema")
    active_request: ReasoningRequest | None = None
    pending_proposal: CandidateProposal | None = None
    last_failed_action_ref: str | None = None
    last_failed_belief_ref: str | None = None
    account_id: str
    account_version: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_restart_state(self) -> SessionCheckpoint:
        if self.schema_id != "strongwiz.session-checkpoint.v1":
            raise ValueError("unsupported session checkpoint schema")
        identities = (
            self.session_id,
            self.driver_id,
            self.domain_adapter_id,
            self.account_id,
        )
        if not all(value.strip() for value in identities):
            raise ValueError("checkpoint session, adapters, and ledger account are required")
        digest_refs = (
            self.frozen_runtime_ref,
            self.router_policy_ref,
            self.cadence_policy_ref,
            self.governing_goal_ref,
            *self.ledger_receipt_refs,
        )
        if any(
            len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
            for value in digest_refs
        ):
            raise ValueError("checkpoint bindings must use lowercase SHA-256 references")
        if len(set(self.ledger_receipt_refs)) != len(self.ledger_receipt_refs):
            raise ValueError("checkpoint ledger receipt references must be unique")
        if any(scan.session_id != self.session_id for scan in self.scans):
            raise ValueError("checkpoint scan history crosses session identity")
        if any(
            decision.session_id != self.session_id or decision.driver_id != self.driver_id
            for decision in self.decisions
        ):
            raise ValueError("checkpoint decision history crosses session or driver identity")
        if any(assessment.session_id != self.session_id for assessment in self.assessments):
            raise ValueError("checkpoint assessment history crosses session identity")
        if any(
            scan.phase_after is not SessionPhase.READY_TO_ACT
            or scan.phase_before in {SessionPhase.AWAITING_ASSESSMENT, SessionPhase.TERMINAL}
            for scan in self.scans
        ):
            raise ValueError("checkpoint scan history contains an invalid phase transition")
        for decision in self.decisions:
            if decision.selected_proposal_ref != decision.route.selected_proposal_ref:
                raise ValueError("checkpoint decision and route select different proposals")
            if decision.request_ref not in {scan.request_ref for scan in self.scans}:
                raise ValueError("checkpoint decision is not supported by a recorded scan")
            if (
                decision.selected_proposal_ref is not None
                and decision.selected_proposal_ref not in decision.candidate_refs
            ):
                raise ValueError("checkpoint decision selected an unrecorded candidate")
        for assessment in self.assessments:
            terminal = assessment.terminal_authority in {
                TerminalAuthority.SUCCESS,
                TerminalAuthority.BLOCKED,
            }
            if terminal != (assessment.phase_after is SessionPhase.TERMINAL):
                raise ValueError("checkpoint assessment has an incoherent terminal transition")
            if assessment.phase_after not in {
                SessionPhase.NEEDS_SCAN,
                SessionPhase.TERMINAL,
            }:
                raise ValueError("checkpoint assessment does not close its pending action")
        if any(
            assessment.phase_after is SessionPhase.TERMINAL
            for assessment in self.assessments[:-1]
        ):
            raise ValueError("checkpoint history continues after a terminal assessment")
        selected_refs = tuple(
            decision.selected_proposal_ref
            for decision in self.decisions
            if decision.selected_proposal_ref is not None
        )
        assessed_refs = tuple(assessment.proposal_ref for assessment in self.assessments)
        if assessed_refs != selected_refs[: len(assessed_refs)]:
            raise ValueError("checkpoint assessments do not follow admitted proposals")
        expected_selected = len(self.assessments) + (
            1 if self.phase is SessionPhase.AWAITING_ASSESSMENT else 0
        )
        if len(selected_refs) != expected_selected:
            raise ValueError("checkpoint selected-action history disagrees with its phase")
        admitted = sum(
            decision.route.disposition in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}
            for decision in self.decisions
        )
        if self.admitted_action_count != admitted:
            raise ValueError("checkpoint admitted-action count disagrees with decisions")
        if self.completion_genuinely_observed != (
            self.phase is SessionPhase.TERMINAL
            and self.terminal_authority is TerminalAuthority.SUCCESS
        ):
            raise ValueError("checkpoint completion claim disagrees with terminal authority")
        if (self.last_failed_action_ref is None) != (self.last_failed_belief_ref is None):
            raise ValueError("checkpoint failed-action guard must be complete or absent")
        for value in (self.last_failed_action_ref, self.last_failed_belief_ref):
            if value is not None and (
                len(value) != 64
                or value.lower() != value
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("checkpoint failed-action guard must use SHA-256 references")

        if self.active_request is not None:
            if self.active_request.governing_goal.digest != self.governing_goal_ref:
                raise ValueError("checkpoint request replaces the governing goal")
            if not self.scans or self.scans[-1].request_ref != self.active_request.digest:
                raise ValueError("checkpoint request is not the latest completed scan")
        if self.pending_proposal is not None:
            request = self.active_request
            if request is None:
                raise ValueError("checkpoint pending proposal requires its active request")
            if (
                self.pending_proposal.model_driver_id != self.driver_id
                or self.pending_proposal.observation_id != request.observation.observation_id
                or self.pending_proposal.observation_ref != request.observation.digest
                or self.pending_proposal.scope_id != request.observation.scope_id
                or self.pending_proposal.goal_id != request.scoped_goal.goal_id
                or self.pending_proposal.goal_ref != request.scoped_goal.digest
            ):
                raise ValueError("checkpoint pending proposal is not bound to its request")
            if (
                not self.decisions
                or self.decisions[-1].request_ref != request.digest
                or self.decisions[-1].selected_proposal_ref != self.pending_proposal.digest
            ):
                raise ValueError(
                    "checkpoint pending proposal was not admitted by the last decision"
                )

        if self.phase is SessionPhase.NEEDS_SCAN:
            if self.active_request is not None or self.pending_proposal is not None:
                raise ValueError("needs-scan checkpoint cannot retain actionable state")
            if self.terminal_authority is not None:
                raise ValueError("needs-scan checkpoint cannot claim terminal authority")
        elif self.phase is SessionPhase.READY_TO_ACT:
            if self.active_request is None or self.pending_proposal is not None:
                raise ValueError("ready checkpoint requires one request and no pending action")
            if self.terminal_authority is not None:
                raise ValueError("ready checkpoint cannot claim terminal authority")
        elif self.phase is SessionPhase.AWAITING_ASSESSMENT:
            if self.active_request is None or self.pending_proposal is None:
                raise ValueError("awaiting checkpoint requires request and pending proposal")
            if self.terminal_authority is not None:
                raise ValueError("awaiting checkpoint cannot claim terminal authority")
        else:
            if self.active_request is not None or self.pending_proposal is not None:
                raise ValueError("terminal checkpoint cannot retain actionable state")
            if self.terminal_authority not in {
                TerminalAuthority.SUCCESS,
                TerminalAuthority.BLOCKED,
            }:
                raise ValueError("terminal checkpoint requires final domain authority")
            if (
                not self.assessments
                or self.assessments[-1].phase_after is not SessionPhase.TERMINAL
                or self.assessments[-1].terminal_authority is not self.terminal_authority
            ):
                raise ValueError("terminal checkpoint is not supported by its last assessment")
        return self

    def concise_receipt(self) -> SessionReceipt:
        """Explicitly project restart state to the stable concise wire schema."""

        return SessionReceipt(
            session_id=self.session_id,
            driver_id=self.driver_id,
            domain_adapter_id=self.domain_adapter_id,
            frozen_runtime_ref=self.frozen_runtime_ref,
            router_policy_ref=self.router_policy_ref,
            cadence_policy_ref=self.cadence_policy_ref,
            governing_goal_ref=self.governing_goal_ref,
            phase=self.phase,
            terminal_authority=self.terminal_authority,
            scans=self.scans,
            decisions=self.decisions,
            assessments=self.assessments,
            admitted_action_count=self.admitted_action_count,
            completion_genuinely_observed=self.completion_genuinely_observed,
            ledger_receipt_refs=self.ledger_receipt_refs,
            limitations=self.limitations,
        )


class SessionCheckpointV2(ContractModel):
    """Reference-normalized durable checkpoint with constant-size history metadata.

    Historical scan, decision, assessment, and earlier checkpoint objects already
    exist as predecessor-linked ledger receipts.  V2 stores their counts and exact
    tail rather than copying the complete history into every checkpoint.  Restore
    expands that lineage through the original verified ledger without rerunning a
    model call or external action.
    """

    schema_id: str = Field(default="strongwiz.session-checkpoint.v2", alias="schema")
    session_id: str
    driver_id: str
    domain_adapter_id: str
    frozen_runtime_ref: str
    router_policy_ref: str
    cadence_policy_ref: str
    governing_goal_ref: str
    phase: SessionPhase
    terminal_authority: TerminalAuthority | None
    history_receipt_count: NonNegativeInt
    history_receipt_head: str | None
    scan_count: NonNegativeInt
    decision_count: NonNegativeInt
    assessment_count: NonNegativeInt
    admitted_action_count: NonNegativeInt
    completion_genuinely_observed: bool
    active_request_ref: str | None = None
    pending_proposal_ref: str | None = None
    last_failed_action_ref: str | None = None
    last_failed_belief_ref: str | None = None
    account_id: str
    account_version: NonNegativeInt = 0
    limitations: tuple[str, ...] = Field(
        default=(
            "history reconstruction requires the exact original verified ledger",
            "admitted actions may still require separate permission, authorization, "
            "and execution",
        )
    )

    @model_validator(mode="after")
    def validate_reference_checkpoint(self) -> SessionCheckpointV2:
        if self.schema_id != "strongwiz.session-checkpoint.v2":
            raise ValueError("unsupported reference checkpoint schema")
        if not all(
            value.strip()
            for value in (
                self.session_id,
                self.driver_id,
                self.domain_adapter_id,
                self.account_id,
            )
        ):
            raise ValueError("reference checkpoint identities are required")
        digest_refs = (
            self.frozen_runtime_ref,
            self.router_policy_ref,
            self.cadence_policy_ref,
            self.governing_goal_ref,
        )
        optional_refs = (
            self.history_receipt_head,
            self.active_request_ref,
            self.pending_proposal_ref,
            self.last_failed_action_ref,
            self.last_failed_belief_ref,
        )
        if any(
            len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
            for value in (*digest_refs, *(item for item in optional_refs if item is not None))
        ):
            raise ValueError("reference checkpoint bindings must be lowercase SHA-256 values")
        if (self.history_receipt_count == 0) != (self.history_receipt_head is None):
            raise ValueError("reference checkpoint history count and tail disagree")
        if (self.last_failed_action_ref is None) != (self.last_failed_belief_ref is None):
            raise ValueError("reference checkpoint failed-action guard is incomplete")
        if self.assessment_count > self.admitted_action_count:
            raise ValueError("reference checkpoint assessments exceed admitted actions")
        if self.phase is SessionPhase.NEEDS_SCAN:
            if self.active_request_ref is not None or self.pending_proposal_ref is not None:
                raise ValueError("needs-scan reference checkpoint retains actionable state")
            if self.terminal_authority is not None:
                raise ValueError("needs-scan reference checkpoint claims terminal authority")
        elif self.phase is SessionPhase.READY_TO_ACT:
            if self.active_request_ref is None or self.pending_proposal_ref is not None:
                raise ValueError("ready reference checkpoint requires only an active request")
            if self.terminal_authority is not None:
                raise ValueError("ready reference checkpoint claims terminal authority")
        elif self.phase is SessionPhase.AWAITING_ASSESSMENT:
            if self.active_request_ref is None or self.pending_proposal_ref is None:
                raise ValueError("awaiting reference checkpoint requires request and proposal")
            if self.terminal_authority is not None:
                raise ValueError("awaiting reference checkpoint claims terminal authority")
        else:
            if self.active_request_ref is not None or self.pending_proposal_ref is not None:
                raise ValueError("terminal reference checkpoint retains actionable state")
            if self.terminal_authority not in {
                TerminalAuthority.SUCCESS,
                TerminalAuthority.BLOCKED,
            }:
                raise ValueError(
                    "terminal reference checkpoint requires final domain authority"
                )
        if self.completion_genuinely_observed != (
            self.phase is SessionPhase.TERMINAL
            and self.terminal_authority is TerminalAuthority.SUCCESS
        ):
            raise ValueError("reference checkpoint completion claim disagrees with authority")
        return self


def _checkpoint_from_ledger(
    ledger: SQLiteLedger, checkpoint_receipt_ref: str
) -> SessionCheckpoint | SessionCheckpointV2:
    """Resolve one checkpoint through only the ledger's public read surface."""

    ledger.verify()
    matches = tuple(
        envelope
        for envelope in ledger.receipts()
        if envelope.receipt_id == checkpoint_receipt_ref
    )
    if len(matches) != 1:
        raise RuntimeError("checkpoint receipt is absent or ambiguous in the ledger")
    envelope = matches[0]
    payload = ledger.get_payload(envelope.payload_hash)
    schema_id = payload.get("schema") if isinstance(payload, dict) else None
    try:
        if schema_id == "strongwiz.session-checkpoint.v2":
            checkpoint: SessionCheckpoint | SessionCheckpointV2 = (
                SessionCheckpointV2.model_validate(payload)
            )
        else:
            checkpoint = SessionCheckpoint.model_validate(payload)
    except ValueError as error:
        raise RuntimeError(
            "ledger receipt does not contain a valid session checkpoint"
        ) from error
    if checkpoint.digest != envelope.payload_hash:
        raise RuntimeError("checkpoint content identity disagrees with its ledger receipt")
    return checkpoint


def _validate_checkpoint_ledger(
    checkpoint: SessionCheckpoint,
    ledger: SQLiteLedger,
    *,
    checkpoint_receipt_ref: str | None,
) -> ReceiptEnvelope:
    """Bind a checkpoint to the latest durable receipt for exactly one session."""

    ledger.verify()
    envelopes = tuple(ledger.receipts())
    if checkpoint_receipt_ref is None:
        matches = tuple(
            envelope for envelope in envelopes if envelope.payload_hash == checkpoint.digest
        )
    else:
        matches = tuple(
            envelope for envelope in envelopes if envelope.receipt_id == checkpoint_receipt_ref
        )
    if len(matches) != 1:
        raise RuntimeError("checkpoint is absent or ambiguous in the supplied ledger")
    checkpoint_envelope = matches[0]
    if checkpoint_envelope.payload_hash != checkpoint.digest:
        raise RuntimeError("checkpoint receipt binds different checkpoint content")
    if (
        checkpoint_envelope.account_id != checkpoint.account_id
        or checkpoint_envelope.account_version != checkpoint.account_version
    ):
        raise RuntimeError("checkpoint ledger account binding disagrees with its state")
    expected_occurrence = (
        f"{checkpoint.session_id}:{len(checkpoint.ledger_receipt_refs):08d}:"
        f"{checkpoint_envelope.kind}"
    )
    if checkpoint_envelope.occurrence_id != expected_occurrence:
        raise RuntimeError("checkpoint occurrence identity is not the session boundary")

    session_envelopes: list[ReceiptEnvelope] = []
    for envelope in envelopes:
        payload = ledger.get_payload(envelope.payload_hash)
        if isinstance(payload, dict) and payload.get("session_id") == checkpoint.session_id:
            session_envelopes.append(envelope)
    prior = tuple(
        envelope
        for envelope in session_envelopes
        if envelope.sequence < checkpoint_envelope.sequence
    )
    later = tuple(
        envelope
        for envelope in session_envelopes
        if envelope.sequence > checkpoint_envelope.sequence
    )
    if tuple(envelope.receipt_id for envelope in prior) != checkpoint.ledger_receipt_refs:
        raise RuntimeError("checkpoint omits or reorders prior session ledger receipts")
    if any(
        envelope.account_id != checkpoint.account_id
        or envelope.account_version != checkpoint.account_version
        for envelope in prior
    ):
        raise RuntimeError("checkpoint prior receipts cross ledger account identity")
    if later:
        raise RuntimeError("cannot restore a stale checkpoint over later session receipts")
    expected_parents = (
        () if not checkpoint.ledger_receipt_refs else (checkpoint.ledger_receipt_refs[-1],)
    )
    if checkpoint_envelope.parent_refs != expected_parents:
        raise RuntimeError("checkpoint parent receipt does not bind the prior session state")

    expected_objects = {
        checkpoint.frozen_runtime_ref,
        *(scan.digest for scan in checkpoint.scans),
        *(decision.digest for decision in checkpoint.decisions),
        *(assessment.digest for assessment in checkpoint.assessments),
    }
    if checkpoint.active_request is not None:
        expected_objects.add(checkpoint.active_request.digest)
    if checkpoint.pending_proposal is not None:
        expected_objects.add(checkpoint.pending_proposal.digest)
    if not expected_objects.issubset(set(checkpoint_envelope.object_refs)):
        raise RuntimeError("checkpoint receipt omits restart-critical content objects")
    stored = SessionCheckpoint.model_validate(
        ledger.get_payload(checkpoint_envelope.payload_hash)
    )
    if stored != checkpoint:
        raise RuntimeError("checkpoint object differs from the durable ledger payload")
    return checkpoint_envelope


def _validate_reference_checkpoint_ledger(
    checkpoint: SessionCheckpointV2,
    ledger: SQLiteLedger,
    *,
    checkpoint_receipt_ref: str | None,
) -> tuple[SessionCheckpoint, ReceiptEnvelope]:
    """Expand a V2 checkpoint from its exact predecessor-linked ledger history."""

    ledger.verify()
    envelopes = tuple(ledger.receipts())
    if checkpoint_receipt_ref is None:
        matches = tuple(
            envelope for envelope in envelopes if envelope.payload_hash == checkpoint.digest
        )
    else:
        matches = tuple(
            envelope for envelope in envelopes if envelope.receipt_id == checkpoint_receipt_ref
        )
    if len(matches) != 1:
        raise RuntimeError("reference checkpoint is absent or ambiguous in the ledger")
    checkpoint_envelope = matches[0]
    if checkpoint_envelope.payload_hash != checkpoint.digest:
        raise RuntimeError("reference checkpoint receipt binds different content")
    if (
        checkpoint_envelope.account_id != checkpoint.account_id
        or checkpoint_envelope.account_version != checkpoint.account_version
    ):
        raise RuntimeError("reference checkpoint ledger account binding disagrees")
    expected_occurrence = (
        f"{checkpoint.session_id}:{checkpoint.history_receipt_count:08d}:"
        f"{checkpoint_envelope.kind}"
    )
    if checkpoint_envelope.occurrence_id != expected_occurrence:
        raise RuntimeError("reference checkpoint occurrence is not the session boundary")

    session_envelopes: list[ReceiptEnvelope] = []
    for envelope in envelopes:
        payload = ledger.get_payload(envelope.payload_hash)
        if isinstance(payload, dict) and payload.get("session_id") == checkpoint.session_id:
            session_envelopes.append(envelope)
    prior = tuple(
        envelope
        for envelope in session_envelopes
        if envelope.sequence < checkpoint_envelope.sequence
    )
    later = tuple(
        envelope
        for envelope in session_envelopes
        if envelope.sequence > checkpoint_envelope.sequence
    )
    if later:
        raise RuntimeError("cannot restore a stale checkpoint over later session receipts")
    if len(prior) != checkpoint.history_receipt_count:
        raise RuntimeError("reference checkpoint history count disagrees with the ledger")
    prior_head = None if not prior else prior[-1].receipt_id
    if checkpoint.history_receipt_head != prior_head:
        raise RuntimeError("reference checkpoint history tail disagrees with the ledger")
    if checkpoint_envelope.parent_refs != (() if prior_head is None else (prior_head,)):
        raise RuntimeError("reference checkpoint does not bind its exact history tail")
    for index, envelope in enumerate(prior):
        expected_parent = () if index == 0 else (prior[index - 1].receipt_id,)
        if envelope.parent_refs != expected_parent:
            raise RuntimeError("reference checkpoint predecessor lineage is broken")
        if (
            envelope.account_id != checkpoint.account_id
            or envelope.account_version != checkpoint.account_version
        ):
            raise RuntimeError("reference checkpoint history crosses ledger account identity")

    expected_objects = {checkpoint.frozen_runtime_ref}
    if checkpoint.active_request_ref is not None:
        expected_objects.add(checkpoint.active_request_ref)
    if checkpoint.pending_proposal_ref is not None:
        expected_objects.add(checkpoint.pending_proposal_ref)
    if not expected_objects.issubset(set(checkpoint_envelope.object_refs)):
        raise RuntimeError("reference checkpoint omits restart-critical objects")

    scans: list[ScanReceipt] = []
    decisions: list[DecisionRecord] = []
    assessments: list[AssessmentRecord] = []
    for envelope in prior:
        payload = ledger.get_payload(envelope.payload_hash)
        try:
            if envelope.kind == "scan":
                scans.append(ScanReceipt.model_validate(payload))
            elif envelope.kind == "decision":
                decisions.append(DecisionRecord.model_validate(payload))
            elif envelope.kind == "assessment":
                assessments.append(AssessmentRecord.model_validate(payload))
        except ValueError as error:
            raise RuntimeError(
                "reference checkpoint history contains an invalid record"
            ) from error
    if (
        len(scans) != checkpoint.scan_count
        or len(decisions) != checkpoint.decision_count
        or len(assessments) != checkpoint.assessment_count
    ):
        raise RuntimeError("reference checkpoint typed history counts disagree")

    active_request = None
    if checkpoint.active_request_ref is not None:
        try:
            active_request = ReasoningRequest.model_validate(
                ledger.get_payload(checkpoint.active_request_ref)
            )
        except ValueError as error:
            raise RuntimeError("reference checkpoint active request is invalid") from error
    pending_proposal = None
    if checkpoint.pending_proposal_ref is not None:
        try:
            pending_proposal = CandidateProposal.model_validate(
                ledger.get_payload(checkpoint.pending_proposal_ref)
            )
        except ValueError as error:
            raise RuntimeError("reference checkpoint pending proposal is invalid") from error

    expanded = SessionCheckpoint(
        session_id=checkpoint.session_id,
        driver_id=checkpoint.driver_id,
        domain_adapter_id=checkpoint.domain_adapter_id,
        frozen_runtime_ref=checkpoint.frozen_runtime_ref,
        router_policy_ref=checkpoint.router_policy_ref,
        cadence_policy_ref=checkpoint.cadence_policy_ref,
        governing_goal_ref=checkpoint.governing_goal_ref,
        phase=checkpoint.phase,
        terminal_authority=checkpoint.terminal_authority,
        scans=tuple(scans),
        decisions=tuple(decisions),
        assessments=tuple(assessments),
        admitted_action_count=checkpoint.admitted_action_count,
        completion_genuinely_observed=checkpoint.completion_genuinely_observed,
        ledger_receipt_refs=tuple(envelope.receipt_id for envelope in prior),
        limitations=checkpoint.limitations,
        active_request=active_request,
        pending_proposal=pending_proposal,
        last_failed_action_ref=checkpoint.last_failed_action_ref,
        last_failed_belief_ref=checkpoint.last_failed_belief_ref,
        account_id=checkpoint.account_id,
        account_version=checkpoint.account_version,
    )
    return expanded, checkpoint_envelope


class ReasoningSession:
    """Enforce no stale action, predicted consequence, and post-action assessment."""

    def __init__(
        self,
        *,
        session_id: str,
        model_driver: ModelDriver,
        domain_adapter: DomainAdapter,
        governing_goal_ref: str,
        frozen_runtime: FrozenRuntimeManifest,
        router_policy: RouterPolicy | None = None,
        cadence_policy: CadencePolicy | None = None,
        ledger: SQLiteLedger | None = None,
        account_id: str | None = None,
        account_version: int = 0,
    ) -> None:
        if not all(
            (
                session_id,
                model_driver.driver_id,
                model_driver.driver_version,
                model_driver.driver_artifact_ref,
                domain_adapter.adapter_id,
                domain_adapter.adapter_version,
                domain_adapter.adapter_artifact_ref,
                governing_goal_ref,
            )
        ):
            raise RuntimeError("session, driver, domain, and governing goal are required")
        active_router = router_policy or RouterPolicy()
        active_cadence = cadence_policy or CadencePolicy()
        if (
            frozen_runtime.contract_schema != CONTRACT_SCHEMA
            or frozen_runtime.model_driver_id != model_driver.driver_id
            or frozen_runtime.model_driver_version != model_driver.driver_version
            or frozen_runtime.model_driver_artifact_ref != model_driver.driver_artifact_ref
            or frozen_runtime.domain_adapter_id != domain_adapter.adapter_id
            or frozen_runtime.domain_adapter_version != domain_adapter.adapter_version
            or frozen_runtime.domain_adapter_artifact_ref != domain_adapter.adapter_artifact_ref
            or active_router.digest not in frozen_runtime.policy_refs
            or active_cadence.digest not in frozen_runtime.policy_refs
        ):
            raise RuntimeError(
                "frozen runtime does not bind this contract, driver, domain, and policies"
            )
        self.session_id = session_id
        self.driver_id = model_driver.driver_id
        self._model_driver = model_driver
        self.domain_adapter_id = domain_adapter.adapter_id
        self._domain_adapter = domain_adapter
        self._frozen_runtime = frozen_runtime
        self.frozen_runtime_ref = frozen_runtime.manifest_ref
        self.governing_goal_ref = governing_goal_ref
        self.router_policy = active_router
        self.cadence_policy = active_cadence
        self._router_policy_ref = active_router.digest
        self._cadence_policy_ref = active_cadence.digest
        self.phase = SessionPhase.NEEDS_SCAN
        self._request: ReasoningRequest | None = None
        self._pending: CandidateProposal | None = None
        self._terminal: TerminalAuthority | None = None
        self._scans: list[ScanReceipt] = []
        self._decisions: list[DecisionRecord] = []
        self._assessments: list[AssessmentRecord] = []
        self._last_failed_signature: tuple[str, str] | None = None
        self._ledger = ledger
        self._runtime_object_ref: str | None = None
        if ledger is not None:
            runtime_ref = ledger.put_object(
                frozen_runtime.model_dump(mode="json", by_alias=True)
            )
            if runtime_ref != self.frozen_runtime_ref:
                raise RuntimeError("stored frozen-runtime identity disagrees with its digest")
            self._runtime_object_ref = runtime_ref
        self._account_id = account_id or session_id
        self._account_version = account_version
        self._ledger_receipt_refs: list[str] = []

    @classmethod
    def restore(
        cls,
        checkpoint: SessionCheckpoint | SessionCheckpointV2,
        *,
        model_driver: ModelDriver,
        domain_adapter: DomainAdapter,
        frozen_runtime: FrozenRuntimeManifest,
        router_policy: RouterPolicy | None = None,
        cadence_policy: CadencePolicy | None = None,
        ledger: SQLiteLedger | None = None,
        checkpoint_receipt_ref: str | None = None,
    ) -> ReasoningSession:
        """Restore one checkpoint without rerunning model or environment work."""

        active_router = router_policy or RouterPolicy()
        active_cadence = cadence_policy or CadencePolicy()
        checkpoint_envelope: ReceiptEnvelope | None = None
        if isinstance(checkpoint, SessionCheckpointV2):
            if ledger is None:
                raise RuntimeError(
                    "a reference-normalized checkpoint requires its original ledger"
                )
            checkpoint, checkpoint_envelope = _validate_reference_checkpoint_ledger(
                checkpoint,
                ledger,
                checkpoint_receipt_ref=checkpoint_receipt_ref,
            )
        if (
            checkpoint.frozen_runtime_ref != frozen_runtime.manifest_ref
            or checkpoint.driver_id != model_driver.driver_id
            or checkpoint.domain_adapter_id != domain_adapter.adapter_id
            or checkpoint.router_policy_ref != active_router.digest
            or checkpoint.cadence_policy_ref != active_cadence.digest
            or active_router.digest not in frozen_runtime.policy_refs
            or active_cadence.digest not in frozen_runtime.policy_refs
        ):
            raise RuntimeError(
                "checkpoint does not bind the supplied runtime, driver, domain, and policies"
            )
        if ledger is not None:
            if checkpoint_envelope is None:
                checkpoint_envelope = _validate_checkpoint_ledger(
                    checkpoint,
                    ledger,
                    checkpoint_receipt_ref=checkpoint_receipt_ref,
                )
        elif checkpoint_receipt_ref is not None:
            raise RuntimeError("a checkpoint receipt reference requires its ledger")
        elif checkpoint.ledger_receipt_refs:
            raise RuntimeError(
                "a checkpoint with durable receipt lineage requires its original ledger"
            )

        restored = cls(
            session_id=checkpoint.session_id,
            model_driver=model_driver,
            domain_adapter=domain_adapter,
            governing_goal_ref=checkpoint.governing_goal_ref,
            frozen_runtime=frozen_runtime,
            router_policy=active_router,
            cadence_policy=active_cadence,
            ledger=ledger,
            account_id=checkpoint.account_id,
            account_version=checkpoint.account_version,
        )
        restored.phase = checkpoint.phase
        restored._request = checkpoint.active_request
        restored._pending = checkpoint.pending_proposal
        restored._terminal = checkpoint.terminal_authority
        restored._scans = list(checkpoint.scans)
        restored._decisions = list(checkpoint.decisions)
        restored._assessments = list(checkpoint.assessments)
        if checkpoint.last_failed_action_ref is None:
            restored._last_failed_signature = None
        else:
            failed_belief_ref = checkpoint.last_failed_belief_ref
            if failed_belief_ref is None:
                raise RuntimeError("checkpoint lost half of its failed-action guard")
            restored._last_failed_signature = (
                checkpoint.last_failed_action_ref,
                failed_belief_ref,
            )
        restored._ledger_receipt_refs = list(checkpoint.ledger_receipt_refs)
        if checkpoint_envelope is not None:
            restored._ledger_receipt_refs.append(checkpoint_envelope.receipt_id)
        return restored

    def _require_frozen_model(self) -> None:
        frozen = self._frozen_runtime
        if (
            self._model_driver.driver_id != frozen.model_driver_id
            or self._model_driver.driver_version != frozen.model_driver_version
            or self._model_driver.driver_artifact_ref != frozen.model_driver_artifact_ref
        ):
            raise RuntimeError("model driver identity drifted from the frozen runtime")

    def _require_frozen_domain(self) -> None:
        frozen = self._frozen_runtime
        if (
            self._domain_adapter.adapter_id != frozen.domain_adapter_id
            or self._domain_adapter.adapter_version != frozen.domain_adapter_version
            or self._domain_adapter.adapter_artifact_ref != frozen.domain_adapter_artifact_ref
        ):
            raise RuntimeError("domain adapter identity drifted from the frozen runtime")

    def _require_frozen_policies(self) -> None:
        if (
            self.router_policy.digest != self._router_policy_ref
            or self.cadence_policy.digest != self._cadence_policy_ref
        ):
            raise RuntimeError("reasoning policy identity drifted from the frozen runtime")

    def _store(self, *values: ContractModel) -> tuple[str, ...]:
        if self._ledger is None:
            return ()
        refs: list[str] = []
        for value in values:
            stored_ref = self._ledger.put_object(value.model_dump(mode="json", by_alias=True))
            if stored_ref != value.digest:
                raise RuntimeError("stored contract identity disagrees with its digest")
            if stored_ref not in refs:
                refs.append(stored_ref)
        return tuple(refs)

    def _record(
        self,
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str | None:
        if self._ledger is None:
            return None
        parent_refs = () if not self._ledger_receipt_refs else (self._ledger_receipt_refs[-1],)
        bound_object_refs = tuple(
            dict.fromkeys(
                (
                    *(() if self._runtime_object_ref is None else (self._runtime_object_ref,)),
                    *object_refs,
                )
            )
        )
        envelope = self._ledger.append(
            occurrence_id=(f"{self.session_id}:{len(self._ledger_receipt_refs):08d}:{kind}"),
            kind=kind,
            account_id=self._account_id,
            account_version=self._account_version,
            payload=value.model_dump(mode="json", by_alias=True),
            object_refs=bound_object_refs,
            parent_refs=parent_refs,
        )
        self._ledger_receipt_refs.append(envelope.receipt_id)
        return envelope.receipt_id

    def scan(self, request: ReasoningRequest) -> ScanReceipt:
        if self.phase is SessionPhase.AWAITING_ASSESSMENT:
            raise RuntimeError("pending action must be assessed before a new scan")
        if self.phase is SessionPhase.TERMINAL:
            raise RuntimeError("terminal session cannot scan")
        if request.governing_goal.digest != self.governing_goal_ref:
            raise RuntimeError("request does not bind the session governing goal")
        receipt = ScanReceipt(
            session_id=self.session_id,
            observation_ref=request.observation.digest,
            request_ref=request.digest,
            distinction_refs=tuple(item.digest for item in request.active_distinctions),
            retained_fact_refs=request.retained_fact_refs,
            phase_before=self.phase,
            phase_after=SessionPhase.READY_TO_ACT,
        )
        object_refs = self._store(
            request.observation,
            request.governing_goal,
            request.scoped_goal,
            *request.active_distinctions,
            request,
        )
        self._record("scan", receipt, object_refs=object_refs)
        self._request = request
        self.phase = SessionPhase.READY_TO_ACT
        self._scans.append(receipt)
        return receipt

    def decide(
        self,
        control: ControlSnapshot,
        *,
        cadence_signals: CadenceSignals | None = None,
        credible_plan_supported: bool = False,
        uncertainty_blocks_progress: bool = True,
    ) -> DecisionRecord:
        if self.phase is not SessionPhase.READY_TO_ACT or self._request is None:
            raise RuntimeError("decision requires a fresh completed scan")
        self._require_frozen_model()
        self._require_frozen_policies()
        if (
            control.observation_id != self._request.observation.observation_id
            or control.observation_ref != self._request.observation.digest
            or control.scope_id != self._request.observation.scope_id
            or not control.contains_goal(
                self._request.scoped_goal.goal_id, self._request.scoped_goal.digest
            )
        ):
            raise RuntimeError("control does not bind the current observation and scoped goal")
        proposals = tuple(self._model_driver.propose(self._request))
        self._require_frozen_model()
        self._require_frozen_policies()
        proposal_ids = tuple(proposal.proposal_id for proposal in proposals)
        if len(set(proposal_ids)) != len(proposal_ids):
            raise RuntimeError("driver returned duplicate proposal identities")
        for proposal in proposals:
            if proposal.model_driver_id != self._model_driver.driver_id:
                raise RuntimeError("proposal cannot impersonate another model driver")
            if proposal.observation_id != self._request.observation.observation_id:
                raise RuntimeError("driver proposed against a stale observation")
            if proposal.observation_ref != self._request.observation.digest:
                raise RuntimeError("driver proposed against altered observation content")
            if (
                proposal.goal_id != self._request.scoped_goal.goal_id
                or proposal.goal_ref != self._request.scoped_goal.digest
            ):
                raise RuntimeError("driver proposal spliced or replaced the scoped goal")
        mode = action_mode(
            credible_plan_supported=credible_plan_supported,
            uncertainty_blocks_progress=uncertainty_blocks_progress,
        )
        cadence = self.cadence_policy.select(cadence_signals or CadenceSignals())
        route = select_route(
            proposals,
            control,
            policy=self.router_policy,
            prefer_information=mode is DeliberationMode.INVESTIGATE,
        )
        selected = next(
            (
                proposal
                for proposal in proposals
                if proposal.digest == route.selected_proposal_ref
            ),
            None,
        )
        if route.selected_proposal_ref is not None and selected is None:
            raise RuntimeError("router selected an unknown proposal")
        belief_ref = content_hash(
            {
                "distinctions": [item.digest for item in self._request.active_distinctions],
                "facts": list(self._request.retained_fact_refs),
                "goal": self._request.scoped_goal.digest,
            }
        )
        if selected is not None:
            signature = (selected.action.digest, belief_ref)
            if signature == self._last_failed_signature:
                raise RuntimeError(
                    "identical failed action is blocked under materially unchanged beliefs"
                )
        record = DecisionRecord(
            session_id=self.session_id,
            request_ref=self._request.digest,
            driver_id=self._model_driver.driver_id,
            candidate_refs=tuple(proposal.digest for proposal in proposals),
            selected_proposal_ref=None if selected is None else selected.digest,
            route=route,
            cadence=cadence,
            mode=mode,
            alternatives_considered=tuple(proposal.proposal_id for proposal in proposals),
            concise_selection_summary=(
                "no proposal passed the control-owned route"
                if selected is None
                else (
                    "selected the smallest declared discriminating candidate"
                    if mode is DeliberationMode.INVESTIGATE
                    else "selected the shortest declared credible progress candidate"
                )
            ),
        )
        object_refs = self._store(control, *proposals, route)
        self._record("decision", record, object_refs=object_refs)
        if selected is not None:
            self._pending = selected
            self.phase = SessionPhase.AWAITING_ASSESSMENT
        self._decisions.append(record)
        return record

    def assess(
        self,
        execution: ExecutionCallResult,
        *,
        matched_prediction_items: tuple[str, ...],
        residual_refs: tuple[str, ...],
        preserved_hypothesis_refs: tuple[str, ...],
        revised_hypothesis_refs: tuple[str, ...],
        concise_update_summary: str,
    ) -> AssessmentRecord:
        if self.phase is not SessionPhase.AWAITING_ASSESSMENT or self._pending is None:
            raise RuntimeError("assessment requires one pending selected proposal")
        proposal = self._pending
        request = self._request
        if request is None:
            raise RuntimeError("session lost its active request")
        if not self._decisions:
            raise RuntimeError("session lost the decision that admitted the pending action")
        release = execution.release
        attempt = execution.attempt
        admission = execution.admission
        executor_observation = execution.observation
        decision = self._decisions[-1]
        if (
            not execution.coordinator_issued
            or decision.selected_proposal_ref != proposal.digest
            or admission.route_ref != decision.route.digest
            or admission.control_ref != decision.route.control_ref
            or release.status is not ReleaseStatus.RELEASED
            or attempt.disposition is not ExecutionDisposition.COMPLETED
            or executor_observation is None
            or admission.proposal_ref != proposal.digest
            or admission.action_ref != proposal.action.digest
            or admission.observation_id != proposal.observation_id
            or admission.observation_ref != proposal.observation_ref
            or admission.scope_id != proposal.scope_id
            or release.grant_ref != admission.grant_ref
            or release.invocation_id != admission.invocation_id
            or release.route_ref != admission.route_ref
            or release.control_ref != admission.control_ref
            or release.lab_decision_ref != admission.lab_decision_ref
            or release.proposal_ref != proposal.digest
            or release.action_ref != proposal.action.digest
            or release.action_name != proposal.action.name
            or release.executor_id != admission.executor_id
            or release.executor_version != admission.executor_version
            or release.executor_artifact_ref != admission.executor_artifact_ref
            or release.observation_id != proposal.observation_id
            or release.observation_ref != proposal.observation_ref
            or release.scope_id != proposal.scope_id
            or release.candidate_ref != proposal.digest
            or attempt.admission_ref != admission.digest
            or attempt.release_ref != release.digest
            or attempt.proposal_ref != proposal.digest
            or attempt.action_ref != proposal.action.digest
            or attempt.executor_id != release.executor_id
            or attempt.executor_version != release.executor_version
            or attempt.executor_artifact_ref != release.executor_artifact_ref
            or attempt.idempotency_key != admission.digest
            or attempt.result_evidence_ref != executor_observation.evidence_ref.sha256
        ):
            raise RuntimeError("assessment requires the exact completed execution evidence")
        raw_after = executor_observation.raw_after
        self._require_frozen_domain()
        observation_after = self._domain_adapter.normalize_observation(raw_after)
        self._require_frozen_domain()
        if not isinstance(observation_after, Observation):
            raise RuntimeError("domain adapter returned an invalid normalized observation")
        self._require_frozen_domain()
        outcome = self._domain_adapter.extract_outcome(
            request.observation, proposal.action, raw_after
        )
        self._require_frozen_domain()
        if not isinstance(outcome, Outcome):
            raise RuntimeError("domain adapter returned an invalid outcome")
        self._require_frozen_domain()
        terminal_authority = self._domain_adapter.terminal_authority(observation_after)
        self._require_frozen_domain()
        if not isinstance(terminal_authority, TerminalAuthority):
            raise RuntimeError("domain adapter returned an invalid terminal authority")
        if outcome.observation_before_id != proposal.observation_id:
            raise RuntimeError("outcome does not bind the acted-on observation")
        if outcome.observation_before_ref != proposal.observation_ref:
            raise RuntimeError("outcome does not bind the acted-on observation content")
        if outcome.observation_after_id != observation_after.observation_id:
            raise RuntimeError("outcome does not bind the normalized after-observation")
        if outcome.observation_after_ref != observation_after.digest:
            raise RuntimeError("outcome does not bind the normalized after-observation content")
        if outcome.action != proposal.action:
            raise RuntimeError("outcome action disagrees with the selected proposal")
        if (
            terminal_authority
            in {
                TerminalAuthority.SUCCESS,
                TerminalAuthority.BLOCKED,
            }
            and not outcome.terminal
        ):
            raise RuntimeError("terminal authority disagrees with the outcome terminal marker")
        if terminal_authority is TerminalAuthority.CONTINUE and outcome.terminal:
            raise RuntimeError(
                "nonterminal authority disagrees with the outcome terminal marker"
            )
        next_terminal = self._terminal
        next_failed_signature = self._last_failed_signature
        if terminal_authority in {TerminalAuthority.SUCCESS, TerminalAuthority.BLOCKED}:
            next_phase = SessionPhase.TERMINAL
            next_terminal = terminal_authority
        else:
            next_phase = SessionPhase.NEEDS_SCAN
            if terminal_authority is TerminalAuthority.FAILURE:
                belief_ref = content_hash(
                    {
                        "distinctions": [item.digest for item in request.active_distinctions],
                        "facts": list(request.retained_fact_refs),
                        "goal": request.scoped_goal.digest,
                    }
                )
                next_failed_signature = (proposal.action.digest, belief_ref)
        record = AssessmentRecord(
            session_id=self.session_id,
            proposal_ref=proposal.digest,
            execution_admission_ref=admission.digest,
            release_ref=release.digest,
            execution_attempt_ref=attempt.digest,
            executor_evidence_ref=executor_observation.evidence_ref.sha256,
            outcome_ref=outcome.digest,
            observation_after_ref=observation_after.digest,
            terminal_authority=terminal_authority,
            matched_prediction_items=matched_prediction_items,
            residual_refs=residual_refs,
            preserved_hypothesis_refs=preserved_hypothesis_refs,
            revised_hypothesis_refs=revised_hypothesis_refs,
            concise_update_summary=concise_update_summary,
            phase_after=next_phase,
        )
        object_refs = self._store(
            proposal,
            admission,
            release,
            attempt,
            executor_observation.evidence_ref,
            observation_after,
            outcome,
        )
        self._record("assessment", record, object_refs=object_refs)
        self._pending = None
        self._request = None
        self.phase = next_phase
        self._terminal = next_terminal
        self._last_failed_signature = next_failed_signature
        self._assessments.append(record)
        if next_phase is SessionPhase.TERMINAL:
            self.checkpoint(kind="session_terminal")
        return record

    def checkpoint(self, *, kind: str = "session_checkpoint") -> str | None:
        """Append one replay-complete session snapshot without changing session state."""

        if self._ledger is None:
            return None
        snapshot = self.reference_checkpoint_snapshot()
        values: list[ContractModel] = []
        if self._request is not None:
            values.append(self._request)
        if self._pending is not None:
            values.append(self._pending)
        object_refs = self._store(*values)
        return self._record(kind, snapshot, object_refs=object_refs)

    def reference_checkpoint_snapshot(self) -> SessionCheckpointV2:
        """Return compact durable state; history stays in predecessor receipts."""

        failed_action_ref: str | None = None
        failed_belief_ref: str | None = None
        if self._last_failed_signature is not None:
            failed_action_ref, failed_belief_ref = self._last_failed_signature
        admitted = sum(
            decision.route.disposition in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}
            for decision in self._decisions
        )
        return SessionCheckpointV2(
            session_id=self.session_id,
            driver_id=self.driver_id,
            domain_adapter_id=self.domain_adapter_id,
            frozen_runtime_ref=self.frozen_runtime_ref,
            router_policy_ref=self.router_policy.digest,
            cadence_policy_ref=self.cadence_policy.digest,
            governing_goal_ref=self.governing_goal_ref,
            phase=self.phase,
            terminal_authority=self._terminal,
            history_receipt_count=len(self._ledger_receipt_refs),
            history_receipt_head=(
                None if not self._ledger_receipt_refs else self._ledger_receipt_refs[-1]
            ),
            scan_count=len(self._scans),
            decision_count=len(self._decisions),
            assessment_count=len(self._assessments),
            admitted_action_count=admitted,
            completion_genuinely_observed=self._terminal is TerminalAuthority.SUCCESS,
            active_request_ref=None if self._request is None else self._request.digest,
            pending_proposal_ref=None if self._pending is None else self._pending.digest,
            last_failed_action_ref=failed_action_ref,
            last_failed_belief_ref=failed_belief_ref,
            account_id=self._account_id,
            account_version=self._account_version,
        )

    def checkpoint_snapshot(self) -> SessionCheckpoint:
        """Return the exact immutable state that :meth:`checkpoint` persists."""

        receipt = self.receipt()
        failed_action_ref: str | None = None
        failed_belief_ref: str | None = None
        if self._last_failed_signature is not None:
            failed_action_ref, failed_belief_ref = self._last_failed_signature
        return SessionCheckpoint(
            session_id=receipt.session_id,
            driver_id=receipt.driver_id,
            domain_adapter_id=receipt.domain_adapter_id,
            frozen_runtime_ref=receipt.frozen_runtime_ref,
            router_policy_ref=receipt.router_policy_ref,
            cadence_policy_ref=receipt.cadence_policy_ref,
            governing_goal_ref=receipt.governing_goal_ref,
            phase=receipt.phase,
            terminal_authority=receipt.terminal_authority,
            scans=receipt.scans,
            decisions=receipt.decisions,
            assessments=receipt.assessments,
            admitted_action_count=receipt.admitted_action_count,
            completion_genuinely_observed=receipt.completion_genuinely_observed,
            ledger_receipt_refs=receipt.ledger_receipt_refs,
            limitations=receipt.limitations,
            active_request=self._request,
            pending_proposal=self._pending,
            last_failed_action_ref=failed_action_ref,
            last_failed_belief_ref=failed_belief_ref,
            account_id=self._account_id,
            account_version=self._account_version,
        )

    def receipt(self) -> SessionReceipt:
        admitted = sum(
            decision.route.disposition in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}
            for decision in self._decisions
        )
        return SessionReceipt(
            session_id=self.session_id,
            driver_id=self.driver_id,
            domain_adapter_id=self.domain_adapter_id,
            frozen_runtime_ref=self.frozen_runtime_ref,
            router_policy_ref=self.router_policy.digest,
            cadence_policy_ref=self.cadence_policy.digest,
            governing_goal_ref=self.governing_goal_ref,
            phase=self.phase,
            terminal_authority=self._terminal,
            scans=tuple(self._scans),
            decisions=tuple(self._decisions),
            assessments=tuple(self._assessments),
            admitted_action_count=admitted,
            completion_genuinely_observed=self._terminal is TerminalAuthority.SUCCESS,
            ledger_receipt_refs=tuple(self._ledger_receipt_refs),
        )


class StrongwizKernel:
    """Composition root for replaceable drivers and independent sessions."""

    def __init__(self, registry: DriverRegistry | None = None) -> None:
        self.registry = registry or DriverRegistry()

    def new_session(
        self,
        *,
        session_id: str,
        driver_id: str,
        domain_adapter_id: str,
        governing_goal_ref: str,
        frozen_runtime: FrozenRuntimeManifest,
        ledger: SQLiteLedger | None = None,
        account_id: str | None = None,
        account_version: int = 0,
    ) -> ReasoningSession:
        driver = self.registry.model(driver_id)
        adapter = self.registry.domain(domain_adapter_id)
        return ReasoningSession(
            session_id=session_id,
            model_driver=driver,
            domain_adapter=adapter,
            governing_goal_ref=governing_goal_ref,
            frozen_runtime=frozen_runtime,
            ledger=ledger,
            account_id=account_id,
            account_version=account_version,
        )

    def restore_session(
        self,
        *,
        checkpoint: SessionCheckpoint | SessionCheckpointV2 | str,
        frozen_runtime: FrozenRuntimeManifest,
        ledger: SQLiteLedger | None = None,
        router_policy: RouterPolicy | None = None,
        cadence_policy: CadencePolicy | None = None,
    ) -> ReasoningSession:
        """Restore a frozen session checkpoint through registered adapters.

        Passing a receipt reference requires the original ledger.  Passing an
        in-memory checkpoint supports transport into a fresh process; when a
        ledger is also supplied, the checkpoint must be its latest receipt for
        that session.
        """

        checkpoint_receipt_ref: str | None = None
        if isinstance(checkpoint, str):
            if ledger is None:
                raise RuntimeError("checkpoint receipt restoration requires a ledger")
            checkpoint_receipt_ref = checkpoint
            snapshot = _checkpoint_from_ledger(ledger, checkpoint)
        else:
            snapshot = checkpoint
        driver = self.registry.model(snapshot.driver_id)
        adapter = self.registry.domain(snapshot.domain_adapter_id)
        return ReasoningSession.restore(
            snapshot,
            model_driver=driver,
            domain_adapter=adapter,
            frozen_runtime=frozen_runtime,
            router_policy=router_policy,
            cadence_policy=cadence_policy,
            ledger=ledger,
            checkpoint_receipt_ref=checkpoint_receipt_ref,
        )
