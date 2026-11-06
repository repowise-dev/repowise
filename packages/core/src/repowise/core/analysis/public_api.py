"""The Public API of a package, computed from manifests and re-exports.

A package's public surface is what a consumer can import from it: whatever its
manifest names as the start (``package.json`` ``bin``/``main``/``exports["."]``,
``[project.scripts]``, a distribution's ``__init__.py``; stamped at ingestion as
``FileInfo.is_manifest_entry``) declares or re-exports through barrels. It is
read off the parsed imports, never guessed by a model, so a class that nothing
in the repo imports still appears when the package's front door publishes it.
A .NET project that ships a package (:mod:`.published_packages`) has no front
door: every public type its C# files declare is published.

Two readers share it: a wiki page's Public API section, and the per-symbol
``public_api`` flag the graph carries (:func:`stamp_public_api`).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.ingestion.barrels import BARREL_FILENAMES
from repowise.core.ingestion.languages.python_modules import (
    build_python_module_index,
    dotted_module_for,
)
from repowise.core.ingestion.package_roots import module_for, package_roots_from_paths
from repowise.core.pyparse import parse_python
from repowise.core.workspace.code_api import dunder_all

# A Rust ``impl`` block is not a name anyone imports.
_UNPUBLISHED_KINDS = frozenset({"impl"})
# (public name, Symbol or None for a namespace export, declaring file)
_Published = list[tuple[str, Any, str]]


def api_roots(
    material: Iterable[str], parsed: Mapping[str, Any], package_roots: set[str]
) -> list[str]:
    """The files a page's public API is read from, per package it covers.

    Per package (or per page when no package holds the files): its
    manifest-declared entries, else its shallowest barrels.
    """
    by_owner: dict[str, list[str]] = {}
    for path in sorted(set(material)):
        if path in parsed:
            owner = module_for(path, package_roots)
            by_owner.setdefault(owner if owner in package_roots else "", []).append(path)
    roots: list[str] = []
    for _owner, paths in sorted(by_owner.items()):
        declared = [p for p in paths if getattr(parsed[p].file_info, "is_manifest_entry", False)]
        if not declared:
            barrels = [p for p in paths if PurePosixPath(p).name in BARREL_FILENAMES]
            depth = min((p.count("/") for p in barrels), default=0)
            declared = [p for p in barrels if p.count("/") == depth]
        roots.extend(declared)
    return roots


def _forwards(imp: Any, pf: Any) -> bool:
    """Whether *imp* publishes what it imports (a re-export), not just uses it."""
    if getattr(imp, "deferred", False):
        return False
    if imp.is_reexport or imp.raw_statement.lstrip().startswith("export"):
        return True
    # A Python package's ``__init__`` imports are its published namespace.
    return pf.file_info.path.rsplit("/", 1)[-1] == "__init__.py"


def _declared(pf: Any) -> dict[str, Any]:
    """The public top-level symbols *pf* declares, by name."""
    # Declarations sort first so an overload's implementation wins the name.
    return {
        s.name: s
        for s in sorted(pf.symbols, key=lambda s: not s.is_declaration)
        if s.parent_name is None
        and s.visibility == "public"
        and s.kind not in _UNPUBLISHED_KINDS
        and s.name != "__all__"
    }


def _string_literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_lazy_map(node: ast.AST | None) -> dict[str, tuple[str, str | None]]:
    """Read one conservative ``__getattr__`` map, or nothing if it is computed."""
    if not isinstance(node, ast.Dict) or len(node.keys) != len(node.values):
        return {}
    result: dict[str, tuple[str, str | None]] = {}
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        key = _string_literal(key_node)
        if key is None:
            return {}
        module: str | None = None
        attr: str | None = None
        if isinstance(value_node, ast.Tuple) and len(value_node.elts) == 2:
            module = _string_literal(value_node.elts[0])
            attr = _string_literal(value_node.elts[1])
        else:
            module = _string_literal(value_node)
            attr = key
        if not module or not attr:
            return {}
        result[key] = (module, attr)
    return result


def _module_lazy_maps(tree: ast.Module) -> list[dict[str, tuple[str, str | None]]]:
    """Find literal maps read by a module-level ``__getattr__`` function."""
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
    ]
    if not functions:
        return []
    loads = {
        node.id
        for function in functions
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    effective: dict[str, dict[str, tuple[str, str | None]]] = {}
    statements = [*tree.body, *(statement for function in functions for statement in function.body)]
    for statement in statements:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            continue
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        lazy_map = _literal_lazy_map(value)
        for name in names & loads:
            # An empty result also records a computed or empty later assignment,
            # so an earlier literal map cannot survive a rebinding.
            effective[name] = lazy_map
    return [lazy_map for lazy_map in effective.values() if lazy_map]


def _lazy_map_from(
    path: str,
    paths: set[str],
    module_index: Mapping[str, str],
    source_path: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return ``(public_name, target_module, target_name)`` lazy exports."""
    try:
        source = Path(source_path or path).read_text(encoding="utf-8", errors="replace")
        tree = parse_python(source)
    except (OSError, SyntaxError, ValueError):
        return []
    current = dotted_module_for(path, paths)
    if not current:
        return []
    package = current if path.rsplit("/", 1)[-1] == "__init__.py" else current.rsplit(".", 1)[0]
    package_parts = package.split(".") if package else []
    exports: list[tuple[str, str, str]] = []
    for lazy_map in _module_lazy_maps(tree):
        for public_name, (module_name, target_name) in lazy_map.items():
            if module_name.startswith("."):
                level = len(module_name) - len(module_name.lstrip("."))
                if level > len(package_parts) + 1:
                    continue
                base = package_parts[: len(package_parts) - (level - 1)]
                tail = module_name.lstrip(".")
                resolved_module = ".".join((*base, tail)) if tail else ".".join(base)
            else:
                resolved_module = module_name
            target_path = module_index.get(resolved_module)
            if target_path and target_name:
                exports.append((public_name, target_path, target_name))
    return exports


