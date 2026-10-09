"""Interactive provider selection (+ inline API key entry + save)."""

from __future__ import annotations

import functools
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.errors import reasoned_error
from repowise.cli.ui.brand import BRAND, BRAND_STYLE, OK, VALUE, WARN
from repowise.cli.ui.env_persistence import _save_key_to_dotenv
from repowise.cli.ui.openai_compatible import (
    discover_models as _discover_openai_compatible_models,
)
from repowise.cli.ui.openai_compatible import (
    persist_setup as _persist_openai_compatible_setup,
)
from repowise.cli.ui.openai_compatible import (
    prompt_setup as _prompt_openai_compatible_setup_values,
)
from repowise.core.agents.identity import get_identity, identity_for_provider
from repowise.core.providers.llm.base import ProviderModelOption
from repowise.core.providers.llm.specs import PROVIDER_SPECS, ProviderSpec
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

# ---------------------------------------------------------------------------
# Provider metadata, read from the provider specs. Flag-only providers (mock)
# are not offered; rows follow ``picker_rank`` (gemini first = default).
# ---------------------------------------------------------------------------

_PICKER_SPECS: dict[str, ProviderSpec] = {
    spec.name: spec
    for spec in sorted(PROVIDER_SPECS.values(), key=lambda s: s.picker_rank or 0)
    if spec.picker_rank is not None
}
_PROVIDER_DEFAULTS: dict[str, str] = {
    name: spec.default_model for name, spec in _PICKER_SPECS.items()
}
# The env var a key prompt writes. Agent CLIs have none; the picker never
# prompts for them.
_PROVIDER_ENV: dict[str, str] = {
    name: spec.required_envs[0] for name, spec in _PICKER_SPECS.items() if spec.required_envs
}

_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
# The OpenAI adapter is also the generic adapter for local gateways. Keep the
# official endpoint as the prompt default, while letting a user replace it
# inline with a vLLM/SGLang/9router URL without exporting another env var first.
_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_OPENAI_COMPATIBLE_CHOICE = "openai_compatible"
_PROVIDER_CHOICES = tuple(
    choice
    for provider in _PICKER_SPECS
    for choice in ((provider, _OPENAI_COMPATIBLE_CHOICE) if provider == "openai" else (provider,))
)
# Enough for a loopback connect; the table renders before any prompt, so a slow
# or firewalled endpoint must not hold the whole screen.
_OLLAMA_PROBE_TIMEOUT_S = 0.3


@dataclass(frozen=True)
class ProviderSelection:
    """Interactive provider/model/reasoning selection result."""

    provider_name: str
    model: str
    reasoning: ReasoningMode = "auto"


def _agent_cli_status(name: str) -> tuple[bool, bool]:
    """``(installed, ready)`` for the agent CLI behind provider *name*.

    Ready means signed in where the CLI can say so cheaply, else installed:
    an unauthenticated CLI without such a query surfaces on first use.
    """
    agent = identity_for_provider(name)
    if agent is None or not agent.is_installed():
        return False, False
    return True, agent.is_logged_in()


def agent_providers_set_up(repo_path: Path | None) -> tuple[str, ...]:
    """Agent-CLI providers whose agent is set up as a target here, in picker order.

    The picker defaults to the first of these whose CLI is ready. Empty when
    config.yaml already names a provider, so a re-init keeps today's default.
    """
    from repowise.cli.agent_targets.registry import get_target
    from repowise.cli.helpers import load_config

    if repo_path is not None and load_config(repo_path).get("provider"):
        return ()
    names: list[str] = []
    for name, spec in _PICKER_SPECS.items():
        agent = get_identity(spec.agent) if spec.agent else None
        target = get_target(agent.cli_target_id) if agent else None
        if target is None:
            continue
        try:
            if target.is_present(repo_path) or target.detect(repo_path):
                names.append(name)
        except Exception:
            continue
    return tuple(names)


