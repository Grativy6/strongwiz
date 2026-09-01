"""Build Strongwiz twice and emit a canonical reproducibility receipt.

The script refuses a dirty tree and keeps every generated path beneath the
repository.  It prepares release evidence; it does not create a tag, upload an
artifact, publish a release, or change legal terms.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

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
        raise RuntimeError("release artifacts differ across identical clean-tree builds")
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
        "package_version": __version__,
        "publication_performed": False,
        "reproducible": True,
        "schema": "strongwiz.reproducible-build.v1",
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
