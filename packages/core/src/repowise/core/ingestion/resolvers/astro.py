"""Astro import resolution.

Any ``/``-rooted value, a frontmatter import or a markup ref (an ``is:inline``
script, an icon or stylesheet ``<link>``), is looked up under the nearest
``public/`` above the page, which Astro serves at the web root, when no Astro manifest is known. With a manifest, use its configured directories. A source-directory URL (``/src/...`` by default) is then
tried under each folder above the page, as Vite serves it from the project
root. Any ``/``-rooted miss is then tried through a tsconfig ``paths`` alias
(``"/@/*"``). A final miss is no node at all: ``/favicon.svg`` names no parsed
file, not a package. Everything else goes to the TS/JS resolver: frontmatter
and ``<script>`` imports are ordinary ESM, as is a markup ``<script src>`` with
no other attribute, which Astro compiles to ``import "<src>"``, so an alias or a
package resolves. Any other markup value is a URL, so a bare one
(``favicon.ico``) arrives page-relative as ``./favicon.ico``. A ``./`` value the
TS/JS resolver misses is then tried as ``/``-rooted, under the nearest
``public/``, since a layout or top-level page sits at the web root.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from ..framework_facts.astro import ASTRO, astro_directory
from ..framework_facts.npm import npm_manifests
from .typescript import resolve_ts_js_import

if TYPE_CHECKING:
    from .context import ResolverContext


def resolve_astro_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve an Astro import or markup ref to a repo-relative path, or None."""
    # shortcut: a relative href that misses takes the TS ``.x`` -> ``.ts``
    # fallback, so ``./site.css`` could bind a sibling ``site.ts``, an
    # extensionless one takes its extension guessing, so a prefetch ``docs``
    # binds a sibling page ``docs.astro`` (navigation, not a dependency), and a
    # missed ``/``-rooted URL takes a tsconfig alias (a catch-all ``*`` maps it
    # into ``src/``); route markup refs separately once a repo shows that.
    if not module_path.startswith("/"):
        hit = resolve_ts_js_import(module_path, importer_path, ctx)
        # shortcut: a page-relative miss is retried as ``/``-rooted, which is right
        # for a layout or top-level page but can bind a ``public/`` file a page in
        # a subfolder never fetches; join the route path once pages are mapped to URLs.
        if hit is None and module_path.startswith("./"):
            hit = _served_file(module_path[1:], importer_path, ctx)
        return hit
    hit = _served_file(module_path, importer_path, ctx)
    # A ``/``-rooted tsconfig alias (``"/@/*"``) resolves as in a ``.ts`` file.
    if hit is None and ctx.tsconfig_resolver is not None:
        importer_abs = str(ctx.repo_path / importer_path) if ctx.repo_path else importer_path
        hit = ctx.tsconfig_resolver.resolve(module_path, importer_abs)
    return hit


def _served_file(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """The file a ``/``-rooted URL names: under the nearest ``public/``, or a ``/src/`` path."""
    # A query string or fragment is cache-busting, not part of the filename.
    tail = module_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if ctx.repo_path is not None:
        manifests = getattr(ctx, "_astro_manifests", None)
        if manifests is None:
            manifests = sorted(
                npm_manifests(ctx.repo_path, ctx.path_set),
                key=lambda manifest: len(manifest.rel_dir),
                reverse=True,
            )
            ctx._astro_manifests = manifests
        for manifest in manifests:
            root = manifest.rel_dir
            if root and not importer_path.startswith(root + "/"):
                continue
            if not ASTRO.matches_npm(manifest):
                continue
            public = astro_directory(manifest, "publicDir", "public")
            src = astro_directory(manifest, "srcDir", "src")
            candidate = posixpath.normpath(posixpath.join(root, public, tail))
            if candidate in ctx.path_set:
                return candidate
            if src == "." or tail.startswith(("src/", src + "/")):
                candidate = posixpath.normpath(posixpath.join(root, tail))
                if candidate in ctx.path_set:
                    return candidate
            return None
    # Vite resolves and serves a ``/src/...`` path from the project root, so any such
    # value (an import, a ``<script src>``, a ``<link href>``) is tried there after ``public/``.
    # Without a known Astro manifest, retain the ancestor-based fallback.
    for root in ("public", "") if tail.startswith("src/") else ("public",):
        directory = posixpath.dirname(importer_path)
        while True:
            candidate = posixpath.normpath(posixpath.join(directory, root, tail))
            if candidate in ctx.path_set:
                return candidate
            if not directory:
                break
            directory = posixpath.dirname(directory)
    return None
