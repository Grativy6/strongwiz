from __future__ import annotations

import pytest
from pydantic import ValidationError

from strongwiz.contracts import CostVector
from strongwiz.facts import (
    DerivedFact,
    FactError,
    FactStatus,
    FactStore,
    FactUseKind,
    TransferRule,
)
from tests.support import ref


def earned_fact(
    *,
    subject: str = "object-a",
    version: int = 0,
    status: FactStatus = FactStatus.EXACT,
    value: object = "opens",
    rules: tuple[str, ...] = ("same-pattern",),
) -> DerivedFact:
    return DerivedFact(
        subject_ref=subject,
        subject_version=version,
        scope_id="scope",
        predicate="interaction.effect",
        value=value,
        status=status,
        producer_ref="learner-v1",
        evidence_refs=(ref(f"evidence-{subject}-{version}"),),
        validity_tags=("same-shape",),
        transfer_rule_ids=rules,
        invalidated_by_events=("shape-change", "rule-change"),
        acquisition_cost=CostVector(acquisition_units=4, compute_units=8),
    )


def test_fact_requires_earned_evidence_and_lower_bound_resume_state() -> None:
    with pytest.raises(ValidationError, match="earned"):
        DerivedFact(
            subject_ref="s",
            subject_version=0,
            scope_id="scope",
            predicate="p",
            value=True,
            status=FactStatus.EXACT,
            producer_ref="producer",
            evidence_refs=(),
        )
    with pytest.raises(ValidationError, match="residual"):
        DerivedFact(
            subject_ref="s",
            subject_version=0,
            scope_id="scope",
            predicate="p",
            value=1,
            status=FactStatus.LOWER_BOUND,
            producer_ref="producer",
            evidence_refs=(ref("e"),),
        )
    partial = DerivedFact(
        subject_ref="s",
        subject_version=0,
        scope_id="scope",
        predicate="p",
        value=1,
        status=FactStatus.LOWER_BOUND,
        producer_ref="producer",
        evidence_refs=(ref("e"),),
        continuation_token={"resume_at": 4},
    )
    assert partial.continuation_token == {"resume_at": 4}


def test_exact_lookup_negative_cache_and_version_invalidation() -> None:
    store = FactStore()
    fact = earned_fact(status=FactStatus.EXACT_NEGATIVE, value=False)
    fact_ref = store.issue(fact)
    found, use = store.lookup(
        query_ref=ref("query"),
        subject_ref="object-a",
        subject_version=0,
        predicate="interaction.effect",
        avoided_cost=CostVector(compute_units=20),
    )
    assert found == fact
    assert use.kind is FactUseKind.HIT
    assert use.fact_ref == fact_ref
    invalidation = store.mutate_subject(
        subject_ref="object-a",
        new_version=1,
        event_kind="shape-change",
        reason="observed material change",
        evidence_refs=(ref("change"),),
    )
    assert invalidation.invalidated_fact_refs == (fact_ref,)
    stale, stale_use = store.lookup(
        query_ref=ref("query-2"),
        subject_ref="object-a",
        subject_version=0,
        predicate="interaction.effect",
    )
    assert stale is None
    assert stale_use.kind is FactUseKind.INVALID
    with pytest.raises(FactError, match="no longer active"):
        store.require(fact_ref)
    assert store.economics.hits == 1
    assert store.economics.misses == 1


def test_transfer_requires_declared_rule_status_and_validation() -> None:
    store = FactStore()
    rule = TransferRule(
        rule_id="same-pattern",
        operation="pattern-copy",
        allowed_statuses=(FactStatus.EXACT,),
        declared_scope="identical observable pattern and action semantics",
    )
    store.register_transfer_rule(rule)
    fact_ref = store.issue(earned_fact())
    with pytest.raises(FactError, match="validation"):
        store.transfer(
            fact_ref=fact_ref,
            rule_id="same-pattern",
            operation="pattern-copy",
            target_subject_ref="object-b",
            target_version=0,
            target_scope_id="scope-2",
            validation_refs=(),
        )
    transferred = store.transfer(
        fact_ref=fact_ref,
        rule_id="same-pattern",
        operation="pattern-copy",
        target_subject_ref="object-b",
        target_version=0,
        target_scope_id="scope-2",
        validation_refs=(ref("pattern-match"),),
    )
    assert transferred.parent_fact_ref == fact_ref
    assert transferred.subject_ref == "object-b"
    assert store.economics.transfers == 1


