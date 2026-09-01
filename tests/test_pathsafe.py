from __future__ import annotations

import pytest

from strongwiz.pathsafe import is_portable_component


@pytest.mark.parametrize(
    "value",
    (
        "a<b",
        "a>b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
        "control\x01name",
        "control\x1fname",
    ),
)
def test_portable_component_rejects_windows_forbidden_characters(value: str) -> None:
    assert not is_portable_component(value)


@pytest.mark.parametrize("value", ("strongwiz-0.2.0", "source_file.py", "evidence capsule"))
def test_portable_component_accepts_ordinary_cross_platform_names(value: str) -> None:
    assert is_portable_component(value)
