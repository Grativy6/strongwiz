"""Sequential adaptive calibration campaigns with explicit learned-stack transfer."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt, PositiveInt
from strongwiz.lab import RunDisposition

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INHERITANCE_EXCLUSIONS = (
    "action_sequences",
    "authorization",
    "domain_state",
    "private_reasoning",
)


class CurriculumError(ValueError):
    """An adaptive campaign crossed an undeclared stage boundary."""


class CurriculumMode(StrEnum):
    BASELINE = "baseline"
    ACQUIRE = "acquire"
    DEEPEN = "deepen"
    FINISH_OR_REASSESS = "finish_or_reassess"


class NextStageDecision(StrEnum):
    ADVANCE = "advance"
    FINISH = "finish"
    REASSESS = "reassess"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_refs(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    for value in values:
        _require_digest(value, label)
    return values


class CurriculumStage(ContractModel):
    schema_id: str = Field(default="strongwiz.curriculum-stage.v1", alias="schema")
    stage_id: str
    ordinal: NonNegativeInt
    mode: CurriculumMode
    purpose: str
    resource_budget: CostVector
    may_inherit_shorthand: bool
    may_inherit_mechanics: bool
    success_condition_ref: str

    @model_validator(mode="after")
    def validate_stage(self) -> CurriculumStage:
        if not self.stage_id.strip() or not self.purpose.strip():
            raise ValueError("curriculum stage identity and purpose are required")
        if self.resource_budget.wall_clock_ms <= 0:
            raise ValueError("curriculum stages require a positive wall-clock bound")
        _require_digest(self.success_condition_ref, "stage success condition")
        if self.mode is CurriculumMode.BASELINE and (
            self.may_inherit_shorthand or self.may_inherit_mechanics
        ):
            raise ValueError("baseline stage cannot inherit a learned stack")
        return self


class AdaptiveCurriculumPlan(ContractModel):
    schema_id: str = Field(default="strongwiz.adaptive-curriculum.v1", alias="schema")
    campaign_id: str
    objective: str
    stages: tuple[CurriculumStage, ...]
    final_authority_source: str
    claim_ceiling: str = "adaptive development campaign; not independent clean-room trials"
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_plan(self) -> AdaptiveCurriculumPlan:
        if not all(
            value.strip()
            for value in (self.campaign_id, self.objective, self.final_authority_source)
        ):
            raise ValueError(
                "campaign identity, objective, and terminal authority are required"
            )
        if len(self.stages) < 2:
            raise ValueError("adaptive curriculum requires multiple bounded stages")
        if tuple(stage.ordinal for stage in self.stages) != tuple(range(len(self.stages))):
            raise ValueError("curriculum stage ordinals must be contiguous from zero")
        identities = tuple(stage.stage_id for stage in self.stages)
        if len(set(identities)) != len(identities):
            raise ValueError("curriculum stage identities must be unique")
        if self.stages[0].mode is not CurriculumMode.BASELINE:
            raise ValueError("curriculum must begin with a baseline stage")
        if self.stages[-1].mode is not CurriculumMode.FINISH_OR_REASSESS:
            raise ValueError("curriculum must end with a finish-or-reassess stage")
        return self


def four_stage_curriculum(
    *,
    campaign_id: str,
    objective: str,
    success_condition_ref: str,
    final_authority_source: str,
    final_wall_minutes: int,
) -> AdaptiveCurriculumPlan:
    """Return the owner-proposed 30/60/90/final bounded sequence."""

    if final_wall_minutes <= 0:
        raise CurriculumError("the final stage requires a positive caller-supplied bound")

    def stage(
        ordinal: int,
        mode: CurriculumMode,
        minutes: int,
        purpose: str,
        *,
        inherit: bool,
    ) -> CurriculumStage:
        return CurriculumStage(
            stage_id=f"{campaign_id}-stage-{ordinal + 1}",
            ordinal=ordinal,
            mode=mode,
            purpose=purpose,
            resource_budget=CostVector(wall_clock_ms=minutes * 60 * 1000),
            may_inherit_shorthand=inherit,
            may_inherit_mechanics=inherit,
            success_condition_ref=success_condition_ref,
        )

    return AdaptiveCurriculumPlan(
        campaign_id=campaign_id,
        objective=objective,
        final_authority_source=final_authority_source,
        stages=(
            stage(
                0,
                CurriculumMode.BASELINE,
                30,
                "establish behavior and a blank codebook baseline",
                inherit=False,
            ),
            stage(
                1,
                CurriculumMode.ACQUIRE,
                60,
                "acquire mechanics and evaluate codebook adaptations",
                inherit=True,
            ),
            stage(
                2,
                CurriculumMode.DEEPEN,
                90,
                "deepen planning with the explicitly inherited learned stack",
                inherit=True,
            ),
            stage(
                3,
                CurriculumMode.FINISH_OR_REASSESS,
                final_wall_minutes,
                "pursue the terminal objective with a frozen stack or return for reassessment",
                inherit=True,
            ),
        ),
    )


class LearnedStackTransfer(ContractModel):
    """Declared run-to-run input that cannot transfer action traces or authority."""

    schema_id: str = Field(default="strongwiz.learned-stack-transfer.v1", alias="schema")
    transfer_id: str
    source_stage_handoff_ref: str
    source_run_seal_ref: str
    target_stage_ref: str
    shorthand_transfer_ref: str | None = None
    shorthand_adoption_ref: str | None = None
    mechanic_refs: tuple[str, ...] = ()
    other_learned_fact_refs: tuple[str, ...] = ()
    validation_refs: tuple[str, ...]
    excluded_material: tuple[str, ...] = _INHERITANCE_EXCLUSIONS
    transfers_authority: bool = False
    claim_ceiling: str = "declared learned representation and mechanics only"

    @model_validator(mode="after")
    def validate_transfer(self) -> LearnedStackTransfer:
        if not self.transfer_id.strip() or not self.claim_ceiling.strip():
            raise ValueError("learned-stack transfer identity and claim ceiling are required")
        for value in (
            self.source_stage_handoff_ref,
            self.source_run_seal_ref,
            self.target_stage_ref,
        ):
            _require_digest(value, "learned-stack transfer binding")
        if self.shorthand_transfer_ref is not None:
            _require_digest(self.shorthand_transfer_ref, "shorthand transfer reference")
        if self.shorthand_adoption_ref is not None:
            _require_digest(self.shorthand_adoption_ref, "shorthand adoption reference")
        if (self.shorthand_transfer_ref is None) != (self.shorthand_adoption_ref is None):
            raise ValueError(
                "shorthand inheritance requires both transfer and adoption decisions"
            )
        _require_refs(self.mechanic_refs, "mechanic references")
        _require_refs(self.other_learned_fact_refs, "learned fact references")
        _require_refs(self.validation_refs, "transfer validation references")
        if not self.validation_refs:
            raise ValueError("learned-stack transfer requires validation evidence")
        if not (
            self.shorthand_transfer_ref or self.mechanic_refs or self.other_learned_fact_refs
        ):
            raise ValueError("learned-stack transfer cannot be empty")
        if self.excluded_material != _INHERITANCE_EXCLUSIONS or self.transfers_authority:
            raise ValueError("learned stack cannot carry excluded state or authority")
        return self


class CurriculumStageStart(ContractModel):
    schema_id: str = Field(default="strongwiz.curriculum-stage-start.v1", alias="schema")
    campaign_ref: str
    stage_ref: str
    frozen_stack_ref: str
    predecessor_handoff_ref: str | None = None
    learned_stack_transfer_ref: str | None = None
    occurrence: PositiveInt
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_start(self) -> CurriculumStageStart:
        for required_ref in (self.campaign_ref, self.stage_ref, self.frozen_stack_ref):
            _require_digest(required_ref, "stage start binding")
        if (self.predecessor_handoff_ref is None) != (self.learned_stack_transfer_ref is None):
            raise ValueError("adaptive stage inheritance requires both handoff and transfer")
        for optional_ref in (self.predecessor_handoff_ref, self.learned_stack_transfer_ref):
            if optional_ref is not None:
                _require_digest(optional_ref, "stage predecessor binding")
        return self


class CurriculumStageHandoff(ContractModel):
    schema_id: str = Field(default="strongwiz.curriculum-stage-handoff.v1", alias="schema")
    stage_start_ref: str
    stage_ref: str
    run_seal_ref: str
    disposition: RunDisposition
    completion_genuinely_observed: bool
    terminal_state: str
    progress_evidence_refs: tuple[str, ...]
    active_codebook_ref: str | None = None
    retained_mechanic_refs: tuple[str, ...] = ()
    next_decision: NextStageDecision
    concise_result: str
    authority: str = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_handoff(self) -> CurriculumStageHandoff:
        for value in (self.stage_start_ref, self.stage_ref, self.run_seal_ref):
            _require_digest(value, "stage handoff binding")
        if not self.terminal_state.strip() or not self.concise_result.strip():
            raise ValueError("stage handoff requires terminal state and concise result")
        _require_refs(self.progress_evidence_refs, "progress evidence references")
        _require_refs(self.retained_mechanic_refs, "retained mechanic references")
        if not self.progress_evidence_refs:
            raise ValueError("stage handoff requires progress evidence")
        if self.active_codebook_ref is not None:
            _require_digest(self.active_codebook_ref, "active codebook reference")
        success = self.disposition is RunDisposition.SUCCESS_OBSERVED
        if self.completion_genuinely_observed != success:
            raise ValueError("stage success disposition and observation marker disagree")
        if success and self.next_decision is not NextStageDecision.FINISH:
            raise ValueError("observed completion must stop the curriculum")
        if not success and self.next_decision is NextStageDecision.FINISH:
            raise ValueError("curriculum cannot claim finish without observed completion")
        return self


class CurriculumCheckpoint(ContractModel):
    schema_id: str = Field(default="strongwiz.curriculum-checkpoint.v1", alias="schema")
    plan: AdaptiveCurriculumPlan
    completed_handoffs: tuple[CurriculumStageHandoff, ...] = ()
    active_start: CurriculumStageStart | None = None

    @model_validator(mode="after")
    def validate_checkpoint(self) -> CurriculumCheckpoint:
        if len(self.completed_handoffs) > len(self.plan.stages):
            raise ValueError("curriculum contains too many completed stages")
        for index, handoff in enumerate(self.completed_handoffs):
            if handoff.stage_ref != self.plan.stages[index].digest:
                raise ValueError("curriculum handoff order disagrees with its plan")
            if (
                index < len(self.completed_handoffs) - 1
                and handoff.next_decision is not NextStageDecision.ADVANCE
            ):
                raise ValueError("curriculum history continues after a stopping decision")
        if self.active_start is not None:
            index = len(self.completed_handoffs)
            if index >= len(self.plan.stages):
                raise ValueError("completed curriculum cannot have an active stage")
            if self.active_start.stage_ref != self.plan.stages[index].digest:
                raise ValueError("active curriculum stage disagrees with plan order")
        return self


class AdaptiveCurriculumController:
    """Single-active-stage coordinator; it never runs a model or domain itself."""

    def __init__(self, plan: AdaptiveCurriculumPlan) -> None:
        self.plan = plan
        self._handoffs: list[CurriculumStageHandoff] = []
        self._active: CurriculumStageStart | None = None

    @classmethod
    def restore(cls, checkpoint: CurriculumCheckpoint) -> AdaptiveCurriculumController:
        controller = cls(checkpoint.plan)
        controller._handoffs = list(checkpoint.completed_handoffs)
        controller._active = checkpoint.active_start
        return controller

    @property
    def active_start(self) -> CurriculumStageStart | None:
        return self._active

    def start_next(
        self,
        *,
        frozen_stack_ref: str,
        transfer: LearnedStackTransfer | None = None,
    ) -> CurriculumStageStart:
        if self._active is not None:
            raise CurriculumError("only one curriculum stage may run at a time")
        index = len(self._handoffs)
        if index >= len(self.plan.stages):
            raise CurriculumError("curriculum has no remaining stage")
        stage = self.plan.stages[index]
        predecessor = None if not self._handoffs else self._handoffs[-1]
        if (
            predecessor is not None
            and predecessor.next_decision is not NextStageDecision.ADVANCE
        ):
            raise CurriculumError("curriculum stopped for finish or reassessment")
        if index == 0:
            if transfer is not None:
                raise CurriculumError("baseline stage cannot inherit a learned stack")
        else:
            if transfer is None or predecessor is None:
                raise CurriculumError(
                    "adaptive successor requires an explicit learned-stack transfer"
                )
            if (
                transfer.source_stage_handoff_ref != predecessor.digest
                or transfer.source_run_seal_ref != predecessor.run_seal_ref
                or transfer.target_stage_ref != stage.digest
            ):
                raise CurriculumError("learned-stack transfer crosses stage lineage")
            if transfer.shorthand_transfer_ref is not None and not stage.may_inherit_shorthand:
                raise CurriculumError("stage does not permit shorthand inheritance")
            if transfer.mechanic_refs and not stage.may_inherit_mechanics:
                raise CurriculumError("stage does not permit mechanic inheritance")
        self._active = CurriculumStageStart(
            campaign_ref=self.plan.digest,
            stage_ref=stage.digest,
            frozen_stack_ref=frozen_stack_ref,
            predecessor_handoff_ref=None if predecessor is None else predecessor.digest,
            learned_stack_transfer_ref=None if transfer is None else transfer.digest,
            occurrence=index + 1,
        )
        return self._active

    def finish_active(self, handoff: CurriculumStageHandoff) -> CurriculumStageHandoff:
        active = self._active
        if active is None:
            raise CurriculumError("no curriculum stage is active")
        index = len(self._handoffs)
        stage = self.plan.stages[index]
        if handoff.stage_start_ref != active.digest or handoff.stage_ref != stage.digest:
            raise CurriculumError("stage handoff does not close the active occurrence")
        if stage.mode is CurriculumMode.FINISH_OR_REASSESS:
            expected = (
                NextStageDecision.FINISH
                if handoff.completion_genuinely_observed
                else NextStageDecision.REASSESS
            )
            if handoff.next_decision is not expected:
                raise CurriculumError(
                    "final stage must finish on success or return for reassessment"
                )
        elif handoff.next_decision is NextStageDecision.FINISH and not (
            handoff.completion_genuinely_observed
        ):
            raise CurriculumError("nonterminal evidence cannot finish the campaign")
        self._handoffs.append(handoff)
        self._active = None
        return handoff

    def checkpoint(self) -> CurriculumCheckpoint:
        return CurriculumCheckpoint(
            plan=self.plan,
            completed_handoffs=tuple(self._handoffs),
            active_start=self._active,
        )
