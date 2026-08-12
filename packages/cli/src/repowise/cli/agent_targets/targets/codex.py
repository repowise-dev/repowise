"""Codex CLI as an agent target.

Full tier by the derived rule — it names both a hook adapter and a transcript
adapter. It is asymmetric with Claude Code in a way worth stating, because it
runs the *opposite* way round for each of the two content surfaces.

Skills reach Claude Code and Codex the same way, through the plugin. Slash
commands do not, and cannot: a Codex plugin manifest has no slot for them. A
plugin may bundle ``skills/``, ``hooks/``, ``assets/``, ``.mcp.json`` and
``.app.json``, and that is the whole list. The only surface that yields a Codex
slash command is ``~/.codex/prompts/``, which is local-only (plugins cannot
write it), so the CLI installs them from package data, off the same shared
source that renders the plugin's skills. Claude Code gets its commands from the
plugin and never from ``init``; Codex gets its commands from ``init``'s
successor commands and never from the plugin.

Codex is the only target that writes three formats for one install: a TOML
server table, a TOML feature flag that has to be switched on separately or the
hooks are inert, and a JSON hooks file. That combination is the concrete
argument for composition over a base class — no single inherited ``write()``
shape fits it without an override per format.

Two quirks preserved from the existing implementation, both load-bearing:

* The shell-tool matcher is derived from the hook adapter rather than spelled
  here. Codex renamed its shell tool between releases, and a hardcoded name
  produced installs whose matcher selected nothing — silent in exactly the way
  a working hook is.
* Enabling ``features.hooks`` is a second write to the same TOML file. Skipping
  it leaves a hooks.json the host never reads.
"""

from __future__ import annotations

from pathlib import Path

from ..types import (
    Capability,
    DoctorReport,
    DoctorStatus,
    FileAction,
    FileWrite,
    InstallMethod,
    Registration,
    Scope,
    WriteResult,
)

ID = "codex"
DISPLAY_NAME = "Codex CLI"
DOCS_URL = "https://developers.openai.com/codex/cli"

#: Config key for this agent's managed instruction file (``AGENTS.md``).
PROJECT_FILE_ID = "agents_md"

