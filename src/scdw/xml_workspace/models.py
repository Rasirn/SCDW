from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ArtifactError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    block_name: str | None
    device_name: str | None
    current_version: int
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    last_validation: dict[str, Any] | None = None
    last_import: dict[str, Any] | None = None
    conversation_id: str | None = None

    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactMetadata": return cls(**value)


@dataclass(frozen=True)
class PatchOperation:
    op: str
    old: str | None = None
    new: str | None = None
    expected_occurrences: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PatchOperation": return cls(**value)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class FragmentResult:
    artifact_id: str
    version: int
    start_line: int
    end_line: int
    content: str
    sha256: str
    truncated: bool

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class PatchResult:
    artifact_id: str
    old_version: int
    new_version: int
    changes: list[dict[str, Any]]
    validation: ValidationResult

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["validation"] = self.validation.to_dict(); return data
