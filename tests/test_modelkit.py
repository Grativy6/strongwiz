from __future__ import annotations

import io
from typing import cast

import pytest

from strongwiz.contracts import ActionSpec, CandidateProposal, CostVector
from strongwiz.drivers import ModelDriver
from strongwiz.modelkit import (
    CallableModelDriver,
    FramedModelDriver,
    FramedModelRestartState,
    ProposalDraft,
    framed_request_identity,
)
from strongwiz.transport import (
    FrameTimeoutError,
    IdentityReuseError,
    decode_frame,
    encode_frame,
)

from .support import distinction, prediction, ref, request


def draft(*, proposal_id: str = "draft-1") -> ProposalDraft:
    return ProposalDraft(
        proposal_id=proposal_id,
        action=ActionSpec(name="inspect"),
        meaningful_distinction=distinction(),
        prediction=prediction(),
        decision_effects=distinction().decision_effects,
        evidence_refs=(ref("evidence-1"),),
        concise_rationale="inspect the smallest decision-relevant difference",
        reversible=True,
        expected_progress_rank=1,
        information_gain_rank=1,
        risk_rank=0,
        costs=CostVector(environment_actions=1),
    )


def driver(function: object) -> CallableModelDriver:
    return CallableModelDriver(
        driver_id="driver-test",
        driver_version="driver-v1",
        driver_artifact_ref=ref("driver-artifact"),
        proposal_function=cast("object", function),  # type: ignore[arg-type]
    )


def restart_state(*, starting_sequence: int = 0) -> FramedModelRestartState:
    return FramedModelRestartState.initial(
        session_id="framed-session-1",
        driver_id="driver-test",
        driver_version="driver-v1",
        driver_artifact_ref=ref("driver-artifact"),
        starting_sequence=starting_sequence,
    )


class CrashReader:
    def read(self, size: int = -1, /) -> bytes:
        del size
        raise TimeoutError("simulated model process loss")


def test_callable_model_driver_satisfies_protocol_and_binds_request() -> None:
    model = driver(lambda _request: (draft(),))
    assert isinstance(model, ModelDriver)

    proposals = model.propose(request())

    assert len(proposals) == 1
    proposal = proposals[0]
    assert isinstance(proposal, CandidateProposal)
    assert proposal.model_driver_id == model.driver_id
    assert proposal.observation_id == request().observation.observation_id
    assert proposal.observation_ref == request().observation.digest
    assert proposal.goal_id == request().scoped_goal.goal_id
    assert proposal.goal_ref == request().scoped_goal.digest


def test_callable_model_driver_rejects_duplicate_ids() -> None:
    model = driver(lambda _request: (draft(), draft()))
    with pytest.raises(ValueError, match="duplicate"):
        model.propose(request())


@pytest.mark.parametrize("bad", ["not a sequence", [object()]])
def test_callable_model_driver_rejects_invalid_callback_results(bad: object) -> None:
    model = driver(lambda _request: bad)
    with pytest.raises(TypeError, match=r"sequence|non-ProposalDraft"):
        model.propose(request())


def test_callable_model_driver_rejects_empty_identity() -> None:
    with pytest.raises(ValueError, match="driver ID"):
        CallableModelDriver("", "v1", ref("artifact"), lambda _request: ())


def test_framed_model_driver_round_trips_without_a_tty_or_newlines() -> None:
    state = restart_state()
    message_id = framed_request_identity(
        "driver-test",
        request(),
        0,
        session_id=state.session_id,
    )
    response = encode_frame(
        {
            "driver_id": "driver-test",
            "in_reply_to": message_id,
            "kind": "strongwiz.model-response.v1",
            "message_id": "response-1",
            "proposal_drafts": [draft().model_dump(mode="json", by_alias=True)],
        }
    )
    written = io.BytesIO()
    model = FramedModelDriver(
        driver_id="driver-test",
        driver_version="driver-v1",
        driver_artifact_ref=ref("driver-artifact"),
        reader=io.BytesIO(response),
        writer=written,
        restart_state=state,
    )

    proposals = model.propose(request())

    assert proposals[0].observation_ref == request().observation.digest
    outbound = decode_frame(written.getvalue())
    assert isinstance(outbound, dict)
    assert outbound["kind"] == "strongwiz.model-request.v1"
    assert outbound["message_id"] == message_id
    assert outbound["session_id"] == state.session_id
    assert model.restart_state.next_sequence == 1
    assert model.restart_state.accepted_responses[0].message_id == "response-1"


