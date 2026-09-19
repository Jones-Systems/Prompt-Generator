"""Fail-closed path helpers used by generation and snapshot publication.

The public generator writes only below a caller supplied root.  These helpers
keep path policy in one place: no traversal, ambiguous Unicode normalization,
symlink escapes, or writable hard-linked output files.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import unicodedata
from typing import Iterable


class PathSafetyError(ValueError):
    """Raised when a path or output target is not safe to use."""


UnsafePathError = PathSafetyError


_COMPONENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _normalized_text(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PathSafetyError(f"{field} must be non-empty text")
    if "\x00" in value:
        raise PathSafetyError(f"{field} contains NUL")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or unicodedata.normalize("NFKC", value) != value:
        raise PathSafetyError(f"{field} has ambiguous Unicode normalization")
    return value


def safe_component(value: str, *, field: str = "path component") -> str:
    """Validate one portable, non-reserved path component."""

    value = _normalized_text(value, field=field)
    if value in {".", ".."} or _COMPONENT_RE.fullmatch(value) is None:
        raise PathSafetyError(f"unsafe {field}: {value!r}")
    if value.upper().split(".", 1)[0] in _WINDOWS_RESERVED:
        raise PathSafetyError(f"reserved {field}: {value!r}")
    if value.endswith(" ") or value.endswith("."):
        raise PathSafetyError(f"ambiguous {field}: {value!r}")
    return value


def safe_id(value: str, *, field: str = "identifier") -> str:
    """Alias with the terminology used by catalog and generation callers."""

    return safe_component(value, field=field)


def validate_relative_parts(parts: Iterable[str], *, field: str = "relative path") -> tuple[str, ...]:
    result = tuple(safe_component(part, field=field) for part in parts)
    if not result:
        raise PathSafetyError(f"{field} must not be empty")
    return result


def safe_relative_path(value: str | os.PathLike[str], *, field: str = "relative path") -> Path:
    """Return a normalized relative path, rejecting all ambiguous forms."""

    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise PathSafetyError(f"{field} must be text")
    _normalized_text(raw, field=field)
    if raw.startswith(("/", "\\")) or Path(raw).is_absolute():
        raise PathSafetyError(f"{field} must be relative")
    if "\\" in raw:
        raise PathSafetyError(f"{field} contains a platform separator")
    path = Path(raw)
    if not path.parts:
        raise PathSafetyError(f"{field} must not be empty")
    for part in path.parts:
        safe_component(part, field=field)
    normalized = Path(*path.parts)
    if str(normalized) != raw:
        raise PathSafetyError(f"{field} is not in canonical form")
    return normalized


def assert_root_directory(root: str | os.PathLike[str]) -> Path:
    """Resolve an existing root and reject a symlink root."""

    path = Path(root)
    try:
        stat = path.lstat()
    except FileNotFoundError as exc:
        raise PathSafetyError(f"root does not exist: {path}") from exc
    if not path.is_dir() or path.is_symlink():
        raise PathSafetyError("root must be a real directory")
    return path.resolve(strict=True)


def rooted_path(
    root: str | os.PathLike[str],
    relative: str | os.PathLike[str],
    *,
    allow_missing: bool = True,
    reject_symlinks: bool = True,
) -> Path:
    """Resolve *relative* below *root* without following an escape."""

    root_path = assert_root_directory(root)
    relative_path = safe_relative_path(relative)
    candidate = root_path.joinpath(relative_path)

    # Check each existing component before resolve(); otherwise a symlink can
    # make an apparently relative path resolve outside the generation root.
    cursor = root_path
    for part in relative_path.parts:
        cursor = cursor / part
        if not cursor.exists() and not cursor.is_symlink():
            if not allow_missing:
                raise PathSafetyError(f"path does not exist: {relative_path}")
            break
        if reject_symlinks and cursor.is_symlink():
            raise PathSafetyError(f"symlink is not permitted in generated path: {relative_path}")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise PathSafetyError(f"path escapes root: {relative_path}") from exc
    return candidate


def assert_regular_output(path: str | os.PathLike[str], *, allow_existing: bool = False) -> Path:
    """Check a file target before writing it.

    Existing output is allowed only when explicitly requested, and still must
    be a regular, non-symlink, singly linked file.  The generator never
    follows an existing link while replacing an output.
    """

    target = Path(path)
    if target.exists() or target.is_symlink():
        if not allow_existing:
            raise PathSafetyError(f"output already exists: {target}")
        stat = target.lstat()
        if target.is_symlink() or not target.is_file():
            raise PathSafetyError(f"output is not a regular file: {target}")
        if stat.st_nlink != 1:
            raise PathSafetyError(f"writable output is hard-linked: {target}")
    return target


def check_tree_containment(root: str | os.PathLike[str], paths: Iterable[str | os.PathLike[str]]) -> tuple[Path, ...]:
    """Validate a collection of relative paths and return rooted targets."""

    root_path = assert_root_directory(root)
    result = tuple(rooted_path(root_path, item) for item in paths)
    if len(set(result)) != len(result):
        raise PathSafetyError("duplicate generated output path")
    return result


@dataclass(frozen=True)
class PathPolicy:
    """Small explicit policy object for callers that need reusable checks."""

    root: Path
    reject_symlinks: bool = True

    def resolve(self, relative: str | os.PathLike[str], *, allow_missing: bool = True) -> Path:
        return rooted_path(
            self.root,
            relative,
            allow_missing=allow_missing,
            reject_symlinks=self.reject_symlinks,
        )
