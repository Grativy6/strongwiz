"""FBT-informed, model-neutral continuation and causal splice tools.

Working state and cached context are evidence-bearing components.  Neither is
the current goal, a grant, or authority.  Post-successor outcomes are treated
as re-entangled rather than component-specific attribution.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt


class FeedbackError(ValueError):
    pass


class ComponentSource(StrEnum):
    RECIPIENT = "recipient"
    DONOR = "donor"


class ContinuationState(ContractModel):
    schema_id: str = Field(default="strongwiz.continuation-state.v1", alias="schema")
    producer_id: str
    producer_version: str
    domain_epoch: NonNegativeInt
    goal_epoch: NonNegativeInt
    observation_ref: str
    authoritative_state_ref: str
    explicit_working_state_ref: str
    cached_context_refs: tuple[str, ...]
    branch_id: str
    parent_state_ref: str | None = None
    causal_reach_steps: NonNegativeInt
    overwrite_horizon_steps: PositiveInt
    bottleneck_description: str

    @model_validator(mode="after")
    def validate_state(self) -> ContinuationState:
        required = (
            self.producer_id,
            self.producer_version,
            self.observation_ref,
            self.authoritative_state_ref,
            self.explicit_working_state_ref,
            self.branch_id,
            self.bottleneck_description,
        )
        if not all(value.strip() for value in required):
            raise ValueError(
                "continuation identity, components, and horizon audit are required"
            )
        return self

    @property
    def state_ref(self) -> str:
        return self.digest


class StructuralHorizonAudit(ContractModel):
    mechanism_ref: str
    causal_reach_steps: NonNegativeInt
    overwrite_horizon_steps: PositiveInt
    forced_convergence: bool
    convergence_reason: str | None = None
    valid_measurement_start: NonNegativeInt
    valid_measurement_end: NonNegativeInt
    information_bottleneck: str

    @model_validator(mode="after")
    def validate_horizon(self) -> StructuralHorizonAudit:
        if self.valid_measurement_end < self.valid_measurement_start:
            raise ValueError("measurement window is inverted")
        if self.forced_convergence and not self.convergence_reason:
            raise ValueError("forced convergence requires its structural reason")
        if not self.mechanism_ref or not self.information_bottleneck:
            raise ValueError("horizon audit requires mechanism and bottleneck")
        return self


class SpliceCell(ContractModel):
    cell_id: str
    working_state_source: ComponentSource
    cached_context_source: ComponentSource
    working_state_ref: str
    cached_context_refs: tuple[str, ...]
    interpretation_limit: str = (
        "component attribution applies to the immediate measured successor only"
    )


class SpliceMatrix(ContractModel):
    recipient_state_ref: str
    donor_state_ref: str
    cells: tuple[SpliceCell, ...]

    @model_validator(mode="after")
    def validate_matrix(self) -> SpliceMatrix:
        identities = {cell.cell_id for cell in self.cells}
        if identities != {"00", "01", "10", "11"} or len(self.cells) != 4:
            raise ValueError("causal splice matrix must contain exact 00/01/10/11 cells")
        return self


class ContinuationStore:
    """Exact version/epoch cache with branch-safe immutable snapshots."""

    def __init__(self) -> None:
        self._states: dict[str, ContinuationState] = {}
        self._invalid: dict[str, str] = {}

    def put(self, state: ContinuationState) -> str:
        current = self._states.get(state.state_ref)
        if current is not None and current != state:
            raise FeedbackError("continuation identity cannot be rewritten")
        self._states[state.state_ref] = state
        return state.state_ref

    def get(
        self,
        state_ref: str,
        *,
        producer_id: str,
        producer_version: str,
        domain_epoch: int,
        goal_epoch: int,
    ) -> ContinuationState:
        try:
            state = self._states[state_ref]
        except KeyError as error:
            raise FeedbackError("unknown continuation state") from error
        if state_ref in self._invalid:
            raise FeedbackError(f"continuation invalidated: {self._invalid[state_ref]}")
        expected = (producer_id, producer_version, domain_epoch, goal_epoch)
        actual = (
            state.producer_id,
            state.producer_version,
            state.domain_epoch,
            state.goal_epoch,
        )
        if actual != expected:
            raise FeedbackError("continuation producer or epoch binding is stale")
        return state

    def fork(self, state_ref: str, *, branch_id: str) -> ContinuationState:
        if not branch_id:
            raise FeedbackError("counterfactual branch requires identity")
        try:
            state = self._states[state_ref]
        except KeyError as error:
            raise FeedbackError("unknown continuation state") from error
        if state_ref in self._invalid:
            raise FeedbackError(f"continuation invalidated: {self._invalid[state_ref]}")
        forked = state.model_copy(
            update={"branch_id": branch_id, "parent_state_ref": state_ref}
        )
        self.put(forked)
        return forked

    def invalidate_producer(self, producer_id: str, *, reason: str) -> tuple[str, ...]:
        invalidated = tuple(
            sorted(
                state_ref
                for state_ref, state in self._states.items()
                if state.producer_id == producer_id and state_ref not in self._invalid
            )
        )
        for state_ref in invalidated:
            self._invalid[state_ref] = reason
        return invalidated


def build_splice_matrix(recipient: ContinuationState, donor: ContinuationState) -> SpliceMatrix:
    """Build the generic two-factor 00/01/10/11 causal audit."""

    if recipient.producer_id != donor.producer_id:
        raise FeedbackError("splice states must use the same producer interface")
    cells: list[SpliceCell] = []
    for working_source, cache_source, cell_id in (
        (ComponentSource.RECIPIENT, ComponentSource.RECIPIENT, "00"),
        (ComponentSource.RECIPIENT, ComponentSource.DONOR, "01"),
        (ComponentSource.DONOR, ComponentSource.RECIPIENT, "10"),
        (ComponentSource.DONOR, ComponentSource.DONOR, "11"),
    ):
        working = recipient if working_source is ComponentSource.RECIPIENT else donor
        cached = recipient if cache_source is ComponentSource.RECIPIENT else donor
        cells.append(
            SpliceCell(
                cell_id=cell_id,
                working_state_source=working_source,
                cached_context_source=cache_source,
                working_state_ref=working.explicit_working_state_ref,
                cached_context_refs=cached.cached_context_refs,
            )
        )
    return SpliceMatrix(
        recipient_state_ref=recipient.state_ref,
        donor_state_ref=donor.state_ref,
        cells=tuple(cells),
    )


def interaction_contrast(*, y00: int, y01: int, y10: int, y11: int) -> int:
    """Return the two-factor interaction: y11 - y10 - y01 + y00."""

    return y11 - y10 - y01 + y00
