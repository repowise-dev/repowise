"""Structural facts for a module page, as data the model reads, not bullets.

A module page used to receive its material as a dozen bulleted lists (key files,
dependencies, owners, health counts) and returned a page shaped like them. The
facts here are what a reader needs to understand how the module works: its
parts and the import edges between them, its key files, exact source for its
central symbols and signatures for the rest (both chosen and budgeted by
``module_excerpts``), what it depends on and what depends on it, and, for a
module that hosts an operation, one traced execution flow through it. They are handed to the model
as one JSON object, together with the diagram the module's shape calls for.

Paths are relative to the module's base directory, which is how a reader thinks
about the module and how the page cites them; :func:`module_base` is the one
place that decides the base, so the sources lint resolves them the same way.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any

import networkx as nx

from repowise.core.analysis.execution_flows import rank_by_importance
from repowise.core.fsutils import strip_dir_prefix as relative_to

# The diagram stays small enough to read without zooming.
_MAX_PARTS = 9
_MAX_PART_EDGES = 12
_MAX_KEY_FILES = 6
_MAX_NEIGHBOURS = 6
_MAX_FLOW_STEPS = 10
# A traced flow earns a sequence diagram only when it starts here and enough
# of it runs here: a flow merely passing through says little about the module.
_MIN_FLOW_STEPS_INSIDE = 3
_MIN_FLOW_STEPS = 4
# Package entry files that re-export their siblings. Their import edges point
# at every part and turn a diagram into a star, so the diagram leaves them out.
_BARREL_STEMS = frozenset({"index", "__init__", "mod", "lib"})
CONNECTIONS_HEADING = "How it connects"


def module_base(files: Iterable[str]) -> str:
    """The deepest directory holding every one of *files*, ``""`` for the root."""
    parents = [PurePosixPath(f).parent.parts for f in files]
    if not parents:
        return ""
    common: list[str] = []
    for level in zip(*parents, strict=False):
        if len(set(level)) != 1:
            break
        common.append(level[0])
    return "/".join(common)


def _part_of(path: str, base: str) -> str:
    """The module part a file belongs to: its first segment below *base*."""
    rest = relative_to(path, base)
    head, _, tail = rest.partition("/")
    return f"{head}/" if tail else head


def _area(path: str) -> str:
    """How a file outside the module is named: its directory."""
    parent = str(PurePosixPath(path).parent)
    return "(root)" if parent == "." else f"{parent}/"


def _import_edges(graph: Any, members: set[str]) -> Iterable[tuple[str, str, dict]]:
    for source in sorted(members):
        if source not in graph:
            continue
        for _, target, data in graph.out_edges(source, data=True):
            if data.get("edge_type") == "imports":
                yield source, target, data


def _flow_steps(
    flows: Sequence[Any], graph: Any, members: set[str], base: str
) -> list[dict[str, str]]:
    """The most important traced flow that starts in this module and runs in it, as steps.

    Importance is :func:`rank_by_importance`, the order every page picks its
    flow by: the operation that drives the most code. Ranking by the parts a
    flow crosses instead drew a niche command on the CLI page because its one
    path happened to touch more directories than the main commands did.
    """
    best = next(
        (
            flow
            for flow in rank_by_importance(flows)
            if _runs_here(list(getattr(flow, "trace", []) or []), members)
        ),
        None,
    )
    if best is None:
        return []
    steps = []
    for node in best.trace[:_MAX_FLOW_STEPS]:
        file, _, symbol = node.partition("::")
        data = graph.nodes[node] if node in graph else {}
        inside = file in members
        if steps and steps[-1]["call"] == (data.get("name") or symbol or file):
            continue  # the same call back to back is one step
        steps.append(
            {
                "call": data.get("name") or symbol or file,
                "file": relative_to(file, base) if inside else file,
                "part": _part_of(file, base) if inside else f"outside: {_area(file)}",
            }
        )
    return steps


def _runs_here(trace: Sequence[str], members: set[str]) -> bool:
    """Whether a trace starts in the module and enough of it stays there to draw."""
    inside = sum(1 for n in trace if n.split("::")[0] in members)
    return (
        bool(trace)
        and trace[0].split("::")[0] in members
        and inside >= _MIN_FLOW_STEPS_INSIDE
        and len(trace) >= _MIN_FLOW_STEPS
    )


def connections_markdown(facts: dict[str, Any]) -> str | None:
    """The page's "How it connects" section, from the import graph rather than prose.

    A model asked for this paragraph restated the opening and listed neighbour
    names it read from these same two fields; rendering the fields directly
    says the same thing exactly, and costs no tokens. ``None`` for a module
    with no neighbours, so the section is left out rather than left empty.
    """
    lines = [
        f"- {label}: " + ", ".join(_area_ref(row["area"]) for row in facts.get(key) or ())
        for key, label in (("used_by", "Used by"), ("depends_on", "Depends on"))
        if facts.get(key)
    ]
    return "\n".join(lines) or None


def _area_ref(area: str) -> str:
    return "the repository root" if area == "(root)" else f"`{area}`"


def _ranked(counts: Counter[Any]) -> list[tuple[Any, int]]:
    """Highest count first, ties by key: the facts are part of the prompt, and a
    prompt that changes between runs defeats reuse of the page written from it."""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _public_api(rows: Sequence[dict]) -> list[dict]:
    """The assembler's budgeted Public API: rows with a signature in full, then
    the names past the budget grouped by file, so every name costs one word."""
    full = [
        {k: e[k] for k in ("name", "kind", "file", "signature", "doc", "alias_of") if e.get(k)}
        for e in rows
        if e.get("signature")
    ]
    bare: dict[str, list[str]] = {}
    for e in rows:
        if not e.get("signature"):
            alias = f" (another name for {e['alias_of']})" if e.get("alias_of") else ""
            bare.setdefault(e["file"], []).append(e["name"] + alias)
    return full + [{"file": f, "names": names} for f, names in bare.items()]


def _is_hub(part_edges: Counter[tuple[str, str]], parts: Sequence[str], has_entry: bool) -> bool:
    """A module run from one place: an entry point, or one part importing most others."""
    if has_entry:
        return True
    reach: dict[str, set[str]] = defaultdict(set)
    for a, b in part_edges:
        reach[a].add(b)
    needed = max(3, math.ceil(0.6 * (len(parts) - 1)))
    return any(len(targets) >= needed for targets in reach.values())


def build_module_facts(
    ctx: Any,
    file_contexts: Sequence[Any],
    graph: Any,
    flows: Sequence[Any] = (),
) -> dict[str, Any]:
    """The JSON-ready facts a module page is written from."""
    graph = graph if graph is not None else nx.DiGraph()
    members = set(ctx.files)
    base = module_base(ctx.files)
    by_part: Counter[str] = Counter(_part_of(f, base) for f in ctx.files)

    part_edges: Counter[tuple[str, str]] = Counter()
    edge_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    depends_on: Counter[str] = Counter()
    for source, target, data in _import_edges(graph, members):
        if target in members:
            if PurePosixPath(source).stem in _BARREL_STEMS:
                continue
            pair = (_part_of(source, base), _part_of(target, base))
            if pair[0] != pair[1]:
                part_edges[pair] += 1
                edge_names[pair].update(sorted(data.get("imported_names") or [])[:4])
        elif "::" not in target:
            depends_on[_area(target)] += 1
    used_by: Counter[str] = Counter()
    for target in sorted(members):
        if target not in graph:
            continue
        for source, _, data in graph.in_edges(target, data=True):
            if (
                data.get("edge_type") == "imports"
                and source not in members
                and "::" not in source
                and not graph.nodes[source].get("is_test")
            ):
                used_by[_area(source)] += 1

    # The busiest parts, so a wide module still draws a readable diagram.
    weight: Counter[str] = Counter()
    for (a, b), n in part_edges.items():
        weight[a] += n
        weight[b] += n
    parts = sorted(by_part, key=lambda p: (-weight[p], -by_part[p], p))[:_MAX_PARTS]
    shown = set(parts)
    edges = [
        {"from": a, "to": b, "imports": n, "names": sorted(edge_names[(a, b)])[:4]}
        for (a, b), n in _ranked(part_edges)
        if a in shown and b in shown
    ][:_MAX_PART_EDGES]

    flow = _flow_steps(flows, graph, members, base)
    hub = _is_hub(part_edges, parts, bool(ctx.entry_points))
    # Parts only earn a flowchart when enough of them are connected; isolated
    # boxes tell a reader nothing an ordinary list would not.
    connected = {p for e in edges for p in (e["from"], e["to"])}
    if flow and hub:
        diagram = "sequenceDiagram"
    elif len(connected) >= 3 and len(edges) >= 2:
        diagram = "flowchart"
    else:
        diagram = "sequenceDiagram" if flow else "none"

    ranked = sorted(file_contexts, key=lambda fc: (-fc.pagerank_score, fc.file_path))
    key_files = [
        {
            "path": relative_to(fc.file_path, base),
            "summary": (ctx.file_summaries.get(fc.file_path) or "").strip(),
            "entry_point": fc.is_entry_point,
        }
        for fc in ranked[:_MAX_KEY_FILES]
    ]

    facts: dict[str, Any] = {
        "module": ctx.title,
        "base_directory": base or "(repository root)",
        "language": ctx.language,
        "files": len(ctx.files),
        "shape": "hub" if hub else "parts",
        "diagram": diagram,
        "parts": [{"part": p, "files": by_part[p]} for p in parts],
        "parts_not_shown": max(0, len(by_part) - len(parts)),
        "imports_between_parts": edges,
        "traced_flow": flow,
        "key_files": key_files,
        "entry_points": [relative_to(p, base) for p in ctx.entry_points],
        "public_api": _public_api(ctx.public_api),
        "public_api_not_listed": ctx.public_api_omitted,
        "code_excerpts": [{**e, "file": relative_to(e["file"], base)} for e in ctx.code_excerpts],
        "declared": ctx.declared_files,
        "depends_on": [
            {"area": a, "imports": n} for a, n in _ranked(depends_on)[:_MAX_NEIGHBOURS]
        ],
        "used_by": [{"area": a, "imports": n} for a, n in _ranked(used_by)[:_MAX_NEIGHBOURS]],
        "external_systems": [s["name"] for s in ctx.external_systems[:8]],
        "decisions": [
            {"title": d.get("title", ""), "decision": d.get("decision", ""), "why": d.get("rationale", "")}
            for d in ctx.decision_records[:3]
        ],
        "child_pages": ctx.child_pages,
        "packages": [p["path"] for p in ctx.packages],
    }
    # Empty keys are noise the model has to read past.
    return {k: v for k, v in facts.items() if v not in ([], "", None, 0)}
