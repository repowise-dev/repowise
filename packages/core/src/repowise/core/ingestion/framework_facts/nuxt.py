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


def _nuxt_globs(
    srcs: tuple[str, ...],
    dirs: dict[str, str] | None = None,
    server_dir: str = "server",
    layers: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(entry_globs, review_globs)`` for an app whose source directory is one of *srcs*.

    ``server/`` sits at the root on Nuxt 4 and in the source directory on
    Nuxt 3, so a known source directory anchors both.
    """
    pages = glob.escape(dirs.get("pages", "pages")) if dirs else "pages"
    layouts = glob.escape(dirs.get("layouts", "layouts")) if dirs else "layouts"
    middleware = glob.escape(dirs.get("middleware", "middleware")) if dirs else "middleware"
    plugins = glob.escape(dirs.get("plugins", "plugins")) if dirs else "plugins"

    app_dirs = (f"{pages}/*", f"{layouts}/*", f"{middleware}/*", f"{plugins}/*")
    layer_dirs = ("pages/*", "layouts/*", "middleware/*", "plugins/*")

    all_srcs = tuple(dict.fromkeys((*srcs, *layers)))
    app_servers = ("",) if srcs == _NUXT_SRC else tuple(dict.fromkeys(("", *srcs)))

    entry = (
        *_under(srcs, *app_dirs),
        *_under(layers, *layer_dirs),
        *_under(all_srcs, "app.vue", "error.vue", *_js("app.config", "router.options")),
        *_under(app_servers, f"{server_dir}/*"),
        *_under(layers, "server/*"),
    )
    review = (*_under(all_srcs, "components/*", "composables/*", "utils/*"), "shared/*")
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
_DIR_BLOCK_RE = re.compile(r"""\bdir\s*:\s*\{([^}]+)\}""", re.DOTALL)
_DIR_PROP_RE = re.compile(r"""\b(pages|layouts|middleware|plugins)\s*:\s*["'`]([^"'`\n]+)["'`]""")
_SERVER_DIR_RE = re.compile(r"""\bserverDir\s*:\s*["'`]([^"'`\n]+)["'`]""")
_EXTENDS_ARRAY_RE = re.compile(r"""\bextends\s*:\s*\[([^\]]+)\]""", re.DOTALL)
_EXTENDS_STRING_RE = re.compile(r"""\bextends\s*:\s*["'`]([^"'`\n]+)["'`]""")
_STR_LITERAL_RE = re.compile(r"""["'`]([^"'`\n]+)["'`]""")


def _nuxt_dirs(config: str) -> dict[str, str]:
    m = _DIR_BLOCK_RE.search(config)
    if not m:
        return {}
    dirs: dict[str, str] = {}
    for prop, val in _DIR_PROP_RE.findall(m.group(1)):
        norm = posixpath.normpath(val.strip()).strip("/")
        if norm and norm != ".":
            dirs[prop] = norm
    return dirs


def _nuxt_server_dir(config: str) -> str:
    m = _SERVER_DIR_RE.search(config)
    if not m:
        return "server"
    norm = posixpath.normpath(m.group(1).strip()).strip("/")
    return norm if norm and norm != "." else "server"


def _nuxt_extends(config: str) -> tuple[str, ...]:
    if m := _EXTENDS_ARRAY_RE.search(config):
        return tuple(dict.fromkeys(p.strip() for p in _STR_LITERAL_RE.findall(m.group(1)) if p.strip()))
    if m := _EXTENDS_STRING_RE.search(config):
        return (m.group(1).strip(),)
    return ()


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
    ``locales``); v8 resolved it under the source directory, one of *srcs*
    (unescaped; this escapes what it builds).
    Every reading is anchored, since an extra anchor only spares a file that
    is not there.
    """
    src_dirs = tuple(s.rstrip("/") for s in srcs)
    dirs = {"i18n/locales", *(_join(s, "locales") for s in src_dirs)}
    for lang_dir in _nuxt_lang_dirs(manifest):
        for base in ("i18n", *src_dirs):
            if (inside := _inside(posixpath.join(base, lang_dir))) is not None:
                dirs.add(inside)
    configs = _js("i18n/i18n.config", *(glob.escape(_join(s, "i18n.config")) for s in src_dirs))
    return (*(f"{glob.escape(d)}/*" for d in sorted(dirs)), *configs)


def _layer_srcs(root: str, config: str) -> list[str]:
    extra_srcs: list[str] = []
    for ext in _nuxt_extends(config):
        layer_path = _join(root, ext.rstrip("/"))
        if layer_path.startswith(("../", "/")) or layer_path == "..":
            continue
        if root:
            rel_layer = posixpath.relpath(layer_path or ".", root)
            extra_srcs.append(f"{rel_layer}/")
        else:
            extra_srcs.append(f"{layer_path}/" if layer_path else "")
    return extra_srcs


def nuxt_facts(manifest: NpmManifest, paths: Collection[str] | None = None) -> FrameworkFacts:
    """:data:`NUXT` with its globs rooted at the app's source directory.

    The globs are relative to the manifest's directory, like every
    :func:`~.js.js_frameworks` answer.
    """
    root = manifest.rel_dir
    config = NUXT.config(manifest) or ""
    src = nuxt_src_dir(manifest, paths)
    if src is None:
        srcs = _NUXT_SRC
    else:
        rel = src[len(root) + 1 :] if root and src.startswith(root + "/") else src
        if src == root:
            srcs = ("",)
        elif root and rel == src:
            srcs = _NUXT_SRC  # a srcDir outside the package: nothing of ours to root at
        else:
            srcs = (f"{rel}/",)
    layers = tuple(dict.fromkeys(extra_srcs)) if (extra_srcs := _layer_srcs(root, config)) else ()
    dirs = _nuxt_dirs(config)
    server_dir = _nuxt_server_dir(config)
    entry, review = _nuxt_globs(
        tuple(glob.escape(s) for s in srcs),
        dirs=dirs,
        server_dir=glob.escape(server_dir),
        layers=tuple(glob.escape(s) for s in layers),
    )
    if NUXT_I18N in manifest.dependencies:
        entry += _nuxt_i18n_globs(manifest, srcs)
    return replace(NUXT, entry_globs=entry, review_globs=review)
