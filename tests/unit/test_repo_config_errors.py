"""Repo-config error surfacing (issue #852).

A broken ``.repowise/config.yaml`` or unreadable ``.env`` used to be silently
treated as "no config": the server's repo-context loader caught everything and
returned empty dicts, so the user's provider/model/coverage settings vanished
and every run used defaults with no explanation.
"""

from __future__ import annotations

import pytest

from repowise.core.repo_config import (
    RepoConfigError,
    load_repo_config,
    load_repo_env,
)


def test_malformed_config_raises_typed_error(tmp_path) -> None:
    """A YAML syntax error must not read as 'no config file'."""
    cfg_dir = tmp_path / ".repowise"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("provider: [unclosed\n  model: x\n")

    with pytest.raises(RepoConfigError) as exc:
        load_repo_config(tmp_path)
    assert "config.yaml" in str(exc.value)


def test_cli_load_config_warns_and_degrades_to_empty(tmp_path) -> None:
    """The CLI chokepoint surfaces the broken config as a warning on stderr
    and degrades to an empty dict — a run continues but the user sees why
    their settings are gone (issue #852)."""
    cfg_dir = tmp_path / ".repowise"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("provider: [unclosed\n  model: x\n")

    from repowise.cli.helpers import load_config

    cfg = load_config(tmp_path)
    assert cfg == {}


def test_non_mapping_config_raises_typed_error(tmp_path) -> None:
    """A config that parses but is not a mapping is still broken."""
    cfg_dir = tmp_path / ".repowise"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("- just\n- a\n- list\n")

    with pytest.raises(RepoConfigError) as exc:
        load_repo_config(tmp_path)
    assert "config.yaml" in str(exc.value)


def test_absent_config_is_still_an_empty_dict(tmp_path) -> None:
    """No file is the normal case and must not raise."""
    assert load_repo_config(tmp_path) == {}
    assert load_repo_env(tmp_path) == {}


def test_unreadable_env_raises_typed_error(tmp_path) -> None:
    """An existing-but-unreadable .env must not read as 'no keys'."""
    env_file = tmp_path / ".repowise" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-test\n")
    env_file.chmod(0o000)

    try:
        with pytest.raises(RepoConfigError) as exc:
            load_repo_env(tmp_path)
        assert ".env" in str(exc.value)
    finally:
        env_file.chmod(0o644)
