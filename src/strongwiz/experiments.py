"""Causal ablation and honest fixed-denominator evaluation machinery."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from functools import partial
from threading import Lock

from pydantic import Field, model_validator

from strongwiz.canonical import ImmutableJSONValue, content_hash
from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt, PositiveInt

_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_STEP_PREFIX = "strongwiz:"
_OCCURRENCE_REGISTRY: set[tuple[str, str, str]] = set()
_OCCURRENCE_REGISTRY_LOCK = Lock()
_RETENTION_CLAIM_CEILING = (
    "mechanical arm evidence for this fixed workload only; caller-supplied isolation "
    "is unauthenticated, so causal attribution and general reasoning benefit are not "
    "established"
)
_TRACE_CLAIM_CEILING = (
    "mechanical trace-perturbation evidence within this matched heldout-first design "
    "only; caller-supplied isolation is unauthenticated, so causal trace use and general "
    "memory or reasoning claims are not established"
)


def _require_lowercase_sha256(value: str, label: str) -> None:
    if not _LOWERCASE_SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _require_clean_identity(value: str, label: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty identity without edge whitespace")


def _require_unique_hashes(values: Sequence[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    for value in values:
        _require_lowercase_sha256(value, label)


class AblationArm(StrEnum):
    DISCARD = "discard"
    CONTENT_CACHE = "content_cache"
    EARNED_RECEIPT = "earned_receipt"


class TraceArm(StrEnum):
    NORMAL = "normal"
    SWAPPED = "swapped"
    DUPLICATE_LEFT = "duplicate_left"
    DUPLICATE_RIGHT = "duplicate_right"
    ZEROED = "zeroed"
    NO_TRACE = "no_trace"


class AttemptDisposition(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED_MECHANISM = "failed_mechanism"
    FAILED_ASSERTION = "failed_assertion"
    FAILED_INFRASTRUCTURE = "failed_infrastructure"
    BLOCKED_EXTERNAL = "blocked_external"


class RetryPolicy(StrEnum):
    NONE = "none"
    INFRASTRUCTURE_ONLY = "infrastructure_only"


class IsolationEvidenceStatus(StrEnum):
    """What Strongwiz itself can establish about supplied isolation evidence."""

    CALLER_SUPPLIED_UNAUTHENTICATED = "caller_supplied_not_authenticated_process_isolation"


class ComparisonStatus(StrEnum):
    """Closed claim status for the evidence this in-process harness can establish."""

    MECHANICALLY_INCOMPLETE = "mechanically_incomplete"
    MECHANICALLY_COMPLETE_CAUSAL_NOT_ESTABLISHED = (
        "mechanically_complete_causal_not_established"
    )


def _trace_design_ref(
    *,
    visible_input_ref: str,
    conflicting_target_refs: Sequence[str],
    matched_schedule_ref: str,
    independent_oracle_ref: str,
    heldout_first: bool,
) -> str:
    """Hash only the preregistered, outcome-independent trace design."""

    return content_hash(
        {
            "schema": "strongwiz.trace-use-design.v1",
            "visible_input_ref": visible_input_ref,
            "conflicting_target_refs": tuple(conflicting_target_refs),
            "matched_schedule_ref": matched_schedule_ref,
            "independent_oracle_ref": independent_oracle_ref,
            "heldout_first": heldout_first,
        }
    )


class DeclaredRunnerFailure(RuntimeError):
    """Base class for a caller's explicit, categorized runner failure."""

    def __init__(self, category: str) -> None:
        _require_clean_identity(category, "runner failure category")
        self.category = category
        super().__init__(category)


class RunnerInfrastructureFailure(DeclaredRunnerFailure):
    """An explicitly declared infrastructure failure, not a mechanism result."""


class RunnerMechanismFailure(DeclaredRunnerFailure):
    """An explicitly declared failure of the mechanism under test."""


class DuplicateAttemptOccurrenceError(ValueError):
    """Raised when an experiment tries to reuse an in-process arm occurrence."""


class _RunnerAssertionFailure(DeclaredRunnerFailure):
    """An internal or runner-contract assertion failure."""


class AttemptStartReceipt(ContractModel):
    """Pre-outcome registration for one bounded experimental attempt."""

    schema_id: str = Field(default="strongwiz.attempt-start.v2", alias="schema")
    experiment_id: str
    arm_id: str
    attempt_occurrence_id: str
    scenario_ref: str
    frozen_runtime_ref: str
    design_ref: str | None = None
    isolation_evidence_ref: str
    isolation_evidence_status: IsolationEvidenceStatus = (
        IsolationEvidenceStatus.CALLER_SUPPLIED_UNAUTHENTICATED
    )
    fixed_denominator: PositiveInt
    seed: NonNegativeInt
    budget: CostVector
    retry_policy: RetryPolicy = RetryPolicy.NONE
    role: str

    @model_validator(mode="after")
    def validate_start(self) -> AttemptStartReceipt:
        required = (
            self.experiment_id,
            self.arm_id,
            self.attempt_occurrence_id,
            self.scenario_ref,
            self.frozen_runtime_ref,
            self.isolation_evidence_ref,
            self.role,
        )
        if not all(value.strip() for value in required):
            raise ValueError("attempt identity, evidence references, and role are required")
        _require_clean_identity(self.experiment_id, "experiment_id")
        _require_clean_identity(self.arm_id, "arm_id")
        _require_clean_identity(self.attempt_occurrence_id, "attempt_occurrence_id")
        _require_lowercase_sha256(self.scenario_ref, "scenario_ref")
        _require_lowercase_sha256(self.frozen_runtime_ref, "frozen_runtime_ref")
        if self.design_ref is not None:
            _require_lowercase_sha256(self.design_ref, "design_ref")
        _require_lowercase_sha256(self.isolation_evidence_ref, "isolation_evidence_ref")
        return self


