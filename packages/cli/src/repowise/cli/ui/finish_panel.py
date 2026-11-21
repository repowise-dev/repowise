"""The one panel ``repowise init`` ends on.

Read top to bottom it answers, in order: what did I get, can my agent reach it
and what needs a restart, what do I ask it first, what do I run next, and what
did init leave in my repo. The rest waits in ``repowise next`` and the dashboard.
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
from repowise.cli.ui.brand import BRAND, INFO, WARN, key_value_table
from repowise.cli.ui.mode_names import INDEX_ONLY, completion_title, mode_label, scope_line
from repowise.cli.ui.result_panels import build_completion_panel, print_completion_panel


@dataclass(frozen=True)
class StoodOut:
    """What the run measured that is worth a look; ``None`` means not measured."""

    health_score: float | None = None
    #: ``grading.headline_band`` of the score; None when too little was analysed to grade.
    health_band: str | None = None
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
                "band": self.health_band,
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


#: The footer line for an agent: relay the summary, and leave the choice to the person.
AGENT_LINE = (
    "Repo report: tell the user what it found (repowise report --format md); "
    "they decide what to fix next."
)
#: How an agent reads the report, as init's JSON summary names it.
SUMMARY_COMMAND = "repowise report --format md"
JSON_COMMAND = "repowise report --format json"


@dataclass(frozen=True)
class FirstLook:
    """Where the repo report is and who wrote it: one footer line."""

    #: The app page while ``repowise serve`` runs, else the HTML file's path.
    where: str
    #: ``template``, or the model id that wrote the prose (the report's stamp).
    generator: str = "template"
    cost: float | None = None
    #: An agent or a run nobody is watching: the line tells the agent what to do.
    agent: bool = False
    #: Items no one has answered yet.
    open_items: int = 0

    def line(self) -> str:
        if self.agent:
            return AGENT_LINE
        if self.generator == "template" and self.cost:
            how = f"template: the model's reply was unusable, ${self.cost:.3f}"
        elif self.generator == "template":
            how = "template, free"
        else:
            how = f"written by {self.generator}" + (
                f", ${self.cost:.3f}" if self.cost is not None else ""
            )
        return f"Repo report  {self.where}  ({how})"

    def summary(self) -> dict[str, Any]:
        """The ``report`` object of init's JSON summary."""
        return {
            "open_items": self.open_items,
            "summary_command": SUMMARY_COMMAND,
            "json_command": JSON_COMMAND,
        }


@dataclass(frozen=True)
class FinishFacts:
    """What the run produced, already counted; the panel only formats it."""

    scope: Mapping[str, Any]
    files: int
    symbols: int
    languages: str
    elapsed: str
    #: The one question for the agent, from ``core.agent_prompts.first_prompt``.
    first_prompt: str = ""
    #: The documentation count, e.g. "42 pages"; None when none was built.
    pages: str | None = None
    #: Model spend, e.g. "$0.12 (40,000 tokens)"; None for a keyless run.
    spend: str | None = None
    embed_failed: int = 0
    stood_out: StoodOut = field(default_factory=StoodOut)
    #: The PR bot install link, on the one run that shows it; None on every other.
    pr_bot_url: str | None = None
    #: The repo report this run wrote; None when it wrote none.
    first_look: FirstLook | None = None


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
    rows.append(("Elapsed", facts.elapsed))
    return rows


def _health_value(s: StoodOut) -> str:
    from repowise.core.analysis.health.grading import (
        BAND_LABEL,
        BAND_TERMINAL_COLOR,
        format_score,
    )

    figure = f"{format_score(s.health_score)}/10"
    if s.health_band:
        style = BAND_TERMINAL_COLOR[s.health_band]
        figure = f"[{style}]{figure} {BAND_LABEL[s.health_band]}[/]"
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
    # Mined records are background an agent asks about, not a list to work.
    count = s.decision_candidates or 0
    noun = "record" if count == 1 else "records"
    return f"{count:,} background {noun} from history"


