from __future__ import annotations

from dataclasses import dataclass

import pytest

from strongwiz.canonical import content_hash
from strongwiz.contracts import CostVector
from strongwiz.experiments import (
    AblationArm,
    AblationResult,
    AttemptDisposition,
    AttemptStartReceipt,
    AttemptTerminalReceipt,
    ComparisonStatus,
    DuplicateAttemptOccurrenceError,
    IsolationEvidenceStatus,
    RetryPolicy,
    RunnerInfrastructureFailure,
    RunnerMechanismFailure,
    StepResult,
    TraceArm,
    TraceUseAblationResult,
    run_retention_ablation,
    run_trace_use_ablation,
)
from strongwiz.planning import SearchDisposition, SearchEdge, bounded_astar
from tests.support import ref


def retention_bindings(
    label: str,
) -> tuple[dict[AblationArm, str], dict[AblationArm, str]]:
    return (
        {arm: f"{label}:{arm.value}:occurrence" for arm in AblationArm},
        {arm: ref(f"{label}:{arm.value}:isolation") for arm in AblationArm},
    )


def trace_bindings(label: str) -> tuple[dict[TraceArm, str], dict[TraceArm, str]]:
    return (
        {arm: f"{label}:{arm.value}:occurrence" for arm in TraceArm},
        {arm: ref(f"{label}:{arm.value}:isolation") for arm in TraceArm},
    )


def complete_retention_result(label: str) -> AblationResult:
    occurrence_ids, isolation_refs = retention_bindings(label)
    return run_retention_ablation(
        experiment_id=label,
        scenario={"workload": "matched"},
        fixed_denominator=1,
        frozen_runtime_ref=ref(f"{label}:runtime"),
        seed=7,
        budget=CostVector(compute_units=10),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=lambda arm, _start: (
            lambda: (
                StepResult(
                    step_id="step-0",
                    attempted=True,
                    valid=True,
                    output={"arm": arm.value},
                    reason="completed registered workload",
                    costs=CostVector(compute_units=1),
                ),
            )
        ),
    )


def complete_trace_result(label: str) -> TraceUseAblationResult:
    occurrence_ids, isolation_refs = trace_bindings(label)
    return run_trace_use_ablation(
        experiment_id=label,
        scenario={"workload": "matched-trace"},
        visible_input={"tokens": [1, 2]},
        conflicting_targets=({"target": 0}, {"target": 1}),
        matched_schedule={"seed": 7, "updates": 1},
        independent_oracle={"implementation": "test-oracle"},
        heldout_first=True,
        fixed_denominator=1,
        frozen_runtime_ref=ref(f"{label}:runtime"),
        seed=7,
        budget=CostVector(compute_units=10),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=lambda arm, _start: (
            lambda: (
                StepResult(
                    step_id="step-0",
                    attempted=True,
                    valid=True,
                    output={"arm": arm.value},
                    reason="completed registered trace workload",
                    costs=CostVector(compute_units=1),
                ),
            )
        ),
    )


@dataclass(frozen=True)
class LineProblem:
    target: int

    def state_key(self, state: int) -> str:
        return f"{state:04d}"

    def is_goal(self, state: int) -> bool:
        return state == self.target

    def neighbors(self, state: int) -> tuple[SearchEdge[int, str], ...]:
        edges: list[SearchEdge[int, str]] = []
        if state < self.target + 2:
            edges.append(SearchEdge(action="right", state=state + 1))
        if state > 0:
            edges.append(SearchEdge(action="left", state=state - 1))
        return tuple(edges)

    def heuristic(self, state: int) -> int:
        return abs(self.target - state)

    def action_key(self, action: str) -> str:
        return action


def test_bounded_astar_finds_deterministic_shortest_plan() -> None:
    result = bounded_astar(LineProblem(target=3), 0, max_expansions=10)
    assert result.disposition is SearchDisposition.FOUND
    assert result.states == (0, 1, 2, 3)
    assert result.actions == ("right", "right", "right")
    assert result.total_cost == 3
    assert bounded_astar(LineProblem(target=3), 0, max_expansions=10) == result


