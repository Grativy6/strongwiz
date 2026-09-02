from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.contracts import ContractModel
from strongwiz.ledger import SQLiteLedger
from strongwiz.pal23 import BoundaryAdapter, BoundaryRef, BoundaryRole, StateProjection
from strongwiz.scribe import (
    CallableScribeDriver,
    ScribeCycleReceipt,
    ScribeCycleStatus,
    ScribeDraft,
    ScribeDriverBinding,
    ScribeError,
    ScribeEvidenceAtom,
    ScribeEvidenceStatus,
    ScribeGenesis,
    ScribeMaterialFrontier,
    ScribeMaterialInput,
    ScribeMaterialKind,
    ScribeMaterialView,
    ScribePolicy,
    ScribeRequestView,
    ScribeSession,
    ScribeTrigger,
    scribe_schema_bundle,
)
from strongwiz.shorthand import (
    KevinSpeakConfiguration,
    KevinSpeakError,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
)


def _ref(label: str) -> str:
    return content_hash({"ref": label})


def _projection() -> StateProjection:
    return StateProjection(
        projection_id="scribe-work",
        state_space="derived working summaries",
        included_coordinates=("payload", "source_identity", "uncertainty"),
        excluded_coordinates=(
            "action_authority",
            "domain_state",
            "private_reasoning",
            "raw_frames",
        ),
        comparator="canonical JSON bytes under fixed Kevin decoder",
        provenance_refs=(_ref("pal23-sc21"),),
    )


def _adapter(projection: StateProjection) -> BoundaryAdapter:
    source = BoundaryRef(
        boundary_id="summary-scope",
        role=BoundaryRole.SCOPE,
        carrier_or_domain="receipt-bound concise summaries",
        scope="one scribe session",
        orientation_or_coefficients_or_na="N/A: symbolic",
        resolution_or_admissible_set_or_na="positive material-kind allowlist",
        provenance_refs=(_ref("source-boundary"),),
    )
    target = BoundaryRef(
        boundary_id="representation-interface",
        role=BoundaryRole.INTERFACE,
        carrier_or_domain="Kevin Speak canonical codec",
        scope="one run-local workspace",
        orientation_or_coefficients_or_na="N/A: symbolic",
        resolution_or_admissible_set_or_na="exact UTF-8 reconstruction",
        provenance_refs=(projection.digest,),
    )
    return BoundaryAdapter(
        adapter_id="scribe-summary-adapter",
        source=source,
        target=target,
        hypotheses=("input is a supplied derived summary",),
        preserved_data=("canonical payload", "evidence reference", "uncertainty"),
        lost_data=("private reasoning", "raw observation"),
        lossless=False,
        evidence_refs=(_ref("adapter-evidence"),),
        authority_ceiling="representation recommendation only",
        reopening_condition="material kind, projection, or decoder changes",
    )


def _binding() -> ScribeDriverBinding:
    return ScribeDriverBinding(
        driver_id="test-scribe",
        driver_version="1",
        driver_artifact_ref=_ref("test-scribe-code"),
    )


def _session(
    tmp_path: Path,
    proposal_function: Callable[[ScribeRequestView], ScribeDraft],
) -> tuple[SQLiteLedger, KevinSpeakWorkspace, ScribeSession]:
    ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    workspace = KevinSpeakWorkspace.open_blank(
        ledger,
        workspace_id="kevin-workspace",
        configuration=KevinSpeakConfiguration(),
    )
    projection = _projection()
    driver = CallableScribeDriver(
        binding=_binding(),
        proposal_function=proposal_function,
    )
    session = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id="scribe-session",
        driver=driver,
        policy=ScribePolicy(),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    return ledger, workspace, session


def _atom(label: str, statement: str) -> ScribeEvidenceAtom:
    return ScribeEvidenceAtom(
        atom_id=label,
        statement=statement,
        status=ScribeEvidenceStatus.UNRESOLVED,
        uncertainty="bounded fixture; source truthfulness is not inferred",
        goal_relevance="tests exact working-representation behavior",
        reopening_condition="new fixture evidence changes this summary",
    )


def _ingest(
    ledger: SQLiteLedger,
    session: ScribeSession,
    *,
    ordinal: int,
    statement: str,
    atom: ScribeEvidenceAtom | None = None,
    kind: ScribeMaterialKind = ScribeMaterialKind.RESIDUAL_SUMMARY,
) -> ScribeEvidenceAtom:
    payload = atom or _atom(f"atom-{ordinal}", statement)
    evidence_ref = ledger.put_object({"fixture_evidence": f"evidence-{ordinal}"})
    session.ingest(
        ScribeMaterialInput(
            material_id=f"m-{ordinal}",
            ordinal=ordinal,
            kind=kind,
            scope_id="test-scope",
            payload=payload,
            payload_ref=payload.digest,
            projection_ref=session.work_projection.digest,
            evidence_refs=(evidence_ref,),
        )
    )
    return payload


def _ingest_eight(
    ledger: SQLiteLedger,
    session: ScribeSession,
    repeated: str = "bounded repeated summary ",
) -> None:
    for ordinal in range(8):
        _ingest(
            ledger,
            session,
            ordinal=ordinal,
            statement=f"{repeated}{ordinal}",
            kind=ScribeMaterialKind.CHECKPOINT_SUMMARY,
        )


