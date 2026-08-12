"""A deliberately narrow TOML writer, scoped to whole-table upserts.

Absorbed from ``mcp_config``. It is not a general TOML serializer and should
not become one: it handles the value types our tables actually contain, and
raises on anything else rather than guessing at an encoding. codegraph made the
same call for the same reason — 254 lines serving one target beats a dependency
whose failure modes you inherit.

The merge strategy is regex table replacement over the *source text*, not a
parse-and-reserialize. That is what preserves the user's comments, key order and
formatting everywhere outside the one table we own. The cost is that the regex
only recognises the bare ``[table.name]`` spelling, so every merged result is
re-parsed before it is written: a user who spelled the same key differently
(quoted, or inline under a parent) slips past the regex, and appending our block
would produce a duplicate-key file. Re-parsing turns each of those into a clean
abort with the original file untouched.

Known wart, preserved deliberately for now: upserting a table strips it from
wherever it sat and re-appends it at the end, so two upserts in sequence swap
the two tables' order and leave a blank line at the top on the second run. It is
cosmetic, it settles after one run, and the baseline test records it by name.
Fixing it belongs with the deep-equal-before-write pass, not here, so that the
byte-level oracle has one thing to compare against at a time.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import click


def toml_value(value: object) -> str:
    """Encode a scalar or string list as TOML.

    Strings go through ``json.dumps``, whose escaping for basic strings is
    TOML-compatible. Anything outside the handful of types our tables hold
    raises, because a silent wrong encoding in a config file is far more
    expensive to diagnose than a crash here.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return f"[{', '.join(json.dumps(item) for item in value)}]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def table_block(table_name: str, values: dict[str, object]) -> str:
    """Render ``[table_name]`` and its key/value lines, without a trailing newline."""
    lines = [f"[{table_name}]"]
    lines.extend(f"{key} = {toml_value(value)}" for key, value in values.items())
    return "\n".join(lines)


def ensure_valid_toml(merged_text: str, config_path: Path) -> None:
    """Abort before writing if the regex merge produced invalid TOML."""
    try:
        tomllib.loads(merged_text)
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
