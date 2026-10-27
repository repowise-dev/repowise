"""init asks before repointing the global ``repowise`` MCP entry.

Every test runs against a fake home: the real ``~/.claude/settings.json`` and
Claude Desktop config are never read or written.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from rich.console import Console

from repowise.cli.editor_integrations import claude_config, global_mcp

_REAL_HOME = Path.home()


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=300, color_system=None), buf


def _arg(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _entry(target: str, command: str = "repowise") -> dict:
    return {"command": command, "args": ["mcp", target, "--transport", "stdio"]}


def _write(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"repowise": entry}}), encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home holding both Claude configs' locations."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("APPDATA", str(fake / "AppData" / "Roaming"))
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "repowise")
    # tests/conftest.py turns editor setup off; this module is about it being on.
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    assert Path.home() != _REAL_HOME
    return fake


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _desktop(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = home / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: path)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _no_prompt(*_a: Any, **_k: Any) -> bool:
    raise AssertionError("must not prompt")


def test_read_reports_not_set_on_first_run(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    target = global_mcp.read_global_mcp_target(repo)

    assert target.repoints
    assert target.new_target == _arg(repo)
    [line] = target.summary_lines()
    assert "now points at not set" in line
    assert line.endswith(f"will point here: {_arg(repo)}")


def test_read_lists_desktop_only_when_installed(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        claude_config, "_claude_desktop_config_path", lambda: home / "nope" / "c.json"
    )
    clients = [e.client for e in global_mcp.read_global_mcp_target(repo).entries]
    assert clients == ["Claude Code"]


def test_read_names_each_client_when_they_differ(home: Path, repo: Path, monkeypatch) -> None:
    desktop = _desktop(home, monkeypatch)
    _write(_settings(home), _entry("C:/a"))
    _write(desktop, _entry("C:/b"))

    lines = global_mcp.read_global_mcp_target(repo).summary_lines()
    assert lines[0].endswith(f"will point here: {_arg(repo)}")
    assert "Claude Code now points at C:/a" in lines[1]
    assert "Claude Desktop now points at C:/b" in lines[2]


def test_read_truncated_entry_reads_not_set(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    _write(_settings(home), {"command": "repowise", "args": ["mcp"]})
    [line] = global_mcp.read_global_mcp_target(repo).summary_lines()
    assert "None" not in line and "not set" in line


def test_same_target_is_quiet_and_never_asks(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    _write(_settings(home), _entry(_arg(repo)))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is True
    assert buf.getvalue() == ""


def test_command_only_change_is_one_quiet_line(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    _write(_settings(home), _entry(_arg(repo), command="C:/old/repowise.exe"))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is True
    out = buf.getvalue().strip()
    assert len(out.splitlines()) == 1 and "command moves to repowise" in out


@pytest.mark.parametrize("yes", [True, False])
def test_yes_or_no_terminal_registers_and_prints_old_and_new(
    home: Path, repo: Path, monkeypatch, yes: bool
) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    _write(_settings(home), _entry("C:/elsewhere"))
    monkeypatch.setattr("sys.stdin", _Tty() if yes else io.StringIO())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo, yes=yes) is True
    out = buf.getvalue()
    assert "now points at C:/elsewhere" in out and _arg(repo) in out


@pytest.mark.parametrize("answer", [True, False])
def test_interactive_asks_with_default_yes(
    home: Path, repo: Path, monkeypatch, answer: bool
) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr("sys.stdin", _Tty())
    asked: list[bool] = []

    def confirm(_text: str, default: bool) -> bool:
        asked.append(default)
        return answer

    monkeypatch.setattr(click, "confirm", confirm)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is answer
    assert asked == [True]
    assert ("left as is" in buf.getvalue()) is (not answer)


def test_unanswerable_prompt_declines(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr("sys.stdin", _Tty())

    def abort(*_a: Any, **_k: Any) -> bool:
        raise click.Abort()

    monkeypatch.setattr(click, "confirm", abort)
    console, _ = _console()
    assert global_mcp.confirm_global_mcp(console, repo) is False


def test_no_editor_setup_asks_and_prints_nothing(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    assert global_mcp.confirm_global_mcp(console, repo, no_editor_setup=True) is False
    assert buf.getvalue() == ""


def _run(repo: Path, *, yes: bool, consent: bool | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        repo_path=repo,
        opts=SimpleNamespace(yes=yes, editor_setup=True),
        global_mcp_consent=consent,
    )


def test_decline_leaves_global_configs_byte_identical(
    home: Path, repo: Path, monkeypatch
) -> None:
    from repowise.cli.commands.init_cmd import _finish

    desktop = _desktop(home, monkeypatch)
    _write(_settings(home), _entry("C:/elsewhere"))
    _write(desktop, _entry("C:/elsewhere"))
    before = (_settings(home).read_bytes(), desktop.read_bytes())
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)
    monkeypatch.setattr(_finish, "console", _console()[0])

    _finish.register_clients(_run(repo, yes=False))

    assert (_settings(home).read_bytes(), desktop.read_bytes()) == before


def test_yes_repoints_both_global_configs(home: Path, repo: Path, monkeypatch) -> None:
    from repowise.cli.commands.init_cmd import _finish

    desktop = _desktop(home, monkeypatch)
    _write(_settings(home), _entry("C:/elsewhere"))
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    monkeypatch.setattr(_finish, "console", console)

    _finish.register_clients(_run(repo, yes=True))

    for path in (_settings(home), desktop):
        stored = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["repowise"]
        assert stored["args"][1] == _arg(repo)
    assert "now points at" in buf.getvalue()


def test_probe_failure_never_blocks_registration(home: Path, repo: Path, monkeypatch) -> None:
    def broken(_repo: Path) -> None:
        raise OSError("unreadable")

    monkeypatch.setattr(global_mcp, "read_global_mcp_target", broken)
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    assert global_mcp.confirm_global_mcp(console, repo) is True
    assert buf.getvalue() == ""


def test_plan_screen_this_repo_only_writes_nothing_and_asks_nothing(
    home: Path, repo: Path, monkeypatch
) -> None:
    from repowise.cli.commands.init_cmd import _finish

    _write(_settings(home), _entry("C:/elsewhere"))
    before = _settings(home).read_bytes()
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    monkeypatch.setattr(_finish, "console", console)

    _finish.register_clients(_run(repo, yes=False, consent=False))

    assert _settings(home).read_bytes() == before
    assert "this repo only" in buf.getvalue()


def test_plan_screen_yes_repoints_without_asking_again(
    home: Path, repo: Path, monkeypatch
) -> None:
    from repowise.cli.commands.init_cmd import _finish

    _write(_settings(home), _entry("C:/elsewhere"))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    monkeypatch.setattr(_finish, "console", console)

    _finish.register_clients(_run(repo, yes=False, consent=True))

    stored = json.loads(_settings(home).read_text(encoding="utf-8"))["mcpServers"]["repowise"]
    assert stored["args"][1] == _arg(repo)
    assert "now points at" in buf.getvalue()
