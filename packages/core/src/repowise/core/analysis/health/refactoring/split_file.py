"""Split File detector — the file-level analog of Extract Class.

Extract Class partitions a *class* by its methods' cohesion (LCOM4
components). Split File partitions a *file* by its top-level symbols'
cohesion: a 2,000-line module of 40 loosely-related top-level functions is
the single most common thing developers stare at and call "needs splitting",
yet no class-internal or edge-level refactoring fires on it.

The detector is language-agnostic — it reads only the already-built graph
(``defines`` / ``calls`` edges on ``ctx.graph``), exactly like Move Method
and Break Cycle. No per-language module, no re-parse, no indexing change.

Algorithm (all signals from ``ctx.graph`` for v1):

- **Nodes:** the file's top-level symbols. A class collapses to one node
  (its methods roll up into it); each top-level function is a node; nested
  functions roll up into their owner.
- **Edges (weighted, strongest -> weakest):**
  1. direct intra-file call (A calls B) -> ``w = 3`` — they belong together;
  2. co-change affinity (A and B's line ranges are touched by overlapping
     commits) -> ``w = 2 x Jaccard(commits_a, commits_b)`` — "these always
     change together, keep them together". Read from the file's blame index
     at zero index cost; absent when no ``blame_index`` is threaded in.
  3. shared local helper (A and B both call a third local symbol) ->
     ``w = 2`` per shared helper — cohesion without a direct A<->B edge;
  4. shared external-import surface (A and B lean on the same *imported
     names*) -> ``w = 1`` per shared imported name — true "same dependency
     surface", recovered from the file's ``imports`` edges at zero index cost.
     Falls back to the older cross-module affinity proxy (A and B both call
     into the same *foreign module* -> ``w = 1`` per shared module) when a
     symbol's imported-name surface is empty (lightweight-tier languages).
- **Partition:** community detection (Leiden via the shared
  ``communities`` module, Louvain fallback) on this weighted subgraph. A
  shared-utility *spine* (a local helper most symbols call) is collapsed
  into a residual ``core`` group before clustering so it does not glue
  everything together.
- **The decomposability gate (the precision story):** emit a suggestion only
  when the partition has **high modularity** — the inter-group cut is small
  relative to intra-group cohesion. A big-but-cohesive file (one giant state
  machine, a generated registry) yields a low-modularity partition and
  produces *nothing*. Better ten great splits than two hundred maybes.

Output is "split into these N files; here are the import edits in the M
dependent files" — the blast-radius column nobody else has. Splitting Go
files in the same package is near-zero blast (no import edits); Python/TS
need a back-compat re-export shim, surfaced as ``shim_required``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from repowise.core.analysis.execution_graph import is_reliable_call_edge

from ....test_paths import is_test_related_path
from ...dead_code.file_reachability import BARREL_FILENAMES
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# Edge weights over the intra-file symbol graph (see module docstring).
_DIRECT_CALL_WEIGHT = 3.0
# Co-change weight is scaled by the commit-set Jaccard (0..1), so it peaks at
# this value for two symbols that always change together and decays toward the
# floor below. Tunable on dogfood evidence.
_COCHANGE_WEIGHT = 2.0
# Only symbols whose commit sets overlap by at least this Jaccard get a
# co-change edge. Same-file symbols share commit history freely, so an unfloored
# (or low-floored) edge densifies the graph into a uniform glue that depresses
# the partition modularity without sharpening any group. "These always change
# together" means sharing the majority of their history, so the floor sits at
# half. Dogfood on .repowise: an unfloored edge dropped mean modularity 0.42 ->
# 0.40 and cut high-confidence splits from 14 to 6; at 0.5 the mean returns to
# 0.42 (within noise of the call-only baseline) while co-change still reshapes
# the strongly-coupled files.
_COCHANGE_MIN_JACCARD = 0.5
_SHARED_HELPER_WEIGHT = 2.0
# Per shared imported name (the true dependency-surface signal) and, as the
# fallback when a symbol has no imported-name surface, per shared foreign module.
_SHARED_IMPORT_WEIGHT = 1.0
_SHARED_MODULE_WEIGHT = 1.0

# Floors — gate on decomposability, not size, but a tiny or short file is
# never worth a split suggestion regardless of how it partitions.
_MIN_FILE_NLOC = 300
_MIN_SYMBOLS = 8

# A split needs at least this many substantive resulting groups (a lone helper
# split out is not worth a suggestion). A group is substantive at >= this many
# symbols; smaller communities fold into the residual ``core``.
_MIN_GROUPS = 2
_MIN_GROUP_SYMBOLS = 2

# The decomposability gate: the weighted partition must separate this cleanly
# (Newman modularity over the weighted graph). Tuned toward suppression — a
# cohesive big file scores well below this and yields nothing. Tunable on
# dogfood evidence.
_MIN_MODULARITY = 0.30

# Route single-dominant-class files to Extract Class instead: if one class is
# more than this fraction of the file, splitting the file *is* splitting that
# class, which Extract Class already covers. The two compose, never overlap.
_DOMINANT_CLASS_FRACTION = 0.70

# A local symbol called by at least this fraction of the file's symbols (and
# by at least 3 of them) is a shared-utility spine: it connects every group,
# so it is pulled into the residual ``core`` rather than gluing the partition.
_SPINE_CALLER_FRACTION = 0.6
_SPINE_MIN_CALLERS = 3

# Confidence: a very clean separation is high; a marginal-but-passing one is
# medium (still worth surfacing, ranks lower).
_HIGH_CONFIDENCE_MODULARITY = 0.45


def _is_generated_path(path: str) -> bool:
    """Generated / vendored / append-only code: a migration or a barrel
    re-export file must stay self-contained, so it is never a split target."""
    p = path.lower().replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    return (
        "/migrations/" in p
        or "/alembic/versions/" in p
        or "/node_modules/" in p
        or "/vendor/" in p
        or "/__generated__/" in p
        or ".generated." in base
        or base.endswith(".min.js")
        # Barrel / package-init re-export files: nothing of substance to split.
        or base in BARREL_FILENAMES
    )


def _is_skippable_path(path: str, language: str | None = None) -> bool:
    return is_test_related_path(path, language) or _is_generated_path(path)


def _line_range(data: dict) -> tuple[int, int] | None:
    start = data.get("start_line")
    end = data.get("end_line")
    if isinstance(start, int) and isinstance(end, int) and end >= start:
        return start, end
    return None


def _node_span(data: dict) -> int:
    span = _line_range(data)
    return span[1] - span[0] + 1 if span else 0


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _split_stem_ext(path: str) -> tuple[str, str]:
    base = _basename(path)
    if "." in base:
        stem, ext = base.rsplit(".", 1)
        return stem, "." + ext
    return base, ""


def _directory(path: str) -> str:
    norm = path.replace("\\", "/")
    return norm.rsplit("/", 1)[0] if "/" in norm else ""


# Generic verbs / connectives that name no responsibility — skipped when
# voting for a group's name so ``get_repo`` / ``build_story`` don't all read as
# "get" / "build".
_NAME_STOPWORDS = frozenset(
    {
        "get",
        "set",
        "build",
        "make",
        "run",
        "load",
        "save",
        "read",
        "write",
        "create",
        "update",
        "fetch",
        "handle",
        "compute",
        "render",
        "the",
        "and",
        "for",
        "with",
        "into",
        "from",
        "all",
        "new",
        "raw",
    }
)


def _name_tokens(name: str) -> set[str]:
    """The meaningful ``snake_case`` tokens of *name*: no stopwords, digits or short tokens."""
    return {
        tok
        for tok in name.lower().lstrip("_").split("_")
        if len(tok) >= 4 and tok not in _NAME_STOPWORDS and not tok.isdigit()
    }


def _dominant_token(names: list[str]) -> str:
    """A group's name by *plurality vote* over its symbols' name tokens.

    The most frequent meaningful ``snake_case`` token across the group wins
    (``filter_dicts``, ``filter_path``, ``is_excluded`` -> ``filter``). A
    plurality is far more robust than a shared prefix, which one outlier kills.
    Stopword verbs and short tokens are ignored; each symbol votes for a token
    at most once. Returns ``""`` when no token is shared by >= 2 symbols.
    """
    if len(names) < 2:
        return ""
    counts: Counter[str] = Counter()
    for name in names:
        counts.update(_name_tokens(name))
    if not counts:
        return ""
    token, votes = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return token if votes >= 2 else ""


def _label_identifier(label: str) -> str:
    """The last path segment of a module label, stripped of its extension and
    sanitized to an identifier."""
    seg = label.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    seg = _split_stem_ext(seg)[0]
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in seg).strip("_")


def _module_label(
    foreign_of: dict[str, set[str]], members: list[str], self_segments: set[str]
) -> str:
    """The group's most-called foreign module label as a file name, or ``""``."""
    labels = Counter(lab for m in members for lab in foreign_of.get(m, set()))
    if not labels:
        return ""
    best = min(labels, key=lambda lab: (-labels[lab], lab))
    cleaned = _label_identifier(best)
    # Reject uninformative labels: too short, carrying a community-dedup
    # digit suffix, or naming the file's own namespace (tells you
    # nothing new about the group).
    if (
        len(cleaned) >= 3
        and not any(ch.isdigit() for ch in cleaned)
        and cleaned.lower() not in self_segments
    ):
        return cleaned.lower()
    return ""


