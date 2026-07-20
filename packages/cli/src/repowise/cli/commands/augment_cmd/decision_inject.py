"""Relevance-ranked decision injection for the agent hooks.

Two delivery moments, both pure indexed-SQLite lookups (no LLM, no network,
target well under 100ms):

  * SessionStart — score the repo's active decisions against the session's
    likely working set (dirty/staged files, branch-vs-main changed files, the
    previous session's edited files, branch-name tokens) expanded one hop via
    import edges and co-change partners, and inject the top few under a hard
    token cap. Relevance or silence: nothing clears the floor, nothing is
    injected. Never top-confidence-globally.
  * Edit-time (PostToolUse Edit/Write) — when the edited file has a governing
    decision (via decision_node_links), say so once per session per decision,
    under a strict per-session cap.

Repo-wide session rules (user corrections with no named files, so no node
links) can only reach the agent here: they carry a flat base relevance at
SessionStart so a rule like "never use em dashes" is deliverable at all, but
they still compete under the same floor and cap as everything else.

Every injected decision id is recorded in the sessions.db sidecar so the
update-time miner can check whether the guidance was followed or contradicted
(usage feedback v1). Same operational rules as the rest of augment: any
failure degrades to silence, never an error in the agent transcript.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
from pathlib import Path

# --- SessionStart tunables -------------------------------------------------

#: Hard budget for the whole injected block, in estimated tokens (chars/4).
_TOKEN_CAP = 400
#: Minimum final score a decision needs to be injected at all.
_RELEVANCE_FLOOR = 0.25
#: Never inject more than this many decisions regardless of the token cap.
_MAX_ITEMS = 6
#: Working-set caps keep the SQL IN-lists and the hop expansion bounded.
_MAX_SEEDS = 30
_MAX_HOP = 100

#: Hop weights: a decision governing a file the session is actually touching
#: outranks one governing a neighbor of that file.
_W_SEED_FILE = 0.6
_W_HOP_FILE = 0.3
#: Module links are broader claims than file links, so they count for half.
_MODULE_FACTOR = 0.5
#: Score contribution when a branch-name token appears in the decision text.
_W_BRANCH_TOKEN = 0.4
#: Base relevance for repo-wide session rules (active, session-sourced, no
#: node links). They apply everywhere, so SessionStart is their only path.
_W_GLOBAL_RULE = 0.5
#: At most this many unlinked global rules per block. Working-set-relevant
#: decisions must never be crowded out by always-eligible rules, and a
#: mis-promoted one-off (dogfood: "merge the backend PRs" made it to active)
#: costs at most one slot until it is dismissed.
_MAX_GLOBAL_RULES = 2

#: Branch-name tokens that identify workflow, not topic.
_GENERIC_BRANCH_TOKENS = frozenset(
    {
        "feat",
        "feature",
        "fix",
        "bugfix",
        "hotfix",
        "chore",
        "refactor",
        "docs",
        "test",
        "tests",
        "wip",
        "dev",
        "main",
        "master",
        "head",
        "release",
        "branch",
    }
)

# --- Edit-time tunables ------------------------------------------------------

#: Strict per-session cap on edit-time decision notices.
_MAX_EDIT_NOTICES = 3

_CLIP_DECISION = 220
_CLIP_RATIONALE = 160


# ---------------------------------------------------------------------------
# Shared SQLite plumbing (read-only wiki.db, stdlib sqlite3 — the hook path
# must not pay the sqlalchemy import; same pattern as the skeleton nudge)
# ---------------------------------------------------------------------------


def _open_wiki_ro(repo_path: Path) -> sqlite3.Connection | None:
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None


def _clip(text: str, cap: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _norm_path(node_id: str) -> str:
    """Link node ids are stored OS-native (backslashes on Windows); the seed
    set is POSIX. Compare everything in POSIX."""
    return (node_id or "").replace("\\", "/")


def _module_deep_enough(node_id: str) -> bool:
    """A top-level module link ("packages") governs the whole tree — that is
    an extraction artifact, not governance, and injecting it on every edit is
    pure noise (dogfood: a truncated legacy record linked to `packages` fired
    on unrelated files). Require at least two path segments."""
    return "/" in _norm_path(node_id).strip("/")


def _echoes_title(title: str, text: str) -> bool:
    """True when *text* is just the title again (legacy records often store
    the same truncated string in title, decision, and rationale)."""
    a = " ".join((title or "").lower().split())
    b = " ".join((text or "").lower().split())
    if not a or not b:
        return False
    probe = min(len(a), len(b), 60)
    return a[:probe] == b[:probe]


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------------------
# SessionStart: seed collection
# ---------------------------------------------------------------------------


def _git_lines(repo_path: Path, *args: str) -> list[str] | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _dirty_files_and_branch(repo_path: Path) -> tuple[list[str], str]:
    """Dirty + staged paths and the branch name, from one git call.

    ``git status --porcelain --branch`` carries both (the ``## branch...``
    header line), halving the subprocess cost of seed collection — git
    startup dominates this hook's latency on Windows. Renames yield the new
    path; untracked directories are skipped.
    """
    lines = _git_lines(repo_path, "status", "--porcelain", "--branch") or []
    files: list[str] = []
    branch = ""
    for ln in lines:
        if ln.startswith("## "):
            head = ln[3:].split("...", 1)[0].strip()
            branch = "" if "(" in head else head  # "## HEAD (no branch)" etc.
            continue
        path = ln[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path and not path.endswith("/"):
            files.append(path)
    return files, branch


def _branch_changed_files(repo_path: Path, branch: str) -> list[str]:
    """Files changed on this branch vs the default branch (merge-base diff)."""
    if branch in ("", "HEAD", "main", "master"):
        return []
    for base in ("main", "master"):
        lines = _git_lines(repo_path, "diff", "--name-only", f"{base}...HEAD")
        if lines is not None:
            return [ln.strip() for ln in lines if ln.strip()]
    return []


def _previous_session_edits(repo_path: Path) -> list[str]:
    """Edited files recorded by the previous session's read/edit state.

    At SessionStart the state file still holds the last session's entries
    (the new session_id resets it only on the first PostToolUse event).
    """
    state_path = repo_path / ".repowise" / ".augment-session.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    edits = state.get("edits") if isinstance(state, dict) else None
    return [f for f in edits if isinstance(f, str)] if isinstance(edits, dict) else []


_BRANCH_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _branch_tokens(branch: str) -> list[str]:
    """Topic-bearing tokens from a branch name (workflow prefixes dropped)."""
    return [
        t
        for t in _BRANCH_SPLIT_RE.split(branch.lower())
        if len(t) >= 3 and t not in _GENERIC_BRANCH_TOKENS
    ]


def _collect_seeds(repo_path: Path) -> tuple[list[str], str]:
    """The session's likely working set (repo-relative POSIX) + branch name."""
    dirty, branch = _dirty_files_and_branch(repo_path)
    seen: dict[str, None] = {}
    for f in dirty + _branch_changed_files(repo_path, branch) + _previous_session_edits(repo_path):
        norm = f.replace("\\", "/").lstrip("./")
        if norm:
            seen.setdefault(norm, None)
    return list(seen)[:_MAX_SEEDS], branch


