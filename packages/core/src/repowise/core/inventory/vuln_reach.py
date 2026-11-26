"""How close a package advisory comes to production code: one tier per (package, advisory).

A pure fold over records the index already holds, so every caller (the CLI,
MCP, a reader of an uploaded index) gets the same answer from the same rows:

* production roots: entry-point files (their top-level code), the functions
  serving HTTP routes, and CLI commands, kept only in production code
  (:mod:`repowise.core.code_origin`);
* the functions the reliable execution graph reaches from those roots
  (:meth:`ExecutionGraphIndex.forward_reachable`);
* the package's import sites and its external call sites (a repository
  function calling a name an import of the package binds);
* the advisory's affected package paths and symbols;
* whether the package is a direct dependency only, or may also arrive through
  another dependency.

Tiers, strongest first:

``reachable``
    The advisory names affected symbols and a production-reachable function
    calls one. Go is judged at import-path granularity: a reachable file
    imports an affected package path.
``package_called``
    A production-reachable function makes a proven call into the package, but
    no call to a named affected symbol is proven (or the advisory names none,
    as PyPI and npm advisories rarely do).
``imported``
    Code that ships imports the package, and no reachable call into it is
    proven. Missing call proof stays here.
``indirect_unknown``
    No shipping import site, but the package is, or may be, pulled in by
    another dependency, whose own calls into it the index cannot see.
``test_or_dev_only``
    A direct-only dependency whose every import site is in test, tooling,
    build or docs-example code.
``not_imported``
    A direct-only dependency no file imports.

Nothing here says "unreachable". A tier below ``imported`` rests on proof: a
direct-only dependency with no import site, or only non-shipping ones. A
package that may be transitive never goes below ``indirect_unknown``, so a
package named only by an SBOM or bundle metadata, with no import site and no
dependency graph, is ``indirect_unknown``: never elevated, never buried.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from repowise.core.code_origin import path_origin
from repowise.core.ingestion.symbol_identity import file_of_symbol, module_symbol_id

Tier = Literal[
    "reachable",
    "package_called",
    "imported",
    "indirect_unknown",
    "test_or_dev_only",
    "not_imported",
]
TIERS: tuple[Tier, ...] = (
    "reachable",
    "package_called",
    "imported",
    "indirect_unknown",
    "test_or_dev_only",
    "not_imported",
)
"""Every tier, strongest first."""

Dependency = Literal["direct", "indirect", "unknown"]
"""How the package enters the build.

