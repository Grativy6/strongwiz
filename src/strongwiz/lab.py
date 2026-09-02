"""Deterministic lab genesis, run sealing, and portable evidence capsules.

The contracts in this module identify exact content and preserve evidence.  They
do not grant authority, execute actions, establish truth, or promote a learned
mechanism into the Strongwiz kernel.  Labs retain concise, falsifiable summaries;
private or hidden chain-of-thought is outside the contract.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from strongwiz.canonical import (
    ImmutableJSONValue,
    canonical_bytes,
    content_hash,
    parse_strict_json,
    sha256_bytes,
)
from strongwiz.contracts import ContractModel, CostVector, NonNegativeInt
from strongwiz.ledger import ReceiptEnvelope
from strongwiz.pathsafe import is_link_like, is_portable_component

LAB_MANIFEST_SCHEMA = "strongwiz.lab-manifest.v1"
RUN_SPEC_SCHEMA = "strongwiz.run-spec.v1"
LAB_GENESIS_SCHEMA = "strongwiz.lab-genesis.v1"
RUN_SEAL_SCHEMA = "strongwiz.run-seal.v1"
CAPSULE_MANIFEST_SCHEMA = "strongwiz.evidence-capsule.v1"
PROMOTION_RECEIPT_SCHEMA = "strongwiz.promotion-receipt.v1"

CAPSULE_MANIFEST_PATH = "evidence-capsule.manifest.json"
CAPSULE_LAB_MANIFEST_PATH = "lab.manifest.json"
CAPSULE_RUN_SPEC_PATH = "run.spec.json"
CAPSULE_GENESIS_PATH = "lab.genesis.json"
CAPSULE_RUN_SEAL_PATH = "run.seal.json"
CAPSULE_OBJECTS_PATH = "ledger/objects.jsonl"
CAPSULE_RECEIPTS_PATH = "ledger/receipts.jsonl"
CAPSULE_DOMAIN_STATE_PATH = "domain-state"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SQLITE_TRANSIENT_SUFFIXES = ("-journal", "-shm", "-wal")
_PRIVATE_REASONING_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_cot",
        "hidden_reasoning",
        "internal_monologue",
        "private_reasoning",
        "scratchpad",
        "thought_tokens",
    }
)
_PROMOTION_EXCLUSIONS = (
    "action_sequences",
    "domain_state",
    "hidden_chain_of_thought",
    "learned_mechanics",
    "replay_state",
)


class LabError(RuntimeError):
    """A lab or capsule boundary failed closed."""


class RunDisposition(StrEnum):
    SUCCESS_OBSERVED = "success_observed"
    PARTIAL = "partial"
    BLOCKED_EXTERNAL = "blocked_external"
    FAILED_MECHANISM = "failed_mechanism"
    FAILED_INFRASTRUCTURE = "failed_infrastructure"


class CapsuleFileRole(StrEnum):
    LAB_MANIFEST = "lab_manifest"
    RUN_SPEC = "run_spec"
    LAB_GENESIS = "lab_genesis"
    RUN_SEAL = "run_seal"
    LEDGER_OBJECTS = "ledger_objects"
    LEDGER_RECEIPTS = "ledger_receipts"


def _require_digest(value: str, *, label: str) -> str:
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_text(value: str, *, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _require_sorted_unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise ValueError("lab paths must be non-empty canonical relative POSIX paths")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(not is_portable_component(part) for part in path.parts)
    ):
        raise ValueError("lab paths must be non-empty canonical relative POSIX paths")
    return value


class LabLayout(ContractModel):
    """Fixed bootstrap names plus safely relocatable state paths."""

    manifest_path: Literal["lab.manifest.json"] = "lab.manifest.json"
    run_spec_path: Literal["run.spec.json"] = "run.spec.json"
    genesis_path: Literal["lab.genesis.json"] = "lab.genesis.json"
    run_seal_path: Literal["run.seal.json"] = "run.seal.json"
    ledger_path: str = "state/ledger.sqlite3"
    domain_state_path: str = "state/domain"

    @field_validator("ledger_path", "domain_state_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative(value)

    @model_validator(mode="after")
    def validate_layout(self) -> LabLayout:
        file_paths = (
            self.manifest_path,
            self.run_spec_path,
            self.genesis_path,
            self.run_seal_path,
            self.ledger_path,
        )
        if len(set(file_paths)) != len(file_paths):
            raise ValueError("lab control and ledger paths must be distinct")
        domain = PurePosixPath(self.domain_state_path)
        if any(
            domain == PurePosixPath(item)
            or domain in PurePosixPath(item).parents
            or PurePosixPath(item) in domain.parents
            for item in file_paths
        ):
            raise ValueError("lab control files cannot be stored in domain state")
        return self


class LabManifest(ContractModel):
    """Model-neutral identity of one newly constructed reasoning laboratory."""

    schema_id: str = Field(default=LAB_MANIFEST_SCHEMA, alias="schema")
    lab_id: str
    lab_version: str
    purpose: str
    strongwiz_version: str
    kernel_artifact_ref: str
    contract_schema: str
    layout: LabLayout = Field(default_factory=LabLayout)
    capability_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    source_identity_refs: tuple[str, ...] = ()
    reasoning_record_policy: Literal["concise_summary_only"] = "concise_summary_only"
    reasoning_record_policy_scope: Literal["typed_strongwiz_records_only"] = (
        "typed_strongwiz_records_only"
    )
    authority_origin: Literal["externally_supplied_only"] = "externally_supplied_only"
    self_authorizing: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> LabManifest:
        if self.schema_id != LAB_MANIFEST_SCHEMA:
            raise ValueError("unsupported lab manifest schema")
        for label, value in (
            ("lab id", self.lab_id),
            ("lab version", self.lab_version),
            ("purpose", self.purpose),
            ("Strongwiz version", self.strongwiz_version),
            ("contract schema", self.contract_schema),
        ):
            _require_text(value, label=label)
        _require_digest(self.kernel_artifact_ref, label="kernel artifact reference")
        for label, values in (
            ("capability references", self.capability_refs),
            ("policy references", self.policy_refs),
            ("source identity references", self.source_identity_refs),
        ):
            _require_sorted_unique(values, label=label)
            for value in values:
                _require_digest(value, label=label)
        return self


class RunSpec(ContractModel):
    """Predeclared identity and ceilings for one run inside one lab."""

    schema_id: str = Field(default=RUN_SPEC_SCHEMA, alias="schema")
    run_id: str
    lab_manifest_ref: str
    objective: str
    success_condition: str
    success_state: str
    terminal_authority_source: str
    evaluation_class: str
    frozen_runtime_ref: str
    model_driver_id: str
    model_driver_version: str
    model_driver_artifact_ref: str
    domain_adapter_id: str
    domain_adapter_version: str
    domain_adapter_artifact_ref: str
    seed: NonNegativeInt
    resource_budget: CostVector = Field(default_factory=CostVector)
    allowed_action_names: tuple[str, ...] = ()
    declared_input_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    prior_run_refs: tuple[str, ...] = ()
    prior_domain_state_refs: tuple[str, ...] = ()
    execution_grant_ref: str | None = None
    shadow_only: bool = True
    reasoning_record_policy: Literal["concise_summary_only"] = "concise_summary_only"
    reasoning_record_policy_scope: Literal["typed_strongwiz_records_only"] = (
        "typed_strongwiz_records_only"
    )
    authority_ceiling: Literal["supplied_grant_only"] = "supplied_grant_only"
    self_authorizing: Literal[False] = False

    @model_validator(mode="after")
    def validate_run(self) -> RunSpec:
        if self.schema_id != RUN_SPEC_SCHEMA:
            raise ValueError("unsupported run specification schema")
        for label, value in (
            ("run id", self.run_id),
            ("objective", self.objective),
            ("success condition", self.success_condition),
            ("success state", self.success_state),
            ("terminal authority source", self.terminal_authority_source),
            ("evaluation class", self.evaluation_class),
            ("model driver id", self.model_driver_id),
            ("model driver version", self.model_driver_version),
            ("domain adapter id", self.domain_adapter_id),
            ("domain adapter version", self.domain_adapter_version),
        ):
            _require_text(value, label=label)
        for label, value in (
            ("lab manifest reference", self.lab_manifest_ref),
            ("frozen runtime reference", self.frozen_runtime_ref),
            ("model driver artifact reference", self.model_driver_artifact_ref),
            ("domain adapter artifact reference", self.domain_adapter_artifact_ref),
        ):
            _require_digest(value, label=label)
        for label, values in (
            ("allowed action names", self.allowed_action_names),
            ("declared input references", self.declared_input_refs),
            ("policy references", self.policy_refs),
        ):
            _require_sorted_unique(values, label=label)
        for label, values in (
            ("declared input references", self.declared_input_refs),
            ("policy references", self.policy_refs),
        ):
            for value in values:
                _require_digest(value, label=label)
        if self.prior_run_refs or self.prior_domain_state_refs:
            raise ValueError("a genesis run cannot inherit prior run or domain state")
        if self.execution_grant_ref is not None:
            _require_digest(self.execution_grant_ref, label="execution grant reference")
        if not self.shadow_only and self.execution_grant_ref is None:
            raise ValueError("non-shadow execution requires an externally supplied grant")
        return self


class LabGenesisSeal(ContractModel):
    """External assertion that the lab began before any run-local state existed."""

    schema_id: str = Field(default=LAB_GENESIS_SCHEMA, alias="schema")
    lab_manifest_ref: str
    run_spec_ref: str
    ledger_receipt_count: Literal[0] = 0
    ledger_object_count: Literal[0] = 0
    ledger_head: None = None
    domain_state_entry_count: Literal[0] = 0
    prior_run_refs: tuple[()] = ()
    prior_domain_state_refs: tuple[()] = ()
    assertion: Literal["empty_ledger_and_no_prior_domain_state"] = (
        "empty_ledger_and_no_prior_domain_state"
    )
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_genesis(self) -> LabGenesisSeal:
        if self.schema_id != LAB_GENESIS_SCHEMA:
            raise ValueError("unsupported lab genesis schema")
        _require_digest(self.lab_manifest_ref, label="lab manifest reference")
        _require_digest(self.run_spec_ref, label="run specification reference")
        return self


class ExternalLedgerSeal(ContractModel):
    """Count/head plus full object and receipt projection identities."""

    receipt_count: NonNegativeInt
    receipt_head: str | None
    object_count: NonNegativeInt
    objects_projection_ref: str
    receipts_projection_ref: str

    @model_validator(mode="after")
    def validate_seal(self) -> ExternalLedgerSeal:
        for label, value in (
            ("objects projection reference", self.objects_projection_ref),
            ("receipts projection reference", self.receipts_projection_ref),
        ):
            _require_digest(value, label=label)
        if self.receipt_count == 0:
            if self.receipt_head is not None:
                raise ValueError("an empty ledger cannot have a receipt head")
        elif self.receipt_head is None:
            raise ValueError("a non-empty ledger requires a receipt head")
        else:
            _require_digest(self.receipt_head, label="receipt head")
        return self


@dataclass(frozen=True)
class _LedgerSnapshot:
    seal: ExternalLedgerSeal
    terminal_object_present: bool
    terminal_receipt_present: bool


class _CanonicalArrayHasher:
    """Hash canonical values as one JSON array without retaining prior rows."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"[")
        self._count = 0

    def update(self, value: bytes) -> None:
        if self._count:
            self._digest.update(b",")
        self._digest.update(value)
        self._count += 1

    def hexdigest(self) -> str:
        digest = self._digest.copy()
        digest.update(b"]")
        return digest.hexdigest()


class DomainStateEntry(ContractModel):
    """One exact file or directory relative to a lab's domain-state root."""

    relative_path: str
    kind: Literal["file", "directory"]
    size_bytes: NonNegativeInt = 0
    sha256: str | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative(value)

    @model_validator(mode="after")
    def validate_entry(self) -> DomainStateEntry:
        if self.kind == "directory":
            if self.size_bytes != 0 or self.sha256 is not None:
                raise ValueError("domain directories cannot carry file bytes or a digest")
        elif self.sha256 is None:
            raise ValueError("domain files require an exact SHA-256 digest")
        else:
            _require_digest(self.sha256, label="domain-state file digest")
        return self


class ExternalDomainStateSeal(ContractModel):
    """Complete path/type/size/hash projection of the domain-state tree."""

    entry_count: NonNegativeInt
    entries: tuple[DomainStateEntry, ...]
    projection_ref: str
    content_handling: Literal["opaque_unsanitized_bytes"] = "opaque_unsanitized_bytes"

    @model_validator(mode="after")
    def validate_domain_state(self) -> ExternalDomainStateSeal:
        paths = tuple(entry.relative_path for entry in self.entries)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("domain-state entries must be sorted and unique")
        if self.entry_count != len(self.entries):
            raise ValueError("domain-state entry count disagrees with its projection")
        _require_digest(self.projection_ref, label="domain-state projection reference")
        if self.projection_ref != content_hash(self.entries):
            raise ValueError("domain-state projection reference disagrees with its entries")
        return self


class LabVerification(ContractModel):
    lab_manifest_ref: str
    run_spec_ref: str
    genesis_ref: str
    ledger_seal: ExternalLedgerSeal
    domain_state_seal: ExternalDomainStateSeal
    domain_state_entry_count: NonNegativeInt
    current_state_matches_genesis: bool
    run_seal_ref: str | None = None
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"


class RunSeal(ContractModel):
    """Evidence-bound terminal disposition; never an authority grant."""

    schema_id: str = Field(default=RUN_SEAL_SCHEMA, alias="schema")
    run_id: str
    lab_manifest_ref: str
    run_spec_ref: str
    genesis_ref: str
    ledger_seal: ExternalLedgerSeal
    domain_state_seal: ExternalDomainStateSeal
    disposition: RunDisposition
    terminal_state: str
    terminal_evidence_ref: str
    completion_genuinely_observed: bool
    terminal_authority_source: str
    concise_result_summary: str
    claim_ceiling: Literal["declared_terminal_observation_only"] = (
        "declared_terminal_observation_only"
    )
    reasoning_record_policy: Literal["concise_summary_only"] = "concise_summary_only"
    reasoning_record_policy_scope: Literal["typed_strongwiz_records_only"] = (
        "typed_strongwiz_records_only"
    )
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_run_seal(self) -> RunSeal:
        if self.schema_id != RUN_SEAL_SCHEMA:
            raise ValueError("unsupported run seal schema")
        for label, value in (
            ("run id", self.run_id),
            ("terminal state", self.terminal_state),
            ("terminal authority source", self.terminal_authority_source),
            ("concise result summary", self.concise_result_summary),
        ):
            _require_text(value, label=label)
        for label, value in (
            ("lab manifest reference", self.lab_manifest_ref),
            ("run specification reference", self.run_spec_ref),
            ("genesis reference", self.genesis_ref),
            ("terminal evidence reference", self.terminal_evidence_ref),
        ):
            _require_digest(value, label=label)
        if self.completion_genuinely_observed != (
            self.disposition is RunDisposition.SUCCESS_OBSERVED
        ):
            raise ValueError("success disposition and observed-completion marker must agree")
        return self


class CapsuleObject(ContractModel):
    payload_hash: str
    payload: ImmutableJSONValue

    @model_validator(mode="after")
    def validate_object(self) -> CapsuleObject:
        _require_digest(self.payload_hash, label="object payload hash")
        if content_hash(self.payload) != self.payload_hash:
            raise ValueError("capsule object content disagrees with its payload hash")
        return self


class CapsuleFile(ContractModel):
    role: CapsuleFileRole
    relative_path: str
    size_bytes: NonNegativeInt
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _require_digest(value, label="capsule file digest")


_EXPECTED_CAPSULE_PATHS = {
    CapsuleFileRole.LAB_MANIFEST: CAPSULE_LAB_MANIFEST_PATH,
    CapsuleFileRole.RUN_SPEC: CAPSULE_RUN_SPEC_PATH,
    CapsuleFileRole.LAB_GENESIS: CAPSULE_GENESIS_PATH,
    CapsuleFileRole.RUN_SEAL: CAPSULE_RUN_SEAL_PATH,
    CapsuleFileRole.LEDGER_OBJECTS: CAPSULE_OBJECTS_PATH,
    CapsuleFileRole.LEDGER_RECEIPTS: CAPSULE_RECEIPTS_PATH,
}


class EvidenceCapsuleManifest(ContractModel):
    """Portable, closed projection of one sealed run and its complete ledger."""

    schema_id: str = Field(default=CAPSULE_MANIFEST_SCHEMA, alias="schema")
    capsule_name: str
    lab_id: str
    run_id: str
    lab_manifest_ref: str
    run_spec_ref: str
    genesis_ref: str
    run_seal_ref: str
    ledger_seal: ExternalLedgerSeal
    domain_state_seal: ExternalDomainStateSeal
    files: tuple[CapsuleFile, ...]
    terminal_evidence_ref: str
    completion_genuinely_observed: bool
    complete_sqlite_projection: Literal[True] = True
    complete_domain_state_projection: Literal[True] = True
    domain_state_disclosure_status: Literal["opaque_unsanitized_not_publication_reviewed"] = (
        "opaque_unsanitized_not_publication_reviewed"
    )
    opaque_domain_state_copy_acknowledged: bool = False
    reasoning_record_policy: Literal["concise_summary_only"] = "concise_summary_only"
    reasoning_record_policy_scope: Literal["typed_strongwiz_records_only"] = (
        "typed_strongwiz_records_only"
    )
    claim_ceiling: Literal["evidence_only"] = "evidence_only"
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"
    self_authorizing: Literal[False] = False

    @model_validator(mode="after")
    def validate_capsule(self) -> EvidenceCapsuleManifest:
        if self.schema_id != CAPSULE_MANIFEST_SCHEMA:
            raise ValueError("unsupported evidence capsule schema")
        for label, value in (
            ("capsule name", self.capsule_name),
            ("lab id", self.lab_id),
            ("run id", self.run_id),
        ):
            _require_text(value, label=label)
        for label, value in (
            ("lab manifest reference", self.lab_manifest_ref),
            ("run specification reference", self.run_spec_ref),
            ("genesis reference", self.genesis_ref),
            ("run seal reference", self.run_seal_ref),
            ("terminal evidence reference", self.terminal_evidence_ref),
        ):
            _require_digest(value, label=label)
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("capsule files must be sorted by unique relative path")
        by_role = {item.role: item.relative_path for item in self.files}
        if len(by_role) != len(self.files) or by_role != _EXPECTED_CAPSULE_PATHS:
            raise ValueError("capsule must contain each required file role exactly once")
        if (
            self.domain_state_seal.entry_count
            and not self.opaque_domain_state_copy_acknowledged
        ):
            raise ValueError(
                "nonempty opaque domain state requires explicit copy acknowledgment"
            )
        return self


class PromotionReceipt(ContractModel):
    """A bounded proposal to evaluate a mechanism, not an adoption record."""

    schema_id: str = Field(default=PROMOTION_RECEIPT_SCHEMA, alias="schema")
    candidate_id: str
    source_capsule_ref: str
    source_run_seal_ref: str
    candidate_mechanism_ref: str
    target_scope: str
    falsifiable_claim: str
    concise_rationale: str
    evidence_refs: tuple[str, ...]
    excluded_run_specific_material: tuple[str, ...] = _PROMOTION_EXCLUSIONS
    status: Literal["proposed_not_adopted"] = "proposed_not_adopted"
    claim_ceiling: Literal["candidate_mechanism_only"] = "candidate_mechanism_only"
    requires_independent_review: Literal[True] = True
    requires_ablation: Literal[True] = True
    transfers_domain_state: Literal[False] = False
    transfers_action_sequences: Literal[False] = False
    transfers_authority: Literal[False] = False
    reasoning_record_policy: Literal["concise_summary_only"] = "concise_summary_only"
    authority: Literal["NONE"] = "NONE"
    effect: Literal["NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_promotion(self) -> PromotionReceipt:
        if self.schema_id != PROMOTION_RECEIPT_SCHEMA:
            raise ValueError("unsupported promotion receipt schema")
        for label, value in (
            ("candidate id", self.candidate_id),
            ("target scope", self.target_scope),
            ("falsifiable claim", self.falsifiable_claim),
            ("concise rationale", self.concise_rationale),
        ):
            _require_text(value, label=label)
        for label, value in (
            ("source capsule reference", self.source_capsule_ref),
            ("source run seal reference", self.source_run_seal_ref),
            ("candidate mechanism reference", self.candidate_mechanism_ref),
        ):
            _require_digest(value, label=label)
        _require_sorted_unique(self.evidence_refs, label="promotion evidence references")
        if not self.evidence_refs:
            raise ValueError("a promotion candidate requires evidence references")
        for value in self.evidence_refs:
            _require_digest(value, label="promotion evidence reference")
        if self.excluded_run_specific_material != _PROMOTION_EXCLUSIONS:
            raise ValueError("promotion must exclude every declared run-specific category")
        return self


def _resolved_root(root: str | Path) -> Path:
    supplied = Path(root)
    if is_link_like(supplied):
        raise LabError("lab root must not be a link-like path")
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise LabError("lab root does not exist") from error
    if not resolved.is_dir():
        raise LabError("lab root must be a directory")
    return resolved


def _safe_existing(root: Path, relative_path: str, *, file: bool | None = None) -> Path:
    _safe_relative(relative_path)
    candidate = root
    for part in PurePosixPath(relative_path).parts:
        candidate = candidate / part
        if is_link_like(candidate):
            raise LabError(f"lab path is link-like: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise LabError(f"lab path is missing or escapes its root: {relative_path}") from error
    if file is True and not resolved.is_file():
        raise LabError(f"lab path is not a regular file: {relative_path}")
    if file is False and not resolved.is_dir():
        raise LabError(f"lab path is not a directory: {relative_path}")
    return resolved


def _safe_new_path(root: Path, relative_path: str) -> Path:
    _safe_relative(relative_path)
    parts = PurePosixPath(relative_path).parts
    parent = root
    for part in parts[:-1]:
        child = parent / part
        if is_link_like(child):
            raise LabError(f"new lab path crosses a link-like entry: {relative_path}")
        if child.exists():
            if not child.is_dir():
                raise LabError(f"new lab path crosses a non-directory: {relative_path}")
        else:
            child.mkdir()
        try:
            child.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise LabError("new lab path escapes its root") from error
        parent = child
    candidate = parent / parts[-1]
    if is_link_like(candidate):
        raise LabError(f"new lab path is link-like: {relative_path}")
    return candidate


def _scan_tree_paths(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    files: list[Path] = []
    directories: list[Path] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if is_link_like(child):
                raise LabError(f"link-like paths are forbidden: {child.relative_to(root)}")
            if child.is_dir():
                directories.append(child)
                visit(child)
            elif child.is_file():
                files.append(child)
            else:
                raise LabError(f"special filesystem entries are forbidden: {child}")

    visit(root)
    return tuple(files), tuple(directories)


def _scan_tree(root: Path) -> tuple[Path, ...]:
    return _scan_tree_paths(root)[0]


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists() or is_link_like(path):
        if path.is_file() and not is_link_like(path) and path.read_bytes() == payload:
            return
        raise LabError(f"immutable artifact already exists with different content: {path}")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _read_contract(path: Path, model: type[ContractModel]) -> ContractModel:
    raw = path.read_bytes()
    try:
        value = model.model_validate_json(raw)
    except ValueError as error:
        raise LabError(f"invalid contract file: {path.name}: {error}") from error
    if canonical_bytes(value) != raw:
        raise LabError(f"contract file is not exact canonical JSON: {path.name}")
    return value


def _domain_snapshot(
    path: Path,
) -> tuple[ExternalDomainStateSeal, dict[str, bytes]]:
    files, directories = _scan_tree_paths(path)
    payloads: dict[str, bytes] = {}
    entries: list[DomainStateEntry] = []
    for directory in directories:
        entries.append(
            DomainStateEntry(
                relative_path=directory.relative_to(path).as_posix(),
                kind="directory",
            )
        )
    for file in files:
        relative = file.relative_to(path).as_posix()
        payload = file.read_bytes()
        payloads[relative] = payload
        entries.append(
            DomainStateEntry(
                relative_path=relative,
                kind="file",
                size_bytes=len(payload),
                sha256=sha256_bytes(payload),
            )
        )
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    return (
        ExternalDomainStateSeal(
            entry_count=len(ordered),
            entries=ordered,
            projection_ref=content_hash(ordered),
        ),
        payloads,
    )


def _expected_parent_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for value in paths:
        parent = PurePosixPath(value).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_lab_tree(
    root: Path,
    manifest: LabManifest,
    domain_state: ExternalDomainStateSeal,
    *,
    run_seal_present: bool,
) -> None:
    layout = manifest.layout
    expected_files = {
        layout.manifest_path,
        layout.run_spec_path,
        layout.genesis_path,
        layout.ledger_path,
    }
    if run_seal_present:
        expected_files.add(layout.run_seal_path)
    expected_files.update(
        f"{layout.domain_state_path}/{entry.relative_path}"
        for entry in domain_state.entries
        if entry.kind == "file"
    )
    expected_directories = _expected_parent_directories(expected_files)
    expected_directories.add(layout.domain_state_path)
    expected_directories.update(
        f"{layout.domain_state_path}/{entry.relative_path}"
        for entry in domain_state.entries
        if entry.kind == "directory"
    )
    files, directories = _scan_tree_paths(root)
    actual_files = {path.relative_to(root).as_posix() for path in files}
    actual_directories = {path.relative_to(root).as_posix() for path in directories}
    if actual_files != expected_files or actual_directories != expected_directories:
        raise LabError("lab contains missing or undeclared files or directories")


def _reject_private_reasoning(value: object, *, location: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in _PRIVATE_REASONING_KEYS:
                raise LabError(
                    f"private reasoning field is outside the evidence contract: {location}"
                )
            _reject_private_reasoning(item, location=f"{location}.{key}")
    elif isinstance(value, tuple | list):
        for index, item in enumerate(value):
            _reject_private_reasoning(item, location=f"{location}[{index}]")


def _receipt_references(evidence_ref: str, receipts: tuple[ReceiptEnvelope, ...]) -> bool:
    return any(
        evidence_ref == receipt.payload_hash or evidence_ref in receipt.object_refs
        for receipt in receipts
    )


def _ledger_file_identity(path: Path) -> tuple[int, int, str]:
    try:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return stat.st_size, stat.st_mtime_ns, digest.hexdigest()
    except OSError as error:
        raise LabError(f"cannot identify the SQLite ledger: {error}") from error


def _require_quiescent_ledger(path: Path) -> None:
    transient_paths = tuple(Path(f"{path}{suffix}") for suffix in _SQLITE_TRANSIENT_SUFFIXES)
    present = tuple(item for item in transient_paths if item.exists() or is_link_like(item))
    if present:
        names = ", ".join(item.name for item in present)
        raise LabError(
            "ledger snapshot requires a closed, checkpointed writer; "
            f"found transient SQLite state: {names}"
        )


@contextmanager
def _ledger_identity_index() -> Iterator[sqlite3.Connection]:
    """Provide a fixed-cache temporary index for exact cross-row closure checks."""

    connection = sqlite3.connect("")
    try:
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = FILE")
        connection.execute("PRAGMA cache_size = -2048")
        connection.execute("PRAGMA mmap_size = 0")
        connection.executescript(
            """
            CREATE TABLE objects (
                payload_hash TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE receipts (
                receipt_id TEXT PRIMARY KEY,
                occurrence_id TEXT NOT NULL UNIQUE,
                receipt_hash TEXT NOT NULL UNIQUE
            ) WITHOUT ROWID;
            """
        )
        yield connection
    finally:
        connection.close()


def _index_has(connection: sqlite3.Connection, table: str, column: str, value: str) -> bool:
    if (table, column) not in {
        ("objects", "payload_hash"),
        ("receipts", "receipt_id"),
    }:
        raise AssertionError("internal ledger index query is not allow-listed")
    row = connection.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", (value,)).fetchone()
    return row is not None


def _ledger_snapshot(
    path: Path, *, terminal_evidence_ref: str | None = None
) -> _LedgerSnapshot:
    """Validate and hash a complete ledger with memory bounded by one row."""

    _require_quiescent_ledger(path)
    identity_before = _ledger_file_identity(path)
    source: sqlite3.Connection | None = None
    try:
        uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
        source = sqlite3.connect(uri, uri=True)
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA temp_store = FILE")
        source.execute("PRAGMA cache_size = -2048")
        source.execute("PRAGMA mmap_size = 0")
        source.execute("BEGIN")
        with _ledger_identity_index() as index:
            objects_projection = _CanonicalArrayHasher()
            object_count = 0
            previous_object: str | None = None
            terminal_object_present = False
            for stored_hash, stored_payload in source.execute(
                "SELECT payload_hash, canonical_payload FROM objects ORDER BY payload_hash"
            ):
                raw = bytes(stored_payload)
                payload = parse_strict_json(raw)
                if canonical_bytes(payload) != raw:
                    raise LabError("stored ledger object is not canonical JSON")
                item = CapsuleObject(payload_hash=str(stored_hash), payload=payload)
                if previous_object is not None and item.payload_hash <= previous_object:
                    raise LabError("duplicate or unsorted object identity in SQLite ledger")
                _reject_private_reasoning(item.payload)
                try:
                    index.execute(
                        "INSERT INTO objects(payload_hash) VALUES (?)", (item.payload_hash,)
                    )
                except sqlite3.IntegrityError as error:
                    raise LabError("duplicate object identity in SQLite ledger") from error
                objects_projection.update(canonical_bytes(item))
                object_count += 1
                previous_object = item.payload_hash
                terminal_object_present |= item.payload_hash == terminal_evidence_ref
            index.commit()

            receipts_projection = _CanonicalArrayHasher()
            receipt_count = 0
            previous_receipt: str | None = None
            terminal_receipt_present = False
            rows = source.execute(
                """SELECT sequence, receipt_id, occurrence_id, kind,
                          account_id, account_version, payload_hash,
                          envelope_json, receipt_hash
                   FROM receipts ORDER BY sequence"""
            )
            for row in rows:
                (
                    sequence,
                    receipt_id,
                    occurrence_id,
                    kind,
                    account_id,
                    account_version,
                    payload_hash,
                    envelope_json,
                    receipt_hash,
                ) = row
                raw = bytes(envelope_json)
                envelope = ReceiptEnvelope.model_validate_json(raw)
                if canonical_bytes(envelope) != raw:
                    raise LabError("stored receipt envelope is not canonical JSON")
                table_projection = (
                    int(sequence),
                    str(receipt_id),
                    str(occurrence_id),
                    str(kind),
                    str(account_id),
                    int(account_version),
                    str(payload_hash),
                    str(receipt_hash),
                )
                envelope_projection = (
                    envelope.sequence,
                    envelope.receipt_id,
                    envelope.occurrence_id,
                    envelope.kind,
                    envelope.account_id,
                    envelope.account_version,
                    envelope.payload_hash,
                    envelope.receipt_hash,
                )
                if table_projection != envelope_projection:
                    raise LabError("receipt table projection disagrees with its envelope")
                expected_id = content_hash(
                    {
                        "account_id": envelope.account_id,
                        "account_version": envelope.account_version,
                        "kind": envelope.kind,
                        "object_refs": list(envelope.object_refs),
                        "occurrence_id": envelope.occurrence_id,
                        "parent_refs": list(envelope.parent_refs),
                        "payload_hash": envelope.payload_hash,
                    }
                )
                if envelope.receipt_id != expected_id:
                    raise LabError("receipt identity disagrees with its content binding")
                if envelope.sequence != receipt_count:
                    raise LabError("receipt sequence is not contiguous")
                if envelope.previous_receipt_hash != previous_receipt:
                    raise LabError("receipt chain predecessor mismatch")
                if not _index_has(index, "objects", "payload_hash", envelope.payload_hash):
                    raise LabError("receipt payload object is missing")
                if any(
                    not _index_has(index, "objects", "payload_hash", reference)
                    for reference in envelope.object_refs
                ):
                    raise LabError("receipt content reference is missing")
                if any(
                    not _index_has(index, "receipts", "receipt_id", reference)
                    for reference in envelope.parent_refs
                ):
                    raise LabError("receipt parent reference is missing or forward")
                try:
                    index.execute(
                        """INSERT INTO receipts(receipt_id, occurrence_id, receipt_hash)
                           VALUES (?, ?, ?)""",
                        (
                            envelope.receipt_id,
                            envelope.occurrence_id,
                            envelope.receipt_hash,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise LabError("receipt violates SQLite identity uniqueness") from error
                receipts_projection.update(raw)
                receipt_count += 1
                previous_receipt = envelope.receipt_hash
                terminal_receipt_present |= terminal_evidence_ref is not None and (
                    envelope.payload_hash == terminal_evidence_ref
                    or terminal_evidence_ref in envelope.object_refs
                )
            index.commit()
    except LabError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as error:
        raise LabError(f"invalid SQLite ledger content: {error}") from error
    finally:
        if source is not None:
            source.close()

    _require_quiescent_ledger(path)
    if _ledger_file_identity(path) != identity_before:
        raise LabError("SQLite ledger changed during its read-only snapshot")
    seal = ExternalLedgerSeal(
        receipt_count=receipt_count,
        receipt_head=previous_receipt,
        object_count=object_count,
        objects_projection_ref=objects_projection.hexdigest(),
        receipts_projection_ref=receipts_projection.hexdigest(),
    )
    return _LedgerSnapshot(
        seal=seal,
        terminal_object_present=terminal_object_present,
        terminal_receipt_present=terminal_receipt_present,
    )


def _materialized_ledger_snapshot(
    path: Path,
) -> tuple[tuple[CapsuleObject, ...], tuple[ReceiptEnvelope, ...], ExternalLedgerSeal]:
    transient_paths = tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-shm", "-wal"))
    present_transients = tuple(item for item in transient_paths if item.exists())
    if present_transients:
        names = ", ".join(item.name for item in present_transients)
        raise LabError(
            "ledger snapshot requires a closed, checkpointed writer; "
            f"found transient SQLite state: {names}"
        )

    def file_identity() -> tuple[int, int, str]:
        try:
            stat = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return stat.st_size, stat.st_mtime_ns, digest.hexdigest()
        except OSError as error:
            raise LabError(f"cannot identify the SQLite ledger: {error}") from error

    identity_before = file_identity()
    try:
        uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        object_rows = connection.execute(
            "SELECT payload_hash, canonical_payload FROM objects ORDER BY payload_hash"
        ).fetchall()
        receipt_rows = connection.execute(
            """SELECT sequence, receipt_id, occurrence_id, kind,
                      account_id, account_version, payload_hash,
                      envelope_json, receipt_hash
               FROM receipts ORDER BY sequence"""
        ).fetchall()
    except sqlite3.Error as error:
        raise LabError(f"cannot read the SQLite ledger: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    if any(item.exists() for item in transient_paths):
        raise LabError("read-only ledger inspection created transient SQLite state")
    if file_identity() != identity_before:
        raise LabError("SQLite ledger changed during its read-only snapshot")

    objects: list[CapsuleObject] = []
    object_refs: set[str] = set()
    try:
        for stored_hash, stored_payload in object_rows:
            raw = bytes(stored_payload)
            payload = parse_strict_json(raw)
            if canonical_bytes(payload) != raw:
                raise LabError("stored ledger object is not canonical JSON")
            item = CapsuleObject(payload_hash=str(stored_hash), payload=payload)
            if item.payload_hash in object_refs:
                raise LabError("duplicate object identity in SQLite ledger")
            _reject_private_reasoning(item.payload)
            objects.append(item)
            object_refs.add(item.payload_hash)

        receipts: list[ReceiptEnvelope] = []
        previous: str | None = None
        seen_receipts: set[str] = set()
        for expected_sequence, row in enumerate(receipt_rows):
            (
                sequence,
                receipt_id,
                occurrence_id,
                kind,
                account_id,
                account_version,
                payload_hash,
                envelope_json,
                receipt_hash,
            ) = row
            raw = bytes(envelope_json)
            envelope = ReceiptEnvelope.model_validate_json(raw)
            if canonical_bytes(envelope) != raw:
                raise LabError("stored receipt envelope is not canonical JSON")
            table_projection = (
                int(sequence),
                str(receipt_id),
                str(occurrence_id),
                str(kind),
                str(account_id),
                int(account_version),
                str(payload_hash),
                str(receipt_hash),
            )
            envelope_projection = (
                envelope.sequence,
                envelope.receipt_id,
                envelope.occurrence_id,
                envelope.kind,
                envelope.account_id,
                envelope.account_version,
                envelope.payload_hash,
                envelope.receipt_hash,
            )
            if table_projection != envelope_projection:
                raise LabError("receipt table projection disagrees with its envelope")
            expected_id = content_hash(
                {
                    "account_id": envelope.account_id,
                    "account_version": envelope.account_version,
                    "kind": envelope.kind,
                    "object_refs": list(envelope.object_refs),
                    "occurrence_id": envelope.occurrence_id,
                    "parent_refs": list(envelope.parent_refs),
                    "payload_hash": envelope.payload_hash,
                }
            )
            if envelope.receipt_id != expected_id:
                raise LabError("receipt identity disagrees with its content binding")
            if envelope.sequence != expected_sequence:
                raise LabError("receipt sequence is not contiguous")
            if envelope.previous_receipt_hash != previous:
                raise LabError("receipt chain predecessor mismatch")
            if envelope.payload_hash not in object_refs:
                raise LabError("receipt payload object is missing")
            if any(ref not in object_refs for ref in envelope.object_refs):
                raise LabError("receipt content reference is missing")
            if any(ref not in seen_receipts for ref in envelope.parent_refs):
                raise LabError("receipt parent reference is missing or forward")
            receipts.append(envelope)
            seen_receipts.add(envelope.receipt_id)
            previous = envelope.receipt_hash
    except ValueError as error:
        raise LabError(f"invalid SQLite ledger content: {error}") from error

    object_tuple = tuple(objects)
    receipt_tuple = tuple(receipts)
    seal = ExternalLedgerSeal(
        receipt_count=len(receipt_tuple),
        receipt_head=previous,
        object_count=len(object_tuple),
        objects_projection_ref=content_hash(object_tuple),
        receipts_projection_ref=content_hash(receipt_tuple),
    )
    return object_tuple, receipt_tuple, seal


def _load_lab(root: Path) -> tuple[LabManifest, RunSpec, LabGenesisSeal]:
    manifest = _read_contract(
        _safe_existing(root, CAPSULE_LAB_MANIFEST_PATH, file=True), LabManifest
    )
    assert isinstance(manifest, LabManifest)
    spec = _read_contract(
        _safe_existing(root, manifest.layout.run_spec_path, file=True), RunSpec
    )
    assert isinstance(spec, RunSpec)
    genesis = _read_contract(
        _safe_existing(root, manifest.layout.genesis_path, file=True), LabGenesisSeal
    )
    assert isinstance(genesis, LabGenesisSeal)
    if spec.lab_manifest_ref != manifest.digest:
        raise LabError("run specification does not bind the exact lab manifest")
    if genesis.lab_manifest_ref != manifest.digest or genesis.run_spec_ref != spec.digest:
        raise LabError("genesis seal does not bind the exact lab and run")
    return manifest, spec, genesis


def initialize_lab(
    root: str | Path,
    *,
    manifest: LabManifest,
    run_spec: RunSpec,
) -> LabGenesisSeal:
    """Create one empty lab and bind its zero-state before any domain work."""

    if run_spec.lab_manifest_ref != manifest.digest:
        raise LabError("run specification must bind the exact lab manifest")
    supplied = Path(root)
    if is_link_like(supplied):
        raise LabError("lab root must not be a link-like path")
    if supplied.exists():
        if not supplied.is_dir():
            raise LabError("lab root must be a directory")
        if any(supplied.iterdir()):
            raise LabError("lab genesis requires an absent or empty root")
    else:
        try:
            supplied.parent.resolve(strict=True)
        except OSError as error:
            raise LabError("lab parent must already exist") from error
        supplied.mkdir()
    resolved = _resolved_root(supplied)

    layout = manifest.layout
    manifest_path = _safe_new_path(resolved, layout.manifest_path)
    spec_path = _safe_new_path(resolved, layout.run_spec_path)
    ledger_path = _safe_new_path(resolved, layout.ledger_path)
    domain_path = _safe_new_path(resolved, layout.domain_state_path)
    domain_path.mkdir(parents=True, exist_ok=False)
    _write_immutable(manifest_path, canonical_bytes(manifest))
    _write_immutable(spec_path, canonical_bytes(run_spec))

    from strongwiz.ledger import SQLiteLedger

    with SQLiteLedger(ledger_path):
        pass
    ledger_seal = _ledger_snapshot(ledger_path).seal
    domain_seal, _ = _domain_snapshot(domain_path)
    if (
        ledger_seal.receipt_count != 0
        or ledger_seal.object_count != 0
        or ledger_seal.receipt_head is not None
        or domain_seal.entry_count != 0
    ):
        raise LabError("lab genesis failed to establish an empty state")
    genesis = LabGenesisSeal(
        lab_manifest_ref=manifest.digest,
        run_spec_ref=run_spec.digest,
    )
    _write_immutable(_safe_new_path(resolved, layout.genesis_path), canonical_bytes(genesis))
    verify_lab(resolved, require_current_genesis=True)
    return genesis


def verify_lab(root: str | Path, *, require_current_genesis: bool = False) -> LabVerification:
    """Verify control contracts, path boundaries, ledger closure, and any run seal."""

    resolved = _resolved_root(root)
    _scan_tree(resolved)
    manifest, spec, genesis = _load_lab(resolved)
    ledger_path = _safe_existing(resolved, manifest.layout.ledger_path, file=True)
    domain_path = _safe_existing(resolved, manifest.layout.domain_state_path, file=False)
    run_seal_path = resolved.joinpath(*PurePosixPath(manifest.layout.run_seal_path).parts)
    if is_link_like(run_seal_path):
        raise LabError("run seal must not be link-like")
    run_seal: RunSeal | None = None
    if run_seal_path.exists():
        loaded_seal = _read_contract(run_seal_path, RunSeal)
        assert isinstance(loaded_seal, RunSeal)
        run_seal = loaded_seal
    ledger_snapshot = _ledger_snapshot(
        ledger_path,
        terminal_evidence_ref=(None if run_seal is None else run_seal.terminal_evidence_ref),
    )
    ledger_seal = ledger_snapshot.seal
    domain_seal, _ = _domain_snapshot(domain_path)
    domain_count = domain_seal.entry_count
    _verify_lab_tree(
        resolved,
        manifest,
        domain_seal,
        run_seal_present=run_seal_path.exists(),
    )
    current_genesis = (
        ledger_seal.receipt_count == 0
        and ledger_seal.object_count == 0
        and ledger_seal.receipt_head is None
        and domain_count == 0
    )
    if require_current_genesis and not current_genesis:
        raise LabError("current lab state no longer matches its zero-state genesis")

    run_seal_ref: str | None = None
    if run_seal is not None:
        if (
            run_seal.run_id != spec.run_id
            or run_seal.lab_manifest_ref != manifest.digest
            or run_seal.run_spec_ref != spec.digest
            or run_seal.genesis_ref != genesis.digest
            or run_seal.ledger_seal != ledger_seal
            or run_seal.domain_state_seal != domain_seal
            or run_seal.terminal_authority_source != spec.terminal_authority_source
            or not ledger_snapshot.terminal_object_present
            or not ledger_snapshot.terminal_receipt_present
        ):
            raise LabError("run seal disagrees with the current sealed lab state")
        if (
            run_seal.completion_genuinely_observed
            and run_seal.terminal_state != spec.success_state
        ):
            raise LabError("observed completion does not match the predeclared success state")
        run_seal_ref = run_seal.digest
    return LabVerification(
        lab_manifest_ref=manifest.digest,
        run_spec_ref=spec.digest,
        genesis_ref=genesis.digest,
        ledger_seal=ledger_seal,
        domain_state_seal=domain_seal,
        domain_state_entry_count=domain_count,
        current_state_matches_genesis=current_genesis,
        run_seal_ref=run_seal_ref,
    )


def verify_lab_genesis(root: str | Path) -> LabVerification:
    """Require that a newly initialized lab still has exactly zero run state."""

    return verify_lab(root, require_current_genesis=True)


def seal_run(
    root: str | Path,
    *,
    disposition: RunDisposition,
    terminal_state: str,
    terminal_evidence_ref: str,
    completion_genuinely_observed: bool,
    concise_result_summary: str,
) -> RunSeal:
    """Bind one terminal report to the exact closed SQLite projection."""

    resolved = _resolved_root(root)
    verification = verify_lab(resolved)
    manifest, spec, genesis = _load_lab(resolved)
    ledger_snapshot = _ledger_snapshot(
        _safe_existing(resolved, manifest.layout.ledger_path, file=True),
        terminal_evidence_ref=terminal_evidence_ref,
    )
    ledger_seal = ledger_snapshot.seal
    domain_seal, _ = _domain_snapshot(
        _safe_existing(resolved, manifest.layout.domain_state_path, file=False)
    )
    if verification.run_seal_ref is not None:
        existing = _read_contract(
            _safe_existing(resolved, manifest.layout.run_seal_path, file=True), RunSeal
        )
        assert isinstance(existing, RunSeal)
    else:
        existing = None
    if not ledger_snapshot.terminal_object_present:
        raise LabError("terminal evidence must be a content object in the sealed ledger")
    if not ledger_snapshot.terminal_receipt_present:
        raise LabError("terminal evidence must be bound by a sealed ledger receipt")
    if completion_genuinely_observed and terminal_state != spec.success_state:
        raise LabError("observed completion must match the predeclared success state")
    seal = RunSeal(
        run_id=spec.run_id,
        lab_manifest_ref=manifest.digest,
        run_spec_ref=spec.digest,
        genesis_ref=genesis.digest,
        ledger_seal=ledger_seal,
        domain_state_seal=domain_seal,
        disposition=disposition,
        terminal_state=terminal_state,
        terminal_evidence_ref=terminal_evidence_ref,
        completion_genuinely_observed=completion_genuinely_observed,
        terminal_authority_source=spec.terminal_authority_source,
        concise_result_summary=concise_result_summary,
    )
    if existing is not None:
        if existing != seal:
            raise LabError("an immutable run seal already exists with different content")
        return existing
    _write_immutable(
        _safe_new_path(resolved, manifest.layout.run_seal_path), canonical_bytes(seal)
    )
    verify_lab(resolved)
    return seal


def _jsonl(values: tuple[ContractModel, ...]) -> bytes:
    return b"".join(canonical_bytes(value) + b"\n" for value in values)


def _capsule_files(
    payloads: Mapping[CapsuleFileRole, tuple[str, bytes]],
) -> tuple[CapsuleFile, ...]:
    files = (
        CapsuleFile(
            role=role,
            relative_path=path,
            size_bytes=len(payload),
            sha256=sha256_bytes(payload),
        )
        for role, (path, payload) in payloads.items()
    )
    return tuple(sorted(files, key=lambda item: item.relative_path))


def pack_evidence(
    root: str | Path,
    destination: str | Path,
    *,
    capsule_name: str | None = None,
    acknowledge_opaque_domain_state: bool = False,
) -> EvidenceCapsuleManifest:
    """Export a closed capsule after acknowledging any opaque domain-state copy."""

    resolved = _resolved_root(root)
    verification = verify_lab(resolved)
    if verification.run_seal_ref is None:
        raise LabError("a run must be sealed before its evidence can be packed")
    manifest, spec, genesis = _load_lab(resolved)
    run_seal = _read_contract(
        _safe_existing(resolved, manifest.layout.run_seal_path, file=True), RunSeal
    )
    assert isinstance(run_seal, RunSeal)
    objects, receipts, ledger_seal = _materialized_ledger_snapshot(
        _safe_existing(resolved, manifest.layout.ledger_path, file=True)
    )
    domain_seal, domain_payloads = _domain_snapshot(
        _safe_existing(resolved, manifest.layout.domain_state_path, file=False)
    )
    if run_seal.ledger_seal != ledger_seal:
        raise LabError("ledger changed after the immutable run seal")
    if run_seal.domain_state_seal != domain_seal:
        raise LabError("domain state changed after the immutable run seal")
    if domain_seal.entry_count and not acknowledge_opaque_domain_state:
        raise LabError(
            "domain state is opaque and unsanitized; explicitly acknowledge its local copy "
            "and review it separately before any publication"
        )

    payloads: dict[CapsuleFileRole, tuple[str, bytes]] = {
        CapsuleFileRole.LAB_MANIFEST: (
            CAPSULE_LAB_MANIFEST_PATH,
            canonical_bytes(manifest),
        ),
        CapsuleFileRole.RUN_SPEC: (CAPSULE_RUN_SPEC_PATH, canonical_bytes(spec)),
        CapsuleFileRole.LAB_GENESIS: (CAPSULE_GENESIS_PATH, canonical_bytes(genesis)),
        CapsuleFileRole.RUN_SEAL: (CAPSULE_RUN_SEAL_PATH, canonical_bytes(run_seal)),
        CapsuleFileRole.LEDGER_OBJECTS: (
            CAPSULE_OBJECTS_PATH,
            _jsonl(objects),
        ),
        CapsuleFileRole.LEDGER_RECEIPTS: (
            CAPSULE_RECEIPTS_PATH,
            _jsonl(receipts),
        ),
    }
    capsule = EvidenceCapsuleManifest(
        capsule_name=capsule_name or f"{manifest.lab_id}-{spec.run_id}",
        lab_id=manifest.lab_id,
        run_id=spec.run_id,
        lab_manifest_ref=manifest.digest,
        run_spec_ref=spec.digest,
        genesis_ref=genesis.digest,
        run_seal_ref=run_seal.digest,
        ledger_seal=ledger_seal,
        domain_state_seal=domain_seal,
        files=_capsule_files(payloads),
        terminal_evidence_ref=run_seal.terminal_evidence_ref,
        completion_genuinely_observed=run_seal.completion_genuinely_observed,
        opaque_domain_state_copy_acknowledged=(
            acknowledge_opaque_domain_state and domain_seal.entry_count > 0
        ),
    )

    target = Path(destination)
    if is_link_like(target):
        raise LabError("capsule destination must not be link-like")
    target_resolved = target.resolve(strict=False)
    if (
        target_resolved == resolved
        or resolved in target_resolved.parents
        or target_resolved in resolved.parents
    ):
        raise LabError("capsule destination and sealed lab must be disjoint")
    if target.exists():
        existing = verify_evidence_capsule(target)
        if existing != capsule:
            raise LabError("immutable capsule destination contains different evidence")
        return existing
    try:
        target.parent.resolve(strict=True)
    except OSError as error:
        raise LabError("capsule parent must already exist") from error
    target.mkdir()
    target_root = _resolved_root(target)
    for _, (relative_path, payload) in payloads.items():
        _write_immutable(_safe_new_path(target_root, relative_path), payload)
    capsule_domain = _safe_new_path(target_root, CAPSULE_DOMAIN_STATE_PATH)
    capsule_domain.mkdir()
    for entry in domain_seal.entries:
        relative = f"{CAPSULE_DOMAIN_STATE_PATH}/{entry.relative_path}"
        destination_path = _safe_new_path(target_root, relative)
        if entry.kind == "directory":
            destination_path.mkdir()
        else:
            _write_immutable(destination_path, domain_payloads[entry.relative_path])
    _write_immutable(
        _safe_new_path(target_root, CAPSULE_MANIFEST_PATH), canonical_bytes(capsule)
    )
    verify_evidence_capsule(target_root, expected_capsule_ref=capsule.digest)
    return capsule


def _parse_object_jsonl(raw: bytes) -> tuple[CapsuleObject, ...]:
    if raw and not raw.endswith(b"\n"):
        raise LabError("capsule JSONL must end with one LF")
    output: list[CapsuleObject] = []
    for line in raw.splitlines():
        try:
            value = CapsuleObject.model_validate_json(line)
        except ValueError as error:
            raise LabError(f"invalid capsule object JSONL: {error}") from error
        if canonical_bytes(value) != line:
            raise LabError("capsule object JSONL is not canonical")
        _reject_private_reasoning(value.payload)
        output.append(value)
    if tuple(item.payload_hash for item in output) != tuple(
        sorted({item.payload_hash for item in output})
    ):
        raise LabError("capsule objects must be sorted and unique")
    return tuple(output)


def _parse_receipt_jsonl(raw: bytes) -> tuple[ReceiptEnvelope, ...]:
    if raw and not raw.endswith(b"\n"):
        raise LabError("capsule JSONL must end with one LF")
    output: list[ReceiptEnvelope] = []
    for line in raw.splitlines():
        try:
            value = ReceiptEnvelope.model_validate_json(line)
        except ValueError as error:
            raise LabError(f"invalid capsule receipt JSONL: {error}") from error
        if canonical_bytes(value) != line:
            raise LabError("capsule receipt JSONL is not canonical")
        output.append(value)
    return tuple(output)


def _seal_from_exports(
    objects: tuple[CapsuleObject, ...], receipts: tuple[ReceiptEnvelope, ...]
) -> ExternalLedgerSeal:
    object_refs = {item.payload_hash for item in objects}
    previous: str | None = None
    seen_receipts: set[str] = set()
    seen_occurrences: set[str] = set()
    seen_receipt_hashes: set[str] = set()
    for sequence, envelope in enumerate(receipts):
        expected_id = content_hash(
            {
                "account_id": envelope.account_id,
                "account_version": envelope.account_version,
                "kind": envelope.kind,
                "object_refs": list(envelope.object_refs),
                "occurrence_id": envelope.occurrence_id,
                "parent_refs": list(envelope.parent_refs),
                "payload_hash": envelope.payload_hash,
            }
        )
        if envelope.receipt_id != expected_id:
            raise LabError("exported receipt identity disagrees with its binding")
        if (
            envelope.receipt_id in seen_receipts
            or envelope.occurrence_id in seen_occurrences
            or envelope.receipt_hash in seen_receipt_hashes
        ):
            raise LabError("exported receipt violates SQLite identity uniqueness")
        if envelope.sequence != sequence or envelope.previous_receipt_hash != previous:
            raise LabError("exported receipt chain is not contiguous")
        if envelope.payload_hash not in object_refs or any(
            ref not in object_refs for ref in envelope.object_refs
        ):
            raise LabError("exported receipt references a missing object")
        if any(ref not in seen_receipts for ref in envelope.parent_refs):
            raise LabError("exported receipt references a missing or forward parent")
        seen_receipts.add(envelope.receipt_id)
        seen_occurrences.add(envelope.occurrence_id)
        seen_receipt_hashes.add(envelope.receipt_hash)
        previous = envelope.receipt_hash
    return ExternalLedgerSeal(
        receipt_count=len(receipts),
        receipt_head=previous,
        object_count=len(objects),
        objects_projection_ref=content_hash(objects),
        receipts_projection_ref=content_hash(receipts),
    )


def verify_evidence_capsule(
    root: str | Path, *, expected_capsule_ref: str | None = None
) -> EvidenceCapsuleManifest:
    """Verify every byte, path, binding, receipt, and sealed domain-state entry."""

    resolved = _resolved_root(root)
    actual_files, actual_directories = _scan_tree_paths(resolved)
    manifest_path = _safe_existing(resolved, CAPSULE_MANIFEST_PATH, file=True)
    capsule = _read_contract(manifest_path, EvidenceCapsuleManifest)
    assert isinstance(capsule, EvidenceCapsuleManifest)
    if expected_capsule_ref is not None:
        _require_digest(expected_capsule_ref, label="expected capsule reference")
        if capsule.digest != expected_capsule_ref:
            raise LabError("capsule digest disagrees with the expected identity")

    expected_files = {
        CAPSULE_MANIFEST_PATH,
        *(item.relative_path for item in capsule.files),
        *(
            f"{CAPSULE_DOMAIN_STATE_PATH}/{entry.relative_path}"
            for entry in capsule.domain_state_seal.entries
            if entry.kind == "file"
        ),
    }
    expected_directories = _expected_parent_directories(expected_files)
    expected_directories.add(CAPSULE_DOMAIN_STATE_PATH)
    expected_directories.update(
        f"{CAPSULE_DOMAIN_STATE_PATH}/{entry.relative_path}"
        for entry in capsule.domain_state_seal.entries
        if entry.kind == "directory"
    )
    actual_file_paths = {item.relative_to(resolved).as_posix() for item in actual_files}
    actual_directory_paths = {
        item.relative_to(resolved).as_posix() for item in actual_directories
    }
    if actual_file_paths != expected_files or actual_directory_paths != expected_directories:
        raise LabError("capsule contains missing or undeclared files or directories")
    raw_by_role: dict[CapsuleFileRole, bytes] = {}
    for frozen in capsule.files:
        path = _safe_existing(resolved, frozen.relative_path, file=True)
        raw = path.read_bytes()
        if len(raw) != frozen.size_bytes or sha256_bytes(raw) != frozen.sha256:
            raise LabError(
                f"capsule file disagrees with its exact digest: {frozen.relative_path}"
            )
        raw_by_role[frozen.role] = raw

    manifest = LabManifest.model_validate_json(raw_by_role[CapsuleFileRole.LAB_MANIFEST])
    spec = RunSpec.model_validate_json(raw_by_role[CapsuleFileRole.RUN_SPEC])
    genesis = LabGenesisSeal.model_validate_json(raw_by_role[CapsuleFileRole.LAB_GENESIS])
    run_seal = RunSeal.model_validate_json(raw_by_role[CapsuleFileRole.RUN_SEAL])
    for model, role in (
        (manifest, CapsuleFileRole.LAB_MANIFEST),
        (spec, CapsuleFileRole.RUN_SPEC),
        (genesis, CapsuleFileRole.LAB_GENESIS),
        (run_seal, CapsuleFileRole.RUN_SEAL),
    ):
        if canonical_bytes(model) != raw_by_role[role]:
            raise LabError("capsule contract file is not canonical JSON")

    objects = _parse_object_jsonl(raw_by_role[CapsuleFileRole.LEDGER_OBJECTS])
    receipts = _parse_receipt_jsonl(raw_by_role[CapsuleFileRole.LEDGER_RECEIPTS])
    ledger_seal = _seal_from_exports(objects, receipts)
    domain_seal, _ = _domain_snapshot(
        _safe_existing(resolved, CAPSULE_DOMAIN_STATE_PATH, file=False)
    )
    object_refs = {item.payload_hash for item in objects}
    if (
        spec.lab_manifest_ref != manifest.digest
        or genesis.lab_manifest_ref != manifest.digest
        or genesis.run_spec_ref != spec.digest
        or run_seal.run_id != spec.run_id
        or run_seal.lab_manifest_ref != manifest.digest
        or run_seal.run_spec_ref != spec.digest
        or run_seal.genesis_ref != genesis.digest
        or run_seal.ledger_seal != ledger_seal
        or run_seal.domain_state_seal != domain_seal
        or run_seal.terminal_authority_source != spec.terminal_authority_source
        or run_seal.terminal_evidence_ref not in object_refs
        or not _receipt_references(run_seal.terminal_evidence_ref, receipts)
        or capsule.lab_id != manifest.lab_id
        or capsule.run_id != spec.run_id
        or capsule.lab_manifest_ref != manifest.digest
        or capsule.run_spec_ref != spec.digest
        or capsule.genesis_ref != genesis.digest
        or capsule.run_seal_ref != run_seal.digest
        or capsule.ledger_seal != ledger_seal
        or capsule.domain_state_seal != domain_seal
        or capsule.terminal_evidence_ref != run_seal.terminal_evidence_ref
        or capsule.completion_genuinely_observed != run_seal.completion_genuinely_observed
    ):
        raise LabError("capsule cross-object bindings do not close")
    if run_seal.completion_genuinely_observed and run_seal.terminal_state != spec.success_state:
        raise LabError("capsule completion does not match the predeclared success state")
    return capsule
