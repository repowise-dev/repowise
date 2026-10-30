"""Kotlin calls under a prefix or postfix operator, or on a string literal, are call sites.

tree-sitter-kotlin binds ``!`` / ``-`` / ``!!`` before the call, so ``!g(1)``
arrives as ``(!g)(1)`` and ``!b.h(2)`` / ``a!!.h(3)`` as ``(!b).h(2)``; the
operand is still the callee or the receiver. A string literal receiver is a
``String``, so only an extension on ``String`` answers it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import STRING_LITERAL_RECEIVER, FileInfo
from repowise.core.ingestion.parser import parse_file

SOURCE = '''\
package app

class Account {
    fun valid(x: Int): Boolean = true
    fun again(): Account = this
}

class Text {
    fun shout(): Int = 1
}

fun allowed(x: Int): Boolean = true
fun String.slash(): Account = Account()

fun unary(maybe: Account?, account: Account) {
    if (!allowed(1)) return
    if (!account.valid(2)) return
    maybe!!.valid(3)
    !account.again().valid(4)
}

fun literal() {
    "/".slash()
    """raw""".slash()
    "a$x".slash().valid(5)
    "s".shout()
}
'''


def _parse(tmp_path: Path):
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
    return parse_file(info, SOURCE.encode("utf-8"))


def _edges(tmp_path: Path, caller: str) -> set[tuple[str, str]]:
    parsed = _parse(tmp_path)
    resolver = CallResolver({"App.kt": parsed}, {"App.kt": set()}, repo_path=str(tmp_path))
    return {
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("App.kt", parsed.calls)
        if rc.caller_id == f"App.kt::{caller}"
    }


def test_operator_wrapped_calls_are_sites(tmp_path: Path) -> None:
    sites = {(c.line, c.receiver_name, c.target_name) for c in _parse(tmp_path).calls}
    assert {
        (16, None, "allowed"),
        (17, "account", "valid"),
        (18, "maybe", "valid"),
        (19, "account", "again"),
        (19, None, "valid"),
    } <= sites


def test_operator_wrapped_calls_resolve(tmp_path: Path) -> None:
    assert {callee for callee, _ in _edges(tmp_path, "unary")} == {
        "App.kt::allowed",
        "App.kt::Account::valid",
        "App.kt::Account::again",
    }


def test_a_string_literal_receiver_is_a_string(tmp_path: Path) -> None:
    sites = {(c.line, c.receiver_name, c.target_name) for c in _parse(tmp_path).calls}
    assert (23, STRING_LITERAL_RECEIVER, "slash") in sites
    assert (24, STRING_LITERAL_RECEIVER, "slash") in sites
    # Only the extension on String answers; a repo class's same-named method does not.
    assert _edges(tmp_path, "literal") == {
        ("App.kt::slash", "receiver_extension_same_file"),
        ("App.kt::Account::valid", "return_type_same_file"),
    }
