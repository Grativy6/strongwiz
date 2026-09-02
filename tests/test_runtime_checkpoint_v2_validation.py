from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.contracts import CandidateProposal
from strongwiz.drivers import DriverRegistry, TerminalAuthority
from strongwiz.integrity import FrozenRuntimeManifest
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger
from strongwiz.runtime import (
    ReasoningSession,
    RuntimeError,
    SessionCheckpointV2,
    SessionPhase,
    StrongwizKernel,
)
from tests.support import frozen_runtime, governing_goal, proposal, ref, request
from tests.test_runtime_arc import SyntheticDomain, prepare_execution


class StaticDriver:
    driver_id = "driver-test"
    driver_version = "driver-v1"
    driver_artifact_ref = ref("driver-artifact")

    def __init__(self, proposals: tuple[CandidateProposal, ...]) -> None:
        self._proposals = proposals

    def propose(self, _request: object) -> Sequence[CandidateProposal]:
        return self._proposals


def _runtime_components(
    *, ledger: SQLiteLedger | None = None
) -> tuple[StrongwizKernel, ReasoningSession, FrozenRuntimeManifest]:
    candidate = proposal()
    driver = StaticDriver((candidate,))
    domain = SyntheticDomain()
    registry = DriverRegistry()
    registry.register_model(driver)
    registry.register_domain(domain)
    runtime = frozen_runtime()
    session = ReasoningSession(
        session_id="v2-validation-session",
        model_driver=driver,
        domain_adapter=domain,
        governing_goal_ref=governing_goal().digest,
        frozen_runtime=runtime,
        ledger=ledger,
        account_id="v2-validation-account",
        account_version=3,
    )
    return StrongwizKernel(registry), session, runtime


def _checkpoint_for_phase(phase: SessionPhase) -> SessionCheckpointV2:
    _, session, _ = _runtime_components()
    candidate = proposal()
    if phase is not SessionPhase.NEEDS_SCAN:
        session.scan(request())
    if phase in {SessionPhase.AWAITING_ASSESSMENT, SessionPhase.TERMINAL}:
        prepared = prepare_execution(
            candidate,
            state="SUCCESS" if phase is SessionPhase.TERMINAL else "CONTINUE",
            fixture_id=f"v2-validation-{phase.value}",
        )
        decision = session.decide(prepared.control)
        if phase is SessionPhase.TERMINAL:
            session.assess(
                prepared.execute(decision.route),
                matched_prediction_items=("goal",),
                residual_refs=(),
                preserved_hypothesis_refs=("hyp-1",),
                revised_hypothesis_refs=(),
                concise_update_summary="terminal success was observed",
            )
    return session.reference_checkpoint_snapshot()


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"schema_id": "strongwiz.session-checkpoint.v3"}, "unsupported reference"),
        ({"session_id": " "}, "identities are required"),
        ({"frozen_runtime_ref": "A" * 64}, "lowercase SHA-256"),
        ({"history_receipt_head": ref("unrecorded-tail")}, "count and tail disagree"),
        ({"last_failed_action_ref": ref("failed-action")}, "guard is incomplete"),
        ({"assessment_count": 1}, "assessments exceed admitted actions"),
    ],
)
def test_reference_checkpoint_rejects_invalid_global_invariants(
    update: dict[str, object], message: str
) -> None:
    checkpoint = _checkpoint_for_phase(SessionPhase.NEEDS_SCAN)

    with pytest.raises(ValidationError, match=message):
        checkpoint.model_copy(update=update)


