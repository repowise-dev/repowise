"""Interactive provider selection (+ inline API key entry + save)."""

from __future__ import annotations

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

from repowise.cli.ui.brand import BRAND, BRAND_STYLE, OK, VALUE, WARN
from repowise.cli.ui.env_persistence import _save_key_to_dotenv
from repowise.core.providers.llm.base import ProviderModelOption
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

# ---------------------------------------------------------------------------
# Provider metadata  —  order matters (gemini first = default)
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, str] = {
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-5.6-luna",
    "anthropic": "claude-haiku-4-5",
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-for-coding",
    "edenai": "mistral/mistral-small-latest",
    "codex_cli": "codex_cli/default",
    "claude_cli": "claude_cli/claude-haiku-4-5",
    "opencode": "opencode/default",
    "ollama": "qwen3.5:4b",
    "openrouter": "google/gemini-3.5-flash-lite",
    "litellm": "groq/llama-3.1-70b-versatile",
}

_PROVIDER_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "KIMI_API_KEY",
    "edenai": "EDENAI_API_KEY",
    "codex_cli": "__CODEX_CLI__",
    "claude_cli": "__CLAUDE_CLI__",
    "opencode": "__OPENCODE_CLI__",
    "ollama": "OLLAMA_BASE_URL",
    "openrouter": "OPENROUTER_API_KEY",
    # The picker iterates this map, so a provider missing here never renders a
    # row no matter what `_PROVIDER_DEFAULTS` says. litellm was in the defaults
    # only, which made it unreachable from init.
    "litellm": "LITELLM_API_KEY",
}

_PROVIDER_SIGNUP: dict[str, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "kimi": "https://www.kimi.com/code/console",
    "edenai": "https://app.edenai.run/user/register",
    "codex_cli": "https://developers.openai.com/codex/cli",
    "claude_cli": "https://claude.com/claude-code",
    "opencode": "https://opencode.ai",
    "ollama": "https://ollama.com/download",
    "openrouter": "https://openrouter.ai/keys",
    "litellm": "https://docs.litellm.ai/docs/providers",
}


# Short dim suffix on the provider name, saying what this provider is or how it
# authenticates. The Status column answers "can I pick this right now"; anything
# provider-specific belongs here so one column keeps one meaning.
_PROVIDER_NOTES: dict[str, str] = {
    "gemini": "recommended",
    "codex_cli": "uses your Codex CLI login",
    "claude_cli": "uses your Claude Code login",
    "opencode": "uses your opencode CLI setup",
    "ollama": "runs on your machine, no key",
    "litellm": "proxy in front of another provider",
}

_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
# Enough for a loopback connect; the table renders before any prompt, so a slow
# or firewalled endpoint must not hold the whole screen.
_OLLAMA_PROBE_TIMEOUT_S = 0.3


@dataclass(frozen=True)
class ProviderSelection:
    """Interactive provider/model/reasoning selection result."""

    provider_name: str
    model: str
    reasoning: ReasoningMode = "auto"


def _detect_codex_cli_status() -> tuple[bool, bool]:
    """Return ``(installed, logged_in)`` for the local Codex CLI."""
    from repowise.cli.mcp_config import is_codex_cli_installed, is_codex_logged_in

    installed = is_codex_cli_installed()
    return installed, is_codex_logged_in() if installed else False


def _detect_claude_cli_status() -> bool:
    """Return ``True`` if the Claude Code CLI is installed on PATH.

    Login state is not probed: ``claude`` keeps its credentials in a keychain or
    an OAuth token store with no cheap, side-effect-free "am I logged in" query,
    so the readiness signal stops at "installed" and an unauthenticated CLI
    surfaces as a provider error on first use.
    """
    import shutil

    return shutil.which("claude") is not None


def _detect_opencode_status() -> bool:
    """Return ``True`` if the opencode CLI is installed on PATH."""
    import shutil

    return shutil.which("opencode") is not None


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
    for prov, env_var in _PROVIDER_ENV.items():
        if prov == "gemini":
            if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                status[prov] = env_var
        elif prov == "codex_cli":
            installed, logged_in = _detect_codex_cli_status()
            if installed and logged_in:
                status[prov] = "codex CLI"
        elif prov == "claude_cli":
            if _detect_claude_cli_status():
                status[prov] = "claude CLI"
        elif prov == "opencode":
            if _detect_opencode_status():
                status[prov] = "opencode CLI"
        elif prov == "ollama":
            if _detect_ollama_status():
                status[prov] = ollama_base_url()
        elif os.environ.get(env_var):
            status[prov] = env_var
    return status