def test_framed_model_driver_rejects_foreign_reply() -> None:
    response = encode_frame(
        {
            "driver_id": "driver-test",
            "in_reply_to": "foreign-request",
            "kind": "strongwiz.model-response.v1",
            "message_id": "response-1",
            "proposal_drafts": [],
        }
    )
    model = FramedModelDriver(
        "driver-test",
        "driver-v1",
        ref("driver-artifact"),
        io.BytesIO(response),
        io.BytesIO(),
        restart_state(),
    )
    with pytest.raises(ValueError, match="current request"):
        model.propose(request())


def test_fresh_driver_after_failed_exchange_uses_reserved_next_sequence() -> None:
    state_updates: list[FramedModelRestartState] = []
    crashed_writer = io.BytesIO()
    crashed = FramedModelDriver(
        "driver-test",
        "driver-v1",
        ref("driver-artifact"),
        CrashReader(),
        crashed_writer,
        restart_state(),
        state_sink=state_updates.append,
    )
    with pytest.raises(FrameTimeoutError):
        crashed.propose(request())

    reserved_state = FramedModelRestartState.model_validate_json(
        crashed.restart_state.model_dump_json(by_alias=True)
    )
    assert reserved_state.next_sequence == 1
    assert state_updates[-1] == reserved_state
    first_outbound = decode_frame(crashed_writer.getvalue())
    assert isinstance(first_outbound, dict)

    next_message_id = framed_request_identity(
        "driver-test",
        request(),
        1,
        session_id=reserved_state.session_id,
    )
    response = encode_frame(
        {
            "driver_id": "driver-test",
            "in_reply_to": next_message_id,
            "kind": "strongwiz.model-response.v1",
            "message_id": "response-after-reconstruction",
            "proposal_drafts": [draft().model_dump(mode="json", by_alias=True)],
        }
    )
    fresh_writer = io.BytesIO()
    fresh = FramedModelDriver(
        "driver-test",
        "driver-v1",
        ref("driver-artifact"),
        io.BytesIO(response),
        fresh_writer,
        reserved_state,
        state_sink=state_updates.append,
    )

    assert fresh.propose(request())
    second_outbound = decode_frame(fresh_writer.getvalue())
    assert isinstance(second_outbound, dict)
    assert first_outbound["message_id"] != second_outbound["message_id"]
    assert second_outbound["message_id"] == next_message_id
    assert fresh.restart_state.next_sequence == 2


def test_fresh_driver_retains_accepted_response_identity_window() -> None:
    state = restart_state()
    first_request_id = framed_request_identity(
        "driver-test", request(), 0, session_id=state.session_id
    )
    first_response = encode_frame(
        {
            "driver_id": "driver-test",
            "in_reply_to": first_request_id,
            "kind": "strongwiz.model-response.v1",
            "message_id": "durable-response-id",
            "proposal_drafts": [],
        }
    )
    first = FramedModelDriver(
        "driver-test",
        "driver-v1",
        ref("driver-artifact"),
        io.BytesIO(first_response),
        io.BytesIO(),
        state,
    )
    first.propose(request())

    retained = FramedModelRestartState.model_validate_json(
        first.restart_state.model_dump_json(by_alias=True)
    )
    next_request_id = framed_request_identity(
        "driver-test", request(), 1, session_id=retained.session_id
    )
    replayed_identity = encode_frame(
        {
            "driver_id": "driver-test",
            "in_reply_to": next_request_id,
            "kind": "strongwiz.model-response.v1",
            "message_id": "durable-response-id",
            "proposal_drafts": [draft().model_dump(mode="json", by_alias=True)],
        }
    )
    fresh = FramedModelDriver(
        "driver-test",
        "driver-v1",
        ref("driver-artifact"),
        io.BytesIO(replayed_identity),
        io.BytesIO(),
        retained,
    )

    with pytest.raises(IdentityReuseError):
        fresh.propose(request())


def test_framed_restart_state_is_explicit_validated_and_driver_bound() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        restart_state(starting_sequence=-1)
    with pytest.raises(ValueError, match="different driver"):
        FramedModelDriver(
            "other-driver",
            "driver-v1",
            ref("driver-artifact"),
            io.BytesIO(),
            io.BytesIO(),
            restart_state(),
        )
