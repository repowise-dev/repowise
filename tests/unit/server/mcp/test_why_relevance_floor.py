"""Search mode ranks by question vocabulary, and refuses when nothing carries it.

Search mode used to answer everything. Its fallback was gated on
``not merged_decisions``, which nothing reached: with 614 records and a scorer
that always found token overlap, the list was never empty, so a question the
store knew nothing about got the three closest records anyway. Measured on that
store, eight of eight such questions were answered — at 11 700 to 14 700 chars
each — and the ranking that chose them scored by length and by how ordinary a
word is, so the two ``active`` records answering "why ruff check and not ruff
format" placed nowhere.

These pin both halves: what gets ranked first, and what happens when nothing
deserves to be.
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
    decision: str = "dec",
    rationale: str = "why",
    context: str = "ctx",
    files: list[str] | None = None,
    commits: list[str] | None = None,
    confidence: float = 0.8,
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
            evidence_commits_json=json.dumps(commits or [id_]),
            evidence_file=None,
            source="pr",
            confidence=confidence,
            staleness_score=0.0,
        )
    )
    await session.flush()


# --- Ranking ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_terse_record_with_the_rare_word_beats_a_long_one_without_it(
    session, setup_mcp
):
    """The ruff case, reduced to its shape.

    "Never run ruff format" is 48 characters and matches two words of the
    question. It lost to records matching "use", "check", "format" and
    "instead" while containing no "ruff" at all, because an occurrence count
    rewards saying an ordinary word in many fields.
    """
    for n in range(6):
        await _seed(
            session,
            setup_mcp,
            id_=f"common{n}",
            title=f"Formatting check {n}",
            decision="use the formatting check instead",
            rationale="use the formatting check instead",
            context="use the formatting check instead",
        )
    await _seed(session, setup_mcp, id_="terse", title="Never run zebrafish format")

    result = await get_why_search("why do we use zebrafish check instead of format")
    ids = [d["id"] for d in result["decisions"]]

    assert ids and ids[0] == "terse", ids


@pytest.mark.asyncio
async def test_a_plural_in_the_question_matches_a_singular_record(session, setup_mcp):
    """"why must issue comments avoid em dashes" is answered by a record that
    writes "comment" and "dashes", and ``comments`` is the word carrying most of
    that question's weight."""
    for n in range(6):
        await _seed(session, setup_mcp, id_=f"noise{n}", title=f"Unrelated matter {n}")
    await _seed(
        session,
        setup_mcp,
        id_="singular",
        title="Close issues and comment with short text",
        decision="Write a short comment; avoid the em dash",
        rationale="a long comment reads as noise",
        context="issue comment",
    )

    result = await get_why_search("why must issue comments avoid em dashes")

    assert [d["id"] for d in result["decisions"]] == ["singular"]


def test_a_word_in_every_record_still_carries_weight():
    """A small store where one term is universal must not score its own answer at zero.

    Textbook ``log(N/df)`` weighs a term appearing in every record at exactly
    nothing, so on a store of three records about one subsystem a question
    naming that subsystem would be refused by the tool holding its answer.
    Asserted directly rather than through ``get_why`` because the fixture store
    always carries a record that does not mention the term, which is the one
    arrangement that hides this.
    """
    from repowise.server.mcp_server._why_relevance import relevance, term_idf

    corpus = ["zebrafish one", "zebrafish two", "zebrafish three"]
    idf = term_idf(["zebrafish"], corpus)

    assert idf["zebrafish"] > 0
    assert relevance(corpus[0], idf) == 1.0


@pytest.mark.asyncio
async def test_the_answer_is_found_below_the_old_two_hundred_record_cut(
    session, setup_mcp
):
    """Search ranked over 200 records against a store of 614.

    ``list_decisions`` sorts confirmed-then-confident, so the records below the
    cut were unreachable by any question — a silent recall ceiling rather than a
    cost control. The answer here is seeded last and at the lowest confidence,
    which is exactly where that sort puts it.
    """
    for n in range(240):
        await _seed(
            session,
            setup_mcp,
            id_=f"bulk{n}",
            title=f"Routine maintenance note {n}",
            confidence=0.9,
        )
    await _seed(
        session,
        setup_mcp,
        id_="buried",
        title="Zebrafish caching strategy",
        decision="Use zebrafish caching",
        rationale="zebrafish caching is faster",
        context="zebrafish caching",
        confidence=0.1,
    )

    result = await get_why_search("why zebrafish caching strategy")

    assert "buried" in [d["id"] for d in result["decisions"]]


# --- The floor and the redirect --------------------------------------------


