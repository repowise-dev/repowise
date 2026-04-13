"""Branding, theme constants, and interactive UI helpers for the repowise CLI."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import ProgressColumn, Task
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
# Quick repo pre-scan (fast, no AST)
# ---------------------------------------------------------------------------


@dataclass
class RepoScanInfo:
    """Lightweight repo stats collected before mode selection."""

    total_files: int = 0
    language_counts: dict[str, int] = field(default_factory=dict)
    total_commits: int = 0
    test_file_count: int = 0
    infra_file_count: int = 0
    submodule_count: int = 0
    large_dirs: list[tuple[str, int]] = field(default_factory=list)
    """(dir_name, file_count) for dirs with >50 files — used for exclude suggestions."""


_TEST_PATTERNS = {"test_", "_test.", ".test.", "tests/", "test/", "__tests__/", "spec/"}
_INFRA_NAMES = {"dockerfile", "makefile", "jenkinsfile", "terraform", ".tf", ".sh", ".bash"}
_LANG_MAP: dict[str, list[str]] = {
    "Python": [".py"],
    "TypeScript": [".ts", ".tsx"],
    "JavaScript": [".js", ".jsx", ".mjs", ".cjs"],
    "Go": [".go"],
    "Rust": [".rs"],
    "Java": [".java"],
    "C#": [".cs"],
    "Ruby": [".rb"],
    "PHP": [".php"],
    "Swift": [".swift"],
    "Kotlin": [".kt", ".kts"],
    "C/C++": [".c", ".cpp", ".cc", ".h", ".hpp"],
    "Scala": [".scala"],
    "Dart": [".dart"],
    "Lua": [".lua"],
    "Shell": [".sh", ".bash", ".zsh"],
    "HTML": [".html", ".htm"],
    "CSS": [".css", ".scss", ".sass", ".less"],
    "YAML": [".yaml", ".yml"],
    "JSON": [".json"],
    "Markdown": [".md", ".mdx"],
    "SQL": [".sql"],
}
_EXT_TO_LANG: dict[str, str] = {}
for _lang, _exts in _LANG_MAP.items():
    for _ext in _exts:
        _EXT_TO_LANG[_ext] = _lang

_SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    "vendor",
    ".git",
    ".hg",
    "env",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
}


def quick_repo_scan(repo_path: Path) -> RepoScanInfo:
    """Fast pre-scan: count files, detect languages, count git commits.

    No AST parsing — just ``os.walk`` + extension histogram + ``git rev-list --count``.
    Typically completes in <2s even on large repos.
    """
    info = RepoScanInfo()
    dir_counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Prune heavy/irrelevant directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]

        rel_dir = os.path.relpath(dirpath, repo_path)
        top_dir = rel_dir.split(os.sep)[0] if rel_dir != "." else "."

        for fname in filenames:
            info.total_files += 1
            lower = fname.lower()
            ext = os.path.splitext(lower)[1]

            # Language detection
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                info.language_counts[lang] = info.language_counts.get(lang, 0) + 1

            # Test file detection
            full_rel = os.path.join(rel_dir, lower).replace("\\", "/")
            if any(p in full_rel for p in _TEST_PATTERNS):
                info.test_file_count += 1

            # Infra file detection
            if lower in _INFRA_NAMES or ext in _INFRA_NAMES:
                info.infra_file_count += 1

            # Track top-level dir sizes for exclude suggestions
            if top_dir != ".":
                dir_counts[top_dir] = dir_counts.get(top_dir, 0) + 1

    # Large dirs (>50 files) sorted by size
    info.large_dirs = sorted(
        [(d, c) for d, c in dir_counts.items() if c > 50],
        key=lambda x: -x[1],
    )

    # Git commit count (fast)
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            info.total_commits = int(result.stdout.strip())
    except Exception:
        pass

    # Submodule count
    gitmodules = repo_path / ".gitmodules"
    if gitmodules.exists():
        try:
            content = gitmodules.read_text(encoding="utf-8", errors="ignore")
            info.submodule_count = content.count("[submodule ")
        except Exception:
            pass

    return info


def print_scan_summary(console: Console, scan: RepoScanInfo) -> None:
    """Print a compact pre-scan summary below the banner."""
    # File count + language count
    lang_count = len(
        [
            name
            for name, c in scan.language_counts.items()
            if c > 0 and name not in ("JSON", "YAML", "Markdown", "HTML", "CSS")
        ]
    )

    parts = [f"[bold]{scan.total_files:,}[/bold] files"]
    if lang_count:
        parts.append(f"[bold]{lang_count}[/bold] languages")
    if scan.total_commits:
        parts.append(f"[bold]{scan.total_commits:,}[/bold] commits")

    header_line = " · ".join(parts)

    # Top languages (source code only, top 4)
    source_langs = {
        lang: count
        for lang, count in scan.language_counts.items()
        if lang not in ("JSON", "YAML", "Markdown", "HTML", "CSS")
    }
    total_source = sum(source_langs.values()) or 1
    top_langs = sorted(source_langs.items(), key=lambda x: -x[1])[:4]
    lang_parts = [f"{lang} {count / total_source:.0%}" for lang, count in top_langs]
    if len(source_langs) > 4:
        lang_parts.append(f"+{len(source_langs) - 4} more")
    lang_line = ", ".join(lang_parts) if lang_parts else "no source files detected"

    body = f"  {header_line}\n  [dim]{lang_line}[/dim]"

    console.print(
        Panel(
            body,
            border_style="dim",
            padding=(0, 1),
        )
    )
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


def interactive_advanced_config(
    console: Console,
    scan: RepoScanInfo | None = None,
) -> dict[str, Any]:
    """Prompt for advanced init options, grouped into logical sections.

    When *scan* is provided, uses it for smart defaults and contextual hints
    (file counts, suggested exclude patterns, etc.).

    Returns a dict with keys matching init_command kwargs:
    ``commit_limit``, ``follow_renames``, ``skip_tests``, ``skip_infra``,
    ``concurrency``, ``exclude``, ``test_run``, ``embedder``,
    ``include_submodules``, ``no_claude_md``.
    """
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Advanced Configuration[/]",
            style=DIM,
        )
    )

    result: dict[str, Any] = {}

    # ── Scope ─────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [{BRAND}]Scope[/]")
    console.print("  [dim]Choose what to include in the analysis[/dim]")
    console.print()

    test_hint = f" ({scan.test_file_count:,} found)" if scan and scan.test_file_count else ""
    result["skip_tests"] = click.confirm(
        f"  Skip test files?{test_hint}",
        default=False,
    )

    infra_hint = f" ({scan.infra_file_count:,} found)" if scan and scan.infra_file_count else ""
    result["skip_infra"] = click.confirm(
        f"  Skip infrastructure files?{infra_hint} (Dockerfile, CI, Makefile …)",
        default=False,
    )

    if scan and scan.submodule_count:
        result["include_submodules"] = click.confirm(
            f"  Include git submodules? ({scan.submodule_count} found)",
            default=False,
        )
    else:
        result["include_submodules"] = False

    # ── Exclude Patterns ──────────────────────────────────────────────────
    console.print()
    console.print(f"  [{BRAND}]Exclude Patterns[/]")

    # Show suggestions from large dirs
    if scan and scan.large_dirs:
        suggestions = scan.large_dirs[:5]
        console.print("  [dim]Large directories detected:[/dim]")
        for dirname, count in suggestions:
            console.print(f"    [dim]{dirname}/[/dim] [dim]({count:,} files)[/dim]")
        console.print()

    console.print("  [dim]Gitignore-style patterns, comma-separated or one per line.[/dim]")
    console.print("  [dim]Press Enter with empty input to finish.[/dim]")
    patterns: list[str] = []
    while True:
        raw = click.prompt("  Pattern", default="", show_default=False)
        raw = raw.strip()
        if not raw:
            break
        # Support comma-separated input
        for part in raw.split(","):
            part = part.strip()
            if part:
                patterns.append(part)
    result["exclude"] = tuple(patterns)

    # ── Git Analysis ──────────────────────────────────────────────────────
    console.print()
    console.print(f"  [{BRAND}]Git Analysis[/]")
    commit_hint = ""
    if scan and scan.total_commits:
        commit_hint = f" [dim](repo has ~{scan.total_commits:,} total commits)[/dim]"
    console.print(f"  [dim]Controls how deeply git history is analyzed[/dim]{commit_hint}")
    console.print()

    # Smart default based on repo size
    default_limit = 500
    if scan:
        if scan.total_files < 500:
            default_limit = 1000
        elif scan.total_files > 5000:
            default_limit = 200

    val = click.prompt(
        "  Max commits per file",
        default=default_limit,
        type=int,
    )
    val = max(1, min(val, 5000))
    result["commit_limit"] = val

    result["follow_renames"] = click.confirm(
        "  Track files across git renames? (slower but more accurate)",
        default=False,
    )

    # ── Generation ────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [{BRAND}]Generation[/]")
    console.print("  [dim]LLM page generation settings[/dim]")
    console.print()

    # Smart concurrency default
    default_concurrency = 5
    if scan and scan.total_files < 200:
        default_concurrency = 8
    elif scan and scan.total_files > 5000:
        default_concurrency = 3

    result["concurrency"] = click.prompt(
        "  Max concurrent LLM calls",
        default=default_concurrency,
        type=int,
    )

    # Embedder selection
    detected_embedder = _resolve_embedder_from_env()
    embedder_choices = ["gemini", "openai", "mock"]
    result["embedder"] = click.prompt(
        "  Embedder for RAG",
        default=detected_embedder,
        type=click.Choice(embedder_choices),
    )

    result["test_run"] = click.confirm(
        "  Test run? (limit to top 10 files for quick validation)",
        default=False,
    )

    # ── Editor Integration ────────────────────────────────────────────────
    console.print()
    console.print(f"  [{BRAND}]Editor Integration[/]")
    console.print()

    result["no_claude_md"] = not click.confirm(
        "  Generate .claude/CLAUDE.md?",
        default=True,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    console.print()
    summary = Table(box=None, padding=(0, 2), show_header=False)
    summary.add_column("Option", style="dim")
    summary.add_column("Value", style="bold")
    summary.add_row("Skip tests", "yes" if result["skip_tests"] else "no")
    summary.add_row("Skip infra", "yes" if result["skip_infra"] else "no")
    if result["include_submodules"]:
        summary.add_row("Include submodules", "yes")
    summary.add_row("Commit limit", str(result["commit_limit"]))
    summary.add_row("Follow renames", "yes" if result["follow_renames"] else "no")
    summary.add_row("Concurrency", str(result["concurrency"]))
    summary.add_row("Embedder", result["embedder"])
    if patterns:
        summary.add_row("Exclude", ", ".join(patterns))
    summary.add_row("Test run", "yes" if result["test_run"] else "no")
    summary.add_row("CLAUDE.md", "no" if result["no_claude_md"] else "yes")

    console.print(
        Panel(
            summary,
            title="[bold]Configuration Summary[/bold]",
            border_style=BRAND,
            padding=(0, 1),
        )
    )
    console.print()
    return result


def _resolve_embedder_from_env() -> str:
    """Auto-detect embedder from env vars (for advanced config default)."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


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
# Analysis summary panel (shown between analysis and generation)
# ---------------------------------------------------------------------------


