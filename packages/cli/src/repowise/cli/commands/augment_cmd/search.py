"""PostToolUse Grep/Glob smart enrichment: rescue, triage, flood digest."""

from __future__ import annotations

from pathlib import Path

from ._shared import _extract_output_text, _find_repo_root

# Tunables — fixed thresholds keep the fire pattern predictable across
# repos. If these ever need to vary, derive them from indexed-row counts
# rather than exposing knobs (every knob is a way for the hook to drift).
_TRIAGE_THRESHOLD = 15  # grep result lines before we surface a ranking
_TRIAGE_TOP_N = 3
_RESCUE_TOP_N = 2
_DIGEST_THRESHOLD = 50  # grep result lines before the full compact digest
_DIGEST_TOP_FILES = 10


def _handle_search_post(
    tool_name: str,
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str = "",
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
            _log_search_firing(repo_path, session_id, "digest", output_text, digest)
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

    enrichment = asyncio.run(_search_enrich(repo_path, pattern, mode, result_count))
    if enrichment:
        _log_search_firing(repo_path, session_id, mode, pattern, enrichment)
    return enrichment


def _log_search_firing(
    repo_path: Path, session_id: str, category: str, keyed_on: str, text: str
) -> None:
    """Record one search enrichment in the shared ledger; measurement only.

    All hook surfaces share the sessions.db efficacy ledger so the miner can
    classify used vs ignored firings in one pass. Keyed on the category plus a
    content hash — the same rescue repeated in one session logs once. Never
    changes what the agent sees; any failure is silent.
    """
    if not session_id:
        return
    import hashlib

    from .decision_inject import _claim_ledger

    digest = hashlib.sha1(keyed_on.encode("utf-8", "replace")).hexdigest()[:12]
    _claim_ledger(
        repo_path,
        session_id,
        f"search:{category}:{digest}",
        node_id="",
        surface="search",
        category=category,
        chars=len(text),
    )


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
