"""Where a third-party package is used: import sites, components, owners, reachability.

Read from what the index already holds. Declarations are the ``external_systems``
rows the manifest parsers wrote (:mod:`repowise.core.ingestion.external_systems`);
import sites are ``imports`` edges from a file to an ``external:`` graph node.
A node counts for a package in one of two ways, and every row says which:

* ``declared``: ingestion linked the node to the package's manifest
  declaration (``graph_nodes.external_system_id``): the import module is the
  declared name or starts with it (``requests.adapters``, ``lodash/merge``).
* ``inferred``: no link; the import module starts with the asked name but no
  manifest declaration was matched to it. A Python distribution whose import
  name differs (``PyYAML`` / ``yaml``) is found only by asking for the import
  name, and then only as inferred.

Reachability per importing file, from the stored graph: ``reachable`` when an
entry point or other reachability root imports it, directly or transitively;
``unreachable`` when dead-code analysis reported the file as ``unreachable_file``;
``test`` for test files; otherwise ``unknown``. A file no import path reaches
may still be loaded by a framework, a script or reflection, so that is never
reported as unreachable on the import graph alone.

Owners are declared teams only (catalog ``spec.owner`` and CODEOWNERS through the
``team-directory`` seam). Individuals are never named.
"""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.external_systems import (
    EXTERNAL_NODE_PREFIX,
    IMPORT_EDGE_TYPE,
    declaration_name_candidates,
    package_key,
)
from repowise.core.ids import SYMBOL_SEP

#: Language tag of an importing file to the manifest ecosystem its imports
#: come from. Used only to keep an undeclared import name from matching a
#: package of another ecosystem when the caller names one.
LANGUAGE_ECOSYSTEM = {
    "python": "pypi",
    "javascript": "npm",
    "typescript": "npm",
    "tsx": "npm",
    "jsx": "npm",
    "rust": "cargo",
    "go": "go",
    "csharp": "nuget",
    "java": "maven",
    "kotlin": "maven",
    "scala": "maven",
}

#: A statement that imports, anchored at the start of the line so a comment or a
#: string that mentions a module never counts. ``const x = require(...)`` is the
#: one form where the keyword is not first (and its ``import(...)`` twin).
_IMPORT_LINE = re.compile(
    r"(?:(?:import|from|use|using|require|include|load|extern|export|open)\b"
    r"|\}\s*from\b"  # the last line of a multi-line JS/TS import
    r"|#\s*include\b|#import\b"
    r"|(?:const|let|var)\b.*\b(?:require|import)\s*\()"
)
#: First characters :data:`_IMPORT_LINE` can match on, checked before the regex.
_IMPORT_STARTS = tuple("iufrleo}#cv")
#: Opens an ``import ( ... )`` block (Go): each line inside names one module.
_BLOCK_OPEN = re.compile(r"import\s*\(\s*$")
#: Files larger than this are not read for the import line; the site keeps no line.
_MAX_READ_BYTES = 2_000_000
#: File-to-file edges that load the target: static imports, and the dynamic
#: loads the parsers resolve (``importlib.import_module`` and ``"module:attr"``
#: string tables, such as the CLI's lazily loaded commands).
_LOAD_EDGE_TYPES = (IMPORT_EDGE_TYPE, "dynamic_imports", "dynamic_uses")
#: Bound parameters per ``IN`` list, well under SQLite's variable limit.
_CHUNK = 500

METHOD = {
    "import_sites": "imports edges from a file to an external graph node",
    "mapping": "declared: linked to the manifest declaration by ingestion; "
    "inferred: matched by import name only, no declaration linked",
    "line": "first import statement of the file (a line starting with an import "
    "keyword, or inside a Go import block) that names the module; absent when none does",
    "reachability": "reachable: an entry point or reachability root loads the file "
    "through stored import or resolved dynamic-load edges; unreachable: an open dead-code unreachable_file finding; "
    "test: a test file; unknown: neither",
    "owners": "declared teams from the catalog spec.owner and CODEOWNERS; individual "
    "and unresolved owners are counted in owner_counts, never named",
}


