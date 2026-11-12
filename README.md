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
| `argus open <room>` | Create a room, or return the existing one — safe to call on every start |
| `argus join <room> <name>` | Join as an agent on this machine |
| `argus who <room>` | Participants, with the machine each is on |
| `argus send <room> <body>` | Say something; `--to <id>` names a participant |
| `argus read <room>` | Read messages; `--mine`, `--follow`, `--after <seq>` |
| `argus heartbeat` | Hold this run's lease; `--state idle\|waiting` says what you are doing |
| `argus end` | End this run cleanly. You stay in the conversation |
| `argus inbox <room>` | What is still waiting for you, across sessions |
| `argus ack <room> <seq>` | Report `read`, `processed` or `failed` |
| `argus agents` | Who is available to work, and reachable |
| `argus sessions <agent>` | Every run of an agent, and why each one ended |
| `argus machines` | Which bare metals the hub has seen |
| `argus tools` | What the hub offers over MCP |
| `argus delegate <title>` | Create a task; `--to <agent>` or `--to-person <identity>` |
| `argus tasks` | What is outstanding and **which run is holding each one** |
| `argus claim [task]` | Take exclusive hold; with no task, the next thing assigned to you |
| `argus report <task> <state>` | `done`, `failed`, `blocked` or `cancelled` |
| `argus handoff <task> --to <agent> --reason <why>` | Move responsibility to somebody else |
| `argus release <task>` | Give up a hold without finishing |
| `argus context <task> [paths…]` | Read a path's decisions with `ctx` and record them, or show what is recorded |
| `argus drift <task>` | What has changed in those decisions since the baseline |
| `argus runtimes` | What each agent runtime can be asked to do |
| `argus cancel <task> --reason <why>` | Ask the run working on a task to stop |

Add `--json` to any command to get the hub's structured payload instead of a
rendering. Agents should use it rather than parsing the human output.

## Sessions, and what happens when one ends

An **agent** is an identity. A **session** is one run of it. They are different
things with different lifetimes, and that is what makes the rest work.

```sh
argus join lab worker --backend omp --session run-1   # start a run
argus heartbeat --state idle                          # hold the lease
argus end --reason "shift over"                       # stop cleanly
```

**Ending a run does not leave the conversation.** You stay a member. Anything
addressed to you while you are gone waits, and your next session reads it:

```sh
argus join lab worker --backend omp --session run-2
argus inbox lab          # what arrived while you were away
argus ack lab 41 processed --detail "migration applied"
```

A session holds a **lease** it has to renew. Silence past the lease marks it
`disconnected`, which is reversible: a machine that was merely partitioned
comes back and carries on. Silence past the grace period marks it `lost` and
releases its claim on work. A machine that loses power never gets to announce
anything, so silence has to be the signal.

A terminal session cannot be revived under its old id. Coming back means a new
`--session`, which leaves the dead one's record intact.

```sh
argus sessions worker
run-1   ended        2026-11-12 01:40:11  shift over
run-2   active       responsive  2026-11-12 02:02:55
```

## Saying what happened to a message

`--to` addresses a message to someone. The recipient owes an outcome on it, and
`argus inbox` is the list of what it still owes. Reporting is explicit:

```sh
argus ack lab 41 processed
argus ack lab 42 failed --detail "no credentials for that cluster"
```

This exists because an agent that crashes between reading a task and finishing
it is otherwise indistinguishable from one that completed it, so the work is
either dropped or done twice.

Delivery is **at least once**. You can be handed the same message twice, so
dedupe on `seq`. Exactly-once is not offered, because it is not achievable
across machines without you deduplicating anyway.

## Tasks, and work that survives the run doing it

A task has an **owner**, who is responsible and stays responsible, and
separately a **claim**, which is the one run doing it right now and dies when
that run dies. Those are different questions and `argus tasks` answers both:

```
6e4140bf  in_progress  worker  Rotate the GHCR pull secret  [held by run-2]
ab129560  assigned     helper  Apply migration 011          (1 handoff)
```

`[held by run-2]` is the important part. A task that is merely `assigned` is
waiting for somebody. A held task is being worked on by one named run, and if
that run has stopped answering the chip says `silent` — which is the only
symptom a dead agent has.

**Exactly one run can hold a task.** A second claim is refused and told who
holds it:

```
$ argus claim ab129560
Refused: task ab129560-… is already being worked on by session run-1.
Nothing was claimed. Ask for the next task instead.
```

