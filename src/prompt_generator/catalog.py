"""Validation, indexing, lookup, and search for canonical prompt catalogs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ContractError,
    canonical_json_bytes,
    contract_bytes,
    validate_contract,
)
from .identity import (
    IdentityError,
    canonical_workflow,
    qualify_prompt_id,
    split_prompt_id,
    validate_identifier,
    validate_local_id,
    validate_prompt_id,
    validate_project_id,
)


class CatalogError(ContractError):
    """Raised when a catalog cannot be indexed without ambiguity."""


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy_json(child) for child in value]
    return value


def _validate_prompt_invariants(document: Mapping[str, Any]) -> dict[str, Any]:
    validate_contract(document, "prompt")
    result = _copy_json(document)
    project_id, local_id = split_prompt_id(result["prompt_id"])
    if result["local_id"] != local_id:
        raise CatalogError("local_id must match the qualified prompt_id")
    if result["workflow"] != canonical_workflow(result["workflow"]):
        raise CatalogError("workflow aliases must be canonicalized before storage")
    if result["local_id"] != validate_local_id(result["local_id"]):
        raise CatalogError("invalid local prompt ID")
    validate_project_id(project_id)
    if result["aliases"]:
        if result["prompt_id"] in result["aliases"]:
            raise CatalogError("canonical prompt ID cannot also be an alias")
        for alias in result["aliases"]:
            validate_identifier(alias, field="prompt alias")
    binding_ids = [binding["binding_id"] for binding in result["repository_bindings"]]
    if len(binding_ids) != len(set(binding_ids)):
        raise CatalogError("duplicate repository binding IDs")
    for target in (
        result["lineage"].get("supersedes", [])
        + result["lineage"].get("corrects", [])
        + result["lineage"].get("derived_from", [])
    ):
        validate_prompt_id(target)
    gap_ids = [gap["gap_id"] for gap in result["intentional_gaps"]]
    if len(gap_ids) != len(set(gap_ids)):
        raise CatalogError("duplicate intentional gap IDs")
    return result


def validate_prompt(document: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_prompt_invariants(document)


def validate_catalog(document: Mapping[str, Any]) -> dict[str, Any]:
    validate_contract(document, "catalog")
    result = _copy_json(document)
    seen: set[tuple[str, int]] = set()
    prompt_ids: set[str] = set()
    aliases: dict[str, str] = {}
    for raw_prompt in result["prompts"]:
        prompt = _validate_prompt_invariants(raw_prompt)
        key = (prompt["prompt_id"], prompt["revision"])
        if key in seen:
            raise CatalogError(f"duplicate prompt revision: {key[0]} r{key[1]}")
        seen.add(key)
        prompt_ids.add(prompt["prompt_id"])
        for alias in prompt["aliases"]:
            existing = aliases.get(alias)
            if existing is not None and existing != prompt["prompt_id"]:
                raise CatalogError(f"alias collision: {alias}")
            aliases[alias] = prompt["prompt_id"]
    for alias, target in result.get("aliases", {}).items():
        validate_identifier(alias, field="catalog alias")
        validate_prompt_id(target)
        if target not in prompt_ids:
            raise CatalogError(f"alias targets unknown prompt: {target}")
        existing = aliases.get(alias)
        if existing is not None and existing != target:
            raise CatalogError(f"alias collision: {alias}")
        aliases[alias] = target
    result["prompts"] = [_validate_prompt_invariants(prompt) for prompt in result["prompts"]]
    return result


@dataclass(frozen=True)
class PromptRecord:
    """Immutable view over one validated prompt revision."""

    document: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "document", _validate_prompt_invariants(self.document))

    @property
    def prompt_id(self) -> str:
        return self.document["prompt_id"]

    @property
    def local_id(self) -> str:
        return self.document["local_id"]

    @property
    def project_id(self) -> str:
        return split_prompt_id(self.prompt_id)[0]

    @property
    def revision(self) -> int:
        return self.document["revision"]

    @property
    def workflow(self) -> str:
        return self.document["workflow"]

    @property
    def title(self) -> str:
        return self.document["title"]

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(self.document["aliases"])

    def to_dict(self) -> dict[str, Any]:
        return _copy_json(self.document)

    def to_bytes(self) -> bytes:
        return contract_bytes(self.document, "prompt")

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PromptRecord":
        return cls(document)


class PromptCatalog:
    """Deterministic catalog index keyed by qualified ID and revision."""

    def __init__(self, records: Iterable[PromptRecord | Mapping[str, Any]] = (), *, catalog_id: str | None = None, generator_version: str | None = None) -> None:
        if catalog_id is not None:
            validate_identifier(catalog_id, field="catalog ID")
        self.catalog_id = catalog_id
        self.generator_version = generator_version
        self._records: dict[tuple[str, int], PromptRecord] = {}
        self._aliases: dict[str, str] = {}
        for record in records:
            self.add(record)

    @property
    def records(self) -> tuple[PromptRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    @property
    def aliases(self) -> dict[str, str]:
        return dict(sorted(self._aliases.items()))

    def add(self, record: PromptRecord | Mapping[str, Any]) -> PromptRecord:
        item = record if isinstance(record, PromptRecord) else PromptRecord(record)
        key = (item.prompt_id, item.revision)
        if key in self._records:
            raise CatalogError(f"duplicate prompt revision: {item.prompt_id} r{item.revision}")
        for alias in item.aliases:
            self._add_alias(alias, item.prompt_id)
        self._records[key] = item
        return item

    def add_prompt(self, **fields: Any) -> PromptRecord:
        return self.add(fields)

    def add_alias(self, alias: str, prompt_id: str) -> None:
        validate_identifier(alias, field="catalog alias")
        validate_prompt_id(prompt_id)
        if (prompt_id, 1) not in self._records and not any(key[0] == prompt_id for key in self._records):
            raise CatalogError(f"cannot alias unknown prompt: {prompt_id}")
        self._add_alias(alias, prompt_id)

    def _add_alias(self, alias: str, prompt_id: str) -> None:
        existing = self._aliases.get(alias)
        if existing is not None and existing != prompt_id:
            raise CatalogError(f"alias collision: {alias}")
        self._aliases[alias] = prompt_id

    def lookup(self, prompt_id: str, revision: int | None = None) -> PromptRecord:
        target = self._aliases.get(prompt_id, prompt_id)
        if target != prompt_id and revision is None:
            revision = max(key[1] for key in self._records if key[0] == target)
        validate_prompt_id(target)
        if revision is None:
            revisions = [key[1] for key in self._records if key[0] == target]
            if not revisions:
                raise CatalogError(f"unknown prompt ID: {target}")
            revision = max(revisions)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise CatalogError("revision must be a positive integer")
        try:
            return self._records[(target, revision)]
        except KeyError as exc:
            raise CatalogError(f"unknown prompt revision: {target} r{revision}") from exc

    def lookup_local(self, project_id: str, local_id: str, revision: int | None = None) -> PromptRecord:
        return self.lookup(qualify_prompt_id(project_id, local_id), revision)

    def search(self, query: str = "", *, workflow: str | None = None, project_id: str | None = None) -> tuple[PromptRecord, ...]:
        if not isinstance(query, str):
            raise CatalogError("search query must be text")
        normalized_query = query.casefold().strip()
        normalized_workflow = canonical_workflow(workflow) if workflow is not None else None
        if project_id is not None:
            validate_project_id(project_id)
        results: list[PromptRecord] = []
        for record in self.records:
            if normalized_workflow is not None and record.workflow != normalized_workflow:
                continue
            if project_id is not None and record.project_id != project_id:
                continue
            haystack = " ".join(
                [
                    record.prompt_id,
                    record.local_id,
                    record.workflow,
                    record.title,
                    record.document["outcome"]["summary"],
                    *record.document["aliases"],
                    *record.document["scope"]["included"],
                    *record.document["scope"]["excluded"],
                ]
            ).casefold()
            if normalized_query and normalized_query not in haystack:
                continue
            results.append(record)
        return tuple(sorted(results, key=lambda item: (item.prompt_id, item.revision)))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "catalog",
            "prompts": [record.to_dict() for record in self.records],
        }
        if self.catalog_id is not None:
            result["catalog_id"] = self.catalog_id
        if self.generator_version is not None:
            result["generator_version"] = self.generator_version
        if self._aliases:
            result["aliases"] = self.aliases
        return validate_catalog(result)

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PromptCatalog":
        normalized = validate_catalog(document)
        catalog = cls(catalog_id=normalized.get("catalog_id"), generator_version=normalized.get("generator_version"))
        for prompt in normalized["prompts"]:
            catalog.add(prompt)
        for alias, target in normalized.get("aliases", {}).items():
            catalog._add_alias(alias, target)
        return catalog


Catalog = PromptCatalog
CatalogEntry = PromptRecord


def catalog_from_dict(document: Mapping[str, Any]) -> PromptCatalog:
    return PromptCatalog.from_dict(document)


def catalog_bytes(document: Mapping[str, Any]) -> bytes:
    return PromptCatalog.from_dict(document).to_bytes()
