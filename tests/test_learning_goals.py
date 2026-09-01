from __future__ import annotations

import pytest

from strongwiz.contracts import GoalStatus, HypothesisStatus
from strongwiz.goals import GoalError, GoalGraph, ScopeTransition
from strongwiz.learning import (
    ChannelValue,
    ConsequenceSchema,
    ConsequenceVector,
    KnowledgeState,
    LearningError,
    MechanicLedger,
    MechanicVersion,
    RepairScope,
    ResidualKind,
    choose_repair_scope,
    compare_consequences,
)
from tests.support import governing_goal, ref, scoped_goal


def schema() -> ConsequenceSchema:
    return ConsequenceSchema(
        schema_id="synthetic-door.v1",
        channel_names=("access", "movement", "resource"),
    )


def vector(
    *,
    access: str | None,
    movement: str | None,
    resource: str | None,
    unknown_resource: bool = False,
) -> ConsequenceVector:
    declared = schema()
    return ConsequenceVector.build(
        declared,
        {
            "access": ChannelValue.known("access", *(() if access is None else (access,))),
            "movement": ChannelValue.known(
                "movement", *(() if movement is None else (movement,))
            ),
            "resource": ChannelValue.unknown("resource")
            if unknown_resource
            else ChannelValue.known("resource", *(() if resource is None else (resource,))),
        },
    )


def mechanic() -> MechanicVersion:
    return MechanicVersion(
        mechanic_id="latch",
        version=0,
        scope_id="scope-1",
        action_pattern="inspect",
        condition_tags=("door-present",),
        consequence=vector(access="opens", movement=None, resource=None),
        component_refs_by_channel={
            "access": ("component-access",),
            "movement": ("component-movement",),
            "resource": ("component-resource",),
        },
        status=HypothesisStatus.CANDIDATE,
    )


def test_known_empty_is_distinct_from_unknown() -> None:
    empty = ChannelValue.known("resource")
    unknown = ChannelValue.unknown("resource")
    assert empty.knowledge is KnowledgeState.KNOWN
    assert not empty.atoms
    assert unknown.knowledge is KnowledgeState.UNKNOWN
    with pytest.raises(ValueError, match="unknown channels"):
        ChannelValue(channel="x", knowledge=KnowledgeState.UNKNOWN, atoms=("effect",))


def test_residual_is_localized_and_unaffected_components_are_preserved() -> None:
    expected = mechanic().consequence
    observed = vector(access="stays-closed", movement=None, resource=None)
    assessment = compare_consequences(
        expected,
        observed,
        evidence_refs=(ref("outcome"),),
        component_refs_by_channel=mechanic().component_refs_by_channel,
    )
    assert not assessment.exact_match
    assert len(assessment.residuals) == 1
    assert assessment.residuals[0].channel == "access"
    assert assessment.residuals[0].kind is ResidualKind.MISMATCH
    assert assessment.implicated_component_refs == ("component-access",)
    assert set(assessment.preserved_component_refs) == {
        "component-movement",
        "component-resource",
    }


def test_unknown_prediction_channel_does_not_become_false_known_empty() -> None:
    expected = vector(access="opens", movement=None, resource=None, unknown_resource=True)
    observed = vector(access="opens", movement=None, resource="spent")
    assessment = compare_consequences(
        expected,
        observed,
        evidence_refs=(ref("outcome"),),
        component_refs_by_channel={
            "access": ("a",),
            "movement": ("m",),
            "resource": ("r",),
        },
    )
    assert not assessment.exact_match
    assert "resource" in assessment.unscored_channels
    assert "resource" not in assessment.matched_channels
    assert "r" not in assessment.preserved_component_refs


def test_mechanic_ledger_revises_exactly_implicated_channel() -> None:
    ledger = MechanicLedger()
    original = mechanic()
    ledger.register(original)
    assessment = ledger.assess(
        "latch",
        vector(access="stays-closed", movement=None, resource=None),
        evidence_refs=(ref("outcome"),),
    )
    with pytest.raises(LearningError, match="exactly"):
        ledger.revise_implicated(
            "latch",
            assessment,
            replacements={
                "access": ChannelValue.known("access", "stays-closed"),
                "resource": ChannelValue.known("resource", "spent"),
            },
            evidence_refs=(ref("outcome-2"),),
            reason="overbroad rewrite",
        )
    revised = ledger.revise_implicated(
        "latch",
        assessment,
        replacements={"access": ChannelValue.known("access", "stays-closed")},
        evidence_refs=(ref("outcome"),),
        reason="access prediction alone failed",
    )
    assert revised.version == 1
    assert revised.parent_version_ref == original.version_ref
    assert revised.consequence.get("movement") == original.consequence.get("movement")
    with pytest.raises(LearningError, match="current mechanic"):
        ledger.revise_implicated(
            "latch",
            assessment,
            replacements={"access": ChannelValue.known("access", "stays-closed")},
            evidence_refs=(ref("outcome-2"),),
            reason="overbroad rewrite",
        )


def test_matching_mechanic_gets_support_version() -> None:
    ledger = MechanicLedger()
    original = mechanic()
    ledger.register(original)
    assessment = ledger.assess(
        "latch", original.consequence, evidence_refs=(ref("confirmation"),)
    )
    supported = ledger.support(
        "latch",
        assessment,
        evidence_refs=(ref("confirmation"),),
        reason="prediction matched",
    )
    assert supported.status is HypothesisStatus.SUPPORTED
    assert supported.version == 1
    assert supported.support_refs == (ref("confirmation"),)


def test_repair_widens_only_after_repeated_local_failure() -> None:
    assert choose_repair_scope(0) is RepairScope.LOCAL_COMPONENT
    assert choose_repair_scope(1) is RepairScope.LOCAL_COMPONENT
    assert choose_repair_scope(2) is RepairScope.DEPENDENCY
    assert choose_repair_scope(3) is RepairScope.SCOPED_MODEL


def test_goal_graph_parks_stage_attention_and_reopens_with_history() -> None:
    graph = GoalGraph(governing_goal())
    local = scoped_goal()
    graph.add_subgoal(local)
    transition = ScopeTransition(
        old_scope_id="scope-1",
        old_epoch=0,
        new_scope_id="scope-2",
        new_epoch=1,
        observation_ref=ref("new-surface"),
        retained_fact_refs=(ref("portable-mechanic"),),
        reopening_condition="later evidence makes the old route consequential",
    )
    graph.new_surface(transition, park_goal_ids=(local.goal_id,))
    assert graph.current(local.goal_id).status is GoalStatus.PARKED
    reopened = graph.transition(
        local.goal_id,
        GoalStatus.REOPENED,
        reason="new challenge depends on the parked latch",
        evidence_refs=(ref("contradiction"),),
    )
    assert reopened.prior_status is GoalStatus.PARKED
    assert graph.current(local.goal_id).status is GoalStatus.REOPENED
    assert len(graph.transitions) == 2
    assert graph.scope_transitions == (transition,)


def test_goal_graph_rejects_unknown_parent_and_evidence_free_transition() -> None:
    graph = GoalGraph(governing_goal())
    bad = scoped_goal().model_copy(update={"parent_goal_id": "absent"})
    with pytest.raises(GoalError, match="parent"):
        graph.add_subgoal(bad)
    with pytest.raises(GoalError, match="reason and evidence"):
        graph.transition(
            governing_goal().goal_id,
            GoalStatus.PARKED,
            reason="",
            evidence_refs=(),
        )
