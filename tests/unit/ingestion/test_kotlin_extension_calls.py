"""A bare Kotlin call reaches an extension only through an implicit receiver.

``fun AuthenticationConfig.digest(..)`` needs an ``AuthenticationConfig`` as
``this``: a bare ``digest(..)`` in a plain function body is something else (a
local function, a library call), while one inside a member or extension of
that type, or inside a lambda whose receiver a text scan cannot type, may be
the extension.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.lang_helpers.kotlin_lambdas import in_lambda
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.type_names import kotlin_extension_receiver

SOURCE = """\
package app

fun Config.digest(name: String) = name

class Config {
    fun member() {
        digest("a")
    }
}

fun Config.sibling() {
    digest("b")
}

fun plain(bytes: ByteArray) {
    digest("c")
}

fun scoped() {
    configure {
        digest("d")
    }
}

typealias Cfg = Config

open class Base {
    inner class In {
        fun f() {
            based("e")
        }
    }
}

fun Base.based(name: String) = name
fun Cfg.viaAlias(name: String) = name
fun List<String>.firstTwo(name: String) = name

class UsesAlias : Base() {
    fun k() {
        viaAlias("f")
    }
}

class MyList : ArrayList<String>() {
    fun h() {
        firstTwo("g")
    }
}
"""


def _callees(tmp_path: Path, caller: str) -> set[str]:
    path = tmp_path / "App.kt"
    path.write_text(SOURCE, encoding="utf-8")
    info = FileInfo(
        path="App.kt",
        abs_path=str(path),
        language="kotlin",
        size_bytes=len(SOURCE),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = {"App.kt": parse_file(info, SOURCE.encode("utf-8"))}
    resolver = CallResolver(parsed, {"App.kt": set()}, repo_path=str(tmp_path))
    return {
        rc.callee_id
        for rc in resolver.resolve_file("App.kt", parsed["App.kt"].calls)
        if rc.caller_id == f"App.kt::{caller}"
    }


@pytest.mark.parametrize("caller", ["Config::member", "sibling", "scoped"])
def test_a_receiver_in_scope_keeps_the_extension(tmp_path: Path, caller: str) -> None:
    assert _callees(tmp_path, caller) == {"App.kt::digest"}


def test_a_plain_body_has_no_receiver_for_it(tmp_path: Path) -> None:
    assert _callees(tmp_path, "plain") == set()


@pytest.mark.parametrize(
    ("caller", "callee"),
    [
        ("In::f", "App.kt::based"),  # an inner class sees its outer `this`
        ("UsesAlias::k", "App.kt::viaAlias"),  # an alias receiver is not refused
        ("MyList::h", "App.kt::firstTwo"),  # nor a library one
    ],
)
def test_an_untyped_or_outer_receiver_keeps_the_extension(
    tmp_path: Path, caller: str, callee: str
) -> None:
    assert _callees(tmp_path, caller) == {callee}


class TestReceiverShape:
    @pytest.mark.parametrize(
        ("header", "name", "receiver"),
        [
            ("private operator fun Random.get(range: IntRange) = 1", "get", ("Random", "Random")),
            ("fun <T : Any> Mono<T>.capture(x: Int)", "capture", ("Mono", "Mono")),
            ("fun String?.orBlank(): String", "orBlank", ("String?", "String")),
            ("fun <T> T.also2(): T", "also2", ("T", None)),
            ("@Composable fun Card(x: a.B) {}", "Card", None),
            ("fun foo()\nfun Bar.foo()", "foo", None),
        ],
    )
    def test_kotlin_extension_receiver(self, header: str, name: str, receiver) -> None:
        assert kotlin_extension_receiver(header, name) == receiver


class TestLambdaScan:
    @pytest.mark.parametrize(
        ("source", "has_body", "inside"),
        [
            ("fun a() {\n  routing {\n    ", True, True),
            ("fun a() {\n  if (x) {\n    ", True, False),
            ('fun a() {\n  val s = "{"\n  ', True, False),
            ("fun a() = rule.collect(name) {\n  ", True, True),
            ("fun a(x: Int = 1): Int {\n  list.forEach { it }\n  ", True, False),
            ("plugins {\n  ", False, True),
        ],
    )
    def test_in_lambda(self, source: str, has_body: bool, inside: bool) -> None:
        assert in_lambda(source, has_body) is inside


TYPED = """\
package app

interface Helper {
    fun log(name: String)
}

open class Base
class Derived : Base()

class Owner {
    fun own() = 1
}

