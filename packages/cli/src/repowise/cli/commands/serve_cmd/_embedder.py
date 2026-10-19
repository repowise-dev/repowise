"""Embedder and provider env setup before the in-process API server starts."""

from __future__ import annotations

import os
from pathlib import Path

import click

from repowise.cli.helpers import console, load_config

_GLOBAL_CONFIG_DIR = Path.home() / ".repowise"


#: Embedders offered at the prompt, in menu order, with the env vars that
#: carry their key. The first var is the one prompted for and set.
_EMBEDDER_KEY_VARS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def _setup_embedder() -> None:
    """Ensure REPOWISE_EMBEDDER is set before the server starts.

    Priority:
      1. Already set in environment → nothing to do.
      2. Saved in ~/.repowise/config.yaml → restore it (and its API key).
      3. Prompt the user interactively → save choice for next time.
    """
    if os.environ.get("REPOWISE_EMBEDDER") or _restore_saved_embedder():
        return

    choice = _prompt_embedder_choice()
    if choice == "skip":
        console.print("[dim]Skipping embedder — chat and search will be unavailable.[/dim]\n")
        return

    os.environ["REPOWISE_EMBEDDER"] = choice
    api_key = _get_or_prompt_api_key(choice)
    _set_api_key_env(choice, api_key)
    # Save choice (and key) to ~/.repowise/config.yaml for future runs.
    _save_global_embedder(choice, api_key)
    console.print()


def _restore_saved_embedder() -> bool:
    """Restore the embedder (and key) a previous serve/init saved globally."""
    cfg = load_config(Path.home())
    saved_embedder = cfg.get("embedder", "")
    if not saved_embedder or saved_embedder == "mock":
        return False
    os.environ["REPOWISE_EMBEDDER"] = saved_embedder
    if cfg.get("embedder_api_key"):
        _set_api_key_env(saved_embedder, cfg["embedder_api_key"])
    return True


def _exported_key(embedder: str) -> str:
    """The first non-empty exported key for *embedder*, or ``""``."""
    names = _EMBEDDER_KEY_VARS.get(embedder, ())
    return next((os.environ[n] for n in names if os.environ.get(n)), "")


def _prompt_embedder_choice() -> str:
    """Show the embedder menu and return the chosen name, or ``"skip"``."""
    console.print(
        "\n[bold]Chat & search require an embedder.[/bold] "
        "Choose one or skip (other features still work).\n"
    )
    options = [*_EMBEDDER_KEY_VARS, "skip"]
    for number, (name, key_vars) in enumerate(_EMBEDDER_KEY_VARS.items(), start=1):
        status = (
            "[green]✓ key set[/green]"
            if _exported_key(name)
            else f"[dim]needs {' / '.join(key_vars)}[/dim]"
        )
        console.print(f"  [{number}] {name:<12}{status}")
    console.print(f"  [{len(options)}] skip        [dim]no chat/search[/dim]")
    console.print()

    default = "1" if (_exported_key("gemini") or _exported_key("openai")) else "3"
    raw = click.prompt("  Select", default=default).strip()
    if raw in options:
        return raw
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return "skip"


def _get_or_prompt_api_key(embedder: str) -> str:
    """Return existing API key for *embedder* or prompt the user for one."""
    key_vars = _EMBEDDER_KEY_VARS.get(embedder)
    if not key_vars:
        return ""
    return (
        _exported_key(embedder)
        or click.prompt(f"  {key_vars[0]}", default="", show_default=False).strip()
    )


def _set_api_key_env(embedder: str, key: str) -> None:
    key_vars = _EMBEDDER_KEY_VARS.get(embedder)
    if key and key_vars:
        os.environ.setdefault(key_vars[0], key)


def _save_global_embedder(embedder: str, api_key: str) -> None:
    """Persist embedder choice to ~/.repowise/config.yaml."""
    _GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = _GLOBAL_CONFIG_DIR / "config.yaml"
    try:
        existing: dict = {}
        if config_path.exists():
            import yaml  # type: ignore[import-untyped]

            existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        existing["embedder"] = embedder
        if api_key:
            existing["embedder_api_key"] = api_key
        import yaml  # type: ignore[import-untyped]

        config_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # Non-fatal — user just gets prompted again next time.


def _load_local_provider_config() -> None:
    """Load ``.repowise/.env`` and seed the embedder from ``config.yaml``.

    The API server runs in-process here and builds one process-wide embedder
    at startup from env vars, so those are set now. ``load_dotenv`` never
    overwrites an existing var, and the embedder vars are only set when unset,
    so explicit environment configuration always wins. The chat provider
    needs no seeding: it is resolved per repo from that repo's own files.
    """
    from repowise.cli.helpers import load_config
    from repowise.cli.ui import load_dotenv

    cwd = Path.cwd()
    if not (cwd / ".repowise").exists():
        return

    # 1) API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, …) from .repowise/.env
    load_dotenv(cwd)
    cfg = load_config(cwd)

    # 2) Embedder from config so chat/search work without an interactive prompt.
    # Including "mock": that pin is not a preference to be talked out of, it is
    # the width the table on disk was written at. Skipping it sent a keyless
    # repo down _setup_embedder's detection path, which picks up any exported
    # API key and queries an 8-wide table with 1536-wide vectors — search
    # returns nothing, and nothing says why.
    embedder = cfg.get("embedder")
    if embedder and not os.environ.get("REPOWISE_EMBEDDER"):
        os.environ["REPOWISE_EMBEDDER"] = str(embedder)

    # 3) Embedding model from config — without this the server rebuilds the
    # embedder with a provider default (e.g. text-embedding-3-small) that
    # mismatches the indexed vectors, degrading chat/search retrieval (#426).
    embedding_model = cfg.get("embedding_model")
    if embedding_model and not os.environ.get("REPOWISE_EMBEDDING_MODEL"):
        os.environ["REPOWISE_EMBEDDING_MODEL"] = str(embedding_model)
