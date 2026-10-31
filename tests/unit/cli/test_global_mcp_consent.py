"""init adds the per-user ``repowise`` MCP entries but never moves one serving another repo.

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
_LAUNCH_DIR_ARGS = ["mcp", "--transport", "stdio"]


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


def _stored(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["repowise"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home holding both Claude configs' locations."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("APPDATA", str(fake / "AppData" / "Roaming"))
    monkeypatch.setenv("REPOWISE_WORKSPACE_CEILING", str(tmp_path))
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


def _no_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def other(tmp_path: Path) -> Path:
    """Another repo on the machine that still exists."""
    path = tmp_path / "other"
    path.mkdir()
    return path


def _no_prompt(*_a: Any, **_k: Any) -> bool:
    raise AssertionError("must not prompt")


def _run(repo: Path, *, yes: bool, consent: bool | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        repo_path=repo,
        opts=SimpleNamespace(yes=yes, editor_setup=True),
        global_mcp_consent=consent,
    )


def _register(repo: Path, monkeypatch, *, yes: bool = True, consent: bool | None = None) -> str:
    from repowise.cli.commands.init_cmd import _finish

    console, buf = _console()
    monkeypatch.setattr(_finish, "console", console)
    _finish.register_clients(_run(repo, yes=yes, consent=consent))
    return buf.getvalue()


# --- the four machines -------------------------------------------------------


def test_fresh_machine_writes_launch_dir_code_entry_and_desktop_path(
    home: Path, repo: Path, monkeypatch
) -> None:
    desktop = _desktop(home, monkeypatch)
    monkeypatch.setattr(click, "confirm", _no_prompt)

    out = _register(repo, monkeypatch)

    assert _stored(_settings(home))["args"] == _LAUNCH_DIR_ARGS
    assert _stored(desktop)["args"][1] == _arg(repo)
    assert "Claude Code repowise MCP entry now points at not set" in out
    assert f"Claude Desktop MCP registered for {repo}" in out


def test_entry_for_another_live_repo_is_never_moved(
    home: Path, repo: Path, other: Path, monkeypatch
) -> None:
    desktop = _desktop(home, monkeypatch)
    _write(_settings(home), _entry(_arg(other)))
    _write(desktop, _entry(_arg(other)))
    before = desktop.read_bytes()
    monkeypatch.setattr(click, "confirm", _no_prompt)

    out = _register(repo, monkeypatch)

    # Hooks are repo-agnostic and still land in settings.json; the entry does not move.
    assert _stored(_settings(home)) == _entry(_arg(other))
    assert desktop.read_bytes() == before
    assert f"global repowise entry serves {_arg(other)}; this repo's .mcp.json" in out
    assert f"Claude Desktop's repowise entry serves {_arg(other)} and is left as is" in out
    assert "rewrites both Claude entries" in out
    assert global_mcp.REGISTER_LATER_COMMAND in out
    assert "now points at" not in out


def test_entry_whose_repo_is_gone_is_healed(home: Path, repo: Path, tmp_path, monkeypatch) -> None:
    desktop = _desktop(home, monkeypatch)
    gone = _arg(tmp_path / "moved-away")
    _write(_settings(home), _entry(gone))
    _write(desktop, _entry(gone))
    monkeypatch.setattr(click, "confirm", _no_prompt)

    out = _register(repo, monkeypatch)

    assert _stored(_settings(home))["args"] == _LAUNCH_DIR_ARGS
    assert _stored(desktop)["args"][1] == _arg(repo)
    assert f"now points at {gone} (missing)" in out


def test_workspace_registers_root_and_sibling_init_keeps_it(
    home: Path, tmp_path: Path, monkeypatch
) -> None:
    desktop = _desktop(home, monkeypatch)
    root = tmp_path / "ws"
    first, second = root / "api", root / "web"
    first.mkdir(parents=True)
    second.mkdir()
    (root / ".repowise-workspace.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.setattr(click, "confirm", _no_prompt)

    _register(first, monkeypatch)
    after_first = (_stored(_settings(home)), desktop.read_bytes())
    out = _register(second, monkeypatch)

    assert _stored(desktop)["args"][1] == _arg(root)
    assert _stored(_settings(home))["args"] == _LAUNCH_DIR_ARGS
    assert (_stored(_settings(home)), desktop.read_bytes()) == after_first
    assert "now points at" not in out


# --- the rule in detail ------------------------------------------------------


def test_same_target_is_quiet_and_never_asks(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), _entry(_arg(repo)))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is True
    assert buf.getvalue() == ""


def test_same_target_refreshes_only_the_command(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    args = ["mcp", _arg(repo), "--transport", "stdio", "--tools", "lean"]
    _write(_settings(home), {"command": "C:/old/repowise.exe", "args": args})

    assert claude_config.register_with_claude_code(repo) == _settings(home)
    assert _stored(_settings(home)) == {"command": "repowise", "args": args}


def test_command_only_change_is_one_quiet_line(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), _entry(_arg(repo), command="C:/old/repowise.exe"))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is True
    out = buf.getvalue().strip()
    assert len(out.splitlines()) == 1 and "command moves to repowise" in out


def test_launch_dir_entry_only_gets_its_command_refreshed(
    home: Path, repo: Path, monkeypatch
) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), {"command": "C:/old/repowise.exe", "args": ["mcp", "--all"]})

    assert claude_config.register_with_claude_code(repo) == _settings(home)
    assert _stored(_settings(home)) == {"command": "repowise", "args": ["mcp", "--all"]}


def test_hand_shaped_entry_is_kept(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), {"command": "uvx", "args": ["repowise", "mcp"]})
    before = _settings(home).read_bytes()

    assert claude_config.register_with_claude_code(repo) is None
    assert _settings(home).read_bytes() == before


def test_missing_pinned_command_is_refreshed_without_moving(
    home: Path, repo: Path, other: Path, monkeypatch
) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), _entry(_arg(other), command=str(other / "gone" / "repowise.exe")))

    assert claude_config.register_with_claude_code(repo) == _settings(home)
    stored = _stored(_settings(home))
    assert stored["command"] == "repowise" and stored["args"][1] == _arg(other)


def test_user_env_block_survives_a_heal(home: Path, repo: Path, tmp_path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), {**_entry(_arg(tmp_path / "gone")), "env": {"A": "1"}})

    claude_config.register_with_claude_code(repo)

    assert _stored(_settings(home))["env"] == {"A": "1"}


# --- explicit re-targeting ---------------------------------------------------


def test_agents_add_moves_an_entry_and_refresh_does_not(
    home: Path, repo: Path, other: Path, monkeypatch
) -> None:
    from repowise.cli.agent_targets.targets.claude_code import TARGET
    from repowise.cli.agent_targets.types import InstallOptions, Scope

    desktop = _desktop(home, monkeypatch)
    for path in (_settings(home), desktop):
        _write(path, _entry(_arg(other)))

    TARGET.install(Scope.USER, InstallOptions(), repo_path=repo)
    assert _stored(desktop)["args"][1] == _arg(other)
    assert _stored(_settings(home))["args"][1] == _arg(other)

    TARGET.install(Scope.USER, InstallOptions(retarget=True), repo_path=repo)
    assert _stored(desktop)["args"][1] == _arg(repo)
    assert _stored(_settings(home))["args"] == _LAUNCH_DIR_ARGS


# --- consent -----------------------------------------------------------------


@pytest.mark.parametrize("answer", [True, False])
def test_interactive_asks_before_adding_with_default_yes(
    home: Path, repo: Path, monkeypatch, answer: bool
) -> None:
    _no_desktop(monkeypatch)
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


def test_interactive_never_asks_about_an_entry_it_keeps(
    home: Path, repo: Path, other: Path, monkeypatch
) -> None:
    _no_desktop(monkeypatch)
    _write(_settings(home), _entry(_arg(other)))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, _ = _console()

    assert global_mcp.confirm_global_mcp(console, repo) is True


def test_unanswerable_prompt_declines(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    monkeypatch.setattr("sys.stdin", _Tty())

    def abort(*_a: Any, **_k: Any) -> bool:
        raise click.Abort()

    monkeypatch.setattr(click, "confirm", abort)
    console, _ = _console()
    assert global_mcp.confirm_global_mcp(console, repo) is False


def test_decline_leaves_global_configs_byte_identical(
    home: Path, repo: Path, tmp_path, monkeypatch
) -> None:
    desktop = _desktop(home, monkeypatch)
    gone = _arg(tmp_path / "gone")
    _write(_settings(home), _entry(gone))
    _write(desktop, _entry(gone))
    before = (_settings(home).read_bytes(), desktop.read_bytes())
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)

    _register(repo, monkeypatch, yes=False)

    assert (_settings(home).read_bytes(), desktop.read_bytes()) == before


def test_no_editor_setup_asks_and_prints_nothing(home: Path, repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)
    console, buf = _console()
    assert global_mcp.confirm_global_mcp(console, repo, no_editor_setup=True) is False
    assert buf.getvalue() == ""


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
    _no_desktop(monkeypatch)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)

    out = _register(repo, monkeypatch, yes=False, consent=False)

    assert not _settings(home).exists()
    assert "this repo only" in out


def test_plan_screen_yes_writes_without_asking_again(home: Path, repo: Path, monkeypatch) -> None:
    _no_desktop(monkeypatch)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(click, "confirm", _no_prompt)

    out = _register(repo, monkeypatch, yes=False, consent=True)

    assert _stored(_settings(home))["args"] == _LAUNCH_DIR_ARGS
    assert "now points at not set" in out


def test_plan_screen_lists_only_entries_that_change(
    home: Path, repo: Path, other: Path, monkeypatch
) -> None:
    from repowise.cli.commands.init_cmd._plan import outside_lines

    desktop = _desktop(home, monkeypatch)
    _write(_settings(home), _entry(_arg(other)))
    assert outside_lines(repo) == (
        "Claude Desktop repowise MCP entry now points at not set, "
        f"will serve {_arg(repo)}",
    )
    _write(desktop, _entry(_arg(other)))
    assert outside_lines(repo) == ()


# --- doctor ------------------------------------------------------------------


def test_doctor_flags_a_desktop_entry_whose_repo_is_gone(
    home: Path, tmp_path: Path, other: Path, monkeypatch
) -> None:
    from repowise.cli.commands.doctor_cmd._checks.integrations import _claude_desktop_entry_row

    desktop = _desktop(home, monkeypatch)
    assert _claude_desktop_entry_row() is None

    _write(desktop, _entry(_arg(other)))
    row = _claude_desktop_entry_row()
    assert row is not None and row.level != "warn" and _arg(other) in row.detail

    gone = _arg(tmp_path / "gone")
    _write(desktop, _entry(gone))
    row = _claude_desktop_entry_row()
    assert row is not None and row.level == "warn"
    assert row.fix == global_mcp.REGISTER_LATER_COMMAND and gone in row.detail
