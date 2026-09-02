"""Executable PAL v2.3 boundary, transport, cadence, and budget profiles.

This module implements a deliberately narrow Strongwiz adapter for the PAL
v2.3 additions used by the v3 laboratory.  It does not claim package-wide PAL
conformance and it does not turn PAL receipts into execution authority.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from strongwiz.canonical import content_hash
from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt

PAL23_ADAPTER_SCHEMA = "strongwiz.pal23-adapter.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Pal23Error(ValueError):
    """A PAL v2.3 adapter invariant failed closed."""


class BoundaryRole(StrEnum):
    """Roles kept noncoercible by PAL v2.3 SC-21."""

    CUT = "cut"
    CHAIN = "chain"
    INTERFACE = "interface"
    TOPOLOGICAL = "topological"
    CONSTRAINT = "constraint"
    SCOPE = "scope"
    TRANSPORT_VALIDITY = "transport_validity"


class TransportProfile(StrEnum):
    BOUNDARY_ADAPTER = "boundary_adapter"
    CHECKPOINT_FREEZE = "checkpoint_freeze"
    CHECKPOINT_THAW = "checkpoint_thaw"
    HEARTBEAT_STUTTER = "heartbeat_stutter"
    REENTRY = "reentry"


class TransitionClass(StrEnum):
    ADMINISTRATIVE = "administrative"
    PRODUCTIVE = "productive"


class ThawStatus(StrEnum):
    EXACT = "exact"
    TRANSPORT_BREAK = "transport_break"


class CheckpointCoordinate(StrEnum):
    """Non-work coordinates that PAL v2.3 requires a thaw to revalidate."""

    CURSOR = "cursor"
    COMPARATOR = "comparator"
    SCHEDULE = "schedule"
    CODE = "code"
    DEPENDENCIES = "dependencies"
    ENVIRONMENT = "environment"
    INVARIANT = "invariant"
    GRANT_EPOCH = "grant_epoch"
    RESOURCE_LEDGER = "resource_ledger"
    AUTHORITY_CEILING = "authority_ceiling"
    AUDIT_STATE = "audit_state"
    RESIDUALS = "residuals"
    TRACE_ANCHOR = "trace_anchor"
    EXTERNAL_EFFECT_BOUNDARY = "external_effect_boundary"


class RevalidationDisposition(StrEnum):
    """Result of comparing one frozen non-work coordinate at thaw."""

    SAME = "same"
    ADMISSIBLE_CHANGE = "admissible_change"
    MATERIAL_BREAK = "material_break"


_EXACT_CHECKPOINT_COORDINATES = frozenset(
    {
        CheckpointCoordinate.CURSOR,
        CheckpointCoordinate.COMPARATOR,
        CheckpointCoordinate.SCHEDULE,
        CheckpointCoordinate.CODE,
        CheckpointCoordinate.DEPENDENCIES,
        CheckpointCoordinate.ENVIRONMENT,
        CheckpointCoordinate.INVARIANT,
        CheckpointCoordinate.EXTERNAL_EFFECT_BOUNDARY,
    }
)


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_text(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not values or any(not value.strip() for value in values):
        raise ValueError(f"{label} must contain nonempty entries")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _cost_leq(left: CostVector, right: CostVector) -> bool:
    return all(getattr(left, name) <= getattr(right, name) for name in CostVector.model_fields)


class BoundaryRef(ContractModel):
    """A role-typed boundary reference; a shared label supplies no coercion."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    boundary_id: str
    role: BoundaryRole
    carrier_or_domain: str
    scope: str
    orientation_or_coefficients_or_na: str
    resolution_or_admissible_set_or_na: str
    provenance_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_boundary(self) -> BoundaryRef:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        text = (
            self.boundary_id,
            self.carrier_or_domain,
            self.scope,
            self.orientation_or_coefficients_or_na,
            self.resolution_or_admissible_set_or_na,
        )
        if any(not value.strip() for value in text):
            raise ValueError("a boundary must declare identity, carrier, scope, and qualifiers")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("boundary provenance references must be sorted and unique")
        if not self.provenance_refs:
            raise ValueError("a boundary requires provenance")
        for value in self.provenance_refs:
            _require_digest(value, "boundary provenance reference")
        return self