METHODS = (
    InstallMethod(
        id="plugin",
        provides=frozenset({Capability.SKILLS, Capability.HOOKS}),
        managed_by="host",
    ),
    InstallMethod(
        id="direct",
        provides=frozenset(
            {
                Capability.MCP,
                Capability.HOOKS,
                Capability.INSTRUCTIONS,
                # Slash commands come from the direct path, not the plugin.
                # see the module docstring. This is the mirror image of Claude
                # Code, where they come from the plugin and not the direct path.
                Capability.COMMANDS,
            }
        ),
        managed_by="repowise",
        preferred=True,
    ),
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def project_config_path(repo_path: Path) -> Path:
    return repo_path / ".codex" / "config.toml"


def project_hooks_path(repo_path: Path) -> Path:
    return repo_path / ".codex" / "hooks.json"


def user_hooks_path() -> Path:
    from repowise.cli.editor_integrations.codex_config import _codex_hooks_path

    return _codex_hooks_path()


def instructions_path(repo_path: Path) -> Path:
    return repo_path / "AGENTS.md"


def user_prompts_dir() -> Path:
    """Where Codex looks for slash commands.

    Global and flat. It is shared with every other tool the user has installed,
    which is why every file we put there is prefixed ``repowise-``.
    """
    return Path.home() / ".codex" / "prompts"


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def server_table(repo_path: Path) -> dict[str, object]:
    """The ``[mcp_servers.repowise]`` table Codex reads.

    Bare ``repowise`` command: this file is repo-local and may be committed, so
    it follows the same rule as ``.mcp.json`` rather than pinning an absolute
    path only valid on the machine that ran ``init``.
    """
    return {
        "command": "repowise",
        "args": ["mcp"],
        "cwd": str(repo_path.resolve()),
        "startup_timeout_sec": 20,
    }


def hooks_config() -> dict[str, object]:
    """Project-local Codex hooks for context injection and freshness checks."""
    from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER

    context_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Loading repowise context...",
    }
    freshness_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Checking repowise freshness...",
    }
    return {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [context_hook]}],
            "UserPromptSubmit": [{"hooks": [context_hook]}],
            "PostToolUse": [
                {"matcher": SHELL_TOOL_MATCHER, "hooks": [freshness_hook]},
                {"matcher": "apply_patch|Edit|Write", "hooks": [freshness_hook]},
            ],
        }
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def write_server_config(repo_path: Path) -> FileWrite:
    """Merge the repowise server table into project-local ``.codex/config.toml``."""
    import click

    from ..formats.toml_merge import (
        ensure_valid_toml,
        load_toml_document,
        replace_table,
        require_table,
        table_block,
        write_if_changed,
    )

    config_path = project_config_path(repo_path)

    if config_path.exists():
        existing_text = config_path.read_text(encoding="utf-8")
        doc: dict | None = load_toml_document(config_path, existing_text)
    else:
        existing_text = ""
        doc = None

    # Both levels are checked before the regex runs: a scalar where a table
    # belongs means the merge would produce a duplicate key, and refusing is
    # the only answer that leaves the user's file intact.
    stored: dict[str, object] = {}
    if doc is not None:
        servers = require_table(doc, "mcp_servers", config_path, "mcp_servers")
        if servers is not None:
            stored = dict(require_table(servers, "repowise", config_path, "mcp_servers.repowise") or {})

    # Generated keys overwrite stored ones so a moved repo repoints, but any
    # key the user added to the table survives. ``replace_table`` rewrites the
    # whole table, so without this every extra key is dropped on every write —
    # the same failure the JSON path fixed for ``env`` blocks in issue #307,
    # which this path never had a guard for.
    #
    # Preserving a key means re-rendering it, so a value the narrow serializer
    # cannot encode has to stop the write rather than escape as a bare
    # TypeError. This function is on init's path with no try around it, so the
    # difference is a ClickException naming the file against a traceback
    # mid-run.
    merged = {**stored, **server_table(repo_path)}
    try:
        block = table_block("mcp_servers.repowise", merged)
    except TypeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: [mcp_servers.repowise] holds a value repowise "
            f"cannot rewrite ({exc}). Remove that key and retry; no changes were written."
        ) from exc
    merged_text = replace_table(existing_text, "mcp_servers.repowise", block)
    merged_doc = ensure_valid_toml(merged_text, config_path)
    action = write_if_changed(config_path, merged_text, merged_doc, doc)
    return FileWrite(path=config_path, action=action)


def enable_hooks_feature(repo_path: Path) -> FileWrite:
    """Switch on ``features.hooks``, without which the hooks file is inert."""
    from ..formats.toml_merge import (
        ensure_valid_toml,
        load_toml_document,
        replace_table,
        require_table,
        table_block,
        write_if_changed,
    )

    config_path = project_config_path(repo_path)

    if config_path.exists():
        existing_text = config_path.read_text(encoding="utf-8")
        doc: dict | None = load_toml_document(config_path, existing_text)
    else:
        existing_text = ""
        doc = None

    features = dict(require_table(doc or {}, "features", config_path, "features") or {})
    features["hooks"] = True
    merged_text = replace_table(existing_text, "features", table_block("features", features))
    merged_doc = ensure_valid_toml(merged_text, config_path)
    action = write_if_changed(config_path, merged_text, merged_doc, doc)
    return FileWrite(path=config_path, action=action)


