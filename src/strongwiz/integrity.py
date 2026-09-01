"""Frozen runtime identity that includes the action-selecting model boundary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt


class IntegrityError(ValueError):
    pass


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class FrozenFile(ContractModel):
    relative_path: str
    size_bytes: NonNegativeInt
    sha256: str

    @model_validator(mode="after")
    def validate_file(self) -> FrozenFile:
        path = Path(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or not _DIGEST.fullmatch(self.sha256)
        ):
            raise ValueError("frozen file requires a safe relative path and SHA-256")
        return self


class FrozenRuntimeManifest(ContractModel):
    schema_id: str = Field(default="strongwiz.frozen-runtime.v1", alias="schema")
    package_version: str
    contract_schema: str
    source_files: tuple[FrozenFile, ...]
    configuration_ref: str
    dependency_lock_ref: str
    model_driver_id: str
    model_driver_version: str
    model_driver_artifact_ref: str
    domain_adapter_id: str
    domain_adapter_version: str
    domain_adapter_artifact_ref: str
    capability_refs: tuple[str, ...]
    policy_refs: tuple[str, ...]
    runtime_description: str

    @model_validator(mode="after")
    def validate_manifest(self) -> FrozenRuntimeManifest:
        required = (
            self.package_version,
            self.contract_schema,
            self.configuration_ref,
            self.dependency_lock_ref,
            self.model_driver_id,
            self.model_driver_version,
            self.model_driver_artifact_ref,
            self.domain_adapter_id,
            self.domain_adapter_version,
            self.domain_adapter_artifact_ref,
            self.runtime_description,
        )
        if not all(value.strip() for value in required):
            raise ValueError(
                "frozen runtime must bind package, model, domain, config, and runtime"
            )
        if not self.source_files:
            raise ValueError("frozen runtime must contain at least one source file")
        digest_refs = (
            self.configuration_ref,
            self.dependency_lock_ref,
            self.model_driver_artifact_ref,
            self.domain_adapter_artifact_ref,
            *self.capability_refs,
            *self.policy_refs,
        )
        if any(not _DIGEST.fullmatch(value) for value in digest_refs):
            raise ValueError("frozen runtime evidence references must be SHA-256 digests")
        paths = tuple(item.relative_path for item in self.source_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("frozen source files must be sorted and unique")
        return self

    @property
    def manifest_ref(self) -> str:
        return self.digest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_files(root: str | Path, paths: Iterable[str | Path]) -> tuple[FrozenFile, ...]:
    """Freeze exact regular files while refusing paths outside the supplied root."""

    resolved_root = Path(root).resolve(strict=True)
    frozen: list[FrozenFile] = []
    seen: set[str] = set()
    for supplied in paths:
        relative = Path(supplied)
        raw_candidate = relative if relative.is_absolute() else resolved_root / relative
        if raw_candidate.is_symlink():
            raise IntegrityError("frozen inputs must not be symbolic links")
        candidate = raw_candidate.resolve(strict=True)
        try:
            canonical_relative = candidate.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise IntegrityError("frozen path escapes the declared root") from error
        if not candidate.is_file():
            raise IntegrityError("frozen inputs must be ordinary files")
        if canonical_relative in seen:
            raise IntegrityError("frozen input path is duplicated")
        seen.add(canonical_relative)
        frozen.append(
            FrozenFile(
                relative_path=canonical_relative,
                size_bytes=candidate.stat().st_size,
                sha256=sha256_file(candidate),
            )
        )
    return tuple(sorted(frozen, key=lambda item: item.relative_path))


def verify_frozen_files(root: str | Path, files: tuple[FrozenFile, ...]) -> None:
    resolved_root = Path(root).resolve(strict=True)
    for frozen in files:
        raw_candidate = resolved_root / frozen.relative_path
        if raw_candidate.is_symlink():
            raise IntegrityError(f"frozen file became a link: {frozen.relative_path}")
        candidate = raw_candidate.resolve(strict=True)
        try:
            candidate.relative_to(resolved_root)
        except ValueError as error:
            raise IntegrityError("manifest path escapes the declared root") from error
        if not candidate.is_file():
            raise IntegrityError(
                f"frozen file is missing or not regular: {frozen.relative_path}"
            )
        if candidate.stat().st_size != frozen.size_bytes:
            raise IntegrityError(f"frozen file size changed: {frozen.relative_path}")
        if sha256_file(candidate) != frozen.sha256:
            raise IntegrityError(f"frozen file digest changed: {frozen.relative_path}")
