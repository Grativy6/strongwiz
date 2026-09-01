"""ARC adapter, proposal bridge, transport, acquisition, tracing, and single writer."""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

from arcengine import FrameData, FrameDataRaw, GameAction, GameState
from PIL import Image

from calibration.models import (
    ArtifactPointer,
    AssessmentDraft,
    AssetFile,
    BudgetReceipt,
    CalibrationBudgets,
    FrameEvidence,
    OfficialAssetManifest,
    ProposalDraft,
)
from strongwiz.canonical import (
    JSONValue,
    canonical_bytes,
    content_hash,
    deep_thaw_json,
    parse_strict_json,
    sha256_bytes,
)
from strongwiz.contracts import (
    ActionSpec,
    CandidateProposal,
    CostVector,
    Distinction,
    EvidenceRef,
    Observation,
    Outcome,
    Prediction,
    ReasoningRequest,
    RouteDecision,
    RouteDisposition,
)
from strongwiz.drivers import ExecutionCommand, ExecutorObservation, TerminalAuthority
from strongwiz.integrity import sha256_file
from strongwiz.transport import ReplayGuard, read_identified_frame, write_frame

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CLASS_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_PRIVATE_REASONING_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_cot",
        "hidden_reasoning",
        "internal_monologue",
        "private_reasoning",
        "scratchpad",
        "thought_tokens",
    }
)


class CalibrationError(RuntimeError):
    """The integration failed closed before claiming a completed boundary."""


class BudgetExceeded(CalibrationError):
    """A preregistered run ceiling has been reached."""


class KnownNoEffectDenied(CalibrationError):
    """A control or grant denial occurred before an environment effect started."""


