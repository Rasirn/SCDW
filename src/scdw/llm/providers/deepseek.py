"""统一的 DeepSeek V4 Provider。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from openai import AsyncOpenAI, OpenAI
from dotenv import load_dotenv

from scdw.common.config import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_FAST_MODEL
from scdw.common.exceptions import (
    LlmAuthenticationError, LlmError, LlmOutputTruncatedError,
    LlmRateLimitError, LlmResponseError, LlmTimeoutError,
)
from scdw.common.paths import PROJECT_ROOT

# 本地开发阶段沿用原项目密钥。不得在日志、异常、测试结果或文档中输出该值。
DEFAULT_MODEL = DEEPSEEK_DEFAULT_MODEL
FAST_MODEL = DEEPSEEK_FAST_MODEL
MAX_TOOL_ROUNDS = 20


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LlmResponse:
    content: str | None
    reasoning_content: str | None
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    model: str
    usage: LlmUsage
    request_id: str | None = None
    raw_choice: Any = None

    @property
    def message(self) -> Any:
        return self.raw_choice.message if self.raw_choice is not None else None


@dataclass(frozen=True)
class LlmStreamResult:
    """一次真实流式响应聚合后的完整结果。"""
    content: str
    reasoning_content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    model: str
    usage: LlmUsage

    @property
    def message(self) -> Any:
        """兼容旧 Chat/MCP 调用方使用的 ``response.message``。"""
        return self.raw_choice.message if self.raw_choice is not None else None


class DeepSeekProvider:
    """将 SDK 响应和错误收敛为项目内部类型。"""

    def __init__(self, model: str = DEFAULT_MODEL,
                 *, timeout: float = 120, max_retries: int = 2, client: Any = None):
        self.model = self._normalise_model(model)
        self.timeout = timeout
        self.max_retries = max_retries
        load_dotenv(PROJECT_ROOT / ".env")
        resolved_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not resolved_key and client is None:
            raise LlmAuthenticationError("未读取到 DeepSeek API Key，请检查项目 .env 配置。")
        self.client = client or OpenAI(api_key=resolved_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout)
        self.async_client = None if client is not None else AsyncOpenAI(api_key=resolved_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout)

    async def stream_chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
                          mode: str = "thinking", cancel_event: Any = None) -> AsyncIterator[dict[str, Any]]:
        """消费 DeepSeek 真实流；事件中的 delta 未经定时器伪造。"""
        if mode not in {"thinking", "fast"}:
            raise LlmResponseError("无效模式，仅支持 thinking 或 fast。")
        model = DEFAULT_MODEL if mode == "thinking" else FAST_MODEL
        params: dict[str, Any] = {"model": model, "messages": list(messages), "stream": True,
                                  "stream_options": {"include_usage": True},
                                  "extra_body": {"thinking": {"type": "enabled" if mode == "thinking" else "disabled"}}}
        if mode == "thinking":
            params["extra_body"]["reasoning_effort"] = "high"
        if tools:
            params["tools"] = tools
        client = self.async_client
        if client is None:
            raise LlmResponseError("注入的测试客户端不支持异步真实流。")
        stream = await client.chat.completions.create(**params)
        reasoning = content = ""
        calls: dict[int, dict[str, Any]] = {}
        finish_reason = None
        usage = LlmUsage()
        reasoning_open = answer_open = False
        try:
            async for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    await stream.close()
                    yield {"type": "stream_cancelled"}
                    return
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is not None:
                    usage = LlmUsage(getattr(raw_usage, "prompt_tokens", None), getattr(raw_usage, "completion_tokens", None), getattr(raw_usage, "total_tokens", None))
                    yield {"type": "usage", "usage": usage}
                for choice in getattr(chunk, "choices", []) or []:
                    delta = choice.delta
                    part_reasoning = getattr(delta, "reasoning_content", None)
                    if part_reasoning:
                        if not reasoning_open:
                            reasoning_open = True; yield {"type": "reasoning_start"}
                        reasoning += part_reasoning; yield {"type": "reasoning_delta", "content": part_reasoning}
                    part_content = getattr(delta, "content", None)
                    if part_content:
                        if reasoning_open:
                            reasoning_open = False; yield {"type": "reasoning_end"}
                        if not answer_open:
                            answer_open = True; yield {"type": "answer_start"}
                        content += part_content; yield {"type": "answer_delta", "content": part_content}
                    for item in getattr(delta, "tool_calls", None) or []:
                        index = int(getattr(item, "index", 0)); call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        call["id"] += getattr(item, "id", "") or ""
                        function = getattr(item, "function", None)
                        if function:
                            call["function"]["name"] += getattr(function, "name", "") or ""
                            call["function"]["arguments"] += getattr(function, "arguments", "") or ""
                    finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            if reasoning_open: yield {"type": "reasoning_end"}
            if answer_open: yield {"type": "answer_end"}
            result = LlmStreamResult(content, reasoning, [calls[k] for k in sorted(calls)], finish_reason, model, usage)
            yield {"type": "stream_end", "result": result}
        except Exception as exc:
            raise self._map_error(exc)[0] from exc

    @staticmethod
    def _normalise_model(model: str | None) -> str:
        return DEFAULT_MODEL if model in {None, "", "deepseek-chat", "deepseek-reasoner", "deepseek_v4", "deepseek-v4"} else model

    @staticmethod
    def serialise_assistant_message(message: Any) -> dict[str, Any]:
        """保存可再次发送的 assistant 消息，包含 reasoning_content。"""
        payload: dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None)}
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is not None:
            payload["reasoning_content"] = reasoning
        calls = getattr(message, "tool_calls", None)
        if calls:
            payload["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {
                    "name": call.function.name, "arguments": call.function.arguments,
                }} for call in calls
            ]
        return payload

    def chat(self, messages: list[dict[str, Any]], *, system_prompt: str | None = None,
             tools: list[dict[str, Any]] | None = None, thinking: bool = True,
             reasoning_effort: str | None = None, temperature: float | None = None,
             max_tokens: int = 4096, model: str | None = None,
             response_format: dict[str, Any] | None = None) -> LlmResponse:
        request_messages = list(messages)
        if system_prompt and not any(item.get("role") == "system" for item in request_messages):
            request_messages.insert(0, {"role": "system", "content": system_prompt})
        params: dict[str, Any] = {
            "model": self._normalise_model(model or self.model), "messages": request_messages,
            "max_tokens": max_tokens,
        }
        # ``thinking`` / ``reasoning_effort`` 是 DeepSeek 扩展字段。
        # OpenAI Python SDK 2.x 会拒绝未知的顶层关键字，必须经 extra_body 透传。
        extra_body: dict[str, Any] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
        if tools:
            params["tools"] = tools
        if response_format:
            params["response_format"] = response_format
        if reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort
        params["extra_body"] = extra_body
        # 思考模式不把 temperature 当作控制手段。
        if temperature is not None and not thinking:
            params["temperature"] = temperature
        response = self._call_with_retry(params)
        result = self._to_response(response, params["model"])
        if result.finish_reason in {"length", "max_tokens"}:
            raise LlmOutputTruncatedError("模型输出被长度限制截断（MODEL_OUTPUT_TRUNCATED）。")
        if result.content is None and not result.tool_calls:
            raise LlmResponseError("模型返回为空，未包含文本或工具调用。")
        return result

    def generate_json(self, messages: list[dict[str, Any]], *, schema_validator: Callable[[Any], Any] | None = None,
                      max_tokens: int = 4096, **kwargs: Any) -> Any:
        prompt = "请只输出一个符合要求的 JSON 对象，不要使用 Markdown 代码块。"
        prepared = list(messages) + [{"role": "user", "content": prompt}]
        for attempt in range(self.max_retries + 1):
            response = self.chat(prepared, max_tokens=max_tokens,
                                 response_format={"type": "json_object"}, **kwargs)
            if not response.content or not response.content.strip():
                error = "JSON 输出为空"
            else:
                try:
                    parsed = json.loads(response.content)
                    return schema_validator(parsed) if schema_validator else parsed
                except Exception as exc:
                    error = f"JSON 校验失败：{exc}"
            if attempt == self.max_retries:
                raise LlmResponseError(error)
            prepared.append({"role": "user", "content": f"上次{error}。请修正后只输出 JSON 对象。"})
        raise AssertionError("unreachable")

    def _call_with_retry(self, params: dict[str, Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.chat.completions.create(**params)
            except Exception as exc:
                mapped, retryable = self._map_error(exc)
                if not retryable or attempt == self.max_retries:
                    raise mapped from exc
                time.sleep(min(2 ** attempt, 4))
        raise AssertionError("unreachable")

    @staticmethod
    def _map_error(exc: Exception) -> tuple[LlmError, bool]:
        status = getattr(exc, "status_code", None)
        name = type(exc).__name__.lower()
        if status in {401, 403} or "authentication" in name:
            return LlmAuthenticationError("DeepSeek 身份认证失败。"), False
        if status == 429 or "ratelimit" in name:
            return LlmRateLimitError("DeepSeek 请求被限流。"), True
        if "timeout" in name:
            return LlmTimeoutError("DeepSeek 请求超时。"), True
        if status is not None and status >= 500:
            return LlmError("DeepSeek 服务端错误。"), True
        if status == 400:
            return LlmError("DeepSeek 请求参数错误，请检查模型名称和工具参数。"), False
        return LlmError("DeepSeek 请求失败。"), False

    @staticmethod
    def _to_response(raw: Any, fallback_model: str) -> LlmResponse:
        if not getattr(raw, "choices", None):
            raise LlmResponseError("模型响应中没有 choices。")
        choice = raw.choices[0]
        message = choice.message
        tool_calls = DeepSeekProvider.serialise_assistant_message(message).get("tool_calls", [])
        usage = getattr(raw, "usage", None)
        return LlmResponse(
            content=getattr(message, "content", None),
            reasoning_content=getattr(message, "reasoning_content", None),
            tool_calls=tool_calls, finish_reason=getattr(choice, "finish_reason", None),
            model=getattr(raw, "model", None) or fallback_model,
            usage=LlmUsage(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None), getattr(usage, "total_tokens", None)),
            request_id=getattr(raw, "id", None), raw_choice=choice,
        )
