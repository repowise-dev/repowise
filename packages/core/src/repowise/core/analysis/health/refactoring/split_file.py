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
  2. shared local helper (A and B both call a third local symbol) ->
     ``w = 2`` per shared helper — cohesion without a direct A<->B edge;
  3. cross-module affinity proxy (A and B both call into the same *foreign*
     module) -> ``w = 1`` per shared module — the graph-derived stand-in for
     "same responsibility".
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
from typing import Any

from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# Edge weights over the intra-file symbol graph (see module docstring).
_DIRECT_CALL_WEIGHT = 3.0
_SHARED_HELPER_WEIGHT = 2.0
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


def _is_test_path(path: str) -> bool:
    """Conservative test-file check (mirrors the other detectors)."""
    p = path.lower().replace("\\", "/")
    segments = p.split("/")
    if any(seg in ("test", "tests", "__tests__") for seg in segments[:-1]):
        return True
    base = segments[-1]
    return (
        base in ("tests.py", "test.py", "conftest.py")
        or base.startswith("test_")
        or base.endswith(
            (
                "_test.py",
                "_test.go",
                ".test.ts",
                ".test.tsx",
                ".test.js",
                ".test.mts",
                ".test.cts",
                ".spec.ts",
                ".spec.js",
                ".spec.mts",
                ".spec.cts",
            )
        )
    )


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
        or base in ("__init__.py", "index.ts", "index.js", "mod.rs")
    )


def _is_skippable_path(path: str) -> bool:
    return _is_test_path(path) or _is_generated_path(path)


def _node_span(data: dict) -> int:
    start = data.get("start_line")
    end = data.get("end_line")
    if isinstance(start, int) and isinstance(end, int) and end >= start:
        return end - start + 1
    return 0


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
        seen: set[str] = set()
        for tok in name.lower().lstrip("_").split("_"):
            if len(tok) >= 4 and tok not in _NAME_STOPWORDS and not tok.isdigit():
                seen.add(tok)
        counts.update(seen)
    if not counts:
        return ""
    token, votes = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return token if votes >= 2 else ""


def _partition_weighted(wg: Any) -> dict[str, int]:
    """Community assignment over the weighted intra-file graph *wg*.

    Reuses the repo's shared Leiden/Louvain partitioner (seeded, deterministic)
    and degrades to connected components on any failure or when *wg* has no
    edges. Every node of *wg* is always assigned.
    """
    nodes = sorted(wg.nodes())
    if wg.number_of_edges() == 0:
        return {n: i for i, n in enumerate(nodes)}

    assignment: dict[str, int] = {}
    try:
        from repowise.core.analysis.communities import _partition

        raw, _algo = _partition(wg)
        assignment = {n: int(c) for n, c in raw.items()}
    except Exception:
        assignment = {}

    if not assignment:
        # Connected-components fallback — fully deterministic.
        try:
            import networkx as nx

            for cid, comp in enumerate(nx.connected_components(wg)):
                for n in comp:
                    assignment[n] = cid
        except Exception:
            return {n: i for i, n in enumerate(nodes)}

    # Any node the partitioner dropped (Leiden can omit isolates) gets its own
    # community so it lands in the residual rather than vanishing.
    next_cid = max(assignment.values(), default=-1) + 1
    for n in nodes:
        if n not in assignment:
            assignment[n] = next_cid
            next_cid += 1
    return assignment


