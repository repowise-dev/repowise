"""A Kotlin or Swift property initialised with a constructor types its calls (#3752).

``private val store = Store()`` / ``let store = Store()`` at class scope is a
field of type ``Store``, as the spelled-out ``val store: Store = Store()`` is.
A declaration in code that runs at class scope without being a method, an
``init`` block or a lambda / closure a property holds, is a local of that block
and never a field.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

_KOTLIN = (
    "class Store {\n    fun current() {}\n}\n"
    "class Builder {\n    fun build(): Other = Other()\n}\n"
    "class Other {\n    fun current() {}\n}\n"
    "class Use {\n{decl}\n    fun go() {\n        store.current()\n    }\n}\n"
)
_SWIFT = (
    "class Store {\n    func current() {}\n}\n"
    "class Builder {\n    func build() -> Other { return Other() }\n}\n"
    "class Other {\n    func current() {}\n}\n"
    "class Use {\n{decl}\n    func go() {\n        store.current()\n    }\n}\n"
)
_SOURCES = {"kotlin": ("a.kt", _KOTLIN), "swift": ("a.swift", _SWIFT)}


def _callees(tmp_path: Path, language: str, text: str) -> set[str]:
    """The ``current`` methods the file's calls reach."""
    path = _SOURCES[language][0]
    (tmp_path / path).write_text(text, encoding="utf-8")
    info = FileInfo(
        path=path,
        abs_path=str(tmp_path / path),
        language=language,
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = {path: parse_file(info, text.encode("utf-8"))}
    resolver = CallResolver(parsed, {path: set()}, repo_path=str(tmp_path))
    return {
        rc.callee_id
        for rc in resolver.resolve_file(path, parsed[path].calls)
        if rc.callee_id.endswith("::current")
    }


def _current_callees(tmp_path: Path, language: str, decl: str) -> set[str]:
    return _callees(tmp_path, language, _SOURCES[language][1].replace("{decl}", decl))


@pytest.mark.parametrize(
    ("language", "decl"),
    [
        ("kotlin", "    private val store = Store()"),
        ("kotlin", "    var store = Store()"),
        ("swift", "    let store = Store()"),
        ("swift", "    var store = Store()"),
    ],
)
def test_a_constructed_property_types_its_calls(tmp_path: Path, language: str, decl: str) -> None:
    path = _SOURCES[language][0]
    assert _current_callees(tmp_path, language, decl) == {f"{path}::Store::current"}


@pytest.mark.parametrize(
    ("language", "decl"),
    [
        # A chain is not a construction, at class scope as in a body.
        ("kotlin", "    private val store = Builder().build()"),
        ("swift", "    let store = Builder().build()"),
        # A local of a block that runs at class scope is no field.
        ("kotlin", "    val handler = { val store = Store() }"),
        ("kotlin", "    val handler = {\n        val store = Store()\n    }"),
        ("kotlin", "    init {\n        val store = Store()\n    }"),
        (
            "swift",
            "    lazy var h: Int = {\n        let store = Store()\n        return 1\n    }()",
        ),
    ],
)
def test_no_field_without_a_class_scope_construction(
    tmp_path: Path, language: str, decl: str
) -> None:
    assert _current_callees(tmp_path, language, decl) == set()


def test_a_primary_constructor_property_is_still_a_field(tmp_path: Path) -> None:
    text = _KOTLIN.replace("class Use {\n{decl}\n", "class Use(private val store: Store) {\n")
    assert _callees(tmp_path, "kotlin", text) == {"a.kt::Store::current"}


def test_a_local_still_shadows_the_property(tmp_path: Path) -> None:
    text = _KOTLIN.replace("{decl}", "    private val store = Store()").replace(
        "        store.current()", "        val store = Other()\n        store.current()"
    )
    assert _callees(tmp_path, "kotlin", text) == {"a.kt::Other::current"}
