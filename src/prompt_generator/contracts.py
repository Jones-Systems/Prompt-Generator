"""Public, versioned data contracts for Prompt Generator.

The module deliberately uses the standard library only.  JSON Schema files in
``schemas/`` are the interchange contract; the small validator here enforces
the same strict subset needed at the public boundary.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
CANONICAL_JSON_VERSION = 1
PRIVATE_REFERENCE_SCHEMA_VERSION = 1

# Keep this list local to the contracts module rather than importing identity.py:
# identity.py imports this module to use the shared identifier expressions.
# External prompt.schema.json carries the same closed vocabulary.
CANONICAL_WORKFLOWS: tuple[str, ...] = (
    "research",
    "planning",
    "implementation",
    "verification",
    "review",
    "integration",
    "conflict-resolution",
    "correction",
    "resume",
    "migration",
    "handoff",
)


class ContractError(ValueError):
    """Raised when a public contract is malformed or unsupported."""


class DuplicateKeyError(ContractError):
    """Raised when a JSON object contains a key more than once."""


class UnsupportedVersionError(ContractError):
    """Raised when a contract version is not understood by this library."""


class CanonicalJSONError(ContractError):
    """Raised when bytes do not satisfy canonical JSON v1."""


class SchemaValidationError(ContractError):
    """Raised when a document does not satisfy its strict JSON Schema."""


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = OrderedDict()
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise CanonicalJSONError(f"non-finite JSON number is not supported: {value}")


def _check_json_value(value: Any, path: str = "$", *, allow_bytes: bool = False) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, int) and not isinstance(value, bool):
            if value < -(2**53 - 1) or value > 2**53 - 1:
                raise CanonicalJSONError(f"integer outside interoperable range at {path}")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJSONError(f"non-finite number at {path}")
        raise CanonicalJSONError(f"fractional number is not supported at {path}")
    if allow_bytes and isinstance(value, bytes):
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(f"JSON object key is not a string at {path}")
            _check_json_value(child, f"{path}.{key}", allow_bytes=allow_bytes)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _check_json_value(child, f"{path}[{index}]", allow_bytes=allow_bytes)
        return
    raise CanonicalJSONError(f"unsupported JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical JSON v1 bytes, including exactly one final LF."""

    _check_json_value(value)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result = (rendered + "\n").encode("utf-8", "strict")
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise CanonicalJSONError(str(exc)) from exc
    if result.count(b"\n") != 1 or not result.endswith(b"\n"):
        raise CanonicalJSONError("canonical JSON must contain exactly one final LF")
    return result


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def parse_json_bytes(data: bytes | bytearray | str, *, canonical: bool = False) -> Any:
    """Parse JSON with duplicate-key, BOM, number, and UTF-8 rejection."""

    if isinstance(data, str):
        raw = data.encode("utf-8", "strict")
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raise CanonicalJSONError("JSON input must be text or bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise CanonicalJSONError("UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CanonicalJSONError("JSON must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_pairs,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CanonicalJSONError(f"invalid JSON: {exc}") from exc
    _check_json_value(value)
    if canonical:
        if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
            raise CanonicalJSONError("canonical JSON requires exactly one final LF")
        if canonical_json_bytes(value) != raw:
            raise CanonicalJSONError("JSON bytes are not canonical JSON v1")
    return value


def canonical_json_loads(data: bytes | bytearray | str) -> Any:
    return parse_json_bytes(data, canonical=True)


def require_schema_version(document: Mapping[str, Any], expected: int = SCHEMA_VERSION) -> None:
    if not isinstance(document, Mapping):
        raise SchemaValidationError("contract root must be an object")
    value = document.get("schema_version")
    if value != expected or isinstance(value, bool):
        raise UnsupportedVersionError(
            f"unsupported schema_version {value!r}; expected {expected}"
        )


def _type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, False)


def _validate_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    expected_types = schema.get("type")
    if expected_types is not None:
        types = expected_types if isinstance(expected_types, list) else [expected_types]
        if not any(_type_matches(value, type_name) for type_name in types):
            raise SchemaValidationError(f"{path}: expected {expected_types}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: value does not match const")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: string is too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise SchemaValidationError(f"{path}: string does not match pattern")
        if schema.get("format") == "date-time" and not _looks_like_datetime(value):
            raise SchemaValidationError(f"{path}: invalid date-time")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path}: integer is below minimum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: array has too many items")
        if "uniqueItems" in schema and schema["uniqueItems"]:
            fingerprints = [canonical_json_bytes(item) for item in value]
            if len(set(fingerprints)) != len(fingerprints):
                raise SchemaValidationError(f"{path}: array items are not unique")
        if "items" in schema:
            for index, child in enumerate(value):
                _validate_schema(child, schema["items"], f"{path}[{index}]")
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise SchemaValidationError(f"{path}: missing required field {required!r}")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise SchemaValidationError(
                    f"{path}: unknown field(s): {', '.join(sorted(unknown))}"
                )
        for key, child in value.items():
            if key in properties:
                _validate_schema(child, properties[key], f"{path}.{key}")
    if "oneOf" in schema:
        matches = 0
        for candidate in schema["oneOf"]:
            try:
                _validate_schema(value, candidate, path)
            except SchemaValidationError:
                continue
            matches += 1
        if matches != 1:
            raise SchemaValidationError(f"{path}: expected exactly one schema alternative")


