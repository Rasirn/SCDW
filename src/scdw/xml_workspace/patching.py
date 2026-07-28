from __future__ import annotations

from .models import ArtifactError, PatchOperation


def apply_operations(content: str, operations: list[PatchOperation]) -> tuple[str, list[dict]]:
    result, summary = content, []
    for index, operation in enumerate(operations):
        if operation.op not in {"replace_exact", "insert_before", "insert_after", "delete_exact"}:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", f"unsupported operation: {operation.op}")
        if not operation.old or operation.expected_occurrences < 0:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", f"operation {index} requires non-empty old and non-negative expected_occurrences")
        found = result.count(operation.old)
        if found != operation.expected_occurrences:
            raise ArtifactError("PATCH_PRECONDITION_FAILED", f"{operation.op} expected {operation.expected_occurrences} occurrence(s), found {found}", retryable=True)
        if operation.op == "replace_exact":
            if operation.new is None: raise ArtifactError("PATCH_PRECONDITION_FAILED", "replace_exact requires new")
            result = result.replace(operation.old, operation.new)
        elif operation.op == "delete_exact": result = result.replace(operation.old, "")
        elif operation.op == "insert_before":
            if operation.new is None: raise ArtifactError("PATCH_PRECONDITION_FAILED", "insert_before requires new")
            result = result.replace(operation.old, operation.new + operation.old)
        else:
            if operation.new is None: raise ArtifactError("PATCH_PRECONDITION_FAILED", "insert_after requires new")
            result = result.replace(operation.old, operation.old + operation.new)
        summary.append({"op": operation.op, "occurrences": found, "old_chars": len(operation.old), "new_chars": len(operation.new or "")})
    return result, summary