def test_bounded_astar_preserves_budget_exhaustion() -> None:
    result = bounded_astar(LineProblem(target=10), 0, max_expansions=2)
    assert result.disposition is SearchDisposition.BUDGET_EXHAUSTED
    assert result.actions == ()
    assert result.total_cost is None


def test_bounded_astar_stops_an_unbounded_neighbor_source() -> None:
    @dataclass(frozen=True)
    class Unbounded:
        def state_key(self, state: int) -> str:
            return str(state)

        def is_goal(self, _state: int) -> bool:
            return False

        def neighbors(self, state: int) -> object:
            def generate() -> object:
                index = 0
                while True:
                    yield SearchEdge(action=f"edge-{index}", state=state + index + 1)
                    index += 1

            return generate()

        def heuristic(self, _state: int) -> int:
            return 0

        def action_key(self, action: str) -> str:
            return action

    result = bounded_astar(Unbounded(), 0, max_expansions=2, max_neighbors_per_expansion=3)
    assert result.disposition is SearchDisposition.BUDGET_EXHAUSTED
    assert "neighbor ceiling" in result.reason


def test_retention_ablation_runs_same_fixed_denominator_for_all_arms() -> None:
    def runner_factory(arm: AblationArm, _start: AttemptStartReceipt) -> object:
        compute = {
            AblationArm.DISCARD: 10,
            AblationArm.CONTENT_CACHE: 6,
            AblationArm.EARNED_RECEIPT: 3,
        }[arm]

        def run() -> tuple[StepResult, ...]:
            return tuple(
                StepResult(
                    step_id=f"step-{index}",
                    attempted=True,
                    valid=True,
                    output={"answer": index},
                    reason="completed",
                    costs=CostVector(compute_units=compute),
                )
                for index in range(3)
            )

        return run

    occurrence_ids, isolation_refs = retention_bindings("retain-vs-discard")
    result = run_retention_ablation(
        experiment_id="retain-vs-discard",
        scenario={"queries": [1, 2, 3]},
        fixed_denominator=3,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(compute_units=100),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    assert not result.comparable
    assert (
        result.comparison_status
        is ComparisonStatus.MECHANICALLY_COMPLETE_CAUSAL_NOT_ESTABLISHED
    )
    assert all(arm.disposition is AttemptDisposition.COMPLETE for arm in result.arms)
    costs = {arm.arm: arm.total_costs.compute_units for arm in result.arms}
    assert costs[AblationArm.DISCARD] == 30
    assert costs[AblationArm.CONTENT_CACHE] == 18
    assert costs[AblationArm.EARNED_RECEIPT] == 9
    assert all(
        arm.attempt_terminal.attempt_start_ref == arm.attempt_start.digest
        for arm in result.arms
    )
    assert all(
        arm.attempt_start.isolation_evidence_ref in arm.attempt_terminal.evidence_refs
        for arm in result.arms
    )
    assert all(
        arm.attempt_start.isolation_evidence_status
        is IsolationEvidenceStatus.CALLER_SUPPLIED_UNAUTHENTICATED
        for arm in result.arms
    )
    assert "general reasoning benefit are not established" in result.claim_ceiling


def test_ablation_results_require_exactly_one_result_per_registered_arm() -> None:
    retention = complete_retention_result("exact-retention-arms")
    with pytest.raises(ValueError, match="exactly one result"):
        retention.model_copy(update={"arms": (*retention.arms, retention.arms[0])})

    trace = complete_trace_result("exact-trace-arms")
    with pytest.raises(ValueError, match="exactly one result"):
        trace.model_copy(update={"arms": (*trace.arms, trace.arms[0])})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("frozen_runtime_ref", ref("spliced-runtime")),
        ("seed", 8),
        ("budget", CostVector(compute_units=11)),
        ("retry_policy", RetryPolicy.INFRASTRUCTURE_ONLY),
    ),
)
def test_retention_result_rejects_preregistered_invariant_splices(
    field: str, value: object
) -> None:
    result = complete_retention_result(f"matched-starts:{field}")
    original = result.arms[0]
    spliced_start = original.attempt_start.model_copy(update={field: value})
    terminal_updates: dict[str, object] = {"attempt_start_ref": spliced_start.digest}
    if field == "retry_policy":
        terminal_updates["retry_policy"] = value
    spliced_terminal = original.attempt_terminal.model_copy(update=terminal_updates)
    spliced_arm = original.model_copy(
        update={"attempt_start": spliced_start, "attempt_terminal": spliced_terminal}
    )
    arms = (spliced_arm, *result.arms[1:])

    with pytest.raises(ValueError, match="starts must match runtime"):
        result.model_copy(update={"arms": arms})


