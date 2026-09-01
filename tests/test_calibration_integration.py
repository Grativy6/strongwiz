from __future__ import annotations

import gc
import io
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from arcengine import ActionInput, FrameDataRaw, GameAction, GameState
from PIL import Image

from calibration.core import (
    BudgetCounter,
    BudgetExceeded,
    CalibrationError,
    LocalControlProtocol,
    OfficialAssetAcquirer,
    OfficialLocalArcPort,
    RawFrameDataAdapter,
    RawTraceStore,
    SingleWriterArcExecutor,
)
from calibration.models import (
    AssessmentDraft,
    AssetFile,
    CalibrationBudgets,
    OfficialAssetManifest,
    ProposalDraft,
    RunTerminalRecord,
    load_preregistration,
)
from calibration.server import CalibrationControlServer, read_endpoint
from calibration.workflow import (
    CalibrationHarness,
    _verify_baseline,
    pack_run,
    prepare_run,
    seal_prepared_run,
)
from strongwiz.canonical import canonical_bytes, content_hash, parse_strict_json, sha256_bytes
from strongwiz.contracts import ActionSpec, DecisionEffect
from strongwiz.drivers import ExecutionCommand, TerminalAuthority
from strongwiz.lab import RunDisposition
from strongwiz.transport import (
    DuplicateFrameError,
    decode_frame,
    encode_frame,
    read_frame,
    write_frame,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _raw_frame(
    state: GameState,
    *,
    action: GameAction = GameAction.RESET,
    available: tuple[GameAction, ...] = (GameAction.ACTION1,),
    pixel: int = 1,
    levels_completed: int = 0,
) -> FrameDataRaw:
    raw = FrameDataRaw(
        game_id="ls20-testversion",
        state=state,
        levels_completed=levels_completed,
        win_levels=levels_completed,
        action_input=ActionInput(id=action),
        guid="fixture-guid",
        available_actions=[item.value for item in available],
    )
    raw.frame = [np.full((4, 4), pixel, dtype=np.uint8)]
    return raw


@dataclass
class _Response:
    content: bytes
    status_code: int = 200


class _HTTPClient:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str] | Any, timeout: float) -> _Response:
        del timeout
        self.calls.append((url, dict(headers)))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)


class _Port:
    def __init__(self, initial: FrameDataRaw, after: list[FrameDataRaw]) -> None:
        self._initial = initial
        self.after = after
        self.calls: list[tuple[GameAction, dict[str, Any]]] = []

    @property
    def initial_frame(self) -> FrameDataRaw:
        return self._initial

    def step(self, action: GameAction, data: dict[str, Any] | Any) -> FrameDataRaw:
        self.calls.append((action, dict(data)))
        if not self.after:
            raise AssertionError("fixture has no post-action frame")
        return self.after.pop(0)

    def recording_paths(self) -> tuple[Path, ...]:
        return ()


def _command(
    executor: SingleWriterArcExecutor,
    *,
    invocation: str,
    action: ActionSpec,
) -> ExecutionCommand:
    return ExecutionCommand(
        invocation_id=invocation,
        idempotency_key=content_hash({"invocation": invocation}),
        grant_ref=content_hash("grant"),
        admission_ref=content_hash({"admission": invocation}),
        proposal_ref=content_hash({"proposal": invocation}),
        action_ref=action.digest,
        action=action,
        executor_id=executor.executor_id,
        executor_version=executor.executor_version,
        executor_artifact_ref=executor.executor_artifact_ref,
    )


def _proposal(request_ref: str, action: str, *, suffix: str) -> ProposalDraft:
    return ProposalDraft(
        message_id=f"proposal-{suffix}",
        request_ref=request_ref,
        proposal_id=f"candidate-{suffix}",
        action_name=action,
        distinction_id=f"distinction-{suffix}",
        distinction_statement="Does this action advance or fail on the current surface?",
        candidate_resolutions=("advances", "does not advance"),
        competing_predictions=("state changes", "state remains unchanged"),
        decision_effects=(DecisionEffect.MOVEMENT,),
        decision_that_could_change="whether to continue this action pattern",
        relevance_summary="the next consequence changes the next legal experiment",
        smallest_discriminating_test="perform this one reversible environment action",
        reopening_condition="the raw returned frame conflicts with the selected resolution",
        prediction_id=f"prediction-{suffix}",
        expected_consequences=("state may change",),
        falsified_by=("the returned frame contradicts the proposed effect",),
        concise_rationale="one bounded action distinguishes the live alternatives",
        reversible=True,
        expected_progress_rank=1,
        information_gain_rank=1,
        risk_rank=0,
    )


