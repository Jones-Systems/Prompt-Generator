"""Stable project, prompt, workflow, and repository-binding identities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import secrets
from typing import Any, Iterable, Mapping

from .contracts import (
    ContractError,
    IDENTIFIER_PATTERN,
    PROJECT_ID_PATTERN,
    QUALIFIED_PROMPT_ID_PATTERN,
    canonical_json_bytes,
)


class IdentityError(ContractError):
    """Raised when an identity or binding is malformed or collides."""


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


WORKFLOW_ALIASES: dict[str, str] = {
    "investigation": "research",
    "study": "research",
    "plan": "planning",
    "build": "implementation",
    "implement": "implementation",
    "test": "verification",
    "verify": "verification",
    "audit": "review",
    "merge": "integration",
    "resolve-conflict": "conflict-resolution",
    "fix": "correction",
    "continue": "resume",
    "transfer": "handoff",
}


_IDENTIFIER_RE = re.compile(rf"^{IDENTIFIER_PATTERN}$")
_PROJECT_ID_RE = re.compile(rf"^{PROJECT_ID_PATTERN}$")
_QUALIFIED_PROMPT_RE = re.compile(rf"^{QUALIFIED_PROMPT_ID_PATTERN}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_OPAQUE_BINDING_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,127}$")


def validate_identifier(value: str, *, field: str = "identifier") -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise IdentityError(f"invalid {field}: {value!r}")
    if re.fullmatch(r"P\d+", value, flags=re.IGNORECASE):
        raise IdentityError("reserved bare P<number> identity")
    return value


def canonical_workflow(value: str) -> str:
    if not isinstance(value, str):
        raise IdentityError("workflow must be a string")
    normalized = value.strip().lower()
    if normalized in CANONICAL_WORKFLOWS:
        return normalized
    try:
        return WORKFLOW_ALIASES[normalized]
    except KeyError as exc:
        raise IdentityError(f"unsupported workflow: {value!r}") from exc


def workflow_aliases() -> dict[str, str]:
    return dict(WORKFLOW_ALIASES)


def validate_project_id(value: str) -> str:
    if not isinstance(value, str) or _PROJECT_ID_RE.fullmatch(value) is None:
        raise IdentityError(f"invalid project ID: {value!r}")
    return value


def new_project_id() -> str:
    return "prj_" + secrets.token_hex(16)


def project_id_from_seed(seed: bytes | str) -> str:
    if isinstance(seed, str):
        seed = seed.encode("utf-8", "strict")
    if not isinstance(seed, bytes) or not seed:
        raise IdentityError("project seed must be non-empty bytes or text")
    return "prj_" + hashlib.sha256(seed).hexdigest()[:32]


def validate_local_id(value: str) -> str:
    return validate_identifier(value, field="local prompt ID")


def qualify_prompt_id(project_id: str, local_id: str) -> str:
    return f"{validate_project_id(project_id)}:{validate_local_id(local_id)}"


def validate_prompt_id(value: str) -> str:
    if not isinstance(value, str) or _QUALIFIED_PROMPT_RE.fullmatch(value) is None:
        raise IdentityError(f"invalid qualified prompt ID: {value!r}")
    project_id, local_id = value.split(":", 1)
    validate_project_id(project_id)
    validate_local_id(local_id)
    return value


def split_prompt_id(value: str) -> tuple[str, str]:
    value = validate_prompt_id(value)
    return tuple(value.split(":", 1))  # type: ignore[return-value]


@dataclass(frozen=True)
class RepositoryBinding:
    binding_id: str
    repository: str
    branch: str
    delivery: str = "task-branch"
    root: str | None = None
    base: str | None = None
    head: str | None = None
    workspace: str | None = None

    def __post_init__(self) -> None:
        if _OPAQUE_BINDING_RE.fullmatch(self.binding_id) is None:
            raise IdentityError(f"invalid repository binding ID: {self.binding_id!r}")
        if _REPOSITORY_RE.fullmatch(self.repository) is None:
            raise IdentityError(f"invalid repository: {self.repository!r}")
        if _BRANCH_RE.fullmatch(self.branch) is None:
            raise IdentityError(f"invalid branch: {self.branch!r}")
        if self.delivery not in {"task-branch", "draft-pr", "local"}:
            raise IdentityError(f"invalid delivery type: {self.delivery!r}")
        for name, value in (("root", self.root), ("base", self.base), ("head", self.head), ("workspace", self.workspace)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise IdentityError(f"invalid {name} binding value")

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {
            "binding_id": self.binding_id,
            "repository": self.repository,
            "branch": self.branch,
            "delivery": self.delivery,
        }
        for name in ("root", "base", "head", "workspace"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepositoryBinding":
        allowed = {"binding_id", "repository", "branch", "delivery", "root", "base", "head", "workspace"}
        unknown = set(value) - allowed
        if unknown:
            raise IdentityError(f"unknown repository binding field(s): {sorted(unknown)}")
        return cls(
            binding_id=value["binding_id"], repository=value["repository"], branch=value["branch"],
            delivery=value.get("delivery", "task-branch"), root=value.get("root"),
            base=value.get("base"), head=value.get("head"), workspace=value.get("workspace"),
        )


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    title: str
    bindings: tuple[RepositoryBinding, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_project_id(self.project_id)
        if not isinstance(self.title, str) or not self.title.strip():
            raise IdentityError("project title must be non-empty")
        ids = [binding.binding_id for binding in self.bindings]
        if len(ids) != len(set(ids)):
            raise IdentityError("duplicate repository binding ID")

    def with_binding(self, binding: RepositoryBinding) -> "ProjectRecord":
        if any(existing.binding_id == binding.binding_id for existing in self.bindings):
            raise IdentityError(f"repository binding already exists: {binding.binding_id}")
        return ProjectRecord(self.project_id, self.title, self.bindings + (binding,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "bindings": [binding.to_dict() for binding in self.bindings],
        }


class IdentityRegistry:
    """In-memory project registry; repository bindings never define identity."""

    def __init__(self, projects: Iterable[ProjectRecord] = ()) -> None:
        self._projects: dict[str, ProjectRecord] = {}
        self._binding_keys: dict[tuple[str, str, str], str] = {}
        for project in projects:
            self.register(project)

    @property
    def projects(self) -> tuple[ProjectRecord, ...]:
        return tuple(self._projects[key] for key in sorted(self._projects))

    def register(self, project: ProjectRecord) -> ProjectRecord:
        validate_project_id(project.project_id)
        if project.project_id in self._projects:
            raise IdentityError(f"project already exists: {project.project_id}")
        for binding in project.bindings:
            self._check_binding_collision(project.project_id, binding)
        self._projects[project.project_id] = project
        for binding in project.bindings:
            self._binding_keys[self._binding_key(binding)] = project.project_id
        return project

    def create_project(self, title: str, *, project_id: str | None = None) -> ProjectRecord:
        record = ProjectRecord(project_id or new_project_id(), title)
        self.register(record)
        return record

    def bind_repository(self, project_id: str, binding: RepositoryBinding) -> ProjectRecord:
        project = self.get_project(project_id)
        self._check_binding_collision(project_id, binding)
        updated = project.with_binding(binding)
        self._projects[project_id] = updated
        self._binding_keys[self._binding_key(binding)] = project_id
        return updated

    def get_project(self, project_id: str) -> ProjectRecord:
        validate_project_id(project_id)
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise IdentityError(f"unknown project: {project_id}") from exc

    def project_for_binding(self, repository: str, branch: str, root: str | None = None) -> ProjectRecord:
        key = (repository, branch, root or "")
        try:
            return self.get_project(self._binding_keys[key])
        except KeyError as exc:
            raise IdentityError("unknown repository binding") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "identity-registry", "projects": [project.to_dict() for project in self.projects]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IdentityRegistry":
        if set(value) != {"schema_version", "kind", "projects"}:
            raise IdentityError("identity registry has unknown or missing fields")
        if value["schema_version"] != 1 or value["kind"] != "identity-registry":
            raise IdentityError("unsupported identity registry version")
        projects = []
        for item in value["projects"]:
            allowed = {"project_id", "title", "bindings"}
            if set(item) - allowed or not {"project_id", "title", "bindings"} <= set(item):
                raise IdentityError("malformed project record")
            projects.append(ProjectRecord(item["project_id"], item["title"], tuple(RepositoryBinding.from_dict(binding) for binding in item["bindings"])))
        return cls(projects)

    @staticmethod
    def _binding_key(binding: RepositoryBinding) -> tuple[str, str, str]:
        return binding.repository, binding.branch, binding.root or ""

    def _check_binding_collision(self, project_id: str, binding: RepositoryBinding) -> None:
        owner = self._binding_keys.get(self._binding_key(binding))
        if owner is not None and owner != project_id:
            raise IdentityError("repository binding is already owned by another project")


def identity_digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()
