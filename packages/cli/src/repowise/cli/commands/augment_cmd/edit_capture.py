"""Edit-time AI attribution: record an agent's edit as line hashes, when opted in.

The consent gate runs before the payload is parsed for edits, and it is cheap
when capture is off (see :func:`repowise.core.edit_capture.store.capture_level`),
because this runs on every hooked edit of every user. Nothing is emitted to the
agent; the event lands in ``.repowise/attribution/events.jsonl`` and
``repowise update`` binds it to a commit.
"""

from __future__ import annotations

from pathlib import Path

from ._shared import _find_repo_root


def _payload_cwd(payload: dict) -> str:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    roots = payload.get("workspace_roots")
    if isinstance(roots, list):
        return next((r for r in roots if isinstance(r, str) and r), "")
    return ""


def capture_edit(hook_adapter: str, payload: dict) -> int:
    """Record *payload*'s edits for the agent behind *hook_adapter*.

    Returns the number of events written (0 when capture is off for it, or
    when the repo or sidecar cannot be read or written). Anything else is a
    bug and reaches the hook's top-level guard.
    """
    try:
        from repowise.core.agents.identity import get_identity, slug_for_hook_adapter

        identity = get_identity(slug_for_hook_adapter(hook_adapter))
        if identity is None or identity.capture_adapter is None:
            return 0
        cwd = _payload_cwd(payload)
        repo_root = _find_repo_root(Path(cwd)) if cwd else None
        if repo_root is None:
            return 0
        from repowise.core.edit_capture.store import capture_level, record_call

        if capture_level(repo_root, identity.slug) == "off":
            return 0
        from repowise.core.edit_capture.edits import CAPTURE_ADAPTERS

        call = CAPTURE_ADAPTERS[identity.capture_adapter](payload)
        return record_call(repo_root, identity.slug, call) if call is not None else 0
    except (OSError, RuntimeError, ValueError):
        return 0
