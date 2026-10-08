"""One external projection for every fresh and cached ``get_answer`` reply."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from repowise.server.mcp_server.tool_answer.config import (
    _CANDIDATE_FILES_HIGH,
    _CANDIDATE_FILES_MAX,
    _LARGE_FILE_BYTES,
)

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


_SYMBOL_TEXT_KEYS = ("source_excerpt", "docstring")


def _body_spans(bodies: list[Any]) -> list[tuple[str, int, int]]:
    """The file and line range of every body this response already serves."""
    spans: list[tuple[str, int, int]] = []
    for row in bodies:
        if not isinstance(row, dict):
            continue
        path = _nav_path(row)
        lines = row.get("lines")
        if not path or not isinstance(lines, list | tuple) or len(lines) != 2:
            continue
        start, end = lines
        if isinstance(start, int) and isinstance(end, int):
            spans.append((path, start, end))
    return spans


def _inside_served_body(
    entry: dict[str, Any], path: str | None, spans: list[tuple[str, int, int]]
) -> bool:
    """True when this symbol's lines sit inside a body served for the same file."""
    start = entry.get("start_line")
    end = entry.get("end_line")
    if not path or not isinstance(start, int) or not isinstance(end, int):
        return False
    return any(
        span_path == path and span_start <= start and end <= span_end
        for span_path, span_start, span_end in spans
    )


def _trim_key_symbols(
    row: dict[str, Any], spans: list[tuple[str, int, int]], served_text: list[str]
) -> None:
    """Drop a key symbol's source and docstring when those bytes are served elsewhere.

    The symbol still names and locates itself, so nothing is lost for navigation.
    """
    symbols = row.get("key_symbols")
    if not isinstance(symbols, list):
        return
    row_path = _nav_path(row)
    trimmed: list[Any] = []
    for original in symbols:
        if not isinstance(original, dict):
            trimmed.append(original)
            continue
        entry = dict(original)
        inside = _inside_served_body(entry, _nav_path(entry) or row_path, spans)
        for key in _SYMBOL_TEXT_KEYS:
            text = _text(entry, key)
            if text and (inside or _contained(text, served_text)):
                entry.pop(key, None)
        trimmed.append(entry)
    row["key_symbols"] = trimmed


def _deduplicate(payload: dict[str, Any]) -> None:
    """Keep each source fragment once, then keep paths only where they add navigation."""
    bodies = _unique(
        list(payload.get("symbol_bodies") or []),
        lambda row: (_path(row), row.get("name"), tuple(row.get("lines") or ())),
    )
    payload["symbol_bodies"] = bodies
    body_text = [_text(row, "source") for row in bodies]
    body_spans = _body_spans(bodies)

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
        if isinstance(row, dict):
            _trim_key_symbols(row, body_spans, [*body_text, *quote_text])
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


def _shape_confidence(payload: dict[str, Any]) -> Any:
    # A degraded payload keeps the fullest shape whatever it graded: trimming is
    # keyed on prose replacing evidence, and there the evidence IS the product.
    return "low" if payload.get("degraded") else payload.get("confidence", "low")


def _shape_candidate_files(payload: dict[str, Any], *, expanded: bool) -> None:
    """Serve the ranked paths the final citations do not already name."""
    rows = payload.pop("candidate_files", None)
    if not isinstance(rows, list):
        return
    cited = {path for path in map(_nav_path, payload.get("citations") or []) if path}
    paths = [
        path for path in dict.fromkeys(row for row in rows if isinstance(row, str))
        if path not in cited
    ]
    high = not expanded and _shape_confidence(payload) == "high"
    paths = paths[: _CANDIDATE_FILES_HIGH if high else _CANDIDATE_FILES_MAX]
    if paths:
        payload["candidate_files"] = paths


# Only the legacy abstain reply points at excerpts; once slimmed there are none.
_EXCERPT_NOTE = re.compile(r", and its excerpt carries that page's actual content(?=\.)")


