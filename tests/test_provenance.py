from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.provenance import SourceIdentityRegistry, load_source_registry


def test_declared_source_stack_is_exact_and_complete() -> None:
    registry = load_source_registry(Path("docs/source-identities.json"))
    assert registry.steward == "Christopher D. Pang"
    assert len(registry.sources) == 9
    assert sum(source.source_kind == "paper" for source in registry.sources) == 6
    assert sum(source.source_kind == "policy" for source in registry.sources) == 3
    versions = {source.title: source.version for source in registry.sources}
    assert versions["PEA Core"] == "1.1.3"
    assert versions["PECAN"] == "1.0.4"
    assert versions["SEED"] == "0.3"


def test_source_registry_contract_remains_extensible_but_rejects_duplicates() -> None:
    registry = load_source_registry(Path("docs/source-identities.json"))
    value = registry.model_dump(mode="python")
    value["sources"] = (*value["sources"], value["sources"][-1])
    with pytest.raises(ValidationError, match="sorted and unique"):
        SourceIdentityRegistry.model_validate(value)