def test_distinct_runner_wrappers_over_shared_mutable_state_never_claim_causality() -> None:
    shared = {"count": 0}

    def runner_factory(_arm: AblationArm, _start: AttemptStartReceipt) -> object:
        def run() -> tuple[StepResult, ...]:
            shared["count"] += 1
            return (
                StepResult(
                    step_id="shared-state-step",
                    attempted=True,
                    valid=True,
                    output={"shared_count": shared["count"]},
                    reason="mechanically completed through a distinct wrapper",
                ),
            )

        return run

    occurrence_ids, isolation_refs = retention_bindings("shared-mutable-state")
    result = run_retention_ablation(
        experiment_id="shared-mutable-state",
        scenario={"same": True},
        fixed_denominator=1,
        frozen_runtime_ref=ref("shared-mutable-state:runtime"),
        seed=7,
        budget=CostVector(),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )

    assert shared["count"] == len(AblationArm)
    assert all(arm.disposition is AttemptDisposition.COMPLETE for arm in result.arms)
    assert (
        result.comparison_status
        is ComparisonStatus.MECHANICALLY_COMPLETE_CAUSAL_NOT_ESTABLISHED
    )
    assert not result.comparable
    with pytest.raises(ValueError, match="cannot claim causal comparability"):
        result.model_copy(update={"comparable": True})
    with pytest.raises(ValueError, match="claim ceiling is not control-derived"):
        result.model_copy(update={"claim_ceiling": "causal comparison established"})


