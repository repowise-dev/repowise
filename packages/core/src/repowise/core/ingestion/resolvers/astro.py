"""Astro import resolution.

Any ``/``-rooted value, a frontmatter import or a markup ref (an ``is:inline``
script, an icon or stylesheet ``<link>``), is looked up under the nearest
``public/`` above the page, which Astro serves at the web root; an app whose
``astro.config`` names a literal ``publicDir`` is looked up there instead, so a
monorepo with several Astro apps roots each at its own. A ``/src/...`` value is then
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
from pathlib import Path
from typing import TYPE_CHECKING

from ..framework_facts import ASTRO_CONFIGS, astro_public_dir
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
    # Vite resolves and serves a ``/src/...`` path from the project root, so any such
    # value (an import, a ``<script src>``, a ``<link href>``) is tried there after ``public/``.
    # shortcut: a ``base``-prefixed URL (``/docs/favicon.svg``) misses; read
    # ``base`` from astro.config once a repo sets one.
    public_dirs = _public_dirs(ctx)
    for root in ("public", "") if tail.startswith("src/") else ("public",):
        directory = posixpath.dirname(importer_path)
        while True:
            served = public_dirs.get(directory, root) if root else root
            candidate = posixpath.normpath(posixpath.join(directory, served, tail))
            if candidate in ctx.path_set:
                return candidate
            if not directory:
                break
            directory = posixpath.dirname(directory)
    return None


def _public_dirs(ctx: ResolverContext) -> dict[str, str]:
    """Each Astro app directory whose ``astro.config`` names a ``publicDir`` -> that directory.

    Read once per context. An app with no config, or the default ``public``, is
    left out: the lookup already tries ``public/`` there.
    """
    cached = getattr(ctx, "_astro_public_dirs", None)
    if cached is not None:
        return cached
    configs: dict[str, str] = {}  # app directory -> its astro.config, first name wins
    for path in ctx.path_set:
        directory, _, name = path.rpartition("/")
        if name in ASTRO_CONFIGS:
            current = configs.get(directory)
            if current is None or ASTRO_CONFIGS.index(name) < ASTRO_CONFIGS.index(
                current.rpartition("/")[2]
            ):
                configs[directory] = path
    public_dirs: dict[str, str] = {}
    for directory, path in configs.items():
        public = astro_public_dir(_read_text(ctx, path) or "")
        if public != "public":
            public_dirs[directory] = public
    ctx._astro_public_dirs = public_dirs  # type: ignore[attr-defined]
    return public_dirs


def _read_text(ctx: ResolverContext, rel: str) -> str | None:
    data = (ctx.source_map or {}).get(rel)
    if data is not None:
        return data.decode("utf-8", errors="ignore")
    if ctx.repo_path is None:
        return None
    try:
        return (Path(ctx.repo_path) / rel).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
