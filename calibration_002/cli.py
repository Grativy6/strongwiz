"""Run-local command line for Calibration 002 without changing Calibration 001."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calibration.core import OfficialAssetAcquirer
from calibration.models import load_preregistration
from calibration.server import CalibrationControlServer, send_command
from calibration.workflow import CalibrationHarness, pack_run, prepare_run, seal_prepared_run
from calibration_002.learning import Calibration002LearningSidecar
from strongwiz.canonical import canonical_text, content_hash, deep_thaw_json, parse_strict_json
from strongwiz.shorthand import KevinEvaluationSample, KevinSymbolProposal

STAGE_PREREGISTRATIONS = {
    stage: Path(f"docs/calibrations/002-stage-{stage}-preregistration.json")
    for stage in range(1, 5)
}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} must stay under {root}") from error
    return resolved


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


def _object_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    values = _object_list(value, label)
    if not all(isinstance(item, str) for item in values):
        raise ValueError(f"{label} must contain only strings")
    return tuple(item for item in values if isinstance(item, str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m calibration_002")
    commands = parser.add_subparsers(dest="command", required=True)

    acquire = commands.add_parser("acquire", help="download/hash the preregistered ls20 asset")
    acquire.add_argument("assets_root", type=Path)

    prepare = commands.add_parser("prepare", help="create one fresh campaign-stage lab")
    prepare.add_argument("run_root", type=Path)
    prepare.add_argument("assets_root", type=Path)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--stage", type=int, choices=range(1, 5), required=True)

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

    seal = commands.add_parser("seal", help="seal an immutable terminal stage run")
    seal.add_argument("run_root", type=Path)

    capsule = commands.add_parser("capsule", help="pack and verify the stage evidence capsule")
    capsule.add_argument("run_root", type=Path)
    capsule.add_argument("capsule_root", type=Path)
    capsule.add_argument("--receipt", type=Path, required=True)

    learn_create = commands.add_parser(
        "learn-create", help="create the persistent adaptive-campaign learning ledger"
    )
    learn_create.add_argument("ledger", type=Path)

    learn_open = commands.add_parser(
        "learn-open", help="open stage 1 with a blank Kevin Speak workspace"
    )
    learn_open.add_argument("ledger", type=Path)
    learn_open.add_argument("--run-id", required=True)

    learn_table = commands.add_parser("learn-table", help="show active translations")
    learn_table.add_argument("ledger", type=Path)

    learn_append = commands.add_parser(
        "learn-append", help="append one derived, non-authoritative working payload"
    )
    learn_append.add_argument("ledger", type=Path)
    learn_append.add_argument("--input", type=Path, required=True)

    learn_adapt = commands.add_parser(
        "learn-adapt", help="evaluate and conditionally promote a shorthand revision"
    )
    learn_adapt.add_argument("ledger", type=Path)
    learn_adapt.add_argument("--input", type=Path, required=True)

    learn_recommend = commands.add_parser(
        "learn-recommend", help="record the model's advisory next-stage shorthand"
    )
    learn_recommend.add_argument("ledger", type=Path)
    learn_recommend.add_argument("--input", type=Path, required=True)

    learn_verify = commands.add_parser(
        "learn-verify", help="verify the campaign learning ledger and exact round trips"
    )
    learn_verify.add_argument("ledger", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = _repository_root()
    result: object
    if args.command == "acquire":
        _require_under(args.assets_root, repository_root / "artifacts/local", "asset root")
        prereg = load_preregistration(
            repository_root,
            repository_root / STAGE_PREREGISTRATIONS[1],
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
            preregistration_relative=STAGE_PREREGISTRATIONS[args.stage],
            package_version="0.3.0.dev0",
            task_id=f"calibration-002-stage-{args.stage}-official-public-ls20",
            lab_id=f"strongwiz-arc3-calibration-002-stage-{args.stage}",
            lab_version="2",
            lab_purpose=(
                "One fresh stage of the preregistered Codex-operated Strongwiz v2 "
                "local-public ARC-AGI-3 adaptive calibration."
            ),
            integration_packages=("calibration", "calibration_002"),
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
        result = send_command(args.endpoint, kind="stop", payload={"summary": args.summary})
    elif args.command == "seal":
        _require_under(args.run_root, repository_root / "playground", "run root")
        result = seal_prepared_run(args.run_root)
    elif args.command == "capsule":
        _require_under(args.run_root, repository_root / "playground", "run root")
        _require_under(args.capsule_root, repository_root / "artifacts/local", "capsule root")
        _require_under(args.receipt, repository_root / "artifacts/local", "run receipt")
        result = pack_run(
            run_root=args.run_root,
            capsule_root=args.capsule_root,
            delivery_receipt_path=args.receipt,
        )
    elif args.command == "learn-create":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        with Calibration002LearningSidecar.create(
            args.ledger,
            success_condition_ref=content_hash(
                {
                    "authoritative_state": "GameState.WIN",
                    "exact_game_id": "ls20-9607627b",
                }
            ),
            campaign_id="calibration-002",
            objective="reach official GameState.WIN on exact public ls20-9607627b",
            final_authority_source="pinned arcengine.GameState enum",
        ) as learning:
            result = learning.verify()
    elif args.command == "learn-open":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.open_stage(run_id=args.run_id)
    elif args.command == "learn-table":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.table()
    elif args.command == "learn-append":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        payload = _load_object(repository_root, args.input)
        entry_id = payload.pop("entry_id", None)
        value = payload.pop("payload", None)
        if not isinstance(entry_id, str) or value is None or payload:
            raise ValueError("learn-append requires only entry_id and payload")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.append(entry_id=entry_id, payload=value)
    elif args.command == "learn-adapt":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        payload = _load_object(repository_root, args.input)
        proposals = tuple(
            KevinSymbolProposal.model_validate(value)
            for value in _object_list(payload.pop("proposals", None), "proposals")
        )
        samples = tuple(
            KevinEvaluationSample.model_validate(value)
            for value in _object_list(payload.pop("samples", None), "samples")
        )
        rationale = payload.pop("rationale", None)
        evaluation_id = payload.pop("evaluation_id", None)
        retired_tokens = _string_tuple(payload.pop("retired_tokens", []), "retired_tokens")
        model_proposal_ref = payload.pop("model_proposal_ref", None)
        if not isinstance(rationale, str) or not isinstance(evaluation_id, str) or payload:
            raise ValueError("learn-adapt has missing or unexpected fields")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.adapt(
                proposals=proposals,
                samples=samples,
                rationale=rationale,
                evaluation_id=evaluation_id,
                retired_tokens=retired_tokens,
                model_proposal_ref=(
                    model_proposal_ref if isinstance(model_proposal_ref, str) else None
                ),
            )
    elif args.command == "learn-recommend":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        payload = _load_object(repository_root, args.input)
        recommendation_id = payload.pop("recommendation_id", None)
        rationale = payload.pop("rationale", None)
        evaluation_refs = _string_tuple(payload.pop("evaluation_refs", []), "evaluation_refs")
        known_residuals = _string_tuple(payload.pop("known_residuals", []), "known_residuals")
        if not isinstance(recommendation_id, str) or not isinstance(rationale, str) or payload:
            raise ValueError("learn-recommend has missing or unexpected fields")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.recommend(
                recommendation_id=recommendation_id,
                recommending_driver_ref=content_hash(
                    {
                        "interface": "calibration_002/model-interface.json",
                        "hosted_weights_bound": False,
                    }
                ),
                evaluation_refs=evaluation_refs,
                rationale=rationale,
                known_residuals=known_residuals,
            )
    elif args.command == "learn-verify":
        _require_under(args.ledger, repository_root / "playground", "learning ledger")
        with Calibration002LearningSidecar.restore(args.ledger) as learning:
            result = learning.verify()
    else:  # pragma: no cover
        raise AssertionError("unknown command")
    print(canonical_text(result))
    if isinstance(result, dict) and result.get("ok") is False:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