def test_prime_like_negative_transport_must_not_be_generalized_to_composites() -> None:
    store = FactStore()
    prime_rule = TransferRule(
        rule_id="prime-nondivisibility-product",
        operation="multiply",
        allowed_statuses=(FactStatus.EXACT_NEGATIVE,),
        preserves_negative=True,
        declared_scope="same prime divisor and exact parent residuals",
    )
    store.register_transfer_rule(prime_rule)
    prime_fact = earned_fact(
        status=FactStatus.EXACT_NEGATIVE,
        value=False,
        rules=("prime-nondivisibility-product",),
    )
    prime_ref = store.issue(prime_fact)
    assert (
        store.transfer(
            fact_ref=prime_ref,
            rule_id=prime_rule.rule_id,
            operation="multiply",
            target_subject_ref="product",
            target_version=0,
            target_scope_id="scope",
            validation_refs=(ref("prime-witness"),),
        ).status
        is FactStatus.EXACT_NEGATIVE
    )
    with pytest.raises(ValidationError, match="negative transfer"):
        TransferRule(
            rule_id="composite-six",
            operation="multiply",
            allowed_statuses=(FactStatus.EXACT_NEGATIVE,),
            preserves_negative=False,
            declared_scope="invalid counterexample: 6 divides 2*3",
        )


def test_stale_fact_cannot_be_reissued_or_transferred() -> None:
    store = FactStore()
    store.register_transfer_rule(
        TransferRule(
            rule_id="same-pattern",
            operation="pattern-copy",
            allowed_statuses=(FactStatus.EXACT,),
            declared_scope="exact match",
        )
    )
    fact_ref = store.issue(earned_fact())
    store.mutate_subject(
        subject_ref="object-a",
        new_version=2,
        event_kind="rule-change",
        reason="operator changed",
        evidence_refs=(ref("change"),),
    )
    with pytest.raises(FactError, match="stale"):
        store.issue(earned_fact(version=1))
    with pytest.raises(FactError, match="invalidated"):
        store.transfer(
            fact_ref=fact_ref,
            rule_id="same-pattern",
            operation="pattern-copy",
            target_subject_ref="b",
            target_version=0,
            target_scope_id="scope",
            validation_refs=(ref("v"),),
        )


def test_dependency_invalidation_cascades_and_blocks_new_derived_facts() -> None:
    store = FactStore()
    base_ref = store.issue(earned_fact(subject="base"))
    dependent = DerivedFact(
        subject_ref="dependent",
        subject_version=0,
        scope_id="scope",
        predicate="derived.effect",
        value="result",
        status=FactStatus.EXACT,
        producer_ref="composer-v1",
        evidence_refs=(ref("dependent-evidence"),),
        dependency_fact_refs=(base_ref,),
    )
    dependent_ref = store.issue(dependent)
    invalidation = store.mutate_subject(
        subject_ref="base",
        new_version=1,
        event_kind="rule-change",
        reason="base mechanism changed",
        evidence_refs=(ref("base-change"),),
    )
    assert set(invalidation.invalidated_fact_refs) == {base_ref, dependent_ref}
    found, use = store.lookup(
        query_ref=ref("dependent-query"),
        subject_ref="dependent",
        subject_version=0,
        predicate="derived.effect",
    )
    assert found is None
    assert use.kind is FactUseKind.INVALID

    with pytest.raises(FactError, match="dependencies"):
        store.issue(
            DerivedFact.model_validate(
                {
                    **dependent.model_dump(mode="python"),
                    "subject_ref": "new-dependent",
                    "evidence_refs": (ref("new-dependent-evidence"),),
                }
            )
        )