@pytest.mark.parametrize(
    ("phase", "update", "message"),
    [
        (
            SessionPhase.NEEDS_SCAN,
            {"active_request_ref": ref("unexpected-request")},
            "retains actionable state",
        ),
        (
            SessionPhase.NEEDS_SCAN,
            {"terminal_authority": TerminalAuthority.BLOCKED},
            "claims terminal authority",
        ),
        (
            SessionPhase.READY_TO_ACT,
            {"pending_proposal_ref": ref("unexpected-proposal")},
            "requires only an active request",
        ),
        (
            SessionPhase.READY_TO_ACT,
            {"terminal_authority": TerminalAuthority.BLOCKED},
            "claims terminal authority",
        ),
        (
            SessionPhase.AWAITING_ASSESSMENT,
            {"pending_proposal_ref": None},
            "requires request and proposal",
        ),
        (
            SessionPhase.AWAITING_ASSESSMENT,
            {"terminal_authority": TerminalAuthority.BLOCKED},
            "claims terminal authority",
        ),
        (
            SessionPhase.TERMINAL,
            {"active_request_ref": ref("unexpected-request")},
            "retains actionable state",
        ),
        (
            SessionPhase.TERMINAL,
            {
                "terminal_authority": None,
                "completion_genuinely_observed": False,
            },
            "requires final domain authority",
        ),
        (
            SessionPhase.NEEDS_SCAN,
            {"completion_genuinely_observed": True},
            "completion claim disagrees",
        ),
    ],
)
def test_reference_checkpoint_phase_state_fails_closed(
    phase: SessionPhase, update: dict[str, object], message: str
) -> None:
    checkpoint = _checkpoint_for_phase(phase)

    with pytest.raises(ValidationError, match=message):
        checkpoint.model_copy(update=update)


def test_blocked_terminal_reference_checkpoint_does_not_claim_completion() -> None:
    checkpoint = _checkpoint_for_phase(SessionPhase.TERMINAL).model_copy(
        update={
            "terminal_authority": TerminalAuthority.BLOCKED,
            "completion_genuinely_observed": False,
        }
    )

    assert checkpoint.terminal_authority is TerminalAuthority.BLOCKED
    assert not checkpoint.completion_genuinely_observed


def _store_runtime(ledger: SQLiteLedger) -> FrozenRuntimeManifest:
    runtime = frozen_runtime()
    stored_ref = ledger.put_object(runtime.model_dump(mode="json", by_alias=True))
    assert stored_ref == runtime.manifest_ref
    return runtime


def _append_checkpoint(
    ledger: SQLiteLedger,
    checkpoint: SessionCheckpointV2,
    *,
    object_refs: tuple[str, ...] | None = None,
    parent_refs: tuple[str, ...] = (),
    account_id: str | None = None,
    occurrence_id: str | None = None,
) -> ReceiptEnvelope:
    if object_refs is None:
        object_refs = (checkpoint.frozen_runtime_ref,)
    kind = "v2-checkpoint"
    return ledger.append(
        occurrence_id=occurrence_id
        or f"{checkpoint.session_id}:{checkpoint.history_receipt_count:08d}:{kind}",
        kind=kind,
        account_id=account_id or checkpoint.account_id,
        account_version=checkpoint.account_version,
        payload=checkpoint.model_dump(mode="json", by_alias=True),
        object_refs=object_refs,
        parent_refs=parent_refs,
    )


def _append_prior(
    ledger: SQLiteLedger,
    checkpoint: SessionCheckpointV2,
    *,
    kind: str = "note",
    payload: dict[str, object] | None = None,
    parent_refs: tuple[str, ...] = (),
    account_id: str | None = None,
) -> ReceiptEnvelope:
    index = len(tuple(ledger.receipts()))
    return ledger.append(
        occurrence_id=f"{checkpoint.session_id}:prior:{index:08d}:{kind}",
        kind=kind,
        account_id=account_id or checkpoint.account_id,
        account_version=checkpoint.account_version,
        payload=payload or {"session_id": checkpoint.session_id, "index": index},
        parent_refs=parent_refs,
    )


def test_direct_reference_checkpoint_restores_only_with_its_ledger(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "direct-v2.sqlite3") as ledger:
        kernel, session, runtime = _runtime_components(ledger=ledger)
        session.scan(request())
        checkpoint_ref = session.checkpoint()
        assert checkpoint_ref is not None
        envelope = tuple(ledger.receipts())[-1]
        checkpoint = SessionCheckpointV2.model_validate(
            ledger.get_payload(envelope.payload_hash)
        )

        with pytest.raises(RuntimeError, match="requires its original ledger"):
            kernel.restore_session(checkpoint=checkpoint, frozen_runtime=runtime)

        restored = kernel.restore_session(
            checkpoint=checkpoint,
            frozen_runtime=runtime,
            ledger=ledger,
        )

        assert restored.receipt() == session.receipt()