fun Helper.logScreen(name: String) = log(name)
fun Base.describe() = "base"
fun ByteArray.toText(): String = ""
fun List<String>.firstTwo() = take(2)
fun Owner.own() = 2
fun <T> T.also2(): T = this

class Uses(private val helper: Helper) {
    fun typed(derived: Derived, bytes: ByteArray, names: List<String>, maybe: Derived?) {
        helper.logScreen("x")
        derived.describe()
        bytes.toText()
        names.firstTwo()
        maybe?.describe()
    }

    fun refused(owner: Owner, derived: Derived) {
        owner.own()
        derived.also2()
    }
}
"""


def _typed_callees(tmp_path: Path, caller: str) -> set[str]:
    path = tmp_path / "Typed.kt"
    path.write_text(TYPED, encoding="utf-8")
    info = FileInfo(
        path="Typed.kt",
        abs_path=str(path),
        language="kotlin",
        size_bytes=len(TYPED),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = {"Typed.kt": parse_file(info, TYPED.encode("utf-8"))}
    heritage = {"Typed.kt::Derived": {"Typed.kt::Base"}}
    resolver = CallResolver(
        parsed, {"Typed.kt": set()}, repo_path=str(tmp_path), heritage_parents=heritage
    )
    return {
        rc.callee_id
        for rc in resolver.resolve_file("Typed.kt", parsed["Typed.kt"].calls)
        if rc.caller_id == f"Typed.kt::Uses::{caller}"
    }


def test_a_typed_receiver_reaches_the_extension_on_its_type_or_ancestor(tmp_path: Path) -> None:
    assert _typed_callees(tmp_path, "typed") == {
        "Typed.kt::logScreen",
        "Typed.kt::describe",  # declared on Derived's base
        "Typed.kt::toText",  # a library receiver
        "Typed.kt::firstTwo",
    }


def test_a_member_wins_and_a_generic_receiver_is_not_indexed(tmp_path: Path) -> None:
    assert _typed_callees(tmp_path, "refused") == {"Typed.kt::Owner::own"}


def _resolve_files(tmp_path: Path, files: dict[str, str], caller_file: str) -> set[str]:
    parsed = {}
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if not rel.endswith(".kt"):
            continue  # a build file, read off the disk
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language="kotlin",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parse_file(info, text.encode("utf-8"))
    # What the builder's import pass would record: `a.Foo` is the repo file
    # whose package `a` declares `Foo`; any other import stays outside.
    packages = {rel: re.match(r"package (\S+)", files[rel]) for rel in parsed}
    declaring = {
        f"{package[1]}.{symbol.name}": rel
        for rel, package in packages.items()
        if package
        for symbol in parsed[rel].symbols
    }
    for result in parsed.values():
        for imp in result.imports:
            imp.resolved_file = declaring.get(imp.module_path)
    resolver = CallResolver(parsed, {rel: set() for rel in parsed}, repo_path=str(tmp_path))
    return {rc.callee_id for rc in resolver.resolve_file(caller_file, parsed[caller_file].calls)}


_EXT = "src/a/Ext.kt"
_FOO = "package a\n\nclass Foo\n"


@pytest.mark.parametrize(
    ("ext", "caller", "callee"),
    [
        # The caller's own package sees the extension with no import.
        ("package a\n\nfun Foo.ext() = 1\n", "package a\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n", _EXT + "::ext"),
        # An import naming it, or its package's `*`, brings it in.
        ("package a\n\nfun Foo.ext() = 1\n", "package b\n\nimport a.Foo\nimport a.ext\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n", _EXT + "::ext"),
        ("package a\n\nfun Foo.ext() = 1\n", "package b\n\nimport a.*\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n", _EXT + "::ext"),
        # Another package that does not import it does not see it.
        ("package a\n\nfun Foo.ext() = 1\n", "package b\n\nimport a.Foo\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n", None),
        # `private` stays in its own file, even within the package.
        ("package a\n\nprivate fun Foo.ext() = 1\n", "package a\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n", None),
    ],
)  # fmt: skip
def test_an_extension_binds_only_where_kotlin_sees_it(
    tmp_path: Path, ext: str, caller: str, callee: str | None
) -> None:
    files = {"src/a/Foo.kt": _FOO, _EXT: ext, "src/b/Use.kt": caller}
    if caller.startswith("package a"):
        files = {"src/a/Foo.kt": _FOO, _EXT: ext, "src/a/Use.kt": caller}
    found = _resolve_files(tmp_path, files, next(f for f in files if f.endswith("Use.kt")))
    assert found == ({callee} if callee else set())


def test_a_same_named_type_in_another_package_is_not_extended(tmp_path: Path) -> None:
    files = {
        "src/a/Foo.kt": _FOO,
        _EXT: "package a\n\nfun Foo.ext() = 1\n",
        "src/b/Foo.kt": "package b\n\nclass Foo\n",
        "src/b/Use.kt": "package b\n\nfun use(foo: Foo) {\n    foo.ext()\n}\n",
    }
    assert _resolve_files(tmp_path, files, "src/b/Use.kt") == set()


def test_a_member_the_repo_cannot_see_wins(tmp_path: Path) -> None:
    """`Any` members and a builtin's own members beat any extension of their name."""
    source = (
        "package a\n\nclass Foo\n\n"
        "fun Foo.toString(x: Int) = 1\n"
        "fun List<String>.get(x: Long) = 1\n"
        "fun String.trimFoo() = 1\n\n"
        "fun use(foo: Foo, names: List<String>, name: String) {\n"
        "    foo.toString(1)\n    names.get(1L)\n    name.trimFoo()\n}\n"
    )
    found = _resolve_files(tmp_path, {"src/a/Use.kt": source}, "src/a/Use.kt")
    assert found == {"src/a/Use.kt::trimFoo"}


