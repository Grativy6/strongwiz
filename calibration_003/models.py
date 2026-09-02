"""Closed contracts for the preparation-only Strongwiz v3 campaign.

The contracts bind a matched no-scribe/scribe comparison.  They do not grant
environment access or claim that a later calibration has occurred.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt, PositiveInt
from strongwiz.scribe import ScribeDriverBinding, ScribePolicy

CALIBRATION_003_SCHEMA = "strongwiz.calibration-003.v1"
V2_CARRY_PACKET_SCHEMA = "strongwiz.calibration-003-v2-carry-packet.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

_V2_CARRY_CLASSIFICATION = "same-game adaptive-successor evidence"
_V2_CARRY_CLAIM_CEILING = (
    "concise reanalysis of one public-game campaign; not fresh, unseen, independent, "
    "causal, or generalization evidence"
)
_V2_REQUIRED_EXCLUSIONS = (
    "action_sequences",
    "authority",
    "authorization",
    "domain_state",
    "frames",
    "permission",
    "private_reasoning",
    "raw_traces",
    "replay_state",
)


class Calibration003Error(ValueError):
    """A v3 preparation or verification invariant failed closed."""


class CampaignClaimLabel(StrEnum):
    FRESH_MATCHED_ABLATION = "fresh_matched_ablation"
    ADAPTIVE_SUCCESSOR = "adaptive_successor"
    REANALYSIS = "reanalysis"


class CampaignArmRole(StrEnum):
    NO_SCRIBE = "no_scribe"
    SCRIBE = "scribe"


class V2CarryFactStatus(StrEnum):
    """Closed status vocabulary retained from the Calibration 002 reanalysis."""

    SUPPORTED_BOUNDED = "supported_bounded"
    EXACT_NEGATIVE_BOUNDED = "exact_negative_bounded"
    UNRESOLVED = "unresolved"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_text(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} is required")
    return value


def _require_canonical_identity(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or value != normalized:
        raise ValueError(f"{label} must be canonical NFKC text without padding")
    return value


def _identity_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _require_sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _require_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    return values


def _require_safe_relative_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} must be a canonical relative POSIX path")
    return value


class V2CarrySourceCampaign(ContractModel):
    campaign_id: str
    game_id: str
    toolbelt_commit: str
    toolbelt_tree: str

    @model_validator(mode="after")
    def validate_source(self) -> V2CarrySourceCampaign:
        _require_text(self.campaign_id, "source campaign identity")
        _require_text(self.game_id, "source game identity")
        for label, value in (
            ("source toolbelt commit", self.toolbelt_commit),
            ("source toolbelt tree", self.toolbelt_tree),
        ):
            if _GIT_OBJECT.fullmatch(value) is None:
                raise ValueError(f"{label} must be a lowercase Git object identity")
        return self


class V2CarrySourceArtifact(ContractModel):
    path: str
    sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> V2CarrySourceArtifact:
        _require_safe_relative_path(self.path, "source artifact path")
        _require_digest(self.sha256, "source artifact digest")
        return self


class V2CarrySourceDisposition(ContractModel):
    completion_genuinely_observed: Literal[False] = False
    disposition: Literal["partial"] = "partial"
    final_state: Literal["NOT_FINISHED"] = "NOT_FINISHED"
    levels_completed: NonNegativeInt
    win_levels: PositiveInt
    non_reset_actions: NonNegativeInt
    resets: NonNegativeInt
    total_environment_calls: NonNegativeInt
    elapsed_wall_ms: NonNegativeInt

    @model_validator(mode="after")
    def validate_disposition(self) -> V2CarrySourceDisposition:
        if self.levels_completed >= self.win_levels:
            raise ValueError("partial source disposition cannot claim all win levels")
        if self.non_reset_actions + self.resets != self.total_environment_calls:
            raise ValueError("source environment-call accounting does not balance")
        return self


class V2CarryFact(ContractModel):
    fact_id: str
    status: V2CarryFactStatus
    statement: str
    scope: str
    evidence_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...]
    predecessor_fact_ids: tuple[str, ...]
    supersedes_fact_ids: tuple[str, ...]
    uncertainty: str
    reopening_condition: str

    @model_validator(mode="after")
    def validate_fact(self) -> V2CarryFact:
        if _SAFE_ID.fullmatch(self.fact_id) is None:
            raise ValueError("carry fact identity must be a safe stable identifier")
        for label, value in (
            ("carry fact statement", self.statement),
            ("carry fact scope", self.scope),
            ("carry fact uncertainty", self.uncertainty),
            ("carry fact reopening condition", self.reopening_condition),
        ):
            _require_text(value, label)
        for label, values in (
            ("carry fact evidence references", self.evidence_refs),
            ("carry fact counterevidence references", self.counterevidence_refs),
        ):
            _require_unique(values, label)
            for value in values:
                _require_digest(value, label)
        if not self.evidence_refs:
            raise ValueError("carry fact requires at least one evidence reference")
        for label, values in (
            ("carry fact predecessor identities", self.predecessor_fact_ids),
            ("carry fact superseded identities", self.supersedes_fact_ids),
        ):
            _require_unique(values, label)
            for value in values:
                if _SAFE_ID.fullmatch(value) is None:
                    raise ValueError(f"{label} must use safe stable identifiers")
                if value == self.fact_id:
                    raise ValueError("carry fact cannot reference itself")
        return self


class V2CarryConsumptionRules(ContractModel):
    fresh_generalization_arm_may_consume: Literal[False] = False
    same_game_adaptive_successor_may_consume_after_target_bound_review: Literal[True] = True
    target_stage_binding_required: Literal[True] = True
    source_campaign_remains_immutable: Literal[True] = True
    transfers_authority: Literal[False] = False


class V2CarryPacket(ContractModel):
    """Closed, source-bound Calibration 002 carry packet contract."""

    schema_id: str = Field(default=V2_CARRY_PACKET_SCHEMA, alias="schema")
    packet_id: str
    classification: Literal["same-game adaptive-successor evidence"]
    claim_ceiling: Literal[
        "concise reanalysis of one public-game campaign; not fresh, unseen, independent, "
        "causal, or generalization evidence"
    ]
    source_campaign: V2CarrySourceCampaign
    source_artifacts: tuple[V2CarrySourceArtifact, ...]
    source_disposition: V2CarrySourceDisposition
    excluded_material: tuple[str, ...]
    facts: tuple[V2CarryFact, ...]
    consumption_rules: V2CarryConsumptionRules

    @model_validator(mode="after")
    def validate_packet(self) -> V2CarryPacket:
        if self.schema_id != V2_CARRY_PACKET_SCHEMA:
            raise ValueError("unsupported Calibration 003 v2 carry packet schema")
        if _SAFE_ID.fullmatch(self.packet_id) is None:
            raise ValueError("carry packet identity must be a safe stable identifier")
        if self.classification != _V2_CARRY_CLASSIFICATION:
            raise ValueError("unsupported carry packet classification")
        if self.claim_ceiling != _V2_CARRY_CLAIM_CEILING:
            raise ValueError("unsupported carry packet claim ceiling")
        if self.excluded_material != _V2_REQUIRED_EXCLUSIONS:
            raise ValueError("carry packet must retain the complete fixed exclusion boundary")
        artifact_paths = tuple(item.path for item in self.source_artifacts)
        if artifact_paths != tuple(sorted(set(artifact_paths))) or not artifact_paths:
            raise ValueError("carry source artifacts must be non-empty, sorted, and unique")
        fact_ids = tuple(item.fact_id for item in self.facts)
        if len(set(fact_ids)) != len(fact_ids) or not fact_ids:
            raise ValueError("carry facts must have unique stable identities")
        known = set(fact_ids)
        for fact in self.facts:
            referenced = set(fact.predecessor_fact_ids) | set(fact.supersedes_fact_ids)
            if not referenced <= known:
                raise ValueError("carry fact relation references an unknown fact identity")
        return self


class OperatorBinding(ContractModel):
    """One operator/model identity shared exactly by both matched arms."""

    operator_id: str
    operator_version: str
    operator_artifact_ref: str
    interaction_mode: Literal["external_concise_proposals"] = "external_concise_proposals"
    hosted_weights_bound: bool
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_binding(self) -> OperatorBinding:
        _require_canonical_identity(self.operator_id, "operator identity")
        _require_canonical_identity(self.operator_version, "operator version")
        _require_digest(self.operator_artifact_ref, "operator artifact")
        return self


class DomainBinding(ContractModel):
    """Future domain adapter identity; this package never invokes it."""

    adapter_id: str
    adapter_version: str
    adapter_artifact_ref: str
    terminal_authority_source: str

    @model_validator(mode="after")
    def validate_binding(self) -> DomainBinding:
        _require_text(self.adapter_id, "domain adapter identity")
        _require_text(self.adapter_version, "domain adapter version")
        _require_digest(self.adapter_artifact_ref, "domain adapter artifact")
        _require_text(self.terminal_authority_source, "terminal authority source")
        return self


class EvidenceYieldGate(ContractModel):
    """A non-timer-only stage gate retained for later execution integration."""

    minimum_material_distinctions: PositiveInt = 1
    maximum_consecutive_no_yield_cycles: PositiveInt = 2
    minimum_prediction_residuals_reviewed: PositiveInt = 1
    timer_alone_may_advance: Literal[False] = False
    exhaustion_requires_reassessment_or_stop: Literal[True] = True


class Calibration003Plan(ContractModel):
    """Predeclared matched campaign configuration with no execution grant."""

    schema_id: str = Field(default=CALIBRATION_003_SCHEMA, alias="schema")
    campaign_id: str
    claim_label: CampaignClaimLabel
    v2_carry_evidence_ref: str | None = None
    objective: str
    success_condition: str
    success_state: str
    evaluation_class: str
    strongwiz_version: str
    kernel_artifact_ref: str
    frozen_runtime_ref: str
    pal23_profile_ref: str
    operator: OperatorBinding
    domain: DomainBinding
    scribe_driver: ScribeDriverBinding
    scribe_policy: ScribePolicy
    source_identity_refs: tuple[str, ...]
    seed: NonNegativeInt
    resource_budget: CostVector
    evidence_yield_gate: EvidenceYieldGate = EvidenceYieldGate()
    arm_roles: tuple[CampaignArmRole, CampaignArmRole] = (
        CampaignArmRole.NO_SCRIBE,
        CampaignArmRole.SCRIBE,
    )
    carry_application_order: Literal["after_both_zero_state_genesis_seals"] = (
        "after_both_zero_state_genesis_seals"
    )
    campaign_mode: Literal["preparation_only"] = "preparation_only"
    environment_access_allowed: Literal[False] = False
    credential_path_present: Literal[False] = False
    action_port_present: Literal[False] = False
    execution_grant_ref: None = None
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_plan(self) -> Calibration003Plan:
        if self.schema_id != CALIBRATION_003_SCHEMA:
            raise ValueError("unsupported Calibration 003 schema")
        if _SAFE_ID.fullmatch(self.campaign_id) is None:
            raise ValueError("campaign identity must be a safe stable identifier")
        for label, value in (
            ("objective", self.objective),
            ("success condition", self.success_condition),
            ("success state", self.success_state),
            ("evaluation class", self.evaluation_class),
            ("Strongwiz version", self.strongwiz_version),
        ):
            _require_text(value, label)
        for label, value in (
            ("kernel artifact", self.kernel_artifact_ref),
            ("frozen runtime", self.frozen_runtime_ref),
            ("PAL v2.3 profile", self.pal23_profile_ref),
        ):
            _require_digest(value, label)
        _require_sorted_unique(self.source_identity_refs, "source identity references")
        if not self.source_identity_refs:
            raise ValueError("at least one source identity is required")
        for value in self.source_identity_refs:
            _require_digest(value, "source identity reference")
        if self.arm_roles != (CampaignArmRole.NO_SCRIBE, CampaignArmRole.SCRIBE):
            raise ValueError("Calibration 003 requires exactly no-scribe then scribe arms")
        if _identity_key(self.operator.operator_id) == _identity_key(
            self.scribe_driver.driver_id
        ):
            raise ValueError("operator and scribe must have distinct role identities")
        if self.operator.operator_artifact_ref == self.scribe_driver.driver_artifact_ref:
            raise ValueError("operator and scribe must bind distinct artifacts")
        if self.v2_carry_evidence_ref is not None:
            _require_digest(self.v2_carry_evidence_ref, "v2 carry evidence")
            if self.claim_label not in {
                CampaignClaimLabel.ADAPTIVE_SUCCESSOR,
                CampaignClaimLabel.REANALYSIS,
            }:
                raise ValueError(
                    "v2 carry evidence requires an adaptive_successor or reanalysis claim"
                )
        return self


class CampaignArmIndex(ContractModel):
    """External pointer set for one physically independent zero-state lab."""

    arm_role: CampaignArmRole
    relative_lab_root: str
    lab_manifest_ref: str
    run_spec_ref: str
    genesis_seal_ref: str
    ledger_zero_state_seal_ref: str
    domain_zero_state_seal_ref: str
    current_state_matches_genesis: Literal[True] = True

    @model_validator(mode="after")
    def validate_arm(self) -> CampaignArmIndex:
        expected = f"arms/{self.arm_role.value}"
        if self.relative_lab_root != expected:
            raise ValueError("campaign arm root must use its fixed external index locator")
        for value in (
            self.lab_manifest_ref,
            self.run_spec_ref,
            self.genesis_seal_ref,
            self.ledger_zero_state_seal_ref,
            self.domain_zero_state_seal_ref,
        ):
            _require_digest(value, "campaign arm reference")
        return self


class CampaignPreparationMarker(ContractModel):
    """Immutable restart marker written before either zero-state arm exists."""

    schema_id: str = Field(default=CALIBRATION_003_SCHEMA, alias="schema")
    campaign_id: str
    plan_ref: str
    carry_evidence_ref: str | None
    arm_roles: tuple[CampaignArmRole, CampaignArmRole] = (
        CampaignArmRole.NO_SCRIBE,
        CampaignArmRole.SCRIBE,
    )
    preparation_only: Literal[True] = True
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_marker(self) -> CampaignPreparationMarker:
        if self.schema_id != CALIBRATION_003_SCHEMA:
            raise ValueError("unsupported campaign preparation marker schema")
        if _SAFE_ID.fullmatch(self.campaign_id) is None:
            raise ValueError("campaign identity must be a safe stable identifier")
        _require_digest(self.plan_ref, "campaign plan")
        if self.carry_evidence_ref is not None:
            _require_digest(self.carry_evidence_ref, "carry evidence")
        if self.arm_roles != (CampaignArmRole.NO_SCRIBE, CampaignArmRole.SCRIBE):
            raise ValueError("campaign marker must bind exactly the two matched arms")
        return self


class CampaignIndex(ContractModel):
    """Metadata-only index outside both labs; it contains references and seals."""

    schema_id: str = Field(default=CALIBRATION_003_SCHEMA, alias="schema")
    campaign_id: str
    plan_ref: str
    claim_label: CampaignClaimLabel
    carry_evidence_ref: str | None
    arms: tuple[CampaignArmIndex, CampaignArmIndex]
    content_policy: Literal["identities_references_and_zero_state_seals_only"] = (
        "identities_references_and_zero_state_seals_only"
    )
    contains_observations: Literal[False] = False
    contains_domain_state: Literal[False] = False
    contains_action_sequences: Literal[False] = False
    contains_private_reasoning: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_index(self) -> CampaignIndex:
        if self.schema_id != CALIBRATION_003_SCHEMA:
            raise ValueError("unsupported campaign index schema")
        if _SAFE_ID.fullmatch(self.campaign_id) is None:
            raise ValueError("campaign identity must be a safe stable identifier")
        _require_digest(self.plan_ref, "campaign plan")
        if self.carry_evidence_ref is not None:
            _require_digest(self.carry_evidence_ref, "carry evidence")
        if tuple(item.arm_role for item in self.arms) != (
            CampaignArmRole.NO_SCRIBE,
            CampaignArmRole.SCRIBE,
        ):
            raise ValueError("campaign index must bind exactly the two matched arms")
        if self.arms[0].relative_lab_root == self.arms[1].relative_lab_root:
            raise ValueError("campaign arm roots must be physically distinct")
        return self


class CampaignVerification(ContractModel):
    schema_id: str = Field(default=CALIBRATION_003_SCHEMA, alias="schema")
    campaign_id: str
    plan_ref: str
    index_ref: str
    arm_genesis_refs: tuple[str, str]
    arm_ledger_seal_refs: tuple[str, str]
    arm_domain_seal_refs: tuple[str, str]
    physically_separate_roots: Literal[True] = True
    physically_separate_ledgers: Literal[True] = True
    both_currently_zero_state: Literal[True] = True
    matched_seed: Literal[True] = True
    matched_resource_budget: Literal[True] = True
    matched_operator_identity: Literal[True] = True
    carry_declared_only_after_genesis: Literal[True] = True
    no_environment_access: Literal[True] = True
    no_credentials: Literal[True] = True
    no_action_port: Literal[True] = True
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_verification(self) -> CampaignVerification:
        if self.schema_id != CALIBRATION_003_SCHEMA:
            raise ValueError("unsupported campaign verification schema")
        return self


class SyntheticPreflightReceipt(ContractModel):
    """Bounded synthetic evidence for the scribe machinery, never a task result."""

    schema_id: str = Field(default=CALIBRATION_003_SCHEMA, alias="schema")
    preflight_id: str
    driver_binding_ref: str
    scribe_policy_ref: str
    boundary_adapter_ref: str
    state_projection_ref: str
    promoted_cycle_ref: str
    residual_cycle_ref: str
    failure_cycle_ref: str
    ledger_receipt_count: PositiveInt
    ledger_receipt_head: str
    request_bound_to_driver: Literal[True] = True
    heldout_payloads_absent_from_request_view: Literal[True] = True
    heldout_refs_disjoint_from_adaptation: Literal[True] = True
    duplicate_payload_refs_kept_in_one_arm: Literal[True] = True
    exact_round_trip: Literal[True] = True
    promotion_policy_applied: Literal[True] = True
    restart_deterministic: Literal[True] = True
    repeated_cycle_idempotent: Literal[True] = True
    residual_fallback_receipted: Literal[True] = True
    driver_failure_fallback_receipted: Literal[True] = True
    pending_material_preserved_after_failure: Literal[True] = True
    no_environment_access: Literal[True] = True
    no_credentials: Literal[True] = True
    no_action_port: Literal[True] = True
    result_class: Literal["synthetic_preflight_only"] = "synthetic_preflight_only"
    claim_ceiling: Literal[
        "scribe contract behavior on this deterministic synthetic fixture only"
    ] = "scribe contract behavior on this deterministic synthetic fixture only"
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_receipt(self) -> SyntheticPreflightReceipt:
        if self.schema_id != CALIBRATION_003_SCHEMA:
            raise ValueError("unsupported synthetic preflight receipt schema")
        if _SAFE_ID.fullmatch(self.preflight_id) is None:
            raise ValueError("preflight identity must be a safe stable identifier")
        for value in (
            self.driver_binding_ref,
            self.scribe_policy_ref,
            self.boundary_adapter_ref,
            self.state_projection_ref,
            self.promoted_cycle_ref,
            self.residual_cycle_ref,
            self.failure_cycle_ref,
            self.ledger_receipt_head,
        ):
            _require_digest(value, "synthetic preflight reference")
        return self


def calibration_003_schema_bundle() -> dict[str, object]:
    """Expose only the preparation surface and its bounded claim."""

    return {
        "schema": CALIBRATION_003_SCHEMA,
        "commands": ("prepare", "schema", "synthetic-preflight", "verify"),
        "forbidden_capabilities": (
            "credential loading",
            "environment acquisition",
            "environment actions",
            "network access",
            "result assessment",
        ),
        "claim_ceiling": "preparation and deterministic synthetic preflight only",
        "schemas": {
            "campaign_index": CampaignIndex.model_json_schema(),
            "campaign_plan": Calibration003Plan.model_json_schema(),
            "campaign_preparation_marker": CampaignPreparationMarker.model_json_schema(),
            "campaign_verification": CampaignVerification.model_json_schema(),
            "synthetic_preflight_receipt": SyntheticPreflightReceipt.model_json_schema(),
            "v2_carry_packet": V2CarryPacket.model_json_schema(),
        },
    }
