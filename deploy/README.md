# Keeping a machine's argus identifiable

A box running a hand-copied tarball cannot answer "what are you running".
`git log` can, so a deployment is a checkout that only ever fast-forwards.

## Two shapes, one script

`argus-update` reads `ARGUS_REPO`, defaulting to `/opt/argus`.

| | repo | units | invoked as |
|---|---|---|---|
| a server | `/opt/argus` | `argus-update.{service,timer}` as root | `pag` |
| a workstation | `~/.local/share/argus` | `argus-update.user.{service,timer}` | `argus` |

The user variant needs lingering (`loginctl enable-linger $USER`) or it stops
when you log out.

## The wrapper

`pag` fixes the three things a machine must not get wrong per invocation: which
checkout to import, which key to present, and which hub to talk to. Install it
as `/usr/local/bin/pag` and override `ARGUS_HOME_DIR`, `ARGUS_KEY_FILE` and
`ARGUS_HUB` per machine.

It refuses rather than running keyless, because an argus that cannot
authenticate produces a refusal from the hub that reads like a server problem.

## Install

```sh
# a server
sudo git clone --branch main https://github.com/khaidao2/argus /opt/argus
sudo install -m 755 deploy/argus-update /usr/local/bin/argus-update
sudo install -m 755 deploy/pag /usr/local/bin/pag
sudo install -m 644 deploy/argus-update.service deploy/argus-update.timer \
    /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now argus-update.timer

# a workstation
git clone --branch main https://github.com/khaidao2/argus ~/.local/share/argus
install -m 755 deploy/argus-update ~/.local/bin/argus-update
install -m 644 deploy/argus-update.user.service \
    ~/.config/systemd/user/argus-update.service
install -m 644 deploy/argus-update.user.timer \
    ~/.config/systemd/user/argus-update.timer
systemctl --user daemon-reload && systemctl --user enable --now argus-update.timer
```

## What it refuses to do

**It never resets.** A tree with local commits stops updating and says so,
rather than discarding a hand-patch somebody applied to keep the box alive. It
also warns on every tick when the tree is ahead of origin, because a box
quietly carrying an unpushed commit is the "running code nobody can identify"
state this is meant to end.

**It skips while argus is running.** Python imports modules as it reaches them,
so rewriting files under a long-lived process can hand it a mix of versions.

**It does not update itself.** systemd runs the installed copy, so one bad
commit cannot leave a box unable to fast-forward to the fix. It reports when
the checkout's copy differs from the installed one.

## Do not point this at a development checkout

The repo you push *from* sits on whatever branch the work is on. A timer there
fights feature branches, and `argus` would behave differently depending on what
was being edited. Keep the deployment separate.

## The secret does not live in the checkout

A key inside `/opt/argus` is one stray `git add -A` from being published, and
this repository is public. Keep it in `~/.config/argus/` and have the wrapper
read it from there.

## The tradeoff you are accepting

Push access to this repository reaches every machine running the timer, as
whatever user it runs as, within one interval and with no human in the loop.
