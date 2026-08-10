"""PostToolUse Grep → serve the compact digest instead of the match flood.

The flood digest used to be appended **next to** the flood the agent had
already been billed for. Ranking a flood you also keep is a lens, not a
saving. This module replaces the flood with the digest instead, the same trade
``repowise distill`` makes for shell output and :mod:`read_skeleton` now makes
for Reads.

**Not a silent truncation.** :func:`render_search_digest` names every file, its
match count, two anchored line numbers per file, and an explicit
``(N more files, M matches)`` tail. The agent can see what was dropped and
re-run scoped to any file it names. The header says so.

What this deliberately does **not** touch:

* **Single-file context greps** (``-C``/``-A``/``-B``). Claude Code renders
  them with no path prefix, so :func:`group_search_matches` declines them and
  no digest is built. That is the right answer for the wrong reason: the agent
  asked for that context by name, and compressing it would be taking back what
  it requested. Left alone on purpose.
* **``files_with_matches`` and Glob**, whose payloads carry no ``content`` at
  all. There is nothing to replace, and the file list is already a digest.

So the population here is genuinely multi-file floods.

Gates: this surface owns only the two that are about a *digest* — worth it,
and under the output cap (:func:`digest_replacement`). The harness capability
probe, the opt-in flag, the Grep-shaped wire payload, the no-payload-no-row
rule and both ledger writes are :mod:`.replacement`'s, shared with every other
replacing surface.

Operational rules are the rest of augment's: stdlib on the way in, no network,
any failure degrades to returning None and the agent sees its Grep untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._shared import MAX_OUTPUT_CHARS
from .replacement import Offer

if TYPE_CHECKING:  # pragma: no cover - hot path keeps pathlib out of the graph
    from pathlib import Path

#: Savings-ledger identity, so ``repowise saved`` can name this surface.
SAVINGS_SOURCE = "hook-search"
_SAVINGS_FILTER = "search_digest"

CONFIG_FLAG = "search_digest"

#: The digest must be at most this fraction of the flood it replaces. Mirrors
#: ``read_state._READ_NUDGE_MAX_RATIO``: below half, replacing is a saving;
#: above it, it is a detour with extra steps.
_MAX_RATIO = 0.5

#: Floor on tokens saved. Lower than the Read surface's 1500 because a flood is
#: a smaller object than a whole file. The measured median saving on real
#: multi-file floods is ~1,183 tokens, which a 1500 floor would reject
#: outright. Set under the measurement, not over it.
_MIN_SAVED_TOKENS = 400


def enabled(repo_path: Path) -> bool:
    """True when this repo opted into flood replacement. Fails closed."""
    from ._shared import hook_flag_enabled

    return hook_flag_enabled(repo_path, CONFIG_FLAG)


def digest_replacement(pattern: str, flood_text: str, digest_body: str) -> Offer | None:
    """A replacement for this flood, or None when it is not worth making.

    *digest_body* is what :func:`search._grep_flood_digest` already rendered.
    This decides whether serving it in place of *flood_text* is a saving, and
    never re-renders it. The gates around this one — capability, opt-in, wire
    shape, ledgers — are :mod:`.replacement`'s.
    """
    text = _render(digest_body)
    if len(text) > MAX_OUTPUT_CHARS:
        # A cut digest loses its trailing "N more files" line, which is the
        # part that makes the omission visible. Skip rather than truncate.
        return None
    flood_tokens = max(1, len(flood_text) // 4)
    digest_tokens = max(1, len(text) // 4)
    if digest_tokens > flood_tokens * _MAX_RATIO:
        return None
    if flood_tokens - digest_tokens < _MIN_SAVED_TOKENS:
        return None
    return Offer(
        key=pattern,
        text=text,
        raw_tokens=flood_tokens,
        new_tokens=digest_tokens,
        category="digest_served",
        filter_name=_SAVINGS_FILTER,
    )


def _render(digest_body: str) -> str:
    """Header plus digest; the header is the reversibility contract."""
    return (
        "[repowise] Serving a compact digest of this search in place of the raw "
        "matches. Every matched file is named below with its match count and "
        "anchor line numbers; re-run the search scoped to one of them, or read "
        "the lines directly, to see any match in full.\n"
        f"{digest_body}"
    )