# ---------------------------------------------------------------------------
# SessionStart: one-hop expansion (import edges + co-change partners)
# ---------------------------------------------------------------------------


def _looks_like_file_node(node_id: str) -> bool:
    return "::" not in node_id and not node_id.startswith("external:")


def _expand_one_hop(conn: sqlite3.Connection, seeds: list[str]) -> set[str]:
    """Files one graph/co-change hop away from the seed set."""
    if not seeds:
        return set()
    hop: set[str] = set()
    marks = ",".join("?" * len(seeds))
    with contextlib.suppress(sqlite3.Error):
        rows = conn.execute(
            f"SELECT source_node_id, target_node_id FROM graph_edges "
            f"WHERE source_node_id IN ({marks}) OR target_node_id IN ({marks})",
            (*seeds, *seeds),
        ).fetchall()
        seed_set = set(seeds)
        for src, dst in rows:
            for node in (src, dst):
                if isinstance(node, str) and node not in seed_set and _looks_like_file_node(node):
                    hop.add(node)
                    if len(hop) >= _MAX_HOP:
                        return hop
    with contextlib.suppress(sqlite3.Error):
        rows = conn.execute(
            f"SELECT co_change_partners_json FROM git_metadata WHERE file_path IN ({marks})",
            tuple(seeds),
        ).fetchall()
        for (raw,) in rows:
            try:
                partners = json.loads(raw or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(partners, list):
                continue
            for p in partners:
                path = p.get("file_path") if isinstance(p, dict) else None
                if isinstance(path, str) and path and path not in seeds:
                    hop.add(path)
                    if len(hop) >= _MAX_HOP:
                        return hop
    return hop


# ---------------------------------------------------------------------------
# Decision loading + scoring
# ---------------------------------------------------------------------------


def _load_active_decisions(conn: sqlite3.Connection) -> list[dict]:
    """Active decisions with their node links, as plain dicts."""
    try:
        rows = conn.execute(
            "SELECT id, title, decision, rationale, confidence, staleness_score, source "
            "FROM decision_records WHERE status = 'active'"
        ).fetchall()
    except sqlite3.Error:
        return []
    decisions = [
        {
            "id": r[0],
            "title": r[1] or "",
            "decision": r[2] or "",
            "rationale": r[3] or "",
            "confidence": r[4] if isinstance(r[4], (int, float)) else 0.5,
            "staleness": r[5] if isinstance(r[5], (int, float)) else 0.0,
            "source": r[6] or "",
            "links": [],
        }
        for r in rows
    ]
    if not decisions:
        return []
    by_id = {d["id"]: d for d in decisions}
    marks = ",".join("?" * len(by_id))
    with contextlib.suppress(sqlite3.Error):
        for decision_id, node_id, link_type in conn.execute(
            f"SELECT decision_id, node_id, link_type FROM decision_node_links "
            f"WHERE decision_id IN ({marks})",
            tuple(by_id),
        ):
            by_id[decision_id]["links"].append((_norm_path(node_id), link_type))
    return decisions


def _freshness(staleness: float) -> float:
    """Staleness discounts relevance but never zeroes an otherwise-relevant hit."""
    return 1.0 - 0.6 * max(0.0, min(1.0, staleness))


def _overlap_score(links: list[tuple[str, str]], seeds: set[str], hop: set[str]) -> float:
    score = 0.0
    for node_id, link_type in links:
        if link_type == "module":
            if not _module_deep_enough(node_id):
                continue  # a top-level module link "governs" everything: noise
            prefix = node_id.rstrip("/") + "/"
            if any(f.startswith(prefix) for f in seeds):
                score += _W_SEED_FILE * _MODULE_FACTOR
            elif any(f.startswith(prefix) for f in hop):
                score += _W_HOP_FILE * _MODULE_FACTOR
        elif node_id in seeds:
            score += _W_SEED_FILE
        elif node_id in hop:
            score += _W_HOP_FILE
    return min(1.0, score)


def _score_decision(
    decision: dict, seeds: set[str], hop: set[str], branch_tokens: list[str]
) -> float:
    relevance = _overlap_score(decision["links"], seeds, hop)
    if branch_tokens:
        text = f"{decision['title']} {decision['decision']}".lower()
        if any(t in text for t in branch_tokens):
            relevance = min(1.0, relevance + _W_BRANCH_TOKEN)
    if not decision["links"] and decision["source"] == "session":
        # A repo-wide rule mined from user corrections: applies everywhere,
        # so it gets a base relevance instead of file overlap.
        relevance = max(relevance, _W_GLOBAL_RULE)
    return relevance * decision["confidence"] * _freshness(decision["staleness"])


# ---------------------------------------------------------------------------
# SessionStart entry point
# ---------------------------------------------------------------------------


def _format_decision_line(decision: dict) -> str:
    title = _clip(decision["title"], 100)
    body = _clip(decision["decision"], _CLIP_DECISION)
    rationale = _clip(decision["rationale"], _CLIP_RATIONALE)
    # Legacy records echo the title into decision/rationale; saying a thing
    # once is guidance, saying it three times is noise.
    if _echoes_title(decision["title"], body):
        body = ""
    if _echoes_title(decision["title"], rationale) or _echoes_title(body, rationale):
        rationale = ""
    line = f"- {title}: {body}" if body else f"- {title}"
    if rationale:
        line += f" (because {rationale})"
    return line


def _session_decision_block(repo_path: Path, session_id: str) -> str | None:
    """The relevance-ranked SessionStart decision block, or None (silence)."""
    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return None
    try:
        decisions = _load_active_decisions(conn)
        if not decisions:
            return None
        seeds, branch = _collect_seeds(repo_path)
        hop = _expand_one_hop(conn, seeds)
        tokens = _branch_tokens(branch)

        scored = [(d, _score_decision(d, set(seeds), hop, tokens)) for d in decisions]
        scored = [(d, s) for d, s in scored if s >= _RELEVANCE_FLOOR]
        if not scored:
            return None
        scored.sort(key=lambda pair: pair[1], reverse=True)
    finally:
        conn.close()

    header = (
        "[repowise] Standing decisions relevant to this session's working set "
        "(accumulated from prior sessions; follow them unless the user says otherwise):"
    )
    lines = [header]
    budget = _TOKEN_CAP - _estimate_tokens(header)
    shown: list[dict] = []
    globals_shown = 0
    for decision, _score in scored:
        if len(shown) >= _MAX_ITEMS:
            break
        is_global = not decision["links"] and decision["source"] == "session"
        if is_global and globals_shown >= _MAX_GLOBAL_RULES:
            continue
        line = _format_decision_line(decision)
        cost = _estimate_tokens(line)
        if cost > budget:
            break
        lines.append(line)
        budget -= cost
        shown.append(decision)
        if is_global:
            globals_shown += 1
    if not shown:
        return None
    _record_injections(repo_path, session_id, [d["id"] for d in shown], node_id="")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Edit-time governing-decision notice
# ---------------------------------------------------------------------------


def _governing_decisions(conn: sqlite3.Connection, rel: str) -> list[dict]:
    """Active decisions governing *rel* via file links or module-prefix links.

    Link node ids are matched in POSIX regardless of how they were stored
    (Windows extraction persists backslashes). Top-level module links are
    ignored (see :func:`_module_deep_enough`).
    """
    out: list[dict] = []
    seen: set[str] = set()
    native = rel.replace("/", "\\")
    with contextlib.suppress(sqlite3.Error):
        for row in conn.execute(
            "SELECT d.id, d.title, d.decision, d.rationale "
            "FROM decision_node_links l JOIN decision_records d ON d.id = l.decision_id "
            "WHERE l.node_id IN (?, ?) AND l.link_type = 'file' AND d.status = 'active'",
            (rel, native),
        ):
            if row[0] not in seen:
                seen.add(row[0])
                out.append({"id": row[0], "title": row[1], "decision": row[2], "rationale": row[3]})
        # Module links are few; prefix-match them in Python.
        for row in conn.execute(
            "SELECT d.id, d.title, d.decision, d.rationale, l.node_id "
            "FROM decision_node_links l JOIN decision_records d ON d.id = l.decision_id "
            "WHERE l.link_type = 'module' AND d.status = 'active'"
        ):
            if (
                row[0] not in seen
                and _module_deep_enough(row[4])
                and rel.startswith(_norm_path(row[4]).rstrip("/") + "/")
            ):
                seen.add(row[0])
                out.append({"id": row[0], "title": row[1], "decision": row[2], "rationale": row[3]})
    return out


def _session_evidence_count(conn: sqlite3.Connection, decision_id: str) -> int:
    """Distinct sessions that attested to this decision (evidence rows)."""
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT evidence_commit) FROM decision_evidence "
            "WHERE decision_id = ? AND source = 'session' AND evidence_commit IS NOT NULL",
            (decision_id,),
        ).fetchone()
    except sqlite3.Error:
        return 0
    return row[0] if row and isinstance(row[0], int) else 0


