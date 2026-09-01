from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.authority import GrantRegistry, GrantSource, TaskGrant
from strongwiz.contracts import (
    ActionSpec,
    CandidateProposal,
    ControlSnapshot,
    EvidenceRef,
    Observation,
    Outcome,
    RouteDecision,
    RouteDisposition,
)
from strongwiz.domains.arc_agi3 import (
    ArcGameState,
    ArcRunReceipt,
    legal_actions_after,
    terminal_authority,
)
from strongwiz.drivers import ExecutionCommand, ExecutorObservation, TerminalAuthority
from strongwiz.lab_policy import (
    ConsequentialCrossing,
    CrossingStage,
    LabBoundaryContext,
    LabPolicyDecision,
    PEAReview,
    ReviewStatus,
    evaluate_lab_rules,
)
from strongwiz.ledger import LedgerError, SQLiteLedger
from strongwiz.orchestration import (
    ExecutionCallResult,
    ExecutionCoordinator,
    ExecutionDisposition,
)
from strongwiz.policy import CadenceSignals, ReasoningDepth
from strongwiz.routing import RouterPolicy, evaluate_proposal
from strongwiz.runtime import ReasoningSession, RuntimeError, SessionPhase
from tests.support import (
    control,
    evidence,
    frozen_runtime,
    governing_goal,
    proposal,
    ref,
    request,
)


class FixedDriver:
    driver_id = "driver-test"
    driver_version = "driver-v1"
    driver_artifact_ref = ref("driver-artifact")

    def __init__(self, proposals: tuple[CandidateProposal, ...]) -> None:
        self._proposals = proposals

    def propose(self, _request: object) -> Sequence[CandidateProposal]:
        return self._proposals


class ReplacementDriver(FixedDriver):
    driver_artifact_ref = ref("replacement-driver-artifact")


@dataclass(frozen=True)
class SyntheticRawAfter:
    observation: Observation
    outcome: Outcome


class SyntheticDomain:
    adapter_id = "synthetic"
    adapter_version = "adapter-v1"
    adapter_artifact_ref = ref("adapter-artifact")

    def normalize_observation(self, raw: object) -> Observation:
        if not isinstance(raw, SyntheticRawAfter):
            raise TypeError("expected synthetic raw result")
        return raw.observation

    def available_actions(self, _observation: Observation) -> tuple[ActionSpec, ...]:
        return (ActionSpec(name="inspect"), ActionSpec(name="open"))

    def extract_outcome(
        self, _before: Observation, _action: ActionSpec, raw_after: object
    ) -> Outcome:
        if not isinstance(raw_after, SyntheticRawAfter):
            raise TypeError("expected synthetic raw result")
        return raw_after.outcome

    def terminal_authority(self, observation: Observation) -> TerminalAuthority:
        return TerminalAuthority[observation.summary]


def session(
    proposals: tuple[CandidateProposal, ...] | None = None,
) -> ReasoningSession:
    return ReasoningSession(
        session_id="session-1",
        model_driver=FixedDriver((proposal(),) if proposals is None else proposals),
        domain_adapter=SyntheticDomain(),
        governing_goal_ref=governing_goal().digest,
        frozen_runtime=frozen_runtime(),
    )


def after_observation(state: str) -> Observation:
    return Observation(
        observation_id="obs-2",
        domain="synthetic",
        scope_id="scope-1",
        epoch=1,
        payload_ref=evidence(f"after-{state}"),
        summary=state,
        available_action_names=("inspect", "open"),
    )


def outcome_for(candidate: CandidateProposal, *, state: str = "CONTINUE") -> Outcome:
    after = after_observation(state)
    return Outcome(
        outcome_id=f"outcome-{state}",
        observation_before_id=candidate.observation_id,
        observation_before_ref=candidate.observation_ref,
        observation_after_id="obs-2",
        observation_after_ref=after.digest,
        action=candidate.action,
        observed_consequences=("latch state became visible",),
        state_label=state,
        evidence_refs=(ref(f"outcome-{state}"),),
        terminal=state != "CONTINUE",
    )


