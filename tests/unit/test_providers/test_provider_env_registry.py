"""The shared per-provider environment mapping in the provider registry.

These are the maps the CLI and the MCP server both resolve through. They used
to be hand-copied into each caller, which is how the MCP server ended up
resolving a keyless agent-CLI provider without telling it which repo to run in
(issue #1119). The drift guards below fail when a provider is added to the
registry table but not wired into the mapping beside it.
"""

from __future__ import annotations

import pytest

from repowise.core.providers.llm.registry import (
    _BUILTIN_PROVIDERS,
    KEYLESS_PROVIDERS,
    PROVIDER_API_KEY_ENVS,
    PROVIDER_AUTODETECT_ORDER,
    PROVIDER_BASE_URL_ENVS,
    REPO_PATH_PROVIDERS,
    provider_credentials_present,
    provider_is_usable,
    provider_kwargs,
    provider_required_envs,
)

# --- drift guards ----------------------------------------------------------


def test_every_builtin_provider_declares_how_it_authenticates():
    """A provider either names a key env or is explicitly keyless. No silent third case."""
    undeclared = {
        name
        for name in _BUILTIN_PROVIDERS
        if name not in PROVIDER_API_KEY_ENVS and name not in KEYLESS_PROVIDERS
    }
    assert not undeclared, (
        f"providers {sorted(undeclared)} are in _BUILTIN_PROVIDERS but declare neither an "
        "API-key env var nor membership in KEYLESS_PROVIDERS; resolution cannot reason "
        "about them"
    )


@pytest.mark.parametrize(
    "mapping",
    [PROVIDER_API_KEY_ENVS, PROVIDER_BASE_URL_ENVS],
    ids=["api_key_envs", "base_url_envs"],
)
def test_env_maps_only_name_real_providers(mapping):
    unknown = set(mapping) - set(_BUILTIN_PROVIDERS)
    assert not unknown, f"env map names providers that do not exist: {sorted(unknown)}"


@pytest.mark.parametrize(
    "collection",
    [KEYLESS_PROVIDERS, REPO_PATH_PROVIDERS, set(PROVIDER_AUTODETECT_ORDER)],
    ids=["keyless", "repo_path", "autodetect"],
)
def test_provider_sets_only_name_real_providers(collection):
    unknown = set(collection) - set(_BUILTIN_PROVIDERS)
    assert not unknown, f"names providers that do not exist: {sorted(unknown)}"


def test_autodetect_never_guesses_a_keyless_provider():
    """Auto-detect picks a provider from credentials the user set.

    A keyless provider is usable in every environment, so including one here
    would hijack every repo that never asked for it.
    """
    for name in PROVIDER_AUTODETECT_ORDER:
        assert provider_required_envs(name), (
            f"{name} is in the auto-detect order but needs no env var, so it would "
            "match unconditionally"
        )


def test_autodetect_order_has_no_duplicates():
    assert len(PROVIDER_AUTODETECT_ORDER) == len(set(PROVIDER_AUTODETECT_ORDER))


def test_every_provider_that_can_be_auto_detected_is_in_the_order():
    """A keyed provider left out of the tuple is silently unreachable.

    That was the openrouter bug: the MCP server carried a key mapping for it
    but never listed it among the candidates, so an OPENROUTER_API_KEY-only
    environment got no synthesis at all.
    """
    detectable = {
        name
        for name in _BUILTIN_PROVIDERS
        if provider_required_envs(name) and name not in KEYLESS_PROVIDERS
    }
    assert detectable - set(PROVIDER_AUTODETECT_ORDER) == set(), (
        "these providers have credentials we could detect but are missing from "
        "PROVIDER_AUTODETECT_ORDER, so auto-detection will never pick them"
    )


# --- the copies that survive elsewhere -------------------------------------
#
# Two catalogs still list env vars of their own because they carry display
# metadata this table has no business holding (labels, model lists, signup
# URLs, CLI sentinels). They are pinned to the registry here rather than left
# to drift: a mismatch fails, and moving one into the registry is a deletion.


def test_server_provider_catalog_agrees_with_the_registry():
    from repowise.core.providers.llm.registry import _BUILTIN_PROVIDERS
    from repowise.server.provider_config import PROVIDER_CATALOG

    catalog_ids = {entry["id"] for entry in PROVIDER_CATALOG}
    # Every provider the CLI can resolve must appear in the server catalog so
    # the web dashboard's provider picker never silently disagrees with what
    # the CLI can resolve. `mock` is the one deliberate exception — it is a
    # test/fallback provider, not something a user should pick in the UI.
    missing = set(_BUILTIN_PROVIDERS) - catalog_ids - {"mock"}
    assert not missing, (
        f"server/provider_config.py is missing catalog entries for: {sorted(missing)}"
    )

    mismatched = {
        entry["id"]: (entry["env_keys"], list(PROVIDER_API_KEY_ENVS[entry["id"]]))
        for entry in PROVIDER_CATALOG
        if entry["id"] in PROVIDER_API_KEY_ENVS
        and entry["env_keys"] != list(PROVIDER_API_KEY_ENVS[entry["id"]])
    }
    assert not mismatched, f"server/provider_config.py disagrees with the registry: {mismatched}"


