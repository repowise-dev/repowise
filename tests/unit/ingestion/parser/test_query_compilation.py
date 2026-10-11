"""Every language's tree-sitter query must compile against its grammar.

Regression test for #2165: with `tree-sitter` 0.23.x/0.24.x installed, 13 of
the 27 language queries failed to compile (`Impossible pattern`), and
`_load_compiled_query` swallows that failure and returns `None` — so every
file in the affected languages silently indexed with zero symbols. This
walks the full language registry, compiles each language's query directly,
and asserts on the return value rather than expecting an exception, since
that's exactly what production does.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.parser import QUERIES_DIR, _load_compiled_query


def _languages_with_queries() -> list[str]:
    tags = []
    for spec in REGISTRY.all_specs():
        scm_name = spec.scm_file or f"{spec.tag}.scm"
        if (QUERIES_DIR / scm_name).exists():
            tags.append(spec.tag)
    return sorted(tags)


@pytest.mark.parametrize("lang", _languages_with_queries())
def test_query_compiles(lang: str) -> None:
    query = _load_compiled_query(lang)
    assert query is not None, f"{lang}: query failed to compile against its grammar"
