from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.contracts import CandidateProposal
from strongwiz.drivers import DriverRegistry
from strongwiz.ledger import SQLiteLedger
from strongwiz.policy import CadencePolicy
from strongwiz.routing import RouterPolicy
from strongwiz.runtime import (
    ReasoningSession,
    RuntimeError,
    SessionCheckpoint,
    SessionPhase,
    StrongwizKernel,
)
from tests.support import frozen_runtime, governing_goal, proposal, ref, request
from tests.test_runtime_arc import SyntheticDomain, prepare_execution


class CountingDriver:
    driver_id = "driver-test"
    driver_version = "driver-v1"
    driver_artifact_ref = ref("driver-artifact")

    def __init__(self, proposals: tuple[CandidateProposal, ...]) -> None:
        self._proposals = proposals
        self.calls = 0

    def propose(self, _request: object) -> Sequence[CandidateProposal]:
        self.calls += 1
        return self._proposals


def registered_kernel(driver: CountingDriver, domain: SyntheticDomain) -> StrongwizKernel:
    registry = DriverRegistry()
    registry.register_model(driver)
    registry.register_domain(domain)
    return StrongwizKernel(registry)


def active_session(
    driver: CountingDriver,
    domain: SyntheticDomain,
    *,
    ledger: SQLiteLedger | None = None,
    account_id: str | None = None,
    account_version: int = 0,
) -> ReasoningSession:
    return ReasoningSession(
        session_id="restore-session",
        model_driver=driver,
        domain_adapter=domain,
        governing_goal_ref=governing_goal().digest,
        frozen_runtime=frozen_runtime(),
        ledger=ledger,
        account_id=account_id,
        account_version=account_version,
    )


@pytest.mark.parametrize(
    "phase",
    [
        SessionPhase.NEEDS_SCAN,
        SessionPhase.READY_TO_ACT,
        SessionPhase.AWAITING_ASSESSMENT,
        SessionPhase.TERMINAL,
    ],
)
def test_checkpoint_round_trip_restores_every_phase_without_replaying_work(
    phase: SessionPhase,
) -> None:
    candidate = proposal()
    driver = CountingDriver((candidate,))
    domain = SyntheticDomain()
    active = active_session(driver, domain)
    prepared = prepare_execution(
        candidate,
        state="SUCCESS" if phase is SessionPhase.TERMINAL else "CONTINUE",
        fixture_id=f"roundtrip-{phase.value}",
    )
    if phase is not SessionPhase.NEEDS_SCAN:
        active.scan(request())
    if phase in {SessionPhase.AWAITING_ASSESSMENT, SessionPhase.TERMINAL}:
        decision = active.decide(prepared.control)
        if phase is SessionPhase.TERMINAL:
            active.assess(
                prepared.execute(decision.route),
                matched_prediction_items=("goal",),
                residual_refs=(),
                preserved_hypothesis_refs=("hyp-1",),
                revised_hypothesis_refs=(),
                concise_update_summary="domain reported terminal success",
            )

    snapshot = SessionCheckpoint.model_validate_json(
        active.checkpoint_snapshot().model_dump_json(by_alias=True)
    )
    calls_before_restore = driver.calls
    restored = registered_kernel(driver, domain).restore_session(
        checkpoint=snapshot,
        frozen_runtime=frozen_runtime(),
    )

    assert restored.checkpoint_snapshot() == snapshot
    assert restored.receipt() == snapshot.concise_receipt()
    assert snapshot.schema_id == "strongwiz.session-checkpoint.v1"
    assert snapshot.concise_receipt().schema_id == "strongwiz.session-receipt.v1"
    assert driver.calls == calls_before_restore


def test_restored_ready_and_pending_sessions_continue_at_exact_boundary() -> None:
    candidate = proposal()
    driver = CountingDriver((candidate,))
    domain = SyntheticDomain()
    kernel = registered_kernel(driver, domain)

    ready = active_session(driver, domain)
    ready.scan(request())
    ready_restored = kernel.restore_session(
        checkpoint=ready.checkpoint_snapshot(),
        frozen_runtime=frozen_runtime(),
    )
    prepared_ready = prepare_execution(candidate, fixture_id="continued-ready")
    decision = ready_restored.decide(prepared_ready.control)
    assert decision.selected_proposal_ref == candidate.digest
    assert ready_restored.phase is SessionPhase.AWAITING_ASSESSMENT

    awaiting = active_session(driver, domain)
    awaiting.scan(request())
    prepared_awaiting = prepare_execution(candidate, fixture_id="continued-awaiting")
    awaiting_decision = awaiting.decide(prepared_awaiting.control)
    calls_before_restore = driver.calls
    awaiting_restored = kernel.restore_session(
        checkpoint=awaiting.checkpoint_snapshot(),
        frozen_runtime=frozen_runtime(),
    )
    assessment = awaiting_restored.assess(
        prepared_awaiting.execute(awaiting_decision.route),
        matched_prediction_items=("visible",),
        residual_refs=(),
        preserved_hypothesis_refs=("hyp-1",),
        revised_hypothesis_refs=(),
        concise_update_summary="continued from the admitted occurrence",
    )
    assert assessment.phase_after is SessionPhase.NEEDS_SCAN
    assert driver.calls == calls_before_restore


