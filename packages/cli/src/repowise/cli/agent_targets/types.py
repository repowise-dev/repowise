"""The descriptor every agent integration implements.

One :class:`AgentTarget` per agent repowise can wire up. The orchestrators
(``init``, ``update``, ``doctor``) drive targets through this Protocol and never
branch on an agent's name, so adding an agent is a descriptor module plus a
registry line rather than an edit to every caller.

Three shapes here are load-bearing and worth reading before implementing a
target:

**The tier is derived, never declared.** :func:`derive_tier` computes it from
what a descriptor actually wires — whether it names a hook adapter, whether it
names a transcript adapter, whether it writes anything at all. A target cannot
claim Full while its session adapter is missing, which is exactly the drift a
declared field would let through and the docs would then repeat.

**:meth:`AgentTarget.detect` returns a list, not a bool.** "Configured: yes" is
the wrong answer when the truth is "configured three times, one of them five
weeks old". Each :class:`Registration` carries the method, scope and config path
that produced it, which is what lets the CLI report duplicate registrations and
lets a repair collapse them.

**Install method is a first-class axis.** An agent reachable both through a host
plugin and through direct wiring declares both, so the direct path can stand
down when :meth:`detect` finds the plugin already present rather than writing a
second registration the host will merge and charge for twice.

A descriptor names the hook and transcript layers by string and never
reimplements them. ``agent_targets`` may reference ``cli.agent_adapters``;
``cli.agent_adapters`` must never import ``agent_targets``, because it sits on
the PreToolUse hot path where module scope is stdlib-only by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Scope(StrEnum):
    """Where a registration lives.

    ``USER`` is per-machine config outside the repo (``~/.claude/settings.json``,
    ``~/.codex/hooks.json``); ``PROJECT`` is repo-local and may be committed.
    The distinction drives more than paths: a repo-shared file keeps the bare
    ``repowise`` command because one contributor's absolute path would break
    everyone else's checkout, while a per-user file pins the absolute path of
    the install that wrote it so a PATH shadow cannot hijack the server.
    """

    USER = "user"
    PROJECT = "project"


class FileAction(StrEnum):
    """What an install or uninstall did to one file.

    One per file, which is what lets an installer render a real per-file log
    instead of a summary. ``UNCHANGED`` is the value that earns its keep: it
    means the file was inspected and already held exactly what we would write,
    which is what makes a re-run byte-identical and stops an idempotent
    re-install from printing a misleading "updated".

    Six values, not seven. Appending a managed block to a file that did not
    carry one looks like a distinct action and is not: it is folded into
    ``UPDATED`` before it reaches a result, because from the caller's side the
    file existed and now differs.
    """

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    NOT_FOUND = "not-found"
    KEPT = "kept"


class Capability(StrEnum):
    """A surface a target can wire, and the vocabulary tiers are derived from."""

    #: An MCP server registration the host will launch.
    MCP = "mcp"
    #: Hook entries that let repowise intercept or annotate tool calls.
    HOOKS = "hooks"
    #: Skill/prompt content the host loads from disk.
    SKILLS = "skills"
    #: Slash commands the host exposes to the user.
    COMMANDS = "commands"
    #: A managed instructions file (``CLAUDE.md``, ``AGENTS.md``).
    INSTRUCTIONS = "instructions"
    #: Session transcripts repowise can mine after the fact.
    TRANSCRIPTS = "transcripts"


class Tier(StrEnum):
    """Support depth, mirroring the language tiers in the README.

    Never set by hand. See :func:`derive_tier`.
    """

    FULL = "full"
    GOOD = "good"
    PASTE_CONFIG = "paste-config"


class DoctorStatus(StrEnum):
    """The four states a target's health can be in.

    Four rather than the obvious two, for one reason: ``STALE`` has to be
    distinct from ``BROKEN``. A hook whose matcher names a tool the host has
    since renamed is
    installed, parses fine, and will never fire — reporting that as "ok" is how
    it stays invisible, and reporting it as "broken" sends the user to fix a
    file that is not damaged.
    """

    OK = "ok"
    NOT_INSTALLED = "not-installed"
    STALE = "stale"
    BROKEN = "broken"


@dataclass(frozen=True)
class InstallMethod:
    """One route by which a target can be wired up.

    *managed_by* is the field that matters. ``"host"`` means the host owns the
    artifact's lifecycle and repowise cannot rewrite it — a plugin the user
    updates through their agent's own command — so a version skew there is
    something to report, never something to fix by writing. ``"repowise"``
    means we own the file and a refresh is ours to perform.
    """

    id: str
    provides: frozenset[Capability]
    managed_by: str
    #: Preferred when several methods are available and nothing is installed.
    preferred: bool = False


@dataclass(frozen=True)
class Registration:
    """One place this target is currently wired to repowise.

    A target can legitimately return several: a user who installed the plugin
    *and* ran ``init`` has two, and reporting that honestly is the point of
    returning a list.
    """

    method: str
    scope: Scope
    config_path: Path
    #: Version of the artifact where the file records one; ``None`` when the
    #: format has nowhere to put it, which is most of them.
    version: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "scope": self.scope.value,
            "config_path": str(self.config_path),
            "version": self.version,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FileWrite:
    """What happened to one file."""

    path: Path
    action: FileAction


@dataclass
class WriteResult:
    """Per-file log of an install or uninstall.

    Mutable and appended to as a target works through its files, because that
    is how targets are written; callers should treat a returned result as
    read-only. Doubles as the ``--format json`` payload, which is why the
    actions are a closed enum rather than prose.
    """

    files: list[FileWrite] = field(default_factory=list)
    #: One-line notes surfaced verbatim, e.g. "Restart the editor to apply."
    notes: list[str] = field(default_factory=list)

    def record(self, path: Path, action: FileAction) -> None:
        self.files.append(FileWrite(path=path, action=action))

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def changed(self) -> bool:
        """True when anything actually moved on disk."""
        return any(
            f.action in (FileAction.CREATED, FileAction.UPDATED, FileAction.REMOVED)
            for f in self.files
        )

    def as_dict(self) -> dict:
        """JSON-ready projection, built here at the construction site.

        Built from the result itself rather than re-derived by a renderer: a
        trimmed projection has two silent failure modes, a key dropped and a key
        kept that nothing prints, and only the first is visible to a test that
        checks the projection alone.
        """
        return {
            "files": [{"path": str(f.path), "action": f.action.value} for f in self.files],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DoctorReport:
    """A target's health, with exactly one thing to run about it.

    One *fix_command*, never a list. A diagnostic that hands back three
    candidate repairs has moved the diagnosis onto the user; picking the single
    next command is the job.
    """

    target_id: str
    status: DoctorStatus
    issues: tuple[str, ...] = ()
    fix_command: str | None = None
    #: Whether ``doctor --repair`` can actually resolve this, i.e. whether
    #: *fix_command* is something the repair pass performs. False for anything
    #: whose fix is a host command or a different repowise command.
    #:
    #: Without it, ``--repair`` fires a global refresh for a condition refresh
    #: provably cannot touch, and then prints its "nothing moved" advice, which
    #: names two conditions the user does not have and omits the one command
    #: that works. A repair that cannot help should decline, not try.
    repairable: bool = True

    def as_dict(self) -> dict:
        return {
            "target": self.target_id,
            "status": self.status.value,
            "issues": list(self.issues),
            "fix_command": self.fix_command,
            "repairable": self.repairable,
        }


@runtime_checkable
class AgentTarget(Protocol):
    """One agent repowise can wire up.

    Implementations compose the helpers in :mod:`.formats` rather than
    inheriting from a base class. The targets differ too much for one to fit:
    Codex needs TOML plus a hooks JSON, VS Code needs two JSON files one of
    which may carry comments, and Cursor needs a JSON config plus a rules file
    with YAML frontmatter that repowise creates outright. A base class would
    need an override hook for each and would force that shape on every target
    that does not want it.

    Every method must be safe to call when nothing was ever installed.
    """

    #: Stable id used in ``--target`` and registry lookup. Short, lowercase.
    id: str
    display_name: str
    docs_url: str | None

    #: Named entries in ``cli.agent_adapters`` / ``core.sessions.adapters``.
    #: ``None`` means this agent has no such surface, which is what keeps it
    #: out of the Full tier.
    hook_adapter: str | None
    session_adapter: str | None

    #: Routes this target can be wired through, in preference order.
    methods: tuple[InstallMethod, ...]

    #: Config key gating this agent's managed instruction file, under
    #: ``editor_files`` in ``.repowise/config.yaml``. Owned by the descriptor so
    #: the legacy CLI flag mapping can look it up rather than restate it.
    project_file_id: str

    def supports_scope(self, scope: Scope) -> bool:
        """Whether this target has a config home at *scope*."""
        ...

    def is_present(self, repo_path: Path | None = None) -> bool:
        """Whether this agent looks installed on this machine.

        Distinct from :meth:`detect`, and the distinction is the whole reason
        this exists: ``detect`` answers "is repowise wired into this agent",
        which is ``False`` for every agent on a first-time user's machine.
        "Which agents should we offer to wire up" needs the other question, and
        it has to be answered by the descriptor — asking it anywhere else
        rebuilds the per-host ``if agent == "codex"`` chain the seam exists to
        delete.

        Cheap by contract: a directory probe or a PATH lookup, never a
        subprocess. This runs on every listing and in the middle of ``init``,
        and an agent that has to be *launched* to find out it is installed is
        an agent we report as absent.

        Best-effort in both directions. A false positive costs an unchecked box
        the user unchecks; a false negative costs a checked box they check.
        Neither is worth a slow probe.
        """
        ...

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        """Every place this target is currently wired.

        *repo_path* is optional because user-scope registrations are knowable
        without one — ``doctor`` run from anywhere still wants them. Passing it
        adds the project-scope registrations. Never raises: a probe that cannot
        read a config reports no registration rather than guessing at one.
        """
        ...

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        """Wire this target up at *scope*, idempotently.

        *repo_path* is keyword-only and required in practice for every scope: a
        project install writes into it, and a user-scope MCP registration has to
        record which repo the server should serve.
        """
        ...

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove only what :meth:`install` writes, preserving siblings.

        Must be safe to call when nothing was ever installed, reporting
        ``not-found`` or ``kept`` rather than raising.
        """
        ...

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        """The config snippet a user would paste by hand.

        MUST NOT touch the filesystem. This is the whole of the Paste-config
        tier: an agent nobody has asked us to support is still served by a
        snippet, at zero maintenance cost.
        """
        ...

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        """Files this target would write at *scope*, without writing them.

        May predict a superset of what one :meth:`install` call writes — an
        opt-in surface counts — but never a path install would not touch, since
        this is what a dry run shows the user.
        """
        ...

    def doctor(self) -> DoctorReport:
        """This target's health, with one command that would fix it."""
        ...


