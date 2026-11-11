# argus

A terminal client for the Mission Control agent chat. It lets an agent — or a
person — on one machine talk to an agent on another machine.

```
$ argus login
  Open:  https://auth.example.com/realms/lab/device?user_code=WDJB-MJHT
  Code:  WDJB-MJHT

  Logged in as testuser1 on machine 'fedora'.
  Session key mck_a1b2c3d4... saved to ~/.config/argus/session.key (mode 0600).

$ argus join lab my-agent --backend omp --session w1
Joined lab as my-agent@fedora (w1). Machine fedora came from the key.

$ argus send lab "Which kernel are you on?" --to a6bcbc59-… --id ask-1
Sent as #47. Named: a6bcbc59-0000-4000-8000-000000000000

$ argus read lab --mine
* [48] 17:27:18 responder@fedora-1 (agent) -> kernel 6.19.10-300.fc44.x86_64
```

## Install

```sh
pip install git+https://github.com/khaidao2/argus
```

Python 3.9+ and no dependencies: argus gets installed on whatever box an agent
happens to run on, and an install that drags in a web stack is a reason not to
install it.

## Logging in

`argus login` uses the OAuth 2.0 **device authorization grant**. The usual CLI
login opens a browser at a loopback redirect, which is no good on a box reached
over SSH: there is no browser to open and no loopback port your browser could
reach. The device flow inverts it — argus prints a URL and a short code, you log
in on whatever device has a browser, and argus polls until the token arrives.

Consequences worth knowing:

- **argus never sees your password.** You type it into your identity provider's
  own page.
- **argus holds no client secret.** It is a public client, which the device flow
  allows. A secret shipped inside a CLI that every machine runs is not a secret.
- **The Keycloak token is not what gets saved.** It is traded immediately for a
  Mission Control *session key*, scoped to one machine and revocable on its own,
  and then dropped.
- argus hard-codes no realm, client id or endpoint. It asks the server
  (`GET /api/cli/login-config`), so moving the realm does not break the CLI.

## The machine name is not yours to choose

Every session key is issued **for one machine**. Everything you say is
attributed to that machine, and no flag or tool argument can change it. There is
deliberately no `--machine` option on `join`.

This is not pedantry. Both bare metals in the lab argus was built for answer
`hostname` with `fedora`. A self-reported machine name would have been wrong on
one of them and unverifiable on both, which is worse than useless when the point
of the label is to tell you which box an agent is running on.

At login time you do pick the name the key is issued under:

```sh
argus login --machine fedora-1
```

The default comes from `$ARGUS_MACHINE`, then `/etc/argus-machine`, then the
hostname — in that order, because the hostname is often the least informative of
the three. `argus whoami` asks the hub, not the local file, so it tells you what
everyone else sees.

## Commands

| Command | What it does |
| --- | --- |
| `argus login` | Browser login, then save a machine-scoped session key |
| `argus logout` | Delete the local key (it stays valid at the hub until revoked there) |
| `argus whoami` | Who the hub says you are, and which machines it has seen |
| `argus rooms` | List conversations |
| `argus join <room> <name>` | Join as an agent on this machine |
| `argus who <room>` | Participants, with the machine each is on |
| `argus send <room> <body>` | Say something; `--to <id>` names a participant |
| `argus read <room>` | Read messages; `--mine`, `--follow`, `--after <seq>` |
| `argus machines` | Which bare metals the hub has seen |
| `argus tools` | What the hub offers over MCP |

Add `--json` to any command to get the hub's structured payload instead of a
rendering. Agents should use it rather than parsing the human output.

## Two agents, two machines

Nothing is pushed to you. An agent reads **what names it**, replies to the
sender, and keeps a cursor so it never re-reads what it has seen:

```sh
# on machine A
A_ID=$(argus --json join lab planner --backend omp | jq -r .participant_id)
argus send lab "Take the migration?" --to "$B_ID" --id task-1

# on machine B, blocking until something is addressed to it
argus --json read lab --mine --follow --until-message --timeout 240
argus send lab "Taking it." --to "$A_ID" --id reply-1
```

Two things keep this from looping forever. `--mine` means an agent only sees
messages that name it, so two agents in one room do not answer each other's
every utterance. And `--id` is a client message id: a retried send cannot
double-post.

## Exit codes

Scripts and agents branch on these rather than on message text.

| Code | Meaning |
| --- | --- |
| 0 | fine |
| 2 | bad usage |
| 3 | the hub understood and **refused** — read the reason, do not retry |
| 4 | the hub rejected the request as malformed — a bug |
| 5 | the hub was unreachable |
| 6 | not logged in |

3 and 4 are kept apart on purpose. A refusal ("you have not joined") is an
answer to act on; a protocol error is a defect. An agent that retries the first
or reports the second is behaving wrongly.

## Where argus keeps things

```
~/.config/argus/session.key    the key, mode 0600, in a 0700 directory
~/.config/argus/session.json   url, machine, identity, key prefix — no secret
```

`$ARGUS_HOME` or `$XDG_CONFIG_HOME` move it. `$ARGUS_KEY` and `$ARGUS_URL`
override both, so CI never has to write a file. Only the key's **prefix** is
ever printed, and that is also what the hub shows in its key list — so you can
match a running agent to a key without the secret going anywhere.

## The server

argus talks [MCP](https://modelcontextprotocol.io) (`2025-06-18`) to a single
`/mcp` endpoint. The server lives in the Mission Control repository; `argus
tools` lists what it offers.
