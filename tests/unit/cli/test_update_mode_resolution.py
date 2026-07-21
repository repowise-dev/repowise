"""Tests for `repowise update`'s mode-resolution from state.

`update_command` decides between full LLM regeneration and index-only mode
in this priority order:

  1. Explicit `--index-only` flag → index-only.
  2. `--no-docs` → index-only; `--docs` → full.
  3. The persisted docs mode from `.repowise/state.json`: only an `llm` repo
     defaults to a full update.

This is what lets the post-commit hook do the right thing without needing
to know how the user originally indexed the repo.
"""

from __future__ import annotations

from repowise.cli.commands.update_cmd import _resolve_index_only_mode as _resolve_mode


class TestModeResolution:
    def test_explicit_index_only_wins(self):
        assert _resolve_mode(index_only=True, docs_flag=True, state={"docs_enabled": True}) is True

    def test_no_docs_flag_forces_index_only(self):
        assert (
            _resolve_mode(index_only=False, docs_flag=False, state={"docs_enabled": True}) is True
        )

    def test_docs_flag_forces_full(self):
        # User explicitly opts in to LLM regen even though state says no.
        assert (
            _resolve_mode(index_only=False, docs_flag=True, state={"docs_enabled": False}) is False
        )

    def test_state_default_index_only(self):
        # No flags → state value drives behavior.
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"docs_enabled": False}) is True
        )

    def test_state_default_full(self):
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"docs_enabled": True}) is False
        )

    def test_missing_field_with_provider_defaults_to_full(self):
        # Pre-migration full init wrote provider/model into state. Those
        # users keep the full-mode default after upgrading — no surprise
        # change in behavior.
        assert (
            _resolve_mode(
                index_only=False,
                docs_flag=None,
                state={"provider": "openai", "model": "gpt-5.4-mini"},
            )
            is False
        )

    def test_missing_field_no_provider_defaults_to_index_only(self):
        # Pre-migration `init --index-only` skipped writing provider/model,
        # so absence of both signals "this was an index-only init" — and
        # we default future updates to index-only too. Critical for not
        # surprising those users with an LLM bill on first upgrade.
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"last_sync_commit": "abc"})
            is True
        )

    def test_deterministic_mode_defaults_to_index_only(self):
        # A template wiki has no provider configured, so a full update would
        # either fail or bill someone who never asked for a model.
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"docs_mode": "deterministic"})
            is True
        )

    def test_llm_mode_defaults_to_full(self):
        assert _resolve_mode(index_only=False, docs_flag=None, state={"docs_mode": "llm"}) is False

    def test_docs_mode_wins_over_legacy_docs_enabled(self):
        assert (
            _resolve_mode(
                index_only=False,
                docs_flag=None,
                state={"docs_mode": "deterministic", "docs_enabled": True},
            )
            is True
        )
