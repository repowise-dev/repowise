"""PreToolUse shell hook: run ``repowise health check`` before an agent's ``git commit`` or ``git push``.

Opt-in per repository and off by default. It acts only where
``hooks.health_gate: true`` is set, which ``repowise hook health-gate install``
writes together with the repo-local Claude Code entry; ``init``, ``upgrade``
and the self-heal never write either.

The verdict is the command's own: this runs ``repowise health check`` as a
subprocess and blocks only on its exit 1 (the gate failed). Everything else
fails open, with no output: exit 2, a crash, a timeout past
:data:`BUDGET_SECONDS`, a command shape this cannot read, no git, no index.

What is checked is what the command would record:

* ``git commit``: the staged index against ``HEAD``, after replaying any
  ``git add`` earlier in the same chain and ``-a``. It is built in a temporary
  index and object directory, so the repository is not touched. ``--amend``,
  pathspecs, ``--only``/``--include`` and interactive forms are not read.
* ``git push``: ``@{u}...HEAD``, else the merge-base with the default branch
  (the command's own default). A push of another branch, tags, ``--all``,
  ``--mirror``, ``--delete`` or ``--dry-run`` is not read.

Module scope is stdlib only: this fires on every shell call in an opted-in
repository and returns on the first regex for everything that is not git.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

#: Wall-clock budget for the whole gate; past it the command goes through.
BUDGET_SECONDS = 20.0
_GIT_TIMEOUT = 5.0
#: Shown in the deny reason; more than this is counted, not listed.
_MAX_LISTED = 5

_QUICK_RE = re.compile(r"\bgit\b[\s\S]*\b(?:commit|push)\b")
_OPERATORS = frozenset({"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"})
_REDIRECT_RE = re.compile(r"^[<>&]+$")
#: Global ``git`` options that take the next token as their value.
_GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
#: Global options that point git somewhere this hook does not follow.
_GIT_OPAQUE_OPTS = frozenset({"--git-dir", "--work-tree", "--namespace", "--bare"})
#: Verbs that may run earlier in the chain without changing what is committed or pushed.
_READ_ONLY_VERBS = frozenset(
    {"status", "diff", "log", "show", "fetch", "remote", "rev-parse", "ls-files", "describe"}
)

_COMMIT_VALUE_SHORT = frozenset("mFCct")
_COMMIT_VALUE_LONG = frozenset({
    "--message", "--file", "--reuse-message", "--reedit-message", "--fixup", "--squash",
    "--template", "--author", "--date", "--cleanup", "--trailer",
})
_COMMIT_UNREAD_LONG = frozenset({
    "--amend", "--patch", "--interactive", "--only", "--include", "--dry-run",
    "--pathspec-from-file", "--short", "--porcelain", "--long",
})
_COMMIT_UNREAD_SHORT = frozenset("poi")
_ADD_UNREAD = frozenset({"-p", "--patch", "-i", "--interactive", "-e", "--edit", "-n", "--dry-run"})

_PUSH_UNREAD = frozenset({
    "--all", "--branches", "--mirror", "--tags", "--delete", "--dry-run", "--prune",
})
_PUSH_UNREAD_SHORT = frozenset("dn")
_PUSH_VALUE = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})

#: Identity for the throwaway snapshot commit, so an unset user.name cannot fail it.
_SNAPSHOT_IDENTITY = {
    "GIT_AUTHOR_NAME": "repowise", "GIT_AUTHOR_EMAIL": "repowise@localhost",
    "GIT_COMMITTER_NAME": "repowise", "GIT_COMMITTER_EMAIL": "repowise@localhost",
}


@dataclass(frozen=True)
class GitCall:
    """One ``git`` invocation in a shell command, with the directory it runs in."""

    verb: str
    cwd: str
    args: tuple[str, ...]
    #: Pointed elsewhere (``--git-dir``, ``--work-tree``): not read.
    opaque: bool = False


def run(stdin: TextIO, out: TextIO) -> None:
    """Read one PreToolUse payload from *stdin*; write a deny to *out* on a failed gate."""
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        return
    reason = deny_reason(payload) if isinstance(payload, dict) else None
    if reason:
        out.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        out.flush()


def deny_reason(payload: dict) -> str | None:
    """Why this shell call is blocked, or ``None`` to let it run (every unclear case)."""
    from repowise.cli.agent_adapters.claude_code import SHELL_TOOL_NAMES

    if payload.get("hook_event_name") != "PreToolUse":
        return None
    if payload.get("tool_name") not in SHELL_TOOL_NAMES:
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = payload.get("cwd")
    if not isinstance(command, str) or not _QUICK_RE.search(command) or not isinstance(cwd, str):
        return None
    try:
        return gate(command, cwd)
    except Exception:
        # Fail open: a broken gate never blocks a commit.
        return None


def gate(command: str, cwd: str, *, budget: float = BUDGET_SECONDS) -> str | None:
    """The deny reason for the first commit or push in *command* that fails the gate."""
    calls = git_calls(command, cwd)
    deadline = time.monotonic() + budget
    for i, call in enumerate(calls):
        if call.verb not in ("commit", "push") or call.opaque or not _enabled(call.cwd):
            continue
        check = _check_commit if call.verb == "commit" else _check_push
        reason = check(call, calls[:i], deadline)
        if reason:
            return reason
    return None


def _enabled(cwd: str) -> bool:
    from repowise.cli.commands.augment_cmd._shared import _find_repo_root, hook_flag_enabled

    repo = _find_repo_root(Path(cwd))
    return repo is not None and hook_flag_enabled(repo, "health_gate")


# -- reading the command ------------------------------------------------------


def git_calls(command: str, cwd: str) -> list[GitCall]:
    """Every ``git`` call in *command*, in order; ``[]`` when it does not tokenize."""
    calls: list[GitCall] = []
    here = cwd
    for tokens in _segments(command):
        tokens = _unwrap(tokens)
        if len(tokens) >= 2 and tokens[0] == "cd":
            here = os.path.join(here, os.path.expanduser(tokens[1]))
        elif tokens and Path(tokens[0]).name.lower() in ("git", "git.exe"):
            call = _git_call(tokens[1:], here)
            if call is not None:
                calls.append(call)
    return calls


def _segments(command: str) -> list[list[str]]:
    """The simple commands in *command*, split on shell operators, redirections dropped."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    skip_next = False
    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
        elif token in _OPERATORS:
            segments.append([])
        elif _REDIRECT_RE.match(token):
            skip_next = True  # the redirection's target
        elif token.isdigit() and i + 1 < len(tokens) and _REDIRECT_RE.match(tokens[i + 1]):
            continue  # the ``2`` of ``2>&1``
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _unwrap(tokens: list[str]) -> list[str]:
    """*tokens* without leading ``VAR=value`` and the ``repowise distill`` wrapper."""
    while tokens and re.match(r"^\w+=", tokens[0]):
        tokens = tokens[1:]
    if tokens[:2] != ["repowise", "distill"]:
        return tokens
    rest = tokens[2:]
    if rest[:1] == ["--source"]:
        rest = rest[2:]
    if len(rest) == 1:  # the rewritten form quotes the whole command
        segments = _segments(rest[0])
        return segments[0] if len(segments) == 1 else []
    return rest


