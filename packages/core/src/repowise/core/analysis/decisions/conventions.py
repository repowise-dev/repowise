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
#: Module-level bindings that can hold a client instance built from the library.
_BINDING_KINDS = frozenset({"variable", "constant"})
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


def _call_pattern(names: tuple[str, ...] | set[str]) -> re.Pattern[str] | None:
    """A call into one of *names*: ``name(``, ``name.member(`` or ``new name``."""
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(
        r"(?<![\w.$])(?:" + alt + r")\s*(?:\.\s*[\w$]+\s*)*\(|new\s+(?:" + alt + r")(?![\w$])"
    )


def _confirmed_bindings(parsed: Any, use: _Use, text: str) -> list[Any]:
    """Module-level bindings whose own initializer calls into the library.

    The second wrapper shape: ``service = axios.create()``, ``client =
    httpx.Client()``, an exported ``fetch`` built over undici. A binding that
    merely names the library (``TIMEOUT = httpx.Timeout(5)``) also matches
    here; what separates a client from a setting is whether importers call
    it, which :func:`_calling_importers` decides.
    """
    pattern = _call_pattern(use.local_names)
    if pattern is None:
        return []
    suffix = Path(parsed.file_info.path).suffix
    lines = mask_source(text, suffix, strings=True).split("\n")
    out = []
    for sym in parsed.symbols:
        if sym.kind not in _BINDING_KINDS or sym.parent_name:
            continue
        span = "\n".join(lines[max(sym.start_line - 1, 0) : max(sym.end_line, sym.start_line)])
        if pattern.search(span):
            out.append(sym)
    return out


def _first_call_line(text: str, suffix: str, name: str) -> int | None:
    """Line of the first call through *name* in *text*, or None when it is only read."""
    pattern = _call_pattern((name,))
    if pattern is None:
        return None
    masked = mask_source(text, suffix, strings=True)
    m = pattern.search(masked)
    return masked.count("\n", 0, m.start()) + 1 if m else None


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


def _calling_importers(
    graph: nx.DiGraph,
    importers: set[str],
    wrapper: str,
    name: str,
    parsed_by_path: dict[str, Any],
    source_map: dict[str, bytes] | None,
    repo_path: Path,
) -> dict[str, int | None]:
    """Importers that bind *name* from *wrapper* and call it: ``{file: line}``.

    A setting is read; a client is called. Reading each importer once,
    bounded to the files that already import the wrapper, is what tells the
    two apart without a vocabulary of constructors per library.
    """
    out: dict[str, int | None] = {}
    for f in sorted(importers):
        imported = graph[f][wrapper].get("imported_names") or []
        if name not in imported:
            continue
        text = _file_text(f, source_map, repo_path)
        if text is None:
            continue
        line = _first_call_line(text, Path(f).suffix, name)
        if line is not None:
            out[f] = line
    return out


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
    #: What the counts are counts of: "files", or "packages" for languages
    #: whose imports name a package directory.
    unit: str = "files"
    #: The direct importers as files, for the record's scope; equals
    #: ``direct`` when the unit is files.
    direct_files: set[str] | None = None


def _package_dir(path: str) -> str:
    parent = path.rpartition("/")[0]
    return parent or "."


