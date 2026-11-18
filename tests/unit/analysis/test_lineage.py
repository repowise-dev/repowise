"""Function lineage: which new function continues which old one, and why one did not."""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.core.analysis.lineage import (
    FunctionBody,
    mint_lineage,
    pair_lineage,
    similar_by_source,
)

_LONG = 30


def _old(symbol: str, digest: str, tokens: int = _LONG, lineage: str | None = None) -> FunctionBody:
    return FunctionBody(symbol, digest, tokens, lineage or f"L-{symbol}")


def _new(symbol: str, digest: str, tokens: int = _LONG) -> FunctionBody:
    return FunctionBody(symbol, digest, tokens)


def _methods(plan) -> dict[tuple[str, str | None], tuple[str, str | None]]:
    return {(e.from_symbol_id, e.to_symbol_id): (e.method, e.reason) for e in plan.events}


def test_a_kept_id_keeps_its_lineage_and_a_new_one_is_minted() -> None:
    plan = pair_lineage(
        [_old("a.py::f", "d1"), FunctionBody("a.py::g", "d2", _LONG, None)],
        [_new("a.py::f", "d9"), _new("a.py::g", "d2"), _new("a.py::h", "d3")],
        commit="c1",
    )
    assert plan.lineage == {
        "a.py::f": "L-a.py::f",
        # A row stored before lineage existed gets one from its id.
        "a.py::g": mint_lineage("a.py::g", None),
        "a.py::h": mint_lineage("a.py::h", "c1"),
    }
    assert plan.events == []
    assert len(plan.lineage["a.py::h"]) == 16


def test_a_file_rename_carries_every_function_in_it() -> None:
    plan = pair_lineage(
        [_old("old.py::C::f", "d1")], [_new("new.py::C::f", "d2")], renames={"old.py": "new.py"}
    )
    assert plan.lineage["new.py::C::f"] == "L-old.py::C::f"
    assert _methods(plan) == {("old.py::C::f", "new.py::C::f"): ("file_rename", None)}


def test_a_file_renamed_onto_an_existing_one_takes_no_lineage_from_it() -> None:
    plan = pair_lineage(
        [_old("a.py::f", "d1"), _old("b.py::f", "d2")],
        [_new("b.py::f", "d3")],
        renames={"a.py": "b.py"},
    )
    assert plan.lineage == {"b.py::f": "L-b.py::f"}


def test_the_three_passes() -> None:
    plan = pair_lineage(
        [
            _old("a.py::moved", "d1"),
            _old("a.py::before", "d2"),
            _old("a.py::edited", "d3"),
        ],
        [
            _new("b.py::moved", "d1"),
            _new("a.py::after", "d2"),
            _new("c.py::edited", "d4"),
        ],
        similar=lambda old, new: (old, new) == ("a.py::edited", "c.py::edited"),
    )
    assert plan.lineage == {
        "b.py::moved": "L-a.py::moved",
        "a.py::after": "L-a.py::before",
        "c.py::edited": "L-a.py::edited",
    }
    assert _methods(plan) == {
        ("a.py::moved", "b.py::moved"): ("verbatim_move", None),
        ("a.py::before", "a.py::after"): ("body_rename", None),
        ("a.py::edited", "c.py::edited"): ("edited_move", None),
    }


def test_an_undecidable_candidate_breaks_the_lineage_with_its_reason() -> None:
    plan = pair_lineage(
        [
            # Two removed bodies alike: neither can claim the added one.
            _old("a.py::x1", "dup"),
            _old("a.py::x2", "dup"),
            # A short body under a new name matches by chance.
            _old("a.py::tiny", "short", tokens=5),
            # Same name in another file, but the body changed beyond the gate.
            _old("a.py::rewritten", "r1"),
            # Gone with nothing like it: no event.
            _old("a.py::deleted", "gone"),
        ],
        [
            _new("b.py::y", "dup"),
            _new("a.py::small", "short", tokens=5),
            _new("b.py::rewritten", "r2"),
        ],
        similar=lambda old, new: False,
        commit="c2",
    )
    assert _methods(plan) == {
        ("a.py::x1", None): ("broken", "ambiguous_body"),
        ("a.py::x2", None): ("broken", "ambiguous_body"),
        ("a.py::tiny", None): ("broken", "short_body"),
        ("a.py::rewritten", None): ("broken", "edited_body"),
    }
    assert plan.lineage["b.py::y"] == mint_lineage("b.py::y", "c2")
    assert {e.lineage_id for e in plan.events if e.from_symbol_id == "a.py::x1"} == {"L-a.py::x1"}


def test_an_edited_move_without_a_way_to_compare_is_never_taken() -> None:
    plan = pair_lineage([_old("a.py::f", "d1")], [_new("b.py::f", "d2")])
    assert plan.lineage["b.py::f"] != "L-a.py::f"
    assert _methods(plan) == {("a.py::f", None): ("broken", "unverified_edit")}


def test_two_same_named_candidates_are_ambiguous() -> None:
    plan = pair_lineage(
        [_old("a.py::run", "d1")],
        [_new("b.py::run", "d2"), _new("c.py::run", "d3")],
        similar=lambda old, new: True,
    )
    assert _methods(plan) == {("a.py::run", None): ("broken", "ambiguous_name")}


_BODY = """def {name}(rows, limit):
    total = 0
    for row in rows:
        if row.size > limit:
            total += row.size * 2
        else:
            total -= 1
    return total
"""


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_bodies_are_compared_at_the_base_commit_and_on_disk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text(_BODY.format(name="tally"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").unlink()
    (repo / "b.py").write_text(_BODY.format(name="tally").replace("* 2", "* 3"))
    (repo / "c.py").write_text("def tally():\n    return {'a': 1, 'b': 2}\n")

    similar = similar_by_source(repo, base)
    assert similar is not None
    assert similar("a.py::tally", "b.py::tally") is True
    assert similar("a.py::tally", "c.py::tally") is False
    # A symbol neither side has is no evidence of anything.
    assert similar("a.py::missing", "b.py::tally") is False
    assert similar_by_source(repo, None) is None