``direct``: the repository declares it and the resolved dependency graph shows
no other dependency requiring it. ``indirect``: another dependency requires it
(it may be declared too). ``unknown``: no resolved graph to tell.
"""

#: Origins whose code never ships, so an import there cannot expose production.
#: Vendored and generated code ships with the product, and ``data`` is not
#: certain enough to discount, so all three keep a site at ``imported``.
NON_SHIPPING_ORIGINS = frozenset({"test", "tooling", "build", "docs_example"})

#: Evidence sites kept per tier.
MAX_SITES = 3

#: Ecosystems whose advisories name affected package paths, judged by import.
_IMPORT_PATH_ECOSYSTEMS = frozenset({"go"})
#: The character that opens a submodule after a package name, per ecosystem.
_SUBMODULE_SEPARATORS = {"npm": "/", "pypi": "."}


class CallRecord(Protocol):
    """The fields of ``ingestion.external_calls.ExternalCallSite`` the fold reads."""

    @property
    def file_path(self) -> str: ...
    @property
    def line(self) -> int: ...
    @property
    def caller_symbol_id(self) -> str: ...
    @property
    def module(self) -> str: ...
    @property
    def symbol_path(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ImportSite:
    """A file importing a module of the package (``requests.adapters``, ``lodash/merge``)."""

    path: str
    module: str
    line: int | None = None


class AffectedImport(Protocol):
    """An advisory's affected package path and the symbols in it (OSV ``imports[]``),
    as ``inventory.osv.AffectedImport`` carries them.

    No *symbols* means the advisory names none for this path. An empty *path*
    means it named symbols without one: they are matched anywhere in the package.
    """

    @property
    def path(self) -> str: ...
    @property
    def symbols(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class Site:
    path: str
    line: int | None = None
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class TierResult:
    """The tier, the rule that decided it, and up to :data:`MAX_SITES` sites."""

    tier: Tier
    basis: str
    sites: tuple[Site, ...] = field(default=())


def _origin_reader(origins: Mapping[str, str] | None) -> Callable[[str], str]:
    """Stored ``code_origin`` per path when given, the path rule otherwise."""
    stored = origins or {}
    return lambda path: stored.get(path) or path_origin(path)


def production_roots(
    *,
    entry_files: Iterable[str] = (),
    route_handlers: Iterable[str | None] = (),
    cli_commands: Iterable[str] = (),
    origins: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Execution-graph nodes production work starts from.

    *entry_files* are files flagged ``is_entry_point``; their top-level code
    (``<file>::__module__``) is the root. *route_handlers* are
    ``http_routes.handler_symbol_id`` values (None skipped). *cli_commands* are
    command function ids. Only roots in files whose origin is ``production``
    count: a test's ``main`` or a tutorial's route never starts production work.
    """
    origin = _origin_reader(origins)
    roots = {module_symbol_id(path) for path in entry_files if origin(path) == "production"}
    for symbol in (*route_handlers, *cli_commands):
        if symbol and origin(file_of_symbol(symbol)) == "production":
            roots.add(symbol)
    return frozenset(roots)


def _module_matches(module: str, path: str, ecosystem: str) -> bool:
    """Whether an imported *module* is the affected package *path*, or a submodule
    of it. A Go package path is exact; an npm submodule follows ``/`` (``lodash.merge``
    is another package), a Python one follows ``.``."""
    if module == path:
        return True
    if ecosystem in _IMPORT_PATH_ECOSYSTEMS:
        return False
    separators = _SUBMODULE_SEPARATORS.get(ecosystem, "./")
    return any(module.startswith(f"{path}{sep}") for sep in separators)


def _names_symbol(call: CallRecord, affected: AffectedImport, ecosystem: str) -> bool:
    """Whether *call* is a call of a symbol *affected* names.

    ``Session.get`` named matches a call of ``Session.get`` only; a bare ``get``
    never matches ``Session.get``. A default binding's leading ``default`` is
    the module's export, so ``default.get`` is a call of ``get``.
    """
    if not affected.symbols:
        return False
    # No path: the record named symbols only, and the calls are the package's.
    if affected.path and not _module_matches(call.module, affected.path, ecosystem):
        return False
    called = call.symbol_path
    plain = called.removeprefix("default.")
    return called in affected.symbols or plain in affected.symbols


def _sites(items: Iterable[Site]) -> tuple[Site, ...]:
    unique = sorted(set(items), key=lambda s: (s.path, s.line or 0, s.symbol or ""))
    return tuple(unique[:MAX_SITES])


def _call_site(call: CallRecord) -> Site:
    symbol = f"{call.module}.{call.symbol_path}" if call.symbol_path else call.module
    return Site(call.file_path, call.line, symbol)


def _below_imported(imports: tuple[ImportSite, ...], dependency: Dependency) -> TierResult:
    """The tier of a package no shipping code imports."""
    sites = _sites(Site(s.path, s.line) for s in imports)
    if dependency != "direct":
        basis = "indirect_dependency" if dependency == "indirect" else "dependency_graph_unknown"
        return TierResult("indirect_unknown", basis, sites)
    if imports:
        return TierResult("test_or_dev_only", "non_shipping_imports_only", sites)
    return TierResult("not_imported", "no_import_site")