def test_init_picker_env_vars_agree_with_the_registry():
    from repowise.cli.ui.provider_selection import _PROVIDER_ENV

    mismatched = {
        name: (env_var, PROVIDER_API_KEY_ENVS[name][0])
        for name, env_var in _PROVIDER_ENV.items()
        if name in PROVIDER_API_KEY_ENVS and env_var != PROVIDER_API_KEY_ENVS[name][0]
    }
    assert not mismatched, f"cli/ui/provider_selection.py disagrees with the registry: {mismatched}"


# --- provider_required_envs ------------------------------------------------


def test_required_envs_is_the_api_key_for_keyed_providers():
    assert provider_required_envs("anthropic") == ("ANTHROPIC_API_KEY",)
    assert provider_required_envs("gemini") == ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def test_required_envs_is_the_endpoint_for_ollama():
    """ollama takes no key, but it does need somewhere to send the request."""
    assert provider_required_envs("ollama") == ("OLLAMA_BASE_URL",)


def test_required_envs_is_empty_for_the_agent_clis():
    assert provider_required_envs("codex_cli") == ()
    assert provider_required_envs("opencode") == ()


# --- provider_is_usable vs provider_credentials_present --------------------


def test_keyless_provider_is_usable_with_an_empty_environment():
    """The two questions differ, and this is the case that separates them."""
    empty = {}.get
    assert provider_is_usable("codex_cli", empty) is True
    assert provider_credentials_present("codex_cli", empty) is False


def test_keyed_provider_needs_its_key_to_be_usable():
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    assert provider_is_usable("anthropic", env.get) is True
    assert provider_is_usable("anthropic", {}.get) is False


def test_either_gemini_key_satisfies_the_requirement():
    assert provider_credentials_present("gemini", {"GOOGLE_API_KEY": "g"}.get) is True
    assert provider_credentials_present("gemini", {"GEMINI_API_KEY": "g"}.get) is True


def test_blank_env_var_counts_as_unset():
    """CI matrices declare empty vars routinely; an empty key authenticates nothing."""
    for blank in ("", "   ", "\t"):
        assert provider_credentials_present("openai", {"OPENAI_API_KEY": blank}.get) is False
        assert provider_is_usable("openai", {"OPENAI_API_KEY": blank}.get) is False


def test_unknown_provider_is_not_vetoed():
    """A runtime-registered provider has requirements we cannot know."""
    assert provider_is_usable("some_custom_provider", {}.get) is True


# --- provider_kwargs -------------------------------------------------------


def test_kwargs_pull_key_and_base_url_from_the_injected_lookup():
    env = {"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "https://proxy.internal/v1"}
    kwargs = provider_kwargs("openai", model="gpt-5.4-nano", getenv=env.get)
    assert kwargs == {
        "model": "gpt-5.4-nano",
        "api_key": "sk-test",
        "base_url": "https://proxy.internal/v1",
    }


def test_kwargs_omit_absent_values_rather_than_passing_none():
    assert provider_kwargs("openai", getenv={}.get) == {}


def test_kwargs_strip_surrounding_whitespace_and_drop_blanks():
    env = {"OPENAI_API_KEY": "  sk-test  ", "OPENAI_BASE_URL": "   "}
    assert provider_kwargs("openai", getenv=env.get) == {"api_key": "sk-test"}


def test_gemini_falls_back_to_google_api_key():
    kwargs = provider_kwargs("gemini", getenv={"GOOGLE_API_KEY": "g-key"}.get)
    assert kwargs["api_key"] == "g-key"


def test_litellm_prefers_base_url_over_api_base():
    env = {"LITELLM_BASE_URL": "first", "LITELLM_API_BASE": "second"}
    assert provider_kwargs("litellm", getenv=env.get)["base_url"] == "first"


def test_agent_cli_providers_are_told_which_repo_to_run_in():
    """#1119: without this the CLI runs against the host process cwd."""
    for name in ("codex_cli", "opencode"):
        kwargs = provider_kwargs(name, repo_path="/repo", getenv={}.get)
        assert kwargs["repo_path"] == "/repo"


def test_repo_path_is_not_forwarded_to_providers_that_reject_it():
    """An HTTP provider's constructor has no repo_path parameter."""
    kwargs = provider_kwargs("anthropic", repo_path="/repo", getenv={"ANTHROPIC_API_KEY": "k"}.get)
    assert "repo_path" not in kwargs
