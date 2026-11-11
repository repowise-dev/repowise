"""Render the setting tables in ``docs/reference/CONFIG.md`` from the settings registry.

Every key and environment variable repowise reads is declared once, in
``repowise.core.settings``. The tables in CONFIG.md are rendered from those
declarations between marker pairs, one pair per block::

    <!-- BEGIN GENERATED settings:distill (python scripts/gen_config_docs.py) -->
    ...
    <!-- END GENERATED settings:distill -->

Everything outside the markers is hand-written prose and is never touched. A
user-facing setting that no block claims is an error, so a new key cannot ship
undocumented; internal settings are left out.

Render discipline, the same as ``scripts/gen_agent_matrix.py``: registry order,
LF newlines, no timestamp or version in the output.

Run ``python scripts/gen_config_docs.py`` to write, ``--check`` to report drift
without writing. ``tests/unit/settings/test_config_docs.py`` runs the check.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "reference" / "CONFIG.md"
COMMAND = "python scripts/gen_config_docs.py"

for _pkg in ("core",):
    _src = ROOT / "packages" / _pkg / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

if TYPE_CHECKING:
    from repowise.core.settings import Setting

_BEGIN = re.compile(r"^<!-- BEGIN GENERATED settings:(?P<name>[a-z0-9_-]+) .*-->$")
_END = "<!-- END GENERATED settings:{name} -->"


@dataclass(frozen=True)
class Block:
    """One generated table: which settings it holds and how their keys read."""

    name: str
    claims: Callable[[Setting], bool]
    #: Removed from the front of each key, so a block's table reads ``enabled``
    #: under its ``distill:`` heading.
    strip: str = ""
    #: An environment-variable table: rows are variables, not file keys.
    env: bool = False


def _in_file(s: Setting) -> bool:
    return s.scope != "env"


def _top_level(s: Setting) -> bool:
    return (
        _in_file(s)
        and s.path == ".repowise/config.yaml"
        and ("." not in s.key or s.key.startswith(("forges.", "changes.")))
    )


def _block(name: str, prefix: str | None = None) -> Block:
    prefix = prefix if prefix is not None else f"{name}."
    return Block(name, lambda s: _in_file(s) and s.key.startswith(prefix), strip=prefix)


def _env_area(area: str) -> Block:
    return Block(f"env-{area}", lambda s: s.env is not None and s.area == area, env=True)


#: The variable tables, one per area that has a user-facing variable. Every
#: area is listed, so a first variable in a new place asks for its region.
_ENV_AREA_ORDER = (
    "generation",
    "providers",
    "indexing",
    "mcp",
    "hooks",
    "server",
    "account",
    "editor",
    "attribution",
    "decisions",
    "distill",
    "events",
    "health",
)

#: Every generated table, in the order the file shows them. A setting may sit in
#: one key table and, when a variable sets it, in its area's variable table.
BLOCKS: tuple[Block, ...] = (
    Block("config-yaml", _top_level),
    _block("generation_context"),
    _block("distill"),
    _block("hooks"),
    _block("mcp"),
    _block("editor_files"),
    Block(
        "decisions",
        lambda s: (
            _in_file(s)
            and s.key.startswith("decisions.")
            and not s.key.startswith("decisions.sources.")
        ),
        strip="decisions.",
    ),
    _block("decisions-sources", "decisions.sources."),
    _block("coverage"),
    _block("security"),
    _block("attribution"),
    _block("agent_provenance"),
    _block("events"),
    _block("refactoring"),
    _block("assertions"),
    _block("tests"),
    Block("platform-json", lambda s: s.path == "~/.repowise/platform.json"),
    Block("provider-endpoints", lambda s: _in_file(s) and s.key.endswith(".base_url")),
    *(_env_area(area) for area in _ENV_AREA_ORDER),
)

#: Areas whose top-level keys lead the ``config.yaml`` table, in this order.
_TOP_AREA_ORDER = ("generation", "indexing", "editor", "health")


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _code(text: str) -> str:
    return f"`{text}`"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    return str(value)


def default_cell(s: Setting, *, as_env: bool = False) -> str:
    """The default as a reader writes it: YAML in a key table, words for a variable."""
    value = s.default
    if as_env:
        if value is None or value == "" or value == []:
            return "unset"
        if s.type == "bool":
            return "on" if value else "off"
    if value is None:
        return "unset"
    if isinstance(value, (list, tuple)):
        return _code("[" + ", ".join(_yaml_scalar(v) for v in value) + "]")
    if isinstance(value, dict):
        inner = ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in value.items())
        return _code("{" + inner + "}")
    if value == "":
        return _code('""')
    return _code(_yaml_scalar(value))


def values_cell(s: Setting, *, as_env: bool = False) -> str:
    bounds = _bounds(s)
    if bounds:
        return f"{bounds} (clamped)" if s.out_of_range == "clamp" else bounds
    if s.choices:
        return ", ".join(_code(c) for c in s.choices)
    if as_env and s.type == "bool" and s.env is not None:
        return _bool_spellings(s)
    return ""


def _bool_spellings(s: Setting) -> str:
    """What turns a switch variable on, in the way its reader parses it."""
    assert s.env is not None
    if s.env.false_values is not None:
        spellings = sorted(v for v in s.env.false_values if v)
        lead = "on unless " if s.default else "on when set, unless "
        return lead + ", ".join(_code(v) for v in spellings)
    from repowise.core.settings import TRUTHY

    truthy = s.env.true_values if s.env.true_values is not None else TRUTHY
    return ", ".join(_code(v) for v in sorted(truthy))


def _bounds(s: Setting) -> str:
    low, high = s.minimum, s.maximum
    if low is not None and high is not None:
        return f"{_number(low)}-{_number(high)}"
    if low is not None:
        return f"{'>' if s.exclusive_minimum else '>='} {_number(low)}"
    if high is not None:
        return f"<= {_number(high)}"
    return ""


def description_cell(s: Setting) -> str:
    """The description, followed by how the default is decided when it varies."""
    return f"{s.description} {s.default_note}" if s.default_note else s.description


def env_cell(s: Setting) -> str:
    if s.env is None:
        return ""
    return f"{_code(s.env.name)} ({'wins over the file' if s.env.wins else 'when the file is unset'})"


def config_key_cell(s: Setting) -> str:
    if s.scope == "env":
        return ""
    return f"{_code(s.key)} ({'this wins' if s.env and s.env.wins else 'the file wins'})"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _key_label(s: Setting, block: Block) -> str:
    if s.scope == "env" and s.env is not None and s.env.prefix:
        return _code(s.key)
    return _code(s.key[len(block.strip) :] if block.strip else s.key)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _table(header: list[str], rows: Iterable[list[str]]) -> list[str]:
    """A Markdown table, dropping optional columns no row fills.

    The first, second and fourth columns always show; ``Values`` and the
    variable or key column show only when some row has one.
    """
    rows = list(rows)
    keep = [
        i
        for i in range(len(header))
        if i in (0, 1, 3) or any(row[i] for row in rows)
    ]
    out = ["| " + " | ".join(header[i] for i in keep) + " |"]
    out.append("|" + "|".join("-" * (len(header[i]) + 2) for i in keep) + "|")
    for row in rows:
        out.append("| " + " | ".join(_escape(row[i]) for i in keep) + " |")
    return out


def render_block(block: Block, settings: Iterable[Setting]) -> list[str]:
    chosen = [s for s in settings if block.claims(s)]
    if block.name == "config-yaml":
        order = {area: i for i, area in enumerate(_TOP_AREA_ORDER)}
        chosen.sort(key=lambda s: order.get(s.area, len(order)))
    if block.env:
        return _table(
            ["Variable", "Default", "Values", "What it does", "Config key"],
            (
                [
                    _code(s.key if s.env.prefix else s.env.name),
                    default_cell(s, as_env=True),
                    values_cell(s, as_env=True),
                    description_cell(s),
                    config_key_cell(s),
                ]
                for s in chosen
                if s.env is not None
            ),
        )
    return _table(
        ["Key", "Default", "Values", "What it does", "Env var"],
        (
            [_key_label(s, block), default_cell(s), values_cell(s), description_cell(s), env_cell(s)]
            for s in chosen
        ),
    )


def user_facing() -> tuple[Setting, ...]:
    from repowise.core.settings import all_settings

    return all_settings(include_internal=False)


def _shown(s: Setting) -> bool:
    """A file key needs a key table; an environment-only setting, a variable table."""
    if any(b.claims(s) for b in BLOCKS if not b.env):
        return True
    return s.scope == "env" and any(b.claims(s) for b in BLOCKS if b.env)


def rendered_blocks() -> dict[str, list[str]]:
    """Every block's table lines, by block name; a block with no rows is left out.

    Raises ``ValueError`` naming any user-facing setting no block shows.
    """
    settings = user_facing()
    unclaimed = [s.key for s in settings if not _shown(s)]
    if unclaimed:
        raise ValueError(
            f"No table in CONFIG.md shows {', '.join(unclaimed)}. Add a block for it "
            "in scripts/gen_config_docs.py and its markers to CONFIG.md."
        )
    out: dict[str, list[str]] = {}
    for block in BLOCKS:
        lines = render_block(block, settings)
        if len(lines) > 2:
            out[block.name] = lines
    return out


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def render(text: str) -> str:
    """*text* with every generated region replaced by the registry's table.

    Raises ``ValueError`` for a marker with no end, a marker naming no block,
    or a block with rows and no marker in the file.
    """
    blocks = rendered_blocks()
    lines = text.split("\n")
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = _BEGIN.match(line)
        i += 1
        if not match:
            continue
        name = match.group("name")
        end = _END.format(name=name)
        try:
            j = lines.index(end, i)
        except ValueError:
            raise ValueError(f"CONFIG.md opens settings:{name} and never closes it") from None
        if name not in blocks:
            raise ValueError(f"CONFIG.md has a settings:{name} region but no block renders it")
        out.extend(blocks[name])
        out.append(end)
        seen.add(name)
        i = j + 1
    missing = sorted(set(blocks) - seen)
    if missing:
        raise ValueError(f"CONFIG.md has no region for {', '.join(missing)}")
    return "\n".join(out)


def _on_disk(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report drift and exit non-zero instead of writing"
    )
    args = parser.parse_args(argv)
    current = _on_disk(OUTPUT)
    try:
        expected = render(current)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rel = OUTPUT.relative_to(ROOT).as_posix()
    if current == expected:
        print("up to date" if args.check else "no changes")
        return 0
    if args.check:
        print(
            f"stale: {rel} disagrees with the settings registry. Run: {COMMAND}",
            file=sys.stderr,
        )
        return 1
    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(expected)
    print(f"wrote: {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
