"""primitive_obsession on the JVM: signatures a framework or the compiler owns.

Dagger ``@Provides`` and Spring ``@Bean`` methods are called by the container,
``@Composable`` functions take UI state as flat parameters by design, Kotlin
and Scala parameters with a default are left out or named at the call site,
and Scala type parameters and ``implicit`` / ``using`` lists are not what a
caller passes.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity

_FILLER = FunctionComplexity("filler", 900, 990, ccn=1, max_nesting=0, cognitive=0, nloc=70)


def _fns(path: str, language: str, source: str) -> dict[str, FunctionComplexity]:
    return {f.name: f for f in walk_file_complexity(path, language, source.encode())}


def _flagged(path: str, language: str, fns: dict[str, FunctionComplexity]) -> set[str]:
    ctx = FileContext(
        file_path=path, language=language, nloc=80, has_test_file=False, module=None,
        is_test=False, all_functions=(*fns.values(), _FILLER),
    )
    return {f.function_name for f in PrimitiveObsessionDetector().detect(ctx)}


_KOTLIN = """
@Composable
fun Card(title: String, subtitle: String, count: Int, enabled: Boolean, label: String) {}
@Provides fun provideClient(a: String, b: Int, c: Int, d: Boolean, e: Long): Client = TODO()
@Bean fun dataSource(a: String, b: Int, c: Int, d: Boolean, e: Long): DataSource = TODO()
fun connect(url: String, user: String, password: String, timeout: Int = 30, retries: Int = 3): Conn = TODO()
fun open(url: String, user: String, password: String, timeout: Int, retries: Int): Conn = TODO()
"""


def test_kotlin_framework_owned_and_defaulted_signatures() -> None:
    fns = _fns("src/main/kotlin/App.kt", "kotlin", _KOTLIN)
    assert fns["connect"].defaulted_param_count == 2
    assert _flagged("src/main/kotlin/App.kt", "kotlin", fns) == {"open"}


def test_java_provides_and_bean_methods_are_fixed() -> None:
    fns = _fns(
        "src/main/java/M.java",
        "java",
        "class M {\n"
        "  @Provides static Client provideClient(String a, int b, int c, boolean d, long e) { return null; }\n"
        "  @Bean public DataSource dataSource(String a, int b, int c, boolean d, long e) { return null; }\n"
        "  static Client build(String a, int b, int c, boolean d, long e) { return null; }\n"
        "}\n",
    )
    assert _flagged("src/main/java/M.java", "java", fns) == {"build"}


_SCALA = """
object O {
  def check[A, B, C, D, E, F](fun: (A, B) => T, genA: Generator[A], genB: Generator[B]): Unit = {}
  def reformat(s: Readable, out: Writer, indent: Int = -1, escape: Boolean = false, sort: Boolean = false): Unit = {}
  def gens[A, B, C, D, E, F](implicit a: Gen[A], b: Gen[B], c: Gen[C], d: Gen[D], e: Gen[E]): Unit = {}
  def g(a: Int, b: Int)(using c: Int, d: Int, e: Int): Unit = {}
  def raw(a: Int, b: Int, c: String, d: Boolean, e: Long): Unit = {}
}
"""


@pytest.mark.parametrize(
    ("name", "counts"),
    [("check", (3, 0)), ("reformat", (5, 3)), ("gens", (0, 0)), ("g", (2, 0)), ("raw", (5, 0))],
)
def test_scala_parameter_lists(name: str, counts: tuple[int, int]) -> None:
    fn = _fns("src/main/scala/O.scala", "scala", _SCALA)[name]
    assert (fn.param_count, fn.defaulted_param_count) == counts


def test_scala_only_the_raw_list_is_flagged() -> None:
    fns = _fns("src/main/scala/O.scala", "scala", _SCALA)
    assert _flagged("src/main/scala/O.scala", "scala", fns) == {"raw"}
