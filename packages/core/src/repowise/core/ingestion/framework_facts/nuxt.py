"""Nuxt: its source directory, the files it loads, and the ``@nuxtjs/i18n`` locale files.

Every reader asks this module where a Nuxt app keeps its code: the
convention anchors (:func:`nuxt_facts`) and the auto-import resolver
(``resolvers/nuxt.py``) both root at :func:`nuxt_src_dir`.

``nuxt.config`` is read as text, never evaluated: ``srcDir`` and ``langDir``
count when written as literals. Without a literal ``srcDir``, the repository's
files decide between Nuxt 3's root and Nuxt 4's ``app/``; with no file list to
ask, both are anchored, since an extra anchor only spares a file that is not
there.
"""

from __future__ import annotations

import glob
import posixpath
import re
from collections.abc import Collection
from dataclasses import replace
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _inside, _js, _under

if TYPE_CHECKING:
    from .npm import NpmManifest

NUXT_CONFIGS = tuple(f"nuxt.config.{ext}" for ext in ("ts", "js", "mjs", "mts"))

#: The source directories Nuxt reads when the config names none: Nuxt 3 keeps
#: app code at the root, Nuxt 4's default ``srcDir`` is ``app/``.
_NUXT_SRC = ("", "app/")


def _nuxt_globs(srcs: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(entry_globs, review_globs)`` for an app whose source directory is one of *srcs*.

    ``server/`` sits at the root on Nuxt 4 and in the source directory on
    Nuxt 3, so a known source directory anchors both.
    """
    servers = ("",) if srcs == _NUXT_SRC else tuple(dict.fromkeys(("", *srcs)))
    entry = (
        *_under(srcs, "pages/*", "layouts/*", "middleware/*", "plugins/*"),
        *_under(srcs, "app.vue", "error.vue", *_js("app.config", "router.options")),
        *_under(servers, "server/*"),
    )
    review = (*_under(srcs, "components/*", "composables/*", "utils/*"), "shared/*")
    return entry, review


_ENTRY, _REVIEW = _nuxt_globs(_NUXT_SRC)
NUXT = FrameworkFacts(
    name="Nuxt",
    anchor="framework:nuxt",
    npm_packages=("nuxt", "nuxt3"),
    config_files=NUXT_CONFIGS,
    entry_globs=_ENTRY,
    review_globs=_REVIEW,
)

NUXT_I18N = "@nuxtjs/i18n"
_LANG_DIR_RE = re.compile(r"""\blangDir\s*:\s*["'`]([^"'`\n]+)["'`]""")
_SRC_DIR_RE = re.compile(r"""\bsrcDir\s*:\s*["'`]([^"'`]+)["'`]""")
_COMPAT_V4_RE = re.compile(r"\bcompatibilityVersion\s*:\s*4\b")
_VERSION_MAJOR_RE = re.compile(r"(\d+)")


def _join(*parts: str) -> str:
    """The parts joined and normalised; the repository root is ``""``, never ``"."``."""
    joined = posixpath.normpath(posixpath.join(*[p for p in parts if p])) if any(parts) else ""
    return "" if joined == "." else joined


def nuxt_src_dir(manifest: NpmManifest, paths: Collection[str] | None = None) -> str | None:
    """The repo-relative ``srcDir`` of the Nuxt app *manifest* declares.

    The literal ``srcDir`` in its ``nuxt.config``; else, asked against the
    repository's *paths*, ``app/`` on Nuxt 4 unless the app keeps a Nuxt 3
    layout (``pages/`` or ``app.vue`` at the root), else the root. None when
    there is no literal and no *paths* to ask.
    """
    root = manifest.rel_dir
    config = NUXT.config(manifest) or ""
    match = _SRC_DIR_RE.search(config)
    if match:
        return _join(root, match.group(1).strip().rstrip("/"))
    if paths is None:
        return None
    version = next((manifest.versions[p] for p in NUXT.npm_packages if p in manifest.versions), "")
    major = _VERSION_MAJOR_RE.search(version)
    v4 = (major is not None and int(major.group(1)) >= 4) or bool(_COMPAT_V4_RE.search(config))
    app = _join(root, "app")
    if v4 and any(p.startswith(app + "/") for p in paths):
        # Nuxt 4 keeps a Nuxt 3 layout (pages/ or app.vue at the root) at the root.
        legacy = (_join(root, "app.vue"), _join(root, "pages") + "/")
        if not any(p == legacy[0] or p.startswith(legacy[1]) for p in paths):
            return app
    return root


def _nuxt_lang_dirs(manifest: NpmManifest) -> tuple[str, ...]:
    """The literal ``langDir`` values in the app's Nuxt config.

    Read, not evaluated: a computed value is missed, and only the default
    locale directories are anchored then.
    """
    return tuple(dict.fromkeys(_LANG_DIR_RE.findall(NUXT.config(manifest) or "")))


def _nuxt_i18n_globs(manifest: NpmManifest, srcs: tuple[str, ...]) -> tuple[str, ...]:
    """Locale files and the vue-i18n config ``@nuxtjs/i18n`` loads by path.

    v9 and later resolve ``langDir`` under ``<rootDir>/i18n`` (default
    ``locales``); v8 resolved it under the source directory, one of *srcs*.
    Every reading is anchored, since an extra anchor only spares a file that
    is not there.
    """
    src_dirs = tuple(s.rstrip("/") for s in srcs)
    dirs = {"i18n/locales", *(_join(s, "locales") for s in src_dirs)}
    for lang_dir in _nuxt_lang_dirs(manifest):
        for base in ("i18n", *src_dirs):
            if (inside := _inside(posixpath.join(base, lang_dir))) is not None:
                dirs.add(inside)
    configs = _js("i18n/i18n.config", *(_join(s, "i18n.config") for s in src_dirs))
    return (*(f"{glob.escape(d)}/*" for d in sorted(dirs)), *configs)


def nuxt_facts(manifest: NpmManifest, paths: Collection[str] | None = None) -> FrameworkFacts:
    """:data:`NUXT` with its globs rooted at the app's source directory.

    The globs are relative to the manifest's directory, like every
    :func:`~.js.js_frameworks` answer.
    """
    src = nuxt_src_dir(manifest, paths)
    if src is None:
        srcs = _NUXT_SRC
    else:
        root = manifest.rel_dir
        rel = src[len(root) + 1 :] if root and src.startswith(root + "/") else src
        if src == root:
            srcs = ("",)
        elif root and rel == src:
            srcs = _NUXT_SRC  # a srcDir outside the package: nothing of ours to root at
        else:
            srcs = (f"{glob.escape(rel)}/",)
    entry, review = _nuxt_globs(srcs)
    if NUXT_I18N in manifest.dependencies:
        entry += _nuxt_i18n_globs(manifest, srcs)
    return replace(NUXT, entry_globs=entry, review_globs=review)
