"""SvelteKit: the directories ``kit.files`` names, and the files it loads from them.

Every reader asks this module where a SvelteKit app keeps its code: the
convention anchors (:func:`sveltekit_facts`) and the ``$lib`` resolver
(``resolvers/svelte_kit.py``) both read :func:`kit_files`.

``svelte.config`` is read as text, never evaluated: a ``kit.files`` entry
counts when written as a literal, and a computed one keeps the default.
"""

from __future__ import annotations

import glob
import posixpath
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _inside, _js

if TYPE_CHECKING:
    from .npm import NpmManifest

SVELTE_CONFIGS = ("svelte.config.js", "svelte.config.ts", "svelte.config.mjs")


@dataclass(frozen=True, slots=True)
class KitFiles:
    """Where ``kit.files`` puts each directory, relative to the app root."""

    lib: str = "src/lib"
    routes: str = "src/routes"
    params: str = "src/params"
    service_worker: str = "src/service-worker"
    hooks_server: str = "src/hooks.server"
    hooks_client: str = "src/hooks.client"
    hooks_universal: str = "src/hooks"


def _kit_globs(files: KitFiles) -> tuple[str, ...]:
    def esc(path: str) -> str:
        return glob.escape(path)

    return (
        f"{esc(files.routes)}/+*",
        f"{esc(files.routes)}/*/+*",
        f"{esc(files.params)}/*",
        *_js(*(esc(p) for p in (files.hooks_universal, files.hooks_server, files.hooks_client))),
        *_js(esc(files.service_worker)),
        f"{esc(files.service_worker)}/*",
    )


SVELTEKIT = FrameworkFacts(
    name="SvelteKit",
    anchor="framework:sveltekit",
    npm_packages=("@sveltejs/kit",),
    config_files=SVELTE_CONFIGS,
    entry_globs=_kit_globs(KitFiles()),
)

_FILES_RE = re.compile(r"\bfiles\s*:\s*\{")
_HOOKS_RE = re.compile(r"\bhooks\s*:\s*\{([^{}]*)\}")


def _literal(key: str, text: str) -> str | None:
    match = re.search(rf"""\b{key}\s*:\s*["'`]([^"'`\n]+)["'`]""", text)
    return _inside(match.group(1).strip().rstrip("/")) if match else None


def _files_block(config: str) -> str:
    """The text of the ``files: { ... }`` object in *config*, braces balanced, else ''."""
    match = _FILES_RE.search(config)
    if match is None:
        return ""
    depth, start = 1, match.end()
    for i in range(start, len(config)):
        if config[i] == "{":
            depth += 1
        elif config[i] == "}":
            depth -= 1
            if depth == 0:
                return config[start:i]
    return ""


def kit_files(manifest: NpmManifest) -> KitFiles:
    """The ``kit.files`` directories of the SvelteKit app *manifest* declares.

    A literal that leaves the app is ignored, keeping the default.
    """
    block = _files_block(SVELTEKIT.config(manifest) or "")
    if not block:
        return KitFiles()
    hooks_match = _HOOKS_RE.search(block)
    hooks = hooks_match.group(1) if hooks_match else ""
    flat = _HOOKS_RE.sub("", block)
    found = {
        "lib": _literal("lib", flat),
        "routes": _literal("routes", flat),
        "params": _literal("params", flat),
        "service_worker": _literal("serviceWorker", flat),
        "hooks_server": _literal("server", hooks),
        "hooks_client": _literal("client", hooks),
        "hooks_universal": _literal("universal", hooks),
    }
    return replace(KitFiles(), **{k: v for k, v in found.items() if v is not None})


def sveltekit_lib_dir(manifest: NpmManifest) -> str:
    """The repo-relative directory ``$lib`` names in the app *manifest* declares."""
    return posixpath.join(manifest.rel_dir, kit_files(manifest).lib)


def sveltekit_facts(manifest: NpmManifest) -> FrameworkFacts:
    """:data:`SVELTEKIT` with its globs at the app's ``kit.files`` directories."""
    files = kit_files(manifest)
    if files == KitFiles():
        return SVELTEKIT
    return replace(SVELTEKIT, entry_globs=_kit_globs(files))
