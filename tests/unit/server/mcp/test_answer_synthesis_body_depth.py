"""Calibration: synthesis must see the same body depth the response inlines.

Dogfood 2026-07-11 item 4: get_answer returned confidence=low with "not
determinable from the provided excerpts" while its own attached ``symbol_bodies``
contained the answer. Root cause: the hydrator fed synthesis a 40-line
``source_excerpt`` (``_MATCHED_SYMBOL_SOURCE_LINES``) but the response inlined a
120-line body (``_INLINE_BODY_MAX_LINES``) — so a docstring-heavy definition was
cut off before its answer-bearing logic ever reached the LLM, even though that
logic was served to the agent.

This locks the fix at the hydration layer (deterministic, no LLM): the top
question-matched symbol's ``source_excerpt`` must reach the inline-body depth so
the LLM reasons over what the agent gets.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.config import (
    _MATCHED_SYMBOL_SOURCE_LINES,
    _SYNTH_FULL_BODY_MAX_SYMBOLS,
)
from repowise.server.mcp_server.tool_answer.symbols import _hydrate_symbols_for_hits

# A definition longer than the old 40-line synthesis cap, with a docstring
# heavy enough to fill it and the answer-bearing return dict past line 40. The
# unique marker sits at ~line 60 so its presence in source_excerpt proves the
# fuller read happened.
_MARKER = "RANGE_READ_RESPONSE_KEYS"
_DOCSTRING_FILLER = "\n".join(f"    docstring line {i} explaining behaviour." for i in range(45))
_SOURCE = f'''\
def _resolve_range_read(spec):
    """Serve a live, bounded line-range read.

{_DOCSTRING_FILLER}
    """
    start, end = spec
    source = _slice(start, end)
    return {{
        # {_MARKER}: the keys a live range read returns
        "source": source,
        "start_line": start,
        "end_line": end,
        "verified": True,
    }}
'''


@pytest.fixture
def repo_with_body(tmp_path):
    """A real file on disk plus the matching WikiSymbol row + hit + ctx."""
    src_file = tmp_path / "tool_symbol.py"
    src_file.write_text(_SOURCE, encoding="utf-8")
    total_lines = _SOURCE.count("\n") + 1
    assert total_lines > _MATCHED_SYMBOL_SOURCE_LINES, "fixture must exceed the old cap"
    return SimpleNamespace(path=tmp_path, total_lines=total_lines)


async def test_matched_symbol_source_reaches_inline_body_depth(
    session, repo_id, repo_with_body
) -> None:
    row = WikiSymbol(
        id="rr1",
        repository_id=repo_id,
        file_path="tool_symbol.py",
        symbol_id="tool_symbol.py::_resolve_range_read",
        name="_resolve_range_read",
        qualified_name="tool_symbol._resolve_range_read",
        kind="function",
        signature="def _resolve_range_read(spec)",
        start_line=1,
        end_line=repo_with_body.total_lines,
        docstring="Serve a live, bounded line-range read.",
        visibility="private",
        is_async=False,
        complexity_estimate=3,
        language="python",
        parent_name=None,
    )
    session.add(row)
    await session.commit()

    hits = [{"target_path": "tool_symbol.py", "page_type": "file_page"}]
    ctx = SimpleNamespace(path=repo_with_body.path)

    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"_resolve_range_read"}
    )

    syms = hits[0]["symbols"]
    matched = next(s for s in syms if s["name"] == "_resolve_range_read")
    assert matched["_matched"] is True
    excerpt = matched["source_excerpt"]
    # The answer-bearing return dict is past line 40; the old 40-line cap
    # dropped it and synthesis hedged. The fuller read must now carry it.
    assert _MARKER in excerpt
    assert '"verified": True' in excerpt
    assert excerpt.count("\n") + 1 >= 50


async def test_class_flood_does_not_upgrade_every_sibling(
    session, repo_id, tmp_path
) -> None:
    """A class-name flood must not read a fuller body for every sibling method.

    When the question names a class, each method 'matches' through the parent's
    qualified name. Upgrading all of them to the inline-body depth would balloon
    the synthesis prompt; only the leading few earn the fuller read.
    """
    method_body = "\n".join(f"        x{i} = {i}" for i in range(60))
    lines: list[str] = ["class Coupling:"]
    starts: list[int] = []
    for j in range(5):
        starts.append(len(lines) + 1)  # 1-indexed line of the def
        lines.append(f"    def m{j}(self):")
        lines.extend(method_body.splitlines())
        lines.append("        return x0")
    src = "\n".join(lines) + "\n"
    (tmp_path / "coupling.py").write_text(src, encoding="utf-8")

    rows = [
        WikiSymbol(
            id=f"coupling-m{j}",
            repository_id=repo_id,
            file_path="coupling.py",
            symbol_id=f"coupling.py::Coupling.m{j}",
            name=f"m{j}",
            qualified_name=f"coupling.Coupling.m{j}",
            kind="method",
            signature=f"def m{j}(self)",
            start_line=starts[j],
            end_line=starts[j] + 61,
            docstring="",
            visibility="public",
            is_async=False,
            complexity_estimate=1,
            language="python",
            parent_name="Coupling",
        )
        for j in range(5)
    ]
    for r in rows:
        session.add(r)
    await session.commit()

    hits = [{"target_path": "coupling.py", "page_type": "file_page"}]
    ctx = SimpleNamespace(path=tmp_path)
    # "coupling" (len>=5) substring-matches every method's qualified_name → the
    # class-name flood: all 5 methods count as matched.
    await _hydrate_symbols_for_hits(session, repo_id, hits, ctx, question_ids={"Coupling"})

    syms = [s for s in hits[0]["symbols"] if s.get("source_excerpt")]
    assert len(syms) == 5, "the flood should match every sibling method"
    # Non-upgraded matched symbols keep the ~40-line excerpt (41 after the cap's
    # inclusive slice); only the leading few get the deeper read.
    fuller = [
        s for s in syms if s["source_excerpt"].count("\n") + 1 > _MATCHED_SYMBOL_SOURCE_LINES + 1
    ]
    assert len(fuller) <= _SYNTH_FULL_BODY_MAX_SYMBOLS
