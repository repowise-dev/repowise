"""PostToolUse Bash: ask the agent to record, or settle, what it just committed.

The write-side hooks of this layer, both on a fresh commit:

* the capture prompt (on by default) asks for a record of a choice the commit
  message states;
* the upkeep prompt (off by default, ``decisions.upkeep_prompt``) names the
  decisions in force over the files the commit changed and the one command
  for each outcome: it still holds, this change replaced it, or this change
  knowingly breaks it. It advises rather than answers, the class of hook that
  has failed measurement before, so every firing is logged to the injections
  ledger under ``upkeep``/``commit_notice`` for the transcript classifier to
  judge.

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

from ._shared import _checkout_root, _find_repo_root

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
    "  repowise decision add --agent <your slug> --decision '<what you chose>' "
    "--rationale '<why, and what you rejected>' --affects <path> --evidence-commit {sha}\n"
    "  It comes into force when this commit merges. Once a session. "
    "Off: repowise decision config capture-prompt --off"
)

#: Decisions named per commit, and how many changed files are looked up. A
#: commit wider than the file cap is checked on its first files only; the
#: upgrade is one query over all in-force links instead of one per file.
_MAX_UPKEEP_DECISIONS = 3
_MAX_UPKEEP_FILES = 100
_CLIP_UPKEEP_TITLE = 60

#: One line. The ids are the eight-character prefixes every decision command
#: accepts, and ``efficacy`` reads them back out of this text to judge whether
#: a later command settled one, so its shape is a contract with that module.
_UPKEEP = (
    "[repowise] Commit {sha} changes code governed by {named}. Settle each with one "
    "command: still holds `repowise decision reaffirm <id> --agent <your slug>`; "
    "replaced `repowise decision deprecate <id> --superseded-by <new id> --agent <your slug>`; "
    "knowingly broken `repowise decision deprecate <id> --reason '<why>' --agent <your slug>`."
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
    commit = _fresh_commit(tool_input, tool_output, cwd, session_id)
    if commit is None:
        return None
    repo_path, policy, sha, message = commit
    lines = [
        _capture_prompt(repo_path, session_id, sha, message) if policy.commit_prompt else None,
        _upkeep_prompt(repo_path, session_id, sha) if policy.commit_upkeep else None,
    ]
    return "\n".join(line for line in lines if line) or None


def _fresh_commit(
    tool_input: dict, tool_output: object, cwd: str, session_id: str
) -> tuple[Path, object, str, str] | None:
    """``(repo, policy, sha, message)`` when this shell call just made a commit here."""
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
    # the flags these prompts claim are never cleared, so one such call would
    # silence the repository for good.
    if not session_id:
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None or not (repo_path / ".repowise").exists():
        return None
    # Checked before any git call: an opted-out repository should pay one
    # config read and nothing else.
    policy = _policy(repo_path)
    if not (policy.commit_prompt or policy.commit_upkeep):
        return None
    # ``.repowise`` marks the indexed repository, not the git one. This tree
    # nests other repositories inside it, and a commit made in one of those
    # must not be read as a commit here. A worktree reading this index is
    # compared as itself: `repowise decision add` there records into this
    # index too.
    checkout = _checkout_root(Path(cwd), repo_path)
    here = _git_root(cwd)
    if here is None or here != _git_root(str(checkout)):
        return None

    sha, committed_at, message = _head_commit(checkout)
    # Both sides: a committer date ahead of this clock — a pulled commit, a
    # machine whose clock jumped — otherwise passes an age test for free, and
    # the age test is the only thing standing in for "did this call commit".
    if not sha or abs(time.time() - committed_at) > _MAX_COMMIT_AGE_SECONDS:
        return None
    return repo_path, policy, sha, message


def _capture_prompt(repo_path: Path, session_id: str, sha: str, message: str) -> str | None:
    """The once-a-session ask to record the choice *message* states."""
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


def _upkeep_prompt(repo_path: Path, session_id: str, sha: str) -> str | None:
    """Name the in-force decisions this commit's files carry, each once a session."""
    files = _commit_files(repo_path)
    if not files:
        return None

    from .read_state import _load_session_state, _save_session_state

    state = _load_session_state(repo_path, session_id)
    named: list = state.setdefault("upkeep_named", [])
    governing = _governing_any(repo_path, files, set(named))
    if not governing:
        return None
    titles = "; ".join(
        f"{d['id'][:8]} \"{_clip(d['title'], _CLIP_UPKEEP_TITLE)}\"" for d in governing
    )
    noun = "decision" if len(governing) == 1 else f"{len(governing)} decisions"
    line = _UPKEEP.format(sha=sha[:8], named=f"{noun}: {titles}")

    from repowise.cli.hook_ledger import _claim_ledger

    from ._shared import _ledger_key
    from .command import _claim_emission

    if not _claim_emission("decision-upkeep", f"{session_id}{chr(0)}{sha}"):
        return None
    claimed, _ = _claim_ledger(
        repo_path,
        session_id,
        _ledger_key("upkeep", "commit_notice", line),
        # The records it named, so each one's delivery count includes this line.
        node_id=" ".join(d["id"] for d in governing),
        surface="upkeep",
        category="commit_notice",
        chars=len(line),
    )
    if not claimed:
        return None
    named.extend(d["id"] for d in governing)
    _save_session_state(repo_path, state)
    return line


def _governing_any(repo_path: Path, files: list[str], skip: set[str]) -> list[dict]:
    """Up to :data:`_MAX_UPKEEP_DECISIONS` decisions in force over *files*, in file order."""
    from .decision_inject import _governing_decisions, _open_wiki_ro

    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return []
    out: list[dict] = []
    try:
        for rel in files[:_MAX_UPKEEP_FILES]:
            for decision in _governing_decisions(conn, rel):
                if decision["id"] in skip:
                    continue
                skip.add(decision["id"])
                out.append(decision)
                if len(out) >= _MAX_UPKEEP_DECISIONS:
                    return out
    finally:
        conn.close()
    return out


def _clip(text: str, cap: int) -> str:
    from .decision_inject import _clip as clip

    return clip(text, cap).replace('"', "'")


def _commit_files(repo_path: Path) -> list[str]:
    """Repo-relative POSIX paths HEAD changed. ``--root`` covers a first commit.

    ``-z`` keeps git from quoting and octal-escaping a non-ASCII path, which
    would then never match the path a decision is linked to.
    """
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    if result.returncode != 0:
        return []
    return [path for path in result.stdout.split("\0") if path]


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


def _policy(repo_path: Path):
    """This repository's resolved decision policy."""
    from repowise.core.analysis.decisions.policy_store import load_policy

    return load_policy(repo_path).policy


def _git_root(path: str) -> str | None:
    """The git work tree *path* sits in, as a resolved posix string."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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
