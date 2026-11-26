"""The :class:`FrameworkFacts` record and the glob helpers every framework module shares."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..composer import ComposerManifest
    from .npm import NpmManifest


@dataclass(frozen=True, slots=True)
class FrameworkFacts:
    name: str  # tech-stack display name
    anchor: str = ""  # graph node the convention edges hang off
    composer_packages: tuple[str, ...] = ()  # any of these in require marks it
    composer_types: tuple[str, ...] = ()  # or a composer ``type`` value
    #: Files the runtime loads with no importer, relative to the app (or
    #: extension) root. ``*`` spans ``/``, as in the dead-code never-flag globs.
    entry_globs: tuple[str, ...] = ()
    #: npm packages whose presence in a ``package.json`` marks the framework.
    npm_packages: tuple[str, ...] = ()
    #: Files the framework imports by name when a template uses them (Nuxt
    #: auto-imports). Not entries, since an unused one is never loaded, but no
    #: importer is not proof of disuse either, so none is deletion-ready.
    review_globs: tuple[str, ...] = ()
    #: Config files beside the ``package.json`` whose literals move where the
    #: framework looks (Nuxt ``srcDir``, SvelteKit ``kit.files``), first wins.
    config_files: tuple[str, ...] = ()

    def matches(self, manifest: ComposerManifest) -> bool:
        if manifest.type in self.composer_types:
            return True
        requires = manifest.requires
        return any(pkg in requires for pkg in self.composer_packages)

    def matches_npm(self, manifest: NpmManifest) -> str | None:
        """The first of :attr:`npm_packages` *manifest* depends on, or None."""
        return next((pkg for pkg in self.npm_packages if pkg in manifest.dependencies), None)

    def config(self, manifest: NpmManifest) -> str | None:
        """The text of the first of :attr:`config_files` beside *manifest*, or None."""
        return next((manifest.configs[n] for n in self.config_files if n in manifest.configs), None)

    def entry_files(self, root: str, paths: Iterable[str]) -> list[str]:
        """The members of *paths* that match an entry glob under *root*, sorted.

        The root is matched literally and only the part below it against the
        globs, so a directory named ``ext[old]`` is not read as a pattern.
        """
        if not self.entry_globs:
            return []
        prefix = f"{root}/" if root else ""
        pattern = _compiled(self.entry_globs)
        return sorted(
            p for p in paths if p.startswith(prefix) and pattern.match(p, len(prefix))
        )


@cache
def _compiled(globs: tuple[str, ...]) -> re.Pattern[str]:
    # Case-sensitive on every platform, like the paths it is matched against.
    return re.compile("|".join(fnmatch.translate(g) for g in globs))


_JS_EXTS = ("tsx", "ts", "jsx", "js", "mjs", "cjs", "mts", "cts")


def _js(*stems: str, exts: tuple[str, ...] = _JS_EXTS) -> tuple[str, ...]:
    """Each glob stem with every JS/TS extension (``*`` spans ``/``)."""
    return tuple(f"{stem}.{ext}" for stem in stems for ext in exts)


def _under(prefixes: tuple[str, ...], *globs: str) -> tuple[str, ...]:
    return tuple(f"{prefix}{glob}" for prefix in prefixes for glob in globs)


def _inside(path: str) -> str | None:
    """*path* normalised, or None when it leaves the package."""
    norm = posixpath.normpath(path)
    return None if norm == "." or norm.startswith(("..", "/")) else norm