def raw_after_for(
    candidate: CandidateProposal, *, state: str = "CONTINUE"
) -> SyntheticRawAfter:
    return SyntheticRawAfter(
        observation=after_observation(state),
        outcome=outcome_for(candidate, state=state),
    )


class FixtureExecutor:
    executor_id = "executor"
    executor_version = "executor-v1"
    executor_artifact_ref = ref("executor-artifact")

    def __init__(self, result: SyntheticRawAfter, evidence_ref: EvidenceRef) -> None:
        self._result = result
        self._evidence_ref = evidence_ref

    def execute(self, _command: ExecutionCommand) -> ExecutorObservation:
        return ExecutorObservation(
            evidence_ref=self._evidence_ref,
            raw_after=self._result,
        )


@dataclass(frozen=True)
class PreparedExecution:
    candidate: CandidateProposal
    control: ControlSnapshot
    coordinator: ExecutionCoordinator
    lab_decision: LabPolicyDecision
    review: PEAReview
    crossing: ConsequentialCrossing
    invocation_id: str

    def execute(self, route: RouteDecision) -> ExecutionCallResult:
        permit, admission = self.coordinator.begin(
            proposal=self.candidate,
            route=route,
            control=self.control,
            lab_decision=self.lab_decision,
            pea_review=self.review,
            crossing=self.crossing,
            seed_release=None,
            invocation_id=self.invocation_id,
            boundary=0,
        )
        return self.coordinator.execute_once(permit, admission, self.candidate, boundary=0)


def prepare_execution(
    candidate: CandidateProposal,
    *,
    state: str = "CONTINUE",
    raw_after: SyntheticRawAfter | None = None,
    fixture_id: str = "default",
) -> PreparedExecution:
    evidence_ref = evidence(f"executor-{state}-{fixture_id}")
    executor = FixtureExecutor(raw_after or raw_after_for(candidate, state=state), evidence_ref)
    grants = GrantRegistry()
    grant = TaskGrant(
        root_ref=ref(f"grant-root-{state}-{fixture_id}"),
        source=GrantSource.HUMAN,
        task_id=f"task-{state}-{fixture_id}",
        goal_id=candidate.goal_id,
        goal_ref=candidate.goal_ref,
        scope_id=candidate.scope_id,
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=1,
        maximum_invocations=1,
        allowed_action_names=(candidate.action.name,),
        allowed_action_refs=(candidate.action.digest,),
        executor_id="executor",
        executor_version="executor-v1",
        executor_artifact_ref=ref("executor-artifact"),
        output_destination_ref=ref("destination"),
        release_review_required=False,
        maximum_attention_units=0,
    )
    grant_ref = grants.activate(grant)
    context = LabBoundaryContext(
        grant_ref=grant_ref,
        task_id=grant.task_id,
        goal_id=grant.goal_id,
        goal_ref=grant.goal_ref,
        scope_id=grant.scope_id,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        proposal_ref=candidate.digest,
        action_ref=candidate.action.digest,
        output_destination_ref=grant.output_destination_ref,
        attention_budget=0,
    )
    review = PEAReview(
        boundary_context_ref=context.digest,
        external_grant_ref=grant_ref,
        consent=ReviewStatus.SUPPLIED,
        standing=ReviewStatus.SUPPLIED,
        privacy=ReviewStatus.SUPPLIED,
        reversibility=ReviewStatus.SUPPLIED,
        remedy=ReviewStatus.SUPPLIED,
        contestability=ReviewStatus.SUPPLIED,
        refusal=ReviewStatus.SUPPLIED,
        human_responsibility_ref=ref("responsible-human"),
    )
    crossing = ConsequentialCrossing(
        boundary_context_ref=context.digest,
        subject_ref=candidate.action.digest,
        description_ref=ref("description"),
        recommendation_ref=ref("recommendation"),
        permission_ref=ref("permission"),
        authorization_ref=ref("authorization"),
        current_stage=CrossingStage.AUTHORIZATION,
        externally_supplied_authorization=True,
    )
    lab_decision = evaluate_lab_rules(
        context=context,
        pea_review=review,
        crossing=crossing,
        seed_release=None,
        external_effect_requested=True,
        release_requested=False,
    )
    binding = lab_decision.external_effect_binding
    assert binding is not None
    external_control = ControlSnapshot(
        account_id="execution-account",
        account_version=0,
        observation_id=candidate.observation_id,
        observation_ref=candidate.observation_ref,
        scope_id=candidate.scope_id,
        active_goal_ids=(candidate.goal_id,),
        active_goal_refs=(candidate.goal_ref,),
        available_evidence_refs=candidate.evidence_refs,
        available_trace_refs=candidate.trace_refs,
        available_residual_refs=candidate.residual_refs,
        accepted_material_delta_refs=candidate.material_delta_refs,
        allowed_action_names=(candidate.action.name,),
        allowed_action_refs=(candidate.action.digest,),
        remaining_budget=control().remaining_budget,
        lab_boundary=binding,
        execution_grant_ref=grant_ref,
        serial_token=f"serial-{state}-{fixture_id}",
        shadow_only=False,
    )
    coordinator = ExecutionCoordinator(grants, executor)
    return PreparedExecution(
        candidate=candidate,
        control=external_control,
        coordinator=coordinator,
        lab_decision=lab_decision,
        review=review,
        crossing=crossing,
        invocation_id=f"invocation-{state}-{fixture_id}",
    )