def and_join(names: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _findings(s: StoodOut) -> list[tuple[str, str, str]]:
    """``(label, value, mention)`` for each measured finding, the hotspot first.

    The hotspot leads because the first question is about it.
    """
    out = []
    if s.hotspot_file:
        out.append(("Hotspot", _hotspot_value(s), "the hotspot"))
    if s.health_score is not None:
        out.append(("Health", _health_value(s), "health"))
    if s.dead_unreachable is not None:
        out.append(("Dead code", _dead_value(s), "dead code"))
    if s.decision_candidates:
        records = _decisions_value(s)
        out.append(("Decisions", records, records.removesuffix(" from history")))
    return out


def stood_out_lines(s: StoodOut) -> list[RenderableType]:
    """One headline finding, then one dim line naming the rest; unmeasured ones are left out."""
    findings = _findings(s)
    if not findings:
        return []
    label, value, _ = findings[0]
    lines: list[RenderableType] = [
        Text(""),
        line("[bold]What stood out[/bold]"),
        key_value_table([(label, value)], label_width=10, value_style=INFO),
    ]
    if findings[1:]:
        rest = and_join([mention for _, _, mention in findings[1:]])
        lines.append(dim_line(f"Also in the dashboard: {rest}.", indent=16))
    return lines


def line(markup: str, indent: int = 2) -> Padding:
    """One wrapped line; wrapped text keeps the indent."""
    return Padding(Text.from_markup(markup), (0, 0, 0, indent))


def dim_line(markup: str, indent: int = 2) -> Padding:
    return line(f"[dim]{markup}[/dim]", indent)


#: The agent block's own label column: as wide as "Also installed", so the
#: connect command under it fits a 100-column panel on one line.
_AGENT_LABEL = 14


def connected_lines(setup: Any) -> list[RenderableType]:
    """Which agent can reach the index, and the restart it needs to see it.

    Silent without a setup snapshot. A run with editor setup off gets the
    ``repowise agents add`` step row instead of a line here.
    """
    if setup is None:
        return []
    if getattr(setup, "editor_setup_disabled", False):
        return [
            key_value_table(
                [("Connected", "no editor (editor setup was skipped)")], label_width=_AGENT_LABEL
            )
        ]

    surfaces = getattr(setup, "tool_surfaces", ())
    connected = [s["name"] for s in surfaces]
    if not connected and getattr(setup, "claude_code_connected", False):
        connected = ["Claude Code"]
    lines: list[RenderableType] = []
    restart = [h["name"] for h in getattr(setup, "restart_hosts", ())]
    if connected:
        lines.append(
            key_value_table([("Connected", escape(", ".join(connected)))], label_width=_AGENT_LABEL)
        )
        # Each of these loads its MCP config only at startup, so a host whose
        # config this run wrote or moved has not loaded it yet.
        if restart:
            who = "both" if len(restart) == 2 and set(restart) == set(connected) else and_join(restart)
            restart_line = f"[bold {BRAND}]Restart {escape(who)}[/] to load the repowise tools."
            if "Claude Code" in restart:
                restart_line += " [dim]In Claude Code, /mcp also works.[/dim]"
            lines.append(line(restart_line))
        else:
            lines.append(
                dim_line("Already connected; restart an agent only if the tools are not showing.")
            )
    # The per-agent tool profiles and the always-load switch stay out of this
    # panel: --progress json carries them as mcp_tools, the dashboard's Agents
    # page shows them, and always_load is suggested once usage warrants it.
    unconnected = getattr(setup, "unconnected", ())
    if unconnected:
        from repowise.cli.editor_setup import connect_command

        command = escape(connect_command(a["id"] for a in unconnected))
        lines.append(
            key_value_table(
                [
                    ("Also installed", escape(", ".join(a["name"] for a in unconnected))),
                    ("", f"[bold]{command}[/bold]"),
                ],
                label_width=_AGENT_LABEL,
            )
        )
    # The interactive run offered this hook live; a --yes run skipped the offer.
    if not getattr(setup, "interactive", False) and not getattr(
        setup, "rewrite_hook_installed", False
    ):
        lines.append(
            dim_line("[bold]repowise hook rewrite install[/bold] compresses noisy command output.")
        )
    return lines


def ask_lines(prompt: str) -> list[RenderableType]:
    """The one question to ask once the agent has the tools; nothing without one."""
    if not prompt:
        return []
    return [
        Text(""),
        line("[bold]Then ask your agent[/bold]"),
        line(f'[dim]"{escape(prompt)}"[/dim]', indent=4),
    ]


def _relative(repo_path: Path, paths: list[Path]) -> list[str]:
    out = []
    for path in paths:
        try:
            out.append(path.relative_to(repo_path).as_posix())
        except ValueError:
            out.append(str(path))
    return out


def _search_lines(facts: FinishFacts, *, server: bool = False) -> list[RenderableType]:
    """The command that turns semantic search on, when documentation exists without it.

    A *server* run keeps only the embedding-failed warning: the reindex hint is
    a command for someone at the machine.
    """
    search = facts.scope.get("search") or {}
    if facts.embed_failed:
        return [
            line(
                f"[{WARN}]Semantic search is unavailable:[/] embedding failed for "
                f"{facts.embed_failed} page(s). See the warning above, then run "
                "[bold]repowise reindex[/bold]."
            )
        ]
    if server or not facts.pages or search.get("semantic") == "available":
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
    facts: FinishFacts,
    repo_path: Path,
    files_written: list[Path],
    *,
    server: bool = False,
) -> list[RenderableType]:
    lines = _search_lines(facts, server=server)
    if facts.first_look is not None and not server:
        lines.insert(0, dim_line(escape(facts.first_look.line())))
    if files_written:
        lines.append(
            dim_line(
                "Files added: "
                + escape(", ".join(_relative(repo_path, files_written)))
                + " (not gitignored; [bold]--no-editor-setup[/bold] skips them)."
            )
        )
    if facts.pr_bot_url:
        lines.append(
            dim_line(
                "Review pull requests with this repo's context: add the Repowise PR bot at "
                + escape(facts.pr_bot_url)
            )
        )
    return lines


