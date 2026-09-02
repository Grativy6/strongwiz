from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from strongwiz.provenance import SourceIdentityRegistry, load_source_registry

PAL_V23_DOI = "https://doi.org/10.5281/zenodo.22240134"
PAL_V23_SOURCES = {
    "paper-pal-v2.3-c-compatibility-note": {
        "source_kind": "paper",
        "title": "Compatibility Note",
        "version": "2.3-C",
        "persistent_id": PAL_V23_DOI,
        "local_artifact_sha256": (
            "57bd5432c6a0a4474c781f918e60e1fcf3f1b80119bfdc317bf6c17bbbc80f07"
        ),
        "role": "prospective v2.2-to-v2.3 migration mapping and non-retrofit constraints",
        "authority_ceiling": (
            "migration guidance only; cannot redefine the spine, enlarge Atlas results, "
            "close ledger burdens, or establish full PAL v2.3 conformance"
        ),
    },
    "paper-pal-v2.3-l-obligation-decision-ledger": {
        "source_kind": "paper",
        "title": "Obligation and Decision Ledger",
        "version": "2.3-L",
        "persistent_id": PAL_V23_DOI,
        "local_artifact_sha256": (
            "694449304139c642c5112f9e41f3b41848a652fc7d12b9a60450bf5e776f704b"
        ),
        "role": (
            "prospective decision, obligation, residual, and reopening ledger for PAL "
            "v2.3 adoption"
        ),
        "authority_ceiling": (
            "ledger authority only; records rather than manufactures closure, evidence, "
            "permission, authorization, or Strongwiz conformance"
        ),
    },
    "paper-pal-v2.3-m-mathematical-realization-atlas": {
        "source_kind": "paper",
        "title": "Mathematical Realization Atlas",
        "version": "2.3-M",
        "persistent_id": PAL_V23_DOI,
        "local_artifact_sha256": (
            "c053292376363edd6fc743f0f2e31e3bb3850edc78ade3a289bbb07e7e8452c5"
        ),
        "role": (
            "prospective mathematical realization maps, typed boundary adapters, and "
            "scoped fixtures for PAL v2.3"
        ),
        "authority_ceiling": (
            "realization authority only; does not amend the spine or establish universal, "
            "empirical, ontological, or independent-validation claims"
        ),
    },
    "paper-pal-v2.3-spine": {
        "source_kind": "paper",
        "title": "Mechanical Structural Spine",
        "version": "2.3",
        "persistent_id": PAL_V23_DOI,
        "local_artifact_sha256": (
            "e9517b17278b72995f22469d825a62ad9a47d3a151089684f3d4c3ef96e4e9a2"
        ),
        "role": (
            "prospective mechanical semantics and invariant vocabulary for new Strongwiz "
            "records"
        ),
        "authority_ceiling": (
            "mechanical framework source only; creates no external authority and does not "
            "establish full Strongwiz conformance to PAL v2.3"
        ),
    },
    "paper-pal-v2.3-t-conformance-tests": {
        "source_kind": "paper",
        "title": "Conformance Tests",
        "version": "2.3-T",
        "persistent_id": PAL_V23_DOI,
        "local_artifact_sha256": (
            "bf79d3a76ef71d6946704be551488bdfe2c062231f4a0c1a85bb51b203fe4b89"
        ),
        "role": (
            "prospective PAL v2.3 conformance specifications, fixtures, falsifiers, and "
            "reopening handles"
        ),
        "authority_ceiling": (
            "test specification only; passing bounded checks is not full PAL v2.3 "
            "conformance or independent validation"
        ),
    },
}
PRIOR_SOURCE_IDS = {
    "paper-a0bk-v0.10.0",
    "paper-context-draws-map-v1.0",
    "paper-context-is-model-record-21713134",
    "paper-context-rhythm-v0.1",
    "paper-gppr-v0.1",
    "paper-single-cut-transport-v0.1",
    "policy-pea-core-v1.1.3",
    "policy-pecan-v1.0.4",
    "policy-seed-v0.3",
}


def test_declared_source_stack_is_exact_and_complete() -> None:
    registry = load_source_registry(Path("docs/source-identities.json"))
    assert registry.steward == "Christopher D. Pang"
    assert len(registry.sources) == 14
    assert sum(source.source_kind == "paper" for source in registry.sources) == 11
    assert sum(source.source_kind == "policy" for source in registry.sources) == 3
    by_id = {source.source_id: source for source in registry.sources}
    assert set(by_id) == PRIOR_SOURCE_IDS | set(PAL_V23_SOURCES)
    for source_id, expected in PAL_V23_SOURCES.items():
        assert by_id[source_id].model_dump(mode="json", exclude_none=True) == {
            "source_id": source_id,
            **expected,
        }
    versions = {source.title: source.version for source in registry.sources}
    assert versions["PEA Core"] == "1.1.3"
    assert versions["PECAN"] == "1.0.4"
    assert versions["SEED"] == "0.3"


@pytest.mark.parametrize(
    ("path", "claim_ceiling"),
    (
        (
            Path("docs/provenance.md"),
            "No full PAL v2.3 conformance claim is made",
        ),
        (
            Path("THIRD_PARTY_NOTICES.md"),
            "nor full PAL v2.3 conformance",
        ),
    ),
)
def test_pal_v23_provenance_is_receipted_and_prospective(
    path: Path,
    claim_ceiling: str,
) -> None:
    text = path.read_text(encoding="utf-8")
    assert "Christopher D. Pang" in text
    assert "2026-09-02" in text
    assert "CC BY 4.0" in text
    assert PAL_V23_DOI in text
    assert "prospective `SUCCESSOR`" in text
    assert claim_ceiling in text
    for expected in PAL_V23_SOURCES.values():
        assert expected["title"] in text
        assert expected["local_artifact_sha256"] in text


def test_source_registry_contract_remains_extensible_but_rejects_duplicates() -> None:
    registry = load_source_registry(Path("docs/source-identities.json"))
    value = registry.model_dump(mode="python")
    value["sources"] = (*value["sources"], value["sources"][-1])
    with pytest.raises(ValidationError, match="sorted and unique"):
        SourceIdentityRegistry.model_validate(value)
