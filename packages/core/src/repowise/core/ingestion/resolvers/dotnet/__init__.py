"""MSBuild / .sln aware resolution helpers for C# / VB.NET / .NET.

The ``DotNetProjectIndex`` is the entry point — it walks a repo once,
parses every ``.csproj``/``.vbproj`` and ``.sln``, builds a namespace →
file map, collects implicit/global usings, and exposes lookup helpers
used by ``resolvers/csharp.py`` and ``resolvers/vbnet.py``.

Construction is lazy and idempotent: the index is built on first access
and cached on the ``ResolverContext`` for the lifetime of one
``GraphBuilder.build()`` invocation.
"""

from __future__ import annotations

from .import_resolution import resolve_dotnet_import
from .index import DotNetProjectIndex, build_index, get_or_build_index
from .msbuild import MSBuildProject, find_vbproj_files, parse_csproj, parse_vbproj
from .namespace_map import build_namespace_map
from .solution import SolutionEntry, parse_sln

__all__ = [
    "DotNetProjectIndex",
    "MSBuildProject",
    "SolutionEntry",
    "build_index",
    "build_namespace_map",
    "find_vbproj_files",
    "get_or_build_index",
    "parse_csproj",
    "parse_sln",
    "parse_vbproj",
    "resolve_dotnet_import",
]
