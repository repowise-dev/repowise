"""Conventions: majority import patterns the graph proves.

A convention is a choice point the repository has already settled: an in-repo
wrapper W whose body uses an external library X that performs I/O (an HTTP
client, a database driver, a subprocess runner, a filesystem or lock
primitive), where most files reach X through W and few import X directly.
The candidate says so in counts and nothing else, and it lands ``proposed``:
a person confirms it before any agent sees it as a rule.

Everything here is read from the in-memory graph, the parsed files and the
source bytes ingestion already holds. No database, no model, no tree walk.

What the graph can and cannot say shapes the rule. A ``calls`` edge is only
minted between two in-repo symbols, so "which files call X" is not in the
graph for any language. "Which files import X" is, once the resolver
registers the miss as an ``external:`` node, and "which files import W" is an
ordinary file-to-file import edge. The wrapper itself is confirmed by reading
its callable symbols' bodies, bounded by their indexed line spans, for a
reference to the name the file bound X to. Co-location is not wrapping: a
file that imports X and happens to be imported by many files is only a
wrapper when one of its functions actually uses X.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.analysis.decisions.provenance import compute_confidence, rank_for_source
from repowise.core.analysis.decisions.scope import resolve_module_nodes
from repowise.core.analysis.external_systems.links import declaration_name_candidates
from repowise.core.ingestion.cohesion import UNIT_FANOUT_LANGUAGES
from repowise.core.ingestion.external_systems.io_kind import classify_io_kind
from repowise.core.workspace.extractors.http.wrappers import mask_source, symbol_body

if TYPE_CHECKING:
    import networkx as nx

    from repowise.core.analysis.decisions.extractor import ExtractedDecision

__all__ = [
    "MAX_PROPOSALS",
    "MIN_RATIO",
    "MIN_THROUGH",
    "SOURCE_KEY",
    "scan_conventions",
]

SOURCE_KEY = "conventions"

#: Files that must reach the library through the wrapper before a pattern is
#: worth proposing. Below this there is no majority to speak of.
MIN_THROUGH = 5

#: At most one direct importer per this many files through the wrapper.
MIN_RATIO = 4

#: Proposals per index. Nothing else caps a deterministic source.
MAX_PROPOSALS = 10

#: How many direct importers a record lists by path before it counts the rest.
MAX_LISTED_EXCEPTIONS = 25

_CALLABLE_KINDS = frozenset({"function", "method", "constructor"})
_EXTERNAL_PREFIX = "external:"
_ECOSYSTEM_PREFIXES = ("gem:", "nuget:")


@dataclass(frozen=True, slots=True)
class _Use:
    """One file's import of one I/O package."""

    package: str
    io_kind: str
    local_names: tuple[str, ...]
    reexport_only: bool


def _package_of(external_key: str) -> tuple[str, str] | None:
    """``(package, io_kind)`` for an ``external:`` node id, or None when untyped."""
    name = external_key[len(_EXTERNAL_PREFIX) :]
    for prefix in _ECOSYSTEM_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
    for candidate in declaration_name_candidates(name):
        kind = classify_io_kind(candidate)
        if kind is not None:
            return candidate, kind
    return None


def _uses_by_file(parsed_files: list[Any]) -> dict[str, dict[str, _Use]]:
    """Per file, the I/O packages it imports, merged across import statements."""
    out: dict[str, dict[str, _Use]] = {}
    for parsed in parsed_files:
        path = parsed.file_info.path
        merged: dict[str, _Use] = {}
        for imp in parsed.imports:
            target = imp.resolved_file or ""
            if not target.startswith(_EXTERNAL_PREFIX):
                continue
            typed = _package_of(target)
            if typed is None:
                continue
            package, kind = typed
            names = tuple(n for n in imp.local_names if n and n != "*")
            prior = merged.get(package)
            reexport_only = bool(imp.is_reexport) and (prior is None or prior.reexport_only)
            merged[package] = _Use(
                package=package,
                io_kind=kind,
                local_names=tuple(dict.fromkeys((*(prior.local_names if prior else ()), *names))),
                reexport_only=reexport_only,
            )
        if merged:
            out[path] = merged
    return out


def _file_text(path: str, source_map: dict[str, bytes] | None, repo_path: Path) -> str | None:
    """The bytes ingestion read for *path*, or one bounded read of that file."""
    raw = source_map.get(path) if source_map else None
    if raw is None:
        try:
            raw = (repo_path / path).read_bytes()
        except OSError:
            return None
    return raw.decode("utf-8", errors="replace")


