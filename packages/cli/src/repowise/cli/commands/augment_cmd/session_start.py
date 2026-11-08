"""Claude Code SessionStart: index freshness + tool-protocol context block.

The generated CLAUDE.md is static between reindexes; what it cannot carry is
whether the index is current *right now*. This handler closes that gap with a
short per-session block so the agent starts with calibrated trust instead of
discovering staleness mid-task:

  * index current  → one line saying so;
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
claim (it degrades to a line without the freshness fact).
"""

from __future__ import annotations

import json
from pathlib import Path

# The leaf module, not the ``helpers`` facade: the facade imports rich and
# asyncio, which every hook call would pay at import.
from repowise.cli._helpers.git import as_commit_id

from ._shared import _find_repo_root, emitting_build
from .bash_staleness import _read_in_flight_marker
from .decision_inject import _session_decision_block as _decision_block
from .fast_lookup import index_locked


def _session_decision_block(repo_path: Path, session_id: str) -> str | None:
    """The standing-decisions block, or None while another process locks the index.

    A locked index fails every decision query anyway, each after its own busy
    wait; one probe answers the same within one wait.
    """
    if index_locked(repo_path):
        return None
    return _decision_block(repo_path, session_id)

#: The tools this block points a fresh session at. **Entry points only.**
#:
#: ``get_symbol`` used to be in this list and is deliberately not. It is a
#: follow-up on an id another response already handed over, and naming it here
#: — three times per session, in the one block every session reads first — is
#: what turns it into a first-reach tool. An agent that sees it listed beside
#: ``get_answer`` spends most of its retrieval calls fetching one symbol at a
#: time; an agent that does not, reaches for the coarse tools instead. The tool
#: is still served and still in the CLAUDE.md table, just not advertised as
#: somewhere to start. Same reasoning, at more length, in
#: ``generation/editor_files/tool_table.py``.
_CORE_TOOLS = "get_answer, get_context, search_codebase, get_risk"


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
    off_path = _distill_off_path_note()
    if off_path:
        freshness = f"{freshness} {off_path}"

    # Relevance-ranked standing decisions for this session's working set;
    # silent whenever nothing clears the floor (see decision_inject).
    try:
        decisions = _session_decision_block(repo_path, session_id)
    except Exception:
        decisions = None
    return f"{freshness}\n{decisions}" if decisions else freshness


#: Said when the rewrite hook is installed but every command it would rewrite
#: passes through, because the ``repowise distill`` it emits would not start.
_DISTILL_OFF_PATH = "Command distilling is off: repowise is not on PATH."


def _distill_off_path_note() -> str:
    """:data:`_DISTILL_OFF_PATH` when the rewrite hook cannot rewrite, else "".

    The rewrite hook runs from a pinned path but emits a bare ``repowise``, so
    it passes every command through when that name is not on PATH. This
    process has the same PATH as the hook. Only the user settings are read:
    a plugin's hooks use bare names, so off PATH this hook would not be
    running either. The common path pays one PATH lookup.
    """
    import shutil

    if shutil.which("repowise") is not None:
        return ""
    try:
        settings = (Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return _DISTILL_OFF_PATH if "repowise-rewrite" in settings else ""


def _freshness_block(repo_path: Path, last_sync: str) -> str | None:
    """The live index-freshness paragraph (the original SessionStart block).

    The tag carries the emitting build. It costs a few tokens once per session
    and it makes the injections that follow attributable to the install that
    produced them, which a transcript otherwise cannot recover. The ledger
    stamps every row for the same reason; this is the half a transcript can
    read back.

    A sidechain has no SessionStart, so a subagent's emissions are attributable
    through the ledger's ``build`` column only. That is a real gap, stated
    rather than papered over.
    """
    tag = f"[repowise {emitting_build()}]"
    head = _git_head(repo_path)
    # No "prefer the MCP tools" sentence on the two lines below: agents did not
    # act on it, and the generated CLAUDE.md already carries the tool table.
    if head is None:
        return f"{tag} This repository has a local codebase index."

    if head == last_sync:
        return f"{tag} Codebase index is current (HEAD {head[:8]})."

    in_flight = _read_in_flight_marker(repo_path)
    if in_flight is not None:
        elapsed = in_flight.get("elapsed_seconds")
        elapsed_str = (
            f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
        )
        return (
            f"{tag} Index update in progress ({elapsed_str}), catching up to "
            f"{head[:8]}. The MCP tools remain reliable meanwhile; a response carries "
            "`stale_warning` only when a file it serves actually changed."
        )

    changed = _changed_file_count(repo_path, last_sync, head)
    drift = f" ({changed} files changed since)" if changed is not None else ""
    return (
        f"{tag} Index is behind HEAD: indexed {last_sync[:8]}, now {head[:8]}{drift}. "
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

    if as_commit_id(indexed) is None or as_commit_id(live) is None:
        return None
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--end-of-options", indexed, live],
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
