"""Kevin Speak: adaptive, reversible shorthand for bounded working ledgers.

Kevin Speak compresses canonical representations; it does not compress truth,
evidence requirements, authority, or uncertainty.  Model-authored translations
are declarative data consumed by one fixed decoder.  They are never executable
code, and an entry is admitted to the compact lane only after exact round-trip
verification against its content-addressed source object.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from strongwiz.canonical import (
    ImmutableJSONValue,
    canonical_bytes,
    content_hash,
    parse_strict_json,
    sha256_bytes,
)
from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt
from strongwiz.ledger import ReceiptEnvelope, SQLiteLedger

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")
_TRANSFER_EXCLUSIONS = (
    "action_sequences",
    "authority",
    "domain_state",
    "private_reasoning",
)

KEVIN_SPEAK_DECODER_ID = "strongwiz.kevin-speak.segment-decoder"
KEVIN_SPEAK_DECODER_VERSION = "1"
KEVIN_SPEAK_CONTRACT_SCHEMA = "strongwiz.kevin-speak.v1"
KEVIN_SPEAK_DECODER_ARTIFACT_REF = content_hash(
    {
        "escape": "~~ means one literal tilde",
        "literal": "all non-tilde Unicode text is literal UTF-8",
        "schema": "strongwiz.kevin-speak.segment-decoder.v1",
        "symbol": "~TOKEN~ expands through the exact bound codebook",
        "validation": "decoded bytes must be canonical JSON and match source SHA-256",
    }
)


class KevinSpeakError(ValueError):
    """A shorthand boundary failed closed."""


class ShorthandLane(StrEnum):
    COMPACT = "compact"
    RESIDUAL = "residual"


class EvaluationRole(StrEnum):
    ADAPTATION = "adaptation"
    VALIDATION = "validation"


class EvaluationStatus(StrEnum):
    ELIGIBLE = "eligible"
    NOT_EARNED = "not_earned"


class ReviewDisposition(StrEnum):
    APPROVE = "approve"
    RECOMMEND = "recommend"
    REJECT = "reject"
    DEFER = "defer"
    HISTORICAL_ONLY = "historical_only"


class AdoptionStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class KevinPresentationMode(StrEnum):
    DECODED_STORAGE = "decoded_storage"
    MODEL_FACING = "model_facing"


def _require_digest(value: str, label: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _encoded_size(value: str) -> int:
    return len(value.encode("utf-8"))


def decode_shorthand_text(encoded: str, translations: Mapping[str, str]) -> str:
    """Decode the fixed, nonexecuting ``~token~``/``~~`` wire language."""

    output: list[str] = []
    cursor = 0
    while cursor < len(encoded):
        marker = encoded.find("~", cursor)
        if marker < 0:
            output.append(encoded[cursor:])
            break
        output.append(encoded[cursor:marker])
        if marker + 1 < len(encoded) and encoded[marker + 1] == "~":
            output.append("~")
            cursor = marker + 2
            continue
        closing = encoded.find("~", marker + 1)
        if closing < 0:
            raise KevinSpeakError("shorthand text contains a dangling symbol marker")
        token = encoded[marker + 1 : closing]
        if _TOKEN.fullmatch(token) is None:
            raise KevinSpeakError("shorthand text contains an invalid symbol token")
        try:
            output.append(translations[token])
        except KeyError as error:
            raise KevinSpeakError(f"shorthand text names an unknown symbol: {token}") from error
        cursor = closing + 1
    return "".join(output)


class EncodedText(ContractModel):
    """One deterministic encoding result before lane selection."""

    encoded: str
    source_size_bytes: NonNegativeInt
    encoded_size_bytes: NonNegativeInt
    symbol_uses: NonNegativeInt


def encode_shorthand_text(source: str, translations: Mapping[str, str]) -> EncodedText:
    """Return a minimum-byte encoding under the supplied exact translations.

    Dynamic programming makes overlapping phrases deterministic.  Literal text
    wins ties, so a symbol is used only when it strictly reduces UTF-8 bytes.
    """

    for token, expansion in translations.items():
        if _TOKEN.fullmatch(token) is None or not expansion:
            raise KevinSpeakError("translations require valid tokens and nonempty expansions")

    size = len(source)
    best_cost = [0] * (size + 1)
    best_uses = [0] * (size + 1)
    choice: list[tuple[str, int] | None] = [None] * (size + 1)
    ordered = tuple(sorted(translations.items(), key=lambda item: item[0]))

    for index in range(size - 1, -1, -1):
        literal = "~~" if source[index] == "~" else source[index]
        literal_cost = _encoded_size(literal) + best_cost[index + 1]
        best_cost[index] = literal_cost
        best_uses[index] = best_uses[index + 1]
        choice[index] = (literal, index + 1)
        for token, expansion in ordered:
            if not source.startswith(expansion, index):
                continue
            replacement = f"~{token}~"
            successor = index + len(expansion)
            candidate_cost = _encoded_size(replacement) + best_cost[successor]
            candidate_uses = 1 + best_uses[successor]
            if candidate_cost < best_cost[index] or (
                candidate_cost == best_cost[index] and candidate_uses < best_uses[index]
            ):
                best_cost[index] = candidate_cost
                best_uses[index] = candidate_uses
                choice[index] = (replacement, successor)

    pieces: list[str] = []
    cursor = 0
    while cursor < size:
        selected = choice[cursor]
        if selected is None:  # pragma: no cover - construction invariant
            raise KevinSpeakError("encoder lost its deterministic continuation")
        piece, cursor = selected
        pieces.append(piece)
    encoded = "".join(pieces)
    if decode_shorthand_text(encoded, translations) != source:
        raise KevinSpeakError("encoder failed its exact text round trip")
    return EncodedText(
        encoded=encoded,
        source_size_bytes=_encoded_size(source),
        encoded_size_bytes=_encoded_size(encoded),
        symbol_uses=best_uses[0],
    )


class KevinSymbolProposal(ContractModel):
    """A model-authored translation proposal; data only, never executable code."""

    token: str
    expansion: str
    concise_meaning: str
    source_payload_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_proposal(self) -> KevinSymbolProposal:
        if _TOKEN.fullmatch(self.token) is None:
            raise ValueError("Kevin Speak token is invalid")
        if not self.expansion or not self.concise_meaning.strip():
            raise ValueError("a symbol requires a nonempty expansion and concise meaning")
        _require_sorted_unique(self.source_payload_refs, "symbol source references")
        if not self.source_payload_refs:
            raise ValueError("a symbol proposal requires at least one source payload")
        for value in self.source_payload_refs:
            _require_digest(value, "symbol source reference")
        return self


class KevinSymbolDefinition(ContractModel):
    """One tracked translation encoded only through the predecessor codebook."""

    token: str
    encoded_expansion: str
    expansion_ref: str
    expansion_size_bytes: PositiveInt
    concise_meaning: str
    source_payload_refs: tuple[str, ...]
    supersedes_definition_ref: str | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> KevinSymbolDefinition:
        if _TOKEN.fullmatch(self.token) is None:
            raise ValueError("Kevin Speak token is invalid")
        if not self.encoded_expansion or not self.concise_meaning.strip():
            raise ValueError("a symbol definition requires translation content")
        _require_digest(self.expansion_ref, "symbol expansion reference")
        _require_sorted_unique(self.source_payload_refs, "symbol source references")
        if not self.source_payload_refs:
            raise ValueError("a symbol definition requires source payload references")
        for value in self.source_payload_refs:
            _require_digest(value, "symbol source reference")
        if self.supersedes_definition_ref is not None:
            _require_digest(self.supersedes_definition_ref, "superseded definition")
        return self


class KevinCodebookRevision(ContractModel):
    """An immutable codebook delta with an exact predecessor."""

    schema_id: str = Field(default="strongwiz.kevin-codebook.v1", alias="schema")
    codebook_id: str
    version: NonNegativeInt
    predecessor_ref: str | None
    decoder_id: str = KEVIN_SPEAK_DECODER_ID
    decoder_version: str = KEVIN_SPEAK_DECODER_VERSION
    decoder_artifact_ref: str = KEVIN_SPEAK_DECODER_ARTIFACT_REF
    definitions: tuple[KevinSymbolDefinition, ...] = ()
    retired_tokens: tuple[str, ...] = ()
    rationale: str
    model_proposal_ref: str | None = None

    @model_validator(mode="after")
    def validate_revision(self) -> KevinCodebookRevision:
        if self.schema_id != "strongwiz.kevin-codebook.v1":
            raise ValueError("unsupported Kevin Speak codebook schema")
        if not all(
            value.strip()
            for value in (
                self.codebook_id,
                self.decoder_id,
                self.decoder_version,
                self.rationale,
            )
        ):
            raise ValueError("codebook and decoder identities plus rationale are required")
        _require_digest(self.decoder_artifact_ref, "decoder artifact reference")
        tokens = tuple(item.token for item in self.definitions)
        if tokens != tuple(sorted(set(tokens))):
            raise ValueError("codebook definitions must be sorted by unique token")
        _require_sorted_unique(self.retired_tokens, "retired tokens")
        if any(_TOKEN.fullmatch(token) is None for token in self.retired_tokens):
            raise ValueError("retired Kevin Speak tokens must use valid token syntax")
        if set(tokens) & set(self.retired_tokens):
            raise ValueError("one revision cannot both define and retire a token")
        if self.version == 0:
            if self.predecessor_ref is not None or self.definitions or self.retired_tokens:
                raise ValueError("codebook genesis must be blank and predecessor-free")
        else:
            if self.predecessor_ref is None:
                raise ValueError("codebook revisions require an exact predecessor")
            _require_digest(self.predecessor_ref, "codebook predecessor")
            if not self.definitions and not self.retired_tokens:
                raise ValueError("a codebook revision must change at least one symbol")
        if self.model_proposal_ref is not None:
            _require_digest(self.model_proposal_ref, "model proposal reference")
        return self

    @classmethod
    def blank(cls, *, codebook_id: str) -> KevinCodebookRevision:
        return cls(
            codebook_id=codebook_id,
            version=0,
            predecessor_ref=None,
            rationale="blank model-authored shorthand surface",
        )


class KevinTranslation(ContractModel):
    token: str
    expansion: str
    definition_ref: str
    effective_codebook_ref: str
    concise_meaning: str
    source_payload_refs: tuple[str, ...]


class KevinTranslationTable(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-translation-table.v1", alias="schema")
    codebook_ref: str
    codebook_version: NonNegativeInt
    translations: tuple[KevinTranslation, ...]
    decoder_artifact_ref: str
    authority: str = "NONE"


class CodebookRegistry:
    """Resolve immutable codebook branches without reinterpreting old entries."""

    def __init__(self) -> None:
        self._books: dict[str, KevinCodebookRevision] = {}
        self._translations: dict[str, dict[str, str]] = {}
        self._definitions: dict[str, dict[str, KevinSymbolDefinition]] = {}

    def register(self, revision: KevinCodebookRevision) -> str:
        if revision.digest in self._books:
            if self._books[revision.digest] != revision:
                raise KevinSpeakError("codebook identity collision")
            return revision.digest
        if revision.version == 0:
            if any(book.codebook_id == revision.codebook_id for book in self._books.values()):
                raise KevinSpeakError("codebook lineage already has a genesis")
            translations: dict[str, str] = {}
            definitions: dict[str, KevinSymbolDefinition] = {}
        else:
            parent_ref = revision.predecessor_ref
            if parent_ref is None or parent_ref not in self._books:
                raise KevinSpeakError("codebook predecessor is not registered")
            parent = self._books[parent_ref]
            if (
                revision.codebook_id != parent.codebook_id
                or revision.version != parent.version + 1
                or revision.decoder_id != parent.decoder_id
                or revision.decoder_version != parent.decoder_version
                or revision.decoder_artifact_ref != parent.decoder_artifact_ref
            ):
                raise KevinSpeakError("codebook revision changes lineage or decoder identity")
            translations = dict(self._translations[parent_ref])
            definitions = dict(self._definitions[parent_ref])
            for token in revision.retired_tokens:
                if token not in translations:
                    raise KevinSpeakError("codebook cannot retire an unknown token")
                translations.pop(token)
                definitions.pop(token)
            predecessor_translations = self._translations[parent_ref]
            predecessor_definitions = self._definitions[parent_ref]
            for definition in revision.definitions:
                prior = predecessor_definitions.get(definition.token)
                expected_superseded = None if prior is None else prior.digest
                if definition.supersedes_definition_ref != expected_superseded:
                    raise KevinSpeakError(
                        "symbol replacement does not bind its exact predecessor"
                    )
                expansion = decode_shorthand_text(
                    definition.encoded_expansion, predecessor_translations
                )
                if not expansion:
                    raise KevinSpeakError("decoded symbol expansion must be nonempty")
                if sha256_bytes(expansion.encode("utf-8")) != definition.expansion_ref:
                    raise KevinSpeakError("decoded symbol expansion disagrees with its digest")
                if _encoded_size(expansion) != definition.expansion_size_bytes:
                    raise KevinSpeakError("decoded symbol expansion disagrees with its size")
                translations[definition.token] = expansion
                definitions[definition.token] = definition
        self._books[revision.digest] = revision
        self._translations[revision.digest] = translations
        self._definitions[revision.digest] = definitions
        return revision.digest

    def require(self, revision_ref: str) -> KevinCodebookRevision:
        try:
            return self._books[revision_ref]
        except KeyError as error:
            raise KevinSpeakError("unknown codebook revision") from error

    def resolved(self, revision_ref: str) -> Mapping[str, str]:
        self.require(revision_ref)
        return dict(self._translations[revision_ref])

    def build_revision(
        self,
        *,
        predecessor_ref: str,
        proposals: Sequence[KevinSymbolProposal],
        retired_tokens: tuple[str, ...] = (),
        rationale: str,
        model_proposal_ref: str | None = None,
    ) -> KevinCodebookRevision:
        parent = self.require(predecessor_ref)
        if not rationale.strip():
            raise KevinSpeakError("codebook adaptation requires a concise rationale")
        proposal_tokens = tuple(item.token for item in proposals)
        if len(set(proposal_tokens)) != len(proposal_tokens):
            raise KevinSpeakError("codebook proposal repeats a token")
        predecessor_translations = self._translations[predecessor_ref]
        predecessor_definitions = self._definitions[predecessor_ref]
        definitions: list[KevinSymbolDefinition] = []
        for proposal in sorted(proposals, key=lambda item: item.token):
            encoded = encode_shorthand_text(proposal.expansion, predecessor_translations)
            prior = predecessor_definitions.get(proposal.token)
            definitions.append(
                KevinSymbolDefinition(
                    token=proposal.token,
                    encoded_expansion=encoded.encoded,
                    expansion_ref=sha256_bytes(proposal.expansion.encode("utf-8")),
                    expansion_size_bytes=_encoded_size(proposal.expansion),
                    concise_meaning=proposal.concise_meaning,
                    source_payload_refs=proposal.source_payload_refs,
                    supersedes_definition_ref=None if prior is None else prior.digest,
                )
            )
        revision = KevinCodebookRevision(
            codebook_id=parent.codebook_id,
            version=parent.version + 1,
            predecessor_ref=parent.digest,
            decoder_id=parent.decoder_id,
            decoder_version=parent.decoder_version,
            decoder_artifact_ref=parent.decoder_artifact_ref,
            definitions=tuple(definitions),
            retired_tokens=tuple(sorted(retired_tokens)),
            rationale=rationale,
            model_proposal_ref=model_proposal_ref,
        )
        self.register(revision)
        return revision

    def table(self, revision_ref: str) -> KevinTranslationTable:
        revision = self.require(revision_ref)
        translations = tuple(
            KevinTranslation(
                token=token,
                expansion=self._translations[revision_ref][token],
                definition_ref=definition.digest,
                effective_codebook_ref=revision_ref,
                concise_meaning=definition.concise_meaning,
                source_payload_refs=definition.source_payload_refs,
            )
            for token, definition in sorted(self._definitions[revision_ref].items())
        )
        return KevinTranslationTable(
            codebook_ref=revision_ref,
            codebook_version=revision.version,
            translations=translations,
            decoder_artifact_ref=revision.decoder_artifact_ref,
        )

    def lineage(self, revision_ref: str) -> tuple[KevinCodebookRevision, ...]:
        output: list[KevinCodebookRevision] = []
        current = self.require(revision_ref)
        while True:
            output.append(current)
            if current.predecessor_ref is None:
                break
            current = self.require(current.predecessor_ref)
        return tuple(reversed(output))

    def effective_definition_refs(self, revision_ref: str) -> tuple[str, ...]:
        self.require(revision_ref)
        return tuple(
            sorted(definition.digest for definition in self._definitions[revision_ref].values())
        )

    def lineage_definition_refs(self, revision_ref: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    definition.digest
                    for revision in self.lineage(revision_ref)
                    for definition in revision.definitions
                }
            )
        )

    def known_definition_refs(self) -> frozenset[str]:
        return frozenset(
            definition.digest
            for revision in self._books.values()
            for definition in revision.definitions
        )

    def definition(self, definition_ref: str) -> KevinSymbolDefinition:
        """Return one known immutable definition by content identity."""

        for revision in self._books.values():
            for definition in revision.definitions:
                if definition.digest == definition_ref:
                    return definition
        raise KevinSpeakError("unknown symbol definition")


class KevinEvaluationSample(ContractModel):
    case_id: str
    role: EvaluationRole
    payload: ImmutableJSONValue

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluation case identity is required")
        return value


class KevinPromotionPolicy(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-promotion-policy.v1", alias="schema")
    minimum_cases: PositiveInt = 2
    minimum_adaptation_cases: PositiveInt = 1
    minimum_validation_cases: PositiveInt = 1
    require_validation_improvement: bool = True
    require_net_savings: bool = True
    forbid_validation_source_reuse: bool = True


class KevinSpeakConfiguration(ContractModel):
    """Frozen limits and presentation mode for one shorthand workspace."""

    schema_id: str = Field(default="strongwiz.kevin-configuration.v1", alias="schema")
    presentation_mode: KevinPresentationMode = KevinPresentationMode.DECODED_STORAGE
    max_active_symbols: PositiveInt = 256
    max_entry_bytes: PositiveInt = 1_048_576
    max_incremental_codebook_bytes: PositiveInt = 262_144
    promotion_policy: KevinPromotionPolicy = KevinPromotionPolicy()
    require_exact_round_trip: bool = True
    residual_lane_enabled: bool = True
    claim_ceiling: str = "working representation behavior under this exact configuration"
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_configuration(self) -> KevinSpeakConfiguration:
        if not self.require_exact_round_trip or not self.residual_lane_enabled:
            raise ValueError("Kevin Speak cannot disable exact reconstruction or residuals")
        if not self.claim_ceiling.strip() or self.authority != "NONE":
            raise ValueError("Kevin Speak configuration grants no authority")
        return self


class KevinEvaluationCase(ContractModel):
    case_id: str
    role: EvaluationRole
    payload_ref: str
    source_size_bytes: PositiveInt
    predecessor_representation_bytes: PositiveInt
    candidate_representation_bytes: PositiveInt
    exact_round_trip: bool

    @model_validator(mode="after")
    def validate_case(self) -> KevinEvaluationCase:
        _require_digest(self.payload_ref, "evaluation payload reference")
        return self


class KevinCodebookEvaluation(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-evaluation.v1", alias="schema")
    workspace_id: str
    evaluation_id: str
    candidate_codebook_ref: str
    predecessor_codebook_ref: str
    promotion_policy_ref: str
    cases: tuple[KevinEvaluationCase, ...]
    incremental_codebook_bytes: PositiveInt
    gross_content_savings_bytes: int
    net_savings_bytes: int
    status: EvaluationStatus
    reasons: tuple[str, ...]
    claim_ceiling: str = (
        "exact mechanical round trip and measured bytes on this declared suite only"
    )
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_evaluation(self) -> KevinCodebookEvaluation:
        if not all(
            value.strip()
            for value in (self.workspace_id, self.evaluation_id, self.claim_ceiling)
        ):
            raise ValueError("evaluation identity and claim ceiling are required")
        for value in (
            self.candidate_codebook_ref,
            self.predecessor_codebook_ref,
            self.promotion_policy_ref,
        ):
            _require_digest(value, "evaluation binding")
        case_ids = tuple(item.case_id for item in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case identities must be unique")
        _require_sorted_unique(self.reasons, "evaluation reasons")
        if self.status is EvaluationStatus.ELIGIBLE and self.reasons:
            raise ValueError("an eligible evaluation cannot retain failed gates")
        if self.status is EvaluationStatus.NOT_EARNED and not self.reasons:
            raise ValueError("a non-eligible evaluation must name failed gates")
        return self


class KevinPromotionReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-promotion.v1", alias="schema")
    workspace_id: str
    predecessor_codebook_ref: str
    promoted_codebook_ref: str
    evaluation_ref: str
    policy_ref: str
    status: str = "promoted_for_future_working_representation"
    truth_change: bool = False
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_promotion(self) -> KevinPromotionReceipt:
        if not self.workspace_id.strip():
            raise ValueError("promotion workspace identity is required")
        for value in (
            self.predecessor_codebook_ref,
            self.promoted_codebook_ref,
            self.evaluation_ref,
            self.policy_ref,
        ):
            _require_digest(value, "promotion binding")
        return self


class KevinDefinitionReview(ContractModel):
    """One explicit next-round disposition for one symbol definition."""

    definition_ref: str
    disposition: ReviewDisposition
    rationale: str

    @model_validator(mode="after")
    def validate_review(self) -> KevinDefinitionReview:
        _require_digest(self.definition_ref, "reviewed definition")
        if not self.rationale.strip():
            raise ValueError("a definition disposition requires a concise rationale")
        return self


class KevinNextRoundRecommendation(ContractModel):
    """A source agent's proposal for a successor; it grants no adoption."""

    schema_id: str = Field(
        default="strongwiz.kevin-next-round-recommendation.v1", alias="schema"
    )
    recommendation_id: str
    source_workspace_id: str
    source_evidence_boundary_ref: str
    source_configuration_ref: str
    recommended_codebook_ref: str
    recommended_definition_refs: tuple[str, ...]
    evaluation_refs: tuple[str, ...]
    recommending_driver_ref: str
    rationale: str
    known_residuals: tuple[str, ...] = ()
    status: str = "recommended_not_approved"
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_recommendation(self) -> KevinNextRoundRecommendation:
        if not all(
            value.strip()
            for value in (
                self.recommendation_id,
                self.source_workspace_id,
                self.rationale,
            )
        ):
            raise ValueError("next-round recommendation identity and rationale are required")
        for value in (
            self.source_evidence_boundary_ref,
            self.source_configuration_ref,
            self.recommended_codebook_ref,
            self.recommending_driver_ref,
        ):
            _require_digest(value, "next-round recommendation binding")
        _require_sorted_unique(
            self.recommended_definition_refs, "recommended definition references"
        )
        _require_sorted_unique(self.evaluation_refs, "recommendation evaluation references")
        for value in (*self.recommended_definition_refs, *self.evaluation_refs):
            _require_digest(value, "next-round recommendation evidence")
        _require_sorted_unique(self.known_residuals, "known recommendation residuals")
        if self.status != "recommended_not_approved" or self.authority != "NONE":
            raise ValueError("a model recommendation cannot approve itself or grant authority")
        return self


