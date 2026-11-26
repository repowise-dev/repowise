"""Nuxt, and the ``@nuxtjs/i18n`` locale files it loads by path."""

from __future__ import annotations

import glob
import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _inside, _js, _under

if TYPE_CHECKING:
    from .npm import NpmManifest

#: Nuxt 3 keeps app code at the root; Nuxt 4's default ``srcDir`` is ``app/``.
_NUXT_SRC = ("", "app/")
NUXT = FrameworkFacts(
    name="Nuxt",
    anchor="framework:nuxt",
    npm_packages=("nuxt", "nuxt3"),
    entry_globs=(
        *_under(_NUXT_SRC, "pages/*", "layouts/*", "middleware/*", "plugins/*"),
        *_under(_NUXT_SRC, "app.vue", "error.vue", *_js("app.config", "router.options")),
        "server/*",
    ),
    review_globs=(*_under(_NUXT_SRC, "components/*", "composables/*", "utils/*"), "shared/*"),
)

NUXT_I18N = "@nuxtjs/i18n"
_NUXT_CONFIGS = tuple(f"nuxt.config.{ext}" for ext in ("ts", "js", "mjs", "mts"))
_LANG_DIR_RE = re.compile(r"""\blangDir\s*:\s*["'`]([^"'`\n]+)["'`]""")


def _nuxt_lang_dirs(app_dir: Path) -> tuple[str, ...]:
    """The literal ``langDir`` values in the Nuxt config in *app_dir*.

    Read, not evaluated: a computed value is missed, and only the default
    locale directories are anchored then.
    """
    for name in _NUXT_CONFIGS:
        try:
            text = (app_dir / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        return tuple(dict.fromkeys(_LANG_DIR_RE.findall(text)))
    return ()


def _nuxt_i18n_globs(manifest: NpmManifest) -> tuple[str, ...]:
    """Locale files and the vue-i18n config ``@nuxtjs/i18n`` loads by path.

    v9 and later resolve ``langDir`` under ``<rootDir>/i18n`` (default
    ``locales``); v8 resolved it under the source directory. Every reading is
    anchored, since an extra anchor only spares a file that is not there.
    """
    dirs = {"i18n/locales", "locales", "app/locales"}
    for lang_dir in manifest.nuxt_lang_dirs:
        for base in ("i18n", "", "app"):
            if (inside := _inside(posixpath.join(base, lang_dir))) is not None:
                dirs.add(inside)
    configs = _js("i18n.config", "i18n/i18n.config", "app/i18n.config")
    return (*(f"{glob.escape(d)}/*" for d in sorted(dirs)), *configs)
