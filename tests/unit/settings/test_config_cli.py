"""``repowise config``: list, get, explain, set, reset, and the writers behind them."""

from __future__ import annotations

import inspect
import json
import shlex
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.settings import all_settings

_VIEW_KEYS = {
    "key", "type", "default", "description", "area", "scope", "file", "choices", "minimum",
    "maximum", "exclusive_minimum", "env", "internal", "reload",
    "value", "source", "problem", "locked", "changed", "schema_version", "default_note",
    "out_of_range",
}  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for s in all_settings():
        if s.env is not None and not s.env.prefix:
            monkeypatch.delenv(s.env.name, raising=False)
    return root


def _run(repo: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli, ["config", *args, "--path", str(repo)])
    return result.exit_code, result.output


def _json(repo: Path, *args: str) -> dict:
    code, out = _run(repo, *args, "--format", "json")
    return {"_exit": code, **json.loads(out)}


def _config(repo: Path) -> dict:
    path = repo / ".repowise" / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def _write_config(repo: Path, data: dict) -> None:
    (repo / ".repowise" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


# ---------------------------------------------------------------- reading


def test_list_json_carries_the_documented_shape(repo):
    out = _json(repo, "list")
    assert out["_exit"] == 0
    keys = [row["key"] for row in out["settings"]]
    assert "decisions.mode" in keys and "commit_limit" in keys
    assert all(set(row) == _VIEW_KEYS for row in out["settings"])
    assert not any(row["internal"] for row in out["settings"])


def test_list_all_adds_internal_settings(repo):
    assert any(row["internal"] for row in _json(repo, "list", "--all")["settings"])


def test_list_changed_and_area(repo):
    _write_config(repo, {"commit_limit": 300, "language": "en"})
    changed = _json(repo, "list", "--changed")["settings"]
    assert [row["key"] for row in changed] == ["commit_limit"]
    assert changed[0] | {} == changed[0] and changed[0]["source"] == "repo_file"
    hooks = _json(repo, "list", "--area", "hooks")["settings"]
    assert {row["area"] for row in hooks} == {"hooks"}


def test_list_text_groups_by_area(repo):
    _write_config(repo, {"commit_limit": 300})
    code, out = _run(repo, "list")
    assert code == 0
    assert "\nindexing\n" in out and "\ndecisions\n" in out
    assert "commit_limit" in out and "default 5000" in out
    code, out = _run(repo, "list", "--changed")
    assert "1 changed from the default" in out


def test_get_text_and_json(repo):
    _write_config(repo, {"hooks": {"read_skeleton": True}})
    out = _json(repo, "get", "hooks.read_skeleton")
    assert (out["value"], out["source"], out["changed"], out["default"]) == (
        True, "repo_file", True, False,
    )  # fmt: skip
    code, text = _run(repo, "get", "hooks.read_skeleton")
    assert code == 0 and text.startswith("hooks.read_skeleton = true")


def test_get_reports_the_env_source(repo, monkeypatch):
    monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON", "1")
    out = _json(repo, "get", "hooks.read_skeleton")
    assert (out["value"], out["source"]) == (True, "env")


def test_a_set_credential_is_listed_but_never_printed(repo, monkeypatch):
    monkeypatch.setenv("REPOWISE_GITLAB_TOKEN", "glpat-not-a-real-token")
    out = _json(repo, "get", "REPOWISE_GITLAB_TOKEN")
    assert (out["value"], out["source"], out["changed"]) == ("(set, hidden)", "env", True)
    for args in (("list", "--changed"), ("get", "REPOWISE_GITLAB_TOKEN"), ("explain", "REPOWISE_GITLAB_TOKEN")):
        assert "glpat-not-a-real-token" not in _run(repo, *args, "--format", "json")[1]
        assert "glpat-not-a-real-token" not in _run(repo, *args)[1]
    assert _json(repo, "get", "REPOWISE_API_KEY")["value"] is None


def test_unknown_key_is_refused_with_a_suggestion(repo):
    out = _json(repo, "get", "commit_limt")
    assert out["_exit"] == 1 and out["error"] == "unknown_setting"
    assert "commit_limit" in out["message"]


def test_explain_text_and_json(repo):
    out = _json(repo, "explain", "commit_limit")
    assert out["accepts"] == "a whole number, at least 1"
    assert out["applies"] == "Takes effect on the next `repowise update`."
    assert out["set_with"].startswith("repowise config set commit_limit <value>")
    code, text = _run(repo, "explain", "decisions.mode")
    assert code == 0
    assert "one of off, read_only, full" in text and "repowise config reset decisions.mode" in text


def test_explain_lists_custom_styles(repo):
    style = repo / ".repowise" / "styles" / "terse"
    style.mkdir(parents=True)
    (style / "style.yaml").write_text("description: short\nuser_directive: Be brief.\n", encoding="utf-8")
    styles = _json(repo, "explain", "wiki_style")["styles"]
    assert styles[:4] == ["comprehensive", "caveman", "reference", "tutorial"]
    assert "terse" in styles
    assert _json(repo, "set", "wiki_style", "terse")["after"]["value"] == "terse"


def test_explain_env_only_setting(repo):
    out = _json(repo, "explain", "REPOWISE_PORT")
    assert out["set_with"] == "REPOWISE_PORT=<value> in the environment"
    assert "reset_with" not in out


# ---------------------------------------------------------------- set / reset per writer


def test_set_and_reset_a_plain_file_setting(repo):
    _write_config(repo, {"provider": "openai"})
    out = _json(repo, "set", "hooks.read_skeleton", "true")
    assert out["_exit"] == 0 and out["changed"] is True
    assert (out["before"]["value"], out["after"]["value"]) == (False, True)
    assert out["undo"] == [f"repowise config reset hooks.read_skeleton --path {shlex.quote(str(repo))}"]
    assert out["effect"].endswith("Takes effect the next time it is read.")
    assert _config(repo) == {"provider": "openai", "hooks": {"read_skeleton": True}}
    assert _json(repo, "reset", "hooks.read_skeleton")["after"]["value"] is False
    assert _config(repo) == {"provider": "openai"}


def test_set_text_prints_change_effect_and_undo(repo):
    _write_config(repo, {"commit_limit": 300})
    code, out = _run(repo, "set", "commit_limit", "400")
    assert code == 0
    assert "commit_limit: 300 -> 400" in out
    assert "Takes effect on the next `repowise update`." in out
    assert "Undo: repowise config set commit_limit 300" in out


def test_set_the_same_value_changes_nothing(repo):
    _write_config(repo, {"commit_limit": 300})
    code, out = _run(repo, "set", "commit_limit", "300")
    assert code == 0 and "nothing changed" in out


def test_set_validates_type_choices_and_bounds(repo):
    for key, value in (("commit_limit", "abc"), ("language", "xx"), ("decisions.mode", "on")):
        out = _json(repo, "set", key, value)
        assert out["_exit"] == 1 and out["error"] == "invalid_value", key
        assert out["message"].startswith(key)
    assert _config(repo) == {}


def test_set_a_list(repo):
    _json(repo, "set", "exclude_patterns", "vendor/,dist/")
    assert _config(repo) == {"exclude_patterns": ["vendor/", "dist/"]}
    out = _json(repo, "set", "exclude_patterns", "[build/]")
    assert out["undo"] == [f"repowise config set exclude_patterns vendor/,dist/ --path {shlex.quote(str(repo))}"]
    assert _config(repo)["exclude_patterns"] == ["build/"]
    _json(repo, "reset", "exclude_patterns")
    assert _config(repo) == {}


def test_set_wiki_style_through_the_file_settings_writer(repo):
    assert _json(repo, "set", "wiki_style", "nope")["error"] == "invalid_value"
    _json(repo, "set", "wiki_style", "reference")
    assert _config(repo) == {"wiki_style": "reference"}
    _json(repo, "set", "wiki_style", "comprehensive")
    assert _config(repo) == {}


def test_decisions_go_through_the_policy_writer(repo):
    out = _json(repo, "set", "decisions.mode", "read_only")
    assert out["after"]["value"] == "read_only"
    also = {row["key"]: (row["before"]["value"], row["after"]["value"]) for row in out["also"]}
    assert also["decisions.agent_acceptance"] == (True, False)
    assert _config(repo)["decisions"]["mode"] == "read_only"
    _json(repo, "set", "decisions.sync", "true")
    assert _config(repo)["decisions"]["sync"] is True
    _json(repo, "reset", "decisions.mode")  # a mode resets the switches under it
    assert "decisions" not in _config(repo)
    assert _json(repo, "get", "decisions.sync")["value"] is False


def test_a_mode_change_lists_only_what_moved_and_one_undo(repo):
    out = _json(repo, "set", "decisions.mode", "read_only")
    assert {row["key"] for row in out["also"]} == {
        "decisions.agent_acceptance", "decisions.capture_prompt",
    }  # fmt: skip
    assert out["undo"] == [f"repowise config reset decisions.mode --path {shlex.quote(str(repo))}"]
    assert _config(repo) == {"decisions": {"mode": "read_only"}}


_BLOCK = {"provider": "openai", "decisions": {"sync": True, "sources": {"pr": False}}}


@pytest.mark.parametrize("start", [{"provider": "openai"}, _BLOCK])
@pytest.mark.parametrize(
    ("key", "value"),
    [("decisions.mode", "read_only"), ("decisions.mode", "off"), ("decisions.sync", "true"),
     ("decisions.sources.session.enabled", "true"), ("decisions.discovery.max_sessions", "3"),
     ("decisions.llm", "false"), ("commit_limit", "300"), ("hooks.read_skeleton", "true")],
)  # fmt: skip
def test_set_then_its_undo_returns_the_file_to_its_bytes(repo, start, key, value):
    from repowise.core.repo_config import save_repo_config

    save_repo_config(repo, start)
    path = repo / ".repowise" / "config.yaml"
    original = path.read_bytes()
    out = _json(repo, "set", key, value)
    assert out["_exit"] == 0
    assert bool(out["undo"]) is out["changed"]
    for line in out["undo"]:
        args = shlex.split(line)[2:]  # drop "repowise config"
        assert _run(repo, *args[: args.index("--path")])[0] == 0, line
    if len(out["undo"]) > 1:
        # Restoring a switch a mode had reset puts it back at the end of its block.
        assert _config(repo) == start
    else:
        assert path.read_bytes() == original


def test_set_then_reset_on_a_file_without_the_key_restores_its_bytes(repo):
    from repowise.core.repo_config import save_repo_config

    save_repo_config(repo, {"provider": "openai"})
    path = repo / ".repowise" / "config.yaml"
    original = path.read_bytes()
    for key, value in (("decisions.mode", "read_only"), ("decisions.sources.session.enabled", "true")):
        _json(repo, "set", key, value)
        _json(repo, "reset", key)
        assert path.read_bytes() == original, key


@pytest.mark.parametrize("spelling", ["read-only", "read_only"])
def test_both_mode_spellings_work_in_both_commands(tmp_path, monkeypatch, spelling):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(
        "repowise.cli.commands.decision_config_cmd._provider_available", lambda _repo: False
    )
    runner = CliRunner()
    first = runner.invoke(cli, ["decision", "config", "mode", spelling, str(a), "--format", "json"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli, ["config", "set", "decisions.mode", spelling, "--path", str(b), "--format", "json"]
    )
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["after"]["value"] == "read_only"
    assert _config(a)["decisions"]["mode"] == _config(b)["decisions"]["mode"] == "read_only"


def test_decision_source_and_discovery_settings(repo):
    _json(repo, "set", "decisions.sources.session.enabled", "true")
    _json(repo, "set", "decisions.discovery.max_sessions", "3")
    assert _json(repo, "get", "decisions.sources.session.enabled")["value"] is True
    assert _json(repo, "get", "decisions.discovery.max_sessions")["value"] == 3
    out = _json(repo, "reset", "decisions.sources.session.enabled")
    assert out["after"]["value"] is False


def test_provider_change_drops_a_model_meant_for_the_old_one(repo):
    _write_config(repo, {"provider": "openai", "model": "gpt-5"})
    out = _json(repo, "set", "provider", "anthropic")
    assert [row["key"] for row in out["also"]] == ["model"]
    assert _config(repo) == {"provider": "anthropic"}
    assert _json(repo, "set", "provider", "nope")["error"] == "invalid_value"


def test_user_setting_goes_to_platform_json(repo, tmp_path):
    out = _json(repo, "set", "hints_enabled", "false")
    assert out["after"]["source"] == "user_file"
    state = json.loads((tmp_path / "home" / ".repowise" / "platform.json").read_text())
    assert state["hints_enabled"] is False
    _json(repo, "reset", "hints_enabled")
    state = json.loads((tmp_path / "home" / ".repowise" / "platform.json").read_text())
    assert "hints_enabled" not in state


def test_env_only_setting_is_refused(repo):
    out = _json(repo, "set", "REPOWISE_PORT", "9000")
    assert out["_exit"] == 1 and out["error"] == "env_only"
    assert "REPOWISE_PORT" in out["message"] and "environment" in out["message"]
    assert _json(repo, "reset", "REPOWISE_PORT")["error"] == "env_only"


def test_env_override_is_named(repo, monkeypatch):
    monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON", "0")
    out = _json(repo, "set", "hooks.read_skeleton", "true")
    assert out["env_override"] == "REPOWISE_HOOK_READ_SKELETON"
    assert out["after"]["value"] is False
    assert out["changed"] is True  # the file took it; the variable still decides
    assert _config(repo) == {"hooks": {"read_skeleton": True}}
    _json(repo, "reset", "hooks.read_skeleton")
    _, text = _run(repo, "set", "hooks.read_skeleton", "true")
    assert "Saved, but REPOWISE_HOOK_READ_SKELETON" in text


def test_undo_restores_the_file_value_under_an_env_override(repo, monkeypatch):
    _write_config(repo, {"provider": "openai"})
    monkeypatch.setenv("REPOWISE_EMBEDDER", "gemini")
    _write_config(repo, {"embedder": "openai"})
    out = _json(repo, "set", "embedder", "ollama")
    assert out["before"]["source"] == "env"
    assert out["undo"] == [f"repowise config set embedder openai --path {shlex.quote(str(repo))}"]


def test_a_config_that_is_not_utf8_is_refused(repo):
    path = repo / ".repowise" / "config.yaml"
    raw = b"provider: \xff\xfe\n"
    path.write_bytes(raw)
    for args in (("list",), ("set", "commit_limit", "300")):
        assert _json(repo, *args)["error"] == "config_unreadable"
    assert path.read_bytes() == raw


def test_claude_md_opt_out_on_a_broken_config_warns_and_carries_on(repo):
    from rich.console import Console

    from repowise.cli.editor_integrations.claude import maybe_generate_claude_md

    path = repo / ".repowise" / "config.yaml"
    path.write_text(BROKEN, encoding="utf-8")
    out = Console(record=True, width=200)
    assert maybe_generate_claude_md(out, repo, no_claude_md=True) is None
    assert "Nothing was saved" in out.export_text()
    assert path.read_text(encoding="utf-8") == BROKEN


# ---------------------------------------------------------------- broken config


BROKEN = "provider: openai\nmodel: [unclosed\n"


@pytest.mark.parametrize(
    "args",
    [("set", "commit_limit", "300"), ("reset", "commit_limit"), ("set", "decisions.sync", "true"),
     ("set", "provider", "anthropic"), ("set", "wiki_style", "reference"), ("list",)],
)  # fmt: skip
def test_a_broken_config_is_refused_and_left_alone(repo, args):
    path = repo / ".repowise" / "config.yaml"
    path.write_text(BROKEN, encoding="utf-8")
    out = _json(repo, *args)
    assert out["_exit"] == 1 and out["error"] == "config_unreadable"
    assert path.read_text(encoding="utf-8") == BROKEN


def test_cli_partial_writers_refuse_a_broken_config(repo):
    import click

    from repowise.cli.editor_files import set_editor_file_enabled
    from repowise.cli.helpers import save_config_partial, save_hook_surface_enabled

    path = repo / ".repowise" / "config.yaml"
    path.write_text(BROKEN, encoding="utf-8")
    for write in (
        lambda: save_config_partial(repo, commit_limit=300),
        lambda: save_hook_surface_enabled(repo, "read_skeleton", enabled=True),
        lambda: set_editor_file_enabled(repo, "claude_md", False),
    ):
        with pytest.raises(click.ClickException, match="Nothing was saved"):
            write()
        assert path.read_text(encoding="utf-8") == BROKEN


# ---------------------------------------------------------------- aliases and contracts


@pytest.mark.parametrize(
    ("alias", "key", "value"),
    [
        (["sync", "--on"], "decisions.sync", "true"),
        (["mode", "read-only"], "decisions.mode", "read_only"),
        (["preset", "local_only"], "decisions.preset", "local_only"),
        (["session-start", "--on"], "decisions.session_start", "true"),
        (["discovery", "--max-sessions", "3"], "decisions.discovery.max_sessions", "3"),
    ],
)
def test_decision_config_aliases_write_what_config_set_writes(tmp_path, monkeypatch, alias, key, value):
    """One write path: the two spellings leave the same bytes."""
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".repowise").mkdir(parents=True)
    runner = CliRunner()
    monkeypatch.setattr(
        "repowise.cli.commands.decision_config_cmd._provider_available", lambda _repo: False
    )
    first = runner.invoke(cli, ["decision", "config", *alias, str(a), "--format", "json"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(cli, ["config", "set", key, value, "--path", str(b), "--format", "json"])
    assert second.exit_code == 0, second.output
    path = Path(".repowise", "config.yaml")
    assert (a / path).read_bytes() == (b / path).read_bytes()


def test_every_writer_and_parser_imports_with_the_one_signature():
    for s in all_settings():
        for ref, arity in ((s.writer, 3), (s.parser, 1)):
            if ref is None:
                continue
            module, _, attr = ref.partition(":")
            fn = getattr(import_module(module), attr)
            params = [
                p for p in inspect.signature(fn).parameters.values()
                if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]  # fmt: skip
            assert len(params) == arity, (s.key, ref)


# ---------------------------------------------------------------- init saves what it was told


def test_init_rejects_a_commit_limit_out_of_range(tmp_path):
    for value in ("0", "-5"):
        result = CliRunner().invoke(cli, ["init", str(tmp_path), "--commit-limit", value])
        assert result.exit_code == 2, value
        assert "commit-limit" in result.output


@pytest.mark.parametrize(("flag", "stored", "written"), [
    (True, None, True), (False, True, False), (False, None, None), (None, True, True),
])  # fmt: skip
def test_init_saves_follow_renames(repo, flag, stored, written):
    from repowise.cli.commands.init_cmd._finish import save_run_config
    from repowise.core.generation.styles import DEFAULT_STYLE

    config = {} if stored is None else {"follow_renames": stored}
    _write_config(repo, config)
    opts = SimpleNamespace(
        onboarding=True, folder_coverage_rules=(), wiki_style=DEFAULT_STYLE,
        commit_limit=None, follow_renames=flag,
    )  # fmt: skip
    run = SimpleNamespace(
        opts=opts, repo_path=repo, max_file_pages=None, config=config, language="en",
        effective_index_only=False,
    )  # fmt: skip
    save_run_config(run)
    assert _config(repo).get("follow_renames") == written


@pytest.mark.parametrize(
    ("cfg", "limit", "renames"),
    [({}, 5000, False), ({"commit_limit": 20000, "follow_renames": "true"}, 20000, True),
     ({"commit_limit": "abc"}, 5000, False)],
)  # fmt: skip
def test_workspace_add_reads_the_primary_config_like_update(cfg, limit, renames):
    from repowise.cli.commands.workspace_cmd._index import _added_repo_ctx

    ctx = _added_repo_ctx(None, SimpleNamespace(), cfg, concurrency=1, save_key=False)
    assert (ctx.resolved_commit_limit, ctx.follow_renames) == (limit, renames)


def test_base_url_is_set_per_provider_and_the_family_is_refused(repo):
    out = _json(repo, "set", "openai.base_url", "http://localhost:8080/v1")
    assert out["_exit"] == 0
    assert _config(repo) == {"openai": {"base_url": "http://localhost:8080/v1"}}
    assert _json(repo, "set", "openai.base_url", "not a url")["error"] == "invalid_value"
    _json(repo, "reset", "openai.base_url")
    assert _config(repo) == {}
    refused = _json(repo, "set", "<provider>.base_url", "http://x")
    assert refused["error"] == "invalid_value" and "openai" in refused["message"]