def _leiden_assignment(wg: Any) -> dict[str, int]:
    """The shared Leiden/Louvain partition of *wg*, or ``{}`` when it fails."""
    try:
        from repowise.core.analysis.communities import _partition

        raw, _algo = _partition(wg)
        return {n: int(c) for n, c in raw.items()}
    except Exception:
        return {}


def _component_assignment(wg: Any) -> dict[str, int] | None:
    """Connected components of *wg* as communities, or ``None`` when they cannot be computed."""
    try:
        import networkx as nx

        return {n: cid for cid, comp in enumerate(nx.connected_components(wg)) for n in comp}
    except Exception:
        return None


def _partition_weighted(wg: Any) -> dict[str, int]:
    """Community assignment over the weighted intra-file graph *wg*.

    Reuses the repo's shared Leiden/Louvain partitioner (seeded, deterministic)
    and degrades to connected components on any failure or when *wg* has no
    edges. Every node of *wg* is always assigned.
    """
    nodes = sorted(wg.nodes())
    if wg.number_of_edges() == 0:
        return {n: i for i, n in enumerate(nodes)}

    assignment = _leiden_assignment(wg)
    if not assignment:
        assignment = _component_assignment(wg)
        if assignment is None:
            return {n: i for i, n in enumerate(nodes)}

    # Any node the partitioner dropped (Leiden can omit isolates) gets its own
    # community so it lands in the residual rather than vanishing.
    next_cid = max(assignment.values(), default=-1) + 1
    for n in nodes:
        if n not in assignment:
            assignment[n] = next_cid
            next_cid += 1
    return assignment


