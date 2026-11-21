"""Rust path-qualified calls mixed into ``CallResolver``.

A Rust call can name its callee by a module path with no ``use`` at all:
``crate::index::open_ro()``, ``super::util::helper()``, ``self::f()``, or a
child module the file declares (``mod index;`` then ``index::open_ro()``).
The path is walked down the module tree, one segment per module file, and the
callee is looked up in the module it ends on; a last segment that is no module
is read as a type, whose method the call names (``index::Store::open()``).

An inline ``mod name { ... }`` is no module file: a path through one stays in
the file that holds it, and the callee is looked up among that block's items
(``outer::inner::f()``, ``crate::a::m::Type::new()``).

Ceilings: a ``mod x;`` declared inside an inline block is not followed; a
``#[path]`` module is found only through its ``mod`` declaration; a crate with
both ``lib.rs`` and ``main.rs`` answers ``crate::f()`` from ``lib.rs``. A child module with no
``mod`` declaration is still found by its file, as import resolution does.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import CallSite, Symbol
from .resolved_call import ResolvedCall
from .resolvers.rust import (
    _crate_root_file,
    _find_rust_crate_root_cached,
    _module_files_of_dir,
    _probe_rust_path,
    _rust_module_dir,
)


class RustPathCallsMixin:
    def _resolve_rust_module_path(
        self, file_path: str, call: CallSite, caller_id: str
    ) -> ResolvedCall | None:
        """A call through a module path: ``crate::a::f()``, ``super::f()``, ``m::T::new()``."""
        receiver = call.receiver_name or ""
        segments = receiver.split("::")
        if not all(s.isidentifier() for s in segments):
            return None  # ``<T as X>::m``, ``Vec::<u8>::new``: no module path
        # ``x.m()`` arrives with the same receiver as ``x::m()``: a lone segment
        # is a path only where the line spells it as one.
        if len(segments) == 1 and not self._rust_spelled_as_path(file_path, call):
            return None
        start = self._rust_path_start(file_path, segments, call.line)
        if start is None:
            return None
        module, rest = start
        # The inline ``mod`` block the walk stands in, None at a file's top level.
        scope = self._rust_start_scope(file_path, segments, call.line) if module == file_path else None
        for i, segment in enumerate(rest):
            child = None if scope else self._rust_child_module(module, segment)
            if child is not None:
                module = child
                continue
            if self._rust_inline_module(module, segment, scope):
                if self._rust_name_reused(module, segment):
                    # Items of two blocks named alike share one key: no pick.
                    return None
                scope = segment
                continue
            if i < len(rest) - 1:
                return None
            # The last segment is no module: a type the call names a method of.
            if scope is not None and self._rust_type_reused(module, segment):
                return None  # ``a::S::new()`` and ``b::S::new()`` share one key
            target = self._file_methods.get(module, {}).get((segment, call.target_name))  # type: ignore[attr-defined]
            return self._rust_path_edge(caller_id, target, call)
        if scope is not None:
            # An item of an inline block is keyed by the block's name.
            # ``self::f()`` in a block names the block's ``f``, never the file's.
            target = self._file_methods.get(module, {}).get((scope, call.target_name))  # type: ignore[attr-defined]
            return self._rust_path_edge(caller_id, target, call)
        hit = self._module_alias_call(file_path, module, call, caller_id)  # type: ignore[attr-defined]
        if hit is not None:
            return hit if hit.callee_id != caller_id else None
        if module == file_path:
            # ``self::f()`` / ``super::f()`` inside an inline module: the item
            # sits in this file, below its top level.
            return self._rust_path_edge(
                caller_id, self._published(file_path, call.target_name), call  # type: ignore[attr-defined]
            )
        return None

    @staticmethod
    def _rust_path_edge(caller_id: str, target: str | None, call: CallSite) -> ResolvedCall | None:
        if target is None or target == caller_id:
            return None
        return ResolvedCall(caller_id, target, 0.88, call.line, "module_alias")

    def _rust_spelled_as_path(self, file_path: str, call: CallSite) -> bool:
        lines = self._lines_of(file_path)  # type: ignore[attr-defined]
        if not 0 < call.line <= len(lines):
            return False
        head, target = re.escape(call.receiver_name or ""), re.escape(call.target_name)
        return re.search(rf"(?<![\w.]){head}\s*::\s*{target}\b", lines[call.line - 1]) is not None

    def _rust_path_start(
        self, file_path: str, segments: list[str], line: int
    ) -> tuple[str, list[str]] | None:
        """``(module file, segments left)`` the path's first segments name."""
        head = segments[0]
        if head == "crate":
            crate_dir = self._rust_crate_dir(file_path)
            inside = PurePosixPath(file_path).relative_to(crate_dir).parts
            if len(inside) > 1 and inside[0] == "bin":
                # A ``src/bin`` target is its own crate: ``bin/x.rs`` or ``bin/x/main.rs``.
                bin_dir = PurePosixPath(crate_dir, "bin")
                root = bin_dir / inside[1] if len(inside) == 2 else bin_dir / inside[1] / "main.rs"
                return (root.as_posix(), segments[1:]) if root.as_posix() in self._parsed_files else None  # type: ignore[attr-defined]
            return self._rust_root_module(crate_dir, segments[1:])
        if head == "self":
            return file_path, segments[1:]
        if head == "super":
            hops = 0
            while hops < len(segments) and segments[hops] == "super":
                hops += 1
            # A ``super`` out of an inline ``mod`` block stays in this file.
            climb = hops - min(hops, len(self._rust_enclosing_modules(file_path, line)))
            if not climb:
                return file_path, segments[hops:]
            module_dir = PurePosixPath(_rust_module_dir(file_path))
            for _ in range(climb):
                module_dir = module_dir.parent
            module = next(
                (
                    f
                    for f in _module_files_of_dir(module_dir.as_posix())
                    if f in self._parsed_files and f != file_path  # type: ignore[attr-defined]
                ),
                None,
            )
            return (module, segments[hops:]) if module else None
        used = self._import_names.get(file_path, {}).get(head)  # type: ignore[attr-defined]
        if used is not None:
            # A ``use``d name alone is the module-alias tier's; one from outside
            # the repository names nothing here.
            if len(segments) == 1 or used.startswith("external:"):
                return None
            return used, segments[1:]
        child = self._rust_child_module(file_path, head)
        if child is not None:
            return child, segments[1:]
        enclosing = self._rust_enclosing_modules(file_path, line)
        if self._rust_inline_module(file_path, head, enclosing[-1] if enclosing else None):
            return file_path, segments  # the walk enters the inline block
        if len(segments) == 1:
            return None  # a type, or an external crate: other tiers answer
        crate_src = self._get_rust_crate_src().get(head)  # type: ignore[attr-defined]
        return self._rust_root_module(crate_src, segments[1:]) if crate_src else None

    def _rust_root_module(self, crate_dir: str, rest: list[str]) -> tuple[str, list[str]] | None:
        root_file = _crate_root_file(crate_dir, self._parsed_files)  # type: ignore[attr-defined]
        return (root_file, rest) if root_file else None

    def _rust_keys(self) -> frozenset[str]:
        keys: frozenset[str] | None = getattr(self, "_rust_parsed_keys", None)
        if keys is None:
            keys = self._rust_parsed_keys = frozenset(self._parsed_files)  # type: ignore[attr-defined]
        return keys

    def _rust_crate_dir(self, file_path: str) -> str:
        return _find_rust_crate_root_cached(file_path, self._rust_keys())

    def _rust_child_module(self, module: str, name: str) -> str | None:
        """The file of child module *name* of the module *module* defines."""
        parsed = self._parsed_files.get(module)  # type: ignore[attr-defined]
        if parsed is None or parsed.file_info.language != "rust":
            return None
        for imp in parsed.imports:
            if imp.is_module_declaration and imp.module_path == name and imp.resolved_file:
                found = imp.resolved_file
                return found if found in self._parsed_files and found != module else None  # type: ignore[attr-defined]
        found = _probe_rust_path(_rust_module_dir(module), [name], self._rust_keys())
        return found if found != module else None

    def _rust_modules(self, file_path: str) -> list[Symbol]:
        """The ``mod`` symbols of *file_path* in source order, read once per file."""
        cache: dict[str, list[Symbol]] | None = getattr(self, "_rust_modules_cache", None)
        if cache is None:
            cache = self._rust_modules_cache = {}
        found = cache.get(file_path)
        if found is None:
            parsed = self._parsed_files.get(file_path)  # type: ignore[attr-defined]
            symbols = parsed.symbols if parsed is not None else []
            found = cache[file_path] = sorted(
                (sym for sym in symbols if sym.kind == "module"), key=lambda sym: sym.start_line
            )
        return found

    def _rust_enclosing_modules(self, file_path: str, line: int) -> list[str]:
        """The inline ``mod name { ... }`` blocks that hold *line*, outermost first."""
        # A ``mod name;`` line holds no call, so any module spanning the line is inline.
        return [
            sym.name
            for sym in self._rust_modules(file_path)
            if sym.start_line <= line <= sym.end_line
        ]

    def _rust_start_scope(self, file_path: str, segments: list[str], line: int) -> str | None:
        """The inline block a path read in its own file starts from.

        ``crate::`` starts at the top level; ``self::`` and a bare name at the
        block the call sits in; each ``super`` one block further out.
        """
        if segments[0] == "crate":
            return None
        enclosing = self._rust_enclosing_modules(file_path, line)
        hops = 0
        while hops < len(segments) and segments[hops] == "super":
            hops += 1
        depth = len(enclosing) - hops
        return enclosing[depth - 1] if depth > 0 else None

    def _rust_inline_module(self, module: str, name: str, parent: str | None = None) -> bool:
        """Whether *module* holds a ``mod name`` block directly inside block *parent*."""
        return any(
            sym.name == name and sym.parent_name == parent for sym in self._rust_modules(module)
        )

    def _rust_type_reused(self, module: str, name: str) -> bool:
        """Whether *module* declares a type *name* in more than one block."""
        parsed = self._parsed_files.get(module)  # type: ignore[attr-defined]
        owners = {
            sym.parent_name
            for sym in (parsed.symbols if parsed is not None else [])
            if sym.name == name and sym.kind not in ("function", "method", "module")
        }
        return len(owners) > 1

    def _rust_name_reused(self, module: str, name: str) -> bool:
        """Whether two ``mod name`` blocks of *module* sit in different parents."""
        return len({sym.parent_name for sym in self._rust_modules(module) if sym.name == name}) > 1

    def _rust_block_item(self, file_path: str, line: int, name: str) -> str | None:
        """The item *name* the innermost inline ``mod`` block holding *line* declares.

        A bare name in a block names the block's own item first; the file's
        top level is reached only through ``use super::...``.
        """
        enclosing = self._rust_enclosing_modules(file_path, line)
        if not enclosing or self._rust_name_reused(file_path, enclosing[-1]):
            return None
        return self._file_methods.get(file_path, {}).get((enclosing[-1], name))  # type: ignore[attr-defined]
