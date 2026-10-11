"""Choose the boxes and arrows of the overview's system map.

The map a newcomer reads first shows a handful of boxes in tiers: who uses the
system, the ways in, shared libraries, the server, the engine, and the data
store. Structure alone picks them, so the same index always draws the same map:

* Boxes start as the C4 containers (manifest roots). A repository with too few
  of them is opened one directory level at a time, largest first, and then
  gets a smaller box budget, since a single package reads best in a few parts.
* A re-export file (it imports, but defines no symbol, route or entry point of
  its own) is not flow: its outgoing imports are dropped, and a box that is
  only such files is dropped. An entry point that only forwards to one file
  hands its role to that file.
* Tiers come from how a box is entered: declaring HTTP routes makes it the
  server; an entry point, an HTTP call to another box, or nothing depending on
  it makes it a way in; everything else is engine. A package only ways in
  import is a library of theirs: folded into its one consumer, or into one
  shared box.
* Actors come from the entry points: one person for every human way in (a
  terminal, an app), and one box per program that calls in. An entry point
  that looks like an API but serves no route is a person's way in.
* Arrows are the import and call graph rolled up to boxes, plus the runtime
  links no import expresses (actor to entry point, HTTP client to route). An
  arrow carrying a large share of the dependencies is always drawn; the rest
  fill a small budget, strongest first, at most two per box, skipping any that
  drawn arrows already connect. The caption says when parts or links were cut.

A model may rename boxes and verbs later; it never adds or removes one.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from repowise.core.analysis.c4.actors import actor_for, classify_entry_point
from repowise.core.analysis.c4.containers import assign_containers
from repowise.core.analysis.c4.labels import relation_label
from repowise.core.analysis.c4.relations import BoxEdges, roll_up_edges
from repowise.core.ingestion.languages.registry import REGISTRY

TIERS: tuple[str, ...] = ("actor", "surface", "shared", "service", "engine", "store")

MAX_NODES = 11
# A single package opened into its parts reads best in a few of them.
MAX_NODES_OPENED = 7
# Fewer code parts than this and the containers alone say nothing, so the
# largest one is opened up a level.
_MIN_UNITS = 5
_MAX_EDGES = 10
_MAX_EDGES_PER_BOX = 2
# An arrow carrying this share of the dependency weight is always drawn, and
# drawn thick when it also stands out from a typical arrow.
_HEAVY_SHARE = 0.1

# Share of a box's files that must declare HTTP routes for it to be the server.
_ROUTE_SHARE = 0.05

_STORE_ID = "n_store"
_PERSON_ID = "n_actor_user"
# Kinds a person enters by, in the order that names the person when alone.
_PERSON_KINDS = ("cli", "user", "developer")


@dataclass(frozen=True)
class MapNode:
    """One box. ``name``/``role`` are the keyless labels a model may replace."""

    id: str
    tier: str
    name: str
    role: str
    path: str = ""
    page_id: str = ""
    #: Plain facts about the box, shown to the model that names it.
    facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class MapEdge:
    source: str
    target: str
    verb: str
    weight: int = 0
    heavy: bool = False
    #: Why the arrow exists: the evidence shown to the model that names it.
    basis: str = ""


@dataclass(frozen=True)
class SystemMap:
    nodes: tuple[MapNode, ...]
    edges: tuple[MapEdge, ...]
    #: Code parts left off the map for space.
    omitted: int = 0
    #: A model-written sentence introducing the map; empty when keyless.
    caption: str = ""
    #: True when the arrow budget left out links that no drawn path shows.
    arrows_cut: bool = False

    def with_names(
        self, names: Mapping[str, tuple[str, str]], verbs: Mapping[tuple[str, str], str]
    ) -> SystemMap:
        """A copy with the given node labels and edge verbs swapped in."""
        nodes = tuple(
            replace(n, name=names[n.id][0], role=names[n.id][1]) if n.id in names else n
            for n in self.nodes
        )
        edges = tuple(
            replace(e, verb=verbs[(e.source, e.target)]) if (e.source, e.target) in verbs else e
            for e in self.edges
        )
        return replace(self, nodes=nodes, edges=edges)


@dataclass
class _Graph:
    """Box-level view of the edges, recomputed whenever boxes merge."""

    rolled: BoxEdges
    runtime: set[tuple[str, str]]
    deps: set[tuple[str, str]]
    has_in: set[str]
    has_out: set[str]


def build_system_map(
    files: Mapping[str, str],
    edges: Iterable[tuple[str, str, str]],
    *,
    repo_name: str,
    weights: Mapping[str, float] | None = None,
    manifests: Iterable[str] = (),
    package_names: Mapping[str, str] | None = None,
    entry_points: Sequence[str] = (),
    barrels: Collection[str] = (),
    route_files: Collection[str] = (),
    stores: Mapping[str, str] | None = None,
    http_links: Iterable[tuple[str, str]] = (),
    pages: Sequence[tuple[str, Collection[str]]] = (),
) -> SystemMap | None:
    """Pick the map's boxes and arrows, or ``None`` when there is nothing to draw.

    *files* maps each production code file to its language and *edges* are
    ``(source, target, edge_type)`` graph rows. *barrels* are files that define
    no symbols of their own, *route_files* declare HTTP routes, *stores* maps an
    ``external:*`` node id to the database client it stands for, *http_links*
    pairs a calling file with the file serving the route, and *pages* lists each
    module page's target path with the files it covers, for click-through.
    """
    weights = weights or {}
    package_names = package_names or {}
    edges = list(edges)
    # A re-export file imports something and declares no route of its own.
    importing = {s for s, _, etype in edges if etype == "imports"}
    barrels = (set(barrels) & importing) - set(route_files)
    entries = _forward_entries(entry_points, barrels, edges)
    barrels -= {path for path, _ in entries}
    edges = [e for e in edges if e[0] not in barrels]
    units, opened = _units(files, manifests, weights, barrels)
    store_of = {node: _STORE_ID for node in (stores or {})}
    links = list(http_links)

    graph = _graph(units, edges, store_of, links)
    kinds = _entry_kinds(units, entries, route_files)
    # A part another way in already calls is reached through it, not by a person.
    kinds = {
        u: k
        for u, k in kinds.items()
        if not (
            k <= set(_PERSON_KINDS)
            and any((o, u) in graph.deps and (u, o) not in graph.deps for o in kinds)
        )
    }
    tiers = _tiers(units, kinds, graph, route_files)
    labels: dict[str, str] = {}
    if not opened:
        units, shared = _fold_libraries(units, kinds, tiers, graph)
        if shared:
            labels[shared] = "Shared libraries"
        graph = _graph(units, edges, store_of, links)
        tiers = _tiers(units, kinds, graph, route_files)
        if shared:
            tiers[shared] = "shared"
    anchored = set(kinds) | {u for pair in graph.runtime for u in pair}
    # The repository-root catch-all of a multi-package repo holds leftovers
    # (scripts, loose tooling), not a part of the system.
    if len(tiers) > _MIN_UNITS and "" in tiers and "" not in anchored:
        del tiers[""]

    unreached = {
        u for u, t in tiers.items() if t == "surface" and u not in graph.has_in and u not in kinds
    }
    person, programs = _actors(kinds, unreached)
    store_users = {s for s, t in graph.rolled if t == _STORE_ID}
    cap = MAX_NODES_OPENED if opened else MAX_NODES
    budget = cap - (1 if person else 0) - len(programs)
    ranked = sorted(
        tiers,
        key=lambda u: (u not in anchored, -len(units[u]), -_weight(units[u], weights), u),
    )
    # The store takes a slot only when a box that is drawn uses it.
    kept = ranked[: budget - 1] if store_users & set(ranked[: budget - 1]) else ranked[:budget]
    if len(kept) < 2:
        return None

    ids = _node_ids(kept)
    starts = [path for path, _ in entries]
    nodes: list[MapNode] = []
    if person:
        nodes.append(MapNode(id=_PERSON_ID, tier="actor", name=person.name, role=person.description))
    nodes += [
        MapNode(id=f"n_actor_{a.kind}", tier="actor", name=a.name, role=a.description)
        for a in programs
    ]
    for unit in sorted(kept, key=lambda u: (TIERS.index(tiers[u]), -len(units[u]), u)):
        name = labels.get(unit) or package_names.get(unit) or _last_segment(unit, repo_name)
        nodes.append(
            _unit_node(unit, ids[unit], tiers[unit], name, units[unit], files, starts, pages)
        )
    if store_users & set(kept):
        clients = sorted(set((stores or {}).values()))
        nodes.append(
            MapNode(
                id=_STORE_ID,
                tier="store",
                name="Database",
                role=_clip_words("Via " + ", ".join(clients[:2]), 6),
                facts=(f"database clients imported: {', '.join(clients)}",),
            )
        )

    map_edges = _actor_edges(person, programs, kept, ids, kinds, unreached)
    map_edges += [
        MapEdge(ids[c], ids[p], "calls over HTTP", basis="HTTP client call to a route it serves")
        for c, p in sorted(graph.runtime)
        if c in ids and p in ids
    ]
    drawn, cut = _dependency_edges(graph.rolled, ids, graph.runtime)
    return SystemMap(
        nodes=tuple(nodes),
        edges=tuple(map_edges + drawn),
        omitted=len(units) - len(kept),
        arrows_cut=cut,
    )


# -- structure ----------------------------------------------------------------


def _forward_entries(
    entry_points: Sequence[str], barrels: set[str], edges: list[tuple[str, str, str]]
) -> list[tuple[str, str]]:
    """``(path, kind)`` per classified entry point.

    A symbol-less launcher hands its kind to the one file it imports.
    """
    out: list[tuple[str, str]] = []
    for path in entry_points:
        kind = classify_entry_point(path)
        if kind is None:
            continue
        targets = {t for s, t, etype in edges if s == path and etype == "imports"}
        if path in barrels and len(targets) == 1:
            path = targets.pop()
        if (path, kind) not in out:
            out.append((path, kind))
    return out


def _graph(units, edges, store_of, http_links) -> _Graph:
    file_to_unit = {f: u for u, members in units.items() for f in members}
    rolled = roll_up_edges(edges, file_to_unit, file_to_external=store_of)
    runtime = {
        (file_to_unit[c], file_to_unit[p])
        for c, p in http_links
        if c in file_to_unit and p in file_to_unit and file_to_unit[c] != file_to_unit[p]
    }
    deps = {pair for pair in rolled if pair[1] != _STORE_ID} | runtime
    return _Graph(
        rolled=rolled,
        runtime=runtime,
        deps=deps,
        has_in={t for _, t in deps},
        has_out={s for s, _ in deps},
    )


def _entry_kinds(
    units: Mapping[str, list[str]],
    entries: Sequence[tuple[str, str]],
    route_files: Collection[str],
) -> dict[str, set[str]]:
    """How each unit is entered, by the kind of entry point it holds."""
    file_to_unit = {f: u for u, members in units.items() for f in members}
    serves = {file_to_unit[f] for f in route_files if f in file_to_unit}
    kinds: dict[str, set[str]] = defaultdict(set)
    for path, kind in entries:
        if path in file_to_unit:
            unit = file_to_unit[path]
            # An "app" that serves no route is a library a person's code calls.
            kinds[unit].add("user" if kind == "api" and unit not in serves else kind)
    if any(k - {"developer"} for k in kinds.values()):
        # Scripts and tooling are how the repo is maintained, not how it is used.
        kinds = {u: k - {"developer"} for u, k in kinds.items() if k - {"developer"}}
    return dict(kinds)


def _tiers(units, kinds, graph: _Graph, route_files: Collection[str]) -> dict[str, str]:
    """Which tier each unit sits in; a part nothing touches is left out."""
    routes = set(route_files)
    # A stray route pattern in a large library (a string, a parser's own test
    # input) does not make it the server; a box that is mostly routes does.
    serves = {
        u
        for u, members in units.items()
        if sum(1 for m in members if m in routes) >= _ROUTE_SHARE * len(members)
        and not routes.isdisjoint(members)
    }
    tiers: dict[str, str] = {}
    for unit in units:
        entered = kinds.get(unit, set())
        if unit in serves or "api" in entered or any(p == unit for _, p in graph.runtime):
            tiers[unit] = "service"
        elif (
            entered
            or any(c == unit for c, _ in graph.runtime)
            or (unit not in graph.has_in and unit in graph.has_out)
        ):
            tiers[unit] = "surface"
        elif unit in graph.has_in or unit in graph.has_out or (unit, _STORE_ID) in graph.rolled:
            tiers[unit] = "engine"
    return tiers


def _fold_libraries(units, kinds, tiers, graph: _Graph) -> tuple[dict[str, list[str]], str]:
    """Fold packages only the ways in import into them.

    A library with one consuming way in joins that box; libraries shared by
    several join one shared box. Returns the new units and the shared box key.
    """
    surfaces = {u for u, t in tiers.items() if t == "surface" and u in kinds}
    surfaces |= {u for u, t in tiers.items() if t == "surface" and u not in graph.has_in}
    importers: dict[str, set[str]] = defaultdict(set)
    for s, t in graph.deps:
        importers[t].add(s)
    # A package that keeps the data is the engine, whoever calls it.
    libs = {
        u
        for u in tiers
        if u not in surfaces
        and tiers[u] != "service"
        and importers[u]
        and (u, _STORE_ID) not in graph.rolled
    }
    changed = True
    while changed:
        changed = False
        for u in sorted(libs):
            if importers[u] - libs - surfaces:
                libs.discard(u)
                changed = True
    # Folding every part behind the ways in would leave a map with no engine.
    if not libs or not set(tiers) - libs - surfaces:
        return units, ""

    def roots(u: str, seen: frozenset[str] = frozenset()) -> set[str]:
        found = importers[u] & surfaces
        for lib in (importers[u] & libs) - seen - {u}:
            found |= roots(lib, seen | {u})
        return found

    merged = {u: list(members) for u, members in units.items()}
    shared = [u for u in sorted(libs) if len(roots(u)) != 1]
    for u in sorted(libs):
        if u not in shared:
            (owner,) = roots(u)
            merged[owner] += merged.pop(u)
    if not shared:
        return merged, ""
    key = max(shared, key=lambda u: (len(units[u]), u))
    for u in shared:
        if u != key:
            merged[key] += merged.pop(u)
    return merged, key


def _actors(kinds: Mapping[str, set[str]], unreached: set[str]):
    """The person entering every human way in, and each program that calls in."""
    present = set().union(*kinds.values()) if kinds else set()
    if unreached or not present:
        present.add("user")
    human = [k for k in _PERSON_KINDS if k in present]
    person = None
    if human:
        person = actor_for(human[0] if len(human) == 1 else "user")
    programs = [actor_for(k) for k in ("api", "scheduler") if k in present]
    return person, programs[:1]


# -- units --------------------------------------------------------------------


def _units(
    files: Mapping[str, str],
    manifests: Iterable[str],
    weights: Mapping[str, float],
    barrels: set[str],
) -> tuple[dict[str, list[str]], bool]:
    """Code units, and whether a container had to be opened to find enough."""
    owner, _ = assign_containers(manifests, files)
    units: dict[str, list[str]] = defaultdict(list)
    for path in sorted(files):
        if path in owner:
            units[owner[path]].append(path)
    opened = False
    while len(units) < _MIN_UNITS:
        splits = {u: _split(u, members) for u, members in units.items()}
        splittable = [u for u, parts in splits.items() if len(parts) > 1]
        if not splittable:
            break
        # Opening a unit can overshoot the node budget; the ranking trims it.
        largest = max(splittable, key=lambda u: (len(units[u]), _weight(units[u], weights), u))
        del units[largest]
        units.update(splits[largest])
        opened = True
    kept = {u: m for u, m in units.items() if not set(m) <= barrels}
    return kept, opened


def _split(root: str, members: list[str]) -> dict[str, list[str]]:
    """Children of *root*: each subdirectory, and each loose file, one level down.

    Directory chains every member shares (``src/pkg``) are walked through, so a
    split lands on the first level where the files actually diverge.
    """
    base = root
    while True:
        rel = [_rel(p, base) for p in members]
        heads = {r.split("/", 1)[0] for r in rel}
        if len(heads) == 1 and all("/" in r for r in rel):
            base = f"{base}/{heads.pop()}" if base else heads.pop()
            continue
        break
    parts: dict[str, list[str]] = defaultdict(list)
    for path, r in zip(members, rel, strict=True):
        head = r.split("/", 1)[0]
        child = path if "/" not in r else (f"{base}/{head}" if base else head)
        parts[child].append(path)
    return dict(parts)


def _rel(path: str, base: str) -> str:
    return path[len(base) + 1 :] if base else path


def _weight(members: Iterable[str], weights: Mapping[str, float]) -> float:
    return sum(weights.get(m, 0.0) for m in members)


def _node_ids(units: Iterable[str]) -> dict[str, str]:
    ids: dict[str, str] = {}
    taken: set[str] = {_STORE_ID, _PERSON_ID}
    for unit in sorted(units):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", unit).strip("_").lower() or "root"
        candidate = f"n_{slug}"
        n = 2
        while candidate in taken:
            candidate = f"n_{slug}_{n}"
            n += 1
        taken.add(candidate)
        ids[unit] = candidate
    return ids


def _unit_node(
    unit: str,
    node_id: str,
    tier: str,
    name: str,
    members: list[str],
    files: Mapping[str, str],
    entry_points: Sequence[str],
    pages: Sequence[tuple[str, Collection[str]]],
) -> MapNode:
    languages: dict[str, int] = defaultdict(int)
    for m in members:
        languages[files[m]] += 1
    language = max(sorted(languages), key=lambda lang: languages[lang])
    count = len(members)
    spec = REGISTRY.get(language)
    shown = spec.display_name if spec else language
    role = f"{count} {shown} file{'s' if count != 1 else ''}"
    facts = [f"path: {unit or '(repository root)'}", f"current name: {name}", role]
    dirs = sorted({m.rsplit("/", 1)[0] for m in members if "/" in m})
    if len(dirs) > 1:
        facts.append("directories: " + ", ".join(dirs[:6]))
    starts = [e for e in entry_points if e in members]
    if starts:
        facts.append("entry points: " + ", ".join(starts[:3]))
    facts.append("files: " + ", ".join(m.rsplit("/", 1)[-1] for m in members[:8]))
    return MapNode(
        id=node_id,
        tier=tier,
        name=name,
        role=role,
        path=unit,
        page_id=_page_for(unit, members, pages),
        facts=tuple(facts),
    )


def _last_segment(unit: str, repo_name: str) -> str:
    return unit.rsplit("/", 1)[-1] if unit else repo_name


def _page_for(unit: str, members: list[str], pages: Sequence[tuple[str, Collection[str]]]) -> str:
    """The module page to open for *unit*: its own, else the one covering most of it."""
    best, best_count = "", 0
    for key, covered in pages:
        if key == unit:
            return key
        count = sum(1 for m in members if m in covered)
        if count > best_count or (count == best_count and count and len(key) < len(best)):
            best, best_count = key, count
    return best


# -- edges --------------------------------------------------------------------


def _actor_edges(person, programs, kept, ids, kinds, unreached) -> list[MapEdge]:
    edges: list[MapEdge] = []
    if person:
        for unit in sorted(kept):
            entered = [k for k in _PERSON_KINDS if k in kinds.get(unit, set())]
            if not entered and unit not in unreached:
                continue
            verb = actor_for(entered[0]).verb if entered else "uses"
            basis = "entry point" if entered else "top-level way in"
            edges.append(MapEdge(_PERSON_ID, ids[unit], verb, basis=f"{basis}: {unit}"))
    for actor in programs:
        edges += [
            MapEdge(f"n_actor_{actor.kind}", ids[u], actor.verb, basis=f"entry point: {u}")
            for u in sorted(kept)
            if actor.kind in kinds.get(u, set())
        ]
    return edges


def _dependency_edges(rolled, ids, runtime) -> tuple[list[MapEdge], bool]:
    """The arrows to draw, and whether the budget left any link out."""
    pool = [
        (count, s, t, types)
        for (s, t), (count, types) in rolled.items()
        if s in ids and (t in ids or t == _STORE_ID) and (s, t) not in runtime
    ]
    # A pair importing each other draws once, the heavier way.
    counts = {(s, t): count for count, s, t, _ in pool}
    pool = [p for p in pool if (counts.get((p[2], p[1]), -1), p[2], p[1]) < (p[0], p[1], p[2])]
    pool.sort(key=lambda p: (-p[0], p[1], p[2]))
    total = sum(p[0] for p in pool) or 1
    typical = sorted(p[0] for p in pool)[len(pool) // 2] if pool else 0
    node = {**ids, _STORE_ID: _STORE_ID}
    drawn: list[MapEdge] = []
    pairs: set[tuple[str, str]] = set()
    per_box: dict[str, int] = defaultdict(int)
    cut = False
    for count, s, t, types in pool:
        big = count >= _HEAVY_SHARE * total
        # Every arrow into the store is drawn: it is the only thing the store box shows.
        always = big or t == _STORE_ID
        if not always and _reachable(s, t, pairs):
            continue
        if not always and (len(drawn) >= _MAX_EDGES or per_box[s] >= _MAX_EDGES_PER_BOX):
            cut = True
            continue
        if not always:
            per_box[s] += 1
        pairs.add((s, t))
        verb = "reads and writes" if t == _STORE_ID else relation_label(types)
        pairs_text = f"{count} file pair{'s' if count != 1 else ''}"
        drawn.append(
            MapEdge(
                node[s],
                node[t],
                verb,
                weight=count,
                heavy=big and count >= 2 * typical,
                basis=f"{verb} across {pairs_text}",
            )
        )
    return drawn, cut


def _reachable(source: str, target: str, pairs: set[tuple[str, str]]) -> bool:
    """True when the arrows drawn so far already lead from *source* to *target*."""
    seen, frontier = {source}, [source]
    while frontier:
        here = frontier.pop()
        for a, b in pairs:
            if a == here and b not in seen:
                if b == target:
                    return True
                seen.add(b)
                frontier.append(b)
    return False


def _clip_words(text: str, limit: int) -> str:
    return " ".join(text.split()[:limit])
