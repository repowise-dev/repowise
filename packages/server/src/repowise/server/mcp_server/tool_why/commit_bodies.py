"""The body of a commit get_why serves: often the description of the PR it merged.

A squash or merge commit's subject restates the change; the reason is in the
body the forge wrote under it. The indexer already keeps the bodies that carry
decision words on each file's significant commits, so those cost no git call;
any other body is one local ``git cat-file`` over the few commits served. Nothing
here touches the network: a description the forge never wrote into git is not
read (ceiling: a forge API fetch at index time).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from repowise.core.changes.reconstruct import _is_trailer
from repowise.core.ingestion.git_indexer._constants import _MAX_COMMIT_BODY_BYTES
from repowise.server.mcp_server._why_relevance import (
    question_terms,
    relevance,
    term_idf,
    word_set,
)

#: Characters of one excerpt; the rest is one ``git show`` away.
EXCERPT_CHARS = 280
#: Commits whose bodies one call reads. The compact reply serves one excerpt.
MAX_COMMITS = 4
_GIT_TIMEOUT_S = 5
_MIN_PARAGRAPH_CHARS = 40
_MIN_TERMS = 2

_COMMIT_LANES = ("file_commits", "cross_references", "git_log")
_SUBJECT_LINE_RE = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
#: Headings whose section never states the reason: how it was checked, not why.
_SKIPPED_SECTION_RE = re.compile(
    r"^(?:verif|test|checklist|screenshot|how to test|reviewer|related|changelog)", re.I
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SEPARATOR_RE = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,})\s*$")


def body_excerpt(body: str, subject: str, query: str) -> str | None:
    """The paragraph of *body* that best answers *query*, bounded, or ``None``.

    ``None`` unless the paragraph carries two of the question's words, the bar
    a card's commits clear: a quoted body that is about something else reads
    as the reason when it is not.
    """
    paragraphs = _paragraphs(body, subject)
    if not paragraphs:
        return None
    words = [word_set(p) for p in paragraphs]
    idf = term_idf(question_terms(query), words)
    scores = [relevance(w, idf) for w in words]
    best = max(range(len(paragraphs)), key=lambda i: (scores[i], -i))
    if sum(t in words[best] for t in idf) < _MIN_TERMS:
        return None
    return _bounded(paragraphs[best])


def _paragraphs(body: str, subject: str) -> list[str]:
    """The prose paragraphs of *body*: no trailers, tables, code, checklists or test sections."""
    subject_words = word_set(subject)
    out = []
    for block in _prose_blocks(body):
        text = " ".join(block)
        if len(text) < _MIN_PARAGRAPH_CHARS or word_set(text) <= subject_words:
            continue
        if all(_is_trailer(line, True) for line in block):
            continue
        out.append(text)
    return out


def _prose_blocks(body: str) -> Iterator[list[str]]:
    """Blank-line separated runs of prose lines, markup stripped."""
    block: list[str] = []
    for line in _prose_lines(body):
        if line:
            block.append(line)
        elif block:
            yield block
            block = []
    if block:
        yield block


def _prose_lines(body: str) -> Iterator[str]:
    """Each line of *body* as prose, or ``""`` where a paragraph breaks or markup sits."""
    skipping = in_fence = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            line = ""
        elif heading := _HEADING_RE.match(line):
            skipping = bool(_SKIPPED_SECTION_RE.match(heading.group(1)))
            line = ""
        bare = _BULLET_RE.sub("", line)
        # A squash body lists its commits' subjects; a subject restates, it does not explain.
        if in_fence or skipping or _is_markup(line) or _SUBJECT_LINE_RE.match(bare):
            bare = ""
        yield bare


def _is_markup(line: str) -> bool:
    return (
        line.startswith(("|", "<!--", "- [", "* [", ">"))
        or bool(_SEPARATOR_RE.match(line))
    )


def _bounded(text: str) -> str:
    if len(text) <= EXCERPT_CHARS:
        return text
    cut = text[: EXCERPT_CHARS - 3]
    # End on a sentence when one ends in the second half, else on a word.
    stop = cut.rfind(". ")
    if stop > EXCERPT_CHARS // 2:
        return cut[: stop + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "..."


async def attach_body_excerpts(
    result: dict[str, Any], repo_root: Any, target_git: dict[str, Any], query: str
) -> None:
    """Give the response's commit rows the excerpt of their body that bears on *query*.

    Rows are the commits the question already singled out: the archaeology
    lanes left after focusing, top level and on each target card. A row whose
    body names its PR gets ``pr_number`` too, so the excerpt is citable.
    """
    rows = _commit_rows(result)[:MAX_COMMITS]
    if not rows:
        return
    stored = _stored_bodies(target_git)
    missing = [_sha(r) for r in rows if _sha(r) not in stored]
    live = await _live_bodies(repo_root, missing) if missing else {}
    for row in rows:
        _annotate(row, stored.get(_sha(row)) or live.get(_sha(row)), query)


def _annotate(row: dict, found: tuple[str, int | None] | None, query: str) -> None:
    body, pr = found or ("", None)
    excerpt = body_excerpt(body, str(row.get("message") or ""), query) if body else None
    if excerpt:
        row["body_excerpt"] = excerpt
        if pr and not row.get("pr_number"):
            row["pr_number"] = pr


def keep_one_excerpt(result: dict[str, Any]) -> None:
    """Leave ``body_excerpt`` on the first served commit that has one, in place.

    The MCP reply quotes one body; more is one ``git show`` per commit away.
    """
    found = False
    for row in _lane_rows(result, with_key_commits=True):
        if "body_excerpt" in row:
            if found:
                del row["body_excerpt"]
            found = True


def _commit_rows(result: dict[str, Any]) -> list[dict]:
    """Unique commit rows of the archaeology lanes, top level first, in served order."""
    seen: set[str] = set()
    rows = []
    for row in _lane_rows(result):
        sha = _sha(row)
        if sha and sha not in seen:
            seen.add(sha)
            rows.append(row)
    return rows


def _lane_rows(result: dict[str, Any], *, with_key_commits: bool = False) -> Iterator[dict]:
    if with_key_commits:
        yield from _dicts(result.get("key_commits"))
    cards = _dicts((result.get("target_context") or {}).values())
    for block in _dicts([result.get("git_archaeology"), *(c.get("git_archaeology") for c in cards)]):
        for lane in _COMMIT_LANES:
            yield from _dicts(block.get(lane))


def _dicts(rows: Any) -> list[dict]:
    return [r for r in rows or [] if isinstance(r, dict)]


def _sha(row: dict) -> str:
    return str(row.get("sha") or row.get("commit") or "")[:8].lower()


def _stored_bodies(target_git: dict[str, Any]) -> dict[str, tuple[str, int | None]]:
    """``{sha8: (body, pr number)}`` from the targets' indexed significant commits."""
    out: dict[str, tuple[str, int | None]] = {}
    for meta in target_git.values():
        raw = getattr(meta, "significant_commits_json", None)
        try:
            entries = json.loads(raw) if raw else []
        except ValueError:
            continue
        for e in entries:
            # A body at the indexer's cap was cut; the clone has it whole.
            body = e.get("body") if isinstance(e, dict) else None
            if body and e.get("sha") and len(body.encode()) < _MAX_COMMIT_BODY_BYTES - 4:
                out.setdefault(str(e["sha"])[:8].lower(), (e["body"], e.get("pr_number")))
    return out