def _git_call(args: list[str], cwd: str) -> GitCall | None:
    """The verb, effective directory and arguments of one ``git ...`` command."""
    opaque = False
    i = 0
    while i < len(args) and args[i].startswith("-"):
        name = args[i].split("=", 1)[0]
        opaque = opaque or name in _GIT_OPAQUE_OPTS
        if args[i] in _GIT_VALUE_OPTS:
            if i + 1 >= len(args):
                return None
            if args[i] == "-C":
                cwd = os.path.join(cwd, os.path.expanduser(args[i + 1]))
            i += 1
        i += 1
    if i >= len(args):
        return None
    return GitCall(args[i], cwd, tuple(args[i + 1:]), opaque)


def commit_plan(args: tuple[str, ...]) -> str | None:
    """``"staged"`` or ``"all"`` (``-a``) for a commit this can read; ``None`` otherwise."""
    plan = "staged"
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            return None if args[i:] else plan
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name in _COMMIT_UNREAD_LONG:
                return None
            if name == "--all":
                plan = "all"
            elif name in _COMMIT_VALUE_LONG and "=" not in arg:
                i += 1
            continue
        if not arg.startswith("-") or arg == "-":
            return None  # a pathspec: the commit is not the index
        step = _short_cluster(arg[1:])
        if step is None:
            return None
        all_flag, takes_next = step
        plan = "all" if all_flag else plan
        i += 1 if takes_next else 0
    return plan


def _short_cluster(flags: str) -> tuple[bool, bool] | None:
    """``(has -a, value is the next token)`` for one ``-xyz`` cluster; ``None`` if unread."""
    has_all = False
    for j, ch in enumerate(flags):
        if ch in _COMMIT_UNREAD_SHORT:
            return None
        has_all = has_all or ch == "a"
        if ch in _COMMIT_VALUE_SHORT:
            return has_all, j == len(flags) - 1
        if ch in "Su":  # optional value, attached only
            break
    return has_all, False


def push_targets_head(args: tuple[str, ...], branch: str) -> bool:
    """Whether this push sends the current branch and nothing else this cannot read."""
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg.split("=", 1)[0] in _PUSH_UNREAD:
            return False
        if arg in _PUSH_VALUE:
            i += 1
        elif arg.startswith("--"):
            continue
        elif arg.startswith("-"):
            if _PUSH_UNREAD_SHORT & set(arg[1:]):
                return False
            i += 1 if arg.endswith("o") else 0
        else:
            positional.append(arg)
    allowed = {"HEAD", branch, f"refs/heads/{branch}"} - {""}
    return all(ref.lstrip("+").split(":", 1)[0] in allowed for ref in positional[1:])


