"""Preparation-only command line for Strongwiz v3 calibration design."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calibration_003.models import Calibration003Plan, calibration_003_schema_bundle
from calibration_003.workflow import (
    LoadedV2CarryPacket,
    load_plan,
    load_v2_carry_packet,
    prepare_campaign,
    run_synthetic_preflight,
    verify_campaign,
)
from strongwiz.canonical import canonical_text


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError(f"{label} must stay under {root}") from error
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m calibration_003")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the closed preparation schemas")

    prepare = commands.add_parser("prepare", help="create two matched zero-state shadow labs")
    prepare.add_argument("plan", type=Path)
    prepare.add_argument("campaign_root", type=Path)
    prepare.add_argument(
        "--carry-packet",
        type=Path,
        help="exact repository-local v2 packet required when the plan declares one",
    )

    verify = commands.add_parser("verify", help="verify both labs remain matched zero-state")
    verify.add_argument("plan", type=Path)
    verify.add_argument("campaign_root", type=Path)
    verify.add_argument(
        "--carry-packet",
        type=Path,
        help="exact repository-local v2 packet required when the plan declares one",
    )

    preflight = commands.add_parser(
        "synthetic-preflight",
        help="exercise the scribe with deterministic synthetic summaries only",
    )
    preflight.add_argument("preflight_root", type=Path)
    return parser


def _load_declared_carry(
    plan: Calibration003Plan,
    packet_path: Path | None,
    repository_root: Path,
) -> LoadedV2CarryPacket | None:
    expected_ref = plan.v2_carry_evidence_ref
    if expected_ref is None:
        if packet_path is not None:
            raise ValueError("fresh campaign does not declare a v2 carry packet")
        return None
    if packet_path is None:
        raise ValueError("--carry-packet is required by the supplied campaign plan")
    return load_v2_carry_packet(
        packet_path,
        repository_root,
        expected_ref=expected_ref,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = _repository_root()
    playground_root = repository_root / "playground"
    result: object
    if args.command == "schema":
        result = calibration_003_schema_bundle()
    elif args.command == "prepare":
        plan_path = _require_under(args.plan, repository_root, "campaign plan")
        plan = load_plan(plan_path)
        carry_packet = _load_declared_carry(
            plan,
            args.carry_packet,
            repository_root,
        )
        campaign_root = _require_under(
            args.campaign_root,
            playground_root,
            "campaign root",
        )
        result = prepare_campaign(campaign_root, plan, carry_packet=carry_packet)
    elif args.command == "verify":
        plan_path = _require_under(args.plan, repository_root, "campaign plan")
        plan = load_plan(plan_path)
        carry_packet = _load_declared_carry(
            plan,
            args.carry_packet,
            repository_root,
        )
        campaign_root = _require_under(
            args.campaign_root,
            playground_root,
            "campaign root",
        )
        result = verify_campaign(campaign_root, plan, carry_packet=carry_packet)
    elif args.command == "synthetic-preflight":
        preflight_root = _require_under(
            args.preflight_root,
            playground_root,
            "synthetic preflight root",
        )
        result = run_synthetic_preflight(preflight_root)
    else:  # pragma: no cover
        raise AssertionError("unknown command")
    print(canonical_text(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
