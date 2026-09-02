from __future__ import annotations

import pytest
from pydantic import ValidationError

from strongwiz.canonical import content_hash
from strongwiz.contracts import CostVector
from strongwiz.pal23 import (
    PAL23_ADAPTER_SCHEMA,
    BoundaryAdapter,
    BoundaryRef,
    BoundaryRole,
    CadenceTransition,
    CheckpointCapsule,
    CheckpointCoordinate,
    CheckpointThawReceipt,
    CoordinateRevalidation,
    GrantEpoch,
    RevalidationDisposition,
    StateProjection,
    ThawStatus,
    TransitionClass,
    TransportProfile,
    TransportReceipt,
    pal23_schema_bundle,
)


def _ref(label: str) -> str:
    return content_hash({"ref": label})


def _projection() -> StateProjection:
    return StateProjection(
        projection_id="working-representation",
        state_space="scribe plus Kevin Speak working state",
        included_coordinates=("active_codebook", "derived_summary", "residual_status"),
        excluded_coordinates=(
            "action_authority",
            "audit_history",
            "domain_state",
            "grant",
            "private_reasoning",
            "raw_observation",
            "resources",
        ),
        comparator="canonical JSON equality under exact decoder version",
        provenance_refs=(_ref("pal-2.3-sc21"),),
    )


def _boundary(label: str, role: BoundaryRole = BoundaryRole.SCOPE) -> BoundaryRef:
    return BoundaryRef(
        boundary_id=label,
        role=role,
        carrier_or_domain="typed fixture",
        scope="one test",
        orientation_or_coefficients_or_na="N/A",
        resolution_or_admissible_set_or_na="closed fixture",
        provenance_refs=(_ref(label),),
    )


def _capsule() -> CheckpointCapsule:
    return CheckpointCapsule(
        capsule_id="scribe-checkpoint-1",
        work_projection_ref=_projection().digest,
        work_state_ref=_ref("work"),
        cursor_ref=_ref("cursor"),
        comparator_ref=_ref("comparator"),
        schedule_ref=_ref("schedule"),
        code_ref=_ref("code"),
        dependencies_ref=_ref("dependencies"),
        environment_ref=_ref("environment"),
        invariant_ref=_ref("invariant"),
        grant_epoch_ref=_ref("grant-epoch"),
        resource_ledger_ref=_ref("resources"),
        authority_ceiling="representation-only continuation",
        audit_state_ref=_ref("audit-state"),
        residual_refs=(_ref("residual"),),
        trace_anchor_ref=_ref("trace"),
        external_effect_boundary_ref=_ref("external-effect-boundary"),
    )


def _revalidations(
    capsule: CheckpointCapsule,
    *,
    current_grant_ref: str,
    current_resource_ref: str,
    current_authority: str,
    material_break: CheckpointCoordinate | None = None,
) -> tuple[CoordinateRevalidation, ...]:
    frozen = {
        CheckpointCoordinate.CURSOR: capsule.cursor_ref,
        CheckpointCoordinate.COMPARATOR: capsule.comparator_ref,
        CheckpointCoordinate.SCHEDULE: capsule.schedule_ref,
        CheckpointCoordinate.CODE: capsule.code_ref,
        CheckpointCoordinate.DEPENDENCIES: capsule.dependencies_ref,
        CheckpointCoordinate.ENVIRONMENT: capsule.environment_ref,
        CheckpointCoordinate.INVARIANT: capsule.invariant_ref,
        CheckpointCoordinate.GRANT_EPOCH: capsule.grant_epoch_ref,
        CheckpointCoordinate.RESOURCE_LEDGER: capsule.resource_ledger_ref,
        CheckpointCoordinate.AUTHORITY_CEILING: content_hash(
            {"authority_ceiling": capsule.authority_ceiling}
        ),
        CheckpointCoordinate.AUDIT_STATE: capsule.audit_state_ref,
        CheckpointCoordinate.RESIDUALS: content_hash({"residual_refs": capsule.residual_refs}),
        CheckpointCoordinate.TRACE_ANCHOR: capsule.trace_anchor_ref,
        CheckpointCoordinate.EXTERNAL_EFFECT_BOUNDARY: (capsule.external_effect_boundary_ref),
    }
    current = dict(frozen)
    current[CheckpointCoordinate.GRANT_EPOCH] = current_grant_ref
    current[CheckpointCoordinate.RESOURCE_LEDGER] = current_resource_ref
    current[CheckpointCoordinate.AUTHORITY_CEILING] = content_hash(
        {"authority_ceiling": current_authority}
    )
    if material_break is not None:
        current[material_break] = _ref(f"changed-{material_break.value}")
    output = []
    for coordinate in sorted(CheckpointCoordinate, key=str):
        if coordinate is material_break:
            disposition = RevalidationDisposition.MATERIAL_BREAK
        elif current[coordinate] == frozen[coordinate]:
            disposition = RevalidationDisposition.SAME
        else:
            disposition = RevalidationDisposition.ADMISSIBLE_CHANGE
        output.append(
            CoordinateRevalidation(
                coordinate=coordinate,
                frozen_ref=frozen[coordinate],
                current_ref=current[coordinate],
                evidence_ref=_ref(f"revalidate-{coordinate.value}"),
                disposition=disposition,
            )
        )
    return tuple(output)


