"""Astro: the files it loads by convention, rooted at the app's ``srcDir``.

``astro.config`` is read as text, never evaluated: ``srcDir`` and ``publicDir``
count when written as literals, and default to ``src`` and ``public``.
"""

from __future__ import annotations

import glob
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _inside, _js

if TYPE_CHECKING:
    from .npm import NpmManifest

ASTRO_CONFIGS = tuple(f"astro.config.{ext}" for ext in ("mjs", "js", "ts", "mts"))

_SRC_DIR_RE = re.compile(r"""\bsrcDir\s*:\s*["'`]([^"'`\n]+)["'`]""")
_PUBLIC_DIR_RE = re.compile(r"""\bpublicDir\s*:\s*["'`]([^"'`\n]+)["'`]""")
# ``/* ... */`` and ``// ...`` comments; a ``//`` after ``:``, a quote or a word
# character is part of a URL or string (``https://``, ``'//cdn'``), not a comment.
_COMMENT_RE = re.compile(r"""/\*.*?\*/|(?<![:"'`\w/])//[^\n]*""", re.DOTALL)


def _astro_globs(src: str) -> tuple[str, ...]:
    """The entry globs of an app whose source directory is *src* (escaped, no ``/``)."""
    return (
        f"{src}/pages/*",
        *_js(f"{src}/middleware", f"{src}/middleware/index", f"{src}/content/config"),
        *_js(f"{src}/content.config", f"{src}/actions/index"),
    )


ASTRO = FrameworkFacts(
    name="Astro",
    anchor="framework:astro",
    npm_packages=("astro",),
    config_files=ASTRO_CONFIGS,
    entry_globs=_astro_globs("src"),
)


def _literal_dir(pattern: re.Pattern[str], config: str, default: str) -> str:
    """The app-relative directory a literal option names in *config*, else *default*.

    A value that leaves the app (``../shared``) or names the app itself keeps
    the default: there is nothing of the app's own to root at. A commented-out
    option is not read.
    """
    match = pattern.search(_COMMENT_RE.sub("", config))
    if match is None:
        return default
    return _inside(match.group(1).strip().rstrip("/")) or default


def astro_src_dir(config: str) -> str:
    """The app-relative ``srcDir`` an ``astro.config`` text names, default ``src``."""
    return _literal_dir(_SRC_DIR_RE, config, "src")


def astro_public_dir(config: str) -> str:
    """The app-relative ``publicDir`` an ``astro.config`` text names, default ``public``."""
    return _literal_dir(_PUBLIC_DIR_RE, config, "public")


def astro_facts(manifest: NpmManifest) -> FrameworkFacts:
    """:data:`ASTRO` with its entry globs rooted at the app's ``srcDir``.

    The globs are relative to the manifest's directory, like every
    :func:`~.js.js_frameworks` answer.
    """
    src = astro_src_dir(ASTRO.config(manifest) or "")
    if src == "src":
        return ASTRO
    return replace(ASTRO, entry_globs=_astro_globs(glob.escape(src)))
