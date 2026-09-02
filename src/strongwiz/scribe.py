"""A representation-only scribe between a reasoning model and Kevin Speak.

The scribe receives concise, receipt-bound working material.  It has no action
port, no domain state, and no authority.  It may propose a reversible notation;
Strongwiz alone applies the existing exact decoder, disjoint evaluation, cost,
and promotion gates.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, model_validator

from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt
from strongwiz.ledger import SQLiteLedger
from strongwiz.pal23 import BoundaryAdapter, StateProjection
from strongwiz.shorthand import (
    CodebookRegistry,
    EvaluationRole,
    EvaluationStatus,
    KevinCodebookEvaluation,
    KevinCodebookRevision,
    KevinEvaluationCase,
    KevinEvaluationSample,
    KevinPromotionReceipt,
    KevinSpeakEntry,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
    decode_shorthand_text,
    encode_shorthand_text,
)

SCRIBE_SCHEMA = "strongwiz.scribe.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ScribeError(ValueError):
    """The representation-only boundary failed closed."""


class ScribeMaterialKind(StrEnum):
    DECISION_SUMMARY = "decision_summary"
    OUTCOME_SUMMARY = "outcome_summary"
    RESIDUAL_SUMMARY = "residual_summary"
    MECHANIC_SUMMARY = "mechanic_summary"
    CHECKPOINT_SUMMARY = "checkpoint_summary"


class ScribeEvidenceStatus(StrEnum):
    OBSERVED = "observed"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"
    DEFERRED = "deferred"
    REOPENED = "reopened"


class ScribeTrigger(StrEnum):
    MATERIAL_THRESHOLD = "material_threshold"
    STAGE_BOUNDARY = "stage_boundary"
    REASSESSMENT = "reassessment"


class ScribeCycleStatus(StrEnum):
    DEFERRED = "deferred"
    NO_CANDIDATE = "no_candidate"
    NOT_EARNED = "not_earned"
    PROMOTED = "promoted"
    FAILED = "failed"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_safe_id(value: str, label: str) -> str:
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{label} must use a safe stable identifier")
    return value


def _require_canonical_identity(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or value != normalized:
        raise ValueError(f"{label} must be nonempty canonical NFKC text without padding")
    return value


def _require_sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _evaluation_id(request: ScribeRequest) -> str:
    """Return a globally collision-resistant identity for one frozen request."""

    return f"{request.session_id}.{request.request_id}.{request.digest}.kevin-evaluation"


def _entry_id(session_id: str, material_id: str) -> str:
    """Return an injective content identity for one session-owned material."""

    identity_ref = content_hash(
        {
            "material_id": material_id,
            "namespace": "strongwiz.scribe.material-entry.v1",
            "session_id": session_id,
        }
    )
    return f"scribe-material.{identity_ref}"


class ScribeEvidenceAtom(ContractModel):
    """Closed derived-evidence shape; truthfulness remains source-owned."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    atom_id: str
    statement: str
    status: ScribeEvidenceStatus
    uncertainty: str
    goal_relevance: str
    reopening_condition: str
    predecessor_refs: tuple[str, ...] = ()
    counterevidence_refs: tuple[str, ...] = ()
    source_is_derived_summary: Literal[True] = True
    contains_private_reasoning: Literal[False] = False
    contains_raw_frame: Literal[False] = False
    contains_domain_state: Literal[False] = False
    contains_action_sequence: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_atom(self) -> ScribeEvidenceAtom:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.atom_id, "scribe evidence atom identity")
        for label, value in (
            ("statement", self.statement),
            ("uncertainty", self.uncertainty),
            ("goal relevance", self.goal_relevance),
            ("reopening condition", self.reopening_condition),
        ):
            if not value.strip():
                raise ValueError(f"scribe evidence atom {label} is required")
        for label, values in (
            ("predecessor references", self.predecessor_refs),
            ("counterevidence references", self.counterevidence_refs),
        ):
            _require_sorted_unique(values, f"scribe evidence atom {label}")
            for value in values:
                _require_digest(value, f"scribe evidence atom {label}")
        return self


class ScribeMaterialInput(ContractModel):
    """Ephemeral input containing one concise payload before Kevin storage."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    material_id: str
    ordinal: NonNegativeInt
    kind: ScribeMaterialKind
    scope_id: str
    payload: ScribeEvidenceAtom
    payload_ref: str
    projection_ref: str
    evidence_refs: tuple[str, ...]
    source_is_derived_summary: Literal[True] = True
    contains_private_reasoning: Literal[False] = False
    contains_raw_frame: Literal[False] = False
    contains_domain_state: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_material(self) -> ScribeMaterialInput:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.material_id, "scribe material identity")
        if not self.scope_id.strip():
            raise ValueError("scribe material scope is required")
        _require_digest(self.payload_ref, "scribe payload reference")
        _require_digest(self.projection_ref, "scribe projection reference")
        if content_hash(self.payload) != self.payload_ref:
            raise ValueError("scribe payload reference does not bind its canonical payload")
        _require_sorted_unique(self.evidence_refs, "scribe evidence references")
        if not self.evidence_refs:
            raise ValueError("scribe material requires receipt-bound evidence")
        for value in self.evidence_refs:
            _require_digest(value, "scribe evidence reference")
        return self


class ScribeMaterial(ContractModel):
    """Durable metadata pointing to one Kevin-stored, exactly decodable payload."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    material_id: str
    ordinal: NonNegativeInt
    kind: ScribeMaterialKind
    scope_id: str
    payload_ref: str
    entry_ref: str
    projection_ref: str
    evidence_refs: tuple[str, ...]
    source_is_derived_summary: Literal[True] = True
    contains_private_reasoning: Literal[False] = False
    contains_raw_frame: Literal[False] = False
    contains_domain_state: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_material(self) -> ScribeMaterial:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.material_id, "scribe material identity")
        if not self.scope_id.strip():
            raise ValueError("scribe material scope is required")
        for value in (self.payload_ref, self.entry_ref, self.projection_ref):
            _require_digest(value, "scribe material reference")
        _require_sorted_unique(self.evidence_refs, "scribe evidence references")
        if not self.evidence_refs:
            raise ValueError("scribe material requires receipt-bound evidence")
        for value in self.evidence_refs:
            _require_digest(value, "scribe evidence reference")
        return self


class ScribePolicy(ContractModel):
    """A frozen, non-timer schedule and bounded adaptation aperture."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    trigger_material_count: PositiveInt = 8
    minimum_adaptation_materials: PositiveInt = 4
    minimum_validation_materials: PositiveInt = 2
    validation_stride: PositiveInt = 3
    validation_slot: NonNegativeInt = 2
    maximum_materials_per_cycle: PositiveInt = 96
    maximum_proposals_per_cycle: PositiveInt = 8
    promote_when_mechanical_gates_pass: bool = True
    require_stage_boundary_cycle: bool = True
    timer_trigger_allowed: Literal[False] = False
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_policy(self) -> ScribePolicy:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        if self.validation_slot >= self.validation_stride:
            raise ValueError("scribe validation slot must be inside its stride")
        minimum = self.minimum_adaptation_materials + self.minimum_validation_materials
        if self.maximum_materials_per_cycle < minimum:
            raise ValueError("scribe cycle cannot hold its minimum split")
        if self.trigger_material_count < minimum:
            raise ValueError("scribe trigger cannot precede its minimum split")
        return self


class ScribeDriverBinding(ContractModel):
    """Identity of a replaceable scribe provider, separate from the action model."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    driver_id: str
    driver_version: str
    driver_artifact_ref: str
    role: Literal["representation_only"] = "representation_only"
    has_environment_action_port: Literal[False] = False
    has_authority_port: Literal[False] = False

    @model_validator(mode="after")
    def validate_binding(self) -> ScribeDriverBinding:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_canonical_identity(self.driver_id, "scribe driver identity")
        _require_canonical_identity(self.driver_version, "scribe driver version")
        _require_digest(self.driver_artifact_ref, "scribe driver artifact")
        return self


