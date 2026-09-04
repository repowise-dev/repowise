"""JSON config mechanics shared by every target that writes JSON.

Mechanics only — atomic writes, order-insensitive comparison, and the two
reader disciplines the existing writers use. Merge *semantics* stay in each
target, because they differ in ways that are not incidental: the Claude and
root configs deep-merge per server so a user's ``env`` block survives
re-registration, while the VS Code files bail rather than touch a file that
might carry comments. Unifying those would change observable behaviour, which
is the one thing this rewrite is not allowed to do.

The two reader disciplines are deliberate and both are kept:

* :func:`load_json_object` raises ``click.ClickException`` with a message that
  names the file and states that nothing was written. This is for config we own
  the write of, where the right answer is to stop and tell the user.
* :func:`load_json_object_or_value_error` raises ``ValueError``. This is for the
  ``.vscode`` files, where the caller catches it and leaves the file alone: VS
  Code accepts comments, so a parse failure is more likely to mean "JSONC" than
  "damaged", and destroying a commented config would be far worse than skipping
  a merge.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from repowise.cli.errors import reasoned_error

from ..types import FileAction


def load_json_object(config_path: Path) -> dict:
    """Read a JSON object, refusing to silently replace bad content.

    Behaviourally identical to the long-standing ``mcp_config.load_existing_config``
    it replaces, down to the message text, because those messages are what
    users have seen and what the tests assert.
    """
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise reasoned_error(
            f"Cannot update {config_path}: existing file is not valid JSON. "
            "Fix or remove it and retry; no changes were written.",
            reason="editor_config_malformed",
        ) from exc
    except OSError as exc:
        raise reasoned_error(
            f"Cannot update {config_path}: existing file could not be read. "
            "Fix the file permissions and retry; no changes were written.",
            reason="editor_config_unreadable",
        ) from exc
    if not isinstance(existing, dict):
        raise reasoned_error(
            f"Cannot update {config_path}: existing file must contain a JSON object. "
            "Fix or remove it and retry; no changes were written.",
            reason="editor_config_malformed",
        )
    return existing


def load_json_object_or_value_error(config_path: Path, label: str) -> dict:
    """Read a JSON object, raising ``ValueError`` so the caller can skip.

    ``json.JSONDecodeError`` is itself a ``ValueError``, so a JSONC file and a
    wrong-shaped file reach the caller through one except clause.
    """
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return existing


def is_damaged(config_path: Path) -> bool:
    """True when *config_path* is present but is not readable JSON.

    The distinction a health check lives on. A config that is absent and a
    config that is there and unparseable both make every "is repowise wired
    up" probe answer no, and telling the user "not installed" when the truth is
    "your settings file has a trailing comma in it" sends them to run an
    install that will refuse for the same reason.

    False for a file that is absent or that cannot be opened at all: neither is
    damage we can claim to have seen.

    A file that is not UTF-8 counts as damage. It is present, it was opened,
    and it is not readable JSON, which is exactly what this answers. It used to
    escape instead: ``UnicodeDecodeError`` is a ``ValueError``, so neither
    handler below caught it, and both callers run this inside ``doctor()``. The
    result was not a crash -- ``repo_checks`` catches a raising ``doctor()`` --
    but something quieter and worse: a cp1252 ``settings.json`` or
    ``hooks.json``, an ordinary thing to meet on Windows, rendered as a
    **passing** "Could not check" row, so the one file state this check exists
    to report was the one state it could not report.
    """
    if not config_path.exists():
        return False
    try:
        json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True
    except OSError:
        return False
    return False


def json_deep_equal(left: Any, right: Any) -> bool:
    """Deep equality that ignores mapping key order.

    Python's ``==`` on dicts already ignores key order, so this exists for the
    case ``==`` gets wrong for our purpose: it is the check that decides whether
    a write is needed at all, and it has to walk nested structures without being
    fooled by a numeric-tower or bool/int coincidence.

    Its caller is the **TOML** path, not the JSON one, which is worth saying
    plainly. JSON configs are rendered from a dict every time, so comparing the
    rendered text answers "would this write move the file" exactly. The TOML
    merge rewrites the source text and re-appends the table it owns, so the text
    legitimately differs on a re-run while the *document* is identical —
    comparing documents is the only way to see that the write is a no-op. It
    lives here rather than in ``toml_merge`` because the structure it walks is
    the JSON data model, which is what ``tomllib`` hands back.
    """
    # Strict on type, including across the numeric tower. The contract is "the
    # bytes would not move", and ``json.dumps`` renders 1 and 1.0 differently,
    # so treating them as equal would report ``unchanged`` for a write that
    # changes the file. ``True == 1`` in Python makes bool-vs-int the same trap.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if set(left) != set(right):
            return False
        return all(json_deep_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        if len(left) != len(right):
            return False
        return all(json_deep_equal(a, b) for a, b in zip(left, right, strict=True))
    return bool(left == right)


def dumps_config(data: dict) -> str:
    """Serialize exactly as every existing writer does: indent 2, trailing newline."""
    return json.dumps(data, indent=2) + "\n"


def atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Write *content* atomically, preserving what a plain write would preserve.

    The atomic mechanics are ``core.fsutils.atomic_write_text`` — temp file in
    the destination directory, then rename — rather than a second copy of the
    same eight lines. What this wrapper adds is the two things a temp-file
    rename silently loses that a plain ``write_text`` kept:

    * **Symlinks.** ``os.replace`` replaces the *link*; a plain write follows
      it. Dotfile setups do symlink ``.mcp.json``, so the link is resolved
      first and the write lands on its target.
    * **Permissions.** The temp file's mode is umask-derived, so replacing an
      existing file would quietly reset its bits. The prior mode is restored.

    Parent directories are **not** created. Every caller that needs one already
    creates it at the point where it knows the directory is legitimately
    absent; creating them here turned a missing repo path from a loud
    ``FileNotFoundError`` into a silently-created tree.

    *newline* is passed through rather than defaulted, because the two
    disciplines in this codebase are both deliberate: config files take the
    platform translation (``None``), and the marker-managed markdown files pass
    ``"\\n"`` explicitly so a repo-shared file does not change line endings
    depending on who ran the command. Getting this wrong is a whole-file diff.
    """
    from repowise.core.fsutils import atomic_write_text as _atomic_write_text

    target = Path(os.path.realpath(path)) if path.is_symlink() else path

    mode: int | None = None
    with contextlib.suppress(OSError):
        if target.exists():
            mode = stat.S_IMODE(target.stat().st_mode)

    _atomic_write_text(target, content, newline=newline)

    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(target, mode)


