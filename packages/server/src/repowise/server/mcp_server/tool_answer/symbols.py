"""Question identifier extraction + symbol anchoring for retrieval hits.

Pull the identifiers a question names, anchor the files that define them, and
inline the union of a homonym's bodies. Hydration, live source reads, masking
and the withheld-definition scan live in sibling modules and are re-exported
here, since callers and tests import them from this module.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._line_hits import production_lines
from repowise.server.mcp_server._query_shape import (
    _WORD_CHAIN_RE,
    _folds_case,
    _identifier_shaped,
    _looks_like_code_name,
)
from repowise.server.mcp_server._query_terms import split_humps
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _ANCHOR_KINDS,
    _ANCHOR_TOPICAL_FILES,
    _ENRICH_TOP_N_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _HOMONYM_UNION_BODY_MAX_LINES,
    _HOMONYM_UNION_CHAR_BUDGET,
    _HOMONYM_UNION_PROSE_DEF_CEILING,
    _INLINE_BODY_MAX_LINES,
    _INLINE_BODY_MAX_SYMBOLS,
    _LITERAL_SCAN_MAX_TOKENS,
    _SPECIFIC_TOKEN_MAX_FILES,
    _STOPWORDS,
)
from repowise.server.mcp_server.tool_answer.hydration import (  # noqa: F401  re-exported
    _DEFINE_KIND_RANK,
    _STEM_SUFFIXES,
    _hydrate_candidate_defines,
    _hydrate_symbols_for_hits,
    _question_names_symbol,
    _stem,
    _stem_hit,
    _symbol_relevance,
    _text_stems,
)
from repowise.server.mcp_server.tool_answer.source import (  # noqa: F401  re-exported
    _SIG_TERMINATOR_RE,
    _read_repo_text,
    _read_signature_from_source,
    _read_symbol_source,
)
from repowise.server.mcp_server.tool_answer.string_mask import (  # noqa: F401  re-exported
    _BACKTICK_STRING_SUFFIXES,
    _QUOTEISH_RE,
    _REGEX_CAN_START_AFTER,
    _REGEX_CAN_START_AFTER_WORD,
    _has_backtick_strings,
    _Masked,
    _regex_position,
    _skip_quoted,
    _skip_regex,
    _string_masked_lines,
    _walk_string_state,
)
from repowise.server.mcp_server.tool_answer.withheld import (  # noqa: F401  re-exported
    _BRACE_MEMBER,
    _DECL_MODIFIERS,
    _FIRST_WORD_RE,
    _NOT_A_DEFINITION,
    _RESERVED_NAMES,
    _UNBOUNDED_INDENT,
    _WITHHELD_DEF_PATTERNS,
    _WITHHELD_MAX_SYMBOLS,
    _indent_width,
    _match_definition,
    withheld_definitions,
)
from repowise.server.mcp_server.tool_search import _is_test_query, _prose_dominates


def _extract_question_identifiers(question: str) -> set[str]:
    """Pull out Python-looking identifiers the question names explicitly.

    snake_case, CamelCase and dotted paths, at least 3 chars and not
    stopwords. Drives question-aware promotion in ``_hydrate_symbols_for_hits``.
    """
    ids: set[str] = set()
    for tok in _WORD_CHAIN_RE.findall(question):
        # A dotted path yields both the full thing and each part.
        for c in (tok, *tok.split(".")):
            if c.lower() not in _STOPWORDS and _identifier_shaped(c):
                ids.add(c)
    return ids


def union_defers_to_synthesis(
    question: str, question_ids: set[str], union_groups: dict
) -> bool:
    """True when an answer-by-union should fall through to synthesis.

    A union answers a small set of parallel implementations, not a prose
    question that merely mentions a generic many-def method. Both must hold:

    * ``_prose_dominates``: a bare symbol lookup still unions.
    * The def count exceeds ``_HOMONYM_UNION_PROSE_DEF_CEILING``.
    """
    if not union_groups:
        return False
    total_defs = sum(len(defs) for defs in union_groups.values())
    if total_defs <= _HOMONYM_UNION_PROSE_DEF_CEILING:
        return False
    return _prose_dominates(question, list(question_ids))


def is_symbol_lookup_question(question: str, question_ids: set[str]) -> bool:
    """True when the question IS the symbol names, not prose that mentions them.

    For a lookup the served body is the answer, so truncating it is a loss on
    its own; for prose the body is evidence for a claim.

    Stricter than ``_prose_dominates``, which counts ASCII tokens and so reads
    non-Latin-script questions and identifier-dense English as bare lookups.
    Removing the identifiers and checking for any surviving word character is
    script-independent.
    """
    if not question_ids:
        return False
    residual = question
    for ident in sorted(question_ids, key=len, reverse=True):
        residual = residual.replace(ident, " ")
    if re.search(r"\w", residual, re.UNICODE):
        return False
    return not _prose_dominates(question, list(question_ids))


def _value_entry(s: dict, path: str) -> dict | None:
    """The extractable assignment for one hydrated symbol, or None."""
    if not s.get("_matched") or s.get("kind") not in ("constant", "variable"):
        return None
    sig = s.get("signature") or ""
    if "=" not in sig:
        return None
    entry = {
        "name": s.get("name"),
        "signature": sig,
        "file": path,
        "line": s.get("start_line"),
        "answer": f"{sig}  ({path}:{s.get('start_line')})",
    }
    # Multi-line values: include the live body the hydrator attached.
    excerpt = s.get("source_excerpt")
    if excerpt and excerpt.strip() != sig.strip():
        entry["value_source"] = excerpt
    return entry


def _extract_value_answer(
    hits: list[dict], question_ids: set[str], question: str
) -> dict | None:
    """Verbatim-assignment answer for value-shaped questions (the C1 fast path).

    A matched constant/variable's signature is the verbatim assignment from
    live source, so it is the answer, with no LLM call. An exact name match
    wins. A name that only contains a question identifier (``Symbol`` inside
    ``SymbolKind``) is a coincidence unless the question's words spell every
    part of it; otherwise None, so synthesis and its gates answer instead.
    """
    qids_lower = {q.lower() for q in question_ids}
    question_stems = _text_stems(question)
    question_parts = _name_parts(question)
    spelled: dict | None = None
    for h in hits[:_ENRICH_TOP_N_HITS]:
        path = h.get("target_path")
        for s in (h.get("symbols") or []) if path else []:
            entry = _value_entry(s, path)
            if entry is None:
                continue
            name = entry["name"] or ""
            if name.lower() in qids_lower:
                entry["exact_name"] = True
                return entry
            if spelled is None and _question_spells(name, question_stems, question_parts):
                spelled = entry
    return spelled


def _name_parts(text: str) -> set[str]:
    """Lowercased hump- and separator-split parts of *text*, short ones included."""
    return {p.lower() for p in re.split(r"[^A-Za-z0-9]+", split_humps(text)) if p}


def _question_spells(name: str, question_stems: set[str], question_parts: set[str]) -> bool:
    """Whether the question's words cover every part of *name*."""
    name_stems = _text_stems(name)
    if not name_stems or not all(_stem_hit(t, question_stems) for t in name_stems):
        return False
    # _text_stems drops parts under 3 chars, so `V2` in `MAX_V2` must appear as is.
    return all(p in question_parts for p in _name_parts(name) if len(p) < 3)