class AttemptTerminalReceipt(ContractModel):
    """Fixed-denominator terminal evidence that never drops failed continuations."""

    schema_id: str = Field(default="strongwiz.attempt-terminal.v2", alias="schema")
    attempt_start_ref: str
    design_ref: str | None = None
    disposition: AttemptDisposition
    fixed_denominator: PositiveInt
    valid_steps: NonNegativeInt
    invalid_steps: NonNegativeInt
    unattempted_steps: NonNegativeInt
    total_costs: CostVector
    evidence_refs: tuple[str, ...] = ()
    failure_category: str | None = None
    retry_policy: RetryPolicy = RetryPolicy.NONE
    retry_eligible: bool = False
    claim_ceiling: str

    @model_validator(mode="after")
    def validate_terminal(self) -> AttemptTerminalReceipt:
        if (
            self.valid_steps + self.invalid_steps + self.unattempted_steps
            != self.fixed_denominator
        ):
            raise ValueError("terminal counts must retain the fixed denominator")
        if self.disposition is AttemptDisposition.COMPLETE and (
            self.invalid_steps or self.unattempted_steps
        ):
            raise ValueError("a complete attempt cannot hide invalid or unattempted steps")
        if self.retry_eligible and (
            self.retry_policy is not RetryPolicy.INFRASTRUCTURE_ONLY
            or self.disposition is not AttemptDisposition.FAILED_INFRASTRUCTURE
        ):
            raise ValueError(
                "retry eligibility is limited to preregistered infrastructure failures"
            )
        if self.disposition in {
            AttemptDisposition.FAILED_INFRASTRUCTURE,
            AttemptDisposition.FAILED_MECHANISM,
            AttemptDisposition.FAILED_ASSERTION,
            AttemptDisposition.BLOCKED_EXTERNAL,
        } and not (self.failure_category and self.failure_category.strip()):
            raise ValueError("failure and blocked dispositions require a category")
        if not self.attempt_start_ref.strip() or not self.claim_ceiling.strip():
            raise ValueError("attempt reference and claim ceiling are required")
        if self.disposition is AttemptDisposition.FAILED_MECHANISM and self.invalid_steps == 0:
            raise ValueError("mechanism failure must retain at least one invalid step")
        _require_lowercase_sha256(self.attempt_start_ref, "attempt_start_ref")
        if self.design_ref is not None:
            _require_lowercase_sha256(self.design_ref, "design_ref")
            if self.design_ref not in self.evidence_refs:
                raise ValueError("terminal evidence must retain its preregistered design_ref")
        _require_unique_hashes(self.evidence_refs, "terminal evidence_refs")
        return self


class StepResult(ContractModel):
    step_id: str
    attempted: bool
    valid: bool
    output: ImmutableJSONValue = None
    reason: str
    costs: CostVector = Field(default_factory=CostVector)
    receipt_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_step(self) -> StepResult:
        _require_clean_identity(self.step_id, "step_id")
        if not self.reason.strip():
            raise ValueError("step reason is required")
        if self.valid and not self.attempted:
            raise ValueError("an unattempted step cannot be valid")
        if not self.attempted and (self.output is not None or self.costs != CostVector()):
            raise ValueError("an unattempted step cannot claim output or costs")
        _require_unique_hashes(self.receipt_refs, "step receipt_refs")
        return self