def dist_key(name: str) -> str:
    """PEP 503 spelling, so ``PyYAML`` and ``pyyaml`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).casefold()


def _same_name(ecosystem: str, a: str, b: str) -> bool:
    return lookup_key(ecosystem, a) == lookup_key(ecosystem, b)


def parse_query(query: str) -> tuple[str, str | None]:
    """``name`` or ``ecosystem:name`` (a package key) to ``(name, ecosystem)``."""
    head, sep, rest = query.partition(":")
    if sep and rest and head in _KNOWN_ECOSYSTEMS:
        return rest, head
    return query, None


_KNOWN_ECOSYSTEMS = frozenset(
    {"npm", "pypi", "cargo", "go", "nuget", "maven", "bazel", "cmake", "composer"}
)


@dataclass(frozen=True)
class Declaration:
    id: int
    ecosystem: str
    name: str
    version: str | None
    declared_in: str
    is_dev_dep: bool

    @property
    def key(self) -> str:
        return package_key(self.ecosystem, self.name)


@dataclass
class UsageIndex:
    """Declarations, external nodes and the import edges reaching them, read once."""

    declarations: list[Declaration] = field(default_factory=list)
    #: ``external:`` node id to its linked declaration id, or ``None``.
    nodes: dict[str, int | None] = field(default_factory=dict)
    #: ``(source file, external node, imported names)``.
    edges: list[tuple[str, str, list[str]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Built once so each query below is a few dict lookups, however many a
        # supplied SBOM asks about.
        self.by_target: dict[str, list[tuple[str, list[str]]]] = {}
        for src, dst, names in self.edges:
            self.by_target.setdefault(dst, []).append((src, names))
        self._decls_by_name: dict[str, list[Declaration]] = {}
        for d in self.declarations:
            self._decls_by_name.setdefault(lookup_key(d.ecosystem, d.name), []).append(d)
        self._nodes_by_decl: dict[int, list[str]] = {}
        self._unlinked_by_name: dict[str, list[str]] = {}
        for node_id, decl_id in self.nodes.items():
            if decl_id is not None:
                self._nodes_by_decl.setdefault(decl_id, []).append(node_id)
                continue
            suffix = node_id[len(EXTERNAL_NODE_PREFIX) :]
            for cand in {c.casefold() for c in declaration_name_candidates(suffix)}:
                self._unlinked_by_name.setdefault(cand, []).append(node_id)

    def match(
        self, name: str, ecosystem: str | None = None
    ) -> tuple[list[Declaration], dict[str, str]]:
        """The declarations of *name* and each matching external node's mapping basis."""
        found = {
            d.id: d
            for key in dict.fromkeys((name.casefold(), dist_key(name)))
            for d in self._decls_by_name.get(key, ())
            if (ecosystem is None or d.ecosystem == ecosystem)
            and _same_name(d.ecosystem, d.name, name)
        }
        mapped: dict[str, str] = {}
        for decl_id in found:
            for node_id in self._nodes_by_decl.get(decl_id, ()):
                mapped[node_id] = "declared"
        for node_id in self._unlinked_by_name.get(name.casefold(), ()):
            mapped[node_id] = "inferred"
        return list(found.values()), mapped


def lookup_key(ecosystem: str, name: str) -> str:
    """One spelling per package within an ecosystem: PEP 503 for PyPI, else case-folded."""
    return dist_key(name) if ecosystem == "pypi" else name.casefold()


def _is_file(node_id: str) -> bool:
    return SYMBOL_SEP not in node_id and not node_id.startswith(("external:", "framework:"))


async def load_usage_index(session: Any, repository_id: str) -> UsageIndex:
    from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode

    decls = (
        await session.execute(
            select(
                ExternalSystem.id,
                ExternalSystem.ecosystem,
                ExternalSystem.name,
                ExternalSystem.version,
                ExternalSystem.declared_in,
                ExternalSystem.is_dev_dep,
            ).where(ExternalSystem.repository_id == repository_id)
        )
    ).all()
    nodes = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.external_system_id).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id.startswith(EXTERNAL_NODE_PREFIX),
            )
        )
    ).all()
    edges = (
        await session.execute(
            select(
                GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.imported_names_json
            ).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.edge_type == IMPORT_EDGE_TYPE,
                GraphEdge.target_node_id.startswith(EXTERNAL_NODE_PREFIX),
            )
        )
    ).all()
    return UsageIndex(
        declarations=[Declaration(*row) for row in decls],
        nodes={n: d for n, d in nodes},
        edges=[(s, t, _names(raw)) for s, t, raw in edges if _is_file(s)],
    )