def _symbol_def_dict(sym) -> dict:
    """Plain-dict view of a WikiSymbol def (decouples answer.py from the ORM)."""
    return {
        "name": sym.name,
        "kind": sym.kind,
        "file_path": sym.file_path,
        "start_line": sym.start_line,
        "end_line": sym.end_line,
        "qualified_name": sym.qualified_name,
        "parent_name": sym.parent_name,
    }


async def _anchor_symbol_hits(
    session,
    repo_id: str,
    question_ids: set[str],
    hits: list[dict],
    repo_root: Path | None = None,
    session_factory: Any = None,
    question: str = "",
) -> tuple[list[dict], dict[str, Any]]:
    """Inject the defining file of a question-named indexed symbol into hits.

    Fuzzy retrieval can miss a deep-path file even when the named symbol is
    indexed. A question identifier resolving to one indexed def makes its file
    the dominant hit, so the answer grounds in the definition.

    Homonyms (several defs of one name) split three ways:

    * The question narrows it to exactly one def: anchor that def.
    * The question does not qualify the name: the defs go to
      ``homonyms["union"]`` so the caller inlines the union of bodies, which
      fuzzy retrieval would never surface.
    * The question qualifies the name (``Parent.leaf``) but no def matches:
      ``homonyms["qualified_miss"]``, so the caller answers not-found rather
      than from a same-named symbol elsewhere.

    Returns ``(hits, homonyms)``; ``hits`` is re-sorted by score (mutated in
    place). ``homonyms = {"union": {name: [def_dict, ...]}, "qualified_miss":
    [name, ...]}``.
    """
    homonyms: dict[str, Any] = {"union": {}, "qualified_miss": []}
    if not question_ids:
        return hits, homonyms
    qids_lower = {q.lower() for q in question_ids}
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.in_(list(question_ids)),
        )
    )
    by_name: dict[str, list] = {}
    indexed_names: set[str] = set()
    for row in res.scalars().all():
        indexed_names.add(row.name)
        # A pathless row names nothing the reply can point at: drop it from
        # every route below.
        if not row.file_path or row.kind not in _ANCHOR_KINDS:
            continue
        by_name.setdefault(row.name, []).append(row)
    by_name = {name: _without_constructors(cands) for name, cands in by_name.items()}

    # Verify bounds before slicing: union and anchored bodies are served at
    # high trust, so a drifted row would ground them in the wrong lines.
    # One live read per file, cached.
    _text_cache: dict[str, str | None] = {}

    async def _verified_dict(row) -> dict:
        d = _symbol_def_dict(row)
        if row.file_path not in _text_cache:
            _text_cache[row.file_path] = _read_repo_text(repo_root, row.file_path)
        text = _text_cache[row.file_path]
        if text is None:
            d["_approx"] = True
            return d
        check = await verify_and_heal(session_factory, row, text)
        d["start_line"], d["end_line"] = check.start_line, check.end_line
        if not check.verified:
            d["_approx"] = True
        return d

    literal: dict[str, list] = {}
    prose = bool(question) and not is_symbol_lookup_question(question, question_ids)
    if prose and repo_root is not None:
        guard = _TopicGuard(repo_root, hits, question, qids_lower)
        by_name = await _trusted_over_topic(by_name, question_ids, guard)
        literal = await _literal_holders(session, repo_id, question_ids - indexed_names, guard)

    chosen = await _choose_anchor_symbols(by_name, question_ids, _verified_dict, homonyms)
    if not chosen and not literal:
        return hits, homonyms
    await _anchor_chosen_symbols(hits, chosen, _verified_dict, prose=prose)
    for token, rows in literal.items():
        await _anchor_chosen_symbols(hits, rows, _verified_dict, named_by=token)
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits, homonyms