def _entry(name: str, sym: Any, path: str, alias_of: str) -> dict:
    """One Public API row; a second name for a symbol carries no excerpt."""
    if sym is None:  # a namespace export
        return {
            "name": name,
            "id": "",
            "kind": "module",
            "file": path,
            "signature": "",
            "doc": "",
            "alias_of": alias_of,
        }
    doc = (sym.docstring or "").strip().splitlines()
    return {
        "name": name,
        "id": sym.id,
        "kind": sym.kind,
        "file": path,
        "signature": "" if alias_of else " ".join((sym.signature or "").split()),
        "doc": "" if alias_of or not doc else doc[0].strip(),
        "alias_of": alias_of,
    }


class _Resolver:
    def __init__(self, parsed: Mapping[str, Any]) -> None:
        self._parsed = parsed
        self._memo: dict[str, _Published] = {}
        self._visiting: set[str] = set()
        self._paths = set(parsed)
        self._module_index = build_python_module_index(self._paths)
        # Set when a cycle cut a walk short; such partial results are not memoized.
        self._cut = False

    def published(self, path: str) -> _Published:
        """Every public name *path* binds: its own declarations, then its re-exports.

        What a named import can reach; :meth:`exported` narrows it to what
        the module publishes.
        """
        if path in self._memo:
            return self._memo[path]
        pf = self._parsed.get(path)
        if pf is None:
            return []
        if path in self._visiting:
            self._cut = True
            return []
        self._visiting.add(path)
        outer_cut, self._cut = self._cut, False
        own = _declared(pf)
        out: _Published = [(name, sym, path) for name, sym in own.items()]
        # ``export { local as alias }`` publishes a declared symbol under a new name.
        out += [
            (alias, own[local], path) for alias, local in pf.export_aliases.items() if local in own
        ]
        for imp in pf.imports:
            if _forwards(imp, pf) and imp.resolved_file in self._parsed:
                out += self._forwarded(imp)
        lazy_exports = (
            _lazy_map_from(
                path,
                self._paths,
                self._module_index,
                getattr(pf.file_info, "abs_path", None),
            )
            if pf.file_info.language == "python"
            else []
        )
        published_names = {name for name, _sym, _path in out}
        # A Python module alias binds its local name at import time, even
        # though the existing eager-export reader does not publish a module
        # namespace row for it.  It must still win over a same-named lazy
        # fallback.  Imports inside a deferred function are excluded by
        # ``_forwards`` above.
        if pf.file_info.language == "python":
            published_names.update(
                binding.local_name
                for imp in pf.imports
                if _forwards(imp, pf) and imp.resolved_file in self._parsed
                for binding in imp.bindings
                if binding.local_name != "*"
            )
        for public_name, target_path, target_name in lazy_exports:
            if public_name in published_names:
                continue
            hit = next(
                (r for r in self.published(target_path) if r[0] == target_name),
                None,
            )
            if hit is not None:
                out.append((public_name, hit[1], hit[2]))
                published_names.add(public_name)
        self._visiting.discard(path)
        if not self._cut:
            self._memo[path] = out
        self._cut = self._cut or outer_cut
        return out

    def exported(self, path: str) -> _Published:
        """What *path* publishes: a Python ``__all__`` when it has one, else no ``_`` names."""
        out = self.published(path)
        pf = self._parsed.get(path)
        if pf is None or pf.file_info.language != "python":
            return out
        listed = dunder_all(Path(pf.file_info.abs_path)) if pf.file_info.abs_path else None
        if listed is None:
            return [r for r in out if not r[0].startswith("_")]
        return [r for r in out if r[0] in listed]

    def _forwarded(self, imp: Any) -> _Published:
        if "*" in imp.imported_names:
            # ``export * as ns`` publishes the module itself under ``ns``.
            ns = next((b.exported_name for b in imp.bindings if b.local_name == "*"), None)
            if ns:
                return [(ns, None, imp.resolved_file)]
            return list(self.exported(imp.resolved_file))
        out: _Published = []
        for b in imp.bindings:
            if b.local_name == "*":
                continue
            if b.is_module_alias:
                continue
            wanted = b.exported_name or b.local_name
            hit = next(
                (r for r in self.published(b.source_file or imp.resolved_file) if r[0] == wanted),
                None,
            )
            if hit is not None:
                out.append((b.local_name, hit[1], hit[2]))
        return out


