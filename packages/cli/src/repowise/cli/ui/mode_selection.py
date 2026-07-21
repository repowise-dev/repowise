"""Interactive mode selection, fast-mode offer, and advanced configuration."""

from __future__ import annotations

import os
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from repowise.cli.ui.brand import BRAND, BRAND_STYLE, DIM
from repowise.cli.ui.repo_scanner import RepoScanInfo
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.styles import DEFAULT_STYLE, list_styles
from repowise.core.reasoning import REASONING_MODES

# A repo at or above this many files is "large" — large enough that a quick
# fast-mode first index (graph + essential git, no LLM docs) is worth offering.
LARGE_REPO_FILE_THRESHOLD = 5000


def should_offer_fast_mode(scan: RepoScanInfo | None) -> bool:
    """Whether to surface the fast-mode offer for this repo.

    Fast mode only makes sense on large repos; small repos run full in seconds
    so the offer would just be noise.
    """
    return scan is not None and scan.total_files > LARGE_REPO_FILE_THRESHOLD


def interactive_fast_mode_offer(
    console: Console,
    scan: RepoScanInfo | None,
    *,
    default_fast: bool,
) -> bool:
    """Offer fast mode after a large repo is detected. Returns True to use it.

    Shown only when :func:`should_offer_fast_mode` is true. Fast mode is a quick
    first index (dependency graph + essential git history, metrics in SQL) with
    no per-file blame, no co-change walk, and no LLM docs — backfillable later.
    """
    n = scan.total_files if scan else 0
    body = Text()
    body.append("  Large repository detected — ", style="bold")
    body.append(f"{n:,} files.\n\n", style=BRAND_STYLE)
    body.append("  Fast mode runs a quick first index:\n", style="bold")
    body.append("    • dependency graph + essential git history\n")
    body.append("    • graph metrics materialized to SQL\n")
    body.append("    • no per-file blame, no co-change walk, no LLM docs\n\n")
    body.append("  You can backfill full git history and generate docs later.\n", style="dim")
    console.print(
        Panel(
            body,
            title="[bold]Fast first index?[/bold]",
            border_style=BRAND,
            padding=(1, 2),
        )
    )
    return click.confirm("  Use fast mode?", default=default_fast)


