"""One external projection for every fresh and cached ``get_answer`` reply."""

from __future__ import annotations

import copy
from collections.abc import Callable
from functools import wraps
from typing import Any

_COLLECTIONS = (
    "citations",
    "retrieval",
    "quotes",
    "symbol_bodies",
    "best_guesses",
    "code_rationale",
    "candidates",
    "fallback_targets",
)


def _path(row: Any) -> str | None:
    if isinstance(row, str):
        return row
    if not isinstance(row, dict):
        return None
    return row.get("path") or row.get("file") or row.get("target_path")


def _nav_path(row: Any) -> str | None:
    """Comparable file path for navigation rows and symbol-qualified evidence."""
    path = row["file"] if isinstance(row, dict) and row.get("file") else _path(row)
    return path.split("::", 1)[0] if isinstance(path, str) else None


def _text(row: Any, *keys: str) -> str:
    if not isinstance(row, dict):
        return ""
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unique(rows: list[Any], identity: Callable[[Any], Any]) -> list[Any]:
    seen: set[Any] = set()
    emitted: list[Any] = []
    for row in rows:
        marker = identity(row)
        if marker in seen:
            continue
        seen.add(marker)
        emitted.append(row)
    return emitted


def _contained(text: str, slabs: list[str]) -> bool:
    compact = " ".join(text.split())
    return bool(compact) and any(compact in " ".join(slab.split()) for slab in slabs)


def _deduplicate(payload: dict[str, Any]) -> None:
    """Keep each source fragment once, then keep paths only where they add navigation."""
    bodies = _unique(
        list(payload.get("symbol_bodies") or []),
        lambda row: (_path(row), row.get("name"), tuple(row.get("lines") or ())),
    )
    payload["symbol_bodies"] = bodies
    body_text = [_text(row, "source") for row in bodies]

    quotes = _unique(
        [
            row
            for row in (payload.get("quotes") or [])
            if not _contained(_text(row, "quote"), body_text)
        ],
        lambda row: (_path(row), tuple(row.get("lines") or ()), _text(row, "quote")),
    )
    payload["quotes"] = quotes
    quote_text = [_text(row, "quote") for row in quotes]

    rationale = _unique(
        [
            row
            for row in (payload.get("code_rationale") or [])
            if not _contained(
                _text(row, "rationale", "comment", "quote", "source", "text"),
                [*body_text, *quote_text],
            )
        ],
        lambda row: (
            _path(row),
            tuple(row.get("lines") or ()),
            _text(row, "rationale", "comment", "quote", "source", "text"),
        ),
    )
    payload["code_rationale"] = rationale
    rationale_text = [
        _text(row, "rationale", "comment", "quote", "source", "text") for row in rationale
    ]

    guesses: list[dict[str, Any]] = []
    for original in _unique(
        list(payload.get("best_guesses") or []),
        lambda row: (
            _path(row),
            _text(row, "excerpt"),
            _text(row, "why_relevant", "reason"),
        ),
    ):
        row = dict(original) if isinstance(original, dict) else original
        if isinstance(row, dict) and _contained(
            _text(row, "excerpt"), [*body_text, *quote_text, *rationale_text]
        ):
            row.pop("excerpt", None)
        guesses.append(row)
    payload["best_guesses"] = guesses
    guess_text = [_text(row, "excerpt") for row in guesses]

    retrieval: list[dict[str, Any]] = []
    for original in _unique(
        list(payload.get("retrieval") or []),
        lambda row: (
            _path(row),
            row.get("page_id") if isinstance(row, dict) else None,
            _text(row, "excerpt", "snippet"),
            _text(row, "summary"),
        ),
    ):
        row = dict(original) if isinstance(original, dict) else original
        if isinstance(row, dict) and _contained(
            _text(row, "excerpt", "snippet"),
            [*body_text, *quote_text, *rationale_text, *guess_text],
        ):
            row.pop("excerpt", None)
            row.pop("snippet", None)
        retrieval.append(row)
    payload["retrieval"] = retrieval

    citations = _unique(list(payload.get("citations") or []), lambda row: row)
    payload["citations"] = citations
    occupied = {path for path in map(_nav_path, citations) if path}
    occupied.update(
        path
        for rows in (bodies, quotes, rationale, guesses, retrieval)
        for path in map(_nav_path, rows)
        if path
    )

    fallbacks = [
        row
        for row in _unique(list(payload.get("fallback_targets") or []), lambda row: row)
        if _nav_path(row) not in occupied
    ]
    payload["fallback_targets"] = fallbacks

    ranked_paths = {path for path in map(_nav_path, fallbacks) if path}
    candidates = [
        row
        for row in _unique(list(payload.get("candidates") or []), lambda row: _path(row))
        if _nav_path(row) not in occupied and _nav_path(row) not in ranked_paths
    ]
    payload["candidates"] = candidates