class BoundaryAdapter(ContractModel):
    """A named cross-role translation with explicit preservation and loss."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    adapter_id: str
    source: BoundaryRef
    target: BoundaryRef
    hypotheses: tuple[str, ...]
    preserved_data: tuple[str, ...]
    lost_data: tuple[str, ...] = ()
    lossless: bool = False
    evidence_refs: tuple[str, ...]
    authority_ceiling: str
    reopening_condition: str
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_adapter(self) -> BoundaryAdapter:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not all(
            value.strip()
            for value in (self.adapter_id, self.authority_ceiling, self.reopening_condition)
        ):
            raise ValueError("adapter identity, ceiling, and reopening condition are required")
        if self.source.digest == self.target.digest:
            raise ValueError("an adapter must cross two distinct declared boundaries")
        _require_text(self.hypotheses, "adapter hypotheses")
        _require_text(self.preserved_data, "adapter preserved data")
        if self.lost_data != tuple(sorted(set(self.lost_data))):
            raise ValueError("adapter lost-data entries must be sorted and unique")
        if any(not value.strip() for value in self.lost_data):
            raise ValueError("adapter lost-data entries cannot be blank")
        if self.lossless == bool(self.lost_data):
            raise ValueError("lossless must be true exactly when no lost data are declared")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("adapter evidence references must be sorted and unique")
        if not self.evidence_refs:
            raise ValueError("a boundary adapter requires evidence")
        for value in self.evidence_refs:
            _require_digest(value, "adapter evidence reference")
        return self


class StateProjection(ContractModel):
    """The exact state coordinates compared by an equality or stutter claim."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    projection_id: str
    state_space: str
    included_coordinates: tuple[str, ...]
    excluded_coordinates: tuple[str, ...]
    comparator: str
    provenance_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_projection(self) -> StateProjection:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not all(
            value.strip() for value in (self.projection_id, self.state_space, self.comparator)
        ):
            raise ValueError("projection identity, state space, and comparator are required")
        _require_text(self.included_coordinates, "included projection coordinates")
        if self.excluded_coordinates != tuple(sorted(set(self.excluded_coordinates))):
            raise ValueError("excluded projection coordinates must be sorted and unique")
        if set(self.included_coordinates) & set(self.excluded_coordinates):
            raise ValueError("a projection coordinate cannot be both included and excluded")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("projection provenance references must be sorted and unique")
        if not self.provenance_refs:
            raise ValueError("a projection requires provenance")
        for value in self.provenance_refs:
            _require_digest(value, "projection provenance reference")
        return self


