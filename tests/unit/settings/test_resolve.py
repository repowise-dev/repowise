"""The one resolver: precedence, typed validation, and each invalid policy."""

from __future__ import annotations

import pytest

from repowise.core.settings import Setting, SettingError, Source, get, resolve
from repowise.core.settings.model import FALSY, Env
from repowise.core.settings.resolve import MISSING, lookup, validate


def test_lookup_walks_dotted_keys_and_treats_null_as_absent():
    cfg = {"a": {"b": {"c": 1}, "n": None}}
    assert lookup(cfg, "a.b.c") == 1
    assert lookup(cfg, "a.n") is MISSING
    assert lookup(cfg, "a.x") is MISSING
    assert lookup({"a": 3}, "a.b") is MISSING
    assert lookup(None, "a") is MISSING


def test_default_when_nothing_is_set():
    r = resolve("commit_limit", {}, environ={})
    assert (r.value, r.source, r.problem) == (5000, Source.DEFAULT, None)


def test_file_value_and_its_source():
    r = resolve("commit_limit", {"commit_limit": 200}, environ={})
    assert (r.value, r.source) == (200, Source.REPO_FILE)


def test_file_beats_env_unless_env_wins():
    env = {"REPOWISE_PROVIDER": "openai", "REPOWISE_EMBEDDER": "gemini"}
    cfg = {"provider": "anthropic", "embedder": "openai"}
    assert resolve("provider", cfg, environ=env).value == "anthropic"
    assert resolve("provider", {}, environ=env).source is Source.ENV
    embedder = resolve("embedder", cfg, environ=env)
    assert (embedder.value, embedder.source) == ("gemini", Source.ENV)


def test_blank_env_is_unset_except_for_a_switch():
    assert resolve("provider", {}, environ={"REPOWISE_PROVIDER": "  "}).source is Source.DEFAULT
    flag = resolve("hooks.read_skeleton", {"hooks": {"read_skeleton": True}}, environ={"REPOWISE_HOOK_READ_SKELETON": ""})
    assert (flag.value, flag.source) == (False, Source.ENV)


def test_env_bool_rules():
    on_unless_off = get("REPOWISE_WORKTREE_FALLBACK")
    assert resolve(on_unless_off, environ={"REPOWISE_WORKTREE_FALLBACK": "maybe"}).value is True
    assert resolve(on_unless_off, environ={"REPOWISE_WORKTREE_FALLBACK": " Off "}).value is False
    off_unless_on = get("REPOWISE_NO_HINTS")
    assert resolve(off_unless_on, environ={"REPOWISE_NO_HINTS": "maybe"}).value is False
    assert resolve(off_unless_on, environ={"REPOWISE_NO_HINTS": "YES"}).value is True
    assert Env("X", false_values=FALSY).parse_bool("") is True


def test_env_numbers_and_lists_are_parsed():
    assert resolve("REPOWISE_PORT", environ={"REPOWISE_PORT": " 8080 "}).value == 8080
    r = resolve("REPOWISE_PORT", environ={"REPOWISE_PORT": "eighty"})
    assert (r.value, r.source) == (7337, Source.DEFAULT)
    assert "whole number" in r.problem
    cors = resolve("REPOWISE_CORS_ORIGINS", environ={"REPOWISE_CORS_ORIGINS": "a, b,,c"})
    assert cors.value == ["a", "b", "c"]


def test_invalid_file_value_falls_to_the_next_layer_with_a_problem():
    r = resolve("commit_limit", {"commit_limit": 0}, environ={})
    assert (r.value, r.source) == (5000, Source.DEFAULT)
    assert r.problem == "commit_limit: must be at least 1"
    r = resolve("events.retention_days", {"events": {"retention_days": 0}}, environ={})
    assert r.problem == "events.retention_days: must be more than 0"
    r = resolve("commit_limit", {"commit_limit": True}, environ={})
    assert "number" in r.problem


def test_invalid_value_can_raise_or_read_as_a_fallback():
    with pytest.raises(SettingError, match="max_tokens"):
        resolve("max_tokens", {"max_tokens": -1}, environ={})
    r = resolve("attribution.enabled", {"attribution": {"enabled": "maybe"}}, environ={})
    assert (r.value, r.source) == (False, Source.REPO_FILE)
    assert r.problem


def test_choices_lists_and_mappings():
    assert resolve("language", {"language": "xx"}, environ={}).value == "en"
    assert resolve("coverage.artifacts", {"coverage": {"artifacts": "cov.xml"}}, environ={}).value == [
        "cov.xml"
    ]
    assert resolve("forges.hosts", {"forges": {"hosts": ["x"]}}, environ={}).value == {}


def test_parser_names_the_areas_own_grammar():
    r = resolve("reasoning", {"reasoning": "HIGH"}, environ={})
    assert r.value == "high"
    assert resolve("mcp.tools", {"mcp": {"tools": "lean, +get_why"}}, environ={}).value == [
        "lean",
        "+get_why",
    ]