def interactive_mode_select(console: Console) -> str:
    """Let the user choose full / index-only / advanced.

    Returns ``"full"``, ``"index_only"``, or ``"advanced"``.
    """
    body = Text()
    body.append("  [1]", style=BRAND_STYLE)
    body.append("  Everything  ", style="bold")
    body.append("(recommended)\n", style="dim")
    body.append("       All five layers: dependency graph, git history, code\n")
    body.append("       health, decisions + AI-generated wiki, diagrams & API docs.\n\n")

    body.append("  [2]", style=BRAND_STYLE)
    body.append("  Index only  ", style="bold")
    body.append("(no key, no spend)\n", style="dim")
    body.append("       Same layers, and a complete wiki rendered from the code's\n")
    body.append("       structure instead of written by a model. Upgrade it to\n")
    body.append("       model-written prose later with update --full.\n\n")

    body.append("  [3]", style=BRAND_STYLE)
    body.append("  Advanced\n", style="bold")
    body.append("       Full control — turn AI docs on or off, then tune indexing\n")
    body.append("       and generation (commit limit, exclude patterns, concurrency …)")

    console.print(
        Panel(
            body,
            title="[bold]How would you like to index this repo?[/bold]",
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


def interactive_generate_docs_toggle(console: Console) -> bool:
    """Advanced-mode entry: ask whether to generate AI wiki docs.

    Returns True for a full doc-generating run, False for an index-only run that
    still flows through the indexing-configuration prompts. Indexing (graph, git,
    code health, dead code) is free; only doc generation calls the LLM.
    """
    console.print()
    console.print(
        "  [dim]Model-written docs call the LLM (has a cost). Say no and you still "
        "get a wiki,\n  rendered from the code's structure, for free.[/dim]"
    )
    return click.confirm("  Generate model-written wiki docs?", default=True)


def interactive_customize_offer(console: Console, *, generate_docs: bool) -> bool:
    """Offer to drop into advanced configuration from full / index-only modes.

    Returns True when the user wants to tune the defaults rather than accept
    them. The label tracks whether generation knobs are in scope.
    """
    what = "indexing & generation" if generate_docs else "indexing"
    console.print()
    return click.confirm(f"  Customize {what} options?", default=False)


def prompt_wiki_style(console: Console) -> str:
    """Interactive picker for the wiki documentation style.

    Returns the chosen style name. Defaults to the comprehensive baseline so a
    bare Enter keeps today's behaviour.
    """
    styles = list_styles()
    console.print("\n[bold]Documentation style[/bold]")
    for idx, spec in enumerate(styles, 1):
        marker = " [dim](default)[/dim]" if spec.name == DEFAULT_STYLE else ""
        console.print(f"  {idx}. [cyan]{spec.name}[/cyan]{marker} — {spec.description}")
    default_idx = next((i for i, s in enumerate(styles, 1) if s.name == DEFAULT_STYLE), 1)
    choice = click.prompt(
        "  Choose a style",
        type=click.IntRange(1, len(styles)),
        default=default_idx,
        show_default=True,
    )
    return styles[choice - 1].name


def prompt_language(console: Console) -> str:
    """Interactive picker for the wiki output language.

    Returns the chosen language code. Defaults to English so a bare Enter
    keeps today's behaviour.
    """
    console.print("\n[bold]Output language[/bold]")
    console.print(
        "  [dim]"
        + " · ".join(f"{code} {name}" for code, name in SUPPORTED_LANGUAGES.items())
        + "[/dim]"
    )
    console.print("  [dim]Code, file paths, and symbol names stay untranslated.[/dim]")
    return click.prompt(
        "  Language code",
        default="en",
        show_default=True,
        type=click.Choice(sorted(SUPPORTED_LANGUAGES)),
        show_choices=False,
    )


def _prompt_scope(console: Console, scan: RepoScanInfo | None, result: dict[str, Any]) -> None:
    """Scope section: which file classes to include."""
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


def _prompt_run_mode(
    console: Console,
    result: dict[str, Any],
    *,
    allow_fast: bool,
    is_large: bool,
) -> None:
    """Run-mode section (large-repo scale). Only offered for single-repo init."""
    # Fast mode = quick graph + essential-git index, no LLM docs. Suggested
    # by default on large repos; off otherwise. Only offered for single-repo
    # init (allow_fast); the workspace path leaves this untouched.
    if allow_fast:
        console.print()
        console.print(f"  [{BRAND}]Run mode[/]")
        console.print(
            "  [dim]standard = full depth · fast = quick graph + essential git, no LLM docs[/dim]"
        )
        result["run_mode"] = click.prompt(
            "  Run mode",
            default="fast" if is_large else "standard",
            type=click.Choice(["standard", "fast"]),
        )
    else:
        result["run_mode"] = "standard"


def _prompt_exclude(
    console: Console, scan: RepoScanInfo | None, result: dict[str, Any]
) -> list[str]:
    """Exclude-patterns section. Returns the parsed pattern list."""
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
    seen_patterns: set[str] = set()
    while True:
        raw = click.prompt("  Pattern", default="", show_default=False)
        raw = raw.strip()
        if not raw:
            break
        # Support comma-separated input; dedupe so re-pasting / re-entering
        # the same suggestions doesn't bloat the summary panel.
        for part in raw.split(","):
            part = part.strip()
            if part and part not in seen_patterns:
                seen_patterns.add(part)
                patterns.append(part)
    result["exclude"] = tuple(patterns)
    return patterns


def _prompt_git(console: Console, scan: RepoScanInfo | None, result: dict[str, Any]) -> None:
    """Git-analysis section: commit limit + rename following."""
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
    val = max(1, min(val, 10000))
    result["commit_limit"] = val

    result["follow_renames"] = click.confirm(
        "  Track files across git renames? (slower but more accurate)",
        default=False,
    )


def _prompt_generation(
    console: Console,
    scan: RepoScanInfo | None,
    result: dict[str, Any],
    *,
    allow_fast: bool,
    is_large: bool,
    prompt_reasoning: bool = True,
    wiki_style: str | None = None,
    language: str | None = None,
) -> None:
    """Generation section: concurrency, reasoning, embedder, test run, tiering,
    onboarding, decision harvesting, wiki style, and output language.

    *wiki_style* carries an explicit ``--wiki-style`` value; when set the style
    prompt is skipped so the flag wins. *language* works the same way for the
    ``--language`` flag.
    """
    console.print()
    console.print(f"  [{BRAND}]Generation[/]")
    console.print("  [dim]LLM page generation settings[/dim]")
    console.print()

    # Smart concurrency default
    default_concurrency = 10
    if scan and scan.total_files < 200:
        default_concurrency = 12
    elif scan and scan.total_files > 5000:
        default_concurrency = 5

    result["concurrency"] = click.prompt(
        "  Max concurrent LLM calls",
        default=default_concurrency,
        type=int,
    )

    if prompt_reasoning:
        result["reasoning"] = click.prompt(
            "  Reasoning mode",
            default="auto",
            type=click.Choice(REASONING_MODES),
        )
    else:
        result["reasoning"] = None

    # Embedder selection
    detected_embedder = _resolve_embedder_from_env()
    embedder_choices = ["gemini", "openai", "openrouter", "ollama", "mock"]
    result["embedder"] = click.prompt(
        "  Embedder for RAG",
        default=detected_embedder,
        type=click.Choice(embedder_choices),
    )

    result["test_run"] = click.confirm(
        "  Test run? (full ingestion; LLM page generation limited to top 10 files for quick validation)",
        default=False,
    )

    # Curated onboarding collection (up to 8 overview pages) — extra LLM cost,
    # slots without enough signal are skipped automatically.
    result["onboarding"] = click.confirm(
        "  Generate the curated Onboarding collection? (up to 8 overview pages)",
        default=True,
    )

    # Decision harvesting rides along with file-page generation; the token cost
    # lands only on files that actually carry a decision.
    result["harvest_decisions"] = click.confirm(
        "  Harvest architectural decisions during generation?",
        default=True,
    )

    # Tiered doc generation: cap the number of full-LLM (tier-1) file pages on
    # large repos. The long tail is rendered from a deterministic template +
    # embedded for search (no LLM). 0 = no cap (every selected page is tier-1).
    # Only meaningful when docs actually generate (standard mode). This caps the
    # full-LLM pages; the coverage chooser (shown later) sets how many files are
    # documented at all.
    result["tier1_top_n"] = None
    if allow_fast and result["run_mode"] == "standard":
        tier_default = 300 if is_large else 0
        console.print()
        console.print(
            "  [dim]Tiered docs: cap full-LLM file pages; rest are template-only. "
            "Coverage (how many files to document) is chosen just before generation.[/dim]"
        )
        tier_val = click.prompt(
            "  Full-LLM file-page cap (tier-1, 0 = no cap)",
            default=tier_default,
            type=int,
        )
        result["tier1_top_n"] = tier_val if tier_val > 0 else None

    # Deterministic coverage tail: give every remaining (budget-dropped) source
    # file a free, no-LLM page so the whole codebase is retrievable by search.
    result["tier2_tail_enabled"] = True
    if result["run_mode"] == "standard":
        console.print()
        console.print(
            "  [dim]Recommended: keep this on so search and get_answer can find "
            "every source file, not just the documented slice (free, no tokens).[/dim]"
        )
        result["tier2_tail_enabled"] = click.confirm(
            "  Document remaining files deterministically (no-LLM coverage)?",
            default=True,
        )

    # Documentation voice/density. An explicit --wiki-style wins; otherwise prompt
    # here so the choice lands inside the section, before the summary panel.
    result["wiki_style"] = wiki_style if wiki_style is not None else prompt_wiki_style(console)

    # Output language. An explicit --language wins; otherwise prompt (English default).
    result["language"] = language if language is not None else prompt_language(console)


def _build_summary_table(
    result: dict[str, Any],
    patterns: list[str],
    *,
    allow_fast: bool,
    generate_docs: bool = True,
) -> Table:
    """Build the configuration-summary table from the gathered answers.

    Generation rows are shown only when *generate_docs* is True, so an
    index-only advanced run doesn't list knobs that never apply.
    """
    summary = Table(box=None, padding=(0, 2), show_header=False)
    summary.add_column("Option", style="dim")
    summary.add_column("Value", style="bold")

    # ── Indexing (always) ──────────────────────────────────────────────────
    summary.add_row("Generate docs", "yes" if generate_docs else "no (index only)")
    summary.add_row("Skip tests", "yes" if result["skip_tests"] else "no")
    summary.add_row("Skip infra", "yes" if result["skip_infra"] else "no")
    if result["include_submodules"]:
        summary.add_row("Include submodules", "yes")
    summary.add_row("Commit limit", str(result["commit_limit"]))
    summary.add_row("Follow renames", "yes" if result["follow_renames"] else "no")
    if allow_fast:
        summary.add_row("Run mode", result["run_mode"])
    if patterns:
        if len(patterns) <= 5:
            summary.add_row("Exclude", ", ".join(patterns))
        else:
            # Bullet-list when many patterns — comma-joined wraps unreadably.
            summary.add_row("Exclude", "\n".join(f"• {p}" for p in patterns))

    if not generate_docs and result.get("embedder"):
        summary.add_row("Embedder", result["embedder"])

    # ── Generation (docs only) ─────────────────────────────────────────────
    if generate_docs:
        summary.add_row("Concurrency", str(result["concurrency"]))
        if result.get("reasoning"):
            summary.add_row("Reasoning", result["reasoning"])
        summary.add_row("Embedder", result["embedder"])
        if result.get("wiki_style"):
            summary.add_row("Wiki style", result["wiki_style"])
        if result.get("language") and result["language"] != "en":
            summary.add_row(
                "Language",
                f"{result['language']} ({SUPPORTED_LANGUAGES.get(result['language'], '?')})",
            )
        if allow_fast and result.get("tier1_top_n"):
            summary.add_row("Full-LLM page cap", str(result["tier1_top_n"]))
        summary.add_row(
            "Deterministic coverage",
            "yes" if result.get("tier2_tail_enabled", True) else "no",
        )
        summary.add_row("Onboarding", "yes" if result.get("onboarding", True) else "no")
        summary.add_row(
            "Harvest decisions", "yes" if result.get("harvest_decisions", True) else "no"
        )
        summary.add_row("Test run", "yes" if result["test_run"] else "no")
    return summary


def interactive_advanced_config(
    console: Console,
    scan: RepoScanInfo | None = None,
    *,
    allow_fast: bool = False,
    prompt_reasoning: bool = True,
    generate_docs: bool = True,
    wiki_style: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Prompt for advanced init options, grouped into logical sections.

    When *scan* is provided, uses it for smart defaults and contextual hints
    (file counts, suggested exclude patterns, etc.).

    The indexing section (scope, run mode, exclude, git) is always prompted.
    The generation section (concurrency, reasoning, embedder, onboarding,
    decision harvesting, tiering, wiki style, test run) is prompted only when
    *generate_docs* is True, so an index-only advanced run skips knobs that have
    no effect. ``generate_docs`` is echoed back in the result.

    Returns a dict with keys matching init_command kwargs:
    ``commit_limit``, ``follow_renames``, ``skip_tests``, ``skip_infra``,
    ``exclude``, ``include_submodules``, ``run_mode``, ``generate_docs`` (always),
    plus ``concurrency``, ``reasoning``, ``embedder``, ``test_run``,
    ``tier1_top_n``, ``onboarding``, ``harvest_decisions``, ``wiki_style``,
    ``language`` (docs only).

    Editor integration prompts are intentionally not asked here so that full and
    advanced modes stay aligned. Editor setup owns those prompts after mode
    selection.
    """
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Advanced Configuration[/]",
            style=DIM,
        )
    )

    result: dict[str, Any] = {}
    is_large = bool(scan and scan.total_files > LARGE_REPO_FILE_THRESHOLD)

    _prompt_scope(console, scan, result)
    _prompt_run_mode(console, result, allow_fast=allow_fast, is_large=is_large)
    patterns = _prompt_exclude(console, scan, result)
    _prompt_git(console, scan, result)
    if generate_docs:
        _prompt_generation(
            console,
            scan,
            result,
            allow_fast=allow_fast,
            is_large=is_large,
            prompt_reasoning=prompt_reasoning,
            wiki_style=wiki_style,
            language=language,
        )
    else:
        _prompt_index_only_search(console, result)
    result["generate_docs"] = generate_docs

    # ── Summary ───────────────────────────────────────────────────────────
    console.print()
    summary = _build_summary_table(
        result, patterns, allow_fast=allow_fast, generate_docs=generate_docs
    )
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


def _prompt_index_only_search(console: Console, result: dict[str, Any]) -> None:
    """Ask an index-only run which embedder its template wiki should use.

    Index-only renders a full wiki now, and those pages embed like any other,
    so the choice is real. It is not asked outside advanced mode because the
    honest default is the one that cannot bill anyone: a hosted embedder is
    picked up automatically from an API key in the environment, and this mode
    promises no spend. Choosing one here counts as asking for it.
    """
    console.print()
    console.print(f"  [{BRAND}]Search[/]")
    console.print(
        "  [dim]Full-text search always works. Semantic search needs an embedder;\n"
        "  ollama is the keyless one, and the hosted ones charge per page.[/dim]"
    )
    console.print()
    result["embedder"] = click.prompt(
        "  Embedder for semantic search (mock = full-text only)",
        default="mock",
        type=click.Choice(["mock", "ollama", "gemini", "openai", "openrouter"]),
    )


def _resolve_embedder_from_env() -> str:
    """Auto-detect embedder from env vars (for advanced config default)."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("OLLAMA_EMBEDDING_MODEL"):
        return "ollama"
    return "mock"


def print_index_only_intro(console: Console, has_provider: bool = False) -> None:
    """Show what index-only mode will do before starting."""
    lines = [
        "  [green]✓[/] Parse all source files (AST)",
        "  [green]✓[/] Build dependency graph (PageRank, communities)",
        "  [green]✓[/] Index git history (hotspots, ownership, co-changes)",
        "  [green]✓[/] Detect dead code",
        "  [green]✓[/] Extract architectural decisions",
        "  [green]✓[/] Render the wiki from structure: file, module, layer and",
        "    cycle pages, the architecture diagram, the repo overview, API and",
        "    infra pages, and the onboarding collection",
        "  [green]✓[/] Set up MCP server for AI assistants",
    ]
    if has_provider:
        lines.append(
            "  [green]✓[/] [dim]Decision extraction enhanced (provider key detected)[/dim]"
        )
    lines.append("")
    lines.append("  [dim]No LLM calls. No API key. No cost.[/dim]")
    lines.append(
        "  [dim]Every page says it was derived from structure. Run "
        "[bold]repowise update --full[/bold][/dim]"
    )
    lines.append("  [dim]later to rewrite the wiki with a model.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Index Only[/bold]",
            border_style=BRAND,
            padding=(1, 1),
        )
    )
    console.print()
