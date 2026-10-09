"""Call-graph callers as evidence for "who calls / triggers X" questions.

Retrieval answers from pages, which describe what a file does rather than who
reaches it. When the question asks for callers and names a symbol (or
retrieval's top file has a symbol matching the question), the graph's callers
of that symbol are served as ``graph_callers``, production first. Every other
question is untouched.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import get_graph_nodes_by_ids
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.test_paths import is_test_path
from repowise.server.mcp_server._budget import cap_collection
from repowise.server.mcp_server._helpers import filter_dicts_by_key
from repowise.server.mcp_server._query_shape import _unmistakably_code
from repowise.server.mcp_server._wrapper_callers import (
    MIN_CALL_CONFIDENCE,
    files_importing,
    forwarding_wrapper_callers,
)
from repowise.server.mcp_server.tool_answer.config import _RELEVANCE_NAME_WEIGHT

_SUBJECT = (
    r"\b(?:who|which|what)(?:\s+(?:non-test|production|source|other|file|files|function|"
    r"functions|method|methods|code|module|modules|class|classes|component|components|"
    r"service|services|package|packages|handler|handlers|caller|callers|part|parts|place|"
    r"places|script|scripts))*\s+(?:would\s+|will\s+|can\s+|could\s+|might\s+)?"
)
#: Asks who reaches a symbol. Strong enough to resolve the symbol from retrieval
#: when the question does not name one.
_CALLER_QUESTION = re.compile(
    _SUBJECT + r"(?:calls?|invokes?|triggers?)\b"
    r"|\bwhere\s+(?:is|are|was|were)\s+[^?]{1,80}?\s+(?:called|invoked|triggered)\b"
    r"|\bcallers?\s+of\b|\bwhat\s+(?:would\s+|will\s+)?breaks?\b",
    re.IGNORECASE,
)
#: Caller verbs that also describe ordinary "where is X done" questions, so they
#: count only when the question names the symbol itself.
_NAMED_CALLER_QUESTION = re.compile(
    _SUBJECT + r"(?:uses?|registers?|references?|imports?|depends?\s+on)\b"
    r"|\bwhere\s+(?:is|are|was|were)\s+[^?]{1,80}?\s+(?:used|registered|referenced|imported)\b"
    r"|\bdepend(?:s|ing)?\s+on\b|\bused\s+by\b",
    re.IGNORECASE,
)

_TARGET_KINDS = ("function", "method", "class")
#: A name with more definitions than this is too generic to answer for.
_MAX_DEFS_PER_NAME = 4
_MAX_TARGETS = 4
#: Two of the question's terms in the symbol's own name.
_MIN_RETRIEVED_RELEVANCE = 2 * _RELEVANCE_NAME_WEIGHT
_MAX_RETRIEVED_TARGETS = 2
#: Callers fetched per target; the served block is cut to _MAX_ROWS after the
#: production-first sort.
_MAX_DIRECT = 50
_MAX_IMPORTER_ROWS = 3
_MAX_ROWS = 8


class CallerEvidence(NamedTuple):
    """Graph caller rows, production first, and whether the question named the target."""

    rows: list[dict]
    named: bool = False

    @property
    def graph_answered(self) -> bool:
        """A named target with a direct production call: enough to grade on.

        Hop and importer rows are leads, and a target resolved from retrieval
        may be the wrong symbol, so neither lifts a grade.
        """
        return self.named and any(
            r["edge_type"] == "calls" and "via_wrapper" not in r and not r.get("test")
            for r in self.rows
        )


NO_EVIDENCE = CallerEvidence([])


def is_caller_question(question: str, question_ids: set[str]) -> bool:
    """Whether the question asks who calls, triggers or depends on something."""
    if _CALLER_QUESTION.search(question):
        return True
    return bool(_named_ids(question_ids)) and bool(_NAMED_CALLER_QUESTION.search(question))


def _named_ids(question_ids: set[str]) -> list[str]:
    """Code-shaped identifiers; a bare leaf yields to the qualified form naming it."""
    ids = {q for q in question_ids if _unmistakably_code(q)}
    leaves = {q.rsplit(".", 1)[-1] for q in ids if "." in q}
    return sorted(q for q in ids if "." in q or q not in leaves)


def _qualifier_matches(node: GraphNode, qualifier: str) -> bool:
    """Whether ``qualifier`` (``Foo`` in ``Foo.run``) names the node's parent or module."""
    want = qualifier.rsplit(".", 1)[-1].lower()
    local = node.node_id.split("::", 1)[-1]
    parents = {
        *(part.lower() for part in (node.qualified_name or "").split(".")[:-1]),
        *(part.lower() for part in local.split(".")[:-1]),
    }
    return want in parents or PurePosixPath(node.file_path or "").stem.lower() == want


