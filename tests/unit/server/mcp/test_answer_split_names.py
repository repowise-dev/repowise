"""A name the question writes that production code defines in more than one
file does not earn high unless the answer tells the definitions apart."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server.tool_answer.confidence import _grade_answer
from repowise.server.mcp_server.tool_answer.payload import _split_names_note
from repowise.server.mcp_server.tool_answer.symbols import _split_definitions

_POOL = "src/utils/concurrent.ts"
_SSG = "src/helper/ssg/ssg.ts"
_SPLIT = {"DEFAULT_CONCURRENCY": [_SSG, _POOL]}
_HITS = [
    {
        "target_path": _POOL,
        "score": 4.6,
        "summary": "A bounded pool for running async tasks; DEFAULT_CONCURRENCY caps it.",
        "symbols": [
            {
                "name": "DEFAULT_CONCURRENCY",
                "kind": "constant",
                "signature": "const DEFAULT_CONCURRENCY = 1024",
                "_verified": True,
                "_matched": True,
            }
        ],
    },
    {
        "target_path": _SSG,
        "score": 3.2,
        "summary": "Static site generation.",
        "symbols": [{"name": "DEFAULT_CONCURRENCY", "kind": "constant", "_verified": True}],
    },
]
_ONE = f"In `{_POOL}`, `DEFAULT_CONCURRENCY` is `1024`."
_BOTH = f"`DEFAULT_CONCURRENCY` is `1024` in `{_POOL}` and `2` in `{_SSG}`."


def _grade(question, answer, *, split=_SPLIT, citations=(_POOL,), hits=_HITS):
    return _grade_answer(
        question=question,
        question_ids={"DEFAULT_CONCURRENCY"},
        answer_text=answer,
        hits=hits,
        citations=list(citations),
        symbol_bodies=[],
        served_named_body=False,
        dominance="gap",
        split_names=split,
    )


def test_an_answer_naming_one_of_two_definitions_is_held_at_medium() -> None:
    grade = _grade("DEFAULT_CONCURRENCY", _ONE)
    assert grade.confidence == "medium"
    assert grade.split_names == _SPLIT


def test_an_answer_naming_every_definition_keeps_high() -> None:
    grade = _grade("DEFAULT_CONCURRENCY", _BOTH, citations=(_POOL, _SSG))
    assert grade.confidence == "high"
    assert grade.split_names == {}


def test_a_file_named_for_another_reason_does_not_settle_it() -> None:
    answer = f"{_ONE} The pool is imported by `{_SSG}`."
    grade = _grade("DEFAULT_CONCURRENCY", answer, citations=(_POOL, _SSG))
    assert grade.confidence == "medium"


def test_a_question_word_picking_one_definition_settles_it() -> None:
    answer = f"In `{_SSG}`, `DEFAULT_CONCURRENCY` is `2`."
    ssg_first = [{**_HITS[1], "score": 4.6, "summary": "SSG helper defaults."}, _HITS[0]]
    grade = _grade(
        "What is DEFAULT_CONCURRENCY in the ssg helper?", answer, citations=(_SSG,), hits=ssg_first
    )
    assert grade.confidence == "high"


def test_a_question_word_picking_one_definition_does_not_settle_the_other() -> None:
    grade = _grade("What is DEFAULT_CONCURRENCY in the ssg helper?", _ONE)
    assert grade.confidence == "medium"


def test_a_name_defined_once_is_untouched() -> None:
    assert _grade("DEFAULT_CONCURRENCY", _ONE, split={}).confidence == "high"


def test_a_lower_grade_carries_no_split_finding() -> None:
    weak = [{**h, "score": 0.5} for h in _HITS]
    grade = _grade_answer(
        question="DEFAULT_CONCURRENCY",
        question_ids={"DEFAULT_CONCURRENCY"},
        answer_text=_ONE,
        hits=weak,
        citations=[_POOL],
        symbol_bodies=[],
        served_named_body=False,
        dominance=None,
        split_names=_SPLIT,
    )
    assert grade.confidence == "medium"
    assert grade.split_names == {}


def test_the_note_names_every_definition() -> None:
    note = _split_names_note(_SPLIT)
    assert _SSG in note and _POOL in note and note.startswith("Held at medium")


def _row(name, path, language="typescript"):
    return SimpleNamespace(name=name, file_path=path, language=language)


def test_split_definitions_counts_production_files_only() -> None:
    rows = [
        _row("DEFAULT_CONCURRENCY", _SSG),
        _row("DEFAULT_CONCURRENCY", _POOL),
        _row("run", "src/a.ts"),
        _row("run", "src/a.ts"),
        _row("run", "src/a.test.ts"),
        _row("orphan", None),
    ]
    assert _split_definitions(rows) == {"DEFAULT_CONCURRENCY": [_SSG, _POOL]}


def test_a_generic_name_defined_in_many_files_is_not_split_checked() -> None:
    rows = [_row("get", f"src/m{i}.ts") for i in range(6)]
    assert _split_definitions(rows) == {}


class _UntrustingGuard:
    async def trusts(self, token, row):
        return False


def test_a_name_written_as_an_owner_anchors_its_definition() -> None:
    import asyncio

    from repowise.server.mcp_server.tool_answer.symbols import _trusted_over_topic

    client = SimpleNamespace(
        name="Client", file_path="httpx/_client.py", parent_name=None, qualified_name="httpx.Client"
    )
    by_name = {"Client": [client]}
    run = asyncio.run
    assert run(_trusted_over_topic(by_name, {"Client", "Client.get"}, _UntrustingGuard())) == by_name
    assert run(_trusted_over_topic(by_name, {"Client"}, _UntrustingGuard())) == {}
    other = SimpleNamespace(
        name="Client", file_path="pkg/rpc.py", parent_name=None, qualified_name="pkg.rpc.Client"
    )
    two = {"Client": [client, other]}
    assert run(_trusted_over_topic(two, {"Client", "Client.get"}, _UntrustingGuard())) == {}


# --- narrowing a shared name to the definitions the question names -----------

_PAGES = "src/adapter/cloudflare-pages/handler.ts"
_WORKERS = "src/adapter/cloudflare-workers/serve-static.ts"
_DENO = "src/adapter/deno/serve-static.ts"
_DENO_PKG = "adapters/deno/src/serve-static.ts"


def _defs(*paths):
    return [
        SimpleNamespace(
            name="serveStatic", file_path=p, kind="function", parent_name=None,
            qualified_name="serveStatic",
        )
        for p in paths
    ]


async def _choose(question, cands, question_ids=frozenset({"serveStatic"})):
    from repowise.server.mcp_server.tool_answer.symbols import _choose_anchor_symbols

    async def _verified(c):
        return {"path": c.file_path}

    homonyms = {"union": {}, "qualified_miss": []}
    chosen = await _choose_anchor_symbols(
        {"serveStatic": cands}, set(question_ids), _verified, homonyms, question=question
    )
    return [c.file_path for c in chosen], {
        name: [d["path"] for d in defs] for name, defs in homonyms["union"].items()
    }


async def test_a_question_naming_one_file_anchors_its_definition() -> None:
    chosen, union = await _choose(
        "serveStatic in the cloudflare pages handler", _defs(_PAGES, _WORKERS, _DENO)
    )
    assert chosen == [_PAGES]
    assert union == {}


async def test_a_question_naming_several_files_unions_only_those() -> None:
    chosen, union = await _choose(
        "how does serveStatic work on deno", _defs(_PAGES, _WORKERS, _DENO, _DENO_PKG)
    )
    assert chosen == []
    assert union == {"serveStatic": [_DENO, _DENO_PKG]}


async def test_a_question_naming_no_file_unions_every_definition() -> None:
    chosen, union = await _choose("how does serveStatic work", _defs(_PAGES, _WORKERS, _DENO))
    assert chosen == []
    assert union == {"serveStatic": [_PAGES, _WORKERS, _DENO]}


async def test_a_qualifier_that_matches_nothing_is_still_a_miss() -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _choose_anchor_symbols

    async def _verified(c):
        return {"path": c.file_path}

    homonyms = {"union": {}, "qualified_miss": []}
    chosen = await _choose_anchor_symbols(
        {"serveStatic": _defs(_PAGES, _DENO)},
        {"Router.serveStatic", "serveStatic"},
        _verified,
        homonyms,
        question="Router.serveStatic on deno",
    )
    assert chosen == []
    assert homonyms["qualified_miss"] == ["serveStatic"]


def test_the_path_sharing_the_most_question_words_is_the_one_named() -> None:
    from repowise.server.mcp_server._query_terms import paths_the_question_names

    paths = [_PAGES, _WORKERS]
    assert paths_the_question_names("serveStatic", paths, "the cloudflare pages handler") == [
        _PAGES
    ]
    assert paths_the_question_names("serveStatic", paths, "on cloudflare") == paths
    assert paths_the_question_names("serveStatic", paths, "how does it work") == []