async def _live_bodies(repo_root: Any, shas: list[str]) -> dict[str, tuple[str, int | None]]:
    """Bodies of *shas* from the local clone; empty without one, or on any git failure."""
    if not repo_root or not (Path(repo_root) / ".git").exists():
        return {}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_git_bodies, str(repo_root), shas), timeout=_GIT_TIMEOUT_S + 1
        )
    except (TimeoutError, subprocess.SubprocessError, OSError):
        return {}


def _git_bodies(repo_root: str, shas: list[str]) -> dict[str, tuple[str, int | None]]:
    """``{sha8: (body, pr number)}`` from one ``git cat-file --batch``.

    The batch answers each name on its own, so a sha the clone lacks (a shallow
    clone, rewritten history, an ambiguous prefix) loses only its own body.
    """
    from repowise.core.forges.changes import change_number
    from repowise.core.forges.detect import detect_forge

    names = [s for s in shas if re.fullmatch(r"[0-9a-f]{4,40}", s)]
    if not names:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo_root,
        input="".join(f"{n}^{{commit}}\n" for n in names).encode(),
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        return {}
    messages = dict(zip(names, _batch_messages(proc.stdout), strict=False))
    forge = detect_forge(repo_root) if messages else None
    out: dict[str, tuple[str, int | None]] = {}
    for name, message in messages.items():
        subject, _, body = message.partition("\n\n")
        if body.strip():
            subject = " ".join(subject.split())
            out[name[:8].lower()] = (body.strip(), change_number(subject, body, forge))
    return out


def _batch_messages(raw: bytes) -> Iterator[str]:
    """Each object's commit message, ``""`` for a name git reported missing."""
    pos = 0
    while pos < len(raw):
        eol = raw.find(b"\n", pos)
        if eol < 0:
            return
        header = raw[pos:eol].split()
        pos = eol + 1
        if len(header) != 3 or header[1] != b"commit":
            yield ""
            continue
        size = int(header[2])
        commit = raw[pos : pos + size].decode("utf-8", errors="replace")
        pos += size + 1
        yield commit.partition("\n\n")[2]