class KevinRecommendationBundle(ContractModel):
    """Portable post-seal input for a separate successor-review workspace."""

    schema_id: str = Field(default="strongwiz.kevin-recommendation-bundle.v1", alias="schema")
    source_run_seal_ref: str
    source_capsule_ref: str
    recommendation: KevinNextRoundRecommendation
    source_configuration: KevinSpeakConfiguration
    codebooks: tuple[KevinCodebookRevision, ...]
    evaluations: tuple[KevinCodebookEvaluation, ...]
    excluded_material: tuple[str, ...] = _TRANSFER_EXCLUSIONS
    claim_ceiling: str = "candidate working representation for review only"
    transfers_authority: bool = False

    @model_validator(mode="after")
    def validate_bundle(self) -> KevinRecommendationBundle:
        _require_digest(self.source_run_seal_ref, "recommendation source run seal")
        _require_digest(self.source_capsule_ref, "recommendation source capsule")
        if self.recommendation.source_configuration_ref != self.source_configuration.digest:
            raise ValueError("recommendation bundle changes its source configuration")
        if not self.codebooks:
            raise ValueError("a recommendation bundle requires its codebook lineage")
        if tuple(book.version for book in self.codebooks) != tuple(range(len(self.codebooks))):
            raise ValueError("recommendation codebooks must be contiguous and genesis-first")
        registry = CodebookRegistry()
        for book in self.codebooks:
            registry.register(book)
        if self.codebooks[-1].digest != self.recommendation.recommended_codebook_ref:
            raise ValueError("recommendation bundle ends at the wrong codebook")
        if self.recommendation.recommended_definition_refs != (
            registry.effective_definition_refs(self.codebooks[-1].digest)
        ):
            raise ValueError("recommendation bundle changes its effective definitions")
        evaluation_refs = tuple(item.digest for item in self.evaluations)
        if evaluation_refs != tuple(sorted(set(evaluation_refs))):
            raise ValueError("recommendation evaluations must be sorted by unique digest")
        if evaluation_refs != self.recommendation.evaluation_refs:
            raise ValueError("recommendation bundle omits or adds evaluation evidence")
        lineage_refs = {book.digest for book in self.codebooks}
        if any(
            item.status is not EvaluationStatus.ELIGIBLE
            or item.candidate_codebook_ref not in lineage_refs
            for item in self.evaluations
        ):
            raise ValueError("recommendation bundle carries unearned evaluation evidence")
        if self.excluded_material != _TRANSFER_EXCLUSIONS or self.transfers_authority:
            raise ValueError("recommendation bundle cannot carry run state or authority")
        return self


