""".NET types a framework finds by scanning an assembly.

``modelBuilder.ApplyConfigurationsFromAssembly(...)`` makes EF Core
instantiate every ``IEntityTypeConfiguration<T>`` in the assembly;
``AddFastEndpoints()`` does the same for every ``Endpoint<,>`` and
``Validator<T>``, ``AddMediator()`` for every handler. No source names the
discovered class, so the import graph sees nothing and dead code called every
one of them unused.

A type is wired here only on two pieces of evidence together: it derives from
or implements a base the framework discovers (read from the parser's heritage,
so the name is the declared base and not a naming convention), and the
repository makes that framework's registration call. The edge runs from the
file making the call to the file declaring the type, with no names, like the
controller edges in :mod:`.aspnet`: the framework constructs the type and its
file's public types are the request, response and mapper types it binds.

Ceiling: the call is matched anywhere in the repository rather than in the
project whose assembly it scans, which only widens a rescue. A type discovered
with no registration call at all (EF's ``IDesignTimeDbContextFactory``, found
by the ``dotnet ef`` tool) is not covered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .base import _add_edge_if_new, read_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext
    from .base import DetectionContext


@dataclass(frozen=True)
class _Discovery:
    """One framework: the bases it discovers and the calls that start it."""

    bases: frozenset[str]
    calls: tuple[str, ...]


#: Base names are as the heritage records them: generic arity dropped.
_DISCOVERIES: tuple[_Discovery, ...] = (
    # EF Core model configuration.
    _Discovery(frozenset({"IEntityTypeConfiguration"}), ("ApplyConfigurationsFromAssembly",)),
    # FastEndpoints: endpoints, validators, mappers, summaries, groups, processors.
    _Discovery(
        frozenset(
            {
                "Endpoint",
                "EndpointWithoutRequest",
                "Ep",
                "Validator",
                "Mapper",
                "RequestMapper",
                "ResponseMapper",
                "Summary",
                "Group",
                "SubGroup",
                "IGlobalPreProcessor",
                "IGlobalPostProcessor",
                "IPreProcessor",
                "IPostProcessor",
            }
        ),
        ("AddFastEndpoints",),
    ),
    # MediatR.
    _Discovery(
        frozenset(
            {
                "IRequestHandler",
                "INotificationHandler",
                "IStreamRequestHandler",
                "IPipelineBehavior",
                "IRequestPreProcessor",
                "IRequestPostProcessor",
                "IRequestExceptionHandler",
                "IRequestExceptionAction",
            }
        ),
        ("AddMediatR", "RegisterServicesFromAssembly", "RegisterServicesFromAssemblies",
         "RegisterServicesFromAssemblyContaining"),
    ),
    # Mediator (source generated).
    _Discovery(
        frozenset(
            {
                "IRequestHandler",
                "ICommandHandler",
                "IQueryHandler",
                "INotificationHandler",
                "IStreamRequestHandler",
                "IStreamCommandHandler",
                "IStreamQueryHandler",
                "IPipelineBehavior",
                "IStreamPipelineBehavior",
            }
        ),
        ("AddMediator",),
    ),
    # FluentValidation assembly registration.
    _Discovery(
        frozenset({"AbstractValidator"}),
        ("AddValidatorsFromAssembly", "AddValidatorsFromAssemblies",
         "AddValidatorsFromAssemblyContaining", "AddFluentValidation"),
    ),
    # MinimalApi.Endpoint.
    _Discovery(frozenset({"IEndpoint"}), ("AddEndpoints",)),
    # AutoMapper profiles.
    _Discovery(frozenset({"Profile"}), ("AddAutoMapper",)),
    # ASP.NET Core MVC controllers, including Ardalis.ApiEndpoints bases.
    _Discovery(
        frozenset({"Controller", "ControllerBase", "EndpointBaseAsync", "EndpointBaseSync"}),
        ("AddControllers", "AddControllersWithViews", "AddMvc", "AddMvcCore", "MapControllers"),
    ),
)

_CALL_RE = re.compile(
    r"\b(" + "|".join(sorted({c for d in _DISCOVERIES for c in d.calls})) + r")\s*[<(]"
)


def _registering_files(cs_files: list[tuple[str, Any]]) -> dict[str, str]:
    """Registration call -> the first file (in path order) that makes it."""
    found: dict[str, str] = {}
    for path, parsed in sorted(cs_files, key=lambda item: item[0]):
        text = read_text(parsed, encoding="utf-8-sig")
        for call in _CALL_RE.findall(text):
            found.setdefault(call, path)
    return found


def _discovered_bases(registrars: dict[str, str]) -> dict[str, str]:
    """Base name -> the file registering a framework that discovers it."""
    out: dict[str, str] = {}
    for discovery in _DISCOVERIES:
        source = next((registrars[c] for c in discovery.calls if c in registrars), None)
        if source is not None:
            for base in discovery.bases:
                out.setdefault(base, source)
    return out


def _add_discovery_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], path_set: set[str]
) -> int:
    cs_files = [
        (path, parsed)
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "csharp" and path in path_set
    ]
    bases = _discovered_bases(_registering_files(cs_files))
    if not bases:
        return 0
    count = 0
    for path, parsed in cs_files:
        for relation in parsed.heritage:
            source = bases.get(relation.parent_name.rsplit(".", 1)[-1])
            if source is not None and _add_edge_if_new(graph, source, path):
                count += 1
    return count


class _AssemblyScanHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "csharp" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_discovery_edges(graph, parsed_files, path_set)


HANDLERS = [_AssemblyScanHandler()]
