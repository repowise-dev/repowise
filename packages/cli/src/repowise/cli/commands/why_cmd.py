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


def _with_trust(projected: dict, source: dict) -> dict:
    """Keep the compact evidence contract visible in default agent output."""
    for key in ("source", "provenance", "evidence_refs", "restates"):
        if key in source:
            projected[key] = source[key]
    return projected


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
    return _with_trust(out, decision)


def _project_episode(episode: dict) -> dict:
    return _with_trust({
        "kind": episode.get("kind", ""),
        "subject": episode.get("subject", ""),
        "recorded": episode.get("recorded", ""),
        "evidence": episode.get("evidence", ""),
        "still_true": episode.get("still_true", ""),
    }, episode)


_ARCH_LAYERS = (
    ("file_commits", ("sha", "date", "author", "message")),
    ("git_log", ("sha", "date", "author", "message")),
    ("cross_references", ("source_file", "sha", "date", "message")),
)


def _project_archaeology(arch: dict) -> dict:
    """The git-history fallback, capped per layer, each with its total.

    Three layers: the file's own significant commits and the cross-references
    (10 each from the tool), and a live ``git log`` (20). The summary line
    survives whole — it is the one sentence that says a file is ungoverned and
    what was recovered instead. Every cap reports what it cut, the way every
    other capped list in this module does; a head with no total is how "5 of
    18" reads as "18".
    """
    out: dict = {}
    if arch.get("summary"):
        out["summary"] = arch["summary"]
    for key, fields in _ARCH_LAYERS:
        rows = arch.get(key) or []
        if not rows:
            continue
        out[key] = [
            _with_trust({f: c.get(f, "") for f in fields}, c)
            for c in _capped(rows)
        ]
        if len(rows) > len(out[key]):
            out[f"{key}_total"] = len(rows)
    return out


def _project_target_entry(entry: dict) -> dict:
    """One ``--target``'s card: its governing decisions, origin and fallback.

    Recursively the same trim as the top level — the per-target ``origin``
    carries the same ~2K-char prose ``summary`` and the per-target
    ``git_archaeology`` the same three capped layers.
    """
    out: dict = {"governing_decisions": entry.get("governing_decisions") or []}
    origin = entry.get("origin") or {}
    if origin:
        out["origin"] = {
            key: origin[key]
            for key in ("available", "primary_author", "total_commits", "age_days", "summary")
            if key in origin and (key != "summary" or not origin.get("available"))
        }
    if entry.get("git_archaeology"):
        out["git_archaeology"] = _project_archaeology(entry["git_archaeology"])
    return out


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
                        truncated / omission_marker /
                        dropped_decisions, and the three blocks the
                        tool substitutes when nothing governs the
                        path: git_archaeology (summary plus the head
                        of each of its three layers), code_rationale
                        and target_context
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
    # Several --targets and no query: the paths asked about, since there is no
    # single ``path`` naming them and ``target_context`` is keyed by them.
    if payload.get("paths"):
        out["paths"] = payload["paths"]
    if payload.get("summary"):
        out["summary"] = payload["summary"]
    if payload.get("counts"):
        out["counts"] = payload["counts"]
    if payload.get("alignment"):
        out["alignment"] = payload["alignment"]
    # Which lane the answer rests on. Only "decision" is a ruling.
    if payload.get("answer_basis"):
        out["answer_basis"] = payload["answer_basis"]

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
                _with_trust({
                    "sha": c.get("sha", ""),
                    "date": c.get("date", ""),
                    "author": c.get("author", ""),
                    "message": c.get("message", ""),
                }, c)
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

    # The three blocks the tool substitutes when decisions are silent. Losing
    # them empties exactly the mode this tool exists to never leave empty:
    # `get_why` sets git_archaeology and code_rationale *because* nothing
    # governs the path, and target_context is the entire product of --target.
    if payload.get("git_archaeology"):
        out["git_archaeology"] = _project_archaeology(payload["git_archaeology"])
    if payload.get("code_rationale"):
        out["code_rationale"] = [
            _with_trust(
                {k: entry.get(k) for k in ("path", "lines", "comment")}, entry
            )
            for entry in _capped(payload["code_rationale"])
        ]
    if payload.get("target_context"):
        out["target_context"] = {
            target: _project_target_entry(entry)
            for target, entry in payload["target_context"].items()
        }

    for key in ("stale_decisions", "proposed_awaiting_review", "ungoverned_hotspots", "conflicts"):
        values = payload.get(key)
        if values:
            out[key] = _capped(values)
            if len(values) > len(out[key]):
                out[f"{key}_total"] = len(values)

    if payload.get("truncated"):
        out["truncated"] = True
    for key in ("omission_marker", "dropped_decisions"):
        if payload.get(key):
            out[key] = payload[key]

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
    help="File path to anchor the search to, or to ask about when QUERY is omitted. Repeatable.",
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
    health dashboard — or for the --targets, when any are named. Falls back to
    git archaeology when a path has no decisions, so it is never empty.
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

    payload = _ta.run(repo_path, _factory, "get_why")

    if full:
        _ta.emit_full(payload)
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

    if projected.get("git_archaeology"):
        console.print("")
        _render_archaeology(console, projected["git_archaeology"], indent="")

    rationale = projected.get("code_rationale") or []
    if rationale:
        console.print("\n[bold]Rationale comments in the code[/bold]")
        for entry in rationale:
            lines = entry.get("lines") or []
            where = f":{lines[0]}" if lines else ""
            console.print(f"  [cyan]{entry.get('path', '')}{where}[/cyan]")
            console.print(f"    [dim]{entry.get('comment', '')}[/dim]")

    for target, entry in (projected.get("target_context") or {}).items():
        console.print(f"\n[bold]Target[/bold] [cyan]{target}[/cyan]")
        for governing in entry.get("governing_decisions") or []:
            console.print(
                f"  {governing.get('title', '')} [dim]({governing.get('status', '')})[/dim]"
            )
        origin = entry.get("origin") or {}
        if origin.get("summary"):
            # Only set when there is no git history — the short "No git history
            # for X." line, not the long prose the projection drops.
            console.print(f"  [dim]{origin['summary']}[/dim]")
        elif origin.get("available"):
            console.print(
                f"  [dim]{origin.get('total_commits', '?')} commits over "
                f"{origin.get('age_days', '?')} days, mostly "
                f"{origin.get('primary_author', '?')}[/dim]"
            )
        if entry.get("git_archaeology"):
            _render_archaeology(console, entry["git_archaeology"], indent="  ")

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

    # Every block this function can render. Search mode with --target sets
    # target_context and code_rationale and none of the others, so a guard
    # listing only the path-mode blocks printed "Nothing recorded" underneath
    # the things it had just recorded.
    if not any(projected.get(key) for key in _RENDERABLE_BLOCKS):
        console.print("[yellow]Nothing recorded for that query.[/yellow]")


