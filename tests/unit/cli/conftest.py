"""CLI test configuration.

CLI commands write user-level config as a side effect (``repowise init``
registers the MCP server and hooks in ``~/.claude/settings.json``, enables
tool search, and touches Claude Desktop / Codex config). A test that drives a
command end-to-end against a pytest tmp_path would therefore leak that temp
path into the developer's real settings, a wedged registration that breaks
every subsequent Claude Code session until repaired. The autouse fixture
below points "home" at a per-test temp directory so no CLI test can ever
touch real user-level config; tests that build their own fake home simply
patch over it.

``XDG_CONFIG_HOME`` is cleared for the same reason, and it is not covered by
redirecting home: it is an *absolute* path that wins over ``~`` outright. The
OpenCode target resolves its user config through it on every platform, so on a
machine that exports one, an unset ``XDG_CONFIG_HOME`` is the difference
between a test writing to a temp directory and a test writing to the
developer's real ``opencode.jsonc``. A test that wants the variable set sets it
itself.

``HERMES_HOME`` and ``LOCALAPPDATA`` are the same hole, one variable further
along. The Hermes target reads ``HERMES_HOME`` first and then, on Windows only,
``%LOCALAPPDATA%\\hermes``, never ``~/.hermes`` there, so redirecting home
covers neither. ``LOCALAPPDATA`` is *redirected* rather than cleared, because
unlike the other two it is a variable Windows itself always sets and other code
legitimately reads; pointing it inside the sandbox keeps it meaningful while
still making it impossible for a test to reach the developer's real
``config.yaml``.

Every entry here is an absolute-path variable that outranks ``~``. Any new
target that resolves its config through one belongs in this list, and the check
is not "does redirecting home cover it", because it does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home
