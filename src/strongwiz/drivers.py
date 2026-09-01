"""Replaceable model, capability, domain, and execution interfaces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from strongwiz.contracts import (
    ActionSpec,
    CandidateProposal,
    ContractModel,
    EvidenceRef,
    Observation,
    Outcome,
    ReasoningRequest,
)


class TerminalAuthority(StrEnum):
    CONTINUE = "continue"
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"


DriverT = TypeVar("DriverT")


@runtime_checkable
class ModelDriver(Protocol):
    """A model proposes; it never supplies control state or executes actions."""

    @property
    def driver_id(self) -> str: ...

    @property
    def driver_version(self) -> str: ...

    @property
    def driver_artifact_ref(self) -> str: ...

    def propose(self, request: ReasoningRequest) -> Sequence[CandidateProposal]: ...


@runtime_checkable
class DomainAdapter(Protocol):
    """A domain translates raw state without changing the reasoning contract."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    @property
    def adapter_artifact_ref(self) -> str: ...

    def normalize_observation(self, raw: object) -> Observation: ...

    def available_actions(self, observation: Observation) -> Sequence[ActionSpec]: ...

    def extract_outcome(
        self, before: Observation, action: ActionSpec, raw_after: object
    ) -> Outcome: ...

    def terminal_authority(self, observation: Observation) -> TerminalAuthority: ...


class ExecutionCommand(ContractModel):
    """Evidence-bound writer input; it carries no authority by itself."""

    invocation_id: str
    idempotency_key: str
    grant_ref: str
    admission_ref: str
    proposal_ref: str
    action_ref: str
    action: ActionSpec
    executor_id: str
    executor_version: str
    executor_artifact_ref: str
    non_authorizing: bool = True
    authority: str = "NONE"

    def model_post_init(self, _context: object) -> None:
        required = (
            self.invocation_id,
            self.idempotency_key,
            self.grant_ref,
            self.admission_ref,
            self.proposal_ref,
            self.action_ref,
            self.executor_id,
            self.executor_version,
            self.executor_artifact_ref,
        )
        if not all(value.strip() for value in required):
            raise ValueError("execution command must bind the admitted occurrence")
        if self.action_ref != self.action.digest:
            raise ValueError("execution command action content disagrees with its reference")
        if not self.non_authorizing or self.authority != "NONE":
            raise ValueError("execution commands cannot manufacture authority")


@runtime_checkable
class ActionExecutor(Protocol):
    """A control-selected single writer called only through ExecutionCoordinator."""

    @property
    def executor_id(self) -> str: ...

    @property
    def executor_version(self) -> str: ...

    @property
    def executor_artifact_ref(self) -> str: ...

    def execute(self, command: ExecutionCommand) -> ExecutorObservation: ...


@dataclass(frozen=True, slots=True)
class ExecutorObservation:
    """Raw post-action input paired with the executor's immutable evidence identity."""

    evidence_ref: EvidenceRef
    raw_after: object


@runtime_checkable
class ReasoningCapability(Protocol):
    """Optional model-neutral subsystem contributing proposal-side evidence."""

    @property
    def capability_id(self) -> str: ...

    def evidence_refs(self, request: ReasoningRequest) -> Sequence[str]: ...


class DriverRegistry:
    """Small local registry; provider packages remain independently replaceable."""

    def __init__(self) -> None:
        self._models: dict[str, ModelDriver] = {}
        self._domains: dict[str, DomainAdapter] = {}
        self._capabilities: dict[str, ReasoningCapability] = {}

    @staticmethod
    def _register(table: dict[str, DriverT], identity: str, value: DriverT) -> None:
        if not identity:
            raise ValueError("driver identity must be non-empty")
        current = table.get(identity)
        if current is not None and current is not value:
            raise ValueError(f"driver identity already registered: {identity}")
        table[identity] = value

    def register_model(self, driver: ModelDriver) -> None:
        self._register(self._models, driver.driver_id, driver)

    def register_domain(self, adapter: DomainAdapter) -> None:
        self._register(self._domains, adapter.adapter_id, adapter)

    def register_capability(self, capability: ReasoningCapability) -> None:
        self._register(self._capabilities, capability.capability_id, capability)

    def model(self, driver_id: str) -> ModelDriver:
        try:
            return self._models[driver_id]
        except KeyError as error:
            raise KeyError(f"unknown model driver: {driver_id}") from error

    def domain(self, adapter_id: str) -> DomainAdapter:
        try:
            return self._domains[adapter_id]
        except KeyError as error:
            raise KeyError(f"unknown domain adapter: {adapter_id}") from error

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._domains))

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))
