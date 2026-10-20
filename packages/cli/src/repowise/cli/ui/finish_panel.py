"""The one panel ``repowise init`` ends on.

Read top to bottom it answers, in order: what did I get, can my agent reach it,
what do I ask it, what do I run next, and what did init leave in my repo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import BRAND, WARN, key_value_table
from repowise.cli.ui.mode_names import INDEX_ONLY, completion_title, mode_label, scope_line
from repowise.cli.ui.result_panels import build_completion_panel, print_completion_panel


@dataclass(frozen=True)
class FinishFacts:
    """What the run produced, already counted; the panel only formats it."""

    scope: Mapping[str, Any]
    files: int
    symbols: int
    languages: str
    graph_nodes: int
    graph_edges: int
    elapsed: str
    dead_unreachable: int = 0
    dead_unused: int = 0
    decisions: int = 0
    #: None when the run read no git history.
    git_files: int | None = None
    hotspots: int = 0
    top_hotspot: str = ""
    #: The documentation count, e.g. "42 pages"; None when none was built.
    pages: str | None = None
    #: Model spend, e.g. "$0.12 (40,000 tokens)"; None for a keyless run.
    spend: str | None = None
    embed_failed: int = 0


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _no_pages(scope: Mapping[str, Any]) -> str:
    return f"none ({INDEX_ONLY})" if mode_label(scope) == INDEX_ONLY else "none"


def _search_label(facts: FinishFacts) -> str:
    if facts.embed_failed:
        return f"full-text (embedding failed for {_n(facts.embed_failed, 'page')})"
    if (facts.scope.get("search") or {}).get("semantic") == "available":
        return "full-text and semantic"
    return "full-text only"


def _metrics(facts: FinishFacts) -> list[tuple[str, str]]:
    rows = [
        ("Scope", escape(scope_line(facts.scope))),
        (
            "Indexed",
            f"{_n(facts.files, 'file')} · {_n(facts.symbols, 'symbol')} · {facts.languages}",
        ),
        ("Documentation", facts.pages or _no_pages(facts.scope)),
    ]
    if facts.spend:
        rows.append(("Cost", facts.spend))
    if facts.pages:
        rows.append(("Search", _search_label(facts)))
    rows += [
        ("Graph", f"{_n(facts.graph_nodes, 'node')} · {_n(facts.graph_edges, 'edge')}"),
        (
            "Dead code",
            f"{_n(facts.dead_unreachable, 'unreachable file')} · "
            f"{_n(facts.dead_unused, 'unused export')}",
        ),
        ("Decisions", _n(facts.decisions, "decision")),
    ]
    if facts.git_files is not None:
        rows.append(
            ("Git history", f"{_n(facts.git_files, 'file')} · {_n(facts.hotspots, 'hotspot')}")
        )
    rows.append(("Elapsed", facts.elapsed))
    return rows


def line(markup: str, indent: int = 2) -> Padding:
    """One wrapped line; wrapped text keeps the indent."""
    return Padding(Text.from_markup(markup), (0, 0, 0, indent))


def dim_line(markup: str, indent: int = 2) -> Padding:
    return line(f"[dim]{markup}[/dim]", indent)


def connected_lines(setup: Any) -> list[RenderableType]:
    """Which agent can reach the index, and the restart it needs to see it.

    Silent without a setup snapshot. A run with editor setup off gets the
    ``repowise mcp .`` step row instead of a line here.
    """
    if setup is None:
        return []
    if getattr(setup, "editor_setup_disabled", False):
        return [key_value_table([("Connected", "no editor (editor setup was skipped)")])]

    lines: list[RenderableType] = []
    if getattr(setup, "claude_code_connected", False):
        lines.append(key_value_table([("Connected", "Claude Code")]))
        # A first index is the run that wrote the MCP entry, so the client
        # has not loaded it yet.
        if getattr(setup, "first_index", True):
            lines.append(
                line(
                    f"[bold {BRAND}]Restart Claude Code[/] [bold](or run /mcp) "
                    "to load the repowise tools.[/bold]"
                )
            )
        else:
            lines.append(
                dim_line("Claude Code stays connected; restart it only if the tools are not showing.")
            )
    lines.append(
        dim_line("Cursor or Codex: run [bold]repowise mcp .[/bold] (config in .repowise/mcp.json).")
    )
    # The interactive run offered this hook live; a --yes run skipped the offer.
    if not getattr(setup, "interactive", False) and not getattr(
        setup, "rewrite_hook_installed", False
    ):
        lines.append(
            dim_line("[bold]repowise hook rewrite install[/bold] compresses noisy command output.")
        )
    return lines


def agent_questions(top_hotspot: str = "") -> list[str]:
    """Two questions that show off the index on any repo."""
    second = (
        f"Why does {top_hotspot} change so often, and what breaks if I edit it?"
        if top_hotspot
        else "Which files are riskiest to change, and why?"
    )
    return ["How is this repo organised, and where should I start reading?", second]


def ask_lines(questions: list[str]) -> list[RenderableType]:
    lines: list[RenderableType] = [Text(""), line("[bold]Try asking your agent[/bold]")]
    lines += [line(f'[dim]"{escape(q)}"[/dim]', indent=4) for q in questions]
    return lines


def _relative(repo_path: Path, paths: list[Path]) -> list[str]:
    out = []
    for path in paths:
        try:
            out.append(path.relative_to(repo_path).as_posix())
        except ValueError:
            out.append(str(path))
    return out


def _search_lines(facts: FinishFacts) -> list[RenderableType]:
    """The command that turns semantic search on, when documentation exists without it."""
    search = facts.scope.get("search") or {}
    if facts.embed_failed:
        return [
            line(
                f"[{WARN}]Semantic search is unavailable:[/] embedding failed for "
                f"{facts.embed_failed} page(s). See the warning above, then run "
                "[bold]repowise reindex[/bold]."
            )
        ]
    if not facts.pages or search.get("semantic") == "available":
        return []
    command = search.get("next_command") or "repowise reindex"
    return [
        dim_line(
            f"Semantic search is off: [bold]{command}[/bold] turns it on "
            f"({_embed_cost(command)})."
        )
    ]


def _embed_cost(command: str) -> str:
    """Who pays for the embedder the command names; Ollama is the keyless option."""
    from repowise.cli.providers import is_free_embedder
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    args = command.split()
    name = args[args.index("--embedder") + 1] if "--embedder" in args[:-1] else None
    if name is None:
        return "Ollama needs no key"
    if is_free_embedder(name):
        return "runs locally with Ollama, no key"
    # Hosted embedders share their provider's name, so its catalog label names the key.
    spec = PROVIDER_SPECS.get(name)
    return f"uses your {spec.label if spec else name} key"


def _footer(
    facts: FinishFacts, setup: Any, repo_path: Path, files_written: list[Path]
) -> list[RenderableType]:
    lines = _search_lines(facts)
    if files_written:
        lines.append(
            dim_line(
                "Files added: "
                + escape(", ".join(_relative(repo_path, files_written)))
                + " (not gitignored; [bold]--no-editor-setup[/bold] skips them)."
            )
        )
    return lines + autosync_lines(setup)


def autosync_lines(setup: Any) -> list[RenderableType]:
    """Whether the post-commit hook keeps the index synced; silent without editor setup."""
    if setup is None or getattr(setup, "editor_setup_disabled", False):
        return []
    if getattr(setup, "autosync_hook_installed", False):
        return [dim_line("Auto-sync: on, the index updates after each commit.")]
    return [
        dim_line(
            "Auto-sync: off. [bold]repowise hook install[/bold] keeps the index synced on commit."
        )
    ]


def build_finish_panel(
    facts: FinishFacts,
    *,
    steps: list[tuple[str, str]],
    setup: Any = None,
    repo_path: Path | None = None,
    files_written: list[Path] | None = None,
    owl: str = mascot.DONE,
) -> Any:
    """The init completion panel: counts, Connected, questions, steps, receipt.

    *owl* is the title owl's state: how the run finished.
    """
    title, metrics, body, footer = _panel_parts(facts, setup, repo_path, files_written)
    return build_completion_panel(
        title, metrics, next_steps=steps, body=body, footer=footer, owl=owl
    )


def print_finish_panel(
    console: Any,
    facts: FinishFacts,
    *,
    steps: list[tuple[str, str]],
    setup: Any = None,
    repo_path: Path | None = None,
    files_written: list[Path] | None = None,
    owl: str = mascot.DONE,
) -> None:
    """The finish panel on a terminal; the same rows as plain lines off one."""
    title, metrics, body, footer = _panel_parts(facts, setup, repo_path, files_written)
    print_completion_panel(console, title, metrics, steps, body=body, footer=footer, owl=owl)


def _panel_parts(
    facts: FinishFacts,
    setup: Any,
    repo_path: Path | None,
    files_written: list[Path] | None,
) -> tuple[str, list[tuple[str, str]], list[RenderableType], list[RenderableType]]:
    body: list[RenderableType] = []
    connected = connected_lines(setup)
    if connected:
        body.append(Text(""))
        body += connected
    body += ask_lines(agent_questions(facts.top_hotspot))
    footer = _footer(facts, setup, repo_path or Path("."), files_written or [])
    return escape(completion_title(facts.scope)), _metrics(facts), body, footer
