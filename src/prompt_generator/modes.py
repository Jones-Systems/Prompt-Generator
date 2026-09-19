"""Strict, public-safe mode contracts for prompt generation.

Mode documents are data.  This module validates their shape and renders a
copyable carrier; it never discovers an app, allocates a workspace, dispatches
a prompt, or resolves a private value.
"""

from __future__ import annotations

import base64
import copy
import hashlib
from importlib import resources
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import ContractError, canonical_json_bytes, canonical_json_loads


MODE_SCHEMA_VERSION = 1
LEVEL_1 = "level-1.v1"
LEVEL_2 = "level-2.v1"
LEVEL_3 = "level-3.v1"
LEVEL_ONE = LEVEL_1
LEVEL_TWO = LEVEL_2
LEVEL_THREE = LEVEL_3
LEVEL_1_MODE = LEVEL_1
LEVEL_2_MODE = LEVEL_2
LEVEL_3_MODE = LEVEL_3
SUPPORTED_MODES = (LEVEL_1, LEVEL_2, LEVEL_3)

PROJECT_REQUEST_SCHEMA = "codex-v3.chatgpt-project-request.v2"
PROJECT_RESUME_SCHEMA = "codex-v3.chatgpt-project-resume.v1"
PROJECT_FLOW = "docs/handoffs/chatgpt-pro-project-flow.md"
PROJECT_CONNECTOR_TOOLS = (
    "read",
    "view_image",
    "apply_patch",
    "exec_command",
    "write_stdin",
)
PRIVATE_APP_NAME = "ChatGPT Web Connector"
PRIVATE_RESEARCH_APP_NAME = "ChatGPT Web Connector — Research"
PRIVATE_APP_SLUG = "chatgpt-web-connector"
WEB_SEARCH_COMPOSER = "@Web search"

