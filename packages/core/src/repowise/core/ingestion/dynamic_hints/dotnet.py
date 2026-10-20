"""Dynamic-hint extractor for .NET / C# patterns that escape static analysis.

The .NET ecosystem leans heavily on conventions that the AST never sees:

- ``services.AddScoped<IFoo, Foo>()`` — DI container registration that
  wires consumers (which depend on ``IFoo``) to producers (the ``Foo``
  implementation type) only at runtime.
- ``Activator.CreateInstance(typeof(T))`` / ``Type.GetType("X.Y.Z")`` —
  reflection-driven type loading.
- ``[assembly: InternalsVisibleTo("Other.Tests")]`` — cross-project
  visibility that pretends the friend assembly imports everything
  without writing any using directive.

These patterns produce ``DynamicEdge`` rows so the dead-code analyser
won't flag DI-registered types and the graph reflects the de facto
dependency surface.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..type_names import bare_type_name
from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"bin", "obj", ".vs", "node_modules", ".git", "packages"}

# services.AddScoped<IFoo, Foo>() / AddSingleton<...> / AddTransient<...>
# Also matches the framework-specific registration helpers that wire up
# concrete types at runtime: EF Core DbContexts, gRPC services, SignalR
# hubs, typed HttpClients, options, and middleware. Each of those
# patterns hands a closed generic type to the runtime which then loads
# the named class — without recording the edge here, the dead-code
# analyser sees the registered type as having no importers.
_DI_GENERIC_RE = re.compile(
    r"\.\s*(?:Add|Map|Use)"
    r"(?:Scoped|Singleton|Transient|HostedService"
    r"|DbContext(?:Pool|Factory)?"
    r"|HttpClient|Options"
    r"|GrpcService|GrpcClient|Hub|SignalR"
    r"|Controllers?"
    r"|Middleware)"
    r"\s*<\s*([\w.]+)\s*(?:,\s*([\w.]+)\s*)?>"
)

# Configure<TOptions>(...) — options binding, very common in ASP.NET
# Core. Same shape as DI registration: argument type is the consumer.
_CONFIGURE_RE = re.compile(r"\.\s*Configure\s*<\s*([\w.]+)\s*>")

# eventBus.Subscribe<TIntegrationEvent, THandler>() and the matching
# UnsubscribeDynamic / SubscribeDynamic forms used by integration
# event buses (RabbitMQ, EventBus). Drives consumer wiring across
# microservices that the static graph never sees.
_EVENT_BUS_SUBSCRIBE_RE = re.compile(
    r"\.\s*(?:Un)?Subscribe(?:Dynamic)?\s*<\s*([\w.]+)\s*(?:,\s*([\w.]+)\s*)?>"
)

# Activator.CreateInstance(typeof(Foo)) / Activator.CreateInstance("Acme.Foo")
_ACTIVATOR_TYPEOF_RE = re.compile(r"Activator\.CreateInstance\s*\(\s*typeof\s*\(\s*([\w.]+)\s*\)")
_ACTIVATOR_STRING_RE = re.compile(r"Activator\.CreateInstance\s*\(\s*[\"']([\w.]+)[\"']")

# Type.GetType("Acme.Foo")
_TYPE_GETTYPE_RE = re.compile(r"Type\.GetType\s*\(\s*[\"']([\w.]+)[\"']")

# [assembly: InternalsVisibleTo("Other.Tests")]
_INTERNALS_VISIBLE_RE = re.compile(
    r"\[\s*assembly\s*:\s*InternalsVisibleTo\s*\(\s*[\"']([^\"']+)[\"']"
)

# ``nameof(TypeName)`` — used heavily for DI key strings (e.g.
# ``services.Configure<T>(nameof(T))``), options binding, and route /
# policy names that never appear as a `using` import. We only match
# arguments that look like a *type* (PascalCase identifier) so we
# don't bind to property / method names — those produce noise and
# resolve to internal members that the analyser already credits via
# the parent class file. The dotted form (``nameof(NS.Type)``) also
# resolves: ``_files_for`` strips the namespace.
_NAMEOF_TYPE_RE = re.compile(r"\bnameof\s*\(\s*([A-Z][\w.]*)\s*\)")

# ``typeof(TypeName)`` — used heavily in ``[JsonConverter(typeof(X))]``,
# ``[TypeConverter(typeof(X))]``, ``DataTemplate.DataType = typeof(X)``,
# ``services.AddSingleton(typeof(IFoo), typeof(FooImpl))``, route
# constraints, etc. Same shape and PascalCase guard as ``nameof``.
_TYPEOF_TYPE_RE = re.compile(r"\btypeof\s*\(\s*([A-Z][\w.]*)\s*\)")


# Single-type patterns that each mean "this file loads that type", in the
# order their edges are emitted after the DI and event-bus passes.
_REFLECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_ACTIVATOR_TYPEOF_RE, "activator"),
    (_ACTIVATOR_STRING_RE, "activator_string"),
    (_TYPE_GETTYPE_RE, "type_gettype"),
    # DI keys, options, policies.
    (_NAMEOF_TYPE_RE, "nameof"),
    # JsonConverter/TypeConverter attrs, DataTemplate.DataType, manual DI.
    (_TYPEOF_TYPE_RE, "typeof"),
)

_TYPE_DECLARATION_RE = re.compile(
    r"\b(?:class|interface|struct|record(?:\s+(?:class|struct))?|enum)\s+([A-Z]\w*)"
)

FilesFor = Callable[[str], list[str]]


class DotNetDynamicHints(DynamicHintExtractor):
    """Discover DI registrations, reflection, and assembly-level hints in .NET."""

    name = "dotnet"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        type_to_files, cs_files = self._index_types(repo_root)

        def _files_for(name: str) -> list[str]:
            return type_to_files.get(bare_type_name(name), [])

        edges: list[DynamicEdge] = []
        repo_root_resolved = repo_root.resolve()
        for cs, text in cs_files:
            try:
                rel = cs.resolve().relative_to(repo_root_resolved).as_posix()
            except ValueError:
                continue
            edges.extend(self._di_edges(rel, text, _files_for))
            edges.extend(self._pattern_edges(rel, text, _files_for, ((_CONFIGURE_RE, "configure_options"),)))
            edges.extend(self._subscribe_edges(rel, text, _files_for))
            edges.extend(self._pattern_edges(rel, text, _files_for, _REFLECTION_PATTERNS))
            edges.extend(self._internals_visible_edges(rel, text))
        return edges

    def _index_types(
        self, repo_root: Path
    ) -> tuple[dict[str, list[str]], list[tuple[Path, str]]]:
        """``(type name -> declaring files, [(path, text)])`` over the repo's C# files.

        A short type name can map to several files when projects reuse names
        across namespaces. Edges go to *every* candidate: pruning false
        positives is the dead-code analyser's job, while a missing edge flags a
        real service dead.
        """
        type_to_files: dict[str, list[str]] = {}
        cs_files: list[tuple[Path, str]] = []  # (path, text)
        repo_root_resolved = repo_root.resolve()
        for cs in self._rglob(repo_root, "*.cs"):
            try:
                rel_path = cs.resolve().relative_to(repo_root_resolved)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel_path.parts):
                continue
            try:
                text = cs.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            rel = rel_path.as_posix()
            cs_files.append((cs, text))
            for match in _TYPE_DECLARATION_RE.finditer(text):
                bucket = type_to_files.setdefault(match.group(1), [])
                if rel not in bucket:
                    bucket.append(rel)
        return type_to_files, cs_files

    def _edge(self, source: str, target: str, hint: str) -> DynamicEdge:
        return DynamicEdge(
            source=source,
            target=target,
            edge_type="dynamic_uses",
            hint_source=f"{self.name}:{hint}",
        )

    def _edges_to(self, rel: str, targets: list[str], hint: str) -> list[DynamicEdge]:
        """Edges from *rel* to each target other than itself."""
        return [self._edge(rel, target, hint) for target in targets if target != rel]

    def _pairwise_edges(
        self, sources: list[str], targets: list[str], hint: str
    ) -> list[DynamicEdge]:
        return [
            self._edge(source, target, hint)
            for source in sources
            for target in targets
            if source != target
        ]

    def _pattern_edges(
        self,
        rel: str,
        text: str,
        files_for: FilesFor,
        patterns: tuple[tuple[re.Pattern[str], str], ...],
    ) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        for regex, hint in patterns:
            for match in regex.finditer(text):
                edges.extend(self._edges_to(rel, files_for(match.group(1)), hint))
        return edges

    def _di_edges(self, rel: str, text: str, files_for: FilesFor) -> list[DynamicEdge]:
        """``AddScoped<IFoo, Foo>()``: site to implementation, and interface to implementation."""
        edges: list[DynamicEdge] = []
        for match in _DI_GENERIC_RE.finditer(text):
            first = match.group(1)
            second = match.group(2) if match.group(2) else None
            # With two type args the site reaches the impl, with one the type.
            edges.extend(self._edges_to(rel, files_for(second or first), "di_register"))
            # The interface file is recorded as having a real implementation.
            if second is not None:
                edges.extend(
                    self._pairwise_edges(
                        files_for(first), files_for(second), "di_interface_to_impl"
                    )
                )
        return edges

    def _subscribe_edges(self, rel: str, text: str, files_for: FilesFor) -> list[DynamicEdge]:
        """``eventBus.Subscribe<TEvent, THandler>()``: site to both, event to handler."""
        edges: list[DynamicEdge] = []
        for match in _EVENT_BUS_SUBSCRIBE_RE.finditer(text):
            event_targets = files_for(match.group(1))
            handler_targets = files_for(match.group(2)) if match.group(2) else []
            edges.extend(self._edges_to(rel, event_targets, "subscribe_event"))
            edges.extend(self._edges_to(rel, handler_targets, "subscribe_handler"))
            edges.extend(self._pairwise_edges(event_targets, handler_targets, "event_to_handler"))
        return edges

    def _internals_visible_edges(self, rel: str, text: str) -> list[DynamicEdge]:
        """``[assembly: InternalsVisibleTo("X")]`` as an edge to a synthetic friend.

        The friend is not resolved to a project; its presence is the signal.
        """
        return [
            self._edge(rel, f"external:friend:{match.group(1)}", "internals_visible_to")
            for match in _INTERNALS_VISIBLE_RE.finditer(text)
        ]
