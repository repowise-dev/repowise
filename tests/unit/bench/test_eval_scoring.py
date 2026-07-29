"""Scoring maths for the answer retrieval eval.

Every expected value in this file is computed by hand in the docstring or a
comment. If an assertion here needs a calculator, the test is wrong.
"""

import pytest

from repowise_bench.eval.scoring import (
    RetrievalScores,
    ScoringError,
    recall_at_k,
    reciprocal_rank,
    score_retrieval,
)


class TestRecallAtK:
    def test_one_of_two_expected_inside_top_five(self):
        """Expected pages sit at ranks 2 and 7 of 10 → 1 of 2 inside top-5 → 0.5."""
        retrieved = [f"p{i}" for i in range(1, 11)]
        assert recall_at_k(retrieved, {"p2", "p7"}, k=5) == 0.5

    def test_all_expected_inside_top_k(self):
        assert recall_at_k(["a", "b", "c"], {"a", "c"}, k=5) == 1.0

    def test_none_expected_retrieved(self):
        assert recall_at_k(["a", "b", "c"], {"z"}, k=5) == 0.0

    def test_hit_below_the_cut_does_not_count(self):
        """The only expected page is at rank 6, so recall@5 is 0 even though it was found."""
        retrieved = [f"p{i}" for i in range(1, 11)]
        assert recall_at_k(retrieved, {"p6"}, k=5) == 0.0

    def test_short_result_list_is_not_padded(self):
        """Two results, one expected and present → 1.0, not 1/5."""
        assert recall_at_k(["a", "b"], {"b"}, k=5) == 1.0

    def test_duplicate_retrieved_ids_do_not_consume_the_window(self):
        """A page repeated at ranks 1-5 must not push p6 out of the top-5 window."""
        retrieved = ["dup", "dup", "dup", "dup", "dup", "p6"]
        assert recall_at_k(retrieved, {"p6"}, k=5) == 1.0

    def test_empty_expected_set_raises(self):
        with pytest.raises(ScoringError, match="no expected pages"):
            recall_at_k(["a"], set(), k=5)

    def test_non_positive_k_raises(self):
        with pytest.raises(ScoringError, match="k must be >= 1"):
            recall_at_k(["a"], {"a"}, k=0)


class TestReciprocalRank:
    def test_first_expected_at_rank_two(self):
        """Ranks 2 and 7 are expected; the first is rank 2 → 1/2."""
        retrieved = [f"p{i}" for i in range(1, 11)]
        assert reciprocal_rank(retrieved, {"p2", "p7"}) == 0.5

    def test_first_expected_at_rank_one(self):
        assert reciprocal_rank(["a", "b"], {"a"}) == 1.0

    def test_first_expected_at_rank_four(self):
        assert reciprocal_rank(["a", "b", "c", "d"], {"d"}) == 0.25

    def test_no_expected_retrieved_scores_zero(self):
        assert reciprocal_rank(["a", "b"], {"z"}) == 0.0

    def test_duplicates_do_not_shift_the_rank(self):
        """'dup' occupying ranks 1-2 means the real hit is rank 2, not rank 3."""
        assert reciprocal_rank(["dup", "dup", "hit"], {"hit"}) == 0.5

    def test_empty_expected_set_raises(self):
        with pytest.raises(ScoringError, match="no expected pages"):
            reciprocal_rank(["a"], set())


class TestScoreRetrieval:
    def test_aggregate_is_the_mean_over_questions(self):
        """
        q1: expected p1, retrieved at rank 1 → recall@5 = 1.0, RR = 1.0
        q2: expected p9, retrieved at rank 9 → recall@5 = 0.0, RR = 1/9
        mean recall@5 = (1.0 + 0.0) / 2       = 0.5
        mean MRR      = (1.0 + 0.111...) / 2  = 0.5555...
        """
        scores = score_retrieval(
            {
                "q1": (["p1", "p2"], {"p1"}),
                "q2": ([f"p{i}" for i in range(1, 11)], {"p9"}),
            },
            k=5,
        )
        assert scores == RetrievalScores(
            n_questions=2,
            recall_at_k=0.5,
            mrr=pytest.approx(0.5555555, abs=1e-6),
            k=5,
            n_empty_results=0,
        )

    def test_empty_result_list_scores_zero_and_is_counted(self):
        """A retrieval that returned nothing is a zero, not a skip - the count proves it."""
        scores = score_retrieval({"q1": ([], {"p1"})}, k=5)
        assert scores.recall_at_k == 0.0
        assert scores.mrr == 0.0
        assert scores.n_empty_results == 1

    def test_no_questions_raises(self):
        with pytest.raises(ScoringError, match="no questions"):
            score_retrieval({}, k=5)
