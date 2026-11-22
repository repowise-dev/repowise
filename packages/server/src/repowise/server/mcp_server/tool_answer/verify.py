"""The one line a get_answer reply gives for checking it: what it rests on, by path:line.

Agents re-check every answer whatever its grade, so the cheap move is to make the
check one read: name the definition the answer rests on and its line, and say
what to do when that line is not it. The line replaces the generic next steps
("refine with search_codebase", "read the top evidence row"), which sent the
agent back to search even when the top file was the answer.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CODE_SPAN_RE = re.compile(r"`([^`\s]+)`")
#: Question framing, never what a symbol is named for.
_FRAME_WORDS = frozenset(
    (
        "the",
        "and",
        "for",
        "are",
        "was",
        "how",
        "what",
        "which",
        "where",
        "when",
        "why",
        "who",
        "does",
        "did",
        "that",
        "this",
        "with",
        "from",
        "into",
        "file",
        "files",
        "function",
        "functions",
        "class",
        "method",
        "code",
        "name",
        "named",
        "called",
        "used",
        "uses",
        "use",
    )
)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class Anchor(NamedTuple):
    path: str
    name: str | None = None
    line: int | None = None
    #: Last line, when the reply serves the definition's source.
    end: int | None = None


def _path_of(row: Any) -> str | None:
    if isinstance(row, str):
        return row.split("::", 1)[0] or None
    if not isinstance(row, dict):
        return None
    path = row.get("file") or row.get("path") or row.get("target_path")
    return path.split("::", 1)[0] if isinstance(path, str) and path else None


def _symbol_rows(payload: dict[str, Any], path: str) -> list[tuple[str, int]]:
    """``(name, line)`` of the definitions the reply lists for *path*, in served order."""
    rows: list[tuple[str, int]] = []
    for key, field, line_key in (
        ("best_guesses", "functions", "line"),
        ("retrieval", "key_symbols", "start_line"),
    ):
        for row in payload.get(key) or []:
            if not isinstance(row, dict) or _path_of(row) != path:
                continue
            for sym in row.get(field) or []:
                if isinstance(sym, dict) and sym.get("name") and isinstance(sym.get(line_key), int):
                    rows.append((sym["name"], sym[line_key]))
    return rows


def _named_in(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")}


def _parts(name: str) -> set[str]:
    """The words of an identifier: ``parse_pytest_summaries`` -> parse, pytest, summaries."""
    spaced = _CAMEL_RE.sub(" ", name.rsplit(".", 1)[-1]).replace("_", " ")
    return {p.lower() for p in spaced.split() if len(p) >= 3}


def _same_word(a: str, b: str) -> bool:
    """Equal, or one inflects the other (parse / parses, summary / summaries)."""
    if a == b:
        return True
    shared = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )
    return shared >= 4 and shared >= min(len(a), len(b)) - 3


def _overlap(name: str, words: set[str]) -> int:
    return sum(any(_same_word(p, w) for w in words) for p in _parts(name))


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower()


def _pick(rows: list[tuple[str, int]], cited: str | None, question: str, answer: str):
    """The definition the reply rests on, or None when no listed one is about the question.

    In order: the cited symbol, one the question names exactly, the one sharing
    the most words with the question, one the answer writes as code. A file's
    first symbol is not a guess worth a line number: the wrong line costs a read.
    """
    asked = _named_in(question)
    for wanted in ({cited.lower()} if cited else set(), asked):
        hit = next((r for r in rows if _short(r[0]) in wanted), None)
        if hit:
            return hit
    words = {w for w in asked if len(w) >= 3} - _FRAME_WORDS
    scored = [(_overlap(r[0], words), -i, r) for i, r in enumerate(rows)]
    best = max(scored, default=None)
    if best and best[0] > 0:
        return best[2]
    coded = {m.rsplit(".", 1)[-1].strip("()").lower() for m in _CODE_SPAN_RE.findall(answer or "")}
    return next((r for r in rows if _short(r[0]) in coded), None)


def _served_anchor(payload: dict[str, Any]) -> Anchor | None:
    """The first served body with a line range: its source is in the reply."""
    for body in payload.get("symbol_bodies") or []:
        lines = body.get("lines") if isinstance(body, dict) else None
        if _path_of(body) and isinstance(lines, list) and len(lines) == 2:
            return Anchor(_path_of(body), body.get("name"), lines[0], lines[1])
    return None


def _lead(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """The lead file and the symbol its citation names, if any.

    The first citation of a written answer, or the top guess of a reply with
    no prose.
    """
    keyless = bool(payload.get("degraded"))
    order = ("best_guesses", "citations") if keyless else ("citations", "best_guesses")
    for key in order:
        first = next((r for r in payload.get(key) or [] if _path_of(r)), None)
        if first is not None:
            cited = first.rsplit("::", 1)[-1] if isinstance(first, str) and "::" in first else None
            return _path_of(first), cited
    return None, None


def answer_anchor(payload: dict[str, Any], question: str) -> Anchor | None:
    """The definition a reply rests on, or None when it names no file.

    A served body is the strongest anchor; otherwise the lead file, with the
    listed symbol the question or answer points at.
    """
    served = _served_anchor(payload)
    if served is not None:
        return served
    lead, cited = _lead(payload)
    if lead is None:
        return None
    hit = _pick(_symbol_rows(payload, lead), cited, question, str(payload.get("answer") or ""))
    if hit:
        return Anchor(lead, hit[0], hit[1])
    return Anchor(lead, cited)


def _where(a: Anchor) -> str:
    at = f"{a.path}:{a.line}" if a.line else a.path
    return f"`{a.name}` at {at}" if a.name else at


def verify_line(anchor: Anchor, confidence: str) -> str:
    """The next step for a reply resting on *anchor*, graded *confidence*."""
    if anchor.end:
        span = f"{anchor.path}:{anchor.line}-{anchor.end}"
        served = f"`{anchor.name}` ({span})" if anchor.name else span
        if confidence == "high":
            return f"Use the answer directly; it rests on {served}, served in symbol_bodies."
        return (
            f"symbol_bodies serves {served} from live source; read further only for "
            "what it does not show."
        )
    if confidence == "high":
        return f"Use the answer directly; it rests on {_where(anchor)}."
    return f"Verify by reading {_where(anchor)}; if that falls short, search_codebase."
