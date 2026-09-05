"""Selecting and inlining the live symbol bodies get_answer serves.

``symbol_bodies`` collapses the get_answer → get_symbol drill: the agent that
asked "how does X work" gets X's body in the same call. Selection is the only
thing here that ever touched synthesis — the bodies themselves are re-read live
off disk at the indexed bounds — which is why the degraded path calls the same
builder with the question's identifiers standing in for the prose.
"""

from __future__ import annotations

from pathlib import Path

from repowise.server.mcp_server._verify import end_anchor_holds, name_at_line
from repowise.server.mcp_server.tool_answer.config import (
    _ENRICH_TOP_N_HITS,
    _INLINE_BODY_MAX_LINES,
    _INLINE_BODY_MAX_SYMBOLS,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    _read_repo_text,
    _read_symbol_source,
    attach_truncation_contract,
)


def _gather_body_candidates(
    hits: list[dict], answer_text: str, *, anchor_names: set[str] | None = None
) -> list[tuple[int, int, int, str, dict]]:
    """Rank the definitions to inline in ``symbol_bodies``, most-relevant first.

    Returns ``(tier, kind_rank, start_line, path, symbol)`` tuples, pre-sorted so
    the leading entries are the bodies the agent is most likely to want:

      * Tier 0 — the exact symbol the question named, resolved by symbol
        anchoring (survives the fuzzy hydration cap a parent class name floods).
      * Tier 1 — a question-matched hydrated symbol the answer names.

    Within a tier a function/method outranks a class container, then document
    order. Only definitions the answer text actually names qualify; constants
    stay in ``quotes``.

    ``anchor_names`` switches tier 0 off the prose and onto the identifiers the
    QUESTION named: the degraded path, where there is no synthesised text to
    match against. Tier 1 is skipped in that mode by construction, since it
    exists to catch a symbol only the prose names.
    """
    candidates: list[tuple[int, int, int, str, dict]] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        path = h.get("target_path")
        if not path:
            continue
        for s in h.get("_anchor_symbols") or []:
            if _selection_names(s.get("name"), answer_text, anchor_names):
                candidates.append((0, _kind_rank(s), s.get("start_line") or 0, path, s))
        # Tier 1 exists to catch a symbol only the PROSE names, so it has
        # nothing to do in the degraded mode that has no prose.
        if anchor_names is not None:
            continue
        for s in h.get("symbols") or []:
            if _is_named_definition(s, answer_text):
                candidates.append((1, _kind_rank(s), s.get("start_line") or 0, path, s))
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    return candidates


def _selection_names(name: str | None, answer_text: str, anchor_names: set[str] | None) -> bool:
    """Whether the text that selects bodies names this symbol.

    Two selection texts, one predicate: normally the synthesised answer decides
    which definitions are worth inlining, but the degraded path has no prose, so
    ``anchor_names`` puts the question's own identifiers in its place. Note the
    difference in kind: ``anchor_names`` is an exact set membership, the answer
    text a substring match.
    """
    if not name:
        return False
    return name in anchor_names if anchor_names is not None else name in answer_text


def _kind_rank(s: dict) -> int:
    """0 for a callable, 1 for a container.

    Within a tier a function or method outranks the class that holds it, so
    "explain the extract_all method of DecisionExtractor" serves extract_all
    rather than the class head.
    """
    return 0 if s.get("kind") in ("function", "method") else 1


def _is_named_definition(s: dict, answer_text: str) -> bool:
    """Whether a hydrated symbol is a definition the answer actually names.

    Requires a name long enough that substring containment means something (a
    1-2 character constant would "appear" in almost any answer), the
    question-match the hydrator recorded, and a kind that has a body worth
    inlining. Constants stay in ``quotes``: their body IS the assignment line.
    """
    name = s.get("name")
    if not name or len(name) < 3 or not s.get("_matched"):
        return False
    if name not in answer_text:
        return False
    return s.get("kind") in ("function", "method", "class", "interface")