def build_finish_panel(
    facts: FinishFacts,
    *,
    steps: list[tuple[str, str]],
    setup: Any = None,
    repo_path: Path | None = None,
    files_written: list[Path] | None = None,
    owl: str = mascot.DONE,
) -> Any:
    """The init completion panel: counts, Connected, the first question, steps, receipt.

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
    """The finish panel on a terminal; the same rows as plain lines off one.

    A server run (no terminal and editor setup off: a stack worker, a CI job)
    prints no next steps, agent questions or reindex hint: they are commands
    for someone at the machine, and its log has no one reading them there.
    """
    server = not console.is_terminal and bool(getattr(setup, "editor_setup_disabled", False))
    title, metrics, body, footer = _panel_parts(
        facts, setup, repo_path, files_written, server=server
    )
    print_completion_panel(
        console, title, metrics, [] if server else steps, body=body, footer=footer, owl=owl
    )


def _panel_parts(
    facts: FinishFacts,
    setup: Any,
    repo_path: Path | None,
    files_written: list[Path] | None,
    *,
    server: bool = False,
) -> tuple[str, list[tuple[str, str]], list[RenderableType], list[RenderableType]]:
    body: list[RenderableType] = stood_out_lines(facts.stood_out)
    connected = connected_lines(setup)
    if connected:
        body.append(Text(""))
        body += connected
    if not server:
        body += ask_lines(facts.first_prompt)
    footer = _footer(facts, repo_path or Path("."), files_written or [], server=server)
    return escape(completion_title(facts.scope)), _metrics(facts), body, footer
