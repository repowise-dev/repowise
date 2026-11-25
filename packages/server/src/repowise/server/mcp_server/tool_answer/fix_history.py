"""The bug-fix record of a file the question asks why it keeps changing or breaking.

"Why so many fixes in call_resolver.py" is a question about history, which no
page or code excerpt holds. The index already counts each file's fixes, their
recency and the symbols they landed in (what ``get_risk`` reports as the
defect profile), so a question of that shape gets those facts as evidence
instead of a second call. Every other question is untouched.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, or_, select

from repowise.core.persistence.models import GitMetadata
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._query_shape import path_tokens
from repowise.server.mcp_server.tool_risk.history import _defect_profile, _fix_clause

# Words that ask about defects, whatever else the question asks.
_DEFECT_WORDS_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|bugs?|buggy|break(?:s|ing|age|ages)?|broken?|regress\w*|"
    r"churn\w*|flak\w*|fragile|unstable)\b",
    re.IGNORECASE,
)
# Words that ask about change only together with a frequency: "why does x.py
# change the config" asks about code, "why does x.py change so often" history.
_CHANGE_WORDS_RE = re.compile(r"\bchang(?:e|es|ed|ing)\b", re.IGNORECASE)
_FREQUENCY_RE = re.compile(
    r"\b(?:often|many|much|frequent\w*|constant\w*|repeated\w*|keeps?|always)\b",
    re.IGNORECASE,
)
_WHY_OR_FREQUENCY_RE = re.compile(rf"\bwhy\b|{_FREQUENCY_RE.pattern}", re.IGNORECASE)
# Files a question names, at most. One is the usual case; two covers "a.py or b.py".
_MAX_FILES = 2


def asks_fix_history(question: str) -> bool:
    """Whether *question* asks why or how often something breaks or changes."""
    if _DEFECT_WORDS_RE.search(question):
        return bool(_WHY_OR_FREQUENCY_RE.search(question))
    return bool(_CHANGE_WORDS_RE.search(question) and _FREQUENCY_RE.search(question))


async def named_file_fix_history(
    session, repo_id: str, question: str, exclude_spec: Any = None
) -> list[dict]:
    """``{path, fix_count, window, last_fix_days_ago, bug_magnet, top_symbols}``
    for each file *question* names, when it asks about fix history.

    A bare file name counts only when one indexed file carries it. Files with
    no counted fix are left out: no row says nothing was fixed, which is what
    an index without the fix rollup would say too.
    """
    if not asks_fix_history(question):
        return []
    tokens = [t.replace("\\", "/").removeprefix("./") for t in path_tokens(question)]
    named = await _named_rows(session, repo_id, tokens, exclude_spec) if tokens else []
    if not named:
        return []
    # Ages count back from the indexed head, as get_risk counts them.
    as_of = (
        await session.execute(
            select(func.max(GitMetadata.last_commit_at)).where(
                GitMetadata.repository_id == repo_id
            )
        )
    ).scalar()
    out = []
    for meta in named[:_MAX_FILES]:
        profile = _defect_profile(meta, now=as_of)
        if profile:
            out.append({"path": meta.file_path, **profile})
    return out


async def _named_rows(session, repo_id: str, tokens: list[str], exclude_spec: Any) -> list:
    """The ``GitMetadata`` row each token names: its path, or a file name only
    one indexed file carries. In token order, each row once."""
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            or_(
                *(
                    or_(
                        GitMetadata.file_path == t,
                        GitMetadata.file_path.like(f"%/{escape_like(t)}", escape=LIKE_ESCAPE),
                    )
                    for t in tokens
                )
            ),
        )
    )
    rows = [r for r in res.scalars().all() if not is_excluded(r.file_path, exclude_spec)]
    named: dict[str, Any] = {}
    for token in tokens:
        matches = [r for r in rows if r.file_path == token or r.file_path.endswith(f"/{token}")]
        if len(matches) == 1:
            named.setdefault(matches[0].file_path, matches[0])
    return list(named.values())


def fix_history_lines(history: list[dict]) -> list[str]:
    """One prompt line per file: the same clause get_risk's summary leads with."""
    lines = []
    for row in history:
        clause = _fix_clause(row).rstrip(", ") or (
            f"{row['fix_count']} bug fixes in {row['window']}"
        )
        top = row.get("top_symbols") or {}
        symbols = ", ".join(f"{name} ({n})" for name, n in top.items())
        lines.append(
            f"Bug-fix history of {row['path']}: {clause}"
            + (f"; fixes landed most in {symbols}" if symbols else "")
        )
    return lines
