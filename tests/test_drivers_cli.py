from __future__ import annotations

import json
from pathlib import Path

import pytest

from strongwiz.cli import main
from strongwiz.drivers import DriverRegistry
from strongwiz.ledger import LedgerError, SQLiteLedger
from tests.support import control, proposal


class Driver:
    driver_id = "driver"
    driver_version = "1"
    driver_artifact_ref = "0" * 64

    def propose(self, _request: object) -> tuple[object, ...]:
        return ()


class Domain:
    adapter_id = "domain"
    adapter_version = "1"
    adapter_artifact_ref = "1" * 64

    def normalize_observation(self, raw: object) -> object:
        return raw

    def available_actions(self, _observation: object) -> tuple[object, ...]:
        return ()

    def extract_outcome(self, _before: object, _action: object, raw_after: object) -> object:
        return raw_after

    def terminal_authority(self, _observation: object) -> object:
        return None


def test_registry_preserves_replaceable_identity() -> None:
    registry = DriverRegistry()
    driver = Driver()
    domain = Domain()
    registry.register_model(driver)  # type: ignore[arg-type]
    registry.register_domain(domain)  # type: ignore[arg-type]
    assert registry.model_ids == ("driver",)
    assert registry.domain_ids == ("domain",)
    registry.register_model(driver)  # idempotent exact object
    with pytest.raises(ValueError, match="already registered"):
        registry.register_model(Driver())  # type: ignore[arg-type]


def test_cli_schema_route_and_ledger_verification(tmp_path: Path, capsys: object) -> None:
    assert main(["schema"]) == 0
    schema_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "strongwiz.contract.v1" in schema_output
    assert main(["schema", "--all"]) == 0
    bundle_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"candidate_proposal"' in bundle_output
    assert '"control_snapshot"' in bundle_output

    route_file = tmp_path / "route.json"
    route_file.write_text(
        json.dumps(
            {
                "proposal": proposal().model_dump(mode="json", by_alias=True),
                "control": control().model_dump(mode="json", by_alias=True),
            }
        ),
        encoding="utf-8",
    )
    assert main(["route", str(route_file)]) == 0
    route_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"disposition":"admit"' in route_output

    ledger_path = tmp_path / "ledger.sqlite3"
    with SQLiteLedger(ledger_path) as ledger:
        receipt = ledger.append(
            occurrence_id="test-0",
            kind="test",
            account_id="account",
            account_version=0,
            payload={"ok": True},
        )
    assert main(["verify-ledger", str(ledger_path)]) == 0
    verify_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"receipt_count":1' in verify_output
    assert '"valid":true' in verify_output
    assert '"externally_sealed":false' in verify_output

    assert (
        main(
            [
                "verify-ledger",
                str(ledger_path),
                "--expected-count",
                "1",
                "--expected-head",
                receipt.receipt_hash,
            ]
        )
        == 0
    )
    sealed_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"externally_sealed":true' in sealed_output

    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(LedgerError, match="does not exist"):
        main(["verify-ledger", str(missing)])
    assert not missing.exists()
