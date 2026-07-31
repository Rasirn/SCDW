"""Artifact-first TIA import, compile, InstanceDB and verified-save tools.

Tool annotations intentionally stay eagerly evaluated.  The legacy FastMCP
used by the ``plc`` Conda environment calls ``issubclass`` on annotations and
cannot register string annotations produced by the future import.
"""

import json
from datetime import datetime, timezone
from typing import Any

from scdw.common.run_logging import get_run_logger
from scdw.lad_generation import LadPlanService
from scdw.xml_workspace import ArtifactError, XmlArtifactService


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _message(exc: Exception) -> dict[str, Any]:
    return {"severity": "error", "path": "", "description": str(exc), "type": type(exc).__name__}


def _session_code(exc: Exception, *, project_stage: bool = False) -> tuple[str, str]:
    text = str(exc).lower()
    if project_stage or any(token in text for token in ("没有打开工程", "no open project", "project not open")):
        return "tia_project", "TIA_PROJECT_NOT_OPEN"
    return "tia_session", "TIA_SESSION_UNAVAILABLE"


def _import_code(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    kind = type(exc).__name__.lower()
    if any(token in text for token in ("未发现 plc", "plc device", "device not found", "设备名称")):
        return "tia_target", "TIA_PLC_NOT_FOUND"
    if any(token in text for token in ("override", "overwrite", "same name", "already exists", "同名", "已存在")):
        return "tia_import", "TIA_BLOCK_OVERRIDE_FAILED"
    if (
        "internal error" in text
        or "internalerror" in text
        or "内部错误" in text
        or kind in {"internalexception", "tiainternalexception"}
    ):
        return "tia_import", "TIA_INTERNAL_ERROR"
    return "tia_import", "TIA_XML_IMPORT_FAILED"


def register_lad_runtime_tools(mcp, session, artifact_service: XmlArtifactService | None = None, plan_service: LadPlanService | None = None) -> None:
    artifacts = artifact_service or XmlArtifactService()
    plans = plan_service or LadPlanService()

    def output(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def current_network(metadata, version: int) -> str | None:
        if not metadata.plan_id:
            return None
        try:
            plan = plans.get(metadata.plan_id)
            key = plan.current_network
            if key and any(item["network_key"] == key for item in artifacts.list_networks(metadata.artifact_id, version)):
                network = next(item for item in plan.networks if item.network_key == key)
                return key if network.block_name == metadata.block_name else None
        except (KeyError, ValueError, ArtifactError):
            pass
        return None

    def record_import(metadata, version: int, success: bool, code: str, stage: str, device_name: str, messages: list[dict], network_key: str | None) -> dict:
        updated = artifacts.record_import_result(
            metadata.artifact_id,
            version,
            success,
            code,
            "TIA import completed" if success else (messages[0]["description"] if messages else code),
            messages,
            stage=stage,
            target={"project": getattr(getattr(session, "context", None), "project_name", None), "device_name": device_name},
            network_key=network_key,
        )
        record = updated.last_import or {}
        if metadata.plan_id and metadata.block_name:
            plans.record_import_result(metadata.plan_id, metadata.block_name, metadata.artifact_id, version, record, network_key)
        return record

    def import_lad_xml(artifact_id: str, device_name: str, version: int | None = None) -> str:
        try:
            metadata = artifacts.get_artifact(artifact_id)
            used = version or metadata.current_version
            path = artifacts.store.version_path(artifact_id, used)
            try:
                xml = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                return output(record_import(metadata, used, False, "XML_READ_FAILED", "artifact_read", device_name, [_message(exc)], None))
        except ArtifactError as exc:
            return output({"success": False, "stage": "artifact_read", "artifact_id": artifact_id, "version": version, "block_name": None, "block_type": None, "device_name": device_name, "code": exc.code, "messages": [_message(exc)]})

        network_key = current_network(metadata, used)
        previous = metadata.last_import or {}
        if (
            previous.get("success") is True
            and previous.get("version") == used
            and previous.get("device_name") == device_name
            and previous.get("network_key") == network_key
            and metadata.network_states.get(network_key) in {"imported", "compiling", "verified"}
        ):
            return output({**previous, "code": "ALREADY_IMPORTED", "message": "Identical Artifact version is already imported; TIA import was not repeated", "idempotent": True})
        if (
            previous.get("success") is False
            and previous.get("version") == used
            and previous.get("device_name") == device_name
            and previous.get("network_key") == network_key
        ):
            return output({
                **previous,
                "code": "UNCHANGED_FAILED_VERSION",
                "message": "This unchanged Artifact version already failed with the same TIA diagnostic; import was not repeated",
                "idempotent": True,
            })
        try:
            artifacts.set_workflow_state(artifact_id, "importing", network_key)
            if metadata.plan_id:
                plans.set_runtime_status(metadata.plan_id, "importing", block_name=metadata.block_name, network_key=network_key)
            session.ensure_current_context()
        except Exception as exc:
            stage, code = _session_code(exc)
            return output(record_import(metadata, used, False, code, stage, device_name, [_message(exc)], network_key))
        try:
            session.require_project()
        except Exception as exc:
            stage, code = _session_code(exc, project_stage=True)
            return output(record_import(metadata, used, False, code, stage, device_name, [_message(exc)], network_key))

        try:
            from scdw.openness.tia_blocks import import_lad_xml_block

            temp_dir = session.get_temp_dir()
            session.run_plc_operation("import_lad_xml", device_name, lambda _project, plc: import_lad_xml_block(plc, temp_dir, metadata.block_name or "Main", xml))
        except Exception as exc:
            get_run_logger().log_exception("lad_artifact_import_failed", exc, component="mcp.lad_runtime", artifact_id=artifact_id, version=used)
            stage, code = _import_code(exc)
            return output(record_import(metadata, used, False, code, stage, device_name, [_message(exc)], network_key))

        return output(record_import(metadata, used, True, "OK", "tia_import", device_name, [], network_key))

    def compile_failure(*, code: str, stage: str, device_name: str, block_name: str | None, artifact_id: str | None, version: int | None, network_key: str | None, exc: Exception) -> dict:
        return {
            "success": False,
            "stage": stage,
            "scope": "block" if block_name else "plc",
            "device_name": device_name,
            "block_name": block_name,
            "artifact_id": artifact_id,
            "version": version,
            "network_key": network_key,
            "code": code,
            "state": "Error",
            "error_count": 1,
            "warning_count": 0,
            "messages": [_message(exc)],
            "recorded_at": _stamp(),
        }

    def compile_check(device_name: str, block_name: str | None = None, artifact_id: str | None = None, version: int | None = None, network_key: str | None = None, plan_id: str | None = None) -> str:
        metadata = None
        used = version
        if version is not None and artifact_id is None:
            return output(compile_failure(code="VERSION_REQUIRES_ARTIFACT", stage="artifact_read", device_name=device_name, block_name=block_name, artifact_id=None, version=version, network_key=network_key, exc=ValueError("version requires artifact_id")))
        if network_key and artifact_id is None:
            return output(compile_failure(code="NETWORK_REQUIRES_ARTIFACT", stage="artifact_read", device_name=device_name, block_name=block_name, artifact_id=None, version=version, network_key=network_key, exc=ValueError("network_key requires artifact_id")))
        if artifact_id:
            try:
                metadata = artifacts.get_artifact(artifact_id)
                used = version or metadata.current_version
                artifacts.store.version_path(artifact_id, used)
            except ArtifactError as exc:
                return output(compile_failure(code=exc.code, stage="artifact_read", device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=exc))
            if block_name and metadata.block_name and block_name != metadata.block_name:
                return output(compile_failure(code="ARTIFACT_BLOCK_MISMATCH", stage="artifact_read", device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=ValueError("block_name does not match Artifact metadata")))
            block_name = block_name or metadata.block_name
            plan_id = plan_id or metadata.plan_id

        if metadata and used is not None and network_key:
            if metadata.network_states.get(network_key) == "verified" and metadata.verified_versions.get(network_key) == used:
                previous = metadata.last_compile or {}
                return output({**previous, "success": True, "stage": "tia_compile", "code": "ALREADY_VERIFIED", "message": "This Network and Artifact version are already verified; TIA compile was not repeated", "artifact_id": artifact_id, "version": used, "network_key": network_key, "idempotent": True})
        if plan_id and block_name is None:
            try:
                if plans.get(plan_id).step_status.get("plc_compile") == "verified":
                    return output({"success": True, "stage": "tia_compile", "scope": "plc", "code": "ALREADY_VERIFIED", "message": "PLC compile is already verified; TIA compile was not repeated", "plan_id": plan_id, "device_name": device_name, "idempotent": True})
            except (KeyError, ValueError, OSError):
                pass
        if plan_id and block_name and network_key is None:
            try:
                plan_value = plans.get(plan_id)
                block_value = next(item for item in [plan_value.main_fc, *plan_value.auxiliary_fbs] if item.block_name == block_name)
                if block_value.status == "verified" and (used is None or block_value.verified_version == used):
                    return output({"success": True, "stage": "tia_compile", "scope": "block", "code": "ALREADY_VERIFIED", "message": "Final block compile is already verified; TIA compile was not repeated", "plan_id": plan_id, "block_name": block_name, "artifact_id": artifact_id, "version": used, "idempotent": True})
            except (KeyError, ValueError, OSError, StopIteration):
                pass

        try:
            if plan_id and block_name and network_key:
                plans.validate_network_compile(plan_id, block_name, network_key, artifact_id, used)
            elif plan_id and block_name and network_key is None:
                plans.validate_final_block_compile(plan_id, block_name)
            elif plan_id and block_name is None:
                plans.validate_final_plc_compile(plan_id)
        except Exception as exc:
            failure = compile_failure(code="PLAN_PRECONDITION_FAILED", stage="plan", device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=exc)
            return output(failure)
        try:
            if metadata:
                artifacts.set_workflow_state(metadata.artifact_id, "compiling", network_key)
            if plan_id:
                plans.set_runtime_status(plan_id, "compiling", block_name=block_name, network_key=network_key)
            session.ensure_current_context()
        except Exception as exc:
            stage, code = _session_code(exc)
            failure = compile_failure(code=code, stage=stage, device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=exc)
            if metadata and used is not None:
                artifacts.record_compile_result(artifact_id, used, failure, block_name=block_name, network_key=network_key, scope=failure["scope"])
            if plan_id:
                plans.record_compile_result(plan_id, failure, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key)
            return output(failure)
        try:
            session.require_project()
        except Exception as exc:
            failure = compile_failure(code="TIA_PROJECT_NOT_OPEN", stage="tia_project", device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=exc)
            if metadata and used is not None:
                artifacts.record_compile_result(artifact_id, used, failure, block_name=block_name, network_key=network_key, scope=failure["scope"])
            if plan_id:
                plans.record_compile_result(plan_id, failure, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key)
            return output(failure)

        try:
            from scdw.openness.tia_compiler import CompileTargetNotFoundError, compile_block, compile_plc

            result = session.run_plc_operation("compile_check", device_name, lambda _project, plc: compile_block(plc, block_name) if block_name else compile_plc(plc))
            value = {
                **result.to_dict(),
                "stage": "tia_compile",
                "device_name": device_name,
                "block_name": block_name,
                "artifact_id": artifact_id,
                "version": used,
                "network_key": network_key,
                "code": "OK" if result.success else "TIA_COMPILE_FAILED",
                "recorded_at": _stamp(),
            }
        except Exception as exc:
            code = "TIA_BLOCK_NOT_FOUND" if type(exc).__name__ == "CompileTargetNotFoundError" else "TIA_COMPILE_INTERNAL_ERROR"
            value = compile_failure(code=code, stage="tia_compile", device_name=device_name, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key, exc=exc)

        if metadata and used is not None:
            artifacts.record_compile_result(artifact_id, used, value, block_name=block_name, network_key=network_key, scope=value["scope"])
        if plan_id:
            plans.record_compile_result(plan_id, value, block_name=block_name, artifact_id=artifact_id, version=used, network_key=network_key)
        return output(value)

    # Kept private for the composite workflow and focused unit tests.  These
    # primitives are deliberately absent from the MCP tool surface so the LLM
    # cannot split import, compile, and state recording into redundant calls.
    mcp._scdw_lad_runtime_internal = {
        "import_lad_xml": import_lad_xml,
        "compile_check": compile_check,
    }

    @mcp.tool(
        name="import_and_compile_artifact",
        description="Import one immutable Artifact version and compile its current Network in one call. On success it atomically records Artifact/Plan verification and, when ready, chains final block and PLC compilation. Identical verified versions are not re-run.",
    )
    def import_and_compile_artifact(artifact_id: str, device_name: str, version: int | None = None, network_key: str | None = None, finalize_ready: bool = True) -> str:
        try:
            metadata = artifacts.get_artifact(artifact_id)
            used = version or metadata.current_version
            if not metadata.plan_id or not metadata.block_name:
                return output({"success": False, "stage": "workflow", "code": "PLAN_PRECONDITION_FAILED", "message": "Artifact must be linked to an active Plan and block", "artifact_id": artifact_id, "version": used})
            plan = plans.get(metadata.plan_id)
            selected_network = network_key or plan.current_network
            if not selected_network:
                return output({"success": False, "stage": "workflow", "code": "NETWORK_REQUIRED", "message": "No current Network is available to import and compile", "artifact_id": artifact_id, "version": used, "plan_id": metadata.plan_id})
            network = next((item for item in plan.networks if item.network_key == selected_network), None)
            if network is None or network.block_name != metadata.block_name:
                return output({"success": False, "stage": "workflow", "code": "PLAN_PRECONDITION_FAILED", "message": "Network does not belong to the Artifact block", "artifact_id": artifact_id, "version": used, "network_key": selected_network})
            plans.set_cursor(plan.plan_id, metadata.block_name, selected_network)
        except (ArtifactError, KeyError, ValueError, OSError) as exc:
            return output({"success": False, "stage": "workflow", "code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "artifact_id": artifact_id, "version": version})

        imported = json.loads(import_lad_xml(artifact_id, device_name, used))
        if not imported.get("success"):
            repeated = plans.stop_repeated_diagnostic(plan.plan_id, selected_network, imported)
            return output({
                "success": False, "stage": "tia_import",
                "code": "REPEATED_TIA_DIAGNOSTIC" if repeated else imported.get("code", "TIA_XML_IMPORT_FAILED"),
                "message": (
                    "The same TIA import diagnostic occurred twice; approximate expression patching is stopped while the frozen blueprint remains unchanged."
                    if repeated else imported.get("message", "TIA import failed")
                ),
                "artifact_id": artifact_id, "version": used, "network_key": selected_network,
                "import": imported, "next": plans.next_step(plan.plan_id),
                "retryable": not repeated, "needs_user_action": False,
                "recommended_action": "repair_lad_xml_expression" if repeated else "repair_from_tia_diagnostic",
                "fallback_arguments": {"artifact_id": artifact_id, "expected_version": used, "network_key": selected_network} if repeated else {},
            })

        compiled = json.loads(compile_check(device_name, metadata.block_name, artifact_id, used, selected_network, plan.plan_id))
        if not compiled.get("success"):
            repeated = plans.stop_repeated_diagnostic(plan.plan_id, selected_network, compiled)
            return output({
                "success": False, "stage": "tia_compile",
                "code": "REPEATED_TIA_DIAGNOSTIC" if repeated else compiled.get("code", "TIA_COMPILE_FAILED"),
                "message": (
                    "The same TIA compile diagnostic occurred twice; approximate expression patching is stopped while the frozen blueprint remains unchanged."
                    if repeated else "Network compile failed"
                ),
                "artifact_id": artifact_id, "version": used, "network_key": selected_network,
                "import": imported, "compile": compiled, "next": plans.next_step(plan.plan_id),
                "retryable": not repeated, "needs_user_action": False,
                "recommended_action": "repair_lad_xml_expression" if repeated else "repair_from_tia_diagnostic",
                "fallback_arguments": {"artifact_id": artifact_id, "expected_version": used, "network_key": selected_network} if repeated else {},
            })

        final_block = None
        final_plc = None
        if finalize_ready:
            try:
                plans.validate_final_block_compile(plan.plan_id, metadata.block_name)
            except ValueError:
                pass
            else:
                final_block = json.loads(compile_check(device_name, metadata.block_name, artifact_id, used, None, plan.plan_id))
            if final_block is None or final_block.get("success"):
                try:
                    plans.validate_final_plc_compile(plan.plan_id)
                except ValueError:
                    pass
                else:
                    final_plc = json.loads(compile_check(device_name, None, None, None, None, plan.plan_id))

        success = bool(compiled.get("success")) and (final_block is None or bool(final_block.get("success"))) and (final_plc is None or bool(final_plc.get("success")))
        return output({
            "success": success,
            "stage": "workflow_verified" if success else "tia_compile",
            "code": "OK" if success else "TIA_COMPILE_FAILED",
            "message": "Artifact import and required compilation completed" if success else "A chained compilation step failed",
            "artifact_id": artifact_id,
            "version": used,
            "network_key": selected_network,
            "plan_id": plan.plan_id,
            "block_name": metadata.block_name,
            "device_name": device_name,
            "import": imported,
            "compile": compiled,
            "final_block_compile": final_block,
            "final_plc_compile": final_plc,
            "next": plans.next_step(plan.plan_id),
        })

    def resolve_plan_id(plan_id: str | None, device_name: str, fb_name: str, instance_db_name: str) -> str:
        if plan_id:
            return plan_id
        matches = [plan.plan_id for plan in plans.list() if plan.target_device == device_name and any(item.fb_name == fb_name and item.db_name == instance_db_name for item in plan.instance_dbs)]
        if len(matches) != 1:
            raise ValueError("plan_id is required when the active LAD plan cannot be identified uniquely")
        return matches[0]

    @mcp.tool(
        name="create_instance_db",
        description="在目标PLC中创建绑定指定FB的Instance DB。FB必须已导入并通过块级编译；支持自动编号或指定db_number，返回结构化创建结果并更新LAD计划。",
    )
    def create_instance_db(device_name: str, fb_name: str, instance_db_name: str, db_number: int | None = None, plan_id: str | None = None) -> str:
        resolved_plan = None
        try:
            resolved_plan = resolve_plan_id(plan_id, device_name, fb_name, instance_db_name)
            plans.validate_instance_db_order(resolved_plan, fb_name, instance_db_name)
            plans.set_runtime_status(resolved_plan, "importing", instance_db_name=instance_db_name)
            session.ensure_current_context()
            session.require_project()
            from scdw.openness.tia_blocks import create_instance_db as openness_create_instance_db

            result = session.run_plc_operation("create_instance_db", device_name, lambda _project, plc: openness_create_instance_db(plc, fb_name, instance_db_name, db_number))
            value = {"stage": "tia_instance_db", "plan_id": resolved_plan, "device_name": device_name, "recorded_at": _stamp(), **result}
        except Exception as exc:
            value = {"success": False, "stage": "tia_instance_db", "plan_id": resolved_plan or plan_id, "device_name": device_name, "fb_name": fb_name, "instance_db_name": instance_db_name, "db_number": db_number, "code": "INSTANCE_DB_CREATE_FAILED", "messages": [_message(exc)], "recorded_at": _stamp()}
            if resolved_plan:
                plans.record_instance_db_result(resolved_plan, fb_name, instance_db_name, value)
            return output(value)
        plans.record_instance_db_result(resolved_plan, fb_name, instance_db_name, value)
        return output(value)

    @mcp.tool(name="save_verified_project", description="仅当计划中所有Network、块级编译和最终PLC编译均已验证成功时保存当前TIA项目。")
    def save_verified_project(device_name: str, plan_id: str) -> str:
        try:
            plans.validate_ready_to_save(plan_id)
            session.ensure_current_context()
            session.require_project()
            from scdw.openness.tia_core import save_project

            session.run_project_operation("save_verified_project", save_project)
            return output({"success": True, "stage": "tia_save", "code": "OK", "plan_id": plan_id, "device_name": device_name, "recorded_at": _stamp()})
        except Exception as exc:
            return output({"success": False, "stage": "tia_save", "code": "PROJECT_SAVE_BLOCKED", "plan_id": plan_id, "device_name": device_name, "messages": [_message(exc)], "recorded_at": _stamp()})