def _names(raw: str | None) -> list[str]:
    try:
        names = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(n) for n in names if n and n != "*"] if isinstance(names, list) else []


# -- file facts: component, owners, reachability -------------------------------


@dataclass
class FileFacts:
    """What the rows need about the importing files, read once per call."""

    component_of: dict[str, str] = field(default_factory=dict)
    component_owner: dict[str, str | None] = field(default_factory=dict)
    language: dict[str, str] = field(default_factory=dict)
    reachability: dict[str, dict[str, str]] = field(default_factory=dict)
    declared: Any = None  # DeclaredOwnership
    #: Path to its import statements as ``(1-based line, stripped text)``, read once.
    import_lines: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    repo_root: Path | None = None

    def owners(self, path: str) -> tuple[list[str], dict[str, int]]:
        """Declared owner teams of *path* (the component's, then CODEOWNERS'), and
        how many other declared owners there are by kind: ``individual`` and
        ``unresolved`` (a bare ``@name`` that may be a person or a GitLab group).
        Those are counted, never named, as in ``components ownership``."""
        refs = []
        comp_owner = self.component_owner.get(self.component_of.get(path, ""))
        if comp_owner and self.declared is not None:
            refs.append(self.declared.from_backstage(comp_owner))
        if self.declared is not None and self.declared.rules:
            refs.extend(self.declared.from_codeowners(o) for o in self.declared.codeowners_of(path))
        teams = list(dict.fromkeys(r.name for r in refs if r.kind == "team"))
        others = Counter(r.kind for r in dict.fromkeys(refs) if r.kind != "team")
        return teams, dict(sorted(others.items()))

    def line_of(self, path: str, tokens: Sequence[str]) -> tuple[int, str] | None:
        """``(1-based line, text)`` of the first import statement naming a token."""
        if self.repo_root is None:
            return None
        if path not in self.import_lines:
            self.import_lines[path] = _import_lines(self.repo_root / path)
        patterns = [_token_pattern(t) for t in tokens if t]
        for number, text in self.import_lines[path]:
            if any(p.search(text) for p in patterns):
                return number, text
        return None


@lru_cache(maxsize=4096)
def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w@/.-]){re.escape(token)}(?![\w-])")


def _import_lines(path: Path) -> list[tuple[int, str]]:
    """The file's import statements, as ``(1-based line, stripped text)``; empty
    when it is unreadable or too large. The text itself is not kept."""
    try:
        if path.stat().st_size > _MAX_READ_BYTES:
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[tuple[int, str]] = []
    in_block = False
    for number, raw in enumerate(lines, 1):
        text = raw.strip()
        if in_block:
            if text.startswith(")"):
                in_block = False
            elif text and not text.startswith("//"):
                out.append((number, text[:200]))
        # The cheap prefix test skips most lines before any regex runs.
        elif text.startswith(_IMPORT_STARTS):
            if _BLOCK_OPEN.match(text):
                in_block = True
            elif _IMPORT_LINE.match(text):
                out.append((number, text[:200]))
    return out


async def load_file_facts(
    session: Any, repository_id: str, repo_root: Path | str, files: Iterable[str]
) -> FileFacts:
    """Components, declared owners and reachability for *files*."""
    from repowise.core.components.store import load_repo_components, workspace_services
    from repowise.core.ownership.directory import team_directories

    root = Path(repo_root)
    wanted = set(files)
    found = await load_repo_components(session, repository_id, root, services=workspace_services(root))
    facts = FileFacts(repo_root=root, declared=team_directories.get().load(root))
    for comp in found.resolved.components:
        facts.component_owner[comp.name] = comp.owner
        for path in found.resolved.files.get(comp.name, ()):
            if path in wanted:
                facts.component_of[path] = comp.name
    facts.reachability, facts.language = await _reachability(
        session, repository_id, wanted, found.test_files
    )
    return facts


