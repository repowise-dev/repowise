"""PostToolUse Read → serve the indexed skeleton instead of the whole file.

The skeleton *nudge* — a one-line pointer at ``get_context(include=
["skeleton"])`` — did not fail on content. It failed by asking the agent to do
something the hook could do itself. This module does it instead: when every
gate below clears, the hook returns ``updatedToolOutput`` and the agent's Read
of a large indexed file arrives as its skeleton.

The nudge has since been retired outright rather than kept as a fallback, so a
client that cannot honour a replacement now gets silence, which is the honest
fallback for a surface with nothing to say.

**This is not a silent truncation.** ``build_skeleton`` marks every elided
span with its 1-indexed line range, so the agent can see exactly what was
removed and range-read any of it back — the same contract ``repowise distill``
makes for shell output and the omission store makes for truncated MCP
responses. Reads were the last unfiltered surface. Reading the file a second
time returns it whole; the header says so.

Gates this surface owns, cheapest first (its caller in :mod:`.read_state`
holds the first five, :func:`skeleton_replacement` the rest):

1. **Unbounded read only.** A ranged Read is already a targeted question.
2. **Above the size floor** (100 output lines).
3. **Not a verification re-read.** A Read that follows an Edit of the same
   file wants fidelity, not structure.
4. **Once per file per session.** The second Read is the escape hatch.
5. **Indexed**, with symbol bounds persisted.
6. **Worth it** — skeleton well under full, and the saving clears the floor.
7. **Under the output cap**, whole. A truncated skeleton would lose its
   trailing elision ranges, which is the part that makes this reversible.

The harness capability probe, the ``hooks.read_skeleton`` opt-in, the
Read-shaped wire payload, the no-payload-no-ledger-row rule and both ledger
writes are :mod:`.replacement`'s, shared with the digest and the re-read
collapse.

Operational rules are the rest of augment's: stdlib on the way in, no network,
any failure degrades to returning None and the agent sees its Read untouched.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

from ._shared import MAX_OUTPUT_CHARS
from .replacement import Offer

if TYPE_CHECKING:  # pragma: no cover - the hook path imports these lazily
    from pathlib import Path

    from repowise.core.distill.skeleton import SkeletonResult, SkeletonSymbol

#: Hard ceiling on the replacement string; shared with every other replacing
#: surface. A skeleton that does not fit is skipped rather than cut, because
#: the tail elision markers are what make the omission recoverable.
_MAX_OUTPUT_CHARS = MAX_OUTPUT_CHARS

#: Savings-ledger identity, so ``repowise saved`` can name this surface.
SAVINGS_SOURCE = "hook-read"
_SAVINGS_FILTER = "read_skeleton"

CONFIG_FLAG = "read_skeleton"


def enabled(repo_path: Path) -> bool:
    """True when this repo opted into read replacement. Fails closed.

    Thin alias over the shared reader so the replacing surfaces cannot drift
    on how they read their flag. See :func:`_shared.hook_flag_enabled` for the
    fail-closed rationale and the env override.
    """
    from ._shared import hook_flag_enabled

    return hook_flag_enabled(repo_path, CONFIG_FLAG)


def is_unbounded_read(tool_input: dict) -> bool:
    """True for a Read with neither offset nor limit — the whole file."""
    if not isinstance(tool_input, dict):
        return False
    return tool_input.get("offset") is None and tool_input.get("limit") is None


# ---------------------------------------------------------------------------
# Building the replacement
# ---------------------------------------------------------------------------


def skeleton_replacement(
    repo_path: Path,
    rel: str,
    *,
    min_ratio_gain: float,
    min_saved_tokens: int,
) -> Offer | None:
    """Render *rel*'s skeleton, or None when any content gate fails.

    Callers own the cheap gates (see the module docstring); by the time this
    runs we have already decided the Read is a candidate, so it may pay for
    the index query and the render (~1ms — pure line slicing, no parser).
    """
    symbols = _indexed_symbols(repo_path / ".repowise" / "wiki.db", rel)
    if not symbols:
        return None
    try:
        source = (repo_path / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not source.strip():
        return None

    from repowise.core.distill.skeleton import build_skeleton

    total_lines = len(source.splitlines())
    # "smart" over "signatures" for the docstring summary line it keeps under
    # each signature. Its body-budget machinery is inert here on purpose: no
    # PageRank is persisted on wiki_symbols, so every importance is 0.0 and no
    # body is ever kept. That is the intended output — a map, not an excerpt.
    result = build_skeleton(source, symbols, mode="smart")
    if result.mode == "raw":  # no usable bounds — the index cannot help here
        return None
    if result.skeleton_tokens > result.full_tokens * min_ratio_gain:
        return None
    if result.full_tokens - result.skeleton_tokens < min_saved_tokens:
        return None

    text = _render(rel, result, total_lines)
    if len(text) > _MAX_OUTPUT_CHARS:
        # Without this retry the cap scales backwards: the biggest files, which
        # have the most to save, are the ones whose signatures alone overflow
        # it. Signatures-only drops the docstring summaries and usually fits.
        result = build_skeleton(source, symbols, mode="signatures")
        text = _render(rel, result, total_lines)
        if len(text) > _MAX_OUTPUT_CHARS:
            return None
    return Offer(
        key=rel,
        text=text,
        raw_tokens=result.full_tokens,
        # The header is part of what the agent is billed for.
        new_tokens=max(1, len(text) // 4),
        category="skeleton_served",
        filter_name=_SAVINGS_FILTER,
    )


#: The elision marker ``_render`` in distill.skeleton emits: indent, then
#: ``... N lines (start-end)``, 1-indexed and inclusive.
_ELISION_RE = re.compile(r"^\s*\.\.\. \d+ lines \((\d+)-(\d+)\)\s*$")


def _render(rel: str, result: SkeletonResult, total_lines: int) -> str:
    """Header plus numbered skeleton — the header is the reversibility contract."""
    body = _number(result.text, total_lines)
    return (
        f"[repowise] Serving the indexed skeleton of {rel} in place of the full file "
        f"(~{result.skeleton_tokens} tokens vs ~{result.full_tokens}; "
        f"{result.symbol_count} symbols).\n"
        "There are two gutters. Ignore the outer one — Claude Code numbers the lines it "
        "is handed, and it is counting skeleton lines. The inner gutter is this file's "
        "real line numbers, and it is the one to Read against.\n"
        "Every `... N lines (a-b)` marker is an elided span: Read this file with that "
        "offset/limit to pull it back, or Read it again with no range to get the whole "
        "file.\n"
        "You have NOT seen the bodies. Do not Edit, Write, or conclude what this file "
        "does or does not contain from a skeleton — read the range first.\n\n"
        f"{body}"
    )


def _number(text: str, total_lines: int) -> str:
    """Restore Read's line-number gutter on the kept lines.

    Claude Code's Read returns ``cat -n`` output, and dropping the gutter would
    leave the agent knowing line numbers only for the spans it *cannot* see —
    exactly backwards. The numbers are recoverable without the source: each
    elision marker states the range it swallowed, so walking the rendered text
    and jumping the counter at every marker reproduces the original numbering.

    This *is* a second gutter, and deliberately. Claude Code renders the
    ``content`` it is handed through its own ``cat -n``, numbering sequentially
    from ``startLine`` — which for a skeleton counts skeleton lines, not file
    lines, and cannot be switched off. So the choice is two gutters where the
    inner one is right, or one gutter that is wrong. Item 5 already settled
    that a wrong line number is worse than none; the header names both columns
    so the outer one cannot be mistaken for the file's.

    Self-checking: if the walk does not land on the file's real line count the
    reconstruction is wrong somewhere, and a wrong number is worse than none,
    so the text is returned unnumbered instead.
    """
    out: list[str] = []
    line_no = 1
    for line in text.splitlines():
        match = _ELISION_RE.match(line)
        if match:
            out.append(line)
            line_no = int(match.group(2)) + 1
            continue
        out.append(f"{line_no:6d}\t{line}")
        line_no += 1
    if line_no - 1 != total_lines:
        return text
    return "\n".join(out) + "\n"


def _indexed_symbols(db_path: Path, rel: str) -> list[SkeletonSymbol]:
    """Persisted symbol rows for one file as ``SkeletonSymbol``s, or [].

    Read-only stdlib sqlite3 for the same reason
    :mod:`fast_lookup` uses it: the hook path must not pay the sqlalchemy
    import to read five columns.
    """
    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT name, kind, start_line, end_line, signature "
                "FROM wiki_symbols WHERE file_path = ?",
                (rel,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    if not rows:
        return []

    from repowise.core.distill.skeleton import SkeletonSymbol

    return [
        SkeletonSymbol(
            name=name,
            kind=kind,
            start_line=start,
            end_line=end,
            signature=signature or "",
        )
        for name, kind, start, end, signature in rows
        if isinstance(start, int) and isinstance(end, int) and start > 0
    ]
