"""General consequence factoring, residual localization, and mechanic revision."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, cast

from pydantic import AfterValidator, PlainSerializer, model_validator

from strongwiz.canonical import ImmutableJSONValue, canonical_text, content_hash
from strongwiz.contracts import ContractModel, HypothesisStatus, NonNegativeInt


class LearningError(ValueError):
    pass


class KnowledgeState(StrEnum):
    UNKNOWN = "unknown"
    KNOWN = "known"


class ResidualKind(StrEnum):
    MISMATCH = "mismatch"
    MISSING_EXPECTED = "missing_expected"
    UNEXPECTED = "unexpected"
    UNOBSERVED = "unobserved"


class RepairScope(StrEnum):
    LOCAL_COMPONENT = "local_component"
    DEPENDENCY = "dependency"
    SCOPED_MODEL = "scoped_model"
    GLOBAL_MODEL = "global_model"


def _freeze_component_map(
    value: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    return cast(dict[str, tuple[str, ...]], MappingProxyType(dict(value)))


def _thaw_component_map(
    value: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    return dict(value)


ComponentMap = Annotated[
    dict[str, tuple[str, ...]],
    AfterValidator(_freeze_component_map),
    PlainSerializer(
        _thaw_component_map,
        return_type=dict[str, tuple[str, ...]],
    ),
]


class ConsequenceSchema(ContractModel):
    schema_id: str
    channel_names: tuple[str, ...]

    @model_validator(mode="after")
    def validate_schema(self) -> ConsequenceSchema:
        if not self.schema_id or not self.channel_names:
            raise ValueError("consequence schema requires identity and channels")
        if tuple(sorted(set(self.channel_names))) != self.channel_names:
            raise ValueError("consequence channels must be sorted and unique")
        return self


class ChannelValue(ContractModel):
    channel: str
    knowledge: KnowledgeState
    atoms: tuple[ImmutableJSONValue, ...] = ()

    @model_validator(mode="after")
    def validate_channel(self) -> ChannelValue:
        if not self.channel:
            raise ValueError("consequence channel must be non-empty")
        if self.knowledge is KnowledgeState.UNKNOWN and self.atoms:
            raise ValueError("unknown channels cannot carry asserted atoms")
        keys = [canonical_text(atom) for atom in self.atoms]
        if len(keys) != len(set(keys)):
            raise ValueError("consequence atoms must be unique")
        return self

    @classmethod
    def unknown(cls, channel: str) -> ChannelValue:
        return cls(channel=channel, knowledge=KnowledgeState.UNKNOWN)

    @classmethod
    def known(cls, channel: str, *atoms: ImmutableJSONValue) -> ChannelValue:
        ordered = tuple(
            atom for _, atom in sorted((canonical_text(atom), atom) for atom in atoms)
        )
        return cls(channel=channel, knowledge=KnowledgeState.KNOWN, atoms=ordered)


class ConsequenceVector(ContractModel):
    schema_ref: str
    channels: tuple[ChannelValue, ...]

    @model_validator(mode="after")
    def validate_vector(self) -> ConsequenceVector:
        if not self.schema_ref or not self.channels:
            raise ValueError("consequence vector requires schema and channels")
        names = tuple(channel.channel for channel in self.channels)
        if names != tuple(sorted(set(names))):
            raise ValueError("consequence vector channels must be sorted and unique")
        return self

    @classmethod
    def build(
        cls, schema: ConsequenceSchema, values: dict[str, ChannelValue]
    ) -> ConsequenceVector:
        if set(values) != set(schema.channel_names):
            raise LearningError("vectors must contain every adapter-declared channel")
        if any(name != value.channel for name, value in values.items()):
            raise LearningError("channel map keys and values disagree")
        return cls(
            schema_ref=schema.digest,
            channels=tuple(values[name] for name in schema.channel_names),
        )

    def get(self, channel: str) -> ChannelValue:
        for value in self.channels:
            if value.channel == channel:
                return value
        raise LearningError(f"unknown consequence channel: {channel}")

    def replace(self, replacements: dict[str, ChannelValue]) -> ConsequenceVector:
        unknown = set(replacements) - {value.channel for value in self.channels}
        if unknown:
            raise LearningError("replacement names a channel outside the schema")
        return ConsequenceVector(
            schema_ref=self.schema_ref,
            channels=tuple(replacements.get(value.channel, value) for value in self.channels),
        )


class PredictionResidual(ContractModel):
    residual_id: str
    channel: str
    kind: ResidualKind
    expected: ChannelValue
    observed: ChannelValue
    evidence_refs: tuple[str, ...]
    implicated_component_refs: tuple[str, ...]


class Assessment(ContractModel):
    prediction_ref: str
    observed_ref: str
    matched_channels: tuple[str, ...]
    unscored_channels: tuple[str, ...]
    residuals: tuple[PredictionResidual, ...]
    preserved_component_refs: tuple[str, ...]
    implicated_component_refs: tuple[str, ...]

    @property
    def exact_match(self) -> bool:
        return not self.residuals and not self.unscored_channels


class MechanicVersion(ContractModel):
    mechanic_id: str
    version: NonNegativeInt
    scope_id: str
    action_pattern: str
    condition_tags: tuple[str, ...]
    consequence: ConsequenceVector
    component_refs_by_channel: ComponentMap
    status: HypothesisStatus
    support_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    parent_version_ref: str | None = None
    revision_reason: str | None = None

    @model_validator(mode="after")
    def validate_mechanic(self) -> MechanicVersion:
        if not all((self.mechanic_id, self.scope_id, self.action_pattern)):
            raise ValueError("mechanic identity, scope, and action pattern are required")
        channels = {value.channel for value in self.consequence.channels}
        if set(self.component_refs_by_channel) != channels:
            raise ValueError("mechanic components must cover every consequence channel")
        if self.version == 0:
            if self.parent_version_ref is not None:
                raise ValueError("initial mechanic cannot have a parent version")
        elif not self.parent_version_ref or not self.revision_reason:
            raise ValueError("mechanic revision requires parent identity and reason")
        return self

    @property
    def version_ref(self) -> str:
        return self.digest


class RepairDecision(ContractModel):
    assessment_ref: str
    scope: RepairScope
    implicated_channels: tuple[str, ...]
    preserved_channels: tuple[str, ...]
    competing_explanations: tuple[str, ...]
    next_discriminating_test: str | None
    local_failure_count: NonNegativeInt
    widened: bool = False


def compare_consequences(
    expected: ConsequenceVector,
    observed: ConsequenceVector,
    *,
    evidence_refs: tuple[str, ...],
    component_refs_by_channel: dict[str, tuple[str, ...]],
) -> Assessment:
    if expected.schema_ref != observed.schema_ref:
        raise LearningError("cannot compare different consequence schemas")
    if not evidence_refs:
        raise LearningError("consequence assessment requires observed evidence")
    channel_names = {value.channel for value in expected.channels}
    if set(component_refs_by_channel) != channel_names:
        raise LearningError("component map must cover every expected consequence channel")
    matched: list[str] = []
    unscored: list[str] = []
    residuals: list[PredictionResidual] = []
    implicated: set[str] = set()
    preserved: set[str] = set()
    for expected_value in expected.channels:
        observed_value = observed.get(expected_value.channel)
        components = component_refs_by_channel.get(expected_value.channel, ())
        if expected_value.knowledge is KnowledgeState.UNKNOWN:
            unscored.append(expected_value.channel)
            continue
        if observed_value.knowledge is KnowledgeState.UNKNOWN:
            kind = ResidualKind.UNOBSERVED
        else:
            expected_keys = {canonical_text(atom) for atom in expected_value.atoms}
            observed_keys = {canonical_text(atom) for atom in observed_value.atoms}
            if expected_keys == observed_keys:
                matched.append(expected_value.channel)
                preserved.update(components)
                continue
            if expected_keys and not observed_keys:
                kind = ResidualKind.MISSING_EXPECTED
            elif observed_keys and not expected_keys:
                kind = ResidualKind.UNEXPECTED
            else:
                kind = ResidualKind.MISMATCH
        implicated.update(components)
        residual_id = content_hash(
            {
                "channel": expected_value.channel,
                "evidence_refs": list(evidence_refs),
                "expected": expected_value,
                "kind": kind.value,
                "observed": observed_value,
            }
        )
        residuals.append(
            PredictionResidual(
                residual_id=residual_id,
                channel=expected_value.channel,
                kind=kind,
                expected=expected_value,
                observed=observed_value,
                evidence_refs=evidence_refs,
                implicated_component_refs=components,
            )
        )
    return Assessment(
        prediction_ref=expected.digest,
        observed_ref=observed.digest,
        matched_channels=tuple(sorted(matched)),
        unscored_channels=tuple(sorted(unscored)),
        residuals=tuple(residuals),
        preserved_component_refs=tuple(sorted(preserved - implicated)),
        implicated_component_refs=tuple(sorted(implicated)),
    )


class MechanicLedger:
    """Immutable mechanic versions with implicated-only repair."""

    def __init__(self) -> None:
        self._versions: dict[str, list[MechanicVersion]] = {}

    def register(self, mechanic: MechanicVersion) -> str:
        versions = self._versions.setdefault(mechanic.mechanic_id, [])
        if versions:
            expected_version = versions[-1].version + 1
            if mechanic.version != expected_version:
                raise LearningError("mechanic versions must be contiguous")
            if mechanic.parent_version_ref != versions[-1].version_ref:
                raise LearningError("mechanic revision must bind the exact parent")
        elif mechanic.version != 0:
            raise LearningError("first mechanic version must be zero")
        versions.append(mechanic)
        return mechanic.version_ref

    def current(self, mechanic_id: str) -> MechanicVersion:
        try:
            return self._versions[mechanic_id][-1]
        except (KeyError, IndexError) as error:
            raise LearningError("unknown mechanic") from error

    def assess(
        self,
        mechanic_id: str,
        observed: ConsequenceVector,
        *,
        evidence_refs: tuple[str, ...],
    ) -> Assessment:
        mechanic = self.current(mechanic_id)
        return compare_consequences(
            mechanic.consequence,
            observed,
            evidence_refs=evidence_refs,
            component_refs_by_channel=mechanic.component_refs_by_channel,
        )

    def revise_implicated(
        self,
        mechanic_id: str,
        assessment: Assessment,
        *,
        replacements: dict[str, ChannelValue],
        evidence_refs: tuple[str, ...],
        reason: str,
    ) -> MechanicVersion:
        current = self.current(mechanic_id)
        if assessment.prediction_ref != current.consequence.digest:
            raise LearningError("repair assessment does not bind the current mechanic")
        if not evidence_refs or not reason:
            raise LearningError("repair requires evidence and a reason")
        implicated = {residual.channel for residual in assessment.residuals}
        if not implicated:
            raise LearningError("matching prediction needs support, not repair")
        if set(replacements) != implicated:
            raise LearningError("repair must change exactly the implicated channels")
        revised = MechanicVersion(
            mechanic_id=current.mechanic_id,
            version=current.version + 1,
            scope_id=current.scope_id,
            action_pattern=current.action_pattern,
            condition_tags=current.condition_tags,
            consequence=current.consequence.replace(replacements),
            component_refs_by_channel=current.component_refs_by_channel,
            status=HypothesisStatus.NARROWED,
            support_refs=current.support_refs,
            conflict_refs=tuple(sorted(set((*current.conflict_refs, *evidence_refs)))),
            parent_version_ref=current.version_ref,
            revision_reason=reason,
        )
        self.register(revised)
        return revised

    def support(
        self,
        mechanic_id: str,
        assessment: Assessment,
        *,
        evidence_refs: tuple[str, ...],
        reason: str,
    ) -> MechanicVersion:
        current = self.current(mechanic_id)
        if assessment.prediction_ref != current.consequence.digest:
            raise LearningError("support assessment does not bind the current mechanic")
        if not assessment.exact_match:
            raise LearningError("support requires a fully observed matching prediction")
        if not evidence_refs or not reason:
            raise LearningError("support requires evidence and a reason")
        revised = current.model_copy(
            update={
                "version": current.version + 1,
                "status": HypothesisStatus.SUPPORTED,
                "support_refs": tuple(sorted(set((*current.support_refs, *evidence_refs)))),
                "parent_version_ref": current.version_ref,
                "revision_reason": reason,
            }
        )
        self.register(revised)
        return revised


def choose_repair_scope(local_failure_count: int, *, escalation_after: int = 2) -> RepairScope:
    if local_failure_count < 0 or escalation_after <= 0:
        raise LearningError("repair counts and escalation boundary are invalid")
    if local_failure_count < escalation_after:
        return RepairScope.LOCAL_COMPONENT
    if local_failure_count == escalation_after:
        return RepairScope.DEPENDENCY
    return RepairScope.SCOPED_MODEL
