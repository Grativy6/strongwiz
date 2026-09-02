"""Synthetic non-ARC demonstration of sealed Kevin Speak succession.

This example exercises representation mechanics only. It contains no domain
policy, environment action, or performance claim.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.ledger import SQLiteLedger
from strongwiz.shorthand import (
    EvaluationRole,
    KevinEvaluationSample,
    KevinPresentationMode,
    KevinSpeakConfiguration,
    KevinSpeakWorkspace,
    KevinSymbolProposal,
)


def _payload(label: str, phrase: str, *, repetitions: int = 96) -> dict[str, object]:
    return {
        "label": label,
        "provisional_mechanics": [phrase for _ in range(repetitions)],
        "status": "candidate",
    }


def run_demo(root: Path) -> dict[str, object]:
    """Run one source/review/successor chain inside ``root``."""

    root.mkdir(parents=True, exist_ok=False)
    phrase = "bounded-resource-transition-with-reopen-handle"
    source_adaptation = _payload("source-adaptation", phrase)
    source_validation = _payload("source-validation", phrase)
    source_path = root / "source.sqlite3"

    with SQLiteLedger(source_path) as ledger:
        source = KevinSpeakWorkspace.open_blank(ledger, workspace_id="demo-source")
        candidate = source.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="br",
                    expansion=phrase,
                    concise_meaning="bounded resource transition with reopening handle",
                    source_payload_refs=(content_hash(source_adaptation),),
                ),
            ),
            rationale="abbreviate a repeated mechanically scoped phrase",
            model_proposal_ref=content_hash("demo-source-model-proposal"),
        )
        evaluation = source.evaluate_candidate(
            candidate.digest,
            (
                KevinEvaluationSample(
                    case_id="source-adaptation",
                    role=EvaluationRole.ADAPTATION,
                    payload=source_adaptation,
                ),
                KevinEvaluationSample(
                    case_id="source-validation",
                    role=EvaluationRole.VALIDATION,
                    payload=source_validation,
                ),
            ),
            evaluation_id="source-codebook-evaluation",
        )
        source.promote(
            candidate_ref=candidate.digest,
            evaluation_ref=evaluation.digest,
        )
        source_entry = source.append(
            entry_id="source-working-entry",
            payload=_payload("source-working", phrase),
        )
        recommendation = source.recommend_next_round(
            recommendation_id="source-next-round-recommendation",
            recommending_driver_ref=content_hash("demo-source-model-driver"),
            evaluation_refs=(evaluation.digest,),
            rationale="recommend the earned codebook for isolated successor review",
            known_residuals=("unseen task vocabulary remains in the residual lane",),
        )
        source_verification = source.verify()

    with SQLiteLedger(source_path, readonly=True) as ledger:
        sealed_source = KevinSpeakWorkspace.restore(ledger, workspace_id="demo-source")
        bundle = sealed_source.export_recommendation_bundle(
            recommendation_ref=recommendation.digest,
            source_run_seal_ref=content_hash("demo-source-run-seal"),
            source_capsule_ref=content_hash("demo-source-evidence-capsule"),
        )

    recursive_phrase = f"{phrase}:{phrase}:access-change"
    review_adaptation = _payload("review-adaptation", recursive_phrase)
    review_validation = _payload("review-validation", recursive_phrase)
    review_path = root / "review.sqlite3"
    model_facing = KevinSpeakConfiguration(presentation_mode=KevinPresentationMode.MODEL_FACING)
    with SQLiteLedger(review_path) as ledger:
        review_workspace = KevinSpeakWorkspace.open_review(
            ledger,
            workspace_id="demo-review",
            bundle=bundle,
            configuration=model_facing,
        )
        refinement = review_workspace.propose_revision(
            proposals=(
                KevinSymbolProposal(
                    token="bra",
                    expansion=recursive_phrase,
                    concise_meaning="two bounded transitions followed by access change",
                    source_payload_refs=(content_hash(review_adaptation),),
                ),
            ),
            rationale="compose the source token into one validated successor candidate",
            model_proposal_ref=content_hash("demo-review-model-proposal"),
        )
        refinement_evaluation = review_workspace.evaluate_candidate(
            refinement.digest,
            (
                KevinEvaluationSample(
                    case_id="review-adaptation",
                    role=EvaluationRole.ADAPTATION,
                    payload=review_adaptation,
                ),
                KevinEvaluationSample(
                    case_id="review-validation",
                    role=EvaluationRole.VALIDATION,
                    payload=review_validation,
                ),
            ),
            evaluation_id="review-codebook-evaluation",
        )
        review = review_workspace.review_next_round(
            review_id="stronger-model-review",
            recommendation_ref=recommendation.digest,
            reviewer_driver_ref=content_hash("demo-review-model-driver"),
            evaluation_refs=(refinement_evaluation.digest,),
            rationale="recommend the source vocabulary plus the validated refinement",
            reviewed_codebook_ref=refinement.digest,
        )
        adoption = review_workspace.decide_next_round_adoption(
            adoption_id="demo-successor-adoption",
            recommendation_ref=recommendation.digest,
            review_ref=review.digest,
            target_stage_ref=content_hash("demo-successor-stage"),
            control_source_ref=content_hash("demo-supplied-control-policy"),
            approve=True,
            rationale="admit this exact working representation for the successor only",
        )
        transfer = review_workspace.export_transfer(
            transfer_id="demo-sealed-shorthand-transfer",
            adoption_ref=adoption.digest,
        )
        review_verification = review_workspace.verify()

    target_path = root / "successor.sqlite3"
    with SQLiteLedger(target_path) as ledger:
        successor = KevinSpeakWorkspace.open_inherited(
            ledger,
            workspace_id="demo-successor",
            target_stage_ref=content_hash("demo-successor-stage"),
            transfer=transfer,
        )
        target_entry = successor.append(
            entry_id="successor-working-entry",
            payload=_payload("successor-working", recursive_phrase),
        )
        target_verification = successor.verify()

    summary: dict[str, object] = {
        "adoption_status": adoption.status.value,
        "claim_ceiling": "synthetic representation-mechanism demonstration only",
        "completion_genuinely_observed": False,
        "model_facing_behavior_evaluated": False,
        "recommendation_status": recommendation.status,
        "review_status": review.status,
        "schema": "strongwiz.kevin-speak-demo.v1",
        "source_compact": source_entry.lane.value,
        "source_verification_ref": source_verification.digest,
        "successor_compact": target_entry.lane.value,
        "successor_configuration_ref": successor.configuration.digest,
        "successor_mode": successor.configuration.presentation_mode.value,
        "successor_verification_ref": target_verification.digest,
        "transfer_ref": transfer.digest,
        "transfer_size_bytes": len(canonical_bytes(transfer)),
        "review_verification_ref": review_verification.digest,
    }
    (root / "summary.json").write_bytes(canonical_bytes(summary) + b"\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(canonical_bytes(run_demo(args.root)).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