def test_retention_ablation_never_drops_unattempted_denominator() -> None:
    def runner_factory(arm: AblationArm, _start: AttemptStartReceipt) -> object:
        def run() -> tuple[StepResult, ...]:
            if arm is AblationArm.DISCARD:
                return ()
            return (
                StepResult(
                    step_id="only-step",
                    attempted=True,
                    valid=False,
                    reason="mechanism failed",
                ),
            )

        return run

    occurrence_ids, isolation_refs = retention_bindings("failed-arms")
    result = run_retention_ablation(
        experiment_id="failed-arms",
        scenario={"same": True},
        fixed_denominator=2,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(compute_units=100),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    assert not result.comparable
    assert result.comparison_status is ComparisonStatus.MECHANICALLY_INCOMPLETE
    discard = next(arm for arm in result.arms if arm.arm is AblationArm.DISCARD)
    assert discard.unattempted_steps == 2
    assert len(discard.steps) == 2
    other = next(arm for arm in result.arms if arm.arm is AblationArm.CONTENT_CACHE)
    assert other.disposition is AttemptDisposition.FAILED_MECHANISM
    assert other.failure_category == "mechanism:invalid_step"
    assert other.invalid_steps == 1
    assert other.unattempted_steps == 1


def test_retention_ablation_records_runner_failure_without_dropping_the_arm() -> None:
    def runner_factory(arm: AblationArm, _start: AttemptStartReceipt) -> object:
        if arm is AblationArm.CONTENT_CACHE:
            raise RunnerInfrastructureFailure("worker_unavailable")
        return lambda: ()

    occurrence_ids, isolation_refs = retention_bindings("runner-failure")
    result = run_retention_ablation(
        experiment_id="runner-failure",
        scenario={"same": True},
        fixed_denominator=2,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(compute_units=10),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    failed = next(arm for arm in result.arms if arm.arm is AblationArm.CONTENT_CACHE)
    assert failed.disposition is AttemptDisposition.FAILED_INFRASTRUCTURE
    assert failed.failure_category == "infrastructure:worker_unavailable"
    assert len(failed.steps) == 2
    assert failed.unattempted_steps == 2
    assert failed.attempt_terminal.disposition is AttemptDisposition.FAILED_INFRASTRUCTURE
    assert not result.comparable


def test_retention_ablation_refuses_shared_runner_state_between_arms() -> None:
    def shared_runner() -> tuple[StepResult, ...]:
        return ()

    occurrence_ids, isolation_refs = retention_bindings("shared-runner")
    result = run_retention_ablation(
        experiment_id="shared-runner",
        scenario={"same": True},
        fixed_denominator=1,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(compute_units=10),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=lambda _arm, _start: shared_runner,
    )
    assert result.arms[0].disposition is AttemptDisposition.PARTIAL
    assert all(
        arm.disposition is AttemptDisposition.FAILED_ASSERTION for arm in result.arms[1:]
    )
    assert all(
        arm.failure_category == "runner_assertion:runner_instance_reused"
        for arm in result.arms[1:]
    )


def test_retention_ablation_enforces_each_budget_component() -> None:
    def runner_factory(_arm: AblationArm, _start: AttemptStartReceipt) -> object:
        return lambda: (
            StepResult(
                step_id="over-environment-budget",
                attempted=True,
                valid=True,
                output={"computed": True},
                reason="runner claimed completion",
                costs=CostVector(environment_actions=1, compute_units=1),
            ),
            StepResult(
                step_id="must-not-be-consumed",
                attempted=True,
                valid=True,
                output={"computed": "later"},
                reason="later runner output",
            ),
        )

    occurrence_ids, isolation_refs = retention_bindings("component-budget")
    result = run_retention_ablation(
        experiment_id="component-budget",
        scenario={"same": True},
        fixed_denominator=2,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(environment_actions=0, compute_units=100),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    assert not result.comparable
    assert all(arm.disposition is AttemptDisposition.FAILED_ASSERTION for arm in result.arms)
    assert all(
        arm.failure_category == "component_budget_exceeded:environment_actions"
        for arm in result.arms
    )
    assert all(arm.invalid_steps == 1 and arm.unattempted_steps == 1 for arm in result.arms)
    assert all(len(arm.steps) == 2 for arm in result.arms)


def test_unexpected_runner_exception_is_assertion_not_infrastructure() -> None:
    def runner_factory(arm: AblationArm, _start: AttemptStartReceipt) -> object:
        if arm is AblationArm.CONTENT_CACHE:
            raise RuntimeError("unclassified failure")
        return lambda: ()

    occurrence_ids, isolation_refs = retention_bindings("unexpected-runner-exception")
    result = run_retention_ablation(
        experiment_id="unexpected-runner-exception",
        scenario={"same": True},
        fixed_denominator=1,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    failed = next(arm for arm in result.arms if arm.arm is AblationArm.CONTENT_CACHE)
    assert failed.disposition is AttemptDisposition.FAILED_ASSERTION
    assert failed.failure_category == "runner_assertion:RuntimeError"
    assert failed.attempt_terminal.disposition is AttemptDisposition.FAILED_ASSERTION


def test_explicit_mechanism_failure_remains_distinct() -> None:
    def runner_factory(arm: AblationArm, _start: AttemptStartReceipt) -> object:
        if arm is AblationArm.EARNED_RECEIPT:
            raise RunnerMechanismFailure("receipt_transfer_falsified")
        return lambda: ()

    occurrence_ids, isolation_refs = retention_bindings("declared-mechanism-failure")
    result = run_retention_ablation(
        experiment_id="declared-mechanism-failure",
        scenario={"same": True},
        fixed_denominator=1,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    failed = next(arm for arm in result.arms if arm.arm is AblationArm.EARNED_RECEIPT)
    assert failed.disposition is AttemptDisposition.FAILED_MECHANISM
    assert failed.failure_category == "mechanism:receipt_transfer_falsified"
    assert failed.invalid_steps == 1


@pytest.mark.parametrize(
    ("label", "fixed_denominator", "steps", "failure_category"),
    (
        (
            "too-many",
            1,
            (
                StepResult(step_id="one", attempted=True, valid=True, reason="done"),
                StepResult(step_id="two", attempted=True, valid=True, reason="done"),
            ),
            "runner_assertion:fixed_denominator_exceeded",
        ),
        (
            "duplicate-step",
            2,
            (
                StepResult(step_id="same", attempted=True, valid=True, reason="done"),
                StepResult(step_id="same", attempted=True, valid=True, reason="done"),
            ),
            "runner_assertion:duplicate_step_identity",
        ),
        (
            "reserved-step",
            1,
            (
                StepResult(
                    step_id="strongwiz:forged",
                    attempted=True,
                    valid=True,
                    reason="done",
                ),
            ),
            "runner_assertion:reserved_step_identity",
        ),
    ),
)
def test_runner_contract_failures_are_assertions(
    label: str,
    fixed_denominator: int,
    steps: tuple[StepResult, ...],
    failure_category: str,
) -> None:
    occurrence_ids, isolation_refs = retention_bindings(f"runner-contract:{label}")
    result = run_retention_ablation(
        experiment_id=f"runner-contract:{label}",
        scenario={"same": True},
        fixed_denominator=fixed_denominator,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=lambda _arm, _start: lambda: steps,
    )
    assert all(arm.disposition is AttemptDisposition.FAILED_ASSERTION for arm in result.arms)
    assert all(arm.failure_category == failure_category for arm in result.arms)


def test_occurrence_registry_refuses_reusing_experiment_arm_occurrences() -> None:
    occurrence_ids, isolation_refs = retention_bindings("occurrence-registry")
    kwargs = {
        "experiment_id": "occurrence-registry",
        "scenario": {"same": True},
        "fixed_denominator": 1,
        "frozen_runtime_ref": ref("runtime"),
        "seed": 7,
        "budget": CostVector(),
        "attempt_occurrence_ids": occurrence_ids,
        "isolation_evidence_refs": isolation_refs,
        "runner_factory": lambda _arm, _start: lambda: (),
    }
    run_retention_ablation(**kwargs)  # type: ignore[arg-type]
    try:
        run_retention_ablation(**kwargs)  # type: ignore[arg-type]
    except DuplicateAttemptOccurrenceError as error:
        assert "already registered" in str(error)
    else:
        raise AssertionError("one process must not reuse an experiment+arm occurrence")


def test_arm_bindings_require_unique_occurrences_and_isolation_refs() -> None:
    occurrence_ids, isolation_refs = retention_bindings("binding-uniqueness")
    occurrence_ids[AblationArm.CONTENT_CACHE] = occurrence_ids[AblationArm.DISCARD]
    try:
        run_retention_ablation(
            experiment_id="duplicate-occurrences",
            scenario={},
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )
    except ValueError as error:
        assert "occurrence identities must be unique" in str(error)
    else:
        raise AssertionError("per-arm occurrence identities must be unique")

    occurrence_ids, isolation_refs = retention_bindings("isolation-uniqueness")
    isolation_refs[AblationArm.CONTENT_CACHE] = isolation_refs[AblationArm.DISCARD]
    try:
        run_retention_ablation(
            experiment_id="duplicate-isolation",
            scenario={},
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )
    except ValueError as error:
        assert "isolation evidence refs must be unique" in str(error)
    else:
        raise AssertionError("per-arm isolation evidence references must be unique")


def test_arm_bindings_require_every_registered_arm() -> None:
    occurrence_ids, isolation_refs = retention_bindings("missing-bindings")
    del occurrence_ids[AblationArm.DISCARD]
    with pytest.raises(ValueError, match="attempt_occurrence_ids must bind every arm"):
        run_retention_ablation(
            experiment_id="missing-occurrence-binding",
            scenario={},
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )

    occurrence_ids, isolation_refs = retention_bindings("missing-isolation-binding")
    del isolation_refs[AblationArm.DISCARD]
    with pytest.raises(ValueError, match="isolation_evidence_refs must bind every arm"):
        run_retention_ablation(
            experiment_id="missing-isolation-binding",
            scenario={},
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )


def test_attempt_receipts_bind_runtime_budget_and_fixed_denominator() -> None:
    start = AttemptStartReceipt(
        experiment_id="exp-1",
        arm_id="primary",
        attempt_occurrence_id="attempt-1",
        scenario_ref=ref("scenario"),
        frozen_runtime_ref=ref("runtime"),
        isolation_evidence_ref=ref("isolated-worker"),
        fixed_denominator=4,
        seed=7,
        budget=CostVector(compute_units=100, environment_actions=4),
        retry_policy=RetryPolicy.INFRASTRUCTURE_ONLY,
        role="primary preregistered attempt",
    )
    terminal = AttemptTerminalReceipt(
        attempt_start_ref=start.digest,
        disposition=AttemptDisposition.FAILED_INFRASTRUCTURE,
        fixed_denominator=4,
        valid_steps=1,
        invalid_steps=1,
        unattempted_steps=2,
        total_costs=CostVector(compute_units=25, environment_actions=2),
        evidence_refs=(ref("isolated-worker"), ref("terminal-evidence")),
        failure_category="worker_lost",
        retry_policy=RetryPolicy.INFRASTRUCTURE_ONLY,
        retry_eligible=True,
        claim_ceiling="infrastructure evidence only",
    )
    assert terminal.attempt_start_ref == start.digest
    assert terminal.retry_eligible
    assert (
        start.isolation_evidence_status
        is IsolationEvidenceStatus.CALLER_SUPPLIED_UNAUTHENTICATED
    )


def test_attempt_receipts_reject_outcome_conditioned_retry_and_hidden_steps() -> None:
    base = {
        "attempt_start_ref": ref("receipt"),
        "disposition": AttemptDisposition.FAILED_MECHANISM,
        "fixed_denominator": 2,
        "valid_steps": 1,
        "invalid_steps": 1,
        "unattempted_steps": 0,
        "total_costs": CostVector(),
        "evidence_refs": (ref("terminal-evidence"),),
        "failure_category": "hypothesis_falsified",
        "retry_policy": RetryPolicy.INFRASTRUCTURE_ONLY,
        "retry_eligible": True,
        "claim_ceiling": "bounded test",
    }
    try:
        AttemptTerminalReceipt.model_validate(base)
    except ValueError as error:
        assert "infrastructure failures" in str(error)
    else:
        raise AssertionError("mechanism failure must not become retry eligible")

    base["retry_eligible"] = False
    base["valid_steps"] = 0
    try:
        AttemptTerminalReceipt.model_validate(base)
    except ValueError as error:
        assert "fixed denominator" in str(error)
    else:
        raise AssertionError("terminal receipt must retain every registered step")


def test_attempt_receipts_require_lowercase_sha256_evidence_refs() -> None:
    try:
        AttemptStartReceipt(
            experiment_id="hash-validation",
            arm_id="primary",
            attempt_occurrence_id="hash-validation:primary:occurrence",
            scenario_ref=ref("scenario"),
            frozen_runtime_ref="A" * 64,
            isolation_evidence_ref=ref("isolation"),
            fixed_denominator=1,
            seed=7,
            budget=CostVector(),
            role="hash validation",
        )
    except ValueError as error:
        assert "frozen_runtime_ref must be a lowercase SHA-256" in str(error)
    else:
        raise AssertionError("uppercase runtime hashes must be refused")

    try:
        StepResult(
            step_id="bad-evidence",
            attempted=True,
            valid=True,
            reason="invalid evidence reference",
            receipt_refs=("B" * 64,),
        )
    except ValueError as error:
        assert "step receipt_refs must be a lowercase SHA-256" in str(error)
    else:
        raise AssertionError("uppercase step evidence hashes must be refused")

    try:
        AttemptTerminalReceipt(
            attempt_start_ref=ref("start"),
            disposition=AttemptDisposition.COMPLETE,
            fixed_denominator=1,
            valid_steps=1,
            invalid_steps=0,
            unattempted_steps=0,
            total_costs=CostVector(),
            evidence_refs=("C" * 64,),
            claim_ceiling="bounded",
        )
    except ValueError as error:
        assert "terminal evidence_refs must be a lowercase SHA-256" in str(error)
    else:
        raise AssertionError("uppercase terminal evidence hashes must be refused")


@pytest.mark.parametrize(
    ("values", "message"),
    (
        (
            {
                "step_id": "blank-reason",
                "attempted": True,
                "valid": True,
                "reason": " ",
            },
            "step reason is required",
        ),
        (
            {
                "step_id": "unattempted-valid",
                "attempted": False,
                "valid": True,
                "reason": "impossible",
            },
            "unattempted step cannot be valid",
        ),
        (
            {
                "step_id": "unattempted-cost",
                "attempted": False,
                "valid": False,
                "reason": "impossible",
                "costs": CostVector(compute_units=1),
            },
            "unattempted step cannot claim output or costs",
        ),
    ),
)
def test_step_receipts_refuse_internal_assertion_conflicts(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        StepResult.model_validate(values)


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        (
            {
                "disposition": AttemptDisposition.COMPLETE,
                "valid_steps": 0,
                "unattempted_steps": 1,
            },
            "complete attempt cannot hide invalid or unattempted steps",
        ),
        (
            {
                "disposition": AttemptDisposition.FAILED_ASSERTION,
                "failure_category": None,
            },
            "failure and blocked dispositions require a category",
        ),
        (
            {
                "disposition": AttemptDisposition.FAILED_MECHANISM,
                "failure_category": "mechanism:no_invalid_step",
            },
            "mechanism failure must retain at least one invalid step",
        ),
    ),
)
def test_terminal_receipts_refuse_incoherent_failure_summaries(
    updates: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "attempt_start_ref": ref("start"),
        "disposition": AttemptDisposition.PARTIAL,
        "fixed_denominator": 1,
        "valid_steps": 0,
        "invalid_steps": 0,
        "unattempted_steps": 1,
        "total_costs": CostVector(),
        "evidence_refs": (ref("terminal-evidence"),),
        "claim_ceiling": "bounded",
    }
    values.update(updates)
    with pytest.raises(ValueError, match=message):
        AttemptTerminalReceipt.model_validate(values)


def test_trace_use_ablation_runs_all_perturbations_under_one_design() -> None:
    def runner_factory(arm: TraceArm, _start: AttemptStartReceipt) -> object:
        def run() -> tuple[StepResult, ...]:
            return (
                StepResult(
                    step_id="heldout-0",
                    attempted=True,
                    valid=True,
                    output={"arm": arm.value},
                    reason="matched deterministic evaluation",
                    costs=CostVector(compute_units=1),
                ),
            )

        return run

    occurrence_ids, isolation_refs = trace_bindings("trace-use")
    result = run_trace_use_ablation(
        experiment_id="trace-use",
        scenario={"pair": "same-visible-input"},
        visible_input={"tokens": [1, 2]},
        conflicting_targets=({"target": 0}, {"target": 1}),
        matched_schedule={"seed": 7, "updates": 10},
        independent_oracle={"implementation": "reference-v1"},
        heldout_first=True,
        fixed_denominator=1,
        frozen_runtime_ref=ref("runtime"),
        seed=7,
        budget=CostVector(compute_units=10),
        attempt_occurrence_ids=occurrence_ids,
        isolation_evidence_refs=isolation_refs,
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )
    assert not result.comparable
    assert (
        result.comparison_status
        is ComparisonStatus.MECHANICALLY_COMPLETE_CAUSAL_NOT_ESTABLISHED
    )
    assert {arm.arm for arm in result.arms} == set(TraceArm)
    assert all(arm.total_costs.compute_units == 1 for arm in result.arms)
    assert all(
        arm.attempt_terminal.attempt_start_ref == arm.attempt_start.digest
        for arm in result.arms
    )
    assert all(
        arm.design_ref
        == arm.attempt_start.design_ref
        == arm.attempt_terminal.design_ref
        == result.design_ref
        for arm in result.arms
    )
    assert "general memory or reasoning claims are not established" in result.claim_ceiling


@pytest.mark.parametrize(
    "update",
    (
        {"visible_input_ref": ref("spliced-visible-input")},
        {
            "conflicting_target_refs": (
                ref("spliced-target-left"),
                ref("spliced-target-right"),
            )
        },
        {"matched_schedule_ref": ref("spliced-schedule")},
        {"independent_oracle_ref": ref("spliced-oracle")},
        {"heldout_first": False},
    ),
)
def test_trace_design_ref_reconstructs_every_preregistered_input(
    update: dict[str, object],
) -> None:
    result = complete_trace_result(f"trace-design-splice:{next(iter(update))}")
    with pytest.raises(ValueError):
        result.model_copy(update=update)


def test_trace_design_ref_cannot_be_recomputed_only_at_the_aggregate_result() -> None:
    result = complete_trace_result("trace-design-aggregate-splice")
    spliced_visible_input_ref = ref("aggregate-spliced-visible-input")
    forged_design_ref = content_hash(
        {
            "schema": "strongwiz.trace-use-design.v1",
            "visible_input_ref": spliced_visible_input_ref,
            "conflicting_target_refs": result.conflicting_target_refs,
            "matched_schedule_ref": result.matched_schedule_ref,
            "independent_oracle_ref": result.independent_oracle_ref,
            "heldout_first": result.heldout_first,
        }
    )

    with pytest.raises(ValueError, match="arms must bind the result design"):
        result.model_copy(
            update={
                "visible_input_ref": spliced_visible_input_ref,
                "design_ref": forged_design_ref,
            }
        )


def test_trace_use_ablation_rejects_nonconflicting_targets() -> None:
    occurrence_ids, isolation_refs = trace_bindings("bad-trace-use")
    try:
        run_trace_use_ablation(
            experiment_id="bad-trace-use",
            scenario={},
            visible_input={},
            conflicting_targets=({"same": True}, {"same": True}),
            matched_schedule={},
            independent_oracle={},
            heldout_first=True,
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(compute_units=1),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )
    except ValueError as error:
        assert "conflicting targets" in str(error)
    else:
        raise AssertionError("trace-use design requires target conflict")


def test_trace_use_ablation_requires_heldout_first_registration() -> None:
    occurrence_ids, isolation_refs = trace_bindings("not-heldout-first")
    try:
        run_trace_use_ablation(
            experiment_id="not-heldout-first",
            scenario={},
            visible_input={},
            conflicting_targets=({"left": True}, {"right": True}),
            matched_schedule={},
            independent_oracle={},
            heldout_first=False,
            fixed_denominator=1,
            frozen_runtime_ref=ref("runtime"),
            seed=7,
            budget=CostVector(compute_units=1),
            attempt_occurrence_ids=occurrence_ids,
            isolation_evidence_refs=isolation_refs,
            runner_factory=lambda _arm, _start: lambda: (),
        )
    except ValueError as error:
        assert "heldout-first" in str(error)
    else:
        raise AssertionError("trace-use design must be declared heldout-first")
