"""``repowise why`` — the CLI adapter over the ``get_why`` MCP tool."""

from __future__ import annotations

import click

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.output import emit_json

#: How many entries of a long list survive the trim. The tool already ranks
#: them, so a head is the cheapest honest cut — and the count it was cut from
#: travels beside it so a caller knows there is more.
_LIST_CAP = 5


def _capped(values: list, cap: int = _LIST_CAP) -> list:
    return list(values or [])[:cap]


def _project_decision(decision: dict) -> dict:
    """One decision record, trimmed to the decision and why it was taken."""
    out = {
        "id": decision.get("id", ""),
        "title": decision.get("title", ""),
        "status": decision.get("status", ""),
        "decision": decision.get("decision", ""),
        "rationale": decision.get("rationale", ""),
    }
    files = decision.get("affected_files") or []
    if files:
        out["affected_files"] = _capped(files)
        total = decision.get("affected_files_total", len(files))
        if total > len(out["affected_files"]):
            out["affected_files_total"] = total
    if decision.get("still_true"):
        out["still_true"] = decision["still_true"]
    return out


def _project_episode(episode: dict) -> dict:
    return {
        "kind": episode.get("kind", ""),
        "subject": episode.get("subject", ""),
        "recorded": episode.get("recorded", ""),
        "evidence": episode.get("evidence", ""),
        "still_true": episode.get("still_true", ""),
    }


def project(payload: dict) -> dict:
    """``get_why``'s dict, trimmed. The tool has three modes and so has this.

    Common to every mode: ``mode`` survives, ``_meta`` collapses to ``index``.

    ==================  ===========================================
    search mode kept    query, decisions (id / title / status /
                        decision / rationale / affected_files head
                        / still_true), related_documentation
                        (title / page_type / snippet), episodes
                        (kind / subject / recorded / evidence /
                        still_true)
    path mode kept      path, alignment, decisions as above,
                        origin_story minus ``summary`` and minus
                        each key commit's ``body``, episodes,
                        truncated / omission_marker
    dashboard kept      summary, counts, and the head of each of
                        stale_decisions / proposed_awaiting_review /
                        ungoverned_hotspots / conflicts, with the
                        full count beside it
    ==================  ===========================================

    Dropped throughout: a decision's ``context``, ``consequences``,
    ``alternatives``, ``lineage``, ``confidence`` and ``staleness_score``; the
    tail of every capped list; ``origin_story.summary`` (a ~2K-char prose
    retelling of the key commits printed beside it) and each key commit's
    ``body``. ``--full`` returns all of it.
    """
    mode = payload.get("mode", "")
    out: dict = {"mode": mode}

    if payload.get("query"):
        out["query"] = payload["query"]
    if payload.get("path"):
        out["path"] = payload["path"]
    if payload.get("summary"):
        out["summary"] = payload["summary"]
    if payload.get("counts"):
        out["counts"] = payload["counts"]
    if payload.get("alignment"):
        out["alignment"] = payload["alignment"]

    decisions = payload.get("decisions")
    if decisions is not None:
        out["decisions"] = [_project_decision(d) for d in _capped(decisions)]
        total = payload.get("decisions_total", len(decisions))
        if total > len(out["decisions"]):
            out["decisions_total"] = total

    origin = payload.get("origin_story") or {}
    if origin:
        out["origin_story"] = {
            key: origin[key]
            for key in (
                "available",
                "primary_author",
                "author_commit_pct",
                "total_commits",
                "first_commit",
                "last_commit",
                "age_days",
            )
            if key in origin
        }
        commits = origin.get("key_commits") or []
        if commits:
            out["origin_story"]["key_commits"] = [
                {
                    "sha": c.get("sha", ""),
                    "date": c.get("date", ""),
                    "author": c.get("author", ""),
                    "message": c.get("message", ""),
                }
                for c in _capped(commits)
            ]

    docs = payload.get("related_documentation")
    if docs:
        out["related_documentation"] = [
            {
                "title": d.get("title", ""),
                "page_type": d.get("page_type", ""),
                "snippet": d.get("snippet", ""),
            }
            for d in _capped(docs)
        ]

    episodes = payload.get("episodes")
    if episodes:
        out["episodes"] = [_project_episode(e) for e in _capped(episodes)]

    for key in ("stale_decisions", "proposed_awaiting_review", "ungoverned_hotspots", "conflicts"):
        values = payload.get(key)
        if values:
            out[key] = _capped(values)
            if len(values) > len(out[key]):
                out[f"{key}_total"] = len(values)

    if payload.get("truncated"):
        out["truncated"] = True
    if payload.get("omission_marker"):
        out["omission_marker"] = payload["omission_marker"]

    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