class ArmResult(ContractModel):
    schema_id: str = Field(default="strongwiz.ablation-arm.v1", alias="schema")
    scenario_ref: str
    arm: AblationArm
    attempt_start: AttemptStartReceipt
    attempt_terminal: AttemptTerminalReceipt
    disposition: AttemptDisposition
    failure_category: str | None = None
    fixed_denominator: PositiveInt
    steps: tuple[StepResult, ...]
    total_costs: CostVector
    completed_steps: NonNegativeInt
    invalid_steps: NonNegativeInt
    unattempted_steps: NonNegativeInt
    output_digest: str

    @model_validator(mode="after")
    def validate_result(self) -> ArmResult:
        if len(self.steps) != self.fixed_denominator:
            raise ValueError("arm must retain the preregistered fixed denominator")
        completed = sum(step.attempted and step.valid for step in self.steps)
        invalid = sum(step.attempted and not step.valid for step in self.steps)
        unattempted = sum(not step.attempted for step in self.steps)
        if (completed, invalid, unattempted) != (
            self.completed_steps,
            self.invalid_steps,
            self.unattempted_steps,
        ):
            raise ValueError("arm summary disagrees with retained step evidence")
        expected_digest = content_hash([step.output for step in self.steps])
        if self.output_digest != expected_digest:
            raise ValueError("arm output digest disagrees with fixed-denominator outputs")
        if (
            self.disposition is AttemptDisposition.FAILED_INFRASTRUCTURE
            and not self.failure_category
        ):
            raise ValueError("infrastructure failure requires a category")
        if self.attempt_start.scenario_ref != self.scenario_ref:
            raise ValueError("attempt start and arm scenario disagree")
        if self.attempt_start.arm_id != self.arm.value:
            raise ValueError("attempt start and arm identity disagree")
        if self.attempt_start.fixed_denominator != self.fixed_denominator:
            raise ValueError("attempt start and arm denominator disagree")
        _validate_terminal_link(
            start=self.attempt_start,
            terminal=self.attempt_terminal,
            disposition=self.disposition,
            failure_category=self.failure_category,
            fixed_denominator=self.fixed_denominator,
            valid_steps=self.completed_steps,
            invalid_steps=self.invalid_steps,
            unattempted_steps=self.unattempted_steps,
            total_costs=self.total_costs,
        )
        _validate_budget_result(
            total_costs=self.total_costs,
            budget=self.attempt_start.budget,
            disposition=self.disposition,
            failure_category=self.failure_category,
        )
        _require_lowercase_sha256(self.scenario_ref, "arm scenario_ref")
        _require_lowercase_sha256(self.output_digest, "arm output_digest")
        return self


def _validate_exact_arm_membership(
    actual: Sequence[StrEnum], expected: Sequence[StrEnum], label: str
) -> None:
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError(f"{label} requires exactly one result for every registered arm")


def _validate_matched_attempt_starts(
    starts: Sequence[AttemptStartReceipt],
    *,
    experiment_id: str,
    scenario_ref: str,
    design_ref: str | None,
    label: str,
) -> None:
    if not starts:
        raise ValueError(f"{label} requires preregistered attempt starts")
    if any(start.experiment_id != experiment_id for start in starts):
        raise ValueError(f"{label} starts must bind the result experiment")
    if any(start.scenario_ref != scenario_ref for start in starts):
        raise ValueError(f"{label} starts must bind the same preregistered scenario")
    if any(start.design_ref != design_ref for start in starts):
        raise ValueError(f"{label} starts must bind the same preregistered design")

    reference = starts[0]
    if any(
        (
            start.frozen_runtime_ref,
            start.fixed_denominator,
            start.seed,
            start.budget,
            start.retry_policy,
            start.isolation_evidence_status,
        )
        != (
            reference.frozen_runtime_ref,
            reference.fixed_denominator,
            reference.seed,
            reference.budget,
            reference.retry_policy,
            reference.isolation_evidence_status,
        )
        for start in starts[1:]
    ):
        raise ValueError(
            f"{label} starts must match runtime, denominator, seed, budget, "
            "retry policy, and isolation-evidence status"
        )


def _expected_comparison_status(
    dispositions: Sequence[AttemptDisposition],
) -> ComparisonStatus:
    if not all(disposition is AttemptDisposition.COMPLETE for disposition in dispositions):
        return ComparisonStatus.MECHANICALLY_INCOMPLETE
    return ComparisonStatus.MECHANICALLY_COMPLETE_CAUSAL_NOT_ESTABLISHED


def _validate_comparison_claim(
    *,
    comparison_status: ComparisonStatus,
    comparable: bool,
    dispositions: Sequence[AttemptDisposition],
    label: str,
) -> None:
    expected = _expected_comparison_status(dispositions)
    if comparison_status is not expected:
        raise ValueError(f"{label} comparison status disagrees with arm dispositions")
    if comparable:
        raise ValueError(
            f"{label} cannot claim causal comparability from caller-supplied "
            "unauthenticated isolation"
        )


class AblationResult(ContractModel):
    schema_id: str = Field(default="strongwiz.retention-ablation.v2", alias="schema")
    experiment_id: str
    scenario_ref: str
    arms: tuple[ArmResult, ...]
    comparison_status: ComparisonStatus
    comparable: bool
    claim_ceiling: str

    @model_validator(mode="after")
    def validate_arms(self) -> AblationResult:
        _validate_exact_arm_membership(
            tuple(arm.arm for arm in self.arms), tuple(AblationArm), "retention ablation"
        )
        if any(arm.scenario_ref != self.scenario_ref for arm in self.arms):
            raise ValueError("ablation arms must share an identical scenario")
        starts = tuple(arm.attempt_start for arm in self.arms)
        _validate_matched_attempt_starts(
            starts,
            experiment_id=self.experiment_id,
            scenario_ref=self.scenario_ref,
            design_ref=None,
            label="retention ablation",
        )
        if any(
            arm.attempt_start.role != f"retention ablation arm {arm.arm.value}"
            for arm in self.arms
        ):
            raise ValueError("retention ablation arm roles must match their registered arms")
        occurrence_ids = tuple(arm.attempt_start.attempt_occurrence_id for arm in self.arms)
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("ablation arms require unique attempt occurrence identities")
        isolation_refs = tuple(arm.attempt_start.isolation_evidence_ref for arm in self.arms)
        if len(set(isolation_refs)) != len(isolation_refs):
            raise ValueError("ablation arms require unique isolation evidence references")
        _validate_comparison_claim(
            comparison_status=self.comparison_status,
            comparable=self.comparable,
            dispositions=tuple(arm.disposition for arm in self.arms),
            label="retention ablation",
        )
        if self.claim_ceiling != _RETENTION_CLAIM_CEILING:
            raise ValueError("retention ablation claim ceiling is not control-derived")
        _require_lowercase_sha256(self.scenario_ref, "ablation scenario_ref")
        return self