def _top_level_nodes(defined: dict[str, dict], owner_of: dict[str, str]) -> list[str]:
    """The file's split units: the outermost class and function owners, sorted."""
    return sorted(
        {
            owner
            for owner in owner_of.values()
            if defined.get(owner, {}).get("kind") in ("class", "function")
            and not defined.get(owner, {}).get("parent_name")
        }
    )


def _has_dominant_class(defined: dict[str, dict], node_ids: list[str], nloc: int) -> bool:
    return any(
        defined[nid].get("kind") == "class"
        and _node_span(defined[nid]) > _DOMINANT_CLASS_FRACTION * nloc
        for nid in node_ids
    )


def _split_off_spine(
    node_ids: list[str], callers_of: dict[str, set[str]]
) -> tuple[set[str], list[str]]:
    """Separate the shared-utility spine from the nodes left to cluster."""
    min_callers = max(_SPINE_MIN_CALLERS, _SPINE_CALLER_FRACTION * len(node_ids))
    spine = {callee for callee, callers in callers_of.items() if len(callers) >= min_callers}
    return spine, [nid for nid in node_ids if nid not in spine]


def _substantive_groups(assignment: dict[str, int]) -> tuple[dict[int, list[str]], list[list[str]]]:
    """Members per community, and the communities big enough to become a file
    (largest first)."""
    groups_map: dict[int, list[str]] = defaultdict(list)
    for node, cid in assignment.items():
        groups_map[cid].append(node)
    substantive = sorted(
        (sorted(members) for members in groups_map.values() if len(members) >= _MIN_GROUP_SYMBOLS),
        key=lambda members: (-len(members), members[0]),
    )
    return groups_map, substantive


def _residual_ids(node_ids: list[str], spine: set[str], groups: list[list[str]]) -> list[str]:
    """The spine plus every symbol not placed in a substantive group."""
    placed = {m for g in groups for m in g}
    return sorted(spine | {nid for nid in node_ids if nid not in placed and nid not in spine})