def test_session_enforces_scan_decide_assess_lifecycle() -> None:
    active = session()
    with pytest.raises(RuntimeError, match="fresh completed scan"):
        active.decide(control())
    scan = active.scan(request())
    assert scan.phase_after is SessionPhase.READY_TO_ACT
    prepared = prepare_execution(proposal())
    decision = active.decide(
        prepared.control, cadence_signals=CadenceSignals(structural_novelty=True)
    )
    assert decision.route.disposition is RouteDisposition.ADMIT
    assert decision.cadence.depth is ReasoningDepth.DEEP
    assert active.phase is SessionPhase.AWAITING_ASSESSMENT
    with pytest.raises(RuntimeError, match="assessed"):
        active.scan(request())
    assessment = active.assess(
        prepared.execute(decision.route),
        matched_prediction_items=("visible state",),
        residual_refs=(),
        preserved_hypothesis_refs=("hyp-1",),
        revised_hypothesis_refs=(),
        concise_update_summary="prediction matched; retain the mechanic provisionally",
    )
    assert assessment.phase_after is SessionPhase.NEEDS_SCAN
    receipt = active.receipt()
    assert receipt.admitted_action_count == 1
    assert not receipt.completion_genuinely_observed


def test_session_binds_exact_driver_domain_and_policy_manifest_identity() -> None:
    with pytest.raises(RuntimeError, match="driver, domain, and policies"):
        ReasoningSession(
            session_id="replacement-driver",
            model_driver=ReplacementDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
        )
    with pytest.raises(RuntimeError, match="driver, domain, and policies"):
        ReasoningSession(
            session_id="unfrozen-policy",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            router_policy=RouterPolicy(request_missing_witness=False),
        )


def test_session_revalidates_model_and_domain_identity_at_each_call_boundary() -> None:
    driver = FixedDriver((proposal(),))
    domain = SyntheticDomain()
    active = ReasoningSession(
        session_id="mutable-model",
        model_driver=driver,
        domain_adapter=domain,
        governing_goal_ref=governing_goal().digest,
        frozen_runtime=frozen_runtime(),
    )
    active.scan(request())
    driver.driver_artifact_ref = ref("drifted-driver-artifact")
    with pytest.raises(RuntimeError, match="model driver identity drifted"):
        active.decide(control())

    driver = FixedDriver((proposal(),))
    domain = SyntheticDomain()
    active = ReasoningSession(
        session_id="mutable-domain",
        model_driver=driver,
        domain_adapter=domain,
        governing_goal_ref=governing_goal().digest,
        frozen_runtime=frozen_runtime(),
    )
    candidate = proposal()
    active.scan(request())
    prepared = prepare_execution(candidate, fixture_id="domain-drift")
    decision = active.decide(prepared.control)
    execution = prepared.execute(decision.route)
    domain.adapter_artifact_ref = ref("drifted-domain-artifact")
    with pytest.raises(RuntimeError, match="domain adapter identity drifted"):
        active.assess(
            execution,
            matched_prediction_items=(),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary="drifted domain must not assess an action",
        )
    assert active.phase is SessionPhase.AWAITING_ASSESSMENT