class TraceArmResult(ContractModel):
    schema_id: str = Field(default="strongwiz.trace-use-arm.v2", alias="schema")
    scenario_ref: str
    design_ref: str
    arm: TraceArm
    attempt_start: AttemptStartReceipt
    attempt_terminal: AttemptTerminalReceipt
    disposition: AttemptDisposition
    failure_category: str | None = None
    fixed_denominator: PositiveInt
    steps: tuple[StepResult, ...]
    total_costs: CostVector
    valid_steps: NonNegativeInt
    invalid_steps: NonNegativeInt
    unattempted_steps: NonNegativeInt
    output_digest: str

    @model_validator(mode="after")
    def validate_result(self) -> TraceArmResult:
        if len(self.steps) != self.fixed_denominator:
            raise ValueError("trace arm must retain the preregistered fixed denominator")
        counts = (
            sum(step.attempted and step.valid for step in self.steps),
            sum(step.attempted and not step.valid for step in self.steps),
            sum(not step.attempted for step in self.steps),
        )
        if counts != (self.valid_steps, self.invalid_steps, self.unattempted_steps):
            raise ValueError("trace-arm summary disagrees with retained step evidence")
        if self.output_digest != content_hash([step.output for step in self.steps]):
            raise ValueError("trace-arm output digest disagrees with retained outputs")
        if (
            self.disposition is AttemptDisposition.FAILED_INFRASTRUCTURE
            and not self.failure_category
        ):
            raise ValueError("infrastructure failure requires a category")
        if self.attempt_start.scenario_ref != self.scenario_ref:
            raise ValueError("attempt start and trace-arm scenario disagree")
        if self.attempt_start.design_ref != self.design_ref:
            raise ValueError("attempt start and trace-arm design disagree")
        if self.attempt_terminal.design_ref != self.design_ref:
            raise ValueError("attempt terminal and trace-arm design disagree")
        if self.attempt_start.arm_id != self.arm.value:
            raise ValueError("attempt start and trace-arm identity disagree")
        if self.attempt_start.fixed_denominator != self.fixed_denominator:
            raise ValueError("attempt start and trace-arm denominator disagree")
        _validate_terminal_link(
            start=self.attempt_start,
            terminal=self.attempt_terminal,
            disposition=self.disposition,
            failure_category=self.failure_category,
            fixed_denominator=self.fixed_denominator,
            valid_steps=self.valid_steps,
            invalid_steps=self.invalid_steps,
            unattempted_steps=self.unattempted_steps,
            total_costs=self.total_costs,
        )
        _validate_budget_result(
            total_costs=self.total_costs,
            budget=self.attempt_start.budget,
            disposition=self.disposition,
            failure_category=self.failure_category,
        )
        _require_lowercase_sha256(self.scenario_ref, "trace-arm scenario_ref")
        _require_lowercase_sha256(self.design_ref, "trace-arm design_ref")
        _require_lowercase_sha256(self.output_digest, "trace-arm output_digest")
        return self


