"""PostToolUse Bash: ask the agent to record the choice it just committed.

The only write-side hook this layer has. Every other surface delivers what was
already recorded; nothing asked for a record, and of the records this
repository holds from ``decision add`` a person wrote all of them.

**An agent cannot decline a hook**, so the gates matter more than the wording.
This fires at most once per session, only after a fresh commit in *this*
repository, only when the message carries two or more decision signals, and
never for a commit a record already cites. It proposes; the agent decides and
writes the record itself, which is what makes the record worth having — the
agent knows what it chose and why, and a miner reading the message afterwards
can only infer.

Budget: no ``repowise.core`` import at module scope, raw :mod:`sqlite3`, and
silence on every failure.
"""

from __future__ import annotations

import sqlite3
import subprocess
import time
from pathlib import Path

from ._shared import _find_repo_root

#: How many distinct signal keywords the message must carry. One admits 58% of
#: this repository's commits and two admits 25%; the once-per-session gate is
#: what bounds the cost, and this keeps the session's one ask for a commit that
#: reads like a choice.
_MIN_SIGNALS = 2

#: How recently HEAD must have been committed for this to be *that* commit.
#: The success of the shell call cannot be trusted on its own — the PowerShell
#: tool result carries no exit code, so a string scan is all that is left, and
#: a commit subject saying "remove the old fatal handler" reads as a failure
#: while `nothing to commit` reads as a success. Freshness answers the question
#: the exit code was standing in for: did this call produce a new commit.
#:
#: Generous, because the hook fires when the whole shell call returns and not
#: when ``git commit`` does — ``git commit && npm test`` is one call. It only
#: has to separate this call's commit from yesterday's.
_MAX_COMMIT_AGE_SECONDS = 900.0

#: Only a commit. A merge or a rebase replays choices somebody already had the
#: chance to record. ``--dry-run`` writes nothing, so it must not consume the
#: session's one ask on whatever HEAD happens to be.
_COMMIT_PATTERNS = ("git commit",)
_NOT_A_COMMIT = ("--dry-run", "--no-verify --dry-run")

_PROMPT = (
    "[repowise] Commit {sha} states a choice and nothing records it. If it is a "
    "decision a future edit should follow, record it while you still have the "
    "reasoning:\n"
    "  repowise decision add --title '<short>' --decision '<what you chose>' "
    "--rationale '<why, and what you rejected>' --affects <path> "
    "--evidence-commit {sha} --format json\n"
    "  Lands as a candidate for review, never as a rule. Once a session. "
    "Off: repowise decision config capture-prompt --off"
)


def commit_capture_notice(
    tool_input: dict, tool_output: object, cwd: str, session_id: str
) -> str | None:
    """The capture prompt for a just-made commit, or ``None`` to stay quiet."""
    try:
        return _notice(tool_input, tool_output, cwd, session_id)
    except Exception:
        return None


def _notice(tool_input: dict, tool_output: object, cwd: str, session_id: str) -> str | None:
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        return None
    if not any(p in command for p in _COMMIT_PATTERNS) or any(
        p in command for p in _NOT_A_COMMIT
    ):
        return None
    if _failed(tool_output):
        return None
    # An unidentified session would share one state file with every other, and
    # the flag below is claimed and never cleared, so one such call would
    # silence the repository for good.
    if not session_id:
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None or not (repo_path / ".repowise").exists():
        return None
    # Checked before the session state is read: the switch ships off, so the
    # opted-out repository should pay one config read and nothing else.
    if not _capture_enabled(repo_path):
        return None
    # ``.repowise`` marks the indexed repository, not the git one. This tree
    # nests other repositories inside it, and a commit made in one of those
    # must not be read as a commit here.
    here = _git_root(cwd)
    if here is None or here != _git_root(str(repo_path)):
        return None

    sha, committed_at, message = _head_commit(repo_path)
    # Both sides: a committer date ahead of this clock — a pulled commit, a
    # machine whose clock jumped — otherwise passes an age test for free, and
    # the age test is the only thing standing in for "did this call commit".
    if not sha or abs(time.time() - committed_at) > _MAX_COMMIT_AGE_SECONDS:
        return None

    from repowise.core.analysis.decisions.commit_signals import count_decision_signals

    if count_decision_signals(message) < _MIN_SIGNALS:
        return None
    if _already_recorded(repo_path, sha):
        return None

    from .read_state import _load_session_state, _save_session_state

    state = _load_session_state(repo_path, session_id)
    if state.get("capture_prompted"):
        return None

    from .command import _claim_emission

    # Both hooks fire on one tool event, and the session state is a
    # read-modify-write of a file the Read surfaces also own. Claiming first
    # means the loser writes nothing: it neither clobbers that file nor spends
    # the session's one ask on a prompt it will not return.
    if not _claim_emission("decision-capture", f"{session_id}{chr(0)}{sha}"):
        return None
    # Claimed rather than confirmed: a session that saw the prompt and ignored
    # it has still had its ask, and re-asking is the tax.
    state["capture_prompted"] = True
    if not _save_session_state(repo_path, state):
        return None
    return _PROMPT.format(sha=sha[:8])


def _failed(tool_output: object) -> bool:
    """Whether the shell call reported a non-zero exit.

    Only the exit code, and only when the harness sent one. The string scan
    that used to stand in for it is wrong in both directions, and freshness
    covers the case where no code arrives at all.
    """
    from .bash_staleness import extract_exit_code

    output = tool_output if isinstance(tool_output, dict) else {}
    code = extract_exit_code(output)
    return code is not None and code != 0


def _capture_enabled(repo_path: Path) -> bool:
    """Whether this repository switched the prompt on. Off is the default."""
    from repowise.core.analysis.decisions.policy_store import load_policy

    return bool(load_policy(repo_path).policy.capture_prompt)


def _git_root(path: str) -> str | None:
    """The git work tree *path* sits in, as a resolved posix string."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve().as_posix()


def _head_commit(repo_path: Path) -> tuple[str, float, str]:
    """``(sha, committed_at, subject + body)`` for HEAD, or ``("", 0.0, "")``.

    Read from git rather than parsed out of the command: a commit made with
    ``-F``, a heredoc or an editor carries its message nowhere the hook can
    see it. No ``--no-merges`` — that filters merges out of the walk and
    returns an older ancestor, so finishing a conflicted merge with
    ``git commit`` would gate on an unrelated commit.
    """
    result = subprocess.run(
        ["git", "log", "-1", "--pretty=%H%n%ct%n%s%n%b"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return "", 0.0, ""
    head, _, rest = result.stdout.partition("\n")
    stamp, _, message = rest.partition("\n")
    try:
        committed_at = float(stamp.strip())
    except ValueError:
        return "", 0.0, ""
    return head.strip(), committed_at, message.strip()


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
