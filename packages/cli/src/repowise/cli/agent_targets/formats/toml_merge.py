"""A deliberately narrow TOML writer, scoped to whole-table upserts.

Absorbed from ``mcp_config``. It is not a general TOML serializer and should
not become one: it handles the value types our tables actually contain, and
raises on anything else rather than guessing at an encoding. A couple of hundred
lines serving one target beats a dependency whose failure modes you inherit.

The merge strategy is regex table replacement over the *source text*, not a
parse-and-reserialize. That is what preserves the user's comments, key order and
formatting everywhere outside the one table we own. The cost is that the regex
only recognises the bare ``[table.name]`` spelling, so every merged result is
re-parsed before it is written: a user who spelled the same key differently
(quoted, or inline under a parent) slips past the regex, and appending our block
would produce a duplicate-key file. Re-parsing turns each of those into a clean
abort with the original file untouched.

Upserting a table strips it from wherever it sat and re-appends it at the end,
so two upserts in sequence swap the two tables' order and the second run
produces different bytes for an identical document. :func:`write_if_changed` is
what stops that from reaching disk: it compares parsed documents rather than
text, so a re-run that would only reshuffle writes nothing at all.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from repowise.cli.errors import reasoned_error

from ..types import FileAction
from .json_merge import json_deep_equal


def toml_value(value: object) -> str:
    """Encode a scalar, list or inline table as TOML.

    Strings go through ``json.dumps``, whose escaping for basic strings is
    TOML-compatible. Anything outside the types handled here raises, because a
    silent wrong encoding in a config file is far more expensive to diagnose
    than a crash.

    Dicts render as **inline tables**, which is what makes preserving a user's
    keys possible at all: the one key a Codex MCP server entry is most likely
    to carry beyond ours is ``env``, and ``env`` is a table. Writing a whole
    generated table means re-rendering everything already in it, so a type this
    cannot encode is not a hypothetical — it is the standard case. Nested
    inline tables are legal TOML and the recursion produces them.

    Floats are handled for the same reason: they cost one line, and a timeout
    someone wrote as ``1.5`` is not an exotic thing to find in a config file.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        rendered = ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items())
        return f"{{ {rendered} }}" if rendered else "{}"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return f"[{', '.join(json.dumps(item) for item in value)}]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def table_block(table_name: str, values: dict[str, object]) -> str:
    """Render ``[table_name]`` and its key/value lines, without a trailing newline."""
    lines = [f"[{table_name}]"]
    lines.extend(f"{key} = {toml_value(value)}" for key, value in values.items())
    return "\n".join(lines)


def ensure_valid_toml(merged_text: str, config_path: Path) -> dict:
    """Abort before writing if the regex merge produced invalid TOML.

    Returns the parsed result, because every caller wants it: the parse has to
    happen anyway to validate, and the document it produces is what
    :func:`write_if_changed` compares against to decide whether the write is a
    no-op. Parsing twice to keep the return type ``None`` would be paying for
    the same work to throw it away.
    """
    try:
        return tomllib.loads(merged_text)
    except tomllib.TOMLDecodeError as exc:
        raise reasoned_error(
            f"Cannot update {config_path}: merging the repowise entry would produce "
            "invalid TOML (an existing entry may use a different key spelling). "
            "No changes were written.",
            reason="editor_config_unmergeable",
        ) from exc


def load_toml_document(config_path: Path, existing_text: str) -> dict:
    """Parse *existing_text*, refusing to overwrite a file we cannot read."""
    try:
        return tomllib.loads(existing_text)
    except tomllib.TOMLDecodeError as exc:
        raise reasoned_error(
            f"Cannot update {config_path}: existing file is not valid TOML. "
            "Fix or remove it and retry; no changes were written.",
            reason="editor_config_malformed",
        ) from exc