# -- checking -----------------------------------------------------------------


def _check_commit(call: GitCall, before: list[GitCall], deadline: float) -> str | None:
    plan = commit_plan(call.args)
    root = _git(call.cwd, "rev-parse", "--show-toplevel")
    if plan is None or not root or any(c.verb not in _READ_ONLY_VERBS | {"add"} for c in before):
        return None
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    if not head:
        return None  # the first commit: nothing to compare with
    with tempfile.TemporaryDirectory(prefix="repowise-gate-") as tmp:
        env = _scratch_env(root, Path(tmp))
        if env is None:
            return None
        adds = [c for c in before if c.verb == "add" and not c.opaque]
        for add in adds:
            if _git(add.cwd, "rev-parse", "--show-toplevel") != root:
                continue
            if _ADD_UNREAD & set(add.args) or _git(add.cwd, "add", *add.args, env=env) is None:
                return None
        if plan == "all" and _git(root, "add", "-u", env=env) is None:
            return None
        snapshot = _snapshot(root, head, env)
        if snapshot is None:
            return None
        env.pop("GIT_INDEX_FILE")
        failing = _run_health_check(root, f"{head}..{snapshot}", env, deadline)
    return _reason("commit", failing, None) if failing else None


def _check_push(call: GitCall, before: list[GitCall], deadline: float) -> str | None:
    root = _git(call.cwd, "rev-parse", "--show-toplevel")
    if not root or any(c.verb not in _READ_ONLY_VERBS | {"add", "commit"} for c in before):
        return None
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD") or ""
    if not push_targets_head(call.args, branch):
        return None
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    upstream = _git(root, "rev-parse", "--verify", "-q", "@{u}")
    if not head or head == upstream:
        return None
    revspec = f"{upstream}...{head}" if upstream else None
    failing = _run_health_check(root, revspec, dict(os.environ), deadline)
    shown = "@{u}...HEAD" if upstream else None
    return _reason("push", failing, shown) if failing else None


def _scratch_env(root: str, tmp: Path) -> dict[str, str] | None:
    """An environment whose index and new objects live in *tmp*, reading the repo's objects."""
    index = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")
    objects = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    if not index or not objects or not Path(index).is_file():
        return None
    shutil.copyfile(index, tmp / "index")
    (tmp / "objects").mkdir()
    inherited = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    return {
        **os.environ,
        **_SNAPSHOT_IDENTITY,
        "GIT_INDEX_FILE": str(tmp / "index"),
        "GIT_OBJECT_DIRECTORY": str(tmp / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(filter(None, [objects, inherited])),
    }


def _snapshot(root: str, head: str, env: dict[str, str]) -> str | None:
    """A commit of the scratch index on top of *head*; ``None`` when it equals *head*."""
    tree = _git(root, "write-tree", env=env)
    if not tree or tree == _git(root, "rev-parse", f"{head}^{{tree}}", env=env):
        return None
    return _git(root, "commit-tree", tree, "-p", head, "-m", "repowise health gate", env=env)


def _run_health_check(
    root: str, revspec: str | None, env: dict[str, str], deadline: float
) -> list[dict]:
    """The failing rows of ``repowise health check``; ``[]`` unless it exited 1 with them."""
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        return []
    argv = [sys.executable, "-m", "repowise.cli.main", "health", "check"]
    argv += [revspec] if revspec else []
    argv += ["--path", root, "--format", "json"]
    try:
        proc = subprocess.run(
            argv, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, timeout=remaining, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 1:
        return []
    try:
        failing = json.loads(proc.stdout)["gate"]["failing"]
    except (ValueError, KeyError, TypeError):
        return []  # exit 1 without the report is a crash, not a verdict
    return [row for row in failing if isinstance(row, dict)] if isinstance(failing, list) else []


def _reason(what: str, failing: list[dict], revspec: str | None) -> str:
    """The short message the agent sees in place of the command's output."""
    lines = [
        f"repowise health gate: this {what} adds {len(failing)} health finding(s) that fail "
        "the gate.",
    ]
    for row in failing[:_MAX_LISTED]:
        where = str(row.get("file_path") or "?")
        if row.get("line_number"):
            where += f":{row['line_number']}"
        lines.append(f"  {where}  {row.get('message', '')}")
    if len(failing) > _MAX_LISTED:
        lines.append(f"  ... and {len(failing) - _MAX_LISTED} more")
    report = ""
    if what == "push":
        report = f"; `repowise health check{' ' + revspec if revspec else ''}` shows the full report"
    lines.append(
        f"Fix them and {what} again{report}. The gate is opt-in for this repository: "
        "`repowise hook health-gate uninstall` turns it off."
    )
    return "\n".join(lines)


def _git(cwd: str, *args: str, env: dict[str, str] | None = None) -> str | None:
    """``git`` stdout, stripped; ``None`` on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, env=env, timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None