def _edit_decision_notice(repo_path: Path, rel: str, session_id: str, state: dict) -> str | None:
    """One-line governing-decision notice for an edited file, deduplicated.

    Once per session per decision, and at most :data:`_MAX_EDIT_NOTICES`
    notices per session total — an agent editing many governed files gets the
    first few, not a drumbeat. The authoritative dedup is the atomic
    ``INSERT OR IGNORE`` into the injections sidecar: the JSON session state
    is written read-modify-write by concurrently racing hook processes and
    loses updates (dogfood: the same decision re-fired minutes apart), so it
    is kept only as a cheap fast-path. The caller owns loading/saving *state*.
    """
    shown: list = state.setdefault("decisions_shown", [])
    if len(shown) >= _MAX_EDIT_NOTICES:
        return None
    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return None
    try:
        governing = [d for d in _governing_decisions(conn, rel) if d["id"] not in shown]
        if not governing:
            return None
        decision = governing[0]
        sessions_n = _session_evidence_count(conn, decision["id"])
    finally:
        conn.close()

    shown.append(decision["id"])
    if session_id:
        claimed, session_total = _claim_injection(repo_path, session_id, decision["id"], rel)
        if not claimed or session_total > _MAX_EDIT_NOTICES:
            return None

    why = _clip(decision["rationale"] or decision["decision"], _CLIP_RATIONALE)
    if _echoes_title(decision["title"], why):
        why = ""  # legacy rows echo the title into decision/rationale
    line = f"[repowise] {rel} is governed by a standing decision: {_clip(decision['title'], 100)}"
    if why:
        line += f" because {why}"
    if sessions_n >= 2:
        line += f" (confirmed across {sessions_n} sessions)"
    return line + "."


