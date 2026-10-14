"""A Kotlin call on a value reaches its type's ``operator fun invoke``.

``getTopics()`` where ``getTopics`` is a property, a parameter or a local of a
type declaring ``operator fun invoke`` calls that ``invoke``. A function-typed
value, a type without the operator and a plain function keep their own
answers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

SOURCE = """\
package app

class GetTopicsUseCase {
    operator fun invoke(sortBy: Int = 0): List<String> = emptyList()
}

class Plain {
    fun invoke(): Int = 0
}

fun helper(): Int = 1

class TopicsViewModel(
    private val getTopics: GetTopicsUseCase,
    private val plain: Plain,
) {
    lateinit var logger: GetTopicsUseCase

    fun load() {
        getTopics(1)
    }

    fun log() {
        logger()
    }

    fun plainValue() {
        plain()
    }

    fun withCallback(onDone: () -> Unit) {
        onDone()
    }

    fun local() {
        val useCase: GetTopicsUseCase = GetTopicsUseCase()
        useCase()
    }

    fun function() {
        helper()
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
        if rc.caller_id == f"App.kt::TopicsViewModel::{caller}"
    }


@pytest.mark.parametrize("caller", ["load", "log"])
def test_a_typed_value_call_reaches_its_operator_invoke(tmp_path: Path, caller: str) -> None:
    assert _callees(tmp_path, caller) == {"App.kt::GetTopicsUseCase::invoke"}


def test_a_typed_local_call_reaches_its_operator_invoke(tmp_path: Path) -> None:
    assert "App.kt::GetTopicsUseCase::invoke" in _callees(tmp_path, "local")


def test_an_invoke_without_the_operator_is_not_called(tmp_path: Path) -> None:
    assert "App.kt::Plain::invoke" not in _callees(tmp_path, "plainValue")


def test_a_function_typed_value_has_no_repo_callee(tmp_path: Path) -> None:
    assert _callees(tmp_path, "withCallback") == set()


def test_a_plain_function_call_is_unchanged(tmp_path: Path) -> None:
    assert _callees(tmp_path, "function") == {"App.kt::helper"}
