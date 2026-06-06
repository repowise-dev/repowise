"""Regex import extraction for Erlang.

Captured forms:

    -include("header.hrl").                  — importer-relative / include-dir
    -include_lib("app/include/header.hrl").  — library include (app-prefixed)
    -behaviour(gen_thing).  / -behavior(…)   — callback-module dependency
    mod:fun(Args)                            — module-qualified call

Module-qualified calls are the real dependency spine of Erlang's flat
namespace, but a bare regex over call sites would drown the graph in
stdlib references (``io:format``, ``lists:map``). They are therefore
emitted with a ``call:`` marker prefix and the resolver applies a strict
local-index-hit-or-drop policy: a qualified call only becomes an edge
when the target module is declared in this repo, and never mints an
external node.
"""

from __future__ import annotations

import re

from ..models import Import

_INCLUDE_RE = re.compile(r'^-(include|include_lib)\("([^"]+)"\)', re.M)
_BEHAVIOUR_RE = re.compile(r"^-behaviou?r\(([a-z][A-Za-z0-9_]*)\)", re.M)
# module:function( — both atoms lowercase-led; ?MACRO: and Var: forms don't match.
_QUALIFIED_CALL_RE = re.compile(r"\b([a-z][a-z0-9_]*):[a-z][a-z0-9_]*\(")
_COMMENT_RE = re.compile(r"%[^\n]*")
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def extract_erlang_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()

    for match in _INCLUDE_RE.finditer(text):
        directive, header = match.group(1), match.group(2)
        module = f"lib:{header}" if directive == "include_lib" else header
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0),
                module_path=module,
                imported_names=[],
                is_relative=directive == "include",
                resolved_file=None,
            )
        )

    for match in _BEHAVIOUR_RE.finditer(text):
        module = match.group(1)
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )

    # Strip comments and strings before the call scan — `%% lists:map(`
    # in a comment or a log format string must not mint an edge.
    stripped = _STRING_RE.sub('""', _COMMENT_RE.sub("", text))
    for match in _QUALIFIED_CALL_RE.finditer(stripped):
        module = f"call:{match.group(1)}"
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).rstrip("("),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports
