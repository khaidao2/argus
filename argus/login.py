"""`argus login`: a browser login that works over SSH.

WHY THE DEVICE FLOW. The usual CLI login opens a browser at a loopback
redirect. On a lab box reached over SSH there is no browser to open and no
loopback port the person's browser could reach. The device authorization grant
inverts it: argus prints a URL and a short code, the person logs in on whatever
device has a browser, and argus polls until the token appears.

WHY ARGUS NEVER SEES A PASSWORD. It is typed into Keycloak's own page. argus
holds no client secret either -- it is a public client, which the device flow
permits, because a secret shipped inside a CLI that every machine runs is not
a secret.

WHAT IT KEEPS. Not the Keycloak token: that is traded immediately for a
Mission Control session key, scoped to one machine and revocable on its own.
The access token is short-lived and is dropped as soon as the trade is done.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

from .config import DEFAULT_URL, Session, save

UA = "argus"


def _get_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_form(url: str, data: dict[str, str], timeout: float = 20.0):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": "non_json_response", "error_description": raw[:300]}


def _post_json(url: str, payload: dict[str, Any], bearer: str, timeout: float = 20.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Authorization": f"Bearer {bearer}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:300]}


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def login(
    url: str = DEFAULT_URL,
    machine: str | None = None,
    label: str | None = None,
    open_browser: bool = True,
    say: Callable[[str], None] = print,
) -> Session:
    url = url.rstrip("/")

    # The server publishes where to log in, so argus hard-codes no realm,
    # client id or endpoint. Move the realm and the CLI keeps working.
    cfg = _get_json(f"{url}/api/cli/login-config")
    verifier, challenge = _pkce()

    form = {"client_id": cfg["client_id"], "scope": "openid"}
    if cfg.get("pkce_required"):
        # Not optional for this client: Keycloak answers "Missing parameter:
        # code_challenge_method" at this first step without it.
        form["code_challenge"] = challenge
        form["code_challenge_method"] = cfg.get("pkce_method", "S256")

    status, dev = _post_form(cfg["device_authorization_endpoint"], form)
    if status != 200:
        raise RuntimeError(
            f"the identity provider refused to start a device login: "
            f"{dev.get('error')}: {dev.get('error_description')}")

    verify_url = dev.get("verification_uri_complete") or dev["verification_uri"]
    say("")
    say(f"  Open:  {verify_url}")
    say(f"  Code:  {dev['user_code']}")
    say("")
    say(f"  Waiting for the login to finish (the code expires in {dev['expires_in']}s).")
    if open_browser and not os.environ.get("SSH_CONNECTION"):
        # Only when a browser could plausibly exist. Over SSH this would
        # either fail silently or open a browser on the wrong machine.
        try:
            webbrowser.open(verify_url)
        except Exception:
            pass

    token = _poll_for_token(cfg, dev, verifier, say)

    machine = machine or _default_machine()
    status, issued = _post_json(
        f"{url}/api/workspaces/{cfg['workspace_id']}/me/session-keys",
        {"machine": machine, "label": label or f"argus on {machine}"},
        bearer=token["access_token"])
    if status not in (200, 201):
        raise RuntimeError(
            f"logged in, but the hub would not issue a session key "
            f"(HTTP {status}): {issued.get('detail')}")

    session = Session(
        url=url, key=issued["token"], machine=issued["machine"],
        identity=_identity_of(token["access_token"]),
        workspace=cfg["workspace_id"])
    path = save(session)
    say("")
    say(f"  Logged in as {session.identity} on machine {session.machine!r}.")
    say(f"  Session key {session.prefix}... saved to {path} (mode 0600).")
    say("")
    say("  The machine name is baked into the key. Everything you say in the")
    say("  chat is attributed to that machine, and no argument can change it.")
    return session


def _poll_for_token(cfg, dev, verifier, say) -> dict[str, Any]:
    interval = int(dev.get("interval") or 5)
    deadline = time.monotonic() + int(dev.get("expires_in") or 600)
    form = {
        "client_id": cfg["client_id"],
        "grant_type": cfg.get("grant_type",
                              "urn:ietf:params:oauth:grant-type:device_code"),
        "device_code": dev["device_code"],
        "code_verifier": verifier,
    }
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, body = _post_form(cfg["token_endpoint"], form)
        if status == 200:
            return body
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            # The spec says add five seconds, not retry harder.
            interval += 5
            continue
        if err == "expired_token":
            raise RuntimeError("the login code expired; run `argus login` again")
        if err == "access_denied":
            raise RuntimeError("the login was declined")
        raise RuntimeError(f"login failed: {err}: {body.get('error_description')}")
    raise RuntimeError("the login code expired; run `argus login` again")


def _identity_of(access_token: str) -> str:
    """Read the username out of the token for display only.

    Not a security decision: the hub verifies this token itself against the
    provider's keys. argus only wants something friendly to print.
    """
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)))
        return claims.get("preferred_username") or claims.get("sub", "?")
    except Exception:
        return "?"


def _default_machine() -> str:
    """A name for this box, overridable, because the hostname is often wrong.

    Both bare metals in the lab this was built for answer `hostname` with
    "fedora". A default that cannot tell two machines apart is worse than no
    default, so ARGUS_MACHINE overrides it and `--machine` overrides that.
    """
    env = os.environ.get("ARGUS_MACHINE")
    if env:
        return env.strip()
    for path in ("/etc/argus-machine",):
        try:
            with open(path) as fh:
                name = fh.read().strip()
                if name:
                    return name
        except OSError:
            pass
    import socket
    return socket.gethostname()