def build_analysis_summary_panel(
    *,
    file_count: int,
    symbol_count: int,
    graph_nodes: int,
    graph_edges: int,
    dead_unreachable: int = 0,
    dead_unused: int = 0,
    dead_lines: int = 0,
    decision_count: int = 0,
    git_files: int = 0,
    hotspot_count: int = 0,
    community_count: int = 0,
    lang_summary: str = "",
) -> Panel:
    """Compact analysis-complete interstitial shown before generation."""
    lines: list[str] = []
    lines.append(
        f"  [bold]{file_count:,}[/bold] files · "
        f"[bold]{symbol_count:,}[/bold] symbols"
        + (f" · [bold]{community_count}[/bold] communities" if community_count else "")
    )
    if lang_summary:
        lines.append(f"  [dim]{lang_summary}[/dim]")
    lines.append("")
    lines.append(
        f"  Graph    [bold]{graph_nodes:,}[/bold] nodes · [bold]{graph_edges:,}[/bold] edges"
    )
    if git_files:
        lines.append(
            f"  Git      [bold]{git_files:,}[/bold] files indexed"
            + (f" · [bold]{hotspot_count}[/bold] hotspots" if hotspot_count else "")
        )
    if dead_unreachable or dead_unused:
        lines.append(
            f"  Dead     [bold]{dead_unreachable}[/bold] unreachable · "
            f"[bold]{dead_unused}[/bold] unused exports"
            + (f" · ~{dead_lines:,} lines" if dead_lines else "")
        )
    if decision_count:
        lines.append(f"  Decisions [bold]{decision_count}[/bold] extracted")

    return Panel(
        "\n".join(lines),
        title="[bold]Analysis Complete[/bold]",
        border_style=BRAND,
        padding=(1, 1),
    )


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


