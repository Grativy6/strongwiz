"""Closed contracts owned by calibration 001, separate from the Strongwiz kernel."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from strongwiz.authority import TaskGrant
from strongwiz.canonical import ImmutableJSONObject, parse_strict_json
from strongwiz.contracts import (
    ContractModel,
    DecisionEffect,
    Goal,
    NonNegativeInt,
    PositiveInt,
)
from strongwiz.integrity import FrozenRuntimeManifest, sha256_file

PREREGISTRATION_SCHEMA = "strongwiz.arc-agi3-calibration-preregistration.v1"
ASSET_MANIFEST_SCHEMA = "strongwiz.arc-agi3-official-asset.v1"
RUN_RECEIPT_SCHEMA = "strongwiz.arc-agi3-calibration-run-receipt.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class AccessPolicy(ContractModel):
    account_creation_authorized: Literal[False]
    anonymous_public_api_authorized: Literal[True]
    competition_entry_authorized: Literal[False]
    owner_credentials_authorized: Literal[False]
    submission_authorized: Literal[False]
    terms_conflict_reviewed_by_owner: Literal[True]


class CalibrationBudgets(ContractModel):
    maximum_non_reset_actions: PositiveInt
    maximum_resets: PositiveInt
    maximum_total_environment_calls: PositiveInt
    wall_clock_seconds: PositiveInt

    @model_validator(mode="after")
    def validate_totals(self) -> CalibrationBudgets:
        if self.maximum_total_environment_calls != (
            self.maximum_non_reset_actions + self.maximum_resets
        ):
            raise ValueError("total call budget must equal action plus reset budgets")
        return self


class CleanRoomBoundary(ContractModel):
    forbidden_sources: tuple[str, ...]
    parent_context_disqualified_from_action_selection: Literal[True]
    prior_domain_state_refs: tuple[()] = ()
    prior_run_refs: tuple[()] = ()

    @field_validator("forbidden_sources")
    @classmethod
    def validate_sources(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value.strip() for value in values):
            raise ValueError("clean-room forbidden sources must be explicit")
        return values


class CompletionPolicy(ContractModel):
    authoritative_state: Literal["GameState.WIN"]
    game_over_is_failure_evidence: Literal[True]
    not_finished_requires_continuation: Literal[True]
    scorecard_is_not_completion_authority: Literal[True]


class EvaluationTarget(ContractModel):
    exact_versioned_game_id: str | None
    game_name: Literal["ls20"]
    game_selection_basis: str
    seed: Literal[0]


class ModelBoundary(ContractModel):
    action_selector: str
    autonomous_offline_claim: Literal[False]
    hosted_weights_bound: Literal[False]
    parent_may_recommend_actions: Literal[False]


class OfficialDependencies(ContractModel):
    arc_agi: Literal["0.9.9"]
    arcengine: Literal["0.9.3"]
    python: Literal["3.12"]


class ToolbeltIdentity(ContractModel):
    commit: Literal["a85508dc11cc6ac30336f5c42344b62afdc86b24"]
    repository: Literal["https://github.com/Grativy6/strongwiz"]
    tree: Literal["9e58cb361919fca3638b1f76a00379740c4e4aa4"]


class CalibrationPreregistration(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-calibration-preregistration.v1"] = Field(
        alias="schema"
    )
    access: AccessPolicy
    budgets: CalibrationBudgets
    claim_class: Literal["local-public-codex-operated-strongwiz-calibration"]
    clean_room: CleanRoomBoundary
    completion: CompletionPolicy
    evaluation: EvaluationTarget
    model: ModelBoundary
    official_dependencies: OfficialDependencies
    status: Literal["preregistered_before_dependency_acquisition"]
    toolbelt: ToolbeltIdentity


class LoadedPreregistration(ContractModel):
    preregistration: CalibrationPreregistration
    file_sha256: str
    relative_path: str


def load_preregistration(repository_root: Path, path: Path) -> LoadedPreregistration:
    """Load strict JSON and bind its exact on-disk bytes."""

    root = repository_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("preregistration must remain inside the repository") from error
    parsed = parse_strict_json(resolved.read_bytes())
    preregistration = CalibrationPreregistration.model_validate(parsed)
    return LoadedPreregistration(
        preregistration=preregistration,
        file_sha256=sha256_file(resolved),
        relative_path=relative,
    )


class AssetFile(ContractModel):
    relative_path: str
    size_bytes: NonNegativeInt
    sha256: str


class OfficialAssetManifest(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-official-asset.v1"] = Field(
        default="strongwiz.arc-agi3-official-asset.v1", alias="schema"
    )
    base_game_id: Literal["ls20"]
    exact_game_id: str
    class_name: str
    metadata_file: AssetFile
    source_file: AssetFile
    arc_agi_version: Literal["0.9.9"]
    arcengine_version: Literal["0.9.3"]
    acquisition_method: Literal["official-anonymous-public-api-no-environment"] = (
        "official-anonymous-public-api-no-environment"
    )
    environment_constructed: Literal[False] = False
    anonymous_key_persisted: Literal[False] = False
    private_fields_disclosed: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> OfficialAssetManifest:
        if not self.exact_game_id.startswith(f"{self.base_game_id}-"):
            raise ValueError("asset manifest requires an exact versioned game ID")
        if not self.class_name.strip():
            raise ValueError("asset class name is required")
        return self


class ProposalDraft(ContractModel):
    """Externally supplied interpretation; control bindings are added locally."""

    schema_id: Literal["strongwiz.arc-agi3-proposal-draft.v1"] = Field(
        default="strongwiz.arc-agi3-proposal-draft.v1", alias="schema"
    )
    message_id: str
    request_ref: str
    proposal_attempt: PositiveInt = 1
    supersedes_proposal_ref: str | None = None
    proposal_id: str
    action_name: str
    action_parameters: ImmutableJSONObject = Field(default_factory=dict)
    distinction_id: str
    distinction_statement: str
    candidate_resolutions: tuple[str, ...]
    competing_predictions: tuple[str, ...]
    decision_effects: tuple[DecisionEffect, ...]
    decision_that_could_change: str
    relevance_summary: str
    smallest_discriminating_test: str
    reopening_condition: str
    prediction_id: str
    hypothesis_refs: tuple[str, ...] = ()
    expected_consequences: tuple[str, ...]
    falsified_by: tuple[str, ...]
    alternatives: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    concise_rationale: str
    reversible: bool
    expected_progress_rank: PositiveInt
    information_gain_rank: PositiveInt
    risk_rank: NonNegativeInt
    credible_plan_supported: bool = False
    uncertainty_blocks_progress: bool = True

    @model_validator(mode="after")
    def validate_draft(self) -> ProposalDraft:
        required = (
            self.message_id,
            self.request_ref,
            self.proposal_id,
            self.action_name,
            self.distinction_id,
            self.distinction_statement,
            self.decision_that_could_change,
            self.relevance_summary,
            self.smallest_discriminating_test,
            self.reopening_condition,
            self.prediction_id,
            self.concise_rationale,
        )
        if not all(value.strip() for value in required):
            raise ValueError("proposal draft fields must be non-empty")
        if len(self.candidate_resolutions) < 2 or len(self.competing_predictions) < 2:
            raise ValueError("proposal draft must preserve competing alternatives")
        if not self.decision_effects or not self.expected_consequences or not self.falsified_by:
            raise ValueError("proposal draft requires effects, predictions, and falsifiers")
        if DecisionEffect.OUTPUT in self.decision_effects:
            raise ValueError("environment proposals cannot request a release output effect")
        if self.proposal_attempt == 1:
            if self.supersedes_proposal_ref is not None:
                raise ValueError("first proposal attempt cannot supersede another proposal")
        elif self.supersedes_proposal_ref is None:
            raise ValueError("revised proposal attempt must bind its exact predecessor")
        if self.supersedes_proposal_ref is not None and not _DIGEST.fullmatch(
            self.supersedes_proposal_ref
        ):
            raise ValueError("superseded proposal reference must be a lowercase SHA-256 digest")
        return self


class AssessmentDraft(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-assessment-draft.v1"] = Field(
        default="strongwiz.arc-agi3-assessment-draft.v1", alias="schema"
    )
    message_id: str
    proposal_ref: str
    matched_prediction_items: tuple[str, ...] = ()
    residual_refs: tuple[str, ...] = ()
    preserved_hypothesis_refs: tuple[str, ...] = ()
    revised_hypothesis_refs: tuple[str, ...] = ()
    concise_update_summary: str

    @model_validator(mode="after")
    def validate_draft(self) -> AssessmentDraft:
        if not self.message_id.strip() or not self.proposal_ref.strip():
            raise ValueError("assessment draft identity is required")
        if not self.concise_update_summary.strip():
            raise ValueError("assessment requires a concise update summary")
        if set(self.preserved_hypothesis_refs) & set(self.revised_hypothesis_refs):
            raise ValueError("one hypothesis cannot be preserved and revised")
        return self


class FrameEvidence(ContractModel):
    occurrence_id: str
    call_index: PositiveInt
    raw_ref: str
    raw_relative_path: str
    image_relative_paths: tuple[str, ...]
    state: Literal["NOT_PLAYED", "NOT_FINISHED", "WIN", "GAME_OVER"]
    game_id: str
    levels_completed: NonNegativeInt
    win_levels: NonNegativeInt
    available_action_names: tuple[str, ...]


class BudgetReceipt(ContractModel):
    maximum_non_reset_actions: PositiveInt
    maximum_resets: PositiveInt
    maximum_total_environment_calls: PositiveInt
    wall_clock_seconds: PositiveInt
    non_reset_actions: NonNegativeInt
    resets: NonNegativeInt
    total_environment_calls: NonNegativeInt
    elapsed_wall_ms: NonNegativeInt


class InitialResetAdmission(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-initial-reset-admission.v1"] = Field(
        default="strongwiz.arc-agi3-initial-reset-admission.v1", alias="schema"
    )
    run_id: str
    game_id: str
    asset_manifest_ref: str
    lab_genesis_ref: str
    call_index: Literal[1] = 1
    action_name: Literal["RESET"] = "RESET"
    budget_after_reservation: BudgetReceipt
    status: Literal["admitted_unclosed"] = "admitted_unclosed"
    effect: Literal["MAY_START_AFTER_THIS_DURABLE_RECORD"] = (
        "MAY_START_AFTER_THIS_DURABLE_RECORD"
    )


class InitialResetCompletion(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-initial-reset-completion.v1"] = Field(
        default="strongwiz.arc-agi3-initial-reset-completion.v1", alias="schema"
    )
    run_id: str
    admission_ref: str
    frame: FrameEvidence
    budget: BudgetReceipt
    status: Literal["completed"] = "completed"
    effect_started: Literal[True] = True


class EnvironmentCallAdmission(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-call-admission.v1"] = Field(
        default="strongwiz.arc-agi3-call-admission.v1", alias="schema"
    )
    run_id: str
    invocation_id: str
    call_index: PositiveInt
    action_name: str
    proposal_ref: str
    execution_admission_ref: str
    budget_before_reservation: BudgetReceipt
    status: Literal["admitted_unclosed"] = "admitted_unclosed"
    effect: Literal["MAY_START_AFTER_THIS_DURABLE_RECORD"] = (
        "MAY_START_AFTER_THIS_DURABLE_RECORD"
    )


class EnvironmentCallCompletion(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-call-completion.v1"] = Field(
        default="strongwiz.arc-agi3-call-completion.v1", alias="schema"
    )
    run_id: str
    admission_ref: str
    execution_admission_ref: str
    execution_attempt_ref: str
    frame: FrameEvidence
    budget: BudgetReceipt
    status: Literal["completed_awaiting_assessment"] = "completed_awaiting_assessment"
    effect_started: Literal[True] = True


class EnvironmentCallDenial(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-call-denial.v1"] = Field(
        default="strongwiz.arc-agi3-call-denial.v1", alias="schema"
    )
    run_id: str
    admission_ref: str
    execution_admission_ref: str
    execution_attempt_ref: str
    denial_category: str
    status: Literal["denied_known_no_effect"] = "denied_known_no_effect"
    effect_started: Literal[False] = False

    @field_validator("denial_category")
    @classmethod
    def validate_denial(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("known-no-effect denial requires a category")
        return value


class EnvironmentCallAssessmentClosure(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-call-assessment-closure.v1"] = Field(
        default="strongwiz.arc-agi3-call-assessment-closure.v1", alias="schema"
    )
    run_id: str
    admission_ref: str
    completion_ref: str
    assessment_ref: str
    status: Literal["assessed_closed"] = "assessed_closed"


class InterruptedRunMarker(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-interrupted-run.v1"] = Field(
        default="strongwiz.arc-agi3-interrupted-run.v1", alias="schema"
    )
    run_id: str
    initial_reset_admission_ref: str
    latest_frame: FrameEvidence | None = None
    budget: BudgetReceipt
    failure_stage: str
    error_class: str
    disposition: Literal["failed_infrastructure"] = "failed_infrastructure"
    effect_status: Literal["UNKNOWN_EFFECT"] = "UNKNOWN_EFFECT"
    retry_permitted: Literal[False] = False
    concise_summary: str

    @model_validator(mode="after")
    def validate_summary(self) -> InterruptedRunMarker:
        if not self.failure_stage.strip() or not self.error_class.strip():
            raise ValueError("interrupted run requires a failure stage and class")
        if not self.concise_summary.strip():
            raise ValueError("interrupted run requires a concise summary")
        return self


class ArtifactPointer(ContractModel):
    path: str
    sha256: str
    size_bytes: NonNegativeInt


class PreparedRunBundle(ContractModel):
    """Exact control inputs written only after the empty-genesis seal exists."""

    schema_id: Literal["strongwiz.arc-agi3-calibration-prepared-run.v1"] = Field(
        default="strongwiz.arc-agi3-calibration-prepared-run.v1", alias="schema"
    )
    run_id: str
    preregistration_path: str
    preregistration_file_ref: str
    asset_manifest_path: str
    asset_manifest_ref: str
    dependency_ref: str
    toolbelt_ref: str
    integration_ref: str
    model_interface_ref: str
    domain_adapter_ref: str
    executor_ref: str
    goal: Goal
    grant: TaskGrant
    frozen_runtime: FrozenRuntimeManifest


class RunTerminalRecord(ContractModel):
    schema_id: Literal["strongwiz.arc-agi3-calibration-terminal.v1"] = Field(
        default="strongwiz.arc-agi3-calibration-terminal.v1", alias="schema"
    )
    run_id: str
    game_id: str
    asset_manifest_ref: str
    final_state: Literal["NOT_PLAYED", "NOT_FINISHED", "WIN", "GAME_OVER", "UNKNOWN_EFFECT"]
    levels_completed: NonNegativeInt
    win_levels: NonNegativeInt
    budget: BudgetReceipt
    frozen_runtime_ref: str
    toolbelt_ref: str
    integration_ref: str
    dependency_ref: str
    model_interface_ref: str
    domain_adapter_ref: str
    executor_ref: str
    lab_genesis_ref: str
    latest_checkpoint_ref: str | None
    initial_reset_admission_ref: str
    terminal_frame: FrameEvidence | None
    raw_trace: ArtifactPointer | None
    official_recordings: tuple[ArtifactPointer, ...]
    completion_genuinely_observed: bool
    disposition: Literal[
        "success_observed",
        "partial",
        "blocked_external",
        "failed_mechanism",
        "failed_infrastructure",
    ]
    concise_result_summary: str
    claim_class: str
    claim_exclusions: tuple[str, ...]
    incidents: tuple[str, ...] = ()
    unresolved_burdens: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_completion(self) -> RunTerminalRecord:
        if not self.concise_result_summary.strip():
            raise ValueError("terminal record requires a concise result summary")
        if self.completion_genuinely_observed and self.final_state != "WIN":
            raise ValueError("completion requires the exact WIN enum projection")
        if self.completion_genuinely_observed and (
            self.terminal_frame is None or self.raw_trace is None
        ):
            raise ValueError("completion requires terminal frame and raw trace evidence")
        if self.terminal_frame is not None and self.terminal_frame.state != self.final_state:
            raise ValueError("terminal frame disagrees with the final state")
        if self.final_state == "UNKNOWN_EFFECT" and self.terminal_frame is not None:
            raise ValueError("unknown initial effect cannot claim a terminal frame")
        if self.completion_genuinely_observed != (self.disposition == "success_observed"):
            raise ValueError("success disposition must agree with terminal WIN")
        return self


class CalibrationRunReceipt(ContractModel):
    """Post-capsule delivery receipt; kept outside the sealed lab to avoid a hash cycle."""

    schema_id: Literal["strongwiz.arc-agi3-calibration-run-receipt.v1"] = Field(
        default="strongwiz.arc-agi3-calibration-run-receipt.v1", alias="schema"
    )
    terminal_record_ref: str
    terminal_record: RunTerminalRecord
    run_seal: ArtifactPointer
    run_seal_ref: str
    evidence_capsule_path: str
    evidence_capsule_ref: str
    evidence_capsule_manifest: ArtifactPointer
    capsule_verified: Literal[True]
    note: Literal["delivery receipt stays external to avoid a circular capsule hash"] = (
        "delivery receipt stays external to avoid a circular capsule hash"
    )
