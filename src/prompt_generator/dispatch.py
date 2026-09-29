"""Local duplicate-dispatch guards.

The dispatch ledger is deliberately only a local, append-only *intent* ledger.
It never calls a provider, opens a network connection, or changes a remote
branch.  A carrier is part of the identity of an intent: sending revision 2
to carrier ``a`` does not make sending it to carrier ``b`` a duplicate, while
an alias or a lineage reference cannot bypass a previous entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Iterable, Mapping

from .catalog import PromptCatalog, PromptRecord
from .contracts import canonical_json_bytes
from .identity import validate_prompt_id


SCHEMA_VERSION = 1


class DispatchError(ValueError):
    """Base class for local dispatch-guard failures."""


class DuplicateDispatchError(DispatchError):
    """Raised when an equivalent local dispatch intent already exists."""


class DispatchCollisionError(DispatchError):
    """Raised when an identity is reused with different content."""


class NonDispatchableCarrierError(DispatchError):
    """Raised for a malformed carrier descriptor."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def payload_digest(payload: Any) -> str:
    """Return a stable digest for bytes, text, or inert JSON data."""

    if isinstance(payload, bytes):
        data = payload
    elif isinstance(payload, str):
        data = payload.encode("utf-8", "strict")
    else:
        data = canonical_json_bytes(payload)
    return _sha256(data)