def build_contextual_next_steps(
    *,
    index_only: bool,
    dead_unreachable: int = 0,
    dead_unused: int = 0,
    hotspot_count: int = 0,
    decision_count: int = 0,
    top_hotspot: str = "",
) -> list[tuple[str, str]]:
    """Build next-step suggestions based on what the analysis actually found."""
    steps: list[tuple[str, str]] = []

    if index_only:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise init --provider gemini", "generate full documentation"))
    else:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise search <query>", "search the generated wiki"))

    if dead_unreachable + dead_unused > 0:
        steps.append(
            ("repowise dead-code", f"explore {dead_unreachable + dead_unused} dead code findings")
        )

    if hotspot_count > 0 and top_hotspot:
        steps.append((f"repowise risk {top_hotspot}", "assess risk for top hotspot"))

    if decision_count > 0:
        steps.append(("repowise decisions", f"browse {decision_count} architectural decisions"))

    if not steps:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise search <query>", "search the index"))

    return steps


# ---------------------------------------------------------------------------
# Workspace: interactive repo selection
# ---------------------------------------------------------------------------


def interactive_repo_select(
    console: Console,
    repos: list[Any],
) -> list[Any]:
    """Display discovered repos and let the user pick which ones to index.

    *repos* is a list of :class:`~repowise.core.workspace.scanner.DiscoveredRepo`.
    Returns the selected subset in original order.
    """
    # Build display table
    table = Table(
        show_header=True,
        box=None,
        padding=(0, 2),
        title="[bold]Discovered Repositories[/bold]",
        title_style="",
    )
    table.add_column("#", style=BRAND_STYLE, width=4)
    table.add_column("Repository", style="bold", min_width=16)
    table.add_column("Path", style="dim", min_width=20)
    table.add_column("Status", min_width=14)

    for idx, repo in enumerate(repos, 1):
        status = f"[{OK}]indexed[/]" if repo.has_repowise else "[dim]new[/dim]"
        if repo.is_submodule:
            status += " [dim](submodule)[/dim]"
        table.add_row(f"[{idx}]", repo.name, str(repo.path.name), status)

    console.print()
    console.print(table)
    console.print()

    # Selection prompt with retry
    while True:
        raw = Prompt.ask(
            "  Select repos to index",
            default="all",
            console=console,
        )
        raw = raw.strip().lower()

        if raw == "all":
            return list(repos)
        if raw == "none":
            return []

        selected = _parse_selection(raw, len(repos))
        if selected is not None:
            return [repos[i] for i in selected]

        console.print(
            f"  [{WARN}]Invalid selection. Use numbers (1,2,3), ranges (1-3), 'all', or 'none'.[/]"
        )


