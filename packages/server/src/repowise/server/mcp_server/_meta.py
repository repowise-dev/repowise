"""Shared `_meta` envelope helpers for MCP tool responses.

Every tool can attach a small `_meta` dict to its response with timing and
optional hint text. The hint is the killer feature: a short, conservative
nudge toward the cheaper next-tool when one obviously applies. Hints are
intentionally narrow — pushing every agent toward `get_symbol` regardless of
question shape would replicate the over-trust failure mode that drove
jcodemunch's accuracy regression on alive-with-dead-exports tasks.

Rules of thumb baked into the hint generators:
  * NEVER suggest a more compact tool when the original question contains
    explanation words ("explain", "why", "how does", "what is the relationship",
    "describe").
  * Only suggest get_symbol when the agent has already pinpointed a single
    symbol or single file — never as a starting move.
  * Hints are advisory; the harness/agent is free to ignore them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Only warn about age when we have no other signal AND the index is genuinely
# old. A short threshold here would nag on every call and train the agent to
# ignore the field — defeating the point. 90 days is a deliberate floor: by
# then even quiet repos have likely drifted enough that a re-index matters.
# The preferred path is the git-HEAD comparison below, which fires only on a
# real mismatch and gives a single, calibrated stale signal.
_STALE_AGE_FLOOR_DAYS = 90


def _read_live_head(local_path: str | None) -> str | None:
    """Read the repo's current git HEAD SHA via plain file I/O.

    Returns a full 40-char SHA, or ``None`` when the repo isn't a git checkout
    on disk (hosted indexes, ephemeral clones). Avoids spawning ``git``: we
    parse ``.git/HEAD`` and follow at most one ref — fast enough to call on
    every MCP tool response without caching, and never blocks the event loop.

    Detached HEADs are handled (the HEAD file contains the SHA directly).
    Unknown ref formats just return ``None`` rather than guessing — staleness
    semantics should fail closed (no warning) rather than open (false alarms).
    """
    if not local_path:
        return None
    git_dir = Path(local_path) / ".git"
    if not git_dir.is_dir():
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref: "):
        ref_rel = head[5:].strip()
        # Try the loose ref first, then packed-refs as a fallback. Packed-refs
        # is common right after `git gc` or on freshly cloned repos.
        ref_file = git_dir / ref_rel
        try:
            return ref_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        packed = git_dir / "packed-refs"
        try:
            for raw in packed.read_text(encoding="utf-8").splitlines():
                if raw.startswith("#") or raw.startswith("^"):
                    continue
                sha, _, name = raw.partition(" ")
                if name.strip() == ref_rel:
                    return sha.strip() or None
        except OSError:
            return None
        return None
    # Detached HEAD: the file contains the SHA verbatim.
    return head or None


def freshness_from_repo(repository: Any | None) -> dict[str, Any]:
    """Return a minimal freshness dict for the given Repository row.

    Calibrated to fire ``stale_warning`` rarely so the agent keeps trusting
    the tools. Two signals, in order of preference:

      1. **Git HEAD comparison** (preferred). If the repo is a checkout on
         disk, compare ``repository.head_commit`` to the live HEAD. They
         match → silence. They differ → ``stale_warning`` plus the live SHA
         so the agent can decide whether to call ``repowise update``.
      2. **Age fallback** (only when git is unreachable). Warn only past 90
         days; under that, just emit ``index_age_days`` for the agent to
         consult on its own terms.

    Always-emitted fields:
      * ``index_age_days``  — informational, never a directive
      * ``indexed_commit``  — short SHA the index was built against

    Conditionally emitted:
      * ``live_head``       — only when it differs from the indexed commit
      * ``stale_warning``   — only on a real signal (HEAD mismatch OR very old)

    Defensive throughout: any missing piece is dropped rather than raised so
    an upstream change to the Repository model can never poison a tool result.
    """
    if repository is None:
        return {}
    out: dict[str, Any] = {}

    updated_at = getattr(repository, "updated_at", None)
    age_days: int | None = None
    if isinstance(updated_at, datetime):
        ua = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - ua).days)
        out["index_age_days"] = age_days

    indexed_full = getattr(repository, "head_commit", None) or None
    if indexed_full:
        out["indexed_commit"] = indexed_full[:12] if isinstance(indexed_full, str) else indexed_full

    local_path = getattr(repository, "local_path", None)
    live_full = _read_live_head(local_path)

    if live_full and indexed_full:
        if live_full != indexed_full:
            out["live_head"] = live_full[:12]
            out["stale_warning"] = (
                f"Index built at {indexed_full[:12]} but HEAD is at "
                f"{live_full[:12]} — code has moved on. Run `repowise update`."
            )
        # Match: deliberately emit nothing extra. Silence is the signal.
    elif live_full is None and age_days is not None and age_days > _STALE_AGE_FLOOR_DAYS:
        # No git signal available and the index is genuinely old.
        out["stale_warning"] = (
            f"Index is {age_days} days old and live HEAD is unreachable — "
            "results may be stale. Run `repowise update`."
        )

    return out

# Question patterns where narrative wiki context wins over symbol-body slicing.
# Used to suppress "use get_symbol" hints — those questions need surrounding prose.
_EXPLAIN_TOKENS = (
    "explain",
    "why ",
    "why is",
    "why does",
    "why was",
    "how does",
    "how do",
    "how is",
    "how are",
    "what is the relationship",
    "describe",
    "walk me through",
    "tell me about",
    "purpose of",
)


def is_explanation_question(question: str | None) -> bool:
    """True if the question reads like 'explain X', not 'find X'.

    Used as a guard before any hint that would push the agent toward
    symbol-level (narrower) retrieval. Conservative by design: any explanation
    cue suppresses the hint.
    """
    if not question:
        return False
    q = question.strip().lower()
    return any(tok in q for tok in _EXPLAIN_TOKENS)


def build_meta(
    *,
    timing_ms: float | None = None,
    hint: str | None = None,
    cached: bool = False,
    repository: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a `_meta` envelope. All fields optional, omitted if falsy.

    Pass ``repository`` to auto-inject freshness fields (``index_age_days``,
    ``indexed_commit``, optional ``stale_warning``). Every MCP response should
    carry these so an agent can detect drift between the index and live HEAD
    without an extra round-trip.

    Stable shape:
      {
        "timing_ms":       float,  # tool wall-time (omitted if None)
        "hint":            str,    # short follow-up suggestion (omitted if None)
        "cached":          bool,   # only included when True
        "index_age_days":  int,    # days since last `repowise update`
        "indexed_commit":  str,    # short SHA the index was built against
        "stale_warning":   str,    # only when age > threshold
        ...extras
      }
    """
    out: dict[str, Any] = {}
    if timing_ms is not None:
        out["timing_ms"] = round(float(timing_ms), 2)
    if hint:
        out["hint"] = hint
    if cached:
        out["cached"] = True
    if repository is not None:
        out.update(freshness_from_repo(repository))
    if extra:
        out.update(extra)
    return out


def context_hint(targets: list[str], compact: bool, include: set[str] | None = None) -> str | None:
    """Hint for `get_context` callers.

    Conservative: only fires when the call shape suggests the agent could
    have used a cheaper tool, AND the suggestion is unambiguously safe.
    """
    if not targets:
        return None
    # If caller requested source and got a large symbol, nudge toward Read with offset
    if include and "source" in include and len(targets) == 1:
        return None  # source mode provides its own truncation info
    return None


def symbol_hint(symbol_id: str, end_line: int, start_line: int) -> str | None:
    """Hint for source retrieval (kept for backward compat with tool_symbol.py)."""
    return None


def answer_hint(confidence: str, retrieval_count: int) -> str | None:
    """Hint for `get_answer` callers.

    Encourages verification when confidence is low; never tells the agent to
    "trust the answer" — that's the over-trust failure mode.
    """
    if confidence == "low":
        return (
            "Low confidence — Read the listed fallback_targets to verify "
            "before answering."
        )
    if retrieval_count == 0:
        return "No wiki hits — fall back to search_codebase or Grep."
    return None
