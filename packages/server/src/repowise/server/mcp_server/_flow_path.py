"""Flow-path expansion for ``get_answer``.

Some questions are not "what is X" but "how does X reach Y" — a flow that
crosses two subsystems the answer must traverse (``how does retrieval feed
synthesis in get_answer``, ``how does a parsed symbol's line range travel from
ingestion into a get_answer symbol body``). Plain 1-hop expansion does not
solve these: it walks outward from the top hit ranked by PageRank, so it pulls
in central hubs, not the specific far endpoint the question names. The far
endpoint sits 2-4 hops away and never surfaces (measured: ``enrichment.py`` is
absent from ``answer.py``'s 18-file 1-hop neighborhood).

This stage is the other mechanism. When a question anchors two or more
endpoints (a named symbol's file, a module the question names by basename), it
runs a bounded bidirectional BFS between those endpoints over the file-level
dependency graph and injects the files ON the connecting path as hits, so both
endpoints surface in one call with the chain between them.

File-level adjacency is built from two edge kinds:

* ``imports`` edges are already file-to-file (``a.py -> b.py``).
* ``calls`` edges are symbol-to-symbol (``a.py::f -> b.py::g``); we project
  each onto its two files and drop same-file self-loops. This is what triples
  the edge material over the imports-only surface and catches call-only
  relationships that carry no import (a function passed in, a registry lookup).

Guardrails, all from the live-index feasibility test: a confidence floor on
``calls`` edges (avg 0.90, but the floor drops the noise tail), a hop cap of 4
(the endpoints connect in 2-4), a per-token fan-out cap so a generic basename
(``models``, ``config``) that resolves to many files never seeds the search,
and a hard visited cap so a core-layer hub (100 neighbors) can't blow the
budget.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import GraphEdge, Page, WikiSymbol
from repowise.server.mcp_server.tool_answer.retrieval import _question_terms

# Edge kinds that form the file-level dependency graph. ``imports`` is
# file-to-file already; ``calls`` is symbol-to-symbol and gets projected to
# files. The other kinds (co_changes, framework, extends, has_method, defines)
# are either not directed dependency flow or not answer-bearing, so they stay
# out of the path search.
_FLOW_EDGE_TYPES = ("imports", "calls")

# Confidence floor for ``calls`` edges. Imports are always 1.0; calls average
# 0.90 but have a low-confidence tail (heuristic resolution). 0.5 keeps every
# genuine call and drops the noise. Imports are never filtered.
_FLOW_CALLS_CONF_FLOOR = 0.5

# Depth cap for the path search. Endpoints connect in 2-4 hops on the measured
# graph; past 4 a "path" is a coincidence, not a flow.
_FLOW_MAX_DEPTH = 4

# A question token that resolves to more than this many files is too generic to
# be an endpoint (``models``, ``config``, ``__init__``). Drop it rather than
# seed the search from a dozen unrelated files.
_FLOW_MAX_FILES_PER_TOKEN = 3

# At least this many DISTINCT question-derived endpoints (named symbols +
# module-name matches, NOT the top retrieval hits) are required before the
# stage fires. Two endpoints is what makes a question a flow; one is a plain
# "what is X" that the normal pipeline already handles.
_FLOW_MIN_QUESTION_ANCHORS = 2

# Cap the anchor set so the pairwise BFS stays cheap.
_FLOW_MAX_ANCHORS = 6

# Cap how many new files the path can inject, so a long chain can't flood the
# 5-hit budget. Endpoints are injected before intermediate pass-through nodes.
_FLOW_MAX_INJECT = 4

# Damping for injected path files, mirroring ``_answer_pipeline``'s graph
# expansion: they did not surface in retrieval, so they sit below the confident
# top hit (dominance gate stays intact) but above the weak tail, landing in the
# top-5 the response serves.
_FLOW_DAMPING = 0.7

# Safety valve: stop the BFS frontier from exploding on a hub node. Bidirectional
# search meets in the middle well before this on real paths; the cap only bites
# on pathological fan-out.
_FLOW_MAX_VISITED = 4000


def _basename_stem(path: str) -> str:
    """``a/b/retrieval.py`` -> ``retrieval`` (lower-case, extension dropped)."""
    base = os.path.basename(path)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem.lower()


def _ext(path: str) -> str:
    """Lower-case file extension (``retrieval.py`` -> ``py``), ``""`` if none."""
    base = os.path.basename(path)
    return base.rsplit(".", 1)[1].lower() if "." in base else ""


def _is_plumbing(path: str) -> bool:
    """Package glue and tests: legitimate import hops but never a flow endpoint.

    ``__init__.py`` re-exports and test modules bridge unrelated files in the
    graph (a test imports both ends of the codebase), so they make good BFS
    coincidences and bad injected answers. Kept out of anchors and injection;
    they may still be traversed as interior nodes.
    """
    norm = path.replace("\\", "/").lower()
    base = norm.rsplit("/", 1)[-1]
    if base == "__init__.py":
        return True
    if base.startswith("test_") or base.endswith("_test.py") or base.endswith(".test.ts"):
        return True
    return "/tests/" in norm or "/test/" in norm or "/__tests__/" in norm


def _token_matches_stem(token: str, stem: str) -> bool:
    """Match a question token to a file stem, tolerating a trailing plural.

    ``retrieval``/``retrieval``, ``symbol``/``symbols``, ``synthesis``/
    ``synthesis`` all match; unrelated words do not. Kept deliberately narrow
    (exact or single-``s`` plural) so a token can't fuzzily grab a file.
    """
    if token == stem:
        return True
    return token + "s" == stem or token == stem + "s"


async def _resolve_question_anchors(
    session: Any,
    repo_id: str,
    question: str,
    question_ids: set[str],
) -> dict[str, str]:
    """Map the question to the files it names, from two sources.

    * Named symbols: an identifier the question spells out (``get_answer``,
      ``DecisionExtractor``) that resolves to an indexed function/method/class
      -> its defining file.
    * Module names: a content word whose stem is a file basename with a wiki
      page (``retrieval`` -> ``.../retrieval.py``), skipping over-generic
      stems that resolve to many files.

    Returns ``{file_path: source}`` where source is ``"symbol"`` or
    ``"module"``. These are the endpoints the path search connects.
    """
    anchors: dict[str, str] = {}

    # Named symbols -> defining files.
    if question_ids:
        res = await session.execute(
            select(WikiSymbol.file_path).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.name.in_(list(question_ids)),
                WikiSymbol.kind.in_(("function", "method", "class", "interface")),
            )
        )
        for (fp,) in res.all():
            if fp:
                anchors.setdefault(fp, "symbol")

    # Module-name matches. Build a stem -> file_page paths map once, then keep
    # tokens that resolve tightly (not the generic ones).
    tokens = {t for t in _question_terms(question) if len(t) >= 4}
    if tokens:
        res = await session.execute(
            select(Page.target_path).where(
                Page.repository_id == repo_id,
                Page.page_type == "file_page",
            )
        )
        stem_to_paths: dict[str, list[str]] = {}
        for (tp,) in res.all():
            if tp:
                stem_to_paths.setdefault(_basename_stem(tp), []).append(tp)
        for token in tokens:
            for stem, paths in stem_to_paths.items():
                if not _token_matches_stem(token, stem):
                    continue
                if len(paths) > _FLOW_MAX_FILES_PER_TOKEN:
                    continue  # too generic to be an endpoint
                for p in paths:
                    if not _is_plumbing(p):
                        anchors.setdefault(p, "module")

    return anchors


async def _load_file_adjacency(session: Any, repo_id: str) -> dict[str, set[str]]:
    """Undirected file-level adjacency from imports + projected calls edges.

    Both directions are added: a flow can be read caller->callee or the reverse,
    and the endpoints are anchored, not oriented. ``calls`` edges are projected
    from symbol node_ids onto their files and self-loops dropped.
    """
    res = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.confidence,
        ).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type.in_(_FLOW_EDGE_TYPES),
        )
    )
    adj: dict[str, set[str]] = {}
    for src, tgt, etype, conf in res.all():
        if etype == "calls":
            if (conf or 0.0) < _FLOW_CALLS_CONF_FLOOR:
                continue
            src = src.split("::", 1)[0]
            tgt = tgt.split("::", 1)[0]
        if not src or not tgt or src == tgt:
            continue
        # A flow the answer traverses is single-language: cross-extension edges
        # (a Python module and a same-named TypeScript re-export, a test that
        # imports both ends) are graph coincidences, not dependency flow. Keeping
        # them lets BFS stitch unrelated files across package boundaries.
        if _ext(src) != _ext(tgt):
            continue
        adj.setdefault(src, set()).add(tgt)
        adj.setdefault(tgt, set()).add(src)
    return adj


def _bfs_path(adj: dict[str, set[str]], src: str, dst: str, max_depth: int) -> list[str] | None:
    """Shortest file path between ``src`` and ``dst`` (inclusive), or None.

    Bidirectional BFS bounded to ``max_depth`` hops and ``_FLOW_MAX_VISITED``
    nodes. Returns the node sequence ``[src, ..., dst]``.
    """
    if src == dst:
        return [src]
    if src not in adj or dst not in adj:
        return None
    # Parents from each side; the sides meet in the middle. Each round advances
    # one side by a hop, so the combined reach after ``fwd_depth + bwd_depth``
    # rounds is that many hops — the loop runs while that sum is under the cap,
    # which lets a full ``max_depth``-hop path be found. Strict alternation (not
    # smaller-frontier-first) guarantees both sides advance; expanding only the
    # forward side would never close a path longer than the forward reach.
    fwd: dict[str, Any] = {src: None}
    bwd: dict[str, Any] = {dst: None}
    fwd_frontier = {src}
    bwd_frontier = {dst}
    fwd_depth = 0
    bwd_depth = 0
    visited = 0
    expand_fwd = True
    while fwd_frontier and bwd_frontier and (fwd_depth + bwd_depth) < max_depth:
        if expand_fwd:
            frontier, parents, other = fwd_frontier, fwd, bwd
        else:
            frontier, parents, other = bwd_frontier, bwd, fwd
        nxt: set[str] = set()
        for node in frontier:
            for nb in adj.get(node, ()):
                if nb in parents:
                    continue
                parents[nb] = node
                visited += 1
                if nb in other:
                    return _stitch(fwd, bwd, nb)
                nxt.add(nb)
            if visited > _FLOW_MAX_VISITED:
                return None
        if expand_fwd:
            fwd_frontier = nxt
            fwd_depth += 1
        else:
            bwd_frontier = nxt
            bwd_depth += 1
        expand_fwd = not expand_fwd
    return None


def _stitch(fwd: dict[str, Any], bwd: dict[str, Any], meet: str) -> list[str]:
    """Reconstruct the full path from the two BFS parent maps at ``meet``."""
    left: list[str] = []
    node: Any = meet
    while node is not None:
        left.append(node)
        node = fwd.get(node)
    left.reverse()
    right: list[str] = []
    node = bwd.get(meet)
    while node is not None:
        right.append(node)
        node = bwd.get(node)
    return left + right


async def expand_via_flow_path(
    session: Any,
    repo_id: str,
    hits: list[dict],
    question: str,
    question_ids: set[str],
) -> tuple[list[dict], list[list[str]]]:
    """Inject the graph path between question-anchored endpoints into ``hits``.

    No-op (returns ``hits`` unchanged, ``[]``) unless the question resolves to
    ``>= _FLOW_MIN_QUESTION_ANCHORS`` distinct endpoints AND a bounded path
    connects at least one pair. On a hit, each path's two endpoints are surfaced
    into the served top-5 (a buried one is boosted, an absent one injected, both
    at a damped score), and the ordered paths are returned for the answer to
    lead with.
    """
    if not hits:
        return hits, []

    top_hit = hits[0].get("target_path")

    anchors = await _resolve_question_anchors(session, repo_id, question, question_ids)
    # A flow is single-language, and the confident top hit fixes which one: a
    # Python question retrieves a Python top hit, so a same-named file in another
    # language (``symbols.ts`` for a ``symbols.py`` question) is not an endpoint.
    # Dropping off-language anchors here keeps the pairwise BFS from stitching
    # two coincidental cross-package matches of a generic token.
    primary_ext = _ext(top_hit) if top_hit else ""
    if primary_ext:
        anchors = {p: src for p, src in anchors.items() if _ext(p) == primary_ext}
    if len(anchors) < _FLOW_MIN_QUESTION_ANCHORS:
        return hits, []

    # Endpoints to connect: the question-derived anchors, plus the confident top
    # hit as a bridge (a question often names one endpoint and the top hit is
    # the other). Cap the set so the pairwise BFS stays cheap.
    endpoints = list(anchors)
    if top_hit and top_hit not in anchors:
        endpoints.append(top_hit)
    endpoints = endpoints[:_FLOW_MAX_ANCHORS]

    adj = await _load_file_adjacency(session, repo_id)

    paths: list[list[str]] = []
    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            path = _bfs_path(adj, endpoints[i], endpoints[j], _FLOW_MAX_DEPTH)
            if path and len(path) >= 2:
                paths.append(path)

    if not paths:
        return hits, []

    # Surface only the two ENDPOINTS of each path — the files the question
    # actually named. The interior is what proves the endpoints connect (so a
    # coincidental basename match is rejected), but injecting it as extra hits
    # just hands the agent pass-through files to drill into for a two-file
    # question. Shortest paths first so a direct neighbor beats a 4-hop
    # coincidence; capped at _FLOW_MAX_INJECT.
    paths.sort(key=len)
    path_nodes: list[str] = []
    seen_nodes: set[str] = set()
    for path in paths:
        for node in (path[0], path[-1]):
            if node not in seen_nodes and not _is_plumbing(node):
                seen_nodes.add(node)
                path_nodes.append(node)
    path_nodes = path_nodes[:_FLOW_MAX_INJECT]

    hit_by_path = {h.get("target_path"): h for h in hits}
    parent_score = hits[0].get("score", 0.0)
    flow_score = parent_score * _FLOW_DAMPING

    # A path file already in the candidate set is usually ranked below the cap
    # (that is the whole flow-miss failure: the far endpoint was retrieved but
    # buried). Boost it to the flow score so it rises into the served top-5
    # rather than injecting a duplicate. Genuinely absent path files are fetched
    # and injected fresh.
    absent = [p for p in path_nodes if p not in hit_by_path]
    meta_by_path: dict[str, tuple[str, str]] = {}
    if absent:
        res = await session.execute(
            select(Page.target_path, Page.summary, Page.page_type).where(
                Page.repository_id == repo_id,
                Page.target_path.in_(absent),
                Page.page_type == "file_page",
            )
        )
        meta_by_path = {
            tp: (summary or "", ptype or "file_page") for tp, summary, ptype in res.all()
        }

    additions: list[dict] = []
    touched = False
    for path in path_nodes:
        existing_hit = hit_by_path.get(path)
        if existing_hit is not None:
            if existing_hit.get("score", 0.0) < flow_score:
                existing_hit["score"] = flow_score
                existing_hit.setdefault("_sources", set()).add("graph_path")
                touched = True
            continue
        if path not in meta_by_path:
            continue
        summary, ptype = meta_by_path[path]
        additions.append(
            {
                "page_id": f"file_page:{path}",
                "target_path": path,
                "title": f"File: {path}",
                "summary": summary,
                "snippet": summary[:200],
                "page_type": ptype,
                "score": flow_score,
                "_sources": {"graph_path"},
                "_expanded_from": "flow",
            }
        )
        touched = True

    if not touched:
        return hits, paths

    combined = hits + additions
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined, paths
