"""A stub hub, so the tests exercise the real HTTP path without a server.

Monkeypatching urlopen would test argus against a mock of a protocol rather
than the protocol, and the headers are part of what matters here: a missing
Accept type or protocol version is exactly the kind of bug a mock hides.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class Hub:
    """Records what it was sent and replies with whatever a test queued."""

    def __init__(self):
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.replies: list[tuple[int, dict]] = []
        self.login_config: dict = {}

    def queue(self, body: dict, status: int = 200) -> None:
        self.replies.append((status, body))

    def result(self, payload: dict, is_error: bool = False) -> None:
        self.queue({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "summary"}],
            "structuredContent": payload, "isError": is_error}})

    def refusal(self, reason: str) -> None:
        self.queue({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": reason}], "isError": True}})

    def rpc_error(self, code: int, message: str) -> None:
        self.queue({"jsonrpc": "2.0", "id": 1,
                    "error": {"code": code, "message": message}})


@pytest.fixture
def hub():
    state = Hub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/api/cli/login-config":
                self._send(200, state.login_config)
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode()
            state.requests.append(json.loads(raw) if raw else {})
            # Lower-cased, because HTTP header names are case-insensitive and
            # urllib capitalises what it is given: a case-sensitive assertion
            # here would fail on a request that is perfectly correct.
            state.headers.append({k.lower(): v for k, v in self.headers.items()})
            if state.replies:
                status, body = state.replies.pop(0)
            else:
                status, body = 200, {"jsonrpc": "2.0", "id": 1, "result": {}}
            self._send(status, body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def argus_home(tmp_path, monkeypatch):
    home = tmp_path / "argus-home"
    monkeypatch.setenv("ARGUS_HOME", str(home))
    monkeypatch.delenv("ARGUS_KEY", raising=False)
    monkeypatch.delenv("ARGUS_URL", raising=False)
    return home