def test_scribe_uses_disjoint_hidden_validation_and_can_earn_compression(
    tmp_path: Path,
) -> None:
    repeated = "movement residual remains unresolved under the current scope; " * 18
    seen: list[ScribeRequestView] = []

    def propose(request: ScribeRequestView) -> ScribeDraft:
        seen.append(request)
        sources = tuple(
            sorted(item.material.payload_ref for item in request.adaptation_materials)
        )
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="MRU",
                    expansion=repeated,
                    concise_meaning="movement residual remains unresolved in current scope",
                    source_payload_refs=sources,
                ),
            ),
            rationale="The exact long clause repeats across adaptation summaries.",
        )

    ledger, workspace, session = _session(tmp_path, propose)
    try:
        for ordinal in range(8):
            _ingest(
                ledger,
                session,
                ordinal=ordinal,
                statement=f"{repeated}case-{ordinal}",
            )
        assert session.should_run()
        cycle = session.run_cycle(
            cycle_id="cycle-1",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        assert cycle.status is ScribeCycleStatus.PROMOTED
        assert cycle.productive_transition
        assert len(seen) == 1
        request = seen[0]
        visible = {item.material.digest for item in request.adaptation_materials}
        withheld = set(request.request.withheld_validation_material_refs)
        visible_payloads = {item.material.payload_ref for item in request.adaptation_materials}
        withheld_payloads = {
            item.payload_ref
            for item in session.materials
            if item.digest in request.request.withheld_validation_material_refs
        }
        assert visible.isdisjoint(withheld)
        assert visible_payloads.isdisjoint(withheld_payloads)
        assert len(visible) == 6
        assert len(withheld) == 2
        assert (
            session.run_cycle(cycle_id="cycle-1", trigger=ScribeTrigger.MATERIAL_THRESHOLD)
            == cycle
        )

        later = _atom("atom-8", f"{repeated}later")
        compact = session.ingest(
            ScribeMaterialInput(
                material_id="m-8",
                ordinal=8,
                kind=ScribeMaterialKind.RESIDUAL_SUMMARY,
                scope_id="test-scope",
                payload=later,
                payload_ref=later.digest,
                projection_ref=session.work_projection.digest,
                evidence_refs=(ledger.put_object({"fixture_evidence": "evidence-8"}),),
            )
        )
        assert compact.lane == "compact"
        with pytest.raises(ScribeError, match="cannot be reused across semantics"):
            session.run_cycle(cycle_id="cycle-1", trigger=ScribeTrigger.REASSESSMENT)
        verification = session.verify()
        assert verification.material_count == 9
        assert verification.promoted_cycle_count == 1
        assert verification.exact_workspace_round_trips

        durable_request = ledger.get_payload(cycle.request_ref)
        assert repeated.encode() not in canonical_bytes(durable_request)
        assert workspace.decode_entry(compact) == later.model_dump(mode="json", by_alias=True)
    finally:
        ledger.close()


def test_duplicate_payload_never_crosses_the_hidden_validation_boundary(
    tmp_path: Path,
) -> None:
    seen: list[ScribeRequestView] = []

    def no_candidate(request: ScribeRequestView) -> ScribeDraft:
        seen.append(request)
        return ScribeDraft(proposals=(), rationale="No repeated structure earned a symbol.")

    ledger, _workspace, session = _session(tmp_path, no_candidate)
    try:
        duplicate = _atom("same-atom", "the exact same payload appears twice")
        for ordinal in range(9):
            _ingest(
                ledger,
                session,
                ordinal=ordinal,
                statement=f"unique-{ordinal}",
                atom=duplicate if ordinal in {0, 2} else None,
            )
        cycle = session.run_cycle(
            cycle_id="duplicate-payload",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        assert cycle.status is ScribeCycleStatus.NO_CANDIDATE
        visible_payloads = {item.material.payload_ref for item in seen[0].adaptation_materials}
        withheld_payloads = {
            item.payload_ref
            for item in session.materials
            if item.digest in cycle.validation_material_refs
        }
        assert visible_payloads.isdisjoint(withheld_payloads)
        duplicate_materials = tuple(
            item for item in session.materials if item.payload_ref == duplicate.digest
        )
        assert len(duplicate_materials) == 2
        assert all(
            item.digest in cycle.adaptation_material_refs for item in duplicate_materials
        )
    finally:
        ledger.close()


def test_payload_arm_assignment_remains_sticky_across_cycles(tmp_path: Path) -> None:
    seen: list[ScribeRequestView] = []

    def no_candidate(request: ScribeRequestView) -> ScribeDraft:
        seen.append(request)
        return ScribeDraft(proposals=(), rationale="No candidate in this cycle.")

    ledger, _workspace, session = _session(tmp_path, no_candidate)
    try:
        first_atoms: list[ScribeEvidenceAtom] = []
        for ordinal in range(8):
            first_atoms.append(
                _ingest(
                    ledger,
                    session,
                    ordinal=ordinal,
                    statement=f"first-cycle-{ordinal}",
                )
            )
        session.run_cycle(cycle_id="first-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD)
        first_visible = {item.material.payload_ref for item in seen[0].adaptation_materials}
        first_withheld = {
            item.payload_ref
            for item in session.materials
            if item.digest in seen[0].request.withheld_validation_material_refs
        }
        assert first_atoms[0].digest in first_visible
        assert first_atoms[2].digest in first_withheld

        for ordinal in range(8, 16):
            repeated_atom = (
                first_atoms[2] if ordinal == 9 else first_atoms[0] if ordinal == 11 else None
            )
            _ingest(
                ledger,
                session,
                ordinal=ordinal,
                statement=f"second-cycle-{ordinal}",
                atom=repeated_atom,
            )
        second = session.run_cycle(
            cycle_id="second-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD
        )
        second_visible = {item.material.payload_ref for item in seen[1].adaptation_materials}
        second_withheld = {
            item.payload_ref
            for item in session.materials
            if item.digest in second.validation_material_refs
        }
        assert first_atoms[0].digest in second_visible
        assert first_atoms[0].digest not in second_withheld
        assert first_atoms[2].digest in second_withheld
        assert first_atoms[2].digest not in second_visible
        assert second_visible.isdisjoint(second_withheld)
    finally:
        ledger.close()


def test_shared_workspace_sessions_namespace_same_cycle_evaluations(
    tmp_path: Path,
) -> None:
    repeated = "shared workspace structure earns exact compression; " * 20

    def proposal(request: ScribeRequestView) -> ScribeDraft:
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="SWS",
                    expansion=repeated,
                    concise_meaning="shared-workspace structure",
                    source_payload_refs=tuple(
                        sorted(
                            item.material.payload_ref for item in request.adaptation_materials
                        )
                    ),
                ),
            ),
            rationale="The long exact clause repeats across this session's split.",
        )

    ledger = SQLiteLedger(tmp_path / "shared-scribe.sqlite3")
    workspace = KevinSpeakWorkspace.open_blank(
        ledger,
        workspace_id="shared-kevin-workspace",
        configuration=KevinSpeakConfiguration(),
    )
    projection = _projection()
    driver_a = CallableScribeDriver(
        binding=_binding(),
        proposal_function=proposal,
    )
    driver_b = CallableScribeDriver(
        binding=_binding(),
        proposal_function=proposal,
    )
    session_a = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id="scribe-session-a",
        driver=driver_a,
        policy=ScribePolicy(promote_when_mechanical_gates_pass=False),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    session_b = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id="scribe-session-b",
        driver=driver_b,
        policy=ScribePolicy(),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    for ordinal in range(8):
        _ingest(
            ledger,
            session_a,
            ordinal=ordinal,
            statement=f"{repeated}a-{ordinal}",
        )
        _ingest(
            ledger,
            session_b,
            ordinal=ordinal,
            statement=f"{repeated}b-{ordinal}",
        )
    cycle_a = session_a.run_cycle(
        cycle_id="same-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD
    )
    cycle_b = session_b.run_cycle(
        cycle_id="same-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD
    )
    assert cycle_a.status is ScribeCycleStatus.NOT_EARNED
    assert cycle_b.status is ScribeCycleStatus.PROMOTED
    assert cycle_a.evaluation_ref is not None
    assert cycle_b.evaluation_ref is not None
    evaluation_a = ledger.get_payload(cycle_a.evaluation_ref)
    evaluation_b = ledger.get_payload(cycle_b.evaluation_ref)
    assert isinstance(evaluation_a, dict)
    assert isinstance(evaluation_b, dict)
    assert evaluation_a["evaluation_id"] != evaluation_b["evaluation_id"]
    assert "scribe-session-a.same-cycle" in str(evaluation_a["evaluation_id"])
    assert "scribe-session-b.same-cycle" in str(evaluation_b["evaluation_id"])
    session_a.verify()
    session_b.verify()
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "shared-scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored_a = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session-a",
            driver=driver_a,
        )
        restored_b = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session-b",
            driver=driver_b,
        )
        assert restored_a.cycles == (cycle_a,)
        assert restored_b.cycles == (cycle_b,)
        restored_a.verify()
        restored_b.verify()
    finally:
        restored_ledger.close()