@pytest.mark.asyncio
async def test_a_question_the_store_cannot_answer_gets_no_records(session, setup_mcp):
    """The fallback was gated on an empty list, which 614 records never produced."""
    # These share vocabulary with the question — "ingestion", "decided",
    # "pipeline" — so a rule that only drops records matching *nothing* would
    # serve them. What they do not carry is what the question is about.
    for n in range(8):
        await _seed(
            session,
            setup_mcp,
            id_=f"other{n}",
            title=f"Ingestion pipeline note {n}",
            decision="the ingestion pipeline is decided by the deployment order",
            rationale="ingestion decided at deployment",
            context="ingestion pipeline decided",
        )

    result = await get_why_search("why is entry-point candidacy decided at ingestion")

    assert result["decisions"] == []
    assert result["try_instead"]
    assert result["reason"]


@pytest.mark.asyncio
async def test_a_refusal_carries_no_semantic_or_episode_padding(session, setup_mcp):
    """A nearest-neighbour search over any store returns three records.

    So the refusal returns before the lookup runs, rather than stapling its
    three nearest guesses to the redirect — that would be the padding the
    redirect exists to stop.
    """
    for n in range(8):
        await _seed(
            session,
            setup_mcp,
            id_=f"other{n}",
            title=f"Ingestion pipeline note {n}",
            decision="the ingestion pipeline is decided by the deployment order",
            rationale="ingestion decided at deployment",
        )

    result = await get_why_search("why is entry-point candidacy decided at ingestion")

    assert "related_documentation" not in result
    assert "episodes" not in result
    assert len(json.dumps(result, default=str)) < 2000, result


@pytest.mark.asyncio
async def test_a_good_answer_is_not_hedged_with_a_redirect(session, setup_mcp):
    """The redirect fires only when the floor is missed.

    Stapled to an answer as a hedge it teaches the same distrust from the other
    direction: an agent that sees "try get_answer instead" beside a correct
    record stops reading either.

    A guard rather than a proof: it passes against the code before the redirect
    existed, because nothing could hedge when nothing could redirect. It is here
    to fail on the change that turns the redirect into a default.
    """
    await _seed(
        session,
        setup_mcp,
        id_="answered",
        title="Zebrafish caching strategy",
        decision="Use zebrafish caching",
        rationale="zebrafish caching is faster",
        context="zebrafish caching strategy",
    )

    result = await get_why_search("why zebrafish caching strategy")

    assert result["decisions"]
    assert "try_instead" not in result
    assert "reason" not in result


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("what breaks if I change the persist pipeline", "get_risk"),
        ("what is the knowledge graph module responsible for", "get_context"),
        ("where is the episode store implemented", "search_codebase"),
        ("how does the ingestion pipeline project single file components", "get_answer"),
        ("why is entry-point candidacy decided at ingestion", "get_answer"),
    ],
)
@pytest.mark.asyncio
async def test_the_redirect_names_a_tool_that_fits_the_question(
    session, setup_mcp, query, expected
):
    """Routed on the question's shape, because its subject is what the store
    was just found to know nothing about."""
    # Carries a word from every one of the queries below, so reaching the
    # redirect depends on the floor rather than on matching nothing at all.
    for n in range(6):
        await _seed(
            session,
            setup_mcp,
            id_=f"partial{n}",
            title=f"Pipeline change note {n}",
            decision="the pipeline module is implemented by the ingestion component",
            rationale="pipeline change persists the graph store",
            context="pipeline module component",
        )

    result = await get_why_search(query)

    assert expected in result["try_instead"], result


@pytest.mark.asyncio
async def test_an_unaccepted_record_naming_a_target_is_not_an_answer(
    session, setup_mcp
):
    """Naming a file is a handle on the file, not an answer to the question.

    This pinned the opposite until the floor bypass was measured: a record
    governing a named target scored 1.0 outright and entered the ranked lane
    owing the question no vocabulary. On the real store that served "Move risk
    scale reference metadata behind opt-in inclusion" as the answer to "why does
    changed_lines() drop deletion-only files", whose whole claim to the question
    was sharing a directory with it.

    Nothing is lost, which is the other half of the contract: the record is
    still reachable through the target's own card, as a candidate, with the call
    that recovers it in full.
    """
    await _seed(
        session,
        setup_mcp,
        id_="governs",
        title="Unrelated wording entirely",
        decision="unrelated",
        rationale="unrelated",
        context="unrelated",
        files=["src/app/handler.py"],
    )

    result = await get_why_search(
        "why is entry-point candidacy decided at ingestion",
        targets=["src/app/handler.py"],
    )

    assert "governs" not in [d["id"] for d in result["decisions"]]
    assert result.get("answer_basis") != "decision"
    context = result["target_context"]["src/app/handler.py"]
    assert not context["governing_decisions"]
    reachable = [d["id"] for d in context.get("candidate_decisions") or []]
    assert "governs" in reachable or "recover_with" in (
        context.get("candidate_decisions_omitted") or {}
    )


async def get_why_search(query: str, targets: list[str] | None = None) -> dict:
    from repowise.server.mcp_server import get_why

    return await get_why(query, targets=targets)


# --- Commit lanes on a target card -----------------------------------------


def _commit(sha: str, message: str) -> dict:
    return {"sha": sha, "message": message}


