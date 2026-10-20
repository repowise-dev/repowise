"""The panel a workspace ``repowise init`` ends on.

Same shape as the single-repo finish panel (:mod:`finish_panel`): counts with a
Scope row, then Connected, questions for the agent, serve first, and a short
receipt. The per-repo documentation list sits inside it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rich.console import RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import ERR, OK, WARN, key_value_table
from repowise.cli.ui.finish_panel import (
    agent_questions,
    ask_lines,
    autosync_lines,
    connected_lines,
    dim_line,
    line,
)
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mode_names import (
    INDEX_ONLY,
    TITLE_WORKSPACE,
    UNKNOWN_MODE,
    commit_cap_label,
    mode_label,
    pages,
)
from repowise.cli.ui.result_panels import (
    build_completion_panel,
    print_completion_panel,
    serve_step,
)
from repowise.core.index_scope import fast_to_full_command

#: Repos listed by name; the rest are counted, problems listed first.
MAX_REPO_ROWS = 10
_MAX_FOLLOW_UPS = 2


@dataclass(frozen=True)
class RepoOutcome:
    """One repo's result. ``scope`` is its resolved index scope (empty when it failed)."""

    alias: str
    scope: Mapping[str, Any] = field(default_factory=dict)
    pages: int = 0
    skipped: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class WorkspaceFacts:
    repos: Sequence[RepoOutcome]
    files: int
    symbols: int
    primary: str
    elapsed: str
    #: ``claude_cli / claude-haiku-5-5``; None for a run without a model.
    provider: str | None = None
    #: Nothing was written, so there is no mode to name and nothing to rebuild.
    dry_run: bool = False


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _indexed(facts: WorkspaceFacts) -> list[RepoOutcome]:
    return [r for r in facts.repos if not r.error]


def workspace_scope_line(scopes: Sequence[Mapping[str, Any]]) -> str:
    """``Built from code · git full · last 5,000 commits``, or ``Mixed (...)`` across repos."""
    if not scopes:
        return UNKNOWN_MODE
    labels = Counter(mode_label(s) for s in scopes)
    if len(labels) == 1:
        parts = [next(iter(labels))]
    else:
        counts = ", ".join(f"{_n(n, 'repo')} {label}" for label, n in labels.most_common())
        parts = [f"Mixed ({counts})"]
    tiers = {s.get("git_tier", "unknown") for s in scopes}
    parts.append(f"git {tiers.pop()}" if len(tiers) == 1 else "git mixed")
    caps = {s.get("git_commit_cap") for s in scopes}
    cap = commit_cap_label(caps.pop()) if len(caps) == 1 else None
    if cap:
        parts.append(cap)
    return " · ".join(parts)


def _metrics(facts: WorkspaceFacts) -> list[tuple[str, str]]:
    indexed = _indexed(facts)
    failed = len(facts.repos) - len(indexed)
    repos = f"{len(indexed):,} indexed" + (f" · {failed:,} failed" if failed else "")
    total_pages = sum(r.pages for r in indexed)
    scopes = [r.scope for r in indexed]
    no_docs = "none (Index only)" if {mode_label(s) for s in scopes} == {INDEX_ONLY} else "none"
    rows = [
        (
            "Scope",
            "Dry run, nothing written"
            if facts.dry_run
            else escape(workspace_scope_line(scopes)),
        ),
        ("Repositories", repos),
        ("Indexed", f"{_n(facts.files, 'file')} · {_n(facts.symbols, 'symbol')}"),
        ("Documentation", pages(total_pages) if total_pages else no_docs),
    ]
    if facts.provider:
        rows.append(("Provider", escape(facts.provider)))
    rows += [("Primary repo", escape(facts.primary)), ("Elapsed", facts.elapsed)]
    return rows


