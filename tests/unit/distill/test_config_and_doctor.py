"""Unit tests for distill config validation, doctor checks, and the
install/uninstall <-> init opt-out interplay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import _distill_checks
from repowise.cli.commands.hook_cmd import rewrite_install, rewrite_uninstall
from repowise.cli.editor_integrations import claude_config
from repowise.core.distill.config import (
    omission_store_settings,
    validate_distill_config,
)
from repowise.core.distill.store import DEFAULT_MAX_MB, DEFAULT_TTL_DAYS, OmissionStore

# ---------------------------------------------------------------------------
# validate_distill_config
# ---------------------------------------------------------------------------


def test_validate_absent_block_is_ok() -> None:
    assert validate_distill_config(None) == []


def test_validate_full_valid_block() -> None:
    cfg = {
        "enabled": True,
        "commands": {
            "enabled": True,
            "permission": "ask",
            "families": {"test_output": "allow", "git_diff": "deny"},
            "disabled_filters": ["logs"],
        },
        "omission_store": {"ttl_days": 14, "max_mb": 100},
    }
    assert validate_distill_config(cfg) == []


def test_validate_non_mapping_block() -> None:
    assert validate_distill_config("yes") == ["distill: must be a mapping"]


def test_validate_bad_permission() -> None:
    problems = validate_distill_config({"commands": {"permission": "always"}})
    assert any("permission" in p and "'always'" in p for p in problems)


def test_validate_unknown_family_and_bad_value() -> None:
    problems = validate_distill_config(
        {"commands": {"families": {"nope": "ask", "git_status": "maybe"}}}
    )
    assert any("families.nope: unknown filter" in p for p in problems)
    assert any("families.git_status" in p and "'maybe'" in p for p in problems)


def test_validate_unknown_disabled_filter() -> None:
    problems = validate_distill_config({"commands": {"disabled_filters": ["bogus"]}})
    assert any("disabled_filters: unknown filter 'bogus'" in p for p in problems)


def test_validate_unknown_keys_flagged() -> None:
    problems = validate_distill_config({"read_nudges": True, "commands": {"permision": "ask"}})
    assert any("distill.read_nudges: unknown key" in p for p in problems)
    assert any("commands.permision: unknown key" in p for p in problems)


def test_validate_store_values() -> None:
    problems = validate_distill_config(
        {"omission_store": {"ttl_days": -1, "max_mb": "big", "size": 1}}
    )
    assert any("ttl_days: must be a positive number" in p for p in problems)
    assert any("max_mb: must be a positive number" in p for p in problems)
    assert any("omission_store.size: unknown key" in p for p in problems)


def test_validate_enabled_must_be_bool() -> None:
    problems = validate_distill_config({"enabled": "yes"})
    assert problems == ["distill.enabled: must be true or false"]


# ---------------------------------------------------------------------------
# omission_store_settings
# ---------------------------------------------------------------------------


def test_store_settings_defaults() -> None:
    assert omission_store_settings(None) == (DEFAULT_TTL_DAYS, DEFAULT_MAX_MB)
    assert omission_store_settings({}) == (DEFAULT_TTL_DAYS, DEFAULT_MAX_MB)


def test_store_settings_reads_values() -> None:
    cfg = {"omission_store": {"ttl_days": 3, "max_mb": 10.5}}
    assert omission_store_settings(cfg) == (3.0, 10.5)


def test_store_settings_invalid_values_fall_back() -> None:
    cfg = {"omission_store": {"ttl_days": True, "max_mb": -5}}
    assert omission_store_settings(cfg) == (DEFAULT_TTL_DAYS, DEFAULT_MAX_MB)


def test_distill_cmd_loads_store_settings(tmp_path: Path) -> None:
    from repowise.cli.commands.distill_cmd import _load_distill_config

    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "config.yaml").write_text(
        "distill:\n  omission_store:\n    ttl_days: 2\n    max_mb: 5\n",
        encoding="utf-8",
    )
    enabled, disabled, (ttl, cap) = _load_distill_config(tmp_path)
    assert enabled is True
    assert disabled == ()
    assert (ttl, cap) == (2.0, 5.0)


# ---------------------------------------------------------------------------
# doctor distill checks
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


@pytest.fixture
def no_real_hook(monkeypatch):
    """Keep doctor away from the developer's real ~/.claude/settings.json."""
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "rewrite_hook_installed", lambda self: False)


