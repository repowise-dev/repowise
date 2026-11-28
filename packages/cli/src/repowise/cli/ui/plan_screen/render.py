"""The init plan screen: one readout of what Enter runs, and the keys that change it."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text

from repowise.cli.ui.brand import BRAND, WARN, key_value_table
from repowise.cli.ui.mode_names import BUILT_FROM_CODE, INDEX_ONLY, written_by
from repowise.cli.ui.mode_selection import LARGE_REPO_FILE_THRESHOLD
from repowise.cli.ui.plan_screen.state import (
    PlanState,
    eta_phrase,
    eta_seconds,
    source_and_languages,
)
from repowise.core.index_scope import FAST_TO_FULL_COMMAND

#: What ``e`` opens, named so the key is not a blind door.
EDIT_PREVIEW = (
    "scope, run mode, excludes, commit limit, file pages, embedder, agents and "
    "sync, keeping machine-specific files out of git, plus concurrency, style and language with a model"
)


def _say(console: Console, markup: str, indent: int = 4) -> None:
    """One line, wrapped under its own indent rather than at column 0."""
    console.print(Padding(Text.from_markup(markup), (0, 0, 0, indent)))


def _model_adds(state: PlanState) -> str:
    search = (
        f"semantic search with the {escape(state.model_embedder)} embedder"
        if state.model_embedder != "mock"
        else "semantic search once an embedder is set up"
    )
    return (
        "Adds written subsystem documentation, decisions from PRs, git history "
        f"and comments, and {search}."
    )


def _scan_line(state: PlanState) -> str:
    scan = state.scan
    source, languages = source_and_languages(scan)
    parts = [f"[bold]{escape(state.repo_name)}[/bold]", f"{scan.total_files:,} files scanned"]
    if source:
        parts.append(f"{source:,} source")
    if languages:
        parts.append(f"{languages} language" + ("" if languages == 1 else "s"))
    if scan.total_commits:
        parts.append(f"{scan.total_commits:,} commits")
    return "  " + " · ".join(parts)


def _eta_line(state: PlanState) -> str:
    what = "to index" if state.run_mode == "fast" else "to index and build the documentation"
    if state.chosen is not None:
        what = "to index and write the documentation"
    phrase = eta_phrase(*eta_seconds(state))
    return f"  {phrase[0].upper()}{phrase[1:]} {what}."


def _documentation_rows(state: PlanState) -> list[tuple[str, str]]:
    if state.run_mode == "fast":
        return [
            ("Documentation", f"{INDEX_ONLY}: none this run"),
            ("", f"[dim]{FAST_TO_FULL_COMMAND} adds it later[/dim]"),
        ]
    if state.chosen is not None:
        return [("Documentation", written_by(escape(state.chosen.model_label)))]
    return [
        ("Documentation", BUILT_FROM_CODE),
        ("", "[dim]a model can write it later: repowise generate[/dim]"),
    ]


def _search_row(state: PlanState) -> str:
    if state.run_mode == "fast":
        return "no documentation to search this run"
    embedder = state.model_embedder if state.chosen is not None else state.free_embedder
    if embedder == "mock":
        return "full-text · semantic search needs an embedder (choose one under e)"
    return f"full-text and semantic search ({escape(embedder)} embedder)"


def _decisions_row(state: PlanState) -> str:
    if state.chosen is not None:
        return "from code markers, ADR files, PRs, git history and comments"
    return "from code markers and ADR files"


def _connect_rows(state: PlanState) -> list[tuple[str, str]]:
    if not state.editor_setup:
        off = "off (--no-editor-setup)"
        return [("Connect", off), ("Keep in sync", off)]
    agents = ", ".join(escape(a) for a in state.agents) or "no agents"
    sync = (
        "repowise update after each commit (post-commit hook)"
        if state.sync
        else "off: run repowise update yourself"
    )
    return [("Connect", agents), ("Keep in sync", sync)]


def _keep_out_line(state: PlanState) -> str:
    """The ``.git/info/exclude`` offer, on or off in words."""
    files = ", ".join(escape(p) for p in state.keep_out)
    if state.git_exclude:
        return f"Keep out of git: {files} [dim](adds to .git/info/exclude)[/dim]"
    them = "it" if len(state.keep_out) == 1 else "them"
    return f"[dim]Keep out of git: off for {files} (e adds {them} to .git/info/exclude)[/dim]"


def _plan_heading(state: PlanState) -> str:
    if state.chosen is None:
        return f"  [{BRAND}]The plan[/]  [dim]free, runs on this machine[/dim]"
    chosen = state.chosen
    return (
        f"  [{BRAND}]The plan[/]  [dim]uses {escape(chosen.provider)} / "
        f"{escape(chosen.model_label)}: {escape(chosen.cost)}, about {chosen.minutes:,} min more[/dim]"
    )


def _print_model_offer(console: Console, state: PlanState) -> None:
    """The opt-in line: what a model adds, what it costs, and that it waits for ``m``."""
    if state.chosen is not None or state.run_mode == "fast":
        return
    console.print()
    console.print(f"  [{BRAND}]Optional[/]  {written_by()}, used only if you press m")
    price = state.offer.price
    if price is None:
        _say(
            console,
            "No key found. m sets up an API key or a logged-in Claude Code, Codex or OpenCode.",
        )
    else:
        _say(
            console,
            f"{escape(state.offer.found)}: {escape(price.provider)} / {escape(price.model_label)}",
        )
        _say(console, f"[dim]{escape(price.cost)}, about {price.minutes:,} min more[/dim]")
    _say(console, f"[dim]{_model_adds(state)}[/dim]")


def _print_outside(console: Console, state: PlanState) -> None:
    """D2: where the one global MCP entry points now, and where it will point."""
    if not state.editor_setup:
        return
    if not state.outside:
        return
    console.print()
    console.print(f"  [{WARN}]![/] [bold]Outside this repo[/bold]")
    if state.this_repo_only:
        _say(
            console,
            "Global repowise MCP entry left as is. Agents opened in this repo "
            "still connect through its project files.",
        )
        _say(console, "[dim]Press s to point it here after all.[/dim]")
        return
    for line in state.outside:
        _say(console, escape(line.strip()))
    _say(console, "[dim]Press s to leave it as is and connect this repo only.[/dim]")


def _key_line(state: PlanState, extra_keys: tuple[tuple[str, str], ...]) -> str:
    model_key = "back to built from code" if state.chosen is not None else "add a model"
    keys = [("Enter", "start"), ("m", model_key), ("e", "edit plan"), *extra_keys]
    if state.can_keep_global:
        keys.append(("s", "point it here" if state.this_repo_only else "this repo only"))
    keys.append(("q", "quit"))
    return "  " + "   ".join(f"[bold {BRAND}]{k}[/] {label}" for k, label in keys)


def render_plan(console: Console, state: PlanState) -> None:
    """Print the plan screen for *state*."""
    _say(console, _scan_line(state).strip(), indent=2)
    _say(console, _eta_line(state).strip(), indent=2)
    if state.large_repo and state.run_mode != "fast":
        _say(
            console,
            f"[dim]Above {LARGE_REPO_FILE_THRESHOLD:,} files a fast first index "
            f"({INDEX_ONLY.lower()}) is recommended: choose run mode fast under e.[/dim]",
            indent=2,
        )
    render_plan_body(console, state)


def render_plan_body(
    console: Console,
    state: PlanState,
    *,
    extra_keys: tuple[tuple[str, str], ...] = (),
    edit_preview: str = EDIT_PREVIEW,
) -> None:
    """The plan rows, the model opt-in, the outside line and the keys.

    Shared by the single-repo and the workspace screen; each prints its own
    header above it and may offer *extra_keys* before ``s`` and ``q``.
    """
    console.print()
    console.print(_plan_heading(state))
    rows = [
        ("Index", "code graph, git history, health, dead code"),
        ("Decisions", _decisions_row(state)),
        *_documentation_rows(state),
        ("Search", _search_row(state)),
        *_connect_rows(state),
    ]
    console.print(key_value_table(rows, label_width=14, value_style=""), highlight=False)
    if state.keep_out:
        _say(console, _keep_out_line(state), indent=2)
    _print_model_offer(console, state)
    _print_outside(console, state)
    console.print()
    _say(console, _key_line(state, extra_keys).strip(), indent=2)
    _say(console, f"[dim]e covers {edit_preview}.[/dim]", indent=2)
