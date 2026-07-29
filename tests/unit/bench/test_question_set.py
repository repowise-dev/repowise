"""Loading a retrieval question set.

The point of every test here is that a malformed file stops the run. A question
set that silently drops a question, or reads a typo'd key as "no expectations",
produces a score that looks fine and means nothing.
"""

import pytest

from repowise_bench.eval.question_set import (
    QuestionSetError,
    RetrievalQuestion,
    load_retrieval_questions,
)

GOOD_LINE = '{"id": "q001", "question": "Where is the cache invalidated?", '
GOOD_LINE += '"expected_page_ids": ["file:a.py"]}'


def write_set(tmp_path, text):
    path = tmp_path / "questions.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadsAValidSet:
    def test_parses_every_field(self, tmp_path):
        path = write_set(tmp_path, GOOD_LINE)
        assert load_retrieval_questions(path) == [
            RetrievalQuestion(
                id="q001",
                question="Where is the cache invalidated?",
                expected_page_ids=frozenset({"file:a.py"}),
                tags=frozenset(),
            )
        ]

    def test_keeps_file_order(self, tmp_path):
        lines = "\n".join(
            f'{{"id": "q00{i}", "question": "q{i}?", "expected_page_ids": ["p{i}"]}}'
            for i in (1, 2, 3)
        )
        assert [q.id for q in load_retrieval_questions(write_set(tmp_path, lines))] == [
            "q001",
            "q002",
            "q003",
        ]

    def test_blank_lines_and_comments_are_skipped(self, tmp_path):
        path = write_set(tmp_path, f"# retrieval set\n\n{GOOD_LINE}\n\n")
        assert len(load_retrieval_questions(path)) == 1

    def test_tags_are_optional_and_read_when_present(self, tmp_path):
        line = (
            '{"id": "q001", "question": "Where?", "expected_page_ids": ["p1"], '
            '"tags": ["where", "hard"]}'
        )
        (question,) = load_retrieval_questions(write_set(tmp_path, line))
        assert question.tags == frozenset({"where", "hard"})


class TestMalformedSetRaises:
    def test_missing_file(self, tmp_path):
        with pytest.raises(QuestionSetError, match="does not exist"):
            load_retrieval_questions(tmp_path / "nope.jsonl")

    def test_file_with_no_questions(self, tmp_path):
        with pytest.raises(QuestionSetError, match="no questions"):
            load_retrieval_questions(write_set(tmp_path, "# only a comment\n"))

    def test_unparseable_json_names_the_line(self, tmp_path):
        path = write_set(tmp_path, f"{GOOD_LINE}\n{{not json\n")
        with pytest.raises(QuestionSetError, match="line 2"):
            load_retrieval_questions(path)

    def test_line_that_is_not_an_object(self, tmp_path):
        with pytest.raises(QuestionSetError, match="line 1"):
            load_retrieval_questions(write_set(tmp_path, '["q001"]'))

    @pytest.mark.parametrize("field", ["id", "question", "expected_page_ids"])
    def test_missing_required_field(self, tmp_path, field):
        line = '{"id": "q001", "question": "Where?", "expected_page_ids": ["p1"]}'.replace(
            f'"{field}"', '"unused"'
        )
        with pytest.raises(QuestionSetError):
            load_retrieval_questions(write_set(tmp_path, line))

    def test_empty_expected_page_ids_is_not_a_valid_expectation(self, tmp_path):
        line = '{"id": "q001", "question": "Where?", "expected_page_ids": []}'
        with pytest.raises(QuestionSetError, match="expected_page_ids"):
            load_retrieval_questions(write_set(tmp_path, line))

    def test_blank_question_text(self, tmp_path):
        line = '{"id": "q001", "question": "   ", "expected_page_ids": ["p1"]}'
        with pytest.raises(QuestionSetError, match="question"):
            load_retrieval_questions(write_set(tmp_path, line))

    def test_expected_page_ids_must_be_a_list_of_strings(self, tmp_path):
        line = '{"id": "q001", "question": "Where?", "expected_page_ids": [3]}'
        with pytest.raises(QuestionSetError, match="expected_page_ids"):
            load_retrieval_questions(write_set(tmp_path, line))

    def test_duplicate_id_raises(self, tmp_path):
        with pytest.raises(QuestionSetError, match="duplicate"):
            load_retrieval_questions(write_set(tmp_path, f"{GOOD_LINE}\n{GOOD_LINE}"))

    def test_unknown_key_raises_so_a_typo_cannot_be_read_as_no_expectation(self, tmp_path):
        """`expected_page_id` (singular) must not parse as a question with no expectations."""
        line = (
            '{"id": "q001", "question": "Where?", "expected_page_ids": ["p1"], '
            '"expected_page_id": "p2"}'
        )
        with pytest.raises(QuestionSetError, match="unknown"):
            load_retrieval_questions(write_set(tmp_path, line))
