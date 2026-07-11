"""Detect the coding agent's model, for pricing savings at the real rate.

Saved tokens are *input* tokens the coding agent never had to read, so the
dollar value of a saving should be priced at that agent's actual input rate —
not a flat guess. This module scans the local agent transcripts to find the
most-recent model that touched a repo:

  - **Claude Code** — ``~/.claude/projects/<munged-cwd>/*.jsonl``; the model is
    each assistant entry's ``message.model`` (e.g. ``claude-opus-4-8``).
  - **Codex** — ``~/.codex/sessions/**/*.jsonl``; the model appears on a turn
    context / session record (``model`` field, sometimes nested under
    ``payload``). The exact shape varies across Codex versions, so the parser
    is deliberately tolerant and checks several plausible locations.

The newest transcript (by file mtime) across both agents wins. Everything is
best-effort and read-only: an unreadable file, an absent directory, or a shape
we don't recognize degrades to the Sonnet default, never an error — this runs
inside a dashboard endpoint that must not break because a transcript changed.

Privacy: transcripts are read from the user's own machine and never leave it
(same posture as :mod:`repowise.core.distill.missed`).

Layering note: this lives in ``core`` (the server costs endpoint, which cannot
import ``cli``, is the primary caller), so the Codex/Claude transcript shapes
live here rather than in the ``cli`` agent adapters — ``core`` must not depend
on ``cli``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from repowise.core.fs_walk import iter_glob

#: Pricing fallback when no agent transcript is detectable.
DEFAULT_MODEL = "claude-sonnet-4-6"

#: Explicit raw→pricing-key aliases. Empty today; the bracket-suffix strip in
#: :func:`normalize_model_id` covers the known cases (``claude-opus-4-8[1m]``).
#: Add entries here when a transcript reports a name the pricing table spells
#: differently — keeping the pricing table the single source of truth.
_ALIASES: dict[str, str] = {}

#: Trailing bracketed qualifier such as the context-window tag ``[1m]``.
_BRACKET_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")


@dataclass(frozen=True)
class ResolvedModel:
    """The agent model to price savings at, plus how it was determined."""

    #: Pricing-table key, e.g. ``"claude-opus-4-8"``.
    model: str
    #: As seen in the transcript, e.g. ``"claude-opus-4-8[1m]"``.
    raw: str
    #: ``"claude_code"`` | ``"codex"`` | ``"unknown"``.
    agent: str
    #: Human label, e.g. ``"detected from Claude Code session"``.
    source: str


_DEFAULT = ResolvedModel(DEFAULT_MODEL, DEFAULT_MODEL, "unknown", "default")


@dataclass(frozen=True)
class _Sighting:
    """One agent's most-recent model and the transcript mtime it came from."""

    model: str
    ts: float


def normalize_model_id(raw: str) -> str:
    """Normalize a transcript model id to a pricing-table key.

    Strips a trailing bracketed qualifier (the ``[1m]`` context-window tag) and
    applies the explicit alias map. Conservative on purpose — it does *not*
    strip date suffixes, since some real keys carry one
    (``claude-3-5-sonnet-20241022``).
    """
    cleaned = _BRACKET_SUFFIX.sub("", (raw or "").strip())
    return _ALIASES.get(cleaned, cleaned)


def resolve_session_model(
    repo_root: Path,
    *,
    projects_root: Path | None = None,
    codex_sessions_root: Path | None = None,
) -> ResolvedModel:
    """Most-recent agent model that touched *repo_root*, for savings pricing.

    *projects_root* / *codex_sessions_root* override the Claude Code and Codex
    transcript roots (for tests); both default to their real ``~/`` locations.
    Returns the Sonnet :data:`_DEFAULT` when nothing is detectable.
    """
    try:
        return _resolve(Path(repo_root), projects_root, codex_sessions_root)
    except Exception:
        return _DEFAULT


