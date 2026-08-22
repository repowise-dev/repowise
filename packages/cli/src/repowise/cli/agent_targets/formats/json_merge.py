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

import click

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


# ---------------------------------------------------------------------------
# Surgical (minimal-edit) JSON merge
#
# ``write_json_config`` renders the whole document from a dict, which is the
# right call for config we own. It is the wrong call for a tracked, repo-shared
# file such as the root ``.mcp.json`` that other tools also write into: the
# file is parsed to a dict and re-serialised end to end, so pre-existing,
# unrelated server entries come back reformatted (issue #1603). The function
# below performs a *minimal edit*: only the bytes of the entry we own are
# touched, everything else in the file stays byte-identical.
# ---------------------------------------------------------------------------


def _skip_ws(text: str, i: int, end: int) -> int:
    while i < end and text[i] in " \t\r\n":
        i += 1
    return i


def _find_object_member(
    text: str, obj_start: int, obj_end: int, key_target: str
) -> tuple[int, int, int, int] | None:
    """Locate a ``"key": value`` member inside the object spanning *text*.

    *obj_start* points at the ``{`` and *obj_end* just past its ``}``. Returns
    ``(key_start, key_end, value_start, value_end)`` for the member whose key
    equals *key_target*, or ``None`` when the key is not present. Strict JSON
    only — this is only ever called after :func:`load_json_object` has already
    validated the document, so the scan is free to be lax about errors.
    """
    decoder = json.JSONDecoder()
    i = obj_start + 1
    while True:
        i = _skip_ws(text, i, obj_end)
        if i >= obj_end or text[i] == "}":
            return None
        if text[i] == ",":
            i += 1
            continue
        key_start = i
        try:
            key, kend = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            return None
        i = _skip_ws(text, kend, obj_end)
        if i >= obj_end or text[i] != ":":
            return None
        i = _skip_ws(text, i + 1, obj_end)
        try:
            _, vend = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            return None
        if key == key_target:
            return key_start, kend, i, vend
        i = _skip_ws(text, vend, obj_end)


def _container_indent(text: str, obj_start: int, obj_end: int) -> str:
    """Return the whitespace prefix indenting the object's members.

    Derived from the whitespace preceding the matching closing brace, so a
    member inserted into the object is indented to sit with its siblings no
    matter the file's formatting. Falls back to ``"  "`` for one-line objects.
    """
    i = obj_end - 1
    j = i
    while j > obj_start and text[j] in " \t\r\n":
        j -= 1
    if j != i:
        _, after_newline = text[j:obj_end].rsplit("\n", 1)
        if after_newline.strip() == "":
            return after_newline
    return "  "


def _render_indented(value_text: str, base_indent: str) -> str:
    """Re-indent a ``json.dumps(..., indent=2)`` block so its lines sit at
    *base_indent* relative to the opening brace.

    ``json.dumps`` emits ``{``, then keys indented by two spaces each level,
    then a closing ``}`` at column 0 — all relative to the opening brace. To
    embed that block mid-document so the keys sit at *base_indent* and the
    closing brace at *close_indent*, every line after the first gets that
    amount of leading whitespace prepended.
    """
    lines = value_text.split("\n")
    if len(lines) <= 1:
        return value_text
    return lines[0] + "\n" + "\n".join(base_indent + line for line in lines[1:])


def _render_value_block(value: dict, member_indent: str) -> str:
    """Render a ``{ ... }`` value block whose members sit at *member_indent*.

    Produces ``{``, then each key at *member_indent* (two spaces past the
    member's own indent), then a closing ``}`` at *member_indent* — matching
    how the rest of the document lays out nested objects.
    """
    return _render_indented(json.dumps(value, indent=2), member_indent)


def _render_member(member: str, value: dict, member_indent: str) -> str:
    """Render a ``"key": { ... }`` member at *member_indent*.

    Deterministic — the same *member*/*value*/*member_indent* always yields the
    same bytes — which is what makes the upsert idempotent: replacing an
    existing value re-produces exactly what an insert produced, so a re-run
    reports ``UNCHANGED`` instead of churning the file.
    """
    return f"{member_indent}{json.dumps(member)}: {_render_value_block(value, member_indent)}"


