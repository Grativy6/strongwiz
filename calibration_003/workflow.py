"""Prepare and verify matched shadow labs plus a deterministic scribe preflight."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from calibration_002.transition import Calibration002TransitionManifest
from calibration_003.models import (
    Calibration003Error,
    Calibration003Plan,
    CampaignArmIndex,
    CampaignArmRole,
    CampaignIndex,
    CampaignPreparationMarker,
    CampaignVerification,
    SyntheticPreflightReceipt,
    V2CarryPacket,
)
from strongwiz.canonical import (
    canonical_bytes,
    content_hash,
    parse_strict_json,
    sha256_bytes,
)
from strongwiz.curriculum import CurriculumStageHandoff
from strongwiz.lab import (
    LabManifest,
    LabVerification,
    RunSpec,
    initialize_lab,
    verify_lab_genesis,
)
from strongwiz.ledger import SQLiteLedger
from strongwiz.pal23 import BoundaryAdapter, BoundaryRef, BoundaryRole, StateProjection
from strongwiz.pathsafe import is_link_like
from strongwiz.scribe import (
    CallableScribeDriver,
    ScribeCycleStatus,
    ScribeDraft,
    ScribeDriverBinding,
    ScribeEvidenceAtom,
    ScribeEvidenceStatus,
    ScribeMaterialInput,
    ScribeMaterialKind,
    ScribePolicy,
    ScribeRequestView,
    ScribeSession,
    ScribeTrigger,
)
from strongwiz.shorthand import (
    KevinSpeakConfiguration,
    KevinSpeakEntry,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
)

CAMPAIGN_INDEX_PATH = "campaign.index.json"
CAMPAIGN_PREPARATION_MARKER_PATH = "campaign.preparation.json"
SYNTHETIC_PREFLIGHT_RECEIPT_PATH = "synthetic-preflight.receipt.json"

_V2_CAMPAIGN_SUMMARY_SCHEMA = "strongwiz.arc-agi3-calibration-campaign-summary.v1"
_V2_TRANSFERABLE_MECHANICS_SCHEMA = "strongwiz.calibration-002-stage4-transferable-mechanics.v1"


@dataclass(frozen=True, slots=True)
class LoadedV2CarryPacket:
    """A validated packet together with the identity of its exact source bytes."""

    packet: V2CarryPacket
    file_sha256: str
    source_path: Path
    repository_root: Path


def _immutable_write(path: Path, payload: bytes) -> None:
    if path.exists() or is_link_like(path):
        if path.is_file() and not is_link_like(path) and path.read_bytes() == payload:
            return
        raise Calibration003Error(f"immutable artifact already exists: {path}")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _require_fresh_root(root: Path, *, label: str) -> Path:
    if is_link_like(root):
        raise Calibration003Error(f"{label} must not be link-like")
    if root.exists():
        if not root.is_dir():
            raise Calibration003Error(f"{label} must be a directory")
        if any(root.iterdir()):
            raise Calibration003Error(f"{label} requires an absent or empty root")
    else:
        try:
            root.parent.resolve(strict=True)
        except OSError as error:
            raise Calibration003Error(f"{label} parent must already exist") from error
        root.mkdir()
    return root.resolve(strict=True)


def _campaign_root_for_preparation(
    root: Path,
    marker: CampaignPreparationMarker,
) -> Path:
    if is_link_like(root):
        raise Calibration003Error("campaign root must not be link-like")
    if root.exists():
        if not root.is_dir():
            raise Calibration003Error("campaign root must be a directory")
        if any(root.iterdir()):
            marker_path = root / CAMPAIGN_PREPARATION_MARKER_PATH
            if is_link_like(marker_path) or not marker_path.is_file():
                raise Calibration003Error(
                    "campaign root contains data without its matching preparation marker"
                )
            raw = marker_path.read_bytes()
            try:
                found = CampaignPreparationMarker.model_validate_json(raw)
            except ValueError as error:
                raise Calibration003Error(
                    f"invalid campaign preparation marker: {error}"
                ) from error
            if canonical_bytes(found) != raw or found != marker:
                raise Calibration003Error(
                    "campaign root preparation marker does not bind this plan"
                )
    else:
        try:
            root.parent.resolve(strict=True)
        except OSError as error:
            raise Calibration003Error("campaign root parent must already exist") from error
        root.mkdir()
    resolved = root.resolve(strict=True)
    _immutable_write(
        resolved / CAMPAIGN_PREPARATION_MARKER_PATH,
        canonical_bytes(marker),
    )
    return resolved


def load_plan(path: str | Path) -> Calibration003Plan:
    """Load one exact canonical plan; prose files never become instructions."""

    source = Path(path).resolve(strict=True)
    if is_link_like(source) or not source.is_file():
        raise Calibration003Error("campaign plan must be one ordinary file")
    raw = source.read_bytes()
    try:
        plan = Calibration003Plan.model_validate_json(raw)
    except ValueError as error:
        raise Calibration003Error(f"invalid campaign plan: {error}") from error
    if canonical_bytes(plan) != raw:
        raise Calibration003Error("campaign plan must use exact canonical JSON")
    return plan


def _repository_file(
    repository_root: Path,
    path: str | Path,
    *,
    label: str,
) -> Path:
    root = repository_root.resolve(strict=True)
    if is_link_like(root) or not root.is_dir():
        raise Calibration003Error("repository root must be one ordinary directory")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise Calibration003Error(f"{label} escapes the repository root") from error
    if any(":" in part for part in relative.parts):
        raise Calibration003Error(f"{label} must not use an alternate data stream")
    current = root
    for part in relative.parts:
        current = current / part
        if is_link_like(current):
            raise Calibration003Error(f"{label} crosses a link-like path")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise Calibration003Error(
            f"{label} is missing or escapes the repository root"
        ) from error
    if not resolved.is_file():
        raise Calibration003Error(f"{label} must be one ordinary file")
    if resolved.stat().st_nlink != 1:
        raise Calibration003Error(f"{label} must not be a hard-linked file")
    return resolved


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise Calibration003Error(f"{label} must be a JSON object")
    return value


def _digest_field(value: dict[str, Any], key: str, *, label: str) -> str:
    found = value.get(key)
    if not isinstance(found, str):
        raise Calibration003Error(f"{label} must contain string field {key!r}")
    if len(found) != 64 or found != found.lower():
        raise Calibration003Error(f"{label} field {key!r} must be a lowercase SHA-256")
    try:
        bytes.fromhex(found)
    except ValueError as error:
        raise Calibration003Error(
            f"{label} field {key!r} must be a lowercase SHA-256"
        ) from error
    return found


def _typed_source_evidence_refs(raw: bytes, *, source_path: Path) -> set[str]:
    """Resolve only schema-known evidence identity fields, never ambient digest text."""

    if source_path.suffix.casefold() != ".json":
        return set()
    try:
        payload = _mapping(
            parse_strict_json(raw),
            label=f"v2 carry JSON source {source_path.name!r}",
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise Calibration003Error(
            f"invalid v2 carry JSON source {source_path.name!r}: {error}"
        ) from error
    schema = payload.get("schema")
    if schema == _V2_CAMPAIGN_SUMMARY_SCHEMA:
        stages = payload.get("stages")
        if not isinstance(stages, list) or not stages:
            raise Calibration003Error("v2 campaign summary requires a non-empty stage list")
        refs = {
            _digest_field(
                _mapping(stage, label="v2 campaign-summary stage"),
                "run_seal_ref",
                label="v2 campaign-summary stage",
            )
            for stage in stages
        }
        final_handoff = _mapping(
            payload.get("final_handoff"),
            label="v2 campaign-summary final handoff",
        )
        refs.add(
            _digest_field(
                final_handoff,
                "handoff_ref",
                label="v2 campaign-summary final handoff",
            )
        )
        refs.add(
            _digest_field(
                final_handoff,
                "selected_recommendation_ref",
                label="v2 campaign-summary final handoff",
            )
        )
        return refs
    if schema == "strongwiz.calibration-002-transition-manifest.v1":
        try:
            manifest = Calibration002TransitionManifest.model_validate(payload)
        except ValueError as error:
            raise Calibration003Error(
                f"invalid v2 transition manifest {source_path.name!r}: {error}"
            ) from error
        return {
            reference
            for artifact in manifest.artifacts
            for reference in (artifact.sha256, artifact.object_ref)
        }
    if schema == "strongwiz.curriculum-stage-handoff.v1":
        try:
            handoff = CurriculumStageHandoff.model_validate(payload)
        except ValueError as error:
            raise Calibration003Error(
                f"invalid v2 final handoff {source_path.name!r}: {error}"
            ) from error
        refs = {
            handoff.stage_start_ref,
            handoff.stage_ref,
            handoff.run_seal_ref,
            *handoff.progress_evidence_refs,
            *handoff.retained_mechanic_refs,
        }
        if handoff.active_codebook_ref is not None:
            refs.add(handoff.active_codebook_ref)
        return refs
    if schema == _V2_TRANSFERABLE_MECHANICS_SCHEMA:
        mechanics = payload.get("mechanics")
        if not isinstance(mechanics, list):
            raise Calibration003Error("v2 transferable mechanics requires a mechanic list")
        return set()
    raise Calibration003Error(
        f"unsupported v2 carry evidence source schema in {source_path.name!r}: {schema!r}"
    )


def load_v2_carry_packet(
    path: str | Path,
    repository_root: str | Path,
    *,
    expected_ref: str | None = None,
) -> LoadedV2CarryPacket:
    """Validate one exact packet and every repository-local source artifact it cites."""

    root = Path(repository_root)
    source = _repository_file(root, path, label="v2 carry packet")
    raw = source.read_bytes()
    file_sha256 = sha256_bytes(raw)
    if expected_ref is not None and file_sha256 != expected_ref:
        raise Calibration003Error("v2 carry packet bytes do not match the declared digest")
    try:
        packet = V2CarryPacket.model_validate(parse_strict_json(raw))
    except ValueError as error:
        raise Calibration003Error(f"invalid v2 carry packet: {error}") from error
    resolved_root = root.resolve(strict=True)
    anchored_evidence_refs: set[str] = set()
    for artifact in packet.source_artifacts:
        artifact_path = _repository_file(
            resolved_root,
            artifact.path,
            label=f"v2 carry source artifact {artifact.path!r}",
        )
        artifact_bytes = artifact_path.read_bytes()
        if sha256_bytes(artifact_bytes) != artifact.sha256:
            raise Calibration003Error(
                f"v2 carry source artifact digest changed: {artifact.path}"
            )
        anchored_evidence_refs.add(artifact.sha256)
        anchored_evidence_refs.update(
            _typed_source_evidence_refs(artifact_bytes, source_path=artifact_path)
        )
    for fact in packet.facts:
        for evidence_ref in (*fact.evidence_refs, *fact.counterevidence_refs):
            if evidence_ref not in anchored_evidence_refs:
                raise Calibration003Error(
                    f"v2 carry fact evidence is absent from pinned sources: {fact.fact_id}"
                )
    return LoadedV2CarryPacket(
        packet=packet,
        file_sha256=file_sha256,
        source_path=source,
        repository_root=resolved_root,
    )


def _require_carry_binding(
    plan: Calibration003Plan,
    carry_packet: LoadedV2CarryPacket | None,
) -> None:
    declared = plan.v2_carry_evidence_ref
    if declared is None:
        if carry_packet is not None:
            raise Calibration003Error("fresh campaign must not receive a v2 carry packet")
        return
    if carry_packet is None:
        raise Calibration003Error("declared v2 carry evidence requires its validated packet")
    if carry_packet.file_sha256 != declared:
        raise Calibration003Error("validated v2 carry packet does not match the plan")
    current = load_v2_carry_packet(
        carry_packet.source_path,
        carry_packet.repository_root,
        expected_ref=declared,
    )
    if current.packet != carry_packet.packet:
        raise Calibration003Error("validated v2 carry packet changed after loading")


def _arm_manifest(plan: Calibration003Plan, role: CampaignArmRole) -> LabManifest:
    policy_refs = [plan.pal23_profile_ref]
    capability_refs = [plan.kernel_artifact_ref]
    if role is CampaignArmRole.SCRIBE:
        policy_refs.append(plan.scribe_policy.digest)
        capability_refs.append(plan.scribe_driver.digest)
    return LabManifest(
        lab_id=f"{plan.campaign_id}.{role.value}",
        lab_version="3-preparation",
        purpose=(
            f"Preparation-only zero-state {role.value} arm for a matched Strongwiz v3 "
            "campaign; no environment access or action port."
        ),
        strongwiz_version=plan.strongwiz_version,
        kernel_artifact_ref=plan.kernel_artifact_ref,
        contract_schema="strongwiz.contract.v1",
        capability_refs=tuple(sorted(set(capability_refs))),
        policy_refs=tuple(sorted(set(policy_refs))),
        source_identity_refs=plan.source_identity_refs,
    )


def _arm_run_spec(
    plan: Calibration003Plan,
    role: CampaignArmRole,
    manifest: LabManifest,
) -> RunSpec:
    policy_refs = [plan.pal23_profile_ref]
    if role is CampaignArmRole.SCRIBE:
        policy_refs.append(plan.scribe_policy.digest)
    return RunSpec(
        run_id=f"{plan.campaign_id}.{role.value}.preparation",
        lab_manifest_ref=manifest.digest,
        objective=plan.objective,
        success_condition=plan.success_condition,
        success_state=plan.success_state,
        terminal_authority_source=plan.domain.terminal_authority_source,
        evaluation_class=plan.evaluation_class,
        frozen_runtime_ref=plan.frozen_runtime_ref,
        model_driver_id=plan.operator.operator_id,
        model_driver_version=plan.operator.operator_version,
        model_driver_artifact_ref=plan.operator.operator_artifact_ref,
        domain_adapter_id=plan.domain.adapter_id,
        domain_adapter_version=plan.domain.adapter_version,
        domain_adapter_artifact_ref=plan.domain.adapter_artifact_ref,
        seed=plan.seed,
        resource_budget=plan.resource_budget,
        allowed_action_names=(),
        # A carry packet is named only in the external campaign index after
        # both genesis seals exist. Importing it is a later target-bound event,
        # never part of zero-state lab creation.
        declared_input_refs=(),
        policy_refs=tuple(sorted(set(policy_refs))),
        prior_run_refs=(),
        prior_domain_state_refs=(),
        execution_grant_ref=None,
        shadow_only=True,
    )


def _ledger_seal_ref(verification: LabVerification) -> str:
    return content_hash(verification.ledger_seal)


def _domain_seal_ref(verification: LabVerification) -> str:
    return content_hash(verification.domain_state_seal)


def _initialize_or_resume_arm(
    lab_root: Path,
    *,
    manifest: LabManifest,
    run_spec: RunSpec,
) -> LabVerification:
    if is_link_like(lab_root):
        raise Calibration003Error("campaign arm root must not be link-like")
    if lab_root.exists() and not lab_root.is_dir():
        raise Calibration003Error("campaign arm root must be a directory")
    if lab_root.exists() and any(lab_root.iterdir()):
        verified = verify_lab_genesis(lab_root)
        found_manifest, found_spec = _load_arm_contracts(lab_root)
        if found_manifest != manifest or found_spec != run_spec:
            raise Calibration003Error("resumed campaign arm differs from the matched plan")
        return verified
    initialize_lab(lab_root, manifest=manifest, run_spec=run_spec)
    return verify_lab_genesis(lab_root)


def prepare_campaign(
    root: str | Path,
    plan: Calibration003Plan,
    *,
    carry_packet: LoadedV2CarryPacket | None = None,
) -> CampaignIndex:
    """Create two physically distinct labs and seal both genesis states first."""

    _require_carry_binding(plan, carry_packet)
    marker = CampaignPreparationMarker(
        campaign_id=plan.campaign_id,
        plan_ref=plan.digest,
        carry_evidence_ref=plan.v2_carry_evidence_ref,
    )
    campaign_root = _campaign_root_for_preparation(Path(root), marker)
    allowed_root_entries = {
        CAMPAIGN_INDEX_PATH,
        CAMPAIGN_PREPARATION_MARKER_PATH,
        "arms",
    }
    unexpected_root_entries = {
        item.name for item in campaign_root.iterdir()
    } - allowed_root_entries
    if unexpected_root_entries:
        raise Calibration003Error("campaign root contains unexpected preparation data")
    arms_root = campaign_root / "arms"
    if is_link_like(arms_root):
        raise Calibration003Error("campaign arms root must not be link-like")
    if arms_root.exists() and not arms_root.is_dir():
        raise Calibration003Error("campaign arms root must be a directory")
    arms_root.mkdir(exist_ok=True)
    unexpected_arms = {item.name for item in arms_root.iterdir()} - {
        CampaignArmRole.NO_SCRIBE.value,
        CampaignArmRole.SCRIBE.value,
    }
    if unexpected_arms:
        raise Calibration003Error("campaign arms root contains an unexpected entry")
    indexed_arms: list[CampaignArmIndex] = []
    for role in (CampaignArmRole.NO_SCRIBE, CampaignArmRole.SCRIBE):
        lab_root = arms_root / role.value
        manifest = _arm_manifest(plan, role)
        run_spec = _arm_run_spec(plan, role, manifest)
        verified = _initialize_or_resume_arm(
            lab_root,
            manifest=manifest,
            run_spec=run_spec,
        )
        indexed_arms.append(
            CampaignArmIndex(
                arm_role=role,
                relative_lab_root=f"arms/{role.value}",
                lab_manifest_ref=manifest.digest,
                run_spec_ref=run_spec.digest,
                genesis_seal_ref=verified.genesis_ref,
                ledger_zero_state_seal_ref=_ledger_seal_ref(verified),
                domain_zero_state_seal_ref=_domain_seal_ref(verified),
            )
        )
    if indexed_arms[0].genesis_seal_ref == indexed_arms[1].genesis_seal_ref:
        raise Calibration003Error("matched arms unexpectedly share one genesis identity")
    index = CampaignIndex(
        campaign_id=plan.campaign_id,
        plan_ref=plan.digest,
        claim_label=plan.claim_label,
        carry_evidence_ref=plan.v2_carry_evidence_ref,
        arms=(indexed_arms[0], indexed_arms[1]),
    )
    _immutable_write(campaign_root / CAMPAIGN_INDEX_PATH, canonical_bytes(index))
    verify_campaign(campaign_root, plan, carry_packet=carry_packet)
    return index


def _read_index(root: Path) -> CampaignIndex:
    path = root / CAMPAIGN_INDEX_PATH
    if is_link_like(path) or not path.is_file():
        raise Calibration003Error("campaign index is missing or link-like")
    raw = path.read_bytes()
    try:
        index = CampaignIndex.model_validate_json(raw)
    except ValueError as error:
        raise Calibration003Error(f"invalid campaign index: {error}") from error
    if canonical_bytes(index) != raw:
        raise Calibration003Error("campaign index is not exact canonical JSON")
    return index


def _read_marker(root: Path) -> CampaignPreparationMarker:
    path = root / CAMPAIGN_PREPARATION_MARKER_PATH
    if is_link_like(path) or not path.is_file():
        raise Calibration003Error("campaign preparation marker is missing or link-like")
    raw = path.read_bytes()
    try:
        marker = CampaignPreparationMarker.model_validate_json(raw)
    except ValueError as error:
        raise Calibration003Error(f"invalid campaign preparation marker: {error}") from error
    if canonical_bytes(marker) != raw:
        raise Calibration003Error("campaign preparation marker is not exact canonical JSON")
    return marker


def _load_arm_contracts(root: Path) -> tuple[LabManifest, RunSpec]:
    try:
        manifest = LabManifest.model_validate_json((root / "lab.manifest.json").read_bytes())
        spec = RunSpec.model_validate_json((root / "run.spec.json").read_bytes())
    except (OSError, ValueError) as error:
        raise Calibration003Error(f"invalid arm control contracts: {error}") from error
    return manifest, spec


def verify_campaign(
    root: str | Path,
    plan: Calibration003Plan,
    *,
    carry_packet: LoadedV2CarryPacket | None = None,
) -> CampaignVerification:
    """Verify matched identities and require both labs still be exact zero-state."""

    _require_carry_binding(plan, carry_packet)
    campaign_root = Path(root).resolve(strict=True)
    if is_link_like(campaign_root) or not campaign_root.is_dir():
        raise Calibration003Error("campaign root must be an ordinary directory")
    marker = _read_marker(campaign_root)
    expected_marker = CampaignPreparationMarker(
        campaign_id=plan.campaign_id,
        plan_ref=plan.digest,
        carry_evidence_ref=plan.v2_carry_evidence_ref,
    )
    if marker != expected_marker:
        raise Calibration003Error("campaign preparation marker does not bind the supplied plan")
    index = _read_index(campaign_root)
    if (
        index.campaign_id != plan.campaign_id
        or index.plan_ref != plan.digest
        or index.claim_label is not plan.claim_label
        or index.carry_evidence_ref != plan.v2_carry_evidence_ref
    ):
        raise Calibration003Error("campaign index does not bind the supplied plan")

    roots: list[Path] = []
    manifests: list[LabManifest] = []
    specs: list[RunSpec] = []
    genesis_refs: list[str] = []
    ledger_refs: list[str] = []
    domain_refs: list[str] = []
    for arm in index.arms:
        lab_root = (campaign_root / arm.relative_lab_root).resolve(strict=True)
        try:
            lab_root.relative_to(campaign_root)
        except ValueError as error:
            raise Calibration003Error("campaign arm escaped its external index") from error
        verified = verify_lab_genesis(lab_root)
        manifest, spec = _load_arm_contracts(lab_root)
        expected_manifest = _arm_manifest(plan, arm.arm_role)
        expected_spec = _arm_run_spec(plan, arm.arm_role, expected_manifest)
        if manifest != expected_manifest or spec != expected_spec:
            raise Calibration003Error("campaign arm differs from the matched plan")
        actual = (
            manifest.digest,
            spec.digest,
            verified.genesis_ref,
            _ledger_seal_ref(verified),
            _domain_seal_ref(verified),
        )
        indexed = (
            arm.lab_manifest_ref,
            arm.run_spec_ref,
            arm.genesis_seal_ref,
            arm.ledger_zero_state_seal_ref,
            arm.domain_zero_state_seal_ref,
        )
        if actual != indexed or not verified.current_state_matches_genesis:
            raise Calibration003Error("campaign arm no longer matches its zero-state seals")
        roots.append(lab_root)
        manifests.append(manifest)
        specs.append(spec)
        genesis_refs.append(verified.genesis_ref)
        ledger_refs.append(_ledger_seal_ref(verified))
        domain_refs.append(_domain_seal_ref(verified))

    ledger_paths = [roots[i] / manifests[i].layout.ledger_path for i in range(2)]
    physically_separate_roots = roots[0] != roots[1]
    physically_separate_ledgers = ledger_paths[0] != ledger_paths[1] and not os.path.samefile(
        ledger_paths[0], ledger_paths[1]
    )
    matched_seed = specs[0].seed == specs[1].seed == plan.seed
    matched_budget = (
        specs[0].resource_budget == specs[1].resource_budget == plan.resource_budget
    )
    matched_operator = all(
        (
            item.model_driver_id,
            item.model_driver_version,
            item.model_driver_artifact_ref,
        )
        == (
            plan.operator.operator_id,
            plan.operator.operator_version,
            plan.operator.operator_artifact_ref,
        )
        for item in specs
    )
    if not all(
        (
            physically_separate_roots,
            physically_separate_ledgers,
            matched_seed,
            matched_budget,
            matched_operator,
        )
    ):
        raise Calibration003Error("campaign matched-arm invariants failed")
    if any(
        item.allowed_action_names
        or not item.shadow_only
        or item.execution_grant_ref is not None
        for item in specs
    ):
        raise Calibration003Error("preparation labs unexpectedly expose execution")

    return CampaignVerification(
        campaign_id=plan.campaign_id,
        plan_ref=plan.digest,
        index_ref=index.digest,
        arm_genesis_refs=(genesis_refs[0], genesis_refs[1]),
        arm_ledger_seal_refs=(ledger_refs[0], ledger_refs[1]),
        arm_domain_seal_refs=(domain_refs[0], domain_refs[1]),
    )


def _synthetic_projection() -> StateProjection:
    return StateProjection(
        projection_id="calibration-003-synthetic-scribe-work",
        state_space="receipt-bound derived summaries",
        included_coordinates=("payload", "source_identity", "uncertainty"),
        excluded_coordinates=(
            "action_authority",
            "domain_state",
            "private_reasoning",
            "raw_frames",
        ),
        comparator="canonical JSON bytes under the fixed Kevin Speak decoder",
        provenance_refs=(content_hash({"pal23": "SC-21 targeted synthetic preflight"}),),
    )


def _synthetic_adapter(projection: StateProjection) -> BoundaryAdapter:
    source = BoundaryRef(
        boundary_id="synthetic-derived-summary-scope",
        role=BoundaryRole.SCOPE,
        carrier_or_domain="receipt-bound concise synthetic summaries",
        scope="one deterministic synthetic preflight",
        orientation_or_coefficients_or_na="N/A: symbolic",
        resolution_or_admissible_set_or_na="closed scribe material contract",
        provenance_refs=(content_hash({"boundary": "synthetic-source"}),),
    )
    target = BoundaryRef(
        boundary_id="synthetic-representation-interface",
        role=BoundaryRole.INTERFACE,
        carrier_or_domain="Kevin Speak canonical codec",
        scope="one preflight-local workspace",
        orientation_or_coefficients_or_na="N/A: symbolic",
        resolution_or_admissible_set_or_na="exact canonical reconstruction",
        provenance_refs=(projection.digest,),
    )
    return BoundaryAdapter(
        adapter_id="calibration-003-synthetic-scribe-adapter",
        source=source,
        target=target,
        hypotheses=("supplied material is a derived synthetic summary",),
        preserved_data=("canonical payload", "evidence reference", "uncertainty"),
        lost_data=("private reasoning", "raw observation"),
        lossless=False,
        evidence_refs=(content_hash({"evidence": "synthetic-adapter-declaration"}),),
        authority_ceiling="representation recommendation only",
        reopening_condition="projection, material contract, or decoder changes",
    )


def _ingest_fixture(
    ledger: SQLiteLedger,
    session: ScribeSession,
    *,
    prefix: str,
    repeated: str,
    duplicate_payload_ordinals: tuple[int, int] | None = None,
) -> tuple[tuple[KevinSpeakEntry, ScribeEvidenceAtom], ...]:
    entries: list[tuple[KevinSpeakEntry, ScribeEvidenceAtom]] = []
    atoms: dict[int, ScribeEvidenceAtom] = {}
    for ordinal in range(8):
        duplicate_source = (
            duplicate_payload_ordinals[0]
            if duplicate_payload_ordinals is not None
            and ordinal == duplicate_payload_ordinals[1]
            else ordinal
        )
        payload = atoms.get(duplicate_source)
        if payload is None:
            payload = ScribeEvidenceAtom(
                atom_id=f"{prefix}-atom-{duplicate_source}",
                statement=f"{repeated}fixture-{duplicate_source}",
                status=ScribeEvidenceStatus.UNRESOLVED,
                uncertainty="This is one deterministic synthetic fixture.",
                goal_relevance="Tests reversible representation under a closed projection.",
                reopening_condition="A changed fixture or reconstruction failure.",
            )
            atoms[duplicate_source] = payload
        evidence_ref = ledger.put_object(
            {"fixture-evidence": f"{prefix}-{ordinal}", "payload_ref": payload.digest}
        )
        entries.append(
            (
                session.ingest(
                    ScribeMaterialInput(
                        material_id=f"{prefix}-{ordinal}",
                        ordinal=ordinal,
                        kind=ScribeMaterialKind.RESIDUAL_SUMMARY,
                        scope_id="calibration-003-synthetic",
                        payload=payload,
                        payload_ref=payload.digest,
                        projection_ref=session.work_projection.digest,
                        evidence_refs=(evidence_ref,),
                    )
                ),
                payload,
            )
        )
    return tuple(entries)


def _new_session(
    ledger: SQLiteLedger,
    *,
    suffix: str,
    proposal_function: Callable[[ScribeRequestView], ScribeDraft],
    projection: StateProjection,
    adapter: BoundaryAdapter,
    policy: ScribePolicy,
) -> tuple[KevinSpeakWorkspace, ScribeSession, CallableScribeDriver]:
    workspace = KevinSpeakWorkspace.open_blank(
        ledger,
        workspace_id=f"calibration-003-{suffix}-workspace",
        configuration=KevinSpeakConfiguration(),
    )
    driver = CallableScribeDriver(
        binding=ScribeDriverBinding(
            driver_id=f"calibration-003-{suffix}-scribe",
            driver_version="1",
            driver_artifact_ref=content_hash(
                {"fixture": "deterministic representation-only scribe", "suffix": suffix}
            ),
        ),
        proposal_function=proposal_function,
    )
    session = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id=f"calibration-003-{suffix}-session",
        driver=driver,
        policy=policy,
        boundary_adapter=adapter,
        work_projection=projection,
    )
    return workspace, session, driver


def run_synthetic_preflight(root: str | Path) -> SyntheticPreflightReceipt:
    """Exercise scribe boundaries without importing or contacting any domain."""

    preflight_root = _require_fresh_root(Path(root), label="synthetic preflight root")
    ledger_path = preflight_root / "scribe-preflight.sqlite3"
    projection = _synthetic_projection()
    adapter = _synthetic_adapter(projection)
    policy = ScribePolicy()
    repeated = "prediction residual remains open under the declared projection; " * 18
    observed_views: list[ScribeRequestView] = []

    def promote(request: ScribeRequestView) -> ScribeDraft:
        observed_views.append(request)
        payload_refs = tuple(
            sorted({item.material.payload_ref for item in request.adaptation_materials})
        )
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="PRO",
                    expansion=repeated,
                    concise_meaning="prediction residual remains open in this projection",
                    source_payload_refs=payload_refs,
                ),
            ),
            rationale="The exact long residual clause repeats in adaptation summaries.",
        )

    def residual(_request: ScribeRequestView) -> ScribeDraft:
        return ScribeDraft(
            proposals=(),
            rationale="No shorthand candidate passed the bounded synthetic review.",
            known_residuals=("no_repeated_structure_worth_encoding",),
        )

    def fail(_request: ScribeRequestView) -> ScribeDraft:
        raise RuntimeError("synthetic provider failure")

    ledger = SQLiteLedger(ledger_path)
    try:
        workspace, session, driver = _new_session(
            ledger,
            suffix="promotion",
            proposal_function=promote,
            projection=projection,
            adapter=adapter,
            policy=policy,
        )
        source_entries = _ingest_fixture(
            ledger,
            session,
            prefix="promotion-material",
            repeated=repeated,
            duplicate_payload_ordinals=(2, 3),
        )
        promoted = session.run_cycle(
            cycle_id="promotion-cycle",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        if promoted.status is not ScribeCycleStatus.PROMOTED or len(observed_views) != 1:
            raise Calibration003Error("synthetic promotion did not pass its declared gates")
        calls_before_replay = len(observed_views)
        repeated_cycle = session.run_cycle(
            cycle_id="promotion-cycle",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        if repeated_cycle != promoted or len(observed_views) != calls_before_replay:
            raise Calibration003Error(
                "identical cycle retry repeated or changed its provider call"
            )
        view = observed_views[0]
        visible = {item.material.digest for item in view.adaptation_materials}
        withheld = set(view.request.withheld_validation_material_refs)
        by_material_ref = {item.digest: item for item in session.materials}
        visible_payloads = {item.material.payload_ref for item in view.adaptation_materials}
        withheld_payloads = {by_material_ref[item].payload_ref for item in withheld}
        if (
            view.request.driver != driver.binding
            or visible & withheld
            or not withheld
            or visible_payloads & withheld_payloads
        ):
            raise Calibration003Error("synthetic request or heldout boundary failed")
        compact_payload = ScribeEvidenceAtom(
            atom_id="promotion-material-8-atom",
            statement=f"{repeated}post-promotion",
            status=ScribeEvidenceStatus.UNRESOLVED,
            uncertainty="This is one deterministic synthetic fixture.",
            goal_relevance="Tests the promoted reversible representation.",
            reopening_condition="A reconstruction failure or changed fixture.",
        )
        compact_evidence_ref = ledger.put_object(
            {"fixture-evidence": "promotion-material-8", "payload_ref": compact_payload.digest}
        )
        compact_entry = session.ingest(
            ScribeMaterialInput(
                material_id="promotion-material-8",
                ordinal=8,
                kind=ScribeMaterialKind.RESIDUAL_SUMMARY,
                scope_id="calibration-003-synthetic",
                payload=compact_payload,
                payload_ref=compact_payload.digest,
                projection_ref=projection.digest,
                evidence_refs=(compact_evidence_ref,),
            )
        )
        if canonical_bytes(workspace.decode_entry(compact_entry)) != canonical_bytes(
            compact_payload
        ):
            raise Calibration003Error("post-promotion exact reconstruction failed")
        for entry, source_payload in source_entries:
            if canonical_bytes(workspace.decode_entry(entry)) != canonical_bytes(
                source_payload
            ):
                raise Calibration003Error("synthetic source round trip changed")

        residual_workspace, residual_session, _residual_driver = _new_session(
            ledger,
            suffix="residual",
            proposal_function=residual,
            projection=projection,
            adapter=adapter,
            policy=policy,
        )
        _ingest_fixture(
            ledger,
            residual_session,
            prefix="residual-material",
            repeated="unique bounded summary ",
        )
        residual_cycle = residual_session.run_cycle(
            cycle_id="residual-cycle",
            trigger=ScribeTrigger.REASSESSMENT,
        )
        if (
            residual_cycle.status is not ScribeCycleStatus.NO_CANDIDATE
            or "no_repeated_structure_worth_encoding" not in residual_cycle.reasons
        ):
            raise Calibration003Error("synthetic residual fallback was not preserved")

        failure_workspace, failure_session, failure_driver = _new_session(
            ledger,
            suffix="failure",
            proposal_function=fail,
            projection=projection,
            adapter=adapter,
            policy=policy,
        )
        _ingest_fixture(
            ledger,
            failure_session,
            prefix="failure-material",
            repeated="bounded failure material ",
        )
        failure_cycle = failure_session.run_cycle(
            cycle_id="failure-cycle",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        if (
            failure_cycle.status is not ScribeCycleStatus.FAILED
            or failure_session.verify().pending_material_count != 8
        ):
            raise Calibration003Error("synthetic failure fallback lost pending material")

        workspace_id = workspace.workspace_id
        failure_workspace_id = failure_workspace.workspace_id
        residual_workspace_id = residual_workspace.workspace_id
    finally:
        ledger.close()

    restored_ledger = SQLiteLedger(ledger_path)
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger,
            workspace_id=workspace_id,
        )
        restored_session = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="calibration-003-promotion-session",
            driver=driver,
        )
        if restored_session.cycles[0] != promoted or len(observed_views) != 1:
            raise Calibration003Error("completed cycle did not restore without provider replay")

        restored_failure_workspace = KevinSpeakWorkspace.restore(
            restored_ledger,
            workspace_id=failure_workspace_id,
        )
        restored_failure = ScribeSession.restore(
            restored_ledger,
            workspace=restored_failure_workspace,
            session_id="calibration-003-failure-session",
            driver=failure_driver,
        )
        if (
            restored_failure.cycles != (failure_cycle,)
            or restored_failure.verify().pending_material_count != 8
        ):
            raise Calibration003Error("failure state did not restore exactly")
        restored_residual_workspace = KevinSpeakWorkspace.restore(
            restored_ledger,
            workspace_id=residual_workspace_id,
        )
        residual_verification = ScribeSession.restore(
            restored_ledger,
            workspace=restored_residual_workspace,
            session_id="calibration-003-residual-session",
            driver=_residual_driver,
        ).verify()
        promotion_verification = restored_session.verify()
        total_receipts, receipt_head = restored_ledger.verify()
        if receipt_head is None:
            raise Calibration003Error("synthetic preflight has no receipt head")
        exact = (
            promotion_verification.exact_workspace_round_trips
            and residual_verification.exact_workspace_round_trips
            and restored_failure.verify().exact_workspace_round_trips
        )
        if not exact:
            raise Calibration003Error("synthetic workspace verification lost exact round trips")
        receipt = SyntheticPreflightReceipt(
            preflight_id="calibration-003-scribe-preflight",
            driver_binding_ref=driver.binding.digest,
            scribe_policy_ref=policy.digest,
            boundary_adapter_ref=adapter.digest,
            state_projection_ref=projection.digest,
            promoted_cycle_ref=promoted.digest,
            residual_cycle_ref=residual_cycle.digest,
            failure_cycle_ref=failure_cycle.digest,
            ledger_receipt_count=total_receipts,
            ledger_receipt_head=receipt_head,
        )
    finally:
        restored_ledger.close()
    _immutable_write(
        preflight_root / SYNTHETIC_PREFLIGHT_RECEIPT_PATH,
        canonical_bytes(receipt),
    )
    return receipt