def test_boundary_adapter_names_role_scope_preservation_loss_and_reopening() -> None:
    projection = _projection()
    source = BoundaryRef(
        boundary_id="operator-summary-scope",
        role=BoundaryRole.SCOPE,
        carrier_or_domain="typed concise summaries",
        scope="one run-local scribe session",
        orientation_or_coefficients_or_na="N/A: no geometric orientation",
        resolution_or_admissible_set_or_na="five positive material kinds",
        provenance_refs=(_ref("source-receipt"),),
    )
    target = BoundaryRef(
        boundary_id="kevin-interface",
        role=BoundaryRole.INTERFACE,
        carrier_or_domain="canonical JSON and fixed Kevin decoder",
        scope="one run-local representation workspace",
        orientation_or_coefficients_or_na="N/A: symbolic codec",
        resolution_or_admissible_set_or_na="exact UTF-8 byte reconstruction",
        provenance_refs=(projection.digest,),
    )
    adapter = BoundaryAdapter(
        adapter_id="concise-summary-to-kevin",
        source=source,
        target=target,
        hypotheses=("supplied summaries are derived and receipt-bound",),
        preserved_data=("canonical payload", "evidence identity", "uncertainty markers"),
        lost_data=("private reasoning", "raw observation"),
        lossless=False,
        evidence_refs=(_ref("adapter-test"),),
        authority_ceiling="representation only",
        reopening_condition="source kind or projection changes",
    )
    assert adapter.source.role is BoundaryRole.SCOPE
    assert adapter.target.role is BoundaryRole.INTERFACE
    assert adapter.authority == "NONE"

    with pytest.raises(ValidationError, match="lossless"):
        adapter.model_copy(update={"lossless": True})


def test_grant_epoch_slack_is_local_and_top_up_appends() -> None:
    first = GrantEpoch(
        epoch_id="epoch-0",
        ordinal=0,
        grant_ref=_ref("grant-0"),
        budget=CostVector(wall_clock_ms=100, context_tokens=50),
        cumulative_consumption=CostVector(wall_clock_ms=40, context_tokens=12),
        authority_ceiling="one declared experiment",
    )
    assert first.slack.wall_clock_ms == 60
    assert first.slack.context_tokens == 38

    second = GrantEpoch(
        epoch_id="epoch-1",
        ordinal=1,
        predecessor_epoch_ref=first.digest,
        top_up_evidence_ref=_ref("owner-top-up"),
        grant_ref=_ref("grant-1"),
        budget=CostVector(wall_clock_ms=30),
        cumulative_consumption=CostVector(wall_clock_ms=0),
        authority_ceiling="new bounded epoch",
    )
    assert second.predecessor_epoch_ref == first.digest
    with pytest.raises(ValidationError, match="exceeds"):
        first.model_copy(update={"cumulative_consumption": CostVector(wall_clock_ms=101)})


