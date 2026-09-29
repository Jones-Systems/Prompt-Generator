"""Source-preserving, inert-data migration planning and application.

Migration only reads source bytes and copies them after a dry-run plan has
identified all collisions.  JSON is parsed as data for identity/provenance
reporting; no imported text, links, templates, or code is evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable, Mapping

from .contracts import canonical_json_bytes, parse_json_bytes


class MigrationError(ValueError):
    """Base class for migration failures."""


class MigrationCollisionError(MigrationError):
    """Raised when an apply would overwrite different bytes."""


class UnsafeMigrationPathError(MigrationError):
    """Raised for symlink, traversal, or hard-link targets."""


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class MigrationProvenance:
    source: str
    source_revision: str
    imported: bool = True
    license: str | None = None
    ownership: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise MigrationError("provenance source is required")
        if not isinstance(self.source_revision, str) or not self.source_revision.strip():
            raise MigrationError("provenance source_revision is required")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "source_revision": self.source_revision,
            "imported": self.imported,
        }
        for key in ("license", "ownership", "notes"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class MigrationItem:
    source: str
    target: str
    size: int
    source_digest: str
    identity: str | None = None
    revision: int | None = None
    action: str = "copy"
    collision: str | None = None
    error: str | None = None
    provenance: MigrationProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "target": self.target,
            "size": self.size,
            "source_digest": self.source_digest,
            "action": self.action,
        }
        for key in ("identity", "revision", "collision", "error"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.provenance is not None:
            result["provenance"] = self.provenance.to_dict()
        return result


@dataclass(frozen=True)
class MigrationReport:
    source_root: str
    destination_root: str
    dry_run: bool
    items: tuple[MigrationItem, ...] = ()
    collisions: tuple[MigrationItem, ...] = ()
    provenance: MigrationProvenance | None = None
    source_preserved: bool = True
    verified: bool = False

    @property
    def ok(self) -> bool:
        return not self.collisions and all(item.error is None for item in self.items)

    @property
    def copied(self) -> tuple[MigrationItem, ...]:
        return tuple(item for item in self.items if item.action == "copied")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "migration-report",
            "source_root": self.source_root,
            "destination_root": self.destination_root,
            "dry_run": self.dry_run,
            "source_preserved": self.source_preserved,
            "verified": self.verified,
            "ok": self.ok,
            "items": [item.to_dict() for item in self.items],
            "collisions": [item.to_dict() for item in self.collisions],
        }
        if self.provenance is not None:
            result["provenance"] = self.provenance.to_dict()
        return result

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


def _coerce_provenance(value: MigrationProvenance | Mapping[str, Any] | None) -> MigrationProvenance | None:
    if value is None or isinstance(value, MigrationProvenance):
        return value
    return MigrationProvenance(
        value["source"], value["source_revision"], value.get("imported", True),
        value.get("license"), value.get("ownership"), value.get("notes"),
    )


def _absolute_path(path: Path) -> Path:
    """Return an absolute lexical path without following symlinks."""

    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path) -> Path:
    """Reject every existing symlink component before any path is used.

    ``Path.resolve`` is deliberately not used here.  Resolving first would
    turn a destination containing a link into an apparently valid path below
    the link target, which is exactly the escape this module must reject.
    Missing components are allowed for destinations and are checked again
    immediately before publication.
    """

    absolute = _absolute_path(path)
    cursor = Path(absolute.parts[0])
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MigrationError(f"cannot inspect path: {cursor}") from exc
        if stat.S_ISLNK(mode):
            raise UnsafeMigrationPathError(f"symlink path component is not allowed: {cursor}")
    return absolute


def _root(path: Path, *, exists: bool) -> Path:
    absolute = _reject_symlink_components(path)
    if exists and not absolute.exists():
        raise MigrationError(f"path does not exist: {path}")
    return absolute


def _source_files(source: Path) -> tuple[tuple[Path, str], ...]:
    source = _root(source, exists=True)
    if source.is_file():
        return ((source, source.name),)
    if not source.is_dir():
        raise MigrationError(f"source is not a regular file or directory: {source}")
    result: list[tuple[Path, str]] = []
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        dirs.sort()
        for name in dirs:
            if (current_path / name).is_symlink():
                raise UnsafeMigrationPathError(f"source symlink is not allowed: {current_path / name}")
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink():
                raise UnsafeMigrationPathError(f"source symlink is not allowed: {path}")
            if not path.is_file():
                raise MigrationError(f"source is not a regular file: {path}")
            result.append((path, path.relative_to(source).as_posix()))
    return tuple(result)


def _destination_target(source: Path, destination: Path, relative: str) -> tuple[Path, Path]:
    source = _root(source, exists=True)
    destination = _reject_symlink_components(destination)
    if source.is_file() and (destination.exists() and destination.is_dir()):
        root = _root(destination, exists=True)
        return root, root / relative
    if source.is_file():
        if destination.exists() and not destination.is_file():
            raise MigrationError(f"destination is not a regular file or directory: {destination}")
        target = destination
        root = _root(target.parent, exists=True)
        return root, target
    if destination.exists() and not destination.is_dir():
        raise MigrationError(f"directory source requires directory destination: {destination}")
    root = _root(destination, exists=False)
    return root, root / relative


def _safe_target(root: Path, target: Path) -> None:
    root = _reject_symlink_components(root)
    target = _reject_symlink_components(target)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise MigrationError(f"destination root is not a regular directory: {root}")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UnsafeMigrationPathError(f"target escapes destination: {target}") from exc
    current = root
    relative = target.relative_to(root)
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise UnsafeMigrationPathError(f"destination symlink component: {current}")
    if target.is_symlink():
        raise UnsafeMigrationPathError(f"destination symlink target: {target}")
    if target.exists():
        if not target.is_file():
            raise MigrationError(f"destination target is not a regular file: {target}")
        if stat.S_ISREG(target.lstat().st_mode) and target.lstat().st_nlink > 1:
            raise UnsafeMigrationPathError(f"hard-linked destination target: {target}")


def _identity(data: bytes) -> tuple[str | None, int | None, str | None]:
    if not data.lstrip().startswith(b"{"):
        return None, None, None
    try:
        value = parse_json_bytes(data, canonical=False)
    except Exception as exc:
        return None, None, f"invalid inert JSON: {exc}"
    if not isinstance(value, Mapping):
        return None, None, None
    prompt_id = value.get("prompt_id")
    revision = value.get("revision")
    if prompt_id is not None and not isinstance(prompt_id, str):
        return None, None, "prompt_id is not text"
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
        return prompt_id, None, "revision is not an integer"
    return prompt_id, revision, None


def dry_run_import(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    provenance: MigrationProvenance | Mapping[str, Any] | None = None,
) -> MigrationReport:
    """Build a deterministic migration report without writing any bytes."""

    source_path = Path(source)
    destination_path = Path(destination)
    source_root = _root(source_path, exists=True)
    files = _source_files(source_path)
    destination_root, explicit_target = _destination_target(
        source_root, destination_path, files[0][1] if files else "",
    )
    prov = _coerce_provenance(provenance)
    items: list[MigrationItem] = []
    collisions: list[MigrationItem] = []
    seen_identities: dict[tuple[str, int], str] = {}
    for path, relative in files:
        data = path.read_bytes()
        source_digest = sha256_bytes(data)
        identity, revision, error = _identity(data)
        target = explicit_target if source_root.is_file() else destination_root / relative
        _safe_target(destination_root, target)
        collision = None
        action = "copy"
        if target.exists():
            existing = target.read_bytes()
            if existing == data:
                action = "unchanged"
            else:
                collision = "target bytes differ"
                action = "collision"
        if identity is not None and revision is not None:
            key = (identity, revision)
            prior = seen_identities.get(key)
            if prior is not None:
                collision = "duplicate prompt identity and revision"
                action = "collision"
            seen_identities[key] = source_digest
        item = MigrationItem(
            path.as_posix(), target.as_posix(), len(data), source_digest,
            identity, revision, action, collision, error, prov,
        )
        items.append(item)
        if collision is not None or error is not None:
            collisions.append(item)
    return MigrationReport(
        source_root.as_posix(), destination_root.as_posix(), True,
        tuple(items), tuple(collisions), prov, True, False,
    )


def _planned_source_snapshot(
    source: Path,
    plan: MigrationReport,
    destination: Path,
) -> tuple[Path, dict[str, bytes]]:
    """Validate a plan binding and return the exact bytes it describes.

    Applying a plan must not silently turn into a fresh dry-run.  The plan's
    source set and digests are therefore checked directly against the current
    source tree, while its destination paths are checked against the explicit
    source/destination interpretation used during planning.
    """

    if not isinstance(plan, MigrationReport):
        raise MigrationError("migration plan must be a MigrationReport")
    if not plan.dry_run:
        raise MigrationError("migration plan must be a dry-run report")
    if not plan.source_preserved:
        raise MigrationError("migration plan does not preserve its source")
    if not plan.ok or plan.collisions:
        raise MigrationCollisionError("migration plan contains collisions or invalid data")

    files = _source_files(source)
    expected_source_root = _root(source, exists=True)
    destination_root, explicit_target = _destination_target(
        expected_source_root, destination, files[0][1] if files else "",
    )
    if plan.source_root != expected_source_root.as_posix():
        raise MigrationError("migration plan source root changed")
    if plan.destination_root != destination_root.as_posix():
        raise MigrationError("migration plan destination root changed")

    expected_sources = tuple(path.as_posix() for path, _ in files)
    planned_sources = tuple(item.source for item in plan.items)
    if planned_sources != expected_sources:
        raise MigrationError("migration plan source set changed")

    snapshot: dict[str, bytes] = {}
    for (path, relative), item in zip(files, plan.items):
        expected_target = (
            explicit_target if expected_source_root.is_file() else destination_root / relative
        ).as_posix()
        if item.target != expected_target:
            raise MigrationError(f"migration plan target changed: {item.target}")
        if item.action not in {"copy", "unchanged"}:
            raise MigrationError(f"migration plan action is not applicable: {item.action}")
        if item.collision is not None or item.error is not None:
            raise MigrationCollisionError(f"migration plan item is not applicable: {item.source}")
        data = path.read_bytes()
        if len(data) != item.size or sha256_bytes(data) != item.source_digest:
            raise MigrationError(f"migration plan source digest changed: {path}")
        snapshot[item.source] = data
    return destination_root, snapshot


def _fsync_directory(directory: Path) -> None:
    """Flush a directory after publishing a new entry when supported."""

    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_no_clobber(temporary: Path, target: Path) -> bool:
    """Atomically publish *temporary* only when *target* is absent.

    ``os.replace`` is intentionally not used: a target can appear after an
    existence check and would otherwise be overwritten.  A same-filesystem
    hard-link create is atomic and fails with ``FileExistsError`` on that
    race.  The temporary name is removed after a successful publication.
    """

    try:
        os.link(temporary, target)
    except FileExistsError:
        return False
    os.unlink(temporary)
    _fsync_directory(target.parent)
    return True


def copy_then_verify(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    provenance: MigrationProvenance | Mapping[str, Any] | None = None,
    dry_run: bool = False,
    plan: MigrationReport | None = None,
) -> MigrationReport:
    """Apply a validated plan, then verify each destination digest.

    A differing existing target is never overwritten.  An equal existing
    target is idempotently accepted.  When ``plan`` is supplied it is applied
    as-is: no new dry-run is computed.  The source tree is never modified.
    """

    source_path = Path(source)
    destination_path = Path(destination)
    if plan is None:
        report = dry_run_import(source_path, destination_path, provenance=provenance)
        destination_root, source_snapshot = _planned_source_snapshot(
            source_path, report, destination_path,
        )
    else:
        supplied_provenance = _coerce_provenance(provenance)
        if supplied_provenance is not None and supplied_provenance != plan.provenance:
            raise MigrationError("migration plan provenance changed")
        destination_root, source_snapshot = _planned_source_snapshot(
            source_path, plan, destination_path,
        )
        report = plan
    if dry_run:
        return report
    if not report.ok:
        raise MigrationCollisionError("migration dry-run contains collisions or invalid data")
    if source_path.is_dir():
        _reject_symlink_components(destination_root)
        destination_root.mkdir(parents=True, exist_ok=True)
    copied: list[MigrationItem] = []
    for item in report.items:
        target = Path(item.target)
        _safe_target(destination_root, target)
        _reject_symlink_components(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        _safe_target(destination_root, target)
        source_path_for_item = Path(item.source)
        source_bytes = (
            source_snapshot[item.source]
            if source_snapshot is not None
            else source_path_for_item.read_bytes()
        )
        if len(source_bytes) != item.size or sha256_bytes(source_bytes) != item.source_digest:
            raise MigrationError(f"migration plan source digest changed: {source_path_for_item}")
        if target.exists():
            _safe_target(destination_root, target)
            if target.read_bytes() != source_bytes:
                raise MigrationCollisionError(f"target changed after dry-run: {target}")
            copied.append(MigrationItem(**{**item.__dict__, "action": "unchanged"}))
            continue
        fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=target.parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            if sha256_bytes(temp_path.read_bytes()) != item.source_digest:
                raise MigrationError(f"temporary copy failed verification: {target}")
            if not _publish_no_clobber(temp_path, target):
                _safe_target(destination_root, target)
                if target.read_bytes() != source_bytes:
                    raise MigrationCollisionError(f"target changed after dry-run: {target}")
                copied.append(MigrationItem(**{**item.__dict__, "action": "unchanged"}))
                continue
            if sha256_bytes(target.read_bytes()) != item.source_digest:
                raise MigrationError(f"destination verification failed: {target}")
            copied.append(MigrationItem(**{**item.__dict__, "action": "copied"}))
        finally:
            if temp_path.exists():
                temp_path.unlink()
    return MigrationReport(
        report.source_root, report.destination_root, False, tuple(copied), (),
        report.provenance, True, True,
    )


def migrate(*args: Any, **kwargs: Any) -> MigrationReport:
    """Explicit name for apply mode; callers should dry-run first."""

    return copy_then_verify(*args, **kwargs)


plan_migration = dry_run_import
import_dry_run = dry_run_import
