"""Preparation-only Strongwiz v3 matched-calibration integration.

This package can describe, initialize, and verify fresh shadow laboratories and
exercise the representation-only scribe on synthetic data.  It deliberately
contains no environment acquisition, credential, action, or assessment path.
"""

from calibration_003.models import (
    Calibration003Plan,
    CampaignClaimLabel,
    CampaignIndex,
    CampaignPreparationMarker,
    CampaignVerification,
    EvidenceYieldGate,
    OperatorBinding,
    SyntheticPreflightReceipt,
    V2CarryPacket,
    calibration_003_schema_bundle,
)
from calibration_003.workflow import (
    LoadedV2CarryPacket,
    load_plan,
    load_v2_carry_packet,
    prepare_campaign,
    run_synthetic_preflight,
    verify_campaign,
)

__all__ = [
    "Calibration003Plan",
    "CampaignClaimLabel",
    "CampaignIndex",
    "CampaignPreparationMarker",
    "CampaignVerification",
    "EvidenceYieldGate",
    "LoadedV2CarryPacket",
    "OperatorBinding",
    "SyntheticPreflightReceipt",
    "V2CarryPacket",
    "calibration_003_schema_bundle",
    "load_plan",
    "load_v2_carry_packet",
    "prepare_campaign",
    "run_synthetic_preflight",
    "verify_campaign",
]