def require_table(doc: dict, key: str, config_path: Path, label: str) -> dict | None:
    """Fetch ``doc[key]`` insisting it is a table, or ``None`` when absent."""
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise reasoned_error(
            f"Cannot update {config_path}: [{label}] must be a TOML table. "
            "Fix or remove it and retry; no changes were written.",
            reason="editor_config_malformed",
        )
    return value


def replace_table(existing_text: str, table_name: str, block: str) -> str:
    """Strip *table_name* from *existing_text* and re-append *block*.

    Matches the table header at a line start through to the next header or end
    of input, so sibling tables and everything between them survive verbatim.
    """
    table_re = re.compile(
        r"(?ms)^\s*\[" + re.escape(table_name) + r"\]\s*\n.*?(?=^\s*\[|\Z)",
    )
    merged_text = table_re.sub("", existing_text).rstrip()
    return f"{merged_text}\n\n{block}\n" if merged_text else f"{block}\n"


#: A line that is blank, or holds nothing but a comment.
#:
#: TOML has no way to say who a trailing comment belongs to. A run of them
#: between our table and whatever follows could be annotating either. On the
#: destructive verb the tie has to break towards the user, so these lines are
#: never part of what we remove.
_TRAILING_NOISE = re.compile(r"^[ \t]*(#.*)?$")


def remove_table(existing_text: str, table_name: str) -> str:
    """Strip *table_name* from *existing_text*, keeping every comment.

    Shares :func:`replace_table`'s regex for finding the table, and then does
    something that function does not: **peels the trailing comments and blank
    lines back off the match before deleting it.**

    That step is the whole function. The shared pattern runs to the next table
    header, and ``\\s`` does not match a ``#``, so a comment line never ends the
    span. On the merge path the consequence is a lost comment. On this path it
    was far worse: everything from our header to the next live header was
    consumed, so a file whose remainder was a commented-out server and the
    user's notes came back empty, and the caller read empty as "this file was
    ours" and deleted it. An empty parse is not an empty file, and an emptiness
    test is worthless when the thing being tested has already eaten the
    evidence.

    Comments *inside* the table, annotating our own keys, go with it. Only the
    trailing run is preserved, because only the trailing run can belong to
    something else.

    The bare ``[table.name]`` spelling is still the only one recognised, so a
    table written as a quoted key or nested inline under its parent survives
    silently. Callers must re-parse the result and confirm the key is gone
    rather than trusting the returned text.
    """
    table_re = re.compile(
        r"(?ms)^\s*\[" + re.escape(table_name) + r"\]\s*\n.*?(?=^\s*\[|\Z)",
    )

    def _keep_trailing_noise(match: re.Match[str]) -> str:
        lines = match.group(0).splitlines(keepends=True)
        cut = len(lines)
        while cut > 0 and _TRAILING_NOISE.match(lines[cut - 1].rstrip("\r\n")):
            cut -= 1
        return "".join(lines[cut:])

    remaining = table_re.sub(_keep_trailing_noise, existing_text)
    if not remaining.strip():
        return ""
    # The file's own ending, not the platform's: this is a config the user owns
    # and a rewritten last line is a diff on every Windows checkout.
    newline = "\r\n" if "\r\n" in existing_text else "\n"
    return remaining.rstrip("\r\n \t") + newline


