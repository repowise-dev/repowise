"""Relevance-ranked decision injection for the agent hooks.

Two delivery moments, both pure indexed-SQLite lookups (no LLM, no network,
target well under 100ms):

  * SessionStart — score the repo's decisions against the session's likely
    working set (dirty/staged files, branch-vs-main changed files, the previous
    session's edited files, branch-name tokens) expanded one hop via import
    edges and co-change partners, and inject the top few under a hard token
    cap. Relevance or silence: nothing clears the floor, nothing is injected.
    Never top-confidence-globally. Two labelled sections under two separate
    caps: accepted decisions, then the candidates nobody has agreed to. The
    second cannot shrink the first. A dismissed record is in neither.
  * Edit-time (PostToolUse Edit/Write) — when the edited file has a governing
    decision (via decision_node_links), say so once per session per decision,
    under a strict per-session cap.

Working agreements (records whose ``kind`` says they govern how the work is
conducted, so they name no file and have no node links) can only reach the
agent here: they carry a flat base relevance at SessionStart so a rule like
"never use em dashes" is deliverable at all, but they still compete under the
same floor and cap as everything else.

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
from pathlib import Path

from repowise.core.co_change import parse_partners

# --- SessionStart tunables -------------------------------------------------

#: Hard budget for the whole injected block, in estimated tokens (chars/4).
_TOKEN_CAP = 400
#: Minimum final score a decision needs to be injected at all. Rescaled when
#: confidence stopped being near-constant per source: keeping 0.25 would have
#: turned this into a gate on how much a record states.
_RELEVANCE_FLOOR = 0.20
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
#: Base relevance for working agreements (accepted, ``kind = 'agreement'``, no
#: node links). They apply everywhere, so SessionStart is their only path.
_W_GLOBAL_RULE = 0.5
#: At most this many unlinked global rules per block. Working-set-relevant
#: decisions must never be crowded out by always-eligible rules, and a
#: mis-promoted one-off (dogfood: "merge the backend PRs" made it to active)
#: costs at most one slot until it is dismissed.
_MAX_GLOBAL_RULES = 2

#: Budget for the candidate section, held separately from ``_TOKEN_CAP`` rather
#: than carved out of it. A shared cap would mean every candidate admitted
#: displaces an accepted decision that is injected today, which is the one
#: thing restoring candidates must not do; a separate cap makes the trade
#: explicit and bounded instead. Tighter than the accepted block by design:
#: nobody has agreed to any of these.
_CANDIDATE_TOKEN_CAP = 120
#: Never more than this many candidates, whatever the token budget allows.
_MAX_CANDIDATE_ITEMS = 2
#: And at most one of those slots may go to an unlinked repo-wide rule. A
#: repo-wide candidate clears the relevance floor on every session by
#: construction, so without this the lane would never carry a candidate that
#: is actually about the files in hand.
_MAX_CANDIDATE_GLOBALS = 1

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
# must not pay the sqlalchemy import; same pattern as fast_lookup)
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
            for partner in parse_partners(raw):
                if partner.file_path not in seeds:
                    hop.add(partner.file_path)
                    if len(hop) >= _MAX_HOP:
                        return hop
    return hop


# ---------------------------------------------------------------------------
# Decision loading + scoring
# ---------------------------------------------------------------------------


#: Governance is acceptance, not a status string. Spelled out here rather than
#: imported because this path opens the store with stdlib sqlite3 and never
#: imports ``repowise.core``; it mirrors ``crud.authority.ACCEPTED_SQL_PREDICATE``.
_ACCEPTED = "EXISTS (SELECT 1 FROM decision_acceptances a WHERE a.decision_id = {ref}.id)"

#: What to add to a governance query on a store that predates the entity split.
#: Nothing, deliberately. This path opens the store read-only and never runs the
#: schema reconciler, so it cannot create the table and cannot wait for one: an
#: acceptance filter there would silently strip every standing decision from the
#: agent, with no error and no way for this process to fix it. The status column
#: is what such a store has, and it is what the split's own migration reads.
_UNMIGRATED = "1 = 1"

#: ``lifecycle.AGREEMENT_KIND``, spelled out for the same reason as _ACCEPTED.
_AGREEMENT_KIND = "agreement"

#: Capture tiers a record may be delivered on with no acceptance row. A
#: ``(source, scope_basis)`` pair belongs here only once a census of its
#: (record, file) pairs has measured at least 90% governs with a 95% lower
#: bound of at least 80%; today only ``comment`` has. That is a result about
#: this corpus and not a property of the miner, so re-measure before adding a
#: pair, and re-measure if comment attribution changes.
_EVIDENCE_TIERS: frozenset[tuple[str, str]] = frozenset({("comment", "")})

#: Measured trust per tier, for ordering the candidate lane only. It orders,
#: it does not admit: the relevance floor still decides what enters the lane.
_TIER_TRUST: dict[tuple[str, str], int] = {
    ("comment", ""): 2,
    ("pr", "commit_selected"): 1,
    ("git_archaeology", "commit_selected"): 1,
}


def _accepted_clause(conn: sqlite3.Connection, ref: str) -> str:
    """The acceptance filter for *ref*, or a no-op on a pre-split store."""
    try:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("decision_acceptances",),
        ).fetchone()
    except sqlite3.Error:
        return _UNMIGRATED
    return _ACCEPTED.format(ref=ref) if found else _UNMIGRATED


def _kind_column(conn: sqlite3.Connection) -> str:
    """``kind`` where the store has the column, ``NULL`` where it does not.

    Probed the way :func:`_accepted_clause` probes for the acceptance table,
    and for the same reason: this path opens the store read-only, never runs
    the schema reconciler, and so has to read whatever it is given. Selecting
    a literal NULL keeps one shape of row for both stores, and NULL is what
    :func:`_is_repo_wide` reads as "this store cannot answer".
    """
    try:
        cols = conn.execute("PRAGMA table_info(decision_records)").fetchall()
    except sqlite3.Error:
        return "NULL"
    return "kind" if any(c[1] == "kind" for c in cols) else "NULL"


def _has_column(conn: sqlite3.Connection, column: str) -> bool:
    """Whether ``decision_records`` has *column* on this store.

    Probed for the reason :func:`_kind_column` gives. A store without
    ``scope_basis`` cannot say how a record was scoped, so it delivers nothing
    on evidence and keeps the acceptance path it has.
    """
    try:
        return any(c[1] == column for c in conn.execute("PRAGMA table_info(decision_records)"))
    except sqlite3.Error:
        return False


def _untouched_clause(conn: sqlite3.Connection, ref: str) -> str:
    """SQL matching records no reviewer has acted on.

    ``review_state`` is the column that knows, not ``status``: of the four
    review actions only ``dismiss_candidate`` writes ``status``, so a merged or
    split-flagged candidate still reads as ``proposed`` with no acceptance row.
    ``1 = 0`` where the store has no such table -- unreadable is not untouched.
    """
    try:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("decision_candidate_meta",),
        ).fetchone()
    except sqlite3.Error:
        return "1 = 0"
    if not found:
        return "1 = 0"
    return (
        f"NOT EXISTS (SELECT 1 FROM decision_candidate_meta m "
        f"WHERE m.decision_id = {ref}.id AND m.review_state <> 'open')"
    )


def _evidence_tier_clause(conn: sqlite3.Connection, ref: str) -> str:
    """SQL matching records whose capture tier delivers on its own evidence.

    ``1 = 0`` where the store cannot answer, which is the safe direction: a
    tier that cannot be read is not a tier that has been earned.
    """
    if not _EVIDENCE_TIERS or not _has_column(conn, "scope_basis"):
        return "1 = 0"
    # Interpolated, so anything but a bare identifier must not reach the SQL.
    if any(
        not isinstance(v, str) or not re.fullmatch(r"[a-z_]*", v)
        for pair in _EVIDENCE_TIERS
        for v in pair
    ):
        return "1 = 0"
    terms = [
        f"({ref}.source = '{src}' AND COALESCE({ref}.scope_basis, '') = '{basis}')"
        for src, basis in sorted(_EVIDENCE_TIERS)
    ]
    return "(" + " OR ".join(terms) + ")"


def _load_active_decisions(conn: sqlite3.Connection) -> list[dict]:
    """Accepted, current decisions with their node links, as plain dicts."""
    return _load_decisions(
        conn,
        "status = 'active' AND " + _accepted_clause(conn, "decision_records"),
    )


def _load_candidate_decisions(conn: sqlite3.Connection) -> list[dict]:
    """Records nobody has accepted, and that nobody has tombstoned either.

    The mirror image of :func:`_load_active_decisions`, and the same statuses
    ``_answer_context.fetch_relevant_decisions`` reads: ``active`` and
    ``proposed`` are live claims, while ``deprecated``, ``superseded`` and
    ``dismissed`` are history and must not be put to an agent as something to
    consider. Dismissed is the load-bearing one — a candidate tombstoned
    without ever having been accepted carries no acceptance row, so the
    acceptance test alone reads it as an ordinary candidate.

    On a store that predates the entity split :func:`_accepted_clause` is
    ``1 = 1``, so this returns nothing at all. That is the right answer rather
    than a degradation: such a store cannot tell a candidate from a decision,
    and the fallback it does have already delivers its records through the
    accepted path. Guessing here would inject its whole review queue.
    """
    return _load_decisions(
        conn,
        "status IN ('active', 'proposed') AND NOT ("
        + _accepted_clause(conn, "decision_records")
        + ")",
    )


def _load_decisions(conn: sqlite3.Connection, where: str) -> list[dict]:
    """Decision rows matching *where*, with their node links, as plain dicts."""
    try:
        rows = conn.execute(
            "SELECT id, title, decision, rationale, confidence, staleness_score, source, "
            + _kind_column(conn)
            + ", "
            + ("COALESCE(scope_basis, '')" if _has_column(conn, "scope_basis") else "''")
            + " FROM decision_records WHERE "
            + where
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
            "kind": r[7],
            "basis": r[8] or "",
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


def _is_repo_wide(decision: dict) -> bool:
    """Whether *decision* governs the repository rather than particular files.

    Mirrors ``decisions.lifecycle.is_repo_wide``:
    an agreement that names files has been given a real scope by something and
    the ordinary overlap rules apply to it, so the noun is necessary and not
    sufficient.

    A store written before the entity split has no noun to read, and refusing
    to call anything repo-wide there would stop delivering the rules it does
    hold. That store gets the guess this function replaces.
    """
    if decision["kind"] is None:
        return not decision["links"] and decision["source"] == "session"
    return decision["kind"] == _AGREEMENT_KIND and not decision["links"]


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
    if _is_repo_wide(decision):
        # A working agreement: it applies everywhere, so it gets a base
        # relevance instead of file overlap.
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


_ACCEPTED_HEADER = (
    "[repowise] Standing decisions relevant to this session's working set "
    "(accumulated from prior sessions; follow them unless the user says otherwise):"
)

#: Candidates are mined, not agreed. The header carries the whole of that
#: distinction in the transcript, so it says it in words rather than leaving an
#: agent to infer a lane from a blank line.
_CANDIDATE_HEADER = (
    "[repowise] Proposed but NOT accepted - mined from prior sessions and "
    "awaiting review. Weigh these; do not treat them as rules:"
)


def _tier_trust(decision: dict) -> int:
    """How far the capture tier of *decision* was measured to be trusted."""
    return _TIER_TRUST.get((decision.get("source") or "", decision.get("basis") or ""), 0)


def _rank(
    decisions: list[dict],
    seeds: set[str],
    hop: set[str],
    tokens: list[str],
    *,
    by_tier: bool = False,
) -> list[dict]:
    """Decisions above the relevance floor, most relevant first.

    *by_tier* orders by measured capture tier before relevance, for the
    candidate lane only: nobody has agreed to anything there, so how a record
    was scoped is all that separates two of them. The accepted lane does not
    use it, because a signature outranks a capture tier.
    """
    scored = [(d, _score_decision(d, seeds, hop, tokens)) for d in decisions]
    scored = [(d, score) for d, score in scored if score >= _RELEVANCE_FLOOR]
    if by_tier:
        scored.sort(key=lambda pair: (_tier_trust(pair[0]), pair[1]), reverse=True)
    else:
        scored.sort(key=lambda pair: pair[1], reverse=True)
    return [d for d, _ in scored]


def _select_lines(
    ranked: list[dict], header: str, token_cap: int, max_items: int, max_globals: int
) -> tuple[list[str], list[dict]]:
    """Render *ranked* under its own caps.

    Returns the header plus the rendered lines, and the decisions behind them.
    The caps are arguments rather than module reads because the two sections
    are budgeted separately, which is the point of having two.

    A line that does not fit is skipped, not read as the end of the list. One
    line can cost more than a whole section's budget, so stopping there makes
    the section's contents a function of the top record's verbosity rather
    than of its rank, and a single wordy record silences the section.
    """
    lines = [header]
    budget = token_cap - _estimate_tokens(header)
    shown: list[dict] = []
    globals_shown = 0
    for decision in ranked:
        if len(shown) >= max_items:
            break
        is_global = _is_repo_wide(decision)
        if is_global and globals_shown >= max_globals:
            continue
        line = _format_decision_line(decision)
        cost = _estimate_tokens(line)
        if cost > budget:
            continue
        lines.append(line)
        budget -= cost
        shown.append(decision)
        if is_global:
            globals_shown += 1
    return lines, shown


def _session_decision_block(repo_path: Path, session_id: str) -> str | None:
    """The relevance-ranked SessionStart decision block, or None (silence).

    Two sections under two budgets: accepted decisions under ``_TOKEN_CAP``,
    exactly as before, then candidates under ``_CANDIDATE_TOKEN_CAP``. The
    accepted section is selected first and nothing in the second can reduce
    what it holds, so restoring candidates cannot cost an agent a rule it is
    given today. Either section may come back empty; the block is emitted when
    either one is not.
    """
    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return None
    try:
        decisions = _load_active_decisions(conn)
        candidates = _load_candidate_decisions(conn)
        if not decisions and not candidates:
            return None
        seeds, branch = _collect_seeds(repo_path)
        seed_set = set(seeds)
        hop = _expand_one_hop(conn, seeds)
        tokens = _branch_tokens(branch)
        ranked = _rank(decisions, seed_set, hop, tokens)
        ranked_candidates = _rank(candidates, seed_set, hop, tokens, by_tier=True)
    finally:
        conn.close()

    accepted_lines, shown = _select_lines(
        ranked, _ACCEPTED_HEADER, _TOKEN_CAP, _MAX_ITEMS, _MAX_GLOBAL_RULES
    )
    candidate_lines, candidates_shown = _select_lines(
        ranked_candidates,
        _CANDIDATE_HEADER,
        _CANDIDATE_TOKEN_CAP,
        _MAX_CANDIDATE_ITEMS,
        _MAX_CANDIDATE_GLOBALS,
    )
    lines: list[str] = []
    if shown:
        lines += accepted_lines
    if candidates_shown:
        lines += candidate_lines
    if not lines:
        return None
    from repowise.cli.hook_ledger import _record_injections

    block = "\n".join(lines)
    _record_injections(
        repo_path,
        session_id,
        [d["id"] for d in shown + candidates_shown],
        node_id="",
        chars=len(block),
    )
    return block


# ---------------------------------------------------------------------------
# Edit-time governing-decision notice
# ---------------------------------------------------------------------------


def _governing_decisions(conn: sqlite3.Connection, rel: str) -> list[dict]:
    """Decisions governing *rel* via file links or module-prefix links.

    Two disjoint ways in: a record someone accepted, or one nobody has
    reviewed whose capture tier was measured to hold (:data:`_EVIDENCE_TIERS`).
    Review wins either way -- accepting delivers whatever the tier, and any
    other review action removes the record from the tier branch.

    **File links only.** What was measured is whether a record governs a file
    it names; a module link claims a whole subtree, so those stay
    acceptance-only.

    Link node ids are matched in POSIX regardless of how they were stored
    (Windows extraction persists backslashes). Top-level module links are
    ignored (see :func:`_module_deep_enough`).
    """
    out: list[dict] = []
    seen: set[str] = set()
    native = rel.replace("/", "\\")
    accepted = _accepted_clause(conn, "d")
    on_evidence = (
        f"(d.status IN ('active', 'proposed') AND NOT ({accepted}) "
        f"AND {_untouched_clause(conn, 'd')} "
        f"AND {_evidence_tier_clause(conn, 'd')})"
    )
    governs_file = f"(d.status = 'active' AND {accepted}) OR {on_evidence}"
    # A tier delivers on the surface it was measured on: file pairs, not
    # subtrees.
    governs_module = f"d.status = 'active' AND {accepted}"
    with contextlib.suppress(sqlite3.Error):
        for row in conn.execute(
            "SELECT d.id, d.title, d.decision, d.rationale, " + accepted + " "
            "FROM decision_node_links l JOIN decision_records d ON d.id = l.decision_id "
            "WHERE l.node_id IN (?, ?) AND l.link_type = 'file' AND (" + governs_file + ")",
            (rel, native),
        ):
            if row[0] not in seen:
                seen.add(row[0])
                out.append(_governing_row(row))
        # Module links are few; prefix-match them in Python.
        for row in conn.execute(
            "SELECT d.id, d.title, d.decision, d.rationale, " + accepted + ", l.node_id "
            "FROM decision_node_links l JOIN decision_records d ON d.id = l.decision_id "
            "WHERE l.link_type = 'module' AND (" + governs_module + ")"
        ):
            if (
                row[0] not in seen
                and _module_deep_enough(row[5])
                and rel.startswith(_norm_path(row[5]).rstrip("/") + "/")
            ):
                seen.add(row[0])
                out.append(_governing_row(row))
    # A reviewed decision outranks a mined one whatever the link order said.
    out.sort(key=lambda d: not d["accepted"])
    return out


def _governing_row(row: tuple) -> dict:
    """One ``_governing_decisions`` row, with how it earned its place."""
    return {
        "id": row[0],
        "title": row[1],
        "decision": row[2],
        "rationale": row[3],
        "accepted": bool(row[4]),
    }


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

    # Built before the claim, not after: the claim records what this emission
    # costs, and the cost model sums that column. A row claimed at zero chars
    # is an injection the net reports as free. Building first is a handful of
    # string operations on a path that has already done a database query.
    why = _clip(decision["rationale"] or decision["decision"], _CLIP_RATIONALE)
    if _echoes_title(decision["title"], why):
        why = ""  # legacy rows echo the title into decision/rationale
    # "Standing" means a person stood behind it, so a record delivered on its
    # tier alone must not borrow the word.
    if decision["accepted"]:
        line = f"[repowise] {rel} is governed by a standing decision: {_clip(decision['title'], 100)}"
    else:
        line = (
            f"[repowise] {rel} has a decision recorded in it, mined but not reviewed: "
            f"{_clip(decision['title'], 100)}"
        )
    if why:
        line += f" because {why}"
    if sessions_n >= 2:
        line += f" (confirmed across {sessions_n} sessions)"
    line += "."

    if session_id:
        from repowise.cli.hook_ledger import _claim_injection

        claimed, session_total = _claim_injection(
            repo_path, session_id, decision["id"], rel, chars=len(line)
        )
        if not claimed or session_total > _MAX_EDIT_NOTICES:
            return None
    return line


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
        from repowise.cli.hook_ledger import _claim_ledger

        from ._shared import _ledger_key

        claimed, shown = _claim_ledger(
            repo_path,
            session_id,
            _ledger_key("fix_history", "edit_notice", line),
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
