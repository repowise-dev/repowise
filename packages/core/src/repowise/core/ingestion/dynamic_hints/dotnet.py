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
from pathlib import Path

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
# VB generic-argument syntax is `(Of T[, T2])` rather than `<T[, T2]>` —
# kept as a separate pattern (not merged into one alternation) so neither
# stays legible; call sites chain both iterators together.
_DI_GENERIC_RE_VB = re.compile(
    r"\.\s*(?:Add|Map|Use)"
    r"(?:Scoped|Singleton|Transient|HostedService"
    r"|DbContext(?:Pool|Factory)?"
    r"|HttpClient|Options"
    r"|GrpcService|GrpcClient|Hub|SignalR"
    r"|Controllers?"
    r"|Middleware)"
    r"\s*\(\s*Of\s+([\w.]+)\s*(?:,\s*([\w.]+)\s*)?\)",
    re.IGNORECASE,
)

# Configure<TOptions>(...) — options binding, very common in ASP.NET
# Core. Same shape as DI registration: argument type is the consumer.
_CONFIGURE_RE = re.compile(r"\.\s*Configure\s*<\s*([\w.]+)\s*>")
_CONFIGURE_RE_VB = re.compile(r"\.\s*Configure\s*\(\s*Of\s+([\w.]+)\s*\)", re.IGNORECASE)

# eventBus.Subscribe<TIntegrationEvent, THandler>() and the matching
# UnsubscribeDynamic / SubscribeDynamic forms used by integration
# event buses (RabbitMQ, EventBus). Drives consumer wiring across
# microservices that the static graph never sees.
_EVENT_BUS_SUBSCRIBE_RE = re.compile(
    r"\.\s*(?:Un)?Subscribe(?:Dynamic)?\s*<\s*([\w.]+)\s*(?:,\s*([\w.]+)\s*)?>"
)
_EVENT_BUS_SUBSCRIBE_RE_VB = re.compile(
    r"\.\s*(?:Un)?Subscribe(?:Dynamic)?\s*\(\s*Of\s+([\w.]+)\s*(?:,\s*([\w.]+)\s*)?\)",
    re.IGNORECASE,
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

# C#'s class/interface/struct/record/enum keywords vs. VB's
# Class/Interface/Structure/Module/Enum — used to index type declarations
# so the hint patterns above can resolve a target type to its file
# regardless of which of the two languages declares it.
_CS_TYPE_DECL_RE = re.compile(
    r"\b(?:class|interface|struct|record(?:\s+(?:class|struct))?|enum)\s+([A-Z]\w*)"
)
_VB_TYPE_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:Public|Private|Protected|Friend|MustInherit|NotInheritable|Partial)\s+)*"
    r"(?:Class|Interface|Structure|Enum|Module)\s+([A-Za-z_]\w*)",
    re.MULTILINE | re.IGNORECASE,
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