def _package_candidate(
    graph: nx.DiGraph,
    package: str,
    io_kind: str,
    wrapper: str,
    parsed: Any,
    use: _Use,
    text: str,
    direct_files: set[str],
) -> _Candidate | None:
    """The package-level shape for Go and Java.

    An import there names a package directory and the builder fans the edge
    out to every file in it, so a file-level import count would say every
    file in an importing package reaches the library. Packages are the unit
    instead, and a package reaches the library through the wrapper only when
    one of its files calls a confirmed callable: the call edges are symbol
    level and carry no fan-out.
    """
    confirmed = _confirmed_callables(parsed, use, text)
    if not confirmed:
        return None
    own = _package_dir(wrapper)
    direct_pkgs = {_package_dir(f) for f in direct_files} - {own}
    ranked = sorted(
        ((sym, _inbound_calls(graph, sym.id, wrapper)) for sym in confirmed),
        key=lambda pair: (-len(pair[1]), pair[0].start_line),
    )
    symbol, sites = ranked[0]
    caller_pkgs = {
        _package_dir(f)
        for _sym, sites_ in ranked
        for f, _line in sites_
        if not _is_test(graph, f)
    }
    through = caller_pkgs - direct_pkgs - {own}
    if len(through) < MIN_THROUGH:
        return None
    return _Candidate(
        package=package,
        io_kind=io_kind,
        wrapper=wrapper,
        symbol=symbol,
        through=through,
        direct=direct_pkgs,
        call_sites=sites,
        unit="packages",
        direct_files={f for f in direct_files if _package_dir(f) != own},
    )


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
    by_package_dir: dict[str, _Candidate] = {}
    for wrapper in sorted(direct_files):
        parsed = parsed_by_path.get(wrapper)
        if parsed is None:
            continue
        if parsed.file_info.language in UNIT_FANOUT_LANGUAGES:
            text = _file_text(wrapper, source_map, repo_path)
            if text is None:
                continue
            candidate = _package_candidate(
                graph, package, io_kind, wrapper, parsed, uses[wrapper][package], text, direct_files
            )
            if candidate is None:
                continue
            # Two files of one package are one wrapper; keep the wider one.
            key = _package_dir(wrapper)
            prior = by_package_dir.get(key)
            if prior is None or len(candidate.through) > len(prior.through):
                by_package_dir[key] = candidate
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
        use = uses[wrapper][package]
        confirmed = _confirmed_callables(parsed, use, text)
        symbol = None
        sites: list[tuple[str, int | None]] = []
        through: set[str] = set()
        if confirmed:
            # The symbol other files call most is the entry point the
            # convention names; a tie falls to the earliest declaration.
            ranked = sorted(
                ((sym, _inbound_calls(graph, sym.id, wrapper)) for sym in confirmed),
                key=lambda pair: (-len(pair[1]), pair[0].start_line),
            )
            symbol, sites = ranked[0]
            # A file reaches the library through the wrapper only if it
            # imports one of the confirmed callables, the class holding one,
            # or the module whole, or calls one. Importing the file for an
            # unrelated helper is not reaching the library.
            names = {s.name for s in confirmed} | {s.parent_name for s in confirmed if s.parent_name}
            callers = {f for _sym, sites_ in ranked for f, _line in sites_}
            through = {f for f in importers if _reaches(graph, f, wrapper, names) or f in callers}
        if len(through) < MIN_THROUGH:
            # Second shape: a client instance built at module level and
            # called by its importers. Only importers that call it count.
            for binding in _confirmed_bindings(parsed, use, text):
                calling = _calling_importers(
                    graph, importers, wrapper, binding.name, parsed_by_path, source_map, repo_path
                )
                if len(calling) > len(through):
                    symbol = binding
                    through = set(calling)
                    sites = sorted(calling.items())
        if symbol is None or len(through) < MIN_THROUGH:
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
    return [*out, *by_package_dir.values()]


def _record(candidate: _Candidate) -> ExtractedDecision:
    from repowise.core.analysis.decisions.extractor import ExtractedDecision

    through = len(candidate.through)
    direct = len(candidate.direct)
    exceptions = sorted(candidate.direct)
    unit = candidate.unit
    decision = (
        f"{through} of {through + direct} {unit} reach {candidate.package} through "
        f"{candidate.wrapper}; {direct} import it directly."
    )
    basis = "call edges" if unit == "packages" else "import edges"
    rationale = (
        f"Counted from {basis}, excluding test files and the wrapper's own {unit[:-1]}. "
        f"Threshold: at least {MIN_THROUGH} {unit} through the wrapper and at most one "
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
    what = "instance" if candidate.symbol.kind in _BINDING_KINDS else "symbol"
    context = (
        f"Wrapper {what} {candidate.symbol.id} at {candidate.wrapper}:"
        f"{candidate.symbol.start_line}. Sample call sites: {sample}."
    )
    quote = "; ".join([decision, *consequences])
    direct_files = sorted(candidate.direct_files if candidate.direct_files is not None else exceptions)
    affected_files = [candidate.wrapper, *direct_files][:20]
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
