"""C# same-namespace + global-using implicit reference resolution.

Background
----------
C# needs no ``using`` directive for types declared in the same namespace —
``class RetryPolicy : PolicyBase`` compiles with zero usings when
``PolicyBase`` lives in a sibling file of the same namespace. On top of
that, C# 10 ``global using`` directives and MSBuild ``<Using Include>``
items make whole namespaces visible to every file of a project, so test
projects routinely contain *no* per-file usings at all. Both cases
produce **no edge at all** under plain import resolution, which makes the
most cohesive parts of a C# codebase (and entire xunit suites) read as
disconnected orphans.

This pass mirrors the JVM same-package prior art
(:mod:`.jvm_same_package`): a self-contained, regex-driven scan over raw
source text that emits conservative ``imports`` edges after the regular
import-resolution phases ran.

For each C# file A, every capitalized identifier in A's source is checked
against the types declared in A's *candidate namespaces*: first A's own
declared namespace(s) (closest scope wins, per C# lookup rules), then the
project's global-using namespaces that exist locally in the repo. An edge
A → B is emitted only when ALL of:

- the identifier names a type declared in exactly **one** file of the
  candidate namespace tier (ambiguous names — declared in two or more
  files — produce no edge to anyone; a wrong edge is worse than a
  missing one);
- B is not A itself, and the type is not also declared in A;
- the identifier is not a ubiquitous BCL type name (``String``,
  ``Task``, … — overwhelmingly stdlib references);
- the identifier is not the target of a ``using Alias = …`` directive
  in A (the alias shadows it);
- for the global-using tier only: the identifier is not declared in a
  namespace A explicitly ``using``s (the explicit using already resolved
  through the normal import path and wins);
- no edge A → B already exists (a real import wins).

C# members are PascalCase, so a method sharing a sibling type's name can
match — accepted: the ambiguity guard plus locally-declared-types-only
lookup keeps the false-positive surface small, and such name collisions
usually indicate real coupling (``Policy.Retry()`` ↔ ``Retry``).

Emitted edges are ``imports`` edges carrying
``hint_source="same_namespace"`` (own namespace) or
``hint_source="global_using"`` (project-wide usings) so density metrics
count them separately and any false positive is diagnosable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers.dotnet.index import DotNetProjectIndex

# Capitalized identifier — candidate type reference.
_TYPE_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")

# ``using Foo.Bar;`` / ``global using Foo.Bar;`` — namespace usings.
_USING_NS_RE = re.compile(
    r"^\s*(?:global\s+)?using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;", re.MULTILINE
)

# ``using Alias = Foo.Bar.Baz;`` — the alias name shadows bare identifiers.
_USING_ALIAS_RE = re.compile(
    r"^\s*(?:global\s+)?using\s+([A-Za-z_]\w*)\s*=", re.MULTILINE
)

# Ubiquitous BCL type names — references are overwhelmingly stdlib, so a
# local type sharing the name never gets a same-namespace hint edge.
_BCL_COMMON_TYPES = frozenset({
    "String", "Object", "Boolean", "Byte", "SByte", "Char", "Decimal",
    "Double", "Single", "Int16", "Int32", "Int64", "UInt16", "UInt32",
    "UInt64", "IntPtr", "UIntPtr", "Void", "Type", "Array", "Enum",
    "Delegate", "Attribute", "Exception", "ArgumentException",
    "ArgumentNullException", "ArgumentOutOfRangeException",
    "InvalidOperationException", "NotSupportedException",
    "NotImplementedException", "NullReferenceException",
    "OperationCanceledException", "TimeoutException", "AggregateException",
    "ObjectDisposedException", "FormatException", "OverflowException",
    "EventArgs", "EventHandler", "Action", "Func", "Predicate",
    "Comparison", "Converter", "Tuple", "ValueTuple", "Nullable", "Lazy",
    "WeakReference", "GC", "Console", "Convert", "Math", "Random",
    "DateTime", "DateTimeOffset", "TimeSpan", "DateOnly", "TimeOnly",
    "Guid", "Uri", "Version", "Environment", "Activator", "Buffer",
    "BitConverter", "StringBuilder", "StringComparer", "StringComparison",
    "Encoding", "Regex", "Match", "Group", "Capture",
    "List", "Dictionary", "HashSet", "SortedSet", "SortedList",
    "SortedDictionary", "Queue", "Stack", "LinkedList", "KeyValuePair",
    "IEnumerable", "IEnumerator", "ICollection", "IList", "IDictionary",
    "IReadOnlyList", "IReadOnlyCollection", "IReadOnlyDictionary",
    "ISet", "IQueryable", "IGrouping", "ILookup", "IComparer",
    "IEqualityComparer", "IComparable", "IEquatable", "IDisposable",
    "IAsyncDisposable", "IFormattable", "ICloneable", "IServiceProvider",
    "Task", "ValueTask", "CancellationToken", "CancellationTokenSource",
    "Thread", "ThreadPool", "Monitor", "Mutex", "SemaphoreSlim",
    "Interlocked", "Volatile", "SpinLock", "TaskCompletionSource",
    "ConcurrentDictionary", "ConcurrentQueue", "ConcurrentStack",
    "ConcurrentBag", "BlockingCollection",
    "Stream", "MemoryStream", "FileStream", "StreamReader", "StreamWriter",
    "TextReader", "TextWriter", "BinaryReader", "BinaryWriter", "File",
    "Directory", "Path", "FileInfo", "DirectoryInfo",
    "Span", "ReadOnlySpan", "Memory", "ReadOnlyMemory", "ArraySegment",
    "Range", "Index", "Enumerable", "Linq",
    "HttpClient", "HttpRequestMessage", "HttpResponseMessage",
    "HttpMethod", "HttpStatusCode", "HttpContent", "StringContent",
    "Debug", "Trace", "Debugger", "Stopwatch", "Process",
    "CultureInfo", "Comparer", "EqualityComparer", "Expression",
    "MethodInfo", "PropertyInfo", "FieldInfo", "ParameterInfo",
    "Assembly", "AssemblyName", "BindingFlags",
    "ObsoleteAttribute", "FlagsAttribute", "SerializableAttribute",
    "ThreadStaticAttribute", "CallerMemberNameAttribute", "Obsolete",
    "Flags", "Serializable", "CLSCompliant", "InternalsVisibleTo",
})

_SAME_NAMESPACE_HINT = "same_namespace"
_GLOBAL_USING_HINT = "global_using"


def build_namespace_type_index(
    cs_texts: dict[str, str],
) -> dict[str, dict[str, list[str]]]:
    """Build ``namespace → {type_name: [declaring rel paths]}`` from source.

    Reuses the dotnet resolver's declaration scanner so the type shapes
    (file-scoped namespaces, modifier soup, records) match the namespace
    map used by import resolution.
    """
    from ..resolvers.dotnet.namespace_map import scan_type_declarations

    ns_types: dict[str, dict[str, list[str]]] = {}
    for path in sorted(cs_texts):
        for decl in scan_type_declarations(cs_texts[path]):
            bucket = ns_types.setdefault(decl.namespace, {})
            files = bucket.setdefault(decl.name, [])
            if path not in files:
                files.append(path)
    return ns_types


def resolve_csharp_same_namespace_refs(
    graph: nx.DiGraph,
    dotnet_index: DotNetProjectIndex | None,
    cs_texts: dict[str, str],
    repo_path: Path | None,
) -> int:
    """Emit same-namespace / global-using ``imports`` edges for C# files.

    *cs_texts* maps repo-relative path → source text. Returns the number
    of edges added.
    """
    from ..resolvers.dotnet.namespace_map import declared_namespaces

    ns_types = build_namespace_type_index(cs_texts)

    count = 0
    for path in sorted(cs_texts):
        text = cs_texts[path]
        own_namespaces = list(dict.fromkeys(declared_namespaces(text)))
        explicit_ns = [m.group(1) for m in _USING_NS_RE.finditer(text)]
        alias_names = {m.group(1) for m in _USING_ALIAS_RE.finditer(text)}

        # Project-wide usings (``global using`` files + csproj <Using>
        # items + SDK implicit sets), restricted to namespaces that
        # actually exist locally.
        global_ns: list[str] = []
        if dotnet_index is not None and repo_path is not None:
            csproj = dotnet_index.file_to_project.get(
                (repo_path / path).resolve()
            )
            if csproj is not None:
                global_ns = sorted(
                    ns
                    for ns in dotnet_index.globals_for_project(csproj)
                    if ns in ns_types and ns not in own_namespaces
                )

        if not own_namespaces and not global_ns:
            continue

        # Types declared in namespaces this file explicitly ``using``s —
        # those resolve through the normal import path and shadow the
        # global-using tier.
        explicit_types: set[str] = set()
        for ns in explicit_ns:
            explicit_types.update(ns_types.get(ns, ()))

        # target file → (referenced names, hint source)
        found: dict[str, tuple[list[str], str]] = {}
        for ident in sorted(set(_TYPE_IDENT_RE.findall(text))):
            if ident in _BCL_COMMON_TYPES or ident in alias_names:
                continue
            target: str | None = None
            hint = _SAME_NAMESPACE_HINT
            # Tier 1: the file's own namespace(s) — closest scope wins.
            declaring: set[str] = set()
            for ns in own_namespaces:
                declaring.update(ns_types.get(ns, {}).get(ident, ()))
            if declaring:
                if len(declaring) != 1:
                    continue  # ambiguous — no edge to anyone
                target = next(iter(declaring))
            elif global_ns:
                if ident in explicit_types:
                    continue  # explicit using already resolved it
                hint = _GLOBAL_USING_HINT
                declaring = set()
                for ns in global_ns:
                    declaring.update(ns_types.get(ns, {}).get(ident, ()))
                if len(declaring) != 1:
                    continue
                target = next(iter(declaring))
            if target is None or target == path:
                continue
            names, _ = found.setdefault(target, ([], hint))
            names.append(ident)

        for target, (names, hint) in sorted(found.items()):
            if not graph.has_node(path) or not graph.has_node(target):
                continue
            if graph.has_edge(path, target):
                continue  # a real import (or stronger evidence) wins
            graph.add_edge(
                path,
                target,
                edge_type="imports",
                imported_names=names,
                hint_source=hint,
            )
            count += 1

    return count