class _TopicGuard:
    """Whether an identifier the question writes may anchor a def over the topic.

    An exact name alone is not enough on a prose question: ``module_path`` can
    name one def in a provider spec and a field in fifty files, and a test
    fixture shares its name with the local it stands in for. A def is trusted
    when its file is among the top retrieved files, when few production files
    write the identifier (it names one thing), or when its file name or owner
    shares a word with the rest of the question. Off the top files, a test def
    is trusted only by a question about tests.
    """

    def __init__(self, root: Path, hits: list[dict], question: str, qids_lower: set[str]):
        self.root = root
        self.topical = {
            h["target_path"] for h in hits[:_ANCHOR_TOPICAL_FILES] if h.get("target_path")
        }
        named = set().union(*(_text_stems(q) for q in qids_lower))
        self.topic_stems = _text_stems(question) - named
        self.about_tests = _is_test_query(question)
        self._lines: dict[str, list[dict]] = {}

    async def lines(self, token: str) -> list[dict]:
        """Production lines writing *token*, scanned at most once per question."""
        if token not in self._lines:
            self._lines[token] = await asyncio.to_thread(production_lines, self.root, token)
        return self._lines[token]

    async def trusts(self, token: str, row) -> bool:
        # A plain capitalised word (``Gson``, ``Client``) is also prose, often the
        # project's own name, so its file ranking high proves nothing.
        if row.file_path in self.topical and _folds_case(token):
            return True
        # Before the count: a name only tests write counts zero production files.
        if is_test_related_path(row.file_path) and not self.about_tests:
            return False
        files = len({r["path"] for r in await self.lines(token)})
        if (files or self.about_tests) and files <= _SPECIFIC_TOKEN_MAX_FILES:
            return True
        stem = row.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        owner = f"{stem} {row.parent_name or ''} {row.name or ''}"
        return bool(self.topic_stems & _text_stems(owner))


