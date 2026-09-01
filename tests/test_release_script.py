from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

import scripts.verify_reproducible_build as release_script
from scripts.verify_reproducible_build import _build, _inside, _normalize_sdist, main

SemanticEntry = tuple[str, bytes, int, int, int, int, str, str, str, str]


def test_release_receipt_path_cannot_escape_repository(tmp_path: Path) -> None:
    root = (tmp_path / "repository").resolve()
    root.mkdir()
    with pytest.raises(ValueError, match="inside the repository"):
        _inside(root, root.parent / "outside.json")


def test_release_paths_refuse_link_like_build_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "repository").resolve()
    (root / "build").mkdir(parents=True)
    monkeypatch.setattr(
        release_script,
        "is_link_like",
        lambda path: path.name == "build",
    )
    with pytest.raises(ValueError, match="link-like"):
        _inside(root, Path("build/reproducibility/run"))


def _write_nondeterministic_sdist(path: Path, *, timestamp: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, mtime=int(timestamp)) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        directory = tarfile.TarInfo("strongwiz-0.2.0")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        directory.mtime = timestamp
        directory.uid = 1001
        directory.gid = 1002
        directory.uname = "builder"
        directory.gname = "builders"
        archive.addfile(directory)
        payload = b"deterministic source bytes\n"
        member = tarfile.TarInfo("strongwiz-0.2.0/source.txt")
        member.size = len(payload)
        member.mode = 0o644
        member.mtime = timestamp
        member.uid = 1003
        member.gid = 1004
        member.uname = "packager"
        member.gname = "packagers"
        archive.addfile(member, io.BytesIO(payload))


def _semantic_manifest(path: Path) -> tuple[SemanticEntry, ...]:
    entries: list[SemanticEntry] = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            payload_hash = ""
            if member.isfile():
                payload = archive.extractfile(member)
                assert payload is not None
                payload_hash = hashlib.sha256(payload.read()).hexdigest()
            entries.append(
                (
                    member.name,
                    member.type,
                    member.mode,
                    member.size,
                    member.uid,
                    member.gid,
                    member.uname,
                    member.gname,
                    member.linkname,
                    payload_hash,
                )
            )
    return tuple(entries)


