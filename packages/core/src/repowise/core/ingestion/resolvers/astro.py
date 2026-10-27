"""Astro import resolution.

Any ``/``-rooted value, a frontmatter import or a markup ref (an ``is:inline``
script, an icon or stylesheet ``<link>``), is looked up under the nearest
``public/`` above the page, which Astro serves at the web root, so a monorepo
with several Astro apps needs no config lookup. A ``/src/...`` value is then
tried under each folder above the page, as Vite serves it from the project
root. Any ``/``-rooted miss is then tried through a tsconfig ``paths`` alias
(``"/@/*"``). A final miss is no node at all: ``/favicon.svg`` names no parsed
file, not a package. Everything else goes to the TS/JS resolver: frontmatter
and ``<script>`` imports are ordinary ESM, as is a markup ``<script src>`` with
no other attribute, which Astro compiles to ``import "<src>"``, so an alias or a
package resolves. Any other markup value is a URL, so a bare one
(``favicon.ico``) arrives page-relative as ``./favicon.ico``.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from .typescript import resolve_ts_js_import

if TYPE_CHECKING:
    from .context import ResolverContext


def resolve_astro_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve an Astro import or markup ref to a repo-relative path, or None."""
    # shortcut: a relative href that misses takes the TS ``.x`` -> ``.ts``
    # fallback, so ``./site.css`` could bind a sibling ``site.ts``, and a missed
    # ``/``-rooted URL takes a tsconfig alias (a catch-all ``*`` maps it into
    # ``src/``); route markup refs separately once a repo shows that.
    if not module_path.startswith("/"):
        return resolve_ts_js_import(module_path, importer_path, ctx)
    # A query string or fragment is cache-busting, not part of the filename.
    tail = module_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    # Vite resolves and serves a ``/src/...`` path from the project root, so any such
    # value (an import, a ``<script src>``, a ``<link href>``) is tried there after ``public/``.
    # shortcut: a ``base``-prefixed URL (``/docs/favicon.svg``) or a custom
    # ``publicDir`` or ``srcDir`` misses; read astro.config once a repo sets one.
    for root in ("public", "") if tail.startswith("src/") else ("public",):
        directory = posixpath.dirname(importer_path)
        while True:
            candidate = posixpath.normpath(posixpath.join(directory, root, tail))
            if candidate in ctx.path_set:
                return candidate
            if not directory:
                break
            directory = posixpath.dirname(directory)
    # A ``/``-rooted tsconfig alias (``"/@/*"``) resolves as in a ``.ts`` file.
    if ctx.tsconfig_resolver is not None:
        importer_abs = str(ctx.repo_path / importer_path) if ctx.repo_path else importer_path
        return ctx.tsconfig_resolver.resolve(module_path, importer_abs)
    return None