def compute_public_api(
    material: Iterable[str], parsed: Mapping[str, Any], package_roots: set[str]
) -> list[dict]:
    """``[{name, id, kind, file, signature, doc}]`` a page's files publish, entry order.

    A symbol published under a second name (``export { A as B }``) is listed
    again with ``alias_of`` and no excerpt. Ceiling: ``export { default as X }``,
    computed ``__all__`` values, computed lazy-export maps, and an ``import``
    followed by a bare ``export { X }`` are not followed.
    """
    resolver = _Resolver(parsed)
    first_name: dict[tuple[str, str], str] = {}  # (file, symbol) -> first public name
    seen: set[tuple[str, str, str]] = set()
    api: list[dict] = []
    for root in api_roots(material, parsed, package_roots):
        for name, sym, path in resolver.exported(root):
            key = (path, sym.name if sym is not None else "")
            if (name, *key) in seen:
                continue
            seen.add((name, *key))
            first = first_name.setdefault(key, name)
            api.append(_entry(name, sym, path, "" if first == name else first))
    return api


def _is_declared_entry(pf: Any, entries: AbstractSet[str]) -> bool:
    """Where a manifest says its package starts: a distribution's ``__init__.py``,
    a ``package.json`` entry or ``exports`` subpath (*entries*). A Python
    module a console script runs is not one: nothing imports from it."""
    info = pf.file_info
    if info.path in entries:
        return True
    if not getattr(info, "is_manifest_entry", False):
        return False
    return info.language != "python" or PurePosixPath(info.path).name == "__init__.py"


def _is_launcher(pf: Any) -> bool:
    """A Python module a console script runs, which makes its package an application."""
    info = pf.file_info
    return (
        bool(getattr(info, "is_manifest_entry", False))
        and info.language == "python"
        and PurePosixPath(info.path).name != "__init__.py"
    )


def _imported_from_other_packages(
    parsed: Mapping[str, Any], owner: Mapping[str, str | None]
) -> set[str]:
    """Files a non-test file of another package imports."""
    out: set[str] = set()
    for path, pf in parsed.items():
        if pf.file_info.is_test:
            continue
        for imp in pf.imports:
            target = imp.resolved_file
            if target in owner and owner[target] != owner[path]:
                out.add(target)
    return out


