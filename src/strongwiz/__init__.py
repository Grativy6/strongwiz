"""Strongwiz: a model-neutral laboratory for difficult work."""

from strongwiz.conformance import (
    ConformanceReport,
    check_domain_adapter,
    check_model_driver,
)
from strongwiz.contracts import (
    CONTRACT_SCHEMA,
    ActionSpec,
    BoundaryStatus,
    CandidateProposal,
    ControlSnapshot,
    CostVector,
    DecisionEffect,
    Distinction,
    Goal,
    Observation,
    Outcome,
    ReasoningRequest,
    RouteDecision,
    RouteDisposition,
    contract_schema_bundle,
)
from strongwiz.features import (
    ExperimentalFeature,
    ExperimentalFeatureSet,
    default_experimental_features,
)
from strongwiz.lab import (
    EvidenceCapsuleManifest,
    LabManifest,
    PromotionReceipt,
    RunDisposition,
    RunSpec,
    initialize_lab,
    pack_evidence,
    seal_run,
    verify_evidence_capsule,
    verify_lab,
)
from strongwiz.modelkit import (
    CallableModelDriver,
    FramedModelDriver,
    FramedModelRestartState,
    ProposalDraft,
)
from strongwiz.runtime import SessionCheckpoint, StrongwizKernel

__all__ = [
    "CONTRACT_SCHEMA",
    "ActionSpec",
    "BoundaryStatus",
    "CallableModelDriver",
    "CandidateProposal",
    "ConformanceReport",
    "ControlSnapshot",
    "CostVector",
    "DecisionEffect",
    "Distinction",
    "EvidenceCapsuleManifest",
    "ExperimentalFeature",
    "ExperimentalFeatureSet",
    "FramedModelDriver",
    "FramedModelRestartState",
    "Goal",
    "LabManifest",
    "Observation",
    "Outcome",
    "PromotionReceipt",
    "ProposalDraft",
    "ReasoningRequest",
    "RouteDecision",
    "RouteDisposition",
    "RunDisposition",
    "RunSpec",
    "SessionCheckpoint",
    "StrongwizKernel",
    "check_domain_adapter",
    "check_model_driver",
    "contract_schema_bundle",
    "default_experimental_features",
    "initialize_lab",
    "pack_evidence",
    "seal_run",
    "verify_evidence_capsule",
    "verify_lab",
]

__version__ = "0.2.0"
