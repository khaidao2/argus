"""The updater script, exercised against real git repositories.

WHY THIS IS TESTED AT ALL. `deploy/argus-update` is three guards and a
fast-forward, and every one of the guards is the kind of line that looks
removable to somebody tidying up. The bytecode purge especially: it reads like
housekeeping and is the difference between an update taking effect and a
machine reporting a fresh commit while running the old code.

Real git repositories rather than mocks, because what is being tested is how
the script behaves against git, and a mock of git would only assert that the
script calls the commands it calls.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "argus-update"


def git(cwd, *args):
    return subprocess.run(
        ("git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args),
        cwd=cwd, capture_output=True, text=True, check=True)


def run_update(repo):
    return subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(repo),
             "ARGUS_REPO": str(repo)})


@pytest.fixture
def deployment(tmp_path):
    """An origin with one commit, and a clone of it: a deployment."""
    origin = tmp_path / "origin"
    (origin / "pkg").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(origin))
    (origin / "pkg" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "first")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--branch", "main", str(origin), str(clone))
    return origin, clone


def advance(origin, version="0.2.0"):
    """A change upstream of exactly the same byte length as what it replaces."""
    (origin / "pkg" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    git(origin, "commit", "-qam", f"release {version}")


def test_it_fast_forwards(deployment):
    origin, clone = deployment
    advance(origin)
    result = run_update(clone)
    assert result.returncode == 0, result.stderr
    assert "updated" in result.stdout
    assert '0.2.0' in (clone / "pkg" / "__init__.py").read_text()


def test_it_says_when_there_is_nothing_to_do(deployment):
    _, clone = deployment
    result = run_update(clone)
    assert result.returncode == 0
    assert "already at" in result.stdout


def test_it_drops_stale_bytecode(deployment):
    """The line that looks like housekeeping and is not.

    Python reuses a .pyc when the source's mtime-in-seconds AND byte size both
    match what the .pyc recorded. A fast-forward writes the source with the
    current second, and a version bump of equal length leaves the size
    unchanged, so without this the updated tree keeps executing the old code.
    """
    origin, clone = deployment
    cache = clone / "pkg" / "__pycache__"
    subprocess.run(["python3", "-c", "import pkg"], cwd=clone, check=True,
                   capture_output=True, env={"PATH": "/usr/bin:/bin",
                                             "PYTHONPATH": str(clone)})
    assert cache.exists(), "the fixture did not produce bytecode to go stale"

    advance(origin)
    run_update(clone)

    assert not cache.exists(), (
        "stale bytecode survived the update, so the tree moved and the running "
        "code did not")


def test_a_diverged_tree_is_refused_rather_than_reset(deployment):
    """A reset would discard a hand-patch somebody applied to keep a box
    alive. Refusing loudly is the lesser failure."""
    origin, clone = deployment
    (clone / "pkg" / "__init__.py").write_text('__version__ = "local-fix"\n')
    git(clone, "commit", "-qam", "local hotfix")
    advance(origin)

    result = run_update(clone)

    assert result.returncode == 1
    assert "diverged" in result.stderr
    assert "local-fix" in (clone / "pkg" / "__init__.py").read_text(), (
        "the local change was destroyed")


def test_being_ahead_of_origin_is_reported(deployment):
    """A fast-forward of a branch already ahead succeeds as a no-op, so
    without this a box carrying an unpushed commit reports itself in step."""
    _, clone = deployment
    (clone / "extra.txt").write_text("x\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "unpushed")

    result = run_update(clone)

    assert "ahead of origin/main" in result.stderr
    assert "unpushed" in result.stderr


def test_an_unreachable_origin_keeps_the_working_copy(deployment, tmp_path):
    """No network is not an emergency: the box keeps what it has."""
    origin, clone = deployment
    origin.rename(tmp_path / "origin-gone")

    result = run_update(clone)

    assert result.returncode == 1
    assert "fetch failed" in result.stderr
    assert (clone / "pkg" / "__init__.py").exists()
