"""A Kotlin call binds the declaration its source set sees.

``Foo`` is declared ``expect`` in ``commonMain`` and ``actual`` in each
platform's source set. Common code calls the ``expect``; a platform file calls
its own ``actual``; another platform's ``actual`` is never the callee, whether
the name is reached through the package or through an import. An ``actual
typealias`` names a type outside the repo, so a call through it has no callee,
as does a name imported from a library namespace. A plain Gradle project ranks
the same way: ``src/main`` code calls its own declaration, ``src/test`` its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_SRC = "lib/src/{}/kotlin/"
_EXPECT = "package p\n\nexpect class Foo() {\n    fun size(): Int\n}\n"
_ACTUAL = (
    "package p\n\nactual class Foo actual constructor() {\n    actual fun size(): Int = 0\n}\n"
)
_PLATFORM = "package n\n\nactual class Plat\n"

MULTIPLATFORM = {
    # Sorted first, so a first-found answer lands on another platform.
    _SRC.format("appleMain") + "p/Foo.kt": _ACTUAL,
    _SRC.format("commonMain") + "p/Foo.kt": _EXPECT,
    _SRC.format("jvmMain") + "p/Foo.kt": _ACTUAL,
    _SRC.format("commonMain") + "p/Common.kt": "package p\n\nfun common() = Foo()\n",
    _SRC.format("jvmMain") + "p/Jvm.kt": "package p\n\nfun jvm() = Foo()\n",
    _SRC.format("commonMain") + "q/Other.kt": "package q\n\nimport p.Foo\n\nfun other() = Foo()\n",
    _SRC.format("jvmMain") + "q/OtherJvm.kt": (
        "package q\n\nimport p.Foo\n\nfun otherJvm() = Foo()\n"
    ),
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
    # Library imports: a repo package declaring the same simple names.
    _SRC.format("jvmMain") + "r/Things.kt": (
        "package r\n\nclass File\n\nclass Job\n\nclass Marker\n"
    ),
    _SRC.format("jvmMain") + "s/Coroutines.kt": (
        "package s\n\nimport kotlinx.coroutines.Job\nimport r.Marker\n\nfun coroutines() = Job()\n"
    ),
    _SRC.format("jvmMain") + "s/Aliased.kt": (
        "package s\n\nimport java.io.File as JFile\nimport r.Marker\n\nfun aliased() = File()\n"
    ),
    # Several actuals and no expect.
    _SRC.format("appleMain") + "n/Plat.kt": _PLATFORM,
    _SRC.format("jvmMain") + "n/Plat.kt": _PLATFORM,
    _SRC.format("jvmMain") + "n/UseJvm.kt": "package n\n\nfun useJvm() = Plat()\n",
    _SRC.format("nativeMain") + "n/UseNative.kt": "package n\n\nfun useNative() = Plat()\n",
}

# A test set sees its main set; a platform set sees the groups above it, not its siblings.
_V = "vis/src/{}/kotlin/"
_DECL = "package n\n\ninternal fun platformName(): String = \"x\"\n"
VISIBILITY = {
    _V.format("appleMain") + "n/Platform.kt": _DECL,
    _V.format("jvmMain") + "n/Platform.kt": _DECL,
    _V.format("nativeMain") + "n/Platform.kt": _DECL,
    _V.format("jvmTest") + "n/PlatformTest.kt": "package n\n\nfun jvmTestCaller() = platformName()\n",
    _V.format("linuxMain") + "n/UseLinux.kt": "package n\n\nfun linuxCaller() = platformName()\n",
    _V.format("commonMain") + "p/Foo.kt": "package p\n\nexpect class Foo()\n",
    _V.format("jvmMain") + "p/Foo.kt": "package p\n\nactual class Foo actual constructor()\n",
    _V.format("jvmMain") + "p/UseJvm.kt": "package p\n\nfun jvmMainCaller() = Foo()\n",
    _V.format("jvmTest") + "p/FooTest.kt": "package p\n\nfun jvmTestFooCaller() = Foo()\n",
    # Imported rather than reached through the package.
    _V.format("jvmTest") + "q/ImportTest.kt": "package q\n\nimport p.Foo\n\nfun jvmTestImports() = Foo()\n",
}

_APP = "app/src/{}/kotlin/m/"
PLAIN_GRADLE = {
    _APP.format("main") + "Helper.kt": "package m\n\nclass Helper\n",
    _APP.format("test") + "Helper.kt": "package m\n\nclass Helper\n",
    _APP.format("main") + "UseMain.kt": "package m\n\nfun useMain() = Helper()\n",
    _APP.format("test") + "UseTest.kt": "package m\n\nfun useTest() = Helper()\n",
}


def _calls(repo: Path, files: dict[str, str]) -> dict[str, set[str]]:
    for rel, text in files.items():
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


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    return _calls(tmp_path_factory.mktemp("kmp"), MULTIPLATFORM)


@pytest.fixture(scope="module")
def visibility_calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    return _calls(tmp_path_factory.mktemp("vis"), VISIBILITY)


@pytest.fixture(scope="module")
def plain_calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    return _calls(tmp_path_factory.mktemp("plain"), PLAIN_GRADLE)


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


@pytest.mark.parametrize("caller", ["failJvm", "failOther", "io", "coroutines"])
def test_a_call_through_an_alias_or_library_import_has_no_callee(
    calls: dict[str, set[str]], caller: str
) -> None:
    assert caller not in calls


def test_an_aliased_library_import_binds_only_its_alias(calls: dict[str, set[str]]) -> None:
    assert calls["aliased"] == {_SRC.format("jvmMain") + "r/Things.kt::File"}


def test_without_an_expect_a_platform_calls_its_own_actual(calls: dict[str, set[str]]) -> None:
    assert calls["useJvm"] == {_SRC.format("jvmMain") + "n/Plat.kt::Plat"}


def test_without_an_expect_or_own_actual_a_set_that_sees_no_declaration_has_no_callee(
    calls: dict[str, set[str]],
) -> None:
    # ``nativeMain`` sees neither ``appleMain`` nor ``jvmMain``: no edge, not a guess.
    assert "useNative" not in calls


def test_a_test_set_binds_the_declaration_its_main_set_sees(
    visibility_calls: dict[str, set[str]],
) -> None:
    jvm_main = _V.format("jvmMain")
    assert visibility_calls["jvmTestCaller"] == {jvm_main + "n/Platform.kt::platformName"}
    assert visibility_calls["jvmTestFooCaller"] == {jvm_main + "p/Foo.kt::Foo"}
    assert visibility_calls["jvmTestImports"] == {jvm_main + "p/Foo.kt::Foo"}
    assert visibility_calls["jvmMainCaller"] == {jvm_main + "p/Foo.kt::Foo"}


def test_a_platform_group_binds_the_declaration_of_the_group_it_depends_on(
    visibility_calls: dict[str, set[str]],
) -> None:
    # ``linuxMain`` -> ``nativeMain``; its sibling ``appleMain`` is out of sight.
    assert visibility_calls["linuxCaller"] == {_V.format("nativeMain") + "n/Platform.kt::platformName"}


@pytest.mark.parametrize(("caller", "source_set"), [("useMain", "main"), ("useTest", "test")])
def test_a_plain_gradle_source_set_calls_its_own_declaration(
    plain_calls: dict[str, set[str]], caller: str, source_set: str
) -> None:
    assert plain_calls[caller] == {_APP.format(source_set) + "Helper.kt::Helper"}
