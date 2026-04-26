"""``repowise augment`` — hook-driven context enrichment for AI coding agents.

Reads a Claude Code hook payload from stdin (JSON), queries the local wiki.db
for graph context (importers, symbols), and writes enriched context back to
stdout as the hook response.

PreToolUse: enriches Grep/Glob calls with up to 3 related files, their key
symbols, importers, and dependencies.

PostToolUse: detects git commits and notifies the agent when the wiki is stale.

Design goals:
  - Cold start < 500ms (lazy imports, minimal work)
  - Graceful failure: any error → exit 0 with empty output
  - No LLM calls, no network — pure local SQLite queries
"""

from __future__ import annotations

import json
import sys

import click


@click.command("augment")
def augment_command() -> None:
    """Enrich AI agent tool calls with codebase graph context (hook mode)."""
    try:
        _run_augment()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        # Hooks must never fail — exit silently on any error.
        sys.exit(0)


def _run_augment() -> None:
    """Main entry point — reads stdin, dispatches to pre/post handler."""
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = payload.get("hook_event_name", "")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", "")

    if event == "PreToolUse" and tool_name in ("Grep", "Glob"):
        result = _handle_pre_tool_use(tool_name, tool_input, cwd)
        if result:
            _emit_response(event, result)

    elif event == "PostToolUse" and tool_name == "Bash":
        tool_output = payload.get("tool_output", {})
        result = _handle_post_tool_use(tool_input, tool_output, cwd)
        if result:
            _emit_response(event, result)


def _emit_response(event: str, context: str) -> None:
    """Write the hook JSON response to stdout."""
    response = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# PreToolUse — enrich search/grep/glob with graph context
# ---------------------------------------------------------------------------


def _extract_search_pattern(tool_name: str, tool_input: dict) -> str | None:
    """Extract the search pattern from the tool input."""
    if tool_name in ("Grep", "Glob"):
        return tool_input.get("pattern")
    return None


def _handle_pre_tool_use(tool_name: str, tool_input: dict, cwd: str) -> str | None:
    """Query the wiki DB for graph context related to the search pattern."""
    pattern = _extract_search_pattern(tool_name, tool_input)
    if not pattern:
        return None

    from pathlib import Path

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    import asyncio

    return asyncio.run(_query_graph_context(repo_path, pattern))