def _first_guess(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The first ``best_guesses`` row naming a file: what both hints point at."""
    return next(
        (
            row for row in payload.get("best_guesses") or []
            if isinstance(row, dict) and _nav_path(row)
        ),
        None,
    )


def _slim_best_guesses(payload: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Swap each low-confidence guess's page excerpt for compact facts about its file.

    Rows, keys and order stay; ``excerpt`` returns with ``include=["evidence"]``.
    The row's own ``why_relevant`` and ``score`` win: they come from the same hit.
    Returns whether there were rows to slim, so sizes are stamped on those only.
    """
    guesses = payload.get("best_guesses")
    if not isinstance(guesses, list) or not guesses:
        return False
    dropped = False
    for row in guesses:
        if not isinstance(row, dict):
            continue
        dropped |= row.pop("excerpt", None) is not None
        known = facts.get(_nav_path(row))
        if not isinstance(known, dict):
            continue
        if not row.get("why_relevant") and known.get("why"):
            row["why"] = known["why"]
        if known.get("functions"):
            row["functions"] = known["functions"]
    first = _first_guess(payload)
    if dropped:
        note = payload.get("note")
        if isinstance(note, str):
            payload["note"] = _EXCERPT_NOTE.sub("", note)
        hint = payload.get("next_action_hint")
        if first and isinstance(hint, str) and hint.startswith("Start from the excerpt of "):
            payload["next_action_hint"] = (
                f"Read {_nav_path(first)} first: it scored highest, and best_guesses "
                "says why each file is in the running."
            )
    return True


def _default_shape(payload: dict[str, Any], question: str) -> None:
    confidence = _shape_confidence(payload)
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
    payload: dict[str, Any], totals: dict[str, int], *, scope: str | None, repo: str | None,
    expanded: bool
) -> None:
    reduced = False
    for key in _COLLECTIONS:
        # By default ``candidate_files`` carries these paths, so counting the
        # hidden rows would only advertise what the reply already serves.
        if key == "candidates" and not expanded:
            continue
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
        # The caller already holds the question; restating a long one costs
        # tokens on every reduced reply. Short scope and repo stay, so a caller
        # that rebuilds the call from this block cannot widen it silently.
        arguments: dict[str, Any] = {"include": ["evidence"]}
        if scope is not None:
            arguments["scope"] = scope
        if repo is not None:
            arguments["repo"] = repo
        projection["recovery"] = {
            "tool": "get_answer",
            "same_arguments": True,
            "arguments": arguments,
        }


def project_answer_payload(
    raw: dict[str, Any], *, question: str, scope: str | None = None,
    repo: str | None = None, include: list[str] | None = None
) -> dict[str, Any]:
    """Return the cache-independent, confidence-specific external response."""
    return _project(raw, question=question, scope=scope, repo=repo, include=include)[0]


def _project(
    raw: dict[str, Any], *, question: str, scope: str | None,
    repo: str | None, include: list[str] | None
) -> tuple[dict[str, Any], bool]:
    """The projection, and whether it slimmed ``best_guesses`` (decided once, here)."""
    payload = copy.deepcopy(raw)
    totals = {
        key: len(payload.get(key) or []) if isinstance(payload.get(key), list) else 0
        for key in _COLLECTIONS
    }
    _deduplicate(payload)
    expanded = "evidence" in set(include or [])
    facts = payload.pop("_candidate_file_facts", None)
    slimmed = False
    if not expanded:
        _default_shape(payload, question)
        if _shape_confidence(payload) == "low":
            slimmed = _slim_best_guesses(payload, facts if isinstance(facts, dict) else {})
    _shape_candidate_files(payload, expanded=expanded)
    _rewrite_degraded_answer(payload)
    for key in _COLLECTIONS:
        if not payload.get(key):
            payload.pop(key, None)
    _record_reductions(payload, totals, scope=scope, repo=repo, expanded=expanded)
    unknown = sorted(set(include or []) - {"evidence"})
    if unknown:
        payload.setdefault("_meta", {})["ignored_arguments"] = {"include": unknown}
    return payload, slimmed


def _served_paths(payload: dict[str, Any]) -> list[str]:
    paths = [path for path in payload.get("citations") or [] if isinstance(path, str)]
    for key in _COLLECTIONS:
        if key == "citations":
            continue
        paths.extend(path for path in map(_path, payload.get(key) or []) if path)
    return list(dict.fromkeys(paths))


def _hint_with_update_pointer(
    hint: Any, confidence: Any, freshness: dict[str, Any]
) -> Any:
    """Add the update pointer to a low-confidence hint served off a behind index."""
    if freshness.get("index_behind") is not True or confidence != "low":
        return hint
    # A stale_warning already names the command, so don't say it twice.
    if freshness.get("stale_warning"):
        return hint
    from repowise.server.mcp_server._meta import INDEX_BEHIND_LOW_CONFIDENCE_HINT

    existing = hint.strip() if isinstance(hint, str) else ""
    if not existing:
        return INDEX_BEHIND_LOW_CONFIDENCE_HINT
    return f"{existing} {INDEX_BEHIND_LOW_CONFIDENCE_HINT}"


