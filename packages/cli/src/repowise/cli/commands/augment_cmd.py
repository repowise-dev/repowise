"""``repowise augment`` — hook-driven context enrichment for AI coding agents.

Reads Claude Code or Codex hook payloads from stdin (JSON) and writes
targeted enrichment back as a hook response.

Design philosophy: an enrichment hook is only valuable when it tells the
agent something the raw tool output didn't. Anything else is noise the
agent has to scroll past. So the hook fires on every Grep/Glob/Bash but
returns *nothing* most of the time, and only speaks up when there is
asymmetric, durable value:

  PostToolUse → Grep / Glob
    * Zero-result rescue: grep returned 0 hits but the wiki has a
      semantic match (FTS on docs, fuzzy symbol match, decision record
      mention). Surfaces the closest hit so the agent doesn't burn
      another round on a synonym.
    * Triage on flood: grep returned a large unfocused result set
      (>=_TRIAGE_THRESHOLD lines). Surfaces the top 3 files by
      PageRank so the agent can prioritise. The raw matches are still
      visible — this is just a ranking lens.
    * Skip otherwise: a focused result set means the agent already
      found what it wanted; further graph context is just noise.

Codex SessionStart/UserPromptSubmit: adds short repowise MCP usage guidance.

  PostToolUse → Bash
    * After a successful git commit/merge/rebase/cherry-pick/pull, if
      the wiki HEAD has drifted from .repowise/state.json's last sync
      commit AND no `repowise update` is in flight AND we haven't
      already warned for this HEAD, emit a one-line stale-wiki notice.

  PostToolUse → Read
    * Skeleton nudge: a large Read of an indexed file gets a one-line
      pointer at the skeleton surface (get_context include=["skeleton"]),
      with a cheap bounds-arithmetic estimate of the saving. Once per file
      per session.
    * Stale-read notice: when this file was Edited/Written after the
      session's previous Read of it, flag that earlier excerpts are stale.
      Once per file per session, never blocking.

  PostToolUse → Edit / Write
    * Record the edit in the per-session state file so a later Read can
      detect staleness. Emits nothing itself (Claude clients); Codex
      lifecycle hooks additionally get the index-staleness reminder below.

  PostToolUse → edit tools (Codex)
    * After file edits, emit a short reminder that the indexed context may
      be stale.

There is intentionally NO PreToolUse handling. Earlier versions enriched
every Grep/Glob unconditionally with importers/dependencies/symbols; in
practice this added noise on the >70% of searches where the agent had
already located what it wanted. PostToolUse is strictly more informed —
it can see the actual result count — and is the only entry point now.

Operational invariants:
  * No LLM calls, no network. Pure local SQLite + Python.
  * Cold start budget: well under the 10s hook timeout. Heavy imports
    (sqlalchemy, asyncio) are deferred until we actually have work.
  * Graceful failure: any unexpected error exits 0 with empty stdout
    so a repowise problem never surfaces in the agent transcript.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import click

# Tunables — fixed thresholds keep the fire pattern predictable across
# repos. If these ever need to vary, derive them from indexed-row counts
# rather than exposing knobs (every knob is a way for the hook to drift).
_TRIAGE_THRESHOLD = 15  # grep result lines before we surface a ranking
_TRIAGE_TOP_N = 3
_RESCUE_TOP_N = 2
_DIGEST_THRESHOLD = 50  # grep result lines before the full compact digest
_DIGEST_TOP_FILES = 10
_READ_NUDGE_MIN_LINES = 100  # Read output lines before a skeleton nudge
_READ_NUDGE_MIN_TOKENS = 3000  # full-file tokens below which a nudge is noise
_READ_NUDGE_MIN_SAVINGS = 1500  # estimated tokens saved must clear this
_READ_NUDGE_MAX_RATIO = 0.5  # skeleton must be at most this fraction of full


@click.command("augment")
@click.option(
    "--client",
    type=click.Choice(["codex"]),
    default=None,
    help="Hook client marker. Codex lifecycle hooks pass this explicitly.",
)
def augment_command(client: str | None = None) -> None:
    """Enrich AI agent tool calls with codebase graph context (hook mode)."""
    try:
        _run_augment(client=client)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        # Hooks must never fail — exit silently on any error.
        sys.exit(0)


def _run_augment(*, client: str | None = None) -> None:
    """Main entry point — reads stdin, dispatches to hook handlers."""
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

    if client == "codex" and event in ("SessionStart", "UserPromptSubmit"):
        result = _handle_codex_context_event(event, cwd)
        if result:
            _emit_response(event, result)
        return

    if event != "PostToolUse":
        return

    tool_output = payload.get("tool_response", payload.get("tool_output", {}))
    session_id = payload.get("session_id", "")
    result = _handle_post_tool_use(
        tool_name,
        tool_input,
        tool_output,
        cwd,
        client=client,
        session_id=session_id if isinstance(session_id, str) else "",
    )
    if result:
        _emit_response(event, result)


def _emit_response(event: str, context: str) -> None:
    """Write the hook JSON response to stdout.

    Suppressed when an identical emission was just produced (see
    :func:`_claim_emission`) so two concurrently-registered repowise hooks —
    one bundled in the Claude Code plugin, one written to
    ``~/.claude/settings.json`` by ``repowise init`` — can't echo the same
    enrichment block twice on a single tool event.
    """
    if not _claim_emission(event, context):
        return
    response = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


# Window within which an identical (event, context) emission is treated as a
# duplicate. Generous enough to cover two hook processes racing on the same
# tool event, short enough that a genuinely repeated search later still speaks.
_EMIT_DEDUP_TTL_SECONDS = 8.0


def _claim_emission(event: str, context: str) -> bool:
    """Return True if this caller may emit ``context`` for ``event`` now.

    Both repowise PostToolUse hooks fire on the same tool call and compute the
    same enrichment. A temp lock file keyed on the emission content lets
    exactly one win: the first caller creates it atomically and emits; a second
    caller within the TTL sees a fresh marker and stays silent. Fail-open — any
    error returns True so a dedup glitch can never swallow a real emission.
    """
    import hashlib
    import os
    import tempfile
    import time

    try:
        key = hashlib.sha1(f"{event}\x00{context}".encode()).hexdigest()[:16]
        marker = Path(tempfile.gettempdir()) / f".repowise-augment-{key}"
        now = time.time()
        try:
            # O_EXCL: only the first concurrent caller creates the file.
            fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(now).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            # Someone emitted recently — defer unless the marker is stale, in
            # which case take it over (handles a real repeat search later, and
            # a crashed prior run that left the marker behind).
            try:
                if now - marker.stat().st_mtime <= _EMIT_DEDUP_TTL_SECONDS:
                    return False
            except OSError:
                return True
            with contextlib.suppress(OSError):
                marker.write_text(str(now), encoding="utf-8")
            return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Codex SessionStart/UserPromptSubmit — lightweight MCP guidance
# ---------------------------------------------------------------------------


def _handle_codex_context_event(event: str, cwd: str) -> str | None:
    """Return short Codex developer context when repowise is initialized."""
    if event not in ("SessionStart", "UserPromptSubmit"):
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    return (
        "[repowise] This repository has a local codebase wiki and graph index. "
        "Use the repowise MCP tools for architecture overview, semantic search, "
        "implementation context, risk/hotspot checks, decision history, and "
        "dead-code analysis. After meaningful edits or git operations, run "
        "`repowise update` when refreshed context is needed."
    )


# ---------------------------------------------------------------------------
# PostToolUse — Grep / Glob: smart enrichment
# ---------------------------------------------------------------------------


def _handle_search_post(
    tool_name: str,
    tool_input: dict,
    tool_output: object,
    cwd: str,
) -> str | None:
    """Decide whether to enrich a Grep/Glob result and how."""
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    result_count = _search_result_count(tool_output)
    if result_count is None:
        # Unknown/unextractable response shape. Skipping is the only safe
        # answer — treating it as zero results would fire a "no match"
        # rescue under a Grep that actually succeeded.
        return None
    output_text = _extract_output_text(tool_output)

    # A genuine flood gets a compact per-file digest regardless of what the
    # pattern looks like — it summarizes the actual results, not the concept.
    if result_count >= _DIGEST_THRESHOLD:
        digest = _grep_flood_digest(repo_path, output_text)
        if digest:
            return digest
        # Unparseable output (e.g. Glob path lists): fall through to triage.

    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None

    # Path-style lookups don't benefit from semantic enrichment — the agent
    # is reading literal locations, not exploring a concept.
    if _looks_like_path_lookup(pattern):
        return None

    # Decision tree. The skip case is the most common — that's by design.
    if result_count == 0:
        # Rescue relevance guards. A regex pattern loses its structure in the
        # symbol-lookup sanitizer (`distill|savings` → `distillsavings`), so
        # any "closest symbol" answer would be luck, not signal. And a grep
        # scoped to one non-code file is a config-key check, not a symbol
        # hunt — the wiki has nothing useful to add to either.
        if _looks_like_regex(pattern) or _targets_single_non_code_file(tool_input):
            return None
        mode = "rescue"
    elif result_count >= _TRIAGE_THRESHOLD:
        mode = "triage"
    else:
        return None

    import asyncio

    return asyncio.run(_search_enrich(repo_path, pattern, mode, result_count))


def _grep_flood_digest(repo_path: Path, output_text: str) -> str | None:
    """Compact per-file digest of a Grep flood, index-ranked when possible.

    Cannot replace the tool output (PostToolUse is additionalContext only),
    so this is positioned as a digest the agent can navigate by instead of
    scanning hundreds of match lines. Grouping is pure text work from the
    shared distill filter; PageRank ordering is attempted against the index
    and silently skipped when there is no graph (plain count order then).
    """
    from repowise.core.distill.filters.search_results import (
        group_search_matches,
        render_search_digest,
    )

    groups = group_search_matches(output_text)
    if groups is None or len(groups) < 3:
        # One or two files: the raw output is already navigable.
        return None

    file_order = None
    ranked_by_graph = False
    try:
        import asyncio

        file_order = asyncio.run(_pagerank_file_order(repo_path, list(groups.keys())))
        ranked_by_graph = file_order is not None
    except Exception:
        file_order = None

    if file_order is None:
        file_order = sorted(groups, key=lambda p: -len(groups[p]))

    digest = render_search_digest(groups, file_order=file_order, max_files=_DIGEST_TOP_FILES)
    order_note = "graph centrality" if ranked_by_graph else "match count"
    return f"[repowise] Search flood — compact digest (files ordered by {order_note}):\n{digest}"


async def _pagerank_file_order(repo_path: Path, paths: list[str]) -> list[str] | None:
    """Order *paths* by indexed PageRank, or None when the graph can't help."""
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        # Bail before the sqlalchemy imports — unindexed repos shouldn't pay
        # the heavy-import cost for a digest that falls back to count order.
        return None

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.database import resolve_db_url

    # Grep output paths may be absolute or OS-native; graph node ids are
    # repo-relative POSIX. Normalize both ways and keep the original spelling.
    normalized: dict[str, str] = {}
    repo_posix = repo_path.as_posix().rstrip("/") + "/"
    for p in paths:
        norm = p.replace("\\", "/").removeprefix("./")
        if norm.startswith(repo_posix):
            norm = norm[len(repo_posix) :]
        normalized[norm] = p

    engine = create_engine(resolve_db_url(repo_path))
    try:
        from sqlalchemy import select

        from repowise.core.persistence import GraphNode

        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            stmt = select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.repository_id == repo.id,
                GraphNode.node_type == "file",
                GraphNode.node_id.in_(normalized.keys()),
            )
            rows = (await session.execute(stmt)).all()
    finally:
        await engine.dispose()

    if not rows:
        return None
    rank = {normalized[node_id]: pr or 0.0 for node_id, pr in rows if node_id in normalized}
    ranked = sorted(rank, key=lambda p: -rank[p])
    rest = [p for p in paths if p not in rank]
    return ranked + rest


