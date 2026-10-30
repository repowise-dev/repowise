"""Grades for correct answers the cascade under-rated, and the guards that keep
wrong ones from gaining: a closing caveat is not a hedge, a verified constant
line the answer states earns high, and a name no code carries grades low."""

from __future__ import annotations

import asyncio

from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _grade_answer,
    stated_value_line,
)
from repowise.server.mcp_server.tool_answer.payload import (
    _held_at_medium_note,
    _high_confidence_note,
)
from repowise.server.mcp_server.tool_answer.symbols import _names_nothing_in_code, _TopicGuard

_TS = "core/ingestion/extractors/imports/ts_js.py"
_TS_HITS = [
    {
        "target_path": _TS,
        "score": 4.4,
        "summary": "TypeScript and JavaScript import extraction for ingestion, "
        "including dynamic import() call expressions.",
        "symbols": [
            {
                "name": "_is_dynamic_esm_import",
                "kind": "function",
                "signature": "def _is_dynamic_esm_import(stmt_node)",
                "_verified": True,
            }
        ],
    },
    {
        "target_path": "core/ingestion/extractors/bindings/ts_js.py",
        "score": 3.2,
        "symbols": [{"name": "dynamic_import_bindings", "kind": "function", "_verified": True}],
    },
]
_TS_QUESTION = "How are TypeScript dynamic import() expressions handled in ingestion?"
_TS_CLAIM = (
    "Dynamic `import()` calls are detected by `_is_dynamic_esm_import`, which checks for a "
    "call_expression whose function node is `import`; `dynamic_import_bindings` binds them."
)
_TS_CAVEAT = "The excerpt does not include the body of `_ts_js_statement`, which wires it in."


def _grade(question, answer, hits, *, dominance="gap", ids=(), **kw):
    return _grade_answer(
        question=question,
        question_ids=set(ids),
        answer_text=answer,
        hits=hits,
        citations=[hits[0]["target_path"]],
        symbol_bodies=[],
        served_named_body=False,
        dominance=dominance,
        **kw,
    )


# --- a closing caveat after a grounded claim is not a hedge ------------------


def test_a_closing_caveat_holds_a_grounded_answer_at_medium_not_low() -> None:
    grade = _grade(
        _TS_QUESTION, f"{_TS_CLAIM} {_TS_CAVEAT}", _TS_HITS,
        ids={"TypeScript"}, unindexed_names={"TypeScript"},
    )
    assert not grade.hedged
    assert grade.confidence == "medium"
    assert grade.admitted_gap == _TS_CAVEAT


def test_the_same_admission_before_any_claim_is_still_a_hedge() -> None:
    grade = _grade(_TS_QUESTION, f"{_TS_CAVEAT} {_TS_CLAIM}", _TS_HITS, ids={"TypeScript"})
    assert grade.hedged
    assert grade.confidence == "low"


def test_naming_only_what_the_question_named_is_no_claim() -> None:
    assert _answer_is_hedged(
        "`RetryPolicyManager` is in the excerpts' list. The excerpts do not contain its body.",
        "How does RetryPolicyManager back off?",
    )


def test_an_admission_is_the_answer_when_retrieval_lacks_what_was_asked() -> None:
    hits = [{"target_path": "completions.go", "score": 3.0, "summary": "Shell completion.",
             "symbols": [{"name": "GenBashCompletionV2", "kind": "function"}]}]
    answer = (
        "Cobra generates completions through `GenBashCompletionV2`. "
        "The excerpts do not contain any Kubernetes reconciliation code."
    )
    grade = _grade("Where does cobra implement Kubernetes reconciliation?", answer, hits)
    assert grade.hedged and grade.confidence == "low"


def test_an_unanswered_value_is_a_hedge() -> None:
    assert _answer_is_hedged(
        "The default value of the floor in `get_answer` is not present.",
        "What is the default value of the floor in get_answer?",
    )


# --- a verified constant line the answer states earns high ------------------

