"""What repowise costs an agent, so a saving can be reported as a net.

The savings ledger has only ever counted credits. It therefore cannot report a
loss, which makes it unable to answer the one question worth asking about any
of this: is the programme positive? A dashboard that structurally cannot go
negative is not measuring, it is advertising.

Three debits are computable here, and each is priced the way it is actually
paid:

* **The repowise block in ``CLAUDE.md``** is *resident*. It sits in the prompt
  prefix, so it is not paid once — it is re-read on every API call in the
  session. That makes it the most expensive place a token can sit, and it is
  where we put the most.
* **The MCP tool schema**, on the same terms, whenever the harness loads it
  rather than deferring it.
* **Every advisory injection**, priced at the characters it added times the
  amplification for the remaining calls in that session.

A fourth — tool uses a surface proposed that the user or a policy then
rejected — is **not** computed. It needs a transcript pass this module does not
do, and inventing a number for it would be worse than leaving the line blank.
It is named in the report as missing rather than silently omitted.

**Amplification is measured, never assumed.** ``cache_read / cache_creation``
per session is the multiplier on every figure above, and it is a function of
how long the session ran: a token in the prefix is re-read once per subsequent
call, so a long session punishes resident cost and a short one barely notices
it. A single constant would be wrong at both ends, so this reads the real ratio
out of the transcripts and reports the call count beside it.

Everything here is read-only and best-effort: transcripts stay on the user's
machine, an unreadable one is skipped, and any failure degrades to a smaller
report rather than an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Characters per token. The same crude estimate the savings ledger uses, kept
#: identical on purpose: a debit and a credit measured differently would not be
#: subtractable, and being consistently approximate beats being inconsistently
#: precise. The ``usage`` figures below are exact and are not estimated.
_CHARS_PER_TOKEN = 4

#: Where the repowise block starts in a project's CLAUDE.md, and where it ends.
#: The generated block is fenced by this heading; everything from it to the next
#: top-level heading is ours. A file without the heading contributes nothing.
_BLOCK_HEADING = "## Codebase Intelligence for"


@dataclass
class Amplification:
    """How many times a resident token was re-read, measured not assumed."""

    #: ``cache_read / cache_creation``, per session, median across sessions.
    ratio: float = 0.0
    #: Median API calls per session — the reason the ratio is what it is.
    calls: int = 0
    #: Sessions the ratio was measured over.
    sessions: int = 0

    @property
    def known(self) -> bool:
        return self.sessions > 0 and self.ratio > 0


@dataclass
class Debit:
    """One thing repowise costs, and how that cost is incurred."""

    label: str
    #: Tokens added to context once.
    raw_tokens: int
    #: Tokens actually billed, after amplification.
    billed_tokens: int
    #: How the figure was arrived at, shown to the reader verbatim.
    detail: str


@dataclass
class Footprint:
    """The full cost side, plus what could not be measured."""

    debits: list[Debit] = field(default_factory=list)
    amplification: Amplification = field(default_factory=Amplification)
    #: Cost lines that exist but were not computed, named so the total is read
    #: as a lower bound rather than as complete.
    unmeasured: list[str] = field(default_factory=list)

    @property
    def billed_total(self) -> int:
        return sum(d.billed_tokens for d in self.debits)

    @property
    def raw_total(self) -> int:
        return sum(d.raw_tokens for d in self.debits)


def measure_amplification(
    repo_root: Path, *, projects_root: Path | None = None, limit: int = 25
) -> Amplification:
    """Median ``cache_read / cache_creation`` over this repo's recent sessions.

    Exact, not estimated: these come from each assistant message's own ``usage``
    block. A session with no cache creation is skipped rather than counted as
    zero — it means the prefix was never cached, so there is no re-read ratio
    to speak of, which is different from a ratio of nothing.
    """
    try:
        from repowise.core.sessions import transcript_dir_for

        directory = transcript_dir_for(repo_root, projects_root)
        if not directory.is_dir():
            return Amplification()
        files = sorted(
            directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:limit]
    except (OSError, ValueError):
        return Amplification()

    ratios: list[float] = []
    call_counts: list[int] = []
    for path in files:
        read = created = calls = 0
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    usage = (entry.get("message") or {}).get("usage")
                    if not isinstance(usage, dict):
                        continue
                    calls += 1
                    read += usage.get("cache_read_input_tokens") or 0
                    created += usage.get("cache_creation_input_tokens") or 0
        except OSError:
            continue
        if created > 0 and calls:
            ratios.append(read / created)
            call_counts.append(calls)
    if not ratios:
        return Amplification()
    ratios.sort()
    call_counts.sort()
    mid = len(ratios) // 2
    return Amplification(
        ratio=ratios[mid], calls=call_counts[len(call_counts) // 2], sessions=len(ratios)
    )


def claude_md_block_chars(repo_root: Path) -> int:
    """Characters of ``CLAUDE.md`` that repowise wrote, or 0.

    Only our own block is charged. The rest of the file is the project's and
    would be there whether or not repowise was installed.
    """
    for candidate in (repo_root / ".claude" / "CLAUDE.md", repo_root / "CLAUDE.md"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        start = text.find(_BLOCK_HEADING)
        if start == -1:
            continue
        rest = text[start + len(_BLOCK_HEADING) :]
        end = rest.find("\n## ")
        return len(_BLOCK_HEADING) + (len(rest) if end == -1 else end)
    return 0


def measure(
    repo_root: Path,
    *,
    advisory_chars: int = 0,
    advisory_firings: int = 0,
    mcp_schema_chars: int = 0,
    projects_root: Path | None = None,
) -> Footprint:
    """Everything repowise cost this repo that can be counted from local data.

    *advisory_chars* and *advisory_firings* come from the hook ledger, which
    records the characters of every emission it made. *mcp_schema_chars* is
    zero unless the caller knows the harness loaded the schema rather than
    deferring it — Claude Code defers, so guessing would invent a large debit.
    """
    amp = measure_amplification(repo_root, projects_root=projects_root)
    multiplier = amp.ratio if amp.known else 1.0
    footprint = Footprint(amplification=amp)

    resident_note = (
        f"resident in the prompt prefix, re-read {amp.ratio:.1f}x per session "
        f"(median {amp.calls} API calls)"
        if amp.known
        else "resident in the prompt prefix; no cache figures on disk, so billed = raw"
    )

    block_chars = claude_md_block_chars(repo_root)
    if block_chars:
        raw = block_chars // _CHARS_PER_TOKEN
        footprint.debits.append(
            Debit(
                label="CLAUDE.md repowise block",
                raw_tokens=raw,
                billed_tokens=int(raw * multiplier),
                detail=f"{block_chars:,} chars, {resident_note}",
            )
        )
    if mcp_schema_chars:
        raw = mcp_schema_chars // _CHARS_PER_TOKEN
        footprint.debits.append(
            Debit(
                label="MCP tool schema",
                raw_tokens=raw,
                billed_tokens=int(raw * multiplier),
                detail=f"{mcp_schema_chars:,} chars, {resident_note}",
            )
        )
    else:
        footprint.unmeasured.append(
            "the MCP tool schema, which is only resident when the harness does "
            "not defer it (Claude Code defers)"
        )
    if advisory_chars:
        raw = advisory_chars // _CHARS_PER_TOKEN
        footprint.debits.append(
            Debit(
                label="advisory injections",
                raw_tokens=raw,
                billed_tokens=int(raw * multiplier),
                detail=f"{advisory_firings:,} firings, {advisory_chars:,} chars",
            )
        )

    footprint.unmeasured.append(
        "tool uses our surfaces proposed that the user or a policy then "
        "rejected, which needs a transcript pass this does not do"
    )
    return footprint
