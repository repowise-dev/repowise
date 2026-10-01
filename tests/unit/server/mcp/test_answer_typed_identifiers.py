"""A typed identifier is one name, not the words it is spelled with.

"How is ``report_card`` built" names symbols such as ``build_report_card``. Read
as the words ``report`` and ``card`` it matched the symbol index too weakly to
enter the pool, ranked below any page whose prose said "report card", and put
the question into the UI domain because ``card`` is a UI word.
"""

from __future__ import annotations

from repowise.server.mcp_server._prose_symbols import _corroborated, _covers
from repowise.server.mcp_server._retrieval_rank import rerank_by_context_coverage
from repowise.server.mcp_server.tool_answer.retrieval import _detect_question_domain
from repowise.server.mcp_server.tool_search_symbols import _tokens


class _Sym:
    def __init__(self, name: str) -> None:
        self.name = name


def test_a_typed_compound_is_covered_by_a_longer_name_holding_its_words():
    assert _covers("report_card", _tokens("build_report_card"))
    assert not _covers("report_card", _tokens("build_report"))


def test_a_term_with_no_words_left_covers_nothing():
    assert not _covers("_x_", _tokens("anything_at_all"))


def test_a_typed_compound_corroborates_on_its_own_when_its_words_are_common():
    covered = {"report_card": 1.0, "report": 0.25, "card": 0.25}
    assert _corroborated(_Sym("build_report_card"), covered, saturated={"report", "card"})


def test_a_single_plain_word_still_does_not_corroborate():
    assert not _corroborated(_Sym("build_report"), {"report": 1.0}, saturated=set())


def _hit(path: str, score: float, summary: str, names: list[str] | None = None) -> dict:
    hit = {"target_path": path, "score": score, "title": path, "summary": summary}
    if names is not None:
        hit["_symbol_names"] = names
    return hit


def test_the_file_defining_the_named_symbol_outranks_prose_that_spells_its_words():
    prose = _hit("web/report/card.tsx", 1.0, "Renders the report card view.")
    defining = _hit("lib/build.py", 1.0, "Builds documents.", names=["build_report_card"])

    ranked = rerank_by_context_coverage(
        [prose, defining], "how is report_card built", score_key="score", floor=0.5
    )

    assert ranked[0]["target_path"] == "lib/build.py"


def test_an_identifier_holding_a_ui_word_does_not_make_the_question_a_ui_one():
    assert _detect_question_domain("how is report_page generated") is None
    assert _detect_question_domain("how is the settings page rendered") == "ui"


def test_domain_words_are_matched_whole():
    # ``client`` is not ``cli``, ``overview`` is not ``view``.
    assert _detect_question_domain("how does the client retry") is None
    assert _detect_question_domain("where is the overview built") is None
    assert _detect_question_domain("how does the orchestration layer start") == "backend"


def test_a_compound_needs_whole_words_inside_the_longer_name():
    # ``this_test`` does not hold ``is_test``; prose "report card" is not one name.
    wrong = _hit("lib/a.py", 1.0, "Guards this_test and the report card.")
    right = _hit("lib/b.py", 1.0, "Checks flags.", names=["check_is_test"])

    ranked = rerank_by_context_coverage(
        [wrong, right], "where is is_test decided", score_key="score", floor=0.5
    )

    assert ranked[0]["target_path"] == "lib/b.py"


def test_a_camel_case_compound_matches_the_same_words_in_snake_case():
    prose = _hit("web/report/card.tsx", 1.0, "Renders the report card view.")
    defining = _hit("lib/build.py", 1.0, "Builds documents.", names=["build_report_card"])

    ranked = rerank_by_context_coverage(
        [prose, defining], "how is reportCard built", score_key="score", floor=0.5
    )

    assert ranked[0]["target_path"] == "lib/build.py"


def test_hyphenated_and_suffixed_domain_words_still_count():
    assert _detect_question_domain("where is the settings-page layout") == "ui"
    assert _detect_question_domain("how is the api-key stored") == "backend"
    assert _detect_question_domain("how does the viewer scroll") == "ui"
    assert _detect_question_domain("how is client-side state kept") == "ui"
