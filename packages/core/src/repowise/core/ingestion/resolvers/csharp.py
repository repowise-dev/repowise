"""C# import resolution — thin wrapper over the shared C#/VB.NET core.

See ``dotnet/import_resolution.py`` for the resolution algorithm.
"""

from __future__ import annotations

from .context import ResolverContext
from .dotnet.import_resolution import resolve_dotnet_import


def resolve_csharp_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a C# using directive to a repo-relative file path or external key."""
    return resolve_dotnet_import(module_path, importer_path, ctx, ext=".cs")
