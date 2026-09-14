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

import json
import tomllib
from pathlib import Path

from repowise.cli.errors import reasoned_error

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


#: Seconds a Codex augment hook may run before Codex gives up on it. This is a
#: ceiling for a pathological case rather than a budget: the hook does local
#: SQLite and ``git`` reads with no network and no LLM, so a normal fire is well
#: under a second and most of that is interpreter start. Matches the Claude Code
#: entries, which have carried 10 since they were written; Codex shipped 30 only
#: because the two were never reconciled.
_HOOK_TIMEOUT = 10

#: Timeouts this entry has shipped with, current one excluded. A migration moves
#: only these exact values, so a user who raised the timeout on purpose keeps it.
_LEGACY_HOOK_TIMEOUTS = (30,)

#: Events we used to register on and no longer do. ``UserPromptSubmit`` fired an
#: unmatched context hook on **every** prompt, and what it emitted was the
#: freshness line ``SessionStart`` already carries — so a turn paid one process
#: start per prompt to repeat a block it was given at startup. The Claude Code
#: entries never registered it. Migration removes ours from an existing file; a
#: hook the user wrote on the same event is untouched.
_RETIRED_EVENTS = ("UserPromptSubmit",)


def hooks_config() -> dict[str, object]:
    """Project-local Codex hooks for context injection and freshness checks.

    The shell matcher stays, and that asymmetry with Claude Code is deliberate.
    Claude Code dropped ``Bash``/``PowerShell`` from its augment matcher on
    measurement (51% of invocations, 0.7% of emissions), but it kept ``Read``,
    ``Grep`` and ``Glob``, which is where its emissions come from. Codex has
    none of those tools, so the shell is the only surface a hook can reach it
    on: dropping it here would not trim a matcher, it would leave Codex with
    startup and edits alone.
    """
    from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER

    context_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": _HOOK_TIMEOUT,
        "statusMessage": "Loading repowise context...",
    }
    freshness_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": _HOOK_TIMEOUT,
        "statusMessage": "Checking repowise freshness...",
    }
    return {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [context_hook]}],
            "PostToolUse": [
                {"matcher": SHELL_TOOL_MATCHER, "hooks": [freshness_hook]},
                {"matcher": "apply_patch|Edit|Write", "hooks": [freshness_hook]},
            ],
        }
    }


