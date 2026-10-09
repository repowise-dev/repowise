"""Setup primitives for custom OpenAI-compatible gateways."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import click
from rich.console import Console

from repowise.cli.ui.brand import WARN
from repowise.cli.ui.env_persistence import _save_key_to_dotenv
from repowise.core.providers.llm.base import ProviderModelOption

DEFAULT_BASE_URL = "http://localhost:20128/v1"


def normalize_base_url(value: str) -> str:
    """Validate and normalize an OpenAI-compatible HTTP API root."""
    raw = value.strip()
    if not raw:
        raise ValueError("Base URL cannot be empty")
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlparse(raw)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid Base URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Base URL must use http:// or https://")
    if not parsed.hostname:
        raise ValueError("Base URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("put credentials in the API key field, not the Base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL cannot include a query string or fragment")
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc, path, "", "", ""))


def prompt_setup(
    console: Console,
    *,
    official_base_url: str,
) -> tuple[str, str]:
    """Collect a validated endpoint and a non-empty gateway API key."""
    existing_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    default_url = (
        existing_url
        if existing_url.rstrip("/") not in ("", official_base_url)
        else DEFAULT_BASE_URL
    )
    while True:
        raw_url = click.prompt(
            "  Base URL (OpenAI-compatible endpoint)",
            default=default_url,
            show_default=True,
        )
        try:
            base_url = normalize_base_url(raw_url)
            break
        except ValueError as exc:
            console.print(f"  [{WARN}]{exc}.[/] Try again.")

    existing_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    while True:
        label = (
            "  API key (hidden; Enter keeps the current key)"
            if existing_key
            else "  API key (hidden)"
        )
        entered = click.prompt(
            label,
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        api_key = entered or existing_key
        if api_key:
            break
        console.print(f"  [{WARN}]An API key is required.[/] Use any gateway token it accepts.")

    os.environ["OPENAI_BASE_URL"] = base_url
    os.environ["OPENAI_API_KEY"] = api_key
    return base_url, api_key


def discover_models(
    repo_path: Path | None,
    *,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    """Use the core adapter's strict discovery path for an actionable check."""
    from repowise.cli.helpers import resolve_provider
    from repowise.core.providers.llm.openai import OpenAIProvider

    provider = resolve_provider("openai", fallback_model, repo_path)
    if not isinstance(provider, OpenAIProvider):
        raise TypeError("the openai provider did not resolve to OpenAIProvider")
    return provider.discover_model_options()


def persist_setup(
    console: Console,
    repo_path: Path | None,
    *,
    base_url: str,
    api_key: str,
    save_key: bool,
) -> None:
    """Persist the endpoint and, with consent, its secret after setup succeeds."""
    if repo_path is None:
        return
    _save_key_to_dotenv(repo_path, "OPENAI_BASE_URL", base_url)
    console.print("  [green]✓[/green] Saved endpoint to .repowise/.env")

    from repowise.cli.helpers import NO_SAVE_KEY_ENV

    if not save_key:
        os.environ[NO_SAVE_KEY_ENV] = "1"
        console.print("  [dim]API key kept for this process only (--no-save-key).[/dim]")
        return
    save = click.confirm(
        "  Save the API key to .repowise/.env for future runs? (gitignored, owner-only)",
        default=True,
    )
    if save:
        _save_key_to_dotenv(repo_path, "OPENAI_API_KEY", api_key)
        console.print("  [green]✓[/green] Saved API key to .repowise/.env")
    else:
        os.environ[NO_SAVE_KEY_ENV] = "1"
        console.print("  [dim]API key kept for this process only.[/dim]")