def test_cadence_separates_administrative_heartbeat_from_progress() -> None:
    common = {
        "work_projection_ref": _projection().digest,
        "audit_before_ref": _ref("audit-before"),
        "audit_after_ref": _ref("audit-after"),
        "grant_epoch_ref": _ref("epoch"),
        "cumulative_before": CostVector(wall_clock_ms=1),
        "cumulative_after": CostVector(wall_clock_ms=2),
        "evidence_refs": (_ref("transition"),),
    }
    administrative = CadenceTransition(
        transition_id="heartbeat-1",
        transition_class=TransitionClass.ADMINISTRATIVE,
        work_before_ref=_ref("same-work"),
        work_after_ref=_ref("same-work"),
        progress_coordinate="promoted-codebook count",
        progress_before_ref=_ref("same-progress"),
        progress_after_ref=_ref("same-progress"),
        heartbeat_namespace="strongwiz.controller.suspension",
        counts_as_progress=False,
        **common,
    )
    assert administrative.counts_as_progress is False

    productive = CadenceTransition(
        transition_id="promotion-1",
        transition_class=TransitionClass.PRODUCTIVE,
        work_before_ref=_ref("work-before"),
        work_after_ref=_ref("work-after"),
        progress_coordinate="promoted-codebook count",
        progress_before_ref=_ref("zero"),
        progress_after_ref=_ref("one"),
        heartbeat_namespace=None,
        counts_as_progress=True,
        **common,
    )
    assert productive.counts_as_progress is True

    with pytest.raises(ValidationError, match="cannot claim productive progress"):
        administrative.model_copy(update={"counts_as_progress": True})


def test_checkpoint_thaw_preserves_work_but_not_grant_or_resources() -> None:
    capsule = _capsule()
    current_grant = _ref("current-grant")
    current_resources = _ref("current-resources")
    current_authority = "current externally supplied authority"
    exact = CheckpointThawReceipt(
        thaw_id="thaw-1",
        capsule=capsule,
        capsule_ref=capsule.digest,
        predecessor_ref=_ref("freeze-receipt"),
        deterministic_suffix_ref=_ref("suffix"),
        work_projection_ref=capsule.work_projection_ref,
        frozen_work_state_ref=capsule.work_state_ref,
        thawed_work_state_ref=capsule.work_state_ref,
        frozen_grant_epoch_ref=capsule.grant_epoch_ref,
        current_grant_epoch_ref=current_grant,
        frozen_resource_ledger_ref=capsule.resource_ledger_ref,
        current_resource_ledger_ref=current_resources,
        frozen_authority_ceiling=capsule.authority_ceiling,
        current_authority_ceiling=current_authority,
        coordinate_revalidations=_revalidations(
            capsule,
            current_grant_ref=current_grant,
            current_resource_ref=current_resources,
            current_authority=current_authority,
        ),
        status=ThawStatus.EXACT,
        reentry_required=False,
    )
    assert not exact.renews_grant
    assert not exact.restores_resources
    assert not exact.expands_authority

    broken = CheckpointThawReceipt(
        thaw_id="thaw-2",
        capsule=capsule,
        capsule_ref=capsule.digest,
        predecessor_ref=exact.digest,
        deterministic_suffix_ref=_ref("suffix"),
        work_projection_ref=capsule.work_projection_ref,
        frozen_work_state_ref=capsule.work_state_ref,
        thawed_work_state_ref=_ref("changed-work"),
        frozen_grant_epoch_ref=capsule.grant_epoch_ref,
        current_grant_epoch_ref=current_grant,
        frozen_resource_ledger_ref=capsule.resource_ledger_ref,
        current_resource_ledger_ref=current_resources,
        frozen_authority_ceiling=capsule.authority_ceiling,
        current_authority_ceiling=current_authority,
        coordinate_revalidations=_revalidations(
            capsule,
            current_grant_ref=current_grant,
            current_resource_ref=current_resources,
            current_authority=current_authority,
            material_break=CheckpointCoordinate.ENVIRONMENT,
        ),
        transport_break_reasons=("environment_identity_changed",),
        status=ThawStatus.TRANSPORT_BREAK,
        reentry_required=True,
    )
    assert broken.reentry_required


