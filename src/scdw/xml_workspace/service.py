from __future__ import annotations

import hashlib, json, secrets, threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from scdw.common.paths import XML_ARTIFACTS_DIR, XML_ARTIFACT_TTL_HOURS
from .models import ArtifactError, ArtifactMetadata, FragmentResult, PatchOperation, PatchResult, ValidationResult
from .patching import apply_operations
from .store import ArtifactStore
from .validation import validate_xml

def _now() -> datetime: return datetime.now(timezone.utc)
def _iso(value: datetime) -> str: return value.isoformat().replace("+00:00", "Z")

class XmlArtifactService:
    """Pure-Python XML workspace; all artifact writes are atomic and versioned."""
    def __init__(self, root: Path | None = None, *, ttl_hours: int = XML_ARTIFACT_TTL_HOURS) -> None:
        self.store = ArtifactStore(root or XML_ARTIFACTS_DIR); self.ttl_hours = ttl_hours
        self._locks: dict[str, threading.RLock] = {}; self._locks_guard = threading.Lock()
    def _lock(self, artifact_id: str) -> threading.RLock:
        with self._locks_guard: return self._locks.setdefault(artifact_id, threading.RLock())
    def _active(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        if datetime.fromisoformat(metadata.expires_at.replace("Z", "+00:00")) <= _now(): raise ArtifactError("ARTIFACT_EXPIRED", "artifact has expired")
        return metadata
    def create_artifact(self, xml_content: str, block_name: str | None = None, device_name: str | None = None, conversation_id: str | None = None) -> ArtifactMetadata:
        artifact_id = "xml_" + secrets.token_hex(12); stamp = _now()
        metadata = ArtifactMetadata(artifact_id, block_name, device_name, 1, "draft", _iso(stamp), _iso(stamp), _iso(stamp + timedelta(hours=self.ttl_hours)), conversation_id=conversation_id)
        directory = self.store.artifact_dir(artifact_id)
        directory.mkdir(parents=True, exist_ok=False)
        validation = validate_xml(xml_content)
        metadata = replace(metadata, last_validation=validation.to_dict(), status="draft" if validation.valid else "invalid")
        self.store.write_version(artifact_id, 1, xml_content); self.store.write_metadata(metadata)
        return metadata
    def get_artifact(self, artifact_id: str) -> ArtifactMetadata: return self._active(self.store.metadata(artifact_id))
    def _content(self, artifact_id: str, version: int | None = None) -> tuple[ArtifactMetadata, int, str]:
        metadata = self.get_artifact(artifact_id); version = version or metadata.current_version
        return metadata, version, self.store.version_path(artifact_id, version).read_text(encoding="utf-8")
    def read_fragment(self, artifact_id: str, version: int | None = None, search: str | None = None, start_line: int | None = None, end_line: int | None = None, context_lines: int = 10, max_chars: int = 12000) -> FragmentResult:
        if max_chars < 1: raise ArtifactError("PATCH_PRECONDITION_FAILED", "max_chars must be positive")
        _, used_version, content = self._content(artifact_id, version); lines = content.splitlines(keepends=True)
        if search:
            match = next((i for i, line in enumerate(lines, 1) if search in line), None)
            if match is None: raise ArtifactError("PATCH_PRECONDITION_FAILED", "search text not found")
            first, last = max(1, match - max(0, context_lines)), min(len(lines), match + max(0, context_lines))
        else:
            first, last = start_line or 1, end_line or len(lines)
            if first < 1 or last < first: raise ArtifactError("PATCH_PRECONDITION_FAILED", "invalid line range")
            last = min(last, len(lines))
        fragment = "".join(lines[first-1:last]); truncated = len(fragment) > max_chars
        if truncated: fragment = fragment[:max_chars]
        return FragmentResult(artifact_id, used_version, first, last, fragment, hashlib.sha256(fragment.encode()).hexdigest(), truncated)
    def validate_artifact(self, artifact_id: str, version: int | None = None) -> ValidationResult:
        metadata, used_version, content = self._content(artifact_id, version); result = validate_xml(content)
        if used_version == metadata.current_version:
            with self._lock(artifact_id): self.store.write_metadata(replace(metadata, last_validation=result.to_dict(), updated_at=_iso(_now()), status="draft" if result.valid else "invalid"))
        return result
    def apply_patch(self, artifact_id: str, expected_version: int, operations: list[PatchOperation]) -> PatchResult:
        with self._lock(artifact_id):
            metadata, version, content = self._content(artifact_id)
            if expected_version != version: raise ArtifactError("VERSION_CONFLICT", f"expected version {expected_version}, current version is {version}", retryable=True)
            patched, changes = apply_operations(content, operations); validation = validate_xml(patched)
            if not validation.valid: raise ArtifactError("XML_VALIDATION_FAILED", "patched XML failed validation")
            new_version = version + 1; self.store.write_version(artifact_id, new_version, patched)
            patch_path = self.store.artifact_dir(artifact_id) / "patches" / f"v{version:04d}_to_v{new_version:04d}.json"
            self.store._atomic(patch_path, json.dumps({"artifact_id":artifact_id,"from_version":version,"to_version":new_version,"operations":[o.to_dict() for o in operations]}, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            self.store.write_metadata(replace(metadata, current_version=new_version, updated_at=_iso(_now()), status="draft", last_validation=validation.to_dict()))
            return PatchResult(artifact_id, version, new_version, changes, validation)
    def record_import_result(self, artifact_id: str, version: int, success: bool, code: str, message: str, diagnostics: list[dict] | None = None) -> ArtifactMetadata:
        with self._lock(artifact_id):
            metadata = self.get_artifact(artifact_id); self.store.version_path(artifact_id, version)
            record = {"success":success,"code":code,"message":message,"version":version,"recorded_at":_iso(_now()),"diagnostics":diagnostics or []}
            path = self.store.artifact_dir(artifact_id) / "diagnostics" / f"import_v{version:04d}.json"
            self.store._atomic(path, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            status = "imported" if success else "import_failed"; updated = replace(metadata, status=status, updated_at=_iso(_now()), last_import=record)
            self.store.write_metadata(updated); return updated
    def list_artifacts(self, include_expired: bool = False) -> list[ArtifactMetadata]:
        result = []
        for child in self.store.root.iterdir():
            if child.is_dir() and (child / "artifact.json").is_file():
                try:
                    value = self.store.metadata(child.name)
                    if include_expired or datetime.fromisoformat(value.expires_at.replace("Z", "+00:00")) > _now(): result.append(value)
                except (ArtifactError, ValueError, json.JSONDecodeError): continue
        return sorted(result, key=lambda item: item.updated_at, reverse=True)
