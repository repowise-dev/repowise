"""Search-mode payload shape: caps, restatement collapse, one embedding.

Search mode had no caps at all. It served eight whole records with their file
arrays inlined, measuring 27 640 - 34 917 chars against a 32 000 budget, and
paid two embeddings of the same query to build them. These pin the three things
that fixed: the projection is clamped, restatements of one decision collapse
instead of filling every slot, and the two semantic lanes come out of one
embedding rather than two round trips.

Six of these eight fail against the code they were written for. The two that do
not — ``test_search_keeps_records_citing_different_commits_apart`` and
``test_status_breaks_ties_without_gating`` — are deliberate guards on behaviour
the fix must *not* change: one bounds the restatement collapse so two real
decisions never merge, the other pins the ordering back after status-first
ranking was tried and measured worse. They are here to fail on a future change,
not this one.
"""

from __future__ import annotations

import json

import pytest


async def _seed(
    session,
    rid: str,
    *,
    id_: str,
    title: str,
    status: str = "proposed",
    source: str = "pr",
    commits: list[str] | None = None,
    files: list[str] | None = None,
    confidence: float = 0.8,
    decision: str = "dec",
    rationale: str = "why",
    context: str = "ctx",
    evidence_file: str | None = None,
) -> None:
    from repowise.core.persistence.models import DecisionRecord

    session.add(
        DecisionRecord(
            id=id_,
            repository_id=rid,
            title=title,
            status=status,
            context=context,
            decision=decision,
            rationale=rationale,
            affected_files_json=json.dumps(files or []),
            affected_modules_json=json.dumps([]),
            evidence_commits_json=json.dumps(commits or []),
            evidence_file=evidence_file,
            source=source,
            confidence=confidence,
            staleness_score=0.0,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_search_clamps_affected_files_and_reports_the_total(session, setup_mcp):
    """Whole arrays were 36% of the payload; path mode already clamps to a head."""
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import _MAX_AFFECTED_FILES

    await _seed(
        session,
        setup_mcp,
        id_="wide1",
        title="Zebrafish caching strategy",
        files=[f"src/mod{n}/file.py" for n in range(83)],
    )

    result = await get_why("why the zebrafish caching strategy")
    wide = next(d for d in result["decisions"] if d["id"] == "wide1")

    assert len(wide["affected_files"]) == _MAX_AFFECTED_FILES
    assert wide["affected_files_total"] == 83


@pytest.mark.asyncio
async def test_search_collapses_restatements_of_one_decision(session, setup_mcp):
    """Dedup was by id, which is unique per extraction, so it collapsed nothing.

    Fourteen records on the dogfooded repo restate one decision and share a
    single ``evidence_commits`` entry. Serving five phrasings of one decision is
    how a three-slot cap gets spent on one answer.
    """
    from repowise.server.mcp_server import get_why

    for n in range(5):
        await _seed(
            session,
            setup_mcp,
            id_=f"restate{n}",
            title=f"Zebrafish caching, phrasing {n}",
            commits=["deadbeef" * 5],
            confidence=0.9 - n / 100,
        )

    result = await get_why("why the zebrafish caching phrasing")
    served = [d for d in result["decisions"] if d["id"].startswith("restate")]

    assert len(served) == 1, f"expected one record, got {[d['id'] for d in served]}"
    # The folded ids stay addressable rather than vanishing.
    assert sorted(served[0]["restates"]) == [f"restate{n}" for n in range(1, 5)]


@pytest.mark.asyncio
async def test_search_keeps_records_citing_different_commits_apart(session, setup_mcp):
    """The key is the cited evidence, so two real decisions must not merge."""
    from repowise.server.mcp_server import get_why

    await _seed(
        session,
        setup_mcp,
        id_="ev1",
        title="Zebrafish caching one",
        commits=["a" * 40],
    )
    await _seed(
        session,
        setup_mcp,
        id_="ev2",
        title="Zebrafish caching two",
        commits=["b" * 40],
    )

    result = await get_why("why zebrafish caching")
    served = {d["id"] for d in result["decisions"]}

    assert {"ev1", "ev2"} <= served


@pytest.mark.asyncio
async def test_search_caps_full_bodies(session, setup_mcp):
    """Three whole records, not eight thinned ones."""
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import _MAX_SEARCH_DECISIONS

    for n in range(12):
        await _seed(
            session,
            setup_mcp,
            id_=f"many{n}",
            title=f"Zebrafish caching decision {n}",
            commits=[f"sha{n}"],
        )

    result = await get_why("why zebrafish caching decision")
    bodies = [d for d in result["decisions"] if "decision" in d]

    assert len(bodies) == _MAX_SEARCH_DECISIONS


@pytest.mark.asyncio
async def test_search_embeds_the_query_once(session, setup_mcp, monkeypatch):
    """Two awaits embedded the same string back to back, one question, two trips."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_why

    # Seeded because the semantic lanes now run only once some record has
    # cleared the relevance floor: a question with no answer behind it returns
    # before embedding anything, which is its own test below.
    await _seed(
        session,
        setup_mcp,
        id_="jwt",
        title="JWT used for authentication",
        decision="JWT is used for authentication",
        rationale="JWT authentication is stateless",
        context="authentication",
    )

    vs = mcp_mod._vector_store
    calls: list[str] = []

    original = vs.embed_texts

    async def _counting(texts):
        calls.append("embed_texts")
        return await original(texts)

    async def _forbidden(query, limit=10):
        calls.append(f"search:{query}")
        return []

    monkeypatch.setattr(vs, "embed_texts", _counting)
    monkeypatch.setattr(vs, "search", _forbidden)

    await get_why("why is JWT used for authentication")

    # One embedding, and no fall-through to the text-embedding search path.
    assert calls.count("embed_texts") == 1, calls
    assert not [c for c in calls if c.startswith("search:")], calls


@pytest.mark.asyncio
async def test_related_documentation_excludes_decision_pages(session, setup_mcp):
    """The doc lane took the nearest pages of any kind, decisions included.

    So a decision record came back as "related documentation" beside the
    decisions list it belonged in. One window, partitioned by namespace, gives
    each lane only what is its own.
    """
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
    from repowise.server.mcp_server import get_why

    # Seeded so the question clears the relevance floor and the lanes run at
    # all; the assertion is about which lane a decision page lands in.
    await _seed(
        session,
        setup_mcp,
        id_="zebra-latency",
        title="Zebrafish caching keeps latency down",
        decision="Zebrafish caching keeps latency down",
        rationale="zebrafish caching latency",
    )

    vs = mcp_mod._vector_store
    await vs.embed_and_upsert(
        f"{DECISION_VECTOR_PREFIX}dec-doc-lane",
        "Zebrafish caching keeps latency down",
        {
            "title": "Zebrafish caching",
            "page_type": "decision_record",
            "target_path": "",
            "content": "Zebrafish caching keeps latency down",
        },
    )

    result = await get_why("why zebrafish caching keeps latency down")

    assert all(
        not r["page_id"].startswith(DECISION_VECTOR_PREFIX)
        for r in result["related_documentation"]
    ), result["related_documentation"]


@pytest.mark.asyncio
async def test_breadth_does_not_outscore_relevance(session, setup_mcp):
    """A record's file list is a scope, not question text.

    Joining 83 paths into one haystack and substring-matching a question against
    it scored by breadth: ordinary query words occur somewhere in 83 paths, so
    the widest record on the dogfooded repo led three of five probe questions,
    including one about ruff that it has nothing to do with.
    """
    from repowise.server.mcp_server import get_why

    await _seed(
        session,
        setup_mcp,
        id_="broad",
        title="Unrelated infrastructure change",
        decision="unrelated",
        rationale="unrelated",
        context="unrelated",
        files=[f"src/zebrafish/caching/mod{n}.py" for n in range(83)],
    )
    await _seed(
        session,
        setup_mcp,
        id_="narrow",
        title="Zebrafish caching strategy",
        decision="Use zebrafish caching",
        rationale="zebrafish caching is faster",
        files=["src/one.py"],
    )

    result = await get_why("why zebrafish caching")
    ids = [d["id"] for d in result["decisions"]]

    # The wide record matches the question on nothing but its file paths, so it
    # now scores zero and is not a candidate at all.
    assert "narrow" in ids, ids
    assert "broad" not in ids, ids


@pytest.mark.asyncio
async def test_status_breaks_ties_without_gating(session, setup_mcp):
    """Confirmed wins a tie; it does not outrank a better match.

    Ordering by status first was tried and measured worse: only 69 of the
    dogfooded repo's 614 records are active, so a hard gate served three weakly
    matching confirmed records ahead of the only relevant ones. The four records
    answering one probe question are all proposed.
    """
    from repowise.server.mcp_server import get_why

    # Both candidates now have to clear the relevance floor before ordering is
    # observable at all, so the weaker one is weaker rather than unrelated: it
    # carries "zebrafish caching" and not "strategy". The three fillers exist to
    # make "strategy" an ordinary word in this store, which is what leaves the
    # weaker record enough of the question's weight to be served.
    for n in range(3):
        await _seed(
            session,
            setup_mcp,
            id_=f"filler{n}",
            title=f"Deployment strategy {n}",
            decision="strategy",
            rationale="strategy",
            context="strategy",
            commits=[f"fill{n}"],
        )
    await _seed(
        session,
        setup_mcp,
        id_="weak-active",
        status="active",
        title="Zebrafish caching",
        decision="zebrafish caching",
        rationale="zebrafish caching",
        context="zebrafish caching",
        commits=["aaa"],
    )
    await _seed(
        session,
        setup_mcp,
        id_="strong-proposed",
        status="proposed",
        title="Zebrafish caching strategy chosen",
        decision="Zebrafish caching strategy chosen",
        rationale="zebrafish caching strategy",
        context="zebrafish caching",
        commits=["bbb"],
    )

    result = await get_why("why zebrafish caching strategy")
    ids = [d["id"] for d in result["decisions"]]

    assert ids.index("strong-proposed") < ids.index("weak-active"), ids
