"""What the full-text query asks for, and how much of the corpus that matches.

Both backends built an expression that could not retrieve. SQLite OR-ed every
token with a prefix wildcard, so a question matched two thirds of the corpus and
BM25 was left to sort out the rest -- a full scan with tie-breaking. PostgreSQL
handed the raw question to ``plainto_tsquery``, which joins every lexeme with
AND, so a paraphrased question matched almost nothing. Opposite failures, same
cause: nothing looked at how many pages a term could actually distinguish.

The fix is a document-frequency ceiling. A term matching more of the corpus than
the ceiling is dropped from the expression, because it cannot tell two pages
apart. Measured on a 3,678-page corpus, that moves the median candidate set from
65% of the corpus to 20% with recall@5 unchanged.

AND-first is deliberately *not* what this does, and the tests below pin why: a
question is a paraphrase of the page that answers it, so requiring all of its
terms excludes the right page far more often than it narrows usefully.
"""

from __future__ import annotations

import pytest
from sqlalchemy.sql import text

from repowise.core.persistence.search import FullTextSearch, _build_fts5_query


def _df(counts: dict[str, int], total: int):
    """A document-frequency lookup over fixed counts. ``""`` is the corpus size."""

    def lookup(term: str) -> int:
        return total if term == "" else counts.get(term, 0)

    return lookup


# --- term selection --------------------------------------------------------


class TestMeaningfulTerms:
    def test_stop_words_and_single_characters_go(self):
        from repowise.core.persistence.search import _meaningful_terms

        assert _meaningful_terms("How does the a b walker work") == ["walker", "work"]

    def test_punctuation_is_not_a_term(self):
        from repowise.core.persistence.search import _meaningful_terms

        assert _meaningful_terms("walker, nested-git (checkouts)!") == [
            "walker",
            "nested",
            "git",
            "checkouts",
        ]


class TestPrefixWildcard:
    """The wildcard buys morphology on long terms and floods on short ones."""

    def test_a_long_term_keeps_its_wildcard(self):
        assert '"invalidation"*' in _build_fts5_query("invalidation")

    def test_a_short_term_is_matched_exactly(self):
        # "set*" matches settings, setup, setter, setdefault: a corpus-wide hit
        # from a three-letter word.
        assert _build_fts5_query("set") == '"set"'

    def test_the_cut_is_where_the_constant_says(self):
        from repowise.core.persistence.search import _PREFIX_MIN_CHARS

        short = "x" * (_PREFIX_MIN_CHARS - 1)
        long = "x" * _PREFIX_MIN_CHARS
        assert _build_fts5_query(short) == f'"{short}"'
        assert _build_fts5_query(long) == f'"{long}"*'


# --- the frequency ceiling -------------------------------------------------