#: Every key ``_render`` can put on the screen. Keeping the emptiness guard
#: derived from one list is what stops it drifting behind a new block.
_RENDERABLE_BLOCKS = (
    "summary",
    "counts",
    "alignment",
    "decisions",
    "origin_story",
    "git_archaeology",
    "code_rationale",
    "target_context",
    "stale_decisions",
    "proposed_awaiting_review",
    "conflicts",
    "ungoverned_hotspots",
    "related_documentation",
    "episodes",
)


#: Re-exported for the module's own readers; ``risk`` renders the same field
#: (``owner_pct``) off the same git metadata, so the normalisation lives in
#: ``_tool_adapters`` and both callers share one copy.
_owner_share = _ta.owner_share


def _render_archaeology(console, arch: dict, *, indent: str) -> None:
    """The archaeology block, at the top level or nested under one ``--target``."""
    console.print(f"{indent}[bold]Git archaeology[/bold] {arch.get('summary', '')}")
    for label, key in (
        ("this file", "file_commits"),
        ("git log", "git_log"),
        ("mentioned by", "cross_references"),
    ):
        rows = arch.get(key) or []
        for c in rows:
            where = f" [dim]({c['source_file']})[/dim]" if c.get("source_file") else ""
            console.print(
                f"{indent}  [dim]{label}: {str(c.get('sha', ''))[:8]} "
                f"{str(c.get('date', ''))[:10]}[/dim] {c.get('message', '')}{where}"
            )
        total = arch.get(f"{key}_total")
        if total:
            console.print(
                f"{indent}  [dim]{len(rows)} of {total} {label} shown; "
                f"pass --full for the rest.[/dim]"
            )


def _print_more(console, projected: dict, key: str, shown: int) -> None:
    total = projected.get(f"{key}_total")
    if total:
        console.print(f"  [dim]{shown} of {total} shown; pass --full for the rest.[/dim]")