def test_sdist_normalization_removes_tar_and_gzip_timestamp_variance(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first" / "strongwiz-0.2.0.tar.gz"
    second = tmp_path / "second" / "strongwiz-0.2.0.tar.gz"
    _write_nondeterministic_sdist(first, timestamp=1_700_000_001.125)
    _write_nondeterministic_sdist(second, timestamp=1_700_000_099.875)

    before = _semantic_manifest(first)
    assert before == _semantic_manifest(second)
    with (
        tarfile.open(first, "r:gz") as first_archive,
        tarfile.open(second, "r:gz") as second_archive,
    ):
        first_pax_times = [member.pax_headers.get("mtime") for member in first_archive]
        second_pax_times = [member.pax_headers.get("mtime") for member in second_archive]
        assert all(value is not None for value in first_pax_times)
        assert first_pax_times != second_pax_times

    epoch = 1_600_000_000
    _normalize_sdist(first, epoch)
    _normalize_sdist(second, epoch)

    assert (
        hashlib.sha256(first.read_bytes()).digest()
        == hashlib.sha256(second.read_bytes()).digest()
    )
    assert int.from_bytes(first.read_bytes()[4:8], "little") == epoch
    assert int.from_bytes(second.read_bytes()[4:8], "little") == epoch
    assert first.read_bytes()[3] & 0x08 == 0
    assert second.read_bytes()[3] & 0x08 == 0
    assert _semantic_manifest(first) == before
    assert _semantic_manifest(second) == before
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        assert {member.mtime for member in members} == {epoch}
        assert all("mtime" not in member.pax_headers for member in members)


def _write_sdist_with_custom_member(
    path: Path,
    member_name: str,
    *,
    member_type: bytes = tarfile.REGTYPE,
    linkname: str = "",
    uid: int = 0,
    gid: int = 0,
    uname: str = "",
    gname: str = "",
    mode: int = 0o644,
    pax_headers: dict[str, str] | None = None,
    additional_member_names: tuple[str, ...] = (),
    archive_format: int = tarfile.PAX_FORMAT,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz", format=archive_format) as archive:
        root = tarfile.TarInfo("strongwiz-0.2.0")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for index, name in enumerate((member_name, *additional_member_names)):
            member = tarfile.TarInfo(name)
            if index == 0:
                member.type = member_type
                member.linkname = linkname
                member.uid = uid
                member.gid = gid
                member.uname = uname
                member.gname = gname
                member.mode = mode
                member.pax_headers = pax_headers or {}
            payload = b"payload\n" if member.isfile() else b""
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)


@pytest.mark.parametrize(
    "member_name",
    [
        "/strongwiz-0.2.0/absolute.txt",
        "../strongwiz-0.2.0/escape.txt",
        "strongwiz-0.2.0/../escape.txt",
        "different-root/source.txt",
        "strongwiz-0.2.0\\source.txt",
        "strongwiz-0.2.0/CON",
        "strongwiz-0.2.0/file:stream",
        "strongwiz-0.2.0/trailing.",
        "strongwiz-0.2.0/a?.txt",
        "strongwiz-0.2.0/control\x1fname",
    ],
)
def test_sdist_normalization_rejects_unsafe_or_unexpected_paths(
    tmp_path: Path,
    member_name: str,
) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(path, member_name)

    with pytest.raises(ValueError, match="unsafe or unexpected path"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_rejects_casefold_path_collisions(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        additional_member_names=("strongwiz-0.2.0/SOURCE.txt",),
    )

    with pytest.raises(ValueError, match="case-folding path collision"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_rejects_file_ancestor_topology(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/a",
        additional_member_names=("strongwiz-0.2.0/A/child.txt",),
    )

    with pytest.raises(ValueError, match="regular file is an ancestor"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_rejects_special_members(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/link",
        member_type=tarfile.SYMTYPE,
        linkname="../../outside",
    )

    with pytest.raises(ValueError, match="member type is not supported"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_rejects_link_metadata_on_regular_files(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        linkname="../../outside",
    )

    with pytest.raises(ValueError, match="unsupported link metadata"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_rejects_non_temporal_pax_metadata(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        pax_headers={"comment": "must not be silently removed"},
    )

    with pytest.raises(ValueError, match="unsupported PAX metadata"):
        _normalize_sdist(path, 1_600_000_000)


def test_sdist_normalization_preserves_backend_ownership_metadata(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        uid=123,
        gid=456,
        uname="runner",
        gname="docker",
    )
    before = _semantic_manifest(path)

    _normalize_sdist(path, 1_600_000_000)

    assert _semantic_manifest(path) == before


def test_sdist_normalization_rejects_synthesized_ownership_pax_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        uid=1_000_000_000_000,
        gid=1_000_000_000_001,
        archive_format=tarfile.GNU_FORMAT,
    )
    original = path.read_bytes()
    with tarfile.open(path, "r:gz") as archive:
        assert all(not member.pax_headers for member in archive.getmembers())

    with pytest.raises(ValueError, match="unsupported PAX metadata"):
        _normalize_sdist(path, 1_600_000_000)

    assert path.read_bytes() == original
    assert not path.with_name(f".{path.name}.normalized").exists()


def test_sdist_normalization_rejects_special_mode_bits(tmp_path: Path) -> None:
    path = tmp_path / "strongwiz-0.2.0.tar.gz"
    _write_sdist_with_custom_member(
        path,
        "strongwiz-0.2.0/source.txt",
        mode=0o4644,
    )

    with pytest.raises(ValueError, match="unsupported mode bits"):
        _normalize_sdist(path, 1_600_000_000)


def test_build_requires_the_expected_wheel_and_sdist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    output = tmp_path / "artifacts"

    def fake_build(*_args: object, **_kwargs: object) -> None:
        _write_nondeterministic_sdist(
            output / "strongwiz-0.2.0.tar.gz",
            timestamp=1_700_000_001.125,
        )

    monkeypatch.setattr(release_script.subprocess, "run", fake_build)
    with pytest.raises(RuntimeError, match="exactly the expected sdist and wheel"):
        _build(root, output, 1_600_000_000)


def test_source_distribution_manifest_includes_capsule_jsonl() -> None:
    manifest = (Path(__file__).resolve().parents[1] / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include docs *.json *.jsonl *.md" in manifest


@pytest.mark.parametrize(
    "run_id",
    [
        "../escape",
        "nested/escape",
        "..",
        " spaced",
        "CON",
        "nul.json",
        "COM1",
        "trailing.",
        "x" * 65,
    ],
)
def test_reproducibility_run_id_is_one_safe_component(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="safe repository-local"):
        main(
            [
                "--receipt",
                str(tmp_path / "unused.json"),
                "--run-id",
                run_id,
            ]
        )