def _migrate_hooks_config(hooks: dict) -> None:
    """Repair our own entries in an existing ``.codex/hooks.json``, in place.

    The twin of :func:`~repowise.cli.editor_integrations.claude_config._migrate_legacy_hook`,
    and it exists because the merge in :func:`write_hooks_config` is purely
    *additive*: it appends an entry when no entry carries our hook for that
    matcher, and does nothing at all when one does. So a shape we stopped
    shipping survives on an existing install forever, and reinstalling is not a
    repair anyone would think to try, because the install reports success either
    way.

    Two repairs, both scoped to hooks whose command is ours:

    * **Retired events are dropped** — see :data:`_RETIRED_EVENTS`.
    * **Legacy timeouts move to** :data:`_HOOK_TIMEOUT`, and only from the values
      we shipped, so a user who raised it keeps their number.

    Returns nothing on purpose. The obvious shape here is a ``changed`` boolean,
    and the Claude Code twin does return one — but its caller needs it to decide
    whether to write at all, and this one does not: ``write_json_config`` renders
    the merged document and diffs it against what is on disk, so the
    CREATED/UPDATED/UNCHANGED verdict is already computed from the bytes. A
    boolean here would be tracked through three branches and then dropped.
    """
    for event in _RETIRED_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list) or not entries:
            continue
        surviving: list[object] = []
        for entry in entries:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(inner, list):
                surviving.append(entry)
                continue
            remaining = [h for h in inner if not (isinstance(h, dict) and _is_augment_hook(h))]
            if len(remaining) == len(inner):
                surviving.append(entry)
                continue
            # A group that held only our hook goes with it; a group the user
            # also wrote in keeps everything except ours. Same rule as
            # ``remove_hooks_config``, for the same reason.
            if remaining:
                entry["hooks"] = remaining
                surviving.append(entry)
        # Iterating ``_RETIRED_EVENTS`` rather than ``hooks`` is what makes this
        # pop safe: the loop never walks the dict being mutated.
        if surviving:
            hooks[event] = surviving
        else:
            hooks.pop(event)

    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if not isinstance(hook, dict) or not _is_augment_hook(hook):
                    continue
                if hook.get("timeout") in _LEGACY_HOOK_TIMEOUTS:
                    hook["timeout"] = _HOOK_TIMEOUT


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def write_server_config(repo_path: Path) -> FileWrite:
    """Merge the repowise server table into project-local ``.codex/config.toml``."""

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
        raise reasoned_error(
            f"Cannot update {config_path}: [mcp_servers.repowise] holds a value repowise "
            f"cannot rewrite ({exc}). Remove that key and retry; no changes were written.",
            reason="editor_config_unmergeable",
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
    is left alone, so a user who narrowed or annotated an entry keeps it. That
    rule is why :func:`_migrate_hooks_config` runs first — additive alone can
    never retire a shape, only ever add one.

    Returns both writes — the hooks file *and* ``config.toml``'s feature flag —
    because they are genuinely two files and reporting only the first would hide
    a run whose sole effect was switching the feature back on.
    """
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
        raise reasoned_error(
            f"Cannot update {hooks_path}: hooks must contain a JSON object. "
            "Fix or remove it and retry; no changes were written.",
            reason="editor_config_malformed",
        )

    _migrate_hooks_config(hooks)

    for event, entries in new_config["hooks"].items():
        event_hooks = hooks.setdefault(event, [])
        if not isinstance(event_hooks, list):
            raise reasoned_error(
                f"Cannot update {hooks_path}: hooks.{event} must contain a JSON array. "
                "Fix or remove it and retry; no changes were written.",
                reason="editor_config_malformed",
            )
        for entry in entries:
            if not _has_augment_hook_for_matcher(event_hooks, entry.get("matcher")):
                event_hooks.append(entry)

    action = write_json_config(hooks_path, existing)
    return FileWrite(path=hooks_path, action=action), enable_hooks_feature(repo_path)


def remove_hooks_config(repo_path: Path) -> FileWrite:
    """Strip our hook entries from ``.codex/hooks.json``, sparing the user's.

    A two-pass prune, not a file delete: drop our commands from each matcher
    group, drop a group we emptied, drop an event left with no groups, drop the
    ``hooks`` key once it holds nothing, and unlink the file only when the whole
    document is empty. A user who added their own hook to the same event keeps
    it, and keeps the event.

    The ownership test is :func:`_is_augment_hook`, the same predicate install
    uses to decide a matcher already carries our entry. It matches on the
    command string rather than on the entry's shape, which is sound here for a
    reason worth stating: the string it looks for is a repowise executable, so a
    hook carrying it is a repowise hook whoever typed it, and leaving one behind
    after an uninstall would spawn a process for a tool that is no longer wired.
    """
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    hooks_path = project_hooks_path(repo_path)
    if not hooks_path.exists():
        return FileWrite(path=hooks_path, action=FileAction.NOT_FOUND)
    try:
        existing = load_json_object_or_value_error(hooks_path, "hooks.json")
    except ValueError:
        return FileWrite(
            path=hooks_path,
            action=FileAction.KEPT,
            reason="not strict JSON, so removing our entries would drop comments",
        )

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return FileWrite(path=hooks_path, action=FileAction.NOT_FOUND)

    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        # An event that was already empty is not something we emptied, and
        # popping it reported REMOVED over a file holding none of our hooks
        # while quietly deleting a key the user had written.
        if not entries:
            continue
        surviving: list[object] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                surviving.append(entry)
                continue
            inner = entry["hooks"]
            remaining = [h for h in inner if not (isinstance(h, dict) and _is_augment_hook(h))]
            if len(remaining) == len(inner):
                surviving.append(entry)
                continue
            changed = True
            # A group that held only our hook goes with it. A group the user
            # also wrote in keeps everything except ours.
            if remaining:
                entry["hooks"] = remaining
                surviving.append(entry)
        if surviving:
            hooks[event] = surviving
        else:
            hooks.pop(event)
            changed = True

    if not changed:
        return FileWrite(path=hooks_path, action=FileAction.NOT_FOUND)
    if not hooks:
        existing.pop("hooks", None)

    if not existing:
        try:
            hooks_path.unlink()
        except OSError:
            # Falling through to the rewrite rather than swallowing: reporting
            # REMOVED over a file we failed to delete is the false success this
            # track has already shipped once.
            write_json_config(hooks_path, existing)
        return FileWrite(path=hooks_path, action=FileAction.REMOVED)

    write_json_config(hooks_path, existing)
    return FileWrite(path=hooks_path, action=FileAction.REMOVED)


def remove_server_config(repo_path: Path, *, drop_hooks_feature: bool) -> FileWrite:
    """Strip ``[mcp_servers.repowise]``, and the hooks feature when it is ours to drop.

    *drop_hooks_feature* is decided by the caller from what the hooks file looks
    like afterwards. ``features.hooks`` is Codex's global switch, not a repowise
    key: turning it off while the user still has hooks of their own in
    ``.codex/hooks.json`` would silently disable them. So it goes only when
    nothing is left for it to enable.

    Both removals are re-parsed and re-checked rather than trusted.
    :func:`~..formats.toml_merge.remove_table` works by regex over the source
    text, which is what preserves the user's comments everywhere else in the
    file, and which is also why a table spelled some other legal way slips past
    it. A silent miss on the destructive verb reads as a successful uninstall
    over a file that still launches our server.
    """
    import click

    from ..formats.toml_merge import (
        ensure_valid_toml,
        load_toml_document,
        remove_key_line,
        remove_table,
        require_table,
        table_is_bare,
    )

    config_path = project_config_path(repo_path)
    if not config_path.exists():
        return FileWrite(path=config_path, action=FileAction.NOT_FOUND)

    existing_text = config_path.read_text(encoding="utf-8")
    doc = load_toml_document(config_path, existing_text)

    servers = require_table(doc, "mcp_servers", config_path, "mcp_servers")
    has_server = isinstance(servers, dict) and "repowise" in servers
    features = require_table(doc, "features", config_path, "features") or {}
    has_feature = drop_hooks_feature and "hooks" in features

    if not has_server and not has_feature:
        return FileWrite(path=config_path, action=FileAction.NOT_FOUND)

    # The two edits are applied and validated **independently**, and that is the
    # whole shape of this function. Accumulating both into one string and
    # gating the write on "did either fail" meant a `features.hooks` value the
    # line remover declines to cut (a multi-line array, Codex's own key, nothing
    # to do with us) threw away a perfectly good removal of
    # `[mcp_servers.repowise]` as well, and blamed it on "the repowise entry
    # uses a key spelling this remover cannot match" when the repowise entry
    # was fine. Codex went on launching our MCP server.
    def _attempt(text: str, edit, gone) -> tuple[str, bool]:
        """Apply *edit*, keep it only if it parses and *gone* says the key went."""
        candidate = edit(text)
        try:
            parsed = ensure_valid_toml(candidate, config_path) if candidate.strip() else {}
        except click.ClickException:
            return text, False
        return (candidate, True) if gone(parsed) else (text, False)

    merged_text = existing_text
    server_refused = feature_refused = False

    if has_server:
        merged_text, ok = _attempt(
            merged_text,
            lambda text: remove_table(text, "mcp_servers.repowise"),
            lambda doc: "repowise" not in (doc.get("mcp_servers") or {}),
        )
        server_refused = not ok

    if has_feature:
        # One line, not a re-render. ``[features]`` is Codex's table, so
        # rebuilding it from the parse would drop the user's comments inside it
        # and raise outright on any value the narrow serializer cannot encode.
        def _drop_feature(text: str) -> str:
            text = remove_key_line(text, "features", "hooks")
            return remove_table(text, "features") if table_is_bare(text, "features") else text

        merged_text, ok = _attempt(
            merged_text,
            _drop_feature,
            lambda doc: "hooks" not in (doc.get("features") or {}),
        )
        feature_refused = not ok

    # Keyed on whether anything actually moved, not on which flag is set. The
    # earlier spelling asked `server_refused and ...`, which is never true when
    # there was no server table to remove, so a lone declined `features.hooks`
    # fell through, wrote the file back byte-identical, skipped a re-check
    # gated on `has_server`, and reported REMOVED with no reason. Three runs in
    # a row said `removed` over an unchanged file, and run 2 of the ordinary
    # flow lands in exactly that shape.
    changed = (has_server and not server_refused) or (has_feature and not feature_refused)
    if not changed:
        left = []
        if server_refused:
            left.append("the repowise server entry")
        if feature_refused:
            left.append("features.hooks")
        return FileWrite(
            path=config_path,
            action=FileAction.KEPT,
            reason=f"{' and '.join(left)} could not be removed; delete by hand",
        )

    try:
        if not merged_text.strip():
            try:
                config_path.unlink()
            except OSError:
                config_path.write_text(merged_text, encoding="utf-8")
        else:
            config_path.write_text(merged_text, encoding="utf-8")
    except OSError as exc:
        # Escaping here reached `agents remove`, which wraps nothing, as a
        # traceback part-way through a batch.
        return FileWrite(path=config_path, action=FileAction.FAILED, reason=str(exc))

    # Proven from disk, never from ``merged_doc``: a check fed a value derived
    # from our own output cannot fail, which is exactly how ``agents remove``
    # came to report REMOVED over a file that still held the entry. Each key is
    # re-checked only where the edit was actually applied, so a deliberate
    # decline is not reported as a failed write.
    if config_path.exists():
        try:
            written = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            written = {}
        left_server = has_server and not server_refused
        left_server = left_server and "repowise" in (written.get("mcp_servers") or {})
        left_feature = has_feature and not feature_refused
        left_feature = left_feature and "hooks" in (written.get("features") or {})
        if left_server or left_feature:
            return FileWrite(
                path=config_path,
                action=FileAction.KEPT,
                reason="the entry was still present after the write",
            )

    # Something moved and something else was declined. The row is KEPT, not
    # REMOVED, and the action is what matters rather than the reason: the runner
    # counts leftovers and picks the exit code from the action alone, so a
    # REMOVED row carrying a "left in place" sentence was counted as clean.
    # `uninstall --all` exited 0 under "everything selected is gone" while
    # Codex went on launching our MCP server from an entry still in the file.
    #
    # Both halves are named when both were refused. The KEPT head and the
    # REMOVED tail used to disagree about which one to report, so a run that
    # declined both mentioned only the server.
    if server_refused or feature_refused:
        left = []
        if server_refused:
            left.append("the repowise server entry")
        if feature_refused:
            left.append("features.hooks")
        return FileWrite(
            path=config_path,
            action=FileAction.KEPT,
            reason=f"{' and '.join(left)} could not be removed; delete by hand",
        )
    return FileWrite(path=config_path, action=FileAction.REMOVED)


def _config_parse_refusal(repo_path: Path) -> str | None:
    """Why ``config.toml`` cannot be edited, or ``None`` when it can.

    Asked before anything is removed, so a file we cannot read stops the run
    cleanly at the start rather than raising out of the middle of it. The merge
    path is right to raise: it is called from `install`, where aborting leaves
    the file untouched. Removal is called from a batch that has already deleted
    things by the time it gets here.
    """
    config_path = project_config_path(repo_path)
    if not config_path.exists():
        return None
    try:
        doc = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return "the file is not valid TOML, so editing it would lose whatever we misread"
    for key in ("mcp_servers", "features"):
        value = doc.get(key)
        if value is not None and not isinstance(value, dict):
            return f"[{key}] is not a table in this file, so our entry cannot be located"
    return None


def _user_hooks_leftover_reason() -> str | None:
    """Why ``~/.codex/hooks.json`` still has us in it, read from disk. None when clean.

    Scoped to **exactly what the remover removes**, which is the rewrite hook.
    Probing for any command containing "repowise" made the two halves disagree:
    the probe flagged a hook the remover does not touch, so the row reported
    ``KEPT`` on every run and the machine could never be made clean. Any hook
    the user wrote that merely mentions ``repowise distill`` or
    ``repowise update`` triggered it.

    The Claude Code side is broad because its uninstall is broad, removing the
    rewrite hook and the augment hooks and the MCP entry. Codex's user-scope
    uninstall removes the rewrite hook, so this asks about the rewrite hook.
    """
    from repowise.cli.editor_integrations.codex_config import _is_rewrite_hook

    path = user_hooks_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        try:
            if b"repowise-rewrite" not in path.read_bytes().lower():
                return None
        except OSError:
            return None
        return "the file could not be read and still mentions our rewrite hook"
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(hooks, dict):
        return None
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            if any(_is_rewrite_hook(hook) for hook in entry["hooks"]):
                return "our rewrite hook was still present after the write"
    return None


def _hooks_file_has_entries(hooks_path: Path) -> bool:
    """Whether ``hooks.json`` still holds any hook at all.

    Read from disk rather than inferred from what the removal returned, and
    deliberately broader than "has a `hooks` key with something in it": a file
    we cannot parse might hold anything, and switching off the feature that runs
    it would be a guess about somebody else's file.
    """
    if not hooks_path.exists():
        return False
    try:
        doc = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    # A file with no hooks left but other keys still in it is the user's file,
    # and still not a reason to keep the feature switched on.
    return isinstance(hooks, dict) and any(entries for entries in hooks.values())


def prune_project_dir(repo_path: Path) -> None:
    """Remove ``.codex/`` once uninstall emptied it, and never otherwise.

    ``rmdir`` rather than a recursive delete, so a directory holding anything at
    all, ours or the user's, is left exactly as it is. The symlink guard is the
    one Cursor already carries: following a junction out of the repo to delete
    something is not a risk worth a tidy directory.
    """
    directory = project_config_path(repo_path).parent
    if directory.is_symlink() or not directory.is_dir():
        return
    try:
        directory.rmdir()
    except OSError:
        return


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
            writes.append(
                FileWrite(
                    path=path,
                    action=FileAction.KEPT,
                    reason="could not be written (permission, or a directory in its place)",
                )
            )
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
        existed = path.exists()
        try:
            path.unlink()
        except OSError as exc:
            # Reported rather than skipped. Producing no row at all for a file
            # we could not delete meant the caller saw no leftover, so
            # `repowise uninstall` printed "everything selected is gone" and
            # exited zero over a prompt still sitting in the user's directory.
            if existed:
                removed.append(
                    FileWrite(path=path, action=FileAction.FAILED, reason=str(exc))
                )
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
    """True when *hook*'s command is ours. Any other shape is False.

    ``hook.get("command", "")`` was the original, and it assumes the value is a
    string. This reads a file the user owns, where ``"command": 7`` is a shape
    to answer rather than to raise ``TypeError`` on, part-way through a write
    that has already rewritten other entries. The Claude Code twin was hardened
    for exactly this; the Codex half was missed, and the migration above walks
    every event in the document, which is where such a value would sit.
    """
    cmd = hook.get("command")
    if not isinstance(cmd, str):
        return False
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _has_augment_hook_for_matcher(hook_list: list, matcher: object) -> bool:
    """True when some entry for *matcher* already carries our hook.

    Every shape is guarded for the same reason :func:`_is_augment_hook` is: this
    walks a file the user owns, and a bare string or a null under ``hooks`` used
    to raise out of the middle of ``write_hooks_config`` rather than being
    answered. A shape we cannot read carries nothing of ours, which makes the
    install append its entry — the safe direction, since a duplicate matcher is
    inert where a missing hook is silent.
    """
    for entry in hook_list:
        if not isinstance(entry, dict) or entry.get("matcher") != matcher:
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if isinstance(hook, dict) and _is_augment_hook(hook):
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
            result.record(written.path, written.action, written.reason)
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        from repowise.cli.editor_integrations.codex_config import (
            remove_agents_md_distill_section,
            uninstall_codex_rewrite_hook,
        )

        result = WriteResult()
        if scope is Scope.USER:
            removed = uninstall_codex_rewrite_hook()
            # Asked of the file rather than inferred from the boolean, for the
            # same reason Claude Code's user scope is: that return is False for
            # "nothing of ours was here", "could not parse" and "the write
            # failed" alike, and calling the last two not-found claims we looked
            # and the file was clean.
            leftover = _user_hooks_leftover_reason()
            if leftover is not None:
                result.record(user_hooks_path(), FileAction.KEPT, leftover)
            else:
                result.record(
                    user_hooks_path(),
                    FileAction.REMOVED if removed else FileAction.NOT_FOUND,
                )
            for deleted in remove_prompts():
                result.record(deleted.path, deleted.action, deleted.reason)
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
        # The two config files install writes go first, and unconditionally.
        # They used to be left behind entirely: `describe_paths` listed them as
        # ours, install wrote both, and uninstall touched neither, so a removed
        # Codex kept launching our MCP server and spawning our hooks. `.codex/`
        # is gitignored in most repos, so `git status` showed nothing either.
        # Computed up front so the config decision is made against the file as
        # the user left it. It gates the config edit below, not the hooks
        # removal: the two files are independent, and a config we cannot read is
        # no reason to leave our hooks running.
        hooks_path = project_hooks_path(repo_path)
        parse_refusal = _config_parse_refusal(repo_path)

        # Hooks first, then one pass over config.toml. Two passes over the same
        # file put two rows for one path in the report, which is contradictory
        # the moment they disagree.
        hooks_write = remove_hooks_config(repo_path)
        result.record(hooks_write.path, hooks_write.action, hooks_write.reason)

        if parse_refusal is not None:
            result.record(project_config_path(repo_path), FileAction.KEPT, parse_refusal)
        else:
            # `features.hooks` is Codex's global switch, so it goes only once
            # nothing is left for it to enable. Read from the file as it stands
            # now, not inferred from what the removal returned: an `unlink` that
            # failed and fell back to a rewrite reports REMOVED over a file that
            # still exists.
            keep_feature = _hooks_file_has_entries(hooks_path)
            server_write = remove_server_config(repo_path, drop_hooks_feature=not keep_feature)
            result.record(server_write.path, server_write.action, server_write.reason)
            if keep_feature:
                result.note(
                    f"{project_config_path(repo_path)}: features.hooks left enabled because "
                    f"{hooks_path} still holds hooks that are not ours."
                )
        # Only when something of ours actually went, matching Cursor. Pruning
        # unconditionally deleted a `.codex/` that happened to be empty and that
        # this uninstall had just reported finding nothing in.
        if any(written.action is FileAction.REMOVED for written in result.files):
            prune_project_dir(repo_path)

        owners = other_managers_of(instructions, exclude=ID, scope=scope, repo_path=repo_path)
        block_state = inspect(instructions, DISTILL_MARKER_START, DISTILL_MARKER_END).state
        if owners and block_state is BlockState.PRESENT:
            reason = (
                f"{' and '.join(owners)} still reads the same managed block; "
                "remove that agent too if you want the block gone"
            )
            result.record(instructions, FileAction.KEPT, reason)
            result.note(f"{instructions} kept: {reason}.")
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
        if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
            result.record(instructions, FileAction.NOT_FOUND)
        else:
            from ..formats.marker_block import refusal_reason

            # PRESENT here means the write failed, not that we declined: the
            # shared-ownership refusal returned earlier.
            action = FileAction.FAILED if state is BlockState.PRESENT else FileAction.KEPT
            result.record(instructions, action, refusal_reason(state))
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
