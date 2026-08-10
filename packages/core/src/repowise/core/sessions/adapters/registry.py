"""Which transcript adapter a consumer gets, by name.

Governs the transcript-reading adapters in this package
(:class:`~repowise.core.sessions.adapters.base.HarnessAdapter`) — the ones
that turn a session file into an Event stream. Not to be confused with the
similarly named ``repowise.cli.agent_adapters`` family, which installs and
checks editor hooks and shares nothing with this one but a word.

Claude Code and Codex today. The point is not extensibility for its own
sake: it is that a second harness becomes a registration in its own module
rather than a search-and-replace across every module that reads transcripts.

Lookup happens per call, never at import. Adapters may hold per-file state
(see :meth:`~repowise.core.sessions.adapters.base.HarnessAdapter.begin_file`),
so a shared module-level instance would be a bug the day one does; and a
module-level construction is weight on the hook import path, which has been
fought down once already.
"""

from __future__ import annotations

from repowise.core.sessions.adapters.base import HarnessAdapter

#: Registered adapter classes, keyed by their ``name``.
_ADAPTERS: dict[str, type[HarnessAdapter]] = {}

#: The harness assumed when a caller names none.
DEFAULT_ADAPTER = "claude_code"


def register_adapter(adapter_cls: type[HarnessAdapter]) -> type[HarnessAdapter]:
    """Register *adapter_cls* under its ``name``. Returns it, so it decorates."""
    _ADAPTERS[adapter_cls.name] = adapter_cls
    return adapter_cls


def get_adapter(name: str | None = None) -> HarnessAdapter:
    """A fresh adapter instance for *name*, defaulting to Claude Code."""
    key = name or DEFAULT_ADAPTER
    try:
        adapter_cls = _ADAPTERS[key]
    except KeyError:
        raise LookupError(f"no transcript adapter registered for {key!r}") from None
    return adapter_cls()


def registered_adapters() -> list[str]:
    """Names of every registered harness, sorted."""
    return sorted(_ADAPTERS)
