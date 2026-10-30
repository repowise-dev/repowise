"""Kotlin ``super.m()`` binds the nearest supertype's ``m``, not the caller's own override."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from tests.unit.ingestion.test_call_resolver_strategies import _parse_all

_BASE = "package a\n\nopen class Base {\n    open fun m() {}\n}\n"
_I = "package a\n\ninterface I {\n    fun m() {}\n}\n"


def _targets(tmp_path: Path, sub: str) -> list[tuple[str, str]]:
    files = {"a/Base.kt": _BASE, "a/I.kt": _I, "a/Sub.kt": sub}
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    parents = {"a/Sub.kt::Sub": {"a/Base.kt::Base", "a/I.kt::I"}}
    resolver = CallResolver(
        parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
    )
    calls = parsed["a/Sub.kt"].calls
    return [(rc.callee_id, rc.origin) for rc in resolver.resolve_file("a/Sub.kt", calls)]


def _sub(body: str) -> str:
    return f"package a\n\nclass Sub : Base(), I {{\n    override fun m() {{\n        {body}\n    }}\n}}\n"


def test_super_skips_the_callers_own_override(tmp_path: Path) -> None:
    assert _targets(tmp_path, _sub("super.m()")) == [("a/Base.kt::Base::m", "self_inherited")]


def test_a_qualified_super_reaches_the_named_supertype(tmp_path: Path) -> None:
    assert _targets(tmp_path, _sub("super<I>.m()")) == [("a/I.kt::I::m", "self_inherited")]


def test_a_labelled_super_names_no_call_site(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"a/Sub.kt": ("kotlin", _sub("super@Sub.m()"))})
    assert parsed["a/Sub.kt"].calls == []


def test_a_qualified_super_names_a_supertype_not_an_enclosing_class(tmp_path: Path) -> None:
    """``super<Base>`` in a class nested in a ``Base`` means the supertype's ``m``."""
    source = (
        "package a\n\nopen class Base {\n    open fun m() {}\n"
        "    class Inner : Base() {\n        override fun m() {\n"
        "            super<Base>.m()\n        }\n    }\n}\n"
    )
    parsed = _parse_all(tmp_path, {"a/Base.kt": ("kotlin", source)})
    parents = {"a/Base.kt::Base::Inner": {"a/Base.kt::Base"}}
    resolver = CallResolver(
        parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
    )
    calls = parsed["a/Base.kt"].calls
    assert [rc.callee_id for rc in resolver.resolve_file("a/Base.kt", calls)] == [
        "a/Base.kt::Base::m"
    ]
