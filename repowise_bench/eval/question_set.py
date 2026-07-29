"""Loading the retrieval question set.

The set is JSONL - one question per line - so a diff shows exactly which
questions a change added or reworded, which a single JSON array would not.

Every validation here raises. None of it warns and continues. A question set
that loses a question, or reads a mistyped key as "this question expects
nothing", still produces a score, and that score is worse than no score at all
because it looks like a measurement.

Line format::

    {"id": "q001", "question": "Where is X?", "expected_page_ids": ["file:a.py"]}

``tags`` is the one optional key. Blank lines and ``#`` comment lines are
skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_KEYS = frozenset({"id", "question", "expected_page_ids"})
OPTIONAL_KEYS = frozenset({"tags"})


class QuestionSetError(ValueError):
    """A question-set file is missing, empty, or malformed."""


@dataclass(frozen=True)
class RetrievalQuestion:
    """One question and the page ids a correct retrieval must surface."""

    id: str
    question: str
    expected_page_ids: frozenset[str]
    tags: frozenset[str] = field(default_factory=frozenset)


def _fail(path: Path, line_number: int, problem: str) -> QuestionSetError:
    return QuestionSetError(f"{path}, line {line_number}: {problem}")


def _read_string_list(path: Path, line_number: int, raw: object, key: str) -> frozenset[str]:
    if not isinstance(raw, list) or not raw:
        raise _fail(path, line_number, f"{key} must be a non-empty list of strings")
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise _fail(path, line_number, f"{key} must contain only non-empty strings")
    return frozenset(entry.strip() for entry in raw)


def _parse_line(path: Path, line_number: int, text: str) -> RetrievalQuestion:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(path, line_number, f"is not valid JSON ({exc.msg})") from exc

    if not isinstance(payload, dict):
        raise _fail(path, line_number, "must be a JSON object")

    keys = set(payload)
    if missing := REQUIRED_KEYS - keys:
        raise _fail(path, line_number, f"missing required key(s): {', '.join(sorted(missing))}")
    if unknown := keys - REQUIRED_KEYS - OPTIONAL_KEYS:
        raise _fail(path, line_number, f"unknown key(s): {', '.join(sorted(unknown))}")

    for key in ("id", "question"):
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise _fail(path, line_number, f"{key} must be a non-empty string")

    tags = (
        _read_string_list(path, line_number, payload["tags"], "tags")
        if "tags" in payload
        else frozenset()
    )

    return RetrievalQuestion(
        id=payload["id"].strip(),
        question=payload["question"].strip(),
        expected_page_ids=_read_string_list(
            path, line_number, payload["expected_page_ids"], "expected_page_ids"
        ),
        tags=tags,
    )


def load_retrieval_questions(path: str | Path) -> list[RetrievalQuestion]:
    """Read and validate a JSONL retrieval question set, preserving file order.

    Raises ``QuestionSetError`` on anything that would make a run's score
    unreliable: a missing file, a file with no questions, a line that does not
    parse, a missing or blank field, an unknown key, or a duplicate question id.
    """
    path = Path(path)
    if not path.is_file():
        raise QuestionSetError(f"question set does not exist: {path}")

    questions: list[RetrievalQuestion] = []
    seen_ids: set[str] = set()

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw_line.strip()
        if not text or text.startswith("#"):
            continue

        question = _parse_line(path, line_number, text)
        if question.id in seen_ids:
            raise _fail(path, line_number, f"duplicate question id: {question.id}")
        seen_ids.add(question.id)
        questions.append(question)

    if not questions:
        raise QuestionSetError(f"question set contains no questions: {path}")

    return questions