def test_transport_profile_rejects_heartbeat_work_motion() -> None:
    common = {
        "receipt_id": "transport-1",
        "predecessor_ref": _ref("predecessor"),
        "work_projection_ref": _projection().digest,
        "grant_epoch_ref": _ref("epoch"),
        "resource_ledger_ref": _ref("resource"),
        "trace_anchor_ref": _ref("trace"),
        "authority_ceiling": "evidence only",
        "residual_refs": (),
        "reopening_condition": "projection changes",
    }
    receipt = TransportReceipt(
        profile=TransportProfile.HEARTBEAT_STUTTER,
        source_work_state_ref=_ref("work"),
        target_work_state_ref=_ref("work"),
        **common,
    )
    assert receipt.profile is TransportProfile.HEARTBEAT_STUTTER
    with pytest.raises(ValidationError, match="must preserve"):
        receipt.model_copy(update={"target_work_state_ref": _ref("other-work")})
    claim_ceiling = pal23_schema_bundle()["claim_ceiling"]
    assert isinstance(claim_ceiling, str)
    assert claim_ceiling.startswith("targeted")


def test_boundary_and_projection_invalid_claims_fail_closed() -> None:
    with pytest.raises(ValidationError, match="identity, carrier, scope"):
        _boundary("source").model_copy(update={"scope": ""})
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        _boundary("source").model_copy(update={"provenance_refs": ("bad",)})
    with pytest.raises(ValidationError, match="sorted and unique"):
        _boundary("source").model_copy(update={"provenance_refs": (_ref("z"), _ref("a"))})

    projection = _projection()
    with pytest.raises(ValidationError, match="both included and excluded"):
        projection.model_copy(
            update={
                "excluded_coordinates": tuple(
                    sorted((*projection.excluded_coordinates, "active_codebook"))
                )
            }
        )
    with pytest.raises(ValidationError, match="nonempty entries"):
        projection.model_copy(update={"included_coordinates": ()})


def test_adapter_requires_real_crossing_and_explicit_evidence() -> None:
    source = _boundary("source")
    target = _boundary("target", BoundaryRole.INTERFACE)
    adapter = BoundaryAdapter(
        adapter_id="adapter",
        source=source,
        target=target,
        hypotheses=("declared hypothesis",),
        preserved_data=("identity",),
        lost_data=(),
        lossless=True,
        evidence_refs=(_ref("evidence"),),
        authority_ceiling="evidence only",
        reopening_condition="boundary changes",
    )
    with pytest.raises(ValidationError, match="distinct declared boundaries"):
        adapter.model_copy(update={"target": source})
    with pytest.raises(ValidationError, match="nonempty entries"):
        adapter.model_copy(update={"hypotheses": ()})
    with pytest.raises(ValidationError, match="requires evidence"):
        adapter.model_copy(update={"evidence_refs": ()})


