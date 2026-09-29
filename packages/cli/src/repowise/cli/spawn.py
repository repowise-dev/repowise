"""Start a process that outlives this one. Stdlib only: hooks import it."""

from __future__ import annotations

import os
import subprocess
from typing import IO

#: DETACHED_PROCESS | CREATE_NO_WINDOW: no console window flashes up in front
#: of the user. CREATE_BREAKAWAY_FROM_JOB is deliberately absent: under a job
#: that forbids breakaway, CreateProcess then fails outright, and a child kept
#: in the job only dies with it, which the callers here tolerate.
_WINDOWS_FLAGS = 0x00000008 | 0x08000000


def spawn_detached(argv: list[str], cwd: str, stdout: IO[bytes] | int | None = None) -> None:
    """Run *argv* in *cwd* in its own session or console, never waited on.

    No stdin; stdout and stderr go to *stdout* (a file opened by the caller)
    or nowhere, so the child can never write into the parent's terminal.
    """
    sink = subprocess.DEVNULL if stdout is None else stdout
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": sink,
        "stderr": sink,
        "close_fds": True,
        "cwd": cwd,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _WINDOWS_FLAGS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)  # type: ignore[call-overload]