class TraceUseAblationResult(ContractModel):
    """Test whether a retained trace is causally used rather than merely present."""

    schema_id: str = Field(default="strongwiz.trace-use-ablation.v2", alias="schema")
    experiment_id: str
    scenario_ref: str
    design_ref: str
    visible_input_ref: str
    conflicting_target_refs: tuple[str, ...]
    matched_schedule_ref: str
    independent_oracle_ref: str
    heldout_first: bool
    arms: tuple[TraceArmResult, ...]
    comparison_status: ComparisonStatus
    comparable: bool
    claim_ceiling: str

    @model_validator(mode="after")
    def validate_design(self) -> TraceUseAblationResult:
        if not self.heldout_first:
            raise ValueError("trace-use ablations require heldout-first construction")
        if len(set(self.conflicting_target_refs)) < 2:
            raise ValueError("identical visible input must bind conflicting target cases")
        expected_design_ref = _trace_design_ref(
            visible_input_ref=self.visible_input_ref,
            conflicting_target_refs=self.conflicting_target_refs,
            matched_schedule_ref=self.matched_schedule_ref,
            independent_oracle_ref=self.independent_oracle_ref,
            heldout_first=self.heldout_first,
        )
        if self.design_ref != expected_design_ref:
            raise ValueError("trace-use design_ref disagrees with its preregistered inputs")
        _validate_exact_arm_membership(
            tuple(arm.arm for arm in self.arms), tuple(TraceArm), "trace-use ablation"
        )
        if any(arm.scenario_ref != self.scenario_ref for arm in self.arms):
            raise ValueError("trace-use arms must share one scenario")
        if any(arm.design_ref != self.design_ref for arm in self.arms):
            raise ValueError("trace-use arms must bind the result design")
        starts = tuple(arm.attempt_start for arm in self.arms)
        _validate_matched_attempt_starts(
            starts,
            experiment_id=self.experiment_id,
            scenario_ref=self.scenario_ref,
            design_ref=self.design_ref,
            label="trace-use ablation",
        )
        if any(
            arm.attempt_start.role != f"trace-use ablation arm {arm.arm.value}"
            for arm in self.arms
        ):
            raise ValueError("trace-use arm roles must match their registered arms")
        occurrence_ids = tuple(arm.attempt_start.attempt_occurrence_id for arm in self.arms)
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("trace-use arms require unique attempt occurrence identities")
        isolation_refs = tuple(arm.attempt_start.isolation_evidence_ref for arm in self.arms)
        if len(set(isolation_refs)) != len(isolation_refs):
            raise ValueError("trace-use arms require unique isolation evidence references")
        _validate_comparison_claim(
            comparison_status=self.comparison_status,
            comparable=self.comparable,
            dispositions=tuple(arm.disposition for arm in self.arms),
            label="trace-use ablation",
        )
        if self.claim_ceiling != _TRACE_CLAIM_CEILING:
            raise ValueError("trace-use claim ceiling is not control-derived")
        refs = (
            self.scenario_ref,
            self.design_ref,
            self.visible_input_ref,
            *self.conflicting_target_refs,
            self.matched_schedule_ref,
            self.independent_oracle_ref,
        )
        for value in refs:
            _require_lowercase_sha256(value, "trace-use evidence reference")
        return self


IsolatedRunner = Callable[[], Sequence[StepResult]]
ArmRunnerFactory = Callable[[AblationArm, AttemptStartReceipt], IsolatedRunner]
TraceArmRunnerFactory = Callable[[TraceArm, AttemptStartReceipt], IsolatedRunner]


def _validate_arm_bindings[ArmKey: (AblationArm, TraceArm)](
    expected_arms: Sequence[ArmKey],
    occurrence_ids: Mapping[ArmKey, str],
    isolation_refs: Mapping[ArmKey, str],
) -> None:
    expected = set(expected_arms)
    if set(occurrence_ids) != expected:
        raise ValueError("attempt_occurrence_ids must bind every arm exactly once")
    if set(isolation_refs) != expected:
        raise ValueError("isolation_evidence_refs must bind every arm exactly once")

    occurrences = tuple(occurrence_ids[arm] for arm in expected_arms)
    for occurrence_id in occurrences:
        _require_clean_identity(occurrence_id, "attempt occurrence identity")
    if len(set(occurrences)) != len(occurrences):
        raise ValueError("attempt occurrence identities must be unique across arms")

    supplied_refs = tuple(isolation_refs[arm] for arm in expected_arms)
    _require_unique_hashes(supplied_refs, "per-arm isolation evidence refs")


def _reserve_occurrences(starts: Sequence[AttemptStartReceipt]) -> None:
    keys = tuple(
        (start.experiment_id, start.arm_id, start.attempt_occurrence_id) for start in starts
    )
    if len(set(keys)) != len(keys):
        raise DuplicateAttemptOccurrenceError(
            "duplicate experiment+arm occurrence identity in one registration"
        )
    with _OCCURRENCE_REGISTRY_LOCK:
        duplicates = tuple(key for key in keys if key in _OCCURRENCE_REGISTRY)
        if duplicates:
            rendered = ", ".join(": ".join(key) for key in duplicates)
            raise DuplicateAttemptOccurrenceError(
                f"experiment+arm occurrence already registered in this process: {rendered}"
            )
        _OCCURRENCE_REGISTRY.update(keys)


def _budget_overruns(total_costs: CostVector, budget: CostVector) -> tuple[str, ...]:
    return tuple(
        name
        for name in CostVector.model_fields
        if getattr(total_costs, name) > getattr(budget, name)
    )


def _validate_budget_result(
    *,
    total_costs: CostVector,
    budget: CostVector,
    disposition: AttemptDisposition,
    failure_category: str | None,
) -> None:
    overruns = _budget_overruns(total_costs, budget)
    budget_failure = bool(
        failure_category and failure_category.startswith("component_budget_exceeded:")
    )
    if overruns and (
        disposition is not AttemptDisposition.FAILED_ASSERTION or not budget_failure
    ):
        raise ValueError("componentwise budget overrun must be an assertion failure")
    if not overruns and budget_failure:
        raise ValueError("budget failure category requires a retained component overrun")


