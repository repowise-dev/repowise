"""PostToolUse Grep → serve the compact digest instead of the match flood.

The flood digest has always been the most expensive thing this hook says:
across the efficacy ledger it fired 37 times for ~17.6k tokens, and it *added*
every one of them **next to** the flood the agent had already been billed for.
Ranking a flood you also keep is a lens, not a saving. This module replaces the
flood with the digest instead, the same trade ``repowise distill`` makes for
shell output and :mod:`read_skeleton` now makes for Reads.

**Not a silent truncation.** :func:`render_search_digest` names every file, its
match count, two anchored line numbers per file, and an explicit
``(N more files, M matches)`` tail. The agent can see what was dropped and
re-run scoped to any file it names. The header says so.

What this deliberately does **not** touch, measured over real transcripts:

* **Single-file context greps** (``-C``/``-A``/``-B``) are the most common
  flood by a distance: 20 of 22 in the sample. Claude Code renders them with
  no path prefix, so :func:`group_search_matches` declines them and no digest
  is built. That is the right answer for the wrong reason: the agent asked for
  that context by name, and compressing it would be taking back what it
  requested. Left alone on purpose.
* **``files_with_matches`` and Glob**, whose payloads carry no ``content`` at
  all. There is nothing to replace, and the file list is already a digest.

So the population here is genuinely multi-file floods. On those the measured
digest is 0.30 of the flood, ~1,183 tokens saved per firing, none near the
output cap.

Gates, cheapest first (:func:`digest_replacement`):

1. **The client can honour a replacement.** Codex's hook protocol has no
   output-replacement field (see :func:`replaces_tool_output`).
2. **Opted in**: ``hooks.search_digest``, written by the same init consent as
   ``hooks.read_skeleton``. Fails closed.
3. **The client build supports ``updatedToolOutput``** (shared version probe).
4. **Grep content mode**, with a ``content`` string to stand in for.
5. **Worth it**: digest well under the flood, and the saving clears a floor.
6. **Under the output cap**, whole.

Operational rules are the rest of augment's: stdlib on the way in, no network,
any failure degrades to returning None and the agent sees its Grep untouched.
"""

from __future__ import annotations

from pathlib import Path

from ._shared import MAX_OUTPUT_CHARS, hook_flag_enabled
from ._shared import record_forgone as _record_forgone
from ._shared import record_saving as _record_saving

#: Savings-ledger identity, so ``repowise saved`` can name this surface.
_SAVINGS_SOURCE = "hook-search"
_SAVINGS_FILTER = "search_digest"

_CONFIG_FLAG = "search_digest"

#: The digest must be at most this fraction of the flood it replaces. Mirrors
#: ``read_state._READ_NUDGE_MAX_RATIO``: below half, replacing is a saving;
#: above it, it is a detour with extra steps.
_MAX_RATIO = 0.5

#: Floor on tokens saved. Lower than the Read surface's 1500 because a flood is
#: a smaller object than a whole file. The measured median saving on real
#: multi-file floods is ~1,183 tokens, which a 1500 floor would reject
#: outright. Set under the measurement, not over it.
_MIN_SAVED_TOKENS = 400


class DigestReplacement:
    """A digest ready to stand in for a Grep flood, and its ledger facts."""

    __slots__ = ("digest_tokens", "flood_tokens", "pattern", "payload", "text")

    def __init__(self, *, pattern: str, text: str, flood_tokens: int, digest_tokens: int) -> None:
        self.pattern = pattern
        self.text = text
        self.flood_tokens = flood_tokens
        self.digest_tokens = digest_tokens
        #: Grep-shaped wire payload wrapping :attr:`text`, filled in by the
        #: caller once it has the Grep's own ``tool_response`` to build from.
        self.payload: dict | None = None

    @property
    def saved_tokens(self) -> int:
        return max(0, self.flood_tokens - self.digest_tokens)


def enabled(repo_path: Path) -> bool:
    """True when this repo opted into flood replacement. Fails closed."""
    return hook_flag_enabled(repo_path, _CONFIG_FLAG)


def replaces_tool_output(client: str | None) -> bool:
    """True when *client*'s hook protocol can honour ``updatedToolOutput``.

    Only Claude Code can. Codex's hook contract carries a context string and
    nothing else, so a replacement handed to it is dropped on the floor, and
    dropped *silently*, which is the failure that let the Read surface record
    ``skeleton_served`` rows for two commits while every agent saw the original
    file. A capability that is checked is worth more than one that is assumed,
    even while Codex registers no Grep matcher to reach this with.

    This is the narrow version of what ``AgentAdapter`` already does for the
    rewrite hook via ``rewrite_permissions`` (plan item 18 folds augment into
    that ABC properly). Unknown clients are treated as Claude Code, matching
    every other handler here: ``--client`` is passed only by Codex's own
    lifecycle hooks.
    """
    return client != "codex"


def as_grep_output(tool_output: object, text: str) -> dict | None:
    """Wrap *text* in the object shape Claude Code requires for a Grep.

    ``updatedToolOutput`` is validated against the schema of the tool being
    replaced, not against a common one. Grep's content mode is
    ``{"mode": "content", "content", "numLines", "numFiles", "filenames",
    "totalLines"}``, and handing it a bare string is rejected, after which the
    *original* output goes to the agent while the hook still records a served
    row. That failure is invisible from inside the hook (exit 0, no stderr),
    which is why this builds from the payload's own ``tool_response`` rather
    than constructing the envelope from scratch: unknown or future keys are
    carried through untouched and only ``content`` and its line count move.

    ``totalLines`` and ``numFiles`` are deliberately left alone. They describe
    the search, not the rendering of it, and they stay true after the swap. The digest's own
    header restates both anyway.

    Returns None when the payload is not the shape we think it is, which
    degrades to no replacement rather than to a rejected one.
    """
    if not isinstance(tool_output, dict):
        return None
    if tool_output.get("mode") != "content":
        # files_with_matches / Glob carry no content to stand in for.
        return None
    if not isinstance(tool_output.get("content"), str):
        return None
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return {**tool_output, "content": text, "numLines": lines}


def digest_replacement(
    pattern: str, flood_text: str, digest_body: str
) -> DigestReplacement | None:
    """A replacement for this flood, or None when it is not worth making.

    *digest_body* is what :func:`search._grep_flood_digest` already rendered. This decides whether serving it in place of *flood_text* is a saving, and
    never re-renders it. Callers own the cheaper gates above.
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
    return DigestReplacement(
        pattern=pattern,
        text=text,
        flood_tokens=flood_tokens,
        digest_tokens=digest_tokens,
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


def record_saving(repo_path: Path, replacement: DigestReplacement) -> None:
    """Bill this replacement to the savings ledger so ``repowise saved`` sees it."""
    _record_saving(
        repo_path,
        source=_SAVINGS_SOURCE,
        filter_name=_SAVINGS_FILTER,
        command=replacement.pattern,
        raw_tokens=replacement.flood_tokens,
        distilled_tokens=replacement.digest_tokens,
    )


def record_forgone(repo_path: Path, replacement: DigestReplacement) -> None:
    """Record a saving this repo *would* have made, had the surface been on."""
    _record_forgone(
        repo_path,
        source=_SAVINGS_SOURCE,
        path=replacement.pattern,
        raw_tokens=replacement.flood_tokens,
        distilled_tokens=replacement.digest_tokens,
    )
