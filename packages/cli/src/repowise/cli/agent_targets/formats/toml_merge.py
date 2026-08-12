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

import click

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
        raise click.ClickException(
            f"Cannot update {config_path}: merging the repowise entry would produce "
            "invalid TOML (an existing entry may use a different key spelling). "
            "No changes were written."
        ) from exc


def load_toml_document(config_path: Path, existing_text: str) -> dict:
    """Parse *existing_text*, refusing to overwrite a file we cannot read."""
    try:
        return tomllib.loads(existing_text)
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid TOML. "
            "Fix or remove it and retry; no changes were written."
        ) from exc


def require_table(doc: dict, key: str, config_path: Path, label: str) -> dict | None:
    """Fetch ``doc[key]`` insisting it is a table, or ``None`` when absent."""
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: [{label}] must be a TOML table. "
            "Fix or remove it and retry; no changes were written."
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