def _looks_like_path_lookup(pattern: str) -> bool:
    """Heuristic: pattern is a literal file path, not a search concept.

    Path-style queries that should skip enrichment:
      - Contains a directory separator (``/`` or ``\\``).
      - Ends with a known source extension (``.py``, ``.ts``, ``.tsx``,
        ``.js``, ``.jsx``, ``.go``, ``.rs``, ``.java``, ``.kt``, etc.).
      - Looks like a glob over files (``*.py``, ``**/*.ts``).

    These are agents looking up specific files; semantic enrichment of
    such queries duplicates information the result already provides.
    """
    if "/" in pattern or "\\" in pattern:
        return True
    lower = pattern.lower().rstrip()
    exts = (
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".rb",
        ".php",
        ".cs",
        ".swift",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".lua",
        ".sql",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".md",
    )
    return lower.endswith(exts)


def _looks_like_regex(pattern: str) -> bool:
    """Heuristic: pattern uses regex syntax, not a literal symbol name.

    Flags unescaped alternation/class/group openers and the common regex
    idioms (``\\b``, ``.*``, ``.+``) that agents reach for. Escaped literals
    (``\\[``, ``\\|``) stay eligible for rescue.
    """
    import re

    return re.search(r"(?<!\\)[|\[(]|\\b|\.[*+]", pattern) is not None


