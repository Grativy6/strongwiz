"""Local CLI for contracts, lab genesis, sealing, and evidence audit."""

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
from strongwiz.lab import (
    LabManifest,
    RunDisposition,
    RunSpec,
    initialize_lab,
    pack_evidence,
    seal_run,
    verify_evidence_capsule,
    verify_lab,
)
from strongwiz.ledger import SQLiteLedger
from strongwiz.pal23 import pal23_schema_bundle
from strongwiz.provenance import load_source_registry
from strongwiz.routing import evaluate_proposal
from strongwiz.scribe import scribe_schema_bundle
from strongwiz.shorthand import KevinSpeakWorkspace, kevin_speak_schema_bundle


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
    source = subcommands.add_parser(
        "verify-sources", help="verify a Strongwiz source-identity registry"
    )
    source.add_argument("registry", type=Path)
    source.set_defaults(handler=_verify_sources)

    kevin = subcommands.add_parser(
        "kevin", help="initialize or audit an adaptive Kevin Speak working ledger"
    )
    kevin_commands = kevin.add_subparsers(dest="kevin_command", required=True)
    kevin_schema = kevin_commands.add_parser(
        "schema", help="print the declarative Kevin Speak boundary schemas"
    )
    kevin_schema.set_defaults(handler=_kevin_schema)
    kevin_init = kevin_commands.add_parser("init", help="open one blank shorthand surface")
    kevin_init.add_argument("ledger", type=Path)
    kevin_init.add_argument("--workspace-id", required=True)
    kevin_init.add_argument("--account-id")
    kevin_init.add_argument("--account-version", type=int, default=0)
    kevin_init.set_defaults(handler=_kevin_init)
    kevin_verify = kevin_commands.add_parser(
        "verify", help="reconstruct and verify one shorthand workspace"
    )
    kevin_verify.add_argument("ledger", type=Path)
    kevin_verify.add_argument("--workspace-id", required=True)
    kevin_verify.add_argument("--account-id")
    kevin_verify.add_argument("--account-version", type=int, default=0)
    kevin_verify.set_defaults(handler=_kevin_verify)
    kevin_table = kevin_commands.add_parser(
        "table", help="print the active reversible translation table"
    )
    kevin_table.add_argument("ledger", type=Path)
    kevin_table.add_argument("--workspace-id", required=True)
    kevin_table.add_argument("--account-id")
    kevin_table.add_argument("--account-version", type=int, default=0)
    kevin_table.set_defaults(handler=_kevin_table)

    pal23 = subcommands.add_parser("pal23", help="inspect the bounded PAL v2.3 adapter surface")
    pal23_commands = pal23.add_subparsers(dest="pal23_command", required=True)
    pal23_schema = pal23_commands.add_parser(
        "schema", help="print the targeted PAL v2.3 adapter schemas"
    )
    pal23_schema.set_defaults(handler=_pal23_schema)

    scribe = subcommands.add_parser(
        "scribe", help="inspect the representation-only scribe boundary"
    )
    scribe_commands = scribe.add_subparsers(dest="scribe_command", required=True)
    scribe_schema = scribe_commands.add_parser(
        "schema", help="print the representation-only scribe schemas"
    )
    scribe_schema.set_defaults(handler=_scribe_schema)

    lab = subcommands.add_parser("lab", help="create and audit sealed laboratories")
    lab_commands = lab.add_subparsers(dest="lab_command", required=True)
    lab_init = lab_commands.add_parser("init", help="initialize a zero-state laboratory")
    lab_init.add_argument("root", type=Path)
    lab_init.add_argument("--manifest", type=Path, required=True)
    lab_init.add_argument("--run-spec", type=Path, required=True)
    lab_init.set_defaults(handler=_lab_init)
    lab_verify = lab_commands.add_parser("verify", help="verify a laboratory")
    lab_verify.add_argument("root", type=Path)
    lab_verify.add_argument(
        "--require-genesis",
        action="store_true",
        help="also require that the lab still has exactly zero run state",
    )
    lab_verify.set_defaults(handler=_lab_verify)
    lab_seal = lab_commands.add_parser("seal-run", help="seal one terminal disposition")
    lab_seal.add_argument("root", type=Path)
    lab_seal.add_argument("--disposition", required=True, choices=tuple(RunDisposition))
    lab_seal.add_argument("--terminal-state", required=True)
    lab_seal.add_argument("--terminal-evidence-ref", required=True)
    lab_seal.add_argument("--summary", required=True)
    lab_seal.add_argument("--completion-genuinely-observed", action="store_true")
    lab_seal.set_defaults(handler=_lab_seal)
    lab_pack = lab_commands.add_parser(
        "pack-evidence", help="pack a complete portable evidence capsule"
    )
    lab_pack.add_argument("root", type=Path)
    lab_pack.add_argument("destination", type=Path)
    lab_pack.add_argument("--capsule-name")
    lab_pack.add_argument(
        "--acknowledge-opaque-domain-state",
        action="store_true",
        help=(
            "acknowledge that domain-state bytes are copied without privacy, secret, "
            "or publication review"
        ),
    )
    lab_pack.set_defaults(handler=_lab_pack)
    capsule_verify = lab_commands.add_parser(
        "verify-capsule", help="verify one portable evidence capsule"
    )
    capsule_verify.add_argument("capsule", type=Path)
    capsule_verify.add_argument("--expected-capsule-ref")
    capsule_verify.set_defaults(handler=_capsule_verify)
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