def validate_document(document: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise SchemaValidationError("contract root must be an object")
    require_schema_version(document)
    _validate_schema(document, schema)
    return dict(document)


def _looks_like_datetime(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value))


IDENTIFIER_PATTERN = r"[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*"
PROJECT_ID_PATTERN = r"prj_[0-9a-f]{32}"
QUALIFIED_PROMPT_ID_PATTERN = rf"{PROJECT_ID_PATTERN}:{IDENTIFIER_PATTERN}"
OPAQUE_REFERENCE_PATTERN = r"ref_[A-Za-z0-9][A-Za-z0-9_-]{7,127}"


_TEXT = {"type": "string", "minLength": 1}
_ID = {"type": "string", "pattern": IDENTIFIER_PATTERN}
_PROJECT_ID = {"type": "string", "pattern": PROJECT_ID_PATTERN}
_PROMPT_ID = {"type": "string", "pattern": QUALIFIED_PROMPT_ID_PATTERN}


def _strict_object(
    properties: Mapping[str, Mapping[str, Any]],
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


_REPOSITORY_BINDING = _strict_object(
    {
        "binding_id": _ID,
        "repository": {"type": "string", "pattern": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"},
        "root": {"type": "string", "minLength": 1},
        "branch": {"type": "string", "pattern": r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}"},
        "base": {"type": "string", "minLength": 1},
        "head": {"type": "string", "minLength": 1},
        "delivery": {"type": "string", "enum": ["task-branch", "draft-pr", "local"]},
        "workspace": {"type": "string", "minLength": 1},
    },
    ("binding_id", "repository", "branch", "delivery"),
)


_OUTCOME = _strict_object({"summary": _TEXT, "success_criteria": {"type": "array", "items": _TEXT, "minItems": 1}}, ("summary", "success_criteria"))
_SCOPE = _strict_object({"included": {"type": "array", "items": _TEXT}, "excluded": {"type": "array", "items": _TEXT}}, ("included", "excluded"))
_AUTHORITY = _strict_object({"required": {"type": "array", "items": _TEXT}, "forbidden": {"type": "array", "items": _TEXT}}, ("required", "forbidden"))
_FRESHNESS = _strict_object({"source_revision": _TEXT, "checked_at": {"type": "string", "format": "date-time"}, "expires_at": {"type": "string", "format": "date-time"}}, ("source_revision", "checked_at"))
_DEPENDENCIES = _strict_object({"requires": {"type": "array", "items": _TEXT}, "gates": {"type": "array", "items": _TEXT}}, ("requires", "gates"))
_OWNERSHIP = _strict_object({"accountable": _TEXT, "writer_scope": {"type": "array", "items": _TEXT, "minItems": 1}, "reviewers": {"type": "array", "items": _TEXT}}, ("accountable", "writer_scope"))
_OUTPUTS = _strict_object({"artifacts": {"type": "array", "items": _TEXT}, "formats": {"type": "array", "items": _TEXT}}, ("artifacts", "formats"))
_VERIFICATION = _strict_object({"checks": {"type": "array", "items": _TEXT}, "evidence": {"type": "array", "items": _TEXT}}, ("checks",))
_CONTINUITY = _strict_object({"checkpoint": _TEXT, "recovery": _TEXT, "invalidation": _TEXT}, ("checkpoint", "recovery"))
_FINISH_LINE = _strict_object({"criteria": {"type": "array", "items": _TEXT, "minItems": 1}, "owner": _TEXT}, ("criteria", "owner"))
_REPORTING = _strict_object({"fields": {"type": "array", "items": _TEXT}, "channel": _TEXT}, ("fields",))
_LINEAGE = _strict_object({"supersedes": {"type": "array", "items": _PROMPT_ID}, "corrects": {"type": "array", "items": _PROMPT_ID}, "derived_from": {"type": "array", "items": _PROMPT_ID}, "reason": _TEXT})
_GAP = _strict_object({"gap_id": _ID, "reason": _TEXT, "owner": _TEXT}, ("gap_id", "reason"))
_PROVENANCE = _strict_object({"source": _TEXT, "source_revision": _TEXT, "imported": {"type": "boolean"}, "license": _TEXT, "notes": _TEXT}, ("source", "source_revision", "imported"))


PROMPT_SCHEMA: dict[str, Any] = _strict_object(
    {
        "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
        "kind": {"type": "string", "const": "prompt"},
        "prompt_id": _PROMPT_ID,
        "local_id": _ID,
        "revision": {"type": "integer", "minimum": 1},
        "workflow": {"type": "string", "enum": list(CANONICAL_WORKFLOWS)},
        "title": _TEXT,
        "outcome": _OUTCOME,
        "scope": _SCOPE,
        "authority": _AUTHORITY,
        "freshness": _FRESHNESS,
        "dependencies": _DEPENDENCIES,
        "ownership": _OWNERSHIP,
        "outputs": _OUTPUTS,
        "verification": _VERIFICATION,
        "continuity": _CONTINUITY,
        "finish_line": _FINISH_LINE,
        "reporting": _REPORTING,
        "lineage": _LINEAGE,
        "aliases": {"type": "array", "items": _ID, "uniqueItems": True},
        "intentional_gaps": {"type": "array", "items": _GAP, "uniqueItems": True},
        "repository_bindings": {"type": "array", "items": _REPOSITORY_BINDING, "uniqueItems": True},
        "provenance": _PROVENANCE,
    },
    (
        "schema_version", "kind", "prompt_id", "local_id", "revision", "workflow", "title",
        "outcome", "scope", "authority", "freshness", "dependencies", "ownership", "outputs",
        "verification", "continuity", "finish_line", "reporting", "lineage", "aliases",
        "intentional_gaps", "repository_bindings", "provenance",
    ),
)


CATALOG_SCHEMA: dict[str, Any] = _strict_object(
    {
        "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
        "kind": {"type": "string", "const": "catalog"},
        "catalog_id": _ID,
        "generator_version": _TEXT,
        "prompts": {"type": "array", "items": PROMPT_SCHEMA, "uniqueItems": True},
        "aliases": {"type": "object", "additionalProperties": {"type": "string", "pattern": QUALIFIED_PROMPT_ID_PATTERN}},
    },
    ("schema_version", "kind", "prompts"),
)


PRIVATE_REFERENCE_REQUEST_SCHEMA: dict[str, Any] = _strict_object(
    {
        "schema_version": {"type": "integer", "const": PRIVATE_REFERENCE_SCHEMA_VERSION},
        "kind": {"type": "string", "const": "private-reference-request"},
        "reference": {"type": "string", "pattern": OPAQUE_REFERENCE_PATTERN},
        "purpose": _TEXT,
        "scopes": {"type": "array", "items": _ID, "minItems": 1, "uniqueItems": True},
    },
    ("schema_version", "kind", "reference", "purpose", "scopes"),
)


PRIVATE_REFERENCE_RESULT_SCHEMA: dict[str, Any] = _strict_object(
    {
        "schema_version": {"type": "integer", "const": PRIVATE_REFERENCE_SCHEMA_VERSION},
        "kind": {"type": "string", "const": "private-reference-result"},
        "status": {"type": "string", "enum": ["resolved", "denied"]},
        "reference": {"type": "string", "pattern": OPAQUE_REFERENCE_PATTERN},
        "purpose": _TEXT,
        "scopes": {"type": "array", "items": _ID, "minItems": 1, "uniqueItems": True},
        "schema": _TEXT,
        "payload_digest": {"type": "string", "pattern": r"sha256:[0-9a-f]{64}"},
        "expires_at": {"type": "string", "format": "date-time"},
    },
    ("schema_version", "kind", "status", "reference", "purpose", "scopes"),
)


PRIVATE_REFERENCE_ERROR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "schema_version": {"type": "integer", "const": PRIVATE_REFERENCE_SCHEMA_VERSION},
        "kind": {"type": "string", "const": "private-reference-error"},
        "code": {"type": "string", "enum": ["denied", "unknown-reference", "revoked", "scope-denied", "schema-mismatch", "expired", "internal"]},
        "message": _TEXT,
        "reference": {"type": "string", "pattern": OPAQUE_REFERENCE_PATTERN},
        "retryable": {"type": "boolean"},
    },
    ("schema_version", "kind", "code", "message", "retryable"),
)


