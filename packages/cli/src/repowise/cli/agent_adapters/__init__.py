"""Agent adapters — one module per AI coding agent that can rewrite tool input.

The distill engine is agent-agnostic: it sees commands and content, never
hook payloads. Everything an agent's hook protocol touches (payload parsing,
response rendering, hook install/uninstall) lives behind the
:class:`~repowise.cli.agent_adapters.base.AgentAdapter` interface, so
supporting a new agent is an additive module here — no engine or hook-script
refactors.

Hot-path discipline: these modules are imported by the ``repowise-rewrite``
PreToolUse hook, which must answer well under its timeout on every shell
command an agent runs. Module scope is stdlib-only; anything heavier
(settings writers, config helpers) is imported lazily inside methods.
"""

from repowise.cli.agent_adapters.base import AgentAdapter, RewriteRequest, RewriteResult

#: Every agent repowise knows how to hook, by the ``--client`` / ``--agent``
#: marker its hook config passes. Values are ``module:attribute`` rather than
#: classes so registration costs no import: the PostToolUse hook resolves one
#: adapter per invocation and must not pay for the others.
#:
#: **This mapping is the whole cost of adding a harness.** Payload parsing,
#: response rendering, install/uninstall and — since the capability class
#: variables landed on :class:`AgentAdapter` — which tools it calls and whether
#: it can replace a tool result all live in the adapter module. Nothing
#: downstream branches on an agent's name.
_REGISTRY: dict[str, str] = {
    "claude-code": "repowise.cli.agent_adapters.claude_code:ClaudeCodeAdapter",
    "codex": "repowise.cli.agent_adapters.codex:CodexAdapter",
}

#: What an unmarked payload is. Claude Code's hook config passes no marker,
#: because it was the only harness when the hooks were written.
DEFAULT_AGENT = "claude-code"


def adapter_for(client: str | None) -> AgentAdapter:
    """The adapter for *client*, falling back to Claude Code.

    An unknown marker resolves to the default rather than raising: a hook must
    never fail on a payload, and the default's capability answers are checked
    before anything is served, so the worst case is an enrichment declined.
    """
    import importlib

    target = _REGISTRY.get((client or "").strip().lower() or DEFAULT_AGENT)
    if target is None:
        target = _REGISTRY[DEFAULT_AGENT]
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)()


__all__ = [
    "DEFAULT_AGENT",
    "AgentAdapter",
    "RewriteRequest",
    "RewriteResult",
    "adapter_for",
]
