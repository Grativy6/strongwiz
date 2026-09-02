"""Optional experimental capability declarations.

Feature declarations are data, not imports.  The kernel never depends on an
experimental implementation, so a feature can be disabled, deleted, or swapped
without changing the stable observation/action/memory/receipt boundary.
"""

from __future__ import annotations

import re

from pydantic import model_validator

from strongwiz.contracts import ContractModel

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ExperimentalFeature(ContractModel):
    feature_id: str
    enabled: bool = False
    implementation_ref: str | None = None
    configuration_ref: str | None = None
    purpose: str
    claim_boundary: str

    @model_validator(mode="after")
    def validate_feature(self) -> ExperimentalFeature:
        required = (self.feature_id, self.purpose, self.claim_boundary)
        if not all(value.strip() for value in required):
            raise ValueError(
                "experimental features require identity, purpose, and claim boundary"
            )
        refs = (self.implementation_ref, self.configuration_ref)
        if self.enabled and any(value is None for value in refs):
            raise ValueError(
                "enabled features require implementation and configuration references"
            )
        if any(value is not None and _DIGEST.fullmatch(value) is None for value in refs):
            raise ValueError("feature references must be lowercase SHA-256 digests")
        return self


class ExperimentalFeatureSet(ContractModel):
    schema_id: str = "strongwiz.experimental-features.v1"
    features: tuple[ExperimentalFeature, ...] = ()

    @model_validator(mode="after")
    def validate_unique_features(self) -> ExperimentalFeatureSet:
        identities = tuple(feature.feature_id for feature in self.features)
        if len(set(identities)) != len(identities):
            raise ValueError("experimental feature identities must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("experimental features must be sorted by identity")
        return self

    def enabled_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for feature in self.features:
            if feature.enabled:
                assert feature.implementation_ref is not None
                assert feature.configuration_ref is not None
                refs.extend((feature.implementation_ref, feature.configuration_ref))
        return tuple(refs)


def default_experimental_features() -> ExperimentalFeatureSet:
    """Return inert defaults; optional mechanisms require explicit experiments."""

    return ExperimentalFeatureSet(
        features=(
            ExperimentalFeature(
                feature_id="gppr",
                purpose="test geometry-aware path selection under a declared experiment",
                claim_boundary=(
                    "disabled by default; enabling it does not establish a universal "
                    "optimization"
                ),
            ),
            ExperimentalFeature(
                feature_id="kevin-speak",
                purpose=(
                    "test reversible model-authored working-ledger shorthand under "
                    "matched storage and model-facing evaluations"
                ),
                claim_boundary=(
                    "disabled by default; exact reconstruction does not establish "
                    "unchanged model behavior or improved reasoning"
                ),
            ),
        )
    )
