"""Astro."""

from __future__ import annotations

import glob
import posixpath
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _js

if TYPE_CHECKING:
    from .npm import NpmManifest

ASTRO_CONFIGS = tuple(f"astro.config.{ext}" for ext in ("mjs", "js", "ts", "mts"))

ASTRO = FrameworkFacts(
    name="Astro",
    anchor="framework:astro",
    npm_packages=("astro",),
    config_files=ASTRO_CONFIGS,
    entry_globs=(
        "src/pages/*",
        *_js("src/middleware", "src/middleware/index", "src/content/config"),
        *_js("src/content.config", "src/actions/index"),
    ),
)


def astro_directory(manifest: NpmManifest, key: str, default: str) -> str:
    """Read a package-relative literal directory without evaluating JavaScript."""
    config = ASTRO.config(manifest) or ""
    match = re.search(
        rf"""\b{re.escape(key)}\s*:\s*(["'\x60])([^"'\x60\\\r\n]*)\1\s*(?=[,}}])""",
        config,
    )
    if match is None or "${" in match.group(2):
        return default
    directory = posixpath.normpath(match.group(2))
    if directory.startswith(("/", "../")) or directory == ".." or ":" in directory:
        return default
    return directory


def astro_facts(manifest: NpmManifest) -> FrameworkFacts:
    """Root convention globs at the configured source directory."""
    src = astro_directory(manifest, "srcDir", "src")
    prefix = "" if src == "." else glob.escape(src) + "/"
    return replace(
        ASTRO,
        entry_globs=tuple(prefix + pattern.removeprefix("src/") for pattern in ASTRO.entry_globs),
    )
