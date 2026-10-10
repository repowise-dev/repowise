"""Order the tests a selection runs, the ones likeliest to fail first.

:func:`~.test_selection.select_tests` decides which tests a change needs; this
module only orders them, so a runner told to stop at the first failure reaches
a real breakage sooner. It never adds or drops a selected test. Each test is
ordered by, in turn:

1. tier: a test the change edits; one the per-test map names for a changed
   line; one it names for a changed file; one whose calls or fixtures reach a
   changed file, or that imports it directly (one hop); one reaching a changed
   file through other files; one that runs with every subset. Tests that run
   with every subset come last among the selected because nothing links them
   to this change: they are there because the graph cannot see into them, and
   a head meant to fail fast should be the tests evidence points at. With
   *everything* (:func:`rank_selection`) the unselected tests follow.
2. graph distance: fewer hops first, when the collection measured them.
3. co-change: the commits that touched both the test and a changed file, read
   from the partners the indexer stored per file. Both ends are read, since
   each file keeps only its strongest partners.
4. a failure in the last local pytest run (pytest's own last-failed cache).
5. how many changed files the test reaches, then its path.

No model and no score: every key is a count a reader can check, and
:attr:`RankedTest.reason` spells them out.

Cost: one indexed query of two columns over the changed files and the ranked
test files (batched, a few thousand rows on a large repository), one small
file read for the last-failed cache, and a sort. No walk, git call or source
read.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .test_selection import Selection, is_runnable_test, selected_by_change

#: Tiers, strongest first. ``rest`` is a test the selection left out.
TIERS = ("changed-test", "line", "file", "direct", "transitive", "every-subset", "rest")
_TIER_INDEX = {tier: i for i, tier in enumerate(TIERS)}
_TIER_WHY = {
    "changed-test": "the change edits it",
    "line": "the per-test map runs a changed line",
    "file": "the per-test map runs a changed file",
    "direct": "its calls, a fixture or a direct import reach a changed file",
    "transitive": "reaches a changed file through other files",
    "every-subset": "runs with every subset",
    "rest": "not selected",
}
# Vias where the test's own calls or fixtures reach the changed file (the call
# walk takes a few hops; still nearer than an import closure).
_DIRECT_VIAS = frozenset({"call-graph", "conftest-fixture"})

#: Where pytest records the node ids that failed in its last run. Its ids are
#: relative to pytest's rootdir, so a rootdir other than the repository root
#: matches nothing. Ceiling: a ``cache_dir`` moved in pytest's config is not
#: followed, and no other runner's results are read; a runner's own results
#: file is the upgrade.
LAST_FAILED_PATH = ".pytest_cache/v/cache/lastfailed"


@dataclass(frozen=True)
class RankedTest:
    """One test in run order, and the counts that placed it there."""

    __test__ = False  # not a pytest class, whatever its name says

    test: str
    tier: str
    hops: int | None = None
    co_change: int = 0
    failed: bool = False
    reach: int = 0

    @property
    def reason(self) -> str:
        parts = [_TIER_WHY[self.tier]]
        if self.hops is not None:
            parts.append(f"{self.hops} hop(s)")
        if self.co_change:
            parts.append(f"{self.co_change} shared change(s) with the changed files")
        if self.failed:
            parts.append("failed in the last run")
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "tier": self.tier,
            "hops": self.hops,
            "co_change": self.co_change,
            "failed_last_run": self.failed,
            "reason": self.reason,
        }


def suite(tracked: Iterable[str], roots: Any = None) -> list[str]:
    """Every runnable test among *tracked*, the set the selection expands scopes to."""
    return sorted(p for p in tracked if is_runnable_test(p, roots))


def last_failed(text: str | None) -> frozenset[str]:
    """Node ids (or files) pytest's last-failed cache names; empty when unreadable."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return frozenset()
    return frozenset(k for k, v in data.items() if v) if isinstance(data, dict) else frozenset()


