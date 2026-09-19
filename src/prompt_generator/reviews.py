"""Exact-revision review evidence and invalidation.

Review entries bind an outcome to the source revision and payload digest that
were actually reviewed.  They are evidence records, not approvals and not a
replacement for an independent review or branch protection.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping

from .contracts import canonical_json_bytes
from .identity import validate_prompt_id


SCHEMA_VERSION = 1


class ReviewError(ValueError):
    """Base class for review-ledger failures."""


class ReviewCollisionError(ReviewError):
    """Raised when one review key is reused for different evidence."""


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8", "strict")
    else:
        raw = canonical_json_bytes(value)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(c in "0123456789abcdef" for c in value[7:])


def _strict_schema_version(value: Any, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != SCHEMA_VERSION:
        raise ReviewError(f"{name} schema_version must be integer {SCHEMA_VERSION}")


def _strict_kind(value: Any, expected: str, *, name: str) -> None:
    if not isinstance(value, str) or value != expected:
        raise ReviewError(f"{name} kind must be {expected!r}")


def _strict_mapping(value: Any, *, required: set[str], allowed: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewError(f"{name} must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - allowed
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ReviewError(f"{name} has invalid fields ({'; '.join(details)})")
    return value


@dataclass(frozen=True)
class ReviewEvidence:
    prompt_id: str
    revision: int
    source_revision: str
    payload_digest: str
    reviewer: str
    outcome: str = "pass"
    source_digest: str | None = None
    evidence_id: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_prompt_id(self.prompt_id)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ReviewError("revision must be a positive integer")
        if not isinstance(self.source_revision, str) or not self.source_revision.strip():
            raise ReviewError("source_revision must be non-empty")
        if not _valid_digest(self.payload_digest):
            raise ReviewError("payload_digest must be sha256")
        if self.source_digest is not None and not _valid_digest(self.source_digest):
            raise ReviewError("source_digest must be sha256")
        if not isinstance(self.reviewer, str) or not self.reviewer.strip():
            raise ReviewError("reviewer must be non-empty")
        if not isinstance(self.outcome, str) or self.outcome not in {"pass", "fail", "accepted", "rejected", "pending"}:
            raise ReviewError("unsupported review outcome")
        if self.evidence_id is not None and (not isinstance(self.evidence_id, str) or not self.evidence_id.strip()):
            raise ReviewError("evidence_id must be non-empty text when supplied")
        if not isinstance(self.notes, (list, tuple)) or any(not isinstance(note, str) or not note.strip() for note in self.notes):
            raise ReviewError("notes must be an array of non-empty text")

    @property
    def key(self) -> tuple[str, int, str]:
        return self.prompt_id, self.revision, self.reviewer

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "review-evidence",
            "prompt_id": self.prompt_id,
            "revision": self.revision,
            "source_revision": self.source_revision,
            "payload_digest": self.payload_digest,
            "reviewer": self.reviewer,
            "outcome": self.outcome,
        }
        if self.source_digest is not None:
            result["source_digest"] = self.source_digest
        if self.evidence_id is not None:
            result["evidence_id"] = self.evidence_id
        if self.notes:
            result["notes"] = list(self.notes)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewEvidence":
        allowed = {
            "schema_version", "kind", "prompt_id", "revision", "source_revision", "payload_digest",
            "reviewer", "outcome", "source_digest", "evidence_id", "notes",
        }
        data = _strict_mapping(
            value,
            required={"schema_version", "kind", "prompt_id", "revision", "source_revision", "payload_digest", "reviewer", "outcome"},
            allowed=allowed,
            name="review evidence",
        )
        _strict_schema_version(data["schema_version"], name="review evidence")
        _strict_kind(data["kind"], "review-evidence", name="review evidence")
        if isinstance(data["revision"], bool) or not isinstance(data["revision"], int):
            raise ReviewError("review evidence revision must be integer")
        notes = data.get("notes", [])
        if not isinstance(notes, list):
            raise ReviewError("review evidence notes must be an array")
        return cls(
            data["prompt_id"], data["revision"], data["source_revision"], data["payload_digest"],
            data["reviewer"], data["outcome"], data.get("source_digest"), data.get("evidence_id"), tuple(notes),
        )


@dataclass(frozen=True)
class ReviewStatus:
    valid: bool
    reason: str
    evidence: ReviewEvidence | None = None

    def __bool__(self) -> bool:
        return self.valid

    def to_dict(self) -> dict[str, Any]:
        result = {"valid": self.valid, "reason": self.reason}
        if self.evidence is not None:
            result["evidence"] = self.evidence.to_dict()
        return result


class ReviewLedger:
    """A deterministic ledger whose entries are invalidated by material drift."""

    def __init__(self, entries: Iterable[ReviewEvidence | Mapping[str, Any]] = ()) -> None:
        self._entries: dict[tuple[str, int, str], ReviewEvidence] = {}
        for entry in entries:
            self.record(entry)

    @property
    def entries(self) -> tuple[ReviewEvidence, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    @property
    def records(self) -> tuple[ReviewEvidence, ...]:
        return self.entries

    @staticmethod
    def _coerce(entry: ReviewEvidence | Mapping[str, Any], **fields: Any) -> ReviewEvidence:
        if isinstance(entry, ReviewEvidence):
            return entry
        data = dict(entry)
        data.update({key: value for key, value in fields.items() if value is not None})
        record_fields = {"schema_version", "kind"}
        if record_fields & set(data):
            return ReviewEvidence.from_dict(data)
        allowed = {
            "prompt_id", "revision", "source_revision", "payload_digest", "reviewer", "outcome",
            "source_digest", "evidence_id", "notes", "payload", "source",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ReviewError(f"review evidence has unknown field(s): {sorted(unknown)}")
        notes = data.get("notes", [])
        if not isinstance(notes, (list, tuple)):
            raise ReviewError("notes must be an array")
        return ReviewEvidence(
            data["prompt_id"], data["revision"], data["source_revision"], data["payload_digest"],
            data["reviewer"], data.get("outcome", "pass"), data.get("source_digest"),
            data.get("evidence_id"), tuple(notes),
        )

    def record(self, entry: ReviewEvidence | Mapping[str, Any] | None = None, **fields: Any) -> ReviewEvidence:
        if "payload" in fields and "payload_digest" not in fields:
            fields["payload_digest"] = digest(fields.pop("payload"))
        if "source" in fields and "source_digest" not in fields:
            fields["source_digest"] = digest(fields.pop("source"))
        if entry is None:
            item = self._coerce(fields)
        else:
            item = self._coerce(entry, **fields)
        prior = self._entries.get(item.key)
        if prior is not None:
            if prior == item:
                return prior
            raise ReviewCollisionError("review identity already has different evidence")
        self._entries[item.key] = item
        return item

    def _matching(self, prompt_id: str, revision: int, reviewer: str | None = None) -> tuple[ReviewEvidence, ...]:
        validate_prompt_id(prompt_id)
        return tuple(item for item in self.entries if item.prompt_id == prompt_id and item.revision == revision and (reviewer is None or item.reviewer == reviewer))

    def status(
        self,
        prompt_id: str,
        revision: int,
        *,
        source_revision: str,
        payload_digest: str,
        source_digest: str | None = None,
        reviewer: str | None = None,
    ) -> ReviewStatus:
        if not _valid_digest(payload_digest):
            raise ReviewError("payload_digest must be sha256")
        if source_digest is not None and not _valid_digest(source_digest):
            raise ReviewError("source_digest must be sha256")
        candidates = self._matching(prompt_id, revision, reviewer)
        if not candidates:
            return ReviewStatus(False, "no review evidence for exact prompt revision")
        for item in candidates:
            if item.outcome not in {"pass", "accepted"}:
                continue
            if item.source_revision != source_revision:
                continue
            if item.payload_digest != payload_digest:
                continue
            if source_digest is not None and source_digest != item.source_digest:
                continue
            return ReviewStatus(True, "exact source revision and payload are reviewed", item)
        return ReviewStatus(False, "review evidence was invalidated by source or payload change", candidates[0])

    def is_valid(self, *args: Any, **kwargs: Any) -> bool:
        return bool(self.status(*args, **kwargs))

    valid_for = is_valid

    def invalidate_if_changed(
        self,
        prompt_id: str,
        revision: int,
        *,
        source_revision: str,
        payload_digest: str,
        source_digest: str | None = None,
    ) -> tuple[ReviewEvidence, ...]:
        """Return evidence made stale by a material source/payload change.

        Entries remain in the ledger for auditability; invalidation is logical
        and cannot accidentally erase review history.
        """

        return tuple(
            item for item in self._matching(prompt_id, revision)
            if item.source_revision != source_revision
            or item.payload_digest != payload_digest
            or (source_digest is not None and item.source_digest != source_digest)
        )

    invalidate = invalidate_if_changed

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "review-ledger", "entries": [item.to_dict() for item in self.entries]}

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "ReviewLedger":
        data = _strict_mapping(
            document,
            required={"schema_version", "kind", "entries"},
            allowed={"schema_version", "kind", "entries"},
            name="review ledger",
        )
        _strict_schema_version(data["schema_version"], name="review ledger")
        _strict_kind(data["kind"], "review-ledger", name="review ledger")
        if not isinstance(data["entries"], list):
            raise ReviewError("review ledger entries must be an array")
        return cls(ReviewEvidence.from_dict(entry) for entry in data["entries"])
