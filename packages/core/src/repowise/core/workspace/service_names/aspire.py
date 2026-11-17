""".NET Aspire AppHost: ``AddProject`` names the service every reference resolves by.

``builder.AddProject("catalog-api", "../Catalog.API/Catalog.API.csproj")`` (and
eShop's ``AddDotnetProject`` helper of the same shape) places ``catalog-api`` in
the repo at that path. ``AddProject<Projects.Catalog_API>("catalog-api")``
names a generated type instead; it is resolved through the AppHost project's
own ``<ProjectReference>`` entries, whose file names generate those types
(``.`` and ``-`` become ``_``). ``WithReference`` only hands that name to
another project, so it adds no name of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .base import Claim, ServiceNameSource, SourceFile, join_config_path

_ADD_PROJECT_RE = re.compile(
    r"\.Add(?:Dotnet)?Project\s*(?:<\s*(?:global::)?([\w.]+)\s*>)?\s*\(\s*"
    r"\"([^\"]+)\"(?:\s*,\s*@?\"([^\"]+)\")?"
)
_PROJECT_REF_RE = re.compile(r"<ProjectReference\s+Include\s*=\s*\"([^\"]+\.csproj)\"", re.IGNORECASE)


def _project_dir(apphost_dir: Path, ref: str) -> Path:
    path = join_config_path(apphost_dir, ref)
    return path.parent if path.suffix.lower() == ".csproj" else path


def _generated_types(apphost_dir: Path) -> dict[str, Path]:
    """``Projects.<Name>`` type -> project directory, from the AppHost's project references."""
    types: dict[str, Path] = {}
    for csproj in sorted(apphost_dir.glob("*.csproj")):
        try:
            text = csproj.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for ref in _PROJECT_REF_RE.findall(text):
            path = _project_dir(apphost_dir, ref)
            stem = Path(ref.replace("\\", "/")).stem
            types[re.sub(r"[.\-]", "_", stem).lower()] = path
    return types


def read(file: SourceFile) -> Iterable[Claim]:
    apphost_dir = file.path.parent
    types: dict[str, Path] | None = None
    for type_name, name, path in _ADD_PROJECT_RE.findall(file.text):
        # With a generated type, a second string is a launch profile ("https"), not a path.
        if path and (not type_name or path.lower().endswith(".csproj") or "/" in path.replace("\\", "/")):
            yield Claim(name=name.lower(), at=_project_dir(apphost_dir, path))
        elif type_name:
            if types is None:
                types = _generated_types(apphost_dir)
            at = types.get(type_name.rsplit(".", 1)[-1].lower())
            if at is not None:
                yield Claim(name=name.lower(), at=at)


SOURCE = ServiceNameSource(
    name="aspire_apphost",
    # The two entry-point names Aspire templates use; one per project, so cheap to read.
    filenames=("AppHost.cs", "Program.cs"),
    marker="DistributedApplication.CreateBuilder",
    read=read,
)
