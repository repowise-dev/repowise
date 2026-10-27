"""Mainframe edges: JCL steps to programs and PROCs, CSD definitions to programs,
COBOL programs to BMS mapsets and to the programs their TRANSIDs run.

* ``EXEC PGM=X`` -> the COBOL program whose PROGRAM-ID is X, marked a
  reachability root: a batch program runs because a job names it.
* ``EXEC P`` / ``EXEC PROC=P`` -> the procedure member P, by file stem first,
  then by a unique ``//P PROC`` declaration.
* ``DEFINE PROGRAM(X)`` or ``DEFINE TRANSACTION(T) PROGRAM(X)``, in a ``.csd``
  file or a JCL step's in-stream data -> program X, marked a reachability
  root: CICS runs it by its definition.
* ``EXEC CICS SEND|RECEIVE MAP ... MAPSET(M)`` -> the BMS source defining M.
* ``EXEC CICS RETURN|START TRANSID(T)`` -> the program transaction T runs,
  when every definition of T names the same one.

A job member is an entry point. Names fold case; two equally near candidates
bind neither unless exactly one nearest mapset is under a ``cobol`` directory.
A utility (``IDCAMS``, ``SORT``) the repo does not hold links
to nothing. A program returning to its own transaction gets no edge.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ..cobol import code_view
from ..cobol.cics import mapset_uses, transid_uses
from ..cobol.csd import CsdDefinition, defined_programs, read_csd
from ..cobol.jcl import read_jcl
from ..resolvers.lwc import nearest
from .aura import _file_edge
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


def _csd_definitions(path: str, parsed: Any, source_map: dict[str, bytes]) -> list[CsdDefinition]:
    """A ``.csd`` file's definitions, or those in a JCL member's in-stream data."""
    language = parsed.file_info.language
    if language not in ("csd", "jcl"):
        return []
    text = source_text(path, parsed, source_map)
    if language == "csd":
        return read_csd(enumerate(text.splitlines(), 1))
    return read_csd(read_jcl(text).in_stream)


def _add_mainframe_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext
) -> int:
    source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
    programs = _by_name(parsed_files, "cobol", "module")
    mapsets = _by_name(parsed_files, "bms", "module")
    procs = _proc_files(parsed_files)
    definitions = {
        path: defs
        for path in sorted(parsed_files)
        if (defs := _csd_definitions(path, parsed_files[path], source_map))
    }
    # Transaction -> every program a definition of it names (regions may differ).
    transactions: dict[str, set[str]] = {}
    for defs in definitions.values():
        for d in defs:
            if d.program:
                transactions.setdefault(d.name, set()).add(d.program)
    count = 0
    for path, defs in definitions.items():
        for name in defined_programs(defs):
            count += _root_edge(graph, path, nearest(programs.get(name, []), path))
    for path in sorted(parsed_files):
        parsed = parsed_files[path]
        language = parsed.file_info.language
        if language == "jcl":
            member = read_jcl(source_text(path, parsed, source_map))
            if member.jobs and path in graph:
                graph.nodes[path]["is_entry_point"] = True
            for step in member.steps:
                if step.program:
                    count += _root_edge(graph, path, nearest(programs.get(step.program, []), path))
                elif step.procedure:
                    target = nearest(procs.get(step.procedure, []), path)
                    if target:
                        count += _file_edge(graph, path, target, set())
        elif language == "cobol" and (mapsets or transactions):
            raw = source_map.get(path) or source_text(path, parsed, source_map).encode()
            code = code_view(raw)
            for use in mapset_uses(code):
                for name in use.targets:
                    candidates = mapsets.get(name, [])
                    target = nearest(candidates, path)
                    if target is None and candidates:
                        # Apply the language preference only within the nearest tie.
                        ref = PurePosixPath(path).parts
                        scores = {}
                        for candidate in candidates:
                            shared = 0
                            for left, right in zip(PurePosixPath(candidate).parts, ref, strict=False):
                                if left != right:
                                    break
                                shared += 1
                            scores[candidate] = shared
                        closest = max(scores.values())
                        cobol = [
                            candidate
                            for candidate in candidates
                            if scores[candidate] == closest
                            and "cobol" in {
                                part.lower() for part in PurePosixPath(candidate).parts[:-1]
                            }
                        ]
                        if len(cobol) == 1:
                            target = cobol[0]
                    if target:
                        count += _file_edge(graph, path, target, set())
            for use in transid_uses(code):
                # A TRANSID also set at runtime may name any transaction: no edge.
                for transid in () if use.dynamic else use.targets:
                    runs = transactions.get(transid, set())
                    if len(runs) == 1:
                        target = nearest(programs.get(next(iter(runs)), []), path)
                        if target:
                            count += _file_edge(graph, path, target, set())
    return count


def _root_edge(graph: nx.DiGraph, src: str, program: str | None) -> int:
    """An edge from *src* to *program*, which something outside the source runs."""
    if program is None or program not in graph:
        return 0
    graph.nodes[program]["is_reachability_root"] = True
    return _file_edge(graph, src, program, set())


class _MainframeHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(
            p.file_info.language in ("jcl", "bms", "csd") for p in dctx.parsed_files.values()
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_mainframe_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [_MainframeHandler()]