def _build_symbol_bodies(
    body_candidates: list[tuple[int, int, int, str, dict]], repo_root: Path | None
) -> tuple[list[dict], bool]:
    """Inline the ranked definitions as live source, with the truncation contract.

    Returns ``(symbol_bodies, served_named_body)``. ``served_named_body`` is True
    once a tier-0 body (the exact symbol the question named) is inlined; the
    confidence gates read it to avoid the "low, go Read" label on a payload that
    already holds the answer.

    Every step here is prose-independent: the body is re-read live from disk at
    the indexed bounds, and when the indexed body outruns the line cap a
    ``continuation`` names the exact range read for the remainder plus the
    ``withheld_symbols`` it covers. That is why the degraded path can call this
    too.

    ``source`` is the live body sliced at the indexed bounds. ``verified: True``
    is set only when the cheap bounds gate holds on the live file (the name is
    still on its definition line and the stored end still closes the body), the
    same gate get_symbol uses; an entry that fails it carries no such key.
    """
    symbol_bodies: list[dict] = []
    seen: set[tuple[str, str]] = set()
    served_named_body = False
    texts: dict[str, str | None] = {}
    for tier, _kind, start, path, s in body_candidates:
        if len(symbol_bodies) >= _INLINE_BODY_MAX_SYMBOLS:
            break
        name = s["name"]
        if (path, name) in seen:
            continue
        sym_end = s.get("end_line") or 0
        # Re-read a fuller body than the synthesis excerpt: this block is for
        # the agent, so a docstring-heavy def shouldn't spend its whole window
        # on docstring and truncate the logic the question asked about. Falls
        # back to the hydrator's excerpt if the re-read fails.
        if path not in texts:
            texts[path] = _read_repo_text(repo_root, path)
        text = texts[path]
        body = (
            _read_symbol_source(
                repo_root, path, start, sym_end, max_lines=_INLINE_BODY_MAX_LINES, text=text
            )
            if text is not None
            else None
        ) or s.get("source_excerpt")
        if not body:
            continue
        verified = False
        if text is not None:
            file_lines = text.splitlines()
            verified = name_at_line(file_lines, name, start) and (
                not sym_end or end_anchor_holds(file_lines, start, sym_end)
            )
        served = body.count("\n") + 1
        end_served = start + served - 1
        sym_end = sym_end or end_served
        entry: dict = {
            "path": path,
            "name": name,
            "lines": [start, end_served],
            "source": body,
        }
        if verified:
            entry["verified"] = True
        attach_truncation_contract(
            entry, indexed_end=sym_end, end_served=end_served, repo_root=repo_root
        )
        symbol_bodies.append(entry)
        seen.add((path, name))
        if tier == 0:
            served_named_body = True
    return symbol_bodies, served_named_body


def build_quotes(hits: list[dict], answer_text: str) -> list[dict]:
    """Line-grounded quotes for the symbols the answer names.

    The verbatim source line(s) the hydrator read live from disk, so an agent can
    publish a cited claim without any verification Read — the quote IS the
    verification. Requires a name long enough that substring containment is
    meaningful: a 1-2 char constant (``T``, ``e``) would "appear" in almost any
    answer and attach an irrelevant quote.
    """
    quotes: list[dict] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        for s in h.get("symbols") or []:
            name = s.get("name")
            if not name or len(name) < 3 or name not in answer_text:
                continue
            src = s.get("source_excerpt") or s.get("signature") or ""
            if not src:
                continue
            quote_lines = src.splitlines()[:3]
            start = s.get("start_line") or 0
            quotes.append(
                {
                    "path": h.get("target_path"),
                    "lines": [start, start + len(quote_lines) - 1],
                    "quote": "\n".join(quote_lines),
                }
            )
            if len(quotes) >= 5:
                break
        if len(quotes) >= 5:
            break
    return quotes