_CFG = "packages/server/src/repowise/server/mcp_server/tool_answer/config.py"
_FLOOR = {
    "name": "_HIGH_CONFIDENCE_SCORE_FLOOR",
    "kind": "constant",
    "signature": "_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5",
    "start_line": 299,
    "_verified": True,
}
_FLOOR_QUESTION = "What is the default value of the high confidence score floor in get_answer?"
_FLOOR_ANSWER = "The floor is 1.5: `_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5` in config.py."


def _floor_hits(floor: dict | None = None, extra: list[dict] | None = None) -> list[dict]:
    return [
        {"target_path": _CFG, "score": 2.0, "summary": "Tuning constants for get_answer.",
         "symbols": [floor or _FLOOR]},
        {"target_path": "tool_answer/answer.py", "score": 2.1,
         "symbols": [{"name": "get_answer", "kind": "function", "_matched": True}]},
        *(extra or []),
    ]


def test_a_stated_verified_constant_earns_high_on_an_ambiguous_retrieval() -> None:
    hits = _floor_hits()
    before = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, hits, dominance=None, ids={"get_answer"})
    assert before.confidence == "high"
    assert before.high_reason == "value_line"
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, _FLOOR_ANSWER, hits) == {
        "path": _CFG, "name": "_HIGH_CONFIDENCE_SCORE_FLOOR", "line": 299,
    }


def test_an_exact_name_lookup_of_a_variable_earns_high() -> None:
    alias = {
        "name": "_projected_get_answer", "kind": "variable", "_verified": True, "_matched": True,
        "signature": "_projected_get_answer = project_answer(_get_answer_raw)",
    }
    hits = [
        {"target_path": "tool_answer/answer.py", "score": 2.0, "symbols": [alias]},
        {"target_path": "tool_answer/projection.py", "score": 2.1},
    ]
    answer = "`_projected_get_answer` is `project_answer(_get_answer_raw)`, the projected tool."
    grade = _grade("_projected_get_answer", answer, hits, dominance=None,
                   ids={"_projected_get_answer"})
    assert (grade.confidence, grade.high_reason) == ("high", "value_line")


def test_a_lookup_answer_misreading_the_assignment_earns_nothing() -> None:
    alias = {
        "name": "_projected_get_answer", "kind": "variable", "_verified": True, "_matched": True,
        "signature": "_projected_get_answer = project_answer(_get_answer_raw)",
    }
    hits = [
        {"target_path": "tool_answer/answer.py", "score": 2.0, "symbols": [alias]},
        {"target_path": "tool_answer/projection.py", "score": 2.1},
    ]
    answer = "`_projected_get_answer` is an alias of `get_answer`."
    grade = _grade("_projected_get_answer", answer, hits, dominance=None,
                   ids={"_projected_get_answer"})
    assert grade.confidence == "medium"


def test_a_wrong_value_beside_the_right_one_earns_nothing() -> None:
    hits = _floor_hits()
    answer = (
        "It is set by `_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5`. "
        "At runtime `_HIGH_CONFIDENCE_SCORE_FLOOR` is raised to 2.0."
    )
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_a_longer_name_holding_the_constant_names_nothing() -> None:
    hits = _floor_hits()
    answer = "`_HIGH_CONFIDENCE_SCORE_FLOOR_MAX` is 1.5."
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_a_value_too_short_to_match_on_earns_nothing() -> None:
    hits = _floor_hits({**_FLOOR, "signature": '_HIGH_CONFIDENCE_SCORE_FLOOR = "a"'})
    answer = "`_HIGH_CONFIDENCE_SCORE_FLOOR` is a constant in config.py."
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_an_unverified_line_earns_nothing() -> None:
    hits = _floor_hits({**_FLOOR, "_verified": False})
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, hits, dominance=None, ids={"get_answer"})
    assert grade.confidence == "medium"


def test_a_value_the_line_does_not_assign_earns_nothing() -> None:
    answer = "The floor is 2.0, from `_HIGH_CONFIDENCE_SCORE_FLOOR`."
    grade = _grade(_FLOOR_QUESTION, answer, _floor_hits(), dominance=None, ids={"get_answer"})
    assert grade.confidence != "high"


def test_a_name_defined_twice_in_the_hits_earns_nothing() -> None:
    twin = {"target_path": "other/config.py", "score": 1.0, "symbols": [{**_FLOOR}]}
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, _floor_hits(extra=[twin]), dominance=None,
                   ids={"get_answer"})
    assert grade.confidence == "medium"


