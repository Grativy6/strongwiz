from __future__ import annotations

import pytest

from strongwiz.heartbeat import (
    BudgetBand,
    EventDrivenHeartbeat,
    HeartbeatError,
    HeartbeatLivenessEvidence,
    HeartbeatReason,
    HeartbeatState,
    RiskBand,
    SteeringAperture,
)
from tests.support import ref


def state(
    *,
    phase: str = "learning",
    checkpoint: str | None = None,
    gate: str = "map-access",
    budget: BudgetBand = BudgetBand.HEALTHY,
    budget_ref: str = "budget-1",
    steering: SteeringAperture = SteeringAperture.SAFE,
    risk: RiskBand = RiskBand.LOW,
    terminal: str | None = None,
) -> HeartbeatState:
    return HeartbeatState(
        run_id="run-1",
        phase=phase,
        latest_checkpoint_ref=None if checkpoint is None else ref(checkpoint),
        active_gate=gate,
        budget_band=budget,
        budget_snapshot_ref=ref(budget_ref),
        steering_aperture=steering,
        risk_band=risk,
        terminal_state=terminal,
    )


def test_heartbeat_is_event_driven_and_suppresses_unchanged_pings() -> None:
    heartbeat = EventDrivenHeartbeat()
    initial = heartbeat.observe(state())
    assert initial is not None
    assert initial.reasons == (HeartbeatReason.INITIAL,)
    assert initial.durable_boundary is not None

    # Exact budget evidence can advance inside the same meaningful band without
    # creating a social or timer-driven message.
    assert heartbeat.observe(state(budget_ref="budget-2")) is None

    changed = heartbeat.observe(
        state(
            phase="planning",
            checkpoint="checkpoint-1",
            gate="route-to-goal",
            budget_ref="budget-3",
        )
    )
    assert changed is not None
    assert changed.durable_boundary is not None
    assert set(changed.reasons) == {
        HeartbeatReason.PHASE_CHANGED,
        HeartbeatReason.CHECKPOINT_CHANGED,
        HeartbeatReason.ACTIVE_GATE_CHANGED,
    }
    assert changed.durable_boundary.predecessor_ref == initial.durable_boundary.digest


def test_silence_break_requires_fresh_observable_liveness() -> None:
    heartbeat = EventDrivenHeartbeat()
    heartbeat.observe(state())
    liveness = HeartbeatLivenessEvidence(
        run_id="run-1",
        progress_ordinal=12,
        evidence_ref=ref("process-progress-12"),
        concise_observation="verifier completed another immutable chunk",
    )
    emission = heartbeat.show_liveness(liveness)

    assert emission.durable_boundary is None
    assert emission.reasons == (HeartbeatReason.OBSERVED_LIVENESS,)
    assert emission.view.liveness_evidence_ref == liveness.evidence_ref
    with pytest.raises(HeartbeatError, match="fresh increasing"):
        heartbeat.show_liveness(liveness)


def test_steering_change_binds_displayed_view_and_external_authority() -> None:
    heartbeat = EventDrivenHeartbeat()
    initial = heartbeat.observe(state())
    assert initial is not None
    receipt = heartbeat.record_steering_change(
        supplied_authority_ref=ref("owner-authority"),
        instruction_ref=ref("steering-instruction"),
        prior_policy_ref=ref("old-policy"),
        resulting_policy_ref=ref("new-policy"),
        reversible=True,
        concise_effect="changed the next safe experiment",
    )

    assert receipt.displayed_view_ref == initial.view.digest
    assert receipt.authority == "SUPPLIED_EXTERNALLY"
    with pytest.raises(ValueError, match="unchanged policy"):
        heartbeat.record_steering_change(
            supplied_authority_ref=ref("owner-authority"),
            instruction_ref=ref("steering-instruction"),
            prior_policy_ref=ref("same-policy"),
            resulting_policy_ref=ref("same-policy"),
            reversible=True,
            concise_effect="no actual change",
        )


def test_heartbeat_restores_from_durable_boundary() -> None:
    heartbeat = EventDrivenHeartbeat()
    first = heartbeat.observe(state())
    assert first is not None and first.durable_boundary is not None
    second = heartbeat.observe(state(phase="planning"))
    assert second is not None and second.durable_boundary is not None

    restored = EventDrivenHeartbeat.restore(second.durable_boundary)
    third = restored.observe(state(phase="executing", risk=RiskBand.ELEVATED))
    assert third is not None and third.durable_boundary is not None
    assert third.durable_boundary.sequence == 2
    assert third.durable_boundary.predecessor_ref == second.durable_boundary.digest
