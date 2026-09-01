"""Earned derived facts, exact reuse, transfer, invalidation, and economics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from strongwiz.canonical import ImmutableJSONValue
from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt


class FactError(ValueError):
    pass


class FactStatus(StrEnum):
    LOWER_BOUND = "lower_bound"
    EXACT = "exact"
    EXACT_NEGATIVE = "exact_negative"


class FactUseKind(StrEnum):
    HIT = "hit"
    MISS = "miss"
    INVALID = "invalid"
    TRANSFER = "transfer"
    RECOMPUTE = "recompute"


class DerivedFact(ContractModel):
    schema_id: str = Field(default="strongwiz.derived-fact.v1", alias="schema")
    subject_ref: str
    subject_version: NonNegativeInt
    scope_id: str
    predicate: str
    value: ImmutableJSONValue
    status: FactStatus
    producer_ref: str
    evidence_refs: tuple[str, ...]
    dependency_fact_refs: tuple[str, ...] = ()
    parent_fact_ref: str | None = None
    validity_tags: tuple[str, ...] = ()
    transfer_rule_ids: tuple[str, ...] = ()
    invalidated_by_events: tuple[str, ...] = ()
    residual: ImmutableJSONValue = None
    continuation_token: ImmutableJSONValue = None
    acquisition_cost: CostVector = Field(default_factory=CostVector)

    @model_validator(mode="after")
    def validate_fact(self) -> DerivedFact:
        if self.schema_id != "strongwiz.derived-fact.v1":
            raise ValueError("unsupported derived-fact schema")
        required = (self.subject_ref, self.scope_id, self.predicate, self.producer_ref)
        if not all(value.strip() for value in required):
            raise ValueError("fact subject, scope, predicate, and producer are required")
        if not self.evidence_refs:
            raise ValueError("a derived fact must be earned from evidence")
        if self.status is FactStatus.LOWER_BOUND:
            if self.residual is None and self.continuation_token is None:
                raise ValueError("lower-bound facts must retain residual or continuation state")
        elif self.continuation_token is not None:
            raise ValueError("terminal facts cannot retain a continuation token")
        if self.status is FactStatus.EXACT_NEGATIVE and self.value not in (False, None):
            raise ValueError("exact-negative facts must carry false or null value")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("fact evidence references must be unique")
        return self

    @property
    def fact_ref(self) -> str:
        return self.digest


class TransferRule(ContractModel):
    schema_id: str = Field(default="strongwiz.fact-transfer-rule.v1", alias="schema")
    rule_id: str
    operation: str
    allowed_statuses: tuple[FactStatus, ...]
    preserves_predicate: bool = True
    preserves_negative: bool = False
    requires_validation: bool = True
    declared_scope: str

    @model_validator(mode="after")
    def validate_rule(self) -> TransferRule:
        if not all((self.rule_id, self.operation, self.declared_scope)):
            raise ValueError("transfer rule identity, operation, and scope are required")
        if not self.allowed_statuses:
            raise ValueError("transfer rule must name allowed fact states")
        if FactStatus.EXACT_NEGATIVE in self.allowed_statuses and not self.preserves_negative:
            raise ValueError("negative transfer requires an explicit preservation claim")
        return self


class FactInvalidation(ContractModel):
    schema_id: str = Field(default="strongwiz.fact-invalidation.v1", alias="schema")
    subject_ref: str
    old_version: NonNegativeInt
    new_version: NonNegativeInt
    event_kind: str
    reason: str
    evidence_refs: tuple[str, ...]
    invalidated_fact_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_invalidation(self) -> FactInvalidation:
        if self.new_version <= self.old_version:
            raise ValueError("subject mutation must advance the version")
        if not self.event_kind or not self.reason or not self.evidence_refs:
            raise ValueError("invalidation requires event, reason, and evidence")
        return self


class FactUseReceipt(ContractModel):
    schema_id: str = Field(default="strongwiz.fact-use.v1", alias="schema")
    query_ref: str
    kind: FactUseKind
    subject_ref: str
    subject_version: NonNegativeInt
    predicate: str
    fact_ref: str | None
    reason: str
    charged_cost: CostVector
    avoided_cost: CostVector = Field(default_factory=CostVector)


class FactEconomics(ContractModel):
    acquisitions: NonNegativeInt = 0
    lookups: NonNegativeInt = 0
    hits: NonNegativeInt = 0
    misses: NonNegativeInt = 0
    validations: NonNegativeInt = 0
    transfers: NonNegativeInt = 0
    invalidations: NonNegativeInt = 0
    recomputations: NonNegativeInt = 0
    charged: CostVector = Field(default_factory=CostVector)
    avoided: CostVector = Field(default_factory=CostVector)


class FactStore:
    """Version-bound cache for already-earned facts; never a speculative scout."""

    def __init__(self) -> None:
        self._facts: dict[str, DerivedFact] = {}
        self._active: set[str] = set()
        self._by_key: dict[tuple[str, int, str], list[str]] = {}
        self._versions: dict[str, int] = {}
        self._rules: dict[str, TransferRule] = {}
        self._uses: list[FactUseReceipt] = []
        self._economics = FactEconomics()

    def register_transfer_rule(self, rule: TransferRule) -> None:
        current = self._rules.get(rule.rule_id)
        if current is not None and current != rule:
            raise FactError("transfer rule identity cannot be rewritten")
        self._rules[rule.rule_id] = rule

    def issue(self, fact: DerivedFact) -> str:
        current_version = self._versions.get(fact.subject_ref)
        if current_version is not None and fact.subject_version < current_version:
            raise FactError("cannot issue a fact for a stale subject version")
        unavailable_dependencies = tuple(
            ref for ref in fact.dependency_fact_refs if ref not in self._active
        )
        if unavailable_dependencies:
            raise FactError("cannot issue a fact from missing or invalidated dependencies")
        fact_ref = fact.fact_ref
        current = self._facts.get(fact_ref)
        if current is not None and current != fact:
            raise FactError("fact content identity collision")
        self._facts[fact_ref] = fact
        self._active.add(fact_ref)
        key = (fact.subject_ref, fact.subject_version, fact.predicate)
        refs = self._by_key.setdefault(key, [])
        if fact_ref not in refs:
            refs.append(fact_ref)
        self._versions[fact.subject_ref] = max(
            fact.subject_version, self._versions.get(fact.subject_ref, 0)
        )
        self._economics = self._economics.model_copy(
            update={
                "acquisitions": self._economics.acquisitions + 1,
                "charged": self._economics.charged + fact.acquisition_cost,
            }
        )
        return fact_ref

    def lookup(
        self,
        *,
        query_ref: str,
        subject_ref: str,
        subject_version: int,
        predicate: str,
        lookup_cost: CostVector | None = None,
        avoided_cost: CostVector | None = None,
    ) -> tuple[DerivedFact | None, FactUseReceipt]:
        charged = lookup_cost or CostVector(validation_units=1)
        avoided = avoided_cost or CostVector()
        refs = self._by_key.get((subject_ref, subject_version, predicate), [])
        candidates = [self._facts[ref] for ref in refs if ref in self._active]
        fact = candidates[-1] if candidates else None
        if fact is None:
            current_version = self._versions.get(subject_ref)
            stale = current_version is not None and current_version != subject_version
            invalidated = bool(refs)
            kind = FactUseKind.INVALID if stale or invalidated else FactUseKind.MISS
            if stale:
                reason = "subject version is stale"
            elif invalidated:
                reason = "the exact earned fact was invalidated"
            else:
                reason = "no exact earned fact"
        else:
            kind = FactUseKind.HIT
            reason = "exact subject, version, and predicate match"
        receipt = FactUseReceipt(
            query_ref=query_ref,
            kind=kind,
            subject_ref=subject_ref,
            subject_version=subject_version,
            predicate=predicate,
            fact_ref=None if fact is None else fact.fact_ref,
            reason=reason,
            charged_cost=charged,
            avoided_cost=avoided if fact is not None else CostVector(),
        )
        self._uses.append(receipt)
        self._economics = self._economics.model_copy(
            update={
                "lookups": self._economics.lookups + 1,
                "hits": self._economics.hits + (1 if fact is not None else 0),
                "misses": self._economics.misses + (1 if fact is None else 0),
                "charged": self._economics.charged + charged,
                "avoided": self._economics.avoided
                + (avoided if fact is not None else CostVector()),
            }
        )
        return fact, receipt

    def mutate_subject(
        self,
        *,
        subject_ref: str,
        new_version: int,
        event_kind: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        invalidation_cost: CostVector | None = None,
    ) -> FactInvalidation:
        old_version = self._versions.get(subject_ref, 0)
        directly_implicated = {
            ref
            for (candidate_subject, _version, _predicate), refs in self._by_key.items()
            if candidate_subject == subject_ref
            for ref in refs
            if ref in self._active
        }
        candidates = set(directly_implicated)
        changed = True
        while changed:
            changed = False
            for fact_ref in tuple(self._active - candidates):
                if set(self._facts[fact_ref].dependency_fact_refs) & candidates:
                    candidates.add(fact_ref)
                    changed = True
        receipt = FactInvalidation(
            subject_ref=subject_ref,
            old_version=old_version,
            new_version=new_version,
            event_kind=event_kind,
            reason=reason,
            evidence_refs=evidence_refs,
            invalidated_fact_refs=tuple(sorted(candidates)),
        )
        self._active.difference_update(candidates)
        self._versions[subject_ref] = new_version
        charged = invalidation_cost or CostVector(invalidation_units=max(1, len(candidates)))
        self._economics = self._economics.model_copy(
            update={
                "invalidations": self._economics.invalidations + 1,
                "charged": self._economics.charged + charged,
            }
        )
        return receipt

    def transfer(
        self,
        *,
        fact_ref: str,
        rule_id: str,
        operation: str,
        target_subject_ref: str,
        target_version: int,
        target_scope_id: str,
        validation_refs: tuple[str, ...],
        transfer_cost: CostVector | None = None,
    ) -> DerivedFact:
        try:
            fact = self._facts[fact_ref]
            rule = self._rules[rule_id]
        except KeyError as error:
            raise FactError("unknown fact or transfer rule") from error
        if fact_ref not in self._active:
            raise FactError("invalidated facts cannot transfer")
        if operation != rule.operation or rule_id not in fact.transfer_rule_ids:
            raise FactError("fact does not declare this legal transfer")
        if fact.status not in rule.allowed_statuses:
            raise FactError("fact status is not legal for this transfer")
        if fact.status is FactStatus.EXACT_NEGATIVE and not rule.preserves_negative:
            raise FactError("negative fact does not transport under this rule")
        if rule.requires_validation and not validation_refs:
            raise FactError("transfer requires control-owned validation evidence")
        charged = transfer_cost or CostVector(validation_units=1, transport_units=1)
        transferred = DerivedFact(
            subject_ref=target_subject_ref,
            subject_version=target_version,
            scope_id=target_scope_id,
            predicate=fact.predicate,
            value=fact.value,
            status=fact.status,
            producer_ref=rule.rule_id,
            evidence_refs=tuple(sorted(set((*fact.evidence_refs, *validation_refs)))),
            dependency_fact_refs=tuple(sorted(set((*fact.dependency_fact_refs, fact_ref)))),
            parent_fact_ref=fact_ref,
            validity_tags=fact.validity_tags,
            transfer_rule_ids=fact.transfer_rule_ids,
            invalidated_by_events=fact.invalidated_by_events,
            residual=fact.residual,
            continuation_token=fact.continuation_token,
            acquisition_cost=charged,
        )
        self.issue(transferred)
        self._economics = self._economics.model_copy(
            update={
                "transfers": self._economics.transfers + 1,
                "validations": self._economics.validations
                + (1 if rule.requires_validation else 0),
            }
        )
        return transferred

    def record_recomputation(self, cost: CostVector) -> None:
        self._economics = self._economics.model_copy(
            update={
                "recomputations": self._economics.recomputations + 1,
                "charged": self._economics.charged + cost,
            }
        )

    def require(self, fact_ref: str, *, active: bool = True) -> DerivedFact:
        try:
            fact = self._facts[fact_ref]
        except KeyError as error:
            raise FactError("unknown fact reference") from error
        if active and fact_ref not in self._active:
            raise FactError("fact is no longer active")
        return fact

    @property
    def economics(self) -> FactEconomics:
        return self._economics

    @property
    def use_receipts(self) -> tuple[FactUseReceipt, ...]:
        return tuple(self._uses)

    @property
    def active_fact_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))