def _reference_pattern(names: tuple[str, ...]) -> re.Pattern[str] | None:
    """A use of one of the bound names as a standalone identifier.

    Not preceded by ``.`` or an identifier character, so ``self.client`` does
    not match a binding called ``client`` and ``myrequests`` does not match
    ``requests``.
    """
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<![\w.$])(?:" + alternatives + r")(?![\w$])")


def _confirmed_callables(parsed: Any, use: _Use, text: str) -> list[Any]:
    """Callable symbols of *parsed* whose own body references the bound name."""
    pattern = _reference_pattern(use.local_names)
    if pattern is None:
        return []
    suffix = Path(parsed.file_info.path).suffix
    lines = mask_source(text, suffix, strings=True).split("\n")
    out = []
    for sym in parsed.symbols:
        if sym.kind not in _CALLABLE_KINDS:
            continue
        if pattern.search(symbol_body(lines, sym)):
            out.append(sym)
    return out


def _inbound_calls(graph: nx.DiGraph, symbol_id: str, own_file: str) -> list[tuple[str, int | None]]:
    """``(caller file, first call line)`` for calls into *symbol_id* from other files."""
    if symbol_id not in graph:
        return []
    sites: list[tuple[str, int | None]] = []
    for caller in graph.predecessors(symbol_id):
        data = graph[caller][symbol_id]
        if data.get("edge_type") != "calls":
            continue
        caller_file = graph.nodes[caller].get("file_path")
        if not caller_file or caller_file == own_file:
            continue
        lines = data.get("call_lines") or []
        sites.append((caller_file, min(lines) if lines else None))
    return sorted(sites, key=lambda s: (s[0], s[1] if s[1] is not None else 0))


def _importers(graph: nx.DiGraph, path: str) -> set[str]:
    """Files with an import edge into *path*."""
    if path not in graph:
        return set()
    return {
        src
        for src in graph.predecessors(path)
        if graph[src][path].get("edge_type") == "imports"
        and graph.nodes[src].get("node_type", "file") == "file"
    }


def _reaches(graph: nx.DiGraph, importer: str, wrapper: str, names: set[str]) -> bool:
    """Whether *importer*'s import of *wrapper* binds a confirmed callable."""
    imported = graph[importer][wrapper].get("imported_names") or []
    if not imported or "*" in imported:
        return True  # the module as a whole; the callable is reachable through it
    return any(n in names for n in imported)


def _is_test(graph: nx.DiGraph, path: str) -> bool:
    return bool(graph.nodes[path].get("is_test")) if path in graph else False


@dataclass(slots=True)
class _Candidate:
    package: str
    io_kind: str
    wrapper: str
    symbol: Any
    through: set[str]
    direct: set[str]
    call_sites: list[tuple[str, int | None]]


def _candidates_for(
    graph: nx.DiGraph,
    package: str,
    io_kind: str,
    direct_files: set[str],
    parsed_by_path: dict[str, Any],
    uses: dict[str, dict[str, _Use]],
    source_map: dict[str, bytes] | None,
    repo_path: Path,
) -> list[_Candidate]:
    """Every confirmed wrapper of *package* that clears the minimum count."""
    out: list[_Candidate] = []
    for wrapper in sorted(direct_files):
        parsed = parsed_by_path.get(wrapper)
        if parsed is None or parsed.file_info.language in UNIT_FANOUT_LANGUAGES:
            continue
        importers = {
            f
            for f in _importers(graph, wrapper)
            if f != wrapper and not _is_test(graph, f) and f not in direct_files
        }
        if len(importers) < MIN_THROUGH:
            continue
        text = _file_text(wrapper, source_map, repo_path)
        if text is None:
            continue
        confirmed = _confirmed_callables(parsed, uses[wrapper][package], text)
        if not confirmed:
            continue
        # The symbol other files call most is the entry point the convention
        # names; a tie falls to the earliest declaration.
        ranked = sorted(
            ((sym, _inbound_calls(graph, sym.id, wrapper)) for sym in confirmed),
            key=lambda pair: (-len(pair[1]), pair[0].start_line),
        )
        symbol, sites = ranked[0]
        # A file reaches the library through the wrapper only if it imports
        # one of the confirmed callables, the class holding one, or the module
        # whole, or calls one. Importing the file for an unrelated helper is
        # not reaching the library.
        names = {s.name for s in confirmed} | {s.parent_name for s in confirmed if s.parent_name}
        callers = {f for _sym, sites_ in ranked for f, _line in sites_}
        through = {f for f in importers if _reaches(graph, f, wrapper, names) or f in callers}
        if len(through) < MIN_THROUGH:
            continue
        out.append(
            _Candidate(
                package=package,
                io_kind=io_kind,
                wrapper=wrapper,
                symbol=symbol,
                through=through,
                direct=direct_files - {wrapper},
                call_sites=sites,
            )
        )
    return out