def _assessment(proposal_ref: str, *, suffix: str) -> AssessmentDraft:
    return AssessmentDraft(
        message_id=f"assessment-{suffix}",
        proposal_ref=proposal_ref,
        matched_prediction_items=("state may change",),
        concise_update_summary="the exact returned frame was retained and localized",
    )


def _write_fixture_asset(root: Path) -> OfficialAssetManifest:
    relative = Path("environments/ls20/testversion")
    metadata = canonical_bytes(
        {
            "class_name": "Ls20",
            "default_fps": 5,
            "game_id": "ls20-testversion",
            "title": "sealed fixture",
        }
    )
    source = b"# opaque nonexecuted fixture bytes\n"
    metadata_path = root / relative / "metadata.json"
    source_path = root / relative / "ls20.py"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_bytes(metadata)
    source_path.write_bytes(source)
    manifest = OfficialAssetManifest(
        base_game_id="ls20",
        exact_game_id="ls20-testversion",
        class_name="Ls20",
        metadata_file=AssetFile(
            relative_path=metadata_path.relative_to(root).as_posix(),
            size_bytes=len(metadata),
            sha256=sha256_bytes(metadata),
        ),
        source_file=AssetFile(
            relative_path=source_path.relative_to(root).as_posix(),
            size_bytes=len(source),
            sha256=sha256_bytes(source),
        ),
        arc_agi_version="0.9.9",
        arcengine_version="0.9.3",
    )
    (root / "ls20.asset.json").write_bytes(canonical_bytes(manifest))
    return manifest


def test_preregistration_is_strict_and_retains_declared_budgets() -> None:
    loaded = load_preregistration(
        REPOSITORY_ROOT,
        REPOSITORY_ROOT / "docs/calibrations/001-preregistration.json",
    )
    assert loaded.preregistration.evaluation.game_name == "ls20"
    assert loaded.preregistration.budgets.maximum_non_reset_actions == 2048
    assert loaded.preregistration.budgets.maximum_resets == 64
    assert loaded.preregistration.budgets.maximum_total_environment_calls == 2112
    assert loaded.preregistration.budgets.wall_clock_seconds == 28800
    assert loaded.preregistration.clean_room.prior_run_refs == ()
    assert loaded.preregistration.clean_room.prior_domain_state_refs == ()


def test_asset_acquisition_is_download_only_sanitized_and_keyless(tmp_path: Path) -> None:
    anonymous_key = "anonymous-secret-must-not-persist"
    metadata = canonical_bytes(
        {
            "baseline_actions": [1, 2, 3],
            "class_name": "Ls20",
            "default_fps": 7,
            "game_id": "ls20-version123",
            "private_tags": ["do-not-project"],
            "tags": ["also-not-needed"],
            "title": "Official public fixture",
        }
    )
    source = b"# opaque official-source fixture\n"
    client = _HTTPClient(
        [
            _Response(canonical_bytes({"api_key": anonymous_key})),
            _Response(metadata),
            _Response(source),
        ]
    )
    manifest = OfficialAssetAcquirer(client=client).acquire(tmp_path)
    assert [url for url, _headers in client.calls] == [
        "https://three.arcprize.org/api/games/anonkey",
        "https://three.arcprize.org/api/games/ls20",
        "https://three.arcprize.org/api/games/ls20-version123/source",
    ]
    assert "X-Api-Key" not in client.calls[0][1]
    assert client.calls[1][1]["X-Api-Key"] == anonymous_key
    assert manifest.environment_constructed is False
    assert manifest.anonymous_key_persisted is False
    safe_metadata = parse_strict_json(
        (tmp_path / manifest.metadata_file.relative_path).read_bytes()
    )
    assert isinstance(safe_metadata, dict)
    assert set(safe_metadata) == {"class_name", "default_fps", "game_id", "title"}
    retained = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert anonymous_key.encode() not in retained
    assert b"baseline_actions" not in retained
    assert b"private_tags" not in retained
    assert OfficialAssetAcquirer(client=client).acquire(tmp_path) == manifest
    assert len(client.calls) == 3