def write_hooks_config(repo_path: Path) -> tuple[FileWrite, FileWrite]:
    """Merge repowise hooks into ``.codex/hooks.json`` and enable the feature.

    Additive per matcher group: a matcher that already carries one of our hooks
    is left alone, so a user who narrowed or annotated an entry keeps it.

    Returns both writes — the hooks file *and* ``config.toml``'s feature flag —
    because they are genuinely two files and reporting only the first would hide
    a run whose sole effect was switching the feature back on.
    """
    import click

    from ..formats.json_merge import load_json_object, write_json_config

    hooks_path = project_hooks_path(repo_path)
    new_config = hooks_config()

    if hooks_path.exists():
        existing = load_json_object(hooks_path)
    else:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}

    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise click.ClickException(
            f"Cannot update {hooks_path}: hooks must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
        )

    for event, entries in new_config["hooks"].items():
        event_hooks = hooks.setdefault(event, [])
        if not isinstance(event_hooks, list):
            raise click.ClickException(
                f"Cannot update {hooks_path}: hooks.{event} must contain a JSON array. "
                "Fix or remove it and retry; no changes were written."
            )
        for entry in entries:
            if not _has_augment_hook_for_matcher(event_hooks, entry.get("matcher")):
                event_hooks.append(entry)

    action = write_json_config(hooks_path, existing)
    return FileWrite(path=hooks_path, action=action), enable_hooks_feature(repo_path)


#: Namespace for every prompt this target writes. ``~/.codex/prompts`` is a flat
#: global directory shared with every other tool the user has installed, so the
#: prefix is what keeps our filenames from colliding with theirs.
#:
#: It is **not** an ownership test. A user writing their own Repowise prompt will
#: reasonably call it ``repowise-my-team-workflow.md``, and treating the prefix as
#: proof of ownership would delete it on uninstall. Nothing on disk distinguishes
#: our file from theirs, so the only safe answer is to touch the names we ship and
#: no others. See :func:`remove_prompts`.
PROMPT_PREFIX = "repowise-"


def bundled_prompts() -> list[tuple[str, str]]:
    """The Codex prompts shipped in the wheel, as ``(filename, text)``, sorted.

    Package data rather than ``plugins/codex/``: a Codex plugin manifest has no
    slot for commands (``skills/``, ``hooks/``, ``assets/``, ``.mcp.json`` and
    ``.app.json``, and nothing else), so the only surface that yields a Codex
    slash command is this directory, and the CLI is the only thing that can
    write it. Generated from ``plugins/shared/commands/`` by
    ``scripts/gen_plugin_content.py``.
    """
    from importlib.resources import files

    root = files("repowise.cli.agent_targets").joinpath("_data").joinpath("codex_prompts")
    prompts = [entry for entry in root.iterdir() if entry.name.endswith(".md")]
    return sorted(
        ((entry.name, entry.read_text(encoding="utf-8")) for entry in prompts),
        key=lambda pair: pair[0],
    )


def _current_prompt_text(path: Path) -> str | None:
    """The prompt's text with line endings normalised, or None if unusable.

    "Unusable" covers absent, a directory, unreadable, and *not valid UTF-8*.
    that last one is a ``UnicodeDecodeError``, which is a ``ValueError`` and so
    escapes an ``OSError`` handler. Nothing wraps ``install``: ``agents add``,
    ``agents refresh`` and ``doctor --repair`` all call it bare, and the last of
    those would abort part-way through having already written other agents'
    configs. Every one of these states means the same thing to the caller: the
    file we would write is not there, so they collapse to None.
    """
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, ValueError):
        return None


def write_prompts() -> list[FileWrite]:
    """Install the slash commands into ``~/.codex/prompts``, one file each.

    LF discipline rather than the platform translation the JSON configs take:
    these are whole files repowise owns end to end, so pinning the endings makes
    a re-run on a different machine a no-op instead of a rewrite.

    One unwritable name (a directory sitting at ``repowise-ask.md``, a
    permission) does not stop the other seventeen. Raising instead would abort
    ``install`` after the rewrite hook had already been written and recorded,
    which is the part-way failure this was supposed to remove, just moved. The
    refusal is reported as :attr:`~..types.FileAction.KEPT`, the value this
    codebase already uses for "something is there and we did not touch it", and
    it reaches the user as a row in the ``agents add`` output.
    """
    from ..formats.json_merge import atomic_write_text

    directory = user_prompts_dir()
    writes: list[FileWrite] = []
    for name, text in bundled_prompts():
        path = directory / name
        current = _current_prompt_text(path)
        if current == text:
            writes.append(FileWrite(path=path, action=FileAction.UNCHANGED))
            continue
        existed = path.exists()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text, newline="\n")
        except OSError:
            writes.append(FileWrite(path=path, action=FileAction.KEPT))
            continue
        writes.append(
            FileWrite(path=path, action=FileAction.UPDATED if existed else FileAction.CREATED)
        )
    return writes


