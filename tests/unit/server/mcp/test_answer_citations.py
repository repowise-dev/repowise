"""Citations follow the files a synthesized answer names, not only the retrieval hits."""

from __future__ import annotations

from pathlib import Path

from repowise.server.mcp_server.tool_answer.citations import answer_files
from repowise.server.mcp_server.tool_answer.confidence import _grade_answer

CASCADE = "packages/core/src/repowise/core/generation/cascade.py"
DETECTOR = "packages/core/src/repowise/core/ingestion/change_detector.py"


def _hit(path: str, score: float = 1.0) -> dict:
    return {"target_path": path, "score": score}


def test_named_pool_file_is_cited_and_ruled_out_hit_is_not() -> None:
    # The reported answer: the budget lives in change_detector.py, which is not
    # a hit, and the answer says the top hit does not compute it.
    answer = (
        f"The budget is computed by `compute_adaptive_budget` in ({DETECTOR}). "
        f"`generation/cascade.py` does not compute it; `expand_cascade` only consumes it."
    )
    got = answer_files(answer, [_hit(CASCADE)], [_hit(CASCADE), _hit(DETECTOR)], [CASCADE], None)
    assert got.citations == [DETECTOR]
    assert got.ruled_out == {CASCADE}
    assert got.drops_top_hit([_hit(CASCADE)], answer) == DETECTOR


def test_named_live_file_resolves_and_invented_path_does_not(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "real.py").write_text("x = 1\n", encoding="utf-8")
    answer = "See pkg/real.py and pkg/invented.py for details."
    got = answer_files(answer, [_hit("pkg/other.py")], [], ["pkg/other.py"], tmp_path)
    assert got.citations == ["pkg/real.py"]


def test_bare_name_resolves_only_to_a_unique_hit() -> None:
    hits = [_hit("a/utils.py"), _hit("b/utils.py"), _hit("c/main.py")]
    got = answer_files("main.py calls utils.py.", hits, [], ["a/utils.py"], None)
    assert got.citations == ["c/main.py"]


def test_file_named_positively_elsewhere_stays_cited() -> None:
    answer = f"{CASCADE} expands the set. The budget is not in {CASCADE}, it is passed in."
    got = answer_files(answer, [_hit(CASCADE)], [], [CASCADE], None)
    assert got.citations == [CASCADE]
    assert not got.ruled_out


def test_no_named_file_falls_back_to_unruled_targets() -> None:
    got = answer_files("It retries three times.", [_hit("a.py")], [], ["a.py", "b.py", "c.py"], None)
    assert got.citations == ["a.py", "b.py"]
    assert not got.named
    assert got.drops_top_hit([_hit("a.py")], "It retries three times.") is None


def test_answer_citing_the_top_hit_keeps_its_grade_inputs() -> None:
    answer = f"See {CASCADE} and the caller in {DETECTOR}."
    got = answer_files(answer, [_hit(CASCADE)], [_hit(DETECTOR)], [CASCADE], None)
    assert got.citations == [CASCADE, DETECTOR]
    assert got.drops_top_hit([_hit(CASCADE)], answer) is None


def test_grade_holds_high_at_medium_when_answer_leaves_the_top_hit() -> None:
    hits = [
        {"target_path": CASCADE, "score": 9.0, "symbols": [{"name": "expand_cascade"}]},
        {"target_path": "x.py", "score": 1.0},
    ]
    kwargs = dict(
        question="where is the cascade budget computed?",
        question_ids=set(),
        answer_text=f"In {DETECTOR}.",
        hits=hits,
        citations=[CASCADE],
        symbol_bodies=[],
        served_named_body=False,
        dominance="ratio",
        uncovered=[],
    )
    assert _grade_answer(**kwargs).confidence == "high"
    grade = _grade_answer(**kwargs, lead_off_top=DETECTOR)
    assert grade.confidence == "medium"
    assert grade.lead_off_top == DETECTOR


def test_a_negated_description_does_not_rule_a_file_out() -> None:
    answer = "The handler in pkg/retry.py has no backoff and does not validate input."
    got = answer_files(answer, [_hit("pkg/retry.py")], [], ["pkg/retry.py"], None)
    assert got.citations == ["pkg/retry.py"]
    assert not got.ruled_out


def test_top_hit_named_by_an_ambiguous_file_name_still_counts_as_named() -> None:
    hits = [_hit("a/utils.py"), _hit("b/utils.py"), _hit("c/main.py")]
    answer = "utils.py holds the parser; c/main.py calls it."
    got = answer_files(answer, hits, [], ["a/utils.py"], None)
    assert got.drops_top_hit(hits, answer) is None


def test_a_path_leaving_the_checkout_is_never_cited(tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    got = answer_files("See ../outside.py.", [_hit("a.py")], [], ["a.py"], root)
    assert got.citations == ["a.py"]
    assert not got.named