def test_prose_terms_name_an_identifier_by_its_parts():
    """No commit subject spells a private helper, so the compound is not a term."""
    from repowise.server.mcp_server._why_relevance import question_terms, word_set

    terms = question_terms("why does argsMinusFirstX skip _flag_values", prose=True)

    assert "argsminusfirstx" not in terms and "_flag_values" not in terms
    assert {"arg", "minu", "first", "skip", "flag", "value"} <= set(terms)
    assert "tie" not in word_set("the entire tier")
    assert "managed" in word_set("withManagedSignal")


def test_a_question_naming_code_keeps_the_commit_that_answers_it():
    """The decision floor served no commit for a question naming code, and none
    at all on a store without records. The card's best commit is served."""
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    arch = {
        "file_commits": [
            _commit("aaaaaaa1", "fix: don't remove flag value that matches subcommand name"),
            _commit("bbbbbbb2", "docs: update README"),
        ],
        "git_log": [_commit("ccccccc3", "chore: bump flag parser")],
    }
    _focus_archaeology(arch, "why does argsMinusFirstX skip the value of a flag", "recall")

    assert [c["sha"] for c in arch["file_commits"]] == ["aaaaaaa1"]
    assert arch["git_log_omitted"]["suppressed"] == 1


def test_a_card_whose_commits_share_one_word_serves_none():
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    arch = {"git_log": [_commit("aaaaaaa1", "fix flag parsing"), _commit("b2", "add tests")]}
    _focus_archaeology(arch, "why is the mousetrap splash shown for a flag", "recall")

    assert "git_log" not in arch
    assert arch["git_log_omitted"] == {
        "suppressed": 2,
        "reason": "query-irrelevant",
        "recover_with": "recall",
    }


def test_a_commit_in_two_lanes_is_served_once():
    """The indexed lane and the live log abbreviate one sha to different lengths."""
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    message = "Allow running persistent run hooks of all parents"
    full = "4cafa37a1b2c" + "0" * 28
    arch = {
        "file_commits": [_commit("4cafa37a", message)],
        "git_log": [{**_commit("4cafa37a1b2c", message), "commit": full}],
    }
    _focus_archaeology(arch, "why do persistent hooks of all parents run", "recall")

    assert [c["sha"] for c in arch["file_commits"]] == ["4cafa37a"]
    # Not suppressed: the commit was served from the other lane.
    assert "git_log" not in arch and "git_log_omitted" not in arch


def test_commits_sharing_a_short_prefix_stay_distinct():
    """Two commits whose shas share seven characters are two commits."""
    from repowise.server.mcp_server.tool_why.search import _commit_keys

    a, b = "abcdef1" + "1" * 33, "abcdef1" + "2" * 33
    rows = [{**_commit(a[:12], "one"), "commit": a}, {**_commit(b[:12], "two"), "commit": b}]
    keys = _commit_keys({"git_log": rows})

    assert len(set(keys.values())) == 2


def test_an_abbreviated_sha_resolves_to_the_full_one_it_prefixes():
    from repowise.server.mcp_server.tool_why.search import _commit_keys

    full = "abcdef12" + "3" * 32
    indexed = _commit("abcdef12", "one")
    live = {**_commit(full[:12], "one"), "commit": full}
    keys = _commit_keys({"file_commits": [indexed], "git_log": [live]})

    assert keys[id(indexed)] == keys[id(live)] == full


def test_rows_without_a_sha_are_never_merged():
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    arch = {
        "git_log": [
            {"message": "Allow running persistent run hooks of all parents"},
            {"message": "Allow running persistent run hooks of all parents"},
            {"message": "docs: update README"},
        ]
    }
    _focus_archaeology(arch, "why do persistent hooks of all parents run", "recall")

    # Two tied commits, not one row standing for both.
    assert len(arch["git_log"]) == 2


def test_a_near_tie_between_commits_serves_none():
    """Two commits each carrying two question words at almost the same weight:
    the question does not single either out."""
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    arch = {
        "git_log": [
            _commit("aaaaaaa1", "Delay fetch in tests"),
            _commit("bbbbbbb2", "Retry body before reading"),
            _commit("ccccccc3", "Retry before reading"),
            _commit("ddddddd4", "docs: update README"),
        ]
    }
    _focus_archaeology(arch, "why delay the retry before the fetch body", "recall")

    assert "git_log" not in arch
    assert arch["git_log_omitted"]["suppressed"] == 4


def test_tied_best_commits_are_both_served():
    from repowise.server.mcp_server.tool_why.search import _focus_archaeology

    arch = {
        "git_log": [
            _commit("aaaaaaa1", "Make detection for test-binary more universal"),
            _commit("bbbbbbb2", 'Revert "Make detection for test-binary more universal"'),
            _commit("ccccccc3", "docs: update README"),
        ]
    }
    _focus_archaeology(arch, "why is the test binary detected by name", "recall")

    assert [c["sha"] for c in arch["git_log"]] == ["aaaaaaa1", "bbbbbbb2"]