def _rewrite_degraded_answer(payload: dict[str, Any]) -> None:
    """Describe only evidence that survived the external projection."""
    reason = payload.get("degraded")
    if not reason:
        return
    if payload.get("symbol_bodies"):
        payload["answer"] = (
            f"Synthesis is unavailable ({reason}), but symbol_bodies contains live source "
            "for the named code. Use that evidence directly."
        )
    elif payload.get("code_rationale"):
        row = payload["code_rationale"][0]
        conclusion = _text(row, "rationale", "comment", "quote", "source", "text")
        path = _path(row) or "the top source match"
        payload["answer"] = (
            f"Synthesis is unavailable ({reason}). Source rationale in {path}: "
            f"{conclusion[:400]}"
        )
    elif payload.get("best_guesses"):
        first = _path(payload["best_guesses"][0])
        payload["answer"] = (
            f"Synthesis is unavailable ({reason}). Local retrieval points first to "
            f"{first}; best_guesses carries the evidence and ranking reason."
        )
    elif payload.get("retrieval"):
        first = _path(payload["retrieval"][0])
        payload["answer"] = (
            f"Synthesis is unavailable ({reason}). Local retrieval points first to "
            f"{first}; inspect the emitted evidence before answering."
        )
    else:
        payload["answer"] = (
            f"Synthesis is unavailable ({reason}), and no local evidence matched. "
            "Refine the question with a symbol or path."
        )


def _keep(payload: dict[str, Any], key: str, limit: int | None) -> None:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return
    if limit is None:
        return
    payload[key] = rows[:limit]


def _default_shape(payload: dict[str, Any], question: str) -> None:
    confidence = payload.get("confidence", "low")
    why = question.lstrip().lower().startswith("why")
    if confidence == "high":
        for key in ("retrieval", "best_guesses", "candidates", "fallback_targets"):
            payload.pop(key, None)
        if not payload.get("grounding"):
            _keep(payload, "symbol_bodies", 1)
        _keep(payload, "quotes", 1)
        if payload.get("quotes") or not why:
            payload.pop("code_rationale", None)
        else:
            _keep(payload, "code_rationale", 1)
        payload.setdefault("next_action_hint", "Use the answer and citations directly.")
        return

    if confidence == "medium":
        _keep(payload, "symbol_bodies", 1)
        _keep(payload, "quotes", 2)
        _keep(payload, "code_rationale", 2)
        if payload.get("best_guesses"):
            payload.pop("retrieval", None)
            _keep(payload, "best_guesses", 2)
        else:
            _keep(payload, "retrieval", 2)
        payload.pop("candidates", None)
        if payload.get("best_guesses") or payload.get("retrieval"):
            payload.pop("fallback_targets", None)
        payload.setdefault(
            "next_action_hint",
            "Verify the top evidence row before relying on details the answer does not settle.",
        )
        return

    _keep(payload, "symbol_bodies", 2)
    _keep(payload, "code_rationale", 2)
    _keep(payload, "quotes", 1)
    if payload.get("best_guesses"):
        payload.pop("retrieval", None)
        _keep(payload, "best_guesses", 3)
    else:
        _keep(payload, "retrieval", 3)
    payload.pop("candidates", None)
    if payload.get("best_guesses") or payload.get("retrieval") or payload.get("symbol_bodies"):
        payload.pop("fallback_targets", None)
    if not str(payload.get("answer") or "").strip():
        payload["answer"] = str(payload.get("note") or "No grounded answer was found.")
    payload.setdefault(
        "next_action_hint",
        "Use the first emitted evidence row; refine the question if it does not resolve the issue.",
    )


