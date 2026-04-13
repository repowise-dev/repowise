"""``repowise augment`` — hook-driven context enrichment for AI coding agents.

Reads a Claude Code hook payload from stdin (JSON), queries the local wiki.db
for graph context (importers, dependencies, symbols), and writes enriched
context back to stdout as the hook response.

Also handles PostToolUse events: detects git commits and notifies the agent
when the wiki is stale.

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

    if event == "PreToolUse" and tool_name in ("Grep", "Glob", "Bash"):
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
    if tool_name == "Grep":
        return tool_input.get("pattern")
    if tool_name == "Glob":
        return tool_input.get("pattern")
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Only augment grep/rg/find commands, not arbitrary bash
        import re

        m = re.search(r"\b(?:grep|rg|find|ag|ack)\b.*?['\"]([^'\"]+)['\"]", cmd)
        if m:
            return m.group(1)
        # Also match: grep pattern (unquoted, first non-flag arg)
        m = re.search(r"\b(?:grep|rg)\s+(?:-\S+\s+)*(\S+)", cmd)
        if m and not m.group(1).startswith("-"):
            return m.group(1)
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
    """Run FTS + graph queries and format the enrichment context."""
    from pathlib import Path as _Path

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
    )
    from repowise.core.persistence.database import resolve_db_url

    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None

    url = resolve_db_url(repo_path)
    engine = create_engine(url)

    try:
        # Phase 1: FTS search to find relevant files
        fts = FullTextSearch(engine)
        try:
            results = await fts.search(pattern, limit=5)
        except Exception:
            results = []

        if not results:
            # Fallback: try matching graph nodes by path pattern
            results = await _search_nodes_by_path(engine, repo_path, pattern)
            if not results:
                return None

        # Collect file paths from search results — prefer file-level pages
        file_paths = []
        for r in results:
            target = getattr(r, "target_path", None) or getattr(r, "node_id", None)
            if not target:
                continue
            # Skip non-file pages — they don't map to graph nodes
            page_type = getattr(r, "page_type", "")
            if page_type and page_type not in ("file", "file_page", "infra_page", "api_contract"):
                continue
            # Symbol spotlight pages have target_path like "file.py::SymbolName"
            if "::" in target:
                target = target.split("::")[0]
            file_paths.append(target)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_paths = []
        for fp in file_paths:
            if fp not in seen:
                seen.add(fp)
                unique_paths.append(fp)
        file_paths = unique_paths[:5]

        if not file_paths:
            # All FTS results were module-level — try path-based fallback
            results = await _search_nodes_by_path(engine, repo_path, pattern)
            file_paths = [r.node_id for r in results if hasattr(r, "node_id")][:5]

        if not file_paths:
            return None

        # Phase 2: Batch graph queries for importers, dependencies, and symbols
        from repowise.core.persistence import (
            GraphEdge,
            GraphNode,
            WikiSymbol,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import get_repository_by_path
        from sqlalchemy import select

        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            repo_id = repo.id

            # Importers: who imports these files? (limit 3 per file)
            importers_stmt = select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(file_paths),
            )
            importers_result = await session.execute(importers_stmt)
            importers_by_file: dict[str, list[str]] = {}
            for edge in importers_result.scalars().all():
                lst = importers_by_file.setdefault(edge.target_node_id, [])
                if len(lst) < 3:
                    lst.append(edge.source_node_id)

            # Dependencies: what do these files import?
            deps_stmt = select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.source_node_id.in_(file_paths),
            )
            deps_result = await session.execute(deps_stmt)
            deps_by_file: dict[str, list[str]] = {}
            for edge in deps_result.scalars().all():
                lst = deps_by_file.setdefault(edge.source_node_id, [])
                if len(lst) < 3:
                    lst.append(edge.target_node_id)

            # Symbols: key symbols in these files
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
                if len(lst) < 5:
                    lst.append(sym)

            # Hotspot info (best-effort — DB schema may not have all columns)
            git_by_file: dict = {}
            try:
                from repowise.core.persistence.models import GitMetadata

                git_stmt = select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path.in_(file_paths),
                )
                git_result = await session.execute(git_stmt)
                for gm in git_result.scalars().all():
                    git_by_file[gm.file_path] = gm
            except Exception:
                pass  # git metadata is optional enrichment

        # Phase 3: Format the enrichment text
        lines = [f"[repowise] {len(file_paths)} related file(s) found:\n"]

        for fp in file_paths[:5]:
            lines.append(f"  {fp}")

            # Symbols
            syms = symbols_by_file.get(fp, [])
            if syms:
                sym_strs = [f"{s.kind}:{s.name}" for s in syms]
                lines.append(f"    Symbols: {', '.join(sym_strs)}")

            # Importers
            imps = importers_by_file.get(fp, [])
            if imps:
                lines.append(f"    Imported by: {', '.join(imps)}")

            # Dependencies
            deps = deps_by_file.get(fp, [])
            if deps:
                lines.append(f"    Depends on: {', '.join(deps)}")

            # Git signals
            gm = git_by_file.get(fp)
            if gm:
                signals = []
                if gm.is_hotspot:
                    signals.append("HOTSPOT")
                if gm.bus_factor and gm.bus_factor <= 1:
                    signals.append(f"bus-factor={gm.bus_factor}")
                if gm.primary_owner_name:
                    signals.append(f"owner={gm.primary_owner_name}")
                if signals:
                    lines.append(f"    Git: {', '.join(signals)}")

            lines.append("")

        return "\n".join(lines)

    finally:
        await engine.dispose()


async def _search_nodes_by_path(engine, repo_path: "Path", pattern: str) -> list:
    """Fallback: search GraphNode.node_id by LIKE pattern."""
    from repowise.core.persistence import (
        GraphNode,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import get_repository_by_path
    from sqlalchemy import select

    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await get_repository_by_path(session, str(repo_path))
        if repo is None:
            return []
        # Clean pattern for LIKE query: strip regex chars, use as substring
        import re

        clean = re.sub(r"[^\w/._-]", "", pattern)
        if not clean:
            return []

        stmt = (
            select(GraphNode)
            .where(
                GraphNode.repository_id == repo.id,
                GraphNode.node_id.like(f"%{clean}%"),
            )
            .order_by(GraphNode.pagerank.desc())
            .limit(5)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


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