@register
class SplitFileDetector(RefactoringDetector):
    name = "split_file"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        graph = ctx.graph
        if graph is None or _is_skippable_path(ctx.file_path):
            return []
        if ctx.nloc < _MIN_FILE_NLOC:
            return []

        defined = self._defined_symbols(graph, ctx.file_path)
        owner_of = {sid: self._resolve_owner(ctx.file_path, sid, defined) for sid in defined}
        node_ids = sorted(
            {
                owner
                for owner in owner_of.values()
                if defined.get(owner, {}).get("kind") in ("class", "function")
                and not defined.get(owner, {}).get("parent_name")
            }
        )
        if len(node_ids) < _MIN_SYMBOLS:
            return []

        # Single-dominant-class router: that is an Extract Class candidate.
        node_set = set(node_ids)
        for nid in node_ids:
            data = defined[nid]
            if (
                data.get("kind") == "class"
                and _node_span(data) > _DOMINANT_CLASS_FRACTION * ctx.nloc
            ):
                return []

        local_pairs, callers_of, foreign_of = self._intra_file_signals(
            graph, ctx, defined, owner_of, node_set
        )

        # Collapse the shared-utility spine into the residual ``core`` so it
        # does not connect every group (the under-split risk).
        n = len(node_ids)
        spine = {
            callee
            for callee, callers in callers_of.items()
            if len(callers) >= max(_SPINE_MIN_CALLERS, _SPINE_CALLER_FRACTION * n)
        }
        cluster_nodes = [nid for nid in node_ids if nid not in spine]
        if len(cluster_nodes) < _MIN_SYMBOLS // 2:
            return []

        wg = self._build_weighted_graph(cluster_nodes, spine, local_pairs, callers_of, foreign_of)
        if wg is None:
            return []

        assignment = _partition_weighted(wg)
        groups_map: dict[int, list[str]] = defaultdict(list)
        for node, cid in assignment.items():
            groups_map[cid].append(node)

        modularity = self._modularity(wg, groups_map)
        substantive = sorted(
            (
                sorted(members)
                for members in groups_map.values()
                if len(members) >= _MIN_GROUP_SYMBOLS
            ),
            key=lambda members: (-len(members), members[0]),
        )
        if len(substantive) < _MIN_GROUPS or modularity < _MIN_MODULARITY:
            return []

        # Residual = the spine + every symbol not in a substantive group.
        placed = {m for g in substantive for m in g}
        residual_ids = sorted(
            spine | {nid for nid in node_ids if nid not in placed and nid not in spine}
        )

        intra_edges, cut_edges = self._edge_cut(wg, assignment)
        groups, residual = self._shape_groups(ctx, defined, foreign_of, substantive, residual_ids)
        blast = self._blast_radius(graph, ctx, defined, shim_required=_shim_required(ctx.language))

        confidence = "high" if modularity >= _HIGH_CONFIDENCE_MODULARITY else "medium"
        return [
            RefactoringSuggestion(
                refactoring_type=self.name,
                file_path=ctx.file_path,
                target_symbol=f"{_basename(ctx.file_path)} -> {len(groups)} files",
                line_start=None,
                line_end=None,
                plan={
                    "groups": groups,
                    "residual": residual,
                    "shim_required": _shim_required(ctx.language),
                },
                evidence={
                    "file_nloc": ctx.nloc,
                    "symbol_count": len(node_ids),
                    "group_count": len(groups),
                    "modularity": round(modularity, 3),
                    "intra_edges": intra_edges,
                    "cut_edges": cut_edges,
                },
                impact_delta=0.0,
                effort_bucket=effort_bucket(ctx.nloc),
                blast_radius=blast,
                confidence=confidence,
                source_biomarker="",
            )
        ]

    # ----- graph reading ---------------------------------------------------

    def _defined_symbols(self, graph: Any, file_path: str) -> dict[str, dict]:
        """Symbol nodes defined in *file_path* via ``defines`` edges (with a
        prefix-scan fallback), excluding the synthetic ``__module__`` node."""
        out: dict[str, dict] = {}
        if file_path in graph:
            for _u, v, data in graph.out_edges(file_path, data=True):
                if data.get("edge_type") != "defines":
                    continue
                node = graph.nodes[v]
                if node.get("node_type") == "symbol" and node.get("kind") != "module":
                    out[v] = node
        if not out:
            prefix = f"{file_path}::"
            for node_id, data in graph.nodes(data=True):
                if (
                    data.get("node_type") == "symbol"
                    and data.get("kind") != "module"
                    and node_id.startswith(prefix)
                ):
                    out[node_id] = data
        return out

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
        self,
        graph: Any,
        ctx: RefactoringContext,
        defined: dict[str, dict],
        owner_of: dict[str, str],
        node_set: set[str],
    ) -> tuple[set[tuple[str, str]], dict[str, set[str]], dict[str, set[str]]]:
        """Walk ``calls`` edges once and derive the three cohesion signals:
        direct local-call pairs, who-calls-each-local-helper, and each node's
        set of foreign module labels."""
        local_pairs: set[tuple[str, str]] = set()
        callers_of: dict[str, set[str]] = defaultdict(set)
        foreign_of: dict[str, set[str]] = defaultdict(set)

        for sid, owner in owner_of.items():
            if owner not in node_set:
                continue
            for _u, callee, edata in graph.out_edges(sid, data=True):
                if edata.get("edge_type") != "calls" or callee == sid:
                    continue
                if callee in owner_of:
                    cowner = owner_of[callee]
                    if cowner not in node_set or cowner == owner:
                        continue
                    local_pairs.add((owner, cowner))
                    callers_of[cowner].add(owner)
                else:
                    cdata = graph.nodes.get(callee, {})
                    fpath = cdata.get("file_path")
                    if fpath and fpath != ctx.file_path:
                        label = ctx.module_map.get(fpath) or fpath
                        foreign_of[owner].add(label)
        return local_pairs, callers_of, foreign_of

    def _build_weighted_graph(
        self,
        cluster_nodes: list[str],
        spine: set[str],
        local_pairs: set[tuple[str, str]],
        callers_of: dict[str, set[str]],
        foreign_of: dict[str, set[str]],
    ) -> Any:
        try:
            import networkx as nx
        except Exception:
            return None

        weights: Counter[tuple[str, str]] = Counter()

        def _add(a: str, b: str, w: float) -> None:
            if a == b or a in spine or b in spine:
                return
            weights[tuple(sorted((a, b)))] += w

        # 1. direct intra-file call
        for a, b in local_pairs:
            _add(a, b, _DIRECT_CALL_WEIGHT)
        # 2. shared local helper (callers of a common, non-spine local symbol)
        for callee, callers in callers_of.items():
            if callee in spine:
                continue
            members = sorted(c for c in callers if c not in spine)
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    _add(members[i], members[j], _SHARED_HELPER_WEIGHT)
        # 3. cross-module affinity proxy (shared foreign modules)
        for i in range(len(cluster_nodes)):
            for j in range(i + 1, len(cluster_nodes)):
                a, b = cluster_nodes[i], cluster_nodes[j]
                shared = foreign_of.get(a, set()) & foreign_of.get(b, set())
                if shared:
                    _add(a, b, _SHARED_MODULE_WEIGHT * len(shared))

        wg = nx.Graph()
        wg.add_nodes_from(sorted(cluster_nodes))
        for (a, b), w in weights.items():
            wg.add_edge(a, b, weight=w)
        return wg

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
        self,
        ctx: RefactoringContext,
        defined: dict[str, dict],
        foreign_of: dict[str, set[str]],
        substantive: list[list[str]],
        residual_ids: list[str],
    ) -> tuple[list[dict], dict | None]:
        stem, ext = _split_stem_ext(ctx.file_path)
        directory = _directory(ctx.file_path)
        used: set[str] = {_basename(ctx.file_path)}
        groups: list[dict] = []
        self_segments = {seg.lower() for seg in ctx.file_path.replace("\\", "/").split("/")}
        self_segments.add(stem.lower())
        for idx, members in enumerate(substantive, 1):
            label = self._group_label(defined, foreign_of, members, self_segments)
            file_stem = label or f"{stem}_part{idx}"
            filename = self._unique_filename(file_stem, ext, used)
            suggested = f"{directory}/{filename}" if directory else filename
            groups.append(
                {
                    "name": label or None,
                    "symbols": sorted(self._sym_name(defined, m) for m in members),
                    "suggested_file": suggested,
                }
            )
        residual = (
            {"symbols": sorted(self._sym_name(defined, m) for m in residual_ids)}
            if residual_ids
            else None
        )
        return groups, residual

    def _group_label(
        self,
        defined: dict[str, dict],
        foreign_of: dict[str, set[str]],
        members: list[str],
        self_segments: set[str],
    ) -> str:
        """Deterministic file name for a group: the dominant shared name token
        first (a plurality vote — the most semantically meaningful signal),
        else a clean dominant foreign-module label, else ``""`` (the caller
        falls back to ``<file>_partN``).

        Dogfood showed the foreign-module label is frequently the repo's own
        package community (carrying a size suffix like ``repowise (290)``),
        which sanitizes to noise like ``repowise__290``. So the name vote
        leads, and a module label is used only when it is a clean identifier
        that is not already part of the file's own path."""
        token = _dominant_token([self._sym_name(defined, m) for m in members])
        if token and token not in self_segments:
            return token

        labels: Counter[str] = Counter()
        for m in members:
            for lab in foreign_of.get(m, set()):
                labels[lab] += 1
        if labels:
            best = min(labels, key=lambda lab: (-labels[lab], lab))
            seg = best.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            seg = _split_stem_ext(seg)[0]
            cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in seg).strip("_")
            # Reject uninformative labels: too short, carrying a community-dedup
            # digit suffix, or naming the file's own namespace (tells you
            # nothing new about the group).
            if (
                len(cleaned) >= 3
                and not any(ch.isdigit() for ch in cleaned)
                and cleaned.lower() not in self_segments
            ):
                return cleaned.lower()
        return token

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
        self, graph: Any, ctx: RefactoringContext, defined: dict[str, dict], *, shim_required: bool
    ) -> dict[str, Any]:
        """External files that reference this file's symbols (call in-edges) or
        import the file. Import rewrites are zero for the same-package Go case
        (no shim, no edits); for shim languages it is the count of dependents
        whose imports the plan's shim preserves but lists for review."""
        dependent_files: set[str] = set()
        for sid in defined:
            for u, _v, edata in graph.in_edges(sid, data=True):
                if edata.get("edge_type") != "calls":
                    continue
                f = graph.nodes.get(u, {}).get("file_path")
                if f and f != ctx.file_path:
                    dependent_files.add(f)
        if ctx.file_path in graph:
            for u, _v, edata in graph.in_edges(ctx.file_path, data=True):
                if edata.get("edge_type") == "imports" and u != ctx.file_path:
                    dependent_files.add(u)
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
