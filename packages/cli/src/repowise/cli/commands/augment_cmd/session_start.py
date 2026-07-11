"""Claude Code SessionStart: index freshness + tool-protocol context block.

The generated CLAUDE.md is static between reindexes; what it cannot carry is
whether the index is current *right now*. This handler closes that gap with a
short per-session block so the agent starts with calibrated trust instead of
discovering staleness mid-task:

  * index current  → one line saying so, plus the core-tool pointer;
  * update running → positive "catching up" notice (never a stale scare);
  * index behind   → indexed vs HEAD with a changed-file count, and the
    target-scoped trust rule (stale_warning only when a served file changed).

Deliberately no hotspot/health lines here: CLAUDE.md already carries those
statically, and this block is re-billed every session; freshness is the only
signal that must be live. The one addition is the relevance-ranked standing
decisions block (see :mod:`.decision_inject`), which is also live by nature:
it is scored against what this session is about to touch.

Same operational rules as the rest of augment: stdlib + git subprocess only,
any failure returns None, and a git failure never produces a false "current"
claim (it degrades to the freshness-free reminder).
"""

from __future__ import annotations

import json
from pathlib import Path

from ._shared import _find_repo_root
from .bash_staleness import _read_in_flight_marker
from .decision_inject import _session_decision_block

_CORE_TOOLS = "get_answer, get_context, get_symbol, search_codebase, get_risk"


def _handle_claude_session_start(cwd: str, session_id: str = "") -> str | None:
    """Return the session context block, or None outside indexed repos."""
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None
    try:
        state = json.loads((repo_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    last_sync = state.get("last_sync_commit")
    if not isinstance(last_sync, str) or not last_sync:
        return None

    freshness = _freshness_block(repo_path, last_sync)
    if freshness is None:
        return None

    # Relevance-ranked standing decisions for this session's working set;
    # silent whenever nothing clears the floor (see decision_inject).
    try:
        decisions = _session_decision_block(repo_path, session_id)
    except Exception:
        decisions = None
    return f"{freshness}\n{decisions}" if decisions else freshness


def _freshness_block(repo_path: Path, last_sync: str) -> str | None:
    """The live index-freshness paragraph (the original SessionStart block)."""
    head = _git_head(repo_path)
    if head is None:
        return (
            "[repowise] This repository has a local codebase index. Prefer the "
            f"repowise MCP tools ({_CORE_TOOLS}) over raw file reads for locating "
            "and understanding code before you edit."
        )

    if head == last_sync:
        return (
            f"[repowise] Codebase index is current (HEAD {head[:8]}). The MCP tools "
            f"({_CORE_TOOLS}) serve content verified against this exact tree; prefer "
            "them over raw file reads for locating and understanding code before you edit."
        )

    in_flight = _read_in_flight_marker(repo_path)
    if in_flight is not None:
        elapsed = in_flight.get("elapsed_seconds")
        elapsed_str = (
            f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
        )
        return (
            f"[repowise] Index update in progress ({elapsed_str}), catching up to "
            f"{head[:8]}. The MCP tools remain reliable meanwhile; a response carries "
            "`stale_warning` only when a file it serves actually changed."
        )

    changed = _changed_file_count(repo_path, last_sync, head)
    drift = f" ({changed} files changed since)" if changed is not None else ""
    return (
        f"[repowise] Index is behind HEAD: indexed {last_sync[:8]}, now {head[:8]}{drift}. "
        f"The MCP tools ({_CORE_TOOLS}) stay reliable: a response carries `stale_warning` "
        "only when a file it serves actually changed, and silence means current. "
        "Run `repowise update` to resync."
    )


def _git_head(repo_path: Path) -> str | None:
    """Live HEAD SHA, or None when git can't answer quickly."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and head else None


def _changed_file_count(repo_path: Path, indexed: str, live: str) -> int | None:
    """Files changed between the indexed commit and live HEAD, or None."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", indexed, live],
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
    return sum(1 for line in out.stdout.splitlines() if line.strip())
