"""The MCP client. One POST, one JSON-RPC envelope, two error channels.

The two channels are the whole reason this is a module rather than three lines
of curl. A JSON-RPC `error` means the request was wrong: the tool name does not
exist, the body was not JSON-RPC. A result with `isError` means the request was
understood and refused: you have not joined, that participant is not here. The
first is a bug to fix, the second is a message to read -- so argus raises
different exceptions for them and the CLI prints them differently.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = "argus"


class ProtocolError(RuntimeError):
    """The server could not make sense of the request. A bug, not an answer."""


class Refused(RuntimeError):
    """The server understood and declined. The text is meant to be read."""


class Unreachable(RuntimeError):
    """The hub did not answer at all."""


class Client:
    def __init__(self, url: str, key: str | None = None, timeout: float = 30.0):
        self.endpoint = url.rstrip("/") + "/mcp"
        self.key = key
        self.timeout = timeout
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": "application/json",
            # Both types, because the spec requires a client to accept either
            # a single JSON reply or a stream, and a server may choose.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise ProtocolError(f"HTTP {e.code} from the hub: {detail}") from None
        except urllib.error.URLError as e:
            raise Unreachable(f"cannot reach {self.endpoint}: {e.reason}") from None
        try:
            env = json.loads(raw)
        except json.JSONDecodeError:
            raise ProtocolError(f"the hub did not return JSON: {raw[:200]}") from None
        if "error" in env:
            err = env["error"]
            raise ProtocolError(f"{err.get('message')} (code {err.get('code')})")
        return env.get("result")

    def initialize(self) -> dict[str, Any]:
        return self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "argus", "version": "0.1.0"},
        })

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list", {}).get("tools", [])

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("isError"):
            raise Refused(texts[0] if texts else "refused without a reason")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        # A tool with nothing structured to say still said something.
        return {"message": texts[0] if texts else ""}
