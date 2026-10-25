"""Every gated extraction prompt asks for ``decision`` as a verbatim quote.

The substring gate blanks any field the source does not contain, so a prompt
that invites a reworded decision stores the record with no decision text.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.decisions.prompts import (
    COMMENT_ARCHAEOLOGY_PROMPT,
    GIT_ARCHAEOLOGY_PROMPT,
    INLINE_MARKER_PROMPT,
    PR_BODY_MINING_PROMPT,
    README_MINING_PROMPT,
)


@pytest.mark.parametrize(
    "prompt",
    [
        INLINE_MARKER_PROMPT,
        GIT_ARCHAEOLOGY_PROMPT,
        README_MINING_PROMPT,
        PR_BODY_MINING_PROMPT,
        COMMENT_ARCHAEOLOGY_PROMPT,
    ],
)
def test_the_decision_field_is_asked_for_verbatim(prompt: str) -> None:
    line = next(ln for ln in prompt.splitlines() if ln.strip().startswith('"decision"'))
    assert "copied verbatim" in line