def co_change_counts(
    changed: Collection[str], partners: Mapping[str, Sequence[Any]]
) -> dict[str, int]:
    """``{file: shared changes with the changed files}``, summed over each changed file.

    *partners* maps a file to its :class:`~repowise.core.co_change.CoChangePartner`
    list. A pair recorded at both ends counts once. Ceiling: each file stores
    only its 25 strongest partners, so a weaker pair recorded at neither end
    counts 0.
    """
    changed = set(changed)
    pairs: dict[tuple[str, str], int] = {}
    for owner, records in partners.items():
        for p in records:
            if key := _pair(owner, p.file_path, changed):
                # No plain count on an older index: its recency-decayed weight stands in.
                pairs[key] = max(pairs.get(key, 0), p.support or round(p.weight))
    out: dict[str, int] = {}
    for (_, other), count in pairs.items():
        out[other] = out.get(other, 0) + count
    return out


def _pair(a: str, b: str, changed: set[str]) -> tuple[str, str] | None:
    """``(changed file, other file)`` when exactly one of *a* and *b* changed."""
    if (a in changed) == (b in changed):
        return None
    return (a, b) if a in changed else (b, a)


def _lift(best: dict[str, int], test_file: str | None, tier: str) -> None:
    if test_file:
        best[test_file] = min(best.get(test_file, len(TIERS)), _TIER_INDEX[tier])


def _covered_tier(sources: set[str], line_matched: set[str], changed: set[str]) -> str:
    if sources & line_matched:
        return "line"
    return "file" if sources & changed else "transitive"


def _graph_tier(row: Mapping[str, str], changed: set[str], hops: Mapping[str, int]) -> str:
    if row["source_file"] not in changed:
        return "transitive"  # a route to the change, not a changed file
    if row["via"] == "changed-test":
        return "changed-test"
    if row["via"] in _DIRECT_VIAS or hops.get(row["test_file"]) == 1:
        return "direct"
    return "transitive"


def tiers_of(
    result: Mapping[str, Any],
    selection: Selection,
    line_matched: Collection[str],
    hops: Mapping[str, int] | None = None,
) -> dict[str, str]:
    """``{selected test file: its tier}`` from the collection *result*.

    *line_matched* are the changed files the per-test map was asked by line.
    A test several changed files reach keeps its strongest tier. Ceiling: a
    file's node ids share its tier; ordering them apart needs a tier per id.
    """
    changed, by_line = set(selection.basis), set(line_matched)
    best: dict[str, int] = {}
    for info in (result.get("covered") or {}).values():
        sources = set(info.get("source_files") or ())
        _lift(best, info.get("test_file"), _covered_tier(sources, by_line, changed))
    for row in result.get("inferred") or ():
        _lift(best, row["test_file"], _graph_tier(row, changed, hops or {}))
    return {t: _tier(t, best, selection) for t in selection.test_files}


def _tier(test: str, best: Mapping[str, int], selection: Selection) -> str:
    """*test*'s strongest evidence; with none, whether a changed file or a rule put it in."""
    if test in best:
        return TIERS[best[test]]
    return "transitive" if selected_by_change(selection, test) else "every-subset"


def _reach(result: Mapping[str, Any], changed: Collection[str]) -> dict[str, int]:
    """``{test file: changed files it reaches}``."""
    seen: dict[str, set[str]] = {}
    for row in result.get("inferred") or ():
        seen.setdefault(row["test_file"], set()).add(row["source_file"])
    for info in (result.get("covered") or {}).values():
        if info.get("test_file"):
            seen.setdefault(info["test_file"], set()).update(info.get("source_files") or ())
    changed = set(changed)
    return {t: len(s & changed) for t, s in seen.items()}


def rank(
    tests: Iterable[str],
    tiers: Mapping[str, str],
    *,
    hops: Mapping[str, int] | None = None,
    co_change: Mapping[str, int] | None = None,
    failed: Collection[str] = frozenset(),
    reach: Mapping[str, int] | None = None,
) -> list[RankedTest]:
    """*tests* (files or node ids) in run order; a file *tiers* lacks is ``rest``."""
    hops, co_change, reach = hops or {}, co_change or {}, reach or {}
    failed_files = {f.split("::", 1)[0] for f in failed}
    ranked = []
    for test in dict.fromkeys(tests):
        path = test.split("::", 1)[0]
        ranked.append(
            RankedTest(
                test=test,
                tier=tiers.get(path, "rest"),
                hops=hops.get(path),
                co_change=co_change.get(path, 0),
                # A node id fails only on its own; another test in its file says nothing.
                failed=test in failed or ("::" not in test and path in failed_files),
                reach=reach.get(path, 0),
            )
        )

    def key(r: RankedTest) -> tuple:
        far = r.hops if r.hops is not None else sys.maxsize
        return _TIER_INDEX[r.tier], far, -r.co_change, not r.failed, -r.reach, r.test

    return sorted(ranked, key=key)