def test_shared_workspace_material_entry_identity_is_injective(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "shared-entry-identity.sqlite3")
    workspace = KevinSpeakWorkspace.open_blank(
        ledger,
        workspace_id="shared-entry-workspace",
        configuration=KevinSpeakConfiguration(),
    )
    projection = _projection()
    driver = CallableScribeDriver(
        binding=_binding(),
        proposal_function=lambda _request: ScribeDraft(
            proposals=(), rationale="No cycle is needed."
        ),
    )
    session_ab = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id="a.b",
        driver=driver,
        policy=ScribePolicy(),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    session_a = ScribeSession.open(
        ledger,
        workspace=workspace,
        session_id="a",
        driver=driver,
        policy=ScribePolicy(),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    atom_ab = _atom("atom-ab", "owned by session a.b and material c")
    atom_a = _atom("atom-a", "owned by session a and material b.c")
    input_ab = ScribeMaterialInput(
        material_id="c",
        ordinal=0,
        kind=ScribeMaterialKind.DECISION_SUMMARY,
        scope_id="test-scope",
        payload=atom_ab,
        payload_ref=atom_ab.digest,
        projection_ref=projection.digest,
        evidence_refs=(ledger.put_object({"evidence": "a.b/c"}),),
    )
    input_a = ScribeMaterialInput(
        material_id="b.c",
        ordinal=0,
        kind=ScribeMaterialKind.DECISION_SUMMARY,
        scope_id="test-scope",
        payload=atom_a,
        payload_ref=atom_a.digest,
        projection_ref=projection.digest,
        evidence_refs=(ledger.put_object({"evidence": "a/b.c"}),),
    )
    assert f"{session_ab.session_id}.{input_ab.material_id}" == (
        f"{session_a.session_id}.{input_a.material_id}"
    )
    entry_ab = session_ab.ingest(input_ab)
    entry_a = session_a.ingest(input_a)
    assert entry_ab.entry_id != entry_a.entry_id
    assert len(workspace.entries) == 2
    assert workspace.decode_entry(entry_ab) == atom_ab.model_dump(mode="json", by_alias=True)
    assert workspace.decode_entry(entry_a) == atom_a.model_dump(mode="json", by_alias=True)
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "shared-entry-identity.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored_ab = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="a.b",
            driver=driver,
        )
        restored_a = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="a",
            driver=driver,
        )
        assert restored_ab.materials[0].payload_ref == atom_ab.digest
        assert restored_a.materials[0].payload_ref == atom_a.digest
        restored_ab.verify()
        restored_a.verify()
    finally:
        restored_ledger.close()


def test_scribe_defers_without_a_valid_disjoint_split(tmp_path: Path) -> None:
    def no_candidate(_request: ScribeRequestView) -> ScribeDraft:
        raise AssertionError("driver must not be called before the evidence gate")

    ledger, _workspace, session = _session(tmp_path, no_candidate)
    try:
        for ordinal in range(2):
            _ingest(
                ledger,
                session,
                ordinal=ordinal,
                statement=f"small-{ordinal}",
                kind=ScribeMaterialKind.DECISION_SUMMARY,
            )
        cycle = session.run_cycle(cycle_id="too-early", trigger=ScribeTrigger.STAGE_BOUNDARY)
        assert cycle.status is ScribeCycleStatus.DEFERRED
        assert cycle.reasons == ("insufficient_disjoint_material",)
        assert len(session.requests) == 1
    finally:
        ledger.close()


def test_scribe_failure_is_receipted_and_restartable(tmp_path: Path) -> None:
    def fail(_request: ScribeRequestView) -> ScribeDraft:
        raise RuntimeError("provider unavailable")

    ledger, workspace, session = _session(tmp_path, fail)
    _ingest_eight(ledger, session)
    failed = session.run_cycle(
        cycle_id="failed-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD
    )
    assert failed.status is ScribeCycleStatus.FAILED
    assert failed.reasons == ("scribe_driver_failure:RuntimeError",)
    assert not failed.requires_reentry
    assert session.verify().pending_material_count == 8
    workspace_id = workspace.workspace_id
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace_id
        )
        restored = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session",
            driver=session.driver,
        )
        assert restored.cycles == (failed,)
        assert restored.verify().pending_material_count == 8
    finally:
        restored_ledger.close()