class TestFrequencyCeiling:
    def test_without_a_lookup_every_term_is_kept(self):
        """The prior behaviour, which every caller without counts still gets."""
        built = _build_fts5_query("walker nested checkouts")
        assert built.count(" OR ") == 2

    def test_a_term_matching_most_of_the_corpus_is_dropped(self):
        counts = {"walker": 30, "file": 900, "checkouts": 40, "nested": 25, "descending": 12}
        built = _build_fts5_query(
            "walker file checkouts nested descending", _df(counts, total=1000)
        )
        assert "walker" in built
        assert "checkouts" in built
        assert "file" not in built

    def test_the_floor_outranks_the_ceiling_on_a_short_question(self):
        """A question with too few selective terms keeps its rarest few even when
        the ceiling would reject them.

        This is the measured configuration, not an accident of ordering: the
        eval's questions run 8 to 15 meaningful terms, where the ceiling decides,
        and the floor only takes over on questions too short to narrow safely.
        """
        counts = {"walker": 30, "file": 900, "checkouts": 40}
        built = _build_fts5_query("walker file checkouts", _df(counts, total=1000))
        assert "file" in built, "with only three terms the floor keeps all of them"

    def test_a_term_just_under_the_ceiling_survives(self):
        from repowise.core.persistence.search import _DF_CEILING

        total = 1000
        counts = {"walker": 10, "index": int(_DF_CEILING * total) - 1, "nested": 10}
        built = _build_fts5_query("walker index nested", _df(counts, total))
        assert "index" in built

    def test_the_ceiling_never_empties_the_query(self):
        """A question written entirely from common words still has to match
        something: a thin match beats none, and vector retrieval is answering
        alongside this."""
        from repowise.core.persistence.search import _MIN_KEPT_TERMS

        counts = {"file": 900, "page": 950, "index": 800, "does": 990}
        built = _build_fts5_query("file page index does", _df(counts, total=1000))
        kept = [t for t in ("file", "page", "index", "does") if t in built]
        assert len(kept) == _MIN_KEPT_TERMS
        # The rarest ones, not an arbitrary three.
        assert set(kept) == {"index", "file", "page"}

    def test_terms_are_or_ed_never_and_ed(self):
        """AND is the tempting fix and the wrong one.

        On a 3,678-page corpus, AND over the meaningful terms of 99 developer
        questions returned no rows at all for 65 of them, and held the expected
        page for 25 of 99 against 99 of 99 for OR. A question is a paraphrase;
        its rarest words are often the asker's, not the page's.
        """
        built = _build_fts5_query("walker nested checkouts", _df({}, total=0))
        assert " AND " not in built
        assert built.count(" OR ") == 2

    def test_an_empty_corpus_keeps_every_term(self):
        """Nothing is measurable against zero pages, so nothing is dropped."""
        built = _build_fts5_query("walker file checkouts", _df({}, total=0))
        assert built.count(" OR ") == 2

    def test_a_question_of_only_stop_words_falls_back_to_a_phrase(self):
        assert _build_fts5_query("how does it") == '"how does it"'


class TestPostgresTerms:
    """The same terms, in the other backend's syntax."""

    def test_a_long_term_is_a_prefix_lexeme(self):
        from repowise.core.persistence.search import _pg_term

        assert _pg_term("invalidation") == "invalidation:*"

    def test_a_short_term_is_exact(self):
        from repowise.core.persistence.search import _pg_term

        assert _pg_term("set") == "set"


# --- against a real index --------------------------------------------------


@pytest.fixture
async def fts(async_engine):
    fs = FullTextSearch(async_engine)
    await fs.ensure_index()
    return fs


async def _index_corpus(fts, common_pages: int) -> None:
    """One page that answers a question, plus *common_pages* that only share its
    filler words -- the shape that makes a whole-corpus match possible."""
    await fts.index(
        "gold",
        "File: walk.py",
        "The walker skips any nested checkout it finds while descending.",
    )
    for i in range(common_pages):
        await fts.index(f"noise{i}", f"File: other{i}.py", "This file documents a page index.")


class TestCandidateSetSize:
    async def test_a_common_term_no_longer_drags_the_corpus_in(self, fts, async_engine):
        """The regression this exists to catch: one filler word matching every
        page, and BM25 asked to rank the whole corpus afterwards."""
        await _index_corpus(fts, common_pages=40)

        hits = await fts.search("which file documents how the walker skips a nested checkout", 5)

        assert hits, "the answering page must still be found"
        assert hits[0].page_id == "gold"

        question = "which file documents how the walker skips a nested checkout"
        async with async_engine.connect() as conn:

            async def df(term: str) -> int:
                return await fts._document_frequency(conn, term)

            expression = await fts._build_selective_query(question, df)
            matched = await conn.execute(
                text("SELECT count(*) FROM page_fts WHERE page_fts MATCH :q"),
                {"q": expression},
            )
            candidates = matched.scalar()

        assert candidates < 41, (
            f"{candidates} of 41 pages matched by {expression!r}: the filler terms "
            "are still in the query"
        )

    async def test_results_stay_ordered_by_score(self, fts):
        """Two MATCH expressions' BM25 scales are not comparable, so a widened
        retry must not out-score the narrow hits it was appended to."""
        await _index_corpus(fts, common_pages=8)

        hits = await fts.search("walker nested checkout descending page index file", 10)

        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True), scores

    async def test_a_question_whose_terms_are_all_common_still_retrieves(self, fts):
        await _index_corpus(fts, common_pages=30)

        hits = await fts.search("this file documents a page index", 5)

        assert hits
