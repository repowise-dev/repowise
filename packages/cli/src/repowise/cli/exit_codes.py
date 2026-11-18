"""Exit codes a script or agent can branch on.

0 is ready. Any code not named here is a failure: nothing usable was produced.
"""

from __future__ import annotations

#: Finished and usable, but something the run was asked for did not complete.
EXIT_DEGRADED = 2
#: Stopped by Ctrl-C; the shell convention for SIGINT (128 + 2).
EXIT_INTERRUPTED = 130

EXIT_CODES_HELP = (
    "Exit codes: 0 ready, 2 finished with gaps (named on the last line), 130 interrupted."
)
