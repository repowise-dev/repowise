"""``answer_basis``: the strongest evidence lane a get_why response rests on."""

from __future__ import annotations

from typing import Any

from repowise.server.mcp_server._budget import (
    OmissionCollector,
    register_post_shed,
)


def _has_archaeology(archaeology: Any) -> bool:
    """Whether an archaeology block found anything.

    ``_git_archaeology_fallback`` always returns a dict, carrying ``triggered``
    and a summary that may say it found nothing, so the block's presence proves
    only that it ran.
    """
    return isinstance(archaeology, dict) and any(
        archaeology.get(lane)
        for lane in ("file_commits", "cross_references", "git_log")
    )


def _is_accepted_row(row: Any) -> bool:
    """Whether an emitted decision row rests on an acceptance.

    Reads the ``authority`` key this module stamps rather than ``status``, for
    the reason ``decision_currencies`` gives: the column is a projection every
    writer keeps in step, and it agrees right up until something writes it
    without an acceptance. A row with no ``authority`` at all is a semantic hit
    projected from the vector store, which carries no record to join on and so
    cannot be claimed as accepted either.
    """
    return isinstance(row, dict) and row.get("authority") == "accepted"


def _stamp_answer_basis(result: dict) -> dict:
    """Name the strongest lane the response actually rests on.

    This tool serves commit messages and mined comments beside decision
    records. Only an *accepted* decision is a ruling; the rest are evidence a
    reader has to weigh. Per-row ``provenance`` answers that one row at a time,
    which is no help in deciding how much of the whole response to trust.

    ``decision`` requires an acceptance, not a record. A ``DecisionRecord`` is a
    candidate until a ``DecisionAcceptance`` row exists for it, and accepting is
    a deliberate manual step, so a store of nothing but candidates is the state
    every user who has not worked through the acceptance UI is in — the common
    case, not an edge one. Stamping ``decision`` off the mere presence of a
    record therefore claimed a ruling on most calls this tool ever serves, and
    it did so beside titles like "Do not ship UI components yet": session
    artifacts nobody confirmed, presented as governing.

    ``candidate`` sits *below* every evidence lane rather than above them for
    the same reason. A mined comment or a commit message is something a reader
    can weigh; an unconfirmed candidate is a guess about what somebody once
    meant, so it must not outrank the lanes that carry real evidence.

    Absent when nothing was served, so a refusal cannot read as an answer.
    Re-derived after the budget pass has shed, so the claim cannot outlive the
    lane it names; clears first to stay correct on that second call.
    """
    result.pop("answer_basis", None)
    entries = [
        e for e in (result.get("target_context") or {}).values() if isinstance(e, dict)
    ]
    decision_rows = [
        *(result.get("decisions") or []),
        *(r for e in entries for r in (e.get("governing_decisions") or [])),
    ]
    candidate_rows = [
        *(r for e in entries for r in (e.get("candidate_decisions") or [])),
        *(r for r in decision_rows if not _is_accepted_row(r)),
    ]
    if any(_is_accepted_row(r) for r in decision_rows):
        result["answer_basis"] = "decision"
    elif result.get("episodes"):
        result["answer_basis"] = "episode"
    elif result.get("code_rationale"):
        result["answer_basis"] = "rationale"
    elif _has_archaeology(result.get("git_archaeology")) or any(
        _has_archaeology(e.get("git_archaeology")) for e in entries
    ):
        result["answer_basis"] = "archaeology"
    elif result.get("related_documentation"):
        result["answer_basis"] = "documentation"
    elif candidate_rows:
        result["answer_basis"] = "candidate"
    return result


def _restamp_answer_basis(result: dict, _collector: OmissionCollector) -> None:
    """Re-derive the basis after shedding.

    The basis names a lane, and a lane the budget pass emptied must not leave
    the claim standing.
    """
    _stamp_answer_basis(result)


register_post_shed("get_why", _restamp_answer_basis)