# Extensions where a zero-match grep is a config/doc lookup, not a missed
# symbol — rescue would answer a question the agent isn't asking.
_NON_CODE_SUFFIXES = (
    ".yaml",
    ".yml",
    ".json",
    ".jsonc",
    ".toml",
    ".ini",
    ".cfg",
    ".md",
    ".rst",
    ".txt",
    ".lock",
    ".env",
)


def _targets_single_non_code_file(tool_input: dict) -> bool:
    """True when the Grep was scoped to one non-code file (path or glob)."""
    if not isinstance(tool_input, dict):
        return False
    path = tool_input.get("path")
    if isinstance(path, str) and path.lower().rstrip("/\\").endswith(_NON_CODE_SUFFIXES):
        return True
    glob = tool_input.get("glob")
    return (
        isinstance(glob, str)
        and "*" not in glob
        and "?" not in glob
        and glob.lower().endswith(_NON_CODE_SUFFIXES)
    )


def _extract_output_text(tool_output: object) -> str:
    """Pull the textual portion of a Claude Code tool_output, defensively.

    Claude Code's hook payload shape varies by tool: Bash/PowerShell
    surface ``stdout``/``stderr``, Grep surfaces a structured dict
    (``mode``/``content``/``filenames``), Glob a bare ``filenames`` list.
    We only need a string we can count newlines in, so we accept any of
    the shapes captured from real hook payloads.
    """
    if isinstance(tool_output, str):
        return tool_output
    if not isinstance(tool_output, dict):
        return ""
    for key in ("output", "result", "content", "stdout", "text"):
        val = tool_output.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list):
            # Some shapes wrap content as [{"type": "text", "text": "..."}].
            parts = []
            for item in val:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "\n".join(parts)
    # Grep files_with_matches / Glob: a list of paths, one per line.
    filenames = tool_output.get("filenames")
    if isinstance(filenames, list):
        parts = [f for f in filenames if isinstance(f, str)]
        if parts:
            return "\n".join(parts)
    return ""