class KevinRecommendationReview(ContractModel):
    """Advisory review or refinement by a later, possibly stronger, model."""

    schema_id: str = Field(default="strongwiz.kevin-recommendation-review.v1", alias="schema")
    review_id: str
    recommendation_ref: str
    original_codebook_ref: str
    reviewed_codebook_ref: str
    reviewer_driver_ref: str
    review_configuration_ref: str
    evaluation_refs: tuple[str, ...]
    definition_reviews: tuple[KevinDefinitionReview, ...]
    rationale: str
    status: str = "reviewed_not_adopted"
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_recommendation_review(self) -> KevinRecommendationReview:
        if not self.review_id.strip() or not self.rationale.strip():
            raise ValueError("recommendation review identity and rationale are required")
        for value in (
            self.recommendation_ref,
            self.original_codebook_ref,
            self.reviewed_codebook_ref,
            self.reviewer_driver_ref,
            self.review_configuration_ref,
        ):
            _require_digest(value, "recommendation review binding")
        _require_sorted_unique(self.evaluation_refs, "review evaluation references")
        for value in self.evaluation_refs:
            _require_digest(value, "review evaluation reference")
        reviewed_refs = tuple(item.definition_ref for item in self.definition_reviews)
        if reviewed_refs != tuple(sorted(set(reviewed_refs))):
            raise ValueError("definition reviews must be sorted by unique definition reference")
        if any(
            item.disposition is ReviewDisposition.APPROVE for item in self.definition_reviews
        ):
            raise ValueError("an advisory review cannot approve a definition")
        if self.status != "reviewed_not_adopted" or self.authority != "NONE":
            raise ValueError("a review cannot silently become an adoption decision")
        return self


class KevinAdoptionDecision(ContractModel):
    """Scoped control decision governing what may enter one successor stage."""

    schema_id: str = Field(default="strongwiz.kevin-adoption-decision.v1", alias="schema")
    adoption_id: str
    recommendation_ref: str
    review_ref: str | None
    target_stage_ref: str
    candidate_codebook_ref: str
    approved_codebook_ref: str | None
    definition_decisions: tuple[KevinDefinitionReview, ...]
    evaluation_refs: tuple[str, ...]
    control_source_ref: str
    target_configuration_ref: str
    status: AdoptionStatus
    rationale: str
    scope: str = "successor_working_representation_only"
    transfers_authority: bool = False

    @model_validator(mode="after")
    def validate_adoption(self) -> KevinAdoptionDecision:
        if not self.adoption_id.strip() or not self.rationale.strip():
            raise ValueError("adoption decision identity and rationale are required")
        for value in (
            self.recommendation_ref,
            self.target_stage_ref,
            self.candidate_codebook_ref,
            self.control_source_ref,
            self.target_configuration_ref,
        ):
            _require_digest(value, "adoption decision binding")
        if self.review_ref is not None:
            _require_digest(self.review_ref, "adoption review reference")
        if self.approved_codebook_ref is not None:
            _require_digest(self.approved_codebook_ref, "approved codebook reference")
        _require_sorted_unique(self.evaluation_refs, "adoption evaluation references")
        for value in self.evaluation_refs:
            _require_digest(value, "adoption evaluation reference")
        decided_refs = tuple(item.definition_ref for item in self.definition_decisions)
        if decided_refs != tuple(sorted(set(decided_refs))):
            raise ValueError("adoption definitions must be sorted by unique reference")
        approved = tuple(
            item
            for item in self.definition_decisions
            if item.disposition is ReviewDisposition.APPROVE
        )
        if self.status is AdoptionStatus.APPROVED:
            if self.approved_codebook_ref != self.candidate_codebook_ref:
                raise ValueError("approved adoption must bind its exact candidate codebook")
            if not approved and self.definition_decisions:
                raise ValueError("approved adoption must approve active definitions")
        elif self.approved_codebook_ref is not None or approved:
            raise ValueError("rejected adoption cannot approve a codebook or definition")
        if self.scope != "successor_working_representation_only":
            raise ValueError("Kevin Speak adoption has only working-representation scope")
        if self.transfers_authority:
            raise ValueError("Kevin Speak adoption cannot transfer authority")
        return self


