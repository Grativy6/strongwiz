"""Sealed, receipt-backed transitions between Calibration 002 stages.

The source run, its learning workspace, the advisory review, and the external
control decision remain separate objects.  This module never exposes an
environment-action API.  A review may recommend, reject, or defer a shorthand
candidate, but only a distinct target-bound control decision can create a
successor transfer.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from calibration.models import RunTerminalRecord
from calibration_002.learning import (
    CALIBRATION_002_EXCLUDED_MATERIAL,
    Calibration002Inheritance,
    Calibration002LearningSidecar,
    Calibration002StageBinding,
    Calibration002StageClosure,
)
from strongwiz.canonical import (
    canonical_bytes,
    content_hash,
    parse_strict_json,
    sha256_bytes,
)
from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt
from strongwiz.curriculum import CurriculumStageHandoff, NextStageDecision
from strongwiz.lab import RunDisposition, RunSeal
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger
from strongwiz.shorthand import (
    AdoptionStatus,
    EvaluationRole,
    EvaluationStatus,
    KevinAdoptionDecision,
    KevinCodebookEvaluation,
    KevinCodebookRevision,
    KevinEvaluationSample,
    KevinRecommendationBundle,
    KevinRecommendationReview,
    KevinSpeakConfiguration,
    KevinSpeakTransfer,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
    KevinWorkspaceVerification,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_FIELDS = frozenset(
    {
        "action_history",
        "action_sequence",
        "action_sequences",
        "action_trace",
        "actions",
        "authority",
        "authorization",
        "chain_of_thought",
        "domain_state",
        "game_state",
        "hidden_reasoning",
        "private_reasoning",
        "raw_frame",
        "raw_frames",
        "scratchpad",
    }
)
_FORBIDDEN_FIELDS_COMPACT = frozenset(value.replace("_", "") for value in _FORBIDDEN_FIELDS)
_ARTIFACT_MANIFEST_PATH = "transition.manifest.json"


class Calibration002TransitionError(ValueError):
    """A sealed stage transition crossed or omitted an evidence boundary."""


class AdvisoryDisposition(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    DEFER = "defer"


class TransitionDisposition(StrEnum):
    READY = "ready"
    REJECTED = "rejected"
    DEFERRED = "deferred"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_safe_id(value: str, label: str) -> str:
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{label} must use a safe stable identifier")
    return value


def _require_sorted_digests(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    for value in values:
        _require_digest(value, label)
    return values


def _reject_excluded_payload(value: object, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized = _NON_ALNUM.sub("_", key.casefold()).strip("_")
                if (
                    normalized in _FORBIDDEN_FIELDS
                    or normalized.replace("_", "") in _FORBIDDEN_FIELDS_COMPACT
                ):
                    raise Calibration002TransitionError(
                        f"{path} contains excluded field {key!r}"
                    )
            _reject_excluded_payload(item, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_excluded_payload(item, path=f"{path}[{index}]")


class Calibration002RetainedMechanicDraft(ContractModel):
    """Concise, provisional mechanic proposed for explicit successor transfer."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-retained-mechanic-draft.v1", alias="schema"
    )
    mechanic_id: str
    concise_statement: str
    scope: str
    evidence_refs: tuple[str, ...]
    reopening_condition: str
    reliability: Literal["provisional", "supported", "reliable"] = "provisional"
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_draft(self) -> Calibration002RetainedMechanicDraft:
        _require_safe_id(self.mechanic_id, "mechanic identity")
        if not all(
            value.strip()
            for value in (self.concise_statement, self.scope, self.reopening_condition)
        ):
            raise ValueError("retained mechanics require statement, scope, and reopening")
        _require_sorted_digests(self.evidence_refs, "mechanic evidence references")
        if not self.evidence_refs:
            raise ValueError("a retained mechanic requires evidence")
        return self


class Calibration002RetainedMechanic(ContractModel):
    """One source-bound mechanic object; evidence only and reopenable."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-retained-mechanic.v1", alias="schema"
    )
    draft: Calibration002RetainedMechanicDraft
    source_stage_ref: str
    source_run_seal_ref: str
    source_terminal_record_ref: str
    transfers_authority: Literal[False] = False
    claim_ceiling: Literal["provisional_mechanic_for_successor_testing"] = (
        "provisional_mechanic_for_successor_testing"
    )
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_record(self) -> Calibration002RetainedMechanic:
        for value in (
            self.source_stage_ref,
            self.source_run_seal_ref,
            self.source_terminal_record_ref,
        ):
            _require_digest(value, "retained mechanic source binding")
        return self


class Calibration002RefinementRequest(ContractModel):
    """Optional review-only refinement, admitted only after measured evaluation."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-refinement-request.v1", alias="schema"
    )
    proposals: tuple[KevinSymbolProposal, ...]
    samples: tuple[KevinEvaluationSample, ...]
    rationale: str
    evaluation_id: str
    retired_tokens: tuple[str, ...] = ()
    model_proposal_ref: str | None = None
    select_if_eligible: bool = True
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_refinement(self) -> Calibration002RefinementRequest:
        if not self.proposals or not self.samples:
            raise ValueError("a review refinement requires proposals and evaluation samples")
        if not self.rationale.strip() or not self.evaluation_id.strip():
            raise ValueError("a review refinement requires rationale and evaluation identity")
        case_ids = tuple(item.case_id for item in self.samples)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("refinement evaluation case identities must be unique")
        if tuple(sorted(set(self.retired_tokens))) != self.retired_tokens:
            raise ValueError("retired refinement tokens must be sorted and unique")
        if self.model_proposal_ref is not None:
            _require_digest(self.model_proposal_ref, "refinement model proposal")
        for sample in self.samples:
            _reject_excluded_payload(sample.payload, path=f"refinement[{sample.case_id}]")
        return self


