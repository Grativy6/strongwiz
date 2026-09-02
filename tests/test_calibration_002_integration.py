from __future__ import annotations

from pathlib import Path

from calibration.models import load_preregistration
from strongwiz.canonical import parse_strict_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
V2_COMMIT = "1e6c2478cbb4f4168d8ec8857b65b88af1fd499a"
V2_TREE = "83172b2de0712af0e591486ff3e6d0550cea5691"


def test_calibration_001_identity_remains_explicitly_pinned() -> None:
    loaded = load_preregistration(
        REPOSITORY_ROOT,
        REPOSITORY_ROOT / "docs/calibrations/001-preregistration.json",
    )

    assert loaded.preregistration.toolbelt.commit == (
        "a85508dc11cc6ac30336f5c42344b62afdc86b24"
    )
    assert loaded.preregistration.toolbelt.tree == (
        "9e58cb361919fca3638b1f76a00379740c4e4aa4"
    )


def test_calibration_002_stage_preregistrations_preserve_aggregate_match() -> None:
    expected = (
        (1800, 128, 4, 132),
        (3600, 256, 8, 264),
        (5400, 384, 12, 396),
        (18000, 1280, 40, 1320),
    )
    observed: list[tuple[int, int, int, int]] = []
    for stage in range(1, 5):
        loaded = load_preregistration(
            REPOSITORY_ROOT,
            REPOSITORY_ROOT
            / f"docs/calibrations/002-stage-{stage}-preregistration.json",
        )
        prereg = loaded.preregistration
        assert prereg.toolbelt.commit == V2_COMMIT
        assert prereg.toolbelt.tree == V2_TREE
        assert prereg.evaluation.exact_versioned_game_id == "ls20-9607627b"
        observed.append(
            (
                prereg.budgets.wall_clock_seconds,
                prereg.budgets.maximum_non_reset_actions,
                prereg.budgets.maximum_resets,
                prereg.budgets.maximum_total_environment_calls,
            )
        )

    assert tuple(observed) == expected
    assert sum(item[0] for item in observed) == 28800
    assert sum(item[1] for item in observed) == 2048
    assert sum(item[2] for item in observed) == 64
    assert sum(item[3] for item in observed) == 2112


def test_campaign_preregistration_forbids_domain_and_action_transfer() -> None:
    raw = parse_strict_json(
        (REPOSITORY_ROOT / "docs/calibrations/002-preregistration.json").read_bytes()
    )
    assert isinstance(raw, dict)
    transfer = raw["transfer_policy"]
    assert isinstance(transfer, dict)
    forbidden = transfer["forbidden"]
    assert isinstance(forbidden, list)
    assert "action sequences" in forbidden
    assert "domain state" in forbidden
    assert "private reasoning" in forbidden
    assert "authorization" in forbidden