def _record(candidate: _Candidate) -> ExtractedDecision:
    from repowise.core.analysis.decisions.extractor import ExtractedDecision

    through = len(candidate.through)
    direct = len(candidate.direct)
    exceptions = sorted(candidate.direct)
    decision = (
        f"{through} of {through + direct} files reach {candidate.package} through "
        f"{candidate.wrapper}; {direct} import it directly."
    )
    rationale = (
        "Counted from import edges, excluding test files and the wrapper's own file. "
        f"Threshold: at least {MIN_THROUGH} files through the wrapper and at most one "
        f"direct importer per {MIN_RATIO}."
    )
    consequences = [f"{path} imports {candidate.package} directly" for path in exceptions]
    if len(consequences) > MAX_LISTED_EXCEPTIONS:
        rest = len(consequences) - MAX_LISTED_EXCEPTIONS
        consequences = [*consequences[:MAX_LISTED_EXCEPTIONS], f"and {rest} more direct importers"]
    sites = candidate.call_sites[:3]
    if sites:
        sample = ", ".join(f"{f}:{line}" if line else f for f, line in sites)
    else:
        sample = ", ".join(sorted(candidate.through)[:3])
    context = (
        f"Wrapper symbol {candidate.symbol.id} at {candidate.wrapper}:"
        f"{candidate.symbol.start_line}. Sample call sites: {sample}."
    )
    quote = "; ".join([decision, *consequences])
    affected_files = [candidate.wrapper, *exceptions][:20]
    return ExtractedDecision(
        title=f"{candidate.package} goes through {candidate.wrapper}",
        context=context,
        decision=decision,
        rationale=rationale,
        consequences=consequences,
        affected_files=affected_files,
        affected_modules=resolve_module_nodes(affected_files),
        tags=["convention", candidate.io_kind],
        source=SOURCE_KEY,
        evidence_file=candidate.wrapper,
        evidence_line=candidate.symbol.start_line,
        confidence=compute_confidence(rank_for_source(SOURCE_KEY), 1, "exact"),
        status="proposed",
        source_quote=quote,
        # The counts are the ground truth, so the gate reads them as exact.
        source_text=quote,
        # The share of files that bypass the wrapper. It moves when the
        # pattern erodes, which is what stale means for a convention.
        staleness_score=round(direct / (direct + through), 3),
    )


def scan_conventions(
    graph: nx.DiGraph | None,
    parsed_files: list[Any],
    source_map: dict[str, bytes] | None,
    repo_path: Path,
    *,
    limit: int = MAX_PROPOSALS,
) -> list[ExtractedDecision]:
    """Propose one candidate per (wrapper, I/O package) the graph proves.

    A package with two confirmed wrappers yields nothing: two legitimate
    wrappers would each read as the other's minority, and a wrong convention
    costs more than a missed one.
    """
    if graph is None or not parsed_files:
        return []
    repo_path = Path(repo_path)
    parsed_by_path = {pf.file_info.path: pf for pf in parsed_files}
    uses = _uses_by_file(parsed_files)

    direct_by_package: dict[str, set[str]] = defaultdict(set)
    kind_by_package: dict[str, str] = {}
    for path, per_package in uses.items():
        if _is_test(graph, path):
            continue
        for package, use in per_package.items():
            if use.reexport_only:
                continue  # a barrel forwards the name; it neither wraps nor uses it
            direct_by_package[package].add(path)
            kind_by_package[package] = use.io_kind

    proposals: list[_Candidate] = []
    for package in sorted(direct_by_package):
        direct_files = direct_by_package[package]
        candidates = _candidates_for(
            graph,
            package,
            kind_by_package[package],
            direct_files,
            parsed_by_path,
            uses,
            source_map,
            repo_path,
        )
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        if len(candidate.direct) * MIN_RATIO > len(candidate.through):
            continue
        proposals.append(candidate)

    proposals.sort(key=lambda c: (-len(c.through), c.package, c.wrapper))
    return [_record(c) for c in proposals[:limit]]
