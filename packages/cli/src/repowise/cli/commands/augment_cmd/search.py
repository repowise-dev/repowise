"""PostToolUse Grep/Glob smart enrichment: rescue, triage, flood digest.

Every surface here answers a question **about the agent's own results**, so
each one starts by reading them: :func:`_matched_files` parses the grep output
into ``node id -> match count`` and nothing downstream may speak without it.
That is the correction plan items 8 and 10 exist for. Triage used to build its
candidates from a ``WikiSymbol`` ILIKE and rank them by PageRank without ever
looking at the output, which let a confident three-line answer name a file the
search had not matched; rescue used to be reachable only at exactly zero
results, which is the cheapest possible proxy for "the agent is missing
something".

The digest is the exception and stays index-free by design: it summarizes the
flood rather than reasoning about it, so it works on an unindexed repo.

Rescue, triage and the appended digest are **advisory**: they add context
next to the tool output, they do not replace it. They therefore spend tokens
and are not billed to the savings ledger; only the *served* digest
(:mod:`search_digest`) replaces bytes and records a saving.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import fast_lookup
from ._shared import HookResult, _extract_output_text, _find_repo_root

# NOTE: repowise.core.persistence.sql is imported inside _rescue/_triage, not
# here. At module scope it costs ~790ms on every single hook invocation — it
# pulls persistence -> crud -> analysis -> dead_code.analyzer — and command.py
# imports this module unconditionally, so a Read or Bash hook that never runs
# a search was paying the whole bill. Both users are already async functions
# that defer their sqlalchemy imports; this is the same rule applied one line
# further. Keep it deferred.
#
# The three lookups that no longer need it at all go through ``fast_lookup``,
# which is stdlib ``sqlite3`` only and therefore safe at module scope. Every
# fast path here falls back to the ORM one below it, never to silence.

# Tunables — fixed thresholds keep the fire pattern predictable across
# repos. If these ever need to vary, derive them from indexed-row counts
# rather than exposing knobs (every knob is a way for the hook to drift).
_TRIAGE_THRESHOLD = 15  # grep result lines before we surface a ranking
_TRIAGE_TOP_N = 3
_RESCUE_TOP_N = 2
# Exact-name rows the widened rescue fetches before ranking. Its gate asks
# about the *top* match's file, so an arbitrary two-of-many would make the
# answer depend on row order; this is small enough to stay one indexed lookup
# and large enough that the ranking, not the LIMIT, picks the winner.
_RESCUE_EXACT_FETCH = 8
# Files the triage ranker considers. Ranking is over the grep's own matches,
# so this only bites on floods wider than 200 files, where the tail is taken
# by match count, the one signal available before any index lookup.
_TRIAGE_MAX_CANDIDATES = 200
# Reciprocal Rank Fusion constant, k=60 from the original RRF paper. Same
# value as ``_fused_retrieve`` in the MCP search tool, which fuses the same
# way over a different set of legs.
_RRF_K = 60
_DIGEST_THRESHOLD = 50  # grep result lines before the full compact digest
_DIGEST_TOP_FILES = 10


def _handle_search_post(
    tool_name: str,
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str = "",
    client: str | None = None,
) -> HookResult:
    """Decide whether to enrich a Grep/Glob result and how."""
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return HookResult()

    result_count = _search_result_count(tool_output)
    if result_count is None:
        # Unknown/unextractable response shape. Skipping is the only safe
        # answer — treating it as zero results would fire a "no match"
        # rescue under a Grep that actually succeeded.
        return HookResult()
    output_text = _extract_output_text(tool_output)

    # A genuine flood gets a compact per-file digest regardless of what the
    # pattern looks like — it summarizes the actual results, not the concept.
    if result_count >= _DIGEST_THRESHOLD:
        digest = _grep_flood_digest(repo_path, output_text)
        if digest:
            return _digest_result(
                repo_path,
                tool_input,
                tool_output,
                output_text,
                digest,
                session_id,
                client=client,
            )
        # Unparseable output (e.g. Glob path lists, or a single-file context
        # grep, see search_digest): fall through to triage.

    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return HookResult()

    # Path-style lookups don't benefit from semantic enrichment — the agent
    # is reading literal locations, not exploring a concept.
    if _looks_like_path_lookup(pattern):
        return HookResult()

    # Decision tree. The skip case is the most common — that's by design.
    if result_count == 0:
        # Rescue relevance guards. A regex pattern loses its structure in the
        # symbol-lookup sanitizer (`distill|savings` → `distillsavings`), so
        # any "closest symbol" answer would be luck, not signal. And a grep
        # scoped to one non-code file is a config-key check, not a symbol
        # hunt — the wiki has nothing useful to add to either.
        if _looks_like_regex(pattern) or _targets_single_non_code_file(tool_input):
            return HookResult()
        mode = "rescue"
    elif result_count >= _TRIAGE_THRESHOLD:
        mode = "triage"
    elif _looks_like_regex(pattern) or _targets_single_non_code_file(tool_input):
        # Same two guards as the zero-result rescue, for the same reasons.
        return HookResult()
    elif len(_pattern_terms(pattern)) < 2:
        # A third guard the zero-result rescue does not need. Replayed over
        # this machine's real greps, every false positive the widened rescue
        # produced came from a single-token pattern (`coverage`, `score`,
        # `provider`) where "a symbol by that name is defined elsewhere" is
        # true of half the repo and interesting nowhere. Under zero results
        # even a generic name is the only lead going; under real results it
        # competes with evidence. Free to check, and it runs before the
        # query, so it also keeps the cost off the common path.
        return HookResult()
    else:
        mode = "rescue_wide"

    # Both surviving modes are answers *about the agent's own results*, so
    # neither may run without them. Triage ranks the matched files and rescue
    # fires only on a file that is absent from them; a mode that cannot see
    # the result set has no honest question to answer and stays silent.
    matched = None
    if mode != "rescue":
        matched = _matched_files(repo_path, tool_output, output_text)
        if not matched:
            return HookResult()

    enrichment = _fast_search_enrich(repo_path, pattern, mode, result_count, matched)
    if enrichment is _ORM:
        import asyncio

        enrichment = asyncio.run(
            _search_enrich(repo_path, pattern, mode, result_count, matched)
        )
    if enrichment:
        _log_search_firing(repo_path, session_id, mode, enrichment)
    return HookResult(context=enrichment or None)


def _digest_result(
    repo_path: Path,
    tool_input: dict,
    tool_output: object,
    output_text: str,
    digest: str,
    session_id: str,
    *,
    client: str | None,
) -> HookResult:
    """Serve the digest in place of the flood, or append it as before.

    Two ledger categories on purpose. A *served* digest and an *appended* one
    are different products. One costs the agent tokens to gain a ranking, the
    other saves them, and pooling both under ``digest`` would average a cost
    and a saving into a number that describes neither. ``digest_served`` is
    scored as no-action-expected for the same structural reason
    ``skeleton_served`` is: its text leaves as ``updatedToolOutput`` and never
    appears in the transcript the classifier reads.
    """
    replacement, forgone = _digest_replacement(
        repo_path, tool_input, tool_output, output_text, digest, client=client
    )
    if replacement is not None:
        _log_search_firing(repo_path, session_id, "digest_served", replacement.text)
        from .search_digest import record_saving

        return HookResult(
            replacement=replacement.payload,
            on_emitted=lambda: record_saving(repo_path, replacement),
        )

    _log_search_firing(repo_path, session_id, "digest", digest)
    if forgone is not None:
        from .search_digest import record_forgone

        return HookResult(
            context=digest,
            on_emitted=lambda: record_forgone(repo_path, forgone),
        )
    return HookResult(context=digest)


def _digest_replacement(
    repo_path: Path,
    tool_input: dict,
    tool_output: object,
    output_text: str,
    digest: str,
    *,
    client: str | None,
):
    """``(replacement, forgone)`` for this flood; at most one set. Never raises.

    The two legs share every gate but the flag, on purpose: a counterfactual
    computed under looser conditions than the thing it stands in for would be
    measuring a different feature. So a repo with the surface off still learns
    what it would have saved, and one whose client cannot honour a replacement
    is told nothing, because for that client there was nothing to forgo.
    """
    try:
        from .read_skeleton import supports_updated_output
        from .search_digest import (
            as_grep_output,
            digest_replacement,
            enabled,
            replaces_tool_output,
        )

        if not supports_updated_output() or not replaces_tool_output(client):
            return None, None
        pattern = tool_input.get("pattern") if isinstance(tool_input, dict) else None
        candidate = digest_replacement(
            pattern if isinstance(pattern, str) else "", output_text, digest
        )
        if candidate is None:
            return None, None
        if not enabled(repo_path):
            return None, candidate
        candidate.payload = as_grep_output(tool_output, candidate.text)
        if candidate.payload is None:
            # Not a shape we can legally replace (files_with_matches, Glob).
            # The digest still appends, and nothing was forgone: with the flag
            # on we would have reached exactly here.
            return None, None
        return candidate, None
    except Exception:
        return None, None


def _log_search_firing(
    repo_path: Path, session_id: str, category: str, text: str) -> None:
    """Record one search enrichment in the shared ledger; measurement only.

    All hook surfaces share the sessions.db efficacy ledger so the miner can
    classify used vs ignored firings in one pass. Keyed on a hash of the
    emitted text (:func:`_shared._ledger_key`) — the same enrichment repeated
    in one session logs once, and the transcript classifier can recompute the
    id from what the agent saw. Never changes what the agent sees; any failure
    is silent.
    """
    if not session_id:
        return
    from ._shared import _ledger_key
    from .ledger import _claim_ledger

    _claim_ledger(
        repo_path,
        session_id,
        _ledger_key("search", category, text),
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

    paths = list(groups.keys())
    file_order = _fast_pagerank_file_order(repo_path, paths)
    if file_order is _ORM:
        file_order = None
        try:
            import asyncio

            file_order = asyncio.run(_pagerank_file_order(repo_path, paths))
        except Exception:
            file_order = None
    ranked_by_graph = file_order is not None

    if file_order is None:
        file_order = sorted(groups, key=lambda p: -len(groups[p]))

    digest = render_search_digest(groups, file_order=file_order, max_files=_DIGEST_TOP_FILES)
    order_note = "graph centrality" if ranked_by_graph else "match count"
    return f"[repowise] Search flood — compact digest (files ordered by {order_note}):\n{digest}"


def _as_node_ids(repo_path: Path, paths: list[str]) -> dict[str, str]:
    """Map each grep-spelled path to its graph-node id, keeping the original.

    Grep output paths may be absolute or OS-native; graph node ids and
    ``WikiSymbol.file_path`` are repo-relative POSIX. Every index lookup in
    this module goes through here so the two spellings only have to be
    reconciled once.
    """
    normalized: dict[str, str] = {}
    repo_posix = repo_path.as_posix().rstrip("/") + "/"
    for p in paths:
        norm = p.replace("\\", "/").removeprefix("./")
        if norm.startswith(repo_posix):
            norm = norm[len(repo_posix) :]
        normalized[norm] = p
    return normalized


def _matched_files(repo_path: Path, tool_output: object, output_text: str) -> dict[str, int] | None:
    """Files the search actually matched, as node id → match count.

    ``None`` means "unknowable", which is different from "none matched": a
    single-file context grep (``-C``/``-A``/``-B``) is rendered with no path
    prefix and a ``files_with_matches`` payload carries no line data, so the
    two shapes have to be read differently and a third shape has to be
    refused outright rather than guessed at.

    This is the whole point of items 8 and 10: everything downstream ranks or
    gates against the agent's *real* results instead of against the index's
    opinion of what the pattern means.
    """
    from repowise.core.distill.filters.search_results import group_search_matches

    groups = group_search_matches(output_text)
    if groups:
        ids = _as_node_ids(repo_path, list(groups))
        return {node_id: len(groups[original]) for node_id, original in ids.items()}

    # ``filenames`` rides along on content-mode payloads too, so it is the
    # fallback rather than the first look: parsing the matches keeps the
    # per-file counts, and this branch is what serves the modes that have no
    # match text at all (``files_with_matches``, Glob) plus the context greps
    # the parser declines. No counts here, so one apiece leaves the grep leg
    # in ripgrep's own order, which is the only evidence that shape carries.
    filenames = tool_output.get("filenames") if isinstance(tool_output, dict) else None
    if not isinstance(filenames, list):
        return None
    names = [f for f in filenames if isinstance(f, str)]
    return dict.fromkeys(_as_node_ids(repo_path, names), 1) or None


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

    normalized = _as_node_ids(repo_path, paths)

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

    return _order_by_pagerank(paths, normalized, dict(rows))


def _order_by_pagerank(
    paths: list[str], normalized: dict[str, str], by_node: dict[str, float]
) -> list[str] | None:
    """Grep-spelled *paths*, PageRank first, unranked tail in grep order.

    Shared by both lookup paths so the ordering cannot drift between them.
    """
    if not by_node:
        return None
    rank = {
        normalized[node_id]: pr or 0.0
        for node_id, pr in by_node.items()
        if node_id in normalized
    }
    ranked = sorted(rank, key=lambda p: -rank[p])
    rest = [p for p in paths if p not in rank]
    return ranked + rest


# ---------------------------------------------------------------------------
# Fast path: the same three lookups without the persistence import
# ---------------------------------------------------------------------------

#: "The fast path could not answer this; run the ORM query." Distinct from
#: ``None``, which is a real answer meaning "stay silent", and the reason
#: these helpers cannot just return ``None`` on failure: that would turn a
#: missing table into a silently dropped surface.
_ORM = object()


def _wiki_db_exists(repo_path: Path) -> bool:
    """Whether this repo has a local index at all.

    Both ORM entry points bail out before their sqlalchemy imports on this
    check, so the fast path has to answer "no index" the same way, with
    silence, not with a fallback that pays the import to learn the same thing.
    """
    return (repo_path / ".repowise" / "wiki.db").exists()


def _fast_pagerank_file_order(repo_path: Path, paths: list[str]) -> list[str] | None | object:
    """``_pagerank_file_order`` over stdlib sqlite3. ``_ORM`` to fall back."""
    if not _wiki_db_exists(repo_path):
        return None
    conn = fast_lookup.connect(repo_path)
    if conn is None:
        return _ORM
    try:
        repository_id = fast_lookup.repo_id(conn, repo_path)
        if repository_id is None:
            return None
        normalized = _as_node_ids(repo_path, paths)
        by_node = fast_lookup.pagerank(conn, repository_id, list(normalized))
        return _order_by_pagerank(paths, normalized, by_node)
    except sqlite3.Error:
        return _ORM
    finally:
        conn.close()


def _fast_search_enrich(
    repo_path: Path,
    pattern: str,
    mode: str,
    result_count: int,
    matched: dict[str, int] | None,
) -> str | None | object:
    """Triage and the widened rescue without the ORM. ``_ORM`` to fall back.

    The zero-result rescue is not served here on purpose: 45% of its queries
    fall through to ``FullTextSearch``, so it stays on the shared retrieval
    code and pays the import it actually uses.
    """
    if mode not in ("triage", "rescue_wide") or not matched:
        return _ORM
    clean = _clean_pattern(pattern)
    if not clean:
        return None
    if not _wiki_db_exists(repo_path):
        return None
    conn = fast_lookup.connect(repo_path)
    if conn is None:
        return _ORM
    try:
        repository_id = fast_lookup.repo_id(conn, repo_path)
        if repository_id is None:
            return None
        if mode == "triage":
            if len(matched) < 2:
                return None
            paths = _triage_candidates(matched)
            symbol_names: dict[str, list[str]] = {}
            for file_path, name in fast_lookup.symbols_matching(
                conn, repository_id, paths, clean
            ):
                if file_path and name:
                    symbol_names.setdefault(file_path, []).append(name)
            pagerank = fast_lookup.pagerank(conn, repository_id, paths)
            return _triage_text(pattern, result_count, matched, symbol_names, pagerank)
        rows = fast_lookup.symbols_named(
            conn, repository_id, sorted(_name_variants(clean)), _RESCUE_EXACT_FETCH
        )
        return _rescue_wide_text(pattern, clean, matched, rows)
    except sqlite3.Error:
        return _ORM
    finally:
        conn.close()


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
    matched: dict[str, int] | None = None,
) -> str | None:
    """Run the rescue or triage query against the wiki and format output."""
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

            clean = _clean_pattern(pattern)

            if mode == "rescue":
                return await _rescue(session, engine, repo_id, pattern, clean)
            if mode == "rescue_wide" and matched:
                return await _rescue(session, engine, repo_id, pattern, clean, matched)
            if mode == "triage" and matched:
                return await _triage(session, repo_id, pattern, clean, result_count, matched)
            return None
    finally:
        await engine.dispose()


async def _rescue(
    session,
    engine,
    repo_id: int,
    pattern: str,
    clean: str,
    matched: dict[str, int] | None = None,
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

    Passing *matched* runs the **widened** variant (plan item 10): the grep
    returned a handful of results rather than none, and the rescue is allowed
    to speak only when the best symbol hit lives in a file that is **not**
    among them. This is a set difference, deliberately not a count threshold.
    Rescue's 44% action rate is the highest in the system and it comes from
    firing only when it has genuinely new information; a count threshold
    ("few results, so say something") would turn it into a general suggester
    and spend that rate. Two extra narrowings hold the same bar:

    * only an **exact** name-variant match qualifies. Under a real result set
      a fuzzy neighbour is a guess competing against evidence the agent
      already has, where under zero results it was the only candidate going.
    * **no FTS fallback.** "The wiki suggests this page" answers a question
      the agent is no longer asking once grep has handed it real files.
    """
    from sqlalchemy import or_, select

    from repowise.core.persistence import (
        FullTextSearch,
        WikiSymbol,
    )

    if not clean:
        return None

    from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like

    # Build a small set of token variants. Cheap; helps catch case-style
    # drift without a heavy similarity index.
    variants = _name_variants(clean)
    lowered = {v.lower() for v in variants}
    if matched is None:
        name_clause = or_(
            *[
                WikiSymbol.name.ilike(f"%{escape_like(v)}%", escape=LIKE_ESCAPE)
                for v in variants
            ]
        )
    else:
        # Exactness enforced in SQL, not by filtering the fetched page. The
        # substring query is unordered under a LIMIT, so an exact match can
        # sit well outside the first two rows, and post-filtering them would
        # have made the widened rescue almost never fire, silently.
        name_clause = WikiSymbol.name.in_(sorted(variants))
    # Ordered because the LIMIT truncates: unordered, which rows survive the cut
    # is whatever order the chosen index walks, so an unrelated index added to
    # wiki_symbols changes the result. (file_path, name) is what the
    # uq_wiki_symbol autoindex gave for free — its key is "<path>::<name>" — so
    # this pins the long-standing behaviour. Mirrored in
    # ``fast_lookup.symbols_named``, which serves the same rescue.
    sym_stmt = (
        select(WikiSymbol.name, WikiSymbol.kind, WikiSymbol.file_path, WikiSymbol.start_line)
        .where(WikiSymbol.repository_id == repo_id, name_clause)
        .order_by(WikiSymbol.file_path, WikiSymbol.name)
        .limit(_RESCUE_TOP_N if matched is None else _RESCUE_EXACT_FETCH)
    )
    rows = (await session.execute(sym_stmt)).all()
    if matched is not None:
        return _rescue_wide_text(pattern, clean, matched, rows)
    if rows:
        rows = _rescue_rank(rows, lowered)
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
    matched: dict[str, int],
) -> str | None:
    """Big-result triage: rank the files the search matched, best first.

    *matched* is the grep's own output, parsed (see :func:`_matched_files`),
    and it is the whole candidate set. The previous version built candidates
    from a ``WikiSymbol`` ILIKE plus a path ILIKE and ranked those by bare
    PageRank, never reading the grep output at all, so a confident,
    well-formatted answer could name a file the agent's search had not
    matched. The index now orders the agent's results; it no longer proposes
    its own.

    Three ranked lists are fused by Reciprocal Rank Fusion (``1/(rank + k)``,
    k=60), the same arithmetic ``_fused_retrieve`` uses in the MCP search
    tool. RRF is pure rank arithmetic, so it ports into a hook unchanged,
    unlike the embedding leg it fuses there, which needs a network call and is
    out of scope for a hook by the phase-3 retrieval ceiling.

      * **grep evidence**: every matched file, by match count. The only leg
        that is not an opinion, and the only one that is always populated.
      * **name coverage**: the fraction of the pattern's subtokens present
        in a file's path or in the names of its matching indexed symbols.
        Formula ported from ``_rerank_by_coverage`` in the MCP answer
        pipeline (see :func:`_coverage_order`). This is what makes the
        ranking query-aware rather than a fixed importance order: it is the
        leg that separates the file that *defines* the thing from the twenty
        that merely mention it.
      * **PageRank**: structural centrality, the previous sole signal, kept
        as a tiebreak-weight leg rather than as the verdict.

    A file present in two legs beats a file present in one, which is exactly
    the property that makes RRF the right fusion here.

    Output is one header line plus at most :data:`_TRIAGE_TOP_N` files.
    """
    from sqlalchemy import select

    from repowise.core.persistence import GraphNode, WikiSymbol
    from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like

    if not clean or len(matched) < 2:
        # One matched file needs no ranking; the agent is already looking at
        # it. Emitting there would be pure cost.
        return None

    paths = _triage_candidates(matched)

    sym_stmt = select(WikiSymbol.file_path, WikiSymbol.name).where(
        WikiSymbol.repository_id == repo_id,
        WikiSymbol.file_path.in_(paths),
        WikiSymbol.name.ilike(f"%{escape_like(clean)}%", escape=LIKE_ESCAPE),
    )
    symbol_names: dict[str, list[str]] = {}
    for file_path, name in (await session.execute(sym_stmt)).all():
        if file_path and name:
            symbol_names.setdefault(file_path, []).append(name)

    pr_stmt = select(GraphNode.node_id, GraphNode.pagerank).where(
        GraphNode.repository_id == repo_id,
        GraphNode.node_type == "file",
        GraphNode.node_id.in_(paths),
    )
    pagerank = {
        node_id: pr or 0.0
        for node_id, pr in (await session.execute(pr_stmt)).all()
        if node_id
    }
    return _triage_text(pattern, result_count, matched, symbol_names, pagerank)