# ---------------------------------------------------------------------------
# Edit-time bug-history notice
# ---------------------------------------------------------------------------

#: Silence past this age, no matter how large the historical count. A file fixed
#: four times two years ago is history; the notice exists to interrupt an edit,
#: and only a recent run of fixes earns that. Mirrors the ``prior_defect``
#: window the count itself is drawn from.
_FIX_NOTICE_MAX_AGE_DAYS = 180
#: Below this many counted fixes the notice is not worth an agent's attention.
_FIX_NOTICE_MIN_COUNT = 3


def _humanize_age(days: int) -> str:
    """Render an age as "2 weeks ago" / "3 months ago", never as a bare count.

    Deliberately duplicated by ``editor_files/fetcher.py``, which renders the
    same recency phrasing into CLAUDE.md. Sharing it would mean this hook
    importing ``repowise.core``, and the cheapest module that could host it
    costs ~660ms to import (``analysis.health.__init__`` builds the whole
    HealthAnalyzer) against this module's sub-100ms budget. Ten lines of copy
    is the cheaper trade; keep the two phrasings in step by hand.
    """
    if days <= 1:
        return "today" if days <= 0 else "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        weeks = round(days / 7)
        return f"{weeks} week{'' if weeks == 1 else 's'} ago"
    months = round(days / 30)
    return f"{months} month{'' if months == 1 else 's'} ago"