def _parse_selection(raw: str, count: int) -> list[int] | None:
    """Parse a comma-separated selection string into zero-based indices.

    Supports: ``"1,2,3"``, ``"1-3"``, ``"1,3-5"``, ``"1-3,5"``.
    Returns ``None`` on invalid input.
    """
    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except ValueError:
                return None
            if lo < 1 or hi > count or lo > hi:
                return None
            indices.extend(range(lo - 1, hi))
        else:
            try:
                num = int(part)
            except ValueError:
                return None
            if num < 1 or num > count:
                return None
            indices.append(num - 1)

    if not indices:
        return None

    # Deduplicate while preserving order
    seen: set[int] = set()
    result: list[int] = []
    for i in indices:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result


def interactive_primary_select(
    console: Console,
    repos: list[Any],
) -> str:
    """Ask which repo is the primary/default. Returns the alias.

    *repos* is the list of selected :class:`DiscoveredRepo` objects.
    """
    if len(repos) == 1:
        return repos[0].alias

    console.print()
    for idx, repo in enumerate(repos, 1):
        console.print(f"  [{BRAND_STYLE}][{idx}][/] {repo.name}")
    console.print()

    choices = [str(i) for i in range(1, len(repos) + 1)]
    chosen = Prompt.ask(
        "  Which is your primary repo?",
        choices=choices,
        default="1",
        console=console,
    )
    return repos[int(chosen) - 1].alias


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
# Rich progress helpers
# ---------------------------------------------------------------------------


class MaybeCountColumn(ProgressColumn):
    """Progress column that shows ``completed/total`` when total is known,
    or just ``completed`` when total is ``None`` (indeterminate phase).

    This prevents the ugly ``1214/None`` display that appears for phases
    like file traversal and dead-code detection whose total is not known
    upfront.
    """

    def render(self, task: Task) -> Text:
        if task.total is None:
            return Text(str(int(task.completed)), style="progress.download")
        return Text(
            f"{int(task.completed)}/{int(task.total)}",
            style="progress.download",
        )


# ---------------------------------------------------------------------------
# Rich progress callback — implements core ProgressCallback protocol
# ---------------------------------------------------------------------------

_PHASE_LABELS: dict[str, str] = {
    "traverse": "Scanning & filtering files...",
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
            self._tasks[phase] = self._progress.add_task(label, total=total, visible=True, cost=0.0)

    def on_item_done(self, phase: str) -> None:
        if phase in self._tasks:
            self._progress.advance(self._tasks[phase])

    def on_message(self, level: str, text: str) -> None:
        style_map = {"info": OK, "warning": WARN, "error": ERR}
        style = style_map.get(level, "")
        # Insight lines (indented with →) get special formatting
        if text.lstrip().startswith("→"):
            self._progress.console.print(f"  [dim]{text}[/dim]")
        elif style:
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