@dataclass(frozen=True)
class Signals:
    """History behind the order: co-change counts per file and last-run failures."""

    co_change: Mapping[str, int]
    failed: frozenset[str] = frozenset()


def rank_selection(
    result: Mapping[str, Any],
    selection: Selection,
    line_matched: Collection[str],
    signals: Signals,
    *,
    hops: Mapping[str, int] | None = None,
    everything: Sequence[str] | None = None,
) -> list[RankedTest]:
    """``selection.tests`` in run order, then, given *everything* (the suite), the rest.

    In a whole-suite order a file the selection runs only some node ids of is
    run whole at its first id's place: pytest given ``a.py::t1 a.py`` runs t1
    twice, and the file's other ids are not known here.
    """
    tiers = tiers_of(result, selection, line_matched, hops)
    common = {
        "hops": hops,
        "co_change": signals.co_change,
        "failed": signals.failed,
        "reach": _reach(result, selection.basis),
    }
    ranked = rank(selection.tests, tiers, **common)
    if everything is None:
        return ranked
    head: dict[str, RankedTest] = {}
    for r in ranked:
        path = r.test.split("::", 1)[0]
        head.setdefault(path, replace(r, test=path))
    rest = rank([t for t in everything if t not in head], {}, **common)
    return [*head.values(), *rest]


def ordered(
    selection: Selection, ranked: Sequence[RankedTest], *, whole: bool = False
) -> Selection:
    """*selection* with its tests in *ranked* order; with *whole*, every ranked test.

    A whole-suite order is a subset of nothing: ``run_all`` turns false so a
    runner gets the list, and the reasons still say why every test runs.
    """
    tests = tuple(r.test for r in ranked)
    if not whole:
        # A node id a whole-suite order folded into its file sorts at the file's place.
        at: dict[str, int] = {}
        for i, test in enumerate(tests):
            at.setdefault(test, i)
        last = len(at)
        tests = tuple(
            sorted(selection.tests, key=lambda t: at.get(t, at.get(t.split("::", 1)[0], last)))
        )
    # Every selected file has an entry in ``tests``, so this keeps them all.
    files = tuple(dict.fromkeys(t.split("::", 1)[0] for t in tests))
    run_all = False if whole else selection.run_all
    return replace(selection, run_all=run_all, tests=tests, test_files=files)


async def read_signals(
    session: Any, repo_id: str, changed: Collection[str], tests: Iterable[str], read: Any
) -> Signals:
    """Co-change counts between *changed* and *tests*, and the last run's failures.

    *read* returns a repository file's text or ``None`` (a checkout's reader);
    a server without the working tree gets no failures, never an error.
    """
    from ..persistence.crud import get_co_change_partners

    files = {t.split("::", 1)[0] for t in tests}
    partners = await get_co_change_partners(session, repo_id, sorted({*changed, *files}))
    return Signals(co_change_counts(changed, partners), last_failed(read(LAST_FAILED_PATH)))


# The collection's fewest hops from each reached test file to the change, once
# it records them; absent, distance does not order and tiers decide.
_HOPS_KEY = "test_hops"


async def rank_change(
    session: Any,
    repo_id: str,
    change: Any,
    result: Mapping[str, Any],
    selection: Selection,
    read: Any,
    *,
    everything: Sequence[str] | None = None,
) -> list[RankedTest]:
    """:func:`rank_selection` for *change*, with its signals read; no *session* reads none.

    *result* is :func:`~.test_collection.collect`'s, *read* a checkout's reader.
    """
    from .test_collection import query_lines

    lines = query_lines(change, result.get("measured_commit"))
    tests = [*selection.tests, *(everything or ())]
    if session is None:
        signals = Signals({}, last_failed(read(LAST_FAILED_PATH)))
    else:
        signals = await read_signals(session, repo_id, selection.basis, tests, read)
    return rank_selection(
        result,
        selection,
        {p for p, touched in lines.items() if touched},
        signals,
        hops=result.get(_HOPS_KEY),
        everything=everything,
    )