async def _trusted_over_topic(
    by_name: dict[str, list], question_ids: set[str], guard: _TopicGuard
) -> dict[str, list]:
    """*by_name* without the defs an exact name may not anchor (see :class:`_TopicGuard`).

    A def whose parent the question names is the one it means, so it stays.
    """
    kept: dict[str, list] = {}
    for name, cands in by_name.items():
        trusted = [
            c
            for c in cands
            if _question_names_parent(c, question_ids) or await guard.trusts(name, c)
        ]
        if trusted:
            kept[name] = trusted
    return kept


async def _literal_holders(
    session, repo_id: str, tokens: set[str], guard: _TopicGuard
) -> dict[str, list]:
    """The functions holding an identifier the index does not define, by identifier.

    A string literal or a key (``"retry_after"``) lives in a function body, so
    no page and no symbol name carries it. A holder must pass the same guard
    as a named def; holders in top retrieved files rank first, then by how
    many of the lines they hold, at most ``_INLINE_BODY_MAX_SYMBOLS`` overall.
    """
    # Code-shaped only: ``Authorization`` or ``JVMs`` is a word prose writes too.
    plain = sorted(
        (t for t in tokens if "." not in t and _looks_like_code_name(t)),
        key=lambda t: (-len(t), t),
    )
    lines = {t: rows for t in plain[:_LITERAL_SCAN_MAX_TOKENS] if (rows := await guard.lines(t))}
    if not lines:
        return {}
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_({r["path"] for rows in lines.values() for r in rows}),
            WikiSymbol.kind.in_(("function", "method")),
        )
    )
    defs = [d for d in res.scalars().all() if d.start_line and d.end_line]
    found: list[tuple[tuple, str, Any]] = []
    for token, rows in lines.items():
        for d, n in _innermost_holders(defs, rows).items():
            if await guard.trusts(token, d):
                key = (d.file_path not in guard.topical, -n, d.file_path, d.start_line)
                found.append((key, token, d))
    found.sort(key=lambda t: t[0])
    out: dict[str, list] = {}
    for _key, token, d in found[:_INLINE_BODY_MAX_SYMBOLS]:
        out.setdefault(token, []).append(d)
    return out


def _innermost_holders(defs: list, rows: list[dict]) -> Counter:
    """How many of *rows* each def holds, counting a line for its innermost def only."""
    # Comment lines count on purpose: httpx writes NO_PROXY only in its holder's comments.
    held: Counter = Counter()
    for r in rows:
        spans = [
            d for d in defs if d.file_path == r["path"] and d.start_line <= r["line"] <= d.end_line
        ]
        if spans:
            held[max(spans, key=lambda d: d.start_line)] += 1
    return held


def _without_constructors(cands: list) -> list:
    """*cands* without the constructors of a class that is itself among them.

    A constructor shares its class's name, so a question writing ``Gson`` would
    otherwise get the class's overloaded constructors as homonyms of each other,
    each one "named by its parent". The name means the class.
    """
    classes = {(c.file_path, c.name) for c in cands if c.kind in ("class", "interface")}
    return [
        c
        for c in cands
        if not (
            (c.file_path, c.name) in classes
            and (c.parent_name or "").rsplit(".", 1)[-1] == c.name
        )
    ]


def _question_names_parent(cand, question_ids: set[str]) -> bool:
    """Whether the question names this def's parent, or a qualifier of it.

    A qualifier must be written as code: a plain word (``Java``) also spells a
    directory of the qualified name.
    """
    if (cand.parent_name or "").lower() in {q.lower() for q in question_ids}:
        return True
    qualified = (cand.qualified_name or "").lower()
    name = (cand.name or "").lower()
    code = {q.lower() for q in question_ids if "." in q or _folds_case(q)}
    return any(q in qualified for q in code if len(q) >= 4 and q != name)