def _record_reductions(
    payload: dict[str, Any], totals: dict[str, int], *, question: str, scope: str | None,
    repo: str | None, expanded: bool
) -> None:
    reduced = False
    for key in _COLLECTIONS:
        total = totals.get(key, 0)
        emitted = len(payload.get(key) or []) if isinstance(payload.get(key), list) else 0
        if total <= emitted:
            continue
        reason = "deduplicated" if expanded else "confidence_projection_and_deduplication"
        payload[f"{key}_total"] = total
        payload[f"{key}_emitted"] = emitted
        payload[f"{key}_reduced_reason"] = reason
        reduced = True
    if reduced and not expanded:
        projection = payload.setdefault("_meta", {}).setdefault("projection", {})
        arguments: dict[str, Any] = {"question": question, "include": ["evidence"]}
        if scope is not None:
            arguments["scope"] = scope
        if repo is not None:
            arguments["repo"] = repo
        projection["recovery"] = {"tool": "get_answer", "arguments": arguments}


def project_answer_payload(
    raw: dict[str, Any], *, question: str, scope: str | None = None,
    repo: str | None = None, include: list[str] | None = None
) -> dict[str, Any]:
    """Return the cache-independent, confidence-specific external response."""
    payload = copy.deepcopy(raw)
    totals = {
        key: len(payload.get(key) or []) if isinstance(payload.get(key), list) else 0
        for key in _COLLECTIONS
    }
    _deduplicate(payload)
    expanded = "evidence" in set(include or [])
    if not expanded:
        _default_shape(payload, question)
    _rewrite_degraded_answer(payload)
    for key in _COLLECTIONS:
        if not payload.get(key):
            payload.pop(key, None)
    _record_reductions(
        payload, totals, question=question, scope=scope, repo=repo, expanded=expanded
    )
    unknown = sorted(set(include or []) - {"evidence"})
    if unknown:
        payload.setdefault("_meta", {})["ignored_arguments"] = {"include": unknown}
    return payload


def _served_paths(payload: dict[str, Any]) -> list[str]:
    paths = [path for path in payload.get("citations") or [] if isinstance(path, str)]
    for key in _COLLECTIONS:
        if key == "citations":
            continue
        paths.extend(path for path in map(_path, payload.get(key) or []) if path)
    return list(dict.fromkeys(paths))


async def _refresh_freshness(payload: dict[str, Any], repo: str | None) -> None:
    """Scope trust metadata to evidence in the final fresh/cache projection."""
    if repo == "all":
        return
    try:
        from repowise.core.persistence.database import get_session
        from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
        from repowise.server.mcp_server._meta import freshness_from_repo

        ctx = await _resolve_repo_context(repo)
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
        freshness = freshness_from_repo(repository, targets=_served_paths(payload))
    except Exception:
        return
    meta = payload.setdefault("_meta", {})
    for key in (
        "index_age_days",
        "indexed_commit",
        "live_head",
        "index_behind",
        "stale_warning",
    ):
        meta.pop(key, None)
    meta.update(freshness)


def projected_answer(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Project the completed raw result once, regardless of cache or early return."""

    @wraps(fn)
    async def _wrapped(
        question: str,
        scope: str | None = None,
        repo: str | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        raw = await fn(question=question, scope=scope, repo=repo, include=include)
        payload = project_answer_payload(
            raw, question=question, scope=scope, repo=repo, include=include
        )
        await _refresh_freshness(payload, repo)
        return payload

    return _wrapped


__all__ = ["project_answer_payload", "projected_answer"]