def export_roots(parsed: Mapping[str, Any], entries: AbstractSet[str] = frozenset()) -> list[str]:
    """The files whose exports reach consumers, never a test file.

    A manifest-declared entry always is. A barrel is when another package in
    the repository imports through it, or when its package has no consumer
    here and runs no console script: a library whose consumers live outside.
    A barrel only its own package reads, in a package other packages consume
    or in an application, is internal.
    """
    packages = package_roots_from_paths(set(parsed))
    owner = {path: module_for(path, packages) for path in parsed}
    imported = _imported_from_other_packages(parsed, owner)
    closed = {owner[path] for path in imported}
    closed |= {owner[path] for path, pf in parsed.items() if _is_launcher(pf)}
    roots: list[str] = []
    for path, pf in sorted(parsed.items()):
        if pf.file_info.is_test:
            continue
        if _is_declared_entry(pf, entries) or (PurePosixPath(path).name in BARREL_FILENAMES and (
            path in imported or owner[path] not in closed
        )):
            roots.append(path)
    return roots


def _sibling_owner(
    path: str,
    parent_name: str,
    out: Mapping[str, str],
    roots: AbstractSet[str],
    package_files: Mapping[str, Sequence[str]],
) -> str | None:
    """The export root of a type declared in another file of *path*'s package.

    Go splits a type's methods across the package's files; the receiver type
    is looked up in each sibling file. The method's own file is its root when
    it is one, else the type's.
    """
    for sibling in package_files.get(path, ()):
        if sibling == path:
            continue
        owner = out.get(f"{sibling}::{parent_name}")
        if owner is not None:
            return path if path in roots else owner
    return None


def _enclosing_ruby_owner(
    sym: Any, out: Mapping[str, str], containers: Sequence[Any]
) -> str | None:
    """The export root of the Ruby module or class that encloses *sym* in its file.

    A Ruby symbol's id names only its immediate parent, so a method of
    ``Mygem::Client`` is ``<file>::Client::get`` while the class is
    ``<file>::Mygem::Client``, and ``<file>::<parent>`` is not a symbol. The
    enclosing container is found by name and extent instead: the innermost one
    whose lines hold *sym*, which also tells two ``Client`` classes in different
    namespaces apart.
    """
    parent = (sym.parent_name or "").rsplit("::", 1)[-1]
    best = None
    for container in containers:
        if (
            container.name == parent
            and container is not sym
            and container.start_line <= sym.start_line
            and container.end_line >= sym.end_line
            and (
                best is None
                or container.end_line - container.start_line < best.end_line - best.start_line
            )
        ):
            best = container
    return out.get(best.id) if best is not None else None


def _local_id(sym: Any, path: str) -> str:
    """A top-level symbol's id within its file (``Box`1`` for a generic ``Box<T>``)."""
    return sym.id[len(path) + 2 :]


def _publish_csharp_types(
    parsed: Mapping[str, Any], roots: Iterable[str], out: dict[str, str]
) -> None:
    """Publish every public top-level type a C# file of a shipped project declares.

    :func:`_declared` keys a file's symbols by name, so ``Box<T>`` and ``Box``
    declared together would leave one of them out; C# tells them apart by
    type-parameter count, which the symbol id carries.
    """
    for root in roots:
        pf = parsed[root]
        if pf.file_info.language != "csharp":
            continue
        for sym in pf.symbols:
            if (
                sym.parent_name is None
                and sym.visibility == "public"
                and sym.kind not in _UNPUBLISHED_KINDS
            ):
                out.setdefault(sym.id, root)


def _publish_partial_fragments(
    parsed: Mapping[str, Any],
    fragments: Mapping[str, AbstractSet[str]],
    out: dict[str, str],
) -> None:
    """Publish the ``partial`` fragments of a published C# type.

    Every fragment of a partial type has the accessibility of the one that
    declares it, but a fragment written without a modifier reads ``internal``,
    so its type and members would be missed. *fragments* maps a C# file to the
    files holding the other fragments of its partial types. A fragment is
    published when the same type, in one of those files, already is.
    """
    for path, others in sorted(fragments.items()):
        pf = parsed.get(path)
        if pf is None or pf.file_info.is_test:
            continue
        for sym in pf.symbols:
            if sym.parent_name is not None or sym.id in out or "partial" not in sym.modifiers:
                continue
            local = _local_id(sym, path)
            for other in sorted(others):
                twin = next((s for s in parsed[other].symbols if s.id == f"{other}::{local}"), None)
                if twin is not None and "partial" in twin.modifiers and twin.id in out:
                    out[sym.id] = path
                    break