async def _named_targets(
    session: AsyncSession, repo_id: str, names: list[str]
) -> list[GraphNode]:
    res = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.name.in_({n.rsplit(".", 1)[-1] for n in names}),
            GraphNode.kind.in_(_TARGET_KINDS),
        )
    )
    by_leaf: dict[str, list[GraphNode]] = {}
    for node in res.scalars().all():
        if node.file_path and not is_test_path(node.file_path, node.language):
            by_leaf.setdefault(node.name, []).append(node)
    out: list[GraphNode] = []
    for name in names:
        qualifier, _, leaf = name.rpartition(".")
        defs = by_leaf.get(leaf, [])
        # A qualifier that matches no definition drops the name rather than
        # widening it to every same-named symbol.
        if qualifier:
            defs = [n for n in defs if _qualifier_matches(n, qualifier)]
        if 0 < len(defs) <= _MAX_DEFS_PER_NAME:
            out.extend(n for n in sorted(defs, key=lambda n: n.node_id) if n not in out)
    return out[:_MAX_TARGETS]


async def _retrieved_targets(
    session: AsyncSession, repo_id: str, hits: list[dict]
) -> list[GraphNode]:
    """The top hit's symbols whose names carry the question's terms."""
    top = hits[0] if hits else {}
    path = top.get("target_path")
    if not path or is_test_path(path):
        return []
    picked = sorted(
        (
            s
            for s in top.get("symbols") or []
            if s.get("kind") in _TARGET_KINDS
            and (s.get("_relevance") or 0) >= _MIN_RETRIEVED_RELEVANCE
        ),
        key=lambda s: (-(s.get("_relevance") or 0), s.get("start_line") or 0),
    )[:_MAX_RETRIEVED_TARGETS]
    if not picked:
        return []
    res = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == path,
            GraphNode.name.in_([s["name"] for s in picked]),
        )
    )
    order = {s["name"]: i for i, s in enumerate(picked)}
    return sorted(res.scalars().all(), key=lambda n: (order.get(n.name, 99), n.node_id))


def _row(caller: str, file: str, line: int | None, target: str, edge_type: str) -> dict:
    row: dict[str, Any] = {"caller": caller, "file": file}
    # A module-level caller sits at line 0, which names no line to open.
    if line:
        row["line"] = line
    row["target"] = target
    row["edge_type"] = edge_type
    return row


async def _direct_edges(
    session: AsyncSession, repo_id: str, target: GraphNode, *, cross_file: bool
) -> list[GraphEdge]:
    """One target's inbound calls, capped on their own so a hub cannot starve the rest."""
    res = await session.execute(
        select(GraphEdge)
        .where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id == target.node_id,
            GraphEdge.edge_type == "calls",
            GraphEdge.confidence >= MIN_CALL_CONFIDENCE,
        )
        .order_by(GraphEdge.confidence.desc(), GraphEdge.source_node_id)
        .limit(_MAX_DIRECT)
    )
    return [
        e
        for e in res.scalars().all()
        if e.source_node_id != target.node_id
        and not (cross_file and e.source_node_id.split("::")[0] == target.file_path)
    ]


async def caller_evidence(
    session: AsyncSession,
    repo_id: str,
    question: str,
    question_ids: set[str],
    hits: list[dict],
    exclude_spec: Any = None,
) -> CallerEvidence:
    """Graph callers of the symbol a caller question asks about, production first.

    Named targets list every caller; a target resolved from retrieval lists only
    callers in other files, since its own file's plumbing is not what triggers
    it. A forwarding wrapper's callers come one hop further (``via_wrapper``),
    and a production caller nothing in production calls is followed to the
    files importing it by name, which is how a handler or extension gets
    registered.
    """
    if not is_caller_question(question, question_ids):
        return NO_EVIDENCE
    named = _named_ids(question_ids)
    targets = await _named_targets(session, repo_id, named) if named else []
    cross_file = not targets
    if cross_file:
        if not _CALLER_QUESTION.search(question):
            return NO_EVIDENCE
        targets = await _retrieved_targets(session, repo_id, hits)
    if not targets:
        return NO_EVIDENCE

    edges = {
        t.node_id: await _direct_edges(session, repo_id, t, cross_file=cross_file)
        for t in targets
    }
    nodes = await get_graph_nodes_by_ids(
        session, repo_id, list({e.source_node_id for es in edges.values() for e in es})
    )

    rows: list[dict] = []
    seen: set[str] = set()
    for target in targets:
        mine = edges[target.node_id]
        for e in mine:
            if e.source_node_id in seen:
                continue
            seen.add(e.source_node_id)
            src = nodes.get(e.source_node_id)
            rows.append(
                _row(
                    e.source_node_id,
                    src.file_path if src else e.source_node_id.split("::")[0],
                    src.start_line if src else None,
                    target.node_id,
                    "calls",
                )
            )
        hop = await forwarding_wrapper_callers(
            session, repo_id, target, [e.source_node_id for e in mine], known_nodes=nodes
        )
        for h in hop:
            caller = h.get("symbol_id") or h["file"]
            if caller in seen or (cross_file and h["file"] == target.file_path):
                continue
            seen.add(caller)
            row = _row(
                caller, h["file"], h.get("line"), target.node_id, h.get("edge_type") or "imports"
            )
            if h.get("wholesale"):
                row["wholesale"] = True
            row["via_wrapper"] = h["via_wrapper"]
            rows.append(row)

    rows.extend(await _registering_files(session, repo_id, rows, nodes))
    for row in rows:
        if is_test_path(row["file"]):
            row["test"] = True
    rows = filter_dicts_by_key(rows, "file", exclude_spec)
    rows.sort(key=lambda r: bool(r.get("test")))
    return CallerEvidence(rows, named=not cross_file)