def _validate_terminal_link(
    *,
    start: AttemptStartReceipt,
    terminal: AttemptTerminalReceipt,
    disposition: AttemptDisposition,
    failure_category: str | None,
    fixed_denominator: int,
    valid_steps: int,
    invalid_steps: int,
    unattempted_steps: int,
    total_costs: CostVector,
) -> None:
    expected = (
        terminal.attempt_start_ref == start.digest
        and terminal.design_ref == start.design_ref
        and terminal.disposition is disposition
        and terminal.failure_category == failure_category
        and terminal.fixed_denominator == fixed_denominator
        and terminal.valid_steps == valid_steps
        and terminal.invalid_steps == invalid_steps
        and terminal.unattempted_steps == unattempted_steps
        and terminal.total_costs == total_costs
        and terminal.retry_policy is start.retry_policy
    )
    if not expected:
        raise ValueError("attempt terminal receipt does not match its arm result")
    if start.isolation_evidence_ref not in terminal.evidence_refs:
        raise ValueError("attempt terminal must retain its supplied isolation evidence ref")


def _fixed_steps(
    returned: Sequence[StepResult],
    fixed_denominator: int,
    *,
    allow_reserved_ids: bool = False,
) -> tuple[StepResult, ...]:
    if len(returned) > fixed_denominator:
        raise _RunnerAssertionFailure("fixed_denominator_exceeded")
    step_ids = tuple(step.step_id for step in returned)
    if len(set(step_ids)) != len(step_ids):
        raise _RunnerAssertionFailure("duplicate_step_identity")
    if not allow_reserved_ids and any(
        step_id.startswith(_RESERVED_STEP_PREFIX) for step_id in step_ids
    ):
        raise _RunnerAssertionFailure("reserved_step_identity")
    return tuple(returned) + tuple(
        StepResult(
            step_id=f"{_RESERVED_STEP_PREFIX}unattempted:{index:05d}",
            attempted=False,
            valid=False,
            reason="arm terminated before this fixed-denominator step",
        )
        for index in range(len(returned), fixed_denominator)
    )


def _total_costs(steps: Sequence[StepResult]) -> CostVector:
    costs = CostVector()
    for step in steps:
        costs = costs + step.costs
    return costs


def _enforce_componentwise_budget(
    returned: Sequence[StepResult], budget: CostVector
) -> tuple[tuple[StepResult, ...], str | None]:
    accepted: list[StepResult] = []
    cumulative = CostVector()
    for step in returned:
        candidate = cumulative + step.costs
        overruns = _budget_overruns(candidate, budget)
        if overruns:
            category = f"component_budget_exceeded:{','.join(overruns)}"
            values = step.model_dump(mode="python", by_alias=True)
            values["valid"] = False
            values["reason"] = category
            accepted.append(StepResult.model_validate(values))
            return tuple(accepted), category
        accepted.append(step)
        cumulative = candidate
    return tuple(accepted), None


def _failure_steps(
    *,
    disposition: AttemptDisposition,
    category: str,
    fixed_denominator: int,
) -> tuple[StepResult, ...]:
    marker = StepResult(
        step_id=f"{_RESERVED_STEP_PREFIX}failure:{disposition.value}",
        attempted=disposition is AttemptDisposition.FAILED_MECHANISM,
        valid=False,
        reason=category,
    )
    return _fixed_steps((marker,), fixed_denominator, allow_reserved_ids=True)


def _execute_isolated_runner(
    factory: Callable[[], IsolatedRunner],
    *,
    fixed_denominator: int,
    budget: CostVector,
    prior_runners: list[IsolatedRunner],
) -> tuple[tuple[StepResult, ...], AttemptDisposition, str | None]:
    try:
        runner = factory()
        if any(runner is prior for prior in prior_runners):
            raise _RunnerAssertionFailure("runner_instance_reused")
        prior_runners.append(runner)
        returned = tuple(runner())
        _fixed_steps(returned, fixed_denominator)
        budgeted, budget_failure = _enforce_componentwise_budget(returned, budget)
        steps = _fixed_steps(budgeted, fixed_denominator)
        if budget_failure:
            return steps, AttemptDisposition.FAILED_ASSERTION, budget_failure
    except RunnerInfrastructureFailure as error:
        category = f"infrastructure:{error.category}"
        return (
            _failure_steps(
                disposition=AttemptDisposition.FAILED_INFRASTRUCTURE,
                category=category,
                fixed_denominator=fixed_denominator,
            ),
            AttemptDisposition.FAILED_INFRASTRUCTURE,
            category,
        )
    except RunnerMechanismFailure as error:
        category = f"mechanism:{error.category}"
        return (
            _failure_steps(
                disposition=AttemptDisposition.FAILED_MECHANISM,
                category=category,
                fixed_denominator=fixed_denominator,
            ),
            AttemptDisposition.FAILED_MECHANISM,
            category,
        )
    except _RunnerAssertionFailure as error:
        category = f"runner_assertion:{error.category}"
        return (
            _failure_steps(
                disposition=AttemptDisposition.FAILED_ASSERTION,
                category=category,
                fixed_denominator=fixed_denominator,
            ),
            AttemptDisposition.FAILED_ASSERTION,
            category,
        )
    except Exception as error:
        category = f"runner_assertion:{type(error).__name__}"
        return (
            _failure_steps(
                disposition=AttemptDisposition.FAILED_ASSERTION,
                category=category,
                fixed_denominator=fixed_denominator,
            ),
            AttemptDisposition.FAILED_ASSERTION,
            category,
        )
    valid = sum(step.attempted and step.valid for step in steps)
    invalid = sum(step.attempted and not step.valid for step in steps)
    if invalid:
        return steps, AttemptDisposition.FAILED_MECHANISM, "mechanism:invalid_step"
    if valid == fixed_denominator:
        return steps, AttemptDisposition.COMPLETE, None
    return steps, AttemptDisposition.PARTIAL, None