SCHEMAS: dict[str, dict[str, Any]] = {
    "prompt": PROMPT_SCHEMA,
    "catalog": CATALOG_SCHEMA,
    "private-reference-request": PRIVATE_REFERENCE_REQUEST_SCHEMA,
    "private-reference-result": PRIVATE_REFERENCE_RESULT_SCHEMA,
    "private-reference-error": PRIVATE_REFERENCE_ERROR_SCHEMA,
}


# The package ships the same public schemas that are published in the
# repository's top-level ``schemas/`` directory.  Keep the resource names
# explicit so callers never have to derive a path from untrusted input.
SCHEMA_RESOURCE_FILES: dict[str, str] = {
    "catalog": "catalog.schema.json",
    "identity-registry": "identity-registry.schema.json",
    "migration-report": "migration-report.schema.json",
    "private-reference": "private-reference.schema.json",
    "prompt": "prompt.schema.json",
    "review": "review.schema.json",
}

_SCHEMA_RESOURCE_ALIASES = {
    "catalog-v1": "catalog",
    "identity-registry-v1": "identity-registry",
    "migration-report-v1": "migration-report",
    "private-reference-v1": "private-reference",
    "private-reference-request": "private-reference",
    "private-reference-request-v1": "private-reference",
    "private-reference-result": "private-reference",
    "private-reference-result-v1": "private-reference",
    "private-reference-error": "private-reference",
    "private-reference-error-v1": "private-reference",
    "prompt-v1": "prompt",
    "review-v1": "review",
}


