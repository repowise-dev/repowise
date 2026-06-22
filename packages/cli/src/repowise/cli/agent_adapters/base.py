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

    __slots__ = ("command", "cwd", "shell")

    def __init__(self, command: str, cwd: str, shell: str = "posix") -> None:
        self.command = command
        self.cwd = cwd
        #: ``"posix"`` or ``"powershell"`` — which shell dialect the agent
        #: will run the command under. PowerShell commands get extra
        #: classifier bailouts (PS aliases like ``ls`` don't survive a
        #: subprocess wrap through the system shell).
        self.shell = shell


class RewriteResult:
    """A decision to rewrite the command before the agent executes it."""

    __slots__ = ("command", "permission", "reason")

    def __init__(self, command: str, permission: str, reason: str) -> None:
        #: The replacement command (e.g. ``repowise distill pytest -x``).
        self.command = command
        #: ``"ask"`` surfaces the rewritten command for user approval;
        #: ``"allow"`` executes it without a prompt (the default — see the
        #: ``rewrite_hook`` module docstring for why auto-allowing a
        #: bailout-filtered ``repowise distill`` wrap is not an escalation).
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

    #: Permission postures this agent's hook protocol can actually honor for
    #: a rewritten command. Claude Code supports ask-with-mutation; an agent
    #: that can only allow-with-mutation (Codex) narrows this to
    #: ``{"allow"}`` and the hook passes ``ask`` decisions through untouched
    #: rather than silently escalating them to an unprompted rewrite.
    rewrite_permissions: ClassVar[frozenset[str]] = frozenset({"ask", "allow"})

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
