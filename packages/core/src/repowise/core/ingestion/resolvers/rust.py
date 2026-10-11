"""Rust import resolution."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .context import ResolverContext

if TYPE_CHECKING:
    from tree_sitter import Node


def _get_frozen_path_set(ctx: ResolverContext) -> frozenset[str]:
    """Return a cached frozenset of ctx.path_set, built once per context."""
    cached: frozenset[str] | None = getattr(ctx, "_rust_frozen_path_set", None)
    if cached is not None:
        return cached
    frozen = frozenset(ctx.path_set)
    ctx._rust_frozen_path_set = frozen
    return frozen


def _get_frozen_parsed_keys(ctx: ResolverContext) -> frozenset[str]:
    """Return a cached frozenset of parsed_files keys, built once per context."""
    cached: frozenset[str] | None = getattr(ctx, "_rust_frozen_parsed_keys", None)
    if cached is not None:
        return cached
    parsed_files = ctx.parsed_files or {}
    frozen = frozenset(parsed_files.keys())
    ctx._rust_frozen_parsed_keys = frozen
    return frozen


def resolve_rust_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
    *,
    _reexport_depth: int = 0,
) -> str | None:
    """Resolve a Rust ``use`` path to a repo-relative file.

    ``_reexport_depth`` caps ``pub use`` hop-following at one level —
    re-export cycles between crate roots must not recurse.
    """
    # Strip `as <alias>` suffix from aliased imports (e.g. "typst_syntax as syntax")
    if " as " in module_path:
        module_path = module_path.split(" as ")[0].strip()

    # #[path = "..."] attribute — resolve relative to importer
    if module_path.endswith(".rs"):
        importer_dir = str(Path(importer_path).parent.as_posix())
        candidate = f"{importer_dir}/{module_path}"
        if candidate in ctx.path_set:
            return candidate
        return None

    parts = module_path.split("::")
    if not parts:
        return None

    # Strip brace-grouped imports: "crate::diag::{A, B}" → "crate::diag"
    if parts and parts[-1].startswith("{"):
        parts = parts[:-1]
    if not parts:
        return None

    frozen_path_set = _get_frozen_path_set(ctx)
    prefix = parts[0]

    # --- crate:: — resolve from the crate root ---
    if prefix == "crate":
        crate_root = _find_rust_crate_root(importer_path, ctx)
        resolved = _probe_rust_path(crate_root, parts[1:], frozen_path_set)
        if resolved is None and _reexport_depth == 0:
            # `use crate::Type` where lib.rs re-exports Type (the prelude
            # pattern `pub use crate::module::Type`) — follow one hop
            # through the crate root's re-exports.
            resolved = _follow_crate_root_reexport(crate_root, parts[1:], ctx)
        return resolved

    # --- self:: / super:: — resolve from the module tree, not the directory ---
    if prefix in ("self", "super"):
        hops = 0
        while hops < len(parts) and parts[hops] == "super":
            hops += 1
        rest = parts[hops:] if hops else parts[1:]
        resolved = _probe_module_relative(importer_path, hops, rest, frozen_path_set)
        if resolved is not None or not rest:
            return resolved
        # Legacy base, one directory per hop above the importer's own: right
        # for a crate root outside lib.rs/main.rs (``src/bin/x.rs``), whose
        # children sit beside it rather than under ``x/``.
        legacy = Path(importer_path).parent
        for _ in range(hops):
            legacy = legacy.parent
        return _probe_rust_path(legacy.as_posix(), rest, frozen_path_set)

    # --- Bare path: `mod foo;` or a 2018 path through a child module ---
    # A child module of the importer comes first: `mod foo;` and
    # `use foo::Bar` both name the declaring module's own child.
    resolved = _probe_rust_path(_rust_module_dir(importer_path), parts, frozen_path_set)
    if resolved is not None:
        return resolved
    if len(parts) == 1:
        importer_dir = str(Path(importer_path).parent.as_posix())
        resolved = _probe_rust_path(importer_dir, parts, frozen_path_set)
        if resolved is not None:
            return resolved

    # --- External crate (no prefix or unknown crate name) ---
    # Check if it might be a local module at the crate root first
    crate_root = _find_rust_crate_root(importer_path, ctx)
    resolved = _probe_rust_path(crate_root, parts, frozen_path_set)
    if resolved is not None:
        return resolved

    from .rust_workspace import get_or_build_cargo_workspace_index

    ws_index = get_or_build_cargo_workspace_index(ctx)

    # Try workspace-aware crate root for the importer.
    # _find_rust_crate_root is a heuristic and may return the wrong root;
    # if the workspace index can identify the importer's own crate, use that
    # src_dir as a second probe base before falling through to sibling lookup.
    if ws_index is not None:
        importer_crate = ws_index.lookup_crate_for_file(importer_path)
        if importer_crate and importer_crate.src_dir != crate_root:
            resolved = _probe_rust_path(importer_crate.src_dir, parts, frozen_path_set)
            if resolved is not None:
                return resolved

    # Cargo workspace sibling crate: `use sibling_crate::...`
    if ws_index is not None:
        sibling_src = ws_index.lookup(prefix)
        if sibling_src is not None and sibling_src != crate_root:
            resolved = _probe_rust_path(sibling_src, parts[1:], frozen_path_set)
            if resolved is None and _reexport_depth == 0:
                # `use crate_x::ReexportedName`: the name is no module file —
                # follow one hop through the sibling crate root's `pub use`
                # re-exports to the defining module.
                resolved = _follow_crate_root_reexport(sibling_src, parts[1:], ctx)
            if resolved is None:
                # Probe the crate root itself (lib.rs / main.rs) when the
                # import has no further path segments.
                for root_file in ("lib.rs", "main.rs"):
                    candidate = f"{sibling_src}/{root_file}"
                    if candidate in ctx.path_set:
                        return candidate
            if resolved is not None:
                return resolved

    # External crate
    return ctx.add_external_node(module_path)


def _follow_crate_root_reexport(
    crate_src_dir: str, remaining_parts: list[str], ctx: ResolverContext
) -> str | None:
    """Follow ONE ``pub use`` hop through a crate root's re-exports.

    ``lib.rs`` saying ``pub use crate::module::Type`` makes
    ``use crate_x::Type`` (and within-crate ``use crate::Type``) legal —
    but ``Type`` is no module file, so path probing fails. Match the
    first unresolved segment against the crate root's re-exported names
    and resolve that ``pub use``'s own module path instead. Depth is
    capped at one hop: a chain of re-exporting hubs resolves to the next
    hub, whose own ``pub use`` edges keep the graph connected.

    Segments after the matched one are carried through the hop. A
    re-exported *module* can be named on the way to a submodule, as in
    ``use crate_x::outer::inner::Type`` against a root that re-exports
    ``outer``, and dropping the tail resolves the import to the parent
    module's file instead. ``_probe_rust_path`` probes longest-first and
    walks down, so a matched name that is a type rather than a module
    still falls back to the module file for free.
    """
    if not remaining_parts:
        return None
    parsed_files = ctx.parsed_files or {}
    root_path = None
    for root_file in ("lib.rs", "main.rs"):
        candidate = f"{crate_src_dir}/{root_file}" if crate_src_dir not in (".", "") else root_file
        if candidate in parsed_files:
            root_path = candidate
            break
    if root_path is None:
        return None

    name = remaining_parts[0]
    rest = remaining_parts[1:]
    for imp in getattr(parsed_files[root_path], "imports", []) or []:
        if not getattr(imp, "is_reexport", False):
            continue
        mp = imp.module_path.split(" as ")[0].strip()
        segments = mp.split("::")
        last = segments[-1]
        names = list(getattr(imp, "imported_names", []) or [])
        if last.startswith("{"):
            # Brace group: `pub use crate::module::{A, B}` — the extractor
            # carries the selected names.
            if name not in names:
                continue
            target_mp = "::".join([*segments[:-1], name, *rest])
        elif last == "*":
            # Glob re-export: `pub use crate::module::*` — resolve the module.
            # `name` names a member of it rather than a path segment, so
            # neither it nor anything after it is appended.
            target_mp = "::".join(segments[:-1])
        elif last == name or name in names:
            # `name` either ends `mp` or is an alias for it: a renamed
            # re-export carries the alias in `imported_names`, never a
            # segment below `mp`. Either way it names `mp` itself, so only
            # the tail is appended.
            target_mp = "::".join([mp, *rest]) if rest else mp
        else:
            continue
        resolved = resolve_rust_import(target_mp, root_path, ctx, _reexport_depth=1)
        if resolved is not None and not resolved.startswith("external:"):
            return resolved
    return None


_DIRECTORY_MODULE_FILES = ("mod.rs", "lib.rs", "main.rs")


def _rust_module_dir(file_path: str) -> str:
    """The directory holding the child modules of the module *file_path* defines.

    ``mod.rs``, ``lib.rs`` and ``main.rs`` own their directory; any other
    ``foo.rs`` owns ``foo/`` (the 2018 layout, ``foo.rs`` beside ``foo/bar.rs``).
    """
    path = Path(file_path)
    if path.name in _DIRECTORY_MODULE_FILES:
        return path.parent.as_posix()
    return (path.parent / path.stem).as_posix()


def _probe_module_relative(
    importer_path: str, hops: int, rest: list[str], path_set: frozenset[str]
) -> str | None:
    """Resolve ``self::<rest>`` (no hops) or ``super::<rest>`` climbing *hops* modules.

    A path that names an item of the ancestor module itself
    (``super::Type``) lands on that module's own file.
    """
    base = Path(_rust_module_dir(importer_path))
    for _ in range(hops):
        base = base.parent
    base_dir = base.as_posix()
    if rest:
        resolved = _probe_rust_path(base_dir, rest, path_set)
        if resolved is not None:
            return resolved
    if not hops:
        return None
    for candidate in _module_files_of_dir(base_dir):
        if candidate in path_set and candidate != importer_path:
            return candidate
    return None


def _module_files_of_dir(module_dir: str) -> tuple[str, ...]:
    """The files that can define the module whose children live in *module_dir*."""
    roots = tuple(
        f"{module_dir}/{name}" if module_dir not in (".", "") else name
        for name in _DIRECTORY_MODULE_FILES
    )
    if module_dir in (".", ""):
        return roots
    return (f"{module_dir}.rs", *roots)


@lru_cache(maxsize=4096)
def _find_rust_crate_root_cached(
    importer_path: str, parsed_file_keys: frozenset[str]
) -> str:
    """Cached crate-root lookup (pure function with hashable args)."""
    parts = Path(importer_path).parts
    for i in range(len(parts) - 1, -1, -1):
        candidate_dir = Path(*parts[:i]) if i > 0 else Path(".")
        for root_file in ("lib.rs", "main.rs"):
            root_path = (candidate_dir / root_file).as_posix()
            if root_path in parsed_file_keys:
                return candidate_dir.as_posix()
        if parts[i] == "src" and i > 0:
            return candidate_dir.as_posix()
    return Path(importer_path).parent.as_posix()


def _find_rust_crate_root(importer_path: str, ctx: ResolverContext) -> str:
    """Find the ``src/`` directory containing the importer (Rust crate root)."""
    return _find_rust_crate_root_cached(importer_path, _get_frozen_parsed_keys(ctx))


@lru_cache(maxsize=8192)
def _probe_rust_path_cached(
    base_dir: str,
    path_parts: tuple[str, ...],
    path_set_frozen: frozenset[str],
) -> str | None:
    """Cached probe (pure function with hashable args)."""
    if not path_parts:
        return None
    base = Path(base_dir)
    for depth in range(len(path_parts), 0, -1):
        module_parts = path_parts[:depth]
        module_dir = base
        for p in module_parts[:-1]:
            module_dir = module_dir / p
        last = module_parts[-1]
        candidate = (module_dir / f"{last}.rs").as_posix()
        if candidate in path_set_frozen:
            return candidate
        candidate = (module_dir / last / "mod.rs").as_posix()
        if candidate in path_set_frozen:
            return candidate

    # Trailing-underscore fallback: #[path]-renamed modules use names like
    # `export_` backed by `export.rs`.
    stripped = tuple(p.rstrip("_") if p.endswith("_") else p for p in path_parts)
    if stripped != path_parts:
        for depth in range(len(stripped), 0, -1):
            module_parts = stripped[:depth]
            module_dir = base
            for p in module_parts[:-1]:
                module_dir = module_dir / p
            last = module_parts[-1]
            candidate = (module_dir / f"{last}.rs").as_posix()
            if candidate in path_set_frozen:
                return candidate
            candidate = (module_dir / last / "mod.rs").as_posix()
            if candidate in path_set_frozen:
                return candidate

    return None


def _probe_rust_path(
    base_dir: str,
    path_parts: list[str],
    path_set: frozenset[str],
) -> str | None:
    """Probe for a Rust module path, trying ``.rs`` and ``mod.rs`` variants."""
    return _probe_rust_path_cached(base_dir, tuple(path_parts), path_set)


def add_macro_rules_mod_imports(ctx: ResolverContext) -> int:
    """Give each top-level ``name!()`` call the modules ``macro_rules! name`` declares.

    A ``mod x;`` in a ``macro_rules!`` body is a template: it declares ``x`` in
    the module that calls the macro, and nowhere if nothing calls it. The
    definition and the call are paired by name within one crate (serde defines
    ``crate_root!`` in ``crate_root.rs`` and calls it from ``lib.rs``). The
    imports join the calling file's, so they resolve from there. Returns the
    number added.
    """
    from tree_sitter import Parser

    from ..parser import _get_language  # local import: avoid a cycle at module load

    language = _get_language("rust")
    sources = _rust_sources(ctx)
    if language is None or not any(b"macro_rules!" in src for src in sources.values()):
        return 0
    parser = Parser(language)
    templates: dict[tuple[str, str], list[Node]] = {}
    for path, src in sources.items():
        if b"macro_rules!" in src:
            crate_root = _find_rust_crate_root(path, ctx)
            for name, bodies in _mod_declaring_macros(parser.parse(src).root_node):
                templates.setdefault((crate_root, name), []).extend(bodies)
    if not templates:
        return 0
    return sum(
        _add_called_macro_mods(ctx, path, src, templates, parser) for path, src in sources.items()
    )


def _rust_sources(ctx: ResolverContext) -> dict[str, bytes]:
    parsed_files = ctx.parsed_files or {}
    sources = ctx.source_map or {}
    return {
        path: sources[path]
        for path, parsed in parsed_files.items()
        if parsed is not None and parsed.file_info.language == "rust" and path in sources
    }


def _mod_declaring_macros(root: Node) -> list[tuple[str, list[Node]]]:
    """``(name, rule bodies)`` of each top-level ``macro_rules!`` that declares a module."""
    from ..extractors.bindings.rust import macro_body_mod_names

    found = []
    for node in root.children:
        if node.type != "macro_definition":
            continue
        rules = (
            rule.child_by_field_name("right")
            for rule in node.children
            if rule.type == "macro_rule"
        )
        bodies = [body for body in rules if body is not None]
        if any(macro_body_mod_names(body) for body in bodies):
            found.append((_node_name(node.child_by_field_name("name")), bodies))
    return found


def _add_called_macro_mods(
    ctx: ResolverContext,
    path: str,
    src: bytes,
    templates: dict[tuple[str, str], list[Node]],
    parser: Any,
) -> int:
    """Append to *path*'s imports the modules of each template it calls."""
    from ..extractors.bindings.rust import macro_mod_imports

    crate_root = _find_rust_crate_root(path, ctx)
    names = {name for root, name in templates if root == crate_root and f"{name}!".encode() in src}
    if not names:
        return 0
    imports = ctx.parsed_files[path].imports
    have = {imp.module_path for imp in imports if imp.imported_names == ["*"]}
    called = [
        imp
        for name in _top_level_macro_calls(parser.parse(src).root_node, names)
        for body in templates[(crate_root, name)]
        for imp in macro_mod_imports(body, f"{name}!()")
    ]
    added = 0
    for imp in called:
        if imp.module_path not in have:
            have.add(imp.module_path)
            imports.append(imp)
            added += 1
    return added


def _top_level_macro_calls(root: Node, names: set[str]) -> list[str]:
    """Which of *names* the file calls as a macro at its top level, in order."""
    called: dict[str, None] = {}
    for node in root.children:
        call = node.children[0] if node.type == "expression_statement" and node.children else node
        if call.type == "macro_invocation":
            name = _node_name(call.child_by_field_name("macro"))
            if name in names:
                called[name] = None
    return list(called)


def _node_name(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""
