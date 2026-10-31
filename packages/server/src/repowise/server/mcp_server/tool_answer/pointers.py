"""Locate pointers on a get_answer reply, so placing a cited detail needs no Read.

Quoted code in the answer gets the line range it came from, and functions the
answer names without serving their bodies get the get_symbol call that reads
them (``next_actions``; on an answer with a flow path the first call adds
``depth=2``). Applied at projection time, so cached replies get them
too. Best-effort: a failure leaves the reply as it was.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

_log = logging.getLogger("repowise.mcp.answer.pointers")

#: A fenced code block, fences on their own lines; group 1 is its body (None when empty).
_FENCE_RE = re.compile(r"^[ \t]*```[^\n`]*\n(?:(.*?)\n)?[ \t]*```[ \t]*$", re.S | re.M)
#: The citation right after a block: ``(src/x.ts)`` or ``(`src/x.ts`, ...)``.
_CITE_RE = re.compile(r"\s*\(\s*`?(?P<path>[\w./-]+\.\w{1,8})(?=[`),;\s])")
#: A backticked code name: ``fetch()``, ``Client.send``, ``Hono::#handleError``,
#: ``budget(...)``. Group 2 is set when it is written as a call.
_NAME_RE = re.compile(r"`([A-Za-z_$#][\w$#]*(?:(?:\.|::)[A-Za-z_$#][\w$#]*)*)(\([^`)]*\))?`")
#: A plain lowercase word (``scheme``, ``compose``) reads as a field or value
#: unless written as a call or qualified.
_PLAIN_WORD = re.compile(r"[a-z]+")
#: Lines an answer writes in place of code it left out.
_ELISION = frozenset({"...", "…", "// ...", "# ..."})
#: Source lines a quoted block may skip between two of its lines.
_MAX_GAP = 12
#: A block shorter than this (``}``, ``return x``) could stand anywhere.
_MIN_BLOCK_CHARS = 16
#: How far after a block a citation in another form (``see src/x.ts``) is looked for.
_CITE_WINDOW = 120
_MAX_HELPER_CALLS = 3
_CALLABLE_KINDS = ("function", "method")

Reader = Callable[[str], "list[str] | None"]


def _block_span(code: str | None, source: list[str]) -> tuple[int, int] | None:
    """The 1-based ``(first, last)`` source lines a quoted block reproduces, in order.

    None unless the block is long enough to be specific and appears exactly once.
    """
    want = [s for ln in (code or "").splitlines() if (s := ln.strip()) and s not in _ELISION]
    if sum(map(len, want)) < _MIN_BLOCK_CHARS:
        return None
    spans = [
        (start + 1, end + 1)
        for start, line in enumerate(source)
        if line.strip() == want[0] and (end := _follows(want[1:], source, start)) is not None
    ]
    return spans[0] if len(spans) == 1 else None


def _follows(rest: list[str], source: list[str], at: int) -> int | None:
    """Index of the last of *rest* matched in order after *at*, each within the gap."""
    for target in rest:
        stop = min(len(source), at + 2 + _MAX_GAP)
        at = next((i for i in range(at + 1, stop) if source[i].strip() == target), -1)
        if at < 0:
            return None
    return at


def anchor_code_blocks(answer: str, citations: list[str], read: Reader) -> str:
    """*answer* with each quoted block's file citation carrying ``:first-last``.

    A block followed by a ``(path)`` citation is located in that file. An
    uncited block gets a citation only when exactly one cited file holds it.
    """
    out: list[str] = []
    pos = 0
    for m in _FENCE_RE.finditer(answer):
        out.append(answer[pos : m.end()])
        pos = m.end()
        if cite := _CITE_RE.match(answer, pos):
            lines = read(cite["path"]) if cite["path"] in citations else None
            if lines and (span := _block_span(m[1], lines)):
                out.append(f"{answer[pos : cite.end('path')]}:{span[0]}-{span[1]}")
                pos = cite.end("path")
            continue
        if any(p in answer[pos : pos + _CITE_WINDOW] for p in citations):
            continue  # cited in a form the pattern does not read; leave it
        found = [(p, s) for p in citations if (ls := read(p)) and (s := _block_span(m[1], ls))]
        if len(found) == 1:
            path, (first, last) = found[0]
            out.append(f"\n({path}:{first}-{last})")
    out.append(answer[pos:])
    return "".join(out)


def _named(answer: str) -> list[tuple[str | None, str]]:
    """``(qualifier, name)`` per backticked code name in the prose, in first-mention order."""
    seen: dict[tuple[str | None, str], None] = {}
    for token, call in _NAME_RE.findall(_FENCE_RE.sub("", answer)):
        parts = re.split(r"\.|::", token)
        if len(parts) == 1 and not call and _PLAIN_WORD.fullmatch(token):
            continue
        seen.setdefault((parts[-2] if len(parts) > 1 else None, parts[-1]), None)
    return list(seen)


def _pick(rows: list, qualifier: str | None, mentioned: set[str]) -> Any | None:
    """The one definition a name means, or None when the answer does not settle it."""
    if qualifier:
        rows = [r for r in rows if r.parent_name == qualifier]
    elif len(rows) > 1:
        rows = [r for r in rows if r.parent_name in mentioned]
    return rows[0] if len(rows) == 1 else None


def helper_calls(answer: str, rows: list, served: set[tuple[str, str]]) -> list[str]:
    """get_symbol calls for functions *answer* names whose bodies were not served.

    *rows* are the indexed functions and methods of the files the answer rests
    on; a name with several definitions there needs a qualifier, or a parent
    the answer also names, to be picked.
    """
    by_name: dict[str, list] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)
    named = _named(answer)
    mentioned = {name for _, name in named}
    calls: list[str] = []
    for qualifier, name in named:
        row = _pick(by_name.get(name, []), qualifier, mentioned)
        if row is None or (row.file_path, row.name) in served:
            continue
        call = f"get_symbol('{row.symbol_id}')"
        if call not in calls:
            calls.append(call)
        if len(calls) >= _MAX_HELPER_CALLS:
            break
    return calls


def chain_first(calls: list[str], question: str = "") -> list[str]:
    """*calls* led by the chain's entry, which also reads its callees (``depth=2``).

    For an answer that walks a call chain: one call returns the chain, where
    one call per helper costs a round trip each. The entry is the first helper
    the question names (``client.get``), else the first the answer names.
    """
    if not calls:
        return calls
    asked = set(re.findall(r"[A-Za-z_$#][\w$#]*", question))
    entry = next((c for c in calls if re.split(r"::|'", c)[-2] in asked), calls[0])
    return [f"{entry[:-1]}, depth=2)", *(c for c in calls if c != entry)]


def _answer_files(payload: dict[str, Any]) -> list[str]:
    """The files the answer cites, then the files on its flow path."""
    files = [c for c in payload.get("citations") or [] if isinstance(c, str) and "." in c]
    for hop_chain in payload.get("flow_path") or []:
        files += [hop.split("::", 1)[0] for hop in str(hop_chain).split(" -> ") if "::" in hop]
    return list(dict.fromkeys(files))


def _served(payload: dict[str, Any]) -> set[tuple[str, str]]:
    """``(path, name)`` of each body served whole; a cut body still needs its call."""
    return {
        (b.get("path"), b.get("name"))
        for b in payload.get("symbol_bodies") or []
        if isinstance(b, dict) and not b.get("truncated") and "continuation" not in b
    }


def _file_reader(root: Any) -> Reader:
    """A cached live-file line reader for the checkout at *root*."""
    from repowise.server.mcp_server._helpers import read_repo_file_text

    cache: dict[str, list[str] | None] = {}

    def read(path: str) -> list[str] | None:
        if path not in cache:
            text = read_repo_file_text(root, path, allow_extracted=False) if root else None
            cache[path] = text.splitlines() if text is not None else None
        return cache[path]

    return read


async def _callable_rows(ctx: Any, files: list[str], names: set[str]) -> list:
    """The indexed functions and methods called *names* in *files*."""
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server._helpers import _get_repo

    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path.in_(files),
                WikiSymbol.name.in_(names),
                WikiSymbol.kind.in_(_CALLABLE_KINDS),
            )
        )
        return list(res.scalars().all())


async def attach_pointers(
    payload: dict[str, Any], repo: str | None, question: str = "", chain: bool = False
) -> None:
    """Add line ranges to the answer's quoted code and ``next_actions`` for unserved helpers."""
    answer = payload.get("answer")
    files = _answer_files(payload)
    if repo == "all" or payload.get("error") or not isinstance(answer, str) or not files:
        return
    if "`" not in answer:  # no quoted code and no named code
        return
    try:
        from repowise.server.mcp_server._helpers import _resolve_repo_context

        ctx = await _resolve_repo_context(repo)
        if "```" in answer:
            payload["answer"] = anchor_code_blocks(answer, files, _file_reader(getattr(ctx, "path", None)))
        rows = await _callable_rows(ctx, files, {name for _, name in _named(answer)})
    except Exception:  # pointers are a convenience; never fail the answer over one
        _log.debug("get_answer: locate pointers skipped", exc_info=True)
        return
    if calls := helper_calls(answer, rows, _served(payload)):
        payload["next_actions"] = chain_first(calls, question) if chain else calls
