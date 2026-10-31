"""What reading a file would have cost the agent making this call.

The one place an MCP counterfactual sizes a Read. A tool that stands in for
reading a file credits what one rangeless Read of it returns (the first
2,000 lines), on the same ``floor(chars / 4)`` scale as everything else, with
Claude Code's line-number gutter when Claude Code is the caller. Other hosts
read without one, so they get bare content.
"""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import READ_DEFAULT_LINES, estimate_read_tokens, rangeless_read

from . import interaction

#: The only caller whose Read adds a gutter, as its agent registry slug.
_NUMBERED_READ_AGENT = "claude_code"


def _numbered() -> bool:
    live = interaction.current()
    return live is not None and live.agent == _NUMBERED_READ_AGENT


def read_tokens(text: str) -> int:
    """Tokens one rangeless Read of a file holding *text* costs the calling agent."""
    return estimate_read_tokens(rangeless_read(text), numbered=_numbered())


def file_read_tokens(root: Any, path: str) -> int | None:
    """:func:`read_tokens` for a repo file, or None when it cannot be read as text.

    Reads only the lines a rangeless Read returns. Refuses a path outside
    *root*: an index row or a citation is not a trust boundary.
    """
    try:
        base = Path(str(root)).resolve()
        abs_path = (base / path).resolve()
        abs_path.relative_to(base)
        with abs_path.open(encoding="utf-8", errors="replace") as handle:
            text = "".join(islice(handle, READ_DEFAULT_LINES))
    except (OSError, ValueError):
        return None
    if "\x00" in text:
        return None
    return read_tokens(text) or None