def ollama_base_url() -> str:
    """Where the picker expects to find Ollama."""
    return os.environ.get("OLLAMA_BASE_URL") or _OLLAMA_DEFAULT_BASE_URL


def _ollama_endpoint() -> tuple[str, int] | None:
    """``(host, port)`` for the configured Ollama URL, or ``None`` if unusable.

    A bare ``host:port`` is accepted, since that is a natural thing to put in
    ``OLLAMA_BASE_URL`` and urlparse would otherwise read the host as a scheme.
    Anything with no host left after that (``unix://…``, plain junk with a
    scheme) has no TCP endpoint to probe, and must not silently fall back to
    localhost: that reports someone's typo'd remote box as ready and defers the
    failure to the first generation call.
    """
    raw = ollama_base_url().strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlparse(raw)
        host = parsed.hostname
        # A property, and it raises rather than returning None for a
        # non-numeric or out-of-range port.
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    return host, port or (443 if parsed.scheme == "https" else 11434)


def _detect_ollama_status() -> bool:
    """Return ``True`` if something is listening at the Ollama endpoint.

    Ollama takes no API key, so "is the env var set" was never the question:
    a user with it running locally and no ``OLLAMA_BASE_URL`` was shown as
    unavailable and then asked to paste a key that does not exist. A TCP
    connect is the cheapest honest answer.
    """
    endpoint = _ollama_endpoint()
    if endpoint is None:
        return False
    try:
        # gaierror and timeout are both OSError subclasses, so a bad host or a
        # black-holed address ends up here rather than escaping.
        with socket.create_connection(endpoint, timeout=_OLLAMA_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _detect_provider_status() -> dict[str, str]:
    """Return {provider: what makes it ready} for providers that can be used now.

    The value is the env var carrying the key for the hosted providers, and a
    short description of the out-of-band credential for the local ones. Only
    truthiness is load-bearing; the value is for debugging.
    """
    status: dict[str, str] = {}
    openai_base_url = (os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    openai_has_key = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    for prov, spec in _PICKER_SPECS.items():
        env_var = _PROVIDER_ENV.get(prov, "")
        if spec.agent:
            if _agent_cli_status(prov)[1]:
                status[prov] = "agent CLI"
        elif prov == "ollama":
            if _detect_ollama_status():
                status[prov] = ollama_base_url()
        elif prov == "openai":
            if openai_has_key and openai_base_url in ("", _OPENAI_DEFAULT_BASE_URL):
                status[prov] = env_var
            elif openai_has_key and openai_base_url:
                status[_OPENAI_COMPATIBLE_CHOICE] = env_var
        elif any(os.environ.get(name) for name in spec.api_key_envs):
            status[prov] = env_var
    return status


def _agent_cli_setup_lines(name: str) -> list[str]:
    agent = identity_for_provider(name)
    assert agent is not None, name
    installed, _ = _agent_cli_status(name)
    problem = (
        f"{agent.display_name} CLI is on PATH but not logged in."
        if installed
        else f"{agent.display_name} CLI not found on PATH."
    )
    lines = [
        f"  [bold]{name}[/bold] uses the {agent.display_name} CLI's own login. No API key here.",
        f"  Install: [{BRAND}]{agent.install_hint}[/]",
        f"  Set up:  [{BRAND}]{agent.login_hint}[/]",
    ]
    models = PROVIDER_SPECS[name].models
    if len(models) > 1:
        lines += [
            "",
            f"  To pick a specific model: [{BRAND}]repowise init --provider {name} "
            f"--model {models[1]}[/]",
        ]
    return [*lines, "", f"  [{WARN}]{problem}[/] Set it up and retry, or select another provider."]


def _ollama_setup_lines() -> list[str]:
    base_url = ollama_base_url()
    lines = ["  [bold]ollama[/bold] runs models on your machine. No key needed.", ""]
    if _ollama_endpoint() is None:
        # The URL never resolved to a host and port, so "nothing is listening"
        # would name the wrong problem.
        lines.append(
            f"  [{WARN}]OLLAMA_BASE_URL is set to {base_url!r}, which is not a "
            f"host repowise can reach.[/] Expected something like "
            f"[{BRAND}]http://localhost:11434[/]."
        )
        return lines
    lines.append(
        f"  [{WARN}]Nothing is listening at {base_url}.[/] Start it with "
        f"[{BRAND}]ollama serve[/], pull a model with [{BRAND}]ollama pull qwen3.5:4b[/], "
        "then retry."
    )
    lines.append("  [dim]Set OLLAMA_BASE_URL if it listens somewhere else.[/dim]")
    return lines


# Providers with no API key to paste: they authenticate out of band or run
# locally, so readiness is a probe and the remedy is a command, never a prompt.
# Narrower than ``KEYLESS_PROVIDERS``: litellm takes a key here and mock is not
# offered interactively.
_LOCAL_PROVIDER_SETUP: dict[str, Callable[[], list[str]]] = {
    **{
        name: functools.partial(_agent_cli_setup_lines, name)
        for name, spec in _PICKER_SPECS.items()
        if spec.agent
    },
    "ollama": _ollama_setup_lines,
}


def _interactive_provider_name(
    console: Console,
    model_flag: str | None,
    *,
    repo_path: Path | None = None,
    save_key: bool = True,
    prefer: tuple[str, ...] = (),
) -> str:
    """Show provider table, handle selection + inline key entry + save.

    The first ready provider in *prefer* (see :func:`agent_providers_set_up`)
    becomes the default. Returns the chosen provider name.
    """
    providers = list(_PROVIDER_CHOICES)  # gemini first
    detected = _detect_provider_status()

    # --- provider table ---
    table = Table(
        show_header=True,
        box=None,
        padding=(0, 2),
        title="[bold]Provider Setup[/bold]",
        title_style="",
    )
    table.add_column("#", style=BRAND_STYLE, width=4)
    table.add_column("Provider", style="bold", min_width=12)
    table.add_column("Status", min_width=16)
    table.add_column("Default Model", style="dim")

    # One axis, two states. The remedy is provider-specific and lives in the
    # dim note beside the name, so the Status column can be read at a glance
    # instead of decoded from three phrasings and two colours.
    for idx, prov in enumerate(providers, 1):
        runtime_provider = "openai" if prov == _OPENAI_COMPATIBLE_CHOICE else prov
        ready = prov in detected
        status_text = f"[{OK}]✓ ready[/]" if ready else "[dim]✗ not set up[/dim]"
        if prov == _OPENAI_COMPATIBLE_CHOICE:
            label = "OpenAI-compatible [dim](Custom / local gateway)[/dim]"
            default_model = "discover from /models"
        else:
            note = _PICKER_SPECS[prov].note
            label = f"{prov} [dim]({note})[/dim]" if note else prov
            default_model = _PROVIDER_DEFAULTS.get(runtime_provider, "")
        table.add_row(f"[{idx}]", label, status_text, default_model)

    console.print()
    console.print(table)
    console.print(
        "  [dim]mock is flag-only (writes placeholder pages): repowise init --provider mock[/dim]"
    )
    console.print()

    # --- selection ---
    valid_choices = [str(i) for i in range(1, len(providers) + 1)]
    # Default: first detected provider, or gemini (index 1)
    default_idx = "1"
    for idx, prov in enumerate(providers, 1):
        if prov in detected:
            default_idx = str(idx)
            break
    # The CLI of the agent set up here outranks a key: it is the user's own login.
    recommended = next((name for name in prefer if name in detected), None)
    if recommended is not None:
        default_idx = str(providers.index(recommended) + 1)
        agent = identity_for_provider(recommended)
        assert agent is not None, recommended
        console.print(
            f"  [dim]Default: {recommended}. {agent.display_name} is set up here, "
            f"so indexing {_PICKER_SPECS[recommended].note}.[/dim]"
        )
        console.print()

    chosen_idx = Prompt.ask(
        "  Select provider",
        choices=valid_choices,
        default=default_idx,
        console=console,
    )
    chosen = providers[int(chosen_idx) - 1]

    if chosen == _OPENAI_COMPATIBLE_CHOICE:
        return chosen
    if chosen == "openai":
        # The official and custom rows intentionally share the core adapter.
        # Pin the official row so a previously saved custom endpoint cannot
        # silently redirect an OpenAI selection back to the local gateway.
        os.environ["OPENAI_BASE_URL"] = _OPENAI_DEFAULT_BASE_URL

    # --- inline API key entry if missing ---
    has_official_openai_key = chosen == "openai" and bool(
        (os.environ.get("OPENAI_API_KEY") or "").strip()
    )
    if chosen not in detected and not has_official_openai_key:
        setup_lines = _LOCAL_PROVIDER_SETUP.get(chosen)
        if setup_lines is not None:
            # Nothing to paste: these authenticate out of band or run locally,
            # so the fix is a command, not a key prompt.
            console.print()
            for line in setup_lines():
                console.print(line)
            return _interactive_provider_name(
                console,
                model_flag,
                repo_path=repo_path,
                save_key=save_key,
                prefer=prefer,
            )
        env_var = _PROVIDER_ENV[chosen]
        signup_url = _PICKER_SPECS[chosen].signup_url
        console.print()
        console.print(f"  [bold]{chosen}[/bold] requires [{VALUE}]{env_var}[/].")
        if signup_url:
            console.print(f"  Get your API key here: [{BRAND}]{signup_url}[/]")
        console.print()
        key = _prompt_api_key(
            console,
            chosen,
            env_var,
            repo_path=repo_path,
            save_key=save_key,
        )
        if not key:
            console.print(f"  [{WARN}]Skipped. Please select another provider.[/]")
            return _interactive_provider_name(
                console,
                model_flag,
                repo_path=repo_path,
                save_key=save_key,
                prefer=prefer,
            )

    if chosen == "openai" and repo_path is not None:
        _save_key_to_dotenv(repo_path, "OPENAI_BASE_URL", _OPENAI_DEFAULT_BASE_URL)
    return chosen


def _fallback_model_option(provider_name: str) -> ProviderModelOption:
    default_model = _PROVIDER_DEFAULTS.get(provider_name, "")
    return ProviderModelOption(
        model=default_model,
        label=default_model,
        reasoning_modes=("auto",),
        recommended=True,
        source="fallback",
    )


def _provider_model_options(
    console: Console,
    provider_name: str,
    *,
    model: str,
    repo_path: Path | None,
) -> tuple[ProviderModelOption, ...]:
    try:
        from repowise.cli.helpers import resolve_provider

        provider = resolve_provider(provider_name, model, repo_path)
        return provider.available_model_options()
    except Exception as exc:
        console.print(
            f"  [dim]Model list unavailable for {provider_name}: {exc}. "
            "Using built-in defaults.[/dim]"
        )
        return (_fallback_model_option(provider_name),)


def _provider_supported_reasoning_modes(
    provider_name: str,
    model: str,
    repo_path: Path | None,
) -> tuple[ReasoningMode, ...]:
    try:
        from repowise.cli.helpers import resolve_provider

        provider = resolve_provider(provider_name, model, repo_path)
        return provider.supported_reasoning_modes()
    except Exception:
        return ("auto",)


def _format_reasoning_modes(modes: tuple[ReasoningMode, ...]) -> str:
    modes = tuple(dict.fromkeys(modes or ("auto",)))
    return ", ".join(modes)


_MAX_MODEL_ROWS = 40


def _initial_model_options(
    options: tuple[ProviderModelOption, ...],
) -> list[ProviderModelOption]:
    if len(options) <= _MAX_MODEL_ROWS:
        return list(options)
    recommended = [option for option in options if option.recommended]
    return (recommended or list(options))[:_MAX_MODEL_ROWS]


def _search_model_options(
    options: tuple[ProviderModelOption, ...],
    query: str,
) -> list[ProviderModelOption]:
    q = query.lower()
    return [
        option
        for option in options
        if q in option.model.lower()
        or (option.label and q in option.label.lower())
        or (option.notes and q in option.notes.lower())
    ]


def _select_model_option(
    console: Console,
    provider_name: str,
    options: tuple[ProviderModelOption, ...],
    *,
    default_model: str,
    repo_path: Path | None,
) -> ProviderModelOption:
    console.print(
        f"  [dim]Smaller is fine. repowise is tuned for the {provider_name} budget tier; "
        "bigger models do not produce better docs.[/dim]"
    )

    display_options = _initial_model_options(options)
    if not display_options:
        display_options = [_fallback_model_option(provider_name)]
    searchable = len(options) > len(display_options)
    if searchable:
        console.print(
            f"  [dim]Showing {len(display_options)} recommended of "
            f"{len(options):,} available models.[/dim]"
        )

    while True:
        table = Table(
            show_header=True,
            box=None,
            padding=(0, 2),
            title="[bold]Model Options[/bold]",
            title_style="",
        )
        table.add_column("#", style=BRAND_STYLE, width=4)
        table.add_column("Model", style="bold", min_width=22)
        table.add_column("Reasoning", style="dim")
        # No Source column: its values were internal enums (``fallback``,
        # ``manual``) a first-time user cannot act on, and the one case that
        # matters — a built-in list because the provider's list was
        # unreachable — is already stated in full above the table.
        table.add_column("Notes", style="dim")

        default_idx = "1"
        for idx, option in enumerate(display_options, 1):
            if option.model == default_model or option.recommended:
                default_idx = str(idx)
                break

        for idx, option in enumerate(display_options, 1):
            label = option.label or option.model
            if option.recommended:
                label = f"{label} [dim](recommended)[/dim]"
            table.add_row(
                f"[{idx}]",
                label,
                _format_reasoning_modes(option.reasoning_modes),
                option.notes,
            )

        custom_idx = str(len(display_options) + 1)
        table.add_row(f"[{custom_idx}]", "Custom model", "auto", "type an exact model id")

        search_idx = None
        if searchable:
            search_idx = str(len(display_options) + 2)
            table.add_row(
                f"[{search_idx}]",
                f"Search all {len(options):,} models",
                "",
                "filter the full list by name",
            )

        console.print()
        console.print(table)
        console.print()

        max_choice = len(display_options) + (2 if searchable else 1)
        choice = Prompt.ask(
            "  Select model",
            choices=[str(i) for i in range(1, max_choice + 1)],
            default=default_idx,
            console=console,
        )
        if search_idx and choice == search_idx:
            query = Prompt.ask(
                "  Search (e.g. 'mini' or 'nano')",
                default="",
                show_default=False,
                console=console,
            ).strip()
            if not query:
                continue
            matches = _search_model_options(options, query)
            if not matches:
                console.print(f"  [{WARN}]No models matched {query!r}.[/]")
                continue
            if len(matches) > _MAX_MODEL_ROWS:
                console.print(
                    f"  [dim]{len(matches)} matches; showing first "
                    f"{_MAX_MODEL_ROWS}. Refine the search to see others.[/dim]"
                )
                matches = matches[:_MAX_MODEL_ROWS]
            else:
                console.print(f"  [dim]{len(matches)} match(es) for {query!r}.[/dim]")
            display_options = matches
            continue
        if choice == custom_idx:
            model = Prompt.ask("  Model", default=default_model, console=console)
            return ProviderModelOption(
                model=model,
                label=model,
                reasoning_modes=_provider_supported_reasoning_modes(
                    provider_name,
                    model,
                    repo_path,
                ),
                source="fallback",
            )

        return display_options[int(choice) - 1]


def _select_reasoning_mode(
    console: Console,
    selected: ProviderModelOption,
    reasoning_flag: str | None,
) -> ReasoningMode:
    choices = tuple(dict.fromkeys(selected.reasoning_modes or ("auto",)))
    if "auto" not in choices:
        choices = ("auto", *choices)

    if reasoning_flag:
        try:
            requested = normalize_reasoning(reasoning_flag)
        except ValueError as exc:
            raise reasoned_error(str(exc), reason="invalid_reasoning") from exc
        if requested not in choices:
            supported = ", ".join(choices)
            raise reasoned_error(
                f"reasoning={requested!r} is not supported by model "
                f"{selected.model!r}. Supported reasoning modes: {supported}.",
                reason="unsupported_reasoning",
            )
        return requested

    if choices == ("auto",):
        return "auto"

    # The one knob on this screen that multiplies token spend, so it gets the
    # same one-line explanation every other prompt in the flow has.
    console.print(
        "  [dim]How hard the model thinks per page. Higher costs more tokens; "
        "auto lets repowise pick per page type.[/dim]"
    )
    return click.prompt(
        "  Reasoning effort",
        default="auto",
        type=click.Choice(choices),
    )


def interactive_provider_config_select(
    console: Console,
    model_flag: str | None,
    reasoning_flag: str | None = None,
    *,
    repo_path: Path | None = None,
    save_key: bool = True,
    prefer: tuple[str, ...] = (),
) -> ProviderSelection:
    """Show provider/model/reasoning selection for interactive init.

    Returns a :class:`ProviderSelection` carrying the chosen provider name,
    model, and reasoning mode.
    """
    chosen = _interactive_provider_name(
        console,
        model_flag,
        repo_path=repo_path,
        save_key=save_key,
        prefer=prefer,
    )
    if chosen == _OPENAI_COMPATIBLE_CHOICE:
        return _interactive_openai_compatible_select(
            console,
            model_flag,
            reasoning_flag,
            repo_path=repo_path,
            save_key=save_key,
        )
    default_model = _PROVIDER_DEFAULTS.get(chosen, "")

    if model_flag:
        selected = ProviderModelOption(
            model=model_flag,
            label=model_flag,
            reasoning_modes=_provider_supported_reasoning_modes(
                chosen,
                model_flag,
                repo_path,
            ),
            source="fallback",
        )
    else:
        options = _provider_model_options(
            console,
            chosen,
            model=default_model,
            repo_path=repo_path,
        )
        selected = _select_model_option(
            console,
            chosen,
            options,
            default_model=default_model,
            repo_path=repo_path,
        )

    if not model_flag and _is_flagship_model(selected.model):
        console.print(
            f"  [{WARN}]Note:[/] [dim]'{selected.model}' works, but flash-lite / haiku / "
            "nano produce equivalent docs at ~10x lower cost on most repos.[/]"
        )

    reasoning = _select_reasoning_mode(console, selected, reasoning_flag)
    return ProviderSelection(chosen, selected.model, reasoning)


def interactive_provider_select(
    console: Console,
    model_flag: str | None,
    *,
    repo_path: Path | None = None,
) -> tuple[str, str]:
    """Show provider table, handle selection + inline key entry + save.

    Returns ``(provider_name, model_name)``.
    """
    selection = interactive_provider_config_select(
        console,
        model_flag,
        reasoning_flag="auto",
        repo_path=repo_path,
    )
    return selection.provider_name, selection.model


# Checked before the flagship tokens: a cheap-tier marker wins even when the
# family name is a flagship one, so `gpt-5.4-nano` is not mistaken for `gpt-5`.
_BUDGET_MODEL_TOKENS = (
    "nano",
    # OpenAI's 5.6 budget tier carries no cheap-tier word in its name, and
    # `gpt-5` matches it as flagship. Without this, accepting the default
    # printed a note advising the user to switch to a cheaper model than the
    # one they were already on.
    "luna",
    "mini",
    "lite",
    "haiku",
    "small",
    "tiny",
    "flash",
    r"\d+b",  # 8b, 3b, 270m-style parameter-count tags
)

_FLAGSHIP_MODEL_TOKENS = (
    "opus",
    "gpt-4o",
    "gpt-5",
    "pro",
    "sonnet",
    "ultra",
    "o1",
    "o3",
    "o4",
)


def _matches_token(model: str, tokens: tuple[str, ...]) -> bool:
    """True if any token appears in ``model`` as a whole dash/dot-delimited word.

    Substring matching is wrong here: ``gemini`` contains ``mini`` and
    ``gpt-5.4-nano`` contains ``gpt-5``.
    """
    return any(re.search(rf"(?<![a-z0-9]){tok}(?![a-z])", model) for tok in tokens)


def _is_flagship_model(model: str) -> bool:
    """Heuristic: True if the model name suggests a flagship-tier model."""
    if not model:
        return False
    m = model.lower().rsplit("/", 1)[-1]
    if _matches_token(m, _BUDGET_MODEL_TOKENS):
        return False
    return _matches_token(m, _FLAGSHIP_MODEL_TOKENS)


def _prompt_api_key(
    console: Console,
    provider: str,
    env_var: str,
    *,
    repo_path: Path | None = None,
    save_key: bool = True,
) -> str | None:
    """Prompt for an API key, set env var, and optionally save to .repowise/.env.

    Returns the key, or ``None`` if the user pressed Enter without typing.
    """
    key = click.prompt(
        "  Paste your API key (hidden)",
        default="",
        hide_input=True,
        show_default=False,
    )
    key = key.strip()
    if not key:
        return None

    os.environ[env_var] = key
    console.print(f"  [{OK}]✓ Key set for this session[/]")

    # Offer to save for future runs
    if repo_path is not None and save_key:
        save = click.confirm(
            "  Save this key to .repowise/.env so future runs find it? "
            "(the file is git-ignored, so it will not be committed)",
            default=True,
        )
        if save:
            _save_key_to_dotenv(repo_path, env_var, key)
            console.print(f"  [{OK}]✓ Saved to .repowise/.env[/]")
        else:
            # A declined key must stay declined. The run puts the key in the
            # environment (above) so indexing can use it, and init now mirrors
            # the environment's key into .repowise/.env when it writes config,
            # which would quietly overrule this exact answer. Record the "no"
            # where that later step will see it.
            from repowise.cli.helpers import NO_SAVE_KEY_ENV

            os.environ[NO_SAVE_KEY_ENV] = "1"
    elif repo_path is not None:
        # Keep --no-save-key visible to the endpoint prompt below too. The
        # final init persistence pass already honors this flag, but the
        # interactive prompt writes values immediately.
        from repowise.cli.helpers import NO_SAVE_KEY_ENV

        os.environ[NO_SAVE_KEY_ENV] = "1"
    console.print()

    return key


def _prompt_openai_base_url(
    console: Console,
    *,
    repo_path: Path | None = None,
) -> str | None:
    """Prompt for an optional OpenAI-compatible endpoint and persist it.

    This is deliberately a separate question from the API key: the same
    ``openai`` provider can target api.openai.com, a local gateway, or a
    self-hosted server. An existing env value wins, so re-running init never
    asks a question the user already answered.
    """
    env_var = "OPENAI_BASE_URL"
    current = (os.environ.get(env_var) or "").strip()
    if current:
        return current

    label = "  Base URL (OpenAI-compatible endpoint)"
    value = click.prompt(label, default=_OPENAI_DEFAULT_BASE_URL, show_default=True)
    value = value.strip()
    if not value:
        return None

    os.environ[env_var] = value
    if repo_path is not None:
        # --no-save-key governs secrets, not the endpoint needed to recreate
        # this provider. Base URLs are non-secret and remain repo-local.
        _save_key_to_dotenv(repo_path, env_var, value)
        console.print(f"  [{OK}]✓ Saved {env_var} to .repowise/.env[/]")
    console.print()
    return value


def _prompt_exact_model(console: Console, *, default: str = "") -> ProviderModelOption:
    while True:
        model = Prompt.ask(
            "  Exact model id",
            default=default,
            show_default=bool(default),
            console=console,
        ).strip()
        if model:
            return ProviderModelOption(
                model=model,
                label=model,
                reasoning_modes=("auto",),
                source="fallback",
            )
        console.print(f"  [{WARN}]Model id cannot be empty.[/]")


def _interactive_openai_compatible_select(
    console: Console,
    model_flag: str | None,
    reasoning_flag: str | None,
    *,
    repo_path: Path | None,
    save_key: bool,
) -> ProviderSelection:
    """Configure, verify, and select a model from a custom OpenAI gateway."""
    console.print()
    console.print("  [bold]OpenAI-compatible custom gateway[/bold]")
    console.print("  [dim]Works with 9router, vLLM, SGLang, and compatible /v1 APIs.[/dim]")

    while True:
        base_url, api_key = _prompt_openai_compatible_setup_values(
            console,
            official_base_url=_OPENAI_DEFAULT_BASE_URL,
        )
        if model_flag:
            selected = ProviderModelOption(
                model=model_flag,
                label=model_flag,
                reasoning_modes=("auto",),
                source="fallback",
            )
            break
        try:
            options = _discover_openai_compatible_models(
                repo_path,
                fallback_model=_PROVIDER_DEFAULTS["openai"],
            )
        except Exception as exc:
            console.print(f"  [{WARN}]Could not read {base_url}/models:[/] {exc}")
            console.print("  [1] Retry endpoint or key")
            console.print("  [2] Enter an exact model id anyway")
            console.print("  [3] Back to provider selection")
            action = Prompt.ask(
                "  Continue",
                choices=["1", "2", "3"],
                default="1",
                console=console,
            )
            if action == "1":
                continue
            if action == "3":
                return interactive_provider_config_select(
                    console,
                    model_flag,
                    reasoning_flag,
                    repo_path=repo_path,
                    save_key=save_key,
                )
            selected = _prompt_exact_model(console)
            break

        console.print(f"  [{OK}]✓ Connected[/] — discovered {len(options):,} model(s).")
        selected = _select_model_option(
            console,
            "openai",
            options,
            default_model=options[0].model,
            repo_path=repo_path,
        )
        break

    _persist_openai_compatible_setup(
        console,
        repo_path,
        base_url=base_url,
        api_key=api_key,
        save_key=save_key,
    )
    reasoning = _select_reasoning_mode(console, selected, reasoning_flag)
    return ProviderSelection("openai", selected.model, reasoning)


def interactive_provider_credentials(
    console: Console,
    provider: str,
    *,
    repo_path: Path | None = None,
    save_key: bool = True,
) -> bool:
    """Onboard an explicitly selected provider from a terminal.

    Returns ``True`` when a missing credential was supplied, ``False`` when
    there was nothing this prompt could configure or the user skipped it.
    The OpenAI provider additionally asks for a base URL, which is the small
    piece that makes the generic adapter useful for local gateways such as
    9router.
    """
    from repowise.core.providers.llm.registry import PROVIDER_API_KEY_ENVS

    env_vars = PROVIDER_API_KEY_ENVS.get(provider, ())
    if not env_vars:
        return False

    configured = False
    key_present = any((os.environ.get(name) or "").strip() for name in env_vars)
    if not key_present:
        env_var = env_vars[0]
        console.print()
        console.print(f"  [bold]{provider}[/bold] setup")
        console.print(f"  API key goes in [{VALUE}]{env_var}[/]. It will be hidden while typing.")
        key = _prompt_api_key(
            console,
            provider,
            env_var,
            repo_path=repo_path,
            save_key=save_key,
        )
        if not key:
            return False
        configured = True

    # The generic OpenAI adapter is the supported path for all compatible
    # gateways. Hosted OpenAI users can simply press Enter; local users paste
    # their gateway's /v1 URL here. Ask this when an explicit OpenAI provider
    # has a key but no endpoint too, so an existing OPENAI_API_KEY does not
    # force a local-gateway user back to shell configuration.
    if provider == "openai" and _prompt_openai_base_url(
        console,
        repo_path=repo_path,
    ):
        configured = True
    return configured
