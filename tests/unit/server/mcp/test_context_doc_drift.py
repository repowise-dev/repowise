"""``get_context(include=["doc_drift"])`` — the drift reverse view.

The drift pass files a finding against the *document*, so a question asked
about a code file can only be answered from the stored reference rows. What
these tests pin is the honesty of that answer: the two claims it makes must
stay apart, and an answer it cannot support must not render as a confident
zero. "No document mentions src/auth.py" is a far stronger statement than "no
drift findings", and an unpopulated store produces it silently.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from repowise.core.analysis.doc_drift.constants import REFERENCE_BASIS
from repowise.core.persistence.models import DocDriftFinding, DocDriftReference

_TARGET = "src/auth/service.py"


def _ref(repo_id: str, document: str, line: int, **kw) -> DocDriftReference:
    return DocDriftReference(
        repository_id=repo_id,
        document_path=document,
        target_path=kw.get("target", _TARGET),
        kind=kw.get("kind", "path"),
        line_number=line,
        section=kw.get("section", "Authentication"),
    )


def _finding(repo_id: str, document: str, line: int) -> DocDriftFinding:
    return DocDriftFinding(
        repository_id=repo_id,
        file_path=document,
        line_number=line,
        kind="path",
        target="src/gone.py",
        confidence=0.9,
        reason="Document names src/gone.py, which no longer exists.",
        origin="path_no_candidate",
        evidence_json="[]",
        raw="src/gone.py",
        context="see src/gone.py",
    )


@pytest.fixture
async def reference_rows(session, repo_id):
    session.add_all(
        [
            _ref(repo_id, "docs/auth.md", 12),
            _ref(repo_id, "docs/auth.md", 40, kind="link"),
            _ref(repo_id, "docs/overview.md", 3, section=""),
            # A different file entirely: it must not reach the answer.
            _ref(repo_id, "docs/db.md", 5, target="src/db/models.py"),
        ]
    )
    await session.commit()
    return repo_id


@pytest.mark.asyncio
async def test_the_block_is_absent_until_it_is_asked_for(setup_mcp, reference_rows):
    from repowise.server.mcp_server import get_context

    card = (await get_context(targets=[_TARGET]))["targets"][_TARGET]
    assert "doc_drift" not in card


@pytest.mark.asyncio
async def test_doc_drift_is_a_known_include_key(setup_mcp, reference_rows):
    """An unrecognised key lands in ignored_arguments, so this would be visible."""
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=[_TARGET], include=["doc_drift"])

    ignored = [
        value
        for entry in (result.get("ignored_arguments") or [])
        for value in entry.get("values", [])
    ]
    assert "doc_drift" not in ignored


@pytest.mark.asyncio
async def test_it_answers_with_the_documents_that_name_this_file(
    setup_mcp, reference_rows
):
    from repowise.server.mcp_server import get_context

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]
    block = card["doc_drift"]

    assert [r["document"] for r in block["references"]] == [
        "docs/auth.md",
        "docs/auth.md",
        "docs/overview.md",
    ]
    assert block["documents"] == 2
    assert block["references"][0] == {
        "document": "docs/auth.md",
        "line": 12,
        "kind": "path",
        "section": "Authentication",
    }


@pytest.mark.asyncio
async def test_a_reference_to_another_file_stays_out_of_the_answer(
    setup_mcp, reference_rows
):
    """The reverse index is keyed by target; a leak here answers the wrong question."""
    from repowise.server.mcp_server import get_context

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]

    assert "docs/db.md" not in {r["document"] for r in card["doc_drift"]["references"]}


@pytest.mark.asyncio
async def test_the_basis_rides_along_even_when_nothing_was_found(
    setup_mcp, reference_rows
):
    """The empty answer is the one most likely to be read as proof of absence."""
    from repowise.server.mcp_server import get_context

    result = await get_context(
        targets=["src/auth/middleware.py"], include=["doc_drift"]
    )
    block = result["targets"]["src/auth/middleware.py"]["doc_drift"]

    assert block["references"] == []
    assert block["documents"] == 0
    assert block["references_basis"] == REFERENCE_BASIS


@pytest.mark.asyncio
async def test_an_unpopulated_store_is_not_reported_as_a_clean_tree(setup_mcp, repo_id):
    """The gate this slice had to settle.

    The table is created on the ordinary schema-reconcile path, but the pass
    only runs when an update has work to do. "Upgraded, never analysed" is a
    real state, and without this it is indistinguishable from "no document
    mentions this file" --- a far stronger claim, wrong in the same silent way.
    """
    from repowise.server.mcp_server import get_context

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]

    assert card["doc_drift"] == {"unavailable": "not_computed"}


@pytest.mark.asyncio
async def test_an_index_without_the_reference_table_says_so(
    setup_mcp, reference_rows, monkeypatch
):
    """"Could not read" must never render as "nothing to report"."""
    import repowise.server.mcp_server.tool_context.enrichment as enrichment
    from repowise.server.mcp_server import get_context

    async def _raise(*_a, **_k):
        raise OperationalError(
            "select", {}, Exception("no such table: doc_drift_references")
        )

    monkeypatch.setattr(enrichment, "get_doc_drift_references", _raise)

    result = await get_context(targets=[_TARGET], include=["doc_drift"])
    card = result["targets"][_TARGET]

    assert card["doc_drift"] == {"unavailable": "index_predates_doc_drift"}
    # And the failed read did not take the rest of the card with it.
    assert card["type"] == "file"
    assert "docs" in card


@pytest.mark.asyncio
async def test_a_transient_read_failure_is_not_reported_as_an_old_index(
    setup_mcp, reference_rows, monkeypatch
):
    """"Reindex" is the wrong advice for a locked database.

    Both refusals name a cause the reader will act on, so mapping every
    failure onto the schema one sends them to rebuild an index that was
    never the problem.
    """
    import repowise.server.mcp_server.tool_context.enrichment as enrichment
    from repowise.server.mcp_server import get_context

    async def _raise(*_a, **_k):
        raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(enrichment, "get_doc_drift_references", _raise)

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]

    assert card["doc_drift"] == {"unavailable": "drift_read_failed"}


@pytest.mark.asyncio
async def test_one_link_with_a_fragment_is_counted_once(setup_mcp, session, repo_id):
    """The store keeps the link and the anchor apart; a reader counts mentions.

    ``[guide](x.py#top)`` yields both a link row and an anchor row at the same
    site, because the two drift apart and a finding has to name which. Showing
    the reader the same document and line twice over-reports how many places
    mention the file, and spends two of twenty card slots saying it.
    """
    from repowise.server.mcp_server import get_context

    session.add_all(
        [
            _ref(repo_id, "docs/auth.md", 12, kind="link"),
            _ref(repo_id, "docs/auth.md", 12, kind="anchor"),
        ]
    )
    await session.commit()

    block = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]["doc_drift"]

    assert len(block["references"]) == 1
    assert block["documents"] == 1


@pytest.mark.asyncio
async def test_an_answer_emptied_by_exclusions_says_so(
    setup_mcp, session, repo_id, monkeypatch
):
    """An excluded document is not an absent one.

    Without the count, a repository whose documentation directory is excluded
    reports every file in it as mentioned by nothing --- the same false claim
    the unpopulated-store guard exists to prevent, reached by another route.
    """
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    session.add(_ref(repo_id, "docs/auth.md", 12))
    await session.commit()

    import pathspec

    # Excludes the naming document, not the target, which is the case that
    # empties the answer without emptying the card.
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["docs/"])
    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda *_a, **_k: spec)

    block = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]["doc_drift"]

    assert block["references"] == []
    assert block["references_excluded"] == 1


@pytest.mark.asyncio
async def test_a_documents_own_drift_is_reported_as_the_documents_and_not_the_targets(
    setup_mcp, session, reference_rows
):
    """The easiest way for this surface to lie.

    A document that describes this file and a document carrying a drifted
    reference are different claims. The drifted reference here points at
    ``src/gone.py``, not at the target, and the block must keep that separate
    rather than implying the description of the target is wrong.
    """
    from repowise.server.mcp_server import get_context

    session.add(_finding(reference_rows, "docs/auth.md", 90))
    await session.commit()

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]
    block = card["doc_drift"]

    assert block["documents_with_drift"] == [{"document": "docs/auth.md", "findings": 1}]
    # The reference rows themselves are untouched by it.
    assert len(block["references"]) == 3
    assert "which need not be about this file" in block["references_basis"]


@pytest.mark.asyncio
async def test_several_targets_in_one_call_each_get_their_own_answer(
    setup_mcp, reference_rows
):
    """Targets are resolved concurrently over ONE shared session.

    This block is the only enrichment helper that opens a savepoint, and
    ``asyncio.gather`` means a second target can enter one while the first is
    still inside. If those nest, the first to exit closes the other's
    transaction and every later statement in the call fails --- turning the
    guard meant to isolate one bad read into the thing that breaks the rest.
    """
    from repowise.server.mcp_server import get_context

    result = await get_context(
        targets=[_TARGET, "src/auth/middleware.py", "src/db/models.py"],
        include=["doc_drift"],
    )

    cards = result["targets"]
    assert len(cards) == 3
    assert [r["document"] for r in cards[_TARGET]["doc_drift"]["references"]] == [
        "docs/auth.md",
        "docs/auth.md",
        "docs/overview.md",
    ]
    assert cards["src/db/models.py"]["doc_drift"]["references"] == [
        {"document": "docs/db.md", "line": 5, "kind": "path", "section": "Authentication"}
    ]
    assert cards["src/auth/middleware.py"]["doc_drift"]["references"] == []
    # No target reported a failure, and the rest of each card survived.
    assert not any("error" in c for c in cards.values())
    assert all("docs" in c for c in cards.values())


@pytest.mark.asyncio
async def test_a_clean_document_gets_no_drift_entry(setup_mcp, reference_rows):
    from repowise.server.mcp_server import get_context

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]

    assert "documents_with_drift" not in card["doc_drift"]


@pytest.mark.asyncio
async def test_a_target_with_no_file_path_gets_a_null_block(setup_mcp, reference_rows):
    """A module target resolves to a directory, which nothing can reference."""
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["nonexistent-target-xyz"], include=["doc_drift"])
    card = result["targets"]["nonexistent-target-xyz"]

    assert card.get("doc_drift") in (None, {"unavailable": "not_computed"})


@pytest.mark.asyncio
async def test_the_answer_is_capped_with_a_recoverable_receipt(setup_mcp, session, repo_id):
    """Twenty documents is a card; the rest is a CLI question."""
    from repowise.server.mcp_server import get_context

    session.add_all([_ref(repo_id, f"docs/g{i:03d}.md", 1) for i in range(30)])
    await session.commit()

    card = (await get_context(targets=[_TARGET], include=["doc_drift"]))["targets"][
        _TARGET
    ]
    block = card["doc_drift"]

    assert len(block["references"]) == 20
    assert block["references_total"] == 30
    assert block["references_omitted"] == 10
    assert block["references_reduced_reason"] == "construction_cap"
