"""Anonymous install identity for the hosted platform.

The anonymous id is a random UUIDv4 generated once and persisted in
``~/.repowise/platform.json``. It exists only to count distinct installs and
group a single install's events; it is **not** derived from any machine
identifier (hostname, username, MAC), which keeps it impossible to reverse to a
person. A per-process session id groups the commands of one CLI invocation.
"""

from __future__ import annotations

import uuid

from repowise.cli.platform import store

_ANON_ID_KEY = "anon_id"

#: Generated once per process. Groups the events emitted by a single CLI run.
_SESSION_ID = uuid.uuid4().hex


def get_anonymous_id() -> str:
    """Return the persistent anonymous install id, creating it on first use."""
    state = store.load()
    anon = state.get(_ANON_ID_KEY)
    if isinstance(anon, str) and anon:
        return anon
    anon = uuid.uuid4().hex
    store.update(**{_ANON_ID_KEY: anon})
    return anon


def get_session_id() -> str:
    """Return this process's ephemeral session id."""
    return _SESSION_ID