class Calibration002AdvisoryReviewRequest(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-advisory-review-request.v1", alias="schema"
    )
    review_id: str
    reviewer_driver_ref: str
    disposition: AdvisoryDisposition
    rationale: str
    refinement: Calibration002RefinementRequest | None = None
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_request(self) -> Calibration002AdvisoryReviewRequest:
        _require_safe_id(self.review_id, "review identity")
        _require_digest(self.reviewer_driver_ref, "reviewer driver")
        if not self.rationale.strip():
            raise ValueError("an advisory review requires a concise rationale")
        return self


class Calibration002SourceTransition(ContractModel):
    """Closed source-stage evidence before any successor review or adoption."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-source-transition.v1", alias="schema"
    )
    transition_id: str
    source_binding: Calibration002StageBinding
    run_seal: RunSeal
    source_capsule_ref: str
    terminal_record: RunTerminalRecord
    handoff: CurriculumStageHandoff
    recommendation_ref: str
    recommendation_bundle: KevinRecommendationBundle
    retained_mechanics: tuple[Calibration002RetainedMechanic, ...]
    source_workspace_verification: KevinWorkspaceVerification
    controller_checkpoint_ref: str
    next_stage_ref: str | None
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    transfers_authority: Literal[False] = False
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_source(self) -> Calibration002SourceTransition:
        _require_safe_id(self.transition_id, "transition identity")
        _require_digest(self.source_capsule_ref, "source capsule")
        _require_digest(self.recommendation_ref, "source recommendation")
        _require_digest(self.controller_checkpoint_ref, "controller checkpoint")
        if self.next_stage_ref is not None:
            _require_digest(self.next_stage_ref, "next curriculum stage")
        if self.run_seal.digest != self.handoff.run_seal_ref:
            raise ValueError("source handoff does not bind the exact run seal")
        if self.run_seal.terminal_evidence_ref != self.terminal_record.digest:
            raise ValueError("run seal does not bind the exact terminal record")
        if (
            self.run_seal.run_id != self.terminal_record.run_id
            or self.run_seal.run_id != self.source_binding.run_id
            or self.run_seal.genesis_ref != self.terminal_record.lab_genesis_ref
            or self.run_seal.terminal_state != self.terminal_record.final_state
            or self.run_seal.disposition.value != self.terminal_record.disposition
            or self.run_seal.completion_genuinely_observed
            != self.terminal_record.completion_genuinely_observed
        ):
            raise ValueError("run seal, terminal record, and stage binding disagree")
        if (
            self.handoff.stage_start_ref != self.source_binding.stage_start_ref
            or self.handoff.stage_ref != self.source_binding.frozen_stack.stage_ref
            or self.handoff.active_codebook_ref
            != self.source_workspace_verification.active_codebook_ref
        ):
            raise ValueError("source handoff crosses its learning-stage binding")
        bundle = self.recommendation_bundle
        if (
            bundle.recommendation.digest != self.recommendation_ref
            or bundle.source_run_seal_ref != self.run_seal.digest
            or bundle.source_capsule_ref != self.source_capsule_ref
            or bundle.recommendation.source_workspace_id
            != self.source_binding.frozen_stack.workspace_id
        ):
            raise ValueError("recommendation bundle crosses its sealed source")
        mechanic_refs = tuple(sorted(item.digest for item in self.retained_mechanics))
        if mechanic_refs != self.handoff.retained_mechanic_refs:
            raise ValueError("source handoff omits or reorders retained mechanics")
        mechanic_ids = tuple(item.draft.mechanic_id for item in self.retained_mechanics)
        if len(set(mechanic_ids)) != len(mechanic_ids):
            raise ValueError("retained mechanic identities must be unique")
        for mechanic in self.retained_mechanics:
            if (
                mechanic.source_stage_ref != self.handoff.stage_ref
                or mechanic.source_run_seal_ref != self.run_seal.digest
                or mechanic.source_terminal_record_ref != self.terminal_record.digest
            ):
                raise ValueError("retained mechanic crosses its source stage")
        advancing = self.handoff.next_decision is NextStageDecision.ADVANCE
        if advancing != (self.next_stage_ref is not None):
            raise ValueError("next-stage binding disagrees with the handoff disposition")
        if self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL:
            raise ValueError("source transition changed the inheritance exclusions")
        return self


class Calibration002AdvisoryReview(ContractModel):
    """Advisory result in a separate ledger; explicitly not an adoption."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-advisory-review.v1", alias="schema"
    )
    source_transition_ref: str
    request: Calibration002AdvisoryReviewRequest
    recommendation_ref: str
    kevin_review: KevinRecommendationReview
    refinement_candidate: KevinCodebookRevision | None = None
    refinement_evaluation: KevinCodebookEvaluation | None = None
    refinement_selected: bool = False
    review_workspace_id: str
    review_account_id: str
    status: Literal["reviewed_not_adopted"] = "reviewed_not_adopted"
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    self_authorizing: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_review(self) -> Calibration002AdvisoryReview:
        _require_digest(self.source_transition_ref, "review source transition")
        _require_digest(self.recommendation_ref, "review recommendation")
        _require_safe_id(self.review_workspace_id, "review workspace identity")
        _require_safe_id(self.review_account_id, "review account identity")
        if self.kevin_review.recommendation_ref != self.recommendation_ref:
            raise ValueError("advisory review crosses its recommendation")
        if self.kevin_review.reviewer_driver_ref != self.request.reviewer_driver_ref:
            raise ValueError("advisory review changed reviewer identity")
        paired = (
            self.refinement_candidate is not None and self.refinement_evaluation is not None
        )
        if paired != (
            self.refinement_candidate is not None or self.refinement_evaluation is not None
        ):
            raise ValueError("refinement candidate and evaluation must travel together")
        if self.refinement_selected:
            if not paired:
                raise ValueError("a selected refinement requires measured evidence")
            candidate = self.refinement_candidate
            evaluation = self.refinement_evaluation
            if candidate is None or evaluation is None:  # pragma: no cover - narrowed above
                raise AssertionError("selected refinement lost its paired evidence")
            if (
                evaluation.status is not EvaluationStatus.ELIGIBLE
                or evaluation.candidate_codebook_ref != candidate.digest
                or self.kevin_review.reviewed_codebook_ref != candidate.digest
            ):
                raise ValueError("selected refinement did not earn its exact codebook")
        if self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL:
            raise ValueError("advisory review changed the inheritance exclusions")
        return self


