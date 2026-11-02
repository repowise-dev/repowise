"""A Kotlin typed receiver reaches a member its type inherits from a supertype."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from tests.unit.ingestion.test_call_resolver_strategies import _parse_all


def _targets(
    tmp_path: Path, files: dict[str, str], parents: dict[str, set[str]], caller: str
) -> list[str]:
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    resolver = CallResolver(
        parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
    )
    return [rc.callee_id for rc in resolver.resolve_file(caller, parsed[caller].calls)]


_SINK = "package a\n\ninterface Sink {\n    fun timeout(): Int\n}\n"
_BUFFERED = "package a\n\ninterface BufferedSink : Sink {\n    fun emit()\n}\n"
_USE = (
    "package a\n\nclass Use(private val sink: BufferedSink) {\n"
    "    fun f(): Int = sink.timeout()\n}\n"
)


def test_a_typed_receiver_reaches_an_inherited_interface_member(tmp_path: Path) -> None:
    files = {"a/Sink.kt": _SINK, "a/BufferedSink.kt": _BUFFERED, "a/Use.kt": _USE}
    parents = {"a/BufferedSink.kt::BufferedSink": {"a/Sink.kt::Sink"}}
    assert _targets(tmp_path, files, parents, "a/Use.kt") == ["a/Sink.kt::Sink::timeout"]


def test_the_nearest_superclass_override_wins(tmp_path: Path) -> None:
    files = {
        "a/Base.kt": "package a\n\nopen class Base {\n    open fun m() = 1\n}\n",
        "a/Mid.kt": "package a\n\nopen class Mid : Base() {\n    override fun m() = 2\n}\n",
        "a/Leaf.kt": "package a\n\nclass Leaf : Mid()\n",
        "a/Use.kt": "package a\n\nfun f(leaf: Leaf) = leaf.m()\n",
    }
    parents = {"a/Mid.kt::Mid": {"a/Base.kt::Base"}, "a/Leaf.kt::Leaf": {"a/Mid.kt::Mid"}}
    assert _targets(tmp_path, files, parents, "a/Use.kt") == ["a/Mid.kt::Mid::m"]


def test_two_interfaces_declaring_the_member_bind_nothing(tmp_path: Path) -> None:
    files = {
        "a/Sink.kt": _SINK,
        "a/Timed.kt": "package a\n\ninterface Timed {\n    fun timeout(): Int\n}\n",
        "a/BufferedSink.kt": "package a\n\ninterface BufferedSink : Sink, Timed\n",
        "a/Use.kt": _USE,
    }
    parents = {"a/BufferedSink.kt::BufferedSink": {"a/Sink.kt::Sink", "a/Timed.kt::Timed"}}
    assert _targets(tmp_path, files, parents, "a/Use.kt") == []


def test_a_platform_caller_reaches_the_actual_member(tmp_path: Path) -> None:
    """The jvm caller compiles against the jvm ``actual`` twins, not the common ``expect``."""
    common, jvm = "src/commonMain/kotlin/a", "src/jvmMain/kotlin/a"
    files = {
        f"{common}/Sink.kt": "package a\n\nexpect interface Sink {\n    fun timeout(): Int\n}\n",
        f"{jvm}/Sink.kt": "package a\n\nactual interface Sink {\n    actual fun timeout(): Int\n}\n",
        f"{common}/BufferedSink.kt": "package a\n\nexpect interface BufferedSink : Sink\n",
        f"{jvm}/BufferedSink.kt": "package a\n\nactual interface BufferedSink : Sink\n",
        f"{jvm}/Use.kt": _USE,
    }
    parents = {
        f"{common}/BufferedSink.kt::BufferedSink": {f"{common}/Sink.kt::Sink"},
        f"{jvm}/BufferedSink.kt::BufferedSink": {f"{common}/Sink.kt::Sink"},
    }
    assert _targets(tmp_path, files, parents, f"{jvm}/Use.kt") == [
        f"{jvm}/Sink.kt::Sink::timeout"
    ]


def test_a_same_named_type_in_another_package_is_no_twin(tmp_path: Path) -> None:
    """Only an ``actual`` in the ``expect``'s package stands in for it."""
    common, jvm = "src/commonMain/kotlin/a", "src/jvmMain/kotlin/a"
    files = {
        f"{common}/Sink.kt": "package a\n\nexpect interface Sink {\n    fun timeout(): Int\n}\n",
        "src/jvmMain/kotlin/b/Sink.kt": "package b\n\ninterface Sink {\n    fun timeout(): Int\n}\n",
        f"{common}/BufferedSink.kt": "package a\n\ninterface BufferedSink : Sink\n",
        f"{jvm}/Use.kt": _USE,
    }
    parents = {f"{common}/BufferedSink.kt::BufferedSink": {f"{common}/Sink.kt::Sink"}}
    assert _targets(tmp_path, files, parents, f"{jvm}/Use.kt") == [
        f"{common}/Sink.kt::Sink::timeout"
    ]