def test_grant_cadence_and_checkpoint_invalid_transports_fail_closed() -> None:
    first = GrantEpoch(
        epoch_id="epoch-0",
        ordinal=0,
        grant_ref=_ref("grant"),
        budget=CostVector(wall_clock_ms=10),
        cumulative_consumption=CostVector(),
        authority_ceiling="bounded",
    )
    with pytest.raises(ValidationError, match="genesis grant epoch"):
        first.model_copy(update={"predecessor_epoch_ref": _ref("prior")})
    with pytest.raises(ValidationError, match="requires predecessor"):
        first.model_copy(update={"ordinal": 1})

    common = {
        "work_projection_ref": _projection().digest,
        "work_before_ref": _ref("work"),
        "work_after_ref": _ref("work"),
        "progress_coordinate": "evidence count",
        "progress_before_ref": _ref("progress"),
        "progress_after_ref": _ref("progress"),
        "audit_before_ref": _ref("audit-0"),
        "audit_after_ref": _ref("audit-1"),
        "grant_epoch_ref": first.digest,
        "cumulative_before": CostVector(wall_clock_ms=2),
        "cumulative_after": CostVector(wall_clock_ms=1),
        "heartbeat_namespace": "test.heartbeat",
        "evidence_refs": (_ref("cadence"),),
        "counts_as_progress": False,
    }
    with pytest.raises(ValidationError, match="cannot decrease"):
        CadenceTransition(
            transition_id="bad-cadence",
            transition_class=TransitionClass.ADMINISTRATIVE,
            **common,
        )

    capsule = _capsule().model_copy(
        update={
            "capsule_id": "capsule",
            "grant_epoch_ref": first.digest,
            "authority_ceiling": "bounded",
            "residual_refs": (),
        }
    )
    current_grant = _ref("current-grant")
    current_resources = _ref("current-resources")
    current_authority = "current externally supplied authority"
    with pytest.raises(ValidationError, match="exact thaw"):
        CheckpointThawReceipt(
            thaw_id="bad-thaw",
            capsule=capsule,
            capsule_ref=capsule.digest,
            predecessor_ref=_ref("freeze"),
            deterministic_suffix_ref=_ref("suffix"),
            work_projection_ref=capsule.work_projection_ref,
            frozen_work_state_ref=capsule.work_state_ref,
            thawed_work_state_ref=_ref("different"),
            frozen_grant_epoch_ref=capsule.grant_epoch_ref,
            current_grant_epoch_ref=current_grant,
            frozen_resource_ledger_ref=capsule.resource_ledger_ref,
            current_resource_ledger_ref=current_resources,
            frozen_authority_ceiling=capsule.authority_ceiling,
            current_authority_ceiling=current_authority,
            coordinate_revalidations=_revalidations(
                capsule,
                current_grant_ref=current_grant,
                current_resource_ref=current_resources,
                current_authority=current_authority,
            ),
            status=ThawStatus.EXACT,
            reentry_required=False,
        )
    with pytest.raises(ValidationError, match="must preserve a material mismatch"):
        CheckpointThawReceipt(
            thaw_id="bad-break",
            capsule=capsule,
            capsule_ref=capsule.digest,
            predecessor_ref=_ref("freeze"),
            deterministic_suffix_ref=_ref("suffix"),
            work_projection_ref=capsule.work_projection_ref,
            frozen_work_state_ref=capsule.work_state_ref,
            thawed_work_state_ref=_ref("different"),
            frozen_grant_epoch_ref=capsule.grant_epoch_ref,
            current_grant_epoch_ref=current_grant,
            frozen_resource_ledger_ref=capsule.resource_ledger_ref,
            current_resource_ledger_ref=current_resources,
            frozen_authority_ceiling=capsule.authority_ceiling,
            current_authority_ceiling=current_authority,
            coordinate_revalidations=_revalidations(
                capsule,
                current_grant_ref=current_grant,
                current_resource_ref=current_resources,
                current_authority=current_authority,
                material_break=CheckpointCoordinate.ENVIRONMENT,
            ),
            status=ThawStatus.TRANSPORT_BREAK,
            reentry_required=False,
        )


def test_transport_profiles_require_their_native_references() -> None:
    common = {
        "receipt_id": "transport",
        "predecessor_ref": _ref("predecessor"),
        "work_projection_ref": _projection().digest,
        "source_work_state_ref": _ref("source-work"),
        "target_work_state_ref": _ref("target-work"),
        "grant_epoch_ref": _ref("epoch"),
        "resource_ledger_ref": _ref("resources"),
        "trace_anchor_ref": _ref("trace"),
        "authority_ceiling": "evidence only",
        "residual_refs": (),
        "reopening_condition": "a bound identity changes",
    }
    with pytest.raises(ValidationError, match="checkpoint profiles"):
        TransportReceipt(profile=TransportProfile.CHECKPOINT_FREEZE, **common)
    with pytest.raises(ValidationError, match="must appear together"):
        TransportReceipt(
            profile=TransportProfile.BOUNDARY_ADAPTER,
            capsule_ref=_ref("capsule"),
            **common,
        )
    with pytest.raises(ValidationError, match="re-entry profiles"):
        TransportReceipt(profile=TransportProfile.REENTRY, **common)


