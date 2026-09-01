from __future__ import annotations

from pathlib import Path

import pytest

from strongwiz.integrity import (
    FrozenRuntimeManifest,
    IntegrityError,
    freeze_files,
    verify_frozen_files,
)
from tests.support import ref


def test_frozen_runtime_binds_model_domain_capabilities_and_files(tmp_path: Path) -> None:
    source = tmp_path / "driver.py"
    config = tmp_path / "config.json"
    source.write_text("def choose(): return 'inspect'\n", encoding="utf-8")
    config.write_text('{"seed":1}\n', encoding="utf-8")
    files = freeze_files(tmp_path, ("config.json", "driver.py"))
    manifest = FrozenRuntimeManifest(
        package_version="0.1.0",
        contract_schema="strongwiz.contract.v1",
        source_files=files,
        configuration_ref=ref("config"),
        dependency_lock_ref=ref("lock"),
        model_driver_id="local-model",
        model_driver_version="weights-v1",
        model_driver_artifact_ref=ref("weights"),
        domain_adapter_id="synthetic",
        domain_adapter_version="1",
        domain_adapter_artifact_ref=ref("adapter-artifact"),
        capability_refs=(ref("planner"), ref("learner")),
        policy_refs=(ref("router"),),
        runtime_description="Python 3.12 local offline test runtime",
    )
    assert manifest.manifest_ref
    assert tuple(item.relative_path for item in files) == ("config.json", "driver.py")
    verify_frozen_files(tmp_path, manifest.source_files)
    source.write_text("def choose(): return 'open'\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="changed"):
        verify_frozen_files(tmp_path, manifest.source_files)


def test_freeze_refuses_duplicate_and_escaping_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("pass\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="duplicated"):
        freeze_files(tmp_path, ("source.py", "source.py"))
    outside = tmp_path.parent / "outside-strongwiz-test.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        with pytest.raises(IntegrityError, match="escapes"):
            freeze_files(tmp_path, (outside,))
    finally:
        outside.unlink()