def test_a_builtin_typed_local_binds_no_class_method(tmp_path: Path) -> None:
    """`name: String` is typed now, and a repo class's same-named method is not it."""
    source = (
        "package a\n\nclass Text {\n    fun shout() = 1\n}\n\n"
        "fun use(name: String) {\n    name.shout()\n}\n"
    )
    assert _resolve_files(tmp_path, {"src/a/Use.kt": source}, "src/a/Use.kt") == set()


_USE = "fun use(foo: Foo) {\n    foo.ext()\n}\n"


def test_an_extension_in_another_build_is_not_seen(tmp_path: Path) -> None:
    """Same package name, but two Gradle builds side by side."""
    files = {
        "one/settings.gradle.kts": "",
        "two/settings.gradle.kts": "",
        "one/src/a/Foo.kt": _FOO,
        "one/src/a/Ext.kt": "package a\n\nfun Foo.ext() = 1\n",
        "two/src/a/Use.kt": "package a\n\n" + _USE,
    }
    assert _resolve_files(tmp_path, files, "two/src/a/Use.kt") == set()


@pytest.mark.parametrize(
    ("caller", "callee"),
    [("src/Use.kt", "src/Ext.kt::ext"), ("other/Use.kt", None)],
)
def test_with_no_package_only_the_extensions_directory_sees_it(
    tmp_path: Path, caller: str, callee: str | None
) -> None:
    """No package to compare, so the directory stands in for it (conservative)."""
    files = {
        "src/Foo.kt": "class Foo\n",
        "src/Ext.kt": "fun Foo.ext() = 1\n",
        caller: _USE,
    }
    assert _resolve_files(tmp_path, files, caller) == ({callee} if callee else set())


@pytest.mark.parametrize(
    ("extension_import", "callee"),
    [
        ("import lib.one.Request", "src/a/Ext.kt::ext"),
        ("import lib.two.Request", None),  # another library's `Request`
        ("", None),  # the package's own `Request`, not the caller's library one
    ],
)
def test_a_library_receiver_must_be_the_same_import(
    tmp_path: Path, extension_import: str, callee: str | None
) -> None:
    files = {
        "src/a/Ext.kt": f"package a\n\n{extension_import}\n\nfun Request.ext() = 1\n",
        "src/a/Use.kt": (
            "package a\n\nimport lib.one.Request\n\n"
            "fun use(request: Request) {\n    request.ext()\n}\n"
        ),
    }
    found = _resolve_files(tmp_path, files, "src/a/Use.kt")
    assert found == ({callee} if callee else set())


def test_an_aliased_library_import_leaves_the_name_to_the_repo(tmp_path: Path) -> None:
    """`import lib.Request as LibRequest` binds `LibRequest`; `Request` is still `a.Request`."""
    files = {
        "src/a/Request.kt": "package a\n\nclass Request\n",
        "src/a/Ext.kt": "package a\n\nimport lib.Request as LibRequest\n\nfun Request.ext() = 1\n",
        "src/a/Use.kt": "package a\n\nfun use(request: Request) {\n    request.ext()\n}\n",
    }
    assert _resolve_files(tmp_path, files, "src/a/Use.kt") == {"src/a/Ext.kt::ext"}