# ---------------------------------------------------------------------------
# Pure ranking and formatting, shared by the ORM and sqlite3 paths
# ---------------------------------------------------------------------------
#
# Everything below takes rows and returns text. Both lookup paths run the same
# two queries and then land here, so a ported query can change what it costs
# but not what it says.


def _clean_pattern(pattern: str) -> str:
    """The pattern reduced to a symbol-ish token for index lookups."""
    import re

    return re.sub(r"[^\w./_-]", "", pattern).strip("./")


def _triage_candidates(matched: dict[str, int]) -> list[str]:
    """Matched files the ranker considers, widest floods first-cut by count.

    See :data:`_TRIAGE_MAX_CANDIDATES`; also the parameter list of both index
    queries, so the cut has to happen before either of them runs.
    """
    return sorted(matched, key=lambda p: (-matched[p], p))[:_TRIAGE_MAX_CANDIDATES]


def _triage_text(
    pattern: str,
    result_count: int,
    matched: dict[str, int],
    symbol_names: dict[str, list[str]],
    pagerank: dict[str, float],
) -> str | None:
    """Fuse the three legs and render triage's block, or None to stay silent."""
    if not symbol_names and not pagerank:
        # The index knows nothing about any matched file. Re-listing the
        # grep's own top files back at it is not worth the tokens.
        return None

    paths = _triage_candidates(matched)
    legs = [
        paths,
        _coverage_order(pattern, paths, symbol_names),
        sorted(
            (p for p in paths if p in pagerank),
            key=lambda p: (-pagerank[p], p),
        ),
    ]
    fused = _rrf(legs)
    ranked = sorted(paths, key=lambda p: (-fused.get(p, 0.0), p))[:_TRIAGE_TOP_N]

    header = (
        f"[repowise] {result_count}+ matches for `{pattern}` across {len(matched)} "
        f"files. Most likely relevant, ranked over the files your search matched:"
    )
    lines = [header] + [f"  {p}  ({matched[p]} matches)" for p in ranked]
    return "\n".join(lines)


