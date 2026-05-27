"""Interactive provider selection (+ inline API key entry + save)."""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.ui.brand import BRAND, BRAND_STYLE, OK, WARN
from repowise.cli.ui.env_persistence import _save_key_to_dotenv

# ---------------------------------------------------------------------------
# Provider metadata  —  order matters (gemini first = default)
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "openai": "gpt-5.4-nano",
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-v4-flash",
    "ollama": "llama3.2",
    "openrouter": "anthropic/claude-sonnet-4.6",
    "litellm": "groq/llama-3.1-70b-versatile",
}

_PROVIDER_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "ollama": "OLLAMA_BASE_URL",
    "openrouter": "OPENROUTER_API_KEY",
}

_PROVIDER_SIGNUP: dict[str, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "ollama": "https://ollama.com/download",
    "openrouter": "https://openrouter.ai/keys",
}


def _detect_provider_status() -> dict[str, str]:
    """Return {provider: env_var_name} for providers whose key is set."""
    status: dict[str, str] = {}
    for prov, env_var in _PROVIDER_ENV.items():
        if prov == "gemini":
            if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                status[prov] = env_var
        elif os.environ.get(env_var):
            status[prov] = env_var
    return status


def interactive_provider_select(
    console: Console,
    model_flag: str | None,
    *,
    repo_path: Path | None = None,
) -> tuple[str, str]:
    """Show provider table, handle selection + inline key entry + save.

    Returns ``(provider_name, model_name)``.
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
        status_text = f"[{OK}]✓ API key set[/]" if prov in detected else "[dim]✗ no key[/dim]"
        default_model = _PROVIDER_DEFAULTS.get(prov, "")
        # Mark gemini as recommended
        label = prov
        if prov == "gemini":
            label = f"{prov} [dim](recommended)[/dim]"
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
            return interactive_provider_select(console, model_flag, repo_path=repo_path)

    # --- model ---
    default_model = _PROVIDER_DEFAULTS.get(chosen, "")
    if not model_flag:
        console.print(
            "  [dim]↳ Smaller is fine — repowise is calibrated for "
            "flash-lite / nano / haiku / 8B-class ollama. Bigger models "
            "don't improve doc quality.[/]"
        )
    model = model_flag or click.prompt(
        "  Model",
        default=default_model,
    )

    if not model_flag and _is_flagship_model(model):
        console.print(
            f"  [{WARN}]Note:[/] [dim]'{model}' works, but flash-lite / haiku / "
            "nano produce equivalent docs at ~10x lower cost on most repos.[/]"
        )

    return chosen, model


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
