from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.canonical import content_hash
from strongwiz.ledger import SQLiteLedger
from strongwiz.shorthand import (
    EvaluationRole,
    EvaluationStatus,
    KevinEvaluationSample,
    KevinPresentationMode,
    KevinSpeakConfiguration,
    KevinSpeakError,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
    ShorthandLane,
    decode_shorthand_text,
    encode_shorthand_text,
)
from tests.support import ref


def repetitive_payload(label: str, phrase: str, repetitions: int = 120) -> dict[str, object]:
    return {
        "label": label,
        "observations": [phrase for _ in range(repetitions)],
        "status": "provisional",
    }


def test_fixed_shorthand_language_is_exact_escaped_and_deterministic() -> None:
    translations = {"a": "north", "aa": "north-east"}
    source = "~north-east~ then north"
    encoded = encode_shorthand_text(source, translations)

    assert decode_shorthand_text(encoded.encoded, translations) == source
    assert encoded.encoded == encode_shorthand_text(source, translations).encoded
    assert encoded.symbol_uses == 2
    assert encoded.encoded_size_bytes < encoded.source_size_bytes

    with pytest.raises(KevinSpeakError, match="unknown symbol"):
        decode_shorthand_text("~missing~", translations)
    with pytest.raises(KevinSpeakError, match="dangling"):
        decode_shorthand_text("unfinished~", translations)


def test_configuration_separates_decoded_storage_from_model_facing_use(
    tmp_path: Path,
) -> None:
    decoded = KevinSpeakConfiguration()
    model_facing = KevinSpeakConfiguration(
        presentation_mode=KevinPresentationMode.MODEL_FACING,
        max_entry_bytes=32,
    )
    assert decoded.digest != model_facing.digest
    with pytest.raises(ValidationError, match="cannot disable"):
        KevinSpeakConfiguration(require_exact_round_trip=False)

    with SQLiteLedger(tmp_path / "bounded.sqlite3") as ledger:
        workspace = KevinSpeakWorkspace.open_blank(
            ledger,
            workspace_id="bounded",
            configuration=model_facing,
        )
        with pytest.raises(KevinSpeakError, match="entry budget"):
            workspace.append(entry_id="too-large", payload={"text": "x" * 64})


def test_blank_workspace_uses_residual_lane_and_restores(tmp_path: Path) -> None:
    path = tmp_path / "kevin.sqlite3"
    with SQLiteLedger(path) as ledger:
        workspace = KevinSpeakWorkspace.open_blank(ledger, workspace_id="blank")
        entry = workspace.append(entry_id="novel", payload={"novel": [1, 2, 3]})

        assert entry.lane is ShorthandLane.RESIDUAL
        assert workspace.decode_entry(entry) == {"novel": [1, 2, 3]}
        assert workspace.translation_table().translations == ()
        verification = workspace.verify()
        assert verification.entry_count == 1
        assert verification.residual_entry_count == 1

        restored = KevinSpeakWorkspace.restore(ledger, workspace_id="blank")
        assert restored.verify() == verification
        assert restored.decode_entry(restored.entries[0]) == {"novel": [1, 2, 3]}


def test_candidate_requires_multicase_validation_and_recovers_codebook_cost(
    tmp_path: Path,
) -> None:
    phrase = "north-corridor-resource-transition"
    adaptation = repetitive_payload("adapt", phrase)
    validation_a = repetitive_payload("validation-a", phrase)
    validation_b = repetitive_payload("validation-b", phrase)
    with SQLiteLedger(tmp_path / "evaluation.sqlite3") as ledger:
        workspace = KevinSpeakWorkspace.open_blank(ledger, workspace_id="evaluation")
        candidate = workspace.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="n",
                    expansion=phrase,
                    concise_meaning="repeated north corridor resource transition",
                    source_payload_refs=(content_hash(adaptation),),
                ),
            ),
            rationale="compress a repeated mechanics phrase",
            model_proposal_ref=ref("kevin-model-proposal"),
        )
        evaluation = workspace.evaluate_candidate(
            candidate.digest,
            (
                KevinEvaluationSample(
                    case_id="adapt", role=EvaluationRole.ADAPTATION, payload=adaptation
                ),
                KevinEvaluationSample(
                    case_id="validation-a",
                    role=EvaluationRole.VALIDATION,
                    payload=validation_a,
                ),
                KevinEvaluationSample(
                    case_id="validation-b",
                    role=EvaluationRole.VALIDATION,
                    payload=validation_b,
                ),
            ),
            evaluation_id="candidate-v1-suite",
        )

        assert evaluation.status is EvaluationStatus.ELIGIBLE
        assert evaluation.net_savings_bytes > 0
        workspace.promote(
            candidate_ref=candidate.digest,
            evaluation_ref=evaluation.digest,
        )
        assert workspace.translation_table().translations[0].expansion == phrase

        compact_payload = repetitive_payload("fresh-entry", phrase, repetitions=140)
        compact = workspace.append(entry_id="compact", payload=compact_payload)
        residual = workspace.append(entry_id="residual", payload={"different": True})
        assert compact.lane is ShorthandLane.COMPACT
        assert residual.lane is ShorthandLane.RESIDUAL
        assert workspace.decode_entry(compact) == compact_payload
        assert workspace.decode_entry(residual) == {"different": True}
        # Compact entries reconstruct their canonical source rather than storing a
        # second full source object beside the shorthand representation.
        assert not ledger.has_object(compact.source_payload_ref)
        assert ledger.has_object(residual.source_payload_ref)

        restored = KevinSpeakWorkspace.restore(ledger, workspace_id="evaluation")
        assert restored.translation_table() == workspace.translation_table()
        assert restored.verify().exact_round_trips


