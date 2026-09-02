"""Receipt-backed Kevin Speak learning sidecar for Calibration 002.

This module coordinates representation learning only.  It has no environment
port, action API, game state, or execution authority.  Each stage receives a
fresh run-local Kevin Speak workspace; only an exact, reviewed shorthand
transfer may initialize a successor workspace.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from strongwiz.canonical import content_hash
from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt
from strongwiz.curriculum import (
    AdaptiveCurriculumController,
    AdaptiveCurriculumPlan,
    CurriculumCheckpoint,
    CurriculumMode,
    CurriculumStageHandoff,
    LearnedStackTransfer,
    four_stage_curriculum,
)
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger
from strongwiz.shorthand import (
    AdoptionStatus,
    EvaluationStatus,
    KevinCodebookEvaluation,
    KevinCodebookRevision,
    KevinEvaluationSample,
    KevinNextRoundRecommendation,
    KevinPromotionReceipt,
    KevinSpeakConfiguration,
    KevinSpeakEntry,
    KevinSpeakTransfer,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
    KevinTranslationTable,
    KevinWorkspaceVerification,
)

CALIBRATION_002_STAGE_MINUTES = (30, 60, 90, 300)
CALIBRATION_002_EXCLUDED_MATERIAL = (
    "action_sequences",
    "authority",
    "authorization",
    "domain_state",
    "private_reasoning",
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_PAYLOAD_FIELDS = frozenset(
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
        "private_reasoning",
        "raw_frame",
        "raw_frames",
    }
)
_FORBIDDEN_PAYLOAD_FIELDS_COMPACT = frozenset(
    field.replace("_", "") for field in _FORBIDDEN_PAYLOAD_FIELDS
)


class Calibration002LearningError(ValueError):
    """A campaign or run-local learning boundary failed closed."""


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_safe_id(value: str, label: str) -> str:
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{label} must use a safe stable identifier")
    return value


def _normalized_field(value: str) -> str:
    return _NON_ALNUM.sub("_", value.casefold()).strip("_")


def _reject_excluded_payload(value: object, *, path: str = "payload") -> None:
    """Reject structurally named material outside the learning sidecar's scope."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized = _normalized_field(key)
                if (
                    normalized in _FORBIDDEN_PAYLOAD_FIELDS
                    or normalized.replace("_", "")
                    in _FORBIDDEN_PAYLOAD_FIELDS_COMPACT
                ):
                    raise Calibration002LearningError(
                        f"{path} contains excluded field {key!r}"
                    )
            _reject_excluded_payload(item, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_excluded_payload(item, path=f"{path}[{index}]")


def calibration_002_learning_plan(
    *,
    success_condition_ref: str,
    campaign_id: str = "calibration-002",
    objective: str = "reach the declared terminal state",
    final_authority_source: str = "declared domain adapter terminal authority",
) -> AdaptiveCurriculumPlan:
    """Return the fixed 30/60/90/300-minute Calibration 002 curriculum."""

    plan = four_stage_curriculum(
        campaign_id=campaign_id,
        objective=objective,
        success_condition_ref=success_condition_ref,
        final_authority_source=final_authority_source,
        final_wall_minutes=300,
    )
    _require_exact_plan(plan)
    return plan


def _require_exact_plan(plan: AdaptiveCurriculumPlan) -> None:
    wall_clock_ms = tuple(stage.resource_budget.wall_clock_ms for stage in plan.stages)
    modes = tuple(stage.mode for stage in plan.stages)
    if wall_clock_ms != tuple(
        minutes * 60_000 for minutes in CALIBRATION_002_STAGE_MINUTES
    ):
        raise Calibration002LearningError("campaign plan is not the fixed 30/60/90/300 plan")
    if modes != (
        CurriculumMode.BASELINE,
        CurriculumMode.ACQUIRE,
        CurriculumMode.DEEPEN,
        CurriculumMode.FINISH_OR_REASSESS,
    ):
        raise Calibration002LearningError("campaign plan has the wrong four-stage order")
    if plan.authority != "NONE":
        raise Calibration002LearningError("campaign plan cannot carry authority")


class Calibration002CampaignGenesis(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-learning-genesis.v1", alias="schema"
    )
    campaign_ref: str
    initial_checkpoint_ref: str
    stage_wall_minutes: tuple[int, int, int, int] = CALIBRATION_002_STAGE_MINUTES
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    transfers_authority: bool = False
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_genesis(self) -> Calibration002CampaignGenesis:
        _require_digest(self.campaign_ref, "campaign plan reference")
        _require_digest(self.initial_checkpoint_ref, "initial controller checkpoint")
        if self.stage_wall_minutes != CALIBRATION_002_STAGE_MINUTES:
            raise ValueError("Calibration 002 requires the fixed 30/60/90/300 plan")
        if (
            self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL
            or self.transfers_authority
            or self.authority != "NONE"
        ):
            raise ValueError("learning genesis cannot carry excluded state or authority")
        return self


class Calibration002Inheritance(ContractModel):
    """One exact shorthand-plus-curriculum transfer for a successor stage."""

    schema_id: str = Field(default="strongwiz.calibration-002-inheritance.v1", alias="schema")
    curriculum_transfer: LearnedStackTransfer
    shorthand_transfer: KevinSpeakTransfer
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    transfers_authority: bool = False
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_inheritance(self) -> Calibration002Inheritance:
        curriculum = self.curriculum_transfer
        shorthand = self.shorthand_transfer
        if curriculum.shorthand_transfer_ref != shorthand.digest:
            raise ValueError("curriculum transfer does not bind the exact shorthand transfer")
        if curriculum.shorthand_adoption_ref != shorthand.adoption.digest:
            raise ValueError("curriculum transfer does not bind the exact shorthand adoption")
        if curriculum.source_run_seal_ref != shorthand.source_run_seal_ref:
            raise ValueError("curriculum and shorthand transfers cross source run seals")
        if curriculum.target_stage_ref != shorthand.adoption.target_stage_ref:
            raise ValueError("curriculum and shorthand transfers cross target stages")
        if shorthand.adoption.status is not AdoptionStatus.APPROVED:
            raise ValueError("successor shorthand was not approved")
        if (
            self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL
            or self.transfers_authority
            or self.authority != "NONE"
        ):
            raise ValueError("learning inheritance cannot carry excluded state or authority")
        return self

    @classmethod
    def bind(
        cls,
        *,
        transfer_id: str,
        predecessor_handoff: CurriculumStageHandoff,
        target_stage_ref: str,
        shorthand_transfer: KevinSpeakTransfer,
        validation_refs: tuple[str, ...],
        mechanic_refs: tuple[str, ...] = (),
        other_learned_fact_refs: tuple[str, ...] = (),
    ) -> Calibration002Inheritance:
        """Bind a portable shorthand transfer to one exact curriculum successor."""

        transfer = LearnedStackTransfer(
            transfer_id=transfer_id,
            source_stage_handoff_ref=predecessor_handoff.digest,
            source_run_seal_ref=predecessor_handoff.run_seal_ref,
            target_stage_ref=target_stage_ref,
            shorthand_transfer_ref=shorthand_transfer.digest,
            shorthand_adoption_ref=shorthand_transfer.adoption.digest,
            mechanic_refs=mechanic_refs,
            other_learned_fact_refs=other_learned_fact_refs,
            validation_refs=validation_refs,
        )
        return cls(curriculum_transfer=transfer, shorthand_transfer=shorthand_transfer)


class Calibration002FrozenLearningStack(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-learning-stack.v1", alias="schema"
    )
    stage_ref: str
    run_scope_ref: str
    workspace_id: str
    configuration_ref: str
    initial_codebook_ref: str
    inheritance_ref: str | None = None
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_stack(self) -> Calibration002FrozenLearningStack:
        for value in (
            self.stage_ref,
            self.run_scope_ref,
            self.configuration_ref,
            self.initial_codebook_ref,
        ):
            _require_digest(value, "frozen learning-stack binding")
        _require_safe_id(self.workspace_id, "workspace identity")
        if self.inheritance_ref is not None:
            _require_digest(self.inheritance_ref, "learning inheritance")
        if (
            self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL
            or self.authority != "NONE"
        ):
            raise ValueError("frozen learning stack cannot carry excluded state or authority")
        return self


class Calibration002StageBinding(ContractModel):
    schema_id: str = Field(default="strongwiz.calibration-002-stage-binding.v1", alias="schema")
    campaign_ref: str
    stage_start_ref: str
    run_id: str
    account_id: str
    workspace_mode: Literal["blank", "explicit_inheritance"]
    frozen_stack: Calibration002FrozenLearningStack
    curriculum_transfer_ref: str | None = None
    shorthand_transfer_ref: str | None = None
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_binding(self) -> Calibration002StageBinding:
        _require_digest(self.campaign_ref, "stage campaign")
        _require_digest(self.stage_start_ref, "curriculum stage start")
        _require_safe_id(self.run_id, "stage run identity")
        _require_safe_id(self.account_id, "stage account identity")
        inherited = self.workspace_mode == "explicit_inheritance"
        if inherited != (self.curriculum_transfer_ref is not None):
            raise ValueError("only inherited stages may bind a curriculum transfer")
        if inherited != (self.shorthand_transfer_ref is not None):
            raise ValueError("only inherited stages may bind a shorthand transfer")
        for value in (self.curriculum_transfer_ref, self.shorthand_transfer_ref):
            if value is not None:
                _require_digest(value, "stage inheritance binding")
        if inherited != (self.frozen_stack.inheritance_ref is not None):
            raise ValueError("stage binding and frozen stack disagree about inheritance")
        if self.authority != "NONE":
            raise ValueError("stage learning binding grants no authority")
        return self


class Calibration002StageClosure(ContractModel):
    schema_id: str = Field(default="strongwiz.calibration-002-stage-closure.v1", alias="schema")
    stage_binding_ref: str
    handoff_ref: str
    workspace_verification_ref: str
    authority: str = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_closure(self) -> Calibration002StageClosure:
        for value in (
            self.stage_binding_ref,
            self.handoff_ref,
            self.workspace_verification_ref,
        ):
            _require_digest(value, "stage closure binding")
        if self.authority != "EVIDENCE_ONLY":
            raise ValueError("stage closure is evidence only")
        return self


class Calibration002CheckpointRecord(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-checkpoint-record.v1", alias="schema"
    )
    campaign_ref: str
    checkpoint_ref: str
    stage_binding_refs: tuple[str, ...]
    active_stage_binding_ref: str | None = None
    reason: Literal["campaign_created", "stage_started", "stage_finished"]
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_record(self) -> Calibration002CheckpointRecord:
        _require_digest(self.campaign_ref, "checkpoint campaign")
        _require_digest(self.checkpoint_ref, "controller checkpoint")
        if len(set(self.stage_binding_refs)) != len(self.stage_binding_refs):
            raise ValueError("checkpoint stage bindings must be unique")
        for value in self.stage_binding_refs:
            _require_digest(value, "checkpoint stage binding")
        if self.active_stage_binding_ref is not None:
            _require_digest(self.active_stage_binding_ref, "active stage binding")
            if (
                not self.stage_binding_refs
                or self.stage_binding_refs[-1] != self.active_stage_binding_ref
            ):
                raise ValueError("active stage must be the latest stage binding")
        if self.authority != "NONE":
            raise ValueError("controller checkpoint grants no authority")
        return self


class Calibration002Adaptation(ContractModel):
    candidate: KevinCodebookRevision
    evaluation: KevinCodebookEvaluation
    promotion: KevinPromotionReceipt | None = None
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_adaptation(self) -> Calibration002Adaptation:
        if self.authority != "NONE":
            raise ValueError("learning adaptation grants no authority")
        return self


class Calibration002LearningVerification(ContractModel):
    schema_id: str = Field(
        default="strongwiz.calibration-002-learning-verification.v1", alias="schema"
    )
    campaign_ref: str
    checkpoint_ref: str
    stage_wall_minutes: tuple[int, int, int, int]
    stage_binding_count: NonNegativeInt
    completed_stage_count: NonNegativeInt
    active_stage_binding_ref: str | None
    workspace_verifications: tuple[KevinWorkspaceVerification, ...]
    ledger_receipt_count: PositiveInt
    ledger_receipt_head: str
    source_payload_refs_run_local: bool = True
    excluded_material: tuple[str, ...] = CALIBRATION_002_EXCLUDED_MATERIAL
    transfers_authority: bool = False
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_verification(self) -> Calibration002LearningVerification:
        _require_digest(self.campaign_ref, "verified campaign")
        _require_digest(self.checkpoint_ref, "verified checkpoint")
        _require_digest(self.ledger_receipt_head, "verified ledger head")
        if self.active_stage_binding_ref is not None:
            _require_digest(self.active_stage_binding_ref, "verified active stage")
        if self.stage_wall_minutes != CALIBRATION_002_STAGE_MINUTES:
            raise ValueError("verification does not cover the fixed campaign plan")
        if not self.source_payload_refs_run_local:
            raise ValueError("workspace contains a cross-run source payload reference")
        if (
            self.excluded_material != CALIBRATION_002_EXCLUDED_MATERIAL
            or self.transfers_authority
            or self.authority != "NONE"
        ):
            raise ValueError("learning verification cannot carry excluded state or authority")
        return self


class Calibration002LearningSidecar:
    """Persistent campaign controller plus one active run-local Kevin workspace."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger,
        ledger_path: Path,
        plan: AdaptiveCurriculumPlan,
        controller: AdaptiveCurriculumController,
        campaign_head: str | None,
        campaign_sequence: int,
        checkpoint_ref: str,
        checkpoint_record: Calibration002CheckpointRecord | None,
        bindings: list[Calibration002StageBinding],
        binding_refs: list[str],
        workspace: KevinSpeakWorkspace | None,
    ) -> None:
        self._ledger = ledger
        self.ledger_path = ledger_path
        self.plan = plan
        self.controller = controller
        self._campaign_head = campaign_head
        self._campaign_sequence = campaign_sequence
        self._checkpoint_ref = checkpoint_ref
        self._checkpoint_record = checkpoint_record
        self._bindings = bindings
        self._binding_refs = binding_refs
        self._workspace = workspace
        self._closed = False

    @classmethod
    def create(
        cls,
        ledger_path: str | Path,
        *,
        success_condition_ref: str,
        campaign_id: str = "calibration-002",
        objective: str = "reach the declared terminal state",
        final_authority_source: str = "declared domain adapter terminal authority",
    ) -> Calibration002LearningSidecar:
        """Create an empty campaign ledger and persist plan plus checkpoint genesis."""

        path = Path(ledger_path)
        ledger = SQLiteLedger(path)
        try:
            if tuple(ledger.receipts()):
                raise Calibration002LearningError("campaign ledger is not empty")
            plan = calibration_002_learning_plan(
                success_condition_ref=success_condition_ref,
                campaign_id=campaign_id,
                objective=objective,
                final_authority_source=final_authority_source,
            )
            controller = AdaptiveCurriculumController(plan)
            checkpoint = controller.checkpoint()
            plan_ref = ledger.put_object(plan.model_dump(mode="json", by_alias=True))
            checkpoint_ref = ledger.put_object(
                checkpoint.model_dump(mode="json", by_alias=True)
            )
            sidecar = cls(
                ledger=ledger,
                ledger_path=path,
                plan=plan,
                controller=controller,
                campaign_head=None,
                campaign_sequence=0,
                checkpoint_ref=checkpoint_ref,
                checkpoint_record=None,
                bindings=[],
                binding_refs=[],
                workspace=None,
            )
            genesis = Calibration002CampaignGenesis(
                campaign_ref=plan_ref,
                initial_checkpoint_ref=checkpoint_ref,
            )
            sidecar._record_campaign(
                "calibration_002_campaign_genesis",
                genesis,
                extra_object_refs=(plan_ref, checkpoint_ref),
            )
            sidecar._persist_checkpoint("campaign_created")
            return sidecar
        except Exception:
            ledger.close()
            raise

    @classmethod
    def restore(
        cls,
        ledger_path: str | Path,
        *,
        campaign_id: str = "calibration-002",
    ) -> Calibration002LearningSidecar:
        """Restore the last exact controller and active workspace from its ledger."""

        path = Path(ledger_path)
        ledger = SQLiteLedger(path)
        try:
            ledger.verify()
            selected = cls._campaign_receipts(ledger, campaign_id)
            if not selected:
                raise Calibration002LearningError("campaign ledger has no learning genesis")
            for index, envelope in enumerate(selected):
                expected = (
                    f"{campaign_id}.learning:{index:08d}:{envelope.kind}"
                )
                if envelope.occurrence_id != expected:
                    raise Calibration002LearningError("campaign receipt sequence is invalid")
                expected_parent = () if index == 0 else (selected[index - 1].receipt_id,)
                if envelope.parent_refs != expected_parent:
                    raise Calibration002LearningError("campaign receipt lineage is broken")

            first = selected[0]
            if first.kind != "calibration_002_campaign_genesis":
                raise Calibration002LearningError("campaign does not begin at learning genesis")
            genesis = Calibration002CampaignGenesis.model_validate(
                ledger.get_payload(first.payload_hash)
            )
            plan = AdaptiveCurriculumPlan.model_validate(
                ledger.get_payload(genesis.campaign_ref)
            )
            if plan.digest != genesis.campaign_ref or plan.campaign_id != campaign_id:
                raise Calibration002LearningError("campaign plan identity changed")
            _require_exact_plan(plan)

            bindings: list[Calibration002StageBinding] = []
            binding_refs: list[str] = []
            latest_record: Calibration002CheckpointRecord | None = None
            for envelope in selected[1:]:
                payload = ledger.get_payload(envelope.payload_hash)
                if envelope.kind == "calibration_002_stage_opened":
                    binding = Calibration002StageBinding.model_validate(payload)
                    bindings.append(binding)
                    binding_refs.append(binding.digest)
                elif envelope.kind == "calibration_002_stage_closed":
                    Calibration002StageClosure.model_validate(payload)
                elif envelope.kind == "calibration_002_controller_checkpoint":
                    latest_record = Calibration002CheckpointRecord.model_validate(payload)
                else:
                    raise Calibration002LearningError(
                        "campaign contains an unknown receipt kind"
                    )
            if latest_record is None:
                raise Calibration002LearningError("campaign has no controller checkpoint")
            if latest_record.stage_binding_refs != tuple(binding_refs):
                raise Calibration002LearningError("checkpoint omits or reorders stage bindings")
            checkpoint = CurriculumCheckpoint.model_validate(
                ledger.get_payload(latest_record.checkpoint_ref)
            )
            if checkpoint.digest != latest_record.checkpoint_ref or checkpoint.plan != plan:
                raise Calibration002LearningError("controller checkpoint identity changed")
            controller = AdaptiveCurriculumController.restore(checkpoint)
            workspace: KevinSpeakWorkspace | None = None
            if latest_record.active_stage_binding_ref is not None:
                active = bindings[-1]
                if active.digest != latest_record.active_stage_binding_ref:
                    raise Calibration002LearningError("active stage binding identity changed")
                workspace = KevinSpeakWorkspace.restore(
                    ledger,
                    workspace_id=active.frozen_stack.workspace_id,
                    account_id=active.account_id,
                )
            sidecar = cls(
                ledger=ledger,
                ledger_path=path,
                plan=plan,
                controller=controller,
                campaign_head=selected[-1].receipt_id,
                campaign_sequence=len(selected),
                checkpoint_ref=latest_record.checkpoint_ref,
                checkpoint_record=latest_record,
                bindings=bindings,
                binding_refs=binding_refs,
                workspace=workspace,
            )
            sidecar.verify()
            return sidecar
        except Exception:
            ledger.close()
            raise

    @staticmethod
    def _campaign_receipts(
        ledger: SQLiteLedger, campaign_id: str
    ) -> list[ReceiptEnvelope]:
        account_id = f"{campaign_id}.learning-campaign"
        prefix = f"{campaign_id}.learning:"
        return [
            envelope
            for envelope in ledger.receipts()
            if envelope.account_id == account_id
            and envelope.account_version == 0
            and envelope.occurrence_id.startswith(prefix)
        ]

    def _assert_open(self) -> None:
        if self._closed:
            raise Calibration002LearningError("learning sidecar is closed")

    def _record_campaign(
        self,
        kind: str,
        value: ContractModel,
        *,
        extra_object_refs: tuple[str, ...] = (),
    ) -> str:
        value_ref = self._ledger.put_object(value.model_dump(mode="json", by_alias=True))
        if value_ref != value.digest:
            raise Calibration002LearningError(
                "campaign contract identity changed during storage"
            )
        envelope = self._ledger.append(
            occurrence_id=(
                f"{self.plan.campaign_id}.learning:{self._campaign_sequence:08d}:{kind}"
            ),
            kind=kind,
            account_id=f"{self.plan.campaign_id}.learning-campaign",
            account_version=0,
            payload=value.model_dump(mode="json", by_alias=True),
            object_refs=tuple(dict.fromkeys((value_ref, *extra_object_refs))),
            parent_refs=() if self._campaign_head is None else (self._campaign_head,),
        )
        self._campaign_head = envelope.receipt_id
        self._campaign_sequence += 1
        return value_ref

    def _persist_checkpoint(
        self, reason: Literal["campaign_created", "stage_started", "stage_finished"]
    ) -> Calibration002CheckpointRecord:
        checkpoint = self.controller.checkpoint()
        checkpoint_ref = self._ledger.put_object(
            checkpoint.model_dump(mode="json", by_alias=True)
        )
        active_ref = None if self._workspace is None else self._binding_refs[-1]
        record = Calibration002CheckpointRecord(
            campaign_ref=self.plan.digest,
            checkpoint_ref=checkpoint_ref,
            stage_binding_refs=tuple(self._binding_refs),
            active_stage_binding_ref=active_ref,
            reason=reason,
        )
        self._record_campaign(
            "calibration_002_controller_checkpoint",
            record,
            extra_object_refs=(self.plan.digest, checkpoint_ref, *self._binding_refs),
        )
        self._checkpoint_ref = checkpoint_ref
        self._checkpoint_record = record
        return record

    @property
    def checkpoint(self) -> CurriculumCheckpoint:
        return self.controller.checkpoint()

    @property
    def active_binding(self) -> Calibration002StageBinding | None:
        return None if self._workspace is None else self._bindings[-1]

    def open_stage(
        self,
        *,
        run_id: str,
        inheritance: Calibration002Inheritance | None = None,
        configuration: KevinSpeakConfiguration | None = None,
    ) -> Calibration002StageBinding:
        """Open the next stage blank at baseline or through exact inheritance."""

        self._assert_open()
        _require_safe_id(run_id, "stage run identity")
        if self._workspace is not None or self.controller.active_start is not None:
            raise Calibration002LearningError("one learning stage is already active")
        if run_id in {binding.run_id for binding in self._bindings}:
            raise Calibration002LearningError("stage run identity cannot be reused")
        index = len(self.controller.checkpoint().completed_handoffs)
        if index >= len(self.plan.stages):
            raise Calibration002LearningError("campaign has no remaining stage")
        stage = self.plan.stages[index]
        workspace_id = f"{self.plan.campaign_id}.s{index + 1}.{run_id}.kevin"
        account_id = f"{self.plan.campaign_id}.s{index + 1}.{run_id}"
        run_scope_ref = content_hash(
            {
                "campaign_ref": self.plan.digest,
                "run_id": run_id,
                "stage_ref": stage.digest,
            }
        )
        preview = AdaptiveCurriculumController.restore(self.controller.checkpoint())
        if index == 0:
            if inheritance is not None:
                raise Calibration002LearningError("stage 1 must open a blank workspace")
            active_configuration = configuration or KevinSpeakConfiguration()
            blank = KevinCodebookRevision.blank(
                codebook_id=f"{self.plan.campaign_id}.kevin-speak"
            )
            stack = Calibration002FrozenLearningStack(
                stage_ref=stage.digest,
                run_scope_ref=run_scope_ref,
                workspace_id=workspace_id,
                configuration_ref=active_configuration.digest,
                initial_codebook_ref=blank.digest,
            )
            start = preview.start_next(frozen_stack_ref=stack.digest)
            workspace = KevinSpeakWorkspace.open_blank(
                self._ledger,
                workspace_id=workspace_id,
                account_id=account_id,
                codebook_id=blank.codebook_id,
                configuration=active_configuration,
            )
            mode: Literal["blank", "explicit_inheritance"] = "blank"
            curriculum_transfer_ref = None
            shorthand_transfer_ref = None
            extra_refs: tuple[str, ...] = ()
        else:
            if configuration is not None:
                raise Calibration002LearningError(
                    "successor configuration must come from the exact inherited transfer"
                )
            if inheritance is None:
                raise Calibration002LearningError(
                    "successor stage requires an exact target-bound inheritance"
                )
            predecessor = self.controller.checkpoint().completed_handoffs[-1]
            prior_binding = self._bindings[-1]
            curriculum = inheritance.curriculum_transfer
            shorthand = inheritance.shorthand_transfer
            if (
                curriculum.source_stage_handoff_ref != predecessor.digest
                or curriculum.source_run_seal_ref != predecessor.run_seal_ref
                or curriculum.target_stage_ref != stage.digest
                or shorthand.source_workspace_id != prior_binding.frozen_stack.workspace_id
                or shorthand.source_run_seal_ref != predecessor.run_seal_ref
                or shorthand.adoption.target_stage_ref != stage.digest
            ):
                raise Calibration002LearningError(
                    "successor inheritance crosses its exact source or target boundary"
                )
            stack = Calibration002FrozenLearningStack(
                stage_ref=stage.digest,
                run_scope_ref=run_scope_ref,
                workspace_id=workspace_id,
                configuration_ref=shorthand.target_configuration.digest,
                initial_codebook_ref=shorthand.active_codebook_ref,
                inheritance_ref=inheritance.digest,
            )
            start = preview.start_next(
                frozen_stack_ref=stack.digest,
                transfer=curriculum,
            )
            workspace = KevinSpeakWorkspace.open_inherited(
                self._ledger,
                workspace_id=workspace_id,
                account_id=account_id,
                target_stage_ref=stage.digest,
                transfer=shorthand,
            )
            mode = "explicit_inheritance"
            curriculum_transfer_ref = curriculum.digest
            shorthand_transfer_ref = shorthand.digest
            extra_refs = (
                inheritance.digest,
                curriculum.digest,
                shorthand.digest,
                shorthand.adoption.digest,
            )
            inheritance_objects = (
                inheritance,
                curriculum,
                shorthand,
                shorthand.adoption,
            )
            for inheritance_object in inheritance_objects:
                stored = self._ledger.put_object(
                    inheritance_object.model_dump(mode="json", by_alias=True)
                )
                if stored != inheritance_object.digest:
                    raise Calibration002LearningError(
                        "inheritance identity changed during storage"
                    )

        binding = Calibration002StageBinding(
            campaign_ref=self.plan.digest,
            stage_start_ref=start.digest,
            run_id=run_id,
            account_id=account_id,
            workspace_mode=mode,
            frozen_stack=stack,
            curriculum_transfer_ref=curriculum_transfer_ref,
            shorthand_transfer_ref=shorthand_transfer_ref,
        )
        for stage_object in (stack, start):
            stored = self._ledger.put_object(
                stage_object.model_dump(mode="json", by_alias=True)
            )
            if stored != stage_object.digest:
                raise Calibration002LearningError("stage-start identity changed during storage")
        binding_ref = self._record_campaign(
            "calibration_002_stage_opened",
            binding,
            extra_object_refs=(stack.digest, start.digest, *extra_refs),
        )
        self.controller = preview
        self._workspace = workspace
        self._bindings.append(binding)
        self._binding_refs.append(binding_ref)
        self._persist_checkpoint("stage_started")
        return binding

    def _require_workspace(self) -> KevinSpeakWorkspace:
        self._assert_open()
        if self._workspace is None:
            raise Calibration002LearningError("no learning stage is active")
        return self._workspace

    def append(self, *, entry_id: str, payload: object) -> KevinSpeakEntry:
        """Append one non-authoritative learning payload to the active stage."""

        _reject_excluded_payload(payload)
        return self._require_workspace().append(entry_id=entry_id, payload=payload)

    def adapt(
        self,
        *,
        proposals: Sequence[KevinSymbolProposal],
        samples: Sequence[KevinEvaluationSample],
        rationale: str,
        evaluation_id: str,
        retired_tokens: tuple[str, ...] = (),
        model_proposal_ref: str | None = None,
        promote_if_eligible: bool = True,
    ) -> Calibration002Adaptation:
        """Evaluate an adaptation sourced only from this stage's appended payloads."""

        workspace = self._require_workspace()
        run_local_refs = {entry.source_payload_ref for entry in workspace.entries}
        for proposal in proposals:
            if not set(proposal.source_payload_refs) <= run_local_refs:
                raise Calibration002LearningError(
                    "symbol source payload references must be run-local appended entries"
                )
        for sample in samples:
            _reject_excluded_payload(sample.payload, path=f"evaluation[{sample.case_id}]")
        candidate = workspace.propose_revision(
            proposals=proposals,
            retired_tokens=retired_tokens,
            rationale=rationale,
            model_proposal_ref=model_proposal_ref,
        )
        evaluation = workspace.evaluate_candidate(
            candidate.digest,
            samples,
            evaluation_id=evaluation_id,
        )
        promotion = None
        if promote_if_eligible and evaluation.status is EvaluationStatus.ELIGIBLE:
            promotion = workspace.promote(
                candidate_ref=candidate.digest,
                evaluation_ref=evaluation.digest,
            )
        return Calibration002Adaptation(
            candidate=candidate,
            evaluation=evaluation,
            promotion=promotion,
        )

    def recommend(
        self,
        *,
        recommendation_id: str,
        recommending_driver_ref: str,
        evaluation_refs: Sequence[str],
        rationale: str,
        known_residuals: Sequence[str] = (),
    ) -> KevinNextRoundRecommendation:
        """Record a recommendation; this does not approve successor adoption."""

        return self._require_workspace().recommend_next_round(
            recommendation_id=recommendation_id,
            recommending_driver_ref=recommending_driver_ref,
            evaluation_refs=evaluation_refs,
            rationale=rationale,
            known_residuals=known_residuals,
        )

    def table(self) -> KevinTranslationTable:
        """Return the active exact translation table."""

        return self._require_workspace().translation_table()

    def finish_stage(self, handoff: CurriculumStageHandoff) -> CurriculumStageHandoff:
        """Close the active controller occurrence and persist its exact checkpoint."""

        workspace = self._require_workspace()
        binding = self._bindings[-1]
        verification = workspace.verify()
        if handoff.active_codebook_ref != verification.active_codebook_ref:
            raise Calibration002LearningError(
                "stage handoff does not bind the active Kevin Speak codebook"
            )
        preview = AdaptiveCurriculumController.restore(self.controller.checkpoint())
        closed = preview.finish_active(handoff)
        for closure_object in (handoff, verification):
            stored = self._ledger.put_object(
                closure_object.model_dump(mode="json", by_alias=True)
            )
            if stored != closure_object.digest:
                raise Calibration002LearningError(
                    "stage closure identity changed during storage"
                )
        closure = Calibration002StageClosure(
            stage_binding_ref=binding.digest,
            handoff_ref=handoff.digest,
            workspace_verification_ref=verification.digest,
        )
        self._record_campaign(
            "calibration_002_stage_closed",
            closure,
            extra_object_refs=(binding.digest, handoff.digest, verification.digest),
        )
        self.controller = preview
        self._workspace = None
        self._persist_checkpoint("stage_finished")
        return closed

    def _verify_workspace_sources(
        self, binding: Calibration002StageBinding
    ) -> KevinWorkspaceVerification:
        workspace = KevinSpeakWorkspace.restore(
            self._ledger,
            workspace_id=binding.frozen_stack.workspace_id,
            account_id=binding.account_id,
        )
        entries = iter(workspace.entries)
        seen_source_refs: set[str] = set()
        selected = [
            envelope
            for envelope in self._ledger.receipts()
            if envelope.account_id == binding.account_id
            and envelope.account_version == 0
            and envelope.occurrence_id.startswith(
                f"{binding.frozen_stack.workspace_id}:"
            )
        ]
        for envelope in selected:
            payload = self._ledger.get_payload(envelope.payload_hash)
            if envelope.kind == "kevin_entry":
                entry = next(entries)
                if KevinSpeakEntry.model_validate(payload) != entry:
                    raise Calibration002LearningError("workspace entry identity changed")
                _reject_excluded_payload(
                    workspace.decode_entry(entry), path=f"entry[{entry.entry_id}]"
                )
                seen_source_refs.add(entry.source_payload_ref)
            elif envelope.kind == "kevin_codebook_candidate":
                candidate = KevinCodebookRevision.model_validate(payload)
                for definition in candidate.definitions:
                    if not set(definition.source_payload_refs) <= seen_source_refs:
                        raise Calibration002LearningError(
                            "workspace contains a cross-run source payload reference"
                        )
        return workspace.verify()

    def verify(self) -> Calibration002LearningVerification:
        """Verify ledger, campaign checkpoint, target lineage, and run-local sources."""

        self._assert_open()
        _require_exact_plan(self.plan)
        receipt_count, receipt_head = self._ledger.verify()
        if receipt_head is None:
            raise Calibration002LearningError("campaign ledger has no receipt head")
        if self._checkpoint_record is None:
            raise Calibration002LearningError("campaign has no persisted checkpoint record")
        if self.controller.checkpoint().digest != self._checkpoint_ref:
            raise Calibration002LearningError("in-memory controller differs from checkpoint")
        if self._checkpoint_record.checkpoint_ref != self._checkpoint_ref:
            raise Calibration002LearningError("latest checkpoint record changed identity")
        if self._checkpoint_record.stage_binding_refs != tuple(self._binding_refs):
            raise Calibration002LearningError("latest checkpoint omits stage bindings")
        expected_active = None if self._workspace is None else self._binding_refs[-1]
        if self._checkpoint_record.active_stage_binding_ref != expected_active:
            raise Calibration002LearningError("latest checkpoint has the wrong active stage")
        completed = self.controller.checkpoint().completed_handoffs
        expected_bindings = len(completed) + (1 if self._workspace is not None else 0)
        if len(self._bindings) != expected_bindings:
            raise Calibration002LearningError("stage bindings disagree with controller history")
        for index, binding in enumerate(self._bindings):
            stage = self.plan.stages[index]
            if (
                binding.campaign_ref != self.plan.digest
                or binding.frozen_stack.stage_ref != stage.digest
            ):
                raise Calibration002LearningError("stage binding crosses the campaign plan")
            if index == 0:
                if binding.workspace_mode != "blank":
                    raise Calibration002LearningError("stage 1 workspace is not blank")
                continue
            inheritance_ref = binding.frozen_stack.inheritance_ref
            if inheritance_ref is None:
                raise Calibration002LearningError("successor stage lost its inheritance")
            inheritance = Calibration002Inheritance.model_validate(
                self._ledger.get_payload(inheritance_ref)
            )
            predecessor = completed[index - 1]
            prior_binding = self._bindings[index - 1]
            if (
                inheritance.digest != inheritance_ref
                or inheritance.curriculum_transfer.source_stage_handoff_ref
                != predecessor.digest
                or inheritance.curriculum_transfer.target_stage_ref != stage.digest
                or inheritance.shorthand_transfer.source_workspace_id
                != prior_binding.frozen_stack.workspace_id
            ):
                raise Calibration002LearningError("successor inheritance lineage changed")
        workspaces = tuple(
            self._verify_workspace_sources(binding) for binding in self._bindings
        )
        return Calibration002LearningVerification(
            campaign_ref=self.plan.digest,
            checkpoint_ref=self._checkpoint_ref,
            stage_wall_minutes=CALIBRATION_002_STAGE_MINUTES,
            stage_binding_count=len(self._bindings),
            completed_stage_count=len(completed),
            active_stage_binding_ref=expected_active,
            workspace_verifications=workspaces,
            ledger_receipt_count=receipt_count,
            ledger_receipt_head=receipt_head,
        )

    def close(self) -> None:
        if not self._closed:
            self._ledger.close()
            self._closed = True

    def __enter__(self) -> Calibration002LearningSidecar:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