def test_interrupted_provider_is_not_invoked_again(tmp_path: Path) -> None:
    calls = 0

    def interrupt(_request: ScribeRequestView) -> ScribeDraft:
        nonlocal calls
        calls += 1
        raise SystemExit("simulated process loss during provider call")

    ledger, workspace, session = _session(tmp_path, interrupt)
    _ingest_eight(ledger, session)
    with pytest.raises(SystemExit):
        session.run_cycle(
            cycle_id="interrupted-provider",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
    assert session.verify().incomplete_request_count == 1
    blocked_atom = _atom("blocked-after-request", "must not cross an open request")
    with pytest.raises(ScribeError, match="unfinished cycle"):
        session.ingest(
            ScribeMaterialInput(
                material_id="m-8",
                ordinal=8,
                kind=ScribeMaterialKind.CHECKPOINT_SUMMARY,
                scope_id="test-scope",
                payload=blocked_atom,
                payload_ref=blocked_atom.digest,
                projection_ref=session.work_projection.digest,
                evidence_refs=(ledger.put_object({"evidence": "blocked"}),),
            )
        )
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session",
            driver=session.driver,
        )
        failed = restored.run_cycle(
            cycle_id="interrupted-provider",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        assert failed.status is ScribeCycleStatus.FAILED
        assert failed.reasons == ("scribe_provider_outcome_unknown_after_interruption",)
        assert calls == 1
    finally:
        restored_ledger.close()


def test_frozen_draft_resumes_without_reinvoking_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def no_candidate(_request: ScribeRequestView) -> ScribeDraft:
        nonlocal calls
        calls += 1
        return ScribeDraft(proposals=(), rationale="Nothing earned compression.")

    ledger, workspace, session = _session(tmp_path, no_candidate)
    _ingest_eight(ledger, session)

    def interrupt_after_freeze(*_args: object, **_kwargs: object) -> ScribeCycleReceipt:
        raise SystemExit("simulated loss after frozen draft")

    monkeypatch.setattr(session, "_finish_frozen", interrupt_after_freeze)
    with pytest.raises(SystemExit):
        session.run_cycle(
            cycle_id="frozen-resume",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
    assert len(session.frozen_drafts) == 1
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session",
            driver=session.driver,
        )
        cycle = restored.run_cycle(
            cycle_id="frozen-resume",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        assert cycle.status is ScribeCycleStatus.NO_CANDIDATE
        assert calls == 1
    finally:
        restored_ledger.close()


def test_partial_kevin_mutation_fails_closed_and_requires_reentry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repeated = "earned structure survives representation compression; " * 20
    calls = 0

    def proposal(request: ScribeRequestView) -> ScribeDraft:
        nonlocal calls
        calls += 1
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="ESC",
                    expansion=repeated,
                    concise_meaning="earned structure survives compression",
                    source_payload_refs=tuple(
                        sorted(
                            item.material.payload_ref for item in request.adaptation_materials
                        )
                    ),
                ),
            ),
            rationale="Repeated clause is present across the split.",
        )

    ledger, workspace, session = _session(tmp_path, proposal)
    for ordinal in range(8):
        _ingest(ledger, session, ordinal=ordinal, statement=f"{repeated}{ordinal}")
    original_record = session._record

    def interrupt_cycle_record(
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str:
        if kind == "scribe_cycle":
            raise SystemExit("simulated cross-account interruption")
        return original_record(kind, value, object_refs=object_refs)

    monkeypatch.setattr(session, "_record", interrupt_cycle_record)
    with pytest.raises(SystemExit):
        session.run_cycle(
            cycle_id="partial-promotion",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
    assert workspace.active_codebook.version == 1
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session",
            driver=session.driver,
        )
        failed = restored.run_cycle(
            cycle_id="partial-promotion",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
        assert failed.status is ScribeCycleStatus.FAILED
        assert failed.requires_reentry
        assert failed.promotion_ref is not None
        assert "partial_kevin_mutation_requires_reentry" in failed.reasons
        assert calls == 1
        blocked_atom = _atom("blocked-reentry", "requires a new session boundary")
        with pytest.raises(ScribeError, match="new re-entry boundary"):
            restored.ingest(
                ScribeMaterialInput(
                    material_id="m-8",
                    ordinal=8,
                    kind=ScribeMaterialKind.CHECKPOINT_SUMMARY,
                    scope_id="test-scope",
                    payload=blocked_atom,
                    payload_ref=blocked_atom.digest,
                    projection_ref=restored.work_projection.digest,
                    evidence_refs=(
                        restored_ledger.put_object({"evidence": "blocked-reentry"}),
                    ),
                )
            )
        with pytest.raises(ScribeError, match="new re-entry boundary"):
            restored.run_cycle(
                cycle_id="unsafe-continuation",
                trigger=ScribeTrigger.REASSESSMENT,
            )
    finally:
        restored_ledger.close()


def test_interrupted_ingest_finishes_exact_orphan_without_duplicate_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, workspace, session = _session(
        tmp_path,
        lambda _request: ScribeDraft(proposals=(), rationale="unused"),
    )
    atom = _atom("orphan", "one exact derived summary")
    evidence_ref = ledger.put_object({"fixture_evidence": "orphan"})
    material_input = ScribeMaterialInput(
        material_id="m-0",
        ordinal=0,
        kind=ScribeMaterialKind.OUTCOME_SUMMARY,
        scope_id="test-scope",
        payload=atom,
        payload_ref=atom.digest,
        projection_ref=session.work_projection.digest,
        evidence_refs=(evidence_ref,),
    )
    original_record = session._record

    def interrupt_material(
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str:
        if kind == "scribe_material":
            raise RuntimeError("simulated interruption")
        return original_record(kind, value, object_refs=object_refs)

    monkeypatch.setattr(session, "_record", interrupt_material)
    with pytest.raises(RuntimeError):
        session.ingest(material_input)
    assert len(workspace.entries) == 1
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        restored = ScribeSession.restore(
            restored_ledger,
            workspace=restored_workspace,
            session_id="scribe-session",
            driver=session.driver,
        )
        conflicting_atom = _atom("orphan-conflict", "different source under same identity")
        with pytest.raises(ScribeError, match="orphan Kevin entry conflicts"):
            restored.ingest(
                material_input.model_copy(
                    update={
                        "payload": conflicting_atom,
                        "payload_ref": conflicting_atom.digest,
                    }
                )
            )
        restored.ingest(material_input)
        assert len(restored_workspace.entries) == 1
        assert len(restored.materials) == 1
    finally:
        restored_ledger.close()


def test_restore_rejects_forged_promoted_lineage_without_kevin_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_candidate(_request: ScribeRequestView) -> ScribeDraft:
        return ScribeDraft(proposals=(), rationale="No proposal.")

    ledger, workspace, session = _session(tmp_path, no_candidate)
    _ingest_eight(ledger, session)

    def interrupt_after_freeze(*_args: object, **_kwargs: object) -> ScribeCycleReceipt:
        raise SystemExit("leave a request and frozen draft")

    monkeypatch.setattr(session, "_finish_frozen", interrupt_after_freeze)
    with pytest.raises(SystemExit):
        session.run_cycle(
            cycle_id="forged-cycle",
            trigger=ScribeTrigger.MATERIAL_THRESHOLD,
        )
    request = session.requests[0]
    frozen = session.frozen_drafts[0]
    candidate_ref = ledger.put_object({"forged": "candidate"})
    evaluation_ref = ledger.put_object({"forged": "evaluation"})
    promotion_ref = ledger.put_object({"forged": "promotion"})
    forged = ScribeCycleReceipt(
        cycle_id=request.request_id,
        request_ref=request.digest,
        draft_ref=frozen.draft_ref,
        frozen_draft_ref=frozen.digest,
        predecessor_codebook_ref=request.active_codebook_ref,
        candidate_codebook_ref=candidate_ref,
        evaluation_ref=evaluation_ref,
        promotion_ref=promotion_ref,
        adaptation_material_refs=request.adaptation_material_refs,
        validation_material_refs=request.withheld_validation_material_refs,
        status=ScribeCycleStatus.PROMOTED,
        reasons=(),
        productive_transition=True,
    )
    session._record(
        "scribe_cycle",
        forged,
        object_refs=(
            request.digest,
            frozen.digest,
            frozen.draft_ref,
            candidate_ref,
            evaluation_ref,
            promotion_ref,
        ),
    )
    ledger.close()

    restored_ledger = SQLiteLedger(tmp_path / "scribe.sqlite3")
    try:
        restored_workspace = KevinSpeakWorkspace.restore(
            restored_ledger, workspace_id=workspace.workspace_id
        )
        with pytest.raises(ScribeError, match="durable Kevin lineage"):
            ScribeSession.restore(
                restored_ledger,
                workspace=restored_workspace,
                session_id="scribe-session",
                driver=session.driver,
            )
    finally:
        restored_ledger.close()


def test_scribe_requires_resolved_evidence_and_closed_derived_atoms(
    tmp_path: Path,
) -> None:
    ledger, _workspace, session = _session(
        tmp_path,
        lambda _request: ScribeDraft(proposals=(), rationale="unused"),
    )
    atom = _atom("missing-evidence", "derived but not self-authenticating")
    with pytest.raises(ScribeError, match="evidence absent"):
        session.ingest(
            ScribeMaterialInput(
                material_id="bad",
                ordinal=0,
                kind=ScribeMaterialKind.DECISION_SUMMARY,
                scope_id="test-scope",
                payload=atom,
                payload_ref=atom.digest,
                projection_ref=session.work_projection.digest,
                evidence_refs=(_ref("not-stored"),),
            )
        )
    with pytest.raises(ValidationError):
        ScribeEvidenceAtom.model_validate(
            {
                **atom.model_dump(mode="json", by_alias=True),
                "contains_private_reasoning": True,
            }
        )
    with pytest.raises(ValidationError):
        ScribeEvidenceAtom.model_validate(
            {
                **atom.model_dump(mode="json", by_alias=True),
                "raw_frame": "forbidden extra field",
            }
        )
    ledger.close()


def test_scribe_schema_is_representation_only() -> None:
    bundle = scribe_schema_bundle()
    assert bundle["schema"] == "strongwiz.scribe.v1"
    assert "representation-only" in str(bundle["claim_ceiling"])


def test_every_durable_scribe_record_rejects_a_foreign_schema(tmp_path: Path) -> None:
    ledger, _workspace, session = _session(
        tmp_path,
        lambda _request: ScribeDraft(proposals=(), rationale="No shorthand earned."),
    )
    _ingest_eight(ledger, session)
    cycle = session.run_cycle(cycle_id="schema-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD)
    request = session.requests[0]
    frozen = session.frozen_drafts[0]
    frontier = ScribeMaterialFrontier.model_validate(
        ledger.get_payload(request.material_frontier_ref)
    )
    genesis_envelope = next(
        item
        for item in ledger.receipts()
        if item.account_id == "scribe-session.scribe" and item.kind == "scribe_genesis"
    )
    genesis = ScribeGenesis.model_validate(ledger.get_payload(genesis_envelope.payload_hash))
    atom = _atom("schema-atom", "closed schema fixture")
    material_input = ScribeMaterialInput(
        material_id="schema-only",
        ordinal=99,
        kind=ScribeMaterialKind.DECISION_SUMMARY,
        scope_id="test-scope",
        payload=atom,
        payload_ref=atom.digest,
        projection_ref=session.work_projection.digest,
        evidence_refs=(ledger.put_object({"fixture_evidence": "schema"}),),
    )
    durable_records: tuple[ContractModel, ...] = (
        atom,
        material_input,
        session.materials[0],
        session.policy,
        session.driver.binding,
        frontier,
        request,
        frozen.draft,
        frozen,
        genesis,
        cycle,
        session.verify(),
    )
    for record in durable_records:
        payload = record.model_dump(mode="json", by_alias=True)
        payload["schema"] = "foreign.scribe.schema"
        with pytest.raises(ValidationError, match="unsupported scribe schema"):
            type(record).model_validate(payload)
    ledger.close()


def test_scribe_contracts_reject_inconsistent_boundary_states(tmp_path: Path) -> None:
    ledger, _workspace, session = _session(
        tmp_path,
        lambda _request: ScribeDraft(proposals=(), rationale="No shorthand earned."),
    )
    try:
        _ingest_eight(ledger, session)
        cycle = session.run_cycle(
            cycle_id="invariant-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD
        )
        request = session.requests[0]
        frozen = session.frozen_drafts[0]
        material = session.materials[0]
        atom = session._payload(material)
        verification = session.verify()

        with pytest.raises(ValidationError, match="safe stable identifier"):
            atom.model_copy(update={"atom_id": "not safe/id"})
        with pytest.raises(ValidationError, match="statement is required"):
            atom.model_copy(update={"statement": " "})
        with pytest.raises(ValidationError, match="sorted and unique"):
            atom.model_copy(update={"predecessor_refs": (_ref("z"), _ref("a"))})
        with pytest.raises(ValidationError, match="lowercase SHA-256"):
            atom.model_copy(update={"counterevidence_refs": ("bad",)})

        evidence_ref = ledger.put_object({"fixture_evidence": "contract-errors"})
        material_input = ScribeMaterialInput(
            material_id="contract-input",
            ordinal=99,
            kind=ScribeMaterialKind.DECISION_SUMMARY,
            scope_id="test-scope",
            payload=atom,
            payload_ref=atom.digest,
            projection_ref=session.work_projection.digest,
            evidence_refs=(evidence_ref,),
        )
        with pytest.raises(ValidationError, match="scope is required"):
            material_input.model_copy(update={"scope_id": ""})
        with pytest.raises(ValidationError, match="does not bind"):
            material_input.model_copy(update={"payload_ref": _ref("other-payload")})
        with pytest.raises(ValidationError, match="requires receipt-bound evidence"):
            material_input.model_copy(update={"evidence_refs": ()})
        with pytest.raises(ValidationError, match="lowercase SHA-256"):
            material.model_copy(update={"entry_ref": "bad"})
        with pytest.raises(ValidationError, match="scope is required"):
            material.model_copy(update={"scope_id": ""})
        with pytest.raises(ValidationError, match="receipt-bound evidence"):
            material.model_copy(update={"evidence_refs": ()})

        with pytest.raises(ValidationError, match="inside its stride"):
            session.policy.model_copy(update={"validation_slot": 3})
        with pytest.raises(ValidationError, match="cannot hold its minimum split"):
            session.policy.model_copy(update={"maximum_materials_per_cycle": 5})
        with pytest.raises(ValidationError, match="cannot precede its minimum split"):
            session.policy.model_copy(update={"trigger_material_count": 5})
        with pytest.raises(ValidationError, match="canonical NFKC text without padding"):
            session.driver.binding.model_copy(update={"driver_id": "test-scribe "})

        frontier = ScribeMaterialFrontier.model_validate(
            ledger.get_payload(request.material_frontier_ref)
        )
        with pytest.raises(ValidationError, match="must be unique"):
            frontier.model_copy(
                update={"material_refs": (*frontier.material_refs, frontier.material_refs[0])}
            )
        with pytest.raises(ValidationError, match="ordinal does not match"):
            frontier.model_copy(update={"latest_ordinal": None})
        with pytest.raises(ValidationError, match="concise task"):
            request.model_copy(update={"concise_task": ""})
        with pytest.raises(ValidationError, match="must be disjoint"):
            request.model_copy(
                update={
                    "withheld_validation_material_refs": (request.adaptation_material_refs[0],)
                }
            )

        view = ScribeMaterialView(material=material, payload=atom)
        with pytest.raises(ValidationError, match="changed its source payload"):
            view.model_copy(update={"payload": _atom("different", "different payload")})
        request_view = ScribeRequestView(
            request=request,
            adaptation_materials=tuple(
                ScribeMaterialView(material=item, payload=session._payload(item))
                for item in session.materials
                if item.digest in request.adaptation_material_refs
            ),
        )
        with pytest.raises(ValidationError, match="durable material aperture"):
            request_view.model_copy(update={"adaptation_materials": ()})
        with pytest.raises(ValidationError, match="concise rationale"):
            frozen.draft.model_copy(update={"rationale": ""})
        proposal = KevinSymbolProposal(
            token="ONE",
            expansion="one repeated expansion",
            concise_meaning="one",
            source_payload_refs=(atom.digest,),
        )
        with pytest.raises(ValidationError, match="tokens must be unique"):
            frozen.draft.model_copy(update={"proposals": (proposal, proposal)})
        with pytest.raises(ValidationError, match="changed its provider output"):
            frozen.model_copy(update={"draft_ref": _ref("wrong-draft")})

        with pytest.raises(ValidationError, match="must travel together"):
            cycle.model_copy(update={"frozen_draft_ref": None})
        with pytest.raises(ValidationError, match="deferred cycle"):
            cycle.model_copy(update={"status": ScribeCycleStatus.DEFERRED})
        with pytest.raises(ValidationError, match="bind only its empty draft"):
            cycle.model_copy(update={"candidate_codebook_ref": _ref("candidate")})
        with pytest.raises(ValidationError, match="only a failed partial cycle"):
            cycle.model_copy(update={"requires_reentry": True})
        failed = cycle.model_copy(
            update={
                "status": ScribeCycleStatus.FAILED,
                "draft_ref": None,
                "frozen_draft_ref": None,
                "reasons": ("provider_failed",),
            }
        )
        with pytest.raises(ValidationError, match="preserve reasons"):
            failed.model_copy(update={"reasons": ()})
        with pytest.raises(ValidationError, match="retain its candidate"):
            failed.model_copy(update={"evaluation_ref": _ref("evaluation")})
        with pytest.raises(ValidationError, match="explicit scribe re-entry"):
            failed.model_copy(update={"candidate_codebook_ref": _ref("candidate")})

        with pytest.raises(ValidationError, match="exceeds total cycles"):
            verification.model_copy(update={"promoted_cycle_count": 2})
        with pytest.raises(ValidationError, match="unfinished request"):
            verification.model_copy(
                update={"requires_reentry": True, "incomplete_request_count": 1}
            )
    finally:
        ledger.close()


def test_scribe_runtime_receipts_invalid_provider_outputs(tmp_path: Path) -> None:
    repeated = "provider output must remain inside its frozen aperture; " * 18

    def invalid_return(_request: ScribeRequestView) -> ScribeDraft:
        return cast(ScribeDraft, {"not": "a typed draft"})

    def oversized(request: ScribeRequestView) -> ScribeDraft:
        sources = tuple(
            sorted(item.material.payload_ref for item in request.adaptation_materials)
        )
        return ScribeDraft(
            proposals=tuple(
                KevinSymbolProposal(
                    token=f"P{index}",
                    expansion=repeated,
                    concise_meaning=f"proposal {index}",
                    source_payload_refs=sources,
                )
                for index in range(9)
            ),
            rationale="Deliberately exceeds the frozen proposal aperture.",
        )

    def withheld_source(_request: ScribeRequestView) -> ScribeDraft:
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="BADREF",
                    expansion=repeated,
                    concise_meaning="cites material outside the adaptation aperture",
                    source_payload_refs=(_ref("withheld-or-unknown"),),
                ),
            ),
            rationale="Deliberately cites an unavailable source.",
        )

    def absent_expansion(request: ScribeRequestView) -> ScribeDraft:
        source = request.adaptation_materials[0].material.payload_ref
        return ScribeDraft(
            proposals=(
                KevinSymbolProposal(
                    token="ABSENT",
                    expansion="this exact expansion is absent from every source",
                    concise_meaning="unsupported expansion",
                    source_payload_refs=(source,),
                ),
            ),
            rationale="Deliberately proposes text absent from its cited source.",
        )

    cases: tuple[tuple[str, Callable[[ScribeRequestView], ScribeDraft], str], ...] = (
        ("invalid-return", invalid_return, "scribe_driver_failure:ScribeError"),
        ("oversized", oversized, "scribe_post_provider_failure:ScribeError"),
        ("withheld", withheld_source, "scribe_post_provider_failure:ScribeError"),
        ("absent", absent_expansion, "scribe_post_provider_failure:ScribeError"),
    )
    for case_id, provider, expected_reason in cases:
        ledger, _workspace, session = _session(tmp_path / case_id, provider)
        try:
            _ingest_eight(ledger, session, repeated)
            cycle = session.run_cycle(
                cycle_id=f"{case_id}-cycle",
                trigger=ScribeTrigger.MATERIAL_THRESHOLD,
            )
            assert cycle.status is ScribeCycleStatus.FAILED
            assert expected_reason in cycle.reasons
            assert not cycle.requires_reentry
            session.verify()
        finally:
            ledger.close()


def test_scribe_runtime_detects_driver_change_and_freeze_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SwitchingDriver:
        def __init__(self) -> None:
            self.current = _binding()

        @property
        def binding(self) -> ScribeDriverBinding:
            return self.current

        def propose(self, _request: ScribeRequestView) -> ScribeDraft:
            self.current = ScribeDriverBinding(
                driver_id="changed-driver",
                driver_version="2",
                driver_artifact_ref=_ref("changed-driver"),
            )
            return ScribeDraft(proposals=(), rationale="Binding changed during call.")

    switch_ledger = SQLiteLedger(tmp_path / "switch" / "scribe.sqlite3")
    switch_workspace = KevinSpeakWorkspace.open_blank(
        switch_ledger, workspace_id="switch-workspace"
    )
    projection = _projection()
    switching_driver = SwitchingDriver()
    switch_session = ScribeSession.open(
        switch_ledger,
        workspace=switch_workspace,
        session_id="switch-session",
        driver=switching_driver,
        policy=ScribePolicy(),
        boundary_adapter=_adapter(projection),
        work_projection=projection,
    )
    _ingest_eight(switch_ledger, switch_session)
    changed = switch_session.run_cycle(
        cycle_id="driver-change", trigger=ScribeTrigger.MATERIAL_THRESHOLD
    )
    assert changed.status is ScribeCycleStatus.FAILED
    assert changed.reasons == ("scribe_driver_binding_changed",)
    switch_ledger.close()

    freeze_ledger, _workspace, freeze_session = _session(
        tmp_path / "freeze",
        lambda _request: ScribeDraft(proposals=(), rationale="Valid provider output."),
    )
    _ingest_eight(freeze_ledger, freeze_session)
    original_record = freeze_session._record

    def reject_frozen_record(
        kind: str,
        value: ContractModel,
        *,
        object_refs: tuple[str, ...] = (),
    ) -> str:
        if kind == "scribe_frozen_draft":
            raise RuntimeError("simulated durable freeze failure")
        return original_record(kind, value, object_refs=object_refs)

    monkeypatch.setattr(freeze_session, "_record", reject_frozen_record)
    frozen_failure = freeze_session.run_cycle(
        cycle_id="freeze-failure", trigger=ScribeTrigger.MATERIAL_THRESHOLD
    )
    assert frozen_failure.status is ScribeCycleStatus.FAILED
    assert frozen_failure.reasons == ("scribe_freeze_failure:RuntimeError",)
    freeze_session.verify()
    freeze_ledger.close()


def test_scribe_open_restore_and_ingest_guards(tmp_path: Path) -> None:
    ledger, workspace, session = _session(
        tmp_path,
        lambda _request: ScribeDraft(proposals=(), rationale="No shorthand earned."),
    )
    projection = session.work_projection
    wrong_target = _adapter(projection).model_copy(
        update={
            "target": _adapter(projection).target.model_copy(
                update={"provenance_refs": (_ref("another-projection"),)}
            )
        }
    )
    with pytest.raises(ScribeError, match="does not bind its work projection"):
        ScribeSession.open(
            ledger,
            workspace=workspace,
            session_id="bad-boundary-session",
            driver=session.driver,
            policy=ScribePolicy(),
            boundary_adapter=wrong_target,
            work_projection=projection,
        )
    with pytest.raises(ScribeError, match="already exists"):
        ScribeSession.open(
            ledger,
            workspace=workspace,
            session_id="scribe-session",
            driver=session.driver,
            policy=ScribePolicy(),
            boundary_adapter=_adapter(projection),
            work_projection=projection,
        )
    with pytest.raises(ScribeError, match="no durable genesis"):
        ScribeSession.restore(
            ledger,
            workspace=workspace,
            session_id="missing-session",
            driver=session.driver,
        )
    wrong_driver = CallableScribeDriver(
        binding=ScribeDriverBinding(
            driver_id="wrong-driver",
            driver_version="1",
            driver_artifact_ref=_ref("wrong-driver"),
        ),
        proposal_function=lambda _request: ScribeDraft(proposals=(), rationale="Not called."),
    )
    with pytest.raises(ScribeError, match="driver identity changed"):
        ScribeSession.restore(
            ledger,
            workspace=workspace,
            session_id="scribe-session",
            driver=wrong_driver,
        )

    first_atom = _atom("guard-first", "first material")
    first_evidence = ledger.put_object({"evidence": "guard-first"})
    first_input = ScribeMaterialInput(
        material_id="guard-first",
        ordinal=0,
        kind=ScribeMaterialKind.DECISION_SUMMARY,
        scope_id="test-scope",
        payload=first_atom,
        payload_ref=first_atom.digest,
        projection_ref=projection.digest,
        evidence_refs=(first_evidence,),
    )
    first_entry = session.ingest(first_input)
    assert session.ingest(first_input) == first_entry
    changed_atom = _atom("guard-changed", "changed payload")
    with pytest.raises(ScribeError, match="cannot be rewritten"):
        session.ingest(
            first_input.model_copy(
                update={"payload": changed_atom, "payload_ref": changed_atom.digest}
            )
        )
    second_atom = _atom("guard-second", "second material")
    with pytest.raises(ScribeError, match="ordinals must increase"):
        session.ingest(
            ScribeMaterialInput(
                material_id="guard-second",
                ordinal=0,
                kind=ScribeMaterialKind.DECISION_SUMMARY,
                scope_id="test-scope",
                payload=second_atom,
                payload_ref=second_atom.digest,
                projection_ref=projection.digest,
                evidence_refs=(ledger.put_object({"evidence": "guard-second"}),),
            )
        )
    with pytest.raises(ScribeError, match="crosses its declared projection"):
        session.ingest(
            ScribeMaterialInput(
                material_id="wrong-projection",
                ordinal=1,
                kind=ScribeMaterialKind.DECISION_SUMMARY,
                scope_id="test-scope",
                payload=second_atom,
                payload_ref=second_atom.digest,
                projection_ref=_ref("wrong-projection"),
                evidence_refs=(ledger.put_object({"evidence": "wrong-projection"}),),
            )
        )
    ledger.close()


def test_scribe_restore_rejects_unknown_receipt_and_material_during_open_cycle(
    tmp_path: Path,
) -> None:
    unknown_ledger, unknown_workspace, unknown_session = _session(
        tmp_path / "unknown",
        lambda _request: ScribeDraft(proposals=(), rationale="unused"),
    )
    unknown_session._record("unknown_scribe_kind", unknown_session.policy)
    unknown_ledger.close()
    restored_unknown_ledger = SQLiteLedger(tmp_path / "unknown" / "scribe.sqlite3")
    try:
        restored_unknown_workspace = KevinSpeakWorkspace.restore(
            restored_unknown_ledger, workspace_id=unknown_workspace.workspace_id
        )
        with pytest.raises(ScribeError, match="unknown receipt kind"):
            ScribeSession.restore(
                restored_unknown_ledger,
                workspace=restored_unknown_workspace,
                session_id="scribe-session",
                driver=unknown_session.driver,
            )
    finally:
        restored_unknown_ledger.close()

    def interrupt(_request: ScribeRequestView) -> ScribeDraft:
        raise SystemExit("leave request open")

    open_ledger, open_workspace, open_session = _session(tmp_path / "open", interrupt)
    _ingest_eight(open_ledger, open_session)
    with pytest.raises(SystemExit):
        open_session.run_cycle(cycle_id="open-cycle", trigger=ScribeTrigger.MATERIAL_THRESHOLD)
    open_session._record("scribe_material", open_session.materials[0])
    open_ledger.close()
    restored_open_ledger = SQLiteLedger(tmp_path / "open" / "scribe.sqlite3")
    try:
        restored_open_workspace = KevinSpeakWorkspace.restore(
            restored_open_ledger, workspace_id=open_workspace.workspace_id
        )
        with pytest.raises(ScribeError, match="cannot cross an unfinished cycle"):
            ScribeSession.restore(
                restored_open_ledger,
                workspace=restored_open_workspace,
                session_id="scribe-session",
                driver=open_session.driver,
            )
    finally:
        restored_open_ledger.close()


def test_kevin_recommendation_semantic_id_is_idempotent_not_rewritable(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "recommend.sqlite3")
    workspace = KevinSpeakWorkspace.open_blank(ledger, workspace_id="recommend-workspace")
    try:
        first = workspace.recommend_next_round(
            recommendation_id="round-1",
            recommending_driver_ref=_ref("scribe"),
            evaluation_refs=(),
            rationale="Retain the blank codebook because no shorthand earned promotion.",
        )
        replay = workspace.recommend_next_round(
            recommendation_id="round-1",
            recommending_driver_ref=_ref("scribe"),
            evaluation_refs=(),
            rationale="Retain the blank codebook because no shorthand earned promotion.",
        )
        assert replay == first
        assert len(workspace.recommendations) == 1
        with pytest.raises(KevinSpeakError, match="cannot be reused"):
            workspace.recommend_next_round(
                recommendation_id="round-1",
                recommending_driver_ref=_ref("scribe"),
                evaluation_refs=(),
                rationale="Conflicting content under the same semantic identity.",
            )
    finally:
        ledger.close()
