"""Small local CLI for schema discovery, shadow routing, and ledger audit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from strongwiz.canonical import canonical_text, parse_strict_json
from strongwiz.contracts import (
    CandidateProposal,
    ControlSnapshot,
    contract_json_schema,
    contract_schema_bundle,
)
from strongwiz.ledger import SQLiteLedger
from strongwiz.routing import evaluate_proposal


class _RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal: CandidateProposal
    control: ControlSnapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="strongwiz")
    subcommands = parser.add_subparsers(dest="command", required=True)
    schema = subcommands.add_parser("schema", help="print the model-driver request schema")
    schema.add_argument(
        "--all", action="store_true", help="print all declared cross-boundary schemas"
    )
    schema.set_defaults(handler=_schema)
    route = subcommands.add_parser("route", help="evaluate one nonexecuting route request")
    route.add_argument("request", type=Path)
    route.set_defaults(handler=_route)
    verify = subcommands.add_parser("verify-ledger", help="verify one Strongwiz SQLite ledger")
    verify.add_argument("ledger", type=Path)
    verify.add_argument("--expected-count", type=int)
    verify.add_argument("--expected-head")
    verify.set_defaults(handler=_verify_ledger)
    return parser


def _schema(args: argparse.Namespace) -> int:
    value = contract_schema_bundle() if args.all else contract_json_schema()
    print(canonical_text(value))
    return 0


def _route(args: argparse.Namespace) -> int:
    raw = parse_strict_json(args.request.read_bytes())
    request = _RouteInput.model_validate(raw)
    decision = evaluate_proposal(request.proposal, request.control)
    print(canonical_text(decision))
    return 0 if decision.selected_proposal_id is not None else 2


def _verify_ledger(args: argparse.Namespace) -> int:
    with SQLiteLedger(args.ledger, readonly=True) as ledger:
        count, head = ledger.verify(
            expected_count=args.expected_count,
            expected_head=args.expected_head,
        )
        externally_sealed = args.expected_count is not None and args.expected_head is not None
        result = {
            "externally_sealed": externally_sealed,
            "head": head,
            "limitations": []
            if externally_sealed
            else ["tail truncation is not excluded without expected count and head"],
            "receipt_count": count,
            "valid": True,
        }
    print(canonical_text(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
