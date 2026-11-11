"""Where argus keeps its state, and why it keeps so little of it.

One file holds the session key, one holds the server it belongs to. The key is
written 0600 and never logged; the only thing printed anywhere is its prefix,
which is also what the server shows in its key list, so a person can match the
two without the secret leaving the file.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "https://mission-control.sentpul.click"


def config_dir() -> Path:
    """Honour XDG, because a machine with several logins should not share keys."""
    base = os.environ.get("ARGUS_HOME")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "argus"


@dataclass
class Session:
    url: str
    key: str
    machine: str
    identity: str
    workspace: str

    @property
    def prefix(self) -> str:
        return self.key[:12]


def _key_path() -> Path:
    return config_dir() / "session.key"


def _meta_path() -> Path:
    return config_dir() / "session.json"


def save(session: Session) -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, stat.S_IRWXU)  # 0700: nobody else on the box reads this
    kp = _key_path()
    # Create with the right mode from the start. Writing first and chmod-ing
    # after leaves a window where the key is world-readable.
    fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(session.key)
    _meta_path().write_text(json.dumps({
        "url": session.url, "machine": session.machine,
        "identity": session.identity, "workspace": session.workspace,
        "prefix": session.prefix,
    }, indent=2) + "\n")
    return kp


def load() -> Session | None:
    """The environment wins, so a CI job or a test never has to write a file."""
    env_key = os.environ.get("ARGUS_KEY")
    if env_key:
        return Session(
            url=os.environ.get("ARGUS_URL", DEFAULT_URL).rstrip("/"),
            key=env_key.strip(), machine="(from key)",
            identity="(from key)", workspace=os.environ.get("ARGUS_WORKSPACE", ""),
        )
    kp, mp = _key_path(), _meta_path()
    if not kp.exists():
        return None
    key = kp.read_text().strip()
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    return Session(
        url=os.environ.get("ARGUS_URL", meta.get("url", DEFAULT_URL)).rstrip("/"),
        key=key, machine=meta.get("machine", "?"),
        identity=meta.get("identity", "?"), workspace=meta.get("workspace", ""),
    )


def clear() -> None:
    for p in (_key_path(), _meta_path()):
        if p.exists():
            p.unlink()
