"""A qualified recursive call keeps its self edge in every language.

The cfg build-variant fan-out once dropped any edge whose callee was its own
caller, which removed ordinary recursion too (``ClassName.f()`` inside ``f``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()

CASES = [
    (
        "python",
        "m.py",
        "class C:\n    @staticmethod\n    def walk(n):\n        return C.walk(n - 1) if n else 0\n",
        "m.py::C::walk",
    ),
    (
        "java",
        "A.java",
        "class A {\n  static int f(int n) { return n == 0 ? 1 : A.f(n - 1); }\n}\n",
        "A.java::A::f",
    ),
    (
        "rust",
        "lib.rs",
        "pub struct T;\nimpl T {\n    pub fn e(n: u32) -> u32 { T::e(n) }\n}\n",
        "lib.rs::T::e",
    ),
]


@pytest.mark.parametrize(
    ("language", "rel", "text", "symbol"), CASES, ids=[case[0] for case in CASES]
)
def test_a_recursive_call_is_a_self_edge(
    tmp_path: Path, language: str, rel: str, text: str, symbol: str
) -> None:
    path = tmp_path / rel
    path.write_text(text, encoding="utf-8")
    info = FileInfo(
        path=rel,
        abs_path=str(path),
        language=language,
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, text.encode("utf-8"))
    resolver = CallResolver({rel: parsed}, {rel: set()}, repo_path=str(tmp_path))
    edges = {(rc.caller_id, rc.callee_id) for rc in resolver.resolve_file(rel, parsed.calls)}
    assert edges == {(symbol, symbol)}