def _reliable_callees(graph: Any, sid: str) -> Iterator[str]:
    for _u, callee, edata in graph.out_edges(sid, data=True):
        if callee != sid and is_reliable_call_edge(
            edata.get("edge_type"), edata.get("resolution_origin")
        ):
            yield callee


def _defined_by_edges(graph: Any, file_path: str) -> dict[str, dict]:
    if file_path not in graph:
        return {}
    return {
        v: graph.nodes[v]
        for _u, v, data in graph.out_edges(file_path, data=True)
        if data.get("edge_type") == "defines" and _is_split_symbol(graph.nodes[v])
    }


def _defined_by_prefix(graph: Any, file_path: str) -> dict[str, dict]:
    prefix = f"{file_path}::"
    return {
        node_id: data
        for node_id, data in graph.nodes(data=True)
        if _is_split_symbol(data) and node_id.startswith(prefix)
    }


def _is_split_symbol(data: dict) -> bool:
    return data.get("node_type") == "symbol" and data.get("kind") != "module"


def _calling_files(graph: Any, file_path: str, defined: dict[str, dict]) -> set[str]:
    """Other files holding a reliable call into one of *defined*."""
    out: set[str] = set()
    for sid in defined:
        for u, _v, edata in graph.in_edges(sid, data=True):
            f = graph.nodes.get(u, {}).get("file_path")
            if f and f != file_path and is_reliable_call_edge(
                edata.get("edge_type"), edata.get("resolution_origin")
            ):
                out.add(f)
    return out


def _importing_files(graph: Any, file_path: str) -> set[str]:
    if file_path not in graph:
        return set()
    return {
        u
        for u, _v, edata in graph.in_edges(file_path, data=True)
        if edata.get("edge_type") == "imports" and u != file_path
    }


def _imported_names(graph: Any, src_file: str, dst_file: str) -> set[str]:
    """Imported names on the ``imports`` edge ``src_file -> dst_file`` (the
    ``imported_names`` payload ``builder.py`` aggregates). Empty when the
    edge is missing or carries no names (e.g. lightweight-tier languages)."""
    edata = graph.get_edge_data(src_file, dst_file)
    if not edata or edata.get("edge_type") != "imports":
        return set()
    return {n for n in edata.get("imported_names", ()) if n}


@dataclass
class _CallSignals:
    """Cohesion signals read off one walk of the file's ``calls`` edges.

    ``local_pairs`` are direct local-call pairs, ``callers_of`` maps each local
    helper to its callers, ``foreign_of`` holds each node's foreign module
    labels (the affinity-proxy fallback) and ``imports_of`` its external-import
    surface (the imported names it leans on, read off the file's ``imports``
    edges).
    """

    local_pairs: set[tuple[str, str]] = field(default_factory=set)
    callers_of: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    foreign_of: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    imports_of: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_local_call(self, owner: str, callee_owner: str) -> None:
        self.local_pairs.add((owner, callee_owner))
        self.callers_of[callee_owner].add(owner)

    def add_foreign_call(self, ctx: RefactoringContext, owner: str, callee: str) -> None:
        graph = ctx.graph
        fpath = graph.nodes.get(callee, {}).get("file_path")
        if not fpath or fpath == ctx.file_path:
            return
        self.foreign_of[owner].add(ctx.community_label_map.get(fpath) or fpath)
        # The imported names this file pulls from the callee's
        # file approximate the dependency surface this symbol
        # actually leans on (file-edge granularity; the precise
        # per-call binding-name variant is a deferred index
        # change). Empty on lightweight-tier languages -> the
        # foreign-module proxy carries the signal instead.
        names = _imported_names(graph, ctx.file_path, fpath)
        if names:
            self.imports_of[owner] |= names


@dataclass(frozen=True)
class _FileGraph:
    """The file's symbols, its top-level split units, and the call signals between them."""

    defined: dict[str, dict]
    node_ids: list[str]
    signals: _CallSignals


class _EdgeWeights:
    """Symmetric pair weights over the intra-file symbol graph. Self-pairs and
    spine members never get an edge."""

    def __init__(self, spine: set[str]) -> None:
        self.spine = spine
        self.weights: Counter[tuple[str, str]] = Counter()

    def add(self, a: str, b: str, w: float) -> None:
        if a == b or a in self.spine or b in self.spine:
            return
        self.weights[tuple(sorted((a, b)))] += w


