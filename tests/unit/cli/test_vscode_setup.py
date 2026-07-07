from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from repowise.cli import mcp_config
from repowise.cli.editor_integrations.defaults import get_default_editor_integrations
from repowise.cli.editor_integrations.vscode import VSCodeSetup
from repowise.cli.editor_setup import EditorSetupOptions


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def _repowise_server_args(repo_path: Path) -> list[str]:
    return mcp_config.generate_mcp_config(repo_path)["mcpServers"]["repowise"]["args"]


# ---------------------------------------------------------------------------
# .vscode/mcp.json writer
# ---------------------------------------------------------------------------


def test_save_vscode_mcp_config_creates_missing_file(tmp_path: Path) -> None:
    config_path = mcp_config.save_vscode_mcp_config(tmp_path)

    assert config_path == tmp_path / ".vscode" / "mcp.json"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    server = saved["servers"]["repowise"]
    assert server["type"] == "stdio"
    assert server["command"] == "repowise"
    # Path arg mirrors the repo-shared .mcp.json convention exactly.
    assert server["args"] == _repowise_server_args(tmp_path)


def test_save_vscode_mcp_config_ends_with_newline_and_two_space_indent(tmp_path: Path) -> None:
    config_path = mcp_config.save_vscode_mcp_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")

    assert text.endswith("\n")
    assert '\n  "servers"' in text


def test_save_vscode_mcp_config_merges_and_preserves_foreign_servers(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "servers": {"other": {"type": "stdio", "command": "other"}},
                "inputs": [{"id": "token"}],
            }
        ),
        encoding="utf-8",
    )

    mcp_config.save_vscode_mcp_config(tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["servers"]["other"] == {"type": "stdio", "command": "other"}
    assert "repowise" in saved["servers"]
    assert saved["inputs"] == [{"id": "token"}]


def test_save_vscode_mcp_config_preserves_user_env_block(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "repowise": {
                        "type": "stdio",
                        "command": "old-command",
                        "args": ["stale"],
                        "env": {"OPENAI_API_KEY": "sk-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    mcp_config.save_vscode_mcp_config(tmp_path)

    repowise = json.loads(config_path.read_text(encoding="utf-8"))["servers"]["repowise"]
    assert repowise["env"] == {"OPENAI_API_KEY": "sk-secret"}
    assert repowise["command"] == "repowise"


def test_save_vscode_mcp_config_is_idempotent(tmp_path: Path) -> None:
    mcp_config.save_vscode_mcp_config(tmp_path)
    first = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    mcp_config.save_vscode_mcp_config(tmp_path)
    second = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")

    assert first == second


def test_save_vscode_mcp_config_rejects_jsonc_without_writing(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    original = '{\n  // workspace MCP servers\n  "servers": {}\n}\n'
    config_path.write_text(original, encoding="utf-8")

    try:
        mcp_config.save_vscode_mcp_config(tmp_path)
        raise AssertionError("expected ValueError on JSONC content")
    except ValueError:
        pass

    assert config_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# .vscode/extensions.json writer
# ---------------------------------------------------------------------------


def test_save_vscode_extensions_config_creates_missing_file(tmp_path: Path) -> None:
    config_path = mcp_config.save_vscode_extensions_config(tmp_path)

    assert config_path == tmp_path / ".vscode" / "extensions.json"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["recommendations"] == ["repowise-dev.repowise"]


def test_save_vscode_extensions_config_appends_and_dedupes(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "extensions.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "recommendations": ["ms-python.python"],
                "unwantedRecommendations": ["some.ext"],
            }
        ),
        encoding="utf-8",
    )

    mcp_config.save_vscode_extensions_config(tmp_path)
    # Second run must not duplicate the entry.
    mcp_config.save_vscode_extensions_config(tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["recommendations"] == ["ms-python.python", "repowise-dev.repowise"]
    assert saved["unwantedRecommendations"] == ["some.ext"]


def test_save_vscode_extensions_config_rejects_jsonc_without_writing(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "extensions.json"
    config_path.parent.mkdir(parents=True)
    original = '{\n  // recommended extensions\n  "recommendations": []\n}\n'
    config_path.write_text(original, encoding="utf-8")

    try:
        mcp_config.save_vscode_extensions_config(tmp_path)
        raise AssertionError("expected ValueError on JSONC content")
    except ValueError:
        pass

    assert config_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# VSCodeSetup integration
# ---------------------------------------------------------------------------


def test_vscode_setup_writes_both_project_files(tmp_path: Path) -> None:
    VSCodeSetup().write_project_files(_silent_console(), tmp_path, EditorSetupOptions())

    assert (tmp_path / ".vscode" / "mcp.json").exists()
    assert (tmp_path / ".vscode" / "extensions.json").exists()


def test_vscode_setup_skips_when_project_file_disabled(tmp_path: Path) -> None:
    VSCodeSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"vscode_mcp"})),
    )

    assert not (tmp_path / ".vscode").exists()


def test_vscode_setup_refresh_rewrites_idempotently(tmp_path: Path) -> None:
    console = _silent_console()
    VSCodeSetup().write_project_files(console, tmp_path, EditorSetupOptions())
    mcp_first = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    ext_first = (tmp_path / ".vscode" / "extensions.json").read_text(encoding="utf-8")

    VSCodeSetup().refresh_project_files(console, tmp_path, EditorSetupOptions())

    assert (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8") == mcp_first
    assert (tmp_path / ".vscode" / "extensions.json").read_text(encoding="utf-8") == ext_first


def test_vscode_setup_refresh_skips_when_disabled(tmp_path: Path) -> None:
    VSCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"vscode_mcp"})),
    )

    assert not (tmp_path / ".vscode").exists()


def test_vscode_setup_warns_and_preserves_jsonc_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    original = '{\n  // comment\n  "servers": {}\n}\n'
    config_path.write_text(original, encoding="utf-8")

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    VSCodeSetup().write_project_files(console, tmp_path, EditorSetupOptions())

    # mcp.json is JSONC: left untouched, warning emitted.
    assert config_path.read_text(encoding="utf-8") == original
    assert "left unchanged" in output.getvalue()
    # extensions.json is fresh and still written.
    assert (tmp_path / ".vscode" / "extensions.json").exists()


def test_vscode_setup_register_client_is_noop(tmp_path: Path) -> None:
    assert VSCodeSetup().register_client(_silent_console(), tmp_path) is None


def test_vscode_included_in_default_integrations() -> None:
    integrations = get_default_editor_integrations()
    assert any(isinstance(i, VSCodeSetup) for i in integrations)


def test_vscode_configure_options_disables_when_declined(monkeypatch) -> None:
    monkeypatch.setattr("click.confirm", lambda *a, **k: False)
    options = VSCodeSetup().configure_options(
        _silent_console(),
        EditorSetupOptions(prompt_for_project_files=True),
    )
    assert "vscode_mcp" in options.disabled_project_files


def test_vscode_configure_options_keeps_enabled_when_accepted(monkeypatch) -> None:
    monkeypatch.setattr("click.confirm", lambda *a, **k: True)
    options = VSCodeSetup().configure_options(
        _silent_console(),
        EditorSetupOptions(prompt_for_project_files=True),
    )
    assert "vscode_mcp" not in options.disabled_project_files


def test_vscode_configure_options_no_prompt_when_not_requested(monkeypatch) -> None:
    def _fail(*_a, **_k):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("click.confirm", _fail)
    options = VSCodeSetup().configure_options(_silent_console(), EditorSetupOptions())
    assert "vscode_mcp" not in options.disabled_project_files
