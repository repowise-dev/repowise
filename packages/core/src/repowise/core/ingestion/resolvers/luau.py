"""Luau import resolution.

Luau's ``require(...)`` accepts four kinds of argument:

1. String literals — ``require("./helper")`` or ``require("some/path")``
   (plain Lua style + Luau's new require-by-string).
2. Relative instance paths — ``require(script.Parent.Foo)``,
   ``require(script.Foo)``, or the Rojo-safe variant
   ``require(script.Parent:WaitForChild("Foo"))``.
3. Absolute Roblox instance paths — ``require(game.ReplicatedStorage.Foo)``,
   where the leading service is resolved against a Rojo project's ``tree``
   mapping in ``default.project.json``.
4. ``.luaurc``-aliased requires — ``require("@dep")``, resolved by reading
   ``.luaurc`` files along the directory hierarchy for an ``aliases`` map.

This resolver handles (1) and (2).  (3) requires a Rojo ``default.project.json``
reader layered in via ``core/ingestion/dynamic_hints/rojo.py``; (4) requires a
``.luaurc`` reader analogous to the tsconfig resolver.  Both are deferred to
follow-ups — see the xfail tests in ``test_luau_resolver.py``.

Unresolved paths are intentionally *not* silently matched by filename — a
wrong edge is worse than no edge when the downstream graph feeds docs and
dead-code detection.  They fall through to ``add_external_node`` so they
still appear in the graph as external references.

Parser contract note
--------------------
The tree-sitter query in ``queries/luau.scm`` captures the raw argument node;
``parser.py`` then normalizes the captured text with ``.strip("\"'` ")``
before calling this function.  String-literal requires therefore arrive here
*without* their surrounding quotes — e.g. ``require("./helper")`` reaches this
function as ``./helper``, not ``"./helper"``.  We identify the literal branch
by process of elimination (doesn't parse as ``script.X`` or ``game.X``).
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

    ``module_path`` is the argument text captured by ``luau.scm`` after the
    parser's quote-strip pass (see module docstring).  It may be a bare
    filesystem path (from a string-literal require), an instance-path
    expression such as ``script.Parent.Foo``, or an ``@alias`` reference.
    """
    arg = module_path.strip()

    # Relative instance path: script[.Parent]*.Name[.Name]*
    m = _SCRIPT_RELATIVE.match(arg)
    if m:
        parts = [p.strip() for p in m.group(1).split(".") if p.strip()]
        resolved = _resolve_script_relative(parts, importer_path, ctx)
        if resolved is not None:
            return resolved
        return ctx.add_external_node(arg)

    # Absolute instance path: game.<Service>.Path...
    # Full Rojo-tree resolution is deferred to the Rojo follow-up; fall through
    # to an external node so the graph still records the reference.
    if _GAME_ABSOLUTE.match(arg):
        return ctx.add_external_node(arg)

    # `.luaurc` alias: require("@dep").  Needs a luaurc reader; deferred.
    if arg.startswith("@"):
        return ctx.add_external_node(arg)

    # Everything else is a string-literal path.  The parser has already
    # stripped surrounding quotes, so `arg` is e.g. `./helper` or
    # `some/path`.  `_resolve_literal` handles both relative and stem-match
    # resolution; unresolved literals fall through to an external node
    # without any silent filename guess.
    resolved = _resolve_literal(arg, importer_path, ctx)
    if resolved is not None:
        return resolved
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
