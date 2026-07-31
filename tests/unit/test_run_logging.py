import json
import threading

from scdw.common.run_logging import RunLogManager
from scdw.common.workflow_analysis import analyze_run


def test_run_log_is_jsonl_thread_safe_and_redacts(tmp_path):
    manager = RunLogManager(tmp_path / "run", "run-test")

    def write(index):
        with manager.bind_context(conversation_id="c1", turn_id=str(index)):
            manager.log_event("test_event", component="tia", api_key="sk-secret-value", index=index)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    manager.close()

    rows = [json.loads(line) for line in (tmp_path / "run" / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [row for row in rows if row["event"] == "test_event"]
    assert len(events) == 4
    assert sorted(row["sequence"] for row in rows) == list(range(1, len(rows) + 1))
    assert all("secret-value" not in json.dumps(row) for row in events)
    assert (tmp_path / "run" / "tia" / "tia_operations.jsonl").exists()


def test_large_payload_is_saved_outside_jsonl(tmp_path):
    manager = RunLogManager(tmp_path / "run", "run-test")
    reference = manager.save_payload("request", {"text": "x" * 5000})
    manager.close()
    assert reference["payload_ref"].startswith("payloads/")
    assert (tmp_path / "run" / reference["payload_ref"]).is_file()


def test_workflow_analysis_uses_complete_parent_session_mcp_timeline(tmp_path):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "mcp" / "mcp.jsonl").write_text("", encoding="utf-8")
    rows = [
        {"event": "turn_started", "time_utc": "2026-01-01T00:00:00+00:00", "time_local": "2026-01-01T08:00:00+08:00", "query": {"inline": "创建并编译"}},
        {"event": "mcp_tool_call_started", "time_utc": "2026-01-01T00:00:01+00:00", "time_local": "2026-01-01T08:00:01+08:00", "tool_name": "import_and_compile_artifact", "payload": {"inline": {"network_key": "one"}}},
        {"event": "mcp_tool_call_finished", "time_utc": "2026-01-01T00:00:02+00:00", "time_local": "2026-01-01T08:00:02+08:00", "tool_name": "import_and_compile_artifact", "result": {"inline": "success=False"}},
        {"event": "tool_result_received", "time_utc": "2026-01-01T00:00:02.100000+00:00", "result": {"inline": {"role": "tool", "content": '{"success":false,"code":"TIA_XML_IMPORT_FAILED"}'} }},
        {"event": "turn_failed", "time_utc": "2026-01-01T00:00:03+00:00", "exception": {"type": "RuntimeError"}},
    ]
    (run / "session.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    value = analyze_run(run)
    assert value["summary"]["total_tool_calls"] == 1
    assert value["failures"][0]["code"] == "TIA_XML_IMPORT_FAILED"
    assert value["termination"]["success"] is False
    assert value["turns"][0]["query"] == "创建并编译"
