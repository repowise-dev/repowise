"""Cross-file oracle resolution for ``assertion_free_test``.

A test with no oracle of its own -- ``asserts/predicate.checks_something``
answers that, and it is four questions rather than an assertion count -- has
still checked something if it handed the job to a helper that has one. ``_asserting_names`` resolves that
within one file; a JS/TS suite keeps its helpers in another module and a
class-based suite inherits them from a base class, so the same delegation is
invisible to a same-file rule by construction.

What this reaches is bounded by ``BINDING_ORIGINS``. An inherited helper called
through ``self`` binds, which is the shape that moved Python. A Go
package-level helper does **not**: its origins are ``same_package`` and
``package_alias``, neither of which is admitted here, and Go does not ship this
marker anyway. ``super().test_x(...)`` does not either, because the resolver
has no ``super()`` receiver handling at all.

This pass answers it from the call graph instead of from names.

Two lanes, because a test case is not always a graph node
---------------------------------------------------------
A Python ``def test_x`` is a declared symbol, so the graph has a node for it and
its call edges are attributed to it. A JS/TS test case is an anonymous callback
handed to ``it(...)``; the graph has **no node for it at all**, and the calls
inside it are attributed to the enclosing module. Resolving from the module node
would make one delegating test suppress every other test in its file, so:

Resolution asks for the function, never for whatever encloses it:
``resolve_function``'s containment fallback exists to tolerate a decorator
offset, and read as identity it would answer one wrapper symbol for every
callback inside it. Declining falls through to the file lane, which is
per-function. The sentence above is therefore true of the shapes that reach the
file lane *because* of that bound, not independently of it.

* **call-edge** -- the test resolves to a symbol; walk its own outgoing edges.
  Precise attribution, bounded depth. Python, Go, Java.
* **file-edge** -- the test does not resolve; require both that *this file* has
  a resolved edge to the asserting symbol and that *this function's body* calls
  it by name **with no receiver**. The edge alone is file-scoped and the name
  alone is repo-scoped; together, on an unqualified call that the file's own
  imports bind, they are neither. One hop only, because it is already the
  product of two approximations and a second hop would compound them.

Each suppression records which lane answered, so the two are measured apart.

Precision
---------
**Suppression only ever happens on a resolved edge**, and only on an edge that
binds one definition. An unresolved call is indistinguishable from a call to
something that checks nothing, so a call the graph could not bind suppresses
nothing and the marker fires. That is the tolerable direction for an advisory
marker: a missing edge leaves a false positive, a wrong edge hides a real
finding.

The edges are filtered harder than ``ExecutionGraphIndex`` filters them --
see ``BINDING_ORIGINS`` -- because that index is tuned for performance
reachability, where an extra edge costs a cheap false positive, and here it
costs a hidden one.

Why this needs nothing persisted
--------------------------------
An incremental run walks the changed files plus
``ExecutionGraphIndex.affected_files``' bounded closure around them, and the
closure's files are walked in full (``_performance_only_config`` disables their
detectors, not the walk). The edges this pass reads are a **subset** of the ones
that closure walks, so any callee it can resolve is a callee whose file was
walked and whose assertion count is in hand. If the edge is missing, neither the
closure nor this pass can see it and the marker fires either way. So an edited
test file is judged identically by ``init`` and by ``update`` (issue #1484), and
there is no repo-wide quantity to store. The subset relation holds only while
``max_depth`` stays within the closure's forward bound, which is what
``CLOSURE_FORWARD_DEPTH`` records and a test pins.

**What that argument does not cover is the reverse edit.** This marker's verdict
is now a function of another file's contents, and an incremental run re-reports
only the files in its own changed set. A helper that loses its assertions leaves
every test delegating to it stale until that test's own file is next edited: the
closure walks those files, but ``_performance_only_config`` disables a
``test_quality`` detector on them and the report is filtered back to the changed
set afterwards. The perf passes this one is modelled on do not have that gap,
because ``performance`` is the dimension both of those filters make an exception
for. It is tolerable for an advisory marker that never scores, and the honest
statement is that the staleness is bounded by the next edit to the test, not
that it cannot arise.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....test_paths import is_test_related_path
from ...execution_graph import ExecutionGraphIndex, file_of_symbol, reachable_to_sink
from ..coverage import is_test_file
from .predicate import checks_something

if TYPE_CHECKING:
    from ..complexity import FileComplexity

#: ``ExecutionGraphIndex.affected_files`` takes a forward closure of this depth
#: when an incremental run decides which files to walk. Resolving deeper than it
#: walks is what would make ``update`` disagree with ``init``, so this is a
#: ceiling rather than a default.
CLOSURE_FORWARD_DEPTH = 3

#: Hops from the test to the asserting function on the call-edge lane. One
#: covers the measured shape -- a test calling a helper that asserts -- and two
#: covers a helper that delegates once more. Every extra hop widens what gets
#: suppressed, and suppression is this rule's failure mode, so it sits below the
#: ceiling above.
DEFAULT_MAX_DEPTH = 2

#: Resolution origins that bind a call to **one** definition. Everything the
#: resolver reaches by matching a bare name against a set of candidates is
#: absent, however high its confidence: ``import_merged`` (0.85) means only
#: "a function of this name exists in one of the files this file imports", and
#: it was measured resolving ``Silent().check()`` to ``Asserting.check`` because
#: the receiver could not be typed. That is a wrong edge hiding a real finding,
#: which is the one failure this rule must not have.
BINDING_ORIGINS = frozenset(
    {
        "same_file",
        "enclosing_class",
        "self_scope",
        "self_inherited",
        "import_scoped",
        "module_alias",
        "receiver_same_file",
        "receiver_typed_same_file",
        "receiver_typed_same_package",
        "receiver_typed_import",
        "return_type_same_file",
        "return_type_same_package",
        "return_type_import",
    }
)


@dataclass(frozen=True, slots=True)
class OracleReach:
    """Which asserting function answered for a test, and how it was reached."""

    #: Graph node id of the asserting function, ``"{path}::{name}"``.
    oracle: str
    #: Its bare name, for reporting.
    oracle_name: str
    #: Call-graph hops from the test to it. 1 on the file-edge lane.
    distance: int
    #: Which lane answered: ``"call-edge"`` or ``"file-edge"``.
    basis: str


def collect_cross_file_oracles(
    walked: Iterable[tuple[Any, FileComplexity]],
    graph: Any,
    *,
    index: ExecutionGraphIndex | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, dict[int, OracleReach]]:
    """Test cases whose calls reach a function that asserts, keyed by file path.

    The inner key is the test function's 1-indexed start line. Start line rather
    than name: a file's same-named functions are one graph node but distinct
    walked functions, and keying on the name would reintroduce the collision
    #2408 removed.

    Returns ``{}`` without a graph, without binding edges, or when no walked
    file holds both a candidate and an oracle -- never a guess.
    """
    walked_list = list(walked)
    if graph is None or not walked_list or max_depth < 1:
        return {}

    # Cheap pre-check over facts the walk already computed, before touching the
    # graph: the work needs a test that checks nothing. A repo with none
    # returns without building an index or reading an edge.
    if not any(_candidates(pf, fcx) for pf, fcx in walked_list):
        return {}

    if index is None:
        index = ExecutionGraphIndex(graph)

    forward = _binding_adjacency(graph)
    if not forward:
        return {}
    reverse: dict[str, list[str]] = {}
    for caller, callees in forward.items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)

    # Sinks: functions that assert, restricted to test and test-support files.
    # Without that gate a bare ``assert`` in production code is an oracle, and
    # a test calling any such function would be suppressed -- a false
    # suppression, the one direction this rule must not fail in.
    oracle_name: dict[str, str] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        if not is_test_related_path(path, pf.file_info.language):
            continue
        for fn in fcx.functions:
            if not checks_something(fn):
                continue
            sid = index.resolve_function(path, fn.start_line, func_end=fn.end_line)
            if sid is not None:
                oracle_name.setdefault(sid, fn.name)
    if not oracle_name:
        return {}

    reach = reachable_to_sink(
        oracle_name.keys(),
        lambda node: reverse.get(node, ()),
        max_depth=max_depth,
    )
    # file-edge lane: every binding edge this file has, by callee name.
    by_file_name = _oracles_by_file_and_name(forward, oracle_name)

    out: dict[str, dict[int, OracleReach]] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        for fn in _candidates(pf, fcx):
            found = _resolve_one(path, fn, index, reach, oracle_name, by_file_name)
            if found is not None:
                out.setdefault(path, {})[fn.start_line] = found
    return out


def _resolve_one(
    path: str,
    fn: Any,
    index: ExecutionGraphIndex,
    reach: dict[str, Any],
    oracle_name: dict[str, str],
    by_file_name: dict[str, dict[str, set[str]]],
) -> OracleReach | None:
    # ``func_end`` asks for this function rather than for whatever encloses it.
    # The containment fallback answers the innermost symbol spanning the start
    # line, and a symbol wrapped around several test callbacks spans all of
    # them, so without the bound one delegating test would resolve to the same
    # node as its silent siblings and suppress them. Unresolved here falls
    # through to the file-edge lane, whose two conjuncts are per-function.
    sid = index.resolve_function(path, fn.start_line, func_end=fn.end_line)
    if sid is not None:
        info = reach.get(sid)
        # ``distance == 0`` means this node IS an oracle, which happens when a
        # same-named sibling in the file asserts and the two share a node id.
        # That is the same-file case ``_asserting_names`` already answers by
        # name; claiming it here would report a resolution that did not happen.
        if info is not None and info.distance >= 1:
            return OracleReach(
                oracle=info.sink,
                oracle_name=oracle_name.get(info.sink, ""),
                distance=info.distance,
                basis="call-edge",
            )
        # A test that IS a graph node and reaches no oracle has been answered.
        # Falling through to the file lane would let a sibling's edge speak for
        # it, which is exactly the over-suppression that lane exists to avoid.
        return None

    # file-edge lane, for a test case the graph has no node for.
    named = by_file_name.get(path)
    if not named:
        return None
    # Unqualified call sites only. ``called_names`` drops the receiver, so
    # ``harness.checkOk()`` would otherwise pair a method on an unrelated
    # object with the file's edge to an imported ``checkOk`` -- letting one
    # delegating test license every same-named call beside it, which is the
    # over-suppression this lane's two conjuncts exist to prevent. Sorted so
    # the oracle recorded for a test calling two of them is reproducible.
    for called in sorted(fn.bare_called_names):
        targets = named.get(called)
        if not targets:
            continue
        oracle = sorted(targets)[0]
        return OracleReach(
            oracle=oracle,
            oracle_name=oracle_name.get(oracle, ""),
            distance=1,
            basis="file-edge",
        )
    return None


def _binding_adjacency(graph: Any) -> dict[str, tuple[str, ...]]:
    """Caller -> callees, over call edges that bind exactly one definition."""
    out: dict[str, list[str]] = {}
    try:
        edges = graph.edges(data=True)
    except Exception:
        return {}
    for source, target, data in edges:
        if data.get("edge_type") != "calls":
            continue
        if data.get("resolution_origin") not in BINDING_ORIGINS:
            continue
        out.setdefault(source, []).append(target)
    return {k: tuple(v) for k, v in out.items()}


def _oracles_by_file_and_name(
    forward: dict[str, tuple[str, ...]],
    oracle_name: dict[str, str],
) -> dict[str, dict[str, set[str]]]:
    """Per file, the asserting symbols it has a binding edge to, by bare name.

    Keyed by the callee's lowercased name so it meets ``called_names``, which
    the walker lowercases. Only oracles in another file are kept: a same-file
    one is ``_asserting_names``' job and reporting it here would claim a
    cross-file resolution that did not happen.
    """
    out: dict[str, dict[str, set[str]]] = {}
    for caller, callees in forward.items():
        caller_path = file_of_symbol(caller)
        for callee in callees:
            if callee not in oracle_name:
                continue
            callee_path = file_of_symbol(callee)
            if callee_path == caller_path:
                continue
            name = oracle_name[callee].lower()
            out.setdefault(caller_path, {}).setdefault(name, set()).add(callee)
    return out


def _candidates(pf: Any, fcx: FileComplexity) -> list[Any]:
    """Test cases in a test file that check nothing themselves."""
    if not is_test_file(pf.file_info.path):
        return []
    return [fn for fn in fcx.functions if fn.is_test_case and not checks_something(fn)]