def _add_cochange_edges(edges: _EdgeWeights, commits_of: dict[str, set[str]]) -> int:
    """Co-change affinity: overlapping commit sets keep symbols together."""
    count = 0
    for a, b in combinations(sorted(n for n in commits_of if n not in edges.spine), 2):
        ca, cb = commits_of[a], commits_of[b]
        inter = len(ca & cb)
        jaccard = inter / len(ca | cb)
        if inter and jaccard >= _COCHANGE_MIN_JACCARD:
            edges.add(a, b, _COCHANGE_WEIGHT * jaccard)
            count += 1
    return count


def _add_shared_helper_edges(edges: _EdgeWeights, callers_of: dict[str, set[str]]) -> None:
    """Callers of a common, non-spine local symbol belong together."""
    for callee, callers in callers_of.items():
        if callee in edges.spine:
            continue
        members = sorted(c for c in callers if c not in edges.spine)
        for a, b in combinations(members, 2):
            edges.add(a, b, _SHARED_HELPER_WEIGHT)


def _add_shared_surface_edges(
    edges: _EdgeWeights, cluster_nodes: list[str], signals: _CallSignals
) -> int:
    """Shared external-import surface, with the foreign-module proxy as the
    fallback when a symbol carries no imported-name surface. Returns the number
    of import-name edges."""
    count = 0
    for a, b in combinations(cluster_nodes, 2):
        a_names, b_names = signals.imports_of.get(a), signals.imports_of.get(b)
        if a_names and b_names:
            shared_names = a_names & b_names
            if shared_names:
                edges.add(a, b, _SHARED_IMPORT_WEIGHT * len(shared_names))
                count += 1
            continue
        shared_mod = signals.foreign_of.get(a, set()) & signals.foreign_of.get(b, set())
        if shared_mod:
            edges.add(a, b, _SHARED_MODULE_WEIGHT * len(shared_mod))
    return count


def _weighted_graph(
    cluster_nodes: list[str],
    spine: set[str],
    signals: _CallSignals,
    commits_of: dict[str, set[str]],
) -> tuple[Any, dict[str, int]]:
    """The weighted intra-file symbol graph and how many co-change and
    import-name edges it carries."""
    try:
        import networkx as nx
    except Exception:
        return None, {"cochange_edges": 0, "import_edges": 0}

    # Signals are added strongest first (see module docstring).
    edges = _EdgeWeights(spine)
    for a, b in signals.local_pairs:
        edges.add(a, b, _DIRECT_CALL_WEIGHT)
    cochange_edges = _add_cochange_edges(edges, commits_of)
    _add_shared_helper_edges(edges, signals.callers_of)
    import_edges = _add_shared_surface_edges(edges, cluster_nodes, signals)

    wg = nx.Graph()
    wg.add_nodes_from(sorted(cluster_nodes))
    for (a, b), w in edges.weights.items():
        wg.add_edge(a, b, weight=w)
    return wg, {"cochange_edges": cochange_edges, "import_edges": import_edges}


@dataclass(frozen=True)
class _Partition:
    """A weighted symbol graph whose community partition passed the decomposability gate."""

    graph: Any
    assignment: dict[str, int]
    groups: list[list[str]]
    residual: list[str]
    modularity: float
    signal_counts: dict[str, int]


