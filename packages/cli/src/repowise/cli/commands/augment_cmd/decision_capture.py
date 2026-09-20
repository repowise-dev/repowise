"""PostToolUse Bash: ask the agent to record the choice it just committed.

The only write-side hook this layer has. Every other surface delivers what was
already recorded; nothing asked for a record, so the store's four
hand-authored records were written by a person and none by an agent.

**An agent cannot decline a hook**, so the gates matter more than the wording.
This fires at most once per session, only after a successful commit, only when
the message carries two or more decision signals, and never for a commit a
record already cites. It proposes; the agent decides and runs ``decision add``
itself, which is what makes the record worth having — the agent knows what it
chose and why, and a miner reading the message afterwards does not.

Budget: no ``repowise.core`` import at module scope, raw :mod:`sqlite3`, and
silence on every failure.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from ._shared import _find_repo_root

#: How many distinct signal keywords the message must carry. One admits 58% of
#: this repository's commits and two admits 25%; the once-per-session gate is
#: what bounds the cost, and this keeps the session's one prompt for a commit
#: that reads like a choice.
_MIN_SIGNALS = 2

#: Commands that can produce the commit this fires on. ``git commit`` only:
#: a merge or a rebase replays choices somebody already had the chance to
#: record, and asking again is the duplicate this layer already has too many of.
_COMMIT_PATTERNS = ("git commit",)

_PROMPT = (
    "[repowise] That commit states a choice and nothing records it. If it was a "
    "decision a future edit should follow, record it while you still have the "
    "reasoning:\n"
    "  repowise decision add --title '<short>' --decision '<what you chose>' "
    "--rationale '<why, and what you rejected>' --affects <path> --format json\n"
    "  [dim]Lands as a candidate for review, never as a rule. Once a session. "
    "Off: repowise decision config capture-prompt --off[/dim]"
)


def commit_capture_notice(
    tool_input: dict, tool_output: object, cwd: str, session_id: str
) -> str | None:
    """The capture prompt for a just-made commit, or ``None`` to stay quiet.

    Gates run cheapest first: the session marker is a file read, the policy is
    a second one, and only then does git run.
    """
    try:
        return _notice(tool_input, tool_output, cwd, session_id)
    except Exception:
        return None


def _notice(tool_input: dict, tool_output: object, cwd: str, session_id: str) -> str | None:
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not any(p in command for p in _COMMIT_PATTERNS):
        return None
    if not _succeeded(tool_output):
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None or not (repo_path / ".repowise").exists():
        return None

    from .read_state import _load_session_state, _save_session_state

    state = _load_session_state(repo_path, session_id)
    if state.get("capture_prompted"):
        return None

    if not _capture_enabled(repo_path):
        return None

    sha, message = _head_commit(repo_path)
    if not sha:
        return None

    from repowise.core.analysis.decisions.commit_signals import count_decision_signals

    if count_decision_signals(message) < _MIN_SIGNALS:
        return None
    if _already_recorded(repo_path, sha):
        return None

    # Claimed before the prompt is returned: a session that saw it and did
    # nothing has still spent its one ask, and re-asking is the tax.
    state["capture_prompted"] = True
    if not _save_session_state(repo_path, state):
        return None
    return _PROMPT


def _succeeded(tool_output: object) -> bool:
    """Whether the shell call looks like it worked, on the Bash result shape."""
    output = tool_output if isinstance(tool_output, dict) else {"stdout": str(tool_output)}
    code = output.get("exit_code", output.get("exitCode"))
    if isinstance(code, int):
        return code == 0
    combined = f"{output.get('stdout', '')}\n{output.get('stderr', '')}".lower()
    return "error" not in combined and "fatal" not in combined


def _capture_enabled(repo_path: Path) -> bool:
    """Whether this repository switched the prompt on. Off is the default."""
    from repowise.core.analysis.decisions.policy_store import load_policy

    return bool(load_policy(repo_path).policy.capture_prompt)


def _head_commit(repo_path: Path) -> tuple[str, str]:
    """``(sha, subject + body)`` for HEAD, or ``("", "")``.

    Read from git rather than parsed out of the command: a commit made with
    ``-F``, a heredoc or an editor carries its message nowhere the hook can
    see it.
    """
    result = subprocess.run(
        ["git", "log", "-1", "--no-merges", "--pretty=%H%n%s%n%b"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return "", ""
    head, _, message = result.stdout.partition("\n")
    return head.strip(), message.strip()


def _already_recorded(repo_path: Path, sha: str) -> bool:
    """Whether a record already cites this commit. Fails closed, into silence."""
    from .decision_inject import _open_wiki_ro

    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return True
    try:
        row = conn.execute(
            "SELECT 1 FROM decision_records WHERE evidence_commits_json LIKE ? LIMIT 1",
            (f"%{sha[:8]}%",),
        ).fetchone()
    except sqlite3.Error:
        return True
    finally:
        conn.close()
    return row is not None
