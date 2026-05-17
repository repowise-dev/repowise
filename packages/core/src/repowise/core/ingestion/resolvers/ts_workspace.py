"""npm/yarn/pnpm workspace package resolution for TypeScript imports.

Reads the root ``package.json``'s ``workspaces`` field (string list or
``{"packages": [...]}`` form), expands glob patterns, and reads each
sibling package's ``name`` field. The resulting ``{pkg_name: dir_posix}``
map lets the TS resolver turn ``import x from "@myorg/foo"`` into the
correct intra-repo file rather than an ``external:`` node.

Subpath imports (``@myorg/foo/bar/baz``) honour Node.js ``"exports"``
subpath patterns when the workspace's ``package.json`` declares them:

    "exports": {
      ".":             "./src/index.ts",
      "./util":        "./src/util.ts",
      "./graph/*":     "./src/graph/*.tsx",
      "./modules/*":   { "import": "./src/modules/*.ts" }
    }

Conditional values (``{"import": ..., "default": ..., ...}``) are
flattened to the first plausible source target. Packages without an
``exports`` field fall back to the legacy ``<pkg>/<subpath>`` probe so
"plain" monorepo layouts keep working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import ResolverContext


# Order in which we collapse Node "conditional exports" objects down to
# a single target. Source-pointing conditions come first so a TS-aware
# static analyser sees the original ``.ts`` file rather than a built
# artefact, then ESM/default, with CJS last.
_CONDITION_PRIORITY: tuple[str, ...] = (
    "source",
    "import",
    "default",
    "node",
    "require",
    "types",
    "browser",
)


def _flatten_export_value(value: Any) -> str | None:
    """Collapse a Node ``exports`` entry to a single relative target string.

    Returns ``None`` for blocked entries (``null``) or shapes we can't
    handle. Recursively unwraps nested condition objects and arrays.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for cond in _CONDITION_PRIORITY:
            inner = value.get(cond)
            if inner is None:
                continue
            flat = _flatten_export_value(inner)
            if flat:
                return flat
        return None
    if isinstance(value, list):
        for item in value:
            flat = _flatten_export_value(item)
            if flat:
                return flat
    return None


def _build_exports_map(pkg_data: dict) -> dict[str, str]:
    """Return ``{exports_key: relative_target}`` for a workspace package.

    ``exports`` may be a single string (shorthand for ``{".": <str>}``)
    or a subpath dict. Keys that don't start with ``.`` are dropped (the
    Node spec disallows mixing main-entry shorthand with subpath maps).
    """
    raw = pkg_data.get("exports")
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {".": raw}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.startswith("."):
            continue
        flat = _flatten_export_value(value)
        if flat is None:
            continue
        out[key] = flat
    return out


def _match_export_key(subpath: str, exports_map: dict[str, str]) -> str | None:
    """Resolve a subpath against an ``exports`` map.

    ``subpath`` is the part of the import specifier after the package
    name, with no leading slash (``""`` for the bare package, ``"lib/x"``
    for ``@org/pkg/lib/x``). Exact keys win over wildcard patterns; among
    wildcards the longest static prefix wins (Node spec).
    """
    key = "." if subpath == "" else "./" + subpath
    if key in exports_map:
        return exports_map[key]
    best_target: str | None = None
    best_prefix_len = -1
    for pattern, target in exports_map.items():
        if "*" not in pattern:
            continue
        prefix, _, suffix = pattern.partition("*")
        if not key.startswith(prefix):
            continue
        if suffix and not key.endswith(suffix):
            continue
        captured = (
            key[len(prefix) : len(key) - len(suffix)]
            if suffix
            else key[len(prefix) :]
        )
        resolved = target.replace("*", captured, 1) if "*" in target else target
        if len(prefix) > best_prefix_len:
            best_target = resolved
            best_prefix_len = len(prefix)
    return best_target


def _read_workspaces_field(pkg_data: dict) -> list[str]:
    ws = pkg_data.get("workspaces")
    if isinstance(ws, list):
        return [str(p) for p in ws if isinstance(p, str)]
    if isinstance(ws, dict):
        packages = ws.get("packages")
        if isinstance(packages, list):
            return [str(p) for p in packages if isinstance(p, str)]
    return []


def build_workspace_map(repo_path: Path | None) -> dict[str, str]:
    """Return ``{package_name: dir_posix}`` for every workspace package.

    Empty dict if no root ``package.json`` or no ``workspaces`` field.
    """
    return {name: info["dir"] for name, info in build_workspace_info(repo_path).items()}