def _resolve(
    repo_root: Path, projects_root: Path | None, codex_sessions_root: Path | None
) -> ResolvedModel:
    candidates: list[tuple[str, str, _Sighting]] = []
    claude = _claude_code_sighting(repo_root, projects_root)
    if claude is not None:
        candidates.append(("claude_code", "Claude Code", claude))
    codex = _codex_sighting(repo_root, codex_sessions_root)
    if codex is not None:
        candidates.append(("codex", "Codex", codex))
    if not candidates:
        return _DEFAULT
    agent, label, sighting = max(candidates, key=lambda c: c[2].ts)
    return ResolvedModel(
        model=normalize_model_id(sighting.model),
        raw=sighting.model,
        agent=agent,
        source=f"detected from {label} session",
    )


# -- Claude Code ----------------------------------------------------------


def _claude_code_sighting(repo_root: Path, projects_root: Path | None) -> _Sighting | None:
    """Newest Claude Code transcript for *repo_root* that names a model."""
    from repowise.core.sessions import transcript_dir_for

    directory = transcript_dir_for(repo_root, projects_root)
    if not directory.is_dir():
        return None
    for path in _by_mtime_desc(directory.glob("*.jsonl")):
        model = _last_claude_model(path)
        if model:
            try:
                return _Sighting(model, path.stat().st_mtime)
            except OSError:
                continue
    return None


def _last_claude_model(path: Path) -> str | None:
    """The last assistant ``message.model`` in a Claude Code transcript."""
    model: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if '"model"' not in raw or '"assistant"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                value = (entry.get("message") or {}).get("model")
                if isinstance(value, str) and value:
                    model = value
    except OSError:
        return None
    return model


# -- Codex ----------------------------------------------------------------


def _codex_sighting(repo_root: Path, sessions_root: Path | None) -> _Sighting | None:
    """Newest Codex session under *repo_root* that names a model.

    Sessions whose recorded cwd lies outside *repo_root* are skipped; a session
    with no recoverable cwd is kept (best-effort — better a model than nothing).
    """
    root = sessions_root if sessions_root is not None else Path.home() / ".codex" / "sessions"
    if not root.is_dir():
        return None
    repo_prefix = str(repo_root).lower().rstrip("\\/")
    best: _Sighting | None = None
    for path in _by_mtime_desc(iter_glob(root, "*.jsonl")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if best is not None and mtime <= best.ts:
            break  # files are mtime-desc; nothing later can beat the current best
        found = _codex_model_and_cwd(path)
        if found is None:
            continue
        model, cwd = found
        if cwd and not cwd.lower().rstrip("\\/").startswith(repo_prefix):
            continue
        best = _Sighting(model, mtime)
    return best


def _codex_model_and_cwd(path: Path) -> tuple[str, str | None] | None:
    """Last model id and a session cwd from a Codex rollout file, if any."""
    model: str | None = None
    cwd: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if '"model"' not in raw and '"cwd"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                found_model, found_cwd = _extract_codex_fields(entry)
                if found_model:
                    model = found_model
                if found_cwd:
                    cwd = found_cwd
    except OSError:
        return None
    if model is None:
        return None
    return model, cwd


def _extract_codex_fields(entry: dict) -> tuple[str | None, str | None]:
    """Pull a model id and cwd from a Codex record across known shapes.

    Codex versions have placed the model at the top level, under ``payload``,
    and nested in a ``turn_context``/``info`` object; cwd similarly. We check
    each plausible spot and take whatever is present.
    """
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    turn = payload.get("turn_context") if isinstance(payload.get("turn_context"), dict) else {}
    info = entry.get("info") if isinstance(entry.get("info"), dict) else {}

    model = _first_str(
        entry.get("model"), payload.get("model"), turn.get("model"), info.get("model")
    )
    cwd = _first_str(entry.get("cwd"), payload.get("cwd"), turn.get("cwd"), info.get("cwd"))
    return model, cwd


def _first_str(*values: object) -> str | None:
    """First non-empty string among *values*, else None."""
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


# -- shared ---------------------------------------------------------------


def _by_mtime_desc(paths) -> list[Path]:
    """*paths* sorted newest-first by mtime; unreadable files sort last."""

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return float("-inf")

    return sorted(paths, key=_mtime, reverse=True)
