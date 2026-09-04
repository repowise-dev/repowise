"""GDScript import resolution.

GDScript names dependencies three ways, all of which reach this function as
the raw quoted path after ``parser.py`` strips the surrounding quotes (see
queries/gdscript.scm)::

    preload("res://actors/player.gd")
    load("res://ui/menu.tscn")
    extends "res://actors/base_actor.gd"

``res://`` is an *absolute* path from the Godot project root -- the directory
holding ``project.godot`` -- not from the repo root. The two coincide in a
single-project repo and diverge in exactly the repo the validation corpus
leads with: ``godotengine/godot-demo-projects`` is one checkout containing
dozens of independent projects, each with its own ``project.godot`` and its
own ``res://`` namespace. Resolving against the repo root there would map
every project's ``res://player.gd`` onto whichever one sorted first.

So the resolver finds the *nearest* ``project.godot`` at or above the
importing file and resolves against that directory. A repo with no
``project.godot`` at all (a loose bag of scripts, or a plugin checked out
on its own) falls back to the repo root, which is the only sensible reading
of ``res://`` when the project boundary is not declared.

Unresolved paths are deliberately NOT stem-matched onto a same-named file
elsewhere in the repo: ``res://`` is exact by construction, so a miss means
the target genuinely is not indexed, and a wrong edge is worse than none.
They fall through to ``add_external_node`` so the reference still shows up.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from repowise.core.fs_walk import iter_glob

from .context import ResolverContext

_RES_PREFIX = "res://"

# Godot 4.4+ writes `uid://` references into scenes and, increasingly, into
# scripts. Resolving one needs the generated `.uid` sidecar files, which are
# build-cache artifacts the spec blocks from indexing. Recorded as external
# rather than guessed at.
_UID_PREFIX = "uid://"

# `user://` is the per-user writable data directory at runtime. It never
# points at a repo file.
_USER_PREFIX = "user://"


def _project_roots(ctx: ResolverContext) -> tuple[str, ...]:
    """Repo-relative POSIX dirs holding a ``project.godot``, longest first.

    Built once per ``GraphBuilder.build()`` and stashed on the context, the
    same lazy-index pattern the dotnet/PHP/Kotlin resolvers use.

    Scanned from the filesystem rather than ``ctx.path_set``: ``project.godot``
    is an ini manifest with no language tag of its own, so it is not
    guaranteed to be among the indexed source files.
    """
    cached = getattr(ctx, "_gdscript_project_roots", None)
    if cached is not None:
        return cached

    roots: list[str] = []
    if ctx.repo_path is not None:
        for manifest in iter_glob(
            ctx.repo_path, "project.godot", prune_nested_git=ctx.prune_nested_git
        ):
            try:
                rel = manifest.relative_to(ctx.repo_path).parent
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            roots.append("" if rel_posix == "." else rel_posix)

    # Longest first so the nearest enclosing project wins; the secondary sort
    # key keeps the order stable across runs (see ResolverContext.sorted_paths
    # on why resolver iteration order must not vary).
    result = tuple(sorted(set(roots), key=lambda r: (-len(r), r)))
    ctx._gdscript_project_roots = result  # type: ignore[attr-defined]
    return result


def _root_for(importer_path: str, ctx: ResolverContext) -> str:
    """Return the repo-relative project root governing *importer_path*."""
    for root in _project_roots(ctx):
        if not root:
            return ""
        if importer_path == root or importer_path.startswith(root + "/"):
            return root
    # No declared project boundary above this file: res:// is the repo root.
    return ""


def resolve_gdscript_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve a GDScript ``res://`` path to a repo-relative file path."""
    raw = module_path.strip()
    if not raw:
        return None

    if raw.startswith(_UID_PREFIX) or raw.startswith(_USER_PREFIX):
        return ctx.add_external_node(raw)

    if raw.startswith(_RES_PREFIX):
        relative = raw[len(_RES_PREFIX) :].lstrip("/")
        root = _root_for(importer_path, ctx)
        candidate = f"{root}/{relative}" if root else relative
        if candidate in ctx.path_set:
            return candidate
        return ctx.add_external_node(raw)

    # Godot also accepts a path relative to the importing script.
    candidate = _join_relative(PurePosixPath(importer_path).parent, raw)
    if candidate is not None and candidate in ctx.path_set:
        return candidate

    return ctx.add_external_node(raw)