async def _query_graph_context(repo_path: "Path", pattern: str) -> str | None:
    """Multi-signal search + graph enrichment.

    Phase 1 — find relevant files via three signals (merged, deduped):
      a) Symbol name match (wiki_symbols.name LIKE pattern)
      b) File path match (graph_nodes.node_id LIKE pattern)
      c) FTS on wiki page content (fallback for conceptual queries)
    Results ranked by how many signals matched, then by PageRank.

    Phase 2 — enrich top 3 files with symbols, importers, dependencies.
    """
    import re

    from repowise.core.persistence import (
        FullTextSearch,
        GraphEdge,
        GraphNode,
        WikiSymbol,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.database import resolve_db_url
    from sqlalchemy import select

    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None

    url = resolve_db_url(repo_path)
    engine = create_engine(url)

    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            repo_id = repo.id

            # Clean pattern for SQL LIKE — strip regex syntax
            clean = re.sub(r"[^\w/._-]", "", pattern)

            # Track how many signals each file matched + its PageRank
            file_scores: dict[str, float] = {}  # path -> score
            file_ranks: dict[str, float] = {}  # path -> pagerank

            # Signal 1: symbol name match — most precise
            if clean:
                sym_stmt = (
                    select(WikiSymbol.file_path)
                    .where(
                        WikiSymbol.repository_id == repo_id,
                        WikiSymbol.name.like(f"%{clean}%"),
                    )
                    .distinct()
                    .limit(5)
                )
                sym_result = await session.execute(sym_stmt)
                for (fp,) in sym_result.all():
                    file_scores[fp] = file_scores.get(fp, 0) + 2.0

            # Signal 2: file path match
            if clean:
                path_stmt = (
                    select(GraphNode.node_id, GraphNode.pagerank)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "file",
                        GraphNode.node_id.like(f"%{clean}%"),
                    )
                    .order_by(GraphNode.pagerank.desc())
                    .limit(5)
                )
                path_result = await session.execute(path_stmt)
                for node_id, pr in path_result.all():
                    file_scores[node_id] = file_scores.get(node_id, 0) + 1.5
                    file_ranks[node_id] = pr

            # Signal 3: FTS on wiki content — broadest, lowest weight
            fts = FullTextSearch(engine)
            try:
                fts_results = await fts.search(pattern, limit=5)
            except Exception:
                fts_results = []

            for r in fts_results:
                target = getattr(r, "target_path", None) or ""
                page_type = getattr(r, "page_type", "")
                if page_type and page_type not in (
                    "file", "file_page", "infra_page", "api_contract",
                ):
                    continue
                if "::" in target:
                    target = target.split("::")[0]
                if target:
                    file_scores[target] = file_scores.get(target, 0) + 1.0

            if not file_scores:
                return None

            # Fetch PageRank for files we don't have it for yet
            missing_pr = [fp for fp in file_scores if fp not in file_ranks]
            if missing_pr:
                pr_stmt = select(GraphNode.node_id, GraphNode.pagerank).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_type == "file",
                    GraphNode.node_id.in_(missing_pr),
                )
                pr_result = await session.execute(pr_stmt)
                for node_id, pr in pr_result.all():
                    file_ranks[node_id] = pr

            # Rank: primary by signal score, secondary by PageRank
            ranked = sorted(
                file_scores.keys(),
                key=lambda fp: (file_scores[fp], file_ranks.get(fp, 0)),
                reverse=True,
            )
            file_paths = ranked[:3]

            # Phase 2: enrich with symbols, importers, dependencies

            # Importers: who uses these files? (limit 3 per file)
            importers_stmt = select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(file_paths),
                GraphEdge.edge_type == "imports",
            )
            importers_result = await session.execute(importers_stmt)
            importers_by_file: dict[str, list[str]] = {}
            for edge in importers_result.scalars().all():
                lst = importers_by_file.setdefault(edge.target_node_id, [])
                if len(lst) < 3:
                    lst.append(edge.source_node_id)

            # Dependencies: what does this file use? (limit 2 per file)
            deps_stmt = select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.source_node_id.in_(file_paths),
                GraphEdge.edge_type == "imports",
            )
            deps_result = await session.execute(deps_stmt)
            deps_by_file: dict[str, list[str]] = {}
            for edge in deps_result.scalars().all():
                lst = deps_by_file.setdefault(edge.source_node_id, [])
                if len(lst) < 2:
                    lst.append(edge.target_node_id)

            # Symbols: key symbols (limit 3 per file)
            symbols_stmt = (
                select(WikiSymbol)
                .where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.file_path.in_(file_paths),
                )
                .order_by(WikiSymbol.start_line)
            )
            symbols_result = await session.execute(symbols_stmt)
            symbols_by_file: dict[str, list] = {}
            for sym in symbols_result.scalars().all():
                lst = symbols_by_file.setdefault(sym.file_path, [])
                if len(lst) < 3:
                    lst.append(sym)

        # Phase 3: Format
        lines = [f"[repowise] {len(file_paths)} related file(s) found:\n"]

        for fp in file_paths:
            lines.append(f"  {fp}")

            syms = symbols_by_file.get(fp, [])
            if syms:
                sym_strs = [f"{s.kind}:{s.name}" for s in syms]
                lines.append(f"    Symbols: {', '.join(sym_strs)}")

            imps = importers_by_file.get(fp, [])
            if imps:
                lines.append(f"    Imported by: {', '.join(imps)}")

            deps = deps_by_file.get(fp, [])
            if deps:
                lines.append(f"    Uses: {', '.join(deps)}")

            lines.append("")

        return "\n".join(lines)

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# PostToolUse — detect git commits and flag stale wiki
# ---------------------------------------------------------------------------

_GIT_COMMIT_PATTERNS = (
    "git commit",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git pull",
)


def _handle_post_tool_use(tool_input: dict, tool_output: dict, cwd: str) -> str | None:
    """After a successful git commit, check if the wiki needs updating."""
    # Only act on successful commands
    exit_code = tool_output.get("exit_code")
    if exit_code is None:
        # Try stdout-based detection (some hook formats differ)
        stdout = tool_output.get("stdout", "")
        if "error" in stdout.lower() or "fatal" in stdout.lower():
            return None
    elif exit_code != 0:
        return None

    cmd = tool_input.get("command", "")
    if not any(p in cmd for p in _GIT_COMMIT_PATTERNS):
        return None

    from pathlib import Path

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    last_sync = state.get("last_sync_commit")
    if not last_sync:
        return None

    # Compare HEAD against last sync
    try:
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return None

    if head == last_sync:
        return None

    return (
        "[repowise] Wiki is stale — last indexed at commit "
        f"{last_sync[:8]}, HEAD is now {head[:8]}. "
        "Run `repowise update` to refresh documentation and graph context."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_repo_root(cwd: "Path") -> "Path | None":
    """Walk up from cwd to find a directory with .repowise/."""
    from pathlib import Path

    current = Path(cwd).resolve()
    for _ in range(20):  # safety limit
        if (current / ".repowise").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
