"""TYPO3 extension convention-file edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
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
# file present in an extension. ``Configuration/JavaScriptModules.php`` is
# also parsed to add edges to the JS modules it registers (CKEditor plugins,
# backend modules, etc.).
#
# Discovery signal: ``composer.json`` with ``"type": "typo3-cms-extension"``
# (canonical for v11-v14) or, as fallback, any ``ext_emconf.php`` (legacy
# non-composer installs).
# ---------------------------------------------------------------------------

_TYPO3_EXTERNAL_NODE = "framework:typo3-core"

# Convention files at the extension root (matched by basename).
_TYPO3_ROOT_FILES: tuple[str, ...] = (
    "ext_localconf.php",
    "ext_emconf.php",
    "ext_tables.php",  # legacy v11-v13; absent in v14
    "ext_tables.sql",
)

# Convention files / globs under ``Configuration/`` (matched relative to the
# extension root, with forward-slash separators).
_TYPO3_CONFIG_GLOBS: tuple[str, ...] = (
    "Configuration/JavaScriptModules.php",
    "Configuration/ContentSecurityPolicies.php",
    "Configuration/RequestMiddlewares.php",
    "Configuration/Icons.php",
    "Configuration/Services.php",
    "Configuration/Services.yaml",
    "Configuration/Services.yml",
    "Configuration/TCA/*.php",
    "Configuration/TCA/Overrides/*.php",
    "Configuration/Backend/*.php",
    "Configuration/RTE/*.yaml",
    "Configuration/RTE/*.yml",
)

# JavaScriptModules.php registers JS files via entries like
# ``'@vendor/ext/MyModule' => 'EXT:ext_key/Resources/Public/JavaScript/My.js'``.
# We extract the right-hand value to add edges to the registered files.
_TYPO3_JS_MODULE_VALUE_RE = re.compile(
    r"""['"]EXT:(?P<ext>[a-z0-9_]+)/(?P<rel>[^'"]+\.(?:js|mjs))['"]""",
    re.IGNORECASE,
)


def _has_typo3_extension(ctx: ResolverContext, path_set: set[str]) -> bool:
    """Return True if the repo contains at least one TYPO3 extension.

    Checks ``composer.json`` ``type`` field first (canonical across v11-v14);
    falls back to any ``ext_emconf.php`` in path_set for legacy installs.
    """
    return bool(_find_typo3_extension_roots(ctx, path_set))


def _find_typo3_extension_roots(ctx: ResolverContext, path_set: set[str]) -> set[str]:
    """Return the set of extension root directories (repo-relative, posix).

    Sources, in order of authority:
      1. Any ``composer.json`` with ``"type": "typo3-cms-extension"``.
      2. Any ``ext_emconf.php`` (legacy fallback when composer.json is missing).
    """
    roots: set[str] = set()

    # 1. composer.json based discovery — walk the filesystem from repo root,
    # bounded depth to avoid deep vendor/node_modules traversal during tests.
    repo_path = getattr(ctx, "repo_path", None)
    if repo_path is not None:
        try:
            for composer in _iter_composer_jsons(Path(repo_path)):
                try:
                    data = json.loads(composer.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") != "typo3-cms-extension":
                    continue
                ext_root = composer.parent.resolve()
                rel = _to_repo_relative(repo_path, ext_root)
                if rel is not None:
                    roots.add(rel)
        except OSError:
            pass

    # 2. ext_emconf.php fallback for repos without a composer.json (legacy
    # non-composer installs, mostly v11 and earlier).
    for p in path_set:
        if Path(p).name == "ext_emconf.php":
            parent = Path(p).parent.as_posix()
            roots.add("" if parent == "." else parent)

    return roots


def _iter_composer_jsons(root: Path):
    """Yield composer.json paths likely to declare a TYPO3 extension.

    Searched locations:
      - ``<root>/composer.json``
      - ``<root>/<dir>/composer.json`` (single-level, for monorepos of extensions)
      - ``<root>/vendor/<vendor>/<package>/composer.json`` (project-mode TYPO3
        installs where extensions live under ``vendor/``)

    ``node_modules``, ``.git``, ``.bare``, and ``Build`` are skipped. Hidden
    directories are skipped at the top level only — vendor packages keep their
    nested layout.
    """
    skip_top = {"node_modules", ".git", ".bare", "var", "Build"}
    if (root / "composer.json").is_file():
        yield root / "composer.json"

    try:
        children = list(root.iterdir())
    except OSError:
        return

    for child in children:
        if not child.is_dir():
            continue
        if child.name == "vendor":
            yield from _iter_vendor_composer_jsons(child)
            continue
        if child.name in skip_top or child.name.startswith("."):
            continue
        candidate = child / "composer.json"
        if candidate.is_file():
            yield candidate


def _iter_vendor_composer_jsons(vendor_root: Path):
    """Yield composer.json files at ``vendor/<vendor>/<package>/composer.json``.

    Bounded to two levels deep — composer's flat layout means we never need
    to recurse further. Symlinks are followed at most once.
    """
    try:
        vendors = list(vendor_root.iterdir())
    except OSError:
        return
    for vendor_dir in vendors:
        if not vendor_dir.is_dir() or vendor_dir.name.startswith("."):
            continue
        try:
            packages = list(vendor_dir.iterdir())
        except OSError:
            continue
        for pkg_dir in packages:
            if not pkg_dir.is_dir() or pkg_dir.name.startswith("."):
                continue
            candidate = pkg_dir / "composer.json"
            if candidate.is_file():
                yield candidate


def _to_repo_relative(repo_path: Path, abs_path: Path) -> str | None:
    try:
        rel = abs_path.relative_to(Path(repo_path).resolve()).as_posix()
    except ValueError:
        return None
    return "" if rel == "." else rel


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
    roots = _find_typo3_extension_roots(ctx, path_set)
    if not roots:
        return 0

    if _TYPO3_EXTERNAL_NODE not in graph:
        graph.add_node(_TYPO3_EXTERNAL_NODE, language="external")

    for root in roots:
        for basename in _TYPO3_ROOT_FILES:
            target = f"{root}/{basename}" if root else basename
            if target in path_set and _add_edge_if_new(graph, _TYPO3_EXTERNAL_NODE, target):
                count += 1

        for glob in _TYPO3_CONFIG_GLOBS:
            prefix = f"{root}/" if root else ""
            pat = f"{prefix}{glob}"
            for p in path_set:
                if fnmatch.fnmatch(p, pat) and _add_edge_if_new(graph, _TYPO3_EXTERNAL_NODE, p):
                    count += 1

        # Parse JavaScriptModules.php for registered JS files.
        js_modules_path = (
            f"{root}/Configuration/JavaScriptModules.php"
            if root
            else "Configuration/JavaScriptModules.php"
        )
        if js_modules_path in path_set:
            count += _add_typo3_js_module_edges(
                graph, parsed_files, path_set, root, js_modules_path
            )

    return count


def _add_typo3_js_module_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
    ext_root: str,
    js_modules_path: str,
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

    own_ext_key = _extract_ext_key_from_composer(parsed_files, ext_root)

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


def _extract_ext_key_from_composer(parsed_files: dict[str, Any], ext_root: str) -> str | None:
    """Best-effort extraction of the TYPO3 extension key from composer.json.

    Reads ``extra.typo3/cms.extension-key`` first (canonical), falls back to
    deriving the key from the package name (``vendor/ext-key`` → ``ext_key``).
    Returns ``None`` if composer.json is missing or unreadable.
    """
    composer_path = f"{ext_root}/composer.json" if ext_root else "composer.json"
    parsed = parsed_files.get(composer_path)
    abs_path: Path | None = None
    if parsed is not None:
        abs_path = Path(parsed.file_info.abs_path)
    if abs_path is None or not abs_path.is_file():
        return None
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    extra_raw = data.get("extra")
    extra: dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
    typo3_raw = extra.get("typo3/cms")
    typo3: dict[str, Any] = typo3_raw if isinstance(typo3_raw, dict) else {}
    key = typo3.get("extension-key")
    if isinstance(key, str) and key:
        return key.lower()
    name = data.get("name")
    if isinstance(name, str) and "/" in name:
        return name.split("/", 1)[1].replace("-", "_").lower()
    return None


class _Typo3Handler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "typo3" in dctx.stack_lower or _has_typo3_extension(dctx.ctx, dctx.path_set)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_typo3_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_Typo3Handler()]