class GrantEpoch(ContractModel):
    """One immutable resource epoch; a top-up appends rather than rewrites."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    epoch_id: str
    ordinal: NonNegativeInt
    grant_ref: str
    predecessor_epoch_ref: str | None = None
    top_up_evidence_ref: str | None = None
    budget: CostVector
    cumulative_consumption: CostVector
    authority_ceiling: str

    @model_validator(mode="after")
    def validate_epoch(self) -> GrantEpoch:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not self.epoch_id.strip() or not self.authority_ceiling.strip():
            raise ValueError("grant epoch identity and authority ceiling are required")
        _require_digest(self.grant_ref, "grant reference")
        if not _cost_leq(self.cumulative_consumption, self.budget):
            raise ValueError("grant-epoch consumption exceeds its immutable budget")
        if self.ordinal == 0:
            if self.predecessor_epoch_ref is not None or self.top_up_evidence_ref is not None:
                raise ValueError("genesis grant epoch cannot claim a predecessor or top-up")
        else:
            if self.predecessor_epoch_ref is None or self.top_up_evidence_ref is None:
                raise ValueError(
                    "a successor grant epoch requires predecessor and top-up evidence"
                )
            _require_digest(self.predecessor_epoch_ref, "predecessor grant epoch")
            _require_digest(self.top_up_evidence_ref, "grant top-up evidence")
        return self

    @property
    def slack(self) -> CostVector:
        """Return B-c for this epoch without carrying anything into another epoch."""

        return self.budget.subtract_floor_zero(self.cumulative_consumption)


class CadenceTransition(ContractModel):
    """Separate administrative stutter from a productive macrostep."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    transition_id: str
    transition_class: TransitionClass
    work_projection_ref: str
    work_before_ref: str
    work_after_ref: str
    progress_coordinate: str
    progress_before_ref: str
    progress_after_ref: str
    audit_before_ref: str
    audit_after_ref: str
    grant_epoch_ref: str
    cumulative_before: CostVector
    cumulative_after: CostVector
    heartbeat_namespace: str | None = None
    evidence_refs: tuple[str, ...]
    counts_as_progress: bool
    claim_ceiling: str = "declared projected transition only"
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_transition(self) -> CadenceTransition:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not all(
            value.strip()
            for value in (self.transition_id, self.progress_coordinate, self.claim_ceiling)
        ):
            raise ValueError("cadence identity, progress coordinate, and ceiling are required")
        for value in (
            self.work_projection_ref,
            self.work_before_ref,
            self.work_after_ref,
            self.progress_before_ref,
            self.progress_after_ref,
            self.audit_before_ref,
            self.audit_after_ref,
            self.grant_epoch_ref,
            *self.evidence_refs,
        ):
            _require_digest(value, "cadence reference")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("cadence evidence references must be sorted and unique")
        if not self.evidence_refs:
            raise ValueError("a cadence transition requires evidence")
        if not _cost_leq(self.cumulative_before, self.cumulative_after):
            raise ValueError("cumulative resources cannot decrease inside a grant epoch")
        if self.transition_class is TransitionClass.ADMINISTRATIVE:
            if self.work_before_ref != self.work_after_ref:
                raise ValueError("administrative transition changed declared work state")
            if self.progress_before_ref != self.progress_after_ref or self.counts_as_progress:
                raise ValueError("administrative transition cannot claim productive progress")
            if self.heartbeat_namespace is None or not self.heartbeat_namespace.strip():
                raise ValueError("administrative heartbeat requires a namespace")
        else:
            if (
                self.progress_before_ref == self.progress_after_ref
                or not self.counts_as_progress
            ):
                raise ValueError(
                    "productive transition must change its declared progress coordinate"
                )
            if self.heartbeat_namespace is not None:
                raise ValueError("productive transitions are not heartbeat namespace events")
        return self


class CheckpointCapsule(ContractModel):
    """Complete declared work-state capsule for exact freeze/thaw checking."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    capsule_id: str
    work_projection_ref: str
    work_state_ref: str
    cursor_ref: str
    comparator_ref: str
    schedule_ref: str
    code_ref: str
    dependencies_ref: str
    environment_ref: str
    invariant_ref: str
    grant_epoch_ref: str
    resource_ledger_ref: str
    authority_ceiling: str
    audit_state_ref: str
    residual_refs: tuple[str, ...]
    trace_anchor_ref: str
    external_effect_boundary_ref: str

    @model_validator(mode="after")
    def validate_capsule(self) -> CheckpointCapsule:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not self.capsule_id.strip() or not self.authority_ceiling.strip():
            raise ValueError("checkpoint capsule identity and authority ceiling are required")
        refs = (
            self.work_projection_ref,
            self.work_state_ref,
            self.cursor_ref,
            self.comparator_ref,
            self.schedule_ref,
            self.code_ref,
            self.dependencies_ref,
            self.environment_ref,
            self.invariant_ref,
            self.grant_epoch_ref,
            self.resource_ledger_ref,
            self.audit_state_ref,
            *self.residual_refs,
            self.trace_anchor_ref,
            self.external_effect_boundary_ref,
        )
        for value in refs:
            _require_digest(value, "checkpoint capsule reference")
        if self.residual_refs != tuple(sorted(set(self.residual_refs))):
            raise ValueError("checkpoint residual references must be sorted and unique")
        return self


class CoordinateRevalidation(ContractModel):
    """Evidence-bound comparison of one non-work checkpoint coordinate."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    coordinate: CheckpointCoordinate
    frozen_ref: str
    current_ref: str
    evidence_ref: str
    disposition: RevalidationDisposition

    @model_validator(mode="after")
    def validate_revalidation(self) -> CoordinateRevalidation:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        for value in (self.frozen_ref, self.current_ref, self.evidence_ref):
            _require_digest(value, "checkpoint coordinate revalidation reference")
        same = self.frozen_ref == self.current_ref
        if same != (self.disposition is RevalidationDisposition.SAME):
            raise ValueError(
                "coordinate disposition must be SAME exactly when frozen and current refs match"
            )
        if (
            not same
            and self.coordinate in _EXACT_CHECKPOINT_COORDINATES
            and self.disposition is not RevalidationDisposition.MATERIAL_BREAK
        ):
            raise ValueError(f"changed {self.coordinate.value} is a material checkpoint break")
        return self