async def _file_nodes(
    session: Any, repository_id: str, files: set[str]
) -> tuple[list[str], dict[str, str]]:
    """The reachability roots (entry points and other roots), and each of
    *files*' language."""
    from repowise.core.persistence.models import GraphNode

    nodes = (
        await session.execute(
            select(
                GraphNode.node_id,
                GraphNode.language,
                GraphNode.is_entry_point,
                GraphNode.is_reachability_root,
            ).where(GraphNode.repository_id == repository_id, GraphNode.node_type == "file")
        )
    ).all()
    language = {n: lang for n, lang, _e, _r in nodes if n in files and lang}
    roots = [n for n, _l, entry, root in nodes if entry or root]
    return roots, language


async def _reached_from(session: Any, repository_id: str, roots: list[str]) -> set[str]:
    """Every file the stored load edges reach from *roots*, the roots included."""
    from repowise.core.persistence.models import GraphEdge

    # Ceiling: every file-to-file load edge is read to walk from the roots;
    # one read per call, a few MB on the largest indexes measured so far.
    edges = (
        await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.edge_type.in_(_LOAD_EDGE_TYPES),
            )
        )
    ).all()
    adjacency: dict[str, list[str]] = {}
    for src, dst in edges:
        if _is_file(src) and _is_file(dst):
            adjacency.setdefault(src, []).append(dst)
    reached = set(roots)
    queue = deque(roots)
    while queue:
        for nxt in adjacency.get(queue.popleft(), ()):
            if nxt not in reached:
                reached.add(nxt)
                queue.append(nxt)
    return reached


async def _unreachable_findings(session: Any, repository_id: str, files: set[str]) -> set[str]:
    """*files* with an unreachable_file finding the dead-code surface serves:
    open, of a shown kind, in no gated language."""
    from repowise.core.analysis.dead_code.models import DeadCodeKind
    from repowise.core.persistence.crud import get_dead_code_findings

    unreachable: set[str] = set()
    ordered = sorted(files)
    for i in range(0, len(ordered), _CHUNK):
        found = await get_dead_code_findings(
            session,
            repository_id,
            kind=DeadCodeKind.UNREACHABLE_FILE.value,
            file_paths=ordered[i : i + _CHUNK],
        )
        unreachable.update(f.file_path for f in found)
    return unreachable


def _file_reachability(
    path: str,
    *,
    test_files: set[str],
    reached: set[str],
    unreachable: set[str],
    has_roots: bool,
) -> dict[str, str]:
    """One file's state and the basis it rests on, in order of precedence."""
    if path in test_files:
        return {"state": "test", "basis": "is_test"}
    if path in reached:
        return {"state": "reachable", "basis": "import_graph"}
    if path in unreachable:
        return {"state": "unreachable", "basis": "dead_code"}
    if not has_roots:
        return {"state": "unknown", "basis": "no_entry_points"}
    return {"state": "unknown", "basis": "no_import_path"}


