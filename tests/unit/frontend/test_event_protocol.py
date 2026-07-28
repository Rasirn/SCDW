import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest

from scdw.frontend.events import summarize_tool_arguments, to_json_safe, validate_event_payload
from scdw.llm.providers.deepseek import LlmUsage


@pytest.mark.unit
def test_usage_event_is_json_serializable():
    usage = LlmUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    event = {"type": "usage", "usage": to_json_safe(usage)}
    validate_event_payload(event)
    assert event == {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


@pytest.mark.unit
def test_protocol_converter_handles_expected_structured_values():
    class State(Enum):
        READY = "ready"

    @dataclass(frozen=True)
    class Details:
        path: Path
        state: State

    payload = to_json_safe({"details": Details(Path("a/b"), State.READY), "items": {1, 2}})
    assert json.loads(json.dumps(payload)) == {"details": {"path": str(Path("a/b")), "state": "ready"}, "items": [1, 2]}


@pytest.mark.unit
def test_large_xml_argument_is_summarized_before_frontend_event():
    xml = "<Document>\n" + ("<Node />\n" * 1000) + "</Document>"
    payload = summarize_tool_arguments({"block_name": "Motor", "xml_content": xml})

    encoded = json.dumps(payload, ensure_ascii=False)
    assert xml not in encoded
    assert payload["xml_content"]["type"] == "XML"
    assert payload["xml_content"]["target_file"] == "Motor.xml"
    assert str(len(xml)) in payload["xml_content"]["summary"]