def tier_advisory(
    *,
    ecosystem: str,
    affected: Iterable[AffectedImport],
    import_sites: Iterable[ImportSite],
    call_sites: Iterable[CallRecord] = (),
    reachable: Collection[str] = frozenset(),
    dependency: Dependency = "unknown",
    origins: Mapping[str, str] | None = None,
    call_sites_known: bool = True,
) -> TierResult:
    """The tier of one advisory on one package, with its evidence.

    *import_sites* and *call_sites* are the package's. *reachable* is what
    :meth:`ExecutionGraphIndex.forward_reachable` returned from
    :func:`production_roots`. *call_sites_known* is False when the call-site
    table is not complete for the repository or does not cover the language,
    so no call can be proven. *origins* maps paths to stored ``code_origin``.
    """
    origin = _origin_reader(origins)
    affected = tuple(affected)
    imports = tuple(import_sites)
    shipping = tuple(s for s in imports if origin(s.path) not in NON_SHIPPING_ORIGINS)
    if not shipping:
        return _below_imported(imports, dependency)
    if ecosystem in _IMPORT_PATH_ECOSYSTEMS:
        by_path = _by_import_path(shipping, affected, reachable, ecosystem)
        if by_path is not None:
            return by_path
    if not call_sites_known:
        return TierResult("imported", "no_call_records", _import_sites(shipping))
    reached_calls = _reached_calls(call_sites, imports, reachable, origin, ecosystem)
    return _by_calls(reached_calls, affected, ecosystem) or TierResult(
        "imported", "import", _import_sites(shipping)
    )


def _reached_calls(
    call_sites: Iterable[CallRecord],
    imports: tuple[ImportSite, ...],
    reachable: Collection[str],
    origin: Callable[[str], str],
    ecosystem: str,
) -> list[CallRecord]:
    """Calls a production-reachable function in shipping code makes into the
    package: into a module one of its import sites names, or a submodule of one,
    so a record of a sibling package (``lodash.merge`` beside ``lodash``) never counts."""
    modules = {s.module for s in imports}
    return [
        call
        for call in call_sites
        if call.caller_symbol_id in reachable
        and origin(call.file_path) not in NON_SHIPPING_ORIGINS
        and any(_module_matches(call.module, module, ecosystem) for module in modules)
    ]


def _import_sites(imports: Iterable[ImportSite]) -> tuple[Site, ...]:
    return _sites(Site(s.path, s.line) for s in imports)


def _by_import_path(
    shipping: tuple[ImportSite, ...],
    affected: tuple[AffectedImport, ...],
    reachable: Collection[str],
    ecosystem: str,
) -> TierResult | None:
    """``reachable`` when a reached file imports an affected path that names symbols."""
    named_paths = [a.path for a in affected if a.path and a.symbols]
    if not named_paths:
        return None
    reached_files = {file_of_symbol(node) for node in reachable}
    hits = [
        s
        for s in shipping
        if s.path in reached_files
        and any(_module_matches(s.module, path, ecosystem) for path in named_paths)
    ]
    return TierResult("reachable", "import_path", _import_sites(hits)) if hits else None


def _by_calls(
    reached_calls: list[CallRecord], affected: tuple[AffectedImport, ...], ecosystem: str
) -> TierResult | None:
    """``reachable`` for a reached call of a named symbol, else ``package_called``."""
    named = [c for c in reached_calls if any(_names_symbol(c, a, ecosystem) for a in affected)]
    if named:
        return TierResult("reachable", "call", _sites(map(_call_site, named)))
    if reached_calls:
        return TierResult("package_called", "call", _sites(map(_call_site, reached_calls)))
    return None


__all__ = [
    "MAX_SITES",
    "NON_SHIPPING_ORIGINS",
    "TIERS",
    "AffectedImport",
    "CallRecord",
    "Dependency",
    "ImportSite",
    "Site",
    "Tier",
    "TierResult",
    "production_roots",
    "tier_advisory",
]