def test_checkpoint_restores_failed_action_guard() -> None:
    candidate = proposal()
    driver = CountingDriver((candidate,))
    domain = SyntheticDomain()
    active = active_session(driver, domain)
    active.scan(request())
    failed = prepare_execution(candidate, state="FAILURE", fixture_id="failed-guard")
    decision = active.decide(failed.control)
    active.assess(
        failed.execute(decision.route),
        matched_prediction_items=(),
        residual_refs=(ref("failure-residual"),),
        preserved_hypothesis_refs=(),
        revised_hypothesis_refs=("hyp-1",),
        concise_update_summary="action failed under the retained belief state",
    )
    snapshot = active.checkpoint_snapshot()
    assert snapshot.last_failed_action_ref == candidate.action.digest

    restored = registered_kernel(driver, domain).restore_session(
        checkpoint=snapshot,
        frozen_runtime=frozen_runtime(),
    )
    restored.scan(request())
    with pytest.raises(RuntimeError, match="identical failed action"):
        restored.decide(failed.control)


def test_durable_checkpoint_restores_account_chain_and_rejects_stale_state(
    tmp_path: Path,
) -> None:
    candidate = proposal()
    driver = CountingDriver((candidate,))
    domain = SyntheticDomain()
    kernel = registered_kernel(driver, domain)
    with SQLiteLedger(tmp_path / "restore.sqlite3") as ledger:
        active = active_session(
            driver,
            domain,
            ledger=ledger,
            account_id="restored-account",
            account_version=7,
        )
        active.scan(request())
        durable_snapshot = active.checkpoint_snapshot()
        with pytest.raises(RuntimeError, match="requires its original ledger"):
            kernel.restore_session(
                checkpoint=durable_snapshot,
                frozen_runtime=frozen_runtime(),
            )
        checkpoint_ref = active.checkpoint()
        assert checkpoint_ref is not None

        restored = kernel.restore_session(
            checkpoint=checkpoint_ref,
            frozen_runtime=frozen_runtime(),
            ledger=ledger,
        )
        assert restored.receipt().ledger_receipt_refs[-1] == checkpoint_ref
        prepared = prepare_execution(candidate, fixture_id="durable-continuation")
        restored.decide(prepared.control)
        final_envelope = tuple(ledger.receipts())[-1]
        assert final_envelope.account_id == "restored-account"
        assert final_envelope.account_version == 7
        assert final_envelope.parent_refs == (checkpoint_ref,)

        with pytest.raises(RuntimeError, match="stale checkpoint"):
            kernel.restore_session(
                checkpoint=checkpoint_ref,
                frozen_runtime=frozen_runtime(),
                ledger=ledger,
            )


def test_checkpoint_validation_and_runtime_bindings_fail_closed() -> None:
    candidate = proposal()
    driver = CountingDriver((candidate,))
    domain = SyntheticDomain()
    active = active_session(driver, domain)
    active.scan(request())
    snapshot = active.checkpoint_snapshot()

    with pytest.raises(ValidationError, match="ready checkpoint"):
        snapshot.model_copy(update={"active_request": None})

    awaiting = active_session(driver, domain)
    awaiting.scan(request())
    prepared = prepare_execution(candidate, fixture_id="corrupt-pending")
    awaiting.decide(prepared.control)
    with pytest.raises(ValidationError, match="not admitted by the last decision"):
        awaiting.checkpoint_snapshot().model_copy(
            update={"pending_proposal": proposal(action="open")}
        )

    mismatched_runtime = frozen_runtime().model_copy(
        update={"configuration_ref": ref("other-config")}
    )
    with pytest.raises(RuntimeError, match="does not bind"):
        registered_kernel(driver, domain).restore_session(
            checkpoint=snapshot,
            frozen_runtime=mismatched_runtime,
        )

    with pytest.raises(RuntimeError, match="does not bind"):
        registered_kernel(driver, domain).restore_session(
            checkpoint=snapshot,
            frozen_runtime=frozen_runtime(),
            router_policy=RouterPolicy(request_missing_witness=False),
            cadence_policy=CadencePolicy(),
        )


def test_receipt_reference_requires_original_ledger() -> None:
    driver = CountingDriver((proposal(),))
    domain = SyntheticDomain()
    with pytest.raises(RuntimeError, match="requires a ledger"):
        registered_kernel(driver, domain).restore_session(
            checkpoint=ref("not-a-checkpoint-receipt"),
            frozen_runtime=frozen_runtime(),
        )
