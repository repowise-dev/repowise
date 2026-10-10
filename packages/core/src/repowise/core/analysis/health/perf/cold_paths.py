"""Code that runs once per deploy, boot or incident, read off its name.

A loop in a schema migration, a startup or shutdown step, or crash recovery
repeats for real, but nobody waits on it per request, so batching it is not
work to schedule. :func:`~.actionability.expected_reason` makes a group whose
every loop owner is cold ``expected`` with reason ``cold_path``.

Names and file stems only, compared as whole words. Ceiling: a helper whose
only caller is a migration (``_rebuild_drifted_tables``) and a scheduled job
carry no such word; telling them apart needs execution roles propagated over
the call graph. Known false positives: request code in a module named for a
phase (``startup.py``) whose function name carries no request word, and a
per-request function whose name leads with ``migrate`` beside no user or
request word. Not an identity input: ``execution_context`` stays path-only,
so no opportunity id moves.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

_WORD_BREAK = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

# A module named for its phase: ``workspace_startup``, ``session_recovery``.
# A stem the word leads (``startup-trace-segment``) is about the phase, and
# may run at any time.
_STEM_PHASES = frozenset({"startup", "shutdown", "migration", "migrations", "recovery"})
# A function named for its phase leads or ends with it: ``on_shutdown``,
# ``_migrate_add_optional_columns``, ``cmd_migrate``. ``apply_migration_step``
# is a helper anything may call.
_NAME_PHASES = frozenset({"startup", "shutdown", "migrate"})
# ``startPluginServices``: the verb leads, a long-lived process is the object,
# and a qualifier names it. A bare ``start_server`` is as often a per-job helper.
_LIFECYCLE_VERBS = frozenset({"start", "stop"})
_LIFECYCLE_OBJECTS = frozenset({"service", "services", "server", "servers", "daemon", "daemons"})
_RECOVERY_WORDS = frozenset({"recover", "recovery"})
_CRASH_WORDS = frozenset({"interrupted", "orphaned", "abandoned", "crashed", "aborted"})
# Per-user or per-request work: beside one of these a phase word is a request
# (``recover_password``, ``migrate_user_settings``, ``recover_aborted_upload``).
_WARM_WORDS = frozenset(
    {
        "password",
        "passwd",
        "account",
        "login",
        "credential",
        "credentials",
        "user",
        "users",
        "request",
        "requests",
        "middleware",
        "handler",
        "route",
        "endpoint",
        "upload",
        "download",
    }
)


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD_BREAK.split(text) if word]


def is_cold_path(file_path: str, function_name: Any) -> bool:
    """Whether this function's name or its module's says it runs once.

    Called per member by ``opportunities._assemble``: a group is cold only
    when every loop owner is.
    """
    name = str(function_name or "").replace("::", ".").rsplit(".", 1)[-1]
    words = _words(name)
    stem = _words(PurePosixPath(file_path.replace("\\", "/")).stem)
    found = set(words)
    if (found | set(stem)) & _WARM_WORDS:
        return False
    if stem and stem[-1] in _STEM_PHASES:
        return True
    if not words:
        return False
    if words[0] in _NAME_PHASES or words[-1] in _NAME_PHASES:
        return True
    if len(words) >= 3 and words[0] in _LIFECYCLE_VERBS and words[-1] in _LIFECYCLE_OBJECTS:
        return True
    return bool(found & _RECOVERY_WORDS and found & _CRASH_WORDS)


__all__ = ["is_cold_path"]
