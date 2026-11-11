"""Each registered default, type and invalid-value policy matches its parser.

The registry describes settings; the parsers below still decide them. Every
check runs the real parser. Where the two disagree today the case sits in a
``*_DISAGREEMENTS`` table with the reason, and its test asserts that they still
disagree, so fixing the parser fails the test and the row is deleted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from repowise.core.settings import Source, all_settings, get, resolve, settings_in
from repowise.core.settings.areas import editor, events, generation, providers
from repowise.core.settings.areas._common import file_reload
from repowise.core.settings.resolve import validate

ROOT = Path(__file__).resolve().parents[3]
CLI_SRC = ROOT / "packages" / "cli" / "src" / "repowise" / "cli"


def _repo(tmp_path: Path, config: Any | None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (tmp_path / ".repowise").mkdir(exist_ok=True)
        (tmp_path / ".repowise" / "config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
    return tmp_path


def _registry(key: str, config: dict | None, environ: dict | None = None) -> Any:
    return resolve(key, config or {}, environ=environ or {}).value


# ---------------------------------------------------------------- decisions


def test_decisions_keys_are_the_policy_keys():
    from repowise.core.analysis.decisions.policy import POLICY_CONFIG_KEYS, SOURCE_SPECS

    keys = [s.key for s in settings_in("decisions")]
    assert {k.split(".")[1] for k in keys} == POLICY_CONFIG_KEYS
    togglable = {spec.key for spec in SOURCE_SPECS if spec.togglable}
    assert {k.split(".")[2] for k in keys if k.startswith("decisions.sources.")} == togglable


def test_decisions_defaults_are_what_an_empty_block_resolves_to():
    for s in settings_in("decisions"):
        assert resolve(s, {}).value == s.default, s.key


@pytest.mark.parametrize(
    "name",
    ["agent_acceptance", "capture_prompt", "require_approval", "sync", "session_start", "upkeep_prompt"],
)
def test_decisions_unreadable_switch_matches_the_declared_policy(name):
    from repowise.core.analysis.decisions.policy import resolve_policy

    s = get(f"decisions.{name}")
    real = getattr(resolve_policy({"decisions": {name: "no"}}).policy, name)
    expected = s.fallback if s.on_invalid == "fallback" else s.default
    assert real == expected


def test_decisions_mode_unreadable_matches():
    from repowise.core.analysis.decisions.policy import resolve_policy

    s = get("decisions.mode")
    assert resolve_policy({"decisions": {"mode": "bogus"}}).policy.mode == s.fallback


@pytest.mark.parametrize("value", [0, 25, 13, "12", True])
def test_decisions_discovery_bounds_match(value):
    from repowise.core.analysis.decisions.policy import resolve_policy

    s = get("decisions.discovery.max_sessions")
    real = resolve_policy({"decisions": {"discovery": {"max_sessions": value}}}).policy
    try:
        expected = validate(s, value)
    except ValueError:
        expected = s.default
    assert real.discovery.max_sessions == expected


# ---------------------------------------------------------------- hooks


def _cli_hook_flags() -> set[str]:
    from repowise.cli.commands.augment_cmd import read_skeleton, reread, search_digest

    flags = {read_skeleton.CONFIG_FLAG, reread.CONFIG_FLAG, search_digest.CONFIG_FLAG}
    call = re.compile(r"hook_flag_enabled\([^,()]+,\s*\"(\w+)\"")
    for path in CLI_SRC.rglob("*.py"):
        flags.update(call.findall(path.read_text(encoding="utf-8")))
    return flags


def test_hook_flags_match_the_cli():
    from repowise.cli.commands.augment_cmd.read_skeleton import MODE_KEY

    registered = {s.key.split(".", 1)[1] for s in settings_in("hooks") if s.scope == "repo"}
    assert registered == _cli_hook_flags() | {MODE_KEY}


_HOOK_CONFIGS = [None, {}, {"hooks": "x"}, {"hooks": {}}]
_HOOK_VALUES = [True, False, "true", 1]
_HOOK_ENVS = [None, "1", "on", " YES ", "0", "", "maybe"]


@pytest.mark.parametrize("env_value", _HOOK_ENVS)
@pytest.mark.parametrize("variant", range(len(_HOOK_CONFIGS) + len(_HOOK_VALUES)))
def test_hook_flags_resolve_like_hook_flag_enabled(tmp_path, monkeypatch, variant, env_value):
    from repowise.cli.commands.augment_cmd._shared import hook_flag_enabled

    for flag in sorted(_cli_hook_flags()):
        if variant < len(_HOOK_CONFIGS):
            config = _HOOK_CONFIGS[variant]
        else:
            config = {"hooks": {flag: _HOOK_VALUES[variant - len(_HOOK_CONFIGS)]}}
        repo = _repo(tmp_path / flag, config)
        env_name = f"REPOWISE_HOOK_{flag.upper()}"
        environ = {} if env_value is None else {env_name: env_value}
        monkeypatch.delenv(env_name, raising=False)
        if env_value is not None:
            monkeypatch.setenv(env_name, env_value)
        assert hook_flag_enabled(repo, flag) == _registry(f"hooks.{flag}", config, environ), flag


#: (config value, env value) where ``configured_mode`` and the registry part.
SKELETON_MODE_DISAGREEMENTS = {
    ("PLUS", None): "the reader lower-cases; the registry's choices are exact",
    (" plus ", None): "the reader strips; the registry's choices are exact",
}


@pytest.mark.parametrize(
    ("value", "env_value"),
    [
        ("plus", None),
        ("smart", None),
        ("bogus", None),
        (None, "plus"),
        ("plus", "smart"),
        ("plus", ""),
        *SKELETON_MODE_DISAGREEMENTS,
    ],
)
def test_read_skeleton_mode(tmp_path, monkeypatch, value, env_value):
    from repowise.cli.commands.augment_cmd.read_skeleton import configured_mode

    config = {"hooks": {"read_skeleton_mode": value}} if value is not None else {}
    repo = _repo(tmp_path, config)
    monkeypatch.delenv("REPOWISE_HOOK_READ_SKELETON_MODE", raising=False)
    environ = {}
    if env_value is not None:
        monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON_MODE", env_value)
        environ = {"REPOWISE_HOOK_READ_SKELETON_MODE": env_value}
    agree = configured_mode(repo) == _registry("hooks.read_skeleton_mode", config, environ)
    assert agree is ((value, env_value) not in SKELETON_MODE_DISAGREEMENTS)


# ---------------------------------------------------------------- distill


def test_distill_defaults_match_both_readers(tmp_path):
    from repowise.cli.rewrite_hook import _load_commands_config
    from repowise.core.distill.config import omission_store_settings

    enabled, permission, families = _load_commands_config(str(tmp_path))
    assert enabled is get("distill.enabled").default is get("distill.commands.enabled").default
    assert permission == get("distill.commands.permission").default
    assert families == get("distill.commands.families").default
    assert omission_store_settings(None) == (
        get("distill.omission_store.ttl_days").default,
        get("distill.omission_store.max_mb").default,
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enabled", "yes"),
        ("commands.enabled", 0),
        ("commands.permission", "maybe"),
        ("commands.permission", "ask"),
        ("commands.families", ["x"]),
        ("commands.disabled_filters", "logs"),
        ("omission_store.ttl_days", 0),
        ("omission_store.ttl_days", True),
        ("omission_store.max_mb", 2.5),
    ],
)
def test_distill_validator_and_registry_agree_on_what_is_invalid(key, value):
    from repowise.core.distill.config import validate_distill_config

    block: dict = {}
    node = block
    *parents, leaf = key.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    doctor_says_bad = bool(validate_distill_config(block))
    try:
        validate(get(f"distill.{key}"), value)
        registry_says_bad = False
    except ValueError:
        registry_says_bad = True
    expected_disagreement = DISTILL_VALIDATION_DISAGREEMENTS.get((key, repr(value)))
    assert (doctor_says_bad != registry_says_bad) is bool(expected_disagreement)


#: Where doctor's validator and the registry's typed check part.
DISTILL_VALIDATION_DISAGREEMENTS = {
    ("commands.disabled_filters", "'logs'"): "doctor wants a list; the registry reads one bare "
    "name as a list of one, as the coverage and section readers do",
}


def test_distill_readers_disagree_with_each_other_on_a_non_bool_enabled(tmp_path):
    """``enabled: 0``: the hook reads on (``is False``), ``repowise distill`` reads off
    (``bool(0)``), doctor and the registry call it invalid (default on)."""
    from repowise.cli.commands.distill_cmd import _load_distill_config
    from repowise.cli.rewrite_hook import _load_commands_config

    repo = _repo(tmp_path, {"distill": {"enabled": 0}})
    assert _load_commands_config(str(repo))[0] is True
    assert _load_distill_config(repo)[0] is False
    assert _registry("distill.enabled", {"distill": {"enabled": 0}}) is True


def test_distill_hook_accepts_off_spellings_the_registry_rejects(tmp_path):
    from repowise.cli.rewrite_hook import _load_commands_config

    repo = _repo(tmp_path, {"distill": {"commands": {"permission": "deny"}}})
    assert _load_commands_config(str(repo))[0] is False
    with pytest.raises(ValueError):
        validate(get("distill.commands.permission"), "deny")


# ---------------------------------------------------------------- file settings and buckets


def test_file_settings_are_registered_with_their_defaults_and_writer():
    from repowise.core.repo_config import FILE_SETTING_KEYS, _file_setting_default

    for key in FILE_SETTING_KEYS:
        s = get(key)
        assert s.default == _file_setting_default(key), key
        assert s.writer == "repowise.core.repo_config:save_file_settings"


def test_every_dependency_key_is_registered():
    from repowise.core.repo_config import CONFIG_DEPENDENCY_KEYS

    keys = {s.key for s in all_settings() if s.scope == "repo"}
    for bucket in CONFIG_DEPENDENCY_KEYS.values():
        for name in bucket:
            assert name in keys or any(k.startswith(f"{name}.") for k in keys), name


def test_reload_follows_the_dependency_table():
    from repowise.core.repo_config import CONFIG_DEPENDENCY_KEYS

    listed = set().union(*CONFIG_DEPENDENCY_KEYS.values())
    for s in all_settings():
        if s.scope == "repo" and s.reload != "reindex" and s.key.split(".")[0] in listed:
            assert s.reload == file_reload(s.key), s.key


# ---------------------------------------------------------------- generation


def test_generation_defaults_match_the_generation_config():
    from repowise.core.generation.models import (
        DEFAULT_MAX_TOKENS,
        DEFAULT_SOURCE_EVIDENCE_TOKEN_BUDGET,
        GenerationConfig,
    )
    from repowise.core.generation.styles import DEFAULT_STYLE
    from repowise.core.providers.embedding.registry import _BUILTIN_EMBEDDERS, DEFAULT_EMBEDDER
    from repowise.core.reasoning import resolve_reasoning

    assert get("max_tokens").default == DEFAULT_MAX_TOKENS
    assert get("generation_context.token_budget").default == DEFAULT_SOURCE_EVIDENCE_TOKEN_BUDGET
    cfg = GenerationConfig.from_repo_config({})
    assert cfg.max_tokens == DEFAULT_MAX_TOKENS
    assert get("wiki_style").default == DEFAULT_STYLE
    assert get("embedder").default == DEFAULT_EMBEDDER
    assert set(generation.EMBEDDERS) == set(_BUILTIN_EMBEDDERS)
    assert resolve_reasoning(None, {}) == get("reasoning").default


@pytest.mark.parametrize("value", [0, -5, "x", True])
def test_max_tokens_fails_closed_like_the_generation_config(value):
    from repowise.core.generation.models import GenerationConfig

    with pytest.raises(ValueError):
        GenerationConfig.from_repo_config({"max_tokens": value})
    with pytest.raises(ValueError):
        resolve("max_tokens", {"max_tokens": value}, environ={})


def test_max_tokens_reader_accepts_a_quoted_number_the_registry_rejects():
    from repowise.core.generation.models import GenerationConfig

    assert GenerationConfig.from_repo_config({"max_tokens": "12"}).max_tokens == 12
    with pytest.raises(ValueError):
        resolve("max_tokens", {"max_tokens": "12"}, environ={})


def test_reasoning_env_beats_the_file_like_resolve_reasoning(monkeypatch):
    from repowise.core.reasoning import resolve_reasoning

    monkeypatch.setenv("REPOWISE_REASONING", "low")
    assert resolve_reasoning(None, {"reasoning": "high"}) == _registry(
        "reasoning", {"reasoning": "high"}, {"REPOWISE_REASONING": "low"}
    )


def test_embedder_env_beats_the_pin_like_the_resolver(tmp_path):
    from repowise.core.providers.embedding.resolve import resolve_embedder_name

    repo = _repo(tmp_path, {"embedder": "openai"})
    env = {"REPOWISE_EMBEDDER": "gemini"}
    name, _ = resolve_embedder_name(repo_path=repo, getenv=env.get, detect=False)
    assert name == _registry("embedder", {"embedder": "openai"}, env)


#: Top-level keys whose readers coerce or clamp where the registry checks.
GENERATION_DISAGREEMENTS = {
    "commit_limit": "init clamps 20000 to 10000; update and the registry do not clamp "
    "(update uses 20000, the registry rejects it and uses 5000)",
    "wiki_style": "a custom style lives in .repowise/styles/, so the registry cannot list "
    "the choices; an unknown name reads as comprehensive only in resolve_style",
}


def test_commit_limit_clamp_differs():
    assert _registry("commit_limit", {"commit_limit": 20000}) == 5000
    assert "commit_limit" in GENERATION_DISAGREEMENTS


def test_language_falls_back_like_the_reader():
    from repowise.core.generation.languages import SUPPORTED_LANGUAGES

    assert set(get("language").choices) == set(SUPPORTED_LANGUAGES)
    assert _registry("language", {"language": "xx"}) == "en"


# ---------------------------------------------------------------- providers


def test_base_url_providers_match_the_specs():
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    expected = tuple(name for name, spec in PROVIDER_SPECS.items() if spec.base_url_envs)
    assert expected == providers.BASE_URL_PROVIDERS


@pytest.mark.parametrize("raw", ["", "2", "x", "-3", "0"])
def test_cli_concurrency(monkeypatch, raw):
    from repowise.core.providers.llm._concurrency import DEFAULT_CONCURRENCY, resolve_concurrency

    name = "REPOWISE_CLAUDE_CLI_CONCURRENCY"
    assert get(name).default == DEFAULT_CONCURRENCY
    monkeypatch.setenv(name, raw)
    real = resolve_concurrency(name, "claude_cli")
    ours = _registry(name, None, {name: raw})
    # The reader clamps a number under 1 up to 1; the registry rejects it.
    assert (real == ours) is (raw not in ("-3", "0"))


# ---------------------------------------------------------------- editor, events, attribution


def test_editor_sections_default_matches():
    from repowise.core.generation.editor_files.sections import DEFAULT_SECTIONS

    assert set(editor.DEFAULT_SECTIONS) == set(DEFAULT_SECTIONS)
    assert get("editor_files.sections").default == sorted(DEFAULT_SECTIONS)


def test_editor_file_ids_match_the_agent_targets():
    from repowise.cli.agent_targets.targets.cursor import CursorTarget
    from repowise.cli.agent_targets.targets.kiro import KiroTarget
    from repowise.cli.agent_targets.targets.vscode import VSCodeTarget

    ids = {t.project_file_id for t in (CursorTarget, KiroTarget, VSCodeTarget)}
    registered = {s.key.split(".")[1] for s in settings_in("editor") if s.key.startswith("editor_files.")}
    assert ids | {"claude_md", "agents_md", "sections"} == registered


def test_event_kinds_and_defaults_match():
    from repowise.core.events.model import KINDS
    from repowise.core.events.store import _KEYS, EventSettings

    assert {(n, on) for n, on, _ in events.KINDS} == {(k.name, k.default_on) for k in KINDS.values()}
    base = EventSettings()
    assert get("events.enabled").default == base.enabled
    assert get("events.retention_days").default == base.retention_days
    assert get("events.compact_after_days").default == base.compact_after_days
    assert get("events.sinks").default == list(base.sinks)
    tops = {s.key.split(".")[1] for s in settings_in("events")}
    assert tops == set(_KEYS)


def test_attribution_defaults_match(tmp_path, monkeypatch):
    from repowise.core.attribution import CAPTURE_LEVELS, load_attribution_settings

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    assert tuple(get("attribution.capture").choices) == tuple(CAPTURE_LEVELS)
    real = load_attribution_settings(_repo(tmp_path, {}))
    assert real.enabled == get("attribution.enabled").default
    assert real.capture == get("attribution.capture").default
    assert list(real.agents) == get("attribution.agents").default
    bad = load_attribution_settings(_repo(tmp_path, {"attribution": {"enabled": "yes"}}))
    assert bad.enabled == get("attribution.enabled").fallback


# ---------------------------------------------------------------- health


def test_coverage_defaults_match_the_coverage_config():
    from repowise.core.analysis.health.coverage.discovery import CoverageConfig

    real = CoverageConfig.from_repo_config({})
    for s in settings_in("health"):
        if not s.key.startswith("coverage."):
            continue
        value = getattr(real, s.key.split(".")[1])
        assert (list(value) if isinstance(value, tuple) else value) == s.default, s.key


def test_refactoring_and_assertion_defaults_match():
    from repowise.core.analysis.health.config import HealthConfig
    from repowise.core.analysis.health.refactoring.llm.enrich import llm_enrichment_enabled

    real = HealthConfig._from_refactoring_block(None)
    assert real.refactoring_enabled == get("refactoring.enabled").default
    assert real.disabled_refactorings == get("refactoring.detectors.disabled").default
    assert real.refactoring_min_confidence == get("refactoring.min_confidence").default
    assert real.assert_extra_names == get("assertions.extra_names").default
    assert llm_enrichment_enabled({}) == get("refactoring.llm.enabled").default


def test_refactoring_llm_reads_a_quoted_false_as_on():
    """``bool("false")`` is true: the reader turns a quoted false on."""
    from repowise.core.analysis.health.refactoring.llm.enrich import llm_enrichment_enabled

    cfg = {"refactoring": {"llm": {"enabled": "false"}}}
    assert llm_enrichment_enabled(cfg) is True
    assert _registry("refactoring.llm.enabled", cfg) is False


def test_test_selection_and_outcome_defaults_match():
    from repowise.core.analysis.test_selection import TestSelectionConfig
    from repowise.core.changes.outcomes import DEFAULT_FIX_DAYS

    real = TestSelectionConfig.from_repo_config({})
    assert list(real.full_run_on) == get("tests.full_run_on").default
    assert list(real.always_run) == get("tests.always_run").default
    assert get("changes.outcome_fix_days").default == DEFAULT_FIX_DAYS


# ---------------------------------------------------------------- env readers


def _env_cases():
    from repowise.cli._helpers.paths import WORKTREE_FALLBACK_ENV
    from repowise.cli.editor_setup import is_editor_setup_disabled
    from repowise.core.pipeline.commit_health_drain import background_enabled
    from repowise.core.pipeline.incremental.feature import incremental_graph_enabled
    from repowise.server.mcp_server.tool_answer import config as answer

    def worktree_fallback() -> bool:
        import os

        return os.environ.get(WORKTREE_FALLBACK_ENV, "").strip().lower() not in FALSY_LIST

    cases = [
        ("REPOWISE_SKIP_EDITOR_SETUP", is_editor_setup_disabled),
        ("REPOWISE_COMMIT_HEALTH_BACKGROUND", background_enabled),
        ("REPOWISE_INCREMENTAL_GRAPH", incremental_graph_enabled),
        ("REPOWISE_WORKTREE_FALLBACK", worktree_fallback),
    ]
    for name, default, _ in __import__(
        "repowise.core.settings.areas.mcp", fromlist=["_ANSWER_SWITCHES"]
    )._ANSWER_SWITCHES:
        env = f"REPOWISE_ANSWER_{name}"
        reader = answer._flag_on if default else answer._opt_in
        cases.append((env, lambda env=env, reader=reader: reader(env)))
    return cases


FALSY_LIST = ("0", "false", "no", "off")


@pytest.mark.parametrize("raw", [None, "", "0", "1", "true", "OFF", "no", "maybe"])
def test_env_switches_read_like_their_readers(monkeypatch, raw):
    for name, reader in _env_cases():
        monkeypatch.delenv(name, raising=False)
        if raw is not None:
            monkeypatch.setenv(name, raw)
        environ = {} if raw is None else {name: raw}
        assert reader() == _registry(name, None, environ), (name, raw)
        monkeypatch.delenv(name, raising=False)


def test_resolved_sources_are_reported():
    r = resolve("REPOWISE_NO_HINTS", environ={"REPOWISE_NO_HINTS": "1"})
    assert r.source is Source.ENV
