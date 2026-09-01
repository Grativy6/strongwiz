from __future__ import annotations

from pathlib import Path

import pytest

import scripts.verify_reproducible_build as release_script
from scripts.verify_reproducible_build import _inside, main


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
