"""Branding, theme constants, and interactive UI helpers for the repowise CLI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Brand / theme
# ---------------------------------------------------------------------------

BRAND = "#F59520"
BRAND_STYLE = f"bold {BRAND}"
DIM = "dim"
OK = "green"
WARN = "yellow"
ERR = "bold red"

# ---------------------------------------------------------------------------
# ASCII art  —  bold half-block, compact lowercase, 2 lines
# ---------------------------------------------------------------------------

_LOGO = " █▀█ █▀▀ █▀█ █▀█ █ █ █ ▀ █▀▀ █▀▀\n █▀▄ ██▄ █▀▀ █▄█ ▀▄▀▄▀ █ ▄▄█ ██▄"


def print_banner(console: Console, repo_name: str | None = None) -> None:
    """Print the repowise logo, tagline, and optional repo name."""
    from repowise.cli import __version__

    console.print()
    console.print(Text(_LOGO, style=BRAND_STYLE))
    console.print(
        f"  [dim]codebase intelligence for developers and AI[/dim]  [dim]v{__version__}[/dim]"
    )
    if repo_name:
        console.print()
        console.print(f"  Repository: [bold]{repo_name}[/bold]")
    console.print()


# ---------------------------------------------------------------------------
# Phase headers
# ---------------------------------------------------------------------------


def print_phase_header(
    console: Console,
    num: int,
    total: int,
    title: str,
    subtitle: str = "",
) -> None:
    """Print a styled phase separator, e.g. ━━ Phase 1 of 4 · Ingestion ━━━."""
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Phase {num} of {total}[/] · [bold]{title}[/bold]",
            style=DIM,
        )
    )
    if subtitle:
        console.print(f"  [dim]{subtitle}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Provider metadata  —  order matters (gemini first = default)
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "openai": "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "ollama": "llama3.2",
    "litellm": "groq/llama-3.1-70b-versatile",
}

_PROVIDER_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_BASE_URL",
}

_PROVIDER_SIGNUP: dict[str, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "ollama": "https://ollama.com/download",
}


# ---------------------------------------------------------------------------
# .env persistence  —  save/load API keys in .repowise/.env
# ---------------------------------------------------------------------------


def load_dotenv(repo_path: Path) -> None:
    """Load ``<repo>/.repowise/.env`` into ``os.environ`` (without overwriting)."""
    env_file = repo_path / ".repowise" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # Don't overwrite existing env vars (explicit env takes priority)
        if key and value and key not in os.environ:
            os.environ[key] = value


def _save_key_to_dotenv(repo_path: Path, env_var: str, value: str) -> None:
    """Append or update a key in ``<repo>/.repowise/.env``."""
    env_dir = repo_path / ".repowise"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / ".env"

    # Read existing lines
    existing_lines: list[str] = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{env_var}="):
                existing_lines.append(f"{env_var}={value}")
                found = True
            else:
                existing_lines.append(line)

    if not found:
        existing_lines.append(f"{env_var}={value}")

    env_file.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")

    # Ensure .repowise/.env is gitignored
    _ensure_gitignored(repo_path)


def _ensure_gitignored(repo_path: Path) -> None:
    """Add ``.repowise/.env`` to ``.gitignore`` if not already present."""
    gitignore = repo_path / ".gitignore"
    pattern = ".repowise/.env"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if pattern in content:
            return
        # Append to existing file
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# repowise API keys (local)\n{pattern}\n"
        gitignore.write_text(content, encoding="utf-8")
    else:
        gitignore.write_text(
            f"# repowise API keys (local)\n{pattern}\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Interactive mode selection
# ---------------------------------------------------------------------------


def interactive_mode_select(console: Console) -> str:
    """Let the user choose full / index-only / advanced.

    Returns ``"full"``, ``"index_only"``, or ``"advanced"``.
    """
    body = Text()
    body.append("  [1]", style=BRAND_STYLE)
    body.append("  Full documentation  ", style="bold")
    body.append("(recommended)\n", style="dim")
    body.append("       Generate wiki pages, architecture diagrams, and API\n")
    body.append("       docs using an AI provider.\n\n")

    body.append("  [2]", style=BRAND_STYLE)
    body.append("  Index only  ", style="bold")
    body.append("(no LLM, no cost)\n", style="dim")
    body.append("       Dependency graph, git history, dead code analysis.\n")
    body.append("       Perfect for MCP-powered AI coding assistants.\n\n")

    body.append("  [3]", style=BRAND_STYLE)
    body.append("  Advanced\n", style="bold")
    body.append("       Full documentation with extra configuration\n")
    body.append("       (commit limit, exclude patterns, concurrency …)")

    console.print(
        Panel(
            body,
            title="[bold]How would you like to document this repo?[/bold]",
            border_style=BRAND,
            padding=(1, 2),
        )
    )

    choice = Prompt.ask(
        "  Select mode",
        choices=["1", "2", "3"],
        default="1",
        console=console,
    )
    return {"1": "full", "2": "index_only", "3": "advanced"}[choice]


# ---------------------------------------------------------------------------
# Interactive provider selection (+ inline API key entry + save)
# ---------------------------------------------------------------------------


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
    model = model_flag or click.prompt(
        "  Model",
        default=default_model,
    )

    return chosen, model


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


# ---------------------------------------------------------------------------
# Advanced configuration
# ---------------------------------------------------------------------------


def interactive_advanced_config(console: Console) -> dict[str, Any]:
    """Prompt for advanced init options.

    Returns a dict with keys matching init_command kwargs:
    ``commit_limit``, ``follow_renames``, ``skip_tests``, ``skip_infra``,
    ``concurrency``, ``exclude``, ``test_run``.
    """
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Advanced Configuration[/]",
            style=DIM,
        )
    )
    console.print()

    result: dict[str, Any] = {}

    # --- commit limit ---
    val = click.prompt(
        "  Max commits to analyze per file",
        default=500,
        type=int,
    )
    val = max(1, min(val, 5000))
    result["commit_limit"] = val

    # --- follow renames ---
    result["follow_renames"] = click.confirm(
        "  Track files across git renames? (slower but more accurate)",
        default=False,
    )

    # --- skip tests ---
    result["skip_tests"] = click.confirm(
        "  Skip test files?",
        default=False,
    )

    # --- skip infra ---
    result["skip_infra"] = click.confirm(
        "  Skip infrastructure files? (Dockerfile, CI, Makefile …)",
        default=False,
    )

    # --- concurrency ---
    result["concurrency"] = click.prompt(
        "  Max concurrent LLM calls",
        default=5,
        type=int,
    )

    # --- exclude patterns ---
    console.print("  [dim]Exclude patterns (gitignore-style, comma-separated, or empty):[/dim]")
    raw = click.prompt("  Exclude", default="", show_default=False)
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    result["exclude"] = tuple(patterns)

    # --- test run ---
    result["test_run"] = click.confirm(
        "  Test run? (limit to top 10 files for quick validation)",
        default=False,
    )

    # --- summary ---
    console.print()
    summary = Table(box=None, padding=(0, 2), show_header=False)
    summary.add_column("Option", style="dim")
    summary.add_column("Value", style="bold")
    summary.add_row("Commit limit", str(result["commit_limit"]))
    summary.add_row("Follow renames", "yes" if result["follow_renames"] else "no")
    summary.add_row("Skip tests", "yes" if result["skip_tests"] else "no")
    summary.add_row("Skip infra", "yes" if result["skip_infra"] else "no")
    summary.add_row("Concurrency", str(result["concurrency"]))
    if patterns:
        summary.add_row("Exclude", ", ".join(patterns))
    summary.add_row("Test run", "yes" if result["test_run"] else "no")

    console.print(
        Panel(
            summary,
            title="[bold]Configuration[/bold]",
            border_style=BRAND,
            padding=(0, 1),
        )
    )
    console.print()
    return result


# ---------------------------------------------------------------------------
# Index-only confirmation screen
# ---------------------------------------------------------------------------


def print_index_only_intro(console: Console, has_provider: bool = False) -> None:
    """Show what index-only mode will do before starting."""
    lines = [
        "  [green]✓[/] Parse all source files (AST)",
        "  [green]✓[/] Build dependency graph (PageRank, communities)",
        "  [green]✓[/] Index git history (hotspots, ownership, co-changes)",
        "  [green]✓[/] Detect dead code",
        "  [green]✓[/] Extract architectural decisions",
        "  [green]✓[/] Set up MCP server for AI assistants",
    ]
    if has_provider:
        lines.append(
            "  [green]✓[/] [dim]Decision extraction enhanced (provider key detected)[/dim]"
        )
    lines.append("")
    lines.append("  [dim]No LLM calls. No cost.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Index Only[/bold]",
            border_style=BRAND,
            padding=(1, 1),
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# Completion panels
# ---------------------------------------------------------------------------


def build_completion_panel(
    title: str,
    metrics: list[tuple[str, str]],
    *,
    next_steps: list[tuple[str, str]] | None = None,
) -> Panel:
    """Build a bordered summary panel.

    *metrics* is a list of ``(label, value)`` pairs.
    *next_steps* is an optional list of ``(command, description)`` pairs.
    """
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column("Metric", style="dim", min_width=20)
    table.add_column("Value", style="bold")

    for label, value in metrics:
        table.add_row(label, value)

    parts: list[Any] = [table]

    if next_steps:
        parts.append(Text(""))
        parts.append(Text("  What's next:", style="bold"))
        for cmd, desc in next_steps:
            parts.append(Text(f"  {cmd:<28}{desc}", style="dim"))

    return Panel(
        Group(*parts),
        title=f"[bold]{title}[/bold]",
        border_style=BRAND,
        padding=(1, 1),
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def format_elapsed(seconds: float) -> str:
    """Format seconds as ``Xm Ys`` or ``X.Ys``."""
    if seconds >= 60:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


# ---------------------------------------------------------------------------
# Rich progress callback — implements core ProgressCallback protocol
# ---------------------------------------------------------------------------

_PHASE_LABELS: dict[str, str] = {
    "traverse": "Traversing files...",
    "parse": "Parsing files...",
    "graph": "Building dependency graph...",
    "git": "Indexing file history...",
    "co_change": "Analyzing co-changes...",
    "dead_code": "Detecting dead code...",
    "decisions": "Extracting decisions...",
    "generation": "Generating pages...",
}


class RichProgressCallback:
    """Adapter that implements ``repowise.core.pipeline.ProgressCallback``
    using a Rich ``Progress`` instance for terminal display.

    Usage::

        from rich.progress import Progress
        with Progress(...) as progress_bar:
            callback = RichProgressCallback(progress_bar, console)
            result = run_async(run_pipeline(..., progress=callback))
    """

    def __init__(self, progress: Any, console: Console) -> None:
        self._progress = progress
        self._console = console
        self._tasks: dict[str, Any] = {}

    def on_phase_start(self, phase: str, total: int | None) -> None:
        label = _PHASE_LABELS.get(phase, f"{phase}...")
        # If phase already has a task, update its total and make visible
        if phase in self._tasks:
            self._progress.update(self._tasks[phase], total=total, visible=True)
        else:
            self._tasks[phase] = self._progress.add_task(
                label, total=total, visible=True, cost=0.0
            )

    def on_item_done(self, phase: str) -> None:
        if phase in self._tasks:
            self._progress.advance(self._tasks[phase])

    def on_message(self, level: str, text: str) -> None:
        style_map = {"info": OK, "warning": WARN, "error": ERR}
        style = style_map.get(level, "")
        if style:
            self._progress.console.print(f"  [{style}]{text}[/{style}]")
        else:
            self._progress.console.print(f"  {text}")

    def set_cost(self, total_cost: float) -> None:
        """Update the live cost display on all active progress tasks."""
        for task_id in self._tasks.values():
            try:
                self._progress.update(task_id, cost=total_cost)
            except Exception:
                pass
