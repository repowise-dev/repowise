"""Collect implicit + global using namespaces per MSBuild project.

C# 10 introduced ``global using`` directives that apply to every file in
the compilation unit. The .NET 6 SDK auto-injects a set of common
namespaces when ``<ImplicitUsings>enable</ImplicitUsings>`` is set in the
.csproj. Both must be honoured for accurate import resolution.
"""

from __future__ import annotations

import re
from pathlib import Path

# Default ImplicitUsings set for SDK ``Microsoft.NET.Sdk`` projects.
# Source: dotnet/sdk repo, Sdks/Microsoft.NET.Sdk/targets/Microsoft.NET.Sdk.ImplicitNamespaceImports.props
_DEFAULT_IMPLICIT_USINGS: frozenset[str] = frozenset(
    {
        "System",
        "System.Collections.Generic",
        "System.IO",
        "System.Linq",
        "System.Net.Http",
        "System.Threading",
        "System.Threading.Tasks",
    }
)

# Web SDK adds these on top of the defaults.
_WEB_IMPLICIT_USINGS: frozenset[str] = frozenset(
    {
        "System.Net.Http.Json",
        "Microsoft.AspNetCore.Builder",
        "Microsoft.AspNetCore.Hosting",
        "Microsoft.AspNetCore.Http",
        "Microsoft.AspNetCore.Routing",
        "Microsoft.Extensions.Configuration",
        "Microsoft.Extensions.DependencyInjection",
        "Microsoft.Extensions.Hosting",
        "Microsoft.Extensions.Logging",
    }
)


_GLOBAL_USING_RE = re.compile(
    r"^\s*global\s+using(?:\s+static)?\s+(?:[A-Za-z_]\w*\s*=\s*)?([A-Za-z_][\w.]*)\s*;",
    re.MULTILINE,
)


def scan_global_usings(cs_text: str) -> list[str]:
    """Return every namespace target of a ``global using`` line in *cs_text*."""
    return [m.group(1) for m in _GLOBAL_USING_RE.finditer(cs_text)]


def collect_project_global_usings(
    project_dir: Path, implicit_usings: bool, sdk_is_web: bool = False
) -> set[str]:
    """Walk *project_dir* for global using sources and return the merged set.

    Sources merged:
    - Default ImplicitUsings set (when enabled)
    - Web SDK extras (when ``sdk_is_web`` is True)
    - Every ``global using`` directive found in any .cs file under the project
    - Project usings declared in the .csproj are added by the caller
    """
    result: set[str] = set()
    if implicit_usings:
        result.update(_DEFAULT_IMPLICIT_USINGS)
        if sdk_is_web:
            result.update(_WEB_IMPLICIT_USINGS)

    skip = {"bin", "obj", ".vs", "node_modules"}
    for cs_path in project_dir.rglob("*.cs"):
        if any(part in skip for part in cs_path.parts):
            continue
        try:
            text = cs_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        result.update(scan_global_usings(text))

    return result
