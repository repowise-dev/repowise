"""The one fast-to-full command, and the scope surfaces that carry it."""

from __future__ import annotations

from repowise.core.index_scope import (
    FAST_TO_FULL_COMMAND,
    MODEL_COMMAND,
    FreeRoute,
    answers_key_line,
    answers_upgrade_line,
    compact_index_scope,
    fast_to_full_command,
    model_command,
    resolve_index_scope,
    upgrade_command,
    upgrade_line,
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


def test_answers_upgrade_line_names_the_model_step_until_prose_exists() -> None:
    no_prose = answers_upgrade_line(_scope("deterministic"))
    assert no_prose is not None
    assert f"`{MODEL_COMMAND}` (no re-index)" in no_prose
    index_only = answers_upgrade_line(_scope("none"))
    assert index_only is not None
    assert f"`{FAST_TO_FULL_COMMAND}` with a provider key or a local model," in index_only
    assert answers_upgrade_line(_scope("llm")) is None
    assert answers_upgrade_line(None) is None


def test_one_upgrade_line_names_a_plan_billed_cli_or_falls_back() -> None:
    route = FreeRoute("claude_cli", "Claude Code", minutes=5)
    assert answers_upgrade_line(_scope("deterministic"), route) == (
        "answers and docs: `repowise generate --provider claude_cli` uses your Claude Code "
        "sign-in and plan quota, about 5 minutes; then restart the MCP server"
    )
    assert answers_upgrade_line(_scope("none"), route).startswith(
        f"answers and docs: `{FAST_TO_FULL_COMMAND} --provider claude_cli`"
    )
    assert answers_upgrade_line(_scope("deterministic")) == (
        "answers and docs: `repowise generate` (no re-index) with a provider key or a "
        "local model, then restart the MCP server"
    )
    # A prose index needs only the provider; the line never times a setting.
    assert upgrade_line(_scope("llm"), route) == answers_key_line(route)
    assert "about 5 minutes" not in answers_key_line(route)
    assert "repowise config set provider claude_cli" in answers_key_line(route)
    assert "a provider key or a local model" in upgrade_line(_scope("llm"))
    # The fallback rides on every weak keyless answer: no longer than the lines it replaced.
    assert len(answers_upgrade_line(_scope("deterministic"))) <= 117
    assert len(answers_key_line()) <= 142
    # Never called free: a quota is still spent.
    assert "free" not in answers_upgrade_line(_scope("deterministic"), route)