def public_symbols(
    parsed: Mapping[str, Any],
    entries: AbstractSet[str] = frozenset(),
    package_files: Mapping[str, Sequence[str]] | None = None,
    fragments: Mapping[str, AbstractSet[str]] | None = None,
) -> dict[str, str]:
    """``{symbol id: the file that exports it}`` for every symbol consumers can import.

    Exported means a barrel or a package entry publishes it (re-exports and
    ``__all__`` followed as for :func:`compute_public_api`); a public method
    of an exported class is exported with it. *entries* are the files a
    ``package.json`` ``exports`` map names or a shipped .NET project holds
    (:func:`export_roots`). *package_files* maps a Go file to the files of its
    package, so a method declared apart from its receiver type is exported
    with that type. *fragments* are the C# files that share a ``partial``
    type (:func:`_publish_partial_fragments`).
    """
    resolver = _Resolver(parsed)
    out: dict[str, str] = {}
    roots = export_roots(parsed, entries)
    for root in roots:
        for _name, sym, declared_in in resolver.exported(root):
            if sym is not None and not parsed[declared_in].file_info.is_test:
                out.setdefault(sym.id, root)

    root_set = set(roots)
    _publish_csharp_types(parsed, roots, out)
    if fragments:
        _publish_partial_fragments(parsed, fragments, out)

    for path, pf in parsed.items():
        symbols: Sequence[Any] = pf.symbols
        containers: list[Any] = []
        if pf.file_info.language == "ruby":
            # Parents before their members, so a nested class is exported
            # before the methods it holds are looked up.
            symbols = sorted(pf.symbols, key=lambda s: (s.start_line, -s.end_line))
            containers = [s for s in symbols if s.kind in ("class", "module")]
        for sym in symbols:
            owner = None
            if containers and sym.parent_name:
                owner = _enclosing_ruby_owner(sym, out, containers)
            if owner is None and sym.parent_name:
                owner = out.get(f"{path}::{sym.parent_name}")
            if owner is None and sym.parent_name and package_files:
                owner = _sibling_owner(path, sym.parent_name, out, root_set, package_files)
            if owner is not None and sym.visibility == "public":
                out.setdefault(sym.id, owner)
    return out


def _partial_fragments(graph: Any, parsed: Mapping[str, Any]) -> dict[str, set[str]]:
    """``{C# file: the files holding other fragments of its partial types}``.

    The graph links the files of one partial type with ``partial_class``
    import edges, in both directions.
    """
    out: dict[str, set[str]] = {}
    for path, pf in parsed.items():
        if pf.file_info.language != "csharp" or path not in graph:
            continue
        for _u, other, data in graph.out_edges(path, data=True):
            if data.get("hint_source") == "partial_class" and other in parsed:
                out.setdefault(path, set()).add(other)
    return out


def _ruby_gem_files(graph: Any, parsed: Mapping[str, Any]) -> set[str]:
    """The files a gem publishes: its entry and what that requires inside its own ``lib``.

    The Ruby warmup stamps each gem's ``lib/<name>.rb`` with the ``lib`` root it
    sits in (``ruby_gem_lib``). Requires are not resolved at warmup time, so the
    closure is taken here over ``Import.resolved_file``. A file nothing in the
    gem requires is not part of its surface, nor is a test file or anything
    another gem's ``lib`` holds.
    """
    out: set[str] = set()
    for entry in sorted(parsed):
        lib_root = graph.nodes.get(entry, {}).get("ruby_gem_lib")
        if not lib_root:
            continue
        prefix = f"{lib_root}/"
        reached = {entry}
        pending = [entry]
        while pending:
            for imp in parsed[pending.pop()].imports:
                target = imp.resolved_file
                if (
                    target in reached
                    or target not in parsed
                    or not target.startswith(prefix)
                    or parsed[target].file_info.is_test
                ):
                    continue
                reached.add(target)
                pending.append(target)
        out |= reached
    return out


def stamp_public_api(
    graph: Any,
    parsed: Mapping[str, Any],
    package_files: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Set ``public_api`` on every symbol node: the exporting file, or ``""``."""
    entries = {
        path for path in parsed if graph.nodes.get(path, {}).get("package_export")
    }
    entries |= _ruby_gem_files(graph, parsed)
    exported = public_symbols(parsed, entries, package_files, _partial_fragments(graph, parsed))
    for pf in parsed.values():
        for sym in pf.symbols:
            node = graph.nodes.get(sym.id)
            if node is not None:
                node["public_api"] = exported.get(sym.id, "")
