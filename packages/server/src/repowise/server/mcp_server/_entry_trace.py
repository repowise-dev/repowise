"""Call order for "what happens when" questions in ``get_answer``.

A sequence question ("what happens when a user runs X", "walk me through Y",
"step by step") is answered by one entry function and the calls it makes, in
order. Retrieval lands near that function but ranks files by vocabulary, so a
file that merely says "step" or "order" can take the top slot and the answer
guesses the sequence.

This stage picks the entry among the functions defined in the retrieved files:
the one whose name carries the question's rarest words (a word most candidates
share says little), preferring the one that calls the other matches (it is
upstream of them) and then the one with the most calls. Its callees come from the call graph in call-site order, one level deep
plus the steps of its heaviest callees, and reach synthesis as a call-order
line. The entry's file leads the hits and the files of its heaviest callees
join them, so the prose and the bodies behind each step are both served.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.execution_graph import (
    is_excluded_execution_path,
    is_walkable_execution_edge,
)
from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES
from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.mcp_server._answer_pipeline import is_subsystem_query
from repowise.server.mcp_server._graph_files import node_to_file
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server.tool_search_symbols import _tokens

_SEQUENCE_RE = re.compile(
    r"\bwhat happens\b|\bstep[- ]by[- ]step\b|\bwalk (?:me )?through\b"
    r"|\bin (?:what|which) order\b|\bsequence of\b|\bcall (?:chain|path|sequence|order)\b"
    r"|\bend[- ]to[- ]end\b|\blifecycle of\b",
    re.IGNORECASE,
)

# Words that describe the question's shape, never the code it asks about.
_SHAPE_WORDS = frozenset(
    {
        "happen",
        "happens",
        "step",
        "steps",
        "walk",
        "order",
        "sequence",
        "call",
        "calls",
        "called",
        "function",
        "functions",
        "flow",
        "lifecycle",
        "end",
        "user",
        "users",
    }
)
# A span the question quotes as code (`tool serve`) names the thing asked about.
_QUOTED_RE = re.compile("`([^`]+)`")

_ENTRY_KINDS = ("function", "method", "constructor")
_SEED_HITS = 8  # retrieved files the entry is looked for in
_MIN_CALLS = 2  # an entry that calls fewer has no sequence to tell
_MAX_STEPS = 10  # direct callees named, heaviest kept, then shown in call order
_EXPAND_STEPS = 2  # heaviest callees whose own steps are named too
_MAX_SUBSTEPS = 6
_MAX_INJECT = 2  # callee files placed right under the entry
_RANK_STEP = 0.01  # keeps the placed files in order without opening a dominance gap

_WALK_RE = re.compile(r"\bwalk (?:me )?through\b", re.IGNORECASE)

# "What happens if X fails" asks about a failure path, which the happy-path
# call order would answer wrongly.
_FAILURE_RE = re.compile(
    r"\bwhat happens (?:if|when)\b.*\b(?:fails?|failed|errors?|raises?|throws?|crash(?:es)?"
    r"|times? out|timeouts?|is missing|missing|goes down|is down|breaks?)\b",
    re.IGNORECASE,
)


def is_sequence_question(question: str) -> bool:
    """True when the question asks what happens, in order, on the normal path.

    A subsystem overview ("walk me through the architecture of X") belongs to
    the parent-page stage, and a failure question to the failure path.
    """
    if not question or not _SEQUENCE_RE.search(question):
        return False
    # "Walk me through" alone reads as an overview to the parent-page stage; a
    # question is an overview here only when something else says so too.
    overview = is_subsystem_query(_WALK_RE.sub(" ", question))
    return not _FAILURE_RE.search(question) and not overview


def _question_words(question: str) -> dict[str, float]:
    """Content words of the question, those it quotes as code counting double."""
    quoted = {w for span in _QUOTED_RE.findall(question) for w in _tokens(span)}
    words: dict[str, float] = {}
    for term in content_terms(question, max_terms=16):
        for word in _tokens(term):
            if word not in _SHAPE_WORDS and len(word) >= 3:
                words[word] = 2.0 if word in quoted else 1.0
    return words


def _word_weights(names: list[str], words: dict[str, float]) -> dict[str, float]:
    """A question word's weight, divided by how many candidate names carry it.

    A word many names share says little about which one the question means;
    the named thing is usually rare among them.
    """
    counts = {w: sum(1 for name in names if w in _tokens(name)) for w in words}
    return {w: words[w] / n for w, n in counts.items() if n}


def _name_score(name: str, weights: dict[str, float]) -> float:
    tokens = _tokens(name)
    return sum(weight for word, weight in weights.items() if word in tokens)


async def _callees(
    session: Any, repo_id: str, sources: list[str]
) -> dict[str, list[tuple[int, str]]]:
    """``{source: [(first call line, target), ...]}`` over walkable execution edges."""
    return await _calls(session, repo_id, sources, upstream=False)


async def _callers(
    session: Any, repo_id: str, targets: list[str]
) -> dict[str, list[tuple[int, str]]]:
    """``{target: [(first call line in the caller, caller), ...]}``, as :func:`_callees`."""
    return await _calls(session, repo_id, targets, upstream=True)


async def _calls(
    session: Any, repo_id: str, nodes: list[str], *, upstream: bool
) -> dict[str, list[tuple[int, str]]]:
    if not nodes:
        return {}
    keyed = GraphEdge.target_node_id if upstream else GraphEdge.source_node_id
    res = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.resolution_origin,
            GraphEdge.confidence,
            GraphEdge.call_lines_json,
        ).where(
            GraphEdge.repository_id == repo_id,
            keyed.in_(nodes),
            GraphEdge.edge_type.in_(sorted(EXECUTION_EDGE_TYPES)),
        )
    )
    out: dict[str, dict[str, int]] = {}
    for src, tgt, etype, origin, conf, lines_json in res.all():
        if upstream:
            src, tgt = tgt, src
        if not tgt or tgt == src or is_excluded_execution_path(node_to_file(tgt)):
            continue
        if not is_walkable_execution_edge(etype, origin, conf):
            continue
        try:
            lines = [int(n) for n in json.loads(lines_json or "[]")]
        except (TypeError, ValueError):
            lines = []
        first = min(lines) if lines else 1 << 30
        seen = out.setdefault(src, {})
        seen[tgt] = min(first, seen.get(tgt, first))
    return {
        src: sorted((line, tgt) for tgt, line in targets.items()) for src, targets in out.items()
    }


async def _fanout(session: Any, repo_id: str, node_ids: list[str]) -> dict[str, int]:
    """Walkable calls per node: the same edges the trace would show."""
    calls = await _callees(session, repo_id, node_ids)
    return {node: len(targets) for node, targets in calls.items()}


def _leaf(node_id: str) -> str:
    return node_id.rsplit("::", 1)[-1]


def _heaviest_in_order(calls: list[tuple[int, str]], fanout: dict[str, int], cap: int) -> list[str]:
    """The *cap* callees with the most calls of their own, returned in call order."""
    ranked = sorted(calls, key=lambda c: (-fanout.get(c[1], 0), c[0]))[:cap]
    return [tgt for _line, tgt in sorted(ranked)]


async def _choose_entry(
    session: Any, repo_id: str, seed_files: list[str], words: dict[str, float]
) -> tuple[str, dict[str, list[tuple[int, str]]]] | None:
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.name, GraphNode.file_path).where(
            GraphNode.repository_id == repo_id,
            GraphNode.file_path.in_(seed_files),
            GraphNode.node_type == "symbol",
            GraphNode.kind.in_(_ENTRY_KINDS),
            GraphNode.is_test.is_(False),
        )
    )
    rows = [(node_id, name or "", path) for node_id, name, path in res.all()]
    weights = _word_weights([name for _id, name, _path in rows], words)
    file_rank = {path: i for i, path in enumerate(seed_files)}
    matches = {
        node_id: (score, file_rank.get(path, len(seed_files)))
        for node_id, name, path in rows
        if (score := _name_score(name, weights))
    }
    if not matches:
        return None
    calls = await _callees(session, repo_id, sorted(matches))

    def rank(node_id: str) -> tuple:
        score, file_pos = matches[node_id]
        own = calls.get(node_id, [])
        upstream = sum(1 for _line, tgt in own if tgt in matches)
        return (-score, -upstream, -len(own), file_pos, node_id)

    entry = min(matches, key=rank)
    if len(calls.get(entry, [])) < _MIN_CALLS:
        return None
    return entry, calls


def _label(node_id: str) -> str:
    return f"{_leaf(node_id)} ({node_to_file(node_id)})"


async def expand_via_entry_trace(
    session: Any, repo_id: str, hits: list[dict], question: str, exclude_spec: Any = None
) -> tuple[list[dict], list[list[str]]]:
    """Lead with the entry a sequence question names and serve its call order.

    No-op (``hits`` unchanged, ``[]``) unless the question is sequence-shaped
    and an entry with at least ``_MIN_CALLS`` calls is found in the retrieved
    files. Returns the hits and ``[[entry, step, ...]]`` for ``flow_path``.
    """
    if not hits or not is_sequence_question(question):
        return hits, []
    words = _question_words(question)
    seed_files: list[str] = []
    for h in hits[:_SEED_HITS]:
        path = node_to_file(h.get("target_path") or "")
        if path and path not in seed_files:
            seed_files.append(path)
    if not words or not seed_files:
        return hits, []

    chosen = await _choose_entry(session, repo_id, seed_files, words)
    if chosen is None:
        return hits, []
    entry, calls = chosen

    direct = calls[entry]
    fanout = await _fanout(session, repo_id, [tgt for _line, tgt in direct])
    steps = _heaviest_in_order(direct, fanout, _MAX_STEPS)
    heavy = sorted(steps, key=lambda s: -fanout.get(s, 0))[:_EXPAND_STEPS]
    heavy = [s for s in heavy if fanout.get(s, 0) >= _MIN_CALLS]
    sub_calls = await _callees(session, repo_id, heavy)
    sub_fanout = await _fanout(
        session, repo_id, [tgt for s in heavy for _line, tgt in sub_calls.get(s, [])]
    )

    parts = []
    for step in steps:
        label = _label(step)
        subs = _heaviest_in_order(sub_calls.get(step, []), sub_fanout, _MAX_SUBSTEPS)
        if subs:
            label += " [calls, in order: " + ", ".join(_leaf(s) for s in subs) + "]"
        parts.append(label)
    sequence = (
        f"Calls made by {_label(entry)}, in source order (a call may sit on a branch "
        "that does not always run): " + " -> ".join(parts)
    )

    entry_file = node_to_file(entry)
    inject = []
    for step in heavy:
        path = node_to_file(step)
        if path != entry_file and path not in inject and not is_excluded(path, exclude_spec):
            inject.append(path)
    inject = inject[:_MAX_INJECT]

    hits = await _place_files(session, repo_id, hits, entry_file, inject)
    entry_hit = next((h for h in hits if h.get("target_path") == entry_file), None)
    if entry_hit is None:
        # No readable page for the entry: nothing to carry the order to synthesis.
        return hits, []
    entry_hit["_call_sequence"] = sequence
    return hits, [[entry, *steps]]


async def _place_files(
    session: Any, repo_id: str, hits: list[dict], entry_file: str, inject: list[str]
) -> list[dict]:
    """The entry's file first, then the callee files, then everything else."""
    top = max((h.get("score", 0.0) for h in hits), default=0.0)
    wanted: dict[str, float] = {}
    for i, path in enumerate([entry_file, *inject]):
        wanted.setdefault(path, top + _RANK_STEP * (len(inject) + 1 - i))
    by_path = {h.get("target_path"): h for h in hits if h.get("page_type") in FILE_ROW_TYPES}
    absent = [p for p in wanted if p not in by_path]
    if absent:
        res = await session.execute(
            select(Page.target_path, Page.summary).where(
                Page.repository_id == repo_id,
                Page.target_path.in_(absent),
                Page.page_type == "file_page",
                Page.freshness_status != "tombstone",
            )
        )
        for path, summary in res.all():
            hit = {
                "page_id": f"file_page:{path}",
                "target_path": path,
                "title": f"File: {path}",
                "summary": summary or "",
                "snippet": (summary or "")[:200],
                "page_type": "file_page",
                "score": 0.0,
                "_sources": {"call_trace"},
                "_expanded_from": "call_trace",
            }
            hits.append(hit)
            by_path[path] = hit
    for path, score in wanted.items():
        hit = by_path.get(path)
        if hit is not None and hit.get("score", 0.0) < score:
            hit["score"] = score
            hit.setdefault("_sources", set()).add("call_trace")
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits


