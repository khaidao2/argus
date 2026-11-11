"""The client, and above all the two error channels it must keep apart."""
import pytest

from argus.client import Client, ProtocolError, Refused, Unreachable


def test_a_refusal_raises_refused_with_the_servers_own_words(hub):
    """A refusal is an answer. The text is written to be acted on, so it must
    survive to the caller instead of becoming a generic failure."""
    hub.refusal("join lab first with chat_join, so your messages carry a machine.")
    with pytest.raises(Refused) as e:
        Client(hub.url, "mck_x").call("chat_send", {"conversation": "lab"})
    assert "chat_join" in str(e.value)


def test_a_jsonrpc_error_raises_protocol_error(hub):
    """A different exception, because the right response is different: a
    refusal is read and acted on, a protocol error is a bug to fix. An agent
    that retried this would loop."""
    hub.rpc_error(-32602, "Unknown tool 'chat_nope'")
    with pytest.raises(ProtocolError) as e:
        Client(hub.url, "mck_x").call("chat_nope", {})
    assert "Unknown tool" in str(e.value)


def test_refused_and_protocol_error_are_not_the_same_exception():
    assert not issubclass(Refused, ProtocolError)
    assert not issubclass(ProtocolError, Refused)


def test_the_request_carries_what_the_spec_requires(hub):
    hub.result({"rooms": []})
    Client(hub.url, "mck_secret").call("chat_rooms", {})
    headers = hub.headers[0]
    accept = headers["accept"]
    # Both, because the server may answer with either a single JSON reply or
    # a stream, and the spec makes the client say it accepts both.
    assert "application/json" in accept
    assert "text/event-stream" in accept
    assert headers["mcp-protocol-version"] == "2025-06-18"
    assert headers["authorization"] == "Bearer mck_secret"

    body = hub.requests[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "chat_rooms", "arguments": {}}


def test_no_authorization_header_when_there_is_no_key(hub):
    """tools/list must work before the caller has a credential."""
    hub.queue({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
    Client(hub.url).list_tools()
    assert "authorization" not in hub.headers[0]


def test_structured_content_is_what_comes_back(hub):
    hub.result({"machine": "fedora-1", "identity": "testuser2"})
    out = Client(hub.url, "k").call("chat_whoami", {})
    assert out == {"machine": "fedora-1", "identity": "testuser2"}


def test_a_result_with_no_structured_content_still_returns_its_text(hub):
    hub.queue({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": "nothing to say"}], "isError": False}})
    assert Client(hub.url, "k").call("chat_rooms", {}) == {"message": "nothing to say"}


def test_an_unreachable_hub_is_its_own_error(hub):
    """Distinct from a refusal: nothing was decided, so a retry is sensible."""
    with pytest.raises(Unreachable):
        Client("http://127.0.0.1:1", "k", timeout=2).call("chat_rooms", {})


def test_a_non_json_body_is_a_protocol_error_not_a_crash(hub):
    hub.queue({}, status=500)
    with pytest.raises(ProtocolError):
        Client(hub.url, "k").call("chat_rooms", {})


def test_request_ids_increase(hub):
    hub.result({"a": 1})
    hub.result({"a": 2})
    c = Client(hub.url, "k")
    c.call("chat_rooms", {})
    c.call("chat_rooms", {})
    assert [r["id"] for r in hub.requests] == [1, 2]