def test_direct_reference_checkpoint_must_exist_in_supplied_ledger(tmp_path: Path) -> None:
    kernel, session, runtime = _runtime_components()
    checkpoint = session.reference_checkpoint_snapshot()

    with (
        SQLiteLedger(tmp_path / "unrelated.sqlite3") as unrelated_ledger,
        pytest.raises(RuntimeError, match="reference checkpoint is absent"),
    ):
        kernel.restore_session(
            checkpoint=checkpoint,
            frozen_runtime=runtime,
            ledger=unrelated_ledger,
        )


def test_checkpoint_receipt_lookup_rejects_absent_and_non_checkpoint_payloads(
    tmp_path: Path,
) -> None:
    with SQLiteLedger(tmp_path / "lookup.sqlite3") as ledger:
        kernel, _, runtime = _runtime_components()
        with pytest.raises(RuntimeError, match="absent or ambiguous"):
            kernel.restore_session(
                checkpoint=ref("absent-checkpoint"),
                frozen_runtime=runtime,
                ledger=ledger,
            )

        envelope = ledger.append(
            occurrence_id="not-a-checkpoint",
            kind="note",
            account_id="v2-validation-account",
            account_version=3,
            payload={"schema": "strongwiz.session-checkpoint.v2"},
        )
        with pytest.raises(RuntimeError, match="valid session checkpoint"):
            kernel.restore_session(
                checkpoint=envelope.receipt_id,
                frozen_runtime=runtime,
                ledger=ledger,
            )

        legacy_dispatch = ledger.append(
            occurrence_id="not-a-v1-checkpoint",
            kind="note",
            account_id="v2-validation-account",
            account_version=3,
            payload={"schema": "unknown-checkpoint-schema"},
        )
        with pytest.raises(RuntimeError, match="valid session checkpoint"):
            kernel.restore_session(
                checkpoint=legacy_dispatch.receipt_id,
                frozen_runtime=runtime,
                ledger=ledger,
            )


def test_reference_checkpoint_rejects_receipt_content_and_account_mismatches(
    tmp_path: Path,
) -> None:
    with SQLiteLedger(tmp_path / "binding.sqlite3") as ledger:
        _store_runtime(ledger)
        kernel, session, runtime = _runtime_components()
        checkpoint = session.reference_checkpoint_snapshot()
        envelope = _append_checkpoint(
            ledger,
            checkpoint,
            account_id="different-account",
        )

        with pytest.raises(RuntimeError, match="ledger account binding disagrees"):
            kernel.restore_session(
                checkpoint=checkpoint,
                frozen_runtime=runtime,
                ledger=ledger,
            )

        changed = checkpoint.model_copy(update={"account_id": "changed-checkpoint-account"})
        with pytest.raises(RuntimeError, match="binds different content"):
            ReasoningSession.restore(
                changed,
                model_driver=kernel.registry.model(checkpoint.driver_id),
                domain_adapter=kernel.registry.domain(checkpoint.domain_adapter_id),
                frozen_runtime=runtime,
                ledger=ledger,
                checkpoint_receipt_ref=envelope.receipt_id,
            )


