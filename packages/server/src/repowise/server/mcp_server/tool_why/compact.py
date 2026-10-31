"""The MCP shape of a question that no decision record answers.

Such a response used to carry each target's whole card: contributors, recent
commits, counts of suppressed lanes and the call that restores them. Agents act
on the top rationale comment or the commit that names the change, and almost
never on the rest, so the delivered form keeps those and one call for more.

A post-enforce hook, so only the MCP surface is compacted: ``repowise why``
calls the tool directly and keeps its ``--full`` view.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from repowise.server.mcp_server._budget import register_post_enforce
from repowise.server.mcp_server._why_relevance import (
    question_terms,
    relevance,
    term_idf,
    word_set,
)
from repowise.server.mcp_server.tool_why.basis import _stamp_answer_basis

#: Rationale comments kept, in the order the tool ranked them.
RATIONALE_KEPT = 3
#: Commits kept: those the question singled out, then the best scored ones.
COMMITS_KEPT = 2

_COMMIT_LANES = ("file_commits", "cross_references", "git_log")
#: Fields that only restate the call, or that no agent acts on.
_DROPPED = (
    "target_context",
    "git_archaeology",
    "try_instead",
    "code_rationale_total",
    "code_rationale_emitted",
    "code_rationale_truncated",
    "code_rationale_reduced_reason",
    "code_rationale_omitted",
)

_REASON = (
    "No decision record covers this question. The rationale comments and commits "
    "below are evidence from the code and its history, not a ruling."
)


def compact_unanswered(result: dict[str, Any], call: Mapping[str, Any]) -> None:
    """Reduce a search with no governing decision to its verdict and best evidence."""
    result.pop("try_instead", None)
    if result.get("mode") != "search":
        return
    targets = list(call.get("targets") or []) or (
        [result["anchored_on"]] if result.get("anchored_on") else []
    )
    if _has_decision_rows(result):
        # One recall is shorter than the line that would replace it.
        if targets and _count_recalls(result) > 1:
            _strip_recalls(result)
            result["more"] = _more(targets)
        return
    query = str(result.get("query") or call.get("query") or "")
    rationale = [_rationale_row(r) for r in (result.get("code_rationale") or [])[:RATIONALE_KEPT]]
    commits = _key_commits(result, query, fill=bool(rationale))
    for key in _DROPPED:
        result.pop(key, None)
    result.pop("code_rationale", None)
    result.pop("key_commits", None)
    if rationale:
        result["code_rationale"] = rationale
    if commits:
        result["key_commits"] = commits
    if rationale or commits:
        result["reason"] = _REASON
    paths = targets or [r["path"] for r in rationale if isinstance(r, dict) and r.get("path")][:1]
    if paths and "next_call" not in result:
        result["more"] = _more(paths)
    _stamp_answer_basis(result)
    if "_meta" in result:
        result["_meta"] = result.pop("_meta")  # Evidence first.


def _more(paths: list[str]) -> str:
    return f"get_why(targets={json.dumps(paths)}) for the full file history"


def _children(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.values())
    return list(value) if isinstance(value, list) else []


def _count_recalls(value: Any) -> int:
    own = int(isinstance(value, dict) and "recover_with" in value)
    return own + sum(_count_recalls(child) for child in _children(value))


def _strip_recalls(value: Any) -> None:
    """Drop every per-lane ``recover_with`` call; the one ``more`` line replaces them."""
    if isinstance(value, dict):
        value.pop("recover_with", None)
    for child in _children(value):
        _strip_recalls(child)


def _has_decision_rows(result: dict[str, Any]) -> bool:
    """Any decision row served, at top level or on a target card.

    A card's rules bind the file whatever the question, and a candidate that
    cleared the question's floor may be the answer: both keep the full form.
    """
    if result.get("decisions"):
        return True
    return any(
        isinstance(card, dict)
        and (card.get("governing_decisions") or card.get("candidate_decisions"))
        for card in (result.get("target_context") or {}).values()
    )


def _rationale_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    return {k: row[k] for k in ("path", "lines", "comment") if k in row}


def _key_commits(result: dict[str, Any], query: str, *, fill: bool) -> list[dict[str, Any]]:
    """The commits the question singled out, then the file's key commits it scores best.

    The archaeology lanes reaching here were already focused on the question,
    so they lead. A card's origin key commits are the file's most significant,
    whatever the question. They serve only when their messages carry its terms,
    and only beside evidence the question found (*fill*, or a focused commit):
    they never turn a refusal into an answer.
    """
    focused, origin = _commit_pools(result)
    if not (fill or focused):
        origin = []
    rows = _unique([*focused, *origin])
    focused_keys = {_sha_key(r) for r in focused}
    idf = term_idf(question_terms(query, prose=True), [_words(r) for r in rows])
    # Focused rows keep their order, so a second pass over the result is a no-op.
    kept = [r for r in rows if _sha_key(r) in focused_keys]
    scored = sorted(
        ((-relevance(_words(r), idf), i, r) for i, r in enumerate(rows) if r not in kept),
        key=lambda t: t[:2],
    )
    kept += [r for score, _, r in scored if score < 0]
    return [_commit_row(r) for r in kept[:COMMITS_KEPT]]


def _commit_pools(result: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    focused: list[dict] = list(result.get("key_commits") or [])
    origin: list[dict] = []
    blocks = [result.get("git_archaeology")]
    for card in (result.get("target_context") or {}).values():
        if not isinstance(card, dict):
            continue
        blocks.append(card.get("git_archaeology"))
        origin.extend((card.get("origin") or {}).get("key_commits") or [])
    for block in blocks:
        if isinstance(block, dict):
            for lane in _COMMIT_LANES:
                focused.extend(block.get(lane) or [])
    return [r for r in focused if isinstance(r, dict)], [r for r in origin if isinstance(r, dict)]


def _sha_key(row: dict) -> str:
    return str(row.get("sha") or row.get("commit") or "")[:8].lower()


def _unique(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in rows:
        key = _sha_key(r) or f"row:{id(r)}"
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _words(row: dict) -> frozenset[str]:
    return word_set(str(row.get("message") or ""))


def _commit_row(row: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"sha": str(row.get("sha") or row.get("commit") or "")[:12]}
    if row.get("date"):
        out["date"] = str(row["date"])[:10]
    out["message"] = row.get("message")
    if row.get("pr_number"):
        out["pr_number"] = row["pr_number"]
    return out


register_post_enforce("get_why", compact_unanswered, with_call=True)