def test_session_wires_scan_decision_and_assessment_to_ledger(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "session.sqlite3") as ledger:
        active = ReasoningSession(
            session_id="session-ledger",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
            account_id="account",
        )
        active.scan(request())
        prepared = prepare_execution(proposal(), fixture_id="ledger")
        decision = active.decide(prepared.control)
        active.assess(
            prepared.execute(decision.route),
            matched_prediction_items=("visible",),
            residual_refs=(),
            preserved_hypothesis_refs=("hyp-1",),
            revised_hypothesis_refs=(),
            concise_update_summary="matched",
        )
        receipt = active.receipt()
        assert len(receipt.ledger_receipt_refs) == 3
        assert ledger.verify()[0] == 3
        assert ledger.get_payload(request().digest)["observation"]["observation_id"] == "obs-1"
        assert ledger.get_payload(proposal().digest)["proposal_id"] == "proposal-1"
        assessed_outcome = outcome_for(proposal())
        assert ledger.get_payload(assessed_outcome.digest)["outcome_id"] == "outcome-CONTINUE"


def test_ledger_failure_never_advances_actionable_session_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_append(**_kwargs: object) -> object:
        raise LedgerError("simulated durable append failure")

    with SQLiteLedger(tmp_path / "scan-failure.sqlite3") as ledger:
        active = ReasoningSession(
            session_id="scan-failure",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
        )
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "append", reject_append)
            with pytest.raises(LedgerError, match="durable append"):
                active.scan(request())
        assert active.phase is SessionPhase.NEEDS_SCAN
        assert active.receipt().scans == ()
        assert active.receipt().ledger_receipt_refs == ()

    with SQLiteLedger(tmp_path / "decide-failure.sqlite3") as ledger:
        active = ReasoningSession(
            session_id="decide-failure",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
        )
        active.scan(request())
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "append", reject_append)
            with pytest.raises(LedgerError, match="durable append"):
                active.decide(control())
        assert active.phase is SessionPhase.READY_TO_ACT
        assert active.receipt().decisions == ()
        assert len(active.receipt().ledger_receipt_refs) == 1

    with SQLiteLedger(tmp_path / "assess-failure.sqlite3") as ledger:
        active = ReasoningSession(
            session_id="assess-failure",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
        )
        active.scan(request())
        prepared = prepare_execution(proposal(), fixture_id="assess-failure")
        decision = active.decide(prepared.control)
        execution = prepared.execute(decision.route)
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "append", reject_append)
            with pytest.raises(LedgerError, match="durable append"):
                active.assess(
                    execution,
                    matched_prediction_items=(),
                    residual_refs=(),
                    preserved_hypothesis_refs=(),
                    revised_hypothesis_refs=(),
                    concise_update_summary="must remain pending",
                )
        assert active.phase is SessionPhase.AWAITING_ASSESSMENT
        assert active.receipt().assessments == ()
        assert len(active.receipt().ledger_receipt_refs) == 2


def test_terminal_session_appends_replay_complete_terminal_snapshot(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "terminal.sqlite3") as ledger:
        active = ReasoningSession(
            session_id="terminal-ledger",
            model_driver=FixedDriver((proposal(),)),
            domain_adapter=SyntheticDomain(),
            governing_goal_ref=governing_goal().digest,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
        )
        candidate = proposal()
        active.scan(request())
        prepared = prepare_execution(candidate, state="SUCCESS", fixture_id="terminal-ledger")
        decision = active.decide(prepared.control)
        active.assess(
            prepared.execute(decision.route),
            matched_prediction_items=("goal",),
            residual_refs=(),
            preserved_hypothesis_refs=("hyp-1",),
            revised_hypothesis_refs=(),
            concise_update_summary="domain reported success",
        )
        receipt = active.receipt()
        assert receipt.completion_genuinely_observed
        assert len(receipt.ledger_receipt_refs) == 4
        assert ledger.verify()[0] == 4
        terminal_envelope = tuple(ledger.receipts())[-1]
        terminal_payload = ledger.get_payload(terminal_envelope.payload_hash)
        assert terminal_payload["completion_genuinely_observed"] is True


