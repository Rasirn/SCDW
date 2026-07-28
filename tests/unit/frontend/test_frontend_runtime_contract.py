"""Fast, dependency-free checks for the browser runtime's lifecycle contracts."""
from pathlib import Path
import shutil
import subprocess

import pytest


STATIC = Path("src/scdw/frontend/static")


@pytest.mark.unit
def test_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not available")
    for path in (STATIC / "js").glob("*.js"):
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True, text=True)


@pytest.mark.unit
def test_turn_lifecycle_uses_one_ticker_and_one_terminal_cleanup():
    source = (STATIC / "js/app.js").read_text(encoding="utf-8")
    assert "beginTurn(id, userText)" in source
    assert "setInterval(() => renderRunStatus(), 250)" in source
    assert "function finishTurn(id, status" in source
    assert "clearTimers();" in source
    assert "conversationState.activeTurnId = null" in source
    assert "setBusy(false)" in source
    assert "finishTurn(id, 'complete')" in source
    assert "finishTurn(id, 'cancelled')" in source
    assert "finishTurn(id, 'disconnected')" in source


@pytest.mark.unit
def test_scroll_stream_buffer_and_terminal_race_guards_are_present():
    source = (STATIC / "js/renderer.js").read_text(encoding="utf-8")
    assert "requestAnimationFrame" in source
    assert "stream.textNode.data += stream.pending" in source
    assert "box.scrollHeight - box.scrollTop - box.clientHeight <= 80" in source
    assert "autoFollow = nearBottom()" in source
    assert "latestButton.hidden = false" in source
    assert "tool.status !== 'running'" in source


@pytest.mark.unit
def test_tool_arguments_are_collapsed_and_defensively_summarized():
    source = (STATIC / "js/renderer.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "safeArgumentSummary" in source
    assert "name.toLowerCase() === 'xml_content'" in source
    assert "args.className = 'tool-arguments'" in source
    assert "args.open" not in source
    assert 'id="run-status"' in html
    assert 'id="new-content"' in html
    assert 'id="resend"' in html
