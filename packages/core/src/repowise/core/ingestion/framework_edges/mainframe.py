"""Mainframe edges: JCL steps to programs and PROCs, COBOL programs to BMS mapsets.

* ``EXEC PGM=X`` -> the COBOL program whose PROGRAM-ID is X, marked a
  reachability root: a batch program runs because a job names it.
* ``EXEC P`` / ``EXEC PROC=P`` -> the procedure member P, by file stem first,
  then by a unique ``//P PROC`` declaration.
* ``EXEC CICS SEND|RECEIVE MAP ... MAPSET(M)`` -> the BMS source defining M.

A job member is an entry point. Names fold case; two equally near candidates
bind neither, and a utility (``IDCAMS``, ``SORT``) the repo does not hold links
to nothing.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ..cobol import code_view
from ..cobol.cics import mapset_uses
from ..cobol.jcl import read_jcl
from .aura import _file_edge, _nearest
from .base import DetectionContext, FrameworkHandler, source_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_PROC_SUFFIXES = frozenset({".prc", ".proc"})


def _by_name(parsed_files: dict[str, Any], language: str, kind: str) -> dict[str, list[str]]:
    """Upper-cased symbol name -> files declaring it, for *language*'s *kind* symbols."""
    out: dict[str, list[str]] = {}
    for path in sorted(parsed_files):
        parsed = parsed_files[path]
        if parsed.file_info.language != language:
            continue
        for sym in parsed.symbols:
            if sym.kind == kind:
                out.setdefault(sym.name.upper(), []).append(path)
    return out


def _proc_files(parsed_files: dict[str, Any]) -> dict[str, list[str]]:
    """PROC name -> member: the file stem, else a PROC (never a JOB) declared in one file."""
    out: dict[str, list[str]] = {}
    declared: dict[str, list[str]] = {}
    for path in sorted(parsed_files):
        parsed = parsed_files[path]
        if parsed.file_info.language != "jcl":
            continue
        p = PurePosixPath(path)
        if p.suffix.lower() in _PROC_SUFFIXES:
            out.setdefault(p.stem.upper(), []).append(path)
        for sym in parsed.symbols:
            if sym.kind == "module" and sym.signature.startswith("PROC "):
                declared.setdefault(sym.name.upper(), []).append(path)
    for name, paths in declared.items():
        if name not in out and len(set(paths)) == 1:
            out[name] = paths
    return out


def _add_mainframe_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext
) -> int:
    source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
    programs = _by_name(parsed_files, "cobol", "module")
    mapsets = _by_name(parsed_files, "bms", "module")
    procs = _proc_files(parsed_files)
    count = 0
    for path in sorted(parsed_files):
        parsed = parsed_files[path]
        language = parsed.file_info.language
        if language == "jcl":
            member = read_jcl(source_text(path, parsed, source_map))
            if member.jobs and path in graph:
                graph.nodes[path]["is_entry_point"] = True
            for step in member.steps:
                if step.program:
                    target = _nearest(path, programs.get(step.program, []))
                    if target and target in graph:
                        graph.nodes[target]["is_reachability_root"] = True
                        count += _file_edge(graph, path, target, set())
                elif step.procedure:
                    target = _nearest(path, procs.get(step.procedure, []))
                    if target:
                        count += _file_edge(graph, path, target, set())
        elif language == "cobol" and mapsets:
            raw = source_map.get(path) or source_text(path, parsed, source_map).encode()
            for use in mapset_uses(code_view(raw)):
                for name in use.targets:
                    target = _nearest(path, mapsets.get(name, []))
                    if target:
                        count += _file_edge(graph, path, target, set())
    return count


class _MainframeHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language in ("jcl", "bms") for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_mainframe_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [_MainframeHandler()]