def _edit_fix_history_notice(repo_path: Path, rel: str, session_id: str) -> str | None:
    """One-line bug-history heads-up for an edited file, or ``None`` for silence.

    Fires on files with a real recent run of fixes and nothing else. Three gates,
    all of which have to hold: at least :data:`_FIX_NOTICE_MIN_COUNT` counted
    fixes, a last fix inside :data:`_FIX_NOTICE_MAX_AGE_DAYS`, and one claim per
    file per session (the same atomic ``INSERT OR IGNORE`` ledger the decision
    notice uses, so two racing hook processes cannot double-fire).

    The age is mandatory in the copy. A two-week-old fix and a two-year-old fix
    must never read the same, which is also why the age gate exists rather than
    a count gate alone.
    """
    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT prior_defect_count, bug_magnet, last_fix_at, fix_symbol_counts_json "
            "FROM git_metadata WHERE file_path = ? LIMIT 1",
            (rel,),
        ).fetchone()
    except sqlite3.Error:
        # A pre-fix-events index has no such columns: silence, not an error.
        return None
    finally:
        conn.close()
    if row is None:
        return None

    count = row[0] or 0
    if count < _FIX_NOTICE_MIN_COUNT:
        return None
    days = _days_since(row[2])
    if days is None or days > _FIX_NOTICE_MAX_AGE_DAYS:
        return None

    line = (
        f"[repowise] {rel} has been bug-fixed {count}x in the last 6 months, "
        f"last {_humanize_age(days)}"
    )
    if row[1]:
        line += " (bug magnet)"
    symbol = _top_fix_symbol(row[3])
    if symbol:
        # Hedged on purpose: symbol spans are current-tree and the fix ranges
        # are from each fix's own parent, so this is "mostly", not "exactly".
        line += f"; mostly in {symbol}"
    line += "."

    if session_id:
        claimed, shown = _claim_ledger(
            repo_path,
            session_id,
            f"fix_history:{rel}",
            node_id=rel,
            surface="fix_history",
            category="edit_notice",
            chars=len(line),
        )
        if not claimed or shown > _MAX_EDIT_NOTICES:
            return None
    return line


def _days_since(raw: object) -> int | None:
    """Whole days between a stored ``last_fix_at`` and now, or ``None``.

    The column round-trips through sqlite as a naive-UTC string (the ORM writes
    naive UTC), so it is read here without a timezone and compared to a naive
    UTC now. Anything unparseable is no signal.
    """
    from datetime import UTC, datetime

    if isinstance(raw, str):
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return None
    elif isinstance(raw, datetime):
        moment = raw
    else:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return max(0, (datetime.now(UTC).replace(tzinfo=None) - moment).days)


