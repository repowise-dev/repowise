"""Interactive mode selection, fast-mode offer, and advanced configuration."""

from __future__ import annotations

import os
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from repowise.cli.ui.brand import (
    BRAND,
    BRAND_STYLE,
    DIM,
    VALUE,
    key_value_table,
    print_section,
)
from repowise.cli.ui.repo_scanner import (
    RepoScanInfo,
    estimated_documentable_files,
    estimated_wiki_render_minutes,
)
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.selection import (
    FILE_PAGE_AUTO_CEILING,
    recommended_file_page_cap,
)
from repowise.core.generation.styles import DEFAULT_STYLE, list_styles
from repowise.core.reasoning import REASONING_MODES

# A repo at or above this many files is "large" — large enough that a quick
# fast-mode first index (graph + essential git, no LLM docs) is worth offering.
LARGE_REPO_FILE_THRESHOLD = 5000

# Bytes a file page occupies with its metadata, measured over 1,961 file pages in
# a local index (mean 8.8 KB). Used to quote wiki size while asking; an order of
# magnitude is the point, not a byte count.
_FILE_PAGE_BYTES = 8_800


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


def interactive_mode_select(console: Console, *, title: str | None = None) -> str:
    """Let the user choose full / index-only / advanced.

    All three options index identically; what differs is how much of the wiki a
    model writes, so the panel asks that rather than "how would you like to
    index". *title* overrides the question for callers indexing more than one
    repo (the workspace flow shows this same panel for a whole workspace).

    Returns ``"full"``, ``"index_only"``, or ``"advanced"``.
    """
    body = Text()
    body.append("  [1]", style=BRAND_STYLE)
    body.append("  Everything  ", style="bold")
    body.append("(recommended, needs an API key)\n", style="dim")
    body.append("       Everything repowise can build: dependency graph, git history,\n")
    body.append("       code health, architectural decisions, a model-written wiki,\n")
    body.append("       architecture diagrams and API docs.\n")
    body.append(
        "       Costs a few cents to a few dollars; you see the estimate\n"
        "       before anything runs.\n\n",
        style="dim",
    )

    body.append("  [2]", style=BRAND_STYLE)
    body.append("  No prose  ", style="bold")
    body.append("(no key, no spend)\n", style="dim")
    body.append("       Every page type still gets built, straight from the code.\n")
    body.append("       Only the subsystem pages come out as outlines instead of prose;\n")
    body.append("       add a key later and run repowise generate to fill them in.\n\n")

    body.append("  [3]", style=BRAND_STYLE)
    body.append("  Advanced\n", style="bold")
    body.append("       Full control — turn AI docs on or off, then tune indexing\n")
    body.append("       and generation (commit limit, exclude patterns, concurrency, ...)")

    console.print(
        Panel(
            body,
            title=f"[bold]{title or 'How much should repowise write?'}[/bold]",
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
        console.print(f"  {idx}. [{VALUE}]{spec.name}[/]{marker} — {spec.description}")
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
    print_section(console, "Scope", "Choose what to include in the analysis")
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


def _format_mb(pages: int) -> str:
    """Wiki bytes for *pages* file pages, as a human size."""
    mb = pages * _FILE_PAGE_BYTES / 1_000_000
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"


def prompt_file_page_volume(
    console: Console,
    scan: RepoScanInfo | None,
) -> int | None:
    """Ask a large repo whether to bound the file-page bucket.

    Returns the value for ``GenerationConfig.max_file_pages``: a positive cap to
    take the top slice by importance, ``0`` to refuse any cap (one page per
    eligible file, however many that is), or ``None`` when the repo is small
    enough that there is nothing to ask and the policy leaves it alone.

    One page per source file is what makes small and mid-size repos good, so the
    question is only asked on repos where a cap is worth considering, and only in
    advanced mode. It is asked before ingestion, so the page count is the pre-scan
    estimate.

    The recommended answer is whatever :func:`recommended_file_page_cap` says for
    this size, so the question can never recommend a number the volume policy
    would then override. Above the automatic ceiling, refusing writes an explicit
    ``0`` rather than an unset value, because "every eligible file" has to mean
    that even though the policy would otherwise step in.

    Never blocks: a terminal that cannot answer takes the recommendation and the
    run carries on, same as the other optional questions in an init flow.
    """
    estimate = estimated_documentable_files(scan)
    recommended = recommended_file_page_cap(estimate)
    if recommended is None:
        return None

    would_be_capped_anyway = estimate > FILE_PAGE_AUTO_CEILING
    print_section(
        console,
        "Page volume",
        f"About [bold]{estimate:,}[/bold] files here would each get a file page "
        f"({_format_mb(estimate)} of wiki).\n"
        "  File pages are rendered from structure, so they cost no model tokens — what\n"
        "  the tail costs is wiki size, one embedding call each, and search results\n"
        "  that restate what a subsystem page already says.",
    )
    if would_be_capped_anyway:
        console.print(
            f"  [dim]A repo this size is held to {FILE_PAGE_AUTO_CEILING:,} file pages "
            "unless you say otherwise here.[/dim]"
        )
    console.print()
    console.print(
        f"  [{BRAND}][1][/] Top [bold]{recommended:,}[/bold] by importance  [dim](recommended) — "
        f"~{recommended:,} pages, {_format_mb(recommended)}[/dim]"
    )
    render_min, render_max = estimated_wiki_render_minutes(estimate)
    console.print(
        f"  [{BRAND}][2][/] Every eligible file  [dim]— ~{estimate:,} pages, "
        f"{_format_mb(estimate)}, roughly {render_min:,}-{render_max:,} min "
        "longer to render and embed[/dim]"
    )
    try:
        choice = Prompt.ask("  File pages", choices=["1", "2"], default="1", console=console)
    except (click.Abort, EOFError):
        # isatty() lied about being answerable (Windows Git Bash `< /dev/null`,
        # pty wrappers, `docker run -t` without -i). Take the recommendation and
        # keep going: an agent or CI job must never hang here.
        console.print("  [dim]No answer available — taking the recommendation.[/dim]")
        return recommended
    if choice == "1":
        return recommended
    # Refusing has to survive the policy on a repo the policy would cap, and
    # recording 0 rather than nothing is what says the answer was "all of them"
    # instead of "never asked".
    return 0 if would_be_capped_anyway else None


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
        print_section(
            console,
            "Run mode",
            "standard = full depth · fast = quick graph + essential git, no LLM docs",
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
    print_section(
        console,
        "Exclude patterns",
        "Keep whole directories or globs out of the index entirely",
    )
    console.print()

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
    commit_hint = ""
    if scan and scan.total_commits:
        commit_hint = f" (repo has ~{scan.total_commits:,} total commits)"
    print_section(console, "Git analysis", f"Controls how deeply git history is analyzed{commit_hint}")
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
    onboarding, wiki style, and output language.

    *wiki_style* carries an explicit ``--wiki-style`` value; when set the style
    prompt is skipped so the flag wins. *language* works the same way for the
    ``--language`` flag.
    """
    print_section(console, "Generation", "LLM page generation settings")
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
    embedder_choices = ["gemini", "openai", "openrouter", "ollama", "edenai", "mock"]
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

    # Documentation voice/density. An explicit --wiki-style wins; otherwise prompt
    # here so the choice lands inside the section, before the summary panel.
    result["wiki_style"] = wiki_style if wiki_style is not None else prompt_wiki_style(console)

    # Output language. An explicit --language wins; otherwise prompt (English default).
    result["language"] = language if language is not None else prompt_language(console)


def _summary_rows(
    result: dict[str, Any],
    patterns: list[str],
    *,
    allow_fast: bool,
    generate_docs: bool = True,
) -> list[tuple[str, str]]:
    """The configuration-summary rows gathered from the answers.

    Returns rows rather than a table so the one shared ``key_value_table``
    renders them, which is what keeps this screen's gutter identical to the
    completion panel's instead of merely similar.

    Generation rows are shown only when *generate_docs* is True, so an
    index-only advanced run doesn't list knobs that never apply.
    """
    summary: list[tuple[str, str]] = []

    def add(label: str, value: str) -> None:
        summary.append((label, value))

    # ── Indexing (always) ──────────────────────────────────────────────────
    add("Generate docs", "yes" if generate_docs else "no (index only)")
    add("Skip tests", "yes" if result["skip_tests"] else "no")
    add("Skip infra", "yes" if result["skip_infra"] else "no")
    if result["include_submodules"]:
        add("Include submodules", "yes")
    add("Commit limit", str(result["commit_limit"]))
    add("Follow renames", "yes" if result["follow_renames"] else "no")
    if allow_fast:
        add("Run mode", result["run_mode"])
    if patterns:
        if len(patterns) <= 5:
            add("Exclude", ", ".join(patterns))
        else:
            # Bullet-list when many patterns — comma-joined wraps unreadably.
            add("Exclude", "\n".join(f"• {p}" for p in patterns))

    cap = result.get("max_file_pages")
    if cap:
        add("File pages", f"top {cap:,} by importance")
    elif cap == 0:
        add("File pages", "one per eligible file (uncapped)")

    if not generate_docs and result.get("embedder"):
        add("Embedder", result["embedder"])

    # ── Generation (docs only) ─────────────────────────────────────────────
    if generate_docs:
        add("Concurrency", str(result["concurrency"]))
        if result.get("reasoning"):
            add("Reasoning", result["reasoning"])
        add("Embedder", result["embedder"])
        if result.get("wiki_style"):
            add("Wiki style", result["wiki_style"])
        if result.get("language") and result["language"] != "en":
            add(
                "Language",
                f"{result['language']} ({SUPPORTED_LANGUAGES.get(result['language'], '?')})",
            )
        add("Onboarding", "yes" if result.get("onboarding", True) else "no")
        add("Test run", "yes" if result["test_run"] else "no")
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
    tiering, wiki style, test run) is prompted only when
    *generate_docs* is True, so an index-only advanced run skips knobs that have
    no effect. ``generate_docs`` is echoed back in the result.

    Returns a dict with keys matching init_command kwargs:
    ``commit_limit``, ``follow_renames``, ``skip_tests``, ``skip_infra``,
    ``exclude``, ``include_submodules``, ``run_mode``, ``max_file_pages``,
    ``generate_docs`` (always), plus ``concurrency``, ``reasoning``, ``embedder``,
    ``test_run``, ``onboarding``, ``wiki_style``,
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
    # Asked in both branches: an index-only run renders file pages too (they need
    # no model), so page volume is a real question either way. Fast mode renders
    # no wiki at all, so there is nothing to bound.
    if result.get("run_mode") != "fast":
        result["max_file_pages"] = prompt_file_page_volume(console, scan)
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
    elif result.get("run_mode") != "fast":
        # Fast mode renders no wiki at all, so there would be nothing to embed.
        _prompt_index_only_search(console, result)
    result["generate_docs"] = generate_docs

    # ── Summary ───────────────────────────────────────────────────────────
    # A receipt, not a question: no border. The two panels left on this flow are
    # the two screens that actually ask something.
    print_section(console, "Configuration summary")
    console.print()
    console.print(
        key_value_table(
            _summary_rows(result, patterns, allow_fast=allow_fast, generate_docs=generate_docs),
            label_width=18,
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
    print_section(
        console,
        "Search",
        "Full-text search always works. Semantic search needs an embedder;\n"
        "  ollama is the keyless one, and the hosted ones charge per page.",
    )
    console.print()
    result["embedder"] = click.prompt(
        "  Embedder for semantic search (mock = full-text only)",
        default="mock",
        type=click.Choice(["mock", "ollama", "gemini", "openai", "openrouter", "edenai"]),
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
    # Last, like the shared resolver in cli/providers/embedders.py: an unrelated
    # EDENAI_API_KEY in the environment must not outrank a provider the user was
    # already resolving to.
    if os.environ.get("EDENAI_API_KEY"):
        return "edenai"
    return "mock"


def print_index_only_intro(console: Console, has_provider: bool = False) -> None:
    """Show what index-only mode will do before starting.

    A forecast, so no border — nothing here is a thing to act on, and it used to
    carry the same box as the question two screens earlier.
    """
    print_section(console, "Index only", "No LLM calls. No API key. No cost.")
    console.print()
    # Bullets, not checkmarks: every green ✓ elsewhere in a run means "already
    # done", and this is a forecast.
    lines = [
        "  [dim]•[/dim] Parse all source files (AST)",
        "  [dim]•[/dim] Build dependency graph (PageRank, communities)",
        "  [dim]•[/dim] Index git history (hotspots, ownership, co-changes)",
        "  [dim]•[/dim] Detect dead code",
        "  [dim]•[/dim] Extract architectural decisions",
        "  [dim]•[/dim] Render the wiki from structure: file, module, layer and",
        "    cycle pages, the architecture diagram, the repo overview, API and",
        "    infra pages, and the onboarding collection",
        "  [dim]•[/dim] Set up MCP server for AI assistants",
    ]
    if has_provider:
        lines.append(
            "  [dim]•[/dim] [dim]Decision extraction enhanced (provider key detected)[/dim]"
        )
    for line in lines:
        console.print(line)
    console.print()
    console.print(
        "  [dim]The subsystem pages read as stubs. Add a key and run "
        "[bold]repowise generate[/bold] to write them as prose.[/dim]"
    )
    console.print()