def _strict_schema_version(value: Any, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != SCHEMA_VERSION:
        raise DispatchError(f"{name} schema_version must be integer {SCHEMA_VERSION}")


def _strict_kind(value: Any, expected: str, *, name: str) -> None:
    if not isinstance(value, str) or value != expected:
        raise DispatchError(f"{name} kind must be {expected!r}")


def _strict_optional_text(value: Any, *, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise DispatchError(f"{name} must be non-empty text when supplied")
    return value


def _strict_lineage(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DispatchError("lineage must be an array")
    result = tuple(value)
    for item in result:
        if not isinstance(item, str):
            raise DispatchError("lineage entries must be text")
    return result


def _strict_mapping(value: Any, *, required: set[str], allowed: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DispatchError(f"{name} must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - allowed
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise DispatchError(f"{name} has invalid fields ({'; '.join(details)})")
    return value


@dataclass(frozen=True)
class Carrier:
    """A local carrier descriptor; it contains no executable transport."""

    carrier_id: str
    dispatchable: bool = False
    kind: str = "markdown"
    content_digest: str | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.carrier_id, str) or not self.carrier_id.strip():
            raise NonDispatchableCarrierError("carrier_id must be non-empty text")
        if not isinstance(self.dispatchable, bool):
            raise NonDispatchableCarrierError("dispatchable must be boolean")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise NonDispatchableCarrierError("carrier kind must be non-empty text")
        if self.content_digest is not None and not _is_digest(self.content_digest):
            raise NonDispatchableCarrierError("carrier content_digest must be sha256")
        if self.source_revision is not None and (not isinstance(self.source_revision, str) or not self.source_revision.strip()):
            raise NonDispatchableCarrierError("carrier source_revision must be non-empty text when supplied")

    @property
    def digest(self) -> str:
        return _sha256(canonical_json_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "carrier_id": self.carrier_id,
            "dispatchable": self.dispatchable,
            "kind": self.kind,
        }
        if self.content_digest is not None:
            result["content_digest"] = self.content_digest
        if self.source_revision is not None:
            result["source_revision"] = self.source_revision
        return result

    @classmethod
    def from_value(cls, value: "Carrier | Mapping[str, Any] | str") -> "Carrier":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, Mapping):
            raise NonDispatchableCarrierError("carrier must be a descriptor")
        allowed = {"carrier_id", "dispatchable", "kind", "content_digest", "source_revision", "carrier_digest"}
        unknown = set(value) - allowed
        if unknown:
            raise NonDispatchableCarrierError(f"unknown carrier field(s): {sorted(unknown)}")
        carrier_id = value.get("carrier_id")
        dispatchable = value.get("dispatchable", False)
        kind = value.get("kind", "markdown")
        content_digest = value.get("content_digest")
        source_revision = value.get("source_revision")
        if not isinstance(carrier_id, str):
            raise NonDispatchableCarrierError("carrier_id must be text")
        if not isinstance(dispatchable, bool):
            raise NonDispatchableCarrierError("dispatchable must be boolean")
        if not isinstance(kind, str):
            raise NonDispatchableCarrierError("carrier kind must be text")
        if content_digest is not None and not isinstance(content_digest, str):
            raise NonDispatchableCarrierError("carrier content_digest must be text or null")
        if source_revision is not None and not isinstance(source_revision, str):
            raise NonDispatchableCarrierError("carrier source_revision must be text or null")
        carrier = cls(
            carrier_id,
            dispatchable,
            kind,
            content_digest,
            source_revision,
        )
        supplied = value.get("carrier_digest")
        if supplied is not None and supplied != carrier.digest:
            raise DispatchCollisionError("carrier digest does not match descriptor")
        return carrier


@dataclass(frozen=True)
class DispatchRequest:
    prompt_id: str
    revision: int
    carrier: Carrier
    payload_digest: str
    source_revision: str | None = None
    lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_prompt_id(self.prompt_id)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise DispatchError("revision must be a positive integer")
        if not _is_digest(self.payload_digest):
            raise DispatchError("payload_digest must be sha256")
        _strict_optional_text(self.source_revision, name="source_revision")
        if not isinstance(self.lineage, (list, tuple)):
            raise DispatchError("lineage must be an array")
        for item in self.lineage:
            if not isinstance(item, str):
                raise DispatchError("lineage entries must be text")
            validate_prompt_id(item)

    @property
    def key(self) -> tuple[str, int, str]:
        return self.prompt_id, self.revision, self.carrier.carrier_id

    @classmethod
    def from_value(
        cls,
        value: "DispatchRequest | Mapping[str, Any] | str",
        revision: int | None = None,
        carrier: Carrier | Mapping[str, Any] | str | None = None,
        payload: Any = None,
        *,
        payload_digest_value: str | None = None,
        source_revision: str | None = None,
        lineage: Iterable[str] = (),
    ) -> "DispatchRequest":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            data = value
            allowed = {
                "schema_version", "kind", "prompt_id", "revision", "carrier", "carrier_id",
                "dispatchable", "carrier_kind", "carrier_content_digest", "carrier_digest",
                "payload_digest", "payload", "source_revision", "lineage",
            }
            unknown = set(data) - allowed
            if unknown:
                raise DispatchError(f"dispatch request has unknown field(s): {sorted(unknown)}")
            if "schema_version" not in data or "kind" not in data:
                raise DispatchError("dispatch request requires schema_version and kind")
            _strict_schema_version(data["schema_version"], name="dispatch request")
            _strict_kind(data["kind"], "dispatch-request", name="dispatch request")
            prompt_id = data.get("prompt_id")
            revision = data.get("revision", revision)
            carrier = data.get("carrier", carrier)
            if carrier is None and data.get("carrier_id") is not None:
                carrier = {
                    "carrier_id": data.get("carrier_id"),
                    "dispatchable": data.get("dispatchable", False),
                    "kind": data.get("carrier_kind", "markdown"),
                    "content_digest": data.get("carrier_content_digest"),
                }
            payload_digest_value = data.get("payload_digest", payload_digest_value)
            source_revision = data.get("source_revision", source_revision)
            lineage = data.get("lineage", lineage)
            payload = data.get("payload", payload)
        else:
            prompt_id = value
        if not isinstance(prompt_id, str) or revision is None or carrier is None:
            raise DispatchError("prompt_id, revision, carrier, and payload_digest are required")
        if payload_digest_value is None:
            if payload is None:
                raise DispatchError("payload_digest or payload is required")
            payload_digest_value = payload_digest(payload)
        request = cls(
            prompt_id,
            revision,
            Carrier.from_value(carrier),
            payload_digest_value,
            source_revision,
            _strict_lineage(lineage),
        )
        if isinstance(value, Mapping) and value.get("carrier_digest") is not None and value["carrier_digest"] != request.carrier.digest:
            raise DispatchCollisionError("carrier digest does not match descriptor")
        return request

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dispatch-request",
            "prompt_id": self.prompt_id,
            "revision": self.revision,
            "carrier": self.carrier.to_dict(),
            "carrier_digest": self.carrier.digest,
            "payload_digest": self.payload_digest,
        }
        if self.source_revision is not None:
            result["source_revision"] = self.source_revision
        if self.lineage:
            result["lineage"] = list(self.lineage)
        return result


@dataclass(frozen=True)
class DispatchRecord:
    prompt_id: str
    revision: int
    carrier_id: str
    carrier_digest: str
    payload_digest: str
    source_revision: str | None = None
    lineage: tuple[str, ...] = ()
    status: str = "recorded"

    def __post_init__(self) -> None:
        validate_prompt_id(self.prompt_id)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise DispatchError("revision must be a positive integer")
        if not isinstance(self.carrier_id, str) or not self.carrier_id.strip():
            raise DispatchError("carrier_id must be non-empty text")
        if not _is_digest(self.carrier_digest) or not _is_digest(self.payload_digest):
            raise DispatchError("dispatch record digests must be sha256")
        _strict_optional_text(self.source_revision, name="source_revision")
        if not isinstance(self.status, str) or not self.status.strip():
            raise DispatchError("status must be non-empty text")
        _strict_lineage(self.lineage)
        for item in self.lineage:
            validate_prompt_id(item)

    @property
    def key(self) -> tuple[str, int, str]:
        return self.prompt_id, self.revision, self.carrier_id

    @classmethod
    def from_request(cls, request: DispatchRequest) -> "DispatchRecord":
        return cls(
            request.prompt_id,
            request.revision,
            request.carrier.carrier_id,
            request.carrier.digest,
            request.payload_digest,
            request.source_revision,
            tuple(request.lineage),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DispatchRecord":
        allowed = {
            "schema_version", "kind", "prompt_id", "revision", "carrier_id", "carrier_digest",
            "payload_digest", "source_revision", "lineage", "status",
        }
        data = _strict_mapping(
            value,
            required={"schema_version", "kind", "prompt_id", "revision", "carrier_id", "carrier_digest", "payload_digest", "status"},
            allowed=allowed,
            name="dispatch record",
        )
        _strict_schema_version(data["schema_version"], name="dispatch record")
        _strict_kind(data["kind"], "dispatch-record", name="dispatch record")
        if not isinstance(data["prompt_id"], str):
            raise DispatchError("dispatch record prompt_id must be text")
        if isinstance(data["revision"], bool) or not isinstance(data["revision"], int):
            raise DispatchError("dispatch record revision must be integer")
        if not isinstance(data["carrier_id"], str):
            raise DispatchError("dispatch record carrier_id must be text")
        if not isinstance(data["status"], str):
            raise DispatchError("dispatch record status must be text")
        source_revision = data.get("source_revision")
        lineage = data.get("lineage", ())
        return cls(
            data["prompt_id"], data["revision"], data["carrier_id"], data["carrier_digest"],
            data["payload_digest"], source_revision, _strict_lineage(lineage), data["status"],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dispatch-record",
            "prompt_id": self.prompt_id,
            "revision": self.revision,
            "carrier_id": self.carrier_id,
            "carrier_digest": self.carrier_digest,
            "payload_digest": self.payload_digest,
            "status": self.status,
        }
        if self.source_revision is not None:
            result["source_revision"] = self.source_revision
        if self.lineage:
            result["lineage"] = list(self.lineage)
        return result


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    reason: str
    request: DispatchRequest
    conflict: DispatchRecord | None = None

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "allowed": self.allowed,
            "reason": self.reason,
            "request": self.request.to_dict(),
        }
        if self.conflict is not None:
            result["conflict"] = self.conflict.to_dict()
        return result


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdef" for char in value[7:]
    )


def _lineage_for(prompt: PromptRecord, catalog: PromptCatalog | None) -> tuple[str, ...]:
    values: set[str] = set(prompt.document.get("lineage", {}).get("supersedes", []))
    values.update(prompt.document.get("lineage", {}).get("corrects", []))
    values.update(prompt.document.get("lineage", {}).get("derived_from", []))
    if catalog is None:
        return tuple(sorted(values))
    pending = list(values)
    while pending:
        candidate = pending.pop()
        try:
            parent = catalog.lookup(candidate)
        except Exception:
            continue
        for nested in (
            parent.document.get("lineage", {}).get("supersedes", []),
            parent.document.get("lineage", {}).get("corrects", []),
            parent.document.get("lineage", {}).get("derived_from", []),
        ):
            for item in nested:
                if item not in values:
                    values.add(item)
                    pending.append(item)
    return tuple(sorted(values))


class DispatchLedger:
    """Deterministic, local duplicate-dispatch guard."""

    def __init__(self, records: Iterable[DispatchRecord | Mapping[str, Any]] = ()) -> None:
        self._records: dict[tuple[str, int, str], DispatchRecord] = {}
        for record in records:
            self._add_record(record)

    @property
    def records(self) -> tuple[DispatchRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def _add_record(self, record: DispatchRecord | Mapping[str, Any]) -> DispatchRecord:
        if not isinstance(record, DispatchRecord):
            record = DispatchRecord.from_dict(record)
        if record.key in self._records:
            raise DuplicateDispatchError("duplicate dispatch record")
        self._records[record.key] = record
        return record

    @staticmethod
    def _canonical_request(
        request: DispatchRequest,
        catalog: PromptCatalog | None,
    ) -> DispatchRequest:
        if catalog is None:
            return request
        resolved = catalog.lookup(request.prompt_id, request.revision)
        lineage = set(request.lineage)
        lineage.update(_lineage_for(resolved, catalog))
        return DispatchRequest(
            resolved.prompt_id, resolved.revision, request.carrier, request.payload_digest,
            request.source_revision, tuple(sorted(lineage)),
        )

    def check(
        self,
        request: DispatchRequest | Mapping[str, Any] | str,
        revision: int | None = None,
        carrier: Carrier | Mapping[str, Any] | str | None = None,
        payload: Any = None,
        *,
        payload_digest_value: str | None = None,
        source_revision: str | None = None,
        lineage: Iterable[str] = (),
        catalog: PromptCatalog | None = None,
    ) -> DispatchDecision:
        # Aliases are accepted only at this catalog-boundary input.  Resolve
        # them before DispatchRequest's strict qualified-ID validation so an
        # alias cannot become a stored identity or bypass canonical checks.
        if catalog is not None:
            if isinstance(request, str):
                try:
                    validate_prompt_id(request)
                except Exception:
                    request = catalog.lookup(request, revision).prompt_id
            elif isinstance(request, Mapping) and isinstance(request.get("prompt_id"), str):
                try:
                    validate_prompt_id(request["prompt_id"])
                except Exception:
                    resolved = catalog.lookup(request["prompt_id"], revision).prompt_id
                    request = {**request, "prompt_id": resolved}
        req = self._canonical_request(
            DispatchRequest.from_value(
                request, revision, carrier, payload,
                payload_digest_value=payload_digest_value,
                source_revision=source_revision,
                lineage=lineage,
            ),
            catalog,
        )
        exact = self._records.get(req.key)
        if exact is not None:
            if exact.payload_digest != req.payload_digest or exact.carrier_digest != req.carrier.digest:
                return DispatchDecision(False, "identity already recorded with different content", req, exact)
            return DispatchDecision(False, "duplicate dispatch for prompt revision and carrier", req, exact)
        related = set(req.lineage)
        for record in self.records:
            if record.carrier_id != req.carrier.carrier_id:
                continue
            if record.prompt_id in related or any(item in related for item in record.lineage):
                return DispatchDecision(False, "lineage already recorded for carrier", req, record)
        return DispatchDecision(True, "not previously recorded", req)

    def record(
        self,
        request: DispatchRequest | Mapping[str, Any] | str,
        revision: int | None = None,
        carrier: Carrier | Mapping[str, Any] | str | None = None,
        payload: Any = None,
        *,
        payload_digest_value: str | None = None,
        source_revision: str | None = None,
        lineage: Iterable[str] = (),
        catalog: PromptCatalog | None = None,
    ) -> DispatchRecord:
        decision = self.check(
            request, revision, carrier, payload,
            payload_digest_value=payload_digest_value,
            source_revision=source_revision,
            lineage=lineage,
            catalog=catalog,
        )
        if not decision:
            if decision.reason == "identity already recorded with different content":
                raise DispatchCollisionError(decision.reason)
            raise DuplicateDispatchError(decision.reason)
        return self._add_record(DispatchRecord.from_request(decision.request))

    def has_dispatched(self, *args: Any, **kwargs: Any) -> bool:
        return not bool(self.check(*args, **kwargs))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "dispatch-ledger", "records": [item.to_dict() for item in self.records]}

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "DispatchLedger":
        data = _strict_mapping(
            document,
            required={"schema_version", "kind", "records"},
            allowed={"schema_version", "kind", "records"},
            name="dispatch ledger",
        )
        _strict_schema_version(data["schema_version"], name="dispatch ledger")
        _strict_kind(data["kind"], "dispatch-ledger", name="dispatch ledger")
        if not isinstance(data["records"], list):
            raise DispatchError("dispatch ledger records must be an array")
        return cls(data["records"])


DispatchGuard = DispatchLedger


def check_dispatch(*args: Any, **kwargs: Any) -> DispatchDecision:
    """Convenience guard using a fresh ledger (for preflight-only checks)."""

    ledger = kwargs.pop("ledger", None) or DispatchLedger()
    return ledger.check(*args, **kwargs)


def record_dispatch(*args: Any, **kwargs: Any) -> DispatchRecord:
    ledger = kwargs.pop("ledger", None) or DispatchLedger()
    return ledger.record(*args, **kwargs)
