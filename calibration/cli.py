"""Command line for the run-local ARC-AGI-3 calibration integration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calibration.core import OfficialAssetAcquirer
from calibration.models import load_preregistration
from calibration.server import CalibrationControlServer, send_command
from calibration.workflow import (
    CalibrationHarness,
    pack_run,
    prepare_run,
    seal_prepared_run,
)
from strongwiz.canonical import canonical_text, deep_thaw_json, parse_strict_json


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} must stay under {root}") from error
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m calibration")
    commands = parser.add_subparsers(dest="command", required=True)

    acquire = commands.add_parser("acquire", help="download/hash ls20 without making it")
    acquire.add_argument("assets_root", type=Path)

    prepare = commands.add_parser("prepare", help="freeze inputs and create empty genesis")
    prepare.add_argument("run_root", type=Path)
    prepare.add_argument("assets_root", type=Path)
    prepare.add_argument("--run-id", required=True)

    serve = commands.add_parser("serve", help="start the loopback single-writer server")
    serve.add_argument("run_root", type=Path)
    serve.add_argument("assets_root", type=Path)
    serve.add_argument("--endpoint", type=Path, required=True)
    serve.add_argument("--port", type=int, default=0)

    status = commands.add_parser("status", help="read current frame/request status")
    status.add_argument("endpoint", type=Path)

    act = commands.add_parser("act", help="submit one externally authored ProposalDraft")
    act.add_argument("endpoint", type=Path)
    act.add_argument("--input", dest="draft", type=Path, required=True)

    assess = commands.add_parser("assess", help="submit one concise AssessmentDraft")
    assess.add_argument("endpoint", type=Path)
    assess.add_argument("--input", dest="draft", type=Path, required=True)

    stop = commands.add_parser("stop", help="stop without manufacturing completion")
    stop.add_argument("endpoint", type=Path)
    stop.add_argument("--summary", required=True)

    seal = commands.add_parser("seal", help="seal an immutable terminal run")
    seal.add_argument("run_root", type=Path)

    capsule = commands.add_parser("capsule", help="pack and verify the evidence capsule")
    capsule.add_argument("run_root", type=Path)
    capsule.add_argument("capsule_root", type=Path)
    capsule.add_argument("--receipt", type=Path, required=True)
    return parser


def _load_object(repository_root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to((repository_root / "playground").resolve())
    except ValueError as error:
        raise ValueError("model-authored drafts must stay under ignored playground/") from error
    value = deep_thaw_json(parse_strict_json(resolved.read_bytes()))
    if not isinstance(value, dict):
        raise ValueError("draft JSON must be an object")
    return dict(value)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = _repository_root()
    result: object
    if args.command == "acquire":
        _require_under(args.assets_root, repository_root / "artifacts/local", "asset root")
        prereg = load_preregistration(
            repository_root,
            repository_root / "docs/calibrations/001-preregistration.json",
        )
        if not prereg.preregistration.access.anonymous_public_api_authorized:
            raise RuntimeError("anonymous official acquisition is not authorized")
        result = OfficialAssetAcquirer().acquire(args.assets_root)
    elif args.command == "prepare":
        _require_under(args.run_root, repository_root / "playground", "run root")
        _require_under(args.assets_root, repository_root / "artifacts/local", "asset root")
        result = prepare_run(
            repository_root=repository_root,
            run_root=args.run_root,
            assets_root=args.assets_root,
            run_id=args.run_id,
        )
    elif args.command == "serve":
        _require_under(args.run_root, repository_root / "playground", "run root")
        _require_under(args.assets_root, repository_root / "artifacts/local", "asset root")
        _require_under(args.endpoint, repository_root / "playground", "control endpoint")
        harness = CalibrationHarness(
            repository_root=repository_root,
            run_root=args.run_root,
            assets_root=args.assets_root,
        )
        server = CalibrationControlServer(harness)
        print(
            canonical_text(
                {
                    "endpoint": str(args.endpoint.resolve()),
                    "event": "server_starting",
                    "transport": "SWZJ-v1-length-prefixed-canonical-json",
                }
            ),
            flush=True,
        )
        server.serve(args.endpoint, port=args.port)
        return 0
    elif args.command == "status":
        _require_under(args.endpoint, repository_root / "playground", "control endpoint")
        result = send_command(args.endpoint, kind="status")
    elif args.command == "act":
        _require_under(args.endpoint, repository_root / "playground", "control endpoint")
        result = send_command(
            args.endpoint,
            kind="act",
            payload=_load_object(repository_root, args.draft),
        )
    elif args.command == "assess":
        _require_under(args.endpoint, repository_root / "playground", "control endpoint")
        result = send_command(
            args.endpoint,
            kind="assess",
            payload=_load_object(repository_root, args.draft),
        )
    elif args.command == "stop":
        _require_under(args.endpoint, repository_root / "playground", "control endpoint")
        result = send_command(
            args.endpoint,
            kind="stop",
            payload={"summary": args.summary},
        )
    elif args.command == "seal":
        _require_under(args.run_root, repository_root / "playground", "run root")
        result = seal_prepared_run(args.run_root)
    elif args.command == "capsule":
        _require_under(args.run_root, repository_root / "playground", "run root")
        _require_under(
            args.capsule_root,
            repository_root / "artifacts/local",
            "capsule root",
        )
        _require_under(args.receipt, repository_root / "artifacts/local", "run receipt")
        result = pack_run(
            run_root=args.run_root,
            capsule_root=args.capsule_root,
            delivery_receipt_path=args.receipt,
        )
    else:  # pragma: no cover - argparse prevents this branch
        raise AssertionError("unknown command")
    print(canonical_text(result))
    if isinstance(result, dict) and result.get("ok") is False:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