class DotNetDynamicHints(DynamicHintExtractor):
    """Discover DI registrations, reflection, and assembly-level hints in .NET."""

    name = "dotnet"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        # Build a class-name → list-of-files index in one pass so the
        # regex hits below can resolve target file paths cheaply. A
        # short type name can map to multiple files when projects
        # legitimately reuse names across namespaces (e.g. eShop has
        # ``Basket.API/Grpc/BasketService.cs`` and
        # ``WebApp/Services/BasketService.cs``). Collisions are common
        # in microservice repos, so we emit dynamic edges to *every*
        # candidate rather than picking the first match — pruning false
        # positives is the dead-code analyser's job, but missing edges
        # cause real services to be flagged dead with high confidence.
        type_to_files: dict[str, list[str]] = {}
        cs_files: list[tuple[Path, str]] = []  # (path, text) — C# and VB.NET both
        repo_root_resolved = repo_root.resolve()
        for src_file in (*self._rglob(repo_root, "*.cs"), *self._rglob(repo_root, "*.vb")):
            try:
                rel_path = src_file.resolve().relative_to(repo_root_resolved)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel_path.parts):
                continue
            try:
                text = src_file.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            rel = rel_path.as_posix()
            cs_files.append((src_file, text))
            type_decl_re = (
                _VB_TYPE_DECL_RE if src_file.suffix.lower() == ".vb" else _CS_TYPE_DECL_RE
            )
            for match in type_decl_re.finditer(text):
                name = match.group(1)
                bucket = type_to_files.setdefault(name, [])
                if rel not in bucket:
                    bucket.append(rel)

        def _short(name: str) -> str:
            return name.rsplit(".", 1)[-1]

        def _files_for(name: str) -> list[str]:
            return type_to_files.get(_short(name), [])

        for cs, text in cs_files:
            try:
                rel = cs.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            # ---- DI: AddScoped<IFoo, Foo>() / AddScoped(Of IFoo, Foo)() ----
            for match in (*_DI_GENERIC_RE.finditer(text), *_DI_GENERIC_RE_VB.finditer(text)):
                first = match.group(1)
                second = match.group(2) if match.group(2) else None
                # When two type args are present, edge: registration site → impl
                # When one type arg is present, edge: registration site → that type
                target_name = second or first
                for target in _files_for(target_name):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:di_register",
                            )
                        )
                # Also link interface → impl when both are present so the
                # interface file is recorded as having a real implementation
                # (helps dead-code analysis treat unused interfaces correctly).
                if second is not None:
                    iface_targets = _files_for(first)
                    impl_targets = _files_for(second)
                    for iface_target in iface_targets:
                        for impl_target in impl_targets:
                            if iface_target != impl_target:
                                edges.append(
                                    DynamicEdge(
                                        source=iface_target,
                                        target=impl_target,
                                        edge_type="dynamic_uses",
                                        hint_source=f"{self.name}:di_interface_to_impl",
                                    )
                                )

            # ---- Configure<TOptions>(section) / Configure(Of TOptions)(section) ----
            for match in (*_CONFIGURE_RE.finditer(text), *_CONFIGURE_RE_VB.finditer(text)):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:configure_options",
                            )
                        )

            # ---- eventBus.Subscribe<TEvent, THandler>() / Subscribe(Of TEvent, THandler)() ----
            for match in (
                *_EVENT_BUS_SUBSCRIBE_RE.finditer(text),
                *_EVENT_BUS_SUBSCRIBE_RE_VB.finditer(text),
            ):
                event_targets = _files_for(match.group(1))
                handler_targets = _files_for(match.group(2)) if match.group(2) else []
                for tgt in event_targets:
                    if tgt != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=tgt,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:subscribe_event",
                            )
                        )
                for tgt in handler_targets:
                    if tgt != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=tgt,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:subscribe_handler",
                            )
                        )
                # Link each event type to its handler so dead-code
                # analysis sees handler classes as reached.
                for evt in event_targets:
                    for hdl in handler_targets:
                        if evt != hdl:
                            edges.append(
                                DynamicEdge(
                                    source=evt,
                                    target=hdl,
                                    edge_type="dynamic_uses",
                                    hint_source=f"{self.name}:event_to_handler",
                                )
                            )

            # ---- Reflection: Activator.CreateInstance(typeof(...)) ----
            for match in _ACTIVATOR_TYPEOF_RE.finditer(text):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:activator",
                            )
                        )

            # ---- Reflection: Activator.CreateInstance("Acme.Foo") ----
            for match in _ACTIVATOR_STRING_RE.finditer(text):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:activator_string",
                            )
                        )

            # ---- Reflection: Type.GetType("Acme.Foo") ----
            for match in _TYPE_GETTYPE_RE.finditer(text):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:type_gettype",
                            )
                        )

            # ---- nameof(TypeName) — DI keys, options, policies ----
            for match in _NAMEOF_TYPE_RE.finditer(text):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:nameof",
                            )
                        )

            # ---- typeof(TypeName) — JsonConverter/TypeConverter attrs,
            # DataTemplate.DataType, manual DI registration, etc. ----
            for match in _TYPEOF_TYPE_RE.finditer(text):
                for target in _files_for(match.group(1)):
                    if target != rel:
                        edges.append(
                            DynamicEdge(
                                source=rel,
                                target=target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:typeof",
                            )
                        )

            # ---- [assembly: InternalsVisibleTo("Other.Tests")] ----
            for match in _INTERNALS_VISIBLE_RE.finditer(text):
                friend = match.group(1)
                # Map by best-effort: AssemblyName usually equals the project's
                # csproj filename. We can't always resolve precisely without the
                # DotNetProjectIndex, so we record the friend as a synthetic
                # external target. The dead-code analyser uses InternalsVisibleTo
                # presence as a strong "type may be used" signal regardless of
                # whether we can resolve it.
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=f"external:friend:{friend}",
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:internals_visible_to",
                    )
                )

        return edges
