"""Regression test for issue #4280: generate --provider persists provider into config.yaml."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from repowise.cli.commands.generate_cmd.command import _write_state
from repowise.cli.helpers import config_fingerprint, load_config, save_config_partial


def test_write_state_persists_provider_when_all_pages_written(tmp_path):
    """When all concept pages are written, provider/model should be persisted to config.yaml."""
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()

    state = {"docs_mode": "deterministic"}
    outcome = SimpleNamespace(total_pages=5, remaining_template_pages=0)
    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-3-5-sonnet")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    cfg = load_config(tmp_path)
    assert cfg.get("provider") == "anthropic"
    assert cfg.get("model") == "claude-3-5-sonnet"
    assert state.get("docs_mode") == "llm"
    assert state.get("config_fingerprint") == config_fingerprint(tmp_path)


def test_write_state_does_not_persist_provider_when_pages_remain_unwritten(tmp_path):
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()

    state = {"docs_mode": "deterministic"}
    outcome = SimpleNamespace(total_pages=5, remaining_template_pages=2)
    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-3-5-sonnet")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    cfg = load_config(tmp_path)
    assert not cfg.get("provider")


def test_write_state_does_not_overwrite_existing_provider(tmp_path):
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    save_config_partial(tmp_path, provider="openai", model="gpt-4o")

    state = {"docs_mode": "deterministic"}
    outcome = SimpleNamespace(total_pages=5, remaining_template_pages=0)
    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-3-5-sonnet")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    cfg = load_config(tmp_path)
    assert cfg.get("provider") == "openai"
    assert cfg.get("model") == "gpt-4o"