def test_raw_adapter_trace_and_single_writer_enforce_game_over_reset(tmp_path: Path) -> None:
    domain = tmp_path / "domain"
    domain.mkdir()
    initial = _raw_frame(GameState.NOT_FINISHED, pixel=1)
    game_over = _raw_frame(
        GameState.GAME_OVER,
        action=GameAction.ACTION1,
        available=(GameAction.ACTION1, GameAction.RESET),
        pixel=2,
    )
    recovered = _raw_frame(
        GameState.NOT_FINISHED,
        action=GameAction.RESET,
        available=(GameAction.ACTION1,),
        pixel=3,
    )
    port = _Port(initial, [game_over, recovered])
    budget = BudgetCounter(
        CalibrationBudgets(
            maximum_non_reset_actions=2,
            maximum_resets=2,
            maximum_total_environment_calls=4,
            wall_clock_seconds=100,
        )
    )
    executor = SingleWriterArcExecutor(port, budget, RawTraceStore(domain))
    first = _command(executor, invocation="first", action=ActionSpec(name="ACTION1"))
    result = executor.execute(first)
    assert executor.execute(first) is result
    assert len(port.calls) == 1
    adapter = RawFrameDataAdapter()
    observation = adapter.normalize_observation(executor.current)
    assert observation.available_action_names == ("RESET",)
    assert adapter.terminal_authority(observation) is TerminalAuthority.FAILURE
    image_path = domain / executor.current.evidence.image_relative_paths[0]
    assert image_path.is_file()
    with Image.open(image_path) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (153, 153, 153)
    with pytest.raises(CalibrationError, match="legal aperture"):
        executor.execute(
            _command(executor, invocation="illegal", action=ActionSpec(name="ACTION1"))
        )
    executor.execute(_command(executor, invocation="reset", action=ActionSpec(name="RESET")))
    assert [action for action, _data in port.calls] == [GameAction.ACTION1, GameAction.RESET]
    assert budget.resets == 2  # implicit constructor reset plus recovery reset
    assert budget.non_reset_actions == 1
    assert budget.total_environment_calls == 3
    assert (domain / "raw-trace.jsonl").read_bytes().count(b"\n") == 3


def test_local_control_protocol_is_length_prefixed_and_replay_guarded() -> None:
    draft = _proposal(content_hash("request"), "ACTION1", suffix="wire")
    wire_value = {
        "kind": "proposal_draft",
        "message_id": "outer-wire",
        "payload": draft.model_dump(mode="json", by_alias=True),
    }
    incoming = io.BytesIO(encode_frame(wire_value) + encode_frame(wire_value))
    outgoing = io.BytesIO()
    protocol = LocalControlProtocol(incoming, outgoing)
    assert protocol.receive_proposal() == draft
    with pytest.raises(DuplicateFrameError):
        protocol.receive_proposal()
    protocol.send(message_id="status-wire", kind="status", payload={"state": "ready"})
    decoded = decode_frame(outgoing.getvalue())
    assert isinstance(decoded, dict)
    assert decoded["kind"] == "status"


class _StatusHarness:
    def __init__(self) -> None:
        self.bundle = SimpleNamespace(run_id="loopback-fixture")
        self.closed = False

    def status(self) -> dict[str, object]:
        return {"image_paths": ["fixture.png"], "state": "NOT_FINISHED"}

    def close(self) -> None:
        self.closed = True


def _round_trip(
    endpoint: Path,
    request: object,
    *,
    capability_override: str | None = None,
) -> dict[str, object]:
    host, port, capability = read_endpoint(endpoint)
    assert isinstance(request, dict)
    authenticated = {
        **request,
        "capability": capability if capability_override is None else capability_override,
    }
    with socket.create_connection((host, port), timeout=2) as connection:
        stream = connection.makefile("rwb", buffering=0)
        write_frame(stream, authenticated)
        response = read_frame(stream)
    assert isinstance(response, dict)
    return dict(response)


