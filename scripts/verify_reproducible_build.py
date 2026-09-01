"""Build Strongwiz twice and emit a canonical local artifact-identity receipt.

The script refuses a dirty tree and keeps every generated path beneath the
repository.  It prepares release evidence; it does not create a tag, upload an
artifact, publish a release, or change legal terms.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import os
import re
import secrets
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

from strongwiz import __version__
from strongwiz.canonical import canonical_bytes
from strongwiz.pathsafe import is_link_like, is_portable_component


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _inside(root: Path, supplied: Path) -> Path:
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("release evidence paths must remain inside the repository") from error
    cursor = root
    for part in relative.parts:
        cursor /= part
        if is_link_like(cursor):
            raise ValueError("release evidence paths must not cross link-like entries")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("release evidence paths must remain inside the repository") from error
    return resolved


def _artifact_hashes(directory: Path) -> dict[str, str]:
    files = tuple(sorted(path for path in directory.iterdir() if path.is_file()))
    if not files:
        raise RuntimeError("the build produced no artifacts")
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def _validated_sdist_members(
    source: tarfile.TarFile,
    expected_root: str,
) -> tuple[tarfile.TarInfo, ...]:
    """Accept only the narrow regular-file/directory sdist surface we can preserve."""

    if source.pax_headers:
        raise ValueError("sdist has unsupported global PAX metadata")
    members = tuple(source.getmembers())
    seen: set[str] = set()
    seen_casefolded: set[str] = set()
    root_count = 0
    for member in members:
        name = member.name
        parts = name.split("/")
        portable = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or portable.is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
            or any(not is_portable_component(part) for part in parts)
            or portable.as_posix() != name
            or parts[0] != expected_root
        ):
            raise ValueError(f"sdist member has an unsafe or unexpected path: {name!r}")
        if name in seen:
            raise ValueError(f"sdist contains a duplicate member path: {name!r}")
        seen.add(name)
        folded_name = name.casefold()
        if folded_name in seen_casefolded:
            raise ValueError(f"sdist contains a case-folding path collision: {name!r}")
        seen_casefolded.add(folded_name)
        if name == expected_root:
            root_count += 1
            if not member.isdir():
                raise ValueError("sdist top-level root must be a directory")
        elif not (member.isfile() or member.isdir()):
            raise ValueError(f"sdist member type is not supported: {name!r}")
        if member.mode & ~0o777:
            raise ValueError(f"sdist member has unsupported mode bits: {name!r}")
        if member.linkname:
            raise ValueError(f"sdist member has unsupported link metadata: {name!r}")
        if set(member.pax_headers) - {"mtime"}:
            raise ValueError(f"sdist member has unsupported PAX metadata: {name!r}")
    if root_count != 1:
        raise ValueError("sdist must contain exactly one declared top-level root directory")
    file_paths = {member.name.casefold() for member in members if member.isfile()}
    for member in members:
        parts = member.name.split("/")
        for boundary in range(1, len(parts)):
            ancestor = "/".join(parts[:boundary]).casefold()
            if ancestor in file_paths:
                raise ValueError(
                    f"sdist regular file is an ancestor of another member: {member.name!r}"
                )
    return members


def _normalize_sdist(path: Path, source_date_epoch: int) -> None:
    """Normalize only accepted sdist member and gzip timestamps."""

    temporary = path.with_name(f".{path.name}.normalized")
    if temporary.exists() or is_link_like(temporary):
        raise FileExistsError(f"sdist normalization path already exists: {temporary}")
    expected_root = path.name.removesuffix(".tar.gz")
    try:
        with tarfile.open(path, "r:gz") as source:
            members = _validated_sdist_members(source, expected_root)
            with temporary.open("xb") as raw_output:
                with (
                    gzip.GzipFile(
                        filename="",
                        mode="wb",
                        fileobj=raw_output,
                        mtime=source_date_epoch,
                    ) as compressed,
                    tarfile.open(
                        fileobj=compressed,
                        mode="w",
                        format=tarfile.PAX_FORMAT,
                    ) as target,
                ):
                    for member in members:
                        normalized = copy.copy(member)
                        normalized.mtime = source_date_epoch
                        normalized.pax_headers = {
                            key: value
                            for key, value in member.pax_headers.items()
                            if key != "mtime"
                        }
                        payload = source.extractfile(member) if member.isfile() else None
                        target.addfile(normalized, payload)
                raw_output.flush()
                os.fsync(raw_output.fileno())
        with tarfile.open(temporary, "r:gz") as emitted:
            emitted_members = _validated_sdist_members(emitted, expected_root)
            if any(member.mtime != source_date_epoch for member in emitted_members):
                raise ValueError("normalized sdist did not preserve SOURCE_DATE_EPOCH")
            if any(member.pax_headers for member in emitted_members):
                raise ValueError("normalized sdist synthesized unsupported PAX metadata")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _safe_run_id(value: str) -> str:
    """Require one bounded component that is portable across Windows and POSIX."""

    if (
        len(value) > 64
        or value.endswith(".")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None
        or not is_portable_component(value, max_length=64)
    ):
        raise ValueError("run ID must be one safe repository-local path component")
    return value


def _build(root: Path, output: Path, source_date_epoch: int) -> dict[str, str]:
    output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(output),
            str(root),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    expected_sdist = output / f"strongwiz-{__version__}.tar.gz"
    expected_wheel = output / f"strongwiz-{__version__}-py3-none-any.whl"
    produced = {path.name for path in output.iterdir() if path.is_file()}
    expected = {expected_sdist.name, expected_wheel.name}
    if produced != expected:
        raise RuntimeError("the build must produce exactly the expected sdist and wheel")
    _normalize_sdist(expected_sdist, source_date_epoch)
    return _artifact_hashes(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--receipt",
        type=Path,
        required=True,
        help="new canonical JSON receipt path beneath the repository",
    )
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--run-id", default=f"local-{secrets.token_hex(6)}")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve(strict=True).parents[1]
    run_id = _safe_run_id(args.run_id)
    if _git(root, "status", "--porcelain"):
        raise RuntimeError("reproducible release verification requires a clean worktree")
    source_commit = _git(root, "rev-parse", "HEAD")
    source_tree = _git(root, "rev-parse", "HEAD^{tree}")
    epoch = args.source_date_epoch
    if epoch is None:
        epoch = int(_git(root, "show", "-s", "--format=%ct", "HEAD"))
    if epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")

    build_root = _inside(root, Path("build") / "reproducibility" / run_id)
    if build_root.exists() or is_link_like(build_root):
        raise FileExistsError(f"reproducibility run already exists: {build_root}")
    first = _build(root, build_root / "first", epoch)
    second = _build(root, build_root / "second", epoch)
    if first != second:
        raise RuntimeError("post-normalization artifacts differ across the two local builds")
    if (
        _git(root, "status", "--porcelain")
        or _git(root, "rev-parse", "HEAD") != source_commit
        or _git(root, "rev-parse", "HEAD^{tree}") != source_tree
    ):
        raise RuntimeError("source checkout changed during reproducibility verification")

    receipt = _inside(root, args.receipt)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_sha256": first,
        "build_count": 2,
        "claim_scope": "local-two-build-post-normalization-identity",
        "package_version": __version__,
        "post_normalization_artifacts_identical": True,
        "publication_performed": False,
        "schema": "strongwiz.reproducible-build.v2",
        "sdist_normalization": {
            "accepted_member_types": ["directory", "regular-file"],
            "gzip_header_filename": "removed",
            "gzip_header_mtime": "SOURCE_DATE_EPOCH",
            "gzip_stream": "recompressed-with-python-gzip",
            "other_member_metadata": "preserved",
            "ownership_metadata": "preserved",
            "pax_member_mtime": "removed",
            "tar_member_mtime": "SOURCE_DATE_EPOCH",
        },
        "source_commit": source_commit,
        "source_date_epoch": epoch,
        "source_tree": source_tree,
        "tag_created": False,
    }
    with receipt.open("xb") as stream:
        stream.write(canonical_bytes(payload))
    print(receipt.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
