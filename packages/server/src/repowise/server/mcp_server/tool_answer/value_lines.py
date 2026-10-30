"""Value lines: the line inside a function body that sets the value a question asks for.

"What is the timeout for the FTS leg" is answered by ``wait_for(ctx.fts.search(...),
timeout=5.0)``, a keyword argument in a body. No page renders it and no symbol is
named after it, so retrieval never reaches it. This stage greps code lines that
assign a number to a name carrying the question's value word (``timeout=``,
``MAX_RETRIES =``), keeps the line whose own words and enclosing symbol name
carry the most of the question's other words, and anchors that symbol (the
function around the line, or the constant it defines) so it reaches synthesis.

A question naming code (``in get_answer``) means that code and what it imports
directly, so a line outside that reach is not its answer.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.analysis.decisions.rationale_comments import CODE_EXTENSIONS
from repowise.core.persistence.models import WikiSymbol
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._code_rationale import _salient_numbers
from repowise.server.mcp_server._flow_path import file_adjacency
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server.tool_answer.confidence import _is_value_question
from repowise.server.mcp_server.tool_answer.hydration import _stem_hit, _text_stems
from repowise.server.mcp_server.tool_answer.symbols import _anchor_chosen_symbols, def_verifier

# Words a code name carries when it holds a tunable number.
_VALUE_WORDS = frozenset(
    {
        "timeout", "limit", "max", "min", "cap", "threshold", "ttl", "retries", "retry",
        "concurrency", "interval", "delay", "budget", "floor", "depth", "port", "workers",
    }
)
# Question words that say a value is wanted, never which one.
_SHAPE_WORDS = frozenset({"default", "value", "many", "much", "long", "large", "big", "set"})
_GLOBS = [f"*.{ext}" for ext in sorted(CODE_EXTENSIONS - {"toml", "yaml", "yml", "sql"})]
_GREP_SECONDS = 5.0
_MAX_LINES = 4000
# A line must carry this many of the question's other words, counting its
# enclosing function's name: one shared word is a coincidence in a large repo.
_MIN_EVIDENCE = 2
# A module-level constant holds its own line; a function holds a line in its body.
_HOLDER_KINDS = ("function", "method", "constant", "variable")
# More winners than this tie at the top score: the question does not pick one.
_MAX_WINNERS = 2


class _Line(NamedTuple):
    path: str
    line: int
    text: str


def _value_words(question: str) -> list[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", question)}
    return sorted(words & _VALUE_WORDS)


def _grep_value_lines(root: Path, words: list[str]) -> list[_Line]:
    """Code lines assigning a number to a name holding one of *words*."""
    alt = "|".join(words)
    pattern = rf"[A-Za-z_]*({alt})[A-Za-z_]*['\"]?[[:space:]]*[:=][[:space:]]*-?[0-9]"
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "grep", "--no-color", "-n", "-I", "-i", "-E", pattern, "--", *_GLOBS],
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            # The MCP server's stdin is the protocol stream: never let git read it.
            stdin=subprocess.DEVNULL,
            timeout=_GREP_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out: list[_Line] = []
    for raw in proc.stdout.splitlines()[:_MAX_LINES]:
        path, _, rest = raw.partition(":")
        num, _, text = rest.partition(":")
        if num.isdigit() and not is_test_related_path(path):
            out.append(_Line(path, int(num), text))
    return out


def _evidence(nouns: list[str], text: str) -> set[str]:
    stems = _text_stems(text)
    return {n for n in nouns if _stem_hit(n, stems)}


async def _reach(session, repo_id: str, question_ids: set[str]) -> set[str] | None:
    """Files the question's named code lives in plus their direct neighbours, or None."""
    if not question_ids:
        return None
    res = await session.execute(
        select(WikiSymbol.file_path).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.in_(list(question_ids)),
            WikiSymbol.kind.in_(("function", "method", "class")),
        )
    )
    owners = {p for (p,) in res.all() if p and not is_test_related_path(p)}
    if not owners:
        return None
    adj = await file_adjacency(session, repo_id)
    return owners | {n for o in owners for n in adj.get(o, ()) if not is_test_related_path(n)}


async def _holders(session, repo_id: str, lines: list[_Line]) -> dict[_Line, Any]:
    """The innermost function, method or constant holding each line."""
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_({ln.path for ln in lines}),
            WikiSymbol.kind.in_(_HOLDER_KINDS),
        )
    )
    defs = [d for d in res.scalars().all() if d.start_line and d.end_line]
    out: dict[_Line, Any] = {}
    for ln in lines:
        spans = [d for d in defs if d.file_path == ln.path and d.start_line <= ln.line <= d.end_line]
        if spans:
            out[ln] = max(spans, key=lambda d: d.start_line)
    return out


def _winners(scored: list[tuple[int, int, _Line, Any]]) -> list[Any]:
    """The holders of the top-scoring lines, one per file, or [] when too many tie."""
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], -t[1], t[2].path, t[2].line))
    best = scored[0][:2]
    top = [t for t in scored if t[:2] == best]
    files = {t[2].path for t in top}
    if len(files) > _MAX_WINNERS:
        return []
    seen: set[str] = set()
    out = []
    for _, _, ln, holder in top:
        if ln.path not in seen:
            seen.add(ln.path)
            out.append(holder)
    return out


async def value_line_anchor_hits(
    ctx: Any, session, repo_id: str, question: str, question_ids: set[str], hits: list[dict]
) -> list[dict]:
    """Anchor the function whose body sets the value the question asks for.

    Fires on a value question that names a value word and no number (a number
    is :func:`_concept_anchor_hits`'s job). Returns *hits* re-sorted, untouched
    when no line carries enough of the question.
    """
    root = getattr(ctx, "path", None)
    words = _value_words(question)
    if root is None or not words or not _is_value_question(question) or _salient_numbers(question):
        return hits
    nouns = [
        t for t in content_terms(question, max_terms=32)
        if t not in _VALUE_WORDS and t not in _SHAPE_WORDS and "_" not in t
    ]
    if not nouns:
        return hits
    lines = await asyncio.to_thread(_grep_value_lines, Path(str(root)), words)
    reach = await _reach(session, repo_id, question_ids)
    if reach is not None:
        lines = [ln for ln in lines if ln.path in reach]
    if not lines:
        return hits
    holders = await _holders(session, repo_id, lines)
    scored = []
    for ln, holder in holders.items():
        found = _evidence(nouns, ln.text) | _evidence(nouns, holder.name or "")
        if len(found) >= _MIN_EVIDENCE:
            scored.append((len(found), len(_evidence(nouns, ln.path)), ln, holder))
    chosen = _winners(scored)
    if not chosen:
        return hits
    verified = def_verifier(ctx.session_factory, Path(str(root)))
    await _anchor_chosen_symbols(hits, chosen, verified)
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits
