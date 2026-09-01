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
    Observation,
    Outcome,
    ReasoningRequest,
    RouteDecision,
    RouteDisposition,
)
from strongwiz.drivers import DomainAdapter, DriverRegistry, ModelDriver, TerminalAuthority
from strongwiz.integrity import FrozenRuntimeManifest
from strongwiz.ledger import SQLiteLedger
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

        snapshot = self.receipt()
        object_refs = self._store(
            *snapshot.scans,
            *snapshot.decisions,
            *snapshot.assessments,
        )
        return self._record(kind, snapshot, object_refs=object_refs)

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