def build_workspace_info(repo_path: Path | None) -> dict[str, dict[str, Any]]:
    """Return ``{pkg_name: {"dir": <posix>, "exports": {...}, "main": str|None}}``.

    A richer counterpart to :func:`build_workspace_map` that also carries
    the workspace package's ``exports`` subpath map (Node.js spec) plus
    ``main``/``module`` entry-point hints. Lets the resolver translate
    sub-path imports through the package's own resolution rules instead
    of probing ``<pkg>/<subpath>`` blindly.
    """
    if repo_path is None or not repo_path.is_dir():
        return {}
    root_pkg = repo_path / "package.json"
    if not root_pkg.is_file():
        return {}
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    patterns = _read_workspaces_field(data)
    if not patterns:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for pattern in patterns:
        # Glob each pattern relative to the repo root. ``pathlib.Path.glob``
        # already understands ``*`` and ``**``.
        for ws_dir in repo_path.glob(pattern):
            if not ws_dir.is_dir():
                continue
            ws_pkg = ws_dir / "package.json"
            if not ws_pkg.is_file():
                continue
            try:
                ws_data = json.loads(ws_pkg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if not isinstance(ws_data, dict):
                continue
            name = ws_data.get("name")
            if not isinstance(name, str) or not name:
                continue
            try:
                rel = ws_dir.relative_to(repo_path).as_posix()
            except ValueError:
                continue
            result[name] = {
                "dir": rel,
                "exports": _build_exports_map(ws_data),
                "main": ws_data.get("module") if isinstance(ws_data.get("module"), str)
                        else (ws_data.get("main") if isinstance(ws_data.get("main"), str) else None),
            }
    return result


def get_or_build_workspace_info(ctx: "ResolverContext") -> dict[str, dict[str, Any]]:
    cached = getattr(ctx, "_ts_workspace_info", None)
    if cached is not None:
        return cached
    info = build_workspace_info(ctx.repo_path)
    ctx._ts_workspace_info = info  # type: ignore[attr-defined]
    return info


def get_or_build_workspace_map(ctx: "ResolverContext") -> dict[str, str]:
    """Backward-compat shim — kept for callers that only need name → dir."""
    return {name: info["dir"] for name, info in get_or_build_workspace_info(ctx).items()}


_PROBE_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _probe_path(base: str, path_set: set[str]) -> str | None:
    """Locate a concrete file for ``base`` (a repo-relative path stem).

    Tries the path as-is first (handles targets that already carry an
    extension, e.g. ``"./src/graph/sigma-canvas.tsx"`` from an exports
    pattern). Then probes common TS/JS extensions and ``index.*``
    children so directory-shaped specifiers resolve to a barrel file.
    """
    if base in path_set:
        return base
    for ext in _PROBE_EXTENSIONS:
        cand = base + ext
        if cand in path_set:
            return cand
    for ext in _PROBE_EXTENSIONS:
        cand = f"{base}/index{ext}"
        if cand in path_set:
            return cand
    return None


def resolve_via_workspaces(module_path: str, ctx: "ResolverContext") -> str | None:
    """Resolve a bare specifier (``@scope/pkg`` or ``@scope/pkg/sub/file``)
    against the workspace map. Honours each workspace's ``exports``
    subpath map (Node.js spec) before falling back to a ``<pkg>/<subpath>``
    probe so plain monorepo layouts without ``exports`` keep working.
    Returns a repo-relative path or None.
    """
    info = get_or_build_workspace_info(ctx)
    if not info:
        return None

    # Match the longest package-name prefix. ``@scope/pkg/sub/x`` should bind
    # ``@scope/pkg`` and resolve ``sub/x`` under that workspace's dir.
    best_name: str | None = None
    for name in info:
        if module_path == name or module_path.startswith(name + "/"):
            if best_name is None or len(name) > len(best_name):
                best_name = name
    if best_name is None:
        return None

    pkg = info[best_name]
    dir_posix: str = pkg["dir"]
    exports_map: dict[str, str] = pkg["exports"]
    sub = module_path[len(best_name) :].lstrip("/")

    # 1) ``exports`` field — the package's authoritative subpath map.
    if exports_map:
        target = _match_export_key(sub, exports_map)
        if target is not None:
            # Targets are package-relative ("./src/lib/foo.ts"). Strip the
            # leading "./" and join with the package dir to get a repo path.
            stripped = target.lstrip("./")
            resolved = _probe_path(f"{dir_posix}/{stripped}", ctx.path_set)
            if resolved is not None:
                return resolved

    # 2) Bare-package fallback — no ``exports[.]`` entry: try index.*,
    #    then ``main``/``module`` from package.json.
    if not sub:
        cand = _probe_path(f"{dir_posix}/index", ctx.path_set)
        if cand is not None:
            return cand
        main = pkg.get("main")
        if isinstance(main, str):
            cand = _probe_path(f"{dir_posix}/{main.lstrip('./')}", ctx.path_set)
            if cand is not None:
                return cand
        return None

    # 3) Subpath fallback — packages without ``exports`` (plain monorepo
    #    layouts): try ``<pkg>/<sub>`` directly, then under common source
    #    roots (``src``, ``lib``, ``dist``) so the resolver still finds
    #    files in packages that publish from a build directory.
    direct = _probe_path(f"{dir_posix}/{sub}", ctx.path_set)
    if direct is not None:
        return direct
    for src_root in ("src", "lib", "dist"):
        cand = _probe_path(f"{dir_posix}/{src_root}/{sub}", ctx.path_set)
        if cand is not None:
            return cand
    return None