def remove_key_line(existing_text: str, table_name: str, key: str) -> str:
    """Delete one ``key = ...`` line from *table_name*, leaving the rest alone.

    The surgical alternative to upserting the table without the key, and the
    right tool whenever the table is the user's rather than ours.
    ``[features]`` is Codex's own, and re-rendering it to drop one key had two
    failure modes that a single-line delete simply does not have: the narrow
    serializer raises on any value type it cannot encode, so a user with
    ``retries = [1, 2, 3]`` got a ``TypeError`` mid-uninstall; and
    :func:`replace_table` rebuilds the table from the parsed dict, so every
    comment inside it was dropped and the whole table moved to the end of the
    file.

    Only a single-line ``key = value`` is removed. A value continued across
    lines is left entirely in place, and the caller sees that when it re-parses
    and finds the key still there.

    That refusal is the load-bearing part. Deleting the first line of

    .. code-block:: toml

        hooks = [
          "a",
        ]

    orphans the rest into ``[features]\\n  "a",\\n]``, which is not TOML at all.
    The caller's own validation then raised, from a helper whose message says
    "no changes were written", by which point the server table had been written
    out of the file and the hooks file deleted. A removal that cannot see where
    a value ends must decline rather than cut at the first newline.
    """
    table_re = re.compile(
        r"(?ms)^\s*\[" + re.escape(table_name) + r"\]\s*\n.*?(?=^\s*\[|\Z)",
    )
    key_re = re.compile(r"(?m)^[ \t]*" + re.escape(key) + r"[ \t]*=([^\n]*)\r?\n?")

    def _drop(match: re.Match[str]) -> str:
        head, _, body = match.group(0).partition("\n")
        found = key_re.search(body)
        if found is None or not _value_is_complete(found.group(1)):
            return match.group(0)
        # A line reading ``hooks = true`` inside an earlier key's multi-line
        # string is text, not a key. Cutting it silently edited the user's
        # value and left a file that still parsed, so nothing downstream
        # noticed. An odd number of triple quotes before the match means we are
        # inside one.
        before = body[: found.start()]
        if before.count('"""') % 2 or before.count("'''") % 2:
            return match.group(0)
        return f"{head}\n{before}{body[found.end() :]}"

    return table_re.sub(_drop, existing_text, count=1)


def _value_is_complete(value: str) -> bool:
    """Whether *value* is a whole TOML value rather than the head of one.

    Counts brackets and braces outside of strings, and refuses an unterminated
    string or a multi-line basic string opener. Deliberately conservative: any
    shape it is unsure about is reported incomplete, because the only cost of a
    false negative is a key left in place and reported honestly, while a false
    positive writes a file that no longer parses.
    """
    if '"""' in value or "'''" in value:
        return False
    depth = 0
    quote: str | None = None
    escaped = False
    for char in value:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "#":
            break
    return quote is None and depth == 0


def table_is_bare(existing_text: str, table_name: str) -> bool:
    """True when *table_name* holds no keys and no comments, only its header.

    The safe precondition for removing a table wholesale: nothing of the user's
    is inside it, so nothing of the user's can be lost with it.
    """
    table_re = re.compile(
        r"(?ms)^\s*\[" + re.escape(table_name) + r"\]\s*\n(.*?)(?=^\s*\[|\Z)",
    )
    match = table_re.search(existing_text)
    if match is None:
        return False
    return all(
        _TRAILING_NOISE.match(line.rstrip("\r\n")) and not line.strip().startswith("#")
        for line in match.group(1).splitlines()
    )


def write_if_changed(
    config_path: Path,
    merged_text: str,
    merged_doc: dict,
    existing_doc: dict | None,
) -> FileAction:
    """Write *merged_text* only when it would change what the file means.

    The comparison is between **documents**, not text, and that is the whole
    point. :func:`replace_table` strips the table it owns and re-appends it at
    the end, so upserting two tables in sequence swaps their order and the
    second run produces different bytes for an identical document. Comparing
    text would call that an update, write it, and swap them back — which is
    exactly the drift ``.codex/config.toml`` accumulated on every second
    ``init`` before this check existed.

    *existing_doc* is ``None`` when the file is new.

    One consequence worth naming: because the check is on meaning rather than
    bytes, a file that already says the right thing is left exactly as the user
    has it — including its line endings, which the old unconditional rewrite
    would have normalised. Leaving a user's config untouched is the better
    default, and nothing downstream reads this file's newlines.
    """
    if existing_doc is not None and json_deep_equal(merged_doc, existing_doc):
        return FileAction.UNCHANGED
    action = FileAction.UPDATED if existing_doc is not None else FileAction.CREATED
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged_text, encoding="utf-8")
    return action