class ScribeRequest(ContractModel):
    """Durable request identity; source bytes remain in their Kevin entries."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    request_id: str
    session_id: str
    trigger: ScribeTrigger
    driver: ScribeDriverBinding
    active_codebook_ref: str
    policy_ref: str
    material_frontier_ref: str
    boundary_adapter_ref: str
    work_projection_ref: str
    adaptation_material_refs: tuple[str, ...]
    withheld_validation_material_refs: tuple[str, ...]
    maximum_proposals: PositiveInt
    concise_task: str
    recommendation_only: Literal[True] = True
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_request(self) -> ScribeRequest:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.request_id, "scribe request identity")
        _require_safe_id(self.session_id, "scribe session identity")
        if not self.concise_task.strip():
            raise ValueError("scribe request requires a concise task")
        for value in (
            self.active_codebook_ref,
            self.policy_ref,
            self.material_frontier_ref,
            self.boundary_adapter_ref,
            self.work_projection_ref,
            *self.adaptation_material_refs,
            *self.withheld_validation_material_refs,
        ):
            _require_digest(value, "scribe request reference")
        _require_sorted_unique(
            self.adaptation_material_refs, "scribe adaptation material references"
        )
        _require_sorted_unique(
            self.withheld_validation_material_refs,
            "withheld validation material references",
        )
        if set(self.adaptation_material_refs) & set(self.withheld_validation_material_refs):
            raise ValueError("adaptation and validation materials must be disjoint")
        return self


class ScribeMaterialFrontier(ContractModel):
    """Ordered material frontier frozen before a cycle provider is called."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    session_id: str
    material_refs: tuple[str, ...]
    latest_ordinal: NonNegativeInt | None

    @model_validator(mode="after")
    def validate_frontier(self) -> ScribeMaterialFrontier:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.session_id, "scribe session identity")
        if len(set(self.material_refs)) != len(self.material_refs):
            raise ValueError("scribe material frontier references must be unique")
        for value in self.material_refs:
            _require_digest(value, "scribe material frontier reference")
        if bool(self.material_refs) != (self.latest_ordinal is not None):
            raise ValueError("scribe material frontier ordinal does not match its contents")
        return self


class ScribeMaterialView(ContractModel):
    """Ephemeral exact payload view supplied only to the representation driver."""

    material: ScribeMaterial
    payload: ScribeEvidenceAtom

    @model_validator(mode="after")
    def validate_view(self) -> ScribeMaterialView:
        if content_hash(self.payload) != self.material.payload_ref:
            raise ValueError("scribe material view changed its source payload")
        return self


class ScribeRequestView(ContractModel):
    """Driver request view with held-out payloads absent from this interface.

    This data minimization is not a confidentiality sandbox for a trusted
    in-process callable that already holds some other reference to the session.
    """

    request: ScribeRequest
    adaptation_materials: tuple[ScribeMaterialView, ...]

    @model_validator(mode="after")
    def validate_view(self) -> ScribeRequestView:
        refs = tuple(sorted(item.material.digest for item in self.adaptation_materials))
        if refs != self.request.adaptation_material_refs:
            raise ValueError("scribe request view crosses its durable material aperture")
        return self


class ScribeDraft(ContractModel):
    """Declarative output from one representation-only provider."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    proposals: tuple[KevinSymbolProposal, ...]
    rationale: str
    known_residuals: tuple[str, ...] = ()
    recommendation_only: Literal[True] = True
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_draft(self) -> ScribeDraft:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        if not self.rationale.strip():
            raise ValueError("scribe draft requires a concise rationale")
        tokens = tuple(item.token for item in self.proposals)
        if len(set(tokens)) != len(tokens):
            raise ValueError("scribe proposal tokens must be unique")
        _require_sorted_unique(self.known_residuals, "scribe known residuals")
        return self


class ScribeFrozenDraft(ContractModel):
    """Durable provider output bound to the exact request and driver identity."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    cycle_id: str
    request_ref: str
    driver_binding_ref: str
    draft_ref: str
    draft: ScribeDraft

    @model_validator(mode="after")
    def validate_frozen_draft(self) -> ScribeFrozenDraft:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.cycle_id, "scribe cycle identity")
        for value in (self.request_ref, self.driver_binding_ref, self.draft_ref):
            _require_digest(value, "frozen scribe draft reference")
        if self.draft.digest != self.draft_ref:
            raise ValueError("frozen scribe draft changed its provider output")
        return self


class ScribeDriver(Protocol):
    @property
    def binding(self) -> ScribeDriverBinding: ...

    def propose(self, request: ScribeRequestView) -> ScribeDraft: ...


class CallableScribeDriver:
    """Small in-process adapter for a local model or deterministic scribe."""

    def __init__(
        self,
        *,
        binding: ScribeDriverBinding,
        proposal_function: Callable[[ScribeRequestView], ScribeDraft],
    ) -> None:
        self._binding = binding
        self._proposal_function = proposal_function

    @property
    def binding(self) -> ScribeDriverBinding:
        return self._binding

    def propose(self, request: ScribeRequestView) -> ScribeDraft:
        if request.request.driver != self._binding:
            raise ScribeError("scribe request binds another driver")
        result = self._proposal_function(request)
        if not isinstance(result, ScribeDraft):
            raise ScribeError("scribe driver returned an invalid response type")
        return result


class ScribeGenesis(ContractModel):
    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    session_id: str
    workspace_id: str
    driver: ScribeDriverBinding
    policy_ref: str
    boundary_adapter_ref: str
    work_projection_ref: str
    assertion: str = (
        "representation-only scribe; no environment action, domain state, private reasoning, "
        "permission, authorization, or authority"
    )
    authority: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_genesis(self) -> ScribeGenesis:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.session_id, "scribe session identity")
        _require_safe_id(self.workspace_id, "scribe workspace identity")
        for value in (self.policy_ref, self.boundary_adapter_ref, self.work_projection_ref):
            _require_digest(value, "scribe genesis reference")
        return self


class ScribeCycleReceipt(ContractModel):
    """One attempt, including honest no-candidate and not-earned outcomes."""

    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    cycle_id: str
    request_ref: str
    draft_ref: str | None
    frozen_draft_ref: str | None
    predecessor_codebook_ref: str
    candidate_codebook_ref: str | None
    evaluation_ref: str | None
    promotion_ref: str | None
    adaptation_material_refs: tuple[str, ...]
    validation_material_refs: tuple[str, ...]
    status: ScribeCycleStatus
    reasons: tuple[str, ...]
    productive_transition: bool
    requires_reentry: bool = False
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"
    effect: Literal["REPRESENTATION_ONLY"] = "REPRESENTATION_ONLY"

    @model_validator(mode="after")
    def validate_cycle(self) -> ScribeCycleReceipt:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.cycle_id, "scribe cycle identity")
        _require_digest(self.request_ref, "scribe request reference")
        _require_digest(self.predecessor_codebook_ref, "predecessor codebook")
        for value in (
            self.draft_ref,
            self.frozen_draft_ref,
            self.candidate_codebook_ref,
            self.evaluation_ref,
            self.promotion_ref,
        ):
            if value is not None:
                _require_digest(value, "scribe cycle reference")
        _require_sorted_unique(self.adaptation_material_refs, "adaptation material references")
        _require_sorted_unique(self.validation_material_refs, "validation material references")
        for value in (*self.adaptation_material_refs, *self.validation_material_refs):
            _require_digest(value, "scribe cycle material reference")
        _require_sorted_unique(self.reasons, "scribe cycle reasons")
        if set(self.adaptation_material_refs) & set(self.validation_material_refs):
            raise ValueError("scribe cycle adaptation and validation sets must be disjoint")
        if (self.draft_ref is None) != (self.frozen_draft_ref is None):
            raise ValueError("scribe draft and frozen-draft references must travel together")
        if self.status is ScribeCycleStatus.DEFERRED:
            if any(
                value is not None
                for value in (
                    self.draft_ref,
                    self.frozen_draft_ref,
                    self.candidate_codebook_ref,
                    self.evaluation_ref,
                    self.promotion_ref,
                )
            ):
                raise ValueError("deferred cycle cannot claim an attempted adaptation")
        elif self.status is ScribeCycleStatus.FAILED:
            if not self.reasons or self.productive_transition:
                raise ValueError("failed cycle must preserve reasons and claim no transition")
            if self.evaluation_ref is not None and self.candidate_codebook_ref is None:
                raise ValueError("failed evaluation must retain its candidate")
            if self.promotion_ref is not None and (
                self.candidate_codebook_ref is None or self.evaluation_ref is None
            ):
                raise ValueError("failed promotion must retain candidate and evaluation")
            has_partial_kevin_state = any(
                value is not None
                for value in (
                    self.candidate_codebook_ref,
                    self.evaluation_ref,
                    self.promotion_ref,
                )
            )
            if self.requires_reentry != has_partial_kevin_state:
                raise ValueError("partial Kevin mutation requires explicit scribe re-entry")
        elif self.status is ScribeCycleStatus.NO_CANDIDATE:
            if (
                self.draft_ref is None
                or self.frozen_draft_ref is None
                or any(
                    value is not None
                    for value in (
                        self.candidate_codebook_ref,
                        self.evaluation_ref,
                        self.promotion_ref,
                    )
                )
            ):
                raise ValueError("no-candidate cycle must bind only its empty draft")
        else:
            if (
                self.draft_ref is None
                or self.frozen_draft_ref is None
                or self.candidate_codebook_ref is None
                or self.evaluation_ref is None
            ):
                raise ValueError("attempted scribe adaptation lost candidate or evaluation")
        if self.status is not ScribeCycleStatus.FAILED and self.requires_reentry:
            raise ValueError("only a failed partial cycle can require re-entry")
        if self.status is ScribeCycleStatus.PROMOTED:
            if self.promotion_ref is None or not self.productive_transition or self.reasons:
                raise ValueError("promoted cycle requires a clean productive promotion")
        elif self.status is not ScribeCycleStatus.FAILED and (
            self.promotion_ref is not None or self.productive_transition
        ):
            raise ValueError("only a promoted codebook is a productive scribe transition")
        if self.status is ScribeCycleStatus.NOT_EARNED and not self.reasons:
            raise ValueError("not-earned scribe cycle must preserve failed gates")
        return self


