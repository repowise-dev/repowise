"""Doctor's MCP smoke check: does the registered server actually start?

The field data this exists for is a crash loop - ``ModuleNotFoundError`` from a
partial install, ``PermissionError`` on a locked ``.repowise/`` - where the
host restarts a server that dies instantly. Every install in that state passes
the registration check, which only reads a config file. These tests drive real
subprocesses, because the failure being caught is a process-level one that a
mocked launcher would not reproduce.

The autouse ``_isolated_home`` conftest fixture points ``Path.home()`` at a
temp dir, so these tests write their own settings.json freely.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import mcp_smoke
from repowise.cli.commands.doctor_cmd.mcp_smoke import mcp_smoke_check
from repowise.cli.commands.doctor_cmd.repo_checks import (
    _claude_registration_check,
    _mcp_responds_check,
)

# A server that answers ``initialize`` the way a real one does.
_GOOD_SERVER = (
    "import sys, json;"
    "line = sys.stdin.readline();"
    "req = json.loads(line);"
    "sys.stdout.write(json.dumps({"
    "'jsonrpc': '2.0', 'id': req['id'],"
    "'result': {'protocolVersion': '2024-11-05', 'capabilities': {},"
    "'serverInfo': {'name': 'repowise', 'version': '0.47.0'}}}) + chr(10));"
    "sys.stdout.flush()"
)

# The crash loop: dies on import, before it can read anything.
_DEAD_SERVER = (
    "import sys;"
    "sys.stderr.write('ModuleNotFoundError: No module named ' + chr(39) + 'repowise' + chr(39));"
    "sys.exit(1)"
)

# Comes up, never answers.
_MUTE_SERVER = "import time; time.sleep(30)"


@pytest.fixture(autouse=True)
def _clear_smoke_cache():
    """``_mcp_responds_check`` is memoised for the process; each test writes its
    own settings.json and must not read the previous test's launch."""
    _mcp_responds_check.cache_clear()
    yield
    _mcp_responds_check.cache_clear()


def test_a_responding_server_passes() -> None:
    check = mcp_smoke_check(sys.executable, ["-c", _GOOD_SERVER])
    assert check.ok is True
    assert "repowise" in check.detail
    assert "initialised" in check.detail


def test_a_server_that_dies_on_import_fails_and_says_why() -> None:
    """The whole point: this install passes every other doctor check."""
    check = mcp_smoke_check(sys.executable, ["-c", _DEAD_SERVER])
    assert check.ok is False
    assert "exited with code 1" in check.detail
    # The server's own stderr is what names the fault; without it the user is
    # told only that something failed.
    assert "ModuleNotFoundError" in check.detail


def test_a_missing_command_fails() -> None:
    check = mcp_smoke_check("repowise-does-not-exist", ["mcp"])
    assert check.ok is False
    assert "could not launch" in check.detail


def test_a_mute_server_fails_without_hanging_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that never answers must be bounded, not waited on forever."""
    monkeypatch.setattr(mcp_smoke, "_SMOKE_TIMEOUT_S", 1.0)
    check = mcp_smoke_check(sys.executable, ["-c", _MUTE_SERVER])
    assert check.ok is False
    assert "still running" in check.detail


def test_an_error_response_fails() -> None:
    server = (
        "import sys, json;"
        "req = json.loads(sys.stdin.readline());"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        "'error': {'code': -32603, 'message': 'index is unreadable'}}) + chr(10));"
        "sys.stdout.flush()"
    )
    check = mcp_smoke_check(sys.executable, ["-c", server])
    assert check.ok is False
    assert "index is unreadable" in check.detail


def test_log_lines_on_stdout_do_not_break_the_handshake() -> None:
    """A server that logs to stdout is misbehaving but not broken; a real
    client skips what it cannot parse, and so must this."""
    server = (
        "import sys, json;"
        "sys.stdout.write('loading vector store...' + chr(10));"
        "req = json.loads(sys.stdin.readline());"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        "'result': {'serverInfo': {'name': 'repowise'}}}) + chr(10));"
        "sys.stdout.flush()"
    )
    check = mcp_smoke_check(sys.executable, ["-c", server])
    assert check.ok is True


def test_no_registration_is_informational() -> None:
    """Nothing registered means nothing to launch - not a failure. A user on
    the HTTP transport or another client has nothing here to be broken."""
    check = _mcp_responds_check()
    assert check.ok is True
    assert "not registered" in check.detail


def test_a_hand_shaped_registration_is_not_launched() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"mcpServers": {"repowise": {"url": "http://localhost:8000"}}}),
        encoding="utf-8",
    )
    check = _mcp_responds_check()
    assert check.ok is True
    assert "hand-shaped" in check.detail


def test_the_registered_command_is_the_one_launched() -> None:
    """A smoke check that launched something other than the registration would
    give a clean bill of health to the broken thing."""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {"mcpServers": {"repowise": {"command": sys.executable, "args": ["-c", _DEAD_SERVER]}}}
        ),
        encoding="utf-8",
    )
    check = _mcp_responds_check()
    assert check.ok is False
    assert "ModuleNotFoundError" in check.detail

def test_the_server_is_launched_once_per_doctor_run() -> None:
    """Workspace mode runs the repo checks once per entry, but there is one
    global registration - so a ten-repo workspace must not launch ten servers.
    """
    marker = Path.home() / "launch-count"
    server = (
        "import sys, json, pathlib;"
        f"p = pathlib.Path(r'{marker}');"
        "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1');"
        "req = json.loads(sys.stdin.readline());"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        "'result': {'serverInfo': {'name': 'repowise'}}}) + chr(10));"
        "sys.stdout.flush()"
    )
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"mcpServers": {"repowise": {"command": sys.executable, "args": ["-c", server]}}}),
        encoding="utf-8",
    )

    for _ in range(3):
        assert _mcp_responds_check().ok is True
    assert marker.read_text() == "1"


def test_a_chatty_server_is_not_reported_as_broken() -> None:
    """A healthy server that logs heavily before answering must still pass.

    The child's stderr is a pipe with a ~4KB buffer on Windows. Leaving it
    undrained until after the handshake blocks the server inside write() and
    reports it as dead - a false failure driven purely by log volume, on a
    server that works. repowise routes every log sink to stderr on the stdio
    path and does its noisiest work before it answers, so this is the normal
    case, not an edge one.
    """
    server = (
        "import sys, json;"
        "sys.stderr.write('x' * 40000);"
        "sys.stderr.flush();"
        "req = json.loads(sys.stdin.readline());"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        "'result': {'serverInfo': {'name': 'repowise'}}}) + chr(10));"
        "sys.stdout.flush()"
    )
    check = mcp_smoke_check(sys.executable, ["-c", server])
    assert check.ok is True, check.detail


def test_a_chatty_server_that_dies_still_reports_its_last_words() -> None:
    """Draining into a bounded ring must keep the END of stderr - the tail is
    where the traceback names the fault, and the head is startup noise."""
    server = (
        "import sys;"
        "sys.stderr.write('noise' + chr(10));"
        "sys.stderr.write(('filler' + chr(10)) * 5000);"
        "sys.stderr.write('PermissionError: .repowise/wiki.db' + chr(10));"
        "sys.exit(1)"
    )
    check = mcp_smoke_check(sys.executable, ["-c", server])
    assert check.ok is False
    assert "PermissionError" in check.detail


def test_an_unparseable_settings_file_says_so() -> None:
    """A hand-broken settings.json is not the same as no registration:
    telling that user to run init sends them at the same unreadable file."""
    settings_path = Path.home() / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('{ not json', encoding='utf-8')
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False
    assert 'could not parse' in check.detail
