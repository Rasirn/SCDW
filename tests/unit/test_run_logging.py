import json
import threading

from scdw.common.run_logging import RunLogManager


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