def _insert_member(text: str, obj_start: int, obj_end: int, member_snippet: str) -> str | None:
    """Return *text* with *member_snippet* (a ``"key": value`` snippet)
    inserted into the object spanning *obj_start:obj_end*.

    Preserves every byte of the original object apart from the inserted member.
    Returns ``None`` when the object shape cannot be handled safely, so the
    caller leaves the file alone rather than risk corrupting it.
    """
    body = _skip_ws(text, obj_start + 1, obj_end)
    if body >= obj_end:
        return None
    if text[body] == "}":
        # Empty object ``{}`` → ``{ "key": value }``, closing brace indented
        # to match the container's members.
        return (
            text[:body]
            + "\n"
            + member_snippet
            + "\n"
            + _container_indent(text, obj_start, obj_end)
            + text[body:]
        )
    # Non-empty: walk to the end of the last member's value, then insert after
    # it, adding a comma separator unless a trailing comma is already present.
    decoder = json.JSONDecoder()
    i = obj_start + 1
    last_value_end: int | None = None
    trailing_comma = False
    while True:
        i = _skip_ws(text, i, obj_end)
        if i >= obj_end or text[i] == "}":
            break
        if text[i] == ",":
            i += 1
            continue
        try:
            _, kend = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            return None
        i = _skip_ws(text, kend, obj_end)
        if i >= obj_end or text[i] != ":":
            return None
        i = _skip_ws(text, i + 1, obj_end)
        try:
            _, vend = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            return None
        last_value_end = vend
        i = _skip_ws(text, vend, obj_end)
        trailing_comma = i < obj_end and text[i] == ","
        if trailing_comma:
            i += 1
    if last_value_end is None:
        return None
    insert_at = _skip_ws(text, last_value_end, obj_end)
    sep = "" if trailing_comma else ","
    # Append the separator immediately after the last value, then the new
    # member on its own line, then reuse the original whitespace that preceded
    # the closing brace so the brace keeps its existing indent.
    return (
        text[:last_value_end]
        + sep
        + "\n"
        + member_snippet
        + text[last_value_end:insert_at]
        + text[insert_at:]
    )


def merge_json_object_member(
    config_path: Path,
    container_key: str,
    member: str,
    new_value: dict,
) -> FileAction:
    """Surgically upsert one ``member`` into the ``container_key`` object of a
    strict-JSON file, preserving every byte outside that member.

    This is the minimal-edit writer for tracked, repo-shared files. Unlike
    :func:`write_json_config`, which re-renders the whole document from a dict
    (reformatting unrelated content on every run), this touches only the
    ``container_key.member`` value and its surrounding separator. Other servers
    a user configured, and the file's own formatting, are left byte-for-byte
    identical (issue #1603).

    Returns the :class:`FileAction` performed. Raises ``click.ClickException``
    (via :func:`load_json_object`) when the file exists but is not strict
    JSON, so JSONC/JSON5 files are left untouched. When ``container_key`` is
    absent it is created holding just the new member; when the file itself is
    absent it is created with only ``container_key``.
    """
    if not config_path.exists():
        return write_json_config(config_path, {container_key: {member: new_value}})

    original = config_path.read_text(encoding="utf-8")
    load_json_object(config_path)  # validate; raises on non-strict JSON

    root_start = _skip_ws(original, 0, len(original))
    if root_start >= len(original) or original[root_start] != "{":
        return FileAction.KEPT

    span = _find_object_member(original, root_start, len(original), container_key)

    if span is not None:
        _, _, cval_start, cval_end = span
        if original[cval_start] != "{":
            # container_key is present but not an object; leave the file alone
            # rather than guessing what a rewrite would mean.
            return FileAction.KEPT
        inner = _find_object_member(original, cval_start, cval_end, member)
        if inner is not None:
            _, _, vstart, vend = inner
            # Preserve user-added keys on the existing entry (e.g. an ``env``
            # block) while generated keys take the new values. Parse the stored
            # entry and shallow-merge the generated keys over it, so a user's
            # BYOK env survives re-registration (mirrors merge_server_entries,
            # issue #307). Then re-indent to the container's member indent and
            # re-render, which is byte-identical to what an insert produces.
            existing_entry = json.loads(original[vstart:vend])
            merged_entry = dict(existing_entry)
            merged_entry.update(new_value)
            member_indent = _container_indent(original, cval_start, cval_end) + "  "
            rendered = _render_member(member, merged_entry, member_indent)
            _, value_with_indent = rendered.split(": ", 1)
            edited = original[:vstart] + value_with_indent + original[vend:]
        else:
            member_indent = _container_indent(original, cval_start, cval_end) + "  "
            rendered_member = _render_member(member, new_value, member_indent)
            inserted = _insert_member(original, cval_start, cval_end, rendered_member)
            if inserted is None:
                return FileAction.KEPT
            edited = inserted
    else:
        # container_key is absent: create it holding just our member. The
        # container's closing brace sits at the root member indent, and its
        # (single) member at one level deeper, matching the document's layout.
        member_indent = _container_indent(original, root_start, len(original)) + "  "
        rendered_container = (
            f"{member_indent}{json.dumps(container_key)}: "
            f"{_render_value_block({member: new_value}, member_indent)}"
        )
        inserted = _insert_member(original, root_start, len(original), rendered_container)
        if inserted is None:
            return FileAction.KEPT
        edited = inserted

    if edited == original:
        return FileAction.UNCHANGED
    atomic_write_text(config_path, edited)
    return FileAction.UPDATED


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
