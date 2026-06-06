"""Regex import extraction for Elixir.

Captured forms (all compile-time module references — ``use`` injects code,
so it is an edge like the rest):

    alias Foo.Bar
    alias Foo.Bar, as: B
    alias Foo.{Bar, Baz.Qux}
    import Foo.Bar
    require Foo.Bar
    use Foo.Bar, option: :x

Skipped: ``__MODULE__``-relative aliases (self-references), Erlang-atom
modules (``import :math`` — cross-runtime, no Elixir file to resolve to).
"""

from __future__ import annotations

import re

from ..models import Import

# Module path after the directive; an optional `.{…}` brace group expands to
# one import per member. `[^}]` deliberately crosses newlines — multi-line
# brace groups are idiomatic mix formatting.
_DIRECTIVE_RE = re.compile(
    r"^[ \t]*(alias|import|require|use)[ \t]+"
    r"([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)"
    r"(\.\{[^}]*\})?",
    re.M,
)

_MEMBER_RE = re.compile(r"[A-Z][A-Za-z0-9_.]*")


def extract_elixir_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _DIRECTIVE_RE.finditer(text):
        directive, base, brace = match.group(1), match.group(2), match.group(3)
        modules = (
            [f"{base}.{member}" for member in _MEMBER_RE.findall(brace)] if brace else [base]
        )
        raw = match.group(0).split("\n", 1)[0].strip()
        for module in modules:
            if module in seen:
                continue
            seen.add(module)
            imports.append(
                Import(
                    raw_statement=raw,
                    module_path=module,
                    imported_names=["*"] if directive == "import" else [],
                    is_relative=False,
                    resolved_file=None,
                )
            )
    return imports