def _top_fix_symbol(raw: object) -> str | None:
    """The most-fixed symbol's bare name, or ``None``.

    The stored map is already in descending-count order, so the first key wins.
    Keys are ``path/to/file.py::Name`` and the line already names the path.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        counts = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(counts, dict) or not counts:
        return None
    return str(next(iter(counts))).rsplit("::", 1)[-1] or None


# ---------------------------------------------------------------------------
# Injection recording (usage feedback v1)
# ---------------------------------------------------------------------------

_INJECTIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS injections ("
    "session_id TEXT NOT NULL, decision_id TEXT NOT NULL, "
    "node_id TEXT NOT NULL DEFAULT '', shown_at REAL NOT NULL, "
    "evaluated INTEGER NOT NULL DEFAULT 0, "
    "surface TEXT NOT NULL DEFAULT '', "
    "category TEXT NOT NULL DEFAULT '', "
    "chars INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (session_id, decision_id))"
)

#: Mirror of core.sessions.staging.INJECTIONS_LEDGER_COLUMNS — the hook path
#: must not import repowise.core, so the migration is duplicated verbatim.
_LEDGER_COLUMNS = (
    ("surface", "TEXT NOT NULL DEFAULT ''"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("chars", "INTEGER NOT NULL DEFAULT 0"),
)


def _open_injections(repo_path: Path) -> sqlite3.Connection | None:
    db_path = repo_path / ".repowise" / "sessions" / "sessions.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(_INJECTIONS_TABLE_SQL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(injections)")}
        for name, decl in _LEDGER_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE injections ADD COLUMN {name} {decl}")
        return conn
    except (sqlite3.Error, OSError):
        return None


def _record_injections(
    repo_path: Path, session_id: str, decision_ids: list[str], *, node_id: str
) -> None:
    """Log shown decisions in the sessions.db sidecar; best-effort, never raises.

    The update-time miner reads these rows to judge whether injected guidance
    was followed or contradicted (usage feedback v1). Written with raw stdlib
    sqlite3 so the hook path never imports repowise.core.
    """
    if not session_id or not decision_ids:
        return
    conn = _open_injections(repo_path)
    if conn is None:
        return
    try:
        now = time.time()
        conn.executemany(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category) "
            "VALUES (?, ?, ?, ?, 'decision', 'session_start')",
            [(session_id, did, node_id, now) for did in decision_ids],
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def _claim_ledger(
    repo_path: Path,
    session_id: str,
    key: str,
    *,
    node_id: str,
    surface: str,
    category: str,
    chars: int,
) -> tuple[bool, int]:
    """Atomically claim one non-decision ledger emission.

    Generic twin of :func:`_claim_injection` for the read/search enrichment
    surfaces: *key* replaces the decision id in the primary key, so INSERT OR
    IGNORE is the once-per-session-per-key gate. Returns ``(claimed,
    surface_injection_count)`` where the count covers only rows that actually
    carried text (``chars > 0``) on *surface* — pure measurement rows must not
    eat into an injection cap. Fail-closed: any error reports unclaimed.
    """
    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, key, node_id, time.time(), surface, category, chars),
        )
        claimed = cur.rowcount > 0
        count = conn.execute(
            "SELECT COUNT(*) FROM injections WHERE session_id = ? AND surface = ? AND chars > 0",
            (session_id, surface),
        ).fetchone()[0]
        conn.commit()
        return claimed, int(count)
    except sqlite3.Error:
        return False, 0
    finally:
        conn.close()


def _claim_injection(
    repo_path: Path, session_id: str, decision_id: str, node_id: str
) -> tuple[bool, int]:
    """Atomically claim the right to show one decision this session.

    Returns ``(claimed, edit_notice_count)``. The primary key makes the
    INSERT OR IGNORE the once-per-session-per-decision gate, immune to the
    state-file races two concurrent hook processes produce; the count backs
    the strict per-session notice cap. Fail-closed: any error reports
    unclaimed, so a sidecar glitch degrades to silence, never to spam.
    """
    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category) "
            "VALUES (?, ?, ?, ?, 'decision', 'edit_notice')",
            (session_id, decision_id, node_id, time.time()),
        )
        claimed = cur.rowcount > 0
        # Surface-scoped: read/search enrichment rows also carry a node_id and
        # must not eat into the edit-notice cap.
        count = conn.execute(
            "SELECT COUNT(*) FROM injections WHERE session_id = ? AND node_id != '' "
            "AND surface IN ('', 'decision')",
            (session_id,),
        ).fetchone()[0]
        conn.commit()
        return claimed, int(count)
    except sqlite3.Error:
        return False, 0
    finally:
        conn.close()