_HANDLE_RE = re.compile(r"^ws-(?:0[1-9]|10)\.[A-Za-z0-9_-]{22}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_TEXT_RE = re.compile(r"\S")
_EFFECTS = frozenset(
    {"known-no-effect", "desired-effect-observed", "not_applied", "applied"}
)
_CLOSED_EFFECTS = frozenset({"partial", "unknown", "unknown-effect"})


class ModeError(ContractError):
    """Base error for a malformed or unsafe mode contract."""


class UnsupportedModeError(ModeError):
    """Raised when a mode version is not supported by this library."""


class ModeValidationError(ModeError):
    """Raised when a mode document does not satisfy its versioned contract."""


class ModeActivationError(ModeValidationError):
    """Raised when a mode is not explicitly activated or lacks intake proof."""


class ModeBindingError(ModeValidationError):
    """Raised when a repository, app, scratch, or workspace binding is unsafe."""


class ModeResumeError(ModeValidationError):
    """Raised when a resume uses a stale, repeated, or unbound handle."""


def _copy(value: Any) -> Any:
    """Return a detached JSON-like value so validation cannot mutate callers."""

    if isinstance(value, Mapping):
        return {str(key): _copy(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy(child) for child in value]
    return copy.deepcopy(value)


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModeValidationError(f"{name} must be an object")
    return value


def _strict_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    name: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ModeValidationError(f"{name} is missing required field(s): {', '.join(missing)}")
    if unknown:
        raise ModeValidationError(f"{name} has unknown field(s): {', '.join(unknown)}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or _TEXT_RE.search(value) is None:
        raise ModeValidationError(f"{name} must be non-empty text")
    if "\x00" in value or "\r" in value:
        raise ModeValidationError(f"{name} contains an unsafe control character")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ModeValidationError(f"{name} must be boolean")
    return value


def _text_list(value: Any, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ModeValidationError(f"{name} must be a {'non-empty ' if nonempty else ''}list")
    result = [_text(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ModeValidationError(f"{name} must not contain duplicates")
    return result


def validate_safe_path(value: Any, *, field: str = "repository_root") -> str:
    """Validate a lexical root path without following or creating filesystem paths."""

    result = _text(value, field)
    if "\\" in result or "//" in result:
        raise ModeBindingError(f"{field} uses an ambiguous path separator")
    components = result.split("/")
    if any(component in {"", ".", ".."} for component in components[1:] if result.startswith("/")):
        raise ModeBindingError(f"{field} contains traversal or an ambiguous component")
    if any(component in {".", ".."} for component in components):
        raise ModeBindingError(f"{field} contains traversal or an ambiguous component")
    if result.startswith("~"):
        raise ModeBindingError(f"{field} must not use home-directory expansion")
    return result


def _repository_id(value: Any, *, field: str = "repository.repository_id") -> str:
    result = _text(value, field)
    if _REPOSITORY_RE.fullmatch(result) is None:
        raise ModeBindingError(f"{field} must identify one owner/name repository")
    return result


def _branch(value: Any, *, field: str = "repository.branch") -> str:
    result = _text(value, field)
    if _BRANCH_RE.fullmatch(result) is None or ".." in result or result.endswith("/"):
        raise ModeBindingError(f"{field} is not a safe branch name")
    if result in {"main", "master"}:
        raise ModeBindingError(f"{field} must be a non-main task branch")
    return result


def _head(value: Any, *, field: str) -> str:
    result = _text(value, field)
    if result in {"HEAD", "head", "main", "master"}:
        raise ModeBindingError(f"{field} must be the actual bound revision, not a symbolic name")
    return result


def _validate_repository(
    value: Any,
    *,
    delivery: str,
    name: str = "repository",
    require_nondispatchable: bool = True,
) -> dict[str, Any]:
    repository = _require_mapping(value, name)
    required = {"repository_id", "repository_root", "branch", "head", "base", "delivery"}
    allowed = required | {"initial_head", "nondispatchable"}
    if delivery == "task-branch":
        required = required | {"initial_head"}
    _strict_keys(repository, required=required, allowed=allowed, name=name)
    if repository["delivery"] != delivery:
        raise ModeBindingError(f"{name}.delivery must be {delivery!r}")
    _repository_id(repository["repository_id"], field=f"{name}.repository_id")
    validate_safe_path(repository["repository_root"], field=f"{name}.repository_root")
    _branch(repository["branch"], field=f"{name}.branch")
    _head(repository["head"], field=f"{name}.head")
    _text(repository["base"], f"{name}.base")
    if "initial_head" in required:
        _head(repository["initial_head"], field=f"{name}.initial_head")
    if require_nondispatchable:
        if repository.get("nondispatchable") is not True:
            raise ModeBindingError(f"{name}.nondispatchable must be true for public fixtures")
    elif "nondispatchable" in repository:
        _boolean(repository["nondispatchable"], f"{name}.nondispatchable")
    return dict(repository)


def _validate_activation(value: Any, mode: str, *, fallback: bool = False) -> dict[str, Any]:
    activation = _require_mapping(value, "activation")
    allowed = {"mode", "requested", "source_revision", "reason", "explicit_fallback"}
    required = {"mode", "requested", "source_revision"}
    if fallback:
        required |= {"explicit_fallback", "reason"}
    _strict_keys(activation, required=required, allowed=allowed, name="activation")
    if activation["mode"] != mode:
        raise ModeActivationError("activation mode does not match the selected mode")
    if activation["requested"] is not True:
        raise ModeActivationError("mode activation must be explicitly requested")
    _text(activation["source_revision"], "activation.source_revision")
    if fallback:
        if activation["explicit_fallback"] is not True:
            raise ModeActivationError("Level 1 requires explicit_fallback=true")
        _text(activation["reason"], "activation.reason")
    elif "explicit_fallback" in activation:
        raise ModeActivationError("primary modes cannot carry explicit_fallback")
    return dict(activation)


def _validate_recovery(value: Any, *, name: str = "recovery") -> dict[str, Any]:
    recovery = _require_mapping(value, name)
    allowed = {"persistence", "checkpoint", "effect", "status", "attempt"}
    required = {"persistence", "checkpoint"}
    _strict_keys(recovery, required=required, allowed=allowed, name=name)
    persistence = _text(recovery["persistence"], f"{name}.persistence")
    if persistence not in {"bounded", "durable"}:
        raise ModeValidationError(f"{name}.persistence must be bounded or durable")
    _text(recovery["checkpoint"], f"{name}.checkpoint")
    effect = recovery.get("effect", recovery.get("status"))
    if effect is None:
        raise ModeValidationError(f"{name} must record an effect classification")
    if effect in _CLOSED_EFFECTS:
        raise ModeResumeError("unknown or partial effect must remain closed until reconciled")
    if effect not in _EFFECTS:
        raise ModeResumeError(f"unsupported recovery effect classification: {effect!r}")
    if "effect" in recovery and "status" in recovery and recovery["effect"] != recovery["status"]:
        raise ModeResumeError("recovery effect and status disagree")
    return dict(recovery)


def _validate_app(value: Any, *, level: int) -> dict[str, Any]:
    app = _require_mapping(value, "app")
    if level == 2:
        allowed = {"name", "discovered", "authority", "actions"}
        required = allowed
        _strict_keys(app, required=required, allowed=allowed, name="app")
        if app["name"] != "GitHub":
            raise ModeBindingError("Level 2 requires the exact GitHub app")
        if app["discovered"] is not True:
            raise ModeBindingError("Level 2 app discovery is not proven")
        if app["authority"] != "repository-scoped":
            raise ModeBindingError("Level 2 app authority must be repository-scoped")
        actions = _text_list(app["actions"], "app.actions", nonempty=True)
        if "read" not in actions or "write" not in actions:
            raise ModeBindingError("Level 2 app must prove read and write actions")
        return dict(app)

    allowed = {"name", "slug", "discovered", "callable", "authority", "research_app_name"}
    _strict_keys(app, required=allowed, allowed=allowed, name="app")
    if app["name"] != PRIVATE_APP_NAME or app["slug"] != PRIVATE_APP_SLUG:
        raise ModeBindingError("Level 3 requires the exact private ChatGPT Web Connector app")
    if app["research_app_name"] != PRIVATE_RESEARCH_APP_NAME:
        raise ModeBindingError("Level 3 must preserve the distinct research-app name")
    if app["discovered"] is not True or app["callable"] is not True:
        raise ModeBindingError("Level 3 app discovery and callable authority are not proven")
    if app["authority"] != "bound-workspace":
        raise ModeBindingError("Level 3 app authority must be bound-workspace")
    return dict(app)


def _validate_instructions(value: Any) -> dict[str, Any]:
    instructions = _require_mapping(value, "instructions")
    allowed = {"source", "revision", "complete", "intake"}
    _strict_keys(instructions, required=allowed, allowed=allowed, name="instructions")
    _text(instructions["source"], "instructions.source")
    _text(instructions["revision"], "instructions.revision")
    if instructions["complete"] is not True or instructions["intake"] != "complete":
        raise ModeActivationError("Level 2 requires complete instruction intake")
    return dict(instructions)


def _validate_scratch(value: Any) -> dict[str, Any]:
    scratch = _require_mapping(value, "scratch")
    allowed = {"available", "root", "checkpoint", "retention"}
    _strict_keys(scratch, required=allowed, allowed=allowed, name="scratch")
    if scratch["available"] is not True:
        raise ModeActivationError("Level 2 requires available local scratch")
    validate_safe_path(scratch["root"], field="scratch.root")
    _text(scratch["checkpoint"], "scratch.checkpoint")
    if scratch["retention"] != "task-scoped":
        raise ModeActivationError("Level 2 scratch retention must be task-scoped")
    return dict(scratch)


def validate_workspace_handle(value: Any) -> str:
    """Validate an already-issued handle; this function never creates one."""

    result = _text(value, "workspace_handle")
    if _HANDLE_RE.fullmatch(result) is None:
        raise ModeBindingError("workspace_handle has the wrong generation-bearing shape")
    token = result.split(".", 1)[1]
    try:
        decoded = base64.urlsafe_b64decode(token + "==")
    except (ValueError, base64.binascii.Error) as exc:
        raise ModeBindingError("workspace_handle is not canonical base64url") from exc
    if len(decoded) != 16 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != token:
        raise ModeBindingError("workspace_handle must contain a canonical 128-bit token")
    return result


def _validate_resume(value: Any, current_handle: str, *, project_request_digest: str) -> dict[str, Any]:
    resume = _require_mapping(value, "resume")
    allowed = {
        "kind",
        "project_request_sha256",
        "previous_workspace_handle_sha256",
        "workspace_handle",
        "fresh",
    }
    required = allowed
    _strict_keys(resume, required=required, allowed=allowed, name="resume")
    if resume["kind"] != "initial" and resume["kind"] != "resume":
        raise ModeResumeError("resume.kind must be initial or resume")
    if resume["project_request_sha256"] != project_request_digest:
        raise ModeResumeError("resume is bound to a different ProjectRequestV2 revision")
    for field in ("project_request_sha256", "previous_workspace_handle_sha256"):
        digest = resume[field]
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise ModeResumeError(f"resume.{field} must be a lowercase SHA-256 digest")
    if resume["workspace_handle"] != current_handle:
        raise ModeResumeError("resume workspace handle does not match ProjectRequestV2")
    if resume["kind"] == "initial":
        if resume["fresh"] is not True:
            raise ModeResumeError("initial Level 3 binding must be fresh")
    else:
        if resume["fresh"] is not True:
            raise ModeResumeError("resume requires a fresh current workspace handle")
        previous_digest = resume["previous_workspace_handle_sha256"]
        if previous_digest == hashlib.sha256(current_handle.encode("utf-8")).hexdigest():
            raise ModeResumeError("resume reuses the current workspace handle")
    return dict(resume)


def validate_project_request_v2(value: Any) -> dict[str, Any]:
    """Validate the exact source-owned Level 3 ProjectRequestV2 shape."""

    request = _require_mapping(value, "project_request")
    expected = {
        "schema",
        "mode",
        "web_search",
        "connector_tools",
        "flow",
        "repository_id",
        "repository_root",
        "branch",
        "initial_head",
        "base",
        "delivery",
        "publication_transport",
        "workspace_handle",
        "goal",
    }
    _strict_keys(request, required=expected, allowed=expected, name="project_request")
    if request["schema"] != PROJECT_REQUEST_SCHEMA:
        raise ModeValidationError("project_request.schema is not ProjectRequestV2")
    if request["mode"] != "implementation":
        raise ModeValidationError("project_request.mode must be implementation")
    if request["web_search"] != "required-before-first-message":
        raise ModeActivationError("ProjectRequestV2 requires web search before the first message")
    if not isinstance(request["connector_tools"], list):
        raise ModeBindingError("project_request.connector_tools must be an array")
    if tuple(request["connector_tools"]) != PROJECT_CONNECTOR_TOOLS:
        raise ModeBindingError("project_request.connector_tools are not the exact five core actions")
    if request["flow"] != PROJECT_FLOW:
        raise ModeValidationError("project_request.flow is not the canonical project flow")
    for field in ("repository_id", "repository_root", "branch", "initial_head", "base", "goal"):
        _text(request[field], f"project_request.{field}")
    _repository_id(request["repository_id"], field="project_request.repository_id")
    validate_safe_path(request["repository_root"], field="project_request.repository_root")
    _branch(request["branch"], field="project_request.branch")
    _head(request["initial_head"], field="project_request.initial_head")
    _text(request["base"], "project_request.base")
    if request["delivery"] not in {"local-only", "draft-pr"}:
        raise ModeBindingError("project_request.delivery is invalid")
    if request["publication_transport"] not in {
        "none",
        "external-credential-broker",
        "direct-github-token",
    }:
        raise ModeBindingError("project_request.publication_transport is invalid")
    if request["delivery"] == "local-only" and request["publication_transport"] != "none":
        raise ModeBindingError("local-only ProjectRequestV2 must use publication_transport=none")
    if request["delivery"] == "draft-pr" and request["publication_transport"] == "none":
        raise ModeBindingError("draft-pr ProjectRequestV2 requires a publication transport")
    validate_workspace_handle(request["workspace_handle"])
    return dict(request)


def project_request_digest(value: Mapping[str, Any]) -> str:
    """Return the exact ProjectRequestV2 canonical JSON digest used by resume."""

    request = validate_project_request_v2(value)
    return hashlib.sha256(_project_request_source_bytes(request)).hexdigest()


def _sort_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _sort_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    return value


def _project_request_source_bytes(value: Mapping[str, Any]) -> bytes:
    """Use the source ProjectRequestV2 two-space JSON representation for digests."""

    return (
        json.dumps(_sort_json(value), ensure_ascii=False, allow_nan=False, indent=2)
        + "\n"
    ).encode("utf-8", "strict")


def validate_level_one(value: Any) -> dict[str, Any]:
    document = _require_mapping(value, LEVEL_1)
    allowed = {
        "schema_version",
        "kind",
        "mode",
        "activation",
        "goal",
        "repository",
        "fallback",
        "dispatchable",
    }
    required = set(allowed)
    _strict_keys(document, required=required, allowed=allowed, name=LEVEL_1)
    if document["schema_version"] != MODE_SCHEMA_VERSION or isinstance(document["schema_version"], bool):
        raise UnsupportedModeError("unsupported mode schema_version")
    if document["kind"] != "mode-request" or document["mode"] != LEVEL_1:
        raise ModeValidationError("not a Level 1 mode-request")
    _validate_activation(document["activation"], LEVEL_1, fallback=True)
    _text(document["goal"], "goal")
    _validate_repository(document["repository"], delivery="local-only")
    if document["fallback"] != "reduced-local-only":
        raise ModeValidationError("Level 1 fallback must be explicitly reduced-local-only")
    if document["dispatchable"] is not False:
        raise ModeBindingError("public mode carriers must be nondispatchable")
    return _copy(document)


def validate_level_two(value: Any) -> dict[str, Any]:
    document = _require_mapping(value, LEVEL_2)
    allowed = {
        "schema_version",
        "kind",
        "mode",
        "activation",
        "goal",
        "repository",
        "app",
        "instructions",
        "scratch",
        "recovery",
        "dispatchable",
    }
    required = set(allowed)
    _strict_keys(document, required=required, allowed=allowed, name=LEVEL_2)
    if document["schema_version"] != MODE_SCHEMA_VERSION or isinstance(document["schema_version"], bool):
        raise UnsupportedModeError("unsupported mode schema_version")
    if document["kind"] != "mode-request" or document["mode"] != LEVEL_2:
        raise ModeValidationError("not a Level 2 mode-request")
    _validate_activation(document["activation"], LEVEL_2)
    _text(document["goal"], "goal")
    _validate_repository(document["repository"], delivery="task-branch")
    _validate_app(document["app"], level=2)
    _validate_instructions(document["instructions"])
    _validate_scratch(document["scratch"])
    _validate_recovery(document["recovery"])
    if document["dispatchable"] is not False:
        raise ModeBindingError("public mode carriers must be nondispatchable")
    return _copy(document)


def validate_level_three(value: Any, *, previous_workspace_handle: str | None = None) -> dict[str, Any]:
    document = _require_mapping(value, LEVEL_3)
    allowed = {
        "schema_version",
        "kind",
        "mode",
        "activation",
        "app",
        "project_request",
        "recovery",
        "resume",
        "dispatchable",
    }
    required = set(allowed)
    _strict_keys(document, required=required, allowed=allowed, name=LEVEL_3)
    if document["schema_version"] != MODE_SCHEMA_VERSION or isinstance(document["schema_version"], bool):
        raise UnsupportedModeError("unsupported mode schema_version")
    if document["kind"] != "mode-request" or document["mode"] != LEVEL_3:
        raise ModeValidationError("not a Level 3 mode-request")
    _validate_activation(document["activation"], LEVEL_3)
    _validate_app(document["app"], level=3)
    request = validate_project_request_v2(document["project_request"])
    digest = project_request_digest(request)
    current_handle = request["workspace_handle"]
    resume = _validate_resume(document["resume"], current_handle, project_request_digest=digest)
    if resume["kind"] == "resume" and previous_workspace_handle is None:
        raise ModeResumeError("resume requires the caller to supply the prior issued handle")
    if previous_workspace_handle is not None:
        validate_workspace_handle(previous_workspace_handle)
        if resume["kind"] != "resume":
            raise ModeResumeError("a supplied previous handle requires a resume contract")
        if hashlib.sha256(previous_workspace_handle.encode("utf-8")).hexdigest() != resume["previous_workspace_handle_sha256"]:
            raise ModeResumeError("resume previous handle does not match the bound prior generation")
        if previous_workspace_handle == current_handle:
            raise ModeResumeError("resume requires a fresh workspace handle")
    _validate_recovery(document["recovery"])
    if document["dispatchable"] is not False:
        raise ModeBindingError("public mode carriers must be nondispatchable")
    return _copy(document)


def validate_mode(value: Any, mode: str | None = None, *, previous_workspace_handle: str | None = None) -> dict[str, Any]:
    """Validate one explicitly selected mode; no fallback is selected implicitly."""

    document = _require_mapping(value, "mode document")
    selected = mode if mode is not None else document.get("mode")
    if selected not in SUPPORTED_MODES:
        raise UnsupportedModeError(f"unsupported mode: {selected!r}")
    if document.get("mode") != selected:
        raise ModeValidationError("selected mode does not match document mode")
    if selected == LEVEL_1:
        return validate_level_one(document)
    if selected == LEVEL_2:
        return validate_level_two(document)
    return validate_level_three(document, previous_workspace_handle=previous_workspace_handle)


validate_level_1 = validate_level_one
validate_level_2 = validate_level_two
validate_level_3 = validate_level_three


def mode_name(value: Mapping[str, Any]) -> str:
    """Return the validated mode identifier without guessing a fallback."""

    return validate_mode(value)["mode"]


def is_dispatchable(value: Mapping[str, Any]) -> bool:
    """Return the carrier flag after strict validation."""

    return bool(validate_mode(value)["dispatchable"])


def _payload_from_template(text: str) -> bytes:
    if not isinstance(text, str) or not text.startswith(f"{WEB_SEARCH_COMPOSER}\n\n"):
        raise ModeValidationError("template must keep the @Web search composer line outside its payload")
    marker = "<!-- mode-payload:begin -->\n```json\n"
    ending = "\n```\n<!-- mode-payload:end -->\n"
    if text.count(marker) != 1 or text.count(ending) != 1:
        raise ModeValidationError("template must contain one bounded JSON payload")
    start = text.index(marker) + len(marker)
    end = text.index(ending, start)
    raw = text[start:end].encode("utf-8") + b"\n"
    document = canonical_json_loads(raw)
    validate_mode(document)
    return raw


def render_mode_template(value: Mapping[str, Any], *, ephemeral: bool = False) -> str:
    """Render a carrier, requiring ephemeral authorization for Level 3."""

    document = validate_mode(value)
    if document["mode"] == LEVEL_3 and ephemeral is not True:
        raise ModeBindingError(
            "Level 3 mode rendering requires explicit ephemeral authorization"
        )
    payload = canonical_json_bytes(document).decode("utf-8")
    return (
        f"{WEB_SEARCH_COMPOSER}\n\n"
        "<!-- mode-payload:begin -->\n"
        "```json\n"
        f"{payload}"
        "```\n"
        "<!-- mode-payload:end -->\n"
    )


def parse_mode_template(text: str) -> dict[str, Any]:
    """Extract and validate the one mode payload from a rendered carrier."""

    raw = _payload_from_template(text)
    return validate_mode(canonical_json_loads(raw))


def template_path(mode: str, *, root: Path | None = None) -> Path:
    """Resolve a mode template in a checkout or installed package."""

    if mode not in SUPPORTED_MODES:
        raise UnsupportedModeError(f"unsupported mode: {mode!r}")
    filename = f"{mode}.md"
    if root is not None:
        return Path(root) / "templates" / filename
    checkout_path = Path(__file__).resolve().parents[2] / "templates" / filename
    if checkout_path.is_file():
        return checkout_path
    package_path = Path(__file__).resolve().parent / "templates" / filename
    if package_path.is_file():
        return package_path
    # A normal wheel has a filesystem path, while the resource fallback below
    # also keeps load_mode_template usable for archive-backed imports.
    resource = resources.files("prompt_generator").joinpath("templates", filename)
    try:
        return Path(resource)
    except TypeError as exc:
        raise ModeValidationError(f"mode template has no filesystem path: {mode}") from exc


def load_mode_template(mode: str, *, root: Path | None = None) -> dict[str, Any]:
    try:
        if root is not None:
            text = template_path(mode, root=root).read_text(encoding="utf-8")
        else:
            resource = resources.files("prompt_generator").joinpath("templates", f"{mode}.md")
            text = resource.read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError, FileNotFoundError) as exc:
        raise ModeValidationError(f"mode template is unavailable: {mode}") from exc
    document = parse_mode_template(text)
    if document["mode"] != mode:
        raise ModeValidationError("template mode does not match requested mode")
    return document


def render_project_request(value: Mapping[str, Any], *, ephemeral: bool = False) -> str:
    """Render ProjectRequestV2 only for a caller-authorized ephemeral carrier."""

    request = validate_project_request_v2(value)
    if ephemeral is not True:
        raise ModeBindingError("Level 3 ProjectRequestV2 rendering requires explicit ephemeral authorization")
    return _project_request_source_bytes(request).decode("utf-8")


__all__ = [
    "LEVEL_1",
    "LEVEL_2",
    "LEVEL_3",
    "LEVEL_ONE",
    "LEVEL_TWO",
    "LEVEL_THREE",
    "LEVEL_1_MODE",
    "LEVEL_2_MODE",
    "LEVEL_3_MODE",
    "SUPPORTED_MODES",
    "PROJECT_REQUEST_SCHEMA",
    "PROJECT_RESUME_SCHEMA",
    "PROJECT_FLOW",
    "PROJECT_CONNECTOR_TOOLS",
    "PRIVATE_APP_NAME",
    "PRIVATE_RESEARCH_APP_NAME",
    "PRIVATE_APP_SLUG",
    "WEB_SEARCH_COMPOSER",
    "ModeError",
    "UnsupportedModeError",
    "ModeValidationError",
    "ModeActivationError",
    "ModeBindingError",
    "ModeResumeError",
    "validate_safe_path",
    "validate_workspace_handle",
    "validate_project_request_v2",
    "project_request_digest",
    "validate_level_one",
    "validate_level_two",
    "validate_level_three",
    "validate_level_1",
    "validate_level_2",
    "validate_level_3",
    "validate_mode",
    "mode_name",
    "is_dispatchable",
    "render_mode_template",
    "parse_mode_template",
    "template_path",
    "load_mode_template",
    "render_project_request",
]