def test_pal23_records_reject_wrong_schema() -> None:
    source = _boundary("source")
    target = _boundary("target", BoundaryRole.INTERFACE)
    adapter = BoundaryAdapter(
        adapter_id="adapter",
        source=source,
        target=target,
        hypotheses=("typed fixture",),
        preserved_data=("identity",),
        lossless=True,
        evidence_refs=(_ref("adapter-evidence"),),
        authority_ceiling="evidence only",
        reopening_condition="scope changes",
    )
    projection = _projection()
    epoch = GrantEpoch(
        epoch_id="epoch",
        ordinal=0,
        grant_ref=_ref("grant"),
        budget=CostVector(),
        cumulative_consumption=CostVector(),
        authority_ceiling="bounded",
    )
    cadence = CadenceTransition(
        transition_id="heartbeat",
        transition_class=TransitionClass.ADMINISTRATIVE,
        work_projection_ref=projection.digest,
        work_before_ref=_ref("work"),
        work_after_ref=_ref("work"),
        progress_coordinate="test count",
        progress_before_ref=_ref("progress"),
        progress_after_ref=_ref("progress"),
        audit_before_ref=_ref("audit-before"),
        audit_after_ref=_ref("audit-after"),
        grant_epoch_ref=epoch.digest,
        cumulative_before=CostVector(),
        cumulative_after=CostVector(),
        heartbeat_namespace="test.heartbeat",
        evidence_refs=(_ref("cadence-evidence"),),
        counts_as_progress=False,
    )
    capsule = _capsule()
    current_grant = _ref("grant-current")
    current_resources = _ref("resource-current")
    current_authority = "current bounded authority"
    revalidations = _revalidations(
        capsule,
        current_grant_ref=current_grant,
        current_resource_ref=current_resources,
        current_authority=current_authority,
    )
    thaw = CheckpointThawReceipt(
        thaw_id="thaw",
        capsule=capsule,
        capsule_ref=capsule.digest,
        predecessor_ref=_ref("freeze"),
        deterministic_suffix_ref=_ref("suffix"),
        work_projection_ref=capsule.work_projection_ref,
        frozen_work_state_ref=capsule.work_state_ref,
        thawed_work_state_ref=capsule.work_state_ref,
        frozen_grant_epoch_ref=capsule.grant_epoch_ref,
        current_grant_epoch_ref=current_grant,
        frozen_resource_ledger_ref=capsule.resource_ledger_ref,
        current_resource_ledger_ref=current_resources,
        frozen_authority_ceiling=capsule.authority_ceiling,
        current_authority_ceiling=current_authority,
        coordinate_revalidations=revalidations,
        status=ThawStatus.EXACT,
        reentry_required=False,
    )
    transport = TransportReceipt(
        receipt_id="transport",
        profile=TransportProfile.HEARTBEAT_STUTTER,
        predecessor_ref=_ref("predecessor"),
        work_projection_ref=projection.digest,
        source_work_state_ref=_ref("work"),
        target_work_state_ref=_ref("work"),
        grant_epoch_ref=epoch.digest,
        resource_ledger_ref=_ref("resource"),
        trace_anchor_ref=_ref("trace"),
        authority_ceiling="bounded",
        residual_refs=(),
        reopening_condition="projection changes",
    )
    records = (
        source,
        adapter,
        projection,
        epoch,
        cadence,
        capsule,
        revalidations[0],
        thaw,
        transport,
    )
    for record in records:
        with pytest.raises(ValidationError, match=r"unsupported PAL v2\.3 adapter schema"):
            record.model_copy(update={"schema_id": f"{PAL23_ADAPTER_SCHEMA}.wrong"})