def _rescue_rank(rows: list, lowered: set[str]) -> list:
    """Best rescue candidates first, capped at :data:`_RESCUE_TOP_N`.

    Prefer exact-token-equal matches; then the shortest name, as the most
    specific; ties broken by file path lex order so the answer is stable
    across query plans.
    """

    def _key(row):
        name = (row[0] or "").lower()
        return (name not in lowered, len(name), row[2] or "")

    return sorted(rows, key=_key)[:_RESCUE_TOP_N]


def _rescue_wide_text(
    pattern: str, clean: str, matched: dict[str, int], rows: list
) -> str | None:
    """The widened rescue's line, or None when it has nothing new to say.

    The gate is a set difference, not a count: the top exact-name match has to
    live in a file the grep did *not* return. See :func:`_rescue`.
    """
    if not rows:
        return None
    rows = _rescue_rank(rows, {v.lower() for v in _name_variants(clean)})
    first = rows[0]
    if (first[2] or "") in matched:
        # The agent already has this file. Nothing new to say.
        return None
    line = f":{first[3]}" if first[3] else ""
    return (
        f"[repowise] `{pattern}` matched {len(matched)} "
        f"file{'s' if len(matched) != 1 else ''}, but not {first[2]}{line}, "
        f"where indexed {first[1]} `{first[0]}` is defined."
    )


