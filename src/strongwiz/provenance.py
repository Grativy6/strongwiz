"""Strict identities for papers and control-owned policy sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from strongwiz.canonical import parse_strict_json
from strongwiz.contracts import ContractModel

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class SourceIdentity(ContractModel):
    source_id: str
    source_kind: Literal["paper", "policy"]
    title: str
    version: str
    persistent_id: str
    local_artifact_sha256: str | None = None
    role: str
    authority_ceiling: str

    @field_validator(
        "source_id",
        "title",
        "version",
        "persistent_id",
        "role",
        "authority_ceiling",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source identity text must be non-empty")
        return value

    @field_validator("local_artifact_sha256")
    @classmethod
    def validate_artifact_ref(cls, value: str | None) -> str | None:
        if value is not None and _DIGEST.fullmatch(value) is None:
            raise ValueError("local source artifact reference must be lowercase SHA-256")
        return value


class SourceIdentityRegistry(ContractModel):
    schema_id: str = Field(default="strongwiz.source-identities.v1", alias="schema")
    steward: str
    sources: tuple[SourceIdentity, ...]
    shared_lineage_is_independent_corroboration: Literal[False] = False
    source_text_is_authorization: Literal[False] = False

    @model_validator(mode="after")
    def validate_registry(self) -> SourceIdentityRegistry:
        if self.schema_id != "strongwiz.source-identities.v1":
            raise ValueError("unsupported source identity registry schema")
        if not self.steward.strip():
            raise ValueError("source registry steward must be non-empty")
        identities = tuple(source.source_id for source in self.sources)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("source identities must be sorted and unique")
        return self


def load_source_registry(path: str | Path) -> SourceIdentityRegistry:
    """Load a registry through duplicate-key rejecting strict JSON parsing."""

    parsed = parse_strict_json(Path(path).read_bytes())
    return SourceIdentityRegistry.model_validate(parsed)