def test_terminal_success_is_earned_only_from_domain_authority() -> None:
    active = session()
    candidate = proposal()
    active.scan(request())
    prepared = prepare_execution(candidate, state="SUCCESS", fixture_id="terminal")
    decision = active.decide(prepared.control)
    active.assess(
        prepared.execute(decision.route),
        matched_prediction_items=("goal",),
        residual_refs=(),
        preserved_hypothesis_refs=("hyp-1",),
        revised_hypothesis_refs=(),
        concise_update_summary="domain terminal authority reported success",
    )
    receipt = active.receipt()
    assert receipt.phase is SessionPhase.TERMINAL
    assert receipt.completion_genuinely_observed
    with pytest.raises(RuntimeError, match="terminal"):
        active.scan(request())


def test_terminal_success_cannot_be_smuggled_in_an_outcome_label() -> None:
    active = session()
    candidate = proposal()
    active.scan(request())
    asserted_success = SyntheticRawAfter(
        observation=raw_after_for(candidate, state="CONTINUE").observation,
        outcome=outcome_for(candidate, state="CONTINUE").model_copy(
            update={"state_label": "SUCCESS", "terminal": True}
        ),
    )
    prepared = prepare_execution(
        candidate, raw_after=asserted_success, fixture_id="smuggled-terminal"
    )
    decision = active.decide(prepared.control)
    with pytest.raises(RuntimeError, match="nonterminal authority"):
        active.assess(
            prepared.execute(decision.route),
            matched_prediction_items=(),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary="caller asserted success",
        )
    assert not active.receipt().completion_genuinely_observed


def test_assessment_refuses_spliced_or_noncompleted_execution_evidence() -> None:
    active = session()
    candidate = proposal()
    active.scan(request())
    prepared = prepare_execution(candidate, fixture_id="splice")
    decision = active.decide(prepared.control)
    execution = prepared.execute(decision.route)
    spliced_release = execution.release.model_copy(update={"route_ref": ref("other-route")})
    spliced_attempt = execution.attempt.model_copy(
        update={"release_ref": spliced_release.digest}
    )
    with pytest.raises(RuntimeError, match="exact completed execution evidence"):
        active.assess(
            ExecutionCallResult(
                issuer=object(),
                admission=execution.admission,
                release=spliced_release,
                attempt=spliced_attempt,
                observation=execution.observation,
            ),
            matched_prediction_items=(),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary="spliced evidence must not advance state",
        )
    assert active.phase is SessionPhase.AWAITING_ASSESSMENT
    assert active.receipt().assessments == ()

    unknown = execution.attempt.model_copy(
        update={
            "disposition": ExecutionDisposition.UNKNOWN_EFFECT,
            "result_evidence_ref": None,
            "failure_category": "executor_effect_unknown:TimeoutError",
        }
    )
    with pytest.raises(RuntimeError, match="exact completed execution evidence"):
        active.assess(
            ExecutionCallResult(
                issuer=object(),
                admission=execution.admission,
                release=execution.release,
                attempt=unknown,
                observation=None,
            ),
            matched_prediction_items=(),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary="unknown effect cannot be assessed as an observation",
        )
    assert active.phase is SessionPhase.AWAITING_ASSESSMENT


