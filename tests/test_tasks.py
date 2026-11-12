"""The task commands, against the payloads the hub really sends.

WHY THE FIXTURES BELOW ARE SPELLED OUT IN FULL. An earlier version of these
tests invented a shape, asserting against `{"task": {"short": ...}}` when the
hub sends `task` as a plain id string and `held_by` as an object. Both tests
and code agreed with each other and disagreed with the server, so `argus
claim` raised AttributeError the moment it succeeded and the suite stayed
green. A stub is only worth having if what it returns is what the real thing
returns, so these dictionaries are copied from the hub's own task payload.
"""
from __future__ import annotations

import pytest

from argus.cli import main
from argus.config import Session, save


def task(**over) -> dict:
    """One task exactly as `_task_payload` on the hub builds it."""
    row = {
        "task": "a1b2c3d4-0000-4000-8000-000000000001",
        "short": "a1b2c3d4",
        "title": "Apply migration 011",
        "detail": "",
        "state": "assigned",
        "assigned_to": "worker",
        "assigned_to_kind": "agent",
        "held_by": None,
        "priority": 50,
        "claim_count": 0,
        "handoff_count": 0,
        "result": None,
        "blocked_reason": None,
        "created_by": "testuser1",
        "created_at": "2026-11-12T01:00:00+00:00",
        "last_event": {"cause": "created", "detail": None,
                       "at": "2026-11-12T01:00:00+00:00"},
    }
    row.update(over)
    return row


@pytest.fixture
def logged_in_hub(hub, argus_home):
    save(Session(url=hub.url, key="mck_testkey", machine="fedora-1",
                 identity="testuser2", workspace="ws-1"))
    return hub


def test_no_tasks_says_so_rather_than_printing_nothing(logged_in_hub, capsys):
    logged_in_hub.result({"tasks": [], "scope": "mine"})
    assert main(["tasks"]) == 0
    assert "No open tasks." in capsys.readouterr().out


def test_a_held_task_shows_which_run_holds_it(logged_in_hub, capsys):
    """The most important thing on the row.

    A task that is merely assigned is waiting for somebody. A held task is
    being worked on by one specific run, and an operator deciding whether to
    intervene needs to know which.
    """
    logged_in_hub.result({"tasks": [task(
        state="in_progress",
        held_by={"session": "run-1", "state": "active", "responsive": True})],
        "scope": "mine"})
    assert main(["tasks"]) == 0
    out = capsys.readouterr().out
    assert "[held by run-1]" in out
    assert "silent" not in out


def test_a_task_held_by_a_run_that_stopped_answering_is_marked_silent(
        logged_in_hub, capsys):
    """A disconnected holder may be dead. A row that looked the same as a
    healthy one would hide the only symptom there is."""
    logged_in_hub.result({"tasks": [task(
        state="in_progress",
        held_by={"session": "run-1", "state": "disconnected",
                 "responsive": False})], "scope": "mine"})
    assert main(["tasks"]) == 0
    assert "[held by run-1 silent]" in capsys.readouterr().out


def test_an_unassigned_task_shows_a_dash_not_the_word_none(
        logged_in_hub, capsys):
    logged_in_hub.result({"tasks": [task(state="open", assigned_to=None,
                                         assigned_to_kind=None)],
                          "scope": "all"})
    assert main(["tasks", "--all"]) == 0
    assert "None" not in capsys.readouterr().out


def test_delegate_does_not_read_stdin_unless_asked(logged_in_hub, capsys):
    """Detail is optional, so a non-interactive caller must not block on stdin.

    `argus delegate "x" --to worker` from a script or an agent harness has no
    terminal, and reading stdin there would hang or swallow unrelated input.
    Only `--detail -` reads it.
    """
    logged_in_hub.result(task())
    assert main(["delegate", "no detail wanted", "--to", "worker"]) == 0
    assert "detail" not in logged_in_hub.requests[-1]["params"]["arguments"]


def test_delegate_sends_the_owner_and_reports_that_they_were_told(
        logged_in_hub, capsys):
    logged_in_hub.result(task(owner_notified=True, announced_seq=7))
    assert main(["delegate", "Apply migration 011", "--to", "worker",
                 "-c", "lab"]) == 0
    args = logged_in_hub.requests[-1]["params"]["arguments"]
    assert logged_in_hub.requests[-1]["params"]["name"] == "task_create"
    assert args["assign_to_agent"] == "worker"
    assert args["conversation"] == "lab"
    assert "detail" not in args, "an absent flag must not be sent as empty"
    assert "worker has it" in capsys.readouterr().out


def test_delegate_with_no_owner_warns_that_nothing_will_pick_it_up(
        logged_in_hub, capsys):
    """Otherwise the caller believes it has delegated something."""
    logged_in_hub.result(task(state="open", assigned_to=None,
                              assigned_to_kind=None, owner_notified=False))
    assert main(["delegate", "orphan"]) == 0
    assert "Nobody owns it yet" in capsys.readouterr().out


