"""Claude Code SessionStart: what is true now and not already in context.

The block is re-billed for the whole session, so it says only what is both
true and new, and is silent otherwise:

  * no generated ``CLAUDE.md`` block → the call-first line (a plugin-only
    install has no other place that says it);
  * update running → positive "catching up" notice (never a stale scare);
  * index behind   → indexed vs HEAD with a changed-file count, and the
    target-scoped trust rule (stale_warning only when a served file changed);
  * the rewrite hook installed but ``repowise`` off PATH → one line saying so;
  * with ``decisions.session_start`` on, the relevance-ranked decisions block
    (see :mod:`.decision_inject`), scored against what this session touches.

A current index says nothing: the agent assumes it, and the ``CLAUDE.md``
block already carries the indexed commit.

Same operational rules as the rest of augment: stdlib + git subprocess only,
any failure returns None, and a git failure never produces a false "current"
claim.
"""

from __future__ import annotations

import json
from pathlib import Path

# The leaf module, not the ``helpers`` facade: the facade imports rich and
# asyncio, which every hook call would pay at import.
from repowise.cli._helpers.git import as_commit_id
from repowise.core.agent_steer import AGENT_STEER

from ._shared import _checkout_root, _find_repo_root, emitting_build
from .bash_staleness import _read_in_flight_marker
from .decision_inject import _session_decision_block as _decision_block
from .fast_lookup import index_locked


def _session_decision_block(
    repo_path: Path, session_id: str, checkout: Path | None = None
) -> str | None:
    """The standing-decisions block, or None while another process locks the index.

    A locked index fails every decision query anyway, each after its own busy
    wait; one probe answers the same within one wait.
    """
    if index_locked(repo_path):
        return None
    return _decision_block(repo_path, session_id, checkout)

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

    checkout = _checkout_root(Path(cwd), repo_path)
    if checkout != repo_path:
        freshness = _worktree_freshness_block(repo_path, checkout, last_sync)
    else:
        freshness = _freshness_block(repo_path, last_sync)
    # Every line here is paid for the whole session, so each one is said only
    # when it is true and not already in the agent's context.
    lines = [line for line in (freshness, _distill_off_path_note()) if line]
    if not _has_agent_block(checkout):
        lines.insert(0, AGENT_STEER)

    # Relevance-ranked standing decisions for this session's working set;
    # silent whenever nothing clears the floor (see decision_inject).
    try:
        decisions = _session_decision_block(repo_path, session_id, checkout)
    except Exception:
        decisions = None
    if decisions:
        lines.append(decisions)
    if not lines:
        return None
    # The tag makes what follows attributable to the install that emitted it,
    # which a transcript otherwise cannot recover; the ledger stamps every row
    # for the same reason.
    return f"[repowise {emitting_build()}] " + "\n".join(lines)


#: The opening marker of the block ``repowise init`` writes into ``CLAUDE.md``
#: (``generation.editor_files.ClaudeMdGenerator``), spelled out because that
#: module loads Jinja, which a hook should not pay for.
_AGENT_BLOCK_MARKER = "<!-- REPOWISE:START"


def _has_agent_block(checkout: Path) -> bool:
    """Whether a ``CLAUDE.md`` Claude Code reads here already carries the block.

    The plugin writes no ``CLAUDE.md``, so a plugin-only install learns the
    call-first line from this hook; a repository where ``repowise init`` wrote
    the block already has the line and is not told twice.
    """
    for path in (checkout / ".claude" / "CLAUDE.md", checkout / "CLAUDE.md"):
        try:
            if _AGENT_BLOCK_MARKER in path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


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
    """The live index-freshness paragraph, or None when there is nothing to say.

    A current index is what the agent assumes already (the ``CLAUDE.md`` block
    carries the indexed commit), and a HEAD git cannot read is no fact at all,
    so both stay silent. Only drift is news.

    A sidechain has no SessionStart, so a subagent's emissions are attributable
    through the ledger's ``build`` column only. That is a real gap, stated
    rather than papered over.
    """
    head = _git_head(repo_path)
    if head is None or head == last_sync:
        return None

    in_flight = _read_in_flight_marker(repo_path)
    if in_flight is not None:
        elapsed = in_flight.get("elapsed_seconds")
        elapsed_str = (
            f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
        )
        return (
            f"Index update in progress ({elapsed_str}), catching up to "
            f"{head[:8]}. The MCP tools remain reliable meanwhile; a response carries "
            "`stale_warning` only when a file it serves actually changed."
        )

    changed = _changed_file_count(repo_path, last_sync, head)
    drift = f" ({changed} files changed since)" if changed is not None else ""
    return (
        f"Index is behind HEAD: indexed {last_sync[:8]}, now {head[:8]}{drift}. "
        f"The MCP tools ({_CORE_TOOLS}) stay reliable: a response carries `stale_warning` "
        "only when a file it serves actually changed, and silence means current. "
        "Run `repowise update` to resync."
    )


def _worktree_freshness_block(repo_path: Path, checkout: Path, last_sync: str) -> str | None:
    """The freshness paragraph for a worktree reading its main checkout's index.

    The index describes *repo_path* at *last_sync*, not this branch, so the
    paragraph says which files that holds for instead of advising an update:
    `repowise update` here would build the worktree an index of its own.
    Silent while the worktree sits at the indexed commit: nothing differs.
    """
    head = _git_head(checkout)
    base = f"This worktree reads the codebase index of {repo_path}, indexed at {last_sync[:8]}."
    if head is None or head == last_sync:
        return None
    changed = _changed_file_count(checkout, last_sync, head)
    drift = f"{changed} files differ" if changed is not None else "files may differ"
    return (
        f"{base} This worktree is at {head[:8]} ({drift}). The MCP tools ({_CORE_TOOLS}) "
        "answer for the indexed version, so facts about files that differ describe "
        "the main checkout's copy."
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
