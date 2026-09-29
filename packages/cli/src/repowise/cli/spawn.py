"""Start a process that outlives this one. Stdlib only: hooks import it."""

from __future__ import annotations

import os
import subprocess
from typing import IO

#: DETACHED_PROCESS | CREATE_NO_WINDOW: no console window flashes up in front
#: of the user.
_WINDOWS_FLAGS = 0x00000008 | 0x08000000
#: Leave the parent's job, so a child outlives a host that closes its job on
#: exit. A job that forbids breakaway fails CreateProcess with access denied,
#: and the spawn is retried inside the job.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


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
    if os.name != "nt":
        subprocess.Popen(argv, start_new_session=True, **kwargs)  # type: ignore[call-overload]
        return
    try:
        subprocess.Popen(  # type: ignore[call-overload]
            argv, creationflags=_WINDOWS_FLAGS | _CREATE_BREAKAWAY_FROM_JOB, **kwargs
        )
    except PermissionError:
        subprocess.Popen(argv, creationflags=_WINDOWS_FLAGS, **kwargs)  # type: ignore[call-overload]