@pytest.mark.parametrize(
    "failure",
    [
        "occurrence",
        "history-count",
        "history-tail",
        "checkpoint-parent",
        "predecessor-lineage",
        "history-account",
        "restart-objects",
        "typed-counts",
    ],
)
def test_reference_checkpoint_rejects_incoherent_durable_lineage(
    tmp_path: Path, failure: str
) -> None:
    with SQLiteLedger(tmp_path / f"{failure}.sqlite3") as ledger:
        _store_runtime(ledger)
        kernel, session, runtime = _runtime_components()
        checkpoint = session.reference_checkpoint_snapshot()
        parent_refs: tuple[str, ...] = ()
        object_refs: tuple[str, ...] = (checkpoint.frozen_runtime_ref,)
        occurrence_id: str | None = None
        message = ""

        if failure == "occurrence":
            occurrence_id = "wrong-boundary"
            message = "occurrence is not the session boundary"
        elif failure == "history-count":
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 1,
                    "history_receipt_head": ref("unrecorded-tail"),
                }
            )
            message = "history count disagrees"
        elif failure == "history-tail":
            prior = _append_prior(ledger, checkpoint)
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 1,
                    "history_receipt_head": ref("wrong-tail"),
                }
            )
            parent_refs = (prior.receipt_id,)
            message = "history tail disagrees"
        elif failure == "checkpoint-parent":
            prior = _append_prior(ledger, checkpoint)
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 1,
                    "history_receipt_head": prior.receipt_id,
                }
            )
            message = "does not bind its exact history tail"
        elif failure == "predecessor-lineage":
            first = _append_prior(ledger, checkpoint)
            second = _append_prior(ledger, checkpoint)
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 2,
                    "history_receipt_head": second.receipt_id,
                }
            )
            parent_refs = (second.receipt_id,)
            message = "predecessor lineage is broken"
            assert first.receipt_id != second.receipt_id
        elif failure == "history-account":
            prior = _append_prior(
                ledger,
                checkpoint,
                account_id="different-history-account",
            )
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 1,
                    "history_receipt_head": prior.receipt_id,
                }
            )
            parent_refs = (prior.receipt_id,)
            message = "history crosses ledger account identity"
        elif failure == "restart-objects":
            object_refs = ()
            message = "omits restart-critical objects"
        else:
            prior = _append_prior(ledger, checkpoint)
            checkpoint = checkpoint.model_copy(
                update={
                    "history_receipt_count": 1,
                    "history_receipt_head": prior.receipt_id,
                    "scan_count": 1,
                }
            )
            parent_refs = (prior.receipt_id,)
            message = "typed history counts disagree"

        _append_checkpoint(
            ledger,
            checkpoint,
            object_refs=object_refs,
            parent_refs=parent_refs,
            occurrence_id=occurrence_id,
        )

        with pytest.raises(RuntimeError, match=message):
            kernel.restore_session(
                checkpoint=checkpoint,
                frozen_runtime=runtime,
                ledger=ledger,
            )


def test_reference_checkpoint_rejects_invalid_typed_history_record(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "invalid-history.sqlite3") as ledger:
        _store_runtime(ledger)
        kernel, session, runtime = _runtime_components()
        checkpoint = session.reference_checkpoint_snapshot()
        prior = _append_prior(
            ledger,
            checkpoint,
            kind="scan",
            payload={"session_id": checkpoint.session_id},
        )
        checkpoint = checkpoint.model_copy(
            update={
                "history_receipt_count": 1,
                "history_receipt_head": prior.receipt_id,
                "scan_count": 1,
            }
        )
        _append_checkpoint(ledger, checkpoint, parent_refs=(prior.receipt_id,))

        with pytest.raises(RuntimeError, match="history contains an invalid record"):
            kernel.restore_session(
                checkpoint=checkpoint,
                frozen_runtime=runtime,
                ledger=ledger,
            )


@pytest.mark.parametrize("invalid_object", ["active-request", "pending-proposal"])
def test_reference_checkpoint_rejects_invalid_restart_objects(
    tmp_path: Path, invalid_object: str
) -> None:
    with SQLiteLedger(tmp_path / f"{invalid_object}.sqlite3") as ledger:
        _store_runtime(ledger)
        kernel, session, runtime = _runtime_components()
        checkpoint = session.reference_checkpoint_snapshot()
        invalid_ref = ledger.put_object({"not": invalid_object})

        if invalid_object == "active-request":
            checkpoint = checkpoint.model_copy(
                update={
                    "phase": SessionPhase.READY_TO_ACT,
                    "active_request_ref": invalid_ref,
                }
            )
            object_refs = (checkpoint.frozen_runtime_ref, invalid_ref)
            message = "active request is invalid"
        else:
            active_request = request()
            active_request_ref = ledger.put_object(
                active_request.model_dump(mode="json", by_alias=True)
            )
            checkpoint = checkpoint.model_copy(
                update={
                    "phase": SessionPhase.AWAITING_ASSESSMENT,
                    "active_request_ref": active_request_ref,
                    "pending_proposal_ref": invalid_ref,
                    "admitted_action_count": 1,
                }
            )
            object_refs = (
                checkpoint.frozen_runtime_ref,
                active_request_ref,
                invalid_ref,
            )
            message = "pending proposal is invalid"

        _append_checkpoint(ledger, checkpoint, object_refs=object_refs)

        with pytest.raises(RuntimeError, match=message):
            kernel.restore_session(
                checkpoint=checkpoint,
                frozen_runtime=runtime,
                ledger=ledger,
            )
