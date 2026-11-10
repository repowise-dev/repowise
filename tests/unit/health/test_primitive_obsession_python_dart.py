"""primitive_obsession in Python and Dart: what a call site actually passes.

Python's ``*args`` / ``**kwargs`` collect what is left over and are no
parameters. Dart ``{named}`` parameters are named at every call site, its
``[optional]`` ones are positional, and an ``@override`` member keeps the
signature of the member it overrides.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity


def _fns(path: str, language: str, source: str) -> dict[str, FunctionComplexity]:
    return {f.name: f for f in walk_file_complexity(path, language, source.encode())}


def test_python_rest_parameters_are_not_counted() -> None:
    fns = _fns(
        "src/app.py",
        "python",
        "def g(a, b, c, d, *args, **kwargs):\n    pass\n"
        "def f(a, *args: int, b: str, **kwargs: str):\n    pass\n",
    )
    assert (fns["g"].param_count, fns["g"].keyword_only_param_count) == (4, 0)
    assert (fns["f"].param_count, fns["f"].keyword_only_param_count) == (2, 1)
    assert (fns["f"].typed_param_count, fns["f"].primitive_param_count) == (1, 1)


_DART = """
class A extends B {
  @override
  void paint(Canvas canvas, int x, int y, int w, int h) {}
  void draw(int x, int y, [int w = 0, int h = 0, int z = 1]) {}
  void named(int x, {required int y, int w = 0, int h = 0, int z = 1}) {}
}
"""


def test_dart_override_fixes_the_signature() -> None:
    fns = _fns("lib/a.dart", "dart", _DART)
    assert fns["paint"].signature_fixed
    assert not fns["draw"].signature_fixed


def test_dart_optional_and_named_parameters_are_each_counted() -> None:
    fns = _fns("lib/a.dart", "dart", _DART)
    assert (fns["draw"].param_count, fns["draw"].keyword_only_param_count) == (5, 0)
    assert (fns["named"].param_count, fns["named"].keyword_only_param_count) == (5, 4)
    assert fns["named"].primitive_param_count == 5
