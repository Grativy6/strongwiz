"""A complete, non-ARC Strongwiz laboratory with a replaceable model callback.

The example deliberately solves a tiny problem: advance a local counter from
zero to one.  Its purpose is to demonstrate the whole boundary, not to claim a
general capability.  The callback supplies proposal content; Strongwiz binds
the observation and goal, checks an externally rooted grant, admits exactly one
writer call, asks the domain for terminal authority, checkpoints, seals, and
packs the resulting evidence.

No network, terminal input, game identity, or private chain-of-thought is used.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from strongwiz import __version__
from strongwiz.authority import GrantRegistry, GrantSource, TaskGrant
from strongwiz.canonical import canonical_bytes, canonical_text, content_hash
from strongwiz.contracts import (
    CONTRACT_SCHEMA,
    ActionSpec,
    CandidateProposal,
    ContractModel,
    ControlSnapshot,
    CostVector,
    DecisionEffect,
    Distinction,
    EvidenceRef,
    Goal,
    Observation,
    Outcome,
    Prediction,
    ReasoningRequest,
)
from strongwiz.drivers import (
    DriverRegistry,
    ExecutionCommand,
    ExecutorObservation,
    TerminalAuthority,
)
from strongwiz.features import default_experimental_features
from strongwiz.integrity import FrozenRuntimeManifest, freeze_files
from strongwiz.lab import (
    LabManifest,
    RunDisposition,
    RunSpec,
    initialize_lab,
    pack_evidence,
    seal_run,
    verify_evidence_capsule,
    verify_lab_genesis,
)
from strongwiz.lab_policy import (
    PEA_CORE_VERSION,
    PECAN_VERSION,
    SEED_VERSION,
    ConsequentialCrossing,
    CrossingStage,
    LabBoundaryContext,
    PEAReview,
    ReviewStatus,
    evaluate_lab_rules,
)
from strongwiz.ledger import SQLiteLedger
from strongwiz.modelkit import CallableModelDriver, ProposalDraft
from strongwiz.orchestration import ExecutionCoordinator, ExecutionDisposition
from strongwiz.policy import CadencePolicy, CadenceSignals
from strongwiz.provenance import load_source_registry
from strongwiz.routing import RouterPolicy
from strongwiz.runtime import SessionPhase, StrongwizKernel

RUN_ID = "reference-counter-run-001"
LAB_ID = "reference-counter-lab"
SCOPE_ID = "reference-counter-scope"
SESSION_ID = "reference-counter-session"
COUNTER_ACTION = "increment"
SUCCESS_STATE = "SUCCESS"


def _ref(label: object) -> str:
    """Give a concise supplied item a stable content identity."""

    return content_hash({"reference": label})


class ReferenceCounterReceipt(ContractModel):
    """Concise handoff for one evidence-bound reference run."""

    schema_id: str = Field(default="strongwiz.reference-counter-receipt.v1", alias="schema")
    run_id: Literal[RUN_ID] = RUN_ID
    final_environment_state: Literal[SUCCESS_STATE] = SUCCESS_STATE
    terminal_authority: Literal[TerminalAuthority.SUCCESS] = TerminalAuthority.SUCCESS
    action_count: Literal[1] = 1
    completion_genuinely_observed: Literal[True] = True
    lab_manifest_ref: str
    run_spec_ref: str
    genesis_ref: str
    restart_checkpoint_receipt_ref: str
    run_seal_ref: str
    evidence_capsule_ref: str
    replay_evidence_path: Literal["ledger/receipts.jsonl"] = "ledger/receipts.jsonl"
    claim_ceiling: Literal["one_deterministic_reference_domain_run"] = (
        "one_deterministic_reference_domain_run"
    )

    @field_validator(
        "lab_manifest_ref",
        "run_spec_ref",
        "genesis_ref",
        "restart_checkpoint_receipt_ref",
        "run_seal_ref",
        "evidence_capsule_ref",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("reference receipt bindings must be lowercase SHA-256 digests")
        return value


class CounterDomain:
    """A domain adapter whose own normalized state is terminal authority."""

    adapter_id = "reference-counter"
    adapter_version = "1"

    def __init__(self, *, artifact_ref: str) -> None:
        self.adapter_artifact_ref = artifact_ref

    @staticmethod
    def _raw(raw: object) -> tuple[int, int]:
        if not isinstance(raw, Mapping):
            raise TypeError("counter state must be a mapping")
        counter = raw.get("counter")
        epoch = raw.get("epoch")
        if (
            isinstance(counter, bool)
            or not isinstance(counter, int)
            or counter not in {0, 1}
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 0
        ):
            raise ValueError("counter state requires counter in {0,1} and a nonnegative epoch")
        return counter, epoch

    def normalize_observation(self, raw: object) -> Observation:
        counter, epoch = self._raw(raw)
        payload = {"counter": counter, "epoch": epoch}
        terminal = counter == 1
        return Observation(
            observation_id=f"counter-observation-{epoch}-{counter}",
            domain=self.adapter_id,
            scope_id=SCOPE_ID,
            epoch=epoch,
            payload_ref=EvidenceRef(
                kind="reference_counter_state",
                digest=content_hash(payload),
                locator="state/domain/counter.json" if terminal else None,
            ),
            summary=(
                "counter has reached the declared target"
                if terminal
                else "counter is below the declared target"
            ),
            available_action_names=() if terminal else (COUNTER_ACTION,),
        )

    def available_actions(self, observation: Observation) -> Sequence[ActionSpec]:
        if observation.domain != self.adapter_id or observation.scope_id != SCOPE_ID:
            raise ValueError("observation is outside the counter domain")
        if not observation.available_action_names:
            return ()
        return (ActionSpec(name=COUNTER_ACTION, parameters={"amount": 1}),)

    def extract_outcome(
        self, before: Observation, action: ActionSpec, raw_after: object
    ) -> Outcome:
        after = self.normalize_observation(raw_after)
        terminal = self.terminal_authority(after) is TerminalAuthority.SUCCESS
        return Outcome(
            outcome_id=f"counter-outcome-{after.epoch}",
            observation_before_id=before.observation_id,
            observation_before_ref=before.digest,
            observation_after_id=after.observation_id,
            observation_after_ref=after.digest,
            action=action,
            observed_consequences=(
                "counter advanced from zero to the declared target",
                "domain terminal authority reported success",
            ),
            state_label=SUCCESS_STATE if terminal else "NOT_FINISHED",
            evidence_refs=(after.payload_ref.sha256,),
            terminal=terminal,
        )

    def terminal_authority(self, observation: Observation) -> TerminalAuthority:
        if observation.domain != self.adapter_id or observation.scope_id != SCOPE_ID:
            raise ValueError("observation is outside the counter domain")
        return (
            TerminalAuthority.SUCCESS
            if not observation.available_action_names
            else TerminalAuthority.CONTINUE
        )


class CounterExecutor:
    """The sole local writer; an immutable state file makes re-execution fail closed."""

    executor_id = "reference-counter-writer"
    executor_version = "1"

    def __init__(self, state_path: Path, *, artifact_ref: str) -> None:
        self.state_path = state_path
        self.executor_artifact_ref = artifact_ref
        self.calls = 0

    def execute(self, command: ExecutionCommand) -> ExecutorObservation:
        if command.executor_id != self.executor_id:
            raise ValueError("execution command names another writer")
        if command.executor_version != self.executor_version:
            raise ValueError("execution command names another writer version")
        if command.executor_artifact_ref != self.executor_artifact_ref:
            raise ValueError("execution command names another writer artifact")
        if command.action != ActionSpec(name=COUNTER_ACTION, parameters={"amount": 1}):
            raise ValueError("counter writer received an action outside its narrow contract")
        raw_after = {"counter": 1, "epoch": 1}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("xb") as stream:
            stream.write(canonical_bytes(raw_after))
            stream.flush()
        self.calls += 1
        return ExecutorObservation(
            evidence_ref=EvidenceRef(
                kind="reference_counter_state",
                digest=content_hash(raw_after),
                locator="state/domain/counter.json",
            ),
            raw_after=raw_after,
        )


def _goals() -> tuple[Goal, Goal]:
    governing = Goal(
        goal_id="reference-counter-goal",
        statement="complete the declared reference counter task",
        scope_id=SCOPE_ID,
        success_condition="the domain terminal authority reports success",
    )
    scoped = Goal(
        goal_id="reference-counter-step",
        statement="advance the counter to its target",
        scope_id=SCOPE_ID,
        parent_goal_id=governing.goal_id,
        governing_goal_id=governing.goal_id,
        motivating_uncertainty="whether the supplied increment changes the counter",
        decision_that_could_change="whether to issue the one allowed increment",
        smallest_sufficient_test="perform one authorized increment and inspect domain state",
        success_condition="counter equals one and domain authority reports success",
        abandonment_condition="the external grant is withdrawn",
        reopening_condition="a later counter state no longer equals the declared target",
    )
    return governing, scoped


def _proposal_draft(scoped_goal: Goal, observation: Observation) -> ProposalDraft:
    effects = (DecisionEffect.PROGRESS, DecisionEffect.PLAN)
    return ProposalDraft(
        proposal_id="increment-counter-once",
        action=ActionSpec(name=COUNTER_ACTION, parameters={"amount": 1}),
        meaningful_distinction=Distinction(
            distinction_id="increment-effect",
            statement="the supplied increment either reaches the target or does not",
            scope_id=SCOPE_ID,
            parent_goal_id=scoped_goal.goal_id,
            governing_goal_id="reference-counter-goal",
            candidate_resolutions=("target_reached", "target_not_reached"),
            competing_predictions=("counter becomes one", "counter remains below one"),
            decision_effects=effects,
            decision_that_could_change="whether this action completes the run",
            relevance_summary="the declared goal is exactly the counter terminal state",
            smallest_discriminating_test="perform the one authorized increment",
            reopening_condition="the observed counter disagrees with the prediction",
        ),
        prediction=Prediction(
            prediction_id="increment-prediction",
            hypothesis_refs=(),
            expected_consequences=("counter advances to the target",),
            falsified_by=("counter remains below the target",),
            alternatives=("writer refuses before any effect",),
        ),
        decision_effects=effects,
        evidence_refs=(observation.payload_ref.sha256,),
        concise_rationale="one bounded reversible lab action directly tests the goal condition",
        reversible=True,
        expected_progress_rank=1,
        information_gain_rank=1,
        risk_rank=0,
        costs=CostVector(environment_actions=1, compute_units=1, validation_units=1),
    )


def _external_reviews(
    grant: TaskGrant, proposal: CandidateProposal, *, responsibility_ref: str
) -> tuple[LabBoundaryContext, PEAReview, ConsequentialCrossing]:
    context = LabBoundaryContext(
        grant_ref=grant.grant_ref,
        task_id=grant.task_id,
        goal_id=grant.goal_id,
        goal_ref=grant.goal_ref,
        scope_id=grant.scope_id,
        observation_id=proposal.observation_id,
        observation_ref=proposal.observation_ref,
        proposal_ref=proposal.digest,
        action_ref=proposal.action.digest,
        output_destination_ref=grant.output_destination_ref,
        attention_budget=grant.maximum_attention_units,
    )
    review = PEAReview(
        boundary_context_ref=context.digest,
        external_grant_ref=grant.grant_ref,
        consent=ReviewStatus.SUPPLIED,
        standing=ReviewStatus.SUPPLIED,
        privacy=ReviewStatus.NOT_APPLICABLE,
        reversibility=ReviewStatus.SUPPLIED,
        remedy=ReviewStatus.SUPPLIED,
        contestability=ReviewStatus.SUPPLIED,
        refusal=ReviewStatus.SUPPLIED,
        human_responsibility_ref=responsibility_ref,
    )
    crossing = ConsequentialCrossing(
        boundary_context_ref=context.digest,
        subject_ref=proposal.action.digest,
        description_ref=_ref("local counter state write described"),
        recommendation_ref=_ref("one bounded increment recommended"),
        permission_ref=_ref("caller permitted the local reference run"),
        authorization_ref=grant.grant_ref,
        current_stage=CrossingStage.AUTHORIZATION,
        externally_supplied_authorization=True,
    )
    return context, review, crossing


def _store_objects(ledger: SQLiteLedger, values: Sequence[ContractModel]) -> tuple[str, ...]:
    refs = tuple(
        ledger.put_object(value.model_dump(mode="json", by_alias=True)) for value in values
    )
    return tuple(dict.fromkeys(refs))


def run_reference_counter_lab(
    lab_root: str | Path,
    capsule_root: str | Path,
    *,
    authorization_root_ref: str,
) -> ReferenceCounterReceipt:
    """Run a new lab exactly once and return its concise sealed receipt.

    ``authorization_root_ref`` must come from the caller.  It is evidence of the
    supplied boundary identity, not independent authentication of permission.
    Both destination parents must already exist; neither destination may hold a
    previous run.
    """

    if len(authorization_root_ref) != 64 or any(
        character not in "0123456789abcdef" for character in authorization_root_ref
    ):
        raise ValueError("authorization root must be a lowercase SHA-256 digest")
    lab_path = Path(lab_root)
    capsule_path = Path(capsule_root)
    repository_root = Path(__file__).resolve().parents[1]

    runtime_paths = (
        "docs/source-identities.json",
        "examples/reference_counter_lab.py",
        "pyproject.toml",
        "requirements-dev.lock.txt",
        *(
            path.relative_to(repository_root).as_posix()
            for path in sorted((repository_root / "src" / "strongwiz").rglob("*.py"))
        ),
        "src/strongwiz/py.typed",
    )
    source_files = freeze_files(repository_root, runtime_paths)
    source_by_path = {item.relative_path: item.sha256 for item in source_files}
    source_ref = source_by_path["examples/reference_counter_lab.py"]
    model_artifact_ref = _ref({"component": "counter-model", "source": source_ref})
    domain_artifact_ref = _ref({"component": "counter-domain", "source": source_ref})
    executor_artifact_ref = _ref({"component": "counter-writer", "source": source_ref})
    router_policy = RouterPolicy()
    cadence_policy = CadencePolicy()
    policy_refs = tuple(
        sorted(
            (
                router_policy.digest,
                cadence_policy.digest,
                _ref({"source": "PEA Core", "version": PEA_CORE_VERSION}),
                _ref({"source": "PECAN", "version": PECAN_VERSION}),
                _ref({"source": "SEED", "version": SEED_VERSION}),
            )
        )
    )
    experimental_features = default_experimental_features()
    source_registry = load_source_registry(repository_root / "docs/source-identities.json")

    governing_goal, scoped_goal = _goals()
    domain = CounterDomain(artifact_ref=domain_artifact_ref)
    initial_raw = {"counter": 0, "epoch": 0}
    initial_observation = domain.normalize_observation(initial_raw)
    request = ReasoningRequest(
        observation=initial_observation,
        governing_goal=governing_goal,
        scoped_goal=scoped_goal,
    )
    draft = _proposal_draft(scoped_goal, initial_observation)
    driver = CallableModelDriver(
        driver_id="reference-counter-model",
        driver_version="1",
        driver_artifact_ref=model_artifact_ref,
        proposal_function=lambda _request: (draft,),
    )
    preview = tuple(driver.propose(request))
    if len(preview) != 1:
        raise RuntimeError("reference model must produce exactly one proposal")
    proposal = preview[0]

    frozen_runtime = FrozenRuntimeManifest(
        package_version=__version__,
        contract_schema=CONTRACT_SCHEMA,
        source_files=source_files,
        configuration_ref=_ref("reference-counter-configuration-v1"),
        dependency_lock_ref=source_by_path["requirements-dev.lock.txt"],
        model_driver_id=driver.driver_id,
        model_driver_version=driver.driver_version,
        model_driver_artifact_ref=driver.driver_artifact_ref,
        domain_adapter_id=domain.adapter_id,
        domain_adapter_version=domain.adapter_version,
        domain_adapter_artifact_ref=domain.adapter_artifact_ref,
        capability_refs=(experimental_features.digest,),
        policy_refs=policy_refs,
        runtime_description="offline deterministic non-ARC reference counter laboratory",
    )
    manifest = LabManifest(
        lab_id=LAB_ID,
        lab_version="1",
        purpose="demonstrate one complete model-neutral sealed Strongwiz run",
        strongwiz_version=__version__,
        kernel_artifact_ref=frozen_runtime.manifest_ref,
        contract_schema=CONTRACT_SCHEMA,
        capability_refs=(experimental_features.digest,),
        policy_refs=policy_refs,
        source_identity_refs=(source_registry.digest,),
    )
    state_relative = f"{manifest.layout.domain_state_path}/counter.json"
    destination_ref = _ref({"lab": manifest.digest, "path": state_relative})
    grant = TaskGrant(
        root_ref=authorization_root_ref,
        source=GrantSource.EXTERNAL_CONTROL,
        task_id=RUN_ID,
        goal_id=scoped_goal.goal_id,
        goal_ref=scoped_goal.digest,
        scope_id=SCOPE_ID,
        generation=0,
        issued_boundary=0,
        not_before_boundary=0,
        expires_boundary=1,
        maximum_invocations=1,
        allowed_action_names=(COUNTER_ACTION,),
        allowed_action_refs=(proposal.action.digest,),
        executor_id=CounterExecutor.executor_id,
        executor_version=CounterExecutor.executor_version,
        executor_artifact_ref=executor_artifact_ref,
        output_destination_ref=destination_ref,
        release_review_required=False,
        maximum_attention_units=1,
    )
    run_spec = RunSpec(
        run_id=RUN_ID,
        lab_manifest_ref=manifest.digest,
        objective="advance a fresh local counter from zero to one",
        success_condition="the CounterDomain terminal authority reports SUCCESS",
        success_state=SUCCESS_STATE,
        terminal_authority_source="CounterDomain.terminal_authority",
        evaluation_class="local-reference",
        frozen_runtime_ref=frozen_runtime.manifest_ref,
        model_driver_id=driver.driver_id,
        model_driver_version=driver.driver_version,
        model_driver_artifact_ref=driver.driver_artifact_ref,
        domain_adapter_id=domain.adapter_id,
        domain_adapter_version=domain.adapter_version,
        domain_adapter_artifact_ref=domain.adapter_artifact_ref,
        seed=0,
        resource_budget=CostVector(
            environment_actions=1, compute_units=10, validation_units=10
        ),
        allowed_action_names=(COUNTER_ACTION,),
        declared_input_refs=(initial_observation.payload_ref.sha256,),
        policy_refs=policy_refs,
        execution_grant_ref=grant.grant_ref,
        shadow_only=False,
    )

    genesis = initialize_lab(lab_path, manifest=manifest, run_spec=run_spec)
    verification = verify_lab_genesis(lab_path)
    if not verification.current_state_matches_genesis:
        raise RuntimeError("reference lab did not begin from an empty genesis")

    registry = DriverRegistry()
    registry.register_model(driver)
    registry.register_domain(domain)
    kernel = StrongwizKernel(registry)
    ledger_path = lab_path.joinpath(*manifest.layout.ledger_path.split("/"))
    state_path = lab_path.joinpath(*state_relative.split("/"))
    executor = CounterExecutor(state_path, artifact_ref=executor_artifact_ref)
    grants = GrantRegistry()
    grant_ref = grants.activate(grant)

    with SQLiteLedger(ledger_path) as ledger:
        initial_ref = ledger.put_object(initial_raw)
        if initial_ref != initial_observation.payload_ref.sha256:
            raise RuntimeError("initial evidence reference does not bind the declared input")
        grant_object_ref = ledger.put_object(grant.model_dump(mode="json", by_alias=True))
        input_receipt = ledger.append(
            occurrence_id=f"{RUN_ID}:declared-input",
            kind="declared_input_and_external_grant",
            account_id="reference-counter-control",
            account_version=0,
            payload={
                "grant_ref": grant_ref,
                "initial_state_ref": initial_ref,
                "run_id": RUN_ID,
            },
            object_refs=(initial_ref, grant_object_ref),
        )

        session = kernel.new_session(
            session_id=SESSION_ID,
            driver_id=driver.driver_id,
            domain_adapter_id=domain.adapter_id,
            governing_goal_ref=governing_goal.digest,
            frozen_runtime=frozen_runtime,
            ledger=ledger,
            account_id="reference-counter-session-account",
        )
        session.scan(request)
        context, pea_review, crossing = _external_reviews(
            grant, proposal, responsibility_ref=authorization_root_ref
        )
        lab_decision = evaluate_lab_rules(
            context=context,
            pea_review=pea_review,
            crossing=crossing,
            seed_release=None,
            external_effect_requested=True,
            release_requested=False,
        )
        binding = lab_decision.external_effect_binding
        if binding is None or not lab_decision.clears_requested_boundaries:
            raise RuntimeError("reference lab rules did not clear the supplied local grant")
        control = ControlSnapshot(
            account_id="reference-counter-control",
            account_version=0,
            observation_id=initial_observation.observation_id,
            observation_ref=initial_observation.digest,
            scope_id=SCOPE_ID,
            active_goal_ids=(scoped_goal.goal_id,),
            active_goal_refs=(scoped_goal.digest,),
            available_evidence_refs=proposal.evidence_refs,
            allowed_action_names=(COUNTER_ACTION,),
            allowed_action_refs=(proposal.action.digest,),
            remaining_budget=run_spec.resource_budget,
            lab_boundary=binding,
            execution_grant_ref=grant_ref,
            serial_token="reference-counter-single-writer-001",
            shadow_only=False,
        )
        decision = session.decide(
            control,
            cadence_signals=CadenceSignals(startup_uncertainty=True),
            credible_plan_supported=True,
            uncertainty_blocks_progress=False,
        )
        if decision.selected_proposal_ref != proposal.digest:
            raise RuntimeError("reference route did not select the exact previewed proposal")

        coordinator = ExecutionCoordinator(grants, executor)
        permit, admission = coordinator.begin(
            proposal=proposal,
            route=decision.route,
            control=control,
            lab_decision=lab_decision,
            pea_review=pea_review,
            crossing=crossing,
            seed_release=None,
            invocation_id="reference-counter-invocation-001",
            boundary=0,
        )
        execution = coordinator.execute_once(permit, admission, proposal, boundary=0)
        if (
            execution.attempt.disposition is not ExecutionDisposition.COMPLETED
            or execution.observation is None
            or executor.calls != 1
        ):
            raise RuntimeError("the exact single-writer execution did not complete once")
        raw_after_ref = ledger.put_object(execution.observation.raw_after)
        if raw_after_ref != execution.observation.evidence_ref.sha256:
            raise RuntimeError("executor evidence does not bind the observed raw state")
        boundary_values: tuple[ContractModel, ...] = (
            grant,
            context,
            pea_review,
            crossing,
            lab_decision,
            control,
            admission,
            execution.release,
            execution.attempt,
            execution.observation.evidence_ref,
        )
        boundary_refs = _store_objects(ledger, boundary_values)
        boundary_receipt = ledger.append(
            occurrence_id=f"{RUN_ID}:single-writer-boundary",
            kind="authorized_single_writer_execution",
            account_id="reference-counter-control",
            account_version=0,
            payload={
                "execution_admission_ref": admission.digest,
                "grant_ref": grant_ref,
                "lab_decision_ref": lab_decision.digest,
                "raw_after_ref": raw_after_ref,
                "run_id": RUN_ID,
            },
            object_refs=tuple(dict.fromkeys((*boundary_refs, raw_after_ref))),
            parent_refs=(input_receipt.receipt_id,),
        )

        assessment = session.assess(
            execution,
            matched_prediction_items=("counter advances to the target",),
            residual_refs=(),
            preserved_hypothesis_refs=(),
            revised_hypothesis_refs=(),
            concise_update_summary=(
                "the observed counter reached the target and domain authority reported success"
            ),
        )
        if (
            session.phase is not SessionPhase.TERMINAL
            or assessment.terminal_authority is not TerminalAuthority.SUCCESS
            or not session.receipt().completion_genuinely_observed
        ):
            raise RuntimeError("domain SUCCESS was not genuinely observed")
        checkpoint_ref = session.checkpoint(kind="reference_final_checkpoint")
        if checkpoint_ref is None:
            raise RuntimeError("restart-complete checkpoint was not persisted")
        restored = kernel.restore_session(
            checkpoint=checkpoint_ref,
            frozen_runtime=frozen_runtime,
            ledger=ledger,
            router_policy=router_policy,
            cadence_policy=cadence_policy,
        )
        restored_receipt = restored.receipt()
        if (
            restored_receipt.phase is not SessionPhase.TERMINAL
            or restored_receipt.terminal_authority is not TerminalAuthority.SUCCESS
            or not restored_receipt.completion_genuinely_observed
            or restored_receipt.admitted_action_count != 1
        ):
            raise RuntimeError("restored checkpoint lost terminal run state")
        restored_object_ref = ledger.put_object(
            restored_receipt.model_dump(mode="json", by_alias=True)
        )
        ledger.append(
            occurrence_id=f"{RUN_ID}:restart-verification",
            kind="restart_complete_checkpoint_verified",
            account_id="reference-counter-control",
            account_version=0,
            payload={
                "checkpoint_receipt_ref": checkpoint_ref,
                "restored_receipt_ref": restored_object_ref,
                "run_id": RUN_ID,
            },
            object_refs=(restored_object_ref,),
            parent_refs=(boundary_receipt.receipt_id,),
        )
        ledger.verify()

    run_seal = seal_run(
        lab_path,
        disposition=RunDisposition.SUCCESS_OBSERVED,
        terminal_state=SUCCESS_STATE,
        terminal_evidence_ref=assessment.outcome_ref,
        completion_genuinely_observed=True,
        concise_result_summary=(
            "one authorized increment ended when CounterDomain reported SUCCESS"
        ),
    )
    capsule = pack_evidence(
        lab_path,
        capsule_path,
        capsule_name="reference-counter-complete-evidence",
        acknowledge_opaque_domain_state=True,
    )
    verified_capsule = verify_evidence_capsule(
        capsule_path, expected_capsule_ref=capsule.digest
    )
    if verified_capsule != capsule:
        raise RuntimeError("portable evidence capsule failed exact verification")
    return ReferenceCounterReceipt(
        lab_manifest_ref=manifest.digest,
        run_spec_ref=run_spec.digest,
        genesis_ref=genesis.digest,
        restart_checkpoint_receipt_ref=checkpoint_ref,
        run_seal_ref=run_seal.digest,
        evidence_capsule_ref=capsule.digest,
    )


def build_parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    playground = repository_root / "playground" / "reference-counter"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-root", type=Path, default=playground / "lab")
    parser.add_argument("--capsule-root", type=Path, default=playground / "evidence-capsule")
    parser.add_argument(
        "--authorize-local-demo",
        action="store_true",
        required=True,
        help="supply external authorization for this one repo-local state write",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lab_root: Path = args.lab_root
    capsule_root: Path = args.capsule_root
    lab_root.parent.mkdir(parents=True, exist_ok=True)
    capsule_root.parent.mkdir(parents=True, exist_ok=True)
    authorization_root_ref = _ref(
        {
            "authorization": "explicit --authorize-local-demo invocation",
            "scope": "one reference counter state write",
        }
    )
    receipt = run_reference_counter_lab(
        lab_root,
        capsule_root,
        authorization_root_ref=authorization_root_ref,
    )
    print(
        canonical_text(
            {
                "evidence_capsule_path": str(capsule_root.resolve()),
                "lab_path": str(lab_root.resolve()),
                "receipt": receipt.model_dump(mode="json", by_alias=True),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
