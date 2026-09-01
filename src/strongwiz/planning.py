"""Deterministic bounded graph search behind a domain-neutral interface."""

from __future__ import annotations

import heapq
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import count, islice
from typing import Protocol


class SearchDisposition(StrEnum):
    FOUND = "found"
    EXHAUSTED = "exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class SearchEdge[StateT, ActionT]:
    action: ActionT
    state: StateT
    cost: int = 1

    def __post_init__(self) -> None:
        if self.cost <= 0:
            raise ValueError("search edge cost must be positive")


class SearchProblem[StateT, ActionT](Protocol):
    def state_key(self, state: StateT) -> str: ...

    def is_goal(self, state: StateT) -> bool: ...

    def neighbors(self, state: StateT) -> Iterable[SearchEdge[StateT, ActionT]]: ...

    def heuristic(self, state: StateT) -> int: ...

    def action_key(self, action: ActionT) -> str: ...


@dataclass(frozen=True, slots=True)
class SearchResult[StateT, ActionT]:
    disposition: SearchDisposition
    states: tuple[StateT, ...]
    actions: tuple[ActionT, ...]
    total_cost: int | None
    expanded: int
    frontier_size: int
    reason: str


def bounded_astar[StateT, ActionT](
    problem: SearchProblem[StateT, ActionT],
    start: StateT,
    *,
    max_expansions: int,
    max_neighbors_per_expansion: int = 10_000,
) -> SearchResult[StateT, ActionT]:
    """Run deterministic A* with an explicit expansion ceiling."""

    if max_expansions <= 0 or max_neighbors_per_expansion <= 0:
        raise ValueError("search expansion and neighbor budgets must be positive")
    start_key = problem.state_key(start)
    start_h = problem.heuristic(start)
    if start_h < 0:
        raise ValueError("search heuristic must be nonnegative")
    serial = count()
    frontier: list[tuple[int, int, str, int, StateT]] = [
        (start_h, 0, start_key, next(serial), start)
    ]
    best_cost: dict[str, int] = {start_key: 0}
    state_by_key: dict[str, StateT] = {start_key: start}
    parent: dict[str, tuple[str, ActionT]] = {}
    expanded = 0

    while frontier:
        _estimated, cost_so_far, state_key, _serial, state = heapq.heappop(frontier)
        if cost_so_far != best_cost.get(state_key):
            continue
        if problem.is_goal(state):
            states, actions = _reconstruct(state_key, state_by_key, parent)
            return SearchResult(
                disposition=SearchDisposition.FOUND,
                states=states,
                actions=actions,
                total_cost=cost_so_far,
                expanded=expanded,
                frontier_size=len(frontier),
                reason="goal predicate reached",
            )
        if expanded >= max_expansions:
            return SearchResult(
                disposition=SearchDisposition.BUDGET_EXHAUSTED,
                states=(),
                actions=(),
                total_cost=None,
                expanded=expanded,
                frontier_size=len(frontier) + 1,
                reason="expansion ceiling reached before a goal",
            )
        expanded += 1
        bounded_edges = list(islice(problem.neighbors(state), max_neighbors_per_expansion + 1))
        if len(bounded_edges) > max_neighbors_per_expansion:
            return SearchResult(
                disposition=SearchDisposition.BUDGET_EXHAUSTED,
                states=(),
                actions=(),
                total_cost=None,
                expanded=expanded,
                frontier_size=len(frontier),
                reason="neighbor ceiling reached during one expansion",
            )
        ordered_edges = sorted(
            bounded_edges,
            key=lambda edge: (
                problem.state_key(edge.state),
                problem.action_key(edge.action),
                edge.cost,
            ),
        )
        for edge in ordered_edges:
            child_key = problem.state_key(edge.state)
            child_cost = cost_so_far + edge.cost
            if child_cost >= best_cost.get(child_key, child_cost + 1):
                continue
            heuristic = problem.heuristic(edge.state)
            if heuristic < 0:
                raise ValueError("search heuristic must be nonnegative")
            best_cost[child_key] = child_cost
            state_by_key[child_key] = edge.state
            parent[child_key] = (state_key, edge.action)
            heapq.heappush(
                frontier,
                (
                    child_cost + heuristic,
                    child_cost,
                    child_key,
                    next(serial),
                    edge.state,
                ),
            )

    return SearchResult(
        disposition=SearchDisposition.EXHAUSTED,
        states=(),
        actions=(),
        total_cost=None,
        expanded=expanded,
        frontier_size=0,
        reason="reachable state graph exhausted without a goal",
    )


def _reconstruct[StateT, ActionT](
    goal_key: str,
    state_by_key: dict[str, StateT],
    parent: dict[str, tuple[str, ActionT]],
) -> tuple[tuple[StateT, ...], tuple[ActionT, ...]]:
    keys = [goal_key]
    actions: list[ActionT] = []
    current = goal_key
    while current in parent:
        previous, action = parent[current]
        actions.append(action)
        keys.append(previous)
        current = previous
    keys.reverse()
    actions.reverse()
    return tuple(state_by_key[key] for key in keys), tuple(actions)
