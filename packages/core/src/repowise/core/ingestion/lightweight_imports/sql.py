"""Regex import extraction for dbt SQL models.

dbt models are ``.sql`` files with an explicit import system rendered by
Jinja at compile time:

- ``{{ ref('model') }}`` / ``{{ ref('package', 'model') }}`` — model-to-model
  dependency (the two-argument form names a package first).
- ``{{ source('schema', 'table') }}`` — dependency on a table declared in a
  sources yml, by definition outside the project.

Plain (non-dbt) SQL files contain neither form, so extraction self-gates:
no Jinja, no imports. ``source()`` targets are encoded as
``source:<schema>.<table>`` module paths; the resolver turns them into
typed ``external:source:`` nodes so the graph shows the warehouse boundary.
"""

from __future__ import annotations

import re

from ..models import Import

# ref('model') / ref('pkg', 'model') inside a Jinja expression ({{ … }}) or
# statement ({% set x = ref('y') %}). The opening delimiter is required —
# a bare ref() call in plain SQL is a UDF, not a dbt dependency — but the
# closing delimiter is not: refs are routinely piped ({{ ref('x') | … }})
# or wrapped in further calls. Trailing kwargs (version=2) are tolerated.
_REF_RE = re.compile(
    r"\{[\{%][^{}]*?\bref\(\s*['\"]([^'\"]+)['\"]"
    r"(?:\s*,\s*['\"]([^'\"]+)['\"])?"
)

_SOURCE_RE = re.compile(r"\{[\{%][^{}]*?\bsource\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]")


def extract_dbt_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()

    for match in _REF_RE.finditer(text):
        first, second = match.group(1), match.group(2)
        # Two positional strings = ref('package', 'model').
        module = f"{first}.{second}" if second else first
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).split("\n", 1)[0].strip(),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )

    for match in _SOURCE_RE.finditer(text):
        module = f"source:{match.group(1)}.{match.group(2)}"
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).split("\n", 1)[0].strip(),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )

    return imports
