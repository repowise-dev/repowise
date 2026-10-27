"""The one panel ``repowise init`` ends on.

Read top to bottom it answers, in order: what did I get, can my agent reach it,
what do I ask it, what do I run next, and what did init leave in my repo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import BRAND, ERR, INFO, MODEL, OK, WARN, key_value_table
from repowise.cli.ui.mode_names import INDEX_ONLY, completion_title, mode_label, scope_line
from repowise.cli.ui.result_panels import build_completion_panel, print_completion_panel


@dataclass(frozen=True)
class StoodOut:
    """What the run measured that is worth a look; ``None`` means not measured."""

    health_score: float | None = None
    #: ``grading.mark_for`` of the score; None when too little was analysed to grade.
    health_mark: str | None = None
    lowest_file: str | None = None
    lowest_score: float | None = None
    dead_unreachable: int | None = None
    #: Findings of any kind that pass the deletion gate (a whole file never does).
    dead_safe: int | None = None
    hotspot_file: str | None = None
    hotspot_commits: int | None = None
    decision_candidates: int | None = None

    def as_summary(self) -> dict[str, Any]:
        """The same findings for ``--progress json``, one key each, null when not measured."""
        health = None
        if self.health_score is not None:
            health = {
                "score": self.health_score,
                "band": self.health_mark,
                "lowest_file": self.lowest_file,
                "lowest_score": self.lowest_score,
            }
        dead = None
        if self.dead_unreachable is not None:
            dead = {
                "unreachable_files": self.dead_unreachable,
                "findings_safe_to_delete": self.dead_safe,
            }
        hotspot = None
        if self.hotspot_file:
            hotspot = {"file": self.hotspot_file, "commits": self.hotspot_commits}
        return {
            "health": health,
            "dead_code": dead,
            "hotspot": hotspot,
            "decision_candidates": self.decision_candidates,
        }


@dataclass(frozen=True)
class ModelUpgrade:
    """The command that adds a model, and the source on this machine it uses."""

    command: str
    #: e.g. "Uses your Claude Code sign-in"; None when nothing was detected.
    source: str | None = None


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
    top_hotspot: str = ""
    #: The documentation count, e.g. "42 pages"; None when none was built.
    pages: str | None = None
    #: Model spend, e.g. "$0.12 (40,000 tokens)"; None for a keyless run.
    spend: str | None = None
    embed_failed: int = 0
    stood_out: StoodOut = field(default_factory=StoodOut)
    #: The "add a model" block of a run whose documentation was built from the index.
    model_upgrade: ModelUpgrade | None = None


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
        ("Elapsed", facts.elapsed),
    ]
    return rows


_MARK_STYLE = {"healthy": OK, "warning": WARN, "alert": ERR}


def _health_value(s: StoodOut) -> str:
    from repowise.core.analysis.health.grading import MARK_LABEL, format_score

    figure = f"{format_score(s.health_score)}/10"
    if s.health_mark:
        figure = f"[{_MARK_STYLE[s.health_mark]}]{figure} {MARK_LABEL[s.health_mark]}[/]"
    if s.lowest_file and s.lowest_score is not None:
        figure += f"  {escape(s.lowest_file)} is lowest at {format_score(s.lowest_score)}"
    return figure


def _dead_value(s: StoodOut) -> str:
    if s.dead_unreachable:
        noun = "file" if s.dead_unreachable == 1 else "files"
        value = f"{s.dead_unreachable:,} {noun} nothing reaches"
    else:
        value = "no unreachable files"
    if s.dead_safe:
        value += f", {_n(s.dead_safe, 'finding')} safe to delete"
    return value


def _hotspot_value(s: StoodOut) -> str:
    value = escape(s.hotspot_file or "")
    if s.hotspot_commits:
        value += f", changed in {_n(s.hotspot_commits, 'commit')}"
    return value


def _decisions_value(s: StoodOut) -> str:
    count = s.decision_candidates or 0
    noun = "candidate" if count == 1 else "candidates"
    return f"{count:,} {noun} to review"


def stood_out_lines(s: StoodOut) -> list[RenderableType]:
    """Up to four findings from data the run already has; an unmeasured one is left out."""
    rows: list[tuple[str, str]] = []
    if s.health_score is not None:
        rows.append(("Health", _health_value(s)))
    if s.dead_unreachable is not None:
        rows.append(("Dead code", _dead_value(s)))
    if s.hotspot_file:
        rows.append(("Hotspot", _hotspot_value(s)))
    if s.decision_candidates is not None:
        rows.append(("Decisions", _decisions_value(s)))
    if not rows:
        return []
    return [Text(""), line("[bold]What stood out[/bold]"), key_value_table(rows, label_width=10, value_style=INFO)]


def model_upgrade_lines(upgrade: ModelUpgrade | None) -> list[RenderableType]:
    """What a model adds and the exact command for the source this machine has."""
    if upgrade is None:
        return []
    command = f"[{MODEL}]{escape(upgrade.command)}[/]"
    if upgrade.source:
        how = f"{escape(upgrade.source)}: {command}"
    else:
        how = f"{command} needs a key, or [{MODEL}]--provider ollama[/] for a local model."
    return [
        Text(""),
        line(f"[bold {MODEL}]Add a model when you want more[/]"),
        line(
            "Docs that explain how and why, chat in the dashboard, "
            "written answers for your agent."
        ),
        line(how),
        dim_line("Or press m next time on the plan screen."),
    ]


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
    body: list[RenderableType] = [
        *stood_out_lines(facts.stood_out),
        *model_upgrade_lines(facts.model_upgrade),
    ]
    connected = connected_lines(setup)
    if connected:
        body.append(Text(""))
        body += connected
    body += ask_lines(agent_questions(facts.top_hotspot))
    footer = _footer(facts, setup, repo_path or Path("."), files_written or [])
    return escape(completion_title(facts.scope)), _metrics(facts), body, footer