def test_a_constant_whose_page_misses_a_question_word_earns_nothing() -> None:
    # Two constants share the name; the one served is the pool's, not the SSG one asked about.
    pool = {"name": "DEFAULT_CONCURRENCY", "kind": "constant", "_verified": True,
            "signature": "const DEFAULT_CONCURRENCY = 1024"}
    hits = [
        {"target_path": "src/utils/concurrent.ts", "score": 2.0, "summary": "A task pool.",
         "symbols": [pool]},
        {"target_path": "src/helper/css/index.ts", "score": 2.1},
    ]
    answer = "The default concurrency is 1024 (`DEFAULT_CONCURRENCY`)."
    assert stated_value_line("What is the default concurrency for SSG in hono?",
                             {"SSG"}, answer, hits) is None


def test_the_value_line_note_points_at_the_quote() -> None:
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, _floor_hits(), dominance=None,
                   ids={"get_answer"})
    assert "verified line" in _high_confidence_note(grade, "")


# --- names no symbol carries ------------------------------------------------

_REDIRECT_HITS = [
    {"target_path": "httpx/_client.py", "score": 4.0, "summary": "Client redirect handling.",
     "symbols": [{"name": "_redirect_method", "kind": "method", "_verified": True,
                  "source_excerpt": 'if status == SEE_OTHER and method != "HEAD": method = "GET"'}]},
    {"target_path": "httpx/_types.py", "score": 2.8},
]
_REDIRECT_Q = "How does httpx decide the HTTP method when following a redirect?"
_REDIRECT_A = "`_redirect_method` turns a 303 into GET unless the method is HEAD."


def test_a_word_no_symbol_carries_does_not_demote_as_an_unhydrated_identifier() -> None:
    kept = _grade(_REDIRECT_Q, _REDIRECT_A, _REDIRECT_HITS, ids={"HTTP"},
                  unindexed_names={"HTTP"})
    demoted = _grade(_REDIRECT_Q, _REDIRECT_A, _REDIRECT_HITS, ids={"HTTP"})
    assert kept.confidence == "high"
    assert demoted.confidence == "medium"


def test_a_name_nothing_defines_writes_or_serves_grades_low() -> None:
    hits = [{"target_path": "packages/ui/src/zoom/types.ts", "score": 2.5,
             "summary": "Containment tree types for the zoom map."}]
    q = "How does the FileTree component render nested folders?"
    answer = "Folders nest through containment nodes in `ZoomNode`."
    grade = _grade(q, answer, hits, dominance="ratio", ids={"FileTree"},
                   unindexed_names={"FileTree"}, absent_names=("FileTree",))
    assert grade.confidence == "low"
    assert grade.absent_names == ("FileTree",)


def test_a_name_the_retrieval_serves_is_not_absent() -> None:
    hits = [{"target_path": "packages/ui/src/FileTree.tsx", "score": 2.5}]
    grade = _grade("How does FileTree render?", "`FileTree` renders rows.", hits,
                   dominance="ratio", absent_names=("FileTree",))
    assert grade.absent_names == ()


class _Guard:
    def __init__(self, written: set[str], unscanned: set[str] = frozenset()):
        self.names, self.unscanned = written, unscanned

    async def written(self, token: str, scope: str | None = None) -> bool | None:
        return None if token in self.unscanned else token in self.names


def test_only_code_shaped_names_no_line_writes_are_absent() -> None:
    guard = _Guard({"RealThing"}, unscanned={"BigRepoThing"})

    def absent(token: str) -> bool:
        return asyncio.run(_names_nothing_in_code(token, guard))

    assert absent("FileTree")
    assert absent("build_answer_index_v2")
    assert not absent("RealThing")
    assert not absent("SSG")  # an acronym is prose often enough
    assert not absent("Client.get")
    assert not absent("BigRepoThing")  # the scan stopped short: unknown, not absent


# --- markdown markup does not hide a hedge -----------------------------------

