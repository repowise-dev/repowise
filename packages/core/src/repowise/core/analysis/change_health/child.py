"""Run a committed change's health comparison in a child process.

The comparison is CPU-bound Python. In a thread of a server that also selects
tests or answers other calls, it holds the GIL against them, so the work adds
up instead of overlapping. A child interpreter runs it on its own core.

The child is a plain subprocess with its own stdin and stdout pipes, never a
``multiprocessing`` worker: a spawned worker reads the parent's standard
handles at startup, and on the MCP stdio transport those are the JSON-RPC
pipe, where a Windows child blocks behind the server's pending read.
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import threading
from pathlib import Path

import structlog

from .models import ChangeHealthDelta
from .service import ChangeHealthDeltaService, DeltaRequest
from .sources import GitRevisionSource, RevisionPair

log = structlog.get_logger(__name__)

#: Changed files below this compare in process. Starting a child costs about
#: 2-3 s of imports, roughly what a thread spends comparing eight files.
CHILD_MIN_FILES = 8

#: A child still running after this is abandoned and the thread compares.
CHILD_TIMEOUT_SECONDS = 180

#: Children running at once; a comparison past this many runs in its thread.
CHILD_LIMIT = 2
_CHILD_SLOTS = threading.BoundedSemaphore(CHILD_LIMIT)

#: CREATE_NO_WINDOW: no console window flashes up for the child on Windows.
_WINDOWS_FLAGS = 0x08000000

_ENTRY = "from repowise.core.analysis.change_health.child import main; main()"


class ChildProcessDeltaService(ChangeHealthDeltaService):
    """A :class:`ChangeHealthDeltaService` that compares large committed changes in a child.

    Uncommitted work and small changes compare in process: the base-side cache
    serves repeated reviews of the same edits, and a small comparison costs
    less than starting a child. A child that fails falls back to the thread.
    The child builds the default analyzer from *repo_path*, so a service given
    its own analyzer or rules compares in process too.
    """

    def _compare_uncached(self, request: DeltaRequest, pair: RevisionPair) -> ChangeHealthDelta:
        if not self._wants_child(pair) or not _CHILD_SLOTS.acquire(blocking=False):
            return super()._compare_uncached(request, pair)
        try:
            return _run_child(self.repo_path, self.rules_fingerprint, self.cache_dir, request, pair)
        except Exception as exc:  # failed to start, crashed, timed out, or bad output
            log.warning("change_health_child_failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            _CHILD_SLOTS.release()
        return super()._compare_uncached(request, pair)

    def _wants_child(self, pair: RevisionPair) -> bool:
        return not (
            pair.working_tree
            or len(pair.changes) < CHILD_MIN_FILES
            or self._custom_analyzer
            or self.health_config is not None
            or not isinstance(self.source, GitRevisionSource)
        )


def _run_child(
    repo_path: str | None,
    rules_fingerprint: str,
    cache_dir: Path | None,
    request: DeltaRequest,
    pair: RevisionPair,
) -> ChangeHealthDelta:
    proc = subprocess.run(
        # -P: the server's working directory is not put on the child's sys.path.
        [sys.executable, "-P", "-c", _ENTRY],
        input=pickle.dumps((repo_path, rules_fingerprint, cache_dir, request, pair)),
        capture_output=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=True,
        creationflags=_WINDOWS_FLAGS if os.name == "nt" else 0,
    )
    # The bytes of a child this process just started, over its own pipe.
    result = pickle.loads(proc.stdout)  # repowise-security-ignore: pickle_loads
    if not isinstance(result, ChangeHealthDelta):
        raise TypeError(f"child returned {type(result).__name__}")
    return result


def main() -> None:
    """Child entry: compare the pickled request on stdin, pickle the result to stdout."""
    # Only the result goes to stdout. Prints, logs and inherited handles go to stderr.
    result_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    from repowise.core.worker_logging import quiet_worker_logs

    quiet_worker_logs()
    # Written by the parent that started this process, over its own pipe.
    payload = pickle.load(sys.stdin.buffer)  # repowise-security-ignore: pickle_loads
    repo_path, rules_fingerprint, cache_dir, request, pair = payload
    service = ChangeHealthDeltaService(
        repo_path=repo_path, rules_fingerprint=rules_fingerprint, cache_dir=cache_dir or False
    )
    result = service.compare(request, pair=pair)
    with result_out:
        pickle.dump(result, result_out, protocol=pickle.HIGHEST_PROTOCOL)
