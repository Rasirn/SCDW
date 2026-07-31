"""MCP tool discovery and guarded execution for model tool calls."""
from __future__ import annotations

import json
from typing import Any, Optional

from scdw.common.run_logging import get_run_logger
from scdw.mcp.client import MCPClient


class ToolArgumentError(ValueError):
    def __init__(self, code: str, message: str, position: int | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.position = code, message, position
        start = max(0, (position or 0) - 80)
        self.excerpt = raw[start:start + 160]


class ToolManager:
    """Expose MCP schemas and reject bad model arguments before backend calls."""

    _client_tool_names: dict[int, set[str]] = {}
    _tool_schemas: dict[tuple[int, str], dict[str, Any]] = {}

    @classmethod
    async def get_all_tools(cls, clients: dict[str, MCPClient]) -> list[dict]:
        tools = []
        for client in clients.values():
            listed = await client.list_tools()
            cls._client_tool_names[id(client)] = {tool.name for tool in listed}
            for tool in listed:
                cls._tool_schemas[(id(client), tool.name)] = tool.inputSchema
                tools.append({"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema}})
        return tools

    @classmethod
    async def _find_client_with_tool(cls, clients: list[MCPClient], tool_name: str) -> Optional[MCPClient]:
        for client in clients:
            names = cls._client_tool_names.get(id(client))
            if names is None:
                listed = await client.list_tools()
                names = {tool.name for tool in listed}
                cls._client_tool_names[id(client)] = names
                for tool in listed:
                    cls._tool_schemas[(id(client), tool.name)] = tool.inputSchema
            if tool_name in names:
                return client
        return None

    @classmethod
    async def execute_tool_requests(cls, clients: dict[str, MCPClient], message) -> list[dict]:
        requests = message.message.tool_calls if hasattr(message.message, "tool_calls") else []
        results = []
        for request in requests:
            item = await cls.execute_tool_request(clients, request)
            results.append({key: value for key, value in item.items() if key != "success"})
        return results

    @staticmethod
    def _result(tool_id: str, name: str, stage: str, code: str, message: str, **extra: Any) -> dict:
        payload = {"success": False, "stage": stage, "code": code, "tool_name": name,
                   "message": message, "retryable": True, "needs_user_action": False, **extra}
        return {"role": "tool", "tool_call_id": tool_id, "content": json.dumps(payload, ensure_ascii=False), "success": False}

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            raise ToolArgumentError("INVALID_TOOL_ARGUMENT_JSON", "tool arguments must be a JSON string")
        offset = len(raw) - len(raw.lstrip())
        try:
            value, end = json.JSONDecoder().raw_decode(raw.lstrip())
            end += offset
        except json.JSONDecodeError as exc:
            code = "TRUNCATED_TOOL_ARGUMENT_JSON" if exc.pos >= max(0, len(raw) - offset - 2) else "INVALID_TOOL_ARGUMENT_JSON"
            if "escape" in exc.msg.lower():
                code = "INVALID_TOOL_ARGUMENT_ESCAPE"
            raise ToolArgumentError(code, "tool arguments are not valid JSON", exc.pos + offset, raw) from exc
        if raw[end:].strip():
            raise ToolArgumentError("TRAILING_TOOL_ARGUMENT_CONTENT", "tool arguments must contain exactly one JSON object", end, raw)
        if not isinstance(value, dict):
            raise ToolArgumentError("INVALID_TOOL_ARGUMENT_JSON", "tool argument root must be an object", 0, raw)
        return value

    @classmethod
    def _validate_schema(cls, value: Any, schema: dict[str, Any], path: str = "") -> list[dict[str, str]]:
        """Validate the Pydantic/FastMCP JSON-Schema subset before RPC."""
        definitions = schema.get("$defs", {})
        if "$ref" in schema:
            target = definitions.get(schema["$ref"].rsplit("/", 1)[-1], {})
            return cls._validate_schema(value, {**target, "$defs": definitions}, path)
        if "anyOf" in schema:
            candidates = [cls._validate_schema(value, {**item, "$defs": definitions}, path) for item in schema["anyOf"]]
            return [] if any(not candidate for candidate in candidates) else candidates[0]
        if "enum" in schema and value not in schema["enum"]:
            return [{"path": path or "$", "message": "allowed values: " + ", ".join(map(str, schema["enum"]))}]
        kind = schema.get("type")
        errors: list[dict[str, str]] = []
        if kind == "object" or "properties" in schema:
            if not isinstance(value, dict): return [{"path": path or "$", "message": "must be an object"}]
            properties = schema.get("properties", {})
            for field in schema.get("required", []):
                if field not in value: errors.append({"path": f"{path}.{field}".lstrip("."), "message": "required field is missing"})
            if schema.get("additionalProperties") is False:
                for field in value.keys() - properties.keys(): errors.append({"path": f"{path}.{field}".lstrip("."), "message": "additional field is not allowed"})
            for field, child in properties.items():
                if field in value: errors.extend(cls._validate_schema(value[field], {**child, "$defs": definitions}, f"{path}.{field}".lstrip(".")))
        elif kind == "array":
            if not isinstance(value, list): return [{"path": path or "$", "message": "must be an array"}]
            if schema.get("minItems", 0) > len(value): errors.append({"path": path or "$", "message": "array must not be empty"})
            for index, item in enumerate(value): errors.extend(cls._validate_schema(item, {**schema.get("items", {}), "$defs": definitions}, f"{path}[{index}]"))
        elif kind == "string" and not isinstance(value, str): errors.append({"path": path or "$", "message": "must be a string"})
        elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): errors.append({"path": path or "$", "message": "must be an integer"})
        elif kind == "boolean" and not isinstance(value, bool): errors.append({"path": path or "$", "message": "must be a boolean"})
        return errors

    @staticmethod
    def _minimal_example(schema: dict[str, Any]) -> dict[str, str]:
        return {field: "<required>" for field in schema.get("required", [])[:8]}

    @classmethod
    async def execute_tool_request(cls, clients: dict[str, MCPClient], request) -> dict:
        tool_id, name, raw = request.id, request.function.name, request.function.arguments
        logger = get_run_logger()
        logger.log_event("tool_arguments_received", component="tool_manager", tool_name=name,
                         tool_call_id=tool_id, arguments=logger.save_payload(f"raw_tool_arguments_{name}", raw))
        client = await cls._find_client_with_tool(list(clients.values()), name)
        if client is None:
            return cls._result(tool_id, name, "tool_dispatch", "TOOL_NOT_FOUND", "MCP tool is not available")
        schema = cls._tool_schemas.get((id(client), name), {})
        try:
            arguments = cls._parse_arguments(raw)
        except ToolArgumentError as exc:
            return cls._result(tool_id, name, "tool_argument_parse", exc.code, exc.message,
                               error_position=exc.position, excerpt=exc.excerpt, example=cls._minimal_example(schema))
        errors = cls._validate_schema(arguments, schema)
        if errors:
            return cls._result(tool_id, name, "tool_argument_validation", "TOOL_ARGUMENT_INVALID",
                               "tool arguments do not match inputSchema", errors=errors)
        try:
            output = await client.call_tool(name, arguments)
            texts = [item.text if hasattr(item, "text") else str(item) for item in (output.content if output else [])]
            content = "\n".join(texts) or "tool completed without text output"
            success = not bool(getattr(output, "isError", False))
            if success and content.lstrip().startswith("{"):
                try: success = json.loads(content).get("success", True) is not False
                except json.JSONDecodeError: pass
            return {"role": "tool", "tool_call_id": tool_id, "content": content, "success": success}
        except Exception as exc:
            logger.log_exception("tool_dispatch_failed", exc, component="tool_manager", tool_name=name)
            return cls._result(tool_id, name, "tool_dispatch", "TOOL_EXECUTION_FAILED", str(exc))
