"""The last setup answer, in ``.repowise/setup.json``: stdlib only.

``analysis.actions`` computes whether setup is unfinished; every full read
(``repowise next``, ``status``, Do next, ``get_overview``, and one in the
background after an agent's first MCP call) stores the answer here, and
the readers that cannot afford that read (the SessionStart hook,
the MCP handshake) take it from here. It is the last answer, not a live one:
a step done since shows until the next full read.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: ``setup.offer`` while the finish-setup pass is offered.
FINISH_SETUP_OFFER = "finish_setup"

#: The last answer, under the checkout root.
STATE_FILE = Path(".repowise") / "setup.json"

#: The MCP instruction line while the pass is offered; dropped once it is not.
MCP_SETUP_LINE = (
    "Repowise setup is not finished. Offer once at a natural pause: run "
    "`repowise next --prompt setup` and follow it. If declined, run "
    "`repowise next --dismiss setup`."
)


def session_setup_line(steps: int) -> str:
    """The SessionStart line while the pass is offered."""
    noun = "step" if steps == 1 else "steps"
    return (
        f"Repowise setup is not finished: {steps} {noun}. `repowise next --prompt setup` "
        "prints how to finish it; offer once at a natural pause."
    )


def read_setup_state(root: str | Path) -> dict[str, Any]:
    """``.repowise/setup.json`` under *root*, or ``{}`` when absent or unreadable."""
    try:
        data = json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_setup_state(root: str | Path, **changes: Any) -> None:
    """Merge *changes* into ``.repowise/setup.json``; best-effort, atomic, never raises.

    Only an existing ``.repowise`` is written to, so a read never creates one.
    """
    folder = Path(root) / STATE_FILE.parent
    if not folder.is_dir():
        return
    import tempfile  # only writers pay for it; the hook only reads

    state = {**read_setup_state(root), **changes, "written_at": datetime.now(UTC).isoformat()}
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".setup-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp, folder / STATE_FILE.name)
    except (OSError, TypeError, ValueError):
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def record_setup(root: str | Path, block: Mapping[str, Any]) -> None:
    """Store what the hook and the handshake read from a full ``setup`` block.

    Written only when the answer changed, so a read that finds the same steps
    touches no file.
    """
    if not root:
        return
    answer = {
        "open": block.get("open", 0),
        "offer": block.get("offer"),
        "dismissed": bool(block.get("dismissed")),
        # Sticky: once setup reached zero steps, it stays completed.
        "completed": bool(block.get("completed")) or bool(read_setup_state(root).get("completed")),
    }
    last = read_setup_state(root)
    if any(last.get(k) != v for k, v in answer.items()) or not last:
        write_setup_state(root, **answer)


def offered_steps(root: str | Path | None) -> int:
    """Steps left while the pass is offered, from the last answer; 0 otherwise."""
    if not root:
        return 0
    state = read_setup_state(root)
    if state.get("offer") != FINISH_SETUP_OFFER or state.get("dismissed") or state.get("completed"):
        return 0
    left = state.get("open")
    return left if isinstance(left, int) and left > 0 else 0


__all__ = [
    "FINISH_SETUP_OFFER",
    "MCP_SETUP_LINE",
    "STATE_FILE",
    "offered_steps",
    "read_setup_state",
    "record_setup",
    "session_setup_line",
    "write_setup_state",
]
