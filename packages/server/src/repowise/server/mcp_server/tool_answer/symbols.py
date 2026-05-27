"""Question identifier extraction + WikiSymbol hydration for retrieval hits.

The pieces that turn a ranked file into LLM-ready symbol context: pull the
identifiers a question names, read real signatures/source from disk, and
promote question-matched symbols to the top of each hit's symbol list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.config import (
    _ENRICH_TOP_N_HITS,
    _MATCHED_SYMBOL_SOURCE_LINES,
    _MAX_RICH_SIG_LINES,
    _MAX_SYMBOLS_PER_HIT,
    _MAX_SYMBOLS_TOP_HIT,
    _STOPWORDS,
)


def _extract_question_identifiers(question: str) -> set[str]:
    """Pull out Python-looking identifiers the question names explicitly.

    Targets: snake_case (``_local_reachability_density``), CamelCase
    (``NearestCentroid``), dotted paths (``BaseLabelPropagation.fit``).
    Filtered to ≥3 chars, non-stopwords, non-pure-lowercase-English (unless
    they contain an underscore or a digit — otherwise every common word
    matches). The result drives question-aware symbol promotion in
    ``_hydrate_symbols_for_hits``.
    """
    import re

    ids: set[str] = set()
    # Match bare identifiers and dotted paths: first char letter/underscore,
    # rest alnum/underscore, optionally with dotted continuations.
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", question):
        # Split dotted paths into both the full thing and the leaf.
        parts = tok.split(".")
        candidates = [tok, *parts]
        for c in candidates:
            if len(c) < 3:
                continue
            if c.lower() in _STOPWORDS:
                continue
            # Heuristic: keep if it contains an uppercase letter anywhere
            # (covers CamelCase and sentence-initial capitalised nouns like
            # ``Version`` that are typically class names in Python), a
            # digit, or an underscore. Pure-lowercase English words like
            # ``method`` / ``class`` / ``dtype`` are dropped — they are
            # poor promotion signals and match too broadly.
            has_upper = any(ch.isupper() for ch in c)
            has_under = "_" in c
            has_digit = any(ch.isdigit() for ch in c)
            if has_upper or has_under or has_digit:
                ids.add(c)
    return ids


def _read_symbol_source(
    repo_root: Path | None,
    file_path: str,
    start_line: int,
    end_line: int,
    max_lines: int = _MATCHED_SYMBOL_SOURCE_LINES,
) -> str | None:
    """Return the literal source body for a symbol, bounded to max_lines.

    The bounded source is the key ingredient for question-matched symbols.
    The LLM was already getting the file-level summary and a truncated
    docstring; what it was missing was the actual code. With 40 lines of
    the method body in front of it, the synthesis step can answer "how
    does X work" without hedging back to "you should inspect the source".
    """
    if repo_root is None or start_line < 1:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if start_line > len(lines):
        return None
    hi = end_line if end_line and end_line >= start_line else start_line + max_lines
    hi = min(hi, start_line + max_lines, len(lines))
    body = "\n".join(lines[start_line - 1 : hi])
    return body


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    None on any failure — caller falls back to the stored signature.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        # Defense in depth: never read outside the repo root.
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return None
    # Walk forward up to _MAX_RICH_SIG_LINES until we close the parenthesis
    # group (Python signatures often span multiple lines for type hints).
    sig_lines: list[str] = []
    paren_depth = 0
    for i in range(start_line - 1, min(start_line - 1 + _MAX_RICH_SIG_LINES, len(lines))):
        line = lines[i]
        sig_lines.append(line.strip())
        paren_depth += line.count("(") - line.count(")")
        if line.rstrip().endswith(":") and paren_depth <= 0:
            break
    if not sig_lines:
        return None
    return " ".join(sig_lines)


async def _hydrate_symbols_for_hits(
    session,
    repo_id: str,
    hits: list[dict],
    ctx: Any = None,
    question_ids: set[str] | None = None,
) -> None:
    """Mutate `hits` in place: attach `symbols` list to top-N file_page hits.

    Question-aware promotion: if ``question_ids`` contains identifiers that
    match symbols in the retrieved files, those symbols move to the top of
    their file's symbol list, carry a longer docstring, and get a source
    excerpt (``source_excerpt``). This is the difference between the LLM
    seeing ``class LocalOutlierFactor`` at the file top (and hedging on a
    question about ``_local_reachability_density``) vs. seeing the actual
    method body and answering it.

    Top hit gets ``_MAX_SYMBOLS_TOP_HIT`` slots; secondaries get the smaller
    ``_MAX_SYMBOLS_PER_HIT``. Symbols not matching a question id carry the
    short 120-char docstring; matched symbols carry 400 chars + source body.
    """
    question_ids = question_ids or set()
    # Case-folded copy for matching.
    qids_lower = {q.lower() for q in question_ids}

    # Identify the top file_page hits in retrieval-rank order. `hits` is
    # already sorted by descending score upstream.
    enrich_paths: list[str] = []
    for h in hits:
        if (
            h.get("target_path")
            and h.get("page_type") == "file_page"
            and len(enrich_paths) < _ENRICH_TOP_N_HITS
        ):
            enrich_paths.append(h["target_path"])
    if not enrich_paths:
        return

    res = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(enrich_paths),
        )
        .order_by(WikiSymbol.file_path, WikiSymbol.start_line)
    )
    by_file: dict[str, list[dict]] = {}
    repo_root = Path(str(ctx.path)) if ctx and ctx.path else None
    for row in res.scalars().all():
        rich_sig = _read_signature_from_source(repo_root, row.file_path, row.start_line)
        # Does the symbol name match any identifier from the question?
        name_lower = (row.name or "").lower()
        qname_lower = (row.qualified_name or "").lower()
        matched = bool(
            qids_lower
            and (
                name_lower in qids_lower
                or qname_lower in qids_lower
                or any(
                    q in name_lower or q in qname_lower
                    for q in qids_lower
                    if len(q) >= 5  # avoid spurious substring matches on short tokens
                )
            )
        )
        entry: dict[str, Any] = {
            "name": row.name,
            "kind": row.kind,
            "signature": rich_sig or row.signature,
            "docstring": row.docstring or "",
            "start_line": row.start_line,
            "end_line": row.end_line,
            "_matched": matched,
        }
        if matched:
            src = _read_symbol_source(repo_root, row.file_path, row.start_line, row.end_line)
            if src:
                entry["source_excerpt"] = src
        by_file.setdefault(row.file_path, []).append(entry)

    # Sort: matched symbols first (document order within the match group),
    # then unmatched in start_line order. Cap per file — top hit gets more
    # slots than secondary hits.
    for i, h in enumerate(hits):
        path = h.get("target_path")
        if path not in by_file:
            continue
        syms = by_file[path]
        syms.sort(key=lambda s: (not s["_matched"], s["start_line"]))
        cap = _MAX_SYMBOLS_TOP_HIT if i == 0 else _MAX_SYMBOLS_PER_HIT
        # Guarantee at least one matched symbol survives the cap, even if
        # the file has more than `cap` symbols before it.
        kept: list[dict] = [s for s in syms if s["_matched"]][:cap]
        for s in syms:
            if s in kept:
                continue
            if len(kept) >= cap:
                break
            kept.append(s)
        # Sort final slice by start_line for natural reading order.
        kept.sort(key=lambda s: s["start_line"])
        h["symbols"] = kept