@register
class SplitFileDetector(RefactoringDetector):
    name = "split_file"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        graph = ctx.graph
        if graph is None or _is_skippable_path(ctx.file_path, ctx.language):
            return []
        if ctx.nloc < _MIN_FILE_NLOC:
            return []

        defined = self._defined_symbols(graph, ctx.file_path)
        owner_of = {sid: self._resolve_owner(ctx.file_path, sid, defined) for sid in defined}
        node_ids = _top_level_nodes(defined, owner_of)
        # A single dominant class is an Extract Class candidate instead.
        if len(node_ids) < _MIN_SYMBOLS or _has_dominant_class(defined, node_ids, ctx.nloc):
            return []

        signals = self._intra_file_signals(ctx, owner_of, set(node_ids))
        fg = _FileGraph(defined, node_ids, signals)
        partition = self._partition_symbols(ctx, fg)
        if partition is None:
            return []
        return [self._suggestion(ctx, fg, partition)]

    def _partition_symbols(self, ctx: RefactoringContext, fg: _FileGraph) -> _Partition | None:
        # Collapse the shared-utility spine into the residual ``core`` so it
        # does not connect every group (the under-split risk).
        spine, cluster_nodes = _split_off_spine(fg.node_ids, fg.signals.callers_of)
        if len(cluster_nodes) < _MIN_SYMBOLS // 2:
            return None
        wg, signal_counts = _weighted_graph(
            cluster_nodes,
            spine,
            fg.signals,
            self._commit_sets(ctx, fg.defined, cluster_nodes),
        )
        if wg is None:
            return None
        assignment = _partition_weighted(wg)
        groups_map, substantive = _substantive_groups(assignment)
        modularity = self._modularity(wg, groups_map)
        if len(substantive) < _MIN_GROUPS or modularity < _MIN_MODULARITY:
            return None
        residual = _residual_ids(fg.node_ids, spine, substantive)
        return _Partition(wg, assignment, substantive, residual, modularity, signal_counts)

    def _suggestion(
        self, ctx: RefactoringContext, fg: _FileGraph, partition: _Partition
    ) -> RefactoringSuggestion:
        groups = self._shape_groups(ctx, fg, partition.groups)
        residual = (
            {"symbols": sorted(self._sym_name(fg.defined, m) for m in partition.residual)}
            if partition.residual
            else None
        )
        intra_edges, cut_edges = self._edge_cut(partition.graph, partition.assignment)
        shim_required = _shim_required(ctx.language)
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=f"{_basename(ctx.file_path)} -> {len(groups)} files",
            line_start=None,
            line_end=None,
            plan={
                "groups": groups,
                "residual": residual,
                "shim_required": shim_required,
            },
            evidence={
                "file_nloc": ctx.nloc,
                "symbol_count": len(fg.node_ids),
                "group_count": len(groups),
                "modularity": round(partition.modularity, 3),
                "intra_edges": intra_edges,
                "cut_edges": cut_edges,
                # Optional: present only when the richer signals fired, so
                # the surface layers can show "kept together by co-change /
                # shared imports" without breaking older plan records.
                **{k: v for k, v in partition.signal_counts.items() if v},
            },
            impact_delta=0.0,
            effort_bucket=effort_bucket(ctx.nloc),
            blast_radius=self._blast_radius(ctx, fg.defined, shim_required=shim_required),
            confidence=(
                "high" if partition.modularity >= _HIGH_CONFIDENCE_MODULARITY else "medium"
            ),
            source_biomarker="",
        )

    # ----- graph reading ---------------------------------------------------

    def _defined_symbols(self, graph: Any, file_path: str) -> dict[str, dict]:
        """Symbol nodes defined in *file_path* via ``defines`` edges (with a
        prefix-scan fallback), excluding the synthetic ``__module__`` node."""
        return _defined_by_edges(graph, file_path) or _defined_by_prefix(graph, file_path)

    def _resolve_owner(self, file_path: str, sid: str, defined: dict[str, dict]) -> str:
        """Walk ``parent_name`` up to the outermost top-level symbol (a method
        rolls up to its class, a nested function to its owner)."""
        cur = sid
        seen: set[str] = set()
        while cur not in seen:
            seen.add(cur)
            data = defined.get(cur, {})
            parent = data.get("parent_name")
            if not parent:
                return cur
            pid = f"{file_path}::{parent}"
            if pid not in defined or pid == cur:
                return cur
            cur = pid
        return cur

    def _intra_file_signals(
        self, ctx: RefactoringContext, owner_of: dict[str, str], node_set: set[str]
    ) -> _CallSignals:
        """Walk ``calls`` edges once and derive the cohesion signals."""
        signals = _CallSignals()
        for sid, owner in owner_of.items():
            if owner not in node_set:
                continue
            for callee in _reliable_callees(ctx.graph, sid):
                callee_owner = owner_of.get(callee)
                if callee_owner is None:
                    signals.add_foreign_call(ctx, owner, callee)
                elif callee_owner in node_set and callee_owner != owner:
                    signals.add_local_call(owner, callee_owner)
        return signals

    def _commit_sets(
        self,
        ctx: RefactoringContext,
        defined: dict[str, dict],
        cluster_nodes: list[str],
    ) -> dict[str, set[str]]:
        """Project each node's ``(start_line, end_line)`` through the file's
        blame index to its set of touching commits — the raw material for the
        co-change edge. Empty (the documented "no signal" outcome) when no
        ``blame_index`` was threaded in or the index has no coverage."""
        idx = ctx.blame_index
        if idx is None or not getattr(idx, "lines", None):
            return {}
        try:
            from repowise.core.ingestion.git_indexer.function_blame import (
                distinct_commits_in_range,
            )
        except Exception:
            return {}
        out: dict[str, set[str]] = {}
        for nid in cluster_nodes:
            span = _line_range(defined.get(nid, {}))
            commits = distinct_commits_in_range(idx, *span) if span else None
            if commits:
                out[nid] = commits
        return out

    @staticmethod
    def _modularity(wg: Any, groups_map: dict[int, list[str]]) -> float:
        if wg.number_of_edges() == 0:
            return 0.0
        try:
            import networkx as nx

            communities = [set(members) for members in groups_map.values()]
            return float(nx.community.modularity(wg, communities, weight="weight"))
        except Exception:
            return 0.0

    @staticmethod
    def _edge_cut(wg: Any, assignment: dict[str, int]) -> tuple[int, int]:
        intra = cut = 0
        for a, b in wg.edges():
            if assignment.get(a) == assignment.get(b):
                intra += 1
            else:
                cut += 1
        return intra, cut

    # ----- output shaping --------------------------------------------------

    def _shape_groups(
        self, ctx: RefactoringContext, fg: _FileGraph, substantive: list[list[str]]
    ) -> list[dict]:
        stem, ext = _split_stem_ext(ctx.file_path)
        directory = _directory(ctx.file_path)
        used: set[str] = {_basename(ctx.file_path)}
        groups: list[dict] = []
        self_segments = {seg.lower() for seg in ctx.file_path.replace("\\", "/").split("/")}
        self_segments.add(stem.lower())
        for members in substantive:
            label = self._group_label(fg, members, self_segments)
            # A group whose symbols share no name token has no honest filename.
            # ``{stem}_part{idx}`` looked like one and named nothing, so the
            # field is absent instead and the surfaces prompt for a name.
            suggested = None
            if label:
                filename = self._unique_filename(label, ext, used)
                suggested = f"{directory}/{filename}" if directory else filename
            groups.append(
                {
                    "name": label or None,
                    "symbols": sorted(self._sym_name(fg.defined, m) for m in members),
                    "suggested_file": suggested,
                }
            )
        return groups

    def _group_label(self, fg: _FileGraph, members: list[str], self_segments: set[str]) -> str:
        """Deterministic file name for a group: the dominant shared name token
        first (a plurality vote — the most semantically meaningful signal),
        else a clean dominant foreign-module label, else ``""`` (the caller
        falls back to ``<file>_partN``).

        Dogfood showed the foreign-module label is frequently the repo's own
        package community (carrying a size suffix like ``repowise (290)``),
        which sanitizes to noise like ``repowise__290``. So the name vote
        leads, and a module label is used only when it is a clean identifier
        that is not already part of the file's own path."""
        token = _dominant_token([self._sym_name(fg.defined, m) for m in members])
        if token and token not in self_segments:
            return token
        return _module_label(fg.signals.foreign_of, members, self_segments) or token

    @staticmethod
    def _unique_filename(stem: str, ext: str, used: set[str]) -> str:
        candidate = f"{stem}{ext}"
        suffix = 2
        while candidate in used:
            candidate = f"{stem}_{suffix}{ext}"
            suffix += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _sym_name(defined: dict[str, dict], sid: str) -> str:
        return defined.get(sid, {}).get("name") or sid.rsplit("::", 1)[-1]

    def _blast_radius(
        self, ctx: RefactoringContext, defined: dict[str, dict], *, shim_required: bool
    ) -> dict[str, Any]:
        """External files that reference this file's symbols (call in-edges) or
        import the file. Import rewrites are zero for the same-package Go case
        (no shim, no edits); for shim languages it is the count of dependents
        whose imports the plan's shim preserves but lists for review."""
        graph = ctx.graph
        dependent_files = _calling_files(graph, ctx.file_path, defined) | _importing_files(
            graph, ctx.file_path
        )
        files = sorted(f for f in dependent_files if f)
        return {
            "dependent_files": files,
            "dependent_count": len(files),
            "import_rewrites": len(files) if shim_required else 0,
        }


def _shim_required(language: str | None) -> bool:
    """Go files in the same package share a namespace — splitting into sibling
    files needs no import edits. Python/TS/etc. need a back-compat re-export
    shim in the original path to preserve the public API."""
    lang = (language or "").lower()
    return lang not in ("go", "golang")