def write_json_config(path: Path, data: dict) -> FileAction:
    """Atomically write *data*, skipping the write when it would change nothing.

    Returns without writing when the file already holds exactly the bytes this
    call would produce, so a re-run reports
    :attr:`~..types.FileAction.UNCHANGED` instead of an update it did not make.

    The comparison is against the *translated* rendering, not the raw one.
    :func:`atomic_write_text` passes ``newline=None`` here, so on Windows this
    writer emits ``\\r\\n`` where :func:`dumps_config` produced ``\\n``.
    Comparing the untranslated text would call every Windows re-run an update;
    normalising the file's line endings instead would go wrong the other way —
    a CRLF file on POSIX would compare equal and be left alone, where the
    unconditional write used to normalise it. Same trap as the marker block's,
    and the same answer: compare the bytes that would actually land.

    Byte-exact, so this is a narrower promise than "the data is already right":
    a file someone reindented, or whose top-level keys sit in another order,
    holds the same data and is still rewritten. That matches what the
    unconditional write did before and is the conservative direction — the
    action stays truthful either way, and the alternative is deciding that a
    file we would rewrite is "unchanged".
    """
    rendered = dumps_config(data)
    action = FileAction.CREATED
    if path.exists():
        action = FileAction.UPDATED
        on_disk = rendered.replace("\n", os.linesep) if os.linesep != "\n" else rendered
        with contextlib.suppress(OSError):
            if path.read_bytes() == on_disk.encode("utf-8"):
                return FileAction.UNCHANGED
    atomic_write_text(path, rendered)
    return action


def merge_server_entries(servers: dict, new_entry: dict) -> dict:
    """Deep-merge *new_entry* server definitions into *servers* in place.

    Generated ``command``/``args``/``description`` overwrite the stored values
    so a path or command change takes effect, but any other key the user added
    to the entry survives — most importantly an ``env`` block carrying BYOK
    provider keys. A shallow ``servers.update()`` would replace the whole entry
    and wipe ``env`` on every re-registration (issue #307).
    """
    for name, entry in new_entry.items():
        current = servers.get(name)
        if isinstance(current, dict) and isinstance(entry, dict):
            merged_entry = dict(current)
            merged_entry.update(entry)
            servers[name] = merged_entry
        else:
            servers[name] = entry
    return servers