def _codex_cli_setup_lines() -> list[str]:
    installed, _ = _detect_codex_cli_status()
    problem = (
        "Codex CLI is not on PATH." if not installed else "Codex CLI is on PATH but not logged in."
    )
    return [
        "  [bold]codex_cli[/bold] uses the Codex CLI's own session. No API key here.",
        f"  Install: [{BRAND}]npm install -g @openai/codex[/]",
        f"  Log in:  [{BRAND}]codex login[/]",
        "",
        f"  [{WARN}]{problem}[/] Set it up and retry, or select another provider.",
    ]


def _claude_cli_setup_lines() -> list[str]:
    installed = _detect_claude_cli_status()
    lines = [
        "  [bold]claude_cli[/bold] uses the Claude Code CLI's own login, so a "
        "Claude subscription works here. No API key here.",
        f"  Install: [{BRAND}]https://claude.com/claude-code[/]",
        f"  Set up:  [{BRAND}]claude login[/]",
        "",
        f"  To pick a specific model: [{BRAND}]repowise init --provider claude_cli "
        "--model claude_cli/claude-sonnet-4-6[/]",
    ]
    if not installed:
        lines.extend(
            [
                "",
                f"  [{WARN}]claude CLI not found on PATH.[/] Install it and retry, "
                "or select another provider.",
            ]
        )
    return lines


def _opencode_setup_lines() -> list[str]:
    return [
        "  [bold]opencode[/bold] is a local AI coding CLI that manages its own "
        "models and authentication. No API key here.",
        f"  Install: [{BRAND}]curl -fsSL https://opencode.ai/install | bash[/]",
        f"  Set up:  [{BRAND}]opencode[/]  (first run configures your provider)",
        f"  Models:  [{BRAND}]opencode models[/]",
        "",
        f"  To pick a specific model: [{BRAND}]repowise init --provider opencode "
        "--model opencode/deepseek/deepseek-v4-pro[/]",
        "",
        f"  [{WARN}]opencode CLI not found on PATH.[/] Install it and retry, "
        "or select another provider.",
    ]


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
# ``registry.KEYLESS_PROVIDERS`` is the resolution-side version of this idea; it
# also holds litellm and mock, which do take a key here and are not offered
# interactively, so the picker keeps its own narrower list.
_LOCAL_PROVIDER_SETUP: dict[str, Callable[[], list[str]]] = {
    "codex_cli": _codex_cli_setup_lines,
    "claude_cli": _claude_cli_setup_lines,
    "opencode": _opencode_setup_lines,
    "ollama": _ollama_setup_lines,
}


def _interactive_provider_name(
    console: Console,
    model_flag: str | None,
    *,
    repo_path: Path | None = None,
) -> str:
    """Show provider table, handle selection + inline key entry + save.

    Returns the chosen provider name.
    """
    providers = list(_PROVIDER_ENV.keys())  # gemini first
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
        ready = prov in detected
        status_text = f"[{OK}]✓ ready[/]" if ready else "[dim]✗ not set up[/dim]"
        note = _PROVIDER_NOTES.get(prov, "")
        label = f"{prov} [dim]({note})[/dim]" if note else prov
        table.add_row(f"[{idx}]", label, status_text, _PROVIDER_DEFAULTS.get(prov, ""))

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

    chosen_idx = Prompt.ask(
        "  Select provider",
        choices=valid_choices,
        default=default_idx,
        console=console,
    )
    chosen = providers[int(chosen_idx) - 1]

    # --- inline API key entry if missing ---
    if chosen not in detected:
        setup_lines = _LOCAL_PROVIDER_SETUP.get(chosen)
        if setup_lines is not None:
            # Nothing to paste: these authenticate out of band or run locally,
            # so the fix is a command, not a key prompt.
            console.print()
            for line in setup_lines():
                console.print(line)
            return _interactive_provider_name(console, model_flag, repo_path=repo_path)
        env_var = _PROVIDER_ENV[chosen]
        signup_url = _PROVIDER_SIGNUP.get(chosen, "")
        console.print()
        console.print(f"  [bold]{chosen}[/bold] requires [{VALUE}]{env_var}[/].")
        if signup_url:
            console.print(f"  Get your API key here: [{BRAND}]{signup_url}[/]")
        console.print()
        key = _prompt_api_key(console, chosen, env_var, repo_path=repo_path)
        if not key:
            console.print(f"  [{WARN}]Skipped. Please select another provider.[/]")
            return _interactive_provider_name(console, model_flag, repo_path=repo_path)

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
            raise click.ClickException(str(exc)) from exc
        if requested not in choices:
            supported = ", ".join(choices)
            raise click.ClickException(
                f"reasoning={requested!r} is not supported by model "
                f"{selected.model!r}. Supported reasoning modes: {supported}."
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
) -> ProviderSelection:
    """Show provider/model/reasoning selection for interactive init.

    Returns a :class:`ProviderSelection` carrying the chosen provider name,
    model, and reasoning mode.
    """
    chosen = _interactive_provider_name(console, model_flag, repo_path=repo_path)
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
    if repo_path is not None:
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
    console.print()

    return key