def schema_resource(kind: str):
    """Return the installed package resource for one public schema.

    ``kind`` is a contract/schema kind, not a filesystem path.  The returned
    object is an ``importlib.resources`` traversable and supports ``is_file``
    and ``read_bytes`` for both source-tree and installed distributions.
    """

    if not isinstance(kind, str):
        raise ContractError("schema kind must be text")
    resource_name = _SCHEMA_RESOURCE_ALIASES.get(kind, kind)
    try:
        filename = SCHEMA_RESOURCE_FILES[resource_name]
    except KeyError as exc:
        raise ContractError(f"unsupported schema resource: {kind}") from exc
    resource = resources.files("prompt_generator.schemas").joinpath(filename)
    if not resource.is_file():
        raise ContractError(f"schema resource is not installed: {filename}")
    return resource


def schema_bytes(kind: str) -> bytes:
    """Read one installed public schema exactly as shipped."""

    return schema_resource(kind).read_bytes()


def schema_digest(kind: str) -> str:
    """Return the SHA-256 digest of one installed schema's exact bytes."""

    return "sha256:" + hashlib.sha256(schema_bytes(kind)).hexdigest()


def schema_digests() -> dict[str, str]:
    """Return deterministic SHA-256 labels for every shipped schema."""

    return {kind: schema_digest(kind) for kind in sorted(SCHEMA_RESOURCE_FILES)}


