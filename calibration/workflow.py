"""Genesis, exact Strongwiz action lifecycle, sealing, and capsule workflow."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from arcengine import GameAction, GameState

from calibration.core import (
    BudgetCounter,
    BudgetExceeded,
    CalibrationError,
    CapturedFrame,
    KnownNoEffectDenied,
    LocalControlProtocol,
    MemoizedProposalDraftBridge,
    OfficialLocalArcPort,
    RawFrameDataAdapter,
    RawTraceStore,
    SingleWriterArcExecutor,
    artifact_pointer,
    load_asset_manifest,
    verify_dependency_versions,
)
from calibration.models import (
    ArtifactPointer,
    AssessmentDraft,
    BudgetReceipt,
    CalibrationRunReceipt,
    EnvironmentCallAdmission,
    EnvironmentCallAssessmentClosure,
    EnvironmentCallCompletion,
    EnvironmentCallDenial,
    FrameEvidence,
    InitialResetAdmission,
    InitialResetCompletion,
    InterruptedRunMarker,
    PreparedRunBundle,
    ProposalDraft,
    RunTerminalRecord,
    load_preregistration,
)
from strongwiz.authority import GrantRegistry, GrantSource, TaskGrant
from strongwiz.canonical import canonical_bytes, content_hash, deep_thaw_json, parse_strict_json
from strongwiz.contracts import (
    CONTRACT_SCHEMA,
    ControlSnapshot,
    CostVector,
    Goal,
    Outcome,
    ReasoningRequest,
    RouteDisposition,
)
from strongwiz.integrity import FrozenRuntimeManifest, freeze_files, sha256_file
from strongwiz.lab import (
    CAPSULE_MANIFEST_PATH,
    LabGenesisSeal,
    LabManifest,
    RunDisposition,
    RunSeal,
    RunSpec,
    initialize_lab,
    pack_evidence,
    seal_run,
    verify_evidence_capsule,
    verify_lab,
    verify_lab_genesis,
)
from strongwiz.lab_policy import (
    ConsequentialCrossing,
    CrossingStage,
    LabBoundaryContext,
    PEAReview,
    ReleaseClaimStatus,
    ReviewStatus,
    SEEDReleaseReview,
    evaluate_lab_rules,
)
from strongwiz.ledger import SQLiteLedger
from strongwiz.orchestration import (
    ExecutionCallResult,
    ExecutionCoordinator,
    ExecutionDisposition,
)
from strongwiz.policy import CadencePolicy, CadenceSignals
from strongwiz.routing import RouterPolicy
from strongwiz.runtime import ReasoningSession, SessionPhase

PREREGISTRATION_RELATIVE = Path("docs/calibrations/001-preregistration.json")
ASSET_MANIFEST_NAME = "ls20.asset.json"
BUNDLE_RELATIVE = Path("state/domain/control/prepared-run.json")
TERMINAL_RELATIVE = Path("state/domain/terminal.record.json")
LIVE_LOCK_NAME = ".calibration-live.lock"
INITIAL_ADMISSION_RELATIVE = Path("state/domain/control/initial-reset.admission.json")
INITIAL_COMPLETION_RELATIVE = Path("state/domain/control/initial-reset.completion.json")
INTERRUPTED_RELATIVE = Path("state/domain/control/interrupted-run.json")
CALLS_RELATIVE = Path("state/domain/control/calls")


def _call_filename(invocation_id: str, suffix: str) -> str:
    return f"{content_hash({'invocation_id': invocation_id})}.{suffix}.json"


def _git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _working_tree_blob_id(repository_root: Path, relative_path: str) -> str:
    """Hash working content through Git's declared clean filters."""

    return _git(repository_root, "hash-object", f"--path={relative_path}", relative_path)


