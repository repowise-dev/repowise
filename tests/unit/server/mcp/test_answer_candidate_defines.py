"""``_hydrate_candidate_defines``: what each ranked file declares, and in what order.

`candidates` names up to 20 files. Until this, it said nothing about any of
them, so an agent handed `django/shortcuts.py` had exactly one move available
and that move was a Grep. Measured on the 25 flow questions: 434 of the 499
paths a get_answer response served carried no content at all, and the Layer B
taxonomy judged 89% of the agent's post-answer searches to be exactly that
expansion from a bare name.

The selection rules are what make this cheap enough to ship at 6 names per file:
question-named symbols first (they are what the agent came for), then by
declaration kind, then by position, with imports and private names dropped.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.config import _DEFINES_PER_CANDIDATE
from repowise.server.mcp_server.tool_answer.symbols import _hydrate_candidate_defines


def _sym(repo_id, n, *, path, name, kind, line):
    return WikiSymbol(
        id=f"d{n}",
        repository_id=repo_id,
        file_path=path,
        symbol_id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature="",
        start_line=line,
        end_line=line + 5,
    )


@pytest.fixture
async def shortcuts(session, repo_id):
    rows = [
        _sym(repo_id, 1, path="shortcuts.py", name="redirect", kind="function", line=20),
        _sym(repo_id, 2, path="shortcuts.py", name="render", kind="function", line=24),
        _sym(repo_id, 3, path="shortcuts.py", name="resolve_url", kind="function", line=146),
        _sym(repo_id, 4, path="shortcuts.py", name="Http404", kind="class", line=8),
        _sym(repo_id, 5, path="shortcuts.py", name="_helper", kind="function", line=200),
        _sym(repo_id, 6, path="shortcuts.py", name="loader", kind="import", line=1),
        _sym(repo_id, 7, path="other.py", name="Elsewhere", kind="class", line=3),
    ]
    for r in rows:
        session.add(r)
    await session.commit()
    return rows


async def test_a_class_outranks_a_function_and_position_breaks_the_tie(
    session, repo_id, shortcuts
) -> None:
    hits = [{"target_path": "shortcuts.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert hits[0]["_defines"] == [
        ("Http404", 8),
        ("redirect", 20),
        ("render", 24),
        ("resolve_url", 146),
    ]


async def test_a_question_named_symbol_is_promoted_to_the_front(
    session, repo_id, shortcuts
) -> None:
    """The agent that asked about `resolve_url` should not read past three
    other names to find it -- promotion is the whole reason this is
    question-aware rather than a static file outline."""
    hits = [{"target_path": "shortcuts.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits, question_ids={"resolve_url"})
    assert hits[0]["_defines"][0] == ("resolve_url", 146)


async def test_imports_are_not_offered_as_definitions(session, repo_id, shortcuts) -> None:
    """An import fills the budget with a name that answers nothing: the symbol
    it names is defined in a file we are not describing."""
    hits = [{"target_path": "shortcuts.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert "loader" not in [n for n, _ in hits[0]["_defines"]]


async def test_a_private_name_is_dropped_unless_the_question_asked_for_it(
    session, repo_id, shortcuts
) -> None:
    hits = [{"target_path": "shortcuts.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert "_helper" not in [n for n, _ in hits[0]["_defines"]]

    hits = [{"target_path": "shortcuts.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits, question_ids={"_helper"})
    assert hits[0]["_defines"][0] == ("_helper", 200)


async def test_symbols_do_not_leak_across_files(session, repo_id, shortcuts) -> None:
    hits = [{"target_path": "shortcuts.py"}, {"target_path": "other.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert [n for n, _ in hits[1]["_defines"]] == ["Elsewhere"]


async def test_a_dense_file_cannot_consume_the_whole_block(session, repo_id) -> None:
    for i in range(40):
        session.add(_sym(repo_id, 100 + i, path="dense.py", name=f"f{i}", kind="function", line=i))
    await session.commit()
    hits = [{"target_path": "dense.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert len(hits[0]["_defines"]) == _DEFINES_PER_CANDIDATE


async def test_a_file_with_no_indexed_symbols_gets_no_key_at_all(session, repo_id) -> None:
    """Absent, not empty. `serialize_candidates` tests `h.get("_defines")`, and
    an empty list would emit `"defines": ""` into every payload."""
    hits = [{"target_path": "undocumented.py"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert "_defines" not in hits[0]


async def test_a_symbol_page_is_resolved_to_its_file(session, repo_id, shortcuts) -> None:
    """`shortcuts.py::resolve_url` is a page id. The symbol rows are keyed on
    the file path, so a spotlight hit must be resolved before the lookup or it
    silently returns nothing."""
    hits = [{"target_path": "shortcuts.py::resolve_url"}]
    await _hydrate_candidate_defines(session, repo_id, hits)
    assert hits[0].get("_defines"), "a symbol-page hit must still describe its file"