**Ending a run does not lose its work.** The claim goes, the ownership stays,
and the command says what went back:

```
$ argus end --reason "shift over"
run-1 ended at 2026-11-12 01:47:51+00:00.
1 unfinished task(s) went back into your queue rather than being lost:
  6e4140bf  assigned    Rotate the GHCR pull secret
```

The same happens without a clean stop, from the hub's side: when a run goes
silent past its grace period it is declared lost and everything it held is
released to its agent's queue. The work is never moved to a *different* agent
automatically, because that would run it twice the moment the original came
back. Moving work is a handoff, and a handoff is something somebody decides.

**A failed task stays yours.** It is not offered again by a bare `argus
claim`, so a task that fails every time cannot spin, but you can retry it by
name or hand it on:

```
$ argus report ab129560 failed --result "psql is not installed on fedora-1"
ab129560 is failed.
It is still assigned to you. Retry it by name, or hand it on with `argus handoff`.

$ argus handoff ab129560 --to helper --reason "helper has psql"
ab129560 is now helper's.
```

A reason is required. The new owner needs to know why it came to them, and so
does whoever reads the history in a month. The handoff is announced in the
task's room and addressed to the new owner, so it lands in their `argus inbox`
whether or not they are running.

A task id can be any unique prefix. An ambiguous one is refused with every
match listed, rather than resolved to the first.

## The decisions a task was started against

In a repository that uses [`ctx`](https://github.com/khaidao2/ctx), the
constraints governing a file live in commit trailers, and `ctx for <path>`
compiles them back out for the lines you are about to edit.

**argus does not re-implement any of that.** `ctx` decides what a decision is
and what changed; argus carries a baseline from the clone that made it to
whoever needs it next. That carrying is the part `ctx` cannot do on its own: a
baseline is a file on one machine, and work here outlives the process that
wrote it.

```sh
# the planner says where the work lands, before anyone has looked
argus delegate "Shorten the session lease" --to worker -c lab \
  --path backend/src/.../argus_session_repository.py

# the agent reads the real decisions and records them
argus context e6f5a264 backend/src/.../argus_session_repository.py
#   backend/src/.../argus_session_repository.py: 2 active at af3cae59dd0d

# later, in any clone, on any machine
argus drift e6f5a264
```

When something moved underneath, `ctx` says so in its own words:

```
changed since your last edit to backend/src/.../argus_session_repository.py:
superseded:
  - sha: ebabf9eeba6a -> by b11b0052cece
    decision: "Measure a session's grace from its last heartbeat, not from the sweep"
    reason: "the lease is being shortened, so grace no longer needs to exceed it"

1 of 1 scope(s) moved under this task. Read the above before reporting it done.
```

Both commands need `ctx` on PATH and a git working tree, and say so plainly
rather than recording nothing. A report is always sent with the commit it was
taken at, because a report means nothing apart from its revision.

## Asking work to stop

Cancellation is a **request, not a command**. Nothing in the hub can halt a
process. The holder is told on the heartbeat it is already sending, and what it
does next is its own.

```sh
argus cancel a1b2c3d4 --reason "the migration is wrong"
# Asked run-1 to stop a1b2c3d4.
#   semantics: graceful. It is told on its next heartbeat, so this is not
#   immediate and may never happen.
```

The holder finds out where it was always going to look:

```sh
argus heartbeat
# run-1 is active, lease until 03:00:00
#   STOP a1b2c3d4 (a long migration) - asked by boss@example.com: it is wrong
#   Stop if you can, then `argus report <task> cancelled`. If you will not,
#   say so: nobody can tell from the hub.
```

**Not every runtime can be stopped.** `argus runtimes` says which can, and
asking one that cannot tells you so immediately rather than leaving you
waiting:

```
omp: omp cannot be interrupted. The request is on the record, but nothing will
reach the run; it keeps the task until it finishes or its session ends, and the
task then returns to its agent's queue.
```

**A request is aimed at one run, not at the task.** If the holder dies before
acting, the request lapses rather than passing to whoever picks the task up
next -- that run was never asked anything.

## Two agents, two machines

Nothing is pushed to you. An agent reads **what names it**, replies to the
sender, and keeps a cursor so it never re-reads what it has seen:

```sh
# on machine A
argus open lab --topic "nightly migration"        # idempotent; safe every start
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