def remove_prompts() -> list[FileWrite]:
    """Delete the prompts repowise ships, and nothing else in that directory.

    Scoped to the **currently bundled names**, deliberately, and not to
    ``repowise-*.md``. The prefix is a namespace, not a proof of ownership: a
    user's own ``repowise-my-team-workflow.md`` matches it, and deleting that is
    unrecoverable.

    The cost of the narrow rule is that a command retired between releases leaves
    one stale file behind after an uninstall. That is the right way round: losing
    a file of ours is a wasted kilobyte, losing one of theirs is data loss. That is
    it is why :data:`PROMPT_PREFIX` documents itself as a namespace.
    """
    directory = user_prompts_dir()
    if not directory.is_dir():
        return []
    removed: list[FileWrite] = []
    for name, _text in bundled_prompts():
        path = directory / name
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(FileWrite(path=path, action=FileAction.REMOVED))
    return removed


def stale_prompts() -> list[str]:
    """Bundled prompts that are missing from, or out of date in, ``~/.codex/prompts``.

    The drift this branch exists to close, on the one surface the CLI can
    actually repair. The Claude Code plugin skew is *reported* because repowise
    cannot rewrite a plugin; these it can rewrite in one command, so staying
    silent about them would be strictly worse.

    Empty when nothing is installed at all. An agent nobody wired up is not a
    stale install, and ``doctor`` already has a ``not-installed`` answer for that.
    """
    directory = user_prompts_dir()
    if not directory.is_dir():
        return []
    bundled = bundled_prompts()
    if not any((directory / name).exists() for name, _ in bundled):
        return []
    return [name for name, text in bundled if _current_prompt_text(directory / name) != text]


def _combined(first: FileAction, second: FileAction) -> FileAction:
    """One action describing two writes to the same file.

    ``config.toml`` is written twice per install — the server table, then the
    feature flag — and the file's entry in the result has to answer "did this
    file move", not "did the last of the two writes move it".
    """
    if FileAction.CREATED in (first, second):
        return FileAction.CREATED
    if FileAction.UPDATED in (first, second):
        return FileAction.UPDATED
    return first