def _load_contract(path: Path, model: type[BaseModel]) -> BaseModel:
    raw = parse_strict_json(path.read_bytes())
    return model.model_validate(raw)


def _lab_init(args: argparse.Namespace) -> int:
    manifest = _load_contract(args.manifest, LabManifest)
    run_spec = _load_contract(args.run_spec, RunSpec)
    assert isinstance(manifest, LabManifest)
    assert isinstance(run_spec, RunSpec)
    genesis = initialize_lab(args.root, manifest=manifest, run_spec=run_spec)
    print(canonical_text(genesis))
    return 0


def _lab_verify(args: argparse.Namespace) -> int:
    result = verify_lab(args.root, require_current_genesis=args.require_genesis)
    print(canonical_text(result))
    return 0


def _lab_seal(args: argparse.Namespace) -> int:
    result = seal_run(
        args.root,
        disposition=RunDisposition(args.disposition),
        terminal_state=args.terminal_state,
        terminal_evidence_ref=args.terminal_evidence_ref,
        completion_genuinely_observed=args.completion_genuinely_observed,
        concise_result_summary=args.summary,
    )
    print(canonical_text(result))
    return 0


def _lab_pack(args: argparse.Namespace) -> int:
    result = pack_evidence(
        args.root,
        args.destination,
        capsule_name=args.capsule_name,
        acknowledge_opaque_domain_state=args.acknowledge_opaque_domain_state,
    )
    print(canonical_text(result))
    return 0


def _capsule_verify(args: argparse.Namespace) -> int:
    result = verify_evidence_capsule(
        args.capsule,
        expected_capsule_ref=args.expected_capsule_ref,
    )
    print(canonical_text(result))
    return 0


def _verify_sources(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.registry)
    print(
        canonical_text(
            {
                "registry_ref": registry.digest,
                "source_count": len(registry.sources),
                "valid": True,
            }
        )
    )
    return 0


def _kevin_init(args: argparse.Namespace) -> int:
    with SQLiteLedger(args.ledger) as ledger:
        workspace = KevinSpeakWorkspace.open_blank(
            ledger,
            workspace_id=args.workspace_id,
            account_id=args.account_id,
            account_version=args.account_version,
        )
        result = workspace.verify()
    print(canonical_text(result))
    return 0


def _kevin_schema(_args: argparse.Namespace) -> int:
    print(canonical_text(kevin_speak_schema_bundle()))
    return 0


def _pal23_schema(_args: argparse.Namespace) -> int:
    print(canonical_text(pal23_schema_bundle()))
    return 0


def _scribe_schema(_args: argparse.Namespace) -> int:
    print(canonical_text(scribe_schema_bundle()))
    return 0


def _restore_kevin(args: argparse.Namespace) -> KevinSpeakWorkspace:
    ledger = SQLiteLedger(args.ledger, readonly=True)
    try:
        workspace = KevinSpeakWorkspace.restore(
            ledger,
            workspace_id=args.workspace_id,
            account_id=args.account_id,
            account_version=args.account_version,
        )
    except Exception:
        ledger.close()
        raise
    return workspace


def _kevin_verify(args: argparse.Namespace) -> int:
    workspace = _restore_kevin(args)
    try:
        result = workspace.verify()
    finally:
        workspace.close()
    print(canonical_text(result))
    return 0


def _kevin_table(args: argparse.Namespace) -> int:
    workspace = _restore_kevin(args)
    try:
        result = workspace.translation_table()
    finally:
        workspace.close()
    print(canonical_text(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
