"""A repeated refusal preserves the original range, pointers and repair reasons."""

from repowise.cli.commands.update_cmd.persistence import save_prune_hold
from repowise.cli.helpers import load_state
from repowise.core.pipeline.incremental.prune import PruneHoldError, clear_prune_refusal


def test_recording_and_resolving_prune_debt(tmp_path):
    state = {
        "last_sync_commit": "old-head",
        "pending_repair": {"from_commit": "earlier-head", "steps": ["Git persist"]},
    }
    save_prune_hold(tmp_path, state, PruneHoldError(["Symbol persist"]))
    save_prune_hold(tmp_path, load_state(tmp_path), PruneHoldError(["Graph nodes persist"]))
    state = load_state(tmp_path)
    assert state["last_sync_commit"] == state["prune_refused_at"] == "old-head"
    assert "last_docs_commit" not in state
    assert state["pending_repair"] == {
        "from_commit": "earlier-head",
        "steps": ["Deleted-file prune (refused)", "Git persist", "Graph nodes persist", "Symbol persist"],
    }
    clear_prune_refusal(state)
    assert "prune_refused_at" not in state
    assert state["pending_repair"] == {
        "from_commit": "earlier-head",
        "steps": ["Git persist", "Graph nodes persist", "Symbol persist"],
    }
