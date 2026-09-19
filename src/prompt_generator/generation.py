"""Deterministic prompt rendering and atomic generation snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import re
import secrets
from typing import Any, Iterable, Mapping

from .catalog import PromptCatalog, PromptRecord
from .contracts import (
    canonical_json_bytes,
    canonical_json_loads,
    contract_bytes,
    parse_json_bytes,
)
from .integrity import (
    IntegrityError,
    IntegrityRecord,
    atomic_write_bytes,
    payload_and_file_integrity,
    sha256_digest,
)
from .paths import (
    PathSafetyError,
    assert_regular_output,
    assert_root_directory,
    rooted_path,
    safe_component,
)


GENERATOR_VERSION = "prompt-generator/1"
GENERATION_SCHEMA_VERSION = 1
CURRENT_FILENAME = "CURRENT"
GENERATIONS_DIRNAME = ".generations"
LOCK_FILENAME = ".generation.lock"

BEGIN_MARKER = "<!-- prompt-generator:begin -->"
END_MARKER = "<!-- prompt-generator:end -->"
PAYLOAD_BEGIN_MARKER = "<!-- prompt-generator:payload-begin -->"
PAYLOAD_END_MARKER = "<!-- prompt-generator:payload-end -->"
START_MARKER = BEGIN_MARKER
STOP_MARKER = END_MARKER

_MARKER_RE = re.compile(r"^<!-- prompt-generator:(?P<key>[a-z-]+)=(?P<value>[^>]*) -->\n$")
_FENCE_RE = re.compile(r"^(?P<fence>`{3,})json\n$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class GenerationError(ValueError):
    """Raised for malformed generated documents or snapshots."""


class RenderError(GenerationError):
    """Raised when a prompt cannot be rendered or parsed safely."""


class SnapshotError(GenerationError):
    """Raised when a generation snapshot is incomplete or inconsistent."""


class StaleOutputError(SnapshotError):
    """Raised when a valid snapshot no longer represents the supplied catalog."""


class GenerationBusy(GenerationError):
    """Raised when another publisher owns the generation lock."""


class RecoveryError(GenerationError):
    """Raised when a requested recovery operation is outside the dry-run API."""


def _as_prompt_record(prompt: PromptRecord | Mapping[str, Any]) -> PromptRecord:
    if isinstance(prompt, PromptRecord):
        return prompt
    if not isinstance(prompt, Mapping):
        raise RenderError("prompt must be a mapping or PromptRecord")
    return PromptRecord.from_dict(prompt)


def prompt_payload_bytes(prompt: PromptRecord | Mapping[str, Any]) -> bytes:
    """Return the validated canonical JSON payload for one prompt."""

    return contract_bytes(_as_prompt_record(prompt).to_dict(), "prompt")


def _max_backtick_run(data: bytes) -> int:
    text = data.decode("utf-8", "strict")
    runs = re.findall(r"`+", text)
    return max((len(run) for run in runs), default=0)


def adaptive_fence(payload: bytes) -> str:
    """Choose a Markdown fence longer than any backtick run in the payload."""

    return "`" * max(3, _max_backtick_run(payload) + 1)


def _display_title(value: str) -> str:
    # The payload remains authoritative.  This line is merely a stable human
    # heading and is kept single-line so it cannot create a second marker or
    # fence structure.
    return " ".join(value.replace("`", "\\`").splitlines())


def render_prompt(prompt: PromptRecord | Mapping[str, Any]) -> bytes:
    """Render one canonical, nondispatching Markdown prompt document.

    The payload is copied byte-for-byte inside an adaptive JSON fence.  All
    live markers remain outside that fence, so copying the fenced payload is
    unambiguous and cannot itself activate the document markers.
    """

    record = _as_prompt_record(prompt)
    payload = prompt_payload_bytes(record)
    fence = adaptive_fence(payload).encode("ascii")
    metadata = (
        f"<!-- prompt-generator:schema-version=1 -->\n"
        f"<!-- prompt-generator:prompt-id={record.prompt_id} -->\n"
        f"<!-- prompt-generator:revision={record.revision} -->\n"
        f"<!-- prompt-generator:payload-bytes={len(payload)} -->\n"
        f"<!-- prompt-generator:payload-digest={sha256_digest(payload)} -->\n"
    ).encode("utf-8")
    heading = f"# {_display_title(record.title)}\n\n".encode("utf-8")
    opening = fence + b"json\n"
    closing = fence + b"\n"
    result = (
        BEGIN_MARKER.encode("ascii") + b"\n"
        + metadata
        + heading
        + PAYLOAD_BEGIN_MARKER.encode("ascii") + b"\n"
        + opening
        + payload
        + closing
        + PAYLOAD_END_MARKER.encode("ascii") + b"\n"
        + END_MARKER.encode("ascii") + b"\n"
    )
    if result.count(BEGIN_MARKER.encode("ascii")) != 1 or result.count(END_MARKER.encode("ascii")) != 1:
        raise RenderError("generated markers are not paired exactly once")
    return result


def render_prompt_text(prompt: PromptRecord | Mapping[str, Any]) -> str:
    return render_prompt(prompt).decode("utf-8", "strict")


def _metadata(lines: list[bytes]) -> dict[str, str]:
    result: dict[str, str] = {}
    expected = {"schema-version", "prompt-id", "revision", "payload-bytes", "payload-digest"}
    for line in lines:
        if line in {b"\n"} or line.startswith(b"# "):
            continue
        try:
            text = line.decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise RenderError("metadata and heading lines must be ASCII") from exc
        match = _MARKER_RE.fullmatch(text)
        if match is None or match.group("key") not in expected:
            raise RenderError("malformed prompt metadata marker")
        key = match.group("key")
        if key in result:
            raise RenderError(f"duplicate prompt metadata marker: {key}")
        result[key] = match.group("value")
    if set(result) != expected:
        raise RenderError("prompt metadata markers are incomplete")
    return result


def parse_prompt_markdown(document: bytes | bytearray | str) -> dict[str, Any]:
    """Validate generated Markdown and return its canonical prompt payload."""

    if isinstance(document, str):
        raw = document.encode("utf-8", "strict")
    elif isinstance(document, (bytes, bytearray)):
        raw = bytes(document)
    else:
        raise RenderError("prompt Markdown must be text or bytes")
    if not raw or raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise RenderError("prompt Markdown must be UTF-8 with LF-only framing")
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0] != (BEGIN_MARKER + "\n").encode("ascii") or lines[-1] != (END_MARKER + "\n").encode("ascii"):
        raise RenderError("prompt markers are not paired at document boundaries")
    try:
        payload_begin = lines.index((PAYLOAD_BEGIN_MARKER + "\n").encode("ascii"))
        payload_end = lines.index((PAYLOAD_END_MARKER + "\n").encode("ascii"))
    except ValueError as exc:
        raise RenderError("prompt payload markers are missing") from exc
    if payload_begin <= 1 or payload_end <= payload_begin + 2 or payload_end >= len(lines) - 1:
        raise RenderError("prompt payload marker placement is invalid")
    if any(line == (PAYLOAD_BEGIN_MARKER + "\n").encode("ascii") for line in lines[payload_begin + 1 : payload_end]):
        raise RenderError("nested prompt payload marker")
    metadata = _metadata(lines[1:payload_begin])
    fence_match = _FENCE_RE.fullmatch(lines[payload_begin + 1].decode("ascii", "strict"))
    if fence_match is None:
        raise RenderError("prompt payload must use an adaptive JSON fence")
    fence = fence_match.group("fence").encode("ascii")
    if lines[payload_end - 1] != fence + b"\n":
        raise RenderError("prompt payload fence is malformed or mismatched")
    payload = b"".join(lines[payload_begin + 2 : payload_end - 1])
    if not payload.endswith(b"\n"):
        raise RenderError("prompt payload must end with exactly one LF")
    if _max_backtick_run(payload) >= len(fence):
        raise RenderError("prompt payload fence is not adaptive")
    try:
        value = canonical_json_loads(payload)
    except Exception as exc:
        raise RenderError(f"prompt payload is not canonical JSON: {exc}") from exc
    if not isinstance(value, Mapping) or value.get("kind") != "prompt":
        raise RenderError("prompt payload is not a prompt contract")
    record = PromptRecord.from_dict(value)
    if metadata["schema-version"] != "1" or metadata["prompt-id"] != record.prompt_id:
        raise RenderError("prompt metadata does not match payload")
    if metadata["revision"] != str(record.revision):
        raise RenderError("prompt revision metadata does not match payload")
    try:
        if int(metadata["payload-bytes"]) != len(payload):
            raise RenderError("prompt payload byte count does not match")
    except ValueError as exc:
        raise RenderError("prompt payload byte count is malformed") from exc
    if metadata["payload-digest"] != sha256_digest(payload):
        raise RenderError("prompt payload digest does not match")
    if render_prompt(record) != raw:
        raise RenderError("prompt Markdown is not canonical for its payload")
    return record.to_dict()


def validate_prompt_markdown(document: bytes | bytearray | str) -> dict[str, Any]:
    return parse_prompt_markdown(document)


def payload_from_prompt_markdown(document: bytes | bytearray | str) -> bytes:
    value = parse_prompt_markdown(document)
    return prompt_payload_bytes(value)


@dataclass(frozen=True)
class RecoveryItem:
    path: str
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "status": self.status, "reason": self.reason}


@dataclass(frozen=True)
class RecoveryReport:
    root: str
    dry_run: bool
    items: tuple[RecoveryItem, ...]

    @property
    def actionable(self) -> tuple[RecoveryItem, ...]:
        return tuple(item for item in self.items if item.status not in {"current", "orphaned-complete"})

    def to_dict(self) -> dict[str, Any]:
        return {"root": self.root, "dry_run": self.dry_run, "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True)
class CheckReport:
    valid: bool
    stale: bool
    generation_id: str | None
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "stale": self.stale,
            "generation_id": self.generation_id,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class GenerationResult:
    generation_id: str
    snapshot: Path
    current: Path
    manifest: Mapping[str, Any]

    @property
    def generation_digest(self) -> str:
        return "sha256:" + self.generation_id


class _GenerationLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_GenerationLock":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            self.fd = os.open(self.path, flags, 0o600)
            os.write(self.fd, b"prompt-generator generation lock\n")
            os.fsync(self.fd)
        except FileExistsError as exc:
            raise GenerationBusy(f"generation lock already exists: {self.path}") from exc
        except OSError as exc:
            raise GenerationBusy(f"unable to acquire generation lock: {self.path}") from exc
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _clone(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _clone(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_clone(child) for child in value]
    return value


class GenerationStore:
    """Publish and validate complete immutable snapshots below an output root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        if self.root.exists() or self.root.is_symlink():
            if self.root.is_symlink() or not self.root.is_dir():
                raise PathSafetyError("generation root must be a real directory")
        else:
            self.root.mkdir(parents=True)
        self.root = assert_root_directory(self.root)
        self.generations = self.root / GENERATIONS_DIRNAME
        if self.generations.exists() or self.generations.is_symlink():
            if self.generations.is_symlink() or not self.generations.is_dir():
                raise PathSafetyError(".generations must be a real directory")
        else:
            self.generations.mkdir(mode=0o755)
        self.current_path = self.root / CURRENT_FILENAME
        self.lock_path = self.root / LOCK_FILENAME

    def _catalog(self, catalog: PromptCatalog | Mapping[str, Any]) -> PromptCatalog:
        if isinstance(catalog, PromptCatalog):
            return catalog
        if not isinstance(catalog, Mapping):
            raise GenerationError("catalog must be a PromptCatalog or mapping")
        return PromptCatalog.from_dict(catalog)

    def _outputs(self, catalog: PromptCatalog) -> tuple[dict[str, bytes], list[dict[str, Any]], list[dict[str, Any]]]:
        output_bytes: dict[str, bytes] = {"catalog.json": catalog.to_bytes()}
        file_records: list[dict[str, Any]] = []
        prompt_records: list[dict[str, Any]] = []
        catalog_record = IntegrityRecord.from_bytes(output_bytes["catalog.json"])
        file_records.append(
            {
                "path": "catalog.json",
                "kind": "catalog",
                "payload_bytes": catalog_record.byte_count,
                "payload_sha256": catalog_record.digest,
                "whole_file_bytes": catalog_record.byte_count,
                "whole_file_sha256": catalog_record.digest,
            }
        )
        for record in catalog.records:
            payload = prompt_payload_bytes(record)
            rendered = render_prompt(record)
            project_id = safe_component(record.project_id, field="project ID")
            local_id = safe_component(record.local_id, field="local prompt ID")
            relative = f"prompts/{project_id}/{local_id}/r{record.revision}.md"
            if relative in output_bytes:
                raise SnapshotError(f"generated path collision: {relative}")
            output_bytes[relative] = rendered
            fields = payload_and_file_integrity(payload, rendered)
            entry = {
                "path": relative,
                "kind": "prompt",
                "prompt_id": record.prompt_id,
                "revision": record.revision,
                **fields,
            }
            file_records.append(entry)
            prompt_records.append({key: entry[key] for key in ("prompt_id", "revision", "path", "payload_bytes", "payload_sha256", "whole_file_bytes", "whole_file_sha256")})
        return output_bytes, file_records, prompt_records

    def _basis(self, catalog: PromptCatalog, file_records: list[dict[str, Any]]) -> str:
        basis = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "kind": "generation-basis",
            "generator_version": GENERATOR_VERSION,
            "catalog_digest": catalog.digest(),
            "files": file_records,
        }
        return sha256_digest(canonical_json_bytes(basis))[7:]

    @staticmethod
    def _manifest(
        generation_id: str,
        catalog: PromptCatalog,
        file_records: list[dict[str, Any]],
        prompt_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "kind": "generation-manifest",
            "generator_version": GENERATOR_VERSION,
            "generation_id": generation_id,
            "catalog_digest": catalog.digest(),
            "files": _clone(file_records),
            "prompts": _clone(prompt_records),
        }

    @staticmethod
    def _index(
        generation_id: str,
        catalog: PromptCatalog,
        file_records: list[dict[str, Any]],
        prompt_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "kind": "generation-index",
            "generator_version": GENERATOR_VERSION,
            "generation_id": generation_id,
            "catalog_digest": catalog.digest(),
            "files": _clone(file_records),
            "prompts": _clone(prompt_records),
        }

    def publish(self, catalog: PromptCatalog | Mapping[str, Any]) -> GenerationResult:
        """Build, validate, and atomically point ``CURRENT`` at a snapshot."""

        catalog_object = self._catalog(catalog)
        with _GenerationLock(self.lock_path):
            output_bytes, file_records, prompt_records = self._outputs(catalog_object)
            generation_id = self._basis(catalog_object, file_records)
            snapshot = self.generations / generation_id
            manifest = self._manifest(generation_id, catalog_object, file_records, prompt_records)
            index = self._index(generation_id, catalog_object, file_records, prompt_records)
            if snapshot.exists() or snapshot.is_symlink():
                if snapshot.is_symlink() or not snapshot.is_dir():
                    raise SnapshotError(f"generation directory collision: {generation_id}")
                self._validate_snapshot(snapshot, expected_generation_id=generation_id)
            else:
                temporary = self.generations / f".{generation_id}.tmp-{secrets.token_hex(8)}"
                if temporary.exists() or temporary.is_symlink():
                    raise SnapshotError("temporary generation path collision")
                try:
                    temporary.mkdir(mode=0o755)
                    (temporary / "prompts").mkdir(mode=0o755)
                    for relative, data in output_bytes.items():
                        target = temporary / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_bytes(target, data, replace=False, mode=0o444)
                    atomic_write_bytes(temporary / "manifest.json", canonical_json_bytes(manifest), mode=0o444)
                    atomic_write_bytes(temporary / "index.json", canonical_json_bytes(index), mode=0o444)
                    self._validate_snapshot(temporary, expected_generation_id=generation_id)
                    self._freeze_snapshot(temporary)
                    os.replace(temporary, snapshot)
                    temporary = None  # type: ignore[assignment]
                    directory_fd = os.open(self.generations, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    if temporary is not None and temporary.exists():
                        self._leave_unreachable(temporary)
            self._publish_current(generation_id)
            return GenerationResult(generation_id, snapshot, self.current_path, manifest)

    @staticmethod
    def _leave_unreachable(path: Path) -> None:
        # Recovery is intentionally report-only.  Keep an interrupted tree for
        # classification rather than silently pruning evidence.
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass

    @staticmethod
    def _freeze_snapshot(snapshot: Path) -> None:
        """Make a validated snapshot read-only before it becomes reachable."""

        for directory in sorted((item for item in snapshot.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            os.chmod(directory, 0o555)
        os.chmod(snapshot, 0o555)

    def _publish_current(self, generation_id: str) -> None:
        if _HEX_RE.fullmatch(generation_id) is None:
            raise SnapshotError("invalid generation ID")
        if self.current_path.exists() or self.current_path.is_symlink():
            old_id = self._read_current_id()
            self._validate_snapshot(self.generations / old_id, expected_generation_id=old_id)
            assert_regular_output(self.current_path, allow_existing=True)
        temporary = self.root / f".{CURRENT_FILENAME}.tmp-{secrets.token_hex(8)}"
        try:
            atomic_write_bytes(temporary, (generation_id + "\n").encode("ascii"), mode=0o444)
            os.replace(temporary, self.current_path)
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _read_current_id(self) -> str:
        if not self.current_path.exists() or self.current_path.is_symlink():
            raise SnapshotError("CURRENT is missing or not a regular file")
        assert_regular_output(self.current_path, allow_existing=True)
        raw = self.current_path.read_bytes()
        if len(raw) != 65 or not raw.endswith(b"\n"):
            raise SnapshotError("CURRENT is not a canonical generation pointer")
        try:
            value = raw[:-1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise SnapshotError("CURRENT is not ASCII") from exc
        if _HEX_RE.fullmatch(value) is None:
            raise SnapshotError("CURRENT contains an unsafe generation ID")
        return value

    def read_current(self) -> GenerationResult:
        generation_id = self._read_current_id()
        snapshot = self.generations / generation_id
        manifest, _ = self._validate_snapshot(snapshot, expected_generation_id=generation_id)
        return GenerationResult(generation_id, snapshot, self.current_path, manifest)

    def check(self, catalog: PromptCatalog | Mapping[str, Any] | None = None) -> CheckReport:
        generation_id: str | None = None
        issues: list[str] = []
        try:
            generation_id = self._read_current_id()
            snapshot = self.generations / generation_id
            manifest, _ = self._validate_snapshot(snapshot, expected_generation_id=generation_id)
            stale = False
            if catalog is not None:
                expected = self._catalog(catalog).digest()
                if manifest.get("catalog_digest") != expected:
                    stale = True
                    issues.append("CURRENT catalog digest is stale")
            return CheckReport(not issues, stale, generation_id, tuple(issues))
        except (GenerationError, IntegrityError, PathSafetyError, OSError) as exc:
            issues.append(str(exc))
            return CheckReport(False, True if catalog is not None else False, generation_id, tuple(issues))

    def check_current(self, catalog: PromptCatalog | Mapping[str, Any] | None = None) -> CheckReport:
        return self.check(catalog)

    def is_stale(self, catalog: PromptCatalog | Mapping[str, Any]) -> bool:
        return self.check(catalog).stale

    def classify_recovery(self, *, dry_run: bool = True) -> RecoveryReport:
        if not dry_run:
            raise RecoveryError("generation recovery is report-only; deletion requires separate authority")
        current: str | None = None
        items: list[RecoveryItem] = []
        try:
            current = self._read_current_id()
        except (GenerationError, OSError) as exc:
            items.append(RecoveryItem(CURRENT_FILENAME, "invalid-current", str(exc)))
        try:
            children = sorted(self.generations.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            items.append(RecoveryItem(GENERATIONS_DIRNAME, "unavailable", str(exc)))
            return RecoveryReport(str(self.root), True, tuple(items))
        for child in children:
            if child.is_symlink():
                items.append(RecoveryItem(str(child.relative_to(self.root)), "unsafe-symlink", "symlink is never recoverable automatically"))
                continue
            if not child.is_dir():
                items.append(RecoveryItem(str(child.relative_to(self.root)), "unexpected", "non-directory generation entry"))
                continue
            relative = str(child.relative_to(self.root))
            try:
                generation_id = child.name
                self._validate_snapshot(child, expected_generation_id=generation_id if _HEX_RE.fullmatch(generation_id) else None)
            except (GenerationError, IntegrityError, PathSafetyError, OSError) as exc:
                status = "interrupted" if child.name.startswith(".") else "invalid"
                items.append(RecoveryItem(relative, status, str(exc)))
                continue
            if child.name.startswith("."):
                status = "interrupted-complete"
            elif current == child.name:
                status = "current"
            else:
                status = "orphaned-complete"
            items.append(RecoveryItem(relative, status, "validated complete snapshot"))
        return RecoveryReport(str(self.root), True, tuple(items))

    def recover(self, *, dry_run: bool = True) -> RecoveryReport:
        return self.classify_recovery(dry_run=dry_run)

    def _validate_snapshot(self, snapshot: Path, *, expected_generation_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise SnapshotError(f"snapshot is not a real directory: {snapshot}")
        manifest_path = snapshot / "manifest.json"
        index_path = snapshot / "index.json"
        catalog_path = snapshot / "catalog.json"
        for path in (manifest_path, index_path, catalog_path):
            if path.is_symlink() or not path.is_file() or path.lstat().st_nlink != 1:
                raise SnapshotError(f"snapshot metadata is not a safe regular file: {path}")
        try:
            manifest = canonical_json_loads(manifest_path.read_bytes())
            index = canonical_json_loads(index_path.read_bytes())
            catalog_value = canonical_json_loads(catalog_path.read_bytes())
        except Exception as exc:
            raise SnapshotError(f"snapshot metadata is not canonical JSON: {snapshot}") from exc
        if not isinstance(manifest, Mapping) or manifest.get("kind") != "generation-manifest":
            raise SnapshotError("invalid generation manifest")
        if not isinstance(index, Mapping) or index.get("kind") != "generation-index":
            raise SnapshotError("invalid generation index")
        manifest_keys = {"schema_version", "kind", "generator_version", "generation_id", "catalog_digest", "files", "prompts"}
        index_keys = manifest_keys
        if set(manifest) != manifest_keys or set(index) != index_keys:
            raise SnapshotError("manifest or index has unknown or missing fields")
        if manifest.get("schema_version") != GENERATION_SCHEMA_VERSION or index.get("schema_version") != GENERATION_SCHEMA_VERSION:
            raise SnapshotError("unsupported generation metadata version")
        if manifest.get("generator_version") != GENERATOR_VERSION or index.get("generator_version") != GENERATOR_VERSION:
            raise SnapshotError("unsupported generator version")
        generation_id = manifest.get("generation_id")
        if not isinstance(generation_id, str) or _HEX_RE.fullmatch(generation_id) is None:
            raise SnapshotError("invalid manifest generation ID")
        if expected_generation_id is not None and generation_id != expected_generation_id:
            raise SnapshotError("snapshot directory does not match manifest generation ID")
        if index.get("generation_id") != generation_id or manifest.get("catalog_digest") != index.get("catalog_digest"):
            raise SnapshotError("manifest/index generation identity mismatch")
        if manifest.get("files") != index.get("files") or manifest.get("prompts") != index.get("prompts"):
            raise SnapshotError("manifest/index content mismatch")
        if not isinstance(catalog_value, Mapping):
            raise SnapshotError("catalog snapshot is not an object")
        try:
            catalog = PromptCatalog.from_dict(catalog_value)
        except Exception as exc:
            raise SnapshotError("catalog snapshot is invalid") from exc
        if manifest.get("catalog_digest") != catalog.digest():
            raise SnapshotError("catalog digest does not match manifest")
        try:
            expected_outputs, expected_file_records, expected_prompt_records = self._outputs(catalog)
        except Exception as exc:
            raise SnapshotError("validated catalog cannot produce expected outputs") from exc
        expected_generation_id = self._basis(catalog, expected_file_records)
        if generation_id != expected_generation_id:
            raise SnapshotError("snapshot generation basis does not match validated catalog")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise SnapshotError("manifest files are missing")
        if files != expected_file_records:
            raise SnapshotError("manifest files do not match validated catalog outputs")
        if manifest.get("prompts") != expected_prompt_records:
            raise SnapshotError("manifest prompts do not match validated catalog outputs")
        # ``manifest.json`` and ``index.json`` are self-describing metadata
        # and are intentionally not entries in their own file list.  The
        # catalog is a generated output and therefore is listed exactly once.
        metadata_files = {"manifest.json", "index.json"}
        seen_paths: set[str] = set()
        actual_prompt_paths: set[str] = set()
        for entry in files:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
                raise SnapshotError("malformed manifest file entry")
            relative = entry["path"]
            if relative in metadata_files or relative in seen_paths:
                raise SnapshotError(f"duplicate snapshot path: {relative}")
            seen_paths.add(relative)
            try:
                target = rooted_path(snapshot, relative, allow_missing=False)
            except (PathSafetyError, OSError) as exc:
                raise SnapshotError(f"unsafe snapshot path: {relative}") from exc
            if target.is_symlink() or not target.is_file() or target.lstat().st_nlink != 1:
                raise SnapshotError(f"snapshot output is not a safe regular file: {relative}")
            actual_bytes = target.read_bytes()
            if actual_bytes != expected_outputs[relative]:
                raise SnapshotError(f"snapshot output bytes do not match validated catalog: {relative}")
            actual = IntegrityRecord.from_bytes(actual_bytes)
            try:
                expected_whole = IntegrityRecord(entry["whole_file_bytes"], entry["whole_file_sha256"])
            except Exception as exc:
                raise SnapshotError(f"malformed whole-file integrity for {relative}") from exc
            if actual != expected_whole:
                raise SnapshotError(f"whole-file integrity mismatch for {relative}")
            if entry.get("kind") == "prompt":
                actual_prompt_paths.add(relative)
                parsed = parse_prompt_markdown(target.read_bytes())
                payload = prompt_payload_bytes(parsed)
                try:
                    expected_payload = IntegrityRecord(entry["payload_bytes"], entry["payload_sha256"])
                except Exception as exc:
                    raise SnapshotError(f"malformed payload integrity for {relative}") from exc
                if IntegrityRecord.from_bytes(payload) != expected_payload:
                    raise SnapshotError(f"payload integrity mismatch for {relative}")
                if parsed.get("prompt_id") != entry.get("prompt_id") or parsed.get("revision") != entry.get("revision"):
                    raise SnapshotError(f"prompt identity mismatch for {relative}")
            elif entry.get("kind") == "catalog":
                if relative != "catalog.json":
                    raise SnapshotError("catalog entry has unexpected path")
            else:
                raise SnapshotError(f"unknown snapshot file kind for {relative}")
        catalog_paths = [entry.get("path") for entry in files if entry.get("kind") == "catalog"]
        if catalog_paths != ["catalog.json"]:
            raise SnapshotError("catalog must appear exactly once in manifest")
        prompt_paths = {entry.get("path") for entry in files if entry.get("kind") == "prompt"}
        if prompt_paths != actual_prompt_paths:
            raise SnapshotError("prompt manifest path mismatch")
        for path in (manifest_path, index_path):
            if path.stat().st_nlink != 1:
                raise SnapshotError(f"metadata file is hard-linked: {path}")
        allowed = {"manifest.json", "index.json", "catalog.json", *actual_prompt_paths}
        allowed_directories = {"prompts"}
        for relative in actual_prompt_paths:
            path = Path(relative).parent
            while str(path) != ".":
                allowed_directories.add(str(path))
                path = path.parent
        for path in snapshot.rglob("*"):
            if path.is_symlink():
                raise SnapshotError(f"snapshot contains symlink: {path}")
            relative = str(path.relative_to(snapshot))
            if path.is_file() and relative not in allowed:
                raise SnapshotError(f"snapshot contains unlisted file: {path}")
            if path.is_dir() and relative not in allowed_directories:
                raise SnapshotError(f"snapshot contains unlisted directory: {path}")
        return dict(manifest), dict(index)


def generate(catalog: PromptCatalog | Mapping[str, Any], root: str | os.PathLike[str]) -> GenerationResult:
    return GenerationStore(root).publish(catalog)


def check(root: str | os.PathLike[str], catalog: PromptCatalog | Mapping[str, Any] | None = None) -> CheckReport:
    return GenerationStore(root).check(catalog)


def classify_recovery(root: str | os.PathLike[str], *, dry_run: bool = True) -> RecoveryReport:
    return GenerationStore(root).classify_recovery(dry_run=dry_run)


def recover(root: str | os.PathLike[str], *, dry_run: bool = True) -> RecoveryReport:
    return GenerationStore(root).recover(dry_run=dry_run)


# Compatibility aliases used by callers that phrase the same operation as a
# snapshot or artifact publication.
render = render_prompt
parse_rendered_prompt = parse_prompt_markdown
validate_rendered_prompt = validate_prompt_markdown
publish_snapshot = generate
GenerationManager = GenerationStore
