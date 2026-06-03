"""Interactive provider selection (+ inline API key entry + save)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.ui.brand import BRAND, BRAND_STYLE, OK, WARN
from repowise.cli.ui.env_persistence import _save_key_to_dotenv
from repowise.core.providers.llm.base import ProviderModelOption
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

# ---------------------------------------------------------------------------
# Provider metadata  —  order matters (gemini first = default)
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "openai": "gpt-5.4-nano",
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-v4-flash",
    "codex_cli": "codex_cli/default",
    "ollama": "llama3.2",
    "openrouter": "anthropic/claude-sonnet-4.6",
    "litellm": "groq/llama-3.1-70b-versatile",
}

_PROVIDER_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "codex_cli": "__CODEX_CLI__",
    "ollama": "OLLAMA_BASE_URL",
    "openrouter": "OPENROUTER_API_KEY",
}

_PROVIDER_SIGNUP: dict[str, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "codex_cli": "https://developers.openai.com/codex/cli",
    "ollama": "https://ollama.com/download",
    "openrouter": "https://openrouter.ai/keys",
}


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


def _detect_provider_status() -> dict[str, str]:
    """Return {provider: env_var_name} for providers whose key is set."""
    status: dict[str, str] = {}
    for prov, env_var in _PROVIDER_ENV.items():
        if prov == "gemini":
            if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                status[prov] = env_var
        elif prov == "codex_cli":
            installed, logged_in = _detect_codex_cli_status()
            if installed and logged_in:
                status[prov] = "codex CLI"
        elif os.environ.get(env_var):
            status[prov] = env_var
    return status


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

    for idx, prov in enumerate(providers, 1):
        if prov == "codex_cli":
            codex_installed, codex_logged_in = _detect_codex_cli_status()
            if codex_logged_in:
                status_text = f"[{OK}]✓ codex CLI logged in[/]"
            elif codex_installed:
                status_text = "[yellow]✗ codex login required[/yellow]"
            else:
                status_text = "[dim]✗ codex CLI not found[/dim]"
        else:
            status_text = f"[{OK}]✓ API key set[/]" if prov in detected else "[dim]✗ no key[/dim]"
        default_model = _PROVIDER_DEFAULTS.get(prov, "")
        # Mark gemini as recommended
        label = prov
        if prov == "gemini":
            label = f"{prov} [dim](recommended)[/dim]"
        elif prov == "codex_cli":
            label = f"{prov} [dim](uses Codex CLI auth)[/dim]"
        table.add_row(f"[{idx}]", label, status_text, default_model)

    console.print()
    console.print(table)
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
        if chosen == "codex_cli":
            codex_installed, codex_logged_in = _detect_codex_cli_status()
            console.print()
            console.print(
                "  [bold]codex_cli[/bold] requires the Codex CLI on PATH "
                "and an authenticated Codex session."
            )
            console.print(f"  Install CLI: [{BRAND}]npm install -g @openai/codex[/]")
            console.print(f"  Authenticate: [{BRAND}]codex login[/]")
            console.print()
            if not codex_installed:
                console.print(
                    f"  [{WARN}]Codex CLI not detected. Please install and retry, "
                    "or select another provider.[/]"
                )
            elif not codex_logged_in:
                console.print(
                    f"  [{WARN}]Codex CLI is not logged in. Run 'codex login' and retry, "
                    "or select another provider.[/]"
                )
            return _interactive_provider_name(console, model_flag, repo_path=repo_path)
        env_var = _PROVIDER_ENV[chosen]
        signup_url = _PROVIDER_SIGNUP.get(chosen, "")
        console.print()
        console.print(f"  [bold]{chosen}[/bold] requires [cyan]{env_var}[/cyan].")
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


def _filtered_model_options(
    console: Console,
    options: tuple[ProviderModelOption, ...],
) -> list[ProviderModelOption]:
    if len(options) <= 40:
        return list(options)

    console.print(f"  [dim]{len(options):,} models available.[/dim]")
    query = click.prompt(
        "  Filter models (Enter for recommended)",
        default="",
        show_default=False,
    ).strip()
    if query:
        q = query.lower()
        filtered = [
            option
            for option in options
            if q in option.model.lower()
            or (option.label and q in option.label.lower())
            or (option.notes and q in option.notes.lower())
        ][:40]
        if filtered:
            return filtered
        console.print(f"  [{WARN}]No models matched {query!r}; showing recommended.[/]")

    recommended = [option for option in options if option.recommended]
    return (recommended or list(options))[:40]


def _select_model_option(
    console: Console,
    provider_name: str,
    options: tuple[ProviderModelOption, ...],
    *,
    default_model: str,
    repo_path: Path | None,
) -> ProviderModelOption:
    console.print(
        "  [dim]↳ Smaller is fine — repowise is calibrated for "
        "flash-lite / nano / haiku / 8B-class ollama. Bigger models "
        "don't improve doc quality.[/]"
    )

    display_options = _filtered_model_options(console, options)
    if not display_options:
        display_options = [_fallback_model_option(provider_name)]

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
    table.add_column("Source", style="dim")
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
            option.source,
            option.notes,
        )

    custom_idx = str(len(display_options) + 1)
    table.add_row(
        f"[{custom_idx}]",
        "Custom model",
        "auto",
        "manual",
        "type an exact model id",
    )

    console.print()
    console.print(table)
    console.print()

    choice = Prompt.ask(
        "  Select model",
        choices=[str(i) for i in range(1, len(display_options) + 2)],
        default=default_idx,
        console=console,
    )
    if choice == custom_idx:
        model = click.prompt("  Model", default=default_model)
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

    return click.prompt(
        "  Reasoning",
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

    reasoning = _select_reasoning_mode(selected, reasoning_flag)
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


_FLAGSHIP_MODEL_TOKENS = (
    "opus",
    "gpt-4o",
    "gpt-5",
    "-pro",
    "sonnet-4-7",
    "sonnet-4-6",
    "ultra",
    "o1",
    "o3",
)


def _is_flagship_model(model: str) -> bool:
    """Heuristic: True if the model name suggests a flagship-tier model."""
    if not model:
        return False
    m = model.lower()
    return any(tok in m for tok in _FLAGSHIP_MODEL_TOKENS)


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
            "  Save key to .repowise/.env for future runs? (auto-gitignored)",
            default=True,
        )
        if save:
            _save_key_to_dotenv(repo_path, env_var, key)
            console.print(f"  [{OK}]✓ Saved to .repowise/.env[/]")
    console.print()

    return key