def _rrf(legs: list[list[str]]) -> dict[str, float]:
    """Reciprocal Rank Fusion over ranked lists: ``sum(1/(rank + k))``.

    Ported verbatim in behaviour from ``_fused_retrieve``; no score scaling,
    because nothing downstream compares these numbers against a tuned
    threshold; they only order three files.
    """
    scores: dict[str, float] = {}
    for leg in legs:
        for rank, key in enumerate(leg):
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank + _RRF_K)
    return scores


def _coverage_order(
    pattern: str, paths: list[str], symbol_names: dict[str, list[str]]
) -> list[str]:
    """Matched files ordered by how much of *pattern* their names cover.

    Term coverage, ported from ``_rerank_by_coverage`` in
    ``tool_answer/retrieval.py``: the fraction of the query's distinct terms
    present in the candidate's text. What did not port is that function's
    ``raw * (FLOOR + (1-FLOOR)*coverage)`` blend. The floor exists to keep a
    BM25 score dominant while coverage modulates it, and there is no BM25
    score on this path. Here coverage produces a *rank*, and RRF does the
    blending the floor was standing in for.

    Nor does the stopword list port: it lives in the MCP answer pipeline, and
    importing that package into a hook would pull the server stack in at hook
    startup for a set of English words a grep pattern does not contain.

    Files covering nothing are dropped rather than ranked last: an empty leg
    is an honest "no signal", where a full one would inject noise into the
    fusion.
    """
    terms = _pattern_terms(pattern)
    if not terms:
        return []
    scored: list[tuple[float, str]] = []
    for p in paths:
        haystack = " ".join([p, *symbol_names.get(p, [])]).lower()
        coverage = sum(1 for t in terms if t in haystack) / len(terms)
        if coverage:
            scored.append((coverage, p))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [p for _, p in scored]


def _pattern_terms(pattern: str) -> list[str]:
    """Distinct content subtokens of a search pattern, lowercased.

    Splits on non-word characters *and* on snake/camel boundaries, so
    ``record_saving`` covers ``record`` and ``saving`` independently and a
    file named ``savings.py`` scores against the half it actually carries.
    Terms shorter than three characters are dropped as noise, matching
    ``_question_terms``.
    """
    import re

    parts = re.split(r"[^\w]+", pattern)
    terms: list[str] = []
    for part in parts:
        for token in re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", part):
            token = token.lower()
            if len(token) >= 3 and token not in terms:
                terms.append(token)
    return terms


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
