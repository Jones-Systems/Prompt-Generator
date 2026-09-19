"""Public coordination and stacked-branch contracts.

These contracts describe readiness and evidence.  They do not control GitHub,
branches, checks, or merges.  A sibling is the default for independent work;
an exact stack is required only when a real dependency or overlapping contract
needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .contracts import canonical_json_bytes


class CoordinationError(ValueError):
    """Base class for coordination-contract failures."""


class CoordinationUnavailableError(CoordinationError):
    """Raised when required coordination evidence is silent or unavailable."""


class ParentInvalidatedError(CoordinationError):
    """Raised when a stack parent no longer matches its exact recorded OID."""


def _text(value: Any, *, name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise CoordinationError(f"{name} must be {'text' if allow_empty else 'non-empty text'}")
    return value


def _text_sequence(value: Any, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise CoordinationError(f"{name} must be an array")
    result = tuple(value)
    for item in result:
        _text(item, name=f"{name} entry")
    return result


def _boolean(value: Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise CoordinationError(f"{name} must be boolean")
    return value


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str
    evidence: str | None = None

    def __post_init__(self) -> None:
        _text(self.gate_id, name="gate_id")
        if not isinstance(self.status, str) or self.status not in {"pass", "fail", "pending", "unavailable", "silent"}:
            raise CoordinationError(f"unsupported gate status: {self.status}")
        if self.evidence is not None:
            _text(self.evidence, name="gate evidence")

    def to_dict(self) -> dict[str, Any]:
        result = {"gate_id": self.gate_id, "status": self.status}
        if self.evidence is not None:
            result["evidence"] = self.evidence
        return result


@dataclass(frozen=True)
class CoordinationDecision:
    ready: bool
    reason: str
    failures: tuple[str, ...] = ()
    mode: str | None = None

    def __bool__(self) -> bool:
        return self.ready

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ready": self.ready, "reason": self.reason, "failures": list(self.failures)}
        if self.mode is not None:
            result["mode"] = self.mode
        return result


@dataclass(frozen=True)
class CoordinationGate:
    coordinator: str
    participants: tuple[str, ...]
    gates: tuple[GateResult, ...]
    required_participants: tuple[str, ...] = ()
    fictional_coordinator: bool = False

    def __post_init__(self) -> None:
        _text(self.coordinator, name="coordinator", allow_empty=True)
        _text_sequence(self.participants, name="participants")
        _text_sequence(self.required_participants, name="required_participants")
        if not isinstance(self.gates, (list, tuple)):
            raise CoordinationError("gates must be an array")
        if any(not isinstance(gate, GateResult) for gate in self.gates):
            raise CoordinationError("gates must contain GateResult values")
        gate_ids = [gate.gate_id for gate in self.gates]
        if len(gate_ids) != len(set(gate_ids)):
            duplicates = sorted({gate_id for gate_id in gate_ids if gate_ids.count(gate_id) > 1})
            raise CoordinationError(f"duplicate gate_id: {', '.join(duplicates)}")
        _boolean(self.fictional_coordinator, name="fictional_coordinator")

    @classmethod
    def from_value(cls, value: "CoordinationGate | Mapping[str, Any]") -> "CoordinationGate":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise CoordinationError("coordination gate must be an object")
        participants = _text_sequence(value.get("participants", ()), name="participants")
        required = _text_sequence(value.get("required_participants", participants), name="required_participants")
        raw = value.get("gates", value.get("responses", ()))
        if isinstance(raw, Mapping):
            raw = tuple({"gate_id": key, **(item if isinstance(item, Mapping) else {"status": item})} for key, item in raw.items())
        if not isinstance(raw, (list, tuple)):
            raise CoordinationError("gates must be an array")
        gates: list[GateResult] = []
        for item in raw:
            if isinstance(item, GateResult):
                gates.append(item)
                continue
            if not isinstance(item, Mapping):
                raise CoordinationError("gate entries must be objects")
            gate_id = item.get("gate_id", item.get("id", ""))
            gates.append(GateResult(gate_id, item.get("status", "unavailable"), item.get("evidence")))
        coordinator = value.get("coordinator", "")
        if not isinstance(coordinator, str):
            raise CoordinationError("coordinator must be text")
        fictional = value.get("fictional_coordinator", False)
        return cls(coordinator, participants, tuple(gates), required, _boolean(fictional, name="fictional_coordinator"))


_FICTIONAL = {"fictional", "fictional-coordinator", "imaginary", "placeholder", "none", "unknown"}


def validate_coordination(value: CoordinationGate | Mapping[str, Any]) -> CoordinationDecision:
    gate = CoordinationGate.from_value(value)
    failures: list[str] = []
    coordinator = gate.coordinator.strip().casefold()
    if gate.fictional_coordinator or not coordinator or coordinator in _FICTIONAL:
        failures.append("coordinator is fictional or missing")
    if gate.coordinator not in gate.participants:
        failures.append("coordinator is not a declared participant")
    statuses = {item.gate_id: item for item in gate.gates}
    for participant in gate.required_participants:
        item = statuses.get(participant)
        if item is None:
            failures.append(f"silent required gate: {participant}")
        elif item.status in {"silent", "pending"}:
            failures.append(f"silent required gate: {participant}")
        elif item.status == "unavailable":
            failures.append(f"unavailable required gate: {participant}")
        elif item.status == "fail":
            failures.append(f"failed required gate: {participant}")
    for item in gate.gates:
        if item.status == "unavailable" and item.gate_id not in gate.required_participants:
            failures.append(f"unavailable gate: {item.gate_id}")
    if failures:
        return CoordinationDecision(False, "coordination is not ready", tuple(dict.fromkeys(failures)))
    return CoordinationDecision(True, "coordination gates are satisfied")


def require_coordination(value: CoordinationGate | Mapping[str, Any]) -> CoordinationDecision:
    decision = validate_coordination(value)
    if not decision:
        if any("unavailable" in failure or "silent" in failure for failure in decision.failures):
            raise CoordinationUnavailableError("; ".join(decision.failures))
        raise CoordinationError("; ".join(decision.failures))
    return decision


@dataclass(frozen=True)
class StackLayer:
    branch: str
    oid: str
    layer: int
    path_prefix: str
    checks: tuple[str, ...] = ()
    cumulative_checks: tuple[str, ...] = ()
    parent_branch: str | None = None
    parent_oid: str | None = None
    parent_current_oid: str | None = None
    parent_invalidated: bool = False

    def __post_init__(self) -> None:
        _text(self.branch, name="branch", allow_empty=True)
        _text(self.oid, name="oid", allow_empty=True)
        if isinstance(self.layer, bool) or not isinstance(self.layer, int):
            raise CoordinationError("layer must be an integer")
        _text(self.path_prefix, name="path_prefix", allow_empty=True)
        _text_sequence(self.checks, name="checks")
        _text_sequence(self.cumulative_checks, name="cumulative_checks")
        for name in ("parent_branch", "parent_oid", "parent_current_oid"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name=name)
        _boolean(self.parent_invalidated, name="parent_invalidated")

    @classmethod
    def from_value(cls, value: "StackLayer | Mapping[str, Any]") -> "StackLayer":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise CoordinationError("stack layer must be an object")
        parent = value.get("parent", {})
        if not isinstance(parent, Mapping):
            parent = {}
        branch = value.get("branch", "")
        oid = value.get("oid", value.get("head", ""))
        layer = value.get("layer", 0)
        path_prefix = value.get("path_prefix", value.get("prefix", ""))
        checks = value.get("checks", ())
        cumulative_checks = value.get("cumulative_checks", value.get("cumulative", ()))
        parent_branch = value.get("parent_branch", parent.get("branch"))
        parent_oid = value.get("parent_oid", parent.get("oid"))
        parent_current_oid = value.get("parent_current_oid", value.get("current_parent_oid", parent.get("current_oid")))
        parent_invalidated = value.get("parent_invalidated", False)
        if not isinstance(branch, str) or not isinstance(oid, str) or not isinstance(path_prefix, str):
            raise CoordinationError("stack layer branch, OID, and path_prefix must be text")
        if isinstance(layer, bool) or not isinstance(layer, int):
            raise CoordinationError("stack layer number must be integer")
        if not isinstance(parent_invalidated, bool):
            raise CoordinationError("parent_invalidated must be boolean")
        return cls(
            branch, oid, layer, path_prefix, _text_sequence(checks, name="checks"),
            _text_sequence(cumulative_checks, name="cumulative_checks"), parent_branch,
            parent_oid, parent_current_oid, parent_invalidated,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "branch": self.branch,
            "oid": self.oid,
            "layer": self.layer,
            "path_prefix": self.path_prefix,
            "checks": list(self.checks),
            "cumulative_checks": list(self.cumulative_checks),
            "parent_invalidated": self.parent_invalidated,
        }
        for key in ("parent_branch", "parent_oid", "parent_current_oid"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


def _prefix_is_contained(parent: str, child: str) -> bool:
    parent = parent.rstrip("/")
    child = child.rstrip("/")
    return child == parent or child.startswith(parent + "/")


def validate_stack(layers: Iterable[StackLayer | Mapping[str, Any]]) -> CoordinationDecision:
    """Validate exact parent custody and cumulative obligations."""

    normalized = tuple(StackLayer.from_value(layer) for layer in layers)
    failures: list[str] = []
    if not normalized:
        return CoordinationDecision(False, "stack has no layers", ("missing layer",), "stack")
    by_branch: dict[str, StackLayer] = {}
    prior: StackLayer | None = None
    for expected_layer, layer in enumerate(normalized, 1):
        if not layer.branch or not layer.oid:
            failures.append(f"layer {expected_layer} is missing branch or OID")
        if layer.layer != expected_layer:
            failures.append(f"layer numbering is not contiguous at {layer.branch}")
        if layer.branch in by_branch:
            failures.append(f"duplicate stack branch: {layer.branch}")
        by_branch[layer.branch] = layer
        if not layer.path_prefix or ".." in layer.path_prefix.split("/"):
            failures.append(f"unsafe or missing path prefix: {layer.branch}")
        if prior is None:
            if layer.parent_branch is not None or layer.parent_oid is not None or layer.parent_current_oid is not None:
                failures.append("root stack layer unexpectedly has a parent")
        else:
            if layer.parent_branch != prior.branch:
                failures.append(f"parent branch mismatch for {layer.branch}")
            if layer.parent_oid != prior.oid:
                failures.append(f"exact parent OID mismatch for {layer.branch}")
            if layer.parent_current_oid is None:
                failures.append(f"missing current parent OID for {layer.branch}")
            elif layer.parent_current_oid != layer.parent_oid:
                failures.append(f"parent invalidated for {layer.branch}")
            if layer.parent_invalidated:
                failures.append(f"parent explicitly invalidated for {layer.branch}")
            if not _prefix_is_contained(prior.path_prefix, layer.path_prefix):
                failures.append(f"path prefix is not contained by parent for {layer.branch}")
            required = set(prior.cumulative_checks) | set(prior.checks)
            if not required.issubset(set(layer.cumulative_checks)):
                failures.append(f"cumulative checks omit parent obligations for {layer.branch}")
        if not set(layer.checks).issubset(set(layer.cumulative_checks)):
            failures.append(f"cumulative checks omit layer checks for {layer.branch}")
        prior = layer
    if failures:
        return CoordinationDecision(False, "stack contract is not ready", tuple(dict.fromkeys(failures)), "stack")
    return CoordinationDecision(True, "stack contract is ready", (), "stack")


def validate_sibling_or_stack(
    value: Mapping[str, Any],
    *,
    changed_paths: Iterable[str] | None = None,
    sibling_paths: Iterable[str] | None = None,
) -> CoordinationDecision:
    """Choose sibling by default and require a valid stack for dependency."""

    mode = value.get("mode")
    if mode is None:
        overlap = bool(set(changed_paths or ()) & set(sibling_paths or ()))
        mode = "stack" if overlap or value.get("depends_on") else "sibling"
    if mode == "sibling":
        if value.get("depends_on") or value.get("overlap"):
            return CoordinationDecision(False, "dependent or overlapping work must be stacked", ("sibling contract is not independent",), "sibling")
        return CoordinationDecision(True, "independent work may proceed as sibling", (), "sibling")
    if mode != "stack":
        return CoordinationDecision(False, f"unsupported coordination mode: {mode}", ("unknown mode",))
    return validate_stack(value.get("layers", value.get("stack", ())))


validate_coordination_gate = validate_coordination
validate_stack_contract = validate_stack
