"""LLM provider resolution, interactive fallbacks, and config validation."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

import click

from repowise.cli._helpers.config import save_config_partial
from repowise.cli.errors import reasoned_error

# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def resolve_provider(
    provider_name: str | None,
    model: str | None,
    repo_path: Path | None = None,
) -> Any:
    """Resolve a provider instance from CLI flags, the repo config, or env.

    The order is the shared one in :mod:`repowise.core.providers.llm.resolve`:
    ``--provider`` / ``--model`` > ``.repowise/config.yaml`` > the global
    default in ``provider_config.json`` > ``REPOWISE_PROVIDER`` /
    ``REPOWISE_MODEL`` > auto-detection from API keys.
    """
    from repowise.core.providers.llm.resolve import resolve_llm

    selection = resolve_llm(repo_path, provider=provider_name, model=model)
    if selection.provider is None:
        # Not fatal on every path: `init` catches this and renders a template
        # wiki instead, so the reason must not be recorded until it ends a
        # command.
        raise reasoned_error(_no_provider_message(), reason="no_provider_configured")
    return _build_provider(selection, _provider_kwargs_or_error(selection, repo_path))


def _provider_kwargs_or_error(selection: Any, repo_path: Path | None) -> dict[str, Any]:
    """Constructor kwargs, warning when a configured provider has no key anywhere."""
    from repowise.cli import helpers as _facade  # patch point for tests
    from repowise.core.providers.llm.resolve import llm_provider_kwargs

    try:
        kwargs = llm_provider_kwargs(selection, repo_path)
    except ValueError as exc:
        # A base URL the repo's config.yaml sets that is malformed, or that
        # nobody on this machine confirmed for a key from elsewhere.
        raise reasoned_error(str(exc), reason="provider_setup_failed") from exc
    if selection.provider_source != "autodetect" and "api_key" not in kwargs:
        # A configured provider whose key is nowhere still gets built: the
        # constructor says what it needs. Agent CLIs are checked for install.
        for warning in _facade.validate_provider_config(selection.provider, repo_path=repo_path):
            _facade.warn(warning)
    return kwargs


def _build_provider(selection: Any, kwargs: dict[str, Any]) -> Any:
    """Construct the provider, turning a setup failure into a config error."""
    from repowise.core.providers import get_provider

    try:
        return get_provider(selection.provider, **kwargs)
    except click.ClickException:
        raise
    except Exception as exc:
        # Everything this constructor reads is user input: env vars and
        # .repowise/config.yaml, base_url above all. A typo there is a
        # configuration error, not a repowise bug, but it used to surface as
        # a raw traceback that escaped every caller's handler.
        raise reasoned_error(
            f"Could not set up the {selection.provider} provider: {exc}. "
            f"Resolved {selection.describe()}. Check those settings.",
            reason="provider_setup_failed",
        ) from exc


def _no_provider_message() -> str:
    """The no-provider error, listing every env var that would configure one."""
    from repowise.core.providers.llm.registry import (
        PROVIDER_AUTODETECT_ORDER,
        provider_required_envs,
    )

    env_vars = [var for name in PROVIDER_AUTODETECT_ORDER for var in provider_required_envs(name)]
    return (
        "No provider configured. Use --provider, set provider in .repowise/config.yaml or "
        f"REPOWISE_PROVIDER, or set one of {' / '.join(env_vars)}. Use "
        "REPOWISE_PROVIDER=claude_cli to use an authenticated Claude Code subscription, "
        "REPOWISE_PROVIDER=codex_cli to use an authenticated Codex CLI subscription, or "
        "REPOWISE_PROVIDER=opencode to use opencode."
    )


def resolve_explicit_provider_or_prompt(
    provider_name: str | None,
    model: str | None,
    repo_path: Path,
    *,
    interactive: bool,
    save_key: bool = True,
) -> Any:
    """Resolve an explicit provider, onboarding missing credentials in a TTY.

    ``resolve_provider`` stays non-interactive because hooks, CI, and library
    callers must never block on stdin. ``init`` can opt into this wrapper when
    it has a real terminal: a missing explicit provider key gets the same
    inline setup as the provider picker, and an explicit OpenAI provider also
    gets the endpoint question used by local gateways.
    """
    from repowise.cli import helpers as _facade  # patch point for tests
    from repowise.cli.ui.provider_selection import interactive_provider_credentials

    if interactive and provider_name == "openai":
        # A pty can report TTY while stdin is not readable. Let the normal
        # resolution below produce the actionable provider error instead of
        # replacing it with Abort.
        with contextlib.suppress(EOFError, click.Abort):
            interactive_provider_credentials(
                _facade.console,
                provider_name,
                repo_path=repo_path,
                save_key=save_key,
            )

        return _facade.resolve_provider(provider_name, model, repo_path=repo_path)

    try:
        return _facade.resolve_provider(provider_name, model, repo_path=repo_path)
    except click.ClickException as original_error:
        if not interactive or provider_name is None:
            raise

        try:
            configured = interactive_provider_credentials(
                _facade.console,
                provider_name,
                repo_path=repo_path,
                save_key=save_key,
            )
        except (EOFError, click.Abort):
            # A pty can report TTY while stdin is not readable. Preserve the
            # actionable provider error instead of replacing it with Abort.
            raise original_error from None

        if not configured:
            raise original_error
        return _facade.resolve_provider(provider_name, model, repo_path=repo_path)


def resolve_provider_or_prompt(
    provider_name: str | None,
    model: str | None,
    repo_path: Path,
    *,
    reasoning: str | None = None,
    interactive: bool,
) -> Any:
    """Resolve a provider, falling back to init's interactive setup on a miss.

    Ordinary resolution is :func:`resolve_provider`. When that fails because no
    provider/key is configured and ``interactive`` is set (an interactive
    terminal, not a hook / CI / ``--progress json`` run), reuse init's exact
    provider + API-key prompt, persist the choice, and retry — so a docs run
    that only just asked for LLM pages onboards the same way ``init`` does
    instead of dying with "No provider configured".

    When ``interactive`` is False the original error propagates unchanged, so
    background runs (post-commit hook, CI, machine-driven ``--progress json``)
    keep their clean, non-blocking failure.

    Persistence reuses init's helpers: the key lands in ``.repowise/.env`` (from
    the prompt) and provider/model in ``.repowise/config.yaml`` here, so the
    prompt only ever appears once per repo.
    """
    from repowise.cli import helpers as _facade  # patch point for tests

    try:
        return _facade.resolve_provider(provider_name, model, repo_path=repo_path)
    except Exception as original_error:
        if not interactive:
            raise
        from repowise.cli.ui import interactive_provider_config_select

        # ``interactive`` rests on ``isatty()``, which lies: under Git Bash,
        # some pty wrappers, and ``docker run -t`` without -i it claims a
        # terminal it cannot read from, and agents drive us through exactly
        # those shapes. Treat the prompt as the probe (as init does): if stdin
        # cannot answer, click raises EOFError/Abort, so fall back to the clean,
        # actionable "No provider configured" error instead of a bare "Aborted!".
        try:
            selection = interactive_provider_config_select(
                _facade.console, model, reasoning, repo_path=repo_path
            )
        except (EOFError, click.Abort):
            raise original_error from None

        # Persist provider/model so future runs resolve without re-prompting.
        # The API key was already persisted to .repowise/.env by the prompt.
        save_config_partial(
            repo_path,
            provider=selection.provider_name,
            model=selection.model,
        )
        return _facade.resolve_provider(selection.provider_name, selection.model, repo_path=repo_path)


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


def validate_provider_config(
    provider_name: str | None = None, repo_path: Path | None = None
) -> list[str]:
    """Validate that required API keys/environment variables are set for the provider.

    Args:
        provider_name: The provider name to validate. If None, checks all possible providers.
        repo_path: The repo whose configured provider counts as explicitly requested.
            Its ``.repowise/.env`` keys count too; without it only the process
            environment is read.

    Returns:
        List of warning messages for missing or invalid configuration.
        Empty list means all required config is present.
    """
    from repowise.core.providers.llm.registry import provider_required_envs, repo_env_lookup
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    # The key init saves to .repowise/.env counts, as it does for resolution.
    getenv = repo_env_lookup(repo_path)
    # Required environment variables per provider, read from the registry that
    # also drives resolution, so a provider added there is validated here without
    # a second edit. The agent-CLI providers need no env var; the check for them
    # is whether their CLI is installed.
    required = {
        name: list(provider_required_envs(name))
        for name in PROVIDER_SPECS
        if provider_required_envs(name)
    }
    if provider_name:
        return _validate_one_provider(provider_name, required, getenv)
    return _validate_configured_providers(repo_path, required, getenv)


def _validate_one_provider(name: str, required: dict[str, list[str]], getenv: Any) -> list[str]:
    """Warnings for the provider a command names."""
    from repowise.core.agents.identity import identity_for_provider
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    agent = identity_for_provider(name)
    if agent is not None:
        if agent.is_installed():
            return []
        return [
            f"Provider '{name}' requires the {agent.display_name} CLI.\n"
            f"  Install:  {agent.install_hint}\n"
            f"  Setup:    {agent.login_hint}"
        ]
    if name not in PROVIDER_SPECS:
        return [f"Unknown provider '{name}' - cannot validate configuration"]
    missing = _missing_setting(name, required.get(name, []), getenv)
    return [missing] if missing else []


def _validate_configured_providers(
    repo_path: Path | None, required: dict[str, list[str]], getenv: Any
) -> list[str]:
    """Warnings for the configured provider, and any whose env var exists but is empty."""
    from repowise.core.providers.llm.resolve import resolve_llm

    configured = resolve_llm(repo_path, autodetect=False).provider
    warnings = []
    for name, env_vars in required.items():
        if configured != name and not any(var in os.environ for var in env_vars):
            continue
        missing = _missing_setting(name, env_vars, getenv)
        if missing:
            warnings.append(missing)
    return warnings


def _missing_setting(name: str, env_vars: list[str], getenv: Any) -> str | None:
    """The warning for *name* when none of *env_vars* is set, else ``None``.

    Any one of them satisfies it (GEMINI_API_KEY or GOOGLE_API_KEY), the same
    rule resolution applies.
    """
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    if not env_vars or any((getenv(var) or "").strip() for var in env_vars):
        return None
    needs = " or ".join(env_vars)
    default_url = PROVIDER_SPECS[name].default_base_url
    if not default_url:
        return f"Provider '{name}' requires {needs}"
    # The base URL is optional for a server with a default endpoint: the
    # question is whether anything answers there.
    from repowise.cli.ui.provider_selection import endpoint_answers

    if endpoint_answers(default_url):
        return None
    return f"Provider '{name}' is not answering at {default_url}: start it, or set {needs}"
