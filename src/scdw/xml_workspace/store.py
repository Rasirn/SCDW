from __future__ import annotations

import json, os, re, tempfile
from pathlib import Path
from .models import ArtifactError, ArtifactMetadata

_ID = re.compile(r"^xml_[a-f0-9]{12,64}$")

class ArtifactStore:
    def __init__(self, root: Path) -> None: self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
    def artifact_dir(self, artifact_id: str) -> Path:
        if not _ID.fullmatch(artifact_id): raise ArtifactError("ARTIFACT_NOT_FOUND", "invalid artifact id")
        path = (self.root / artifact_id).resolve()
        if path.parent != self.root: raise ArtifactError("ARTIFACT_NOT_FOUND", "invalid artifact path")
        return path
    def _atomic(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle: handle.write(content); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    def write_metadata(self, metadata: ArtifactMetadata) -> None: self._atomic(self.artifact_dir(metadata.artifact_id) / "artifact.json", json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        path = self.artifact_dir(artifact_id) / "artifact.json"
        if not path.is_file(): raise ArtifactError("ARTIFACT_NOT_FOUND", "artifact not found")
        return ArtifactMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))
    def version_path(self, artifact_id: str, version: int) -> Path:
        if version < 1: raise ArtifactError("VERSION_NOT_FOUND", "invalid version")
        path = self.artifact_dir(artifact_id) / "versions" / f"v{version:04d}.xml"
        if not path.is_file(): raise ArtifactError("VERSION_NOT_FOUND", "version not found")
        return path
    def write_version(self, artifact_id: str, version: int, content: str) -> None: self._atomic(self.artifact_dir(artifact_id) / "versions" / f"v{version:04d}.xml", content)
