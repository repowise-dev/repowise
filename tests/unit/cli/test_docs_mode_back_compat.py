"""Back-compat for the docs_enabled to docs_mode migration.

Every state.json shape that has ever shipped must keep resolving to the same
behaviour it had before the field was split, so upgrading repowise never
silently changes what `repowise update` does to an already-indexed repo.
"""

from __future__ import annotations

from repowise.cli.commands.update_cmd import _resolve_index_only_mode as _resolve_mode
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode


class TestResolveDocsMode:
    def test_docs_mode_field_wins(self):
        assert resolve_docs_mode({"docs_mode": "deterministic"}) == "deterministic"
        assert resolve_docs_mode({"docs_mode": "llm"}) == "llm"
        assert resolve_docs_mode({"docs_mode": "none"}) == "none"

    def test_unrecognised_docs_mode_falls_through(self):
        # A newer CLI could write a mode this build has never heard of; the
        # legacy field is still there to answer from.
        assert resolve_docs_mode({"docs_mode": "wat", "docs_enabled": True}) == "llm"

    def test_legacy_docs_enabled_true_reads_as_llm(self):
        # Templates did not exist when this field was written, so True could
        # only ever have meant a model wrote the pages.
        assert resolve_docs_mode({"docs_enabled": True}) == "llm"

    def test_legacy_docs_enabled_false_reads_as_none(self):
        assert resolve_docs_mode({"docs_enabled": False}) == "none"

    def test_neither_field_with_provider_reads_as_llm(self):
        assert resolve_docs_mode({"provider": "openai", "model": "gpt-5.4-mini"}) == "llm"

    def test_neither_field_with_model_only_reads_as_llm(self):
        assert resolve_docs_mode({"model": "gpt-5.4-mini"}) == "llm"

    def test_neither_field_without_provider_reads_as_none(self):
        assert resolve_docs_mode({"last_sync_commit": "abc"}) == "none"

    def test_empty_and_missing_state_read_as_none(self):
        assert resolve_docs_mode({}) == "none"
        assert resolve_docs_mode(None) == "none"


class TestDocsModeStateFields:
    def test_writes_both_fields(self):
        assert docs_mode_state_fields("llm") == {"docs_mode": "llm", "docs_enabled": True}
        assert docs_mode_state_fields("deterministic") == {
            "docs_mode": "deterministic",
            "docs_enabled": True,
        }
        assert docs_mode_state_fields("none") == {"docs_mode": "none", "docs_enabled": False}

    def test_round_trips_through_resolve(self):
        for mode in ("none", "deterministic", "llm"):
            assert resolve_docs_mode(docs_mode_state_fields(mode)) == mode


class TestUpdateModePriority:
    """Explicit flag > --docs/--no-docs > state, on every state shape."""

    def test_index_only_flag_beats_everything(self):
        assert _resolve_mode(index_only=True, docs_flag=True, state={"docs_mode": "llm"}) is True

    def test_docs_flags_beat_state(self):
        assert _resolve_mode(index_only=False, docs_flag=False, state={"docs_mode": "llm"}) is True
        assert _resolve_mode(index_only=False, docs_flag=True, state={"docs_mode": "none"}) is False

    def test_legacy_states_keep_their_old_default(self):
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"docs_enabled": True}) is False
        )
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"docs_enabled": False}) is True
        )
        assert (
            _resolve_mode(index_only=False, docs_flag=None, state={"provider": "openai"}) is False
        )
        assert _resolve_mode(index_only=False, docs_flag=None, state={}) is True