def _safe_component(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise CalibrationError(f"{label} is not a safe path component")
    return value


def _require_digest(value: str, label: str) -> str:
    if not _DIGEST.fullmatch(value):
        raise CalibrationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise CalibrationError(f"stale temporary file blocks write: {temporary.name}")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_idempotent(path: Path, data: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise CalibrationError(f"existing artifact differs: {path}")
        return
    _atomic_write(path, data)


def artifact_pointer(path: Path) -> ArtifactPointer:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise CalibrationError(f"artifact is not a regular file: {resolved}")
    return ArtifactPointer(
        path=str(resolved),
        sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
    )


class HTTPResponse(Protocol):
    status_code: int
    content: bytes


class HTTPClient(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> HTTPResponse: ...


class RequestsHTTPClient:
    """Narrow requests adapter. It never logs request headers or response bodies."""

    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> HTTPResponse:
        import requests

        return cast(
            HTTPResponse,
            requests.get(url, headers=dict(headers), timeout=timeout),
        )


class OfficialAssetAcquirer:
    """Acquire official bytes without importing, executing, or constructing a game."""

    def __init__(
        self,
        *,
        client: HTTPClient | None = None,
        base_url: str = "https://three.arcprize.org",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("official acquisition requires HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive")
        self._client = client or RequestsHTTPClient()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def _get(self, path: str, *, api_key: str | None = None) -> bytes:
        headers = {"Accept": "application/json"}
        if api_key is not None:
            headers["X-Api-Key"] = api_key
        response = self._client.get(
            f"{self._base_url}{path}", headers=headers, timeout=self._timeout
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise CalibrationError(
                f"official asset request failed with HTTP {response.status_code}"
            )
        return bytes(response.content)

    def acquire(self, assets_root: Path, *, game_id: str = "ls20") -> OfficialAssetManifest:
        if game_id != "ls20":
            raise CalibrationError("acquisition target differs from the preregistration")
        assets_root.mkdir(parents=True, exist_ok=True)
        manifest_path = assets_root / "ls20.asset.json"
        if manifest_path.exists():
            manifest = OfficialAssetManifest.model_validate_json(manifest_path.read_bytes())
            verify_asset_manifest(assets_root, manifest)
            return manifest

        # The anonymous key remains a local variable and is never included in an error.
        anon_value = parse_strict_json(self._get("/api/games/anonkey"))
        if not isinstance(anon_value, Mapping):
            raise CalibrationError("anonymous-key response is not an object")
        api_key = anon_value.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise CalibrationError("official API did not supply an anonymous key")

        metadata_bytes = self._get(f"/api/games/{game_id}", api_key=api_key)
        metadata_value = parse_strict_json(metadata_bytes)
        if not isinstance(metadata_value, Mapping):
            raise CalibrationError("official game metadata is not an object")
        exact_game_id = metadata_value.get("game_id")
        if not isinstance(exact_game_id, str) or not exact_game_id.startswith("ls20-"):
            raise CalibrationError("official metadata lacks the expected versioned game ID")
        class_name = metadata_value.get("class_name")
        if class_name is None:
            # Pinned arc-agi 0.9.9 uses this exact fallback when the public
            # metadata response omits class_name.
            class_name = game_id[0].upper() + game_id[1:]
        if not isinstance(class_name, str) or not _CLASS_NAME.fullmatch(class_name):
            raise CalibrationError("official metadata lacks a safe class name")
        _safe_component(exact_game_id, "exact game ID")
        version = _safe_component(exact_game_id.split("-", 1)[1], "game version")

        source_bytes = self._get(f"/api/games/{exact_game_id}/source", api_key=api_key)
        try:
            source_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CalibrationError("official source artifact is not strict UTF-8") from error

        relative_dir = Path("environments") / game_id / version
        metadata_relative = relative_dir / "metadata.json"
        source_relative = relative_dir / f"{class_name.lower()}.py"

        # Private tags and baseline actions are intentionally not projected or hashed.
        default_fps = metadata_value.get("default_fps", 5)
        title = metadata_value.get("title", game_id)
        if (
            isinstance(default_fps, bool)
            or not isinstance(default_fps, int)
            or default_fps <= 0
        ):
            raise CalibrationError("official metadata has an invalid default FPS")
        if not isinstance(title, str) or not title.strip():
            raise CalibrationError("official metadata has an invalid title")
        safe_metadata: dict[str, JSONValue] = {
            "class_name": class_name,
            "default_fps": default_fps,
            "game_id": exact_game_id,
            "title": title,
        }
        safe_metadata_bytes = canonical_bytes(safe_metadata)

        metadata_path = assets_root / metadata_relative
        source_path = assets_root / source_relative
        _write_idempotent(metadata_path, safe_metadata_bytes)
        _write_idempotent(source_path, source_bytes)
        manifest = OfficialAssetManifest(
            base_game_id="ls20",
            exact_game_id=exact_game_id,
            class_name=class_name,
            metadata_file=AssetFile(
                relative_path=metadata_relative.as_posix(),
                size_bytes=len(safe_metadata_bytes),
                sha256=sha256_bytes(safe_metadata_bytes),
            ),
            source_file=AssetFile(
                relative_path=source_relative.as_posix(),
                size_bytes=len(source_bytes),
                sha256=sha256_bytes(source_bytes),
            ),
            arc_agi_version="0.9.9",
            arcengine_version="0.9.3",
        )
        _atomic_write(manifest_path, canonical_bytes(manifest))
        return manifest


def verify_asset_manifest(root: Path, manifest: OfficialAssetManifest) -> None:
    resolved_root = root.resolve(strict=True)
    for item in (manifest.metadata_file, manifest.source_file):
        path = (resolved_root / item.relative_path).resolve(strict=True)
        try:
            path.relative_to(resolved_root)
        except ValueError as error:
            raise CalibrationError("asset manifest path escapes its root") from error
        if not path.is_file() or path.stat().st_size != item.size_bytes:
            raise CalibrationError(f"official asset size changed: {item.relative_path}")
        if sha256_file(path) != item.sha256:
            raise CalibrationError(f"official asset digest changed: {item.relative_path}")


def load_asset_manifest(root: Path, manifest_path: Path) -> OfficialAssetManifest:
    manifest = OfficialAssetManifest.model_validate_json(manifest_path.read_bytes())
    verify_asset_manifest(root, manifest)
    return manifest


class BudgetCounter:
    """Reserve calls before effects so uncertain failures are never free or retried."""

    def __init__(
        self,
        limits: CalibrationBudgets,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._clock = monotonic
        self._started_at: float | None = None
        self.non_reset_actions = 0
        self.resets = 0
        self.total_environment_calls = 0
        self._initial_recorded = False
        self._lock = threading.Lock()

    def start_wall_clock(self) -> None:
        """Start the run clock immediately before the environment boundary."""

        with self._lock:
            if self._started_at is not None:
                raise CalibrationError("wall-clock budget was already started")
            self._started_at = self._clock()

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def reserve_initial_reset(self) -> int:
        with self._lock:
            if self._initial_recorded:
                raise CalibrationError("implicit initial reset was already recorded")
            if self._started_at is None:
                self._started_at = self._clock()
            self._initial_recorded = True
            self.resets = 1
            self.total_environment_calls = 1
            if self.resets > self.limits.maximum_resets:
                raise BudgetExceeded("initial reset exceeds the reset budget")
            if self.total_environment_calls > self.limits.maximum_total_environment_calls:
                raise BudgetExceeded("initial reset exceeds the total call budget")
            if self.elapsed_seconds >= self.limits.wall_clock_seconds:
                raise BudgetExceeded("wall-clock budget expired during environment creation")
            return self.total_environment_calls

    def record_initial_reset(self) -> int:
        """Backward-compatible alias for the pre-effect initial reservation."""

        return self.reserve_initial_reset()

    def _check_action_locked(self, action_name: str) -> None:
        if not self._initial_recorded:
            raise CalibrationError("environment action preceded the implicit initial reset")
        if self.elapsed_seconds >= self.limits.wall_clock_seconds:
            raise BudgetExceeded("wall-clock budget is exhausted")
        if self.total_environment_calls >= self.limits.maximum_total_environment_calls:
            raise BudgetExceeded("total environment-call budget is exhausted")
        if action_name == "RESET":
            if self.resets >= self.limits.maximum_resets:
                raise BudgetExceeded("reset budget is exhausted")
        elif self.non_reset_actions >= self.limits.maximum_non_reset_actions:
            raise BudgetExceeded("non-reset action budget is exhausted")

    def preflight(self, action_name: str) -> None:
        """Prove a budget denial, if any, before reasoning or coordinator admission."""

        with self._lock:
            self._check_action_locked(action_name)

    def reserve(self, action_name: str) -> int:
        with self._lock:
            self._check_action_locked(action_name)
            if action_name == "RESET":
                self.resets += 1
            else:
                self.non_reset_actions += 1
            self.total_environment_calls += 1
            return self.total_environment_calls

    def ensure_time_remaining(self) -> None:
        if (
            self._started_at is not None
            and self.elapsed_seconds >= self.limits.wall_clock_seconds
        ):
            raise BudgetExceeded("wall-clock budget is exhausted")

    def remaining_costs(self) -> CostVector:
        remaining_calls = max(
            0,
            self.limits.maximum_total_environment_calls - self.total_environment_calls,
        )
        remaining_ms = max(
            0,
            int((self.limits.wall_clock_seconds - self.elapsed_seconds) * 1000),
        )
        return CostVector(environment_actions=remaining_calls, wall_clock_ms=remaining_ms)

    def receipt(self) -> BudgetReceipt:
        return BudgetReceipt(
            maximum_non_reset_actions=self.limits.maximum_non_reset_actions,
            maximum_resets=self.limits.maximum_resets,
            maximum_total_environment_calls=self.limits.maximum_total_environment_calls,
            wall_clock_seconds=self.limits.wall_clock_seconds,
            non_reset_actions=self.non_reset_actions,
            resets=self.resets,
            total_environment_calls=self.total_environment_calls,
            elapsed_wall_ms=int(self.elapsed_seconds * 1000),
        )


_ARC_PALETTE = (
    (255, 255, 255),
    (204, 204, 204),
    (153, 153, 153),
    (102, 102, 102),
    (51, 51, 51),
    (0, 0, 0),
    (229, 58, 163),
    (255, 123, 204),
    (249, 60, 49),
    (30, 147, 255),
    (136, 216, 241),
    (255, 220, 0),
    (255, 133, 27),
    (146, 18, 49),
    (79, 204, 48),
    (163, 86, 214),
)


def _frame_projection(raw: FrameData | FrameDataRaw) -> dict[str, JSONValue]:
    if not isinstance(raw.state, GameState):
        raise CalibrationError("frame state is not the pinned arcengine GameState enum")
    layers: list[JSONValue] = []
    for layer in raw.frame:
        value = layer.tolist() if hasattr(layer, "tolist") else layer
        layers.append(cast(JSONValue, value))
    return {
        "action_input": {
            "data": cast(JSONValue, dict(raw.action_input.data)),
            "id": raw.action_input.id.name,
            "reasoning": cast(JSONValue, raw.action_input.reasoning),
        },
        "available_actions": [int(value) for value in raw.available_actions],
        "frame": layers,
        "full_reset": bool(raw.full_reset),
        "game_id": raw.game_id,
        "guid": raw.guid,
        "levels_completed": int(raw.levels_completed),
        "state": raw.state.value,
        "win_levels": int(raw.win_levels),
    }


def _action_names(raw: FrameData | FrameDataRaw) -> tuple[str, ...]:
    names: list[str] = ["RESET"]
    if raw.state is not GameState.GAME_OVER:
        for action_id in raw.available_actions:
            name = GameAction.from_id(int(action_id)).name
            if name != "RESET" and name not in names:
                names.append(name)
    return tuple(names)


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    raw: FrameData | FrameDataRaw
    evidence: FrameEvidence


class RawTraceStore:
    """Single-writer exact raw JSONL plus per-layer PNG projection."""

    def __init__(self, domain_root: Path) -> None:
        self.domain_root = domain_root.resolve(strict=True)
        self.raw_root = self.domain_root / "raw-frames"
        self.image_root = self.domain_root / "images"
        self.trace_path = self.domain_root / "raw-trace.jsonl"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.image_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _save_image(self, layer: object, path: Path) -> None:
        values = layer.tolist() if hasattr(layer, "tolist") else layer
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(row, list) for row in values)
        ):
            raise CalibrationError("frame layer must be a non-empty rectangular grid")
        rows = cast(list[list[object]], values)
        width = len(rows[0])
        if width <= 0 or any(len(row) != width for row in rows):
            raise CalibrationError("frame layer must be rectangular")
        pixels: list[int] = []
        for row in rows:
            for item in row:
                if (
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or not 0 <= item < len(_ARC_PALETTE)
                ):
                    raise CalibrationError("frame pixels must be ARC-AGI-3 palette indices")
                pixels.append(item)
        image = Image.new("P", (width, len(rows)))
        palette = [channel for color in _ARC_PALETTE for channel in color]
        palette.extend([0] * (768 - len(palette)))
        image.putpalette(palette)
        image.putdata(pixels)
        image.save(path, format="PNG", optimize=False)

    def capture(
        self,
        raw: FrameData | FrameDataRaw,
        *,
        occurrence_id: str,
        call_index: int,
    ) -> CapturedFrame:
        _safe_component(occurrence_id, "frame occurrence")
        projection = _frame_projection(raw)
        raw_bytes = canonical_bytes(projection)
        raw_ref = sha256_bytes(raw_bytes)
        stem = f"{call_index:05d}-{occurrence_id}"
        raw_path = self.raw_root / f"{stem}.json"
        with self._lock:
            _write_idempotent(raw_path, raw_bytes)
            image_paths: list[str] = []
            for index, layer in enumerate(raw.frame):
                image_path = self.image_root / f"{stem}-{index:03d}.png"
                if not image_path.exists():
                    self._save_image(layer, image_path)
                image_paths.append(image_path.relative_to(self.domain_root).as_posix())
            evidence = FrameEvidence(
                occurrence_id=occurrence_id,
                call_index=call_index,
                raw_ref=raw_ref,
                raw_relative_path=raw_path.relative_to(self.domain_root).as_posix(),
                image_relative_paths=tuple(image_paths),
                state=raw.state.value,
                game_id=raw.game_id,
                levels_completed=int(raw.levels_completed),
                win_levels=int(raw.win_levels),
                available_action_names=_action_names(raw),
            )
            with self.trace_path.open("ab") as stream:
                stream.write(canonical_bytes(evidence))
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        return CapturedFrame(raw=raw, evidence=evidence)


class RawFrameDataAdapter:
    adapter_id = "arc-agi3-framedata-raw-calibration"
    adapter_version = "1.0.0"

    def __init__(self) -> None:
        self.adapter_artifact_ref = content_hash(
            {
                "component": self.adapter_id,
                "source_sha256": sha256_file(Path(__file__)),
                "version": self.adapter_version,
            }
        )
        self._state_by_observation_ref: dict[str, GameState] = {}

    def normalize_observation(self, raw: object) -> Observation:
        if not isinstance(raw, CapturedFrame):
            raise TypeError("ARC adapter accepts only captured pinned FrameData values")
        frame = raw.raw
        evidence = raw.evidence
        if frame.state.value != evidence.state or frame.game_id != evidence.game_id:
            raise CalibrationError("captured raw frame disagrees with its evidence record")
        observation = Observation(
            observation_id=f"arc-frame:{evidence.occurrence_id}:{evidence.call_index}",
            domain="arc-agi-3-local-public",
            scope_id=f"arc-game:{frame.game_id}",
            epoch=evidence.call_index,
            payload_ref=EvidenceRef(
                kind="arcengine.FrameDataRaw",
                sha256=evidence.raw_ref,
                locator=evidence.raw_relative_path,
            ),
            summary=(
                f"official state={frame.state.value}; levels_completed="
                f"{frame.levels_completed}; win_levels={frame.win_levels}; "
                f"legal_actions={','.join(evidence.available_action_names)}"
            ),
            available_action_names=evidence.available_action_names,
        )
        self._state_by_observation_ref[observation.digest] = frame.state
        return observation

    def available_actions(self, observation: Observation) -> Sequence[ActionSpec]:
        return tuple(ActionSpec(name=name) for name in observation.available_action_names)

    def extract_outcome(
        self, before: Observation, action: ActionSpec, raw_after: object
    ) -> Outcome:
        if not isinstance(raw_after, CapturedFrame):
            raise TypeError("ARC outcome requires captured pinned FrameData")
        after = self.normalize_observation(raw_after)
        echoed = raw_after.raw.action_input.id.name
        if echoed != action.name:
            raise CalibrationError("official post-action frame echoes a different action")
        consequences = [
            f"state:{raw_after.raw.state.value}",
            f"levels_completed:{raw_after.raw.levels_completed}",
            f"win_levels:{raw_after.raw.win_levels}",
        ]
        if before.payload_ref.sha256 == after.payload_ref.sha256:
            consequences.append("raw_frame_unchanged")
        else:
            consequences.append("raw_frame_changed")
        return Outcome(
            outcome_id=f"arc-outcome:{raw_after.evidence.occurrence_id}",
            observation_before_id=before.observation_id,
            observation_before_ref=before.digest,
            observation_after_id=after.observation_id,
            observation_after_ref=after.digest,
            action=action,
            observed_consequences=tuple(consequences),
            state_label=raw_after.raw.state.value,
            evidence_refs=(raw_after.evidence.raw_ref,),
            terminal=raw_after.raw.state is GameState.WIN,
        )

    def terminal_authority(self, observation: Observation) -> TerminalAuthority:
        try:
            state = self._state_by_observation_ref[observation.digest]
        except KeyError as error:
            raise CalibrationError("terminal query lacks a normalized raw frame") from error
        if state is GameState.WIN:
            return TerminalAuthority.SUCCESS
        if state is GameState.GAME_OVER:
            return TerminalAuthority.FAILURE
        return TerminalAuthority.CONTINUE


def _reject_private_reasoning(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _PRIVATE_REASONING_KEYS:
                raise CalibrationError("private chain-of-thought fields are forbidden")
            _reject_private_reasoning(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_private_reasoning(child)


class MemoizedProposalDraftBridge:
    driver_id = "external-context-isolated-codex-proposal-draft"
    driver_version = "1.1.0"

    def __init__(self) -> None:
        declaration = Path(__file__).with_name("model-interface.json")
        self.driver_artifact_ref = content_hash(
            {
                "component": self.driver_id,
                "declaration_sha256": sha256_file(declaration),
                "interface": "memoized externally supplied ProposalDraft",
                "source_sha256": sha256_file(Path(__file__)),
                "version": self.driver_version,
            }
        )
        self._proposals: dict[str, CandidateProposal] = {}
        self._drafts: dict[str, ProposalDraft] = {}
        self._attempt_history: dict[str, list[tuple[ProposalDraft, CandidateProposal]]] = {}
        self._admitted_proposal_refs: dict[str, str] = {}

    def supply(
        self,
        request: ReasoningRequest,
        draft: ProposalDraft,
        *,
        available_evidence_refs: Sequence[str],
    ) -> CandidateProposal:
        _reject_private_reasoning(draft.model_dump(mode="python", by_alias=True))
        if draft.request_ref != request.digest:
            raise CalibrationError("proposal draft does not bind the current request")
        request_ref = request.digest
        if request_ref in self._admitted_proposal_refs:
            raise CalibrationError("an admitted proposal cannot be replaced or replayed")
        prior_draft = self._drafts.get(request_ref)
        if prior_draft is not None:
            if prior_draft == draft:
                return self._proposals[request_ref]
            raise CalibrationError("an active proposal attempt cannot be replaced")
        history = self._attempt_history.get(request_ref, [])
        expected_attempt = len(history) + 1
        expected_predecessor = None if not history else history[-1][1].digest
        if draft.proposal_attempt != expected_attempt:
            raise CalibrationError(
                f"proposal attempt must be exactly {expected_attempt} for the current request"
            )
        if draft.supersedes_proposal_ref != expected_predecessor:
            raise CalibrationError("proposal attempt does not bind the exact held predecessor")
        if draft.action_name not in request.observation.available_action_names:
            raise CalibrationError("proposal draft action is outside the observed aperture")
        if draft.action_name == "ACTION6":
            parameters = dict(draft.action_parameters)
            if set(parameters) != {"x", "y"}:
                raise CalibrationError("ACTION6 requires exactly x and y")
            for name in ("x", "y"):
                value = parameters[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 63
                ):
                    raise CalibrationError("ACTION6 coordinates must be integers in 0..63")
        elif draft.action_parameters:
            raise CalibrationError("simple ARC actions cannot carry parameters")
        evidence_refs = draft.evidence_refs or (request.observation.payload_ref.sha256,)
        available = set(available_evidence_refs)
        if not set(evidence_refs).issubset(available):
            raise CalibrationError("proposal draft cites evidence outside the control aperture")
        for value in (*evidence_refs, *draft.hypothesis_refs):
            _require_digest(value, "proposal evidence or hypothesis reference")

        action = ActionSpec(name=draft.action_name, parameters=draft.action_parameters)
        proposal = CandidateProposal(
            proposal_id=draft.proposal_id,
            model_driver_id=self.driver_id,
            observation_id=request.observation.observation_id,
            observation_ref=request.observation.digest,
            scope_id=request.observation.scope_id,
            goal_id=request.scoped_goal.goal_id,
            goal_ref=request.scoped_goal.digest,
            action=action,
            meaningful_distinction=Distinction(
                distinction_id=draft.distinction_id,
                statement=draft.distinction_statement,
                scope_id=request.observation.scope_id,
                parent_goal_id=request.scoped_goal.goal_id,
                governing_goal_id=request.governing_goal.goal_id,
                candidate_resolutions=draft.candidate_resolutions,
                competing_predictions=draft.competing_predictions,
                decision_effects=draft.decision_effects,
                decision_that_could_change=draft.decision_that_could_change,
                relevance_summary=draft.relevance_summary,
                smallest_discriminating_test=draft.smallest_discriminating_test,
                reopening_condition=draft.reopening_condition,
            ),
            prediction=Prediction(
                prediction_id=draft.prediction_id,
                hypothesis_refs=draft.hypothesis_refs,
                expected_consequences=draft.expected_consequences,
                falsified_by=draft.falsified_by,
                alternatives=draft.alternatives,
            ),
            decision_effects=draft.decision_effects,
            evidence_refs=tuple(evidence_refs),
            concise_rationale=draft.concise_rationale,
            reversible=draft.reversible,
            expected_progress_rank=draft.expected_progress_rank,
            information_gain_rank=draft.information_gain_rank,
            risk_rank=draft.risk_rank,
            costs=CostVector(
                environment_actions=1,
                irreversible_actions=0 if draft.reversible else 1,
            ),
        )
        if proposal.digest == draft.supersedes_proposal_ref:
            raise CalibrationError("a revised proposal must differ from its held predecessor")
        self._proposals[request_ref] = proposal
        self._drafts[request_ref] = draft
        self._attempt_history.setdefault(request_ref, []).append((draft, proposal))
        return proposal

    def release_unexecuted_for_revision(
        self,
        request: ReasoningRequest,
        *,
        proposal_ref: str,
        route: RouteDecision,
    ) -> tuple[int, str]:
        """Release only a nonselected attempt while retaining its exact lineage."""

        request_ref = request.digest
        if request_ref in self._admitted_proposal_refs:
            raise CalibrationError("an admitted proposal cannot be released for revision")
        try:
            proposal = self._proposals[request_ref]
            draft = self._drafts[request_ref]
        except KeyError as error:
            raise CalibrationError("no active proposal attempt can be revised") from error
        if proposal.digest != proposal_ref:
            raise CalibrationError("revision request does not bind the active proposal")
        if route.selected_proposal_ref is not None or route.disposition in {
            RouteDisposition.ADMIT,
            RouteDisposition.REOPEN,
        }:
            raise CalibrationError("a selected proposal cannot be released for revision")
        history = self._attempt_history.get(request_ref, [])
        if not history or history[-1] != (draft, proposal):
            raise CalibrationError("proposal attempt lineage is inconsistent")
        del self._proposals[request_ref]
        del self._drafts[request_ref]
        return draft.proposal_attempt + 1, proposal.digest

    def mark_admitted(
        self,
        request: ReasoningRequest,
        *,
        proposal_ref: str,
        route: RouteDecision,
    ) -> None:
        """Permanently close replacement once the exact proposal is selected."""

        request_ref = request.digest
        try:
            proposal = self._proposals[request_ref]
        except KeyError as error:
            raise CalibrationError("no active proposal attempt can be admitted") from error
        if (
            proposal.digest != proposal_ref
            or route.selected_proposal_ref != proposal.digest
            or route.disposition not in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}
        ):
            raise CalibrationError("admission does not bind the active proposal attempt")
        admitted = self._admitted_proposal_refs.get(request_ref)
        if admitted is not None and admitted != proposal.digest:
            raise CalibrationError("another proposal attempt is already admitted")
        self._admitted_proposal_refs[request_ref] = proposal.digest

    def expected_revision(self, request: ReasoningRequest) -> tuple[int, str | None]:
        """Expose the exact numbered successor required for an unexecuted request."""

        request_ref = request.digest
        if request_ref in self._admitted_proposal_refs:
            raise CalibrationError("an admitted proposal has no revision aperture")
        active = self._drafts.get(request_ref)
        if active is not None:
            return active.proposal_attempt, active.supersedes_proposal_ref
        history = self._attempt_history.get(request_ref, [])
        if not history:
            return 1, None
        draft, proposal = history[-1]
        return draft.proposal_attempt + 1, proposal.digest

    def propose(self, request: ReasoningRequest) -> Sequence[CandidateProposal]:
        try:
            return (self._proposals[request.digest],)
        except KeyError as error:
            raise CalibrationError(
                "no externally supplied draft is memoized for request"
            ) from error

    def draft_for(self, request: ReasoningRequest) -> ProposalDraft:
        try:
            return self._drafts[request.digest]
        except KeyError as error:
            raise CalibrationError("no draft is memoized for request") from error


class ArcEnvironmentPort(Protocol):
    @property
    def initial_frame(self) -> FrameDataRaw: ...

    def step(self, action: GameAction, data: Mapping[str, Any]) -> FrameDataRaw: ...

    def recording_paths(self) -> tuple[Path, ...]: ...


class OfficialLocalArcPort:
    """The only integration object allowed to hold the official environment wrapper."""

    def __init__(self, wrapper: object, recordings_root: Path) -> None:
        self._wrapper = wrapper
        self._recordings_root = recordings_root
        initial = getattr(wrapper, "observation_space", None)
        if not isinstance(initial, FrameDataRaw):
            raise CalibrationError("Arcade.make did not yield its implicit initial reset frame")
        self._initial = initial

    @classmethod
    def open(
        cls,
        *,
        assets_root: Path,
        manifest: OfficialAssetManifest,
        recordings_root: Path,
        seed: int,
    ) -> OfficialLocalArcPort:
        """Construct the pinned environment. Call only after verified lab genesis."""

        verify_asset_manifest(assets_root, manifest)
        # The dedicated live process is forced offline before arc_agi import because
        # its competition environment variable otherwise overrides constructor input.
        os.environ["OPERATION_MODE"] = "offline"
        os.environ["ARC_API_KEY"] = ""
        os.environ["ONLY_RESET_LEVELS"] = "false"
        from arc_agi import Arcade, OperationMode  # type: ignore[import-untyped]

        recordings_root.mkdir(parents=True, exist_ok=True)
        quiet_logger = logging.getLogger("strongwiz.arc3.calibration.offline")
        quiet_logger.handlers.clear()
        quiet_logger.addHandler(logging.NullHandler())
        quiet_logger.propagate = False
        arcade = Arcade(
            arc_api_key="",
            arc_base_url="http://127.0.0.1:9",
            operation_mode=OperationMode.OFFLINE,
            environments_dir=str((assets_root / "environments").resolve(strict=True)),
            recordings_dir=str(recordings_root.resolve()),
            logger=quiet_logger,
        )
        wrapper = arcade.make(
            manifest.exact_game_id,
            seed=seed,
            save_recording=True,
            include_frame_data=True,
        )
        if wrapper is None:
            raise CalibrationError("pinned offline Arcade.make failed")
        info = getattr(wrapper, "info", None)
        if info is None or getattr(info, "game_id", None) != manifest.exact_game_id:
            raise CalibrationError("constructed wrapper is not the pinned game artifact")
        return cls(wrapper, recordings_root)

    @property
    def initial_frame(self) -> FrameDataRaw:
        return self._initial

    def step(self, action: GameAction, data: Mapping[str, Any]) -> FrameDataRaw:
        method = getattr(self._wrapper, "step", None)
        if not callable(method):
            raise CalibrationError("official wrapper lost its step method")
        value = method(
            action,
            data=dict(data),
            reasoning={"record": "concise Strongwiz action receipt; no hidden reasoning"},
        )
        if not isinstance(value, FrameDataRaw):
            raise CalibrationError("official wrapper returned no pinned FrameDataRaw")
        return value

    def recording_paths(self) -> tuple[Path, ...]:
        if not self._recordings_root.exists():
            return ()
        return tuple(
            sorted(path for path in self._recordings_root.rglob("*.jsonl") if path.is_file())
        )


class SingleWriterArcExecutor:
    executor_id = "arc-agi3-official-local-single-writer"
    executor_version = "1.0.0"

    @classmethod
    def declared_artifact_ref(cls) -> str:
        return content_hash(
            {
                "component": cls.executor_id,
                "source_sha256": sha256_file(Path(__file__)),
                "version": cls.executor_version,
            }
        )

    def __init__(
        self,
        port: ArcEnvironmentPort,
        budget: BudgetCounter,
        trace: RawTraceStore,
        *,
        initial: CapturedFrame | None = None,
    ) -> None:
        self.executor_artifact_ref = self.declared_artifact_ref()
        self._port = port
        self.budget = budget
        self.trace = trace
        self._write_lock = threading.Lock()
        self._memo: dict[str, ExecutorObservation] = {}
        self._effect_started: dict[str, bool] = {}
        self._known_budget_denials: dict[str, str] = {}
        if initial is None:
            initial_index = self.budget.reserve_initial_reset()
            initial = trace.capture(
                port.initial_frame,
                occurrence_id="initial-reset",
                call_index=initial_index,
            )
        elif self.budget.total_environment_calls != initial.evidence.call_index:
            raise CalibrationError("initial frame disagrees with the reserved call index")
        self.current = initial

    @staticmethod
    def _validated_action(action: ActionSpec) -> tuple[GameAction, dict[str, Any]]:
        try:
            game_action = GameAction.from_name(action.name)
        except ValueError as error:
            raise CalibrationError("proposal names an unknown ARC action") from error
        parameters = dict(action.parameters)
        if game_action is GameAction.ACTION6:
            if set(parameters) != {"x", "y"}:
                raise CalibrationError("ACTION6 requires exactly x and y")
            for name in ("x", "y"):
                value = parameters[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 63
                ):
                    raise CalibrationError("ACTION6 coordinates must be integers in 0..63")
        elif parameters:
            raise CalibrationError("simple actions cannot carry parameters")
        return game_action, parameters

    def execute(self, command: ExecutionCommand) -> ExecutorObservation:
        prior = self._memo.get(command.idempotency_key)
        if prior is not None:
            return prior
        if not self._write_lock.acquire(blocking=False):
            raise CalibrationError("concurrent environment writers are forbidden")
        try:
            self._effect_started[command.idempotency_key] = False
            if (
                command.executor_id != self.executor_id
                or command.executor_version != self.executor_version
            ):
                raise CalibrationError("execution command targets another executor")
            if command.executor_artifact_ref != self.executor_artifact_ref:
                raise CalibrationError("execution command targets another executor artifact")
            game_action, parameters = self._validated_action(command.action)
            legal = self.current.evidence.available_action_names
            if command.action.name not in legal:
                raise CalibrationError("action is outside the current legal aperture")
            if (
                self.current.raw.state is GameState.GAME_OVER
                and game_action is not GameAction.RESET
            ):
                raise CalibrationError("GAME_OVER permits only RESET")
            try:
                call_index = self.budget.reserve(command.action.name)
            except BudgetExceeded as error:
                self._known_budget_denials[command.idempotency_key] = str(error)
                raise
            self._effect_started[command.idempotency_key] = True
            raw_after = self._port.step(game_action, parameters)
            captured = self.trace.capture(
                raw_after,
                occurrence_id=f"call-{call_index:05d}",
                call_index=call_index,
            )
            observation = ExecutorObservation(
                evidence_ref=EvidenceRef(
                    kind="arcengine.FrameDataRaw",
                    sha256=captured.evidence.raw_ref,
                    locator=captured.evidence.raw_relative_path,
                ),
                raw_after=captured,
            )
            self.current = captured
            self._memo[command.idempotency_key] = observation
            return observation
        finally:
            self._write_lock.release()

    def effect_started(self, idempotency_key: str) -> bool | None:
        return self._effect_started.get(idempotency_key)

    def known_no_effect_budget_denial(self, idempotency_key: str) -> str | None:
        if self._effect_started.get(idempotency_key) is not False:
            return None
        return self._known_budget_denials.get(idempotency_key)

    def recording_artifacts(self) -> tuple[ArtifactPointer, ...]:
        return tuple(artifact_pointer(path) for path in self._port.recording_paths())


class LocalControlProtocol:
    """Length-prefixed, replay-guarded local protocol for a context-isolated selector."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        max_payload_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_payload = max_payload_bytes
        self._replay = ReplayGuard()

    def send(self, *, message_id: str, kind: str, payload: object) -> None:
        _safe_component(message_id, "outgoing message ID")
        write_frame(
            self._writer,
            {"kind": kind, "message_id": message_id, "payload": payload},
            max_payload_bytes=self._max_payload,
        )
        self._writer.flush()

    def _receive(self, expected_kind: str) -> dict[str, object]:
        decoded = read_identified_frame(
            self._reader,
            self._replay,
            max_payload_bytes=self._max_payload,
        )
        value = deep_thaw_json(decoded.value)
        if not isinstance(value, dict) or value.get("kind") != expected_kind:
            raise CalibrationError(f"expected one {expected_kind} control frame")
        payload = value.get("payload")
        if not isinstance(payload, dict):
            raise CalibrationError("control frame payload must be an object")
        _reject_private_reasoning(payload)
        return cast(dict[str, object], payload)

    def receive_proposal(self) -> ProposalDraft:
        return ProposalDraft.model_validate(self._receive("proposal_draft"))

    def receive_assessment(self) -> AssessmentDraft:
        return AssessmentDraft.model_validate(self._receive("assessment_draft"))


def dependency_versions() -> tuple[str, str]:
    import importlib.metadata

    return (
        importlib.metadata.version("arc-agi"),
        importlib.metadata.version("arcengine"),
    )


def verify_dependency_versions() -> None:
    if sys.version_info[:2] != (3, 12):
        raise CalibrationError("runtime must be Python 3.12")
    arc_agi_version, arcengine_version = dependency_versions()
    if (arc_agi_version, arcengine_version) != ("0.9.9", "0.9.3"):
        raise CalibrationError(
            "installed ARC dependencies do not match arc-agi==0.9.9/arcengine==0.9.3"
        )
