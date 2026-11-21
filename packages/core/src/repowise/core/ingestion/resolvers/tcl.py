"""Tcl ``source`` / ``package require`` import resolution.

A ``source`` path reaches this resolver with surrounding quotes stripped.

Handled forms:

1. Literal relative path (``scripts/x.tcl``, ``./x.tcl``) resolved against
   the sourcing file's directory, then the repo root.
2. Directory-anchored path (``$dir/x.tcl``, ``${SCRIPTS}/x.tcl``,
   ``[file dirname [info script]]/x.tcl``): leading variable or command
   substitution segments are stripped, the tail is tried relative to the
   importer, then by a *unique* multi-segment path suffix. A single bare
   filename after stripping is never matched by suffix.
3. ``package require Name`` links to the one repo file holding
   ``package provide Name``; otherwise it becomes an external node.
4. Anything still interpolated, absolute, or ``[file join ...]``-built is an
   external node, so the reference stays in the graph without a guessed edge.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from ..extractors.tcl import is_tcl_switch_arm
from ..extractors.tcl import unwrap_word as _unwrap_word

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..models import Import
    from .context import ResolverContext

# One leading variable anchor segment: ``$name/``, ``${name}/``, ``$name(key)/``,
# each optionally global (``$::dir/``, ``$::env(ROOT)/``).
_DIR_ANCHOR = re.compile(r"^(?:\$\{[^}]*\}|\$(?:::)?[A-Za-z_][\w:]*(?:\([^)]*\))?)/")
_DYNAMIC = re.compile(r"[$\[]")
_OPTION = re.compile(r"^-[A-Za-z]\w*\s+")
# ``source -encoding name``: the option's value is a word of its own.
_VALUED_OPTION = re.compile(r"^-encoding\s+\S+\s+")
_PACKAGE = re.compile(r"^[A-Za-z_][\w:.-]*$")
# The interpreter and toolkit themselves: a version check, not a dependency.
_RUNTIME_PACKAGES = frozenset({"Tcl", "Tk"})


def resolve_tcl_statement(imp: Import, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve *imp* as the command that wrote it: ``package require`` or ``source``.

    The same word can name a package or a file (``source setup``,
    ``package require util.tcl``), so the command decides, not the spelling.
    """
    command = imp.raw_statement.split(None, 1)[0] if imp.raw_statement.strip() else ""
    return resolve_tcl_import(imp.module_path, importer_path, ctx, is_package=command == "package")


def resolve_tcl_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
    *,
    is_package: bool | None = None,
) -> str | None:
    """Resolve a ``source`` / ``package require`` argument to a repo path.

    With no *is_package* (the command unknown), a bare name is read as a package.
    """
    raw = _without_options(module_path)
    if not raw:
        return None
    if is_package is None:
        is_package = _is_package_name(raw)
    if is_package:
        return _resolve_package_require(raw, ctx)
    return _resolve_source(raw, importer_path, ctx)


def _without_options(module_path: str) -> str:
    """The argument with leading options (``-encoding utf-8``, ``-echo``) and quotes removed."""
    raw = module_path.strip()
    while (option := _VALUED_OPTION.match(raw) or _OPTION.match(raw)) is not None:
        raw = raw[option.end() :]
    return raw.strip('"{} ')


def _is_package_name(raw: str) -> bool:
    return bool(_PACKAGE.match(raw)) and not raw.endswith(".tcl") and "/" not in raw


def _resolve_package_require(name: str, ctx: ResolverContext) -> str | None:
    if name in _RUNTIME_PACKAGES:
        return None
    return _resolve_package(name, ctx) or ctx.add_external_node(name)


def _anchor_stripped(raw: str) -> tuple[str, int]:
    """*raw* without its leading ``$dir/``-style anchors, and how many were stripped."""
    tail, stripped = raw, 0
    while (shorter := _strip_anchor(tail)) != tail:
        tail, stripped = shorter, stripped + 1
    return tail, stripped


