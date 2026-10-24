"""The one fast-to-full command, and the scope surfaces that carry it."""

from __future__ import annotations

from repowise.core.index_scope import (
    FAST_TO_FULL_COMMAND,
    MODEL_COMMAND,
    compact_index_scope,
    fast_to_full_command,
    model_command,
    resolve_index_scope,
    upgrade_command,
)


def _scope(docs_mode: str) -> dict:
    return resolve_index_scope({"docs_mode": docs_mode, "run_mode": "fast"})


def test_command_is_update_full_and_targets_a_workspace_repo() -> None:
    assert FAST_TO_FULL_COMMAND == "repowise update --full"
    assert fast_to_full_command() == FAST_TO_FULL_COMMAND
    assert fast_to_full_command("api") == "repowise update --repo api --full"


def test_only_an_index_only_scope_has_an_upgrade_command() -> None:
    assert upgrade_command(_scope("none")) == FAST_TO_FULL_COMMAND
    assert upgrade_command(_scope("deterministic")) is None
    assert upgrade_command(_scope("llm")) is None
    assert upgrade_command(None) is None


def test_compact_scope_carries_the_command_only_when_index_only() -> None:
    assert compact_index_scope(_scope("none"))["upgrade_command"] == FAST_TO_FULL_COMMAND
    assert "upgrade_command" not in compact_index_scope(_scope("deterministic"))
    assert "upgrade_command" not in compact_index_scope(_scope("llm"))


def test_a_fast_run_without_a_docs_stamp_is_index_only() -> None:
    # The same fallback the mode label applies, so the label and command agree.
    assert upgrade_command(resolve_index_scope({"run_mode": "fast"})) == FAST_TO_FULL_COMMAND
    assert upgrade_command(resolve_index_scope({"run_mode": "standard"})) is None


def test_model_command_names_the_step_for_each_mode() -> None:
    assert MODEL_COMMAND == "repowise generate"
    assert model_command(_scope("none")) == FAST_TO_FULL_COMMAND
    assert model_command(_scope("deterministic")) == MODEL_COMMAND
    assert model_command(_scope("llm")) == MODEL_COMMAND
