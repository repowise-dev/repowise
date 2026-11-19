"""Kotlin ``this@X.m()`` names the ``this`` of the enclosing class or extension labelled ``X``."""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_call_resolver_strategies import _edges, _parse_all


def _callees(tmp_path: Path, files: dict[str, str]) -> list[str]:
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    return sorted(callee for _, callee, _, _ in _edges(parsed, tmp_path))


def test_an_outer_class_label_reaches_its_member_from_an_anonymous_object(tmp_path: Path) -> None:
    source = """\
package a

class Buffer {
    fun write(b: Int) {}

    fun stream(): Runnable = object : Runnable {
        override fun run() {
            this@Buffer.write(1)
        }
    }
}
"""
    assert _callees(tmp_path, {"a/Buffer.kt": source}) == ["a/Buffer.kt::Buffer::write"]


def test_an_extension_label_types_its_receiver(tmp_path: Path) -> None:
    files = {
        "a/Syncable.kt": "package a\n\ninterface Syncable {\n    fun syncWith(n: Int): Boolean\n}\n",
        "a/Sync.kt": "package a\n\nfun Syncable.sync() = this@sync.syncWith(1)\n",
    }
    assert _callees(tmp_path, files) == ["a/Syncable.kt::Syncable::syncWith"]


def test_a_lambda_label_binds_nothing(tmp_path: Path) -> None:
    source = """\
package a

class Buffer {
    fun write(b: Int) {}
}

fun f(items: List<Buffer>) {
    items.forEach loop@{ this@loop.write(1) }
}
"""
    assert _callees(tmp_path, {"a/Buffer.kt": source}) == []


def test_an_override_delegating_to_the_outer_class_and_an_inherited_member(tmp_path: Path) -> None:
    source = """\
package a

open class Base {
    fun flush() {}
}

class Sink : Base() {
    fun close() {}

    fun stream(): AutoCloseable = object : AutoCloseable {
        override fun close() = this@Sink.close()

        fun drain() = this@Sink.flush()
    }
}
"""
    files = {"a/Sink.kt": source}
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    from repowise.core.ingestion.call_resolver import CallResolver

    resolver = CallResolver(
        parsed,
        {k: set() for k in parsed},
        repo_path=str(tmp_path),
        heritage_parents={"a/Sink.kt::Sink": {"a/Sink.kt::Base"}},
    )
    found = sorted(rc.callee_id for rc in resolver.resolve_file("a/Sink.kt", parsed["a/Sink.kt"].calls))
    assert found == ["a/Sink.kt::Base::flush", "a/Sink.kt::Sink::close"]
