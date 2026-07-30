from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from scdw.common.paths import XML_ARTIFACTS_DIR, XML_ARTIFACT_TTL_HOURS

from .models import (
    NETWORK_STATES,
    ArtifactError,
    ArtifactMetadata,
    FragmentResult,
    PatchOperation,
    PatchResult,
    ValidationResult,
)
from .patching import apply_operations
from .store import ArtifactStore
from .validation import validate_xml


_COMPILE_UNIT = re.compile(r"<SW\.Blocks\.CompileUnit\b.*?</SW\.Blocks\.CompileUnit>", re.DOTALL)
_NETWORK_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class XmlArtifactService:
    """Immutable XML versions plus stable Network identities kept outside SimaticML."""

    def __init__(self, root: Path | None = None, *, ttl_hours: int = XML_ARTIFACT_TTL_HOURS) -> None:
        self.store = ArtifactStore(root or XML_ARTIFACTS_DIR)
        self.ttl_hours = ttl_hours
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, artifact_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(artifact_id, threading.RLock())

    def _active(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        if datetime.fromisoformat(metadata.expires_at.replace("Z", "+00:00")) <= _now():
            raise ArtifactError("ARTIFACT_EXPIRED", "artifact has expired")
        return metadata

    @staticmethod
    def _units(content: str) -> list[dict]:
        return [
            {"index": index, "xml": match.group(0), "start": match.start(), "end": match.end()}
            for index, match in enumerate(_COMPILE_UNIT.finditer(content))
        ]

    @staticmethod
    def _derived_key(unit: str, used: set[str]) -> str:
        base = "network_" + _digest(unit)[:12]
        key, suffix = base, 2
        while key in used:
            key, suffix = f"{base}_{suffix}", suffix + 1
        return key

    def _network_keys(self, artifact_id: str, version: int, units: list[dict]) -> list[str]:
        sidecar = self.store.network_index(artifact_id, version)
        if len(sidecar) == len(units) and all(_NETWORK_KEY.fullmatch(str(item.get("network_key", ""))) for item in sidecar):
            return [str(item["network_key"]) for item in sidecar]
        used: set[str] = set()
        result: list[str] = []
        for item in units:
            key = self._derived_key(item["xml"], used)
            used.add(key)
            result.append(key)
        return result

    def _snapshot(self, artifact_id: str, version: int | None = None) -> tuple[ArtifactMetadata, int, str, list[dict]]:
        metadata = self.get_artifact(artifact_id)
        used = version or metadata.current_version
        try:
            content = self.store.version_path(artifact_id, used).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ArtifactError("XML_READ_FAILED", str(exc)) from exc
        units = self._units(content)
        keys = self._network_keys(artifact_id, used, units)
        for item, key in zip(units, keys, strict=True):
            item["network_key"] = key
        return metadata, used, content, units

    @staticmethod
    def _validate_network_keys(network_keys: list[str], count: int) -> None:
        if len(network_keys) != count or len(set(network_keys)) != len(network_keys):
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "Network sidecar keys must be unique and match CompileUnits")
        if any(not _NETWORK_KEY.fullmatch(key) for key in network_keys):
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "invalid network key")

    def create_artifact(
        self,
        xml_content: str,
        block_name: str | None = None,
        device_name: str | None = None,
        conversation_id: str | None = None,
        change_source: str | None = "create",
        *,
        plan_id: str | None = None,
        block_type: str | None = None,
        network_keys: list[str] | None = None,
    ) -> ArtifactMetadata:
        validation = validate_xml(xml_content)
        if not validation.valid:
            raise ArtifactError("XML_VALIDATION_FAILED", "XML must be non-empty, reasonably sized, UTF-8 encodable and parseable")
        units = self._units(xml_content)
        keys = list(network_keys or [])
        if not network_keys:
            used: set[str] = set()
            for item in units:
                key = self._derived_key(item["xml"], used)
                keys.append(key)
                used.add(key)
        self._validate_network_keys(keys, len(units))
        artifact_id = "xml_" + secrets.token_hex(12)
        stamp = _now()
        states = {key: "import_pending" for key in keys}
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            block_name=block_name,
            device_name=device_name,
            current_version=1,
            status="import_pending" if keys else "artifact_created",
            created_at=_iso(stamp),
            updated_at=_iso(stamp),
            expires_at=_iso(stamp + timedelta(hours=self.ttl_hours)),
            last_validation=validation.to_dict(),
            conversation_id=conversation_id,
            change_source=change_source,
            affected_networks=keys,
            network_states=states,
            plan_id=plan_id,
            block_type=block_type,
        )
        directory = self.store.artifact_dir(artifact_id)
        directory.mkdir(parents=True, exist_ok=False)
        self.store.write_version(artifact_id, 1, xml_content)
        self.store.write_network_index(artifact_id, 1, keys, [_digest(item["xml"]) for item in units])
        self.store.write_metadata(metadata)
        return metadata

    def create_block_artifact(
        self,
        block_name: str,
        block_type: str = "FC",
        *,
        interface_xml: str | None = None,
        device_name: str | None = None,
        conversation_id: str | None = None,
        plan_id: str | None = None,
    ) -> ArtifactMetadata:
        kind = block_type.upper()
        if kind not in {"FC", "FB"}:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "block_type must be FC or FB")
        if not block_name.strip():
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "block_name must not be empty")
        if interface_xml is None:
            sections = '<Section Name="Input" /><Section Name="Output" /><Section Name="InOut" />'
            if kind == "FB":
                sections += '<Section Name="Static" /><Section Name="Temp" /><Section Name="Constant" />'
            else:
                sections += '<Section Name="Temp" /><Section Name="Constant" /><Section Name="Return"><Member Name="Ret_Val" Datatype="Void" Accessibility="Public" /></Section>'
            interface_xml = f'<Interface><Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">{sections}</Sections></Interface>'
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n<Document>\n  <Engineering version="V17" />\n'
            f'  <SW.Blocks.{kind} ID="0">\n    <AttributeList>\n      {interface_xml}\n'
            f'      <MemoryLayout>Optimized</MemoryLayout><Name>{escape(block_name)}</Name><ProgrammingLanguage>LAD</ProgrammingLanguage>\n'
            f'    </AttributeList>\n    <ObjectList>\n    </ObjectList>\n  </SW.Blocks.{kind}>\n</Document>\n'
        )
        return self.create_artifact(
            xml,
            block_name,
            device_name,
            conversation_id,
            "create_block_framework",
            plan_id=plan_id,
            block_type=kind,
            network_keys=[],
        )

    def get_artifact(self, artifact_id: str) -> ArtifactMetadata:
        return self._active(self.store.metadata(artifact_id))

    def get_block_info(self, artifact_id: str, version: int | None = None) -> dict:
        metadata, used, content, units = self._snapshot(artifact_id, version)
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ArtifactError("XML_VALIDATION_FAILED", str(exc)) from exc
        block = next((item for item in root.iter() if _local_name(item.tag) in {"SW.Blocks.FC", "SW.Blocks.FB"}), None)
        block_type = _local_name(block.tag).rsplit(".", 1)[-1] if block is not None else metadata.block_type
        name = next((item.text for item in block.iter() if _local_name(item.tag) == "Name"), None) if block is not None else metadata.block_name
        language = next((item.text for item in block.iter() if _local_name(item.tag) == "ProgrammingLanguage"), None) if block is not None else None
        sections = []
        if block is not None:
            for section in (item for item in block.iter() if _local_name(item.tag) == "Section"):
                members = [child.attrib.get("Name") for child in list(section) if _local_name(child.tag) == "Member"]
                sections.append({"name": section.attrib.get("Name"), "members": members})
        return {
            "artifact_id": artifact_id,
            "version": used,
            "plan_id": metadata.plan_id,
            "block_name": name,
            "block_type": block_type,
            "programming_language": language,
            "interface_sections": sections,
            "network_count": len(units),
        }

    def read_fragment(self, artifact_id: str, version: int | None = None, search: str | None = None, start_line: int | None = None, end_line: int | None = None, context_lines: int = 10, max_chars: int = 12000) -> FragmentResult:
        if max_chars < 1:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "max_chars must be positive")
        _, used, content, _ = self._snapshot(artifact_id, version)
        lines = content.splitlines(keepends=True)
        if search:
            match = next((index for index, line in enumerate(lines, 1) if search in line), None)
            if match is None:
                raise ArtifactError("PATCH_PRECONDITION_FAILED", "search text not found")
            first, last = max(1, match - context_lines), min(len(lines), match + context_lines)
        else:
            first, last = start_line or 1, end_line or len(lines)
            if first < 1 or last < first:
                raise ArtifactError("PATCH_PRECONDITION_FAILED", "invalid line range")
            last = min(last, len(lines))
        fragment = "".join(lines[first - 1:last])
        truncated = len(fragment) > max_chars
        if truncated:
            fragment = fragment[:max_chars]
        return FragmentResult(artifact_id, used, first, last, fragment, _digest(fragment), truncated)

    def validate_artifact(self, artifact_id: str, version: int | None = None) -> ValidationResult:
        metadata, used, content, _ = self._snapshot(artifact_id, version)
        result = validate_xml(content)
        if used == metadata.current_version:
            self.store.write_metadata(replace(metadata, last_validation=result.to_dict(), updated_at=_iso(_now()), status=metadata.status if result.valid else "invalid"))
        return result

    def _write_version(self, metadata: ArtifactMetadata, content: str, *, source: str, affected: list[str], network_keys: list[str], states: dict[str, str] | None = None) -> int:
        validation = validate_xml(content)
        if not validation.valid:
            raise ArtifactError("XML_VALIDATION_FAILED", "edited XML is not parseable")
        units = self._units(content)
        self._validate_network_keys(network_keys, len(units))
        new_version = metadata.current_version + 1
        self.store.write_version(metadata.artifact_id, new_version, content)
        self.store.write_network_index(metadata.artifact_id, new_version, network_keys, [_digest(item["xml"]) for item in units])
        updated_states = {key: value for key, value in metadata.network_states.items() if key in network_keys}
        updated_states.update(states or {})
        self.store.write_metadata(replace(
            metadata,
            current_version=new_version,
            updated_at=_iso(_now()),
            status="import_pending" if affected else metadata.status,
            last_validation=validation.to_dict(),
            change_source=source,
            affected_networks=affected,
            network_states=updated_states,
        ))
        return new_version

    @staticmethod
    def _reconcile_keys(old_units: list[dict], new_units: list[dict], affected: list[str]) -> list[str]:
        old_keys = [item["network_key"] for item in old_units]
        by_hash: dict[str, list[str]] = {}
        for item in old_units:
            by_hash.setdefault(_digest(item["xml"]), []).append(item["network_key"])
        used: set[str] = set()
        result: list[str] = []
        for index, item in enumerate(new_units):
            matches = by_hash.get(_digest(item["xml"]), [])
            key = next((candidate for candidate in matches if candidate not in used), None)
            if key is None:
                preferred = next((candidate for candidate in affected if candidate not in used and _NETWORK_KEY.fullmatch(candidate)), None)
                positional = old_keys[index] if index < len(old_keys) and old_keys[index] not in used else None
                key = preferred or positional or XmlArtifactService._derived_key(item["xml"], used)
            used.add(key)
            result.append(key)
        return result

    def apply_patch(self, artifact_id: str, expected_version: int, operations: list[PatchOperation], *, change_source: str = "patch", affected_networks: list[str] | None = None) -> PatchResult:
        with self._lock(artifact_id):
            metadata, version, content, old_units = self._snapshot(artifact_id)
            if expected_version != version:
                raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            patched, changes = apply_operations(content, operations)
            affected = affected_networks or []
            keys = self._reconcile_keys(old_units, self._units(patched), affected)
            states = {key: "import_pending" for key in affected}
            new_version = self._write_version(metadata, patched, source=change_source, affected=affected, network_keys=keys, states=states)
            path = self.store.artifact_dir(artifact_id) / "patches" / f"v{version:04d}_to_v{new_version:04d}.json"
            self.store._atomic(path, json.dumps({
                "artifact_id": artifact_id,
                "from_version": version,
                "to_version": new_version,
                "source": change_source,
                "affected_networks": affected,
                "operations": [operation.to_dict() for operation in operations],
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            return PatchResult(artifact_id, version, new_version, changes, validate_xml(patched))

    def list_networks(self, artifact_id: str, version: int | None = None) -> list[dict]:
        metadata, _, _, units = self._snapshot(artifact_id, version)
        return [
            {"index": item["index"], "network_key": item["network_key"], "status": metadata.network_states.get(item["network_key"], "generated")}
            for item in units
        ]

    def get_network(self, artifact_id: str, network_key: str, version: int | None = None) -> str:
        _, _, _, units = self._snapshot(artifact_id, version)
        item = next((unit for unit in units if unit["network_key"] == network_key), None)
        if item is None:
            raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
        return item["xml"]

    @staticmethod
    def _complete_compile_unit(compile_unit_xml: str) -> str:
        value = compile_unit_xml.strip()
        if not _COMPILE_UNIT.fullmatch(value):
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "compile_unit_xml must contain one complete CompileUnit")
        try:
            ET.fromstring(value)
        except ET.ParseError as exc:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", f"CompileUnit is not well-formed: {exc}") from exc
        return value

    def append_network(self, artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str, *, before_key: str | None = None, position: int | None = None, source: str = "incremental_generation") -> int:
        if not _NETWORK_KEY.fullmatch(network_key):
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "invalid network key")
        unit = self._complete_compile_unit(compile_unit_xml)
        if before_key is not None and position is not None:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "use before_key or position, not both")
        with self._lock(artifact_id):
            metadata, version, content, units = self._snapshot(artifact_id)
            if version != expected_version:
                raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            keys = [item["network_key"] for item in units]
            if network_key in keys:
                raise ArtifactError("NETWORK_KEY_CONFLICT", "network key already exists")
            if before_key is not None:
                target = next((item for item in units if item["network_key"] == before_key), None)
                if target is None:
                    raise ArtifactError("NETWORK_NOT_FOUND", "before_key was not found")
                insert_at, key_index = target["start"], target["index"]
            elif position is not None:
                if position < 0 or position > len(units):
                    raise ArtifactError("PATCH_PRECONDITION_FAILED", "position is outside the Network list")
                insert_at = units[position]["start"] if position < len(units) else content.rfind("</ObjectList>")
                key_index = position
            else:
                insert_at, key_index = content.rfind("</ObjectList>"), len(keys)
            if insert_at < 0:
                raise ArtifactError("PATCH_PRECONDITION_FAILED", "ObjectList closing tag was not found")
            content = content[:insert_at] + unit + "\n" + content[insert_at:]
            keys.insert(key_index, network_key)
            return self._write_version(metadata, content, source=source, affected=[network_key], network_keys=keys, states={network_key: "import_pending"})

    def replace_network(self, artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str, *, source: str = "local_revision") -> int:
        replacement = self._complete_compile_unit(compile_unit_xml)
        with self._lock(artifact_id):
            metadata, version, content, units = self._snapshot(artifact_id)
            if version != expected_version:
                raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            item = next((unit for unit in units if unit["network_key"] == network_key), None)
            if item is None:
                raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
            content = content[:item["start"]] + replacement + content[item["end"]:]
            keys = [unit["network_key"] for unit in units]
            return self._write_version(metadata, content, source=source, affected=[network_key], network_keys=keys, states={network_key: "import_pending"})

    def delete_network(self, artifact_id: str, expected_version: int, network_key: str) -> int:
        with self._lock(artifact_id):
            metadata, version, content, units = self._snapshot(artifact_id)
            if version != expected_version:
                raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            item = next((unit for unit in units if unit["network_key"] == network_key), None)
            if item is None:
                raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
            content = content[:item["start"]] + content[item["end"]:]
            keys = [unit["network_key"] for unit in units if unit["network_key"] != network_key]
            return self._write_version(metadata, content, source="delete_network", affected=[network_key], network_keys=keys)

    @staticmethod
    def _next_object_id(content: str) -> int:
        values = [int(value) for value in re.findall(r"(?<!U)ID=\"(\d+)\"", content)]
        return max(values, default=0) + 1

    @classmethod
    def _set_network_text(cls, unit: str, field: str, text: str, next_id: int) -> str:
        pattern = re.compile(rf'<MultilingualText\b(?=[^>]*\bCompositionName="{field}")[^>]*>.*?</MultilingualText>', re.DOTALL)
        match = pattern.search(unit)
        encoded = escape(text)
        if match:
            element = match.group(0)
            changed, count = re.subn(r"(<Text>).*?(</Text>)", rf"\g<1>{encoded}\g<2>", element, count=1, flags=re.DOTALL)
            if count != 1:
                raise ArtifactError("PATCH_PRECONDITION_FAILED", f"{field} element has no Text node")
            return unit[:match.start()] + changed + unit[match.end():]
        node = (
            f'<MultilingualText ID="{next_id}" CompositionName="{field}"><ObjectList>'
            f'<MultilingualTextItem ID="{next_id + 1}" CompositionName="Items"><AttributeList>'
            f'<Culture>zh-CN</Culture><Text>{encoded}</Text></AttributeList></MultilingualTextItem>'
            f'</ObjectList></MultilingualText>'
        )
        closing = unit.rfind("</ObjectList>")
        if closing >= 0:
            return unit[:closing] + node + unit[closing:]
        marker = "</SW.Blocks.CompileUnit>"
        position = unit.rfind(marker)
        return unit[:position] + f"<ObjectList>{node}</ObjectList>" + unit[position:]

    def update_network_text(self, artifact_id: str, expected_version: int, network_key: str, *, title: str | None = None, comment: str | None = None) -> int:
        if title is None and comment is None:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", "title or comment is required")
        with self._lock(artifact_id):
            metadata, version, content, units = self._snapshot(artifact_id)
            if version != expected_version:
                raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            item = next((unit for unit in units if unit["network_key"] == network_key), None)
            if item is None:
                raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
            replacement = item["xml"]
            next_id = self._next_object_id(content)
            if title is not None:
                replacement = self._set_network_text(replacement, "Title", title, next_id)
                next_id += 2
            if comment is not None:
                replacement = self._set_network_text(replacement, "Comment", comment, next_id)
            content = content[:item["start"]] + replacement + content[item["end"]:]
            keys = [unit["network_key"] for unit in units]
            return self._write_version(metadata, content, source="network_text", affected=[network_key], network_keys=keys, states={network_key: "import_pending"})

    def set_network_state(self, artifact_id: str, network_key: str, state: str) -> ArtifactMetadata:
        if state not in NETWORK_STATES:
            raise ArtifactError("INVALID_NETWORK_STATE", "unsupported network state")
        with self._lock(artifact_id):
            metadata, _, _, units = self._snapshot(artifact_id)
            if network_key not in {item["network_key"] for item in units}:
                raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
            states = dict(metadata.network_states)
            states[network_key] = state
            updated = replace(metadata, network_states=states, updated_at=_iso(_now()), affected_networks=[network_key], change_source="network_state")
            self.store.write_metadata(updated)
            return updated

    def set_workflow_state(self, artifact_id: str, state: str, network_key: str | None = None) -> ArtifactMetadata:
        if state not in NETWORK_STATES:
            raise ArtifactError("INVALID_NETWORK_STATE", "unsupported workflow state")
        with self._lock(artifact_id):
            metadata = self.get_artifact(artifact_id)
            states = dict(metadata.network_states)
            if network_key:
                if network_key not in states:
                    raise ArtifactError("NETWORK_NOT_FOUND", "network key was not found")
                states[network_key] = state
            updated = replace(metadata, status=state, network_states=states, updated_at=_iso(_now()), affected_networks=[network_key] if network_key else [], change_source="workflow_state")
            self.store.write_metadata(updated)
            return updated

    def record_import_result(self, artifact_id: str, version: int, success: bool, code: str, message: str, diagnostics: list[dict] | None = None, *, stage: str = "tia_import", target: dict | None = None, network_key: str | None = None) -> ArtifactMetadata:
        with self._lock(artifact_id):
            metadata = self.get_artifact(artifact_id)
            self.store.version_path(artifact_id, version)
            target = target or {}
            record = {
                "success": success,
                "stage": stage,
                "artifact_id": artifact_id,
                "version": version,
                "plan_id": metadata.plan_id,
                "block_name": metadata.block_name,
                "block_type": metadata.block_type,
                "device_name": target.get("device_name") or target.get("plc") or metadata.device_name,
                "network_key": network_key,
                "code": code,
                "message": message,
                "target": target,
                "recorded_at": _iso(_now()),
                "messages": diagnostics or [],
            }
            path = self.store.artifact_dir(artifact_id) / "diagnostics" / f"import_v{version:04d}.json"
            self.store._atomic(path, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            attempt = self.store.artifact_dir(artifact_id) / "diagnostics" / f"import_v{version:04d}_{secrets.token_hex(4)}.json"
            self.store._atomic(attempt, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            status = "imported" if success else "import_failed"
            states = dict(metadata.network_states)
            if network_key in states:
                states[network_key] = status
            updated = replace(metadata, status=status, network_states=states, updated_at=_iso(_now()), last_import=record)
            self.store.write_metadata(updated)
            return updated

    def record_compile_result(self, artifact_id: str, version: int, result: dict, *, block_name: str | None = None, network_key: str | None = None, scope: str = "block") -> ArtifactMetadata:
        with self._lock(artifact_id):
            metadata = self.get_artifact(artifact_id)
            self.store.version_path(artifact_id, version)
            record = {
                **result,
                "stage": "tia_compile",
                "artifact_id": artifact_id,
                "version": version,
                "plan_id": metadata.plan_id,
                "block_name": block_name or metadata.block_name,
                "block_type": metadata.block_type,
                "network_key": network_key,
                "scope": scope,
                "recorded_at": result.get("recorded_at") or _iso(_now()),
            }
            suffix = re.sub(r"[^A-Za-z0-9_.-]", "_", network_key or scope)
            latest = self.store.artifact_dir(artifact_id) / "diagnostics" / f"compile_v{version:04d}_{suffix}.json"
            self.store._atomic(latest, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            attempt = self.store.artifact_dir(artifact_id) / "diagnostics" / f"compile_v{version:04d}_{suffix}_{secrets.token_hex(4)}.json"
            self.store._atomic(attempt, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            success = bool(record.get("success"))
            status = "verified" if success else "compile_failed"
            states = dict(metadata.network_states)
            verified = dict(metadata.verified_versions)
            if network_key in states:
                states[network_key] = status
                if success:
                    verified[network_key] = version
            updated = replace(metadata, status=status, network_states=states, verified_versions=verified, updated_at=_iso(_now()), last_compile=record)
            self.store.write_metadata(updated)
            return updated

    def list_artifacts(self, include_expired: bool = False, plan_id: str | None = None) -> list[ArtifactMetadata]:
        result = []
        for child in self.store.root.iterdir():
            if child.is_dir() and (child / "artifact.json").is_file():
                try:
                    value = self.store.metadata(child.name)
                    active = datetime.fromisoformat(value.expires_at.replace("Z", "+00:00")) > _now()
                    if (include_expired or active) and (plan_id is None or value.plan_id == plan_id):
                        result.append(value)
                except (ArtifactError, ValueError, json.JSONDecodeError):
                    pass
        return sorted(result, key=lambda item: item.updated_at, reverse=True)