async def _choose_anchor_symbols(
    by_name: dict[str, list],
    question_ids: set[str],
    verified_dict: Callable[[Any], Awaitable[dict]],
    homonyms: dict[str, Any],
) -> list:
    """The one def per question-named symbol to anchor on.

    Names with several defs that cannot be narrowed to one are recorded in
    *homonyms* instead, as a union or a qualified miss.
    """
    # Qualifiers the question used (dotted forms like ``decisionextractor.extract_all``).
    qualifiers = {q.lower() for q in question_ids if "." in q}
    chosen: list = []
    for name, cands in by_name.items():
        if len(cands) == 1:
            chosen.append(cands[0])
            continue
        # Disambiguate a homonym when the question names its parent or the
        # parent appears in the qualified name.
        narrowed = [c for c in cands if _question_names_parent(c, question_ids)]
        if len(narrowed) == 1:
            chosen.append(narrowed[0])
            continue
        # Can't narrow to exactly one. Decide union vs qualified-miss.
        leaf = (name or "").lower()
        targeted = any(q.rsplit(".", 1)[-1] == leaf and q != leaf for q in qualifiers)
        if narrowed:
            # Qualifier matched >1 def: union of the narrowed set (still all
            # genuine candidates for the qualified name).
            homonyms["union"][name] = [await verified_dict(c) for c in narrowed]
        elif targeted:
            # Qualifier present but matched nothing: do not guess.
            homonyms["qualified_miss"].append(name)
        else:
            # Bare homonym, no qualifier: union of every def.
            homonyms["union"][name] = [await verified_dict(c) for c in cands]
    return chosen


async def _anchor_chosen_symbols(
    hits: list[dict],
    chosen: list,
    verified_dict: Callable[[Any], Awaitable[dict]],
    named_by: str | None = None,
    prose: bool = False,
) -> None:
    """Boost (or insert) each chosen def's file and stash the def on it.

    ``named_by`` is the identifier the question wrote when the def holds it
    rather than being named by it. On a ``prose`` question a class the inline
    cap would cut is not stashed: its head answers no "how does X do Y", and
    its file still leads.
    """
    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top: an exact symbol match is stronger than prose.
    anchor_score = max(top_score + 2.0, _HIGH_CONFIDENCE_SCORE_FLOOR + 1.0)
    for sym in chosen:
        fp = sym.file_path
        if not fp:
            # A pathless anchor would take rank 1 as an empty row.
            continue
        target = _boost_or_insert_file_hit(hits, by_path, fp, anchor_score)
        target["_symbol_anchored"] = True
        # Stash the named symbol so symbol_bodies serves it even when the
        # hydration cap would drop it. Verified bounds only; an approximate
        # symbol still boosts its file.
        vd = await verified_dict(sym)
        if vd.get("_approx"):
            continue
        # The cap's own test: ``_read_symbol_source`` cuts past start + max_lines.
        if (
            prose
            and sym.kind in ("class", "interface")
            and vd["end_line"] > vd["start_line"] + _INLINE_BODY_MAX_LINES
        ):
            continue
        target.setdefault("_anchor_symbols", []).append(
            {
                "name": sym.name,
                "kind": sym.kind,
                "parent_name": sym.parent_name,
                "start_line": vd["start_line"],
                "end_line": vd["end_line"],
                **({"_named_by": named_by} if named_by else {}),
            }
        )


def lead_with_files(hits: list[dict], paths: list[str]) -> list[dict]:
    """``hits`` led by ``paths`` in their order, each scored above every other
    hit; a path no hit carries is inserted as its file page."""
    by_path = {h.get("target_path"): h for h in hits}
    top = max((h.get("score", 0.0) for h in hits), default=0.0)
    base = max(top + 2.0, _HIGH_CONFIDENCE_SCORE_FLOOR + 1.0)
    led = [
        _boost_or_insert_file_hit(hits, by_path, path, base + len(paths) - i)
        for i, path in enumerate(paths)
    ]
    led_ids = {id(h) for h in led}
    return led + [h for h in hits if id(h) not in led_ids]


# "outside foo.py", "other than src/legacy/": a file or directory the question
# rules out. Only a name with an extension or a slash counts, so prose such as
# "outside the loop" rules out nothing.
_RULED_OUT_RE = re.compile(
    r"\b(?:outside(?:\s+of)?|other\s+than|except(?:\s+(?:for|in))?|excluding|besides|"
    r"apart\s+from)\s+(?:the\s+)?[`'\"]?((?:[\w.-]+/)*[\w-]+\.\w+(?!\.?\w)|(?:[\w.-]+/)+)",
    re.IGNORECASE,
)


