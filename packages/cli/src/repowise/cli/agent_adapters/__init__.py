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

__all__ = ["AgentAdapter", "RewriteRequest", "RewriteResult"]
