"""DeepSeek V4 Provider 离线单元测试，不访问真实 API。"""
from types import SimpleNamespace

import pytest

from scdw.common.exceptions import LlmAuthenticationError, LlmOutputTruncatedError, LlmRateLimitError
from scdw.llm.providers.deepseek import DeepSeekProvider


def _response(content="ok", finish_reason="stop", reasoning=None, tool_calls=None):
    message = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="deepseek-v4-pro", id="req-1", usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3))


class _Client:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.unit
def test_system_message_and_thinking_are_serialised():
    client = _Client([_response()])
    provider = DeepSeekProvider(client=client)
    provider.chat([{"role": "user", "content": "hi"}], system_prompt="system", thinking=True)
    params = client.calls[0]
    assert params["messages"][0] == {"role": "system", "content": "system"}
    assert params["extra_body"]["thinking"] == {"type": "enabled"}
    assert "system" not in params


@pytest.mark.unit
def test_reasoning_and_tool_calls_are_preserved():
    call = SimpleNamespace(id="call-1", function=SimpleNamespace(name="tool", arguments='{"x":1}'))
    client = _Client([_response(content=None, finish_reason="tool_calls", reasoning="private", tool_calls=[call])])
    result = DeepSeekProvider(client=client).chat([{"role": "user", "content": "hi"}])
    saved = DeepSeekProvider.serialise_assistant_message(result.message)
    assert saved["reasoning_content"] == "private"
    assert saved["tool_calls"][0]["function"]["name"] == "tool"


@pytest.mark.unit
def test_length_finish_reason_is_rejected():
    with pytest.raises(LlmOutputTruncatedError):
        DeepSeekProvider(client=_Client([_response(finish_reason="length")])).chat([{"role": "user", "content": "hi"}])


@pytest.mark.unit
def test_rate_limit_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("scdw.llm.providers.deepseek.time.sleep", lambda _: None)
    error = Exception("limited")
    error.status_code = 429
    client = _Client([error, _response()])
    assert DeepSeekProvider(client=client).chat([{"role": "user", "content": "hi"}]).content == "ok"
    assert len(client.calls) == 2


@pytest.mark.unit
def test_authentication_error_does_not_retry():
    error = Exception("unauthorized")
    error.status_code = 401
    client = _Client([error])
    with pytest.raises(LlmAuthenticationError):
        DeepSeekProvider(client=client).chat([{"role": "user", "content": "hi"}])
    assert len(client.calls) == 1


@pytest.mark.unit
def test_json_output_is_locally_validated():
    client = _Client([_response('{"value": 1}')])
    assert DeepSeekProvider(client=client).generate_json([{"role": "user", "content": "json"}], schema_validator=lambda x: x["value"]) == 1
