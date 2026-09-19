"""Public Prompt Generator library surface.

The package exports contract, catalog, generation, dispatch, review, mode, and
migration primitives.  All values are local deterministic data; no export
performs provider access or resolves private references.
"""

from .catalog import Catalog, CatalogEntry, CatalogError, PromptCatalog, PromptRecord, catalog_bytes, catalog_from_dict, validate_catalog, validate_prompt
from .contracts import (
    CanonicalJSONError,
    ContractError,
    SCHEMA_RESOURCE_FILES,
    DuplicateKeyError,
    PrivateReferenceError,
    PrivateReferenceRequest,
    PrivateReferenceResult,
    SchemaValidationError,
    UnsupportedVersionError,
    canonical_json,
    canonical_json_bytes,
    canonical_json_loads,
    contract_bytes,
    parse_contract,
    parse_json_bytes,
    schema_bytes,
    schema_digest,
    schema_digests,
    schema_for,
    schema_manifest,
    schema_resource,
    validate_contract,
)
from .dispatch import (
    Carrier,
    DispatchDecision,
    DispatchError,
    DispatchLedger,
    DispatchRecord,
    DispatchRequest,
    check_dispatch,
    payload_digest,
    record_dispatch,
)
from .generation import (
    CheckReport,
    GenerationResult,
    GenerationStore,
    RecoveryReport,
    check,
    classify_recovery,
    generate,
    parse_prompt_markdown,
    payload_from_prompt_markdown,
    prompt_payload_bytes,
    recover,
    render_prompt,
    render_prompt_text,
    validate_prompt_markdown,
)
from .integrity import IntegrityError, IntegrityRecord, atomic_write_bytes, payload_and_file_integrity, sha256_digest
from .migration import MigrationError, MigrationProvenance, MigrationReport, copy_then_verify, dry_run_import, migrate
from .modes import (
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    ModeError,
    ModeValidationError,
    is_dispatchable,
    load_mode_template,
    parse_mode_template,
    render_mode_template,
    render_project_request,
    validate_mode,
)
from .reviews import ReviewError, ReviewEvidence, ReviewLedger, ReviewStatus

__version__ = "0.1.0"

__all__ = [
    "Catalog", "CatalogEntry", "CatalogError", "PromptCatalog", "PromptRecord", "catalog_bytes", "catalog_from_dict", "validate_catalog", "validate_prompt",
    "CanonicalJSONError", "ContractError", "DuplicateKeyError", "PrivateReferenceError", "PrivateReferenceRequest", "PrivateReferenceResult", "SchemaValidationError", "UnsupportedVersionError", "SCHEMA_RESOURCE_FILES", "canonical_json", "canonical_json_bytes", "canonical_json_loads", "contract_bytes", "parse_contract", "parse_json_bytes", "schema_bytes", "schema_digest", "schema_digests", "schema_for", "schema_manifest", "schema_resource", "validate_contract",
    "Carrier", "DispatchDecision", "DispatchError", "DispatchLedger", "DispatchRecord", "DispatchRequest", "check_dispatch", "payload_digest", "record_dispatch",
    "CheckReport", "GenerationResult", "GenerationStore", "RecoveryReport", "check", "classify_recovery", "generate", "parse_prompt_markdown", "payload_from_prompt_markdown", "prompt_payload_bytes", "recover", "render_prompt", "render_prompt_text", "validate_prompt_markdown",
    "IntegrityError", "IntegrityRecord", "atomic_write_bytes", "payload_and_file_integrity", "sha256_digest",
    "MigrationError", "MigrationProvenance", "MigrationReport", "copy_then_verify", "dry_run_import", "migrate",
    "LEVEL_1", "LEVEL_2", "LEVEL_3", "ModeError", "ModeValidationError", "is_dispatchable", "load_mode_template", "parse_mode_template", "render_mode_template", "render_project_request", "validate_mode",
    "ReviewError", "ReviewEvidence", "ReviewLedger", "ReviewStatus", "__version__",
]