def test_validation_leak_and_unrecovered_transport_cost_do_not_promote(
    tmp_path: Path,
) -> None:
    payload = repetitive_payload("same", "tiny", repetitions=2)
    with SQLiteLedger(tmp_path / "rejected.sqlite3") as ledger:
        workspace = KevinSpeakWorkspace.open_blank(ledger, workspace_id="rejected")
        candidate = workspace.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="t",
                    expansion="tiny",
                    concise_meaning="tiny repeated label",
                    source_payload_refs=(content_hash(payload),),
                ),
            ),
            rationale="deliberately underpowered candidate",
        )
        evaluation = workspace.evaluate_candidate(
            candidate.digest,
            (
                KevinEvaluationSample(
                    case_id="adapt", role=EvaluationRole.ADAPTATION, payload=payload
                ),
                KevinEvaluationSample(
                    case_id="leaked", role=EvaluationRole.VALIDATION, payload=payload
                ),
            ),
            evaluation_id="rejected-suite",
        )

        assert evaluation.status is EvaluationStatus.NOT_EARNED
        assert "validation_payload_used_to_define_candidate" in evaluation.reasons
        assert "codebook_cost_not_recovered" in evaluation.reasons
        with pytest.raises(KevinSpeakError, match="not earned"):
            workspace.promote(
                candidate_ref=candidate.digest,
                evaluation_ref=evaluation.digest,
            )


