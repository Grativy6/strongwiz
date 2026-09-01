"""Non-authorizing structural checks for replaceable Strongwiz adapters."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from strongwiz.contracts import ActionSpec, CandidateProposal, ContractModel, ReasoningRequest
from strongwiz.drivers import DomainAdapter, ModelDriver, TerminalAuthority

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ConformanceCheck(ContractModel):
    name: str
    passed: bool
    detail: str


class ConformanceReport(ContractModel):
    """Bounded adapter result; passing does not grant authority or prove quality."""

    schema_id: str = "strongwiz.adapter-conformance.v1"
    component_kind: Literal["model_driver", "domain_adapter"]
    component_id: str
    component_version: str
    component_artifact_ref: str
    checks: tuple[ConformanceCheck, ...]
    limitations: tuple[str, ...]
    non_authorizing: bool = True

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


def _check(name: str, condition: bool, passing: str, failing: str) -> ConformanceCheck:
    return ConformanceCheck(
        name=name,
        passed=condition,
        detail=passing if condition else failing,
    )


def _identity_checks(identity: str, version: str, artifact: str) -> list[ConformanceCheck]:
    return [
        _check(
            "identity",
            bool(identity.strip()),
            "component identity is declared",
            "component identity is empty",
        ),
        _check(
            "version",
            bool(version.strip()),
            "component version is declared",
            "component version is empty",
        ),
        _check(
            "artifact_ref",
            _DIGEST.fullmatch(artifact) is not None,
            "artifact reference is a lowercase SHA-256 digest",
            "artifact reference is not a lowercase SHA-256 digest",
        ),
    ]


def check_model_driver(
    driver: ModelDriver,
    request: ReasoningRequest,
) -> ConformanceReport:
    """Invoke a driver once and check exact request bindings and output shape."""

    checks = _identity_checks(
        driver.driver_id,
        driver.driver_version,
        driver.driver_artifact_ref,
    )
    try:
        raw = driver.propose(request)
        sequence_ok = isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
        proposals = tuple(raw) if sequence_ok else ()
        typed = sequence_ok and all(isinstance(value, CandidateProposal) for value in proposals)
        checks.append(
            _check(
                "proposal_sequence",
                typed,
                "driver returned a CandidateProposal sequence",
                "driver did not return a CandidateProposal sequence",
            )
        )
    except Exception as error:  # adapter exceptions are evidence, not tool crashes
        proposals = ()
        typed = False
        checks.append(
            ConformanceCheck(
                name="proposal_sequence",
                passed=False,
                detail=f"driver raised {type(error).__name__}",
            )
        )

    if typed:
        ids = tuple(proposal.proposal_id for proposal in proposals)
        checks.append(
            _check(
                "proposal_ids",
                len(set(ids)) == len(ids),
                "proposal identities are unique",
                "proposal identities are duplicated",
            )
        )
        exact_bindings = all(
            proposal.model_driver_id == driver.driver_id
            and proposal.observation_id == request.observation.observation_id
            and proposal.observation_ref == request.observation.digest
            and proposal.scope_id == request.observation.scope_id
            and proposal.goal_id == request.scoped_goal.goal_id
            and proposal.goal_ref == request.scoped_goal.digest
            for proposal in proposals
        )
        checks.append(
            _check(
                "request_bindings",
                exact_bindings,
                "every proposal binds the exact driver, observation, scope, and goal",
                "one or more proposals have stale or foreign request bindings",
            )
        )
        action_aperture = set(request.observation.available_action_names)
        actions_known = all(proposal.action.name in action_aperture for proposal in proposals)
        checks.append(
            _check(
                "action_aperture",
                actions_known,
                "every proposed action is visible in the observation aperture",
                "one or more proposed actions are outside the observation aperture",
            )
        )

    return ConformanceReport(
        component_kind="model_driver",
        component_id=driver.driver_id,
        component_version=driver.driver_version,
        component_artifact_ref=driver.driver_artifact_ref,
        checks=tuple(checks),
        limitations=(
            "one structural invocation does not establish reasoning quality or determinism",
            "this report grants no permission, authorization, or external effect",
        ),
    )


def check_domain_adapter(
    adapter: DomainAdapter,
    raw_before: object,
    *,
    action: ActionSpec | None = None,
    raw_after: object | None = None,
) -> ConformanceReport:
    """Check normalization and, when supplied, one exact outcome translation."""

    checks = _identity_checks(
        adapter.adapter_id,
        adapter.adapter_version,
        adapter.adapter_artifact_ref,
    )
    before = None
    actions: tuple[ActionSpec, ...] = ()
    try:
        first = adapter.normalize_observation(raw_before)
        second = adapter.normalize_observation(raw_before)
        before = first
        checks.append(
            _check(
                "normalization_repeatability",
                first == second,
                "repeated normalization produced identical observations",
                "repeated normalization produced different observations",
            )
        )
        raw_actions = adapter.available_actions(first)
        actions = tuple(raw_actions)
        valid_actions = all(isinstance(value, ActionSpec) for value in actions)
        unique_actions = len({value.digest for value in actions}) == len(actions)
        aperture_matches = {value.name for value in actions} == set(
            first.available_action_names
        )
        checks.extend(
            (
                _check(
                    "action_contracts",
                    valid_actions and unique_actions,
                    "available actions are unique ActionSpec values",
                    "available actions are invalid or duplicated",
                ),
                _check(
                    "action_aperture",
                    valid_actions and aperture_matches,
                    "adapter actions match the normalized observation aperture",
                    "adapter actions disagree with the normalized observation aperture",
                ),
            )
        )
        authority = adapter.terminal_authority(first)
        checks.append(
            _check(
                "terminal_authority",
                isinstance(authority, TerminalAuthority),
                "terminal state is expressed through TerminalAuthority",
                "terminal state is not expressed through TerminalAuthority",
            )
        )
    except Exception as error:  # adapter exceptions become a failed report
        checks.append(
            ConformanceCheck(
                name="normalization",
                passed=False,
                detail=f"adapter raised {type(error).__name__}",
            )
        )

    if before is not None and action is not None and raw_after is not None:
        try:
            after = adapter.normalize_observation(raw_after)
            outcome = adapter.extract_outcome(before, action, raw_after)
            exact = (
                outcome.observation_before_id == before.observation_id
                and outcome.observation_before_ref == before.digest
                and outcome.observation_after_id == after.observation_id
                and outcome.observation_after_ref == after.digest
                and outcome.action == action
            )
            checks.append(
                _check(
                    "outcome_bindings",
                    exact,
                    "outcome binds the exact before observation, action, and after observation",
                    "outcome has stale or foreign before/action/after bindings",
                )
            )
        except Exception as error:
            checks.append(
                ConformanceCheck(
                    name="outcome_bindings",
                    passed=False,
                    detail=f"adapter raised {type(error).__name__}",
                )
            )

    return ConformanceReport(
        component_kind="domain_adapter",
        component_id=adapter.adapter_id,
        component_version=adapter.adapter_version,
        component_artifact_ref=adapter.adapter_artifact_ref,
        checks=tuple(checks),
        limitations=(
            "fixture conformance does not establish domain completeness",
            "this report does not call an executor or authorize an external effect",
        ),
    )