def _resolve_source(raw: str, importer_path: str, ctx: ResolverContext) -> str | None:
    tail, stripped = _anchor_stripped(raw)
    if _DYNAMIC.search(tail) or tail.startswith(("~", "/")):
        return ctx.add_external_node(raw)
    return _source_candidate(tail, stripped, importer_path, ctx) or ctx.add_external_node(raw)


def _source_candidate(
    tail: str, stripped: int, importer_path: str, ctx: ResolverContext
) -> str | None:
    """The repo file *tail* names: importer-relative (or root-relative), else a unique suffix."""
    importer_dir = posixpath.dirname(importer_path)
    bases = (importer_dir,) if stripped else (importer_dir, "")
    for base in bases:
        cand = posixpath.normpath(posixpath.join(base, tail))
        if cand in ctx.path_set:
            return cand
    if not stripped or "/" not in tail:
        return None
    needle = f"/{tail}"
    hits = [p for p in ctx.sorted_paths if p == tail or p.endswith(needle)]
    return hits[0] if len(hits) == 1 else None


def _strip_anchor(path: str) -> str:
    """*path* without one leading ``$var/`` or ``[cmd ...]/`` segment."""
    if path.startswith("["):
        depth = 0
        for i, ch in enumerate(path):
            depth += (ch == "[") - (ch == "]")
            if depth == 0:
                return path[i + 2 :] if path[i + 1 : i + 2] == "/" else path
        return path
    return _DIR_ANCHOR.sub("", path, count=1)


def _resolve_package(name: str, ctx: ResolverContext) -> str | None:
    """The single Tcl file declaring ``package provide <name> <version>``."""
    hits = _package_providers(ctx).get(name, [])
    return hits[0] if len(hits) == 1 else None


def _package_providers(ctx: ResolverContext) -> dict[str, list[str]]:
    """``package provide`` name -> declaring files, built once per context."""
    cached = getattr(ctx, "_tcl_package_providers", None)
    if cached is not None:
        return cached
    providers: dict[str, list[str]] = {}
    for path in ctx.sorted_paths:
        if not _is_tcl_file(ctx, path):
            continue
        data = _read(ctx, path)
        if not data or b"provide" not in data:
            continue
        for name in _provided_packages(data):
            providers.setdefault(name, []).append(path)
    ctx._tcl_package_providers = providers  # type: ignore[attr-defined]
    return providers


def _provided_packages(data: bytes) -> set[str]:
    """Names a file's ``package provide name version`` commands declare.

    Read off the syntax tree, so a declaration in any script body counts
    (``namespace eval u { package provide u 1.0 }``) and text in a data word
    (``set help {..}``) does not. Without a version it only queries.
    """
    from tree_sitter import Parser

    from ..parser import _get_language  # local import: avoid a cycle at module load

    language = _get_language("tcl")
    if language is None:
        return set()
    names: set[str] = set()
    stack = [Parser(language).parse(data).root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if (name := _provide_name(node)) is not None:
            names.add(name)
    return names


def _provide_name(node: Node) -> str | None:
    """``name`` of a ``package provide name version`` command node, else None."""
    head = node.child_by_field_name("name") if node.type == "command" else None
    args = node.child_by_field_name("arguments") if head is not None else None
    if head is None or head.text != b"package" or args is None:
        return None
    words = args.named_children
    if len(words) < 3 or words[0].text != b"provide" or is_tcl_switch_arm(node):
        return None
    return _unwrap_word((words[1].text or b"").decode("utf-8", errors="replace")) or None


def _is_tcl_file(ctx: ResolverContext, path: str) -> bool:
    """Tcl as ingestion read it (a ``tclsh`` shebang counts), else by extension."""
    parsed = ctx.parsed_files.get(path) if ctx.parsed_files else None
    if parsed is not None:
        return parsed.file_info.language == "tcl"
    return path.lower().endswith(".tcl")


def _read(ctx: ResolverContext, path: str) -> bytes | None:
    if ctx.source_map is not None and path in ctx.source_map:
        return ctx.source_map[path]
    if ctx.repo_path is None:
        return None
    try:
        return (ctx.repo_path / path).read_bytes()
    except OSError:
        return None
