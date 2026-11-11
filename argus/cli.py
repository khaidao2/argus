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
import sys
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


def cmd_join(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_join", {
        "conversation": args.conversation,
        "display_name": args.name,
        "agent_backend": args.backend,
        "agent_session": args.session,
    })
    _emit(args, payload, lambda p: print(
        f"Joined {args.conversation} as {p['label']} "
        f"(participant {p['participant_id']}). Machine {p['machine']} came from the key."))
    return 0


def cmd_who(args) -> int:
    s = _session_or_exit(args)
    payload = Client(s.url, s.key).call("chat_who", {"conversation": args.conversation})
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
    payload = Client(s.url, s.key).call("chat_send", arguments)
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
        payload = c.call("chat_read", arguments)
        _emit(args, payload, lambda p: [print(_fmt_message(m)) for m in p["messages"]]
              or print("(nothing new)"))
        return 0

    # --follow polls, because the hub pushes nothing. The cursor the server
    # returns is what stops a poll from re-reading what it has already seen.
    after = args.after or 0
    deadline = time.monotonic() + args.timeout if args.timeout else None
    while True:
        arguments["after_seq"] = after
        payload = c.call("chat_read", arguments)
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="argus",
        description="Talk to the Mission Control agent chat from a terminal.")
    p.add_argument("--version", action="version", version=f"argus {__version__}")
    p.add_argument("--json", action="store_true",
                   help="print the hub's structured payload instead of a rendering")
    p.add_argument("--url", help=f"hub base URL (default: the logged-in one, else {DEFAULT_URL})")
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
    sp.add_argument("--session", help="the agent's own session id, to tell two apart")
    sp.set_defaults(func=cmd_join)

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
