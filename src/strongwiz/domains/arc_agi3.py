"""ARC-AGI-3 completion semantics without an SDK, policy, or action script."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt
from strongwiz.drivers import TerminalAuthority


class ArcGameState(StrEnum):
    NOT_FINISHED = "NOT_FINISHED"
    WIN = "WIN"
    GAME_OVER = "GAME_OVER"


def terminal_authority(state: ArcGameState) -> TerminalAuthority:
    if state is ArcGameState.WIN:
        return TerminalAuthority.SUCCESS
    if state is ArcGameState.GAME_OVER:
        return TerminalAuthority.FAILURE
    return TerminalAuthority.CONTINUE


class ArcRunReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.arc-agi3-run-receipt.v1", alias="schema")
    game_id: str
    environment_class: str
    final_environment_state: ArcGameState
    levels_completed: NonNegativeInt
    win_levels: tuple[int, ...]
    environment_action_count: NonNegativeInt
    reset_count: NonNegativeInt
    frozen_runtime_ref: str
    replay_evidence_ref: str
    completion_genuinely_observed: bool
    claim_class: str

    @model_validator(mode="after")
    def validate_completion(self) -> ArcRunReceipt:
        required = (
            self.game_id,
            self.environment_class,
            self.frozen_runtime_ref,
            self.replay_evidence_ref,
            self.claim_class,
        )
        if not all(value.strip() for value in required):
            raise ValueError("ARC receipt identity and evidence bindings are required")
        observed = self.final_environment_state is ArcGameState.WIN
        if self.completion_genuinely_observed != observed:
            raise ValueError("only the environment WIN state earns observed completion")
        if self.levels_completed < len(self.win_levels):
            raise ValueError("win-level count exceeds completed-level count")
        return self


def legal_actions_after(state: ArcGameState, actions: tuple[str, ...]) -> tuple[str, ...]:
    """Keep reset-only failure semantics explicit without implementing a game client."""

    if state is ArcGameState.GAME_OVER:
        return tuple(action for action in actions if action.upper() == "RESET")
    return actions