def test_claim_with_nothing_waiting_is_not_an_error(logged_in_hub, capsys):
    logged_in_hub.result({"task": None})
    assert main(["claim"]) == 0
    out = capsys.readouterr().out
    assert "Nothing is waiting for you." in out
    assert "task" not in logged_in_hub.requests[-1]["params"]["arguments"]


def test_a_successful_claim_names_the_task_it_took(logged_in_hub, capsys):
    """The case that was broken.

    `task` is an id string, so treating it as an object raised AttributeError
    on exactly the path that works.
    """
    logged_in_hub.result(task(
        state="in_progress", claim_count=1,
        held_by={"session": "run-1", "state": "active", "responsive": True}))
    assert main(["claim"]) == 0
    out = capsys.readouterr().out
    assert "You hold a1b2c3d4 (Apply migration 011)." in out


def test_report_sends_the_state_and_the_result(logged_in_hub, capsys):
    logged_in_hub.result(task(state="done", result="applied"))
    assert main(["report", "a1b2c3d4", "done", "--result", "applied"]) == 0
    args = logged_in_hub.requests[-1]["params"]["arguments"]
    assert args["state"] == "done"
    assert args["result"] == "applied"
    assert "a1b2c3d4 is done." in capsys.readouterr().out


def test_reporting_a_failure_says_the_task_is_still_yours(
        logged_in_hub, capsys):
    """A failed task stays assignable, which is the whole reason handoff is
    useful. An agent that thought failing closed the task would stop."""
    logged_in_hub.result(task(state="failed", result="no psql here"))
    assert main(["report", "a1b2c3d4", "failed", "--result", "no psql"]) == 0
    out = capsys.readouterr().out
    assert "still assigned to you" in out
    assert "argus handoff" in out


def test_report_maps_reason_to_the_hubs_blocked_reason(logged_in_hub):
    logged_in_hub.result(task(state="blocked", blocked_reason="needs a password"))
    assert main(["report", "a1b2c3d4", "blocked", "--reason",
                 "needs a password"]) == 0
    args = logged_in_hub.requests[-1]["params"]["arguments"]
    assert args["blocked_reason"] == "needs a password"
    assert "reason" not in args


def test_handoff_without_a_reason_is_refused_by_the_parser(logged_in_hub):
    """The hub requires one, so failing locally saves a round trip and gives
    the usual argparse message."""
    with pytest.raises(SystemExit) as exit_code:
        main(["handoff", "a1b2c3d4", "--to", "helper"])
    assert exit_code.value.code == 2


def test_handoff_without_a_recipient_is_refused_before_calling_the_hub(
        logged_in_hub, capsys):
    assert main(["handoff", "a1b2c3d4", "--reason", "because"]) == 2
    assert "--to or --to-person" in capsys.readouterr().err
    assert logged_in_hub.requests == []


def test_handoff_says_when_the_new_owner_was_not_notified(
        logged_in_hub, capsys):
    """Silence here would leave the caller waiting for a reply from somebody
    who has no idea the task exists."""
    logged_in_hub.result(task(assigned_to="helper", handoff_count=1,
                              owner_notified=False, announced_seq=None))
    assert main(["handoff", "a1b2c3d4", "--to", "helper",
                 "--reason", "helper has psql"]) == 0
    out = capsys.readouterr().out
    assert "a1b2c3d4 is now helper's." in out
    assert "not notified" in out
    args = logged_in_hub.requests[-1]["params"]["arguments"]
    assert args["to_agent"] == "helper"
    assert args["reason"] == "helper has psql"


def test_release_says_the_task_is_still_yours(logged_in_hub, capsys):
    logged_in_hub.result(task(state="assigned", claim_count=1))
    assert main(["release", "a1b2c3d4"]) == 0
    assert "still assigned to you" in capsys.readouterr().out


def test_every_task_command_passes_the_agent_flag_through(logged_in_hub):
    """One owner can run several agents, and the hub refuses rather than
    guessing which is acting. A command that dropped the flag would make that
    refusal impossible to satisfy."""
    # --agent is global, so it comes before the subcommand, as it does for
    # every other command that acts as an agent.
    for argv, tool in (
        (["--agent", "worker", "tasks"], "task_list"),
        (["--agent", "worker", "delegate", "x"], "task_create"),
        (["--agent", "worker", "claim"], "task_claim"),
    ):
        logged_in_hub.result(task() if tool != "task_list"
                             else {"tasks": [], "scope": "mine"})
        assert main(argv) == 0
        sent = logged_in_hub.requests[-1]["params"]
        assert sent["name"] == tool
        assert sent["arguments"]["agent"] == "worker"
