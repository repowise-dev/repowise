"""TYPO3 extension convention-file edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ..composer import ComposerManifest, find_vendor_manifests, repo_composer_manifests
from ..framework_facts import TYPO3
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_entry_edges,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


# ---------------------------------------------------------------------------
# F8 — TYPO3 framework edges
#
# TYPO3 loads a fixed set of convention-named files from each extension at
# bootstrap (``ext_localconf.php``, ``Configuration/TCA/*.php``, etc.). These
# files are never imported via PHP/JS imports, so the static graph reports
# ``in_degree=0`` and the dead-code analyzer flags them as unreachable.
#
# We attach a ``framework:typo3-core`` synthetic source to each convention
# file present in an extension (the list is ``framework_facts.TYPO3``).
# ``Configuration/JavaScriptModules.php`` is also parsed to add edges to the
# JS modules it registers (CKEditor plugins, backend modules, etc.).
#
# Discovery signal: ``composer.json`` with ``"type": "typo3-cms-extension"``
# (canonical for v11-v14) or, as fallback, any ``ext_emconf.php`` (legacy
# non-composer installs).
# ---------------------------------------------------------------------------

# JavaScriptModules.php registers JS files via entries like
# ``'@vendor/ext/MyModule' => 'EXT:ext_key/Resources/Public/JavaScript/My.js'``.
# We extract the right-hand value to add edges to the registered files.
_TYPO3_JS_MODULE_VALUE_RE = re.compile(
    r"""['"]EXT:(?P<ext>[a-z0-9_]+)/(?P<rel>[^'"]+\.(?:js|mjs))['"]""",
    re.IGNORECASE,
)


def _find_typo3_extensions(
    ctx: ResolverContext, path_set: set[str]
) -> dict[str, ComposerManifest | None]:
    """Extension root (repo-relative posix) -> its composer manifest, if any.

    Sources, in order of authority:
      1. Any ``composer.json`` with ``"type": "typo3-cms-extension"``, and, in
         a TYPO3 project, the packages installed under ``vendor/``.
      2. Any ``ext_emconf.php`` (legacy fallback when composer.json is missing).

    Cached on *ctx*: detection and edge emission both ask.
    """
    cached = getattr(ctx, "_typo3_extensions", None)
    if cached is not None:
        return cached
    manifests = repo_composer_manifests(ctx)
    if any(TYPO3.matches(m) for m in manifests) and ctx.repo_path is not None:
        manifests = [*manifests, *find_vendor_manifests(ctx.repo_path)]
    roots: dict[str, ComposerManifest | None] = {
        m.rel_dir: m for m in manifests if m.type in TYPO3.composer_types
    }
    for p in path_set:
        if posixpath.basename(p) == "ext_emconf.php":
            roots.setdefault(posixpath.dirname(p), None)
    ctx._typo3_extensions = roots  # type: ignore[attr-defined]
    return roots


def _add_typo3_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Add framework edges for each detected TYPO3 extension.

    Edges added:
      - ``framework:typo3-core`` → each convention file present in the extension.
      - ``Configuration/JavaScriptModules.php`` → each JS file it registers.
    """
    count = 0
    for root, manifest in _find_typo3_extensions(ctx, path_set).items():
        count += add_entry_edges(graph, TYPO3, root, path_set)
        js_modules_path = posixpath.join(root, "Configuration/JavaScriptModules.php")
        if js_modules_path in path_set:
            count += _add_typo3_js_module_edges(
                graph, parsed_files, path_set, root, js_modules_path, _extension_key(manifest)
            )
    return count


def _add_typo3_js_module_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
    ext_root: str,
    js_modules_path: str,
    own_ext_key: str | None,
) -> int:
    """Parse JavaScriptModules.php and add edges to each registered JS file.

    Resolves ``EXT:<ext_key>/<rel>`` to a repo-relative path under ``ext_root``
    when the extension is local; cross-extension references are ignored.
    """
    parsed = parsed_files.get(js_modules_path)
    if parsed is None:
        return 0
    text = read_text(parsed)
    if not text:
        return 0

    count = 0
    for m in _TYPO3_JS_MODULE_VALUE_RE.finditer(text):
        ext_key = m.group("ext").lower()
        rel = m.group("rel")
        if own_ext_key is not None and ext_key != own_ext_key:
            continue
        target = f"{ext_root}/{rel}" if ext_root else rel
        if target in path_set and _add_edge_if_new(graph, js_modules_path, target):
            count += 1
    return count


def _extension_key(manifest: ComposerManifest | None) -> str | None:
    """The TYPO3 extension key a composer manifest declares, or None.

    Reads ``extra.typo3/cms.extension-key`` first (canonical), falls back to
    deriving the key from the package name (``vendor/ext-key`` → ``ext_key``).
    """
    if manifest is None:
        return None
    typo3 = manifest.extra.get("typo3/cms")
    key = typo3.get("extension-key") if isinstance(typo3, dict) else None
    if isinstance(key, str) and key:
        return key.lower()
    if "/" in manifest.name:
        return manifest.name.split("/", 1)[1].replace("-", "_").lower()
    return None


class _Typo3Handler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "typo3" in dctx.stack_lower or bool(_find_typo3_extensions(dctx.ctx, dctx.path_set))

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_typo3_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_Typo3Handler()]
