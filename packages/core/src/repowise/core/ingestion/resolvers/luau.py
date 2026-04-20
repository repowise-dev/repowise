"""Luau import resolution.

Luau's ``require(...)`` accepts three kinds of argument:

1. String literals — e.g. ``require("some/path")`` (Lemur / plain Lua style).
2. Relative instance paths — ``require(script.Parent.Foo)`` or
   ``require(script.Foo)``.
3. Absolute Roblox instance paths — ``require(game.ReplicatedStorage.Foo)``,
   where the leading service is resolved against a Rojo project's ``tree``
   mapping in ``default.project.json``.

This resolver handles (1) and (2) directly.  (3) requires reading the Rojo
project JSON to map a service subtree (e.g. ``ReplicatedStorage.Shared``) back
to a filesystem directory; that will be layered in via
``core/ingestion/dynamic_hints/rojo.py`` in a follow-up (issue #52).

Unresolved paths are intentionally *not* silently matched by filename — a
wrong edge is worse than no edge when the downstream graph feeds docs and
dead-code detection.  They fall through to ``add_external_node`` so they
still appear in the graph as external references.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .context import ResolverContext

# `script.Parent.Foo.Bar` / `script.Foo` — capture everything after the leading
# `script` so we can walk up/down from the importer.
_SCRIPT_RELATIVE = re.compile(r"^\s*script\s*((?:\.\s*\w+\s*)+)\s*$")

# `game.<Service>.<Path>...` — capture the service and the remainder.
_GAME_ABSOLUTE = re.compile(r"^\s*game\s*\.\s*(\w+)\s*((?:\.\s*\w+\s*)*)$")

_LUAU_SUFFIXES: tuple[str, ...] = (".luau", ".lua")


def resolve_luau_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve a Luau ``require(...)`` argument to a repo-relative file path.

    ``module_path`` is the raw argument text captured by ``luau.scm`` — it may
    be a string literal (with surrounding quotes) or a Luau expression such as
    ``script.Parent.Foo``.
    """
    arg = module_path.strip()

    # String literal: require("some/path")
    if (arg.startswith('"') and arg.endswith('"')) or (arg.startswith("'") and arg.endswith("'")):
        literal = arg[1:-1]
        resolved = _resolve_literal(literal, importer_path, ctx)
        if resolved is not None:
            return resolved
        return ctx.add_external_node(literal)

    # Relative: script[.Parent]*.Name[.Name]*
    m = _SCRIPT_RELATIVE.match(arg)
    if m:
        parts = [p.strip() for p in m.group(1).split(".") if p.strip()]
        resolved = _resolve_script_relative(parts, importer_path, ctx)
        if resolved is not None:
            return resolved
        return ctx.add_external_node(arg)

    # Absolute: game.<Service>.Path...
    # Full Rojo-tree resolution is out of scope for this skeleton PR — fall
    # through to an external node so the graph still records the reference.
    m = _GAME_ABSOLUTE.match(arg)
    if m:
        return ctx.add_external_node(arg)

    # Unknown expression shape — record as external, don't guess.
    return ctx.add_external_node(arg)


def _resolve_literal(literal: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a plain string require — relative or stem match."""
    importer_dir = PurePosixPath(importer_path).parent
    candidate = (importer_dir / literal).as_posix()
    for suffix in _LUAU_SUFFIXES:
        full = f"{candidate}{suffix}"
        if full in ctx.path_set:
            return full
    if literal in ctx.path_set:
        return literal

    stem = PurePosixPath(literal).stem.lower().replace("-", "_")
    result = ctx.stem_lookup(stem)
    if result and any(result.endswith(s) for s in _LUAU_SUFFIXES):
        return result
    return None


def _resolve_script_relative(
    parts: list[str], importer_path: str, ctx: ResolverContext
) -> str | None:
    """Walk ``Parent``/name segments relative to the importing file.

    Roblox semantics: ``script`` is the importing module instance; its
    ``script.Parent`` is the *container* that holds it.  For Rojo-synced
    code, a ``.luau``/``.lua`` file lives inside its container directory,
    so ``script.Parent`` is that directory.  This means the *first*
    ``Parent`` segment is an identity (we're already there); each
    subsequent ``Parent`` walks one more level up.

    After the leading ``Parent`` run, any remaining identifiers descend
    into child instances by name.  The terminal segment resolves to either
    ``<name>.luau``/``<name>.lua`` or a directory with
    ``init.luau``/``init.lua``.
    """
    here = PurePosixPath(importer_path).parent
    i = 0
    # First "Parent" is a no-op — `here` already represents script.Parent.
    if i < len(parts) and parts[i] == "Parent":
        i += 1
    # Each subsequent "Parent" walks up one level.
    while i < len(parts) and parts[i] == "Parent":
        here = here.parent
        i += 1

    remainder = parts[i:]
    if not remainder:
        return None

    base = here
    for seg in remainder[:-1]:
        base = base / seg

    name = remainder[-1]

    # Module-as-file: <base>/<name>.luau|.lua
    for suffix in _LUAU_SUFFIXES:
        candidate = (base / f"{name}{suffix}").as_posix()
        if candidate in ctx.path_set:
            return candidate

    # Module-as-directory: <base>/<name>/init.luau|.lua
    for suffix in _LUAU_SUFFIXES:
        candidate = (base / name / f"init{suffix}").as_posix()
        if candidate in ctx.path_set:
            return candidate

    return None
