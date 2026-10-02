""".NET types a framework finds by scanning an assembly.

``modelBuilder.ApplyConfigurationsFromAssembly(...)`` makes EF Core
instantiate every ``IEntityTypeConfiguration<T>`` in the assembly;
``AddFastEndpoints()`` does the same for every ``Endpoint<,>`` and
``Validator<T>``, ``AddMediator()`` for every handler. No source names the
discovered class, so the import graph sees nothing and dead code called every
one of them unused.

A type is wired here only on evidence: it derives from or implements a base
the framework discovers (read from the parser's heritage, so the name is the
declared base and not a naming convention), and a file of the type's project,
or of a project that references it, makes that framework's registration call
in code (a call in a comment or a string does not count). The edge runs from
the registering file to the type's file and names only the discovered types, so
a dead sibling type in the same file is still reported. MVC controllers are
wired by :mod:`.aspnet`.

Ceiling: with no .NET project index (no ``.csproj`` in the repository) the
call is matched anywhere in the repository. A type discovered with no
registration call at all (EF's ``IDesignTimeDbContextFactory``, found by the
``dotnet ef`` tool) is not covered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers.dotnet.index import get_or_build_index
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
)

_CALL_RE = re.compile(
    r"\b(" + "|".join(sorted({c for d in _DISCOVERIES for c in d.calls})) + r")\s*[<(]"
)

#: A comment or a string literal, blanked so a call written in one is not read
#: as the registration. Read from the text rather than the parsed call sites,
#: which miss a call on a member chain in top-level statements
#: (``builder.Services.AddMediator();`` in ``Program.cs``).
_COMMENT_OR_STRING = re.compile(
    # A C# 11 raw string (three or more quotes) first, so its quotes are not
    # read as an empty string followed by code.
    r'("{3,}).*?\1'
    r"""|//[^\n]*|/\*.*?\*/|@"(?:[^"]|"")*"|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""",
    re.DOTALL,
)


def _calls_made(parsed: Any) -> set[str]:
    """Registration calls written in *parsed*'s code, comments and strings excluded."""
    text = read_text(parsed, encoding="utf-8-sig")
    if not text:
        return set()
    return set(_CALL_RE.findall(_COMMENT_OR_STRING.sub(" ", text)))


class _Projects:
    """Which project holds a file, and which projects can see a project."""

    def __init__(self, index: Any | None, repo_path: Path | None) -> None:
        self._index = index
        self._root = repo_path
        self._seen_by: dict[Any, frozenset[Any]] = {}

    def of(self, path: str) -> Any | None:
        if self._index is None or self._root is None:
            return None
        project = self._index.project_for_file(self._root / path)
        return None if project is None else project.path

    def can_register(self, registrar: Any | None, owner: Any | None) -> bool:
        """Whether a call in *registrar*'s project scans *owner*'s project.

        Its own project or one it references, directly or transitively. With no
        project for either side there is no scope to check.
        """
        if registrar is None or owner is None:
            return True
        return owner in self._closure(registrar)

    def _closure(self, project: Any) -> frozenset[Any]:
        cached = self._seen_by.get(project)
        if cached is None:
            cached = self._seen_by[project] = _reachable(project, self._index.referenced_projects)
        return cached


def _reachable(start: Any, step: Any) -> frozenset[Any]:
    """*start* and everything *step* leads to from it, transitively."""
    seen, stack = {start}, [start]
    while stack:
        fresh = set(step(stack.pop())) - seen
        seen |= fresh
        stack.extend(fresh)
    return frozenset(seen)


def _registrars(
    cs_files: list[tuple[str, Any]], projects: _Projects
) -> dict[str, list[tuple[str, Any]]]:
    """Discovered base -> ``(file, project)`` of every file registering its framework."""
    by_call: dict[str, list[tuple[str, Any]]] = {}
    for path, parsed in sorted(cs_files, key=lambda item: item[0]):
        for call in _calls_made(parsed):
            by_call.setdefault(call, []).append((path, projects.of(path)))
    out: dict[str, list[tuple[str, Any]]] = {}
    for discovery in _DISCOVERIES:
        sites = [site for call in discovery.calls for site in by_call.get(call, ())]
        for base in discovery.bases if sites else ():
            out.setdefault(base, []).extend(sites)
    return out


def _wired_types(
    cs_files: list[tuple[str, Any]],
    projects: _Projects,
    registrars: dict[str, list[tuple[str, Any]]],
) -> dict[tuple[str, str], list[str]]:
    """``(registering file, type file)`` -> the discovered types the edge names."""
    wired: dict[tuple[str, str], list[str]] = {}
    for path, parsed in cs_files:
        owner = projects.of(path)
        for relation in getattr(parsed, "heritage", ()):
            sites = registrars.get(relation.parent_name.rsplit(".", 1)[-1], ())
            source = next((f for f, p in sites if projects.can_register(p, owner)), path)
            names = wired.setdefault((source, path), [])
            if relation.child_name not in names:
                names.append(relation.child_name)
    return wired


def _add_discovery_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    cs_files = [
        (path, parsed)
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "csharp" and path in path_set
    ]
    projects = _Projects(get_or_build_index(ctx), ctx.repo_path)
    registrars = _registrars(cs_files, projects)
    if not registrars:
        return 0
    wired = _wired_types(cs_files, projects, registrars)
    # A type with no registrar in scope maps to its own file, which adds nothing.
    return sum(_add_edge_if_new(graph, src, dst, names) for (src, dst), names in wired.items())


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
        return _add_discovery_edges(graph, parsed_files, ctx, path_set)


HANDLERS = [_AssemblyScanHandler()]
