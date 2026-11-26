"""Which JS frameworks a ``package.json`` uses, and the files they load by convention."""

from __future__ import annotations

import glob
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from .astro import ASTRO
from .base import FrameworkFacts, _js
from .expo_router import EXPO_ROUTER
from .next import NEXT_PAGES
from .npm import NpmManifest, npm_manifests
from .nuxt import NUXT, NUXT_I18N, _nuxt_i18n_globs
from .react_email import REACT_EMAIL, _react_email_dirs
from .remix import REMIX
from .sveltekit import SVELTEKIT

JS_FRAMEWORKS: tuple[FrameworkFacts, ...] = (
    REACT_EMAIL, NEXT_PAGES, REMIX, NUXT, SVELTEKIT, ASTRO, EXPO_ROUTER,
)


def js_frameworks(manifest: NpmManifest) -> list[tuple[FrameworkFacts, str]]:
    """``(facts, the dependency that marks it)`` for each JS framework *manifest* uses.

    The facts' globs are relative to the manifest's directory. A React Email
    project's are rooted at every directory its ``email`` scripts read.
    """
    out: list[tuple[FrameworkFacts, str]] = []
    for facts in JS_FRAMEWORKS:
        pkg = facts.matches_npm(manifest)
        if pkg is None:
            continue
        if facts is NUXT and NUXT_I18N in manifest.dependencies:
            facts = replace(facts, entry_globs=facts.entry_globs + _nuxt_i18n_globs(manifest))
        if facts is REACT_EMAIL:
            dirs = _react_email_dirs(manifest)
            if not dirs:
                continue
            # Escaped: a directory named ``emails[v2]`` is a name, not a pattern.
            stems = ("*" if d == "." else f"{glob.escape(d)}/*" for d in dirs)
            facts = replace(facts, entry_globs=_js(*stems))
        out.append((facts, pkg))
    return out


def convention_load_reasons(repo_root: str | Path, paths: Iterable[str]) -> dict[str, str]:
    """For each of *paths* a JS framework may load by convention, why.

    A file a framework declared in a ``package.json`` at or above it anchors
    (an entry glob) or imports by name when used (a review glob). The dead-code
    deletion gate reads this: the framework, not an importer, decides whether
    such a file runs.
    """
    wanted = sorted(set(paths))
    reasons: dict[str, str] = {}
    for manifest in npm_manifests(repo_root, wanted):
        for facts, pkg in js_frameworks(manifest):
            loaded = replace(facts, entry_globs=facts.entry_globs + facts.review_globs)
            for path in loaded.entry_files(manifest.rel_dir, wanted):
                reasons.setdefault(
                    path,
                    f"{facts.name} loads files at this path by convention "
                    f"({manifest.rel_dir or '.'}/package.json depends on {pkg}), "
                    "so no importer is not proof of disuse",
                )
    return reasons