async def _reachability(
    session: Any, repository_id: str, files: set[str], test_files: set[str]
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    roots, language = await _file_nodes(session, repository_id, files)
    reached = await _reached_from(session, repository_id, roots) if roots and files else set()
    unreachable = await _unreachable_findings(session, repository_id, files)
    out = {
        path: _file_reachability(
            path,
            test_files=test_files,
            reached=reached,
            unreachable=unreachable,
            has_roots=bool(roots),
        )
        for path in files
    }
    return out, language


# -- the answer -----------------------------------------------------------------


def _sites(
    index: UsageIndex, mapped: dict[str, str], ecosystem: str | None, facts: FileFacts | None
) -> list[dict[str, Any]]:
    by_file: dict[str, dict[str, dict[str, Any]]] = {}
    pairs = ((src, dst, names) for dst in mapped for src, names in index.by_target.get(dst, ()))
    for src, dst, names in pairs:
        basis = mapped[dst]
        if (
            ecosystem is not None
            and basis == "inferred"
            and index.nodes.get(dst) is None
            and facts is not None
            and LANGUAGE_ECOSYSTEM.get(facts.language.get(src, "")) != ecosystem
        ):
            continue  # an undeclared import name from another ecosystem
        module = dst[len(EXTERNAL_NODE_PREFIX) :]
        entry = by_file.setdefault(src, {}).setdefault(
            module, {"name": module, "mapping": basis, "symbols": []}
        )
        entry["symbols"].extend(n for n in names if n not in entry["symbols"])
    sites = []
    for path in sorted(by_file):
        modules = sorted(by_file[path].values(), key=lambda m: m["name"])
        declared = [m for m in modules if m["mapping"] == "declared"]
        # A declared link outranks an import-name match in the same file: the
        # site's symbols and line come from the declared modules alone.
        chosen = declared or modules
        sites.append(
            {
                "path": path,
                "modules": [{"name": m["name"], "mapping": m["mapping"]} for m in modules],
                "symbols": list(dict.fromkeys(n for m in chosen for n in m["symbols"])),
                "mapping": "declared" if declared else "inferred",
            }
        )
    return sites


def _enrich(site: dict[str, Any], name: str, facts: FileFacts) -> dict[str, Any]:
    path = site["path"]
    names = [m["name"] for m in site["modules"] if m["mapping"] == site["mapping"]]
    tokens = [*names, *(c for m in names for c in declaration_name_candidates(m)), name]
    found = facts.line_of(path, list(dict.fromkeys(tokens)))
    if found is not None:
        site["line"], site["statement"] = found
    if path in facts.component_of:
        site["component"] = facts.component_of[path]
    site["owners"], others = facts.owners(path)
    if others:
        site["owner_counts"] = others
    site["reachability"] = facts.reachability.get(path, {"state": "unknown", "basis": "not_in_graph"})
    return site


def _document(
    query: str, ecosystem: str | None, decls: list[Declaration], sites: list[dict[str, Any]]
) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    for s in sites:
        comp = s.get("component")
        if comp is None:
            continue
        row = components.setdefault(comp, {"name": comp, "files": 0, "owners": []})
        row["files"] += 1
        row["owners"].extend(o for o in s["owners"] if o not in row["owners"])
    states = Counter(s["reachability"]["state"] for s in sites)
    doc: dict[str, Any] = {
        "package": query,
        "declared": [
            {
                "package_key": d.key,
                "version": d.version,
                "declared_in": d.declared_in,
                "dev": d.is_dev_dep,
            }
            for d in sorted(decls, key=lambda d: (d.key, d.declared_in))
        ],
        "summary": {
            "files": len(sites),
            "components": len(components),
            "reachability": {k: states.get(k, 0) for k in ("reachable", "unreachable", "test", "unknown")},
            "inferred_files": sum(1 for s in sites if s["mapping"] == "inferred"),
        },
        "sites": sites,
        "components": sorted(components.values(), key=lambda c: (-c["files"], c["name"])),
        "method": METHOD,
    }
    if ecosystem:
        doc["ecosystem"] = ecosystem
    if not decls:
        doc["note"] = (
            "No manifest declares this name; any sites are matched by import name only."
            if sites
            else "No manifest declares this name and no import matches it."
        )
    elif not sites:
        doc["note"] = (
            "Declared but no import edge resolves to it. Its import name may differ from "
            "the package name (ask for the import name), or it is loaded without an import."
        )
    return doc


async def package_usages(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    queries: Sequence[tuple[str, str | None]],
    *,
    index: UsageIndex | None = None,
) -> list[dict[str, Any]]:
    """One usage document per ``(name, ecosystem)`` query; the index and the
    file facts are read once for the whole batch."""
    index = index or await load_usage_index(session, repository_id)
    matched = [(name, eco, *index.match(name, eco)) for name, eco in queries]
    candidate_files = {
        src
        for _n, _e, _d, mapped in matched
        for dst in mapped
        for src, _names in index.by_target.get(dst, ())
    }
    facts = await load_file_facts(session, repository_id, repo_root, candidate_files)
    docs = []
    for name, eco, decls, mapped in matched:
        sites = [_enrich(s, name, facts) for s in _sites(index, mapped, eco, facts)]
        docs.append(_document(name, eco, decls, sites))
    return docs


async def package_usage(
    session: Any, repository_id: str, repo_root: Path | str, query: str
) -> dict[str, Any]:
    """Usage of one package, named as ``name`` or ``ecosystem:name``."""
    name, eco = parse_query(query)
    return (await package_usages(session, repository_id, repo_root, [(name, eco)]))[0]


__all__ = [
    "LANGUAGE_ECOSYSTEM",
    "METHOD",
    "Declaration",
    "FileFacts",
    "UsageIndex",
    "dist_key",
    "load_file_facts",
    "load_usage_index",
    "lookup_key",
    "package_usage",
    "package_usages",
    "parse_query",
]