async def _refresh_freshness(payload: dict[str, Any], repo: str | None) -> None:
    """Scope trust metadata to evidence in the final fresh/cache projection."""
    if repo == "all":
        return
    try:
        from repowise.core.persistence.database import get_session
        from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
        from repowise.server.mcp_server._index_state import index_state_key
        from repowise.server.mcp_server._meta import freshness_from_repo
        from repowise.server.mcp_server._scope import unrelated_scope_hint

        served = _served_paths(payload)
        ctx = await _resolve_repo_context(repo)
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            scope_hint = await unrelated_scope_hint(
                session,
                repository.id,
                [path.split("::", 1)[0] for path in served],
                cache_key=f"{repository.id}:{index_state_key(repository)}",
            )
        freshness = freshness_from_repo(repository, targets=served)
    except Exception:
        return
    meta = payload.setdefault("_meta", {})
    for key in (
        "index_age_days",
        "indexed_commit",
        "live_head",
        "index_behind",
        "stale_warning",
        "scope_hint",
    ):
        meta.pop(key, None)
    meta.update(freshness)
    if scope_hint:
        meta["scope_hint"] = scope_hint
    # An error reply carries confidence "low" too, and its fault is not the
    # index, so the pointer would only misdirect there.
    try:
        hint = _hint_with_update_pointer(
            meta.get("hint"),
            None if payload.get("error") else payload.get("confidence"),
            freshness,
        )
    except Exception:
        return
    if hint:
        meta["hint"] = hint


_LINE_COUNT_MAX_BYTES = 2_000_000


def _file_size(root: Path, path: str) -> tuple[int | None, int] | None:
    """``(lines, size_bytes)`` of a repo file on disk, or None when it is not one.

    Size from ``stat``; lines by streaming, and None above
    :data:`_LINE_COUNT_MAX_BYTES` so a huge file is never read to count them.
    """
    try:
        abs_path = (root / path).resolve()
        # An index row is not a trust boundary; refuse anything outside the repo.
        abs_path.relative_to(root.resolve())
        if not abs_path.is_file():
            return None
        size = abs_path.stat().st_size
        if size > _LINE_COUNT_MAX_BYTES:
            return None, size
        lines, last = 0, b""
        with abs_path.open("rb") as handle:
            while chunk := handle.read(65536):
                lines += chunk.count(b"\n")
                last = chunk[-1:]
    except (OSError, ValueError):
        return None
    return lines + (1 if last and last != b"\n" else 0), size


def _add_file_sizes(payload: dict[str, Any], root: Path | None) -> None:
    """Stamp live ``lines`` / ``size_bytes`` on slimmed guesses and cue a ranged read.

    Serve-time, not cached, so a dirty tree reports the bytes an agent would Read.
    """
    if root is None:
        return
    for row in payload.get("best_guesses") or []:
        path = _nav_path(row) if isinstance(row, dict) else None
        size = _file_size(root, path) if path else None
        if size is None:
            continue
        lines, row["size_bytes"] = size
        if lines is not None:
            row["lines"] = lines
    top = _first_guess(payload)
    if top and top.get("size_bytes", 0) > _LARGE_FILE_BYTES:
        path = _nav_path(top)
        cue = (
            f"{path} is {top['size_bytes'] // 1024} KB: Read a line range, or call "
            f"get_context(targets=[\"{path}\"], include=[\"skeleton\"]), "
            "rather than the whole file."
        )
        hint = payload.get("next_action_hint")
        payload["next_action_hint"] = f"{hint.rstrip()} {cue}" if isinstance(hint, str) else cue


async def _refresh_file_sizes(payload: dict[str, Any], repo: str | None) -> None:
    """Size the slimmed guesses. Best-effort: a sync stat and bounded read of <= 3 files."""
    if repo == "all":
        return
    try:
        from repowise.server.mcp_server._helpers import _resolve_repo_context
        from repowise.server.mcp_server.tool_answer.evidence import _repo_root

        root = _repo_root(await _resolve_repo_context(repo))
    except Exception:
        return
    _add_file_sizes(payload, root)


def _whole_bodies(payload: dict[str, Any]) -> int:
    """Count symbol bodies that survived the projection intact.

    A body that was cut, or that carries a continuation to fetch the rest, is
    not a whole unit and must never be claimed as one.
    """
    return sum(
        1
        for row in payload.get("symbol_bodies") or []
        if isinstance(row, dict)
        and row.get("verified") is True
        and not row.get("truncated")
        and "continuation" not in row
    )


def _stamp_completeness(payload: dict[str, Any]) -> None:
    """Set or clear ``_meta.complete`` for what this projection actually served."""
    from repowise.server.mcp_server._meta import completeness_line

    line = completeness_line(bodies=_whole_bodies(payload))
    meta = payload.get("_meta")
    if line:
        if not isinstance(meta, dict):
            meta = payload.setdefault("_meta", {})
        meta["complete"] = line
    elif isinstance(meta, dict):
        # A cached payload can carry a claim the current projection no longer
        # serves, so drop it rather than let it stand.
        meta.pop("complete", None)


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
        payload, slimmed = _project(
            raw, question=question, scope=scope, repo=repo, include=include
        )
        await _refresh_freshness(payload, repo)
        if slimmed:
            await _refresh_file_sizes(payload, repo)
        _stamp_completeness(payload)
        return payload

    return _wrapped


__all__ = ["project_answer_payload", "projected_answer"]
