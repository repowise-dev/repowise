"""Elixir import resolution (lightweight regex tier).

Resolution order:

1. Declared-module index — ``defmodule Foo.Bar`` scanned from every
   ``.ex``/``.exs`` file, with the Mix path convention
   (``lib/foo/bar.ex`` → ``Foo.Bar``, umbrella ``apps/<app>/lib/…``
   included) as the inverse fallback for files without declarations.
   Trailing segments are stripped progressively so ``alias Foo.Bar.Baz``
   still hits ``foo/bar.ex`` when ``Baz`` is a nested module.
2. Elixir/OTP standard library → dropped (no node), but only AFTER the
   local lookup misses — a repo may BE one of these libraries.
3. Everything else → external node.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_with_trailing_strip

if TYPE_CHECKING:
    from .context import ResolverContext

_DEFMODULE_RE = re.compile(r"^[ \t]*defmodule[ \t]+([A-Z][A-Za-z0-9_.]*)", re.M)

# First-segment match: Elixir stdlib + OTP-shipped applications. Checked
# only after the local index misses (D-034 policy shape).
_ELIXIR_STDLIB = frozenset(
    {
        "Access", "Agent", "Application", "Atom", "Base", "Behaviour", "Bitwise",
        "Calendar", "Code", "Config", "Date", "DateTime", "Duration", "DynamicSupervisor",
        "EEx", "Enum", "Enumerable", "Exception", "ExUnit", "File", "Float", "Function",
        "GenEvent", "GenServer", "IEx", "IO", "Inspect", "Integer", "JSON", "Kernel",
        "Keyword", "List", "Logger", "Macro", "Map", "MapSet", "Mix", "Module",
        "NaiveDateTime", "Node", "OptionParser", "PartitionSupervisor", "Path", "Port",
        "Process", "Protocol", "Range", "Record", "Regex", "Registry", "Stream",
        "String", "StringIO", "Supervisor", "System", "Task", "Time", "Tuple", "URI",
        "Version",
    }
)


def _path_to_module(path: str) -> str | None:
    """Mix convention inverse: ``[apps/<app>/]lib/foo/bar.ex`` → ``Foo.Bar``."""
    segments = path.rsplit(".", 1)[0].split("/")
    anchor = -1
    for i, seg in enumerate(segments[:-1]):  # last "lib" before the filename
        if seg == "lib":
            anchor = i
    if anchor == -1:
        return None
    tail = segments[anchor + 1 :]
    if not tail:
        return None
    return ".".join("".join(part.capitalize() for part in seg.split("_")) for seg in tail)


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_elixir_module_index",
        extensions=(".ex", ".exs"),
        declaration_re=_DEFMODULE_RE,
        path_to_module=_path_to_module,
    )


def resolve_elixir_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    index = _get_index(ctx)
    hit = lookup_with_trailing_strip(index, module_path)
    if hit and hit != importer_path:
        return hit
    if module_path.split(".", 1)[0] in _ELIXIR_STDLIB:
        return None
    if hit == importer_path:  # self-reference (alias of a sibling nested module)
        return None
    return f"external:{module_path}"
