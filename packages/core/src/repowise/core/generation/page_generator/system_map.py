"""Gather a generation run's facts into the overview's system map.

Everything here is read from what the run already holds: the parsed files and
their graph, the declared dependencies, the curated entry points and the
module pages. HTTP links are the one thing computed fresh, by running the
contract extractors over the in-memory sources, since a single-repository
index does not store them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.c4.containers import (
    assign_containers,
    container_name,
    is_manifest,
    manifest_roots,
)
from repowise.core.analysis.external_systems import build_declaration_links
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.support_paths import file_population, is_support_path

from ..architecture_map import SystemMap, build_system_map

log = structlog.get_logger(__name__)

_CODE_LANGUAGES = REGISTRY.code_languages()


def build_run_system_map(run: Any) -> SystemMap | None:
    """The system map for *run*, or ``None`` when the structure cannot support one."""
    files = {
        pf.file_info.path: pf.file_info.language
        for pf in run.parsed_files
        if pf.file_info.language in _CODE_LANGUAGES
        and file_population(pf.file_info.path, is_test=pf.file_info.is_test) == "production"
        and not is_support_path(pf.file_info.path)
    }
    if not files:
        return None
    graph = run.graph_builder.graph()
    manifests = {s.get("declared_in") for s in run.external_systems if s.get("declared_in")}
    manifests |= {pf.file_info.path for pf in run.parsed_files if is_manifest(pf.file_info.path)}
    repo_root = Path(run.repo_path) if run.repo_path else None
    links, routes = _http_links(run, files, manifests)
    package_names = {
        root: name
        for root, found in manifest_roots(manifests).items()
        if (name := container_name(repo_root, found))
    }
    return build_system_map(
        files,
        ((s, t, d.get("edge_type", "")) for s, t, d in graph.edges(data=True)),
        repo_name=run.repo_name,
        weights=run.pagerank,
        manifests=manifests,
        package_names=package_names,
        entry_points=run.kg_ctx.get_entry_points(),
        # A file that defines nothing only re-exports or launches.
        barrels={pf.file_info.path for pf in run.parsed_files if not pf.symbols} & files.keys(),
        route_files=routes,
        stores=_database_clients(run.external_systems, graph),
        http_links=links,
        pages=[
            (mg.key, frozenset(mg.context_paths or mg.file_paths))
            for mg in run.sel_module_groups
        ],
    )


def _database_clients(external_systems: list[dict], graph: Any) -> dict[str, str]:
    """``external:*`` node id -> database client name, for runtime dependencies."""
    declarations = [
        s
        for s in external_systems
        if s.get("io_kind") == "db"
        and not s.get("is_dev_dep")
        and not is_support_path(s.get("declared_in") or "")
    ]
    if not declarations:
        return {}
    nodes = [{"node_id": n} for n in graph.nodes if str(n).startswith("external:")]
    return {link.node_id: link.name for link in build_declaration_links(declarations, nodes)}


def _http_links(
    run: Any, files: dict[str, str], manifests: set[str]
) -> tuple[list[tuple[str, str]], set[str]]:
    """``(calling file, route file)`` pairs between containers, and the route files."""
    from repowise.core.workspace.extractors import HttpExtractor
    from repowise.core.workspace.matching import match_contracts

    owner, _ = assign_containers(manifests, files)
    suffixes = HttpExtractor.source_extensions()
    sources = [
        (path, suffix, run.source_map[path].decode("utf-8", errors="replace"))
        for path in files
        if path in run.source_map and (suffix := Path(path).suffix) in suffixes
    ]
    try:
        contracts = HttpExtractor().extract(Path(run.repo_path or "."), files=sources)
    except Exception as exc:
        # The map loses its runtime arrows, not its structure.
        log.warning("system_map.http_extraction_failed", error=str(exc))
        return [], set()
    # One service per container, so a call between containers counts as a link.
    scoped = [replace(c, service=owner.get(c.file_path)) for c in contracts]
    links = {(link.consumer_file, link.provider_file) for link in match_contracts(scoped)}
    routes = {c.file_path for c in contracts if c.role == "provider" and c.contract_type == "http"}
    return sorted(links), routes
