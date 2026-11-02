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

    Reads the stamped ``authority``, not ``status`` (see
    ``decision_currencies``). A row with no ``authority`` is a semantic hit with
    no record to join on, so it cannot count as accepted.
    """
    return isinstance(row, dict) and row.get("authority") == "accepted"


def _decision_rows(result: dict) -> list:
    """Every decision row served: the top-level lane and each card's rules."""
    return [
        *(result.get("decisions") or []),
        *(
            r
            for e in (result.get("target_context") or {}).values()
            if isinstance(e, dict)
            for r in (e.get("governing_decisions") or [])
        ),
    ]


def _answers(row: Any, mode: Any) -> bool:
    """Whether an accepted *row* answers the call, not just governs a named file.

    A question is answered only by a row that cleared its relevance floor, and
    those are the rows served with their ``decision`` text: a semantic hit
    carries a snippet, a card's off-question rule only its title. Without a
    question (path mode) the path is the question, so every rule answers it.
    """
    return _is_accepted_row(row) and (mode != "search" or "decision" in row)


def rests_on_decision(result: dict) -> bool:
    """Whether *result* serves an accepted decision that answers it.

    The one fact behind ``answer_basis: "decision"`` and behind any prose that
    says no record covers the call, so the two cannot disagree.
    """
    mode = result.get("mode")
    return any(_answers(r, mode) for r in _decision_rows(result))


def _stamp_answer_basis(result: dict) -> dict:
    """Name the strongest lane the response actually rests on.

    Only an accepted decision is a ruling; everything else is evidence to weigh.

    ``decision`` requires a ``DecisionAcceptance``, not just a record: most
    stores hold only unconfirmed candidates. ``candidate`` ranks below every
    evidence lane, since an unconfirmed guess must not outrank real evidence.

    Absent when nothing was served, so a refusal cannot read as an answer.
    Re-derived after the budget pass sheds, so it clears first.
    """
    result.pop("answer_basis", None)
    entries = [
        e for e in (result.get("target_context") or {}).values() if isinstance(e, dict)
    ]
    candidate_rows = [
        *(r for e in entries for r in (e.get("candidate_decisions") or [])),
        *(r for r in result.get("decisions") or [] if not _is_accepted_row(r)),
    ]
    if rests_on_decision(result):
        result["answer_basis"] = "decision"
    elif result.get("episodes"):
        result["answer_basis"] = "episode"
    elif result.get("code_rationale"):
        result["answer_basis"] = "rationale"
    elif (
        _has_archaeology(result.get("git_archaeology"))
        or result.get("key_commits")
        or any(_has_archaeology(e.get("git_archaeology")) for e in entries)
    ):
        result["answer_basis"] = "archaeology"
    elif result.get("related_documentation"):
        result["answer_basis"] = "documentation"
    elif candidate_rows:
        result["answer_basis"] = "candidate"
    return result


def _restamp_answer_basis(result: dict, _collector: OmissionCollector) -> None:
    """Re-derive the basis after shedding, so it never names an emptied lane."""
    _stamp_answer_basis(result)


register_post_shed("get_why", _restamp_answer_basis)