async def _registering_files(
    session: AsyncSession, repo_id: str, rows: list[dict], nodes: dict[str, GraphNode]
) -> list[dict]:
    """Files importing, by name, a production caller that no production code calls."""
    callers = [
        nodes[r["caller"]]
        for r in rows
        if r["edge_type"] == "calls"
        and "via_wrapper" not in r
        and r["caller"] in nodes
        and nodes[r["caller"]].kind in ("function", "method", "class")
        and not is_test_path(r["file"])
    ]
    if not callers:
        return []
    res = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_([c.node_id for c in callers]),
            GraphEdge.edge_type == "calls",
            GraphEdge.confidence >= MIN_CALL_CONFIDENCE,
        )
    )
    called = {dst for src, dst in res.all() if not is_test_path(src.split("::")[0])}
    unreached = [c for c in callers if c.node_id not in called]
    if not unreached:
        return []
    listed = {r["file"] for r in rows} | {c.file_path for c in unreached}
    # A wholesale import (a barrel, ``import *``) does not say this symbol is used.
    imported = [t for t in await files_importing(session, repo_id, unreached, listed) if t[3]]
    # Production importers first, so a test importing the same symbol cannot
    # take the only slots.
    imported.sort(key=lambda t: is_test_path(t[0]))
    return [
        _row(importer, importer, None, symbol_id, "imports")
        for importer, _conf, symbol_id, _named in imported[:_MAX_IMPORTER_ROWS]
    ]


def attach_graph_callers(payload: dict, evidence: CallerEvidence) -> dict:
    """Add ``graph_callers`` (shared count fields when cut) and the keyless answer lead.

    The lead is written from every row, not the served cut, so its counts are
    true; the projection states it only where no model wrote the answer.
    """
    if evidence.rows:
        cap_collection(payload, "graph_callers", evidence.rows, _MAX_ROWS)
        if sentence := caller_sentence(evidence.rows):
            payload["_graph_callers_answer"] = sentence
    return payload


def _short(symbol_id: str) -> str:
    return symbol_id.split("::")[-1] if "::" in symbol_id else symbol_id


def _phrase(row: dict) -> str:
    where = f"{row['file']}:{row['line']}" if row.get("line") is not None else row["file"]
    target = _short(row["target"])
    wrapper = row.get("via_wrapper")
    # A same-named caller or wrapper would read "X calls X"; the file tells them apart.
    if target in (_short(row["caller"]), _short(wrapper or "")):
        target = f"{target} in {row['target'].split('::')[0]}"
    if row["edge_type"] == "imports":
        if wrapper and row.get("wholesale"):
            return (
                f"{row['file']} loads {wrapper.split('::')[0]} wholesale, whose "
                f"{_short(wrapper)} wraps {target}"
            )
        if wrapper:
            return (
                f"{row['file']} imports wrapper {_short(wrapper)} "
                f"({wrapper.split('::')[0]}), which calls {target}"
            )
        return f"{row['file']} imports {target}"
    if wrapper:
        return f"{_short(row['caller'])} ({where}) calls {target} via wrapper {_short(wrapper)}"
    return f"{_short(row['caller'])} ({where}) calls {target}"


def caller_lines(rows: list[dict]) -> list[str]:
    """One plain sentence per caller row, production rows first as served."""
    return [_phrase(r) + (" [test]" if r.get("test") else "") for r in rows[:_MAX_ROWS]]


def caller_sentence(rows: list[dict]) -> str | None:
    """The keyless ``answer`` lead: production rows, then the count of test callers."""
    production = [r for r in rows if not r.get("test")]
    tests = sum(1 for r in rows if r.get("test") and r["edge_type"] == "calls")
    parts = [_phrase(r) for r in production[:_MAX_ROWS]]
    if tests:
        parts.append(f"{tests} test caller{'s' if tests != 1 else ''}")
    if not parts:
        return None
    return "From the call graph: " + "; ".join(parts) + "."
