"""AgentAdapter interface and the agent-agnostic rewrite request/result types.

Deliberately frugal imports: no ``dataclasses`` (pulls ``inspect``), no
``pathlib`` — this module sits on the PreToolUse hot path where every
millisecond of interpreter startup counts. The request/result types are
plain ``__slots__`` classes for the same reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path


class RewriteRequest:
    """Agent-agnostic view of one shell command an agent is about to run."""

    __slots__ = ("command", "cwd")

    def __init__(self, command: str, cwd: str) -> None:
        self.command = command
        self.cwd = cwd


class RewriteResult:
    """A decision to rewrite the command before the agent executes it."""

    __slots__ = ("command", "permission", "reason")

    def __init__(self, command: str, permission: str, reason: str) -> None:
        #: The replacement command (e.g. ``repowise distill pytest -x``).
        self.command = command
        #: ``"ask"`` surfaces the rewritten command for user approval;
        #: ``"allow"`` executes it without a prompt. Never auto-allow unless
        #: the user opted in per command family — a silently mutated command
        #: is a permission escalation.
        self.permission = permission
        #: One-line human explanation shown in the agent's permission UI.
        self.reason = reason


class AgentAdapter(ABC):
    """Everything agent-specific about the command-rewrite hook.

    Implementations translate between one agent's hook protocol and the
    agent-agnostic :class:`RewriteRequest`/:class:`RewriteResult` pair, and
    own that agent's hook install/uninstall. The classification logic in
    :mod:`repowise.cli.rewrite_hook` never sees a hook payload.
    """

    #: Stable adapter identifier (e.g. ``"claude-code"``).
    name: ClassVar[str]

    @abstractmethod
    def detect(self) -> bool:
        """True when this agent appears to be installed for the current user."""

    @abstractmethod
    def parse_hook_payload(self, raw: str) -> RewriteRequest | None:
        """Parse the agent's hook stdin into a request, or None to pass through.

        Must never raise on malformed input — a broken payload is a
        passthrough, not an error.
        """

    @abstractmethod
    def render_response(self, result: RewriteResult) -> str:
        """Render *result* as the agent's hook stdout protocol."""

    @abstractmethod
    def install_rewrite_hook(self) -> Path | None:
        """Register the rewrite hook with this agent; returns the config path."""

    @abstractmethod
    def uninstall_rewrite_hook(self) -> bool:
        """Remove the rewrite hook; True when something was removed."""

    @abstractmethod
    def rewrite_hook_installed(self) -> bool:
        """True when the rewrite hook is currently registered."""