def drop_ruled_out_files(hits: list[dict], question: str) -> list[dict]:
    """``hits`` without the files the question explicitly rules out.

    "Which code parses X outside foo.py" asks for everything but foo.py, which
    is also the file its words match best.
    """
    ruled = [m.lower() for m in _RULED_OUT_RE.findall(question)]
    if not ruled:
        return hits

    # Matched as a path suffix, so "pkg/utils.py" rules out only that file. A bare
    # "utils.py" rules out every utils.py: the question gives nothing to tell
    # them apart (ceiling; a ranked pick would need the question's other words).
    def out(path: str) -> bool:
        p = "/" + path.split("::", 1)[0].lower()
        return any(p.endswith("/" + r) if not r.endswith("/") else "/" + r in p for r in ruled)

    return [h for h in hits if not out(h.get("target_path") or "")]


# A file defining a named identifier is a milder signal than a trace frame: it
# joins just below the top three, and stays put when it already ranks there.
_NAMED_FILE_SLOT = 3


def place_named_files(hits: list[dict], paths: list[str]) -> list[dict]:
    """``hits`` with each of ``paths`` outside the top three moved (or, when no
    hit carries it, inserted as its file page) to just below them, in order,
    scored level with the hit above."""
    out = list(hits)
    slot = _NAMED_FILE_SLOT
    for path in paths:
        idx = next((i for i, h in enumerate(out) if h.get("target_path") == path), None)
        if idx is not None and idx < slot:
            continue
        hit = out.pop(idx) if idx is not None else _file_page_hit(path, 0.0)
        pos = min(slot, len(out))
        if pos:
            hit["score"] = out[pos - 1].get("score", 0.0)
        out.insert(pos, hit)
        slot = pos + 1
    return out


def _file_page_hit(path: str, score: float) -> dict:
    return {
        "page_id": f"file_page:{path}",
        "target_path": path,
        "title": path,
        "summary": "",
        "snippet": "",
        "page_type": "file_page",
        "score": score,
    }


def _boost_or_insert_file_hit(
    hits: list[dict], by_path: dict, path: str, score: float
) -> dict:
    """The hit for *path* raised to at least *score*, or a new one at the front.

    Shared by the symbol and concept anchors, which differ only in the flags
    they set on the returned hit.
    """
    target = by_path.get(path)
    if target is None:
        target = _file_page_hit(path, score)
        hits.insert(0, target)
        by_path[path] = target
    else:
        target["score"] = max(target.get("score", 0.0), score)
    return target


def attach_truncation_contract(
    entry: dict, *, indexed_end: int, end_served: int, repo_root: Path | None
) -> None:
    """Mark an inlined body that was cut, and name what the cut withheld.

    Every site that inlines a body owes the same keys when the indexed body
    outruns what was served: ``truncated``, a ``continuation`` range, and the
    ``withheld_symbols`` in it, so the consumer knows what it is missing. One
    function, because the confidence cascade reads these keys and two copies
    would drift.

    A falsy ``indexed_end`` means the index recorded no end, which is never a
    cut. ``indexed_end`` is trusted to lie within the live file:
    ``check_symbol_bounds`` owns that clamp.
    """
    if indexed_end and indexed_end > end_served:
        entry["truncated"] = True
        entry["continuation"] = f"{entry['path']}:{end_served + 1}-{indexed_end}"
        withheld = withheld_definitions(repo_root, entry["continuation"])
        if withheld:
            entry["withheld_symbols"] = withheld