def _verify_baseline(repository_root: Path, *, commit: str, tree: str) -> None:
    try:
        _git(repository_root, "cat-file", "-e", f"{commit}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise CalibrationError("preregistered toolbelt commit object is unavailable") from error
    if _git(repository_root, "rev-parse", f"{commit}^{{tree}}") != tree:
        raise CalibrationError("preregistered toolbelt commit has a different tree")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if ancestry.returncode != 0:
        raise CalibrationError(
            "current integration does not descend from the toolbelt baseline"
        )
    baseline_paths = tuple(
        value
        for value in _git(
            repository_root,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "src/strongwiz",
        ).splitlines()
        if value
    )
    tracked_paths = tuple(
        value
        for value in _git(repository_root, "ls-files", "--", "src/strongwiz").splitlines()
        if value
    )
    untracked = _git(
        repository_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "src/strongwiz",
    )
    if not baseline_paths or baseline_paths != tracked_paths or untracked:
        raise CalibrationError("pinned kernel path set differs from the baseline commit")
    for relative in baseline_paths:
        working_path = repository_root / relative
        expected_blob = _git(repository_root, "rev-parse", f"{commit}:{relative}")
        if (
            not working_path.is_file()
            or working_path.is_symlink()
            or (_working_tree_blob_id(repository_root, relative) != expected_blob)
        ):
            raise CalibrationError(
                "pinned kernel working-tree bytes differ from baseline after Git clean "
                f"filtering: {relative}"
            )


def _source_paths(
    repository_root: Path,
    *,
    integration_packages: tuple[str, ...] = ("calibration",),
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    kernel = tuple(sorted((repository_root / "src/strongwiz").rglob("*.py")))
    py_typed = repository_root / "src/strongwiz/py.typed"
    if py_typed.exists():
        kernel = (*kernel, py_typed)
    integration = tuple(
        sorted(
            path
            for package in integration_packages
            for pattern in ("*.py", "*.json")
            for path in (repository_root / package).glob(pattern)
        )
    )
    if not kernel or not integration:
        raise CalibrationError("toolbelt or integration source set is empty")
    return kernel, integration


def _write_contract(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    if path.exists() and path.read_bytes() != payload:
        raise CalibrationError(f"immutable control object differs: {path}")
    if not path.exists():
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def _acquire_live_lock(run_root: Path, run_id: str) -> tuple[Path, bytes]:
    """Claim one live environment owner across processes before Arcade.make."""

    path = run_root.resolve(strict=True) / "state" / "domain" / "control" / LIVE_LOCK_NAME
    payload = canonical_bytes(
        {
            "pid": os.getpid(),
            "run_id": run_id,
            "schema": "strongwiz.arc-agi3-live-lock.v1",
        }
    )
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise CalibrationError("another process already owns this live run") from error
    return path, payload


def _release_live_lock(path: Path, expected_payload: bytes) -> None:
    if not path.is_file() or path.is_symlink() or path.read_bytes() != expected_payload:
        raise CalibrationError("live-run lock changed or disappeared")
    path.unlink()


def prepare_run(
    *,
    repository_root: Path,
    run_root: Path,
    assets_root: Path,
    run_id: str,
    preregistration_relative: Path = PREREGISTRATION_RELATIVE,
    package_version: str = "0.2.0",
    task_id: str = "calibration-001-official-public-ls20",
    lab_id: str = "strongwiz-arc3-calibration-001",
    lab_version: str = "1",
    lab_purpose: str = "One clean-room Codex-operated local-public ARC-AGI-3 calibration.",
    integration_packages: tuple[str, ...] = ("calibration",),
) -> PreparedRunBundle:
    """Freeze inputs, prove empty genesis, then write run controls—never make a game."""

    root = repository_root.resolve(strict=True)
    for path, label in ((run_root, "run root"), (assets_root, "asset root")):
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise CalibrationError(f"{label} must remain inside the repository") from error
    verify_dependency_versions()
    prereg = load_preregistration(root, root / preregistration_relative)
    asset_path = assets_root.resolve(strict=True) / ASSET_MANIFEST_NAME
    asset = load_asset_manifest(assets_root, asset_path)
    if asset.exact_game_id == prereg.preregistration.evaluation.game_name:
        raise CalibrationError("asset identity is not versioned")
    expected_game_id = prereg.preregistration.evaluation.exact_versioned_game_id
    if expected_game_id is not None and asset.exact_game_id != expected_game_id:
        raise CalibrationError(
            "official asset version differs from the preregistered comparison target"
        )
    _verify_baseline(
        root,
        commit=prereg.preregistration.toolbelt.commit,
        tree=prereg.preregistration.toolbelt.tree,
    )

    kernel_paths, integration_paths = _source_paths(
        root, integration_packages=integration_packages
    )
    kernel_files = freeze_files(root, kernel_paths)
    integration_files = freeze_files(root, integration_paths)
    all_files = tuple(
        sorted((*kernel_files, *integration_files), key=lambda item: item.relative_path)
    )
    toolbelt_ref = content_hash(kernel_files)
    integration_ref = content_hash(integration_files)
    dependency_path = root / "calibration/dependencies.lock.json"
    dependency_ref = sha256_file(dependency_path)

    router = RouterPolicy()
    cadence = CadencePolicy()
    bridge = MemoizedProposalDraftBridge()
    adapter = RawFrameDataAdapter()
    executor_ref = SingleWriterArcExecutor.declared_artifact_ref()
    scope_id = f"arc-game:{asset.exact_game_id}"
    goal = Goal(
        goal_id="observe-official-ls20-win",
        statement=(
            "Play the preregistered official public game until its pinned enum reports WIN."
        ),
        scope_id=scope_id,
        success_condition="The exact official post-action GameState enum is WIN.",
        abandonment_condition="A preregistered resource budget is exhausted.",
        reopening_condition="Any later raw frame conflicts with the current local model.",
    )
    limits = prereg.preregistration.budgets
    allowed_actions = tuple(sorted(action.name for action in GameAction))
    grant = TaskGrant(
        root_ref=prereg.file_sha256,
        source=GrantSource.HUMAN,
        task_id=task_id,
        goal_id=goal.goal_id,
        goal_ref=goal.digest,
        scope_id=scope_id,
        generation=0,
        issued_boundary=0,
        not_before_boundary=1,
        expires_boundary=limits.maximum_total_environment_calls,
        maximum_invocations=limits.maximum_total_environment_calls - 1,
        allowed_action_names=allowed_actions,
        executor_id=SingleWriterArcExecutor.executor_id,
        executor_version=SingleWriterArcExecutor.executor_version,
        executor_artifact_ref=executor_ref,
        output_destination_ref=content_hash({"run_id": run_id, "scope": scope_id}),
        release_review_required=False,
        maximum_attention_units=0,
    )
    policies = tuple(sorted((router.digest, cadence.digest)))
    frozen = FrozenRuntimeManifest(
        package_version=package_version,
        contract_schema=CONTRACT_SCHEMA,
        source_files=all_files,
        configuration_ref=prereg.file_sha256,
        dependency_lock_ref=dependency_ref,
        model_driver_id=bridge.driver_id,
        model_driver_version=bridge.driver_version,
        model_driver_artifact_ref=bridge.driver_artifact_ref,
        domain_adapter_id=adapter.adapter_id,
        domain_adapter_version=adapter.adapter_version,
        domain_adapter_artifact_ref=adapter.adapter_artifact_ref,
        capability_refs=(),
        policy_refs=policies,
        runtime_description=(
            f"Strongwiz {package_version} run-local ARC-AGI-3 adapter with an "
            "externally supplied "
            "memoized ProposalDraft and a single official-environment writer"
        ),
    )
    source_refs = tuple(
        sorted(
            {
                prereg.file_sha256,
                asset.digest,
                dependency_ref,
                toolbelt_ref,
                integration_ref,
            }
        )
    )
    lab_manifest = LabManifest(
        lab_id=lab_id,
        lab_version=lab_version,
        purpose=lab_purpose,
        strongwiz_version=package_version,
        kernel_artifact_ref=toolbelt_ref,
        contract_schema=CONTRACT_SCHEMA,
        policy_refs=policies,
        source_identity_refs=source_refs,
    )
    run_spec = RunSpec(
        run_id=run_id,
        lab_manifest_ref=lab_manifest.digest,
        objective=goal.statement,
        success_condition=goal.success_condition,
        success_state="WIN",
        terminal_authority_source=(
            "pinned arcengine.GameState enum projected by RawFrameDataAdapter"
        ),
        evaluation_class="local-public",
        frozen_runtime_ref=frozen.digest,
        model_driver_id=bridge.driver_id,
        model_driver_version=bridge.driver_version,
        model_driver_artifact_ref=bridge.driver_artifact_ref,
        domain_adapter_id=adapter.adapter_id,
        domain_adapter_version=adapter.adapter_version,
        domain_adapter_artifact_ref=adapter.adapter_artifact_ref,
        seed=0,
        resource_budget=CostVector(
            environment_actions=limits.maximum_total_environment_calls,
            wall_clock_ms=limits.wall_clock_seconds * 1000,
        ),
        allowed_action_names=allowed_actions,
        declared_input_refs=source_refs,
        policy_refs=policies,
        execution_grant_ref=grant.grant_ref,
        shadow_only=False,
    )
    genesis = initialize_lab(run_root, manifest=lab_manifest, run_spec=run_spec)
    verification = verify_lab_genesis(run_root)
    if verification.genesis_ref != genesis.digest:
        raise CalibrationError("zero-state genesis verification changed identity")

    bundle = PreparedRunBundle(
        run_id=run_id,
        preregistration_path=prereg.relative_path,
        preregistration_file_ref=prereg.file_sha256,
        asset_manifest_path=str(asset_path),
        asset_manifest_ref=asset.digest,
        dependency_ref=dependency_ref,
        toolbelt_ref=toolbelt_ref,
        integration_ref=integration_ref,
        model_interface_ref=bridge.driver_artifact_ref,
        domain_adapter_ref=adapter.adapter_artifact_ref,
        executor_ref=executor_ref,
        goal=goal,
        grant=grant,
        frozen_runtime=frozen,
    )
    bundle_path = run_root / BUNDLE_RELATIVE
    _write_contract(bundle_path, bundle)
    control_root = bundle_path.parent
    _write_contract(control_root / "frozen-runtime.json", frozen)
    _write_contract(control_root / "goal.json", goal)
    _write_contract(control_root / "task-grant.json", grant)
    _write_contract(control_root / "official-asset.manifest.json", asset)
    _write_contract(
        control_root / "preregistration.sha256.json",
        {
            "path": prereg.relative_path,
            "sha256": prereg.file_sha256,
        },
    )

    ledger_path = run_root / lab_manifest.layout.ledger_path
    with SQLiteLedger(ledger_path) as ledger:
        values = (bundle, frozen, goal, grant, asset)
        object_refs = tuple(
            ledger.put_object(value.model_dump(mode="json", by_alias=True)) for value in values
        )
        ledger.append(
            occurrence_id=f"{run_id}:bootstrap:prepared-inputs",
            kind="prepared_run_inputs",
            account_id=f"{run_id}:bootstrap",
            account_version=0,
            payload={
                "genesis_ref": genesis.digest,
                "prepared_bundle_ref": bundle.digest,
                "environment_constructed": False,
            },
            object_refs=object_refs,
        )
        ledger.verify()
    return bundle


def load_prepared_run(
    repository_root: Path, run_root: Path, assets_root: Path
) -> tuple[PreparedRunBundle, LabManifest, RunSpec, LabGenesisSeal]:
    root = repository_root.resolve(strict=True)
    bundle_path = run_root / BUNDLE_RELATIVE
    bundle = PreparedRunBundle.model_validate_json(bundle_path.read_bytes())
    prereg = load_preregistration(root, root / bundle.preregistration_path)
    if prereg.file_sha256 != bundle.preregistration_file_ref:
        raise CalibrationError("preregistration bytes changed after genesis")
    verify_dependency_versions()
    if sha256_file(root / "calibration/dependencies.lock.json") != bundle.dependency_ref:
        raise CalibrationError("dependency lock changed after genesis")
    from strongwiz.integrity import verify_frozen_files

    verify_frozen_files(root, bundle.frozen_runtime.source_files)
    asset = load_asset_manifest(assets_root, Path(bundle.asset_manifest_path))
    if asset.digest != bundle.asset_manifest_ref:
        raise CalibrationError("official asset manifest changed after genesis")
    expected_asset_path = assets_root.resolve(strict=True) / ASSET_MANIFEST_NAME
    if Path(bundle.asset_manifest_path).resolve(strict=True) != expected_asset_path:
        raise CalibrationError("prepared run was pointed at a different asset root")
    lab_manifest = LabManifest.model_validate_json(
        (run_root / "lab.manifest.json").read_bytes()
    )
    run_spec = RunSpec.model_validate_json((run_root / "run.spec.json").read_bytes())
    genesis = LabGenesisSeal.model_validate_json((run_root / "lab.genesis.json").read_bytes())
    if (
        run_spec.frozen_runtime_ref != bundle.frozen_runtime.digest
        or run_spec.execution_grant_ref != bundle.grant.grant_ref
        or genesis.lab_manifest_ref != lab_manifest.digest
        or genesis.run_spec_ref != run_spec.digest
    ):
        raise CalibrationError("prepared run bindings do not close")
    verify_lab(run_root)
    return bundle, lab_manifest, run_spec, genesis


def _verify_pre_environment_state(
    run_root: Path,
    lab_manifest: LabManifest,
    bundle: PreparedRunBundle,
) -> None:
    """Require exactly the post-genesis prepared controls and no run history."""

    expected_files = {
        "control/frozen-runtime.json",
        "control/goal.json",
        "control/official-asset.manifest.json",
        "control/prepared-run.json",
        "control/preregistration.sha256.json",
        "control/task-grant.json",
    }
    domain_root = run_root / lab_manifest.layout.domain_state_path
    actual_files = {
        path.relative_to(domain_root).as_posix()
        for path in domain_root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise CalibrationError("pre-environment domain state contains unexpected run data")
    ledger_path = run_root / lab_manifest.layout.ledger_path
    with SQLiteLedger(ledger_path) as ledger:
        ledger.verify()
        receipts = tuple(ledger.receipts())
        if len(receipts) != 1 or receipts[0].kind != "prepared_run_inputs":
            raise CalibrationError(
                "pre-environment ledger is not the single prepared-input receipt"
            )
        payload = ledger.get_payload(receipts[0].payload_hash)
        if not isinstance(payload, dict) or payload != {
            "environment_constructed": False,
            "genesis_ref": LabGenesisSeal.model_validate_json(
                (run_root / "lab.genesis.json").read_bytes()
            ).digest,
            "prepared_bundle_ref": bundle.digest,
        }:
            raise CalibrationError(
                "prepared-input receipt does not prove the expected boundary"
            )


def _append_typed_receipt(
    ledger: SQLiteLedger,
    *,
    occurrence_id: str,
    kind: str,
    account_id: str,
    value: object,
    payload: dict[str, object],
) -> str:
    if not hasattr(value, "model_dump"):
        raise CalibrationError("durable boundary evidence must be a typed contract")
    dumped = value.model_dump(mode="json", by_alias=True)
    object_ref = ledger.put_object(dumped)
    envelope = ledger.append(
        occurrence_id=occurrence_id,
        kind=kind,
        account_id=account_id,
        account_version=0,
        payload=payload,
        object_refs=(object_ref,),
    )
    ledger.verify()
    return envelope.receipt_id


def _persist_initial_admission(
    run_root: Path,
    ledger: SQLiteLedger,
    admission: InitialResetAdmission,
) -> str:
    _write_contract(run_root / INITIAL_ADMISSION_RELATIVE, admission)
    return _append_typed_receipt(
        ledger,
        occurrence_id=f"{admission.run_id}:initial-reset:admitted",
        kind="initial_reset_admitted_unclosed",
        account_id=f"{admission.run_id}:environment",
        value=admission,
        payload={
            "admission_ref": admission.digest,
            "call_index": 1,
            "effect": admission.effect,
        },
    )


def _persist_initial_completion(
    run_root: Path,
    ledger: SQLiteLedger,
    completion: InitialResetCompletion,
) -> str:
    _write_contract(run_root / INITIAL_COMPLETION_RELATIVE, completion)
    return _append_typed_receipt(
        ledger,
        occurrence_id=f"{completion.run_id}:initial-reset:completed",
        kind="initial_reset_completed",
        account_id=f"{completion.run_id}:environment",
        value=completion,
        payload={
            "admission_ref": completion.admission_ref,
            "completion_ref": completion.digest,
            "raw_frame_ref": completion.frame.raw_ref,
        },
    )


def _latest_frame_from_trace(domain_root: Path) -> FrameEvidence | None:
    trace_path = domain_root / "raw-trace.jsonl"
    if not trace_path.is_file():
        return None
    lines = tuple(line for line in trace_path.read_bytes().splitlines() if line)
    if not lines:
        return None
    value = deep_thaw_json(parse_strict_json(lines[-1]))
    return FrameEvidence.model_validate(value)


def _interrupted_budget(
    run_root: Path,
    admission: InitialResetAdmission,
) -> BudgetReceipt:
    call_admissions = tuple(
        EnvironmentCallAdmission.model_validate_json(path.read_bytes())
        for path in sorted((run_root / CALLS_RELATIVE).glob("*.admission.json"))
        if not path.with_name(path.name.replace(".admission.json", ".denial.json")).exists()
    )
    base = admission.budget_after_reservation
    resets = base.resets + sum(item.action_name == "RESET" for item in call_admissions)
    non_resets = base.non_reset_actions + sum(
        item.action_name != "RESET" for item in call_admissions
    )
    return BudgetReceipt(
        maximum_non_reset_actions=base.maximum_non_reset_actions,
        maximum_resets=base.maximum_resets,
        maximum_total_environment_calls=base.maximum_total_environment_calls,
        wall_clock_seconds=base.wall_clock_seconds,
        non_reset_actions=non_resets,
        resets=resets,
        total_environment_calls=base.total_environment_calls + len(call_admissions),
        elapsed_wall_ms=max(
            (
                base.elapsed_wall_ms,
                *(item.budget_before_reservation.elapsed_wall_ms for item in call_admissions),
            )
        ),
    )


def _recording_artifacts(domain_root: Path) -> tuple[ArtifactPointer, ...]:
    recordings_root = domain_root / "official-recordings"
    if not recordings_root.is_dir():
        return ()
    return tuple(
        artifact_pointer(path)
        for path in sorted(recordings_root.rglob("*.jsonl"))
        if path.is_file()
    )


def _terminalize_interrupted_run(
    *,
    run_root: Path,
    bundle: PreparedRunBundle,
    genesis: LabGenesisSeal,
    ledger: SQLiteLedger,
    failure_stage: str,
    error_class: str,
    latest_frame: FrameEvidence | None = None,
    budget: BudgetReceipt | None = None,
) -> RunTerminalRecord:
    admission = InitialResetAdmission.model_validate_json(
        (run_root / INITIAL_ADMISSION_RELATIVE).read_bytes()
    )
    retained_budget = _interrupted_budget(run_root, admission) if budget is None else budget
    domain_root = run_root / "state/domain"
    traced_frame = _latest_frame_from_trace(domain_root)
    frame = latest_frame
    if traced_frame is not None and (
        frame is None or traced_frame.call_index >= frame.call_index
    ):
        frame = traced_frame
    summary = (
        "an admitted environment boundary did not reach a fully closed terminal run; "
        "retry is forbidden because the external effect is not safely replayable"
    )
    marker = InterruptedRunMarker(
        run_id=bundle.run_id,
        initial_reset_admission_ref=admission.digest,
        latest_frame=frame,
        budget=retained_budget,
        failure_stage=failure_stage,
        error_class=error_class,
        concise_summary=summary,
    )
    _write_contract(run_root / INTERRUPTED_RELATIVE, marker)
    marker_receipt_ref = _append_typed_receipt(
        ledger,
        occurrence_id=f"{bundle.run_id}:interrupted-run",
        kind="interrupted_run_unknown_effect",
        account_id=f"{bundle.run_id}:terminal",
        value=marker,
        payload={
            "effect_status": "UNKNOWN_EFFECT",
            "marker_ref": marker.digest,
            "retry_permitted": False,
        },
    )
    trace_path = domain_root / "raw-trace.jsonl"
    recordings = _recording_artifacts(domain_root)
    record = RunTerminalRecord(
        run_id=bundle.run_id,
        game_id=admission.game_id,
        asset_manifest_ref=bundle.asset_manifest_ref,
        final_state="UNKNOWN_EFFECT",
        levels_completed=0 if frame is None else frame.levels_completed,
        win_levels=0 if frame is None else frame.win_levels,
        budget=retained_budget,
        frozen_runtime_ref=bundle.frozen_runtime.digest,
        toolbelt_ref=bundle.toolbelt_ref,
        integration_ref=bundle.integration_ref,
        dependency_ref=bundle.dependency_ref,
        model_interface_ref=bundle.model_interface_ref,
        domain_adapter_ref=bundle.domain_adapter_ref,
        executor_ref=bundle.executor_ref,
        lab_genesis_ref=genesis.digest,
        latest_checkpoint_ref=marker_receipt_ref,
        initial_reset_admission_ref=admission.digest,
        terminal_frame=None,
        raw_trace=artifact_pointer(trace_path) if trace_path.is_file() else None,
        official_recordings=recordings,
        completion_genuinely_observed=False,
        disposition=RunDisposition.FAILED_INFRASTRUCTURE.value,
        concise_result_summary=summary,
        claim_class="local-public-codex-operated-strongwiz-calibration",
        claim_exclusions=(
            "not a competition entry or Kaggle result",
            "not a private or official evaluation score",
            "not an autonomous-offline or generalization claim",
            "does not establish AGI, consciousness, PAL, or a general theory",
        ),
        incidents=(
            f"UNKNOWN_EFFECT:{failure_stage}:{error_class}",
            "no automatic retry was attempted",
        ),
        unresolved_burdens=(
            "the admitted environment effect could not be fully closed and assessed",
        ),
    )
    _write_contract(run_root / TERMINAL_RELATIVE, record)
    _append_typed_receipt(
        ledger,
        occurrence_id=f"{bundle.run_id}:terminal-record",
        kind="calibration_terminal_record",
        account_id=f"{bundle.run_id}:terminal",
        value=record,
        payload={"summary": summary, "terminal_record_ref": record.digest},
    )
    return record


class CalibrationHarness:
    """In-process state machine retained by the local framed control server."""

    def __init__(
        self,
        *,
        repository_root: Path,
        run_root: Path,
        assets_root: Path,
    ) -> None:
        bundle, lab_manifest, run_spec, genesis = load_prepared_run(
            repository_root, run_root, assets_root
        )
        if (run_root / lab_manifest.layout.run_seal_path).exists():
            raise CalibrationError("sealed run cannot be reopened")
        if (run_root / TERMINAL_RELATIVE).exists():
            raise CalibrationError("terminal run cannot be reopened")
        if (run_root / INITIAL_ADMISSION_RELATIVE).exists():
            raise CalibrationError(
                "initial reset was already admitted; retry is forbidden and the run must seal"
            )
        _verify_pre_environment_state(run_root, lab_manifest, bundle)
        prereg = load_preregistration(
            repository_root.resolve(strict=True),
            repository_root / bundle.preregistration_path,
        ).preregistration
        asset = load_asset_manifest(assets_root, Path(bundle.asset_manifest_path))

        resolved_run = run_root.resolve(strict=True)
        domain_root = (run_root / lab_manifest.layout.domain_state_path).resolve(strict=True)
        live_lock_path, live_lock_payload = _acquire_live_lock(run_root, bundle.run_id)
        budget = BudgetCounter(prereg.budgets)
        budget.start_wall_clock()
        ledger = SQLiteLedger(run_root / lab_manifest.layout.ledger_path)
        trace = RawTraceStore(domain_root)
        initial_index = budget.reserve_initial_reset()
        admission = InitialResetAdmission(
            run_id=bundle.run_id,
            game_id=asset.exact_game_id,
            asset_manifest_ref=asset.digest,
            lab_genesis_ref=genesis.digest,
            call_index=initial_index,
            budget_after_reservation=budget.receipt(),
        )
        try:
            initial_receipt_ref = _persist_initial_admission(run_root, ledger, admission)
        except Exception:
            ledger.close()
            if not (run_root / INITIAL_ADMISSION_RELATIVE).exists():
                _release_live_lock(live_lock_path, live_lock_payload)
            raise

        # This is deliberately the first Arcade.make boundary. Genesis was loaded and
        # cross-checked above, and the prepared-input receipt says no environment existed.
        recordings_root = domain_root / "official-recordings"
        initial: CapturedFrame | None = None
        try:
            port = OfficialLocalArcPort.open(
                assets_root=assets_root,
                manifest=asset,
                recordings_root=recordings_root,
                seed=run_spec.seed,
            )
            initial = trace.capture(
                port.initial_frame,
                occurrence_id="initial-reset",
                call_index=initial_index,
            )
            completion = InitialResetCompletion(
                run_id=bundle.run_id,
                admission_ref=admission.digest,
                frame=initial.evidence,
                budget=budget.receipt(),
            )
            _persist_initial_completion(run_root, ledger, completion)
            bridge = MemoizedProposalDraftBridge()
            adapter = RawFrameDataAdapter()
            if (
                bridge.driver_artifact_ref != bundle.model_interface_ref
                or adapter.adapter_artifact_ref != bundle.domain_adapter_ref
            ):
                raise CalibrationError("live adapter identity differs from frozen runtime")
            executor = SingleWriterArcExecutor(
                port,
                budget,
                trace,
                initial=initial,
            )
            if executor.executor_artifact_ref != bundle.executor_ref:
                raise CalibrationError("live executor identity differs from frozen runtime")
        except Exception as error:
            try:
                _terminalize_interrupted_run(
                    run_root=resolved_run,
                    bundle=bundle,
                    genesis=genesis,
                    ledger=ledger,
                    failure_stage="initial_reset_make_or_setup",
                    error_class=type(error).__name__,
                    latest_frame=None if initial is None else initial.evidence,
                    budget=budget.receipt(),
                )
            finally:
                ledger.close()
            raise CalibrationError(
                "initial reset boundary failed after durable admission; retry is forbidden"
            ) from error
        self.bundle = bundle
        self.lab_manifest = lab_manifest
        self.run_spec = run_spec
        self.genesis = genesis
        self.run_root = resolved_run
        self._live_lock_path = live_lock_path
        self._live_lock_payload = live_lock_payload
        self._closed = False
        self.domain_root = domain_root
        self._initial_admission = admission
        self._initial_admission_receipt_ref = initial_receipt_ref
        self.bridge = bridge
        self.adapter = adapter
        self.trace = trace
        self.budget = budget
        self.executor = executor
        self.ledger = ledger
        self.grants = GrantRegistry()
        self.grants.activate(bundle.grant)
        self.coordinator = ExecutionCoordinator(self.grants, self.executor)
        self.session = ReasoningSession(
            session_id=f"{bundle.run_id}-session",
            model_driver=self.bridge,
            domain_adapter=self.adapter,
            governing_goal_ref=bundle.goal.digest,
            frozen_runtime=bundle.frozen_runtime,
            ledger=self.ledger,
            account_id=f"{bundle.run_id}-session",
        )
        self._request: ReasoningRequest | None = None
        self._pending_execution: ExecutionCallResult | None = None
        self._pending_proposal_ref: str | None = None
        self._latest_checkpoint_ref: str | None = None
        self._terminal_record: RunTerminalRecord | None = None
        self._unknown_effect = False
        self._post_effect_persistence_failure: str | None = None
        self._control_occurrence = 0
        self._pending_call_admission: EnvironmentCallAdmission | None = None
        self._pending_call_completion: EnvironmentCallCompletion | None = None
        try:
            self._prepare_request()
        except Exception as error:
            try:
                _terminalize_interrupted_run(
                    run_root=resolved_run,
                    bundle=bundle,
                    genesis=genesis,
                    ledger=ledger,
                    failure_stage="post_initial_session_setup",
                    error_class=type(error).__name__,
                    latest_frame=initial.evidence,
                    budget=budget.receipt(),
                )
            finally:
                ledger.close()
            raise CalibrationError(
                "session setup failed after the admitted initial reset; retry is forbidden"
            ) from error

    @property
    def current_state(self) -> GameState:
        return self.executor.current.raw.state

    @property
    def unknown_effect(self) -> bool:
        return self._unknown_effect

    def _prepare_request(self) -> None:
        self.budget.ensure_time_remaining()
        observation = self.adapter.normalize_observation(self.executor.current)
        request = ReasoningRequest(
            observation=observation,
            governing_goal=self.bundle.goal,
            scoped_goal=self.bundle.goal,
            retained_fact_refs=(self.executor.current.evidence.raw_ref,),
        )
        self.session.scan(request)
        checkpoint = self.session.checkpoint(kind="operator_waiting_for_proposal")
        if checkpoint is None:
            raise CalibrationError("durable proposal checkpoint was not written")
        self._latest_checkpoint_ref = checkpoint
        self._request = request

    def status(self) -> dict[str, object]:
        frame = self.executor.current.evidence
        if self._terminal_record is not None:
            expected = "sealed_or_capsule"
        elif self._pending_execution is not None:
            expected = "assessment_draft"
        else:
            expected = "proposal_draft"
        revision: dict[str, object] | None = None
        if self._request is not None and self._pending_execution is None:
            attempt, predecessor = self.bridge.expected_revision(self._request)
            revision = {
                "proposal_attempt": attempt,
                "supersedes_proposal_ref": predecessor,
            }
        return {
            "budget": self.budget.receipt().model_dump(mode="json", by_alias=True),
            "completion_genuinely_observed": (
                self.session.receipt().completion_genuinely_observed
            ),
            "expected_next": expected,
            "frame": frame.model_dump(mode="json", by_alias=True),
            "image_paths": [
                str(self.domain_root / value) for value in frame.image_relative_paths
            ],
            "latest_checkpoint_ref": self._latest_checkpoint_ref,
            "phase": self.session.phase.value,
            "post_effect_persistence_failure": self._post_effect_persistence_failure,
            "proposal_ref": self._pending_proposal_ref,
            "proposal_revision": revision,
            "raw_path": str(self.domain_root / frame.raw_relative_path),
            "request": None
            if self._request is None
            else self._request.model_dump(mode="json", by_alias=True),
            "request_ref": None if self._request is None else self._request.digest,
            "run_id": self.bundle.run_id,
            "state": self.current_state.value,
        }

    def _record_control(
        self, kind: str, values: tuple[object, ...], payload: dict[str, object]
    ) -> str:
        refs: list[str] = []
        for value in values:
            if not hasattr(value, "model_dump"):
                raise CalibrationError("control evidence must be a typed contract")
            dumped = value.model_dump(mode="json", by_alias=True)
            refs.append(self.ledger.put_object(dumped))
        self._control_occurrence += 1
        envelope = self.ledger.append(
            occurrence_id=(
                f"{self.bundle.run_id}:control:{self._control_occurrence:08d}:{kind}"
            ),
            kind=kind,
            account_id=f"{self.bundle.run_id}:control",
            account_version=0,
            payload=payload,
            object_refs=tuple(dict.fromkeys(refs)),
        )
        return envelope.receipt_id

    def _call_path(self, invocation_id: str, suffix: str) -> Path:
        return self.run_root / CALLS_RELATIVE / _call_filename(invocation_id, suffix)

    def _require_local_provenance(
        self,
        refs: tuple[str, ...],
        *,
        aperture: tuple[str, ...],
        label: str,
    ) -> None:
        allowed_aperture = set(aperture)
        for value in refs:
            if value in allowed_aperture or self.ledger.has_object(value):
                continue
            raise CalibrationError(f"{label} reference is not run-local evidence")

    def act(self, draft: ProposalDraft) -> dict[str, object]:
        if self._terminal_record is not None:
            raise CalibrationError("terminal run cannot act")
        if self._pending_execution is not None:
            raise CalibrationError("previous action requires assessment")
        request = self._request
        if request is None or self.session.phase is not SessionPhase.READY_TO_ACT:
            raise CalibrationError("action requires one scanned request")
        evidence_aperture = (self.executor.current.evidence.raw_ref,)
        self._require_local_provenance(
            draft.hypothesis_refs,
            aperture=evidence_aperture,
            label="proposal hypothesis",
        )
        proposal = self.bridge.supply(
            request,
            draft,
            available_evidence_refs=evidence_aperture,
        )
        # Known budget ceilings are resolved before Strongwiz admits an action.
        self.budget.preflight(proposal.action.name)
        context = LabBoundaryContext(
            grant_ref=self.bundle.grant.grant_ref,
            task_id=self.bundle.grant.task_id,
            goal_id=self.bundle.goal.goal_id,
            goal_ref=self.bundle.goal.digest,
            scope_id=self.bundle.goal.scope_id,
            observation_id=proposal.observation_id,
            observation_ref=proposal.observation_ref,
            proposal_ref=proposal.digest,
            action_ref=proposal.action.digest,
            output_destination_ref=self.bundle.grant.output_destination_ref,
            attention_budget=0,
        )
        pea = PEAReview(
            boundary_context_ref=context.digest,
            external_grant_ref=self.bundle.grant.grant_ref,
            consent=ReviewStatus.SUPPLIED,
            standing=ReviewStatus.SUPPLIED,
            privacy=ReviewStatus.NOT_APPLICABLE,
            reversibility=ReviewStatus.SUPPLIED
            if proposal.reversible
            else ReviewStatus.NOT_APPLICABLE,
            remedy=ReviewStatus.SUPPLIED,
            contestability=ReviewStatus.NOT_APPLICABLE,
            refusal=ReviewStatus.SUPPLIED,
            human_responsibility_ref=self.bundle.preregistration_file_ref,
        )
        crossing = ConsequentialCrossing(
            boundary_context_ref=context.digest,
            subject_ref=proposal.action.digest,
            description_ref=proposal.digest,
            recommendation_ref=proposal.digest,
            permission_ref=self.bundle.grant.grant_ref,
            authorization_ref=self.bundle.preregistration_file_ref,
            current_stage=CrossingStage.AUTHORIZATION,
            externally_supplied_authorization=True,
        )
        seed = SEEDReleaseReview(
            boundary_context_ref=context.digest,
            output_ref=content_hash({"environment_action": proposal.action.digest}),
            chosen_goal_id=self.bundle.goal.goal_id,
            chosen_goal_ref=self.bundle.goal.digest,
            claim_status=ReleaseClaimStatus.OBSERVATION,
            claim_ceiling="one proposed environment action; no public claim released",
            uncertainty_status=ReviewStatus.SUPPLIED,
            uncertainty_notes=("proposal remains falsifiable by the next raw frame",),
            authority_status=ReviewStatus.SUPPLIED,
            authority_limits=("exact preregistered public-game action grant only",),
            privacy_status=ReviewStatus.NOT_APPLICABLE,
            privacy_notes=(),
            correction_path="assess the exact returned frame and revise only implicated claims",
            reopening_condition="a returned consequence conflicts with the proposal",
            natural_stop="stop at WIN or a preregistered budget boundary",
            attention_units_requested=0,
            preserves_user_agency=True,
        )
        lab_decision = evaluate_lab_rules(
            context=context,
            pea_review=pea,
            crossing=crossing,
            seed_release=seed,
            external_effect_requested=True,
            release_requested=False,
        )
        binding = lab_decision.external_effect_binding
        if binding is None or not lab_decision.clears_requested_boundaries:
            raise CalibrationError("PEA/PECAN/SEED control did not clear this exact action")
        control = ControlSnapshot(
            account_id=f"{self.bundle.run_id}-session",
            account_version=len(self.session.receipt().decisions),
            observation_id=request.observation.observation_id,
            observation_ref=request.observation.digest,
            scope_id=request.observation.scope_id,
            active_goal_ids=(self.bundle.goal.goal_id,),
            active_goal_refs=(self.bundle.goal.digest,),
            available_evidence_refs=evidence_aperture,
            allowed_action_names=request.observation.available_action_names,
            remaining_budget=self.budget.remaining_costs(),
            lab_boundary=binding,
            execution_grant_ref=self.bundle.grant.grant_ref,
            serial_token=content_hash(
                {"proposal": proposal.digest, "call": self.budget.total_environment_calls + 1}
            ),
            shadow_only=False,
        )
        decision = self.session.decide(
            control,
            cadence_signals=CadenceSignals(
                startup_uncertainty=self.budget.non_reset_actions == 0,
                meaningful_contradiction=self.current_state is GameState.GAME_OVER,
            ),
            credible_plan_supported=draft.credible_plan_supported,
            uncertainty_blocks_progress=draft.uncertainty_blocks_progress,
        )
        if decision.selected_proposal_ref is None:
            recovery_receipt_ref = self._record_control(
                "proposal_revision_requested",
                (draft, proposal, decision),
                {
                    "effect": decision.route.effect,
                    "proposal_attempt": draft.proposal_attempt,
                    "proposal_draft_ref": draft.digest,
                    "proposal_ref": proposal.digest,
                    "request_ref": request.digest,
                    "route_ref": decision.route.digest,
                },
            )
            next_attempt, supersedes_proposal_ref = self.bridge.release_unexecuted_for_revision(
                request,
                proposal_ref=proposal.digest,
                route=decision.route,
            )
            return {
                "action_admitted": False,
                "assessment_required": False,
                "effect": decision.route.effect,
                "environment_effect_started": False,
                "expected_next": "proposal_draft",
                "next_proposal_attempt": next_attempt,
                "proposal_attempt": draft.proposal_attempt,
                "proposal_draft_ref": draft.digest,
                "proposal_ref": proposal.digest,
                "recovery_receipt_ref": recovery_receipt_ref,
                "request_ref": request.digest,
                "revision": {
                    "proposal_attempt": next_attempt,
                    "supersedes_proposal_draft_ref": draft.digest,
                    "supersedes_proposal_ref": supersedes_proposal_ref,
                },
                "route_disposition": decision.route.disposition.value,
                "route_ref": decision.route.digest,
                "state": self.current_state.value,
                "supersedes_proposal_draft_ref": draft.digest,
                "supersedes_proposal_ref": supersedes_proposal_ref,
                "terminal": False,
            }
        if decision.route.disposition not in {RouteDisposition.ADMIT, RouteDisposition.REOPEN}:
            raise CalibrationError("a selected proposal has a nonadmitting route")
        self.bridge.mark_admitted(
            request,
            proposal_ref=proposal.digest,
            route=decision.route,
        )
        self._record_control(
            "action_preflight",
            (draft, context, pea, crossing, seed, lab_decision, control),
            {
                "lab_decision_ref": lab_decision.digest,
                "proposal_ref": proposal.digest,
                "proposal_draft_ref": draft.digest,
                "route_ref": decision.route.digest,
            },
        )
        checkpoint = self.session.checkpoint(kind="action_admitted_before_execution")
        if checkpoint is None:
            raise CalibrationError("admitted action checkpoint was not written")
        self._latest_checkpoint_ref = checkpoint
        boundary = self.budget.total_environment_calls + 1
        permit, admission = self.coordinator.begin(
            proposal=proposal,
            route=decision.route,
            control=control,
            lab_decision=lab_decision,
            pea_review=pea,
            crossing=crossing,
            seed_release=seed,
            invocation_id=f"{self.bundle.run_id}-call-{boundary:05d}",
            boundary=boundary,
        )
        call_admission = EnvironmentCallAdmission(
            run_id=self.bundle.run_id,
            invocation_id=admission.invocation_id,
            call_index=boundary,
            action_name=admission.action_name,
            proposal_ref=proposal.digest,
            execution_admission_ref=admission.digest,
            budget_before_reservation=self.budget.receipt(),
        )
        self._pending_call_admission = call_admission
        _write_contract(self._call_path(admission.invocation_id, "admission"), call_admission)
        self._record_control(
            "execution_call_admitted",
            (admission, call_admission),
            {
                "call_admission_ref": call_admission.digest,
                "effect": call_admission.effect,
                "execution_admission_ref": admission.digest,
            },
        )
        execution = self.coordinator.execute_once(
            permit,
            admission,
            proposal,
            boundary=boundary,
        )
        # Assign all in-memory recovery bindings before any post-effect persistence.
        self._pending_execution = execution
        self._pending_proposal_ref = proposal.digest
        self._request = request
        if execution.attempt.disposition is not ExecutionDisposition.COMPLETED:
            budget_denial = self.executor.known_no_effect_budget_denial(admission.digest)
            effect_started = self.executor.effect_started(admission.digest)
            if (
                execution.attempt.disposition is ExecutionDisposition.BLOCKED
                or budget_denial is not None
                or effect_started is False
            ):
                category = (
                    "grant_revalidation_blocked"
                    if execution.attempt.disposition is ExecutionDisposition.BLOCKED
                    else "executor_rejected_before_effect"
                    if budget_denial is None
                    else f"budget_reserve_race:{budget_denial}"
                )
                denial = EnvironmentCallDenial(
                    run_id=self.bundle.run_id,
                    admission_ref=call_admission.digest,
                    execution_admission_ref=admission.digest,
                    execution_attempt_ref=execution.attempt.digest,
                    denial_category=category,
                )
                _write_contract(self._call_path(admission.invocation_id, "denial"), denial)
                self._record_control(
                    "execution_denied_known_no_effect",
                    (execution.admission, execution.release, execution.attempt, denial),
                    {
                        "denial_ref": denial.digest,
                        "effect_started": False,
                        "proposal_ref": proposal.digest,
                    },
                )
                self._pending_execution = None
                self._pending_proposal_ref = None
                self._pending_call_admission = None
                if budget_denial is not None:
                    raise BudgetExceeded(budget_denial)
                raise KnownNoEffectDenied(category)
            self._unknown_effect = True
            raise CalibrationError(
                f"environment effect is not assessable: {execution.attempt.disposition.value}"
            )
        completion = EnvironmentCallCompletion(
            run_id=self.bundle.run_id,
            admission_ref=call_admission.digest,
            execution_admission_ref=admission.digest,
            execution_attempt_ref=execution.attempt.digest,
            frame=self.executor.current.evidence,
            budget=self.budget.receipt(),
        )
        self._pending_call_completion = completion
        persistence_errors: list[str] = []
        try:
            _write_contract(self._call_path(admission.invocation_id, "completion"), completion)
            self._record_control(
                "execution_boundary",
                (
                    execution.admission,
                    execution.release,
                    execution.attempt,
                    completion,
                ),
                {
                    "attempt_ref": execution.attempt.digest,
                    "call_completion_ref": completion.digest,
                    "disposition": execution.attempt.disposition.value,
                    "proposal_ref": proposal.digest,
                },
            )
        except Exception as error:
            self._post_effect_persistence_failure = type(error).__name__
            persistence_errors.append(f"post_effect_persistence:{type(error).__name__}")
        outcome_value: Outcome | None = None
        try:
            outcome_value = self.adapter.extract_outcome(
                request.observation,
                proposal.action,
                self.executor.current,
            )
        except Exception as error:
            persistence_errors.append(f"outcome_projection:{type(error).__name__}")
        result: dict[str, object] = {
            "assessment_required": True,
            "frame": self.executor.current.evidence.model_dump(mode="json", by_alias=True),
            "image_paths": [
                str(self.domain_root / value)
                for value in self.executor.current.evidence.image_relative_paths
            ],
            "outcome": None
            if outcome_value is None
            else outcome_value.model_dump(mode="json", by_alias=True),
            "proposal_ref": proposal.digest,
            "raw_path": str(
                self.domain_root / self.executor.current.evidence.raw_relative_path
            ),
            "route": decision.route.model_dump(mode="json", by_alias=True),
            "state": self.current_state.value,
        }
        if persistence_errors:
            result["persistence_warning"] = tuple(persistence_errors)
        return result

    def assess(self, draft: AssessmentDraft) -> dict[str, object]:
        execution = self._pending_execution
        request = self._request
        if execution is None or request is None or self._pending_proposal_ref is None:
            raise CalibrationError("no completed action is awaiting assessment")
        if draft.proposal_ref != self._pending_proposal_ref:
            raise CalibrationError("assessment draft binds another proposal")
        proposal = self.bridge.propose(request)[0]
        if not set(draft.matched_prediction_items).issubset(
            set(proposal.prediction.expected_consequences)
        ):
            raise CalibrationError("assessment matches an item that was not predicted")
        for value in (
            *draft.residual_refs,
            *draft.preserved_hypothesis_refs,
            *draft.revised_hypothesis_refs,
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise CalibrationError("assessment references must be lowercase SHA-256")
        assessment_refs = (
            *draft.residual_refs,
            *draft.preserved_hypothesis_refs,
            *draft.revised_hypothesis_refs,
        )
        self._require_local_provenance(
            assessment_refs,
            aperture=(
                request.observation.payload_ref.sha256,
                self.executor.current.evidence.raw_ref,
            ),
            label="assessment provenance",
        )
        self._record_control(
            "assessment_draft",
            (draft,),
            {
                "assessment_draft_ref": draft.digest,
                "proposal_ref": self._pending_proposal_ref,
            },
        )
        assessment = self.session.assess(
            execution,
            matched_prediction_items=draft.matched_prediction_items,
            residual_refs=draft.residual_refs,
            preserved_hypothesis_refs=draft.preserved_hypothesis_refs,
            revised_hypothesis_refs=draft.revised_hypothesis_refs,
            concise_update_summary=draft.concise_update_summary,
        )
        closure_warning: str | None = None
        call_admission = self._pending_call_admission
        call_completion = self._pending_call_completion
        if call_admission is None:
            raise CalibrationError("assessment lost its durable call admission")
        if call_completion is None:
            call_completion = EnvironmentCallCompletion(
                run_id=self.bundle.run_id,
                admission_ref=call_admission.digest,
                execution_admission_ref=execution.admission.digest,
                execution_attempt_ref=execution.attempt.digest,
                frame=self.executor.current.evidence,
                budget=self.budget.receipt(),
            )
        closure = EnvironmentCallAssessmentClosure(
            run_id=self.bundle.run_id,
            admission_ref=call_admission.digest,
            completion_ref=call_completion.digest,
            assessment_ref=assessment.digest,
        )
        try:
            _write_contract(
                self._call_path(execution.admission.invocation_id, "completion"),
                call_completion,
            )
            _write_contract(
                self._call_path(execution.admission.invocation_id, "assessment"),
                closure,
            )
            self._record_control(
                "execution_assessed_closed",
                (call_completion, assessment, closure),
                {
                    "assessment_ref": assessment.digest,
                    "call_closure_ref": closure.digest,
                    "effect_status": "ASSESSED",
                },
            )
        except Exception as error:
            closure_warning = f"assessment_closure_persistence:{type(error).__name__}"
            self._post_effect_persistence_failure = type(error).__name__
        self._pending_execution = None
        self._pending_proposal_ref = None
        self._pending_call_admission = None
        self._pending_call_completion = None
        self._request = None
        if self.session.phase is SessionPhase.TERMINAL:
            refs = self.session.receipt().ledger_receipt_refs
            if not refs:
                raise CalibrationError("terminal session lacks a durable checkpoint")
            self._latest_checkpoint_ref = refs[-1]
        else:
            checkpoint = self.session.checkpoint(kind="post_action_assessment")
            if checkpoint is None:
                raise CalibrationError("post-action checkpoint was not written")
            self._latest_checkpoint_ref = checkpoint
            self._prepare_request()
        output = self.status()
        output["assessment"] = assessment.model_dump(mode="json", by_alias=True)
        if closure_warning is not None:
            output["persistence_warning"] = closure_warning
        return output

    def finalize(
        self,
        *,
        disposition: RunDisposition,
        summary: str,
        incidents: tuple[str, ...] = (),
        unresolved_burdens: tuple[str, ...] = (),
    ) -> RunTerminalRecord:
        if self._terminal_record is not None:
            return self._terminal_record
        if not summary.strip():
            raise CalibrationError("terminal summary is required")
        if self._latest_checkpoint_ref is None:
            checkpoint = self.session.checkpoint(kind="terminal_stop_checkpoint")
            if checkpoint is None:
                raise CalibrationError("terminal stop lacks a durable checkpoint")
            self._latest_checkpoint_ref = checkpoint
        observed = (
            self.current_state is GameState.WIN
            and self.session.phase is SessionPhase.TERMINAL
            and self.session.receipt().completion_genuinely_observed
        )
        if observed and disposition is not RunDisposition.SUCCESS_OBSERVED:
            raise CalibrationError("observed WIN requires success_observed disposition")
        if not observed and disposition is RunDisposition.SUCCESS_OBSERVED:
            raise CalibrationError("success cannot be claimed without assessed official WIN")
        if not self.trace.trace_path.exists():
            raise CalibrationError("raw trace is missing")
        recordings = self.executor.recording_artifacts()
        final_burdens = unresolved_burdens
        if not recordings:
            if observed:
                raise CalibrationError("success requires the official SDK recording artifact")
            final_burdens = (*final_burdens, "official SDK recording artifact is absent")
        record = RunTerminalRecord(
            run_id=self.bundle.run_id,
            game_id=self.executor.current.evidence.game_id,
            asset_manifest_ref=self.bundle.asset_manifest_ref,
            final_state=self.current_state.value,
            levels_completed=self.executor.current.evidence.levels_completed,
            win_levels=self.executor.current.evidence.win_levels,
            budget=self.budget.receipt(),
            frozen_runtime_ref=self.bundle.frozen_runtime.digest,
            toolbelt_ref=self.bundle.toolbelt_ref,
            integration_ref=self.bundle.integration_ref,
            dependency_ref=self.bundle.dependency_ref,
            model_interface_ref=self.bundle.model_interface_ref,
            domain_adapter_ref=self.bundle.domain_adapter_ref,
            executor_ref=self.bundle.executor_ref,
            lab_genesis_ref=self.genesis.digest,
            latest_checkpoint_ref=self._latest_checkpoint_ref,
            initial_reset_admission_ref=self._initial_admission.digest,
            terminal_frame=self.executor.current.evidence,
            raw_trace=artifact_pointer(self.trace.trace_path),
            official_recordings=recordings,
            completion_genuinely_observed=observed,
            disposition=disposition.value,
            concise_result_summary=summary,
            claim_class="local-public-codex-operated-strongwiz-calibration",
            claim_exclusions=(
                "not a competition entry or Kaggle result",
                "not a private or official evaluation score",
                "not an autonomous-offline or generalization claim",
                "does not establish AGI, consciousness, PAL, or a general theory",
            ),
            incidents=incidents,
            unresolved_burdens=final_burdens,
        )
        terminal_path = self.run_root / TERMINAL_RELATIVE
        _write_contract(terminal_path, record)
        record_ref = self.ledger.put_object(record.model_dump(mode="json", by_alias=True))
        if record_ref != record.digest:
            raise CalibrationError("terminal record storage changed content identity")
        self.ledger.append(
            occurrence_id=f"{self.bundle.run_id}:terminal-record",
            kind="calibration_terminal_record",
            account_id=f"{self.bundle.run_id}:terminal",
            account_version=0,
            payload={"summary": summary, "terminal_record_ref": record.digest},
            object_refs=(record.digest,),
        )
        self.ledger.verify()
        self._terminal_record = record
        return record

    def finalize_unknown_effect(
        self,
        *,
        failure_stage: str,
        error_class: str,
    ) -> RunTerminalRecord:
        if self._terminal_record is not None:
            return self._terminal_record
        record = _terminalize_interrupted_run(
            run_root=self.run_root,
            bundle=self.bundle,
            genesis=self.genesis,
            ledger=self.ledger,
            failure_stage=failure_stage,
            error_class=error_class,
            latest_frame=self.executor.current.evidence,
            budget=self.budget.receipt(),
        )
        self._terminal_record = record
        return record

    def close(self) -> None:
        if self._closed:
            return
        self.ledger.close()
        _release_live_lock(self._live_lock_path, self._live_lock_payload)
        self._closed = True


def seal_prepared_run(run_root: Path) -> RunSeal:
    terminal_path = run_root / TERMINAL_RELATIVE
    if not terminal_path.exists():
        admission_path = run_root / INITIAL_ADMISSION_RELATIVE
        if not admission_path.is_file():
            raise CalibrationError("run has neither a terminal record nor an admitted effect")
        bundle = PreparedRunBundle.model_validate_json(
            (run_root / BUNDLE_RELATIVE).read_bytes()
        )
        genesis = LabGenesisSeal.model_validate_json(
            (run_root / "lab.genesis.json").read_bytes()
        )
        manifest = LabManifest.model_validate_json(
            (run_root / "lab.manifest.json").read_bytes()
        )
        with SQLiteLedger(run_root / manifest.layout.ledger_path) as ledger:
            _terminalize_interrupted_run(
                run_root=run_root.resolve(strict=True),
                bundle=bundle,
                genesis=genesis,
                ledger=ledger,
                failure_stage="process_exit_before_terminal_record",
                error_class="UnclosedDurableAdmission",
            )
    terminal = RunTerminalRecord.model_validate_json(terminal_path.read_bytes())
    return seal_run(
        run_root,
        disposition=RunDisposition(terminal.disposition),
        terminal_state=terminal.final_state,
        terminal_evidence_ref=terminal.digest,
        completion_genuinely_observed=terminal.completion_genuinely_observed,
        concise_result_summary=(
            "official WIN observed and assessed through Strongwiz"
            if terminal.completion_genuinely_observed
            else "run stopped without an earned official WIN claim"
        ),
    )


def pack_run(
    *,
    run_root: Path,
    capsule_root: Path,
    delivery_receipt_path: Path,
) -> CalibrationRunReceipt:
    resolved_run = run_root.resolve(strict=True)
    resolved_capsule = capsule_root.resolve(strict=False)
    resolved_receipt = delivery_receipt_path.resolve(strict=False)
    if resolved_receipt == resolved_run or resolved_run in resolved_receipt.parents:
        raise CalibrationError("delivery receipt must remain outside the sealed run")
    if resolved_receipt == resolved_capsule or resolved_capsule in resolved_receipt.parents:
        raise CalibrationError("delivery receipt must remain outside the evidence capsule")
    terminal = RunTerminalRecord.model_validate_json(
        (run_root / TERMINAL_RELATIVE).read_bytes()
    )
    run_seal = seal_prepared_run(run_root)
    capsule = pack_evidence(
        run_root,
        capsule_root,
        capsule_name=f"strongwiz-arc3-{terminal.run_id}",
        acknowledge_opaque_domain_state=True,
    )
    verified = verify_evidence_capsule(
        capsule_root,
        expected_capsule_ref=capsule.digest,
    )
    if verified != capsule:
        raise CalibrationError("packed capsule did not verify exactly")
    receipt = CalibrationRunReceipt(
        terminal_record_ref=terminal.digest,
        terminal_record=terminal,
        run_seal=artifact_pointer(run_root / "run.seal.json"),
        run_seal_ref=run_seal.digest,
        evidence_capsule_path=str(capsule_root.resolve(strict=True)),
        evidence_capsule_ref=capsule.digest,
        evidence_capsule_manifest=artifact_pointer(capsule_root / CAPSULE_MANIFEST_PATH),
        capsule_verified=True,
    )
    _write_contract(delivery_receipt_path, receipt)
    return receipt


def run_stdio_control(harness: CalibrationHarness, protocol: LocalControlProtocol) -> None:
    """Simple framed loop for an isolated process; every response names PNG/raw paths."""

    sequence = 0
    try:
        while True:
            sequence += 1
            protocol.send(
                message_id=f"status-{sequence:08d}",
                kind="status",
                payload=harness.status(),
            )
            if harness.session.receipt().completion_genuinely_observed:
                record = harness.finalize(
                    disposition=RunDisposition.SUCCESS_OBSERVED,
                    summary="official WIN was assessed through the frozen Strongwiz boundary",
                )
                sequence += 1
                protocol.send(
                    message_id=f"terminal-{sequence:08d}",
                    kind="terminal",
                    payload=record.model_dump(mode="json", by_alias=True),
                )
                return
            if harness.session.phase is SessionPhase.TERMINAL:
                record = harness.finalize(
                    disposition=RunDisposition.FAILED_INFRASTRUCTURE,
                    summary="session terminalized without an earned official WIN",
                    unresolved_burdens=(
                        "terminal Strongwiz authority was not the official WIN success path",
                    ),
                )
                sequence += 1
                protocol.send(
                    message_id=f"terminal-{sequence:08d}",
                    kind="terminal",
                    payload=record.model_dump(mode="json", by_alias=True),
                )
                return
            if harness._pending_execution is None:
                proposal = protocol.receive_proposal()
                result = harness.act(proposal)
                sequence += 1
                protocol.send(
                    message_id=f"action-result-{sequence:08d}",
                    kind="action_result",
                    payload=result,
                )
            else:
                assessment = protocol.receive_assessment()
                harness.assess(assessment)
    finally:
        harness.close()
