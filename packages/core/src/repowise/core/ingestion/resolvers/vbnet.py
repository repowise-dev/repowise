"""VB.NET import resolution — thin wrapper over the shared C#/VB.NET core.

See ``dotnet/import_resolution.py`` for the resolution algorithm. VB's
``Imports`` statement resolves against the exact same
``DotNetProjectIndex`` namespace map C# uses (see
docs/architecture/vbnet-support.md D4) — only the legacy stem-match
fallback's file extension differs.
"""

from __future__ import annotations

from .context import ResolverContext
from .dotnet.import_resolution import resolve_dotnet_import


def resolve_vbnet_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a VB.NET Imports statement to a repo-relative file path or external key."""
    return resolve_dotnet_import(module_path, importer_path, ctx, ext=".vb")