def test_validate_is_usable_on_its_own():
    s = Setting("x.n", "float", 1.0, "N.", "health", minimum=0, maximum=10)
    assert validate(s, 3) == 3
    for bad in (11, -1, float("nan"), "3"):
        with pytest.raises(ValueError):
            validate(s, bad)


def test_decisions_resolve_through_the_policy():
    cfg = {"decisions": {"mode": "read_only"}}
    r = resolve("decisions.agent_acceptance", cfg, environ={})
    # The mode sets this switch's default, so the registry states none.
    assert get("decisions.agent_acceptance").default is None
    assert "decisions.mode" in get("decisions.agent_acceptance").default_note
    assert (r.value, r.source) == (False, Source.REPO_FILE)
    assert resolve("decisions.agent_acceptance", {}, environ={}).source is Source.DEFAULT
    r = resolve("decisions.sources.pr.enabled", {"decisions": {"preset": "local_only"}}, environ={})
    assert r.value is False
    assert resolve("decisions.mode", {"decisions": {"mode": False}}, environ={}).value == "off"


def test_decisions_problems_and_sources():
    r = resolve("decisions.require_approval", {"decisions": {"require_approval": "no"}})
    assert (r.value, r.source) == (True, Source.REPO_FILE)
    assert "require_approval" in r.problem
    r = resolve("decisions.discovery.max_sessions", {"decisions": {"discovery": {"max_sessions": 99}}})
    assert (r.value, r.source) == (12, Source.DEFAULT)
    assert "max_sessions" in r.problem
    r = resolve("decisions.sources.pr.enabled", {"decisions": {"sources": {"pr": True}}})
    assert (r.value, r.source) == (True, Source.REPO_FILE)


def test_clamp_caps_to_the_nearest_bound():
    name = "REPOWISE_EMBED_TIMEOUT_S"
    assert resolve(name, environ={name: "500"}).value == 120.0
    assert resolve(name, environ={name: "inf"}).value == 120.0
    r = resolve(name, environ={name: "0"})
    assert (r.value, r.source) == (8.0, Source.DEFAULT)
    assert "more than 0" in r.problem
    assert resolve("REPOWISE_CLAUDE_CLI_CONCURRENCY", environ={"REPOWISE_CLAUDE_CLI_CONCURRENCY": "-3"}).value == 1


def test_a_parser_that_raises_anything_is_a_problem_not_a_crash():
    for bad in (5, {"a": 1}, 2.5):
        r = resolve("mcp.tools", {"mcp": {"tools": bad}}, environ={})
        assert (r.value, r.source) == (None, Source.DEFAULT)
        assert r.problem


def test_reader_tolerances():
    assert resolve("embedder", {"embedder": " OpenAI "}, environ={}).value == "openai"
    assert resolve("hooks.read_skeleton_mode", {"hooks": {"read_skeleton_mode": "PLUS"}}, environ={}).value == "plus"
    assert resolve("max_tokens", {"max_tokens": "2048"}, environ={}).value == 2048
    assert resolve("max_file_pages", {"max_file_pages": 12.9}, environ={}).value == 12
    assert validate(get("decisions.mode"), "read-only") == "read_only"
    assert validate(get("decisions.mode"), False) == "off"


def test_switches_read_quoted_and_numeric_spellings():
    """A quoted ``"false"`` or ``0`` is off, as every reader now takes it; anything else is a problem."""
    for raw, value in (("false", False), (" On ", True), (0, False), (1, True)):
        r = resolve("enable_onboarding", {"enable_onboarding": raw}, environ={})
        assert (r.value, r.source, r.problem) == (value, Source.REPO_FILE, None), raw
    r = resolve("enable_onboarding", {"enable_onboarding": "maybe"}, environ={})
    assert (r.value, r.source) == (True, Source.DEFAULT)
    assert r.problem


def test_clamp_never_passes_an_exclusive_minimum():
    name = "REPOWISE_VECTOR_SEARCH_TIMEOUT_S"
    for raw in ("0", "-5", "-inf"):
        r = resolve(name, environ={name: raw})
        assert (r.value, r.source) == (30.0, Source.DEFAULT), raw
    assert resolve(name, environ={name: "1e999"}).value == 120.0
    assert isinstance(resolve(name, environ={name: "500"}).value, float)


def test_blank_folded_text_is_unset():
    r = resolve("embedder", {"embedder": "  "}, environ={})
    assert (r.value, r.source, r.problem) == (None, Source.DEFAULT, None)


def test_max_file_pages_text_from_env_or_file():
    assert resolve("max_file_pages", {"max_file_pages": 5.7}, environ={}).value == 5
    assert resolve("max_file_pages", {"max_file_pages": "7"}, environ={}).value == 7


def test_decisions_preset_and_legacy_warnings_attach_to_their_keys():
    r = resolve("decisions.preset", {"decisions": {"preset": "bogus"}})
    assert r.value is None and "bogus" in r.problem
    cfg = {"decisions": {"preset": "off", "session_mining": True}}
    assert "session_mining" in resolve("decisions.session_mining", cfg).problem
