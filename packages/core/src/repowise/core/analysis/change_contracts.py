"""Symbol-level contract impact: which symbols a change altered, and who calls them.

File-level reach (:mod:`repowise.core.analysis.pr_blast`) answers "which files
import the files you touched". That is a file-altitude answer to a
symbol-altitude question: importing a module says nothing about whether the
*function whose signature changed* is the one being called. This module answers
the narrower question, and the two are kept distinct on purpose:

* contract impact -- which changed symbols affect callers (here);
* PR blast radius -- file-level structural reach, ownership, and test gaps.

The algorithm:

1. Diff a base parse against a head parse per changed file, classifying every
   symbol as added / removed / signature-changed / body-changed.
2. Walk the graph's ``calls`` edges to collect each changed symbol's callers,
   partitioned into inside-change (already under review) and outside-change.
3. Outside callers of a *removed* or *signature-changed* symbol are the finding.

Everything here is deterministic set/graph work over data the caller already
holds. No SQL, no network, no LLM.

Two honesty constraints shape the output:

* A symbol whose signature is unchanged and whose line range does not overlap
  the change's added lines is not reported at all. The base parse may come from
  the last indexed snapshot rather than the change's true base, and a snapshot
  can be many commits behind -- without the added-line gate, every symbol that
  drifted since indexing would be blamed on this change.
* ``callers_total`` is the uncapped count. A surface may cap the rendered list,
  but the number never lies about how many there are.

Core returns the full population: ``callers_per_symbol`` defaults to ``None``
(uncapped). Capping is surface policy, and a surface that needs a cap passes it
explicitly rather than reimplementing the walk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .signature_diff import (
    EFFECT_COMPATIBLE,
    EFFECT_NONE,
    classify_signature_change,
)
from ..test_paths import is_test_related_path

# Symbol kinds worth reporting on. Constants and variables produce enormous,
# low-signal churn (every literal edit reads as a "signature change"), and the
# call graph does not resolve calls *to* them anyway.
REPORTED_KINDS = frozenset(
    {"function", "method", "class", "interface", "trait", "struct", "impl", "enum"}
)

#: Classification values for :attr:`SymbolContractChange.change`.
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_SIGNATURE = "signature"
CHANGE_BODY = "body"


@dataclass(frozen=True, slots=True)
class SymbolFacts:
    """The subset of a parsed symbol this module compares on."""

    symbol_id: str
    name: str
    kind: str
    signature: str
    start_line: int
    end_line: int


@dataclass
class SymbolContractChange:
    """One symbol the change altered, with its resolved callers.

    ``outside_callers`` holds symbol ids (``path::Name``) of callers whose file
    is not part of this change -- the ones nobody is currently reviewing.
    """

    file: str
    name: str
    symbol_id: str
    kind: str
    change: str
    start_line: int
    end_line: int
    signature_effect: str | None = None
    signature_reason: str | None = None
    outside_callers: list[str] = field(default_factory=list)
    outside_production_callers: list[str] = field(default_factory=list)
    outside_test_callers: list[str] = field(default_factory=list)
    inside_caller_count: int = 0
    callers_total: int = 0
    outside_callers_total: int = 0
    outside_production_callers_total: int = 0
    outside_test_callers_total: int = 0

    @property
    def is_breaking(self) -> bool:
        """A contract change with callers nobody in this change is looking at.

        Body-only changes are excluded on purpose: the caller's contract still
        holds, so listing its callers is noise. Added symbols have no prior
        callers by construction.
        """
        if self.change == CHANGE_REMOVED:
            return bool(self.outside_callers)
        if self.change == CHANGE_SIGNATURE:
            if self.signature_effect in (EFFECT_NONE, EFFECT_COMPATIBLE):
                return False
            return bool(self.outside_callers)
        return False


@dataclass
class ContractImpact:
    """The typed result of one contract-impact analysis."""

    changes: list[SymbolContractChange] = field(default_factory=list)
    #: ``available`` | ``partial`` | ``unavailable`` | ``degraded`` | ``unsupported``.
    status: str = "available"
    reason: str | None = None
    # True when the base side came from the last indexed snapshot rather than
    # the change's true base commit -- the comparison is then against whatever
    # was indexed, which may be several commits behind. Renderers say so.
    base_is_snapshot: bool = False

    @property
    def breaking(self) -> list[SymbolContractChange]:
        return [c for c in self.changes if c.is_breaking]

    def is_empty(self) -> bool:
        return not self.changes


def _containing_file(symbol_id: str) -> str:
    """The file a symbol node belongs to.

    Ids are ``<path>::<name>`` (and ``<path>::<class>::<method>`` for methods),
    so the first segment wins.
    """
    return symbol_id.split("::", 1)[0]


def symbol_index(
    parsed: Any, changed_set: set[str] | None = None
) -> dict[str, dict[str, SymbolFacts]]:
    """``{file_path: {symbol_name: SymbolFacts}}`` from either shape of parse.

    Accepts both live ``ParsedFile``-shaped objects and the plain dicts in a
    ``parsed_files.json`` artifact, so the same index builder serves the head
    parse, the base parse, and a snapshot fallback. Unknown or malformed rows
    are skipped rather than raised on: this feeds advisory evidence, and one bad
    row must not cost the whole analysis.
    """
    out: dict[str, dict[str, SymbolFacts]] = {}
    for pf in parsed or []:
        info = pf.get("file_info") if isinstance(pf, dict) else getattr(pf, "file_info", None)
        if info is None:
            continue
        path = info.get("path") if isinstance(info, dict) else getattr(info, "path", None)
        if not path:
            continue
        if changed_set is not None and path not in changed_set:
            continue
        symbols = pf.get("symbols") if isinstance(pf, dict) else getattr(pf, "symbols", None)
        bucket: dict[str, SymbolFacts] = {}
        for sym in symbols or []:
            facts = _facts(sym, path)
            if facts is not None:
                bucket[facts.name] = facts
        if bucket:
            out[path] = bucket
    return out


def _facts(sym: Any, path: str) -> SymbolFacts | None:
    get = sym.get if isinstance(sym, dict) else lambda k, d=None: getattr(sym, k, d)
    name = get("name")
    kind = str(get("kind") or "")
    if not name or kind not in REPORTED_KINDS:
        return None
    # A method's qualified id carries its class ("f.py::Cls::method"); the
    # graph's call edges are keyed on that id, so prefer the parser's own id.
    symbol_id = str(get("id") or f"{path}::{name}")
    try:
        start = int(get("start_line") or 0)
        end = int(get("end_line") or 0)
    except (TypeError, ValueError):
        return None
    return SymbolFacts(
        symbol_id=symbol_id,
        name=str(name),
        kind=kind,
        signature=str(get("signature") or ""),
        start_line=start,
        end_line=end,
    )


def caller_index(graph: dict) -> dict[str, list[str]]:
    """``{callee_symbol_id: [caller_symbol_id, ...]}`` from the graph's ``calls`` edges.

    A node-link graph holds one node per file *and* one per extracted symbol,
    with ``edge_type`` on every edge. Only ``calls`` edges are read here;
    ``imports`` edges are file-level reach's business. Edges written by an
    indexer old enough to predate ``edge_type`` are accepted when both endpoints
    look like symbol ids, so an old snapshot degrades to fewer callers rather
    than to none.
    """
    index: dict[str, list[str]] = {}
    for edge in graph.get("links") or graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src, tgt = edge.get("source"), edge.get("target")
        if src is None or tgt is None:
            continue
        src, tgt = str(src), str(tgt)
        edge_type = edge.get("edge_type") or edge.get("type")
        if edge_type is None:
            if "::" not in src or "::" not in tgt:
                continue
        elif edge_type != "calls":
            continue
        index.setdefault(tgt, []).append(src)
    return index


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    lo, hi = (start, end) if start <= end else (end, start)
    return any(lo <= b and a <= hi for a, b in ranges)


def analyze_contract_impact(
    *,
    graph: dict,
    base_parsed: Any,
    head_parsed: Any,
    changed_set: set[str],
    added_ranges_by_file: dict[str, list[tuple[int, int]]],
    base_is_snapshot: bool = False,
    callers_per_symbol: int | None = None,
) -> ContractImpact:
    """Classify each changed symbol and resolve its callers.

    ``base_parsed`` / ``head_parsed`` are lists of ``ParsedFile``-shaped objects
    or of ``parsed_files.json`` rows (mixed is fine). Only files in
    ``changed_set`` are compared -- a symbol in an untouched file cannot have
    been changed here.

    ``callers_per_symbol`` caps the rendered ``outside_callers`` list. It
    defaults to ``None`` (the full population); ``callers_total`` and
    ``outside_callers_total`` are computed before any cap bites.
    """
    base = symbol_index(base_parsed, changed_set)
    head = symbol_index(head_parsed, changed_set)
    callers = caller_index(graph)

    changes: list[SymbolContractChange] = []
    for path in sorted(changed_set):
        base_syms = base.get(path, {})
        head_syms = head.get(path, {})
        if not base_syms and not head_syms:
            continue
        ranges = added_ranges_by_file.get(path, [])

        for name, facts in sorted(head_syms.items()):
            prior = base_syms.get(name)
            sig_effect: str | None = None
            sig_reason: str | None = None
            if prior is None:
                # A file with no base side at all (added by this change, or
                # missing from the snapshot) would otherwise report every symbol
                # as "added" -- true but useless. Only report added symbols when
                # there IS a base side to have added them to.
                if not base_syms:
                    continue
                change = CHANGE_ADDED
            elif prior.signature != facts.signature:
                effect, reason = classify_signature_change(
                    prior.signature, facts.signature, facts.kind
                )
                if effect == EFFECT_NONE:
                    if _overlaps(facts.start_line, facts.end_line, ranges):
                        change = CHANGE_BODY
                    else:
                        continue
                else:
                    change = CHANGE_SIGNATURE
                    sig_effect = effect
                    sig_reason = reason
            elif _overlaps(facts.start_line, facts.end_line, ranges):
                change = CHANGE_BODY
            else:
                continue
            changes.append(
                _with_callers(
                    path,
                    facts,
                    change,
                    callers,
                    changed_set,
                    callers_per_symbol,
                    signature_effect=sig_effect,
                    signature_reason=sig_reason,
                )
            )

        for name, facts in sorted(base_syms.items()):
            if name not in head_syms:
                changes.append(
                    _with_callers(
                        path,
                        facts,
                        CHANGE_REMOVED,
                        callers,
                        changed_set,
                        callers_per_symbol,
                    )
                )

    changes.sort(key=_rank)
    return ContractImpact(
        changes=changes,
        status="available",
        reason=None,
        base_is_snapshot=base_is_snapshot,
    )


_CHANGE_ORDER = {CHANGE_REMOVED: 0, CHANGE_SIGNATURE: 1, CHANGE_ADDED: 2, CHANGE_BODY: 3}


def _rank(c: SymbolContractChange) -> tuple:
    """Breaking changes first, then by how many outside callers they reach."""
    return (
        not c.is_breaking,
        _CHANGE_ORDER.get(c.change, 9),
        -c.outside_production_callers_total,
        -c.outside_callers_total,
        c.file,
        c.name,
    )


def _with_callers(
    path: str,
    facts: SymbolFacts,
    change: str,
    callers: dict[str, list[str]],
    changed_set: set[str],
    cap: int | None,
    signature_effect: str | None = None,
    signature_reason: str | None = None,
) -> SymbolContractChange:
    inside = 0
    outside_prod: list[str] = []
    outside_test: list[str] = []
    for caller in callers.get(facts.symbol_id, ()):
        caller_file = _containing_file(caller)
        if caller_file in changed_set:
            inside += 1
        elif is_test_related_path(caller_file):
            outside_test.append(caller)
        else:
            outside_prod.append(caller)

    outside_prod.sort()
    outside_test.sort()
    outside = outside_prod + outside_test

    capped_outside = outside if cap is None else outside[:cap]
    capped_prod = [c for c in capped_outside if not is_test_related_path(_containing_file(c))]
    capped_test = [c for c in capped_outside if is_test_related_path(_containing_file(c))]

    return SymbolContractChange(
        file=path,
        name=facts.name,
        symbol_id=facts.symbol_id,
        kind=facts.kind,
        change=change,
        start_line=facts.start_line,
        end_line=facts.end_line,
        signature_effect=signature_effect,
        signature_reason=signature_reason,
        outside_callers=capped_outside,
        outside_production_callers=capped_prod,
        outside_test_callers=capped_test,
        inside_caller_count=inside,
        callers_total=inside + len(outside),
        outside_callers_total=len(outside),
        outside_production_callers_total=len(outside_prod),
        outside_test_callers_total=len(outside_test),
    )


def unavailable_contract_impact(reason: str) -> ContractImpact:
    """An honest empty result when the inputs for this lane were not supplied."""
    return ContractImpact(changes=[], status="unavailable", reason=reason)
