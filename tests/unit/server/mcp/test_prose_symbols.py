"""Unit tests for the prose-keyed symbol leg (``_prose_symbols``).

The behaviour under test is that a question asked in English reaches the same
symbol rows as the same question typed as an identifier. These are pure-function
tests over term extraction and the corroboration guard; the SQL side is covered
by the retrieval eval rather than here.
"""

from __future__ import annotations

from repowise.server.mcp_server._prose_symbols import (
    _corroborated,
    content_terms,
)


class _Sym:
    """The two ``WikiSymbol`` fields the guard reads."""

    def __init__(self, name, file_path="src/pipeline/incremental.py", language="python"):
        self.name = name
        self.file_path = file_path
        self.language = language


def test_prose_question_yields_the_words_a_symbol_is_built_from():
    terms = content_terms("how does an incremental update persist symbols for changed files")
    assert "persist" in terms
    assert "symbols" in terms
    assert "incremental" in terms


def test_stopwords_and_short_tokens_are_dropped():
    terms = content_terms("how does the a of it be")
    assert terms == []


def test_snake_case_contributes_both_the_whole_token_and_its_parts():
    terms = content_terms("where is _persist_symbols called")
    assert "_persist_symbols" in terms
    assert "persist" in terms
    assert "symbols" in terms


def test_camel_case_is_split_into_its_humps():
    terms = content_terms("what does HTTPServerPool do")
    assert "server" in terms
    assert "pool" in terms


def test_terms_are_capped_so_a_pasted_bug_report_is_not_fifty_scans():
    question = " ".join(f"distinctword{i}" for i in range(40))
    assert len(content_terms(question)) <= 8


def test_one_common_word_does_not_seed_a_match():
    # "update" alone must not drag in every symbol named update-something.
    assert not _corroborated(_Sym("update_index"), {"update": 1.0}, saturated={"update"})


def test_two_distinct_terms_corroborate_each_other():
    assert _corroborated(
        _Sym("_persist_symbols"), {"persist": 1.0, "symbols": 1.0}, saturated=set()
    )


def test_two_saturated_terms_are_the_same_non_signal_twice():
    # Both words match most of the corpus, so their agreeing says nothing.
    assert not _corroborated(_Sym("cn"), {"get": 0.25, "file": 0.25}, saturated={"get", "file"})


def test_one_informative_term_plus_one_saturated_is_not_enough():
    assert not _corroborated(_Sym("build_config"), {"config": 1.0, "get": 0.25}, saturated={"get"})


def test_a_single_term_that_is_the_whole_name_is_enough():
    # The caller named the thing; the only difference is the underscores.
    assert _corroborated(_Sym("persist"), {"persist": 1.0}, saturated=set())


def test_a_saturated_term_cannot_carry_a_leaf_name_match_alone():
    assert not _corroborated(_Sym("get"), {"get": 0.25}, saturated={"get"})


def test_generic_member_name_alone_is_weak_even_when_not_saturated():
    assert not _corroborated(_Sym("get"), {"get": 1.0}, saturated=set())


def test_generic_member_survives_when_owner_or_module_context_agrees():
    assert _corroborated(
        _Sym("get"),
        {"get": 0.25, "omission": 1.0, "store": 1.0},
        saturated={"get"},
    )


def test_no_matched_terms_is_never_corroborated():
    assert not _corroborated(_Sym("_persist_symbols"), {}, saturated=set())
