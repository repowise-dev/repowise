"""MSBuild / .sln aware resolution helpers for C# / .NET.

The ``DotNetProjectIndex`` is the entry point — it walks a repo once,
parses every ``.csproj`` and ``.sln``, builds a namespace → file map,
collects implicit/global usings, and exposes lookup helpers used by
``resolvers/csharp.py``.

Construction is lazy and idempotent: the index is built on first access
and cached on the ``ResolverContext`` for the lifetime of one
``GraphBuilder.build()`` invocation.
"""

from __future__ import annotations

from .index import DotNetProjectIndex, get_or_build_index
from .msbuild import MSBuildProject, parse_csproj
from .namespace_map import build_namespace_map
from .solution import SolutionEntry, parse_sln

__all__ = [
    "DotNetProjectIndex",
    "MSBuildProject",
    "SolutionEntry",
    "build_namespace_map",
    "get_or_build_index",
    "parse_csproj",
    "parse_sln",
]
