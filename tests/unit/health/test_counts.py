"""Reading a health figure with or without its change-history half."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.health.counts import (
    DEFAULT_COUNTS,
    code_shape_score,
    parse_counts,
    project,
)
from repowise.core.analysis.health.scoring import SCORE_FLOOR, SCORE_MAX


@dataclass
class _M:
    file_path: str
    score: float
    structure_deduction: float | None
    history_deduction: float | None
    nloc: int = 100
    defect_score: float | None = None
    is_test: bool = False


def test_unknown_values_fall_back_rather_than_raise():
    assert parse_counts("nonsense") == DEFAULT_COUNTS
    assert parse_counts(None) == DEFAULT_COUNTS
    assert parse_counts("code_shape") == "code_shape"


def test_the_default_reading_is_the_rows_themselves():
    """No projection means no copies, so nothing downstream can diverge."""
    rows = [_M("a.py", 7.0, 1.6, 1.4)]
    out, unmeasured = project("everything", rows)
    assert out is rows
    assert unmeasured == 0


def test_code_shape_subtracts_only_the_structure_half():
    rows = [_M("a.py", 7.0, 1.6, 1.4)]
    out, _ = project("code_shape", rows)
    assert out[0].score == 8.4
    # The wire mirror has to move with it, or one surface reports the other's
    # number under this one's label.
    assert out[0].defect_score == 8.4


def test_a_file_pinned_at_the_floor_can_show_its_code_shape():
    """The founding case for the split: 1.0 hides whether the code is bad."""
    rows = [_M("deep.py", SCORE_FLOOR, 2.0, 9.5)]
    out, _ = project("code_shape", rows)
    assert out[0].score == 8.0


def test_the_projection_is_clamped_like_any_score():
    assert code_shape_score(12.9) == SCORE_FLOOR
    assert code_shape_score(0.0) == SCORE_MAX


def test_rows_predating_the_split_are_dropped_not_scored_ten():
    """Coercing a missing half to zero prints a confident 10.0 for a guess."""
    rows = [_M("old.py", 9.0, None, None), _M("new.py", 7.0, 1.6, 1.4)]
    out, unmeasured = project("code_shape", rows)
    assert [m.file_path for m in out] == ["new.py"]
    assert unmeasured == 1
    assert code_shape_score(None) is None


def test_the_history_half_reads_as_zero_so_derived_figures_agree():
    """A row shipping a projected score beside a full-reading unclamped one
    contradicts itself on the wire, and the summary's history average would
    describe the reading the page is not showing."""
    rows = [_M("a.py", 7.0, 1.6, 1.4)]
    out, _ = project("code_shape", rows)
    assert out[0].history_deduction == 0.0
    assert out[0].structure_deduction == 1.6
    # 10 - structure - history now matches the score the same row carries.
    assert SCORE_MAX - out[0].structure_deduction - out[0].history_deduction == out[0].score


def test_everything_else_reads_through_to_the_row():
    """The projection re-scores; it must not hide the rest of the record."""
    rows = [_M("a.py", 7.0, 1.6, 1.4, nloc=250, is_test=True)]
    out, _ = project("code_shape", rows)
    assert out[0].nloc == 250
    assert out[0].file_path == "a.py"
    assert out[0].is_test is True


def test_the_projection_never_writes_back_to_the_row():
    """The rows a route holds are live ORM objects; a mutation would flush."""
    row = _M("a.py", 7.0, 1.6, 1.4)
    out, _ = project("code_shape", [row])
    assert out[0].score == 8.4
    assert row.score == 7.0
