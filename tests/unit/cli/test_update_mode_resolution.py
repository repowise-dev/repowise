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

from repowise.cli.commands.update_cmd import _resolve_index_only_mode as _resolve_mode


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

    def test_missing_field_defaults_to_full(self):
        # Backward compat: state files written before docs_enabled existed
        # must continue to behave as full-mode (the original behavior).
        assert _resolve_mode(
            index_only=False, docs_flag=None, state={}
        ) is False
