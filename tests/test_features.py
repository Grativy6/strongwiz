from __future__ import annotations

import pytest
from pydantic import ValidationError

from strongwiz.features import (
    ExperimentalFeature,
    ExperimentalFeatureSet,
    default_experimental_features,
)

from .support import ref


def test_gppr_is_inert_by_default() -> None:
    feature_set = default_experimental_features()
    assert feature_set.features[0].feature_id == "gppr"
    assert feature_set.features[0].enabled is False
    assert feature_set.enabled_refs() == ()


def test_enabled_feature_binds_replaceable_implementation_and_configuration() -> None:
    feature = ExperimentalFeature(
        feature_id="alternate-geometry",
        enabled=True,
        implementation_ref=ref("implementation"),
        configuration_ref=ref("configuration"),
        purpose="compare an alternate geometry",
        claim_boundary="one declared experiment only",
    )
    feature_set = ExperimentalFeatureSet(features=(feature,))
    assert feature_set.enabled_refs() == (
        ref("implementation"),
        ref("configuration"),
    )


def test_enabled_feature_without_frozen_refs_is_rejected() -> None:
    with pytest.raises(ValidationError, match="enabled features require"):
        ExperimentalFeature(
            feature_id="gppr",
            enabled=True,
            purpose="test geometry-aware selection",
            claim_boundary="experimental only",
        )


def test_feature_set_rejects_duplicates_and_unsorted_identity() -> None:
    gppr = default_experimental_features().features[0]
    with pytest.raises(ValidationError, match="unique"):
        ExperimentalFeatureSet(features=(gppr, gppr))

    alternate = ExperimentalFeature(
        feature_id="alternate",
        purpose="test another capability",
        claim_boundary="experimental only",
    )
    with pytest.raises(ValidationError, match="sorted"):
        ExperimentalFeatureSet(features=(gppr, alternate))
