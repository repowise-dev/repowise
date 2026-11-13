"""A refusal hold must end on its own (#5410 review).

The prune judges liveness, not the commit range, so a genuine bulk deletion
refuses identically on every run. Holding the pointer forever would be worse
than the stale paths the hold was added to fix: nothing would ever advance, and
only a full reindex would break the deadlock.

The rule is one repeat: the first refusal re-covers the range, and a second one
at the same unchanged pointer means the deletion is real, so it is let through.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd.persistence import _prune_refusal_outcome


def test_first_refusal_holds_and_records_the_pointer():
    new_state: dict = {"last_sync_commit": "new-head"}
    state: dict = {"last_sync_commit": "old-head"}

    hold = _prune_refusal_outcome(new_state, state, ["1,597 of 3,013 paths looked deleted"])

    assert hold is True, "the first refusal must re-cover the range"
    assert new_state["last_sync_commit"] == "old-head"
    assert new_state["prune_refused_at"] == "old-head"
    assert new_state["pending_repair"]["steps"], "the range has to stay recoverable"


def test_second_refusal_at_the_same_pointer_lets_it_through():
    # First run refused and held the pointer at "old-head".
    new_state: dict = {"last_sync_commit": "new-head"}
    state: dict = {"last_sync_commit": "old-head", "prune_refused_at": "old-head"}

    hold = _prune_refusal_outcome(new_state, state, ["1,597 of 3,013 paths looked deleted"])

    assert hold is False, "refusing twice at the same commit is an answer, not a stall"
    assert new_state["last_sync_commit"] == "new-head", "the pointer must advance"
    assert "prune_refused_at" not in new_state, "the hold must be cleared, not re-armed"


def test_a_refusal_after_progress_is_a_new_first_refusal():
    # The pointer has moved since the recorded refusal: this is a different
    # change set, so it gets the same one-run grace rather than being accepted.
    new_state: dict = {"last_sync_commit": "third-head"}
    state: dict = {"last_sync_commit": "second-head", "prune_refused_at": "first-head"}

    hold = _prune_refusal_outcome(new_state, state, ["refused"])

    assert hold is True
    assert new_state["last_sync_commit"] == "second-head"
    assert new_state["prune_refused_at"] == "second-head"


@pytest.mark.parametrize("noise", [[], ["some other step"]])
def test_no_refusal_leaves_the_pointer_alone(noise):
    new_state: dict = {"last_sync_commit": "new-head"}
    state: dict = {"last_sync_commit": "old-head", "prune_refused_at": "old-head"}

    hold = _prune_refusal_outcome(new_state, state, [])

    assert hold is False
    assert new_state["last_sync_commit"] == "new-head"