def _search_result_count(tool_output: object) -> int | None:
    """Result count for a Grep/Glob tool_response, or None when unknowable.

    Claude Code's Grep responses are structured dicts whose shape varies by
    output mode (all captured from real PostToolUse payloads):

      content            {"mode": "content", "content": str, "numLines": int, ...}
      files_with_matches {"mode": "files_with_matches", "filenames": [...], "numFiles": int}
      count              {"mode": "count", "content": str, "numMatches": int, ...}
      Glob               {"filenames": [...], "numFiles": int, "truncated": bool}

    Structured counts are trusted as-is — including a genuine zero. For
    anything else we fall back to counting extracted text lines, where a
    zero can only come from an explicit no-match sentinel. An empty or
    unrecognized response returns None: the caller must SKIP, never rescue,
    on a shape we don't positively understand.
    """
    if isinstance(tool_output, dict):
        mode = tool_output.get("mode")
        filenames = tool_output.get("filenames")
        if mode == "files_with_matches" or (
            mode is None and isinstance(filenames, list) and "numFiles" in tool_output
        ):
            num_files = tool_output.get("numFiles")
            if isinstance(num_files, int):
                return num_files
            return len(filenames) if isinstance(filenames, list) else None
        if mode in ("content", "count"):
            count_key = "numLines" if mode == "content" else "numMatches"
            count = tool_output.get(count_key)
            if isinstance(count, int):
                return count
            content = tool_output.get("content")
            if isinstance(content, str):
                return _count_search_results(content) if content.strip() else 0
            return None
        if isinstance(mode, str):
            # A future Grep output mode we don't know — refuse to guess.
            return None

    output_text = _extract_output_text(tool_output)
    if not output_text.strip():
        return None
    return _count_search_results(output_text)


def _count_search_results(output_text: str) -> int:
    """Count tool-result lines, treating Grep/Glob 'no match' as zero."""
    if not output_text or not output_text.strip():
        return 0
    stripped = output_text.strip()
    # Common no-match sentinels emitted by Claude Code's Grep/Glob tool.
    zero_markers = (
        "no matches found",
        "no files found",
        "no files matched",
        "found 0 files",
        "found 0 matches",
    )
    head = stripped.lower().splitlines()[0] if stripped else ""
    if any(marker in head for marker in zero_markers):
        return 0
    # Strip a "Found N files\n" / "Found N matches\n" header if present —
    # the count we want is the actual result lines, not the banner.
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and lines[0].lower().startswith("found "):
        lines = lines[1:]
    return len(lines)


async def _search_enrich(
    repo_path: object,
    pattern: str,
    mode: str,
    result_count: int,
) -> str | None:
    """Run the rescue or triage query against the wiki and format output."""
    import re

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.database import resolve_db_url

    repo_path = Path(repo_path)
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

            clean = re.sub(r"[^\w./_-]", "", pattern).strip("./")

            if mode == "rescue":
                return await _rescue(session, engine, repo_id, pattern, clean)
            if mode == "triage":
                return await _triage(session, repo_id, pattern, clean, result_count)
            return None
    finally:
        await engine.dispose()


