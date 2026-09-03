"""The lists P9 ranked stay ranked (the architecture half of P9).

An architecture check, not a behaviour one. The behaviour lives in
``tests/unit/generation/test_ranked_truncation.py`` and
``tests/unit/server/mcp/test_used_by_ranking.py``, which catch a *wrong*
order; this catches the ranking being **deleted** — the sort disappearing in a
refactor and leaving the cut behind, which is how all seven sites got here.
Every one of them was executed by tests that asserted nothing about it, so
removing the ranking would have been green.

**The first version of this file was vacuous on half its entries, and the way
it failed is the reason it now looks like this.** It walked each registered
function's AST for any call named ``sorted``/``sort``/``order_by`` and passed
if one existed anywhere in the body. ``assemble_module_page`` is 164 lines and
holds seven such calls; ``_resolve_one_target`` is 898 lines and holds five.
Deleting the P9 sort in either would have left this green. Worse,
``active_landscape``'s fix *removed* a ``set`` rather than adding a sort, so
that entry could not have failed for any edit to the code it named. So the
check now pins the ranking **expression**, not the presence of ranking.

That trades one weakness for another and the trade is deliberate: a snippet
check breaks on reformatting. When it does, the fix is to update the snippet
after confirming the ranking still happens — not to delete the entry. The
failure message says so, because a guard whose failure is confusing gets
deleted rather than read.

Why not a general "find every unranked slice" checker: the question is
undecidable from the AST. A list can be ranked three frames up, by a SQL
``ORDER BY``, or by a caller's contract, and a checker that flagged every
``[:N]`` would drown six real sites in the ~200 legitimate slices under
``packages/`` — string truncation, batch sizes, log samples. The census that
found these is in ``local-stash/structural-debt/PROGRESS.md``.
"""

from __future__ import annotations

import pathlib

import pytest

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"


class Cut:
    """One ranked truncation: what must be there, and what must not come back."""

    def __init__(self, site: str, rel: str, requires: str, why: str, forbids: str = "") -> None:
        self.site = site
        self.rel = rel
        self.requires = requires
        self.why = why
        #: The exact shape that shipped, where removing the fix would restore a
        #: recognisable idiom rather than just deleting a line.
        self.forbids = forbids

    def __repr__(self) -> str:  # pytest node id
        return self.site


_CUTS = [
    Cut(
        "extract_call_graph",
        "core/src/repowise/core/generation/context/graph_intelligence.py",
        'entries.sort(key=lambda e: (-float(e.get("confidence") or 0.0), e["caller"], e["callee"]))',
        "confidence, before MAX_CALL_ENTRIES",
        forbids="unique[:15]",
    ),
    Cut(
        "extract_heritage",
        "core/src/repowise/core/generation/context/graph_intelligence.py",
        "zip(scores, entries, strict=True)",
        "confidence, before MAX_HERITAGE_ENTRIES",
        forbids="entries[:10]",
    ),
    Cut(
        "assemble_module_page.key_classes",
        "core/src/repowise/core/generation/context/assembler.py",
        "for fc in sorted(file_contexts, key=lambda fc: (-fc.pagerank_score, fc.file_path)):",
        "the file's PageRank, before _MAX_KEY_CLASSES",
        forbids="for fc in file_contexts:\n            for h in fc.heritage",
    ),
    Cut(
        "get_graph_edges_for_node.callers",
        "core/src/repowise/core/persistence/crud/graph.py",
        "GraphEdge.confidence.desc(), GraphEdge.source_node_id, GraphEdge.edge_type",
        "edge confidence, before `limit`",
    ),
    Cut(
        "get_graph_edges_for_node.callees",
        "core/src/repowise/core/persistence/crud/graph.py",
        "GraphEdge.confidence.desc(), GraphEdge.target_node_id, GraphEdge.edge_type",
        "edge confidence, before `limit`",
    ),
    Cut(
        "pr_blast.all_affected_paths",
        "core/src/repowise/core/analysis/pr_blast.py",
        "all_affected_paths = sorted(changed_set | {e[\"path\"] for e in transitive_affected})",
        "path order, before test_gaps is cut to 3 as missing_tests",
        forbids="all_affected_paths = list(changed_set |",
    ),
    Cut(
        "pr_blast._transitive_affected",
        "core/src/repowise/core/analysis/pr_blast.py",
        "key=lambda item: (item[1], item[0])",
        "depth then path, before may_break takes 15",
        forbids="frontier = list(set(changed_files))",
    ),
    Cut(
        "active_landscape.dead_code_in_hot",
        "core/src/repowise/core/generation/onboarding/subkinds/active_landscape.py",
        "for hot in hot_files:",
        "the churn ranking itself, before the cut to 15",
        # The defect was not a missing sort but a ranking discarded by a set.
        forbids="hot_paths = {h.path for h in hot_files}",
    ),
    Cut(
        "used_by",
        "server/src/repowise/server/mcp_server/tool_context/targets.py",
        "sorted(best_rank, key=lambda p: (-best_rank[p], p))",
        "the source file's PageRank, before _MAX_USED_BY",
    ),
]


def _text(rel: str) -> str:
    path = _PACKAGES / rel
    assert path.is_file(), f"{rel} moved; update the registry in {__file__}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("cut", _CUTS, ids=repr)
def test_the_cut_is_still_ranked(cut: Cut) -> None:
    assert cut.requires in _text(cut.rel), (
        f"{cut.site} truncates a displayed list and the expression that ranks "
        f"it is gone. It must rank by {cut.why}.\n\nExpected to find:\n"
        f"  {cut.requires}\n\nIf the ranking still happens and only the "
        "formatting moved, update this entry. If the cut itself is gone, "
        "delete the entry. Do not delete it to make this pass."
    )


@pytest.mark.parametrize("cut", [c for c in _CUTS if c.forbids], ids=repr)
def test_the_shape_that_shipped_does_not_come_back(cut: Cut) -> None:
    """The unranked idiom itself, not just the absence of a sort.

    Two of these are the whole check: ``active_landscape``'s fix removed a
    ``set`` and added no sort, and ``pr_blast``'s frontier was a bare
    ``list(set(...))`` — neither leaves a ranking call for a presence test to
    find, so only the retired shape distinguishes fixed from reverted.
    """
    assert cut.forbids not in _text(cut.rel), (
        f"{cut.site} is back to the unranked shape `{cut.forbids}`, which "
        f"cuts a displayed list without ranking it by {cut.why}."
    )


def test_the_graph_extractors_cut_at_a_named_constant() -> None:
    """No bare literal back at the ``return``.

    The literals are what made the two cuts invisible: they read as an
    implementation detail rather than as a contract a test could hold. Pinned
    at the return lines rather than by searching the file for ``[:10]``, which
    would fail on any unrelated future slice.
    """
    text = _text("core/src/repowise/core/generation/context/graph_intelligence.py")
    assert "MAX_CALL_ENTRIES = 15" in text
    assert "MAX_HERITAGE_ENTRIES = 10" in text
    assert "return unique[:MAX_CALL_ENTRIES]" in text
    assert "ranked[:MAX_HERITAGE_ENTRIES]" in text


def test_every_registered_file_exists() -> None:
    """Registry rot: an entry naming a file that has moved passes silently.

    ``_text`` asserts, so this only fails when a path is stale — which is the
    state in which every other test here becomes an error rather than a check.
    """
    for cut in _CUTS:
        assert (_PACKAGES / cut.rel).is_file(), f"{cut.site}: {cut.rel} is gone"
