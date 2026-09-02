"""Event-driven steering heartbeat with durable boundaries and ephemeral views.

The heartbeat has no timer input.  It can emit because material state changed,
or because a caller supplies fresh observable liveness evidence.  Mere elapsed
time cannot manufacture a pulse.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class HeartbeatError(ValueError):
    """The steering projection failed closed."""


class BudgetBand(StrEnum):
    HEALTHY = "healthy"
    WATCH = "watch"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"


class SteeringAperture(StrEnum):
    SAFE = "safe"
    LIMITED = "limited"
    CLOSED = "closed"


class RiskBand(StrEnum):
    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"


class HeartbeatReason(StrEnum):
    INITIAL = "initial"
    PHASE_CHANGED = "phase_changed"
    CHECKPOINT_CHANGED = "checkpoint_changed"
    ACTIVE_GATE_CHANGED = "active_gate_changed"
    BUDGET_BAND_CHANGED = "budget_band_changed"
    STEERING_APERTURE_CHANGED = "steering_aperture_changed"
    RISK_BAND_CHANGED = "risk_band_changed"
    RESIDUAL_SET_CHANGED = "residual_set_changed"
    TERMINAL_STATE_CHANGED = "terminal_state_changed"
    OBSERVED_LIVENESS = "observed_liveness"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


class HeartbeatState(ContractModel):
    """Fields promised by the steering interface, not a second reasoning ledger."""

    schema_id: str = Field(default="strongwiz.heartbeat-state.v1", alias="schema")
    run_id: str
    phase: str
    latest_checkpoint_ref: str | None
    active_gate: str
    budget_band: BudgetBand
    budget_snapshot_ref: str
    steering_aperture: SteeringAperture
    risk_band: RiskBand
    residual_refs: tuple[str, ...] = ()
    terminal_state: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> HeartbeatState:
        if self.schema_id != "strongwiz.heartbeat-state.v1":
            raise ValueError("unsupported heartbeat state schema")
        if not all(value.strip() for value in (self.run_id, self.phase, self.active_gate)):
            raise ValueError("heartbeat run, phase, and active gate are required")
        _require_digest(self.budget_snapshot_ref, "budget snapshot reference")
        if self.latest_checkpoint_ref is not None:
            _require_digest(self.latest_checkpoint_ref, "checkpoint reference")
        if self.residual_refs != tuple(sorted(set(self.residual_refs))):
            raise ValueError("heartbeat residual references must be sorted and unique")
        for value in self.residual_refs:
            _require_digest(value, "heartbeat residual reference")
        if self.terminal_state is not None and not self.terminal_state.strip():
            raise ValueError("terminal state cannot be blank")
        return self


class HeartbeatBoundaryWitness(ContractModel):
    """Durable predecessor-linked witness for one material steering boundary."""

    schema_id: str = Field(default="strongwiz.heartbeat-boundary.v1", alias="schema")
    sequence: NonNegativeInt
    predecessor_ref: str | None
    state: HeartbeatState
    reasons: tuple[HeartbeatReason, ...]
    authority: str = "EVIDENCE_ONLY"
    effect: str = "NONE"

    @model_validator(mode="after")
    def validate_witness(self) -> HeartbeatBoundaryWitness:
        if self.schema_id != "strongwiz.heartbeat-boundary.v1":
            raise ValueError("unsupported heartbeat boundary schema")
        if self.sequence == 0:
            if self.predecessor_ref is not None or self.reasons != (HeartbeatReason.INITIAL,):
                raise ValueError("heartbeat genesis must be predecessor-free and initial")
        else:
            if self.predecessor_ref is None:
                raise ValueError("heartbeat successors require their exact predecessor")
            _require_digest(self.predecessor_ref, "heartbeat predecessor")
            if not self.reasons or HeartbeatReason.INITIAL in self.reasons:
                raise ValueError("heartbeat successors require non-initial change reasons")
        if tuple(sorted(set(self.reasons), key=str)) != self.reasons:
            raise ValueError("heartbeat reasons must be sorted and unique")
        return self


class HeartbeatLivenessEvidence(ContractModel):
    """Fresh observable progress required for a silence-breaking liveness view."""

    schema_id: str = Field(default="strongwiz.heartbeat-liveness.v1", alias="schema")
    run_id: str
    progress_ordinal: NonNegativeInt
    evidence_ref: str
    concise_observation: str

    @model_validator(mode="after")
    def validate_liveness(self) -> HeartbeatLivenessEvidence:
        if not self.run_id.strip() or not self.concise_observation.strip():
            raise ValueError("liveness evidence requires a run and observable summary")
        _require_digest(self.evidence_ref, "liveness evidence reference")
        return self


class HeartbeatView(ContractModel):
    """Lossy disposable rendering, complete only for its declared fields."""

    schema_id: str = Field(default="strongwiz.heartbeat-view.v1", alias="schema")
    run_id: str
    phase: str
    latest_checkpoint_ref: str | None
    active_gate: str
    budget_band: BudgetBand
    budget_snapshot_ref: str
    steering_aperture: SteeringAperture
    risk_band: RiskBand
    residual_count: NonNegativeInt
    terminal_state: str | None
    boundary_ref: str | None
    liveness_evidence_ref: str | None = None
    ephemeral: bool = True
    claim_ceiling: str = "lossy steering projection only"
    authority: str = "NONE"
    effect: str = "NONE"


class HeartbeatEmission(ContractModel):
    view: HeartbeatView
    durable_boundary: HeartbeatBoundaryWitness | None
    reasons: tuple[HeartbeatReason, ...]

    @model_validator(mode="after")
    def validate_emission(self) -> HeartbeatEmission:
        if not self.reasons:
            raise ValueError("heartbeat emission requires an informational cause")
        if self.durable_boundary is None:
            if self.reasons != (HeartbeatReason.OBSERVED_LIVENESS,):
                raise ValueError("only fresh liveness may emit without a durable boundary")
            if self.view.liveness_evidence_ref is None:
                raise ValueError("ephemeral liveness view requires exact evidence")
        elif (
            self.durable_boundary.reasons != self.reasons
            or self.view.boundary_ref != self.durable_boundary.digest
        ):
            raise ValueError("heartbeat view does not bind its durable boundary")
        return self


class SteeringChangeReceipt(ContractModel):
    """A consequential steering crossing, not authority manufactured by the view."""

    schema_id: str = Field(default="strongwiz.steering-change.v1", alias="schema")
    run_id: str
    displayed_view_ref: str
    supplied_authority_ref: str
    instruction_ref: str
    prior_policy_ref: str
    resulting_policy_ref: str
    reversible: bool
    concise_effect: str
    authority: str = "SUPPLIED_EXTERNALLY"

    @model_validator(mode="after")
    def validate_change(self) -> SteeringChangeReceipt:
        if not self.run_id.strip() or not self.concise_effect.strip():
            raise ValueError("steering change requires run identity and concise effect")
        for value in (
            self.displayed_view_ref,
            self.supplied_authority_ref,
            self.instruction_ref,
            self.prior_policy_ref,
            self.resulting_policy_ref,
        ):
            _require_digest(value, "steering change binding")
        if self.prior_policy_ref == self.resulting_policy_ref:
            raise ValueError("unchanged policy is not a consequential steering change")
        return self


def _material_reasons(
    before: HeartbeatState, after: HeartbeatState
) -> tuple[HeartbeatReason, ...]:
    if before.run_id != after.run_id:
        raise HeartbeatError("heartbeat state cannot cross run identity")
    reasons: list[HeartbeatReason] = []
    comparisons = (
        (before.phase != after.phase, HeartbeatReason.PHASE_CHANGED),
        (
            before.latest_checkpoint_ref != after.latest_checkpoint_ref,
            HeartbeatReason.CHECKPOINT_CHANGED,
        ),
        (before.active_gate != after.active_gate, HeartbeatReason.ACTIVE_GATE_CHANGED),
        (
            before.budget_band is not after.budget_band,
            HeartbeatReason.BUDGET_BAND_CHANGED,
        ),
        (
            before.steering_aperture is not after.steering_aperture,
            HeartbeatReason.STEERING_APERTURE_CHANGED,
        ),
        (before.risk_band is not after.risk_band, HeartbeatReason.RISK_BAND_CHANGED),
        (
            before.residual_refs != after.residual_refs,
            HeartbeatReason.RESIDUAL_SET_CHANGED,
        ),
        (
            before.terminal_state != after.terminal_state,
            HeartbeatReason.TERMINAL_STATE_CHANGED,
        ),
    )
    reasons.extend(reason for changed, reason in comparisons if changed)
    return tuple(sorted(reasons, key=str))


class EventDrivenHeartbeat:
    """Emit only on a material transition or fresh observable liveness."""

    def __init__(self) -> None:
        self._state: HeartbeatState | None = None
        self._boundary: HeartbeatBoundaryWitness | None = None
        self._last_view: HeartbeatView | None = None
        self._last_liveness_ordinal: int | None = None

    @classmethod
    def restore(cls, boundary: HeartbeatBoundaryWitness) -> EventDrivenHeartbeat:
        heartbeat = cls()
        heartbeat._state = boundary.state
        heartbeat._boundary = boundary
        heartbeat._last_view = heartbeat._render(
            boundary.state, boundary_ref=boundary.digest, liveness_ref=None
        )
        return heartbeat

    @staticmethod
    def _render(
        state: HeartbeatState,
        *,
        boundary_ref: str | None,
        liveness_ref: str | None,
    ) -> HeartbeatView:
        return HeartbeatView(
            run_id=state.run_id,
            phase=state.phase,
            latest_checkpoint_ref=state.latest_checkpoint_ref,
            active_gate=state.active_gate,
            budget_band=state.budget_band,
            budget_snapshot_ref=state.budget_snapshot_ref,
            steering_aperture=state.steering_aperture,
            risk_band=state.risk_band,
            residual_count=len(state.residual_refs),
            terminal_state=state.terminal_state,
            boundary_ref=boundary_ref,
            liveness_evidence_ref=liveness_ref,
        )

    def observe(self, state: HeartbeatState) -> HeartbeatEmission | None:
        reasons: tuple[HeartbeatReason, ...]
        if self._state is None:
            reasons = (HeartbeatReason.INITIAL,)
            boundary = HeartbeatBoundaryWitness(
                sequence=0,
                predecessor_ref=None,
                state=state,
                reasons=reasons,
            )
        else:
            reasons = _material_reasons(self._state, state)
            if not reasons:
                # The latest exact budget object may change inside one band.  Retain it
                # for a later meaningful rendering, but do not manufacture a pulse.
                self._state = state
                return None
            predecessor = self._boundary
            if predecessor is None:  # pragma: no cover - state invariant
                raise HeartbeatError("heartbeat lost its predecessor boundary")
            boundary = HeartbeatBoundaryWitness(
                sequence=predecessor.sequence + 1,
                predecessor_ref=predecessor.digest,
                state=state,
                reasons=reasons,
            )
        view = self._render(state, boundary_ref=boundary.digest, liveness_ref=None)
        self._state = state
        self._boundary = boundary
        self._last_view = view
        return HeartbeatEmission(view=view, durable_boundary=boundary, reasons=reasons)

    def show_liveness(self, evidence: HeartbeatLivenessEvidence) -> HeartbeatEmission:
        state = self._state
        if state is None:
            raise HeartbeatError("liveness cannot precede heartbeat genesis")
        if evidence.run_id != state.run_id:
            raise HeartbeatError("liveness evidence belongs to another run")
        if (
            self._last_liveness_ordinal is not None
            and evidence.progress_ordinal <= self._last_liveness_ordinal
        ):
            raise HeartbeatError("liveness updates require a fresh increasing progress marker")
        boundary_ref = None if self._boundary is None else self._boundary.digest
        view = self._render(
            state,
            boundary_ref=boundary_ref,
            liveness_ref=evidence.evidence_ref,
        )
        self._last_liveness_ordinal = evidence.progress_ordinal
        self._last_view = view
        return HeartbeatEmission(
            view=view,
            durable_boundary=None,
            reasons=(HeartbeatReason.OBSERVED_LIVENESS,),
        )

    def record_steering_change(
        self,
        *,
        supplied_authority_ref: str,
        instruction_ref: str,
        prior_policy_ref: str,
        resulting_policy_ref: str,
        reversible: bool,
        concise_effect: str,
    ) -> SteeringChangeReceipt:
        if self._last_view is None:
            raise HeartbeatError("steering cannot bind an undisplayed heartbeat state")
        return SteeringChangeReceipt(
            run_id=self._last_view.run_id,
            displayed_view_ref=self._last_view.digest,
            supplied_authority_ref=supplied_authority_ref,
            instruction_ref=instruction_ref,
            prior_policy_ref=prior_policy_ref,
            resulting_policy_ref=resulting_policy_ref,
            reversible=reversible,
            concise_effect=concise_effect,
        )