# How something gets somewhere: the calls out of the symbol the question names
# answer it. Words of motion along a call chain, not of any one codebase.
_PATH_RE = re.compile(
    r"\b(?:reach(?:es|ed)?|ends? up|gets? (?:to|into|back)|flows? (?:to|into|through)"
    r"|dispatch(?:es|ed)?|propagat\w*|pass(?:es|ed)? (?:to|into|through)|hands? (?:off|over)"
    r"|(?<!stack )trac(?:e|es|ed))\b",
    re.IGNORECASE,
)
# Sized to a reply of about 1-2 KB: three levels of call, the four heaviest
# callees per symbol, the three heaviest of those walked on.
_PATH_DEPTH = 3
_PATH_FANOUT = 4
_PATH_EXPAND = 3
_PATH_MAX_ROWS = 12


def is_call_path_question(question: str) -> bool:
    """True when the question asks how a call travels on, not what one symbol is."""
    if not question or _FAILURE_RE.search(question):
        return False
    return is_sequence_question(question) or bool(_PATH_RE.search(question))


async def _symbol_starts(session: Any, repo_id: str, node_ids: list[str]) -> dict[str, int]:
    """Definition line per indexed symbol; a call into a library has none."""
    if not node_ids:
        return {}
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.start_line).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.in_(node_ids),
            GraphNode.node_type == "symbol",
        )
    )
    return {node_id: start for node_id, start in res.all() if start}


