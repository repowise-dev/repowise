"""``repowise search`` — the CLI adapter over the ``search_codebase`` MCP tool.

This command used to carry its own retrieval: an FTS query, a LanceDB query,
a ``LIKE`` over ``wiki_symbols``, and a workspace fan-out that fused them. All
of that is what ``search_codebase`` already does, better — it fuses FTS and
vector legs through RRF rather than picking one, honours per-repo excludes and
tombstones, demotes decision and test pages on queries that did not ask for
them, and can route an identifier-shaped query to the symbol index. The command
is now a thin adapter, so a CLI answer and an MCP answer are the same answer.

Two things are deliberately preserved across the collapse.

**The flag vocabulary.** ``--mode fulltext|semantic|symbol`` still works and is
still what ``--help`` documents; the tool's own ``auto|concept|path|hybrid``
are accepted as additional choices. Nobody's script breaks.

**The payload.** The default projection emits the keys this command has always
emitted (``score/title/page_type/path/snippet``), so the collapse is
payload-neutral by default; ``--full`` returns the tool's raw dict, the same
switch the other adapter commands carry.
"""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option, full_option
from repowise.cli.output import notice_console as _notices

# Rank-fusion damping for the workspace fan-out. The tool federates internally
# through a workspace registry the CLI does not build (see ``tool_bridge``), so
# ``--all`` calls it once per repo and fuses the ranked lists here. k=60 is the
# same constant ``_federated_search`` uses, so a fanned-out CLI search ranks the
# way a fanned-out MCP search does.
_WORKSPACE_RRF_K = 60

#: CLI mode spelling -> the tool's ``mode=`` argument.
#:
#: ``fulltext`` and ``semantic`` both land on ``concept``: the tool's concept
#: branch fuses the full-text and vector legs through RRF instead of choosing
#: between them, so it is the successor to both, and it drops the vector leg by
#: itself on a keyless index (``_safe_vector``) exactly where ``--mode
#: semantic`` used to fall back to full text by hand.
_MODE_ALIASES = {"fulltext": "concept", "semantic": "concept", "symbol": "symbol"}

#: What ``--mode`` accepts. The legacy three first, because they are the
#: documented spelling and one of them is still the default.
_MODE_CHOICES = ["fulltext", "semantic", "symbol", "auto", "concept", "path", "hybrid"]


def _tool_mode(requested: str) -> str:
    """The tool's ``mode=`` for a CLI ``--mode``; tool spellings pass through."""
    return _MODE_ALIASES.get(requested, requested)


def _result_kind(item: dict) -> str:
    """``symbol`` / ``file`` / ``page`` for one result, whatever mode produced it.

    Symbol and path search tag their results with ``type``; the concept path
    does not tag its pages at all except inside a hybrid interleave, so an
    untagged hit is a page.
    """
    kind = item.get("type")
    return kind if kind in ("symbol", "file") else "page"


def _project_result(item: dict, *, multi: bool) -> dict:
    """One tool result, trimmed to the keys this command has always emitted.

    Three result shapes reach here and they do not share a payload, so the
    projection dispatches on :func:`_result_kind` rather than taking a fixed
    key list. Each shape keeps the columns its table has always shown, plus
    the one handle that makes the hit actionable:

    ==========  ==============================================================
    page        ``score`` (from ``relevance_score``), ``title``, ``page_type``,
                ``path`` (from ``target_path``), ``snippet``
    symbol      ``score``, ``name``, ``qualified_name``, ``kind``, ``path``
                (from ``file``), ``line`` (from ``start_line``), and
                ``symbol_id`` — the id ``repowise symbol`` takes, without
                which a symbol hit cannot be followed anywhere
    file        ``score``, ``title``, ``path`` (from ``file``)
    ==========  ==============================================================

    Dropped: ``page_id``, ``sources``, ``confidence_score``, ``end_line``,
    ``signature``, ``language`` and ``next`` — call envelope and ranking
    internals, all of them in ``--full``.

    ``type`` is emitted on every row although the old payload had none. It was
    unambiguous when every row in a response was a page; ``hybrid`` interleaves
    symbol hits with page hits, so without it a consumer cannot tell which keys
    a row even has.
    """
    kind = _result_kind(item)
    score = item.get("relevance_score") if kind == "page" else item.get("score")
    out: dict = {"type": kind, "score": round(float(score or 0.0), 6)}
    if kind == "symbol":
        out.update(
            {
                "name": item.get("name") or "",
                "qualified_name": item.get("qualified_name") or "",
                "kind": item.get("kind") or "",
                "path": item.get("file") or "",
                "line": item.get("start_line"),
                "symbol_id": item.get("symbol_id") or "",
            }
        )
    elif kind == "file":
        out.update({"title": item.get("title") or "", "path": item.get("file") or ""})
    else:
        out.update(
            {
                "title": item.get("title") or "",
                "page_type": item.get("page_type") or "",
                "path": item.get("target_path") or "",
                "snippet": item.get("snippet") or "",
            }
        )
        # ``target_path`` on a symbol_spotlight is a page id (``a.py::Foo``),
        # not something a reader can open, which is why the tool attaches
        # ``file`` beside it. ``path`` keeps the tool's own value so the
        # payload stays what it was; ``file`` carries the openable resolution
        # and appears only when the two differ.
        file = item.get("file")
        if file and file != out["path"]:
            out["file"] = file
    if multi:
        out["repo"] = item.get("repo") or ""
    return out


