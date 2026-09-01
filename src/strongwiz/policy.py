"""Two-speed deliberation and goal-relevant proposal ordering."""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from strongwiz.contracts import (
    CandidateProposal,
    ContractModel,
    DeliberationMode,
    NonNegativeInt,
)


class ReasoningDepth(StrEnum):
    FAST = "fast"
    DEEP = "deep"


class DeepTrigger(StrEnum):
    STARTUP_UNCERTAINTY = "startup_uncertainty"
    STRUCTURAL_NOVELTY = "structural_novelty"
    MEANINGFUL_CONTRADICTION = "meaningful_contradiction"
    REOPENING = "reopening"
    INVALID_PLAN = "invalid_plan"
    HIGH_GOAL_UNCERTAINTY = "high_goal_uncertainty"
    REPEATED_NO_PROGRESS = "repeated_no_progress"
    MAX_FAST_STREAK = "max_fast_streak"


class CadenceSignals(ContractModel):
    startup_uncertainty: bool = False
    structural_novelty: bool = False
    meaningful_contradiction: bool = False
    reopening: bool = False
    invalid_plan: bool = False
    high_goal_uncertainty: bool = False
    repeated_no_progress: bool = False
    fast_streak: NonNegativeInt = 0


class CadenceSelection(ContractModel):
    depth: ReasoningDepth
    triggers: tuple[DeepTrigger, ...]
    fast_streak_after: NonNegativeInt


class CadencePolicy(ContractModel):
    max_fast_streak: int = 12

    @model_validator(mode="after")
    def validate_policy(self) -> CadencePolicy:
        if self.max_fast_streak <= 0:
            raise ValueError("maximum fast streak must be positive")
        return self

    def select(self, signals: CadenceSignals) -> CadenceSelection:
        triggers: list[DeepTrigger] = []
        for field, trigger in (
            (signals.startup_uncertainty, DeepTrigger.STARTUP_UNCERTAINTY),
            (signals.structural_novelty, DeepTrigger.STRUCTURAL_NOVELTY),
            (signals.meaningful_contradiction, DeepTrigger.MEANINGFUL_CONTRADICTION),
            (signals.reopening, DeepTrigger.REOPENING),
            (signals.invalid_plan, DeepTrigger.INVALID_PLAN),
            (signals.high_goal_uncertainty, DeepTrigger.HIGH_GOAL_UNCERTAINTY),
            (signals.repeated_no_progress, DeepTrigger.REPEATED_NO_PROGRESS),
        ):
            if field:
                triggers.append(trigger)
        if signals.fast_streak >= self.max_fast_streak:
            triggers.append(DeepTrigger.MAX_FAST_STREAK)
        if triggers:
            return CadenceSelection(
                depth=ReasoningDepth.DEEP,
                triggers=tuple(triggers),
                fast_streak_after=0,
            )
        return CadenceSelection(
            depth=ReasoningDepth.FAST,
            triggers=(),
            fast_streak_after=signals.fast_streak + 1,
        )


def action_mode(
    *, credible_plan_supported: bool, uncertainty_blocks_progress: bool
) -> DeliberationMode:
    if credible_plan_supported and not uncertainty_blocks_progress:
        return DeliberationMode.EXECUTE
    return DeliberationMode.INVESTIGATE


def proposal_order_key(
    proposal: CandidateProposal, *, mode: DeliberationMode
) -> tuple[object, ...]:
    """Order separate declared ranks without pretending they form a probability."""

    if mode is DeliberationMode.EXECUTE:
        return (
            proposal.expected_progress_rank,
            proposal.risk_rank,
            proposal.costs.environment_actions,
            0 if proposal.reversible else 1,
            proposal.information_gain_rank,
            proposal.proposal_id,
        )
    return (
        proposal.information_gain_rank,
        0 if proposal.reversible else 1,
        proposal.risk_rank,
        proposal.costs.environment_actions,
        proposal.expected_progress_rank,
        proposal.proposal_id,
    )
