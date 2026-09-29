"""Deterministic public command line boundary for Prompt Generator.

Every command consumes and produces inert, versioned data.  This module never
opens a provider connection, imports prompt text as code, or resolves private
references.  Writes are limited to the explicitly named local output, ledger,
plan, or migration destination.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .catalog import PromptCatalog, validate_prompt
from .contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_json_loads,
    parse_json_bytes,
)
from .dispatch import DispatchLedger, DispatchRecord, DispatchRequest
from .generation import GenerationStore, parse_prompt_markdown
from .integrity import atomic_write_bytes, sha256_digest
from .identity import validate_prompt_id
from .migration import (
    MigrationItem,
    MigrationProvenance,
    MigrationReport,
    copy_then_verify,
    dry_run_import,
)
from .modes import validate_mode
from .reviews import ReviewLedger


class CLIError(ValueError):
    """Raised for a bounded command-line input or output error."""


_JSON_KINDS = {
    "catalog",
    "dispatch-ledger",
    "dispatch-record",
    "dispatch-request",
    "generation-index",
    "generation-manifest",
    "migration-report",
    "mode-request",
    "prompt",
    "review-ledger",
}

_GENERATION_METADATA_FIELDS = {
    "schema_version", "kind", "generator_version", "generation_id",
    "catalog_digest", "files", "prompts",
}
_GENERATION_FILE_FIELDS = {
    "path", "kind", "payload_bytes", "payload_sha256",
    "whole_file_bytes", "whole_file_sha256", "prompt_id", "revision",
}
_GENERATION_PROMPT_FIELDS = {
    "prompt_id", "revision", "path", "payload_bytes", "payload_sha256",
    "whole_file_bytes", "whole_file_sha256",
}
_UNSET = object()


@contextmanager
def _dispatch_ledger_lock(path: str):
    """Serialize one complete dispatch ledger load/check/write operation."""

    if path == "-":
        raise CLIError("dispatch ledger must be a local path")
    target = Path(path)
    parent = target.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise CLIError(f"dispatch ledger parent is not a real directory: {parent}")
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    if lock_path.is_symlink():
        raise CLIError(f"dispatch ledger lock is a symlink: {lock_path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except FileExistsError as exc:
        raise CLIError(f"dispatch ledger is busy: {path}") from exc
    except OSError as exc:
        raise CLIError(f"cannot lock dispatch ledger: {path}") from exc
    try:
        os.write(fd, b"prompt-generator dispatch ledger lock\n")
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
def _read_bytes(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    target = Path(path)
    if target.is_symlink() or not target.exists() or not target.is_file():
        raise CLIError(f"input is not a safe regular file: {path}")
    try:
        return target.read_bytes()
    except OSError as exc:
        raise CLIError(f"cannot read input: {path}") from exc


def _read_json(path: str) -> Any:
    try:
        return canonical_json_loads(_read_bytes(path))
    except ContractError as exc:
        raise CLIError(f"input is not canonical JSON: {path}: {exc}") from exc


def _write_bytes(path: str, data: bytes, *, expected: bytes | None | object = _UNSET) -> None:
    if path == "-":
        raise CLIError("a local output path is required; refusing to write binary data to stdout")
    target = Path(path)
    if target.is_symlink():
        raise CLIError(f"output is a symlink: {path}")
    parent = target.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise CLIError(f"output parent is not a real directory: {parent}")
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    if expected is not _UNSET:
        if target.is_symlink():
            raise CLIError(f"output is a symlink: {path}")
        current = target.read_bytes() if target.exists() else None
        if current != expected:
            raise CLIError(f"output changed during dispatch operation: {path}")
    try:
        atomic_write_bytes(target, data, replace=target.exists(), mode=0o644)
    except OSError as exc:
        raise CLIError(f"cannot write output: {path}") from exc


def _emit(value: Any) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value))


def _digest(value: Any) -> str:
    return sha256_digest(canonical_json_bytes(value))


def _strict_mapping(value: Any, *, name: str, required: set[str], allowed: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CLIError(f"{name} must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - allowed
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise CLIError(f"{name} has invalid fields ({'; '.join(details)})")
    return value


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(char in "0123456789abcdef" for char in value[7:])


def _strict_positive_integer(value: Any, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CLIError(f"{name} must be a positive integer")


def _validate_generation_metadata(value: Any, *, expected_kind: str | None = None) -> dict[str, Any]:
    document = _strict_mapping(
        value,
        name="generation metadata",
        required=_GENERATION_METADATA_FIELDS,
        allowed=_GENERATION_METADATA_FIELDS,
    )
    if document["schema_version"] != 1 or not isinstance(document["schema_version"], int) or isinstance(document["schema_version"], bool):
        raise CLIError("unsupported generation metadata version")
    kind = document["kind"]
    if kind not in {"generation-manifest", "generation-index"} or (expected_kind is not None and kind != expected_kind):
        raise CLIError(f"unsupported generation metadata kind: {kind!r}")
    if not isinstance(document["generator_version"], str) or document["generator_version"] != "prompt-generator/1":
        raise CLIError("unsupported generation metadata generator version")
    generation_id = document["generation_id"]
    if not isinstance(generation_id, str) or len(generation_id) != 64 or any(char not in "0123456789abcdef" for char in generation_id):
        raise CLIError("generation metadata generation_id must be lowercase hexadecimal")
    if not _valid_digest(document["catalog_digest"]):
        raise CLIError("generation metadata catalog_digest must be sha256")
    files = document["files"]
    prompts = document["prompts"]
    if not isinstance(files, list) or not files:
        raise CLIError("generation metadata files must be a non-empty array")
    if not isinstance(prompts, list):
        raise CLIError("generation metadata prompts must be an array")
    expected_prompt_paths: set[str] = set()
    seen_file_paths: set[str] = set()
    file_entries: dict[str, Mapping[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, Mapping):
            raise CLIError("generation metadata file entries must be objects")
        entry_kind = entry.get("kind")
        if entry_kind == "catalog":
            required = {"path", "kind", "payload_bytes", "payload_sha256", "whole_file_bytes", "whole_file_sha256"}
        elif entry_kind == "prompt":
            required = _GENERATION_FILE_FIELDS
        else:
            raise CLIError(f"unsupported generation metadata file kind: {entry_kind!r}")
        _strict_mapping(entry, name="generation metadata file", required=required, allowed=_GENERATION_FILE_FIELDS)
        if not isinstance(entry["path"], str) or not entry["path"] or entry["path"].startswith("/") or ".." in Path(entry["path"]).parts:
            raise CLIError("generation metadata file path is unsafe")
        if entry["path"] in seen_file_paths:
            raise CLIError(f"duplicate generation metadata file path: {entry['path']}")
        seen_file_paths.add(entry["path"])
        file_entries[entry["path"]] = entry
        for field in ("payload_bytes", "whole_file_bytes"):
            if isinstance(entry[field], bool) or not isinstance(entry[field], int) or entry[field] < 0:
                raise CLIError(f"generation metadata {field} must be a non-negative integer")
        for field in ("payload_sha256", "whole_file_sha256"):
            if not _valid_digest(entry[field]):
                raise CLIError(f"generation metadata {field} must be sha256")
        if entry_kind == "catalog":
            if entry["path"] != "catalog.json":
                raise CLIError("generation metadata catalog path is invalid")
        else:
            if not isinstance(entry["prompt_id"], str):
                raise CLIError("generation metadata prompt_id must be text")
            try:
                validate_prompt_id(entry["prompt_id"])
            except ValueError as exc:
                raise CLIError("generation metadata prompt_id is invalid") from exc
            _strict_positive_integer(entry["revision"], name="generation metadata revision")
            expected_prompt_paths.add(entry["path"])
    seen_prompt_paths: set[str] = set()
    for entry in prompts:
        _strict_mapping(entry, name="generation metadata prompt", required=_GENERATION_PROMPT_FIELDS, allowed=_GENERATION_PROMPT_FIELDS)
        if not isinstance(entry["prompt_id"], str):
            raise CLIError("generation metadata prompt_id must be text")
        try:
            validate_prompt_id(entry["prompt_id"])
        except ValueError as exc:
            raise CLIError("generation metadata prompt_id is invalid") from exc
        _strict_positive_integer(entry["revision"], name="generation metadata revision")
        if not isinstance(entry["path"], str) or not entry["path"]:
            raise CLIError("generation metadata prompt path must be non-empty text")
        if entry["path"].startswith("/") or ".." in Path(entry["path"]).parts:
            raise CLIError("generation metadata prompt path is unsafe")
        if entry["path"] in seen_prompt_paths:
            raise CLIError(f"duplicate generation metadata prompt path: {entry['path']}")
        seen_prompt_paths.add(entry["path"])
        file_entry = file_entries.get(entry["path"])
        if file_entry is None or file_entry.get("kind") != "prompt":
            raise CLIError("generation metadata prompt index references an unknown file")
        for field in ("prompt_id", "revision", "payload_bytes", "payload_sha256", "whole_file_bytes", "whole_file_sha256"):
            if file_entry.get(field) != entry[field]:
                raise CLIError("generation metadata prompt index does not match its file record")
        for field in ("payload_bytes", "whole_file_bytes"):
            if isinstance(entry[field], bool) or not isinstance(entry[field], int) or entry[field] < 0:
                raise CLIError(f"generation metadata {field} must be a non-negative integer")
        for field in ("payload_sha256", "whole_file_sha256"):
            if not _valid_digest(entry[field]):
                raise CLIError(f"generation metadata {field} must be sha256")
    if seen_prompt_paths != expected_prompt_paths:
        raise CLIError("generation metadata prompt index does not match file records")
    if sum(1 for entry in files if entry["kind"] == "catalog") != 1:
        raise CLIError("generation metadata must contain exactly one catalog file")
    return dict(document)


def _validate_review_ledger(value: Any) -> dict[str, Any]:
    try:
        return ReviewLedger.from_dict(value).to_dict()
    except (ContractError, ValueError, KeyError) as exc:
        raise CLIError(f"invalid review ledger: {exc}") from exc


def _validate_migration_report(value: Any) -> dict[str, Any]:
    document = _strict_mapping(
        value, name="migration report",
        required={"schema_version", "kind", "source_root", "destination_root", "dry_run", "source_preserved", "verified", "ok", "items", "collisions"},
        allowed={"schema_version", "kind", "source_root", "destination_root", "dry_run", "source_preserved", "verified", "ok", "items", "collisions", "provenance"},
    )
    if isinstance(document["schema_version"], bool) or not isinstance(document["schema_version"], int) or document["schema_version"] != 1 or document["kind"] != "migration-report":
        raise CLIError("unsupported migration report version")
    if not all(isinstance(document[name], list) for name in ("items", "collisions")):
        raise CLIError("migration report items and collisions must be arrays")
    item_required = {"source", "target", "size", "source_digest", "action"}
    item_allowed = item_required | {"identity", "revision", "collision", "error", "provenance"}
    for field in ("source_root", "destination_root"):
        if not isinstance(document[field], str) or not document[field]:
            raise CLIError(f"migration report {field} must be non-empty text")
    for field in ("dry_run", "source_preserved", "verified", "ok"):
        if not isinstance(document[field], bool):
            raise CLIError(f"migration report {field} must be boolean")
    for item in document["items"] + document["collisions"]:
        item_value = _strict_mapping(item, name="migration item", required=item_required, allowed=item_allowed)
        if not isinstance(item_value["source"], str) or not item_value["source"] or not isinstance(item_value["target"], str) or not item_value["target"]:
            raise CLIError("migration item paths must be non-empty text")
        if isinstance(item_value["size"], bool) or not isinstance(item_value["size"], int) or item_value["size"] < 0:
            raise CLIError("migration item size must be a non-negative integer")
        if not _valid_digest(item_value["source_digest"]):
            raise CLIError("migration item source_digest must be sha256")
        if "identity" in item_value and item_value["identity"] is not None and not isinstance(item_value["identity"], str):
            raise CLIError("migration item identity must be text when supplied")
        if "revision" in item_value and item_value["revision"] is not None:
            _strict_positive_integer(item_value["revision"], name="migration item revision")
        if item_value["action"] not in {"copy", "copied", "unchanged", "collision"}:
            raise CLIError(f"unsupported migration item action: {item_value['action']!r}")
        if "provenance" in item_value:
            if not isinstance(item_value["provenance"], Mapping):
                raise CLIError("migration item provenance must be an object")
            _migration_provenance_from_value(item_value["provenance"])
    if "provenance" in document:
        if not isinstance(document["provenance"], Mapping):
            raise CLIError("migration report provenance must be an object")
        _migration_provenance_from_value(document["provenance"])
    return dict(document)


def _migration_provenance_from_value(value: Mapping[str, Any] | None) -> MigrationProvenance | None:
    if value is None:
        return None
    _strict_mapping(
        value,
        name="migration provenance",
        required={"source", "source_revision", "imported"},
        allowed={"source", "source_revision", "imported", "license", "ownership", "notes"},
    )
    if not isinstance(value["source"], str) or not value["source"]:
        raise CLIError("migration provenance source must be non-empty text")
    if not isinstance(value["source_revision"], str) or not value["source_revision"]:
        raise CLIError("migration provenance source_revision must be non-empty text")
    if not isinstance(value["imported"], bool):
        raise CLIError("migration provenance imported must be boolean")
    for field in ("license", "ownership", "notes"):
        if field in value and (not isinstance(value[field], str) or not value[field]):
            raise CLIError(f"migration provenance {field} must be non-empty text")
    try:
        return MigrationProvenance(
            value["source"], value["source_revision"], value["imported"],
            value.get("license"), value.get("ownership"), value.get("notes"),
        )
    except (ValueError, TypeError) as exc:
        raise CLIError(f"invalid migration provenance: {exc}") from exc


def _migration_item_from_value(value: Mapping[str, Any]) -> MigrationItem:
    _strict_mapping(
        value,
        name="migration item",
        required={"source", "target", "size", "source_digest", "action"},
        allowed={"source", "target", "size", "source_digest", "action", "identity", "revision", "collision", "error", "provenance"},
    )
    item_provenance = value.get("provenance")
    if item_provenance is not None and not isinstance(item_provenance, Mapping):
        raise CLIError("migration item provenance must be an object")
    try:
        return MigrationItem(
            value["source"], value["target"], value["size"], value["source_digest"],
            value.get("identity"), value.get("revision"), value["action"],
            value.get("collision"), value.get("error"),
            _migration_provenance_from_value(item_provenance),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise CLIError(f"invalid migration item: {exc}") from exc


def _migration_report_from_value(value: Mapping[str, Any]) -> MigrationReport:
    document = _validate_migration_report(value)
    items = tuple(_migration_item_from_value(item) for item in document["items"])
    collisions = tuple(_migration_item_from_value(item) for item in document["collisions"])
    provenance_value = document.get("provenance")
    if provenance_value is not None and not isinstance(provenance_value, Mapping):
        raise CLIError("migration report provenance must be an object")
    provenance = _migration_provenance_from_value(provenance_value)
    report = MigrationReport(
        document["source_root"], document["destination_root"], document["dry_run"],
        items, collisions, provenance, document["source_preserved"], document["verified"],
    )
    if report.ok != document["ok"]:
        raise CLIError("migration report ok flag does not match its contents")
    return report


def _validate_value(raw: bytes, kind: str | None, *, source_name: str = "input") -> tuple[str, Any]:
    if kind in {"prompt-markdown", "markdown"} or (kind is None and source_name.lower().endswith((".md", ".markdown"))):
        try:
            value = parse_prompt_markdown(raw)
        except Exception as exc:
            raise CLIError(f"invalid generated prompt Markdown: {exc}") from exc
        return "prompt-markdown", value
    try:
        value = parse_json_bytes(raw, canonical=True)
    except ContractError as exc:
        raise CLIError(f"input is not canonical JSON: {exc}") from exc
    resolved_kind = kind or (value.get("kind") if isinstance(value, Mapping) else None)
    if resolved_kind not in _JSON_KINDS:
        raise CLIError(f"unsupported or missing contract kind: {resolved_kind!r}")
    try:
        if resolved_kind == "catalog":
            value = PromptCatalog.from_dict(value).to_dict()
        elif resolved_kind == "prompt":
            value = validate_prompt(value)
        elif resolved_kind == "mode-request":
            value = validate_mode(value)
        elif resolved_kind == "dispatch-ledger":
            value = DispatchLedger.from_dict(value).to_dict()
        elif resolved_kind == "dispatch-request":
            value = DispatchRequest.from_value(value).to_dict()
        elif resolved_kind == "dispatch-record":
            value = DispatchRecord.from_dict(value).to_dict()
        elif resolved_kind == "review-ledger":
            value = _validate_review_ledger(value)
        elif resolved_kind == "migration-report":
            value = _validate_migration_report(value)
        elif resolved_kind in {"generation-manifest", "generation-index"}:
            value = _validate_generation_metadata(value, expected_kind=resolved_kind)
    except (ContractError, ValueError, KeyError, TypeError) as exc:
        raise CLIError(f"invalid {resolved_kind} contract: {exc}") from exc
    return resolved_kind, value


def _load_catalog(path: str) -> PromptCatalog:
    kind, value = _validate_value(_read_bytes(path), "catalog", source_name=path)
    if kind != "catalog":
        raise CLIError("catalog input did not contain a catalog contract")
    return PromptCatalog.from_dict(value)


def _read_store(root: str) -> GenerationStore:
    """Open an existing generation root without creating read-only state."""

    target = Path(root)
    generations = target / ".generations"
    if target.is_symlink() or not target.is_dir() or generations.is_symlink() or not generations.is_dir():
        raise CLIError(f"generation root is unavailable or unsafe: {root}")
    return GenerationStore(target)


def _result_for_validated(kind: str, value: Any) -> dict[str, Any]:
    return {"valid": True, "kind": kind, "digest": _digest(value), "document": value}


def _run_validate(args: argparse.Namespace) -> int:
    kind, value = _validate_value(_read_bytes(args.path), args.kind, source_name=args.path)
    _emit(_result_for_validated(kind, value))
    return 0


def _run_generate(args: argparse.Namespace) -> int:
    root = _resolve_option(args.output, args.root_option, name="output")
    result = GenerationStore(root).publish(_load_catalog(args.catalog))
    _emit({
        "generation_id": result.generation_id,
        "generation_digest": result.generation_digest,
        "snapshot": str(result.snapshot),
        "current": str(result.current),
        "manifest": dict(result.manifest),
    })
    return 0


def _run_check(args: argparse.Namespace) -> int:
    catalog_path = _resolve_option(args.catalog_positional, args.catalog, name="catalog") if (args.catalog_positional or args.catalog) else None
    catalog = _load_catalog(catalog_path) if catalog_path else None
    report = _read_store(args.root).check(catalog)
    _emit(report.to_dict())
    return 0 if report.valid and not report.stale else 1


def _run_lookup(args: argparse.Namespace) -> int:
    record = _load_catalog(args.catalog).lookup(args.prompt_id, args.revision)
    _emit(record.to_dict())
    return 0


def _run_search(args: argparse.Namespace) -> int:
    records = _load_catalog(args.catalog).search(args.query, workflow=args.workflow, project_id=args.project_id)
    _emit({"count": len(records), "prompts": [record.to_dict() for record in records]})
    return 0


def _run_snapshot_metadata(args: argparse.Namespace, name: str) -> int:
    current = _read_store(args.root).read_current()
    metadata_path = current.snapshot / name
    kind, value = _validate_value(metadata_path.read_bytes(), None, source_name=name)
    if kind not in {"generation-manifest", "generation-index"}:
        # GenerationStore validates these files structurally; this branch is
        # retained as a typed CLI guard if the format grows a new metadata kind.
        raise CLIError(f"unexpected snapshot metadata kind: {kind}")
    _emit(value)
    return 0


def _resolve_option(positional: str | None, option: str | None, *, name: str) -> str:
    if positional and option and positional != option:
        raise CLIError(f"conflicting positional and --{name} values")
    value = positional or option
    if not value:
        raise CLIError(f"{name} is required")
    return value


def _load_dispatch_request(args: argparse.Namespace) -> DispatchRequest:
    if args.request is not None or args.request_option is not None:
        request_path = _resolve_option(args.request, args.request_option, name="request")
        value = _read_json(request_path)
        try:
            return DispatchRequest.from_value(value)
        except (ValueError, KeyError, TypeError) as exc:
            raise CLIError(f"invalid dispatch request: {exc}") from exc
    if args.prompt_id is None or args.revision is None or args.carrier_id is None:
        raise CLIError("dispatch request requires a request file or prompt/revision/carrier fields")
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dispatch-request",
        "prompt_id": args.prompt_id,
        "revision": args.revision,
        "carrier": {"carrier_id": args.carrier_id, "dispatchable": args.dispatchable, "kind": args.carrier_kind},
    }
    if args.payload_digest:
        value["payload_digest"] = args.payload_digest
    elif args.payload:
        value["payload"] = args.payload
    else:
        raise CLIError("dispatch request requires --payload or --payload-digest")
    if args.source_revision:
        value["source_revision"] = args.source_revision
    if args.lineage:
        value["lineage"] = args.lineage
    try:
        return DispatchRequest.from_value(value)
    except (ValueError, KeyError, TypeError) as exc:
        raise CLIError(f"invalid dispatch request: {exc}") from exc


def _load_dispatch_ledger(path: str) -> DispatchLedger:
    target = Path(path)
    if not target.exists():
        return DispatchLedger()
    value = _read_json(path)
    try:
        return DispatchLedger.from_dict(value)
    except (ValueError, KeyError, TypeError) as exc:
        raise CLIError(f"invalid dispatch ledger: {exc}") from exc


def _run_dispatch(args: argparse.Namespace, *, record: bool) -> int:
    ledger_path = _resolve_option(args.ledger, args.ledger_option, name="ledger")
    with _dispatch_ledger_lock(ledger_path):
        target = Path(ledger_path)
        if target.is_symlink():
            raise CLIError(f"input is not a safe regular file: {ledger_path}")
        expected = _read_bytes(ledger_path) if target.exists() else None
        ledger = _load_dispatch_ledger(ledger_path)
        request = _load_dispatch_request(args)
        if not record:
            decision = ledger.check(request)
            result = decision.to_dict()
            status = 0 if decision.allowed else 1
        else:
            entry = ledger.record(request)
            _write_bytes(ledger_path, ledger.to_bytes(), expected=expected)
            result = {"record": entry.to_dict(), "ledger": ledger.to_dict()}
            status = 0
    _emit(result)
    return status


def _load_provenance(path: str | None) -> MigrationProvenance | None:
    if path is None:
        return None
    value = _read_json(path)
    if not isinstance(value, Mapping):
        raise CLIError("provenance must be a JSON object")
    if "imported" not in value:
        value = {**value, "imported": True}
    return _migration_provenance_from_value(value)


def _run_migration(args: argparse.Namespace) -> int:
    if args.apply and args.dry_run:
        raise CLIError("--apply and --dry-run are mutually exclusive")
    if not args.apply and not args.dry_run:
        args.dry_run = True
    provenance = _load_provenance(args.provenance)
    plan = dry_run_import(args.source, args.destination, provenance=provenance)
    plan_bytes = plan.to_bytes()
    selected_plan = plan
    plan_digest = sha256_digest(plan_bytes)
    if args.plan and args.dry_run:
        plan_path = Path(args.plan)
        source_path = Path(plan.source_root)
        if plan_path.resolve().is_relative_to(source_path.resolve()):
            raise CLIError("migration plan must be outside the source")
        if plan_path.exists() and any(plan_path.samefile(item.source) for item in plan.items):
            raise CLIError("migration plan must not alias a source file")
        _write_bytes(args.plan, plan_bytes)
    if args.apply:
        if args.plan:
            supplied = _read_bytes(args.plan)
            if supplied != plan_bytes:
                raise CLIError("migration plan changed; refusing apply")
            try:
                supplied_value = parse_json_bytes(supplied, canonical=True)
                if not isinstance(supplied_value, Mapping):
                    raise CLIError("migration plan must be a JSON object")
                selected_plan = _migration_report_from_value(supplied_value)
            except ContractError as exc:
                raise CLIError(f"invalid migration plan: {exc}") from exc
        if not plan.ok:
            _emit({"plan": plan.to_dict(), "plan_digest": plan_digest})
            return 1
        applied = copy_then_verify(
            args.source, args.destination, provenance=provenance, plan=selected_plan,
        )
        _emit({"plan": plan.to_dict(), "plan_digest": plan_digest, "result": applied.to_dict()})
        return 0 if applied.ok and applied.verified else 1
    _emit({"plan": plan.to_dict(), "plan_digest": plan_digest})
    return 0 if plan.ok else 1


def _run_recovery(args: argparse.Namespace) -> int:
    if not args.dry_run:
        raise CLIError("recovery is report-only; only --dry-run is supported")
    report = _read_store(args.root).recover(dry_run=True)
    _emit(report.to_dict())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-generator", description="Deterministic public-safe prompt catalog tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate one canonical contract or generated Markdown document")
    validate.add_argument("path")
    validate.add_argument("--kind", choices=sorted(_JSON_KINDS | {"prompt-markdown", "markdown"}))
    validate.set_defaults(handler=_run_validate)

    generate = sub.add_parser("generate", help="publish a deterministic catalog snapshot")
    generate.add_argument("catalog")
    generate.add_argument("output", nargs="?")
    generate.add_argument("--output", dest="root_option")
    generate.add_argument("--output-root", dest="root_option")
    generate.set_defaults(handler=_run_generate)

    check = sub.add_parser("check", help="check CURRENT and optionally compare a catalog")
    check.add_argument("root")
    check.add_argument("catalog_positional", nargs="?")
    check.add_argument("--catalog")
    check.set_defaults(handler=_run_check)

    lookup = sub.add_parser("lookup", help="look up one prompt revision")
    lookup.add_argument("catalog")
    lookup.add_argument("prompt_id")
    lookup.add_argument("--revision", type=int)
    lookup.set_defaults(handler=_run_lookup)

    search = sub.add_parser("search", help="deterministically search prompt metadata")
    search.add_argument("catalog")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--workflow")
    search.add_argument("--project-id")
    search.set_defaults(handler=_run_search)

    for name in ("manifest", "index"):
        metadata = sub.add_parser(name, help=f"read the validated CURRENT {name}")
        metadata.add_argument("root")
        metadata.set_defaults(handler=lambda args, name=name: _run_snapshot_metadata(args, name + ".json"))

    dispatch = sub.add_parser("dispatch", help="check or record a local dispatch intent")
    dispatch_sub = dispatch.add_subparsers(dest="dispatch_action", required=True)
    for action, record in (("check", False), ("record", True)):
        command = dispatch_sub.add_parser(action, help=f"{action} a local dispatch intent")
        command.add_argument("ledger", nargs="?")
        command.add_argument("request", nargs="?")
        command.add_argument("--ledger", dest="ledger_option")
        command.add_argument("--request", dest="request_option")
        command.add_argument("--prompt-id")
        command.add_argument("--revision", type=int)
        command.add_argument("--carrier-id")
        command.add_argument("--carrier-kind", default="markdown")
        command.add_argument("--dispatchable", action="store_true")
        command.add_argument("--payload")
        command.add_argument("--payload-digest")
        command.add_argument("--source-revision")
        command.add_argument("--lineage", action="append", default=[])
        command.set_defaults(handler=lambda args, record=record: _run_dispatch(args, record=record))

    for name in ("import", "migrate"):
        migration = sub.add_parser(name, help="plan or apply a source-preserving inert-data migration")
        migration.add_argument("source")
        migration.add_argument("destination")
        mode = migration.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="emit the exact plan without writing destination bytes")
        mode.add_argument("--apply", action="store_true", help="apply only after the exact plan is valid")
        migration.add_argument("--plan", help="write or verify the canonical exact plan")
        migration.add_argument("--provenance", help="canonical JSON provenance descriptor")
        migration.set_defaults(handler=_run_migration)

    for name in ("recovery", "recover"):
        recovery = sub.add_parser(name, help="classify generation remnants without deleting anything")
        recovery.add_argument("root")
        recovery.add_argument("--dry-run", action="store_true", default=True)
        recovery.set_defaults(handler=_run_recovery)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the public CLI and return a process exit status."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CLIError, ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"prompt-generator: error: {exc}", file=sys.stderr)
        return 2


__all__ = ["CLIError", "build_parser", "main"]
