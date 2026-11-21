"""A burst of commits coalesces into one update (#5353).

The guard is a directory in ``.repowise/``, taken inside the backgrounded
subshell so its ``trap`` releases it when the update exits. Three properties
are worth pinning, and the third is the one nobody notices going wrong:

* a second commit arriving while an update runs does not start a second one;
* the guard is released when the update finishes, so the next commit runs;
* a guard left behind by a killed run does not silence the hook forever —
  a post-commit hook that does nothing looks exactly like one that works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from repowise.cli import hooks

_GUARD = ".repowise/.update.lock.d"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / ".repowise").mkdir()
    (path / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def fake_repowise(tmp_path: Path) -> Path:
    """An install that records each run, then sleeps for ``FAKE_SLEEP`` seconds."""
    script = tmp_path / "install" / "bin" / "repowise"
    script.parent.mkdir(parents=True)
    script.write_text(
        # Records the HEAD it was run against, so a test can tell which commits
        # a run actually covered. Each of the first FAKE_COMMITS runs lands one
        # commit mid-run, the burst a coalescing guard must not drop (#5518).
        "#!/bin/sh\n"
        'echo "$(git rev-parse HEAD)" >> "$PWD/.repowise/ran"\n'
        'made=$(wc -l < "$PWD/.repowise/committed" 2>/dev/null || echo 0)\n'
        'if [ "$made" -lt "${FAKE_COMMITS:-0}" ]; then\n'
        '  echo x >> "$PWD/.repowise/committed"\n'
        "  git -c user.email=t@t -c user.name=t commit -q --allow-empty -m mid-run\n"
        "fi\n"
        'sleep "${FAKE_SLEEP:-0}"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    return script


def _install_hook(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Pin to the bare name so the generated hook resolves `repowise` on PATH,
    # where the fixture put the recording install.
    monkeypatch.setattr(hooks, "_pinned_repowise", lambda: "repowise")
    hooks.install(repo)
    return repo / ".git" / "hooks" / "post-commit"


def _env(bin_dir: Path, *, sleep_s: int = 0) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_SLEEP": str(sleep_s),
    }


def _fire(hook: Path, repo: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["sh", str(hook)],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ran(repo: Path) -> list[str]:
    path = repo / ".repowise" / "ran"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _wait_until(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_burst_while_an_update_runs_starts_only_one(repo, fake_repowise, monkeypatch):
    hook = _install_hook(repo, monkeypatch)
    env = _env(fake_repowise.parent, sleep_s=3)

    first = _fire(hook, repo, env)
    first.wait()
    # The hook backgrounds the update and exits, so the guard is held by the
    # update now. Fire a second and third commit into that window.
    assert _wait_until(lambda: (repo / _GUARD).is_dir(), timeout=10)
    second = _fire(hook, repo, env)
    third = _fire(hook, repo, env)
    second.wait()
    third.wait()

    assert _wait_until(lambda: not (repo / _GUARD).exists(), timeout=20), "guard was never released"
    assert len(_ran(repo)) == 1, f"three commits started {len(_ran(repo))} updates"


def test_the_guard_is_released_so_the_next_commit_runs(repo, fake_repowise, monkeypatch):
    hook = _install_hook(repo, monkeypatch)
    env = _env(fake_repowise.parent, sleep_s=0)

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: len(_ran(repo)) == 1, timeout=15)
    assert _wait_until(lambda: not (repo / _GUARD).exists(), timeout=10)

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: len(_ran(repo)) == 2, timeout=15), "the guard was never released"


def test_a_stale_guard_does_not_silence_the_hook(repo, fake_repowise, monkeypatch):
    hook = _install_hook(repo, monkeypatch)
    env = _env(fake_repowise.parent, sleep_s=0)

    guard = repo / _GUARD
    guard.mkdir(parents=True)
    # A run killed mid-flight leaves the directory behind. Age it past the
    # escape window rather than waiting two hours.
    old = time.time() - 3 * 3600
    os.utime(guard, (old, old))

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: len(_ran(repo)) == 1, timeout=15), (
        "the hook did nothing with a stale guard present — a killed run would "
        "silence every later commit"
    )


def test_a_fresh_guard_is_respected(repo, fake_repowise, monkeypatch):
    """The escape must be age-based, not "always clear" — otherwise it is a no-op."""
    hook = _install_hook(repo, monkeypatch)
    env = _env(fake_repowise.parent, sleep_s=0)

    (repo / _GUARD).mkdir(parents=True)
    _fire(hook, repo, env).wait()
    time.sleep(1.0)
    assert _ran(repo) == [], "a fresh guard was ignored, so the hook never coalesces"


def test_a_commit_that_lands_mid_run_is_covered_before_release(repo, fake_repowise, monkeypatch):
    """The guard must not drop the commit it was coalescing behind (#5518).

    The running update read HEAD before this commit existed, so a guard that
    simply exits on contention leaves it unindexed — and because the run also
    clears .update.queued, the stale warning says nothing either.
    """
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "first",
        ],
        cwd=repo,
        check=True,
    )
    # Install after that commit: an installed hook fires on it too, and with the
    # real `repowise` still on PATH it would take the guard before the test ran.
    hook = _install_hook(repo, monkeypatch)
    env = {**_env(fake_repowise.parent, sleep_s=0), "FAKE_COMMITS": "1"}

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: len(_ran(repo)) >= 2, timeout=20), (
        f"the mid-run commit was never indexed: {len(_ran(repo))} update run(s)"
    )
    heads = _ran(repo)
    assert heads[0] != heads[-1], "the second run did not see the commit that landed mid-run"


def _commit(repo: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
    )


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_a_commit_during_every_pass_ends_indexed(repo, fake_repowise, monkeypatch):
    """No cap on passes: the commit that lands during the last pass is still indexed."""
    _commit(repo, "first")
    hook = _install_hook(repo, monkeypatch)
    # The sleep keeps each mid-run commit's own hook from finding the guard free.
    env = {**_env(fake_repowise.parent, sleep_s=1), "FAKE_COMMITS": "4"}

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: len(_ran(repo)) >= 5, timeout=30), (
        f"the burst stopped after {len(_ran(repo))} run(s)"
    )
    assert _wait_until(lambda: not (repo / _GUARD).exists(), timeout=15)
    assert _ran(repo)[-1] == _head(repo), "the last commit of the burst was never indexed"


def test_a_commit_after_the_last_head_check_is_indexed(repo, fake_repowise, tmp_path, monkeypatch):
    """A commit whose hook finds the guard held after the holder's last HEAD check.

    A `git` shim makes that interleaving deterministic: on the holder's first
    HEAD check after an update ran, it reads HEAD, lands a commit whose own hook
    finds the guard held, waits for that hook, then reports the HEAD it read.
    """
    real_git = Path(shutil.which("git") or "git").as_posix()
    shim = tmp_path / "shim" / "git"
    shim.parent.mkdir()
    shim.write_text(
        "#!/bin/sh\n"
        f"REAL='{real_git}'\n"
        'if [ "$*" = "rev-parse HEAD" ] && [ -f .repowise/ran ] && [ ! -f .repowise/shimmed ]; then\n'
        "  : > .repowise/shimmed\n"
        '  out=$("$REAL" rev-parse HEAD)\n'
        '  "$REAL" -c user.email=t@t -c user.name=t commit -q --allow-empty -m late\n'
        "  sleep 1\n"
        '  echo "$out"\n'
        "  exit 0\n"
        "fi\n"
        'exec "$REAL" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)
    _commit(repo, "first")
    hook = _install_hook(repo, monkeypatch)
    env = _env(fake_repowise.parent, sleep_s=0)
    env["PATH"] = f"{shim.parent}{os.pathsep}{env['PATH']}"

    _fire(hook, repo, env).wait()
    assert _wait_until(lambda: (repo / ".repowise" / "shimmed").exists(), timeout=15)
    # The guard is briefly absent while the holder releases and retakes it, so
    # wait on the run log, not the guard.
    assert _wait_until(lambda: bool(_ran(repo)) and _ran(repo)[-1] == _head(repo), timeout=15), (
        f"the late commit was never indexed: {len(_ran(repo))} run(s)"
    )
    assert _wait_until(lambda: not (repo / _GUARD).exists(), timeout=15)
