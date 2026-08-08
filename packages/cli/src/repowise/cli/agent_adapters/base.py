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


class RewriteHookStatus:
    """What an installed rewrite hook will actually do.

    ``installed`` keys on the hook *command*, which is what makes an entry
    ours. ``unmatched`` answers the separate question the command cannot: of
    the tool names this agent runs commands with, which does the entry's
    matcher fail to select? An entry left behind by an upstream tool rename is
    registered and fires on some or none of them, and all three states read as
    "installed" until they are reported apart.
    """

    __slots__ = ("fires", "installed", "matcher", "unmatched")

    def __init__(
        self,
        installed: bool,
        matcher: str | None,
        unmatched: tuple[str, ...] = (),
        fires: bool = False,
    ) -> None:
        self.installed = installed
        #: The matcher as written in the agent's config; ``""`` when the entry
        #: carries none, which every agent here reads as match-all.
        self.matcher = matcher
        #: Shell tool names, sorted, that this matcher provably does not
        #: select. Empty when it selects all of them *and* when there is
        #: nothing to check against — an unknowable case must not read as a
        #: broken hook, because claiming a working hook is dead is the worse
        #: error of the two.
        self.unmatched = unmatched
        #: True unless the matcher selects *none* of them, which is a hook
        #: that runs for nothing at all. A matcher that selects some but not
        #: all still fires, and still misses ``unmatched``. Derived when
        #: nothing is unmatched, so a caller cannot accidentally construct an
        #: installed hook that misses nothing and claims not to fire.
        self.fires = fires or (installed and not unmatched)


#: Characters that keep a matcher in the plain-list dialect. Anything outside
#: this set makes the agent read the matcher as a regular expression.
_PLAIN_MATCHER_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_- ,|"
)


def unmatched_tool_names(matcher: str | None, names: frozenset[str]) -> tuple[str, ...]:
    """Which of *names* the matcher does not select, sorted.

    Two dialects, and getting the split wrong is the one failure that matters
    here — reporting a working hook dead is worse than missing a dead one, so
    every uncertain case resolves to "selected".

    A matcher of letters, digits, ``_``, ``-``, spaces, commas and ``|`` is a
    plain list of tool names, matched exactly. Anything else is a regular
    expression, and an **unanchored** one: ``Shell$`` selects ``PowerShell``.
    Anchoring it here would call that matcher inert while the agent happily
    fires on it. A pattern that will not compile falls back to the list
    reading. An absent matcher is match-all, and an agent that declares no
    tool names is no evidence at all; both answer with nothing missing.
    """
    import re

    if not matcher or not names:
        return ()
    if set(matcher) <= _PLAIN_MATCHER_CHARS:
        listed = {part.strip() for part in matcher.replace(",", "|").split("|")}
        return tuple(sorted(n for n in names if n not in listed))
    try:
        pattern = re.compile(matcher)
    except re.error:
        return tuple(sorted(n for n in names if n != matcher.strip()))
    return tuple(sorted(n for n in names if not pattern.search(n)))


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

    #: Tool names this agent uses to run a shell command. The installed hook's
    #: matcher is derived from this set and checked back against it, so an
    #: upstream rename cannot leave a matcher and a gate disagreeing in
    #: silence. Empty means the adapter declines to say.
    shell_tool_names: ClassVar[frozenset[str]] = frozenset()

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

    def rewrite_hook_matcher(self) -> str | None:
        """The installed entry's tool matcher, or None when not installed.

        ``""`` means the entry carries no matcher. An adapter that cannot
        report one leaves this at None and gets the presence answer, which is
        what this method refines and is never worse than.
        """
        return None

    def rewrite_hook_status(self) -> RewriteHookStatus:
        """Whether the rewrite hook is registered *and* still points at a tool.

        One config read, not two: an adapter that can report a matcher answers
        presence with the same lookup, so the two can never disagree about a
        file being rewritten underneath them. Only an adapter that cannot
        report one falls back to the separate presence check.
        """
        matcher = self.rewrite_hook_matcher()
        if matcher is None:
            if not self.rewrite_hook_installed():
                return RewriteHookStatus(False, None)
            matcher = ""  # installed, but this adapter cannot say on what
        unmatched = unmatched_tool_names(matcher, self.shell_tool_names)
        fires = not self.shell_tool_names or len(unmatched) < len(self.shell_tool_names)
        return RewriteHookStatus(True, matcher, unmatched, fires)