@runtime_checkable
class InstallLifecycle(Protocol):
    """The ``init`` / ``update`` half of an integration's contract.

    Three methods, and they are a subset of :class:`AgentTarget`:
    ``write_project_files`` is a project-scope install, ``register_client`` is a
    user-scope one, and ``refresh_project_files`` is an install that declines to
    create what is not already there.

    It lives here rather than in ``editor_setup`` so there is exactly one home
    for integration protocols. It is still spelled separately from
    :class:`AgentTarget` because the two are driven by different callers:
    ``init`` and ``update`` drive this one and own the console object and the
    per-file progress lines, while ``AgentTarget`` is driven by detection and
    repair paths that must never print into someone else's layout.

    It had a fourth method, ``configure_options``, where each integration
    prompted for itself. That is gone: the prompting is now one registry-built
    checklist rather than one hand-written question per agent, which is what
    stops a fourth agent from meaning a fourth prompt in a fourth module.
    """

    #: The :class:`AgentTarget` id this integration writes for. Declared here
    #: because the checklist reads it to answer "can ``init`` act on this
    #: agent", which is a question the registry alone cannot: a target can be
    #: registered without ``init`` having a writer for it. Two of the three
    #: implementations already carried it; the third did not, and the gap was
    #: invisible until something asked all of them at once.
    integration_id: str

    def write_project_files(
        self, console_obj: object, repo_path: Path, options: object
    ) -> None:
        """Write project-local config or instruction files for this agent."""
        ...

    def register_client(self, console_obj: object, repo_path: Path) -> None:
        """Register user-level client configuration for this agent."""
        ...

    def refresh_project_files(
        self, console_obj: object, repo_path: Path, options: object
    ) -> None:
        """Refresh managed project files after repository content changes."""
        ...


def capabilities_of(target: AgentTarget) -> frozenset[Capability]:
    """Everything *target* wires, unioned across its install methods."""
    provided: set[Capability] = set()
    for method in target.methods:
        provided |= set(method.provides)
    return frozenset(provided)


def derive_tier(target: AgentTarget) -> Tier:
    """Compute a target's support tier from what it actually wires.

    The rule, and why it is this one:

    * A target that writes nothing is **Paste-config**. It has no methods, only
      :meth:`~AgentTarget.print_config`, and costs nothing to keep.
    * A target that names **both** a hook adapter and a transcript adapter is
      **Full**. Those two are the deep surfaces — intercepting tool calls, and
      mining sessions after the fact — and they are the ones that cannot be
      faked by writing a config file. Requiring both is what makes it
      structurally impossible for the docs to claim Full while the session
      adapter is missing.
    * Everything else is **Good**: a real integration, MCP and instructions and
      possibly skills, but no hook-level interception.

    Deliberately strict about Full. Breadth that overclaims depth is worse than
    narrower breadth, and the tier is the thing the README badges repeat.
    """
    if not target.methods:
        return Tier.PASTE_CONFIG
    if target.hook_adapter and target.session_adapter:
        return Tier.FULL
    return Tier.GOOD