class Calibration002ExternalControlDecision(ContractModel):
    """Control input for one exact successor stage, independent of model review."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-external-control.v1", alias="schema"
    )
    decision_id: str
    source_transition_ref: str
    advisory_review_ref: str
    recommendation_ref: str
    target_stage_ref: str
    target_configuration_ref: str
    control_source_ref: str
    status: AdoptionStatus
    rationale: str
    scope: Literal["exact_successor_working_representation_only"] = (
        "exact_successor_working_representation_only"
    )
    transfers_authority: Literal[False] = False
    self_authorizing: Literal[False] = False

    @model_validator(mode="after")
    def validate_control(self) -> Calibration002ExternalControlDecision:
        _require_safe_id(self.decision_id, "control decision identity")
        for value in (
            self.source_transition_ref,
            self.advisory_review_ref,
            self.recommendation_ref,
            self.target_stage_ref,
            self.target_configuration_ref,
            self.control_source_ref,
        ):
            _require_digest(value, "external control binding")
        if not self.rationale.strip():
            raise ValueError("external control requires a concise rationale")
        return self


class Calibration002TransitionOutcome(ContractModel):
    """Final receipt-backed result of external control over one review."""

    schema_id: str = Field(
        default="strongwiz.calibration-002-transition-outcome.v1", alias="schema"
    )
    source_transition_ref: str
    advisory_review_ref: str
    control_decision: Calibration002ExternalControlDecision
    adoption: KevinAdoptionDecision
    disposition: TransitionDisposition
    shorthand_transfer: KevinSpeakTransfer | None = None
    inheritance: Calibration002Inheritance | None = None
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    transfers_authority: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_outcome(self) -> Calibration002TransitionOutcome:
        _require_digest(self.source_transition_ref, "outcome source transition")
        _require_digest(self.advisory_review_ref, "outcome advisory review")
        control = self.control_decision
        if (
            control.source_transition_ref != self.source_transition_ref
            or control.advisory_review_ref != self.advisory_review_ref
            or self.adoption.recommendation_ref != control.recommendation_ref
            or self.adoption.target_stage_ref != control.target_stage_ref
            or self.adoption.target_configuration_ref != control.target_configuration_ref
            or self.adoption.control_source_ref != control.control_source_ref
            or self.adoption.status is not control.status
        ):
            raise ValueError("outcome adoption crosses the exact external control decision")
        ready = self.disposition is TransitionDisposition.READY
        if ready:
            if (
                control.status is not AdoptionStatus.APPROVED
                or self.shorthand_transfer is None
                or self.inheritance is None
            ):
                raise ValueError("ready transition requires approved transfer and inheritance")
            shorthand = self.shorthand_transfer
            inheritance = self.inheritance
            if (
                shorthand.adoption.digest != self.adoption.digest
                or shorthand.digest != inheritance.shorthand_transfer.digest
                or inheritance.curriculum_transfer.target_stage_ref != control.target_stage_ref
            ):
                raise ValueError("ready transition changed its approved transfer lineage")
        elif (
            control.status is not AdoptionStatus.REJECTED
            or self.shorthand_transfer is not None
            or self.inheritance is not None
        ):
            raise ValueError("non-adopted transition cannot carry successor material")
        if self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL:
            raise ValueError("transition outcome changed the inheritance exclusions")
        return self


class Calibration002TransitionArtifact(ContractModel):
    relative_path: str
    size_bytes: NonNegativeInt
    sha256: str
    object_ref: str

    @model_validator(mode="after")
    def validate_artifact(self) -> Calibration002TransitionArtifact:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not self.relative_path.strip():
            raise ValueError("transition artifact path must be safe and relative")
        _require_digest(self.sha256, "transition artifact digest")
        _require_digest(self.object_ref, "transition artifact object")
        if self.sha256 != self.object_ref:
            raise ValueError("canonical transition artifact bytes must match object identity")
        return self


class Calibration002TransitionManifest(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-transition-manifest.v1", alias="schema"
    )
    transition_id: str
    source_transition_ref: str
    outcome_ref: str
    review_ledger_receipt_count: PositiveInt
    review_ledger_receipt_head: str
    review_ledger_projection_ref: str
    artifacts: tuple[Calibration002TransitionArtifact, ...]
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_manifest(self) -> Calibration002TransitionManifest:
        _require_safe_id(self.transition_id, "manifest transition identity")
        for value in (
            self.source_transition_ref,
            self.outcome_ref,
            self.review_ledger_receipt_head,
            self.review_ledger_projection_ref,
        ):
            _require_digest(value, "transition manifest binding")
        paths = tuple(item.relative_path for item in self.artifacts)
        if paths != tuple(sorted(set(paths))) or not paths:
            raise ValueError("transition artifacts must be nonempty, sorted, and unique")
        if self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL:
            raise ValueError("transition manifest changed the inheritance exclusions")
        return self


class Calibration002TransitionVerification(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-transition-verification.v1", alias="schema"
    )
    source_transition_ref: str
    advisory_review_ref: str
    outcome_ref: str
    target_stage_ref: str
    review_ledger_receipt_count: PositiveInt
    review_ledger_receipt_head: str
    source_workspace_verified: Literal[True] = True
    source_handoff_verified: Literal[True] = True
    review_verified: Literal[True] = True
    adoption_verified: Literal[True] = True
    target_binding_verified: Literal[True] = True
    exclusions_verified: Literal[True] = True
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_verification(self) -> Calibration002TransitionVerification:
        for value in (
            self.source_transition_ref,
            self.advisory_review_ref,
            self.outcome_ref,
            self.target_stage_ref,
            self.review_ledger_receipt_head,
        ):
            _require_digest(value, "transition verification binding")
        return self


@dataclass(frozen=True)
class Calibration002TransitionResult:
    outcome: Calibration002TransitionOutcome
    verification: Calibration002TransitionVerification
    manifest: Calibration002TransitionManifest


def _write_canonical(root: Path, relative_path: str, value: ContractModel) -> Path:
    target = root / Path(*PurePosixPath(relative_path).parts)
    raw = canonical_bytes(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or target.read_bytes() != raw:
            raise Calibration002TransitionError(
                f"transition artifact already exists with different content: {relative_path}"
            )
        return target
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, target)
    return target


def _artifact(root: Path, relative_path: str) -> Calibration002TransitionArtifact:
    path = root / Path(*PurePosixPath(relative_path).parts)
    raw = path.read_bytes()
    parsed = parse_strict_json(raw)
    if canonical_bytes(parsed) != raw:
        raise Calibration002TransitionError(
            f"transition artifact is not canonical JSON: {relative_path}"
        )
    digest = sha256_bytes(raw)
    return Calibration002TransitionArtifact(
        relative_path=relative_path,
        size_bytes=len(raw),
        sha256=digest,
        object_ref=content_hash(parsed),
    )


def _put_contract(ledger: SQLiteLedger, value: ContractModel) -> str:
    stored = ledger.put_object(value.model_dump(mode="json", by_alias=True))
    if stored != value.digest:
        raise Calibration002TransitionError("transition contract identity changed in storage")
    return stored


def _append_transition_receipt(
    ledger: SQLiteLedger,
    *,
    occurrence_id: str,
    kind: str,
    transition_id: str,
    value: ContractModel,
    object_refs: tuple[str, ...],
    parent_refs: tuple[str, ...],
) -> ReceiptEnvelope:
    value_ref = _put_contract(ledger, value)
    return ledger.append(
        occurrence_id=occurrence_id,
        kind=kind,
        account_id=f"{transition_id}.transition",
        account_version=0,
        payload=value.model_dump(mode="json", by_alias=True),
        object_refs=tuple(dict.fromkeys((value_ref, *object_refs))),
        parent_refs=parent_refs,
    )


def _assert_run_boundary(
    *,
    binding: Calibration002StageBinding,
    run_seal: RunSeal,
    terminal_record: RunTerminalRecord,
) -> None:
    expected_disposition = RunDisposition(terminal_record.disposition)
    if (
        run_seal.run_id != binding.run_id
        or terminal_record.run_id != binding.run_id
        or run_seal.disposition is not expected_disposition
        or run_seal.terminal_state != terminal_record.final_state
        or run_seal.terminal_evidence_ref != terminal_record.digest
        or run_seal.genesis_ref != terminal_record.lab_genesis_ref
        or run_seal.completion_genuinely_observed
        != terminal_record.completion_genuinely_observed
    ):
        raise Calibration002TransitionError(
            "run seal, terminal record, and active learning stage do not match"
        )


def _next_stage(
    sidecar: Calibration002LearningSidecar,
    binding: Calibration002StageBinding,
    run_seal: RunSeal,
) -> tuple[NextStageDecision, str | None]:
    stage_refs = tuple(stage.digest for stage in sidecar.plan.stages)
    try:
        index = stage_refs.index(binding.frozen_stack.stage_ref)
    except ValueError as error:  # pragma: no cover - sidecar validates this invariant
        raise Calibration002TransitionError(
            "active stage is outside the campaign plan"
        ) from error
    if run_seal.completion_genuinely_observed:
        return NextStageDecision.FINISH, None
    if index == len(stage_refs) - 1:
        return NextStageDecision.REASSESS, None
    return NextStageDecision.ADVANCE, stage_refs[index + 1]


def close_source_stage(
    sidecar: Calibration002LearningSidecar,
    *,
    transition_id: str,
    run_seal: RunSeal,
    source_capsule_ref: str,
    terminal_record: RunTerminalRecord,
    recommendation_ref: str,
    retained_mechanics: Sequence[Calibration002RetainedMechanicDraft],
    artifact_root: str | Path,
) -> Calibration002SourceTransition:
    """Close one stage, then export its recommendation through a read-only reopen.

    The supplied sidecar is consumed and closed after the stage handoff is durably
    recorded.  The source recommendation remains advisory.
    """

    _require_safe_id(transition_id, "transition identity")
    _require_digest(source_capsule_ref, "source capsule")
    _require_digest(recommendation_ref, "source recommendation")
    binding = sidecar.active_binding
    if binding is None:
        raise Calibration002TransitionError("source learning sidecar has no active stage")
    sidecar.verify()
    _assert_run_boundary(
        binding=binding,
        run_seal=run_seal,
        terminal_record=terminal_record,
    )
    next_decision, next_stage_ref = _next_stage(sidecar, binding, run_seal)
    mechanic_records = tuple(
        Calibration002RetainedMechanic(
            draft=draft,
            source_stage_ref=binding.frozen_stack.stage_ref,
            source_run_seal_ref=run_seal.digest,
            source_terminal_record_ref=terminal_record.digest,
        )
        for draft in retained_mechanics
    )
    mechanic_records = tuple(sorted(mechanic_records, key=lambda item: item.digest))
    if len({item.draft.mechanic_id for item in mechanic_records}) != len(mechanic_records):
        raise Calibration002TransitionError("retained mechanic identity cannot be reused")
    handoff = CurriculumStageHandoff(
        stage_start_ref=binding.stage_start_ref,
        stage_ref=binding.frozen_stack.stage_ref,
        run_seal_ref=run_seal.digest,
        disposition=run_seal.disposition,
        completion_genuinely_observed=run_seal.completion_genuinely_observed,
        terminal_state=run_seal.terminal_state,
        progress_evidence_refs=tuple(sorted({terminal_record.digest, source_capsule_ref})),
        active_codebook_ref=sidecar.table().codebook_ref,
        retained_mechanic_refs=tuple(item.digest for item in mechanic_records),
        next_decision=next_decision,
        concise_result=terminal_record.concise_result_summary,
    )
    source_ledger_path = sidecar.ledger_path
    workspace_id = binding.frozen_stack.workspace_id
    account_id = binding.account_id
    closed = False
    try:
        sidecar.finish_stage(handoff)
        checkpoint_ref = sidecar.checkpoint.digest
        sidecar.verify()
        sidecar.close()
        closed = True
        with SQLiteLedger(source_ledger_path, readonly=True) as source_ledger:
            source_ledger.verify()
            workspace = KevinSpeakWorkspace.restore(
                source_ledger,
                workspace_id=workspace_id,
                account_id=account_id,
            )
            source_verification = workspace.verify()
            bundle = workspace.export_recommendation_bundle(
                recommendation_ref=recommendation_ref,
                source_run_seal_ref=run_seal.digest,
                source_capsule_ref=source_capsule_ref,
            )
    finally:
        if not closed:
            sidecar.close()
    source = Calibration002SourceTransition(
        transition_id=transition_id,
        source_binding=binding,
        run_seal=run_seal,
        source_capsule_ref=source_capsule_ref,
        terminal_record=terminal_record,
        handoff=handoff,
        recommendation_ref=recommendation_ref,
        recommendation_bundle=bundle,
        retained_mechanics=mechanic_records,
        source_workspace_verification=source_verification,
        controller_checkpoint_ref=checkpoint_ref,
        next_stage_ref=next_stage_ref,
    )
    root = Path(artifact_root)
    _write_canonical(root, "source/run.seal.json", run_seal)
    _write_canonical(root, "source/terminal.record.json", terminal_record)
    _write_canonical(root, "source/stage.handoff.json", handoff)
    _write_canonical(root, "source/recommendation.bundle.json", bundle)
    for mechanic in mechanic_records:
        _write_canonical(
            root,
            f"source/mechanics/{mechanic.draft.mechanic_id}.json",
            mechanic,
        )
    _write_canonical(root, "source/transition.json", source)
    return source


def _validate_refinement_sources(
    refinement: Calibration002RefinementRequest,
    bundle: KevinRecommendationBundle,
) -> None:
    inherited_sources = {
        case.payload_ref for evaluation in bundle.evaluations for case in evaluation.cases
    }
    adaptation_sources = {
        content_hash(sample.payload)
        for sample in refinement.samples
        if sample.role is EvaluationRole.ADAPTATION
    }
    allowed = inherited_sources | adaptation_sources
    for proposal in refinement.proposals:
        if not set(proposal.source_payload_refs) <= allowed:
            raise Calibration002TransitionError(
                "review refinement cites material outside sealed or adaptation evidence"
            )


def review_source_recommendation(
    source: Calibration002SourceTransition,
    *,
    request: Calibration002AdvisoryReviewRequest,
    source_learning_ledger_path: str | Path,
    review_ledger_path: str | Path,
    artifact_root: str | Path,
) -> Calibration002AdvisoryReview:
    """Review a sealed recommendation in its own ledger without adopting it."""

    if source.next_stage_ref is None:
        raise Calibration002TransitionError("closed source stage has no successor to review")
    source_ledger_path = Path(source_learning_ledger_path)
    ledger_path = Path(review_ledger_path)
    if ledger_path.resolve(strict=False) == source_ledger_path.resolve(strict=False):
        raise Calibration002TransitionError("review ledger must be separate from source")
    if ledger_path.exists():
        raise Calibration002TransitionError("review ledger must begin as a new empty object")
    _verify_source_ledger(source, source_ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_id = f"{source.transition_id}.review"
    account_id = f"{source.transition_id}.review-ledger"
    candidate: KevinCodebookRevision | None = None
    evaluation: KevinCodebookEvaluation | None = None
    selected_ref = source.recommendation_bundle.recommendation.recommended_codebook_ref
    selected = False
    with SQLiteLedger(ledger_path) as ledger:
        if tuple(ledger.receipts()):  # pragma: no cover - new-path invariant
            raise Calibration002TransitionError("review ledger is not empty")
        workspace = KevinSpeakWorkspace.open_review(
            ledger,
            workspace_id=workspace_id,
            account_id=account_id,
            bundle=source.recommendation_bundle,
        )
        refinement = request.refinement
        rejected_refs: tuple[str, ...] = ()
        deferred_refs: tuple[str, ...] = ()
        evaluation_refs = source.recommendation_bundle.recommendation.evaluation_refs
        if refinement is not None:
            _validate_refinement_sources(refinement, source.recommendation_bundle)
            candidate = workspace.propose_revision(
                proposals=refinement.proposals,
                retired_tokens=refinement.retired_tokens,
                rationale=refinement.rationale,
                model_proposal_ref=refinement.model_proposal_ref,
            )
            evaluation = workspace.evaluate_candidate(
                candidate.digest,
                refinement.samples,
                evaluation_id=refinement.evaluation_id,
            )
            selected = (
                request.disposition is AdvisoryDisposition.ACCEPT
                and refinement.select_if_eligible
                and evaluation.status is EvaluationStatus.ELIGIBLE
            )
            candidate_definition_refs = tuple(item.digest for item in candidate.definitions)
            if selected:
                selected_ref = candidate.digest
                evaluation_refs = tuple(sorted({*evaluation_refs, evaluation.digest}))
            elif request.disposition is AdvisoryDisposition.REJECT:
                rejected_refs = candidate_definition_refs
            else:
                deferred_refs = candidate_definition_refs
        review = workspace.review_next_round(
            review_id=request.review_id,
            recommendation_ref=source.recommendation_ref,
            reviewer_driver_ref=request.reviewer_driver_ref,
            evaluation_refs=evaluation_refs,
            rationale=request.rationale,
            reviewed_codebook_ref=selected_ref,
            rejected_definition_refs=rejected_refs,
            deferred_definition_refs=deferred_refs,
        )
        advisory = Calibration002AdvisoryReview(
            source_transition_ref=source.digest,
            request=request,
            recommendation_ref=source.recommendation_ref,
            kevin_review=review,
            refinement_candidate=candidate,
            refinement_evaluation=evaluation,
            refinement_selected=selected,
            review_workspace_id=workspace_id,
            review_account_id=account_id,
        )
        verification = workspace.verify()
        object_refs = tuple(
            _put_contract(ledger, item)
            for item in (
                source,
                request,
                advisory,
                *source.retained_mechanics,
            )
        )
        _append_transition_receipt(
            ledger,
            occurrence_id=f"{source.transition_id}:advisory-review",
            kind="calibration_002_advisory_review",
            transition_id=source.transition_id,
            value=advisory,
            object_refs=(
                *object_refs,
                source.recommendation_bundle.digest,
                review.digest,
                *((candidate.digest,) if candidate is not None else ()),
                *((evaluation.digest,) if evaluation is not None else ()),
            ),
            parent_refs=(verification.receipt_head,),
        )
        ledger.verify()
    root = Path(artifact_root)
    _write_canonical(root, "review/request.json", request)
    if candidate is not None and evaluation is not None:
        _write_canonical(root, "review/refinement.candidate.json", candidate)
        _write_canonical(root, "review/refinement.evaluation.json", evaluation)
    _write_canonical(root, "review/kevin.review.json", review)
    _write_canonical(root, "review/advisory.review.json", advisory)
    return advisory


def _find_receipt(
    ledger: SQLiteLedger,
    *,
    kind: str,
    payload_ref: str,
    transition_id: str,
) -> ReceiptEnvelope:
    matches = tuple(
        envelope
        for envelope in ledger.receipts()
        if envelope.kind == kind
        and envelope.account_id == f"{transition_id}.transition"
        and envelope.payload_hash == payload_ref
    )
    if len(matches) != 1:
        raise Calibration002TransitionError(f"review ledger requires one exact {kind} receipt")
    return matches[0]


def _verify_source_ledger(source: Calibration002SourceTransition, ledger_path: Path) -> None:
    with SQLiteLedger(ledger_path, readonly=True) as ledger:
        ledger.verify()
        workspace = KevinSpeakWorkspace.restore(
            ledger,
            workspace_id=source.source_binding.frozen_stack.workspace_id,
            account_id=source.source_binding.account_id,
        )
        if workspace.verify() != source.source_workspace_verification:
            raise Calibration002TransitionError("source workspace verification changed")
        rebuilt = workspace.export_recommendation_bundle(
            recommendation_ref=source.recommendation_ref,
            source_run_seal_ref=source.run_seal.digest,
            source_capsule_ref=source.source_capsule_ref,
        )
        if rebuilt != source.recommendation_bundle:
            raise Calibration002TransitionError("source recommendation bundle changed")
        closures = tuple(
            Calibration002StageClosure.model_validate(ledger.get_payload(item.payload_hash))
            for item in ledger.receipts()
            if item.kind == "calibration_002_stage_closed"
        )
        if sum(item.handoff_ref == source.handoff.digest for item in closures) != 1:
            raise Calibration002TransitionError("source handoff lacks one durable closure")


def _verify_transfer_exclusions(outcome: Calibration002TransitionOutcome) -> None:
    if (
        outcome.shorthand_transfer is not None
        and outcome.shorthand_transfer.excluded_material
        != (
            "action_sequences",
            "authority",
            "domain_state",
            "private_reasoning",
        )
    ):
        raise Calibration002TransitionError("shorthand transfer exclusions changed")
    if outcome.inheritance is not None:
        if outcome.inheritance.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL:
            raise Calibration002TransitionError("inheritance exclusions changed")
        if outcome.inheritance.curriculum_transfer.excluded_material != (
            "action_sequences",
            "authorization",
            "domain_state",
            "private_reasoning",
        ):
            raise Calibration002TransitionError("learned-stack exclusions changed")


def apply_external_control(
    source: Calibration002SourceTransition,
    review: Calibration002AdvisoryReview,
    *,
    control: Calibration002ExternalControlDecision,
    target_configuration: KevinSpeakConfiguration,
    source_learning_ledger_path: str | Path,
    review_ledger_path: str | Path,
    artifact_root: str | Path,
    transfer_id: str,
    validation_refs: Sequence[str],
) -> Calibration002TransitionResult:
    """Apply external control and, only when approved, build exact inheritance."""

    _require_safe_id(transfer_id, "transition transfer identity")
    validation_tuple = tuple(sorted(set(validation_refs)))
    if len(validation_tuple) != len(tuple(validation_refs)) or not validation_tuple:
        raise Calibration002TransitionError(
            "transition validation references must be nonempty and unique"
        )
    for value in validation_tuple:
        _require_digest(value, "transition validation reference")
    if source.next_stage_ref is None:
        raise Calibration002TransitionError("source stage has no declared successor")
    if (
        review.source_transition_ref != source.digest
        or review.recommendation_ref != source.recommendation_ref
        or control.source_transition_ref != source.digest
        or control.advisory_review_ref != review.digest
        or control.recommendation_ref != source.recommendation_ref
        or control.target_stage_ref != source.next_stage_ref
        or control.target_configuration_ref != target_configuration.digest
    ):
        raise Calibration002TransitionError(
            "external control does not bind this exact source, review, and target"
        )
    recommendation_driver = source.recommendation_bundle.recommendation.recommending_driver_ref
    if control.control_source_ref in {
        recommendation_driver,
        review.request.reviewer_driver_ref,
    }:
        raise Calibration002TransitionError(
            "model recommendation or advisory review cannot authorize itself"
        )
    if (
        review.request.disposition is not AdvisoryDisposition.ACCEPT
        and control.status is AdoptionStatus.APPROVED
    ):
        raise Calibration002TransitionError(
            "external control cannot approve a rejected or deferred advisory review"
        )
    _verify_source_ledger(source, Path(source_learning_ledger_path))
    ledger_path = Path(review_ledger_path)
    with SQLiteLedger(ledger_path) as ledger:
        ledger.verify()
        advisory_receipt = _find_receipt(
            ledger,
            kind="calibration_002_advisory_review",
            payload_ref=review.digest,
            transition_id=source.transition_id,
        )
        workspace = KevinSpeakWorkspace.restore(
            ledger,
            workspace_id=review.review_workspace_id,
            account_id=review.review_account_id,
        )
        adoption = workspace.decide_next_round_adoption(
            adoption_id=control.decision_id,
            recommendation_ref=source.recommendation_ref,
            target_stage_ref=control.target_stage_ref,
            control_source_ref=control.control_source_ref,
            approve=control.status is AdoptionStatus.APPROVED,
            rationale=control.rationale,
            review_ref=review.kevin_review.digest,
            target_configuration=target_configuration,
        )
        shorthand: KevinSpeakTransfer | None = None
        inheritance: Calibration002Inheritance | None = None
        if adoption.status is AdoptionStatus.APPROVED:
            shorthand = workspace.export_transfer(
                transfer_id=f"{transfer_id}.shorthand",
                adoption_ref=adoption.digest,
            )
            inheritance = Calibration002Inheritance.bind(
                transfer_id=f"{transfer_id}.learned-stack",
                predecessor_handoff=source.handoff,
                target_stage_ref=control.target_stage_ref,
                shorthand_transfer=shorthand,
                validation_refs=validation_tuple,
                mechanic_refs=tuple(item.digest for item in source.retained_mechanics),
            )
            disposition = TransitionDisposition.READY
        elif review.request.disposition is AdvisoryDisposition.DEFER:
            disposition = TransitionDisposition.DEFERRED
        else:
            disposition = TransitionDisposition.REJECTED
        outcome = Calibration002TransitionOutcome(
            source_transition_ref=source.digest,
            advisory_review_ref=review.digest,
            control_decision=control,
            adoption=adoption,
            disposition=disposition,
            shorthand_transfer=shorthand,
            inheritance=inheritance,
        )
        stored_refs = tuple(
            _put_contract(ledger, item)
            for item in (
                control,
                adoption,
                *((shorthand,) if shorthand is not None else ()),
                *((inheritance,) if inheritance is not None else ()),
                *((inheritance.curriculum_transfer,) if inheritance is not None else ()),
                outcome,
            )
        )
        _append_transition_receipt(
            ledger,
            occurrence_id=f"{source.transition_id}:external-control",
            kind="calibration_002_external_control",
            transition_id=source.transition_id,
            value=outcome,
            object_refs=stored_refs,
            parent_refs=(advisory_receipt.receipt_id,),
        )
        count, head = ledger.verify()
        projection_ref = ledger.projection_hash
        if head is None:  # pragma: no cover - genesis and transition receipts exist
            raise Calibration002TransitionError("review ledger lost its receipt head")
    _verify_transfer_exclusions(outcome)
    verification = Calibration002TransitionVerification(
        source_transition_ref=source.digest,
        advisory_review_ref=review.digest,
        outcome_ref=outcome.digest,
        target_stage_ref=control.target_stage_ref,
        review_ledger_receipt_count=count,
        review_ledger_receipt_head=head,
    )
    root = Path(artifact_root)
    _write_canonical(root, "control/external-control.json", control)
    _write_canonical(root, "control/adoption.json", adoption)
    if shorthand is not None and inheritance is not None:
        _write_canonical(root, "transfer/kevin-speak.json", shorthand)
        _write_canonical(root, "transfer/learned-stack.json", inheritance.curriculum_transfer)
        _write_canonical(root, "transfer/inheritance.json", inheritance)
    _write_canonical(root, "transition.outcome.json", outcome)
    _write_canonical(root, "transition.verification.json", verification)
    artifact_paths = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*.json")
            if path.name != _ARTIFACT_MANIFEST_PATH
        )
    )
    artifacts = tuple(_artifact(root, relative) for relative in artifact_paths)
    manifest = Calibration002TransitionManifest(
        transition_id=source.transition_id,
        source_transition_ref=source.digest,
        outcome_ref=outcome.digest,
        review_ledger_receipt_count=count,
        review_ledger_receipt_head=head,
        review_ledger_projection_ref=projection_ref,
        artifacts=artifacts,
    )
    _write_canonical(root, _ARTIFACT_MANIFEST_PATH, manifest)
    return Calibration002TransitionResult(
        outcome=outcome,
        verification=verification,
        manifest=manifest,
    )


def verify_transition_result(
    source: Calibration002SourceTransition,
    review: Calibration002AdvisoryReview,
    result: Calibration002TransitionResult,
    *,
    source_learning_ledger_path: str | Path,
    review_ledger_path: str | Path,
    artifact_root: str | Path,
) -> Calibration002TransitionVerification:
    """Recheck source closure, review receipts, target bindings, and artifacts."""

    outcome = result.outcome
    if (
        outcome.source_transition_ref != source.digest
        or outcome.advisory_review_ref != review.digest
        or result.verification.outcome_ref != outcome.digest
        or result.manifest.outcome_ref != outcome.digest
    ):
        raise Calibration002TransitionError("transition result crosses sealed objects")
    _verify_source_ledger(source, Path(source_learning_ledger_path))
    _verify_transfer_exclusions(outcome)
    ledger_path = Path(review_ledger_path)
    with SQLiteLedger(ledger_path, readonly=True) as ledger:
        count, head = ledger.verify(
            expected_count=result.verification.review_ledger_receipt_count,
            expected_head=result.verification.review_ledger_receipt_head,
        )
        if head is None:  # pragma: no cover - expected positive receipt count
            raise Calibration002TransitionError("review ledger lost its receipt head")
        _find_receipt(
            ledger,
            kind="calibration_002_advisory_review",
            payload_ref=review.digest,
            transition_id=source.transition_id,
        )
        _find_receipt(
            ledger,
            kind="calibration_002_external_control",
            payload_ref=outcome.digest,
            transition_id=source.transition_id,
        )
        workspace = KevinSpeakWorkspace.restore(
            ledger,
            workspace_id=review.review_workspace_id,
            account_id=review.review_account_id,
        )
        if not any(
            item.digest == outcome.adoption.digest for item in workspace.adoption_decisions
        ):
            raise Calibration002TransitionError("review workspace lost its adoption decision")
        if result.manifest.review_ledger_projection_ref != ledger.projection_hash:
            raise Calibration002TransitionError("review ledger projection changed")
    root = Path(artifact_root)
    manifest_path = root / _ARTIFACT_MANIFEST_PATH
    if manifest_path.read_bytes() != canonical_bytes(result.manifest):
        raise Calibration002TransitionError("transition manifest bytes changed")
    for artifact in result.manifest.artifacts:
        if _artifact(root, artifact.relative_path) != artifact:
            raise Calibration002TransitionError(
                f"transition artifact changed: {artifact.relative_path}"
            )
    if count != result.verification.review_ledger_receipt_count:
        raise Calibration002TransitionError("review receipt count changed")
    return result.verification
