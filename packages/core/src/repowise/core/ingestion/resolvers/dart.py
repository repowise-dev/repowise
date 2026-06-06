"""Dart import resolution (lightweight regex tier).

Resolution order:

1. ``dart:*`` SDK URIs → dropped (no node).
2. ``package:<name>/<path>`` → pubspec.yaml index: every pubspec's
   ``name:`` maps the package name to its directory, so a self-import
   (``package:own_name/src/x.dart``) resolves to ``lib/<path>`` inside
   that package — monorepos with several pubspecs included. Other
   package URIs become labelled externals (``external:pub:<name>``).
3. Relative URIs (``import``/``export``/``part``) → importer-relative
   probe.
4. ``part of <library.name>`` → ``library`` declaration index; unresolved
   library names are dropped, not externalised (they are intra-package
   by construction).
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_module

if TYPE_CHECKING:
    from .context import ResolverContext

_PUBSPEC_NAME_RE = re.compile(r"^name:[ \t]*[\"']?([A-Za-z0-9_]+)", re.M)
_LIBRARY_DECL_RE = re.compile(r"^[ \t]*library[ \t]+([A-Za-z_][A-Za-z0-9_.]*)", re.M)


def _pubspec_map(ctx: ResolverContext) -> dict[str, str]:
    """``{package_name: package_root_dir}`` from every pubspec.yaml (cached)."""
    cached = getattr(ctx, "_dart_pubspec_map", None)
    if cached is not None:
        return cached
    result: dict[str, str] = {}
    repo = ctx.repo_path
    for path in ctx.sorted_paths:
        if not (path == "pubspec.yaml" or path.endswith("/pubspec.yaml")):
            continue
        if repo is None:
            continue
        try:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _PUBSPEC_NAME_RE.search(text)
        if m:
            root = path[: -len("pubspec.yaml")].rstrip("/")
            result.setdefault(m.group(1), root)
    ctx._dart_pubspec_map = result  # type: ignore[attr-defined]
    return result


def _library_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_dart_library_index",
        extensions=(".dart",),
        declaration_re=_LIBRARY_DECL_RE,
        path_to_module=None,
    )


def resolve_dart_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    if module_path.startswith("dart:"):
        return None

    if module_path.startswith("package:"):
        name, _, rest = module_path[len("package:") :].partition("/")
        root = _pubspec_map(ctx).get(name)
        if root is not None and rest:
            candidate = f"{root}/lib/{rest}" if root else f"lib/{rest}"
            if candidate in ctx.path_set:
                return candidate
        return f"external:pub:{name}"

    if module_path.startswith("library:"):
        return lookup_module(_library_index(ctx), module_path[len("library:") :])

    # Relative URI — import/export/part 'path.dart'
    importer_dir = posixpath.dirname(importer_path)
    candidate = posixpath.normpath(posixpath.join(importer_dir, module_path))
    if candidate in ctx.path_set:
        return candidate
    return f"external:{module_path}"
