"""Loopback-only framed server and one-shot clients for a live calibration run."""

from __future__ import annotations

import os
import secrets
import socket
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from calibration.core import (
    BudgetExceeded,
    CalibrationError,
    KnownNoEffectDenied,
)
from calibration.models import AssessmentDraft, ProposalDraft
from calibration.workflow import CalibrationHarness
from strongwiz.canonical import canonical_bytes, deep_thaw_json
from strongwiz.lab import RunDisposition
from strongwiz.transport import (
    BinaryReader,
    BinaryWriter,
    ReplayGuard,
    read_frame,
    read_identified_frame,
    write_frame,
)


class CalibrationControlServer:
    """Own the live harness; clients never receive an environment or game-source handle."""

    def __init__(self, harness: CalibrationHarness) -> None:
        self.harness = harness
        self._replay = ReplayGuard(capacity=8192)
        self._response_sequence = 0
        self._stopping = False
        self._capability: str | None = None

    def _response(self, *, ok: bool, payload: object) -> dict[str, object]:
        self._response_sequence += 1
        return {
            "kind": "response",
            "message_id": f"server-{self._response_sequence:08d}",
            "ok": ok,
            "payload": payload,
        }

    def dispatch(self, request: Mapping[str, object]) -> dict[str, object]:
        kind = request.get("kind")
        payload = request.get("payload", {})
        if not isinstance(kind, str) or not isinstance(payload, Mapping):
            raise CalibrationError("control request requires kind and object payload")
        if kind == "status":
            return self._response(ok=True, payload=self.harness.status())
        if kind == "act":
            try:
                result = self.harness.act(ProposalDraft.model_validate(payload))
                return self._response(ok=True, payload=result)
            except (BudgetExceeded, KnownNoEffectDenied) as error:
                terminal = self.harness.finalize(
                    disposition=RunDisposition.PARTIAL,
                    summary="a preregistered or known-no-effect boundary denied the action",
                    incidents=(f"KNOWN_NO_EFFECT:{type(error).__name__}",),
                    unresolved_burdens=("official WIN was not earned before the boundary",),
                )
                self._stopping = True
                return self._response(
                    ok=False,
                    payload={
                        "error": "BUDGET_OR_CONTROL_BOUNDARY",
                        "terminal": terminal.model_dump(mode="json", by_alias=True),
                    },
                )
            except CalibrationError:
                if self.harness.unknown_effect:
                    terminal = self.harness.finalize_unknown_effect(
                        failure_stage="environment_action_execution",
                        error_class="UnknownExecutorEffect",
                    )
                    self._stopping = True
                    return self._response(
                        ok=False,
                        payload={
                            "error": "UNKNOWN_EFFECT",
                            "terminal": terminal.model_dump(mode="json", by_alias=True),
                        },
                    )
                raise
        if kind == "assess":
            result = self.harness.assess(AssessmentDraft.model_validate(payload))
            if self.harness.session.receipt().completion_genuinely_observed:
                terminal = self.harness.finalize(
                    disposition=RunDisposition.SUCCESS_OBSERVED,
                    summary="official WIN was assessed through the frozen Strongwiz boundary",
                )
                self._stopping = True
                result["terminal"] = terminal.model_dump(mode="json", by_alias=True)
            return self._response(ok=True, payload=result)
        if kind == "stop":
            summary = payload.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise CalibrationError("stop requires a concise summary")
            terminal = self.harness.finalize(
                disposition=RunDisposition.PARTIAL,
                summary=summary,
                unresolved_burdens=("official WIN was not earned before the stop",),
            )
            self._stopping = True
            return self._response(
                ok=True,
                payload=terminal.model_dump(mode="json", by_alias=True),
            )
        raise CalibrationError(f"unknown control command: {kind}")

    def serve(self, endpoint_path: Path, *, port: int = 0) -> None:
        if port < 0 or port > 65535:
            raise ValueError("port must be in 0..65535")
        endpoint_path.parent.mkdir(parents=True, exist_ok=True)
        capability = secrets.token_hex(32)
        self._capability = capability
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(4)
            address = cast(tuple[str, int], listener.getsockname())
            _replace_endpoint(
                endpoint_path,
                {
                    "capability": capability,
                    "closed": False,
                    "host": "127.0.0.1",
                    "pid": os.getpid(),
                    "port": address[1],
                    "run_id": self.harness.bundle.run_id,
                    "schema": "strongwiz.arc-agi3-local-control-endpoint.v1",
                    "transport": "SWZJ-v1-length-prefixed-canonical-json",
                },
            )
            try:
                while not self._stopping:
                    connection, _peer = listener.accept()
                    with connection:
                        connection.settimeout(30.0)
                        stream = connection.makefile("rwb", buffering=0)
                        try:
                            decoded = read_identified_frame(
                                stream,
                                self._replay,
                                timeout_seconds=30.0,
                            )
                            value = deep_thaw_json(decoded.value)
                            if not isinstance(value, dict):
                                raise CalibrationError("control request must be an object")
                            supplied_capability = value.pop("capability", None)
                            if not isinstance(
                                supplied_capability, str
                            ) or not secrets.compare_digest(supplied_capability, capability):
                                raise CalibrationError("control capability rejected")
                            response = self.dispatch(cast(dict[str, object], value))
                        except Exception as error:
                            response = self._response(
                                ok=False,
                                payload={
                                    "error": type(error).__name__,
                                    "message": str(error),
                                },
                            )
                        write_frame(
                            cast(BinaryWriter, stream),
                            response,
                            timeout_seconds=30.0,
                        )
            finally:
                _replace_endpoint(
                    endpoint_path,
                    {
                        "closed": True,
                        "host": "127.0.0.1",
                        "pid": os.getpid(),
                        "port": address[1],
                        "run_id": self.harness.bundle.run_id,
                        "schema": "strongwiz.arc-agi3-local-control-endpoint.v1",
                        "transport": "SWZJ-v1-length-prefixed-canonical-json",
                    },
                )
                self._capability = None
                self.harness.close()


def _replace_endpoint(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_bytes(value))
    os.replace(temporary, path)


def read_endpoint(endpoint_path: Path) -> tuple[str, int, str]:
    from strongwiz.canonical import parse_strict_json

    value = deep_thaw_json(parse_strict_json(endpoint_path.read_bytes()))
    if not isinstance(value, dict):
        raise CalibrationError("control endpoint is not an object")
    if value.get("closed") is True:
        raise CalibrationError("control server is closed")
    host = value.get("host")
    port = value.get("port")
    capability = value.get("capability")
    if (
        host != "127.0.0.1"
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not isinstance(capability, str)
        or len(capability) != 64
    ):
        raise CalibrationError("control endpoint is not loopback-only")
    return host, port, capability


def send_command(
    endpoint_path: Path,
    *,
    kind: str,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    host, port, capability = read_endpoint(endpoint_path)
    request = {
        "capability": capability,
        "kind": kind,
        "message_id": f"client-{uuid.uuid4()}",
        "payload": {} if payload is None else dict(payload),
    }
    with socket.create_connection((host, port), timeout=10.0) as connection:
        connection.settimeout(30.0)
        stream = connection.makefile("rwb", buffering=0)
        write_frame(cast(BinaryWriter, stream), request, timeout_seconds=10.0)
        value = deep_thaw_json(read_frame(cast(BinaryReader, stream), timeout_seconds=30.0))
    if not isinstance(value, dict):
        raise CalibrationError("control response is not an object")
    return cast(dict[str, object], value)
