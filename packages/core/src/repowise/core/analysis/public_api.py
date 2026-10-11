"""The Public API of a package, computed from manifests and re-exports.

A package's public surface is what a consumer can import from it: whatever its
manifest names as the start (``package.json`` ``bin``/``main``/``exports["."]``,
``[project.scripts]``, a distribution's ``__init__.py``; stamped at ingestion as
``FileInfo.is_manifest_entry``) declares or re-exports through barrels. It is
read off the parsed imports, never guessed by a model, so a class that nothing
in the repo imports still appears when the package's front door publishes it.

Two readers share it: a wiki page's Public API section, and the per-symbol
``public_api`` flag the graph carries (:func:`stamp_public_api`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.ingestion.barrels import BARREL_FILENAMES
from repowise.core.ingestion.package_roots import module_for, package_roots_from_paths
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
            if b.local_name == "*" or b.is_module_alias:
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
    a computed ``__all__`` and an ``import`` followed by a bare ``export { X }``
    are not followed.
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


def public_symbols(
    parsed: Mapping[str, Any], entries: AbstractSet[str] = frozenset()
) -> dict[str, str]:
    """``{symbol id: the file that exports it}`` for every symbol consumers can import.

    Exported means a barrel or a package entry publishes it (re-exports and
    ``__all__`` followed as for :func:`compute_public_api`); a public method
    of an exported class is exported with it. *entries* are the files a
    ``package.json`` ``exports`` map names (:func:`export_roots`).
    """
    resolver = _Resolver(parsed)
    out: dict[str, str] = {}
    for root in export_roots(parsed, entries):
        for _name, sym, declared_in in resolver.exported(root):
            if sym is not None and not parsed[declared_in].file_info.is_test:
                out.setdefault(sym.id, root)
    for path, pf in parsed.items():
        for sym in pf.symbols:
            owner = out.get(f"{path}::{sym.parent_name}") if sym.parent_name else None
            if owner is not None and sym.visibility == "public":
                out.setdefault(sym.id, owner)
    return out


def stamp_public_api(graph: Any, parsed: Mapping[str, Any]) -> None:
    """Set ``public_api`` on every symbol node: the exporting file, or ``""``."""
    entries = {
        path for path in parsed if graph.nodes.get(path, {}).get("package_export")
    }
    exported = public_symbols(parsed, entries)
    for pf in parsed.values():
        for sym in pf.symbols:
            node = graph.nodes.get(sym.id)
            if node is not None:
                node["public_api"] = exported.get(sym.id, "")