class CheckpointThawReceipt(ContractModel):
    """Thaw result that cannot restore spent resources or expired authority."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    thaw_id: str
    capsule: CheckpointCapsule
    capsule_ref: str
    predecessor_ref: str
    deterministic_suffix_ref: str
    work_projection_ref: str
    frozen_work_state_ref: str
    thawed_work_state_ref: str
    frozen_grant_epoch_ref: str
    current_grant_epoch_ref: str
    frozen_resource_ledger_ref: str
    current_resource_ledger_ref: str
    frozen_authority_ceiling: str
    current_authority_ceiling: str
    coordinate_revalidations: tuple[CoordinateRevalidation, ...]
    transport_break_reasons: tuple[str, ...] = ()
    status: ThawStatus
    reentry_required: bool
    renews_grant: Literal[False] = False
    restores_resources: Literal[False] = False
    expands_authority: Literal[False] = False
    claim_ceiling: str = "declared terminal work-state continuity only"

    @model_validator(mode="after")
    def validate_thaw(self) -> CheckpointThawReceipt:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not self.thaw_id.strip() or not self.claim_ceiling.strip():
            raise ValueError("thaw identity and claim ceiling are required")
        if (
            not self.frozen_authority_ceiling.strip()
            or not self.current_authority_ceiling.strip()
        ):
            raise ValueError("thaw requires frozen and current authority ceilings")
        for value in (
            self.capsule_ref,
            self.predecessor_ref,
            self.deterministic_suffix_ref,
            self.work_projection_ref,
            self.frozen_work_state_ref,
            self.thawed_work_state_ref,
            self.frozen_grant_epoch_ref,
            self.current_grant_epoch_ref,
            self.frozen_resource_ledger_ref,
            self.current_resource_ledger_ref,
        ):
            _require_digest(value, "checkpoint thaw reference")
        if self.capsule_ref != self.capsule.digest:
            raise ValueError("checkpoint thaw does not bind its supplied capsule")
        if (
            self.work_projection_ref != self.capsule.work_projection_ref
            or self.frozen_work_state_ref != self.capsule.work_state_ref
            or self.frozen_grant_epoch_ref != self.capsule.grant_epoch_ref
            or self.frozen_resource_ledger_ref != self.capsule.resource_ledger_ref
            or self.frozen_authority_ceiling != self.capsule.authority_ceiling
        ):
            raise ValueError("checkpoint thaw changed a frozen capsule coordinate")
        coordinates = tuple(item.coordinate for item in self.coordinate_revalidations)
        required = tuple(CheckpointCoordinate)
        if coordinates != tuple(sorted(required, key=str)):
            raise ValueError(
                "thaw must revalidate every required non-work coordinate exactly once"
            )
        frozen_refs = {
            CheckpointCoordinate.CURSOR: self.capsule.cursor_ref,
            CheckpointCoordinate.COMPARATOR: self.capsule.comparator_ref,
            CheckpointCoordinate.SCHEDULE: self.capsule.schedule_ref,
            CheckpointCoordinate.CODE: self.capsule.code_ref,
            CheckpointCoordinate.DEPENDENCIES: self.capsule.dependencies_ref,
            CheckpointCoordinate.ENVIRONMENT: self.capsule.environment_ref,
            CheckpointCoordinate.INVARIANT: self.capsule.invariant_ref,
            CheckpointCoordinate.GRANT_EPOCH: self.capsule.grant_epoch_ref,
            CheckpointCoordinate.RESOURCE_LEDGER: self.capsule.resource_ledger_ref,
            CheckpointCoordinate.AUTHORITY_CEILING: content_hash(
                {"authority_ceiling": self.capsule.authority_ceiling}
            ),
            CheckpointCoordinate.AUDIT_STATE: self.capsule.audit_state_ref,
            CheckpointCoordinate.RESIDUALS: content_hash(
                {"residual_refs": self.capsule.residual_refs}
            ),
            CheckpointCoordinate.TRACE_ANCHOR: self.capsule.trace_anchor_ref,
            CheckpointCoordinate.EXTERNAL_EFFECT_BOUNDARY: (
                self.capsule.external_effect_boundary_ref
            ),
        }
        for item in self.coordinate_revalidations:
            if item.frozen_ref != frozen_refs[item.coordinate]:
                raise ValueError(
                    f"revalidation changed frozen {item.coordinate.value} coordinate"
                )
        current_refs = {
            CheckpointCoordinate.GRANT_EPOCH: self.current_grant_epoch_ref,
            CheckpointCoordinate.RESOURCE_LEDGER: self.current_resource_ledger_ref,
            CheckpointCoordinate.AUTHORITY_CEILING: content_hash(
                {"authority_ceiling": self.current_authority_ceiling}
            ),
        }
        for coordinate, current_ref in current_refs.items():
            item = next(
                value
                for value in self.coordinate_revalidations
                if value.coordinate is coordinate
            )
            if item.current_ref != current_ref:
                raise ValueError(
                    f"revalidation does not bind current {coordinate.value} coordinate"
                )
        if self.transport_break_reasons != tuple(sorted(set(self.transport_break_reasons))):
            raise ValueError("transport-break reasons must be sorted and unique")
        material_break = any(
            item.disposition is RevalidationDisposition.MATERIAL_BREAK
            for item in self.coordinate_revalidations
        )
        exact = self.status is ThawStatus.EXACT
        if exact:
            if (
                self.frozen_work_state_ref != self.thawed_work_state_ref
                or self.transport_break_reasons
                or self.reentry_required
                or material_break
            ):
                raise ValueError("exact thaw requires equal work state and no transport break")
        elif (
            not self.transport_break_reasons
            or not self.reentry_required
            or (self.frozen_work_state_ref == self.thawed_work_state_ref and not material_break)
        ):
            raise ValueError(
                "transport break must preserve a material mismatch, reasons, and re-entry"
            )
        return self


class TransportReceipt(ContractModel):
    """Shared native profile fields used at PAL v2.3 transport boundaries."""

    schema_id: str = Field(default=PAL23_ADAPTER_SCHEMA, alias="schema")
    receipt_id: str
    profile: TransportProfile
    predecessor_ref: str
    work_projection_ref: str
    source_work_state_ref: str
    target_work_state_ref: str
    grant_epoch_ref: str
    resource_ledger_ref: str
    trace_anchor_ref: str
    authority_ceiling: str
    residual_refs: tuple[str, ...]
    reopening_condition: str
    capsule: CheckpointCapsule | None = None
    capsule_ref: str | None = None
    transport_break_ref: str | None = None
    transfers_authority: Literal[False] = False
    renews_grant: Literal[False] = False
    restores_resources: Literal[False] = False

    @model_validator(mode="after")
    def validate_transport(self) -> TransportReceipt:
        if self.schema_id != PAL23_ADAPTER_SCHEMA:
            raise ValueError("unsupported PAL v2.3 adapter schema")
        if not all(
            value.strip()
            for value in (self.receipt_id, self.authority_ceiling, self.reopening_condition)
        ):
            raise ValueError(
                "transport identity, ceiling, and reopening condition are required"
            )
        for value in (
            self.predecessor_ref,
            self.work_projection_ref,
            self.source_work_state_ref,
            self.target_work_state_ref,
            self.grant_epoch_ref,
            self.resource_ledger_ref,
            self.trace_anchor_ref,
            *self.residual_refs,
        ):
            _require_digest(value, "transport reference")
        if self.residual_refs != tuple(sorted(set(self.residual_refs))):
            raise ValueError("transport residual references must be sorted and unique")
        checkpoint_profile = self.profile in {
            TransportProfile.CHECKPOINT_FREEZE,
            TransportProfile.CHECKPOINT_THAW,
        }
        has_complete_capsule = self.capsule is not None and self.capsule_ref is not None
        if checkpoint_profile != has_complete_capsule:
            raise ValueError(
                "checkpoint profiles require an embedded checkpoint capsule and its reference"
            )
        if (self.capsule is None) != (self.capsule_ref is None):
            raise ValueError(
                "checkpoint capsule and checkpoint capsule reference must appear together"
            )
        if self.capsule is not None and self.capsule_ref is not None:
            _require_digest(self.capsule_ref, "transport capsule reference")
            if self.capsule_ref != self.capsule.digest:
                raise ValueError("checkpoint transport does not bind its supplied capsule")
            if self.work_projection_ref != self.capsule.work_projection_ref:
                raise ValueError("checkpoint transport changed the capsule work projection")
            if (
                self.source_work_state_ref != self.capsule.work_state_ref
                or self.target_work_state_ref != self.capsule.work_state_ref
            ):
                if self.profile is TransportProfile.CHECKPOINT_THAW:
                    raise ValueError(
                        "checkpoint thaw transport work state must equal the supplied "
                        "capsule work state; use a transport break and re-entry profile "
                        "for mismatches"
                    )
                raise ValueError(
                    "checkpoint freeze transport work state must equal the supplied "
                    "capsule work state"
                )
        reentry = self.profile is TransportProfile.REENTRY
        if reentry != (self.transport_break_ref is not None):
            raise ValueError("only re-entry profiles bind a transport break")
        if self.transport_break_ref is not None:
            _require_digest(self.transport_break_ref, "transport break reference")
        if (
            self.profile is TransportProfile.HEARTBEAT_STUTTER
            and self.source_work_state_ref != self.target_work_state_ref
        ):
            raise ValueError("heartbeat stutter must preserve the declared work projection")
        if (
            self.profile is TransportProfile.CHECKPOINT_THAW
            and self.source_work_state_ref != self.target_work_state_ref
        ):
            raise ValueError(
                "checkpoint thaw transport must preserve work or use a transport break "
                "and re-entry profile"
            )
        return self


def pal23_schema_bundle() -> dict[str, object]:
    """Return the bounded Strongwiz PAL v2.3 adapter schemas."""

    return {
        "schema": PAL23_ADAPTER_SCHEMA,
        "claim_ceiling": "targeted Strongwiz adapter; not package-wide PAL conformance",
        "schemas": {
            "boundary_adapter": BoundaryAdapter.model_json_schema(),
            "boundary_ref": BoundaryRef.model_json_schema(),
            "cadence_transition": CadenceTransition.model_json_schema(),
            "checkpoint_capsule": CheckpointCapsule.model_json_schema(),
            "checkpoint_coordinate_revalidation": CoordinateRevalidation.model_json_schema(),
            "checkpoint_thaw": CheckpointThawReceipt.model_json_schema(),
            "grant_epoch": GrantEpoch.model_json_schema(),
            "state_projection": StateProjection.model_json_schema(),
            "transport_receipt": TransportReceipt.model_json_schema(),
        },
    }
