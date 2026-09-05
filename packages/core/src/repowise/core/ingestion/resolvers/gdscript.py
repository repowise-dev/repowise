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
They fall through to ``add_external_node`` so the reference still shows up,
unless the path names an art or data asset, which yields nothing at all; see
:data:`GODOT_CODE_SUFFIXES`.

Shared with ``godot_resource`` (``.tscn`` / ``.tres`` / ``.escn`` and
``project.godot``): those files name their dependencies with the same
``res://`` paths, so they dispatch to the same function.
"""

from __future__ import annotations

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

#: Suffixes a Godot resource reference must carry for the *scene* extractor to
#: record it at all: a script, a scene, a resource instance (whose own
#: ``[ext_resource]`` list names the script implementing it), or a shader.
#:
#: A scene's ``[ext_resource]`` list mixes those with every texture, sound and
#: font the scene uses, thousands per repo, so it is filtered at extraction.
#: A script's ``preload`` is filtered here instead, and only on the miss path;
#: see :func:`_is_asset`.
GODOT_CODE_SUFFIXES: tuple[str, ...] = (".gd", ".cs", ".tscn", ".tres", ".escn", ".gdshader")

# Suffixes that name art or bulk data. Consulted ONLY when a reference has
# already failed to match an indexed file, so an indexed `.json` data table
# still gets its edge; this decides whether a *miss* is worth an external
# node. On the validation corpus 114 of Pixelorama's `res://` script
# references are .png/.svg/.ttf, and minting an external node each would put
# the repo's whole art tree in the dependency graph. Same call
# `lightweight_imports/html.py` makes for `<img src>`.
_ASSET_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".svg", ".webp", ".bmp", ".tga", ".exr", ".hdr",
    ".ktx", ".dds", ".ogg", ".wav", ".mp3", ".ttf", ".otf", ".woff", ".woff2",
    ".fnt", ".obj", ".glb", ".gltf", ".dae", ".blend", ".json", ".csv", ".txt",
    ".po", ".pot", ".translation", ".theme", ".cfg", ".webm", ".ogv",
})


def _is_asset(path: str) -> bool:
    """True when *path* names an art/data asset rather than code."""
    return PurePosixPath(path).suffix.lower() in _ASSET_SUFFIXES


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


def godot_project_root(importer_path: str, ctx: ResolverContext) -> str:
    """Return the repo-relative project root governing *importer_path*.

    Public because ``res://`` is not the only per-project namespace Godot
    keeps: ``framework_edges/godot.py`` scopes the ``class_name`` global table
    the same way, and for the same reason (see ``_project_roots``).
    """
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
        return _miss(raw, ctx)

    if raw.startswith(_RES_PREFIX):
        relative = raw[len(_RES_PREFIX) :].lstrip("/")
        root = godot_project_root(importer_path, ctx)
        candidate = f"{root}/{relative}" if root else relative
        if candidate in ctx.path_set:
            return candidate
        return _miss(raw, ctx)

    # Godot also accepts a path relative to the importing script.
    candidate = _join_relative(PurePosixPath(importer_path).parent, raw)
    if candidate is not None and candidate in ctx.path_set:
        return candidate

    return _miss(raw, ctx)


def _miss(raw: str, ctx: ResolverContext) -> str | None:
    """What an unmatched reference becomes: an external node, or nothing.

    Reached only once *raw* has failed to match an indexed file, so an
    in-repo ``.json`` data table keeps its real edge and only a genuine miss
    is judged here. An art asset yields nothing at all; anything else stays
    visible as an external node, because "we do not recognise this" is not
    "this is art" (``.gdshader`` is the case that matters: out of scope, but
    a real dependency).
    """
    if _is_asset(raw):
        return None
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