async def symbol_at(session: Any, repo_id: str, path: str, line: int) -> str | None:
    """The callable symbol defined at *path*:*line*, if the graph has one."""
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.kind).where(
            GraphNode.repository_id == repo_id,
            GraphNode.file_path == path,
            GraphNode.start_line == line,
            GraphNode.node_type == "symbol",
        )
    )
    rows = res.all()
    callable_ = [node_id for node_id, kind in rows if kind in _ENTRY_KINDS]
    return (callable_ or [node_id for node_id, _kind in rows] or [None])[0]


def _hop_row(caller: str, call_line: int, callee: str, start: int) -> str:
    at = f":{call_line}" if call_line < 1 << 30 else ""
    return f"{_leaf(caller)} {node_to_file(caller)}{at} -> {_leaf(callee)} {node_to_file(callee)}:{start}"


def names_destination(question: str, name: str) -> bool:
    """True when a motion word comes before *name* and none after: the call ends there.

    "How does a message reach run_job" ends at ``run_job``; "trace how
    handle flows to the store" starts at ``handle``.
    """
    named = None
    for part in dict.fromkeys((name, name.rsplit(".", 1)[-1])):
        named = named or re.search(rf"(?<!\w){re.escape(part)}(?!\w)", question, re.IGNORECASE)
    if named is None:
        return False
    motions = [m.start() for m in _PATH_RE.finditer(question)]
    return any(at < named.start() for at in motions) and not any(
        at > named.start() for at in motions
    )