def _join_relative(base: PurePosixPath, relative: str) -> str | None:
    """Join *relative* onto *base*, collapsing ``.``/``..`` textually.

    Returns None if the path escapes above the repo root. Clamping the
    escape instead would fold ``../../vendor/shared/player.gd`` onto
    ``vendor/shared/player.gd`` and, if that unrelated file happened to
    exist, resolve to it -- a wrong edge, which this module's whole
    contract is to avoid.

    Not ``Path.resolve()``: resolution must not touch the filesystem (the
    target is looked up in ``ctx.path_set``, and in tests no such file
    exists on disk), and ``PurePosixPath`` keeps ``..`` segments literal.
    """
    parts: list[str] = [p for p in base.parts if p not in ("", ".")]
    for segment in relative.split("/"):
        if segment == "..":
            if not parts:
                return None
            parts.pop()
        elif segment not in ("", "."):
            parts.append(segment)
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Engine-loaded scripts
# ---------------------------------------------------------------------------
# Godot reaches a script two ways that are not imports and so leave no edge in
# the graph: an ``[autoload]`` entry in project.godot registers it as a global
# singleton, and a scene attaches it to a node, saved in the .tscn as a
# ``Script`` ext_resource. Read here so the dead-code pass does not report
# every gameplay script in a Godot project as unreachable.

# `[ext_resource type="Script" uid="uid://..." path="res://player.gd" id="5"]`.
# Matched on the path suffix rather than on `type="Script"`: attribute order is
# not fixed, a Godot 3 scene may omit the type, and a `.gd` ext_resource is a
# script whatever the header says. The editor writes double quotes; single
# quotes are accepted because a hand-edited or tool-generated scene may use
# them and the backreference keeps the pair matched.
_SCRIPT_RESOURCE_RE = re.compile(
    r"""\[ext_resource\b[^\]]*?\bpath=(["'])(res://.+?\.gd)\1"""
)

_AUTOLOAD_SECTION = "[autoload]"


def _resolve_res(raw: str, owner_path: str, ctx: ResolverContext) -> str | None:
    """Return the indexed path *raw* names, or None if it is not indexed.

    Unlike :func:`resolve_gdscript_import` this never records an external
    node: a scene referencing an asset outside the index is not a dependency
    anyone asked to see, it is just a path that does not resolve.
    """
    relative = raw[len(_RES_PREFIX) :].lstrip("/")
    root = _root_for(owner_path, ctx)
    candidate = f"{root}/{relative}" if root else relative
    return candidate if candidate in ctx.path_set else None


def _rel_posix(path, ctx: ResolverContext) -> str | None:
    if ctx.repo_path is None:
        return None
    try:
        return path.relative_to(ctx.repo_path).as_posix()
    except ValueError:
        return None


def engine_loaded_scripts(
    ctx: ResolverContext,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(autoload singletons, scene-attached scripts)``.

    Both are sets of indexed repo-relative paths. A path that resolves to a
    file the index does not hold is dropped rather than guessed at, the same
    rule the import resolver follows.
    """
    if ctx.repo_path is None:
        return frozenset(), frozenset()

    autoloads: set[str] = set()
    for manifest in iter_glob(
        ctx.repo_path, "project.godot", prune_nested_git=ctx.prune_nested_git
    ):
        owner = _rel_posix(manifest, ctx)
        if owner is None:
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        in_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = stripped == _AUTOLOAD_SECTION
                continue
            if not in_section or "=" not in stripped:
                continue
            # `Global="*res://autoload/global.gd"` -- the leading `*` marks the
            # singleton as enabled and is not part of the path.
            value = stripped.split("=", 1)[1].strip().strip('"').lstrip("*")
            if not value.startswith(_RES_PREFIX) or not value.endswith(".gd"):
                continue
            resolved = _resolve_res(value, owner, ctx)
            if resolved is not None:
                autoloads.add(resolved)

    scene_scripts: set[str] = set()
    for scene in iter_glob(ctx.repo_path, "*.tscn", prune_nested_git=ctx.prune_nested_git):
        owner = _rel_posix(scene, ctx)
        if owner is None:
            continue
        try:
            text = scene.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _SCRIPT_RESOURCE_RE.finditer(text):
            resolved = _resolve_res(match.group(2), owner, ctx)
            if resolved is not None:
                scene_scripts.add(resolved)

    return frozenset(autoloads), frozenset(scene_scripts)
