"""One governing objective, many bounded subgoals, and reopenable attention."""

from __future__ import annotations

from pydantic import model_validator

from strongwiz.contracts import ContractModel, Goal, GoalStatus, NonNegativeInt


class GoalError(ValueError):
    pass


class GoalTransition(ContractModel):
    goal_id: str
    prior_ref: str
    next_ref: str
    prior_status: GoalStatus
    next_status: GoalStatus
    reason: str
    evidence_refs: tuple[str, ...]


class ScopeTransition(ContractModel):
    old_scope_id: str
    old_epoch: NonNegativeInt
    new_scope_id: str
    new_epoch: NonNegativeInt
    observation_ref: str
    attentional_closure: bool = True
    retained_fact_refs: tuple[str, ...] = ()
    reopening_condition: str

    @model_validator(mode="after")
    def validate_scope(self) -> ScopeTransition:
        if self.new_epoch <= self.old_epoch:
            raise ValueError("new surface must advance the attention epoch")
        if not all((self.old_scope_id, self.new_scope_id, self.observation_ref)):
            raise ValueError("surface transition requires both scopes and an observation")
        if not self.reopening_condition:
            raise ValueError("attentional closure must remain reopenable")
        return self


class GoalGraph:
    """Current goal projection plus immutable transition history."""

    def __init__(self, governing_goal: Goal) -> None:
        if governing_goal.parent_goal_id is not None:
            raise GoalError("governing goal cannot have a parent")
        if governing_goal.status is not GoalStatus.ACTIVE:
            raise GoalError("governing goal must start active")
        self._governing_id = governing_goal.goal_id
        self._current: dict[str, Goal] = {governing_goal.goal_id: governing_goal}
        self._versions: dict[str, list[Goal]] = {governing_goal.goal_id: [governing_goal]}
        self._transitions: list[GoalTransition] = []
        self._scope_transitions: list[ScopeTransition] = []

    @property
    def governing_goal(self) -> Goal:
        return self._current[self._governing_id]

    def add_subgoal(self, goal: Goal) -> None:
        if goal.goal_id in self._current:
            raise GoalError("goal identity already exists")
        if goal.parent_goal_id not in self._current:
            raise GoalError("subgoal parent is unknown")
        if goal.governing_goal_id != self._governing_id:
            raise GoalError("subgoal governing-objective link is wrong")
        self._current[goal.goal_id] = goal
        self._versions[goal.goal_id] = [goal]

    def transition(
        self,
        goal_id: str,
        status: GoalStatus,
        *,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> GoalTransition:
        if not reason or not evidence_refs:
            raise GoalError("goal transition requires reason and evidence")
        try:
            prior = self._current[goal_id]
        except KeyError as error:
            raise GoalError("unknown goal") from error
        if prior.status is status:
            raise GoalError("goal transition must change status")
        if status is GoalStatus.REOPENED and not prior.reopening_condition:
            raise GoalError("goal has no declared reopening condition")
        next_goal = prior.model_copy(update={"status": status})
        transition = GoalTransition(
            goal_id=goal_id,
            prior_ref=prior.digest,
            next_ref=next_goal.digest,
            prior_status=prior.status,
            next_status=status,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        self._current[goal_id] = next_goal
        self._versions[goal_id].append(next_goal)
        self._transitions.append(transition)
        return transition

    def new_surface(
        self,
        transition: ScopeTransition,
        *,
        park_goal_ids: tuple[str, ...] = (),
    ) -> None:
        for goal_id in park_goal_ids:
            goal = self._current.get(goal_id)
            if goal is None:
                raise GoalError("cannot park unknown stage goal")
            if goal.status is GoalStatus.ACTIVE:
                self.transition(
                    goal_id,
                    GoalStatus.PARKED,
                    reason="attention moved to a materially changed surface",
                    evidence_refs=(transition.observation_ref,),
                )
        self._scope_transitions.append(transition)

    def current(self, goal_id: str) -> Goal:
        try:
            return self._current[goal_id]
        except KeyError as error:
            raise GoalError("unknown goal") from error

    @property
    def active_goals(self) -> tuple[Goal, ...]:
        return tuple(
            self._current[key]
            for key in sorted(self._current)
            if self._current[key].status in {GoalStatus.ACTIVE, GoalStatus.REOPENED}
        )

    @property
    def transitions(self) -> tuple[GoalTransition, ...]:
        return tuple(self._transitions)

    @property
    def scope_transitions(self) -> tuple[ScopeTransition, ...]:
        return tuple(self._scope_transitions)
