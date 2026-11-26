"""Each stored advisory with its tier: the read every surface that shows one goes through.

:func:`tier_advisories` joins the last imported advisory document
(:func:`~repowise.core.inventory.advisories.load_advisories`) to the index:
where each package is imported (:mod:`.usage`), the proven calls into it
(``external_call_sites``), and the functions production code reaches
(:func:`~.vuln_reach.load_production_roots` walked over the reliable execution
graph). :func:`~.vuln_reach.tier_advisory` decides the tier, so the CLI, MCP
and the web UI never disagree.

The surfaces show three tiers, the ones a precision check on real repositories
supports:

``package_called``
    A function production code reaches makes a proven call into the package,
    at a call site shown. Python only (:data:`CALL_SITE_ECOSYSTEMS`).
``imported``
    Shipping code imports the package; no call is claimed.
``unknown``
    No shipping import was found, but the package may come through another
    dependency or under an import name the index does not link to it. It is
    never ranked below ``imported``.

Symbol-level ``reachable`` and the discounting tiers below ``imported`` are
computed by the fold but not shown: see :data:`SHOWN_TIERS`.

:func:`check_package` answers "is this package at this version safe to add?"
from the same stored document: it never fetches a feed.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.inventory.advisories import (
    Advisory,
    PackageAdvisories,
    StoredAdvisories,
    _verdict,
    load_advisories,
)
from repowise.core.inventory.osv import pick_fixed
from repowise.core.inventory.vuln_reach import (
    Dependency,
    ImportSite,
    TierResult,
    tier_advisory,
)

#: Ecosystems whose stored calls back a ``package_called`` claim. Ceiling: npm
#: call sites are stored but not claimed, because a call on a module a
#: ``jest.mock``/``vi.mock`` replaced is recorded as a call into the package;
#: add ``"npm"`` once the call-site writer refuses those bindings and a fresh
#: precision sample passes. Go has no call sites.
CALL_SITE_ECOSYSTEMS = frozenset({"pypi"})

#: The tiers the surfaces show, strongest first. Ceiling: no symbol-level
#: claim is made (OSV names no PyPI or npm symbols, and Go import-path
#: granularity over-claims), so ``reachable`` never appears; and every fold
#: tier below ``imported`` shows as ``unknown``, because the linker does not
#: yet tie every import name to its declaration (``yaml`` to ``PyYAML``, a Go
#: subpackage to its module) and a called package can land there.
SHOWN_TIERS = ("package_called", "imported", "unknown")

#: The tier production code is shown to call into: what an agent or a CI gate acts on.
CALLED_TIERS = ("package_called",)

#: Shown tier to rank: ``unknown`` sits with ``imported``, never below it.
_RANK = {"package_called": 0, "imported": 1, "unknown": 1}

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class TieredAdvisory:
    """One advisory on one package, with the version verdict and its tier.

    *affected* is True when the version in use is inside the advisory's range
    and None when no version could decide (never False: a proven-clear advisory
    is dropped). *fix* is the smallest fixed version above *version*.
    """

    ecosystem: str | None
    package: str
    advisory: Advisory
    affected: bool | None
    version: str | None
    fix: str | None
    tier: TierResult

    @property
    def shown_tier(self) -> str:
        """The tier the surfaces show (:data:`SHOWN_TIERS`)."""
        if self.tier.tier in ("reachable", "package_called"):
            return "package_called"
        return "imported" if self.tier.tier == "imported" else "unknown"

    @property
    def rank(self) -> int:
        return _RANK[self.shown_tier]

    @property
    def called(self) -> bool:
        return self.shown_tier in CALLED_TIERS

    def sites(self) -> list[str]:
        """``path:line`` of each evidence site (``path`` when it has no line)."""
        return [f"{s.path}:{s.line}" if s.line else s.path for s in self.tier.sites]

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "package": self.package,
            "ecosystem": self.ecosystem,
            "id": self.advisory.id,
            "tier": self.shown_tier,
            "basis": self.tier.basis,
        }
        if self.advisory.aliases:
            row["aliases"] = list(self.advisory.aliases)
        if self.advisory.severity:
            row["severity"] = self.advisory.severity
        if self.advisory.score is not None:
            row["score"] = self.advisory.score
        if self.version:
            row["version"] = self.version
        if self.fix:
            row["fix"] = self.fix
        row["affected"] = self.affected
        if self.advisory.summary:
            row["summary"] = self.advisory.summary
        row["sites"] = [
            {k: v for k, v in (("path", s.path), ("line", s.line), ("symbol", s.symbol)) if v}
            for s in self.tier.sites
        ]
        return row


def _order(row: TieredAdvisory) -> tuple:
    return (
        row.rank,
        _SEVERITY_RANK.get(row.advisory.severity or "", 4),
        -(row.advisory.score or 0.0),
        row.package.casefold(),
        row.advisory.id,
    )


def _dependency(pkg: PackageAdvisories, decls: Sequence[Any]) -> Dependency:
    """How the package enters the build, by the rule in :mod:`.vuln_reach`.

    ``direct`` is never claimed: a CycloneDX graph marks a package direct when
    the application depends on it, which does not rule out another dependency
    pulling it in too, and a package that may be transitive must not go below
    ``indirect_unknown``. Ceiling: test-only and unimported packages read as
    ``indirect_unknown``; carrying "no other dependent" from the CycloneDX
    graph would let them go lower.
    """
    if pkg.direct is False:
        return "indirect"
    # go.mod ``// indirect`` is stored as a dev dependency.
    if any(d.ecosystem == "go" and d.is_dev_dep for d in decls):
        return "indirect"
    return "unknown"


def _ecosystem(pkg: PackageAdvisories, decls: Sequence[Any]) -> str | None:
    if pkg.ecosystem:
        return pkg.ecosystem
    found = {d.ecosystem for d in decls}
    return found.pop() if len(found) == 1 else None


def _import_sites(doc: dict[str, Any]) -> list[ImportSite]:
    return [
        ImportSite(site["path"], module["name"], site.get("line"))
        for site in doc["sites"]
        for module in site["modules"]
        if module["mapping"] == site["mapping"]
    ]


async def _cli_commands(session: Any, repository_id: str) -> list[str]:
    """CLI command functions, seeded over the stored import, framework and reference edges.

    Ceiling: decorators are not stored, so a CLI declared only by a decorator
    is missed; its entry-point file still roots it.
    """
    import networkx as nx
    from sqlalchemy import select

    from repowise.core.analysis.execution_roles import seed_roles
    from repowise.core.persistence.models import GraphEdge, GraphNode

    graph = nx.DiGraph()
    nodes = await session.execute(
        select(
            GraphNode.node_id,
            GraphNode.node_type,
            GraphNode.kind,
            GraphNode.name,
            GraphNode.file_path,
            GraphNode.language,
        ).where(GraphNode.repository_id == repository_id)
    )
    for node_id, node_type, kind, name, file_path, language in nodes:
        graph.add_node(
            node_id,
            node_type=node_type,
            kind=kind,
            name=name,
            file_path=file_path,
            language=language,
        )
    edges = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.hint_source,
        ).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.edge_type.in_(("imports", "framework", "framework_binds", "references")),
        )
    )
    for source, target, edge_type, hint in edges:
        graph.add_edge(source, target, edge_type=edge_type, hint_source=hint)
    return sorted(seed_roles(graph).by_role.get("cli", set()))


async def _reachable(session: Any, repository_id: str) -> frozenset[str]:
    """Execution-graph nodes reached from the production roots."""
    from repowise.core.analysis.test_reach.db import call_graph_from_db
    from repowise.core.inventory.vuln_reach import load_production_roots

    roots = await load_production_roots(
        session, repository_id, cli_commands=await _cli_commands(session, repository_id)
    )
    if not roots:
        return frozenset()
    graph = await call_graph_from_db(session, repository_id)
    return frozenset(graph.forward_reachable(roots))


@dataclass(frozen=True)
class _Package:
    """One stored package with advisories, joined to the index."""

    pkg: PackageAdvisories
    ecosystem: str | None
    nodes: frozenset[str]
    imports: tuple[ImportSite, ...]
    dependency: Dependency
    #: ``(advisory, affected, version)`` for each advisory not proven clear.
    verdicts: tuple[tuple[Advisory, bool | None, str | None], ...]


@dataclass(frozen=True)
class _Joined:
    packages: tuple[_Package, ...]
    calls: tuple[Any, ...]
    calls_complete: bool
    reachable: frozenset[str]
    origins: dict[str, str | None]


async def _join(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    stored: StoredAdvisories,
    files: Collection[str] | None,
) -> _Joined:
    """Everything the tier fold reads, in one pass over the index."""
    from repowise.core.analysis.test_reach.db import stored_origins
    from repowise.core.inventory.usage import load_usage_index, package_usages
    from repowise.core.persistence.crud.external_call_sites import (
        external_call_sites_complete,
        list_external_call_sites,
    )

    pkgs = [p for p in stored.packages if p.advisories]
    index = await load_usage_index(session, repository_id)
    docs = await package_usages(
        session, repository_id, repo_root, [(p.name, p.ecosystem) for p in pkgs], index=index
    )
    joined: list[_Package] = []
    for pkg, doc in zip(pkgs, docs, strict=True):
        decls, mapped = index.match(pkg.name, pkg.ecosystem)
        ecosystem = _ecosystem(pkg, decls)
        verdicts = tuple(
            (a, affected, version)
            for a in pkg.advisories
            for affected, version, _basis in [_verdict(a, ecosystem, pkg.versions, doc["declared"])]
            if affected is not False
        )
        if verdicts:
            joined.append(
                _Package(
                    pkg, ecosystem, frozenset(mapped), tuple(_import_sites(doc)),
                    _dependency(pkg, decls), verdicts,
                )
            )  # fmt: skip
    nodes = sorted({n for p in joined for n in p.nodes})
    calls = (
        await list_external_call_sites(
            session,
            repository_id,
            external_nodes=nodes,
            file_paths=None if files is None else sorted(files),
        )
        if nodes
        else []
    )
    paths = {s.path for p in joined for s in p.imports} | {c.file_path for c in calls}
    imported = any(p.imports for p in joined)
    return _Joined(
        tuple(joined),
        tuple(calls),
        await external_call_sites_complete(session, repository_id),
        await _reachable(session, repository_id) if imported else frozenset(),
        await stored_origins(session, repository_id, sorted(paths)),
    )


def _tier_all(joined: _Joined, calls: Sequence[Any]) -> list[TieredAdvisory]:
    """:func:`~.vuln_reach.tier_advisory` for every joined advisory, with *calls* as call evidence."""
    out: list[TieredAdvisory] = []
    for p in joined.packages:
        pkg_calls = [c for c in calls if c.external_node in p.nodes]
        known = joined.calls_complete and p.ecosystem in CALL_SITE_ECOSYSTEMS
        for advisory, affected, version in p.verdicts:
            tier = tier_advisory(
                ecosystem=p.ecosystem or "",
                # No symbol-level claim is shown (SHOWN_TIERS), so none is computed.
                affected=(),
                import_sites=p.imports,
                call_sites=pkg_calls,
                reachable=joined.reachable,
                dependency=p.dependency,
                origins=joined.origins,
                call_sites_known=known,
            )
            fix = pick_fixed(p.ecosystem, advisory.fixed, version) if version else None
            out.append(
                TieredAdvisory(p.ecosystem, p.pkg.name, advisory, affected, version, fix, tier)
            )
    out.sort(key=_order)
    return out


async def tier_advisories(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    *,
    packages: Sequence[tuple[str | None, str]] | None = None,
) -> list[TieredAdvisory] | None:
    """Every stored advisory the version in use is not proven clear of, tiered,
    strongest tier then severity first. ``None`` when no document was imported.

    *packages* narrows to those ``(ecosystem, name)`` pairs.
    """
    stored = await load_advisories(session, repository_id, packages=packages)
    if stored is None:
        return None
    if not any(p.advisories for p in stored.packages):
        return []
    joined = await _join(session, repository_id, repo_root, stored, None)
    return _tier_all(joined, joined.calls)


async def called_advisories_in_files(
    session: Any, repository_id: str, repo_root: Path | str, files: Iterable[str]
) -> dict[str, list[TieredAdvisory]]:
    """Per file, the advisories production code reaches through a call in that file.

    Only calls in the file count as call evidence, so each row's sites are
    calls in it. A file with no stored call into a package that has an
    advisory is absent, and that common case costs one indexed read: the
    stored calls of *files*.
    """
    from repowise.core.persistence.crud.external_call_sites import (
        external_call_sites_complete,
        list_external_call_sites,
    )

    wanted = sorted(set(files))
    if not wanted:
        return {}
    if not await list_external_call_sites(session, repository_id, file_paths=wanted):
        return {}
    # An incomplete table proves no call, so nothing here could be "called".
    if not await external_call_sites_complete(session, repository_id):
        return {}
    stored = await load_advisories(session, repository_id)
    if stored is None or not any(p.advisories for p in stored.packages):
        return {}
    joined = await _join(session, repository_id, repo_root, stored, wanted)
    out: dict[str, list[TieredAdvisory]] = {}
    for path in sorted({c.file_path for c in joined.calls}):
        rows = _tier_all(joined, [c for c in joined.calls if c.file_path == path])
        called = [r for r in rows if r.called]
        if called:
            out[path] = called
    return out


@dataclass(frozen=True)
class PackageCheck:
    """The answer to "may I add *name* at *version*?" from the stored document.

    *verdict* is ``has_advisories`` (at least one advisory not proven clear of
    *version*), ``ok`` (the document names the package and none applies), or
    ``unknown_no_source`` (no document, or it does not name the package).
    """

    ecosystem: str | None
    name: str
    version: str | None
    verdict: str
    advisories: tuple[tuple[Advisory, bool | None, str | None], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"package": self.name, "verdict": self.verdict}
        if self.ecosystem:
            row["ecosystem"] = self.ecosystem
        if self.version:
            row["version"] = self.version
        if self.advisories:
            row["advisories"] = [
                {
                    "id": a.id,
                    **({"severity": a.severity} if a.severity else {}),
                    "affected": affected,
                    **({"fix": fix} if fix else {}),
                }
                for a, affected, fix in self.advisories
            ]
        return row


def check_package(
    stored: StoredAdvisories | None, ecosystem: str | None, name: str, version: str | None
) -> PackageCheck:
    """Whether the stored document lists an advisory for *name* at *version*.

    With no *version*, every advisory the document names for the package
    counts, undecided. Nothing is fetched: a package the document does not
    name is ``unknown_no_source``.
    """
    pkg = stored.get(ecosystem, name) if stored is not None else None
    if pkg is None:
        return PackageCheck(ecosystem, name, version, "unknown_no_source")
    eco = ecosystem or pkg.ecosystem
    found = []
    for advisory in pkg.advisories:
        affected = advisory.affects(eco, version) if version else None
        if affected is False:
            continue
        found.append(
            (advisory, affected, pick_fixed(eco, advisory.fixed, version) if version else None)
        )
    verdict = "has_advisories" if found else "ok"
    return PackageCheck(eco, pkg.name, version, verdict, tuple(found))


def split_version(query: str) -> tuple[str, str | None]:
    """``name@1.2`` to ``("name", "1.2")``; an npm scope's leading ``@`` is not a version."""
    at = query.rfind("@")
    if at <= 0 or query[at - 1] in ":/" or not query[at + 1 :]:
        return query, None
    return query[:at], query[at + 1 :]


__all__ = [
    "CALLED_TIERS",
    "CALL_SITE_ECOSYSTEMS",
    "PackageCheck",
    "TieredAdvisory",
    "called_advisories_in_files",
    "check_package",
    "split_version",
    "tier_advisories",
]
