"""Onboarding subkind: Key Concepts.

The abstractions and vocabulary this codebase uses - the mental model
needed to read the code. Not a glossary dump but a narrative that
identifies 4-6 load-bearing concepts and the architectural clusters they
belong to.

A concept is something many *other* files depend on, so importance is
measured on the SYMBOL graph (cross-file caller count, symbol-level
PageRank, export marker), not on the PageRank of the file a symbol
happens to live in. Ranking a symbol by its file's importance surfaced
whole leaf clusters (every method of one registry class) as "the core
concepts"; symbol-level signals surface the types the rest of the system
actually reaches for.

Gate: at least 4 symbols survive filtering and ranking. Below that, the
page would be guessing at "core concepts."
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_KEY_CONCEPTS, SLOT_TITLES

_GATE_MIN_CONCEPTS = 4
_TOP_CONCEPTS = 6
_MAX_DECISION_RECORDS = 6
_MAX_COMMUNITY_LABELS = 6

# Symbol kinds that read as domain nouns - a concept is usually a thing,
# not an action. Preferred over functions/methods when both are available,
# but never required: in function-first languages (Go, C) the fallback
# fills concept slots with the most-depended-on functions instead.
_NOUN_KINDS = frozenset(
    {"class", "interface", "enum", "type_alias", "trait", "struct", "protocol", "dataclass"}
)
# Kinds that are never concepts: the synthetic per-file ``__module__``
# symbol, and loose values (re-exported constants, module globals).
_SKIP_KINDS = frozenset({"module", "variable", "constant"})
# Verb-shaped names that are plumbing, not concepts, regardless of how many
# callers they have. Kept deliberately small and generic so it does not
# swallow real domain functions in a function-first codebase.
_TRIVIAL_NAMES = frozenset(
    {"get", "set", "run", "main", "new", "init", "of", "to", "call", "setup", "wrap"}
)
# Symbol-graph edge types that mean "depends on": a call, or a heritage
# link (subclass / interface implementation).
_CONCEPT_EDGE_TYPES = frozenset({"calls", "extends", "implements"})
# Relationship edge types rendered into "How they connect" - heritage and
# calls plus resolved imports, so the LLM states real links, not guesses.
_RELATION_EDGE_TYPES = frozenset({"calls", "extends", "implements", "imports"})


@dataclass
class ConceptSymbol:
    """A candidate "core concept" symbol with the context needed to write
    about it without re-reading source."""

    name: str
    kind: str
    file_path: str
    docstring: str = ""
    cluster: str = ""
    cross_file_callers: int = 0


@dataclass
class ConceptRelation:
    """One grounded edge between two chosen concepts, drawn from the graph
    so "How they connect" states real links instead of inventing them."""

    source: str
    target: str
    kind: str  # calls | extends | implements | imports


@dataclass
class KeyConceptsContext:
    repo_name: str
    concept_symbols: list[ConceptSymbol] = field(default_factory=list)
    relations: list[ConceptRelation] = field(default_factory=list)
    community_labels: list[str] = field(default_factory=list)
    decision_titles: list[str] = field(default_factory=list)
    layer_order: list[str] = field(default_factory=list)


def _resolve_community_labels(graph_builder: Any) -> dict[int, str]:
    labels: dict[int, str] = {}
    try:
        info = graph_builder.community_info() or {}
        items = info.items() if hasattr(info, "items") else ()
        for cid, ci in items:
            label = getattr(ci, "label", "") or ""
            if label:
                labels[int(cid)] = label
    except Exception:
        pass
    return labels


def _file_to_layer(signals: OnboardingSignals) -> dict[str, str]:
    """Map each file path to its KG layer name (the human-facing cluster).

    Empty when the repo has no curated knowledge graph; callers fall back
    to community labels, then to the file's directory.
    """
    out: dict[str, str] = {}
    for layer in signals.kg_layers:
        name = str(layer.get("name", "")).strip()
        if not name:
            continue
        for nid in layer.get("nodeIds", []) or []:
            if isinstance(nid, str) and nid.startswith("file:"):
                out[nid[len("file:") :]] = name
    return out


@dataclass
class _GraphSignals:
    """Per-symbol importance signals plus node metadata read off the graph.

    ``nodes`` maps each symbol node id to its attributes, so the builder can
    draw candidates straight from the full graph rather than depending on the
    ``parsed_files`` passed into generation. That list is only the CHANGED
    files on an incremental ``update`` (empty when nothing changed), so a
    parsed-files-only builder would produce a partial concept set (or none) on
    every update; the graph always carries the whole repo.
    """

    callers: dict[str, int] = field(default_factory=dict)
    pagerank: dict[str, float] = field(default_factory=dict)
    nodes: dict[str, dict] = field(default_factory=dict)
    test_files: set[str] = field(default_factory=set)


def _symbol_signals(graph_builder: Any) -> _GraphSignals:
    """Pull per-symbol importance signals + node metadata off the graph.

    Degrades to empty on any failure (a rehydrated fast-mode graph may carry
    no symbol nodes); the builder then falls back to ``parsed_files`` and
    file-level PageRank so small repos still get a page.
    """
    out = _GraphSignals()
    try:
        graph = graph_builder.graph()
    except Exception:
        return out

    file_of: dict[str, str] = {}
    for node_id, data in graph.nodes(data=True):
        node_type = data.get("node_type")
        if node_type == "file":
            if data.get("is_test"):
                out.test_files.add(node_id)
            continue
        if node_type != "symbol":
            continue
        path = data.get("file_path", "")
        file_of[node_id] = path
        out.nodes[node_id] = {
            "name": data.get("name", ""),
            "kind": str(data.get("kind", "symbol")),
            "file_path": path,
            "visibility": data.get("visibility", "public"),
            "docstring": (data.get("docstring") or "").strip()[:300],
            "is_exported": bool(data.get("is_exported_symbol", False)),
        }

    # Cross-file caller count = in-edges of a call/heritage type whose source
    # lives in a different file. "Many OTHER files depend on it" is the
    # signal that separates a concept from a busy local helper.
    for src, dst, data in graph.edges(data=True):
        if data.get("edge_type") not in _CONCEPT_EDGE_TYPES:
            continue
        if src not in file_of or dst not in file_of:
            continue
        if file_of[src] != file_of[dst]:
            out.callers[dst] = out.callers.get(dst, 0) + 1

    try:
        out.pagerank = dict(graph_builder.symbol_pagerank())
    except Exception:
        out.pagerank = {}
    return out


def _relations_among(
    graph_builder: Any, chosen: list[ConceptSymbol], id_by_name: dict[str, str]
) -> list[ConceptRelation]:
    """Extract the real edge subgraph among the chosen concepts.

    Only edges where both endpoints are chosen concepts are kept, so the
    "How they connect" section is grounded in dependencies that exist.
    """
    chosen_ids = {id_by_name[c.name] for c in chosen if c.name in id_by_name}
    name_by_id = {id_by_name[c.name]: c.name for c in chosen if c.name in id_by_name}
    if len(chosen_ids) < 2:
        return []
    try:
        graph = graph_builder.graph()
    except Exception:
        return []
    seen: set[tuple[str, str, str]] = set()
    relations: list[ConceptRelation] = []
    for src, dst, data in graph.edges(data=True):
        etype = data.get("edge_type")
        if etype not in _RELATION_EDGE_TYPES:
            continue
        if src not in chosen_ids or dst not in chosen_ids or src == dst:
            continue
        key = (name_by_id[src], name_by_id[dst], etype)
        if key in seen:
            continue
        seen.add(key)
        relations.append(
            ConceptRelation(source=name_by_id[src], target=name_by_id[dst], kind=etype)
        )
    return relations


def _is_trivial(name: str, kind: str) -> bool:
    """Drop dunders, constructors, tiny names, and trivial accessors."""
    if name.startswith("__") and name.endswith("__"):
        return True
    if name == "__init__" or len(name) <= 2:
        return True
    if name.lower() in _TRIVIAL_NAMES:
        return True
    # Classic getter/setter accessors on a class are not concepts. Scoped to
    # methods so module-level functions like ``get_session`` stay eligible in
    # function-first codebases.
    return kind == "method" and (
        name.startswith(("get_", "set_")) or (name[:3] in ("get", "set") and name[3:4].isupper())
    )


def _select(
    primary: list[ConceptSymbol],
    top: int,
    *,
    filler: list[ConceptSymbol] | None = None,
    min_count: int = 0,
) -> list[ConceptSymbol]:
    """Pick up to *top* concepts, importance-ordered, spreading across
    clusters and files so no single corner owns the page.

    Only *primary* candidates (the ones with a real importance signal) count
    toward *top*; a concept page of five load-bearing types beats one padded
    to six with a leaf helper. *filler* candidates (zero-signal symbols) are
    pulled in only if fewer than *min_count* primaries exist, so a small or
    thinly-connected repo still clears the gate.

    Progressive relaxation: tight caps first (prefer spread), then loosen so a
    single-layer or single-file small repo still fills. Nouns are offered
    before functions/methods within each pass.
    """
    layer_cap = max(math.ceil(top / 2), 1)
    chosen: list[ConceptSymbol] = []
    picked: set[int] = set()
    per_file: dict[str, int] = {}
    per_cluster: dict[str, int] = {}

    def take(pool: list[ConceptSymbol], limit: int, file_cap: int, cluster_cap: int) -> None:
        for c in pool:
            if len(chosen) >= limit:
                return
            if id(c) in picked:
                continue
            if per_file.get(c.file_path, 0) >= file_cap:
                continue
            if per_cluster.get(c.cluster, 0) >= cluster_cap:
                continue
            chosen.append(c)
            picked.add(id(c))
            per_file[c.file_path] = per_file.get(c.file_path, 0) + 1
            per_cluster[c.cluster] = per_cluster.get(c.cluster, 0) + 1

    def fill(pool: list[ConceptSymbol], limit: int) -> None:
        nouns = [c for c in pool if c.kind in _NOUN_KINDS]
        verbs = [c for c in pool if c.kind not in _NOUN_KINDS]
        # Pass 1-2: one per file, at most half the page from one cluster.
        take(nouns, limit, file_cap=1, cluster_cap=layer_cap)
        take(verbs, limit, file_cap=1, cluster_cap=layer_cap)
        # Pass 3: still one per file, but let a genuinely dominant cluster fill
        # up (a single-layer repo has nowhere else to spread to).
        take(nouns, limit, file_cap=1, cluster_cap=limit)
        take(verbs, limit, file_cap=1, cluster_cap=limit)
        # Pass 4 (last resort, tiny repos): allow a second concept per file.
        take(nouns, limit, file_cap=2, cluster_cap=limit)
        take(verbs, limit, file_cap=2, cluster_cap=limit)

    fill(primary, top)
    if len(chosen) < min_count and filler:
        fill(filler, min_count)
    return chosen


def _iter_raw_symbols(signals: OnboardingSignals, gs: _GraphSignals) -> list[dict]:
    """Yield the raw symbol records to rank, keyed by node id.

    Prefer the full symbol graph (it holds the WHOLE repo, unlike the possibly
    partial ``parsed_files`` on an incremental update). Fall back to
    ``parsed_files`` only when the graph carries no symbol nodes.
    """
    if gs.nodes:
        return [{"id": nid, **meta} for nid, meta in gs.nodes.items()]
    raw: list[dict] = []
    for pf in signals.parsed_files:
        for sym in pf.symbols:
            raw.append(
                {
                    "id": sym.id,
                    "name": sym.name,
                    "kind": str(getattr(sym, "kind", "symbol")),
                    "file_path": pf.file_info.path,
                    "visibility": getattr(sym, "visibility", "public"),
                    "docstring": (getattr(sym, "docstring", "") or "").strip()[:300],
                    "is_exported": bool(getattr(sym, "is_exported_symbol", False)),
                }
            )
    return raw


def _build(signals: OnboardingSignals) -> KeyConceptsContext | None:
    labels_by_cid = _resolve_community_labels(signals.graph_builder)
    layer_of = _file_to_layer(signals)
    gs = _symbol_signals(signals.graph_builder)

    test_files = set(gs.test_files)
    test_files |= {
        pf.file_info.path for pf in signals.parsed_files if getattr(pf.file_info, "is_test", False)
    }

    def cluster_of(path: str) -> str:
        if path in layer_of:
            return layer_of[path]
        cid = signals.community.get(path)
        if cid is not None:
            label = labels_by_cid.get(int(cid))
            if label:
                return label
        # No graph clusters: the parent directory is a coarse but real
        # anti-concentration axis.
        return os.path.dirname(path) or path

    # One candidate per public, non-trivial symbol, joined to its symbol-graph
    # signals. Test files are excluded - their helpers are not repo concepts.
    candidates: list[ConceptSymbol] = []
    id_by_name: dict[str, str] = {}
    seen_names: set[str] = set()
    scored: list[tuple[int, float, int, int, ConceptSymbol]] = []
    any_symbol_signal = False
    for raw in _iter_raw_symbols(signals, gs):
        path = raw["file_path"]
        if path in test_files:
            continue
        if raw["visibility"] != "public":
            continue
        kind = raw["kind"]
        if kind in _SKIP_KINDS:
            continue
        name = raw["name"]
        if not name or _is_trivial(name, kind):
            continue
        sym_id = raw["id"]
        xcallers = gs.callers.get(sym_id, 0)
        spr = gs.pagerank.get(sym_id, 0.0)
        if xcallers or spr:
            any_symbol_signal = True
        concept = ConceptSymbol(
            name=name,
            kind=kind,
            file_path=path,
            docstring=raw["docstring"],
            cluster=cluster_of(path),
            cross_file_callers=xcallers,
        )
        scored.append(
            (
                xcallers,
                spr,
                1 if raw["is_exported"] else 0,
                1 if concept.docstring else 0,
                concept,
            )
        )
        id_by_name.setdefault(name, sym_id)

    if not scored:
        return None

    if any_symbol_signal:
        # Importance: cross-file callers, then symbol PageRank, then export
        # marker, then presence of a docstring (a deliberate public surface).
        scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
    else:
        # No resolved symbol edges (thin / rehydrated graph): fall back to the
        # file's PageRank so a page still generates on small repos.
        scored.sort(
            key=lambda t: (signals.pagerank.get(t[4].file_path, 0.0), t[3]),
            reverse=True,
        )

    # De-duplicate by concept name (re-exports) before selection. When symbol
    # signals are available, split off "filler" - symbols with no cross-file
    # callers and no export marker are not something the rest of the system
    # depends on, so they pad the gate but never a full page.
    filler: list[ConceptSymbol] = []
    for _, _, exported_flag, _, concept in scored:
        if concept.name in seen_names:
            continue
        seen_names.add(concept.name)
        if any_symbol_signal and concept.cross_file_callers == 0 and not exported_flag:
            filler.append(concept)
        else:
            candidates.append(concept)

    concept_symbols = _select(
        candidates, _TOP_CONCEPTS, filler=filler, min_count=_GATE_MIN_CONCEPTS
    )
    if len(concept_symbols) < _GATE_MIN_CONCEPTS:
        return None

    relations = _relations_among(signals.graph_builder, concept_symbols, id_by_name)

    community_labels = sorted(set(labels_by_cid.values()))[:_MAX_COMMUNITY_LABELS]
    decision_titles = [
        str(d.get("title", "")).strip()
        for d in signals.decisions_all[:_MAX_DECISION_RECORDS]
        if d.get("title")
    ]

    return KeyConceptsContext(
        repo_name=signals.repo_name,
        concept_symbols=concept_symbols,
        relations=relations,
        community_labels=community_labels,
        decision_titles=decision_titles,
        layer_order=list(signals.layer_order),
    )


register(
    SubkindSpec(
        slot=SLOT_KEY_CONCEPTS,
        title=SLOT_TITLES[SLOT_KEY_CONCEPTS],
        template="key_concepts.j2",
        build_context=_build,
    )
)