@click.command("why")
@click.argument("query", required=False, default=None)
@click.option(
    "--target",
    "targets",
    multiple=True,
    help="File path to anchor the search to. Repeatable.",
)
@_ta.target_options
def why_command(
    query: str | None,
    targets: tuple[str, ...],
    path: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    full: bool,
) -> None:
    """Why the code is shaped this way: decisions, rationale, git archaeology.

    QUERY is a question ("why is auth using JWT?"), a file path (its governing
    decisions, origin story and alignment score), or omitted for the decision
    health dashboard. Falls back to git archaeology when a path has no
    decisions, so it is never empty.
    """
    fmt = _ta.resolve_format_for(fmt, full)
    repo_path = _ta.resolve_indexed_repo(
        path=path,
        repo_alias=repo_alias,
        no_workspace=no_workspace,
        fmt=fmt,
        command="why",
    )

    def _factory():
        from repowise.server.mcp_server.tool_why import get_why

        return get_why(query=query, targets=list(targets) or None)

    payload = _ta.run(repo_path, _factory)

    if full:
        emit_json(payload)
        return
    _ta.emit_error(payload, fmt, extra={"query": query})
    projected = project(payload)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected)
    _ta.print_index_note(payload, fmt)


def _render(projected: dict) -> None:
    from rich.table import Table

    from repowise.cli.helpers import console

    if projected.get("summary"):
        console.print(f"[bold]{projected['summary']}[/bold]")
    counts = projected.get("counts") or {}
    if counts:
        table = Table(title="Decision health")
        for key in counts:
            table.add_column(key.replace("_", " ").title(), justify="right")
        table.add_row(*[str(v) for v in counts.values()])
        console.print(table)

    alignment = projected.get("alignment") or {}
    if alignment:
        console.print(
            f"\n[bold]Alignment[/bold] {alignment.get('score', '?')} — "
            f"{alignment.get('explanation', '')}"
        )

    decisions = projected.get("decisions") or []
    if decisions:
        console.print("\n[bold]Decisions[/bold]")
        for d in decisions:
            console.print(f"  [cyan]{d.get('title', '')}[/cyan] [dim]({d.get('status', '')})[/dim]")
            if d.get("decision"):
                console.print(f"    {d['decision']}")
            if d.get("rationale"):
                console.print(f"    [dim]why: {d['rationale']}[/dim]")
            if d.get("still_true"):
                console.print(f"    [dim]{d['still_true']}[/dim]")
        if projected.get("decisions_total"):
            console.print(
                f"  [dim]{len(decisions)} of {projected['decisions_total']} shown; "
                f"pass --full for the rest.[/dim]"
            )

    origin = projected.get("origin_story") or {}
    if origin.get("available"):
        console.print(
            f"\n[bold]Origin[/bold] {origin.get('total_commits', '?')} commits over "
            f"{origin.get('age_days', '?')} days, mostly "
            f"{origin.get('primary_author', '?')} "
            f"({_owner_share(origin.get('author_commit_pct'))})"
        )
        for c in origin.get("key_commits") or []:
            console.print(
                f"  [dim]{c.get('sha', '')} {str(c.get('date', ''))[:10]}[/dim] "
                f"{c.get('message', '')}"
            )

    for label, key in (
        ("Stale decisions", "stale_decisions"),
        ("Proposed, awaiting review", "proposed_awaiting_review"),
        ("Conflicts", "conflicts"),
    ):
        rows = projected.get(key) or []
        if rows:
            console.print(f"\n[bold]{label}[/bold]")
            for row in rows:
                console.print(f"  {row.get('title', row) if isinstance(row, dict) else row}")
            _print_more(console, projected, key, len(rows))

    hotspots = projected.get("ungoverned_hotspots") or []
    if hotspots:
        console.print("\n[bold]Ungoverned hotspots[/bold]")
        for hotspot in hotspots:
            console.print(f"  {hotspot}")
        _print_more(console, projected, "ungoverned_hotspots", len(hotspots))

    docs = projected.get("related_documentation") or []
    if docs:
        console.print("\n[bold]Related documentation[/bold]")
        for d in docs:
            console.print(f"  [cyan]{d.get('title', '')}[/cyan] [dim]{d.get('page_type', '')}[/dim]")

    episodes = projected.get("episodes") or []
    if episodes:
        console.print("\n[bold]Recorded about this checkout[/bold]")
        for e in episodes:
            console.print(f"  [dim]{e.get('evidence', '')}[/dim] {e.get('subject', '')}")
            if e.get("still_true"):
                console.print(f"    [dim]{e['still_true']}[/dim]")

    if not any(
        projected.get(key)
        for key in ("decisions", "origin_story", "counts", "episodes", "related_documentation")
    ):
        console.print("[yellow]Nothing recorded for that query.[/yellow]")


def _owner_share(value: object) -> str:
    """Render ``author_commit_pct``, which is a fraction *or* a percentage.

    Its source stores either, depending on which git-metadata path filled it in
    — ``developer_congestion`` already normalises the same field the same way.
    Printing it raw shows a dominant author as "0.99%".
    """
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    return f"{pct * 100 if pct <= 1.0 else pct:.0f}%"


def _print_more(console, projected: dict, key: str, shown: int) -> None:
    total = projected.get(f"{key}_total")
    if total:
        console.print(f"  [dim]{shown} of {total} shown; pass --full for the rest.[/dim]")
