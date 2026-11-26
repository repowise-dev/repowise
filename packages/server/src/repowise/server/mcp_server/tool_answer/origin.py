"""Origin questions: which change introduced a symbol or file, when, and by whom.

"Which PR introduced _MAX_COOKIES" asks about history. Retrieval answers it
with the definition line, which is a confident answer to another question.
When the origin verb takes a named symbol or file as its object, the answer is
the oldest commit that adds it, read back to its indexed Change Record for the
PR, title and author. A file's adding commit comes from the indexed history
when that history reaches back to it. The index keeps no per-symbol history,
so a symbol (and a file older than the indexed window) walks git: ``git log
-S`` over the files that define it, ``--diff-filter=A`` for a file. Walks are
kept per repository head, so a repeat costs nothing. An origin question this cannot answer says so and
names the git command that can, instead of falling through to retrieval.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from collections import OrderedDict
from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.changed_lines import shallow_roots
from repowise.core.persistence.crud.changes import find_changes
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitCommit, GitCommitFile, GitMetadata, WikiSymbol
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._meta import build_meta, read_live_head
from repowise.server.mcp_server._query_shape import path_tokens
from repowise.server.mcp_server.tool_answer.symbols import _extract_question_identifiers

# A question about where something came from: "which PR/commit ...", "who
# added ...", "when was/did ...", "first introduced".
_ORIGIN_RE = re.compile(
    r"\b(?:which|what)\s+(?:pull\s+request|merge\s+request|pr|mr|commit|change|patch)\b"
    r"|\bwho\s+(?:first\s+|originally\s+|initially\s+)?(?:introduc|add|creat|wr[io]t|author)\w*"
    r"|\bwhen\s+(?:was|were|did)\b"
    r"|\b(?:first|originally|initially)\s+(?:introduc|add|creat|land)\w*",
    re.IGNORECASE,
)
# "start failing" asks when a regression began, not when the code was added.
_VERB = (
    r"(?:introduc\w*|add(?:ed|s|ing)?|creat\w*|wr[io]te|written|authored|landed"
    r"|start(?:ed|s)?(?!\s+\w+ing\b)|appear\w*|brought\s+in)"
)
# Past tense only: "how Session is created" asks about behaviour, not origin.
_ADVERB = r"(?:(?:was|were|got|first|originally|initially)\s+){0,2}"
_ARTICLE = r"(?:(?:the|a|an)\s+)?"

#: Defining files the pickaxe walks. Ceiling: a symbol defined in more files
#: than this (a common method name) is not an origin anyone can pin down.
_MAX_DEF_FILES = 5
#: Seconds for the history walk; a slower one answers "could not tell".
_GIT_TIMEOUT = 10
_LOG_FORMAT = "--format=%H\t%an\t%aI\t%s"

#: Git walks already made: (repo id, kind, name, paths, live head) -> the
#: commit, or why none adds it. History below a head never changes, so an
#: entry is right until the head moves. In-process only; the oldest goes first.
_WALKS: OrderedDict[tuple, dict | str] = OrderedDict()
_WALKS_MAX = 256


def asks_origin(question: str) -> bool:
    """Whether *question* asks which change introduced something, or when or by whom."""
    if not _ORIGIN_RE.search(question):
        return False
    return bool(re.search(rf"\b{_VERB}\b", question, re.IGNORECASE))


def _is_object(question: str, name: str) -> bool:
    """Whether the origin verb takes *name* itself as its object or subject.

    "introduced the X", "X was first added", "did X start" count; "added
    retries to X" does not: that asks about a behaviour inside X, which X's own
    first commit does not answer.
    """
    # Case-sensitive: "Session" names the class, "session" is a word.
    n = rf"`?(?-i:{re.escape(name)})`?"
    # A sentence-final period ends the name; "x.py" or "a.b" continues it.
    # "X's retry logic" is a behaviour inside X.
    active = rf"\b{_VERB}\s+{_ARTICLE}{n}(?!\w|\.\w|['’]s\b)"
    passive = rf"(?<![\w.]){n}\s+{_ADVERB}{_VERB}\b"
    return bool(re.search(f"{active}|{passive}", question, re.IGNORECASE))


async def origin_reply(ask: Any) -> dict | None:
    """The origin answer for an origin question that names code, else ``None``."""
    question = ask.question
    if not asks_origin(question):
        return None
    files = [t.replace("\\", "/").removeprefix("./") for t in path_tokens(question)]
    idents = sorted(_extract_question_identifiers(question) - _stems(files), key=len, reverse=True)
    if not files and not idents:
        # Prose subject ("when did we start caching"): retrieval may still
        # find the commit-free part of the answer.
        return None
    repo_path = getattr(ask.ctx, "path", None)
    async with get_session(ask.ctx.session_factory) as session:
        subject = await _subject(session, ask, question, files, idents)
        if subject is None:
            named = (files or idents)[0]
            defined = files[:1] or await _defining_files(session, ask, named)
            return _cannot(
                ask,
                named,
                defined[:_MAX_DEF_FILES],
                f"the question asks about a change inside `{named}`, not when `{named}` itself was added",
                kind="file" if files else "symbol",
                inside=True,
            )
        name, paths, kind = subject
        if unclear := _unclear(name, paths, kind):
            return _cannot(ask, name, [], unclear, kind=kind)
        commit = (
            await _indexed_file_origin(session, ask.repo_id, paths[0]) if kind == "file" else None
        )
        if commit is None:
            commit, reason = await _first_commit(repo_path, ask.repo_id, name, paths, kind)
        if commit is None:
            return _cannot(ask, name, paths, reason, kind=kind)
        found = await find_changes(session, ask.repo_id, commit["sha"], limit=1)
    return _answer(ask, name, paths, kind, commit, found[0] if found else None)


def _unclear(name: str, paths: list[str], kind: str) -> str | None:
    """Why *paths* name no one place to read the origin in, or ``None``."""
    if not paths:
        return f"no indexed file {'is' if kind == 'file' else 'defines'} `{name}`"
    if kind == "file" and len(paths) > 1:
        listed = ", ".join(paths[:3]) + (", ..." if len(paths) > 3 else "")
        return f"{len(paths)} indexed files are named `{name}` ({listed}); ask with the full path"
    if len(paths) > _MAX_DEF_FILES:
        return f"{len(paths)} indexed files define `{name}`, too many to name one origin"
    return None


def _stems(files: list[str]) -> set[str]:
    return {Path(f).stem for f in files}


async def _subject(
    session, ask: Any, question: str, files: list[str], idents: list[str]
) -> tuple[str, list[str], str] | None:
    """``(name, defining paths, "file" | "symbol")`` for the code the origin verb
    takes, or ``None`` when it takes something else."""
    for f in files:
        if _is_object(question, f) or _is_object(question, Path(f).name):
            return f, await _indexed_files(session, ask, f), "file"
    for ident in idents:
        if _is_object(question, ident):
            return ident, await _defining_files(session, ask, ident), "symbol"
    return None


async def _indexed_files(session, ask: Any, token: str) -> list[str]:
    """The indexed file *token* names: its path, else every file with that name."""
    rows = (
        (
            await session.execute(
                select(GitMetadata.file_path).where(
                    GitMetadata.repository_id == ask.repo_id,
                    (GitMetadata.file_path == token) | GitMetadata.file_path.endswith(f"/{token}"),
                )
            )
        )
        .scalars()
        .all()
    )
    rows = [r for r in rows if not is_excluded(r, ask.exclude_spec)]
    exact = [r for r in rows if r == token]
    return exact or sorted(rows)


async def _defining_files(session, ask: Any, ident: str) -> list[str]:
    """Files holding a definition of *ident* (its last dotted part)."""
    leaf = ident.rsplit(".", 1)[-1]
    rows = (
        (
            await session.execute(
                select(WikiSymbol.file_path)
                .where(WikiSymbol.repository_id == ask.repo_id, WikiSymbol.name == leaf)
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return sorted(r for r in rows if not is_excluded(r, ask.exclude_spec))


_NO_COMMIT = "no commit in this checkout's history adds it"
_TIMED_OUT = f"reading its git history took longer than {_GIT_TIMEOUT} s"


async def _indexed_file_origin(session, repo_id: str, path: str) -> dict | None:
    """The commit that added *path*, from the indexed history, or ``None`` when
    that history does not reach back to it (capped, renamed, or older than the
    stored commits)."""
    meta = (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id, GitMetadata.file_path == path
            )
        )
    ).scalar_one_or_none()
    if meta is None or meta.commit_count_capped or meta.original_path or not meta.first_commit_at:
        return None
    row = (
        await session.execute(
            select(GitCommit)
            .join(
                GitCommitFile,
                (GitCommitFile.repository_id == GitCommit.repository_id)
                & (GitCommitFile.sha == GitCommit.sha),
            )
            .where(GitCommit.repository_id == repo_id, GitCommitFile.file_path == path)
            .order_by(GitCommit.committed_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.committed_at is None:
        return None
    # The stored commits reach the file's first commit only when the oldest
    # one touching it is that first commit.
    if abs((_utc(row.committed_at) - _utc(meta.first_commit_at)).total_seconds()) > 1:
        return None
    return {
        "sha": row.sha,
        "author": row.author_name,
        "date": _utc(row.committed_at).date().isoformat(),
        "subject": row.subject,
        "source": "index",
    }


def _utc(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def _first_commit(
    repo_path: str | None, repo_id: str, name: str, paths: list[str], kind: str
) -> tuple[dict | None, str]:
    """``(commit, "")``, or ``(None, why no commit is named)``."""
    if not repo_path or not (Path(repo_path) / ".git").exists():
        return None, "this checkout has no git history"
    head = read_live_head(str(repo_path))
    key = (repo_id, kind, name, tuple(paths), head)
    commit = _WALKS.get(key) if head else None
    if commit is None:
        try:
            commit = await asyncio.wait_for(
                asyncio.to_thread(_oldest_adding_commit, str(repo_path), name, paths, kind),
                timeout=_GIT_TIMEOUT + 1,
            )
        except TimeoutError:
            return None, _TIMED_OUT
        # A walk that failed or ran out of time may succeed next time.
        if head and (isinstance(commit, dict) or commit == _NO_COMMIT):
            _WALKS[key] = commit
            while len(_WALKS) > _WALKS_MAX:
                _WALKS.popitem(last=False)
    else:
        _WALKS.move_to_end(key)
    if isinstance(commit, str):
        return None, commit
    if commit.get("cut"):
        return None, "it predates the oldest commit in this shallow clone"
    return commit, ""


def _oldest_adding_commit(repo_path: str, name: str, paths: list[str], kind: str) -> dict | str:
    """The oldest commit whose diff adds *name* to *paths* (or adds the file),
    or why there is none."""
    if kind == "file":
        args = ["--diff-filter=A", "--follow"]
    else:
        # A rename moves the text without adding it; follow one file across renames.
        # Whole word: a bare -S would count "parse" inside "parser".
        word = re.escape(name.rsplit(".", 1)[-1])
        args = [
            "--pickaxe-regex",
            f"-S(^|[^A-Za-z0-9_]){word}([^A-Za-z0-9_]|$)",
            *(["--follow"] if len(paths) == 1 else []),
        ]
    try:
        proc = subprocess.run(
            ["git", "log", "--topo-order", *args, _LOG_FORMAT, "--", *paths],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            # A git child inheriting the JSON-RPC stdin can wedge the session.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return _TIMED_OUT
    except OSError:
        return "git could not be run"
    lines = [ln for ln in proc.stdout.splitlines() if ln.count("\t") >= 3]
    if proc.returncode != 0:
        return "git could not read this checkout's history"
    if not lines:
        return _NO_COMMIT
    sha, author, date, subject = lines[-1].split("\t", 3)
    return {
        "sha": sha,
        "author": author,
        "date": date[:10],
        "subject": subject,
        "source": "git",
        # A shallow clone's cut commit shows as adding everything it holds.
        "cut": sha in shallow_roots(repo_path),
    }


def _answer(ask: Any, name: str, paths: list[str], kind: str, commit: dict, change: Any) -> dict:
    short = commit["sha"][:12]
    by = f"{commit['author']}, {commit['date']}"
    if change is not None and change.forge_ref:
        title = change.title or commit["subject"]
        what = f'{change.forge_ref} "{title}" (commit {short}, by {by})'
    else:
        what = f'commit {short} "{commit["subject"]}" (by {by})'
    where = "" if kind == "file" else f", in {', '.join(paths)}"
    answer = f"`{name}` was introduced by {what}{where}."
    origin: dict = {
        "subject": name,
        "commit": commit["sha"],
        "author": commit["author"],
        "date": commit["date"],
    }
    hint = f"Use the answer directly; it rests on git history of {paths[0]}."
    if change is not None:
        origin["change"] = change.change_id
        hint = f'get_context(targets=["change:{change.change_id}"]) for the whole change.'
    return {
        "answer": answer,
        "citations": paths,
        "confidence": "high",
        "grounding": "git_history",
        "origin": origin,
        "note": (
            ("Read from the indexed history" if commit["source"] == "index" else "Read from git")
            + ": the oldest commit that adds it"
            + (", and the indexed change that landed that commit." if change is not None else ".")
        ),
        "next_action_hint": hint,
        "_meta": build_meta(
            timing_ms=(time.perf_counter() - ask.t0) * 1000,
            repository=ask.repository,
            targets=paths,
        ),
    }


def _cannot(
    ask: Any, name: str, paths: list[str], reason: str, *, kind: str, inside: bool = False
) -> dict:
    """Says plainly the origin is not known, and names the git command that can tell."""
    one = paths[0] if len(paths) == 1 else None
    leaf = name.rsplit(".", 1)[-1]
    if kind == "file":
        command = f"git log --follow {'-p' if inside else '--diff-filter=A'} -- {name}"
    elif inside:
        command = f"git log -L:{leaf}:{one or '<file>'}"
    else:
        command = f"git log -S{json.dumps(leaf)} --reverse" + (f" -- {one}" if one else "")
    return {
        "answer": f"Not known from the index which change introduced `{name}`: {reason}.",
        "citations": paths,
        "confidence": "low",
        "grounding": "git_history",
        "next_action_hint": f"Run `{command}` to read the commits that touched it.",
        "_meta": build_meta(
            timing_ms=(time.perf_counter() - ask.t0) * 1000, repository=ask.repository
        ),
    }