def build_homonym_union_bodies(
    repo_root: Path | None,
    union_groups: dict[str, list[dict]],
    char_budget: int = _HOMONYM_UNION_CHAR_BUDGET,
) -> tuple[list[dict], list[dict]]:
    """Inline the UNION of a homonym's defining bodies, char-budgeted.

    ``union_groups`` maps a symbol name to the list of its indexed defs (from
    ``_anchor_symbol_hits``). Returns ``(symbol_bodies, more_definitions)``:

    * ``symbol_bodies``: Read-parity entries (same shape as get_answer's
      existing ``symbol_bodies``: ``path`` / ``name`` / ``lines`` / ``source``,
      plus ``truncated`` / ``continuation`` when the body was line-capped)
      rendered greedily until ``char_budget`` is exhausted. The first def always
      renders even if it alone exceeds the budget (a homonym with one huge def
      must still answer).
    * ``more_definitions``: the defs that did not fit, each ``{file, name,
      line, symbol_id, hint}`` with a "call get_symbol, do NOT Read" redirect so
      the agent never falls back to Read for the remainder.

    Defs are ordered by (name, file_path) so output is deterministic across runs.
    """
    symbol_bodies: list[dict] = []
    more: list[dict] = []
    spent = 0
    defs: list[dict] = []
    for name in sorted(union_groups):
        for d in sorted(union_groups[name], key=lambda x: (x.get("file_path") or "")):
            defs.append(d)

    for d in defs:
        path = d.get("file_path")
        name = d.get("name")
        start = d.get("start_line") or 0
        end = d.get("end_line") or 0
        symbol_id = f"{path}::{name}"
        # Unverified bounds: a get_symbol pointer instead of a slice at
        # unreliable lines under a high-confidence envelope.
        body = (
            None
            if d.get("_approx")
            else _read_symbol_source(
                repo_root, path, start, end, max_lines=_HOMONYM_UNION_BODY_MAX_LINES
            )
        )
        # Budget: always render the first, then only while under budget.
        if body and (not symbol_bodies or spent + len(body) <= char_budget):
            served = body.count("\n") + 1
            end_served = start + served - 1
            entry: dict = {
                "path": path,
                "name": name,
                "lines": [start, end_served],
                "source": body,
            }
            attach_truncation_contract(
                entry, indexed_end=end, end_served=end_served, repo_root=repo_root
            )
            symbol_bodies.append(entry)
            spent += len(body)
        else:
            more.append(
                {
                    "file": path,
                    "name": name,
                    "line": start,
                    "symbol_id": symbol_id,
                    "hint": f"call get_symbol id='{symbol_id}' for this definition, do NOT Read",
                }
            )
    return symbol_bodies, more


async def _concept_anchor_hits(
    repo_root: Path | None,
    question: str,
    hits: list[dict],
) -> list[dict]:
    """Anchor the file whose rationale COMMENT explains a number-bearing question.

    For a question that pins a literal number to a described behaviour (a cap,
    a limit) but names no symbol. Greps comments carrying the number, scores
    them with the rationale miner, and injects the winning file so its comment
    reaches ``code_rationale``.

    Fires only on number-bearing questions (number-free grep is too noisy) and
    only when retrieval missed the winner; otherwise the confidence machinery
    decides. The mined rationale is stashed on the hit to avoid a second grep.

    Returns ``hits`` re-sorted by score (mutated in place). Best-effort: any
    failure leaves ``hits`` untouched.
    """
    import asyncio

    from repowise.server.mcp_server._code_rationale import (
        _salient_numbers,
        grep_comment_candidates,
        mine_rationale,
    )

    if repo_root is None or not question:
        return hits
    # Precision gate: only number-bearing questions.
    if not _salient_numbers(question):
        return hits

    # Blocking grep and file reads: keep them off the server's event loop.
    def _grep_and_mine() -> dict | None:
        candidates = grep_comment_candidates(repo_root, question)
        if not candidates:
            return None
        mined = mine_rationale(repo_root, candidates, question)
        return mined[0] if mined else None

    winner = await asyncio.to_thread(_grep_and_mine)
    if not winner:
        return hits
    winner_path = winner.get("path")
    if not winner_path:
        return hits
    # Retrieval-miss gate: if the winner already leads, leave it to the
    # confidence machinery.
    if hits and hits[0].get("target_path") == winner_path:
        return hits

    near_line = (winner.get("lines") or [0])[0]
    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top, so synthesis runs instead of gating low.
    anchor_score = max(top_score + 1.5, _HIGH_CONFIDENCE_SCORE_FLOOR + 0.5)
    target = _boost_or_insert_file_hit(hits, by_path, winner_path, anchor_score)
    target["_concept_anchored"] = True
    target["_concept_near_line"] = near_line
    # Served verbatim as ``code_rationale`` on any exit path.
    target["_concept_rationale"] = winner
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits
