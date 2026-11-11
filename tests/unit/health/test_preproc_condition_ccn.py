"""A ``#if`` / ``#elif`` condition is decided by the compiler, not at run time.

``#if defined(A) || defined(B)`` picks which code is built; it is not a branch
the built function takes. Its ``||`` / ``&&`` must not add CCN or cognitive
complexity, while the ``if`` statements inside the arms still do.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_ELIF_CONDITIONS = b"""\
int f(int a, int b) {
#if defined(A) || defined(B) || defined(C)
  if (a) return 1;
#elif (defined(D) && defined(E)) || defined(F)
  if (b) return 2;
#else
  return 3;
#endif
  return 0;
}
"""

_LONE_IF_CONDITION = b"""\
int g(int a) {
#if defined(A) && !defined(B)
  if (a) return 1;
#endif
  if (a && a > 2) return 2;
  return 0;
}
"""


def _fn(source: bytes, language: str, name: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    ext = {"c": "c", "cpp": "cpp", "objectivec": "m"}[language]
    fns = [f for f in walk_file(f"f.{ext}", language, source).functions if f.name == name]
    assert fns, f"{name} not found"
    return fns[0]


@pytest.mark.parametrize("language", ["c", "cpp", "objectivec"])
def test_elif_chain_conditions_do_not_count(language):
    """Each arm holds one ``if``: CCN 2 / cognitive 1, whatever the ``#if`` lines say."""
    fn = _fn(_ELIF_CONDITIONS, language, "f")
    assert fn.ccn == 2
    assert fn.cognitive == 1


@pytest.mark.parametrize("language", ["c", "cpp", "objectivec"])
def test_lone_if_condition_does_not_count(language):
    """Two ``if`` statements and one runtime ``&&``: CCN 4. The ``#if``'s ``&&`` is not one."""
    fn = _fn(_LONE_IF_CONDITION, language, "g")
    assert fn.ccn == 4
