"""PostToolUse Read → serve the indexed skeleton instead of the whole file.

The skeleton *nudge* — a one-line pointer at ``get_context(include=
["skeleton"])`` — fired 516 times across 203 sessions and was acted on once.
It did not fail on content. It failed by asking the agent to do something the
hook could do itself. This module does it instead: when every gate below
clears, the hook returns ``updatedToolOutput`` and the agent's Read of a large
indexed file arrives as its skeleton.

The nudge has since been retired outright rather than kept as a fallback: a
replay under three looser judges, including the compliance form used for a
notice that asks for a *non*-action, found it at or below the unconditioned
base rate on every one, and a session's second nudge did no better than its
first. ``sessions/efficacy.py`` carries the numbers. So a client that cannot
honour a replacement now gets silence, which is the honest fallback for a
surface with nothing to say.

**This is not a silent truncation.** ``build_skeleton`` marks every elided
span with its 1-indexed line range, so the agent can see exactly what was
removed and range-read any of it back — the same contract ``repowise distill``
makes for shell output and the omission store makes for truncated MCP
responses. Reads were the last unfiltered surface. Reading the file a second
time returns it whole; the header says so.

Gates, cheapest first (:func:`skeleton_replacement`):

1. **Unbounded read only.** A ranged Read is already a targeted question.
2. **Above the size floor** (100 output lines).
3. **Opted in.** ``hooks.read_skeleton: true`` in ``.repowise/config.yaml``,
   default off. Read with a lazy ``yaml`` import and fail-closed.
4. **Not a verification re-read.** A Read that follows an Edit of the same
   file wants fidelity, not structure.
5. **Once per file per session.** The second Read is the escape hatch.
6. **Indexed**, with symbol bounds persisted.
7. **Worth it** — skeleton well under full, and the saving clears the floor.
8. **Under the output cap**, whole. A truncated skeleton would lose its
   trailing elision ranges, which is the part that makes this reversible.

Operational rules are the rest of augment's: stdlib on the way in, no network,
any failure degrades to returning None and the agent sees its Read untouched.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from ._shared import MAX_OUTPUT_CHARS, hook_flag_enabled
from ._shared import record_forgone as _record_forgone
from ._shared import record_saving as _record_saving

if TYPE_CHECKING:  # pragma: no cover - the hook path imports these lazily
    from repowise.core.distill.skeleton import SkeletonResult, SkeletonSymbol

#: Claude Code release that introduced ``updatedToolOutput``. Older clients
#: ignore the field, which would silently drop the enrichment, so on those the
#: Read is left untouched (see :func:`supports_updated_output`).
_MIN_CLIENT_VERSION = (2, 1, 218)

#: Hard ceiling on the replacement string; shared with every other replacing
#: surface. A skeleton that does not fit is skipped rather than cut, because
#: the tail elision markers are what make the omission recoverable.
_MAX_OUTPUT_CHARS = MAX_OUTPUT_CHARS

#: Savings-ledger identity, so ``repowise saved`` can name this surface.
_SAVINGS_SOURCE = "hook-read"
_SAVINGS_FILTER = "read_skeleton"

_CONFIG_FLAG = "read_skeleton"


class Replacement:
    """A skeleton ready to be served in place of a Read, and its ledger facts.

    A tiny value object rather than a tuple: the caller records a saving, logs
    a ledger row and emits the text, and three positional elements at that
    call site would read as noise.
    """

    __slots__ = ("full_tokens", "payload", "rel", "skeleton_tokens", "text")

    def __init__(self, *, rel: str, text: str, full_tokens: int, skeleton_tokens: int) -> None:
        self.rel = rel
        self.text = text
        self.full_tokens = full_tokens
        self.skeleton_tokens = skeleton_tokens
        #: Read-shaped wire payload wrapping :attr:`text`, filled in by the
        #: caller once it has the Read's own ``tool_response`` to build from.
        self.payload: dict | None = None

    @property
    def saved_tokens(self) -> int:
        return max(0, self.full_tokens - self.skeleton_tokens)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def enabled(repo_path: Path) -> bool:
    """True when this repo opted into read replacement. Fails closed.

    Thin alias over the shared reader so the two replacing surfaces cannot
    drift on how they read their flag. See :func:`_shared.hook_flag_enabled`
    for the fail-closed rationale and the env override.
    """
    return hook_flag_enabled(repo_path, _CONFIG_FLAG)


def supports_updated_output() -> bool:
    """True when the Claude Code client can honour ``updatedToolOutput``.

    There is no version in the hook payload and no version env var, and
    ``claude --version`` is a ~1s subprocess this path cannot afford. The one
    cheap on-disk signal is the updater's own record, so we read that and
    **fail open** — an unsupported client ignores the unknown field, which
    costs a skipped enrichment, never a broken Read.
    """
    override = os.environ.get("REPOWISE_HOOK_UPDATED_OUTPUT")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    version = _recorded_client_version()
    return version is None or version >= _MIN_CLIENT_VERSION


def _recorded_client_version() -> tuple[int, ...] | None:
    """Installed Claude Code version per ``~/.claude/.last-update-result.json``.

    Best effort by construction: the file only exists once the updater has run
    at least once, and its ``version_to`` is null on a failed update — in which
    case ``version_from`` is what is still installed.
    """
    try:
        raw = (Path.home() / ".claude" / ".last-update-result.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError, RuntimeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("version_to", "version_from"):
        value = data.get(key)
        if isinstance(value, str) and value:
            parts = value.split(".")
            if all(p.isdigit() for p in parts) and parts:
                return tuple(int(p) for p in parts)
    return None


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
) -> Replacement | None:
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
    return Replacement(
        rel=rel,
        text=text,
        full_tokens=result.full_tokens,
        # The header is part of what the agent is billed for.
        skeleton_tokens=max(1, len(text) // 4),
    )


def as_read_output(tool_output: object, text: str) -> dict | None:
    """Wrap *text* in the object shape Claude Code requires for a Read.

    ``updatedToolOutput`` is validated against the schema of the tool being
    replaced, not against a common one. Read's is
    ``{"type": "text", "file": {"filePath", "content", "numLines",
    "startLine", "totalLines"}}``, and handing it a bare string is rejected
    with ``does not match Read's output shape`` — after which the *original*
    output goes to the agent while the hook still records a served row. That
    failure is invisible from inside the hook (exit 0, no stderr), which is
    why this builds from the payload's own ``tool_response`` rather than
    constructing the envelope from scratch: unknown or future keys are
    carried through untouched and only ``content`` and the line counts move.

    Returns None when the payload is not the shape we think it is, which
    degrades to no replacement rather than to a rejected one.
    """
    if not isinstance(tool_output, dict):
        return None
    file_block = tool_output.get("file")
    if not isinstance(file_block, dict) or "content" not in file_block:
        return None
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    updated_file = {**file_block, "content": text, "numLines": lines, "startLine": 1}
    return {**tool_output, "file": updated_file}


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


# ---------------------------------------------------------------------------
# Savings ledger
# ---------------------------------------------------------------------------


def record_forgone(repo_path: Path, replacement: Replacement) -> None:
    """Record a saving this repo *would* have made, had the feature been on."""
    _record_forgone(
        repo_path,
        source=_SAVINGS_SOURCE,
        path=replacement.rel,
        raw_tokens=replacement.full_tokens,
        distilled_tokens=replacement.skeleton_tokens,
    )


def record_saving(repo_path: Path, replacement: Replacement) -> None:
    """Bill this replacement to the savings ledger so ``repowise saved`` sees it."""
    _record_saving(
        repo_path,
        source=_SAVINGS_SOURCE,
        filter_name=_SAVINGS_FILTER,
        command=replacement.rel,
        raw_tokens=replacement.full_tokens,
        distilled_tokens=replacement.skeleton_tokens,
    )
