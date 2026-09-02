from __future__ import annotations

from pathlib import Path

from examples.kevin_speak_campaign import run_demo


def test_non_arc_kevin_speak_campaign_is_sealed_and_explicit(tmp_path: Path) -> None:
    summary = run_demo(tmp_path / "kevin-demo")

    assert summary["schema"] == "strongwiz.kevin-speak-demo.v1"
    assert summary["source_compact"] == "compact"
    assert summary["successor_compact"] == "compact"
    assert summary["recommendation_status"] == "recommended_not_approved"
    assert summary["review_status"] == "reviewed_not_adopted"
    assert summary["adoption_status"] == "approved"
    assert summary["successor_mode"] == "model_facing"
    assert summary["model_facing_behavior_evaluated"] is False
    assert summary["completion_genuinely_observed"] is False
    assert (tmp_path / "kevin-demo" / "summary.json").is_file()
