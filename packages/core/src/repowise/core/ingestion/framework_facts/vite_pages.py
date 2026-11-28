"""File-based routing plugins for Vite: ``vite-plugin-pages`` and ``unplugin-vue-router``."""

from __future__ import annotations

from .base import FrameworkFacts

#: Both plugins build the router from every file under ``src/pages/`` (their
#: default directory) and hand it to the app as a virtual module, so nothing
#: imports a page.
VITE_PAGES = FrameworkFacts(
    name="Vite file routes",
    anchor="framework:vite-pages",
    npm_packages=("vite-plugin-pages", "unplugin-vue-router"),
    entry_globs=("src/pages/*",),
)