def test_assessment_refuses_a_genuine_result_from_another_route() -> None:
    active = session()
    candidate = proposal()
    active.scan(request())
    pending = prepare_execution(candidate, fixture_id="pending-route")
    active.decide(pending.control)

    other = prepare_execution(candidate, fixture_id="other-route")
    genuine_other_result = other.execute(evaluate_proposal(candidate, other.control))
    assert (
        genuine_other_result.admission.route_ref != active.receipt().decisions[-1].route.digest
    )
    with pytest.raises(RuntimeError, match="exact completed execution evidence"):
        active.assess(
            genuine_other_result,
            matched_prediction_items=(),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary="a real result from another route cannot be spliced",
        )
    assert active.phase is SessionPhase.AWAITING_ASSESSMENT


def test_identical_failed_action_is_blocked_until_beliefs_change() -> None:
    active = session()
    candidate = proposal()
    active.scan(request())
    prepared = prepare_execution(candidate, state="FAILURE", fixture_id="failed-action")
    decision = active.decide(prepared.control)
    active.assess(
        prepared.execute(decision.route),
        matched_prediction_items=(),
        residual_refs=(ref("failure-residual"),),
        preserved_hypothesis_refs=(),
        revised_hypothesis_refs=("hyp-1-v2",),
        concise_update_summary="failure preserved and implicated access hypothesis",
    )
    active.scan(request())
    with pytest.raises(RuntimeError, match="unchanged beliefs"):
        active.decide(control())


def test_driver_cannot_return_stale_or_impersonated_proposal() -> None:
    impersonated = proposal().model_copy(update={"model_driver_id": "another-driver"})
    active = session((impersonated,))
    active.scan(request())
    with pytest.raises(RuntimeError, match="impersonate"):
        active.decide(control())


def test_session_rejects_changed_observation_content_and_goal_splicing() -> None:
    active = session()
    active.scan(request())
    altered_control = control().model_copy(update={"observation_ref": ref("altered")})
    with pytest.raises(RuntimeError, match="current observation"):
        active.decide(altered_control)

    altered_observation = proposal().model_copy(update={"observation_ref": ref("altered")})
    active = session((altered_observation,))
    active.scan(request())
    with pytest.raises(RuntimeError, match="altered observation content"):
        active.decide(control())

    base = proposal()
    root_distinction = base.meaningful_distinction.model_copy(
        update={"parent_goal_id": "goal-root"}
    )
    spliced_goal = base.model_copy(
        update={"goal_id": "goal-root", "meaningful_distinction": root_distinction}
    )
    active = session((spliced_goal,))
    active.scan(request())
    with pytest.raises(RuntimeError, match="spliced"):
        active.decide(control())


def test_driver_cannot_alias_a_forbidden_action_behind_duplicate_identity() -> None:
    forbidden = proposal(proposal_id="alias", action="forbidden")
    allowed = proposal(proposal_id="alias", action="inspect")
    active = session((forbidden, allowed))
    active.scan(request())
    with pytest.raises(RuntimeError, match="duplicate proposal"):
        active.decide(control())


def test_arc_completion_and_game_over_semantics_are_authoritative() -> None:
    assert terminal_authority(ArcGameState.NOT_FINISHED) is TerminalAuthority.CONTINUE
    assert terminal_authority(ArcGameState.WIN) is TerminalAuthority.SUCCESS
    assert terminal_authority(ArcGameState.GAME_OVER) is TerminalAuthority.FAILURE
    assert legal_actions_after(ArcGameState.GAME_OVER, ("ACTION1", "RESET")) == ("RESET",)
    receipt = ArcRunReceipt(
        game_id="opaque-public-game",
        environment_class="online-public",
        final_environment_state=ArcGameState.WIN,
        levels_completed=6,
        win_levels=(1, 2, 3, 4, 5, 6),
        environment_action_count=100,
        reset_count=1,
        frozen_runtime_ref=ref("runtime"),
        replay_evidence_ref=ref("replay"),
        completion_genuinely_observed=True,
        claim_class="online-public",
    )
    assert receipt.completion_genuinely_observed
    with pytest.raises(ValidationError, match="only the environment WIN"):
        ArcRunReceipt.model_validate(
            {
                **receipt.model_dump(mode="python"),
                "final_environment_state": ArcGameState.NOT_FINISHED,
                "completion_genuinely_observed": True,
            }
        )