def _terminal_receipt(
    *,
    start: AttemptStartReceipt,
    disposition: AttemptDisposition,
    failure_category: str | None,
    steps: Sequence[StepResult],
) -> AttemptTerminalReceipt:
    valid = sum(step.attempted and step.valid for step in steps)
    invalid = sum(step.attempted and not step.valid for step in steps)
    unattempted = sum(not step.attempted for step in steps)
    output_digest = content_hash([step.output for step in steps])
    evidence_candidates = (
        start.isolation_evidence_ref,
        *((start.design_ref,) if start.design_ref is not None else ()),
        output_digest,
        *(receipt for step in steps for receipt in step.receipt_refs),
    )
    evidence_refs = tuple(dict.fromkeys(evidence_candidates))
    claim_ceiling = {
        AttemptDisposition.COMPLETE: "completed arm evidence for this registered workload only",
        AttemptDisposition.PARTIAL: "partial arm evidence with denominator retained",
        AttemptDisposition.FAILED_MECHANISM: (
            "mechanism failure evidence for this registered arm only"
        ),
        AttemptDisposition.FAILED_ASSERTION: (
            "runner or harness assertion failure; no mechanism comparison earned"
        ),
        AttemptDisposition.FAILED_INFRASTRUCTURE: (
            "declared infrastructure failure; no mechanism comparison earned"
        ),
        AttemptDisposition.BLOCKED_EXTERNAL: (
            "external boundary evidence; no mechanism comparison earned"
        ),
    }[disposition]
    return AttemptTerminalReceipt(
        attempt_start_ref=start.digest,
        design_ref=start.design_ref,
        disposition=disposition,
        fixed_denominator=start.fixed_denominator,
        valid_steps=valid,
        invalid_steps=invalid,
        unattempted_steps=unattempted,
        total_costs=_total_costs(steps),
        evidence_refs=evidence_refs,
        failure_category=failure_category,
        retry_policy=start.retry_policy,
        retry_eligible=False,
        claim_ceiling=claim_ceiling,
    )


def run_retention_ablation(
    *,
    experiment_id: str,
    scenario: object,
    fixed_denominator: int,
    frozen_runtime_ref: str,
    seed: int,
    budget: CostVector,
    attempt_occurrence_ids: Mapping[AblationArm, str],
    isolation_evidence_refs: Mapping[AblationArm, str],
    runner_factory: ArmRunnerFactory,
) -> AblationResult:
    """Run three distinct runner instances without conditioning metrics on success."""

    if fixed_denominator <= 0:
        raise ValueError("experiment identity and positive denominator are required")
    _require_clean_identity(experiment_id, "experiment_id")
    _validate_arm_bindings(tuple(AblationArm), attempt_occurrence_ids, isolation_evidence_refs)
    scenario_ref = content_hash(scenario)
    starts = {
        arm: AttemptStartReceipt(
            experiment_id=experiment_id,
            arm_id=arm.value,
            attempt_occurrence_id=attempt_occurrence_ids[arm],
            scenario_ref=scenario_ref,
            frozen_runtime_ref=frozen_runtime_ref,
            isolation_evidence_ref=isolation_evidence_refs[arm],
            fixed_denominator=fixed_denominator,
            seed=seed,
            budget=budget,
            retry_policy=RetryPolicy.NONE,
            role=f"retention ablation arm {arm.value}",
        )
        for arm in AblationArm
    }
    _reserve_occurrences(tuple(starts.values()))
    arms: list[ArmResult] = []
    runners: list[IsolatedRunner] = []
    for arm in AblationArm:
        start = starts[arm]
        padded, disposition, failure_category = _execute_isolated_runner(
            partial(runner_factory, arm, start),
            fixed_denominator=fixed_denominator,
            budget=budget,
            prior_runners=runners,
        )
        completed = sum(step.attempted and step.valid for step in padded)
        invalid = sum(step.attempted and not step.valid for step in padded)
        unattempted = sum(not step.attempted for step in padded)
        total_costs = _total_costs(padded)
        output_digest = content_hash([step.output for step in padded])
        terminal = _terminal_receipt(
            start=start,
            disposition=disposition,
            failure_category=failure_category,
            steps=padded,
        )
        arms.append(
            ArmResult(
                scenario_ref=scenario_ref,
                arm=arm,
                attempt_start=start,
                attempt_terminal=terminal,
                disposition=disposition,
                failure_category=failure_category,
                fixed_denominator=fixed_denominator,
                steps=padded,
                total_costs=total_costs,
                completed_steps=completed,
                invalid_steps=invalid,
                unattempted_steps=unattempted,
                output_digest=output_digest,
            )
        )
    comparison_status = _expected_comparison_status(tuple(arm.disposition for arm in arms))
    return AblationResult(
        experiment_id=experiment_id,
        scenario_ref=scenario_ref,
        arms=tuple(arms),
        comparison_status=comparison_status,
        comparable=False,
        claim_ceiling=_RETENTION_CLAIM_CEILING,
    )


