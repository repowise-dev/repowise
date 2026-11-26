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

_KIT_RE = re.compile(r"\bkit\s*:\s*\{")
_FILES_RE = re.compile(r"\bfiles\s*:\s*\{")
_HOOKS_RE = re.compile(r"\bhooks\s*:\s*\{")
# Block and line comments; ``//`` after ``:`` or a word character is a URL.
_COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:\w])//[^\n]*", re.S)
_INNER_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def _object_after(pattern: re.Pattern[str], text: str, *, top_only: bool = True) -> str:
    """The body of the first ``key: { ... }`` *pattern* finds in *text*, braces balanced.

    With *top_only*, a key inside a nested object of *text* is skipped.
    """
    for match in pattern.finditer(text):
        if top_only and text.count("{", 0, match.start()) != text.count("}", 0, match.start()):
            continue
        level, start = 1, match.end()
        for i in range(start, len(text)):
            if text[i] == "{":
                level += 1
            elif text[i] == "}":
                level -= 1
                if level == 0:
                    return text[start:i]
        return ""
    return ""


def _top_level(text: str) -> str:
    """*text* with every nested object removed, so only its own keys remain."""
    while (flat := _INNER_OBJECT_RE.sub("", text)) != text:
        text = flat
    return text


def _literal(key: str, text: str) -> str | None:
    """The string *key* is set to in *text*, when written as a plain literal inside the app."""
    match = re.search(rf"""\b{key}\s*:\s*(["'`])([^"'`\n]+)\1""", text)
    # ``$``: a template placeholder (``${base}/lib``), whose braces _top_level dropped.
    if match is None or "$" in match.group(2):
        return None
    return _inside(match.group(2).strip().rstrip("/"))


def kit_files(manifest: NpmManifest) -> KitFiles:
    """The ``kit.files`` directories of the SvelteKit app *manifest* declares.

    Only ``files`` directly under ``kit`` counts, and only its own keys (and
    those of ``hooks``). A literal that leaves the app, or one built from a
    template, is ignored, keeping the default.
    """
    config = _COMMENT_RE.sub("", SVELTEKIT.config(manifest) or "")
    kit = _object_after(_KIT_RE, config, top_only=False)
    block = _object_after(_FILES_RE, kit) if kit else ""
    if not block:
        return KitFiles()
    hooks = _top_level(_object_after(_HOOKS_RE, block))
    flat = _top_level(block)
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
