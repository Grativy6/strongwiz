"""A0BK-inspired account, version, and residual bookkeeping.

These accounts preserve scope and revision lineage.  They do not represent
permission, consent, jurisdiction, truth, or closure.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from strongwiz.canonical import content_hash
from strongwiz.contracts import ContractModel, NonNegativeInt


class AccountError(ValueError):
    pass


class AccountKind(StrEnum):
    ROOT = "root"
    CHILD = "child"
    SUCCESSOR = "successor"
    VERSION = "version"


class ResidualStatus(StrEnum):
    ACTIVE = "active"
    SPLIT = "split"
    DISCHARGED = "discharged"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"


class AccountHeader(ContractModel):
    account_id: str
    origin_id: str
    kind: AccountKind
    version: NonNegativeInt
    scope_id: str
    cut_ref: str
    parent_account_id: str | None = None
    parent_account_ref: str | None = None
    predecessor_account_id: str | None = None
    predecessor_account_ref: str | None = None
    prior_version_ref: str | None = None
    witness_refs: tuple[str, ...]
    material_delta_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_header(self) -> AccountHeader:
        if not all((self.account_id, self.origin_id, self.scope_id, self.cut_ref)):
            raise ValueError("account identity, scope, and supplied cut are required")
        if not self.witness_refs:
            raise ValueError("account openings require at least one supplied witness")
        if self.kind is AccountKind.ROOT:
            if self.version != 0 or any(
                value is not None
                for value in (
                    self.parent_account_id,
                    self.parent_account_ref,
                    self.predecessor_account_id,
                    self.predecessor_account_ref,
                    self.prior_version_ref,
                )
            ):
                raise ValueError(
                    "root account cannot inherit a parent, predecessor, or version"
                )
        elif self.kind is AccountKind.CHILD:
            if (
                self.parent_account_id is None
                or self.parent_account_ref is None
                or self.version != 0
            ):
                raise ValueError(
                    "child account requires one exact parent and starts at version zero"
                )
        elif self.kind is AccountKind.SUCCESSOR:
            if (
                self.predecessor_account_id is None
                or self.predecessor_account_ref is None
                or self.version != 0
            ):
                raise ValueError(
                    "successor requires one exact predecessor and starts at version zero"
                )
        else:
            if self.prior_version_ref is None or self.version == 0:
                raise ValueError("version transition requires an exact prior version")
            if not self.material_delta_refs:
                raise ValueError("version transition requires a material delta")
        return self


class ResidualRecord(ContractModel):
    residual_id: str
    account_id: str
    account_version: NonNegativeInt
    statement: str
    status: ResidualStatus
    source_refs: tuple[str, ...]
    parent_residual_ids: tuple[str, ...] = ()
    transition_reason: str | None = None

    @model_validator(mode="after")
    def validate_residual(self) -> ResidualRecord:
        if not all((self.residual_id, self.account_id, self.statement)):
            raise ValueError("residual identity, account, and statement are required")
        if not self.source_refs:
            raise ValueError("residuals require source evidence")
        if self.parent_residual_ids and not self.transition_reason:
            raise ValueError("residual transitions require a reason")
        return self


def _new_account_id(
    *,
    kind: AccountKind,
    scope_id: str,
    cut_ref: str,
    parent_account_ref: str | None,
    predecessor_account_ref: str | None,
) -> str:
    return content_hash(
        {
            "cut_ref": cut_ref,
            "kind": kind.value,
            "parent_account_ref": parent_account_ref,
            "predecessor_account_ref": predecessor_account_ref,
            "scope_id": scope_id,
        }
    )


class AccountBook:
    """Append-only local account registry with exact-version lookup."""

    def __init__(self) -> None:
        self._headers: dict[tuple[str, int], AccountHeader] = {}
        self._residuals: dict[str, ResidualRecord] = {}

    def _record(self, header: AccountHeader) -> AccountHeader:
        key = (header.account_id, header.version)
        current = self._headers.get(key)
        if current is not None and current != header:
            raise AccountError("account version identity cannot be rewritten")
        self._headers[key] = header
        return header

    def open_root(
        self, *, scope_id: str, cut_ref: str, witness_refs: tuple[str, ...]
    ) -> AccountHeader:
        account_id = _new_account_id(
            kind=AccountKind.ROOT,
            scope_id=scope_id,
            cut_ref=cut_ref,
            parent_account_ref=None,
            predecessor_account_ref=None,
        )
        return self._record(
            AccountHeader(
                account_id=account_id,
                origin_id=account_id,
                kind=AccountKind.ROOT,
                version=0,
                scope_id=scope_id,
                cut_ref=cut_ref,
                witness_refs=witness_refs,
            )
        )

    def open_child(
        self,
        parent: AccountHeader,
        *,
        scope_id: str,
        cut_ref: str,
        witness_refs: tuple[str, ...],
    ) -> AccountHeader:
        self.require(parent.account_id, parent.version)
        account_id = _new_account_id(
            kind=AccountKind.CHILD,
            scope_id=scope_id,
            cut_ref=cut_ref,
            parent_account_ref=parent.digest,
            predecessor_account_ref=None,
        )
        return self._record(
            AccountHeader(
                account_id=account_id,
                origin_id=account_id,
                kind=AccountKind.CHILD,
                version=0,
                scope_id=scope_id,
                cut_ref=cut_ref,
                parent_account_id=parent.account_id,
                parent_account_ref=parent.digest,
                witness_refs=witness_refs,
            )
        )

    def open_successor(
        self,
        predecessor: AccountHeader,
        *,
        scope_id: str,
        cut_ref: str,
        witness_refs: tuple[str, ...],
    ) -> AccountHeader:
        self.require(predecessor.account_id, predecessor.version)
        account_id = _new_account_id(
            kind=AccountKind.SUCCESSOR,
            scope_id=scope_id,
            cut_ref=cut_ref,
            parent_account_ref=None,
            predecessor_account_ref=predecessor.digest,
        )
        return self._record(
            AccountHeader(
                account_id=account_id,
                origin_id=account_id,
                kind=AccountKind.SUCCESSOR,
                version=0,
                scope_id=scope_id,
                cut_ref=cut_ref,
                predecessor_account_id=predecessor.account_id,
                predecessor_account_ref=predecessor.digest,
                witness_refs=witness_refs,
            )
        )

    def open_version(
        self,
        previous: AccountHeader,
        *,
        cut_ref: str,
        witness_refs: tuple[str, ...],
        material_delta_refs: tuple[str, ...],
    ) -> AccountHeader:
        self.require(previous.account_id, previous.version)
        next_version = previous.version + 1
        candidate = AccountHeader(
            account_id=previous.account_id,
            origin_id=previous.origin_id,
            kind=AccountKind.VERSION,
            version=next_version,
            scope_id=previous.scope_id,
            cut_ref=cut_ref,
            prior_version_ref=previous.digest,
            witness_refs=witness_refs,
            material_delta_refs=material_delta_refs,
        )
        if (previous.account_id, next_version) in self._headers:
            existing = self._headers[(previous.account_id, next_version)]
            if existing != candidate:
                raise AccountError(
                    "only one exact successor version may occupy an account step"
                )
            return existing
        return self._record(candidate)

    def require(self, account_id: str, version: int) -> AccountHeader:
        try:
            return self._headers[(account_id, version)]
        except KeyError as error:
            raise AccountError("unknown exact account version") from error

    def register_residual(
        self,
        account: AccountHeader,
        *,
        statement: str,
        source_refs: tuple[str, ...],
    ) -> ResidualRecord:
        self.require(account.account_id, account.version)
        residual_id = content_hash(
            {
                "account_id": account.account_id,
                "account_version": account.version,
                "source_refs": source_refs,
                "statement": statement,
            }
        )
        record = ResidualRecord(
            residual_id=residual_id,
            account_id=account.account_id,
            account_version=account.version,
            statement=statement,
            status=ResidualStatus.ACTIVE,
            source_refs=source_refs,
        )
        current = self._residuals.get(residual_id)
        if current is not None and current != record:
            raise AccountError("residual identity cannot be rewritten")
        self._residuals[residual_id] = record
        return record

    def transition_residual(
        self,
        prior_ids: tuple[str, ...],
        *,
        account: AccountHeader,
        statement: str,
        status: ResidualStatus,
        source_refs: tuple[str, ...],
        reason: str,
    ) -> ResidualRecord:
        if not prior_ids or status is ResidualStatus.ACTIVE:
            raise AccountError("residual transition requires parents and a transition status")
        self.require(account.account_id, account.version)
        for prior_id in prior_ids:
            if prior_id not in self._residuals:
                raise AccountError("residual transition parent is unknown")
        residual_id = content_hash(
            {
                "account": account.digest,
                "parents": prior_ids,
                "reason": reason,
                "source_refs": source_refs,
                "statement": statement,
                "status": status.value,
            }
        )
        record = ResidualRecord(
            residual_id=residual_id,
            account_id=account.account_id,
            account_version=account.version,
            statement=statement,
            status=status,
            source_refs=source_refs,
            parent_residual_ids=prior_ids,
            transition_reason=reason,
        )
        self._residuals[residual_id] = record
        return record

    @property
    def headers(self) -> tuple[AccountHeader, ...]:
        return tuple(self._headers[key] for key in sorted(self._headers))

    @property
    def residuals(self) -> tuple[ResidualRecord, ...]:
        return tuple(self._residuals[key] for key in sorted(self._residuals))