async def _rescue(
    session,
    engine,
    repo_id: int,
    pattern: str,
    clean: str,
) -> str | None:
    """Zero-result rescue: grep missed but the wiki has a semantic hit.

    Looks for the closest match in three places, in priority order:

      1. Fuzzy symbol name match — handles snake_case ↔ camelCase ↔
         PascalCase drift. ``parse_yaml`` finds ``parseYaml`` /
         ``ParseYaml`` / ``yaml_parser``.
      2. FTS on wiki page content — handles conceptual misses where
         the agent grepped for a synonym ("session" but the codebase
         calls it "context").
      3. Skip — if neither signal hits, we have nothing useful to add.

    Output is a single line so it can't be confused with a real result.
    """
    from sqlalchemy import or_, select

    from repowise.core.persistence import (
        FullTextSearch,
        WikiSymbol,
    )

    if not clean:
        return None

    # Build a small set of token variants. Cheap; helps catch case-style
    # drift without a heavy similarity index.
    variants = _name_variants(clean)
    like_clauses = [WikiSymbol.name.ilike(f"%{v}%") for v in variants]
    sym_stmt = (
        select(WikiSymbol.name, WikiSymbol.kind, WikiSymbol.file_path, WikiSymbol.start_line)
        .where(WikiSymbol.repository_id == repo_id, or_(*like_clauses))
        .limit(_RESCUE_TOP_N)
    )
    rows = (await session.execute(sym_stmt)).all()
    if rows:
        # Rank: prefer exact-token-equal matches; then shortest name (most
        # specific). All ties broken by file path lex order for stability.
        def _rank(row):
            name = (row[0] or "").lower()
            exact = name in {v.lower() for v in variants}
            return (not exact, len(name), row[2] or "")

        rows = sorted(rows, key=_rank)[:_RESCUE_TOP_N]
        first = rows[0]
        line = f":{first[3]}" if first[3] else ""
        extras = ""
        if len(rows) > 1:
            extras = f" (+{len(rows) - 1} more)"
        return (
            f"[repowise] No literal match for `{pattern}`. Closest indexed symbol: "
            f"{first[1]} `{first[0]}` in {first[2]}{line}{extras}"
        )

    # Fall back to FTS on wiki content. Only return if the FTS row actually
    # points at a code page (file/module/api), not a generic doc page.
    fts = FullTextSearch(engine)
    try:
        fts_rows = await fts.search(pattern, limit=3)
    except Exception:
        fts_rows = []
    for r in fts_rows:
        target = getattr(r, "target_path", None) or ""
        page_type = getattr(r, "page_type", "") or ""
        if "::" in target:
            target = target.split("::")[0]
        if target and page_type in (
            "file",
            "file_page",
            "module_page",
            "api_contract",
            "infra_page",
        ):
            return (
                f"[repowise] No literal match for `{pattern}`. "
                f"Wiki suggests `{target}` ({page_type})."
            )
    return None


async def _triage(
    session,
    repo_id: int,
    pattern: str,
    clean: str,
    result_count: int,
) -> str | None:
    """Big-result triage: surface top files by PageRank.

    The grep result set has too many lines for the agent to scan
    efficiently. Without overriding the agent's literal results, we
    point at the top _TRIAGE_TOP_N files (by structural centrality)
    that contain the pattern in either symbol or path.

    Output is one line plus an enumerated list. Three lines max.
    """
    from sqlalchemy import select

    from repowise.core.persistence import GraphNode, WikiSymbol

    if not clean:
        return None

    # Files that contain a symbol whose name matches, or whose own path
    # matches. Either way we can rank by PageRank from graph_nodes.
    sym_files_stmt = (
        select(WikiSymbol.file_path)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.ilike(f"%{clean}%"),
        )
        .distinct()
        .limit(50)
    )
    sym_files = {r[0] for r in (await session.execute(sym_files_stmt)).all() if r[0]}

    path_stmt = (
        select(GraphNode.node_id)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "file",
            GraphNode.node_id.ilike(f"%{clean}%"),
        )
        .limit(50)
    )
    path_files = {r[0] for r in (await session.execute(path_stmt)).all() if r[0]}

    candidates = sym_files | path_files
    if not candidates:
        return None

    pr_stmt = select(GraphNode.node_id, GraphNode.pagerank).where(
        GraphNode.repository_id == repo_id,
        GraphNode.node_type == "file",
        GraphNode.node_id.in_(candidates),
    )
    pr_rows = (await session.execute(pr_stmt)).all()
    if not pr_rows:
        return None

    ranked = sorted(pr_rows, key=lambda r: r[1] or 0.0, reverse=True)[:_TRIAGE_TOP_N]
    if not ranked:
        return None

    header = f"[repowise] {result_count}+ matches for `{pattern}`. Top files by graph centrality:"
    lines = [header] + [f"  {row[0]}" for row in ranked]
    return "\n".join(lines)


def _name_variants(token: str) -> list[str]:
    """Generate snake_case ↔ camelCase ↔ PascalCase variants for fuzzy match.

    Cheap to compute, and catches the most common naming-drift class
    that causes literal grep to miss what the wiki has indexed.
    """
    import re

    token = token.strip("_-./")
    if not token:
        return []
    seen: list[str] = []
    candidates = {token, token.lower(), token.upper()}
    # snake_case → camelCase / PascalCase
    if "_" in token:
        parts = [p for p in token.split("_") if p]
        if parts:
            candidates.add("".join(p.capitalize() for p in parts))
            candidates.add(parts[0].lower() + "".join(p.capitalize() for p in parts[1:]))
    # camelCase / PascalCase → snake_case
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", token).lower()
    if snake != token.lower():
        candidates.add(snake)
    # Dedup while preserving insertion order roughly.
    for c in candidates:
        if c and c not in seen:
            seen.append(c)
    return seen