class ScribeVerification(ContractModel):
    schema_id: str = Field(default=SCRIBE_SCHEMA, alias="schema")
    session_id: str
    workspace_id: str
    material_count: NonNegativeInt
    cycle_count: NonNegativeInt
    promoted_cycle_count: NonNegativeInt
    pending_material_count: NonNegativeInt
    receipt_count: PositiveInt
    receipt_head: str
    exact_workspace_round_trips: bool
    incomplete_request_count: NonNegativeInt = 0
    requires_reentry: bool = False
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @model_validator(mode="after")
    def validate_verification(self) -> ScribeVerification:
        if self.schema_id != SCRIBE_SCHEMA:
            raise ValueError("unsupported scribe schema")
        _require_safe_id(self.session_id, "scribe session identity")
        _require_safe_id(self.workspace_id, "scribe workspace identity")
        _require_digest(self.receipt_head, "scribe verification receipt head")
        if self.promoted_cycle_count > self.cycle_count:
            raise ValueError("scribe promoted-cycle count exceeds total cycles")
        if self.requires_reentry and self.incomplete_request_count:
            raise ValueError("scribe cannot require re-entry with an unfinished request")
        return self


class ScribeSession:
    """Receipt-backed scribe coordinator sharing one serial Strongwiz writer."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger,
        workspace: KevinSpeakWorkspace,
        session_id: str,
        driver: ScribeDriver,
        policy: ScribePolicy,
        boundary_adapter: BoundaryAdapter,
        work_projection: StateProjection,
        receipt_refs: list[str],
        materials: list[ScribeMaterial] | None = None,
        requests: dict[str, ScribeRequest] | None = None,
        frozen_drafts: dict[str, ScribeFrozenDraft] | None = None,
        cycles: list[ScribeCycleReceipt] | None = None,
        processed_material_refs: set[str] | None = None,
    ) -> None:
        self._ledger = ledger
        self.workspace = workspace
        self.session_id = session_id
        self.driver = driver
        self.policy = policy
        self.boundary_adapter = boundary_adapter
        self.work_projection = work_projection
        self._receipt_refs = receipt_refs
        self._materials = materials or []
        self._requests = requests or {}
        self._frozen_drafts = frozen_drafts or {}
        self._cycles = cycles or []
        self._processed_material_refs = processed_material_refs or set()

    @property
    def _account_id(self) -> str:
        return f"{self.session_id}.scribe"

    @classmethod
    def open(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace: KevinSpeakWorkspace,
        session_id: str,
        driver: ScribeDriver,
        policy: ScribePolicy,
        boundary_adapter: BoundaryAdapter,
        work_projection: StateProjection,
    ) -> ScribeSession:
        _require_safe_id(session_id, "scribe session identity")
        if work_projection.digest not in boundary_adapter.target.provenance_refs:
            # No semantic equality is inferred here.  At least one explicit digest
            # link must bind the target interface to the compared projection.
            raise ScribeError("scribe boundary target does not bind its work projection")
        account_id = f"{session_id}.scribe"
        if any(envelope.account_id == account_id for envelope in ledger.receipts()):
            raise ScribeError("scribe session identity already exists")
        session = cls(
            ledger=ledger,
            workspace=workspace,
            session_id=session_id,
            driver=driver,
            policy=policy,
            boundary_adapter=boundary_adapter,
            work_projection=work_projection,
            receipt_refs=[],
        )
        object_refs = tuple(
            session._put_contract(value)
            for value in (policy, boundary_adapter, work_projection, driver.binding)
        )
        genesis = ScribeGenesis(
            session_id=session_id,
            workspace_id=workspace.workspace_id,
            driver=driver.binding,
            policy_ref=policy.digest,
            boundary_adapter_ref=boundary_adapter.digest,
            work_projection_ref=work_projection.digest,
        )
        session._record("scribe_genesis", genesis, object_refs=object_refs)
        return session

    @classmethod
    def restore(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace: KevinSpeakWorkspace,
        session_id: str,
        driver: ScribeDriver,
    ) -> ScribeSession:
        """Restore one exact scribe account without repeating a provider call."""

        _require_safe_id(session_id, "scribe session identity")
        ledger.verify()
        account_id = f"{session_id}.scribe"
        selected = tuple(
            envelope
            for envelope in ledger.receipts()
            if envelope.account_id == account_id and envelope.account_version == 0
        )
        if not selected:
            raise ScribeError("scribe session has no durable genesis")
        for index, envelope in enumerate(selected):
            expected_occurrence = f"{session_id}.scribe:{index:08d}:{envelope.kind}"
            expected_parent = () if index == 0 else (selected[index - 1].receipt_id,)
            if envelope.occurrence_id != expected_occurrence:
                raise ScribeError("scribe occurrence sequence is invalid")
            if envelope.parent_refs != expected_parent:
                raise ScribeError("scribe receipt lineage is broken")
        first = selected[0]
        if first.kind != "scribe_genesis":
            raise ScribeError("scribe lineage does not begin at genesis")
        genesis = ScribeGenesis.model_validate(ledger.get_payload(first.payload_hash))
        if genesis.session_id != session_id or genesis.workspace_id != workspace.workspace_id:
            raise ScribeError("scribe genesis crosses its session or workspace")
        if genesis.driver != driver.binding:
            raise ScribeError("scribe driver identity changed at restore")
        policy = ScribePolicy.model_validate(ledger.get_payload(genesis.policy_ref))
        boundary_adapter = BoundaryAdapter.model_validate(
            ledger.get_payload(genesis.boundary_adapter_ref)
        )
        work_projection = StateProjection.model_validate(
            ledger.get_payload(genesis.work_projection_ref)
        )
        if (
            policy.digest != genesis.policy_ref
            or boundary_adapter.digest != genesis.boundary_adapter_ref
            or work_projection.digest != genesis.work_projection_ref
            or work_projection.digest not in boundary_adapter.target.provenance_refs
        ):
            raise ScribeError("scribe genesis dependency identity changed")
        materials: list[ScribeMaterial] = []
        requests: dict[str, ScribeRequest] = {}
        frozen_drafts: dict[str, ScribeFrozenDraft] = {}
        cycles: list[ScribeCycleReceipt] = []
        processed: set[str] = set()
        material_ids: set[str] = set()
        cycle_ids: set[str] = set()
        open_request_id: str | None = None
        workspace_entries = {entry.digest: entry for entry in workspace.entries}
        receipt_ids = {envelope.receipt_id for envelope in ledger.receipts()}
        for envelope in selected[1:]:
            payload = ledger.get_payload(envelope.payload_hash)
            if envelope.payload_hash not in envelope.object_refs:
                raise ScribeError("scribe receipt omits its own content object")
            if envelope.kind == "scribe_material":
                if open_request_id is not None:
                    raise ScribeError("scribe material cannot cross an unfinished cycle")
                material = ScribeMaterial.model_validate(payload)
                if material.digest != envelope.payload_hash:
                    raise ScribeError("restored scribe material changed identity")
                if material.material_id in material_ids:
                    raise ScribeError("restored scribe material identity is duplicated")
                if materials and material.ordinal <= materials[-1].ordinal:
                    raise ScribeError("restored scribe material ordinals do not increase")
                if material.projection_ref != work_projection.digest:
                    raise ScribeError("restored scribe material crosses its projection")
                try:
                    entry = workspace_entries[material.entry_ref]
                except KeyError as error:
                    raise ScribeError(
                        "restored scribe material lost its Kevin entry"
                    ) from error
                if entry.entry_id != _entry_id(session_id, material.material_id):
                    raise ScribeError("restored scribe material changed its Kevin identity")
                atom = ScribeEvidenceAtom.model_validate(workspace.decode_entry(entry))
                if atom.digest != material.payload_ref:
                    raise ScribeError("restored scribe material changed payload identity")
                cited = (
                    *material.evidence_refs,
                    *atom.predecessor_refs,
                    *atom.counterevidence_refs,
                )
                if any(not ledger.has_object(ref) and ref not in receipt_ids for ref in cited):
                    raise ScribeError("restored scribe material cites absent evidence")
                material_ids.add(material.material_id)
                materials.append(material)
            elif envelope.kind == "scribe_request":
                request = ScribeRequest.model_validate(payload)
                if request.digest != envelope.payload_hash:
                    raise ScribeError("restored scribe request changed identity")
                if open_request_id is not None or request.request_id in requests:
                    raise ScribeError("scribe request identity or ordering is ambiguous")
                if (
                    request.session_id != session_id
                    or request.driver != driver.binding
                    or request.policy_ref != policy.digest
                    or request.boundary_adapter_ref != boundary_adapter.digest
                    or request.work_projection_ref != work_projection.digest
                ):
                    raise ScribeError("restored scribe request changed its frozen bindings")
                frontier = ScribeMaterialFrontier.model_validate(
                    ledger.get_payload(request.material_frontier_ref)
                )
                expected_frontier = cls._frontier_for(session_id, materials)
                if (
                    frontier != expected_frontier
                    or frontier.digest != request.material_frontier_ref
                ):
                    raise ScribeError("restored scribe request changed its material frontier")
                adaptation, validation = cls._split_materials(materials, processed, policy)
                if request.adaptation_material_refs != tuple(
                    sorted(item.digest for item in adaptation)
                ) or request.withheld_validation_material_refs != tuple(
                    sorted(item.digest for item in validation)
                ):
                    raise ScribeError("restored scribe request changed its evidence split")
                if request.active_codebook_ref != cls._active_codebook_before(
                    ledger, workspace, envelope.sequence
                ):
                    raise ScribeError("restored scribe request changed its active codebook")
                requests[request.request_id] = request
                open_request_id = request.request_id
            elif envelope.kind == "scribe_frozen_draft":
                frozen = ScribeFrozenDraft.model_validate(payload)
                if frozen.digest != envelope.payload_hash:
                    raise ScribeError("restored frozen scribe draft changed identity")
                frozen_request = requests.get(frozen.cycle_id)
                if (
                    open_request_id != frozen.cycle_id
                    or frozen_request is None
                    or frozen.cycle_id in frozen_drafts
                    or frozen.request_ref != frozen_request.digest
                    or frozen.driver_binding_ref != driver.binding.digest
                ):
                    raise ScribeError("restored frozen draft crosses its exact request")
                frozen_drafts[frozen.cycle_id] = frozen
            elif envelope.kind == "scribe_cycle":
                cycle = ScribeCycleReceipt.model_validate(payload)
                if cycle.digest != envelope.payload_hash:
                    raise ScribeError("restored scribe cycle changed identity")
                cycle_request = requests.get(cycle.cycle_id)
                if (
                    open_request_id != cycle.cycle_id
                    or cycle_request is None
                    or cycle.cycle_id in cycle_ids
                    or cycle.request_ref != cycle_request.digest
                    or cycle.predecessor_codebook_ref != cycle_request.active_codebook_ref
                    or cycle.adaptation_material_refs != cycle_request.adaptation_material_refs
                    or cycle.validation_material_refs
                    != cycle_request.withheld_validation_material_refs
                ):
                    raise ScribeError("restored scribe cycle changed its request binding")
                cycle_frozen = frozen_drafts.get(cycle.cycle_id)
                if (cycle_frozen is None) != (cycle.frozen_draft_ref is None):
                    raise ScribeError("restored scribe cycle changed frozen-draft presence")
                if cycle_frozen is not None and (
                    cycle.frozen_draft_ref != cycle_frozen.digest
                    or cycle.draft_ref != cycle_frozen.draft_ref
                ):
                    raise ScribeError("restored scribe cycle changed its frozen draft")
                if cycle.status in {
                    ScribeCycleStatus.NO_CANDIDATE,
                    ScribeCycleStatus.NOT_EARNED,
                    ScribeCycleStatus.PROMOTED,
                }:
                    processed.update(cycle.adaptation_material_refs)
                    processed.update(cycle.validation_material_refs)
                cycle_ids.add(cycle.cycle_id)
                cycles.append(cycle)
                open_request_id = None
            else:
                raise ScribeError("scribe account contains an unknown receipt kind")
        session = cls(
            ledger=ledger,
            workspace=workspace,
            session_id=session_id,
            driver=driver,
            policy=policy,
            boundary_adapter=boundary_adapter,
            work_projection=work_projection,
            receipt_refs=[envelope.receipt_id for envelope in selected],
            materials=materials,
            requests=requests,
            frozen_drafts=frozen_drafts,
            cycles=cycles,
            processed_material_refs=processed,
        )
        session.verify()
        return session

    def _put_contract(self, value: ContractModel) -> str:
        result = self._ledger.put_object(value.model_dump(mode="json", by_alias=True))
        if result != value.digest:
            raise ScribeError("scribe contract identity changed during storage")
        return result

    def _record(
        self,
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str:
        value_ref = self._put_contract(value)
        envelope = self._ledger.append(
            occurrence_id=f"{self.session_id}.scribe:{len(self._receipt_refs):08d}:{kind}",
            kind=kind,
            account_id=self._account_id,
            account_version=0,
            payload=value.model_dump(mode="json", by_alias=True),
            object_refs=tuple(dict.fromkeys((value_ref, *object_refs))),
            parent_refs=() if not self._receipt_refs else (self._receipt_refs[-1],),
        )
        self._receipt_refs.append(envelope.receipt_id)
        return value_ref

    @property
    def materials(self) -> tuple[ScribeMaterial, ...]:
        return tuple(self._materials)

    @property
    def cycles(self) -> tuple[ScribeCycleReceipt, ...]:
        return tuple(self._cycles)

    @property
    def requests(self) -> tuple[ScribeRequest, ...]:
        return tuple(self._requests.values())

    @property
    def frozen_drafts(self) -> tuple[ScribeFrozenDraft, ...]:
        return tuple(self._frozen_drafts.values())

    @staticmethod
    def _frontier_for(
        session_id: str, materials: Sequence[ScribeMaterial]
    ) -> ScribeMaterialFrontier:
        return ScribeMaterialFrontier(
            session_id=session_id,
            material_refs=tuple(item.digest for item in materials),
            latest_ordinal=None if not materials else materials[-1].ordinal,
        )

    @staticmethod
    def _split_materials(
        materials: Sequence[ScribeMaterial],
        processed_material_refs: set[str],
        policy: ScribePolicy,
    ) -> tuple[tuple[ScribeMaterial, ...], tuple[ScribeMaterial, ...]]:
        first_ordinal_by_payload: dict[str, int] = {}
        for item in materials:
            first_ordinal_by_payload.setdefault(item.payload_ref, item.ordinal)
        pending = [item for item in materials if item.digest not in processed_material_refs][
            -policy.maximum_materials_per_cycle :
        ]
        by_payload: dict[str, list[ScribeMaterial]] = {}
        for item in pending:
            by_payload.setdefault(item.payload_ref, []).append(item)
        adaptation_items: list[ScribeMaterial] = []
        validation_items: list[ScribeMaterial] = []
        for group in sorted(by_payload.values(), key=lambda items: items[0].ordinal):
            first_ordinal = first_ordinal_by_payload[group[0].payload_ref]
            target = (
                validation_items
                if first_ordinal % policy.validation_stride == policy.validation_slot
                else adaptation_items
            )
            target.extend(group)
        adaptation = tuple(adaptation_items)
        validation = tuple(validation_items)
        if {item.payload_ref for item in adaptation} & {
            item.payload_ref for item in validation
        }:
            raise ScribeError("scribe adaptation and validation payloads overlap")
        return adaptation, validation

    @staticmethod
    def _active_codebook_before(
        ledger: SQLiteLedger,
        workspace: KevinSpeakWorkspace,
        sequence: int,
    ) -> str:
        active_ref: str | None = None
        for envelope in ledger.receipts():
            if envelope.sequence >= sequence:
                break
            if (
                envelope.account_id != workspace.account_id
                or envelope.account_version != workspace.account_version
                or not envelope.occurrence_id.startswith(f"{workspace.workspace_id}:")
            ):
                continue
            payload = ledger.get_payload(envelope.payload_hash)
            if envelope.kind == "kevin_workspace_genesis":
                initial = (
                    payload.get("initial_codebook_ref")
                    if isinstance(payload, Mapping)
                    else None
                )
                if not isinstance(initial, str):
                    raise ScribeError("Kevin workspace genesis lost its active codebook")
                active_ref = initial
            elif envelope.kind == "kevin_codebook_promotion":
                promotion = KevinPromotionReceipt.model_validate(payload)
                if promotion.predecessor_codebook_ref != active_ref:
                    raise ScribeError("Kevin promotion history changed its active predecessor")
                active_ref = promotion.promoted_codebook_ref
        if active_ref is None:
            raise ScribeError("scribe request precedes its Kevin workspace genesis")
        return active_ref

    def _has_unfinished_request(self) -> bool:
        completed = {item.cycle_id for item in self._cycles}
        return any(cycle_id not in completed for cycle_id in self._requests)

    def ingest(self, material_input: ScribeMaterialInput) -> KevinSpeakEntry:
        """Add one derived summary to both the scribe and representation ledgers."""

        if self._has_unfinished_request():
            raise ScribeError("scribe material cannot cross an unfinished cycle")
        if any(item.requires_reentry for item in self._cycles):
            raise ScribeError("scribe session requires a new re-entry boundary")
        if material_input.projection_ref != self.work_projection.digest:
            raise ScribeError("scribe material crosses its declared projection")
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    *material_input.evidence_refs,
                    *material_input.payload.predecessor_refs,
                    *material_input.payload.counterevidence_refs,
                )
            )
        )
        receipt_refs = {envelope.receipt_id for envelope in self._ledger.receipts()}
        unknown_evidence = tuple(
            value
            for value in evidence_refs
            if not self._ledger.has_object(value) and value not in receipt_refs
        )
        if unknown_evidence:
            raise ScribeError("scribe material cites evidence absent from its ledger")
        payload = material_input.payload.model_dump(mode="json", by_alias=True)
        by_id = {item.material_id: item for item in self._materials}
        prior = by_id.get(material_input.material_id)
        if prior is not None:
            if (
                prior.ordinal != material_input.ordinal
                or prior.kind is not material_input.kind
                or prior.scope_id != material_input.scope_id
                or prior.payload_ref != material_input.payload_ref
                or prior.projection_ref != material_input.projection_ref
                or prior.evidence_refs != material_input.evidence_refs
            ):
                raise ScribeError("scribe material identity cannot be rewritten")
            entry_id = _entry_id(self.session_id, material_input.material_id)
            entry = next(item for item in self.workspace.entries if item.entry_id == entry_id)
            if self.workspace.decode_entry(entry) != payload:
                raise ScribeError("idempotent scribe material changed its payload")
            return entry
        if self._materials and material_input.ordinal <= self._materials[-1].ordinal:
            raise ScribeError("scribe material ordinals must increase")
        entry_id = _entry_id(self.session_id, material_input.material_id)
        matches = tuple(item for item in self.workspace.entries if item.entry_id == entry_id)
        if len(matches) > 1:
            raise ScribeError("scribe material has ambiguous Kevin entry identity")
        if matches:
            # Recover a Kevin entry committed immediately before an interrupted
            # scribe-material receipt. Exact source identity makes this safe to
            # finish without rewriting the representation entry.
            entry = matches[0]
            if (
                entry.source_payload_ref != material_input.payload_ref
                or self.workspace.decode_entry(entry) != payload
            ):
                raise ScribeError("orphan Kevin entry conflicts with scribe material")
        else:
            entry = self.workspace.append(entry_id=entry_id, payload=payload)
        if entry.source_payload_ref != material_input.payload_ref:
            raise ScribeError("scribe and Kevin Speak disagree about source identity")
        material = ScribeMaterial(
            material_id=material_input.material_id,
            ordinal=material_input.ordinal,
            kind=material_input.kind,
            scope_id=material_input.scope_id,
            payload_ref=material_input.payload_ref,
            entry_ref=entry.digest,
            projection_ref=material_input.projection_ref,
            evidence_refs=material_input.evidence_refs,
        )
        material_ref = self._put_contract(material)
        self._record(
            "scribe_material",
            material,
            object_refs=(material_ref, entry.digest),
        )
        self._materials.append(material)
        return entry

    def should_run(self) -> bool:
        pending = [
            item for item in self._materials if item.digest not in self._processed_material_refs
        ]
        return len(pending) >= self.policy.trigger_material_count

    def _split(self) -> tuple[tuple[ScribeMaterial, ...], tuple[ScribeMaterial, ...]]:
        return self._split_materials(
            self._materials, self._processed_material_refs, self.policy
        )

    def _payload(self, material: ScribeMaterial) -> ScribeEvidenceAtom:
        matches = tuple(
            entry for entry in self.workspace.entries if entry.digest == material.entry_ref
        )
        if len(matches) != 1:
            raise ScribeError("scribe material lost its exact Kevin entry")
        payload = ScribeEvidenceAtom.model_validate(self.workspace.decode_entry(matches[0]))
        if payload.digest != material.payload_ref:
            raise ScribeError("scribe material payload changed during reconstruction")
        return payload

    def _request_materials(
        self, request: ScribeRequest
    ) -> tuple[tuple[ScribeMaterial, ...], tuple[ScribeMaterial, ...]]:
        by_ref = {item.digest: item for item in self._materials}
        try:
            adaptation = tuple(by_ref[ref] for ref in request.adaptation_material_refs)
            validation = tuple(by_ref[ref] for ref in request.withheld_validation_material_refs)
        except KeyError as error:
            raise ScribeError("scribe request references unknown material") from error
        if {item.payload_ref for item in adaptation} & {
            item.payload_ref for item in validation
        }:
            raise ScribeError("scribe request leaks a held-out payload")
        return adaptation, validation

    def _kevin_lineage(
        self,
        request: ScribeRequest,
        frozen: ScribeFrozenDraft,
    ) -> tuple[
        KevinCodebookRevision | None,
        KevinCodebookEvaluation | None,
        KevinPromotionReceipt | None,
    ]:
        candidates: list[KevinCodebookRevision] = []
        evaluations: list[KevinCodebookEvaluation] = []
        promotions: list[KevinPromotionReceipt] = []
        for envelope in self._ledger.receipts():
            if (
                envelope.account_id != self.workspace.account_id
                or envelope.account_version != self.workspace.account_version
                or not envelope.occurrence_id.startswith(f"{self.workspace.workspace_id}:")
            ):
                continue
            payload = self._ledger.get_payload(envelope.payload_hash)
            if envelope.kind == "kevin_codebook_candidate":
                candidate = KevinCodebookRevision.model_validate(payload)
                if candidate.digest != envelope.payload_hash:
                    raise ScribeError("Kevin candidate receipt changed identity")
                if candidate.model_proposal_ref == frozen.digest:
                    candidates.append(candidate)
            elif envelope.kind == "kevin_codebook_evaluation":
                evaluation = KevinCodebookEvaluation.model_validate(payload)
                if evaluation.digest != envelope.payload_hash:
                    raise ScribeError("Kevin evaluation receipt changed identity")
                if evaluation.evaluation_id == _evaluation_id(request):
                    evaluations.append(evaluation)
            elif envelope.kind == "kevin_codebook_promotion":
                promotion = KevinPromotionReceipt.model_validate(payload)
                if promotion.digest != envelope.payload_hash:
                    raise ScribeError("Kevin promotion receipt changed identity")
                if any(
                    promotion.evaluation_ref == item.digest
                    or promotion.promoted_codebook_ref == item.candidate_codebook_ref
                    for item in evaluations
                ):
                    promotions.append(promotion)
        if len(candidates) > 1 or len(evaluations) > 1 or len(promotions) > 1:
            raise ScribeError("scribe cycle has ambiguous Kevin lineage")
        matched_candidate = None if not candidates else candidates[0]
        matched_evaluation = None if not evaluations else evaluations[0]
        matched_promotion = None if not promotions else promotions[0]
        if matched_evaluation is not None and matched_candidate is None:
            raise ScribeError("scribe evaluation lacks its request-bound candidate")
        if matched_promotion is not None and matched_evaluation is None:
            raise ScribeError("scribe promotion lacks its request-bound evaluation")
        if matched_candidate is not None:
            self._validate_candidate(request, frozen, matched_candidate)
        if matched_evaluation is not None and matched_candidate is not None:
            self._validate_evaluation(request, matched_candidate, matched_evaluation)
        if (
            matched_promotion is not None
            and matched_candidate is not None
            and matched_evaluation is not None
        ):
            expected = KevinPromotionReceipt(
                workspace_id=self.workspace.workspace_id,
                predecessor_codebook_ref=request.active_codebook_ref,
                promoted_codebook_ref=matched_candidate.digest,
                evaluation_ref=matched_evaluation.digest,
                policy_ref=self.workspace.configuration.promotion_policy.digest,
            )
            if matched_promotion != expected:
                raise ScribeError("scribe promotion changed its mechanical Kevin binding")
        return matched_candidate, matched_evaluation, matched_promotion

    def _validate_candidate(
        self,
        request: ScribeRequest,
        frozen: ScribeFrozenDraft,
        candidate: KevinCodebookRevision,
    ) -> None:
        registry = self._registry_through(request.active_codebook_ref)
        expected = registry.build_revision(
            predecessor_ref=request.active_codebook_ref,
            proposals=frozen.draft.proposals,
            rationale=frozen.draft.rationale,
            model_proposal_ref=frozen.digest,
        )
        if candidate != expected:
            raise ScribeError("scribe candidate changed its frozen proposal")

    def _registry_through(self, revision_ref: str) -> CodebookRegistry:
        lineage: list[KevinCodebookRevision] = []
        seen: set[str] = set()
        current_ref: str | None = revision_ref
        while current_ref is not None:
            if current_ref in seen:
                raise ScribeError("Kevin codebook lineage contains a cycle")
            seen.add(current_ref)
            book = KevinCodebookRevision.model_validate(self._ledger.get_payload(current_ref))
            if book.digest != current_ref:
                raise ScribeError("Kevin codebook object changed identity")
            lineage.append(book)
            current_ref = book.predecessor_ref
        registry = CodebookRegistry()
        for book in reversed(lineage):
            registry.register(book)
        return registry

    def _validate_evaluation(
        self,
        request: ScribeRequest,
        candidate: KevinCodebookRevision,
        evaluation: KevinCodebookEvaluation,
    ) -> None:
        adaptation, validation = self._request_materials(request)
        expected_cases = {
            item.material_id: (EvaluationRole.ADAPTATION, item.payload_ref)
            for item in adaptation
        }
        expected_cases.update(
            {
                item.material_id: (EvaluationRole.VALIDATION, item.payload_ref)
                for item in validation
            }
        )
        actual_cases = {
            item.case_id: (item.role, item.payload_ref) for item in evaluation.cases
        }
        if (
            evaluation.workspace_id != self.workspace.workspace_id
            or evaluation.evaluation_id != _evaluation_id(request)
            or evaluation.candidate_codebook_ref != candidate.digest
            or evaluation.predecessor_codebook_ref != request.active_codebook_ref
            or evaluation.promotion_policy_ref
            != self.workspace.configuration.promotion_policy.digest
            or actual_cases != expected_cases
        ):
            raise ScribeError("scribe evaluation changed its material or policy binding")
        source_refs = {
            ref
            for definition in candidate.definitions
            for ref in definition.source_payload_refs
        }
        validation_refs = {item.payload_ref for item in validation}
        if source_refs & validation_refs:
            raise ScribeError("scribe evaluation reused held-out validation material")
        registry = self._registry_through(request.active_codebook_ref)
        registry.register(candidate)
        predecessor_translations = registry.resolved(request.active_codebook_ref)
        candidate_translations = registry.resolved(candidate.digest)
        expected_case_objects: list[KevinEvaluationCase] = []
        adaptation_refs = {item.digest for item in adaptation}
        for material in (*adaptation, *validation):
            payload_bytes = canonical_bytes(
                self._payload(material).model_dump(mode="json", by_alias=True)
            )
            source = payload_bytes.decode("utf-8")
            before = encode_shorthand_text(source, predecessor_translations)
            after = encode_shorthand_text(source, candidate_translations)
            exact = (
                decode_shorthand_text(after.encoded, candidate_translations).encode("utf-8")
                == payload_bytes
            )
            expected_case_objects.append(
                KevinEvaluationCase(
                    case_id=material.material_id,
                    role=(
                        EvaluationRole.ADAPTATION
                        if material.digest in adaptation_refs
                        else EvaluationRole.VALIDATION
                    ),
                    payload_ref=material.payload_ref,
                    source_size_bytes=len(payload_bytes),
                    predecessor_representation_bytes=min(
                        before.source_size_bytes, before.encoded_size_bytes
                    ),
                    candidate_representation_bytes=min(
                        after.source_size_bytes, after.encoded_size_bytes
                    ),
                    exact_round_trip=exact,
                )
            )
        policy = self.workspace.configuration.promotion_policy
        reasons: list[str] = []
        adaptation_cases = tuple(
            item for item in expected_case_objects if item.role is EvaluationRole.ADAPTATION
        )
        validation_cases = tuple(
            item for item in expected_case_objects if item.role is EvaluationRole.VALIDATION
        )
        if len(expected_case_objects) < policy.minimum_cases:
            reasons.append("insufficient_total_cases")
        if len(adaptation_cases) < policy.minimum_adaptation_cases:
            reasons.append("insufficient_adaptation_cases")
        if len(validation_cases) < policy.minimum_validation_cases:
            reasons.append("insufficient_validation_cases")
        if not all(item.exact_round_trip for item in expected_case_objects):
            reasons.append("round_trip_failure")
        if policy.require_validation_improvement and not any(
            item.candidate_representation_bytes < item.predecessor_representation_bytes
            for item in validation_cases
        ):
            reasons.append("no_validation_improvement")
        if policy.forbid_validation_source_reuse and source_refs & validation_refs:
            reasons.append("validation_payload_used_to_define_candidate")
        gross_savings = sum(
            item.predecessor_representation_bytes - item.candidate_representation_bytes
            for item in expected_case_objects
        )
        transport = len(canonical_bytes(candidate))
        net_savings = gross_savings - transport
        if (
            len(registry.effective_definition_refs(candidate.digest))
            > self.workspace.configuration.max_active_symbols
        ):
            reasons.append("active_symbol_budget_exceeded")
        if transport > self.workspace.configuration.max_incremental_codebook_bytes:
            reasons.append("incremental_codebook_budget_exceeded")
        if policy.require_net_savings and net_savings <= 0:
            reasons.append("codebook_cost_not_recovered")
        expected_evaluation = KevinCodebookEvaluation(
            workspace_id=self.workspace.workspace_id,
            evaluation_id=_evaluation_id(request),
            candidate_codebook_ref=candidate.digest,
            predecessor_codebook_ref=request.active_codebook_ref,
            promotion_policy_ref=policy.digest,
            cases=tuple(expected_case_objects),
            incremental_codebook_bytes=transport,
            gross_content_savings_bytes=gross_savings,
            net_savings_bytes=net_savings,
            status=(EvaluationStatus.ELIGIBLE if not reasons else EvaluationStatus.NOT_EARNED),
            reasons=tuple(sorted(set(reasons))),
        )
        if evaluation != expected_evaluation:
            raise ScribeError("scribe evaluation changed its mechanical result")

    def _validate_cycle_semantics(self, cycle: ScribeCycleReceipt) -> None:
        request = self._requests.get(cycle.cycle_id)
        if request is None or request.digest != cycle.request_ref:
            raise ScribeError("scribe cycle lacks its durable request")
        frozen = self._frozen_drafts.get(cycle.cycle_id)
        if frozen is None:
            candidate = evaluation = promotion = None
        else:
            if frozen.digest != cycle.frozen_draft_ref or frozen.draft_ref != cycle.draft_ref:
                raise ScribeError("scribe cycle changed its frozen provider output")
            candidate, evaluation, promotion = self._kevin_lineage(request, frozen)
        detected = (
            None if candidate is None else candidate.digest,
            None if evaluation is None else evaluation.digest,
            None if promotion is None else promotion.digest,
        )
        declared = (
            cycle.candidate_codebook_ref,
            cycle.evaluation_ref,
            cycle.promotion_ref,
        )
        if detected != declared:
            raise ScribeError("scribe cycle receipt disagrees with durable Kevin lineage")
        if cycle.status is ScribeCycleStatus.DEFERRED:
            adaptation, validation = self._request_materials(request)
            if (
                len(adaptation) >= self.policy.minimum_adaptation_materials
                and len(validation) >= self.policy.minimum_validation_materials
            ):
                raise ScribeError("scribe deferred despite satisfying its evidence gate")
        elif cycle.status is ScribeCycleStatus.NO_CANDIDATE:
            if frozen is None or frozen.draft.proposals:
                raise ScribeError("scribe no-candidate receipt changed its frozen draft")
            expected_reasons = tuple(
                sorted({"scribe_reported_no_candidate", *frozen.draft.known_residuals})
            )
            if cycle.reasons != expected_reasons:
                raise ScribeError("scribe no-candidate reasons changed")
        elif cycle.status is ScribeCycleStatus.NOT_EARNED:
            if frozen is None or evaluation is None or promotion is not None:
                raise ScribeError("scribe not-earned receipt lacks its exact evaluation")
            expected_reasons = tuple(
                sorted(
                    set(evaluation.reasons)
                    | ({"eligible_but_not_promoted"} if not evaluation.reasons else set())
                    | set(frozen.draft.known_residuals)
                )
            )
            if cycle.reasons != expected_reasons:
                raise ScribeError("scribe not-earned reasons changed")
        elif cycle.status is ScribeCycleStatus.PROMOTED:
            if (
                evaluation is None
                or evaluation.status is not EvaluationStatus.ELIGIBLE
                or promotion is None
                or not self.policy.promote_when_mechanical_gates_pass
            ):
                raise ScribeError("scribe promotion was not mechanically earned")

    def _record_cycle(self, receipt: ScribeCycleReceipt) -> ScribeCycleReceipt:
        object_refs = tuple(
            value
            for value in (
                receipt.request_ref,
                receipt.draft_ref,
                receipt.frozen_draft_ref,
                receipt.candidate_codebook_ref,
                receipt.evaluation_ref,
                receipt.promotion_ref,
            )
            if value is not None
        )
        self._record("scribe_cycle", receipt, object_refs=object_refs)
        self._cycles.append(receipt)
        if receipt.status in {
            ScribeCycleStatus.NO_CANDIDATE,
            ScribeCycleStatus.NOT_EARNED,
            ScribeCycleStatus.PROMOTED,
        }:
            self._processed_material_refs.update(
                (*receipt.adaptation_material_refs, *receipt.validation_material_refs)
            )
        return receipt

    def _failed_cycle(
        self,
        request: ScribeRequest,
        frozen: ScribeFrozenDraft | None,
        reason: str,
    ) -> ScribeCycleReceipt:
        candidate: KevinCodebookRevision | None = None
        evaluation: KevinCodebookEvaluation | None = None
        promotion: KevinPromotionReceipt | None = None
        if frozen is not None:
            candidate, evaluation, promotion = self._kevin_lineage(request, frozen)
        partial = any(item is not None for item in (candidate, evaluation, promotion))
        reasons = {reason}
        if partial:
            reasons.add("partial_kevin_mutation_requires_reentry")
        return self._record_cycle(
            ScribeCycleReceipt(
                cycle_id=request.request_id,
                request_ref=request.digest,
                draft_ref=None if frozen is None else frozen.draft_ref,
                frozen_draft_ref=None if frozen is None else frozen.digest,
                predecessor_codebook_ref=request.active_codebook_ref,
                candidate_codebook_ref=None if candidate is None else candidate.digest,
                evaluation_ref=None if evaluation is None else evaluation.digest,
                promotion_ref=None if promotion is None else promotion.digest,
                adaptation_material_refs=request.adaptation_material_refs,
                validation_material_refs=request.withheld_validation_material_refs,
                status=ScribeCycleStatus.FAILED,
                reasons=tuple(sorted(reasons)),
                productive_transition=False,
                requires_reentry=partial,
            )
        )

    def _finish_frozen(
        self,
        request: ScribeRequest,
        frozen: ScribeFrozenDraft,
    ) -> ScribeCycleReceipt:
        draft = frozen.draft
        if len(draft.proposals) > self.policy.maximum_proposals_per_cycle:
            raise ScribeError("scribe exceeded its frozen proposal aperture")
        adaptation, validation = self._request_materials(request)
        source_by_ref = {item.payload_ref: self._payload(item) for item in adaptation}
        for proposal in draft.proposals:
            if not set(proposal.source_payload_refs) <= set(source_by_ref):
                raise ScribeError("scribe proposal cites withheld or unknown material")
            for source_ref in proposal.source_payload_refs:
                source = canonical_bytes(source_by_ref[source_ref]).decode("utf-8")
                if proposal.expansion not in source:
                    raise ScribeError(
                        "scribe proposal expansion is absent from its cited source"
                    )
        if not draft.proposals:
            return self._record_cycle(
                ScribeCycleReceipt(
                    cycle_id=request.request_id,
                    request_ref=request.digest,
                    draft_ref=frozen.draft_ref,
                    frozen_draft_ref=frozen.digest,
                    predecessor_codebook_ref=request.active_codebook_ref,
                    candidate_codebook_ref=None,
                    evaluation_ref=None,
                    promotion_ref=None,
                    adaptation_material_refs=request.adaptation_material_refs,
                    validation_material_refs=request.withheld_validation_material_refs,
                    status=ScribeCycleStatus.NO_CANDIDATE,
                    reasons=tuple(
                        sorted({"scribe_reported_no_candidate", *draft.known_residuals})
                    ),
                    productive_transition=False,
                )
            )
        candidate = self.workspace.propose_revision(
            proposals=draft.proposals,
            rationale=draft.rationale,
            model_proposal_ref=frozen.digest,
        )
        samples = tuple(
            KevinEvaluationSample(
                case_id=item.material_id,
                role=EvaluationRole.ADAPTATION,
                payload=self._payload(item).model_dump(mode="json", by_alias=True),
            )
            for item in adaptation
        ) + tuple(
            KevinEvaluationSample(
                case_id=item.material_id,
                role=EvaluationRole.VALIDATION,
                payload=self._payload(item).model_dump(mode="json", by_alias=True),
            )
            for item in validation
        )
        evaluation = self.workspace.evaluate_candidate(
            candidate.digest,
            samples,
            evaluation_id=_evaluation_id(request),
        )
        promotion: KevinPromotionReceipt | None = None
        if (
            evaluation.status is EvaluationStatus.ELIGIBLE
            and self.policy.promote_when_mechanical_gates_pass
        ):
            promotion = self.workspace.promote(
                candidate_ref=candidate.digest,
                evaluation_ref=evaluation.digest,
            )
        promoted = promotion is not None
        reasons = (
            ()
            if promoted
            else tuple(
                sorted(
                    set(evaluation.reasons)
                    | ({"eligible_but_not_promoted"} if not evaluation.reasons else set())
                    | set(draft.known_residuals)
                )
            )
        )
        return self._record_cycle(
            ScribeCycleReceipt(
                cycle_id=request.request_id,
                request_ref=request.digest,
                draft_ref=frozen.draft_ref,
                frozen_draft_ref=frozen.digest,
                predecessor_codebook_ref=request.active_codebook_ref,
                candidate_codebook_ref=candidate.digest,
                evaluation_ref=evaluation.digest,
                promotion_ref=None if promotion is None else promotion.digest,
                adaptation_material_refs=request.adaptation_material_refs,
                validation_material_refs=request.withheld_validation_material_refs,
                status=(
                    ScribeCycleStatus.PROMOTED if promoted else ScribeCycleStatus.NOT_EARNED
                ),
                reasons=reasons,
                productive_transition=promoted,
            )
        )

    def run_cycle(
        self,
        *,
        cycle_id: str,
        trigger: ScribeTrigger,
    ) -> ScribeCycleReceipt:
        """Ask the isolated scribe once, then apply only mechanical Kevin gates."""

        _require_safe_id(cycle_id, "scribe cycle identity")
        if any(item.requires_reentry for item in self._cycles):
            raise ScribeError("scribe session requires a new re-entry boundary")
        matching = [item for item in self._cycles if item.cycle_id == cycle_id]
        if len(matching) > 1:
            raise ScribeError("scribe cycle identity is ambiguous")
        frontier = self._frontier_for(self.session_id, self._materials)
        if matching:
            request = self._requests[cycle_id]
            if (
                request.trigger is not trigger
                or request.material_frontier_ref != frontier.digest
                or request.policy_ref != self.policy.digest
                or request.driver != self.driver.binding
                or request.boundary_adapter_ref != self.boundary_adapter.digest
                or request.work_projection_ref != self.work_projection.digest
            ):
                raise ScribeError("scribe cycle identity cannot be reused across semantics")
            return matching[0]
        adaptation, validation = self._split()
        adaptation_refs = tuple(sorted(item.digest for item in adaptation))
        validation_refs = tuple(sorted(item.digest for item in validation))
        pending_request = self._requests.get(cycle_id)
        if pending_request is not None:
            expected = ScribeRequest(
                request_id=cycle_id,
                session_id=self.session_id,
                trigger=trigger,
                driver=self.driver.binding,
                active_codebook_ref=pending_request.active_codebook_ref,
                policy_ref=self.policy.digest,
                material_frontier_ref=frontier.digest,
                boundary_adapter_ref=self.boundary_adapter.digest,
                work_projection_ref=self.work_projection.digest,
                adaptation_material_refs=adaptation_refs,
                withheld_validation_material_refs=validation_refs,
                maximum_proposals=self.policy.maximum_proposals_per_cycle,
                concise_task=pending_request.concise_task,
            )
            if expected != pending_request:
                raise ScribeError("scribe cycle identity cannot be reused across semantics")
            frozen = self._frozen_drafts.get(cycle_id)
            if frozen is None:
                return self._failed_cycle(
                    pending_request,
                    None,
                    "scribe_provider_outcome_unknown_after_interruption",
                )
            candidate, evaluation, promotion = self._kevin_lineage(pending_request, frozen)
            if any(item is not None for item in (candidate, evaluation, promotion)):
                return self._failed_cycle(
                    pending_request,
                    frozen,
                    "scribe_interrupted_after_kevin_mutation",
                )
            try:
                return self._finish_frozen(pending_request, frozen)
            except Exception as error:
                return self._failed_cycle(
                    pending_request,
                    frozen,
                    f"scribe_post_provider_failure:{type(error).__name__}",
                )
        if self._has_unfinished_request():
            raise ScribeError("another scribe cycle remains unfinished")
        active_ref = self.workspace.active_codebook.digest
        request = ScribeRequest(
            request_id=cycle_id,
            session_id=self.session_id,
            trigger=trigger,
            driver=self.driver.binding,
            active_codebook_ref=active_ref,
            policy_ref=self.policy.digest,
            material_frontier_ref=frontier.digest,
            boundary_adapter_ref=self.boundary_adapter.digest,
            work_projection_ref=self.work_projection.digest,
            adaptation_material_refs=adaptation_refs,
            withheld_validation_material_refs=validation_refs,
            maximum_proposals=self.policy.maximum_proposals_per_cycle,
            concise_task=(
                "Propose reversible shorthand only for repeated structures in the supplied "
                "adaptation summaries; preserve uncertainty and unresolved distinctions."
            ),
        )
        frontier_ref = self._put_contract(frontier)
        self._record(
            "scribe_request",
            request,
            object_refs=(
                frontier_ref,
                self.policy.digest,
                self.driver.binding.digest,
                self.boundary_adapter.digest,
                self.work_projection.digest,
                *adaptation_refs,
                *validation_refs,
            ),
        )
        self._requests[cycle_id] = request
        if (
            len(adaptation) < self.policy.minimum_adaptation_materials
            or len(validation) < self.policy.minimum_validation_materials
        ):
            receipt = ScribeCycleReceipt(
                cycle_id=cycle_id,
                request_ref=request.digest,
                draft_ref=None,
                frozen_draft_ref=None,
                predecessor_codebook_ref=active_ref,
                candidate_codebook_ref=None,
                evaluation_ref=None,
                promotion_ref=None,
                adaptation_material_refs=adaptation_refs,
                validation_material_refs=validation_refs,
                status=ScribeCycleStatus.DEFERRED,
                reasons=("insufficient_disjoint_material",),
                productive_transition=False,
            )
            return self._record_cycle(receipt)

        before_binding = self.driver.binding
        request_view = ScribeRequestView(
            request=request,
            adaptation_materials=tuple(
                ScribeMaterialView(material=item, payload=self._payload(item))
                for item in adaptation
            ),
        )
        try:
            draft = self.driver.propose(request_view)
        except Exception as error:
            return self._failed_cycle(
                request,
                None,
                f"scribe_driver_failure:{type(error).__name__}",
            )
        if self.driver.binding != before_binding:
            return self._failed_cycle(
                request,
                None,
                "scribe_driver_binding_changed",
            )
        try:
            draft_ref = self._put_contract(draft)
            frozen = ScribeFrozenDraft(
                cycle_id=cycle_id,
                request_ref=request.digest,
                driver_binding_ref=self.driver.binding.digest,
                draft_ref=draft_ref,
                draft=draft,
            )
            self._record(
                "scribe_frozen_draft",
                frozen,
                object_refs=(request.digest, self.driver.binding.digest, draft_ref),
            )
            self._frozen_drafts[cycle_id] = frozen
        except Exception as error:
            return self._failed_cycle(
                request,
                None,
                f"scribe_freeze_failure:{type(error).__name__}",
            )
        try:
            return self._finish_frozen(request, frozen)
        except Exception as error:
            return self._failed_cycle(
                request,
                frozen,
                f"scribe_post_provider_failure:{type(error).__name__}",
            )

    def verify(self) -> ScribeVerification:
        receipt_count, _global_head = self._ledger.verify()
        del receipt_count
        if not self._receipt_refs:
            raise ScribeError("scribe session has no durable genesis")
        workspace = self.workspace.verify()
        for cycle in self._cycles:
            self._validate_cycle_semantics(cycle)
        incomplete = set(self._requests) - {item.cycle_id for item in self._cycles}
        if len(incomplete) > 1:
            raise ScribeError("scribe session has multiple unfinished cycles")
        pending = sum(
            item.digest not in self._processed_material_refs for item in self._materials
        )
        return ScribeVerification(
            session_id=self.session_id,
            workspace_id=self.workspace.workspace_id,
            material_count=len(self._materials),
            cycle_count=len(self._cycles),
            promoted_cycle_count=sum(
                item.status is ScribeCycleStatus.PROMOTED for item in self._cycles
            ),
            pending_material_count=pending,
            receipt_count=len(self._receipt_refs),
            receipt_head=self._receipt_refs[-1],
            exact_workspace_round_trips=workspace.exact_round_trips,
            incomplete_request_count=len(incomplete),
            requires_reentry=any(item.requires_reentry for item in self._cycles),
        )


def scribe_schema_bundle() -> dict[str, object]:
    return {
        "schema": SCRIBE_SCHEMA,
        "claim_ceiling": "representation-only recommendations and measured codec behavior",
        "schemas": {
            "cycle_receipt": ScribeCycleReceipt.model_json_schema(),
            "draft": ScribeDraft.model_json_schema(),
            "driver_binding": ScribeDriverBinding.model_json_schema(),
            "evidence_atom": ScribeEvidenceAtom.model_json_schema(),
            "frozen_draft": ScribeFrozenDraft.model_json_schema(),
            "genesis": ScribeGenesis.model_json_schema(),
            "material": ScribeMaterial.model_json_schema(),
            "material_frontier": ScribeMaterialFrontier.model_json_schema(),
            "material_input": ScribeMaterialInput.model_json_schema(),
            "material_view": ScribeMaterialView.model_json_schema(),
            "policy": ScribePolicy.model_json_schema(),
            "request": ScribeRequest.model_json_schema(),
            "request_view": ScribeRequestView.model_json_schema(),
            "verification": ScribeVerification.model_json_schema(),
        },
    }
