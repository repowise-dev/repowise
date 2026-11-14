"""generate persists the provider it wrote with into config.yaml (#4280)."""

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
    outcome = SimpleNamespace(total_pages=5, remaining_template_pages=0, generated_pages=[])
    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-3-5-sonnet")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    cfg = load_config(tmp_path)
    assert cfg.get("provider") == "anthropic"
    assert cfg.get("model") == "claude-3-5-sonnet"
    assert state.get("docs_mode") == "llm"
    assert state.get("config_fingerprint") == config_fingerprint(tmp_path)


def _page(page_id: str, **metadata) -> SimpleNamespace:
    return SimpleNamespace(page_id=page_id, metadata=metadata)


def test_write_state_persists_provider_when_a_partial_run_wrote_pages(tmp_path):
    """Pages left unwritten no longer keep the provider out of config.yaml: the
    MCP server reads it there, so the prose this run wrote gets answers too."""
    (tmp_path / ".repowise").mkdir()
    state = {"docs_mode": "deterministic"}
    outcome = SimpleNamespace(
        total_pages=5,
        remaining_template_pages=2,
        generated_pages=[_page("module_page:a"), _page("module_page:b", stub_fallback_error="x")],
    )
    provider = SimpleNamespace(provider_name="claude_cli", model_name="claude_cli/claude-haiku")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    assert load_config(tmp_path).get("provider") == "claude_cli"
    # A mixed wiki is not claimed to be model-written.
    assert state.get("docs_mode") == "deterministic"
    assert state.get("config_fingerprint") == config_fingerprint(tmp_path)


def test_write_state_does_not_persist_provider_when_no_page_was_written(tmp_path):
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()

    state = {"docs_mode": "deterministic"}
    outcome = SimpleNamespace(
        total_pages=5,
        remaining_template_pages=2,
        generated_pages=[
            _page("module_page:a", stub_fallback_error="not signed in"),
            _page("module_page:b", reused_from_prior_run=True),
        ],
    )
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
    outcome = SimpleNamespace(total_pages=5, remaining_template_pages=0, generated_pages=[])
    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-3-5-sonnet")

    with patch("repowise.cli.commands.generate_cmd.command.get_head_commit", return_value="abc1234"):
        _write_state(tmp_path, state, provider, outcome)

    cfg = load_config(tmp_path)
    assert cfg.get("provider") == "openai"
    assert cfg.get("model") == "gpt-4o"