def schema_manifest() -> dict[str, Any]:
    """Describe the versioned schema resources and their exact byte digests."""

    entries: dict[str, dict[str, Any]] = {}
    for kind in sorted(SCHEMA_RESOURCE_FILES):
        data = schema_bytes(kind)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        entries[kind] = {
            "resource": f"schemas/{SCHEMA_RESOURCE_FILES[kind]}",
            "bytes": len(data),
            "digest": digest,
            "sha256": digest,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "schema-manifest",
        "schemas": entries,
    }


def schema_for(kind: str) -> dict[str, Any]:
    try:
        return SCHEMAS[kind]
    except KeyError as exc:
        raise ContractError(f"unsupported contract kind: {kind}") from exc


def validate_contract(document: Mapping[str, Any], kind: str | None = None) -> dict[str, Any]:
    if kind is None:
        kind = document.get("kind") if isinstance(document, Mapping) else None
    if not isinstance(kind, str):
        raise SchemaValidationError("contract kind is required")
    schema = schema_for(kind)
    validate_document(document, schema)
    return dict(document)


def contract_bytes(document: Mapping[str, Any], kind: str | None = None) -> bytes:
    validate_contract(document, kind)
    return canonical_json_bytes(document)


@dataclass(frozen=True)
class PrivateReferenceRequest:
    reference: str
    purpose: str
    scopes: tuple[str, ...]
    schema_version: int = PRIVATE_REFERENCE_SCHEMA_VERSION

    kind: str = "private-reference-request"

    def __post_init__(self) -> None:
        validate_contract(self.to_dict(), self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "reference": self.reference,
            "purpose": self.purpose,
            "scopes": list(self.scopes),
        }

    def to_bytes(self) -> bytes:
        return contract_bytes(self.to_dict(), self.kind)

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PrivateReferenceRequest":
        validate_contract(document, "private-reference-request")
        return cls(document["reference"], document["purpose"], tuple(document["scopes"]))


@dataclass(frozen=True)
class PrivateReferenceResult:
    status: str
    reference: str
    purpose: str
    scopes: tuple[str, ...]
    schema: str | None = None
    payload_digest: str | None = None
    expires_at: str | None = None
    schema_version: int = PRIVATE_REFERENCE_SCHEMA_VERSION

    kind: str = "private-reference-result"

    def __post_init__(self) -> None:
        validate_contract(self.to_dict(), self.kind)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "status": self.status,
            "reference": self.reference,
            "purpose": self.purpose,
            "scopes": list(self.scopes),
        }
        if self.schema is not None:
            result["schema"] = self.schema
        if self.payload_digest is not None:
            result["payload_digest"] = self.payload_digest
        if self.expires_at is not None:
            result["expires_at"] = self.expires_at
        return result

    def to_bytes(self) -> bytes:
        return contract_bytes(self.to_dict(), self.kind)

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PrivateReferenceResult":
        validate_contract(document, "private-reference-result")
        return cls(
            document["status"], document["reference"], document["purpose"],
            tuple(document["scopes"]), document.get("schema"),
            document.get("payload_digest"), document.get("expires_at"),
        )


@dataclass(frozen=True)
class PrivateReferenceError:
    code: str
    message: str
    retryable: bool
    reference: str | None = None
    schema_version: int = PRIVATE_REFERENCE_SCHEMA_VERSION

    kind: str = "private-reference-error"

    def __post_init__(self) -> None:
        validate_contract(self.to_dict(), self.kind)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.reference is not None:
            result["reference"] = self.reference
        return result

    def to_bytes(self) -> bytes:
        return contract_bytes(self.to_dict(), self.kind)

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PrivateReferenceError":
        validate_contract(document, "private-reference-error")
        return cls(
            document["code"], document["message"], document["retryable"], document.get("reference")
        )


def parse_contract(data: bytes | bytearray | str, kind: str | None = None) -> dict[str, Any]:
    document = parse_json_bytes(data, canonical=True)
    if not isinstance(document, Mapping):
        raise SchemaValidationError("contract root must be an object")
    return validate_contract(document, kind)
