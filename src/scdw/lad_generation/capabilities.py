from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from scdw.common.paths import RAG_KNOWLEDGE_DIR
from scdw.rag import KnowledgeLibrary


CAPABILITY_FILE = "lad_capabilities.json"


class CapabilityCatalogError(ValueError):
    code = "KNOWLEDGE_GAP"

    def __init__(self, message: str, *, uncovered: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.uncovered = sorted(set(str(item) for item in uncovered))


@dataclass(frozen=True)
class LadCapability:
    capability_id: str
    name: str
    available: bool
    stateful: bool
    recommended_block: str
    knowledge_ids: tuple[str, ...]
    generation_mode: str
    renderer_id: str | None
    can_follow: tuple[str, ...]
    can_precede: tuple[str, ...]
    node_kinds: tuple[str, ...]
    required_operands: tuple[str, ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LadCapability":
        return cls(
            capability_id=str(value["capability_id"]),
            name=str(value["name"]),
            available=bool(value["available"]),
            stateful=bool(value["stateful"]),
            recommended_block=str(value["recommended_block"]),
            knowledge_ids=tuple(str(item) for item in value.get("knowledge_ids", [])),
            generation_mode=str(value["generation_mode"]),
            renderer_id=str(value["renderer_id"]) if value.get("renderer_id") else None,
            can_follow=tuple(str(item) for item in value.get("can_follow", [])),
            can_precede=tuple(str(item) for item in value.get("can_precede", [])),
            node_kinds=tuple(str(item) for item in value.get("node_kinds", [])),
            required_operands=tuple(str(item) for item in value.get("required_operands", [])),
            limitations=tuple(str(item) for item in value.get("limitations", [])),
        )

    def compact(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "available": self.available,
            "stateful": self.stateful,
            "recommended_block": self.recommended_block,
            "knowledge_ids": list(self.knowledge_ids),
            "generation_mode": self.generation_mode,
            "renderer_id": self.renderer_id,
            "can_follow": list(self.can_follow),
            "can_precede": list(self.can_precede),
            "node_kinds": list(self.node_kinds),
            "required_operands": list(self.required_operands),
            "limitations": list(self.limitations),
        }


class LadCapabilityCatalog:
    """Compact planning catalog; it contains no SimaticML bodies."""

    _instance: "LadCapabilityCatalog | None" = None

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or (RAG_KNOWLEDGE_DIR / CAPABILITY_FILE)).resolve()
        self.schema_version = 0
        self.tia_version = "V17"
        self._items: dict[str, LadCapability] = {}
        self._load()

    @classmethod
    def instance(cls) -> "LadCapabilityCatalog":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _load(self) -> None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CapabilityCatalogError(f"LAD capability catalog cannot be read: {exc}") from exc
        raw_items = document.get("capabilities")
        if not isinstance(raw_items, list):
            raise CapabilityCatalogError("lad_capabilities.json capabilities must be an array")
        items: dict[str, LadCapability] = {}
        for raw in raw_items:
            item = LadCapability.from_dict(raw)
            if item.capability_id in items:
                raise CapabilityCatalogError(f"duplicate LAD capability: {item.capability_id}")
            items[item.capability_id] = item
        knowledge = {str(item["id"]): item for item in KnowledgeLibrary.instance().catalog()["items"]}
        from scdw.xml_workspace.knowledge_networks import RENDERABLE_KINDS

        invalid: list[str] = []
        for item in items.values():
            if not item.available:
                continue
            if any(knowledge_id not in knowledge for knowledge_id in item.knowledge_ids):
                invalid.append(item.capability_id)
                continue
            if item.generation_mode == "renderer" and item.renderer_id not in RENDERABLE_KINDS:
                invalid.append(item.capability_id)
        if invalid:
            raise CapabilityCatalogError(
                "available LAD capabilities reference missing knowledge or renderer",
                uncovered=invalid,
            )
        self.schema_version = int(document.get("schema_version", 1))
        self.tia_version = str(document.get("tia_version", "V17"))
        self._items = items

    def get(self, capability_id: str) -> LadCapability:
        try:
            return self._items[capability_id]
        except KeyError as exc:
            raise CapabilityCatalogError(
                f"unknown LAD capability: {capability_id}", uncovered=[capability_id]
            ) from exc

    def compact(self) -> dict[str, Any]:
        values = [item.compact() for item in self._items.values()]
        encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "schema_version": self.schema_version,
            "tia_version": self.tia_version,
            "selection_mode": "capability_first",
            "catalog_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "capabilities": values,
        }

    def validate_node(self, node: Any) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        capability_id = getattr(node, "capability_id", None)
        children = list(getattr(node, "children", []) or [])
        if capability_id:
            try:
                capability = self.get(str(capability_id))
            except CapabilityCatalogError as exc:
                issues.append({"code": "UNKNOWN_CAPABILITY", "message": str(exc), "uncovered_capabilities": exc.uncovered})
            else:
                if not capability.available:
                    issues.append({
                        "code": "CAPABILITY_UNAVAILABLE",
                        "message": f"capability is catalogued but unavailable: {capability.capability_id}",
                        "uncovered_capabilities": [capability.capability_id],
                    })
                if capability.node_kinds and getattr(node, "kind", "") not in capability.node_kinds:
                    issues.append({
                        "code": "CAPABILITY_NODE_KIND_MISMATCH",
                        "message": f"{capability.capability_id} does not support node kind {getattr(node, 'kind', '')}",
                        "uncovered_capabilities": [capability.capability_id],
                    })
                operands = getattr(node, "operands", {}) or {}
                missing = [name for name in capability.required_operands if name not in operands]
                if missing:
                    issues.append({
                        "code": "OPERAND_PLAN_INCOMPLETE",
                        "message": f"{capability.capability_id} is missing operands: {', '.join(missing)}",
                        "uncovered_capabilities": [f"operand:{name}" for name in missing],
                    })
        elif not children:
            issues.append({
                "code": "CAPABILITY_MISSING",
                "message": f"leaf blueprint node {getattr(node, 'node_id', '')} has no capability_id",
                "uncovered_capabilities": ["capability_id"],
            })
        for child in children:
            issues.extend(self.validate_node(child))
        if getattr(node, "kind", "") in {"series", "branch"}:
            for previous, current in zip(children, children[1:]):
                if not previous.capability_id or not current.capability_id:
                    continue
                # Unknown capabilities have already produced a precise issue
                # above.  Do not turn their adjacency check into an unhandled
                # exception during blueprint preflight.
                if previous.capability_id not in self._items or current.capability_id not in self._items:
                    continue
                left, right = self._items[previous.capability_id], self._items[current.capability_id]
                # Metadata names a semantic node family.  For example
                # math.numeric supports both `math` and `calc`; do not reject
                # a valid calc merely because the catalog records its family
                # as math.  Parallel containers never reach this serial walk.
                right_kinds = set(right.node_kinds) | {str(current.kind)}
                left_kinds = set(left.node_kinds) | {str(previous.kind)}
                common = {
                    "source_node": previous.node_id, "target_node": current.node_id,
                    "source_capability": left.capability_id, "target_capability": right.capability_id,
                    "tree_path": [getattr(node, "node_id", ""), previous.node_id, current.node_id],
                    "suggestion": "place independent operations in different parallel branches when they are not serially dependent",
                }
                if left.can_precede and not (right_kinds & set(left.can_precede)):
                    issues.append({
                        "code": "CAPABILITY_CONNECTION_UNSUPPORTED",
                        "message": f"{previous.node_id} ({previous.kind}) cannot precede {current.node_id} ({current.kind})",
                        "uncovered_capabilities": [f"connection:{previous.kind}->{current.kind}"],
                        **common,
                    })
                if right.can_follow and not (left_kinds & set(right.can_follow)):
                    issues.append({
                        "code": "CAPABILITY_CONNECTION_UNSUPPORTED",
                        "message": f"{current.node_id} ({current.kind}) cannot follow {previous.node_id} ({previous.kind})",
                        "uncovered_capabilities": [f"connection:{previous.kind}->{current.kind}"],
                        **common,
                    })
        return issues