class KevinSpeakEntry(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-entry.v1", alias="schema")
    workspace_id: str
    entry_id: str
    source_payload_ref: str
    codebook_ref: str
    codebook_version: NonNegativeInt
    decoder_artifact_ref: str
    lane: ShorthandLane
    encoded_text: str | None = None
    source_size_bytes: PositiveInt
    representation_size_bytes: PositiveInt
    symbol_uses: NonNegativeInt = 0
    residual_reason: str | None = None
    exact_round_trip: bool = True
    claim_ceiling: str = "representation only; source evidence and authority remain unchanged"

    @model_validator(mode="after")
    def validate_entry(self) -> KevinSpeakEntry:
        if not self.workspace_id.strip() or not self.entry_id.strip():
            raise ValueError("shorthand workspace and entry identities are required")
        for value in (
            self.source_payload_ref,
            self.codebook_ref,
            self.decoder_artifact_ref,
        ):
            _require_digest(value, "shorthand entry binding")
        if not self.exact_round_trip:
            raise ValueError("a shorthand entry must have passed exact round-trip validation")
        if self.lane is ShorthandLane.COMPACT:
            if self.encoded_text is None or self.residual_reason is not None:
                raise ValueError("compact entries require encoded text and no residual reason")
            if self.representation_size_bytes >= self.source_size_bytes:
                raise ValueError("compact entries must be strictly smaller than their source")
            if self.symbol_uses == 0:
                raise ValueError("compact entries must use at least one shorthand symbol")
        else:
            if self.encoded_text is not None or not (
                self.residual_reason and self.residual_reason.strip()
            ):
                raise ValueError("residual entries require an explicit uncompressed reason")
            if self.representation_size_bytes != self.source_size_bytes:
                raise ValueError(
                    "residual representation must retain the canonical source size"
                )
            if self.symbol_uses != 0:
                raise ValueError("residual entries cannot claim shorthand symbol use")
        return self


class KevinWorkspaceGenesis(ContractModel):
    schema_id: str = Field(default="strongwiz.kevin-workspace-genesis.v1", alias="schema")
    workspace_id: str
    mode: str
    initial_codebook_ref: str
    configuration_ref: str
    transfer_ref: str | None = None
    recommendation_bundle_ref: str | None = None
    assertion: str
    authority: str = "NONE"

    @model_validator(mode="after")
    def validate_genesis(self) -> KevinWorkspaceGenesis:
        if self.mode not in {"blank", "explicit_inheritance", "recommendation_review"}:
            raise ValueError("unsupported Kevin Speak workspace genesis mode")
        if not self.workspace_id.strip() or not self.assertion.strip():
            raise ValueError("workspace genesis identity and assertion are required")
        _require_digest(self.initial_codebook_ref, "initial codebook reference")
        _require_digest(self.configuration_ref, "workspace configuration reference")
        if (self.mode == "explicit_inheritance") != (self.transfer_ref is not None):
            raise ValueError("only explicit-inheritance genesis may bind a transfer")
        if (self.mode == "recommendation_review") != (
            self.recommendation_bundle_ref is not None
        ):
            raise ValueError("only review genesis may bind a recommendation bundle")
        if self.transfer_ref is not None:
            _require_digest(self.transfer_ref, "inheritance transfer reference")
        if self.recommendation_bundle_ref is not None:
            _require_digest(self.recommendation_bundle_ref, "recommendation bundle reference")
        return self


class KevinSpeakTransfer(ContractModel):
    """Portable shorthand only; no action, domain-state, or authority transfer."""

    schema_id: str = Field(default="strongwiz.kevin-transfer.v1", alias="schema")
    transfer_id: str
    source_workspace_id: str
    source_run_seal_ref: str
    source_capsule_ref: str
    recommendation_bundle: KevinRecommendationBundle
    review: KevinRecommendationReview | None = None
    review_configuration: KevinSpeakConfiguration | None = None
    review_evaluations: tuple[KevinCodebookEvaluation, ...] = ()
    withheld_definitions: tuple[KevinSymbolDefinition, ...] = ()
    adoption: KevinAdoptionDecision
    target_configuration: KevinSpeakConfiguration
    codebooks: tuple[KevinCodebookRevision, ...]
    active_codebook_ref: str
    excluded_material: tuple[str, ...] = _TRANSFER_EXCLUSIONS
    claim_ceiling: str = "working representation inheritance only"
    transfers_authority: bool = False

    @model_validator(mode="after")
    def validate_transfer(self) -> KevinSpeakTransfer:
        if not all(
            value.strip()
            for value in (self.transfer_id, self.source_workspace_id, self.claim_ceiling)
        ):
            raise ValueError("transfer identity and claim ceiling are required")
        for value in (
            self.source_run_seal_ref,
            self.source_capsule_ref,
            self.active_codebook_ref,
        ):
            _require_digest(value, "transfer binding")
        if not self.codebooks:
            raise ValueError("a shorthand transfer requires its complete codebook lineage")
        if tuple(book.version for book in self.codebooks) != tuple(range(len(self.codebooks))):
            raise ValueError("transfer codebooks must be a contiguous genesis-first lineage")
        if self.codebooks[-1].digest != self.active_codebook_ref:
            raise ValueError("transfer does not end at its active codebook")
        registry = CodebookRegistry()
        for book in self.codebooks:
            registry.register(book)
        bundle_lineage_refs = tuple(
            book.digest for book in self.recommendation_bundle.codebooks
        )
        if tuple(book.digest for book in self.codebooks[: len(bundle_lineage_refs)]) != (
            bundle_lineage_refs
        ):
            raise ValueError("transfer changed the sealed recommendation lineage")
        recommendation = self.recommendation_bundle.recommendation
        if recommendation.recommended_codebook_ref not in {
            book.digest for book in self.codebooks
        }:
            raise ValueError("transfer omits its recommended codebook lineage")
        if recommendation.recommended_definition_refs != (
            registry.effective_definition_refs(recommendation.recommended_codebook_ref)
        ):
            raise ValueError("recommendation definition set changed during transfer")
        if (
            recommendation.source_workspace_id != self.source_workspace_id
            or self.recommendation_bundle.source_run_seal_ref != self.source_run_seal_ref
            or self.recommendation_bundle.source_capsule_ref != self.source_capsule_ref
        ):
            raise ValueError("transfer crosses its source recommendation boundary")
        if self.adoption.recommendation_ref != recommendation.digest:
            raise ValueError("transfer adoption does not bind its recommendation")
        if self.adoption.status is not AdoptionStatus.APPROVED:
            raise ValueError("only an approved shorthand decision may transfer")
        if self.adoption.target_configuration_ref != self.target_configuration.digest:
            raise ValueError("transfer changes its adopted target configuration")
        if self.adoption.approved_codebook_ref != self.active_codebook_ref:
            raise ValueError("transfer codebook is not the adopted codebook")
        if self.review is None:
            if (
                self.review_evaluations
                or self.review_configuration is not None
                or self.withheld_definitions
            ):
                raise ValueError(
                    "unreviewed transfer cannot carry review configuration, evaluations, "
                    "or withheld definitions"
                )
            if self.adoption.review_ref is not None:
                raise ValueError("transfer adoption names a missing review")
            if self.adoption.candidate_codebook_ref != recommendation.recommended_codebook_ref:
                raise ValueError("unreviewed transfer must adopt the recommended codebook")
        elif (
            self.adoption.review_ref != self.review.digest
            or self.review.recommendation_ref != recommendation.digest
            or self.adoption.candidate_codebook_ref != self.review.reviewed_codebook_ref
        ):
            raise ValueError("transfer review and adoption lineage disagree")
        if self.review is not None:
            if (
                self.review_configuration is None
                or self.review.review_configuration_ref != self.review_configuration.digest
            ):
                raise ValueError("transfer changes or omits its review configuration")
            reviewed_effective_refs = set(
                registry.effective_definition_refs(self.review.reviewed_codebook_ref)
            )
            reviewed_lineage_refs = set(
                registry.lineage_definition_refs(self.review.reviewed_codebook_ref)
            )
            recommended_refs = {
                item.definition_ref
                for item in self.review.definition_reviews
                if item.disposition is ReviewDisposition.RECOMMEND
            }
            reviewed_historical_refs = {
                item.definition_ref
                for item in self.review.definition_reviews
                if item.disposition is ReviewDisposition.HISTORICAL_ONLY
            }
            if recommended_refs != reviewed_effective_refs:
                raise ValueError("review does not recommend every effective definition")
            if reviewed_historical_refs != reviewed_lineage_refs - reviewed_effective_refs:
                raise ValueError("review does not identify every historical-only definition")
            review_evaluation_refs = tuple(item.digest for item in self.review_evaluations)
            if review_evaluation_refs != tuple(sorted(set(review_evaluation_refs))):
                raise ValueError("review evaluations must be sorted by unique digest")
            if review_evaluation_refs != self.review.evaluation_refs:
                raise ValueError("transfer omits or adds review evaluation evidence")
            if any(
                item.status is not EvaluationStatus.ELIGIBLE
                or item.candidate_codebook_ref not in {book.digest for book in self.codebooks}
                for item in self.review_evaluations
            ):
                raise ValueError("transfer review evidence did not earn its codebook")
            withheld_refs = tuple(item.digest for item in self.withheld_definitions)
            if withheld_refs != tuple(sorted(set(withheld_refs))):
                raise ValueError("withheld definitions must be sorted by unique digest")
            expected_withheld_refs = tuple(
                sorted(
                    item.definition_ref
                    for item in self.review.definition_reviews
                    if item.disposition in {ReviewDisposition.REJECT, ReviewDisposition.DEFER}
                )
            )
            if withheld_refs != expected_withheld_refs:
                raise ValueError("transfer must carry every rejected or deferred definition")
        if self.adoption.evaluation_refs != tuple(
            sorted(
                set(self.recommendation_bundle.recommendation.evaluation_refs)
                | {item.digest for item in self.review_evaluations}
            )
        ):
            raise ValueError("transfer adoption evidence set is incomplete")
        effective_refs = set(registry.effective_definition_refs(self.active_codebook_ref))
        lineage_refs = set(registry.lineage_definition_refs(self.active_codebook_ref))
        approved_refs = {
            item.definition_ref
            for item in self.adoption.definition_decisions
            if item.disposition is ReviewDisposition.APPROVE
        }
        historical_refs = {
            item.definition_ref
            for item in self.adoption.definition_decisions
            if item.disposition is ReviewDisposition.HISTORICAL_ONLY
        }
        if approved_refs != effective_refs:
            raise ValueError("transfer does not explicitly approve every effective definition")
        if historical_refs != lineage_refs - effective_refs:
            raise ValueError("transfer does not identify every historical-only definition")
        if self.review is not None:
            review_residuals = {
                (item.definition_ref, item.disposition)
                for item in self.review.definition_reviews
                if item.disposition in {ReviewDisposition.REJECT, ReviewDisposition.DEFER}
            }
            adoption_residuals = {
                (item.definition_ref, item.disposition)
                for item in self.adoption.definition_decisions
                if item.disposition in {ReviewDisposition.REJECT, ReviewDisposition.DEFER}
            }
            if review_residuals != adoption_residuals:
                raise ValueError(
                    "transfer dropped or rewrote a rejected or deferred definition"
                )
        if self.excluded_material != _TRANSFER_EXCLUSIONS or self.transfers_authority:
            raise ValueError(
                "shorthand transfer cannot carry excluded run material or authority"
            )
        return self


class KevinWorkspaceVerification(ContractModel):
    workspace_id: str
    configuration_ref: str
    active_codebook_ref: str
    codebook_count: PositiveInt
    evaluation_count: NonNegativeInt
    recommendation_count: NonNegativeInt
    recommendation_bundle_count: NonNegativeInt
    review_count: NonNegativeInt
    adoption_count: NonNegativeInt
    entry_count: NonNegativeInt
    compact_entry_count: NonNegativeInt
    residual_entry_count: NonNegativeInt
    source_bytes: NonNegativeInt
    representation_bytes: NonNegativeInt
    exact_round_trips: bool
    receipt_count: PositiveInt
    receipt_head: str
    claim_ceiling: str = "mechanical workspace integrity only"


class KevinSpeakWorkspace:
    """Receipt-backed adaptive shorthand workspace over a Strongwiz ledger."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger,
        workspace_id: str,
        account_id: str,
        account_version: int,
        registry: CodebookRegistry,
        configuration: KevinSpeakConfiguration,
        active_codebook_ref: str,
        receipt_refs: list[str],
        evaluations: dict[str, KevinCodebookEvaluation] | None = None,
        entries: list[KevinSpeakEntry] | None = None,
        recommendations: dict[str, KevinNextRoundRecommendation] | None = None,
        recommendation_bundles: dict[str, KevinRecommendationBundle] | None = None,
        reviews: dict[str, KevinRecommendationReview] | None = None,
        adoptions: dict[str, KevinAdoptionDecision] | None = None,
    ) -> None:
        self._ledger = ledger
        self.workspace_id = workspace_id
        self.account_id = account_id
        self.account_version = account_version
        self._registry = registry
        self.configuration = configuration
        self._configurations = {configuration.digest: configuration}
        self._active_codebook_ref = active_codebook_ref
        self._receipt_refs = receipt_refs
        self._evaluations = evaluations or {}
        self._entries = entries or []
        self._recommendations = recommendations or {}
        self._recommendation_bundles = recommendation_bundles or {}
        self._reviews = reviews or {}
        self._adoptions = adoptions or {}

    @classmethod
    def open_blank(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace_id: str,
        account_id: str | None = None,
        account_version: int = 0,
        codebook_id: str | None = None,
        configuration: KevinSpeakConfiguration | None = None,
    ) -> KevinSpeakWorkspace:
        if not workspace_id.strip():
            raise KevinSpeakError("workspace identity is required")
        if any(
            envelope.occurrence_id.startswith(f"{workspace_id}:")
            for envelope in ledger.receipts()
        ):
            raise KevinSpeakError("shorthand workspace identity already exists")
        registry = CodebookRegistry()
        genesis = KevinCodebookRevision.blank(
            codebook_id=codebook_id or f"{workspace_id}.kevin-speak"
        )
        registry.register(genesis)
        active_configuration = configuration or KevinSpeakConfiguration()
        workspace = cls(
            ledger=ledger,
            workspace_id=workspace_id,
            account_id=account_id or workspace_id,
            account_version=account_version,
            registry=registry,
            configuration=active_configuration,
            active_codebook_ref=genesis.digest,
            receipt_refs=[],
        )
        codebook_ref = workspace._put_contract(genesis)
        configuration_ref = workspace._put_contract(active_configuration)
        workspace._record(
            "kevin_workspace_genesis",
            KevinWorkspaceGenesis(
                workspace_id=workspace_id,
                mode="blank",
                initial_codebook_ref=genesis.digest,
                configuration_ref=configuration_ref,
                assertion="blank codebook with no inherited shorthand or task state",
            ),
            object_refs=(codebook_ref, configuration_ref),
        )
        return workspace

    @classmethod
    def open_inherited(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace_id: str,
        target_stage_ref: str,
        transfer: KevinSpeakTransfer,
        account_id: str | None = None,
        account_version: int = 0,
    ) -> KevinSpeakWorkspace:
        if not workspace_id.strip():
            raise KevinSpeakError("workspace identity is required")
        _require_digest(target_stage_ref, "successor target stage")
        if transfer.adoption.target_stage_ref != target_stage_ref:
            raise KevinSpeakError("shorthand transfer is approved for another target stage")
        if any(
            envelope.occurrence_id.startswith(f"{workspace_id}:")
            for envelope in ledger.receipts()
        ):
            raise KevinSpeakError("shorthand workspace identity already exists")
        registry = CodebookRegistry()
        for book in transfer.codebooks:
            registry.register(book)
        if registry.require(transfer.active_codebook_ref) != transfer.codebooks[-1]:
            raise KevinSpeakError("inheritance transfer has an invalid active lineage")
        workspace = cls(
            ledger=ledger,
            workspace_id=workspace_id,
            account_id=account_id or workspace_id,
            account_version=account_version,
            registry=registry,
            configuration=transfer.target_configuration,
            active_codebook_ref=transfer.active_codebook_ref,
            receipt_refs=[],
            evaluations={
                item.digest: item
                for item in (
                    *transfer.recommendation_bundle.evaluations,
                    *transfer.review_evaluations,
                )
            },
            recommendations={
                transfer.recommendation_bundle.recommendation.digest: (
                    transfer.recommendation_bundle.recommendation
                )
            },
            recommendation_bundles={
                transfer.recommendation_bundle.digest: transfer.recommendation_bundle
            },
            reviews=(
                {} if transfer.review is None else {transfer.review.digest: transfer.review}
            ),
            adoptions={transfer.adoption.digest: transfer.adoption},
        )
        book_refs = tuple(workspace._put_contract(book) for book in transfer.codebooks)
        definition_refs = tuple(
            workspace._put_contract(definition)
            for book in transfer.codebooks
            for definition in book.definitions
        )
        withheld_definition_refs = tuple(
            workspace._put_contract(definition) for definition in transfer.withheld_definitions
        )
        recommendation_ref = workspace._put_contract(
            transfer.recommendation_bundle.recommendation
        )
        bundle_ref = workspace._put_contract(transfer.recommendation_bundle)
        source_configuration_ref = workspace._put_contract(
            transfer.recommendation_bundle.source_configuration
        )
        review_configuration_refs = (
            ()
            if transfer.review_configuration is None
            else (workspace._put_contract(transfer.review_configuration),)
        )
        evaluation_refs = tuple(
            workspace._put_contract(evaluation)
            for evaluation in (
                *transfer.recommendation_bundle.evaluations,
                *transfer.review_evaluations,
            )
        )
        review_refs = (
            () if transfer.review is None else (workspace._put_contract(transfer.review),)
        )
        adoption_ref = workspace._put_contract(transfer.adoption)
        configuration_ref = workspace._put_contract(transfer.target_configuration)
        transfer_ref = workspace._put_contract(transfer)
        workspace._record(
            "kevin_workspace_genesis",
            KevinWorkspaceGenesis(
                workspace_id=workspace_id,
                mode="explicit_inheritance",
                initial_codebook_ref=transfer.active_codebook_ref,
                configuration_ref=configuration_ref,
                transfer_ref=transfer_ref,
                assertion=(
                    "explicit shorthand lineage only; no action sequence, domain state, "
                    "private reasoning, or authority inherited"
                ),
            ),
            object_refs=(
                *book_refs,
                *definition_refs,
                *withheld_definition_refs,
                recommendation_ref,
                bundle_ref,
                source_configuration_ref,
                *review_configuration_refs,
                *evaluation_refs,
                *review_refs,
                adoption_ref,
                configuration_ref,
                transfer_ref,
            ),
        )
        return workspace

    @classmethod
    def open_review(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace_id: str,
        bundle: KevinRecommendationBundle,
        account_id: str | None = None,
        account_version: int = 0,
        configuration: KevinSpeakConfiguration | None = None,
    ) -> KevinSpeakWorkspace:
        """Open an isolated handoff surface for optional model review."""

        if not workspace_id.strip():
            raise KevinSpeakError("workspace identity is required")
        if any(
            envelope.occurrence_id.startswith(f"{workspace_id}:")
            for envelope in ledger.receipts()
        ):
            raise KevinSpeakError("shorthand workspace identity already exists")
        registry = CodebookRegistry()
        for book in bundle.codebooks:
            registry.register(book)
        recommendation = bundle.recommendation
        review_configuration = configuration or bundle.source_configuration
        workspace = cls(
            ledger=ledger,
            workspace_id=workspace_id,
            account_id=account_id or workspace_id,
            account_version=account_version,
            registry=registry,
            configuration=review_configuration,
            active_codebook_ref=recommendation.recommended_codebook_ref,
            receipt_refs=[],
            evaluations={item.digest: item for item in bundle.evaluations},
            recommendations={recommendation.digest: recommendation},
            recommendation_bundles={bundle.digest: bundle},
        )
        book_refs = tuple(workspace._put_contract(book) for book in bundle.codebooks)
        definition_refs = tuple(
            workspace._put_contract(definition)
            for book in bundle.codebooks
            for definition in book.definitions
        )
        evaluation_refs = tuple(
            workspace._put_contract(evaluation) for evaluation in bundle.evaluations
        )
        recommendation_ref = workspace._put_contract(recommendation)
        bundle_ref = workspace._put_contract(bundle)
        source_configuration_ref = workspace._put_contract(bundle.source_configuration)
        configuration_ref = workspace._put_contract(review_configuration)
        workspace._record(
            "kevin_workspace_genesis",
            KevinWorkspaceGenesis(
                workspace_id=workspace_id,
                mode="recommendation_review",
                initial_codebook_ref=recommendation.recommended_codebook_ref,
                configuration_ref=configuration_ref,
                recommendation_bundle_ref=bundle_ref,
                assertion=(
                    "isolated review of sealed shorthand evidence; no action sequence, "
                    "domain state, private reasoning, or authority inherited"
                ),
            ),
            object_refs=(
                *book_refs,
                *definition_refs,
                *evaluation_refs,
                recommendation_ref,
                bundle_ref,
                source_configuration_ref,
                configuration_ref,
            ),
        )
        return workspace

    @property
    def active_codebook(self) -> KevinCodebookRevision:
        return self._registry.require(self._active_codebook_ref)

    @property
    def entries(self) -> tuple[KevinSpeakEntry, ...]:
        return tuple(self._entries)

    @property
    def recommendations(self) -> tuple[KevinNextRoundRecommendation, ...]:
        return tuple(self._recommendations.values())

    @property
    def recommendation_bundles(self) -> tuple[KevinRecommendationBundle, ...]:
        return tuple(self._recommendation_bundles.values())

    @property
    def reviews(self) -> tuple[KevinRecommendationReview, ...]:
        return tuple(self._reviews.values())

    @property
    def adoption_decisions(self) -> tuple[KevinAdoptionDecision, ...]:
        return tuple(self._adoptions.values())

    def close(self) -> None:
        """Close the underlying ledger handle owned by the caller."""

        self._ledger.close()

    def _put_contract(self, value: ContractModel) -> str:
        stored = self._ledger.put_object(value.model_dump(mode="json", by_alias=True))
        if stored != value.digest:
            raise KevinSpeakError("stored shorthand contract identity changed")
        return stored

    def _configuration(self, configuration_ref: str) -> KevinSpeakConfiguration:
        try:
            return self._configurations[configuration_ref]
        except KeyError:
            try:
                configuration = KevinSpeakConfiguration.model_validate(
                    self._ledger.get_payload(configuration_ref)
                )
            except (KeyError, ValueError) as error:
                raise KevinSpeakError("unknown Kevin Speak configuration") from error
            if configuration.digest != configuration_ref:
                raise KevinSpeakError("Kevin Speak configuration identity changed") from None
            self._configurations[configuration_ref] = configuration
            return configuration

    def _record(
        self,
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str:
        parent_refs = () if not self._receipt_refs else (self._receipt_refs[-1],)
        envelope = self._ledger.append(
            occurrence_id=f"{self.workspace_id}:{len(self._receipt_refs):08d}:{kind}",
            kind=kind,
            account_id=self.account_id,
            account_version=self.account_version,
            payload=value.model_dump(mode="json", by_alias=True),
            object_refs=tuple(dict.fromkeys(object_refs)),
            parent_refs=parent_refs,
        )
        self._receipt_refs.append(envelope.receipt_id)
        return envelope.receipt_id

    def propose_revision(
        self,
        *,
        proposals: Sequence[KevinSymbolProposal],
        retired_tokens: tuple[str, ...] = (),
        rationale: str,
        model_proposal_ref: str | None = None,
    ) -> KevinCodebookRevision:
        revision = self._registry.build_revision(
            predecessor_ref=self._active_codebook_ref,
            proposals=proposals,
            retired_tokens=retired_tokens,
            rationale=rationale,
            model_proposal_ref=model_proposal_ref,
        )
        revision_ref = self._put_contract(revision)
        definition_refs = tuple(
            self._put_contract(definition) for definition in revision.definitions
        )
        self._record(
            "kevin_codebook_candidate",
            revision,
            object_refs=(revision_ref, *definition_refs, self._active_codebook_ref),
        )
        return revision

    @staticmethod
    def _representation(source: str, translations: Mapping[str, str]) -> EncodedText:
        return encode_shorthand_text(source, translations)

    def evaluate_candidate(
        self,
        candidate_ref: str,
        samples: Sequence[KevinEvaluationSample],
        *,
        evaluation_id: str,
        policy: KevinPromotionPolicy | None = None,
    ) -> KevinCodebookEvaluation:
        active = self.active_codebook
        candidate = self._registry.require(candidate_ref)
        if candidate.predecessor_ref != active.digest:
            raise KevinSpeakError("candidate evaluation requires the active predecessor")
        if not evaluation_id.strip():
            raise KevinSpeakError("evaluation identity is required")
        case_ids = tuple(sample.case_id for sample in samples)
        if len(set(case_ids)) != len(case_ids):
            raise KevinSpeakError("evaluation case identities must be unique")
        active_policy = self.configuration.promotion_policy
        if policy is not None and policy.digest != active_policy.digest:
            raise KevinSpeakError("evaluation policy differs from the frozen configuration")
        predecessor_translations = self._registry.resolved(active.digest)
        candidate_translations = self._registry.resolved(candidate.digest)
        cases: list[KevinEvaluationCase] = []
        for sample in samples:
            payload_bytes = canonical_bytes(sample.payload)
            payload_ref = self._ledger.put_object(sample.payload)
            source = payload_bytes.decode("utf-8")
            before = self._representation(source, predecessor_translations)
            after = self._representation(source, candidate_translations)
            before_size = min(before.source_size_bytes, before.encoded_size_bytes)
            after_size = min(after.source_size_bytes, after.encoded_size_bytes)
            decoded = decode_shorthand_text(after.encoded, candidate_translations)
            exact = decoded.encode("utf-8") == payload_bytes
            cases.append(
                KevinEvaluationCase(
                    case_id=sample.case_id,
                    role=sample.role,
                    payload_ref=payload_ref,
                    source_size_bytes=len(payload_bytes),
                    predecessor_representation_bytes=before_size,
                    candidate_representation_bytes=after_size,
                    exact_round_trip=exact,
                )
            )
        reasons: list[str] = []
        adaptation_cases = tuple(
            item for item in cases if item.role is EvaluationRole.ADAPTATION
        )
        validation_cases = tuple(
            item for item in cases if item.role is EvaluationRole.VALIDATION
        )
        if len(cases) < active_policy.minimum_cases:
            reasons.append("insufficient_total_cases")
        if len(adaptation_cases) < active_policy.minimum_adaptation_cases:
            reasons.append("insufficient_adaptation_cases")
        if len(validation_cases) < active_policy.minimum_validation_cases:
            reasons.append("insufficient_validation_cases")
        if not all(item.exact_round_trip for item in cases):
            reasons.append("round_trip_failure")
        validation_improvement = any(
            item.candidate_representation_bytes < item.predecessor_representation_bytes
            for item in validation_cases
        )
        if active_policy.require_validation_improvement and not validation_improvement:
            reasons.append("no_validation_improvement")
        source_refs = {
            ref
            for definition in candidate.definitions
            for ref in definition.source_payload_refs
        }
        if active_policy.forbid_validation_source_reuse and any(
            item.payload_ref in source_refs for item in validation_cases
        ):
            reasons.append("validation_payload_used_to_define_candidate")
        gross_savings = sum(
            item.predecessor_representation_bytes - item.candidate_representation_bytes
            for item in cases
        )
        transport = len(canonical_bytes(candidate))
        net_savings = gross_savings - transport
        if (
            len(self._registry.effective_definition_refs(candidate.digest))
            > self.configuration.max_active_symbols
        ):
            reasons.append("active_symbol_budget_exceeded")
        if transport > self.configuration.max_incremental_codebook_bytes:
            reasons.append("incremental_codebook_budget_exceeded")
        if active_policy.require_net_savings and net_savings <= 0:
            reasons.append("codebook_cost_not_recovered")
        evaluation = KevinCodebookEvaluation(
            workspace_id=self.workspace_id,
            evaluation_id=evaluation_id,
            candidate_codebook_ref=candidate.digest,
            predecessor_codebook_ref=active.digest,
            promotion_policy_ref=active_policy.digest,
            cases=tuple(cases),
            incremental_codebook_bytes=transport,
            gross_content_savings_bytes=gross_savings,
            net_savings_bytes=net_savings,
            status=(EvaluationStatus.ELIGIBLE if not reasons else EvaluationStatus.NOT_EARNED),
            reasons=tuple(sorted(set(reasons))),
        )
        evaluation_ref = self._put_contract(evaluation)
        policy_ref = self._put_contract(active_policy)
        object_refs = tuple(
            dict.fromkeys(
                (
                    evaluation_ref,
                    policy_ref,
                    candidate.digest,
                    active.digest,
                    *(item.payload_ref for item in cases),
                )
            )
        )
        self._record("kevin_codebook_evaluation", evaluation, object_refs=object_refs)
        self._evaluations[evaluation.digest] = evaluation
        return evaluation

    def promote(
        self,
        *,
        candidate_ref: str,
        evaluation_ref: str,
        policy: KevinPromotionPolicy | None = None,
    ) -> KevinPromotionReceipt:
        active_policy = self.configuration.promotion_policy
        if policy is not None and policy.digest != active_policy.digest:
            raise KevinSpeakError("promotion policy differs from the frozen configuration")
        try:
            evaluation = self._evaluations[evaluation_ref]
        except KeyError as error:
            raise KevinSpeakError("promotion evaluation is not registered") from error
        candidate = self._registry.require(candidate_ref)
        if (
            evaluation.status is not EvaluationStatus.ELIGIBLE
            or evaluation.candidate_codebook_ref != candidate.digest
            or evaluation.predecessor_codebook_ref != self._active_codebook_ref
            or evaluation.promotion_policy_ref != active_policy.digest
            or candidate.predecessor_ref != self._active_codebook_ref
        ):
            raise KevinSpeakError("codebook promotion has not earned every exact binding")
        policy_ref = self._put_contract(active_policy)
        receipt = KevinPromotionReceipt(
            workspace_id=self.workspace_id,
            predecessor_codebook_ref=self._active_codebook_ref,
            promoted_codebook_ref=candidate.digest,
            evaluation_ref=evaluation.digest,
            policy_ref=policy_ref,
        )
        receipt_ref = self._put_contract(receipt)
        self._record(
            "kevin_codebook_promotion",
            receipt,
            object_refs=(
                receipt_ref,
                candidate.digest,
                evaluation.digest,
                policy_ref,
                self._active_codebook_ref,
            ),
        )
        self._active_codebook_ref = candidate.digest
        return receipt

    def _eligible_evaluations(
        self,
        evaluation_refs: Sequence[str],
        *,
        allowed_codebook_refs: set[str],
        require_candidate_ref: str | None = None,
    ) -> tuple[str, ...]:
        refs = tuple(sorted(set(evaluation_refs)))
        if len(refs) != len(tuple(evaluation_refs)):
            raise KevinSpeakError("evaluation references must be unique")
        candidates: set[str] = set()
        for evaluation_ref in refs:
            try:
                evaluation = self._evaluations[evaluation_ref]
            except KeyError as error:
                raise KevinSpeakError("next-round evidence is not registered") from error
            if (
                evaluation.status is not EvaluationStatus.ELIGIBLE
                or evaluation.candidate_codebook_ref not in allowed_codebook_refs
            ):
                raise KevinSpeakError("next-round evidence did not earn its codebook")
            candidates.add(evaluation.candidate_codebook_ref)
        if require_candidate_ref is not None and require_candidate_ref not in candidates:
            raise KevinSpeakError("refined codebook lacks its own eligible evaluation")
        return refs

    def recommend_next_round(
        self,
        *,
        recommendation_id: str,
        recommending_driver_ref: str,
        evaluation_refs: Sequence[str],
        rationale: str,
        known_residuals: Sequence[str] = (),
    ) -> KevinNextRoundRecommendation:
        """Record what this run recommends without approving successor use."""

        active = self.active_codebook
        lineage_refs = {book.digest for book in self._registry.lineage(active.digest)}
        evidence_refs = self._eligible_evaluations(
            evaluation_refs, allowed_codebook_refs=lineage_refs
        )
        if active.version > 0 and not evidence_refs:
            raise KevinSpeakError("a nonblank recommendation requires eligible evaluation")
        if not self._receipt_refs:
            raise KevinSpeakError("recommendation requires a durable evidence boundary")
        recommendation = KevinNextRoundRecommendation(
            recommendation_id=recommendation_id,
            source_workspace_id=self.workspace_id,
            source_evidence_boundary_ref=self._receipt_refs[-1],
            source_configuration_ref=self.configuration.digest,
            recommended_codebook_ref=active.digest,
            recommended_definition_refs=self._registry.effective_definition_refs(active.digest),
            evaluation_refs=evidence_refs,
            recommending_driver_ref=recommending_driver_ref,
            rationale=rationale,
            known_residuals=tuple(sorted(set(known_residuals))),
        )
        if recommendation.digest in self._recommendations:
            raise KevinSpeakError("next-round recommendation already exists")
        recommendation_ref = self._put_contract(recommendation)
        self._record(
            "kevin_next_round_recommendation",
            recommendation,
            object_refs=(
                recommendation_ref,
                active.digest,
                self.configuration.digest,
                *recommendation.recommended_definition_refs,
                *evidence_refs,
            ),
        )
        self._recommendations[recommendation.digest] = recommendation
        return recommendation

    def export_recommendation_bundle(
        self,
        *,
        recommendation_ref: str,
        source_run_seal_ref: str,
        source_capsule_ref: str,
    ) -> KevinRecommendationBundle:
        """Bind a recorded recommendation to a later immutable run seal."""

        try:
            recommendation = self._recommendations[recommendation_ref]
        except KeyError as error:
            raise KevinSpeakError("recommendation bundle source is not registered") from error
        if recommendation.source_workspace_id != self.workspace_id:
            raise KevinSpeakError("only the source workspace may bundle its recommendation")
        evaluations = tuple(
            sorted(
                (self._evaluations[value] for value in recommendation.evaluation_refs),
                key=lambda item: item.digest,
            )
        )
        return KevinRecommendationBundle(
            source_run_seal_ref=source_run_seal_ref,
            source_capsule_ref=source_capsule_ref,
            recommendation=recommendation,
            source_configuration=self.configuration,
            codebooks=self._registry.lineage(recommendation.recommended_codebook_ref),
            evaluations=evaluations,
        )

    def review_next_round(
        self,
        *,
        review_id: str,
        recommendation_ref: str,
        reviewer_driver_ref: str,
        evaluation_refs: Sequence[str],
        rationale: str,
        reviewed_codebook_ref: str | None = None,
        rejected_definition_refs: Sequence[str] = (),
        deferred_definition_refs: Sequence[str] = (),
    ) -> KevinRecommendationReview:
        """Review or refine a recommendation without adopting it."""

        try:
            recommendation = self._recommendations[recommendation_ref]
        except KeyError as error:
            raise KevinSpeakError("review recommendation is not registered") from error
        selected_ref = reviewed_codebook_ref or recommendation.recommended_codebook_ref
        selected = self._registry.require(selected_ref)
        selected_lineage = self._registry.lineage(selected.digest)
        selected_lineage_refs = {book.digest for book in selected_lineage}
        if recommendation.recommended_codebook_ref not in selected_lineage_refs:
            raise KevinSpeakError("reviewed refinement leaves the recommended lineage")
        evidence_refs = self._eligible_evaluations(
            evaluation_refs,
            allowed_codebook_refs=selected_lineage_refs,
            require_candidate_ref=(
                selected.digest
                if selected.digest != recommendation.recommended_codebook_ref
                else None
            ),
        )
        effective_refs = set(self._registry.effective_definition_refs(selected.digest))
        historical_refs = set(self._registry.lineage_definition_refs(selected.digest)) - (
            effective_refs
        )
        rejected_refs = set(rejected_definition_refs)
        deferred_refs = set(deferred_definition_refs)
        if rejected_refs & deferred_refs:
            raise KevinSpeakError("one definition cannot be both rejected and deferred")
        if (rejected_refs | deferred_refs) & (effective_refs | historical_refs):
            raise KevinSpeakError(
                "active or historical definitions need their exact disposition"
            )
        if not (rejected_refs | deferred_refs) <= self._registry.known_definition_refs():
            raise KevinSpeakError("review names an unknown definition")
        definition_reviews = tuple(
            sorted(
                (
                    *(
                        KevinDefinitionReview(
                            definition_ref=value,
                            disposition=ReviewDisposition.RECOMMEND,
                            rationale="effective definition recommended for successor use",
                        )
                        for value in effective_refs
                    ),
                    *(
                        KevinDefinitionReview(
                            definition_ref=value,
                            disposition=ReviewDisposition.HISTORICAL_ONLY,
                            rationale="retained only to reconstruct the immutable lineage",
                        )
                        for value in historical_refs
                    ),
                    *(
                        KevinDefinitionReview(
                            definition_ref=value,
                            disposition=ReviewDisposition.REJECT,
                            rationale="reviewer rejected this alternative definition",
                        )
                        for value in rejected_refs
                    ),
                    *(
                        KevinDefinitionReview(
                            definition_ref=value,
                            disposition=ReviewDisposition.DEFER,
                            rationale="reviewer left this alternative definition provisional",
                        )
                        for value in deferred_refs
                    ),
                ),
                key=lambda item: item.definition_ref,
            )
        )
        review = KevinRecommendationReview(
            review_id=review_id,
            recommendation_ref=recommendation.digest,
            original_codebook_ref=recommendation.recommended_codebook_ref,
            reviewed_codebook_ref=selected.digest,
            reviewer_driver_ref=reviewer_driver_ref,
            review_configuration_ref=self.configuration.digest,
            evaluation_refs=evidence_refs,
            definition_reviews=definition_reviews,
            rationale=rationale,
        )
        if review.digest in self._reviews:
            raise KevinSpeakError("next-round review already exists")
        review_ref = self._put_contract(review)
        self._record(
            "kevin_next_round_review",
            review,
            object_refs=(
                review_ref,
                recommendation.digest,
                selected.digest,
                self.configuration.digest,
                *evidence_refs,
                *(item.definition_ref for item in definition_reviews),
            ),
        )
        self._reviews[review.digest] = review
        return review

    def decide_next_round_adoption(
        self,
        *,
        adoption_id: str,
        recommendation_ref: str,
        target_stage_ref: str,
        control_source_ref: str,
        approve: bool,
        rationale: str,
        review_ref: str | None = None,
        target_configuration: KevinSpeakConfiguration | None = None,
    ) -> KevinAdoptionDecision:
        """Approve or reject one exact successor representation under supplied control."""

        try:
            recommendation = self._recommendations[recommendation_ref]
        except KeyError as error:
            raise KevinSpeakError("adoption recommendation is not registered") from error
        review: KevinRecommendationReview | None = None
        selected_ref = recommendation.recommended_codebook_ref
        if review_ref is not None:
            try:
                review = self._reviews[review_ref]
            except KeyError as error:
                raise KevinSpeakError("adoption review is not registered") from error
            if review.recommendation_ref != recommendation.digest:
                raise KevinSpeakError("adoption review crosses its recommendation")
            selected_ref = review.reviewed_codebook_ref
        effective_refs = set(self._registry.effective_definition_refs(selected_ref))
        historical_refs = set(self._registry.lineage_definition_refs(selected_ref)) - (
            effective_refs
        )
        decisions: dict[str, KevinDefinitionReview] = {}
        for value in effective_refs:
            decisions[value] = KevinDefinitionReview(
                definition_ref=value,
                disposition=(
                    ReviewDisposition.APPROVE if approve else ReviewDisposition.REJECT
                ),
                rationale=(
                    "approved for encoding new successor working entries"
                    if approve
                    else "not approved for successor working entries"
                ),
            )
        for value in historical_refs:
            decisions[value] = KevinDefinitionReview(
                definition_ref=value,
                disposition=ReviewDisposition.HISTORICAL_ONLY,
                rationale="required only to reconstruct the immutable codebook lineage",
            )
        if review is not None:
            for item in review.definition_reviews:
                if item.definition_ref not in decisions:
                    decisions[item.definition_ref] = item
        evidence_refs = tuple(
            sorted(
                set(recommendation.evaluation_refs)
                | (set() if review is None else set(review.evaluation_refs))
            )
        )
        successor_configuration = target_configuration or self.configuration
        decision = KevinAdoptionDecision(
            adoption_id=adoption_id,
            recommendation_ref=recommendation.digest,
            review_ref=None if review is None else review.digest,
            target_stage_ref=target_stage_ref,
            candidate_codebook_ref=selected_ref,
            approved_codebook_ref=selected_ref if approve else None,
            definition_decisions=tuple(
                sorted(decisions.values(), key=lambda item: item.definition_ref)
            ),
            evaluation_refs=evidence_refs,
            control_source_ref=control_source_ref,
            target_configuration_ref=successor_configuration.digest,
            status=AdoptionStatus.APPROVED if approve else AdoptionStatus.REJECTED,
            rationale=rationale,
        )
        if decision.digest in self._adoptions:
            raise KevinSpeakError("next-round adoption decision already exists")
        configuration_ref = self._put_contract(successor_configuration)
        self._configurations[configuration_ref] = successor_configuration
        decision_ref = self._put_contract(decision)
        self._record(
            "kevin_next_round_adoption",
            decision,
            object_refs=(
                decision_ref,
                recommendation.digest,
                *((review.digest,) if review is not None else ()),
                selected_ref,
                configuration_ref,
                *evidence_refs,
                *(item.definition_ref for item in decision.definition_decisions),
            ),
        )
        self._adoptions[decision.digest] = decision
        return decision

    def append(self, *, entry_id: str, payload: object) -> KevinSpeakEntry:
        if not entry_id.strip():
            raise KevinSpeakError("shorthand entry identity is required")
        if any(item.entry_id == entry_id for item in self._entries):
            raise KevinSpeakError("shorthand entry identity cannot be reused")
        source_bytes = canonical_bytes(payload)
        if len(source_bytes) > self.configuration.max_entry_bytes:
            raise KevinSpeakError("canonical entry exceeds the frozen shorthand entry budget")
        source_payload = parse_strict_json(source_bytes)
        source_ref = sha256_bytes(source_bytes)
        codebook = self.active_codebook
        translations = self._registry.resolved(codebook.digest)
        encoded = encode_shorthand_text(source_bytes.decode("utf-8"), translations)
        if encoded.symbol_uses and encoded.encoded_size_bytes < encoded.source_size_bytes:
            lane = ShorthandLane.COMPACT
            encoded_text: str | None = encoded.encoded
            representation_size = encoded.encoded_size_bytes
            symbol_uses = encoded.symbol_uses
            residual_reason = None
            round_trip = decode_shorthand_text(encoded.encoded, translations)
            if round_trip.encode("utf-8") != source_bytes:
                raise KevinSpeakError("compact entry failed exact canonical reconstruction")
        else:
            lane = ShorthandLane.RESIDUAL
            encoded_text = None
            representation_size = len(source_bytes)
            symbol_uses = 0
            residual_reason = (
                "no_registered_symbol_match"
                if encoded.symbol_uses == 0
                else "shorthand_not_smaller_than_canonical_source"
            )
        entry = KevinSpeakEntry(
            workspace_id=self.workspace_id,
            entry_id=entry_id,
            source_payload_ref=source_ref,
            codebook_ref=codebook.digest,
            codebook_version=codebook.version,
            decoder_artifact_ref=codebook.decoder_artifact_ref,
            lane=lane,
            encoded_text=encoded_text,
            source_size_bytes=len(source_bytes),
            representation_size_bytes=representation_size,
            symbol_uses=symbol_uses,
            residual_reason=residual_reason,
        )
        entry_ref = self._put_contract(entry)
        source_object_refs: tuple[str, ...] = ()
        if lane is ShorthandLane.RESIDUAL:
            stored_source_ref = self._ledger.put_object(source_payload)
            if stored_source_ref != source_ref:
                raise KevinSpeakError("residual source identity changed during storage")
            source_object_refs = (stored_source_ref,)
        self._record(
            "kevin_entry",
            entry,
            object_refs=(entry_ref, *source_object_refs, codebook.digest),
        )
        self._entries.append(entry)
        return entry

    def decode_entry(self, entry: KevinSpeakEntry) -> ImmutableJSONValue:
        if entry.workspace_id != self.workspace_id:
            raise KevinSpeakError("entry belongs to another shorthand workspace")
        codebook = self._registry.require(entry.codebook_ref)
        if (
            entry.codebook_version != codebook.version
            or entry.decoder_artifact_ref != codebook.decoder_artifact_ref
        ):
            raise KevinSpeakError("entry codebook or decoder binding changed")
        if entry.lane is ShorthandLane.RESIDUAL:
            payload = self._ledger.get_payload(entry.source_payload_ref)
            raw = canonical_bytes(payload)
        else:
            if entry.encoded_text is None:  # pragma: no cover - model invariant
                raise KevinSpeakError("compact entry lost its encoded text")
            decoded = decode_shorthand_text(
                entry.encoded_text, self._registry.resolved(entry.codebook_ref)
            )
            raw = decoded.encode("utf-8")
            payload = parse_strict_json(raw)
            if canonical_bytes(payload) != raw:
                raise KevinSpeakError("decoded shorthand is not canonical JSON")
        if sha256_bytes(raw) != entry.source_payload_ref:
            raise KevinSpeakError("decoded shorthand disagrees with source identity")
        return payload

    def translation_table(self) -> KevinTranslationTable:
        return self._registry.table(self._active_codebook_ref)

    def export_transfer(
        self,
        *,
        transfer_id: str,
        adoption_ref: str,
    ) -> KevinSpeakTransfer:
        try:
            adoption = self._adoptions[adoption_ref]
        except KeyError as error:
            raise KevinSpeakError("transfer adoption decision is not registered") from error
        if adoption.status is not AdoptionStatus.APPROVED:
            raise KevinSpeakError("rejected shorthand cannot cross into a successor run")
        try:
            recommendation = self._recommendations[adoption.recommendation_ref]
        except KeyError as error:  # pragma: no cover - construction invariant
            raise KevinSpeakError("transfer lost its recommendation") from error
        review = None if adoption.review_ref is None else self._reviews[adoption.review_ref]
        bundles = tuple(
            bundle
            for bundle in self._recommendation_bundles.values()
            if bundle.recommendation.digest == recommendation.digest
        )
        if len(bundles) != 1:
            raise KevinSpeakError(
                "transfer requires one sealed recommendation bundle in a review workspace"
            )
        bundle = bundles[0]
        review_evaluations = tuple(
            ()
            if review is None
            else sorted(
                (self._evaluations[value] for value in review.evaluation_refs),
                key=lambda item: item.digest,
            )
        )
        withheld_definitions = tuple(
            ()
            if review is None
            else sorted(
                (
                    self._registry.definition(item.definition_ref)
                    for item in review.definition_reviews
                    if item.disposition in {ReviewDisposition.REJECT, ReviewDisposition.DEFER}
                ),
                key=lambda item: item.digest,
            )
        )
        transfer = KevinSpeakTransfer(
            transfer_id=transfer_id,
            source_workspace_id=recommendation.source_workspace_id,
            source_run_seal_ref=bundle.source_run_seal_ref,
            source_capsule_ref=bundle.source_capsule_ref,
            recommendation_bundle=bundle,
            review=review,
            review_configuration=None if review is None else self.configuration,
            review_evaluations=review_evaluations,
            withheld_definitions=withheld_definitions,
            adoption=adoption,
            target_configuration=self._configuration(adoption.target_configuration_ref),
            codebooks=self._registry.lineage(adoption.candidate_codebook_ref),
            active_codebook_ref=adoption.candidate_codebook_ref,
        )
        transfer_ref = self._put_contract(transfer)
        self._record(
            "kevin_transfer_export",
            transfer,
            object_refs=(
                transfer_ref,
                bundle.digest,
                recommendation.digest,
                *((review.digest,) if review is not None else ()),
                *(
                    ()
                    if transfer.review_configuration is None
                    else (transfer.review_configuration.digest,)
                ),
                *(item.digest for item in review_evaluations),
                *(item.digest for item in withheld_definitions),
                adoption.digest,
                transfer.target_configuration.digest,
                *(book.digest for book in transfer.codebooks),
                *(
                    definition.digest
                    for book in transfer.codebooks
                    for definition in book.definitions
                ),
            ),
        )
        return transfer

    def verify(self) -> KevinWorkspaceVerification:
        self._ledger.verify()
        for entry in self._entries:
            self.decode_entry(entry)
        compact = sum(item.lane is ShorthandLane.COMPACT for item in self._entries)
        residual = len(self._entries) - compact
        return KevinWorkspaceVerification(
            workspace_id=self.workspace_id,
            configuration_ref=self.configuration.digest,
            active_codebook_ref=self._active_codebook_ref,
            codebook_count=len(self._registry.lineage(self._active_codebook_ref)),
            evaluation_count=len(self._evaluations),
            recommendation_count=len(self._recommendations),
            recommendation_bundle_count=len(self._recommendation_bundles),
            review_count=len(self._reviews),
            adoption_count=len(self._adoptions),
            entry_count=len(self._entries),
            compact_entry_count=compact,
            residual_entry_count=residual,
            source_bytes=sum(item.source_size_bytes for item in self._entries),
            representation_bytes=sum(item.representation_size_bytes for item in self._entries),
            exact_round_trips=True,
            receipt_count=len(self._receipt_refs),
            receipt_head=self._receipt_refs[-1],
        )

    @classmethod
    def restore(
        cls,
        ledger: SQLiteLedger,
        *,
        workspace_id: str,
        account_id: str | None = None,
        account_version: int = 0,
    ) -> KevinSpeakWorkspace:
        ledger.verify()
        expected_account = account_id or workspace_id
        selected: list[tuple[ReceiptEnvelope, Mapping[str, object]]] = []
        for envelope in ledger.receipts():
            if (
                envelope.account_id != expected_account
                or envelope.account_version != account_version
            ):
                continue
            if not envelope.occurrence_id.startswith(f"{workspace_id}:"):
                continue
            payload = ledger.get_payload(envelope.payload_hash)
            if isinstance(payload, Mapping):
                selected.append((envelope, payload))
        if not selected:
            raise KevinSpeakError("workspace has no durable shorthand receipts")
        for index, (envelope, _payload) in enumerate(selected):
            if envelope.occurrence_id != f"{workspace_id}:{index:08d}:{envelope.kind}":
                raise KevinSpeakError("workspace receipt occurrence sequence is invalid")
            expected_parent = () if index == 0 else (selected[index - 1][0].receipt_id,)
            if envelope.parent_refs != expected_parent:
                raise KevinSpeakError("workspace receipt lineage is broken")
        first_envelope, first_payload = selected[0]
        if first_envelope.kind != "kevin_workspace_genesis":
            raise KevinSpeakError("workspace lineage does not begin at shorthand genesis")
        genesis = KevinWorkspaceGenesis.model_validate(first_payload)
        configuration = KevinSpeakConfiguration.model_validate(
            ledger.get_payload(genesis.configuration_ref)
        )
        if configuration.digest != genesis.configuration_ref:
            raise KevinSpeakError("restored workspace configuration identity changed")
        registry = CodebookRegistry()
        inherited_transfer: KevinSpeakTransfer | None = None
        inherited_bundle: KevinRecommendationBundle | None = None
        if genesis.mode == "blank":
            book = KevinCodebookRevision.model_validate(
                ledger.get_payload(genesis.initial_codebook_ref)
            )
            registry.register(book)
        elif genesis.mode == "explicit_inheritance":
            transfer_ref = genesis.transfer_ref
            if transfer_ref is None:  # pragma: no cover - model invariant
                raise KevinSpeakError("inherited workspace lost its transfer")
            inherited_transfer = KevinSpeakTransfer.model_validate(
                ledger.get_payload(transfer_ref)
            )
            for book in inherited_transfer.codebooks:
                registry.register(book)
            inherited_bundle = inherited_transfer.recommendation_bundle
        else:
            bundle_ref = genesis.recommendation_bundle_ref
            if bundle_ref is None:  # pragma: no cover - model invariant
                raise KevinSpeakError("review workspace lost its recommendation bundle")
            inherited_bundle = KevinRecommendationBundle.model_validate(
                ledger.get_payload(bundle_ref)
            )
            for book in inherited_bundle.codebooks:
                registry.register(book)
        active_ref = genesis.initial_codebook_ref
        evaluations: dict[str, KevinCodebookEvaluation] = (
            {}
            if inherited_bundle is None
            else {item.digest: item for item in inherited_bundle.evaluations}
        )
        if inherited_transfer is not None:
            evaluations.update(
                {item.digest: item for item in inherited_transfer.review_evaluations}
            )
        entries: list[KevinSpeakEntry] = []
        recommendations: dict[str, KevinNextRoundRecommendation] = (
            {}
            if inherited_bundle is None
            else {inherited_bundle.recommendation.digest: inherited_bundle.recommendation}
        )
        recommendation_bundles: dict[str, KevinRecommendationBundle] = (
            {} if inherited_bundle is None else {inherited_bundle.digest: inherited_bundle}
        )
        reviews: dict[str, KevinRecommendationReview] = (
            {}
            if inherited_transfer is None or inherited_transfer.review is None
            else {inherited_transfer.review.digest: inherited_transfer.review}
        )
        adoptions: dict[str, KevinAdoptionDecision] = (
            {}
            if inherited_transfer is None
            else {inherited_transfer.adoption.digest: inherited_transfer.adoption}
        )
        for envelope, stored_payload in selected[1:]:
            if envelope.kind == "kevin_codebook_candidate":
                registry.register(KevinCodebookRevision.model_validate(stored_payload))
            elif envelope.kind == "kevin_codebook_evaluation":
                evaluation = KevinCodebookEvaluation.model_validate(stored_payload)
                registry.require(evaluation.candidate_codebook_ref)
                evaluations[evaluation.digest] = evaluation
            elif envelope.kind == "kevin_codebook_promotion":
                promotion = KevinPromotionReceipt.model_validate(stored_payload)
                if promotion.predecessor_codebook_ref != active_ref:
                    raise KevinSpeakError("restored promotion crosses active codebook state")
                candidate_evaluation = evaluations.get(promotion.evaluation_ref)
                if (
                    candidate_evaluation is None
                    or candidate_evaluation.status is not EvaluationStatus.ELIGIBLE
                    or candidate_evaluation.candidate_codebook_ref
                    != promotion.promoted_codebook_ref
                ):
                    raise KevinSpeakError("restored promotion lacks eligible evaluation")
                registry.require(promotion.promoted_codebook_ref)
                active_ref = promotion.promoted_codebook_ref
            elif envelope.kind == "kevin_entry":
                entry = KevinSpeakEntry.model_validate(stored_payload)
                if any(item.entry_id == entry.entry_id for item in entries):
                    raise KevinSpeakError("restored entry identity is duplicated")
                registry.require(entry.codebook_ref)
                entries.append(entry)
            elif envelope.kind == "kevin_next_round_recommendation":
                recommendation = KevinNextRoundRecommendation.model_validate(stored_payload)
                if recommendation.source_workspace_id != workspace_id:
                    raise KevinSpeakError("restored recommendation crosses workspaces")
                if recommendation.source_configuration_ref != configuration.digest:
                    raise KevinSpeakError("restored recommendation changed configuration")
                if (
                    not envelope.parent_refs
                    or recommendation.source_evidence_boundary_ref != envelope.parent_refs[0]
                ):
                    raise KevinSpeakError(
                        "restored recommendation changed its source evidence boundary"
                    )
                registry.require(recommendation.recommended_codebook_ref)
                if recommendation.recommended_definition_refs != (
                    registry.effective_definition_refs(recommendation.recommended_codebook_ref)
                ):
                    raise KevinSpeakError("restored recommendation changed definitions")
                if any(
                    evaluation_ref not in evaluations
                    for evaluation_ref in recommendation.evaluation_refs
                ):
                    raise KevinSpeakError("restored recommendation lacks evaluation evidence")
                recommendations[recommendation.digest] = recommendation
            elif envelope.kind == "kevin_next_round_review":
                review = KevinRecommendationReview.model_validate(stored_payload)
                if review.recommendation_ref not in recommendations:
                    raise KevinSpeakError("restored review lacks its recommendation")
                if review.review_configuration_ref != configuration.digest:
                    raise KevinSpeakError("restored review changed configuration")
                registry.require(review.reviewed_codebook_ref)
                if any(value not in evaluations for value in review.evaluation_refs):
                    raise KevinSpeakError("restored review lacks evaluation evidence")
                reviews[review.digest] = review
            elif envelope.kind == "kevin_next_round_adoption":
                adoption = KevinAdoptionDecision.model_validate(stored_payload)
                if adoption.recommendation_ref not in recommendations:
                    raise KevinSpeakError("restored adoption lacks its recommendation")
                if adoption.review_ref is not None and adoption.review_ref not in reviews:
                    raise KevinSpeakError("restored adoption lacks its review")
                registry.require(adoption.candidate_codebook_ref)
                target_configuration = KevinSpeakConfiguration.model_validate(
                    ledger.get_payload(adoption.target_configuration_ref)
                )
                if target_configuration.digest != adoption.target_configuration_ref:
                    raise KevinSpeakError("restored adoption changed target configuration")
                adoptions[adoption.digest] = adoption
            elif envelope.kind == "kevin_transfer_export":
                transfer = KevinSpeakTransfer.model_validate(stored_payload)
                if transfer.adoption.digest not in adoptions:
                    raise KevinSpeakError("restored transfer lacks its adoption decision")
            else:
                raise KevinSpeakError("workspace contains an unknown shorthand receipt kind")
        workspace = cls(
            ledger=ledger,
            workspace_id=workspace_id,
            account_id=expected_account,
            account_version=account_version,
            registry=registry,
            configuration=configuration,
            active_codebook_ref=active_ref,
            receipt_refs=[item[0].receipt_id for item in selected],
            evaluations=evaluations,
            entries=entries,
            recommendations=recommendations,
            recommendation_bundles=recommendation_bundles,
            reviews=reviews,
            adoptions=adoptions,
        )
        workspace.verify()
        return workspace


def kevin_speak_schema_bundle() -> dict[str, Any]:
    """Return the declarative model, review, adoption, and transfer schemas."""

    return {
        "contract_version": KEVIN_SPEAK_CONTRACT_SCHEMA,
        "schemas": {
            "adoption_decision": KevinAdoptionDecision.model_json_schema(),
            "codebook_evaluation": KevinCodebookEvaluation.model_json_schema(),
            "codebook_revision": KevinCodebookRevision.model_json_schema(),
            "configuration": KevinSpeakConfiguration.model_json_schema(),
            "entry": KevinSpeakEntry.model_json_schema(),
            "next_round_recommendation": KevinNextRoundRecommendation.model_json_schema(),
            "recommendation_bundle": KevinRecommendationBundle.model_json_schema(),
            "recommendation_review": KevinRecommendationReview.model_json_schema(),
            "symbol_proposal": KevinSymbolProposal.model_json_schema(),
            "transfer": KevinSpeakTransfer.model_json_schema(),
            "translation_table": KevinTranslationTable.model_json_schema(),
        },
    }
