from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from strongwiz.canonical import (
    CanonicalizationError,
    canonical_text,
    content_hash,
    parse_strict_json,
)
from strongwiz.contracts import ActionSpec, CostVector, DecisionEffect, Distinction, Goal
from tests.support import governing_goal, proposal, request


def test_canonical_json_is_order_independent_and_unicode_preserving() -> None:
    left = {"z": "Δ", "a": [1, True, None]}
    right = {"a": [1, True, None], "z": "Δ"}
    assert canonical_text(left) == '{"a":[1,true,null],"z":"Δ"}'
    assert content_hash(left) == content_hash(right)


def test_canonical_json_rejects_floats_and_duplicate_keys() -> None:
    with pytest.raises(CanonicalizationError, match="floating-point"):
        canonical_text({"score": 0.5})
    with pytest.raises(CanonicalizationError, match="duplicate"):
        parse_strict_json('{"a":1,"a":2}')
    with pytest.raises(CanonicalizationError, match="non-finite"):
        parse_strict_json('{"a":NaN}')


def test_contract_serializes_stable_schema_alias() -> None:
    dumped = request().model_dump(mode="json", by_alias=True)
    assert dumped["schema"] == "strongwiz.contract.v1"
    assert "schema_id" not in dumped


def test_subgoal_requires_complete_relevance_chain() -> None:
    with pytest.raises(ValidationError, match="complete relevance"):
        Goal(
            goal_id="child",
            statement="test something",
            scope_id="scope",
            parent_goal_id="root",
            success_condition="done",
        )
    assert governing_goal().parent_goal_id is None


def test_distinction_requires_competing_predictions_and_decision_effect() -> None:
    with pytest.raises(ValidationError, match="at least two"):
        Distinction(
            distinction_id="d",
            statement="one-sided",
            scope_id="s",
            parent_goal_id="p",
            governing_goal_id="g",
            candidate_resolutions=("only",),
            competing_predictions=("only",),
            decision_effects=(DecisionEffect.PLAN,),
            decision_that_could_change="plan",
            relevance_summary="matters",
            reopening_condition="later",
        )


def test_model_proposal_cannot_smuggle_control_fields() -> None:
    payload = proposal().model_dump(mode="json", by_alias=True)
    payload["execution_grant_ref"] = "forged"
    with pytest.raises(ValidationError, match="extra"):
        type(proposal()).model_validate(payload)


def test_cost_vectors_are_componentwise_not_scalarized() -> None:
    cost = CostVector(environment_actions=2, compute_units=5)
    budget = CostVector(environment_actions=3, compute_units=4)
    assert not cost.fits_within(budget)
    assert (cost + CostVector(compute_units=2)).compute_units == 7
    assert cost.subtract_floor_zero(CostVector(environment_actions=9)).environment_actions == 0


def test_contract_json_is_deeply_immutable_after_hashing() -> None:
    action = ActionSpec(
        name="inspect",
        parameters={"target": {"cells": [1, 2, {"label": "door"}]}},
    )
    digest = action.digest
    with pytest.raises(TypeError):
        action.parameters["new"] = True
    target = action.parameters["target"]
    assert isinstance(target, Mapping)
    cells = target["cells"]
    assert isinstance(cells, tuple)
    nested = cells[2]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["label"] = "window"
    copied = action.model_copy(update={"parameters": {"items": [1, 2]}})
    with pytest.raises(TypeError):
        copied.parameters["items"] = []
    assert action.digest == digest
    assert action.model_dump(mode="json")["parameters"] == {
        "target": {"cells": [1, 2, {"label": "door"}]}
    }