def _repo_row(repo: RepoOutcome, *, mixed: bool) -> tuple[str, str]:
    name = escape(repo.alias)
    if repo.error:
        return f"[{ERR}]{glyph('fail')}[/] {name}", f"[{ERR}]failed[/] [dim]({escape(repo.error)})[/dim]"
    if repo.skipped:
        return (
            f"[{WARN}]{glyph('fail')}[/] {name}",
            f"[{WARN}]documentation skipped[/] [dim]({escape(repo.skipped)})[/dim]",
        )
    label = mode_label(repo.scope)
    value = pages(repo.pages) if repo.pages else f"no documentation ({label})"
    if mixed and repo.pages:
        value += f" [dim]· {escape(label)}[/dim]"
    return f"[{OK}]{glyph('ok')}[/] {name}", value


def _repo_lines(facts: WorkspaceFacts) -> list[RenderableType]:
    """The per-repo list, failures and skips first so a cut never hides one."""
    mixed = len({mode_label(r.scope) for r in _indexed(facts)}) > 1
    ordered = sorted(facts.repos, key=lambda r: 0 if r.error else 1 if r.skipped else 2)
    shown = ordered[:MAX_REPO_ROWS]
    table = key_value_table(
        [_repo_row(r, mixed=mixed) for r in shown], label_width=16, label_style="", value_style=""
    )
    lines: list[RenderableType] = [
        Text(""),
        line("[bold]By repository[/bold]"),
        Padding(table, (0, 0, 0, 2)),
    ]
    if len(ordered) > MAX_REPO_ROWS:
        rest = len(ordered) - MAX_REPO_ROWS
        lines.append(
            dim_line(
                f"and {_n(rest, 'more repo')}; "
                "[bold]repowise status --workspace[/bold] lists them all.",
                indent=4,
            )
        )
    return lines


def workspace_steps(facts: WorkspaceFacts, setup: Any = None) -> list[tuple[str, str]]:
    """``repowise serve``, then the two moves that most need making."""
    candidates: list[tuple[str, str]] = []
    if setup is not None and getattr(setup, "editor_setup_disabled", False):
        candidates.append(("repowise mcp .", "connect an MCP client (Cursor, Codex, Claude Code)"))
    skipped = next((r for r in facts.repos if r.skipped), None)
    if skipped and not facts.dry_run:
        candidates.append(
            (fast_to_full_command(escape(skipped.alias)), "build its documentation")
        )
    elif not facts.provider and not facts.dry_run:
        candidates.append(
            ("repowise generate <repo>", "write a repo's documentation with a model")
        )
    candidates += [
        ('repowise search "<query>"', "search across all indexed repos"),
        ("repowise status --workspace", "show workspace status"),
    ]
    return [serve_step("workspace dashboard"), *candidates[:_MAX_FOLLOW_UPS]]


def _questions() -> list[str]:
    return [
        "How do these repos depend on each other, and where should I start reading?",
        agent_questions()[1],
    ]


def build_workspace_finish_panel(
    facts: WorkspaceFacts, *, setup: Any = None, owl: str = mascot.DONE
) -> Any:
    """The workspace completion panel: counts, repos, Connected, questions, steps.

    *owl* is the title owl's state: how the run finished.
    """
    return build_completion_panel(
        TITLE_WORKSPACE,
        _metrics(facts),
        next_steps=workspace_steps(facts, setup),
        body=_body(facts, setup),
        footer=autosync_lines(setup),
        owl=owl,
    )


def print_workspace_finish_panel(
    console: Any, facts: WorkspaceFacts, *, setup: Any = None, owl: str = mascot.DONE
) -> None:
    """The workspace panel on a terminal; the same rows as plain lines off one."""
    print_completion_panel(
        console,
        TITLE_WORKSPACE,
        _metrics(facts),
        workspace_steps(facts, setup),
        body=_body(facts, setup),
        footer=autosync_lines(setup),
        owl=owl,
    )


def _body(facts: WorkspaceFacts, setup: Any) -> list[RenderableType]:
    body = _repo_lines(facts)
    connected = connected_lines(setup)
    if connected:
        body += [Text(""), *connected]
    return body + ask_lines(_questions())