def test_loopback_server_uses_framing_and_rejects_duplicate_message_ids(
    tmp_path: Path,
) -> None:
    harness = _StatusHarness()
    server = CalibrationControlServer(harness)  # type: ignore[arg-type]
    endpoint = tmp_path / "endpoint.json"
    thread = threading.Thread(target=server.serve, args=(endpoint,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not endpoint.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert endpoint.exists()
    _host, _port, capability = read_endpoint(endpoint)
    assert len(capability) == 64
    rejected = _round_trip(
        endpoint,
        {"kind": "status", "message_id": "foreign-message", "payload": {}},
        capability_override="0" * 64,
    )
    assert rejected["ok"] is False
    assert capability not in repr(rejected)
    request = {"kind": "status", "message_id": "same-message", "payload": {}}
    first = _round_trip(endpoint, request)
    assert first["ok"] is True
    server._stopping = True  # wake once more, then close the fixture server
    duplicate = _round_trip(endpoint, request)
    assert duplicate["ok"] is False
    assert duplicate["payload"]["error"] == "DuplicateFrameError"  # type: ignore[index]
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert harness.closed
    assert b"capability" not in endpoint.read_bytes()


def test_game_over_assessment_retains_failure_then_reset_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_fixture_asset(assets)
    run_root = tmp_path / "run"
    prepare_run(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
        run_id="fixture-game-over-recovery",
    )
    port = _Port(
        _raw_frame(GameState.NOT_FINISHED, pixel=1),
        [
            _raw_frame(
                GameState.GAME_OVER,
                action=GameAction.ACTION1,
                available=(GameAction.ACTION1, GameAction.RESET),
                pixel=2,
            ),
            _raw_frame(
                GameState.NOT_FINISHED,
                action=GameAction.RESET,
                available=(GameAction.ACTION1,),
                pixel=3,
            ),
        ],
    )
    monkeypatch.setattr(OfficialLocalArcPort, "open", lambda **_kwargs: port)
    harness = CalibrationHarness(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
    )
    live_lock = run_root / "state/domain/control/.calibration-live.lock"
    assert live_lock.is_file()
    assert (run_root / "state/domain/control/initial-reset.admission.json").is_file()
    assert (run_root / "state/domain/control/initial-reset.completion.json").is_file()
    first_status = harness.status()
    dangling = content_hash("external-dangling-provenance")
    with pytest.raises(CalibrationError, match="not run-local"):
        harness.act(
            _proposal(
                str(first_status["request_ref"]), "ACTION1", suffix="dangling"
            ).model_copy(update={"hypothesis_refs": (dangling,)})
        )
    first = harness.act(
        _proposal(str(first_status["request_ref"]), "ACTION1", suffix="failure").model_copy(
            update={"hypothesis_refs": (harness.bundle.goal.digest,)}
        )
    )
    with pytest.raises(CalibrationError, match="not run-local"):
        harness.assess(
            _assessment(str(first["proposal_ref"]), suffix="dangling").model_copy(
                update={"residual_refs": (dangling,)}
            )
        )
    harness.assess(
        _assessment(str(first["proposal_ref"]), suffix="failure").model_copy(
            update={"residual_refs": (harness.bundle.goal.digest,)}
        )
    )
    after_failure = harness.status()
    assert after_failure["state"] == "GAME_OVER"
    assert after_failure["phase"] == "ready_to_act"
    assert after_failure["frame"]["available_action_names"] == ["RESET"]  # type: ignore[index]
    assessments = harness.session.receipt().assessments
    assert len(assessments) == 1
    assert assessments[0].terminal_authority is TerminalAuthority.FAILURE

    reset = harness.act(
        _proposal(str(after_failure["request_ref"]), "RESET", suffix="recovery")
    )
    harness.assess(_assessment(str(reset["proposal_ref"]), suffix="recovery"))
    recovered = harness.status()
    assert recovered["state"] == "NOT_FINISHED"
    assert recovered["phase"] == "ready_to_act"
    assert len(harness.session.receipt().assessments) == 2
    assert harness.session.receipt().assessments[0].terminal_authority is (
        TerminalAuthority.FAILURE
    )
    from strongwiz.lab import RunDisposition

    with pytest.raises(CalibrationError, match="success cannot be claimed"):
        harness.finalize(
            disposition=RunDisposition.SUCCESS_OBSERVED,
            summary="must not promote a non-WIN fixture",
        )
    terminal = harness.finalize(
        disposition=RunDisposition.PARTIAL,
        summary="fixture stopped after proving GAME_OVER reset recovery",
    )
    assert not terminal.completion_genuinely_observed
    harness.close()
    assert not live_lock.exists()

    receipt = pack_run(
        run_root=run_root,
        capsule_root=tmp_path / "capsule",
        delivery_receipt_path=tmp_path / "delivery-receipt.json",
    )
    assert receipt.capsule_verified
    assert receipt.terminal_record.final_state == "NOT_FINISHED"
    assert receipt.terminal_record.budget.resets == 2
    assert receipt.terminal_record.budget.non_reset_actions == 1


def test_initial_reset_failure_is_durable_nonretryable_and_sealable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "initial-failure-assets"
    assets.mkdir()
    _write_fixture_asset(assets)
    run_root = tmp_path / "initial-failure-run"
    prepare_run(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
        run_id="fixture-initial-reset-failure",
    )
    opens = 0

    def fail_open(**_kwargs: object) -> _Port:
        nonlocal opens
        opens += 1
        raise RuntimeError("fixture setup failure")

    monkeypatch.setattr(OfficialLocalArcPort, "open", fail_open)
    with pytest.raises(CalibrationError, match="retry is forbidden"):
        CalibrationHarness(
            repository_root=REPOSITORY_ROOT,
            run_root=run_root,
            assets_root=assets,
        )
    admission_path = run_root / "state/domain/control/initial-reset.admission.json"
    terminal_path = run_root / "state/domain/terminal.record.json"
    assert admission_path.is_file()
    assert (run_root / "state/domain/control/interrupted-run.json").is_file()
    terminal = RunTerminalRecord.model_validate_json(terminal_path.read_bytes())
    assert terminal.final_state == "UNKNOWN_EFFECT"
    assert terminal.disposition == RunDisposition.FAILED_INFRASTRUCTURE.value
    assert terminal.budget.resets == 1
    assert terminal.budget.total_environment_calls == 1
    with pytest.raises(CalibrationError, match="terminal run cannot be reopened"):
        CalibrationHarness(
            repository_root=REPOSITORY_ROOT,
            run_root=run_root,
            assets_root=assets,
        )
    assert opens == 1
    seal = seal_prepared_run(run_root)
    assert seal.disposition is RunDisposition.FAILED_INFRASTRUCTURE
    assert not seal.completion_genuinely_observed


def test_unclosed_initial_admission_blocks_retry_and_seals_unknown_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "initial-interrupt-assets"
    assets.mkdir()
    _write_fixture_asset(assets)
    run_root = tmp_path / "initial-interrupt-run"
    prepare_run(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
        run_id="fixture-initial-reset-interrupt",
    )
    opens = 0

    def interrupt_open(**_kwargs: object) -> _Port:
        nonlocal opens
        opens += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(OfficialLocalArcPort, "open", interrupt_open)
    with pytest.raises(KeyboardInterrupt):
        CalibrationHarness(
            repository_root=REPOSITORY_ROOT,
            run_root=run_root,
            assets_root=assets,
        )
    gc.collect()
    assert not (run_root / "state/domain/terminal.record.json").exists()
    with pytest.raises(CalibrationError, match="initial reset was already admitted"):
        CalibrationHarness(
            repository_root=REPOSITORY_ROOT,
            run_root=run_root,
            assets_root=assets,
        )
    assert opens == 1
    seal = seal_prepared_run(run_root)
    assert seal.disposition is RunDisposition.FAILED_INFRASTRUCTURE
    terminal = RunTerminalRecord.model_validate_json(
        (run_root / "state/domain/terminal.record.json").read_bytes()
    )
    assert terminal.final_state == "UNKNOWN_EFFECT"
    assert terminal.raw_trace is None


def test_budget_preflight_and_reserve_race_are_known_no_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    limits = CalibrationBudgets(
        maximum_non_reset_actions=2,
        maximum_resets=1,
        maximum_total_environment_calls=3,
        wall_clock_seconds=10,
    )
    budget = BudgetCounter(limits, monotonic=lambda: clock[0])
    budget.start_wall_clock()
    initial_index = budget.reserve_initial_reset()
    with pytest.raises(BudgetExceeded, match="reset budget"):
        budget.preflight("RESET")
    budget.non_reset_actions = limits.maximum_non_reset_actions
    with pytest.raises(BudgetExceeded, match="non-reset action"):
        budget.preflight("ACTION1")
    budget.non_reset_actions = 0
    budget.total_environment_calls = limits.maximum_total_environment_calls
    with pytest.raises(BudgetExceeded, match="total environment-call"):
        budget.preflight("ACTION1")
    budget.total_environment_calls = 1
    trace_root = tmp_path / "race-domain"
    trace_root.mkdir()
    trace = RawTraceStore(trace_root)
    initial = trace.capture(
        _raw_frame(GameState.NOT_FINISHED),
        occurrence_id="initial-reset",
        call_index=initial_index,
    )
    port = _Port(initial.raw, [])
    executor = SingleWriterArcExecutor(port, budget, trace, initial=initial)
    budget.preflight("ACTION1")
    clock[0] = 11.0
    command = _command(
        executor,
        invocation="reserve-race",
        action=ActionSpec(name="ACTION1"),
    )
    with pytest.raises(BudgetExceeded, match="wall-clock"):
        executor.execute(command)
    assert executor.effect_started(command.idempotency_key) is False
    assert executor.known_no_effect_budget_denial(command.idempotency_key) is not None
    assert port.calls == []

    assets = tmp_path / "budget-assets"
    assets.mkdir()
    _write_fixture_asset(assets)
    run_root = tmp_path / "budget-run"
    prepare_run(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
        run_id="fixture-budget-terminal",
    )
    live_port = _Port(_raw_frame(GameState.NOT_FINISHED), [])
    monkeypatch.setattr(OfficialLocalArcPort, "open", lambda **_kwargs: live_port)
    harness = CalibrationHarness(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
    )
    harness.budget.non_reset_actions = harness.budget.limits.maximum_non_reset_actions
    status = harness.status()
    server = CalibrationControlServer(harness)
    response = server.dispatch(
        {
            "kind": "act",
            "payload": _proposal(
                str(status["request_ref"]), "ACTION1", suffix="budget"
            ).model_dump(mode="json", by_alias=True),
        }
    )
    assert response["ok"] is False
    assert response["payload"]["error"] == "BUDGET_OR_CONTROL_BOUNDARY"  # type: ignore[index]
    assert live_port.calls == []
    terminal = RunTerminalRecord.model_validate_json(
        (run_root / "state/domain/terminal.record.json").read_bytes()
    )
    assert terminal.disposition == RunDisposition.PARTIAL.value
    assert not terminal.completion_genuinely_observed
    harness.close()


def test_post_effect_persistence_failure_returns_frame_and_crash_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "persistence-assets"
    assets.mkdir()
    _write_fixture_asset(assets)
    run_root = tmp_path / "persistence-run"
    prepare_run(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
        run_id="fixture-post-effect-persistence",
    )
    port = _Port(
        _raw_frame(GameState.NOT_FINISHED, pixel=1),
        [
            _raw_frame(
                GameState.NOT_FINISHED,
                action=GameAction.ACTION1,
                pixel=2,
            )
        ],
    )
    opens = 0

    def open_port(**_kwargs: object) -> _Port:
        nonlocal opens
        opens += 1
        return port

    monkeypatch.setattr(OfficialLocalArcPort, "open", open_port)
    harness = CalibrationHarness(
        repository_root=REPOSITORY_ROOT,
        run_root=run_root,
        assets_root=assets,
    )
    original_record = harness._record_control

    def fail_after_effect(
        kind: str, values: tuple[object, ...], payload: dict[str, object]
    ) -> None:
        if kind == "execution_boundary":
            raise OSError("fixture ledger persistence failure")
        original_record(kind, values, payload)

    monkeypatch.setattr(harness, "_record_control", fail_after_effect)
    status = harness.status()
    result = harness.act(_proposal(str(status["request_ref"]), "ACTION1", suffix="persistence"))
    assert result["assessment_required"] is True
    assert result["state"] == "NOT_FINISHED"
    assert "post_effect_persistence:OSError" in result["persistence_warning"]
    assert harness.status()["expected_next"] == "assessment_draft"
    assert len(port.calls) == 1
    assert (run_root / "state/domain/raw-trace.jsonl").read_bytes().count(b"\n") == 2
    harness.ledger.close()  # Simulate process loss without releasing its durable lock.
    gc.collect()
    with pytest.raises(CalibrationError, match="initial reset was already admitted"):
        CalibrationHarness(
            repository_root=REPOSITORY_ROOT,
            run_root=run_root,
            assets_root=assets,
        )
    assert opens == 1
    assert len(port.calls) == 1
    seal = seal_prepared_run(run_root)
    assert seal.disposition is RunDisposition.FAILED_INFRASTRUCTURE
    terminal = RunTerminalRecord.model_validate_json(
        (run_root / "state/domain/terminal.record.json").read_bytes()
    )
    assert terminal.final_state == "UNKNOWN_EFFECT"
    assert terminal.raw_trace is not None
    assert terminal.budget.total_environment_calls == 2


def test_baseline_verifier_rejects_changed_and_untracked_kernel_bytes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "kernel-baseline-repo"
    kernel = repository / "src/strongwiz"
    kernel.mkdir(parents=True)
    source = kernel / "kernel.py"
    source.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", "src/strongwiz/kernel.py")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    _verify_baseline(repository, commit=commit, tree=tree)
    source.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    with pytest.raises(CalibrationError, match="working-tree bytes"):
        _verify_baseline(repository, commit=commit, tree=tree)
    source.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (kernel / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")
    with pytest.raises(CalibrationError, match="path set"):
        _verify_baseline(repository, commit=commit, tree=tree)
