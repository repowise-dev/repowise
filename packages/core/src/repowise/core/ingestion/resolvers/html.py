"""``<script src>`` / ``<link href>`` resolution.

An HTML reference is a *path*, not a module specifier, so this deliberately
does not go through ``resolve_ts_js_import``: there is no extension inference,
no ``index.*`` lookup, no ``node_modules`` walk and no tsconfig alias. Writing
``src="./app"`` in HTML fetches a file literally named ``app``; only
``src="./app.js"`` loads the script. Borrowing the TS/JS resolver would invent
edges the browser would never follow.

Two forms reach here, and they anchor differently:

1. **Document-relative** — ``app.js``, ``./js/app.js``, ``../shared/x.css``.
   Resolved against the directory of the referencing page, like any relative
   path.
2. **Root-relative** — ``/src/main.tsx``, ``/static/js/app.js``. The leading
   ``/`` is the *web* root, which is almost never the repository root: Flask
   serves ``static/`` from ``app/static/``, Django collects from
   ``<app>/static/``, and Vite treats the directory holding ``index.html`` as
   the project root. Two anchors are tried, in order:

   a. The referencing page's own directory. This is the bundler convention and
      it is what makes the flagship edge work — ``ui/index.html`` writing
      ``/src/main.tsx`` means ``ui/src/main.tsx``. Only accepted when that file
      actually exists, so it cannot invent anything.
   b. Its ``public/`` subdirectory, which Vite, CRA and Vue CLI copy to the
      web root verbatim — ``ui/index.html`` writing ``/ico/favicon.png`` means
      ``ui/public/ico/favicon.png``.
   c. Failing both, a unique path suffix anywhere in the repo, for the
      server-rooted case (``templates/page.html`` referencing
      ``/static/app.css`` that lives in ``myapp/static/app.css``). Only when
      exactly one repo path ends with it — a tie yields no edge rather than a
      guessed one.

   The order matters in a monorepo: nine files end in ``/src/main.tsx`` in the
   validation corpus, so suffix matching alone correctly refuses to guess and
   the real edge is lost. Anchoring at the page's directory finds it.

External references (CDN URLs, ``data:``) never arrive: the extractor drops
them, since they name no file in the repository.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


def resolve_html_asset(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve one HTML asset reference to a repo-relative path, or None."""
    raw = module_path.strip()
    if not raw:
        return None

    # A query string or fragment is cache-busting or a sprite selector
    # ("app.js?v=3", "icons.svg#home") — not part of the filename.
    for sep in ("?", "#"):
        raw = raw.split(sep, 1)[0]
    raw = raw.strip()
    if not raw or raw.endswith("/"):
        return None

    importer_dir = posixpath.dirname(importer_path)

    if raw.startswith("/"):
        tail = raw.lstrip("/")
        # (a) the page's own directory as the web root — the bundler convention.
        anchored = posixpath.normpath(posixpath.join(importer_dir, tail))
        if anchored in ctx.path_set:
            return anchored
        # (b) its public/ directory, which Vite, CRA and Vue CLI all copy to
        #     the web root verbatim.
        public = posixpath.normpath(posixpath.join(importer_dir, "public", tail))
        if public in ctx.path_set:
            return public
        # (c) otherwise a unique suffix anywhere in the repo.
        return _unique_suffix_match(tail, ctx)

    resolved = posixpath.normpath(posixpath.join(importer_dir, raw))
    # A page at the repo root referencing "../vendor/x.js" escapes the repo.
    if resolved.startswith("..") or resolved in (".", "/"):
        return None
    if resolved in ctx.path_set:
        return resolved

    # A page is often served from a directory that is not where it lives in the
    # repo (templates/index.html serving /static/app.js as "static/app.js"), so
    # a bare relative miss gets the same unique-suffix treatment. Multi-segment
    # only: linking a lone "app.js" by filename would be a guess.
    if "/" in raw:
        return _unique_suffix_match(raw, ctx)
    return None


def _unique_suffix_match(tail: str, ctx: ResolverContext) -> str | None:
    """Return the single repo path ending in *tail*, else None.

    Same precision guard as the shell resolver: exactly one match links, a tie
    links nothing. A wrong asset edge is worse than a missing one for a graph
    that feeds docs and dead-code detection.
    """
    tail = posixpath.normpath(tail)
    if tail in ctx.path_set:
        return tail
    needle = f"/{tail}"
    hits = [p for p in ctx.sorted_paths if p.endswith(needle)]
    return hits[0] if len(hits) == 1 else None
