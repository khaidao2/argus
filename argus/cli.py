"""The argus command line.

Two audiences, one surface. A person runs `argus read lab` and wants a readable
transcript; an agent runs `argus --json read lab` and wants to parse it. Every
command therefore prints a human rendering by default and the server's own
structured payload under --json, so an agent never has to screen-scrape.

Every subcommand but `login` needs a session key, and the key decides which
machine the caller is on. There is deliberately no --machine flag on `join`:
the key was issued for one machine, and letting a flag override it would make
the chat's machine labels a claim rather than a fact.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any

from . import __version__
from .client import Client, ProtocolError, Refused, Unreachable
from .config import DEFAULT_URL, clear, config_dir, load
from .login import login as do_login

EX_USAGE, EX_REFUSED, EX_PROTOCOL, EX_UNREACHABLE, EX_NOAUTH = 2, 3, 4, 5, 6


def _session_or_exit(args):
    s = load()
    if s is None:
        print("Not logged in. Run `argus login` first.", file=sys.stderr)
        raise SystemExit(EX_NOAUTH)
    if getattr(args, "url", None):
        s.url = args.url.rstrip("/")
    return s


def _emit(args, payload: Any, render) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        render(payload)


def _fmt_message(m: dict) -> str:
    mark = "*" if m.get("mentions_me") else " "
    when = str(m.get("created_at", ""))[11:19]
    return (f"{mark} [{m.get('seq')}] {when} "
            f"{m.get('from')} ({m.get('from_kind')}) -> {m.get('body')}")



def _with_agent(args, arguments: dict) -> dict:
    """Add --agent when given.

    The hub refuses rather than guesses when one owner has several agents in a
    conversation, because picking one would attribute the message to the wrong
    agent and the record would look entirely ordinary while being wrong.
    """
    if getattr(args, "agent", None):
        arguments["agent"] = args.agent
    return arguments


def cmd_login(args) -> int:
    session = do_login(url=(args.url or DEFAULT_URL), machine=args.machine,
                       label=args.label, open_browser=not args.no_browser)
    if args.json:
        print(json.dumps({"identity": session.identity, "machine": session.machine,
                          "workspace": session.workspace, "url": session.url,
                          "key_prefix": session.prefix}, indent=2))
    return 0


def cmd_logout(args) -> int:
    clear()
    print(f"Session key removed from {config_dir()}.")
    print("It is still valid at the hub until revoked there: "
          "`argus keys` lists it, and the web UI revokes it.")
    return 0


def cmd_whoami(args) -> int:
    s = _session_or_exit(args)
    c = Client(s.url, s.key)
    # Asked of the hub, not read from the local file. The machine in the local
    # metadata is only a note from login time; the hub's answer comes from the
    # key itself, and that is the one every other participant will see.
    me = c.call("chat_whoami", {})
    seen = c.call("chat_machines", {}).get("machines", [])
    _emit(args, {**me, "url": s.url, "machines_seen": seen},
          lambda p: (
              print(f"{p['identity']} on {p['machine']}  "
                    f"({p['key_prefix']}... at {p['url']})"),
              print("Machines seen by the hub:"),
              [print(f"  {m['machine']:<20} agents={m['agents']} humans={m['humans']}"
                     f"  last seen {m['last_seen_at']}") for m in p["machines_seen"]],
          ))
    return 0


def cmd_rooms(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_rooms", {})
    _emit(args, payload, lambda p: [
        print(f"{r['conversation']:<24} {r['participants']} participant(s)  "
              f"machines: {', '.join(r['machines']) or '-'}")
        for r in p.get("rooms", [])] or print("No conversations yet."))
    return 0


def cmd_open(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_open", {
        "conversation": args.conversation, "topic": args.topic or "",
    })
    _emit(args, payload, lambda p: print(
        f"{p['conversation']!r} is open ({p['participants']} participant(s)"
        + (f", machines: {', '.join(p['machines'])}" if p["machines"] else "")
        + f"). Topic: {p['topic'] or '-'}"))
    return 0


def cmd_inbox(args) -> int:
    s = _session_or_exit(args)
    arguments = {"conversation": args.conversation}
    if args.all:
        arguments["include_unaddressed"] = True
    if args.limit:
        arguments["limit"] = args.limit
    payload = Client(s.url, s.key).call("chat_inbox", _with_agent(args, arguments))

    def render(p):
        if not p["waiting"]:
            print("Nothing waiting for you.")
            return
        for w in p["waiting"]:
            print(f"[{w['seq']}] {w['state']:<10} from {w['from']}")
            print(f"      {w['body']}")
    _emit(args, payload, render)
    return 0


def cmd_ack(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_ack", _with_agent(args, {
        "conversation": args.conversation, "seq": args.seq,
        "outcome": args.outcome, "detail": args.detail or ""}))
    _emit(args, payload, lambda p: print(f"#{p['seq']} is now {p['state']}."))
    return 0


def cmd_heartbeat(args) -> int:
    s = _session_or_exit(args)
    arguments = {}
    if args.state:
        arguments["state"] = args.state
    if args.session:
        arguments["session"] = args.session
    payload = Client(s.url, s.key).call("chat_heartbeat", _with_agent(args, arguments))
    _emit(args, payload, lambda p: print(
        f"{p['session']} is {p['state']}, lease until {p['lease_expires_at']}"))
    return 0


def cmd_end(args) -> int:
    s = _session_or_exit(args)
    arguments = {"reason": args.reason or "stopped by the runtime"}
    if args.session:
        arguments["session"] = args.session
    payload = Client(s.url, s.key).call("chat_session_end", _with_agent(args, arguments))

    def render(p):
        print(f"{p['session']} ended at {p['ended_at']}.")
        print("You are still a participant. Anything addressed to you is "
              "waiting for your next session.")
        # Named, not counted. An operator who stops a run mid-task needs to
        # know which work went back into the queue, and the alternative is
        # finding out from a task list they have no reason to open.
        freed = p.get("released_tasks") or []
        if freed:
            print(f"{len(freed)} unfinished task(s) went back into your queue "
                  "rather than being lost:")
            for t in freed:
                print(f"  {t['short']}  {t['state']:<10}  {t['title']}")
    _emit(args, payload, render)
    return 0


def cmd_agents(args) -> int:
    s = _session_or_exit(args)
    arguments = {}
    if args.capability:
        arguments["capability"] = args.capability
    if args.all:
        arguments["include_inactive"] = True
    payload = Client(s.url, s.key).call("chat_agents", arguments)

    def render(p):
        if not p.get("agents"):
            print("No agents.")
            return
        for a in p["agents"]:
            reach = ("reachable" if a["responsive_sessions"]
                     else ("live but silent" if a["live_sessions"] else "no session"))
            caps = ", ".join(a["capabilities"]) or "-"
            print(f"{a['agent']:<22} {a['runtime']:<14} {a['status']:<9} "
                  f"{reach:<16} {caps}")
    _emit(args, payload, render)
    return 0


def cmd_sessions(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_sessions", {
        "agent": args.which_agent, "live_only": bool(args.live)})

    def render(p):
        for x in p["sessions"]:
            when = x["ended_at"] or x["last_heartbeat_at"]
            print(f"{x['session']:<18} {x['state']:<13} "
                  f"{'responsive' if x['responsive'] else '          '} "
                  f"{str(when)[:19]}  {x['end_reason'] or x['last_cause'] or ''}")
    _emit(args, payload, render)
    return 0


def cmd_join(args) -> int:
    s = _session_or_exit(args)
    arguments = _with_agent(args, {
        "conversation": args.conversation,
        "display_name": args.name,
        "agent_backend": args.backend,
        "agent_session": args.session,
    })
    if args.capability:
        arguments["capabilities"] = args.capability
    if args.streaming:
        arguments["supports_streaming"] = True
    payload = Client(s.url, s.key).call("chat_join", arguments)
    _emit(args, payload, lambda p: print(
        f"Joined {args.conversation} as {p['label']} "
        f"(participant {p['participant_id']}).\n"
        f"You are the agent {p.get('agent')!r} on node {p.get('node')}, "
        f"session {p.get('session_state') or 'none declared'}."))
    return 0


def cmd_who(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call(
        "chat_who", _with_agent(args, {"conversation": args.conversation}))
    def render(p):
        for part in p.get("participants", []):
            you = "  <- you" if part["participant_id"] == p.get("you") else ""
            seen = "present" if part.get("present") else "away"
            print(f"{part['participant_id']}  {part['label']:<32} "
                  f"{part['kind']:<6} {seen}{you}")
    _emit(args, payload, render)
    return 0


def cmd_send(args) -> int:
    s = _session_or_exit(args)
    body = args.body if args.body is not None else sys.stdin.read()
    if not body.strip():
        print("Nothing to send.", file=sys.stderr)
        return EX_USAGE
    arguments = {"conversation": args.conversation, "body": body.strip()}
    if args.to:
        arguments["mentions"] = args.to
    if args.id:
        # The one identifier a client controls, so a retry cannot double-post.
        arguments["client_message_id"] = args.id
    payload = Client(s.url, s.key).call("chat_send", _with_agent(args, arguments))
    _emit(args, payload, lambda p: print(
        f"Sent as #{p['seq']}." + (
            f" Named: {', '.join(p['mentioned'])}" if p.get("mentioned")
            else " Nobody was named, so no agent is woken by it.")))
    return 0


def cmd_read(args) -> int:
    s = _session_or_exit(args)
    c = Client(s.url, s.key)
    arguments = {"conversation": args.conversation}
    if args.mine:
        arguments["mentioning_me"] = True
    if args.after is not None:
        arguments["after_seq"] = args.after
    if args.limit:
        arguments["limit"] = args.limit

    if not args.follow:
        payload = c.call("chat_read", _with_agent(args, arguments))
        _emit(args, payload, lambda p: [print(_fmt_message(m)) for m in p["messages"]]
              or print("(nothing new)"))
        return 0

    # --follow polls, because the hub pushes nothing. The cursor the server
    # returns is what stops a poll from re-reading what it has already seen.
    after = args.after or 0
    deadline = time.monotonic() + args.timeout if args.timeout else None
    while True:
        arguments["after_seq"] = after
        payload = c.call("chat_read", _with_agent(args, arguments))
        for m in payload["messages"]:
            print(_fmt_message(m), flush=True)
        after = payload["next_after_seq"]
        if payload["messages"] and args.until_message:
            return 0
        if deadline and time.monotonic() > deadline:
            return 0
        time.sleep(args.interval)


def cmd_machines(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_machines", {})
    _emit(args, payload, lambda p: [
        print(f"{m['machine']:<20} agents={m['agents']} humans={m['humans']}"
              f"  last seen {m['last_seen_at']}") for m in p.get("machines", [])])
    return 0


def cmd_tools(args) -> int:
    s = load()
    url = (args.url or (s.url if s else DEFAULT_URL))
    c = Client(url, s.key if s else None)
    info = c.initialize()
    tools = c.list_tools()
    _emit(args, {"server": info.get("serverInfo"),
                 "protocolVersion": info.get("protocolVersion"),
                 "tools": tools},
          lambda p: (print(f"{p['server'].get('name')} "
                           f"{p['server'].get('version','')} "
                           f"(MCP {p['protocolVersion']})"),
                     [print(f"  {t['name']:<16} {t['description'].splitlines()[0]}")
                      for t in p["tools"]]))
    return 0


def _held(t: dict) -> str:
    """How a task row says a run is working on it.

    The most important thing on the line. A task that is merely assigned is
    waiting for somebody; a task being held is being worked on by one specific
    run, and a run that has stopped answering may be dead while its work still
    looks like it is progressing.
    """
    held = t.get("held_by")
    if not held:
        return ""
    silent = "" if held.get("responsive") else " silent"
    return f"  [held by {held.get('session')}{silent}]"


def _task_line(t: dict, widths: tuple[int, int, int]) -> str:
    w_short, w_state, w_owner = widths
    owner = t.get("assigned_to") or "-"
    hands = f"  ({t['handoff_count']} handoff)" if t.get("handoff_count") else ""
    return (f"{t.get('short', ''):<{w_short}}  {t.get('state', ''):<{w_state}}  "
            f"{owner:<{w_owner}}  {t.get('title', '')}{_held(t)}{hands}")


def cmd_tasks(args) -> int:
    s = _session_or_exit(args)
    arguments: dict[str, Any] = {}
    if args.all:
        arguments["scope"] = "all"
    if args.conversation:
        arguments["conversation"] = args.conversation
    if args.closed:
        arguments["include_closed"] = True
    if args.limit is not None:
        arguments["limit"] = args.limit
    payload = Client(s.url, s.key).call("task_list", _with_agent(args, arguments))

    def render(p):
        tasks = p["tasks"]
        if not tasks:
            print("No open tasks." if not args.all
                  else "No tasks in this workspace.")
            return
        widths = (
            max(len(t.get("short", "")) for t in tasks),
            max(len(t.get("state", "")) for t in tasks),
            max(len(t.get("assigned_to") or "-") for t in tasks),
        )
        for t in tasks:
            print(_task_line(t, widths))
    _emit(args, payload, render)
    return 0


def cmd_delegate(args) -> int:
    s = _session_or_exit(args)
    # `--detail -` reads stdin, and nothing else does. Reading it whenever
    # --detail was absent would hang any non-interactive caller, and detail is
    # optional here, unlike the body of `argus send`.
    detail = sys.stdin.read().strip() if args.detail == "-" else args.detail
    arguments: dict[str, Any] = {"title": args.title}
    if detail:
        arguments["detail"] = detail
    if args.to:
        arguments["assign_to_agent"] = args.to
    if args.to_person:
        arguments["assign_to_person"] = args.to_person
    if args.conversation:
        arguments["conversation"] = args.conversation
    if args.priority is not None:
        arguments["priority"] = args.priority
    if args.path:
        arguments["paths"] = args.path
    payload = Client(s.url, s.key).call("task_create", _with_agent(args, arguments))

    def render(p):
        print(f"Task {p['short']} created: {p['title']}")
        if p.get("paths"):
            print("  touches: " + ", ".join(p["paths"])
                  + "  (read them with `argus context`)")
        if p.get("assigned_to") is None:
            print("Nobody owns it yet, so nothing will pick it up. "
                  "Give it an owner with `argus handoff`.")
        elif p.get("owner_notified"):
            print(f"{p['assigned_to']} has it, and will still have it if they "
                  "are not running now.")
        else:
            print(f"{p['assigned_to']} owns it but was not notified; "
                  "they will see it with `argus tasks`.")
    _emit(args, payload, render)
    return 0


def cmd_claim(args) -> int:
    s = _session_or_exit(args)
    arguments: dict[str, Any] = {}
    if args.task:
        arguments["task"] = args.task
    if args.conversation:
        arguments["conversation"] = args.conversation
    if args.session:
        arguments["session"] = args.session
    payload = Client(s.url, s.key).call("task_claim", _with_agent(args, arguments))

    def render(p):
        # `task` is the id, and it is null when nothing was waiting. That null
        # is an ordinary answer rather than a refusal, so it is not an error.
        if p.get("task") is None:
            print("Nothing is waiting for you.")
            print("A task you failed is not offered again automatically; "
                  "claim it by name to retry it.")
            return
        print(f"You hold {p['short']} ({p['title']}).")
        if p.get("detail"):
            print(f"  {p['detail']}")
        print("It is yours until you report on it, release it, or your "
              "session ends.")
    _emit(args, payload, render)
    return 0


def cmd_report(args) -> int:
    s = _session_or_exit(args)
    arguments: dict[str, Any] = {"task": args.task, "state": args.state}
    if args.result:
        arguments["result"] = args.result
    if args.reason:
        arguments["blocked_reason"] = args.reason
    if args.session:
        arguments["session"] = args.session
    payload = Client(s.url, s.key).call("task_report", _with_agent(args, arguments))

    def render(p):
        print(f"{p['short']} is {p['state']}.")
        if p["state"] == "failed":
            print("It is still assigned to you. Retry it by name, or hand it "
                  "on with `argus handoff`.")
        elif p["state"] == "blocked":
            print("Your hold is released. Claim it again to resume.")
    _emit(args, payload, render)
    return 0


def cmd_handoff(args) -> int:
    s = _session_or_exit(args)
    if not (args.to or args.to_person):
        print("Name who it goes to with --to or --to-person.", file=sys.stderr)
        return EX_USAGE
    arguments: dict[str, Any] = {"task": args.task, "reason": args.reason}
    if args.to:
        arguments["to_agent"] = args.to
    if args.to_person:
        arguments["to_person"] = args.to_person
    payload = Client(s.url, s.key).call("task_handoff", _with_agent(args, arguments))

    def render(p):
        print(f"{p['short']} is now {p['assigned_to']}'s.")
        if not p.get("owner_notified"):
            print("They were not notified; they will see it with "
                  "`argus tasks`.")
    _emit(args, payload, render)
    return 0


def cmd_release(args) -> int:
    s = _session_or_exit(args)
    arguments: dict[str, Any] = {"task": args.task}
    if args.detail:
        arguments["detail"] = args.detail
    if args.session:
        arguments["session"] = args.session
    payload = Client(s.url, s.key).call("task_release", _with_agent(args, arguments))
    _emit(args, payload, lambda p: print(
        f"Released {p['short']}. It is still assigned to you and waiting for "
        "your next run."))
    return 0


def _git(*args: str) -> str | None:
    """A git value from the working tree, or None when there is no tree.

    None rather than an exception: `argus` is useful outside a checkout for
    everything except the two context commands, and those say so themselves.
    """
    try:
        out = subprocess.run(("git", *args), capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _ctx_report(scope: str) -> dict | None:
    """What ctx says about a path, as a report, or None if it could not say.

    Shelling out to the real binary on purpose. ctx owns every question about
    what a decision is and whether it changed; re-deriving any of it here would
    produce a second answer that eventually disagrees with the first.
    """
    try:
        out = subprocess.run(("ctx", "for", scope, "-format", "json"),
                             capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("ctx is not on PATH. Install it to record decision context.",
              file=sys.stderr)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ctx failed: {exc}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"ctx for {scope}: {out.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        print(f"ctx printed something that is not JSON for {scope}.",
              file=sys.stderr)
        return None


def cmd_context(args) -> int:
    s = _session_or_exit(args)
    client = Client(s.url, s.key)

    if not args.path:
        payload = client.call("task_context_get", {"task": args.task})

        def render(p):
            if not p["contexts"]:
                print("No paths recorded for this task.")
                print("Record one with `argus context <task> <path>`.")
                return
            for c in p["contexts"]:
                if c["captured"]:
                    print(f"{c['scope']}")
                    print(f"  {c['summary']}  at {c['repo_head'][:12]} "
                          f"from {c['captured_on_machine']} "
                          f"by {c['captured_by']}")
                else:
                    print(f"{c['scope']}")
                    print(f"  {c['summary']} (declared by {c['declared_by']})")
            print()
            print("Run `argus drift <task>` to see what has changed since.")
        _emit(args, payload, render)
        return 0

    head = _git("rev-parse", "HEAD")
    if head is None:
        print("Not in a git working tree, so there is nothing to read "
              "decisions from.", file=sys.stderr)
        return EX_USAGE

    results = []
    for scope in args.path:
        report = _ctx_report(scope)
        if report is None:
            return EX_USAGE
        arguments = {"task": args.task, "scope": scope, "report": report,
                     "repo_head": head}
        if args.session:
            arguments["session"] = args.session
        results.append(client.call("task_context_put",
                                   _with_agent(args, arguments)))

    def render(_p):
        for r in results:
            print(f"{r['scope']}: {r['summary']} at {head[:12]}")
        print("Whoever picks this task up next can diff against it.")
    _emit(args, results if args.json else results[-1], render)
    return 0


def cmd_drift(args) -> int:
    """What has changed in the decisions since this task's baseline was taken.

    The hub holds the baseline; ctx computes the delta. This command only
    carries one to the other, which is the division of labour the whole
    feature rests on.
    """
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("task_context_get", {"task": args.task})
    captured = [c for c in payload["contexts"] if c["captured"]]
    unlooked = [c["scope"] for c in payload["contexts"] if not c["captured"]]

    if not captured:
        print("No context has been captured for this task, so there is "
              "nothing to compare against.")
        if unlooked:
            print("Declared but never read: " + ", ".join(unlooked))
            print(f"Capture it with `argus context {args.task} "
                  + " ".join(unlooked) + "`.")
        return 0

    if _git("rev-parse", "HEAD") is None:
        print("Not in a git working tree, so ctx has nothing to compare "
              "against.", file=sys.stderr)
        return EX_USAGE

    moved = 0
    with tempfile.TemporaryDirectory(prefix="argus-ctx-") as tmp:
        for c in captured:
            baseline = pathlib.Path(tmp) / f"{abs(hash(c['scope']))}.json"
            baseline.write_text(json.dumps(c["report"]))
            try:
                out = subprocess.run(
                    ("ctx", "for", c["scope"], "-since", str(baseline)),
                    capture_output=True, text=True, timeout=30)
            except FileNotFoundError:
                print("ctx is not on PATH.", file=sys.stderr)
                return EX_USAGE
            delta = out.stdout.strip()
            if delta:
                moved += 1
                print(delta)
                print()
            elif args.verbose:
                print(f"{c['scope']}: unchanged since "
                      f"{c['repo_head'][:12]} ({c['captured_on_machine']})")

    if moved == 0:
        print(f"Nothing has changed in {len(captured)} scope(s) since this "
              "task's context was captured.")
    else:
        print(f"{moved} of {len(captured)} scope(s) moved under this task. "
              "Read the above before reporting it done.")
    if unlooked:
        print("Declared but never read: " + ", ".join(unlooked))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="argus",
        description="Talk to the Mission Control agent chat from a terminal.")
    p.add_argument("--version", action="version", version=f"argus {__version__}")
    p.add_argument("--json", action="store_true",
                   help="print the hub's structured payload instead of a rendering")
    p.add_argument("--url", help=f"hub base URL (default: the logged-in one, else {DEFAULT_URL})")
    p.add_argument("--agent",
                   help="which of your agents is speaking. Needed only when "
                        "you own more than one in the same conversation.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("login", help="log in through the browser and save a session key")
    sp.add_argument("--machine", help="name this machine goes by in the chat "
                                     "(default: $ARGUS_MACHINE, /etc/argus-machine, hostname)")
    sp.add_argument("--label", help="a note shown beside the key in the hub")
    sp.add_argument("--no-browser", action="store_true",
                    help="never try to open a browser; just print the URL")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("logout", help="delete the local session key")
    sp.set_defaults(func=cmd_logout)

    sp = sub.add_parser("whoami", help="what the hub thinks you are")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("rooms", help="list conversations")
    sp.set_defaults(func=cmd_rooms)

    sp = sub.add_parser(
        "open", help="create a conversation, or return the existing one")
    sp.add_argument("conversation")
    sp.add_argument("--topic", help="what the room is for; kept from the first open")
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("join", help="join a conversation as an agent on this machine")
    sp.add_argument("conversation")
    sp.add_argument("name", help="display name, e.g. the agent's name")
    sp.add_argument("--backend", default="argus", help="which agent runtime this is")
    sp.add_argument("--session", help="this run's id. Without one there is no "
                                      "session to heartbeat or end.")
    sp.add_argument("--capability", action="append",
                    help="something you can do, for discovery; repeatable")
    sp.add_argument("--streaming", action="store_true",
                    help="declare that this runtime can stream")
    sp.set_defaults(func=cmd_join)

    sp = sub.add_parser(
        "inbox", help="what is still waiting for you, across sessions")
    sp.add_argument("conversation")
    sp.add_argument("--all", action="store_true",
                    help="include messages that did not name you")
    sp.add_argument("--limit", type=int)
    sp.set_defaults(func=cmd_inbox)

    sp = sub.add_parser("ack", help="say what happened to a message")
    sp.add_argument("conversation")
    sp.add_argument("seq", type=int)
    sp.add_argument("outcome", choices=["read", "processed", "failed"])
    sp.add_argument("--detail", help="the reason, especially when it failed")
    sp.set_defaults(func=cmd_ack)

    sp = sub.add_parser(
        "heartbeat", help="tell the hub this run is still alive")
    sp.add_argument("--state", choices=["active", "idle", "waiting"],
                    help="waiting means blocked on something outside you")
    sp.add_argument("--session", help="which run, when you have several live")
    sp.set_defaults(func=cmd_heartbeat)

    sp = sub.add_parser(
        "end", help="end this run cleanly; you stay in the conversation")
    sp.add_argument("--reason")
    sp.add_argument("--session")
    sp.set_defaults(func=cmd_end)

    sp = sub.add_parser("agents", help="who is available to work")
    sp.add_argument("--capability", help="only agents declaring this")
    sp.add_argument("--all", action="store_true",
                    help="include suspended and retired agents")
    sp.set_defaults(func=cmd_agents)

    sp = sub.add_parser("sessions", help="every run of an agent, and its state")
    sp.add_argument("which_agent", metavar="agent")
    sp.add_argument("--live", action="store_true")
    sp.set_defaults(func=cmd_sessions)

    sp = sub.add_parser("who", help="who is in a conversation, and on which machine")
    sp.add_argument("conversation")
    sp.set_defaults(func=cmd_who)

    sp = sub.add_parser("send", help="say something (body on the command line or stdin)")
    sp.add_argument("conversation")
    sp.add_argument("body", nargs="?")
    sp.add_argument("--to", action="append",
                    help="participant id to name; repeatable. Only those named are woken.")
    sp.add_argument("--id", help="client message id, so a retry cannot double-post")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("read", help="read messages")
    sp.add_argument("conversation")
    sp.add_argument("--mine", action="store_true", help="only messages that name you")
    sp.add_argument("--after", type=int, help="start after this seq")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--follow", "-f", action="store_true", help="keep polling")
    sp.add_argument("--interval", type=float, default=3.0)
    sp.add_argument("--timeout", type=float, default=0.0,
                    help="with --follow, stop after this many seconds")
    sp.add_argument("--until-message", action="store_true",
                    help="with --follow, stop at the first message that arrives")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("machines", help="which machines the hub has seen")
    sp.set_defaults(func=cmd_machines)

    sp = sub.add_parser("tools", help="what the hub offers over MCP")
    sp.set_defaults(func=cmd_tools)

    sp = sub.add_parser("tasks", help="what work is outstanding, and who holds it")
    sp.add_argument("--all", action="store_true",
                    help="everybody's tasks, not just yours")
    sp.add_argument("--conversation", "-c", help="only tasks raised in this room")
    sp.add_argument("--closed", action="store_true",
                    help="include done and cancelled")
    sp.add_argument("--limit", type=int)
    sp.set_defaults(func=cmd_tasks)

    sp = sub.add_parser(
        "delegate", help="create a task, usually assigned to somebody",
        description="Detail is read from stdin when --detail is not given.")
    sp.add_argument("title")
    sp.add_argument("--to", metavar="AGENT",
                    help="the agent who becomes responsible")
    sp.add_argument("--to-person", metavar="IDENTITY",
                    help="the person who becomes responsible")
    sp.add_argument("--detail", metavar="TEXT",
                    help="what the owner needs to know. `-` reads stdin")
    sp.add_argument("--conversation", "-c",
                    help="where it is discussed, so the owner is told in the room")
    sp.add_argument("--priority", type=int, metavar="N",
                    help="0 to 100, lower is more urgent. 50 by default")
    sp.add_argument("--path", action="append", metavar="PATH",
                    help="a path this task touches; repeatable. Recorded so "
                         "the owner knows where to read the governing "
                         "decisions")
    sp.set_defaults(func=cmd_delegate)

    sp = sub.add_parser(
        "claim", help="take exclusive hold of a task",
        description="With no task, takes the most urgent one assigned to you.")
    sp.add_argument("task", nargs="?", help="a task id or a unique prefix of one")
    sp.add_argument("--conversation", "-c", help="only pull work from this room")
    sp.add_argument("--session", help="which of your runs this is")
    sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("report", help="say what happened to a task you hold")
    sp.add_argument("task")
    sp.add_argument("state", choices=["done", "failed", "blocked", "cancelled"])
    sp.add_argument("--result", help="what you did, or why you could not")
    sp.add_argument("--reason", help="what you are blocked on")
    sp.add_argument("--session", help="which of your runs this is")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("handoff", help="give a task to somebody else")
    sp.add_argument("task")
    sp.add_argument("--to", metavar="AGENT", help="the agent taking it on")
    sp.add_argument("--to-person", metavar="IDENTITY",
                    help="the person taking it on")
    sp.add_argument("--reason", required=True,
                    help="why it is going to them. The hub requires one")
    sp.set_defaults(func=cmd_handoff)

    sp = sub.add_parser(
        "context", help="the decisions governing a task's paths",
        description=("With no path, prints what has been recorded. With paths, "
                     "runs `ctx for` on each and records the result so a later "
                     "run, on any machine, can diff against it."))
    sp.add_argument("task")
    sp.add_argument("path", nargs="*",
                    help="paths to read with ctx and record")
    sp.add_argument("--session", help="which of your runs this is")
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser(
        "drift", help="what changed in the decisions since the baseline",
        description=("Fetches this task's baselines and runs `ctx for -since` "
                     "against your own clone. ctx decides what changed."))
    sp.add_argument("task")
    sp.add_argument("--verbose", "-v", action="store_true",
                    help="also name the scopes that did not move")
    sp.set_defaults(func=cmd_drift)

    sp = sub.add_parser("release", help="release a task hold")
    sp.add_argument("task")
    sp.add_argument("--detail", help="release detail")
    sp.add_argument("--session", help="session identifier")
    sp.set_defaults(func=cmd_release)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Refused as e:
        # The hub understood and declined. Not a crash: print the reason it
        # gave, which is written to be actionable.
        print(f"Refused: {e}", file=sys.stderr)
        return EX_REFUSED
    except Unreachable as e:
        print(f"{e}", file=sys.stderr)
        return EX_UNREACHABLE
    except ProtocolError as e:
        print(f"The hub rejected the request: {e}", file=sys.stderr)
        return EX_PROTOCOL
    except KeyboardInterrupt:
        return 130
    except RuntimeError as e:
        print(f"{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