def _picked(
    steps: list[tuple[int, str]], fanout: dict[str, int], words: dict[str, float]
) -> list[str]:
    """The :data:`_PATH_FANOUT` neighbours the question's words name most, then the
    heaviest, returned in call order."""
    def rank(step: tuple[int, str]) -> tuple:
        line, node = step
        return (-_name_score(node.replace("/", " ").replace(".", " "), words), -fanout.get(node, 0), line)

    return [node for _line, node in sorted(sorted(steps, key=rank)[:_PATH_FANOUT])]


def _shipped_steps(
    calls: list[tuple[int, str]], starts: dict[str, int], seen: set[str], exclude_spec: Any
) -> list[tuple[int, str]]:
    """The calls into indexed, shipped code (``code_origin``) not yet in the path."""
    from repowise.core.code_origin import path_origin

    return [
        (line, t)
        for line, t in calls
        if t in starts and t not in seen
        and path_origin(node_to_file(t)) == "production"
        and not is_excluded(node_to_file(t), exclude_spec)
    ]


async def call_path(
    session: Any, repo_id: str, root: str, exclude_spec: Any = None, *,
    upstream: bool = False, question: str = "",
) -> list[str]:
    """Hops out of *root* (or into it, *upstream*) over walkable call edges, one row each.

    A row reads ``caller file:call line -> callee file:definition line``. Only
    hops through shipped code are kept, so tests, tools and libraries do not
    take the rows a chain needs. Each level keeps the neighbours the
    question's words name, then the heaviest, and walks on from the heaviest.
    """
    words = _question_words(question) if question else {}
    rows: list[str] = []
    seen = {root}
    frontier = [root]
    for _depth in range(_PATH_DEPTH):
        calls = await (_callers if upstream else _callees)(session, repo_id, frontier)
        targets = [t for src in frontier for _line, t in calls.get(src, []) if t not in seen]
        starts = await _symbol_starts(session, repo_id, [*targets, *frontier])
        fanout = await _fanout(session, repo_id, list(starts))
        walked: list[str] = []
        for src in frontier:
            steps = _shipped_steps(calls.get(src, []), starts, seen, exclude_spec)
            line_of = {t: line for line, t in steps}
            for target in _picked(steps, fanout, words):
                seen.add(target)
                walked.append(target)
                ends = (target, src) if upstream else (src, target)
                rows.append(_hop_row(ends[0], line_of[target], ends[1], starts.get(ends[1], 0)))
        if len(rows) >= _PATH_MAX_ROWS:
            return rows[:_PATH_MAX_ROWS]
        frontier = sorted((t for t in walked if fanout.get(t)), key=lambda t: -fanout[t])
        frontier = frontier[:_PATH_EXPAND]
        if not frontier:
            break
    return rows