def _rows(repo: Path) -> dict[str, tuple[str, str]]:
    return {name: (status, detail) for name, status, detail in _distill_checks(repo)}


def test_doctor_defaults_all_ok(repo: Path, no_real_hook) -> None:
    rows = _rows(repo)
    assert "OK" in rows["Distill config"][0]
    assert rows["Distill config"][1] == "defaults (no block)"
    assert "OK" in rows["Omission store"][0]
    assert rows["Omission store"][1] == "not created yet"
    assert "OK" in rows["Distill rewrite hook"][0]
    assert "not installed" in rows["Distill rewrite hook"][1]


def test_doctor_flags_invalid_config(repo: Path, no_real_hook) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    permission: always\n", encoding="utf-8"
    )
    rows = _rows(repo)
    assert "FAIL" in rows["Distill config"][0]
    assert "permission" in rows["Distill config"][1]


def test_doctor_store_within_cap(repo: Path, no_real_hook) -> None:
    store = OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")
    store.put("some content", source="cli:logs", original_tokens=10, kept_tokens=2)
    store.close()
    rows = _rows(repo)
    assert "OK" in rows["Omission store"][0]
    assert "cap" in rows["Omission store"][1]


def test_doctor_store_over_cap_fails(repo: Path, no_real_hook) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  omission_store:\n    max_mb: 0.0001\n", encoding="utf-8"
    )
    store = OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")
    # One row is never evicted by design, so a single oversized row can pin
    # the store over a tiny cap — exactly what the doctor check reports.
    store.put("x" * 200_000, source="cli:logs", original_tokens=10, kept_tokens=2)
    store.close()
    rows = _rows(repo)
    assert "FAIL" in rows["Omission store"][0]
    assert "over cap" in rows["Omission store"][1]


def test_doctor_hook_installed_with_repo_opt_out(repo: Path, monkeypatch) -> None:
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "rewrite_hook_installed", lambda self: True)
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    enabled: false\n", encoding="utf-8"
    )
    rows = _rows(repo)
    assert rows["Distill rewrite hook"][1] == "installed (this repo opted out)"


# ---------------------------------------------------------------------------
# hook rewrite install/uninstall <-> init opt-out interplay
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    return path


def test_install_after_init_opt_out_re_enables_repo(
    tmp_path: Path, settings_path: Path, monkeypatch
) -> None:
    from repowise.core.repo_config import load_repo_config

    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    # Simulate a prior `repowise init --no-distill-hook` opt-out.
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    enabled: false\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(rewrite_install, [])
    assert result.exit_code == 0
    assert "installed" in result.output
    # Hook entry written...
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["hooks"]["PreToolUse"]
    # ...and the repo's opt-out lifted (a manual install must not be inert).
    cfg = load_repo_config(repo)
    assert cfg["distill"]["commands"]["enabled"] is True


def test_uninstall_removes_hook_but_leaves_repo_config(
    tmp_path: Path, settings_path: Path, monkeypatch
) -> None:
    from repowise.core.repo_config import load_repo_config

    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    monkeypatch.chdir(repo)

    CliRunner().invoke(rewrite_install, [])
    result = CliRunner().invoke(rewrite_uninstall, [])
    assert result.exit_code == 0
    assert "removed" in result.output
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert not data.get("hooks", {}).get("PreToolUse")
    # Uninstall is global (settings.json); per-repo config stays as-is so a
    # later reinstall does not need to re-discover repo preferences.
    cfg = load_repo_config(repo)
    assert cfg["distill"]["commands"]["enabled"] is True