def _is_augment_hook(hook: dict) -> bool:
    cmd = hook.get("command", "")
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _has_augment_hook_for_matcher(hook_list: list, matcher: object) -> bool:
    for entry in hook_list:
        if entry.get("matcher") != matcher:
            continue
        for hook in entry.get("hooks", []):
            if _is_augment_hook(hook):
                return True
    return False


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place Codex is currently wired to repowise."""
    from repowise.cli.editor_integrations.codex_config import codex_rewrite_hook_installed

    found: list[Registration] = []

    if codex_rewrite_hook_installed():
        found.append(
            Registration(method="direct", scope=Scope.USER, config_path=user_hooks_path()),
        )

    if repo_path is not None:
        config_path = project_config_path(repo_path)
        if config_path.exists() and "mcp_servers.repowise" in config_path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            found.append(
                Registration(method="direct", scope=Scope.PROJECT, config_path=config_path),
            )

    return found


class CodexTarget:
    """Descriptor for the Codex CLI. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = "codex"
    session_adapter = "codex"
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``codex`` on PATH, or a ``~/.codex`` the CLI left behind.

        Deliberately *not* ``is_codex_cli_installed() and is_codex_logged_in()``,
        which is what init's Codex prompt used to gate on. Those shell out twice
        with a 5s and a 10s timeout, which is not a probe you run on every
        listing. Login state is still checked, but where it can act on the
        answer: the setup path warns when it writes config for a CLI that is
        installed and signed out.
        """
        import shutil

        return shutil.which("codex") is not None or (Path.home() / ".codex").is_dir()

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        return detect(repo_path)

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        from repowise.cli.editor_integrations.codex_config import install_codex_rewrite_hook

        from ..formats.observe import observed_action, read_bytes

        result = WriteResult()
        if scope is Scope.PROJECT:
            if repo_path is None:
                raise ValueError("project-scope install needs a repo_path")
            # Server table first, then the hooks file (which flips the feature
            # flag in the same config.toml). The order is the one init has
            # always used and it is what the file's table order records.
            server = write_server_config(repo_path)
            hooks, feature = write_hooks_config(repo_path)
            result.record(server.path, _combined(server.action, feature.action))
            result.record(hooks.path, hooks.action)
            return result

        # Observed rather than assumed, for the same reason Claude Code's is:
        # ``install_codex_rewrite_hook`` returns a path, not an action. Hard-
        # coding UPDATED here made every re-run and every ``agents refresh``
        # report a change, which also made the "nothing moved" branch of
        # ``doctor --repair`` unreachable for any codex-wired repo.
        hooks_path = user_hooks_path()
        before = read_bytes(hooks_path)
        installed = install_codex_rewrite_hook()
        if installed:
            result.record(hooks_path, observed_action(before, read_bytes(hooks_path)))
        else:
            result.record(hooks_path, FileAction.NOT_FOUND)

        # Not gated on the hook install: the prompts are a separate surface in a
        # separate directory, and a Codex build too old for PreToolUse rewriting
        # still reads slash commands perfectly well.
        for written in write_prompts():
            result.record(written.path, written.action)
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        from repowise.cli.editor_integrations.codex_config import (
            remove_agents_md_distill_section,
            uninstall_codex_rewrite_hook,
        )

        result = WriteResult()
        if scope is Scope.USER:
            removed = uninstall_codex_rewrite_hook()
            result.record(
                user_hooks_path(),
                FileAction.REMOVED if removed else FileAction.NOT_FOUND,
            )
            for deleted in remove_prompts():
                result.record(deleted.path, deleted.action)
            return result

        if repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")
        instructions = instructions_path(repo_path)

        from ..formats.marker_block import BlockState, inspect
        from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
        from ..registry import other_managers_of

        # ``AGENTS.md`` is a host-neutral convention, not this target's private
        # file: OpenCode and Hermes read the same path in the same repo and
        # manage the same marker block. Stripping it while any of them is still
        # wired leaves that agent configured and silently without its
        # instructions, so the block stays and the caller is told who is still
        # reading it.
        #
        # The owner lookup is over the registry rather than a named sibling, so
        # a fourth agent adopting this file inherits the guard. That matters
        # more than it sounds: a shared-ownership fix applied only to the agent
        # added most recently leaves the identical bug in its siblings, which is
        # how this class of defect has kept surviving a review round on this
        # track.
        owners = other_managers_of(instructions, exclude=ID, scope=scope, repo_path=repo_path)
        block_state = inspect(instructions, DISTILL_MARKER_START, DISTILL_MARKER_END).state
        if owners and block_state is BlockState.PRESENT:
            result.record(instructions, FileAction.KEPT)
            result.note(
                f"{instructions} kept: {' and '.join(owners)} still reads the same "
                "managed block. Remove that agent too if you want the block gone."
            )
            return result

        if remove_agents_md_distill_section(repo_path):
            result.record(instructions, FileAction.REMOVED)
            return result

        # The removal returns False for four different reasons, and reporting
        # them all as not-found says "there was nothing of ours here" about a
        # file we deliberately declined to touch. Same distinction the Cursor
        # rules file draws, and it matters more here: AGENTS.md is a file users
        # write in, so "left alone" is the common answer. Re-inspected rather
        # than reusing ``block_state``: the removal above runs in between and
        # the whole question here is what the file looks like after it.
        state = inspect(instructions, DISTILL_MARKER_START, DISTILL_MARKER_END).state
        result.record(
            instructions,
            FileAction.NOT_FOUND
            if state in (BlockState.ABSENT_FILE, BlockState.ABSENT)
            else FileAction.KEPT,
        )
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        from ..formats.toml_merge import table_block

        target = repo_path or Path.cwd()
        return (
            table_block("mcp_servers.repowise", server_table(target))
            + "\n\n"
            + table_block("features", {"hooks": True})
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is Scope.USER:
            return [str(user_hooks_path()), str(user_prompts_dir())]
        repo = repo_path or Path.cwd()
        return [
            str(project_config_path(repo)),
            str(project_hooks_path(repo)),
            str(instructions_path(repo)),
        ]

    def doctor(self) -> DoctorReport:
        """Health of the Codex wiring.

        The stale-matcher case matters more here than anywhere else: Codex is
        the harness that actually renamed its shell tool, and an install written
        before the rename carries a matcher that selects nothing.
        """
        from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER
        from repowise.cli.editor_integrations.codex_config import (
            codex_rewrite_hook_matcher,
            codex_supports_rewrite,
        )

        from ..formats.json_merge import is_damaged

        hooks = user_hooks_path()
        if is_damaged(hooks):
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.BROKEN,
                issues=(f"{hooks} is not valid JSON, so Codex loads none of its hooks.",),
                fix_command="repowise hook rewrite install",
                # Not the refresh pass's job: detection cannot see a registration
                # inside a file it could not parse, so `--repair` would skip this
                # target and report success. Same reasoning as Claude Code's.
                repairable=False,
            )

        # Read before the hook checks so a drifted prompt set is still reported
        # on a machine whose rewrite hook was never installed. The two surfaces
        # are wired by different commands and go stale independently.
        stale = stale_prompts()

        matcher = codex_rewrite_hook_matcher()
        if matcher is None and not stale:
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.NOT_INSTALLED,
                issues=("The distill rewrite hook is not installed for Codex.",),
                fix_command="repowise hook rewrite install",
            )

        issues: list[str] = []
        if stale:
            issues.append(
                f"{len(stale)} of the {len(bundled_prompts())} Codex slash commands in "
                f"{user_prompts_dir()} are missing or out of date, so /prompts:repowise-* "
                "is running an older version than this CLI."
            )
        # Every branch below is gated on the hook actually being installed, and
        # the absent case is stated rather than dropped. Loosening the early
        # return above to `and not stale` moved this whole block into reach for a
        # machine with no hook at all, which silently lost a true fact (the hook
        # is missing) and made a false one sayable (that it "is registered").
        matcher_stale = matcher is not None and matcher != SHELL_TOOL_MATCHER
        if matcher is None:
            issues.append("The distill rewrite hook is not installed for Codex.")
        elif matcher_stale:
            issues.append(
                f"The rewrite hook matches {matcher!r}, but Codex now names its shell "
                f"tools {SHELL_TOOL_MATCHER!r}. The hook is installed and will never fire."
            )
        if matcher is not None and codex_supports_rewrite() is False:
            issues.append(
                "This Codex build predates PreToolUse command rewriting, so the hook "
                "is registered but its rewrite will be rejected at runtime."
            )

        if not issues:
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        # Stale prompts win the fix slot: `agents add` rewrites them *and* the
        # hook, where `hook rewrite install` only does the hook. It is also the
        # honest command for the case `refresh` cannot reach: a machine with
        # prompts but no user-scope hook registration has nothing for refresh to
        # detect, so `--repair` would skip it and report success.
        #
        # `repairable` follows the same rule as Claude Code's: it names what the
        # refresh pass rewrites, which is the hook, and a stale prompt set must
        # not suppress that repair the way the first cut of this let it.
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.STALE,
            issues=tuple(issues),
            fix_command=(
                "repowise agents add --target=codex" if stale else "repowise hook rewrite install"
            ),
            repairable=matcher_stale,
        )


TARGET = CodexTarget()
