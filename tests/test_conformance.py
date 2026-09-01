from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from strongwiz.conformance import check_domain_adapter, check_model_driver
from strongwiz.contracts import ActionSpec, CandidateProposal, Observation, Outcome
from strongwiz.drivers import TerminalAuthority

from .support import evidence, proposal, ref, request


class FixedDriver:
    driver_id = "driver-test"
    driver_version = "driver-v1"
    driver_artifact_ref = ref("driver-artifact")

    def __init__(self, proposals: tuple[CandidateProposal, ...]) -> None:
        self._proposals = proposals

    def propose(self, _request: object) -> Sequence[CandidateProposal]:
        return self._proposals


@dataclass(frozen=True)
class RawState:
    observation: Observation


class ReferenceDomain:
    adapter_id = "reference"
    adapter_version = "1"
    adapter_artifact_ref = ref("reference-domain")

    def normalize_observation(self, raw: object) -> Observation:
        if not isinstance(raw, RawState):
            raise TypeError("expected RawState")
        return raw.observation

    def available_actions(self, _observation: Observation) -> tuple[ActionSpec, ...]:
        return (ActionSpec(name="inspect"), ActionSpec(name="open"))

    def extract_outcome(
        self, before: Observation, action: ActionSpec, raw_after: object
    ) -> Outcome:
        after = self.normalize_observation(raw_after)
        return Outcome(
            outcome_id="outcome-1",
            observation_before_id=before.observation_id,
            observation_before_ref=before.digest,
            observation_after_id=after.observation_id,
            observation_after_ref=after.digest,
            action=action,
            observed_consequences=("state was inspected",),
            state_label="CONTINUE",
            evidence_refs=(ref("outcome"),),
        )

    def terminal_authority(self, _observation: Observation) -> TerminalAuthority:
        return TerminalAuthority.CONTINUE


def after_observation() -> Observation:
    return Observation(
        observation_id="obs-2",
        domain="synthetic",
        scope_id="scope-1",
        epoch=1,
        payload_ref=evidence("after"),
        summary="latch state is visible",
        available_action_names=("inspect", "open"),
    )


def test_model_driver_conformance_passes_exact_bound_proposals() -> None:
    report = check_model_driver(FixedDriver((proposal(),)), request())
    assert report.passed
    assert report.non_authorizing
    assert report.component_kind == "model_driver"


def test_model_driver_conformance_rejects_stale_binding() -> None:
    stale = proposal().model_copy(update={"observation_id": "stale"})
    report = check_model_driver(FixedDriver((stale,)), request())
    assert not report.passed
    binding_check = next(check for check in report.checks if check.name == "request_bindings")
    assert binding_check.passed is False


def test_domain_adapter_conformance_passes_complete_fixture() -> None:
    report = check_domain_adapter(
        ReferenceDomain(),
        RawState(request().observation),
        action=ActionSpec(name="inspect"),
        raw_after=RawState(after_observation()),
    )
    assert report.passed
    assert report.component_kind == "domain_adapter"


def test_domain_adapter_conformance_records_exception() -> None:
    report = check_domain_adapter(ReferenceDomain(), object())
    assert not report.passed
    assert any(check.name == "normalization" for check in report.checks)
