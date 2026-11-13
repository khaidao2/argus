"""`argus session`: a run with no conversation attached to it.

WHY THE PAYLOAD BELOW IS COPIED, NOT INVENTED. The same mistake this repo's
task tests record: a stub that returns a shape the hub does not send leaves
the tests and the code agreeing with each other and disagreeing with the
server. This dictionary is the one session_start really returns, field for
field.
"""
from __future__ import annotations

import json

import pytest

from argus.cli import main
from argus.config import Session, save


def run(**over) -> dict:
    """What session_start sends back, as mcp.py builds it."""
    row = {
        "agent": "omp",
        "agent_id": "a1b2c3d4-0000-4000-8000-000000000001",
        "node": "fedora-1",
        "session_id": "5e5510n0-0000-4000-8000-000000000001",
        "session_state": "active",
    }
    row.update(over)
    return row


@pytest.fixture
def logged_in_hub(hub, argus_home):
    save(Session(url=hub.url, key="mck_testkey", machine="fedora-1",
                 identity="testuser2", workspace="ws-1"))
    return hub


def test_it_calls_session_start_and_never_mentions_a_conversation(
        logged_in_hub, capsys):
    """The whole point, asserted on the wire rather than on the output."""
    logged_in_hub.result(run())
    assert main(["session", "omp", "--backend", "omp", "--session", "w1"]) == 0

    sent = logged_in_hub.requests[-1]
    assert sent["params"]["name"] == "session_start"
    args = sent["params"]["arguments"]
    assert args == {"display_name": "omp", "agent_backend": "omp",
                    "agent_session": "w1"}
    assert "conversation" not in args, (
        "a conversation here would reintroduce exactly what this removes")


def test_it_says_the_run_is_open_and_that_tasks_can_be_claimed(
        logged_in_hub, capsys):
    logged_in_hub.result(run())
    main(["session", "omp", "--backend", "omp", "--session", "w1"])
    out = capsys.readouterr().out
    assert "'w1' is open" in out
    assert "fedora-1" in out
    assert "claim tasks" in out


def test_the_run_id_is_required_because_it_is_the_thing_being_opened(
        logged_in_hub):
    """`join` can omit --session and still seat you in a room. Here there
    would be nothing left to do."""
    with pytest.raises(SystemExit) as exc:
        main(["session", "omp", "--backend", "omp"])
    assert exc.value.code != 0


def test_capabilities_and_streaming_are_passed_through(logged_in_hub):
    logged_in_hub.result(run())
    main(["session", "omp", "--backend", "omp", "--session", "w1",
          "--capability", "python", "--capability", "sql", "--streaming"])
    args = logged_in_hub.requests[-1]["params"]["arguments"]
    assert args["capabilities"] == ["python", "sql"]
    assert args["supports_streaming"] is True


def test_json_output_is_the_payload_verbatim(logged_in_hub, capsys):
    logged_in_hub.result(run())
    main(["--json", "session", "omp", "--backend", "omp", "--session", "w1"])
    assert json.loads(capsys.readouterr().out) == run()


def test_join_is_untouched_and_still_takes_a_conversation(logged_in_hub):
    """The deprecation guard on the client side: nothing is removed yet."""
    logged_in_hub.result({"label": "omp@fedora-1", "participant_id": "p1",
                          "agent": "omp", "node": "fedora-1",
                          "session_state": "active"})
    assert main(["join", "lab", "omp", "--backend", "omp",
                 "--session", "w1"]) == 0
    sent = logged_in_hub.requests[-1]
    assert sent["params"]["name"] == "chat_join"
    assert sent["params"]["arguments"]["conversation"] == "lab"
