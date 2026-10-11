"""Trust contract: get_answer's hydrator must verify bounds before slicing.

The hydrator turns a stored ``WikiSymbol`` bound into live signature/body bytes
for ``key_symbols`` and ``symbol_bodies``. Historically it read the live file at
the stored ``start_line`` with no verification, so a drifted bound (an edit above
the def, or an index that lagged the working tree) produced a garbled signature
and a body that started mid-docstring, served as if fresh. These lock the fix:
the cheap bounds gate runs first, a re-parse relocates and heals a drifted row,
and an un-relocatable symbol falls back to the stored signature (self-consistent)
rather than a live slice at the wrong lines.

All deterministic — no LLM, no synthesis. The drift is created synthetically so
the test does not depend on a naturally stale index.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.symbols import _hydrate_symbols_for_hits

# A file whose real ``target_func`` def sits well below the line the index will
# claim: the stored row points into the import block (the classic post-#779 drift
# where inserted imports pushed every def down while bounds fossilized).
_DRIFTED_SOURCE = '''\
import os
import sys
from collections import defaultdict
from typing import Any

_CONST = 1


def target_func(a: int, b: int) -> int:
    """Add two numbers.

    A short body so the whole thing fits under the slice cap.
    """
    total = a + b
    return total
'''

_REAL_DEF_LINE = 9  # 1-indexed line of "def target_func"
_STORED_DRIFT_LINE = 3  # points at "from collections import defaultdict"


async def _hydrate_one(session, repo_id, tmp_path, *, session_factory=None):
    hits = [{"target_path": "mod.py", "page_type": "file_page"}]
    ctx = SimpleNamespace(path=tmp_path, session_factory=session_factory)
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"target_func"}
    )
    return hits[0].get("symbols") or []


def _target_row(repo_id, start_line, end_line=15):
    return WikiSymbol(
        id="drift1",
        repository_id=repo_id,
        file_path="mod.py",
        symbol_id="mod.py::target_func",
        name="target_func",
        qualified_name="mod.target_func",
        kind="function",
        signature="def target_func(a, b)",
        start_line=start_line,
        end_line=end_line,
        docstring="Add two numbers.",
        visibility="public",
        is_async=False,
        complexity_estimate=1,
        language="python",
        parent_name=None,
    )


async def test_drifted_bounds_are_relocated_not_garbled(session, repo_id, tmp_path):
    """A stored bound pointing into the import block serves the real def, not junk."""
    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    session.add(_target_row(repo_id, start_line=_STORED_DRIFT_LINE))
    await session.commit()

    syms = await _hydrate_one(session, repo_id, tmp_path)
    matched = next(s for s in syms if s["name"] == "target_func")

    assert matched["_matched"] is True
    # Signature is the real def, not the drifted import line it pointed at.
    assert "def target_func" in matched["signature"]
    assert "import" not in matched["signature"]
    # Entry bounds were corrected to the real def location.
    assert matched["start_line"] == _REAL_DEF_LINE
    # The body slice starts at the real def, not mid-import.
    assert matched["source_excerpt"].lstrip().startswith("def target_func")


async def test_corrected_row_is_healed(session, factory, repo_id, tmp_path):
    """When a session factory is available, the drifted row is healed in place."""
    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    session.add(_target_row(repo_id, start_line=_STORED_DRIFT_LINE))
    await session.commit()

    await _hydrate_one(session, repo_id, tmp_path, session_factory=factory)

    # The next serve should hit the cheap gate: the row now carries real bounds.
    async with factory() as s2:
        healed = (
            await s2.execute(select(WikiSymbol).where(WikiSymbol.id == "drift1"))
        ).scalar_one()
    assert healed.start_line == _REAL_DEF_LINE


async def test_unrelocatable_symbol_falls_back_to_stored_signature(
    session, repo_id, tmp_path
):
    """A symbol the live file no longer defines: serve the stored signature, no body."""
    # The file defines target_func, but the index row is for a symbol that was
    # renamed away — relocation cannot find it, so bounds are approximate.
    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    row = _target_row(repo_id, start_line=_STORED_DRIFT_LINE)
    row.name = "renamed_away"
    row.symbol_id = "mod.py::renamed_away"
    row.qualified_name = "mod.renamed_away"
    row.signature = "def renamed_away(a, b)"
    session.add(row)
    await session.commit()

    hits = [{"target_path": "mod.py", "page_type": "file_page"}]
    ctx = SimpleNamespace(path=tmp_path, session_factory=None)
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"renamed_away"}
    )
    matched = next(s for s in hits[0]["symbols"] if s["name"] == "renamed_away")

    # Stored signature is served verbatim (self-consistent); no garbled live slice.
    assert matched["signature"] == "def renamed_away(a, b)"
    assert "source_excerpt" not in matched


async def test_anchor_stashes_verified_bounds_and_heals(
    session, factory, repo_id, tmp_path
):
    """Tier-0 symbol anchoring must stash verified bounds, not the drifted ones."""
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    session.add(_target_row(repo_id, start_line=_STORED_DRIFT_LINE))
    await session.commit()

    hits: list[dict] = []
    hits, _homonyms = await _anchor_symbol_hits(
        session,
        repo_id,
        {"target_func"},
        hits,
        repo_root=tmp_path,
        session_factory=factory,
    )
    anchored = next(h for h in hits if h.get("_anchor_symbols"))
    stashed = anchored["_anchor_symbols"][0]
    assert stashed["name"] == "target_func"
    assert stashed["start_line"] == _REAL_DEF_LINE  # corrected, not the drift line

    async with factory() as s2:
        healed = (
            await s2.execute(select(WikiSymbol).where(WikiSymbol.id == "drift1"))
        ).scalar_one()
    assert healed.start_line == _REAL_DEF_LINE


def test_union_approx_def_routed_to_pointer_not_garbled_body(tmp_path):
    """An unverifiable union def is handed off as a get_symbol pointer, no slice."""
    from repowise.server.mcp_server.tool_answer.symbols import build_homonym_union_bodies

    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    union_groups = {
        "target_func": [
            {
                "file_path": "mod.py",
                "name": "target_func",
                "start_line": _STORED_DRIFT_LINE,
                "end_line": 5,
                "_approx": True,
            }
        ]
    }
    bodies, more = build_homonym_union_bodies(tmp_path, union_groups)
    assert bodies == []  # no live slice served for an unverified bound
    assert len(more) == 1
    assert more[0]["symbol_id"] == "mod.py::target_func"
    assert "get_symbol" in more[0]["hint"]


async def test_consistent_bounds_serve_live_without_reparse(session, repo_id, tmp_path):
    """The happy path: an accurate stored bound serves the live def unchanged."""
    (tmp_path / "mod.py").write_text(_DRIFTED_SOURCE, encoding="utf-8")
    session.add(_target_row(repo_id, start_line=_REAL_DEF_LINE))
    await session.commit()

    syms = await _hydrate_one(session, repo_id, tmp_path)
    matched = next(s for s in syms if s["name"] == "target_func")

    assert "def target_func" in matched["signature"]
    assert matched["start_line"] == _REAL_DEF_LINE
    assert matched["source_excerpt"].lstrip().startswith("def target_func")