def project(payload: dict, query: str, *, multi: bool) -> dict:
    """``search_codebase``'s dict, trimmed to what a CLI caller reads.

    Kept beside ``results``: ``candidates`` (the distinct openable files the
    hits resolve to — some results are pages, and this is what to read),
    ``exact_match`` and ``note`` (an identifier query that matched no indexed
    symbol still returns fuzzy neighbours; the note is what stops a caller
    anchoring on one), ``grep_hint`` (the zero-result recovery path), and the
    freshness half of ``_meta``. Each of those changes the answer rather than
    its size, which is the line this trim draws.

    Dropped: the timing and token halves of ``_meta``, and the per-result keys
    named in :func:`_project_result`.
    """
    out: dict = {
        "query": query,
        # Only the structured branches report the mode they resolved to. The
        # concept branch and the federated one are concept by construction and
        # report nothing, so an absent key is not an unknown.
        "mode": payload.get("mode") or "concept",
        "results": [_project_result(r, multi=multi) for r in payload.get("results") or []],
    }
    for key in ("candidates", "exact_match", "note", "grep_hint"):
        if payload.get(key) is not None:
            out[key] = payload[key]
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


@click.command("search")
@click.argument("query")
@click.argument("path", required=False, default=None)
@click.option(
    "--mode",
    type=click.Choice(_MODE_CHOICES),
    default="fulltext",
    help="Search mode. fulltext/semantic both run the tool's fused concept "
    "search; auto routes on the query's shape.",
)
@click.option("--limit", type=int, default=10, help="Max results.")
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to search (implies workspace mode).",
)
@click.option(
    "--all",
    "search_all",
    is_flag=True,
    default=False,
    help="In workspace mode, fan out across every indexed repo.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@format_option()
@full_option()
def search_command(
    query: str,
    path: str | None,
    mode: str,
    limit: int,
    repo_alias: str | None,
    search_all: bool,
    no_workspace: bool,
    fmt: str,
    full: bool,
) -> None:
    """Search wiki pages by keyword, meaning, or symbol name.

    Runs the same hybrid retrieval as the search_codebase MCP tool: the
    full-text and vector legs are fused rather than chosen between, and
    --mode symbol/path/hybrid reach the structural indexes.

    In workspace mode, defaults to the primary repo; pass --repo <alias>
    for a specific one or --all to search across every indexed repo.
    """
    from pathlib import Path

    from repowise.cli.helpers import ensure_repowise_dir, resolve_command_target

    fmt = _ta.resolve_format_for(fmt, full)
    tool_mode = _tool_mode(mode)
    target = resolve_command_target(
        path=path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    notices = _notices(fmt)
    target.notice(notices, command=f"search ({mode})")

    repo_paths: list[Path] = []
    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        if search_all:
            repo_paths = [(target.ws_root / e.path).resolve() for e in target.ws_config.repos]
        elif target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_paths = [picked]
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_paths = [primary]
    else:
        assert target.repo_path is not None
        repo_paths = [target.repo_path]

    repo_paths = [p for p in repo_paths if (p / ".repowise").is_dir()]
    if not repo_paths:
        notices.print("[yellow]No indexed repos to search. Run 'repowise init' first.[/yellow]")
        # Every early return owes stdout a document: a json caller cannot tell
        # an empty pipe from a crash.
        if fmt == "json":
            emit_json({"query": query, "mode": tool_mode, "results": []})
        return

    if len(repo_paths) == 1:
        repo_path = repo_paths[0]
        ensure_repowise_dir(repo_path)
        payload = _run_search(repo_path, query, limit, tool_mode)
        if full:
            _ta.emit_full(payload)
            return
        _ta.emit_error(payload, fmt, extra={"query": query, "mode": tool_mode})
        _warn_if_lexical_only(payload, mode, notices)
        projected = project(payload, query, multi=False)
        if fmt == "json":
            emit_json(projected)
            return
        _render(projected, query, mode, multi=False)
        _ta.print_index_note(payload, fmt)
        return

    _fan_out(repo_paths, query, limit, mode, tool_mode, fmt, full, notices)


def _run_search(repo_path, query: str, limit: int, tool_mode: str) -> dict:
    """One ``search_codebase`` call against one repo."""

    def _factory():
        from repowise.server.mcp_server.tool_search import search_codebase

        return search_codebase(query=query, limit=limit, mode=tool_mode)

    return _ta.run(repo_path, _factory, "search_codebase")


def _warn_if_lexical_only(payload: dict, requested: str, notices) -> None:
    """Say when ``--mode semantic`` was answered lexically, and why.

    ``_meta`` carries ``semantic_search: false`` whenever the vector leg could
    not be ranked on — a keyless index, or a pinned embedder that would not
    build — which is the condition this command used to detect for itself
    before the tool did it. Rendering those rows without saying so is how
    someone concludes semantic retrieval is bad when what they have is no
    embedder.

    Only ``--mode semantic`` is warned about: it is the spelling that promises
    semantic matching. ``fulltext`` never claimed it, and the tool's own
    spellings are the tool's contract, where ``_meta`` already says this.
    """
    if requested != "semantic":
        return
    meta = payload.get("_meta") or {}
    if meta.get("semantic_search") is not False:
        return
    # Worded for both states. A repo pinned to a real embedder whose key has
    # gone away carries the tool's own reason, which names it; telling that
    # user "no embedder configured" would contradict their own config.
    why = meta.get("embedder_warning") or (
        "No embedder configured for this repo, so there is no semantic index to search."
    )
    notices.print(
        f"[yellow]{why}[/yellow]\nShowing full-text results instead. Set an embedder "
        "key and run [cyan]repowise reindex[/cyan] for semantic search."
    )


def _fan_out(
    repo_paths,
    query: str,
    limit: int,
    mode: str,
    tool_mode: str,
    fmt: str,
    full: bool,
    notices,
) -> None:
    """``--all``: one tool call per repo, fused on rank, rendered once.

    Rank fusion rather than raw score: each repo scores its own list
    independently (RRF within a repo for concept mode, a token-overlap score
    for symbol mode), so the numbers are only comparable within a repo. This is
    what ``_federated_search`` does with the same constant.
    """
    merged: list[tuple[int, dict]] = []
    payloads: list[dict] = []
    raw: dict[str, dict] = {}
    failures: list[str] = []
    for rp in repo_paths:
        payload = _run_search(rp, query, limit, tool_mode)
        raw[rp.name] = payload
        if payload.get("error"):
            # One unreadable repo must not take the fan-out down; the others
            # still have answers. Matches the pre-collapse behaviour.
            #
            # Escaped: the shaped error interpolates the exception verbatim and
            # those routinely carry brackets, so an unescaped ``[/x]`` would
            # raise MarkupError and take the command down with an empty stdout —
            # the state every early return in this file exists to avoid.
            from rich.markup import escape

            failures.append(rp.name)
            notices.print(
                f"[yellow]search failed for {rp.name}: {escape(str(payload['error']))}[/yellow]"
            )
            continue
        payloads.append(payload)
        _warn_if_lexical_only(payload, mode, notices)
        for rank, item in enumerate(payload.get("results") or []):
            item["repo"] = rp.name
            merged.append((rank, item))

    if full:
        # Every repo's own payload, unmerged and unprojected. There is no single
        # tool call here to return the payload *of*, and synthesising one would
        # hand back a dict no tool ever produced while calling it raw.
        out: dict = {"repos": raw}
        if not payloads:
            out["error"] = f"search failed in every repo: {', '.join(failures)}"
        _ta.emit_full(out)
        return

    merged.sort(key=lambda pair: 1.0 / (pair[0] + _WORKSPACE_RRF_K), reverse=True)
    results = [item for _, item in merged[:limit]]

    fused: dict = {"results": results}
    # Reported from the first repo that answered: the mode is resolved from the
    # query, which every repo saw, so they all resolved the same one.
    if payloads and payloads[0].get("mode"):
        fused["mode"] = payloads[0]["mode"]
    # Recomputed over the fused window rather than concatenated: the block
    # promises distinct openable paths, best first, and per-repo lists overlap.
    from repowise.server.mcp_server._page_paths import file_candidates

    if candidates := file_candidates(results, limit=limit):
        fused["candidates"] = candidates
    # A hint or an exact-match verdict is about the query, not the repo, and is
    # only attached when that repo found nothing — so it is true of the fan-out
    # only when every repo that answered agrees.
    for key in ("grep_hint", "note"):
        values = [p[key] for p in payloads if p.get(key)]
        if values and len(values) == len(payloads):
            fused[key] = values[0]
    if payloads and all("exact_match" in p for p in payloads):
        fused["exact_match"] = any(p["exact_match"] for p in payloads)
    fused["_meta"] = _worst_freshness(payloads)

    # "Nothing matched" and "nothing could be searched" are different answers,
    # and the second one is a failure the exit code has to carry.
    if not results and not payloads:
        _ta.emit_error(
            {"error": f"search failed in every repo: {', '.join(failures)}"},
            fmt,
            extra={"query": query, "mode": tool_mode},
        )
    projected = project(fused, query, multi=True)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected, query, mode, multi=True)
    _ta.print_index_note(fused, fmt)


def _worst_freshness(payloads: list[dict]) -> dict:
    """The freshness of the fan-out, which is the freshness of its worst repo.

    Without this the fan-out reports no ``index`` block and prints no staleness
    notice, so ``repowise search foo`` would warn that the index is behind while
    ``repowise search foo --all`` said nothing — and the answer drawn from the
    most repos is the one most likely to have a stale one among them.
    """
    meta: dict = {}
    for payload in payloads:
        repo_meta = payload.get("_meta") or {}
        if repo_meta.get("stale_warning") and not meta.get("stale_warning"):
            meta["stale_warning"] = repo_meta["stale_warning"]
        if repo_meta.get("index_behind"):
            meta["index_behind"] = True
            # Named so the notice can say which checkout it means; the first
            # behind repo wins rather than an invented merge of several SHAs.
            meta.setdefault("indexed_commit", repo_meta.get("indexed_commit", "?"))
            meta.setdefault("live_head", repo_meta.get("live_head", "?"))
        age = repo_meta.get("index_age_days")
        if age is not None:
            meta["index_age_days"] = max(age, meta.get("index_age_days", age))
    return meta


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render(projected: dict, query: str, mode: str, *, multi: bool) -> None:
    """The human path: one table per result shape, then the tool's own notes.

    A hybrid search interleaves symbol hits and page hits, and the two do not
    share columns, so they are grouped rather than forced into one table. The
    groups keep the order they first appear in, which is the tool's ranking.
    """
    results = projected.get("results") or []
    groups: dict[str, list[dict]] = {}
    for row in results:
        groups.setdefault(row.get("type") or "page", []).append(row)

    if not results:
        from rich.markup import escape

        where = " across the workspace" if multi else ""
        if mode == "symbol":
            console.print(f"[yellow]No symbols matching '{escape(query)}'{where}[/yellow]")
        else:
            console.print(f"[yellow]No results found{where}.[/yellow]")
    for kind, rows in groups.items():
        if kind == "symbol":
            console.print(_symbol_table(rows, query, multi=multi))
        elif kind == "file":
            console.print(_file_table(rows, query, multi=multi))
        else:
            console.print(_page_table(rows, query, mode, multi=multi))

    _render_notes(projected)


def _render_notes(projected: dict) -> None:
    """``note``, ``grep_hint`` and ``candidates`` — kept keys owe a renderer.

    Escaped, because a table cell and ``console.print`` both parse rich markup:
    a hint quoting ``mode="symbol"`` is harmless but a note quoting a
    bracketed type would be eaten, and a stray closing tag raises.
    """
    from rich.markup import escape

    # ``exact_match: false`` is covered by the note the tool attaches with it;
    # ``true`` has no note, and silence there leaves a table reader unable to
    # tell an exact symbol hit from the fuzzy neighbours it ranks beside.
    if projected.get("exact_match") is True:
        console.print("[green]Exact symbol match.[/green]")
    for key in ("note", "grep_hint"):
        if projected.get(key):
            console.print(f"[dim]{escape(_ta.as_cli_prose(str(projected[key])))}[/dim]")
    candidates = projected.get("candidates") or []
    if candidates:
        console.print("\n[bold]Files to read[/bold]")
        for entry in candidates:
            console.print(f"  [cyan]{escape(str(entry.get('path', '')))}[/cyan]")


def _titled(query: str, mode: str, *, multi: bool) -> str:
    # Escaped: a Table title is markup-parsed like any cell, so a query
    # containing a bracket (`search 'list[str]'`) would lose it, and one
    # containing a closing tag would raise MarkupError.
    from rich.markup import escape

    query = escape(query)
    label = {
        "fulltext": "Full-text search",
        "semantic": "Semantic search",
        "symbol": "Symbol search",
        "path": "Path search",
        "hybrid": "Hybrid search",
    }.get(mode, f"{mode.capitalize()} search")
    return f"{label}: '{query}' (workspace)" if multi else f"{label}: '{query}'"


def _page_table(rows: list[dict], query: str, mode: str, *, multi: bool) -> Table:
    table = Table(title=_titled(query, mode, multi=multi))
    table.add_column("Score", justify="right", style="green")
    if multi:
        table.add_column("Repo", style="magenta")
    table.add_column("Title", style="cyan")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Snippet", max_width=50)
    from rich.markup import escape

    for row in rows:
        cells = [f"{float(row.get('score') or 0.0):.3f}"]
        if multi:
            cells.append(str(row.get("repo", "")))
        cells += [
            str(row.get("title") or ""),
            str(row.get("page_type") or ""),
            str(row.get("path") or ""),
            # The table clips the snippet to fit its column; the JSON payload
            # does not, because a consumer has no column to fit.
            str(row.get("snippet") or "")[:50],
        ]
        table.add_row(*(escape(c) for c in cells))
    return table


def _symbol_table(rows: list[dict], query: str, *, multi: bool) -> Table:
    table = Table(title=_titled(query, "symbol", multi=multi))
    table.add_column("Name", style="cyan")
    if multi:
        table.add_column("Repo", style="magenta")
    table.add_column("Qualified Name")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Line", justify="right")
    from rich.markup import escape

    for row in rows:
        cells = [str(row.get("name") or "")]
        if multi:
            cells.append(str(row.get("repo", "")))
        cells += [
            str(row.get("qualified_name") or ""),
            str(row.get("kind") or ""),
            str(row.get("path") or ""),
            "" if row.get("line") is None else str(row["line"]),
        ]
        table.add_row(*(escape(c) for c in cells))
    return table


def _file_table(rows: list[dict], query: str, *, multi: bool) -> Table:
    from rich.markup import escape

    table = Table(title=_titled(query, "path", multi=multi))
    table.add_column("Score", justify="right", style="green")
    if multi:
        table.add_column("Repo", style="magenta")
    table.add_column("Title", style="cyan")
    table.add_column("Path")
    for row in rows:
        cells = [f"{float(row.get('score') or 0.0):.3f}"]
        if multi:
            cells.append(str(row.get("repo", "")))
        cells += [str(row.get("title") or ""), str(row.get("path") or "")]
        table.add_row(*(escape(c) for c in cells))
    return table
