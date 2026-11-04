"""Notice when repowise's own code changes on disk under a running server.

A long-lived MCP server keeps the modules it imported at start. After a
``git pull`` in an editable install or a ``pip install -U``, a module it
imports later comes from the new code and can disagree with an old one
already in memory, so a call fails in ways no index rebuild fixes, or worse,
answers from code that no longer exists. The fix is a restart; this module
tells the agent so.

The signal is the stat (mtime, size) of every loaded ``repowise`` module
file, recorded when the server starts watching and compared by a background
poll. A tool call only reads the result, so the check costs a call nothing.
Modules imported later join the baseline when a poll first sees them; a file
edited between its import and that poll is missed, which is the ceiling of a
stat-only check (hashing every file would close it at many times the cost).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import types
from pathlib import Path

logger = logging.getLogger(__name__)

#: What mixed old and new modules raise when one asks the other for a name.
MIXED_CODE_ERRORS = (AttributeError, ImportError, NameError)

#: The one wording for a server whose loaded code predates the install.
SERVER_STALE_LINE = (
    "This MCP server runs older repowise code than the install on disk; "
    "restarting the server or the editor session loads the new code."
)

# A poll stats every loaded module (about 1,100 files, ~20 ms on Windows) off
# the event loop. A failing call checks at once, so this bounds only how soon
# a quiet response starts carrying the line.
_POLL_S = 15.0

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)

_baseline: dict[str, tuple[int, int] | None] = {}
_started_version: str | None = None
# The line responses carry, set once when a poll or a failing call sees drift.
_line: str | None = None


def _loaded_files() -> list[str]:
    files = []
    for name, module in list(sys.modules.items()):
        if not isinstance(module, types.ModuleType):
            continue
        if name != "repowise" and not name.startswith("repowise."):
            continue
        # vars(), not getattr(): a module-level __getattr__ may import.
        path = vars(module).get("__file__")
        if path:
            files.append(path)
    return files


def _stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


#: Where ``repowise.server.__version__`` is defined.
_VERSION_FILE = Path(__file__).resolve().parents[1] / "__init__.py"


def _read_version() -> str | None:
    try:
        match = _VERSION_RE.search(_VERSION_FILE.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def start() -> None:
    """Record the baseline. Idempotent per process: the first start wins."""
    global _started_version
    if _baseline:
        return
    _started_version = _read_version()
    scan()


def scan() -> bool:
    """Compare loaded module files to their baseline; True once any changed."""
    global _line
    if _line is not None:
        return True
    for path in _loaded_files():
        now = _stat(path)
        if path not in _baseline:
            _baseline[path] = now
        elif _baseline[path] != now:
            logger.warning("repowise MCP: %s changed on disk since the server started", path)
            _line = _notice()
            return True
    return False


def check_now() -> bool:
    """Scan at once, for a failing call. False when nothing is being watched."""
    return bool(_baseline) and scan()


def stale_line() -> str | None:
    """The restart line once drift is seen, else ``None``. A read, no I/O."""
    return _line


def _notice() -> str:
    """:data:`SERVER_STALE_LINE`, naming both versions when they differ."""
    installed = _read_version()
    if installed and _started_version and installed != _started_version:
        return f"{SERVER_STALE_LINE} Loaded {_started_version}, installed {installed}."
    return SERVER_STALE_LINE


async def watch() -> None:
    """Background poll for the life of the server; stops once drift is seen."""
    try:
        await asyncio.to_thread(start)
        while not await asyncio.to_thread(scan):
            await asyncio.sleep(_POLL_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("repowise MCP: code drift watch stopped", exc_info=True)