def test_recursive_revision_never_reinterprets_old_entries_and_transfers_explicitly(
    tmp_path: Path,
) -> None:
    first_phrase = "stable-mechanic-transition"
    first_adapt = repetitive_payload("first-adapt", first_phrase)
    first_valid = repetitive_payload("first-valid", first_phrase)
    source_path = tmp_path / "source.sqlite3"
    with SQLiteLedger(source_path) as ledger:
        source = KevinSpeakWorkspace.open_blank(ledger, workspace_id="source")
        first = source.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="m",
                    expansion=first_phrase,
                    concise_meaning="stable mechanic transition",
                    source_payload_refs=(content_hash(first_adapt),),
                ),
            ),
            rationale="first earned shorthand",
        )
        first_eval = source.evaluate_candidate(
            first.digest,
            (
                KevinEvaluationSample(
                    case_id="first-adapt",
                    role=EvaluationRole.ADAPTATION,
                    payload=first_adapt,
                ),
                KevinEvaluationSample(
                    case_id="first-valid",
                    role=EvaluationRole.VALIDATION,
                    payload=first_valid,
                ),
            ),
            evaluation_id="first-suite",
        )
        source.promote(candidate_ref=first.digest, evaluation_ref=first_eval.digest)
        old_payload = repetitive_payload("old", first_phrase)
        old_entry = source.append(entry_id="old", payload=old_payload)

        recursive_phrase = f"{first_phrase}:{first_phrase}:hazard"
        second_adapt = repetitive_payload("second-adapt", recursive_phrase)
        second_valid = repetitive_payload("second-valid", recursive_phrase)
        second = source.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="mh",
                    expansion=recursive_phrase,
                    concise_meaning="two mechanic transitions followed by a hazard",
                    source_payload_refs=(content_hash(second_adapt),),
                ),
            ),
            rationale="recursively compose an earned earlier translation",
        )
        assert "~m~" in second.definitions[0].encoded_expansion
        second_eval = source.evaluate_candidate(
            second.digest,
            (
                KevinEvaluationSample(
                    case_id="second-adapt",
                    role=EvaluationRole.ADAPTATION,
                    payload=second_adapt,
                ),
                KevinEvaluationSample(
                    case_id="second-valid",
                    role=EvaluationRole.VALIDATION,
                    payload=second_valid,
                ),
            ),
            evaluation_id="second-suite",
        )
        source.promote(candidate_ref=second.digest, evaluation_ref=second_eval.digest)

        assert source.decode_entry(old_entry) == old_payload
        recommendation = source.recommend_next_round(
            recommendation_id="source-agent-recommendation",
            recommending_driver_ref=ref("source-model-driver"),
            evaluation_refs=(first_eval.digest, second_eval.digest),
            rationale="carry the mechanically useful shorthand into review",
            known_residuals=("novel geometry remains uncompressed",),
        )
        with pytest.raises(KevinSpeakError, match="adoption decision"):
            source.export_transfer(
                transfer_id="unapproved-transfer",
                adoption_ref=ref("missing-adoption"),
            )

    with SQLiteLedger(source_path) as restored_source_ledger:
        restored_source = KevinSpeakWorkspace.restore(
            restored_source_ledger, workspace_id="source"
        )
        assert len(restored_source.recommendations) == 1
        assert len(restored_source.reviews) == 0
        assert len(restored_source.adoption_decisions) == 0
        assert restored_source.verify().exact_round_trips
        bundle = restored_source.export_recommendation_bundle(
            recommendation_ref=recommendation.digest,
            source_run_seal_ref=ref("source-run-seal"),
            source_capsule_ref=ref("source-capsule"),
        )

    refined_phrase = f"{recursive_phrase}:access"
    refined_adapt = repetitive_payload("refined-adapt", refined_phrase)
    refined_valid = repetitive_payload("refined-valid", refined_phrase)
    review_path = tmp_path / "review.sqlite3"
    with SQLiteLedger(review_path) as review_ledger:
        model_facing_configuration = KevinSpeakConfiguration(
            presentation_mode=KevinPresentationMode.MODEL_FACING
        )
        review_workspace = KevinSpeakWorkspace.open_review(
            review_ledger,
            workspace_id="successor-review",
            bundle=bundle,
            configuration=model_facing_configuration,
        )
        refined = review_workspace.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="mha",
                    expansion=refined_phrase,
                    concise_meaning="mechanic hazard sequence followed by access",
                    source_payload_refs=(content_hash(refined_adapt),),
                ),
            ),
            rationale="successor reviewer proposes one recursive refinement",
            model_proposal_ref=ref("stronger-review-model-proposal"),
        )
        refined_eval = review_workspace.evaluate_candidate(
            refined.digest,
            (
                KevinEvaluationSample(
                    case_id="refined-adapt",
                    role=EvaluationRole.ADAPTATION,
                    payload=refined_adapt,
                ),
                KevinEvaluationSample(
                    case_id="refined-valid",
                    role=EvaluationRole.VALIDATION,
                    payload=refined_valid,
                ),
            ),
            evaluation_id="refined-suite",
        )
        withheld_payload = repetitive_payload("withheld", "speculative-shortcut")
        alternative = review_workspace.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="maybe",
                    expansion="speculative-shortcut",
                    concise_meaning="unvalidated shortcut candidate",
                    source_payload_refs=(content_hash(withheld_payload),),
                ),
                KevinSymbolProposal(
                    token="risk",
                    expansion="speculative-risk-collapse",
                    concise_meaning="unresolved risk compression candidate",
                    source_payload_refs=(content_hash(withheld_payload),),
                ),
            ),
            rationale="preserve alternatives that the review does not adopt",
            model_proposal_ref=ref("withheld-review-model-proposal"),
        )
        rejected_ref = next(
            item.digest for item in alternative.definitions if item.token == "maybe"
        )
        deferred_ref = next(
            item.digest for item in alternative.definitions if item.token == "risk"
        )
        review = review_workspace.review_next_round(
            review_id="stronger-model-review",
            recommendation_ref=recommendation.digest,
            reviewer_driver_ref=ref("stronger-model-driver"),
            reviewed_codebook_ref=refined.digest,
            evaluation_refs=(refined_eval.digest,),
            rationale="retain the source shorthand and add one validated recursive symbol",
            rejected_definition_refs=(rejected_ref,),
            deferred_definition_refs=(deferred_ref,),
        )
        adoption = review_workspace.decide_next_round_adoption(
            adoption_id="control-adopts-reviewed-shorthand",
            recommendation_ref=recommendation.digest,
            review_ref=review.digest,
            target_stage_ref=ref("successor-stage"),
            control_source_ref=ref("campaign-control-policy"),
            approve=True,
            rationale="approved only as the successor's working representation",
        )
        transfer = review_workspace.export_transfer(
            transfer_id="source-to-successor",
            adoption_ref=adoption.digest,
        )
        assert (
            transfer.recommendation_bundle.recommendation.status == "recommended_not_approved"
        )
        assert transfer.review is not None
        assert transfer.review.status == "reviewed_not_adopted"
        assert transfer.adoption.approved_codebook_ref == refined.digest
        assert tuple(item.digest for item in transfer.withheld_definitions) == tuple(
            sorted((rejected_ref, deferred_ref))
        )
        assert {item.token for item in transfer.withheld_definitions} == {"maybe", "risk"}
        incomplete_adoption = transfer.adoption.model_copy(update={"definition_decisions": ()})
        with pytest.raises(ValueError, match="explicitly approve"):
            transfer.model_copy(update={"adoption": incomplete_adoption})
        with pytest.raises(ValueError, match="every rejected or deferred"):
            transfer.model_copy(update={"withheld_definitions": ()})
        with pytest.raises(ValueError, match="source recommendation boundary"):
            transfer.model_copy(update={"source_run_seal_ref": ref("another-run-seal")})
        with pytest.raises(ValueError, match="changes or omits its review configuration"):
            transfer.model_copy(update={"review_configuration": None})
        with pytest.raises(ValueError, match="omits or adds review evaluation evidence"):
            transfer.model_copy(update={"review_evaluations": ()})
        with pytest.raises(ValueError, match="target configuration"):
            transfer.model_copy(
                update={"target_configuration": KevinSpeakConfiguration(max_entry_bytes=2048)}
            )

    with SQLiteLedger(review_path) as restored_review_ledger:
        restored_review = KevinSpeakWorkspace.restore(
            restored_review_ledger, workspace_id="successor-review"
        )
        assert len(restored_review.recommendations) == 1
        assert len(restored_review.reviews) == 1
        assert len(restored_review.adoption_decisions) == 1
        assert restored_review.verify().exact_round_trips

    target_path = tmp_path / "target.sqlite3"
    with SQLiteLedger(target_path) as target_ledger:
        target = KevinSpeakWorkspace.open_inherited(
            target_ledger,
            workspace_id="target",
            target_stage_ref=ref("successor-stage"),
            transfer=transfer,
        )
        assert target.translation_table().codebook_ref == refined.digest
        assert target.configuration.presentation_mode is KevinPresentationMode.MODEL_FACING
        assert len(target.recommendations) == 1
        assert len(target.reviews) == 1
        assert len(target.adoption_decisions) == 1
        assert target_ledger.has_object(rejected_ref)
        assert target_ledger.has_object(deferred_ref)
        assert any(
            translation.expansion == refined_phrase
            for translation in target.translation_table().translations
        )
        inherited_entry = target.append(
            entry_id="inherited-use",
            payload=repetitive_payload("target", recursive_phrase),
        )
        assert inherited_entry.lane is ShorthandLane.COMPACT
        target_verification = target.verify()
        assert target_verification.exact_round_trips

        with pytest.raises(KevinSpeakError, match="another target stage"):
            KevinSpeakWorkspace.open_inherited(
                target_ledger,
                workspace_id="wrong-target",
                target_stage_ref=ref("different-stage"),
                transfer=transfer,
            )

    with SQLiteLedger(target_path) as restored_target_ledger:
        restored_target = KevinSpeakWorkspace.restore(
            restored_target_ledger, workspace_id="target"
        )
        assert restored_target.verify() == target_verification
        assert restored_target.adoption_decisions == (transfer.adoption,)
        assert {
            item.definition_ref
            for item in restored_target.adoption_decisions[0].definition_decisions
            if item.disposition.value in {"reject", "defer"}
        } == {rejected_ref, deferred_ref}


def test_rejected_next_round_shorthand_cannot_transfer(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "rejected-transfer.sqlite3") as ledger:
        workspace = KevinSpeakWorkspace.open_blank(ledger, workspace_id="rejected-transfer")
        recommendation = workspace.recommend_next_round(
            recommendation_id="blank-recommendation",
            recommending_driver_ref=ref("blank-model-driver"),
            evaluation_refs=(),
            rationale="preserve the blank baseline for explicit review",
        )
        rejection = workspace.decide_next_round_adoption(
            adoption_id="reject-blank",
            recommendation_ref=recommendation.digest,
            target_stage_ref=ref("next-stage"),
            control_source_ref=ref("campaign-control"),
            approve=False,
            rationale="the next stage will start another blank shorthand surface",
        )

        with pytest.raises(KevinSpeakError, match="rejected shorthand"):
            workspace.export_transfer(
                transfer_id="forbidden-transfer", adoption_ref=rejection.digest
            )
