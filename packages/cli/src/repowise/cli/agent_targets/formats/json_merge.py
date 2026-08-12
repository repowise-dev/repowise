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
from pathlib import Path
from typing import Any

import click


def load_json_object(config_path: Path) -> dict:
    """Read a JSON object, refusing to silently replace bad content.

    Behaviourally identical to the long-standing ``mcp_config.load_existing_config``
    it replaces, down to the message text, because those messages are what
    users have seen and what the tests assert.
    """
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid JSON. "
            "Fix or remove it and retry; no changes were written."
        ) from exc
    except OSError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file could not be read. "
            "Fix the file permissions and retry; no changes were written."
        ) from exc
    if not isinstance(existing, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: existing file must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
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


def json_deep_equal(left: Any, right: Any) -> bool:
    """Deep equality that ignores mapping key order.

    Python's ``==`` on dicts already ignores key order, so this exists for the
    case ``==`` gets wrong for our purpose: it is the check that decides whether
    a write is needed at all, and it has to walk nested structures without being
    fooled by a list/dict type coincidence. Used to return
    :attr:`~..types.FileAction.UNCHANGED` instead of rewriting a file whose
    bytes would not move.
    """
    if type(left) is not type(right) and not (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
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


def backup_unparseable(config_path: Path) -> Path | None:
    """Copy an unparseable file to ``<path>.backup`` before it is overwritten.

    Only for paths where the caller intends to overwrite regardless. Where the
    caller can decline instead, declining is better and this is not used.
    """
    backup = config_path.with_suffix(config_path.suffix + ".backup")
    try:
        backup.write_bytes(config_path.read_bytes())
        return backup
    except OSError:
        return None


def atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Write *content* via ``<path>.tmp.<pid>`` then rename.

    A crash or a full disk mid-write leaves the original file intact instead of
    truncated. ``os.replace`` is the atomic rename on both POSIX and Windows.

    *newline* is passed through rather than defaulted, because the two
    disciplines in this codebase are both deliberate: config files take the
    platform translation (``None``), and the marker-managed markdown files pass
    ``"\\n"`` explicitly so a repo-shared file does not change line endings
    depending on who ran the command. Getting this wrong is a whole-file diff.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8", newline=newline)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def write_json_config(path: Path, data: dict) -> None:
    """Atomically write *data* in the repo's standard JSON config shape."""
    atomic_write_text(path, dumps_config(data))


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
