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

  PostToolUse → edit tools
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
    result = _handle_post_tool_use(tool_name, tool_input, tool_output, cwd, client=client)
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
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None

    # Path-style lookups don't benefit from semantic enrichment — the agent
    # is reading literal locations, not exploring a concept.
    if _looks_like_path_lookup(pattern):
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    output_text = _extract_output_text(tool_output)
    result_count = _count_search_results(output_text)

    # Decision tree. The skip case is the most common — that's by design.
    if result_count == 0:
        mode = "rescue"
    elif result_count >= _TRIAGE_THRESHOLD:
        mode = "triage"
    else:
        return None

    import asyncio

    return asyncio.run(_search_enrich(repo_path, pattern, mode, result_count))


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


def _extract_output_text(tool_output: object) -> str:
    """Pull the textual portion of a Claude Code tool_output, defensively.

    Claude Code's hook payload shape varies a little by tool: Bash
    surfaces ``stdout``/``stderr``, Grep/Glob surface ``output`` or
    ``tool_response``. We only need a string we can count newlines in,
    so we accept any of the common shapes.
    """
    if isinstance(tool_output, str):
        return tool_output
    if not isinstance(tool_output, dict):
        return ""
    for key in ("output", "result", "content", "stdout", "text"):
        val = tool_output.get(key)
        if isinstance(val, str):
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
    return ""


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
) -> str | None:
    """Dispatch PostToolUse events from Claude or Codex."""
    # The edit-tool freshness notice is a Codex-only lifecycle hook. Gate it on the
    # Codex client so a future widening of the Claude installer's PostToolUse matcher
    # can't start emitting Codex-flavored banners to existing Claude Code users.
    if client == "codex" and tool_name in _EDIT_TOOL_NAMES:
        return _handle_post_edit_use(cwd)
    if tool_name == "Bash":
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
        elapsed_str = f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
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


def _read_in_flight_marker(repo_path: "object") -> dict | None:
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

    lock_path = repo_path / ".repowise" / ".update.lock"
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            started = payload.get("started_at")
            if isinstance(started, (int, float)) and now - started <= 30 * 60:
                return {
                    "source": "lock",
                    "target_commit": payload.get("target_commit"),
                    "elapsed_seconds": now - started,
                }
        except (json.JSONDecodeError, OSError):
            pass

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
