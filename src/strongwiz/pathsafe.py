"""Small cross-platform path predicates for repository and evidence boundaries."""

from __future__ import annotations

import stat
from pathlib import Path, PureWindowsPath


def is_link_like(path: Path) -> bool:
    """Return true for symlinks, junctions, and other Windows reparse points."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(reparse_flag and attributes & reparse_flag)
    except OSError:
        return False


def is_portable_component(value: str, *, max_length: int = 255) -> bool:
    """Check one non-reserved component accepted on both Windows and POSIX."""

    return (
        bool(value)
        and value not in {".", ".."}
        and len(value) <= max_length
        and not value.endswith((" ", "."))
        and all(character not in value for character in ("\x00", "/", "\\", ":"))
        and not PureWindowsPath(value).is_reserved()
    )