# ---------------------------------------------------------------------------
# PostToolUse — Read / Edit / Write: per-session read intelligence
# ---------------------------------------------------------------------------
#
# A small JSON state file under .repowise/ tracks which files this agent
# session has Read and Edited, keyed by the hook payload's session_id. Two
# behaviors ride on it:
#
#   * Stale-read notice — a Read of a file whose previous Read predates a
#     recorded Edit/Write gets a one-line "earlier excerpts are stale" flag.
#   * Skeleton nudge — a large Read of an indexed file gets a pointer at
#     get_context(include=["skeleton"]) with a bounds-arithmetic estimate.
#
# Rate limiting is the state file itself (per-file, per-session lists), NOT
# the _claim_emission temp marker — that TTL-based dedup only suppresses the
# two concurrently-registered hooks racing on one tool event, which still
# applies on top. A new session_id resets the state.


def _session_state_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / ".augment-session.json"


def _load_session_state(repo_path: Path, session_id: str) -> dict:
    """Load the per-session read/edit state, resetting on session change.

    ``reads``/``edits`` map repo-relative paths to a monotonically increasing
    per-session sequence number (``seq``) rather than wall-clock time — two
    hook events can land within one clock tick on Windows, and ordering is
    the only thing the stale-read comparison needs.
    """
    fresh = {
        "session_id": session_id,
        "seq": 0,
        "reads": {},
        "edits": {},
        "nudged": [],
        "stale_notified": [],
        "reread_notified": [],
    }
    try:
        state = json.loads(_session_state_path(repo_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        return fresh
    for key, default in fresh.items():
        if not isinstance(state.get(key), type(default)):
            state[key] = default
    return state


def _save_session_state(repo_path: Path, state: dict) -> None:
    """Persist session state; trims unbounded growth, never raises."""
    for key in ("reads", "edits"):
        entries = state.get(key, {})
        if len(entries) > 500:
            keep = sorted(entries, key=entries.get, reverse=True)[:400]
            state[key] = {k: entries[k] for k in keep}
    with contextlib.suppress(OSError, TypeError, ValueError):
        _session_state_path(repo_path).write_text(json.dumps(state), encoding="utf-8")


def _relativize(file_path: str, repo_path: Path) -> str | None:
    """Repo-relative POSIX path for *file_path*, or None when outside it."""
    try:
        rel = Path(file_path).resolve().relative_to(Path(repo_path).resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _record_edit(tool_input: dict, cwd: str, session_id: str) -> None:
    """Note an Edit/Write so a later Read of the file can flag staleness."""
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return
    state = _load_session_state(repo_path, session_id)
    state["seq"] += 1
    state["edits"][rel] = state["seq"]
    _save_session_state(repo_path, state)


def _handle_read_post(
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str,
) -> str | None:
    """Stale-read notice + skeleton nudge for a completed Read."""
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return None

    state = _load_session_state(repo_path, session_id)
    notices: list[str] = []

    # Stale-read: this session Read the file, then Edited/Wrote it, and is
    # now Reading it again. The fresh Read is fine — the flag is about any
    # reasoning still anchored on the pre-edit excerpt.
    last_read = state["reads"].get(rel)
    last_edit = state["edits"].get(rel)
    edited_since_read = last_read is not None and last_edit is not None and last_read < last_edit
    if edited_since_read and rel not in state["stale_notified"]:
        state["stale_notified"].append(rel)
        notices.append(
            f"[repowise] {rel} changed (Edit/Write) after your previous read of it — "
            "excerpts from before that edit are stale."
        )

    # Re-read: already read this session, unchanged since (the read→edit→read
    # case is the stale notice above), and this is a full re-read of a
    # non-trivial file — the content is still in context, so re-reading the
    # whole file just re-bills it. A targeted range re-read is the behavior
    # we'd recommend, so it is deliberately not flagged; the line floor (the
    # skeleton nudge's) keeps small files from generating noise.
    partial_read = isinstance(tool_input, dict) and (
        tool_input.get("offset") is not None or tool_input.get("limit") is not None
    )
    if (
        last_read is not None
        and not edited_since_read
        and not partial_read
        and rel not in state["reread_notified"]
        and _read_output_line_count(tool_output) >= _READ_NUDGE_MIN_LINES
    ):
        state["reread_notified"].append(rel)
        notices.append(
            f"[repowise] You already read {rel} this session and it is unchanged — "
            "its content is still in context. For a specific symbol use "
            f'get_symbol("{rel}::Name") or a line-range read instead of re-reading the file.'
        )

    state["seq"] += 1
    state["reads"][rel] = state["seq"]

    nudge = _skeleton_nudge(repo_path, rel, tool_output, state)
    if nudge:
        notices.append(nudge)

    _save_session_state(repo_path, state)
    return "\n".join(notices) if notices else None


def _read_output_line_count(tool_output: object) -> int:
    """Line count of a Read result across the hook payload shapes we see."""
    if isinstance(tool_output, dict):
        file_block = tool_output.get("file")
        if isinstance(file_block, dict):
            n = file_block.get("numLines")
            if isinstance(n, int):
                return n
            content = file_block.get("content")
            if isinstance(content, str):
                return content.count("\n") + 1
    text = _extract_output_text(tool_output)
    return (text.count("\n") + 1) if text.strip() else 0


def _skeleton_nudge(repo_path: Path, rel: str, tool_output: object, state: dict) -> str | None:
    """One-line skeleton pointer for a large Read of an indexed file.

    Cheap by construction: bails before any non-stdlib import when the repo
    has no wiki.db, and the size estimate is pure bounds arithmetic — the
    skeleton itself is never rendered on the hook path.
    """
    if rel in state["nudged"]:
        return None
    if _read_output_line_count(tool_output) < _READ_NUDGE_MIN_LINES:
        return None
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None

    bounds = _file_symbol_bounds(db_path, rel)
    if not bounds:
        return None
    try:
        size = (repo_path / rel).stat().st_size
    except OSError:
        return None
    full_tokens = size // 4
    if full_tokens < _READ_NUDGE_MIN_TOKENS:
        return None

    from repowise.core.distill.skeleton import estimate_skeleton_tokens

    skeleton_tokens = estimate_skeleton_tokens(bounds, file_size_bytes=size)
    if skeleton_tokens > full_tokens * _READ_NUDGE_MAX_RATIO:
        return None
    # A nudge is only worth the agent's attention when acting on it buys a
    # real saving — a few hundred tokens on a mid-size file is noise.
    if full_tokens - skeleton_tokens < _READ_NUDGE_MIN_SAVINGS:
        return None

    state["nudged"].append(rel)
    return (
        f"[repowise] A skeleton of {rel} is ~{skeleton_tokens} tokens vs ~{full_tokens} "
        f'for the full file. For structure-level questions use get_context(["{rel}"], '
        'include=["skeleton"]).'
    )


def _file_symbol_bounds(db_path: Path, rel: str) -> list[tuple[int, int]]:
    """Persisted (start_line, end_line) pairs for one file, or [] on any miss.

    Direct read-only stdlib sqlite3 — the hook path must not pay the
    sqlalchemy import for two integers per symbol.
    """
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT start_line, end_line FROM wiki_symbols WHERE file_path = ?",
                (rel,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [(s, e) for s, e in rows if isinstance(s, int) and isinstance(e, int) and s > 0]


# ---------------------------------------------------------------------------
# PostToolUse — Bash: stale-wiki detection after git commits
# ---------------------------------------------------------------------------

_GIT_COMMIT_PATTERNS = (
    "git commit",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git pull",
)

_EDIT_TOOL_NAMES = {"apply_patch", "Edit", "Write"}


def _handle_post_tool_use(
    tool_name: str,
    tool_input: dict,
    tool_output: dict | str,
    cwd: str,
    *,
    client: str | None = None,
    session_id: str = "",
) -> str | None:
    """Dispatch PostToolUse events from Claude or Codex."""
    # The edit-tool freshness notice is a Codex-only lifecycle hook, gated on
    # the Codex client so the widened Claude matcher (Read|Edit|Write) can't
    # emit Codex-flavored banners to Claude Code users. Both clients record
    # the edit for the per-session stale-read state machine first.
    if tool_name in _EDIT_TOOL_NAMES:
        _record_edit(tool_input, cwd, session_id)
        if client == "codex":
            return _handle_post_edit_use(cwd)
        return None
    if tool_name == "Read":
        return _handle_read_post(tool_input, tool_output, cwd, session_id)
    if tool_name in ("Bash", "PowerShell"):
        # The PowerShell tool (Windows Claude Code) surfaces the same
        # stdout/stderr response shape as Bash — one handler covers both.
        return _handle_bash_post(tool_input, tool_output, cwd)
    if tool_name in ("Grep", "Glob"):
        return _handle_search_post(tool_name, tool_input, tool_output, cwd)
    return None


def _handle_post_edit_use(cwd: str) -> str | None:
    """After a Codex edit tool completes, flag that indexed context may be stale."""
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.exists():
        return None

    return (
        "[repowise] Files were edited after the last indexed snapshot. "
        "Run `repowise update` before relying on refreshed docs, graph context, "
        "risk checks, or dead-code results."
    )


def _handle_bash_post(tool_input: dict, tool_output: object, cwd: str) -> str | None:
    """After a successful git commit, check if the wiki needs updating.

    Three-state response to "state.json is behind HEAD":

      1. A real ``.update.lock`` is held → an update is actively running.
         Emit a *positive* notice ("updating in background") so the agent
         knows the system is healing itself. Squelches the noisy stale
         warning during the long tail of large updates.

      2. A fresh ``.update.queued`` marker exists (post-commit hook just
         spawned a new update but the lock file isn't on disk yet) → also
         emit the positive notice. Closes the race window between commit
         and update start where we'd otherwise warn for ~5s.

      3. Neither marker → the update didn't run or already finished. Warn
         once per HEAD as before, so the user knows the wiki is genuinely
         out of sync.
    """
    output = tool_output if isinstance(tool_output, dict) else {"stdout": str(tool_output)}
    exit_code = _extract_exit_code(output)
    if exit_code is None:
        stdout = output.get("stdout", "")
        stderr = output.get("stderr", "")
        combined = f"{stdout}\n{stderr}".lower()
        if "error" in combined or "fatal" in combined:
            return None
    elif exit_code != 0:
        return None

    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not any(p in cmd for p in _GIT_COMMIT_PATTERNS):
        return None

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

    # Active update? Tell the agent the system is healing itself instead of
    # repeating the stale warning every commit. Per-head de-dup applies
    # equally to the positive notice so the chat doesn't get a notice spam
    # for the same HEAD over multiple tool calls.
    in_flight = _read_in_flight_marker(repo_path)
    if in_flight is not None:
        if _already_warned(repo_path, head):
            return None
        _record_warning(repo_path, head)
        target_short = (in_flight.get("target_commit") or head)[:8]
        elapsed = in_flight.get("elapsed_seconds")
        elapsed_str = (
            f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
        )
        return (
            f"[repowise] Wiki update in background — {elapsed_str}, "
            f"target {target_short}. State will catch up once it finishes."
        )

    if _already_warned(repo_path, head):
        return None
    _record_warning(repo_path, head)

    docs_enabled = state.get("docs_enabled", True)
    artifact = "Wiki" if docs_enabled else "Index"
    return (
        f"[repowise] {artifact} is stale — last indexed at commit "
        f"{last_sync[:8]}, HEAD is now {head[:8]}. "
        "Run `repowise update` to refresh documentation and graph context."
    )


def _read_in_flight_marker(repo_path: object) -> dict | None:
    """Return a normalised in-flight marker, or None when nothing is running.

    Considers two on-disk signals as evidence of an in-flight update:

      * ``.update.lock``   — written by ``update_cmd`` once it starts the
        actual work. Authoritative.
      * ``.update.queued`` — written by the post-commit hook *before*
        backgrounding the update, to close the start-up race window.

    Both have a freshness window so an aborted run can't suppress real
    warnings indefinitely.
    """
    import time
    from pathlib import Path

    repo_path = Path(repo_path)
    now = time.time()

    # Delegate lock freshness to the canonical reader: it layers a live-PID
    # probe on top of the wall-clock window, so a crashed update's leftover
    # lock can't suppress real stale-wiki warnings until the window expires.
    from repowise.cli.helpers import read_update_lock

    try:
        payload = read_update_lock(repo_path)
    except Exception:
        payload = None
    if payload is not None:
        started = payload.get("started_at")
        if isinstance(started, (int, float)):
            return {
                "source": "lock",
                "target_commit": payload.get("target_commit"),
                "elapsed_seconds": now - started,
            }

    queued_path = repo_path / ".repowise" / ".update.queued"
    if queued_path.exists():
        try:
            payload = json.loads(queued_path.read_text(encoding="utf-8"))
            queued_at = payload.get("queued_at")
            if isinstance(queued_at, (int, float)) and now - queued_at <= 5 * 60:
                return {
                    "source": "queued",
                    "target_commit": payload.get("target_commit"),
                    "elapsed_seconds": now - queued_at,
                }
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _already_warned(repo_path: object, head: str) -> bool:
    from pathlib import Path

    marker = Path(repo_path) / ".repowise" / ".augment-warned"
    if not marker.exists():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == head
    except OSError:
        return False


def _record_warning(repo_path: object, head: str) -> None:
    from pathlib import Path

    marker = Path(repo_path) / ".repowise" / ".augment-warned"
    with contextlib.suppress(OSError):
        marker.write_text(head, encoding="utf-8")


def _extract_exit_code(tool_output: dict) -> int | None:
    """Extract a process exit code from known hook output shapes."""
    for key in ("exit_code", "exitCode", "status"):
        value = tool_output.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_repo_root(cwd: Path) -> Path | None:
    """Walk up from cwd to find a directory with .repowise/."""
    current = Path(cwd).resolve()
    for _ in range(20):
        if (current / ".repowise").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
