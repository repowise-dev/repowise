"""A Kotlin Multiplatform call binds the declaration its source set sees.

``Foo`` is declared ``expect`` in ``commonMain`` and ``actual`` in each
platform's source set. Common code calls the ``expect``; a platform file calls
its own ``actual``; another platform's ``actual`` is never the callee, whether
the name is reached through the package or through an import. An ``actual
typealias`` names a type outside the repo, so a call through it has no callee,
as does a name imported from the JDK.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_SRC = "lib/src/{}/kotlin/"
_EXPECT = "package p\n\nexpect class Foo() {\n    fun size(): Int\n}\n"
_ACTUAL = "package p\n\nactual class Foo actual constructor() {\n    actual fun size(): Int = 0\n}\n"

FILES = {
    # Sorted first, so a first-found answer lands on another platform.
    _SRC.format("appleMain") + "p/Foo.kt": _ACTUAL,
    _SRC.format("commonMain") + "p/Foo.kt": _EXPECT,
    _SRC.format("jvmMain") + "p/Foo.kt": _ACTUAL,
    _SRC.format("commonMain") + "p/Common.kt": "package p\n\nfun common() = Foo()\n",
    _SRC.format("jvmMain") + "p/Jvm.kt": "package p\n\nfun jvm() = Foo()\n",
    _SRC.format("commonMain") + "q/Other.kt": "package q\n\nimport p.Foo\n\nfun other() = Foo()\n",
    _SRC.format("jvmMain") + "q/OtherJvm.kt": "package q\n\nimport p.Foo\n\nfun otherJvm() = Foo()\n",
    _SRC.format("commonMain") + "p/Errors.kt": (
        "package p\n\nexpect open class IOException(message: String?)\n\nexpect class Missing\n"
    ),
    _SRC.format("jvmMain") + "p/JvmErrors.kt": (
        "package p\n\nactual typealias IOException = java.io.IOException\n"
    ),
    _SRC.format("commonMain") + "p/Fail.kt": 'package p\n\nfun fail() = IOException("x")\n',
    _SRC.format("jvmMain") + "p/FailJvm.kt": 'package p\n\nfun failJvm() = IOException("x")\n',
    _SRC.format("jvmMain") + "q/FailOther.kt": (
        'package q\n\nimport p.IOException\n\nfun failOther() = IOException("x")\n'
    ),
    _SRC.format("jvmMain") + "q/Io.kt": (
        'package q\n\nimport java.io.IOException\nimport p.Missing\n\nfun io() = IOException("x")\n'
    ),
}


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    repo = tmp_path_factory.mktemp("kmp")
    for rel, text in FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    out: dict[str, set[str]] = {}
    for caller, callee, data in graph.edges(data=True):
        if data.get("edge_type") == "calls":
            out.setdefault(caller.rsplit("::", 1)[-1], set()).add(callee)
    return out


@pytest.mark.parametrize(
    ("caller", "source_set"),
    [
        ("common", "commonMain"),  # package sibling, common code
        ("jvm", "jvmMain"),  # package sibling, platform code
        ("other", "commonMain"),  # imported, common code
        ("otherJvm", "jvmMain"),  # imported, platform code
    ],
)
def test_a_call_binds_the_declaration_its_source_set_sees(
    calls: dict[str, set[str]], caller: str, source_set: str
) -> None:
    assert calls[caller] == {_SRC.format(source_set) + "p/Foo.kt::Foo"}


def test_common_code_calls_the_expect_type(calls: dict[str, set[str]]) -> None:
    assert calls["fail"] == {_SRC.format("commonMain") + "p/Errors.kt::IOException"}


@pytest.mark.parametrize("caller", ["failJvm", "failOther", "io"])
def test_a_call_through_an_actual_typealias_has_no_callee(
    calls: dict[str, set[str]], caller: str
) -> None:
    assert caller not in calls
