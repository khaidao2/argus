"""Where the key is kept, and that it is kept no wider than it has to be."""
import json
import os
import stat

from argus.config import Session, clear, config_dir, load, save


def _session(**kw):
    base = dict(url="https://hub.example", key="mck_abcdefgh12345678",
                machine="fedora-1", identity="testuser2", workspace="ws-1")
    base.update(kw)
    return Session(**base)


def test_the_key_file_is_not_readable_by_anyone_else(argus_home):
    path = save(_session())
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config_dir()).st_mode) == 0o700, (
        "a 0600 file inside a world-listable directory still leaks its name"
    )


def test_the_metadata_file_holds_no_secret(argus_home):
    save(_session())
    meta = json.loads((config_dir() / "session.json").read_text())
    assert "mck_abcdefgh12345678" not in json.dumps(meta)
    # The prefix is safe and is what the hub shows in its key list, so a person
    # can match a running agent to a key without handling the secret.
    assert meta["prefix"] == "mck_abcdefgh"
    assert meta["machine"] == "fedora-1"


def test_a_saved_session_comes_back(argus_home):
    save(_session())
    loaded = load()
    assert loaded.key == "mck_abcdefgh12345678"
    assert loaded.machine == "fedora-1"
    assert loaded.url == "https://hub.example"


def test_nothing_saved_means_nothing_loaded(argus_home):
    assert load() is None


def test_the_environment_overrides_the_file(argus_home, monkeypatch):
    """So a CI job or a test never has to write a key to disk."""
    save(_session())
    monkeypatch.setenv("ARGUS_KEY", "mck_from_env")
    monkeypatch.setenv("ARGUS_URL", "http://hub.local:8099/")
    loaded = load()
    assert loaded.key == "mck_from_env"
    assert loaded.url == "http://hub.local:8099", "a trailing slash would double the //mcp"


def test_logout_removes_both_files(argus_home):
    save(_session())
    clear()
    assert load() is None
    assert not (config_dir() / "session.json").exists()


def test_saving_twice_does_not_leave_the_old_key_behind(argus_home):
    save(_session())
    save(_session(key="mck_second_key_value"))
    assert load().key == "mck_second_key_value"
