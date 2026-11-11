"""A Spring Data repository or MyBatis mapper call on each iteration of a loop.

The Java sink lexicon (:mod:`.dialects.java`) reads call names, so it misses
mapper verbs (``insert``, ``selectByPrimaryKey``) and cannot tell a repository
from a map. Here the receiver's declared type decides: a field, parameter or
local typed to an interface the repo's own facts prove is persistence, through
its ``extends`` chain to a Spring Data base, a MyBatis ``@Mapper``, or a
``@MapperScan`` package. Hits are same-function ``io_in_loop`` (boundary
``db``); a line the walker already flagged is left to it. Java only.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog

from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name
from ..complexity.models import PerfHit
from ..complexity.perf_walk import _loop_key_feeds, _sink_call_text
from .dialects import PERF_DIALECTS
from .io_boundaries import collect_io_names
from .loop_facts import LoopFacts, LoopSource
from .reduction_idioms import walk

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tree_sitter import Node

    from ..complexity.models import FileComplexity
    from ..source_reader import SourceReader
    from .dialects.base import BasePerfDialect

log = structlog.get_logger(__name__)

_SPRING_DATA = "org.springframework.data."
_SPRING_DATA_BASES = frozenset(
    {
        "Repository", "CrudRepository", "ListCrudRepository", "PagingAndSortingRepository",
        "ListPagingAndSortingRepository", "JpaRepository", "MongoRepository",
        "ElasticsearchRepository", "JpaSpecificationExecutor", "QuerydslPredicateExecutor",
    }
)
_MYBATIS = ("org.apache.ibatis.", "org.mybatis.")
_MAPPER_SCAN = re.compile(r"@MapperScan\s*\(([^)]*)\)")
_QUOTED = re.compile(r'"([\w.]+)"')
_MAPPER = re.compile(r"@Mapper\b")
# A plain or package-qualified type; generics (``List<FooMapper>``) and arrays are not the receiver's type.
_PLAIN_TYPE = re.compile(r"[\w.]+")
_SKIP_SCOPES = frozenset({"class_body", "interface_body", "enum_body"})
# Enhanced-for only: the shape measured on the Spring corpus. Counted ``for`` and
# ``forEach`` lambdas over the same rows are the same cost; add them once measured.
_LOOP = "enhanced_for_statement"


# -- which interfaces are persistence -------------------------------------------


def _imported_from(pf: Any, name: str, prefixes: tuple[str, ...]) -> bool:
    """*name* is bound in *pf* by an import from a package under *prefixes*."""
    for imp in pf.imports:
        path = imp.module_path or ""
        if not path.startswith(prefixes):
            continue
        if path.endswith("." + name) or path.endswith(".*"):
            return True
    return False


def _folder(path: str) -> str:
    return "/" + str(PurePosixPath(path).parent)


def _resolves_to(pf: Any, name: str, decl_path: str) -> bool:
    """Whether *name* in *pf* means the repo's one declaration at *decl_path*, in
    Java's order: a single-type import, then the same package, then an on-demand
    import. A name imported from anywhere else is another type."""
    single = [
        imp.module_path for imp in pf.imports
        if (imp.module_path or "").endswith("." + name)
    ]
    if single:
        target = "/" + decl_path.rsplit(".", 1)[0]
        return any(target.endswith("/" + fqn.replace(".", "/")) for fqn in single)
    folder = _folder(decl_path)
    if _folder(pf.file_info.path) == folder:
        return True
    return any(
        folder.endswith("/" + imp.module_path[:-2].replace(".", "/"))
        for imp in pf.imports
        if (imp.module_path or "").endswith(".*")
    )


def _scan_packages(pf: Any) -> set[str]:
    """Package directories (``com/acme/mapper``) a ``@MapperScan`` in *pf* names."""
    if not _imported_from(pf, "MapperScan", _MYBATIS):
        return set()
    found: set[str] = set()
    for sym in pf.symbols:
        for deco in sym.decorators:
            for args in _MAPPER_SCAN.findall(deco):
                found.update(p.replace(".", "/") for p in _QUOTED.findall(args))
    return found


def _in_package(path: str, packages: set[str]) -> bool:
    folder = "/" + str(PurePosixPath(path).parent) + "/"
    return any(f"/{pkg}/" in folder for pkg in packages)


def _seed_interface(pf: Any, sym: Any, parents: list[str], packages: set[str]) -> bool:
    if any(p in _SPRING_DATA_BASES and _imported_from(pf, p, (_SPRING_DATA,)) for p in parents):
        return True
    mapper = any(_MAPPER.search(d) for d in sym.decorators)
    if mapper and _imported_from(pf, "Mapper", _MYBATIS):
        return True
    return _in_package(pf.file_info.path, packages)


def persistence_types(parsed_files: Iterable[Any]) -> frozenset[str]:
    """Simple names of the repo's interfaces proven to be repositories or mappers.

    A simple name declared by more than one type is never trusted, as a receiver
    type or as a base others inherit from: it could mean either declaration.
    """
    java = [pf for pf in parsed_files if pf.file_info.language == "java"]
    packages = {pkg for pf in java for pkg in _scan_packages(pf)}
    # (child, its file, the parent names it extends) per interface declaration.
    interfaces: list[tuple[str, Any, list[str]]] = []
    declared_in: dict[str, list[str]] = {}
    typed: set[str] = set()
    for pf in java:
        parents: dict[str, list[str]] = {}
        for h in pf.heritage:
            parents.setdefault(h.child_name, []).append(h.parent_name)
        for sym in pf.symbols:
            if sym.kind not in ("class", "interface", "enum"):
                continue
            declared_in.setdefault(sym.name, []).append(pf.file_info.path)
            if sym.kind != "interface":
                continue
            own = parents.get(sym.name, [])
            interfaces.append((sym.name, pf, own))
            if _seed_interface(pf, sym, own, packages):
                typed.add(sym.name)
    unique = {name: paths[0] for name, paths in declared_in.items() if len(paths) == 1}
    typed &= set(unique)
    edges = [
        (child, parent)
        for child, pf, own in interfaces
        if child in unique
        for parent in own
        if parent in unique and _resolves_to(pf, parent, unique[parent])
    ]
    grew = True
    while grew:
        grew = False
        for child, parent in edges:
            if child not in typed and parent in typed:
                typed.add(child)
                grew = True
    return frozenset(typed)


# -- per-file scan -----------------------------------------------------------------


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


@cache
def _declared_types() -> Any:
    """The Java dataflow dialect's declared-type reader, with fields added: the
    receiver is usually an injected field."""
    from ..dataflow.dialects.base import DeclaredTypes  # deferred: dataflow imports perf

    return DeclaredTypes(
        frozenset({"formal_parameter", "local_variable_declaration", "field_declaration"}),
        frozenset({"variable_declarator"}),
    )


def _declared(node: Node) -> list[tuple[str, str]]:
    """``(name, simple type)`` for each name a field, local or parameter declares."""
    if node.type == "formal_parameter":
        names = [node.child_by_field_name("name")]
    else:
        names = [d.child_by_field_name("name") for d in node.children if d.type == "variable_declarator"]
    out = []
    for name in names:
        typ = _declared_types().of(name) if name is not None else None
        plain = typ is not None and _PLAIN_TYPE.fullmatch(typ)
        out.append((_text(name), typ.rsplit(".", 1)[-1] if plain else ""))
    return out


def _class_fields(fn: Node) -> dict[str, str]:
    body = fn.parent
    while body is not None and body.type not in _SKIP_SCOPES:
        body = body.parent
    out: dict[str, str] = {}
    for member in body.children if body is not None else ():
        if member.type == "field_declaration":
            out.update(_declared(member))
    return out


def _receivers(fn: Node, types: frozenset[str]) -> dict[str, str]:
    """Names bound in *fn* to a persistence type. A parameter or local of another
    type shadows a field of the same name, wherever it is declared."""
    bound = _class_fields(fn)
    for node in walk(fn, _SKIP_SCOPES):
        if node.type in ("formal_parameter", "local_variable_declaration"):
            bound.update(_declared(node))
    return {name: t for name, t in bound.items() if t in types}


def _receiver_name(call: Node) -> str:
    obj = call.child_by_field_name("object")
    if obj is None:
        return ""
    if obj.type == "identifier":
        return _text(obj)
    if obj.type == "field_access" and _text(obj.child_by_field_name("object")) == "this":
        return _text(obj.child_by_field_name("field"))
    return ""


def _loop_facts(
    call: Node, loop: Node, source: LoopSource | None, dialect: BasePerfDialect, lmap: Any
) -> LoopFacts:
    return LoopFacts(
        chunked=dialect.is_chunked_loop(loop),
        magnitude=source.magnitude if source is not None else "unknown",
        iterates=source.iterates if source is not None else "unknown",
        key_unused=not _loop_key_feeds(call, loop),
        not_batchable=dialect.not_batchable(call, loop, lmap.loop_kinds),
    )


def _loop_hits(
    loop: Node, fn: Node, fn_name: str | None, receivers: dict[str, str], ctx: dict[str, Any]
) -> list[PerfHit]:
    dialect: BasePerfDialect = ctx["dialect"]
    body = loop.child_by_field_name("body")
    if body is None:
        return []
    source = dialect.loop_source(loop, ctx["probe"])
    if source is not None and source.iterates == "constant":
        return []
    hits: list[PerfHit] = []
    for call in walk(body, _SKIP_SCOPES):
        if call.type != "method_invocation" or _receiver_name(call) not in receivers:
            continue
        line = call.start_point[0] + 1
        if line in ctx["taken"]:
            continue
        ctx["taken"].add(line)
        method = dialect.callee_method_name(call) or ""
        hits.append(
            PerfHit(
                "io_in_loop", line, fn_name, "db",
                func_start=fn.start_point[0] + 1,
                loop=_loop_facts(call, loop, source, dialect, ctx["lmap"]),
                loop_line=loop.start_point[0] + 1,
                sink_call=_sink_call_text(call, method, dialect),
            )
        )
    return hits


def _probe(dialect: BasePerfDialect, io_names: dict[str, str], receivers: dict[str, str]) -> Any:
    """The walker's sink probe, plus a call on a persistence-typed receiver."""
    has_db = any(v == "db" for v in io_names.values())

    def probe(call: Node) -> str | None:
        if call.type == "method_invocation" and _receiver_name(call) in receivers:
            return "db"
        return dialect.call_sink_kind(call, awaited=False, io_names=io_names, has_db_import=has_db)

    return probe