def run_trace_use_ablation(
    *,
    experiment_id: str,
    scenario: object,
    visible_input: object,
    conflicting_targets: Sequence[object],
    matched_schedule: object,
    independent_oracle: object,
    heldout_first: bool,
    fixed_denominator: int,
    frozen_runtime_ref: str,
    seed: int,
    budget: CostVector,
    attempt_occurrence_ids: Mapping[TraceArm, str],
    isolation_evidence_refs: Mapping[TraceArm, str],
    runner_factory: TraceArmRunnerFactory,
) -> TraceUseAblationResult:
    """Run trace perturbations under one heldout-first, matched schedule."""

    if fixed_denominator <= 0:
        raise ValueError("experiment identity and positive denominator are required")
    _require_clean_identity(experiment_id, "experiment_id")
    if not heldout_first:
        raise ValueError("trace-use construction must be declared heldout-first")
    _validate_arm_bindings(tuple(TraceArm), attempt_occurrence_ids, isolation_evidence_refs)
    target_refs = tuple(content_hash(target) for target in conflicting_targets)
    if len(set(target_refs)) < 2:
        raise ValueError("trace-use design requires at least two conflicting targets")
    scenario_ref = content_hash(scenario)
    visible_input_ref = content_hash(visible_input)
    matched_schedule_ref = content_hash(matched_schedule)
    independent_oracle_ref = content_hash(independent_oracle)
    design_ref = _trace_design_ref(
        visible_input_ref=visible_input_ref,
        conflicting_target_refs=target_refs,
        matched_schedule_ref=matched_schedule_ref,
        independent_oracle_ref=independent_oracle_ref,
        heldout_first=heldout_first,
    )
    starts = {
        arm: AttemptStartReceipt(
            experiment_id=experiment_id,
            arm_id=arm.value,
            attempt_occurrence_id=attempt_occurrence_ids[arm],
            scenario_ref=scenario_ref,
            frozen_runtime_ref=frozen_runtime_ref,
            design_ref=design_ref,
            isolation_evidence_ref=isolation_evidence_refs[arm],
            fixed_denominator=fixed_denominator,
            seed=seed,
            budget=budget,
            retry_policy=RetryPolicy.NONE,
            role=f"trace-use ablation arm {arm.value}",
        )
        for arm in TraceArm
    }
    _reserve_occurrences(tuple(starts.values()))
    arms: list[TraceArmResult] = []
    runners: list[IsolatedRunner] = []
    for arm in TraceArm:
        start = starts[arm]
        steps, disposition, failure_category = _execute_isolated_runner(
            partial(runner_factory, arm, start),
            fixed_denominator=fixed_denominator,
            budget=budget,
            prior_runners=runners,
        )
        valid = sum(step.attempted and step.valid for step in steps)
        invalid = sum(step.attempted and not step.valid for step in steps)
        unattempted = sum(not step.attempted for step in steps)
        total_costs = _total_costs(steps)
        output_digest = content_hash([step.output for step in steps])
        terminal = _terminal_receipt(
            start=start,
            disposition=disposition,
            failure_category=failure_category,
            steps=steps,
        )
        arms.append(
            TraceArmResult(
                scenario_ref=scenario_ref,
                design_ref=design_ref,
                arm=arm,
                attempt_start=start,
                attempt_terminal=terminal,
                disposition=disposition,
                failure_category=failure_category,
                fixed_denominator=fixed_denominator,
                steps=steps,
                total_costs=total_costs,
                valid_steps=valid,
                invalid_steps=invalid,
                unattempted_steps=unattempted,
                output_digest=output_digest,
            )
        )
    return TraceUseAblationResult(
        experiment_id=experiment_id,
        scenario_ref=scenario_ref,
        design_ref=design_ref,
        visible_input_ref=visible_input_ref,
        conflicting_target_refs=target_refs,
        matched_schedule_ref=matched_schedule_ref,
        independent_oracle_ref=independent_oracle_ref,
        heldout_first=heldout_first,
        arms=tuple(arms),
        comparison_status=_expected_comparison_status(tuple(arm.disposition for arm in arms)),
        comparable=False,
        claim_ceiling=_TRACE_CLAIM_CEILING,
    )
