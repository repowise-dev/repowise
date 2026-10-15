"""``get_glossary`` — the declared vocabulary, served to an agent as data.

The page already tells a reader what a word means. The tool exists for the
other question: which of these words is the one to use, which are the ones the
team ruled out, and where is the term used. It answers from the same selection
the generated pages use, so the tool and the page cannot disagree about which
word is canonical.

Opt-in by design: most repositories have not declared a glossary, and a tool in
every session's prompt prefix that answers "nothing here" is not worth its
schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_CONTEXT_MD = """\
# Ordering

How orders are taken and fulfilled.

## Language

**Order**:
A customer's request to purchase one or more products.
_Avoid_: Purchase, transaction

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer

**Bounded Context**:
Where a term means one thing and only one thing.
"""


async def _call(repo_path: Path, **kwargs: object) -> dict:
    """Call the real tool entry point against one repository."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_glossary

    mcp_mod._repo_path = str(repo_path)
    # The module page rows are the corroboration corpus; this repository has
    # none, so the mined rows are gated out and the declared ones are not.
    return await get_glossary(**kwargs)  # type: ignore[arg-type]


def _repo(tmp_path: Path, *, declared: str | None = _CONTEXT_MD) -> Path:
    root = tmp_path / "ordering"
    (root / "src").mkdir(parents=True)
    (root / "src" / "orders.py").write_text(
        '"""Order handling for the ledger."""\n', encoding="utf-8"
    )
    if declared is not None:
        (root / "CONTEXT.md").write_text(declared, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _reset_repo_path():
    """``_repo_path`` is process-global; every test here sets its own."""
    import repowise.server.mcp_server as mcp_mod

    previous = mcp_mod._repo_path
    yield
    mcp_mod._repo_path = previous


@pytest.mark.asyncio
async def test_the_declared_terms_are_served_with_their_avoid_lists(
    tmp_path: Path,
) -> None:
    result = await _call(_repo(tmp_path))

    by_term = {row["term"]: row for row in result["terms"]}
    assert by_term["Order"]["status"] == "declared"
    assert by_term["Order"]["avoid"] == ["Purchase", "transaction"]
    assert by_term["Order"]["source_path"] == "CONTEXT.md"
    assert by_term["Order"]["source_line"] == 7
    assert result["declared_count"] == 3


@pytest.mark.asyncio
async def test_a_declared_term_with_no_corroboration_says_so(tmp_path: Path) -> None:
    """The gap is the signal, so it is named on the row rather than left as an
    empty column a reader has to interpret."""
    result = await _call(_repo(tmp_path))

    row = next(r for r in result["terms"] if r["term"] == "Bounded Context")
    assert row["used_in"] == []
    assert row["used_in_count"] == 0
    assert "not yet in code" in row["note"]


@pytest.mark.asyncio
async def test_the_payload_says_where_the_words_came_from(tmp_path: Path) -> None:
    """A declared glossary is present, so the warning block is not served: the
    terms below it *are* the team's ruling."""
    result = await _call(_repo(tmp_path))
    assert "declared_glossary" not in result
    assert "ruling" in result["_meta"]["hint"]


@pytest.mark.asyncio
async def test_a_repository_with_no_glossary_says_the_rows_are_mined(
    tmp_path: Path,
) -> None:
    """The two are different claims about a word and the payload keeps them
    apart: mined is what the repository says, declared is what the team
    decided."""
    result = await _call(_repo(tmp_path, declared=None))

    assert result["declared_glossary"]["present"] is False
    assert "mined house vocabulary" in result["declared_glossary"]["note"]
    assert result["declared_count"] == 0


@pytest.mark.asyncio
async def test_targets_restrict_to_terms_used_there(tmp_path: Path) -> None:
    """A question about one subsystem should get that subsystem's vocabulary."""
    result = await _call(_repo(tmp_path), targets=["nothing-matches-this"])
    assert result["terms"] == []
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_the_limit_is_reported_rather_than_silently_applied(
    tmp_path: Path,
) -> None:
    result = await _call(_repo(tmp_path), limit=2)

    assert len(result["terms"]) == 2
    assert result["total"] == 3
    assert result["truncated"] is True
    assert result["reduced_reason"] == "limit"


@pytest.mark.asyncio
async def test_a_context_filter_restricts_to_one_bounded_context(
    tmp_path: Path,
) -> None:
    """Declared terms with no context are the single-context case; asking for a
    named context must not match them."""
    result = await _call(_repo(tmp_path), context="Billing")
    assert result["terms"] == []


@pytest.mark.asyncio
async def test_the_tool_is_opt_in(tmp_path: Path) -> None:
    """Not a flagship. A tool most repositories get nothing from does not earn
    a schema in every session's prompt prefix."""
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    entry = next(e for e in mcp_tool_registry.entries() if e.name == "get_glossary")
    assert entry.default is False
    assert entry.tier == "specialist"
    assert entry.requires_workspace is False


@pytest.mark.asyncio
async def test_repo_all_is_refused_with_the_list_of_alternatives(tmp_path: Path) -> None:
    result = await _call(_repo(tmp_path), repo="all")
    assert "not supported" in result["error"]
