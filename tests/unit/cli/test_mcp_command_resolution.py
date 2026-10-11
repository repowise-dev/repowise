"""PATH-hijack fix: per-user MCP registrations pin the running install.

Bare ``"command": "repowise"`` entries are resolved via PATH at session
start, so a shadow install (conda, old pip, pipx, uv tool) silently
hijacks the MCP server. Per-user config files now store the absolute path
of the install that ran ``init``. Repo-shared files (``.mcp.json``,
``.codex/config.toml``) intentionally keep the bare name — they may be
committed, and one contributor's absolute path would break every other
checkout.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from repowise.cli import mcp_config
from repowise.cli.editor_integrations import claude_config

_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _fake_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point sys.executable at a fake venv with a repowise script beside it.

    pytest's tmp_path usually lives under the OS temp dir, which the
    transient-location check rejects — so the fake temp dir is moved away
    to keep the fake install eligible.
    """
    monkeypatch.setattr(
        "tempfile.gettempdir", lambda: str(tmp_path / "elsewhere-tmp")
    )
    bin_dir = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / f"python{_SUFFIX}"
    fake_python.write_text("", encoding="utf-8")
    script = bin_dir / f"repowise{_SUFFIX}"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    return script


# ---------------------------------------------------------------------------
# resolve_repowise_command
# ---------------------------------------------------------------------------


def test_resolves_script_next_to_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_install(tmp_path, monkeypatch)

    resolved = mcp_config.resolve_repowise_command()

    assert resolved == str(script.resolve()).replace("\\", "/")


def test_falls_back_to_bare_name_when_script_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_install(tmp_path, monkeypatch)
    script.unlink()

    assert mcp_config.resolve_repowise_command() == "repowise"


def test_falls_back_to_bare_name_under_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_install(tmp_path, monkeypatch)
    # Re-point the temp dir back over the fake install: now it's transient.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    assert mcp_config.resolve_repowise_command() == "repowise"


def test_falls_back_to_bare_name_in_uv_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tempfile.gettempdir", lambda: str(tmp_path / "elsewhere-tmp")
    )
    bin_dir = (
        tmp_path
        / "uv"
        / "cache"
        / "archive-v0"
        / "xyz"
        / ("Scripts" if sys.platform == "win32" else "bin")
    )
    bin_dir.mkdir(parents=True)
    (bin_dir / f"repowise{_SUFFIX}").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(bin_dir / f"python{_SUFFIX}"))

    assert mcp_config.resolve_repowise_command() == "repowise"


# ---------------------------------------------------------------------------
# Per-user configs get the absolute command
# ---------------------------------------------------------------------------


def test_register_with_claude_code_pins_absolute_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda: "/opt/venv/bin/repowise"
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    settings_path = claude_config.register_with_claude_code(repo)
    assert settings_path is not None

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["repowise"]["command"] == "/opt/venv/bin/repowise"


def test_register_with_claude_desktop_pins_absolute_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda: "/opt/venv/bin/repowise"
    )
    desktop_parent = tmp_path / "home" / "Library" / "Application Support" / "Claude"
    desktop_parent.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    config_path = claude_config.register_with_claude_desktop(repo)
    assert config_path is not None

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["repowise"]["command"] == "/opt/venv/bin/repowise"


def test_reregistration_refreshes_stale_absolute_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moved venv self-heals: the next registration overwrites the path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda: "/old/venv/bin/repowise"
    )
    claude_config.register_with_claude_code(repo)
    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda: "/new/venv/bin/repowise"
    )
    settings_path = claude_config.register_with_claude_code(repo)
    assert settings_path is not None

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["repowise"]["command"] == "/new/venv/bin/repowise"


# ---------------------------------------------------------------------------
# Repo-shared configs keep the bare name
# ---------------------------------------------------------------------------


def test_root_mcp_json_keeps_bare_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.mcp.json`` may be committed — never bake in a machine-local path."""
    _fake_install(tmp_path, monkeypatch)  # absolute path IS resolvable...

    config_path = mcp_config.save_root_mcp_config(tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["repowise"]["command"] == "repowise"  # ...but unused


def test_codex_config_keeps_bare_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_install(tmp_path, monkeypatch)

    config_path = mcp_config.save_codex_mcp_config(tmp_path)

    saved = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcp_servers"]["repowise"]["command"] == "repowise"
