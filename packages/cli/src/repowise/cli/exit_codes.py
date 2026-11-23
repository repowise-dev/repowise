"""Exit codes a script or agent can branch on.

0 is ready. Any code not named here is a failure: nothing usable was produced.
"""

from __future__ import annotations

#: Finished and usable, but something the run was asked for did not complete.
EXIT_DEGRADED = 2
#: Read or done, and the next step is a person's (sign in, install, approve,
#: pay): an agent hands it over instead of retrying.
EXIT_NEEDS_HUMAN = 3
#: Stopped by Ctrl-C; the shell convention for SIGINT (128 + 2).
EXIT_INTERRUPTED = 130

EXIT_CODES_HELP = (
    "Exit codes: 0 ready, 2 finished with gaps (named on the last line), 130 interrupted."
)
