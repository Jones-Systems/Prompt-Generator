"""Byte-level integrity primitives for generated prompt artifacts.

Digests in this module are labels for exact byte equality.  They are not
signatures, provenance, or an authorization decision.  Payload and whole-file
integrity are intentionally represented by separate records so a change to
Markdown framing cannot be confused with a change to the canonical payload.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .paths import PathSafetyError, assert_regular_output


class IntegrityError(ValueError):
    """Raised when integrity metadata or bytes do not match."""


DigestMismatch = IntegrityError


def sha256_digest(data: bytes | bytearray | memoryview) -> str:
    """Return a stable, explicitly typed SHA-256 digest label."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise IntegrityError("digest input must be bytes")
    return "sha256:" + hashlib.sha256(bytes(data)).hexdigest()


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) != len("sha256:") + 64:
        raise IntegrityError(f"invalid SHA-256 digest: {value!r}")
    if not value.startswith("sha256:") or any(char not in "0123456789abcdef" for char in value[7:]):
        raise IntegrityError(f"invalid SHA-256 digest: {value!r}")
    return value


@dataclass(frozen=True)
class IntegrityRecord:
    """Exact byte count and digest for one byte sequence."""

    byte_count: int
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.byte_count, int) or isinstance(self.byte_count, bool) or self.byte_count < 0:
            raise IntegrityError("byte_count must be a non-negative integer")
        validate_digest(self.digest)

    @property
    def bytes(self) -> int:
        return self.byte_count

    @property
    def sha256(self) -> str:
        return self.digest

    def to_dict(self) -> dict[str, Any]:
        return {"bytes": self.byte_count, "sha256": self.digest}

    @classmethod
    def from_bytes(cls, data: bytes) -> "IntegrityRecord":
        return cls(len(data), sha256_digest(data))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntegrityRecord":
        if set(value) != {"bytes", "sha256"}:
            raise IntegrityError("integrity record has unknown or missing fields")
        return cls(value["bytes"], value["sha256"])

    def verify(self, data: bytes) -> None:
        actual = IntegrityRecord.from_bytes(data)
        if actual != self:
            raise IntegrityError(
                f"integrity mismatch: expected {self.digest}/{self.byte_count}, "
                f"got {actual.digest}/{actual.byte_count}"
            )


FileIntegrity = IntegrityRecord


def integrity_for_bytes(data: bytes) -> IntegrityRecord:
    return IntegrityRecord.from_bytes(data)


def payload_and_file_integrity(payload: bytes, whole_file: bytes) -> dict[str, Any]:
    """Return deliberately separate payload and whole-file fields."""

    payload_record = IntegrityRecord.from_bytes(payload)
    whole_record = IntegrityRecord.from_bytes(whole_file)
    return {
        "payload_bytes": payload_record.byte_count,
        "payload_sha256": payload_record.digest,
        "whole_file_bytes": whole_record.byte_count,
        "whole_file_sha256": whole_record.digest,
    }


def verify_integrity(data: bytes, expected: IntegrityRecord | Mapping[str, Any]) -> IntegrityRecord:
    record = expected if isinstance(expected, IntegrityRecord) else IntegrityRecord.from_dict(expected)
    record.verify(data)
    return record


def read_file_integrity(path: str | os.PathLike[str], *, reject_links: bool = True) -> IntegrityRecord:
    target = Path(path)
    if not target.exists() or target.is_symlink() or not target.is_file():
        raise IntegrityError(f"not a regular file: {target}")
    stat = target.lstat()
    if reject_links and stat.st_nlink != 1:
        raise IntegrityError(f"file has unexpected hard links: {target}")
    return IntegrityRecord.from_bytes(target.read_bytes())


def verify_file_integrity(
    path: str | os.PathLike[str],
    expected: IntegrityRecord | Mapping[str, Any],
    *,
    reject_links: bool = True,
) -> IntegrityRecord:
    actual = read_file_integrity(path, reject_links=reject_links)
    expected_record = expected if isinstance(expected, IntegrityRecord) else IntegrityRecord.from_dict(expected)
    if actual != expected_record:
        raise IntegrityError(f"integrity mismatch for {path}")
    return actual


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    replace: bool = False,
    mode: int = 0o444,
) -> IntegrityRecord:
    """Write bytes using a same-directory temporary and atomic replacement.

    The target is checked before any write.  Existing symlink and hard-linked
    files are rejected even when replacement was requested.
    """

    if not isinstance(data, bytes):
        raise IntegrityError("atomic_write_bytes requires bytes")
    target = Path(path)
    parent = target.parent
    if not parent.exists() or not parent.is_dir() or parent.is_symlink():
        raise PathSafetyError(f"output parent is not a real directory: {parent}")
    assert_regular_output(target, allow_existing=replace)
    fd: int | None = None
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=".prompt-write-", dir=str(parent))
        temporary = Path(temporary_name)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.is_symlink() or temporary.stat().st_nlink != 1:
            raise IntegrityError("temporary output target changed unexpectedly")
        os.replace(temporary, target)
        temporary = None
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise
    return IntegrityRecord.from_bytes(data)


# Short aliases are useful to library users without weakening the explicit
# names used by manifest generation.
digest_bytes = sha256_digest
compute_integrity = integrity_for_bytes