def _file_hits(
    pf: Any, fcx: FileComplexity, source: bytes, types: frozenset[str]
) -> list[PerfHit]:
    from ..dataflow.parsing import parse_source  # deferred: dataflow imports perf

    # The walker keeps no trees, so parse again, as lazy_load does; only files
    # whose type references name a persistence type reach here.
    parsed = parse_source(pf.file_info.abs_path, "java", source)
    dialect = PERF_DIALECTS.get("java")
    if parsed is None or dialect is None:
        return []
    root, lmap = parsed
    io_names = collect_io_names(root, "java")
    taken = {h.line for h in fcx.perf_hits if h.kind == "io_in_loop"}
    hits: list[PerfHit] = []
    for fn in _collect_function_nodes(root, lmap):
        receivers = _receivers(fn, types)
        if not receivers:
            continue
        ctx = {
            "dialect": dialect,
            "lmap": lmap,
            "probe": _probe(dialect, io_names, receivers),
            "taken": taken,
        }
        name = _find_function_entry_name(fn, lmap)
        for loop in walk(fn, _SKIP_SCOPES):
            if loop.type == _LOOP:
                hits.extend(_loop_hits(loop, fn, name, receivers, ctx))
    return hits


def collect_repository_loops(
    walked: Iterable[tuple[Any, FileComplexity]],
    parsed_files: Iterable[Any],
    read_source: SourceReader,
) -> None:
    """Append hits to each walked Java file's ``perf_hits``; one file failing skips only it."""
    walked = [(pf, fcx) for pf, fcx in walked if pf.file_info.language == "java"]
    if not walked:
        return
    types = persistence_types(parsed_files)
    if not types:
        return
    for pf, fcx in walked:
        if pf.file_info.is_test or not any(r.type_name in types for r in pf.type_refs):
            continue
        try:
            source = read_source(pf.file_info.abs_path)
            if source is not None:
                fcx.perf_hits.extend(_file_hits(pf, fcx, source, types))
        except Exception as exc:
            log.debug("repository_loops_failed", path=pf.file_info.abs_path, error=str(exc))


__all__ = ["collect_repository_loops", "persistence_types"]
