"""Tests for `repowise update`'s mode-resolution from state.

`update_command` decides between full LLM regeneration and index-only mode
in this priority order:

  1. Explicit `--index-only` flag → index-only.
  2. `--no-docs` → index-only; `--docs` → full.
  3. `state.docs_enabled` from `.repowise/state.json` (default: True).

This is what lets the post-commit hook do the right thing without needing
to know how the user originally indexed the repo.
"""

from __future__ import annotations

from repowise.cli.commands.update_cmd import (
    _infer_legacy_docs_enabled,
    _resolve_index_only_mode as _resolve_mode,
)


class TestModeResolution:
    def test_explicit_index_only_wins(self):
        assert _resolve_mode(
            index_only=True, docs_flag=True, state={"docs_enabled": True}
        ) is True

    def test_no_docs_flag_forces_index_only(self):
        assert _resolve_mode(
            index_only=False, docs_flag=False, state={"docs_enabled": True}
        ) is True

    def test_docs_flag_forces_full(self):
        # User explicitly opts in to LLM regen even though state says no.
        assert _resolve_mode(
            index_only=False, docs_flag=True, state={"docs_enabled": False}
        ) is False

    def test_state_default_index_only(self):
        # No flags → state value drives behavior.
        assert _resolve_mode(
            index_only=False, docs_flag=None, state={"docs_enabled": False}
        ) is True

    def test_state_default_full(self):
        assert _resolve_mode(
            index_only=False, docs_flag=None, state={"docs_enabled": True}
        ) is False

    def test_missing_field_with_provider_defaults_to_full(self):
        # Pre-migration full init wrote provider/model into state. Those
        # users keep the full-mode default after upgrading — no surprise
        # change in behavior.
        assert _resolve_mode(
            index_only=False,
            docs_flag=None,
            state={"provider": "openai", "model": "gpt-5.4-mini"},
        ) is False

    def test_missing_field_no_provider_defaults_to_index_only(self):
        # Pre-migration `init --index-only` skipped writing provider/model,
        # so absence of both signals "this was an index-only init" — and
        # we default future updates to index-only too. Critical for not
        # surprising those users with an LLM bill on first upgrade.
        assert _resolve_mode(
            index_only=False, docs_flag=None, state={"last_sync_commit": "abc"}
        ) is True


class TestLegacyDocsEnabledInference:
    def test_provider_present_means_docs_were_enabled(self):
        assert _infer_legacy_docs_enabled({"provider": "openai"}) is True

    def test_model_present_means_docs_were_enabled(self):
        # Older code paths wrote model without provider in some flows.
        assert _infer_legacy_docs_enabled({"model": "gpt-5.4-mini"}) is True

    def test_neither_means_index_only(self):
        assert _infer_legacy_docs_enabled({"last_sync_commit": "x"}) is False

    def test_empty_state_means_index_only(self):
        assert _infer_legacy_docs_enabled({}) is False
