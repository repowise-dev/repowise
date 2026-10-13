"""A bare Kotlin call reaches an extension only through an implicit receiver.

``fun AuthenticationConfig.digest(..)`` needs an ``AuthenticationConfig`` as
``this``: a bare ``digest(..)`` in a plain function body is something else (a
local function, a library call), while one inside a member or extension of
that type, or inside a lambda whose receiver a text scan cannot type, may be
the extension.
"""

from __future__ import annotations

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
            ("private operator fun Random.get(range: IntRange) = 1", "get", "Random"),
            ("fun <T : Any> Mono<T>.capture(x: Int)", "capture", "Mono"),
            ("fun String?.orBlank(): String", "orBlank", "String"),
            ("fun <T> T.also2(): T", "also2", None),
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