_TREE_Q = "How does the FileTree component in packages/ui render nested folders?"
_TREE_HITS = [
    {"target_path": "packages/ui/src/graph/code-graph-tree.tsx", "score": 2.3,
     "summary": "Outline tree of directories and files.",
     "symbols": [{"name": "CodeGraphTree", "kind": "function"}]},
    {"target_path": "packages/ui/src/zoom/types.ts", "score": 1.6},
]


def test_an_emphasised_admission_is_still_a_hedge() -> None:
    answer = (
        "## What the excerpts show\n\nThe retrieved material does **not include the "
        "`FileTree` component** or its source body. The closest symbol is `CodeGraphTree`."
    )
    grade = _grade(_TREE_Q, answer, _TREE_HITS, dominance="ratio", ids={"FileTree"},
                   unindexed_names={"FileTree"})
    assert grade.hedged
    assert grade.confidence == "low"


def test_markup_inside_an_identifier_is_not_stripped() -> None:
    # Only edge underscores go, so a snake_case name cannot turn into a phrase.
    assert not _answer_is_hedged("`does_not_include_tests` filters the walk.", "What filters it?")


# --- a scoped question's name nothing under the scope writes ------------------


def _scanned_guard(rows: list[str], *, complete: bool = True) -> _TopicGuard:
    guard = _TopicGuard(None, [], "", set())
    guard._scans["FileTree"] = ([{"path": p, "line": 1, "text": "FileTree"} for p in rows], complete)
    return guard


def test_a_name_written_only_outside_the_scope_is_absent_in_it() -> None:
    guard = _scanned_guard(["packages/server/symbols.py"])
    assert asyncio.run(_names_nothing_in_code("FileTree", guard, "packages/ui/src"))
    assert not asyncio.run(_names_nothing_in_code("FileTree", guard))


def test_a_name_written_under_the_scope_is_not_absent() -> None:
    inside = _scanned_guard(["packages/ui/src/tree/FileTree.tsx"])
    assert not asyncio.run(_names_nothing_in_code("FileTree", inside, "packages/ui/src"))
    # A sibling directory sharing the prefix is outside the scope.
    sibling = _scanned_guard(["packages/ui/src-old/FileTree.tsx"])
    assert asyncio.run(_names_nothing_in_code("FileTree", sibling, "packages/ui/src"))
    # A scan that stopped short proves nothing.
    partial = _scanned_guard(["packages/server/symbols.py"], complete=False)
    assert not asyncio.run(_names_nothing_in_code("FileTree", partial, "packages/ui/src"))


# --- agreement alone needs a strong top score ---------------------------------

_CORS_Q = "How does the cors middleware handle a preflight OPTIONS request?"
_CORS_A = "The `cors` middleware sets `Access-Control-Allow-Methods` and returns 204."


def _cors_hits(top: float) -> list[dict]:
    return [
        {"target_path": "src/middleware/cors/index.ts", "score": top,
         "summary": "cors middleware: preflight OPTIONS request handling.",
         "symbols": [{"name": "cors", "kind": "function", "_matched": True}]},
        {"target_path": "src/client/types.ts", "score": top - 0.03},
    ]


def test_agreement_on_a_weak_top_score_holds_at_medium_with_a_note() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(3.27), dominance="agreement")
    assert grade.confidence == "medium"
    assert grade.weak_agreement
    assert "3.27" in _held_at_medium_note(grade)


def test_agreement_on_a_strong_top_score_stays_high() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(4.2), dominance="agreement")
    assert grade.confidence == "high"
    assert grade.high_reason == "agreement"
    assert not grade.weak_agreement


def test_the_agreement_floor_leaves_other_dominance_alone() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(3.27), dominance="gap")
    assert grade.confidence == "high"
    assert not grade.weak_agreement


def test_a_specific_gate_keeps_its_finding_under_weak_agreement() -> None:
    hits = _cors_hits(3.27)
    grade = _grade(_CORS_Q, _CORS_A, hits, dominance="agreement", ids={"cors"},
                   split_names={"cors": ["src/middleware/cors/index.ts", "src/adapter/cors.ts"]})
    assert grade.confidence == "medium"
    assert grade.split_names
    assert not grade.weak_agreement