def test_thaw_binds_capsule_projection_and_every_non_work_coordinate() -> None:
    capsule = _capsule()
    current_grant = _ref("grant-current")
    current_resources = _ref("resource-current")
    current_authority = "narrowed current authority"
    revalidations = _revalidations(
        capsule,
        current_grant_ref=current_grant,
        current_resource_ref=current_resources,
        current_authority=current_authority,
    )
    thaw = CheckpointThawReceipt(
        thaw_id="bound-thaw",
        capsule=capsule,
        capsule_ref=capsule.digest,
        predecessor_ref=_ref("freeze"),
        deterministic_suffix_ref=_ref("suffix"),
        work_projection_ref=capsule.work_projection_ref,
        frozen_work_state_ref=capsule.work_state_ref,
        thawed_work_state_ref=capsule.work_state_ref,
        frozen_grant_epoch_ref=capsule.grant_epoch_ref,
        current_grant_epoch_ref=current_grant,
        frozen_resource_ledger_ref=capsule.resource_ledger_ref,
        current_resource_ledger_ref=current_resources,
        frozen_authority_ceiling=capsule.authority_ceiling,
        current_authority_ceiling=current_authority,
        coordinate_revalidations=revalidations,
        status=ThawStatus.EXACT,
        reentry_required=False,
    )
    with pytest.raises(ValidationError, match="frozen capsule coordinate"):
        thaw.model_copy(update={"work_projection_ref": _ref("other-projection")})
    with pytest.raises(ValidationError, match="every required non-work coordinate"):
        thaw.model_copy(update={"coordinate_revalidations": revalidations[:-1]})
    changed = revalidations[0].model_copy(
        update={"frozen_ref": _ref("forged"), "current_ref": _ref("forged")}
    )
    forged = tuple(sorted((changed, *revalidations[1:]), key=lambda item: str(item.coordinate)))
    with pytest.raises(ValidationError, match="changed frozen"):
        thaw.model_copy(update={"coordinate_revalidations": forged})


def test_checkpoint_thaw_transport_requires_work_equality() -> None:
    capsule = _capsule()
    receipt = TransportReceipt(
        receipt_id="thaw-transport",
        profile=TransportProfile.CHECKPOINT_THAW,
        predecessor_ref=_ref("predecessor"),
        work_projection_ref=capsule.work_projection_ref,
        source_work_state_ref=capsule.work_state_ref,
        target_work_state_ref=capsule.work_state_ref,
        grant_epoch_ref=_ref("epoch"),
        resource_ledger_ref=_ref("resources"),
        trace_anchor_ref=_ref("trace"),
        authority_ceiling="bounded",
        residual_refs=(),
        reopening_condition="any material mismatch",
        capsule=capsule,
        capsule_ref=capsule.digest,
    )
    with pytest.raises(ValidationError, match="use a transport break"):
        receipt.model_copy(update={"target_work_state_ref": _ref("work-after")})
    with pytest.raises(ValidationError, match="capsule work projection"):
        receipt.model_copy(update={"work_projection_ref": _ref("other-projection")})
    with pytest.raises(ValidationError, match="supplied capsule work state"):
        receipt.model_copy(
            update={
                "source_work_state_ref": _ref("unrelated-work"),
                "target_work_state_ref": _ref("unrelated-work"),
            }
        )
    with pytest.raises(ValidationError, match="does not bind its supplied capsule"):
        receipt.model_copy(update={"capsule_ref": _ref("other-capsule")})


def test_changed_environment_cannot_be_downgraded_to_admissible_change() -> None:
    with pytest.raises(ValidationError, match="material checkpoint break"):
        CoordinateRevalidation(
            coordinate=CheckpointCoordinate.ENVIRONMENT,
            frozen_ref=_ref("environment-before"),
            current_ref=_ref("environment-after"),
            evidence_ref=_ref("environment-comparison"),
            disposition=RevalidationDisposition.ADMISSIBLE_CHANGE,
        )
