"""Rojo project-tree and ``.luaurc`` alias readers for Luau resolution.

Rojo (``default.project.json``) maps the Roblox instance tree onto the
filesystem::

    {
      "name": "MyGame",
      "tree": {
        "$className": "DataModel",
        "ReplicatedStorage": {
          "Shared": { "$path": "src/shared" }
        },
        "ServerScriptService": { "$path": "src/server" }
      }
    }

so ``require(game.ReplicatedStorage.Shared.Util)`` resolves by finding the
longest instance-path prefix with a ``$path`` (``ReplicatedStorage.Shared``
→ ``src/shared``) and descending the remaining segments on disk.

``.luaurc`` declares require aliases::

    { "aliases": { "dep": "./dependency" } }

``require("@dep/foo")`` resolves against the ``aliases`` map of the nearest
``.luaurc`` walking UP from the importer; a child directory's ``.luaurc``
overrides parent aliases per key. Alias targets are relative to the
directory of the ``.luaurc`` that declares them.

Both indexes are built once per resolver run and cached on the context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

_LUAU_SUFFIXES: tuple[str, ...] = (".luau", ".lua")

# // and /* */ comments are legal in .luaurc (and common in the wild).
_LINE_COMMENT_RE = re.compile(r"^\s*//.*$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


# ---------------------------------------------------------------------------
# Rojo
# ---------------------------------------------------------------------------


def _walk_rojo_tree(node: dict, prefix: tuple[str, ...], out: dict[tuple[str, ...], str]) -> None:
    """Collect ``instance path → $path`` mappings from a Rojo tree node."""
    path = node.get("$path")
    if isinstance(path, str):
        out[prefix] = path.strip("./")
    for key, child in node.items():
        if key.startswith("$") or not isinstance(child, dict):
            continue
        _walk_rojo_tree(child, (*prefix, key), out)


def build_rojo_index(repo_path: Path | None) -> dict[tuple[str, ...], str]:
    """Parse ``default.project.json`` at the repo root into an instance map.

    Keys are instance-path tuples relative to ``game`` (the DataModel),
    values are repo-relative directory (or file) paths.
    """
    if repo_path is None:
        return {}
    project_file = repo_path / "default.project.json"
    if not project_file.is_file():
        return {}
    try:
        data = json.loads(project_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    tree = data.get("tree")
    if not isinstance(tree, dict):
        return {}
    out: dict[tuple[str, ...], str] = {}
    _walk_rojo_tree(tree, (), out)
    return out


def get_or_build_rojo_index(ctx: ResolverContext) -> dict[tuple[str, ...], str]:
    cached = getattr(ctx, "_rojo_index", None)
    if cached is not None:
        return cached
    index = build_rojo_index(ctx.repo_path)
    ctx._rojo_index = index  # type: ignore[attr-defined]
    return index


def resolve_game_path(segments: list[str], ctx: ResolverContext) -> str | None:
    """Resolve ``game.<Service>.…`` instance segments through the Rojo tree.

    *segments* excludes the leading ``game``. Longest declared instance
    prefix wins; remaining segments descend on the filesystem; the
    terminal segment is a ``<name>.luau``/``.lua`` file or a directory
    with ``init.luau``/``init.lua``.
    """
    index = get_or_build_rojo_index(ctx)
    if not index:
        return None

    for cut in range(len(segments), 0, -1):
        prefix = tuple(segments[:cut])
        base = index.get(prefix)
        if base is None:
            continue
        remainder = segments[cut:]
        if not remainder:
            # The require names the mapped instance itself.
            if base in ctx.path_set:
                return base
            for suffix in _LUAU_SUFFIXES:
                candidate = f"{base}/init{suffix}"
                if candidate in ctx.path_set:
                    return candidate
            return None
        dir_path = PurePosixPath(base)
        for seg in remainder[:-1]:
            dir_path = dir_path / seg
        name = remainder[-1]
        for suffix in _LUAU_SUFFIXES:
            candidate = (dir_path / f"{name}{suffix}").as_posix()
            if candidate in ctx.path_set:
                return candidate
        for suffix in _LUAU_SUFFIXES:
            candidate = (dir_path / name / f"init{suffix}").as_posix()
            if candidate in ctx.path_set:
                return candidate
        return None
    return None


# ---------------------------------------------------------------------------
# .luaurc aliases
# ---------------------------------------------------------------------------


def _read_luaurc(path: Path) -> dict[str, str]:
    """Read one ``.luaurc``'s ``aliases`` map (comment-tolerant JSON)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    text = _BLOCK_COMMENT_RE.sub("", _LINE_COMMENT_RE.sub("", text))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items() if isinstance(v, str)}


def get_or_build_luaurc_cache(ctx: ResolverContext) -> dict[str, dict[str, str]]:
    """Per-directory cache of parsed ``.luaurc`` alias maps (lazy)."""
    cached = getattr(ctx, "_luaurc_cache", None)
    if cached is None:
        cached = {}
        ctx._luaurc_cache = cached  # type: ignore[attr-defined]
    return cached


def resolve_luaurc_alias(alias_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve ``@alias[/sub/path]`` via ``.luaurc`` files above the importer.

    The nearest ``.luaurc`` declaring the alias wins (child overrides
    parent); the alias target is relative to that ``.luaurc``'s directory.
    """
    if ctx.repo_path is None or not alias_path.startswith("@"):
        return None
    body = alias_path[1:]
    if not body:
        return None
    alias, _, rest = body.partition("/")

    cache = get_or_build_luaurc_cache(ctx)
    repo = ctx.repo_path

    # Walk from the importer's directory up to the repo root.
    cur = PurePosixPath(importer_path).parent
    dirs: list[str] = []
    while True:
        dirs.append(cur.as_posix())
        if cur.as_posix() in (".", "/"):
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent

    for d in dirs:
        key = d if d != "." else ""
        if key not in cache:
            luaurc = (repo / d / ".luaurc") if key else (repo / ".luaurc")
            cache[key] = _read_luaurc(luaurc) if luaurc.is_file() else {}
        target = cache[key].get(alias)
        if target is None:
            continue
        base_dir = PurePosixPath(key) if key else PurePosixPath(".")
        combined = base_dir / target.lstrip("./") if not target.startswith("/") else PurePosixPath(target.lstrip("/"))
        if rest:
            combined = combined / rest
        normalized = PurePosixPath(*(
            seg for seg in combined.as_posix().split("/") if seg not in (".", "")
        ))
        # Resolve ".." segments textually (paths are repo-relative posix).
        parts: list[str] = []
        for seg in normalized.parts:
            if seg == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(seg)
        candidate_base = "/".join(parts)
        if candidate_base in ctx.path_set:
            return candidate_base
        for suffix in _LUAU_SUFFIXES:
            candidate = f"{candidate_base}{suffix}"
            if candidate in ctx.path_set:
                return candidate
        for suffix in _LUAU_SUFFIXES:
            candidate = f"{candidate_base}/init{suffix}"
            if candidate in ctx.path_set:
                return candidate
        # The nearest declaration of the alias decides — a parent's same
        # alias must not silently shadow a broken child mapping.
        return None
    return None
