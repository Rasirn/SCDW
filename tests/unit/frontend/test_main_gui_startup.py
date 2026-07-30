from types import SimpleNamespace

import pytest

from scdw.frontend import main_gui


@pytest.mark.unit
def test_wait_for_server_prefers_uvicorn_listen_state(monkeypatch):
    monkeypatch.setattr(main_gui, "_BACKEND_SERVER", SimpleNamespace(started=True))
    monkeypatch.setattr(main_gui, "_BACKEND_ERROR", None)
    monkeypatch.setattr(main_gui, "_http_health_reachable", lambda: False)
    thread = SimpleNamespace(is_alive=lambda: True)

    assert main_gui._wait_for_server(timeout=0.1, server_thread=thread) is True


@pytest.mark.unit
def test_wait_for_server_returns_immediately_on_backend_failure(monkeypatch):
    monkeypatch.setattr(main_gui, "_BACKEND_SERVER", SimpleNamespace(started=False))
    monkeypatch.setattr(main_gui, "_BACKEND_ERROR", RuntimeError("bind failed"))
    monkeypatch.setattr(main_gui, "_http_health_reachable", lambda: False)
    thread = SimpleNamespace(is_alive=lambda: True)

    assert main_gui._wait_for_server(timeout=30, server_thread=thread) is False


@pytest.mark.unit
def test_loopback_health_probe_uses_proxy_free_opener(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    response = Response()
    opened = {}

    class Opener:
        def open(self, url, timeout):
            opened.update(url=url, timeout=timeout)
            return response

    monkeypatch.setattr(main_gui, "_LOCAL_HTTP", Opener())
    assert main_gui._http_health_reachable() is True
    assert opened == {"url": f"http://127.0.0.1:{main_gui.PORT}/health", "timeout": 0.5}
